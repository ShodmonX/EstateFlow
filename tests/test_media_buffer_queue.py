from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from estateflow.services.media_buffer import AlbumBufferKey, TelegramAlbumBuffer
from estateflow.services.queue import QueueMessage, QueuePublishError, RedisEventQueue
from estateflow.services.telegram_listener import (
    InMemoryIdempotencyStore,
    QueueRawTelegramEventPublisher,
    RawTelegramEvent,
    RedisIdempotencyStore,
    TelegramMediaReference,
)


class RecordingEventQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        if self.fail:
            raise QueuePublishError(message.queue_name, "test failure")
        self.messages.append(message)


class FakeRedis:
    def __init__(self, *, fail_rpush: bool = False) -> None:
        self.fail_rpush = fail_rpush
        self.lists: dict[str, list[str]] = {}
        self.values: dict[str, str] = {}

    async def rpush(self, key: str, value: str) -> None:
        if self.fail_rpush:
            raise RuntimeError("redis unavailable")
        self.lists.setdefault(key, []).append(value)

    async def exists(self, key: str) -> int:
        return 1 if key in self.values else 0

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


def _event(
    *,
    message_id: str = "10",
    account_key: str = "listener-a",
    media_group_id: str | None = None,
    media_id: str = "photo-1",
    text: str | None = "caption",
) -> RawTelegramEvent:
    idempotency_key = (
        f"telegram:-1001:album:{media_group_id}:created"
        if media_group_id
        else f"telegram:-1001:{message_id}:created"
    )
    return RawTelegramEvent(
        schema_version="telegram.raw.v1",
        event_type="created",
        idempotency_key=idempotency_key,
        account_key=account_key,
        source_id="source-1",
        source_channel_id="-1001",
        source_message_id=message_id,
        source_identifier="@source",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        correlation_id=f"cid-{message_id}",
        text=text,
        forward_metadata=None,
        media=[
            TelegramMediaReference(
                media_id=media_id,
                media_type="photo",
                mime_type="image/jpeg",
                size_bytes=12345,
            )
        ],
        media_group_id=media_group_id,
        source_message_ids=[message_id],
    )


@pytest.mark.asyncio
async def test_single_post_publishes_immediately_through_queue_publisher() -> None:
    queue = RecordingEventQueue()
    publisher = QueueRawTelegramEventPublisher(
        queue=queue,
        idempotency_store=InMemoryIdempotencyStore(),
    )
    buffer = TelegramAlbumBuffer(downstream=publisher, debounce_seconds=0.01)

    await buffer.publish(_event())

    assert len(queue.messages) == 1
    assert queue.messages[0].payload["idempotency_key"] == "telegram:-1001:10:created"
    assert queue.messages[0].payload["schema_version"] == "telegram.raw.v1"


@pytest.mark.asyncio
async def test_album_flushes_as_one_canonical_raw_event() -> None:
    queue = RecordingEventQueue()
    publisher = QueueRawTelegramEventPublisher(
        queue=queue,
        idempotency_store=InMemoryIdempotencyStore(),
    )
    buffer = TelegramAlbumBuffer(downstream=publisher, debounce_seconds=30)

    await buffer.publish(_event(message_id="11", media_group_id="album-1", media_id="photo-1"))
    await buffer.publish(
        _event(message_id="12", media_group_id="album-1", media_id="photo-2", text=None)
    )
    await buffer.flush_all()

    assert len(queue.messages) == 1
    payload = queue.messages[0].payload
    assert payload["idempotency_key"] == "telegram:-1001:album:album-1:created"
    assert payload["media_group_id"] == "album-1"
    assert payload["source_message_ids"] == ["11", "12"]
    assert [item["media_id"] for item in payload["media"]] == ["photo-1", "photo-2"]
    assert payload["text"] == "caption"


@pytest.mark.asyncio
async def test_album_race_flush_does_not_publish_twice() -> None:
    queue = RecordingEventQueue()
    publisher = QueueRawTelegramEventPublisher(
        queue=queue,
        idempotency_store=InMemoryIdempotencyStore(),
    )
    buffer = TelegramAlbumBuffer(downstream=publisher, debounce_seconds=30)
    key = AlbumBufferKey("-1001", "album-1", "created")

    await buffer.publish(_event(message_id="11", media_group_id="album-1"))
    await buffer.publish(_event(message_id="12", media_group_id="album-1", media_id="photo-2"))
    await asyncio_gather(buffer.flush(key), buffer.flush(key))

    assert len(queue.messages) == 1


@pytest.mark.asyncio
async def test_same_public_channel_message_seen_by_two_accounts_publishes_once() -> None:
    queue = RecordingEventQueue()
    idempotency_store = InMemoryIdempotencyStore()
    publisher = QueueRawTelegramEventPublisher(queue=queue, idempotency_store=idempotency_store)

    await publisher.publish(_event(account_key="listener-a"))
    await publisher.publish(_event(account_key="listener-b"))

    assert len(queue.messages) == 1


@pytest.mark.asyncio
async def test_redis_idempotency_survives_store_recreation() -> None:
    redis = FakeRedis()
    queue = RecordingEventQueue()
    first = QueueRawTelegramEventPublisher(
        queue=queue,
        idempotency_store=RedisIdempotencyStore(redis),  # type: ignore[arg-type]
    )
    second = QueueRawTelegramEventPublisher(
        queue=queue,
        idempotency_store=RedisIdempotencyStore(redis),  # type: ignore[arg-type]
    )

    await first.publish(_event())
    await second.publish(_event())

    assert len(queue.messages) == 1


@pytest.mark.asyncio
async def test_publish_failure_does_not_mark_seen_and_album_can_retry() -> None:
    queue = RecordingEventQueue(fail=True)
    idempotency_store = InMemoryIdempotencyStore()
    publisher = QueueRawTelegramEventPublisher(queue=queue, idempotency_store=idempotency_store)
    buffer = TelegramAlbumBuffer(downstream=publisher, debounce_seconds=30)

    await buffer.publish(_event(message_id="11", media_group_id="album-1"))
    with pytest.raises(QueuePublishError):
        await buffer.flush_all()

    assert await idempotency_store.seen("telegram:-1001:album:album-1:created") is False

    queue.fail = False
    await buffer.flush_all()

    assert len(queue.messages) == 1
    assert await idempotency_store.seen("telegram:-1001:album:album-1:created") is True


@pytest.mark.asyncio
async def test_redis_queue_serializes_media_references_without_binary_payloads() -> None:
    redis = FakeRedis()
    queue = RedisEventQueue(redis)  # type: ignore[arg-type]
    event = _event(media_id="remote-photo-ref")
    publisher = QueueRawTelegramEventPublisher(
        queue=queue,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    await publisher.publish(event)

    stored = redis.lists["ingestion.raw_announcements"][0]
    decoded = json.loads(stored)
    payload_text = json.dumps(decoded)
    assert decoded["payload"]["media"][0]["media_id"] == "remote-photo-ref"
    assert "binary" not in payload_text.lower()
    assert "bytes" not in decoded["payload"]["media"][0]


async def asyncio_gather(*awaitables: Any) -> list[Any]:
    import asyncio

    return list(await asyncio.gather(*awaitables))
