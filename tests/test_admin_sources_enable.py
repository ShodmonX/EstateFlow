from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.adapters.contracts import AdapterReloadReport
from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.services.listener_refresh import (
    RedisListenerRefreshStore,
    TelegramListenerCoordinator,
)
from estateflow.services.listener_status import (
    ListenerAccountTopology,
    ListenerAssignmentTopology,
    ListenerSourceTopology,
    ListenerTopologyGroup,
    ListenerTopologySnapshot,
    ListenerTopologyStatus,
)
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.source_config import SourceConfig


class FakeSourceRegistry:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.sources: dict[str, SourceConfig] = {}

    async def create_or_enable_source(
        self,
        *,
        source_type: str,
        identifier: str,
        name: str,
        adapter_name: str | None = None,
        source_profile: str | None = None,
        session_name: str | None = None,
    ) -> tuple[SourceConfig, bool]:
        source_id = f"{source_type}:{identifier}"
        created = source_id not in self.sources
        source = SourceConfig(
            source_id=source_id,
            name=name,
            source_type=source_type,  # type: ignore[arg-type]
            identifier=identifier,
            enabled=True,
            adapter_name=adapter_name,
            source_profile=source_profile,
            listener_account_key=session_name,
        )
        self.sources[source_id] = source
        self.calls.append(
            {
                "source_type": source_type,
                "identifier": identifier,
                "name": name,
                "adapter_name": adapter_name,
                "source_profile": source_profile,
                "session_name": session_name,
            }
        )
        return source, created


class FakeTelegramAuthService:
    def __init__(self) -> None:
        self.access_checks: list[tuple[str, str]] = []

    async def verify_source_access(self, *, session_name: str, source_identifier: str) -> object:
        self.access_checks.append((session_name, source_identifier))
        return SimpleNamespace(
            session_name=session_name,
            source_identifier=source_identifier,
            accessible=True,
            status="accessible",
        )


class FakeTelegramSessionInventoryService:
    async def list_sessions(self) -> list[object]:
        return [
            SimpleNamespace(
                session_name="acc_9889",
                session_path="/tmp/estateflow/data/sessions/acc_9889",
                session_file_path="/tmp/estateflow/data/sessions/acc_9889.session",
                file_exists=True,
                file_size_bytes=4096,
                authorized=True,
                auth_state_present=False,
                requires_password=False,
                updated_at="2026-08-05T00:00:00+00:00",
                status="ready",
                error=None,
                bound_sources=(
                    SimpleNamespace(
                        source_id="telegram_channel:@estateflow_test_channel",
                        source_type="telegram_channel",
                        identifier="@estateflow_test_channel",
                        name="EstateFlow Test Channel",
                        source_profile="caption_first",
                        enabled=True,
                        session_name="acc_9889",
                    ),
                ),
            )
        ]


class FakeListenerRefreshStore:
    def __init__(self) -> None:
        self.version_value = 0

    async def version(self) -> int:
        return self.version_value

    async def signal(self) -> int:
        self.version_value += 1
        return self.version_value


@dataclass
class FakeTelegramClient:
    connected: bool = False
    disconnected: bool = False
    manifest: Any = field(
        default_factory=lambda: SimpleNamespace(
            family="telegram",
            source_types=("telegram_channel", "telegram_group"),
            version="1.0.0",
        )
    )

    def __post_init__(self) -> None:
        self.subscribed_sources: list[SourceConfig] = []

    async def connect(self) -> None:
        self.connected = True

    async def subscribe(self, sources: list[SourceConfig]) -> None:
        self.subscribed_sources = list(sources)

    def iter_updates(self) -> AsyncIterator[object]:
        async def _empty() -> AsyncIterator[object]:
            if False:
                yield None

        return _empty()

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeAdapterRegistry:
    def __init__(self, modules: dict[str, str]) -> None:
        self._plugins = {
            plugin_name: SimpleNamespace(module_name=module_name)
            for plugin_name, module_name in modules.items()
        }

    def get(self, plugin_name: str) -> object | None:
        return self._plugins.get(plugin_name)


