# V2 executed test evidence

2026-11-10. Overall PARTIALLY VERIFIED. All counts below are read from actual JUnit XML; case-level statuses/commands/limits are in V2_TEST_RESULTS.json. Providers remain FAKE_ONLY. No skipped or nonexistent tests counted as passed.

| Suite | Exit | Passed | Failed | Skipped | Seconds |
|---|---:|---:|---:|---:|---:|
| unit | 0 | 85 | 0 | 0 | 1.473 |
| foundation | 0 | 53 | 0 | 0 | 7.726 |
| integration | 0 | 5 | 0 | 0 | 37.155 |
| e2e | 0 | 3 | 0 | 0 | 9.741 |

Total: 146 passing pytest cases. FND IDs share test cases and are not additional pytest counts. Genuine restart/outage driver exit0 covers FND05/23/24 separately. Restore exit0 compared112 table count/content digests. Contract check exit0:62 JSON/37 schemas. Final image Ruff exit0; MyPy exit0/41source files; pip check exit0. No vulnerability audit implied.

| Case | Status | Evidence / limit |
|---|---|---|
| FND-01 | PASS | Neither synthetic entity mutation nor outbox event persists |
| FND-02 | PASS | Outbox pending; restart eventually publishes; one local consumer effect |
| FND-03 | PASS | Accepted event remains durable and is dispatched after restart |
| FND-04 | PASS | Same event ID may be republished; consumer effect exactly once locally |
| FND-05 | PASS | Outbox retry persists, lease expires/reclaims, no lost event |
| FND-06 | PASS | Stable event ID across retry, no duplicate effect Exception injected after a genuine confirmed publish; no naturally induced half-open confirm timeout or deadline proof. |
| FND-07 | PASS | Single active lease at a time; no concurrent claim; duplicate after lease expiry safe |
| FND-08 | PASS | Fencing token rejects stale update |
| FND-09 | PASS | One inbox/effect; second ACK without effect |
| FND-10 | PASS | Durable payload conflict park and alert; no second effect |
| FND-11 | PASS | Revision conflict parked, no overwrite |
| FND-12 | PASS | Future revision held; after revision 2 it is applied once in order or reconciled per documented policy |
| FND-13 | PASS | No rollback of aggregate; ACK/no effect only with known lineage |
| FND-14 | PASS | Redelivery deduplicated; one local effect Real consumer process death closes its TCP connection before ACK; no Docker network-detach/blackhole experiment. |
| FND-15 | PASS | No success ACK; no effect without inbox, safe redelivery |
| FND-16 | PASS | Retry intent persisted before original ACK; retry not executed before due time |
| FND-17 | PASS | Duplicate original cannot bypass backoff or create duplicate retry intent |
| FND-18 | PASS | Capped 6 total processing attempts; deterministic jitter within bounds |
| FND-19 | PASS | Terminal record and DLQ/park; no silent discard or infinite retry |
| FND-20 | PASS | Durable parking, safe diagnostics, no business effect; no unbounded memory allocation |
| FND-21 | PASS | Audit record, stable original event identity, no forced inbox deletion Local lab-operator string and application audit, not production RBAC/append-only database ACL. |
| FND-22 | PASS | Denied, audit denial, no publication Local lab-operator string and application audit, not production RBAC/append-only database ACL. |
| FND-23 | PASS | Durable message survives ordinary restart; no HA claim Ordinary restart of one durable classic-queue RabbitMQ node; not HA or node/disk loss. |
| FND-24 | PASS | Durable state survives ordinary restart; recovery proceeds Ordinary container restart with retained volume; not PITR/disaster recovery. |
| FND-25 | PASS | Permission denied; same for consumer accessing producer schema |
| FND-26 | PASS | Unauthorized access denied; own-schema intended DML/sequence usage works |
| FND-27 | PASS | Both coexist; no exchange-type redeclare; V1 live broker untouched |
| FND-28 | PASS | No host ports, no host network, no existing EstateFlow volume/network, no Docker socket mount Declared runtime containers inspected; ephemeral test clients intentionally mount V2 source/evidence. |
| FND-29 | PASS | Logs and metrics contain redacted diagnostics, not payload or credentials |
| FND-30 | PASS | One committed effect; unique inbox constraint survives concurrency |
| FND-31 | PASS | Event remains inspectable in terminal park; no out-of-order effect |
| FND-32 | PASS | Deterministic, no destructive/duplicate schema changes; roles/grants correct |
| FND-33 | PASS | At-least-once delivery; idempotent local effect; no broker-level exactly-once claim Real consumer process death closes its TCP connection before ACK; no Docker network-detach/blackhole experiment. |
| FND-34 | PASS | No real Telegram/OpenRouter/R2 integrations or V1 imports; no business data emitted Foundation import check, explicit fake guards and private route proof; full live SDK/provider compatibility NOT_RUN. |
| ISO-01 | PASS | 20 declared containers; internal project network; own volumes; no runtime bind mounts or host ports. |
| ISO-02 | PASS | Fresh named volumes bootstrap and repeat migrations, queues/workers/HTTP ready. |
| ISO-03 | PASS | Only search restarted; all deployed checks passed afterward. Independent restart only; no per-owner packaging/rolling schema upgrade proof. |
| ISO-04 | PASS | 14 owners future defaults/sequence tests; 12 domain roles deny132foreign schema SELECTs, public CREATE and SET ROLE. |
| ISO-05 | PASS | V1-named DIRECT declared only on lab broker alongside distinct V2 TOPIC; durable queues/parking/replay. Shared broker principal is a known authorization gap. |
| ISO-06 | PASS | FND01–34 exercised by53pytest cases plus genuine scoped restart/outage driver. See per-FND limitations; confirm blackhole deadline is unverified. |
| EVT-01 | PASS | 37 schemas meta/reference checks;62strict JSON files; original25scenario fixtures plus semantic/action negatives. |
| EVT-02 | PASS | Duplicate/hash/version/gap/replay tests, stale source/media generations and search rebuild concurrency. |
| ING-01 | MOCK_ONLY | Synthetic create/two edits/empty delete/delete-before-create/tombstone workflow. No real Telegram account/session lease/fencing; uncertain transport reconciliation remains provisional. |
| ING-02 | MOCK_ONLY | Album journal/late members/limits and stable canonical tested in owner-state harness. Actual worker kill while album settling and max-load buffer recovery NOT_RUN. |
| ING-03 | MOCK_ONLY | Generic single/digest/album/mixed policy and reparse tests; buffered threads fail closed. Not a complete V1 recipe registry or full thread assembler. |
| ING-04 | MOCK_ONLY | Durable owner input/quarantine design with explicit authorized release/reparse audit. No traffic-based quarantine global capacity/retention or dedicated process-kill test. |
| DED-01 | MOCK_ONLY | Confirmed active catalog signal required for cross-source suppression; failed unconfirmed extraction cannot suppress next source. Near-text signals are hints; load/multi-replica/perceptual-image parity unproved. |
| AI-01 | MOCK_ONLY | Fake valid/invalid/rate-limit/timeout, owner run audit, schema and normalization gates. Live LLM adapter/fallback/cost latency parity absent. |
| MED-01 | MOCK_ONLY | Actual JPEG/PNG/WebP transform/EXIF/bounds/hash; fake storage and media-only retry revision tests. Public object capability/expiry/multi-asset delivery and real storage absent. |
| CAT-01 | PASS | Owner evidence/canonical snapshots/review/merge and atomic outbox; corrected pending review and empty batch approval. No full adversarial multi-owner merge load matrix. |
| CAT-02 | PASS | Multi-source remaining evidence, last-source hide and stale tombstone behavior. |
| CAT-03 | PASS | Stale1/3/2generation refusal, approved two-to-one replacement, empty-generation approval and corrected-review rebinding. |
| SEA-01 | MOCK_ONLY | Price daily/monthly/one-time and visibility/query error tests over synthetic fixtures. Complete V1 differential sorting/tags/location/media/UI matrix NOT_RUN. |
| SEA-02 | PASS | Archive rebuild into new namespace, pointer switch and actual PostgreSQL serialization against live snapshot. |
| NOT-01 | MOCK_ONLY | One initial job; replay/edit do not duplicate; identity/consent/premium/stale checks. Full V1 matching matrix and long-lived entitlement refresh policy unproved. |
| NOT-02 | MOCK_ONLY | Fake success/unknown hold, retries/attempt states; deployed matcher/sender share owner data. No separately committed external-send intent/result crash protocol or exactly-once Telegram guarantee. |
| IDN-01 | MOCK_ONLY | Identity owner activation/award projection and source reward saga executed. Complete V1 referral/expiry/admin product semantics not established. |
| CTL-01 | MOCK_ONLY | Source saga waits configuration ACKs; failed dependency visible; flags/audit. Already granted reward compensation is manual reconciliation. |
| TAX-01 | PASS | Owner tag event updates catalog/search/filter projections without foreign SQL. Per-consumer operation ACK reconciliation under outage not fully exercised. |
| CON-01 | MOCK_ONLY | Command-driven schedule/fake publication, unknown hold and permanent-failure no-resend. No automatic publication scheduler process or real publish. |
| ANL-01 | PASS | Event-fed local counters plus actual manual record counter mutation. Not full V1 beta analytics/dashboard metric parity. |
| API-01 | MOCK_ONLY | Synthetic HMAC auth, owner HTTP,202operations, explicit terminal failure and forbidden calls. Full route/request/response/rate-limit parity absent; see V2_PARITY.md. |
| BOT-01 | MOCK_ONLY | Synthetic /start,/search,/filters adapter exists. Full referral/dialog/retry workflow and real bot polling/sending absent; no dedicated deployed bot command workflow test. |
| UI-01 | MOCK_ONLY | Static public/admin HTML/JS/CSS served and smoke-tested over TestClient. No browser automation or complete original UI compatibility. |
| E2E-01 | MOCK_ONLY | Genuine owner DB/broker and deployed HTTP raw→parse→AI→catalog→search→fake notification. Providers/storage/sender synthetic; no real external effect. |
| E2E-02 | MOCK_ONLY | Deployed two edits/delete; album/digest/merge/review/reparse converge in owner harness. Not all mutation variants rerun through deployed HTTP/crash boundaries. |
| E2E-03 | MOCK_ONLY | Real process kills, broker/DB restarts, independent search restart then HTTP rerun. Not every business worker/album/reparse/send boundary killed in deployed stack. |
| E2E-04 | NOT_RUN | Executed subset: repeatable26 table synthetic compatibility archive only. Active entity/FK-to-event/backfill relation migration is not implemented; no full acceptance pass. |
| SEC-01 | MOCK_ONLY | Synthetic fixtures; redacted error-class diagnostics; scoped SQL and denied replay tests. Shared broker producer authentication and append-only replay ACL remain gaps; historical lab-only credential incident documented. |
| SEC-02 | PASS | No default route, TEST-NET connect denied, internal runtime network and default-deny fake provider configuration. No actual prohibited provider contacted; no live SDK compatibility/packet-capture certification. |
| OPS-01 | MOCK_ONLY | DB/progress readiness and pending/age/retry/park counters. Full projection lag dashboards, alerts/load/SLO observability absent. |
| OPS-02 | MOCK_ONLY | Replay underlying authorization/state + retry publication; rebuild and112 table same-cluster restore. Full CLI operator flow, offhost/PITR and production RBAC not proven. |
| OPS-03 | PASS | Old project stopped and all volumes preserved; drivers restrict project names. No destructive cleanup command executed or authorized. |
| QA-01 | PASS | Final image build/lint/type/dependency consistency and suites pass. Vulnerability database/SBOM and transitive hash lock audit NOT_RUN. |
| QA-02 | PASS | Every inventoried V1 route/feature explicitly labeled in V2_PARITY.md. Inventory completeness is evidence-backed by Phase0 route list; statuses do not imply complete parity. |
| LIVE-PROVIDERS | NOT_RUN | Forbidden in this scope: no real Telegram/LLM/storage/send/provider validation. |
| ACTIVE-BACKFILL | NOT_RUN | Only archive importer exists; active entity and relationship conversion not implemented. |
| DEPENDENCY-CVE-AUDIT | NOT_RUN | No vulnerability database/SBOM or transitive hash audit; offline pip check only. |
| BROWSER-AUTOMATION | NOT_RUN | Static HTTP smoke executed; no browser runner installed/executed. |
| CONFIRM-BLACKHOLE-DEADLINE | NOT_RUN | 10 s socket/blocked timeout is not a proved independent publisher-confirm deadline. |
| BROKER-FORGERY-NEGATIVE | NOT_RUN | Known shared principal and self-asserted actor security gap; runtime forged-message denial test not implemented. |

