from __future__ import annotations

import asyncio

from aiogram import Bot

from estateflow.application.core.config import get_settings
from estateflow.application.core.logging import configure_logging
from estateflow.contracts.events import NOTIFICATION_DELIVERY_QUEUE, QueueMessage
from estateflow.repositories.sqlalchemy import (
    SQLAlchemyAnalyticsEventRepository,
    SQLAlchemyNotificationDeliveryRepository,
    SQLAlchemyTechnicalMetricRepository,
)
from estateflow.services.analytics import AnalyticsRecorder, TechnicalMetricRecorder
from estateflow.services.notifications import NotificationDeliveryWorker
from estateflow.services.stage_consumer import RabbitStageConsumer
from estateflow.services.telegram_notifications import AiogramTelegramNotificationClient
from services.common.notification_payload import notification_job_from_payload
from services.common.runtime import create_ops_notifier, create_resources


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    if settings.telegram_bot_token is None:
        raise RuntimeError("Notification delivery worker requires TELEGRAM_BOT_TOKEN")
    resources = create_resources(settings)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    try:
        analytics = AnalyticsRecorder(SQLAlchemyAnalyticsEventRepository(resources.db.session))
        technical = TechnicalMetricRecorder(
            SQLAlchemyTechnicalMetricRepository(resources.db.session)
        )
        worker = NotificationDeliveryWorker(
            repository=SQLAlchemyNotificationDeliveryRepository(resources.db.session),
            telegram=AiogramTelegramNotificationClient(bot),
            analytics_recorder=analytics,
            technical_recorder=technical,
            ops_notifier=create_ops_notifier(settings),
        )

        async def handle(message: QueueMessage) -> None:
            await worker.process_job(notification_job_from_payload(message.payload))

        consumer = RabbitStageConsumer(
            queue=resources.queue,
            queue_name=NOTIFICATION_DELIVERY_QUEUE,
            handler=handle,
        )
        await consumer.run_forever()
    finally:
        await bot.close()
        await resources.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
