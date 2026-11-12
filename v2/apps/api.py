"""HTTP ingress adapters. Domain reads/commands go to owner endpoints, never foreign SQL."""

import os
from uuid import uuid4
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from v2.shared.security import verify_actor, issue_actor, verify_webapp, guard_fake_only
from v2.shared.routing import OWNERS


class Gateway:
    def request(self, owner, method, path, token, body=None, params=None):
        if owner not in OWNERS:
            raise ValueError("unknown_owner")
        try:
            with httpx.Client(trust_env=False, timeout=5) as client:
                r = client.request(
                    method,
                    "http://" + owner + ":8000" + path,
                    headers={"Authorization": "Bearer " + token},
                    json=body,
                    params=params,
                )
            if r.status_code >= 400:
                raise HTTPException(r.status_code, r.json().get("detail", "owner_error"))
            return r.json()
        except httpx.HTTPError as exc:
            raise HTTPException(503, "owner_unavailable") from exc


def create_app(profile="public_api", gateway=None, key=None, bot_key=None):
    guard_fake_only()
    gateway = gateway or Gateway()
    key = key or os.environ.get("EFV2_AUTH_KEY", "")
    bot_key = bot_key or os.environ.get("EFV2_FAKE_BOT_TOKEN", "")
    if len(key) < 16:
        raise ValueError("missing_lab_auth_key")
    app = FastAPI(title="EstateFlow V2 isolated " + profile)

    def actor(req, admin=False):
        try:
            token = req.headers.get("authorization", "").removeprefix("Bearer ")
            a = verify_actor(token, key)
        except ValueError as exc:
            raise HTTPException(401, "unauthorized") from exc
        if admin and "admin" not in a["roles"]:
            raise HTTPException(403, "forbidden")
        return a, token

    def submit(req, owner, action, subject, args):
        a, token = actor(req, admin=profile in ("admin_api", "internal_api"))
        return gateway.request(
            owner,
            "POST",
            "/commands",
            token,
            {
                "operation_id": str(uuid4()),
                "subject_id": subject,
                "action": action,
                "arguments": args,
            },
        )

    @app.get("/health/live")
    def live():
        return {"mode": "FAKE_ONLY", "profile": profile}

    @app.get("/health/ready")
    def ready():
        dependency = (
            "search" if profile == "public_api" else "identity" if profile == "bot" else "control"
        )
        gateway.request(
            dependency,
            "GET",
            "/health/ready",
            issue_actor("synthetic-health", ["service"], key),
        )
        return {
            "mode": "FAKE_ONLY",
            "profile": profile,
            "status": "ready",
            "provider_validation": "NOT_RUN",
        }

    @app.get("/")
    def index():
        return FileResponse(
            "v2/frontend/" + ("admin.html" if profile == "admin_api" else "index.html")
        )

    @app.get("/app.js")
    def js():
        return FileResponse("v2/frontend/app.js")

    @app.get("/style.css")
    def css():
        return FileResponse("v2/frontend/style.css")

    @app.get("/operations/{owner}/{operation_id}")
    def operation(owner: str, operation_id: str, request: Request):
        a, token = actor(request)
        return gateway.request(owner, "GET", "/operations/" + operation_id, token)

    @app.post("/commands/{owner}")
    async def command(owner: str, request: Request):
        body = await request.json()
        args = body.get("arguments", {})
        return JSONResponse(
            submit(
                request,
                owner,
                body["action"],
                body.get("subject_id", "synthetic"),
                args,
            ),
            status_code=202,
        )

    if profile in ("public_api", "bot"):

        @app.post("/api/webapp/auth")
        async def auth(request: Request):
            try:
                a = verify_webapp((await request.json())["init_data"], bot_key)
            except (KeyError, ValueError) as exc:
                raise HTTPException(401, "invalid_webapp_auth") from exc
            return {
                "access_token": issue_actor(a["id"], ["user"], key),
                "expires_in": 900,
                "user": {"user_id": int(a["id"])},
                "mode": "FAKE_ONLY",
            }

        @app.get("/api/webapp/me")
        def me(request: Request):
            a, _ = actor(request)
            return {"user_id": a["id"], "mode": "FAKE_ONLY"}

        @app.get("/api/search/announcements")
        @app.get("/api/webapp/search/announcements")
        def search(request: Request):
            _, token = actor(request)
            return gateway.request(
                "search", "GET", "/search", token, params=dict(request.query_params)
            )

        @app.get("/api/webapp/announcements/{listing_id}")
        def detail(listing_id: str, request: Request):
            _, token = actor(request)
            return gateway.request("search", "GET", "/records/listings/" + listing_id, token)

        @app.post("/api/webapp/search/nlp")
        async def nlp(request: Request):
            actor(request)
            text = (await request.json()).get("query", "")
            if not isinstance(text, str) or len(text) > 140:
                raise HTTPException(422, "query_length")
            import re

            prices = re.findall(r"\d+", text)
            criteria = {"max_price": int(prices[0])} if prices else {}
            return {
                "query": text,
                "criteria": criteria,
                "tags": [],
                "confidence": 0.5,
                "mode": "MOCK_ONLY",
                "provider": "deterministic-synthetic",
            }

        @app.get("/api/webapp/filters")
        def filters(request: Request):
            _, token = actor(request)
            return gateway.request("notifications", "GET", "/records/filters", token)

        @app.post("/api/webapp/filters")
        async def create_filter(request: Request):
            a, _ = actor(request)
            body = await request.json()
            fid = str(uuid4())
            return JSONResponse(
                submit(
                    request,
                    "notifications",
                    "save_filter",
                    fid,
                    {
                        "user_id": a["id"],
                        "filter_id": fid,
                        "name": body.get("name", "Synthetic filter"),
                        "criteria": body.get("criteria", {}),
                        "enabled": body.get("enabled", True),
                    },
                ),
                status_code=202,
            )

        @app.patch("/api/webapp/filters/{filter_id}")
        async def update_filter(filter_id: str, request: Request):
            a, _ = actor(request)
            body = await request.json()
            return JSONResponse(
                submit(
                    request,
                    "notifications",
                    "save_filter",
                    filter_id,
                    {"user_id": a["id"], "filter_id": filter_id, **body},
                ),
                status_code=202,
            )

        @app.delete("/api/webapp/filters/{filter_id}")
        def delete_filter(filter_id: str, request: Request):
            a, _ = actor(request)
            return JSONResponse(
                submit(
                    request,
                    "notifications",
                    "delete_filter",
                    filter_id,
                    {"user_id": a["id"], "filter_id": filter_id},
                ),
                status_code=202,
            )

        @app.post("/synthetic/bot/update")
        async def bot(request: Request):
            a, token = actor(request)
            body = await request.json()
            command = body.get("command")
            if command in ("/search", "search"):
                return {
                    "messages": [gateway.request("search", "GET", "/search", token)],
                    "mode": "FAKE_ONLY",
                }
            if command == "/start":
                return {
                    "operation": submit(
                        request,
                        "identity",
                        "upsert_user",
                        a["id"],
                        {"user_id": a["id"], "consent": False},
                    ),
                    "messages": ["SYNTHETIC welcome"],
                    "mode": "FAKE_ONLY",
                }
            if command == "/filters":
                return {
                    "messages": [
                        gateway.request("notifications", "GET", "/records/filters", token)
                    ],
                    "mode": "FAKE_ONLY",
                }
            raise HTTPException(422, "unsupported_synthetic_command")

    if profile in ("admin_api", "internal_api"):

        @app.get("/api/admin/state/{owner}/{kind}")
        def state(owner: str, kind: str, request: Request):
            _, token = actor(request, True)
            return gateway.request(owner, "GET", "/records/" + kind, token)

        @app.get("/api/admin/metrics/beta")
        def metrics(request: Request):
            _, token = actor(request, True)
            return gateway.request("analytics", "GET", "/records/metrics", token)

        @app.get("/api/admin/release/flags")
        def flags(request: Request):
            _, token = actor(request, True)
            return gateway.request("control", "GET", "/records/flags", token)

        @app.post("/api/admin/release/flags")
        async def flag(request: Request):
            body = await request.json()
            return JSONResponse(
                submit(request, "control", "set_flag", body["flag"], body),
                status_code=202,
            )

        @app.get("/api/admin/source-suggestions/pending")
        def suggestions(request: Request):
            _, token = actor(request, True)
            return gateway.request("control", "GET", "/records/suggestions", token)

        @app.post("/internal/source-suggestions")
        async def suggestion(request: Request):
            a, _ = actor(request, True)
            body = await request.json()
            return JSONResponse(
                submit(
                    request,
                    "control",
                    "suggest_source",
                    str(uuid4()),
                    {
                        "source_id": body["source_identifier"],
                        "user_id": body.get("user_id", a["id"]),
                    },
                ),
                status_code=202,
            )

        @app.post("/api/admin/source-suggestions/{suggestion_id}/approve")
        async def approve(suggestion_id: str, request: Request):
            return JSONResponse(
                submit(request, "control", "approve_source", suggestion_id, {}),
                status_code=202,
            )

        @app.post("/api/admin/sources/enable")
        async def enable(request: Request):
            body = await request.json()
            return JSONResponse(
                submit(
                    request,
                    "listener",
                    "set_subscription",
                    body["identifier"],
                    {
                        "enabled": True,
                        "recipe_key": body.get("parser_key", "generic.single_listing"),
                    },
                ),
                status_code=202,
            )

        @app.post("/api/admin/manual-review/{canonical_id}/actions")
        async def review(canonical_id: str, request: Request):
            body = await request.json()
            action = body.get("action")
            args = {
                "canonical_id": canonical_id,
                "reason": body.get("reason", "synthetic-review"),
            }
            if action in ("approve", "approve_as_new", "reject"):
                args["approved"] = action != "reject"
                cmd = "review"
            elif action == "merge":
                cmd = "merge"
                args["target_id"] = body["target_id"]
            else:
                raise HTTPException(422, "unsupported_review_action")
            return JSONResponse(
                submit(request, "catalog", cmd, canonical_id, args), status_code=202
            )

        @app.post("/api/admin/audience-tags/{tag}/approve")
        async def tag_approve(tag: str, request: Request):
            return JSONResponse(
                submit(request, "taxonomy", "approve_tag", tag, {"tag": tag}),
                status_code=202,
            )

        @app.post("/api/admin/audience-tags/{tag}/merge")
        async def tag_merge(tag: str, request: Request):
            return JSONResponse(
                submit(
                    request,
                    "taxonomy",
                    "remap_tag",
                    tag,
                    {"tag": tag, "replacement": (await request.json())["replacement"]},
                ),
                status_code=202,
            )

        @app.post("/api/admin/telegram/{path:path}")
        def no_live(path: str, request: Request):
            actor(request, True)
            raise HTTPException(409, "FAKE_ONLY_live_sessions_forbidden")

    return app
