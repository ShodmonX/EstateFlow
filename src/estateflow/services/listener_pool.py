from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal, Protocol

from estateflow.application.core.correlation import get_correlation_id
from estateflow.application.core.redaction import redact
from estateflow.services.analytics import TechnicalMetricRecorder, safe_record_technical_metric
from estateflow.services.ops_notifications import OpsNotificationService
from estateflow.services.source_config import SourceConfig

ListenerHealthStatus = Literal["online", "reconnecting", "flood_wait", "disabled", "banned"]
AssignmentReason = Literal["initial", "rebalance", "failover", "source_disabled"]

ASSIGNABLE_HEALTH_STATUSES: frozenset[ListenerHealthStatus] = frozenset({"online"})
TELEGRAM_SOURCE_TYPES = frozenset({"telegram_channel", "telegram_group"})
ALLOWED_HEALTH_TRANSITIONS: dict[ListenerHealthStatus, frozenset[ListenerHealthStatus]] = {
    "online": frozenset({"online", "reconnecting", "flood_wait", "disabled", "banned"}),
    "reconnecting": frozenset({"online", "reconnecting", "flood_wait", "disabled", "banned"}),
    "flood_wait": frozenset({"online", "reconnecting", "flood_wait", "disabled", "banned"}),
    "disabled": frozenset({"online", "disabled"}),
    "banned": frozenset({"banned"}),
}


@dataclass(frozen=True)
class ListenerAccountMetadata:
    account_key: str
    enabled: bool = True
    health_status: ListenerHealthStatus = "online"
    last_successful_event_at: datetime | None = None
    flood_wait_count: int = 0

    @property
    def is_assignable(self) -> bool:
        return self.enabled and self.health_status in ASSIGNABLE_HEALTH_STATUSES

    def to_safe_dict(self, *, assigned_channel_count: int = 0) -> dict[str, object]:
        payload = asdict(self)
        payload["assigned_channel_count"] = assigned_channel_count
        safe_payload = redact(payload)
        if not isinstance(safe_payload, dict):
            raise TypeError("Listener account metadata must serialize to a dictionary.")
        return safe_payload


@dataclass(frozen=True)
class ChannelAssignment:
    source_id: str
    account_key: str
    assigned_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    active: bool = True
    released_at: datetime | None = None
    reason: AssignmentReason = "initial"


@dataclass(frozen=True)
class AssignmentAuditEvent:
    source_id: str
    previous_account_key: str | None
    new_account_key: str | None
    reason: AssignmentReason
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ListenerAccountRepository(Protocol):
    async def list_accounts(self) -> list[ListenerAccountMetadata]: ...

    async def save_account(self, account: ListenerAccountMetadata) -> None: ...


class ChannelAssignmentRepository(Protocol):
    async def list_active_assignments(self) -> list[ChannelAssignment]: ...

    async def save_assignment(self, assignment: ChannelAssignment) -> None: ...

    async def release_assignment(
        self,
        *,
        source_id: str,
        reason: AssignmentReason,
        released_at: datetime,
    ) -> ChannelAssignment | None: ...

    async def append_audit_event(self, event: AssignmentAuditEvent) -> None: ...


class InMemoryListenerAccountRepository:
    def __init__(self, accounts: list[ListenerAccountMetadata]) -> None:
        self._accounts = {account.account_key: account for account in accounts}

    async def list_accounts(self) -> list[ListenerAccountMetadata]:
        return sorted(self._accounts.values(), key=lambda account: account.account_key)

    async def save_account(self, account: ListenerAccountMetadata) -> None:
        self._accounts[account.account_key] = account


