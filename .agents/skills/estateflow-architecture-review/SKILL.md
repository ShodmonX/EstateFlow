---
name: estateflow-architecture-review
description: Review EstateFlow V2 service boundaries, event contracts, PostgreSQL schema ownership and migration safety. Use before and after any V2 architectural change.
---

# EstateFlow architecture review

Read `AGENTS.md`, `docs/v2/architecture-v2.md`, `docs/v2/event-contracts.md`, `docs/v2/database-ownership.md` and the current task.

## Review checklist
1. Identify changed service, domain owner, inputs, outputs and deployment impact.
2. Check for imports of other services' business modules, ORM models and repositories.
3. Check for cross-schema reads/writes, FKs and runtime overprivilege.
4. Check event schema versioning, compatibility, routing key and consumer-owned queue bindings.
5. Check transactional outbox/inbox, ACK ordering, idempotency, retries, DLQ and replay.
6. Check sensitive data handling and media references.
7. Check restart/crash behavior, duplicate and out-of-order delivery.
8. Check tests and rollback/migration evidence.

## Output
Return findings ordered by severity, with exact file:line, impact, minimal fix and test to prove it. Separate confirmed findings from hypotheses. If no findings, explicitly list untested risks. Do not edit code unless the task authorizes it.
