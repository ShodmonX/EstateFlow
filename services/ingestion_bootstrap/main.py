from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from estateflow.application.core.config import Settings, get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.db.session import create_session_manager
from estateflow.models.listeners import IngestionSourceRecord, ListenerAccountRecord
from estateflow.models.release_controls import FeatureFlagStateRecord
from estateflow.services.source_config import SourceConfig, SourceType


class IngestionSourceSpec(BaseModel):
    """Declarative source entry accepted from INGESTION_SOURCES_JSON."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["telegram_channel", "telegram_group"] = "telegram_channel"
    identifier: str
    name: str
    adapter_name: str = "telegram_telethon"
    source_profile: str = "default"
    session_name: str = "acc_9889"

    @field_validator("identifier", "name", "adapter_name", "source_profile", "session_name")
    @classmethod
    def value_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned


class SourceRepository(Protocol):
    async def create_or_enable_source(
        self,
        *,
        source_type: Literal["telegram_channel", "telegram_group"],
        identifier: str,
        name: str,
        adapter_name: str | None = None,
        source_profile: str | None = None,
        session_name: str | None = None,
    ) -> tuple[SourceConfig, bool]: ...

    async def list_active_sources(self) -> list[SourceConfig]: ...


class ListenerFlagProvider(Protocol):
    async def listener_sources_enabled(self) -> bool: ...


class SQLAlchemyIngestionBootstrapStore:
    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        *,
        initial_listener_enabled: bool,
    ) -> None:
        self._session_factory = session_factory
        self._initial_listener_enabled = initial_listener_enabled

    async def create_or_enable_source(
        self,
        *,
        source_type: Literal["telegram_channel", "telegram_group"],
        identifier: str,
        name: str,
        adapter_name: str | None = None,
        source_profile: str | None = None,
        session_name: str | None = None,
    ) -> tuple[SourceConfig, bool]:
        source_id = f"{source_type}:{identifier}"
        listener_key = session_name or "acc_9889"
        async with self._session_factory() as session:
            account = await session.scalar(
                select(ListenerAccountRecord).where(
                    ListenerAccountRecord.account_key == listener_key
                )
            )
            if account is None:
                session.add(
                    ListenerAccountRecord(
                        account_key=listener_key,
                        enabled=True,
                        health_status="online",
                        flood_wait_count=0,
                    )
                )
            row = await session.scalar(
                select(IngestionSourceRecord).where(IngestionSourceRecord.source_id == source_id)
            )
            created = row is None
            if row is None:
                row = IngestionSourceRecord(
                    source_id=source_id,
                    name=name,
                    source_type=source_type,
                    identifier=identifier,
                    enabled=True,
                    adapter_name=adapter_name,
                    source_profile=source_profile,
                    listener_account_key=listener_key,
                )
                session.add(row)
            else:
                row.enabled = True
                row.name = name
                row.adapter_name = adapter_name or row.adapter_name
                row.source_profile = source_profile or row.source_profile
                row.listener_account_key = listener_key
            await session.commit()
            await session.refresh(row)
            return _source_from_record(row), created

    async def list_active_sources(self) -> list[SourceConfig]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(IngestionSourceRecord)
                    .where(IngestionSourceRecord.enabled.is_(True))
                    .where(IngestionSourceRecord.deleted_at.is_(None))
                    .order_by(IngestionSourceRecord.source_id)
                )
            ).all()
            return [_source_from_record(row) for row in rows]

    async def listener_sources_enabled(self) -> bool:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(FeatureFlagStateRecord).where(
                    FeatureFlagStateRecord.flag_name == "listener_sources"
                )
            )
            if row is None:
                row = FeatureFlagStateRecord(
                    flag_name="listener_sources",
                    enabled=self._initial_listener_enabled,
                    version=1,
                    updated_at=datetime.now(UTC),
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return bool(row.enabled)


def parse_source_specs(raw: str) -> list[IngestionSourceSpec]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("INGESTION_SOURCES_JSON must contain valid JSON") from exc
    if isinstance(payload, dict):
        payload = payload.get("sources")
    if not isinstance(payload, list):
        raise ValueError("INGESTION_SOURCES_JSON must be a JSON array or an object with sources")
    return [IngestionSourceSpec.model_validate(item) for item in payload]


async def bootstrap_sources(
    settings: Settings,
    repository: SourceRepository,
    flag_store: ListenerFlagProvider,
) -> dict[str, int]:
    specs = parse_source_specs(settings.ingestion_sources_json)
    created_count = 0
    for spec in specs:
        _source, created = await repository.create_or_enable_source(
            source_type=spec.source_type,
            identifier=spec.identifier,
            name=spec.name,
            adapter_name=spec.adapter_name,
            source_profile=spec.source_profile,
            session_name=spec.session_name,
        )
        created_count += int(created)

    active_sources = await repository.list_active_sources()
    telegram_sources = [
        source
        for source in active_sources
        if source.source_type in {"telegram_channel", "telegram_group"}
    ]
    if not telegram_sources:
        raise RuntimeError(
            "No active Telegram ingestion source exists. Configure INGESTION_SOURCES_JSON."
        )

    if not await flag_store.listener_sources_enabled():
        raise RuntimeError(
            "listener_sources feature flag is disabled. Set "
            "FEATURE_LISTENER_SOURCES_ENABLED=true for the initial deployment or enable it "
            "through an operator-controlled migration."
        )
    return {
        "configured": len(specs),
        "created": created_count,
        "active_telegram_sources": len(telegram_sources),
    }


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    database = create_session_manager(settings)
    try:
        store = SQLAlchemyIngestionBootstrapStore(
            database.session,
            initial_listener_enabled=settings.feature_listener_sources_enabled,
        )
        result = await bootstrap_sources(settings, store, store)
        print(json.dumps({"status": "ready", **result}, sort_keys=True))
    finally:
        await database.dispose()


def _source_from_record(record: IngestionSourceRecord) -> SourceConfig:
    return SourceConfig(
        source_id=record.source_id,
        name=record.name,
        source_type=cast(SourceType, record.source_type),
        identifier=record.identifier,
        enabled=record.enabled,
        adapter_name=record.adapter_name,
        source_profile=record.source_profile,
        listener_account_key=record.listener_account_key,
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
