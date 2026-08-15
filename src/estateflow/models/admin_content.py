# ruff: noqa: E501
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from estateflow.models.base import Base, CreatedAtMixin


class AdminAuditEventRecord(CreatedAtMixin, Base):
    __tablename__ = "admin_audit_events"
    __table_args__ = (
        Index("ix_admin_audit_events_entity_created", "entity_type", "entity_id", "created_at"),
    )

    audit_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    admin_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)


class ContentChannelPostRecord(CreatedAtMixin, Base):
    __tablename__ = "content_channel_posts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_content_channel_posts_idempotency_key"),
        CheckConstraint(
            "post_kind in ('daily_stats', 'top_offer', 'district_stats', 'transparency_report')",
            name="ck_content_channel_posts_post_kind",
        ),
        CheckConstraint(
            "status in ('drafted', 'published', 'dry_run', 'failed')",
            name="ck_content_channel_posts_status",
        ),
        Index("ix_content_channel_posts_created", "created_at"),
        Index(
            "ix_content_channel_posts_retry",
            "next_attempt_at",
            postgresql_where=text("status = 'failed'"),
        ),
    )

    post_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    post_kind: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column("text", Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    publisher_message_id: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_reason: Mapped[str | None] = mapped_column(Text)
    schedule_timezone: Mapped[str] = mapped_column(
        Text, nullable=False, default="Asia/Tashkent", server_default=text("'Asia/Tashkent'")
    )
