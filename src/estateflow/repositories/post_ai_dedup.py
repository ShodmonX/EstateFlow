from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import asyncpg  # type: ignore[import-untyped]

from estateflow.services.extraction import CanonicalListing
from estateflow.services.media_storage import StoredMedia
from estateflow.services.post_ai_dedup import (
    AnnouncementStatus,
    DedupCandidateRepository,
    DedupDecision,
    DedupScoringConfig,
    ManualReviewItem,
    ReviewReason,
    ReviewStatus,
    ScoreSignal,
    StructuredAnnouncement,
    merge_parent_fields,
)

DEDUP_CANDIDATE_QUERY = """
with incoming_media(phash) as (
    select unnest($1::text[])
),
candidates as (
    select distinct a.*
    from announcements a
    left join announcement_media am on am.announcement_id = a.announcement_id
    left join incoming_media im on im.phash = am.phash
    where a.parent_announcement_id is null
      and a.status = 'active'
      and a.announcement_id <> $2::uuid
      and a.occurred_at >= $3::timestamptz
      and (
          ($4::text is not null and a.source_url = $4)
          or ($5::text is not null and a.forward_origin_key = $5)
          or ($6::text is not null and a.district = $6)
          or ($7::integer is not null and a.rooms = $7)
          or (
              $8::numeric is not null
              and a.area_sqm between $8::numeric - $9::numeric and $8::numeric + $9::numeric
          )
          or (
              $10::numeric is not null
              and a.price_period = $11
              and a.price_basis = $12
              and a.currency = $13
              and a.price_normalized_monthly between
                  $10::numeric * (1 - $14::numeric)
                  and $10::numeric * (1 + $14::numeric)
          )
          or im.phash is not null
      )
    order by a.occurred_at desc, a.created_at desc
    limit $15
)
select
    c.*,
    coalesce(
        jsonb_agg(
            jsonb_build_object(
                'media_id', am.media_id,
                'storage_url', am.storage_url,
                'object_key', am.object_key,
                'mime_type', am.mime_type,
                'size_bytes', am.size_bytes,
                'phash', am.phash,
                'content_sha256', am.content_sha256,
                'width', am.width,
                'height', am.height,
                'metadata', am.metadata
            )
        ) filter (where am.media_id is not null),
        '[]'::jsonb
    ) as media_items
from candidates c
left join announcement_media am on am.announcement_id = c.announcement_id
group by c.announcement_id, c.idempotency_key, c.source_id, c.source_channel_id,
    c.source_message_id, c.source_message_ids, c.parent_announcement_id, c.status,
    c.canonical_rank, c.source_count, c.latest_source_id, c.source_url,
    c.forward_origin_key, c.price, c.currency, c.price_period, c.price_basis,
    c.price_normalized_monthly, c.rooms, c.area_sqm, c.floor, c.total_floors,
    c.district, c.address, c.phone_numbers, c.owner_type, c.renovation_level,
    c.renovation_source, c.furniture, c.description, c.audience_tags,
    c.audience_excluded_tags, c.confidence, c.field_confidence, c.assumptions,
    c.prompt_version, c.is_promoted, c.promoted_until, c.occurred_at, c.created_at,
    c.updated_at, c.deleted_at
order by c.occurred_at desc, c.created_at desc
"""

USER_FACING_CANONICAL_SEARCH_QUERY = """
select *
from announcements
where parent_announcement_id is null
  and status = 'active'
order by created_at desc
limit $1 offset $2
"""

ADMIN_AUDIT_ANNOUNCEMENTS_QUERY = """
select *
from announcements
where ($1::uuid is null or announcement_id = $1 or parent_announcement_id = $1)
order by coalesce(parent_announcement_id, announcement_id), created_at
limit $2 offset $3
"""

PARENT_CHILD_LINK_LOCKING_QUERY = """
select announcement_id
from announcements
where announcement_id = any($1::uuid[])
for update
"""


