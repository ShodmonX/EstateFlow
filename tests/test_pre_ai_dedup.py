from __future__ import annotations

from datetime import UTC, datetime

import pytest

from estateflow.application.core.config import Settings
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.pre_ai_dedup import (
    InMemoryPreAiDedupAuditSink,
    InMemoryPreAiDedupSignalStore,
    MediaPHashProvider,
    PreAiDedupConfig,
    PreAiDedupFilter,
    RawEventDedupSignals,
    RawEventReference,
    StableMediaReferencePHashProvider,
    build_raw_event_dedup_signals,
    extract_phone_numbers,
    normalize_listing_text,
    pre_ai_dedup_config_from_settings,
    text_sha256,
)
from estateflow.services.telegram_listener import (
    RawTelegramEvent,
    TelegramForwardMetadata,
    TelegramMediaReference,
)


class RecordingOpsNotifier(DisabledOpsNotificationService):
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


class FixedPHashProvider(MediaPHashProvider):
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    async def phash(self, media: TelegramMediaReference) -> str | None:
        return self._mapping.get(media.media_id)


def test_pre_ai_config_uses_its_dedicated_phash_threshold() -> None:
    settings = Settings(
        environment="test",
        pre_ai_near_text_threshold=0.91,
        pre_ai_phash_hamming_threshold=3,
        media_phash_hamming_threshold=11,
    )

    config = pre_ai_dedup_config_from_settings(settings)

    assert config.near_text_threshold == 0.91
    assert config.phash_hamming_threshold == 3


def _event(
    *,
    event_id: str = "telegram:-1001:10:created",
    source_id: str = "source-1",
    channel_id: str = "-1001",
    message_id: str = "10",
    text: str | None = "Yunusobod 2 xona 500$ +998 90 123 45 67",
    forward: TelegramForwardMetadata | None = None,
    media_id: str = "photo-1",
) -> RawTelegramEvent:
    return RawTelegramEvent(
        schema_version="telegram.raw.v1",
        event_type="created",
        idempotency_key=event_id,
        account_key="listener-a",
        source_id=source_id,
        source_channel_id=channel_id,
        source_message_id=message_id,
        source_identifier="@source",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        correlation_id=f"cid-{message_id}",
        text=text,
        forward_metadata=forward,
        media=[
            TelegramMediaReference(media_id=media_id, media_type="photo", mime_type="image/jpeg")
        ],
        source_message_ids=[message_id],
    )


async def _signals(
    event: RawTelegramEvent,
    *,
    phash_provider: MediaPHashProvider | None = None,
) -> RawEventDedupSignals:
    return await build_raw_event_dedup_signals(
        event,
        phash_provider=phash_provider or StableMediaReferencePHashProvider(),
    )


def _reference(raw_event_id: str = "existing") -> RawEventReference:
    return RawEventReference(
        raw_event_id=raw_event_id,
        source_id="source-old",
        source_channel_id="-2002",
        source_message_id="99",
        parent_reference="parent-1",
    )


def _filter(
    store: InMemoryPreAiDedupSignalStore,
    *,
    phash_provider: MediaPHashProvider | None = None,
    ops: RecordingOpsNotifier | None = None,
    audit_sink: InMemoryPreAiDedupAuditSink | None = None,
) -> PreAiDedupFilter:
    return PreAiDedupFilter(
        signal_store=store,
        phash_provider=phash_provider or StableMediaReferencePHashProvider(),
        ops_notifier=ops or RecordingOpsNotifier(),
        config=PreAiDedupConfig(near_text_threshold=0.70, phash_hamming_threshold=8),
        audit_sink=audit_sink,
    )


@pytest.mark.asyncio
async def test_forward_duplicate_skips_ai_with_audit_reference() -> None:
    audit_sink = InMemoryPreAiDedupAuditSink()
    existing = RawEventDedupSignals(
        reference=_reference(),
        forward_origin_key="-777:123",
        text_hash=None,
        normalized_text=None,
        phones=frozenset(),
        media_phashes=(),
    )
    event = _event(
        forward=TelegramForwardMetadata(
            original_channel_id="-777",
            original_message_id="123",
        )
    )

    decision = await _filter(
        InMemoryPreAiDedupSignalStore([existing]),
        audit_sink=audit_sink,
    ).evaluate(event)

    assert decision.decision == "high_confidence_duplicate"
    assert decision.should_skip_ai is True
    assert decision.audit_reference == existing.reference
    assert decision.reasons == ["forward_origin_exact_match"]
    assert audit_sink.records[0][1].audit_reference == existing.reference


