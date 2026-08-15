# Sprint 7 Beta Metrics and Soft-Launch Handoff

Status: dry-run ready, no production launch performed.

## Scope Guardrails

- Do not send messages to real Telegram accounts, real users, or production channels without explicit approval.
- Do not deploy or mutate beta cohorts from this handoff.
- Use fake Telegram/OpenRouter/R2/publisher adapters for local E2E and dry-run checks.
- Changelog decisions override v3 and base docs: flat audience tags, conservative phone-only dedup, R2 media references, and renovation may be derived from validated text or Vision when images exist.

## Beta Metric Contract

All product metrics use immutable `analytics_events` rows with a unique `idempotency_key`. Timestamps are stored in UTC. Report windows are converted from the requested local timezone before querying. Supported reporting timezones are `Asia/Tashkent` and `UTC`.

### Activation Rate

- Event names: `user_registered`, `search_completed`, `saved_filter_created`.
- Trigger: registration/touch of a new bot user, completed search, and saved filter creation.
- Unique identifier: event-level `idempotency_key`; user-level distinct key is `user_id`.
- Denominator: distinct users with `user_registered` in the report window.
- Numerator: denominator users with at least one `search_completed` or `saved_filter_created` event after registration and before report window end.
- Cohort and timezone: registration cohort in `[start local date 00:00, end local date 00:00)`, converted to UTC using the requested timezone.
- Delay policy: activation can update until the report window closes.
- Dedup policy: repeated search/filter events are collapsed by user.
- Missing data: users without `user_registered` are excluded from the cohort; repeated search/filter events are deduped by user.

### D7 Retention

- Event names: `user_registered` plus any non-registration analytics event.
- Trigger: registration and later explicit user/product activity.
- Unique identifier: event-level `idempotency_key`; user-level distinct key is `user_id`.
- Denominator: distinct users registered in the cohort window.
- Numerator: denominator users with a return event in `[registered_at + 7 days, registered_at + 7 days + d7_window_hours)`.
- Cohort and timezone: registration cohort in the requested local timezone; default D7 window is 24 hours.
- Delay policy: D7 is incomplete until seven days plus the configured lookup window has elapsed.
- Dedup policy: one qualifying return event per user.
- Missing data: late-arriving events are absent until the D7 lookup window has elapsed; users without registration are excluded.

### Referral Conversion

- Event names: `referral_accepted`, `referral_activated`.
- Trigger: invitee starts through referral link; invitee later completes first qualifying search/filter action.
- Unique identifier: event-level `idempotency_key`; invitee distinct key is `user_id`; referrer is carried as non-PII `subject_id`.
- Denominator: distinct invited users with `referral_accepted` in the report window.
- Numerator: denominator users with `referral_activated` in the same report window.
- Cohort and timezone: accepted referral events in the requested local report window.
- Delay policy: conversion can update until the report window closes.
- Dedup policy: duplicate accepts/activations are collapsed by invitee user.
- Missing data: referral starts without accepted referral events do not enter denominator; duplicate activations are collapsed by user and idempotency key.

### Notification Engagement

- Event names: `notification_sent`, `notification_action_open`, `notification_action_search`, `notification_action_read`.
- Trigger: successful fake/real adapter send; explicit bot callback actions. Do not infer Telegram read receipts.
- Unique identifier: event-level `idempotency_key`; notification distinct key is `subject_id`.
- Denominator: distinct notification IDs with `notification_sent` in the report window.
- Numerator: denominator notification IDs with at least one explicit action/read event in the report window.
- Cohort and timezone: notification sent events in the requested local report window.
- Delay policy: engagement can update until the report window closes.
- Dedup policy: repeated action/read events are collapsed by notification ID.
- Missing data: Telegram read receipts are not assumed. `notification_sent` is delivery, not engagement; only bot-recorded callback/read actions count.

## Privacy and Retention

- Product analytics stores numeric `user_id`, notification/listing/referral identifiers, event name, UTC timestamp, idempotency key, and small string metadata only.
- No Telegram usernames, phone numbers, message text, media URLs, private channel names, or raw query text are required for metric calculation.
- Metadata must stay redacted and operational: error classes, queue names, action names, status labels, and synthetic IDs are allowed.
- Recommended beta retention: keep immutable raw analytics and technical metric events for 90 days, then retain only aggregate daily/hourly counts needed for trend comparison.

