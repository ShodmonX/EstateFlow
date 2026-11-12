# EstateFlow V2 — One-shot isolated system migration

**Authorization:** User authorizes Codex to implement the entire V2 system end-to-end **inside a new isolated local Docker Compose project** without requesting interim approvals. Codex owns task planning, coding, fixture creation, migrations, tests, repair loops, and final documentation. **Not authorized:** access to production, live V1 infrastructure, real Telegram/LLM/storage credentials, real user sends or destructive host actions.

**Read order:** `AGENTS.md`; this plan; `docs/v2/current-state.md`; Task 001 files and `docs/v2/task-001-decisions.md` (locate actual path); Phase 0 audit+inventories; `docs/v2/architecture-v2.md`, `event-contracts.md`, `database-ownership.md`; `docs/v2/tasks/002-durable-foundation.md` and its fault matrix; `docs/v2/plans/V2_ACCEPTANCE_MATRIX.md`; `docs/v2/plans/V2_OPEN_DECISIONS_DEFAULTS.md`; `docs/v2/plans/V2_ISOLATION_RUNBOOK.md`; master prompt.

## Outcome

A coherent, **runnable** EstateFlow V2 platform: independent deployable components with clear context ownership, a single *new* lab PostgreSQL instance but isolated schemas/roles, RabbitMQ V2 topic event bus, durable contracts/outbox/inbox, listener/parser/dedup/AI-media/catalog/search/notifications, identity/control/taxonomy/content/analytics, HTTP APIs/bot interfaces and front-end integration as evidenced by V1; executable mock-mode end-to-end tests, an operator runbook, repeatable lab schema migration, and a final manifest of verified/unverified functionality. The code must be maintainable and avoid duplicated business logic.

**No big-bang production cutover.** A complete isolated V2 implementation may still have unverified live-provider integrations or unresolved product-policy choices. Never represent offline mock parity as proven production parity. V1 stays authoritative and unchanged.

## Source-of-truth and conflict order

1. User-confirmed decisions: Variant A, 1 PostgreSQL instance with service-owned schemas/roles; RabbitMQ, incremental separation; AI/media initially co-deployed; notification filters/jobs/attempts owned by notifications; isolated new Compose; user authorizes full isolated migration now.
2. Actual repository implementation and Phase 0 evidence for *existing* behavior (no silent removal of functionality).
3. Task 001 **existing** JSON schemas, 25-scenario fixtures and ADRs for drafted event contracts. Inspect and reconcile, **do not replace** them wholesale or regenerate conflicting envelopes.
4. `D01–D08` recommendations and this plan's **explicit LAB-ONLY provisional defaults**, which are not approvals for production policy.
5. Earlier Task 002 spec for the foundation. It is superseded **only** regarding its prohibition on proceeding after Task 002. Its isolation, fault test and technical requirements remain in force.

When code, audit and draft disagree, write a reasoned entry in `docs/v2/verification/V2_DEVIATIONS.md`, preserve evidence, select the safest lab behavior, and continue. Do not hide a disagreement by weakening tests or silently removing a feature.

## Monorepo layout — adaptable, not mandatory

```text
infra/v2/compose.yaml                  # only V2 stack; explicit -p estateflow-v2-isolated
infra/v2/compose.test.yaml             # isolated test-only config
infra/v2/.env.example                  # dummy configuration; never secrets
infra/v2/docker/                     # per-service Dockerfiles / shared immutable base
v2/contracts/                         # adapters for Task 001 contracts; don't duplicate schemas
v2/shared/                            # tiny contracts, telemetry, transport helpers only
v2/foundation/                        # Task 002 outbox/inbox/dispatch/DB/broker
v2/services/{listener,source_parser,pre_ai,ai_media,catalog,search,notifications,identity,control,taxonomy,content,analytics}/
v2/apps/{public_api,admin_api,internal_api,bot}/
v2/infra_migrations/                 # owner-local migrations and roles
v2/tests/{unit,contract,integration,e2e,fault,security,parity}/
v2/fixtures/                        # only synthetic and sanitized
frontend/ or v2/frontend/              # maintain public UI/API parity
admin-frontend/ or v2/admin-frontend/  # maintain admin UI/API parity
scripts/v2/                           # deterministic dev/test/inspect commands
```

