"""Add versioned source parser policy metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1c4e7f9b2d6"
down_revision: str | Sequence[str] | None = "f8a3b2c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TECHNICAL_METRICS_WITH_SOURCE_PARSER = (
    "'queue_delay_ms', 'ai_fallback_attempt', 'ai_validation_failure', "
    "'dedup_decision', 'notification_retry', 'notification_error', "
    "'listener_health', 'source_parser_decision'"
)

_TECHNICAL_METRICS_BEFORE_SOURCE_PARSER = (
    "'queue_delay_ms', 'ai_fallback_attempt', 'ai_validation_failure', "
    "'dedup_decision', 'notification_retry', 'notification_error', 'listener_health'"
)


def upgrade() -> None:
    op.add_column("ingestion_sources", sa.Column("parser_key", sa.Text(), nullable=True))
    op.add_column("ingestion_sources", sa.Column("parser_version", sa.Text(), nullable=True))
    op.add_column(
        "ingestion_sources",
        sa.Column(
            "parser_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "ingestion_sources",
        sa.Column(
            "parser_mode",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_ingestion_sources_parser_key_nonblank",
        "ingestion_sources",
        "parser_key is null or btrim(parser_key) <> ''",
    )
    op.create_check_constraint(
        "ck_ingestion_sources_parser_version_nonblank",
        "ingestion_sources",
        "parser_version is null or btrim(parser_version) <> ''",
    )
    op.create_check_constraint(
        "ck_ingestion_sources_parser_config_object",
        "ingestion_sources",
        "jsonb_typeof(parser_config) = 'object'",
    )
    op.create_check_constraint(
        "ck_ingestion_sources_parser_mode",
        "ingestion_sources",
        "parser_mode in ('active', 'shadow', 'disabled')",
    )
    op.execute(
        """
        update ingestion_sources
        set parser_key = case
                when lower(btrim(coalesce(source_profile, 'default'))) in (
                    'caption_first', 'album_text_merge'
                ) then 'generic.album_caption'
                else 'generic.single_listing'
            end,
            parser_version = '1'
        where parser_key is null
        """
    )

    op.drop_constraint(
        "ck_technical_metric_events_metric_name",
        "technical_metric_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_technical_metric_events_metric_name",
        "technical_metric_events",
        f"metric_name in ({_TECHNICAL_METRICS_WITH_SOURCE_PARSER})",
    )


def downgrade() -> None:
    op.execute("delete from technical_metric_events where metric_name = 'source_parser_decision'")
    op.drop_constraint(
        "ck_technical_metric_events_metric_name",
        "technical_metric_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_technical_metric_events_metric_name",
        "technical_metric_events",
        f"metric_name in ({_TECHNICAL_METRICS_BEFORE_SOURCE_PARSER})",
    )

    op.drop_constraint(
        "ck_ingestion_sources_parser_mode", "ingestion_sources", type_="check"
    )
    op.drop_constraint(
        "ck_ingestion_sources_parser_config_object",
        "ingestion_sources",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingestion_sources_parser_version_nonblank",
        "ingestion_sources",
        type_="check",
    )
    op.drop_constraint(
        "ck_ingestion_sources_parser_key_nonblank",
        "ingestion_sources",
        type_="check",
    )
    op.drop_column("ingestion_sources", "parser_mode")
    op.drop_column("ingestion_sources", "parser_config")
    op.drop_column("ingestion_sources", "parser_version")
    op.drop_column("ingestion_sources", "parser_key")
