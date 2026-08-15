from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from estateflow.bot.search_wizard import SearchWizard
from estateflow.repositories.saved_filters import InMemorySavedFilterRepository
from estateflow.repositories.users import InMemoryUserRepository
from estateflow.services.referrals import (
    InMemoryReferralNotificationPublisher,
    InMemoryReferralRateLimiter,
    ReferralService,
    parse_referral_start_parameter,
)
from estateflow.services.saved_filters import SavedFilterService
from estateflow.services.search import InMemorySearchRepository, SearchCriteria, SearchService


@pytest.mark.asyncio
async def test_referral_deep_link_and_start_parameter_validation() -> None:
    repo = InMemoryUserRepository()
    service = ReferralService(repository=repo)
    await repo.upsert_user(user_id=100)

    link = await service.deep_link(bot_username="@EstateFlowBot", user_id=100)

    assert link == "https://t.me/EstateFlowBot?start=ef_100"
    assert parse_referral_start_parameter("ef_100") == "ef_100"
    with pytest.raises(ValueError):
        parse_referral_start_parameter("bad code with spaces")
    with pytest.raises(ValueError):
        await service.deep_link(bot_username="bad-bot!", user_id=100)


@pytest.mark.asyncio
async def test_referral_start_rejects_self_unknown_duplicate_and_immutable_referrer() -> None:
    repo = InMemoryUserRepository()
    service = ReferralService(repository=repo)
    await repo.upsert_user(user_id=1)
    await repo.upsert_user(user_id=2)
    await repo.upsert_user(user_id=3)

    self_referral = await service.register_start(user_id=1, start_parameter="ef_1")
    unknown = await service.register_start(user_id=2, start_parameter="ef_999")
    first = await service.register_start(user_id=2, start_parameter="ef_1")
    replay = await service.register_start(user_id=2, start_parameter="ef_1")
    other_referrer = await service.register_start(user_id=2, start_parameter="ef_3")

    assert self_referral.skipped_reason == "self_referral"
    assert unknown.skipped_reason == "unknown_code"
    assert first.created is True
    assert replay.skipped_reason == "duplicate_referral"
    assert other_referrer.skipped_reason == "referrer_immutable"
    assert repo.users[2].referred_by == 1
    assert len(repo.referral_events) == 1


@pytest.mark.asyncio
async def test_referral_start_rejects_circular_referral() -> None:
    repo = InMemoryUserRepository()
    service = ReferralService(repository=repo)
    await repo.upsert_user(user_id=1)
    await repo.upsert_user(user_id=2)
    created = await service.register_start(user_id=2, start_parameter="ef_1")

    circular = await service.register_start(user_id=1, start_parameter="ef_2")

    assert created.created is True
    assert circular.skipped_reason == "circular_referral"


@pytest.mark.asyncio
async def test_referral_start_rate_limits_repeated_attempts_and_audits() -> None:
    repo = InMemoryUserRepository()
    service = ReferralService(
        repository=repo,
        rate_limiter=InMemoryReferralRateLimiter(max_attempts=1),
    )
    await repo.upsert_user(user_id=1)

    first = await service.register_start(user_id=2, start_parameter="ef_404")
    second = await service.register_start(user_id=2, start_parameter="ef_404")

    assert first.skipped_reason == "unknown_code"
    assert second.skipped_reason == "rate_limited"
    assert repo.audit_events[-1]["reason"] == "rate_limited"


@pytest.mark.asyncio
async def test_referral_activation_is_concurrent_exactly_once() -> None:
    repo = InMemoryUserRepository()
    service = ReferralService(repository=repo)
    await repo.upsert_user(user_id=1)
    await service.register_start(user_id=2, start_parameter="ef_1")
    now = datetime(2026, 8, 1, tzinfo=UTC)

    results = await asyncio.gather(
        service.record_qualifying_action(
            user_id=2,
            action="search",
            action_id="same-search",
            now=now,
        ),
        service.record_qualifying_action(
            user_id=2,
            action="search",
            action_id="same-search",
            now=now,
        ),
    )

    assert sum(1 for result in results if result.activated) == 1
    assert repo.users[1].active_referral_count == 1
    assert repo.referral_events[2].status == "active"


@pytest.mark.asyncio
async def test_five_active_referrals_award_premium_once_and_notify_high_priority() -> None:
    repo = InMemoryUserRepository()
    notifier = InMemoryReferralNotificationPublisher()
    service = ReferralService(repository=repo, notification_publisher=notifier)
    await repo.upsert_user(user_id=1)
    now = datetime(2026, 8, 1, tzinfo=UTC)

    for user_id in range(10, 15):
        await service.register_start(user_id=user_id, start_parameter="ef_1")
        await service.record_qualifying_action(
            user_id=user_id,
            action="search",
            action_id=f"search-{user_id}",
            now=now,
        )
    retry = await service.record_qualifying_action(
        user_id=14,
        action="search",
        action_id="search-14",
        now=now,
    )

    assert repo.users[1].active_referral_count == 5
    assert repo.users[1].premium_until == now + timedelta(days=7)
    assert retry.activated is False
    assert retry.skipped_reason == "already_active"
    assert len(notifier.messages) == 1
    assert notifier.messages[0]["priority"] == "high"


@pytest.mark.asyncio
async def test_existing_premium_is_extended_from_current_premium_until() -> None:
    repo = InMemoryUserRepository()
    notifier = InMemoryReferralNotificationPublisher()
    service = ReferralService(repository=repo, notification_publisher=notifier)
    referrer = await repo.upsert_user(user_id=1)
    now = datetime(2026, 8, 1, tzinfo=UTC)
    existing_until = now + timedelta(days=3)
    repo.users[1] = referrer.__class__(
        user_id=referrer.user_id,
        referral_code=referrer.referral_code,
        premium_until=existing_until,
        active_referral_count=referrer.active_referral_count,
        status=referrer.status,
        created_at=referrer.created_at,
        updated_at=referrer.updated_at,
    )

    for user_id in range(20, 25):
        await service.register_start(user_id=user_id, start_parameter="ef_1")
        await service.record_qualifying_action(
            user_id=user_id,
            action="saved_filter",
            action_id=f"filter-{user_id}",
            now=now,
        )

    assert repo.users[1].premium_until == existing_until + timedelta(days=7)
    assert notifier.messages[0]["premium_until"] == existing_until + timedelta(days=7)


@pytest.mark.asyncio
async def test_search_and_saved_filter_actions_activate_referral_hooks() -> None:
    repo = InMemoryUserRepository()
    service = ReferralService(repository=repo)
    await repo.upsert_user(user_id=1)
    await service.register_start(user_id=2, start_parameter="ef_1")

    wizard = SearchWizard(
        SearchService(InMemorySearchRepository()),
        activation_recorder=service,
    )
    wizard.start(user_id=2)
    await wizard.run(user_id=2)

    assert repo.users[1].active_referral_count == 1

    await service.register_start(user_id=3, start_parameter="ef_1")
    saved_filters = SavedFilterService(
        repository=InMemorySavedFilterRepository(),
        activation_recorder=service,
    )
    await saved_filters.create(user_id=3, name="Hook", criteria=SearchCriteria())

    assert repo.users[1].active_referral_count == 2
