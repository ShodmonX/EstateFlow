from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from estateflow.services.analytics import (
    InMemoryTechnicalMetricRepository,
    TechnicalMetricRecorder,
)
from estateflow.services.ingestion_pipeline import PreAiIngestionProcessor
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.pre_ai_dedup import (
    InMemoryPreAiDedupSignalStore,
    PreAiDedupConfig,
    PreAiDedupFilter,
    StableMediaReferencePHashProvider,
)
from estateflow.services.queue import (
    AI_PROCESSING_QUEUE,
    SOURCE_PARSER_QUARANTINE_QUEUE,
    QueueMessage,
)
from estateflow.services.source_parsing import (
    CleanupConfig,
    FilterConfig,
    LabeledParseCase,
    ParserRecipe,
    SourceParserEvaluator,
    SourceParserRouter,
    SplitConfig,
    fanout_source_message_identity,
    validate_source_parser_binding,
)
from estateflow.services.telegram_listener import (
    RAW_EVENT_SCHEMA_VERSION,
    RawTelegramEvent,
    TelegramForwardMetadata,
    TelegramMediaReference,
)


def test_source_parser_binding_validation_uses_runtime_recipe_schema() -> None:
    recipe = validate_source_parser_binding(
        source_id="source.valid",
        parser_key="source.valid",
        parser_version="3",
        parser_config={
            "family": "single_listing",
            "score": {"minimum": 0.7},
        },
    )

    assert recipe.parser_key == "source.valid"
    assert recipe.parser_version == "3"
    assert recipe.score.minimum == 0.7
    with pytest.raises(ValueError):
        validate_source_parser_binding(
            source_id="source.invalid",
            parser_key="source.invalid",
            parser_version="1",
            parser_config={"unknown_recipe_field": True},
        )
    with pytest.raises(ValueError, match="binding identity fields"):
        validate_source_parser_binding(
            source_id="source.identity",
            parser_key="source.identity",
            parser_version="1",
            parser_config={
                "parser_key": "source.hidden",
                "family": "mixed_feed",
            },
        )


class RecordingQueue:
    def __init__(self) -> None:
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        self.messages.append(message)


def _event(
    text: str | None,
    *,
    event_id: str = "telegram:-1001:42:created",
    source_id: str = "source-a",
    parser_key: str | None = None,
    parser_version: str | None = None,
    parser_config: dict[str, object] | None = None,
    parser_mode: str = "active",
    with_media: bool = False,
    forward_metadata: TelegramForwardMetadata | None = None,
) -> RawTelegramEvent:
    return RawTelegramEvent(
        schema_version=RAW_EVENT_SCHEMA_VERSION,
        event_type="created",
        idempotency_key=event_id,
        account_key="listener-a",
        source_id=source_id,
        source_channel_id="-1001",
        source_message_id="42",
        source_identifier="@source_a",
        occurred_at=datetime(2026, 8, 20, tzinfo=UTC),
        correlation_id=f"correlation-{event_id}",
        text=text,
        forward_metadata=forward_metadata,
        media=(
            [TelegramMediaReference(media_id="photo-1", media_type="photo")]
            if with_media
            else []
        ),
        source_message_ids=["42"],
        source_url="https://t.me/source_a/42",
        parser_key=parser_key,
        parser_version=parser_version,
        parser_config=dict(parser_config or {}),
        parser_mode=parser_mode,  # type: ignore[arg-type]
    )


def _digest_recipe() -> ParserRecipe:
    return ParserRecipe(
        parser_key="source.a.digest",
        parser_version="2026.08.1",
        family="digest_blocks",
        cleanup=CleanupConfig(strip_patterns=(r"(?im)^Obuna bo['‘’]?ling.*$",)),
        filters=FilterConfig(include_any=(r"(?i)\b(?:xona|kvartira)\b",)),
        required_fields=("price", "rooms"),
        prompt_hints=("Each candidate is exactly one listing.",),
    )


