from __future__ import annotations

import asyncio
import importlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from redis.asyncio import Redis

from estateflow.adapters.registry import AdapterRegistry
from estateflow.application.core.config import Settings
from estateflow.services.adapter_defaults import DEFAULT_TELEGRAM_ADAPTER_NAME
from estateflow.services.listener_pool import (
    TELEGRAM_SOURCE_TYPES,
    InMemoryChannelAssignmentRepository,
    InMemoryListenerAccountRepository,
    ListenerAccountMetadata,
    ListenerAssignmentService,
)
from estateflow.services.listener_status import (
    ListenerTopologyGroup,
    ListenerTopologySnapshot,
    ListenerTopologyStore,
    NoopListenerTopologyStore,
)
from estateflow.services.media_buffer import TelegramAlbumBuffer
from estateflow.services.ops_notifications import (
    DisabledOpsNotificationService,
    OpsNotificationService,
)
from estateflow.services.queue import EventQueue, RedisEventQueue
from estateflow.services.source_config import SourceConfig, SourceConfigProvider
from estateflow.services.telegram_listener import (
    IdempotencyStore,
    QueueRawTelegramEventPublisher,
    RedisIdempotencyStore,
    TelegramAccountListener,
    TelegramClientAdapter,
    TelegramListenerSupervisor,
)

LISTENER_REFRESH_KEY = "estateflow:listener-refresh-version"
LISTENER_REFRESH_CHANNEL = "estateflow:listener-refresh"
logger = logging.getLogger(__name__)


class ListenerRefreshStore(Protocol):
    async def version(self) -> int: ...

    async def signal(self) -> int: ...


class InMemoryListenerRefreshStore:
    def __init__(self) -> None:
        self._version = 0

    async def version(self) -> int:
        return self._version

    async def signal(self) -> int:
        self._version += 1
        return self._version


class RedisListenerRefreshStore:
    def __init__(self, redis: Redis, *, key: str = LISTENER_REFRESH_KEY) -> None:
        self._redis = redis
        self._key = key

    async def version(self) -> int:
        value = await self._redis.get(self._key)
        if value is None:
            return 0
        try:
            return int(value)
        except ValueError:
            return 0

    async def signal(self) -> int:
        updated = await self._redis.incr(self._key)
        await self._redis.publish(LISTENER_REFRESH_CHANNEL, str(updated))
        return int(updated)


@dataclass(frozen=True)
class ListenerRefreshReport:
    refresh_version: int
    source_count: int
    telegram_source_count: int
    started: bool
    reason: str
    refreshed_at: datetime
    adapter_failures: tuple[str, ...] = ()


