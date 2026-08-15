# EstateFlow Database Migration Audit and Safe Plan

Status: draft for review. No schema reset, no production DDL, no history rewrite.

## Decision Priority

1. `real_estate_changelog.md`
2. `real_estate_v3.md`
3. `real_estate_sprint_plan.md`
4. older prompts and notes

## Scope

This audit covers:

- legacy SQL migrations in `migrations/001...010_*.sql`
- database contracts encoded in `src/estateflow/repositories/*.py`
- database contracts encoded in `src/estateflow/services/*.py`
- test fixtures and bootstrap in `tests/conftest.py` and `tests/fixtures.py`
- local deploy and launch runbooks in `docker-compose.yml`, `Dockerfile`,
  `README.md`, `docs/production_launch/*`, `scripts/migration_smoke.py`,
  `scripts/migration_smoke.psql`, `scripts/schema_bridge_check.py`,
  `scripts/sprint7_release_dry_run.py`

## Current State Summary

- Legacy SQL still creates the full durable schema for Sprint 1 through Sprint 7.
- `src/estateflow/models/` now contains SQLAlchemy 2.0 typed ORM models.
- Runtime repositories are still mostly `asyncpg` based and must be migrated
  carefully to async SQLAlchemy sessions.
- `create_app()` wires `release_controls` with `InMemoryFeatureFlagStore`, so
  feature flag changes are process-local and are lost after restart.
- Default `GET /search/announcements` returns `503` when `search_service` is not
  attached to `app.state`.
- `POST /admin/source-suggestions` still accepts arbitrary `user_id` from the
  request body.
- Legacy smoke scripts still seed `announcements.is_promoted` with `null`.

## Inventory

### Legacy SQL files

| File | Purpose |
|---|---|
| `migrations/001_listener_pool.sql` | Listener accounts, source assignment, assignment audit |
| `migrations/002_mvp_schema_and_post_ai_dedup.sql` | Core MVP tables, dedup tables, seed audience tags |
| `migrations/003_sprint3_schema_contract_adjustments.sql` | Source health columns, `is_promoted` nullability change, new indexes |
| `migrations/004_sprint4_search_and_saved_filters.sql` | Search and saved-filter indexes |
| `migrations/005_sprint5_notification_matching.sql` | Notification matching indexes |
| `migrations/006_sprint5_notification_delivery.sql` | Notification delivery lifecycle and attempt log |
| `migrations/007_sprint5_referral_premium.sql` | Referral premium awards and referral audit |
| `migrations/008_sprint6_admin_tags_content.sql` | Source suggestion extensions, admin audit, content posts, tag indexes |
| `migrations/009_sprint7_analytics_events.sql` | Product analytics events |
| `migrations/010_sprint7_technical_metric_events.sql` | Technical metric events |

### Repository contracts

| File | Tables / contracts used |
|---|---|
| `src/estateflow/repositories/analytics.py` | `analytics_events`, `technical_metric_events` |
| `src/estateflow/repositories/audience_tags.py` | `audience_tag_types`, `announcements`, `user_filters` |
| `src/estateflow/repositories/content_automation.py` | `content_channel_posts` |
| `src/estateflow/repositories/listener_pool.py` | `listener_accounts`, `ingestion_sources`, `channel_assignments`, `channel_assignment_audit` |
| `src/estateflow/repositories/notifications.py` | `notifications`, `notification_delivery_attempts`, `user_filters`, `announcements`, `announcement_media` |
| `src/estateflow/repositories/post_ai_dedup.py` | `announcements`, `announcement_media`, `dedup_decisions`, `announcement_merge_audit`, `manual_review_items` |
| `src/estateflow/repositories/saved_filters.py` | `user_filters` |
| `src/estateflow/repositories/search.py` | `announcements`, `announcement_media` |
| `src/estateflow/repositories/source_suggestions.py` | `source_suggestions`, `users`, `admin_audit_events`, `ingestion_sources` |
| `src/estateflow/repositories/users.py` | `users`, `referral_events`, `referral_premium_awards`, `referral_audit_events` |

### Service contracts that encode DB assumptions

