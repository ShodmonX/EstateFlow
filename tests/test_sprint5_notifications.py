from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from estateflow.services.extraction import CanonicalListing
from estateflow.services.notifications import (
    HIGH_PRIORITY_NOTIFICATION_QUEUE,
    STANDARD_NOTIFICATION_QUEUE,
    InMemoryNotificationJobRepository,
    InMemoryNotificationQueuePublisher,
    NotificationMatchingEngine,
    NotificationUser,
)
from estateflow.services.post_ai_dedup import AnnouncementStatus, StructuredAnnouncement
from estateflow.services.saved_filters import UserFilter
from estateflow.services.search import SearchCriteria


class MemoryFilters:
    def __init__(self, filters: list[UserFilter]) -> None:
        self.filters = filters

    async def list_for_matching(self) -> list[UserFilter]:
        return self.filters


class MemoryUsers:
    def __init__(self, users: list[NotificationUser]) -> None:
        self.users = {item.user_id: item for item in users}

    async def get_notification_user(self, *, user_id: int) -> NotificationUser | None:
        return self.users.get(user_id)


def _filter(
    *,
    user_id: int = 1,
    criteria: SearchCriteria | None = None,
    enabled: bool = True,
) -> UserFilter:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return UserFilter(
        filter_id=str(uuid4()),
        user_id=user_id,
        name="filter",
        criteria=criteria
        or SearchCriteria(district="Yunusobod", rooms=2, max_price=Decimal("600")),
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


def _canonical(
    *,
    audience_tags: list[str] | None = None,
    audience_excluded_tags: list[str] | None = None,
) -> CanonicalListing:
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
        phone_numbers=[],
        owner_type="unknown",
        renovation_level="good",
        renovation_source="vision",
        furniture=None,
        description="listing",
        audience_tags=audience_tags or [],
        audience_excluded_tags=audience_excluded_tags or [],
        confidence=0.9,
        field_confidence={},
    )


def _announcement(
    announcement_id: str = "a1",
    *,
    parent_id: str | None = None,
    status: AnnouncementStatus = "active",
    canonical: CanonicalListing | None = None,
) -> StructuredAnnouncement:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id="source-1",
        source_channel_id="-100",
        source_message_id=announcement_id,
        occurred_at=now,
        canonical=canonical or _canonical(),
        parent_id=parent_id,
        status=status,
        source_count=1,
        created_at=now,
        updated_at=now,
    )


def _engine(
    *,
    filters: list[UserFilter],
    users: list[NotificationUser] | None = None,
    jobs: InMemoryNotificationJobRepository | None = None,
    publisher: InMemoryNotificationQueuePublisher | None = None,
) -> tuple[
    NotificationMatchingEngine,
    InMemoryNotificationJobRepository,
    InMemoryNotificationQueuePublisher,
]:
    job_repo = jobs or InMemoryNotificationJobRepository()
    queue = publisher or InMemoryNotificationQueuePublisher()
    engine = NotificationMatchingEngine(
        filter_repository=MemoryFilters(filters),
        user_repository=MemoryUsers(users or [NotificationUser(user_id=1)]),
        job_repository=job_repo,
        queue_publisher=queue,
    )
    return engine, job_repo, queue


@pytest.mark.asyncio
async def test_notification_matching_creates_standard_job_for_matching_filter() -> None:
    engine, _jobs, queue = _engine(filters=[_filter()])

    result = await engine.match_announcement(_announcement())

    assert len(result.jobs) == 1
    assert result.jobs[0].queue_name == STANDARD_NOTIFICATION_QUEUE
    assert result.metrics.jobs_created == 1
    assert queue.jobs == list(result.jobs)


@pytest.mark.asyncio
async def test_notification_matching_uses_search_semantics_for_audience_exclusion() -> None:
    accepted = _filter(criteria=SearchCriteria(audience_tag="family"))
    engine, _jobs, _queue = _engine(filters=[accepted])

    result = await engine.match_announcement(
        _announcement(
            canonical=_canonical(
                audience_tags=["family"],
                audience_excluded_tags=["family"],
            )
        )
    )

    assert result.jobs == ()
    assert result.metrics.skipped_no_match == 1


@pytest.mark.asyncio
async def test_notification_matching_skips_disabled_filter_and_no_match() -> None:
    disabled = _filter(enabled=False)
    no_match = _filter(criteria=SearchCriteria(district="Chilonzor"))
    engine, _jobs, _queue = _engine(filters=[disabled, no_match])

    result = await engine.match_announcement(_announcement())

    assert result.jobs == ()
    assert result.metrics.skipped_disabled_filters == 1
    assert result.metrics.skipped_no_match == 1


@pytest.mark.asyncio
async def test_notification_matching_skips_child_and_inactive_listings() -> None:
    engine, _jobs, _queue = _engine(filters=[_filter()])

    child = await engine.match_announcement(_announcement(parent_id="parent"))
    manual_review = await engine.match_announcement(_announcement(status="manual_review"))

    assert child.jobs == ()
    assert child.metrics.skipped_non_canonical == 1
    assert manual_review.jobs == ()
    assert manual_review.metrics.skipped_non_canonical == 1


@pytest.mark.asyncio
async def test_notification_matching_is_idempotent_across_retries_and_child_updates() -> None:
    filters = [_filter()]
    job_repo = InMemoryNotificationJobRepository()
    queue = InMemoryNotificationQueuePublisher()
    engine, _jobs, _queue = _engine(filters=filters, jobs=job_repo, publisher=queue)
    announcement = _announcement("parent")

    first = await engine.match_announcement(announcement)
    retry = await engine.match_announcement(announcement)
    child = await engine.match_announcement(replace(announcement, parent_id="parent"))

    assert first.metrics.jobs_created == 1
    assert retry.metrics.duplicate_jobs == 1
    assert child.jobs == ()
    assert len(queue.jobs) == 1
    assert len(job_repo.jobs) == 1


@pytest.mark.asyncio
async def test_notification_matching_routes_premium_user_to_high_priority_queue() -> None:
    premium_until = datetime.now(UTC) + timedelta(days=1)
    engine, _jobs, queue = _engine(
        filters=[_filter(user_id=2)],
        users=[NotificationUser(user_id=2, premium_until=premium_until)],
    )

    result = await engine.match_announcement(_announcement())

    assert result.jobs[0].queue_name == HIGH_PRIORITY_NOTIFICATION_QUEUE
    assert result.jobs[0].priority > 0
    assert queue.jobs[0].queue_name == HIGH_PRIORITY_NOTIFICATION_QUEUE


@pytest.mark.asyncio
async def test_notification_matching_skips_inactive_or_notifications_disabled_user() -> None:
    filters = [_filter(user_id=1), _filter(user_id=2)]
    engine, _jobs, _queue = _engine(
        filters=filters,
        users=[
            NotificationUser(user_id=1, status="disabled"),
            NotificationUser(user_id=2, notifications_enabled=False),
        ],
    )

    result = await engine.match_announcement(_announcement())

    assert result.jobs == ()
    assert result.metrics.skipped_inactive_user == 1
    assert result.metrics.skipped_notifications_disabled == 1