class TelegramListenerCoordinator:
    def __init__(
        self,
        *,
        settings: Settings,
        adapter_registry: AdapterRegistry,
        source_provider: SourceConfigProvider,
        refresh_store: ListenerRefreshStore,
        redis: Redis,
        ops_notifier: OpsNotificationService | None = None,
        assignment_service: ListenerAssignmentService | None = None,
        event_queue: EventQueue | None = None,
        idempotency_store: IdempotencyStore | None = None,
        topology_store: ListenerTopologyStore | None = None,
        listener_account_key: str = "acc_9889",
        default_adapter_name: str = DEFAULT_TELEGRAM_ADAPTER_NAME,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._settings = settings
        self._adapter_registry = adapter_registry
        self._source_provider = source_provider
        self._refresh_store = refresh_store
        self._redis = redis
        self._ops_notifier = ops_notifier or DisabledOpsNotificationService()
        self._assignment_service = assignment_service or ListenerAssignmentService(
            account_repository=InMemoryListenerAccountRepository(
                [ListenerAccountMetadata(account_key=listener_account_key)]
            ),
            assignment_repository=InMemoryChannelAssignmentRepository(),
            ops_notifier=self._ops_notifier,
        )
        self._event_queue = event_queue or RedisEventQueue(redis)
        self._idempotency_store = idempotency_store or RedisIdempotencyStore(redis)
        self._topology_store = topology_store or NoopListenerTopologyStore()
        self._listener_account_key = listener_account_key
        self._default_adapter_name = default_adapter_name
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._supervisor: TelegramListenerSupervisor | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._background_tasks: list[asyncio.Task[None]] = []
        self._last_refresh_version = -1
        self._last_fingerprint = ""
        self._last_groups: list[ListenerTopologyGroup] = []
        self._last_listener_count = 0

    async def run_forever(self) -> None:
        self._stop_event.clear()
        logger.info(
            "Telegram listener coordinator starting",
            extra={
                "event": "listener_refresh.starting",
                "listener_account_key": self._listener_account_key,
                "default_adapter_name": self._default_adapter_name,
            },
        )
        await self.refresh(reason="startup")
        self._last_refresh_version = await self._refresh_store.version()
        self._background_tasks = [
            asyncio.create_task(self._pubsub_loop()),
            asyncio.create_task(self._poll_loop()),
        ]
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(1.0)
                supervisor_task = self._supervisor_task
                if supervisor_task is None or not supervisor_task.done():
                    continue
                if self._stop_event.is_set():
                    break
                error = supervisor_task.exception()
                if error is not None:
                    raise RuntimeError("Telegram listener supervisor stopped") from error
                raise RuntimeError("Telegram listener supervisor stopped unexpectedly")
        finally:
            await self._cancel_background_tasks()
            logger.info(
                "Telegram listener coordinator stopped",
                extra={
                    "event": "listener_refresh.stopped",
                    "listener_account_key": self._listener_account_key,
                },
            )

    async def signal_refresh(self) -> int:
        version = await self._refresh_store.signal()
        logger.info(
            "Listener refresh signaled",
            extra={
                "event": "listener_refresh.signaled",
                "listener_account_key": self._listener_account_key,
                "refresh_version": version,
            },
        )
        return version

    async def refresh(self, *, reason: str = "manual") -> ListenerRefreshReport:
        sources = await self._source_provider.list_active_sources()
        telegram_sources = [
            source for source in sources if source.source_type in TELEGRAM_SOURCE_TYPES
        ]
        adapter_names = sorted({self._resolve_adapter_name(source) for source in telegram_sources})
        fingerprint = _fingerprint(
            telegram_sources,
            adapter_fingerprint=_adapter_fingerprint(self._adapter_registry, adapter_names),
            default_adapter_name=self._default_adapter_name,
        )
        logger.info(
            "Evaluated Telegram listener refresh fingerprint",
            extra={
                "event": "listener_refresh.evaluated",
                "reason": reason,
                "source_count": len(sources),
                "telegram_source_count": len(telegram_sources),
                "adapter_count": len(adapter_names),
                "fingerprint": fingerprint,
            },
        )
        if (
            fingerprint == self._last_fingerprint
            and self._supervisor_task is not None
            and not self._supervisor_task.done()
        ):
            listener_count = (
                len(getattr(self._supervisor, "_listeners", []))
                if self._supervisor is not None
                else 0
            )
            await self._write_topology_snapshot(
                started=True,
                reason=f"unchanged:{reason}",
                sources=sources,
                telegram_sources=telegram_sources,
                listeners_count=listener_count or self._last_listener_count,
                adapter_failures=[],
                groups=self._last_groups,
            )
            return ListenerRefreshReport(
                refresh_version=await self._refresh_store.version(),
                source_count=len(sources),
                telegram_source_count=len(telegram_sources),
                started=True,
                reason=f"unchanged:{reason}",
                refreshed_at=datetime.now(UTC),
            )

        await self._assignment_service.reconcile(sources)
        await self._stop_supervisor()

        if not telegram_sources:
            self._last_fingerprint = fingerprint
            await self._write_topology_snapshot(
                started=False,
                reason=f"no_telegram_sources:{reason}",
                sources=sources,
                telegram_sources=telegram_sources,
                listeners_count=0,
                adapter_failures=[],
                groups=[],
            )
            self._last_groups = []
            self._last_listener_count = 0
            logger.info(
                "No Telegram sources configured for refresh",
                extra={
                    "event": "listener_refresh.no_sources",
                    "reason": reason,
                    "source_count": len(sources),
                },
            )
            return ListenerRefreshReport(
                refresh_version=await self._refresh_store.version(),
                source_count=len(sources),
                telegram_source_count=0,
                started=False,
                reason=f"no_telegram_sources:{reason}",
                refreshed_at=datetime.now(UTC),
            )

        listeners, adapter_failures, groups = self._build_listeners(telegram_sources)
        if not listeners:
            self._last_fingerprint = fingerprint
            self._last_groups = groups
            self._last_listener_count = 0
            await self._write_topology_snapshot(
                started=False,
                reason=f"disabled:{reason}",
                sources=sources,
                telegram_sources=telegram_sources,
                listeners_count=0,
                adapter_failures=adapter_failures,
                groups=groups,
            )
            logger.warning(
                "Telegram listener refresh produced no runnable listeners",
                extra={
                    "event": "listener_refresh.no_listeners",
                    "reason": reason,
                    "telegram_source_count": len(telegram_sources),
                    "adapter_failures": adapter_failures,
                },
            )
            return ListenerRefreshReport(
                refresh_version=await self._refresh_store.version(),
                source_count=len(sources),
                telegram_source_count=len(telegram_sources),
                started=False,
                reason=f"disabled:{reason}",
                refreshed_at=datetime.now(UTC),
                adapter_failures=tuple(adapter_failures),
            )
        self._supervisor = TelegramListenerSupervisor(listeners)
        self._supervisor_task = asyncio.create_task(self._supervisor.start())
        self._last_fingerprint = fingerprint
        self._last_groups = groups
        self._last_listener_count = len(listeners)
        await self._write_topology_snapshot(
            started=True,
            reason=f"reloaded:{reason}",
            sources=sources,
            telegram_sources=telegram_sources,
            listeners_count=len(listeners),
            adapter_failures=adapter_failures,
            groups=groups,
        )
        logger.info(
            "Telegram listener refresh started supervisor",
            extra={
                "event": "listener_refresh.started",
                "reason": reason,
                "listener_count": len(listeners),
                "telegram_source_count": len(telegram_sources),
                "adapter_failures": adapter_failures,
            },
        )
        if adapter_failures:
            await self._ops_notifier.notify(
                severity="warning",
                reason=(
                    "Telegram listener reload skipped some adapters: " + "; ".join(adapter_failures)
                ),
                correlation_id="listener-refresh",
            )
        return ListenerRefreshReport(
            refresh_version=await self._refresh_store.version(),
            source_count=len(sources),
            telegram_source_count=len(telegram_sources),
            started=True,
            reason=f"reloaded:{reason}",
            refreshed_at=datetime.now(UTC),
            adapter_failures=tuple(adapter_failures),
        )

    async def aclose(self) -> None:
        self._stop_event.set()
        await self._stop_supervisor()
        await self._cancel_background_tasks()

    def _build_listeners(
        self,
        sources: list[SourceConfig],
    ) -> tuple[list[TelegramAccountListener], list[str], list[ListenerTopologyGroup]]:
        listeners: list[TelegramAccountListener] = []
        failures: list[str] = []
        group_summaries: list[ListenerTopologyGroup] = []
        grouped: dict[tuple[str, str | None, str], list[SourceConfig]] = defaultdict(list)
        for source in sources:
            session_name = self._resolve_session_name(source)
            if session_name is None:
                failures.append(
                    f"{self._resolve_adapter_name(source)}:MissingSessionName:{source.source_id}"
                )
                continue
            group_key = (
                self._resolve_adapter_name(source),
                self._resolve_profile_name(source),
                session_name,
            )
            grouped[group_key].append(source)
        for adapter_name, profile_name, session_name in sorted(
            grouped,
            key=lambda item: (item[0], item[1] or "", item[2]),
        ):
            try:
                plugin = self._telegram_plugin(adapter_name, session_name=session_name)
                source_group = grouped[(adapter_name, profile_name, session_name)]
                summary = ListenerTopologyGroup(
                    adapter_name=adapter_name,
                    source_profile=profile_name,
                    source_count=len(source_group),
                    source_ids=tuple(item.source_id for item in source_group),
                    source_identifiers=tuple(item.identifier for item in source_group),
                    source_names=tuple(item.name for item in source_group),
                )
                logger.info(
                    "Building Telegram listener group",
                    extra={
                        "event": "listener_refresh.group_building",
                        "adapter_name": adapter_name,
                        "source_profile": profile_name or "default",
                        "source_count": len(source_group),
                    },
                )
                event_publisher = TelegramAlbumBuffer(
                    downstream=QueueRawTelegramEventPublisher(
                        queue=self._event_queue,
                        idempotency_store=self._idempotency_store,
                    ),
                    debounce_seconds=self._settings.telegram_album_debounce_seconds,
                )
                listeners.append(
                    TelegramAccountListener(
                        account_key=self._listener_account_key,
                        client=plugin,
                        sources=source_group,
                        extraction_strategy=self._extraction_strategy(profile_name),
                        event_publisher=event_publisher,
                        idempotency_store=self._idempotency_store,
                        assignment_service=self._assignment_service,
                        ops_notifier=self._ops_notifier,
                    )
                )
                group_summaries.append(summary)
                logger.info(
                    "Telegram listener group built",
                    extra={
                        "event": "listener_refresh.group_built",
                        "adapter_name": adapter_name,
                        "source_profile": profile_name or "default",
                        "session_name": session_name,
                        "source_count": len(source_group),
                    },
                )
            except Exception as exc:
                failures.append(f"{adapter_name}:{session_name}:{type(exc).__name__}:{exc}")
                source_group = grouped[(adapter_name, profile_name, session_name)]
                group_summaries.append(
                    ListenerTopologyGroup(
                        adapter_name=adapter_name,
                        source_profile=profile_name,
                        source_count=len(source_group),
                        source_ids=tuple(item.source_id for item in source_group),
                        source_identifiers=tuple(item.identifier for item in source_group),
                        source_names=tuple(item.name for item in source_group),
                        runnable=False,
                        error=str(exc),
                    )
                )
                logger.warning(
                    "Failed to build Telegram listener group",
                    extra={
                        "event": "listener_refresh.group_failed",
                        "adapter_name": adapter_name,
                        "source_profile": profile_name or "default",
                        "session_name": session_name,
                        "error": str(exc),
                    },
                )
        return listeners, failures, group_summaries

    def _telegram_plugin(
        self,
        adapter_name: str,
        *,
        session_name: str,
    ) -> TelegramClientAdapter:
        loaded = self._adapter_registry.get(adapter_name)
        if loaded is None:
            raise RuntimeError(f"{adapter_name} adapter plugin is not loaded.")
        module = importlib.import_module(loaded.module_name)
        factory = getattr(module, "create_plugin", None)
        if factory is None or not callable(factory):
            raise RuntimeError(f"{adapter_name} adapter plugin cannot be instantiated.")
        try:
            plugin = factory(self._settings, session_name=session_name)
        except TypeError:
            plugin = factory(self._settings)
        if not hasattr(plugin, "manifest"):
            raise RuntimeError(f"{adapter_name} adapter plugin must expose a manifest.")
        manifest = plugin.manifest
        family = getattr(manifest, "family", None)
        source_types = set(getattr(manifest, "source_types", ()))
        if family != "telegram" or not source_types.intersection(TELEGRAM_SOURCE_TYPES):
            raise RuntimeError(
                f"{adapter_name} is not a Telegram adapter compatible with listener sources."
            )
        return cast(TelegramClientAdapter, plugin)

    def _resolve_adapter_name(self, source: SourceConfig) -> str:
        adapter_name = source.adapter_name.strip() if source.adapter_name else ""
        return adapter_name or self._default_adapter_name

    def _resolve_profile_name(self, source: SourceConfig) -> str | None:
        profile_name = source.source_profile.strip() if source.source_profile else ""
        return profile_name or None

    def _resolve_session_name(self, source: SourceConfig) -> str | None:
        session_name = source.listener_account_key.strip() if source.listener_account_key else ""
        return session_name or self._listener_account_key

    @staticmethod
    def _extraction_strategy(profile_name: str | None) -> object | None:
        if profile_name is None:
            return None
        from estateflow.services.telegram_profiles import build_telegram_extraction_strategy

        return build_telegram_extraction_strategy(profile_name)

    async def _stop_supervisor(self) -> None:
        supervisor = self._supervisor
        task = self._supervisor_task
        self._supervisor = None
        self._supervisor_task = None
        if supervisor is not None:
            await supervisor.stop()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._poll_interval_seconds)
            current_version = await self._refresh_store.version()
            if current_version == self._last_refresh_version:
                continue
            self._last_refresh_version = current_version
            logger.info(
                "Listener refresh version changed",
                extra={
                    "event": "listener_refresh.version_changed",
                    "refresh_version": current_version,
                },
            )
            await self.refresh(reason="poll")

    async def _pubsub_loop(self) -> None:
        pubsub = self._redis.pubsub()
        subscribe = getattr(pubsub, "subscribe", None)
        if subscribe is None:
            return
        await subscribe(LISTENER_REFRESH_CHANNEL)
        try:
            while not self._stop_event.is_set():
                message = await _get_pubsub_message(pubsub, timeout=1.0)
                if not message:
                    continue
                if message.get("type") != "message":
                    continue
                raw_version = message.get("data")
                try:
                    version = int(str(raw_version))
                except (TypeError, ValueError):
                    version = await self._refresh_store.version()
                self._last_refresh_version = version
                logger.info(
                    "Listener refresh pubsub signal received",
                    extra={
                        "event": "listener_refresh.pubsub_signal",
                        "refresh_version": version,
                    },
                )
                await self.refresh(reason="pubsub")
        finally:
            await _close_pubsub(pubsub)

    async def _cancel_background_tasks(self) -> None:
        if not self._background_tasks:
            return
        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks = []

    async def _write_topology_snapshot(
        self,
        *,
        started: bool,
        reason: str,
        sources: list[SourceConfig],
        telegram_sources: list[SourceConfig],
        listeners_count: int,
        adapter_failures: list[str],
        groups: list[ListenerTopologyGroup],
    ) -> None:
        try:
            await self._topology_store.put(
                ListenerTopologySnapshot(
                    generated_at=datetime.now(UTC),
                    refresh_version=await self._refresh_store.version(),
                    listener_account_key=self._listener_account_key,
                    started=started,
                    reason=reason,
                    source_count=len(sources),
                    telegram_source_count=len(telegram_sources),
                    listener_count=listeners_count,
                    adapter_failures=tuple(adapter_failures),
                    groups=tuple(groups),
                )
            )
        except Exception:
            logger.exception(
                "Failed to persist Telegram listener topology snapshot",
                extra={
                    "event": "listener_refresh.snapshot_failed",
                    "reason": reason,
                    "listener_account_key": self._listener_account_key,
                },
            )


