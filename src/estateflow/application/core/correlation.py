from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

CORRELATION_ID_HEADER = "x-correlation-id"

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str:
    correlation_id = _correlation_id.get()
    if correlation_id is None:
        correlation_id = new_correlation_id()
        set_correlation_id(correlation_id)
    return correlation_id


def new_correlation_id() -> str:
    return uuid4().hex


def set_correlation_id(correlation_id: str) -> None:
    _correlation_id.set(correlation_id)
