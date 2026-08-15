from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, cast

from redis.asyncio import Redis

from estateflow.application.core.correlation import new_correlation_id
from estateflow.services.listener_pool import ListenerAssignmentService
from estateflow.services.ops_notifications import OpsNotificationService
from estateflow.services.queue import RAW_ANNOUNCEMENT_QUEUE, PublishingQueue, QueueMessage
from estateflow.services.source_config import SourceConfig
from estateflow.services.telegram_profiles import build_telegram_extraction_strategy

RAW_EVENT_SCHEMA_VERSION = "telegram.raw.v1"
logger = logging.getLogger(__name__)

TelegramUpdateType = Literal["created", "edited", "deleted"]


@dataclass(frozen=True)
class TelegramForwardMetadata:
    original_channel_id: str | None = None
    original_message_id: str | None = None
    original_channel_username: str | None = None


@dataclass(frozen=True)
class TelegramMediaReference:
    media_id: str
    media_type: str
    mime_type: str | None = None
    size_bytes: int | None = None
    access_hash: str | None = None
    source_channel_id: str | None = None
    source_message_id: str | None = None


@dataclass(frozen=True)
class TelegramUpdate:
    update_type: TelegramUpdateType
    source_channel_id: str
    source_message_id: str
    source_identifier: str
    occurred_at: datetime
    text: str | None = None
    forward_metadata: TelegramForwardMetadata | None = None
    media: list[TelegramMediaReference] = field(default_factory=list)
    media_group_id: str | None = None
    source_username: str | None = None


@dataclass(frozen=True)
class RawTelegramEvent:
    schema_version: str
    event_type: TelegramUpdateType
    idempotency_key: str
    account_key: str
    source_id: str
    source_channel_id: str
    source_message_id: str
    source_identifier: str
    occurred_at: datetime
    correlation_id: str
    text: str | None
    forward_metadata: TelegramForwardMetadata | None
    media: list[TelegramMediaReference]
    media_group_id: str | None = None
    source_message_ids: list[str] = field(default_factory=list)
    source_url: str | None = None

    def to_queue_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["occurred_at"] = self.occurred_at.isoformat()
        return payload

    @classmethod
    def from_queue_payload(cls, payload: dict[str, Any]) -> RawTelegramEvent:
        return cls(
            schema_version=str(payload["schema_version"]),
            event_type=_telegram_update_type(str(payload["event_type"])),
            idempotency_key=str(payload["idempotency_key"]),
            account_key=str(payload["account_key"]),
            source_id=str(payload["source_id"]),
            source_channel_id=str(payload["source_channel_id"]),
            source_message_id=str(payload["source_message_id"]),
            source_identifier=str(payload["source_identifier"]),
            occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
            correlation_id=str(payload["correlation_id"]),
            text=payload.get("text"),
            forward_metadata=_forward_metadata_from_payload(payload.get("forward_metadata")),
            media=[
                _media_reference_from_payload(item)
                for item in payload.get("media", [])
                if isinstance(item, dict)
                and is_supported_telegram_media(_media_reference_from_payload(item))
            ],
            media_group_id=payload.get("media_group_id"),
            source_message_ids=[str(item) for item in payload.get("source_message_ids", [])],
            source_url=payload.get("source_url"),
        )


class TelegramClientAdapter(Protocol):
    async def connect(self) -> None: ...

    async def subscribe(self, sources: list[SourceConfig]) -> None: ...

    def iter_updates(self) -> AsyncIterator[TelegramUpdate]: ...

    async def disconnect(self) -> None: ...


class IdempotencyStore(Protocol):
    async def seen(self, key: str) -> bool: ...

    async def mark_seen(self, key: str) -> None: ...


class RawTelegramEventPublisher(Protocol):
    async def publish(self, event: RawTelegramEvent) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def seen(self, key: str) -> bool:
        return key in self._seen

    async def mark_seen(self, key: str) -> None:
        self._seen.add(key)


