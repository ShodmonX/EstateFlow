from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from estateflow.services.ingestion_pipeline import PreAiIngestionProcessor
from estateflow.services.listener_pool import (
    ChannelAssignment,
    InMemoryChannelAssignmentRepository,
    InMemoryListenerAccountRepository,
    ListenerAccountMetadata,
    ListenerAssignmentService,
)
from estateflow.services.media_buffer import TelegramAlbumBuffer
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.pre_ai_dedup import (
    InMemoryPreAiDedupSignalStore,
    PreAiDedupConfig,
    PreAiDedupFilter,
    StableMediaReferencePHashProvider,
)
from estateflow.services.queue import AI_PROCESSING_QUEUE, QueueMessage, QueuePublishError
from estateflow.services.source_config import SourceConfig
from estateflow.services.telegram_listener import (
    RAW_EVENT_SCHEMA_VERSION,
    InMemoryIdempotencyStore,
    QueueRawTelegramEventPublisher,
    RawTelegramEvent,
    TelegramAccountListener,
    TelegramClientAdapter,
    TelegramFloodWaitError,
    TelegramForwardMetadata,
    TelegramMediaReference,
    TelegramUpdate,
)


class RecordingQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        if self.fail:
            raise QueuePublishError(message.queue_name, "temporary outage")
        self.messages.append(message)


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


@dataclass
class FakeTelegramClient(TelegramClientAdapter):
    updates: list[TelegramUpdate] | None = None
    connect_error: Exception | None = None

    def __post_init__(self) -> None:
        self.subscribed_sources: list[SourceConfig] = []
        self.disconnected = False

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    async def subscribe(self, sources: list[SourceConfig]) -> None:
        self.subscribed_sources = list(sources)

    def iter_updates(self) -> AsyncIterator[TelegramUpdate]:
        return self._iter_updates()

    async def _iter_updates(self) -> AsyncIterator[TelegramUpdate]:
        for update in self.updates or []:
            yield update

    async def disconnect(self) -> None:
        self.disconnected = True


def _source(source_id: str, identifier: str) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        name=source_id,
        source_type="telegram_channel",
        identifier=identifier,
    )


def _update(
    *,
    channel_id: str,
    identifier: str,
    message_id: str,
    text: str | None,
    media_group_id: str | None = None,
    media_id: str = "photo-1",
    forward: TelegramForwardMetadata | None = None,
) -> TelegramUpdate:
    return TelegramUpdate(
        update_type="created",
        source_channel_id=channel_id,
        source_message_id=message_id,
        source_identifier=identifier,
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        text=text,
        forward_metadata=forward,
        media=[
            TelegramMediaReference(
                media_id=media_id,
                media_type="photo",
                mime_type="image/jpeg",
                size_bytes=12345,
            )
        ],
        media_group_id=media_group_id,
    )


def _assignment_service(
    *,
    ops: RecordingOpsNotifier,
    account_repository: InMemoryListenerAccountRepository | None = None,
    assignment_repository: InMemoryChannelAssignmentRepository | None = None,
) -> ListenerAssignmentService:
    return ListenerAssignmentService(
        account_repository=account_repository
        or InMemoryListenerAccountRepository(
            [
                ListenerAccountMetadata(account_key="listener-a"),
                ListenerAccountMetadata(account_key="listener-b"),
            ]
        ),
        assignment_repository=assignment_repository or InMemoryChannelAssignmentRepository(),
        ops_notifier=ops,
    )


def _listener(
    *,
    account_key: str,
    client: FakeTelegramClient,
    sources: list[SourceConfig],
    event_publisher: TelegramAlbumBuffer,
    idempotency_store: InMemoryIdempotencyStore,
    assignment_service: ListenerAssignmentService,
    ops: RecordingOpsNotifier,
) -> TelegramAccountListener:
    return TelegramAccountListener(
        account_key=account_key,
        client=client,
        sources=sources,
        event_publisher=event_publisher,
        idempotency_store=idempotency_store,
        assignment_service=assignment_service,
        ops_notifier=ops,
    )


