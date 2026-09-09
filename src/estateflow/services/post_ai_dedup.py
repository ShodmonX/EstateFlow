from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, cast

from estateflow.application.core.config import Settings
from estateflow.contracts.events import ANNOUNCEMENT_PERSISTED_QUEUE, QueueMessage
from estateflow.services.analytics import TechnicalMetricRecorder, safe_record_technical_metric
from estateflow.services.extraction import CanonicalListing
from estateflow.services.media_storage import StoredMedia
from estateflow.services.pre_ai_dedup import (
    extract_phone_numbers,
    hamming_distance_hex,
    jaccard_similarity,
    normalize_listing_text,
)
from estateflow.services.source_parsing.contracts import (
    are_fanout_source_message_siblings,
)

DedupDecisionType = Literal[
    "exact_duplicate",
    "high_confidence_duplicate",
    "possible_duplicate",
    "new",
]
ReviewStatus = Literal["pending", "approved", "rejected", "merged"]
ReviewReason = Literal["possible_duplicate", "low_confidence", "business_quality"]
AnnouncementStatus = Literal["active", "archived", "manual_review"]


@dataclass(frozen=True)
class DedupScoringConfig:
    version: str = "estateflow.dedup.v2"
    exact_threshold: int = 100
    high_confidence_threshold: int = 80
    possible_threshold: int = 60
    candidate_limit: int = 50
    source_url_weight: int = 100
    same_image_weight: int = 70
    district_weight: int = 10
    rooms_weight: int = 10
    area_weight: int = 10
    price_weight: int = 10
    floor_weight: int = 5
    address_weight: int = 15
    phone_weight: int = 15
    phash_hamming_threshold: int = 8
    area_tolerance_sqm: Decimal = Decimal("3")
    price_tolerance_ratio: Decimal = Decimal("0.05")
    address_similarity_threshold: float = 0.75
    description_similarity_threshold: float = 0.82
    parent_selection_policy: str = "completeness_trust_first_seen"


@dataclass(frozen=True)
class StructuredAnnouncement:
    announcement_id: str
    idempotency_key: str
    source_id: str
    source_channel_id: str
    source_message_id: str
    occurred_at: datetime
    canonical: CanonicalListing
    media: tuple[StoredMedia, ...] = ()
    source_url: str | None = None
    forward_origin_key: str | None = None
    source_message_ids: tuple[str, ...] = ()
    trusted_source_score: int = 0
    parent_id: str | None = None
    status: AnnouncementStatus = "active"
    source_count: int = 1
    latest_source_id: str | None = None
    review_reason: ReviewReason | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_parent(self) -> bool:
        return self.parent_id is None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> StructuredAnnouncement:
        import uuid

        idempotency_key = payload["idempotency_key"]
        source_message_id = str(payload["source_message_id"])
        raw_source_message_ids = payload.get("source_message_ids")
        source_message_ids = tuple(
            str(item)
            for item in raw_source_message_ids
            if item is not None
        ) if isinstance(raw_source_message_ids, list | tuple) else ()
        source_message_ids = source_message_ids or (source_message_id,)
        announcement_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
        raw_review_reason = payload.get("manual_review_reason")
        review_reason: ReviewReason | None = None
        if raw_review_reason == "low_confidence":
            review_reason = "low_confidence"
        elif raw_review_reason:
            review_reason = "business_quality"
        return cls(
            announcement_id=announcement_id,
            idempotency_key=idempotency_key,
            source_id=payload["source_id"],
            source_channel_id=payload["source_channel_id"],
            source_message_id=source_message_id,
            occurred_at=datetime.fromisoformat(payload["occurred_at"]),
            canonical=CanonicalListing.model_validate(payload["canonical"]),
            media=tuple(StoredMedia(**item) for item in payload.get("media", [])),
            source_url=payload.get("source_url"),
            forward_origin_key=payload.get("forward_origin_key"),
            source_message_ids=source_message_ids,
            status="manual_review" if review_reason is not None else "active",
            review_reason=review_reason,
        )


@dataclass(frozen=True)
class ScoreSignal:
    name: str
    weight: int
    matched: bool
    reason: str
    details: dict[str, object] = field(default_factory=dict)
    independent: bool = True
    exact: bool = False


@dataclass(frozen=True)
class CandidateScore:
    candidate_id: str
    score: int
    signals: tuple[ScoreSignal, ...]


@dataclass(frozen=True)
class DedupDecision:
    decision: DedupDecisionType
    score: int
    threshold: int
    candidate_id: str | None
    config_version: str
    breakdown: tuple[ScoreSignal, ...]


class SignalMatcher(Protocol):
    name: str

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal: ...


class DedupCandidateRepository(Protocol):
    async def list_candidates(
        self,
        announcement: StructuredAnnouncement,
        *,
        limit: int = 50,
    ) -> list[StructuredAnnouncement]: ...


class ParentChildAnnouncementRepository(DedupCandidateRepository, Protocol):
    async def save_parent(self, announcement: StructuredAnnouncement) -> StructuredAnnouncement: ...

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None: ...

    async def link_child(
        self,
        *,
        child: StructuredAnnouncement,
        parent_id: str,
        decision: DedupDecision,
        merge_policy: str,
    ) -> StructuredAnnouncement: ...

    async def record_decision(
        self,
        *,
        announcement_id: str,
        decision: DedupDecision,
    ) -> None: ...

    async def update_canonical(
        self,
        *,
        announcement_id: str,
        canonical: CanonicalListing,
        reason: str,
    ) -> StructuredAnnouncement: ...

    async def activate_announcement(self, announcement_id: str) -> StructuredAnnouncement: ...


