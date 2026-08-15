from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

import asyncpg  # type: ignore[import-untyped]

from estateflow.services.source_suggestions import (
    SourceSuggestion,
    SourceSuggestionAuditEvent,
    SourceType,
    SuggestionStatus,
)


class AsyncpgSourceSuggestionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def submit(
        self,
        *,
        user_id: int,
        source_identifier: str,
        source_type: SourceType,
    ) -> tuple[SourceSuggestion, bool]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                existing = await connection.fetchrow(
                    """
                    select *
                    from source_suggestions
                    where source_identifier = $1
                    for update
                    """,
                    source_identifier,
                )
                if existing is not None:
                    if existing["status"] == "rejected":
                        reopened = await connection.fetchrow(
                            """
                            update source_suggestions
                            set user_id = $2,
                                source_type = $3,
                                status = 'pending',
                                admin_note = null,
                                updated_at = now(),
                                decided_at = null
                            where source_identifier = $1
                            returning *
                            """,
                            source_identifier,
                            user_id,
                            source_type,
                        )
                        assert reopened is not None
                        return _from_record(reopened), True
                    return _from_record(existing), False
                record = await connection.fetchrow(
                    """
                    insert into source_suggestions(user_id, source_identifier, source_type)
                    values ($1, $2, $3)
                    returning *
                    """,
                    user_id,
                    source_identifier,
                    source_type,
                )
                assert record is not None
                return _from_record(record), True

    async def list_pending(self) -> tuple[SourceSuggestion, ...]:
        records = await self._pool.fetch(
            """
            select *
            from source_suggestions
            where status = 'pending'
            order by created_at, source_identifier
            """
        )
        return tuple(_from_record(record) for record in records)

    async def get(self, *, suggestion_id: str) -> SourceSuggestion | None:
        record = await self._pool.fetchrow(
            """
            select *
            from source_suggestions
            where suggestion_id = $1::uuid
            """,
            suggestion_id,
        )
        return None if record is None else _from_record(record)

    async def set_status(
        self,
        *,
        suggestion_id: str,
        status: SuggestionStatus,
        admin_note: str | None,
        reward_granted: bool,
        source_id: str | None,
        decided_at: datetime,
    ) -> SourceSuggestion:
        record = await self._pool.fetchrow(
            """
            update source_suggestions
            set status = $2,
                admin_note = $3,
                reward_granted = $4,
                source_id = coalesce($5, source_id),
                updated_at = $6,
                decided_at = $6
            where suggestion_id = $1::uuid
            returning *
            """,
            suggestion_id,
            status,
            admin_note,
            reward_granted,
            source_id,
            decided_at,
        )
        assert record is not None
        return _from_record(record)

    async def audit_once(
        self,
        event: SourceSuggestionAuditEvent,
    ) -> tuple[SourceSuggestionAuditEvent, bool]:
        record = await self._pool.fetchrow(
            """
            insert into admin_audit_events(
                admin_user_id,
                action,
                entity_type,
                entity_id,
                idempotency_key,
                note,
                created_at
            )
            values ($1, $2, 'source_suggestion', $3, $4, $5, $6)
            on conflict (idempotency_key) do nothing
            returning audit_id
            """,
            event.actor_user_id or 0,
            event.action,
            event.suggestion_id,
            event.idempotency_key,
            event.note,
            event.created_at,
        )
        return event, record is not None

    async def grant_premium(
        self,
        *,
        user_id: int,
        extension: timedelta,
        now: datetime,
    ) -> datetime:
        record = await self._pool.fetchrow(
            """
            update users
            set premium_until = (
                    case
                        when premium_until is not null and premium_until > $2
                            then premium_until
                        else $2
                    end
                ) + $3::interval,
                updated_at = $2
            where user_id = $1
            returning premium_until
            """,
            user_id,
            now,
            extension,
        )
        assert record is not None
        return cast(datetime, record["premium_until"])


def _from_record(record: Any) -> SourceSuggestion:
    return SourceSuggestion(
        suggestion_id=str(record["suggestion_id"]),
        user_id=int(record["user_id"]) if record["user_id"] is not None else None,
        source_identifier=str(record["source_identifier"]),
        source_type=record["source_type"],
        status=record["status"],
        admin_note=record["admin_note"],
        reward_granted=bool(record["reward_granted"]),
        source_id=record["source_id"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        decided_at=record["decided_at"],
    )
