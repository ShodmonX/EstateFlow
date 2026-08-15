from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from estateflow.services.notifications import (
    NotificationJob,
    NotificationQueueName,
    NotificationStatus,
)


def notification_job_to_payload(job: NotificationJob) -> dict[str, Any]:
    return {
        "notification_id": job.notification_id,
        "user_id": job.user_id,
        "filter_id": job.filter_id,
        "announcement_id": job.announcement_id,
        "status": job.status,
        "priority": job.priority,
        "queue_name": job.queue_name,
        "created_at": job.created_at.isoformat(),
        "attempts": job.attempts,
        "next_attempt_at": job.next_attempt_at.isoformat() if job.next_attempt_at else None,
        "telegram_message_id": job.telegram_message_id,
    }


def notification_job_from_payload(payload: dict[str, Any]) -> NotificationJob:
    next_attempt_at = payload.get("next_attempt_at")
    return NotificationJob(
        notification_id=str(payload["notification_id"]),
        user_id=int(payload["user_id"]),
        filter_id=str(payload["filter_id"]),
        announcement_id=str(payload["announcement_id"]),
        status=cast(NotificationStatus, str(payload.get("status", "pending"))),
        priority=int(payload.get("priority", 0)),
        queue_name=cast(
            NotificationQueueName,
            str(payload.get("queue_name", "notifications.standard")),
        ),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        attempts=int(payload.get("attempts", 0)),
        next_attempt_at=(datetime.fromisoformat(str(next_attempt_at)) if next_attempt_at else None),
        telegram_message_id=payload.get("telegram_message_id"),
    )
