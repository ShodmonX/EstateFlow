from __future__ import annotations

from dataclasses import dataclass

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
from estateflow.services.queue import EventQueue


@dataclass
class ServiceResources:
    db: DatabaseSessionManager
    redis: Redis
    queue: EventQueue

    async def aclose(self) -> None:
        await self.queue.aclose()
        await self.redis.aclose()
        await self.db.dispose()


def create_resources(settings: Settings) -> ServiceResources:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return ServiceResources(
        db=create_session_manager(settings),
        redis=redis,
        queue=create_event_queue(settings, redis),
    )


def create_ops_notifier(settings: Settings) -> OpsNotificationService:
    if settings.ops_bot_token is None or not settings.ops_chat_id:
        return DisabledOpsNotificationService()
    return TelegramOpsNotificationService(
        bot_token=settings.ops_bot_token,
        chat_id=settings.ops_chat_id,
    )


def create_recorders(
    resources: ServiceResources,
) -> tuple[AnalyticsRecorder, TechnicalMetricRecorder]:
    from estateflow.repositories.sqlalchemy import (
        SQLAlchemyAnalyticsEventRepository,
        SQLAlchemyTechnicalMetricRepository,
    )

    return (
        AnalyticsRecorder(SQLAlchemyAnalyticsEventRepository(resources.db.session)),
        TechnicalMetricRecorder(SQLAlchemyTechnicalMetricRepository(resources.db.session)),
    )
