from __future__ import annotations

from datetime import UTC, datetime

from estateflow.services.notifications import NotificationJob
from services.common.notification_payload import (
    notification_job_from_payload,
    notification_job_to_payload,
)


def test_notification_job_payload_round_trip() -> None:
    created_at = datetime(2026, 8, 9, 10, 30, tzinfo=UTC)
    next_attempt_at = datetime(2026, 8, 9, 10, 35, tzinfo=UTC)
    job = NotificationJob(
        notification_id="notification-1",
        user_id=42,
        filter_id="filter-1",
        announcement_id="announcement-1",
        status="pending",
        priority=10,
        queue_name="notifications.high_priority",
        created_at=created_at,
        attempts=2,
        next_attempt_at=next_attempt_at,
        telegram_message_id="telegram-message-1",
    )

    restored = notification_job_from_payload(notification_job_to_payload(job))

    assert restored == job
    assert restored.idempotency_key == job.idempotency_key
