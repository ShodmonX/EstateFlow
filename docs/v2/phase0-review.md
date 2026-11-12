# EstateFlow V2 — Phase 0 review and architecture corrections

Status: **Phase 0 static audit accepted as a planning baseline; implementation NOT authorized by this document**.
Baseline: `main` / `addb856a99d24946ef127384121b99de371c017b`, audited 2026-11-09.
Review basis: `architecture-audit.md`, `dependency-matrix.md`, `table-ownership-map.md`, `event-inventory.md`, `current-state.md`. These files contain the cited code evidence. Do not replace the audit with this summary.

## Confirmed project decisions

- **Variant A:** one PostgreSQL instance/database, strictly isolated schema and runtime role per *data-owning bounded context*. Schema sharing inside an explicitly defined domain's multiple worker processes can be temporary and must be recorded.
- **RabbitMQ** carries service-to-service business events and commands. UI-facing HTTP/Telegram adapters remain external ingress interfaces.
- **Incremental extraction**, no big-bang rewrite. V1 remains operational until explicit cutover approval.
- Codex works one approved task at a time, produces evidence, and stops at approval gates.

## Corrections to the original draft specifications

1. `docs/v2/event-contracts.md` currently proposes `estateflow.events` as a TOPIC exchange, but V1 declares that exact name as a DIRECT exchange (`event-inventory.md`, `queue.py`). **Never redeclare it with a different type.** Draft a distinct `estateflow.v2.events` TOPIC exchange; leave V1 topology untouched.
2. Service count must follow **transactional/data ownership**, not the number of existing Docker entrypoints. Keep AI+media co-deployed initially because the current resolver opens Telegram sessions; define a later isolation condition, not an immediate forced split.
3. Initially treat notification filters, matching state, jobs and delivery attempts as **one notifications owner**. Matcher and sender may run as separate processes, with idempotent workflows and no double sends. Split ownership only after due-scheduling, atomic claim and unknown-send recovery are proven.
4. Add owners omitted in the first V2 service chart: **identity/referrals/premium**, **control/source management**, **taxonomy**, **content publishing**, **analytics/technical metrics**. API, admin UI and bot are adapters, not automatic owners of all tables.
5. Keep source **transport subscriptions** owned by Listener and **recipe policy** owned by Source Parser. Preserve a versioned policy/binding snapshot for deterministic replay.
6. Preserve canonical listing, child/parent relations, manual review, merge audit, dedup decisions and announcement media references **inside catalog ownership**. `media` can own transformation/storage operations; do not split catalog aggregate FKs across schemas.
7. V1 `announcement.persisted` does not consistently represent actual canonical identity or every mutation. Catalog must produce actual stored ID + aggregate revision, and publish events for update, merge, review, retraction/tombstone, and media state changes as appropriate.
8. Telegram deletion of one source message does **not automatically mean deletion of a merged canonical listing**. The lifecycle contract must specify source evidence retraction, remaining active sources and listing visibility.
9. Do not assume durable messaging delivers exactly once. Commit+publish requires outbox; consumer-side idempotency requires inbox. Telegram sends additionally have an **ambiguous external-side-effect window** even after outbox/inbox.
10. V1 parser `buffered_thread` fails open, quarantine lacks an automatic replay consumer, and Redis/in-memory state is not durable. Require bounded buffer, retention/privacy/replay and crash semantics before cutover.

## Release-critical defect register from the static audit

- F01–F03: job-publish gap, no due retry dispatcher, unlocked delivery claim/ambiguous sends.
- F04–F05: missing edit revisions/deletion path and volatile listener/album buffering.
- F07: invalid pseudo-compression and non-perceptual similarity signals.
- F09–F10: incomplete catalog mutation events and V1 direct vs proposed V2 topic exchange conflict.
- Additional findings F06, F08, F11–F17 remain in the primary audit. Do not close findings without executable proof.

## Verification limitations

The audit ran static source inspection and reference checks; local `python3 -m pytest` failed because `pytest` was unavailable. No live database, RabbitMQ, Telegram, object storage, container build or end-to-end tests were run. Production roles, grants, backups, retention and operational behavior are not verified. Do not use "VERIFIED" source findings as claims that a failure occurred in production.

## Immediate recommended sequence

1. **Task 001: contract/ownership freeze (documentation + JSON schemas + sanitized fixtures ONLY).** Ratify provisional ADRs, identity/edit/delete/album/reparse semantics and isolated V2 exchange; define consumer contracts and the migration cutover gates. No application changes.
2. **Task 002: isolated durable foundation**, after separate approval. Implement owner-local PostgreSQL inbox/outbox, producer confirms, consumer-owned queues, retry/delay/DLQ and integration tests on disposable PostgreSQL/RabbitMQ.
3. **Task 003: durable ingress and parser shadow**, after separate approval; one component per task. Never run parallel live Telegram session listeners without a fencing design.

## Task 001 acceptance gate

Approved domain ownership and cross-domain reference policy; no unowned legacy table; validated contract drafts; explicit source lifecycle and aggregate revision rules; V1/V2 topology separation; failure-mode test matrix; sanitized fixtures for create/edit/edit/delete, delete-before-create, album late member, digest reparse, review, merge, media ready/failed and replay; updated `current-state.md`. Any unresolved policy remains marked **OPEN / REQUIRES APPROVAL** and blocks implementation.