class FakeAdapterReloadRegistry:
    def __init__(self) -> None:
        self.reload_count = 0

    async def reload(self) -> AdapterReloadReport:
        self.reload_count += 1
        return AdapterReloadReport(
            plugin_directory="tests",
            loaded_count=1,
            failed_count=0,
        )


class FakeListenerTopologyService:
    async def snapshot(self) -> ListenerTopologyStatus:
        worker_snapshot = ListenerTopologySnapshot(
            generated_at=datetime.now(UTC),
            refresh_version=7,
            listener_account_key="listener-default",
            started=True,
            reason="startup",
            source_count=1,
            telegram_source_count=1,
            listener_count=1,
            adapter_failures=(),
            groups=(
                ListenerTopologyGroup(
                    adapter_name="telegram_telethon",
                    source_profile="caption_first",
                    source_count=1,
                    source_ids=("telegram_channel:@estateflow_test_channel",),
                    source_identifiers=("@estateflow_test_channel",),
                    source_names=("EstateFlow Test Channel",),
                ),
            ),
        )
        return ListenerTopologyStatus(
            generated_at=datetime.now(UTC),
            refresh_version=7,
            session_dir="/tmp/estateflow/data/sessions",
            adapter_report=AdapterReloadReport(
                plugin_directory="tests/plugins",
                loaded_count=1,
                failed_count=0,
            ),
            worker_snapshot=worker_snapshot,
            sources=(
                ListenerSourceTopology(
                    source_id="telegram_channel:@estateflow_test_channel",
                    name="EstateFlow Test Channel",
                    source_type="telegram_channel",
                    identifier="@estateflow_test_channel",
                    enabled=True,
                    adapter_name="telegram_telethon",
                    source_profile="caption_first",
                    listener_account_key="listener-default",
                    assigned_account_key="listener-default",
                    assignment_active=True,
                    assignment_reason="initial",
                    session_name_hint="listener-default",
                    session_path_hint="/tmp/estateflow/data/sessions/listener-default",
                ),
            ),
            accounts=(
                ListenerAccountTopology(
                    account_key="listener-default",
                    enabled=True,
                    health_status="online",
                    last_successful_event_at=None,
                    flood_wait_count=0,
                    assigned_source_count=1,
                    session_name_hint="listener-default",
                    session_path_hint="/tmp/estateflow/data/sessions/listener-default",
                ),
            ),
            assignments=(
                ListenerAssignmentTopology(
                    source_id="telegram_channel:@estateflow_test_channel",
                    account_key="listener-default",
                    assigned_at=datetime.now(UTC),
                    active=True,
                    released_at=None,
                    reason="initial",
                ),
            ),
        )


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}
        self._pubsubs: list[FakePubSub] = []

    async def get(self, key: str) -> str | None:
        value = self.values.get(key)
        return None if value is None else str(value)

    async def incr(self, key: str) -> int:
        current = int(self.values.get(key, 0) or 0) + 1
        self.values[key] = current
        return current

    async def publish(self, channel: str, data: str) -> int:
        message = {"type": "message", "channel": channel, "data": data}
        for pubsub in list(self._pubsubs):
            await pubsub.queue.put(message)
        return len(self._pubsubs)

    async def rpush(self, *_args: object, **_kwargs: object) -> int:
        return 1

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def exists(self, key: str) -> int:
        return 1 if key in self.values else 0

    def pubsub(self) -> FakePubSub:
        pubsub = FakePubSub(self)
        self._pubsubs.append(pubsub)
        return pubsub

    async def aclose(self) -> None:
        return None


class FakePubSub:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self.queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self.channels: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        self.channels.extend(channels)

    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool = True,
        timeout: float = 0.0,
    ) -> dict[str, str] | None:
        del ignore_subscribe_messages
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    async def aclose(self) -> None:
        self._redis._pubsubs = [pubsub for pubsub in self._redis._pubsubs if pubsub is not self]


