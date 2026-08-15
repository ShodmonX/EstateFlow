from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from estateflow.contracts.events import QueueMessage
from estateflow.services.queue import EventQueue

logger = logging.getLogger(__name__)
MessageHandler = Callable[[QueueMessage], Awaitable[None]]


class RabbitStageConsumer:
    """Small single-queue runner shared by isolated service entrypoints."""

    def __init__(
        self,
        *,
        queue: EventQueue,
        queue_name: str,
        handler: MessageHandler,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self._queue = queue
        self._queue_name = queue_name
        self._handler = handler
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        self._stop_event.clear()
        logger.info(
            "Stage consumer starting",
            extra={"event": "stage_consumer.starting", "queue_name": self._queue_name},
        )
        try:
            while not self._stop_event.is_set():
                message = await self._queue.pop(self._queue_name, timeout_seconds=0.5)
                if message is None:
                    await asyncio.sleep(self._poll_interval_seconds)
                    continue
                try:
                    await self._handler(message)
                except Exception as exc:
                    await self._queue.retry(message, str(exc))
                    logger.exception(
                        "Stage message processing failed",
                        extra={
                            "event": "stage_consumer.failed",
                            "queue_name": self._queue_name,
                            "correlation_id": message.correlation_id,
                            "error": str(exc),
                        },
                    )
                else:
                    await self._queue.ack(message)
        finally:
            logger.info(
                "Stage consumer stopped",
                extra={"event": "stage_consumer.stopped", "queue_name": self._queue_name},
            )

    def stop(self) -> None:
        self._stop_event.set()
