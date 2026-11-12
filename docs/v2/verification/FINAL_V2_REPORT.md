# EstateFlow V2 final isolated migration report

Outcome: **PARTIALLY VERIFIED — runnable synthetic V2 lab; full V1 migration/parity is incomplete.**

2026-11-10. Branch main, HEAD addb856a99d24946ef127384121b99de371c017b unchanged. User authorized isolated M00–M10 work. No V1 tracked file/infrastructure, production database, real Telegram session/provider, real message, commit or push was used. D01–D08 remain LAB_PROVISIONAL. The next work is repair/compatibility work in the isolated lab, not production cutover.

## What runs

Fresh project estateflow-v2-isolated-verify20261110 has20 declared containers:19 running plus completed migration. Its internal private network and two named volumes are unique to that project. No published host ports, host bind mounts or Docker socket in declared runtime containers; all17 application processes run as uid10001. An earlier estateflow-v2-isolated project is stopped, all volumes retained.

| Component / process | Runtime command | PostgreSQL schema / app role | Observed behavior |
|---|---|---|---|
| listener | python -m v2.runner --owner listener --role worker | listener / ef_listener_app | Synthetic journal, subscription policy, edits/deletes, albums and uncertainty hold |
| source_parser | same runner --owner source_parser --role worker | source_parser / ef_source_parser_app | Generic recipes, digest generations, manifests, quarantine/reparse |
| pre_ai | --owner pre_ai --role worker | pre_ai / ef_pre_ai_app | Persistent text signals; suppression requires active confirmed catalog evidence |
| AI/media | --owner ai --role worker | ai / ef_ai_app | Fake extraction, actual codec, fake object bytes, audited media retry |
| catalog | --owner catalog --role worker | catalog / ef_catalog_app | Canonicals/evidence, revisions/tombstones, merge/review/generation replacement |
| search | --owner search --role worker | search / ef_search_app | Own projection/query/archive/rebuild; serialized rebuild/live mutation |
| notifications matcher | --owner notifications --role worker | notifications / ef_notifications_app | Filters, identity/catalog projection, jobs/attempts, unknown hold |
| notification-sender | --owner notifications --role sender | notifications / ef_notifications_app | Separate fake delivery/scheduling process, same bounded context |
| identity | --owner identity --role worker | identity / ef_identity_app | User/referral/award and entitlement facts |
| control | --owner control --role worker | control / ef_control_app | Flags/audit, source-approval saga and operation projections |
| taxonomy | --owner taxonomy --role worker | taxonomy / ef_taxonomy_app | Tag approval/remap events |
| content | --owner content --role worker | content / ef_content_app | Command-driven fake scheduled post ledger; terminal/unknown holds |
| analytics | --owner analytics --role worker | analytics / ef_analytics_app | Event-fed counters and actual operator record mutation |
| public-api | --owner search --role public_api | No DSN | Synthetic signed WebApp auth, owner-routed search/filter/commands |
| admin-api | --owner control --role admin_api | No DSN | Owner-routed synthetic admin commands/projections |
| internal-api | --owner control --role internal_api | No DSN | Scoped owner command adapter |
| bot | --owner identity --role bot | No DSN | Synthetic /start,/search,/filters interface; no polling/send |
| postgres | postgres:16.8-bookworm | estateflow_v2_lab | New private lab database/volume only |
| rabbitmq | rabbitmq:4.0.7-management | Private lab broker | Distinct V2 TOPIC/DLX and durable queues |
| migrate | python -m v2.foundation.cli migrate | Lab admin bootstrap | Applied additive schema versions1–2; exited0 |

Each of the12 domain schemas and2 foundation-test schemas has8 technical tables: records, schema_versions, outbox, inbox, revisions, retry_schedule, parked_events, replay_audit. Owner is ef_<schema>_owner NOLOGIN; runtime and migration logins are distinct NOINHERIT identities. No cross-owner FK or runtime SQL join/import was introduced. App roles get owned-schema DML/default sequence grants, cannot SET ROLE owner, and lack public CREATE. All26 legacy tables remain accounted for in database-ownership.md/table-ownership-map.md; **no physical legacy table or active legacy data migration occurred**. New records are generic JSON documents and domain SQL constraints remain future work.

As built: one queue v2.<owner>.events.v1 per domain worker context, exact bindings in v2/shared/routing.py; each has .dead companion on estateflow.v2.dlx DIRECT. Business exchange estateflow.v2.events is TOPIC. V1-named estateflow.events DIRECT was declared only on the new lab broker for coexistence proof; live V1 broker untouched. Poison/conflicts normally use owner-local durable parking and ACK, distinct from transport dead-letter queues. Workers use manual-ACK basic_get polling; RabbitMQ registered consumer count0 is expected and is not readiness proof. Queue durability was inspected on the private broker. No Redis dependency. Initial AI/media and notification ownership constraints are preserved.