def _processor(
    *,
    queue: RecordingQueue,
    router: SourceParserRouter,
    technical_recorder: TechnicalMetricRecorder | None = None,
) -> PreAiIngestionProcessor:
    signal_store = InMemoryPreAiDedupSignalStore()
    phash_provider = StableMediaReferencePHashProvider()
    return PreAiIngestionProcessor(
        dedup_filter=PreAiDedupFilter(
            signal_store=signal_store,
            phash_provider=phash_provider,
            ops_notifier=DisabledOpsNotificationService(),
            config=PreAiDedupConfig(),
        ),
        ai_queue=queue,
        signal_store=signal_store,
        phash_provider=phash_provider,
        technical_recorder=technical_recorder,
        parser_router=router,
    )


def test_recipe_validation_rejects_invalid_regex_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ParserRecipe.model_validate(
            {
                "parser_key": "bad key",
                "cleanup": {"strip_patterns": ("(",)},
            }
        )
    with pytest.raises(ValidationError):
        ParserRecipe.model_validate(
            {
                "parser_key": "valid.key",
                "family": "single_listing",
                "silently_ignored_typo": True,
            }
        )


@pytest.mark.parametrize(
    "unsafe_pattern",
    (
        r"(a+)+$",
        r"(listing)\1",
        r"(?=listing)listing",
        r"a{10001}",
    ),
)
def test_recipe_validation_rejects_unsafe_config_regexes(unsafe_pattern: str) -> None:
    with pytest.raises(ValidationError, match="unsafe regex"):
        ParserRecipe(
            parser_key="source.unsafe.regex",
            cleanup=CleanupConfig(strip_patterns=(unsafe_pattern,)),
        )


def test_recipe_validation_accepts_normal_and_anchored_split_regexes() -> None:
    recipe = ParserRecipe(
        parser_key="source.safe.regex",
        cleanup=CleanupConfig(strip_patterns=(r"(?im)^reklama uchun.*$",)),
        split=SplitConfig(patterns=(r"(?m)(?=^\s*#?\d+[.)]\s+)",)),
        filters=FilterConfig(include_any=(r"(?i)\b(?:kvartira|xonadon|hovli)\b",)),
    )

    assert recipe.cleanup.strip_patterns
    assert recipe.split.patterns


def test_digest_recipe_cleans_splits_and_produces_stable_candidate_ids() -> None:
    recipe = _digest_recipe()
    router = SourceParserRouter(recipes=[recipe])
    raw_text = (
        "1. Chilonzor 2 xona kvartira 500$ +998 90 111 22 33\n"
        "---\n"
        "2. Yunusobod 3 xona kvartira 700$ +998 90 444 55 66\n"
        "Obuna bo'ling @source_a"
    )
    event = _event(raw_text, parser_key=recipe.parser_key, parser_version=recipe.parser_version)

    first = router.parse(event)
    second = router.parse(event)

    assert first.decision == "emit"
    assert len(first.candidates) == 2
    assert [item.candidate_id for item in first.candidates] == [
        item.candidate_id for item in second.candidates
    ]
    assert first.candidates[0].idempotency_key != first.candidates[1].idempotency_key
    assert all("Obuna" not in (item.cleaned_text or "") for item in first.candidates)
    assert first.diagnostics["removed_pattern_count"] == 1


def test_split_can_drop_unsplit_text_when_no_pattern_matches() -> None:
    recipe = ParserRecipe(
        parser_key="source.strict.digest",
        family="digest_blocks",
        split=SplitConfig(
            patterns=(r"(?m)^---$",),
            keep_unsplit_if_no_match=False,
        ),
        filters=FilterConfig(empty_text_policy="drop"),
    )

    outcome = SourceParserRouter(recipes=[recipe]).parse(
        _event("Bitta ajratilmagan matn", parser_key=recipe.parser_key)
    )

    assert outcome.decision == "drop"
    assert outcome.candidates == ()
    assert outcome.diagnostics["applied_split_pattern_count"] == 0


