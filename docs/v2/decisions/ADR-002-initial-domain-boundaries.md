# ADR-002 — Initial V2 bounded contexts and deployable processes

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See ../verification/FINAL_V2_REPORT.md and ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

Status: **PROPOSED — reconciled by Task 001; full owner-map approval pending**
Date: 2026-11-09

## Context

Phase 0 finds 26 V1 application tables across transactions T0–T6, shared repositories/runtime, and API/bot adapters that currently construct many unrelated domain services. Splitting each Docker process into an independently owned database schema at once would break cross-domain writes and foreign keys. See `table-ownership-map.md`, `dependency-matrix.md`.

## Proposed initial ownership

| Bounded context / schema | Primary legacy data or new V2 state | Initial deployment note |
|---|---|---|
| `listener` | ingestion sources **transport** fields; listener accounts, assignments, assignment audits; durable Telegram ingress/album journal | One fenced Telegram session owner per account |
| `source_parser` | parser policy/configuration/history, quarantine, durable buffer/candidate lineage | Separate worker, Telegram-session-free |
| `pre_ai` | persisted cheap dedup signals, decisions and consumer processing | Separate worker |
| `ai` | extraction runs and AI-owned processing state; media processing operational state initially | AI and media initially co-deployed due to session dependence |
| `catalog` | announcements, announcement_media references, merge/review/dedup decisions, aggregate revisions | Only canonical-listing writer |
| `search` | event-fed read model, search indexes/projections | Source catalog data never joined cross-schema |
| `notifications` | saved filters, matching/jobs, delivery attempts, due schedules | Matcher and sender may be separate processes under one owner; no multi-claim |
| `identity` | users, referrals, audit and premium awards | Direct API/bot SQL to be removed as adapters are migrated |
| `control` | feature flags, audits, source suggestions/approval workflow, source-management orchestration | Must reconcile source suggestion FKs to users/sources |
| `taxonomy` | audience_tag_types, changes/merge commands | Catalog/filter remaps become owner-local reactions |
| `content` | content_channel_posts, scheduled publishing and send ledger | Explicit unknown-send policy |
| `analytics` | analytics_events, technical_metric_events, durable event-fed metrics | Central projection from owner-published events, not shared insert table |

The exact 26-table proposal is now in database-ownership.md and the reconciled owner column of table-ownership-map.md. parsing_logs and processing_jobs each have ai as sole accountable owner while T0 runtime contents remain NOT VERIFIED; do not drop them. admin_audit_events belongs to control for legacy import and central event-fed audit projection; new authoritative audits remain owner-local, never shared writes. AI/media initial co-deployment and notifications grouping are explicit user constraints; other allocations remain D01 proposals.

## Invariants

- Database **instance** is shared; data ownership and runtime privileges are not.
- One context is the sole source of truth for a record; independent workers within that context can be deployed separately if they obey shared domain compatibility.
- No cross-owner FK, JOIN, ORM import, DML or shared transaction in the **target architecture**. These restrictions are migration goals, not claims about V1.
- Catalog owns lifecycle of source evidence within canonical listings; Listener owns transport/source configuration. A source ID can cross boundaries as a scalar identifier/event reference, not a cross-schema FK.
- Bot/Public API/Admin API are **adapters**, with rights limited to their own auth/session state and command publication/projections; they are not omnipotent data owners.
- Do not split domains merely to maximize the count of microservices. Trace each transactional invariant first.

## Alternatives and tradeoffs

- Per-container database schema immediately: stronger visual separation but breaks V1 transactions, creates fragile migrations and unknown consistency.
- Fully isolated PostgreSQL instances: beyond chosen Variant A, higher operational and distributed-consistency cost.
- AI/media split immediately: clean conceptual ownership, but currently introduces Telegram credential/media-readiness dependencies; defer until media source access contract and codec correctness have integration proof.

## Acceptance evidence

Approved table ownership and FK transformation map; negative role/grants tests; migration/backfill/reconciliation plan; no business logic imports across bounded contexts in migrated services; independent image and service health verification. **No production change approved here.**

## Task 001 reconciliation

T0–T6 transitions, every FK class, cross-owner reads/writes and per-schema role/default-privilege tests are specified in database-ownership.md. Design now; execute only after separately approved implementation. No separate initial media schema or matcher/delivery table owner. D01–D08 approval register is task-001-decisions.md.
