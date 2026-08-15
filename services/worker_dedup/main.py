from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from estateflow.application.core.config import get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.contracts.events import POST_AI_DEDUP_QUEUE, QueueMessage
from estateflow.repositories.post_ai_dedup import LazyAsyncpgParentChildAnnouncementRepository
from estateflow.services.post_ai_dedup import (
    InMemoryAnnouncementRepository,
    ManualReviewQueueService,
    PostAiDedupProcessor,
    WeightedDeduplicationEngine,
    dedup_scoring_config_from_settings,
)
from estateflow.services.stage_consumer import RabbitStageConsumer
from services.common.runtime import create_recorders, create_resources


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    resources = create_resources(settings)
    try:
        _analytics, technical_recorder = create_recorders(resources)
        config = dedup_scoring_config_from_settings(settings)
        if settings.environment == "test":
            repository: Any = InMemoryAnnouncementRepository(config=config)
        else:
            repository = LazyAsyncpgParentChildAnnouncementRepository(
                settings.database_url,
                config=config,
            )
        processor = PostAiDedupProcessor(
            engine=WeightedDeduplicationEngine(
                candidate_repository=repository,
                config=config,
            ),
            announcement_repository=repository,
            manual_review_queue=ManualReviewQueueService(repository),
            technical_recorder=technical_recorder,
            event_queue=resources.queue,
        )
        consumer = RabbitStageConsumer(
            queue=resources.queue,
            queue_name=POST_AI_DEDUP_QUEUE,
            handler=_handler(processor),
        )
        await consumer.run_forever()
    finally:
        await resources.aclose()


def _handler(processor: PostAiDedupProcessor) -> Callable[[QueueMessage], Awaitable[None]]:
    from estateflow.services.post_ai_dedup import StructuredAnnouncement

    async def handle(message: QueueMessage) -> None:
        await processor.process(StructuredAnnouncement.from_payload(message.payload))

    return handle


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
