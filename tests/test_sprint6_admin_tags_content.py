from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.bot.callbacks import callback
from estateflow.bot.controller import BotController
from estateflow.repositories.users import InMemoryUserRepository
from estateflow.services.adapter_defaults import DEFAULT_TELEGRAM_ADAPTER_NAME
from estateflow.services.ai_client import LLMRequest, LLMResponse
from estateflow.services.ai_worker import AiExtractionWorker, InMemoryAiProcessingRepository
from estateflow.services.audience_tags import (
    SEED_AUDIENCE_TAGS,
    AudienceTagService,
    InMemoryAudienceTagRepository,
)
from estateflow.services.content_automation import (
    ContentAutomationService,
    ContentStats,
    ContentTopOffer,
    DryRunContentPublisher,
    InMemoryContentRepository,
)
from estateflow.services.extraction import (
    CanonicalListing,
    ListingExtractionRaw,
    build_listing_prompt,
    normalize_listing,
)
from estateflow.services.media_storage import (
    InMemoryObjectStorage,
    MediaProcessor,
    MediaStorageService,
    StaticMediaResolver,
)
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.post_ai_dedup import (
    InMemoryAnnouncementRepository,
    ManualReviewQueueService,
    StructuredAnnouncement,
    manual_review_decision,
)
from estateflow.services.queue import QueueMessage
from estateflow.services.search import InMemorySearchRepository, SearchCriteria
from estateflow.services.source_config import InMemorySourceRegistry
from estateflow.services.source_suggestions import (
    InMemorySourceSuggestionAdminNotifier,
    InMemorySourceSuggestionRepository,
    SourceSuggestionService,
    normalize_source_identifier,
)
from estateflow.services.telegram_listener import RawTelegramEvent
from estateflow.services.users import UserService


class RecordingQueue:
    def __init__(self) -> None:
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        self.messages.append(message)


class FakeLLMClient:
    def __init__(self, content: dict[str, object]) -> None:
        self.content = content
        self.requests: list[LLMRequest] = []

    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[object]]:
        self.requests.append(request)
        return LLMResponse(model="fake", content=self.content, confidence=0.95), []


@pytest.mark.asyncio
async def test_dynamic_audience_tags_are_flat_and_normalize_young_family_to_family() -> None:
    repo = InMemoryAudienceTagRepository()
    service = AudienceTagService(repo)

    family = await service.resolve_tag("young_family")
    military = await service.resolve_tag("military_personnel")
    prompt_tags = await service.prompt_tags(limit=3)

    assert family.tag_key == "family"
    assert family.status == "approved"
    assert military.tag_key == "military_personnel"
    assert military.status == "pending"
    assert [tag.tag_key for tag in await repo.list_pending()] == ["military_personnel"]
    assert "young_family" not in prompt_tags
    assert all(not hasattr(tag, "parent_tag_id") for tag in repo.tags.values())


@pytest.mark.asyncio
async def test_audience_prompt_seed_selection_is_usage_limited_and_flat() -> None:
    repo = InMemoryAudienceTagRepository()
    service = AudienceTagService(repo)
    for index in range(20):
        tag, _ = await repo.upsert_pending(
            tag_key=f"custom_tag_{index}",
            display_name_uz=f"Custom {index}",
        )
        await repo.set_status(tag_key=tag.tag_key, status="approved")
        for _ in range(index):
            await repo.increment_usage(tag_key=tag.tag_key)

    prompt_tags = await service.prompt_tags(limit=15)

    assert len(prompt_tags) == 15
    assert prompt_tags[0] == "custom_tag_19"
    assert "young_family" not in prompt_tags
    assert set(InMemoryAudienceTagRepository().tags) == set(SEED_AUDIENCE_TAGS)