@pytest.mark.asyncio
async def test_admin_sources_enable_signals_listener_refresh() -> None:
    settings = Settings(environment="test", admin_api_token=SecretStr("secret-admin"))
    app = create_app(settings)
    fake_registry = FakeSourceRegistry()
    fake_refresh = FakeListenerRefreshStore()
    fake_auth = FakeTelegramAuthService()
    app.state.source_registry = fake_registry
    app.state.listener_refresh_store = fake_refresh
    app.state.telegram_auth_service = fake_auth

    with TestClient(app) as client:
        response = client.post(
            "/admin/sources/enable",
            headers={"X-Admin-Token": "secret-admin"},
            json={
                "source_type": "telegram_channel",
                "identifier": "@estateflow_test_channel",
                "name": "EstateFlow Test Channel",
                "adapter_name": "telegram_telethon",
                "source_profile": "caption_first",
                "session_name": "acc_9889",
            },
        )
        refresh_response = client.post(
            "/admin/listeners/refresh",
            headers={"X-Admin-Token": "secret-admin"},
        )

    assert response.status_code == 200
    assert refresh_response.status_code == 200
    payload = response.json()
    refresh_payload = refresh_response.json()
    assert payload["source_id"] == "telegram_channel:@estateflow_test_channel"
    assert payload["created"] is True
    assert payload["enabled"] is True
    assert payload["source_profile"] == "caption_first"
    assert payload["session_name"] == "acc_9889"
    assert payload["refresh_version"] == 1
    assert refresh_payload["refresh_version"] == 2
    assert fake_registry.calls == [
        {
            "source_type": "telegram_channel",
            "identifier": "@estateflow_test_channel",
            "name": "EstateFlow Test Channel",
            "adapter_name": "telegram_telethon",
            "source_profile": "caption_first",
            "session_name": "acc_9889",
        }
    ]
    assert fake_auth.access_checks == [
        ("acc_9889", "@estateflow_test_channel"),
    ]


