# Dependency matrix — Task 000

Baseline `main` / `addb856a99d24946ef127384121b99de371c017b`, 2026-11-09. All import/build facts below are VERIFIED statically; successful image builds and deployed contents are NOT VERIFIED. See [table ownership](table-ownership-map.md) and [event inventory](event-inventory.md) for database and broker edges.

## Entrypoints and build contexts

| Dockerfile / context | Declared start | Build evidence |
|---|---|---|
| `Dockerfile` / repository root | `CMD ["uvicorn", "estateflow.main:app", "--host", "0.0.0.0", "--port", "8000"]` | `Dockerfile:25` |
| `Dockerfile.admin-api` / repository root | `CMD ["python", "-m", "apps.admin_api.main"]` | `Dockerfile.admin-api:30` |
| `Dockerfile.bot` / repository root | `CMD ["python", "-m", "apps.bot.main"]` | `Dockerfile.bot:30` |
| `Dockerfile.ingestion-bootstrap` / repository root | `CMD ["python", "-m", "services.ingestion_bootstrap.main"]` | `Dockerfile.ingestion-bootstrap:22` |
| `Dockerfile.ingestion-migrate` / repository root | `CMD ["estateflow-migrations", "upgrade-head"]` | `Dockerfile.ingestion-migrate:21` |
| `Dockerfile.internal-api` / repository root | `CMD ["python", "-m", "apps.internal_api.main"]` | `Dockerfile.internal-api:30` |
| `Dockerfile.listener` / repository root | `CMD ["python", "-m", "services.listener.main"]` | `Dockerfile.listener:26` |
| `Dockerfile.public-api` / repository root | `CMD ["python", "-m", "apps.public_api.main"]` | `Dockerfile.public-api:30` |
| `Dockerfile.worker-ai` / repository root | `CMD ["python", "-m", "services.worker_ai.main"]` | `Dockerfile.worker-ai:24` |
| `Dockerfile.worker-dedup` / repository root | `CMD ["python", "-m", "services.worker_dedup.main"]` | `Dockerfile.worker-dedup:24` |
| `Dockerfile.worker-notification-delivery` / repository root | `CMD ["python", "-m", "services.worker_notification_delivery.main"]` | `Dockerfile.worker-notification-delivery:25` |
| `Dockerfile.worker-notification-match` / repository root | `CMD ["python", "-m", "services.worker_notification_match.main"]` | `Dockerfile.worker-notification-match:25` |
| `Dockerfile.worker-pre-ai` / repository root | `CMD ["python", "-m", "services.worker_pre_ai.main"]` | `Dockerfile.worker-pre-ai:24` |
| `frontend/Dockerfile` / `frontend` | nginx inherited image CMD, static files | `frontend/Dockerfile:1` |
| `admin-frontend/Dockerfile` / `admin-frontend` | nginx inherited image CMD, static files | `admin-frontend/Dockerfile:1` |

Compose mapping: base root builds and commands `docker-compose.yml:62–301`; ingestion-only root builds `docker-compose.ingestion.yml:107–260`; VPS apps/frontends/matcher/delivery `docker-compose.vps-apps.yml:89–203`; production hardening overlay `docker-compose.production.yml:26`. Existing CLI aliases are `pyproject.toml:57` onward. `estateflow-worker` reaches `src/estateflow/workers/serve.py`; compatibility stage all reaches `src/estateflow/application/worker_stages.py:38`. These alternatives must not be run alongside split stages without an explicit consumption/session plan.

## Construction and coupling

