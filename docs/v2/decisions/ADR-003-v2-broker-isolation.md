# ADR-003 — Isolate V2 RabbitMQ topic topology from V1 direct topology

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See ../verification/FINAL_V2_REPORT.md and ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

Status: **PROPOSED topology details — distinct V2 TOPIC exchange is an explicit user constraint**
Date: 2026-11-09

## Context

V1 declares `estateflow.events` with exchange type DIRECT and binds existing queue names as routing keys (`src/estateflow/services/queue.py:175–178`, evidence in `event-inventory.md`). The first draft of `docs/v2/event-contracts.md` incorrectly proposed the same exchange name with type TOPIC. RabbitMQ does not allow redeclaring an existing exchange with conflicting types.

## Proposed decision

- Preserve V1 `estateflow.events` DIRECT and V1 `estateflow.events.dlx` unchanged.
- Introduce **new** `estateflow.v2.events` TOPIC exchange, durable, versioned routing keys such as `raw.message.received.v1`, `catalog.listing.changed.v1`, and separately named V2 DLX/retry infrastructure.
- Use durable **consumer-owned queues**, e.g. `v2.source_parser.raw.v1`, `v2.catalog.extracted.v1`, `v2.search.catalog.v1`, `v2.notifications.catalog.v1`. The complete draft core bindings are now enumerated in event-contracts.md; they await approval.
- A consumer's queue is declared/owned by its deployment/topology provisioning, not implicitly by an unrelated producer.
- No in-place bind/redeclare cutover. A compatibility bridge or shadow publisher is explicitly scoped, measures duplicates and has a reversible switch. Existing listener/session safety remains enforced.
- Event envelope includes **event_id** separate from correlation_id; both application-level inbox/outbox and broker publisher confirm/ACK are required. Broker confirms do not make database writes atomic.

## Open protocol decisions

1. Event names/payload versioning and schema registry location.
2. Outbox publisher service vs embedded worker, with lease/fencing and retry semantics.
3. Retry topology (TTL/DLX, delayed exchange or persistent scheduler), max attempts, redacted DLQ and replay ownership.
4. Source edit version and tombstone semantics; parser candidate supersession and canonical revision.
5. Cross-context command/request-reply UX patterns for bot/API/admin actions.
6. How to reconcile old direct events accepted after a dual-route switch.

## Acceptance evidence

An isolated disposable RabbitMQ integration test must declare V1 and V2 exchange types concurrently, publish/consume across all V2 queue bindings, and show that consumer restarts, republish-before-ACK crashes, stale schema, retries and DLQ/replay behave as contracted. This is **Task 002 test work**, not Task 001 implementation.

## Task 001 result

Common envelope + 14 core event schemas + shared types and scenario schema are drafted under contracts/v2/schemas. Consumer queue/event matrix, separate V2 DLX proposal, confirms/mandatory return handling, owner-local outbox/inbox, capped delayed retries, schema parking and replay modes are specified in event-contracts.md. Additional business-domain commands/events are document-only pending per-domain schemas. Scheduler implementation, retry/age budgets, dispatcher placement and queue replication remain OPEN D02; no topology provisioned. The old same-name TOPIC proposal is explicitly superseded, while V1 remains DIRECT.
