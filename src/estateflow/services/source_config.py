from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

SourceType = Literal["telegram_channel", "telegram_group", "website"]


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

    @property
    def session_name(self) -> str | None:
        return self.listener_account_key


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
        session_name: str | None = None,
    ) -> tuple[SourceConfig, bool]:
        key = _source_key(source_type, identifier)
        existing = self.sources.get(key)
        if existing is not None:
            enabled = SourceConfig(
                source_id=existing.source_id,
                name=existing.name,
                source_type=existing.source_type,
                identifier=existing.identifier,
                enabled=True,
                adapter_name=existing.adapter_name or adapter_name,
                source_profile=existing.source_profile or source_profile,
                listener_account_key=existing.listener_account_key or session_name,
            )
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
        )
        self.sources[key] = source
        return source, True


def _source_key(source_type: SourceType, identifier: str) -> str:
    return f"{source_type}:{identifier.casefold()}"
