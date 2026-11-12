# V1 feature and endpoint parity ledger

Baseline main addb856a99d24946ef127384121b99de371c017b. Initial inventory captured before V2 implementation; statuses updated only with executable proof. MOCK_ONLY means fake provider contract, never live-provider parity. DEFERRED/MISSING are not passes.

| V1 route / feature | Source | V2 owner | Status | Test / limitation |
|---|---|---|---|---|
| POST /webapp/auth | `src/estateflow/api/webapp.py:114` | identity adapter | MOCK_ONLY | v2/tests/unit/test_api.py; e2e/test_http_stack.py: signed synthetic identity at /api/webapp/auth. No real Telegram auth. |
| GET /webapp/me | `src/estateflow/api/webapp.py:136` | identity adapter | DEFERRED | Signed subject works; response only user_id/mode, full V1 profile fields absent. |
| POST /webapp/search/nlp | `src/estateflow/api/webapp.py:144` | search adapter | DEFERRED | Numeric mock route exists; dedicated NLP HTTP/compatibility check NOT_RUN; no LLM semantic parity. |
| GET /webapp/search/announcements | `src/estateflow/api/webapp.py:178` | search | DEFERRED | Deployed HTTP search works at /api/webapp/search/announcements; V2 snapshot/result shape differs. |
| GET /webapp/announcements/{announcement_id} | `src/estateflow/api/webapp.py:251` | search | DEFERRED | Own snapshot detail/hide tested over HTTP; V1 body/media contract differs. |
| GET /webapp/filters | `src/estateflow/api/webapp.py:304` | notifications | DEFERRED | Own filters read path in v2/apps/api.py; full V1 response parity not proved. |
| POST /webapp/filters | `src/estateflow/api/webapp.py:314` | notifications | DEFERRED | Synthetic save tested unit + HTTP pipeline; returns async202 rather than V1 synchronous shape. |
| PATCH /webapp/filters/{filter_id} | `src/estateflow/api/webapp.py:333` | notifications | DEFERRED | Route exists but partial PATCH currently defaults criteria/name/enabled; preservation semantics need contract test/fix. |
| DELETE /webapp/filters/{filter_id} | `src/estateflow/api/webapp.py:365` | notifications | DEFERRED | Disable command exists; async202 and compatibility response contract unverified. |
| GET /search/announcements | `src/estateflow/api/search.py:39` | search | DEFERRED | Deployed HTTP search works at /api/search/announcements; full V1 request/response parity not established. |
| GET /admin/source-suggestions/pending | `src/estateflow/api/admin.py:509` | control / analytics | DEFERRED | Owner-local records exposed by V2 adapter; full V1 filtering/metrics/detail response contract differs. |
| GET /admin/source-suggestions/{suggestion_id} | `src/estateflow/api/admin.py:523` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/source-suggestions/{suggestion_id}/approve | `src/estateflow/api/admin.py:540` | control / routed owner | DEFERRED | Narrow V2 async route/generic owner commands exist; full V1 request/response, errors and workflow parity unverified. |
| POST /admin/source-suggestions/{suggestion_id}/reject | `src/estateflow/api/admin.py:571` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| GET /admin/audience-tags/pending | `src/estateflow/api/admin.py:602` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| GET /admin/audience-tags/{tag_key} | `src/estateflow/api/admin.py:616` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/audience-tags/{tag_key}/approve | `src/estateflow/api/admin.py:636` | control / routed owner | DEFERRED | Narrow V2 async route/generic owner commands exist; full V1 request/response, errors and workflow parity unverified. |
| POST /admin/audience-tags/{tag_key}/reject | `src/estateflow/api/admin.py:666` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/audience-tags/{tag_key}/merge | `src/estateflow/api/admin.py:696` | control / routed owner | DEFERRED | Narrow V2 async route/generic owner commands exist; full V1 request/response, errors and workflow parity unverified. |
| GET /admin/manual-review/pending | `src/estateflow/api/admin.py:726` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| GET /admin/manual-review/{review_id} | `src/estateflow/api/admin.py:757` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/manual-review/{review_id}/actions | `src/estateflow/api/admin.py:775` | control / routed owner | DEFERRED | Narrow V2 async route/generic owner commands exist; full V1 request/response, errors and workflow parity unverified. |
| POST /admin/content/daily | `src/estateflow/api/admin.py:826` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| GET /admin/metrics/beta | `src/estateflow/api/admin.py:846` | control / analytics | DEFERRED | Owner-local records exposed by V2 adapter; full V1 filtering/metrics/detail response contract differs. |
| GET /admin/release/flags | `src/estateflow/api/admin.py:893` | control / analytics | DEFERRED | Owner-local records exposed by V2 adapter; full V1 filtering/metrics/detail response contract differs. |
| POST /admin/release/flags | `src/estateflow/api/admin.py:923` | control / routed owner | DEFERRED | Narrow V2 async route/generic owner commands exist; full V1 request/response, errors and workflow parity unverified. |
| POST /admin/release/beta-allowlist | `src/estateflow/api/admin.py:957` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| GET /admin/adapters | `src/estateflow/api/admin.py:976` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/adapters/reload | `src/estateflow/api/admin.py:986` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/telegram/auth/start | `src/estateflow/api/admin.py:999` | listener adapter | DEFERRED | Live Telegram intentionally forbidden. POST session-auth calls return409; other V1 session routes absent. |
| POST /admin/telegram/auth/confirm-code | `src/estateflow/api/admin.py:1038` | listener adapter | DEFERRED | Live Telegram intentionally forbidden. POST session-auth calls return409; other V1 session routes absent. |
| GET /admin/telegram/auth/state/{session_name} | `src/estateflow/api/admin.py:1079` | listener adapter | DEFERRED | Live Telegram intentionally forbidden. POST session-auth calls return409; other V1 session routes absent. |
| DELETE /admin/telegram/auth/{session_name} | `src/estateflow/api/admin.py:1104` | listener adapter | DEFERRED | Live Telegram intentionally forbidden. POST session-auth calls return409; other V1 session routes absent. |
| GET /admin/telegram/sessions | `src/estateflow/api/admin.py:1118` | listener adapter | DEFERRED | Live Telegram intentionally forbidden. POST session-auth calls return409; other V1 session routes absent. |
| POST /admin/telegram/auth/confirm-password | `src/estateflow/api/admin.py:1129` | listener adapter | DEFERRED | Live Telegram intentionally forbidden. POST session-auth calls return409; other V1 session routes absent. |
| POST /admin/sources/enable | `src/estateflow/api/admin.py:1172` | control / routed owner | DEFERRED | Narrow V2 async route/generic owner commands exist; full V1 request/response, errors and workflow parity unverified. |
| POST /admin/source-parser/preview | `src/estateflow/api/admin.py:1258` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/listeners/refresh | `src/estateflow/api/admin.py:1334` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| GET /admin/listeners/status | `src/estateflow/api/admin.py:1345` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/ai/test-extract | `src/estateflow/api/admin.py:1356` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /admin/ai/extract-test | `src/estateflow/api/admin.py:1357` | control / routed owner | MISSING | No compatible V2 route/workflow. Generic owner commands are not V1 endpoint parity. |
| POST /internal/source-suggestions | `src/estateflow/api/internal.py:17` | control / routed owner | DEFERRED | Narrow V2 async route/generic owner commands exist; full V1 request/response, errors and workflow parity unverified. |
| GET /health/live | `src/estateflow/api/health.py:11` | all adapters/owners | WORKING | e2e/test_http_stack.py checks16 internal HTTP readiness endpoints; owner readiness includes DB/worker heartbeat. |
| GET /health/ready | `src/estateflow/api/health.py:16` | all adapters/owners | WORKING | e2e/test_http_stack.py checks16 internal HTTP readiness endpoints; owner readiness includes DB/worker heartbeat. |
| Telegram edits/deletes/albums | `src/estateflow/services/telegram_listener.py:248` | listener | MOCK_ONLY | test_domains.py lifecycle/album tests; deployed HTTP two edits/delete; synthetic journal, no real account/session fencing. |
| Recipe/digest/quarantine | `src/estateflow/services/ingestion_pipeline.py:48` | source_parser | MOCK_ONLY | test_domains.py digest/reparse/quarantine; generic recipes only, buffered thread fails closed. |
| Extraction and normalization | `src/estateflow/services/extraction.py:163` | ai | MOCK_ONLY | test_media.py + test_domains.py; deterministic fake extraction; daily normalization proved. |
| Search semantics | `src/estateflow/repositories/sqlalchemy.py:391` | search | DEFERRED | Price/visibility/filter/order/query errors/rebuild tested; full V1 fixture-differential matrix absent. |
| Notification due/unknown send | `src/estateflow/services/notifications.py:410` | notifications | MOCK_ONLY | test_domains.py unknown hold + review_regressions stale entitlement/premium; no real external-side-effect crash protocol. |
| Bot workflows | `src/estateflow/bot/controller.py:1` | bot adapter | DEFERRED | /start, /search, /filters synthetic commands exist; complete dialogs/referral/real transport absent. |
| Media transformation | `src/estateflow/services/media_storage.py:368` | ai | WORKING | test_media.py real JPEG/PNG/WebP codec/EXIF/resize/hash; fake storage and no public media download. |
| Referrals/premium | `src/estateflow/repositories/sqlalchemy.py:185` | identity | DEFERRED | Owner-local activation/premium handlers + real source reward saga; complete V1 threshold/expiry/referral UX parity unverified. |
| Tag remap | `src/estateflow/repositories/sqlalchemy.py:835` | taxonomy | WORKING | test_domains.py remap converges catalog/filter/search; complete admin lists/reject/ack reconciliation still missing. |
| Content schedule | `src/estateflow/services/content_automation.py:398` | content | MOCK_ONLY | test_domains.py unknown + review_regressions terminal no-resend; fake command-driven schedule, automatic publication worker absent. |

Statuses apply only to the stated capability. WORKING is narrow executed behavior, not whole-feature parity; MOCK_ONLY includes fake providers; DEFERRED includes implemented but incompatible/incompletely tested interfaces. Original V1 files are unchanged. No overall parity percentage is claimed. The public/admin console has static HTTP smoke only, no browser automation or original frontend compatibility proof.
