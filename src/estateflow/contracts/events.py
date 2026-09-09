from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

RAW_ANNOUNCEMENT_QUEUE = "ingestion.raw_announcements"
RAW_ANNOUNCEMENT_DLQ = "ingestion.raw_announcements.dlq"
SOURCE_PARSER_QUARANTINE_QUEUE = "ingestion.source_parser.quarantine"
AI_PROCESSING_QUEUE = "ai.processing.raw_announcements"
POST_AI_DEDUP_QUEUE = "dedup.post_ai.structured_announcements"
ANNOUNCEMENT_PERSISTED_QUEUE = "announcement.persisted"
NOTIFICATION_DELIVERY_QUEUE = "notification.delivery"


@dataclass(frozen=True)
class QueueMessage:
    queue_name: str
    payload: dict[str, Any]
    correlation_id: str
    metadata: dict[str, str] = field(default_factory=dict)
    delivery_token: str | None = field(default=None, compare=False, repr=False)


def serialize_queue_message(message: QueueMessage) -> dict[str, Any]:
    data = asdict(message)
    data.pop("delivery_token", None)
    return data
