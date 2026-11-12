# EstateFlow V2 — task and phase gates

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See verification/FINAL_V2_REPORT.md and decisions/ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

Updated 2026-11-09. **Task 001 only is authorized for documentation/draft contracts. Task 002 and the remainder of Phase 1 are NOT APPROVED.** No task/phase proceeds automatically after a document or schema validation result.

## Phase 0 — audit

Task 000 completed as static audit and accepted as planning baseline in phase0-review.md. Source findings, 26-table inventory, event/state inventory and verification limitations remain in architecture-audit.md, dependency-matrix.md, table-ownership-map.md and event-inventory.md. Live grants, production schema, broker/Telegram behavior and restore readiness remain unverified.

## Phase 1 — foundation, split into separately approved tasks

| Task | Scope | Status / exit gate |
|---|---|---|
| 001 | Reconcile bounded contexts, all 26 table owners/T0–T6/FKs, draft lifecycle/topology, JSON Schemas and synthetic fixtures, ADRs and choices | Authorized drafting; deliverables complete subject to recorded validation evidence and user review. D01–D08 remain OPEN; validation is not approval |
| 002 (proposed) | One isolated synthetic producer/consumer pair: owner-local outbox/inbox, explicit confirms/mandatory routing/manual ACK, approved delay/DLQ/replay topology, schema validation and durable parking, grants/sequence/default tests | NOT APPROVED / NOT STARTED. Approve D01/D02 foundation subset, identity/version/conflict rules and synthetic replay/privacy scope. Disposable DB/broker, dependency/network permissions and operational budgets separately authorized |
| Later foundation tasks | Complete domain command/result schemas, domain listing parity and authorization contracts, observability/retention and rollback tools | Each requires scoped approval; no extraction until applicable D03–D08 resolved |

Task 002 proof: kill before/after commit, after confirmed publish before sent-mark, after effect commit before ACK; stable event ID/hash under retries; duplicates produce one local effect, unknown versions/conflicts park durably, delay/retries cap, DLQ replay authorized, no external sends. Demonstrate V1 DIRECT and V2 TOPIC concurrently under distinct names. Prove forbidden schema reads/writes/role escalation fail and future tables/sequences receive intended grants. A happy-path unit test is insufficient. Record exact commands/output, rollback and limitations.

## Phase 2 — Listener and Source Parser

Proposed Task 003 is split into one component per approved implementation task: durable ingress/session fencing and album member journal first, then independent policy/candidate/parser buffers and shadow processing. Requires approved lifecycle/order/tombstone/late album/quarantine/buffer caps, real transport reconciliation design and retained snapshot/replay policy. Preserve one fenced live session owner; no parallel live listeners. Gate: restart/lost signal/reordered edits/delete-before-create/late member/digest replacement parity and agreed labeled-corpus thresholds. Shadow never sends notifications/content.

## Phase 3 — Pre-AI, AI+Media and Catalog

Persist pre-AI suppression/evidence without discarding source lineage. Persist AI runs and co-deployed media jobs; real image codec correctness, media state/capability expiry and readiness policy precede cutover. Catalog owns evidence/review/merge/attachment transaction plus outbox and actual canonical revisions; every mutation emits a complete snapshot. Complete V1 listing field parity before production contracts. Extract one owner per task, avoid simultaneous rewrites. Gate: restart/replica dedup, stale media, concurrent merges/review and failed publication tests plus reconciled backfill. A later media split is conditional, not scheduled by default.

## Phase 4 — Search, Notifications and adapters

Search/content/notifications read local projections; query price/pagination/tags/media semantics match reviewed fixtures. Notifications owns filters/jobs/attempts across matcher/sender processes; implement due scheduling/claims/leases and approved unknown-send recovery before sender scale-out. Identity/referral/premium, control/source approvals, taxonomy remap, content and analytics each migrate with explicit local transactions/commands/projections from database-ownership.md. APIs/admin/internal/bot are adapters with scoped credentials and operation-result UX (D06). Gate: no unauthorized cross-owner access, declared projection lag, consent/entitlement checks, retry/unknown outcomes and shadow external-send prohibition. Do not promise exactly-once Telegram delivery.

## Phase 5 — cutover

Backup/restore rehearsal, per-owner backfill/checksum/relationship/event reconciliation, queue checkpoint/drain/replay, alerts and rollback drill. Choose a single authoritative writer/sender per domain; retain V1 data/routes for approved stabilization period. Rollback retains and reconciles newer writes/events before switching routes; never delete volumes or drop owner data to recover. Gate: evidence package and explicit user approval per cutover.

## Universal gates

No cross-owner imports/SQL/FKs/shared business transactions in migrated components. Explicit owner-local role/migration/default grants. Producer/consumer version/schema/authorization compatibility and duplicate/reorder/crash proof. Retention supports the replay horizon. Work has code/migrations/tests/operational evidence only when that implementation is actually authorized; Task 001 intentionally changes no runtime. Update current-state.md and stop at each approval boundary.
