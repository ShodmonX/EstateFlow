from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from estateflow.services.ai_client import LLMRequest, LLMResponse
from estateflow.services.ai_worker import AiExtractionWorker, InMemoryAiProcessingRepository
from estateflow.services.ingestion_pipeline import PreAiIngestionProcessor
from estateflow.services.listener_pool import (
    InMemoryChannelAssignmentRepository,
    InMemoryListenerAccountRepository,
    ListenerAccountMetadata,
    ListenerAssignmentService,
)
from estateflow.services.media_storage import (
    InMemoryObjectStorage,
    MediaProcessor,
    MediaStorageService,
    StaticMediaResolver,
)
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.post_ai_dedup import (
    InMemoryAnnouncementRepository,
    ManualReviewQueueService,
    PostAiDedupProcessor,
    StructuredAnnouncement,
    WeightedDeduplicationEngine,
)
from estateflow.services.pre_ai_dedup import (
    InMemoryPreAiDedupSignalStore,
    PreAiDedupConfig,
    PreAiDedupFilter,
    StableMediaReferencePHashProvider,
)
from estateflow.services.queue import QueueMessage
from estateflow.services.source_config import SourceConfig
from estateflow.services.telegram_listener import (
    RAW_EVENT_SCHEMA_VERSION,
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
class SequenceLLMClient:
    responses: list[dict[str, Any]]

    def __post_init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete_json(self, request: LLMRequest) -> tuple[LLMResponse, list[Any]]:
        self.requests.append(request)
        content = self.responses.pop(0)
        return LLMResponse(model="fake", content=content, confidence=0.95), []


def _png(fill: bytes = b"a") -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + (16).to_bytes(4, "big")
        + (16).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
        + (fill * 64)
    )


def _source(index: int) -> SourceConfig:
    return SourceConfig(
        source_id=f"source-{index}",
        name=f"Source {index}",
        source_type="telegram_channel",
        identifier=f"@estateflow_{index}",
    )


def _event(
    *,
    source: SourceConfig,
    channel_id: str,
    message_id: str,
    text: str,
    media_id: str,
) -> RawTelegramEvent:
    return RawTelegramEvent(
        schema_version=RAW_EVENT_SCHEMA_VERSION,
        event_type="created",
        idempotency_key=f"telegram:{channel_id}:{message_id}:created",
        account_key="listener-a",
        source_id=source.source_id,
        source_channel_id=channel_id,
        source_message_id=message_id,
        source_identifier=source.identifier,
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        correlation_id=f"cid-{message_id}",
        text=text,
        forward_metadata=None,
        media=[
            TelegramMediaReference(
                media_id=media_id,
                media_type="photo",
                mime_type="image/png",
                access_hash=media_id,
            )
        ],
        source_message_ids=[message_id],
    )


def _raw_message(event: RawTelegramEvent) -> QueueMessage:
    return QueueMessage(
        queue_name="ingestion.raw_announcements",
        payload=event.to_queue_payload(),
        correlation_id=event.correlation_id,
        metadata={"idempotency_key": event.idempotency_key},
    )


def _post_ai_announcement(payload: dict[str, Any]) -> StructuredAnnouncement:
    return StructuredAnnouncement.from_payload(payload)


def _pre_ai_processor(ai_queue: RecordingQueue, ops: RecordingOps) -> PreAiIngestionProcessor:
    signal_store = InMemoryPreAiDedupSignalStore()
    phash_provider = StableMediaReferencePHashProvider()
    return PreAiIngestionProcessor(
        dedup_filter=PreAiDedupFilter(
            signal_store=signal_store,
            phash_provider=phash_provider,
            ops_notifier=ops,
            config=PreAiDedupConfig(near_text_threshold=0.70, phash_hamming_threshold=8),
        ),
        ai_queue=ai_queue,
        signal_store=signal_store,
        phash_provider=phash_provider,
    )


def _ai_worker(llm: SequenceLLMClient, post_ai_queue: RecordingQueue) -> AiExtractionWorker:
    return AiExtractionWorker(
        llm_client=llm,  # type: ignore[arg-type]
        media_service=MediaStorageService(
            resolver=StaticMediaResolver(
                {
                    "photo-a": _png(b"a"),
                    "photo-b": _png(b"a"),
                    "photo-c": _png(b"a"),
                    "photo-phone": _png(b"z"),
                }
            ),
            processor=MediaProcessor(max_bytes=128, phash_hamming_threshold=0),
            storage=InMemoryObjectStorage(),
        ),
        repository=InMemoryAiProcessingRepository(),
        post_ai_queue=post_ai_queue,
        ops_notifier=RecordingOps(),
        prompt_version="estateflow.listing.v1",
        min_confidence=0.72,
    )


