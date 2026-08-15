from __future__ import annotations

from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from estateflow.application.core.config import Settings
from estateflow.application.queue_factory import create_event_queue
from estateflow.db.session import DatabaseSessionManager, create_session_manager
from estateflow.services.analytics import AnalyticsRecorder, TechnicalMetricRecorder
from estateflow.services.ops_notifications import (
    DisabledOpsNotificationService,
    OpsNotificationService,
    TelegramOpsNotificationService,
)


async def run_worker_stage(settings: Settings) -> None:
    """Run one deployable worker without constructing the API runtime graph."""
    if settings.worker_stage == "all":
        await _run_combined_compatibility_worker(settings)
        return
    db = create_session_manager(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    event_queue = create_event_queue(settings, redis)
    try:
        if settings.worker_stage == "listener":
            await _run_listener(settings, db, redis, event_queue)
        else:
            await _run_queue_stage(settings, db, redis, event_queue)
    finally:
        await event_queue.aclose()
        await redis.aclose()
        await db.dispose()


async def _run_combined_compatibility_worker(settings: Settings) -> None:
    """Keep the legacy single-process worker available for local development."""
    from estateflow.application.runtime import build_runtime

    runtime = build_runtime(settings, role="worker")
    try:
        await runtime.queue_consumer.run_forever()
    finally:
        await runtime.aclose()


async def _run_listener(
    settings: Settings,
    db: DatabaseSessionManager,
    redis: Redis,
    event_queue: Any,
) -> None:
    from estateflow.adapters.registry import AdapterRegistry
    from estateflow.repositories.release_controls import SQLAlchemyFeatureFlagStore
    from estateflow.repositories.sqlalchemy import SQLAlchemySourceConfigRepository
    from estateflow.services.adapter_defaults import DEFAULT_TELEGRAM_ADAPTER_NAME
    from estateflow.services.listener_refresh import (
        RedisListenerRefreshStore,
        TelegramListenerCoordinator,
    )
    from estateflow.services.listener_status import RedisListenerTopologyStore
    from estateflow.services.release_controls import (
        FeatureGatedSourceProvider,
        ReleaseControlService,
        beta_policy_from_settings,
        feature_flags_from_settings,
    )

    adapter_registry = AdapterRegistry(_adapter_plugin_dir(settings), settings)
    adapter_registry.reload_sync()
    source_registry = SQLAlchemySourceConfigRepository(db.session)
    release_controls = ReleaseControlService(
        store=SQLAlchemyFeatureFlagStore(
            db.session,
            initial=feature_flags_from_settings(settings),
        ),
        cohort_policy=beta_policy_from_settings(settings),
    )
    topology_store = RedisListenerTopologyStore(redis)
    coordinator = TelegramListenerCoordinator(
        settings=settings,
        adapter_registry=adapter_registry,
        source_provider=FeatureGatedSourceProvider(
            source_registry,
            release_controls=release_controls,
        ),
        refresh_store=RedisListenerRefreshStore(redis),
        redis=redis,
        ops_notifier=_ops_notifier(settings),
        event_queue=event_queue,
        topology_store=topology_store,
        listener_account_key=settings.default_telegram_listener_account_key,
        default_adapter_name=DEFAULT_TELEGRAM_ADAPTER_NAME,
    )
    try:
        await coordinator.run_forever()
    finally:
        await coordinator.aclose()


async def _run_queue_stage(
    settings: Settings,
    db: DatabaseSessionManager,
    redis: Redis,
    event_queue: Any,
) -> None:
    from estateflow.services.queue_consumer import IngestionQueueConsumerWorker

    stage = settings.worker_stage
    ops_notifier = _ops_notifier(settings)
    analytics_recorder, technical_recorder = _recorders(db)
    pre_ai_processor = None
    ai_worker = None
    post_ai_processor = None
    notification_worker = None
    bot = None

    if stage == "pre_ai":
        pre_ai_processor = _build_pre_ai_processor(settings, event_queue, ops_notifier)
    elif stage == "ai":
        ai_worker = _build_ai_worker(settings, event_queue, ops_notifier)
    elif stage == "dedup":
        post_ai_processor = _build_post_ai_processor(
            settings,
            db,
            technical_recorder,
            event_queue,
        )
    elif stage == "notification":
        bot, notification_worker = _build_notification_worker(
            settings,
            db,
            analytics_recorder,
            technical_recorder,
            ops_notifier,
        )
    else:
        raise ValueError(f"Unsupported queue worker stage: {stage}")

    consumer = IngestionQueueConsumerWorker(
        settings=settings,
        redis_event_queue=event_queue,
        pre_ai_processor=pre_ai_processor,
        ai_worker=ai_worker,
        post_ai_processor=post_ai_processor,
        notification_worker=notification_worker,
        stage=stage,
    )
    try:
        await consumer.run_forever()
    finally:
        consumer.stop()
        if bot is not None:
            await bot.close()


def _build_pre_ai_processor(settings: Settings, event_queue: Any, ops_notifier: Any) -> Any:
    from estateflow.services.ingestion_pipeline import PreAiIngestionProcessor
    from estateflow.services.pre_ai_dedup import (
        InMemoryPreAiDedupSignalStore,
        PreAiDedupConfig,
        PreAiDedupFilter,
        StableMediaReferencePHashProvider,
    )

    signal_store = InMemoryPreAiDedupSignalStore()
    phash_provider = StableMediaReferencePHashProvider()
    return PreAiIngestionProcessor(
        dedup_filter=PreAiDedupFilter(
            signal_store=signal_store,
            phash_provider=phash_provider,
            ops_notifier=ops_notifier,
            config=PreAiDedupConfig(
                near_text_threshold=settings.pre_ai_near_text_threshold,
                phash_hamming_threshold=settings.media_phash_hamming_threshold,
            ),
        ),
        ai_queue=event_queue,
        signal_store=signal_store,
        phash_provider=phash_provider,
    )


def _build_ai_worker(settings: Settings, event_queue: Any, ops_notifier: Any) -> Any:
    from estateflow.services.ai_client import create_openrouter_llm_client
    from estateflow.services.ai_worker import AiExtractionWorker, InMemoryAiProcessingRepository
    from estateflow.services.media_storage import (
        InMemoryObjectStorage,
        MediaProcessor,
        MediaStorageService,
        ObjectStorage,
        SmartMediaResolver,
        StorageUploadError,
        create_r2_object_storage,
        media_processing_config_from_settings,
    )

    if settings.openrouter_api_key is None:
        raise RuntimeError("AI worker requires OPENROUTER_API_KEY")
    llm_client = create_openrouter_llm_client(settings=settings, ops_notifier=ops_notifier)
    storage: ObjectStorage
    try:
        storage = create_r2_object_storage(settings)
    except (StorageUploadError, ModuleNotFoundError, ImportError):
        storage = InMemoryObjectStorage()
    return AiExtractionWorker(
        llm_client=llm_client,
        media_service=MediaStorageService(
            resolver=SmartMediaResolver(settings=settings),
            processor=MediaProcessor(media_processing_config_from_settings(settings)),
            storage=storage,
        ),
        repository=InMemoryAiProcessingRepository(),
        post_ai_queue=event_queue,
        ops_notifier=ops_notifier,
        prompt_version=settings.ai_prompt_version,
        min_confidence=settings.ai_min_confidence,
        rental_min_confidence=settings.rental_min_confidence,
        ai_vision_enabled=settings.feature_ai_vision_enabled,
    )


def _build_post_ai_processor(
    settings: Settings,
    db: DatabaseSessionManager,
    technical_recorder: TechnicalMetricRecorder | None,
    event_queue: Any,
) -> Any:
    from estateflow.repositories.post_ai_dedup import LazyAsyncpgParentChildAnnouncementRepository
    from estateflow.services.post_ai_dedup import (
        InMemoryAnnouncementRepository,
        ManualReviewQueueService,
        PostAiDedupProcessor,
        WeightedDeduplicationEngine,
        dedup_scoring_config_from_settings,
    )

    if settings.environment == "test":
        repository: Any = InMemoryAnnouncementRepository(
            config=dedup_scoring_config_from_settings(settings)
        )
    else:
        repository = LazyAsyncpgParentChildAnnouncementRepository(
            settings.database_url,
            config=dedup_scoring_config_from_settings(settings),
        )
    return PostAiDedupProcessor(
        engine=WeightedDeduplicationEngine(
            candidate_repository=repository,
            config=dedup_scoring_config_from_settings(settings),
        ),
        announcement_repository=repository,
        manual_review_queue=ManualReviewQueueService(repository),
        technical_recorder=technical_recorder,
        event_queue=event_queue,
    )


def _build_notification_worker(
    settings: Settings,
    db: DatabaseSessionManager,
    analytics_recorder: AnalyticsRecorder | None,
    technical_recorder: TechnicalMetricRecorder | None,
    ops_notifier: OpsNotificationService,
) -> tuple[Any, Any]:
    from aiogram import Bot

    from estateflow.repositories.sqlalchemy import SQLAlchemyNotificationDeliveryRepository
    from estateflow.services.notifications import NotificationDeliveryWorker
    from estateflow.services.telegram_notifications import AiogramTelegramNotificationClient

    if settings.telegram_bot_token is None:
        raise RuntimeError("Notification worker requires TELEGRAM_BOT_TOKEN")
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    worker = NotificationDeliveryWorker(
        repository=SQLAlchemyNotificationDeliveryRepository(db.session),
        telegram=AiogramTelegramNotificationClient(bot),
        analytics_recorder=analytics_recorder,
        technical_recorder=technical_recorder,
        ops_notifier=ops_notifier,
    )
    return bot, worker


def _recorders(
    db: DatabaseSessionManager,
) -> tuple[AnalyticsRecorder | None, TechnicalMetricRecorder | None]:
    from estateflow.repositories.sqlalchemy import (
        SQLAlchemyAnalyticsEventRepository,
        SQLAlchemyTechnicalMetricRepository,
    )

    return (
        AnalyticsRecorder(SQLAlchemyAnalyticsEventRepository(db.session)),
        TechnicalMetricRecorder(SQLAlchemyTechnicalMetricRepository(db.session)),
    )


def _ops_notifier(settings: Settings) -> OpsNotificationService:
    if settings.ops_bot_token is None or not settings.ops_chat_id:
        return DisabledOpsNotificationService()
    return TelegramOpsNotificationService(
        bot_token=settings.ops_bot_token,
        chat_id=settings.ops_chat_id,
    )


def _adapter_plugin_dir(settings: Settings) -> Path:
    if settings.adapter_plugin_dir:
        return Path(settings.adapter_plugin_dir)
    return Path(__file__).resolve().parents[1] / "adapters" / "plugins"