@pytest.mark.asyncio
async def test_admin_sources_enable_requires_session_for_telegram_sources() -> None:
    settings = Settings(environment="test", admin_api_token=SecretStr("secret-admin"))
    app = create_app(settings)
    fake_registry = FakeSourceRegistry()
    fake_refresh = FakeListenerRefreshStore()
    app.state.source_registry = fake_registry
    app.state.listener_refresh_store = fake_refresh

    with TestClient(app) as client:
        response = client.post(
            "/admin/sources/enable",
            headers={"X-Admin-Token": "secret-admin"},
            json={
                "source_type": "telegram_channel",
                "identifier": "@estateflow_test_channel",
                "name": "EstateFlow Test Channel",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "telegram_session_name_required"


@pytest.mark.asyncio
async def test_admin_telegram_sessions_endpoint_lists_status_and_bindings() -> None:
    settings = Settings(environment="test", admin_api_token=SecretStr("secret-admin"))
    app = create_app(settings)
    app.state.telegram_session_inventory_service = FakeTelegramSessionInventoryService()

    with TestClient(app) as client:
        response = client.get(
            "/admin/telegram/sessions",
            headers={"X-Admin-Token": "secret-admin"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["session_name"] == "acc_9889"
    assert payload[0]["status"] == "ready"
    assert payload[0]["bound_sources"][0]["session_name"] == "acc_9889"


@pytest.mark.asyncio
async def test_admin_adapter_reload_signals_listener_refresh() -> None:
    settings = Settings(environment="test", admin_api_token=SecretStr("secret-admin"))
    app = create_app(settings)
    fake_registry = FakeAdapterReloadRegistry()
    fake_refresh = FakeListenerRefreshStore()
    app.state.adapter_registry = fake_registry
    app.state.listener_refresh_store = fake_refresh

    with TestClient(app) as client:
        response = client.post(
            "/admin/adapters/reload",
            headers={"X-Admin-Token": "secret-admin"},
        )

    assert response.status_code == 200
    assert fake_registry.reload_count == 1
    assert fake_refresh.version_value == 1


@pytest.mark.asyncio
async def test_admin_listener_status_reports_sources_adapters_and_session_hints() -> None:
    settings = Settings(environment="test", admin_api_token=SecretStr("secret-admin"))
    app = create_app(settings)
    app.state.listener_topology_service = FakeListenerTopologyService()

    with TestClient(app) as client:
        response = client.get(
            "/admin/listeners/status",
            headers={"X-Admin-Token": "secret-admin"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refresh_version"] == 7
    assert payload["session_dir"] == "/tmp/estateflow/data/sessions"
    assert payload["adapter_report"]["loaded_count"] == 1
    assert payload["worker_snapshot"]["started"] is True
    assert payload["sources"][0]["source_profile"] == "caption_first"
    assert payload["sources"][0]["session_path_hint"].endswith("listener-default")
    assert payload["listener_accounts"][0]["assigned_source_count"] == 1
    assert payload["assignments"][0]["reason"] == "initial"


@pytest.mark.asyncio
async def test_listener_refresh_rebuilds_subscription_on_signal() -> None:
    settings = Settings(environment="test")
    source = SourceConfig(
        source_id="telegram_channel:@estateflow_test_channel",
        name="EstateFlow Test Channel",
        source_type="telegram_channel",
        identifier="@estateflow_test_channel",
        listener_account_key="acc_9889",
    )
    created_clients: list[FakeTelegramClient] = []

    module_name = "estateflow.test_listener_refresh_plugin"
    module = ModuleType(module_name)

    def create_plugin(_settings: Settings) -> FakeTelegramClient:
        client = FakeTelegramClient()
        created_clients.append(client)
        return client

    module.create_plugin = create_plugin  # type: ignore[attr-defined]

    import sys

    sys.modules[module_name] = module
    try:
        coordinator = TelegramListenerCoordinator(
            settings=settings,
            adapter_registry=cast(Any, FakeAdapterRegistry({"telegram_telethon": module_name})),
            source_provider=_MutableSourceProvider([source]),
            refresh_store=FakeListenerRefreshStore(),
            redis=cast(Any, FakeRedis()),
            ops_notifier=DisabledOpsNotificationService(),
            poll_interval_seconds=0.01,
        )
        report = await coordinator.refresh(reason="manual-test")
        await asyncio.sleep(0)
        await coordinator.aclose()
    finally:
        sys.modules.pop(module_name, None)

    assert report.started is True
    assert report.telegram_source_count == 1
    assert created_clients
    assert created_clients[0].connected is True
    assert created_clients[0].disconnected is True
    assert [source.identifier for source in created_clients[0].subscribed_sources] == [
        "@estateflow_test_channel"
    ]


@pytest.mark.asyncio
async def test_listener_refresh_uses_session_name_when_building_telegram_plugin() -> None:
    settings = Settings(environment="test")
    sources = [
        SourceConfig(
            source_id="telegram_channel:@estateflow_session_a",
            name="Session A",
            source_type="telegram_channel",
            identifier="@estateflow_session_a",
            adapter_name="telegram_telethon",
            source_profile="caption_first",
            listener_account_key="acc_9889",
        ),
        SourceConfig(
            source_id="telegram_channel:@estateflow_session_b",
            name="Session B",
            source_type="telegram_channel",
            identifier="@estateflow_session_b",
            adapter_name="telegram_telethon",
            source_profile="caption_first",
            listener_account_key="acc_9999",
        ),
    ]
    created_session_names: list[str] = []
    created_clients: list[FakeTelegramClient] = []

    module_name = "estateflow.test_listener_refresh_session_plugin"
    module = ModuleType(module_name)

    def create_plugin(
        _settings: Settings,
        *,
        session_name: str | None = None,
    ) -> FakeTelegramClient:
        if session_name is None:
            raise AssertionError("session_name is required")
        created_session_names.append(session_name)
        client = FakeTelegramClient()
        created_clients.append(client)
        return client

    module.create_plugin = create_plugin  # type: ignore[attr-defined]

    import sys

    sys.modules[module_name] = module
    try:
        coordinator = TelegramListenerCoordinator(
            settings=settings,
            adapter_registry=cast(Any, FakeAdapterRegistry({"telegram_telethon": module_name})),
            source_provider=_MutableSourceProvider(sources),
            refresh_store=FakeListenerRefreshStore(),
            redis=cast(Any, FakeRedis()),
            ops_notifier=DisabledOpsNotificationService(),
            poll_interval_seconds=0.01,
        )
        report = await coordinator.refresh(reason="session-binding")
        await asyncio.sleep(0)
        await coordinator.aclose()
    finally:
        sys.modules.pop(module_name, None)

    assert report.started is True
    assert created_session_names == ["acc_9889", "acc_9999"]
    assert len(created_clients) == 2


@pytest.mark.asyncio
async def test_listener_refresh_groups_sources_by_adapter_name() -> None:
    settings = Settings(environment="test")
    sources = [
        SourceConfig(
            source_id="telegram_channel:@estateflow_alpha",
            name="Alpha Channel",
            source_type="telegram_channel",
            identifier="@estateflow_alpha",
            adapter_name="telegram_alpha",
            listener_account_key="acc_9889",
        ),
        SourceConfig(
            source_id="telegram_channel:@estateflow_beta",
            name="Beta Channel",
            source_type="telegram_channel",
            identifier="@estateflow_beta",
            adapter_name="telegram_beta",
            listener_account_key="acc_9999",
        ),
    ]
    created_clients: dict[str, FakeTelegramClient] = {}

    import sys

    modules: dict[str, ModuleType] = {}
    for plugin_name in ("telegram_alpha", "telegram_beta"):
        module = ModuleType(f"estateflow.test_{plugin_name}")

        def create_plugin(
            _settings: Settings,
            *,
            _plugin_name: str = plugin_name,
        ) -> FakeTelegramClient:
            client = FakeTelegramClient()
            created_clients[_plugin_name] = client
            return client

        module.create_plugin = create_plugin  # type: ignore[attr-defined]
        sys.modules[module.__name__] = module
        modules[plugin_name] = module

    try:
        coordinator = TelegramListenerCoordinator(
            settings=settings,
            adapter_registry=cast(
                Any,
                FakeAdapterRegistry({name: module.__name__ for name, module in modules.items()}),
            ),
            source_provider=_MutableSourceProvider(sources),
            refresh_store=FakeListenerRefreshStore(),
            redis=cast(Any, FakeRedis()),
            ops_notifier=DisabledOpsNotificationService(),
            poll_interval_seconds=0.01,
        )
        report = await coordinator.refresh(reason="multi-adapter")
        await asyncio.sleep(0)
        await coordinator.aclose()
    finally:
        for module in modules.values():
            sys.modules.pop(module.__name__, None)

    assert report.started is True
    assert report.telegram_source_count == 2
    assert set(created_clients) == {"telegram_alpha", "telegram_beta"}
    assert [
        source.identifier for source in created_clients["telegram_alpha"].subscribed_sources
    ] == ["@estateflow_alpha"]
    assert [
        source.identifier for source in created_clients["telegram_beta"].subscribed_sources
    ] == ["@estateflow_beta"]


@pytest.mark.asyncio
async def test_listener_refresh_groups_sources_by_profile_under_same_adapter() -> None:
    settings = Settings(environment="test")
    sources = [
        SourceConfig(
            source_id="telegram_channel:@estateflow_album_a",
            name="Album A",
            source_type="telegram_channel",
            identifier="@estateflow_album_a",
            adapter_name="telegram_telethon",
            source_profile="caption_first",
            listener_account_key="acc_9889",
        ),
        SourceConfig(
            source_id="telegram_channel:@estateflow_album_b",
            name="Album B",
            source_type="telegram_channel",
            identifier="@estateflow_album_b",
            adapter_name="telegram_telethon",
            source_profile="album_text_merge",
            listener_account_key="acc_9889",
        ),
    ]
    created_clients: list[FakeTelegramClient] = []

    module_name = "estateflow.test_listener_refresh_profile_plugin"
    module = ModuleType(module_name)

    def create_plugin(_settings: Settings) -> FakeTelegramClient:
        client = FakeTelegramClient()
        created_clients.append(client)
        return client

    module.create_plugin = create_plugin  # type: ignore[attr-defined]

    import sys

    sys.modules[module_name] = module
    try:
        coordinator = TelegramListenerCoordinator(
            settings=settings,
            adapter_registry=cast(Any, FakeAdapterRegistry({"telegram_telethon": module_name})),
            source_provider=_MutableSourceProvider(sources),
            refresh_store=FakeListenerRefreshStore(),
            redis=cast(Any, FakeRedis()),
            ops_notifier=DisabledOpsNotificationService(),
            poll_interval_seconds=0.01,
        )
        report = await coordinator.refresh(reason="profile-grouping")
        await asyncio.sleep(0)
        await coordinator.aclose()
    finally:
        sys.modules.pop(module_name, None)

    assert report.started is True
    assert report.telegram_source_count == 2
    assert len(created_clients) == 2
    assert sorted(
        [source.identifier for source in client.subscribed_sources] for client in created_clients
    ) == [["@estateflow_album_a"], ["@estateflow_album_b"]]


@pytest.mark.asyncio
async def test_listener_refresh_signal_reaches_worker_via_pubsub() -> None:
    settings = Settings(environment="test")
    source = SourceConfig(
        source_id="telegram_channel:@estateflow_test_channel",
        name="EstateFlow Test Channel",
        source_type="telegram_channel",
        identifier="@estateflow_test_channel",
        listener_account_key="acc_9889",
    )
    created_clients: list[FakeTelegramClient] = []

    module_name = "estateflow.test_listener_refresh_pubsub_plugin"
    module = ModuleType(module_name)

    def create_plugin(_settings: Settings) -> FakeTelegramClient:
        client = FakeTelegramClient()
        created_clients.append(client)
        return client

    module.create_plugin = create_plugin  # type: ignore[attr-defined]

    import sys

    fake_redis = FakeRedis()
    provider = _MutableSourceProvider([])
    sys.modules[module_name] = module
    run_task: asyncio.Task[None] | None = None
    try:
        coordinator = TelegramListenerCoordinator(
            settings=settings,
            adapter_registry=cast(Any, FakeAdapterRegistry({"telegram_telethon": module_name})),
            source_provider=provider,
            refresh_store=RedisListenerRefreshStore(cast(Any, fake_redis)),
            redis=cast(Any, fake_redis),
            ops_notifier=DisabledOpsNotificationService(),
            poll_interval_seconds=60.0,
        )
        run_task = asyncio.create_task(coordinator.run_forever())
        await asyncio.wait_for(_wait_for(lambda: bool(fake_redis._pubsubs)), timeout=1.0)
        await asyncio.wait_for(
            _wait_for(
                lambda: any(pubsub.channels for pubsub in fake_redis._pubsubs),
            ),
            timeout=1.0,
        )
        provider.sources = [source]
        await coordinator.signal_refresh()
        await asyncio.wait_for(_wait_for(lambda: bool(created_clients)), timeout=1.0)
    finally:
        if "coordinator" in locals():
            await coordinator.aclose()
        if run_task is not None:
            await run_task
        sys.modules.pop(module_name, None)

    assert created_clients
    assert created_clients[0].connected is True
    assert [source.identifier for source in created_clients[0].subscribed_sources] == [
        "@estateflow_test_channel"
    ]


class _MutableSourceProvider:
    def __init__(self, sources: list[SourceConfig]) -> None:
        self.sources = sources

    async def list_active_sources(self) -> list[SourceConfig]:
        return list(self.sources)


async def _wait_for(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0.01)
