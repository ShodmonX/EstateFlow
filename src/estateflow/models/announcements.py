# ruff: noqa: E501
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from estateflow.models.base import Base, CreatedAtMixin, CreatedUpdatedDeletedMixin


class UserRecord(CreatedUpdatedDeletedMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("referral_code", name="uq_users_referral_code"),
        CheckConstraint(
            "active_referral_count >= 0", name="ck_users_active_referral_count_nonnegative"
        ),
        CheckConstraint(
            "status in ('active', 'disabled', 'deleted')",
            name="ck_users_status",
        ),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_username: Mapped[str | None] = mapped_column(Text)
    referral_code: Mapped[str] = mapped_column(Text, nullable=False)
    referred_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    premium_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_referral_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default=text("'active'")
    )


class AnnouncementRecord(CreatedUpdatedDeletedMixin, Base):
    __tablename__ = "announcements"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_announcements_idempotency_key"),
        CheckConstraint(
            "status in ('active', 'archived', 'manual_review', 'deleted')",
            name="ck_announcements_status",
        ),
        CheckConstraint("source_count >= 1", name="ck_announcements_source_count_min"),
        CheckConstraint(
            "price_period in ('daily', 'monthly', 'one_time')",
            name="ck_announcements_price_period",
        ),
        CheckConstraint(
            "price_basis in ('total', 'per_person')",
            name="ck_announcements_price_basis",
        ),
        CheckConstraint("rooms is null or rooms >= 0", name="ck_announcements_rooms_nonnegative"),
        CheckConstraint(
            "area_sqm is null or area_sqm >= 0", name="ck_announcements_area_sqm_nonnegative"
        ),
        CheckConstraint(
            "owner_type in ('owner', 'agent', 'unknown')",
            name="ck_announcements_owner_type",
        ),
        CheckConstraint(
            "renovation_level is null or renovation_level in ('none', 'basic', 'good', 'euro', 'luxury')",
            name="ck_announcements_renovation_level",
        ),
        CheckConstraint(
            "renovation_source in ('vision', 'text', 'unknown')",
            name="ck_announcements_renovation_source",
        ),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="ck_announcements_confidence_range",
        ),
        CheckConstraint(
            "parent_announcement_id is null or parent_announcement_id <> announcement_id",
            name="ck_announcements_parent_not_self",
        ),
        Index(
            "uq_announcements_one_parent_per_source_message",
            "source_id",
            "source_channel_id",
            "source_message_id",
            unique=True,
            postgresql_where=text("status <> 'deleted'"),
        ),
        Index(
            "ix_announcements_user_search_canonical",
            "status",
            "created_at",
            postgresql_where=text("parent_announcement_id is null and status = 'active'"),
        ),
        Index(
            "ix_announcements_parent",
            "parent_announcement_id",
            postgresql_where=text("parent_announcement_id is not null"),
        ),
        Index(
            "ix_announcements_dedup_candidates",
            "district",
            "rooms",
            "occurred_at",
            postgresql_where=text("parent_announcement_id is null and status = 'active'"),
        ),
        Index(
            "ix_announcements_price_search",
            "price_period",
            "price_basis",
            "price_normalized_monthly",
            postgresql_where=text("parent_announcement_id is null and status = 'active'"),
        ),
        Index("ix_announcements_audience_tags_gin", "audience_tags", postgresql_using="gin"),
        Index(
            "ix_announcements_sprint4_search_filters",
            "district",
            "rooms",
            "renovation_level",
            "created_at",
            postgresql_where=text(
                "parent_announcement_id is null and status = 'active' and price_period in ('daily', 'monthly')"
            ),
        ),
        Index(
            "ix_announcements_audience_excluded_tags_gin",
            "audience_excluded_tags",
            postgresql_using="gin",
        ),
        Index(
            "ix_announcements_latest_source",
            "latest_source_id",
            postgresql_where=text("latest_source_id is not null"),
        ),
    )

    announcement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(
        Text, ForeignKey("ingestion_sources.source_id"), nullable=False
    )
    source_channel_id: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[str | None] = mapped_column(Text)
    source_message_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    parent_announcement_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("announcements.announcement_id")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default=text("'active'")
    )
    canonical_rank: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    latest_source_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("ingestion_sources.source_id")
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    forward_origin_key: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(Text)
    price_period: Mapped[str] = mapped_column(
        Text, nullable=False, default="monthly", server_default=text("'monthly'")
    )
    price_basis: Mapped[str] = mapped_column(
        Text, nullable=False, default="total", server_default=text("'total'")
    )
    price_normalized_monthly: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    rooms: Mapped[int | None] = mapped_column(Integer)
    area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    district: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    phone_numbers: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    owner_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="unknown", server_default=text("'unknown'")
    )
    renovation_level: Mapped[str | None] = mapped_column(Text)
    renovation_source: Mapped[str] = mapped_column(
        Text, nullable=False, default="unknown", server_default=text("'unknown'")
    )
    furniture: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    audience_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    audience_excluded_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    field_confidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    assumptions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    prompt_version: Mapped[str | None] = mapped_column(Text)
    is_promoted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    promoted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnnouncementMediaRecord(CreatedAtMixin, Base):
    __tablename__ = "announcement_media"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_announcement_media_object_key"),
        CheckConstraint("size_bytes >= 0", name="ck_announcement_media_size_bytes_nonnegative"),
        Index("ix_announcement_media_announcement", "announcement_id"),
        Index(
            "ix_announcement_media_phash",
            "phash",
            postgresql_where=text("phash is not null"),
        ),
    )

    media_id: Mapped[str] = mapped_column(Text, primary_key=True)
    announcement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("announcements.announcement_id"),
        nullable=False,
        primary_key=True,
    )
    storage_url: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    phash: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    object_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class UserFilterRecord(CreatedAtMixin, Base):
    __tablename__ = "user_filters"
    __table_args__ = (
        Index("ix_user_filters_user_enabled", "user_id", postgresql_where=text("enabled")),
        Index("ix_user_filters_user_created", "user_id", "created_at"),
        Index("ix_user_filters_matching_enabled", "enabled", "updated_at"),
    )

    filter_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NotificationRecord(CreatedAtMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "announcement_id",
            "filter_id",
            name="uq_notifications_user_announcement_filter",
        ),
        CheckConstraint(
            "status in ('pending', 'sent', 'failed', 'suppressed')",
            name="ck_notifications_status",
        ),
        CheckConstraint("priority >= 0", name="ck_notifications_priority_nonnegative"),
        CheckConstraint("attempts >= 0", name="ck_notifications_attempts_nonnegative"),
        Index("ix_notifications_user_status_created", "user_id", "status", "created_at"),
        Index(
            "ix_notifications_pending_priority_created",
            "priority",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_notifications_ready_delivery",
            "status",
            "next_attempt_at",
            "priority",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    notification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=False)
    announcement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("announcements.announcement_id"), nullable=False
    )
    filter_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_filters.filter_id")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_message_id: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_reason: Mapped[str | None] = mapped_column(Text)