@pytest.mark.asyncio
async def test_audience_admin_approve_merge_reject_are_idempotent_and_audited() -> None:
    repo = InMemoryAudienceTagRepository()
    service = AudienceTagService(repo)
    await service.resolve_tag("military_personnel")
    await service.resolve_tag("families")

    approved = await service.approve(
        tag_key="military_personnel",
        admin_user_id=7,
        idempotency_key="tag-approve-1",
        display_name_uz="Harbiy xodimlar",
    )
    replay = await service.approve(
        tag_key="military_personnel",
        admin_user_id=7,
        idempotency_key="tag-approve-1",
        display_name_uz="Changed",
    )
    merged = await service.merge(
        tag_key="families",
        target_tag_key="family",
        admin_user_id=7,
        idempotency_key="tag-merge-1",
    )

    assert approved.status == "approved"
    assert replay.display_name_uz == "Harbiy xodimlar"
    assert merged.status == "rejected"
    assert len(repo.audit_events) == 2


@pytest.mark.asyncio
async def test_audience_resolution_policy_exact_pending_rejected_and_synonym() -> None:
    repo = InMemoryAudienceTagRepository()
    service = AudienceTagService(repo)
    await service.resolve_tag("military_personnel")
    await service.reject(
        tag_key="military_personnel",
        admin_user_id=7,
        idempotency_key="reject-military",
    )

    resolved = await service.resolve_for_canonical(
        ["yosh oila", "students", "diplomats", "military_personnel"]
    )

    assert resolved["approved_tags"] == ["family", "students"]
    assert "pending_audience_tag:diplomats" in resolved["audit_assumptions"]
    assert "rejected_audience_tag:military_personnel" in resolved["audit_assumptions"]
    assert repo.tags["family"].usage_count == 1
    assert repo.tags["students"].usage_count == 1
    assert repo.tags["diplomats"].status == "pending"


@pytest.mark.asyncio
async def test_audience_merge_remaps_announcement_and_filter_references() -> None:
    repo = InMemoryAudienceTagRepository()
    service = AudienceTagService(repo)
    await service.resolve_tag("families")
    await service.approve(
        tag_key="families",
        admin_user_id=7,
        idempotency_key="approve-families",
    )
    repo.announcement_refs["a1"] = {"families"}
    repo.announcement_excluded_refs["a2"] = {"families"}
    repo.filter_refs["f1"] = "families"

    merged = await service.merge(
        tag_key="families",
        target_tag_key="family",
        admin_user_id=7,
        idempotency_key="merge-families",
        expected_status="approved",
    )
    replay = await service.merge(
        tag_key="families",
        target_tag_key="family",
        admin_user_id=7,
        idempotency_key="merge-families",
        expected_status="approved",
    )

    assert merged.status == "rejected"
    assert replay.status == "rejected"
    assert repo.announcement_refs["a1"] == {"family"}
    assert repo.announcement_excluded_refs["a2"] == {"family"}
    assert repo.filter_refs["f1"] == "family"
    assert len(repo.audit_events) == 2


@pytest.mark.asyncio
async def test_ai_worker_uses_limited_dynamic_tags_and_keeps_pending_out_of_canonical() -> None:
    tag_repo = InMemoryAudienceTagRepository()
    tag_service = AudienceTagService(tag_repo)
    for _ in range(3):
        await tag_repo.increment_usage(tag_key="students")
    llm = FakeLLMClient(
        {
            "price": 500,
            "currency": "USD",
            "price_period": "monthly",
            "price_basis": "total",
            "audience_tags": ["students", "diplomats"],
            "confidence": 0.95,
        }
    )
    queue = RecordingQueue()
    worker = AiExtractionWorker(
        llm_client=llm,  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({}),
            processor=MediaProcessor(max_bytes=128),
            storage=InMemoryObjectStorage(),
        ),
        repository=InMemoryAiProcessingRepository(),
        post_ai_queue=queue,
        ops_notifier=DisabledOpsNotificationService(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
        audience_tag_service=tag_service,
    )

    record = await worker.process_raw_queue_message(_message(_event()))

    request_text = "\n".join(message.content for message in llm.requests[0].messages)
    allowed_segment = request_text.split(". Map young_family to family.", maxsplit=1)[0]
    assert request_text.count("students") == 1
    assert "young_family" not in allowed_segment
    assert record.canonical is not None
    assert record.canonical.audience_tags == ["students"]
    assert "pending_audience_tag:diplomats" in record.canonical.assumptions
    assert tag_repo.tags["diplomats"].status == "pending"


