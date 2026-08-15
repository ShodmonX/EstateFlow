"""Make approved manual-review announcements visible to user-facing search."""

from collections.abc import Sequence

from alembic import op

revision: str = "f8a3b2c4d5e6"
down_revision: str | Sequence[str] | None = "e7f2a1b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        update announcements announcement
        set status = 'active', updated_at = now()
        from manual_review_items review
        where review.announcement_id = announcement.announcement_id
          and review.status = 'approved'
          and announcement.status = 'manual_review'
        """
    )


def downgrade() -> None:
    # Approved announcements are valid user-facing data and must stay active.
    pass
