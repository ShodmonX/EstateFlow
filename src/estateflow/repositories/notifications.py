from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from estateflow.repositories.post_ai_dedup import _announcement_from_record_with_media
from estateflow.repositories.saved_filters import _from_record as _filter_from_record
from estateflow.services.notifications import (
    STANDARD_NOTIFICATION_QUEUE,
    NotificationAttemptResult,
    NotificationDeliveryAttempt,
    NotificationDeliveryContext,
    NotificationJob,
    NotificationQueueName,
    NotificationStatus,
    NotificationUser,
)
from estateflow.services.saved_filters import UserFilter


class AsyncpgNotificationSavedFilterRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_for_matching(self) -> list[UserFilter]:
        records = await self._pool.fetch(
            """
            select *
            from user_filters
            order by created_at desc, filter_id desc
            """
        )
        filters: list[UserFilter] = []
        for record in records:
            try:
                filters.append(_filter_from_record(record))
            except Exception:
                continue
        return filters


class AsyncpgNotificationUserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_notification_user(self, *, user_id: int) -> NotificationUser | None:
        record = await self._pool.fetchrow(
            """
            select user_id, premium_until, status
            from users
            where user_id = $1
            """,
            user_id,
        )
        if record is None:
            return None
        return NotificationUser(
            user_id=int(record["user_id"]),
            premium_until=record["premium_until"],
            status=str(record["status"]),
            notifications_enabled=True,
        )


class AsyncpgNotificationJobRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_pending(
        self,
        *,
        user_id: int,
        filter_id: str,
        announcement_id: str,
        priority: int,
        queue_name: NotificationQueueName,
    ) -> tuple[NotificationJob, bool]:
        record = await self._pool.fetchrow(
            """
            insert into notifications(user_id, announcement_id, filter_id, status, priority)
            values ($1, $2::uuid, $3::uuid, 'pending', $4)
            on conflict (user_id, announcement_id, filter_id) do nothing
            returning *
            """,
            user_id,
            announcement_id,
            filter_id,
            priority,
        )
        if record is not None:
            return _job_from_record(record, queue_name=queue_name), True
        existing = await self._pool.fetchrow(
            """
            select *
            from notifications
            where user_id = $1 and announcement_id = $2::uuid and filter_id = $3::uuid
            """,
            user_id,
            announcement_id,
            filter_id,
        )
        assert existing is not None
        return _job_from_record(existing, queue_name=queue_name), False

    async def list_ready(self, *, limit: int, now: datetime) -> list[NotificationJob]:
        records = await self._pool.fetch(
            """
            select *
            from notifications
            where status = 'pending'
              and (next_attempt_at is null or next_attempt_at <= $1)
            order by priority desc, created_at asc, notification_id asc
            limit $2
            """,
            now,
            limit,
        )
        return [
            _job_from_record(
                record,
                queue_name=HIGH_PRIORITY_QUEUE_FROM_PRIORITY(record["priority"]),
            )
            for record in records
        ]

    async def get_delivery_context(
        self,
        *,
        notification_id: str,
    ) -> NotificationDeliveryContext | None:
        record = await self._pool.fetchrow(
            """
            select
                n.*,
                uf.name as filter_name,
                a.announcement_id, a.idempotency_key, a.source_id, a.source_channel_id,
                a.source_message_id, a.occurred_at, a.parent_announcement_id, a.status,
                a.source_count, a.latest_source_id, a.source_url, a.forward_origin_key,
                a.price, a.currency, a.price_period, a.price_basis,
                a.price_normalized_monthly, a.rooms, a.area_sqm, a.floor, a.total_floors,
                a.district, a.address, a.phone_numbers, a.owner_type, a.renovation_level,
                a.renovation_source, a.furniture, a.description, a.audience_tags,
                a.audience_excluded_tags, a.confidence, a.field_confidence,
                a.assumptions, a.prompt_version,
                coalesce(
                    jsonb_agg(
                        jsonb_build_object(
                            'media_id', m.media_id,
                            'storage_url', m.storage_url,
                            'object_key', m.object_key,
                            'mime_type', m.mime_type,
                            'size_bytes', m.size_bytes,
                            'phash', m.phash,
                            'content_sha256', m.content_sha256,
                            'width', m.width,
                            'height', m.height,
                            'metadata', m.metadata
                        )
                    ) filter (where m.media_id is not null),
                    '[]'::jsonb
                ) as media_items
            from notifications n
            join announcements a on a.announcement_id = n.announcement_id
            left join user_filters uf on uf.filter_id = n.filter_id
            left join announcement_media m on m.announcement_id = a.announcement_id
            where n.notification_id = $1::uuid
            group by n.notification_id, uf.name, a.announcement_id
            """,
            notification_id,
        )
        if record is None or record["filter_name"] is None:
            return None
        return NotificationDeliveryContext(
            job=_job_from_record(
                record,
                queue_name=HIGH_PRIORITY_QUEUE_FROM_PRIORITY(record["priority"]),
            ),
            announcement=_announcement_from_record_with_media(record, record["media_items"]),
            filter_name=str(record["filter_name"]),
        )

    async def mark_sending(
        self,
        *,
        notification_id: str,
        now: datetime,
    ) -> NotificationJob | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                record = await connection.fetchrow(
                    """
                    select *
                    from notifications
                    where notification_id = $1::uuid
                      and status = 'pending'
                      and (next_attempt_at is null or next_attempt_at <= $2)
                    for update skip locked
                    """,
                    notification_id,
                    now,
                )
                if record is None:
                    return None
                updated = await connection.fetchrow(
                    """
                    update notifications
                    set status = 'sending',
                        attempts = attempts + 1,
                        last_attempt_at = $2
                    where notification_id = $1::uuid
                    returning *
                    """,
                    notification_id,
                    now,
                )
                assert updated is not None
                job = _job_from_record(
                    updated,
                    queue_name=HIGH_PRIORITY_QUEUE_FROM_PRIORITY(updated["priority"]),
                )
                await _record_attempt(
                    connection,
                    notification_id=notification_id,
                    attempt_no=job.attempts,
                    result="sending",
                    now=now,
                )
                return job

    async def mark_sent(
        self,
        *,
        notification_id: str,
        telegram_message_id: str,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        async with self._pool.acquire() as connection:
            updated = await connection.fetchrow(
                """
                update notifications
                set status = 'sent',
                    sent_at = $2,
                    delivered_at = $2,
                    telegram_message_id = $3,
                    next_attempt_at = null,
                    error_reason = null
                where notification_id = $1::uuid
                returning attempts
                """,
                notification_id,
                now,
                telegram_message_id,
            )
            assert updated is not None
            return await _record_attempt(
                connection,
                notification_id=notification_id,
                attempt_no=int(updated["attempts"]),
                result="sent",
                telegram_message_id=telegram_message_id,
                now=now,
            )

    async def mark_retryable_failure(
        self,
        *,
        notification_id: str,
        error_type: str,
        retry_after: timedelta,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        result: NotificationAttemptResult = (
            "rate_limited" if "rate_limited" in error_type else "retryable_failure"
        )
        async with self._pool.acquire() as connection:
            updated = await connection.fetchrow(
                """
                update notifications
                set status = 'pending',
                    next_attempt_at = $2,
                    error_reason = $3
                where notification_id = $1::uuid
                returning attempts
                """,
                notification_id,
                now + retry_after,
                error_type,
            )
            assert updated is not None
            return await _record_attempt(
                connection,
                notification_id=notification_id,
                attempt_no=int(updated["attempts"]),
                result=result,
                error_type=error_type,
                retryable=True,
                now=now,
            )

    async def mark_permanent_failure(
        self,
        *,
        notification_id: str,
        error_type: str,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        status: NotificationStatus = (
            "suppressed" if error_type == "telegram_user_blocked" else "failed"
        )
        async with self._pool.acquire() as connection:
            updated = await connection.fetchrow(
                """
                update notifications
                set status = $2,
                    next_attempt_at = null,
                    error_reason = $3
                where notification_id = $1::uuid
                returning attempts
                """,
                notification_id,
                status,
                error_type,
            )
            assert updated is not None
            return await _record_attempt(
                connection,
                notification_id=notification_id,
                attempt_no=int(updated["attempts"]),
                result="permanent_failure",
                error_type=error_type,
                retryable=False,
                now=now,
            )


def _job_from_record(record: Any, *, queue_name: NotificationQueueName) -> NotificationJob:
    created_at = record["created_at"]
    if created_at is None:
        created_at = datetime.now(UTC)
    return NotificationJob(
        notification_id=str(record["notification_id"]),
        user_id=int(record["user_id"]),
        filter_id=str(record["filter_id"]),
        announcement_id=str(record["announcement_id"]),
        status=record["status"],
        priority=int(record["priority"]),
        queue_name=queue_name,
        created_at=created_at,
        attempts=int(record["attempts"] or 0) if "attempts" in record else 0,
        next_attempt_at=record["next_attempt_at"] if "next_attempt_at" in record else None,
        telegram_message_id=record["telegram_message_id"]
        if "telegram_message_id" in record
        else None,
    )


def HIGH_PRIORITY_QUEUE_FROM_PRIORITY(priority: int) -> NotificationQueueName:
    if int(priority) > 0:
        return "notifications.high_priority"
    return STANDARD_NOTIFICATION_QUEUE


async def _record_attempt(
    connection: asyncpg.Connection,
    *,
    notification_id: str,
    attempt_no: int,
    result: NotificationAttemptResult,
    now: datetime,
    error_type: str | None = None,
    retryable: bool = False,
    telegram_message_id: str | None = None,
) -> NotificationDeliveryAttempt:
    await connection.execute(
        """
        insert into notification_delivery_attempts(
            notification_id, attempt_no, result, error_type, retryable,
            telegram_message_id, created_at
        )
        values ($1::uuid, $2, $3, $4, $5, $6, $7)
        """,
        notification_id,
        attempt_no,
        result,
        error_type,
        retryable,
        telegram_message_id,
        now,
    )
    return NotificationDeliveryAttempt(
        notification_id=notification_id,
        attempt_no=attempt_no,
        result=result,
        error_type=error_type,
        retryable=retryable,
        telegram_message_id=telegram_message_id,
        created_at=now,
    )
