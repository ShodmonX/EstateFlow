from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import pytest

from estateflow.services.ai_client import (
    LLMAllModelsFailedError,
    LLMAttemptAudit,
    LLMMalformedResponseError,
    LLMRequest,
    LLMResponse,
)
from estateflow.services.ai_worker import (
    POST_AI_DEDUP_QUEUE,
    AiExtractionWorker,
    AiProcessingRecord,
    InMemoryAiProcessingRepository,
)
from estateflow.services.media_storage import (
    InMemoryObjectStorage,
    MediaProcessingConfig,
    MediaProcessingError,
    MediaProcessor,
    MediaStorageService,
    StaticMediaResolver,
    StorageUploadError,
    decode_image_info,
)
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.queue import QueueMessage
from estateflow.services.telegram_listener import (
    RawTelegramEvent,
    TelegramMediaReference,
)


class RecordingQueue:
    def __init__(self) -> None:
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        self.messages.append(message)


class RecordingOps(DisabledOpsNotificationService):
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


@dataclass
class FakeLLMClient:
    content: dict[str, Any]
    confidence: float = 0.9

    def __post_init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]:
        self.requests.append(request)
        return (
            LLMResponse(model="fake", content=self.content, confidence=self.confidence),
            [],
        )


@dataclass
class FailingLLMClient:
    error: Exception

    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]:
        raise self.error


def _png(width: int = 16, height: int = 16, *, fill: bytes = b"a") -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    ihdr_len = b"\x00\x00\x00\r"
    ihdr_type = b"IHDR"
    dimensions = width.to_bytes(4, "big") + height.to_bytes(4, "big")
    rest = b"\x08\x02\x00\x00\x00"
    crc = b"\x00\x00\x00\x00"
    payload = fill * 64
    return header + ihdr_len + ihdr_type + dimensions + rest + crc + payload


def _large_png(width: int = 1600, height: int = 1200) -> bytes:
    return _png(width, height, fill=b"x") + (b"z" * 4096)


def _event(*, media: list[TelegramMediaReference] | None = None) -> RawTelegramEvent:
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
        text="Yunusobod 2 xona sutkasiga 30$ +998901234567",
        forward_metadata=None,
        media=media or [],
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


@pytest.mark.asyncio
async def test_media_processing_keeps_non_duplicate_images_and_uploads() -> None:
    storage = InMemoryObjectStorage()
    service = MediaStorageService(
        resolver=StaticMediaResolver(
            {
                "photo-1": _png(fill=b"a"),
                "photo-2": _png(fill=b"a"),
                "photo-3": _png(fill=b"b"),
            }
        ),
        processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
        storage=storage,
    )

    stored = await service.prepare_and_store(
        idempotency_key="telegram:-1001:10:created",
        media_references=[
            TelegramMediaReference(media_id="photo-1", media_type="photo", mime_type="image/png"),
            TelegramMediaReference(media_id="photo-2", media_type="photo", mime_type="image/png"),
            TelegramMediaReference(media_id="photo-3", media_type="photo", mime_type="image/png"),
        ],
    )

    assert [item.media_id for item in stored] == ["photo-1", "photo-3"]
    assert len(storage.objects) == 2
    assert all(item.storage_url.startswith("memory://r2/") for item in stored)
    assert storage.metadata[stored[0].object_key]["dedup_scope"] == "same_announcement_media_only"
    assert stored[0].phash
    assert stored[0].width == 16


@pytest.mark.asyncio
async def test_media_processing_compresses_large_image_with_aspect_ratio_metadata() -> None:
    storage = InMemoryObjectStorage()
    service = MediaStorageService(
        resolver=StaticMediaResolver({"photo-1": _large_png()}),
        processor=MediaProcessor(
            MediaProcessingConfig(
                max_output_bytes=512,
                target_max_width=800,
                target_max_height=600,
                phash_hamming_threshold=0,
            )
        ),
        storage=storage,
    )

    stored = await service.prepare_and_store(
        idempotency_key="telegram:-1001:10:created",
        media_references=[
            TelegramMediaReference(media_id="photo-1", media_type="photo", mime_type="image/png")
        ],
    )

    assert len(stored) == 1
    assert stored[0].size_bytes <= 512
    assert stored[0].width == 800
    assert stored[0].height == 600
    assert storage.metadata[stored[0].object_key]["original_width"] == "1600"


