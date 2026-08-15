from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Protocol

from estateflow.services.ops_notifications import OpsNotificationService
from estateflow.services.telegram_listener import (
    RawTelegramEvent,
    TelegramForwardMetadata,
    TelegramMediaReference,
)

PreAiDecisionType = Literal[
    "proceed_to_ai",
    "high_confidence_duplicate",
    "needs_reviewable_signal",
]
DedupSignalType = Literal[
    "forward_origin",
    "text_hash",
    "near_text",
    "phone",
    "phash",
]

_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?998)?[\s()-]*(?:\d[\s()-]*){9,12}(?!\d)")
_HASHTAG_PATTERN = re.compile(r"(?<!\w)#\w+", re.UNICODE)
_WORD_PATTERN = re.compile(r"[\w$]+", re.UNICODE)


@dataclass(frozen=True)
class PreAiDedupConfig:
    near_text_threshold: float = 0.88
    phash_hamming_threshold: int = 8


@dataclass(frozen=True)
class RawEventReference:
    raw_event_id: str
    source_id: str
    source_channel_id: str
    source_message_id: str
    parent_reference: str | None = None


@dataclass(frozen=True)
class DedupSignal:
    signal_type: DedupSignalType
    matched: bool
    strength: float
    reason: str
    reference: RawEventReference | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PreAiDedupDecision:
    decision: PreAiDecisionType
    should_skip_ai: bool
    reasons: list[str]
    signals: list[DedupSignal]
    audit_reference: RawEventReference | None = None


@dataclass(frozen=True)
class RawEventDedupSignals:
    reference: RawEventReference
    forward_origin_key: str | None
    text_hash: str | None
    normalized_text: str | None
    phones: frozenset[str]
    media_phashes: tuple[str, ...]


class PreAiDedupSignalStore(Protocol):
    async def find_by_forward_origin(self, key: str) -> RawEventReference | None: ...

    async def find_by_text_hash(self, text_hash: str) -> RawEventReference | None: ...

    async def list_recent_signals(self) -> list[RawEventDedupSignals]: ...


class MediaPHashProvider(Protocol):
    async def phash(self, media: TelegramMediaReference) -> str | None: ...


class PreAiDedupAuditSink(Protocol):
    async def record(self, *, event: RawTelegramEvent, decision: PreAiDedupDecision) -> None: ...


class DisabledPreAiDedupAuditSink:
    async def record(self, *, event: RawTelegramEvent, decision: PreAiDedupDecision) -> None:
        return None


class InMemoryPreAiDedupAuditSink:
    def __init__(self) -> None:
        self.records: list[tuple[RawTelegramEvent, PreAiDedupDecision]] = []

    async def record(self, *, event: RawTelegramEvent, decision: PreAiDedupDecision) -> None:
        self.records.append((event, decision))


class StableMediaReferencePHashProvider:
    async def phash(self, media: TelegramMediaReference) -> str | None:
        if media.media_type not in {"photo", "image", "document"}:
            return None
        seed = media.access_hash or media.media_id
        if not seed:
            return None
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


class InMemoryPreAiDedupSignalStore:
    def __init__(self, signals: list[RawEventDedupSignals] | None = None) -> None:
        self._signals = list(signals or [])

    async def find_by_forward_origin(self, key: str) -> RawEventReference | None:
        for signal in self._signals:
            if signal.forward_origin_key == key:
                return signal.reference
        return None

    async def find_by_text_hash(self, text_hash: str) -> RawEventReference | None:
        for signal in self._signals:
            if signal.text_hash == text_hash:
                return signal.reference
        return None

    async def list_recent_signals(self) -> list[RawEventDedupSignals]:
        return list(self._signals)

    async def add(self, signals: RawEventDedupSignals) -> None:
        self._signals.append(signals)