class InMemoryChannelAssignmentRepository:
    def __init__(self, assignments: list[ChannelAssignment] | None = None) -> None:
        self._assignments = list(assignments or [])
        self.audit_events: list[AssignmentAuditEvent] = []

    async def list_active_assignments(self) -> list[ChannelAssignment]:
        return [assignment for assignment in self._assignments if assignment.active]

    async def save_assignment(self, assignment: ChannelAssignment) -> None:
        self._assignments.append(assignment)

    async def release_assignment(
        self,
        *,
        source_id: str,
        reason: AssignmentReason,
        released_at: datetime,
    ) -> ChannelAssignment | None:
        for index, assignment in enumerate(self._assignments):
            if assignment.source_id == source_id and assignment.active:
                released = replace(
                    assignment,
                    active=False,
                    released_at=released_at,
                    reason=reason,
                )
                self._assignments[index] = released
                return released
        return None

    async def append_audit_event(self, event: AssignmentAuditEvent) -> None:
        self.audit_events.append(event)


class NoHealthyListenerAccountError(RuntimeError):
    pass


class InvalidListenerHealthTransitionError(ValueError):
    pass


class ListenerAssignmentService:
    def __init__(
        self,
        *,
        account_repository: ListenerAccountRepository,
        assignment_repository: ChannelAssignmentRepository,
        ops_notifier: OpsNotificationService,
        technical_recorder: TechnicalMetricRecorder | None = None,
    ) -> None:
        self._account_repository = account_repository
        self._assignment_repository = assignment_repository
        self._ops_notifier = ops_notifier
        self._technical_recorder = technical_recorder

    async def reconcile(self, sources: list[SourceConfig]) -> list[ChannelAssignment]:
        accounts = await self._account_repository.list_accounts()
        account_by_key = {account.account_key: account for account in accounts}
        active_sources = {
            source.source_id: source
            for source in sources
            if source.enabled and source.source_type in TELEGRAM_SOURCE_TYPES
        }
        active_assignments = {
            assignment.source_id: assignment
            for assignment in await self._assignment_repository.list_active_assignments()
        }
        load_by_account = self._calculate_loads(active_assignments.values())

        for source_id, assignment in sorted(active_assignments.items()):
            assigned_account = account_by_key.get(assignment.account_key)
            if source_id not in active_sources:
                await self._release_assignment(
                    assignment,
                    reason="source_disabled",
                    load_by_account=load_by_account,
                )
            elif assigned_account is None or not assigned_account.is_assignable:
                await self._release_assignment(
                    assignment,
                    reason="failover",
                    load_by_account=load_by_account,
                )
                await self._notify_failover(assignment, assigned_account)

        assignments = {
            assignment.source_id: assignment
            for assignment in await self._assignment_repository.list_active_assignments()
        }
        load_by_account = self._calculate_loads(assignments.values())

        for source in sorted(active_sources.values(), key=lambda item: item.source_id):
            if source.source_id in assignments:
                continue
            account = self._select_account(accounts, load_by_account)
            assignment = ChannelAssignment(
                source_id=source.source_id,
                account_key=account.account_key,
                reason="initial",
            )
            await self._assignment_repository.save_assignment(assignment)
            await self._assignment_repository.append_audit_event(
                AssignmentAuditEvent(
                    source_id=source.source_id,
                    previous_account_key=None,
                    new_account_key=account.account_key,
                    reason="initial",
                )
            )
            load_by_account[account.account_key] = load_by_account.get(account.account_key, 0) + 1

        return await self._assignment_repository.list_active_assignments()

    async def transition_health(
        self,
        *,
        account_key: str,
        health_status: ListenerHealthStatus,
        flood_wait_increment: int = 0,
    ) -> ListenerAccountMetadata:
        accounts = await self._account_repository.list_accounts()
        account_by_key = {account.account_key: account for account in accounts}
        account = account_by_key[account_key]
        self._validate_health_transition(account.health_status, health_status)
        updated = replace(
            account,
            health_status=health_status,
            enabled=False if health_status in {"disabled", "banned"} else account.enabled,
            flood_wait_count=account.flood_wait_count + flood_wait_increment,
        )
        await self._account_repository.save_account(updated)
        occurred_at = datetime.now(UTC)
        await self._record_listener_health(updated, occurred_at=occurred_at)
        if health_status in {"flood_wait", "banned"}:
            await self._ops_notifier.notify(
                severity="critical",
                reason=f"Listener account {account_key} health changed to {health_status}",
                correlation_id=get_correlation_id(),
            )
        return updated

    async def record_successful_event(self, *, account_key: str) -> ListenerAccountMetadata:
        accounts = await self._account_repository.list_accounts()
        account_by_key = {account.account_key: account for account in accounts}
        account = account_by_key[account_key]
        self._validate_health_transition(account.health_status, "online")
        updated = replace(
            account,
            health_status="online",
            last_successful_event_at=datetime.now(UTC),
        )
        await self._account_repository.save_account(updated)
        await self._record_listener_health(
            updated,
            occurred_at=updated.last_successful_event_at or datetime.now(UTC),
        )
        return updated

    async def mark_long_silence(self, *, account_key: str) -> ListenerAccountMetadata:
        updated = await self.transition_health(
            account_key=account_key,
            health_status="reconnecting",
        )
        await self._ops_notifier.notify(
            severity="warning",
            reason=f"Listener account {account_key} has no recent successful events",
            correlation_id=get_correlation_id(),
        )
        return updated

    def _select_account(
        self,
        accounts: list[ListenerAccountMetadata],
        load_by_account: dict[str, int],
    ) -> ListenerAccountMetadata:
        candidates = [account for account in accounts if account.is_assignable]
        if not candidates:
            raise NoHealthyListenerAccountError("No enabled healthy listener account is available.")
        return min(
            candidates,
            key=lambda account: (
                load_by_account.get(account.account_key, 0),
                account.account_key,
            ),
        )

    async def _release_assignment(
        self,
        assignment: ChannelAssignment,
        *,
        reason: AssignmentReason,
        load_by_account: dict[str, int],
    ) -> None:
        released = await self._assignment_repository.release_assignment(
            source_id=assignment.source_id,
            reason=reason,
            released_at=datetime.now(UTC),
        )
        if released is None:
            return
        load_by_account[assignment.account_key] = max(
            0, load_by_account.get(assignment.account_key, 0) - 1
        )
        await self._assignment_repository.append_audit_event(
            AssignmentAuditEvent(
                source_id=assignment.source_id,
                previous_account_key=assignment.account_key,
                new_account_key=None,
                reason=reason,
            )
        )

    async def _notify_failover(
        self,
        assignment: ChannelAssignment,
        account: ListenerAccountMetadata | None,
    ) -> None:
        status = account.health_status if account is not None else "missing"
        await self._ops_notifier.notify(
            severity="warning",
            reason=(
                f"Listener account {assignment.account_key} is {status}; "
                f"source {assignment.source_id} will be reassigned"
            ),
            correlation_id=get_correlation_id(),
        )

    async def _record_listener_health(
        self,
        account: ListenerAccountMetadata,
        *,
        occurred_at: datetime,
    ) -> None:
        await safe_record_technical_metric(
            self._technical_recorder,
            metric_name="listener_health",
            idempotency_key=(
                f"listener_health:{account.account_key}:{account.health_status}:"
                f"{occurred_at.isoformat()}"
            ),
            occurred_at=occurred_at,
            component="listener_pool",
            subject_id=account.account_key,
            metadata={
                "status": account.health_status,
                "enabled": str(account.enabled).lower(),
                "flood_wait_count": str(account.flood_wait_count),
            },
        )

    def _calculate_loads(
        self,
        assignments: Iterable[ChannelAssignment],
    ) -> dict[str, int]:
        load_by_account: dict[str, int] = {}
        for assignment in assignments:
            load_by_account[assignment.account_key] = (
                load_by_account.get(assignment.account_key, 0) + 1
            )
        return load_by_account

    def _validate_health_transition(
        self,
        current_status: ListenerHealthStatus,
        next_status: ListenerHealthStatus,
    ) -> None:
        if next_status not in ALLOWED_HEALTH_TRANSITIONS[current_status]:
            raise InvalidListenerHealthTransitionError(
                f"Cannot transition listener account from {current_status} to {next_status}."
            )
