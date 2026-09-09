from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from estateflow.adapters.telethon_listener import TelethonClientAdapter, _media_references
from estateflow.services.listener_pool import (
    InMemoryChannelAssignmentRepository,
    InMemoryListenerAccountRepository,
    ListenerAccountMetadata,
    ListenerAssignmentService,
)
from estateflow.services.media_buffer import TelegramAlbumBuffer
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.queue import QueueMessage
from estateflow.services.source_config import SourceConfig
from estateflow.services.telegram_listener import (
    InMemoryIdempotencyStore,
    QueueRawTelegramEventPublisher,
    RawTelegramEventPublisher,
    TelegramAccountListener,
    TelegramAuthorizationError,
    TelegramClientAdapter,
    TelegramConnectionError,
    TelegramFloodWaitError,
    TelegramForwardMetadata,
    TelegramMediaReference,
    TelegramUpdate,
    build_telegram_idempotency_key,
    build_telegram_source_url,
)
from estateflow.services.telegram_profiles import build_telegram_extraction_strategy


class RecordingQueue:
    def __init__(self) -> None:
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
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
        self.connected = False
        self.disconnected = False
        self.subscribed_sources: list[SourceConfig] = []

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def subscribe(self, sources: list[SourceConfig]) -> None:
        self.subscribed_sources = sources

    def iter_updates(self) -> AsyncIterator[TelegramUpdate]:
        return self._iter_updates()

    async def _iter_updates(self) -> AsyncIterator[TelegramUpdate]:
        for update in self.updates or []:
            yield update

    async def disconnect(self) -> None:
        self.disconnected = True


def _source(source_id: str = "source-1", identifier: str = "@source") -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        name="Source",
        source_type="telegram_channel",
        identifier=identifier,
        source_profile="caption_first",
    )


def _update(
    *,
    update_type: str = "created",
    message_id: str = "10",
    identifier: str = "@source",
    media_group_id: str | None = None,
    media_id: str = "photo-1",
    text: str | None = "Yunusobod 2 xona 500$ +998901234567",
    source_username: str | None = None,
) -> TelegramUpdate:
    return TelegramUpdate(
        update_type=update_type,  # type: ignore[arg-type]
        source_channel_id="-1001",
        source_message_id=message_id,
        source_identifier=identifier,
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        text=text,
        forward_metadata=TelegramForwardMetadata(
            original_channel_id="-2002",
            original_message_id="99",
        ),
        media=[
            TelegramMediaReference(
                media_id=media_id,
                media_type="photo",
                mime_type="image/jpeg",
            )
        ],
        media_group_id=media_group_id,
        source_username=source_username,
    )


def _listener(
    *,
    client: FakeTelegramClient,
    queue: RecordingQueue,
    ops_notifier: RecordingOpsNotifier | None = None,
    event_publisher: RawTelegramEventPublisher | None = None,
) -> TelegramAccountListener:
    account_repository = InMemoryListenerAccountRepository(
        [ListenerAccountMetadata(account_key="listener-a")]
    )
    assignment_service = ListenerAssignmentService(
        account_repository=account_repository,
        assignment_repository=InMemoryChannelAssignmentRepository(),
        ops_notifier=ops_notifier or DisabledOpsNotificationService(),
    )
    publisher = event_publisher or QueueRawTelegramEventPublisher(
        queue=queue,
        idempotency_store=InMemoryIdempotencyStore(),
    )
    return TelegramAccountListener(
        account_key="listener-a",
        client=client,
        sources=[_source()],
        event_publisher=publisher,
        idempotency_store=InMemoryIdempotencyStore(),
        assignment_service=assignment_service,
        ops_notifier=ops_notifier or DisabledOpsNotificationService(),
    )


def test_telethon_media_mapping_accepts_message_convenience_properties() -> None:
    class Photo:
        id = 123
        access_hash = 456

    class Message:
        id = 10
        media = None
        photo = Photo()
        document = None

    references = _media_references(Message(), source_channel_id="-1001")

    assert len(references) == 1
    assert references[0].media_id == "123"
    assert references[0].media_type == "photo"
    assert references[0].source_message_id == "10"


@pytest.mark.asyncio
async def test_telethon_transient_entity_failure_recovers_source_from_event_username() -> None:
    class FakeTelethonClient:
        async def get_entity(self, _identifier: str) -> object:
            raise ValueError("entity cache unavailable")

        def add_event_handler(self, *_args: object, **_kwargs: object) -> None:
            return None

    adapter = object.__new__(TelethonClientAdapter)
    adapter._client = FakeTelethonClient()
    adapter._source_identifiers_by_chat_id = {}
    adapter._source_usernames_by_chat_id = {}
    adapter._source_identifiers_by_username = {}
    await adapter.subscribe([_source(identifier="@source")])
    message = SimpleNamespace(
        id=31,
        chat_id=-1001,
        date=datetime(2026, 8, 1, tzinfo=UTC),
        message="Configured parser must survive",
        media=None,
        photo=None,
        document=None,
        grouped_id=None,
        fwd_from=None,
    )
    event = SimpleNamespace(
        chat_id=-1001,
        chat=SimpleNamespace(username="SOURCE"),
        message=message,
    )

    update = adapter._message_event_to_update(event, "created")

    assert update.source_channel_id == "-1001"
    assert update.source_identifier == "@source"
    assert update.source_username == "SOURCE"
    assert adapter._source_identifiers_by_chat_id["-1001"] == "@source"


