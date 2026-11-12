"""Manual ACK transport, fenced outbox dispatch and durable consumer outcomes."""

import json
import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pika
import psycopg
from psycopg.types.json import Jsonb

from v2.shared.contracts import canonical_hash, validate

MAX_BODY = 65536
DELAYS = (5, 15, 45, 135, 405)


def utcnow():
    return datetime.now(timezone.utc)


@dataclass
class Delivery:
    event: object
    tag: int
    routing_key: str = ""


class Broker:
    def __init__(self, url=None):
        params = pika.URLParameters(url or os.environ["EFV2_BROKER_URL"])
        if os.environ.get("EFV2_MODE", "FAKE_ONLY") != "FAKE_ONLY" or params.host != "rabbitmq":
            raise PermissionError("Broker access restricted to isolated FAKE_ONLY lab")
        params.socket_timeout = 10
        params.blocked_connection_timeout = 10
        params.heartbeat = 30
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()
        self.channel.exchange_declare("estateflow.v2.events", exchange_type="topic", durable=True)
        self.channel.exchange_declare("estateflow.v2.dlx", exchange_type="direct", durable=True)
        self.channel.confirm_delivery()
        self.channel.basic_qos(prefetch_count=10)

    def declare(self, queue, bindings):
        dead = queue + ".dead"
        self.channel.queue_declare(dead, durable=True, arguments={"x-queue-type": "classic"})
        self.channel.queue_bind(dead, "estateflow.v2.dlx", queue)
        self.channel.queue_declare(
            queue,
            durable=True,
            arguments={
                "x-queue-type": "classic",
                "x-dead-letter-exchange": "estateflow.v2.dlx",
                "x-dead-letter-routing-key": queue,
            },
        )
        for binding in bindings:
            self.channel.queue_bind(queue, "estateflow.v2.events", binding)

    def publish(self, event, routing_key=None):
        validate(event)
        body = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(body) > MAX_BODY:
            raise ValueError("Message exceeds synthetic size budget")
        self.channel.basic_publish(
            "estateflow.v2.events",
            routing_key or event["event_type"] + ".v" + str(event["event_version"]),
            body,
            properties=pika.BasicProperties(
                content_type="application/json", delivery_mode=2, message_id=event["event_id"]
            ),
            mandatory=True,
        )

    def get(self, queue):
        method, _, body = self.channel.basic_get(queue, auto_ack=False)
        if method is None:
            return None
        try:
            event = json.loads(body) if len(body) <= MAX_BODY else body
        except (ValueError, UnicodeDecodeError):
            event = body
        return Delivery(event, method.delivery_tag, method.routing_key)

    def ack(self, delivery):
        self.channel.basic_ack(delivery.tag)

    def nack(self, delivery, requeue=True):
        self.channel.basic_nack(delivery.tag, requeue=requeue)

    def purge(self, queue):
        self.channel.queue_purge(queue)

    def close(self):
        if self.connection.is_open:
            self.connection.close()


def park(tx, event, reason, now, error_class=None):
    identifier = str(uuid.uuid4())
    # Only schema-valid synthetic objects are retained. Poison bodies are never logged/stored.
    safe = (
        event
        if isinstance(event, dict)
        and reason not in ("invalid_schema", "unsupported_version", "oversized")
        else None
    )
    event_id = event.get("event_id") if isinstance(event, dict) else None
    event_id = event_id if isinstance(event_id, str) and len(event_id) <= 64 else None
    tx.conn.execute(
        f"INSERT INTO {tx.owner}.parked_events(id,event_id,event,reason,error_class,created_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            identifier,
            event_id,
            Jsonb(safe),
            reason,
            error_class,
            now,
            now + timedelta(minutes=10) if reason == "revision_gap" else None,
        ),
    )
    return reason


