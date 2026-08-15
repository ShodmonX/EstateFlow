from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from redis.asyncio import Redis

from estateflow.application.core.config import Settings
from estateflow.services.content_automation import ContentPublisher, PublishResult
from estateflow.services.notifications import TelegramNotificationClient, TelegramSendResult
from estateflow.services.source_config import SourceConfig, SourceConfigProvider

FeatureFlagName = Literal[
    "bot_access",
    "listener_sources",
    "ai_processing",
    "notifications",
    "nlp",
    "content_publishing",
    "ai_vision",
]

FEATURE_FLAG_NAMES: tuple[FeatureFlagName, ...] = (
    "bot_access",
    "listener_sources",
    "ai_processing",
    "notifications",
    "nlp",
    "content_publishing",
    "ai_vision",
)


@dataclass(frozen=True)
class FeatureFlagSet:
    bot_access: bool = False
    listener_sources: bool = False
    ai_processing: bool = False
    notifications: bool = False
    nlp: bool = False
    content_publishing: bool = False
    ai_vision: bool = True

    def enabled(self, name: FeatureFlagName) -> bool:
        return bool(getattr(self, name))

    def with_update(self, name: FeatureFlagName, enabled: bool) -> FeatureFlagSet:
        return replace(self, **{name: enabled})


@dataclass(frozen=True)
class FeatureFlagState:
    flag_name: FeatureFlagName
    enabled: bool
    version: int
    updated_at: datetime


@dataclass(frozen=True)
class FeatureFlagSnapshot:
    states: dict[FeatureFlagName, FeatureFlagState]

    def enabled(self, name: FeatureFlagName) -> bool:
        return self.states.get(name, _default_flag_state(name)).enabled

    def version(self, name: FeatureFlagName) -> int:
        return self.states.get(name, _default_flag_state(name)).version

    def updated_at(self, name: FeatureFlagName) -> datetime:
        return self.states.get(name, _default_flag_state(name)).updated_at

    def as_set(self) -> FeatureFlagSet:
        return FeatureFlagSet(
            bot_access=self.enabled("bot_access"),
            listener_sources=self.enabled("listener_sources"),
            ai_processing=self.enabled("ai_processing"),
            notifications=self.enabled("notifications"),
            nlp=self.enabled("nlp"),
            content_publishing=self.enabled("content_publishing"),
            ai_vision=self.enabled("ai_vision"),
        )


@dataclass(frozen=True)
class FeatureFlagAuditEvent:
    flag_name: FeatureFlagName
    previous_enabled: bool
    new_enabled: bool
    previous_version: int
    new_version: int
    actor: str
    reason: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


_LOCAL_FEATURE_FLAG_STATE: dict[str, FeatureFlagSet] = {}
_LOCAL_FEATURE_FLAG_AUDIT: dict[str, list[FeatureFlagAuditEvent]] = {}


@dataclass(frozen=True)
class BetaAccessDecision:
    allowed: bool
    reason: Literal["bot_enabled", "allowlist_empty", "allowlisted", "not_allowlisted"]


