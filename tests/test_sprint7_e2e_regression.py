from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.bot.callbacks import callback
from estateflow.bot.controller import BotController
from estateflow.bot.nlp_search import NlpSearchBotFlow
from estateflow.bot.search_wizard import SearchWizard
from estateflow.repositories.saved_filters import InMemorySavedFilterRepository
from estateflow.repositories.users import InMemoryUserRepository
from estateflow.services.ai_client import (
    LLMAllModelsFailedError,
    LLMAttemptAudit,
    LLMMalformedResponseError,
    LLMRequest,
    LLMResponse,
)
from estateflow.services.ai_worker import AiExtractionWorker, InMemoryAiProcessingRepository
from estateflow.services.audience_tags import AudienceTagService, InMemoryAudienceTagRepository
from estateflow.services.ingestion_pipeline import PreAiIngestionProcessor
from estateflow.services.listener_pool import (
    ChannelAssignment,
    InMemoryChannelAssignmentRepository,
    InMemoryListenerAccountRepository,
    ListenerAccountMetadata,
    ListenerAssignmentService,
)
from estateflow.services.media_buffer import TelegramAlbumBuffer
from estateflow.services.media_storage import (
    InMemoryObjectStorage,
    MediaProcessor,
    MediaStorageService,
    StaticMediaResolver,
)
from estateflow.services.nlp_search import NlpSearchExtractor
from estateflow.services.notifications import (
    HIGH_PRIORITY_NOTIFICATION_QUEUE,
    InMemoryNotificationJobRepository,
    InMemoryNotificationQueuePublisher,
    NotificationDeliveryWorker,
    NotificationMatchingEngine,
    NotificationUser,
    TelegramRateLimitError,
    TelegramSendResult,
)
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.post_ai_dedup import (
    InMemoryAnnouncementRepository,
    ManualReviewQueueService,
    PostAiDedupProcessor,
    StructuredAnnouncement,
    WeightedDeduplicationEngine,
)
from estateflow.services.pre_ai_dedup import (
    InMemoryPreAiDedupSignalStore,
    PreAiDedupConfig,
    PreAiDedupFilter,
    StableMediaReferencePHashProvider,
)
from estateflow.services.queue import QueueMessage, QueuePublishError
from estateflow.services.referrals import InMemoryReferralNotificationPublisher, ReferralService
from estateflow.services.saved_filters import SavedFilterService, UserFilter
from estateflow.services.search import InMemorySearchRepository, SearchCriteria, SearchService
from estateflow.services.source_config import InMemorySourceRegistry, SourceConfig
from estateflow.services.source_suggestions import (
    InMemorySourceSuggestionRepository,
    SourceSuggestionService,
)
from estateflow.services.telegram_listener import (
    RAW_EVENT_SCHEMA_VERSION,
    InMemoryIdempotencyStore,
    QueueRawTelegramEventPublisher,
    RawTelegramEvent,
    TelegramAccountListener,
    TelegramClientAdapter,
    TelegramFloodWaitError,
    TelegramMediaReference,
    TelegramUpdate,
)
from estateflow.services.users import UserService


class RecordingQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        if self.fail:
            raise QueuePublishError(message.queue_name, "temporary outage")
        self.messages.append(message)


class RecordingOps(DisabledOpsNotificationService):
    def __init__(self) -> None:
        self.messages: list[str] = []

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
        self.messages.append(f"{severity}:{reason}:{correlation_id}")
        return True


@dataclass
class FakeTelegramClient(TelegramClientAdapter):
    updates: list[TelegramUpdate] | None = None
    connect_error: Exception | None = None

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    async def subscribe(self, sources: list[SourceConfig]) -> None:
        return None

    def iter_updates(self) -> AsyncIterator[TelegramUpdate]:
        return self._iter_updates()

    async def _iter_updates(self) -> AsyncIterator[TelegramUpdate]:
        for update in self.updates or []:
            yield update

    async def disconnect(self) -> None:
        return None


class SequenceLLM:
    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
        self.outcomes = outcomes
        self.requests: list[LLMRequest] = []

    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(model="fake", content=outcome, confidence=0.95), []