Prefer the repository's existing V2 structure if already present. Avoid moving V1 files or renaming existing Compose. Physical microservice count can be lower than bounded-context count *only where explicit temporary domain co-deployment does not cross data ownership*; give each context an independent database runtime role and migration identity. `AI+media` may remain one deployment. Matcher and sender are independent process roles within notifications domain. Admin/public/internal API and bot are adapters and must never receive broad cross-owner SQL grants.

## Execution strategy: autonomous consecutive tasks

Codex must maintain its own implementation checklist and continue through **all** sections below without stopping for approvals. Update `docs/v2/verification/V2_PROGRESS.md` and `docs/v2/current-state.md` at each milestone with verified commands/results and next step; if the Codex context resets, recover from these on-disk checkpoints rather than restarting the whole repository analysis. Complete each phase with reproducible tests and record blockers. Repair failures within scope. A blocker in one workstream must not halt unrelated safe work; proceed with mock modes. No placeholders advertised as working.

### M00 — Baseline, inventory and contract reconciliation

- Record `git status --short`, branch/HEAD and untracked user files. Do not discard or clobber user work. Identify Task 001's 56-path change manifest and the current 17 schemas, 25 scenarios and 42 JSON files; validate actually existing files, never assume counts prove correctness.
- Freeze current V1 API routes, admin/bot workflows, source configuration, parser recipes and edge cases, extraction fields, search semantics, saved-filter matching, premium/referral/taxonomy/content/analytics flows, media semantics. Establish an evidence-backed endpoint/feature parity spreadsheet in Markdown (`V2_PARITY.md`) with source references, owners and test IDs.
- Ratify the **lab-only** table-owner mapping from existing Task 001 docs; if inconsistent, fix V2 docs and record deviation. Keep 26 legacy tables accounted for, distinguish new owner schema layout from physical migration of legacy data.

### M01 — Foundation and private Compose environment

- Implement Task 002 in full, including all FND-01–FND-34 tests wherever possible. Safe all-private Compose: no host-published ports; unique project/network/volumes; PostgreSQL (lab), RabbitMQ (lab), migration bootstrap, deterministic test runners. Optional UI access via `docker compose exec` or explicitly documented local-only opt-in override, not default exposed interfaces.
- Owner-local SQL roles + schema migrations; outbox dispatcher publisher confirms; inbox transaction and version conflict handling; retries, durable parking/DLQ, manual replay audit, dead letter inspection, metrics and health. No V1 imports in foundation. One synthetic demonstration executed before business consumers.
- Lab-only pinned versions/image digests where feasible, resource limits, graceful shutdown and durable volume cleanup guardrails.

### M02 — Listener transport and source policy

- `listener` owns Telegram transport source metadata/assignments/session lease/fencing and persistent ingress/update/album journal. Consume **synthetic Telethon-like updates** and emit raw received/edited/deleted; no real login, session access, network Telegram call or background auto-connect.
- Implement creation, repeated edits, delete-before-create, delete-without-text, late album item, duplicate, order uncertainty and restart recovery. Use local observation sequence only, never invent Telegram-global revision. Source transport policy and parser recipe versions snapshot separately; no parser business imports from Listener.
- Source Parser owns recipe history, policy snapshots, digest fanout, quarantine/replay and bounded durable thread/album handling. Failed buffer policy must fail closed/park; operator replay audited and no silent loss. Respect original source/candidate lineage.

### M03 — Cheap dedup and AI/media