@pytest.mark.asyncio
async def test_corrupt_or_unsupported_media_is_skipped_without_crashing_worker_stage() -> None:
    storage = InMemoryObjectStorage()
    service = MediaStorageService(
        resolver=StaticMediaResolver({"bad": b"not-an-image", "text": b"hello"}),
        processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
        storage=storage,
    )

    stored = await service.prepare_and_store(
        idempotency_key="telegram:-1001:10:created",
        media_references=[
            TelegramMediaReference(media_id="bad", media_type="photo", mime_type="image/png"),
            TelegramMediaReference(media_id="text", media_type="document", mime_type="text/plain"),
        ],
    )

    assert stored == []
    assert storage.objects == {}


def test_decode_rejects_unsafe_image_dimensions() -> None:
    with pytest.raises(MediaProcessingError):
        decode_image_info(_png(30_000, 20), "image/png")


@pytest.mark.asyncio
async def test_storage_failure_does_not_persist_storage_url() -> None:
    storage = InMemoryObjectStorage()
    storage.fail_upload = True
    service = MediaStorageService(
        resolver=StaticMediaResolver({"photo-1": _png()}),
        processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
        storage=storage,
    )

    with pytest.raises(StorageUploadError):
        await service.prepare_and_store(
            idempotency_key="telegram:-1001:10:created",
            media_references=[
                TelegramMediaReference(
                    media_id="photo-1",
                    media_type="photo",
                    mime_type="image/png",
                )
            ],
        )

    assert storage.objects == {}


@pytest.mark.asyncio
async def test_upload_failure_cleans_up_previously_uploaded_objects() -> None:
    storage = InMemoryObjectStorage()
    storage.fail_after_count = 1
    service = MediaStorageService(
        resolver=StaticMediaResolver({"photo-1": _png(fill=b"a"), "photo-2": _png(fill=b"b")}),
        processor=MediaProcessor(max_bytes=256, phash_hamming_threshold=0),
        storage=storage,
    )

    with pytest.raises(StorageUploadError):
        await service.prepare_and_store(
            idempotency_key="telegram:-1001:10:created",
            media_references=[
                TelegramMediaReference(
                    media_id="photo-1",
                    media_type="photo",
                    mime_type="image/png",
                ),
                TelegramMediaReference(
                    media_id="photo-2",
                    media_type="photo",
                    mime_type="image/png",
                ),
            ],
        )

    assert storage.objects == {}
    assert storage.deleted


