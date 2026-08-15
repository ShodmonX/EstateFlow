from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from estateflow.services.analytics import AnalyticsRecorder, safe_record_event


@dataclass(frozen=True)
class TelegramUser:
    user_id: int
    referral_code: str
    referred_by: int | None = None
    premium_until: datetime | None = None
    active_referral_count: int = 0
    status: str = "active"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class UserRepository(Protocol):
    async def upsert_telegram_user(self, *, user_id: int) -> TelegramUser: ...


class UserService:
    def __init__(
        self,
        repository: UserRepository,
        *,
        analytics_recorder: AnalyticsRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._analytics_recorder = analytics_recorder

    async def register_or_touch(self, *, telegram_user_id: int) -> TelegramUser:
        if telegram_user_id <= 0:
            raise ValueError("Telegram user id must be positive.")
        user = await self._repository.upsert_telegram_user(user_id=telegram_user_id)
        await safe_record_event(
            self._analytics_recorder,
            event_name="user_registered",
            idempotency_key=f"user_registered:{user.user_id}",
            occurred_at=user.created_at,
            user_id=user.user_id,
        )
        return user
