from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import aio_pika

from estateflow.contracts.events import QueueMessage
from estateflow.services.queue import RabbitMQEventQueue


async def run() -> None:
    url = os.getenv("RABBITMQ_SMOKE_URL", "amqp://estateflow:estateflow_dev_password@127.0.0.1:5672/")
    queue_name = f"smoke.{uuid4().hex}"
    queue = RabbitMQEventQueue(url, max_retries=1, prefetch_count=1)
    try:
        assert await queue.healthcheck(), "RabbitMQ healthcheck failed"
        await queue.publish(
            QueueMessage(
                queue_name=queue_name,
                payload={"kind": "ack"},
                correlation_id="smoke-ack",
            )
        )
        message = await queue.pop(queue_name, timeout_seconds=2)
        assert message is not None and message.payload["kind"] == "ack"
        await queue.ack(message)

        await queue.publish(
            QueueMessage(
                queue_name=queue_name,
                payload={"kind": "retry"},
                correlation_id="smoke-retry",
            )
        )
        message = await queue.pop(queue_name, timeout_seconds=2)
        assert message is not None
        await queue.retry(message, "expected-smoke-error")
        message = await queue.pop(queue_name, timeout_seconds=2)
        assert message is not None and message.metadata["retry_count"] == "1"
        await queue.retry(message, "terminal-smoke-error")

        connection = await aio_pika.connect_robust(url)
        try:
            channel = await connection.channel()
            dlq = await channel.declare_queue(f"{queue_name}.dlq", durable=True)
            dead = await dlq.get(fail=False, timeout=2, no_ack=True)
            assert dead is not None, "Expected terminal message in DLQ"
        finally:
            await connection.close()
        print("rabbitmq_smoke=ok")
    finally:
        await queue.aclose()


if __name__ == "__main__":
    asyncio.run(run())
