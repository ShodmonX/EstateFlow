from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from estateflow.services.extraction import CanonicalListing
from estateflow.services.notifications import (
    InMemoryNotificationJobRepository,
    InMemoryNotificationRateLimiter,
    NotificationDeliveryContext,
    NotificationDeliveryWorker,
    NotificationJob,
    NotificationMessageFormatter,
    TelegramRateLimitError,
    TelegramSendResult,
    TelegramTransientError,
    TelegramUserBlockedError,
    parse_notification_action_callback,
)
from estateflow.services.post_ai_dedup import StructuredAnnouncement
from estateflow.services.saved_filters import UserFilter
from estateflow.services.search import SearchCriteria


class FakeTelegram:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = outcomes or []
        self.sent: list[dict[str, object]] = []

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramSendResult:
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            assert isinstance(outcome, TelegramSendResult)
            return outcome
        return TelegramSendResult(message_id=f"m{len(self.sent)}")


class FakeOps:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    @property
    def enabled(self) -> bool:
        return True

    async def notify(
        self,
        *,
        severity: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> bool:
        self.calls.append(
            {"severity": severity, "reason": reason, "correlation_id": correlation_id}
        )
        return True


def _filter(*, user_id: int = 1, name: str = "Yunusobod 2 xona") -> UserFilter:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return UserFilter(
        filter_id=str(uuid4()),
        user_id=user_id,
        name=name,
        criteria=SearchCriteria(district="Yunusobod", rooms=2, max_price=Decimal("600")),
        enabled=True,
        created_at=now,
        updated_at=now,
    )


def _canonical(*, description: str = "Yaxshi uy. Telefon: +998901112233") -> CanonicalListing:
    return CanonicalListing(
        prompt_version="test",
        price=Decimal("500"),
        currency="USD",
        price_period="monthly",
        price_basis="total",
        price_normalized_monthly=Decimal("500"),
        listing_type="rent",
        rooms=2,
        area_sqm=None,
        floor=None,
        total_floors=None,
        district="Yunusobod",
        address=None,
        phone_numbers=["+998901112233"],
        owner_type="unknown",
        renovation_level="good",
        renovation_source="vision",
        furniture=None,
        description=description,
        audience_tags=[],
        audience_excluded_tags=[],
        confidence=0.9,
        field_confidence={},
    )


def _announcement(announcement_id: str = "a1") -> StructuredAnnouncement:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id="source-1",
        source_channel_id="-100",
        source_message_id=announcement_id,
        occurred_at=now,
        canonical=_canonical(),
        source_url="https://example.test/listing/a1",
        parent_id=None,
        status="active",
        source_count=3,
        created_at=now,
        updated_at=now,
    )


async def _create_job(
    repo: InMemoryNotificationJobRepository,
    *,
    user_id: int = 1,
    priority: int = 0,
    announcement_id: str = "a1",
) -> tuple[NotificationJob, UserFilter, StructuredAnnouncement]:
    saved_filter = _filter(user_id=user_id)
    announcement = _announcement(announcement_id)
    repo.add_delivery_context(announcement=announcement, saved_filter=saved_filter)
    job, created = await repo.create_pending(
        user_id=user_id,
        filter_id=saved_filter.filter_id,
        announcement_id=announcement.announcement_id,
        priority=priority,
        queue_name="notifications.high_priority" if priority > 0 else "notifications.standard",
    )
    assert created
    return job, saved_filter, announcement


def _stored(repo: InMemoryNotificationJobRepository, job: NotificationJob) -> NotificationJob:
    return repo.jobs[job.idempotency_key]


@pytest.mark.asyncio
async def test_delivery_worker_sends_safe_canonical_message_and_audits_success() -> None:
    repo = InMemoryNotificationJobRepository()
    job, _saved_filter, _announcement = await _create_job(repo)
    telegram = FakeTelegram()
    worker = NotificationDeliveryWorker(repository=repo, telegram=telegram)

    result = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))

    stored = _stored(repo, job)
    assert result.metrics.sent == 1
    assert stored.status == "sent"
    assert stored.telegram_message_id == "m1"
    assert [attempt.result for attempt in repo.attempts] == ["sending", "sent"]
    message = str(telegram.sent[0]["text"])
    assert "Siz saqlagan filtringizga mos yangi e'lon topildi." in message
    assert "Narxi: oyiga 500 USD" in message
    assert "Yunusobod" in message
    assert "Xonalar: 2 ta" in message
    assert "Bu e'lon 3 ta kanalda uchradi." in message
    assert "Manba: https://example.test/listing/a1" in message
    assert "+998901112233" not in message
    markup = telegram.sent[0]["reply_markup"]
    assert isinstance(markup, dict)
    callback_value = markup["inline_keyboard"][0][0]["callback_data"]
    assert parse_notification_action_callback(callback_value)[0] == "open"
    assert parse_notification_action_callback(f"n:v1:read:{job.notification_id}") == (
        "read",
        job.notification_id,
    )


