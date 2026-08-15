"""Backfill manual-review rows that older workers only kept in memory."""

from collections.abc import Sequence

from alembic import op

revision: str = "e7f2a1b3c4d5"
down_revision: str | Sequence[str] | None = "c4d8e7f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        insert into manual_review_items(
            announcement_id,
            reason,
            related_candidate_id,
            score_breakdown,
            status,
            created_at
        )
        select
            a.announcement_id,
            case
                when decision.decision = 'possible_duplicate' then 'possible_duplicate'
                else 'business_quality'
            end,
            decision.candidate_announcement_id,
            coalesce(decision.score_breakdown, '[]'::jsonb),
            'pending',
            a.created_at
        from announcements a
        left join lateral (
            select d.*
            from dedup_decisions d
            where d.announcement_id = a.announcement_id
            order by d.created_at desc
            limit 1
        ) decision on true
        where a.status = 'manual_review'
          and not exists (
              select 1
              from manual_review_items review
              where review.announcement_id = a.announcement_id
          )
        """
    )


def downgrade() -> None:
    # The backfilled rows are valid domain data and must not be deleted on rollback.
    pass