All16owner/adapter readiness endpoints tested by deployed e2e returned FAKE_ONLY; sender function is evidenced by a sent fake job. Own runtime DB metrics and worker heartbeat contribute to owner readiness. Full lag/SLO dashboards are absent. Independent search restart then repeated deployed tests passed; rolling migrations/owner-specific build pipelines were not tested.

## Actual verification

| Suite/check | Exit | Passed | Failed / skipped | Evidence |
|---|---:|---:|---|---|
| Offline unit/contract/provider/codec/API | 0 | 85 | 0 /0 | .artifacts/v2/final/unit.xml |
| Genuine PostgreSQL/RabbitMQ foundation faults | 0 | 53 | 0 /0 | .artifacts/v2/final/foundation.xml |
| Owner event pipeline/security/backfill/rebuild race | 0 | 5 | 0 /0 | .artifacts/v2/final/integration.xml |
| Deployed HTTP and private-network tests after independent search restart | 0 | 3 | 0 /0 | .artifacts/v2/final/e2e.xml |
| Real broker outage/RabbitMQ/PostgreSQL restart driver | 0 | FND05/23/24 | No skipped scenario | .artifacts/v2/final/restarts.json |
| Strict JSON / Draft202012 meta/reference check | 0 | 62 JSON /37 schemas | Existing jsonschema used offline | scripts/v2/verify-contracts.py |
| Final image Ruff / MyPy | 0 /0 | Clean /41 Python files | Gradual typing, not strict typing | V2_TEST_RESULTS.md exact commands |
| Docker image build / Compose config | 0 /0 | Built /valid | No runtime provider network | V2_TEST_RESULTS.md |
| Dependency consistency | 0 | pip check clean | CVE/SBOM audit NOT_RUN | V2_OPEN_ITEMS.md |
| Scoped runtime inspection | 0 | 20 declared containers | No host ports/binds; internal network | .artifacts/v2/final/isolation.json |
| Synthetic dump/restore | 0 | 112 table count/content digests equal | Same cluster, not PITR/offhost | .artifacts/v2/final/restore.json |

Total **146 passing pytest cases**, not including repeated runs/driver assertions as extra tests. All34 FND IDs and41 master acceptance IDs have explicit status, commands, assertions, artifacts and limitations in V2_TEST_RESULTS.json. MOCK_ONLY/NOT_RUN entries keep the whole delivery PARTIAL. Machine-readable cases are accompanied by all146 individual JUnit case outcomes. Exact expanded commands are in V2_TEST_RESULTS.md; reproducible operating sequence in V2_RUNBOOK.md.

Final image estateflow-v2-lab:dev: config sha256:2958b7a8bfc8ca7c75fb561bd9ce3e4849aeaf24f638e3fdbd2b4675a97d1731; manifest-list sha256:4119420e75f44d681448f83ebaf3b1921519679ed83072b738094becd3de12e1. Build resolved Python3.12.8-slim base sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0. Direct Python package versions are pinned; transitive dependencies/image upgrades/security audit remain open. Registry/package access was build-only; runtime network is internal. No host dependency installation.

Deprecation warnings: jsonschema RefResolver and Starlette/anyio alias. No warnings were represented as a failure-free production security audit. Full V1 tests, real providers, browser automation, active entity backfill, HA/load and real-data migration are NOT_RUN. The original Phase0 audit is historical evidence, not retroactively runtime-certified by these tests.

## Reliability proof and repair limits

Actual child process death exercised producer-after-commit, dispatcher-after-confirm-before-mark, consumer-after-effect/inbox-before-ACK and retry-intent-before-ACK. Accepted events remained durable; republished identity stayed stable; local duplicate effect was suppressed. Leases/claim races/fencing, payload/revision conflicts, future gaps and terminal expiry, actual deferred DB commit failure, malformed/oversized/version poison and authorized/denied replay were exercised. All12 domain runtime roles denied 132 foreign-schema reads plus owner SET ROLE/public CREATE; future-object/default-sequence tests ran for 14contexts.

FND06 injects TimeoutError **after a real confirmed publish** to model ambiguous acceptance. It does not prove a naturally timed half-open confirm or independent10 s confirm deadline. FND14/33 kill a client process, producing real TCP closure/redelivery, rather than detaching a Docker network. Broker/DB restarts preserve ordinary volumes; no HA/disk-loss/PITR claim. Retrying consumer work and its transport now have separate finite budgets. Unexpected non-DB handler exceptions use durable bounded backoff; DB exceptions abort the transaction and never ACK success. Terminal retry replay resets budgets under audit, preserves original identity and never deletes inbox history.