class FakeTelegramDelivery:
    def __init__(self, *, fail_once_429: bool = False) -> None:
        self.fail_once_429 = fail_once_429
        self.sent: list[dict[str, object]] = []

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramSendResult:
        if self.fail_once_429:
            self.fail_once_429 = False
            raise TelegramRateLimitError(retry_after_seconds=30)
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return TelegramSendResult(message_id=f"tg-{len(self.sent)}")


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


@pytest.mark.asyncio
async def test_e2e_two_listener_album_to_dedup_single_parent_and_phone_only_new_listing() -> None:
    source_a = _source("source-a", "@estateflow_a")
    source_b = _source("source-b", "@estateflow_b")
    raw_queue = RecordingQueue()
    ai_queue = RecordingQueue()
    post_ai_queue = RecordingQueue()
    ops = RecordingOps()
    idempotency_store = InMemoryIdempotencyStore()
    buffer = TelegramAlbumBuffer(
        downstream=QueueRawTelegramEventPublisher(
            queue=raw_queue,
            idempotency_store=idempotency_store,
        ),
        debounce_seconds=30,
    )
    assignment_service = _assignment_service(ops=ops)

    await _listener(
        account_key="listener-a",
        client=FakeTelegramClient(
            updates=[
                _update(
                    "-1001",
                    "@estateflow_a",
                    "10",
                    "Yunusobod 2 xona 500$",
                    "album-1",
                    "photo-a",
                ),
                _update("-1001", "@estateflow_a", "11", None, "album-1", "photo-b"),
                _update("-1001", "@estateflow_a", "12", "Yunusobod 2 xona 500$", None, "photo-a"),
            ]
        ),
        sources=[source_a],
        event_publisher=buffer,
        idempotency_store=idempotency_store,
        assignment_service=assignment_service,
        ops=ops,
    ).run()
    await _listener(
        account_key="listener-b",
        client=FakeTelegramClient(
            updates=[
                _update(
                    "-1002",
                    "@estateflow_b",
                    "20",
                    "Sergeli 4 xona 1200$ synthetic phone same",
                    None,
                    "photo-phone",
                )
            ]
        ),
        sources=[source_b],
        event_publisher=buffer,
        idempotency_store=idempotency_store,
        assignment_service=assignment_service,
        ops=ops,
    ).run()
    await buffer.flush_all()

    signal_store = InMemoryPreAiDedupSignalStore()
    pre_ai = _pre_ai_processor(signal_store=signal_store, ai_queue=ai_queue, ops=ops)
    for message in raw_queue.messages:
        await pre_ai.process_raw_queue_message(message)

    llm = SequenceLLM(
        [
            _llm_listing(price=Decimal("500"), district="Yunusobod"),
            _llm_listing(price=Decimal("1200"), district="Sergeli", rooms=4),
        ]
    )
    ai_worker = _ai_worker(
        llm=llm,
        post_ai_queue=post_ai_queue,
    )
    announcement_repo = InMemoryAnnouncementRepository()
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=announcement_repo),
        announcement_repository=announcement_repo,
        manual_review_queue=ManualReviewQueueService(announcement_repo),
    )
    for message in ai_queue.messages:
        await ai_worker.process_raw_queue_message(message)
    for message in post_ai_queue.messages:
        await processor.process(_post_ai_announcement(message.payload))

    parents = announcement_repo.search_user_facing()
    audit_rows = announcement_repo.list_all_for_audit()

    assert len(raw_queue.messages) == 3
    assert len(ai_queue.messages) == 2, "exact duplicate must skip AI"
    assert len(llm.requests) == 2
    assert len(parents) == 2, "canonical duplicate parent plus phone-only new listing expected"
    assert len(audit_rows) == 2
    assert {item.canonical.district for item in parents} == {"Yunusobod", "Sergeli"}
    assert all("synthetic phone" not in message for message in ops.messages)


