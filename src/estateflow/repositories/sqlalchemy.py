# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from estateflow.models import (
    AdminAuditEventRecord,
    AnalyticsEventRecord,
    AnnouncementRecord,
    AudienceTagTypeRecord,
    ContentChannelPostRecord,
    IngestionSourceRecord,
    ListenerAccountRecord,
    NotificationDeliveryAttemptRecord,
    NotificationRecord,
    ReferralAuditEventRecord,
    ReferralEventRecord,
    ReferralPremiumAwardRecord,
    SourceSuggestionRecord,
    TechnicalMetricEventRecord,
    UserFilterRecord,
    UserRecord,
)
from estateflow.services.analytics import AnalyticsEvent, TechnicalMetricEvent
from estateflow.services.audience_tags import (
    AudienceTagAuditEvent,
    AudienceTagReferenceRemapResult,
    AudienceTagStatus,
    AudienceTagType,
)
from estateflow.services.content_automation import (
    ContentDistrictStats,
    ContentPost,
    ContentRepository,
    ContentStats,
    ContentTopOffer,
)
from estateflow.services.notifications import (
    HIGH_PRIORITY_NOTIFICATION_QUEUE,
    NotificationDeliveryAttempt,
    NotificationDeliveryContext,
    NotificationJob,
    NotificationQueueName,
    NotificationUser,
)
from estateflow.services.referrals import (
    PremiumAward,
    ReferralActivationResult,
    ReferralEvent,
)
from estateflow.services.source_config import SourceConfig
from estateflow.services.saved_filters import UserFilter
from estateflow.services.search import SearchCriteria, SearchMetadata, SearchResult
from estateflow.services.post_ai_dedup import StructuredAnnouncement
from estateflow.services.source_suggestions import (
    SourceSuggestion,
    SourceSuggestionAuditEvent,
    SourceType,
    SuggestionStatus,
)
from estateflow.services.users import TelegramUser

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class SQLAlchemyUserRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def upsert_telegram_user(self, *, user_id: int) -> TelegramUser:
        async with self._session_factory() as session:
            existing = await session.scalar(select(UserRecord).where(UserRecord.user_id == user_id))
            if existing is None:
                referral_code = f"u{user_id:010d}"
                record = UserRecord(user_id=user_id, referral_code=referral_code)
                session.add(record)
                await session.commit()
                await session.refresh(record)
                return _user_from_record(record)
            # Build the domain object from values that are safe before commit. Reading an
            # expired ORM attribute after commit can trigger implicit async IO and raise
            # MissingGreenlet in the bot process.
            touched_at = datetime.now(UTC)
            existing_user = TelegramUser(
                user_id=existing.user_id,
                referral_code=existing.referral_code,
                referred_by=existing.referred_by,
                premium_until=existing.premium_until,
                active_referral_count=existing.active_referral_count,
                status=existing.status,
                created_at=existing.created_at,
                updated_at=touched_at,
            )
            await session.execute(
                update(UserRecord)
                .where(UserRecord.user_id == user_id)
                .values(updated_at=touched_at)
            )
            await session.commit()
            return existing_user

    async def upsert_user(self, *, user_id: int) -> TelegramUser:
        return await self.upsert_telegram_user(user_id=user_id)

    async def get_user(self, *, user_id: int) -> TelegramUser | None:
        async with self._session_factory() as session:
            record = await session.scalar(select(UserRecord).where(UserRecord.user_id == user_id))
            return None if record is None else _user_from_record(record)

    async def get_user_by_referral_code(self, *, referral_code: str) -> TelegramUser | None:
        async with self._session_factory() as session:
            record = await session.scalar(
                select(UserRecord).where(UserRecord.referral_code == referral_code)
            )
            return None if record is None else _user_from_record(record)

    async def get_pending_or_active_event(
        self,
        *,
        referred_user_id: int,
    ) -> ReferralEvent | None:
        async with self._session_factory() as session:
            record = await session.scalar(
                select(ReferralEventRecord)
                .where(ReferralEventRecord.referred_user_id == referred_user_id)
                .where(ReferralEventRecord.status.in_(("pending", "active")))
            )
            return None if record is None else _referral_event_from_record(record)

    async def create_pending_event(
        self,
        *,
        referrer_user_id: int,
        referred_user_id: int,
        activation_event_key: str,
    ) -> tuple[ReferralEvent, bool]:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(ReferralEventRecord).where(
                    ReferralEventRecord.referred_user_id == referred_user_id
                )
            )
            if existing is not None:
                return _referral_event_from_record(existing), False
            record = ReferralEventRecord(
                referrer_user_id=referrer_user_id,
                referred_user_id=referred_user_id,
                activation_event_key=activation_event_key,
            )
            session.add(record)
            user = await session.scalar(
                select(UserRecord).where(UserRecord.user_id == referred_user_id)
            )
            if user is None:
                raise ValueError("Referred user does not exist.")
            user.referred_by = referrer_user_id
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                existing = await session.scalar(
                    select(ReferralEventRecord).where(
                        ReferralEventRecord.referred_user_id == referred_user_id
                    )
                )
                if existing is None:
                    raise
                return _referral_event_from_record(existing), False
            await session.refresh(record)
            return _referral_event_from_record(record), True

    async def activate_once(
        self,
        *,
        referred_user_id: int,
        qualifying_event_key: str,
        now: datetime,
        threshold: int,
        premium_extension: timedelta,
    ) -> ReferralActivationResult:
        async with self._session_factory() as session:
            event_record = await session.scalar(
                select(ReferralEventRecord)
                .where(ReferralEventRecord.referred_user_id == referred_user_id)
                .with_for_update()
            )
            if event_record is None:
                return ReferralActivationResult(activated=False, skipped_reason="no_referral_event")
            if event_record.status == "active":
                referrer = await session.scalar(
                    select(UserRecord).where(UserRecord.user_id == event_record.referrer_user_id)
                )
                return ReferralActivationResult(
                    activated=False,
                    event=_referral_event_from_record(event_record),
                    skipped_reason="already_active",
                    active_referral_count=referrer.active_referral_count if referrer else None,
                )
            if event_record.status != "pending":
                return ReferralActivationResult(
                    activated=False,
                    event=_referral_event_from_record(event_record),
                    skipped_reason=f"event_{event_record.status}",
                )
            referrer = await session.scalar(
                select(UserRecord)
                .where(UserRecord.user_id == event_record.referrer_user_id)
                .with_for_update()
            )
            if referrer is None:
                return ReferralActivationResult(
                    activated=False,
                    event=_referral_event_from_record(event_record),
                    skipped_reason="referrer_not_found",
                )
            active_count = referrer.active_referral_count + 1
            event_record.status = "active"
            event_record.activation_event_key = qualifying_event_key
            event_record.activated_at = now
            award: PremiumAward | None = None
            existing_award = await session.scalar(
                select(ReferralPremiumAwardRecord).where(
                    ReferralPremiumAwardRecord.referrer_user_id == referrer.user_id,
                    ReferralPremiumAwardRecord.milestone == threshold,
                )
            )
            premium_until = referrer.premium_until
            if active_count >= threshold and existing_award is None:
                base = premium_until if premium_until and premium_until > now else now
                premium_until = base + premium_extension
                session.add(
                    ReferralPremiumAwardRecord(
                        referrer_user_id=referrer.user_id,
                        milestone=threshold,
                    )
                )
                award = PremiumAward(
                    referrer_user_id=referrer.user_id,
                    milestone=threshold,
                    premium_until=premium_until,
                    created=True,
                )
            referrer.active_referral_count = active_count
            referrer.premium_until = premium_until
            referrer.updated_at = now
            await session.commit()
            return ReferralActivationResult(
                activated=True,
                event=_referral_event_from_record(event_record),
                active_referral_count=active_count,
                premium_award=award,
            )

    async def audit(self, *, user_id: int, action: str, reason: str) -> None:
        async with self._session_factory() as session:
            session.add(ReferralAuditEventRecord(user_id=user_id, action=action, reason=reason))
            await session.commit()


class SQLAlchemySourceConfigRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def list_active_sources(self) -> list[SourceConfig]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(IngestionSourceRecord)
                    .where(IngestionSourceRecord.enabled.is_(True))
                    .where(IngestionSourceRecord.deleted_at.is_(None))
                    .order_by(IngestionSourceRecord.source_id)
                )
            ).all()
            return [_source_config_from_record(row) for row in rows]

    async def create_or_enable_source(
        self,
        *,
        source_type: SourceType,
        identifier: str,
        name: str,
        adapter_name: str | None = None,
        source_profile: str | None = None,
        session_name: str | None = None,
    ) -> tuple[SourceConfig, bool]:
        source_id = f"{source_type}:{identifier}"
        listener_key = session_name or "acc_9889"
        async with self._session_factory() as session:
            account = await session.scalar(
                select(ListenerAccountRecord).where(
                    ListenerAccountRecord.account_key == listener_key
                )
            )
            if account is None:
                account = ListenerAccountRecord(
                    account_key=listener_key,
                    enabled=True,
                    health_status="online",
                    flood_wait_count=0,
                )
                session.add(account)
                await session.flush()

            row = await session.scalar(
                select(IngestionSourceRecord).where(IngestionSourceRecord.source_id == source_id)
            )
            created = row is None
            if row is None:
                row = IngestionSourceRecord(
                    source_id=source_id,
                    name=name,
                    source_type=cast(Any, source_type),
                    identifier=identifier,
                    enabled=True,
                    adapter_name=adapter_name,
                    source_profile=source_profile,
                    listener_account_key=listener_key,
                )
                session.add(row)
            else:
                row.enabled = True
                row.name = name
                if adapter_name is not None:
                    row.adapter_name = adapter_name
                if source_profile is not None:
                    row.source_profile = source_profile
                row.listener_account_key = listener_key
            await session.commit()
            await session.refresh(row)
            return _source_config_from_record(row), created


class SQLAlchemySearchRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def search(self, criteria: SearchCriteria) -> SearchResult:
        try:
            async with self._session_factory() as session:
                statement = select(AnnouncementRecord).where(
                    AnnouncementRecord.status == "active",
                    AnnouncementRecord.parent_announcement_id.is_(None),
                    AnnouncementRecord.price_period != "one_time",
                )
                if criteria.include_per_person is False:
                    statement = statement.where(
                        or_(
                            AnnouncementRecord.price_basis == "total",
                            AnnouncementRecord.price_basis.is_(None),
                        )
                    )
                if criteria.district is not None:
                    statement = statement.where(
                        func.lower(AnnouncementRecord.district) == criteria.district.casefold()
                    )
                if criteria.rooms is not None:
                    statement = statement.where(AnnouncementRecord.rooms == criteria.rooms)
                if criteria.renovation_level is not None:
                    statement = statement.where(
                        AnnouncementRecord.renovation_level == criteria.renovation_level
                    )
                if criteria.audience_tag is not None:
                    statement = statement.where(
                        text(":audience_tag = any(audience_tags)").bindparams(
                            audience_tag=criteria.audience_tag
                        )
                    )
                if criteria.min_price is not None:
                    statement = statement.where(
                        or_(
                            AnnouncementRecord.price_normalized_monthly.is_(None),
                            AnnouncementRecord.price_normalized_monthly >= criteria.min_price,
                        )
                    )
                if criteria.max_price is not None:
                    statement = statement.where(
                        or_(
                            AnnouncementRecord.price_normalized_monthly.is_(None),
                            AnnouncementRecord.price_normalized_monthly <= criteria.max_price,
                        )
                    )
                order_by: tuple[Any, ...] = (
                    AnnouncementRecord.created_at.desc(),
                    AnnouncementRecord.announcement_id.desc(),
                )
                if criteria.sort == "cheapest":
                    order_by = (
                        AnnouncementRecord.price_normalized_monthly.is_(None),
                        AnnouncementRecord.price_normalized_monthly.asc().nulls_last(),
                        AnnouncementRecord.created_at.asc(),
                        AnnouncementRecord.announcement_id.asc(),
                    )
                elif criteria.sort == "price_desc":
                    order_by = (
                        AnnouncementRecord.price_normalized_monthly.isnot(None),
                        AnnouncementRecord.price_normalized_monthly.desc().nulls_last(),
                        AnnouncementRecord.created_at.desc(),
                        AnnouncementRecord.announcement_id.desc(),
                    )
                count_statement = select(func.count()).select_from(statement.subquery())
                total = await session.scalar(count_statement)
                rows = (
                    (
                        await session.execute(
                            statement.order_by(*order_by)
                            .offset(criteria.offset)
                            .limit(criteria.limit)
                        )
                    )
                    .scalars()
                    .all()
                )
                return SearchResult(
                    items=tuple(_announcement_from_record(item) for item in rows),
                    metadata=SearchMetadata(
                        returned=len(rows),
                        limit=criteria.limit,
                        offset=criteria.offset,
                        total=int(total or 0),
                        per_person_included=criteria.include_per_person,
                        null_price_included=criteria.min_price is None
                        and criteria.max_price is None,
                    ),
                )
        except Exception:
            return SearchResult(
                items=(),
                metadata=SearchMetadata(
                    returned=0,
                    limit=criteria.limit,
                    offset=criteria.offset,
                    total=0,
                    per_person_included=criteria.include_per_person,
                    null_price_included=criteria.min_price is None and criteria.max_price is None,
                ),
            )

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None:
        try:
            parsed_id = UUID(announcement_id)
        except ValueError:
            return None
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AnnouncementRecord).where(
                    AnnouncementRecord.announcement_id == parsed_id,
                    AnnouncementRecord.status == "active",
                    AnnouncementRecord.parent_announcement_id.is_(None),
                    AnnouncementRecord.price_period != "one_time",
                )
            )
            return None if row is None else _announcement_from_record(row)


class SQLAlchemySavedFilterRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def create(self, *, user_id: int, name: str, criteria: SearchCriteria) -> UserFilter:
        async with self._session_factory() as session:
            record = UserFilterRecord(
                user_id=user_id,
                name=name,
                filters={"criteria": criteria.model_dump(mode="json")},
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return _filter_from_record(record)

    async def list_for_user(self, *, user_id: int) -> list[UserFilter]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(UserFilterRecord)
                    .where(UserFilterRecord.user_id == user_id)
                    .order_by(UserFilterRecord.created_at.desc(), UserFilterRecord.filter_id.desc())
                )
            ).all()
            return [_filter_from_record(row) for row in rows]

    async def get_for_user(self, *, user_id: int, filter_id: str) -> UserFilter | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(UserFilterRecord).where(
                    UserFilterRecord.user_id == user_id,
                    UserFilterRecord.filter_id == UUID(filter_id),
                )
            )
            return None if row is None else _filter_from_record(row)

    async def update(
        self,
        *,
        user_id: int,
        filter_id: str,
        name: str | None = None,
        criteria: SearchCriteria | None = None,
        enabled: bool | None = None,
    ) -> UserFilter:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(UserFilterRecord).where(
                    UserFilterRecord.user_id == user_id,
                    UserFilterRecord.filter_id == UUID(filter_id),
                )
            )
            if row is None:
                raise KeyError("Saved filter not found.")
            row.name = row.name if name is None else name
            row.filters = {
                "criteria": (criteria or _filter_from_record(row).criteria).model_dump(mode="json")
            }
            if enabled is not None:
                row.enabled = enabled
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return _filter_from_record(row)

    async def delete(self, *, user_id: int, filter_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(UserFilterRecord).where(
                    UserFilterRecord.user_id == user_id,
                    UserFilterRecord.filter_id == UUID(filter_id),
                )
            )
            await session.commit()
            return bool(getattr(result, "rowcount", 0))


class SQLAlchemySourceSuggestionRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def submit(
        self,
        *,
        user_id: int,
        source_identifier: str,
        source_type: SourceType,
    ) -> tuple[SourceSuggestion, bool]:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SourceSuggestionRecord).where(
                    SourceSuggestionRecord.source_identifier == source_identifier
                )
            )
            if row is not None:
                if row.status == "rejected":
                    row.user_id = user_id
                    row.source_type = source_type
                    row.status = "pending"
                    row.admin_note = None
                    row.updated_at = datetime.now(UTC)
                    row.decided_at = None
                    await session.commit()
                    return _source_suggestion_from_record(row), True
                return _source_suggestion_from_record(row), False
            row = SourceSuggestionRecord(
                user_id=user_id,
                source_identifier=source_identifier,
                source_type=cast(Any, source_type),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _source_suggestion_from_record(row), True

    async def list_pending(self) -> tuple[SourceSuggestion, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(SourceSuggestionRecord)
                    .where(SourceSuggestionRecord.status == "pending")
                    .order_by(
                        SourceSuggestionRecord.created_at, SourceSuggestionRecord.source_identifier
                    )
                )
            ).all()
            return tuple(_source_suggestion_from_record(row) for row in rows)

    async def get(self, *, suggestion_id: str) -> SourceSuggestion | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SourceSuggestionRecord).where(
                    SourceSuggestionRecord.suggestion_id == UUID(suggestion_id)
                )
            )
            return None if row is None else _source_suggestion_from_record(row)

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
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SourceSuggestionRecord).where(
                    SourceSuggestionRecord.suggestion_id == UUID(suggestion_id)
                )
            )
            if row is None:
                raise KeyError(suggestion_id)
            row.status = status
            row.admin_note = admin_note
            row.reward_granted = reward_granted
            row.source_id = source_id or row.source_id
            row.updated_at = decided_at
            row.decided_at = decided_at
            await session.commit()
            return _source_suggestion_from_record(row)

    async def audit_once(
        self,
        event: SourceSuggestionAuditEvent,
    ) -> tuple[SourceSuggestionAuditEvent, bool]:
        async with self._session_factory() as session:
            record = AdminAuditEventRecord(
                admin_user_id=event.actor_user_id or 0,
                action=event.action,
                entity_type="source_suggestion",
                entity_id=event.suggestion_id,
                idempotency_key=event.idempotency_key,
                note=event.note,
                created_at=event.created_at,
            )
            session.add(record)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                existing = await session.scalar(
                    select(AdminAuditEventRecord).where(
                        AdminAuditEventRecord.idempotency_key == event.idempotency_key
                    )
                )
                return event, False if existing is not None else False
            return event, True

    async def grant_premium(
        self,
        *,
        user_id: int,
        extension: timedelta,
        now: datetime,
    ) -> datetime:
        async with self._session_factory() as session:
            row = await session.scalar(select(UserRecord).where(UserRecord.user_id == user_id))
            if row is None:
                raise KeyError(user_id)
            base = row.premium_until or now
            if base <= now:
                base = now
            row.premium_until = base + extension
            row.updated_at = now
            await session.commit()
            return row.premium_until


class SQLAlchemyAudienceTagRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def list_approved(self, *, limit: int | None = None) -> tuple[AudienceTagType, ...]:
        async with self._session_factory() as session:
            statement = (
                select(AudienceTagTypeRecord)
                .where(AudienceTagTypeRecord.status == "approved")
                .order_by(AudienceTagTypeRecord.usage_count.desc(), AudienceTagTypeRecord.tag_key)
            )
            if limit is not None:
                statement = statement.limit(limit)
            rows = (await session.scalars(statement)).all()
            return tuple(_audience_tag_from_record(row) for row in rows)

    async def list_pending(self) -> tuple[AudienceTagType, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(AudienceTagTypeRecord)
                    .where(AudienceTagTypeRecord.status == "pending")
                    .order_by(AudienceTagTypeRecord.created_at, AudienceTagTypeRecord.tag_key)
                )
            ).all()
            return tuple(_audience_tag_from_record(row) for row in rows)

    async def get(self, *, tag_key: str) -> AudienceTagType | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AudienceTagTypeRecord).where(AudienceTagTypeRecord.tag_key == tag_key)
            )
            return None if row is None else _audience_tag_from_record(row)

    async def upsert_pending(
        self,
        *,
        tag_key: str,
        display_name_uz: str,
    ) -> tuple[AudienceTagType, bool]:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AudienceTagTypeRecord).where(AudienceTagTypeRecord.tag_key == tag_key)
            )
            if row is not None:
                return _audience_tag_from_record(row), False
            row = AudienceTagTypeRecord(
                tag_key=tag_key,
                display_name_uz=display_name_uz,
                status="pending",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _audience_tag_from_record(row), True

    async def increment_usage(self, *, tag_key: str) -> AudienceTagType:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AudienceTagTypeRecord).where(AudienceTagTypeRecord.tag_key == tag_key)
            )
            if row is None:
                raise KeyError(tag_key)
            row.usage_count += 1
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return _audience_tag_from_record(row)

    async def set_status(
        self,
        *,
        tag_key: str,
        status: AudienceTagStatus,
        display_name_uz: str | None = None,
    ) -> AudienceTagType:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AudienceTagTypeRecord).where(AudienceTagTypeRecord.tag_key == tag_key)
            )
            if row is None:
                raise KeyError(tag_key)
            row.status = status
            if display_name_uz is not None:
                row.display_name_uz = display_name_uz
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return _audience_tag_from_record(row)

    async def audit_once(
        self,
        event: AudienceTagAuditEvent,
    ) -> tuple[AudienceTagAuditEvent, bool]:
        async with self._session_factory() as session:
            record = AdminAuditEventRecord(
                admin_user_id=event.admin_user_id,
                action=event.action,
                entity_type="audience_tag",
                entity_id=event.tag_key,
                idempotency_key=event.idempotency_key,
                note=event.note,
                after_state={"target_tag_key": event.target_tag_key}
                if event.target_tag_key
                else None,
                created_at=event.created_at,
            )
            session.add(record)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                return event, False
            return event, True

    async def remap_references(
        self,
        *,
        source_tag_key: str,
        target_tag_key: str,
    ) -> AudienceTagReferenceRemapResult:
        async with self._session_factory() as session:
            announcements_updated = await session.execute(
                text(
                    """
                    update announcements
                    set audience_tags = array(
                        select distinct item
                        from unnest(
                            array_replace(audience_tags, :source_tag_key, :target_tag_key)
                        ) as item
                    ),
                        audience_excluded_tags = array(
                        select distinct item
                        from unnest(
                            array_replace(
                                audience_excluded_tags,
                                :source_tag_key,
                                :target_tag_key
                            )
                        ) as item
                    ),
                        updated_at = now()
                    where :source_tag_key = any(audience_tags)
                       or :source_tag_key = any(audience_excluded_tags)
                    """
                ),
                {"source_tag_key": source_tag_key, "target_tag_key": target_tag_key},
            )
            filters_updated = await session.execute(
                text(
                    """
                    update user_filters
                    set filters = jsonb_set(
                        filters,
                        '{criteria,audience_tag}',
                        to_jsonb(:target_tag_key::text),
                        false
                    ),
                        updated_at = now()
                    where filters #>> '{criteria,audience_tag}' = :source_tag_key
                    """
                ),
                {"source_tag_key": source_tag_key, "target_tag_key": target_tag_key},
            )
            await session.commit()
            return AudienceTagReferenceRemapResult(
                announcements_updated=int(getattr(announcements_updated, "rowcount", 0) or 0),
                filters_updated=int(getattr(filters_updated, "rowcount", 0) or 0),
            )


class SQLAlchemyAnalyticsEventRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def record_once(self, event: AnalyticsEvent) -> bool:
        async with self._session_factory() as session:
            statement = pg_insert(AnalyticsEventRecord).values(
                event_name=cast(Any, event.event_name),
                idempotency_key=event.idempotency_key,
                occurred_at=event.occurred_at,
                user_id=event.user_id,
                subject_id=event.subject_id,
                event_metadata=event.metadata,
            )
            statement = statement.on_conflict_do_nothing(index_elements=["idempotency_key"])
            try:
                result = await session.execute(statement)
                await session.commit()
            except Exception:
                await session.rollback()
                return False
            return bool(getattr(result, "rowcount", 0))

    async def list_events(self, *, start_at: datetime, end_at: datetime) -> list[AnalyticsEvent]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(AnalyticsEventRecord)
                    .where(
                        AnalyticsEventRecord.occurred_at >= start_at,
                        AnalyticsEventRecord.occurred_at < end_at,
                    )
                    .order_by(
                        AnalyticsEventRecord.occurred_at.asc(),
                        AnalyticsEventRecord.analytics_event_id.asc(),
                    )
                )
            ).all()
            return [_analytics_from_record(row) for row in rows]


class SQLAlchemyTechnicalMetricRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def record_once(self, event: TechnicalMetricEvent) -> bool:
        async with self._session_factory() as session:
            row = TechnicalMetricEventRecord(
                metric_name=cast(Any, event.metric_name),
                idempotency_key=event.idempotency_key,
                occurred_at=event.occurred_at,
                value=event.value,
                component=event.component,
                subject_id=event.subject_id,
                event_metadata=event.metadata,
            )
            session.add(row)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                return False
            return True

    async def list_events(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[TechnicalMetricEvent]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(TechnicalMetricEventRecord)
                    .where(
                        TechnicalMetricEventRecord.occurred_at >= start_at,
                        TechnicalMetricEventRecord.occurred_at < end_at,
                    )
                    .order_by(
                        TechnicalMetricEventRecord.occurred_at.asc(),
                        TechnicalMetricEventRecord.technical_metric_event_id.asc(),
                    )
                )
            ).all()
            return [_technical_from_record(row) for row in rows]