def test_generic_digest_quarantines_preamble_instead_of_emitting_junk_candidate() -> None:
    outcome = SourceParserRouter().parse(
        _event(
            "Bugungi yangi kvartiralar e'lonlari\n"
            "1. Chilonzor 2 xona kvartira 500$ +998 90 111 22 33\n"
            "2. Yunusobod 3 xona kvartira 700$ +998 90 444 55 66",
            parser_key="digest_blocks",
        )
    )

    assert outcome.decision == "emit"
    assert len(outcome.candidates) == 2
    assert [item.index for item in outcome.candidates] == [1, 2]
    assert len(outcome.quarantined_candidates) == 1
    assert outcome.quarantined_candidates[0].cleaned_text == (
        "Bugungi yangi kvartiralar e'lonlari"
    )
    assert outcome.quarantined_candidates[0].reason == "digest_preamble"


def test_mixed_feed_drops_excluded_posts_and_quarantines_unknown_posts() -> None:
    router = SourceParserRouter()

    dropped = router.parse(_event("Ish qidiraman, reklama joylayman", parser_key="mixed_feed"))
    uncertain = router.parse(_event("Bugun havo yaxshi", parser_key="mixed_feed"))
    listing = router.parse(
        _event("Kvartira ijaraga beriladi, 2 xona, 500$", parser_key="mixed_feed")
    )

    assert dropped.decision == "drop"
    assert "exclude_marker_matched" in dropped.reasons
    assert uncertain.decision == "quarantine"
    assert listing.decision == "emit"


def test_unknown_parser_falls_back_but_invalid_inline_config_is_quarantined() -> None:
    router = SourceParserRouter()
    fallback = router.parse(_event("2 xona 500$", parser_key="source.not.registered"))
    invalid = router.parse(
        _event(
            "2 xona 500$",
            parser_key="source.invalid",
            parser_config={"cleanup": {"strip_patterns": ["("]}},
        )
    )

    assert fallback.decision == "emit"
    assert fallback.parser_key == "generic.single_listing"
    assert "generic_fallback" in fallback.reasons
    assert invalid.decision == "quarantine"
    assert invalid.reasons == ("invalid_parser_config",)


def test_album_and_stateful_buffer_outcomes_are_explicit() -> None:
    buffered_recipe = ParserRecipe(
        parser_key="source.reply.chain",
        family="buffered_thread",
    )
    router = SourceParserRouter(recipes=[buffered_recipe])

    album = router.parse(_event(None, parser_key="album_caption", with_media=True))
    buffered = router.parse(_event("davomi", parser_key="source.reply.chain"))

    assert album.decision == "emit"
    assert album.candidates[0].cleaned_text is None
    assert buffered.decision == "buffer"
    assert buffered.diagnostics["supported"] is False


@pytest.mark.asyncio
async def test_pre_ai_active_parser_emits_each_candidate_with_full_audit_metadata() -> None:
    recipe = _digest_recipe()
    router = SourceParserRouter(recipes=[recipe])
    queue = RecordingQueue()
    metric_repository = InMemoryTechnicalMetricRepository()
    processor = _processor(
        queue=queue,
        router=router,
        technical_recorder=TechnicalMetricRecorder(metric_repository),
    )
    raw_text = (
        "1. Chilonzor 2 xona kvartira 500$ +998 90 111 22 33\n"
        "2. Yunusobod 3 xona kvartira 700$ +998 90 444 55 66"
    )
    event = _event(raw_text, parser_key=recipe.parser_key, parser_version=recipe.parser_version)

    assert await processor.process_raw_queue_message(
        QueueMessage(
            queue_name="ingestion.raw_announcements",
            payload=event.to_queue_payload(),
            correlation_id=event.correlation_id,
        )
    )

    assert len(queue.messages) == 2
    assert all(message.queue_name == AI_PROCESSING_QUEUE for message in queue.messages)
    assert len({message.metadata["idempotency_key"] for message in queue.messages}) == 2
    for index, message in enumerate(queue.messages):
        parser_payload = message.payload["source_parsing"]
        assert parser_payload["candidate_index"] == index
        assert parser_payload["candidate_count"] == 2
        assert parser_payload["parser_key"] == recipe.parser_key
        assert parser_payload["parser_version"] == recipe.parser_version
        assert parser_payload["required_fields"] == ["price", "rooms"]
        assert parser_payload["provenance"]["raw_text"] == raw_text
        assert parser_payload["score"] > 0
        assert message.payload["pre_ai_dedup"]["decision"] == "proceed_to_ai"

    now = datetime.now(UTC)
    metrics = await metric_repository.list_events(
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(minutes=1),
    )
    parser_metrics = [item for item in metrics if item.metric_name == "source_parser_decision"]
    assert len(parser_metrics) == 1
    assert parser_metrics[0].metadata["decision"] == "emit"
    assert parser_metrics[0].metadata["candidate_count"] == "2"


