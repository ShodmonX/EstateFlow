-- Sprint 5 notification matching support.
-- The idempotency constraint for (user_id, announcement_id, filter_id) already
-- exists in 002_mvp_schema_and_post_ai_dedup.sql. This migration adds focused
-- lookup indexes for priority-aware pending delivery and filter matching.

create index if not exists ix_notifications_pending_priority_created
    on notifications(priority desc, created_at)
    where status = 'pending';

create index if not exists ix_user_filters_matching_enabled
    on user_filters(enabled, updated_at desc);
