-- Sprint 5 referral activation and Premium milestone idempotency.

create table if not exists referral_premium_awards (
    award_id uuid primary key default gen_random_uuid(),
    referrer_user_id bigint not null references users(user_id),
    milestone integer not null check (milestone > 0),
    created_at timestamptz not null default now(),
    unique (referrer_user_id, milestone)
);

create table if not exists referral_audit_events (
    audit_id uuid primary key default gen_random_uuid(),
    user_id bigint not null references users(user_id),
    action text not null,
    reason text not null,
    created_at timestamptz not null default now()
);

create index if not exists ix_referral_events_referred_status
    on referral_events(referred_user_id, status);

create index if not exists ix_referral_audit_events_user_created
    on referral_audit_events(user_id, created_at desc);
