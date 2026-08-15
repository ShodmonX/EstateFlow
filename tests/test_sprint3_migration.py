from __future__ import annotations

from pathlib import Path

from estateflow.db.migrations import LEGACY_SQL_MIGRATIONS
from estateflow.models import Base


def _migration(name: str) -> str:
    return Path("migrations", name).read_text(encoding="utf-8")


def test_legacy_sql_chain_now_covers_all_11_migrations() -> None:
    assert len(LEGACY_SQL_MIGRATIONS) == 11
    combined_sql = "\n".join(
        path.read_text(encoding="utf-8") for path in LEGACY_SQL_MIGRATIONS
    ).lower()

    for table in (
        "listener_accounts",
        "ingestion_sources",
        "channel_assignments",
        "channel_assignment_audit",
        "users",
        "announcements",
        "announcement_media",
        "user_filters",
        "notifications",
        "notification_delivery_attempts",
        "parsing_logs",
        "processing_jobs",
        "audience_tag_types",
        "source_suggestions",
        "referral_events",
        "referral_premium_awards",
        "referral_audit_events",
        "dedup_decisions",
        "announcement_merge_audit",
        "manual_review_items",
        "admin_audit_events",
        "content_channel_posts",
        "analytics_events",
        "technical_metric_events",
    ):
        assert f"create table if not exists {table}" in combined_sql

    assert "parent_tag_id" not in combined_sql
    assert "listing_type" not in combined_sql
    assert "drop table" not in combined_sql


def test_mvp_and_sprint3_sql_contracts_preserve_additive_migrations() -> None:
    sql_002 = _migration("002_mvp_schema_and_post_ai_dedup.sql").lower()
    sql_003 = _migration("003_sprint3_schema_contract_adjustments.sql").lower()
    sql_006 = _migration("006_sprint5_notification_delivery.sql").lower()
    sql_008 = _migration("008_sprint6_admin_tags_content.sql").lower()

    assert "price_period" in sql_002
    assert "price_basis" in sql_002
    assert "price_normalized_monthly" in sql_002
    assert "audience_tag_types" in sql_002
    assert "parent_tag_id" not in sql_002
    assert "young_family" not in sql_002
    assert "parent_announcement_id" in sql_002
    assert "is_promoted" in sql_002
    assert "promoted_until" in sql_002
    assert "drop table" not in sql_002

    assert "alter table ingestion_sources" in sql_003
    assert "add column if not exists last_success_at" in sql_003
    assert "alter table announcements" in sql_003
    assert "alter column is_promoted drop not null" in sql_003
    assert "alter column is_promoted drop default" in sql_003
    assert "drop table" not in sql_003
    assert "drop column" not in sql_003

    assert "notification_delivery_attempts" in sql_006
    assert "status in ('pending', 'sending', 'sent', 'failed', 'suppressed')" in sql_006

    assert "admin_audit_events" in sql_008
    assert "content_channel_posts" in sql_008
    assert "ix_audience_tag_types_pending" in sql_008


def test_orm_metadata_matches_current_schema_contract_without_audience_hierarchy() -> None:
    required_tables = {
        "listener_accounts",
        "ingestion_sources",
        "channel_assignments",
        "channel_assignment_audit",
        "users",
        "announcements",
        "announcement_media",
        "user_filters",
        "notifications",
        "notification_delivery_attempts",
        "parsing_logs",
        "processing_jobs",
        "audience_tag_types",
        "source_suggestions",
        "referral_events",
        "referral_premium_awards",
        "referral_audit_events",
        "dedup_decisions",
        "announcement_merge_audit",
        "manual_review_items",
        "admin_audit_events",
        "content_channel_posts",
        "analytics_events",
        "technical_metric_events",
    }
    assert required_tables.issubset(set(Base.metadata.tables))

    announcements = Base.metadata.tables["announcements"]
    for column in (
        "price_period",
        "price_basis",
        "price_normalized_monthly",
        "audience_tags",
        "audience_excluded_tags",
        "is_promoted",
        "promoted_until",
    ):
        assert column in announcements.columns

    notifications = Base.metadata.tables["notifications"]
    for column in (
        "attempts",
        "last_attempt_at",
        "next_attempt_at",
        "telegram_message_id",
        "delivered_at",
    ):
        assert column in notifications.columns

    source_suggestions = Base.metadata.tables["source_suggestions"]
    for column in ("source_type", "reward_granted", "source_id", "decided_at"):
        assert column in source_suggestions.columns

    audience_tags = Base.metadata.tables["audience_tag_types"]
    assert "parent_tag_id" not in audience_tags.columns
