# PostgreSQL table ownership map — Task 000

> 2026-11-10 lab implementation addendum: original V1 evidence below is retained. The as-built lab uses owner-local JSON records and one exact-bound consumer queue per owner, documented in decisions/ADR-006-isolated-v2-as-built.md and verification/FINAL_V2_REPORT.md. The26-table importer is archive-only; no physical V1 table migration or active-entity compatibility is proved.

Baseline: `main` at `addb856a99d24946ef127384121b99de371c017b`, 2026-11-09. VERIFIED means repository evidence; deployed schema, grants and row counts are NOT VERIFIED. Proposed owners require approval.

All 26 application tables are unqualified in models and the baseline. `public.<table>` below is the default-schema candidate, not a claim about deployed search_path. `alembic_version` is Alembic infrastructure metadata, owned by migration orchestration (`alembic/env.py:64`, `alembic/env.py:94`). No live database was queried.

Task 001 reconciliation: owner column below is the current single-owner proposal, superseding the Phase 0 alternatives. Current caller/evidence facts remain historical V1 findings. See database-ownership.md for T0–T6/FK transitions; no runtime ownership was changed.

## Ownership and callers

| Current table (default schema candidate) | Proposed owner | Current service access (VERIFIED source) | Transaction group | Model / creation migration |
|---|---|---|---|---|
| public.admin_audit_events | control | Admin suggestions/tags append; admin reads | T4 | `src/estateflow/models/admin_content.py:26`; `alembic/versions/5fdae000458c_add_tables.py:27` |
| public.analytics_events | analytics | API/bot/delivery append and metrics API reads | T4 | `src/estateflow/models/analytics.py:27`; `alembic/versions/5fdae000458c_add_tables.py:48` |
| public.announcement_media | catalog | Dedup writes; asyncpg search/delivery read; active SQLAlchemy search omits media | T1 | `src/estateflow/models/announcements.py:237`; `alembic/versions/5fdae000458c_add_tables.py:646` |
| public.announcement_merge_audit | catalog | Dedup/review writes; audit reads | T1 | `src/estateflow/models/reviews.py:56`; `alembic/versions/5fdae000458c_add_tables.py:781` |
| public.announcements | catalog | Dedup/review and admin tag remap write; search, delivery, content and metrics read | T1/T3 | `src/estateflow/models/announcements.py:58`; `alembic/versions/5fdae000458c_add_tables.py:480` |
| public.audience_tag_types | taxonomy | Admin mutates; combined AI resolves/increments; API/bot reads | T3 | `src/estateflow/models/tags_and_suggestions.py:27`; `alembic/versions/5fdae000458c_add_tables.py:80` |
| public.channel_assignment_audit | listener | SQL repository appends; live coordinator instead uses memory | T5 | `src/estateflow/models/listeners.py:173`; `alembic/versions/5fdae000458c_add_tables.py:101` |
| public.channel_assignments | listener | SQL repository writes; topology reads; live coordinator uses memory | T5 | `src/estateflow/models/listeners.py:136`; `alembic/versions/5fdae000458c_add_tables.py:585` |
| public.content_channel_posts | content | Content automation writes/reads; admin triggers | T4 | `src/estateflow/models/admin_content.py:45`; `alembic/versions/5fdae000458c_add_tables.py:116` |
| public.dedup_decisions | catalog | Dedup writes; asyncpg content transparency reads | T1 | `src/estateflow/models/reviews.py:26`; `alembic/versions/5fdae000458c_add_tables.py:676` |
| public.feature_flag_audit_events | control | Admin set_flag appends; admin reads | T4 | `src/estateflow/models/release_controls.py:27`; `alembic/versions/5fdae000458c_add_tables.py:144` |
| public.feature_flag_states | control | Admin updates; shared runtime/listener reads; bootstrap seeds | T4 | `src/estateflow/models/release_controls.py:15`; `alembic/versions/5fdae000458c_add_tables.py:171` |
| public.ingestion_sources | listener | Admin/bootstrap/source repo write; dedup also inserts; listener, scraper, session inventory read | T5/T1 | `src/estateflow/models/listeners.py:51`; `alembic/versions/5fdae000458c_add_tables.py:346` |
| public.listener_accounts | listener | Bootstrap/source config and SQL pool repo write; topology reads; live health is in memory | T5 | `src/estateflow/models/listeners.py:26`; `alembic/versions/5fdae000458c_add_tables.py:190` |
| public.manual_review_items | catalog | Dedup queues; admin review lists/updates | T1 | `src/estateflow/models/reviews.py:82`; `alembic/versions/5fdae000458c_add_tables.py:709` |
| public.notification_delivery_attempts | notifications | Delivery writes; delivery/audit reads | T2 | `src/estateflow/models/announcements.py:359`; `alembic/versions/5fdae000458c_add_tables.py:816` |
| public.notifications | notifications | Matcher creates; delivery updates; bot engagement uses notification identity | T2 | `src/estateflow/models/announcements.py:300`; `alembic/versions/5fdae000458c_add_tables.py:741` |
| public.parsing_logs | ai | No operational reader/writer found; declared model and baseline only | T0 | `src/estateflow/models/announcements.py:387`; `alembic/versions/5fdae000458c_add_tables.py:211` |
| public.processing_jobs | ai | No operational reader/writer found; declared model and baseline only | T0 | `src/estateflow/models/announcements.py:416`; `alembic/versions/5fdae000458c_add_tables.py:248` |
| public.referral_audit_events | identity | User/referral service appends | T6 | `src/estateflow/models/referrals.py:72`; `alembic/versions/5fdae000458c_add_tables.py:380` |
| public.referral_events | identity | API/bot referral flow reads/writes | T6 | `src/estateflow/models/referrals.py:25`; `alembic/versions/5fdae000458c_add_tables.py:400` |
| public.referral_premium_awards | identity | Referral activation reads/writes awards | T6 | `src/estateflow/models/referrals.py:54`; `alembic/versions/5fdae000458c_add_tables.py:429` |
| public.source_suggestions | control | API/bot/internal submit; admin approves/rejects | T4 | `src/estateflow/models/tags_and_suggestions.py:65`; `alembic/versions/5fdae000458c_add_tables.py:609` |
| public.technical_metric_events | analytics | Dedup/delivery/combined stages append; metrics reads; parser downgrade deletes | T4 | `src/estateflow/models/analytics.py:71`; `alembic/versions/5fdae000458c_add_tables.py:283` |
| public.user_filters | notifications | API/bot CRUD; matcher/delivery read; admin tag remap writes | T2/T3 | `src/estateflow/models/announcements.py:276`; `alembic/versions/5fdae000458c_add_tables.py:448` |
| public.users | identity | API/bot upsert/referrals; suggestion rewards mutate premium; matcher reads | T6 | `src/estateflow/models/announcements.py:32`; `alembic/versions/5fdae000458c_add_tables.py:316` |

