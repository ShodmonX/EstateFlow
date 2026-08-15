# Sprint 7 MVP Beta Validation Handoff

Status: beta-ready recommendation for a controlled 50-100 user dry-run cohort, with real launch still requiring explicit owner approval. No production deployment, real Telegram message, real listener expansion, or beta user mutation was performed during this quality gate.

## Quality Gate Evidence

| Group | Command | Result |
|---|---|---|
| Static lint | `.venv\Scripts\python.exe -m ruff check .` | Passed |
| Static types | `.venv\Scripts\python.exe -m mypy src` | Passed, 68 source files |
| Unit | `.venv\Scripts\python.exe -m pytest -m unit` | 107 passed, 113 deselected, 1 warning |
| Integration | `.venv\Scripts\python.exe -m pytest -m integration` | 99 passed, 121 deselected, 1 warning |
| E2E | `.venv\Scripts\python.exe -m pytest -m e2e` | 14 passed, 206 deselected, 1 warning |
| Migration marker | `.venv\Scripts\python.exe -m pytest -m migrations` | 4 passed, 216 deselected, 1 warning |
| Full suite | `.venv\Scripts\python.exe -m pytest` | 220 passed, 1 warning |
| Migration smoke | Docker Postgres throwaway schema | 001-010 applied, analytics/technical inserts returned 1/1, schema dropped |
| Release dry-run load | `.venv\Scripts\python.exe scripts\sprint7_release_dry_run.py --users 75 --listings 150 --workers 4` | No external calls; queue 150 -> 0; fake notifications 75/75; search p50 0.105 ms |
| Secret/session scan | `rg` patterns + ignored file check | No private key/AWS key/bot token/session/dump matches; local `.env` is ignored |

The repeated warning is existing `StarletteDeprecationWarning` from `fastapi.testclient` importing `starlette.testclient`; no skipped or xfailed tests were reported. Marker inventory shows duplicate custom marker descriptions from `pyproject.toml` plus `tests/conftest.py`; this is non-blocking housekeeping.

## Core Acceptance Audit

| Acceptance item | Evidence | Status |
|---|---|---|
| 2-account ingestion | `test_sprint7_e2e_regression.py::test_e2e_two_listener_album_to_dedup_single_parent_and_phone_only_new_listing` | Covered |
| Pre-AI conservative dedup | E2E duplicate skip plus phone-only remains new listing | Covered |
| Renovation survives with images when validated from text | `test_ai_worker_allows_text_renovation_with_images` | Covered |
| R2/media references | media storage tests assert `memory://r2/` references and cleanup on failure | Covered |
| Parent-only search | search tests and E2E canonical parent result flow | Covered |
| Saved filters and notification idempotency | E2E saved filter notification retry creates no second notification | Covered |
| NLP fallback | Uzbek/Russian/mixed NLP E2E and malformed LLM wizard fallback | Covered |
| Referral premium | 5 active referrals grant premium and high-priority notification | Covered |
| Admin review/source approval | protected admin source suggestion, tag review, manual review flows | Covered |
| Dynamic flat tags | flat tag tests, no `parent_tag_id`, `young_family` maps to `family` | Covered |
| Content cap | dry-run content automation capped to 1-2 posts/day | Covered |
| Beta metrics | activation, D7, referral conversion, notification engagement, timezone, technical metrics, protected endpoint | Covered |

## Supported MVP Workflows

- Fake/full-stack ingestion: fake listener accounts, album/media, raw queue, Pre-AI dedup, AI extraction, media storage, post-AI dedup, canonical parent persistence.
- Bot search: registration, FSM search, saved filters, parent-only search results.
- Notifications: saved-filter matching, idempotent job creation, fake Telegram delivery, retry/error observability.
- NLP: natural-language criteria extraction for Uzbek/Russian/mixed inputs with wizard fallback on LLM failure.
- Referral: accepted invite, activation after first search/filter, 5th activation premium milestone, priority notifications.
- Admin: protected source suggestion approval/reject, flat audience tag approve/merge/reject, manual review actions, beta metrics dashboard.
- Content automation: dry-run channel posts with daily cap and safe publisher fallback.

## Release Controls

Default-safe feature flags:

- `bot_access`
- `listener_sources`
- `ai_processing`
- `notifications`
- `nlp`
- `content_publishing`

Beta cohort is explicit allowlist only through `BETA_ALLOWLIST_USER_IDS` or future admin-approved cohort storage. Empty allowlist denies access even when `bot_access=true`. Protected controls are available at:

```text
GET /admin/release/flags
POST /admin/release/flags
Header: x-admin-token: <ADMIN_API_TOKEN>
```

Rollback order: disable `content_publishing`, `notifications`, `nlp`, `ai_processing`, `listener_sources`, then `bot_access` if user-facing access must close.

## Metrics and Dashboard

Protected beta metrics endpoint:

```text
GET /admin/metrics/beta?start=2026-08-01&end=2026-08-08&timezone_name=Asia/Tashkent&d7_window_hours=24
Header: x-admin-token: <ADMIN_API_TOKEN>
```

Metric definitions:

- Activation rate: users with at least one search or saved filter / registered users.
- D7 retention: users in registration cohort returning in day-7 configured window / registration cohort.
- Referral conversion: active invited users / accepted invited users.
- Notification engagement: explicit action/click/read callbacks / sent notifications. `notification_sent` is delivery, not engagement; Telegram read receipts are not inferred.

Technical metrics remain separate: queue delay, AI fallback/validation failure, dedup rate, notification retry/error, listener health.

## Runbooks

- Test matrix and CI/local commands: `docs/sprint_7_test_matrix.md`.
- Beta metrics and soft-launch checklist: `docs/sprint_7_beta_metrics_handoff.md`.
- Release flags, launch checklist, rollback runbook, dry-run load evidence, risk register: `docs/sprint_7_release_readiness.md`.

## Known Limitations

- Real production backup/restore has not been executed in this quality gate; only Docker throwaway schema migration smoke was run.
- Real Telegram sessions, listener accounts, OpenRouter, R2, publisher channel, and beta users were not touched.
- Load evidence is from local fake stack only and is not a production capacity claim.
- Marker descriptions are duplicated in marker inventory because both `pyproject.toml` and `tests/conftest.py` register them.
- Starlette `TestClient` deprecation warning remains dependency housekeeping.

## Release Blockers

No P0/P1 code blockers found in the dry-run quality gate.

| Severity | Item | Owner | Required before real launch |
|---|---|---|---|
| P1 | Production backup/restore evidence missing | Ops owner | Take backup, verify restore path/checksum before migrations. |
| P1 | Real session directory permissions not verified | Ops owner | Verify Telethon `.session` files are volume-only, not git-tracked, listener-only permissions. |
| P1 | Real env validation pending | Release engineer | Validate secrets presence without printing values; keep risky flags off until approval. |
| P2 | Dependency deprecation warning | Backend owner | Plan Starlette/httpx test dependency update after beta gate. |
| P3 | Duplicate marker descriptions | Backend owner | Consolidate marker registration after Sprint 7. |

## Exit Criteria

Validation thresholds for beta decision, not current measured results:

- Notification engagement: 20%+.
- D7 retention: 30%+.
- Referral conversion: visible organic growth signal from invited active users.

Recommendation: beta-ready for a controlled fake-to-staging handoff and owner-approved 50-100 user beta window, provided the P1 operational checks above are completed first. This document does not authorize or perform real launch.