class ManualReviewQueue(Protocol):
    async def enqueue(
        self,
        *,
        announcement: StructuredAnnouncement,
        reason: ReviewReason,
        decision: DedupDecision,
    ) -> ManualReviewItem: ...


def _are_fanout_sibling_announcements(
    incoming: StructuredAnnouncement,
    candidate: StructuredAnnouncement,
) -> bool:
    return bool(
        incoming.source_id == candidate.source_id
        and incoming.source_channel_id == candidate.source_channel_id
        and are_fanout_source_message_siblings(
            incoming.source_message_id,
            candidate.source_message_id,
        )
    )


class SourceUrlMatcher:
    name = "source_url"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        fanout_siblings = _are_fanout_sibling_announcements(incoming, candidate)
        matched = bool(
            incoming.source_url
            and incoming.source_url == candidate.source_url
            and not fanout_siblings
        )
        return ScoreSignal(
            name=self.name,
            weight=config.source_url_weight,
            matched=matched,
            exact=matched,
            reason=(
                "source URL belongs to a distinct digest sibling"
                if fanout_siblings
                else (
                    "source URL matched exactly"
                    if matched
                    else "source URL missing or different"
                )
            ),
        )


class ForwardOriginMatcher:
    name = "forward_origin"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        fanout_siblings = _are_fanout_sibling_announcements(incoming, candidate)
        matched = bool(
            incoming.forward_origin_key
            and incoming.forward_origin_key == candidate.forward_origin_key
            and not fanout_siblings
        )
        return ScoreSignal(
            name=self.name,
            weight=config.source_url_weight,
            matched=matched,
            exact=matched,
            reason=(
                "forward provenance belongs to a distinct digest sibling"
                if fanout_siblings
                else (
                    "forward provenance matched exactly"
                    if matched
                    else "forward provenance missing or different"
                )
            ),
        )


class ImagePHashMatcher:
    name = "same_image_phash"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        fanout_siblings = _are_fanout_sibling_announcements(incoming, candidate)
        best_distance: int | None = None
        for media in incoming.media:
            for candidate_media in candidate.media:
                distance = hamming_distance_hex(media.phash, candidate_media.phash)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
        matched = bool(
            best_distance is not None
            and best_distance <= config.phash_hamming_threshold
            and not fanout_siblings
        )
        return ScoreSignal(
            name=self.name,
            weight=config.same_image_weight,
            matched=matched,
            reason=(
                "image belongs to a distinct digest sibling"
                if fanout_siblings
                else ("image pHash matched" if matched else "no close image pHash match")
            ),
            details={} if best_distance is None else {"hamming_distance": best_distance},
        )


class DistrictMatcher:
    name = "district"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        left = _casefold_or_none(incoming.canonical.district)
        right = _casefold_or_none(candidate.canonical.district)
        matched = left is not None and left == right
        return ScoreSignal(
            name=self.name,
            weight=config.district_weight,
            matched=matched,
            reason="district matched" if matched else "district missing or different",
        )


class RoomsMatcher:
    name = "rooms"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        matched = (
            incoming.canonical.rooms is not None
            and incoming.canonical.rooms == candidate.canonical.rooms
        )
        return ScoreSignal(
            name=self.name,
            weight=config.rooms_weight,
            matched=matched,
            reason="room count matched" if matched else "room count missing or different",
        )


class AreaMatcher:
    name = "area"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        matched = False
        difference: Decimal | None = None
        if incoming.canonical.area_sqm is not None and candidate.canonical.area_sqm is not None:
            difference = abs(incoming.canonical.area_sqm - candidate.canonical.area_sqm)
            matched = difference < config.area_tolerance_sqm
        return ScoreSignal(
            name=self.name,
            weight=config.area_weight,
            matched=matched,
            reason="area is within tolerance" if matched else "area missing or outside tolerance",
            details={} if difference is None else {"difference_sqm": str(difference)},
        )


class PriceMatcher:
    name = "price"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        left = incoming.canonical
        right = candidate.canonical
        if not _prices_are_comparable(left, right):
            return ScoreSignal(
                name=self.name,
                weight=config.price_weight,
                matched=False,
                reason="price period, basis, currency, or value is not comparable",
            )
        assert left.price_normalized_monthly is not None
        assert right.price_normalized_monthly is not None
        base = max(abs(left.price_normalized_monthly), abs(right.price_normalized_monthly))
        diff_ratio = (
            Decimal("0")
            if base == 0
            else abs(left.price_normalized_monthly - right.price_normalized_monthly) / base
        )
        matched = diff_ratio <= config.price_tolerance_ratio
        return ScoreSignal(
            name=self.name,
            weight=config.price_weight,
            matched=matched,
            reason="monthly normalized total price is within tolerance"
            if matched
            else "price outside tolerance",
            details={"difference_ratio": str(diff_ratio)},
        )