def process_event(store, event, handler, now=None, strict_versions=None, routing_key=None):
    now = now or utcnow()
    reason = None
    if isinstance(event, (bytes, str)):
        if len(event) > MAX_BODY:
            reason = "oversized"
        else:
            try:
                event = json.loads(event)
            except (ValueError, UnicodeDecodeError):
                reason = "invalid_schema"
    if reason is None:
        try:
            if len(json.dumps(event)) > MAX_BODY:
                reason = "oversized"
            elif isinstance(event, dict) and event.get("event_version") != 1:
                reason = "unsupported_version"
            else:
                validate(event)
        except (ValueError, TypeError, KeyError):
            reason = "invalid_schema"
        except Exception as exc:
            if type(exc).__module__.startswith(("jsonschema", "referencing")):
                reason = "invalid_schema"
            else:
                raise
    with store.transaction() as tx:
        if reason:
            return park(tx, event, reason, now)
        expected_route = event["event_type"] + ".v" + str(event["event_version"])
        if routing_key is not None and routing_key != expected_route:
            return park(tx, event, "routing_conflict", now)
        event_id, digest = event["event_id"], canonical_hash(event)
        stream = ":".join(
            str(event[k]) for k in ("producer", "event_type", "aggregate_kind", "aggregate_id")
        )
        tx.lock("stream:" + stream)
        existing = tx.conn.execute(
            f"SELECT * FROM {store.owner}.inbox WHERE event_id=%s", (event_id,)
        ).fetchone()
        if existing:
            if existing["fingerprint"] != digest:
                return park(tx, event, "payload_conflict", now)
            return "duplicate"
        retry = tx.conn.execute(
            f"SELECT * FROM {store.owner}.retry_schedule WHERE event_id=%s FOR UPDATE", (event_id,)
        ).fetchone()
        if retry:
            if canonical_hash(retry["event"]) != digest:
                return park(tx, event, "payload_conflict", now)
            if retry["status"] == "terminal":
                return "terminal_failure"
            if retry["next_attempt_at"] > now:
                return "retry_wait"
        if datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")) < now - timedelta(
            hours=24
        ):
            return park(tx, event, "expired", now)
        strict = (
            event["aggregate_kind"] == "synthetic" if strict_versions is None else strict_versions
        )
        if strict:
            previous = tx.conn.execute(
                f"SELECT * FROM {store.owner}.revisions WHERE stream=%s ORDER BY version DESC LIMIT 1",
                (stream,),
            ).fetchone()
            version = event["aggregate_version"]
            latest = previous["version"] if previous else 0
            if version <= latest:
                return park(tx, event, "revision_conflict", now)
            if version != latest + 1:
                return park(tx, event, "revision_gap", now)
        attempts = retry["attempts"] + 1 if retry else 1
        try:
            with tx.conn.transaction():
                handler(tx, event)
        except Exception as exc:
            # Database failures abort the whole transaction and must never ACK.
            if isinstance(exc, psycopg.Error):
                raise
            if isinstance(exc, (ValueError, PermissionError)):
                return park(tx, event, "permanent_failure", now, type(exc).__name__)
            terminal = attempts >= 6
            due = now + timedelta(seconds=retry_delay(event_id, attempts))
            tx.conn.execute(
                f"INSERT INTO {store.owner}.retry_schedule(event_id,event,attempts,next_attempt_at,error_class,status) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO UPDATE SET attempts=EXCLUDED.attempts,next_attempt_at=EXCLUDED.next_attempt_at,error_class=EXCLUDED.error_class,status=EXCLUDED.status,transport_attempts=0,lease_token=NULL,lease_until=NULL",
                (
                    event_id,
                    Jsonb(event),
                    attempts,
                    due,
                    type(exc).__name__,
                    "terminal" if terminal else "pending",
                ),
            )
            return (
                park(tx, event, "terminal_failure", now, type(exc).__name__)
                if terminal
                else "retry_scheduled"
            )
        tx.conn.execute(
            f"INSERT INTO {store.owner}.inbox(event_id,fingerprint,status,processed_at) VALUES(%s,%s,'applied',%s)",
            (event_id, digest, now),
        )
        if strict:
            tx.conn.execute(
                f"INSERT INTO {store.owner}.revisions(stream,version,event_id) VALUES(%s,%s,%s)",
                (stream, event["aggregate_version"], event_id),
            )
        tx.conn.execute(f"DELETE FROM {store.owner}.retry_schedule WHERE event_id=%s", (event_id,))
        return "applied"


def claim(store, table, limit, now):
    if table not in ("outbox", "retry_schedule"):
        raise ValueError("Unknown dispatch table")
    with store.transaction() as tx:
        token = str(uuid.uuid4())
        rows = tx.conn.execute(
            f"WITH candidates AS (SELECT event_id FROM {store.owner}.{table} WHERE status IN ('pending','leased') AND next_attempt_at<=%s AND (lease_until IS NULL OR lease_until<=%s) ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT %s) UPDATE {store.owner}.{table} AS work SET status='leased',lease_token=%s,lease_until=%s FROM candidates WHERE work.event_id=candidates.event_id RETURNING work.*",
            (now, now, limit, token, now + timedelta(seconds=60)),
        ).fetchall()
        return rows