| Process | Construction / business imports | DB and external dependencies | Extraction consequence / evidence |
|---|---|---|---|
| Listener | AdapterRegistry, source config repository, release controls, TelegramListenerCoordinator | PostgreSQL source/flag state; Redis refresh/seen/topology; Telethon session files; RabbitMQ | Transport includes parser binding snapshots; live pool uses memory. `services/listener/main.py:31–55`; `src/estateflow/services/listener_refresh.py:124` |
| Pre-AI | PreAiIngestionProcessor imports source parser, raw listener domain and dedup signals | RabbitMQ; memory signal store; shared resources still construct DB/Redis | Parser and dedup are one process; default split metrics recorder absent. `services/worker_pre_ai/main.py:25–40`; `src/estateflow/services/ingestion_pipeline.py:6–27` |
| AI/media | AiExtractionWorker, LLM client, media service/resolver/storage, memory run repository | OpenRouter, R2, Telegram media session, Redis, RabbitMQ | Separate media would require readiness orchestration and restricted transport access. `services/worker_ai/main.py:31–53`; `src/estateflow/services/media_storage.py:164–200` |
| Dedup/catalog | WeightedDeduplicationEngine, ManualReviewQueueService, LazyAsyncpgParentChildAnnouncementRepository | Shared PostgreSQL source/listing/media/review tables, queue; metrics via separate SQLAlchemy session | Atomic catalog owner required; source insert must move to contract/projection. `services/worker_dedup/main.py:27–44`; `src/estateflow/repositories/post_ai_dedup.py:930` |
| Matcher | NotificationMatchingEngine + SQLAlchemy filter/user/job repositories; imports catalog StructuredAnnouncement inside handler | PostgreSQL users/filters/notifications; RabbitMQ | Identity/filter projections required; committed job then publish gap. `services/worker_notification_match/main.py:47–58` |
| Delivery | NotificationDeliveryWorker + SQLAlchemy repository + aiogram | PostgreSQL notifications/filters/listings/attempts; Telegram Bot API; analytics | Shared context reads and no due dispatcher. `services/worker_notification_delivery/main.py:31–51`; `src/estateflow/repositories/sqlalchemy.py:1169` |
| Public/admin/internal APIs | All call create_app and build_runtime; profile gates routers after full runtime construction | PostgreSQL, Redis, queue, adapters; domain services for identity/referrals/filters/review/content/tags | Separate HTTP profiles do not isolate construction, schemas or business code. `src/estateflow/api/app.py:38–67`; `src/estateflow/application/runtime.py:179–220` |
| Bot | build_runtime(role=bot), dispatcher/controller/search/saved filters/referrals | Same SQL repositories and Redis; Telegram polling | API/bot should be adapters to explicit domain owners, not database owners. `src/estateflow/bot/serve.py:35–40` |
| Ingestion bootstrap | Direct ORM listener/source/feature flag and parser validation imports | PostgreSQL; configured source JSON | Administrative writer with multi-domain settings. `services/ingestion_bootstrap/main.py:17–29`, :123 |
| Migration CLI | Global Alembic metadata + schema bridge helpers | Privileged migration PostgreSQL identity | One chain currently owns all tables; per-service history is a future change. `alembic/env.py:8–26`, :84–98 |
| Website/content paths | WebsiteScraperService and ContentAutomationService built in shared runtime | HTTP adapters, no scrape repository by default; shared catalog SQL; Telegram publishing | Define owners and scheduler contracts before extraction. `src/estateflow/application/runtime.py:217`, :260; `src/estateflow/services/website_scraper.py:149–179` |

## Direct imports from deployed entrypoints

This index records exact imports, including imports nested in handlers. It is evidence of code coupling, not proof every imported class is exercised. Transitive domain edges are described above.

### apps/admin_api/main.py
- `apps/admin_api/main.py:5` → `estateflow.api.admin_app` (app).
- `apps/admin_api/main.py:6` → `estateflow.application.core.config` (get_settings).

### apps/bot/main.py
- `apps/bot/main.py:1` → `estateflow.bot.serve` (main).

### apps/internal_api/main.py
- `apps/internal_api/main.py:5` → `estateflow.api.internal_app` (app).
- `apps/internal_api/main.py:6` → `estateflow.application.core.config` (get_settings).

### apps/listener/main.py
- `apps/listener/main.py:8` → `estateflow.workers.serve` (main).

### apps/public_api/main.py
- `apps/public_api/main.py:5` → `estateflow.api.public` (app).
- `apps/public_api/main.py:6` → `estateflow.application.core.config` (get_settings).

### apps/worker_ai/main.py
- `apps/worker_ai/main.py:8` → `estateflow.workers.serve` (main).

### apps/worker_dedup/main.py
- `apps/worker_dedup/main.py:8` → `estateflow.workers.serve` (main).

### apps/worker_notification/main.py
- `apps/worker_notification/main.py:8` → `estateflow.workers.serve` (main).

### apps/worker_pre_ai/main.py
- `apps/worker_pre_ai/main.py:8` → `estateflow.workers.serve` (main).