| File | Contract note |
|---|---|
| `src/estateflow/services/release_controls.py` | In-memory feature flags; restart drops state |
| `src/estateflow/services/search.py` | Monthly rent search excludes `one_time` and parent rows |
| `src/estateflow/services/source_suggestions.py` | Suggestion submit path currently depends on caller-supplied `user_id` |
| `src/estateflow/services/notifications.py` | Delivery jobs depend on `notifications` lifecycle columns and attempt log |
| `src/estateflow/services/post_ai_dedup.py` | Dedup engine depends on parent-child and manual review tables |
| `src/estateflow/services/audience_tags.py` | Flat tag contract; no `parent_tag_id` in current schema |
| `src/estateflow/services/content_automation.py` | Content posts must support dry-run and scheduled publish lifecycle |
| `src/estateflow/services/analytics.py` | Product and technical metrics are persisted separately |

### Test fixtures and bootstrap

- `tests/conftest.py`
  - sets `ENVIRONMENT=test`
  - injects fake admin token
  - clears Telegram, OpenRouter, and R2 secrets
  - blocks external network by default
  - registers markers and flow mapping
- `tests/fixtures.py`
  - provides `FROZEN_NOW`
  - provides representative Telegram posts
  - provides canonical listing fixtures for search and dedup tests

### Docker and deployment runbook

- `docker-compose.yml`
  - postgres, redis, and api
  - no dedicated migration job
- `Dockerfile`
  - application container only
- `README.md`
  - local development pointers
- `docs/production_launch/00_index.md`
  - launch runbook index
- `docs/production_launch/04_data_and_migrations.md`
  - backup, restore, migration safety
- `docs/production_launch/08_production_cutover.md`
  - final cutover checklist
- `scripts/migration_smoke.py`
  - temporary SQL smoke helper
- `scripts/migration_smoke.psql`
  - legacy partial SQL smoke helper
- `scripts/schema_bridge_check.py`
  - schema inspection helper for legacy bridge decisions
- `scripts/sprint7_release_dry_run.py`
  - local fake-stack release evidence generator

## Legacy SQL Inventory

### `migrations/001_listener_pool.sql`

Creates:

- `listener_accounts`
  - columns: `account_key`, `enabled`, `health_status`, `last_successful_event_at`,
    `flood_wait_count`, `created_at`, `updated_at`
  - primary key: `account_key`
  - checks: `health_status` in `online/reconnecting/flood_wait/disabled/banned`,
    `flood_wait_count >= 0`
- `ingestion_sources`
  - columns: `source_id`, `name`, `source_type`, `identifier`, `enabled`,
    `adapter_name`, `listener_account_key`, `created_at`, `updated_at`
  - primary key: `source_id`
  - foreign key: `listener_account_key -> listener_accounts.account_key`
  - check: `source_type` in `telegram_channel/telegram_group/website`
- `channel_assignments`
  - columns: `id`, `source_id`, `account_key`, `assigned_at`, `active`,
    `released_at`, `reason`
  - primary key: `id`
  - foreign keys:
    - `source_id -> ingestion_sources.source_id`
    - `account_key -> listener_accounts.account_key`
  - check: `reason` in `initial/rebalance/failover/source_disabled`
- `channel_assignment_audit`
  - columns: `id`, `source_id`, `previous_account_key`, `new_account_key`,
    `reason`, `occurred_at`
  - primary key: `id`
  - check: `reason` in `initial/rebalance/failover/source_disabled`

Indexes:

- `uq_channel_assignments_one_active_source`
- `ix_channel_assignments_active_account`

Seed data:

- none

### `migrations/002_mvp_schema_and_post_ai_dedup.sql`

Creates:

- `users`
  - columns: `user_id`, `telegram_username`, `referral_code`, `referred_by`,
    `premium_until`, `active_referral_count`, `status`, `created_at`,
    `updated_at`, `deleted_at`
  - primary key: `user_id`
  - unique: `referral_code`
  - foreign key: `referred_by -> users.user_id`
  - checks: `active_referral_count >= 0`, `status` in `active/disabled/deleted`
