from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.services.ai_client import LLMRequest, LLMResponse
from estateflow.services.ai_worker import AiExtractionWorker, InMemoryAiProcessingRepository
from estateflow.services.audience_tags import AudienceTagService, InMemoryAudienceTagRepository
from estateflow.services.content_automation import (
    ContentAutomationService,
    ContentDistrictStats,
    ContentStats,
    ContentTopOffer,
    InMemoryContentRepository,
    PublishResult,
)
from estateflow.services.extraction import CanonicalListing
from estateflow.services.media_storage import (
    InMemoryObjectStorage,
    MediaProcessor,
    MediaStorageService,
    StaticMediaResolver,
)
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.post_ai_dedup import (
    InMemoryAnnouncementRepository,
    ManualReviewItem,
    ManualReviewQueueService,
    StructuredAnnouncement,
    manual_review_decision,
)
from estateflow.services.queue import QueueMessage
from estateflow.services.search import InMemorySearchRepository, SearchCriteria
from estateflow.services.source_config import InMemorySourceRegistry
from estateflow.services.source_suggestions import (
    InMemorySourceSuggestionRepository,
    SourceSuggestionService,
)
from estateflow.services.telegram_listener import RawTelegramEvent


class RecordingPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publish(self, *, text: str, idempotency_key: str) -> PublishResult:
        self.messages.append((idempotency_key, text))
        return PublishResult(status="published", message_id=f"fake-channel:{len(self.messages)}")


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


def test_source_suggestion_admin_approval_adds_one_source_and_rewards_once() -> None:
    source_repo = InMemorySourceSuggestionRepository()
    source_registry = InMemorySourceRegistry()
    source_service = SourceSuggestionService(
        source_repo,
        source_registry=source_registry,
    )
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
        json={"source_identifier": "https://t.me/estate_beta"},
        headers={
            "X-Telegram-User-Id": "100",
            "X-Internal-Token": "secret-source",
        },
    )
    unauthorized = client.post(
        f"/admin/source-suggestions/{submitted.json()['suggestion_id']}/approve",
        json={"idempotency_key": "source-approve-1"},
    )
    approved = client.post(
        f"/admin/source-suggestions/{submitted.json()['suggestion_id']}/approve",
        json={"idempotency_key": "source-approve-1"},
        headers={"X-Admin-Token": "secret-admin"},
    )
    replay = client.post(
        f"/admin/source-suggestions/{submitted.json()['suggestion_id']}/approve",
        json={"idempotency_key": "source-approve-1"},
        headers={"X-Admin-Token": "secret-admin"},
    )

    assert unauthorized.status_code == 403
    assert approved.status_code == 200
    assert replay.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["source_id"] == "telegram_channel:@estate_beta"
    assert len(source_registry.sources) == 1
    assert datetime.now(UTC) < source_repo.premium_until[100]
    assert source_repo.premium_until[100] <= datetime.now(UTC) + timedelta(days=7, seconds=1)
    assert len(source_repo.premium_until) == 1
    approve_events = [
        event for event in source_repo.audit_events.values() if event.action == "approve"
    ]
    assert len(approve_events) == 1


@pytest.mark.asyncio
async def test_dynamic_tag_lifecycle_keeps_search_on_approved_canonical_tags() -> None:
    tag_repo = InMemoryAudienceTagRepository()
    tag_service = AudienceTagService(tag_repo)
    queue = RecordingQueue()
    worker = _ai_worker(
        content={
            "price": 500,
            "currency": "USD",
            "price_period": "monthly",
            "price_basis": "total",
            "district": "Yunusobod",
            "audience_tags": ["young_family", "military_personnel"],
            "confidence": 0.95,
        },
        audience_tag_service=tag_service,
        queue=queue,
    )

    first_record = await worker.process_raw_queue_message(_message(_event("ai-1")))
    approved = await tag_service.approve(
        tag_key="military_personnel",
        admin_user_id=7,
        idempotency_key="tag-approve-military",
        display_name_uz="Harbiy xodimlar",
    )
    company_tag = await tag_service.resolve_tag("company_staff")
    tag_repo.announcement_refs["legacy-announcement"] = {"company_staff"}
    tag_repo.filter_refs["legacy-filter"] = "company_staff"
    merged = await tag_service.merge(
        tag_key=company_tag.tag_key,
        target_tag_key="students",
        admin_user_id=7,
        idempotency_key="tag-merge-company",
    )
    await tag_service.resolve_tag("vip_clients")
    rejected = await tag_service.reject(
        tag_key="vip_clients",
        admin_user_id=7,
        idempotency_key="tag-reject-vip",
        note="not a housing audience segment",
    )

    second_worker = _ai_worker(
        content={
            "price": 500,
            "currency": "USD",
            "price_period": "monthly",
            "price_basis": "total",
            "district": "Yunusobod",
            "audience_tags": ["military_personnel", "vip_clients"],
            "confidence": 0.95,
        },
        audience_tag_service=tag_service,
        queue=RecordingQueue(),
    )
    second_record = await second_worker.process_raw_queue_message(_message(_event("ai-2")))
    first_canonical = first_record.canonical
    second_canonical = second_record.canonical
    assert first_canonical is not None
    assert second_canonical is not None
    search_repo = InMemorySearchRepository(
        [
            _announcement("family", audience_tags=first_canonical.audience_tags),
            _announcement("military", audience_tags=second_canonical.audience_tags),
            _announcement("students", audience_tags=["students"]),
            _announcement("pending-only", audience_tags=[]),
        ]
    )

    family_result = await search_repo.search(SearchCriteria(audience_tag="family"))
    military_result = await search_repo.search(SearchCriteria(audience_tag="military_personnel"))
    students_result = await search_repo.search(SearchCriteria(audience_tag="students"))
    rejected_result = await search_repo.search(SearchCriteria(audience_tag="vip_clients"))

    assert first_canonical.audience_tags == ["family"]
    assert "pending_audience_tag:military_personnel" in first_canonical.assumptions
    assert approved.status == "approved"
    assert merged.status == "rejected"
    assert rejected.status == "rejected"
    assert tag_repo.filter_refs["legacy-filter"] == "students"
    assert tag_repo.announcement_refs["legacy-announcement"] == {"students"}
    assert second_canonical.audience_tags == ["military_personnel"]
    assert "rejected_audience_tag:vip_clients" in second_canonical.assumptions
    assert [item.announcement_id for item in family_result.items] == ["family"]
    assert [item.announcement_id for item in military_result.items] == ["military"]
    assert [item.announcement_id for item in students_result.items] == ["students"]
    assert rejected_result.items == ()


