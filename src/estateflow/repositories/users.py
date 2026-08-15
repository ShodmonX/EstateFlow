from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]

from estateflow.services.referrals import (
    PremiumAward,
    ReferralActivationResult,
    ReferralEvent,
)
from estateflow.services.users import TelegramUser


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.users: dict[int, TelegramUser] = {}
        self.referral_events: dict[int, ReferralEvent] = {}
        self.premium_awards: set[tuple[int, int]] = set()
        self.audit_events: list[dict[str, object]] = []
        self._lock = asyncio.Lock()

    async def upsert_telegram_user(self, *, user_id: int) -> TelegramUser:
        async with self._lock:
            existing = self.users.get(user_id)
            if existing is not None:
                updated = replace(existing, updated_at=datetime.now(UTC))
                self.users[user_id] = updated
                return updated
            created = TelegramUser(user_id=user_id, referral_code=_referral_code(user_id))
            self.users[user_id] = created
            return created

    async def upsert_user(self, *, user_id: int) -> TelegramUser:
        return await self.upsert_telegram_user(user_id=user_id)

    async def get_user(self, *, user_id: int) -> TelegramUser | None:
        return self.users.get(user_id)

    async def get_user_by_referral_code(self, *, referral_code: str) -> TelegramUser | None:
        return next(
            (user for user in self.users.values() if user.referral_code == referral_code),
            None,
        )

    async def get_pending_or_active_event(self, *, referred_user_id: int) -> ReferralEvent | None:
        event = self.referral_events.get(referred_user_id)
        if event is None or event.status == "rejected":
            return None
        return event

    async def create_pending_event(
        self,
        *,
        referrer_user_id: int,
        referred_user_id: int,
        activation_event_key: str,
    ) -> tuple[ReferralEvent, bool]:
        async with self._lock:
            existing = self.referral_events.get(referred_user_id)
            if existing is not None:
                return existing, False
            referred = self.users[referred_user_id]
            if referred.referred_by is not None and referred.referred_by != referrer_user_id:
                raise ValueError("User already has immutable referrer.")
            self.users[referred_user_id] = replace(
                referred,
                referred_by=referrer_user_id,
                updated_at=datetime.now(UTC),
            )
            event = ReferralEvent(
                referral_event_id=str(uuid4()),
                referrer_user_id=referrer_user_id,
                referred_user_id=referred_user_id,
                activation_event_key=activation_event_key,
            )
            self.referral_events[referred_user_id] = event
            return event, True

    async def activate_once(
        self,
        *,
        referred_user_id: int,
        qualifying_event_key: str,
        now: datetime,
        threshold: int,
        premium_extension: timedelta,
    ) -> ReferralActivationResult:
        async with self._lock:
            event = self.referral_events.get(referred_user_id)
            if event is None:
                return ReferralActivationResult(
                    activated=False,
                    skipped_reason="no_referral_event",
                )
            if event.status == "active":
                return ReferralActivationResult(
                    activated=False,
                    event=event,
                    skipped_reason="already_active",
                    active_referral_count=self.users[event.referrer_user_id].active_referral_count,
                )
            if event.status != "pending":
                return ReferralActivationResult(
                    activated=False,
                    event=event,
                    skipped_reason=f"event_{event.status}",
                )
            referrer = self.users[event.referrer_user_id]
            active_count = referrer.active_referral_count + 1
            updated_event = replace(
                event,
                status="active",
                activation_event_key=qualifying_event_key,
                activated_at=now,
            )
            self.referral_events[referred_user_id] = updated_event

            award: PremiumAward | None = None
            premium_until = referrer.premium_until
            milestone_key = (referrer.user_id, threshold)
            if active_count >= threshold and milestone_key not in self.premium_awards:
                base = premium_until if premium_until and premium_until > now else now
                premium_until = base + premium_extension
                self.premium_awards.add(milestone_key)
                award = PremiumAward(
                    referrer_user_id=referrer.user_id,
                    milestone=threshold,
                    premium_until=premium_until,
                    created=True,
                )
            self.users[referrer.user_id] = replace(
                referrer,
                active_referral_count=active_count,
                premium_until=premium_until,
                updated_at=now,
            )
            return ReferralActivationResult(
                activated=True,
                event=updated_event,
                active_referral_count=active_count,
                premium_award=award,
            )

    async def audit(self, *, user_id: int, action: str, reason: str) -> None:
        self.audit_events.append(
            {
                "user_id": user_id,
                "action": action,
                "reason": reason,
                "created_at": datetime.now(UTC),
            }
        )


class AsyncpgUserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_telegram_user(self, *, user_id: int) -> TelegramUser:
        record = await self._pool.fetchrow(
            """
            insert into users(user_id, referral_code)
            values ($1, $2)
            on conflict (user_id) do update set
                updated_at = now()
            returning
                user_id, referral_code, referred_by, premium_until,
                active_referral_count, status, created_at, updated_at
            """,
            user_id,
            _referral_code(user_id),
        )
        assert record is not None
        return _from_record(record)

    async def upsert_user(self, *, user_id: int) -> TelegramUser:
        return await self.upsert_telegram_user(user_id=user_id)

    async def get_user(self, *, user_id: int) -> TelegramUser | None:
        record = await self._pool.fetchrow(
            """
            select
                user_id, referral_code, referred_by, premium_until,
                active_referral_count, status, created_at, updated_at
            from users
            where user_id = $1
            """,
            user_id,
        )
        return None if record is None else _from_record(record)

    async def get_user_by_referral_code(self, *, referral_code: str) -> TelegramUser | None:
        record = await self._pool.fetchrow(
            """
            select
                user_id, referral_code, referred_by, premium_until,
                active_referral_count, status, created_at, updated_at
            from users
            where referral_code = $1
            """,
            referral_code,
        )
        return None if record is None else _from_record(record)

    async def get_pending_or_active_event(self, *, referred_user_id: int) -> ReferralEvent | None:
        record = await self._pool.fetchrow(
            """
            select *
            from referral_events
            where referred_user_id = $1 and status in ('pending', 'active')
            """,
            referred_user_id,
        )
        return None if record is None else _referral_event_from_record(record)

    async def create_pending_event(
        self,
        *,
        referrer_user_id: int,
        referred_user_id: int,
        activation_event_key: str,
    ) -> tuple[ReferralEvent, bool]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                referred = await connection.fetchrow(
                    """
                    select referred_by
                    from users
                    where user_id = $1
                    for update
                    """,
                    referred_user_id,
                )
                assert referred is not None
                if (
                    referred["referred_by"] is not None
                    and int(referred["referred_by"]) != referrer_user_id
                ):
                    raise ValueError("User already has immutable referrer.")
                await connection.execute(
                    """
                    update users
                    set referred_by = coalesce(referred_by, $2),
                        updated_at = now()
                    where user_id = $1
                    """,
                    referred_user_id,
                    referrer_user_id,
                )
                record = await connection.fetchrow(
                    """
                    insert into referral_events(
                        referrer_user_id, referred_user_id, activation_event_key, status
                    )
                    values ($1, $2, $3, 'pending')
                    on conflict (referred_user_id) do nothing
                    returning *
                    """,
                    referrer_user_id,
                    referred_user_id,
                    activation_event_key,
                )
                if record is not None:
                    return _referral_event_from_record(record), True
                existing = await connection.fetchrow(
                    """
                    select *
                    from referral_events
                    where referred_user_id = $1
                    """,
                    referred_user_id,
                )
                assert existing is not None
                return _referral_event_from_record(existing), False

    async def activate_once(
        self,
        *,
        referred_user_id: int,
        qualifying_event_key: str,
        now: datetime,
        threshold: int,
        premium_extension: timedelta,
    ) -> ReferralActivationResult:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                event_record = await connection.fetchrow(
                    """
                    select *
                    from referral_events
                    where referred_user_id = $1
                    for update
                    """,
                    referred_user_id,
                )
                if event_record is None:
                    return ReferralActivationResult(
                        activated=False,
                        skipped_reason="no_referral_event",
                    )
                event = _referral_event_from_record(event_record)
                if event.status == "active":
                    count = await connection.fetchval(
                        """
                        select active_referral_count
                        from users
                        where user_id = $1
                        """,
                        event.referrer_user_id,
                    )
                    return ReferralActivationResult(
                        activated=False,
                        event=event,
                        active_referral_count=int(count or 0),
                        skipped_reason="already_active",
                    )
                if event.status != "pending":
                    return ReferralActivationResult(
                        activated=False,
                        event=event,
                        skipped_reason=f"event_{event.status}",
                    )

                updated_event = await connection.fetchrow(
                    """
                    update referral_events
                    set status = 'active',
                        activation_event_key = $2,
                        activated_at = $3
                    where referral_event_id = $1
                    returning *
                    """,
                    event.referral_event_id,
                    qualifying_event_key,
                    now,
                )
                referrer_record = await connection.fetchrow(
                    """
                    update users
                    set active_referral_count = active_referral_count + 1,
                        updated_at = $2
                    where user_id = $1
                    returning
                        user_id, referral_code, referred_by, premium_until,
                        active_referral_count, status, created_at, updated_at
                    """,
                    event.referrer_user_id,
                    now,
                )
                assert updated_event is not None
                assert referrer_record is not None
                referrer = _from_record(referrer_record)
                award = await _maybe_award_premium(
                    connection,
                    referrer=referrer,
                    threshold=threshold,
                    premium_extension=premium_extension,
                    now=now,
                )
                return ReferralActivationResult(
                    activated=True,
                    event=_referral_event_from_record(updated_event),
                    active_referral_count=referrer.active_referral_count,
                    premium_award=award,
                )

    async def audit(self, *, user_id: int, action: str, reason: str) -> None:
        await self._pool.execute(
            """
            insert into referral_audit_events(user_id, action, reason)
            values ($1, $2, $3)
            """,
            user_id,
            action,
            reason,
        )


