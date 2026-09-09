# Service topology

See [architecture](architecture.md) for worker boundaries and exact queue ownership, and [deployment](deployment.md) for Compose profiles and network requirements.

| Local service | Loopback port | Purpose |
| --- | --- | --- |
| `estateflow-api` | 8000 | Public/search and Mini App API |
| `admin-api` | 8001 | Admin operations |
| `internal-api` | 8002 | Internal callbacks |
| `frontend` | 8080 | Mini App; `/api` proxies to public API |
| `admin-frontend` | 8081 | Admin interface |
| `redis` | 6379 | Coordination and temporary state |
| `rabbitmq` | 5672 / 15672 | AMQP / local management |

These are base development mappings, not production exposure promises. `applications` enables APIs, frontends, bot and notification processes. Ingestion workers run in the default profile; migration is a separate one-shot profile.

Each service has a Dockerfile and requirement manifest under `requirements/`. `scripts/build_service_bundle.py` copies the Python import closure for service images. Split entrypoints live in `services/` and `apps/`; `estateflow-worker` with `WORKER_STAGE=all` is the combined compatibility path.
