from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.bot.controller import BotController
from estateflow.repositories.users import InMemoryUserRepository
from estateflow.services.content_automation import PublishResult
from estateflow.services.notifications import TelegramSendResult
from estateflow.services.release_controls import (
    BetaCohortPolicy,
    FeatureFlagSet,
    FeatureGatedContentPublisher,
    FeatureGatedSourceProvider,
    FeatureGatedTelegramNotificationClient,
    InMemoryFeatureFlagStore,
    NotificationsDisabledError,
    ReleaseControlService,
)
from estateflow.services.source_config import InMemorySourceRegistry, SourceConfig
from estateflow.services.users import UserService


@pytest.mark.asyncio
async def test_release_flags_default_safe_and_audited() -> None:
    service = ReleaseControlService(store=InMemoryFeatureFlagStore())

    flags = await service.flags()
    assert flags == FeatureFlagSet()

    event = await service.set_flag(
        name="notifications",
        enabled=True,
        expected_version=1,
        actor="release-engineer",
        reason="dry-run enable notification fake stack",
    )

    assert event.previous_enabled is False
    assert event.new_enabled is True
    assert (await service.flags()).notifications is True
    assert (await service.audit_log())[0].actor == "release-engineer"


@pytest.mark.asyncio
async def test_beta_cohort_policy_does_not_add_real_users_by_default() -> None:
    service = ReleaseControlService(
        store=InMemoryFeatureFlagStore(FeatureFlagSet(bot_access=False)),
        cohort_policy=BetaCohortPolicy(allowlist_user_ids=frozenset({1001})),
    )

    allowed = await service.beta_access(user_id=1001)
    denied = await service.beta_access(user_id=9999)

    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason == "not_allowlisted"


@pytest.mark.asyncio
async def test_bot_access_requires_flag_and_allowlist() -> None:
    service = ReleaseControlService(
        store=InMemoryFeatureFlagStore(FeatureFlagSet(bot_access=False)),
        cohort_policy=BetaCohortPolicy(allowlist_user_ids=frozenset({42})),
    )
    controller = BotController(
        user_service=UserService(InMemoryUserRepository()),
        release_controls=service,
    )

    denied = await controller.start(user_id=777)
    allowed = await controller.start(user_id=42)

    assert "Beta hozir yopiq rejimda" in denied.text
    assert "EstateFlow" in allowed.text


@pytest.mark.asyncio
async def test_bot_access_flag_on_allows_everyone_and_flag_off_uses_allowlist() -> None:
    service = ReleaseControlService(
        store=InMemoryFeatureFlagStore(FeatureFlagSet(bot_access=True)),
        cohort_policy=BetaCohortPolicy(allowlist_user_ids=frozenset({42})),
    )

    assert (await service.beta_access(user_id=777)).allowed is True

    await service.set_flag(
        name="bot_access",
        enabled=False,
        expected_version=1,
        actor="release-engineer",
        reason="lock to allowlist only",
    )

    denied = await service.beta_access(user_id=777)
    allowed = await service.beta_access(user_id=42)

    assert denied.allowed is False
    assert denied.reason == "not_allowlisted"
    assert allowed.allowed is True
    assert allowed.reason == "allowlisted"


@pytest.mark.asyncio
async def test_listener_source_gate_returns_empty_when_disabled() -> None:
    registry = InMemorySourceRegistry(
        [
            SourceConfig(
                source_id="telegram_channel:@fake",
                name="Fake",
                source_type="telegram_channel",
                identifier="@fake",
                enabled=True,
            )
        ]
    )
    service = ReleaseControlService(store=InMemoryFeatureFlagStore())
    provider = FeatureGatedSourceProvider(registry, release_controls=service)

    assert await provider.list_active_sources() == []

    await service.set_flag(
        name="listener_sources",
        enabled=True,
        expected_version=1,
        actor="release-engineer",
        reason="dry-run fake source enable",
    )
    assert len(await provider.list_active_sources()) == 1


@pytest.mark.asyncio
async def test_notification_and_content_gates_fail_safe() -> None:
    class FakeTelegram:
        async def send_message(
            self,
            *,
            chat_id: int,
            text: str,
            reply_markup: dict[str, object] | None = None,
        ) -> TelegramSendResult:
            return TelegramSendResult(message_id="fake-message")

    class FakePublisher:
        async def publish(self, *, text: str, idempotency_key: str) -> PublishResult:
            return PublishResult(status="published", message_id="fake-published")

    service = ReleaseControlService(store=InMemoryFeatureFlagStore())
    telegram = FeatureGatedTelegramNotificationClient(
        FakeTelegram(),
        release_controls=service,
    )
    publisher = FeatureGatedContentPublisher(FakePublisher(), release_controls=service)

    with pytest.raises(NotificationsDisabledError):
        await telegram.send_message(chat_id=1, text="test")
    disabled_publish = await publisher.publish(text="test", idempotency_key="content-1")
    assert disabled_publish.status == "dry_run"

    await service.set_flag(
        name="notifications",
        enabled=True,
        expected_version=1,
        actor="release-engineer",
        reason="dry-run fake telegram enable",
    )
    await service.set_flag(
        name="content_publishing",
        enabled=True,
        expected_version=1,
        actor="release-engineer",
        reason="dry-run fake publisher enable",
    )

    assert (await telegram.send_message(chat_id=1, text="test")).message_id == "fake-message"
    assert (await publisher.publish(text="test", idempotency_key="content-1")).status == "published"


def test_release_flags_endpoint_is_admin_protected_and_audited() -> None:
    app = create_app(
        Settings(
            environment="test",
            admin_api_token=SecretStr("secret"),
            admin_actor_user_id=7,
        )
    )
    app.state.release_controls = ReleaseControlService(store=InMemoryFeatureFlagStore())
    client = TestClient(app)

    denied = client.get("/admin/release/flags")
    current = client.get("/admin/release/flags", headers={"x-admin-token": "secret"})
    changed = client.post(
        "/admin/release/flags",
        headers={"x-admin-token": "secret"},
        json={
            "flag_name": "bot_access",
            "enabled": True,
            "expected_version": current.json()["versions"]["bot_access"],
            "reason": "dry-run beta access validation",
        },
    )

    assert denied.status_code == 403
    assert changed.status_code == 200
    payload = changed.json()
    assert payload["flags"]["bot_access"] is True
    assert payload["versions"]["bot_access"] == current.json()["versions"]["bot_access"] + 1
    assert payload["beta_allowlist_size"] == 0
    assert payload["audit_events"][0]["actor"] == "server-admin:7"
    assert payload["audit_events"][0]["occurred_at"] >= datetime(2026, 1, 1, tzinfo=UTC).isoformat()


def test_release_allowlist_endpoint_adds_user_id_and_updates_size() -> None:
    app = create_app(
        Settings(
            environment="test",
            admin_api_token=SecretStr("secret"),
            admin_actor_user_id=7,
        )
    )
    app.state.release_controls = ReleaseControlService(store=InMemoryFeatureFlagStore())
    client = TestClient(app)

    response = client.post(
        "/admin/release/beta-allowlist",
        headers={"x-admin-token": "secret"},
        json={"user_id": 1001},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == 1001
    assert payload["allowlist_size"] == 1

    status = client.get("/admin/release/flags", headers={"x-admin-token": "secret"})
    assert status.json()["beta_allowlist_size"] == 1
