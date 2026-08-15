from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from estateflow.services.analytics import AnalyticsRecorder, safe_record_event
from estateflow.services.users import TelegramUser

REFERRAL_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
REFERRAL_THRESHOLD = 5
PREMIUM_EXTENSION_DAYS = 7

ReferralEventStatus = Literal["pending", "active", "rejected"]
ReferralActivationAction = Literal["search", "saved_filter"]


@dataclass(frozen=True)
class ReferralEvent:
    referral_event_id: str
    referrer_user_id: int
    referred_user_id: int
    activation_event_key: str
    status: ReferralEventStatus = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    activated_at: datetime | None = None


@dataclass(frozen=True)
class PremiumAward:
    referrer_user_id: int
    milestone: int
    premium_until: datetime
    created: bool


@dataclass(frozen=True)
class ReferralStartResult:
    user: TelegramUser
    event: ReferralEvent | None
    created: bool = False
    skipped_reason: str | None = None


@dataclass(frozen=True)
class ReferralActivationResult:
    activated: bool
    event: ReferralEvent | None = None
    active_referral_count: int | None = None
    premium_award: PremiumAward | None = None
    skipped_reason: str | None = None


class ReferralNotificationPublisher(Protocol):
    async def premium_awarded(
        self,
        *,
        user_id: int,
        premium_until: datetime,
        active_referral_count: int,
    ) -> None: ...


class DisabledReferralNotificationPublisher:
    async def premium_awarded(
        self,
        *,
        user_id: int,
        premium_until: datetime,
        active_referral_count: int,
    ) -> None:
        return None


class InMemoryReferralNotificationPublisher:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def premium_awarded(
        self,
        *,
        user_id: int,
        premium_until: datetime,
        active_referral_count: int,
    ) -> None:
        self.messages.append(
            {
                "user_id": user_id,
                "premium_until": premium_until,
                "active_referral_count": active_referral_count,
                "priority": "high",
            }
        )


class ReferralRateLimiter(Protocol):
    async def allow(self, *, user_id: int, now: datetime) -> bool: ...


class InMemoryReferralRateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int = 20,
        window: timedelta = timedelta(minutes=1),
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window
        self._attempts: dict[int, list[datetime]] = {}

    async def allow(self, *, user_id: int, now: datetime) -> bool:
        cutoff = now - self._window
        attempts = [item for item in self._attempts.get(user_id, []) if item >= cutoff]
        if len(attempts) >= self._max_attempts:
            self._attempts[user_id] = attempts
            return False
        attempts.append(now)
        self._attempts[user_id] = attempts
        return True


class ReferralRepository(Protocol):
    async def upsert_user(self, *, user_id: int) -> TelegramUser: ...

    async def get_user(self, *, user_id: int) -> TelegramUser | None: ...

    async def get_user_by_referral_code(self, *, referral_code: str) -> TelegramUser | None: ...

    async def get_pending_or_active_event(
        self,
        *,
        referred_user_id: int,
    ) -> ReferralEvent | None: ...

    async def create_pending_event(
        self,
        *,
        referrer_user_id: int,
        referred_user_id: int,
        activation_event_key: str,
    ) -> tuple[ReferralEvent, bool]: ...

    async def activate_once(
        self,
        *,
        referred_user_id: int,
        qualifying_event_key: str,
        now: datetime,
        threshold: int,
        premium_extension: timedelta,
    ) -> ReferralActivationResult: ...

    async def audit(
        self,
        *,
        user_id: int,
        action: str,
        reason: str,
    ) -> None: ...