@pytest.mark.asyncio
async def test_e2e_bot_fsm_saved_filter_notification_delivery_is_retry_idempotent() -> None:
    user_repo = InMemoryUserRepository()
    announcement = _announcement("bot-a", district="Yunusobod", audience_tags=["family"])
    search_wizard = SearchWizard(SearchService(InMemorySearchRepository([announcement])))
    saved_repo = InMemorySavedFilterRepository()
    saved_service = SavedFilterService(saved_repo)
    controller = BotController(
        user_service=UserService(user_repo),
        search_wizard=search_wizard,
        saved_filter_service=saved_service,
    )

    await controller.start(user_id=100)
    await controller.handle_menu_callback(
        user_id=100,
        data=callback("wizard", "start", owner_id=100),
    )
    await controller.handle_text(user_id=100, text="Yunusobod")
    await controller.handle_text(user_id=100, text="2")
    await controller.handle_text(user_id=100, text="600")
    await controller.handle_menu_callback(
        user_id=100,
        data=callback("wizard", "basis_total", owner_id=100),
    )
    await controller.handle_menu_callback(
        user_id=100,
        data=callback("wizard", "renovation", owner_id=100, value="good"),
    )
    result_screen = await controller.handle_menu_callback(
        user_id=100,
        data=callback("wizard", "audience", owner_id=100, value="family"),
    )
    begin_save = await controller.handle_menu_callback(
        user_id=100,
        data=callback("filter", "save", owner_id=100),
    )
    saved = await controller.handle_text(user_id=100, text="Yunusobod family")

    matching_listing = _announcement("bot-new", district="Yunusobod", audience_tags=["family"])
    saved_filters = await saved_service.list_for_user(user_id=100)
    job_repo = InMemoryNotificationJobRepository()
    queue = InMemoryNotificationQueuePublisher()
    engine = NotificationMatchingEngine(
        filter_repository=SavedFilterMatchingAdapter(saved_filters),
        user_repository=NotificationUsersFromUserRepo(user_repo),
        job_repository=job_repo,
        queue_publisher=queue,
    )
    match = await engine.match_announcement(matching_listing)
    duplicate_match = await engine.match_announcement(matching_listing)
    job_repo.add_delivery_context(announcement=matching_listing, saved_filter=saved_filters[0])
    telegram = FakeTelegramDelivery()
    worker = NotificationDeliveryWorker(repository=job_repo, telegram=telegram)
    delivered = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))
    retry = await worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))

    assert "Yunusobod listing" in result_screen.text
    assert "filtr nomini yozing" in begin_save.text.casefold()
    assert "nomi: yunusobod family" in saved.text.casefold()
    assert match.metrics.jobs_created == 1
    assert duplicate_match.metrics.duplicate_jobs == 1
    assert delivered.metrics.sent == 1
    assert retry.metrics.picked == 0
    assert len(queue.jobs) == 1
    assert len(telegram.sent) == 1


@pytest.mark.asyncio
async def test_e2e_nlp_uz_ru_mixed_queries_and_failure_offer_wizard_fallback() -> None:
    announcements = [
        _announcement("nlp-uz", district="Yunusobod", audience_tags=["family"]),
        _announcement("nlp-ru", district="Chilonzor", rooms=1),
        _announcement(
            "nlp-mixed",
            district="Mirzo Ulugbek",
            rooms=3,
            audience_tags=["group_of_girls"],
        ),
    ]
    llm = SequenceLLM(
        [
            {
                "monthly_budget": "600",
                "price_basis_preference": "total",
                "districts": ["Yunusobod"],
                "rooms": 2,
                "audience_tag": "yosh oila",
                "confidence": 0.95,
            },
            {
                "monthly_budget": "600",
                "price_basis_preference": "total",
                "districts": ["Chilonzor"],
                "rooms": 1,
                "confidence": 0.95,
            },
            {
                "monthly_budget": "600",
                "price_basis_preference": "include_per_person",
                "districts": ["Mirzo Ulugbek"],
                "rooms": 3,
                "audience_tag": "group_of_girls",
                "unapplied_conditions": ["metroga yaqin"],
                "confidence": 0.95,
            },
        ]
    )
    wizard = SearchWizard(SearchService(InMemorySearchRepository(announcements)))
    flow = NlpSearchBotFlow(extractor=NlpSearchExtractor(llm_client=llm), search_wizard=wizard)

    flow.start(user_id=1)
    uz_summary = await flow.handle_text(user_id=1, text="Yunusobod 2 xona yosh oila")
    uz_result = await flow.handle_callback(user_id=1, action="confirm", value="")
    flow.start(user_id=2)
    ru_summary = await flow.handle_text(user_id=2, text="Чиланзар 1 комнатная до 400")
    ru_result = await flow.handle_callback(user_id=2, action="confirm", value="")
    flow.start(user_id=3)
    mixed_summary = await flow.handle_text(user_id=3, text="Mirzo Ulugbek 3 xona 4 ta qizga")
    mixed_result = await flow.handle_callback(user_id=3, action="confirm", value="")

    failure_flow = NlpSearchBotFlow(
        extractor=NlpSearchExtractor(
            llm_client=SequenceLLM([LLMMalformedResponseError("invalid json")])
        ),
        search_wizard=SearchWizard(SearchService(InMemorySearchRepository())),
    )
    failure_flow.start(user_id=4)
    fallback = await failure_flow.handle_text(user_id=4, text="bad model response")

    assert "Auditoriya: family" in uz_summary.text
    assert "Yunusobod listing" in uz_result.text
    assert "Chilonzor" in ru_summary.text
    assert "Chilonzor listing" in ru_result.text
    assert "metroga yaqin" in mixed_summary.text
    assert "Mirzo Ulugbek listing" in mixed_result.text
    assert "wizard" in fallback.text.casefold()