## Exact executed commands

### unit

```bash
docker run --rm --network none --read-only --tmpfs /tmp --user 1000:1000 -v /home/deploy/projects/EstateFlow/v2:/app/v2:ro -v /home/deploy/projects/EstateFlow/contracts:/app/contracts:ro -v /home/deploy/projects/EstateFlow/tests/fixtures/v2:/app/tests/fixtures/v2:ro -v /home/deploy/projects/EstateFlow/.artifacts/v2/final:/evidence:rw estateflow-v2-lab:dev python -m pytest --tb=short -p no:cacheprovider -c /app/v2/pytest.ini --junitxml=/evidence/unit.xml /app/v2/tests/unit -q
```

### foundation

```bash
docker compose --env-file infra/v2/.env.verification.local -f infra/v2/compose.yaml -p estateflow-v2-isolated-verify20261110 run --rm --no-deps --user 1000:1000 -v /home/deploy/projects/EstateFlow/v2:/app/v2:ro -v /home/deploy/projects/EstateFlow/.artifacts/v2/final:/evidence:rw v2-test-runner python -m pytest --tb=short -p no:cacheprovider -c /app/v2/pytest.ini --junitxml=/evidence/foundation.xml /app/v2/tests/foundation -q
```

### integration

```bash
docker compose --env-file infra/v2/.env.verification.local -f infra/v2/compose.yaml -p estateflow-v2-isolated-verify20261110 run --rm --no-deps --user 1000:1000 -v /home/deploy/projects/EstateFlow/.artifacts/v2/final:/evidence:rw v2-test-runner python -m pytest --tb=short -p no:cacheprovider -c /app/v2/pytest.ini --junitxml=/evidence/integration.xml /app/v2/tests/integration -q
```