class SQLAlchemyContentRepository(ContentRepository):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def daily_stats(self, *, day: date) -> ContentStats:
        async with self._session_factory() as session:
            total_checked = await session.scalar(
                select(func.count())
                .select_from(AnnouncementRecord)
                .where(func.date(AnnouncementRecord.occurred_at) == day)
            )
            active_listings = await session.scalar(
                select(func.count())
                .select_from(AnnouncementRecord)
                .where(
                    func.date(AnnouncementRecord.occurred_at) == day,
                    AnnouncementRecord.status == "active",
                    AnnouncementRecord.parent_announcement_id.is_(None),
                )
            )
            return ContentStats(
                total_checked=int(total_checked or 0),
                duplicates_detected=0,
                active_listings=int(active_listings or 0),
                average_monthly_price=None,
            )

    async def top_offers(self, *, day: date, limit: int) -> tuple[ContentTopOffer, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(AnnouncementRecord)
                    .where(
                        func.date(AnnouncementRecord.occurred_at) == day,
                        AnnouncementRecord.status == "active",
                        AnnouncementRecord.parent_announcement_id.is_(None),
                    )
                    .order_by(
                        AnnouncementRecord.price_normalized_monthly.asc().nulls_last(),
                        AnnouncementRecord.created_at.desc(),
                    )
                    .limit(limit)
                )
            ).all()
            return tuple(_top_offer_from_record(row) for row in rows)

    async def district_stats(self, *, day: date, limit: int) -> tuple[ContentDistrictStats, ...]:
        return ()

    async def count_posts(self, *, day: date) -> int:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(func.count())
                .select_from(ContentChannelPostRecord)
                .where(func.date(ContentChannelPostRecord.created_at) == day)
            )
            return int(result or 0)

    async def get_by_idempotency_key(self, *, idempotency_key: str) -> ContentPost | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ContentChannelPostRecord).where(
                    ContentChannelPostRecord.idempotency_key == idempotency_key
                )
            )
            return None if row is None else _content_post_from_record(row)

    async def save_post(self, post: ContentPost) -> ContentPost:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ContentChannelPostRecord).where(
                    ContentChannelPostRecord.post_id == UUID(post.post_id)
                )
            )
            if row is None:
                row = ContentChannelPostRecord(
                    post_id=UUID(post.post_id),
                    post_kind=cast(Any, post.kind),
                    idempotency_key=post.idempotency_key,
                    body_text=post.text,
                    status=cast(Any, post.status),
                    published_at=post.published_at,
                    publisher_message_id=post.publisher_message_id,
                    attempts=post.attempts,
                    next_attempt_at=post.next_attempt_at,
                    error_reason=post.error_reason,
                )
                session.add(row)
            else:
                row.post_kind = post.kind
                row.body_text = post.text
                row.status = cast(Any, post.status)
                row.published_at = post.published_at
                row.publisher_message_id = post.publisher_message_id
                row.attempts = post.attempts
                row.next_attempt_at = post.next_attempt_at
                row.error_reason = post.error_reason
            await session.commit()
            return post


class SQLAlchemyNotificationSavedFilterRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def list_for_matching(self) -> list[UserFilter]:
        async with self._session_factory() as session:
            rows = (await session.scalars(select(UserFilterRecord))).all()
            return [_filter_from_record(row) for row in rows]


class SQLAlchemyNotificationUserRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def get_notification_user(self, *, user_id: int) -> NotificationUser | None:
        async with self._session_factory() as session:
            row = await session.scalar(select(UserRecord).where(UserRecord.user_id == user_id))
            if row is None:
                return None
            return NotificationUser(
                user_id=row.user_id,
                premium_until=row.premium_until,
                status=row.status,
                notifications_enabled=True,
            )


class SQLAlchemyNotificationJobRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def create_pending(
        self,
        *,
        user_id: int,
        filter_id: str,
        announcement_id: str,
        priority: int,
        queue_name: NotificationQueueName,
    ) -> tuple[NotificationJob, bool]:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NotificationRecord).where(
                    NotificationRecord.user_id == user_id,
                    NotificationRecord.filter_id == UUID(filter_id),
                    NotificationRecord.announcement_id == UUID(announcement_id),
                )
            )
            if row is not None:
                return _notification_job_from_record(row, queue_name=queue_name), False
            row = NotificationRecord(
                user_id=user_id,
                filter_id=UUID(filter_id),
                announcement_id=UUID(announcement_id),
                status="pending",
                priority=priority,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _notification_job_from_record(row, queue_name=queue_name), True


class SQLAlchemyNotificationDeliveryRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def list_ready(self, *, limit: int, now: datetime) -> list[NotificationJob]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(NotificationRecord)
                    .where(
                        NotificationRecord.status == "pending",
                        or_(
                            NotificationRecord.next_attempt_at.is_(None),
                            NotificationRecord.next_attempt_at <= now,
                        ),
                    )
                    .order_by(
                        NotificationRecord.priority.desc(),
                        NotificationRecord.created_at.asc(),
                        NotificationRecord.notification_id.asc(),
                    )
                    .limit(limit)
                )
            ).all()
            return [_notification_job_from_record(row, queue_name=_queue_name(row)) for row in rows]

    async def get_delivery_context(
        self,
        *,
        notification_id: str,
    ) -> NotificationDeliveryContext | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NotificationRecord).where(
                    NotificationRecord.notification_id == UUID(notification_id)
                )
            )
            if row is None or row.filter_id is None:
                return None
            filter_row = await session.scalar(
                select(UserFilterRecord).where(UserFilterRecord.filter_id == row.filter_id)
            )
            announcement_row = await session.scalar(
                select(AnnouncementRecord).where(
                    AnnouncementRecord.announcement_id == row.announcement_id
                )
            )
            if filter_row is None or announcement_row is None:
                return None
            job = _notification_job_from_record(row, queue_name=_queue_name(row))
            announcement = _announcement_from_record(announcement_row)
            return NotificationDeliveryContext(
                job=job, announcement=announcement, filter_name=filter_row.name
            )

    async def mark_sending(
        self,
        *,
        notification_id: str,
        now: datetime,
    ) -> NotificationJob | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NotificationRecord).where(
                    NotificationRecord.notification_id == UUID(notification_id),
                    NotificationRecord.status == "pending",
                    or_(
                        NotificationRecord.next_attempt_at.is_(None),
                        NotificationRecord.next_attempt_at <= now,
                    ),
                )
            )
            if row is None:
                return None
            row.status = "sending"
            row.attempts += 1
            row.last_attempt_at = now
            await session.commit()
            return _notification_job_from_record(row, queue_name=_queue_name(row))

    async def mark_sent(
        self,
        *,
        notification_id: str,
        telegram_message_id: str,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NotificationRecord).where(
                    NotificationRecord.notification_id == UUID(notification_id)
                )
            )
            if row is None:
                raise KeyError(notification_id)
            row.status = "sent"
            row.sent_at = now
            row.delivered_at = now
            row.telegram_message_id = telegram_message_id
            row.next_attempt_at = None
            row.error_reason = None
            attempt = NotificationDeliveryAttempt(
                notification_id=notification_id,
                attempt_no=row.attempts,
                result="sent",
                telegram_message_id=telegram_message_id,
                created_at=now,
            )
            session.add(
                NotificationDeliveryAttemptRecord(
                    notification_id=UUID(notification_id),
                    attempt_no=row.attempts,
                    result="sent",
                    telegram_message_id=telegram_message_id,
                    created_at=now,
                )
            )
            await session.commit()
            return attempt

    async def mark_retryable_failure(
        self,
        *,
        notification_id: str,
        error_type: str,
        retry_after: timedelta,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NotificationRecord).where(
                    NotificationRecord.notification_id == UUID(notification_id)
                )
            )
            if row is None:
                raise KeyError(notification_id)
            row.status = "pending"
            row.next_attempt_at = now + retry_after
            row.error_reason = error_type
            attempt = NotificationDeliveryAttempt(
                notification_id=notification_id,
                attempt_no=row.attempts,
                result="retryable_failure",
                error_type=error_type,
                retryable=True,
                created_at=now,
            )
            session.add(
                NotificationDeliveryAttemptRecord(
                    notification_id=UUID(notification_id),
                    attempt_no=row.attempts,
                    result="retryable_failure",
                    error_type=error_type,
                    retryable=True,
                    created_at=now,
                )
            )
            await session.commit()
            return attempt

    async def mark_permanent_failure(
        self,
        *,
        notification_id: str,
        error_type: str,
        now: datetime,
    ) -> NotificationDeliveryAttempt:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NotificationRecord).where(
                    NotificationRecord.notification_id == UUID(notification_id)
                )
            )
            if row is None:
                raise KeyError(notification_id)
            row.status = "suppressed" if error_type == "telegram_user_blocked" else "failed"
            row.next_attempt_at = None
            row.error_reason = error_type
            attempt = NotificationDeliveryAttempt(
                notification_id=notification_id,
                attempt_no=row.attempts,
                result="permanent_failure",
                error_type=error_type,
                retryable=False,
                created_at=now,
            )
            session.add(
                NotificationDeliveryAttemptRecord(
                    notification_id=UUID(notification_id),
                    attempt_no=row.attempts,
                    result="permanent_failure",
                    error_type=error_type,
                    retryable=False,
                    created_at=now,
                )
            )
            await session.commit()
            return attempt


