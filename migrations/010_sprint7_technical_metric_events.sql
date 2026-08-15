-- Sprint 7 technical observability: immutable operational metric events.
create table if not exists technical_metric_events (
    technical_metric_event_id uuid primary key default gen_random_uuid(),
    metric_name text not null check (
        metric_name in (
            'queue_delay_ms',
            'ai_fallback_attempt',
            'ai_validation_failure',
            'dedup_decision',
            'notification_retry',
            'notification_error',
            'listener_health'
        )
    ),
    idempotency_key text not null unique,
    occurred_at timestamptz not null default now(),
    value double precision not null default 1,
    component text null,
    subject_id text null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists ix_technical_metric_events_name_time
    on technical_metric_events(metric_name, occurred_at);

create index if not exists ix_technical_metric_events_component_time
    on technical_metric_events(component, occurred_at)
    where component is not null;

create index if not exists ix_technical_metric_events_subject_time
    on technical_metric_events(subject_id, occurred_at)
    where subject_id is not null;