class NotificationDeliveryAttemptRecord(CreatedAtMixin, Base):
    __tablename__ = "notification_delivery_attempts"
    __table_args__ = (
        CheckConstraint(
            "attempt_no >= 0", name="ck_notification_delivery_attempts_attempt_no_nonnegative"
        ),
        CheckConstraint(
            "result in ('sending', 'sent', 'retryable_failure', 'permanent_failure', 'rate_limited', 'skipped')",
            name="ck_notification_delivery_attempts_result",
        ),
        Index("ix_notification_delivery_attempts_notification", "notification_id", "created_at"),
    )

    attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    notification_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("notifications.notification_id"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    error_type: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    telegram_message_id: Mapped[str | None] = mapped_column(Text)


class ParsingLogRecord(CreatedAtMixin, Base):
    __tablename__ = "parsing_logs"
    __table_args__ = (
        CheckConstraint(
            "status in ('succeeded', 'manual_review', 'failed')",
            name="ck_parsing_logs_status",
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    media: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    error_type: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    post_ai_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProcessingJobRecord(CreatedAtMixin, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        UniqueConstraint(
            "queue_name", "idempotency_key", name="uq_processing_jobs_queue_idempotency"
        ),
        CheckConstraint(
            "status in ('pending', 'processing', 'succeeded', 'failed', 'dead_letter')",
            name="ck_processing_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_processing_jobs_attempts_nonnegative"),
        Index("ix_processing_jobs_ready", "queue_name", "status", "available_at"),
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    queue_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
