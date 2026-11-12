# Task 002 — Fault Injection & Acceptance Matrix

**Status:** required executable tests, not claims of existing behavior. All payloads must be synthetic. Each case needs an automated test ID, reproducible command, observed DB/broker state and pass/fail result. Prefer injected clocks, deterministic seeds, process/container kill points, and polling with bounded timeouts over arbitrary sleeps.

| ID | Fault / condition | Expected invariant |
|---|---|---|
| FND-01 | Roll back producer transaction before commit | Neither synthetic entity mutation nor outbox event persists |
| FND-02 | Commit entity+outbox, stop dispatcher before publish | Outbox pending; restart eventually publishes; one local consumer effect |
| FND-03 | Kill producer immediately after commit | Accepted event remains durable and is dispatched after restart |
| FND-04 | Confirm broker publish then kill dispatcher before `published` update | Same event ID may be republished; consumer effect exactly once locally |
| FND-05 | Broker unavailable while dispatcher has lease | Outbox retry persists, lease expires/reclaims, no lost event |
| FND-06 | Simulate publisher confirm timeout with ambiguous acceptance | Stable event ID across retry, no duplicate effect |
| FND-07 | Run two dispatchers against same pending row | Single active lease at a time; no concurrent claim; duplicate after lease expiry safe |
| FND-08 | Expire lease, old dispatcher tries to mark `published` | Fencing token rejects stale update |
| FND-09 | Send same event ID and identical canonical payload twice | One inbox/effect; second ACK without effect |
| FND-10 | Same event ID, different payload | Durable payload conflict park and alert; no second effect |
| FND-11 | Different event ID, same aggregate/version | Revision conflict parked, no overwrite |
| FND-12 | Send revision 3 before revision 2 | Future revision held; after revision 2 it is applied once in order or reconciled per documented policy |
| FND-13 | Send already-applied stale revision | No rollback of aggregate; ACK/no effect only with known lineage |
| FND-14 | Kill consumer after DB effect+inbox commit, before ACK | Redelivery deduplicated; one local effect |
| FND-15 | Consumer DB commit fails | No success ACK; no effect without inbox, safe redelivery |
| FND-16 | Transient handler failure | Retry intent persisted before original ACK; retry not executed before due time |
| FND-17 | Kill consumer after retry schedule commit before ACK | Duplicate original cannot bypass backoff or create duplicate retry intent |
| FND-18 | Advance fake clock across 5 retry delays | Capped 6 total processing attempts; deterministic jitter within bounds |
| FND-19 | Exceed max attempts or max event age | Terminal record and DLQ/park; no silent discard or infinite retry |
| FND-20 | Malformed JSON, unsupported schema version, oversized body | Durable parking, safe diagnostics, no business effect; no unbounded memory allocation |
| FND-21 | Authorized replay of parked synthetic event | Audit record, stable original event identity, no forced inbox deletion |
| FND-22 | Unauthorized replay or unsupported replay mode | Denied, audit denial, no publication |
| FND-23 | Restart RabbitMQ after confirmed persistent message, before consumption | Durable message survives ordinary restart; no HA claim |
| FND-24 | Restart PostgreSQL after committed outbox/inbox | Durable state survives ordinary restart; recovery proceeds |
| FND-25 | Connect as producer runtime role and read/write consumer schema | Permission denied; same for consumer accessing producer schema |
| FND-26 | Test `public` CREATE, cross-owner sequences, future tables/default privileges | Unauthorized access denied; own-schema intended DML/sequence usage works |
| FND-27 | Declare lab-local `estateflow.events` DIRECT and `estateflow.v2.events` TOPIC | Both coexist; no exchange-type redeclare; V1 live broker untouched |
| FND-28 | Inspect Compose configuration/network/volumes | No host ports, no host network, no existing EstateFlow volume/network, no Docker socket mount |
| FND-29 | Inject synthetic secrets/PII-shaped tokens into errors | Logs and metrics contain redacted diagnostics, not payload or credentials |
| FND-30 | Race two consumers on duplicate delivery | One committed effect; unique inbox constraint survives concurrency |
| FND-31 | Simulate gap expiry and missing predecessor | Event remains inspectable in terminal park; no out-of-order effect |
| FND-32 | Run migration/bootstrap twice on clean disposable DB | Deterministic, no destructive/duplicate schema changes; roles/grants correct |
| FND-33 | Force network interruption after consumer effect but before ACK | At-least-once delivery; idempotent local effect; no broker-level exactly-once claim |
| FND-34 | Inspect synthetic CLI and code paths | No real Telegram/OpenRouter/R2 integrations or V1 imports; no business data emitted |

## Required execution evidence

For every case capture: test ID, command, exit code, key assertions, retry/lease timeline when relevant, and reason for skip. Aggregate the results into a concise machine-readable test report plus Markdown summary under a **new ignored lab output directory** (e.g. `.artifacts/v2-foundation/`); include a redacted summary in `docs/v2/current-state.md`. Never commit raw logs containing secrets.

## Gate policy

All FND-01–FND-34 must be implemented and executed to claim the full Task 002 gate. If a case cannot be implemented without expanding scope, mark it BLOCKED with a proposed separate task; do not mark the gate complete. Static schema parsing or mocked tests do not substitute for broker/DB restart and concurrent-claim tests.