@pytest.mark.asyncio
async def test_ai_worker_allows_text_renovation_with_images() -> None:
    media = [TelegramMediaReference(media_id="photo-1", media_type="photo", mime_type="image/png")]
    event = _event(media=media)
    llm = FakeLLMClient(
        {
            "price": 30,
            "currency": "$",
            "price_period": "daily",
            "price_basis": "total",
            "renovation_level": "euro",
            "renovation_source": "text",
            "confidence": 0.95,
        }
    )
    queue = RecordingQueue()
    repo = InMemoryAiProcessingRepository()
    worker = AiExtractionWorker(
        llm_client=llm,  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({"photo-1": _png()}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=repo,
        post_ai_queue=queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    record = await worker.process_raw_queue_message(_message(event))

    assert record.status == "succeeded"
    assert record.failure_reason is None
    assert record.canonical is not None
    assert record.canonical.renovation_level == "euro"
    assert record.canonical.renovation_source == "text"
    assert len(queue.messages) == 1
    request_text = "\n".join(message.content for message in llm.requests[0].messages)
    assert "vision_required=True" in request_text


@pytest.mark.asyncio
async def test_ai_worker_allows_missing_optional_renovation_with_images() -> None:
    media = [TelegramMediaReference(media_id="photo-1", media_type="photo", mime_type="image/png")]
    event = _event(media=media)
    queue = RecordingQueue()
    worker = AiExtractionWorker(
        llm_client=FakeLLMClient(
            {
                "price": 30,
                "currency": "$",
                "price_period": "daily",
                "price_basis": "total",
                "renovation_level": None,
                "renovation_source": "unknown",
                "confidence": 0.95,
            }
        ),  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({"photo-1": _png()}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=InMemoryAiProcessingRepository(),
        post_ai_queue=queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    record = await worker.process_raw_queue_message(_message(event))

    assert record.status == "succeeded"
    assert record.failure_reason is None
    assert record.canonical is not None
    assert record.canonical.renovation_level is None
    assert len(queue.messages) == 1


@pytest.mark.asyncio
async def test_ai_worker_routes_valid_low_confidence_parse_to_manual_review() -> None:
    event = _event()
    queue = RecordingQueue()
    worker = AiExtractionWorker(
        llm_client=FakeLLMClient(
            {
                "price": 30,
                "currency": "$",
                "price_period": "daily",
                "price_basis": "total",
                "confidence": 0.4,
            },
            confidence=0.95,
        ),  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=InMemoryAiProcessingRepository(),
        post_ai_queue=queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    record = await worker.process_raw_queue_message(_message(event))

    assert record.status == "manual_review"
    assert record.failure_reason == "low_confidence"
    assert len(queue.messages) == 1
    assert queue.messages[0].payload["manual_review_reason"] == "low_confidence"


@pytest.mark.asyncio
async def test_ai_worker_success_publishes_post_ai_contract_payload() -> None:
    media = [TelegramMediaReference(media_id="photo-1", media_type="photo", mime_type="image/png")]
    event = _event(media=media)
    llm = FakeLLMClient(
        {
            "price": 30,
            "currency": "$",
            "price_period": "daily",
            "price_basis": "total",
            "renovation_level": "good",
            "renovation_source": "vision",
            "audience_tags": ["young_family"],
            "confidence": 0.95,
        }
    )
    queue = RecordingQueue()
    worker = AiExtractionWorker(
        llm_client=llm,  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({"photo-1": _png()}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=InMemoryAiProcessingRepository(),
        post_ai_queue=queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    record = await worker.process_raw_queue_message(_message(event))

    assert record.status == "succeeded"
    assert record.post_ai_published is True
    assert len(queue.messages) == 1
    payload = queue.messages[0].payload
    assert queue.messages[0].queue_name == POST_AI_DEDUP_QUEUE
    assert payload["schema_version"] == "estateflow.announcement.v1"
    assert payload["canonical"]["price_normalized_monthly"] == "900"
    assert payload["canonical"]["audience_tags"] == ["family"]
    assert payload["media"][0]["storage_url"].startswith("memory://r2/")
    assert payload["dedup_signals"]["media_phashes"]


@pytest.mark.asyncio
async def test_ai_worker_reprocess_is_idempotent_after_successful_publish() -> None:
    media = [TelegramMediaReference(media_id="photo-1", media_type="photo", mime_type="image/png")]
    event = _event(media=media)
    llm = FakeLLMClient(
        {
            "price": 30,
            "currency": "$",
            "price_period": "daily",
            "price_basis": "total",
            "renovation_level": "good",
            "renovation_source": "vision",
            "confidence": 0.95,
        }
    )
    queue = RecordingQueue()
    repo = InMemoryAiProcessingRepository()
    worker = AiExtractionWorker(
        llm_client=llm,  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({"photo-1": _png()}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=repo,
        post_ai_queue=queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    first = await worker.process_raw_queue_message(_message(event))
    second = await worker.process_raw_queue_message(_message(event))

    assert first == second
    assert len(llm.requests) == 1
    assert len(queue.messages) == 1


@pytest.mark.asyncio
async def test_ai_worker_recovers_unpublished_successful_handoff_on_retry() -> None:
    event = _event()
    queue = RecordingQueue()
    repo = InMemoryAiProcessingRepository()
    canonical_record = await _successful_text_record(event)
    await repo.save(canonical_record)
    worker = AiExtractionWorker(
        llm_client=FakeLLMClient({}),  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=repo,
        post_ai_queue=queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    record = await worker.process_raw_queue_message(_message(event))

    assert record.post_ai_published is True
    assert len(queue.messages) == 1


@pytest.mark.asyncio
async def test_ai_worker_persists_validation_failure_without_post_ai_publish() -> None:
    event = _event()
    llm = FakeLLMClient({"confidence": 0.9, "amenities": ["washer"]})
    queue = RecordingQueue()
    repo = InMemoryAiProcessingRepository()
    worker = AiExtractionWorker(
        llm_client=llm,  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=repo,
        post_ai_queue=queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    record = await worker.process_raw_queue_message(_message(event))

    assert record.status == "failed"
    assert record.error_type == "ValidationError"
    assert record.failure_reason == "ValidationError"
    assert queue.messages == []
    assert await repo.get(event.idempotency_key) == record


@pytest.mark.asyncio
async def test_ai_worker_persists_malformed_and_all_models_failures_without_pii_ops() -> None:
    event = _event()
    ops = RecordingOps()
    queue = RecordingQueue()
    worker = AiExtractionWorker(
        llm_client=FailingLLMClient(
            LLMMalformedResponseError("Malformed OpenRouter response id=req model=fake")
        ),  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=InMemoryAiProcessingRepository(),
        post_ai_queue=queue,
        ops_notifier=ops,
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    record = await worker.process_raw_queue_message(_message(event))

    assert record.status == "failed"
    assert record.error_type == "LLMMalformedResponseError"
    assert queue.messages == []
    joined_ops = "\n".join(ops.messages)
    assert "Yunusobod" not in joined_ops
    assert "+998901234567" not in joined_ops

    all_models_worker = AiExtractionWorker(
        llm_client=FailingLLMClient(
            LLMAllModelsFailedError(
                [
                    LLMAttemptAudit(
                        model="primary",
                        fallback_stage=0,
                        latency_ms=1,
                        status="failed",
                    )
                ]
            )
        ),  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=InMemoryAiProcessingRepository(),
        post_ai_queue=RecordingQueue(),
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )
    all_models_record = await all_models_worker.process_raw_queue_message(_message(event))

    assert all_models_record.status == "failed"
    assert all_models_record.failure_reason == "all_models_failed"
    assert all_models_record.attempts[0]["model"] == "primary"


@pytest.mark.asyncio
async def test_ai_worker_persists_storage_failure_as_observable_failed_status() -> None:
    media = [TelegramMediaReference(media_id="photo-1", media_type="photo", mime_type="image/png")]
    event = _event(media=media)
    storage = InMemoryObjectStorage()
    storage.fail_upload = True
    repo = InMemoryAiProcessingRepository()
    worker = AiExtractionWorker(
        llm_client=FakeLLMClient({}),  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({"photo-1": _png()}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=storage,
        ),
        repository=repo,
        post_ai_queue=RecordingQueue(),
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )

    record = await worker.process_raw_queue_message(_message(event))

    assert record.status == "failed"
    assert record.error_type == "StorageUploadError"
    assert record.failure_reason == "storage_retryable"
    assert await repo.get(event.idempotency_key) == record


def test_post_ai_contract_document_mentions_queue_and_rules() -> None:
    docs = open("docs/post_ai_dedup_contract.md", encoding="utf-8").read()

    assert POST_AI_DEDUP_QUEUE in docs
    assert "price_period" in docs
    assert "Vision" in docs
    assert "signed URL tokens" in docs


async def _successful_text_record(event: RawTelegramEvent) -> AiProcessingRecord:
    llm = FakeLLMClient(
        {
            "price": 30,
            "currency": "$",
            "price_period": "daily",
            "price_basis": "total",
            "confidence": 0.95,
        }
    )
    queue = RecordingQueue()
    repo = InMemoryAiProcessingRepository()
    worker = AiExtractionWorker(
        llm_client=llm,  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver({}),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=repo,
        post_ai_queue=queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )
    record = await worker.process_raw_queue_message(_message(event))
    return replace(record, post_ai_published=False)
