from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from estateflow.adapters.contracts import AdapterManifest, TelegramUpdateModel
from estateflow.adapters.telethon_listener import TelethonClientAdapter
from estateflow.application.core.config import Settings
from estateflow.services.source_config import SourceConfig

PLUGIN_MANIFEST = AdapterManifest(
    name="telegram_existing_session",
    family="telegram",
    version="1.0.0",
    source_types=("telegram_channel", "telegram_group"),
    description="Telethon Telegram adapter wired to an existing local session file.",
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
class TelegramExistingSessionAdapter:
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
) -> TelegramExistingSessionAdapter:
    adapter: TelethonClientAdapter | _DisabledTelegramClientAdapter
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        adapter = _DisabledTelegramClientAdapter()
    else:
        adapter = TelethonClientAdapter(
            session_name=_resolve_session_name(
                settings.telegram_session_dir,
                default_session_name=settings.default_telegram_listener_account_key,
                session_name=session_name,
            ),
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
        )
    return TelegramExistingSessionAdapter(_adapter=adapter)


def _resolve_session_name(
    session_dir: str,
    *,
    default_session_name: str,
    session_name: str | None,
) -> str:
    base_dir = Path(session_dir)
    return str(base_dir / (session_name or default_session_name))
