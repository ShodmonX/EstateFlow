from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr
from telethon import TelegramClient, events, utils  # type: ignore[import-untyped]
from telethon.errors import (  # type: ignore[import-untyped]
    FloodWaitError,
    RPCError,
    UnauthorizedError,
)

from estateflow.services.source_config import SourceConfig
from estateflow.services.telegram_listener import (
    TelegramAuthorizationError,
    TelegramClientAdapter,
    TelegramConnectionError,
    TelegramFloodWaitError,
    TelegramForwardMetadata,
    TelegramMediaReference,
    TelegramUpdate,
    is_supported_telegram_media,
)


class TelethonClientAdapter(TelegramClientAdapter):
    def __init__(
        self,
        *,
        session_name: str,
        api_id: int,
        api_hash: SecretStr,
    ) -> None:
        self._client = TelegramClient(session_name, api_id, api_hash.get_secret_value())
        self._updates: asyncio.Queue[TelegramUpdate | None] = asyncio.Queue()
        self._disconnected = False
        self._source_identifiers_by_chat_id: dict[str, str] = {}
        self._source_usernames_by_chat_id: dict[str, str] = {}

    async def connect(self) -> None:
        self._disconnected = False
        try:
            await self._client.connect()
            if not await self._client.is_user_authorized():
                raise TelegramAuthorizationError("Telegram account is not authorized.")
        except FloodWaitError as exc:
            raise TelegramFloodWaitError(int(exc.seconds)) from exc
        except UnauthorizedError as exc:
            raise TelegramAuthorizationError("Telegram account authorization failed.") from exc
        except RPCError as exc:
            raise TelegramConnectionError(type(exc).__name__) from exc

    async def subscribe(self, sources: list[SourceConfig]) -> None:
        chats: list[Any] = []
        for source in sources:
            if not source.enabled:
                continue
            identifier = source.identifier.strip()
            if identifier.startswith("-") and identifier[1:].isdigit():
                chats.append(int(identifier))
            elif identifier.isdigit():
                chats.append(int(identifier))
            else:
                chats.append(identifier)
            try:
                entity = await self._client.get_entity(identifier)
                chat_id = str(utils.get_peer_id(entity))
                self._source_identifiers_by_chat_id[chat_id] = identifier
                username = getattr(entity, "username", None)
                if isinstance(username, str) and username.strip():
                    self._source_usernames_by_chat_id[chat_id] = username.strip().lstrip("@")
            except (RPCError, ValueError):
                # Keep consuming the configured source if Telegram metadata is temporarily
                # unavailable during startup.
                if identifier.startswith("@"):
                    self._source_usernames_by_chat_id[identifier] = identifier.lstrip("@")
        if not chats:
            return

        self._client.add_event_handler(
            self._handle_new_message,
            events.NewMessage(chats=chats),
        )
        self._client.add_event_handler(
            self._handle_edited_message,
            events.MessageEdited(chats=chats),
        )
        self._client.add_event_handler(
            self._handle_deleted_message,
            events.MessageDeleted(chats=chats),
        )

    def iter_updates(self) -> AsyncIterator[TelegramUpdate]:
        return self._iter_updates()

    async def _iter_updates(self) -> AsyncIterator[TelegramUpdate]:
        while True:
            update = await self._updates.get()
            if update is None:
                break
            yield update

    async def disconnect(self) -> None:
        if self._disconnected:
            return
        self._disconnected = True
        try:
            await self._updates.put(None)
        except Exception:
            pass
        try:
            is_connected = getattr(self._client, "is_connected", None)
            if callable(is_connected) and is_connected():
                await self._client.disconnect()
        except Exception:
            pass

    async def _handle_new_message(self, event: Any) -> None:
        await self._updates.put(self._message_event_to_update(event, "created"))

    async def _handle_edited_message(self, event: Any) -> None:
        await self._updates.put(self._message_event_to_update(event, "edited"))

    async def _handle_deleted_message(self, event: Any) -> None:
        source_channel_id = str(getattr(event, "chat_id", "") or getattr(event, "peer_id", ""))
        source_identifier, source_username = self._source_metadata(source_channel_id)
        occurred_at = datetime.now(UTC)
        for message_id in getattr(event, "deleted_ids", []):
            await self._updates.put(
                TelegramUpdate(
                    update_type="deleted",
                    source_channel_id=source_channel_id,
                    source_message_id=str(message_id),
                    source_identifier=source_identifier,
                    occurred_at=occurred_at,
                    source_username=source_username,
                )
            )

    def _message_event_to_update(self, event: Any, update_type: str) -> TelegramUpdate:
        message = event.message
        source_channel_id = str(getattr(event, "chat_id", "") or getattr(message, "chat_id", ""))
        source_identifier, source_username = self._source_metadata(source_channel_id)
        return TelegramUpdate(
            update_type="edited" if update_type == "edited" else "created",
            source_channel_id=source_channel_id,
            source_message_id=str(message.id),
            source_identifier=source_identifier,
            occurred_at=_message_datetime(message),
            text=getattr(message, "message", None),
            forward_metadata=_forward_metadata(message),
            media=_media_references(message, source_channel_id=source_channel_id),
            media_group_id=str(getattr(message, "grouped_id", "") or "") or None,
            source_username=source_username,
        )

    def _source_metadata(self, source_channel_id: str) -> tuple[str, str | None]:
        identifier = self._source_identifiers_by_chat_id.get(source_channel_id, source_channel_id)
        username = self._source_usernames_by_chat_id.get(source_channel_id)
        if username is None and identifier.startswith("@"):
            username = identifier.lstrip("@")
        return identifier, username


def _message_datetime(message: Any) -> datetime:
    value = getattr(message, "date", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return datetime.now(UTC)


def _forward_metadata(message: Any) -> TelegramForwardMetadata | None:
    forwarded = getattr(message, "fwd_from", None)
    if forwarded is None:
        return None
    return TelegramForwardMetadata(
        original_channel_id=str(getattr(forwarded, "from_id", "") or "") or None,
        original_message_id=str(getattr(forwarded, "channel_post", "") or "") or None,
        original_channel_username=getattr(forwarded, "from_name", None),
    )


def _media_references(
    message: Any, source_channel_id: str | None = None
) -> list[TelegramMediaReference]:
    # Telethon exposes media both through ``message.media`` and convenience
    # properties (``message.photo`` / ``message.document``).  Different
    # update/entity shapes can populate either path, so prefer both instead
    # of silently turning an image post into a text-only event.
    media = getattr(message, "media", None)
    document = getattr(media, "document", None) or getattr(message, "document", None)
    photo = getattr(media, "photo", None) or getattr(message, "photo", None)
    msg_id = str(getattr(message, "id", "") or "") or None
    if document is not None and is_supported_telegram_media(
        TelegramMediaReference(
            media_id=str(getattr(document, "id", "")),
            media_type="document",
            mime_type=getattr(document, "mime_type", None),
        )
    ):
        return [
            TelegramMediaReference(
                media_id=str(getattr(document, "id", "")),
                media_type="document",
                mime_type=getattr(document, "mime_type", None),
                size_bytes=getattr(document, "size", None),
                access_hash=str(getattr(document, "access_hash", "")) or None,
                source_channel_id=source_channel_id,
                source_message_id=msg_id,
            )
        ]
    if photo is not None:
        return [
            TelegramMediaReference(
                media_id=str(getattr(photo, "id", "")),
                media_type="photo",
                access_hash=str(getattr(photo, "access_hash", "")) or None,
                source_channel_id=source_channel_id,
                source_message_id=msg_id,
            )
        ]
    return []
