from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from estateflow.application.core.config import Settings
from estateflow.repositories.post_ai_dedup import (
    ADMIN_AUDIT_ANNOUNCEMENTS_QUERY,
    DEDUP_CANDIDATE_QUERY,
    PARENT_CHILD_LINK_LOCKING_QUERY,
    USER_FACING_CANONICAL_SEARCH_QUERY,
)
from estateflow.services.extraction import (
    CanonicalListing,
    ListingExtractionRaw,
    PriceBasis,
    PricePeriod,
    normalize_listing,
)
from estateflow.services.media_storage import StoredMedia
from estateflow.services.post_ai_dedup import (
    DEFAULT_SIGNAL_MATCHERS,
    DedupDecision,
    DedupScoringConfig,
    InMemoryAnnouncementRepository,
    ManualReviewAdminService,
    ManualReviewQueueService,
    ParentSelectionPolicy,
    PostAiDedupProcessor,
    StructuredAnnouncement,
    WeightedDeduplicationEngine,
    dedup_scoring_config_from_settings,
    manual_review_decision,
)
from estateflow.services.source_parsing import fanout_candidate_source_message_id


def _canonical(
    *,
    price: Decimal | None = Decimal("500"),
    price_period: PricePeriod = "monthly",
    price_basis: PriceBasis = "total",
    district: str | None = "Yunusobod",
    rooms: int | None = 2,
    area: Decimal | None = Decimal("65"),
    floor: int | None = 5,
    address: str | None = "Yunusobod 9 kvartal",
    phone: str | None = "+998901234567",
    confidence: float = 0.9,
) -> CanonicalListing:
    return normalize_listing(
        ListingExtractionRaw(
            price=price,
            currency="USD" if price is not None else None,
            price_period=price_period,
            price_basis=price_basis,
            district=district,
            rooms=rooms,
            area_sqm=area,
            floor=floor,
            address=address,
            phone_numbers=[] if phone is None else [phone],
            confidence=confidence,
        ),
        source_text="" if phone is None else phone,
        vision_required=False,
    )


def _media(media_id: str = "m1", phash: str = "0000000000000000") -> StoredMedia:
    return StoredMedia(
        media_id=media_id,
        storage_url=f"memory://{media_id}",
        object_key=f"announcements/{media_id}.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        phash=phash,
        content_sha256=f"sha-{media_id}",
    )


def _announcement(
    announcement_id: str,
    *,
    canonical: CanonicalListing | None = None,
    media: tuple[StoredMedia, ...] = (),
    source_url: str | None = None,
    source_id: str = "source-a",
    source_message_id: str | None = None,
    source_message_ids: tuple[str, ...] | None = None,
    forward_origin_key: str | None = None,
    created_at: datetime | None = None,
) -> StructuredAnnouncement:
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id=source_id,
        source_channel_id="-100",
        source_message_id=source_message_id or announcement_id,
        source_message_ids=source_message_ids or (source_message_id or announcement_id,),
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        canonical=canonical or _canonical(),
        media=media,
        source_url=source_url,
        forward_origin_key=forward_origin_key,
        created_at=created_at or datetime(2026, 8, 1, tzinfo=UTC),
    )


async def _decision(
    incoming: StructuredAnnouncement,
    existing: list[StructuredAnnouncement],
) -> DedupDecision:
    repository = InMemoryAnnouncementRepository(existing)
    engine = WeightedDeduplicationEngine(candidate_repository=repository)
    return await engine.evaluate(incoming)


class RecordingCandidateRepository:
    def __init__(self, candidates: list[StructuredAnnouncement]) -> None:
        self.candidates = candidates
        self.seen_limit: int | None = None

    async def list_candidates(
        self,
        announcement: StructuredAnnouncement,
        *,
        limit: int = 50,
    ) -> list[StructuredAnnouncement]:
        self.seen_limit = limit
        return self.candidates[:limit]


