"""Explicit offline extraction adapter; this module never opens a network client.

The fake recognises a few synthetic fixture tokens. It is not an LLM or evidence
of real-provider extraction parity. Provider failures are injected by mode so
the owner can persist retry/terminal outcomes without a live external effect.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit
from collections.abc import Mapping
from typing import Any, Protocol


class OfflineConfigurationError(ValueError):
    """A caller attempted to configure a live provider in the offline lab."""


class ProviderInvalidResponse(ValueError):
    """The extraction adapter did not return an acceptable listing."""


class ProviderRateLimit(RuntimeError):
    """Retryable synthetic rate limit with a deterministic delay."""

    retry_after = 5


class ProviderTimeout(TimeoutError):
    """Synthetic timeout; no external request has been made."""


def guard_offline_configuration(
    environ: Mapping[str, str] | None = None, **configuration: Any
) -> None:
    """Reject external endpoints, credentials and session configuration.

    An injected environment is useful for deterministic tests. With no mapping,
    the current process environment is checked each time an adapter is used.
    Values never appear in the exception or in logs. Lab DB/broker connection
    settings are deliberately outside this media/provider guard's remit.
    """
    settings = dict(os.environ if environ is None else environ)
    settings.update(configuration)
    external_prefixes = (
        "OPENAI",
        "OPENROUTER",
        "ANTHROPIC",
        "GEMINI",
        "TELEGRAM",
        "TELETHON",
        "AWS_",
        "R2_",
        "S3_",
        "CLOUDFLARE_",
        "AZURE_OPENAI",
    )
    credential_markers = (
        "API_KEY",
        "API_TOKEN",
        "ACCESS_KEY",
        "SECRET_KEY",
        "CREDENTIAL",
        "SESSION",
        "BOT_TOKEN",
    )
    for raw_key, value in settings.items():
        if value is None or value == "":
            continue
        key = str(raw_key).upper()
        owners = {
            "LISTENER",
            "SOURCE_PARSER",
            "PRE_AI",
            "AI",
            "CATALOG",
            "SEARCH",
            "NOTIFICATIONS",
            "IDENTITY",
            "CONTROL",
            "TAXONOMY",
            "CONTENT",
            "ANALYTICS",
            "LAB_PRODUCER",
            "LAB_CONSUMER",
        }
        lab_secrets = {"EFV2_PASSWORD_" + owner for owner in owners} | {
            "EFV2_MIGRATE_PASSWORD_" + owner for owner in owners
        }
        if key in lab_secrets or key == "EFV2_AUTH_KEY":
            continue
        if key in {"EFV2_DSN", "EFV2_ADMIN_DSN"} | {"EFV2_DSN_" + owner for owner in owners}:
            parsed = urlsplit(str(value))
            if parsed.hostname != "postgres" or parsed.path != "/estateflow_v2_lab":
                raise OfflineConfigurationError("database outside private lab")
            continue
        if key == "EFV2_BROKER_URL":
            if urlsplit(str(value)).hostname != "rabbitmq":
                raise OfflineConfigurationError("broker outside private lab")
            continue
        if key in {"EFV2_MODE", "EFV2_PROVIDER_MODE"}:
            if value != "FAKE_ONLY":
                raise OfflineConfigurationError("offline lab requires FAKE_ONLY mode")
            continue
        if key == "EFV2_FAKE_BOT_TOKEN":
            # Generated solely for local WebApp HMAC fixtures. Its fixed
            # synthetic prefix cannot be mistaken for a live bot token.
            if not isinstance(value, str) or not re.fullmatch(r"synthetic:[0-9a-f]{48}", value):
                raise OfflineConfigurationError("offline lab requires a synthetic bot key")
            continue
        if key in {
            "PROVIDER",
            "EF_V2_PROVIDER",
            "EF_V2_MEDIA_PROVIDER",
            "EF_V2_STORAGE_PROVIDER",
        }:
            if str(value).lower() not in {"fake", "offline"}:
                raise OfflineConfigurationError("offline lab rejects live provider configuration")
            continue
        scoped = key.startswith(external_prefixes) or key.startswith(("EF_V2_", "EFV2_"))
        generic = key in {
            "ENDPOINT",
            "ENDPOINT_URL",
            "BASE_URL",
            "TOKEN",
            "KEY",
            "SECRET",
            "API_ID",
            "API_HASH",
        }
        if (
            generic
            or any(marker in key for marker in credential_markers)
            or (
                scoped
                and any(
                    marker in key
                    for marker in (
                        "ENDPOINT",
                        "BASE_URL",
                        "TOKEN",
                        "SECRET",
                        "PASSWORD",
                        "API_ID",
                        "API_HASH",
                    )
                )
            )
        ):
            raise OfflineConfigurationError(
                "offline lab rejects endpoint, credential or session configuration"
            )


class ExtractionProvider(Protocol):
    def extract(self, text: str, media: Any = None, mode: str = "success") -> dict[str, Any]: ...


class FakeExtractionProvider:
    """Deterministic fixture parser with no sessions, tokens or transport."""

    def __init__(self, *, environ: Mapping[str, str] | None = None, **configuration: Any):
        guard_offline_configuration(environ, **configuration)
        if configuration:
            raise OfflineConfigurationError("offline adapter accepts no provider configuration")
        self._environ = environ

    def extract(self, text: str, media: Any = None, mode: str = "success") -> dict[str, Any]:
        guard_offline_configuration(self._environ)
        if not isinstance(text, str) or len(text) > 4000:
            raise ProviderInvalidResponse("text must be a bounded string")
        if media is not None:
            # Opaque offline references or already-processed bytes only. This
            # fake does not resolve media or infer listing facts from images.
            items = media if isinstance(media, (list, tuple)) else [media]
            for item in items:
                if not isinstance(item, bytes) and not (
                    isinstance(item, str) and item.startswith("offline://objects/")
                ):
                    raise ProviderInvalidResponse("only offline media is accepted")
        if mode == "invalid":
            raise ProviderInvalidResponse("synthetic invalid extraction response")
        if mode == "rate_limit":
            raise ProviderRateLimit("synthetic provider rate limit")
        if mode == "timeout":
            raise ProviderTimeout("synthetic provider timeout")
        if mode not in {"success", "fallback"}:
            raise ProviderInvalidResponse("unsupported offline provider mode")
        listing: dict[str, Any] = {
            "description": text,
            "price": None,
            "currency": None,
            "price_period": "unknown",
            "price_basis": "unknown",
            "rooms": None,
            "district": None,
            "taxonomy_revision": 0,
            "audience_tags": [],
            "contact_ref": None,
        }
        if mode == "success":
            room_match = re.search(r"\b(\d{1,2}(?:\.\d)?)\s+rooms?\b", text, re.IGNORECASE)
            price_match = re.search(
                r"\b(?:(?P<amount_first>\d{1,12}(?:\.\d{1,2})?)\s+(?P<currency_after>USD|UZS)"
                r"|(?P<currency_first>USD|UZS)\s+(?P<amount_after>\d{1,12}(?:\.\d{1,2})?))\b",
                text,
                re.IGNORECASE,
            )
            if room_match:
                listing["rooms"] = float(room_match[1])
            if price_match:
                listing["currency"] = (
                    price_match["currency_after"] or price_match["currency_first"]
                ).upper()
                listing["price"] = float(price_match["amount_first"] or price_match["amount_after"])
                listing["price_basis"] = "total"
            for period in ("monthly", "daily", "one_time"):
                if re.search(rf"\b{period}\b", text, re.IGNORECASE):
                    listing["price_period"] = period
                    break
            if listing["price_period"] == "unknown":
                if re.search(r"\bsale\b", text, re.IGNORECASE):
                    listing["price_period"] = "one_time"
                elif re.search(r"\brent\b", text, re.IGNORECASE):
                    listing["price_period"] = "monthly"
        return listing
