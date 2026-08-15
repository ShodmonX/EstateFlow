from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy import select

from estateflow.adapters.contracts import AdapterReloadReport
from estateflow.adapters.registry import AdapterRegistry
from estateflow.db.session import DatabaseSessionManager
from estateflow.models.listeners import ChannelAssignmentRecord, ListenerAccountRecord
from estateflow.services.source_config import SourceConfig, SourceConfigProvider
from estateflow.services.telegram_auth import TelegramAuthService

LISTENER_TOPOLOGY_KEY = "estateflow:listener-topology"


@dataclass(frozen=True)
class ListenerTopologyGroup:
    adapter_name: str
    source_profile: str | None
    source_count: int
    source_ids: tuple[str, ...]
    source_identifiers: tuple[str, ...]
    source_names: tuple[str, ...]
    runnable: bool = True
    error: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "adapter_name": self.adapter_name,
            "source_profile": self.source_profile,
            "source_count": self.source_count,
            "source_ids": list(self.source_ids),
            "source_identifiers": list(self.source_identifiers),
            "source_names": list(self.source_names),
            "runnable": self.runnable,
            "error": self.error,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> ListenerTopologyGroup:
        source_profile = payload.get("source_profile")
        error = payload.get("error")
        return cls(
            adapter_name=str(payload["adapter_name"]),
            source_profile=str(source_profile) if source_profile else None,
            source_count=_payload_int(payload.get("source_count")),
            source_ids=_payload_strings(payload.get("source_ids")),
            source_identifiers=_payload_strings(payload.get("source_identifiers")),
            source_names=_payload_strings(payload.get("source_names")),
            runnable=bool(payload.get("runnable", True)),
            error=str(error) if error else None,
        )