@pytest.mark.asyncio
async def test_same_phone_different_apartments_stays_new() -> None:
    existing = _announcement(
        "old",
        canonical=_canonical(
            price=Decimal("300"),
            district="Chilonzor",
            rooms=1,
            area=Decimal("35"),
            floor=2,
            address="Chilonzor 1 kvartal",
        ),
    )
    incoming = _announcement(
        "new",
        canonical=_canonical(
            price=Decimal("1200"),
            district="Sergeli",
            rooms=4,
            area=Decimal("100"),
            floor=9,
            address="Sergeli 7 massiv",
        ),
    )

    decision = await _decision(incoming, [existing])

    assert decision.score == 15
    assert decision.decision == "new"
    assert [signal.name for signal in decision.breakdown if signal.matched] == ["phone"]


@pytest.mark.asyncio
async def test_same_image_plus_phone_is_high_confidence_duplicate() -> None:
    existing = _announcement(
        "old",
        canonical=_canonical(
            price=Decimal("300"),
            district="Sergeli",
            rooms=1,
            area=Decimal("35"),
            floor=2,
            address="Chilonzor 1 kvartal",
        ),
        media=(_media("old", "0000000000000000"),),
    )
    incoming = _announcement(
        "new",
        canonical=_canonical(
            price=Decimal("1200"),
            district="Sergeli",
            rooms=4,
            area=Decimal("100"),
            floor=9,
            address="Sergeli 7 massiv",
        ),
        media=(_media("new", "0000000000000001"),),
    )

    decision = await _decision(incoming, [existing])

    assert decision.score >= 80
    assert decision.decision == "high_confidence_duplicate"


@pytest.mark.asyncio
async def test_exact_source_url_is_exact_duplicate() -> None:
    existing = _announcement("old", source_url="https://example.test/listing/1")
    incoming = _announcement("new", source_url="https://example.test/listing/1")

    decision = await _decision(incoming, [existing])

    assert decision.decision == "exact_duplicate"
    assert decision.score >= 100


def test_post_ai_contract_preserves_fanout_source_provenance() -> None:
    source_message_id = fanout_candidate_source_message_id("42", 1)
    announcement = StructuredAnnouncement.from_payload(
        {
            "idempotency_key": "telegram:-100:42:created:candidate:slot-1",
            "source_id": "source-a",
            "source_channel_id": "-100",
            "source_message_id": source_message_id,
            "source_message_ids": ["42"],
            "source_url": "https://t.me/source_a/42",
            "forward_origin_key": "-2002:99",
            "occurred_at": datetime(2026, 8, 1, tzinfo=UTC).isoformat(),
            "canonical": _canonical().model_dump(mode="json"),
        }
    )

    assert announcement.source_message_id == source_message_id
    assert announcement.source_message_ids == ("42",)
    assert announcement.source_url == "https://t.me/source_a/42"
    assert announcement.forward_origin_key == "-2002:99"


@pytest.mark.asyncio
async def test_digest_siblings_are_persisted_as_separate_post_ai_parents() -> None:
    source_url = "https://t.me/source_a/42"
    forward_origin = "-2002:99"
    first = _announcement(
        "digest-first",
        source_message_id=fanout_candidate_source_message_id("42", 0),
        source_message_ids=("42",),
        source_url=source_url,
        forward_origin_key=forward_origin,
        canonical=_canonical(
            price=Decimal("500"),
            district="Chilonzor",
            rooms=2,
            area=Decimal("55"),
            floor=3,
            address="Chilonzor 5 kvartal",
            phone="+998901112233",
        ),
        media=(_media("digest-first", "0123456789abcdef"),),
    )
    second = _announcement(
        "digest-second",
        source_message_id=fanout_candidate_source_message_id("42", 1),
        source_message_ids=("42",),
        source_url=source_url,
        forward_origin_key=forward_origin,
        canonical=_canonical(
            price=Decimal("1200"),
            district="Sergeli",
            rooms=4,
            area=Decimal("100"),
            floor=9,
            address="Sergeli 7 massiv",
            phone="+998909998877",
        ),
        media=(_media("digest-second", "0123456789abcdef"),),
    )
    config = DedupScoringConfig()
    sibling_signals = {
        matcher.name: matcher.match(second, first, config)
        for matcher in DEFAULT_SIGNAL_MATCHERS
    }
    repository = InMemoryAnnouncementRepository()
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=repository),
        announcement_repository=repository,
        manual_review_queue=ManualReviewQueueService(repository),
    )

    first_decision = await processor.process(first)
    second_decision = await processor.process(second)

    assert first_decision.decision == "new"
    assert second_decision.decision == "new"
    assert not sibling_signals["source_url"].matched
    assert not sibling_signals["forward_origin"].matched
    assert not sibling_signals["same_image_phash"].matched
    persisted = repository.list_all_for_audit()
    assert {item.announcement_id for item in persisted} == {
        "digest-first",
        "digest-second",
    }
    assert all(item.parent_id is None for item in persisted)
    assert all(item.source_message_ids == ("42",) for item in persisted)
    assert len(
        {
            (item.source_id, item.source_channel_id, item.source_message_id)
            for item in persisted
        }
    ) == 2
    assert repository.merge_audit == []


