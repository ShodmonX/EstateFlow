from __future__ import annotations

import os
from uuid import uuid4

import aio_pika
import pytest

from estateflow.contracts.events import QueueMessage
from estateflow.services.queue import RabbitMQEventQueue


@pytest.mark.allow_network
@pytest.mark.asyncio
async def test_rabbitmq_ack_retry_and_dlq() -> None:
    url = os.getenv("RABBITMQ_TEST_URL")
    if not url:
        pytest.skip("RABBITMQ_TEST_URL is not configured")

    queue_name = f"test.{uuid4().hex}"
    queue = RabbitMQEventQueue(url, max_retries=1, prefetch_count=1)
    try:
        assert await queue.healthcheck()
        await queue.publish(
            QueueMessage(queue_name=queue_name, payload={"step": "ack"}, correlation_id="ack")
        )
        message = await queue.pop(queue_name, timeout_seconds=2)
        assert message is not None
        await queue.ack(message)

        await queue.publish(
            QueueMessage(queue_name=queue_name, payload={"step": "retry"}, correlation_id="retry")
        )
        message = await queue.pop(queue_name, timeout_seconds=2)
        assert message is not None
        await queue.retry(message, "first-failure")
        message = await queue.pop(queue_name, timeout_seconds=2)
        assert message is not None
        await queue.retry(message, "terminal-failure")

        connection = await aio_pika.connect_robust(url)
        try:
            channel = await connection.channel()
            dlq = await channel.declare_queue(f"{queue_name}.dlq", durable=True)
            assert await dlq.get(fail=False, timeout=2, no_ack=True) is not None
        finally:
            await connection.close()
    finally:
        await queue.aclose()
