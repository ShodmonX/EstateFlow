# ruff: noqa
from __future__ import annotations

"""Add source_profile to ingestion_sources.

This is an additive, non-destructive migration so Telegram source parsing
profiles can persist across restarts and worker refreshes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "9b77c0b3d5a1"
down_revision: Union[str, Sequence[str], None] = "5fdae000458c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "ingestion_sources",
        sa.Column("source_profile", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("ingestion_sources", "source_profile")