class PreAiDedupFilter:
    def __init__(
        self,
        *,
        signal_store: PreAiDedupSignalStore,
        phash_provider: MediaPHashProvider,
        ops_notifier: OpsNotificationService,
        config: PreAiDedupConfig,
        audit_sink: PreAiDedupAuditSink | None = None,
    ) -> None:
        self._signal_store = signal_store
        self._phash_provider = phash_provider
        self._ops_notifier = ops_notifier
        self._config = config
        self._audit_sink = audit_sink or DisabledPreAiDedupAuditSink()

    async def evaluate(self, event: RawTelegramEvent) -> PreAiDedupDecision:
        signals: list[DedupSignal] = []
        event_signals = await build_raw_event_dedup_signals(
            event,
            phash_provider=self._phash_provider,
        )

        forward_reference = await self._match_forward_origin(event_signals)
        if forward_reference is not None:
            signals.append(
                DedupSignal(
                    signal_type="forward_origin",
                    matched=True,
                    strength=1.0,
                    reason="forward origin channel/post matched exactly",
                    reference=forward_reference,
                )
            )
            return await self._decision(
                event=event,
                decision="high_confidence_duplicate",
                reasons=["forward_origin_exact_match"],
                signals=signals,
                audit_reference=forward_reference,
            )

        text_hash_reference = await self._match_text_hash(event_signals)
        if text_hash_reference is not None:
            signals.append(
                DedupSignal(
                    signal_type="text_hash",
                    matched=True,
                    strength=1.0,
                    reason="normalized text SHA-256 matched exactly",
                    reference=text_hash_reference,
                    details={"text_hash": event_signals.text_hash or ""},
                )
            )
            return await self._decision(
                event=event,
                decision="high_confidence_duplicate",
                reasons=["normalized_text_hash_exact_match"],
                signals=signals,
                audit_reference=text_hash_reference,
            )

        signals.extend(await self._ambiguous_signals(event_signals))
        if any(signal.matched for signal in signals):
            return await self._decision(
                event=event,
                decision="needs_reviewable_signal",
                reasons=[signal.reason for signal in signals if signal.matched],
                signals=signals,
                audit_reference=next(
                    (signal.reference for signal in signals if signal.reference is not None),
                    None,
                ),
            )

        return await self._decision(
            event=event,
            decision="proceed_to_ai",
            reasons=["no_high_confidence_duplicate_signal"],
            signals=signals,
            audit_reference=None,
        )

    async def _match_forward_origin(
        self, event_signals: RawEventDedupSignals
    ) -> RawEventReference | None:
        if event_signals.forward_origin_key is None:
            return None
        return await self._signal_store.find_by_forward_origin(event_signals.forward_origin_key)

    async def _match_text_hash(
        self, event_signals: RawEventDedupSignals
    ) -> RawEventReference | None:
        if event_signals.text_hash is None:
            return None
        return await self._signal_store.find_by_text_hash(event_signals.text_hash)

    async def _ambiguous_signals(self, event_signals: RawEventDedupSignals) -> list[DedupSignal]:
        signals: list[DedupSignal] = []
        recent = await self._signal_store.list_recent_signals()
        near_text = _best_near_text_match(event_signals, recent)
        if near_text is not None and near_text[0] >= self._config.near_text_threshold:
            similarity, reference = near_text
            signals.append(
                DedupSignal(
                    signal_type="near_text",
                    matched=True,
                    strength=similarity,
                    reason="near_text_match_requires_ai_confirmation",
                    reference=reference,
                    details={"similarity": round(similarity, 4)},
                )
            )

        phash_match = _best_phash_match(event_signals, recent)
        if phash_match is not None and phash_match[0] <= self._config.phash_hamming_threshold:
            distance, reference = phash_match
            signals.append(
                DedupSignal(
                    signal_type="phash",
                    matched=True,
                    strength=1 - (distance / 64),
                    reason="phash_match_requires_ai_confirmation",
                    reference=reference,
                    details={"hamming_distance": distance},
                )
            )

        phone_match = _best_phone_match(event_signals, recent)
        if phone_match is not None:
            signals.append(
                DedupSignal(
                    signal_type="phone",
                    matched=True,
                    strength=0.15,
                    reason="same_phone_is_weak_signal_only",
                    reference=phone_match,
                    details={"phone_count": len(event_signals.phones)},
                )
            )
        return signals

    async def _decision(
        self,
        *,
        event: RawTelegramEvent,
        decision: PreAiDecisionType,
        reasons: list[str],
        signals: list[DedupSignal],
        audit_reference: RawEventReference | None,
    ) -> PreAiDedupDecision:
        dedup_decision = PreAiDedupDecision(
            decision=decision,
            should_skip_ai=decision == "high_confidence_duplicate",
            reasons=reasons,
            signals=signals,
            audit_reference=audit_reference,
        )
        await self._audit_sink.record(event=event, decision=dedup_decision)
        await self._ops_notifier.notify(
            severity="info",
            reason=(
                "pre_ai_dedup decision="
                f"{decision} source={event.source_id} signal_count={len(signals)}"
            ),
            correlation_id=event.correlation_id,
        )
        return dedup_decision