@dataclass(frozen=True)
class ListenerTopologySnapshot:
    generated_at: datetime
    refresh_version: int
    listener_account_key: str
    started: bool
    reason: str
    source_count: int
    telegram_source_count: int
    listener_count: int
    adapter_failures: tuple[str, ...] = ()
    groups: tuple[ListenerTopologyGroup, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "refresh_version": self.refresh_version,
            "listener_account_key": self.listener_account_key,
            "started": self.started,
            "reason": self.reason,
            "source_count": self.source_count,
            "telegram_source_count": self.telegram_source_count,
            "listener_count": self.listener_count,
            "adapter_failures": list(self.adapter_failures),
            "groups": [group.to_payload() for group in self.groups],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> ListenerTopologySnapshot:
        return cls(
            generated_at=datetime.fromisoformat(str(payload["generated_at"])),
            refresh_version=_payload_int(payload.get("refresh_version")),
            listener_account_key=str(payload.get("listener_account_key", "")),
            started=bool(payload.get("started", False)),
            reason=str(payload.get("reason", "unknown")),
            source_count=_payload_int(payload.get("source_count")),
            telegram_source_count=_payload_int(payload.get("telegram_source_count")),
            listener_count=_payload_int(payload.get("listener_count")),
            adapter_failures=_payload_strings(payload.get("adapter_failures")),
            groups=tuple(
                ListenerTopologyGroup.from_payload(dict(item))
                for item in _payload_items(payload.get("groups"))
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True)
class ListenerAccountTopology:
    account_key: str
    enabled: bool
    health_status: str
    last_successful_event_at: datetime | None
    flood_wait_count: int
    assigned_source_count: int
    session_name_hint: str
    session_path_hint: str


@dataclass(frozen=True)
class ListenerAssignmentTopology:
    source_id: str
    account_key: str
    assigned_at: datetime
    active: bool
    released_at: datetime | None
    reason: str


@dataclass(frozen=True)
class ListenerSourceTopology:
    source_id: str
    name: str
    source_type: str
    identifier: str
    enabled: bool
    adapter_name: str | None
    source_profile: str | None
    listener_account_key: str | None
    assigned_account_key: str | None
    assignment_active: bool
    assignment_reason: str | None
    session_name_hint: str | None
    session_path_hint: str | None


@dataclass(frozen=True)
class ListenerTopologyStatus:
    generated_at: datetime
    refresh_version: int
    session_dir: str
    adapter_report: AdapterReloadReport
    worker_snapshot: ListenerTopologySnapshot | None
    sources: tuple[ListenerSourceTopology, ...]
    accounts: tuple[ListenerAccountTopology, ...]
    assignments: tuple[ListenerAssignmentTopology, ...]


class ListenerTopologyStore(Protocol):
    async def get(self) -> ListenerTopologySnapshot | None: ...

    async def put(self, snapshot: ListenerTopologySnapshot) -> None: ...


class NoopListenerTopologyStore:
    async def get(self) -> ListenerTopologySnapshot | None:
        return None

    async def put(self, snapshot: ListenerTopologySnapshot) -> None:
        del snapshot
        return None


class RedisListenerTopologyStore:
    def __init__(self, redis: Redis, *, key: str = LISTENER_TOPOLOGY_KEY) -> None:
        self._redis = redis
        self._key = key

    async def get(self) -> ListenerTopologySnapshot | None:
        payload = await self._redis.get(self._key)
        if payload is None:
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            return ListenerTopologySnapshot.from_payload(data)
        except (KeyError, TypeError, ValueError):
            return None

    async def put(self, snapshot: ListenerTopologySnapshot) -> None:
        await self._redis.set(self._key, json.dumps(snapshot.to_payload()))


class ListenerTopologyService:
    def __init__(
        self,
        *,
        db: DatabaseSessionManager,
        source_provider: SourceConfigProvider,
        adapter_registry: AdapterRegistry,
        telegram_auth_service: TelegramAuthService,
        topology_store: ListenerTopologyStore | None = None,
    ) -> None:
        self._db = db
        self._source_provider = source_provider
        self._adapter_registry = adapter_registry
        self._telegram_auth_service = telegram_auth_service
        self._topology_store = topology_store or NoopListenerTopologyStore()

    async def snapshot(self) -> ListenerTopologyStatus:
        active_sources = await self._source_provider.list_active_sources()
        worker_snapshot = await self._topology_store.get()
        adapter_report = _adapter_report(self._adapter_registry)
        accounts = await self._list_accounts()
        assignments = await self._list_assignments()
        assignment_by_source = {item.source_id: item for item in assignments if item.active}
        assignment_counts = Counter(item.account_key for item in assignments if item.active)
        sources = tuple(
            _source_topology(
                source,
                assignment_by_source.get(source.source_id),
                self._telegram_auth_service,
            )
            for source in active_sources
        )
        return ListenerTopologyStatus(
            generated_at=datetime.now(UTC),
            refresh_version=worker_snapshot.refresh_version if worker_snapshot is not None else 0,
            session_dir=str(self._telegram_auth_service.session_dir),
            adapter_report=adapter_report,
            worker_snapshot=worker_snapshot,
            sources=sources,
            accounts=tuple(
                ListenerAccountTopology(
                    account_key=account.account_key,
                    enabled=account.enabled,
                    health_status=account.health_status,
                    last_successful_event_at=account.last_successful_event_at,
                    flood_wait_count=account.flood_wait_count,
                    assigned_source_count=assignment_counts.get(account.account_key, 0),
                    session_name_hint=account.account_key,
                    session_path_hint=str(
                        self._telegram_auth_service.session_path(account.account_key)
                    ),
                )
                for account in accounts
            ),
            assignments=tuple(
                ListenerAssignmentTopology(
                    source_id=assignment.source_id,
                    account_key=assignment.account_key,
                    assigned_at=assignment.assigned_at,
                    active=assignment.active,
                    released_at=assignment.released_at,
                    reason=assignment.reason,
                )
                for assignment in assignments
            ),
        )

    async def _list_accounts(self) -> list[ListenerAccountRecord]:
        async with self._db.session() as session:
            rows = (
                await session.scalars(
                    select(ListenerAccountRecord).order_by(ListenerAccountRecord.account_key)
                )
            ).all()
            return list(rows)

    async def _list_assignments(self) -> list[ChannelAssignmentRecord]:
        async with self._db.session() as session:
            rows = (
                await session.scalars(
                    select(ChannelAssignmentRecord).order_by(
                        ChannelAssignmentRecord.source_id,
                        ChannelAssignmentRecord.assigned_at,
                    )
                )
            ).all()
            return list(rows)


def _payload_items(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _payload_strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _payload_items(value))


def _payload_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        return int(value)
    return 0


def _source_topology(
    source: SourceConfig,
    assignment: ChannelAssignmentRecord | None,
    telegram_auth_service: TelegramAuthService,
) -> ListenerSourceTopology:
    assigned_account_key = assignment.account_key if assignment is not None else None
    return ListenerSourceTopology(
        source_id=source.source_id,
        name=source.name,
        source_type=source.source_type,
        identifier=source.identifier,
        enabled=source.enabled,
        adapter_name=source.adapter_name,
        source_profile=source.source_profile,
        listener_account_key=source.listener_account_key,
        assigned_account_key=assigned_account_key,
        assignment_active=assignment.active if assignment is not None else False,
        assignment_reason=assignment.reason if assignment is not None else None,
        session_name_hint=_session_name_hint(source.listener_account_key, assigned_account_key),
        session_path_hint=_session_path_hint(
            telegram_auth_service,
            source.listener_account_key,
            assigned_account_key,
        ),
    )


def _adapter_report(registry: AdapterRegistry) -> AdapterReloadReport:
    from estateflow.adapters.contracts import AdapterLoadFailure, AdapterLoadResult

    return AdapterReloadReport(
        plugin_directory=str(registry.plugin_directory),
        loaded_count=len(registry.list_plugins()),
        failed_count=len(registry.list_failures()),
        loaded_plugins=[
            AdapterLoadResult(
                plugin_name=item.plugin_name,
                module_path=str(item.module_path),
                state="loaded",
                manifest=item.manifest,
            )
            for item in registry.list_plugins()
        ],
        failures=[
            AdapterLoadFailure(
                plugin_name=item.plugin_name,
                module_path=item.module_path,
                error=item.error,
            )
            for item in registry.list_failures()
        ],
    )


def _session_name_hint(
    source_listener_account_key: str | None,
    assigned_account_key: str | None,
) -> str | None:
    return assigned_account_key or source_listener_account_key


def _session_path_hint(
    telegram_auth_service: TelegramAuthService,
    source_listener_account_key: str | None,
    assigned_account_key: str | None,
) -> str | None:
    session_name = _session_name_hint(source_listener_account_key, assigned_account_key)
    if session_name is None:
        return None
    return str(telegram_auth_service.session_path(session_name))
