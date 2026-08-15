# ruff: noqa: E501
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from estateflow.models.base import Base, CreatedUpdatedDeletedMixin, CreatedUpdatedMixin


class ListenerAccountRecord(CreatedUpdatedMixin, Base):
    __tablename__ = "listener_accounts"
    __table_args__ = (
        CheckConstraint(
            "health_status in ('online', 'reconnecting', 'flood_wait', 'disabled', 'banned')",
            name="ck_listener_accounts_health_status",
        ),
        CheckConstraint(
            "flood_wait_count >= 0", name="ck_listener_accounts_flood_wait_count_nonnegative"
        ),
    )

    account_key: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    health_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="online", server_default=text("'online'")
    )
    last_successful_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flood_wait_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class IngestionSourceRecord(CreatedUpdatedDeletedMixin, Base):
    __tablename__ = "ingestion_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type in ('telegram_channel', 'telegram_group', 'website')",
            name="ck_ingestion_sources_source_type",
        ),
        CheckConstraint(
            "status in ('active', 'disabled', 'degraded', 'deleted')",
            name="ck_ingestion_sources_status",
        ),
        CheckConstraint(
            "failure_count >= 0", name="ck_ingestion_sources_failure_count_nonnegative"
        ),
        CheckConstraint(
            "average_response_ms is null or average_response_ms >= 0",
            name="ck_ingestion_sources_average_response_ms_nonnegative",
        ),
        Index(
            "ix_ingestion_sources_active",
            "source_type",
            "status",
            postgresql_where=text("enabled and status = 'active'"),
        ),
        Index(
            "ix_ingestion_sources_listener_account",
            "listener_account_key",
            postgresql_where=text("listener_account_key is not null"),
        ),
    )

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="telegram_channel")
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    adapter_name: Mapped[str | None] = mapped_column(Text)
    source_profile: Mapped[str | None] = mapped_column(Text)
    listener_account_key: Mapped[str | None] = mapped_column(
        Text, ForeignKey("listener_accounts.account_key")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default=text("'active'")
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    average_response_ms: Mapped[int | None] = mapped_column(Integer)


class ChannelAssignmentRecord(Base):
    __tablename__ = "channel_assignments"
    __table_args__ = (
        CheckConstraint(
            "reason in ('initial', 'rebalance', 'failover', 'source_disabled')",
            name="ck_channel_assignments_reason",
        ),
        Index(
            "uq_channel_assignments_one_active_source",
            "source_id",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index(
            "ix_channel_assignments_active_account",
            "account_key",
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        Text, ForeignKey("ingestion_sources.source_id"), nullable=False
    )
    account_key: Mapped[str] = mapped_column(
        Text, ForeignKey("listener_accounts.account_key"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class ChannelAssignmentAuditRecord(Base):
    __tablename__ = "channel_assignment_audit"
    __table_args__ = (
        CheckConstraint(
            "reason in ('initial', 'rebalance', 'failover', 'source_disabled')",
            name="ck_channel_assignment_audit_reason",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    previous_account_key: Mapped[str | None] = mapped_column(Text)
    new_account_key: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
