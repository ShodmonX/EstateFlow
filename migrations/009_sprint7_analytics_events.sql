-- Sprint 7 beta metrics: immutable product analytics events.
create table if not exists analytics_events (
    analytics_event_id uuid primary key default gen_random_uuid(),
    event_name text not null check (
        event_name in (
            'user_registered',
            'search_completed',
            'saved_filter_created',
            'referral_accepted',
            'referral_activated',
            'notification_sent',
            'notification_delivery_failed',
            'notification_action_open',
            'notification_action_search',
            'notification_action_read'
        )
    ),
    idempotency_key text not null unique,
    occurred_at timestamptz not null default now(),
    user_id bigint null,
    subject_id text null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists ix_analytics_events_name_time
    on analytics_events(event_name, occurred_at);

create index if not exists ix_analytics_events_user_time
    on analytics_events(user_id, occurred_at)
    where user_id is not null;

create index if not exists ix_analytics_events_subject_time
    on analytics_events(subject_id, occurred_at)
    where subject_id is not null;
