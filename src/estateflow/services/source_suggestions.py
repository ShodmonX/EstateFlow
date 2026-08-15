from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from estateflow.services.adapter_defaults import DEFAULT_TELEGRAM_ADAPTER_NAME
from estateflow.services.source_config import SourceConfig, SourceRegistry

SuggestionStatus = Literal["pending", "approved", "rejected"]
SourceType = Literal["telegram_channel", "telegram_group"]
SuggestionAction = Literal["submit", "approve", "reject"]

PREMIUM_REWARD_DAYS = 7


@dataclass(frozen=True)
class SourceSuggestion:
    suggestion_id: str
    source_identifier: str
    user_id: int | None
    source_type: SourceType = "telegram_channel"
    status: SuggestionStatus = "pending"
    admin_note: str | None = None
    reward_granted: bool = False
    source_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


@dataclass(frozen=True)
class SourceSuggestionAuditEvent:
    action: SuggestionAction
    idempotency_key: str
    suggestion_id: str
    actor_user_id: int | None = None
    note: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SourceSuggestionDecision:
    suggestion: SourceSuggestion
    created: bool = False
    reward_until: datetime | None = None
    source: SourceConfig | None = None
    source_created: bool = False


class SourceSuggestionRepository(Protocol):
    async def submit(
        self,
        *,
        user_id: int,
        source_identifier: str,
        source_type: SourceType,
    ) -> tuple[SourceSuggestion, bool]: ...

    async def list_pending(self) -> tuple[SourceSuggestion, ...]: ...

    async def get(self, *, suggestion_id: str) -> SourceSuggestion | None: ...

    async def set_status(
        self,
        *,
        suggestion_id: str,
        status: SuggestionStatus,
        admin_note: str | None,
        reward_granted: bool,
        source_id: str | None,
        decided_at: datetime,
    ) -> SourceSuggestion: ...

    async def audit_once(
        self,
        event: SourceSuggestionAuditEvent,
    ) -> tuple[SourceSuggestionAuditEvent, bool]: ...

    async def grant_premium(
        self,
        *,
        user_id: int,
        extension: timedelta,
        now: datetime,
    ) -> datetime: ...


class SourceSuggestionAdminNotifier(Protocol):
    async def pending_source_suggestion(self, suggestion: SourceSuggestion) -> None: ...


class DisabledSourceSuggestionAdminNotifier:
    async def pending_source_suggestion(self, suggestion: SourceSuggestion) -> None:
        return None


class InMemorySourceSuggestionAdminNotifier:
    def __init__(self) -> None:
        self.notifications: list[SourceSuggestion] = []

    async def pending_source_suggestion(self, suggestion: SourceSuggestion) -> None:
        self.notifications.append(suggestion)