Review regressions repaired and executed include stale generations, approved digest replacement retirement, empty-generation explicit review, corrected-generation review rebinding, media-revision rollback, search rebuild/live-event race, delegated-command failure result, action-specific schema omissions, terminal content resend, confirmed-only cheap dedup and actual media-only retry/search rebuild/analytics effects. Catalog mutations use an owner lock, input generation/source/media watermarks and explicit replacement review. Search rebuild creates a separate namespace from its retained local archive and serializes pointer switching with live updates.

Source approvals wait for configuration success before activation. Failed dependencies produce explicit saga failure; already-awarded premium requires manual reconciliation. The fake sender preserves unknown outcomes and does not automatically resend; **exactly-once Telegram is not promised**. Fake side effects still happen within a single transaction, so real send-intent/result crash durability is unproven.

## V1 parity and unfinished requirements

V2_PARITY.md lists every discovered V1 route/feature with source file:line, owner and WORKING/MOCK_ONLY/DEFERRED/MISSING status. A narrow public/admin synthetic console and async API are available; they are not complete V1 frontend/API compatibility. Several list/detail/reject/admin adapters, original response schemas, bot dialogs/referral UX, rate-limit/error contracts and media serving are absent. A route existing or returning202 does not certify its old synchronous contract.

The26 table importer is a repeatable synthetic compatibility **archive**. Its tests prove counts/IDs/preservation, not transformation into active V2 entities or FK/event relationships. The112 table restore proves a same-cluster synthetic backup copy only. Full M09 active migration acceptance remains incomplete. Listener uses synthetic commands and persistent local journal, not a real Telegram account/session lease/fencing adapter; generic buffered threads fail closed instead of implementing the full V1 recipe set. These are recorded gaps, not silent approvals.

## Ten consequential remaining risks

1. VERIFIED P1: shared broker login/self-asserted delegated roles and shared HTTP signing key permit forged authority after owner compromise; SQL isolation does not fix it.
2. VERIFIED P1: fake notification/content send protocol does not prove committed intent/result around real external effects.
3. VERIFIED P1: archive-only import omits active entity/FK/delivery-history reconciliation; unsafe to cut over real data.
4. VERIFIED P1: incomplete original API/bot/UI/media contracts would break existing clients if switched now.
5. NOT VERIFIED P1: dependency vulnerabilities, transitive reproducibility and production hardening have no audit evidence.
6. VERIFIED P2: privileged whole-lab bootstrap/generic JSON records need owner-local upgrade histories and relational constraints/indexes.
7. VERIFIED P2: replay audit mutable via runtime DML; local replay actor string is not production RBAC.
8. VERIFIED P2: real listener session fencing/source-refresh and thread/album process-kill/load recovery are incomplete.
9. VERIFIED P2: saga compensation/entitlement refresh/retention/rate and unknown-send product policies remain provisional.
10. VERIFIED P1 by source: a command that exhausts outbox publication before reaching a consumer can remain accepted; terminal-publish operation reconciliation is missing. Confirm deadline under half-open network, HA/load/capacity/lag and disaster recovery also remain unproved.

V2_OPEN_ITEMS.md gives exact code locations, impacts and recommended repairs, plus D01–D08 alternatives. Their lab defaults are not approvals for production.

## Handoff and next work

First three implementation tasks: (1) authenticated per-owner broker publication/scoped command delegation with forgery negatives; (2) two-transaction fake send-intent/result protocol with real DB crash/race tests; (3) synthetic active-entity/relationship backfill for26 tables/T0–T6 with resumability and rollback evidence. Then complete API/bot/UI contract parity and hardening. Ready-to-copy focused repair prompts are in V2_OPEN_ITEMS.md.

Before any real deployment, approve D01–D08 production policies, real authentication/delegation, broker replication/budgets, source restoration/review/retention, media capabilities/readiness, API compatibility, consent/premium/unknown-send tradeoffs and an independently reviewed data migration/cutover. No such approval was inferred or executed.

Exact changed paths are in V2_CHANGED_PATHS.md; baseline hashes covered379 existing nonignored files. No original file was removed; modifications to existing files are confined to V2 drafts/contracts/current-state. Existing unrelated untracked files preserved. git diff --check and tracked-file diff are clean; since implementation is untracked, the hash/explicit manifest checks provide the additional preservation proof. Generated credentials and test artifacts remain ignored. Historical lab-only credential exposure/cache is documented; final proof uses fresh synthetic credentials, old project stopped/volumes retained, no global cleanup.

Run commands and operator/stop/recovery limitations are in V2_RUNBOOK.md. The final lab is left running with no published ports; original lab is stopped. No production migration, V1 service change, live message, commit or push occurred. This handoff ends the authorized isolated run with the explicitly incomplete gates above.
