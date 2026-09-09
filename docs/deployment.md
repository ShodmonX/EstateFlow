# Deployment

## Supported topology

| Configuration | Supplied services | PostgreSQL and network assumptions |
| --- | --- | --- |
| `docker-compose.yml` | Redis, RabbitMQ, listener, pre-AI, AI, dedup; optional `applications` and `migrations` profiles | Standalone local PostgreSQL via `DOCKER_DB_HOST` / `DOCKER_DB_PORT`, default host gateway and port 55436 |
| Base + `docker-compose.production.yml` | Hardened overlay of base services | Operator-supplied `DB_HOST` / `DB_PORT`; explicit migration release step |
| `docker-compose.ingestion.yml` | Broker/cache, migration/bootstrap, four ingestion processes | Existing PostgreSQL; sample uses same-VPS host gateway, port 5432; creates `estateflow-backbone` |
| `docker-compose.vps-apps.yml` | APIs, frontends, bot, notification workers, migration job | Existing database, broker/cache, external backbone and external `web` network |

None of these Compose files provisions PostgreSQL. The local PowerShell helper starts a separate password-authenticated PostgreSQL 16 container with its own volume. The VPS ingestion example assumes PostgreSQL on the host; it does not establish that the database is a managed DigitalOcean product.

A reachable external PostgreSQL host can be selected through configuration. However, current settings construct driver URLs without explicit TLS/CA options, and templates do not mount database certificates. A managed service requiring verified TLS is therefore not a turnkey supported deployment mode; validate both async runtime and synchronous migration connections before adopting one. No provider-specific provisioning is included.

## Configuration validation

Use a private environment file with required values. `config --quiet` checks interpolation and topology without printing credentials:

```powershell
docker compose --env-file <secure-env-file> -f docker-compose.yml -f docker-compose.production.yml --profile applications config --quiet
docker compose --env-file <secure-env-file> -f docker-compose.ingestion.yml config --quiet
docker compose --env-file <secure-env-file> -f docker-compose.vps-apps.yml config --quiet
```

The production file is an overlay, not a standalone stack. Use Compose supporting its `!reset` tags. Render validation does not build images, verify credentials, connect to dependencies, or establish network reachability.

## Startup

For the full production overlay, run migration before applications:

```powershell
docker compose --env-file <secure-env-file> -f docker-compose.yml -f docker-compose.production.yml --profile migrations run --rm migrate
docker compose --env-file <secure-env-file> -f docker-compose.yml -f docker-compose.production.yml --profile applications up -d --build
```

For ingestion-only startup, follow [the VPS runbook](ingestion_vps_deployment.md). Migration and bootstrap completion gate the listener; bootstrap needs configured or existing enabled sources. Supply an explicit source `session_name` and matching listener account setting. Preserve names and volumes on an existing installation.

The separate VPS application stack assumes ingestion infrastructure is running. Its migration job is not an automatic dependency gate for API startup: run it to completion first. Its `web` network and TLS proxy are externally managed. Adapt the generic Nginx templates in `deploy/nginx/` to your DNS and certificate paths before installing them.

## Exposure and persistence

Development service ports bind to loopback. The production overlay removes broker/cache/admin/internal bindings; standalone ingestion publishes none. VPS applications use networks rather than host ports. PostgreSQL on a Linux host needs a suitable listen address, a narrowly scoped `pg_hba.conf` rule for the Docker subnet, and firewall rules. The base local stack assumes Docker Desktop host access; Linux users must explicitly arrange host-gateway reachability.

Named Redis/RabbitMQ volumes retain broker/cache data. PostgreSQL persistence is external to Compose. Telegram session bind mounts must be writable by the image's application user, while the rest of the hardened application filesystem is read-only. Use a separate authorized session for media downloads to avoid SQLite contention with a live listener.

Production settings validate presence, not the quality of secrets. Configure unique credentials, private infrastructure access, log rotation, backups and tested restores. Retain queues during updates; `down -v` destroys named volume data. See [operations](operations.md).
