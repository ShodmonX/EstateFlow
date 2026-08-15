# ruff: noqa: E501
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from estateflow.models.base import Base, CreatedAtMixin, CreatedUpdatedMixin


class AudienceTagTypeRecord(CreatedAtMixin, Base):
    __tablename__ = "audience_tag_types"
    __table_args__ = (
        UniqueConstraint("tag_key", name="uq_audience_tag_types_tag_key"),
        CheckConstraint(
            "status in ('approved', 'pending', 'rejected')",
            name="ck_audience_tag_types_status",
        ),
        CheckConstraint("usage_count >= 0", name="ck_audience_tag_types_usage_count_nonnegative"),
        Index(
            "ix_audience_tag_types_pending",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_audience_tag_types_approved_usage",
            "usage_count",
            "tag_key",
            postgresql_where=text("status = 'approved'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tag_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name_uz: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="approved", server_default=text("'approved'")
    )
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SourceSuggestionRecord(CreatedUpdatedMixin, Base):
    __tablename__ = "source_suggestions"
    __table_args__ = (
        UniqueConstraint("source_identifier", name="uq_source_suggestions_source_identifier"),
        CheckConstraint(
            "status in ('pending', 'approved', 'rejected')",
            name="ck_source_suggestions_status",
        ),
        CheckConstraint(
            "source_type in ('telegram_channel', 'telegram_group')",
            name="ck_source_suggestions_source_type",
        ),
        Index("ix_source_suggestions_status_created", "status", "created_at"),
        Index(
            "ix_source_suggestions_pending",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    suggestion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    source_identifier: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    admin_note: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="telegram_channel", server_default=text("'telegram_channel'")
    )
    reward_granted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    source_id: Mapped[str | None] = mapped_column(Text, ForeignKey("ingestion_sources.source_id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
