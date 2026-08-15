-- Sprint 6 admin, flat dynamic audience tags, and content automation hardening.
-- Additive/idempotent migration. Audience tags remain flat.

alter table source_suggestions
    add column if not exists source_type text not null default 'telegram_channel'
        check (source_type in ('telegram_channel', 'telegram_group')),
    add column if not exists reward_granted boolean not null default false,
    add column if not exists source_id text null references ingestion_sources(source_id),
    add column if not exists decided_at timestamptz null;

create table if not exists admin_audit_events (
    audit_id uuid primary key default gen_random_uuid(),
    admin_user_id bigint not null,
    action text not null,
    entity_type text not null,
    entity_id text not null,
    idempotency_key text not null unique,
    before_state jsonb null,
    after_state jsonb null,
    note text null,
    created_at timestamptz not null default now()
);

create table if not exists content_channel_posts (
    post_id uuid primary key default gen_random_uuid(),
    post_kind text not null check (
        post_kind in ('daily_stats', 'top_offer', 'district_stats', 'transparency_report')
    ),
    idempotency_key text not null unique,
    text text not null,
    status text not null check (status in ('drafted', 'published', 'dry_run', 'failed')),
    publisher_message_id text null,
    published_at timestamptz null,
    attempts integer not null default 0,
    next_attempt_at timestamptz null,
    error_reason text null,
    schedule_timezone text not null default 'Asia/Tashkent',
    created_at timestamptz not null default now()
);

create index if not exists ix_admin_audit_events_entity_created
    on admin_audit_events(entity_type, entity_id, created_at desc);

create index if not exists ix_content_channel_posts_created
    on content_channel_posts(created_at desc);

create index if not exists ix_content_channel_posts_retry
    on content_channel_posts(next_attempt_at)
    where status = 'failed';

create index if not exists ix_audience_tag_types_pending
    on audience_tag_types(created_at)
    where status = 'pending';

create index if not exists ix_audience_tag_types_approved_usage
    on audience_tag_types(usage_count desc, tag_key)
    where status = 'approved';

create index if not exists ix_source_suggestions_pending
    on source_suggestions(created_at)
    where status = 'pending';
