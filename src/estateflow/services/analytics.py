from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Literal, Protocol

AnalyticsEventName = Literal[
    "user_registered",
    "search_completed",
    "saved_filter_created",
    "referral_accepted",
    "referral_activated",
    "notification_sent",
    "notification_delivery_failed",
    "notification_action_open",
    "notification_action_search",
    "notification_action_read",
]

NOTIFICATION_ENGAGEMENT_EVENTS: frozenset[AnalyticsEventName] = frozenset(
    {
        "notification_action_open",
        "notification_action_search",
        "notification_action_read",
    }
)
ACTIVATION_EVENTS: frozenset[AnalyticsEventName] = frozenset(
    {"search_completed", "saved_filter_created"}
)

TechnicalMetricName = Literal[
    "queue_delay_ms",
    "ai_fallback_attempt",
    "ai_validation_failure",
    "dedup_decision",
    "notification_retry",
    "notification_error",
    "listener_health",
]

TECHNICAL_METRIC_NAMES: frozenset[TechnicalMetricName] = frozenset(
    {
        "queue_delay_ms",
        "ai_fallback_attempt",
        "ai_validation_failure",
        "dedup_decision",
        "notification_retry",
        "notification_error",
        "listener_health",
    }
)


@dataclass(frozen=True)
class AnalyticsEvent:
    event_name: AnalyticsEventName
    idempotency_key: str
    occurred_at: datetime
    user_id: int | None = None
    subject_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProductEventBucket:
    bucket_start: datetime
    event_name: AnalyticsEventName
    count: int


@dataclass(frozen=True)
class TechnicalMetricEvent:
    metric_name: TechnicalMetricName
    idempotency_key: str
    occurred_at: datetime
    value: float = 1.0
    component: str | None = None
    subject_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TechnicalMetricSnapshot:
    queue_delay_avg_ms: float | None
    queue_delay_samples: int
    ai_fallback_count: int
    ai_validation_failure_count: int
    dedup_duplicate_rate: float | None
    dedup_duplicate_count: int
    dedup_total_count: int
    notification_retry_count: int
    notification_error_count: int
    listener_health_counts: dict[str, int]


@dataclass(frozen=True)
class MetricRatio:
    numerator: int
    denominator: int
    rate: float | None
    missing_data: str