@pytest.mark.asyncio
async def test_address_similarity_contributes_explainable_signal() -> None:
    existing = _announcement(
        "old",
        canonical=_canonical(address="Yunusobod 9 kvartal 12 uy", phone=None),
    )
    incoming = _announcement(
        "new",
        canonical=_canonical(address="Yunusobod 9 kvartal 12 uy 1", phone=None),
    )

    decision = await _decision(incoming, [existing])

    assert any(signal.name == "similar_address" and signal.matched for signal in decision.breakdown)


@pytest.mark.asyncio
async def test_complete_structured_fingerprint_with_phone_auto_merges() -> None:
    existing = _announcement("old")
    incoming = _announcement("new")

    decision = await _decision(incoming, [existing])

    assert decision.decision == "high_confidence_duplicate"
    assert decision.score >= 60


@pytest.mark.asyncio
async def test_common_realtor_text_does_not_create_duplicate_review() -> None:
    existing = _announcement(
        "old",
        canonical=_canonical(
            district="Olmazor",
            rooms=3,
            area=Decimal("75"),
            floor=9,
            price=Decimal("700"),
            address="Olmazor NBU Bank",
        ),
    )
    incoming = _announcement(
        "new",
        canonical=_canonical(
            district="Olmazor",
            rooms=2,
            area=Decimal("65"),
            floor=7,
            price=Decimal("600"),
            address="Olmazor",
        ),
    )

    decision = await _decision(incoming, [existing])

    assert decision.decision == "new"


@pytest.mark.asyncio
async def test_per_person_price_mismatch_does_not_add_price_signal() -> None:
    existing = _announcement("old", canonical=_canonical(price=Decimal("500"), phone=None))
    incoming = _announcement(
        "new",
        canonical=_canonical(price=Decimal("100"), price_basis="per_person", phone=None),
    )

    decision = await _decision(incoming, [existing])

    price_signal = next(signal for signal in decision.breakdown if signal.name == "price")
    assert price_signal.matched is False
    assert "not comparable" in price_signal.reason


@pytest.mark.asyncio
async def test_possible_duplicate_goes_to_manual_review_without_merge() -> None:
    existing = _announcement(
        "old",
        canonical=_canonical(phone=None),
        media=(_media("old", "0000000000000000"),),
    )
    incoming = _announcement(
        "new",
        canonical=_canonical(
            price=Decimal("900"),
            district="Chilonzor",
            rooms=3,
            area=Decimal("90"),
            floor=9,
            address="Chilonzor 20 kvartal",
            phone=None,
        ),
        media=(_media("new", "0000000000000001"),),
    )
    repository = InMemoryAnnouncementRepository([existing])
    review_queue = ManualReviewQueueService(repository)
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=repository),
        announcement_repository=repository,
        manual_review_queue=review_queue,
    )

    decision = await processor.process(incoming)

    assert decision.decision == "possible_duplicate"
    assert len(review_queue.items) == 1
    assert repository.search_user_facing() == [existing]


