# ruff: noqa: E501
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class DedupDecisionRecord(CreatedAtMixin, Base):
    __tablename__ = "dedup_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision in ('exact_duplicate', 'high_confidence_duplicate', 'possible_duplicate', 'new')",
            name="ck_dedup_decisions_decision",
        ),
        CheckConstraint("score >= 0", name="ck_dedup_decisions_score_nonnegative"),
        CheckConstraint("threshold >= 0", name="ck_dedup_decisions_threshold_nonnegative"),
        Index("ix_dedup_decisions_announcement", "announcement_id", "created_at"),
    )

    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    announcement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("announcements.announcement_id"), nullable=False
    )
    candidate_announcement_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("announcements.announcement_id")
    )
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class AnnouncementMergeAuditRecord(CreatedAtMixin, Base):
    __tablename__ = "announcement_merge_audit"
    __table_args__ = (
        UniqueConstraint(
            "child_announcement_id", name="uq_announcement_merge_audit_child_announcement_id"
        ),
    )

    audit_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    parent_announcement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("announcements.announcement_id"), nullable=False
    )
    child_announcement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("announcements.announcement_id"), nullable=False
    )
    decision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dedup_decisions.decision_id")
    )
    merge_policy: Mapped[str] = mapped_column(Text, nullable=False)
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


class ManualReviewItemRecord(CreatedAtMixin, Base):
    __tablename__ = "manual_review_items"
    __table_args__ = (
        CheckConstraint(
            "reason in ('possible_duplicate', 'low_confidence', 'business_quality')",
            name="ck_manual_review_items_reason",
        ),
        CheckConstraint(
            "status in ('pending', 'approved', 'rejected', 'merged')",
            name="ck_manual_review_items_status",
        ),
        Index(
            "ix_manual_review_items_pending",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    review_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    announcement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("announcements.announcement_id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    related_candidate_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("announcements.announcement_id")
    )
    score_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