@dataclass(frozen=True)
class BetaMetricSnapshot:
    activation_rate: MetricRatio
    d7_retention: MetricRatio
    referral_conversion: MetricRatio
    notification_engagement: MetricRatio
    timezone: str
    start_at: datetime
    end_at: datetime
    d7_window_hours: int
    product_event_buckets: tuple[ProductEventBucket, ...] = ()
    technical_metrics: TechnicalMetricSnapshot | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AnalyticsEventRepository(Protocol):
    async def record_once(self, event: AnalyticsEvent) -> bool: ...

    async def list_events(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[AnalyticsEvent]: ...


class TechnicalMetricRepository(Protocol):
    async def record_once(self, event: TechnicalMetricEvent) -> bool: ...

    async def list_events(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[TechnicalMetricEvent]: ...


class AnalyticsRecorder:
    def __init__(self, repository: AnalyticsEventRepository) -> None:
        self._repository = repository

    async def record_event(
        self,
        *,
        event_name: AnalyticsEventName,
        idempotency_key: str,
        occurred_at: datetime | None = None,
        user_id: int | None = None,
        subject_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> bool:
        event = AnalyticsEvent(
            event_name=event_name,
            idempotency_key=idempotency_key,
            occurred_at=_as_utc(occurred_at or datetime.now(UTC)),
            user_id=user_id,
            subject_id=subject_id,
            metadata=dict(metadata or {}),
        )
        return await self._repository.record_once(event)


class TechnicalMetricRecorder:
    def __init__(self, repository: TechnicalMetricRepository) -> None:
        self._repository = repository

    async def record_metric(
        self,
        *,
        metric_name: TechnicalMetricName,
        idempotency_key: str,
        occurred_at: datetime | None = None,
        value: float = 1.0,
        component: str | None = None,
        subject_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> bool:
        event = TechnicalMetricEvent(
            metric_name=metric_name,
            idempotency_key=idempotency_key,
            occurred_at=_as_utc(occurred_at or datetime.now(UTC)),
            value=value,
            component=component,
            subject_id=subject_id,
            metadata=dict(metadata or {}),
        )
        return await self._repository.record_once(event)


class InMemoryAnalyticsEventRepository:
    def __init__(self, events: list[AnalyticsEvent] | None = None) -> None:
        self._events: dict[str, AnalyticsEvent] = {}
        for event in events or []:
            self._events[event.idempotency_key] = event

    async def record_once(self, event: AnalyticsEvent) -> bool:
        if event.idempotency_key in self._events:
            return False
        self._events[event.idempotency_key] = event
        return True

    async def list_events(self, *, start_at: datetime, end_at: datetime) -> list[AnalyticsEvent]:
        start = _as_utc(start_at)
        end = _as_utc(end_at)
        return [
            event for event in self._events.values() if start <= _as_utc(event.occurred_at) < end
        ]


class InMemoryTechnicalMetricRepository:
    def __init__(self, events: list[TechnicalMetricEvent] | None = None) -> None:
        self._events: dict[str, TechnicalMetricEvent] = {}
        for event in events or []:
            self._events[event.idempotency_key] = event

    async def record_once(self, event: TechnicalMetricEvent) -> bool:
        if event.idempotency_key in self._events:
            return False
        self._events[event.idempotency_key] = event
        return True

    async def list_events(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[TechnicalMetricEvent]:
        start = _as_utc(start_at)
        end = _as_utc(end_at)
        return [
            event for event in self._events.values() if start <= _as_utc(event.occurred_at) < end
        ]


class ProductMetricsService:
    def __init__(
        self,
        repository: AnalyticsEventRepository,
        *,
        technical_repository: TechnicalMetricRepository | None = None,
    ) -> None:
        self._repository = repository
        self._technical_repository = technical_repository

    async def beta_snapshot(
        self,
        *,
        start: date,
        end: date,
        timezone_name: str = "Asia/Tashkent",
        d7_window_hours: int = 24,
    ) -> BetaMetricSnapshot:
        if end <= start:
            raise ValueError("end date must be after start date.")
        if d7_window_hours < 1 or d7_window_hours > 72:
            raise ValueError("d7_window_hours must be between 1 and 72.")
        tz = _timezone(timezone_name)
        start_at = _local_date_to_utc(start, tz)
        end_at = _local_date_to_utc(end, tz)
        retention_lookup_end = end_at + timedelta(days=8, hours=d7_window_hours)
        events = await self._repository.list_events(
            start_at=start_at,
            end_at=retention_lookup_end,
        )
        window_events = [
            event for event in events if start_at <= _as_utc(event.occurred_at) < end_at
        ]
        technical_metrics = None
        if self._technical_repository is not None:
            technical_events = await self._technical_repository.list_events(
                start_at=start_at,
                end_at=end_at,
            )
            technical_metrics = technical_snapshot(technical_events)
        return BetaMetricSnapshot(
            activation_rate=_activation_rate(window_events, start_at=start_at, end_at=end_at),
            d7_retention=_d7_retention(
                events,
                start_at=start_at,
                end_at=end_at,
                d7_window=timedelta(hours=d7_window_hours),
            ),
            referral_conversion=_referral_conversion(window_events),
            notification_engagement=_notification_engagement(window_events),
            timezone=timezone_name,
            start_at=start_at,
            end_at=end_at,
            d7_window_hours=d7_window_hours,
            product_event_buckets=event_buckets(
                window_events,
                start_at=start_at,
                end_at=end_at,
                granularity="daily",
            ),
            technical_metrics=technical_metrics,
        )


async def safe_record_event(
    recorder: AnalyticsRecorder | None,
    *,
    event_name: AnalyticsEventName,
    idempotency_key: str,
    occurred_at: datetime | None = None,
    user_id: int | None = None,
    subject_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> None:
    if recorder is None:
        return
    try:
        await recorder.record_event(
            event_name=event_name,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            user_id=user_id,
            subject_id=subject_id,
            metadata=metadata,
        )
    except Exception:
        return


async def safe_record_technical_metric(
    recorder: TechnicalMetricRecorder | None,
    *,
    metric_name: TechnicalMetricName,
    idempotency_key: str,
    occurred_at: datetime | None = None,
    value: float = 1.0,
    component: str | None = None,
    subject_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> None:
    if recorder is None:
        return
    try:
        await recorder.record_metric(
            metric_name=metric_name,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            value=value,
            component=component,
            subject_id=subject_id,
            metadata=metadata,
        )
    except Exception:
        return


def event_buckets(
    events: list[AnalyticsEvent],
    *,
    start_at: datetime,
    end_at: datetime,
    granularity: Literal["hourly", "daily"] = "daily",
) -> tuple[ProductEventBucket, ...]:
    counts: dict[tuple[datetime, AnalyticsEventName], int] = defaultdict(int)
    for event in events:
        occurred = _as_utc(event.occurred_at)
        if not start_at <= occurred < end_at:
            continue
        bucket_start = _bucket_start(occurred, granularity=granularity)
        counts[(bucket_start, event.event_name)] += 1
    return tuple(
        ProductEventBucket(bucket_start=bucket, event_name=event_name, count=count)
        for (bucket, event_name), count in sorted(counts.items())
    )


def technical_snapshot(events: list[TechnicalMetricEvent]) -> TechnicalMetricSnapshot:
    queue_delays = [event.value for event in events if event.metric_name == "queue_delay_ms"]
    dedup_events = [event for event in events if event.metric_name == "dedup_decision"]
    duplicate_decisions = {
        "exact_duplicate",
        "high_confidence_duplicate",
    }
    duplicate_count = sum(
        1 for event in dedup_events if event.metadata.get("decision") in duplicate_decisions
    )
    listener_latest: dict[str, str] = {}
    for event in sorted(events, key=lambda item: item.occurred_at):
        if event.metric_name == "listener_health" and event.subject_id is not None:
            listener_latest[event.subject_id] = event.metadata.get("status", "unknown")
    listener_health_counts: dict[str, int] = {}
    for status in listener_latest.values():
        listener_health_counts[status] = listener_health_counts.get(status, 0) + 1
    dedup_total = len(dedup_events)
    return TechnicalMetricSnapshot(
        queue_delay_avg_ms=(None if not queue_delays else sum(queue_delays) / len(queue_delays)),
        queue_delay_samples=len(queue_delays),
        ai_fallback_count=sum(1 for event in events if event.metric_name == "ai_fallback_attempt"),
        ai_validation_failure_count=sum(
            1 for event in events if event.metric_name == "ai_validation_failure"
        ),
        dedup_duplicate_rate=None if dedup_total == 0 else duplicate_count / dedup_total,
        dedup_duplicate_count=duplicate_count,
        dedup_total_count=dedup_total,
        notification_retry_count=sum(
            1 for event in events if event.metric_name == "notification_retry"
        ),
        notification_error_count=sum(
            1 for event in events if event.metric_name == "notification_error"
        ),
        listener_health_counts=listener_health_counts,
    )


def _activation_rate(
    events: list[AnalyticsEvent],
    *,
    start_at: datetime,
    end_at: datetime,
) -> MetricRatio:
    registrations = {
        event.user_id: event.occurred_at
        for event in events
        if event.event_name == "user_registered" and event.user_id is not None
    }
    active_users = {
        event.user_id
        for event in events
        if event.event_name in ACTIVATION_EVENTS
        and event.user_id in registrations
        and registrations[event.user_id] <= event.occurred_at < end_at
    }
    return _ratio(
        numerator=len(active_users),
        denominator=len(registrations),
        missing_data="If user_registered is missing, the user is excluded from this cohort.",
    )


def _d7_retention(
    events: list[AnalyticsEvent],
    *,
    start_at: datetime,
    end_at: datetime,
    d7_window: timedelta,
) -> MetricRatio:
    registrations = {
        event.user_id: _as_utc(event.occurred_at)
        for event in events
        if event.event_name == "user_registered"
        and event.user_id is not None
        and start_at <= _as_utc(event.occurred_at) < end_at
    }
    retained: set[int] = set()
    for user_id, registered_at in registrations.items():
        window_start = registered_at + timedelta(days=7)
        window_end = window_start + d7_window
        for event in events:
            if (
                event.user_id == user_id
                and event.event_name != "user_registered"
                and window_start <= _as_utc(event.occurred_at) < window_end
            ):
                retained.add(user_id)
                break
    return _ratio(
        numerator=len(retained),
        denominator=len(registrations),
        missing_data=(
            "Users without a user_registered event are excluded; late-arriving day-7 events "
            "are absent until the retention lookup window has elapsed."
        ),
    )


def _referral_conversion(events: list[AnalyticsEvent]) -> MetricRatio:
    invited = {
        event.user_id
        for event in events
        if event.event_name == "referral_accepted" and event.user_id is not None
    }
    activated = {
        event.user_id
        for event in events
        if event.event_name == "referral_activated" and event.user_id in invited
    }
    return _ratio(
        numerator=len(activated),
        denominator=len(invited),
        missing_data="Referral starts without referral_accepted are excluded from the denominator.",
    )


def _notification_engagement(events: list[AnalyticsEvent]) -> MetricRatio:
    sent = {
        event.subject_id
        for event in events
        if event.event_name == "notification_sent" and event.subject_id is not None
    }
    engaged = {
        event.subject_id
        for event in events
        if event.event_name in NOTIFICATION_ENGAGEMENT_EVENTS and event.subject_id in sent
    }
    return _ratio(
        numerator=len(engaged),
        denominator=len(sent),
        missing_data=(
            "Telegram read receipts are not assumed. Only explicit action/callback/read events "
            "recorded by the bot count as engagement."
        ),
    )


def _ratio(*, numerator: int, denominator: int, missing_data: str) -> MetricRatio:
    return MetricRatio(
        numerator=numerator,
        denominator=denominator,
        rate=None if denominator == 0 else numerator / denominator,
        missing_data=missing_data,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _local_date_to_utc(value: date, tz: timezone) -> datetime:
    return datetime.combine(value, time.min, tzinfo=tz).astimezone(UTC)


def _timezone(name: str) -> timezone:
    if name == "UTC":
        return UTC
    if name == "Asia/Tashkent":
        return timezone(timedelta(hours=5), name="Asia/Tashkent")
    raise ValueError("Unsupported metrics timezone.")


def _bucket_start(
    value: datetime,
    *,
    granularity: Literal["hourly", "daily"],
) -> datetime:
    if granularity == "hourly":
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)