@pytest.mark.asyncio
async def test_e2e_referral_source_suggestion_tag_review_and_priority_notification() -> None:
    user_repo = InMemoryUserRepository()
    referral_notifier = InMemoryReferralNotificationPublisher()
    referral_service = ReferralService(
        repository=user_repo,
        notification_publisher=referral_notifier,
    )
    await referral_service.register_start(user_id=1)
    for user_id in range(10, 15):
        await referral_service.register_start(user_id=user_id, start_parameter="ef_1")
        await referral_service.record_qualifying_action(
            user_id=user_id,
            action="search",
            action_id=f"first-search-{user_id}",
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )

    source_repo = InMemorySourceSuggestionRepository()
    source_registry = InMemorySourceRegistry()
    source_service = SourceSuggestionService(source_repo, source_registry=source_registry)
    app = create_app(
        Settings(
            environment="test",
            admin_api_token=SecretStr("secret-admin"),
            source_suggestion_ingress_token=SecretStr("secret-source"),
            admin_actor_user_id=7,
        )
    )
    app.state.source_suggestion_service = source_service
    client = TestClient(app)
    submitted = client.post(
        "/internal/source-suggestions",
        json={"source_identifier": "https://t.me/estate_s7"},
        headers={
            "X-Telegram-User-Id": "1",
            "X-Internal-Token": "secret-source",
        },
    )
    unauthorized = client.post(
        f"/admin/source-suggestions/{submitted.json()['suggestion_id']}/approve",
        json={"idempotency_key": "approve-source-s7"},
    )
    approved = client.post(
        f"/admin/source-suggestions/{submitted.json()['suggestion_id']}/approve",
        json={"idempotency_key": "approve-source-s7"},
        headers={"X-Admin-Token": "secret-admin"},
    )

    tag_repo = InMemoryAudienceTagRepository()
    tag_service = AudienceTagService(tag_repo)
    pending = await tag_service.resolve_tag("military_personnel")
    tag = await tag_service.approve(
        tag_key=pending.tag_key,
        admin_user_id=7,
        idempotency_key="approve-tag-s7",
        display_name_uz="Harbiylar",
    )
    saved_filter = UserFilter(
        filter_id="00000000-0000-0000-0000-000000000077",
        user_id=1,
        name="Premium",
        criteria=SearchCriteria(district="Yunusobod", rooms=2, max_price=Decimal("600")),
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    queue = InMemoryNotificationQueuePublisher()
    job_repo = InMemoryNotificationJobRepository()
    engine = NotificationMatchingEngine(
        filter_repository=SavedFilterMatchingAdapter([saved_filter]),
        user_repository=NotificationUsersFromUserRepo(user_repo),
        job_repository=job_repo,
        queue_publisher=queue,
    )
    match = await engine.match_announcement(
        _announcement("premium-s7", district="Yunusobod"),
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert user_repo.users[1].active_referral_count == 5
    assert user_repo.users[1].premium_until is not None
    assert referral_notifier.messages[0]["priority"] == "high"
    assert unauthorized.status_code == 403
    assert approved.status_code == 200
    assert len(source_registry.sources) == 1
    assert tag.status == "approved"
    assert match.jobs[0].queue_name == HIGH_PRIORITY_NOTIFICATION_QUEUE
    assert queue.jobs[0].priority > 0


@pytest.mark.asyncio
async def test_e2e_failure_paths_are_observable_and_retry_safe() -> None:
    source = _source("source-failure", "@estateflow_failure")
    raw_queue = RecordingQueue(fail=True)
    ops = RecordingOps()
    idempotency_store = InMemoryIdempotencyStore()
    update = _update(
        "-1009",
        "@estateflow_failure",
        "90",
        "Retry after Redis outage",
        None,
        "photo-a",
    )
    listener = _listener(
        account_key="listener-a",
        client=FakeTelegramClient(updates=[update]),
        sources=[source],
        event_publisher=TelegramAlbumBuffer(
            downstream=QueueRawTelegramEventPublisher(
                queue=raw_queue,
                idempotency_store=idempotency_store,
            ),
            debounce_seconds=30,
        ),
        idempotency_store=idempotency_store,
        assignment_service=_assignment_service(ops=ops),
        ops=ops,
    )

    with pytest.raises(QueuePublishError):
        await listener.run()
    assert await idempotency_store.seen("telegram:-1009:90:created") is False
    raw_queue.fail = False
    await listener.handle_update(update)
    assert len(raw_queue.messages) == 1

    ai_repo = InMemoryAiProcessingRepository()
    post_ai_queue = RecordingQueue()
    storage = InMemoryObjectStorage()
    storage.fail_upload = True
    worker_storage_failure = _ai_worker(
        llm=SequenceLLM([_llm_listing(price=Decimal("500"), district="Yunusobod")]),
        post_ai_queue=post_ai_queue,
        repository=ai_repo,
        storage=storage,
        ops=ops,
    )
    storage_record = await worker_storage_failure.process_raw_queue_message(
        _ai_message(_raw_event("storage-fail", media_id="photo-a"))
    )
    worker_llm_failure = _ai_worker(
        llm=SequenceLLM(
            [
                LLMAllModelsFailedError(
                    [
                        LLMAttemptAudit(
                            model="fake",
                            fallback_stage=0,
                            latency_ms=1,
                            status="failed",
                            error_type="LLMTransportError",
                        )
                    ]
                )
            ]
        ),
        post_ai_queue=post_ai_queue,
        repository=ai_repo,
        ops=ops,
    )
    llm_record = await worker_llm_failure.process_raw_queue_message(
        _ai_message(_raw_event("llm-fail", media_id="photo-c"))
    )

    job_repo = InMemoryNotificationJobRepository()
    saved_filter = UserFilter(
        filter_id="00000000-0000-0000-0000-000000000099",
        user_id=50,
        name="Retry",
        criteria=SearchCriteria(),
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    job, _created = await job_repo.create_pending(
        user_id=50,
        filter_id=saved_filter.filter_id,
        announcement_id="tg-429",
        priority=0,
        queue_name="notifications.standard",
    )
    job_repo.add_delivery_context(
        announcement=_announcement("tg-429"),
        saved_filter=saved_filter,
    )
    telegram = FakeTelegramDelivery(fail_once_429=True)
    notification_worker = NotificationDeliveryWorker(repository=job_repo, telegram=telegram)
    first_delivery = await notification_worker.process_batch(now=datetime(2026, 8, 1, tzinfo=UTC))
    second_delivery_too_early = await notification_worker.process_batch(
        now=datetime(2026, 8, 1, 0, 0, 10, tzinfo=UTC)
    )
    third_delivery = await notification_worker.process_batch(
        now=datetime(2026, 8, 1, 0, 0, 31, tzinfo=UTC)
    )

    assignment_service = _assignment_service(
        ops=ops,
        account_repository=InMemoryListenerAccountRepository(
            [
                ListenerAccountMetadata(account_key="listener-a"),
                ListenerAccountMetadata(account_key="listener-b"),
            ]
        ),
        assignment_repository=InMemoryChannelAssignmentRepository(
            [ChannelAssignment(source_id="source-failure", account_key="listener-a")]
        ),
    )
    await _listener(
        account_key="listener-a",
        client=FakeTelegramClient(connect_error=TelegramFloodWaitError(30)),
        sources=[source],
        event_publisher=TelegramAlbumBuffer(
            downstream=QueueRawTelegramEventPublisher(
                queue=RecordingQueue(),
                idempotency_store=InMemoryIdempotencyStore(),
            ),
            debounce_seconds=30,
        ),
        idempotency_store=InMemoryIdempotencyStore(),
        assignment_service=assignment_service,
        ops=ops,
    ).run()
    assignments = await assignment_service.reconcile([source])

    announcement_repo = InMemoryAnnouncementRepository()
    dedup_processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=announcement_repo),
        announcement_repository=announcement_repo,
        manual_review_queue=ManualReviewQueueService(announcement_repo),
    )
    parent = _announcement("concurrent-parent", district="Yunusobod")
    child_one = _announcement("concurrent-child-1", district="Yunusobod")
    child_two = _announcement("concurrent-child-2", district="Yunusobod")
    await dedup_processor.process(parent)
    await asyncio.gather(dedup_processor.process(child_one), dedup_processor.process(child_two))
    parents = announcement_repo.search_user_facing()

    assert storage_record.status == "failed"
    assert storage_record.failure_reason == "storage_retryable"
    assert llm_record.status == "failed"
    assert llm_record.failure_reason == "all_models_failed"
    assert first_delivery.metrics.retryable_failures == 1
    assert second_delivery_too_early.metrics.picked == 0
    assert third_delivery.metrics.sent == 1
    assert len(telegram.sent) == 1
    assert job.notification_id in {attempt.notification_id for attempt in job_repo.attempts}
    assert {assignment.account_key for assignment in assignments} == {"listener-b"}
    assert len(parents) == 1
    assert len(announcement_repo.list_all_for_audit()) == 3
    assert "flood" in "\n".join(ops.messages).casefold()


def _source(source_id: str, identifier: str) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        name=source_id,
        source_type="telegram_channel",
        identifier=identifier,
    )


