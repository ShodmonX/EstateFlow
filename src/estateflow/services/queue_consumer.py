from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from estateflow.application.core.config import Settings
from estateflow.contracts.events import POST_AI_DEDUP_QUEUE
from estateflow.services.ops_notifications import OpsNotificationService
from estateflow.services.queue import (
    AI_PROCESSING_QUEUE,
    RAW_ANNOUNCEMENT_QUEUE,
    EventQueue,
)

if TYPE_CHECKING:
    from estateflow.services.ai_worker import AiExtractionWorker
    from estateflow.services.audience_tags import AudienceTagService
    from estateflow.services.ingestion_pipeline import PreAiIngestionProcessor
    from estateflow.services.notifications import NotificationDeliveryWorker
    from estateflow.services.post_ai_dedup import PostAiDedupProcessor

logger = logging.getLogger(__name__)
QueueStage = Literal["all", "pre_ai", "ai", "dedup", "notification"]


class IngestionQueueConsumerWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        redis_event_queue: EventQueue,
        pre_ai_processor: PreAiIngestionProcessor | None,
        ai_worker: AiExtractionWorker | None,
        post_ai_processor: PostAiDedupProcessor | None,
        notification_worker: NotificationDeliveryWorker | None = None,
        stage: QueueStage = "all",
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self._settings = settings
        self._queue = redis_event_queue
        self._pre_ai_processor = pre_ai_processor
        self._ai_worker = ai_worker
        self._post_ai_processor = post_ai_processor
        self._notification_worker = notification_worker
        self._stage = stage
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        self._stop_event.clear()
        logger.info(
            "Ingestion queue consumer worker starting",
            extra={"event": "queue_consumer.starting"},
        )
        try:
            while not self._stop_event.is_set():
                processed_any = await self.process_batch_once()
                if self._notification_worker is not None and self._stage in {
                    "all",
                    "notification",
                }:
                    try:
                        await self._notification_worker.process_batch()
                    except Exception as exc:
                        logger.warning(
                            "Notification delivery batch failed",
                            extra={
                                "event": "queue_consumer.notification_failed",
                                "error": str(exc),
                            },
                        )
                if not processed_any:
                    await asyncio.sleep(self._poll_interval_seconds)
        finally:
            logger.info(
                "Ingestion queue consumer worker stopped",
                extra={"event": "queue_consumer.stopped"},
            )

    async def process_batch_once(self) -> bool:
        processed = False

        # 1. Process raw announcement queue -> Pre-AI Dedup
        raw_msg = (
            await self._queue.pop(RAW_ANNOUNCEMENT_QUEUE, timeout_seconds=0.1)
            if self._stage in {"all", "pre_ai"}
            else None
        )
        if raw_msg is not None:
            processed = True
            logger.info(
                "Processing raw announcement event",
                extra={
                    "event": "queue_consumer.raw_received",
                    "correlation_id": raw_msg.correlation_id,
                },
            )
            try:
                if self._pre_ai_processor is None:
                    raise RuntimeError("Pre-AI processor is not configured for this worker")
                await self._pre_ai_processor.process_raw_queue_message(raw_msg)
                await self._ack(raw_msg)
            except Exception as exc:
                await self._retry(raw_msg, str(exc))
                logger.exception(
                    "Error processing raw announcement message",
                    extra={
                        "event": "queue_consumer.raw_error",
                        "correlation_id": raw_msg.correlation_id,
                        "error": str(exc),
                    },
                )

        # 2. Process AI processing queue -> LLM Extraction
        ai_msg = (
            await self._queue.pop(AI_PROCESSING_QUEUE, timeout_seconds=0.1)
            if self._stage in {"all", "ai"}
            else None
        )
        if ai_msg is not None:
            processed = True
            logger.info(
                "Processing AI extraction event",
                extra={
                    "event": "queue_consumer.ai_received",
                    "correlation_id": ai_msg.correlation_id,
                },
            )
            if self._ai_worker is not None:
                try:
                    await self._ai_worker.process_raw_queue_message(ai_msg)
                    await self._ack(ai_msg)
                except Exception as exc:
                    await self._retry(ai_msg, str(exc))
                    logger.exception(
                        "Error processing AI extraction message",
                        extra={
                            "event": "queue_consumer.ai_error",
                            "correlation_id": ai_msg.correlation_id,
                            "error": str(exc),
                        },
                    )
            else:
                logger.warning(
                    "AI worker not configured (missing OPENROUTER_API_KEY), skipping AI extraction",
                    extra={"event": "queue_consumer.ai_skipped"},
                )
                await self._retry(ai_msg, "AI worker is not configured")

        # 3. Process Post-AI Dedup queue -> DB Save & Match
        post_ai_msg = (
            await self._queue.pop(POST_AI_DEDUP_QUEUE, timeout_seconds=0.1)
            if self._stage in {"all", "dedup"}
            else None
        )
        if post_ai_msg is not None:
            processed = True
            logger.info(
                "Processing post-AI dedup & database save event",
                extra={
                    "event": "queue_consumer.post_ai_received",
                    "correlation_id": post_ai_msg.correlation_id,
                },
            )
            try:
                from estateflow.services.post_ai_dedup import StructuredAnnouncement

                structured = StructuredAnnouncement.from_payload(post_ai_msg.payload)
                if self._post_ai_processor is None:
                    raise RuntimeError("Post-AI processor is not configured for this worker")
                await self._post_ai_processor.process(structured)
                await self._ack(post_ai_msg)
            except Exception as exc:
                await self._retry(post_ai_msg, str(exc))
                logger.exception(
                    "Error processing post-AI dedup message",
                    extra={
                        "event": "queue_consumer.post_ai_error",
                        "correlation_id": post_ai_msg.correlation_id,
                        "error": str(exc),
                    },
                )

        return processed

    def stop(self) -> None:
        self._stop_event.set()

    async def _ack(self, message: object) -> None:
        ack = getattr(self._queue, "ack", None)
        if ack is not None:
            await ack(message)

    async def _retry(self, message: object, reason: str) -> None:
        retry = getattr(self._queue, "retry", None)
        if retry is not None:
            await retry(message, reason)