- `pre_ai`: persistent exact/near dedup signals and decisions; don't present Telegram reference hash as image perceptual hash. Edits/deletions bypass destructive cheap dedup suppression; per-version idempotency; bounded state and retention placeholders remain disabled pending privacy approval.
- `ai_media` (initially co-deployed): owner-local extraction run audit, retry/terminal state, actual schema validation, OpenRouter-compatible adapter behind *fake offline provider by default*. No real tokens needed. Idempotent run identity and cost/timeout metrics. Separate media storage protocol, fake object store and fake authorized media fetch. **Real codec** for JPEG/PNG/WebP transformation and actual decodable output, EXIF/orientation, size/dimension controls. Mark pending/failed media explicitly; no pseudo-compression/hash. Use a correctly implemented content/perceptual comparison if included; otherwise remove misleading naming.

### M04 — Catalog authoritative listings

- `catalog`: source evidence/candidate lineage -> parent/child canonical listings, merges, dedup decisions/audit, manual review, announcement_media *attachment references*, aggregate revision. Atomic transactional record updates + outbox for **every** canonical mutation, including edit, merge, review, retraction, availability, media change.
- Multi-source tombstone semantics: removing one source record does not retract a canonical listing while other active evidence remains. For one-source last retraction, lab default hide listing, preserve tombstone + audit. No out-of-order reactivation. Manual correction/reparse has explicit actor+reason audit, revises deterministic projections.
- Reruns/reparse may replace an earlier candidate generation via explicit supersession manifest; do not assume digest index is stable. State machine and negative concurrency tests required.

### M05 — Search and user-facing read projections

- `search`: separate schema and event-fed projections; rebuild from catalog snapshot/event replay without cross-schema SELECT/JOIN. Preserve V1 business behavior for price unit/monthly/one-time, rent/sale, location/tag, images, active/review visibility, pagination/filter/order. Distinguish query failure from empty result. Strict projection revision monotonicity; shadow parity fixture diffs.
- All query operations have owner-scoped read paths through API or domain interface; no public API direct access to catalog/storage tables.

### M06 — Notifications + identity

- `notifications`: saved filters, matching, jobs, attempts and user-scoped send preferences; distinct matching and delivery processes may share owner schema. Ingest catalog changed/tombstone and identity/consent projections with outbox/inbox. Atomic due scheduling/claim/lease and rate limiting, retry/unknown external side effect ledger.
- **Fake sender ONLY** by default and in every test: deterministic send success/failure/429/timeout, ambiguous result parked, no automatic resend on unknown. One initial notification per `(user, filter, canonical listing)` unless explicit opt-in to edited listing alerts. Check consent/premium at send time, stale projection parks, no exactly-once Telegram guarantee.
- `identity`: users, referrals, referral premium awards, premium entitlement state and activation transaction stay together. Negative access tests and idempotent commands; publish immutable identity/entitlement projections and fake actor authentication.

### M07 — Control, taxonomy, content, analytics

- `control`: admin audit, feature flags, source suggestions and approval workflows, source management orchestration; owner actions via commands and outbox. Listener owns transport `ingestion_sources` record; control may not update it directly. Approvals to grant premium are delegated to identity with causation/compensation.
- `taxonomy`: tag types, versioned rename/remap with catalog and notification projection updates (no cross-owner shared SQL transaction).
- `content`: scheduled channel publication, durable jobs/ledger and fake sender; hold ambiguous send outcomes for audited resolution, no production publishing.
- `analytics`: technical events and product analytics as event-fed projections, no unrestricted shared insert sink; secure redacted diagnostics. `parsing_logs` and `processing_jobs` legacy mapping needs preservation or explicit migration-neutral records, never silent drop.

### M08 — Public/admin/internal HTTP, Telegram bot, UI compatibility

- Build independently deployable public/admin/internal APIs and bot adapters with scoped domain commands/queries. No cross-owner ORM and no import of V1 business service implementations at runtime. Implement async command acceptance + operation ID and status lookup, consistent authz/actor delegation, errors/rate limits. Synthetic Telegram bot adapter only: no real poll/send/webhook without future explicit operator enablement.
- Wire existing frontends to V2 using isolated build/config or implement V2-compatible adapters. For every V1 route/feature list document parity status `WORKING`, `MOCK_ONLY`, `DEFERRED` or `MISSING`, with tests. No fake green status by returning stub data from unfinished handlers. Preserve WebApp auth semantics in offline tests; ensure secure token verification, bounded clock skew and no trust in arbitrary forwarded user IDs.