- `announcements`
  - columns: `announcement_id`, `idempotency_key`, `source_id`,
    `source_channel_id`, `source_message_id`, `source_message_ids`,
    `parent_announcement_id`, `status`, `canonical_rank`, `source_count`,
    `latest_source_id`, `source_url`, `forward_origin_key`, `price`,
    `currency`, `price_period`, `price_basis`, `price_normalized_monthly`,
    `rooms`, `area_sqm`, `floor`, `total_floors`, `district`, `address`,
    `phone_numbers`, `owner_type`, `renovation_level`, `renovation_source`,
    `furniture`, `description`, `audience_tags`, `audience_excluded_tags`,
    `confidence`, `field_confidence`, `assumptions`, `prompt_version`,
    `is_promoted`, `promoted_until`, `occurred_at`, `created_at`, `updated_at`,
    `deleted_at`
  - primary key: `announcement_id`
  - unique: `idempotency_key`
  - foreign keys:
    - `source_id -> ingestion_sources.source_id`
    - `parent_announcement_id -> announcements.announcement_id`
    - `latest_source_id -> ingestion_sources.source_id`
  - checks:
    - `status` in `active/archived/manual_review/deleted`
    - `source_count >= 1`
    - `price_period` in `daily/monthly/one_time`
    - `price_basis` in `total/per_person`
    - `owner_type` in `owner/agent/unknown`
    - `renovation_level` in `none/basic/good/euro/luxury`
    - `renovation_source` in `vision/text/unknown`
    - `confidence` is null or between 0 and 1
    - parent cannot equal self
- `announcement_media`
  - columns: `media_id`, `announcement_id`, `storage_url`, `object_key`,
    `mime_type`, `size_bytes`, `phash`, `content_sha256`, `width`, `height`,
    `metadata`, `created_at`
  - primary key: `media_id`
  - unique: `object_key`
  - foreign key: `announcement_id -> announcements.announcement_id`
  - check: `size_bytes >= 0`
- `user_filters`
  - columns: `filter_id`, `user_id`, `name`, `filters`, `enabled`,
    `created_at`, `updated_at`
  - primary key: `filter_id`
  - foreign key: `user_id -> users.user_id`
- `notifications`
  - columns: `notification_id`, `user_id`, `announcement_id`, `filter_id`,
    `status`, `priority`, `created_at`
  - primary key: `notification_id`
  - foreign keys:
    - `user_id -> users.user_id`
    - `announcement_id -> announcements.announcement_id`
    - `filter_id -> user_filters.filter_id`
  - check: `status` in `pending/sent/failed/suppressed`
- `parsing_logs`
  - columns: `idempotency_key`, `status`, `correlation_id`, `canonical`, `media`,
    `attempts`, `error_type`, `failure_reason`, `post_ai_published`,
    `created_at`, `updated_at`
  - primary key: `idempotency_key`
  - check: `status` in `succeeded/manual_review/failed`
- `processing_jobs`
  - columns: `job_id`, `idempotency_key`, `queue_name`, `status`, `attempts`,
    `correlation_id`, `payload`, `last_error`, `available_at`, `created_at`,
    `updated_at`
  - primary key: `job_id`
  - unique: `(queue_name, idempotency_key)`
  - check: `status` in `pending/processing/succeeded/failed/dead_letter`
- `audience_tag_types`
  - columns: `id`, `tag_key`, `display_name_uz`, `status`, `usage_count`,
    `created_at`, `updated_at`
  - primary key: `id`
  - unique: `tag_key`
  - check: `status` in `approved/pending/rejected`
- `source_suggestions`
  - columns: `suggestion_id`, `user_id`, `source_identifier`, `status`,
    `admin_note`, `created_at`, `updated_at`
  - primary key: `suggestion_id`
  - unique: `source_identifier`
  - foreign key: `user_id -> users.user_id`
  - check: `status` in `pending/approved/rejected`
- `referral_events`
  - columns: `referral_event_id`, `referrer_user_id`, `referred_user_id`,
    `activation_event_key`, `status`, `created_at`, `activated_at`
  - primary key: `referral_event_id`
  - unique: `referred_user_id`, `activation_event_key`
  - foreign keys:
    - `referrer_user_id -> users.user_id`
    - `referred_user_id -> users.user_id`
  - check: `status` in `pending/active/rejected`
- `dedup_decisions`
  - columns: `decision_id`, `announcement_id`, `candidate_announcement_id`,
    `decision`, `score`, `threshold`, `config_version`, `score_breakdown`,
    `created_at`
  - primary key: `decision_id`
  - foreign keys:
    - `announcement_id -> announcements.announcement_id`
    - `candidate_announcement_id -> announcements.announcement_id`
  - check: `decision` in `exact_duplicate/high_confidence_duplicate/possible_duplicate/new`
- `announcement_merge_audit`
  - columns: `audit_id`, `parent_announcement_id`, `child_announcement_id`,
    `decision_id`, `merge_policy`, `conflicts`, `created_at`
  - primary key: `audit_id`
  - unique: `child_announcement_id`
  - foreign keys:
    - both announcement IDs -> `announcements.announcement_id`
    - `decision_id -> dedup_decisions.decision_id`