def _update(
    channel_id: str,
    identifier: str,
    message_id: str,
    text: str | None,
    media_group_id: str | None,
    media_id: str,
) -> TelegramUpdate:
    return TelegramUpdate(
        update_type="created",
        source_channel_id=channel_id,
        source_message_id=message_id,
        source_identifier=identifier,
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        text=text,
        media=[
            TelegramMediaReference(
                media_id=media_id,
                media_type="photo",
                mime_type="image/png",
                access_hash=media_id,
            )
        ],
        media_group_id=media_group_id,
    )


def _listener(
    *,
    account_key: str,
    client: FakeTelegramClient,
    sources: list[SourceConfig],
    event_publisher: TelegramAlbumBuffer,
    idempotency_store: InMemoryIdempotencyStore,
    assignment_service: ListenerAssignmentService,
    ops: RecordingOps,
) -> TelegramAccountListener:
    return TelegramAccountListener(
        account_key=account_key,
        client=client,
        sources=sources,
        event_publisher=event_publisher,
        idempotency_store=idempotency_store,
        assignment_service=assignment_service,
        ops_notifier=ops,
    )


def _assignment_service(
    *,
    ops: RecordingOps,
    account_repository: InMemoryListenerAccountRepository | None = None,
    assignment_repository: InMemoryChannelAssignmentRepository | None = None,
) -> ListenerAssignmentService:
    return ListenerAssignmentService(
        account_repository=account_repository
        or InMemoryListenerAccountRepository(
            [
                ListenerAccountMetadata(account_key="listener-a"),
                ListenerAccountMetadata(account_key="listener-b"),
            ]
        ),
        assignment_repository=assignment_repository or InMemoryChannelAssignmentRepository(),
        ops_notifier=ops,
    )