class RedisIdempotencyStore:
    def __init__(self, redis: Redis, *, prefix: str = "ingestion:idempotency") -> None:
        self._redis = redis
        self._prefix = prefix

    async def seen(self, key: str) -> bool:
        return bool(await self._redis.exists(self._redis_key(key)))

    async def mark_seen(self, key: str) -> None:
        await self._redis.set(self._redis_key(key), "1")

    def _redis_key(self, key: str) -> str:
        return f"{self._prefix}:{key}"


class TelegramFloodWaitError(RuntimeError):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"Telegram FloodWait for {seconds} seconds")
        self.seconds = seconds


class TelegramAuthorizationError(RuntimeError):
    pass


class TelegramConnectionError(RuntimeError):
    pass


class TelegramAccountListener:
    def __init__(
        self,
        *,
        account_key: str,
        client: TelegramClientAdapter,
        sources: list[SourceConfig],
        extraction_strategy: object | None = None,
        event_publisher: RawTelegramEventPublisher | None = None,
        queue: PublishingQueue | None = None,
        idempotency_store: IdempotencyStore,
        assignment_service: ListenerAssignmentService,
        ops_notifier: OpsNotificationService,
    ) -> None:
        self._account_key = account_key
        self._client = client
        self._sources = [source for source in sources if source.enabled]
        self._source_by_identifier = {source.identifier: source for source in self._sources}
        profile_name = self._resolve_profile_name(self._sources)
        self._extraction_strategy = extraction_strategy or build_telegram_extraction_strategy(
            profile_name
        )
        self._event_publisher = event_publisher or QueueRawTelegramEventPublisher(
            queue=_require_queue(queue),
            idempotency_store=idempotency_store,
        )
        self._idempotency_store = idempotency_store
        self._assignment_service = assignment_service
        self._ops_notifier = ops_notifier
        self._stop_requested = False
        self._source_profile = profile_name or "default"

    async def run(self) -> None:
        try:
            logger.info(
                "Telegram listener starting",
                extra={
                    "event": "telegram_listener.starting",
                    "account_key": self._account_key,
                    "source_count": len(self._sources),
                    "source_profile": self._source_profile,
                },
            )
            await self._client.connect()
            await self._client.subscribe(self._sources)
            await self._assignment_service.transition_health(
                account_key=self._account_key,
                health_status="online",
            )
            logger.info(
                "Telegram listener subscribed",
                extra={
                    "event": "telegram_listener.subscribed",
                    "account_key": self._account_key,
                    "source_count": len(self._sources),
                    "source_profile": self._source_profile,
                },
            )
            async for update in self._client.iter_updates():
                if self._stop_requested:
                    break
                if not (update.text or "").strip() and not update.media:
                    continue
                await self.handle_update(update)
        except TelegramFloodWaitError as exc:
            await self._assignment_service.transition_health(
                account_key=self._account_key,
                health_status="flood_wait",
                flood_wait_increment=1,
            )
            await self._ops_notifier.notify(
                severity="warning",
                reason=f"Listener account {self._account_key} hit FloodWait for {exc.seconds}s",
                correlation_id=new_correlation_id(),
            )
        except TelegramAuthorizationError:
            await self._assignment_service.transition_health(
                account_key=self._account_key,
                health_status="banned",
            )
            await self._ops_notifier.notify(
                severity="critical",
                reason=f"Listener account {self._account_key} authorization failed",
                correlation_id=new_correlation_id(),
            )
        except TelegramConnectionError:
            await self._assignment_service.transition_health(
                account_key=self._account_key,
                health_status="reconnecting",
            )
            await self._ops_notifier.notify(
                severity="warning",
                reason=f"Listener account {self._account_key} connection failed",
                correlation_id=new_correlation_id(),
            )
        finally:
            await self._flush_event_publisher()
            await self._client.disconnect()
            logger.info(
                "Telegram listener stopped",
                extra={
                    "event": "telegram_listener.stopped",
                    "account_key": self._account_key,
                    "source_count": len(self._sources),
                    "source_profile": self._source_profile,
                },
            )

    async def stop(self) -> None:
        self._stop_requested = True
        logger.info(
            "Telegram listener stop requested",
            extra={
                "event": "telegram_listener.stop_requested",
                "account_key": self._account_key,
                "source_count": len(self._sources),
                "source_profile": self._source_profile,
            },
        )
        await self._flush_event_publisher()
        await self._client.disconnect()

    async def handle_update(self, update: TelegramUpdate) -> RawTelegramEvent | None:
        logger.info(
            "Telegram update received",
            extra={
                "event": "telegram_listener.update_received",
                "account_key": self._account_key,
                "source_identifier": update.source_identifier,
                "source_channel_id": update.source_channel_id,
                "source_message_id": update.source_message_id,
                "update_type": update.update_type,
                "media_count": len(update.media),
                "media_group_id": update.media_group_id,
                "source_profile": self._source_profile,
            },
        )
        extractor = getattr(self._extraction_strategy, "extract", None)
        if callable(extractor):
            update = extractor(update)
        raw_event = self._build_raw_event(update)
        if await self._idempotency_store.seen(raw_event.idempotency_key):
            logger.info(
                "Telegram update skipped as duplicate",
                extra={
                    "event": "telegram_listener.duplicate_skipped",
                    "account_key": self._account_key,
                    "idempotency_key": raw_event.idempotency_key,
                    "source_identifier": update.source_identifier,
                    "source_profile": self._source_profile,
                },
            )
            return None

        await self._event_publisher.publish(raw_event)
        await self._assignment_service.record_successful_event(account_key=self._account_key)
        logger.info(
            "Telegram raw event published",
            extra={
                "event": "telegram_listener.raw_event_published",
                "account_key": self._account_key,
                "idempotency_key": raw_event.idempotency_key,
                "event_type": raw_event.event_type,
                "source_id": raw_event.source_id,
                "source_message_id": raw_event.source_message_id,
                "media_group_id": raw_event.media_group_id,
                "source_profile": self._source_profile,
            },
        )
        return raw_event

    def _build_raw_event(self, update: TelegramUpdate) -> RawTelegramEvent:
        source = self._source_by_identifier.get(update.source_identifier)
        source_id = source.source_id if source is not None else update.source_identifier
        idempotency_key = build_telegram_idempotency_key(
            event_type=update.update_type,
            source_channel_id=update.source_channel_id,
            source_message_id=update.source_message_id,
        )
        return RawTelegramEvent(
            schema_version=RAW_EVENT_SCHEMA_VERSION,
            event_type=update.update_type,
            idempotency_key=idempotency_key,
            account_key=self._account_key,
            source_id=source_id,
            source_channel_id=update.source_channel_id,
            source_message_id=update.source_message_id,
            source_identifier=update.source_identifier,
            occurred_at=update.occurred_at,
            correlation_id=new_correlation_id(),
            text=update.text,
            forward_metadata=update.forward_metadata,
            media=update.media,
            media_group_id=update.media_group_id,
            source_message_ids=[update.source_message_id],
            source_url=build_telegram_source_url(
                update.source_username
                or (
                    source.identifier
                    if source is not None and source.identifier.startswith("@")
                    else None
                ),
                update.source_message_id,
            ),
        )

    @staticmethod
    def _resolve_profile_name(sources: list[SourceConfig]) -> str | None:
        profiles = {
            source.source_profile.strip()
            for source in sources
            if source.source_profile and source.source_profile.strip()
        }
        if not profiles:
            return None
        if len(profiles) > 1:
            raise ValueError(
                "TelegramAccountListener requires a single source_profile per listener."
            )
        return next(iter(profiles))

    async def _flush_event_publisher(self) -> None:
        flush_all = getattr(self._event_publisher, "flush_all", None)
        if callable(flush_all):
            result = flush_all()
            if asyncio.iscoroutine(result):
                await result


