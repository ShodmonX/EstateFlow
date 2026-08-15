from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import NamedTuple

from estateflow.application.core.correlation import new_correlation_id
from estateflow.services.telegram_listener import (
    RAW_EVENT_SCHEMA_VERSION,
    RawTelegramEvent,
    RawTelegramEventPublisher,
    TelegramMediaReference,
    build_telegram_album_idempotency_key,
)

logger = logging.getLogger(__name__)


class AlbumBufferKey(NamedTuple):
    source_channel_id: str
    media_group_id: str
    event_type: str


class TelegramAlbumBuffer(RawTelegramEventPublisher):
    def __init__(
        self,
        *,
        downstream: RawTelegramEventPublisher,
        debounce_seconds: float,
    ) -> None:
        if debounce_seconds <= 0:
            raise ValueError("debounce_seconds must be positive.")
        self._downstream = downstream
        self._debounce_seconds = debounce_seconds
        self._groups: dict[AlbumBufferKey, list[RawTelegramEvent]] = {}
        self._flush_tasks: dict[AlbumBufferKey, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: RawTelegramEvent) -> None:
        if event.media_group_id is None or event.event_type != "created":
            logger.info(
                "Publishing non-album Telegram event downstream",
                extra={
                    "event": "telegram_album_buffer.direct_publish",
                    "source_channel_id": event.source_channel_id,
                    "source_message_id": event.source_message_id,
                    "idempotency_key": event.idempotency_key,
                },
            )
            await self._downstream.publish(event)
            return

        key = AlbumBufferKey(
            source_channel_id=event.source_channel_id,
            media_group_id=event.media_group_id,
            event_type=event.event_type,
        )
        async with self._lock:
            self._groups.setdefault(key, []).append(event)
            previous_task = self._flush_tasks.pop(key, None)
            if previous_task is not None:
                previous_task.cancel()
            self._flush_tasks[key] = asyncio.create_task(self._flush_after_debounce(key))
            logger.info(
                "Buffered Telegram album event",
                extra={
                    "event": "telegram_album_buffer.buffered",
                    "source_channel_id": event.source_channel_id,
                    "media_group_id": event.media_group_id,
                    "buffered_events": len(self._groups[key]),
                },
            )

    async def flush_all(self) -> None:
        async with self._lock:
            keys = list(self._groups)
            tasks = list(self._flush_tasks.values())
            self._flush_tasks.clear()
        for task in tasks:
            task.cancel()
        logger.info(
            "Flushing all buffered Telegram albums",
            extra={
                "event": "telegram_album_buffer.flush_all",
                "group_count": len(keys),
            },
        )
        await asyncio.gather(*(self.flush(key) for key in keys))

    async def flush(self, key: AlbumBufferKey) -> None:
        async with self._lock:
            events = self._groups.pop(key, None)
            if not events:
                return
            self._flush_tasks.pop(key, None)
            canonical = _canonical_album_event(events)

        try:
            logger.info(
                "Publishing canonical Telegram album event",
                extra={
                    "event": "telegram_album_buffer.flushing",
                    "source_channel_id": canonical.source_channel_id,
                    "media_group_id": canonical.media_group_id,
                    "source_message_ids": canonical.source_message_ids,
                    "media_count": len(canonical.media),
                },
            )
            await self._downstream.publish(canonical)
        except Exception:
            async with self._lock:
                current_events = self._groups.pop(key, [])
                self._groups[key] = events + current_events
            logger.exception(
                "Failed to publish canonical Telegram album event",
                extra={
                    "event": "telegram_album_buffer.flush_failed",
                    "source_channel_id": key.source_channel_id,
                    "media_group_id": key.media_group_id,
                    "buffered_events": len(events),
                },
            )
            raise

    async def _flush_after_debounce(self, key: AlbumBufferKey) -> None:
        try:
            await asyncio.sleep(self._debounce_seconds)
            await self.flush(key)
        except asyncio.CancelledError:
            return


def _canonical_album_event(events: list[RawTelegramEvent]) -> RawTelegramEvent:
    ordered = sorted(events, key=lambda event: _message_sort_key(event.source_message_id))
    first = ordered[0]
    media: list[TelegramMediaReference] = []
    media_ids: set[tuple[str, str]] = set()
    text_parts: list[str] = []
    source_message_ids: list[str] = []
    for event in ordered:
        source_message_ids.extend(event.source_message_ids or [event.source_message_id])
        if event.text:
            text_parts.append(event.text)
        for item in event.media:
            media_key = (item.media_id, item.media_type)
            if media_key in media_ids:
                continue
            media_ids.add(media_key)
            media.append(item)

    media_group_id = first.media_group_id
    if media_group_id is None:
        raise ValueError("Album canonicalization requires media_group_id.")

    return replace(
        first,
        schema_version=RAW_EVENT_SCHEMA_VERSION,
        idempotency_key=build_telegram_album_idempotency_key(
            event_type=first.event_type,
            source_channel_id=first.source_channel_id,
            media_group_id=media_group_id,
        ),
        source_message_id=ordered[0].source_message_id,
        occurred_at=min(event.occurred_at for event in ordered),
        correlation_id=new_correlation_id(),
        text="\n\n".join(dict.fromkeys(text_parts)) or None,
        media=media,
        source_message_ids=sorted(set(source_message_ids), key=_message_sort_key),
    )


def _message_sort_key(message_id: str) -> tuple[int, str]:
    try:
        return (int(message_id), message_id)
    except ValueError:
        return (0, message_id)
