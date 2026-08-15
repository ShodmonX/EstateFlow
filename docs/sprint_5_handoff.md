# Sprint 5 Handoff: Notifications, NLP Search, Referrals

## Notification Idempotency

- Matching creates at most one notification per `(user_id, filter_id, canonical_announcement_id)`.
- Domain idempotency key format:
  `notification:{user_id}:{filter_id}:{announcement_id}`.
- PostgreSQL enforces the same rule with
  `unique (user_id, announcement_id, filter_id)` on `notifications`.
- Delivery lifecycle is:
  `pending -> sending -> sent`, or `pending -> sending -> pending` for retryable
  failures with `next_attempt_at`, or `pending -> sending -> failed/suppressed`
  for permanent failures.
- Telegram blocked-user failures become `suppressed`; they must not retry forever.
- `notification_delivery_attempts` is the audit trail for sending, sent,
  retryable failure, permanent failure, and rate-limit outcomes.
- Premium users receive `notifications.high_priority` jobs, but rate limits are
  still enforced by the delivery worker.

## Referral Premium Policy

- Referral deep links use Telegram `/start` parameter format:
  `https://t.me/{bot_username}?start={referral_code}`.
- Referral codes are validated by `^[A-Za-z0-9_-]{3,64}$`.
- Referred user policy:
  - self-referral is rejected;
  - malformed or unknown code is rejected and audited;
  - `referred_by` is immutable after the first accepted referral;
  - duplicate `/start` replay does not create another event;
  - circular referral chains are rejected;
  - lightweight start-attempt rate limiting records audit events.
- Activation only happens after a qualifying user action:
  `search` or `saved_filter`.
- A referred user can activate exactly once. Activation updates the referral
  event and increments `users.active_referral_count` in the same repository
  transaction.
- At 5 active referrals, the referrer receives 7 days of Premium.
- Premium extension policy: if `premium_until` is in the future, extend from the
  existing expiry; otherwise extend from activation time.
- `referral_premium_awards(referrer_user_id, milestone)` prevents repeated
  Premium awards for the same milestone on retries.
- Premium award notifications are emitted through
  `ReferralNotificationPublisher.premium_awarded(...)` with high-priority
  intent.

## NLP Search Contract

- NLP is an input layer for existing `SearchCriteria`; it is not a separate
  search engine.
- LLM requests use the Sprint 2 `ModelFallbackLLMClient.complete_json(...)`
  contract with a strict Pydantic schema.
- LLM output fields:
  - `monthly_budget`
  - `price_basis_preference`
  - `districts`
  - `rooms`
  - `renovation_level`
  - `audience_tag`
  - `unapplied_conditions`
  - `confidence`
- LLM output is validated before conversion to `SearchCriteria`.
- LLM output is never passed directly to SQL, ORM query builders, callbacks, or
  authorization decisions.
- `young family`, `yosh oila`, and `молодая семья` normalize to the flat
  audience tag `family`; no audience hierarchy or implicit expansion is used.
- Unsupported requirements such as `metroga yaqin` stay visible in
  `unapplied_conditions` and are not silently promised as applied filters.
- Bot flow shows a summary before running search and offers wizard fallback on
  model failure or invalid JSON.

## Sprint 6 Service Boundaries

- Admin review can consume:
  - `ManualReviewQueueService` for pending review items and decisions;
  - `InMemoryAnnouncementRepository.list_all_for_audit()` / asyncpg announcement
    repository queries for canonical/child audit views;
  - `dedup_decisions`, `announcement_merge_audit`, and
    `manual_review_items` tables for traceability.
- Source suggestion admin workflows can consume:
  - `source_suggestions` table with status transitions;
  - `ingestion_sources` health/status columns and indexes from Sprint 3;
  - ops notification services for aggregate alerts.
- Notification/admin audit surfaces can consume:
  - `notifications`, `notification_delivery_attempts`;
  - `referral_events`, `referral_premium_awards`, `referral_audit_events`;
  - matching metrics returned from `NotificationMatchingEngine`;
  - delivery metrics returned from `NotificationDeliveryWorker`.
