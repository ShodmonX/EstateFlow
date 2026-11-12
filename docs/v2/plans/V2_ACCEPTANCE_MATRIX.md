# V2 full-system acceptance matrix — executable gate, not a claim

Codex must map each test to command, actual outcome, evidence file, and status PASS / FAIL / BLOCKED / MOCK_ONLY / NOT_RUN. Mock-only can pass an *offline contract*, but cannot establish real-provider parity. Existing Task 002 FND-01–FND-34 are REQUIRED in addition to these checks; re-use their verified tests, not merely their prose.

| ID | Capability | Required proof |
|---|---|---|
| ISO-01 | Isolated Compose | Unique project/network/volumes, no host ports, no host networking/socket mounts, no existing V1 paths/containers touched |
| ISO-02 | Fresh deployment | From clean lab volumes: build → database migrations → queue topology → workers → seed → green readiness; repeat without duplicate objects |
| ISO-03 | Independent deploy | Restart one service/owner and its consumers without code import of sibling business services or a coordinated redeploy; no source data loss |
| ISO-04 | DB role security | Each runtime role own-schema CRUD only; all other schemas denied, including sequences, defaults/new objects and public CREATE |
| ISO-05 | Messaging isolation | Lab-local V1 DIRECT and V2 TOPIC can coexist; versioned consumer queues, max retries, parked/DLQ, audited authorized replay |
| ISO-06 | Reliable outbox/inbox | Task 002 FND-01–34 actual PostgreSQL/RabbitMQ fault tests (not mocks alone), stable event IDs, lease fencing, and transaction crash points |
| EVT-01 | Contract validity | Existing Task 001 schemas/fixtures plus additions, invalid versions, schema mismatch and replay conflict; validated by JSON Schema library |
| EVT-02 | Ordering | Duplicate, stale, same-version-conflict, future-gap, out-of-order retry, replay never corrupts projection |
| ING-01 | Raw listener | Create/edit1/edit2/delete, empty delete, delete-before-create, restart and late delivery, source ID & local observation order |
| ING-02 | Albums | Group journal survives crash; late member processed or quarantined; bounded buffers and byte/member caps |
| ING-03 | Parser | Single/digest/album caption/mixed-feed, exclusions, policy snapshot, reparse under new recipe, buffered thread closed/parked |
| ING-04 | Quarantine | Rejected material durable, bounded, audited authorized replay, duplicate replay idempotent |
| DED-01 | Pre-AI | Persist exact and near dedup signals across worker restart/replicas; do not suppress edits/deletes |
| AI-01 | AI extraction | Fake provider valid/invalid, rate-limit, timeout, fallback, manual review, schema/semantic gates, run audit and idempotency |
| MED-01 | Media | JPEG/PNG/WebP real decode/resize/re-encode valid, actual dimensions and file type; missing/expired/failed/ready states and fake storage |
| CAT-01 | Canonical catalog | Parent/child, create, review, merge, retained source evidence, all changes publish actual canonical ID/revision atomically |
| CAT-02 | Tombstone | Delete one of 2 active sources remains visible; delete last source hides; stale old create/edit cannot resurrect |
| CAT-03 | Reparse | Manifest supersedes earlier candidate generation, digest reshuffle and orphan cleanup/projection convergence |
| SEA-01 | Search | Correct active status, sell/rent, price normalization, location/tags, media, filtering, sorting/pagination; error != empty |
| SEA-02 | Rebuild | Rebuild projection from catalog replay/snapshot, same read results and no cross-schema queries |
| NOT-01 | Matching | Filters + identity/consent/premium projection, duplicate listing events, one initial job/alert, no repeat on replay |
| NOT-02 | Delivery | Fake sender success/429/transient/terminal/unknown, due retry, lease race/reclaim, no unknown automatic resend |
| IDN-01 | Identity | User/referral/award coherent transaction, duplicate activation, entitlement change event, role enforcement |
| CTL-01 | Control | Admin audit, flags, sources/suggestions, approval-to-identity command with eventual state and reconciliation |
| TAX-01 | Taxonomy | Tag rename/remap reflected in catalog/search/filters without cross-owner SQL transaction |
| CON-01 | Content | Schedule, fake send, retry, unknown outcome parked and durable ledger, no real publish |
| ANL-01 | Analytics | Event-fed product/technical metrics and redacted logs; no direct shared append into analytics DB |
| API-01 | HTTP adapters | Public/admin/internal route inventory, correct auth/actor ownership, operation status, error/timeout semantics, deny forbidden calls |
| BOT-01 | Bot | Synthetic update/command handler + saved filters/search/referral, fake messages only, auth/permissions and retries |
| UI-01 | Frontends | Public/admin build and smoke against V2 mock APIs or explicit mock-only trace; feature/route parity inventory |
| E2E-01 | Happy path | Create raw → parse → dedup → AI/media → catalog → search → match → fake delivery; assert DB/broker outputs |
| E2E-02 | Mutation path | Two edits, album/digest, review, merge, last-source deletion and reparse converge across projections |
| E2E-03 | Restart | Kill workers and broker/DB at planned points, resume without lost accepted events or duplicate local effect |
| E2E-04 | Backfill | Synthetic 26-table data mapping reconciled with source counters/IDs/relations; real-data backfill DRY-RUN plan only |
| SEC-01 | Privacy | All mock values synthetic, sensitive marker never in logs/DLQ metadata, role negative tests, replay authorization |
| SEC-02 | Network | No live Telegram, OpenRouter, R2, production DB/broker activity; outbound allowlist/offline enforcement tested if technically available |
| OPS-01 | Observability | Outbox age, retry count, DLQ/park, projection lag, claimed lease, stalled consumer; health checks report no-progress condition |
| OPS-02 | Recovery | Operator can inspect failure, restart owner, replay authorized event and rebuild projection using documented commands |
| OPS-03 | Cleanup | Lab-only project scoped stop/optional volume removal with safeguards; other containers and volumes preserved |
| QA-01 | Static/build | Ruff/type checks where configured, contracts, unit, integration and Docker builds; failures explicitly documented |
| QA-02 | V1 parity | Every discovered V1 route/feature explicitly WORKING/MOCK_ONLY/DEFERRED/MISSING with file:line and test, no silent omissions |

## Evidence standards

- Create machine-readable `docs/v2/verification/V2_TEST_RESULTS.json` with one entry per case: `id`, `status`, `command`, `exit_code`, `assertions`, `artifact_paths`, `limitations`.
- Use exact observed commands and outcomes; no invented pass counts or manual assertions presented as automated.
- Keep secrets/raw/private payloads out of committed reports and fixtures; test artifacts under ignored `.artifacts/v2`.
- All FND and V2 checks required for a claim of **fully verified offline V2**; if blocked, report partial completion and preserve working subcomponents.
- Real third-party providers and real customer data are not covered by offline acceptance. Mark LIVE PROVIDER VALIDATION NOT RUN, not failure of isolated implementation.