@pytest.mark.asyncio
async def test_pending_audience_tags_do_not_change_user_filter_semantics() -> None:
    repo = InMemorySearchRepository(
        [
            _announcement("approved", audience_tags=["family"]),
            _announcement("pending-only", audience_tags=[]),
        ]
    )

    result = await repo.search(SearchCriteria(audience_tag="family"))

    assert [item.announcement_id for item in result.items] == ["approved"]


def test_extraction_prompt_uses_dynamic_flat_tags_and_keeps_amenities_unstructured() -> None:
    prompt = build_listing_prompt(
        raw_text="Yosh oilaga, kir mashina bor",
        vision_required=False,
        media_count=0,
        audience_tags=("family", "military_personnel"),
    )
    raw = ListingExtractionRaw(
        audience_tags=["young_family", "military_personnel"],
        confidence=0.9,
    )

    canonical = normalize_listing(
        raw,
        vision_required=False,
        allowed_audience_tags=frozenset({"family", "military_personnel"}),
        keep_pending_audience_tags=True,
    )

    assert "family, military_personnel" in prompt[0]["content"]
    assert "Never output parent_tag_id" in prompt[0]["content"]
    assert "amenities fields" in prompt[0]["content"]
    assert canonical.audience_tags == ["family", "military_personnel"]


@pytest.mark.asyncio
async def test_source_suggestion_submit_approve_is_idempotent_and_rewards_once() -> None:
    repo = InMemorySourceSuggestionRepository()
    registry = InMemorySourceRegistry()
    notifier = InMemorySourceSuggestionAdminNotifier()
    service = SourceSuggestionService(
        repo,
        source_registry=registry,
        admin_notifier=notifier,
    )
    now = datetime(2026, 8, 1, tzinfo=UTC)

    first = await service.submit(user_id=100, source_identifier="https://t.me/estate_test")
    replay = await service.submit(user_id=200, source_identifier="@estate_test")
    approved = await service.approve(
        suggestion_id=first.suggestion.suggestion_id,
        admin_user_id=1,
        idempotency_key="approve-source-1",
        note="active channel",
        now=now,
    )
    approved_again = await service.approve(
        suggestion_id=first.suggestion.suggestion_id,
        admin_user_id=1,
        idempotency_key="approve-source-1",
        note="replay",
        now=now + timedelta(days=1),
    )

    assert first.created is True
    assert replay.created is False
    assert len(notifier.notifications) == 1
    assert approved.suggestion.status == "approved"
    assert approved.suggestion.source_id == "telegram_channel:@estate_test"
    assert approved.source_created is True
    assert approved.source is not None
    assert approved.source.adapter_name == DEFAULT_TELEGRAM_ADAPTER_NAME
    assert approved.reward_until == now + timedelta(days=7)
    assert approved_again.reward_until is None
    assert repo.premium_until[100] == now + timedelta(days=7)
    assert len(await registry.list_active_sources()) == 1
    assert len(repo.audit_events) == 3


