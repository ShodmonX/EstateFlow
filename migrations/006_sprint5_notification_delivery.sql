-- Sprint 5 notification delivery lifecycle and audit trail.

alter table notifications
    drop constraint if exists notifications_status_check;

alter table notifications
    add constraint notifications_status_check
    check (status in ('pending', 'sending', 'sent', 'failed', 'suppressed'));

alter table notifications
    add column if not exists attempts integer not null default 0 check (attempts >= 0),
    add column if not exists last_attempt_at timestamptz null,
    add column if not exists next_attempt_at timestamptz null,
    add column if not exists telegram_message_id text null,
    add column if not exists delivered_at timestamptz null;

create table if not exists notification_delivery_attempts (
    attempt_id uuid primary key default gen_random_uuid(),
    notification_id uuid not null references notifications(notification_id) on delete restrict,
    attempt_no integer not null check (attempt_no >= 0),
    result text not null
        check (
            result in (
                'sending',
                'sent',
                'retryable_failure',
                'permanent_failure',
                'rate_limited',
                'skipped'
            )
        ),
    error_type text null,
    retryable boolean not null default false,
    telegram_message_id text null,
    created_at timestamptz not null default now()
);

create index if not exists ix_notifications_ready_delivery
    on notifications(status, next_attempt_at, priority desc, created_at asc)
    where status = 'pending';

create index if not exists ix_notification_delivery_attempts_notification
    on notification_delivery_attempts(notification_id, created_at desc);
