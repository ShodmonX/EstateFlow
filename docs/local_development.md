# Local Development Runbook

## Prerequisites

- Python 3.12+
- Docker with Docker Compose

## Environment

Copy the example file for local overrides:

```powershell
Copy-Item .env.example .env
```

The checked-in example contains only non-secret placeholders. Real Telegram,
OpenRouter, R2, and database secrets must stay in `.env` or a secret manager.
PostgreSQL is not run by Compose: development uses the isolated DigitalOcean
`estateflow_dev` database through the local forward `localhost:6529`.
Redis and RabbitMQ remain local containers.

## Local Containers

Validate Compose:

```powershell
$env:ENV_FILE=".env.example"; docker compose --env-file .env.example config
```

Avoid running `docker compose config` against a real `.env` that contains secrets,
because Compose renders environment values in its output.

Start the local stack:

```powershell
docker compose up --build
```

The API is exposed on:

```text
http://localhost:8000/health/live
http://localhost:8000/health/ready
```

Redis and RabbitMQ are bound to localhost only:

```text
Redis:      localhost:6379
RabbitMQ:   localhost:5672
```

Inside Docker, the API uses the remote database and local Redis/RabbitMQ:

```text
DB_HOST=localhost
DB_PORT=6529
DB_NAME=estateflow_dev
DB_USER=estateflow_dev__dev_user
REDIS_HOST=redis
```

## Data Persistence

PostgreSQL data is managed and backed up on the DigitalOcean host. The local
Compose stack persists only Redis and RabbitMQ data.

Local Redis append-only data is stored in:

```text
estateflow_redis_data
```

Container restarts keep data in these volumes. Removing the volumes deletes local
development data.

## Migrations

Use `estateflow_dev` with `estateflow_dev__dev_user` for schema changes. The
runtime containers use the matching `estateflow_dev__app_user` role. For pytest,
load `.env.test.example` (or equivalent secure values) so tests target only
`estateflow_test`; its runtime and migration roles are `estateflow_test__app_user`
and `estateflow_test__dev_user`.

Alembic is not initialized in Sprint 0. When it is added, runtime code should use
`Settings.database_url` (`postgresql+asyncpg`) and Alembic should use
`Settings.sync_database_url` (`postgresql+psycopg2`).

## Repeatable Sprint 0 Smoke

Run the fast local smoke check first. It uses test doubles and never calls
Telegram, OpenRouter, R2, PostgreSQL, or Redis:

```powershell
.venv\Scripts\python.exe scripts\smoke_check.py
```

Then run the full local checks:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src tests
```

For Docker-backed readiness, start the stack and call:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/health/ready
```

Expected result: `status=ok`, with the remote `postgres` and local `redis`
dependencies both `ok`.

## Logs

Development logs are human-readable console logs. Each request log includes:

```text
timestamp, level, service, event, correlation ID
```

View API logs:

```powershell
docker compose logs --tail 50 api
```

Use `x-correlation-id` to trace one request:

```powershell
Invoke-WebRequest -UseBasicParsing `
  -Uri http://localhost:8000/health/live `
  -Headers @{ "x-correlation-id" = "manual-smoke" }
```

## Sprint 1 Handoff

The listener/source/queue contracts for the next sprint are documented in
[`docs/sprint_1_handoff.md`](sprint_1_handoff.md).
