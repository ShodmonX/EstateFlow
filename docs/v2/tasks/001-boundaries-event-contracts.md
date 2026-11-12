# Task 001 — Ownership decisions, V2 event contracts and replay fixtures

Status: **DRAFT DELIVERABLES COMPLETE / AWAITING USER REVIEW** (2026-11-09). User authorized Task 001 only. This is documentation/contract design, not runtime work or approval of Task 002/full Phase 1.
Prerequisites: Phase 0 audit package present; user-confirmed Variant A and RabbitMQ migration; clean understanding of `git status`.

## Objective

Turn the evidence-backed Phase 0 findings into an internally consistent, implementation-ready **draft** V2 specification. Resolve documentation contradictions before any runtime/service refactor. Do not silently invent business policy.

## Read (focused order)

1. `AGENTS.md`, `docs/v2/current-state.md`, `docs/v2/phase0-review.md`.
2. This task, `docs/v2/architecture-audit.md` sections 2–12, `docs/v2/table-ownership-map.md` sections 1–3, `docs/v2/event-inventory.md`.
3. Existing draft `docs/v2/architecture-v2.md`, `event-contracts.md`, `database-ownership.md`, `implementation-plan.md`, ADR-001.
4. Only the source code files necessary to settle contradictions, using file:line evidence. Reuse the audit instead of repeatedly reading the whole repository.

## Work packages

### A — Bounded contexts and PostgreSQL ownership

- Propose one accountable owner for every one of the **26 legacy application tables** listed in `table-ownership-map.md`; do not infer current physical schema from the `public` candidates.
- Map all current cross-owner reads, writes, FKs and transaction groups T0–T6 to event-fed projections/commands or domain consolidation; distinguish immediately executable vs deferred transitions.
- Explicitly include Listener, Source Parser, Pre-AI, AI+Media (initially co-deployed), Catalog, Search, Notifications, Identity, Control/Source Management, Taxonomy, Content and Analytics. Describe public/admin/internal APIs and Telegram bot as adapters, and assign each command to a responsible owner.
- Document **single PostgreSQL instance**, independent runtime roles, NOLOGIN owner roles, per-owner migration role, sequences/default privileges, safe search_path, and negative cross-schema grants tests. DO NOT apply SQL.
- Specify the eventual split condition for AI/media and optional notification worker separation; physical process boundaries need not equal data ownership boundaries.

### B — Event identity, lifecycle and invariants

Define an event envelope and version strategy for: event_id, event type/version, producer, occurred_at, correlation_id, causation_id, aggregate_id/version (when meaningful), source/channel/message identity, source revision and parser policy revision. Do not fabricate globally ordered Telegram revisions.

Cover **create → edit1 → edit2 → delete**, delete-before-create, late album member, media unavailable/ready/failed, digest fan-out, reparse with different recipe, duplicate/reordered message, manual review, parent-child merge, multi-source retraction and canonical visibility. Precisely distinguish:

- transport/source message identity (evidence), album membership, parser candidate identity/version, canonical listing identity;
- new business event vs redelivery (same event_id) vs replay operation (original identity retained, new operation metadata);
- source tombstone/retraction vs merged canonical publication state;
- producer/consumer responsibilities for stale versions, idempotency and compensation.

### C — Messaging foundation specification (NO implementation)

- New durable TOPIC exchange, proposed `estateflow.v2.events`. V1 `estateflow.events` remains DIRECT and untouched.
- Define a routing-key namespace, consumer-owned queue naming, explicit per-event producer/consumer table; include control-domain and projection use cases rather than only the happy-path pipeline.
- Document queue declaration ownership, publisher confirms, durable messages, manual acknowledgments, outbox/inbox atomicity boundaries, bounded exponential retry with delay + DLQ and authorized replay. Reject/park unknown schema versions with safe diagnostics.
- Address RabbitMQ commands for cross-domain user/admin mutations. Specify how synchronous UX obtains a result without unauthorized direct database access. Treat request/reply with timeouts as a **design choice**, not an automatically approved default.

### D — Draft JSON Schemas and sanitized fixture corpus

Create machine-readable *draft* JSON Schemas under `contracts/v2/schemas/` for the common envelope and the smallest core event set needed to test identity/lifecycle: raw received/edited/deleted, parsed candidate, parser rejected, prechecked candidate, extracted candidate, catalog changed/retracted. Add media state contracts if the story requires them. Use consistent naming, `$schema`, `$id` and versioned filenames; document any intentionally unconstrained payload fields as TODO rather than silently accepting arbitrary content.

