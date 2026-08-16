# EstateFlow Service Topology

EstateFlow is deployed as separate process and image boundaries while sharing
PostgreSQL, RabbitMQ, Redis, and versioned event contracts. Each service has a
dedicated entrypoint, Dockerfile, and dependency manifest. The image build
copies only the Python import closure needed by that entrypoint instead of
shipping the complete source tree to every worker.

## Applications

| Service | Port | Responsibility |
| --- | ---: | --- |
| `estateflow-api` | 8000 | Public search and Telegram Mini App API |
| `admin-api` | 8001 | Admin review, source, feature flag, and operations API |
| `internal-api` | 8002 | Internal callbacks and service-to-service endpoints |
| `frontend` | 8080 | Static Mini App; `/api` proxies to the public API |
| `bot` | - | Telegram user bot and referral/channel suggestion flows |

The three HTTP applications use shared domain and persistence packages, but each
deployment entrypoint creates only its own router surface:

- `apps.public_api.main`
- `apps.admin_api.main`
- `apps.internal_api.main`

## Workers

| Service | `WORKER_STAGE` | Responsibility |
| --- | --- | --- |
| `listener` | `listener` | Telegram channel ingestion and raw event publication |
| `worker-pre-ai` | `pre_ai` | Cheap pre-AI duplicate filtering |
| `worker-ai` | `ai` | LLM extraction and media processing |
| `worker-dedup` | `dedup` | Post-AI duplicate merge and announcement persistence |
| `worker-notification-matcher` | `notification` | Match persisted announcements to user preferences |
| `worker-notification` | `notification` | Deliver matched Telegram notifications |

RabbitMQ queue ownership is stage-specific. A worker never consumes another
stage's queue. Messages are persistent, acknowledged only after successful
processing, retried up to `RABBITMQ_MAX_RETRIES`, and then routed to a durable
dead-letter queue.

## Event Flow

The production split flow is:

```text
ingestion.raw_announcements
  -> ai.processing.raw_announcements
  -> dedup.post_ai.structured_announcements
  -> announcement.persisted
  -> notification.delivery
```

The notification matcher consumes `announcement.persisted`, evaluates active
notification preferences, and publishes one delivery job per matching user.
The delivery worker owns Telegram API calls and delivery attempt state. The
database remains the source of truth for announcements, preferences, and
delivery records; RabbitMQ provides the durable handoff between stages.

## Image Layout

The service-specific build files are:

- `Dockerfile.listener`
- `Dockerfile.worker-pre-ai`
- `Dockerfile.worker-ai`
- `Dockerfile.worker-dedup`
- `Dockerfile.worker-notification-match`
- `Dockerfile.worker-notification-delivery`
- `Dockerfile.public-api`
- `Dockerfile.admin-api`
- `Dockerfile.internal-api`
- `Dockerfile.bot`

The import-closure builder is `scripts/build_service_bundle.py`. Requirement
manifests live under `requirements/` and are intentionally different for
listener, worker, API, and bot images.

## Local operations

```powershell
docker compose --profile migrations run --rm migrate
docker compose up -d --build
docker compose ps
```

The default Compose lifecycle contains only infrastructure and the ingestion
path (`listener`, `worker-pre-ai`, `worker-ai`, `worker-dedup`). Public/admin
applications, frontends, the bot, and notification workers require the explicit
`applications` profile. This keeps unfinished user-facing services outside the
production data-collection lifecycle.

Standalone VPS ingestion has its own deployment boundary:

```powershell
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml up -d --build
```

See `docs/ingestion_vps_deployment.md` for source bootstrap, Telegram sessions,
backup, verification, and update procedures.

Production-like local run (secrets must come from the deployment environment):

```powershell
docker compose -f docker-compose.yml -f docker-compose.production.yml config
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

To scale a stateless stage, run multiple replicas only after confirming the
stage's repository operations are idempotent:

```powershell
docker compose up -d --scale worker-ai=2 --scale worker-notification=2
```

The old combined consumer is still available by running the worker command with
`WORKER_STAGE=all` outside Compose. It is not part of the default split topology.

## Production checklist

- Set `ENVIRONMENT=production` and use a secrets manager for all credentials.
- Use a passworded PostgreSQL instance; never use `POSTGRES_HOST_AUTH_METHOD=trust`.
- Use a managed RabbitMQ cluster or quorum queues instead of one broker node.
- Keep RabbitMQ management, PostgreSQL, Redis, and admin/internal APIs private.
- Run migrations as a one-shot release step before application rollout.
- Configure backups for PostgreSQL, RabbitMQ durable data, and Telegram sessions.
- Monitor queue depth, DLQ depth, worker restart count, and `/health/ready`.
