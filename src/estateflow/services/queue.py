from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from math import ceil
from secrets import token_urlsafe
from typing import Any, Protocol, cast

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from redis.asyncio import Redis

from estateflow.contracts.events import (
    AI_PROCESSING_QUEUE,
    ANNOUNCEMENT_PERSISTED_QUEUE,
    NOTIFICATION_DELIVERY_QUEUE,
    POST_AI_DEDUP_QUEUE,
    RAW_ANNOUNCEMENT_QUEUE,
    SOURCE_PARSER_QUARANTINE_QUEUE,
    QueueMessage,
    serialize_queue_message,
)

__all__ = [
    "AI_PROCESSING_QUEUE",
    "ANNOUNCEMENT_PERSISTED_QUEUE",
    "EventQueue",
    "NOTIFICATION_DELIVERY_QUEUE",
    "POST_AI_DEDUP_QUEUE",
    "PublishingQueue",
    "QueueMessage",
    "RAW_ANNOUNCEMENT_QUEUE",
    "SOURCE_PARSER_QUARANTINE_QUEUE",
    "RabbitMQEventQueue",
    "RedisEventQueue",
]

EVENT_QUEUE_NAMES = (
    RAW_ANNOUNCEMENT_QUEUE,
    SOURCE_PARSER_QUARANTINE_QUEUE,
    AI_PROCESSING_QUEUE,
    POST_AI_DEDUP_QUEUE,
    ANNOUNCEMENT_PERSISTED_QUEUE,
    NOTIFICATION_DELIVERY_QUEUE,
)


class EventQueue(Protocol):
    async def publish(self, message: QueueMessage) -> None: ...

    async def pop(self, queue_name: str, timeout_seconds: float = 1.0) -> QueueMessage | None: ...

    async def ack(self, message: QueueMessage) -> None: ...

    async def retry(self, message: QueueMessage, reason: str) -> None: ...

    async def aclose(self) -> None: ...

    async def healthcheck(self) -> bool: ...


class PublishingQueue(Protocol):
    async def publish(self, message: QueueMessage) -> None: ...


class QueuePublishError(RuntimeError):
    def __init__(self, queue_name: str, reason: str) -> None:
        super().__init__(f"Failed to publish to {queue_name}: {reason}")
        self.queue_name = queue_name
        self.reason = reason


class RedisEventQueue:
    def __init__(self, redis: Redis, *, max_retries: int = 3) -> None:
        self._redis = redis
        self._max_retries = max_retries

    async def publish(self, message: QueueMessage) -> None:
        try:
            await cast(
                Awaitable[int],
                self._redis.rpush(message.queue_name, json.dumps(_serialize_message(message))),
            )
        except Exception as exc:
            raise QueuePublishError(message.queue_name, type(exc).__name__) from exc

    async def pop(self, queue_name: str, timeout_seconds: float = 1.0) -> QueueMessage | None:
        try:
            result = await cast(
                Awaitable[list[Any] | None],
                self._redis.blpop([queue_name], timeout=max(1, ceil(timeout_seconds))),
            )
            if not result:
                return None
            _queue_key, raw_data = result
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode("utf-8")
            data = json.loads(raw_data)
            return QueueMessage(
                queue_name=data.get("queue_name", queue_name),
                payload=data.get("payload", {}),
                correlation_id=data.get("correlation_id", ""),
                metadata=data.get("metadata", {}),
            )
        except Exception:
            return None

    async def ack(self, _message: QueueMessage) -> None:
        return None

    async def retry(self, message: QueueMessage, reason: str) -> None:
        attempt = int(message.metadata.get("retry_count", "0"))
        if attempt >= self._max_retries:
            return None
        metadata = dict(message.metadata)
        metadata["retry_count"] = str(attempt + 1)
        metadata["last_error"] = reason[:200]
        await self.publish(
            QueueMessage(
                queue_name=message.queue_name,
                payload=message.payload,
                correlation_id=message.correlation_id,
                metadata=metadata,
            )
        )

    async def aclose(self) -> None:
        return None

    async def healthcheck(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False


class RabbitMQEventQueue:
    """Durable RabbitMQ event bus with explicit ack/retry and dead-lettering."""

    def __init__(
        self,
        url: str,
        *,
        exchange_name: str = "estateflow.events",
        prefetch_count: int = 10,
        max_retries: int = 3,
    ) -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._dead_letter_exchange_name = f"{exchange_name}.dlx"
        self._prefetch_count = prefetch_count
        self._max_retries = max_retries
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractRobustChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None
        self._dead_letter_exchange: aio_pika.abc.AbstractExchange | None = None
        self._queues: dict[str, aio_pika.abc.AbstractQueue] = {}
        self._deliveries: dict[str, aio_pika.abc.AbstractIncomingMessage] = {}
        self._connect_lock = asyncio.Lock()

    async def _ensure_connected(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            return
        async with self._connect_lock:
            if self._connection is not None and not self._connection.is_closed:
                return
            self._connection = await aio_pika.connect_robust(self._url)
            channel = cast(
                aio_pika.abc.AbstractRobustChannel,
                await self._connection.channel(),
            )
            self._channel = channel
            await channel.set_qos(prefetch_count=self._prefetch_count)
            self._exchange = await channel.declare_exchange(
                self._exchange_name,
                ExchangeType.DIRECT,
                durable=True,
            )
            self._dead_letter_exchange = await channel.declare_exchange(
                self._dead_letter_exchange_name,
                ExchangeType.DIRECT,
                durable=True,
            )
            self._queues.clear()

    async def _queue(self, queue_name: str) -> aio_pika.abc.AbstractQueue:
        await self._ensure_connected()
        existing = self._queues.get(queue_name)
        if existing is not None:
            return existing
        assert self._channel is not None
        assert self._exchange is not None
        assert self._dead_letter_exchange is not None
        queue = await self._channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": self._dead_letter_exchange_name,
                "x-dead-letter-routing-key": queue_name,
            },
        )
        await queue.bind(self._exchange, routing_key=queue_name)
        dead_letter_queue = await self._channel.declare_queue(
            f"{queue_name}.dlq",
            durable=True,
        )
        await dead_letter_queue.bind(self._dead_letter_exchange, routing_key=queue_name)
        self._queues[queue_name] = queue
        return queue

    async def publish(self, message: QueueMessage) -> None:
        try:
            await self._queue(message.queue_name)
            assert self._exchange is not None
            await self._exchange.publish(
                Message(
                    body=json.dumps(serialize_queue_message(message)).encode("utf-8"),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                    message_id=message.correlation_id or token_urlsafe(12),
                    headers={"retry_count": message.metadata.get("retry_count", "0")},
                ),
                routing_key=message.queue_name,
            )
        except Exception as exc:
            raise QueuePublishError(message.queue_name, type(exc).__name__) from exc

    async def pop(self, queue_name: str, timeout_seconds: float = 1.0) -> QueueMessage | None:
        incoming: aio_pika.abc.AbstractIncomingMessage | None = None
        try:
            queue = await self._queue(queue_name)
            incoming = await queue.get(fail=False, timeout=timeout_seconds, no_ack=False)
            if incoming is None:
                return None
            data = json.loads(incoming.body.decode("utf-8"))
            token = token_urlsafe(18)
            self._deliveries[token] = incoming
            return QueueMessage(
                queue_name=data.get("queue_name", queue_name),
                payload=data.get("payload", {}),
                correlation_id=data.get("correlation_id", ""),
                metadata=data.get("metadata", {}),
                delivery_token=token,
            )
        except Exception:
            if incoming is not None and not incoming.processed:
                await incoming.reject(requeue=False)
            return None

    async def ack(self, message: QueueMessage) -> None:
        if message.delivery_token is None:
            return None
        incoming = self._deliveries.pop(message.delivery_token, None)
        if incoming is not None and not incoming.processed:
            await incoming.ack()

    async def retry(self, message: QueueMessage, reason: str) -> None:
        attempt = int(message.metadata.get("retry_count", "0"))
        if attempt < self._max_retries:
            metadata = dict(message.metadata)
            metadata["retry_count"] = str(attempt + 1)
            metadata["last_error"] = reason[:200]
            await self.publish(
                QueueMessage(
                    queue_name=message.queue_name,
                    payload=message.payload,
                    correlation_id=message.correlation_id,
                    metadata=metadata,
                )
            )
            # The retry copy is durable; acknowledge the current delivery so
            # intermediate attempts do not also accumulate in the DLQ.
            await self.ack(message)
            return
        if message.delivery_token is not None:
            incoming = self._deliveries.pop(message.delivery_token, None)
            if incoming is not None and not incoming.processed:
                await incoming.reject(requeue=False)

    async def aclose(self) -> None:
        self._deliveries.clear()
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._connection = None
        self._channel = None
        self._exchange = None
        self._dead_letter_exchange = None
        self._queues.clear()

    async def healthcheck(self) -> bool:
        try:
            await self._ensure_connected()
            return self._connection is not None and not self._connection.is_closed
        except Exception:
            return False


def _serialize_message(message: QueueMessage) -> dict[str, Any]:
    return serialize_queue_message(message)