## Transactions and unsafe shared state

- T0: No operational references to ParsingLogRecord/ProcessingJobRecord or their SQL tables found under src/services/apps. This is VERIFIED as a static search result, not proof of empty production tables. Do not discard them.
- T1: `src/estateflow/repositories/post_ai_dedup.py:316` saves sources, announcement and media without an encompassing transaction. Canonical update uses a transaction at :458; parent/child merge locks and updates announcements plus merge audit at :541. Decision/review writes are separate methods (:214, :421); publication is separate (`src/estateflow/services/post_ai_dedup.py:1450`). Catalog also creates ingestion_sources (:930), violating the proposed ownership boundary.
- T2: Matcher commits notifications at `src/estateflow/repositories/sqlalchemy.py:1138`, then publishes only newly created jobs (`src/estateflow/services/notifications.py:618`). Delivery reads user_filters and announcements (:1182, :1185); claims pending jobs via SELECT then commit (:1205–1220), with no row lock or atomic conditional UPDATE. Status and attempt insert commit together (:1260, :1300). Neither broker ACK nor Telegram send shares that transaction.
- T3: Admin tag remap updates announcements and user_filters in one transaction (`src/estateflow/repositories/sqlalchemy.py:835–885`; alternative asyncpg `src/estateflow/repositories/audience_tags.py:145`). Replace with versioned taxonomy commands and acknowledged owner-local updates before separating schemas.
- T4: SQLAlchemy methods generally open their own session and commit independently; analytics record_once (:896, :937), content save (:1046), suggestion reward (:702) and audit (:675) are separate operations in `src/estateflow/repositories/sqlalchemy.py`. Feature flag state plus audit commit in `src/estateflow/repositories/release_controls.py:50–86`. Content external publish precedes persistence (`src/estateflow/services/content_automation.py:298–315`).
- T5: Bootstrap combines listener account and source configuration in one session (`services/ingestion_bootstrap/main.py:123`); source repository does likewise (`src/estateflow/repositories/sqlalchemy.py:307–382`). SQL pool repositories exist (`src/estateflow/repositories/listener_pool.py:25`), but listener construction uses in-memory accounts/assignments (`src/estateflow/services/listener_refresh.py:124`).
- T6: Referral activation updates referral event, user premium/counters and premium awards together (`src/estateflow/repositories/sqlalchemy.py:185–259`; asyncpg transaction `src/estateflow/repositories/users.py:291`). Keep these in identity. Suggestion premium grants use a separate user update (:702 in SQLAlchemy). Saved filter commit precedes referral activation (`src/estateflow/application/runtime.py:212`).