- `manual_review_items`
  - columns: `review_id`, `announcement_id`, `reason`, `related_candidate_id`,
    `score_breakdown`, `status`, `created_at`, `decided_at`
  - primary key: `review_id`
  - foreign keys:
    - `announcement_id -> announcements.announcement_id`
    - `related_candidate_id -> announcements.announcement_id`
  - check: `reason` in `possible_duplicate/low_confidence/business_quality`
  - check: `status` in `pending/approved/rejected/merged`

Indexes:

- `ix_announcements_user_search_canonical`
- `ix_announcements_parent`
- `ix_announcements_dedup_candidates`
- `ix_announcements_price_search`
- `ix_announcements_audience_tags_gin`
- `ix_announcement_media_announcement`
- `ix_announcement_media_phash`
- `ix_user_filters_user_enabled`
- `ix_processing_jobs_ready`
- `ix_dedup_decisions_announcement`
- `ix_manual_review_items_pending`

Seed data:

- `audience_tag_types`
  - `family`
  - `family_with_children`
  - `students`
  - `single_male`
  - `single_female`
  - `group_of_girls`
  - `group_of_boys`
  - `foreigners`

### `migrations/003_sprint3_schema_contract_adjustments.sql`

Alters:

- `ingestion_sources`
  - adds `status`
  - adds `last_success_at`
  - adds `last_failure_at`
  - adds `failure_count`
  - adds `last_error`
  - adds `average_response_ms`
  - adds `deleted_at`
- `announcements`
  - drops `is_promoted` not null
  - drops `is_promoted` default

Indexes:

- `ix_ingestion_sources_active`
- `ix_ingestion_sources_listener_account`
- `ix_announcements_latest_source`
- `ix_notifications_user_status_created`
- `ix_source_suggestions_status_created`
- `ix_referral_events_referrer_status`

### `migrations/004_sprint4_search_and_saved_filters.sql`

Indexes:

- `ix_announcements_sprint4_search_filters`
- `ix_announcements_audience_excluded_tags_gin`
- `ix_user_filters_user_created`

### `migrations/005_sprint5_notification_matching.sql`

Indexes:

- `ix_notifications_pending_priority_created`
- `ix_user_filters_matching_enabled`

### `migrations/006_sprint5_notification_delivery.sql`

Alters:

- `notifications`
  - extends status check to include `sending`
  - adds `attempts`
  - adds `last_attempt_at`
  - adds `next_attempt_at`
  - adds `telegram_message_id`
  - adds `delivered_at`

Creates:

- `notification_delivery_attempts`
  - columns: `attempt_id`, `notification_id`, `attempt_no`, `result`,
    `error_type`, `retryable`, `telegram_message_id`, `created_at`
  - primary key: `attempt_id`
  - foreign key: `notification_id -> notifications.notification_id`

Indexes:

- `ix_notifications_ready_delivery`
- `ix_notification_delivery_attempts_notification`

### `migrations/007_sprint5_referral_premium.sql`

Creates:

- `referral_premium_awards`
  - columns: `award_id`, `referrer_user_id`, `milestone`, `created_at`
  - primary key: `award_id`
  - foreign key: `referrer_user_id -> users.user_id`
  - check: `milestone > 0`
- `referral_audit_events`
  - columns: `audit_id`, `user_id`, `action`, `reason`, `created_at`
  - primary key: `audit_id`
  - foreign key: `user_id -> users.user_id`

Indexes:

- `ix_referral_events_referred_status`
- `ix_referral_audit_events_user_created`

### `migrations/008_sprint6_admin_tags_content.sql`

Alters:

- `source_suggestions`
  - adds `source_type`
  - adds `reward_granted`
  - adds `source_id`
  - adds `decided_at`

Creates:

- `admin_audit_events`
  - columns: `audit_id`, `admin_user_id`, `action`, `entity_type`, `entity_id`,
    `idempotency_key`, `before_state`, `after_state`, `note`, `created_at`
  - primary key: `audit_id`
- `content_channel_posts`
  - columns: `post_id`, `post_kind`, `idempotency_key`, `text`, `status`,
    `publisher_message_id`, `published_at`, `attempts`, `next_attempt_at`,
    `error_reason`, `schedule_timezone`, `created_at`
  - primary key: `post_id`
  - checks: `post_kind` in `daily_top_offer/weekly_stats/district_comparison/tips/success_story/transparency_report`
  - checks: `status` in `drafted/published/dry_run/failed`