@pytest.mark.asyncio
async def test_manual_review_admin_action_uses_domain_service_and_rejects_non_admin() -> None:
    parent = _announcement("parent", audience_tags=["family"])
    child = _announcement("child", audience_tags=["family"])
    repository = InMemoryAnnouncementRepository([parent, child])
    review_queue = ManualReviewQueueService(repository)
    app = create_app(
        Settings(
            environment="test",
            admin_api_token=SecretStr("secret-admin"),
            admin_actor_user_id=7,
        )
    )
    app.state.manual_review_queue = review_queue
    client = TestClient(app)
    item = await _enqueue_review(review_queue, child, parent.announcement_id)
    body = {
        "action": "merge_duplicate",
        "idempotency_key": "manual-review-merge-1",
        "expected_status": "pending",
        "note": "same apartment",
    }

    unauthorized = client.post(f"/admin/manual-review/{item.review_id}/actions", json=body)
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

    linked = await repository.get("child")
    assert unauthorized.status_code == 403
    assert merged.status_code == 200
    assert replay.status_code == 200
    assert merged.json()["status"] == "merged"
    assert linked is not None
    assert linked.parent_id == "parent"
    assert repository.merge_audit[0]["merge_policy"] == "manual_review"
    assert review_queue.action_audit["manual-review-merge-1"]["admin_user_id"] == 7


@pytest.mark.asyncio
async def test_content_preview_publish_duplicate_cron_and_daily_cap_with_fake_channel() -> None:
    publisher = RecordingPublisher()
    repo = InMemoryContentRepository(
        stats=ContentStats(
            total_checked=100,
            duplicates_detected=12,
            active_listings=70,
            average_monthly_price=Decimal("500"),
        ),
        offers=(
            ContentTopOffer(
                announcement_id="a1",
                district="Yunusobod",
                rooms=2,
                price_normalized_monthly=Decimal("420"),
                currency="USD",
                source_url="https://t.me/estate/1",
            ),
        ),
        district_stats=(
            ContentDistrictStats(
                district="Yunusobod",
                listing_count=5,
                average_monthly_price=Decimal("500"),
                min_monthly_price=Decimal("420"),
                max_monthly_price=Decimal("650"),
            ),
        ),
    )
    service = ContentAutomationService(
        repository=repo,
        publisher=publisher,
        daily_post_limit=2,
    )

    preview = await service.preview_daily(now=datetime(2026, 8, 1, 8, tzinfo=UTC))
    assert [post.status for post in preview] == ["drafted", "drafted"]
    assert repo.posts == {}

    published = await service.generate_daily(now=datetime(2026, 8, 1, 9, tzinfo=UTC))
    duplicate_cron = await service.generate_daily(now=datetime(2026, 8, 1, 10, tzinfo=UTC))

    assert [post.kind for post in published] == ["top_offer", "district_stats"]
    assert [post.kind for post in duplicate_cron] == ["top_offer", "district_stats"]
    assert len(repo.posts) == 2
    assert len(publisher.messages) == 2
    assert "2026-08-01:transparency_report" not in repo.posts


async def _enqueue_review(
    review_queue: ManualReviewQueueService,
    child: StructuredAnnouncement,
    parent_id: str,
) -> ManualReviewItem:
    return await review_queue.enqueue(
        announcement=child,
        reason="possible_duplicate",
        decision=manual_review_decision(reason="possible_duplicate", candidate_id=parent_id),
    )


def _ai_worker(
    *,
    content: dict[str, object],
    audience_tag_service: AudienceTagService,
    queue: RecordingQueue,
) -> AiExtractionWorker:
    return AiExtractionWorker(
        llm_client=FakeLLMClient(content),  # type: ignore[arg-type]
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
        audience_tag_service=audience_tag_service,
    )


def _event(message_id: str) -> RawTelegramEvent:
    return RawTelegramEvent(
        schema_version="telegram.raw.v1",
        event_type="created",
        idempotency_key=f"telegram:-1001:{message_id}:created",
        account_key="listener-a",
        source_id="source-a",
        source_channel_id="-1001",
        source_message_id=message_id,
        source_identifier="@a",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        correlation_id=f"cid-{message_id}",
        text="Yunusobod 2 xona 500$",
        forward_metadata=None,
        media=[],
        source_message_ids=[message_id],
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
