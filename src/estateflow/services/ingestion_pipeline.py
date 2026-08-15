from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from estateflow.services.analytics import TechnicalMetricRecorder, safe_record_technical_metric
from estateflow.services.pre_ai_dedup import (
    InMemoryPreAiDedupSignalStore,
    MediaPHashProvider,
    PreAiDedupFilter,
    build_raw_event_dedup_signals,
)
from estateflow.services.queue import AI_PROCESSING_QUEUE, PublishingQueue, QueueMessage
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
    ) -> None:
        self._dedup_filter = dedup_filter
        self._ai_queue = ai_queue
        self._signal_store = signal_store
        self._phash_provider = phash_provider
        self._technical_recorder = technical_recorder

    async def process_raw_queue_message(self, message: QueueMessage) -> bool:
        event = RawTelegramEvent.from_queue_payload(message.payload)
        decision = await self._dedup_filter.evaluate(event)
        if decision.should_skip_ai:
            return False

        await self._ai_queue.publish(
            QueueMessage(
                queue_name=AI_PROCESSING_QUEUE,
                payload={
                    **event.to_queue_payload(),
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
        await self._signal_store.add(
            await build_raw_event_dedup_signals(event, phash_provider=self._phash_provider)
        )
        return True
