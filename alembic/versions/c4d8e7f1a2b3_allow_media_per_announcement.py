# ruff: noqa
"""Allow the same Telegram media to belong to multiple announcements."""

from typing import Sequence, Union

from alembic import op

revision: str = "c4d8e7f1a2b3"
down_revision: Union[str, Sequence[str], None] = "9b77c0b3d5a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("alter table announcement_media drop constraint if exists announcement_media_pkey")
    op.execute(
        "alter table announcement_media "
        "add constraint pk_announcement_media primary key (announcement_id, media_id)"
    )


def downgrade() -> None:
    op.execute("alter table announcement_media drop constraint if exists pk_announcement_media")
    op.execute(
        "alter table announcement_media "
        "add constraint announcement_media_pkey primary key (media_id)"
    )
