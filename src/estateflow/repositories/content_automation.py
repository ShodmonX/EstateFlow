from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

import asyncpg  # type: ignore[import-untyped]

from estateflow.services.content_automation import (
    ContentDistrictStats,
    ContentPost,
    ContentPostKind,
    ContentPostStatus,
    ContentStats,
    ContentTopOffer,
)


class AsyncpgContentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def daily_stats(self, *, day: date) -> ContentStats:
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(
                """
                select
                    (
                        select count(*)
                        from announcements
                        where occurred_at::date = $1::date
                    ) as total_checked,
                    (
                        select count(*)
                        from dedup_decisions
                        where created_at::date = $1::date
                          and decision in (
                              'exact_duplicate',
                              'high_confidence_duplicate',
                              'possible_duplicate'
                          )
                    ) as duplicates_detected,
                    (
                        select count(*)
                        from announcements
                        where parent_announcement_id is null
                          and status = 'active'
                    ) as active_listings,
                    (
                        select avg(price_normalized_monthly)
                        from announcements
                        where parent_announcement_id is null
                          and status = 'active'
                          and price_basis = 'total'
                          and price_period in ('daily', 'monthly')
                          and price_normalized_monthly is not null
                          and price_normalized_monthly > 0
                    ) as average_monthly_price
                """,
                day,
            )
        assert record is not None
        return ContentStats(
            total_checked=int(record["total_checked"] or 0),
            duplicates_detected=int(record["duplicates_detected"] or 0),
            active_listings=int(record["active_listings"] or 0),
            average_monthly_price=cast(Decimal | None, record["average_monthly_price"]),
        )

    async def top_offers(self, *, day: date, limit: int) -> tuple[ContentTopOffer, ...]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                """
                select
                    announcement_id,
                    district,
                    rooms,
                    price_normalized_monthly,
                    currency,
                    source_url,
                    area_sqm,
                    source_count,
                    created_at
                from announcements
                where parent_announcement_id is null
                  and status = 'active'
                  and occurred_at::date = $1::date
                  and price_basis = 'total'
                  and price_period in ('daily', 'monthly')
                  and price_normalized_monthly is not null
                  and price_normalized_monthly > 0
                  and currency in ('USD', 'UZS')
                order by price_normalized_monthly asc, source_count desc, created_at asc
                limit $2
                """,
                day,
                max(1, min(limit, 10)),
            )
        return tuple(_top_offer_from_record(record) for record in records)

    async def district_stats(self, *, day: date, limit: int) -> tuple[ContentDistrictStats, ...]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                """
                select
                    district,
                    count(*) as listing_count,
                    avg(price_normalized_monthly) as average_monthly_price,
                    min(price_normalized_monthly) as min_monthly_price,
                    max(price_normalized_monthly) as max_monthly_price
                from announcements
                where parent_announcement_id is null
                  and status = 'active'
                  and district is not null
                  and price_basis = 'total'
                  and price_period in ('daily', 'monthly')
                  and price_normalized_monthly is not null
                  and price_normalized_monthly > 0
                  and currency in ('USD', 'UZS')
                group by district
                having count(*) >= 2
                order by listing_count desc, average_monthly_price asc, district asc
                limit $1
                """,
                max(1, min(limit, 10)),
            )
        return tuple(_district_stats_from_record(record) for record in records)

    async def count_posts(self, *, day: date) -> int:
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    """
                    select count(*)
                    from content_channel_posts
                    where idempotency_key like $1
                      and status in ('drafted', 'published', 'dry_run')
                    """,
                    f"{day.isoformat()}:%",
                )
                or 0
            )

    async def get_by_idempotency_key(self, *, idempotency_key: str) -> ContentPost | None:
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(
                """
                select *
                from content_channel_posts
                where idempotency_key = $1
                """,
                idempotency_key,
            )
        return None if record is None else _post_from_record(record)

    async def save_post(self, post: ContentPost) -> ContentPost:
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(
                """
                insert into content_channel_posts(
                    post_id,
                    post_kind,
                    idempotency_key,
                    text,
                    status,
                    publisher_message_id,
                    published_at,
                    attempts,
                    next_attempt_at,
                    error_reason,
                    created_at
                )
                values (
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
                )
                on conflict (idempotency_key) do update set
                    text = excluded.text,
                    status = excluded.status,
                    publisher_message_id = excluded.publisher_message_id,
                    published_at = excluded.published_at,
                    attempts = excluded.attempts,
                    next_attempt_at = excluded.next_attempt_at,
                    error_reason = excluded.error_reason
                returning *
                """,
                post.post_id,
                post.kind,
                post.idempotency_key,
                post.text,
                post.status,
                post.publisher_message_id,
                post.published_at,
                post.attempts,
                post.next_attempt_at,
                post.error_reason,
                post.created_at,
            )
        assert record is not None
        return _post_from_record(record)


def _top_offer_from_record(record: Any) -> ContentTopOffer:
    return ContentTopOffer(
        announcement_id=str(record["announcement_id"]),
        district=record["district"],
        rooms=record["rooms"],
        price_normalized_monthly=record["price_normalized_monthly"],
        currency=record["currency"],
        source_url=record["source_url"],
        area_sqm=record["area_sqm"],
        source_count=int(record["source_count"] or 1),
        created_at=record["created_at"],
    )


def _district_stats_from_record(record: Any) -> ContentDistrictStats:
    return ContentDistrictStats(
        district=str(record["district"]),
        listing_count=int(record["listing_count"] or 0),
        average_monthly_price=record["average_monthly_price"],
        min_monthly_price=record["min_monthly_price"],
        max_monthly_price=record["max_monthly_price"],
    )


def _post_from_record(record: Any) -> ContentPost:
    return ContentPost(
        post_id=str(record["post_id"]),
        kind=cast(ContentPostKind, record["post_kind"]),
        idempotency_key=str(record["idempotency_key"]),
        text=str(record["text"]),
        status=cast(ContentPostStatus, record["status"]),
        published_at=record["published_at"],
        publisher_message_id=record["publisher_message_id"],
        attempts=int(record["attempts"] or 0),
        next_attempt_at=record["next_attempt_at"],
        error_reason=record["error_reason"],
        created_at=record["created_at"],
    )