def _user_from_record(record: UserRecord) -> TelegramUser:
    return TelegramUser(
        user_id=record.user_id,
        referral_code=record.referral_code,
        referred_by=record.referred_by,
        premium_until=record.premium_until,
        active_referral_count=record.active_referral_count,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _referral_event_from_record(record: ReferralEventRecord) -> ReferralEvent:
    return ReferralEvent(
        referral_event_id=str(record.referral_event_id),
        referrer_user_id=record.referrer_user_id,
        referred_user_id=record.referred_user_id,
        activation_event_key=record.activation_event_key,
        status=cast(Any, record.status),
        created_at=record.created_at,
        activated_at=record.activated_at,
    )


def _source_config_from_record(record: IngestionSourceRecord) -> SourceConfig:
    return SourceConfig(
        source_id=record.source_id,
        name=record.name,
        source_type=cast(SourceType, record.source_type),
        identifier=record.identifier,
        enabled=record.enabled,
        adapter_name=record.adapter_name,
        source_profile=record.source_profile,
        listener_account_key=record.listener_account_key,
    )


def _filter_from_record(record: UserFilterRecord) -> UserFilter:
    payload = record.filters or {}
    criteria_payload = payload.get("criteria", payload)
    return UserFilter(
        filter_id=str(record.filter_id),
        user_id=record.user_id,
        name=record.name,
        criteria=SearchCriteria.model_validate(criteria_payload),
        enabled=record.enabled,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _source_suggestion_from_record(record: SourceSuggestionRecord) -> SourceSuggestion:
    return SourceSuggestion(
        suggestion_id=str(record.suggestion_id),
        user_id=record.user_id,
        source_identifier=record.source_identifier,
        source_type=cast(Any, record.source_type),
        status=cast(Any, record.status),
        admin_note=record.admin_note,
        reward_granted=record.reward_granted,
        source_id=record.source_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        decided_at=record.decided_at,
    )


def _audience_tag_from_record(record: AudienceTagTypeRecord) -> AudienceTagType:
    return AudienceTagType(
        tag_key=record.tag_key,
        display_name_uz=record.display_name_uz,
        status=cast(Any, record.status),
        usage_count=record.usage_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _analytics_from_record(record: AnalyticsEventRecord) -> AnalyticsEvent:
    return AnalyticsEvent(
        event_name=cast(Any, record.event_name),
        idempotency_key=record.idempotency_key,
        occurred_at=record.occurred_at,
        user_id=record.user_id,
        subject_id=record.subject_id,
        metadata=dict(record.event_metadata or {}),
    )


def _technical_from_record(record: TechnicalMetricEventRecord) -> TechnicalMetricEvent:
    return TechnicalMetricEvent(
        metric_name=cast(Any, record.metric_name),
        idempotency_key=record.idempotency_key,
        occurred_at=record.occurred_at,
        value=record.value,
        component=record.component,
        subject_id=record.subject_id,
        metadata=dict(record.event_metadata or {}),
    )


def _content_post_from_record(record: ContentChannelPostRecord) -> ContentPost:
    return ContentPost(
        post_id=str(record.post_id),
        kind=cast(Any, record.post_kind),
        idempotency_key=record.idempotency_key,
        text=record.body_text,
        status=cast(Any, record.status),
        published_at=record.published_at,
        publisher_message_id=record.publisher_message_id,
        attempts=record.attempts,
        next_attempt_at=record.next_attempt_at,
        error_reason=record.error_reason,
        created_at=record.created_at,
    )


def _top_offer_from_record(record: AnnouncementRecord) -> ContentTopOffer:
    return ContentTopOffer(
        announcement_id=str(record.announcement_id),
        district=record.district,
        rooms=record.rooms,
        price_normalized_monthly=record.price_normalized_monthly,
        currency=record.currency,
        source_url=record.source_url,
        area_sqm=record.area_sqm,
        source_count=record.source_count,
        created_at=record.created_at,
    )


def _announcement_from_record(record: AnnouncementRecord) -> Any:
    from estateflow.services.extraction import CanonicalListing
    from estateflow.services.post_ai_dedup import StructuredAnnouncement

    canonical = CanonicalListing(
        prompt_version=record.prompt_version or "estateflow.listing.v1",
        price=record.price,
        currency=record.currency,
        price_period=cast(Any, record.price_period),
        price_basis=cast(Any, record.price_basis),
        price_normalized_monthly=record.price_normalized_monthly,
        listing_type=None,
        rooms=record.rooms,
        area_sqm=record.area_sqm,
        floor=record.floor,
        total_floors=record.total_floors,
        district=record.district,
        address=record.address,
        phone_numbers=list(record.phone_numbers or []),
        owner_type=cast(Any, record.owner_type),
        renovation_level=cast(Any, record.renovation_level),
        renovation_source=cast(Any, record.renovation_source),
        furniture=record.furniture,
        description=record.description,
        audience_tags=list(record.audience_tags or []),
        audience_excluded_tags=list(record.audience_excluded_tags or []),
        confidence=float(record.confidence or 0),
        field_confidence=record.field_confidence
        if isinstance(record.field_confidence, dict)
        else {},
        assumptions=list(record.assumptions or []),
    )
    return StructuredAnnouncement(
        announcement_id=str(record.announcement_id),
        idempotency_key=record.idempotency_key,
        source_id=record.source_id,
        source_channel_id=record.source_channel_id or "",
        source_message_id=record.source_message_id or "",
        occurred_at=record.occurred_at,
        canonical=canonical,
        source_url=record.source_url,
        forward_origin_key=record.forward_origin_key,
        parent_id=str(record.parent_announcement_id) if record.parent_announcement_id else None,
        status=cast(Any, record.status),
        source_count=record.source_count,
        latest_source_id=record.latest_source_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _notification_job_from_record(
    record: NotificationRecord, *, queue_name: NotificationQueueName
) -> NotificationJob:
    return NotificationJob(
        notification_id=str(record.notification_id),
        user_id=record.user_id,
        filter_id=str(record.filter_id) if record.filter_id is not None else "",
        announcement_id=str(record.announcement_id),
        status=cast(Any, record.status),
        priority=record.priority,
        queue_name=queue_name,
        created_at=record.created_at,
        attempts=record.attempts,
        next_attempt_at=record.next_attempt_at,
        telegram_message_id=record.telegram_message_id,
    )


def _queue_name(record: NotificationRecord) -> NotificationQueueName:
    return HIGH_PRIORITY_NOTIFICATION_QUEUE if record.priority > 0 else "notifications.standard"
