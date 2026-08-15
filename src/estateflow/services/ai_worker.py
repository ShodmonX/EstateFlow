from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from estateflow.contracts.events import POST_AI_DEDUP_QUEUE
from estateflow.services.ai_client import (
    LLMAllModelsFailedError,
    LLMError,
    LLMMessage,
    LLMRequest,
    ModelFallbackLLMClient,
)
from estateflow.services.analytics import TechnicalMetricRecorder, safe_record_technical_metric
from estateflow.services.audience_tags import AudienceTagService
from estateflow.services.extraction import (
    CanonicalListing,
    ListingExtractionRaw,
    build_listing_prompt,
    listing_json_schema,
    normalize_listing,
)
from estateflow.services.media_storage import MediaStorageService, StorageUploadError, StoredMedia
from estateflow.services.ops_notifications import OpsNotificationService
from estateflow.services.queue import QueueMessage
from estateflow.services.telegram_listener import RawTelegramEvent

__all__ = ["AiExtractionWorker", "AiProcessingRecord", "POST_AI_DEDUP_QUEUE"]

ProcessingStatus = Literal["succeeded", "manual_review", "failed"]


@dataclass(frozen=True)
class AiProcessingRecord:
    idempotency_key: str
    status: ProcessingStatus
    correlation_id: str
    canonical: CanonicalListing | None = None
    media: list[StoredMedia] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    error_type: str | None = None
    failure_reason: str | None = None
    post_ai_published: bool = False


class AiProcessingRepository(Protocol):
    async def get(self, idempotency_key: str) -> AiProcessingRecord | None: ...

    async def save(self, record: AiProcessingRecord) -> None: ...


class InMemoryAiProcessingRepository:
    def __init__(self) -> None:
        self.records: dict[str, AiProcessingRecord] = {}

    async def get(self, idempotency_key: str) -> AiProcessingRecord | None:
        return self.records.get(idempotency_key)

    async def save(self, record: AiProcessingRecord) -> None:
        self.records[record.idempotency_key] = record