def _pre_ai_processor(
    *,
    signal_store: InMemoryPreAiDedupSignalStore,
    ai_queue: RecordingQueue,
    ops: RecordingOpsNotifier,
) -> PreAiIngestionProcessor:
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


@pytest.mark.asyncio
async def test_two_account_listener_to_buffer_raw_queue_and_pre_ai_filter_flow() -> None:
    source_a = _source("source-a", "@a")
    source_b = _source("source-b", "@b")
    raw_queue = RecordingQueue()
    ai_queue = RecordingQueue()
    ops = RecordingOpsNotifier()
    idempotency_store = InMemoryIdempotencyStore()
    buffer = TelegramAlbumBuffer(
        downstream=QueueRawTelegramEventPublisher(
            queue=raw_queue,
            idempotency_store=idempotency_store,
        ),
        debounce_seconds=30,
    )
    assignment_service = _assignment_service(ops=ops)
    listener_a = _listener(
        account_key="listener-a",
        client=FakeTelegramClient(
            updates=[
                _update(
                    channel_id="-1001",
                    identifier="@a",
                    message_id="10",
                    text="Yunusobod 2 xona 500$ +998 90 123 45 67",
                    media_id="single-photo",
                )
            ]
        ),
        sources=[source_a],
        event_publisher=buffer,
        idempotency_store=idempotency_store,
        assignment_service=assignment_service,
        ops=ops,
    )
    listener_b = _listener(
        account_key="listener-b",
        client=FakeTelegramClient(
            updates=[
                _update(
                    channel_id="-1002",
                    identifier="@b",
                    message_id="20",
                    text="Album caption",
                    media_group_id="album-1",
                    media_id="album-photo-1",
                ),
                _update(
                    channel_id="-1002",
                    identifier="@b",
                    message_id="21",
                    text=None,
                    media_group_id="album-1",
                    media_id="album-photo-2",
                ),
            ]
        ),
        sources=[source_b],
        event_publisher=buffer,
        idempotency_store=idempotency_store,
        assignment_service=assignment_service,
        ops=ops,
    )

    await listener_a.run()
    await listener_b.run()
    await buffer.flush_all()

    assert len(raw_queue.messages) == 2
    assert {message.payload["source_id"] for message in raw_queue.messages} == {
        "source-a",
        "source-b",
    }
    album_payload = next(message.payload for message in raw_queue.messages if _is_source_b(message))
    assert album_payload["idempotency_key"] == "telegram:-1002:album:album-1:created"
    assert album_payload["source_message_ids"] == ["20", "21"]
    assert len(album_payload["media"]) == 2

    processor = _pre_ai_processor(
        signal_store=InMemoryPreAiDedupSignalStore(),
        ai_queue=ai_queue,
        ops=ops,
    )
    for message in raw_queue.messages:
        await processor.process_raw_queue_message(message)

    assert len(ai_queue.messages) == 2
    assert all(message.queue_name == AI_PROCESSING_QUEUE for message in ai_queue.messages)
    assert {message.payload["pre_ai_dedup"]["decision"] for message in ai_queue.messages} == {
        "proceed_to_ai"
    }


