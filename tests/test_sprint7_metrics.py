from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.bot.controller import BotController
from estateflow.repositories.users import InMemoryUserRepository
from estateflow.services.analytics import (
    AnalyticsEvent,
    AnalyticsRecorder,
    InMemoryAnalyticsEventRepository,
    InMemoryTechnicalMetricRepository,
    ProductMetricsService,
    TechnicalMetricEvent,
    TechnicalMetricRecorder,
    event_buckets,
    safe_record_event,
    safe_record_technical_metric,
    technical_snapshot,
)
from estateflow.services.notifications import (
    InMemoryNotificationJobRepository,
    NotificationDeliveryWorker,
    NotificationMessageFormatter,
    TelegramRateLimitError,
    notification_action_callback,
)
from estateflow.services.saved_filters import UserFilter
from estateflow.services.search import SearchCriteria
from estateflow.services.users import UserService
from tests.fixtures import canonical_listing_fixture


def _event(
    event_name: str,
    *,
    key: str,
    at: datetime,
    user_id: int | None = None,
    subject_id: str | None = None,
) -> AnalyticsEvent:
    return AnalyticsEvent(
        event_name=event_name,  # type: ignore[arg-type]
        idempotency_key=key,
        occurred_at=at,
        user_id=user_id,
        subject_id=subject_id,
    )


