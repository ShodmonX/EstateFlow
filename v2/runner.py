"""Independent owner process or database-free ingress adapter for the private lab."""

import argparse
import importlib
import json
import os
import threading
import time
from typing import Any
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
import uvicorn
from v2.shared.contracts import new_event, validate, canonical_hash, stable_id
from v2.shared.routing import OWNERS, BINDINGS
from v2.shared.security import guard_fake_only, verify_actor


def owner_app(owner, store, progress):
    app = FastAPI(title="EstateFlow V2 " + owner + " / FAKE_ONLY")
    key = os.environ.get("EFV2_AUTH_KEY", "")
    if len(key) < 16:
        raise ValueError("lab_auth_key_required")

    def actor(request):
        try:
            return verify_actor(
                request.headers.get("authorization", "").removeprefix("Bearer "), key
            )
        except ValueError as exc:
            raise HTTPException(401, "unauthorized") from exc

    def can_read(a, kind, row=None):
        if "admin" in a["roles"] or "service" in a["roles"]:
            return True
        if owner == "search" and kind == "listings":
            return row is None or row.get("visibility") == "active"
        if owner == "notifications" and kind in ("filters", "jobs", "attempts"):
            return row is None or row.get("user_id") == a["id"]
        if owner == "identity" and kind == "users":
            return row is None or row.get("user_id") == a["id"]
        return False

    @app.get("/health/live")
    def live():
        return {"owner": owner, "mode": "FAKE_ONLY"}

    @app.get("/health/ready")
    def ready():
        try:
            metrics = store.metrics()
        except Exception as exc:
            raise HTTPException(503, "database_unavailable") from exc
        if time.monotonic() - progress.get("heartbeat", 0) > 30:
            raise HTTPException(503, "worker_stalled")
        return {
            "owner": owner,
            "mode": "FAKE_ONLY",
            "metrics": metrics,
            "worker": progress.get("state", "starting"),
        }

    @app.post("/commands", status_code=202)
    async def accept(request: Request):
        a = actor(request)
        raw = await request.body()
        if len(raw) > 65536:
            raise HTTPException(413, "body_limit")
        try:
            body = json.loads(raw)
            operation = body.get("operation_id") or str(uuid4())
            payload = {
                "operation_id": operation,
                "action": body["action"],
                "subject_id": body["subject_id"],
                "actor": {"id": a["id"], "roles": a["roles"]},
                "expected_version": body.get("expected_version"),
                "arguments": body.get("arguments", {}),
            }
            event = new_event(owner + ".command.execute", owner, "command", operation, 1, payload)
            validate(event)
            # Authorization is checked before durable acceptance and again by owner handler.
            from v2.shared.events import authorize

            admin = (
                owner not in ("identity", "notifications", "control")
                or (owner == "identity" and payload["action"] == "grant_premium")
                or (owner == "control" and payload["action"] != "suggest_source")
                or (
                    owner == "notifications" and payload["action"] in ("deliver", "resolve_unknown")
                )
            )
            authorize(payload, admin=admin)
            digest = canonical_hash(payload)
            with store.transaction() as tx:
                previous = tx.get("operations", operation)
                if previous:
                    if previous.get("actor_id") != a["id"]:
                        raise HTTPException(403, "forbidden")
                    if previous.get("request_hash") not in (None, digest):
                        raise HTTPException(409, "command_conflict")
                    return {
                        "operation_id": operation,
                        "owner": owner,
                        "status": previous["status"],
                    }
                tx.put(
                    "operations",
                    operation,
                    {
                        "id": operation,
                        "owner": owner,
                        "status": "accepted",
                        "actor_id": a["id"],
                        "request_hash": digest,
                    },
                )
                tx.emit(event)
            return {
                "operation_id": operation,
                "owner": owner,
                "status": "accepted",
                "mode": "FAKE_ONLY",
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, "invalid_command_or_authorization") from exc

    @app.get("/operations/{operation}")
    def operation(operation: str, request: Request):
        a = actor(request)
        with store.transaction() as tx:
            row = tx.get("operations", operation)
        if not row:
            raise HTTPException(404, "not_found")
        if a["id"] != row.get("actor_id") and not set(a["roles"]).intersection(
            {"admin", "service"}
        ):
            raise HTTPException(403, "forbidden")
        return row

    @app.get("/records/{kind}")
    def records(kind: str, request: Request):
        a = actor(request)
        if not can_read(a, kind):
            raise HTTPException(403, "forbidden")
        with store.transaction() as tx:
            rows = tx.scan(kind)
        return [row for row in rows if can_read(a, kind, row)]

    @app.get("/records/{kind}/{record}")
    def record(kind: str, record: str, request: Request):
        a = actor(request)
        if not can_read(a, kind):
            raise HTTPException(403, "forbidden")
        with store.transaction() as tx:
            row = tx.get(kind, record)
        if not row or not can_read(a, kind, row):
            raise HTTPException(404, "not_found")
        return row

    if owner == "search":

        @app.get("/search")
        def search(request: Request):
            actor(request)
            criteria: dict[str, Any] = dict(request.query_params)
            try:
                for field in ("min_price", "max_price", "rooms"):
                    if field in criteria:
                        criteria[field] = float(criteria[field])
                for field in ("limit", "offset"):
                    if field in criteria:
                        criteria[field] = int(criteria[field])
                if "include_per_person" in criteria:
                    criteria["include_per_person"] = (
                        criteria["include_per_person"].lower() == "true"
                    )
                from v2.services.search.handler import query

                with store.transaction() as tx:
                    return query(tx, criteria)
            except ValueError as exc:
                raise HTTPException(422, "invalid_search") from exc
            except Exception as exc:
                raise HTTPException(503, "search_unavailable") from exc

    return app