@pytest.mark.asyncio
async def test_flood_wait_failover_and_ops_are_safe() -> None:
    source_a = _source("source-a", "@a")
    source_b = _source("source-b", "@b")
    account_repository = InMemoryListenerAccountRepository(
        [
            ListenerAccountMetadata(account_key="listener-a"),
            ListenerAccountMetadata(account_key="listener-b"),
        ]
    )
    assignment_repository = InMemoryChannelAssignmentRepository(
        [
            ChannelAssignment(source_id="source-a", account_key="listener-a"),
            ChannelAssignment(source_id="source-b", account_key="listener-a"),
        ]
    )
    ops = RecordingOpsNotifier()
    assignment_service = _assignment_service(
        ops=ops,
        account_repository=account_repository,
        assignment_repository=assignment_repository,
    )
    listener = _listener(
        account_key="listener-a",
        client=FakeTelegramClient(connect_error=TelegramFloodWaitError(30)),
        sources=[source_a, source_b],
        event_publisher=TelegramAlbumBuffer(
            downstream=QueueRawTelegramEventPublisher(
                queue=RecordingQueue(),
                idempotency_store=InMemoryIdempotencyStore(),
            ),
            debounce_seconds=30,
        ),
        idempotency_store=InMemoryIdempotencyStore(),
        assignment_service=assignment_service,
        ops=ops,
    )

    await listener.run()
    assignments = await assignment_service.reconcile([source_a, source_b])

    assert {assignment.account_key for assignment in assignments} == {"listener-b"}
    joined_ops = "\n".join(ops.messages).lower()
    assert "flood" in joined_ops
    assert "failover" not in joined_ops or "listener-a" in joined_ops
    assert "+998" not in joined_ops
    assert "session" not in joined_ops
    assert "token" not in joined_ops


@pytest.mark.asyncio
async def test_same_public_channel_message_seen_by_two_accounts_raw_queue_once() -> None:
    source = _source("source-a", "@a")
    raw_queue = RecordingQueue()
    ops = RecordingOpsNotifier()
    idempotency_store = InMemoryIdempotencyStore()
    buffer = TelegramAlbumBuffer(
        downstream=QueueRawTelegramEventPublisher(
            queue=raw_queue,
            idempotency_store=idempotency_store,
        ),
        debounce_seconds=30,
    )
    assignment_service = _assignment_service(ops=ops)
    update = _update(channel_id="-1001", identifier="@a", message_id="10", text="Same post")

    await _listener(
        account_key="listener-a",
        client=FakeTelegramClient(updates=[update]),
        sources=[source],
        event_publisher=buffer,
        idempotency_store=idempotency_store,
        assignment_service=assignment_service,
        ops=ops,
    ).run()
    await _listener(
        account_key="listener-b",
        client=FakeTelegramClient(updates=[update]),
        sources=[source],
        event_publisher=buffer,
        idempotency_store=idempotency_store,
        assignment_service=assignment_service,
        ops=ops,
    ).run()
    await buffer.flush_all()

    assert len(raw_queue.messages) == 1


@pytest.mark.asyncio
async def test_temporary_redis_outage_does_not_silently_mark_seen() -> None:
    source = _source("source-a", "@a")
    raw_queue = RecordingQueue(fail=True)
    ops = RecordingOpsNotifier()
    idempotency_store = InMemoryIdempotencyStore()
    buffer = TelegramAlbumBuffer(
        downstream=QueueRawTelegramEventPublisher(
            queue=raw_queue,
            idempotency_store=idempotency_store,
        ),
        debounce_seconds=30,
    )
    update = _update(channel_id="-1001", identifier="@a", message_id="10", text="Retry me")
    listener = _listener(
        account_key="listener-a",
        client=FakeTelegramClient(updates=[update]),
        sources=[source],
        event_publisher=buffer,
        idempotency_store=idempotency_store,
        assignment_service=_assignment_service(ops=ops),
        ops=ops,
    )

    with pytest.raises(QueuePublishError):
        await listener.run()
    assert await idempotency_store.seen("telegram:-1001:10:created") is False

    raw_queue.fail = False
    await listener.handle_update(update)

    assert len(raw_queue.messages) == 1
    assert await idempotency_store.seen("telegram:-1001:10:created") is True