## Query Location

Protected endpoint:

```text
GET /admin/metrics/beta?start=2026-08-01&end=2026-08-08&timezone_name=Asia/Tashkent&d7_window_hours=24
Header: x-admin-token: <ADMIN_API_TOKEN>
```

Application state must provide `ProductMetricsService`. PostgreSQL persistence uses `009_sprint7_analytics_events.sql` with `AsyncpgAnalyticsEventRepository` and `010_sprint7_technical_metric_events.sql` with `AsyncpgTechnicalMetricRepository`.

The response includes product ratios, daily product event buckets, and optional technical metric snapshot. Hourly product buckets are available in `event_buckets(..., granularity="hourly")` for dashboard drilldown.

## Instrumented Events

- User registration: `UserService.register_or_touch`.
- Search completed: `SearchWizard.run` and `SearchWizard.run_criteria`.
- Saved filter created: `SavedFilterService.create`.
- Referral accepted/activated: `ReferralService`.
- Notification sent and delivery failure: `NotificationDeliveryWorker`.
- Notification action/read events: `BotController` callback handler records explicit `notification_action_*`; read is only recorded when the bot receives a read action callback.

## Technical Observability

Technical metrics use separate immutable `technical_metric_events` rows so product metrics are not overloaded with operational health signals.

- Queue delay: `queue_delay_ms`, component `pre_ai_ingestion`, value in milliseconds from raw event occurrence to AI queue publish.
- AI fallback and validation failure: `ai_fallback_attempt` and `ai_validation_failure`, component `ai_worker`, with model attempt status/error class metadata only.
- Dedup rate: `dedup_decision`, component `post_ai_dedup`, metadata decision/score/candidate ID.
- Notification retry/error: `notification_retry` and `notification_error`, component `notification_delivery`, from retryable/rate-limited and permanent failures.
- Listener health: `listener_health`, component `listener_pool`, latest status per listener account key.

Analytics recording is best-effort. Recorder failures must not break the main user flow.

## Test Matrix

Current automated suite covers:

- Ingestion/listener pool, media buffering, pre-AI dedup, AI extraction, media/R2 abstraction, post-AI dedup.
- Search, saved filters, NLP fallback, notification matching/delivery, referral premium, admin review/tags/content.
- Sprint 7 metrics: activation denominator, idempotency, D7 boundary, referral conversion, notification engagement, timezone boundary, product buckets, technical snapshot, admin auth, and best-effort recorder failure.

Local commands:

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src
.venv\Scripts\python.exe -m pytest
```

## Soft-Launch Dry-Run Checklist

1. Apply migrations through `010_sprint7_technical_metric_events.sql` in a test database.
2. Validate `.env` without exposing secrets: `ENVIRONMENT`, DB, Redis, admin token, bot tokens, OpenRouter, R2, and content channel settings.
3. Keep dangerous features default-off until owner approval: real listener source enablement, AI processing, notification delivery, NLP, and content publishing.
4. Confirm `/health/live` and `/health/ready` against test dependencies.
5. Run fake listener/account E2E: duplicate skip, phone-only non-duplicate, post-AI canonical parent, parent-only search.
6. Run fake bot E2E: register, search, saved filter, notification idempotency, NLP fallback, referral premium.
7. Query beta metrics endpoint with seeded fake events and verify timezone/date boundaries.
8. Review queue/DLQ, listener health, notification retry/error, AI fallback/failure, dedup rate, and logs for redaction.
9. Confirm content cap is `1-2` posts/day and do not publish to a real channel.
10. Prepare rollback: disable listener, AI, notification, NLP, and content flags; pause workers; preserve queues; notify incident owner.

## Exit Criteria Targets

These are validation thresholds, not current results:

- Notification engagement: 20%+.
- D7 retention: 30%+.
- Referral conversion: visible organic growth signal from invited active users.

## Known Limitations

- Technical metrics are minimal beta decision signals, not a replacement for logs/traces/alerts.
- D7 retention should be treated as incomplete until the configured D7 lookup window has elapsed for the full cohort.
- No real beta users were added and no production messages/deployments were performed during this Sprint 7 dry run.
