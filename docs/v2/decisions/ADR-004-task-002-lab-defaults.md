# ADR-004 — Task 002 isolated foundation defaults

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See ../verification/FINAL_V2_REPORT.md and ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

**Status: ACCEPTED FOR TASK 002 SYNTHETIC LAB ONLY, if user submits the Task 002 Codex prompt.** Not a production-wide D02/D03/D07/D08 approval.

## Decision

Use **Variant A**, a separate Docker Compose project with a dedicated PostgreSQL database and RabbitMQ broker, no host ports, no shared network/volumes with EstateFlow V1. Implement a producer-owned transactional outbox and consumer-owned inbox/retry/parking ledger, publisher confirms, manual ACK, consumer-owned durable queue and explicit DLQ/replay. New V2 topic exchange is `estateflow.v2.events`; V1 direct exchange is untouched. Prefer owner-local PostgreSQL scheduled retry over a delayed-message plugin for this foundation.

Lab-only default limits: 6 total attempts per stage; delays 5/15/45/135/405 seconds with ±20% jitter; 60s lease; 10s confirm timeout; prefetch 10; 10-minute revision-gap reconciliation; 24-hour synthetic event age; 64 KiB synthetic event body. No automatic inbox or parked-event purge during Task 002. Single-node broker: durable but not HA. No user-visible or external sends.

## Rationale

The Phase 0 audit identifies commit/publish holes, immediate retries, shared topology declarations and no consistent event identity. Task 001 defined schema and lifecycle drafts but did not exercise runtime crash behavior. An isolated synthetic foundation proves the reliability primitives without prematurely ratifying legacy table migration, media or notification policy.

## Trade-offs and unresolved decisions

- A local outbox + inbox provides atomic local DB effects and duplicate suppression, **not** exactly-once broker delivery or external side effects.
- PostgreSQL scheduled retries add database work but avoid RabbitMQ delayed-exchange plugin dependencies; production budgets and ownership are still OPEN.
- Single-node RabbitMQ proves persistence across ordinary restart, not HA or replicated durability. Production queue topology/replication must be separately approved.
- Aggregate version rules in this lab use synthetic contiguous revisions; actual Telegram observation ordering, tombstones, reparse and canonical listing merge semantics remain OPEN under D03.
- Lab data is synthetic; real event/PII retention, erasure and replay authorization remain OPEN under D07. No real user notification/content sends (D08).
- The 26-table / 12-context ownership mapping (D01) is not implemented or silently approved by this lab; it is exercised through two new synthetic owner schemas only.

## Supersession

When Task 002 test evidence is reviewed, a separate production architecture ADR may adopt or change these parameters. This ADR does not override the Task 001 decision register for production policy.
