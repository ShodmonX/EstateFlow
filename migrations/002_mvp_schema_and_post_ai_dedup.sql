-- Sprint 3 MVP schema finalization and post-AI dedup persistence.
-- Rollback risk: downgrade can drop newly written Sprint 3 data. Do not run
-- downgrade in production without exporting/archiving affected tables first.

create extension if not exists pgcrypto;

create table if not exists users (
    user_id bigint primary key,
    telegram_username text null,
    referral_code text not null unique,
    referred_by bigint null references users(user_id),
    premium_until timestamptz null,
    active_referral_count integer not null default 0 check (active_referral_count >= 0),
    status text not null default 'active' check (status in ('active', 'disabled', 'deleted')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz null
);

create table if not exists announcements (
    announcement_id uuid primary key default gen_random_uuid(),
    idempotency_key text not null unique,
    source_id text not null references ingestion_sources(source_id),
    source_channel_id text null,
    source_message_id text null,
    source_message_ids text[] not null default '{}',
    parent_announcement_id uuid null references announcements(announcement_id),
    status text not null default 'active'
        check (status in ('active', 'archived', 'manual_review', 'deleted')),
    canonical_rank integer not null default 0,
    source_count integer not null default 1 check (source_count >= 1),
    latest_source_id text null references ingestion_sources(source_id),
    source_url text null,
    forward_origin_key text null,
    price numeric(14,2) null,
    currency text null,
    price_period text not null default 'monthly'
        check (price_period in ('daily', 'monthly', 'one_time')),
    price_basis text not null default 'total'
        check (price_basis in ('total', 'per_person')),
    price_normalized_monthly numeric(14,2) null,
    rooms integer null check (rooms >= 0),
    area_sqm numeric(10,2) null check (area_sqm >= 0),
    floor integer null,
    total_floors integer null,
    district text null,
    address text null,
    phone_numbers text[] not null default '{}',
    owner_type text not null default 'unknown'
        check (owner_type in ('owner', 'agent', 'unknown')),
    renovation_level text null check (renovation_level in ('none', 'basic', 'good', 'euro', 'luxury')),
    renovation_source text not null default 'unknown'
        check (renovation_source in ('vision', 'text', 'unknown')),
    furniture boolean null,
    description text not null default '',
    audience_tags text[] not null default '{}',
    audience_excluded_tags text[] not null default '{}',
    confidence numeric(4,3) null check (confidence is null or (confidence >= 0 and confidence <= 1)),
    field_confidence jsonb not null default '{}'::jsonb,
    assumptions text[] not null default '{}',
    prompt_version text null,
    is_promoted boolean not null default false,
    promoted_until timestamptz null,
    occurred_at timestamptz not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz null,
    check (parent_announcement_id is null or parent_announcement_id <> announcement_id)
);

create unique index if not exists uq_announcements_one_parent_per_source_message
    on announcements(source_id, source_channel_id, source_message_id)
    where status <> 'deleted';

create index if not exists ix_announcements_user_search_canonical
    on announcements(status, created_at desc)
    where parent_announcement_id is null and status = 'active';

create index if not exists ix_announcements_parent
    on announcements(parent_announcement_id)
    where parent_announcement_id is not null;

create index if not exists ix_announcements_dedup_candidates
    on announcements(district, rooms, occurred_at desc)
    where parent_announcement_id is null and status = 'active';

create index if not exists ix_announcements_price_search
    on announcements(price_period, price_basis, price_normalized_monthly)
    where parent_announcement_id is null and status = 'active';

create index if not exists ix_announcements_audience_tags_gin
    on announcements using gin(audience_tags);

create table if not exists announcement_media (
    media_id text primary key,
    announcement_id uuid not null references announcements(announcement_id) on delete restrict,
    storage_url text not null,
    object_key text not null unique,
    mime_type text not null,
    size_bytes integer not null check (size_bytes >= 0),
    phash text null,
    content_sha256 text not null,
    width integer null,
    height integer null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists ix_announcement_media_announcement
    on announcement_media(announcement_id);

create index if not exists ix_announcement_media_phash
    on announcement_media(phash)
    where phash is not null;

create table if not exists user_filters (
    filter_id uuid primary key default gen_random_uuid(),
    user_id bigint not null references users(user_id),
    name text not null,
    filters jsonb not null,
    enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists ix_user_filters_user_enabled
    on user_filters(user_id)
    where enabled;

create table if not exists notifications (
    notification_id uuid primary key default gen_random_uuid(),
    user_id bigint not null references users(user_id),
    announcement_id uuid not null references announcements(announcement_id),
    filter_id uuid null references user_filters(filter_id),
    status text not null default 'pending'
        check (status in ('pending', 'sent', 'failed', 'suppressed')),
    priority integer not null default 0,
    sent_at timestamptz null,
    error_reason text null,
    created_at timestamptz not null default now(),
    unique (user_id, announcement_id, filter_id)
);

create table if not exists parsing_logs (
    idempotency_key text primary key,
    status text not null check (status in ('succeeded', 'manual_review', 'failed')),
    correlation_id text not null,
    canonical jsonb null,
    media jsonb not null default '[]'::jsonb,
    attempts jsonb not null default '[]'::jsonb,
    error_type text null,
    failure_reason text null,
    post_ai_published boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists processing_jobs (
    job_id uuid primary key default gen_random_uuid(),
    idempotency_key text not null,
    queue_name text not null,
    status text not null default 'pending'
        check (status in ('pending', 'processing', 'succeeded', 'failed', 'dead_letter')),
    attempts integer not null default 0 check (attempts >= 0),
    correlation_id text not null,
    payload jsonb not null default '{}'::jsonb,
    last_error text null,
    available_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (queue_name, idempotency_key)
);

create index if not exists ix_processing_jobs_ready
    on processing_jobs(queue_name, status, available_at);

create table if not exists audience_tag_types (
    id uuid primary key default gen_random_uuid(),
    tag_key text not null unique,
    display_name_uz text not null,
    status text not null default 'approved' check (status in ('approved', 'pending', 'rejected')),
    usage_count integer not null default 0 check (usage_count >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

insert into audience_tag_types(tag_key, display_name_uz, status)
values
    ('family', 'Oila', 'approved'),
    ('family_with_children', 'Bolali oila', 'approved'),
    ('students', 'Talabalar', 'approved'),
    ('single_male', 'Yolg''iz erkak', 'approved'),
    ('single_female', 'Yolg''iz ayol', 'approved'),
    ('group_of_girls', 'Qizlar guruhi', 'approved'),
    ('group_of_boys', 'Yigitlar guruhi', 'approved'),
    ('foreigners', 'Chet elliklar', 'approved')
on conflict (tag_key) do nothing;

create table if not exists source_suggestions (
    suggestion_id uuid primary key default gen_random_uuid(),
    user_id bigint null references users(user_id),
    source_identifier text not null,
    status text not null default 'pending'
        check (status in ('pending', 'approved', 'rejected')),
    admin_note text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source_identifier)
);

create table if not exists referral_events (
    referral_event_id uuid primary key default gen_random_uuid(),
    referrer_user_id bigint not null references users(user_id),
    referred_user_id bigint not null references users(user_id),
    activation_event_key text not null,
    status text not null default 'pending'
        check (status in ('pending', 'active', 'rejected')),
    created_at timestamptz not null default now(),
    activated_at timestamptz null,
    unique (referred_user_id),
    unique (activation_event_key)
);

create table if not exists dedup_decisions (
    decision_id uuid primary key default gen_random_uuid(),
    announcement_id uuid not null references announcements(announcement_id),
    candidate_announcement_id uuid null references announcements(announcement_id),
    decision text not null
        check (decision in ('exact_duplicate', 'high_confidence_duplicate', 'possible_duplicate', 'new')),
    score integer not null check (score >= 0),
    threshold integer not null check (threshold >= 0),
    config_version text not null,
    score_breakdown jsonb not null,
    created_at timestamptz not null default now()
);

create index if not exists ix_dedup_decisions_announcement
    on dedup_decisions(announcement_id, created_at desc);

create table if not exists announcement_merge_audit (
    audit_id uuid primary key default gen_random_uuid(),
    parent_announcement_id uuid not null references announcements(announcement_id),
    child_announcement_id uuid not null references announcements(announcement_id),
    decision_id uuid null references dedup_decisions(decision_id),
    merge_policy text not null,
    conflicts jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    unique (child_announcement_id)
);

create table if not exists manual_review_items (
    review_id uuid primary key default gen_random_uuid(),
    announcement_id uuid not null references announcements(announcement_id),
    reason text not null check (reason in ('possible_duplicate', 'low_confidence', 'business_quality')),
    related_candidate_id uuid null references announcements(announcement_id),
    score_breakdown jsonb not null default '[]'::jsonb,
    status text not null default 'pending'
        check (status in ('pending', 'approved', 'rejected', 'merged')),
    created_at timestamptz not null default now(),
    decided_at timestamptz null
);

create index if not exists ix_manual_review_items_pending
    on manual_review_items(created_at)
    where status = 'pending';
