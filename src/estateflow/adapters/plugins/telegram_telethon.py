# ruff: noqa: E501,I001,F401
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from estateflow.adapters.contracts import AdapterManifest, TelegramUpdateModel
from estateflow.adapters.telethon_listener import TelethonClientAdapter
from estateflow.application.core.config import Settings
from estateflow.services.source_config import SourceConfig

PLUGIN_MANIFEST = AdapterManifest(
    name="telegram_telethon",
    family="telegram",
    version="1.0.0",
    source_types=("telegram_channel", "telegram_group"),
    description="Telethon-powered Telegram listener adapter.",
)


@dataclass
class _DisabledTelegramClientAdapter:
    async def connect(self) -> None:
        return None

    async def subscribe(self, sources: list[SourceConfig]) -> None:
        return None

    def iter_updates(self) -> AsyncIterator[object]:
        async def _empty() -> AsyncIterator[object]:
            if False:
                yield None

        return _empty()

    async def disconnect(self) -> None:
        return None


@dataclass
class TelegramTelethonAdapter:
    manifest: AdapterManifest = field(default_factory=lambda: PLUGIN_MANIFEST)
    output_model: type[TelegramUpdateModel] = TelegramUpdateModel
    _adapter: TelethonClientAdapter | _DisabledTelegramClientAdapter | None = None

    async def connect(self) -> None:
        await self._require_adapter().connect()

    async def subscribe(self, sources: list[SourceConfig]) -> None:
        await self._require_adapter().subscribe(sources)

    def iter_updates(self) -> AsyncIterator[object]:
        return self._require_adapter().iter_updates()

    async def disconnect(self) -> None:
        await self._require_adapter().disconnect()

    def _require_adapter(self) -> TelethonClientAdapter | _DisabledTelegramClientAdapter:
        if self._adapter is None:
            raise RuntimeError("Telegram adapter has not been initialized.")
        return self._adapter


def create_plugin(
    settings: Settings,
    *,
    session_name: str | None = None,
) -> TelegramTelethonAdapter:
    adapter: TelethonClientAdapter | _DisabledTelegramClientAdapter
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        adapter = _DisabledTelegramClientAdapter()
    else:
        adapter = TelethonClientAdapter(
            session_name=_resolve_session_name(settings, session_name=session_name),
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
        )
    return TelegramTelethonAdapter(_adapter=adapter)


def _resolve_session_name(settings: Settings, *, session_name: str | None) -> str:
    base_dir = Path(settings.telegram_session_dir)
    resolved_name = session_name or settings.default_telegram_listener_account_key
    return str(base_dir / resolved_name)
