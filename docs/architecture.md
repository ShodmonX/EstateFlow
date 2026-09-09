# Architecture

The [README diagram](../README.md#architecture) describes the split Telegram processing path. PostgreSQL is shared durable state, RabbitMQ is the event transport, and Redis supports coordination. Dedicated entrypoints build resources for each stage. `WORKER_STAGE=all` remains a compatibility path.

## Service and queue boundaries

| Producer | Queue | Consumer |
| --- | --- | --- |
| Listener | `ingestion.raw_announcements` | `worker-pre-ai` |
| Source parser / pre-AI worker | `ai.processing.raw_announcements` | `worker-ai` |
| Source parser | `ingestion.source_parser.quarantine` | Operator review/replay; no automatic consumer |
| AI worker | `dedup.post_ai.structured_announcements` | `worker-dedup` |
| Post-AI persistence | `announcement.persisted` | `worker-notification-matcher` |
| Notification matcher | `notification.delivery` | `worker-notification` |

The pre-AI worker owns source parsing as well as deduplication. AI extraction and media handling share a worker; media is not a separate queue stage. Object storage holds media bytes; PostgreSQL holds media keys/URLs and metadata. The listener publishes references, not binary image payloads.

The standalone bootstrap runs after migration and before listener startup. It inserts or enables configured source rows, merges parser bindings, and checks active sources and the listener feature flag. Sources can also be managed through the admin API. Redis signals listener refreshes and stores topology snapshots; source configuration and feature-flag state live in PostgreSQL.

The public API serves search and authenticated Mini App operations. The admin API owns review, sources, parser preview, Telegram authorization, tags, and release controls. The internal API isolates service callbacks. The bot and two web frontends are separate processes. Website adapter/scraper abstractions also exist, but the default queue diagram represents the Telegram deployment path.

## Claim-to-code evidence

| Claim | Primary implementation |
| --- | --- |
| Stage construction and resource cleanup | [worker_stages.py](../src/estateflow/application/worker_stages.py), [runtime.py](../src/estateflow/application/runtime.py) |
| Event names and schemas | [events.py](../src/estateflow/contracts/events.py), [raw contract](raw_ingestion_contract.md), [post-AI contract](post_ai_dedup_contract.md) |
| Bootstrap / source policy persistence | [bootstrap](../services/ingestion_bootstrap/main.py), [source_config.py](../src/estateflow/services/source_config.py), [source models](../src/estateflow/models/listeners.py) |
| Listener sessions, albums, idempotency | [telegram_listener.py](../src/estateflow/services/telegram_listener.py), [listener_refresh.py](../src/estateflow/services/listener_refresh.py) |
| Parser rollout and quarantine | [router.py](../src/estateflow/services/source_parsing/router.py), [ingestion_pipeline.py](../src/estateflow/services/ingestion_pipeline.py) |
| Pre/post-AI decisions | [pre_ai_dedup.py](../src/estateflow/services/pre_ai_dedup.py), [post_ai_dedup.py](../src/estateflow/services/post_ai_dedup.py) |
| Model sequence / quality gates | [ai_client.py](../src/estateflow/services/ai_client.py), [ai_worker.py](../src/estateflow/services/ai_worker.py) |
| Media storage and processing limits | [media_storage.py](../src/estateflow/services/media_storage.py) |
| Durability, retry, DLQ, acknowledgements | [queue.py](../src/estateflow/services/queue.py), [queue_consumer.py](../src/estateflow/services/queue_consumer.py), [stage_consumer.py](../src/estateflow/services/stage_consumer.py) |
| Notification matching / delivery | [matching entrypoint](../services/worker_notification_match/main.py), [delivery entrypoint](../services/worker_notification_delivery/main.py), [notifications.py](../src/estateflow/services/notifications.py) |
| Constraints / indexing | [announcement models](../src/estateflow/models/announcements.py), [Alembic revisions](../alembic/versions/) |
| Optimistic feature-flag updates | [release control repository](../src/estateflow/repositories/release_controls.py) |
| Validation / hardening | [settings](../src/estateflow/application/core/config.py), [production overlay](../docker-compose.production.yml), [ingestion stack](../docker-compose.ingestion.yml) |
| Test layers / CI | [conftest.py](../tests/conftest.py), [CI workflow](../.github/workflows/ci.yml) |

The parser replay dataset is a small synthetic regression gate, not evidence of accuracy on production channels. Neither process health checks nor durable broker messages establish end-to-end delivery guarantees. See [operations](operations.md).
