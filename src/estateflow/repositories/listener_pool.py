from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import asyncpg  # type: ignore[import-untyped]

from estateflow.services.listener_pool import (
    AssignmentAuditEvent,
    AssignmentReason,
    ChannelAssignment,
    ListenerAccountMetadata,
    ListenerHealthStatus,
)
from estateflow.services.source_config import (
    ParserMode,
    SourceConfig,
    SourceType,
    merge_source_parser_binding,
    validate_source_config_parser_binding,
)


class AsyncpgListenerAccountRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_accounts(self) -> list[ListenerAccountMetadata]:
        records = await self._pool.fetch(
            """
            select account_key, enabled, health_status, last_successful_event_at, flood_wait_count
            from listener_accounts
            order by account_key
            """
        )
        return [_account_from_record(record) for record in records]

    async def save_account(self, account: ListenerAccountMetadata) -> None:
        await self._pool.execute(
            """
            insert into listener_accounts (
                account_key,
                enabled,
                health_status,
                last_successful_event_at,
                flood_wait_count,
                updated_at
            )
            values ($1, $2, $3, $4, $5, $6)
            on conflict (account_key) do update set
                enabled = excluded.enabled,
                health_status = excluded.health_status,
                last_successful_event_at = excluded.last_successful_event_at,
                flood_wait_count = excluded.flood_wait_count,
                updated_at = excluded.updated_at
            """,
            account.account_key,
            account.enabled,
            account.health_status,
            account.last_successful_event_at,
            account.flood_wait_count,
            datetime.now(UTC),
        )


class AsyncpgSourceConfigRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_active_sources(self) -> list[SourceConfig]:
        records = await self._pool.fetch(
            """
            select source_id, name, source_type, identifier, enabled, adapter_name,
                   source_profile, parser_key, parser_version, parser_config,
                   parser_mode, listener_account_key
            from ingestion_sources
            where enabled = true
            order by source_id
            """
        )
        return [_source_from_record(record) for record in records]

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
        source_id = f"{source_type}:{identifier}"
        listener_key = session_name or "acc_9889"
        existing_record = await self._pool.fetchrow(
            """
            select source_id, name, source_type, identifier, enabled, adapter_name,
                   source_profile, parser_key, parser_version, parser_config,
                   parser_mode, listener_account_key
            from ingestion_sources
            where source_id = $1
            """,
            source_id,
        )
        existing_source = (
            _source_from_record(existing_record) if existing_record is not None else None
        )
        resolved_source_profile = (
            source_profile
            if source_profile is not None
            else existing_source.source_profile if existing_source is not None else None
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
            adapter_name=(
                adapter_name
                if adapter_name is not None
                else existing_source.adapter_name if existing_source is not None else None
            ),
            source_profile=resolved_source_profile,
            listener_account_key=listener_key,
            parser_key=binding.parser_key,
            parser_version=binding.parser_version,
            parser_config=binding.parser_config,
            parser_mode=binding.parser_mode,
        )
        validate_source_config_parser_binding(source)
        record = await self._pool.fetchrow(
            """
            insert into ingestion_sources (
                source_id,
                name,
                source_type,
                identifier,
                enabled,
                adapter_name,
                source_profile,
                parser_key,
                parser_version,
                parser_config,
                parser_mode,
                listener_account_key,
                updated_at
            )
            values (
                $1, $2, $3, $4, true, $5, $6, $7, $8, $9::jsonb, $10, $11, now()
            )
            on conflict (source_id) do update set
                enabled = true,
                name = excluded.name,
                adapter_name = excluded.adapter_name,
                source_profile = excluded.source_profile,
                parser_key = excluded.parser_key,
                parser_version = excluded.parser_version,
                parser_config = excluded.parser_config,
                parser_mode = excluded.parser_mode,
                listener_account_key = excluded.listener_account_key,
                updated_at = now()
            returning
                source_id, name, source_type, identifier, enabled, adapter_name,
                source_profile, parser_key, parser_version, parser_config,
                parser_mode, listener_account_key,
                (xmax = 0) as inserted
            """,
            source.source_id,
            source.name,
            source.source_type,
            source.identifier,
            source.adapter_name,
            source.source_profile,
            source.parser_key,
            source.parser_version,
            json.dumps(source.parser_config, sort_keys=True),
            source.parser_mode,
            source.listener_account_key,
        )
        assert record is not None
        return _source_from_record(record), bool(record["inserted"])


class AsyncpgChannelAssignmentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_active_assignments(self) -> list[ChannelAssignment]:
        records = await self._pool.fetch(
            """
            select source_id, account_key, assigned_at, active, released_at, reason
            from channel_assignments
            where active = true
            order by source_id, assigned_at
            """
        )
        return [_assignment_from_record(record) for record in records]

    async def save_assignment(self, assignment: ChannelAssignment) -> None:
        await self._pool.execute(
            """
            insert into channel_assignments (
                source_id,
                account_key,
                assigned_at,
                active,
                released_at,
                reason
            )
            values ($1, $2, $3, $4, $5, $6)
            """,
            assignment.source_id,
            assignment.account_key,
            assignment.assigned_at,
            assignment.active,
            assignment.released_at,
            assignment.reason,
        )

    async def release_assignment(
        self,
        *,
        source_id: str,
        reason: AssignmentReason,
        released_at: datetime,
    ) -> ChannelAssignment | None:
        record = await self._pool.fetchrow(
            """
            update channel_assignments
            set active = false,
                released_at = $2,
                reason = $3
            where id = (
                select id
                from channel_assignments
                where source_id = $1 and active = true
                order by assigned_at desc
                limit 1
            )
            returning source_id, account_key, assigned_at, active, released_at, reason
            """,
            source_id,
            released_at,
            reason,
        )
        if record is None:
            return None
        return _assignment_from_record(record)

    async def append_audit_event(self, event: AssignmentAuditEvent) -> None:
        await self._pool.execute(
            """
            insert into channel_assignment_audit (
                source_id,
                previous_account_key,
                new_account_key,
                reason,
                occurred_at
            )
            values ($1, $2, $3, $4, $5)
            """,
            event.source_id,
            event.previous_account_key,
            event.new_account_key,
            event.reason,
            event.occurred_at,
        )


def _account_from_record(record: Any) -> ListenerAccountMetadata:
    return ListenerAccountMetadata(
        account_key=str(record["account_key"]),
        enabled=bool(record["enabled"]),
        health_status=_health_status(str(record["health_status"])),
        last_successful_event_at=record["last_successful_event_at"],
        flood_wait_count=int(record["flood_wait_count"]),
    )


def _source_from_record(record: Any) -> SourceConfig:
    return SourceConfig(
        source_id=str(record["source_id"]),
        name=str(record["name"]),
        source_type=_source_type(str(record["source_type"])),
        identifier=str(record["identifier"]),
        enabled=bool(record["enabled"]),
        adapter_name=record["adapter_name"],
        source_profile=record["source_profile"],
        listener_account_key=record["listener_account_key"],
        parser_key=_record_value(record, "parser_key"),
        parser_version=_record_value(record, "parser_version"),
        parser_config=_record_json_object(record, "parser_config"),
        parser_mode=cast(ParserMode, _record_value(record, "parser_mode") or "active"),
    )


def _record_value(record: Any, key: str) -> Any:
    try:
        return record[key]
    except (KeyError, TypeError):
        return None


def _record_json_object(record: Any, key: str) -> dict[str, Any]:
    value = _record_value(record, key)
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _assignment_from_record(record: Any) -> ChannelAssignment:
    return ChannelAssignment(
        source_id=str(record["source_id"]),
        account_key=str(record["account_key"]),
        assigned_at=record["assigned_at"],
        active=bool(record["active"]),
        released_at=record["released_at"],
        reason=_assignment_reason(str(record["reason"])),
    )


def _health_status(value: str) -> ListenerHealthStatus:
    if value not in {"online", "reconnecting", "flood_wait", "disabled", "banned"}:
        raise ValueError(f"Unknown listener health status: {value}")
    return cast(ListenerHealthStatus, value)


def _source_type(value: str) -> SourceType:
    if value not in {"telegram_channel", "telegram_group", "website"}:
        raise ValueError(f"Unknown source type: {value}")
    return cast(SourceType, value)


def _assignment_reason(value: str) -> AssignmentReason:
    if value not in {"initial", "rebalance", "failover", "source_disabled"}:
        raise ValueError(f"Unknown assignment reason: {value}")
    return cast(AssignmentReason, value)
