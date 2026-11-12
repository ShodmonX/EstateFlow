# ADR-001 — V2 platform boundaries

> 2026-11-10 isolated implementation note: M00–M10 lab execution was authorized. See ../verification/FINAL_V2_REPORT.md and ADR-006-isolated-v2-as-built.md for actual code/test status. Earlier design sections remain drafts; they are not proof of production policy, physical legacy migration, broker producer authentication or complete V1 parity. D01–D08 remain LAB_PROVISIONAL.

Status: ACCEPTED IN PRINCIPLE for explicitly confirmed platform choices only; implementation and detailed ownership remain separately gated
Date: 2026-11-09

Decision: Monorepo with independently deployable services. RabbitMQ is the transport for inter-service business events. One PostgreSQL instance is shared, with service-owned schemas and roles. No direct cross-service ORM/repository imports or cross-schema SQL access. Prefer incremental extraction over big-bang rewrite.

Consequences: Simpler infrastructure than per-service databases; still a shared PostgreSQL failure domain. Contracts, idempotency, projections and migration orchestration become first-class responsibilities. Eventual consistency must be explicit in user-facing behavior.

Task 001 reconciliation (2026-11-09): Phase 0 is an accepted static planning baseline. AI/media initial co-deployment and one notifications owner are explicit user constraints; additional owner allocations and lifecycle/operational choices remain PROPOSED/OPEN in ADR-002–005. Task 001 drafting authorization does not authorize Task 002 or the full Phase 1. See task-001-decisions.md.
