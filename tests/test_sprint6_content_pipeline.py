from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from estateflow.services.content_automation import (
    CONTENT_AUTOMATION_QUEUE,
    ContentAutomationService,
    ContentDistrictStats,
    ContentPublishError,
    ContentScheduleService,
    ContentStats,
    ContentTopOffer,
    DryRunContentPublisher,
    InMemoryContentRepository,
    PublishResult,
    TelegramContentPublisher,
    rank_top_offers,
)
from estateflow.services.queue import QueueMessage


class RecordingQueue:
    def __init__(self) -> None:
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        self.messages.append(message)


class RecordingPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publish(self, *, text: str, idempotency_key: str) -> PublishResult:
        self.messages.append((idempotency_key, text))
        return PublishResult(status="published", message_id=f"telegram:{len(self.messages)}")


class FailingThenSuccessPublisher:
    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, *, text: str, idempotency_key: str) -> PublishResult:
        self.calls += 1
        if self.calls == 1:
            raise ContentPublishError("temporary_timeout", retryable=True)
        return PublishResult(status="published", message_id="telegram:retry-ok")


def test_rank_top_offers_filters_unreliable_prices_and_is_explainable() -> None:
    ranked = rank_top_offers(
        (
            _offer("unknown", price=None, currency="USD"),
            _offer("zero", price=Decimal("0"), currency="USD"),
            _offer("best-tie", price=Decimal("350"), currency="USD", source_count=3),
            _offer("cheaper-tie", price=Decimal("350"), currency="USD", source_count=1),
            _offer("unsupported-currency", price=Decimal("1"), currency="EUR"),
        ),
        limit=2,
    )

    assert [offer.announcement_id for offer in ranked] == ["best-tie", "cheaper-tie"]


@pytest.mark.asyncio
async def test_dry_run_preview_works_without_telegram_credentials() -> None:
    fallback = DryRunContentPublisher()
    publisher = TelegramContentPublisher(
        bot_token=None,
        chat_id=None,
        dry_run_fallback=fallback,
    )
    result = await publisher.publish(text="preview", idempotency_key="preview-1")

    assert result.status == "dry_run"
    assert fallback.messages == [("preview-1", "preview")]


@pytest.mark.asyncio
async def test_one_time_scheduled_publish_is_idempotent() -> None:
    queue = RecordingQueue()
    schedule = ContentScheduleService(queue=queue)
    publisher = RecordingPublisher()
    repo = _repo_with_content()
    service = ContentAutomationService(repository=repo, publisher=publisher)

    messages = await schedule.enqueue_daily(
        now=datetime(2026, 8, 1, 4, tzinfo=UTC),
        kinds=("top_offer",),
    )
    first = await service.process_scheduled_message(messages[0])
    replay = await service.process_scheduled_message(messages[0])

    assert queue.messages[0].queue_name == CONTENT_AUTOMATION_QUEUE
    assert first is not None
    assert replay is not None
    assert first.post_id == replay.post_id
    assert len(repo.posts) == 1
    assert publisher.messages == [("2026-08-01:top_offer", first.text)]


@pytest.mark.asyncio
async def test_daily_cap_blocks_extra_scheduled_items() -> None:
    publisher = RecordingPublisher()
    repo = _repo_with_content()
    service = ContentAutomationService(
        repository=repo,
        publisher=publisher,
        daily_post_limit=2,
    )

    top = await service.process_scheduled_message(_message("top_offer"))
    district = await service.process_scheduled_message(_message("district_stats"))
    transparency = await service.process_scheduled_message(_message("transparency_report"))

    assert top is not None
    assert district is not None
    assert transparency is None
    assert set(repo.posts) == {"2026-08-01:top_offer", "2026-08-01:district_stats"}
    assert len(publisher.messages) == 2


@pytest.mark.asyncio
async def test_failure_retry_is_deduped_until_backoff_then_publishes_once() -> None:
    publisher = FailingThenSuccessPublisher()
    repo = _repo_with_content()
    service = ContentAutomationService(
        repository=repo,
        publisher=publisher,
        retry_backoff=timedelta(minutes=10),
    )
    first_now = datetime(2026, 8, 1, 8, tzinfo=UTC)

    failed = await service.process_scheduled_message(_message("top_offer"), now=first_now)
    too_early = await service.process_scheduled_message(
        _message("top_offer"),
        now=first_now + timedelta(minutes=5),
    )
    retried = await service.process_scheduled_message(
        _message("top_offer"),
        now=first_now + timedelta(minutes=11),
    )

    assert failed is not None
    assert failed.status == "failed"
    assert too_early is not None
    assert too_early.status == "failed"
    assert retried is not None
    assert retried.status == "published"
    assert publisher.calls == 2
    assert repo.posts["2026-08-01:top_offer"].attempts == 2


@pytest.mark.asyncio
async def test_generate_daily_uses_active_canonical_content_and_respects_cap() -> None:
    publisher = RecordingPublisher()
    repo = _repo_with_content()
    service = ContentAutomationService(
        repository=repo,
        publisher=publisher,
        daily_post_limit=2,
    )

    posts = await service.generate_daily(now=datetime(2026, 8, 1, 9, tzinfo=UTC))

    assert [post.kind for post in posts] == ["top_offer", "district_stats"]
    assert len(publisher.messages) == 2
    assert all("active canonical" in text for _, text in publisher.messages)


def _repo_with_content() -> InMemoryContentRepository:
    return InMemoryContentRepository(
        stats=ContentStats(
            total_checked=90,
            duplicates_detected=11,
            active_listings=64,
            average_monthly_price=Decimal("510.5"),
        ),
        offers=(
            _offer(
                "a1",
                price=Decimal("420"),
                currency="USD",
                source_url="https://t.me/estate/101",
            ),
        ),
        district_stats=(
            ContentDistrictStats(
                district="Yunusobod",
                listing_count=8,
                average_monthly_price=Decimal("500"),
                min_monthly_price=Decimal("350"),
                max_monthly_price=Decimal("700"),
            ),
        ),
    )


def _offer(
    announcement_id: str,
    *,
    price: Decimal | None,
    currency: str | None,
    source_count: int = 1,
    source_url: str | None = None,
) -> ContentTopOffer:
    return ContentTopOffer(
        announcement_id=announcement_id,
        district="Yunusobod",
        rooms=2,
        price_normalized_monthly=price,
        currency=currency,
        source_count=source_count,
        source_url=source_url,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def _message(kind: str) -> QueueMessage:
    return QueueMessage(
        queue_name=CONTENT_AUTOMATION_QUEUE,
        payload={
            "schema_version": "estateflow.content.schedule.v1",
            "kind": kind,
            "day": "2026-08-01",
            "idempotency_key": f"2026-08-01:{kind}",
            "timezone": "Asia/Tashkent",
        },
        correlation_id=f"content:2026-08-01:{kind}",
        metadata={"idempotency_key": f"2026-08-01:{kind}", "kind": kind},
    )