class FloorMatcher:
    name = "floor"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        matched = (
            incoming.canonical.floor is not None
            and incoming.canonical.floor == candidate.canonical.floor
        )
        return ScoreSignal(
            name=self.name,
            weight=config.floor_weight,
            matched=matched,
            reason="floor matched" if matched else "floor missing or different",
        )


class SimilarAddressMatcher:
    name = "similar_address"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        left = _tokens(incoming.canonical.address)
        right = _tokens(candidate.canonical.address)
        similarity = jaccard_similarity(left, right)
        matched = bool(left and right and similarity >= config.address_similarity_threshold)
        return ScoreSignal(
            name=self.name,
            weight=config.address_weight,
            matched=matched,
            reason="address text is similar" if matched else "address missing or not similar",
            details={"similarity": round(similarity, 4)},
        )


class PhoneMatcher:
    name = "phone"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        matched = bool(
            set(incoming.canonical.phone_numbers) & set(candidate.canonical.phone_numbers)
        )
        return ScoreSignal(
            name=self.name,
            weight=config.phone_weight,
            matched=matched,
            independent=False,
            reason="phone matched as weak helper signal"
            if matched
            else "phone missing or different",
        )


class SimilarDescriptionMatcher:
    name = "similar_description"

    def match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
        config: DedupScoringConfig,
    ) -> ScoreSignal:
        left = _description_tokens(incoming.canonical.description)
        right = _description_tokens(candidate.canonical.description)
        similarity = jaccard_similarity(left, right)
        matched = bool(
            left
            and right
            and similarity >= config.description_similarity_threshold
        )
        return ScoreSignal(
            name=self.name,
            weight=30,
            matched=matched,
            exact=matched and similarity >= 0.95,
            reason="description text is similar"
            if matched
            else "description missing or not similar",
            details={"similarity": round(similarity, 4)},
        )


DEFAULT_SIGNAL_MATCHERS: tuple[SignalMatcher, ...] = (
    SourceUrlMatcher(),
    ForwardOriginMatcher(),
    ImagePHashMatcher(),
    DistrictMatcher(),
    RoomsMatcher(),
    AreaMatcher(),
    PriceMatcher(),
    FloorMatcher(),
    SimilarAddressMatcher(),
    SimilarDescriptionMatcher(),
    PhoneMatcher(),
)


class WeightedDeduplicationEngine:
    def __init__(
        self,
        *,
        candidate_repository: DedupCandidateRepository,
        config: DedupScoringConfig | None = None,
        matchers: tuple[SignalMatcher, ...] = DEFAULT_SIGNAL_MATCHERS,
    ) -> None:
        self._candidate_repository = candidate_repository
        self._config = config or DedupScoringConfig()
        self._matchers = matchers

    async def evaluate(self, announcement: StructuredAnnouncement) -> DedupDecision:
        candidates = await self._candidate_repository.list_candidates(
            announcement,
            limit=self._config.candidate_limit,
        )
        best: CandidateScore | None = None
        for candidate in candidates:
            candidate_score = self._score_candidate(announcement, candidate)
            if best is None or candidate_score.score > best.score:
                best = candidate_score
        if best is None:
            return DedupDecision(
                decision="new",
                score=0,
                threshold=self._config.possible_threshold,
                candidate_id=None,
                config_version=self._config.version,
                breakdown=(),
            )
        return self._decision_from_score(best)

    def _score_candidate(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
    ) -> CandidateScore:
        signals = tuple(
            matcher.match(incoming, candidate, self._config) for matcher in self._matchers
        )
        return CandidateScore(
            candidate_id=candidate.announcement_id,
            score=sum(signal.weight for signal in signals if signal.matched),
            signals=signals,
        )

    def _decision_from_score(self, best: CandidateScore) -> DedupDecision:
        matched = {signal.name for signal in best.signals if signal.matched}
        exact_identity = {"source_url", "forward_origin"}
        if any(
            signal.matched
            and signal.exact
            and signal.name in exact_identity
            for signal in best.signals
        ):
            decision: DedupDecisionType = "exact_duplicate"
            threshold = self._config.exact_threshold
        elif _has_structured_identity(matched):
            # A phone number plus the complete structured listing fingerprint
            # is stronger than the raw weighted score (60 by default).  It is
            # safe to merge without creating a noisy manual-review item.
            decision = "high_confidence_duplicate"
            threshold = self._config.high_confidence_threshold
        elif best.score >= self._config.exact_threshold and _has_strong_anchor(
            best.signals
        ):
            decision = "exact_duplicate"
            threshold = self._config.exact_threshold
        elif best.score >= self._config.high_confidence_threshold and _has_strong_anchor(
            best.signals
        ):
            decision = "high_confidence_duplicate"
            threshold = self._config.high_confidence_threshold
        elif (
            best.score >= self._config.possible_threshold
            and _has_manual_review_anchor(best.signals)
        ):
            decision = "possible_duplicate"
            threshold = self._config.possible_threshold
        else:
            decision = "new"
            threshold = self._config.possible_threshold
        return DedupDecision(
            decision=decision,
            score=best.score,
            threshold=threshold,
            candidate_id=best.candidate_id,
            config_version=self._config.version,
            breakdown=best.signals,
        )


_STRUCTURED_IDENTITY_SIGNALS = frozenset(
    {"district", "rooms", "area", "price", "floor"}
)


