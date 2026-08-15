from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from estateflow.application.core.config import Settings


@dataclass(frozen=True)
class TelegramWebAppIdentity:
    user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


@dataclass(frozen=True)
class WebAppSession:
    access_token: str
    user: TelegramWebAppIdentity
    expires_in: int


class TelegramWebAppAuthService:
    def __init__(self, settings: Settings, *, max_age_seconds: int = 86_400) -> None:
        self._settings = settings
        self._max_age_seconds = max_age_seconds

    def authenticate(self, init_data: str) -> TelegramWebAppIdentity:
        fields = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = fields.pop("hash", "")
        if not received_hash:
            raise ValueError("Telegram WebApp init data is missing its hash.")
        bot_token = self._settings.telegram_bot_token
        if bot_token is None:
            raise ValueError("Telegram bot token is not configured.")
        data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.get_secret_value().encode(),
            hashlib.sha256,
        ).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            raise ValueError("Telegram WebApp init data signature is invalid.")
        try:
            auth_date = int(fields["auth_date"])
            raw_user = json.loads(fields["user"])
            user_id = int(raw_user["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Telegram WebApp init data is incomplete.") from exc
        if user_id <= 0 or time.time() - auth_date > self._max_age_seconds:
            raise ValueError("Telegram WebApp init data has expired.")
        return TelegramWebAppIdentity(
            user_id=user_id,
            username=raw_user.get("username"),
            first_name=raw_user.get("first_name"),
            last_name=raw_user.get("last_name"),
        )

    def issue_session(
        self,
        user: TelegramWebAppIdentity,
        *,
        expires_in: int = 86_400,
    ) -> WebAppSession:
        secret = self._signing_secret()
        expires_at = int(time.time()) + expires_in
        payload = f"{user.user_id}.{expires_at}".encode()
        signature = hmac.new(secret, payload, hashlib.sha256).digest()
        token = ".".join(
            (
                _b64encode(payload),
                _b64encode(signature),
            )
        )
        return WebAppSession(access_token=token, user=user, expires_in=expires_in)

    def verify_session(self, token: str) -> TelegramWebAppIdentity:
        try:
            payload_part, signature_part = token.split(".", maxsplit=1)
            payload = _b64decode(payload_part)
            received_signature = _b64decode(signature_part)
            user_id_text, expires_at_text = payload.decode().split(".", maxsplit=1)
            user_id = int(user_id_text)
            expires_at = int(expires_at_text)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("WebApp access token is malformed.") from exc
        expected_signature = hmac.new(self._signing_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_signature, received_signature):
            raise ValueError("WebApp access token is invalid.")
        if user_id <= 0 or expires_at <= int(time.time()):
            raise ValueError("WebApp access token has expired.")
        return TelegramWebAppIdentity(user_id=user_id)

    def _signing_secret(self) -> bytes:
        if self._settings.app_secret_key is not None:
            return self._settings.app_secret_key.get_secret_value().encode()
        if self._settings.telegram_bot_token is not None:
            return self._settings.telegram_bot_token.get_secret_value().encode()
        raise ValueError("APP_SECRET_KEY or TELEGRAM_BOT_TOKEN is required for WebApp tokens.")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