Create fake/synthetic JSON examples under `tests/fixtures/v2/contracts/` for positive and negative cases in work package B. **Do not paste real Telegram messages, phones, session material or real secrets.** Clearly mark expected consumer behavior, including rejection/ignore/reconciliation.

Validate all JSON with Python standard library. If `jsonschema` is already available, run schema/fixture validation; otherwise report it as not executed, and do not claim schema compatibility tests passed.

### E — Reconcile and record decisions

- Update `docs/v2/architecture-v2.md`, `docs/v2/event-contracts.md`, `docs/v2/database-ownership.md`, `docs/v2/implementation-plan.md` to agree with Phase 0 evidence. Make old superseded claims explicit; especially remove `estateflow.events` TOPIC proposal.
- Add targeted ADRs under `docs/v2/decisions/`: ownership, V2 broker topology, source/candidate/canonical identity and lifecycle, and delivery unknown-send policy. Mark each ADR **ACCEPTED only if already explicitly decided**; otherwise PROPOSED or OPEN.
- Record high-risk choices with trade-offs, alternatives and what concrete proof would justify them.
- Update `docs/v2/current-state.md` with changed paths, commands run, verification result, open decisions and exact proposed Task 002. **Do not state Phase 1 complete or Task 002 approved.**

## Hard boundaries / safety

**Allowed:** V2 markdown docs/ADRs/task files, draft V2 JSON Schemas and synthetic fixture data, plus `current-state.md`.

**Forbidden:** application code, current V1 contracts/events, Alembic, SQL schemas, Docker/Compose, CI, deployment scripts, secrets, production or live network services, installation of dependencies, live Telegram sessions, external sends, Git commit/push/branch force actions. Do not delete/overwrite untracked user files.

If an essential product policy cannot be inferred from evidence, mark **OPEN / REQUIRES USER APPROVAL**, document possible alternatives and stop short of an unsafe assertion. Do not ask for permission mid-task; provide a focused decision list at the end.

## Definition of done

- Every table has exactly one proposed owner and its cross-owner references are accounted for.
- No V1/V2 exchange-type conflict remains in V2 drafts.
- No event relies on one ID to represent transport source, parser candidate and canonical entity.
- At least the lifecycle fixture classes specified above exist with expected outcomes; JSON parses without errors.
- Edited/deleted/merged canonical state can be represented without a direct read of another owner's DB schema.
- `git diff --check` passes where applicable; `git status --short` and unchanged application code are reported.
- Findings use `path:line` references or explicitly say when only a design proposal is available.
- Open decisions are clearly identified and **Task 002 is not started**.

## Final report format

1. Proposed owner/domain table and main differences from initial V2.
2. Proposed event types, envelope, exchange, queue topology and lifecycle semantics.
3. Files created/edited; command-by-command validation and its limits.
4. Decision list requiring owner approval, ordered by implementation impact.
5. Task 002 prerequisites and blocked risks. STOP.


## Task 001 delivery evidence (2026-11-09)

- A: all 26 ORM tables reconciled to exactly one proposed owner; 26 declared FK edges (7 cross-owner) and T0–T6 transitions specified in database-ownership.md. All omitted domains and adapter command owners are explicit. Initial AI/media and notifications constraints retained.
- B: event-contracts.md and ADR-004 specify source/album/candidate/canonical identity, locally scoped revisions/uncertain order, two edits/tombstone, late members, digest replacement, reparse, duplicate/reorder/replay, review/merge and media semantics. D03–D05 product choices remain OPEN.
- C: V1 DIRECT remains; distinct V2 TOPIC and consumer queues/producer-consumer matrix, document-only domain commands, confirms/outbox/inbox/ACK/delay/DLQ/replay and UX alternatives specified. D02/D06 choices and command schemas remain explicitly gated.
- D: 17 schema files and 25 synthetic scenario files (47 event instances). Standard-library strict JSON parsing passed for 42 files. Existing jsonschema 4.10.3 validated Draft 2020-12 schemas and accepted 38/rejected 9 event instances as expected, offline with format checks. Domain-state expectations are not executed tests.
- E: architecture, ownership, events, implementation gates and ADR-001–005 reconciled; decision register D01–D08 and validation/change manifest recorded. No product choice silently accepted. See task-001-validation.md for exact commands/results/limitations and changed paths.

Task deliverables are complete for review; approval-dependent acceptance of owner allocations/lifecycle policies is still pending. No finding closed as runtime-fixed. Task 002 remains NOT APPROVED / NOT STARTED. STOP after this task.