### services/ingestion_bootstrap/main.py
- `services/ingestion_bootstrap/main.py:14` → `estateflow.application.core.config` (Settings, get_settings).
- `services/ingestion_bootstrap/main.py:15` → `estateflow.application.core.logging` (configure_logging).
- `services/ingestion_bootstrap/main.py:16` → `estateflow.db.session` (create_session_manager).
- `services/ingestion_bootstrap/main.py:17` → `estateflow.models.listeners` (IngestionSourceRecord, ListenerAccountRecord).
- `services/ingestion_bootstrap/main.py:18` → `estateflow.models.release_controls` (FeatureFlagStateRecord).
- `services/ingestion_bootstrap/main.py:19` → `estateflow.services.source_config` (ParserMode, SourceConfig, SourceType, merge_source_parser_binding, normalize_parser_key, normalize_parser_version, validate_parser_config, validate_source_config_parser_binding).
- `services/ingestion_bootstrap/main.py:29` → `estateflow.services.source_parsing` (validate_source_parser_binding).

### services/listener/main.py
- `services/listener/main.py:6` → `estateflow.adapters.registry` (AdapterRegistry).
- `services/listener/main.py:7` → `estateflow.application.core.config` (Settings, get_settings).
- `services/listener/main.py:8` → `estateflow.application.core.logging` (configure_logging).
- `services/listener/main.py:9` → `estateflow.repositories.release_controls` (SQLAlchemyFeatureFlagStore).
- `services/listener/main.py:10` → `estateflow.repositories.sqlalchemy` (SQLAlchemySourceConfigRepository).
- `services/listener/main.py:11` → `estateflow.services.adapter_defaults` (DEFAULT_TELEGRAM_ADAPTER_NAME).
- `services/listener/main.py:12` → `estateflow.services.listener_refresh` (RedisListenerRefreshStore, TelegramListenerCoordinator).
- `services/listener/main.py:16` → `estateflow.services.listener_status` (RedisListenerTopologyStore).
- `services/listener/main.py:17` → `estateflow.services.release_controls` (FeatureGatedSourceProvider, ReleaseControlService, beta_policy_from_settings, feature_flags_from_settings).
- `services/listener/main.py:23` → `services.common.runtime` (create_ops_notifier, create_resources).

### services/worker_ai/main.py
- `services/worker_ai/main.py:6` → `estateflow.application.core.config` (get_settings).
- `services/worker_ai/main.py:7` → `estateflow.application.core.logging` (configure_logging).
- `services/worker_ai/main.py:8` → `estateflow.contracts.events` (AI_PROCESSING_QUEUE, QueueMessage).
- `services/worker_ai/main.py:9` → `estateflow.services.ai_client` (create_openrouter_llm_client).
- `services/worker_ai/main.py:10` → `estateflow.services.ai_worker` (AiExtractionWorker, InMemoryAiProcessingRepository).
- `services/worker_ai/main.py:11` → `estateflow.services.media_storage` (InMemoryObjectStorage, MediaProcessor, MediaStorageService, ObjectStorage, SmartMediaResolver, create_r2_object_storage, media_processing_config_from_settings).
- `services/worker_ai/main.py:20` → `estateflow.services.stage_consumer` (RabbitStageConsumer).
- `services/worker_ai/main.py:21` → `services.common.runtime` (create_ops_notifier, create_resources).

### services/worker_dedup/main.py
- `services/worker_dedup/main.py:7` → `estateflow.application.core.config` (get_settings).
- `services/worker_dedup/main.py:8` → `estateflow.application.core.logging` (configure_logging).
- `services/worker_dedup/main.py:9` → `estateflow.contracts.events` (POST_AI_DEDUP_QUEUE, QueueMessage).
- `services/worker_dedup/main.py:10` → `estateflow.repositories.post_ai_dedup` (LazyAsyncpgParentChildAnnouncementRepository).
- `services/worker_dedup/main.py:11` → `estateflow.services.post_ai_dedup` (InMemoryAnnouncementRepository, ManualReviewQueueService, PostAiDedupProcessor, WeightedDeduplicationEngine, dedup_scoring_config_from_settings).
- `services/worker_dedup/main.py:18` → `estateflow.services.stage_consumer` (RabbitStageConsumer).
- `services/worker_dedup/main.py:19` → `services.common.runtime` (create_recorders, create_resources).
- `services/worker_dedup/main.py:57` → `estateflow.services.post_ai_dedup` (StructuredAnnouncement).

