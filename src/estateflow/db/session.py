from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from estateflow.application.core.config import Settings


class DatabaseSessionManager:
    def __init__(self, settings: Settings, *, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(
            settings.database_url,
            echo=echo,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            yield session

    async def dispose(self) -> None:
        await self._engine.dispose()


def create_session_manager(settings: Settings, *, echo: bool = False) -> DatabaseSessionManager:
    return DatabaseSessionManager(settings, echo=echo)