@pytest.mark.asyncio
async def test_exact_duplicate_skips_ai_and_ambiguous_duplicate_goes_to_ai() -> None:
    ops = RecordingOpsNotifier()
    signal_store = InMemoryPreAiDedupSignalStore()
    ai_queue = RecordingQueue()
    processor = _pre_ai_processor(signal_store=signal_store, ai_queue=ai_queue, ops=ops)
    first = RawTelegramEvent(
        schema_version=RAW_EVENT_SCHEMA_VERSION,
        event_type="created",
        idempotency_key="telegram:-1001:10:created",
        account_key="listener-a",
        source_id="source-a",
        source_channel_id="-1001",
        source_message_id="10",
        source_identifier="@a",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        correlation_id="cid-1",
        text="Yunusobod 2 xona 500$ +998901234567",
        forward_metadata=None,
        media=[TelegramMediaReference(media_id="photo-a", media_type="photo")],
        source_message_ids=["10"],
    )
    exact_duplicate = RawTelegramEvent.from_queue_payload(
        {
            **first.to_queue_payload(),
            "idempotency_key": "telegram:-1002:11:created",
            "source_id": "source-b",
            "source_channel_id": "-1002",
            "source_message_id": "11",
            "source_identifier": "@b",
            "correlation_id": "cid-2",
            "source_message_ids": ["11"],
        }
    )
    ambiguous = RawTelegramEvent.from_queue_payload(
        {
            **first.to_queue_payload(),
            "idempotency_key": "telegram:-1003:12:created",
            "source_id": "source-c",
            "source_channel_id": "-1003",
            "source_message_id": "12",
            "source_identifier": "@c",
            "correlation_id": "cid-3",
            "text": "Yunusobod 2 xona 500 dollar oilaga beriladi boshqa izoh",
            "media": [{"media_id": "photo-a", "media_type": "photo"}],
            "source_message_ids": ["12"],
        }
    )

    assert await processor.process_raw_queue_message(_queue_message(first)) is True
    assert await processor.process_raw_queue_message(_queue_message(exact_duplicate)) is False
    assert await processor.process_raw_queue_message(_queue_message(ambiguous)) is True

    assert len(ai_queue.messages) == 2
    assert [message.payload["pre_ai_dedup"]["decision"] for message in ai_queue.messages] == [
        "proceed_to_ai",
        "needs_reviewable_signal",
    ]
    joined_ops = "\n".join(ops.messages)
    assert "+998901234567" not in joined_ops
    assert "Yunusobod" not in joined_ops
    assert "session" not in joined_ops.lower()


def test_sprint2_contract_document_matches_code_schema() -> None:
    docs = open("docs/raw_ingestion_contract.md", encoding="utf-8").read()
    handoff = open("docs/sprint_2_ai_handoff.md", encoding="utf-8").read()
    event = RawTelegramEvent(
        schema_version=RAW_EVENT_SCHEMA_VERSION,
        event_type="created",
        idempotency_key="telegram:-1001:10:created",
        account_key="listener-a",
        source_id="source-a",
        source_channel_id="-1001",
        source_message_id="10",
        source_identifier="@a",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        correlation_id="cid",
        text="text",
        forward_metadata=TelegramForwardMetadata(
            original_channel_id="-2002",
            original_message_id="99",
        ),
        media=[TelegramMediaReference(media_id="photo", media_type="photo")],
        source_message_ids=["10"],
    )

    assert RAW_EVENT_SCHEMA_VERSION in docs
    assert AI_PROCESSING_QUEUE in handoff
    for key in event.to_queue_payload():
        assert f"`{key}`" in docs
    for required in ("pre_ai_dedup", "decision", "should_skip_ai", "reasons"):
        assert required in handoff


def _queue_message(event: RawTelegramEvent) -> QueueMessage:
    return QueueMessage(
        queue_name="ingestion.raw_announcements",
        payload=event.to_queue_payload(),
        correlation_id=event.correlation_id,
        metadata={"idempotency_key": event.idempotency_key},
    )


def _is_source_b(message: QueueMessage) -> bool:
    return bool(message.payload["source_id"] == "source-b")