### services/worker_notification_delivery/main.py
- `services/worker_notification_delivery/main.py:7` → `estateflow.application.core.config` (get_settings).
- `services/worker_notification_delivery/main.py:8` → `estateflow.application.core.logging` (configure_logging).
- `services/worker_notification_delivery/main.py:9` → `estateflow.contracts.events` (NOTIFICATION_DELIVERY_QUEUE, QueueMessage).
- `services/worker_notification_delivery/main.py:10` → `estateflow.repositories.sqlalchemy` (SQLAlchemyAnalyticsEventRepository, SQLAlchemyNotificationDeliveryRepository, SQLAlchemyTechnicalMetricRepository).
- `services/worker_notification_delivery/main.py:15` → `estateflow.services.analytics` (AnalyticsRecorder, TechnicalMetricRecorder).
- `services/worker_notification_delivery/main.py:16` → `estateflow.services.notifications` (NotificationDeliveryWorker).
- `services/worker_notification_delivery/main.py:17` → `estateflow.services.stage_consumer` (RabbitStageConsumer).
- `services/worker_notification_delivery/main.py:18` → `estateflow.services.telegram_notifications` (AiogramTelegramNotificationClient).
- `services/worker_notification_delivery/main.py:19` → `services.common.notification_payload` (notification_job_from_payload).
- `services/worker_notification_delivery/main.py:20` → `services.common.runtime` (create_ops_notifier, create_resources).

### services/worker_notification_match/main.py
- `services/worker_notification_match/main.py:5` → `estateflow.application.core.config` (get_settings).
- `services/worker_notification_match/main.py:6` → `estateflow.application.core.logging` (configure_logging).
- `services/worker_notification_match/main.py:7` → `estateflow.contracts.events` (ANNOUNCEMENT_PERSISTED_QUEUE, NOTIFICATION_DELIVERY_QUEUE, QueueMessage).
- `services/worker_notification_match/main.py:12` → `estateflow.repositories.sqlalchemy` (SQLAlchemyNotificationJobRepository, SQLAlchemyNotificationSavedFilterRepository, SQLAlchemyNotificationUserRepository).
- `services/worker_notification_match/main.py:17` → `estateflow.services.notifications` (NotificationJob, NotificationMatchingEngine).
- `services/worker_notification_match/main.py:18` → `estateflow.services.queue` (PublishingQueue).
- `services/worker_notification_match/main.py:19` → `estateflow.services.stage_consumer` (RabbitStageConsumer).
- `services/worker_notification_match/main.py:20` → `services.common.notification_payload` (notification_job_to_payload).
- `services/worker_notification_match/main.py:21` → `services.common.runtime` (create_ops_notifier, create_resources).
- `services/worker_notification_match/main.py:56` → `estateflow.services.post_ai_dedup` (StructuredAnnouncement).

### services/worker_pre_ai/main.py
- `services/worker_pre_ai/main.py:6` → `estateflow.application.core.config` (get_settings).
- `services/worker_pre_ai/main.py:7` → `estateflow.application.core.logging` (configure_logging).
- `services/worker_pre_ai/main.py:8` → `estateflow.contracts.events` (RAW_ANNOUNCEMENT_QUEUE, QueueMessage).
- `services/worker_pre_ai/main.py:9` → `estateflow.services.ingestion_pipeline` (PreAiIngestionProcessor).
- `services/worker_pre_ai/main.py:10` → `estateflow.services.pre_ai_dedup` (InMemoryPreAiDedupSignalStore, PreAiDedupConfig, PreAiDedupFilter, StableMediaReferencePHashProvider).
- `services/worker_pre_ai/main.py:16` → `estateflow.services.stage_consumer` (RabbitStageConsumer).
- `services/worker_pre_ai/main.py:17` → `services.common.runtime` (create_ops_notifier, create_resources).

## Build/readiness and isolation findings

VERIFIED: Dockerfiles use a static AST import closure builder (`scripts/build_service_bundle.py:23–36`, :52–68) plus manually copied extras. This packages shared source transitively; it does not enforce bounded contexts. Dynamic plugin imports require explicit extras (`Dockerfile.listener:11`). Python runtimes use non-root users (`Dockerfile.listener:25`, `Dockerfile.worker-ai:23`); ingestion/VPS hardening adds read-only filesystem/capability drops (`docker-compose.ingestion.yml:35–40`, `docker-compose.vps-apps.yml:53–58`). Session volumes remain mutable and shared by listener/AI (`docker-compose.ingestion.yml:183`, :245). Worker health checks test PID 1 existence (:49; VPS :79), so blocked processing can remain healthy. API profile isolation is verified at router registration only. No images were built and no processes were started.