### e2e

```bash
docker compose --env-file infra/v2/.env.verification.local -f infra/v2/compose.yaml -p estateflow-v2-isolated-verify20261110 run --rm --no-deps --user 1000:1000 -e EFV2_DEPLOYED_TESTS=1 -v /home/deploy/projects/EstateFlow/.artifacts/v2/final:/evidence:rw v2-test-runner python -m pytest --tb=short -p no:cacheprovider -c /app/v2/pytest.ini --junitxml=/evidence/e2e.xml /app/v2/tests/e2e -q
```

### restarts

```bash
python3 scripts/v2/lab-faults.py --project estateflow-v2-isolated-verify20261110 --env-file infra/v2/.env.verification.local
```

### isolation

```bash
python3 scripts/v2/inspect-lab.py
```

### restore

```bash
python3 scripts/v2/lab-restore.py --project estateflow-v2-isolated-verify20261110 --env-file infra/v2/.env.verification.local
```

### contracts

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/v2/verify-contracts.py
```

### build

```bash
docker compose --env-file infra/v2/.env.verification.local -f infra/v2/compose.yaml -p estateflow-v2-isolated-verify20261110 build migrate
```

### deploy

```bash
docker compose --env-file infra/v2/.env.verification.local -f infra/v2/compose.yaml -p estateflow-v2-isolated-verify20261110 up -d --no-build
```

### restart_search

```bash
docker compose --env-file infra/v2/.env.verification.local -f infra/v2/compose.yaml -p estateflow-v2-isolated-verify20261110 restart search
```

### ruff

```bash
docker run --rm --network none --read-only --tmpfs /tmp estateflow-v2-lab:dev python -m ruff check --no-cache --config /app/v2/pyproject.toml /app/v2
```

### mypy

```bash
docker run --rm --network none --read-only --tmpfs /tmp estateflow-v2-lab:dev python -m mypy --config-file /app/v2/pyproject.toml --cache-dir /tmp/mypy /app/v2
```

### pip_check

```bash
docker run --rm --network none --read-only --tmpfs /tmp estateflow-v2-lab:dev python -m pip check
```

## Repair history and limits

Earlier failing runs were used as regressions, not counted as final successes. Red/green evidence covered retry transport budget; terminal replay; media-required publication; daily normalization; confirmed-only cheap dedup; generation ordering/digest supersession/empty review/corrected review; missing merge fields; content terminal state; search rebuild serialization; delegated failure status; unexpected application exception backoff. The runtime inspector initially included an ephemeral evidence-mounted test client and failed; selection was corrected to all 20 declared services, then actual mounts/ports/network were checked.

Warnings: deprecated jsonschema RefResolver and Starlette/anyio alias. Host has standard library/jsonschema but lacks pytest/Pillow/FastAPI/psycopg; container tools supplied those dependencies. No host dependencies installed. Short tracebacks used after a historical generated-lab-only credential exposure; fresh credentials/project used for final evidence.

Expected gaps are explicitly NOT_RUN/MOCK_ONLY, not green by omission. Full original V1 tests, production restore/load/HA, browser behavior, real providers, active legacy migration and broker producer authentication are not established. See V2_OPEN_ITEMS.md.