## Exact operational reference index (VERIFIED static references)

References include alternative repositories; construction in dependency-matrix.md determines active paths. Imports alone do not imply execution. Migration CLI/schema inspection references are excluded from this operational index.

### admin_audit_events
- `src/estateflow/repositories/source_suggestions.py:132` (lines 132).
- `src/estateflow/repositories/sqlalchemy.py:15` (lines 15, 680, 695, 696, 815).
- `src/estateflow/repositories/audience_tags.py:115` (lines 115).

### analytics_events
- `src/estateflow/repositories/sqlalchemy.py:16` (lines 16, 898, 919, 921, 922, 925, 926, 1426).
- `src/estateflow/repositories/analytics.py:18` (lines 18, 38).

### announcement_media
- `src/estateflow/repositories/post_ai_dedup.py:33` (lines 33, 82, 413, 951).
- `src/estateflow/repositories/search.py:53` (lines 53, 98).
- `src/estateflow/repositories/notifications.py:167` (lines 167).
- FK `announcements.announcement_id` at `src/estateflow/models/announcements.py:252`.

### announcement_merge_audit
- `src/estateflow/repositories/post_ai_dedup.py:518` (lines 518, 562).
- FK `announcements.announcement_id` at `src/estateflow/models/reviews.py:67`.
- FK `announcements.announcement_id` at `src/estateflow/models/reviews.py:70`.
- FK `dedup_decisions.decision_id` at `src/estateflow/models/reviews.py:73`.

### announcements
- `src/estateflow/repositories/post_ai_dedup.py:32` (lines 32, 98, 107, 115, 199, 324, 412, 461, 607, 626, 700, 703).
- `src/estateflow/repositories/search.py:52` (lines 52, 97).
- `src/estateflow/repositories/sqlalchemy.py:17` (lines 17, 394, 395, 396, 397, 402, 403, 408, 413, 416, 427, 428, 434, 435, 439, 440, 444, 445, 446, 447, 451, 452, 453, 454, 501, 502, 503, 504, 505, 845, 987, 988, 992, 994, 995, 996, 1010, 1012, 1013, 1014, 1017, 1018, 1186, 1187, 1465, 1479).
- `src/estateflow/repositories/audience_tags.py:154` (lines 154).
- `src/estateflow/repositories/notifications.py:165` (lines 165).
- `src/estateflow/repositories/content_automation.py:30` (lines 30, 45, 51, 84, 111).
- FK `ingestion_sources.source_id` at `src/estateflow/models/announcements.py:159`.
- FK `announcements.announcement_id` at `src/estateflow/models/announcements.py:167`.
- FK `ingestion_sources.source_id` at `src/estateflow/models/announcements.py:179`.

### audience_tag_types
- `src/estateflow/repositories/sqlalchemy.py:18` (lines 18, 729, 730, 731, 742, 743, 744, 752, 764, 768, 781, 799, 1415).
- `src/estateflow/repositories/audience_tags.py:24` (lines 24, 36, 45, 58, 75, 95).

### channel_assignment_audit
- `src/estateflow/repositories/listener_pool.py:267` (lines 267).

### channel_assignments
- `src/estateflow/repositories/listener_pool.py:206` (lines 206, 216, 243, 249).
- `src/estateflow/services/listener_status.py:15` (lines 15, 288, 292, 293, 294, 319).
- FK `ingestion_sources.source_id` at `src/estateflow/models/listeners.py:157`.
- FK `listener_accounts.account_key` at `src/estateflow/models/listeners.py:160`.

### content_channel_posts
- `src/estateflow/repositories/sqlalchemy.py:19` (lines 19, 1032, 1033, 1040, 1041, 1049, 1050, 1054, 1449).
- `src/estateflow/repositories/content_automation.py:135` (lines 135, 149, 160).

### dedup_decisions
- `src/estateflow/repositories/post_ai_dedup.py:173` (lines 173, 430).
- `src/estateflow/repositories/content_automation.py:35` (lines 35).
- FK `announcements.announcement_id` at `src/estateflow/models/reviews.py:41`.
- FK `announcements.announcement_id` at `src/estateflow/models/reviews.py:44`.

### feature_flag_audit_events
- `src/estateflow/repositories/release_controls.py:12` (lines 12, 79, 97, 98, 99, 132).

### feature_flag_states
- `src/estateflow/repositories/release_controls.py:12` (lines 12, 41, 65, 66, 116, 118, 123).
- `services/ingestion_bootstrap/main.py:18` (lines 18, 214, 215, 219).

### ingestion_sources
- `src/estateflow/repositories/post_ai_dedup.py:935` (lines 935).
- `src/estateflow/repositories/sqlalchemy.py:20` (lines 20, 281, 282, 283, 284, 322, 357, 1368).
- `src/estateflow/repositories/listener_pool.py:77` (lines 77, 105, 147).
- `src/estateflow/services/telegram_sessions.py:10` (lines 10, 77, 78, 79, 80, 81, 83, 84).
- `services/ingestion_bootstrap/main.py:17` (lines 17, 139, 170, 203, 204, 205, 206, 322).
- FK `listener_accounts.account_key` at `src/estateflow/models/listeners.py:121`.

### listener_accounts
- `src/estateflow/repositories/sqlalchemy.py:21` (lines 21, 307, 308, 312).
- `src/estateflow/repositories/listener_pool.py:33` (lines 33, 42).
- `src/estateflow/services/listener_status.py:15` (lines 15, 279, 283).
- `services/ingestion_bootstrap/main.py:17` (lines 17, 125, 126, 131).

### manual_review_items
- `src/estateflow/repositories/post_ai_dedup.py:224` (lines 224, 239, 265, 288, 299, 307).
- FK `announcements.announcement_id` at `src/estateflow/models/reviews.py:103`.
- FK `announcements.announcement_id` at `src/estateflow/models/reviews.py:107`.

### notification_delivery_attempts
- `src/estateflow/repositories/sqlalchemy.py:22` (lines 22, 1252, 1291, 1330).
- `src/estateflow/repositories/notifications.py:378` (lines 378).
- FK `notifications.notification_id` at `src/estateflow/models/announcements.py:375`.

### notifications
- `src/estateflow/repositories/sqlalchemy.py:23` (lines 23, 1122, 1123, 1124, 1125, 1130, 1151, 1153, 1155, 1156, 1160, 1161, 1162, 1176, 1177, 1206, 1207, 1208, 1210, 1211, 1232, 1233, 1273, 1274, 1312, 1313, 1532, 1549).
- `src/estateflow/repositories/notifications.py:82` (lines 82, 97, 111, 164, 195, 208, 242, 280, 315).
- FK `users.user_id` at `src/estateflow/models/announcements.py:334`.
- FK `announcements.announcement_id` at `src/estateflow/models/announcements.py:336`.
- FK `user_filters.filter_id` at `src/estateflow/models/announcements.py:339`.

### parsing_logs
- No operational reference found in the searched roots (NOT VERIFIED at runtime).

### processing_jobs
- No operational reference found in the searched roots (NOT VERIFIED at runtime).

### referral_audit_events
- `src/estateflow/repositories/users.py:375` (lines 375).
- `src/estateflow/repositories/sqlalchemy.py:24` (lines 24, 269).
- FK `users.user_id` at `src/estateflow/models/referrals.py:78`.

### referral_events
- `src/estateflow/repositories/users.py:214` (lines 214, 257, 273, 295, 331).
- `src/estateflow/repositories/sqlalchemy.py:25` (lines 25, 137, 138, 139, 152, 153, 158, 175, 176, 196, 197, 1356).
- FK `users.user_id` at `src/estateflow/models/referrals.py:41`.
- FK `users.user_id` at `src/estateflow/models/referrals.py:44`.

### referral_premium_awards
- `src/estateflow/repositories/users.py:425` (lines 425).
- `src/estateflow/repositories/sqlalchemy.py:26` (lines 26, 235, 236, 237, 245).
- FK `users.user_id` at `src/estateflow/models/referrals.py:66`.

### source_suggestions
- `src/estateflow/repositories/source_suggestions.py:32` (lines 32, 42, 61, 76, 87, 106).
- `src/estateflow/repositories/sqlalchemy.py:27` (lines 27, 601, 602, 616, 630, 631, 633, 642, 643, 660, 661, 1399).
- FK `users.user_id` at `src/estateflow/models/tags_and_suggestions.py:87`.
- FK `ingestion_sources.source_id` at `src/estateflow/models/tags_and_suggestions.py:99`.

### technical_metric_events
- `src/estateflow/repositories/sqlalchemy.py:28` (lines 28, 939, 965, 967, 968, 971, 972, 1437).
- `src/estateflow/repositories/analytics.py:66` (lines 66, 94).

### user_filters
- `src/estateflow/repositories/saved_filters.py:74` (lines 74, 89, 101, 123, 142).
- `src/estateflow/repositories/sqlalchemy.py:29` (lines 29, 517, 531, 532, 533, 541, 542, 543, 559, 560, 561, 579, 580, 581, 872, 1086, 1183, 1385).
- `src/estateflow/repositories/audience_tags.py:178` (lines 178).
- `src/estateflow/repositories/notifications.py:31` (lines 31, 166).
- FK `users.user_id` at `src/estateflow/models/announcements.py:286`.

### users
- `src/estateflow/repositories/users.py:167` (lines 167, 190, 203, 233, 247, 311, 344, 441).
- `src/estateflow/repositories/source_suggestions.py:163` (lines 163).
- `src/estateflow/repositories/sqlalchemy.py:30` (lines 30, 85, 88, 108, 109, 120, 126, 165, 204, 219, 220, 710, 1096, 1343).
- `src/estateflow/repositories/notifications.py:52` (lines 52).
- FK `users.user_id` at `src/estateflow/models/announcements.py:47`.

## Migration and isolation gate

VERIFIED: the single baseline creates all tables; legacy SQL files remain a second historical representation. Subsequent Alembic revisions add source_profile (`alembic/versions/9b77c0b3d5a1_add_source_profile_to_ingestion_sources.py:24`), change media PK (`alembic/versions/c4d8e7f1a2b3_allow_media_per_announcement.py:15`), backfill reviews (`alembic/versions/e7f2a1b3c4d5_backfill_manual_review_queue.py:14`), activate approved reviews (`alembic/versions/f8a3b2c4d5e6_activate_approved_manual_reviews.py:14`), and add parser policy/metric constraint (`alembic/versions/a1c4e7f9b2d6_add_source_parser_policy.py:29`). Parser downgrade deletes metrics and policy columns (:96, :126). Media downgrade can conflict with repeated media IDs across announcements (:23). Prefer forward correction; do not use downgrade as a data rollback.

NOT VERIFIED: live roles, grants, sequences/default privileges, extensions, table drift, external SQL clients and actual schema. Current runtime uses one settings database URL (`src/estateflow/db/session.py:18`); ingestion Compose shares runtime DB identity (`docker-compose.ingestion.yml:10`) and separately supplies migration identity (:15). No source evidence establishes per-service runtime isolation.

Recommended migration for every row: inventory deployed dependencies privately; restore backup into a disposable database; add owner schema/tables with owner-local constraints and outbox/inbox; backfill preserving existing IDs and media references; reconcile counts/checksums/relationships; run role-impersonated negative access tests including sequences and future objects; switch one writer, then consumers; retain V1 tables through the rollback window. Cross-owner FKs listed above become scalar references plus local projections. Do not move all shared tables with ALTER SCHEMA and assume ownership is solved. Rollback switches the writer/consumer routing only after reconciling events accepted since the switch; never restore an old backup over newer writes.

## Historical SQL migration reference index (VERIFIED definitions only)

The following legacy statements mention mapped tables. They are reference/bridge material, not an instruction to apply them. Alembic creation and later changes are described above.

- admin_audit_events: `migrations/008_sprint6_admin_tags_content.sql:11`.
- analytics_events: `migrations/009_sprint7_analytics_events.sql:2`.
- announcement_media: `migrations/002_mvp_schema_and_post_ai_dedup.sql:94`, `migrations/011_media_per_announcement.sql:1`.
- announcement_merge_audit: `migrations/002_mvp_schema_and_post_ai_dedup.sql:240`.
- announcements: `migrations/002_mvp_schema_and_post_ai_dedup.sql:20`, `migrations/003_sprint3_schema_contract_adjustments.sql:4`, `migrations/004_sprint4_search_and_saved_filters.sql:5`.
- audience_tag_types: `migrations/002_mvp_schema_and_post_ai_dedup.sql:177`, `migrations/008_sprint6_admin_tags_content.sql:52`.
- channel_assignment_audit: `migrations/001_listener_pool.sql:47`.
- channel_assignments: `migrations/001_listener_pool.sql:28`.
- content_channel_posts: `migrations/008_sprint6_admin_tags_content.sql:24`.
- dedup_decisions: `migrations/002_mvp_schema_and_post_ai_dedup.sql:224`.
- feature_flag_audit_events: .
- feature_flag_states: .
- ingestion_sources: `migrations/001_listener_pool.sql:15`, `migrations/002_mvp_schema_and_post_ai_dedup.sql:23`, `migrations/003_sprint3_schema_contract_adjustments.sql:8`, `migrations/008_sprint6_admin_tags_content.sql:8`.
- listener_accounts: `migrations/001_listener_pool.sql:4`.
- manual_review_items: `migrations/002_mvp_schema_and_post_ai_dedup.sql:251`.
- notification_delivery_attempts: `migrations/006_sprint5_notification_delivery.sql:17`.
- notifications: `migrations/002_mvp_schema_and_post_ai_dedup.sql:130`, `migrations/003_sprint3_schema_contract_adjustments.sql:35`, `migrations/005_sprint5_notification_matching.sql:7`, `migrations/006_sprint5_notification_delivery.sql:3`.
- parsing_logs: `migrations/002_mvp_schema_and_post_ai_dedup.sql:144`.
- processing_jobs: `migrations/002_mvp_schema_and_post_ai_dedup.sql:158`.
- referral_audit_events: `migrations/007_sprint5_referral_premium.sql:11`.
- referral_events: `migrations/002_mvp_schema_and_post_ai_dedup.sql:211`, `migrations/003_sprint3_schema_contract_adjustments.sql:41`, `migrations/007_sprint5_referral_premium.sql:20`.
- referral_premium_awards: `migrations/007_sprint5_referral_premium.sql:3`.
- source_suggestions: `migrations/002_mvp_schema_and_post_ai_dedup.sql:199`, `migrations/003_sprint3_schema_contract_adjustments.sql:38`, `migrations/008_sprint6_admin_tags_content.sql:4`.
- technical_metric_events: `migrations/010_sprint7_technical_metric_events.sql:2`.
- user_filters: `migrations/002_mvp_schema_and_post_ai_dedup.sql:116`, `migrations/004_sprint4_search_and_saved_filters.sql:14`, `migrations/005_sprint5_notification_matching.sql:11`.
- users: `migrations/002_mvp_schema_and_post_ai_dedup.sql:7`, `migrations/007_sprint5_referral_premium.sql:5`.
