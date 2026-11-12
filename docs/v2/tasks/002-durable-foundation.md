# EstateFlow V2 — Task 002: Isolated Durable Messaging Foundation

**Status:** READY FOR BOUNDED IMPLEMENTATION after user selects Variant A and submits the accompanying Codex prompt. This authorizes **Task 002 only**, not V1 changes, service extraction, or production deployment.

**Basis:** Phase 0 audit (`architecture-audit.md`, `event-inventory.md`, `table-ownership-map.md`); Task 001 specifications and decision register (`task-001-decisions.md`). This document is a **Task 002 lab-specific engineering specification**. It does not ratify unresolved D01–D08 business policy. The existing 17 draft schemas and 25 scenario fixtures are inputs, not evidence of running infrastructure.

## 1. Mission and non-goals

Build a self-contained, runnable and testable **PostgreSQL + RabbitMQ foundation** using disposable Docker Compose services, a **synthetic producer owner** and **synthetic consumer owner**. Demonstrate that accepted local business transactions eventually lead to at-least-once delivery, that a consumer applies each event **at most once locally** despite duplicate deliveries, and that errors can be retried, parked, inspected and replayed safely.

**Out of scope:** production or existing development PostgreSQL/RabbitMQ; real Telegram sessions; listener/parser/AI/catalog/search/notification extraction; real business payloads, user data, notification sends or LLM calls; migration of 26 legacy tables; changes to V1 event contracts, routing, Docker/Compose or Alembic; production HA certification; implementing open D03–D08 business policy. No code that automatically starts or sends to real external services.

## 2. Repository isolation and file boundaries

Codex must first inspect the actual Task 001 tree to avoid colliding with its 56 paths. Prefer this self-contained layout; adapt naming only if the repo already has an equivalent V2 foundation layout, recording why:

```
infra/v2/foundation/compose.yaml
infra/v2/foundation/.env.example
infra/v2/foundation/docker/Dockerfile
v2/foundation/pyproject.toml
v2/foundation/src/estateflow_v2_foundation/
  contracts.py  db.py  outbox.py  inbox.py  publisher.py
  consumer.py  retry.py  parking.py  topology.py  config.py
  synthetic.py  cli.py  observability.py
v2/foundation/migrations/
v2/foundation/tests/{unit,integration,fault}/
docs/v2/tasks/002-durable-foundation.md
 docs/v2/task-002-fault-matrix.md
 docs/v2/task-002-runbook.md
 docs/v2/decisions/ADR-004-task-002-lab-defaults.md
```

Do not copy V1 ORM, repository, runtime, business services, or queue abstraction into this foundation. V2 foundation must not import `estateflow.services.*`, `estateflow.repositories.*`, or V1 ORM models. Reuse Task 001 **language-neutral event contracts** by reading/validating the existing `contracts/v2/schemas/`; avoid duplicate competing event definitions. If schemas have an actual contradiction, document it and fix only the draft V2 schema with corresponding fixtures/tests; never silently widen validation to accept arbitrary payloads.

## 3. Disposable infrastructure: Variant A

- One **new Compose project**, e.g. `estateflow-v2-foundation`, with **new** volumes and **new** isolated network(s). Never join existing EstateFlow networks or mount existing data/secret/session directories.
- PostgreSQL 16 image and RabbitMQ 4 management image are **initial image families**; Codex should select available explicit tags, record exact resolved image digests, and document why. Never claim a tag is digest-pinned if it is not. Do not alter host database installations.
- No host-published ports (`ports:` absent), no `network_mode: host`, no host Docker socket mount, no privileged containers. All DB/broker/test traffic stays on the Compose network. Use a containerized test runner; local Docker CLI is the only host interaction.
- Dedicated database `estateflow_v2_lab`; two schemas `lab_producer` and `lab_consumer`. Synthetic table names must not collide with real EstateFlow tables. Use separate owner, migration and runtime roles; no cross-schema DML privileges.
- RabbitMQ durable **TOPIC** exchange `estateflow.v2.events`, separate durable dead-letter exchange `estateflow.v2.dlx`. One consumer-owned durable queue, e.g. `v2.lab.consumer.events.v1`, and a dedicated terminal DLQ, e.g. `v2.lab.consumer.dead.v1`. Declare queues with explicit type and policy. **Single broker node is NOT HA**, even if using a quorum queue with one replica; no production replication claim.
- Provide `.env.example` only. Create synthetic local passwords/credentials in ignored files or through a safe one-time bootstrap; no passwords in tracked Compose YAML, command history, logs or reports. Add narrowly scoped `.gitignore` patterns only for newly generated lab secrets/artifacts; do not alter unrelated ignores.
- Docker image pulls and package installation **inside lab images only** are permitted if available. No host `sudo apt`, global pip install, live network endpoints or credential sharing. If Docker, images, or connectivity are unavailable, report BLOCKED rather than substituting live infrastructure.
- All Compose commands MUST use explicit `-f infra/v2/foundation/compose.yaml -p estateflow-v2-foundation` (or equivalent deterministic project name). `down` must not remove volumes by default. `down -v` requires explicit confirmation and must target only this lab project; no `docker system prune`, global volume pruning or broad cleanup.

