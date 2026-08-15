from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from estateflow.application.core.config import Settings
from estateflow.services.telegram_webapp_auth import TelegramWebAppAuthService


def _init_data(token: str, *, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAH-test",
        "user": json.dumps(
            {"id": 42, "first_name": "Test", "username": "tester"},
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_telegram_webapp_auth_validates_init_data_and_session() -> None:
    token = "123456:ABCDEF"
    service = TelegramWebAppAuthService(
        Settings(
            environment="test",
            telegram_bot_token=SecretStr(token),
            app_secret_key=SecretStr("secret"),
        )
    )

    identity = service.authenticate(_init_data(token))
    session = service.issue_session(identity, expires_in=60)

    assert identity.user_id == 42
    assert identity.username == "tester"
    assert service.verify_session(session.access_token).user_id == 42


def test_telegram_webapp_auth_rejects_tampered_or_expired_data() -> None:
    token = "123456:ABCDEF"
    service = TelegramWebAppAuthService(
        Settings(
            environment="test",
            telegram_bot_token=SecretStr(token),
            app_secret_key=SecretStr("secret"),
        )
    )

    with pytest.raises(ValueError, match="signature"):
        service.authenticate(_init_data(token).replace("tester", "intruder"))
    with pytest.raises(ValueError, match="expired"):
        service.authenticate(_init_data(token, auth_date=int(time.time()) - 100_000))