class ReferralService:
    def __init__(
        self,
        *,
        repository: ReferralRepository,
        notification_publisher: ReferralNotificationPublisher | None = None,
        rate_limiter: ReferralRateLimiter | None = None,
        threshold: int = REFERRAL_THRESHOLD,
        premium_extension: timedelta = timedelta(days=PREMIUM_EXTENSION_DAYS),
        analytics_recorder: AnalyticsRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._notification_publisher = (
            notification_publisher or DisabledReferralNotificationPublisher()
        )
        self._rate_limiter = rate_limiter or InMemoryReferralRateLimiter()
        self._threshold = threshold
        self._premium_extension = premium_extension
        self._analytics_recorder = analytics_recorder

    async def register_start(
        self,
        *,
        user_id: int,
        start_parameter: str | None = None,
    ) -> ReferralStartResult:
        if user_id <= 0:
            raise ValueError("Telegram user id must be positive.")
        now = datetime.now(UTC)
        if not await self._rate_limiter.allow(user_id=user_id, now=now):
            await self._repository.audit(
                user_id=user_id,
                action="referral_start_rejected",
                reason="rate_limited",
            )
            user = await self._repository.upsert_user(user_id=user_id)
            return ReferralStartResult(user=user, event=None, skipped_reason="rate_limited")
        user = await self._repository.upsert_user(user_id=user_id)
        if not start_parameter:
            return ReferralStartResult(user=user, event=None, skipped_reason="no_referral_code")
        referral_code = parse_referral_start_parameter(start_parameter)
        referrer = await self._repository.get_user_by_referral_code(referral_code=referral_code)
        if referrer is None:
            await self._repository.audit(
                user_id=user_id,
                action="referral_start_rejected",
                reason="malformed_or_unknown_code",
            )
            return ReferralStartResult(user=user, event=None, skipped_reason="unknown_code")
        if referrer.user_id == user_id:
            await self._repository.audit(
                user_id=user_id,
                action="referral_start_rejected",
                reason="self_referral",
            )
            return ReferralStartResult(user=user, event=None, skipped_reason="self_referral")
        if await self._would_create_cycle(referrer_user_id=referrer.user_id, user_id=user_id):
            await self._repository.audit(
                user_id=user_id,
                action="referral_start_rejected",
                reason="circular_referral",
            )
            return ReferralStartResult(user=user, event=None, skipped_reason="circular_referral")

        existing = await self._repository.get_pending_or_active_event(referred_user_id=user_id)
        if existing is not None:
            reason = (
                "duplicate_referral"
                if existing.referrer_user_id == referrer.user_id
                else "referrer_immutable"
            )
            return ReferralStartResult(
                user=user,
                event=existing,
                created=False,
                skipped_reason=reason,
            )

        event, created = await self._repository.create_pending_event(
            referrer_user_id=referrer.user_id,
            referred_user_id=user_id,
            activation_event_key=f"telegram_referral:{user_id}",
        )
        await safe_record_event(
            self._analytics_recorder,
            event_name="referral_accepted",
            idempotency_key=f"referral_accepted:{event.referral_event_id}",
            occurred_at=event.created_at,
            user_id=user_id,
            subject_id=str(referrer.user_id),
        )
        return ReferralStartResult(user=user, event=event, created=created)

    async def record_qualifying_action(
        self,
        *,
        user_id: int,
        action: ReferralActivationAction,
        action_id: str,
        now: datetime | None = None,
    ) -> ReferralActivationResult:
        if user_id <= 0:
            raise ValueError("Telegram user id must be positive.")
        if not action_id.strip():
            raise ValueError("Referral activation action id is required.")
        result = await self._repository.activate_once(
            referred_user_id=user_id,
            qualifying_event_key=f"{action}:{action_id}",
            now=now or datetime.now(UTC),
            threshold=self._threshold,
            premium_extension=self._premium_extension,
        )
        if result.premium_award is not None and result.premium_award.created:
            assert result.active_referral_count is not None
            await self._notification_publisher.premium_awarded(
                user_id=result.premium_award.referrer_user_id,
                premium_until=result.premium_award.premium_until,
                active_referral_count=result.active_referral_count,
            )
        if result.activated and result.event is not None:
            await safe_record_event(
                self._analytics_recorder,
                event_name="referral_activated",
                idempotency_key=f"referral_activated:{result.event.referral_event_id}",
                occurred_at=result.event.activated_at,
                user_id=user_id,
                subject_id=str(result.event.referrer_user_id),
            )
        return result

    async def deep_link(self, *, bot_username: str, user_id: int) -> str:
        user = await self._repository.get_user(user_id=user_id)
        if user is None:
            user = await self._repository.upsert_user(user_id=user_id)
        code = validate_referral_code(user.referral_code)
        clean_bot = bot_username.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", clean_bot):
            raise ValueError("Invalid Telegram bot username.")
        return f"https://t.me/{clean_bot}?start={code}"

    async def _would_create_cycle(self, *, referrer_user_id: int, user_id: int) -> bool:
        current_id = referrer_user_id
        visited: set[int] = set()
        while current_id not in visited:
            visited.add(current_id)
            event = await self._repository.get_pending_or_active_event(referred_user_id=current_id)
            if event is None:
                return False
            if event.referrer_user_id == user_id:
                return True
            current_id = event.referrer_user_id
        return True


def parse_referral_start_parameter(value: str) -> str:
    return validate_referral_code(value.strip())


def validate_referral_code(value: str) -> str:
    if not REFERRAL_CODE_PATTERN.fullmatch(value):
        raise ValueError("Invalid Telegram start referral code.")
    return value