@pytest.mark.asyncio
async def test_manual_review_admin_actions_are_idempotent_and_audited() -> None:
    announcement = _announcement("review-me", canonical=_canonical(confidence=0.4, phone=None))
    repository = InMemoryAnnouncementRepository([announcement])
    review_queue = ManualReviewQueueService(repository)
    admin = ManualReviewAdminService(review_queue)
    decision = manual_review_decision(reason="low_confidence")
    item = await review_queue.enqueue(
        announcement=announcement,
        reason="low_confidence",
        decision=decision,
    )
    fixed_canonical = _canonical(price=Decimal("520"), confidence=0.93, phone=None)

    first = await admin.update_canonical(
        item.review_id,
        announcement_id=announcement.announcement_id,
        canonical=fixed_canonical,
    )
    second = await admin.update_canonical(
        item.review_id,
        announcement_id=announcement.announcement_id,
        canonical=fixed_canonical,
    )
    updated = await repository.get("review-me")

    assert first == second
    assert first.status == "approved"
    assert updated is not None
    assert updated.canonical.price == Decimal("520")
    assert [entry["action"] for entry in review_queue.audit_log] == [
        "enqueue",
        "approve_canonical_update",
    ]


@pytest.mark.asyncio
async def test_manual_review_reject_and_mark_as_new_are_idempotent() -> None:
    announcement = replace(
        _announcement("review-me", canonical=_canonical(phone=None)),
        status="manual_review",
    )
    repository = InMemoryAnnouncementRepository([announcement])
    review_queue = ManualReviewQueueService(repository)
    admin = ManualReviewAdminService(review_queue)
    business_item = await review_queue.enqueue(
        announcement=announcement,
        reason="business_quality",
        decision=manual_review_decision(reason="business_quality"),
    )
    duplicate_item = await review_queue.enqueue(
        announcement=announcement,
        reason="possible_duplicate",
        decision=manual_review_decision(reason="possible_duplicate", candidate_id="candidate"),
    )

    rejected = await admin.reject(business_item.review_id)
    rejected_again = await admin.reject(business_item.review_id)
    approved = await admin.mark_as_new(duplicate_item.review_id)
    approved_again = await admin.mark_as_new(duplicate_item.review_id)

    # A replay must not append another action even on clocks whose timestamps
    # have too little resolution to distinguish consecutive calls.
    assert [entry["action"] for entry in review_queue.audit_log] == [
        "enqueue",
        "enqueue",
        "reject",
        "approve_as_new",
    ]
    assert rejected == rejected_again
    assert rejected.status == "rejected"
    assert approved == approved_again
    assert approved.status == "approved"
    activated = await repository.get(announcement.announcement_id)
    assert activated is not None
    assert activated.status == "active"


@pytest.mark.asyncio
async def test_no_candidate_creates_new_parent() -> None:
    repository = InMemoryAnnouncementRepository()
    review_queue = ManualReviewQueueService(repository)
    incoming = _announcement("new", canonical=_canonical(district="Sergeli", phone=None))
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=repository),
        announcement_repository=repository,
        manual_review_queue=review_queue,
    )

    decision = await processor.process(incoming)

    assert decision.decision == "new"
    assert [item.announcement_id for item in repository.search_user_facing()] == ["new"]


def test_parent_selection_is_deterministic_by_completeness_then_first_seen() -> None:
    older = _announcement("old", created_at=datetime(2026, 8, 1, tzinfo=UTC))
    newer = _announcement("new", created_at=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(seconds=1))

    parent, child = ParentSelectionPolicy().select_parent(newer, older)

    assert parent.announcement_id == "old"
    assert child.announcement_id == "new"


