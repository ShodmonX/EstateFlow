from __future__ import annotations

import logging
from time import perf_counter

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from estateflow.application.core.correlation import (
    CORRELATION_ID_HEADER,
    new_correlation_id,
    set_correlation_id,
)

logger = logging.getLogger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or new_correlation_id()
        set_correlation_id(correlation_id)
        started_at = perf_counter()
        logger.info(
            "HTTP request started",
            extra={
                "event": "http.request.started",
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http.request.completed",
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
