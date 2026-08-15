from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from estateflow.application.core.config import Settings
from estateflow.application.core.correlation import get_correlation_id
from estateflow.application.core.redaction import redact

_STANDARD_LOG_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if isinstance(record.args, dict):
            record.args = redact(record.args)
        elif record.args:
            record.args = tuple(redact(arg) for arg in record.args)
        return True


class ConsoleFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", record.getMessage())
        base = (
            f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')} "
            f"{record.levelname:<8} {self.service_name} "
            f"cid={getattr(record, 'correlation_id', get_correlation_id())} "
            f"event={event} msg={record.getMessage()}"
        )
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            base += f"\n{record.exc_text}"
        return base


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "event": getattr(record, "event", record.getMessage()),
            "correlation_id": getattr(record, "correlation_id", get_correlation_id()),
            "message": record.getMessage(),
        }
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exception"] = record.exc_text
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_ATTRS and key not in payload:
                payload[key] = value
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def configure_logging(settings: Settings) -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if handler.__class__.__module__.startswith("_pytest."):
            continue
        root_logger.removeHandler(handler)
    root_logger.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    if settings.environment == "production":
        handler.setFormatter(JsonFormatter(settings.service_name))
    else:
        handler.setFormatter(ConsoleFormatter(settings.service_name))
    root_logger.addHandler(handler)