def _fingerprint(
    sources: list[SourceConfig],
    *,
    adapter_fingerprint: str,
    default_adapter_name: str,
) -> str:
    return (
        "|".join(
            (
                f"{source.source_id}:{source.enabled}:"
                f"{source.adapter_name or default_adapter_name}:"
                f"{source.source_profile or 'default'}:"
                f"{source.listener_account_key or 'missing'}:"
                f"{source.identifier}"
            )
            for source in sorted(sources, key=lambda item: item.source_id)
        )
        + f"|adapters:{adapter_fingerprint}"
    )


def _adapter_fingerprint(registry: AdapterRegistry, adapter_names: list[str]) -> str:
    parts: list[str] = []
    for adapter_name in adapter_names:
        loaded = registry.get(adapter_name)
        if loaded is None:
            parts.append(f"{adapter_name}:missing")
            continue
        file_hash = getattr(loaded, "file_hash", None)
        manifest = getattr(loaded, "manifest", None)
        manifest_version = getattr(manifest, "version", "")
        fingerprint = file_hash or getattr(loaded, "module_name", adapter_name)
        parts.append(f"{adapter_name}:{fingerprint}:{manifest_version}")
    return "|".join(parts)


async def _get_pubsub_message(pubsub: object, *, timeout: float) -> dict[str, object] | None:
    getter = getattr(pubsub, "get_message", None)
    if getter is not None:
        message = await getter(ignore_subscribe_messages=True, timeout=timeout)
        if isinstance(message, dict):
            return message
        return None
    return None


async def _close_pubsub(pubsub: object) -> None:
    close = getattr(pubsub, "aclose", None)
    if close is None:
        close = getattr(pubsub, "close", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result
