from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from estateflow.services.analytics import AnalyticsEvent, TechnicalMetricEvent


class AsyncpgAnalyticsEventRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_once(self, event: AnalyticsEvent) -> bool:
        record = await self._pool.fetchrow(
            """
            insert into analytics_events(
                event_name, idempotency_key, occurred_at, user_id, subject_id, metadata
            )
            values ($1, $2, $3, $4, $5, $6::jsonb)
            on conflict (idempotency_key) do nothing
            returning analytics_event_id
            """,
            event.event_name,
            event.idempotency_key,
            event.occurred_at,
            event.user_id,
            event.subject_id,
            event.metadata,
        )
        return record is not None

    async def list_events(self, *, start_at: datetime, end_at: datetime) -> list[AnalyticsEvent]:
        records = await self._pool.fetch(
            """
            select event_name, idempotency_key, occurred_at, user_id, subject_id, metadata
            from analytics_events
            where occurred_at >= $1 and occurred_at < $2
            order by occurred_at asc, analytics_event_id asc
            """,
            start_at,
            end_at,
        )
        return [_from_record(record) for record in records]


def _from_record(record: Any) -> AnalyticsEvent:
    return AnalyticsEvent(
        event_name=record["event_name"],
        idempotency_key=str(record["idempotency_key"]),
        occurred_at=record["occurred_at"],
        user_id=int(record["user_id"]) if record["user_id"] is not None else None,
        subject_id=str(record["subject_id"]) if record["subject_id"] is not None else None,
        metadata=dict(record["metadata"] or {}),
    )


class AsyncpgTechnicalMetricRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_once(self, event: TechnicalMetricEvent) -> bool:
        record = await self._pool.fetchrow(
            """
            insert into technical_metric_events(
                metric_name, idempotency_key, occurred_at, value,
                component, subject_id, metadata
            )
            values ($1, $2, $3, $4, $5, $6, $7::jsonb)
            on conflict (idempotency_key) do nothing
            returning technical_metric_event_id
            """,
            event.metric_name,
            event.idempotency_key,
            event.occurred_at,
            event.value,
            event.component,
            event.subject_id,
            event.metadata,
        )
        return record is not None

    async def list_events(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[TechnicalMetricEvent]:
        records = await self._pool.fetch(
            """
            select metric_name, idempotency_key, occurred_at, value,
                   component, subject_id, metadata
            from technical_metric_events
            where occurred_at >= $1 and occurred_at < $2
            order by occurred_at asc, technical_metric_event_id asc
            """,
            start_at,
            end_at,
        )
        return [_technical_from_record(record) for record in records]


def _technical_from_record(record: Any) -> TechnicalMetricEvent:
    return TechnicalMetricEvent(
        metric_name=record["metric_name"],
        idempotency_key=str(record["idempotency_key"]),
        occurred_at=record["occurred_at"],
        value=float(record["value"]),
        component=str(record["component"]) if record["component"] is not None else None,
        subject_id=str(record["subject_id"]) if record["subject_id"] is not None else None,
        metadata=dict(record["metadata"] or {}),
    )