@pytest.mark.asyncio
async def test_rejected_source_suggestion_cannot_be_reopened_by_another_user() -> None:
    repo = InMemorySourceSuggestionRepository()
    service = SourceSuggestionService(repo)

    submitted = await service.submit(user_id=100, source_identifier="@estate_reopen")
    rejected = await service.reject(
        suggestion_id=submitted.suggestion.suggestion_id,
        admin_user_id=1,
        idempotency_key="reject-reopen-1",
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    replay_other_user = await service.submit(user_id=200, source_identifier="@estate_reopen")
    replay_same_user = await service.submit(user_id=100, source_identifier="@estate_reopen")

    assert rejected.suggestion.status == "rejected"
    assert replay_other_user.created is False
    assert replay_other_user.suggestion.status == "rejected"
    assert replay_same_user.created is True
    assert replay_same_user.suggestion.status == "pending"


@pytest.mark.asyncio
async def test_source_suggestion_reject_has_no_reward_or_source_creation() -> None:
    repo = InMemorySourceSuggestionRepository()
    registry = InMemorySourceRegistry()
    service = SourceSuggestionService(repo, source_registry=registry)

    submitted = await service.submit(user_id=100, source_identifier="@estate_reject")
    rejected = await service.reject(
        suggestion_id=submitted.suggestion.suggestion_id,
        admin_user_id=1,
        idempotency_key="reject-source-1",
        note="inactive",
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert rejected.suggestion.status == "rejected"
    assert repo.premium_until == {}
    assert await registry.list_active_sources() == []


@pytest.mark.asyncio
async def test_source_registry_creation_is_idempotent_across_review_retries() -> None:
    repo = InMemorySourceSuggestionRepository()
    registry = InMemorySourceRegistry()
    service = SourceSuggestionService(repo, source_registry=registry)
    submitted = await service.submit(user_id=100, source_identifier="@estate_source")

    first = await service.approve(
        suggestion_id=submitted.suggestion.suggestion_id,
        admin_user_id=1,
        idempotency_key="approve-source-1",
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    replay_same_key = await service.approve(
        suggestion_id=submitted.suggestion.suggestion_id,
        admin_user_id=1,
        idempotency_key="approve-source-1",
        now=datetime(2026, 8, 2, tzinfo=UTC),
    )
    with pytest.raises(ValueError):
        await service.approve(
            suggestion_id=submitted.suggestion.suggestion_id,
            admin_user_id=2,
            idempotency_key="approve-source-2",
            now=datetime(2026, 8, 3, tzinfo=UTC),
        )

    assert first.source_created is True
    assert replay_same_key.source is None
    assert len(await registry.list_active_sources()) == 1
    assert repo.premium_until[100] == datetime(2026, 8, 8, tzinfo=UTC)


@pytest.mark.asyncio
async def test_bot_source_suggestion_flow_submits_pending_and_validates_input() -> None:
    repo = InMemorySourceSuggestionRepository()
    service = SourceSuggestionService(repo)
    controller = BotController(
        user_service=UserService(InMemoryUserRepository()),
        source_suggestion_service=service,
    )
    user_id = 808

    await controller.start(user_id=user_id)
    ask = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("source", "start", owner_id=user_id),
    )
    malformed = await controller.handle_text(user_id=user_id, text="https://example.com/bad")
    ask_again = await controller.handle_menu_callback(
        user_id=user_id,
        data=callback("source", "start", owner_id=user_id),
    )
    submitted = await controller.handle_text(user_id=user_id, text="t.me/estate_bot_source")

    assert "username/linkini" in ask.text
    assert "Invalid" in malformed.text or "Telegram" in malformed.text
    assert "username/linkini" in ask_again.text
    assert "pending review" in submitted.text
    assert (await service.list_pending())[0].source_identifier == "@estate_bot_source"


@pytest.mark.asyncio
async def test_content_automation_caps_daily_posts_and_uses_dry_run_without_credentials() -> None:
    publisher = DryRunContentPublisher()
    repo = InMemoryContentRepository(
        stats=ContentStats(
            total_checked=120,
            duplicates_detected=18,
            active_listings=81,
            average_monthly_price=Decimal("512.40"),
        ),
        offers=(
            ContentTopOffer(
                announcement_id="a1",
                district="Yunusobod",
                rooms=2,
                price_normalized_monthly=Decimal("430"),
                currency="USD",
                source_url="https://t.me/source/1",
            ),
        ),
    )
    service = ContentAutomationService(repository=repo, publisher=publisher, daily_post_limit=2)

    first = await service.generate_daily(now=datetime(2026, 8, 1, 9, tzinfo=UTC))
    replay = await service.generate_daily(now=datetime(2026, 8, 1, 10, tzinfo=UTC))

    assert len(first) == 2
    assert len(replay) == 2
    assert len(repo.posts) == 2
    assert all(post.status == "dry_run" for post in repo.posts.values())
    assert len(publisher.messages) == 2


def test_admin_api_requires_token_and_exposes_pending_items() -> None:
    source_repo = InMemorySourceSuggestionRepository()
    source_service = SourceSuggestionService(source_repo)
    tag_repo = InMemoryAudienceTagRepository()
    tag_service = AudienceTagService(tag_repo)
    app = create_app(
        Settings(
            environment="test",
            admin_api_token=SecretStr("secret-admin"),
            source_suggestion_ingress_token=SecretStr("secret-source"),
        )
    )
    app.state.source_suggestion_service = source_service
    app.state.audience_tag_service = tag_service
    client = TestClient(app)

    submitted = client.post(
        "/internal/source-suggestions",
        json={"source_identifier": "t.me/estate_test"},
        headers={
            "X-Telegram-User-Id": "10",
            "X-Internal-Token": "secret-source",
        },
    )
    body_reject = client.post(
        "/internal/source-suggestions",
        json={"source_identifier": "t.me/estate_test", "user_id": 99},
        headers={"X-Telegram-User-Id": "10", "X-Internal-Token": "secret-source"},
    )
    unauthorized = client.get("/admin/source-suggestions/pending")
    spoofed_approve = client.post(
        f"/admin/source-suggestions/{submitted.json()['suggestion_id']}/approve",
        json={"admin_user_id": 99, "idempotency_key": "api-approve-1"},
        headers={"X-Admin-Token": "secret-admin"},
    )
    unauthorized_approve = client.post(
        f"/admin/source-suggestions/{submitted.json()['suggestion_id']}/approve",
        json={"idempotency_key": "api-approve-1"},
    )
    authorized = client.get(
        "/admin/source-suggestions/pending",
        headers={"X-Admin-Token": "secret-admin"},
    )
    invalid_internal = client.post(
        "/internal/source-suggestions",
        json={"source_identifier": "t.me/estate_test"},
        headers={"X-Telegram-User-Id": "10", "X-Internal-Token": "wrong"},
    )
    missing_internal = client.post(
        "/internal/source-suggestions",
        json={"source_identifier": "t.me/estate_test"},
        headers={"X-Telegram-User-Id": "10"},
    )

    assert submitted.status_code == 200
    assert body_reject.status_code == 422
    assert unauthorized.status_code == 403
    assert spoofed_approve.status_code == 422
    assert unauthorized_approve.status_code == 403
    assert invalid_internal.status_code == 403
    assert missing_internal.status_code == 403
    assert authorized.status_code == 200
    assert authorized.json()["items"][0]["source_identifier"] == "@estate_test"
    assert authorized.json()["metadata"]["total"] == 1


def test_admin_api_paginates_and_filters_queues_with_empty_state() -> None:
    source_repo = InMemorySourceSuggestionRepository()
    source_service = SourceSuggestionService(source_repo)
    tag_repo = InMemoryAudienceTagRepository()
    tag_service = AudienceTagService(tag_repo)
    review_queue = ManualReviewQueueService(InMemoryAnnouncementRepository())
    app = create_app(Settings(environment="test", admin_api_token=SecretStr("secret-admin")))
    app.state.source_suggestion_service = source_service
    app.state.audience_tag_service = tag_service
    app.state.manual_review_queue = review_queue
    client = TestClient(app)

    empty = client.get(
        "/admin/manual-review/pending",
        headers={"X-Admin-Token": "secret-admin"},
    )

    assert empty.status_code == 200
    assert empty.json()["metadata"]["empty"] is True
    assert empty.json()["items"] == []


@pytest.mark.asyncio
async def test_admin_manual_review_actions_are_authorized_preconditioned_and_audited() -> None:
    parent = _announcement("parent", audience_tags=["family"])
    child = _announcement("child", audience_tags=["family"])
    repository = InMemoryAnnouncementRepository([parent, child])
    review_queue = ManualReviewQueueService(repository)
    item = await review_queue.enqueue(
        announcement=child,
        reason="possible_duplicate",
        decision=manual_review_decision(reason="possible_duplicate", candidate_id="parent"),
    )
    app = create_app(Settings(environment="test", admin_api_token=SecretStr("secret-admin")))
    app.state.manual_review_queue = review_queue
    client = TestClient(app)
    body = {
        "action": "merge_duplicate",
        "idempotency_key": "review-merge-1",
        "expected_status": "pending",
        "note": "same listing",
    }

    unauthorized = client.post(f"/admin/manual-review/{item.review_id}/actions", json=body)
    detail = client.get(
        f"/admin/manual-review/{item.review_id}",
        headers={"X-Admin-Token": "secret-admin"},
    )
    merged = client.post(
        f"/admin/manual-review/{item.review_id}/actions",
        json=body,
        headers={"X-Admin-Token": "secret-admin"},
    )
    replay = client.post(
        f"/admin/manual-review/{item.review_id}/actions",
        json=body,
        headers={"X-Admin-Token": "secret-admin"},
    )
    conflict = client.post(
        f"/admin/manual-review/{item.review_id}/actions",
        json={**body, "idempotency_key": "review-merge-2"},
        headers={"X-Admin-Token": "secret-admin"},
    )

    assert unauthorized.status_code == 403
    assert detail.status_code == 200
    assert detail.json()["listing_summary"]["description"] == "Yunusobod listing"
    assert detail.json()["listing_summary"]["announcement_id"] == "child"
    assert detail.json()["candidate_listing_summary"]["description"] == "Yunusobod listing"
    assert detail.json()["candidate_listing_summary"]["announcement_id"] == "parent"
    assert merged.status_code == 200
    assert merged.json()["status"] == "merged"
    assert replay.status_code == 200
    assert conflict.status_code == 409
    linked = await repository.get("child")
    assert linked is not None
    assert linked.parent_id == "parent"
    assert review_queue.action_audit["review-merge-1"]["admin_user_id"] == 1
    assert review_queue.action_audit["review-merge-1"]["note"] == "same listing"


def test_sprint6_migration_preserves_flat_tags_and_adds_audit_content_tables() -> None:
    sql = Path("migrations/008_sprint6_admin_tags_content.sql").read_text(encoding="utf-8").lower()

    assert "admin_audit_events" in sql
    assert "content_channel_posts" in sql
    assert "district_stats" in sql
    assert "transparency_report" in sql
    assert "'failed'" in sql
    assert "next_attempt_at" in sql
    assert "parent_tag_id" not in sql
    assert "drop table" not in sql
    assert "drop column" not in sql


def test_source_identifier_normalization() -> None:
    assert normalize_source_identifier("https://t.me/estate_test") == "@estate_test"
    assert normalize_source_identifier("@estate_test") == "@estate_test"
    with pytest.raises(ValueError):
        normalize_source_identifier("https://example.com/not-telegram")


def _event() -> RawTelegramEvent:
    return RawTelegramEvent(
        schema_version="telegram.raw.v1",
        event_type="created",
        idempotency_key="telegram:-1001:10:created",
        account_key="listener-a",
        source_id="source-a",
        source_channel_id="-1001",
        source_message_id="10",
        source_identifier="@a",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        correlation_id="cid-1",
        text="Yunusobod 2 xona 500$ talabalarga",
        forward_metadata=None,
        media=[],
        source_message_ids=["10"],
    )


def _message(event: RawTelegramEvent) -> QueueMessage:
    return QueueMessage(
        queue_name="ai.processing.raw_announcements",
        payload={
            **event.to_queue_payload(),
            "pre_ai_dedup": {"decision": "proceed_to_ai", "should_skip_ai": False},
        },
        correlation_id=event.correlation_id,
    )


def _canonical(*, audience_tags: list[str]) -> CanonicalListing:
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
        renovation_level=None,
        renovation_source="unknown",
        furniture=None,
        description="Yunusobod listing",
        audience_tags=audience_tags,
        audience_excluded_tags=[],
        confidence=0.9,
        field_confidence={},
    )


def _announcement(announcement_id: str, *, audience_tags: list[str]) -> StructuredAnnouncement:
    created = datetime(2026, 8, 1, tzinfo=UTC)
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id="source-1",
        source_channel_id="-100",
        source_message_id=announcement_id,
        occurred_at=created,
        canonical=_canonical(audience_tags=audience_tags),
        parent_id=None,
        status="active",
        source_count=1,
        source_url=f"https://example.test/{announcement_id}",
        created_at=created,
        updated_at=created,
    )
