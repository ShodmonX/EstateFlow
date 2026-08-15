# ruff: noqa: E501
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from estateflow.models.base import Base, CreatedAtMixin


class AnalyticsEventRecord(CreatedAtMixin, Base):
    __tablename__ = "analytics_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_analytics_events_idempotency_key"),
        CheckConstraint(
            "event_name in ('user_registered', 'search_completed', 'saved_filter_created', 'referral_accepted', "
            "'referral_activated', 'notification_sent', 'notification_delivery_failed', 'notification_action_open', "
            "'notification_action_search', 'notification_action_read')",
            name="ck_analytics_events_event_name",
        ),
        Index("ix_analytics_events_name_time", "event_name", "occurred_at"),
        Index(
            "ix_analytics_events_user_time",
            "user_id",
            "occurred_at",
            postgresql_where=text("user_id is not null"),
        ),
        Index(
            "ix_analytics_events_subject_time",
            "subject_id",
            "occurred_at",
            postgresql_where=text("subject_id is not null"),
        ),
    )

    analytics_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    subject_id: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class TechnicalMetricEventRecord(CreatedAtMixin, Base):
    __tablename__ = "technical_metric_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_technical_metric_events_idempotency_key"),
        CheckConstraint(
            "metric_name in ('queue_delay_ms', 'ai_fallback_attempt', 'ai_validation_failure', 'dedup_decision', "
            "'notification_retry', 'notification_error', 'listener_health')",
            name="ck_technical_metric_events_metric_name",
        ),
        Index("ix_technical_metric_events_name_time", "metric_name", "occurred_at"),
        Index(
            "ix_technical_metric_events_component_time",
            "component",
            "occurred_at",
            postgresql_where=text("component is not null"),
        ),
        Index(
            "ix_technical_metric_events_subject_time",
            "subject_id",
            "occurred_at",
            postgresql_where=text("subject_id is not null"),
        ),
    )

    technical_metric_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    value: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1")
    )
    component: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