class AiExtractionWorker:
    def __init__(
        self,
        *,
        llm_client: ModelFallbackLLMClient,
        media_service: MediaStorageService,
        repository: AiProcessingRepository,
        post_ai_queue: Any,
        ops_notifier: OpsNotificationService,
        prompt_version: str,
        min_confidence: float,
        rental_min_confidence: float = 0.60,
        audience_tag_service: AudienceTagService | None = None,
        technical_recorder: TechnicalMetricRecorder | None = None,
        release_controls: Any = None,
        ai_vision_enabled: bool = True,
    ) -> None:
        self._llm_client = llm_client
        self._media_service = media_service
        self._repository = repository
        self._post_ai_queue = post_ai_queue
        self._ops_notifier = ops_notifier
        self._prompt_version = prompt_version
        self._min_confidence = min_confidence
        self._rental_min_confidence = rental_min_confidence
        self._audience_tag_service = audience_tag_service
        self._technical_recorder = technical_recorder
        self._release_controls = release_controls
        self._ai_vision_enabled = ai_vision_enabled

    async def process_raw_queue_message(self, message: QueueMessage) -> AiProcessingRecord:
        event = RawTelegramEvent.from_queue_payload(message.payload)
        existing = await self._repository.get(event.idempotency_key)
        if existing is not None and existing.status == "succeeded" and existing.post_ai_published:
            return existing
        if existing is not None and existing.status == "succeeded":
            await self._publish_post_ai(event=event, record=existing, pre_ai=message.payload)
            existing = replace(existing, post_ai_published=True)
            await self._repository.save(existing)
            return existing
        if existing is not None and existing.status == "manual_review":
            if not existing.post_ai_published:
                await self._publish_post_ai(event=event, record=existing, pre_ai=message.payload)
                existing = replace(existing, post_ai_published=True)
                await self._repository.save(existing)
            return existing

        try:
            stored_media = await self._media_service.prepare_and_store(
                idempotency_key=event.idempotency_key,
                media_references=event.media,
            )
            ai_vision_active = self._ai_vision_enabled
            if self._release_controls is not None:
                try:
                    flags = await self._release_controls.flags()
                    ai_vision_active = flags.ai_vision
                except Exception:
                    pass

            vision_required = bool(stored_media) and ai_vision_active
            prompt_audience_tags = (
                await self._audience_tag_service.prompt_tags(limit=15)
                if self._audience_tag_service is not None
                else None
            )
            prompt_messages = build_listing_prompt(
                raw_text=event.text,
                prompt_version=self._prompt_version,
                vision_required=vision_required,
                media_count=len(stored_media) if ai_vision_active else 0,
                audience_tags=prompt_audience_tags,
            )
            llm_request = LLMRequest(
                messages=[
                    LLMMessage(role="system", content=prompt_messages[0]["content"]),
                    LLMMessage(role="user", content=prompt_messages[1]["content"]),
                ],
                json_schema=listing_json_schema(),
                correlation_id=event.correlation_id,
                min_confidence=self._min_confidence,
            )
            response, attempts = await self._llm_client.complete_json(llm_request)
            await self._record_ai_attempt_metrics(
                idempotency_key=event.idempotency_key,
                correlation_id=event.correlation_id,
                attempts=attempts,
            )
            raw = ListingExtractionRaw.model_validate(response.content)
            rental_confidence = (
                raw.rental_confidence
                if raw.rental_confidence is not None
                else response.confidence
            )
            is_rental = raw.is_rental_announcement
            if is_rental is None:
                # Backward-compatible interpretation for older model responses;
                # new prompts always return the explicit boolean.
                is_rental = raw.listing_type != "sale"
            if not is_rental or rental_confidence <= self._rental_min_confidence:
                reason = (
                    "not_a_rental_announcement"
                    if not is_rental
                    else "rental_confidence_below_threshold"
                )
                record = AiProcessingRecord(
                    idempotency_key=event.idempotency_key,
                    status="failed",
                    correlation_id=event.correlation_id,
                    attempts=[asdict(attempt) for attempt in attempts],
                    failure_reason=reason,
                )
                await self._repository.save(record)
                await self._ops_notifier.notify(
                    severity="info",
                    reason=(
                        "ai_processing rejected_before_extraction "
                        f"source={event.source_id} reason={reason} "
                        f"rental_confidence={rental_confidence:.3f}"
                    ),
                    correlation_id=event.correlation_id,
                )
                return record
            audience_assumptions: list[str] = []
            allowed_audience_tags: frozenset[str] | None = None
            if self._audience_tag_service is not None:
                resolved_tags = await self._audience_tag_service.resolve_for_canonical(
                    raw.audience_tags
                )
                resolved_excluded = await self._audience_tag_service.resolve_for_canonical(
                    raw.audience_excluded_tags
                )
                raw = raw.model_copy(
                    update={
                        "audience_tags": resolved_tags["approved_tags"],
                        "audience_excluded_tags": resolved_excluded["approved_tags"],
                    }
                )
                audience_assumptions = [
                    *resolved_tags["audit_assumptions"],
                    *resolved_excluded["audit_assumptions"],
                ]
                allowed_audience_tags = frozenset(
                    {
                        *resolved_tags["approved_tags"],
                        *resolved_excluded["approved_tags"],
                    }
                )
            canonical = normalize_listing(
                raw,
                prompt_version=self._prompt_version,
                source_text=event.text,
                vision_required=vision_required,
                allowed_audience_tags=allowed_audience_tags,
                extra_assumptions=audience_assumptions,
            )
            has_essential_fields = (
                canonical.price is not None
                or canonical.rooms is not None
                or canonical.district is not None
                or bool(canonical.phone_numbers)
            )
            is_junk_text = len(canonical.description.strip()) < 5 or not has_essential_fields
            AUTO_REJECT_THRESHOLD = 0.40

            status: ProcessingStatus
            if canonical.confidence < AUTO_REJECT_THRESHOLD or (
                is_junk_text and canonical.confidence < 0.60
            ):
                status = "failed"
                failure_reason = (
                    "rejected_not_real_estate_listing"
                    if not has_essential_fields
                    else "low_relevance_score"
                )
            elif (
                canonical.confidence < self._min_confidence
                or is_junk_text
            ):
                status = "manual_review"
                failure_reason = (
                    "missing_essential_fields"
                    if is_junk_text
                    else "low_confidence"
                )
            else:
                status = "succeeded"
                failure_reason = None

            record = AiProcessingRecord(
                idempotency_key=event.idempotency_key,
                status=status,
                correlation_id=event.correlation_id,
                canonical=canonical,
                media=stored_media,
                attempts=[asdict(attempt) for attempt in attempts],
            )
            if status == "succeeded":
                await self._repository.save(record)
                await self._publish_post_ai(event=event, record=record, pre_ai=message.payload)
                record = replace(record, post_ai_published=True)
                await self._repository.save(record)
                await self._ops_notifier.notify(
                    severity="info",
                    reason=(
                        "ai_processing succeeded "
                        f"source={event.source_id} media_count={len(stored_media)}"
                    ),
                    correlation_id=event.correlation_id,
                )
            elif status == "failed":
                record = replace(record, failure_reason=failure_reason)
                await self._repository.save(record)
                await self._ops_notifier.notify(
                    severity="warning",
                    reason=(
                        "ai_processing auto_rejected "
                        f"source={event.source_id} reason={record.failure_reason}"
                    ),
                    correlation_id=event.correlation_id,
                )
            else:
                record = replace(record, failure_reason=failure_reason)
                await self._repository.save(record)
                await self._publish_post_ai(event=event, record=record, pre_ai=message.payload)
                record = replace(record, post_ai_published=True)
                await self._repository.save(record)
                await self._ops_notifier.notify(
                    severity="warning",
                    reason=(
                        "ai_processing manual_review "
                        f"source={event.source_id} reason={record.failure_reason}"
                    ),
                    correlation_id=event.correlation_id,
                )
            return record
        except LLMAllModelsFailedError as exc:
            await self._record_ai_attempt_metrics(
                idempotency_key=event.idempotency_key,
                correlation_id=event.correlation_id,
                attempts=exc.attempts,
            )
            record = AiProcessingRecord(
                idempotency_key=event.idempotency_key,
                status="failed",
                correlation_id=event.correlation_id,
                attempts=[asdict(attempt) for attempt in exc.attempts],
                error_type=type(exc).__name__,
                failure_reason="all_models_failed",
            )
            await self._repository.save(record)
            await self._ops_notifier.notify(
                severity="critical",
                reason=f"ai_processing failed source={event.source_id} error={type(exc).__name__}",
                correlation_id=event.correlation_id,
            )
            return record
        except StorageUploadError as exc:
            await safe_record_technical_metric(
                self._technical_recorder,
                metric_name="ai_validation_failure",
                idempotency_key=f"ai_storage_failure:{event.idempotency_key}",
                component="ai_worker",
                subject_id=event.idempotency_key,
                metadata={
                    "failure_reason": "storage_retryable" if exc.retryable else "storage_permanent"
                },
            )
            record = AiProcessingRecord(
                idempotency_key=event.idempotency_key,
                status="failed",
                correlation_id=event.correlation_id,
                error_type=type(exc).__name__,
                failure_reason="storage_retryable" if exc.retryable else "storage_permanent",
            )
            await self._repository.save(record)
            await self._ops_notifier.notify(
                severity="critical",
                reason=(
                    "ai_processing storage_failure "
                    f"source={event.source_id} retryable={exc.retryable}"
                ),
                correlation_id=event.correlation_id,
            )
            return record
        except (LLMError, RuntimeError, ValidationError) as exc:
            await safe_record_technical_metric(
                self._technical_recorder,
                metric_name="ai_validation_failure",
                idempotency_key=f"ai_validation_failure:{event.idempotency_key}",
                component="ai_worker",
                subject_id=event.idempotency_key,
                metadata={"error_type": type(exc).__name__},
            )
            record = AiProcessingRecord(
                idempotency_key=event.idempotency_key,
                status="failed",
                correlation_id=event.correlation_id,
                error_type=type(exc).__name__,
                failure_reason=type(exc).__name__,
            )
            await self._repository.save(record)
            await self._ops_notifier.notify(
                severity="critical",
                reason=f"ai_processing failed source={event.source_id} error={type(exc).__name__}",
                correlation_id=event.correlation_id,
            )
            return record

    async def _record_ai_attempt_metrics(
        self,
        *,
        idempotency_key: str,
        correlation_id: str,
        attempts: list[Any],
    ) -> None:
        for attempt in attempts:
            stage = getattr(attempt, "fallback_stage", None)
            status = getattr(attempt, "status", "")
            if isinstance(attempt, dict):
                stage = attempt.get("fallback_stage")
                status = str(attempt.get("status", ""))
            if isinstance(stage, int) and (stage > 0 or status in {"failed", "low_confidence"}):
                await safe_record_technical_metric(
                    self._technical_recorder,
                    metric_name="ai_fallback_attempt",
                    idempotency_key=f"ai_fallback:{idempotency_key}:{stage}:{status}",
                    component="ai_worker",
                    subject_id=idempotency_key,
                    metadata={"status": status, "correlation_id": correlation_id},
                )

    async def _publish_post_ai(
        self,
        *,
        event: RawTelegramEvent,
        record: AiProcessingRecord,
        pre_ai: dict[str, Any],
    ) -> None:
        if record.canonical is None:
            return
        await self._post_ai_queue.publish(
            QueueMessage(
                queue_name=POST_AI_DEDUP_QUEUE,
                payload={
                    "schema_version": record.canonical.schema_version,
                    "idempotency_key": event.idempotency_key,
                    "source_id": event.source_id,
                    "source_channel_id": event.source_channel_id,
                    "source_message_id": event.source_message_id,
                    "source_message_ids": event.source_message_ids,
                    "source_url": event.source_url,
                    "occurred_at": event.occurred_at.isoformat(),
                    "correlation_id": event.correlation_id,
                    "canonical": record.canonical.model_dump(mode="json"),
                    "media": [asdict(item) for item in record.media],
                    "pre_ai_dedup": pre_ai.get("pre_ai_dedup"),
                    "dedup_signals": {
                        "phones_present": bool(record.canonical.phone_numbers),
                        "media_phashes": [item.phash for item in record.media],
                    },
                    "manual_review_reason": (
                        record.failure_reason if record.status == "manual_review" else None
                    ),
                },
                correlation_id=event.correlation_id,
                metadata={
                    "idempotency_key": event.idempotency_key,
                    "schema_version": record.canonical.schema_version,
                    "source_id": event.source_id,
                },
            )
        )
