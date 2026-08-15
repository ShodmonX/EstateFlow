-- Sprint 3 schema contract adjustments.
-- Evolves prior additive schema without dropping/recreating tables.
-- Rollback risk: restoring the previous not-null/default behavior for
-- announcements.is_promoted is metadata-only, but dropping source health columns
-- would lose source monitoring history. Do not drop those columns in production
-- without exporting source health/audit data first.

alter table ingestion_sources
    add column if not exists status text not null default 'active'
        check (status in ('active', 'disabled', 'degraded', 'deleted')),
    add column if not exists last_success_at timestamptz null,
    add column if not exists last_failure_at timestamptz null,
    add column if not exists failure_count integer not null default 0 check (failure_count >= 0),
    add column if not exists last_error text null,
    add column if not exists average_response_ms integer null check (average_response_ms is null or average_response_ms >= 0),
    add column if not exists deleted_at timestamptz null;

alter table announcements
    alter column is_promoted drop not null,
    alter column is_promoted drop default;

create index if not exists ix_ingestion_sources_active
    on ingestion_sources(source_type, status)
    where enabled and status = 'active';

create index if not exists ix_ingestion_sources_listener_account
    on ingestion_sources(listener_account_key)
    where listener_account_key is not null;

create index if not exists ix_announcements_latest_source
    on announcements(latest_source_id)
    where latest_source_id is not null;

create index if not exists ix_notifications_user_status_created
    on notifications(user_id, status, created_at desc);

create index if not exists ix_source_suggestions_status_created
    on source_suggestions(status, created_at);

create index if not exists ix_referral_events_referrer_status
    on referral_events(referrer_user_id, status);
