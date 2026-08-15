from __future__ import annotations

import asyncio

from estateflow.application.core.config import get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.contracts.events import (
    ANNOUNCEMENT_PERSISTED_QUEUE,
    NOTIFICATION_DELIVERY_QUEUE,
    QueueMessage,
)
from estateflow.repositories.sqlalchemy import (
    SQLAlchemyNotificationJobRepository,
    SQLAlchemyNotificationSavedFilterRepository,
    SQLAlchemyNotificationUserRepository,
)
from estateflow.services.notifications import NotificationJob, NotificationMatchingEngine
from estateflow.services.queue import PublishingQueue
from estateflow.services.stage_consumer import RabbitStageConsumer
from services.common.notification_payload import notification_job_to_payload
from services.common.runtime import create_ops_notifier, create_resources


class RabbitNotificationPublisher:
    def __init__(self, queue: PublishingQueue) -> None:
        self._queue = queue

    async def publish(self, job: NotificationJob) -> None:
        await self._queue.publish(
            QueueMessage(
                queue_name=NOTIFICATION_DELIVERY_QUEUE,
                payload=notification_job_to_payload(job),
                correlation_id=job.notification_id,
                metadata={
                    "notification_id": job.notification_id,
                    "announcement_id": job.announcement_id,
                },
            )
        )


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    resources = create_resources(settings)
    try:
        engine = NotificationMatchingEngine(
            filter_repository=SQLAlchemyNotificationSavedFilterRepository(resources.db.session),
            user_repository=SQLAlchemyNotificationUserRepository(resources.db.session),
            job_repository=SQLAlchemyNotificationJobRepository(resources.db.session),
            queue_publisher=RabbitNotificationPublisher(resources.queue),
            ops_notifier=create_ops_notifier(settings),
        )

        async def handle(message: QueueMessage) -> None:
            from estateflow.services.post_ai_dedup import StructuredAnnouncement

            await engine.match_announcement(StructuredAnnouncement.from_payload(message.payload))

        consumer = RabbitStageConsumer(
            queue=resources.queue,
            queue_name=ANNOUNCEMENT_PERSISTED_QUEUE,
            handler=handle,
        )
        await consumer.run_forever()
    finally:
        await resources.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
