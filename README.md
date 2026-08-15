# EstateFlow
An AI-Powered Real Estate Aggregation and Notification Platform

## Local Development

Sprint 0 local Docker and test instructions are in
[`docs/local_development.md`](docs/local_development.md).

Sprint 1 implementation contracts are in
[`docs/sprint_1_handoff.md`](docs/sprint_1_handoff.md).

## Runtime Entry Points

Use the dedicated one-shot migration step before starting the API, bot, or workers:

```powershell
docker compose --profile migrations run --rm migrate
estateflow-public-api
estateflow-admin-api
estateflow-internal-api
estateflow-bot
estateflow-worker  # compatibility mode; use the split Compose workers by default
```

The default Compose topology separates the HTTP surfaces into the public Mini App
API, admin API, and internal API. Background processing is split into listener,
pre-AI, AI, post-AI deduplication, and notification workers. Each queue worker
consumes only its own RabbitMQ stage; `WORKER_STAGE=all` remains available for
local compatibility runs.

PostgreSQL is not provisioned by Docker Compose. All environments use the
password-protected DigitalOcean PostgreSQL service; select the matching database
and role in the secure env file (`estateflow` for production, `estateflow_dev` for
development, and `estateflow_test` for pytest runs).

Production-like Compose validates all mandatory secrets before rendering and does not
load a service-level `.env` file. Supply values through the deployment environment or
an explicit secure environment source:

```powershell
docker compose --env-file <secure-env-file> -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose --env-file <secure-env-file> -f docker-compose.yml -f docker-compose.production.yml --profile migrations run --rm migrate
docker compose --env-file <secure-env-file> -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

### Standalone ingestion deployment

Real announcement collection can run independently while Mini App, admin, bot,
and notification applications are unfinished:

```powershell
Copy-Item .env.ingestion.example .env.ingestion
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml config --quiet
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml up -d --build
```

This stack contains Redis, RabbitMQ, migration/source bootstrap,
listener, pre-AI, AI extraction/media, and final dedup/database persistence. It
publishes no host ports. Read
[`docs/ingestion_vps_deployment.md`](docs/ingestion_vps_deployment.md) before VPS rollout.

The split stack uses RabbitMQ for durable worker events and Redis for cache,
sessions, rate limits, and lightweight coordination. The notification path is
also event-driven:

```text
raw -> pre-ai -> ai -> dedup -> announcement.persisted
                                      -> notification matcher
                                      -> notification.delivery -> Telegram
```

RabbitMQ management is available locally at `http://127.0.0.1:15672`; do not
expose that port publicly.

Each application and worker has its own Dockerfile and requirement manifest.
The image builder copies only the import closure needed by that entrypoint;
the legacy combined worker remains available only for compatibility.
