from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_ROOT = PROJECT_ROOT / "migrations"

LEGACY_SQL_MIGRATIONS: tuple[Path, ...] = tuple(
    MIGRATIONS_ROOT / name
    for name in (
        "001_listener_pool.sql",
        "002_mvp_schema_and_post_ai_dedup.sql",
        "003_sprint3_schema_contract_adjustments.sql",
        "004_sprint4_search_and_saved_filters.sql",
        "005_sprint5_notification_matching.sql",
        "006_sprint5_notification_delivery.sql",
        "007_sprint5_referral_premium.sql",
        "008_sprint6_admin_tags_content.sql",
        "009_sprint7_analytics_events.sql",
        "010_sprint7_technical_metric_events.sql",
        "011_media_per_announcement.sql",
    )
)
