# Local development

## Prerequisites and deterministic checks

Use Python 3.12+ and, for infrastructure, Docker Desktop with Docker Compose. From the repository root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe scripts\smoke_check.py
.venv\Scripts\python.exe -m pytest -ra
.venv\Scripts\python.exe scripts\evaluate_source_parser.py
```

These checks use test doubles; Docker-backed tests can run when a daemon is available. See [testing](testing.md) for skip conditions and broker opt-in.

## Environment and database

Create `.env` only if you do not already have one:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set a local `DB_PASSWORD`. The helper reads `DB_USER`, `DB_PASSWORD`, and `DB_NAME` from `.env`, starts or reuses `estateflow-postgres16-local` (`postgres:16`), and binds it to `127.0.0.1:55436`. Its independent volume is `estateflow-postgres16-local-data`; changing env credentials does not reinitialize an existing volume.

```powershell
.\scripts\start_local_postgres.ps1
docker compose up -d redis rabbitmq
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current
```

PostgreSQL is not a Compose service. Host Python/Alembic uses `localhost:55436`; Compose services override database host/port with `DOCKER_DB_HOST` / `DOCKER_DB_PORT` (default `host.docker.internal:55436`). Linux users need explicit host-gateway access; see [deployment](deployment.md).

The example RabbitMQ password is an explicit development-only default matching the base Compose broker. Set `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, and the URL consistently when changing it. Redis/RabbitMQ use Compose hostnames inside containers; host-side service processes require `localhost` instead.

## Start an API or the live stack

For API development after migration:

```powershell
docker compose --profile applications up -d --build estateflow-api
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Readiness checks the configured database, Redis, and event queue. Add `frontend`, `admin-api`, `admin-frontend`, or `internal-api` explicitly as needed; the full applications profile also starts the bot and notification delivery and requires their credentials.

Live ingestion needs Telegram API credentials, an authorized listener session, a distinct media session, enabled sources, OpenRouter and object-storage configuration. Populate source `session_name` explicitly to match your configured account. Existing code retains legacy fallback session names for compatibility; changing an example does not rename sessions or DB bindings. Source flags are persisted, so changing an env default does not necessarily override existing release state. Follow [ingestion deployment](ingestion_vps_deployment.md) for bootstrap and startup order.

## Validation and logs

Use quiet rendering with a real env file to avoid printing credentials:

```powershell
docker compose --profile applications config --quiet
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src
docker compose logs --tail 50 estateflow-api
```

Attach `x-correlation-id` to HTTP requests when tracing a flow. Keep logs and replay data private. Container restarts retain the separate database, broker and cache volumes; deleting volumes destroys their data.