Indexes:

- `ix_admin_audit_events_entity_created`
- `ix_content_channel_posts_created`
- `ix_content_channel_posts_retry`
- `ix_audience_tag_types_pending`
- `ix_audience_tag_types_approved_usage`
- `ix_source_suggestions_pending`

### `migrations/009_sprint7_analytics_events.sql`

Creates:

- `analytics_events`
  - columns: `analytics_event_id`, `event_name`, `idempotency_key`,
    `occurred_at`, `user_id`, `subject_id`, `metadata`, `created_at`
  - primary key: `analytics_event_id`
  - check: event name whitelist

Indexes:

- `ix_analytics_events_name_time`
- `ix_analytics_events_user_time`
- `ix_analytics_events_subject_time`

### `migrations/010_sprint7_technical_metric_events.sql`

Creates:

- `technical_metric_events`
  - columns: `technical_metric_event_id`, `metric_name`, `idempotency_key`,
    `occurred_at`, `value`, `component`, `subject_id`, `metadata`, `created_at`
  - primary key: `technical_metric_event_id`
  - check: metric name whitelist

Indexes:

- `ix_technical_metric_events_name_time`
- `ix_technical_metric_events_component_time`
- `ix_technical_metric_events_subject_time`

## Safe Migration Plan

### Fresh database

Use Alembic as the only execution path:

```powershell
.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
```

Required post-apply checks:

- all expected tables exist
- all expected indexes and constraints exist
- seed audience tags exist
- ORM metadata matches the DDL shape

### Legacy SQL database

Do not stamp `head` until schema inspection is complete.

Bridge flow:

1. Run `scripts/schema_bridge_check.py` against the target DB.
2. Compare table, column, index, and seed-row fingerprint against ORM metadata.
3. If the bridge contract matches, record evidence and stamp `head`.
4. If the schema differs, do not stamp.
5. Apply an explicit forward Alembic revision or a documented data-safe patch.
6. Re-run inspection before any stamp.

### Bridge/stamp rules

- `stamp head` is allowed only after compatibility is proven.
- No destructive reset, drop, or history rewrite is allowed.
- A mismatching legacy database must move forward explicitly.
- Fresh and legacy paths must converge on Alembic as the production source of truth.

## Source of Truth Policy

- Production deploy path: Alembic.
- Legacy `migrations/*.sql`: archive and reference only.
- Temporary bridge helpers: one-shot compatibility aid only.
- Do not keep raw SQL deployment and Alembic deployment as equal production engines.

## Archive / Deprecation Matrix

### Keep as archive/reference

- `migrations/001_listener_pool.sql`
- `migrations/002_mvp_schema_and_post_ai_dedup.sql`
- `migrations/003_sprint3_schema_contract_adjustments.sql`
- `migrations/004_sprint4_search_and_saved_filters.sql`
- `migrations/005_sprint5_notification_matching.sql`
- `migrations/006_sprint5_notification_delivery.sql`
- `migrations/007_sprint5_referral_premium.sql`
- `migrations/008_sprint6_admin_tags_content.sql`
- `migrations/009_sprint7_analytics_events.sql`
- `migrations/010_sprint7_technical_metric_events.sql`

### Keep in Alembic execution path

- `alembic/versions/0001_legacy_sql_baseline.py`
- future schema changes as normal Alembic revisions

### Deprecate

- `scripts/migration_smoke.psql`
- raw SQL deployment assumptions in `scripts/migration_smoke.py`
- any production path that directly runs `migrations/*.sql`

## Red Tests to Add Now

These tests intentionally fail today and should stay red until the next implementation prompt:

1. Default app search should not return `503` when `create_app()` is used without manual wiring.
2. Migration smoke scripts should not seed `announcements.is_promoted` with `null`.
3. Feature flags should survive restart instead of resetting to in-memory defaults.
4. Source suggestion request schema should not expose arbitrary `user_id` in the request body.

## Immediate Follow-Up Sequence

1. Keep this audit doc as the authoritative plan for the migration transition.
2. Add red regression tests for the four review failures.
3. Next implementation prompt should convert the runtime path away from in-memory flags and
   remove raw SQL smoke dependencies.
4. Do not introduce schema reset or production DDL in this step.