def _referral_code(user_id: int) -> str:
    return f"ef_{user_id}"


def _from_record(record: Any) -> TelegramUser:
    return TelegramUser(
        user_id=int(record["user_id"]),
        referral_code=str(record["referral_code"]),
        referred_by=int(record["referred_by"]) if record["referred_by"] is not None else None,
        premium_until=record["premium_until"],
        active_referral_count=int(record["active_referral_count"]),
        status=str(record["status"]),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


def _referral_event_from_record(record: Any) -> ReferralEvent:
    return ReferralEvent(
        referral_event_id=str(record["referral_event_id"]),
        referrer_user_id=int(record["referrer_user_id"]),
        referred_user_id=int(record["referred_user_id"]),
        activation_event_key=str(record["activation_event_key"]),
        status=record["status"],
        created_at=record["created_at"],
        activated_at=record["activated_at"],
    )


async def _maybe_award_premium(
    connection: asyncpg.Connection,
    *,
    referrer: TelegramUser,
    threshold: int,
    premium_extension: timedelta,
    now: datetime,
) -> PremiumAward | None:
    if referrer.active_referral_count < threshold:
        return None
    record = await connection.fetchrow(
        """
        insert into referral_premium_awards(referrer_user_id, milestone)
        values ($1, $2)
        on conflict (referrer_user_id, milestone) do nothing
        returning referrer_user_id
        """,
        referrer.user_id,
        threshold,
    )
    if record is None:
        return None
    base = (
        referrer.premium_until if referrer.premium_until and referrer.premium_until > now else now
    )
    premium_until = base + premium_extension
    await connection.execute(
        """
        update users
        set premium_until = $2,
            updated_at = $3
        where user_id = $1
        """,
        referrer.user_id,
        premium_until,
        now,
    )
    return PremiumAward(
        referrer_user_id=referrer.user_id,
        milestone=threshold,
        premium_until=premium_until,
        created=True,
    )