def test_parent_selection_uses_trusted_source_before_first_seen() -> None:
    lower_trust = _announcement(
        "old",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    higher_trust = _announcement(
        "new",
        created_at=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(seconds=1),
    )
    higher_trust = StructuredAnnouncement(
        **{
            **higher_trust.__dict__,
            "trusted_source_score": 10,
        }
    )

    parent, child = ParentSelectionPolicy().select_parent(higher_trust, lower_trust)

    assert parent.announcement_id == "new"
    assert child.announcement_id == "old"


def test_config_builds_weighted_dedup_settings_from_app_settings() -> None:
    settings = Settings(
        environment="test",
        dedup_candidate_limit=7,
        dedup_phone_weight=15,
        dedup_area_tolerance_sqm=2.5,
        dedup_config_version="estateflow.dedup.test",
    )

    config = dedup_scoring_config_from_settings(settings)

    assert config.version == "estateflow.dedup.test"
    assert config.candidate_limit == 7
    assert config.phone_weight == 15
    assert config.area_tolerance_sqm == Decimal("2.5")


@pytest.mark.asyncio
async def test_engine_uses_configured_candidate_limit() -> None:
    repository = RecordingCandidateRepository([_announcement("old")])
    engine = WeightedDeduplicationEngine(
        candidate_repository=repository,
        config=DedupScoringConfig(candidate_limit=3),
    )

    await engine.evaluate(_announcement("new"))

    assert repository.seen_limit == 3


def test_default_signal_matchers_are_explainable_and_extensible() -> None:
    assert [matcher.name for matcher in DEFAULT_SIGNAL_MATCHERS] == [
        "source_url",
        "forward_origin",
        "same_image_phash",
        "district",
        "rooms",
        "area",
        "price",
        "floor",
        "similar_address",
        "similar_description",
        "phone",
    ]


def test_asyncpg_candidate_query_is_limited_and_index_friendly() -> None:
    normalized_sql = " ".join(DEDUP_CANDIDATE_QUERY.lower().split())

    assert "a.parent_announcement_id is null" in normalized_sql
    assert "a.status = 'active'" in normalized_sql
    assert "a.occurred_at >= $3::timestamptz" in normalized_sql
    assert "a.source_url = $4" in normalized_sql
    assert "a.forward_origin_key = $5" in normalized_sql
    assert "a.district = $6" in normalized_sql
    assert "a.rooms = $7" in normalized_sql
    assert "a.area_sqm between" in normalized_sql
    assert "a.price_normalized_monthly between" in normalized_sql
    assert "limit $15" in normalized_sql


def test_persistent_search_queries_separate_user_facing_and_audit_views() -> None:
    user_sql = " ".join(USER_FACING_CANONICAL_SEARCH_QUERY.lower().split())
    audit_sql = " ".join(ADMIN_AUDIT_ANNOUNCEMENTS_QUERY.lower().split())

    assert "parent_announcement_id is null" in user_sql
    assert "status = 'active'" in user_sql
    assert "parent_announcement_id = $1" in audit_sql
    assert "announcement_id = $1" in audit_sql


def test_persistent_parent_child_link_uses_row_locks_for_concurrency() -> None:
    normalized_sql = " ".join(PARENT_CHILD_LINK_LOCKING_QUERY.lower().split())

    assert "for update" in normalized_sql
    assert "announcement_id = any" in normalized_sql


@pytest.mark.asyncio
async def test_high_confidence_duplicate_links_child_and_search_returns_only_parent() -> None:
    existing = _announcement(
        "old",
        canonical=_canonical(
            price=Decimal("300"),
            district="Chilonzor",
            rooms=1,
            area=Decimal("35"),
            floor=2,
            address="Chilonzor 1 kvartal",
        ),
        media=(_media("old", "0000000000000000"),),
    )
    incoming = _announcement(
        "new",
        canonical=_canonical(
            price=Decimal("1200"),
            district="Sergeli",
            rooms=4,
            area=Decimal("100"),
            floor=9,
            address="Sergeli 7 massiv",
        ),
        media=(_media("new", "0000000000000001"),),
        source_id="source-b",
    )
    repository = InMemoryAnnouncementRepository([existing])
    review_queue = ManualReviewQueueService(repository)
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=repository),
        announcement_repository=repository,
        manual_review_queue=review_queue,
    )

    decision = await processor.process(incoming)
    child = await repository.get("new")

    assert decision.decision == "high_confidence_duplicate"
    assert child is not None
    assert child.parent_id == "old"
    assert [item.announcement_id for item in repository.search_user_facing()] == ["old"]
    assert len(repository.list_all_for_audit()) == 2
    assert len(repository.decision_audit) == 1