## 4. PostgreSQL data and transaction model

### Producer-owned schema

`lab_producer.synthetic_entities`: a synthetic aggregate (`aggregate_id`, `aggregate_version`, `value`, timestamps) with optimistic concurrency and unique revision rules.

`lab_producer.outbox_events`: immutable event ID and canonical envelope/payload, routing key, schema version, aggregate metadata, status, created/next_attempt/published timestamps, attempt count, lease owner/token/expiry, last error **class** (no sensitive payloads). Unique `event_id`; stable event identity across broker retries. Event payload and domain state MUST commit in **one PostgreSQL transaction**. Rollback means neither survives.

### Consumer-owned schema

`lab_consumer.inbox_events`: unique `(consumer_name, event_id)`, canonical payload fingerprint, processed_at and status, with effect committed in **same transaction** as processed inbox entry.

`lab_consumer.synthetic_projection`: deterministic synthetic effect (e.g. aggregate revision and applied event count) to prove no duplicate effect.

`lab_consumer.aggregate_versions`: tracks applied aggregate revision and originating event ID to distinguish duplicate/stale/conflicting revisions. Do not assume ordering is guaranteed by RabbitMQ.

`lab_consumer.retry_schedule`: durable retry intent, attempt count, `next_attempt_at`, lease token, last error class; stable original event ID; scheduled by consumer before ACK, then republished with confirm by a due dispatcher. Duplicate early deliveries must not bypass a scheduled backoff.

`lab_consumer.parked_events`: reason (`invalid_schema`, `unsupported_version`, `payload_conflict`, `revision_gap`, `revision_conflict`, `expired`, `terminal_failure`), event ID if extractable, sanitized diagnostics, payload reference or bounded synthetic payload, created/expiry timestamps, operator/replay audit. Never ACK an invalid event before durable park succeeds.

`lab_consumer.replay_audit`: append-only operator/action/time/event identity and replay result. A replay reuses the original business `event_id` for redelivery; distinct replay-operation ID records the authorized action. If a new semantic event is intentionally generated, it requires a new event ID and causation link. Never blindly delete inbox entries to force reapplication.

### Isolation and migration rules

- `NOLOGIN` owner roles for both schemas; separate `LOGIN` app roles with only required own-schema `USAGE` and object DML, plus own sequence rights; a migration role is separate from runtime. Lock down `public` CREATE, set safe `search_path`, handle default privileges for **future** objects, and test negative grants by connecting as each runtime role.
- All migration artifacts are **lab-only** and independently versioned. No global Alembic migration changes, no production SQL, no `alembic stamp`. Ensure repeatable setup on a fresh disposable DB and document forward-recovery rather than destructive downgrade assumptions.
- Use row-level locks / `FOR UPDATE SKIP LOCKED` or atomic `UPDATE ... RETURNING` for claims; leases must have expiry and unique fencing tokens. Stale workers must not mark another worker's claim completed. A stale worker may still publish, so duplicates must remain safe.

## 5. Broker delivery and failure semantics

- Topic exchange `estateflow.v2.events` with routing key `lab.synthetic.changed.v1`; consumer-owned queue bound to that key. Existing V1 `estateflow.events` is **DIRECT** and must not be redeclared as TOPIC or otherwise modified. Coexistence test may declare a **lab-local** V1-named DIRECT exchange to demonstrate no type conflict; it must not connect to the actual V1 broker.
- Persistent messages, publisher confirms, manual ACK, bounded prefetch, explicit reconnect and backpressure. A broker confirm proves broker acceptance, **not** consumer processing or atomicity with PostgreSQL.
- Outbox dispatcher claims pending/expired-lease records, publishes with confirm, then marks published with its lease token. If publish succeeded but mark fails/crashes, publish again with the **same event ID**. Consumer deduplicates.
- Consumer validates envelope and payload version, records effect+inbox atomically, commits, **then** ACKs. If DB commit fails, do not ACK success; use NACK/requeue or durable retry path with bounded behavior.
- Transient handler failure: in a consumer DB transaction, create/update durable retry intent (or equivalent owner-local outbox), then ACK only after durable scheduling; due worker republishes and confirms before marking dispatched. Failures while recording retry must not ACK.
- Permanent/invalid/unsupported version: durably park with redacted reason; optionally publish a terminal diagnostic event via owner-local outbox to `estateflow.v2.dlx`. Never drop poison messages silently. Use a separate DLQ and a documented operator replay CLI with explicit audit and allowlist.
- For aggregate versions: same `event_id` + same canonical payload => duplicate ACK/no effect; same `event_id` + different payload => park conflict; stale version => ACK/no effect only if lineage is known; same version with different event ID => park conflict; future version gap => park for bounded reconciliation, not apply out of order. A policy for real Telegram revisions remains OPEN (D03).
- Event max age, retries, leases, DLQ policy and numeric budgets use the **lab defaults** in ADR-004. Real-data retention and reparse remain OPEN (D07).