class AsyncpgDedupCandidateRepository(DedupCandidateRepository):
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        config: DedupScoringConfig | None = None,
        lookback: timedelta = timedelta(days=90),
    ) -> None:
        self._pool = pool
        self._config = config or DedupScoringConfig()
        self._lookback = lookback

    async def list_candidates(
        self,
        announcement: StructuredAnnouncement,
        *,
        limit: int = 50,
    ) -> list[StructuredAnnouncement]:
        canonical = announcement.canonical
        records = await self._pool.fetch(
            DEDUP_CANDIDATE_QUERY,
            [item.phash for item in announcement.media],
            announcement.announcement_id,
            announcement.occurred_at - self._lookback,
            announcement.source_url,
            announcement.forward_origin_key,
            canonical.district,
            canonical.rooms,
            canonical.area_sqm,
            self._config.area_tolerance_sqm,
            _comparable_price(canonical),
            canonical.price_period,
            canonical.price_basis,
            canonical.currency,
            self._config.price_tolerance_ratio,
            min(limit, self._config.candidate_limit),
        )
        return [_announcement_from_record(record) for record in records]


class AsyncpgDedupDecisionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_decision(
        self,
        *,
        announcement_id: str,
        decision: DedupDecision,
    ) -> None:
        await self._pool.execute(
            """
            insert into dedup_decisions(
                announcement_id,
                candidate_announcement_id,
                decision,
                score,
                threshold,
                config_version,
                score_breakdown
            )
            values ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            announcement_id,
            decision.candidate_id,
            decision.decision,
            decision.score,
            decision.threshold,
            decision.config_version,
            _breakdown_json(decision),
        )


class AsyncpgParentChildAnnouncementRepository(AsyncpgDedupCandidateRepository):
    async def activate_announcement(self, announcement_id: str) -> StructuredAnnouncement:
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(
                """
                update announcements
                set status = 'active', updated_at = now()
                where announcement_id = $1::uuid
                  and status = 'manual_review'
                returning *
                """,
                announcement_id,
            )
        if record is None:
            existing = await self.get(announcement_id)
            if existing is None:
                raise ValueError(f"Announcement not found: {announcement_id}")
            return existing
        return _announcement_from_record_with_media(record, ())

    async def enqueue_manual_review_item(
        self,
        *,
        announcement: StructuredAnnouncement,
        reason: ReviewReason,
        decision: DedupDecision,
    ) -> ManualReviewItem:
        existing = await self._pool.fetchrow(
            """
            select *
            from manual_review_items
            where announcement_id = $1::uuid and reason = $2
            order by created_at
            limit 1
            """,
            announcement.announcement_id,
            reason,
        )
        if existing is not None:
            return _manual_review_from_record(existing)
        review_id = str(
            uuid5(NAMESPACE_URL, f"manual-review:{reason}:{announcement.idempotency_key}")
        )
        record = await self._pool.fetchrow(
            """
            insert into manual_review_items(
                review_id, announcement_id, reason, related_candidate_id, score_breakdown
            )
            values ($1::uuid, $2::uuid, $3, $4::uuid, $5::jsonb)
            on conflict (review_id) do update set review_id = excluded.review_id
            returning *
            """,
            review_id,
            announcement.announcement_id,
            reason,
            decision.candidate_id,
            _breakdown_json(decision),
        )
        return _manual_review_from_record(record)

    async def list_manual_review_items(
        self,
        *,
        status: ReviewStatus | None = None,
        reason: ReviewReason | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ManualReviewItem, ...]:
        records = await self._pool.fetch(
            """
            select *
            from manual_review_items
            where ($1::text is null or status = $1)
              and ($2::text is null or reason = $2)
            order by created_at, review_id
            limit $3 offset $4
            """,
            status,
            reason,
            limit,
            offset,
        )
        return tuple(_manual_review_from_record(record) for record in records)

    async def count_manual_review_items(
        self,
        *,
        status: ReviewStatus | None = None,
        reason: ReviewReason | None = None,
    ) -> int:
        return int(
            await self._pool.fetchval(
                """
                select count(*)
                from manual_review_items
                where ($1::text is null or status = $1)
                  and ($2::text is null or reason = $2)
                """,
                status,
                reason,
            )
        )

    async def get_manual_review_item(self, review_id: str) -> ManualReviewItem | None:
        record = await self._pool.fetchrow(
            "select * from manual_review_items where review_id = $1::uuid",
            review_id,
        )
        return None if record is None else _manual_review_from_record(record)

    async def update_manual_review_item(self, item: ManualReviewItem) -> None:
        await self._pool.execute(
            """
            update manual_review_items
            set status = $2, decided_at = $3
            where review_id = $1::uuid
            """,
            item.review_id,
            item.status,
            item.decided_at,
        )

    async def save_parent(self, announcement: StructuredAnnouncement) -> StructuredAnnouncement:
        async with self._pool.acquire() as conn:
            await _ensure_source_exists(conn, announcement.source_id)
            await _ensure_source_exists(
                conn, announcement.latest_source_id or announcement.source_id
            )
            record = await conn.fetchrow(
                """
                insert into announcements(
                    announcement_id, idempotency_key, source_id, source_channel_id,
                    source_message_id, source_message_ids, parent_announcement_id,
                    status, source_count, latest_source_id, source_url,
                    forward_origin_key, price, currency, price_period, price_basis,
                    price_normalized_monthly, rooms, area_sqm, floor, total_floors,
                    district, address, phone_numbers, owner_type, renovation_level,
                    renovation_source, furniture, description, audience_tags,
                    audience_excluded_tags, confidence, field_confidence, assumptions,
                    prompt_version, occurred_at, created_at, updated_at
                )
                values (
                    $1::uuid, $2, $3, $4, $5, array[$5]::text[], null, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
                    $21, $22::text[], $23, $24, $25, $26, $27, $28::text[],
                    $29::text[], $30, $31::jsonb, $32::text[], $33, $34, $35, $35
                )
                on conflict (source_id, source_channel_id, source_message_id)
                    where status <> 'deleted'
                do update set
                    updated_at = announcements.updated_at
                returning *
                """,
                announcement.announcement_id,
                announcement.idempotency_key,
                announcement.source_id,
                announcement.source_channel_id,
                announcement.source_message_id,
                announcement.status,
                announcement.source_count,
                announcement.latest_source_id or announcement.source_id,
                announcement.source_url,
                announcement.forward_origin_key,
                announcement.canonical.price,
                announcement.canonical.currency,
                announcement.canonical.price_period,
                announcement.canonical.price_basis,
                announcement.canonical.price_normalized_monthly,
                announcement.canonical.rooms,
                announcement.canonical.area_sqm,
                announcement.canonical.floor,
                announcement.canonical.total_floors,
                announcement.canonical.district,
                announcement.canonical.address,
                announcement.canonical.phone_numbers,
                announcement.canonical.owner_type,
                announcement.canonical.renovation_level,
                announcement.canonical.renovation_source,
                announcement.canonical.furniture,
                announcement.canonical.description,
                announcement.canonical.audience_tags,
                announcement.canonical.audience_excluded_tags,
                announcement.canonical.confidence,
                _json_dump(announcement.canonical.field_confidence),
                announcement.canonical.assumptions,
                announcement.canonical.prompt_version,
                announcement.occurred_at,
                announcement.created_at,
            )
            assert record is not None
            persisted_announcement_id = str(record["announcement_id"])
            await _save_announcement_media(conn, persisted_announcement_id, announcement.media)
            return _announcement_from_record_with_media(record, announcement.media)

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None:
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(
                """
                select
                    a.*,
                    coalesce(
                        jsonb_agg(
                            jsonb_build_object(
                                'media_id', am.media_id,
                                'storage_url', am.storage_url,
                                'object_key', am.object_key,
                                'mime_type', am.mime_type,
                                'size_bytes', am.size_bytes,
                                'phash', am.phash,
                                'content_sha256', am.content_sha256,
                                'width', am.width,
                                'height', am.height,
                                'metadata', am.metadata
                            )
                        ) filter (where am.media_id is not null),
                        '[]'::jsonb
                    ) as media_items
                from announcements a
                left join announcement_media am on am.announcement_id = a.announcement_id
                where a.announcement_id = $1::uuid
                group by a.announcement_id
                """,
                announcement_id,
            )
        return None if record is None else _announcement_from_record(record)

    async def record_decision(
        self,
        *,
        announcement_id: str,
        decision: DedupDecision,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                insert into dedup_decisions(
                    announcement_id,
                    candidate_announcement_id,
                    decision,
                    score,
                    threshold,
                    config_version,
                    score_breakdown
                )
                values ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb)
                """,
                announcement_id,
                decision.candidate_id,
                decision.decision,
                decision.score,
                decision.threshold,
                decision.config_version,
                _breakdown_json(decision),
            )

    async def update_canonical(
        self,
        *,
        announcement_id: str,
        canonical: CanonicalListing,
        reason: str,
    ) -> StructuredAnnouncement:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                record = await conn.fetchrow(
                    """
                    update announcements
                    set price = $2,
                        currency = $3,
                        price_period = $4,
                        price_basis = $5,
                        price_normalized_monthly = $6,
                        rooms = $7,
                        area_sqm = $8,
                        floor = $9,
                        total_floors = $10,
                        district = $11,
                        address = $12,
                        phone_numbers = $13::text[],
                        owner_type = $14,
                        renovation_level = $15,
                        renovation_source = $16,
                        furniture = $17,
                        description = $18,
                        audience_tags = $19::text[],
                        audience_excluded_tags = $20::text[],
                        confidence = $21,
                        field_confidence = $22::jsonb,
                        assumptions = $23::text[],
                        prompt_version = $24,
                        updated_at = now()
                    where announcement_id = $1::uuid
                    returning *
                    """,
                    announcement_id,
                    canonical.price,
                    canonical.currency,
                    canonical.price_period,
                    canonical.price_basis,
                    canonical.price_normalized_monthly,
                    canonical.rooms,
                    canonical.area_sqm,
                    canonical.floor,
                    canonical.total_floors,
                    canonical.district,
                    canonical.address,
                    canonical.phone_numbers,
                    canonical.owner_type,
                    canonical.renovation_level,
                    canonical.renovation_source,
                    canonical.furniture,
                    canonical.description,
                    canonical.audience_tags,
                    canonical.audience_excluded_tags,
                    canonical.confidence,
                    _json_dump(canonical.field_confidence),
                    canonical.assumptions,
                    canonical.prompt_version,
                )
                if record is None:
                    raise ValueError(f"Announcement not found: {announcement_id}")
                await conn.execute(
                    """
                    insert into announcement_merge_audit(
                        parent_announcement_id,
                        child_announcement_id,
                        merge_policy,
                        conflicts
                    )
                    values ($1::uuid, $1::uuid, 'manual_review', $2::jsonb)
                    on conflict (child_announcement_id) do nothing
                    """,
                    announcement_id,
                    _json_dump([{"reason": reason}]),
                )
        return _announcement_from_record_with_media(record, ())

    async def link_child(
        self,
        *,
        child: StructuredAnnouncement,
        parent_id: str,
        decision: DedupDecision,
        merge_policy: str,
    ) -> StructuredAnnouncement:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetch(
                    PARENT_CHILD_LINK_LOCKING_QUERY,
                    [parent_id, child.announcement_id],
                )
                parent = await self._get_for_update(conn, parent_id)
                if parent is None:
                    raise ValueError(f"Parent announcement not found: {parent_id}")
                existing_child = await self._get_for_update(conn, child.announcement_id)
                if existing_child is not None and existing_child.parent_id == parent_id:
                    return existing_child

                parent_update, conflicts = merge_parent_fields(parent, child)
                linked_child = await self._upsert_child(conn, child=child, parent_id=parent_id)
                await self._update_parent_aggregate(
                    conn,
                    parent=parent_update,
                    latest_source_id=child.source_id,
                )
                await conn.execute(
                    """
                    insert into announcement_merge_audit(
                        parent_announcement_id,
                        child_announcement_id,
                        merge_policy,
                        conflicts
                    )
                    values ($1::uuid, $2::uuid, $3, $4::jsonb)
                    on conflict (child_announcement_id) do nothing
                    """,
                    parent_id,
                    child.announcement_id,
                    merge_policy,
                    _json_dump(conflicts),
                )
                return linked_child

    async def list_user_facing(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[StructuredAnnouncement]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(USER_FACING_CANONICAL_SEARCH_QUERY, limit, offset)
        return [_announcement_from_record_with_media(record, ()) for record in records]

    async def list_for_audit(
        self,
        *,
        parent_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[StructuredAnnouncement]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(ADMIN_AUDIT_ANNOUNCEMENTS_QUERY, parent_id, limit, offset)
        return [_announcement_from_record_with_media(record, ()) for record in records]

    async def _get_for_update(
        self,
        conn: asyncpg.Connection,
        announcement_id: str,
    ) -> StructuredAnnouncement | None:
        record = await conn.fetchrow(
            """
            select *
            from announcements
            where announcement_id = $1::uuid
            for update
            """,
            announcement_id,
        )
        return None if record is None else _announcement_from_record_with_media(record, ())

    async def _upsert_child(
        self,
        conn: asyncpg.Connection,
        *,
        child: StructuredAnnouncement,
        parent_id: str,
    ) -> StructuredAnnouncement:
        await _ensure_source_exists(conn, child.source_id)
        await _ensure_source_exists(conn, child.latest_source_id or child.source_id)
        record = await conn.fetchrow(
            """
            insert into announcements(
                announcement_id, idempotency_key, source_id, source_channel_id,
                source_message_id, source_message_ids, parent_announcement_id,
                status, source_count, latest_source_id, source_url, forward_origin_key,
                price, currency, price_period, price_basis, price_normalized_monthly,
                rooms, area_sqm, floor, total_floors, district, address, phone_numbers,
                owner_type, renovation_level, renovation_source, furniture, description,
                audience_tags, audience_excluded_tags, confidence, field_confidence,
                assumptions, prompt_version, occurred_at, created_at, updated_at
            )
            values (
                $1::uuid, $2, $3, $4, $5, array[$5]::text[], $6::uuid, 'active',
                1, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18,
                $19, $20, $21::text[], $22, $23, $24, $25, $26, $27::text[],
                $28::text[], $29, $30::jsonb, $31::text[], $32, $33, $34, $34
            )
            on conflict (announcement_id) do update set
                parent_announcement_id = excluded.parent_announcement_id,
                status = 'active',
                updated_at = excluded.updated_at
            returning *
            """,
            child.announcement_id,
            child.idempotency_key,
            child.source_id,
            child.source_channel_id,
            child.source_message_id,
            parent_id,
            child.source_id,
            child.source_url,
            child.forward_origin_key,
            child.canonical.price,
            child.canonical.currency,
            child.canonical.price_period,
            child.canonical.price_basis,
            child.canonical.price_normalized_monthly,
            child.canonical.rooms,
            child.canonical.area_sqm,
            child.canonical.floor,
            child.canonical.total_floors,
            child.canonical.district,
            child.canonical.address,
            child.canonical.phone_numbers,
            child.canonical.owner_type,
            child.canonical.renovation_level,
            child.canonical.renovation_source,
            child.canonical.furniture,
            child.canonical.description,
            child.canonical.audience_tags,
            child.canonical.audience_excluded_tags,
            child.canonical.confidence,
            _json_dump(child.canonical.field_confidence),
            child.canonical.assumptions,
            child.canonical.prompt_version,
            child.occurred_at,
            child.created_at,
        )
        assert record is not None
        await _save_announcement_media(conn, child.announcement_id, child.media)
        return _announcement_from_record_with_media(record, child.media)

    async def _update_parent_aggregate(
        self,
        conn: asyncpg.Connection,
        *,
        parent: StructuredAnnouncement,
        latest_source_id: str,
    ) -> None:
        await _ensure_source_exists(conn, latest_source_id)
        canonical = parent.canonical

        await conn.execute(
            """
            update announcements
            set source_count = (
                    select greatest(1, count(*))
                    from announcements
                    where parent_announcement_id = $1::uuid or announcement_id = $1::uuid
                ),
                latest_source_id = $2,
                price = $3,
                currency = $4,
                price_period = $5,
                price_basis = $6,
                price_normalized_monthly = $7,
                rooms = $8,
                area_sqm = $9,
                floor = $10,
                total_floors = $11,
                district = $12,
                address = $13,
                phone_numbers = $14::text[],
                owner_type = $15,
                renovation_level = $16,
                renovation_source = $17,
                furniture = $18,
                description = $19,
                audience_tags = $20::text[],
                audience_excluded_tags = $21::text[],
                confidence = $22,
                field_confidence = $23::jsonb,
                assumptions = $24::text[],
                prompt_version = $25,
                updated_at = now()
            where announcement_id = $1::uuid
            """,
            parent.announcement_id,
            latest_source_id,
            canonical.price,
            canonical.currency,
            canonical.price_period,
            canonical.price_basis,
            canonical.price_normalized_monthly,
            canonical.rooms,
            canonical.area_sqm,
            canonical.floor,
            canonical.total_floors,
            canonical.district,
            canonical.address,
            canonical.phone_numbers,
            canonical.owner_type,
            canonical.renovation_level,
            canonical.renovation_source,
            canonical.furniture,
            canonical.description,
            canonical.audience_tags,
            canonical.audience_excluded_tags,
            canonical.confidence,
            _json_dump(canonical.field_confidence),
            canonical.assumptions,
            canonical.prompt_version,
        )


def _announcement_from_record(record: Any) -> StructuredAnnouncement:
    media_raw = record["media_items"] if "media_items" in record else []
    return _announcement_from_record_with_media(record, media_raw)


def _announcement_from_record_with_media(
    record: Any,
    media_items: Any = None,
) -> StructuredAnnouncement:
    items = _parse_json_list(
        media_items
        if media_items is not None
        else (record["media_items"] if "media_items" in record else [])
    )
    return StructuredAnnouncement(
        announcement_id=str(record["announcement_id"]),
        idempotency_key=str(record["idempotency_key"]),
        source_id=str(record["source_id"]),
        source_channel_id=str(record["source_channel_id"]),
        source_message_id=str(record["source_message_id"]),
        occurred_at=record["occurred_at"],
        canonical=_canonical_from_record(record),
        media=tuple(
            item if isinstance(item, StoredMedia) else _media_from_payload(item)
            for item in items
            if isinstance(item, (dict, StoredMedia))
        ),
        source_url=record["source_url"],
        forward_origin_key=record["forward_origin_key"],
        parent_id=str(record["parent_announcement_id"])
        if record["parent_announcement_id"] is not None
        else None,
        status=cast(AnnouncementStatus, record["status"]),
        source_count=int(record["source_count"]),
        latest_source_id=record["latest_source_id"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


def _canonical_from_record(record: Any) -> CanonicalListing:
    return CanonicalListing(
        prompt_version=record["prompt_version"] or "estateflow.listing.v1",
        price=record["price"],
        currency=record["currency"],
        price_period=record["price_period"],
        price_basis=record["price_basis"],
        price_normalized_monthly=record["price_normalized_monthly"],
        listing_type=None,
        rooms=record["rooms"],
        area_sqm=record["area_sqm"],
        floor=record["floor"],
        total_floors=record["total_floors"],
        district=record["district"],
        address=record["address"],
        phone_numbers=list(record["phone_numbers"] or []),
        owner_type=record["owner_type"],
        renovation_level=record["renovation_level"],
        renovation_source=record["renovation_source"],
        furniture=record["furniture"],
        description=record["description"] or "",
        audience_tags=list(record["audience_tags"] or []),
        audience_excluded_tags=list(record["audience_excluded_tags"] or []),
        confidence=float(record["confidence"] or 0),
        field_confidence=_parse_json_dict(record["field_confidence"]),
        assumptions=list(record["assumptions"] or []),
    )


def _media_from_payload(item: dict[str, Any]) -> StoredMedia:
    return StoredMedia(
        media_id=str(item["media_id"]),
        storage_url=str(item["storage_url"]),
        object_key=str(item["object_key"]),
        mime_type=str(item["mime_type"]),
        size_bytes=int(item["size_bytes"]),
        phash=str(item["phash"]),
        content_sha256=str(item["content_sha256"]),
        width=item["width"],
        height=item["height"],
        metadata=_parse_json_dict(item.get("metadata")),
    )


def _comparable_price(canonical: CanonicalListing) -> Decimal | None:
    if canonical.price_basis != "total" or canonical.price_period == "one_time":
        return None
    return canonical.price_normalized_monthly


def _breakdown_json(decision: DedupDecision) -> str:
    return json.dumps(
        [
            {
                "name": signal.name,
                "weight": signal.weight,
                "matched": signal.matched,
                "reason": signal.reason,
                "details": signal.details,
                "independent": signal.independent,
                "exact": signal.exact,
            }
            for signal in decision.breakdown
        ],
        sort_keys=True,
    )


def _json_dump(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _parse_json_dict(val: Any) -> dict[str, Any]:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            res = json.loads(val)
            if isinstance(res, dict):
                return res
        except Exception:
            pass
    return {}


def _parse_json_list(val: Any) -> list[Any]:
    if isinstance(val, (list, tuple)):
        return list(val)
    if isinstance(val, str):
        try:
            res = json.loads(val)
            if isinstance(res, list):
                return res
        except Exception:
            pass
    return []


def _manual_review_from_record(record: Any) -> ManualReviewItem:
    signals = tuple(
        ScoreSignal(
            name=str(item.get("name", "unknown")),
            weight=int(item.get("weight", 0)),
            matched=bool(item.get("matched", False)),
            reason=str(item.get("reason", "")),
            details=_parse_json_dict(item.get("details")),
            independent=bool(item.get("independent", True)),
            exact=bool(item.get("exact", False)),
        )
        for item in _parse_json_list(record["score_breakdown"])
        if isinstance(item, dict)
    )
    return ManualReviewItem(
        review_id=str(record["review_id"]),
        announcement_id=str(record["announcement_id"]),
        reason=cast(ReviewReason, record["reason"]),
        score_breakdown=signals,
        related_candidate_id=(
            None
            if record["related_candidate_id"] is None
            else str(record["related_candidate_id"])
        ),
        status=cast(ReviewStatus, record["status"]),
        created_at=record["created_at"],
        decided_at=record["decided_at"],
    )


async def _ensure_source_exists(conn: asyncpg.Connection, source_id: str | None) -> None:
    if not source_id:
        return
    await conn.execute(
        """
        insert into ingestion_sources(source_id, name, source_type, identifier, enabled, status)
        values ($1, $1, 'telegram_channel', $1, true, 'active')
        on conflict (source_id) do nothing
        """,
        source_id,
    )


async def _save_announcement_media(
    conn: asyncpg.Connection,
    announcement_id: str,
    media: tuple[StoredMedia, ...] | list[StoredMedia],
) -> None:
    for m in media:
        await conn.execute(
            """
            insert into announcement_media(
                media_id, announcement_id, storage_url, object_key, mime_type,
                size_bytes, phash, content_sha256, width, height, metadata
            )
            values ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            on conflict (announcement_id, media_id) do nothing
            """,
            m.media_id,
            announcement_id,
            m.storage_url,
            m.object_key,
            m.mime_type,
            m.size_bytes,
            m.phash,
            m.content_sha256,
            m.width,
            m.height,
            _json_dump(m.metadata),
        )


class LazyAsyncpgParentChildAnnouncementRepository(AsyncpgDedupCandidateRepository):
    def __init__(self, postgres_url: str, config: DedupScoringConfig | None = None) -> None:
        import asyncio

        self._url = postgres_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        self._config = config or DedupScoringConfig()
        self._pool: asyncpg.Pool | None = None
        self._inner: AsyncpgParentChildAnnouncementRepository | None = None
        self._lock = asyncio.Lock()

    async def _get_inner(self) -> AsyncpgParentChildAnnouncementRepository:
        if self._inner is not None:
            return self._inner
        async with self._lock:
            if self._inner is None:
                self._pool = await asyncpg.create_pool(self._url)
                self._inner = AsyncpgParentChildAnnouncementRepository(
                    self._pool, config=self._config
                )
            return self._inner

    async def save_parent(self, announcement: StructuredAnnouncement) -> StructuredAnnouncement:
        inner = await self._get_inner()
        return await inner.save_parent(announcement)

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None:
        inner = await self._get_inner()
        return await inner.get(announcement_id)

    async def link_child(
        self,
        *,
        child: StructuredAnnouncement,
        parent_id: str,
        decision: DedupDecision,
        merge_policy: str,
    ) -> StructuredAnnouncement:
        inner = await self._get_inner()
        return await inner.link_child(
            child=child, parent_id=parent_id, decision=decision, merge_policy=merge_policy
        )

    async def record_decision(
        self,
        *,
        announcement_id: str,
        decision: DedupDecision,
    ) -> None:
        inner = await self._get_inner()
        await inner.record_decision(announcement_id=announcement_id, decision=decision)

    async def update_canonical(
        self,
        *,
        announcement_id: str,
        canonical: CanonicalListing,
        reason: str,
    ) -> StructuredAnnouncement:
        inner = await self._get_inner()
        return await inner.update_canonical(
            announcement_id=announcement_id, canonical=canonical, reason=reason
        )

    async def activate_announcement(self, announcement_id: str) -> StructuredAnnouncement:
        inner = await self._get_inner()
        return await inner.activate_announcement(announcement_id)

    async def enqueue_manual_review_item(
        self,
        *,
        announcement: StructuredAnnouncement,
        reason: ReviewReason,
        decision: DedupDecision,
    ) -> ManualReviewItem:
        inner = await self._get_inner()
        return await inner.enqueue_manual_review_item(
            announcement=announcement,
            reason=reason,
            decision=decision,
        )

    async def list_manual_review_items(
        self,
        *,
        status: ReviewStatus | None = None,
        reason: ReviewReason | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ManualReviewItem, ...]:
        inner = await self._get_inner()
        return await inner.list_manual_review_items(
            status=status, reason=reason, limit=limit, offset=offset
        )

    async def count_manual_review_items(
        self,
        *,
        status: ReviewStatus | None = None,
        reason: ReviewReason | None = None,
    ) -> int:
        inner = await self._get_inner()
        return await inner.count_manual_review_items(status=status, reason=reason)

    async def get_manual_review_item(self, review_id: str) -> ManualReviewItem | None:
        inner = await self._get_inner()
        return await inner.get_manual_review_item(review_id)

    async def update_manual_review_item(self, item: ManualReviewItem) -> None:
        inner = await self._get_inner()
        await inner.update_manual_review_item(item)

    async def list_candidates(
        self,
        announcement: StructuredAnnouncement,
        *,
        limit: int = 50,
    ) -> list[StructuredAnnouncement]:
        inner = await self._get_inner()
        return await inner.list_candidates(announcement, limit=limit)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._inner = None