class TelegramListenerSupervisor:
    def __init__(self, listeners: list[TelegramAccountListener]) -> None:
        self._listeners = listeners
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(listener.run()) for listener in self._listeners]
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                raise RuntimeError("Telegram listener task failed") from result
        raise RuntimeError("Telegram listener task exited unexpectedly")

    async def stop(self) -> None:
        await asyncio.gather(*(listener.stop() for listener in self._listeners))
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


def build_telegram_idempotency_key(
    *,
    event_type: TelegramUpdateType,
    source_channel_id: str,
    source_message_id: str,
) -> str:
    return f"telegram:{source_channel_id}:{source_message_id}:{event_type}"


def build_telegram_album_idempotency_key(
    *,
    event_type: TelegramUpdateType,
    source_channel_id: str,
    media_group_id: str,
) -> str:
    return f"telegram:{source_channel_id}:album:{media_group_id}:{event_type}"


def build_telegram_source_url(username: str | None, message_id: str) -> str | None:
    """Build a public Telegram message URL when the source has a username."""
    if not username or not message_id.isdigit():
        return None
    clean_username = username.strip().lstrip("@").strip("/")
    if not clean_username:
        return None
    return f"https://t.me/{clean_username}/{message_id}"


class QueueRawTelegramEventPublisher:
    def __init__(self, *, queue: PublishingQueue, idempotency_store: IdempotencyStore) -> None:
        self._queue = queue
        self._idempotency_store = idempotency_store

    async def publish(self, event: RawTelegramEvent) -> None:
        if await self._idempotency_store.seen(event.idempotency_key):
            return
        await self._queue.publish(
            QueueMessage(
                queue_name=RAW_ANNOUNCEMENT_QUEUE,
                payload=event.to_queue_payload(),
                correlation_id=event.correlation_id,
                metadata={
                    "idempotency_key": event.idempotency_key,
                    "event_type": event.event_type,
                    "source_id": event.source_id,
                    "schema_version": event.schema_version,
                },
            )
        )
        await self._idempotency_store.mark_seen(event.idempotency_key)


