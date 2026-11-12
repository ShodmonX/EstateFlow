"""Deployed private service workflow over HTTP; no direct DB access and no real sends."""

import os
import time
from uuid import uuid4
import httpx
import pytest
from v2.shared.security import issue_actor, sign_webapp

pytestmark = pytest.mark.integration


def eventually(fn, check, timeout=30):
    end = time.monotonic() + timeout
    value = None
    while time.monotonic() < end:
        value = fn()
        if check(value):
            return value
        time.sleep(0.1)
    raise AssertionError("private workflow timeout: " + str(type(value).__name__))


def test_independent_services_http_to_fake_notification_then_edits_and_delete():
    if os.environ.get("EFV2_DEPLOYED_TESTS") != "1":
        pytest.skip("set EFV2_DEPLOYED_TESTS=1 only after private worker startup")
    key = os.environ["EFV2_AUTH_KEY"]
    bot = os.environ["EFV2_FAKE_BOT_TOKEN"]
    admin = issue_actor("synthetic-admin", ["admin"], key)
    user_id = int(uuid4().hex[:10], 16)
    suffix = uuid4().hex
    with httpx.Client(trust_env=False, timeout=5) as c:
        for service in (
            "listener",
            "source_parser",
            "pre_ai",
            "ai",
            "catalog",
            "search",
            "notifications",
            "identity",
            "control",
            "taxonomy",
            "content",
            "analytics",
            "public-api",
            "admin-api",
            "internal-api",
            "bot",
        ):
            response = eventually(
                lambda: c.get("http://" + service + ":8000/health/ready"),
                lambda r: r.status_code == 200,
                60,
            )
            assert response.json()["mode"] == "FAKE_ONLY"
        auth = c.post(
            "http://public-api:8000/api/webapp/auth",
            json={"init_data": sign_webapp(user_id, bot)},
        )
        assert auth.status_code == 200
        token = auth.json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        admin_headers = {"Authorization": "Bearer " + admin}

        def submit(owner, action, subject, arguments, admin_only=False):
            base = "http://admin-api:8000" if admin_only else "http://public-api:8000"
            h = admin_headers if admin_only else headers
            response = c.post(
                base + "/commands/" + owner,
                headers=h,
                json={"action": action, "subject_id": subject, "arguments": arguments},
            )
            assert response.status_code == 202, response.text
            operation = response.json()["operation_id"]
            result = eventually(
                lambda: c.get(base + "/operations/" + owner + "/" + operation, headers=h),
                lambda r: r.status_code == 200 and r.json()["status"] == "completed",
            )
            return result.json()

        submit(
            "identity",
            "upsert_user",
            str(user_id),
            {"user_id": str(user_id), "consent": True},
        )
        submit(
            "notifications",
            "save_filter",
            "filter-" + suffix,
            {
                "user_id": str(user_id),
                "filter_id": "filter-" + suffix,
                "criteria": {"max_price": 200},
            },
        )
        args = {
            "source_id": "source-" + suffix,
            "channel_id": "synthetic-channel",
            "message_id": "m1",
            "update_type": "received",
            "text": "SYNTHETIC rent 100 USD 2 rooms " + suffix,
        }
        submit("listener", "ingest", "m1", args, True)

        def listing():
            return c.get(
                "http://public-api:8000/api/search/announcements",
                headers=headers,
                params={"limit": 50},
            ).json()

        found = eventually(
            listing,
            lambda value: any(suffix in x["listing"]["description"] for x in value["items"]),
        )
        row = next(x for x in found["items"] if suffix in x["listing"]["description"])
        canonical = row["canonical_id"]
        jobs = eventually(
            lambda: c.get(
                "http://admin-api:8000/api/admin/state/notifications/jobs",
                headers=admin_headers,
            ).json(),
            lambda values: any(
                x["user_id"] == str(user_id)
                and x["canonical_id"] == canonical
                and x["status"] == "sent"
                for x in values
            ),
        )
        assert (
            len(
                [x for x in jobs if x["user_id"] == str(user_id) and x["canonical_id"] == canonical]
            )
            == 1
        )
        for price in (120, 130):
            submit(
                "listener",
                "ingest",
                "m1",
                dict(
                    args,
                    update_type="edited",
                    text=f"SYNTHETIC rent {price} USD 2 rooms " + suffix,
                ),
                True,
            )
        eventually(
            listing,
            lambda value: any(
                x["canonical_id"] == canonical and x["listing"]["price"] == 130
                for x in value["items"]
            ),
        )
        submit(
            "listener",
            "ingest",
            "m1",
            {k: v for k, v in dict(args, update_type="deleted").items() if k != "text"},
            True,
        )
        eventually(
            lambda: c.get(
                "http://public-api:8000/api/webapp/announcements/" + canonical,
                headers=headers,
            ),
            lambda r: r.status_code == 404,
        )
        assert (
            c.post(
                "http://admin-api:8000/api/admin/telegram/auth/start",
                headers=admin_headers,
                json={},
            ).status_code
            == 409
        )


def test_failed_operation_is_observable_and_incomplete_merge_is_rejected():
    if os.environ.get("EFV2_DEPLOYED_TESTS") != "1":
        pytest.skip("deployed private stack required")
    key = os.environ["EFV2_AUTH_KEY"]
    headers = {"Authorization": "Bearer " + issue_actor("synthetic-admin", ["admin"], key)}
    with httpx.Client(trust_env=False, timeout=5) as client:
        base = "http://admin-api:8000"
        bad = client.post(
            base + "/commands/catalog",
            headers=headers,
            json={"action": "merge", "subject_id": "synthetic-missing", "arguments": {}},
        )
        assert bad.status_code == 422
        accepted = client.post(
            base + "/commands/catalog",
            headers=headers,
            json={
                "action": "review",
                "subject_id": "synthetic-missing",
                "arguments": {"approved": True},
            },
        )
        assert accepted.status_code == 202
        operation = accepted.json()["operation_id"]
        failed = eventually(
            lambda: client.get(base + "/operations/catalog/" + operation, headers=headers),
            lambda r: r.status_code == 200 and r.json()["status"] == "failed",
        )
        assert failed.json()["reason"] == "permanent_failure"
        user = {"Authorization": "Bearer " + issue_actor("synthetic-user", ["user"], key)}
        assert (
            client.get(base + "/operations/catalog/" + operation, headers=user).status_code == 403
        )
        invalid = client.get(
            "http://public-api:8000/api/search/announcements",
            headers=user,
            params={"min_price": 200, "max_price": 100},
        )
        assert invalid.status_code == 422


def test_private_lab_has_no_default_network_route():
    from pathlib import Path
    import socket

    if os.environ.get("EFV2_DEPLOYED_TESTS") != "1":
        pytest.skip("deployed private network required")
    routes = Path("/proc/net/route").read_text().splitlines()[1:]
    assert not any(line.split()[1] == "00000000" for line in routes)
    with socket.socket() as client:
        client.settimeout(1)
        # TEST-NET-2 documentation address; no third-party provider is contacted.
        assert client.connect_ex(("198.51.100.1", 443)) != 0
