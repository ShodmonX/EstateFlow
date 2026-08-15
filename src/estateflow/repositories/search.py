from __future__ import annotations

from typing import Any

import asyncpg  # type: ignore[import-untyped]

from estateflow.repositories.post_ai_dedup import _announcement_from_record
from estateflow.services.post_ai_dedup import StructuredAnnouncement
from estateflow.services.search import SearchCriteria, SearchMetadata, SearchResult

SORT_SQL = {
    "newest": "a.created_at desc, a.announcement_id desc",
    "cheapest": (
        "a.price_normalized_monthly asc nulls last, a.created_at desc, a.announcement_id desc"
    ),
    "price_desc": (
        "a.price_normalized_monthly desc nulls last, a.created_at desc, a.announcement_id desc"
    ),
}


class AsyncpgSearchRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def search(self, criteria: SearchCriteria) -> SearchResult:
        where, params = _where(criteria)
        order_by = SORT_SQL[criteria.sort]
        limit_param = len(params) + 1
        offset_param = len(params) + 2
        query = f"""
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
            ) as media_items,
            count(*) over() as total_count
        from announcements a
        left join announcement_media am on am.announcement_id = a.announcement_id
        where {where}
        group by a.announcement_id
        order by {order_by}
        limit ${limit_param} offset ${offset_param}
        """
        records = await self._pool.fetch(query, *params, criteria.limit, criteria.offset)
        total = int(records[0]["total_count"]) if records else 0
        items = tuple(_announcement_from_record(record) for record in records)
        return SearchResult(
            items=items,
            metadata=SearchMetadata(
                returned=len(items),
                limit=criteria.limit,
                offset=criteria.offset,
                total=total,
                per_person_included=criteria.include_per_person,
                null_price_included=not criteria.has_price_filter,
                notes=_notes(criteria),
            ),
        )

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None:
        record = await self._pool.fetchrow(
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
            where a.announcement_id = $1
              and a.parent_announcement_id is null
              and a.status = 'active'
              and a.price_period in ('daily', 'monthly')
            group by a.announcement_id
            """,
            announcement_id,
        )
        if record is None:
            return None
        return _announcement_from_record(record)


def _where(criteria: SearchCriteria) -> tuple[str, list[Any]]:
    clauses = [
        "a.parent_announcement_id is null",
        "a.status = 'active'",
        "a.price_period in ('daily', 'monthly')",
    ]
    params: list[Any] = []
    if not criteria.include_per_person:
        clauses.append("a.price_basis = 'total'")
    if criteria.min_price is not None:
        params.append(criteria.min_price)
        clauses.append(f"a.price_normalized_monthly >= ${len(params)}")
    if criteria.max_price is not None:
        params.append(criteria.max_price)
        clauses.append(f"a.price_normalized_monthly <= ${len(params)}")
    if criteria.district is not None:
        params.append(criteria.district)
        clauses.append(f"lower(a.district) = lower(${len(params)})")
    if criteria.rooms is not None:
        params.append(criteria.rooms)
        clauses.append(f"a.rooms = ${len(params)}")
    if criteria.renovation_level is not None:
        params.append(criteria.renovation_level)
        clauses.append(f"a.renovation_level = ${len(params)}")
    if criteria.audience_tag is not None:
        params.append(criteria.audience_tag)
        idx = len(params)
        clauses.append(f"${idx} = any(a.audience_tags)")
        clauses.append(f"not (${idx} = any(a.audience_excluded_tags))")
    return " and ".join(clauses), params


def _notes(criteria: SearchCriteria) -> tuple[str, ...]:
    notes = [
        "Search returns active canonical parent announcements only.",
        "Sale/one_time listings are excluded from monthly rent search.",
    ]
    if criteria.has_price_filter:
        notes.append("Listings with null price_normalized_monthly are excluded by price filters.")
    if criteria.include_per_person:
        notes.append(
            "Per-person prices are matched as per-person values; they are not converted to total."
        )
    return tuple(notes)
