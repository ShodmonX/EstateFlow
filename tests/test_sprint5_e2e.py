from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from estateflow.bot.callbacks import callback
from estateflow.bot.controller import BotController
from estateflow.bot.nlp_search import NlpSearchBotFlow
from estateflow.bot.search_wizard import SearchWizard
from estateflow.repositories.saved_filters import InMemorySavedFilterRepository
from estateflow.repositories.users import InMemoryUserRepository
from estateflow.services.ai_client import LLMMalformedResponseError, LLMRequest, LLMResponse
from estateflow.services.extraction import CanonicalListing
from estateflow.services.nlp_search import NlpSearchExtractor
from estateflow.services.notifications import (
    HIGH_PRIORITY_NOTIFICATION_QUEUE,
    InMemoryNotificationJobRepository,
    InMemoryNotificationQueuePublisher,
    NotificationDeliveryWorker,
    NotificationMatchingEngine,
    NotificationUser,
    TelegramSendResult,
)
from estateflow.services.post_ai_dedup import (
    InMemoryAnnouncementRepository,
    StructuredAnnouncement,
)
from estateflow.services.referrals import (
    InMemoryReferralNotificationPublisher,
    ReferralService,
)
from estateflow.services.saved_filters import SavedFilterService, UserFilter
from estateflow.services.search import InMemorySearchRepository, SearchCriteria, SearchService
from estateflow.services.users import UserService


class SavedFilterMatchingAdapter:
    def __init__(self, filters: list[UserFilter]) -> None:
        self.filters = filters

    async def list_for_matching(self) -> list[UserFilter]:
        return self.filters


class NotificationUsersFromUserRepo:
    def __init__(self, repo: InMemoryUserRepository) -> None:
        self._repo = repo

    async def get_notification_user(self, *, user_id: int) -> NotificationUser | None:
        user = await self._repo.get_user(user_id=user_id)
        if user is None:
            return None
        return NotificationUser(
            user_id=user.user_id,
            premium_until=user.premium_until,
            status=user.status,
        )


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramSendResult:
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return TelegramSendResult(message_id=f"tg-{len(self.sent)}")


class FakeNlpLlm:
    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
        self.outcomes = outcomes
        self.requests: list[LLMRequest] = []

    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(model="fake", content=outcome, confidence=0.95), []


@pytest.mark.asyncio
async def test_e2e_saved_filter_canonical_match_delivery_is_retry_idempotent() -> None:
    user_repo = InMemoryUserRepository()
    await UserService(user_repo).register_or_touch(telegram_user_id=100)
    saved_repo = InMemorySavedFilterRepository()
    saved_service = SavedFilterService(saved_repo)
    saved_filter = await saved_service.create(
        user_id=100,
        name="Yunusobod oilaviy",
        criteria=SearchCriteria(
            district="Yunusobod",
            rooms=2,
            max_price=Decimal("600"),
            audience_tag="family",
        ),
    )
    announcement_repo = InMemoryAnnouncementRepository()
    canonical = await announcement_repo.save_parent(_announcement("a100"))

    job_repo = InMemoryNotificationJobRepository()
    queue = InMemoryNotificationQueuePublisher()
    engine = NotificationMatchingEngine(
        filter_repository=SavedFilterMatchingAdapter([saved_filter]),
        user_repository=NotificationUsersFromUserRepo(user_repo),
        job_repository=job_repo,
        queue_publisher=queue,
    )

    match = await engine.match_announcement(canonical)
    duplicate_match = await engine.match_announcement(canonical)
    job_repo.add_delivery_context(announcement=canonical, saved_filter=saved_filter)
    telegram = FakeTelegram()
    worker = NotificationDeliveryWorker(repository=job_repo, telegram=telegram)
    delivered = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))
    retry = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))

    assert match.metrics.jobs_created == 1
    assert duplicate_match.metrics.duplicate_jobs == 1
    assert len(queue.jobs) == 1
    assert delivered.metrics.sent == 1
    assert retry.metrics.picked == 0
    assert len(telegram.sent) == 1
    assert "+998901112233" not in str(telegram.sent[0]["text"])