async def build_raw_event_dedup_signals(
    event: RawTelegramEvent,
    *,
    phash_provider: MediaPHashProvider,
) -> RawEventDedupSignals:
    normalized = normalize_listing_text(event.text)
    text_hash = text_sha256(normalized) if normalized else None
    media_phashes: list[str] = []
    for media in event.media:
        phash = await phash_provider.phash(media)
        if phash is not None:
            media_phashes.append(phash)
    return RawEventDedupSignals(
        reference=RawEventReference(
            raw_event_id=event.idempotency_key,
            source_id=event.source_id,
            source_channel_id=event.source_channel_id,
            source_message_id=event.source_message_id,
        ),
        forward_origin_key=forward_origin_key(event.forward_metadata),
        text_hash=text_hash,
        normalized_text=normalized or None,
        phones=frozenset(extract_phone_numbers(event.text or "")),
        media_phashes=tuple(media_phashes),
    )


def forward_origin_key(metadata: TelegramForwardMetadata | None) -> str | None:
    if (
        metadata is None
        or metadata.original_channel_id is None
        or metadata.original_message_id is None
    ):
        return None
    return f"{metadata.original_channel_id}:{metadata.original_message_id}"


def normalize_listing_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    phone_normalized = _PHONE_PATTERN.sub(_normalize_phone_match, normalized)
    without_hashtags = _HASHTAG_PATTERN.sub(" ", phone_normalized)
    alnum_space = "".join(
        char if char.isalnum() or char.isspace() or char in {"$", "+"} else " "
        for char in without_hashtags
    )
    return " ".join(alnum_space.split())


def _normalize_phone_match(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    if len(digits) == 9:
        digits = f"998{digits}"
    if len(digits) >= 12:
        return f"+{digits[-12:]}"
    return match.group()


def text_sha256(normalized_text: str) -> str:
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


def extract_phone_numbers(text: str) -> set[str]:
    phones: set[str] = set()
    for match in _PHONE_PATTERN.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if len(digits) == 9:
            digits = f"998{digits}"
        if len(digits) >= 12:
            phones.add(f"+{digits[-12:]}")
    return phones


def _best_near_text_match(
    event_signals: RawEventDedupSignals,
    candidates: list[RawEventDedupSignals],
) -> tuple[float, RawEventReference] | None:
    if event_signals.normalized_text is None:
        return None
    best: tuple[float, RawEventReference] | None = None
    for candidate in candidates:
        if candidate.normalized_text is None:
            continue
        similarity = jaccard_similarity(
            _tokenize(event_signals.normalized_text),
            _tokenize(candidate.normalized_text),
        )
        if best is None or similarity > best[0]:
            best = (similarity, candidate.reference)
    return best


def _best_phash_match(
    event_signals: RawEventDedupSignals,
    candidates: list[RawEventDedupSignals],
) -> tuple[int, RawEventReference] | None:
    best: tuple[int, RawEventReference] | None = None
    for phash in event_signals.media_phashes:
        for candidate in candidates:
            for candidate_phash in candidate.media_phashes:
                distance = hamming_distance_hex(phash, candidate_phash)
                if best is None or distance < best[0]:
                    best = (distance, candidate.reference)
    return best


def _best_phone_match(
    event_signals: RawEventDedupSignals,
    candidates: list[RawEventDedupSignals],
) -> RawEventReference | None:
    if not event_signals.phones:
        return None
    for candidate in candidates:
        if event_signals.phones & candidate.phones:
            return candidate.reference
    return None


def _tokenize(text: str) -> set[str]:
    return {match.group() for match in _WORD_PATTERN.finditer(text)}


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def hamming_distance_hex(left: str, right: str) -> int:
    left_int = int(left, 16)
    right_int = int(right, 16)
    return (left_int ^ right_int).bit_count()