### M09 — Clean-lab migration, end-to-end and recovery

- Compose `up --build`, migration bootstrap, deterministic synthetic seed and complete workflow: raw -> parser -> pre-AI -> fake AI/media -> catalog -> search -> filter match -> fake send; then edit, digest, album, merge/review, deletion, reparse and replay; verify all owner DBs, queues, projections and API outcomes.
- 26-table compatibility mapping: where legacy rows are transformed to owner-owned V2 entities, implement **synthetic** backfill/mapping, reconcile keys, counts and FK-to-event/projection semantics. Provide a **dry-run** legacy real-data migration plan only; no production data extraction or actual legacy DB connection. Self-contained rollback/restore rehearsals on disposable volumes/snapshots only.
- Execute contract, unit, integration, fault, security, schema/role, concurrent-consumer, projection rebuild, smoke, parity and UI tests. No skipped/nonexistent tests counted as passed. Offline fake providers must not mask adapter errors; contract-test real external adapter interfaces with mocks. Test leak detection: zero network egress to Telegram, OpenRouter, external R2 and production DB. If egress isolation can't be technically proven, explicitly mark it unverified.

### M10 — Hardening, runbook and final handoff

- Run Ruff/typing/test/build checks in appropriate V2 environment, audit dependencies, verify reproducible startup, Compose isolation and secret hygiene, ensure non-root containers where feasible, deterministic migrations, health/readiness for throughput not merely PID, service-restart/recovery, monitoring and crash-point logs.
- Write final docs at `docs/v2/verification/FINAL_V2_REPORT.md`, `V2_TEST_RESULTS.md`, `V2_PARITY.md`, `V2_DEVIATIONS.md`, `V2_OPEN_ITEMS.md`, and update `docs/v2/current-state.md` and architecture docs with actual as-built status.
- Final message: what runs, exact command+exit code+counts, expected vs observed V1 parity, missing/mock integrations, high-severity unresolved issues, architecture decisions chosen provisionally, changed paths and reproducible commands, plus **next repair prompt suggestions only**. Never claim all passes if tests failed.

## Non-negotiable engineering invariants

1. Every context owns its writes and PostgreSQL schema/role; no cross-context SQL, ORM, FK, sequence grant, transaction or shared service business import. A process containing multiple context workers uses distinct DB clients and roles.
2. Internal messaging uses versioned Task 001 JSON contracts; V2 `estateflow.v2.events` TOPIC is on isolated broker; V1 `estateflow.events` DIRECT remains untouched. Consumer queues are independently named, binding ownership explicit.
3. At-least-once delivery; outbox atomically committed with domain change; inbox idempotent locally, consumer ACK only after durable effect/retry/parking; version conflict and out-of-order handling documented.
4. All source/listing identities distinct; edits, deletions, late albums, digest/reparse and merges are events with lineage and no silent state loss.
5. No real external sends, sessions, credentials or production data in this run. Default-deny external providers; fake adapters are explicit, labeled and tested.
6. Code and docs are both required. Avoid endless speculative refactoring; use test-driven minimal implementations with measurable gates.
7. Never solve a failing test by disabling it, weakening an invariant, returning success without data, or marking a mock result as production parity.
8. No blanket approvals of D01–D08 for production. Record lab-only provisional choices, alternatives, tests and later decision requirement.

## Final definition of done

A fresh isolated Compose stack can be built, started, migrated, seeded, exercised end-to-end, restarted, and inspected **without existing EstateFlow V1 or any external provider**. Every implemented feature has executable assertions, every V1 feature has explicit parity status, and failing/missing work is captured with an actionable file/line-level issue. If a gate is blocked, complete the rest safely and report PARTIAL rather than claiming full completion. Do not proceed to production migration or send anything real.
