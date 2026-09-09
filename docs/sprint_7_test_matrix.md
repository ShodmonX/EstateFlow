# Test Foundation and Flow Matrix

This engineering reference retains its established filename because regression tests read its migration inventory. See [testing](testing.md) for current commands and CI.

## Deterministic Environment

- `tests/conftest.py` sets `ENVIRONMENT=test`, removes real Telegram/OpenRouter/R2 credentials from test process env, and defaults timezone to UTC.
- External sockets are blocked by default. Only `127.0.0.1`, `::1`, and `localhost` are allowed; use `@pytest.mark.allow_network` only for explicit local container smoke checks.
- Tests use in-memory/fake adapters for Telethon, Aiogram handlers, OpenRouter/LLM, R2/object storage, Redis-like queues, Telegram publishers, and Ops notifications.
- Time-sensitive tests use fixed datetimes, usually `2026-08-01T09:00:00Z` from `tests.fixtures.FROZEN_NOW`.
- Shared state is avoided through new in-memory repositories per test, `tmp_path`, and fixture factories. No retry plugin is configured for flaky tests.

## Representative Fixture Dataset

`tests/fixtures.py` provides anonymous synthetic Telegram post fixtures:

- Uzbek monthly total rent with album/media and `family` targeting.
- Russian daily total rent.
- Mixed Uzbek/Russian per-person rent with `group_of_girls` targeting and `family_with_children` exclusion.
- One-time sale listing.
- Exact duplicate pair.
- Ambiguous duplicate candidate.

The fixture channel is synthetic (`@estateflow_fixture`), media uses `memory://` references, and text intentionally avoids real phone numbers, private channel media, and real user PII.

## Test Layers

Run commands:

```powershell
.venv\Scripts\python.exe -m pytest -m unit
.venv\Scripts\python.exe -m pytest -m integration
.venv\Scripts\python.exe -m pytest -m e2e
.venv\Scripts\python.exe -m pytest
```

Static quality:

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src
```

## Core Flow Matrix

| Core flow | Unit tests | Integration tests | E2E/full fake stack |
|---|---|---|---|
| Ingestion/listener pool/raw queue | `test_listener_pool.py`, `test_telegram_listener.py` | `test_media_buffer_queue.py`, `test_sprint1_integration.py` | `test_sprint3_final_flow.py`, `test_sprint7_e2e_regression.py` |
| Source parsing | `test_source_config.py`, `test_source_parsing.py`, `test_source_parser_benchmark_cli.py` | `test_source_parsing.py`, `test_admin_sources_enable.py` | Labeled synthetic replay CLI |
| Pre-AI filter | `test_pre_ai_dedup.py` | `test_sprint1_integration.py` | `test_sprint3_final_flow.py`, `test_sprint7_e2e_regression.py` |
| AI extraction/media/R2 | `test_sprint2_ai_client.py`, `test_sprint2_extraction.py` | `test_sprint2_media_and_worker.py` | `test_sprint3_final_flow.py`, `test_sprint6_integration_handoff.py`, `test_sprint7_e2e_regression.py` |
| Post-AI dedup | `test_post_ai_dedup.py` | `test_sprint2_media_and_worker.py` | `test_sprint3_final_flow.py`, `test_sprint7_e2e_regression.py` |
| Search/saved filters | `test_sprint4_search.py` | `test_sprint4_saved_filters_and_bot.py` | `test_sprint5_e2e.py`, `test_sprint7_e2e_regression.py` |
| Notification | `test_sprint5_notifications.py` | `test_sprint5_notification_delivery.py`, `test_sprint7_metrics.py` | `test_sprint5_e2e.py`, `test_sprint7_e2e_regression.py` |
| NLP search | `test_sprint5_nlp_search.py` | `test_sprint5_nlp_search.py` | `test_sprint5_e2e.py`, `test_sprint7_e2e_regression.py` |
| Referral | `test_sprint5_referrals.py` | `test_sprint7_metrics.py` | `test_sprint5_e2e.py`, `test_sprint7_e2e_regression.py` |
| Admin review/tags/source suggestions | `test_post_ai_dedup.py` | `test_sprint6_admin_tags_content.py` | `test_sprint6_integration_handoff.py`, `test_sprint7_e2e_regression.py` |
| Content scheduling | `test_sprint6_content_pipeline.py` | `test_sprint6_admin_tags_content.py` | `test_sprint6_integration_handoff.py` |
| Beta metrics | `test_sprint7_metrics.py`, `test_sprint7_test_foundation.py` | `test_sprint7_metrics.py` | Endpoint exercised with fake app state |
| Release readiness | `test_sprint7_release_readiness.py`, `scripts/sprint7_release_dry_run.py` | `test_sprint7_release_readiness.py` | Feature flags, allowlist, rollback gates, fake load smoke |

## Migration Strategy

Legacy SQL files retained for bridge/reference coverage (Alembic is the current migration path):

- `001_listener_pool.sql`
- `002_mvp_schema_and_post_ai_dedup.sql`
- `003_sprint3_schema_contract_adjustments.sql`
- `004_sprint4_search_and_saved_filters.sql`
- `005_sprint5_notification_matching.sql`
- `006_sprint5_notification_delivery.sql`
- `007_sprint5_referral_premium.sql`
- `008_sprint6_admin_tags_content.sql`
- `009_sprint7_analytics_events.sql`
- `010_sprint7_technical_metric_events.sql`
- `011_media_per_announcement.sql`

For current schema verification, run the Docker-backed Alembic and runtime tests described in [testing](testing.md). The local PostgreSQL container is started separately by `scripts/start_local_postgres.ps1`; there is no `postgres` service in Compose. `scripts/migration_smoke.py` checks the current Alembic contract on an isolated schema. See [database migrations](database-migrations.md) before any upgrade or bridge stamp.

## CI

The workflow in `.github/workflows/ci.yml` runs Ruff, MyPy, and the full pytest suite with a RabbitMQ service. Database/runtime tests use disposable Docker infrastructure when available. Infrastructure skips must be reported separately from passing tests.
