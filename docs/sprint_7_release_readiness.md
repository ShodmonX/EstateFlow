# Sprint 7 Release Readiness and Rollback Controls

Status: dry-run only. No production deploy, no real Telegram messages, and no beta user changes were performed.

## Release Control Audit

Feature flags are typed in `FeatureFlagSet`, default-safe, and stored in PostgreSQL with
immutable audit history. Environment values seed fresh databases, but runtime changes
remain authoritative across restarts and across API/worker/bot processes.

| Flag | Default | Rollback effect |
|---|---:|---|
| `bot_access` | off | Blocks bot registration and user flows except admin-approved beta access. |
| `listener_sources` | off | `FeatureGatedSourceProvider` returns no active sources, pausing ingestion expansion. |
| `ai_processing` | off | Composition must not start AI workers unless enabled. |
| `notifications` | off | `FeatureGatedTelegramNotificationClient` blocks Telegram notification send. |
| `nlp` | off | Bot NLP callbacks/text flow are unavailable while FSM search can remain available. |
| `content_publishing` | off | `FeatureGatedContentPublisher` returns dry-run publish results. |

Runtime flag changes are audit logged with flag name, previous/new value, actor, reason, and UTC timestamp. Protected status/change endpoint:

```text
GET/POST /admin/release/flags
Header: x-admin-token: <ADMIN_API_TOKEN>
```

The change payload must include `expected_version`; stale writes are rejected with a
conflict instead of silently overwriting the current value.

## Beta Cohort Policy

- Beta access is explicit allowlist only through `BETA_ALLOWLIST_USER_IDS` or a future admin-approved cohort store.
- The repository and dry-run do not add real Telegram user IDs.
- Empty allowlist with `bot_access=true` still denies access.
- Existing users are not implicitly migrated into beta; access must be granted by explicit admin action outside this dry-run.

## Launch Checklist

1. Confirm incident owner, backup owner, and release engineer are named for the window.
2. Freeze feature flags: keep all risky flags off until the owner approves each one.
3. Validate environment without printing secrets: `ENVIRONMENT`, DB, Redis, admin token, Telegram tokens, OpenRouter, R2, content channel, session dir.
4. Verify secret/session hygiene: no `.env`, `.session`, dumps, media, or private channel files in git; session dir permissions are listener-only.
5. Take database backup and record restore command/checksum.
6. Apply migrations through `010_sprint7_technical_metric_events.sql` in staging/test first.
7. Run health/readiness checks: `/health/live`, `/health/ready`, DB, Redis, fake adapters.
8. Check queues and DLQ: raw ingestion, AI processing, post-AI dedup, notification, content schedule.
9. Check source account health: online/reconnecting/flood_wait/banned counts and recent successful event.
10. Run fake E2E and release dry-run load checks.
11. Verify admin endpoints require `x-admin-token`.
12. Verify metrics endpoint returns beta product metrics and technical metrics without PII.
13. Keep real listener, AI, notification, NLP, and content publishing flags off until explicit launch approval.

## Rollback Runbook

1. Declare incident owner and timestamp.
2. Disable flags in this order: `content_publishing`, `notifications`, `nlp`, `ai_processing`, `listener_sources`, then `bot_access` if user-facing access must close.
3. Pause workers after queues are preserved; do not purge queues unless data owner approves.
4. Check DLQ/backlog and export counts for incident notes.
5. Revert application version only after flags are off and migration compatibility is confirmed.
6. If migration rollback is required, restore from verified backup rather than hand-editing production data.
7. No cache invalidation or propagation delay is required because the runtime reads the database directly.
7. Send user-facing outage message only through an approved comms channel:

```text
EstateFlow beta vaqtincha texnik xizmat rejimida. Saqlangan ma'lumotlaringizni yo'qotmaslik uchun xizmat qisqa muddatga pauza qilindi. Yangilanish tugashi bilan qayta xabar beramiz.
```

8. Keep monitoring for queue recovery, listener health, notification errors, AI failure rate, and search latency.
9. Write post-incident notes: trigger, customer impact, mitigation, owner, and follow-up.

## Dry-Run Procedure

Local fake-stack command:

```powershell
.venv\Scripts\python.exe scripts\sprint7_release_dry_run.py --users 75 --listings 150 --workers 4
```

Expected properties:

- `real_external_calls` is `false`.
- Queue backlog drains to zero.
- Search latency is measured from synthetic in-memory canonical listings.
- Notification delivery uses fake Telegram only.
- Result is evidence, not a production capacity claim.

## Dry-Run Evidence

Measured on 2026-08-01 in local fake stack:

```json
{
  "dry_run": true,
  "real_external_calls": false,
  "users": 75,
  "listings": 150,
  "workers": 4,
  "queue": {
    "backlog_before": 150,
    "processed": 150,
    "backlog_after": 0,
    "elapsed_ms": 0.328
  },
  "search_latency_ms": {
    "count": 75,
    "p50": 0.048,
    "max": 0.097
  },
  "notifications": {
    "picked": 75,
    "sent": 75,
    "retryable_failures": 0,
    "permanent_failures": 0,
    "rate_limited": 0,
    "skipped": 0,
    "elapsed_ms": 1.668,
    "fake_telegram_sent": 75
  }
}
```

These are measured local dry-run numbers only and are not a production capacity claim.

## Security and Operational Review

| Priority | Risk | Owner | Mitigation before beta |
|---|---|---|---|
| P0 | Real Telegram sends while beta is unapproved | Release engineer | Keep `notifications=false`; require fake Telegram in dry-run; check `/admin/release/flags`. |
| P0 | Real listener source expansion without approval | Release engineer | Keep `listener_sources=false`; use gated source provider; do not add real sources. |
| P0 | Content posts to production channel | Release engineer | Keep `content_publishing=false`; gated publisher returns dry-run. |
| P1 | Admin endpoint token missing or leaked | Backend owner | Production validation requires `ADMIN_API_TOKEN`; admin endpoints are protected. |
| P1 | Telethon session leakage | Ops owner | `.session` files stay out of git and in restricted volume; validate directory permissions before beta. |
| P1 | Migration incompatibility | Backend owner | Backup first; apply migrations in test/staging; restore backup for rollback. |
| P1 | Secret/log leakage | Backend owner | Run redaction tests; do not print env secret values; scan repository before launch. |
| P2 | External AI/R2 outage | Backend owner | Keep AI flag off for rollback; fake adapters in tests; observe `ai_validation_failure` and fallback metrics. |
| P2 | Queue backlog during beta | Release engineer | Monitor queue delay/backlog; disable listener/AI flags before backlog causes data loss. |

Review evidence from this dry-run:

- Private key/AWS access key/Telegram bot token pattern scan: no matches.
- Credential name scan found only config/tests/docs references, not secret values.
- `.env` exists locally but is ignored by `.gitignore`; it was not added to git.
- Admin release flags endpoint was tested for 403 without `x-admin-token`.
- Full fake-stack tests passed without real network credentials.

## Readiness Decision

Dry-run ready means controls and evidence exist. It does not authorize production launch.
