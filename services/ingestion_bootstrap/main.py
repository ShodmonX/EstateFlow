from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from estateflow.application.core.config import Settings, get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.db.session import create_session_manager
from estateflow.models.listeners import IngestionSourceRecord, ListenerAccountRecord
from estateflow.models.release_controls import FeatureFlagStateRecord
from estateflow.services.source_config import (
    ParserMode,
    SourceConfig,
    SourceType,
    merge_source_parser_binding,
    normalize_parser_key,
    normalize_parser_version,
    validate_parser_config,
    validate_source_config_parser_binding,
)
from estateflow.services.source_parsing import validate_source_parser_binding


class IngestionSourceSpec(BaseModel):
    """Declarative source entry accepted from INGESTION_SOURCES_JSON."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["telegram_channel", "telegram_group"] = "telegram_channel"
    identifier: str
    name: str
    adapter_name: str = "telegram_telethon"
    source_profile: str = "default"
    parser_key: str | None = None
    parser_version: str | None = None
    parser_config: dict[str, Any] | None = None
    parser_mode: Literal["active", "shadow", "disabled"] | None = None
    session_name: str = "acc_9889"

    @field_validator("identifier", "name", "adapter_name", "source_profile", "session_name")
    @classmethod
    def value_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned

    @field_validator("parser_key")
    @classmethod
    def parser_key_must_be_valid(cls, value: str | None) -> str | None:
        return normalize_parser_key(value)

    @field_validator("parser_version")
    @classmethod
    def parser_version_must_be_valid(cls, value: str | None) -> str | None:
        return normalize_parser_version(value)

    @field_validator("parser_config")
    @classmethod
    def parser_config_must_be_json_object(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return validate_parser_config(value) if value is not None else None


class SourceRepository(Protocol):
    async def create_or_enable_source(
        self,
        *,
        source_type: Literal["telegram_channel", "telegram_group"],
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
        parser_key: str | None = None,
        parser_version: str | None = None,
        parser_config: dict[str, Any] | None = None,
        parser_mode: ParserMode | None = None,
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
            existing_source = _source_from_record(row) if row is not None else None
            resolved_source_profile = source_profile or (
                existing_source.source_profile if existing_source is not None else None
            )
            binding = merge_source_parser_binding(
                existing_source,
                source_profile=resolved_source_profile,
                parser_key=parser_key,
                parser_version=parser_version,
                parser_config=parser_config,
                parser_mode=parser_mode,
            )
            source = SourceConfig(
                source_id=source_id,
                name=name,
                source_type=source_type,
                identifier=identifier,
                enabled=True,
                adapter_name=adapter_name or (row.adapter_name if row is not None else None),
                source_profile=resolved_source_profile,
                listener_account_key=listener_key,
                parser_key=binding.parser_key,
                parser_version=binding.parser_version,
                parser_config=binding.parser_config,
                parser_mode=binding.parser_mode,
            )
            validate_source_config_parser_binding(source)
            if row is None:
                row = IngestionSourceRecord(
                    source_id=source.source_id,
                    name=source.name,
                    source_type=source.source_type,
                    identifier=source.identifier,
                    enabled=source.enabled,
                    adapter_name=source.adapter_name,
                    source_profile=source.source_profile,
                    parser_key=source.parser_key,
                    parser_version=source.parser_version,
                    parser_config=source.parser_config,
                    parser_mode=source.parser_mode,
                    listener_account_key=source.listener_account_key,
                )
                session.add(row)
            else:
                row.enabled = source.enabled
                row.name = source.name
                row.adapter_name = source.adapter_name
                row.source_profile = source.source_profile
                row.parser_key = source.parser_key
                row.parser_version = source.parser_version
                row.parser_config = source.parser_config
                row.parser_mode = source.parser_mode
                row.listener_account_key = source.listener_account_key
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
    specs: list[IngestionSourceSpec] = []
    for index, item in enumerate(payload):
        spec = IngestionSourceSpec.model_validate(item)
        if spec.parser_key is not None:
            try:
                validate_source_parser_binding(
                    source_id=f"{spec.source_type}:{spec.identifier}",
                    parser_key=spec.parser_key,
                    parser_version=spec.parser_version,
                    parser_config=spec.parser_config,
                    parser_mode=spec.parser_mode or "active",
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"INGESTION_SOURCES_JSON source at index {index} has an invalid "
                    f"parser binding: {exc}"
                ) from exc
        specs.append(spec)
    return specs


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
            parser_key=spec.parser_key,
            parser_version=spec.parser_version,
            parser_config=spec.parser_config,
            parser_mode=spec.parser_mode,
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
        parser_key=record.parser_key,
        parser_version=record.parser_version,
        parser_config=record.parser_config or {},
        parser_mode=cast(ParserMode, record.parser_mode),
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