def mark_published(store, event_id, token, now=None, table="outbox"):
    if table not in ("outbox", "retry_schedule"):
        raise ValueError("Unknown dispatch table")
    with store.transaction() as tx:
        fields = (
            "status='published',published_at=%s"
            if table == "outbox"
            else "status='dispatched',next_attempt_at=%s"
        )
        return (
            tx.conn.execute(
                f"UPDATE {store.owner}.{table} SET {fields},lease_token=NULL,lease_until=NULL WHERE event_id=%s AND lease_token=%s AND lease_until>%s",
                (now or utcnow(), event_id, token, now or utcnow()),
            ).rowcount
            == 1
        )


def dispatch_once(store, broker, limit=20, now=None):
    return _dispatch(store, broker, "outbox", limit, now or utcnow())


def retry_due(store, broker, limit=20, now=None):
    now = now or utcnow()
    expire_gaps(store, now)
    return _dispatch(store, broker, "retry_schedule", limit, now)


def expire_gaps(store, now=None):
    with store.transaction() as tx:
        return tx.conn.execute(
            f"UPDATE {store.owner}.parked_events SET reason='terminal_revision_gap' WHERE reason='revision_gap' AND expires_at<=%s",
            (now or utcnow(),),
        ).rowcount


def _dispatch(store, broker, table, limit, now):
    completed = 0
    for row in claim(store, table, limit, now):
        # Each actual publish has a separate durable budget from handler processing.
        counter = "attempts" if table == "outbox" else "transport_attempts"
        with store.transaction() as tx:
            if row[counter] >= 6:
                updated = tx.conn.execute(
                    f"UPDATE {store.owner}.{table} SET status='terminal',lease_token=NULL,lease_until=NULL WHERE event_id=%s AND lease_token=%s",
                    (row["event_id"], row["lease_token"]),
                ).rowcount
                if updated:
                    park(tx, row["event"], "terminal_failure", now, "PublishBudgetExhausted")
                continue
            updated = tx.conn.execute(
                f"UPDATE {store.owner}.{table} SET {counter}={counter}+1 WHERE event_id=%s AND lease_token=%s AND lease_until>%s",
                (row["event_id"], row["lease_token"], now),
            ).rowcount
            if not updated:
                continue
        try:
            broker.publish(row["event"])
        except Exception as exc:
            with store.transaction() as tx:
                attempts = row[counter] + 1
                terminal = attempts >= 6
                tx.conn.execute(
                    f"UPDATE {store.owner}.{table} SET status=%s,next_attempt_at=%s,lease_token=NULL,lease_until=NULL,error_class=%s WHERE event_id=%s AND lease_token=%s",
                    (
                        "terminal" if terminal else "pending",
                        now + timedelta(seconds=retry_delay(str(row["event_id"]), attempts)),
                        type(exc).__name__,
                        row["event_id"],
                        row["lease_token"],
                    ),
                )
                if terminal:
                    park(tx, row["event"], "terminal_failure", now, type(exc).__name__)
            continue
        if mark_published(store, row["event_id"], row["lease_token"], now, table):
            completed += 1
    return completed


def retry_delay(event_id, attempt):
    """Stable synthetic jitter makes repeated schedules reproducible and bounded."""
    digest = hashlib.sha256(f"{event_id}:{attempt}".encode()).digest()
    fraction = int.from_bytes(digest[:4], "big") / (2**32 - 1)
    return DELAYS[min(max(attempt - 1, 0), 4)] * (0.8 + 0.4 * fraction)


def replay(store, parked_id, actor, mode="redeliver"):
    allowed = actor == "lab-operator" and mode == "redeliver"
    with store.transaction() as tx:
        row = tx.conn.execute(
            f"SELECT * FROM {store.owner}.parked_events WHERE id=%s FOR UPDATE", (parked_id,)
        ).fetchone()
        tx.conn.execute(
            f"INSERT INTO {store.owner}.replay_audit(id,parked_id,actor,mode,allowed) VALUES(%s,%s,%s,%s,%s)",
            (
                str(uuid.uuid4()),
                parked_id,
                actor if allowed else "denied",
                mode if mode in ("redeliver",) else "unsupported",
                allowed,
            ),
        )
        event = row["event"] if row else None
        if allowed and event:
            validate(event)
            tx.conn.execute(
                f"UPDATE {store.owner}.parked_events SET replayed_at=now() WHERE id=%s",
                (parked_id,),
            )
            tx.lock("replay:" + event["event_id"])
            tx.conn.execute(
                f"UPDATE {store.owner}.retry_schedule SET attempts=0,transport_attempts=0,status='pending',next_attempt_at=now(),lease_token=NULL,lease_until=NULL WHERE event_id=%s AND status='terminal'",
                (event["event_id"],),
            )
    if not allowed:
        raise PermissionError("Replay authorization denied")
    if not event:
        raise ValueError("No replayable schema-valid payload")
    return event
