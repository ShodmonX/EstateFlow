from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime

from estateflow.services.analytics import TechnicalMetricRecorder, safe_record_technical_metric
from estateflow.services.pre_ai_dedup import (
    InMemoryPreAiDedupSignalStore,
    MediaPHashProvider,
    PreAiDedupDecision,
    PreAiDedupFilter,
    build_raw_event_dedup_signals,
)
from estateflow.services.queue import (
    AI_PROCESSING_QUEUE,
    SOURCE_PARSER_QUARANTINE_QUEUE,
    PublishingQueue,
    QueueMessage,
)
from estateflow.services.source_parsing import (
    ParsedCandidate,
    ParseOutcome,
    QuarantinedCandidate,
    SourceParserRouter,
    build_passthrough_outcome,
)
from estateflow.services.telegram_listener import RawTelegramEvent


class PreAiIngestionProcessor:
    def __init__(
        self,
        *,
        dedup_filter: PreAiDedupFilter,
        ai_queue: PublishingQueue,
        signal_store: InMemoryPreAiDedupSignalStore,
        phash_provider: MediaPHashProvider,
        technical_recorder: TechnicalMetricRecorder | None = None,
        parser_router: SourceParserRouter | None = None,
    ) -> None:
        self._dedup_filter = dedup_filter
        self._ai_queue = ai_queue
        self._signal_store = signal_store
        self._phash_provider = phash_provider
        self._technical_recorder = technical_recorder
        self._parser_router = parser_router or SourceParserRouter()

    async def process_raw_queue_message(self, message: QueueMessage) -> bool:
        raw_event = RawTelegramEvent.from_queue_payload(message.payload)
        parsed = self._parser_router.parse(raw_event, payload=message.payload)
        await self._record_parser_metric(raw_event, parsed)
        if parsed.mode == "active":
            await self._publish_quarantines(
                message=message,
                raw_event=raw_event,
                outcome=parsed,
            )

        shadow_outcome: ParseOutcome | None = None
        effective = parsed
        if parsed.mode == "shadow":
            shadow_outcome = parsed
            effective = build_passthrough_outcome(
                raw_event,
                parser_key=parsed.parser_key,
                parser_version=parsed.parser_version,
                mode="shadow",
                reasons=("shadow_legacy_emission",),
            )
        elif parsed.decision == "buffer":
            effective = build_passthrough_outcome(
                raw_event,
                parser_key=parsed.parser_key,
                parser_version=parsed.parser_version,
                mode="active",
                reasons=("buffer_unavailable_fail_open", *parsed.reasons),
            )
            effective = replace(
                effective,
                diagnostics={
                    **parsed.diagnostics,
                    "fail_open_from_decision": "buffer",
                },
            )
        if effective.decision != "emit":
            return False

        published = False
        candidate_count = len(effective.candidates)
        for candidate in effective.candidates:
            event = candidate.to_event(
                raw_event,
                force_source_identity_isolation=candidate_count > 1,
            )
            decision = await self._dedup_filter.evaluate(event)
            if decision.should_skip_ai:
                continue

            await self._publish_candidate(
                message=message,
                raw_event=raw_event,
                event=event,
                candidate=candidate,
                candidate_count=candidate_count,
                outcome=effective,
                shadow_outcome=shadow_outcome,
                pre_ai_decision=decision,
            )
            await self._signal_store.add(
                await build_raw_event_dedup_signals(
                    event,
                    phash_provider=self._phash_provider,
                )
            )
            published = True
        return published

    async def _publish_quarantines(
        self,
        *,
        message: QueueMessage,
        raw_event: RawTelegramEvent,
        outcome: ParseOutcome,
    ) -> None:
        quarantined: tuple[QuarantinedCandidate | None, ...]
        if outcome.quarantined_candidates:
            quarantined = tuple(outcome.quarantined_candidates)
        elif outcome.decision == "quarantine":
            quarantined = (None,)
        else:
            return

        for candidate in quarantined:
            identity = candidate.candidate_id if candidate is not None else "raw-event"
            quarantine_id = (
                f"source-parser-quarantine:{raw_event.idempotency_key}:"
                f"{outcome.parser_key}:{outcome.parser_version}:{identity}"
            )
            candidate_payload = None
            if candidate is not None:
                candidate_payload = {
                    **candidate.to_audit_payload(),
                    "cleaned_text": candidate.cleaned_text,
                }
            await self._ai_queue.publish(
                QueueMessage(
                    queue_name=SOURCE_PARSER_QUARANTINE_QUEUE,
                    payload={
                        **raw_event.to_queue_payload(),
                        "source_parsing_quarantine": {
                            **outcome.to_audit_payload(),
                            "quarantine_id": quarantine_id,
                            "candidate": candidate_payload,
                            "origin_queue": message.queue_name,
                        },
                    },
                    correlation_id=raw_event.correlation_id,
                    metadata={
                        "idempotency_key": quarantine_id,
                        "source_id": raw_event.source_id,
                        "source_parser_key": outcome.parser_key,
                        "source_parser_version": outcome.parser_version,
                    },
                )
            )

    async def _publish_candidate(
        self,
        *,
        message: QueueMessage,
        raw_event: RawTelegramEvent,
        event: RawTelegramEvent,
        candidate: ParsedCandidate,
        candidate_count: int,
        outcome: ParseOutcome,
        shadow_outcome: ParseOutcome | None,
        pre_ai_decision: PreAiDedupDecision,
    ) -> None:
        decision = pre_ai_decision
        await self._ai_queue.publish(
            QueueMessage(
                queue_name=AI_PROCESSING_QUEUE,
                payload={
                    **event.to_queue_payload(),
                    "source_parsing": candidate.to_queue_metadata(
                        raw_event=raw_event,
                        candidate_count=candidate_count,
                        decision=outcome.decision,
                        mode=outcome.mode,
                        outcome_reasons=outcome.reasons,
                        outcome_diagnostics=outcome.diagnostics,
                        shadow_outcome=shadow_outcome,
                    ),
                    "pre_ai_dedup": {
                        "decision": decision.decision,
                        "should_skip_ai": decision.should_skip_ai,
                        "reasons": decision.reasons,
                        "signals": [asdict(signal) for signal in decision.signals],
                        "audit_reference": (
                            asdict(decision.audit_reference)
                            if decision.audit_reference is not None
                            else None
                        ),
                    },
                },
                correlation_id=event.correlation_id,
                metadata={
                    "idempotency_key": event.idempotency_key,
                    "schema_version": event.schema_version,
                    "pre_ai_decision": decision.decision,
                    "source_parser_key": candidate.parser_key,
                    "source_parser_version": candidate.parser_version,
                    "source_parser_candidate_id": candidate.candidate_id,
                },
            )
        )
        current = datetime.now(UTC)
        await safe_record_technical_metric(
            self._technical_recorder,
            metric_name="queue_delay_ms",
            idempotency_key=f"queue_delay:{event.idempotency_key}",
            occurred_at=current,
            value=max(0.0, (current - event.occurred_at).total_seconds() * 1000),
            component="pre_ai_ingestion",
            subject_id=event.idempotency_key,
            metadata={"queue_name": message.queue_name},
        )

    async def _record_parser_metric(
        self,
        event: RawTelegramEvent,
        outcome: ParseOutcome,
    ) -> None:
        await safe_record_technical_metric(
            self._technical_recorder,
            metric_name="source_parser_decision",
            idempotency_key=(
                f"source_parser:{event.idempotency_key}:"
                f"{outcome.parser_key}:{outcome.parser_version}:{outcome.mode}"
            ),
            occurred_at=datetime.now(UTC),
            value=float(len(outcome.candidates)),
            component="source_parser",
            subject_id=event.source_id,
            metadata={
                "decision": outcome.decision,
                "mode": outcome.mode,
                "parser_key": outcome.parser_key,
                "parser_version": outcome.parser_version,
                "parser_family": outcome.parser_family,
                "candidate_count": str(len(outcome.candidates)),
                "quarantined_candidate_count": str(
                    len(outcome.quarantined_candidates)
                ),
                "reasons": ",".join(outcome.reasons)[:500],
            },
        )
