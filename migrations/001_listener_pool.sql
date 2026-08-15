-- Sprint 1 listener account pool and channel assignment foundation.
-- This migration intentionally stores account metadata only.

create table if not exists listener_accounts (
    account_key text primary key,
    enabled boolean not null default true,
    health_status text not null default 'online'
        check (health_status in ('online', 'reconnecting', 'flood_wait', 'disabled', 'banned')),
    last_successful_event_at timestamptz null,
    flood_wait_count integer not null default 0 check (flood_wait_count >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists ingestion_sources (
    source_id text primary key,
    name text not null,
    source_type text not null
        check (source_type in ('telegram_channel', 'telegram_group', 'website')),
    identifier text not null,
    enabled boolean not null default true,
    adapter_name text null,
    listener_account_key text null references listener_accounts(account_key),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists channel_assignments (
    id bigserial primary key,
    source_id text not null references ingestion_sources(source_id),
    account_key text not null references listener_accounts(account_key),
    assigned_at timestamptz not null default now(),
    active boolean not null default true,
    released_at timestamptz null,
    reason text not null
        check (reason in ('initial', 'rebalance', 'failover', 'source_disabled'))
);

create unique index if not exists uq_channel_assignments_one_active_source
    on channel_assignments(source_id)
    where active;

create index if not exists ix_channel_assignments_active_account
    on channel_assignments(account_key)
    where active;

create table if not exists channel_assignment_audit (
    id bigserial primary key,
    source_id text not null,
    previous_account_key text null,
    new_account_key text null,
    reason text not null
        check (reason in ('initial', 'rebalance', 'failover', 'source_disabled')),
    occurred_at timestamptz not null default now()
);
