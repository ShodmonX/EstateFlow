from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

SourceType = Literal["telegram_channel", "telegram_group", "website"]
ParserMode = Literal["active", "shadow", "disabled"]

DEFAULT_PARSER_KEY = "generic.single_listing"
DEFAULT_PARSER_VERSION = "1"
PARSER_MODES = frozenset({"active", "shadow", "disabled"})
_PARSER_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_PARSER_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_MAX_PARSER_CONFIG_BYTES = 32 * 1024
_MAX_PARSER_CONFIG_DEPTH = 8

LEGACY_SOURCE_PROFILE_TO_PARSER_KEY: dict[str, str] = {
    "default": DEFAULT_PARSER_KEY,
    "caption_first": "generic.album_caption",
    "album_text_merge": "generic.album_caption",
}


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    name: str
    source_type: SourceType
    identifier: str
    enabled: bool = True
    adapter_name: str | None = None
    source_profile: str | None = None
    listener_account_key: str | None = None
    parser_key: str | None = None
    parser_version: str | None = None
    parser_config: dict[str, Any] = field(default_factory=dict)
    parser_mode: ParserMode = "active"

    def __post_init__(self) -> None:
        object.__setattr__(self, "parser_key", normalize_parser_key(self.parser_key))
        object.__setattr__(
            self,
            "parser_version",
            normalize_parser_version(self.parser_version),
        )
        object.__setattr__(self, "parser_config", validate_parser_config(self.parser_config))
        object.__setattr__(self, "parser_mode", normalize_parser_mode(self.parser_mode))

    @property
    def session_name(self) -> str | None:
        return self.listener_account_key

    @property
    def effective_parser_key(self) -> str:
        return self.parser_key or parser_key_from_legacy_profile(self.source_profile)

    @property
    def effective_parser_version(self) -> str:
        return self.parser_version or DEFAULT_PARSER_VERSION


@dataclass(frozen=True)
class ResolvedSourceParserBinding:
    parser_key: str | None
    parser_version: str | None
    parser_config: dict[str, Any]
    parser_mode: ParserMode


class SourceConfigProvider(Protocol):
    async def list_active_sources(self) -> list[SourceConfig]: ...


class SourceRegistry(Protocol):
    async def create_or_enable_source(
        self,
        *,
        source_type: SourceType,
        identifier: str,
        name: str,
        adapter_name: str | None = None,
        source_profile: str | None = None,
        parser_key: str | None = None,
        parser_version: str | None = None,
        parser_config: dict[str, Any] | None = None,
        parser_mode: ParserMode | None = None,
        session_name: str | None = None,
    ) -> tuple[SourceConfig, bool]: ...


class InMemorySourceRegistry:
    def __init__(self, sources: list[SourceConfig] | None = None) -> None:
        self.sources: dict[str, SourceConfig] = {
            _source_key(source.source_type, source.identifier): source for source in sources or []
        }

    async def list_active_sources(self) -> list[SourceConfig]:
        return sorted(
            [source for source in self.sources.values() if source.enabled],
            key=lambda source: source.source_id,
        )

    async def create_or_enable_source(
        self,
        *,
        source_type: SourceType,
        identifier: str,
        name: str,
        adapter_name: str | None = None,
        source_profile: str | None = None,
        parser_key: str | None = None,
        parser_version: str | None = None,
        parser_config: dict[str, Any] | None = None,
        parser_mode: ParserMode | None = None,
        session_name: str | None = None,
    ) -> tuple[SourceConfig, bool]:
        key = _source_key(source_type, identifier)
        existing = self.sources.get(key)
        if existing is not None:
            resolved_source_profile = existing.source_profile or source_profile
            binding = merge_source_parser_binding(
                existing,
                source_profile=resolved_source_profile,
                parser_key=parser_key,
                parser_version=parser_version,
                parser_config=parser_config,
                parser_mode=parser_mode,
            )
            enabled = SourceConfig(
                source_id=existing.source_id,
                name=existing.name,
                source_type=existing.source_type,
                identifier=existing.identifier,
                enabled=True,
                adapter_name=existing.adapter_name or adapter_name,
                source_profile=resolved_source_profile,
                listener_account_key=existing.listener_account_key or session_name,
                parser_key=binding.parser_key,
                parser_version=binding.parser_version,
                parser_config=binding.parser_config,
                parser_mode=binding.parser_mode,
            )
            validate_source_config_parser_binding(enabled)
            self.sources[key] = enabled
            return enabled, False
        source = SourceConfig(
            source_id=f"{source_type}:{identifier}",
            name=name,
            source_type=source_type,
            identifier=identifier,
            enabled=True,
            adapter_name=adapter_name,
            source_profile=source_profile,
            listener_account_key=session_name,
            parser_key=parser_key,
            parser_version=parser_version,
            parser_config=parser_config or {},
            parser_mode=parser_mode or "active",
        )
        validate_source_config_parser_binding(source)
        self.sources[key] = source
        return source, True