@pytest.mark.asyncio
async def test_forwarded_digest_siblings_both_reach_ai_with_isolated_persistence_ids() -> None:
    recipe = _digest_recipe()
    queue = RecordingQueue()
    processor = _processor(
        queue=queue,
        router=SourceParserRouter(recipes=[recipe]),
    )
    forward_metadata = TelegramForwardMetadata(
        original_channel_id="-2002",
        original_message_id="99",
        original_channel_username="origin",
    )
    event = _event(
        "1. Chilonzor 2 xona kvartira 500$ +998 90 111 22 33\n"
        "2. Yunusobod 3 xona kvartira 700$ +998 90 444 55 66",
        parser_key=recipe.parser_key,
        parser_version=recipe.parser_version,
        forward_metadata=forward_metadata,
    )

    assert await processor.process_raw_queue_message(
        QueueMessage(
            queue_name="ingestion.raw_announcements",
            payload=event.to_queue_payload(),
            correlation_id=event.correlation_id,
        )
    )

    assert len(queue.messages) == 2
    payloads = [message.payload for message in queue.messages]
    assert [
        fanout_source_message_identity(payload["source_message_id"])
        for payload in payloads
    ] == [("42", 0), ("42", 1)]
    assert all(payload["source_message_ids"] == ["42"] for payload in payloads)
    assert all(payload["source_url"] == event.source_url for payload in payloads)
    assert all(payload["forward_metadata"]["original_message_id"] == "99" for payload in payloads)
    assert all(
        payload["parser_provenance"]["raw_source_message_id"] == "42"
        for payload in payloads
    )
    assert all(
        payload["pre_ai_dedup"]["decision"] == "proceed_to_ai"
        for payload in payloads
    )


def test_fanout_persistence_identity_is_stable_across_message_edits() -> None:
    recipe = _digest_recipe()
    router = SourceParserRouter(recipes=[recipe])
    created = _event(
        "1. Chilonzor 2 xona kvartira 500$\n"
        "2. Yunusobod 3 xona kvartira 700$",
        parser_key=recipe.parser_key,
        parser_version=recipe.parser_version,
    )
    edited = replace(
        created,
        event_type="edited",
        idempotency_key="telegram:-1001:42:edited",
        text=(
            "1. Chilonzor 2 xona kvartira 550$\n"
            "2. Yunusobod 3 xona kvartira 750$"
        ),
    )

    created_outcome = router.parse(created)
    edited_outcome = router.parse(edited)
    created_events = [
        candidate.to_event(created, force_source_identity_isolation=True)
        for candidate in created_outcome.candidates
    ]
    edited_events = [
        candidate.to_event(edited, force_source_identity_isolation=True)
        for candidate in edited_outcome.candidates
    ]

    assert [item.source_message_id for item in created_events] == [
        item.source_message_id for item in edited_events
    ]
    assert [item.candidate_id for item in created_outcome.candidates] != [
        item.candidate_id for item in edited_outcome.candidates
    ]
    assert all(
        candidate.diagnostics["source_identity_reorder_sensitive"]
        for candidate in created_outcome.candidates
    )