def _has_structured_identity(matched: set[str]) -> bool:
    """Return whether phone + all core listing fields match.

    District, room count, area, normalized monthly price, and floor together
    form a useful fingerprint.  Phone is required so that common listings in
    the same district are not merged merely because their numeric fields look
    alike.
    """

    return "phone" in matched and _STRUCTURED_IDENTITY_SIGNALS <= matched


def _has_strong_anchor(signals: tuple[ScoreSignal, ...]) -> bool:
    matched = {signal.name for signal in signals if signal.matched}
    if matched & {"source_url", "forward_origin", "same_image_phash"}:
        return True
    if _has_structured_identity(matched):
        return True
    return {"phone", "similar_address", "similar_description"} <= matched


def _has_manual_review_anchor(signals: tuple[ScoreSignal, ...]) -> bool:
    """Keep manual review for evidence-backed ambiguity only.

    A score made only from common fields (for example phone + district + room
    count) is not enough to interrupt operations.  Image, provenance, or a
    combination of content signals must support the review decision.
    """

    matched = {signal.name for signal in signals if signal.matched}
    if matched & {"source_url", "forward_origin", "same_image_phash"}:
        return True
    if _has_structured_identity(matched):
        return False
    if {"similar_address", "similar_description"} <= matched:
        return True
    structured_matches = len(matched & _STRUCTURED_IDENTITY_SIGNALS)
    return (
        {"phone", "similar_description"} <= matched
        and structured_matches >= 2
    )


class InMemoryAnnouncementRepository(DedupCandidateRepository):
    def __init__(
        self,
        announcements: list[StructuredAnnouncement] | None = None,
        *,
        config: DedupScoringConfig | None = None,
    ) -> None:
        self._announcements: dict[str, StructuredAnnouncement] = {
            item.announcement_id: item for item in announcements or []
        }
        self._config = config or DedupScoringConfig()
        self.merge_audit: list[dict[str, object]] = []
        self.decision_audit: list[DedupDecision] = []
        self._lock = asyncio.Lock()

    async def list_candidates(
        self,
        announcement: StructuredAnnouncement,
        *,
        limit: int = 50,
    ) -> list[StructuredAnnouncement]:
        candidates = [
            item
            for item in self._announcements.values()
            if item.announcement_id != announcement.announcement_id
            and item.status == "active"
            and item.is_parent
            and self._cheap_candidate_match(announcement, item)
        ]
        candidates.sort(key=lambda item: item.created_at, reverse=True)
        return candidates[:limit]

    async def save_parent(self, announcement: StructuredAnnouncement) -> StructuredAnnouncement:
        async with self._lock:
            existing = self._announcements.get(announcement.announcement_id)
            if existing is not None:
                return existing
            parent = replace(announcement, parent_id=None, latest_source_id=announcement.source_id)
            self._announcements[parent.announcement_id] = parent
            return parent

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None:
        return self._announcements.get(announcement_id)

    async def activate_announcement(self, announcement_id: str) -> StructuredAnnouncement:
        async with self._lock:
            announcement = self._announcements[announcement_id]
            updated = replace(announcement, status="active", updated_at=datetime.now(UTC))
            self._announcements[announcement_id] = updated
            return updated

    async def link_child(
        self,
        *,
        child: StructuredAnnouncement,
        parent_id: str,
        decision: DedupDecision,
        merge_policy: str,
    ) -> StructuredAnnouncement:
        async with self._lock:
            parent = self._announcements[parent_id]
            existing_child = self._announcements.get(child.announcement_id)
            if existing_child is not None and existing_child.parent_id == parent_id:
                return existing_child
            parent_update, conflicts = merge_parent_fields(parent, child)
            parent_update = replace(
                parent_update,
                source_count=parent.source_count + 1,
                latest_source_id=child.source_id,
                updated_at=datetime.now(UTC),
            )
            linked_child = replace(
                child,
                parent_id=parent_update.announcement_id,
                status="active",
                updated_at=datetime.now(UTC),
            )
            self._announcements[parent_update.announcement_id] = parent_update
            self._announcements[linked_child.announcement_id] = linked_child
            self.merge_audit.append(
                {
                    "parent_id": parent_update.announcement_id,
                    "child_id": linked_child.announcement_id,
                    "decision": decision.decision,
                    "score": decision.score,
                    "config_version": decision.config_version,
                    "merge_policy": merge_policy,
                    "conflicts": conflicts,
                }
            )
            return linked_child

    async def record_decision(
        self,
        *,
        announcement_id: str,
        decision: DedupDecision,
    ) -> None:
        self.decision_audit.append(decision)

    async def update_canonical(
        self,
        *,
        announcement_id: str,
        canonical: CanonicalListing,
        reason: str,
    ) -> StructuredAnnouncement:
        async with self._lock:
            announcement = self._announcements[announcement_id]
            updated = replace(announcement, canonical=canonical, updated_at=datetime.now(UTC))
            self._announcements[announcement_id] = updated
            self.merge_audit.append(
                {
                    "parent_id": announcement_id,
                    "child_id": None,
                    "decision": "manual_canonical_update",
                    "score": 0,
                    "config_version": "",
                    "merge_policy": "manual_review",
                    "conflicts": [{"reason": reason}],
                }
            )
            return updated

    def search_user_facing(self) -> list[StructuredAnnouncement]:
        return [
            item
            for item in self._announcements.values()
            if item.status == "active" and item.parent_id is None
        ]

    def list_all_for_audit(self) -> list[StructuredAnnouncement]:
        return list(self._announcements.values())

    def _cheap_candidate_match(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
    ) -> bool:
        fanout_siblings = _are_fanout_sibling_announcements(incoming, candidate)
        if (
            incoming.source_url
            and incoming.source_url == candidate.source_url
            and not fanout_siblings
        ):
            return True
        if (
            incoming.forward_origin_key
            and incoming.forward_origin_key == candidate.forward_origin_key
            and not fanout_siblings
        ):
            return True
        if set(incoming.canonical.phone_numbers) & set(candidate.canonical.phone_numbers):
            return True
        if (
            incoming.canonical.district
            and incoming.canonical.district == candidate.canonical.district
        ):
            return True
        if (
            incoming.canonical.rooms is not None
            and incoming.canonical.rooms == candidate.canonical.rooms
        ):
            return True
        return bool(
            not fanout_siblings
            and any(
                hamming_distance_hex(incoming_media.phash, candidate_media.phash)
                <= self._config.phash_hamming_threshold
                for incoming_media in incoming.media
                for candidate_media in candidate.media
            )
        )


