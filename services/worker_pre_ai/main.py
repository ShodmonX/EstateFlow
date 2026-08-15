from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from estateflow.application.core.config import get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.contracts.events import RAW_ANNOUNCEMENT_QUEUE, QueueMessage
from estateflow.services.ingestion_pipeline import PreAiIngestionProcessor
from estateflow.services.pre_ai_dedup import (
    InMemoryPreAiDedupSignalStore,
    PreAiDedupConfig,
    PreAiDedupFilter,
    StableMediaReferencePHashProvider,
)
from estateflow.services.stage_consumer import RabbitStageConsumer
from services.common.runtime import create_ops_notifier, create_resources


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    resources = create_resources(settings)
    ops_notifier = create_ops_notifier(settings)
    signal_store = InMemoryPreAiDedupSignalStore()
    phash_provider = StableMediaReferencePHashProvider()
    processor = PreAiIngestionProcessor(
        dedup_filter=PreAiDedupFilter(
            signal_store=signal_store,
            phash_provider=phash_provider,
            ops_notifier=ops_notifier,
            config=PreAiDedupConfig(
                near_text_threshold=settings.pre_ai_near_text_threshold,
                phash_hamming_threshold=settings.media_phash_hamming_threshold,
            ),
        ),
        ai_queue=resources.queue,
        signal_store=signal_store,
        phash_provider=phash_provider,
    )
    consumer = RabbitStageConsumer(
        queue=resources.queue,
        queue_name=RAW_ANNOUNCEMENT_QUEUE,
        handler=_handler(processor),
    )
    try:
        await consumer.run_forever()
    finally:
        await resources.aclose()


def _handler(processor: PreAiIngestionProcessor) -> Callable[[QueueMessage], Awaitable[None]]:
    async def handle(message: QueueMessage) -> None:
        await processor.process_raw_queue_message(message)

    return handle


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
