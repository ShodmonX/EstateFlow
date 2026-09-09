# Standalone ingestion pipeline: VPS deployment

This deployment is intentionally independent from the Telegram bot, Mini App,
public/admin APIs, frontends, and notification delivery. It can collect and
persist real announcements while those applications are still under development.

## Runtime boundary

```text
Telegram adapters
      |
      v
listener -> ingestion.raw_announcements
      |
      v
worker-pre-ai -> ai.processing.raw_announcements
      |
      v
worker-ai -> dedup.post_ai.structured_announcements
      |
      v
worker-dedup -> PostgreSQL announcements
                    |
                    +-> announcement.persisted (durable RabbitMQ queue)
```

The standalone stack contains Redis, RabbitMQ, the one-shot `migrate` and
`bootstrap` jobs, plus `listener`, `worker-pre-ai`, `worker-ai`, and `worker-dedup`.
PostgreSQL is supplied separately, reached through `DB_HOST`/
`DB_PORT`; no PostgreSQL container or host port is created. No HTTP port is
published. Infrastructure is reachable only on the Docker `estateflow-backbone`
network.

The sample targets PostgreSQL on the same VPS host, not a provisioned managed database.
See [deployment](deployment.md) for host networking and external-database limitations.
For host-side `psql` / `pg_dump`, export matching connection variables (use the host-reachable
address, usually `localhost`) and configure a private PostgreSQL password file. Compose
loading an env file does not export it into your shell.

## 1. Prepare the VPS

Clone or copy the repository to a non-root deployment user. From the repository
root:

```bash
cp .env.ingestion.example .env.ingestion
chmod 600 .env.ingestion
mkdir -p data/sessions backups
chmod 700 data/sessions backups
```

Edit `.env.ingestion` and set all required secrets. Use unique high-entropy
passwords. URL-encode RabbitMQ username/password characters in `RABBITMQ_URL`
while keeping their original values in `RABBITMQ_USER` and `RABBITMQ_PASSWORD`.

`INGESTION_SOURCES_JSON` is the operator-controlled source list. Example:

```dotenv
INGESTION_SOURCES_JSON=[{"source_type":"telegram_channel","identifier":"@example_channel","name":"Example channel","adapter_name":"telegram_telethon","source_profile":"default","session_name":"acc_example"}]
```

Bootstrap inserts or re-enables these rows idempotently. It fails if the
database has no active Telegram source or the `listener_sources` flag is off.

## 2. Transfer authorized Telegram sessions

Authorize sessions interactively on a trusted machine, not in an unattended
production container. Copy both independent session files:

```bash
scp data/sessions/acc_example.session deploy@vps:/srv/estateflow/data/sessions/
scp data/sessions/media-example.session deploy@vps:/srv/estateflow/data/sessions/
ssh deploy@vps 'chmod 600 /srv/estateflow/data/sessions/*.session'
```

The listener and media worker must use different files to avoid concurrent
SQLite access. Names must match source `session_name`,
`DEFAULT_TELEGRAM_LISTENER_ACCOUNT_KEY`, and `TELEGRAM_MEDIA_SESSION_NAME`.

## 3. Validate and start

```bash
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml config --quiet
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml build
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml up -d
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml ps -a
```

Expected state:

- `migrate` and `bootstrap`: exited with code `0`;
- Redis, RabbitMQ, listener, and three workers: running/healthy;
- no public host ports.

Listener starts only after migration and bootstrap succeed. Workers start only
after migration and their infrastructure dependencies are healthy.

## 4. Operational verification

```bash
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml logs --tail=200 listener
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml logs --tail=200 worker-ai worker-dedup
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "select count(*) from announcements;"
```

Listener logs must contain `telegram_listener.subscribed`. New input should move
through the stage queues and increase the `announcements` count. Inspect every
`*.dlq` queue if a stage depth stops decreasing.

## 5. Backups

Create a PostgreSQL backup before every update and at least daily:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -Fc > "backups/estateflow-${stamp}.dump"
sha256sum "backups/estateflow-${stamp}.dump" > "backups/estateflow-${stamp}.dump.sha256"
```

Copy backups off the VPS and perform periodic restore drills. Also back up
`data/sessions`. Never publish session files or `.env.ingestion` to Git.

## 6. Safe updates and rollback

```bash
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml build
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml up -d
docker compose --env-file .env.ingestion -f docker-compose.ingestion.yml ps -a
```

RabbitMQ messages survive container recreation through
named volumes; PostgreSQL has its own external persistence. Never run `docker compose down -v`; `-v` deletes the dataset.
Deploy reviewed Git commits or immutable image tags so code rollback does not
require rolling the database backward.

## Local/default Compose profiles

The original `docker-compose.yml` now starts infrastructure and ingestion by
default. User-facing applications require an explicit profile:

```bash
# Ingestion only
docker compose up -d --build

# Explicitly include Mini App/admin/bot services
docker compose --profile applications up -d --build
```

Use `docker-compose.ingestion.yml` on a real VPS. It provides stricter secret
scoping, no host ports, migration/bootstrap gates, and stable resource names.
