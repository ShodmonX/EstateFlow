from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy import text

from estateflow.db.session import DatabaseSessionManager
from estateflow.services.queue import EventQueue


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    status: str
    error: str | None = None


class HealthChecker(Protocol):
    async def check(self) -> list[DependencyStatus]: ...


class InfrastructureHealthChecker:
    def __init__(
        self,
        *,
        db_session_manager: DatabaseSessionManager,
        redis: Redis,
        event_queue: EventQueue | None = None,
        timeout_seconds: float = 1.5,
    ) -> None:
        self._db_session_manager = db_session_manager
        self._redis = redis
        self._event_queue = event_queue
        self._timeout_seconds = timeout_seconds

    async def check(self) -> list[DependencyStatus]:
        database, redis, queue = await asyncio.gather(
            self._check_postgres(),
            self._check_redis(),
            self._check_queue(),
        )
        return [database, redis, queue]

    async def _check_queue(self) -> DependencyStatus:
        if self._event_queue is None:
            return DependencyStatus(name="event_queue", status="ok")
        try:
            is_healthy = await asyncio.wait_for(
                self._event_queue.healthcheck(),
                timeout=self._timeout_seconds,
            )
            return DependencyStatus(
                name="event_queue",
                status="ok" if is_healthy else "error",
                error=None if is_healthy else "unhealthy",
            )
        except Exception as exc:
            return DependencyStatus(name="event_queue", status="error", error=type(exc).__name__)

    async def _check_postgres(self) -> DependencyStatus:
        try:
            async with self._db_session_manager.session() as session:
                await asyncio.wait_for(
                    session.execute(text("select 1")),
                    timeout=self._timeout_seconds,
                )
            return DependencyStatus(name="postgres", status="ok")
        except Exception as exc:
            return DependencyStatus(name="postgres", status="error", error=type(exc).__name__)

    async def _check_redis(self) -> DependencyStatus:
        try:
            await asyncio.wait_for(self._redis.ping(), timeout=self._timeout_seconds)
            return DependencyStatus(name="redis", status="ok")
        except Exception as exc:
            return DependencyStatus(name="redis", status="error", error=type(exc).__name__)