def _source_key(source_type: SourceType, identifier: str) -> str:
    return f"{source_type}:{identifier.casefold()}"


def parser_key_from_legacy_profile(source_profile: str | None) -> str:
    """Map every legacy profile to a known-safe parser.

    Unknown profile names used to become arbitrary passthrough strategies.  They now
    deliberately resolve to the generic parser while the original value remains
    available for audit and rollback.
    """

    normalized = (source_profile or "default").strip().casefold() or "default"
    return LEGACY_SOURCE_PROFILE_TO_PARSER_KEY.get(normalized, DEFAULT_PARSER_KEY)


def merge_source_parser_binding(
    existing: SourceConfig | None,
    *,
    source_profile: str | None,
    parser_key: str | None,
    parser_version: str | None,
    parser_config: dict[str, Any] | None,
    parser_mode: ParserMode | None,
) -> ResolvedSourceParserBinding:
    """Merge a patch-style parser update without carrying policy across identities."""

    resolved_key = (
        normalize_parser_key(parser_key)
        if parser_key is not None
        else existing.parser_key if existing is not None else None
    )
    next_effective_key = resolved_key or parser_key_from_legacy_profile(source_profile)
    identity_changed = (
        existing is not None and next_effective_key != existing.effective_parser_key
    )
    resolved_version = (
        normalize_parser_version(parser_version)
        if parser_version is not None
        else existing.parser_version
        if existing is not None and not identity_changed
        else None
    )
    resolved_config = (
        validate_parser_config(parser_config)
        if parser_config is not None
        else validate_parser_config(existing.parser_config)
        if existing is not None and not identity_changed
        else {}
    )
    resolved_mode = (
        normalize_parser_mode(parser_mode)
        if parser_mode is not None
        else existing.parser_mode
        if existing is not None
        else "active"
    )
    return ResolvedSourceParserBinding(
        parser_key=resolved_key,
        parser_version=resolved_version,
        parser_config=resolved_config,
        parser_mode=resolved_mode,
    )


def validate_source_config_parser_binding(source: SourceConfig) -> None:
    """Validate the exact parser binding that a listener will put on raw events."""

    # Local import avoids a module cycle: the parser router consumes RawTelegramEvent,
    # which itself depends on SourceConfig.
    from estateflow.services.source_parsing import validate_source_parser_binding

    validate_source_parser_binding(
        source_id=source.source_id,
        parser_key=source.effective_parser_key,
        parser_version=source.effective_parser_version,
        parser_config=source.parser_config,
        parser_mode=source.parser_mode,
    )


def normalize_parser_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if not normalized or not _PARSER_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            "parser_key must start with a lowercase letter or digit and contain only "
            "lowercase letters, digits, '.', '_' or '-'."
        )
    return normalized


def normalize_parser_version(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or not _PARSER_VERSION_PATTERN.fullmatch(normalized):
        raise ValueError("parser_version contains unsupported characters.")
    return normalized


def normalize_parser_mode(value: str) -> ParserMode:
    normalized = value.strip().casefold()
    if normalized not in PARSER_MODES:
        raise ValueError("parser_mode must be active, shadow, or disabled.")
    return cast(ParserMode, normalized)


def validate_parser_config(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("parser_config must be a JSON object.")
    _validate_json_value(value, depth=0)
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("parser_config must contain JSON-compatible values.") from exc
    if len(serialized.encode("utf-8")) > _MAX_PARSER_CONFIG_BYTES:
        raise ValueError("parser_config exceeds the 32 KiB limit.")
    # JSON round-tripping gives the frozen SourceConfig its own normalized copy.
    normalized = json.loads(serialized)
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        raise ValueError("parser_config must be a JSON object.")
    return normalized


def _validate_json_value(value: Any, *, depth: int) -> None:
    if depth > _MAX_PARSER_CONFIG_DEPTH:
        raise ValueError("parser_config nesting exceeds the supported depth.")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("parser_config numbers must be finite.")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("parser_config object keys must be strings.")
            _validate_json_value(item, depth=depth + 1)
        return
    raise ValueError("parser_config must contain JSON-compatible values.")
