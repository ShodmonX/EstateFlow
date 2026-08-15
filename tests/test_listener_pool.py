from __future__ import annotations

from dataclasses import asdict

import pytest

from estateflow.services.listener_pool import (
    ChannelAssignment,
    InMemoryChannelAssignmentRepository,
    InMemoryListenerAccountRepository,
    InvalidListenerHealthTransitionError,
    ListenerAccountMetadata,
    ListenerAssignmentService,
)
from estateflow.services.ops_notifications import DisabledOpsNotificationService
from estateflow.services.source_config import SourceConfig


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


def _source(source_id: str) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        name=f"Source {source_id}",
        source_type="telegram_channel",
        identifier=f"@{source_id}",
    )


@pytest.mark.asyncio
async def test_reconcile_balances_channels_across_two_healthy_accounts() -> None:
    account_repository = InMemoryListenerAccountRepository(
        [
            ListenerAccountMetadata(account_key="listener-a"),
            ListenerAccountMetadata(account_key="listener-b"),
        ]
    )
    assignment_repository = InMemoryChannelAssignmentRepository()
    service = ListenerAssignmentService(
        account_repository=account_repository,
        assignment_repository=assignment_repository,
        ops_notifier=DisabledOpsNotificationService(),
    )

    assignments = await service.reconcile([_source("channel-1"), _source("channel-2")])

    assert [(item.source_id, item.account_key) for item in assignments] == [
        ("channel-1", "listener-a"),
        ("channel-2", "listener-b"),
    ]


@pytest.mark.asyncio
async def test_reconcile_fails_over_unhealthy_account_assignments() -> None:
    account_repository = InMemoryListenerAccountRepository(
        [
            ListenerAccountMetadata(account_key="listener-a", health_status="flood_wait"),
            ListenerAccountMetadata(account_key="listener-b"),
        ]
    )
    assignment_repository = InMemoryChannelAssignmentRepository(
        [ChannelAssignment(source_id="channel-1", account_key="listener-a")]
    )
    ops_notifier = RecordingOpsNotifier()
    service = ListenerAssignmentService(
        account_repository=account_repository,
        assignment_repository=assignment_repository,
        ops_notifier=ops_notifier,
    )

    assignments = await service.reconcile([_source("channel-1")])

    assert [(item.source_id, item.account_key) for item in assignments] == [
        ("channel-1", "listener-b")
    ]
    assert [event.reason for event in assignment_repository.audit_events] == [
        "failover",
        "initial",
    ]
    assert len(ops_notifier.messages) == 1
    assert "flood_wait" in ops_notifier.messages[0]


@pytest.mark.asyncio
async def test_disabled_account_is_not_selected_for_new_assignments() -> None:
    account_repository = InMemoryListenerAccountRepository(
        [
            ListenerAccountMetadata(account_key="listener-a", enabled=False),
            ListenerAccountMetadata(account_key="listener-b"),
        ]
    )
    assignment_repository = InMemoryChannelAssignmentRepository()
    service = ListenerAssignmentService(
        account_repository=account_repository,
        assignment_repository=assignment_repository,
        ops_notifier=DisabledOpsNotificationService(),
    )

    assignments = await service.reconcile([_source("channel-1")])

    assert [(item.source_id, item.account_key) for item in assignments] == [
        ("channel-1", "listener-b")
    ]


@pytest.mark.asyncio
async def test_health_transition_records_flood_wait_without_credentials() -> None:
    account_repository = InMemoryListenerAccountRepository(
        [ListenerAccountMetadata(account_key="listener-a")]
    )
    service = ListenerAssignmentService(
        account_repository=account_repository,
        assignment_repository=InMemoryChannelAssignmentRepository(),
        ops_notifier=RecordingOpsNotifier(),
    )

    account = await service.transition_health(
        account_key="listener-a",
        health_status="flood_wait",
        flood_wait_increment=1,
    )

    serialized = asdict(account)
    safe_payload = account.to_safe_dict(assigned_channel_count=3)

    assert account.flood_wait_count == 1
    assert account.health_status == "flood_wait"
    assert "phone" not in serialized
    assert "session" not in serialized
    assert "api_hash" not in serialized
    assert "credential" not in serialized
    assert "assigned_channel_count" in safe_payload


@pytest.mark.asyncio
async def test_banned_account_cannot_transition_back_online() -> None:
    service = ListenerAssignmentService(
        account_repository=InMemoryListenerAccountRepository(
            [ListenerAccountMetadata(account_key="listener-a", health_status="banned")]
        ),
        assignment_repository=InMemoryChannelAssignmentRepository(),
        ops_notifier=DisabledOpsNotificationService(),
    )

    with pytest.raises(InvalidListenerHealthTransitionError):
        await service.transition_health(account_key="listener-a", health_status="online")


@pytest.mark.asyncio
async def test_long_silence_marks_reconnecting_and_emits_safe_ops_event() -> None:
    ops_notifier = RecordingOpsNotifier()
    service = ListenerAssignmentService(
        account_repository=InMemoryListenerAccountRepository(
            [ListenerAccountMetadata(account_key="listener-a")]
        ),
        assignment_repository=InMemoryChannelAssignmentRepository(),
        ops_notifier=ops_notifier,
    )

    account = await service.mark_long_silence(account_key="listener-a")

    assert account.health_status == "reconnecting"
    assert len(ops_notifier.messages) == 1
    assert "session" not in ops_notifier.messages[0].lower()
    assert "token" not in ops_notifier.messages[0].lower()


def test_listener_pool_migration_does_not_persist_credentials() -> None:
    migration = open("migrations/001_listener_pool.sql", encoding="utf-8").read().lower()

    assert "create table if not exists listener_accounts" in migration
    assert "create table if not exists ingestion_sources" in migration
    assert "create table if not exists channel_assignments" in migration
    assert "create table if not exists channel_assignment_audit" in migration
    assert "where active" in migration
    assert "phone" not in migration
    assert "session_path" not in migration
    assert "api_hash" not in migration
    assert "bot_token" not in migration
