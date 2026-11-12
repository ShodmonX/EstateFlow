# V2 progress checkpoint

Runtime cleanup checkpoint, 2026-11-11: user requested stopping/removing unused test containers. Project-scoped Compose down --timeout 20 completed with exit0 for estateflow-v2-isolated and estateflow-v2-isolated-verify20261110; 40 containers and their private networks removed. Verification: zero containers for both exact project labels; all four named DB/broker volumes preserved. No -v, image deletion, global prune or V1/production operation. No application tests rerun. The deployment/runtime statements below describe the historical 2026-11-10 verification run; the test lab is currently down and must be recreated with Compose before future integration tests.

Updated 2026-11-10. User authorization: FULL ISOLATED M00–M10; no intermediate lab design gates. Run ended with **PARTIALLY VERIFIED** result, not a production gate. Resume future repair from this file, current-state.md, FINAL_V2_REPORT.md and V2_OPEN_ITEMS.md.

Baseline main/addb856a99d24946ef127384121b99de371c017b unchanged.379 existing nonignored file hashes captured at /tmp/efv2-full-baseline.json; no original files removed. Unrelated user changes/untracked materials retained. No V1 tracked source/infrastructure changes, production connections, real sessions/providers/sends, host installs, commits/pushes or volume deletion.

| Milestone | Actual result | Evidence / remaining work |
|---|---|---|
| M00 | COMPLETE LAB inventory |26 table map, V1 route/feature ledger, contracts reconciled; physical legacy schema/data unverified |
| M01 | RUNNING / PARTIAL hardening |53 real foundation tests + genuine restart/outage driver cover34 FND IDs; shared broker principal/confirm deadline/replay ACL/per-owner upgrades remain open |
| M02 | IMPLEMENTED SYNTHETIC SUBSET |Journal, edits/deletes/albums, parser/digest/quarantine/reparse tested; real session fencing, full thread recipes and album process-kill/load proof incomplete |
| M03 | IMPLEMENTED FAKE providers / real codec |Confirmed-only pre-AI signals, fake extraction, actual JPEG/PNG/WebP and fake objects; public multi-asset delivery/live provider parity incomplete |
| M04 | IMPLEMENTED LAB catalog |Evidence/tombstones/merge/review/supersession; stale/empty/corrected generations/media regression gates pass; full load/migration constraints not proved |
| M05 | IMPLEMENTED LAB search |Owner projection, filtering, event archive, rebuild/live serialization; complete V1 differential parity/snapshot bootstrap absent |
| M06 | IMPLEMENTED FAKE notifications/identity |Separate matcher/sender same owner; unknown hold/stale entitlement/premium; real external-send committed-intent protocol and full referral parity absent |
| M07 | IMPLEMENTED LAB domains |Control/taxonomy/content/analytics; saga failures explicit; automatic content scheduler and reward compensation incomplete |
| M08 | PARTIAL compatibility |Independent public/admin/internal/bot HTTP + narrow static UI; many old contracts/dialogs/UI workflows missing/deferred |
| M09 | EXECUTED / PARTIAL migration |Fresh project,146pytest checks, actual infrastructure faults, independent search restart,112 table restore;26 row archive only, active-entity migration absent |
| M10 | REPORTS COMPLETE / HARDENING OPEN |Build/Ruff/MyPy/pip consistency, runtime isolation and final evidence complete; CVE/browser/load/HA/live/provider/security gates remain open |

Final fresh project: estateflow-v2-isolated-verify20261110; ignored infra/v2/.env.verification.local mode0600.20 declared containers (19 running, migrate exited0), private internal network/two own volumes, no host ports or runtime bind mounts,17 app processes uid10001. Original estateflow-v2-isolated project stopped preserving all volumes. A synthetic restored database remains in the fresh cluster; no cleanup/destructive operation.

Final results:85 offline unit/contract tests PASS;53actual PG/Rabbit foundation tests PASS;5 real owner integration tests PASS;3 deployed HTTP/network tests PASS after search-only restart;0 failures/0 skips. Genuine FND05/23/24 driver exit0. Strict JSON62 files/37 schemas exit0; final image Ruff exit0, MyPy41 files exit0; pip check exit0; runtime inspector exit0/20 containers; synthetic restore exit0/112 table count/content digests. Exact commands/case statuses in V2_TEST_RESULTS.json/.md; JUnit/sanitized metadata under ignored .artifacts/v2/final.

Review repairs verified red/green: independent retry transport cap; terminal retry replay; unexpected application-error backoff; stale generation and reviewed digest/empty replacement; corrected review rebinding; media revision guard; search rebuild/live serialization; delegated command failure result; required action arguments; terminal content hold; real media-only retry, analytics record effects; pre-AI confirmed evidence and daily price normalization. Earlier failures are not counted as successes.

Known limitation: FND06 injects an exception after actual confirmed publish; FND14/33 use real process/TCP loss, not network blackhole. Broker/DB restarts/restore do not prove HA/PITR. Fake send in one transaction is not a real external side-effect protocol. Historical generated-lab credentials appeared in an early cache/long traceback; image context fixed, final project/keys regenerated, old project stopped; no real credential accessed. Details V2_DEVIATIONS.md.

Additional source finding: terminal outbox publication does not finalize an accepted operation; dedicated end-to-end regression NOT_RUN, documented P1.

Next proposed repair: per-owner broker authentication/scoped actor delegation. Then two-transaction fake send ledger, active synthetic26 table migration and full API/bot/UI parity. No continuation into production or real providers. D01–D08 production decisions remain OPEN; see V2_OPEN_ITEMS.md ready-to-copy prompts. No currently running agent work is promised.