def record_outcome(store, event, status):
    if status not in {
        "permanent_failure",
        "terminal_failure",
        "expired",
        "invalid_schema",
        "routing_conflict",
        "payload_conflict",
        "revision_conflict",
    }:
        return
    if not isinstance(event, dict) or not str(event.get("event_type", "")).endswith(
        ".command.execute"
    ):
        return
    p = event.get("payload", {})
    if not isinstance(p, dict) or not p.get("operation_id"):
        return
    try:
        validate(event)
    except ValueError:
        return
    from v2.shared.events import emit

    with store.transaction() as tx:
        row = tx.get("operations", p["operation_id"]) or {
            "id": p["operation_id"],
            "owner": store.owner,
            "subject_id": p["subject_id"],
            "actor_id": p["actor"]["id"],
            "status": "accepted",
        }
        if row["status"] in ("completed", "failed"):
            return
        row.update(status="failed", reason=status)
        tx.put("operations", p["operation_id"], row)
        emit(
            tx,
            event,
            "operation.completed",
            store.owner,
            "domain_record",
            stable_id(p["operation_id"]),
            1,
            {
                "operation_id": p["operation_id"],
                "owner": store.owner,
                "subject_id": p["subject_id"],
                "status": "failed",
                "reason": status,
            },
        )


def worker(owner, stop, progress):
    from v2.foundation import Store, Broker, process_event, dispatch_once, retry_due

    handler = importlib.import_module("v2.services." + owner + ".handler").handle
    store = Store(owner)
    broker = None
    queue = "v2." + owner + ".events.v1"
    while not stop.is_set():
        try:
            if broker is None:
                broker = Broker()
                broker.declare(queue, BINDINGS[owner])
            dispatch_once(store, broker)
            retry_due(store, broker)
            delivery = broker.get(queue)
            if delivery:
                try:
                    status = process_event(
                        store, delivery.event, handler, routing_key=delivery.routing_key
                    )
                    record_outcome(store, delivery.event, status)
                except Exception:
                    broker.nack(delivery, requeue=True)
                    raise
                else:
                    broker.ack(delivery)
            progress.update(heartbeat=time.monotonic(), state="consuming")
            stop.wait(0.1)
        except Exception as exc:
            progress["state"] = "reconnecting:" + type(exc).__name__
            print(
                json.dumps(
                    {
                        "owner": owner,
                        "error_class": type(exc).__name__,
                        "mode": "FAKE_ONLY",
                    }
                ),
                flush=True,
            )
            if broker:
                try:
                    broker.close()
                except Exception:
                    pass
            broker = None
            stop.wait(1)
    if broker:
        broker.close()


def tick(owner, stop, action, arguments):
    from v2.foundation import Store

    store = Store(owner)
    while not stop.wait(1):
        operation = str(uuid4())
        e = new_event(
            owner + ".command.execute",
            owner,
            "command",
            operation,
            1,
            {
                "operation_id": operation,
                "action": action,
                "subject_id": "synthetic-scheduler",
                "actor": {"id": "synthetic-scheduler", "roles": ["service"]},
                "expected_version": None,
                "arguments": arguments,
            },
        )
        try:
            with store.transaction() as tx:
                tx.emit(e)
        except Exception as exc:
            print(
                json.dumps({"owner": owner, "error_class": type(exc).__name__}),
                flush=True,
            )


def main():
    guard_fake_only()
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", choices=OWNERS, required=True)
    parser.add_argument(
        "--role",
        choices=("worker", "sender", "public_api", "admin_api", "internal_api", "bot"),
        default="worker",
    )
    args = parser.parse_args()
    if args.role in ("public_api", "admin_api", "internal_api", "bot"):
        from v2.apps.api import create_app

        uvicorn.run(create_app(args.role), host="0.0.0.0", port=8000, access_log=False)
        return
    from v2.foundation import Store

    stop = threading.Event()
    progress = {"heartbeat": 0, "state": "starting"}
    thread = threading.Thread(target=worker, args=(args.owner, stop, progress), daemon=True)
    thread.start()
    if args.owner == "listener":
        threading.Thread(
            target=tick, args=("listener", stop, "flush_albums", {}), daemon=True
        ).start()
    if args.role == "sender":
        threading.Thread(
            target=tick,
            args=("notifications", stop, "deliver", {"sender_mode": "success"}),
            daemon=True,
        ).start()
    try:
        uvicorn.run(
            owner_app(args.owner, Store(args.owner), progress),
            host="0.0.0.0",
            port=8000,
            access_log=False,
        )
    finally:
        stop.set()
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