class InMemorySourceSuggestionRepository:
    def __init__(self) -> None:
        self.suggestions: dict[str, SourceSuggestion] = {}
        self.by_identifier: dict[str, str] = {}
        self.audit_events: dict[str, SourceSuggestionAuditEvent] = {}
        self.premium_until: dict[int, datetime] = {}
        self._lock = asyncio.Lock()

    async def submit(
        self,
        *,
        user_id: int,
        source_identifier: str,
        source_type: SourceType,
    ) -> tuple[SourceSuggestion, bool]:
        async with self._lock:
            existing_id = self.by_identifier.get(source_identifier)
            if existing_id is not None:
                existing = self.suggestions[existing_id]
                if existing.status == "rejected":
                    if existing.user_id is not None and existing.user_id != user_id:
                        return existing, False
                    reopened = replace(
                        existing,
                        user_id=user_id,
                        source_type=source_type,
                        status="pending",
                        admin_note=None,
                        updated_at=datetime.now(UTC),
                        decided_at=None,
                    )
                    self.suggestions[existing_id] = reopened
                    return reopened, True
                return existing, False
            suggestion_id = f"source:{source_identifier}"
            suggestion = SourceSuggestion(
                suggestion_id=suggestion_id,
                source_identifier=source_identifier,
                user_id=user_id,
                source_type=source_type,
            )
            self.suggestions[suggestion_id] = suggestion
            self.by_identifier[source_identifier] = suggestion_id
            return suggestion, True

    async def list_pending(self) -> tuple[SourceSuggestion, ...]:
        pending = [item for item in self.suggestions.values() if item.status == "pending"]
        pending.sort(key=lambda item: (item.created_at, item.source_identifier))
        return tuple(pending)

    async def get(self, *, suggestion_id: str) -> SourceSuggestion | None:
        return self.suggestions.get(suggestion_id)

    async def set_status(
        self,
        *,
        suggestion_id: str,
        status: SuggestionStatus,
        admin_note: str | None,
        reward_granted: bool,
        source_id: str | None,
        decided_at: datetime,
    ) -> SourceSuggestion:
        async with self._lock:
            suggestion = self.suggestions[suggestion_id]
            updated = replace(
                suggestion,
                status=status,
                admin_note=admin_note,
                reward_granted=reward_granted,
                source_id=source_id if source_id is not None else suggestion.source_id,
                updated_at=decided_at,
                decided_at=decided_at,
            )
            self.suggestions[suggestion_id] = updated
            return updated

    async def audit_once(
        self,
        event: SourceSuggestionAuditEvent,
    ) -> tuple[SourceSuggestionAuditEvent, bool]:
        async with self._lock:
            existing = self.audit_events.get(event.idempotency_key)
            if existing is not None:
                return existing, False
            self.audit_events[event.idempotency_key] = event
            return event, True

    async def grant_premium(
        self,
        *,
        user_id: int,
        extension: timedelta,
        now: datetime,
    ) -> datetime:
        base = self.premium_until.get(user_id)
        if base is None or base <= now:
            base = now
        premium_until = base + extension
        self.premium_until[user_id] = premium_until
        return premium_until