## 6. Numeric lab defaults (NOT production policy)

| Setting | Task 002 synthetic lab default | Notes |
|---|---|---|
| Outbox delivery attempts | 6 total (initial + 5 retries) | attempt count includes uncertain/timeout publish attempts |
| Retry delays | 5, 15, 45, 135, 405 seconds | exponential factor 3, ±20% jitter; deterministic clock/RNG in tests |
| Outbox lease | 60 seconds | expiry reclaim, fencing token; configurable |
| Consumer retry lease | 60 seconds | due-dispatch claim, fencing token |
| Publisher confirm timeout | 10 seconds | timeout is ambiguous; retry same event ID |
| Consumer prefetch | 10 | adjustable for synthetic load tests |
| Aggregate revision-gap wait | 10 minutes | then park terminal for operator inspection |
| Maximum event processing age | 24 hours | synthetic only; expired events parked, never silently discarded |
| Maximum synthetic message body | 64 KiB | reject/park oversized bodies safely; no real PII |
| Retry/parking/inbox retention | No automatic purge in Task 002 | preserve idempotency/replay proof; lab cleanup only |
| Queue availability | Single-node lab, no HA claim | explicit durable queue type; production replication decision OPEN |

Retries need precise accounting: attempts are *actual publish or processing attempts*; never consume attempts solely because a timer fired or a claim expired before work began. Document how producer publish attempts and consumer processing attempts are counted separately. Time-based tests must use an injectable clock rather than waiting real minutes.

## 7. Required implementation surfaces

- Typed minimal interfaces for transaction/unit-of-work, outbox, inbox, dispatcher, retry scheduling, broker transport and schema validation; no domain imports.
- A synthetic CLI/API (no HTTP port required) that creates an aggregate and outbox event transactionally, plus an inspect/status command. A worker that dispatches and a consumer that applies the effect. Optional dedicated retry worker.
- Health/readiness checks for DB/broker connectivity and worker progress; structured redacted logs with event_id, correlation_id, owner and error class (never raw message text/credentials). Metrics for pending/oldest outbox, lease expiry, retries, parked/DLQ, duplicate suppression, publish failures and consumer lag.
- Fault hooks **only in synthetic test mode** at: before commit, after commit, after broker confirm/before outbox mark, after consumer effect commit/before ACK, after durable retry scheduling/before ACK, after lease expiry, and broker outage. Never ship a production-enabled kill switch.
- Safe runbook with exact startup, test, inspect, restart, recovery and project-scoped cleanup commands; include troubleshooting and proof of no host ports.

## 8. Tests and evidence

Implement all required cases in `docs/v2/task-002-fault-matrix.md`. At minimum: transaction rollback; commit-before-publish crash; publish-confirm-before-mark crash; effect-commit-before-ACK crash; duplicate/conflicting event IDs; stale/future aggregate versions; broker/DB restarts; concurrent dispatcher/consumer claims; delayed retry; bounded terminal DLQ; authorized replay; malformed/unsupported events; cross-schema/sequence/default privilege negative tests; V1 DIRECT/V2 TOPIC lab coexistence; no host ports, no real sends, no secret leakage.

Run tests **inside the isolated Compose test container** with its declared dependencies. Unit/contract tests and integration/fault tests must be reported separately with command, exit code, counts and observed effects. A skipped test is not a pass. If fault tests require Docker access that is unavailable, report BLOCKED and do not claim completion.

## 9. Definition of done / gate to Task 003

1. Foundation starts/stops from a clean Compose project without touching V1; no host-published ports or shared volumes.
2. Producer state+outbox are atomic; a crash at any defined publish/ACK boundary yields no **lost accepted synthetic event** after recovery, allowing duplicate deliveries.
3. Consumer effect is applied exactly once **locally** per event/revision under concurrent duplicates; broker semantics remain at-least-once.
4. Poison, conflicts and terminal retries are durably parked/DLQ'd; replay is explicit, audited and safe.
5. Owner runtime roles cannot read/write another schema or use unauthorized sequences, including newly created objects.
6. All matrix scenarios run and pass, or unresolved failures are recorded and Task 002 remains INCOMPLETE.
7. Documentation includes reproducible commands, actual logs/test summaries, known limitations, changed paths and rollback/cleanup procedure.
8. Update `docs/v2/current-state.md` and decision register: Task 002 completion **does not approve** D01 full legacy migration, D03–D08 business policy, or Task 003 extraction.

**STOP** after reporting evidence. Do not proceed to Task 003 or modify existing EstateFlow services.