@pytest.mark.asyncio
async def test_delivery_worker_retries_telegram_429_without_marking_sent() -> None:
    repo = InMemoryNotificationJobRepository()
    job, _saved_filter, _announcement = await _create_job(repo)
    telegram = FakeTelegram([TelegramRateLimitError(retry_after_seconds=42)])
    now = datetime(2026, 8, 1, tzinfo=UTC)
    worker = NotificationDeliveryWorker(repository=repo, telegram=telegram)

    result = await worker.process_batch(now=now)

    stored = _stored(repo, job)
    assert result.metrics.retryable_failures == 1
    assert stored.status == "pending"
    assert stored.next_attempt_at == now + timedelta(seconds=42)
    assert repo.attempts[-1].result == "rate_limited"
    assert repo.attempts[-1].retryable is True


@pytest.mark.asyncio
async def test_delivery_worker_suppresses_blocked_user_without_infinite_retry() -> None:
    repo = InMemoryNotificationJobRepository()
    job, _saved_filter, _announcement = await _create_job(repo)
    telegram = FakeTelegram([TelegramUserBlockedError("blocked")])
    worker = NotificationDeliveryWorker(repository=repo, telegram=telegram)

    first = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))
    second = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))

    assert first.metrics.permanent_failures == 1
    assert second.metrics.picked == 0
    assert _stored(repo, job).status == "suppressed"
    assert len(telegram.sent) == 1


@pytest.mark.asyncio
async def test_delivery_worker_does_not_duplicate_already_sent_job() -> None:
    repo = InMemoryNotificationJobRepository()
    job, _saved_filter, _announcement = await _create_job(repo)
    telegram = FakeTelegram()
    worker = NotificationDeliveryWorker(repository=repo, telegram=telegram)

    first = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))
    second = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))

    assert first.metrics.sent == 1
    assert second.metrics.picked == 0
    assert len(telegram.sent) == 1
    assert _stored(repo, job).status == "sent"


@pytest.mark.asyncio
async def test_delivery_worker_processes_high_priority_before_standard() -> None:
    repo = InMemoryNotificationJobRepository()
    standard_job, _standard_filter, _standard_announcement = await _create_job(
        repo,
        user_id=1,
        announcement_id="standard",
    )
    premium_job, _premium_filter, _premium_announcement = await _create_job(
        repo,
        user_id=2,
        priority=10,
        announcement_id="premium",
    )
    telegram = FakeTelegram()
    worker = NotificationDeliveryWorker(repository=repo, telegram=telegram)

    result = await worker.process_batch(limit=2, now=datetime(2026, 8, 1, tzinfo=UTC))

    assert result.metrics.sent == 2
    assert telegram.sent[0]["chat_id"] == premium_job.user_id
    assert telegram.sent[1]["chat_id"] == standard_job.user_id


@pytest.mark.asyncio
async def test_delivery_worker_aggregates_bulk_retryable_failures_to_ops() -> None:
    repo = InMemoryNotificationJobRepository()
    await _create_job(repo, user_id=1, announcement_id="a1")
    await _create_job(repo, user_id=2, announcement_id="a2")
    await _create_job(repo, user_id=3, announcement_id="a3")
    telegram = FakeTelegram(
        [
            TelegramTransientError("temporary"),
            TelegramTransientError("temporary"),
            TelegramTransientError("temporary"),
        ]
    )
    ops = FakeOps()
    worker = NotificationDeliveryWorker(
        repository=repo,
        telegram=telegram,
        ops_notifier=ops,
        bulk_failure_threshold=3,
    )

    result = await worker.process_batch(limit=3, now=datetime(2026, 8, 1, tzinfo=UTC))

    assert result.metrics.retryable_failures == 3
    assert len(ops.calls) == 1
    assert "bulk failure count=3" in str(ops.calls[0]["reason"])


@pytest.mark.asyncio
async def test_delivery_worker_respects_local_user_rate_limit_even_for_premium() -> None:
    repo = InMemoryNotificationJobRepository()
    await _create_job(repo, user_id=1, priority=10, announcement_id="a1")
    await _create_job(
        repo,
        user_id=1,
        priority=10,
        announcement_id="a2",
    )
    telegram = FakeTelegram()
    worker = NotificationDeliveryWorker(
        repository=repo,
        telegram=telegram,
        rate_limiter=InMemoryNotificationRateLimiter(
            per_user_interval=timedelta(minutes=1),
        ),
    )

    result = await worker.process_batch(limit=2, now=datetime(2026, 8, 1, tzinfo=UTC))

    assert result.metrics.sent == 1
    assert result.metrics.rate_limited == 1
    assert len(telegram.sent) == 1
    assert sorted(job.status for job in repo.jobs.values()) == ["pending", "sent"]


def test_message_formatter_truncates_long_text_without_sensitive_child_source_fields() -> None:
    formatter = NotificationMessageFormatter()
    saved_filter = _filter()
    announcement = replace(_announcement(), canonical=_canonical(description="x" * 300))
    job = NotificationJob(
        notification_id="n1",
        user_id=1,
        filter_id=saved_filter.filter_id,
        announcement_id=announcement.announcement_id,
    )

    text = formatter.format_message(
        context=repo_context(job=job, announcement=announcement, saved_filter=saved_filter)
    )

    assert len(text) < 600
    assert announcement.source_id not in text
    assert "x" * 260 not in text


def repo_context(
    *,
    job: NotificationJob,
    announcement: StructuredAnnouncement,
    saved_filter: UserFilter,
) -> NotificationDeliveryContext:
    return NotificationDeliveryContext(
        job=job,
        announcement=announcement,
        filter_name=saved_filter.name,
    )
