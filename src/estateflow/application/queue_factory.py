from __future__ import annotations

from redis.asyncio import Redis

from estateflow.application.core.config import Settings
from estateflow.services.queue import EventQueue, RabbitMQEventQueue, RedisEventQueue


def create_event_queue(settings: Settings, redis: Redis) -> EventQueue:
    if settings.queue_backend == "rabbitmq":
        return RabbitMQEventQueue(
            settings.rabbitmq_url,
            exchange_name=settings.rabbitmq_exchange,
            prefetch_count=settings.rabbitmq_prefetch_count,
            max_retries=settings.rabbitmq_max_retries,
        )
    return RedisEventQueue(redis, max_retries=settings.rabbitmq_max_retries)
