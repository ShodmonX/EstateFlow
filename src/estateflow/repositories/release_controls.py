from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from estateflow.models import FeatureFlagAuditRecord, FeatureFlagStateRecord
from estateflow.services.release_controls import (
    FEATURE_FLAG_NAMES,
    FeatureFlagAuditEvent,
    FeatureFlagName,
    FeatureFlagSet,
    FeatureFlagSnapshot,
    FeatureFlagState,
    FeatureFlagStore,
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class SQLAlchemyFeatureFlagStore(FeatureFlagStore):
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        initial: FeatureFlagSet | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._initial = initial or FeatureFlagSet()

    async def get_snapshot(self) -> FeatureFlagSnapshot:
        async with self._session_factory() as session:
            await self._seed_missing(session)
            rows = (
                await session.scalars(
                    select(FeatureFlagStateRecord).order_by(FeatureFlagStateRecord.flag_name)
                )
            ).all()
            return FeatureFlagSnapshot(
                states={
                    cast(FeatureFlagName, row.flag_name): _state_from_record(row) for row in rows
                }
            )

    async def set_flag(
        self,
        *,
        name: FeatureFlagName,
        enabled: bool,
        expected_version: int | None,
        actor: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> FeatureFlagAuditEvent:
        current_at = occurred_at or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                await self._seed_missing(session)
                row = await session.scalar(
                    select(FeatureFlagStateRecord)
                    .where(FeatureFlagStateRecord.flag_name == name)
                    .with_for_update()
                )
                if row is None:
                    raise KeyError(name)
                if expected_version is not None and row.version != expected_version:
                    raise ValueError("feature_flag_version_conflict")
                previous_enabled = bool(row.enabled)
                previous_version = int(row.version)
                new_version = previous_version + 1
                row.enabled = enabled
                row.version = new_version
                row.updated_at = current_at
                audit = FeatureFlagAuditRecord(
                    flag_name=name,
                    previous_enabled=previous_enabled,
                    new_enabled=enabled,
                    previous_version=previous_version,
                    new_version=new_version,
                    actor=actor,
                    reason=reason,
                    occurred_at=current_at,
                )
                session.add(audit)
            await session.refresh(audit)
            return _audit_from_record(audit)

    async def audit_log(self) -> tuple[FeatureFlagAuditEvent, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(FeatureFlagAuditRecord).order_by(
                        FeatureFlagAuditRecord.occurred_at.asc(),
                        FeatureFlagAuditRecord.audit_id.asc(),
                    )
                )
            ).all()
            return tuple(_audit_from_record(row) for row in rows)

    async def _seed_missing(self, session: AsyncSession) -> None:
        payload = [
            {
                "flag_name": name,
                "enabled": getattr(self._initial, name),
                "version": 1,
                "updated_at": datetime.now(UTC),
            }
            for name in FEATURE_FLAG_NAMES
        ]
        statement = (
            insert(FeatureFlagStateRecord)
            .values(payload)
            .on_conflict_do_nothing(index_elements=[FeatureFlagStateRecord.flag_name])
        )
        await session.execute(statement)


def _state_from_record(record: FeatureFlagStateRecord) -> FeatureFlagState:
    return FeatureFlagState(
        flag_name=cast(FeatureFlagName, record.flag_name),
        enabled=bool(record.enabled),
        version=int(record.version),
        updated_at=record.updated_at,
    )


def _audit_from_record(record: FeatureFlagAuditRecord) -> FeatureFlagAuditEvent:
    return FeatureFlagAuditEvent(
        flag_name=cast(FeatureFlagName, record.flag_name),
        previous_enabled=bool(record.previous_enabled),
        new_enabled=bool(record.new_enabled),
        previous_version=int(record.previous_version),
        new_version=int(record.new_version),
        actor=record.actor,
        reason=record.reason,
        occurred_at=record.occurred_at,
    )