def _pre_ai_processor(
    *,
    signal_store: InMemoryPreAiDedupSignalStore,
    ai_queue: RecordingQueue,
    ops: RecordingOps,
) -> PreAiIngestionProcessor:
    phash_provider = StableMediaReferencePHashProvider()
    return PreAiIngestionProcessor(
        dedup_filter=PreAiDedupFilter(
            signal_store=signal_store,
            phash_provider=phash_provider,
            ops_notifier=ops,
            config=PreAiDedupConfig(near_text_threshold=0.70, phash_hamming_threshold=8),
        ),
        ai_queue=ai_queue,
        signal_store=signal_store,
        phash_provider=phash_provider,
    )


def _ai_worker(
    *,
    llm: SequenceLLM,
    post_ai_queue: RecordingQueue,
    repository: InMemoryAiProcessingRepository | None = None,
    storage: InMemoryObjectStorage | None = None,
    ops: RecordingOps | None = None,
) -> AiExtractionWorker:
    return AiExtractionWorker(
        llm_client=llm,  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver(
                {
                    "photo-a": _png(b"a"),
                    "photo-b": _png(b"b"),
                    "photo-c": _png(b"c"),
                    "photo-phone": _png(b"z"),
                }
            ),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=storage or InMemoryObjectStorage(),
        ),
        repository=repository or InMemoryAiProcessingRepository(),
        post_ai_queue=post_ai_queue,
        ops_notifier=ops or RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )


def _ai_message(event: RawTelegramEvent) -> QueueMessage:
    return QueueMessage(
        queue_name="ai.processing.raw_announcements",
        payload={
            **event.to_queue_payload(),
            "pre_ai_dedup": {"decision": "proceed_to_ai", "should_skip_ai": False},
        },
        correlation_id=event.correlation_id,
    )


def _raw_event(message_id: str, *, media_id: str) -> RawTelegramEvent:
    return RawTelegramEvent(
        schema_version=RAW_EVENT_SCHEMA_VERSION,
        event_type="created",
        idempotency_key=f"telegram:-1009:{message_id}:created",
        account_key="listener-a",
        source_id="source-failure",
        source_channel_id="-1009",
        source_message_id=message_id,
        source_identifier="@estateflow_failure",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        correlation_id=f"cid-{message_id}",
        text="Yunusobod 2 xona 500$",
        forward_metadata=None,
        media=[
            TelegramMediaReference(
                media_id=media_id,
                media_type="photo",
                mime_type="image/png",
                access_hash=media_id,
            )
        ],
        source_message_ids=[message_id],
    )


def _post_ai_announcement(payload: dict[str, Any]) -> StructuredAnnouncement:
    return StructuredAnnouncement.from_payload(payload)


def _announcement(
    announcement_id: str,
    *,
    district: str = "Yunusobod",
    rooms: int = 2,
    audience_tags: list[str] | None = None,
) -> StructuredAnnouncement:
    from estateflow.services.extraction import CanonicalListing

    now = datetime(2026, 8, 1, tzinfo=UTC)
    canonical = CanonicalListing(
        prompt_version="test",
        price=Decimal("500"),
        currency="USD",
        price_period="monthly",
        price_basis="total",
        price_normalized_monthly=Decimal("500"),
        listing_type="rent",
        rooms=rooms,
        area_sqm=Decimal("65"),
        floor=5,
        total_floors=None,
        district=district,
        address=f"{district} listing",
        phone_numbers=["+998900000000"],
        owner_type="unknown",
        renovation_level="good",
        renovation_source="vision",
        furniture=None,
        description=f"{district} listing",
        audience_tags=audience_tags or [],
        audience_excluded_tags=[],
        confidence=0.95,
        field_confidence={},
    )
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id="source-1",
        source_channel_id="-100",
        source_message_id=announcement_id,
        occurred_at=now,
        canonical=canonical,
        parent_id=None,
        status="active",
        source_count=1,
        source_url=f"https://example.test/{announcement_id}",
        created_at=now,
        updated_at=now,
    )


def _llm_listing(
    *,
    price: Decimal,
    district: str,
    rooms: int = 2,
) -> dict[str, Any]:
    return {
        "price": str(price),
        "currency": "USD",
        "price_period": "monthly",
        "price_basis": "total",
        "rooms": rooms,
        "area_sqm": "65",
        "floor": 5,
        "district": district,
        "address": f"{district} listing",
        "phone_numbers": ["+998900000000"],
        "renovation_level": "good",
        "renovation_source": "vision",
        "confidence": 0.95,
    }


def _png(fill: bytes) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + (16).to_bytes(4, "big")
        + (16).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
        + (fill * 64)
    )