@pytest.mark.asyncio
async def test_exact_normalized_text_duplicate_skips_ai() -> None:
    existing_text = "🏠 Yunusobod 2 xona 500$ #ijara\n+998 90 123 45 67"
    incoming_text = "YUNUSOBOD   2 xona 500$ +998901234567"
    normalized = normalize_listing_text(existing_text)
    existing = RawEventDedupSignals(
        reference=_reference(),
        forward_origin_key=None,
        text_hash=text_sha256(normalized),
        normalized_text=normalized,
        phones=frozenset({"+998901234567"}),
        media_phashes=(),
    )

    decision = await _filter(InMemoryPreAiDedupSignalStore([existing])).evaluate(
        _event(text=incoming_text)
    )

    assert decision.decision == "high_confidence_duplicate"
    assert decision.should_skip_ai is True
    assert decision.reasons == ["normalized_text_hash_exact_match"]


@pytest.mark.asyncio
async def test_phash_only_is_reviewable_and_never_skips_ai() -> None:
    provider = FixedPHashProvider({"photo-old": "0000000000000000", "photo-1": "0000000000000001"})
    existing = RawEventDedupSignals(
        reference=_reference(),
        forward_origin_key=None,
        text_hash=None,
        normalized_text="chilonzor 1 xona 300",
        phones=frozenset(),
        media_phashes=("0000000000000000",),
    )

    decision = await _filter(
        InMemoryPreAiDedupSignalStore([existing]),
        phash_provider=provider,
    ).evaluate(_event(text="Yunusobod 3 xona 900$", media_id="photo-1"))

    assert decision.decision == "needs_reviewable_signal"
    assert decision.should_skip_ai is False
    assert [signal.signal_type for signal in decision.signals] == ["phash"]


@pytest.mark.asyncio
async def test_near_text_only_is_reviewable_and_never_skips_ai() -> None:
    existing = await _signals(
        _event(
            event_id="existing",
            text="Yunusobod 2 xona 500 dollar oilaga beriladi",
            media_id="different-photo",
        )
    )

    decision = await _filter(InMemoryPreAiDedupSignalStore([existing])).evaluate(
        _event(text="Yunusobod 2 xona 500 dollar faqat oilaga")
    )

    assert decision.decision == "needs_reviewable_signal"
    assert decision.should_skip_ai is False
    assert any(signal.signal_type == "near_text" for signal in decision.signals)


@pytest.mark.asyncio
async def test_same_phone_only_proceeds_to_ai_as_weak_signal() -> None:
    existing = RawEventDedupSignals(
        reference=_reference(),
        forward_origin_key=None,
        text_hash=None,
        normalized_text="chilonzor 1 xona 300 dollar",
        phones=frozenset({"+998901234567"}),
        media_phashes=("aaaaaaaaaaaaaaaa",),
    )

    decision = await _filter(InMemoryPreAiDedupSignalStore([existing])).evaluate(
        _event(text="Yangi kvartira Qorasuv 4 xona 1200$ +998 90 123 45 67")
    )

    assert decision.decision == "needs_reviewable_signal"
    assert decision.should_skip_ai is False
    assert [signal.signal_type for signal in decision.signals] == ["phone"]
    assert decision.reasons == ["same_phone_is_weak_signal_only"]


@pytest.mark.asyncio
async def test_true_multi_signal_duplicate_still_requires_ai_without_exact_signals() -> None:
    provider = FixedPHashProvider({"photo-1": "0000000000000001"})
    existing = RawEventDedupSignals(
        reference=_reference(),
        forward_origin_key=None,
        text_hash=None,
        normalized_text="Yunusobod 2 xona 500 dollar oilaga beriladi".casefold(),
        phones=frozenset({"+998901234567"}),
        media_phashes=("0000000000000000",),
    )

    decision = await _filter(
        InMemoryPreAiDedupSignalStore([existing]),
        phash_provider=provider,
    ).evaluate(_event(text="Yunusobod 2 xona 500 dollar oilaga +998901234567"))

    assert decision.decision == "needs_reviewable_signal"
    assert decision.should_skip_ai is False
    assert {signal.signal_type for signal in decision.signals} == {"near_text", "phone", "phash"}


@pytest.mark.asyncio
async def test_no_signal_proceeds_to_ai() -> None:
    decision = await _filter(InMemoryPreAiDedupSignalStore()).evaluate(
        _event(text="Mutlaqo yangi e'lon, Sergeli 1 xona")
    )

    assert decision.decision == "proceed_to_ai"
    assert decision.should_skip_ai is False


def test_phone_extraction_normalizes_but_is_not_decision_logic() -> None:
    assert extract_phone_numbers("tel: 90 123-45-67") == {"+998901234567"}