class ParentSelectionPolicy:
    name = "completeness_trust_first_seen"

    def select_parent(
        self,
        incoming: StructuredAnnouncement,
        candidate: StructuredAnnouncement,
    ) -> tuple[StructuredAnnouncement, StructuredAnnouncement]:
        ranked = sorted(
            [(candidate, 0), (incoming, 1)],
            key=lambda item_with_tie: (
                -_canonical_completeness(item_with_tie[0].canonical),
                -item_with_tie[0].trusted_source_score,
                item_with_tie[0].created_at,
                item_with_tie[1],
                item_with_tie[0].announcement_id,
            ),
        )
        parent = ranked[0][0]
        child = ranked[1][0]
        return parent, child


def parent_selection_policy_from_config(config: DedupScoringConfig) -> ParentSelectionPolicy:
    if config.parent_selection_policy == "completeness_trust_first_seen":
        return ParentSelectionPolicy()
    raise ValueError(f"Unknown parent selection policy: {config.parent_selection_policy}")


@dataclass(frozen=True)
class ManualReviewItem:
    review_id: str
    announcement_id: str
    reason: ReviewReason
    score_breakdown: tuple[ScoreSignal, ...]
    related_candidate_id: str | None
    status: ReviewStatus = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class ManualReviewQueueService:
    def __init__(self, repository: ParentChildAnnouncementRepository) -> None:
        self._repository = repository
        self.items: dict[str, ManualReviewItem] = {}
        self.audit_log: list[dict[str, object]] = []
        self.action_audit: dict[str, dict[str, object]] = {}
        self._lock = asyncio.Lock()

    async def list_items(
        self,
        *,
        status: ReviewStatus | None = None,
        reason: ReviewReason | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ManualReviewItem, ...]:
        persistent_list = getattr(self._repository, "list_manual_review_items", None)
        if persistent_list is not None:
            return cast(
                tuple[ManualReviewItem, ...],
                await persistent_list(
                    status=status,
                    reason=reason,
                    limit=limit,
                    offset=offset,
                ),
            )
        items = [
            item
            for item in self.items.values()
            if (status is None or item.status == status)
            and (reason is None or item.reason == reason)
        ]
        items.sort(key=lambda item: (item.created_at, item.review_id))
        return tuple(items[offset : offset + limit])

    async def count_items(
        self,
        *,
        status: ReviewStatus | None = None,
        reason: ReviewReason | None = None,
    ) -> int:
        persistent_count = getattr(self._repository, "count_manual_review_items", None)
        if persistent_count is not None:
            return cast(int, await persistent_count(status=status, reason=reason))
        return len(
            [
                item
                for item in self.items.values()
                if (status is None or item.status == status)
                and (reason is None or item.reason == reason)
            ]
        )

    async def get_item(self, review_id: str) -> ManualReviewItem | None:
        persistent_get = getattr(self._repository, "get_manual_review_item", None)
        if persistent_get is not None:
            return cast(ManualReviewItem | None, await persistent_get(review_id))
        return self.items.get(review_id)

    async def enqueue(
        self,
        *,
        announcement: StructuredAnnouncement,
        reason: ReviewReason,
        decision: DedupDecision,
    ) -> ManualReviewItem:
        persistent_enqueue = getattr(self._repository, "enqueue_manual_review_item", None)
        if persistent_enqueue is not None:
            return cast(
                ManualReviewItem,
                await persistent_enqueue(
                    announcement=announcement,
                    reason=reason,
                    decision=decision,
                ),
            )
        async with self._lock:
            review_id = f"{reason}:{announcement.idempotency_key}"
            existing = self.items.get(review_id)
            if existing is not None:
                return existing
            item = ManualReviewItem(
                review_id=review_id,
                announcement_id=announcement.announcement_id,
                reason=reason,
                score_breakdown=decision.breakdown,
                related_candidate_id=decision.candidate_id,
            )
            self.items[review_id] = item
            self.audit_log.append(
                {
                    "action": "enqueue",
                    "review_id": review_id,
                    "reason": reason,
                    "candidate_id": decision.candidate_id,
                }
            )
            return item

    async def approve_merge(
        self,
        review_id: str,
        *,
        child: StructuredAnnouncement,
        parent_id: str,
        decision: DedupDecision,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        async with self._lock:
            item = await self._require_item(review_id)
            if item.status == "approved":
                await self._repository.activate_announcement(item.announcement_id)
                return item
            if self._action_replayed(idempotency_key):
                return item
            self._validate_action(
                item,
                expected_status=expected_status,
                admin_user_id=admin_user_id,
            )
            if item.status in {"merged", "approved"}:
                return item
            await self._repository.link_child(
                child=child,
                parent_id=parent_id,
                decision=decision,
                merge_policy="manual_review",
            )
            updated = replace(item, status="merged", decided_at=datetime.now(UTC))
            await self._store_updated_item(updated)
            self._append_action_audit(
                action="approve_merge",
                item=updated,
                admin_user_id=admin_user_id,
                idempotency_key=idempotency_key,
                note=note,
            )
            return updated

    async def approve_as_new(
        self,
        review_id: str,
        *,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        async with self._lock:
            item = await self._require_item(review_id)
            if self._action_replayed(idempotency_key):
                return item
            self._validate_action(
                item,
                expected_status=expected_status,
                admin_user_id=admin_user_id,
            )
            await self._repository.activate_announcement(item.announcement_id)
            updated = replace(item, status="approved", decided_at=datetime.now(UTC))
            await self._store_updated_item(updated)
            self._append_action_audit(
                action="approve_as_new",
                item=updated,
                admin_user_id=admin_user_id,
                idempotency_key=idempotency_key,
                note=note,
            )
            return updated

    async def approve_canonical_update(
        self,
        review_id: str,
        *,
        announcement_id: str,
        canonical: CanonicalListing,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        async with self._lock:
            item = await self._require_item(review_id)
            if self._action_replayed(idempotency_key):
                return item
            self._validate_action(
                item,
                expected_status=expected_status,
                admin_user_id=admin_user_id,
            )
            if item.status == "approved":
                return item
            await self._repository.update_canonical(
                announcement_id=announcement_id,
                canonical=canonical,
                reason=f"manual_review:{item.reason}",
            )
            updated = replace(item, status="approved", decided_at=datetime.now(UTC))
            await self._store_updated_item(updated)
            self._append_action_audit(
                action="approve_canonical_update",
                item=updated,
                admin_user_id=admin_user_id,
                idempotency_key=idempotency_key,
                note=note,
                extra={"announcement_id": announcement_id},
            )
            return updated

    async def reject(
        self,
        review_id: str,
        *,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        async with self._lock:
            item = await self._require_item(review_id)
            if self._action_replayed(idempotency_key):
                return item
            self._validate_action(
                item,
                expected_status=expected_status,
                admin_user_id=admin_user_id,
            )
            if item.status == "rejected":
                return item
            updated = replace(item, status="rejected", decided_at=datetime.now(UTC))
            await self._store_updated_item(updated)
            self._append_action_audit(
                action="reject",
                item=updated,
                admin_user_id=admin_user_id,
                idempotency_key=idempotency_key,
                note=note,
            )
            return updated

    async def merge_related_duplicate(
        self,
        review_id: str,
        *,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        async with self._lock:
            item = await self._require_item(review_id)
            if self._action_replayed(idempotency_key):
                return item
            self._validate_action(
                item,
                expected_status=expected_status,
                admin_user_id=admin_user_id,
            )
            if item.related_candidate_id is None:
                raise ValueError("manual_review_missing_duplicate_candidate")
            child = await self._repository.get(item.announcement_id)
            if child is None:
                raise KeyError(item.announcement_id)
            await self._repository.link_child(
                child=child,
                parent_id=item.related_candidate_id,
                decision=manual_review_decision(
                    reason=item.reason,
                    candidate_id=item.related_candidate_id,
                    score_breakdown=item.score_breakdown,
                ),
                merge_policy="manual_review",
            )
            updated = replace(item, status="merged", decided_at=datetime.now(UTC))
            await self._store_updated_item(updated)
            self._append_action_audit(
                action="approve_merge",
                item=updated,
                admin_user_id=admin_user_id,
                idempotency_key=idempotency_key,
                note=note,
            )
            return updated

    async def _require_item(self, review_id: str) -> ManualReviewItem:
        item = await self.get_item(review_id)
        if item is None:
            raise KeyError(review_id)
        return item

    async def _store_updated_item(self, item: ManualReviewItem) -> None:
        persistent_update = getattr(self._repository, "update_manual_review_item", None)
        if persistent_update is not None:
            await persistent_update(item)
            return
        self.items[item.review_id] = item

    def _action_replayed(self, idempotency_key: str | None) -> bool:
        return idempotency_key is not None and idempotency_key in self.action_audit

    def _validate_action(
        self,
        item: ManualReviewItem,
        *,
        expected_status: ReviewStatus | None,
        admin_user_id: int | None,
    ) -> None:
        if admin_user_id is not None and admin_user_id <= 0:
            raise PermissionError("Admin reviewer id is required.")
        if expected_status is not None and item.status != expected_status:
            raise ValueError("manual_review_state_conflict")

    def _append_action_audit(
        self,
        *,
        action: str,
        item: ManualReviewItem,
        admin_user_id: int | None,
        idempotency_key: str | None,
        note: str | None,
        extra: dict[str, object] | None = None,
    ) -> None:
        entry = {
            "action": action,
            "review_id": item.review_id,
            "admin_user_id": admin_user_id,
            "idempotency_key": idempotency_key,
            "note": note,
            "decided_at": item.decided_at,
            "status": item.status,
            **(extra or {}),
        }
        self.audit_log.append(entry)
        if idempotency_key is not None:
            self.action_audit[idempotency_key] = entry


class ManualReviewAdminService:
    def __init__(self, review_queue: ManualReviewQueueService) -> None:
        self._review_queue = review_queue

    async def mark_as_new(
        self,
        review_id: str,
        *,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        return await self._review_queue.approve_as_new(
            review_id,
            admin_user_id=admin_user_id,
            idempotency_key=idempotency_key,
            expected_status=expected_status,
            note=note,
        )

    async def merge_duplicate(
        self,
        review_id: str,
        *,
        child: StructuredAnnouncement,
        parent_id: str,
        decision: DedupDecision,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        return await self._review_queue.approve_merge(
            review_id,
            child=child,
            parent_id=parent_id,
            decision=decision,
            admin_user_id=admin_user_id,
            idempotency_key=idempotency_key,
            expected_status=expected_status,
            note=note,
        )

    async def merge_related_duplicate(
        self,
        review_id: str,
        *,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        return await self._review_queue.merge_related_duplicate(
            review_id,
            admin_user_id=admin_user_id,
            idempotency_key=idempotency_key,
            expected_status=expected_status,
            note=note,
        )

    async def update_canonical(
        self,
        review_id: str,
        *,
        announcement_id: str,
        canonical: CanonicalListing,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        return await self._review_queue.approve_canonical_update(
            review_id,
            announcement_id=announcement_id,
            canonical=canonical,
            admin_user_id=admin_user_id,
            idempotency_key=idempotency_key,
            expected_status=expected_status,
            note=note,
        )

    async def reject(
        self,
        review_id: str,
        *,
        admin_user_id: int | None = None,
        idempotency_key: str | None = None,
        expected_status: ReviewStatus | None = None,
        note: str | None = None,
    ) -> ManualReviewItem:
        return await self._review_queue.reject(
            review_id,
            admin_user_id=admin_user_id,
            idempotency_key=idempotency_key,
            expected_status=expected_status,
            note=note,
        )


def manual_review_decision(
    *,
    reason: ReviewReason,
    candidate_id: str | None = None,
    score_breakdown: tuple[ScoreSignal, ...] = (),
) -> DedupDecision:
    return DedupDecision(
        decision="possible_duplicate" if reason == "possible_duplicate" else "new",
        score=0,
        threshold=0,
        candidate_id=candidate_id,
        config_version="manual_review",
        breakdown=score_breakdown,
    )


class PostAiDedupProcessor:
    def __init__(
        self,
        *,
        engine: WeightedDeduplicationEngine,
        announcement_repository: ParentChildAnnouncementRepository,
        manual_review_queue: ManualReviewQueue,
        parent_policy: ParentSelectionPolicy | None = None,
        technical_recorder: TechnicalMetricRecorder | None = None,
        event_queue: Any = None,
    ) -> None:
        self._engine = engine
        self._announcement_repository = announcement_repository
        self._manual_review_queue = manual_review_queue
        self._parent_policy = parent_policy or ParentSelectionPolicy()
        self._technical_recorder = technical_recorder
        self._event_queue = event_queue

    async def process(self, announcement: StructuredAnnouncement) -> DedupDecision:
        if announcement.status == "manual_review":
            reason = announcement.review_reason or "business_quality"
            decision = manual_review_decision(reason=reason)
            persisted = await self._announcement_repository.save_parent(announcement)
            await self._manual_review_queue.enqueue(
                announcement=persisted,
                reason=reason,
                decision=decision,
            )
            return decision
        decision = await self._engine.evaluate(announcement)
        await safe_record_technical_metric(
            self._technical_recorder,
            metric_name="dedup_decision",
            idempotency_key=f"dedup_decision:{announcement.announcement_id}",
            occurred_at=announcement.updated_at,
            component="post_ai_dedup",
            subject_id=announcement.announcement_id,
            metadata={
                "decision": decision.decision,
                "score": str(decision.score),
                "candidate_id": decision.candidate_id or "",
            },
        )
        if decision.decision in {"exact_duplicate", "high_confidence_duplicate"}:
            if decision.candidate_id is None:
                persisted = await self._announcement_repository.save_parent(announcement)
                await self._publish_persisted(persisted)
                await self._announcement_repository.record_decision(
                    announcement_id=persisted.announcement_id,
                    decision=decision,
                )
                return decision
            candidate = await self._announcement_repository.get(decision.candidate_id)
            if candidate is None:
                persisted = await self._announcement_repository.save_parent(announcement)
                await self._publish_persisted(persisted)
                await self._announcement_repository.record_decision(
                    announcement_id=persisted.announcement_id,
                    decision=decision,
                )
                return decision
            parent, child = self._parent_policy.select_parent(announcement, candidate)
            decision_announcement_id = announcement.announcement_id
            if parent.announcement_id == announcement.announcement_id:
                persisted_parent = await self._announcement_repository.save_parent(parent)
                await self._publish_persisted(persisted_parent)
                decision_announcement_id = persisted_parent.announcement_id
                if child.announcement_id != persisted_parent.announcement_id:
                    await self._announcement_repository.link_child(
                        child=child,
                        parent_id=persisted_parent.announcement_id,
                        decision=decision,
                        merge_policy=self._parent_policy.name,
                    )
            else:
                await self._announcement_repository.link_child(
                    child=child,
                    parent_id=parent.announcement_id,
                    decision=decision,
                    merge_policy=self._parent_policy.name,
                )
            await self._announcement_repository.record_decision(
                announcement_id=decision_announcement_id,
                decision=decision,
            )
        elif decision.decision == "possible_duplicate":
            persisted = await self._announcement_repository.save_parent(
                replace(announcement, status="manual_review")
            )
            await self._manual_review_queue.enqueue(
                announcement=persisted,
                reason="possible_duplicate",
                decision=decision,
            )
            await self._announcement_repository.record_decision(
                announcement_id=persisted.announcement_id,
                decision=decision,
            )
        else:
            persisted = await self._announcement_repository.save_parent(announcement)
            await self._publish_persisted(persisted)
            await self._announcement_repository.record_decision(
                announcement_id=persisted.announcement_id,
                decision=decision,
            )
        return decision

    async def _publish_persisted(self, announcement: StructuredAnnouncement) -> None:
        if self._event_queue is None or announcement.status != "active":
            return
        await self._event_queue.publish(
            QueueMessage(
                queue_name=ANNOUNCEMENT_PERSISTED_QUEUE,
                payload={
                    "idempotency_key": announcement.idempotency_key,
                    "source_id": announcement.source_id,
                    "source_channel_id": announcement.source_channel_id,
                    "source_message_id": announcement.source_message_id,
                    "occurred_at": announcement.occurred_at.isoformat(),
                    "canonical": announcement.canonical.model_dump(mode="json"),
                    "media": [item.__dict__ for item in announcement.media],
                },
                correlation_id=announcement.idempotency_key,
                metadata={
                    "idempotency_key": announcement.idempotency_key,
                    "schema_version": "announcement.persisted.v1",
                },
            )
        )


def merge_parent_fields(
    parent: StructuredAnnouncement,
    child: StructuredAnnouncement,
) -> tuple[StructuredAnnouncement, list[dict[str, object]]]:
    parent_data = parent.canonical.model_dump()
    child_data = child.canonical.model_dump()
    conflicts: list[dict[str, object]] = []
    for field_name, parent_value in parent_data.items():
        child_value = child_data[field_name]
        if _is_empty(parent_value) and not _is_empty(child_value):
            parent_data[field_name] = child_value
        elif (
            not _is_empty(parent_value)
            and not _is_empty(child_value)
            and parent_value != child_value
        ):
            conflicts.append(
                {
                    "field": field_name,
                    "parent": str(parent_value),
                    "child": str(child_value),
                }
            )
    updated_canonical = CanonicalListing.model_validate(parent_data)
    return replace(parent, canonical=updated_canonical), conflicts


def _prices_are_comparable(left: CanonicalListing, right: CanonicalListing) -> bool:
    if left.price_basis != "total" or right.price_basis != "total":
        return False
    if left.price_period == "one_time" or right.price_period == "one_time":
        return False
    if not left.currency or left.currency != right.currency:
        return False
    return left.price_normalized_monthly is not None and right.price_normalized_monthly is not None


def _canonical_completeness(canonical: CanonicalListing) -> int:
    values = canonical.model_dump().values()
    return sum(0 if _is_empty(value) else 1 for value in values)


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _casefold_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped.casefold() if stripped else None


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    normalized = value.casefold().replace(",", " ").replace("-", " ")
    return {token for token in normalized.split() if token}


def _description_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    normalized = normalize_listing_text(value)
    return _tokens(normalized) - extract_phone_numbers(value)


def dedup_scoring_config_from_settings(settings: Settings) -> DedupScoringConfig:
    return DedupScoringConfig(
        version=settings.dedup_config_version,
        exact_threshold=settings.dedup_exact_threshold,
        high_confidence_threshold=settings.dedup_high_confidence_threshold,
        possible_threshold=settings.dedup_possible_threshold,
        candidate_limit=settings.dedup_candidate_limit,
        source_url_weight=settings.dedup_source_url_weight,
        same_image_weight=settings.dedup_same_image_weight,
        district_weight=settings.dedup_district_weight,
        rooms_weight=settings.dedup_rooms_weight,
        area_weight=settings.dedup_area_weight,
        price_weight=settings.dedup_price_weight,
        floor_weight=settings.dedup_floor_weight,
        address_weight=settings.dedup_address_weight,
        phone_weight=settings.dedup_phone_weight,
        area_tolerance_sqm=Decimal(str(settings.dedup_area_tolerance_sqm)),
        price_tolerance_ratio=Decimal(str(settings.dedup_price_tolerance_ratio)),
        address_similarity_threshold=settings.dedup_address_similarity_threshold,
        description_similarity_threshold=settings.dedup_description_similarity_threshold,
        parent_selection_policy=settings.dedup_parent_selection_policy,
    )
