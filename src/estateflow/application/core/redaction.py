from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEYWORDS = (
    "api_key",
    "api_hash",
    "announcement_text",
    "authorization",
    "bot_token",
    "message_text",
    "password",
    "raw_announcement",
    "raw_text",
    "secret",
    "session",
    "token",
)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?998)?[\s()-]*(?:\d[\s()-]*){9,12}(?!\d)")
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{25,}\b")
_API_KEY_PATTERN = re.compile(r"\b(?:sk|or|r2)_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str) -> str:
    redacted = _TELEGRAM_BOT_TOKEN_PATTERN.sub(REDACTED, value)
    redacted = _API_KEY_PATTERN.sub(REDACTED, redacted)
    return _PHONE_PATTERN.sub(REDACTED, redacted)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(keyword in normalized for keyword in _SENSITIVE_KEYWORDS)