@pytest.mark.asyncio
async def test_new_message_is_mapped_to_versioned_raw_queue_event() -> None:
    queue = RecordingQueue()
    client = FakeTelegramClient(updates=[_update()])
    listener = _listener(client=client, queue=queue)

    await listener.run()

    assert client.connected is True
    assert client.disconnected is True
    assert [source.identifier for source in client.subscribed_sources] == ["@source"]
    assert len(queue.messages) == 1
    payload = queue.messages[0].payload
    assert payload["schema_version"] == "telegram.raw.v1"
    assert payload["event_type"] == "created"
    assert payload["idempotency_key"] == "telegram:-1001:10:created"
    assert payload["account_key"] == "listener-a"
    assert payload["source_id"] == "source-1"
    assert payload["forward_metadata"]["original_message_id"] == "99"
    assert payload["media"][0]["media_id"] == "photo-1"
    assert payload["source_url"] == "https://t.me/source/10"
    assert payload["parser_key"] == "generic.album_caption"
    assert payload["parser_version"] == "1"
    assert payload["parser_mode"] == "active"
    assert payload["parser_provenance"]["source_binding"] == "configured"


@pytest.mark.asyncio
async def test_listener_uses_event_username_when_channel_identifier_is_numeric() -> None:
    queue = RecordingQueue()
    listener = _listener(
        client=FakeTelegramClient(
            updates=[_update(identifier="-1001", source_username="SOURCE")]
        ),
        queue=queue,
    )

    await listener.run()

    assert queue.messages[0].payload["source_id"] == "source-1"
    assert queue.messages[0].payload["parser_key"] == "generic.album_caption"
    assert queue.messages[0].payload["parser_provenance"]["source_binding"] == "configured"


@pytest.mark.asyncio
async def test_one_listener_resolves_distinct_parser_binding_for_each_source() -> None:
    queue = RecordingQueue()
    sources = [
        SourceConfig(
            source_id="source-a",
            name="Source A",
            source_type="telegram_channel",
            identifier="@source_a",
            parser_key="agency.alpha",
            parser_version="2",
            parser_config={"cleanup": {"footer": "@source_a"}},
            parser_mode="shadow",
        ),
        SourceConfig(
            source_id="source-b",
            name="Source B",
            source_type="telegram_channel",
            identifier="@source_b",
            parser_key="agency.beta",
            parser_version="7",
            parser_config={"minimum_score": 0.7},
            parser_mode="disabled",
        ),
    ]
    listener = TelegramAccountListener(
        account_key="listener-a",
        client=FakeTelegramClient(
            updates=[
                _update(identifier="@SOURCE_A", message_id="21", text="Alpha raw text"),
                _update(identifier="@source_b", message_id="22", text="Beta raw text"),
            ]
        ),
        sources=sources,
        event_publisher=QueueRawTelegramEventPublisher(
            queue=queue,
            idempotency_store=InMemoryIdempotencyStore(),
        ),
        idempotency_store=InMemoryIdempotencyStore(),
        assignment_service=ListenerAssignmentService(
            account_repository=InMemoryListenerAccountRepository(
                [ListenerAccountMetadata(account_key="listener-a")]
            ),
            assignment_repository=InMemoryChannelAssignmentRepository(),
            ops_notifier=DisabledOpsNotificationService(),
        ),
        ops_notifier=DisabledOpsNotificationService(),
    )

    await listener.run()

    assert [(item.payload["source_id"], item.payload["parser_key"]) for item in queue.messages] == [
        ("source-a", "agency.alpha"),
        ("source-b", "agency.beta"),
    ]
    assert queue.messages[0].payload["parser_config"] == {
        "cleanup": {"footer": "@source_a"}
    }
    assert queue.messages[0].payload["parser_mode"] == "shadow"
    assert queue.messages[0].payload["text"] == "Alpha raw text"
    assert queue.messages[1].payload["parser_version"] == "7"
    assert queue.messages[1].payload["parser_mode"] == "disabled"
    assert queue.messages[1].payload["text"] == "Beta raw text"


def test_telegram_source_url_requires_public_username_and_numeric_message_id() -> None:
    assert build_telegram_source_url("@estateflow", "42") == "https://t.me/estateflow/42"
    assert build_telegram_source_url(None, "42") is None
    assert build_telegram_source_url("estateflow", "album") is None


@pytest.mark.asyncio
async def test_duplicate_delivery_for_same_source_message_is_not_republished() -> None:
    queue = RecordingQueue()
    client = FakeTelegramClient(updates=[_update(), _update()])
    listener = _listener(client=client, queue=queue)

    await listener.run()

    assert len(queue.messages) == 1