def _require_queue(queue: PublishingQueue | None) -> PublishingQueue:
    if queue is None:
        raise ValueError("queue is required when event_publisher is not provided.")
    return queue


def _telegram_update_type(value: str) -> TelegramUpdateType:
    if value not in {"created", "edited", "deleted"}:
        raise ValueError(f"Unknown Telegram update type: {value}")
    return cast(TelegramUpdateType, value)


def _forward_metadata_from_payload(value: object) -> TelegramForwardMetadata | None:
    if not isinstance(value, dict):
        return None
    return TelegramForwardMetadata(
        original_channel_id=value.get("original_channel_id"),
        original_message_id=value.get("original_message_id"),
        original_channel_username=value.get("original_channel_username"),
    )


def _media_reference_from_payload(value: dict[str, object]) -> TelegramMediaReference:
    mime_type = value.get("mime_type")
    size_bytes = value.get("size_bytes")
    access_hash = value.get("access_hash")
    source_channel_id = value.get("source_channel_id")
    source_message_id = value.get("source_message_id")
    return TelegramMediaReference(
        media_id=str(value["media_id"]),
        media_type=str(value["media_type"]),
        mime_type=mime_type if isinstance(mime_type, str) else None,
        size_bytes=size_bytes if isinstance(size_bytes, int) else None,
        access_hash=access_hash if isinstance(access_hash, str) else None,
        source_channel_id=str(source_channel_id) if source_channel_id is not None else None,
        source_message_id=str(source_message_id) if source_message_id is not None else None,
    )


def is_supported_telegram_media(media: TelegramMediaReference) -> bool:
    """Only photos and image documents may enter the ingestion pipeline."""
    if media.media_type == "photo":
        return True
    return media.media_type in {"image", "document"} and (
        media.mime_type or ""
    ).lower().startswith("image/")
