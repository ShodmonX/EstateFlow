# ruff: noqa: E501
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from estateflow.models.base import Base, CreatedAtMixin


class ReferralEventRecord(CreatedAtMixin, Base):
    __tablename__ = "referral_events"
    __table_args__ = (
        UniqueConstraint("referred_user_id", name="uq_referral_events_referred_user_id"),
        UniqueConstraint("activation_event_key", name="uq_referral_events_activation_event_key"),
        CheckConstraint(
            "status in ('pending', 'active', 'rejected')",
            name="ck_referral_events_status",
        ),
        Index("ix_referral_events_referrer_status", "referrer_user_id", "status"),
        Index("ix_referral_events_referred_status", "referred_user_id", "status"),
    )

    referral_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    referrer_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), nullable=False
    )
    referred_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), nullable=False
    )
    activation_event_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralPremiumAwardRecord(CreatedAtMixin, Base):
    __tablename__ = "referral_premium_awards"
    __table_args__ = (
        UniqueConstraint(
            "referrer_user_id", "milestone", name="uq_referral_premium_awards_referrer_milestone"
        ),
        CheckConstraint("milestone > 0", name="ck_referral_premium_awards_milestone_positive"),
    )

    award_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    referrer_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), nullable=False
    )
    milestone: Mapped[int] = mapped_column(Integer, nullable=False)


class ReferralAuditEventRecord(CreatedAtMixin, Base):
    __tablename__ = "referral_audit_events"
    __table_args__ = (Index("ix_referral_audit_events_user_created", "user_id", "created_at"),)

    audit_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