@pytest.mark.asyncio
async def test_album_updates_flush_as_one_canonical_event_through_listener() -> None:
    queue = RecordingQueue()
    publisher = TelegramAlbumBuffer(
        downstream=QueueRawTelegramEventPublisher(
            queue=queue,
            idempotency_store=InMemoryIdempotencyStore(),
        ),
        debounce_seconds=30,
    )
    client = FakeTelegramClient()
    listener = TelegramAccountListener(
        account_key="listener-a",
        client=client,
        sources=[
            SourceConfig(
                source_id="source-1",
                name="Source",
                source_type="telegram_channel",
                identifier="@source",
                source_profile="album_text_merge",
            )
        ],
        event_publisher=publisher,
        idempotency_store=InMemoryIdempotencyStore(),
        assignment_service=ListenerAssignmentService(
            account_repository=InMemoryListenerAccountRepository(
                [ListenerAccountMetadata(account_key="listener-a")]
            ),
            assignment_repository=InMemoryChannelAssignmentRepository(),
            ops_notifier=DisabledOpsNotificationService(),
        ),
        ops_notifier=DisabledOpsNotificationService(),
    )

    await listener.handle_update(
        _update(
            message_id="11",
            media_group_id="album-1",
            media_id="photo-1",
            text=None,
        )
    )
    await listener.handle_update(
        _update(
            message_id="12",
            media_group_id="album-1",
            media_id="photo-2",
            text="Album caption",
        )
    )
    await listener.stop()

    assert len(queue.messages) == 1
    payload = queue.messages[0].payload
    assert payload["idempotency_key"] == "telegram:-1001:album:album-1:created"
    assert payload["source_message_ids"] == ["11", "12"]
    assert payload["text"] == "Album caption"


@pytest.mark.asyncio
async def test_edit_and_delete_keep_separate_idempotency_and_semantics() -> None:
    queue = RecordingQueue()
    client = FakeTelegramClient(
        updates=[
            _update(update_type="created"),
            _update(update_type="edited"),
            _update(update_type="deleted", text=None),
        ]
    )
    listener = _listener(client=client, queue=queue)

    await listener.run()

    assert [message.payload["event_type"] for message in queue.messages] == [
        "created",
        "edited",
        "deleted",
    ]
    assert [message.payload["idempotency_key"] for message in queue.messages] == [
        "telegram:-1001:10:created",
        "telegram:-1001:10:edited",
        "telegram:-1001:10:deleted",
    ]


@pytest.mark.asyncio
async def test_connection_error_marks_reconnecting_without_crashing_other_listener() -> None:
    failing_queue = RecordingQueue()
    healthy_queue = RecordingQueue()
    ops_notifier = RecordingOpsNotifier()
    failing = _listener(
        client=FakeTelegramClient(connect_error=TelegramConnectionError("network")),
        queue=failing_queue,
        ops_notifier=ops_notifier,
    )
    healthy = _listener(
        client=FakeTelegramClient(updates=[_update(message_id="20")]),
        queue=healthy_queue,
    )

    results = await asyncio_gather_exceptions(failing.run(), healthy.run())

    assert results == [None, None]
    assert len(failing_queue.messages) == 0
    assert len(healthy_queue.messages) == 1
    assert "connection failed" in ops_notifier.messages[0]


@pytest.mark.asyncio
async def test_flood_wait_marks_account_and_emits_safe_ops_event() -> None:
    queue = RecordingQueue()
    ops_notifier = RecordingOpsNotifier()
    listener = _listener(
        client=FakeTelegramClient(connect_error=TelegramFloodWaitError(17)),
        queue=queue,
        ops_notifier=ops_notifier,
    )

    await listener.run()

    assert len(queue.messages) == 0
    assert len(ops_notifier.messages) >= 1
    joined = "\n".join(ops_notifier.messages).lower()
    assert "floodwait" in joined or "flood_wait" in joined
    assert "session" not in joined
    assert "token" not in joined


@pytest.mark.asyncio
async def test_authorization_error_is_reported_without_raw_text_or_secret() -> None:
    queue = RecordingQueue()
    ops_notifier = RecordingOpsNotifier()
    listener = _listener(
        client=FakeTelegramClient(connect_error=TelegramAuthorizationError("bad auth")),
        queue=queue,
        ops_notifier=ops_notifier,
    )

    await listener.run()

    assert len(queue.messages) == 0
    joined = "\n".join(ops_notifier.messages).lower()
    assert "authorization failed" in joined
    assert "+998901234567" not in joined
    assert "session" not in joined


def test_idempotency_key_is_channel_message_and_event_type_based() -> None:
    assert (
        build_telegram_idempotency_key(
            event_type="created",
            source_channel_id="-1001",
            source_message_id="10",
        )
        == "telegram:-1001:10:created"
    )


def test_caption_first_profile_keeps_media_posts_text_ready() -> None:
    update = _update(text=None)
    strategy = build_telegram_extraction_strategy("caption_first")

    extracted = strategy.extract(update)

    assert extracted.text == ""
    assert extracted.media


async def asyncio_gather_exceptions(*awaitables: Any) -> list[Any]:
    import asyncio

    return list(await asyncio.gather(*awaitables, return_exceptions=True))