class FeatureFlagStore(Protocol):
    async def get_snapshot(self) -> FeatureFlagSnapshot: ...

    async def set_flag(
        self,
        *,
        name: FeatureFlagName,
        enabled: bool,
        expected_version: int | None,
        actor: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> FeatureFlagAuditEvent: ...

    async def audit_log(self) -> tuple[FeatureFlagAuditEvent, ...]: ...


class InMemoryFeatureFlagStore:
    def __init__(self, initial: FeatureFlagSet | None = None) -> None:
        now = datetime.now(UTC)
        flags = initial or FeatureFlagSet()
        self._states: dict[FeatureFlagName, FeatureFlagState] = {
            name: FeatureFlagState(
                flag_name=name,
                enabled=getattr(flags, name),
                version=1,
                updated_at=now,
            )
            for name in FEATURE_FLAG_NAMES
        }
        self._audit: list[FeatureFlagAuditEvent] = []

    async def get_snapshot(self) -> FeatureFlagSnapshot:
        return FeatureFlagSnapshot(states=dict(self._states))

    async def get_flags(self) -> FeatureFlagSet:
        return (await self.get_snapshot()).as_set()

    async def set_flag(
        self,
        *,
        name: FeatureFlagName,
        enabled: bool,
        expected_version: int | None,
        actor: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> FeatureFlagAuditEvent:
        current = self._states[name]
        if expected_version is not None and current.version != expected_version:
            raise ValueError("feature_flag_version_conflict")
        new_version = current.version + 1
        updated_at = occurred_at or datetime.now(UTC)
        self._states[name] = FeatureFlagState(
            flag_name=name,
            enabled=enabled,
            version=new_version,
            updated_at=updated_at,
        )
        event = FeatureFlagAuditEvent(
            flag_name=name,
            previous_enabled=current.enabled,
            new_enabled=enabled,
            previous_version=current.version,
            new_version=new_version,
            actor=actor,
            reason=reason,
            occurred_at=updated_at,
        )
        self._audit.append(event)
        return event

    async def audit_log(self) -> tuple[FeatureFlagAuditEvent, ...]:
        return tuple(self._audit)


class RedisFeatureFlagStore:
    def __init__(
        self,
        redis: Redis,
        *,
        flags_key: str = "estateflow:feature-flags",
        audit_key: str = "estateflow:feature-flags:audit",
        initial: FeatureFlagSet | None = None,
    ) -> None:
        self._redis = redis
        self._flags_key = flags_key
        self._audit_key = audit_key
        self._initial = initial or FeatureFlagSet()

    async def get_flags(self) -> FeatureFlagSet:
        try:
            redis = cast(Any, self._redis)
            payload = await redis.hgetall(self._flags_key)
            if not payload:
                await self._seed_initial()
                payload = await redis.hgetall(self._flags_key)
        except Exception:
            return _LOCAL_FEATURE_FLAG_STATE.get(self._flags_key, self._initial)
        return FeatureFlagSet(
            bot_access=_as_bool(payload.get("bot_access")),
            listener_sources=_as_bool(payload.get("listener_sources")),
            ai_processing=_as_bool(payload.get("ai_processing")),
            notifications=_as_bool(payload.get("notifications")),
            nlp=_as_bool(payload.get("nlp")),
            content_publishing=_as_bool(payload.get("content_publishing")),
            ai_vision=_as_bool(payload.get("ai_vision", "true")),
        )

    async def set_flag(
        self,
        *,
        name: FeatureFlagName,
        enabled: bool,
        actor: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> FeatureFlagAuditEvent:
        current = await self.get_flags()
        previous = current.enabled(name)
        updated = current.with_update(name, enabled)
        try:
            redis = cast(Any, self._redis)
            await redis.hset(
                self._flags_key,
                mapping={flag: str(getattr(updated, flag)).lower() for flag in FEATURE_FLAG_NAMES},
            )
        except Exception:
            _LOCAL_FEATURE_FLAG_STATE[self._flags_key] = updated
        event = FeatureFlagAuditEvent(
            flag_name=name,
            previous_enabled=previous,
            new_enabled=enabled,
            previous_version=0,
            new_version=0,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at or datetime.now(UTC),
        )
        try:
            redis = cast(Any, self._redis)
            await redis.rpush(self._audit_key, _serialize_audit_event(event))
        except Exception:
            _LOCAL_FEATURE_FLAG_AUDIT.setdefault(self._audit_key, []).append(event)
        return event

    async def audit_log(self) -> tuple[FeatureFlagAuditEvent, ...]:
        try:
            redis = cast(Any, self._redis)
            rows = await redis.lrange(self._audit_key, 0, -1)
        except Exception:
            return tuple(_LOCAL_FEATURE_FLAG_AUDIT.get(self._audit_key, ()))
        events: list[FeatureFlagAuditEvent] = []
        for row in rows:
            try:
                events.append(_deserialize_audit_event(row))
            except Exception:
                continue
        return tuple(events)

    async def _seed_initial(self) -> None:
        try:
            redis = cast(Any, self._redis)
            await redis.hset(
                self._flags_key,
                mapping={
                    flag: str(getattr(self._initial, flag)).lower() for flag in FEATURE_FLAG_NAMES
                },
            )
        except Exception:
            _LOCAL_FEATURE_FLAG_STATE[self._flags_key] = self._initial


@dataclass(frozen=True)
class BetaCohortPolicy:
    allowlist_user_ids: frozenset[int] = frozenset()

    def decide(self, *, user_id: int, flags: FeatureFlagSet) -> BetaAccessDecision:
        if not flags.bot_access:
            if user_id in self.allowlist_user_ids:
                return BetaAccessDecision(allowed=True, reason="allowlisted")
            if not self.allowlist_user_ids:
                return BetaAccessDecision(allowed=False, reason="allowlist_empty")
            return BetaAccessDecision(allowed=False, reason="not_allowlisted")
        return BetaAccessDecision(allowed=True, reason="bot_enabled")

    def with_allowlist_user_id(self, user_id: int) -> BetaCohortPolicy:
        return BetaCohortPolicy(allowlist_user_ids=self.allowlist_user_ids | {user_id})


class ReleaseControlService:
    def __init__(
        self,
        *,
        store: FeatureFlagStore,
        cohort_policy: BetaCohortPolicy | None = None,
    ) -> None:
        self._store = store
        self._cohort_policy = cohort_policy or BetaCohortPolicy()

    async def flags(self) -> FeatureFlagSet:
        return (await self._store.get_snapshot()).as_set()

    async def snapshot(self) -> FeatureFlagSnapshot:
        return await self._store.get_snapshot()

    async def flag_versions(self) -> dict[FeatureFlagName, int]:
        snapshot = await self._store.get_snapshot()
        return {name: snapshot.version(name) for name in FEATURE_FLAG_NAMES}

    async def set_flag(
        self,
        *,
        name: FeatureFlagName,
        enabled: bool,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> FeatureFlagAuditEvent:
        if not actor.strip():
            raise ValueError("actor is required for feature flag changes.")
        if len(reason.strip()) < 5:
            raise ValueError("reason is required for feature flag changes.")
        return await self._store.set_flag(
            name=name,
            enabled=enabled,
            expected_version=expected_version,
            actor=actor.strip(),
            reason=reason.strip(),
        )

    async def beta_access(self, *, user_id: int) -> BetaAccessDecision:
        return self._cohort_policy.decide(user_id=user_id, flags=await self.flags())

    async def add_beta_allowlist_user_id(self, *, user_id: int) -> int:
        if user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        self._cohort_policy = self._cohort_policy.with_allowlist_user_id(user_id)
        return len(self._cohort_policy.allowlist_user_ids)

    async def beta_allowlist_size(self) -> int:
        return len(self._cohort_policy.allowlist_user_ids)

    async def audit_log(self) -> tuple[FeatureFlagAuditEvent, ...]:
        return await self._store.audit_log()


def feature_flags_from_settings(settings: Settings) -> FeatureFlagSet:
    return FeatureFlagSet(
        bot_access=settings.feature_bot_access_enabled,
        listener_sources=settings.feature_listener_sources_enabled,
        ai_processing=settings.feature_ai_processing_enabled,
        notifications=settings.feature_notifications_enabled,
        nlp=settings.feature_nlp_enabled,
        content_publishing=settings.feature_content_publishing_enabled,
        ai_vision=settings.feature_ai_vision_enabled,
    )


def beta_policy_from_settings(settings: Settings) -> BetaCohortPolicy:
    return BetaCohortPolicy(allowlist_user_ids=settings.beta_allowlist_ids)


class FeatureGatedSourceProvider:
    def __init__(
        self,
        provider: SourceConfigProvider,
        *,
        release_controls: ReleaseControlService,
    ) -> None:
        self._provider = provider
        self._release_controls = release_controls

    async def list_active_sources(self) -> list[SourceConfig]:
        flags = await self._release_controls.flags()
        if not flags.listener_sources:
            return []
        return await self._provider.list_active_sources()


class FeatureGatedContentPublisher:
    def __init__(
        self,
        publisher: ContentPublisher,
        *,
        release_controls: ReleaseControlService,
    ) -> None:
        self._publisher = publisher
        self._release_controls = release_controls

    async def publish(self, *, text: str, idempotency_key: str) -> PublishResult:
        flags = await self._release_controls.flags()
        if not flags.content_publishing:
            return PublishResult(status="dry_run", message_id=f"feature-disabled:{idempotency_key}")
        return await self._publisher.publish(text=text, idempotency_key=idempotency_key)


class NotificationsDisabledError(RuntimeError):
    pass


class FeatureGatedTelegramNotificationClient:
    def __init__(
        self,
        client: TelegramNotificationClient,
        *,
        release_controls: ReleaseControlService,
    ) -> None:
        self._client = client
        self._release_controls = release_controls

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramSendResult:
        flags = await self._release_controls.flags()
        if not flags.notifications:
            raise NotificationsDisabledError("notifications feature flag is disabled")
        return await self._client.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )


def _as_bool(value: str | None) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def _serialize_audit_event(event: FeatureFlagAuditEvent) -> str:
    return (
        f"{event.flag_name}|{int(event.previous_enabled)}|{int(event.new_enabled)}|"
        f"{event.previous_version}|{event.new_version}|{event.actor}|"
        f"{event.reason}|{event.occurred_at.isoformat()}"
    )


def _deserialize_audit_event(value: str) -> FeatureFlagAuditEvent:
    parts = value.split("|")
    if len(parts) == 6:
        flag_name, previous, new, actor, reason, occurred_at = parts
        previous_version = "0"
        new_version = "0"
    else:
        flag_name, previous, new, previous_version, new_version, actor, reason, occurred_at = parts
    return FeatureFlagAuditEvent(
        flag_name=cast(FeatureFlagName, flag_name),
        previous_enabled=bool(int(previous)),
        new_enabled=bool(int(new)),
        previous_version=int(previous_version),
        new_version=int(new_version),
        actor=actor,
        reason=reason,
        occurred_at=datetime.fromisoformat(occurred_at),
    )


def _default_flag_state(name: FeatureFlagName) -> FeatureFlagState:
    return FeatureFlagState(
        flag_name=name,
        enabled=False,
        version=0,
        updated_at=datetime.fromtimestamp(0, tz=UTC),
    )
