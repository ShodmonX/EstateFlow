# Sprint 1 Handoff

## Scope Boundary

Sprint 0 provides the runtime foundation only: app factory, config, Docker, health
checks, structured logging, correlation IDs, redaction, and Ops notification
abstractions. It does not connect to real Telegram, OpenRouter, R2, or production
cloud services.

## Source Configuration Contract

Sprint 1 listener code should depend on:

```python
from estateflow.services.source_config import SourceConfig, SourceConfigProvider
```

`SourceConfigProvider.list_active_sources()` is the boundary for retrieving active
sources. Initial Sprint 1 implementations may be static, database-backed, or
environment-backed, but listener code should consume the protocol rather than
reading raw environment variables directly.

`SourceConfig` fields reserved for Sprint 1:

- `source_id`: stable internal ID.
- `source_type`: `telegram_channel`, `telegram_group`, or `website`.
- `identifier`: Telegram username/link or website URL.
- `adapter_name`: optional parser/adapter selector.
- `listener_account_key`: optional account-pool assignment key.

## Queue Contract

Sprint 1 ingestion should publish raw source events through:

```python
from estateflow.services.queue import EventQueue, QueueMessage, RAW_ANNOUNCEMENT_QUEUE
```

The queue name for raw announcement ingestion is:

```text
ingestion.raw_announcements
```

`QueueMessage.correlation_id` must be propagated from the listener event into all
downstream logs and Ops alerts.

The current raw ingestion payload contract is documented in
[`raw_ingestion_contract.md`](raw_ingestion_contract.md). Sprint 2 AI workers
should consume the `telegram.raw.v1` schema rather than Telethon-specific objects.

The conservative Pre-AI duplicate filter contract is documented in
[`pre_ai_dedup_contract.md`](pre_ai_dedup_contract.md). Only exact forward-origin
and exact normalized-text matches may skip AI; phone, pHash, and near-text signals
remain reviewable signals that continue to AI.

## Ops Event Contract

Critical operational signals should use the app-level Ops notifier contract:

```python
await ops_notifier.notify(
    severity="critical",
    reason="short sanitized reason",
    correlation_id=correlation_id,
)
```

Use `estateflow.application.core.redaction.redact_text()` or structured redaction
before sending any reason that may contain phone numbers, tokens, raw listing
text, Telegram session paths, or API keys. If `OPS_BOT_TOKEN` or `OPS_CHAT_ID` is
missing, the notifier is disabled and startup must continue.

## Configuration Notes

- Runtime DB URL: `Settings.database_url` (`postgresql+asyncpg`).
- Native asyncpg URL: `Settings.postgres_url` (`postgresql`).
- Alembic URL: `Settings.sync_database_url` (`postgresql+psycopg2`).
- Docker uses `.env` by default through `ENV_FILE`. For config validation without
  exposing secrets, use `.env.example`.

## Validation Before Sprint 1 Work

Run the Sprint 0 smoke path before adding listener code:

```powershell
.venv\Scripts\python.exe scripts\smoke_check.py
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src tests
```
