from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from estateflow.services.notifications import (  # noqa: E402
    InMemoryNotificationJobRepository,
    NotificationDeliveryWorker,
    NotificationMessageFormatter,
    TelegramSendResult,
)
from estateflow.services.queue import QueueMessage  # noqa: E402
from estateflow.services.saved_filters import UserFilter  # noqa: E402
from estateflow.services.search import InMemorySearchRepository, SearchCriteria  # noqa: E402
from tests.fixtures import canonical_listing_fixture  # noqa: E402


class FakeQueue:
    def __init__(self) -> None:
        self.messages: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        self.messages.append(message)

    async def consume_one(self) -> QueueMessage | None:
        if not self.messages:
            return None
        return self.messages.pop(0)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = 0

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramSendResult:
        self.sent += 1
        return TelegramSendResult(message_id=f"fake:{self.sent}")


async def _drain_queue(queue: FakeQueue, *, workers: int) -> int:
    processed = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal processed
        while True:
            message = await queue.consume_one()
            if message is None:
                return
            _ = message.payload["id"]
            async with lock:
                processed += 1

    await asyncio.gather(*(worker() for _ in range(workers)))
    return processed


async def run_dry_load(*, users: int, listings: int, workers: int) -> dict[str, object]:
    queue = FakeQueue()
    for index in range(listings):
        await queue.publish(
            QueueMessage(
                queue_name="dry-run.raw",
                payload={"id": index, "schema_version": "dry-run.v1"},
                correlation_id=f"dry-run:{index}",
                metadata={"synthetic": "true"},
            )
        )
    backlog_before = len(queue.messages)
    queue_started = time.perf_counter()
    processed = await _drain_queue(queue, workers=workers)
    queue_elapsed_ms = (time.perf_counter() - queue_started) * 1000

    announcements = [
        canonical_listing_fixture(
            announcement_id=f"ann-{index}",
            price=Decimal(300 + (index % 12) * 25),
            district="Yunusobod" if index % 2 == 0 else "Chilonzor",
            rooms=1 + (index % 3),
        )
        for index in range(listings)
    ]
    search_repo = InMemorySearchRepository(announcements)
    latencies_ms: list[float] = []
    for index in range(users):
        criteria = SearchCriteria(
            district="Yunusobod" if index % 2 == 0 else "Chilonzor",
            max_price=Decimal("525"),
            limit=10,
        )
        started = time.perf_counter()
        await search_repo.search(criteria)
        latencies_ms.append((time.perf_counter() - started) * 1000)

    job_repo = InMemoryNotificationJobRepository()
    saved_filter = UserFilter(
        filter_id="dry-filter",
        user_id=1,
        name="Dry-run",
        criteria=SearchCriteria(district="Yunusobod"),
    )
    announcement = announcements[0]
    for user_id in range(1, users + 1):
        job, _created = await job_repo.create_pending(
            user_id=user_id,
            filter_id=f"{saved_filter.filter_id}-{user_id}",
            announcement_id=announcement.announcement_id,
            priority=0,
            queue_name="notifications.standard",
        )
        job_repo.add_delivery_context(
            announcement=announcement,
            saved_filter=UserFilter(
                filter_id=job.filter_id,
                user_id=user_id,
                name=saved_filter.name,
                criteria=saved_filter.criteria,
            ),
        )
    telegram = FakeTelegram()
    notification_started = time.perf_counter()
    notification_result = await NotificationDeliveryWorker(
        repository=job_repo,
        telegram=telegram,
        formatter=NotificationMessageFormatter(),
    ).process_batch(limit=users, now=datetime(2026, 8, 1, tzinfo=UTC))
    notification_elapsed_ms = (time.perf_counter() - notification_started) * 1000

    return {
        "dry_run": True,
        "real_external_calls": False,
        "users": users,
        "listings": listings,
        "workers": workers,
        "queue": {
            "backlog_before": backlog_before,
            "processed": processed,
            "backlog_after": len(queue.messages),
            "elapsed_ms": round(queue_elapsed_ms, 3),
        },
        "search_latency_ms": {
            "count": len(latencies_ms),
            "p50": round(statistics.median(latencies_ms), 3),
            "max": round(max(latencies_ms), 3),
        },
        "notifications": {
            **asdict(notification_result.metrics),
            "elapsed_ms": round(notification_elapsed_ms, 3),
            "fake_telegram_sent": telegram.sent,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 7 fake release dry-run load check.")
    parser.add_argument("--users", type=int, default=75)
    parser.add_argument("--listings", type=int, default=150)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    result = asyncio.run(
        run_dry_load(users=args.users, listings=args.listings, workers=args.workers)
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