def _technical_event(
    metric_name: str,
    *,
    key: str,
    at: datetime,
    value: float = 1.0,
    component: str | None = None,
    subject_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> TechnicalMetricEvent:
    return TechnicalMetricEvent(
        metric_name=metric_name,  # type: ignore[arg-type]
        idempotency_key=key,
        occurred_at=at,
        value=value,
        component=component,
        subject_id=subject_id,
        metadata=metadata or {},
    )


@pytest.mark.asyncio
async def test_beta_metrics_use_registration_cohort_and_distinct_events() -> None:
    repo = InMemoryAnalyticsEventRepository(
        [
            _event(
                "user_registered",
                key="u1",
                at=datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
                user_id=1,
            ),
            _event(
                "user_registered",
                key="u2",
                at=datetime(2026, 8, 1, 3, 0, tzinfo=UTC),
                user_id=2,
            ),
            _event(
                "user_registered",
                key="u3",
                at=datetime(2026, 8, 1, 4, 0, tzinfo=UTC),
                user_id=3,
            ),
            _event(
                "search_completed",
                key="s1",
                at=datetime(2026, 8, 1, 5, 0, tzinfo=UTC),
                user_id=1,
            ),
            _event(
                "saved_filter_created",
                key="f1",
                at=datetime(2026, 8, 1, 6, 0, tzinfo=UTC),
                user_id=1,
            ),
            _event(
                "search_completed",
                key="s2",
                at=datetime(2026, 8, 1, 7, 0, tzinfo=UTC),
                user_id=99,
            ),
        ]
    )

    snapshot = await ProductMetricsService(repo).beta_snapshot(
        start=date(2026, 8, 1),
        end=date(2026, 8, 2),
    )

    assert snapshot.activation_rate.denominator == 3
    assert snapshot.activation_rate.numerator == 1
    assert snapshot.activation_rate.rate == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_analytics_recorder_deduplicates_by_idempotency_key() -> None:
    repo = InMemoryAnalyticsEventRepository()
    recorder = AnalyticsRecorder(repo)

    first = await recorder.record_event(
        event_name="notification_action_open",
        idempotency_key="callback:n1:open",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        user_id=1,
        subject_id="n1",
    )
    second = await recorder.record_event(
        event_name="notification_action_open",
        idempotency_key="callback:n1:open",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        user_id=1,
        subject_id="n1",
    )

    assert first is True
    assert second is False
    events = await repo.list_events(
        start_at=datetime(2026, 8, 1, tzinfo=UTC),
        end_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    assert len(events) == 1


@pytest.mark.asyncio
async def test_d7_retention_counts_return_inside_configured_window() -> None:
    registered_at = datetime(2026, 8, 1, 5, 30, tzinfo=UTC)
    repo = InMemoryAnalyticsEventRepository(
        [
            _event("user_registered", key="u1", at=registered_at, user_id=1),
            _event(
                "search_completed",
                key="return-u1",
                at=registered_at + timedelta(days=7, hours=1),
                user_id=1,
            ),
            _event("user_registered", key="u2", at=registered_at, user_id=2),
            _event(
                "search_completed",
                key="late-u2",
                at=registered_at + timedelta(days=8, hours=2),
                user_id=2,
            ),
        ]
    )

    snapshot = await ProductMetricsService(repo).beta_snapshot(
        start=date(2026, 8, 1),
        end=date(2026, 8, 2),
        d7_window_hours=24,
    )

    assert snapshot.d7_retention.denominator == 2
    assert snapshot.d7_retention.numerator == 1


@pytest.mark.asyncio
async def test_metrics_window_uses_configured_timezone_boundaries() -> None:
    repo = InMemoryAnalyticsEventRepository(
        [
            _event(
                "user_registered",
                key="before-local-day",
                at=datetime(2026, 7, 31, 18, 59, tzinfo=UTC),
                user_id=1,
            ),
            _event(
                "user_registered",
                key="inside-local-day",
                at=datetime(2026, 7, 31, 19, 0, tzinfo=UTC),
                user_id=2,
            ),
            _event(
                "search_completed",
                key="inside-search",
                at=datetime(2026, 8, 1, 3, 0, tzinfo=UTC),
                user_id=2,
            ),
        ]
    )

    snapshot = await ProductMetricsService(repo).beta_snapshot(
        start=date(2026, 8, 1),
        end=date(2026, 8, 2),
        timezone_name="Asia/Tashkent",
    )

    assert snapshot.start_at == datetime(2026, 7, 31, 19, 0, tzinfo=UTC)
    assert snapshot.activation_rate.denominator == 1
    assert snapshot.activation_rate.numerator == 1


@pytest.mark.asyncio
async def test_referral_conversion_and_notification_engagement_are_not_confused() -> None:
    repo = InMemoryAnalyticsEventRepository(
        [
            _event(
                "referral_accepted",
                key="r-accepted-1",
                at=datetime(2026, 8, 1, 1, tzinfo=UTC),
                user_id=10,
                subject_id="1",
            ),
            _event(
                "referral_accepted",
                key="r-accepted-2",
                at=datetime(2026, 8, 1, 2, tzinfo=UTC),
                user_id=11,
                subject_id="1",
            ),
            _event(
                "referral_activated",
                key="r-active-1",
                at=datetime(2026, 8, 1, 3, tzinfo=UTC),
                user_id=10,
                subject_id="1",
            ),
            _event(
                "notification_sent",
                key="n-sent-1",
                at=datetime(2026, 8, 1, 4, tzinfo=UTC),
                user_id=1,
                subject_id="n1",
            ),
            _event(
                "notification_sent",
                key="n-sent-2",
                at=datetime(2026, 8, 1, 5, tzinfo=UTC),
                user_id=2,
                subject_id="n2",
            ),
            _event(
                "notification_action_search",
                key="n-action-1",
                at=datetime(2026, 8, 1, 6, tzinfo=UTC),
                user_id=1,
                subject_id="n1",
            ),
            _event(
                "notification_action_read",
                key="n-action-unsent",
                at=datetime(2026, 8, 1, 7, tzinfo=UTC),
                user_id=3,
                subject_id="n3",
            ),
        ]
    )

    snapshot = await ProductMetricsService(repo).beta_snapshot(
        start=date(2026, 8, 1),
        end=date(2026, 8, 2),
    )

    assert snapshot.referral_conversion.denominator == 2
    assert snapshot.referral_conversion.numerator == 1
    assert snapshot.notification_engagement.denominator == 2
    assert snapshot.notification_engagement.numerator == 1


@pytest.mark.asyncio
async def test_safe_record_event_does_not_break_main_flow_on_metric_failure() -> None:
    class BrokenRepository:
        async def record_once(self, event: AnalyticsEvent) -> bool:
            raise RuntimeError("metrics unavailable")

        async def list_events(
            self, *, start_at: datetime, end_at: datetime
        ) -> list[AnalyticsEvent]:
            return []

    await safe_record_event(
        AnalyticsRecorder(BrokenRepository()),
        event_name="user_registered",
        idempotency_key="u1",
        user_id=1,
    )


@pytest.mark.asyncio
async def test_safe_record_technical_metric_does_not_break_main_flow_on_failure() -> None:
    class BrokenRepository:
        async def record_once(self, event: TechnicalMetricEvent) -> bool:
            raise RuntimeError("technical metrics unavailable")

        async def list_events(
            self, *, start_at: datetime, end_at: datetime
        ) -> list[TechnicalMetricEvent]:
            return []

    await safe_record_technical_metric(
        TechnicalMetricRecorder(BrokenRepository()),
        metric_name="queue_delay_ms",
        idempotency_key="queue-delay-1",
        value=42,
    )


@pytest.mark.asyncio
async def test_product_event_buckets_and_technical_snapshot_are_deduped() -> None:
    at = datetime(2026, 8, 1, 5, tzinfo=UTC)
    product_repo = InMemoryAnalyticsEventRepository(
        [
            _event("user_registered", key="u1", at=at, user_id=1),
            _event("user_registered", key="u2", at=at + timedelta(hours=1), user_id=2),
            _event("search_completed", key="s1", at=at + timedelta(hours=2), user_id=1),
        ]
    )
    technical_repo = InMemoryTechnicalMetricRepository()
    technical_recorder = TechnicalMetricRecorder(technical_repo)
    await technical_recorder.record_metric(
        metric_name="queue_delay_ms",
        idempotency_key="queue-1",
        occurred_at=at,
        value=100,
        component="pre_ai_ingestion",
    )
    await technical_recorder.record_metric(
        metric_name="queue_delay_ms",
        idempotency_key="queue-1",
        occurred_at=at,
        value=500,
        component="pre_ai_ingestion",
    )
    await technical_recorder.record_metric(
        metric_name="queue_delay_ms",
        idempotency_key="queue-2",
        occurred_at=at,
        value=300,
        component="pre_ai_ingestion",
    )
    await technical_recorder.record_metric(
        metric_name="ai_fallback_attempt",
        idempotency_key="ai-fallback-1",
        occurred_at=at,
        component="ai_worker",
    )
    await technical_recorder.record_metric(
        metric_name="ai_validation_failure",
        idempotency_key="ai-validation-1",
        occurred_at=at,
        component="ai_worker",
    )
    await technical_recorder.record_metric(
        metric_name="dedup_decision",
        idempotency_key="dedup-1",
        occurred_at=at,
        component="post_ai_dedup",
        metadata={"decision": "exact_duplicate"},
    )
    await technical_recorder.record_metric(
        metric_name="dedup_decision",
        idempotency_key="dedup-2",
        occurred_at=at,
        component="post_ai_dedup",
        metadata={"decision": "new"},
    )
    await technical_recorder.record_metric(
        metric_name="notification_retry",
        idempotency_key="notif-retry-1",
        occurred_at=at,
        component="notification_delivery",
    )
    await technical_recorder.record_metric(
        metric_name="notification_error",
        idempotency_key="notif-error-1",
        occurred_at=at,
        component="notification_delivery",
    )
    await technical_recorder.record_metric(
        metric_name="listener_health",
        idempotency_key="listener-a-online",
        occurred_at=at,
        component="listener_pool",
        subject_id="listener-a",
        metadata={"status": "online"},
    )
    await technical_recorder.record_metric(
        metric_name="listener_health",
        idempotency_key="listener-a-flood",
        occurred_at=at + timedelta(minutes=1),
        component="listener_pool",
        subject_id="listener-a",
        metadata={"status": "flood_wait"},
    )
    await technical_recorder.record_metric(
        metric_name="source_parser_decision",
        idempotency_key="parser-source-a-emit",
        occurred_at=at,
        component="source_parser",
        subject_id="source-a",
        metadata={"decision": "emit", "parser_version": "1"},
    )
    await technical_recorder.record_metric(
        metric_name="source_parser_decision",
        idempotency_key="parser-source-b-drop",
        occurred_at=at,
        component="source_parser",
        subject_id="source-b",
        metadata={"decision": "drop", "parser_version": "2"},
    )

    snapshot = await ProductMetricsService(
        product_repo,
        technical_repository=technical_repo,
    ).beta_snapshot(start=date(2026, 8, 1), end=date(2026, 8, 2))

    bucket_counts = {
        (bucket.bucket_start, bucket.event_name): bucket.count
        for bucket in snapshot.product_event_buckets
    }
    assert bucket_counts[(datetime(2026, 8, 1, tzinfo=UTC), "user_registered")] == 2
    assert snapshot.technical_metrics is not None
    assert snapshot.technical_metrics.queue_delay_avg_ms == pytest.approx(200)
    assert snapshot.technical_metrics.queue_delay_samples == 2
    assert snapshot.technical_metrics.ai_fallback_count == 1
    assert snapshot.technical_metrics.ai_validation_failure_count == 1
    assert snapshot.technical_metrics.dedup_duplicate_rate == pytest.approx(0.5)
    assert snapshot.technical_metrics.notification_retry_count == 1
    assert snapshot.technical_metrics.notification_error_count == 1
    assert snapshot.technical_metrics.listener_health_counts == {"flood_wait": 1}
    assert snapshot.technical_metrics.source_parser_decision_count == 2
    assert snapshot.technical_metrics.source_parser_decision_counts == {
        "drop": 1,
        "emit": 1,
    }
    assert snapshot.technical_metrics.source_parser_source_counts == {
        "source-a": {"emit": 1},
        "source-b": {"drop": 1},
    }


def test_product_event_hourly_buckets_are_available_for_dashboard_drilldown() -> None:
    events = [
        _event(
            "search_completed",
            key="search-1",
            at=datetime(2026, 8, 1, 5, 15, tzinfo=UTC),
            user_id=1,
        ),
        _event(
            "saved_filter_created",
            key="filter-1",
            at=datetime(2026, 8, 1, 5, 45, tzinfo=UTC),
            user_id=1,
        ),
    ]

    buckets = event_buckets(
        events,
        start_at=datetime(2026, 8, 1, tzinfo=UTC),
        end_at=datetime(2026, 8, 2, tzinfo=UTC),
        granularity="hourly",
    )

    assert {bucket.bucket_start for bucket in buckets} == {datetime(2026, 8, 1, 5, tzinfo=UTC)}


def test_technical_snapshot_keeps_product_metrics_separate() -> None:
    snapshot = technical_snapshot(
        [
            _technical_event(
                "notification_retry",
                key="retry-1",
                at=datetime(2026, 8, 1, tzinfo=UTC),
                component="notification_delivery",
            ),
            _technical_event(
                "notification_error",
                key="error-1",
                at=datetime(2026, 8, 1, tzinfo=UTC),
                component="notification_delivery",
            ),
        ]
    )

    assert snapshot.notification_retry_count == 1
    assert snapshot.notification_error_count == 1
    assert snapshot.queue_delay_avg_ms is None


def test_beta_metrics_endpoint_is_admin_protected_and_returns_snapshot() -> None:
    repo = InMemoryAnalyticsEventRepository(
        [
            _event(
                "user_registered",
                key="u1",
                at=datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
                user_id=1,
            ),
            _event(
                "search_completed",
                key="s1",
                at=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
                user_id=1,
            ),
        ]
    )
    technical_repo = InMemoryTechnicalMetricRepository(
        [
            _technical_event(
                "queue_delay_ms",
                key="queue-1",
                at=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
                value=250,
            )
        ]
    )
    app = create_app(Settings(environment="test", admin_api_token=SecretStr("secret")))
    app.state.metrics_service = ProductMetricsService(repo, technical_repository=technical_repo)
    client = TestClient(app)

    denied = client.get("/admin/metrics/beta")
    allowed = client.get(
        "/admin/metrics/beta?start=2026-08-01&end=2026-08-02",
        headers={"x-admin-token": "secret"},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["activation_rate"]["numerator"] == 1
    assert allowed.json()["technical_metrics"]["queue_delay_avg_ms"] == 250
    assert allowed.json()["technical_metrics"]["source_parser_decision_count"] == 0


@pytest.mark.asyncio
async def test_bot_controller_records_notification_action_callbacks() -> None:
    repo = InMemoryAnalyticsEventRepository()
    controller = BotController(
        user_service=UserService(InMemoryUserRepository()),
        analytics_recorder=AnalyticsRecorder(repo),
    )

    screen = await controller.handle_menu_callback(
        user_id=42,
        data=notification_action_callback(notification_id="n-1", action="open"),
    )

    events = await repo.list_events(
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert screen.text == "E'lon tafsilotlari ochildi."
    assert events[0].event_name == "notification_action_open"
    assert events[0].user_id == 42
    assert events[0].subject_id == "n-1"


@pytest.mark.asyncio
async def test_notification_worker_records_retry_as_technical_metric_not_engagement() -> None:
    class RateLimitedTelegram:
        async def send_message(
            self,
            *,
            chat_id: int,
            text: str,
            reply_markup: dict[str, object] | None = None,
        ) -> object:
            raise TelegramRateLimitError(retry_after_seconds=30)

    job_repo = InMemoryNotificationJobRepository()
    saved_filter = UserFilter(
        filter_id="filter-1",
        user_id=1,
        name="Yunusobod",
        criteria=SearchCriteria(district="Yunusobod"),
    )
    announcement = canonical_listing_fixture(announcement_id="ann-1")
    job, created = await job_repo.create_pending(
        user_id=1,
        filter_id=saved_filter.filter_id,
        announcement_id=announcement.announcement_id,
        priority=0,
        queue_name="notifications.standard",
    )
    job_repo.add_delivery_context(announcement=announcement, saved_filter=saved_filter)
    product_repo = InMemoryAnalyticsEventRepository()
    technical_repo = InMemoryTechnicalMetricRepository()

    result = await NotificationDeliveryWorker(
        repository=job_repo,
        telegram=RateLimitedTelegram(),  # type: ignore[arg-type]
        formatter=NotificationMessageFormatter(),
        analytics_recorder=AnalyticsRecorder(product_repo),
        technical_recorder=TechnicalMetricRecorder(technical_repo),
    ).process_batch(now=datetime(2026, 8, 1, 9, tzinfo=UTC))

    product_events = await product_repo.list_events(
        start_at=datetime(2026, 8, 1, tzinfo=UTC),
        end_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    technical_events = await technical_repo.list_events(
        start_at=datetime(2026, 8, 1, tzinfo=UTC),
        end_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    assert created is True
    assert result.metrics.retryable_failures == 1
    assert [event.event_name for event in product_events] == ["notification_delivery_failed"]
    assert [event.metric_name for event in technical_events] == ["notification_retry"]
    assert technical_events[0].subject_id == job.notification_id