class SourceSuggestionService:
    def __init__(
        self,
        repository: SourceSuggestionRepository,
        *,
        source_registry: SourceRegistry | None = None,
        admin_notifier: SourceSuggestionAdminNotifier | None = None,
        premium_extension: timedelta = timedelta(days=PREMIUM_REWARD_DAYS),
    ) -> None:
        self._repository = repository
        self._source_registry = source_registry
        self._admin_notifier = admin_notifier or DisabledSourceSuggestionAdminNotifier()
        self._premium_extension = premium_extension

    async def submit(
        self,
        *,
        user_id: int,
        source_identifier: str,
        source_type: SourceType = "telegram_channel",
    ) -> SourceSuggestionDecision:
        if user_id <= 0:
            raise ValueError("user_id must be positive.")
        normalized = normalize_source_identifier(source_identifier)
        suggestion, created = await self._repository.submit(
            user_id=user_id,
            source_identifier=normalized,
            source_type=source_type,
        )
        await self._repository.audit_once(
            SourceSuggestionAuditEvent(
                action="submit",
                idempotency_key=f"submit:{user_id}:{normalized}",
                suggestion_id=suggestion.suggestion_id,
                actor_user_id=user_id,
            )
        )
        if created:
            await self._admin_notifier.pending_source_suggestion(suggestion)
        return SourceSuggestionDecision(suggestion=suggestion, created=created)

    async def list_pending(self) -> tuple[SourceSuggestion, ...]:
        return await self._repository.list_pending()

    async def get(self, *, suggestion_id: str) -> SourceSuggestion | None:
        return await self._repository.get(suggestion_id=suggestion_id)

    async def approve(
        self,
        *,
        suggestion_id: str,
        admin_user_id: int,
        idempotency_key: str,
        note: str | None = None,
        expected_status: SuggestionStatus | None = "pending",
        now: datetime | None = None,
    ) -> SourceSuggestionDecision:
        if admin_user_id <= 0:
            raise PermissionError("Admin reviewer id is required.")
        current = now or datetime.now(UTC)
        event, created = await self._repository.audit_once(
            SourceSuggestionAuditEvent(
                action="approve",
                idempotency_key=idempotency_key,
                suggestion_id=suggestion_id,
                actor_user_id=admin_user_id,
                note=note,
            )
        )
        suggestion = await self._repository.get(suggestion_id=suggestion_id)
        if suggestion is None:
            raise KeyError(suggestion_id)
        if not created:
            return SourceSuggestionDecision(suggestion=suggestion, created=False)
        if expected_status is not None and suggestion.status != expected_status:
            raise ValueError("source_suggestion_state_conflict")
        if suggestion.status == "approved":
            return SourceSuggestionDecision(suggestion=suggestion, created=False)
        source: SourceConfig | None = None
        source_created = False
        if self._source_registry is not None:
            source, source_created = await self._source_registry.create_or_enable_source(
                source_type=suggestion.source_type,
                identifier=suggestion.source_identifier,
                name=suggestion.source_identifier,
                adapter_name=DEFAULT_TELEGRAM_ADAPTER_NAME,
            )
        reward_until: datetime | None = None
        if suggestion.user_id is not None and not suggestion.reward_granted:
            reward_until = await self._repository.grant_premium(
                user_id=suggestion.user_id,
                extension=self._premium_extension,
                now=current,
            )
        updated = await self._repository.set_status(
            suggestion_id=event.suggestion_id,
            status="approved",
            admin_note=note,
            reward_granted=True,
            source_id=source.source_id if source is not None else suggestion.source_id,
            decided_at=current,
        )
        return SourceSuggestionDecision(
            suggestion=updated,
            created=True,
            reward_until=reward_until,
            source=source,
            source_created=source_created,
        )

    async def reject(
        self,
        *,
        suggestion_id: str,
        admin_user_id: int,
        idempotency_key: str,
        note: str | None = None,
        expected_status: SuggestionStatus | None = "pending",
        now: datetime | None = None,
    ) -> SourceSuggestionDecision:
        if admin_user_id <= 0:
            raise PermissionError("Admin reviewer id is required.")
        current = now or datetime.now(UTC)
        event, created = await self._repository.audit_once(
            SourceSuggestionAuditEvent(
                action="reject",
                idempotency_key=idempotency_key,
                suggestion_id=suggestion_id,
                actor_user_id=admin_user_id,
                note=note,
            )
        )
        suggestion = await self._repository.get(suggestion_id=suggestion_id)
        if suggestion is None:
            raise KeyError(suggestion_id)
        if not created:
            return SourceSuggestionDecision(suggestion=suggestion, created=False)
        if expected_status is not None and suggestion.status != expected_status:
            raise ValueError("source_suggestion_state_conflict")
        if suggestion.status == "rejected":
            return SourceSuggestionDecision(suggestion=suggestion, created=False)
        updated = await self._repository.set_status(
            suggestion_id=event.suggestion_id,
            status="rejected",
            admin_note=note,
            reward_granted=suggestion.reward_granted,
            source_id=suggestion.source_id,
            decided_at=current,
        )
        return SourceSuggestionDecision(suggestion=updated, created=True)


def normalize_source_identifier(value: str) -> str:
    identifier = value.strip()
    identifier = re.sub(r"^https?://t\.me/", "@", identifier, flags=re.IGNORECASE)
    identifier = re.sub(r"^t\.me/", "@", identifier, flags=re.IGNORECASE)
    if identifier.startswith("@"):
        username = identifier[1:]
        if not re.fullmatch(r"[A-Za-z0-9_]{5,64}", username):
            raise ValueError("Invalid Telegram source username.")
        return f"@{username}"
    if identifier.startswith("-100") and identifier[4:].isdigit():
        return identifier
    raise ValueError("Source must be a Telegram @username, t.me link, or channel id.")
