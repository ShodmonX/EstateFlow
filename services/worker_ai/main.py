from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from estateflow.application.core.config import get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.contracts.events import AI_PROCESSING_QUEUE, QueueMessage
from estateflow.services.ai_client import create_openrouter_llm_client
from estateflow.services.ai_worker import AiExtractionWorker, InMemoryAiProcessingRepository
from estateflow.services.media_storage import (
    InMemoryObjectStorage,
    MediaProcessor,
    MediaStorageService,
    ObjectStorage,
    SmartMediaResolver,
    create_r2_object_storage,
    media_processing_config_from_settings,
)
from estateflow.services.stage_consumer import RabbitStageConsumer
from services.common.runtime import create_ops_notifier, create_resources


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    if settings.openrouter_api_key is None:
        raise RuntimeError("AI worker requires OPENROUTER_API_KEY")
    resources = create_resources(settings)
    try:
        ops_notifier = create_ops_notifier(settings)
        storage: ObjectStorage
        if settings.environment == "test":
            storage = InMemoryObjectStorage()
        else:
            storage = create_r2_object_storage(settings)
        worker = AiExtractionWorker(
            llm_client=create_openrouter_llm_client(
                settings=settings,
                ops_notifier=ops_notifier,
            ),
            media_service=MediaStorageService(
                resolver=SmartMediaResolver(settings=settings),
                processor=MediaProcessor(media_processing_config_from_settings(settings)),
                storage=storage,
            ),
            repository=InMemoryAiProcessingRepository(),
            post_ai_queue=resources.queue,
            ops_notifier=ops_notifier,
            prompt_version=settings.ai_prompt_version,
            min_confidence=settings.ai_min_confidence,
            rental_min_confidence=settings.rental_min_confidence,
            ai_vision_enabled=settings.feature_ai_vision_enabled,
        )
        consumer = RabbitStageConsumer(
            queue=resources.queue,
            queue_name=AI_PROCESSING_QUEUE,
            handler=_handler(worker),
        )
        await consumer.run_forever()
    finally:
        await resources.aclose()


def _handler(worker: AiExtractionWorker) -> Callable[[QueueMessage], Awaitable[None]]:
    async def handle(message: QueueMessage) -> None:
        record = await worker.process_raw_queue_message(message)
        if record.error_type == "LLMAllModelsFailedError":
            raise RuntimeError("ai_llm_retryable_failure")
        if record.failure_reason == "storage_retryable":
            raise RuntimeError("ai_storage_retryable_failure")

    return handle


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