def create_queue_consumer_worker(
    *,
    settings: Settings,
    redis_event_queue: EventQueue,
    post_ai_processor: PostAiDedupProcessor,
    ops_notifier: OpsNotificationService,
    audience_tag_service: AudienceTagService | None = None,
    notification_worker: NotificationDeliveryWorker | None = None,
    release_controls: Any = None,
    stage: QueueStage = "all",
) -> IngestionQueueConsumerWorker:
    from estateflow.services.ai_client import (
        LLMConfigurationError,
        create_openrouter_llm_client,
    )
    from estateflow.services.ai_worker import (
        AiExtractionWorker,
        InMemoryAiProcessingRepository,
    )
    from estateflow.services.ingestion_pipeline import PreAiIngestionProcessor
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
    from estateflow.services.pre_ai_dedup import (
        InMemoryPreAiDedupSignalStore,
        PreAiDedupConfig,
        PreAiDedupFilter,
        StableMediaReferencePHashProvider,
    )

    signal_store = InMemoryPreAiDedupSignalStore()
    phash_provider = StableMediaReferencePHashProvider()
    dedup_filter = PreAiDedupFilter(
        signal_store=signal_store,
        phash_provider=phash_provider,
        ops_notifier=ops_notifier,
        config=PreAiDedupConfig(
            near_text_threshold=settings.pre_ai_near_text_threshold,
            phash_hamming_threshold=settings.media_phash_hamming_threshold,
        ),
    )
    pre_ai_processor = PreAiIngestionProcessor(
        dedup_filter=dedup_filter,
        ai_queue=redis_event_queue,
        signal_store=signal_store,
        phash_provider=phash_provider,
    )

    ai_worker: AiExtractionWorker | None = None
    if settings.openrouter_api_key is not None:
        try:
            llm_client = create_openrouter_llm_client(settings=settings, ops_notifier=ops_notifier)
            storage: ObjectStorage
            try:
                storage = create_r2_object_storage(settings)
            except (StorageUploadError, ModuleNotFoundError, ImportError):
                storage = InMemoryObjectStorage()

            media_service = MediaStorageService(
                resolver=SmartMediaResolver(settings=settings),
                processor=MediaProcessor(media_processing_config_from_settings(settings)),
                storage=storage,
            )

            ai_worker = AiExtractionWorker(
                llm_client=llm_client,
                media_service=media_service,
                repository=InMemoryAiProcessingRepository(),
                post_ai_queue=redis_event_queue,
                ops_notifier=ops_notifier,
                prompt_version=settings.ai_prompt_version,
                min_confidence=settings.ai_min_confidence,
                rental_min_confidence=settings.rental_min_confidence,
                audience_tag_service=audience_tag_service,
                release_controls=release_controls,
                ai_vision_enabled=settings.feature_ai_vision_enabled,
            )
        except LLMConfigurationError:
            ai_worker = None

    return IngestionQueueConsumerWorker(
        settings=settings,
        redis_event_queue=redis_event_queue,
        pre_ai_processor=pre_ai_processor,
        ai_worker=ai_worker,
        post_ai_processor=post_ai_processor,
        notification_worker=notification_worker,
        stage=stage,
    )