@pytest.mark.asyncio
async def test_shadow_mode_audits_drop_but_preserves_legacy_emission() -> None:
    queue = RecordingQueue()
    processor = _processor(queue=queue, router=SourceParserRouter())
    event = _event(
        "Ish qidiraman, reklama joylayman",
        parser_key="mixed_feed",
        parser_mode="shadow",
    )

    assert await processor.process_raw_queue_message(
        QueueMessage(
            queue_name="ingestion.raw_announcements",
            payload=event.to_queue_payload(),
            correlation_id=event.correlation_id,
        )
    )

    assert len(queue.messages) == 1
    emitted = queue.messages[0]
    assert emitted.payload["text"] == event.text
    assert emitted.metadata["idempotency_key"] == event.idempotency_key
    assert emitted.payload["source_parsing"]["mode"] == "shadow"
    assert emitted.payload["source_parsing"]["shadow"]["decision"] == "drop"
    assert emitted.payload["source_parsing"]["shadow"]["candidate_count"] == 0


@pytest.mark.asyncio
async def test_unavailable_active_buffer_fails_open_without_losing_raw_event() -> None:
    recipe = ParserRecipe(parser_key="source.reply.chain", family="buffered_thread")
    queue = RecordingQueue()
    metric_repository = InMemoryTechnicalMetricRepository()
    processor = _processor(
        queue=queue,
        router=SourceParserRouter(recipes=[recipe]),
        technical_recorder=TechnicalMetricRecorder(metric_repository),
    )
    event = _event("Davomi keyingi xabarda", parser_key=recipe.parser_key)

    assert await processor.process_raw_queue_message(
        QueueMessage(
            queue_name="ingestion.raw_announcements",
            payload=event.to_queue_payload(),
            correlation_id=event.correlation_id,
        )
    )

    assert len(queue.messages) == 1
    emitted = queue.messages[0]
    assert emitted.payload["text"] == event.text
    assert emitted.metadata["idempotency_key"] == event.idempotency_key
    assert "buffer_unavailable_fail_open" in emitted.payload["source_parsing"]["reasons"]
    assert emitted.payload["source_parsing"]["diagnostics"]["fail_open_from_decision"] == (
        "buffer"
    )

    now = datetime.now(UTC)
    metrics = await metric_repository.list_events(
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(minutes=1),
    )
    parser_metric = next(item for item in metrics if item.metric_name == "source_parser_decision")
    assert parser_metric.metadata["decision"] == "buffer"


@pytest.mark.asyncio
async def test_active_quarantine_is_retained_without_calling_ai() -> None:
    queue = RecordingQueue()
    processor = _processor(queue=queue, router=SourceParserRouter())
    event = _event("Bugun havo yaxshi", parser_key="mixed_feed")

    assert not await processor.process_raw_queue_message(
        QueueMessage(
            queue_name="ingestion.raw_announcements",
            payload=event.to_queue_payload(),
            correlation_id=event.correlation_id,
        )
    )

    assert len(queue.messages) == 1
    retained = queue.messages[0]
    assert retained.queue_name == SOURCE_PARSER_QUARANTINE_QUEUE
    assert retained.payload["text"] == event.text
    audit = retained.payload["source_parsing_quarantine"]
    assert audit["decision"] == "quarantine"
    assert audit["candidate"] is not None
    assert audit["candidate"]["cleaned_text"] == event.text