@pytest.mark.asyncio
async def test_source_registry_assignment_supports_five_sources() -> None:
    sources = [_source(index) for index in range(1, 6)]
    service = ListenerAssignmentService(
        account_repository=InMemoryListenerAccountRepository(
            [
                ListenerAccountMetadata(account_key="listener-a"),
                ListenerAccountMetadata(account_key="listener-b"),
            ]
        ),
        assignment_repository=InMemoryChannelAssignmentRepository(),
        ops_notifier=RecordingOps(),
    )

    assignments = await service.reconcile(sources)

    assert len(assignments) == 5
    assert {assignment.source_id for assignment in assignments} == {
        source.source_id for source in sources
    }
    assert {assignment.account_key for assignment in assignments} <= {
        "listener-a",
        "listener-b",
    }


@pytest.mark.asyncio
async def test_sprint3_end_to_end_pre_ai_ai_and_post_ai_dedup_flow() -> None:
    sources = [_source(index) for index in range(1, 5)]
    ai_queue = RecordingQueue()
    post_ai_queue = RecordingQueue()
    ops = RecordingOps()
    pre_ai = _pre_ai_processor(ai_queue, ops)
    first = _event(
        source=sources[0],
        channel_id="-1001",
        message_id="10",
        text="Yunusobod 2 xona 65 kv 5 qavat 500$ +998901234567",
        media_id="photo-a",
    )
    exact = _event(
        source=sources[1],
        channel_id="-1002",
        message_id="11",
        text=first.text or "",
        media_id="photo-a",
    )
    ambiguous = _event(
        source=sources[2],
        channel_id="-1003",
        message_id="12",
        text="Yunusobod 2 xona 65m2 5 etaj 510 dollar oilaga",
        media_id="photo-a",
    )
    phone_only = _event(
        source=sources[3],
        channel_id="-1004",
        message_id="13",
        text="Sergeli 4 xona 100 kv 9 qavat 1200$ +998901234567",
        media_id="photo-phone",
    )
    llm = SequenceLLMClient(
        [
            _llm_listing(price=Decimal("500"), district="Yunusobod"),
            _llm_listing(price=Decimal("510"), district="Yunusobod"),
            _llm_listing(
                price=Decimal("1200"),
                district="Sergeli",
                rooms=4,
                area=Decimal("100"),
                floor=9,
            ),
        ]
    )
    ai_worker = _ai_worker(llm, post_ai_queue)
    announcement_repo = InMemoryAnnouncementRepository()
    review_queue = ManualReviewQueueService(announcement_repo)
    processor = PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(candidate_repository=announcement_repo),
        announcement_repository=announcement_repo,
        manual_review_queue=review_queue,
    )

    assert await pre_ai.process_raw_queue_message(_raw_message(first)) is True
    assert await pre_ai.process_raw_queue_message(_raw_message(exact)) is False
    assert await pre_ai.process_raw_queue_message(_raw_message(ambiguous)) is True
    assert await pre_ai.process_raw_queue_message(_raw_message(phone_only)) is True

    assert [message.payload["pre_ai_dedup"]["decision"] for message in ai_queue.messages] == [
        "proceed_to_ai",
        "needs_reviewable_signal",
        "needs_reviewable_signal",
    ]

    for message in ai_queue.messages:
        await ai_worker.process_raw_queue_message(message)
    for message in post_ai_queue.messages:
        await processor.process(_post_ai_announcement(message.payload))

    parents = announcement_repo.search_user_facing()
    audit_rows = announcement_repo.list_all_for_audit()

    assert len(post_ai_queue.messages) == 3
    assert len(parents) == 2
    assert len(audit_rows) == 3
    assert any(row.parent_id is not None for row in audit_rows)
    assert any(row.canonical.district == "Sergeli" and row.parent_id is None for row in parents)
    assert not any("+998901234567" in message for message in ops.messages)


def _llm_listing(
    *,
    price: Decimal,
    district: str,
    rooms: int = 2,
    area: Decimal = Decimal("65"),
    floor: int = 5,
) -> dict[str, Any]:
    return {
        "price": str(price),
        "currency": "USD",
        "price_period": "monthly",
        "price_basis": "total",
        "rooms": rooms,
        "area_sqm": str(area),
        "floor": floor,
        "district": district,
        "address": f"{district} 9 kvartal",
        "phone_numbers": ["+998901234567"],
        "renovation_level": "good",
        "renovation_source": "vision",
        "confidence": 0.95,
    }