@pytest.mark.asyncio
async def test_e2e_referral_premium_makes_next_matching_notification_high_priority() -> None:
    user_repo = InMemoryUserRepository()
    referral_notifications = InMemoryReferralNotificationPublisher()
    referral_service = ReferralService(
        repository=user_repo,
        notification_publisher=referral_notifications,
    )
    await referral_service.register_start(user_id=1)
    now = datetime(2026, 8, 1, tzinfo=UTC)
    started_at = datetime.now(UTC)

    for user_id in range(10, 15):
        await referral_service.register_start(user_id=user_id, start_parameter="ef_1")
        wizard = SearchWizard(
            SearchService(InMemorySearchRepository()),
            activation_recorder=referral_service,
        )
        wizard.start(user_id=user_id)
        await wizard.run(user_id=user_id)

    saved_filter = UserFilter(
        filter_id="00000000-0000-0000-0000-000000000001",
        user_id=1,
        name="Premium filter",
        criteria=SearchCriteria(district="Yunusobod", rooms=2, max_price=Decimal("600")),
        created_at=now,
        updated_at=now,
    )
    job_repo = InMemoryNotificationJobRepository()
    queue = InMemoryNotificationQueuePublisher()
    engine = NotificationMatchingEngine(
        filter_repository=SavedFilterMatchingAdapter([saved_filter]),
        user_repository=NotificationUsersFromUserRepo(user_repo),
        job_repository=job_repo,
        queue_publisher=queue,
    )

    match = await engine.match_announcement(_announcement("premium-a"))

    assert user_repo.users[1].active_referral_count == 5
    assert user_repo.users[1].premium_until is not None
    assert user_repo.users[1].premium_until >= started_at + timedelta(days=7)
    assert len(referral_notifications.messages) == 1
    assert match.jobs[0].queue_name == HIGH_PRIORITY_NOTIFICATION_QUEUE
    assert queue.jobs[0].priority > 0


@pytest.mark.asyncio
async def test_e2e_nlp_confirm_uses_search_service_and_failure_offers_wizard() -> None:
    announcement = _announcement("nlp-a")
    search_wizard = SearchWizard(SearchService(InMemorySearchRepository([announcement])))
    flow = NlpSearchBotFlow(
        extractor=NlpSearchExtractor(
            llm_client=FakeNlpLlm(
                [
                    {
                        "monthly_budget": "600",
                        "price_basis_preference": "total",
                        "districts": ["Yunusobod"],
                        "rooms": 2,
                        "renovation_level": "good",
                        "audience_tag": "yosh oila",
                        "unapplied_conditions": ["metroga yaqin"],
                        "confidence": 0.92,
                    }
                ]
            )
        ),
        search_wizard=search_wizard,
    )
    controller = BotController(
        user_service=UserService(InMemoryUserRepository()),
        search_wizard=search_wizard,
        nlp_search_flow=flow,
    )
    await controller.start(user_id=77)

    await controller.handle_menu_callback(user_id=77, data=callback("nlp", "start", owner_id=77))
    summary = await controller.handle_text(user_id=77, text="Yunusobod 2 xona yosh oila")
    result = await controller.handle_menu_callback(
        user_id=77,
        data=callback("nlp", "confirm", owner_id=77),
    )

    failure_flow = NlpSearchBotFlow(
        extractor=NlpSearchExtractor(
            llm_client=FakeNlpLlm([LLMMalformedResponseError("invalid json")])
        ),
        search_wizard=SearchWizard(SearchService(InMemorySearchRepository())),
    )
    failure_flow.start(user_id=78)
    fallback = await failure_flow.handle_text(user_id=78, text="Chilonzor 1 xona")

    assert "Auditoriya: family" in summary.text
    assert "metroga yaqin" in summary.text
    assert "Yunusobod listing" in result.text
    assert search_wizard.current_criteria(user_id=77).audience_tag == "family"
    assert "wizard" in fallback.text.casefold()


def _canonical() -> CanonicalListing:
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
        description="Yunusobod listing. Tel +998901112233",
        audience_tags=["family"],
        audience_excluded_tags=[],
        confidence=0.9,
        field_confidence={},
    )


def _announcement(announcement_id: str) -> StructuredAnnouncement:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id="source-1",
        source_channel_id="-100",
        source_message_id=announcement_id,
        occurred_at=now,
        canonical=_canonical(),
        parent_id=None,
        status="active",
        source_count=2,
        source_url=f"https://example.test/{announcement_id}",
        created_at=now,
        updated_at=now,
    )