@pytest.mark.asyncio
async def test_partial_digest_quarantine_retains_rejected_segment_and_emits_listing() -> None:
    recipe = ParserRecipe(
        parser_key="source.partial.digest",
        family="digest_blocks",
        split=SplitConfig(patterns=(r"(?m)^---$",)),
        filters=FilterConfig(
            include_any=(r"(?i)\bkvartira\b",),
            unmatched_policy="quarantine",
        ),
    )
    queue = RecordingQueue()
    processor = _processor(
        queue=queue,
        router=SourceParserRouter(recipes=[recipe]),
    )
    event = _event(
        "Chilonzor 2 xona kvartira 500$\n---\nKanal adminidan oddiy xabar",
        parser_key=recipe.parser_key,
    )

    assert await processor.process_raw_queue_message(
        QueueMessage(
            queue_name="ingestion.raw_announcements",
            payload=event.to_queue_payload(),
            correlation_id=event.correlation_id,
        )
    )

    assert [item.queue_name for item in queue.messages] == [
        SOURCE_PARSER_QUARANTINE_QUEUE,
        AI_PROCESSING_QUEUE,
    ]
    retained = queue.messages[0].payload["source_parsing_quarantine"]
    assert retained["decision"] == "emit"
    assert retained["candidate"]["candidate_index"] == 1
    assert retained["candidate"]["cleaned_text"] == "Kanal adminidan oddiy xabar"
    assert queue.messages[1].payload["text"] == "Chilonzor 2 xona kvartira 500$"


@pytest.mark.asyncio
async def test_active_drop_stops_before_dedup_and_old_payload_still_passes() -> None:
    queue = RecordingQueue()
    processor = _processor(queue=queue, router=SourceParserRouter())
    dropped = _event("Ish qidiraman, reklama", parser_key="mixed_feed")
    old_event = _event("Chilonzor 2 xona 500$ +998 90 111 22 33", event_id="old:1")
    old_payload = old_event.to_queue_payload()
    for key in ("parser_key", "parser_version", "parser_config", "parser_mode"):
        old_payload.pop(key, None)

    assert not await processor.process_raw_queue_message(
        QueueMessage(
            queue_name="ingestion.raw_announcements",
            payload=dropped.to_queue_payload(),
            correlation_id=dropped.correlation_id,
        )
    )
    assert await processor.process_raw_queue_message(
        QueueMessage(
            queue_name="ingestion.raw_announcements",
            payload=old_payload,
            correlation_id=old_event.correlation_id,
        )
    )

    assert len(queue.messages) == 1
    assert queue.messages[0].metadata["idempotency_key"] == old_event.idempotency_key
    assert queue.messages[0].payload["source_message_id"] == old_event.source_message_id
    assert queue.messages[0].payload["source_message_ids"] == old_event.source_message_ids
    assert queue.messages[0].payload["source_parsing"]["parser_key"] == (
        "generic.single_listing"
    )


def test_replay_evaluator_reports_candidate_precision_recall_and_drop_accuracy() -> None:
    recipe = _digest_recipe()
    router = SourceParserRouter(recipes=[recipe])
    evaluator = SourceParserEvaluator(router)
    digest = _event(
        "1. Chilonzor 2 xona kvartira 500$\n2. Yunusobod 3 xona kvartira 700$",
        parser_key=recipe.parser_key,
        parser_version=recipe.parser_version,
    )
    excluded = _event(
        "Ish qidiraman, reklama",
        event_id="evaluation:drop",
        parser_key="mixed_feed",
    )

    report = evaluator.evaluate(
        [
            LabeledParseCase(
                case_id="digest",
                event=digest,
                expected_decision="emit",
                expected_candidate_texts=(
                    "1. Chilonzor 2 xona kvartira 500$",
                    "2. Yunusobod 3 xona kvartira 700$",
                ),
            ),
            LabeledParseCase(
                case_id="drop",
                event=excluded,
                expected_decision="drop",
                expected_candidate_count=0,
            ),
        ]
    )

    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.decision_accuracy == 1.0
    assert report.drop_accuracy == 1.0
    assert report.candidate_count_accuracy == 1.0


def test_replay_count_only_label_does_not_inflate_content_precision() -> None:
    event = _event("Wrong candidate content")
    report = SourceParserEvaluator(SourceParserRouter()).evaluate(
        [
            LabeledParseCase(
                case_id="count-only",
                event=event,
                expected_decision="emit",
                expected_candidate_count=1,
            )
        ]
    )

    assert report.candidate_count_accuracy == 1.0
    assert report.true_positive_candidates == 0
    assert report.precision is None
    assert report.recall is None
    assert report.labeled_candidate_case_count == 0
