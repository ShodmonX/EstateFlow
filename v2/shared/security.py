"""Lab-only actor delegation and synthetic WebApp HMAC. No external I/O."""

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl, urlencode


def guard_fake_only(environ=None):
    env = os.environ if environ is None else environ
    if (
        env.get("EFV2_PROVIDER_MODE", "FAKE_ONLY") != "FAKE_ONLY"
        or env.get("EFV2_MODE", "FAKE_ONLY") != "FAKE_ONLY"
    ):
        raise ValueError("live_providers_forbidden")
    for key in (
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION_DIR",
        "TELEGRAM_SESSION_NAME",
        "R2_ENDPOINT_URL",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "DATABASE_URL",
    ):
        if env.get(key):
            raise ValueError("external_configuration_forbidden")


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_actor(subject, roles, key, ttl=900, now=None):
    if len(key) < 16:
        raise ValueError("weak_lab_key")
    at = time.time() if now is None else now
    body = b64(
        json.dumps(
            {
                "id": str(subject),
                "roles": roles,
                "iat": at,
                "exp": at + ttl,
                "iss": "efv2-lab",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return body + "." + b64(hmac.digest(key.encode(), body.encode(), "sha256"))


def verify_actor(token, key, now=None):
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(decode(sig), hmac.digest(key.encode(), body.encode(), "sha256")):
            raise ValueError("bad_signature")
        obj = json.loads(decode(body))
        at = time.time() if now is None else now
        if (
            obj["iss"] != "efv2-lab"
            or obj["iat"] > at
            or obj["exp"] <= at
            or obj["exp"] - obj["iat"] > 86400
        ):
            raise ValueError("invalid_token_time")
        if (
            not isinstance(obj["id"], str)
            or not isinstance(obj["roles"], list)
            or any(x not in ("user", "admin", "service") for x in obj["roles"])
        ):
            raise ValueError("invalid_actor")
        return obj
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("unauthorized") from exc


def sign_webapp(user_id, bot_key, now=None):
    data = {
        "auth_date": str(int(time.time() if now is None else now)),
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
    }
    secret = hmac.digest(b"WebAppData", bot_key.encode(), "sha256")
    check = "\n".join(k + "=" + data[k] for k in sorted(data))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def verify_webapp(raw, bot_key, now=None):
    try:
        pairs = parse_qsl(raw, keep_blank_values=True)
        if len({k for k, _ in pairs}) != len(pairs):
            raise ValueError("duplicate_field")
        data = dict(pairs)
        provided = data.pop("hash")
        secret = hmac.digest(b"WebAppData", bot_key.encode(), "sha256")
        check = "\n".join(k + "=" + data[k] for k in sorted(data))
        if not hmac.compare_digest(
            provided, hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        ):
            raise ValueError("bad_signature")
        at = time.time() if now is None else now
        date = int(data["auth_date"])
        if date > at + 60 or at - date > 86400:
            raise ValueError("expired_or_future")
        user = json.loads(data["user"])
        uid = int(user["id"])
        if uid <= 0:
            raise ValueError("invalid_user")
        return {"id": str(uid), "roles": ["user"]}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_webapp_auth") from exc
