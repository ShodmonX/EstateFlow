from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from estateflow.db.session import DatabaseSessionManager
from estateflow.models.listeners import IngestionSourceRecord
from estateflow.services.listener_pool import TELEGRAM_SOURCE_TYPES
from estateflow.services.telegram_auth import (
    TelegramAuthService,
    TelegramSessionStatus,
)


@dataclass(frozen=True)
class TelegramSessionSourceBinding:
    source_id: str
    source_type: str
    identifier: str
    name: str
    source_profile: str | None
    enabled: bool
    session_name: str


@dataclass(frozen=True)
class TelegramSessionInspection:
    session_name: str
    session_path: str
    session_file_path: str
    file_exists: bool
    file_size_bytes: int | None
    authorized: bool | None
    auth_state_present: bool
    requires_password: bool
    updated_at: str | None
    status: str
    error: str | None
    bound_sources: tuple[TelegramSessionSourceBinding, ...]


class TelegramSessionInventoryService:
    def __init__(self, db: DatabaseSessionManager, auth_service: TelegramAuthService) -> None:
        self._db = db
        self._auth_service = auth_service

    async def list_sessions(self) -> list[TelegramSessionInspection]:
        bindings = await self._list_bindings()
        session_names = set(await self._auth_service.list_session_names())
        session_names.update(bindings)
        results: list[TelegramSessionInspection] = []
        for session_name in sorted(session_names):
            status = await self._auth_service.inspect_session(session_name)
            results.append(
                self._inspect_status(
                    status,
                    bindings.get(session_name, ()),
                )
            )
        return results

    async def _list_bindings(self) -> dict[str, tuple[TelegramSessionSourceBinding, ...]]:
        async with self._db.session() as session:
            rows = (
                await session.scalars(
                    select(IngestionSourceRecord)
                    .where(IngestionSourceRecord.deleted_at.is_(None))
                    .where(IngestionSourceRecord.enabled.is_(True))
                    .where(IngestionSourceRecord.source_type.in_(tuple(TELEGRAM_SOURCE_TYPES)))
                    .where(IngestionSourceRecord.listener_account_key.is_not(None))
                    .order_by(
                        IngestionSourceRecord.listener_account_key,
                        IngestionSourceRecord.source_id,
                    )
                )
            ).all()
        grouped: dict[str, list[TelegramSessionSourceBinding]] = defaultdict(list)
        for row in rows:
            session_name = row.listener_account_key or ""
            if not session_name:
                continue
            grouped[session_name].append(
                TelegramSessionSourceBinding(
                    source_id=row.source_id,
                    source_type=row.source_type,
                    identifier=row.identifier,
                    name=row.name,
                    source_profile=row.source_profile,
                    enabled=row.enabled,
                    session_name=session_name,
                )
            )
        return {key: tuple(value) for key, value in grouped.items()}

    @staticmethod
    def _inspect_status(
        status: TelegramSessionStatus,
        bound_sources: tuple[TelegramSessionSourceBinding, ...],
    ) -> TelegramSessionInspection:
        return TelegramSessionInspection(
            session_name=status.session_name,
            session_path=status.session_path,
            session_file_path=status.session_file_path,
            file_exists=status.file_exists,
            file_size_bytes=status.file_size_bytes,
            authorized=status.authorized,
            auth_state_present=status.auth_state_present,
            requires_password=status.requires_password,
            updated_at=status.updated_at.isoformat() if status.updated_at is not None else None,
            status=status.status,
            error=status.error,
            bound_sources=bound_sources,
        )
