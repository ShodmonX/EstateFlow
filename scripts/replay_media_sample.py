from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import quote, urlparse

import aio_pika
import aiohttp
from aio_pika import DeliveryMode, ExchangeType, Message


async def main() -> None:
    parsed = urlparse(os.environ["RABBITMQ_URL"])
    base = f"http://{parsed.hostname or 'rabbitmq'}:15672"
    source = "ai.processing.raw_announcements.dlq"
    request = {
        "count": 20,
        "ackmode": "ack_requeue_true",
        "encoding": "auto",
        "truncate": 500000,
    }
    auth = aiohttp.BasicAuth(parsed.username or "", parsed.password or "")
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(
            f"{base}/api/queues/%2F/{quote(source, safe='')}/get",
            json=request,
        ) as response:
            messages = await response.json()
    selected: dict[str, object] | None = None
    for item in messages:
        try:
            body = json.loads(item.get("payload", "{}"))
        except (TypeError, json.JSONDecodeError):
            continue
        payload = body.get("payload", {}) if isinstance(body, dict) else {}
        media = payload.get("media", []) if isinstance(payload, dict) else []
        if isinstance(media, list) and media:
            selected = body
            break
    if selected is None:
        print("REPLAY none")
        return
    selected["queue_name"] = "ai.processing.raw_announcements"
    raw_metadata = selected.get("metadata", {})
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    metadata.update({"retry_count": "0", "replayed_from": source, "media_probe": "true"})
    selected["metadata"] = metadata
    connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            os.environ.get("RABBITMQ_EXCHANGE", "estateflow.events"),
            ExchangeType.DIRECT,
            durable=True,
        )
        await exchange.publish(
            Message(
                json.dumps(selected).encode("utf-8"),
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key="ai.processing.raw_announcements",
        )
    finally:
        await connection.close()
    payload = selected.get("payload", {})
    print(
        "REPLAY media_sample",
        payload.get("idempotency_key") if isinstance(payload, dict) else None,
        "media_count=" + str(len(payload.get("media", [])) if isinstance(payload, dict) else 0),
    )


asyncio.run(main())