@pytest.mark.asyncio
async def test_multiple_channel_children_link_to_one_parent_without_deleting_sources() -> None:
    parent = _announcement("parent", media=(_media("p", "0000000000000000"),))
    child_a = _announcement(
        "child-a",
        media=(_media("a", "0000000000000001"),),
        source_id="source-b",
    )
    child_b = _announcement(
        "child-b",
        media=(_media("b", "0000000000000002"),),
        source_id="source-c",
    )
    repository = InMemoryAnnouncementRepository([parent])
    review_queue = ManualReviewQueueService(repository)
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=repository),
        announcement_repository=repository,
        manual_review_queue=review_queue,
    )

    await processor.process(child_a)
    await processor.process(child_b)

    assert [item.announcement_id for item in repository.search_user_facing()] == ["parent"]
    assert {item.announcement_id for item in repository.list_all_for_audit()} == {
        "parent",
        "child-a",
        "child-b",
    }
    linked_child_a = await repository.get("child-a")
    linked_child_b = await repository.get("child-b")
    updated_parent = await repository.get("parent")
    assert linked_child_a is not None
    assert linked_child_b is not None
    assert updated_parent is not None
    assert linked_child_a.parent_id == "parent"
    assert linked_child_b.parent_id == "parent"
    assert updated_parent.source_count == 3


@pytest.mark.asyncio
async def test_parallel_duplicate_processing_does_not_create_duplicate_parent() -> None:
    existing = _announcement(
        "old",
        canonical=_canonical(
            price=Decimal("300"),
            district="Chilonzor",
            rooms=1,
            area=Decimal("35"),
            floor=2,
            address="Chilonzor 1 kvartal",
        ),
        media=(_media("old", "0000000000000000"),),
    )
    incoming = _announcement(
        "new",
        canonical=_canonical(
            price=Decimal("1200"),
            district="Sergeli",
            rooms=4,
            area=Decimal("100"),
            floor=9,
            address="Sergeli 7 massiv",
        ),
        media=(_media("new", "0000000000000001"),),
        source_id="source-b",
    )
    repository = InMemoryAnnouncementRepository([existing])
    review_queue = ManualReviewQueueService(repository)
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=repository),
        announcement_repository=repository,
        manual_review_queue=review_queue,
    )

    await asyncio.gather(processor.process(incoming), processor.process(incoming))

    assert [item.announcement_id for item in repository.search_user_facing()] == ["old"]
    assert len(repository.list_all_for_audit()) == 2


@pytest.mark.asyncio
async def test_richer_child_can_be_selected_as_canonical_parent() -> None:
    parent = _announcement(
        "old",
        canonical=_canonical(area=None, address=None, phone=None),
        media=(_media("old", "0000000000000000"),),
    )
    child = _announcement(
        "new",
        canonical=_canonical(area=Decimal("65"), address="Yunusobod 9 kvartal", phone=None),
        media=(_media("new", "0000000000000001"),),
    )
    repository = InMemoryAnnouncementRepository([parent])
    review_queue = ManualReviewQueueService(repository)
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=repository),
        announcement_repository=repository,
        manual_review_queue=review_queue,
    )

    await processor.process(child)
    old_child = await repository.get("old")
    new_parent = await repository.get("new")

    assert old_child is not None
    assert new_parent is not None
    assert old_child.parent_id == "new"
    assert new_parent.canonical.area_sqm == Decimal("65")
    assert new_parent.canonical.address == "Yunusobod 9 kvartal"


@pytest.mark.asyncio
async def test_merge_conflicts_are_audited_without_overwriting_parent_values() -> None:
    parent = _announcement(
        "old",
        canonical=_canonical(price=Decimal("500"), address="Yunusobod 9 kvartal", phone=None),
        media=(_media("old", "0000000000000000"),),
    )
    child = _announcement(
        "new",
        canonical=_canonical(price=Decimal("650"), address="Yunusobod 9 kvartal", phone=None),
        media=(_media("new", "0000000000000001"),),
    )
    repository = InMemoryAnnouncementRepository([parent])
    review_queue = ManualReviewQueueService(repository)
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=repository),
        announcement_repository=repository,
        manual_review_queue=review_queue,
    )

    await processor.process(child)
    updated_parent = await repository.get("old")

    assert updated_parent is not None
    assert updated_parent.canonical.price == Decimal("500")
    conflicts = cast(list[dict[str, Any]], repository.merge_audit[0]["conflicts"])
    assert any(conflict["field"] == "price" for conflict in conflicts)
