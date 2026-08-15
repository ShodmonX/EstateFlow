from __future__ import annotations

from typing import Any

import asyncpg  # type: ignore[import-untyped]

from estateflow.services.audience_tags import (
    AudienceTagAuditEvent,
    AudienceTagReferenceRemapResult,
    AudienceTagStatus,
    AudienceTagType,
)


class AsyncpgAudienceTagRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_approved(self, *, limit: int | None = None) -> tuple[AudienceTagType, ...]:
        limit_sql = "" if limit is None else f"limit {int(limit)}"
        records = await self._pool.fetch(
            f"""
            select *
            from audience_tag_types
            where status = 'approved'
            order by usage_count desc, tag_key
            {limit_sql}
            """
        )
        return tuple(_from_record(record) for record in records)

    async def list_pending(self) -> tuple[AudienceTagType, ...]:
        records = await self._pool.fetch(
            """
            select *
            from audience_tag_types
            where status = 'pending'
            order by created_at, tag_key
            """
        )
        return tuple(_from_record(record) for record in records)

    async def get(self, *, tag_key: str) -> AudienceTagType | None:
        record = await self._pool.fetchrow(
            "select * from audience_tag_types where tag_key = $1",
            tag_key,
        )
        return None if record is None else _from_record(record)

    async def upsert_pending(
        self,
        *,
        tag_key: str,
        display_name_uz: str,
    ) -> tuple[AudienceTagType, bool]:
        record = await self._pool.fetchrow(
            """
            insert into audience_tag_types(tag_key, display_name_uz, status)
            values ($1, $2, 'pending')
            on conflict (tag_key) do nothing
            returning *, true as inserted
            """,
            tag_key,
            display_name_uz,
        )
        if record is not None:
            return _from_record(record), True
        existing = await self.get(tag_key=tag_key)
        assert existing is not None
        return existing, False

    async def increment_usage(self, *, tag_key: str) -> AudienceTagType:
        record = await self._pool.fetchrow(
            """
            update audience_tag_types
            set usage_count = usage_count + 1,
                updated_at = now()
            where tag_key = $1
            returning *
            """,
            tag_key,
        )
        assert record is not None
        return _from_record(record)

    async def set_status(
        self,
        *,
        tag_key: str,
        status: AudienceTagStatus,
        display_name_uz: str | None = None,
    ) -> AudienceTagType:
        record = await self._pool.fetchrow(
            """
            update audience_tag_types
            set status = $2,
                display_name_uz = coalesce($3, display_name_uz),
                updated_at = now()
            where tag_key = $1
            returning *
            """,
            tag_key,
            status,
            display_name_uz,
        )
        assert record is not None
        return _from_record(record)

    async def audit_once(
        self,
        event: AudienceTagAuditEvent,
    ) -> tuple[AudienceTagAuditEvent, bool]:
        record = await self._pool.fetchrow(
            """
            insert into admin_audit_events(
                admin_user_id,
                action,
                entity_type,
                entity_id,
                idempotency_key,
                note,
                after_state,
                created_at
            )
            values (
                $1, $2, 'audience_tag', $3, $4, $5,
                jsonb_build_object('target_tag_key', $6),
                $7
            )
            on conflict (idempotency_key) do nothing
            returning audit_id
            """,
            event.admin_user_id,
            event.action,
            event.tag_key,
            event.idempotency_key,
            event.note,
            event.target_tag_key,
            event.created_at,
        )
        return event, record is not None

    async def remap_references(
        self,
        *,
        source_tag_key: str,
        target_tag_key: str,
    ) -> AudienceTagReferenceRemapResult:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                announcement_count = await connection.fetchval(
                    """
                    with updated as (
                        update announcements
                        set audience_tags = array(
                                select distinct item
                                from unnest(array_replace(audience_tags, $1, $2)) as item
                            ),
                            audience_excluded_tags = array(
                                select distinct item
                                from unnest(
                                    array_replace(audience_excluded_tags, $1, $2)
                                ) as item
                            ),
                            updated_at = now()
                        where $1 = any(audience_tags)
                           or $1 = any(audience_excluded_tags)
                        returning announcement_id
                    )
                    select count(*) from updated
                    """,
                    source_tag_key,
                    target_tag_key,
                )
                filter_count = await connection.fetchval(
                    """
                    with updated as (
                        update user_filters
                        set filters = jsonb_set(
                                filters,
                                '{criteria,audience_tag}',
                                to_jsonb($2::text),
                                false
                            ),
                            updated_at = now()
                        where filters #>> '{criteria,audience_tag}' = $1
                        returning filter_id
                    )
                    select count(*) from updated
                    """,
                    source_tag_key,
                    target_tag_key,
                )
        return AudienceTagReferenceRemapResult(
            announcements_updated=int(announcement_count or 0),
            filters_updated=int(filter_count or 0),
        )


def _from_record(record: Any) -> AudienceTagType:
    return AudienceTagType(
        tag_key=str(record["tag_key"]),
        display_name_uz=str(record["display_name_uz"]),
        status=record["status"],
        usage_count=int(record["usage_count"]),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )
