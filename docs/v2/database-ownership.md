# PostgreSQL ownership — Task 001 draft

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See verification/FINAL_V2_REPORT.md and decisions/ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

Status: **PROPOSED owner map**, constrained by confirmed Variant A, initial AI/media co-deployment and consolidated notifications ownership. Physical V1 schema is NOT VERIFIED. Unqualified table declarations do not prove public search_path in deployment. Evidence for each table/model/migration/caller remains in table-ownership-map.md; that Phase 0 history is retained with a Task 001 reconciliation note.

## Exactly one accountable owner per legacy table

| Owner schema | Legacy tables (26 total) | New owner-local V2 state / retained invariants |
|---|---|---|
| listener | listener_accounts; ingestion_sources; channel_assignments; channel_assignment_audit | Transport fields only for migrated ingestion_sources; fenced session/ingress/album journal. Parser policy copied to source_parser with versioned provenance, never jointly writable columns |
| source_parser | None | Recipe/binding history, immutable snapshots, generations/manifests, quarantine and bounded buffers |
| pre_ai | None | Persistent signals, decisions and processing state |
| ai | parsing_logs; processing_jobs | Initial AI/media run and transformation metadata; T0 legacy data retained until restored-data investigation; names do not imply proven runtime use |
| catalog | announcements; announcement_media; dedup_decisions; announcement_merge_audit; manual_review_items | Canonical/evidence/review/merge transaction and catalog revisions; attachments stay here even if media processing later separates |
| search | None | Local rebuildable listing/media query projection |
| notifications | user_filters; notifications; notification_delivery_attempts | Filter/match/job/attempt/due/lease state; separate matcher/sender processes, one data owner |
| identity | users; referral_events; referral_premium_awards; referral_audit_events | Referral activation/premium award atomicity retained locally |
| control | feature_flag_states; feature_flag_audit_events; source_suggestions; admin_audit_events | Flags and source approval orchestration. Legacy shared admin audit imported once; future central view fed from owner-local audit facts |
| taxonomy | audience_tag_types | Tag versions, remap operation/ack state |
| content | content_channel_posts | Durable schedule, publication attempts and unknown-send ledger |
| analytics | analytics_events; technical_metric_events | Event-fed metrics, no multi-owner inserts |

Every context has its own inbox/outbox/audit as needed; none is a shared mutable infrastructure table. alembic_version is migration metadata, not a 27th application domain table. Future schema-local version tables replace the single-chain responsibility only through an approved migration task.

## T0–T6 transaction reconciliation

“Design now / defer execution” applies to every row: no existing V1 FK, role, transaction or table is changed in Task 001.

| Group / VERIFIED source | Target atomic boundary | Crossing dependency and proposed transition | Gate before extraction |
|---|---|---|---|
| T0: model-only parsing_logs/processing_jobs, `src/estateflow/models/announcements.py:387`, :416 | AI owns retained legacy records; new stage processing belongs to respective owner | No migration based on table name alone; preserve records and external client compatibility | Disposable restored inventory/retention and external writer audit; no DROP |
| T1: catalog save/media/merge/decision/review, `src/estateflow/repositories/post_ai_dedup.py:316`, :458, :541; source insert :930 | One catalog transaction: canonical/evidence/attachments/review or merge + revision + inbox/outbox | Listener publishes source descriptors; catalog local source projection replaces source creation/FK. Search/content/notifications consume full catalog snapshots, not SQL | Partial-write, merge races, review and outbox crash tests; retain scalar legacy/source IDs |
| T2: match/job/attempt/context `src/estateflow/repositories/sqlalchemy.py:1080–1300`; commit/publish `src/estateflow/services/notifications.py:618` | Notifications owns filters/jobs/attempts, local matching and delivery projections; claim + attempt scheduling atomic | Users/entitlements arrive from identity; listings/media from catalog. Commit job+outbox; sender uses same owner ledger and immutable intended payload | Due retries, competing claims, lease expiry and unknown send tests; no exactly-once assertion |
| T3: tag remap `src/estateflow/repositories/sqlalchemy.py:835–885` | Taxonomy commits alias/version and outbox; catalog and notifications apply remaps locally | Remap operation tracks per-owner acknowledgment and lag; temporary mixed revisions visible. No distributed rollback transaction; compensating new revision if needed | Replay remap twice; fail one owner, retry; prove search/match converge before retiring old alias |
| T4: suggestions/rewards/audit/flags/content/metrics `src/estateflow/repositories/sqlalchemy.py:588–1076`; flag state+audit `src/estateflow/repositories/release_controls.py:50` | Control suggestion/flag workflow local; identity reward once by suggestion ID; content send ledger local; analytics projection local | Approval saga issues listener and parser commands, entitlement reward independent and idempotent. Audit facts feed central control/analytics projections. Content rendering uses local catalog/stat projection | Partial saga completion visible; retries/compensation; no session activation until prerequisites acknowledged; send ambiguity approved |
| T5: account/source bootstrap `services/ingestion_bootstrap/main.py:123`; live memory pool `src/estateflow/services/listener_refresh.py:124` | Listener owns account/subscription/assignment journal; parser owns policy separately | Split compound legacy row once during backfill; listener consumes immutable parser-policy reference/snapshot. Control orchestrates prerequisites. Source refresh becomes durable revision reconciliation, Redis hint optional | Lost signal, concurrent refresh, fencing/restart/album journal tests; source activation never depends solely on Redis pubsub |
| T6: users/referral/awards `src/estateflow/repositories/sqlalchemy.py:185–259` | Identity activation, counter and premium awards remain one transaction with inbox/outbox | notifications emits filter activation fact; identity records eligibility and awards once. Suggestion reward becomes control → identity command; notification entitlement projection eventually consistent | Duplicate activation/reward, reordered entitlement and revoked consent; UX explicitly reports pending reward |

## FK and read/write transformation map

All declared FK edges are listed below; source line inventory is retained in table-ownership-map.md. Runtime reference indexes there include SQLAlchemy and alternative asyncpg implementations; current callers are not automatically valid V2 dependencies.

| Current edge | Target treatment |
|---|---|
| users.referred_by → users; all referral_events/referral_premium_awards/referral_audit_events → users | Keep identity-local FKs and transactions |
| ingestion_sources.listener_account_key → listener_accounts; channel_assignments → ingestion_sources/listener_accounts | Keep listener-local FKs; policy history is a separate parser table, not a shared source row |
| announcements.source_id/latest_source_id → ingestion_sources | Replace both FKs with immutable scalar source IDs plus catalog-local descriptor/evidence projection; no catalog inserts into listener schema |
| announcements.parent_announcement_id → announcements; announcement_media → announcements | Keep catalog-local FKs |
| dedup_decisions → announcements (incoming/candidate); merge_audit → announcements (parent/child), dedup_decisions; manual_review_items → announcements (incoming/candidate) | Keep catalog-local FKs; review/merge mutation and events committed together |
| user_filters → users; notifications → users | Replace identity FKs with scalar user IDs plus notifications-owned eligibility/consent projection; stale entitlement policy OPEN D08 |
| notifications → announcements | Replace with scalar actual canonical ID + catalog revision/snapshot in notifications; never reconstruct ID from source idempotency key |
| notifications → user_filters; notification_delivery_attempts → notifications | Keep notifications-local FKs |
| source_suggestions → users/ingestion_sources | Replace with scalar subject/source IDs and control-local workflow projections; external existence checked through commands/events |

Other cross-owner access: SQLAlchemy search and content direct announcement reads become local projections; asyncpg media joins become owner-local copied media references; admin taxonomy writes to announcements/user_filters become T3; all shared analytics/admin audit inserts become owner-published audit/metric facts. APIs/bot delegate to the responsible owner from architecture-v2.md. No cross-owner FK is replaced with a view, SECURITY DEFINER escape, federated query or unrestricted reader role.

## Roles, default privileges and negative tests (specification, no SQL applied)

For each of listener, source_parser, pre_ai, ai, catalog, search, notifications, identity, control, taxonomy, content, analytics, propose ef_<schema>_owner NOLOGIN, ef_<schema>_migrate LOGIN, ef_<schema>_app LOGIN. Migration login may explicitly SET ROLE only to its owner; runtime cannot inherit or assume that role. Runtime gets only owned-schema USAGE and required table DML/sequence usage, no CREATE/ownership/global read. Separate notification processes can use narrower per-process roles within notifications, without creating a second owner.

Use explicit schema qualification and a safe trusted search_path (pg_catalog plus own schema; no writable public/$user path). Revoke public CREATE as an independently reviewed change. Review role inheritance, CONNECT/TEMP, function execute, extensions and RLS separately. Default privileges must be set for the actual object-creating owner, covering future tables, sequences and functions. UUID defaults and serial/identity sequences must not silently require another owner's privilege. Serialize cluster-wide migrations; each domain migration history must remain backward compatible with deployed workers.

Required isolated tests: impersonate each runtime and prove own CRUD with required sequences, forbidden cross-schema SELECT/JOIN/DML/FK/CREATE, forbidden SET ROLE, forbidden function-based bypass; create a new table/sequence under each migration role and repeat grants checks. Check NOTIFY/advisory-lock assumptions separately if used; no ambient global privileges. These tests have NOT run.

## Migration and rollback gates

Backfill into new owner tables/projections with stable ID mapping and checksums/counts/relationship reconciliation, preserve T0 records, and never infer deployed grants or empty tables from source. Move one writer per cutover; V1 remains authoritative until that domain's gate. Do not dual-write business state without a reviewed reconciliation protocol. Retain old tables and aliases through rollback; reconcile accepted events since the switch before reversing routes. Backup restore/grants/migration rehearsal in disposable infrastructure is required before extraction, not authorized by Task 001. No destructive downgrade, schema movement or stamp is approved.
