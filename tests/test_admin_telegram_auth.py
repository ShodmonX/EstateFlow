from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.services.telegram_auth import TelegramAuthService, TelegramAuthSessionState


class InMemoryTelegramAuthStore:
    def __init__(self) -> None:
        self.states: dict[str, TelegramAuthSessionState] = {}

    async def get(self, session_name: str) -> TelegramAuthSessionState | None:
        return self.states.get(session_name)

    async def put(self, state: TelegramAuthSessionState, *, ttl_seconds: int) -> None:
        del ttl_seconds
        self.states[state.session_name] = state

    async def delete(self, session_name: str) -> None:
        self.states.pop(session_name, None)


class FakeRefreshStore:
    def __init__(self) -> None:
        self.version_value = 0

    async def version(self) -> int:
        return self.version_value

    async def signal(self) -> int:
        self.version_value += 1
        return self.version_value


@dataclass
class FakeTelethonClient:
    session_path: Path
    api_id: int
    api_hash: str
    authorized: bool = False
    needs_password: bool = False

    def __post_init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self.sent_phone: str | None = None
        self.sent_force_sms: bool | None = None
        self.code: str | None = None
        self.password: str | None = None

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def send_code_request(self, phone_number: str, force_sms: bool = False) -> Any:
        self.sent_phone = phone_number
        self.sent_force_sms = force_sms
        return SimpleNamespace(phone_code_hash="hash-123")

    async def sign_in(
        self,
        phone_number: str | None = None,
        code: str | None = None,
        *,
        phone_code_hash: str | None = None,
        password: str | None = None,
    ) -> Any:
        del phone_number, phone_code_hash
        if password is not None:
            self.password = password
            self.authorized = True
            return SimpleNamespace(id=1)
        self.code = code
        if self.needs_password:
            from telethon.errors import SessionPasswordNeededError  # type: ignore[import-untyped]

            raise SessionPasswordNeededError(request=None)
        self.authorized = True
        return SimpleNamespace(id=1)


class FakeTelethonFactory:
    def __init__(self) -> None:
        self.clients: dict[str, FakeTelethonClient] = {}

    def __call__(self, session_path: Path, api_id: int, api_hash: str) -> FakeTelethonClient:
        key = str(session_path)
        client = self.clients.get(key)
        if client is None:
            client = FakeTelethonClient(
                session_path=session_path,
                api_id=api_id,
                api_hash=api_hash,
            )
            self.clients[key] = client
        return client


@pytest.mark.asyncio
async def test_telegram_auth_service_handles_code_and_password_flow() -> None:
    settings = Settings(
        environment="test",
        telegram_api_id=12345,
        telegram_api_hash=SecretStr("secret"),
        telegram_session_dir="data/test-sessions",
    )
    factory = FakeTelethonFactory()
    store = InMemoryTelegramAuthStore()
    service = TelegramAuthService(settings, store=store, client_factory=factory)
    assert service.session_path("estateflow_channel").parent.exists()

    start = await service.start_login(
        session_name="estateflow_channel",
        phone_number="+998901234567",
    )
    session_key = str(service.session_path("estateflow_channel"))
    assert start.code_sent is True
    assert start.already_authorized is False
    assert store.states["estateflow_channel"].phone_code_hash == "hash-123"

    factory.clients[session_key].needs_password = True
    code_result = await service.confirm_code(
        session_name="estateflow_channel",
        code="12345",
    )
    assert code_result.authorized is False
    assert code_result.requires_password is True
    assert store.states["estateflow_channel"].requires_password is True

    password_result = await service.confirm_password(
        session_name="estateflow_channel",
        password="strong-password",
    )
    assert password_result.authorized is True
    assert "estateflow_channel" not in store.states
    assert factory.clients[session_key].password == "strong-password"


@pytest.mark.asyncio
async def test_admin_telegram_auth_endpoints_use_admin_token_and_persist_session() -> None:
    settings = Settings(
        environment="test",
        admin_api_token=SecretStr("secret-admin"),
        telegram_api_id=12345,
        telegram_api_hash=SecretStr("secret"),
        telegram_session_dir="data/test-sessions-api",
    )
    app = create_app(settings)
    factory = FakeTelethonFactory()
    store = InMemoryTelegramAuthStore()
    refresh = FakeRefreshStore()
    auth_service = TelegramAuthService(
        settings,
        store=store,
        client_factory=factory,
    )
    app.state.telegram_auth_service = auth_service
    app.state.listener_refresh_store = refresh

    with TestClient(app) as client:
        start = client.post(
            "/admin/telegram/auth/start",
            headers={"X-Admin-Token": "secret-admin"},
            json={
                "session_name": "estateflow_api",
                "phone_number": "+998901111222",
            },
        )
        state = client.get(
            "/admin/telegram/auth/state/estateflow_api",
            headers={"X-Admin-Token": "secret-admin"},
        )
        code = client.post(
            "/admin/telegram/auth/confirm-code",
            headers={"X-Admin-Token": "secret-admin"},
            json={
                "session_name": "estateflow_api",
                "code": "12345",
            },
        )
        deleted = client.delete(
            "/admin/telegram/auth/estateflow_api",
            headers={"X-Admin-Token": "secret-admin"},
        )

    assert start.status_code == 200
    assert code.status_code == 200
    assert state.status_code == 200
    assert deleted.status_code == 200
    assert start.json()["code_sent"] is True
    assert code.json()["authorized"] is True
    assert state.json()["phone_code_hash_present"] is True
    assert deleted.json()["deleted"] is True
    session_key = str(auth_service.session_path("estateflow_api"))
    assert factory.clients[session_key].sent_phone == "+998901111222"
    assert refresh.version_value >= 2
