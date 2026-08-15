from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from estateflow.services.analytics import AnalyticsRecorder, safe_record_event
from estateflow.services.search import SearchCriteria


class SavedFilterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "estateflow.saved_filter.v1"
    criteria: SearchCriteria


@dataclass(frozen=True)
class UserFilter:
    filter_id: str
    user_id: int
    name: str
    criteria: SearchCriteria
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SavedFilterRepository(Protocol):
    async def create(self, *, user_id: int, name: str, criteria: SearchCriteria) -> UserFilter: ...

    async def list_for_user(self, *, user_id: int) -> list[UserFilter]: ...

    async def get_for_user(self, *, user_id: int, filter_id: str) -> UserFilter | None: ...

    async def update(
        self,
        *,
        user_id: int,
        filter_id: str,
        name: str | None = None,
        criteria: SearchCriteria | None = None,
        enabled: bool | None = None,
    ) -> UserFilter: ...

    async def delete(self, *, user_id: int, filter_id: str) -> bool: ...


class SavedFilterActivationRecorder(Protocol):
    async def record_qualifying_action(
        self,
        *,
        user_id: int,
        action: Literal["saved_filter"],
        action_id: str,
    ) -> object: ...


class SavedFilterService:
    def __init__(
        self,
        repository: SavedFilterRepository,
        *,
        max_filters_per_user: int = 10,
        activation_recorder: SavedFilterActivationRecorder | None = None,
        analytics_recorder: AnalyticsRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._max_filters_per_user = max_filters_per_user
        self._activation_recorder = activation_recorder
        self._analytics_recorder = analytics_recorder

    async def create(self, *, user_id: int, name: str, criteria: SearchCriteria) -> UserFilter:
        clean_name = _validate_name(name)
        existing = await self._repository.list_for_user(user_id=user_id)
        if len(existing) >= self._max_filters_per_user:
            raise ValueError("Saved filter limit reached.")
        created = await self._repository.create(user_id=user_id, name=clean_name, criteria=criteria)
        if self._activation_recorder is not None:
            await self._activation_recorder.record_qualifying_action(
                user_id=user_id,
                action="saved_filter",
                action_id=created.filter_id,
            )
        await safe_record_event(
            self._analytics_recorder,
            event_name="saved_filter_created",
            idempotency_key=f"saved_filter_created:{created.filter_id}",
            occurred_at=created.created_at,
            user_id=user_id,
            subject_id=created.filter_id,
        )
        return created

    async def list_for_user(self, *, user_id: int) -> list[UserFilter]:
        return await self._repository.list_for_user(user_id=user_id)

    async def get_for_user(self, *, user_id: int, filter_id: str) -> UserFilter | None:
        return await self._repository.get_for_user(user_id=user_id, filter_id=filter_id)

    async def update(
        self,
        *,
        user_id: int,
        filter_id: str,
        name: str | None = None,
        criteria: SearchCriteria | None = None,
        enabled: bool | None = None,
    ) -> UserFilter:
        return await self._repository.update(
            user_id=user_id,
            filter_id=filter_id,
            name=None if name is None else _validate_name(name),
            criteria=criteria,
            enabled=enabled,
        )

    async def set_enabled(self, *, user_id: int, filter_id: str, enabled: bool) -> UserFilter:
        return await self.update(user_id=user_id, filter_id=filter_id, enabled=enabled)

    async def delete(self, *, user_id: int, filter_id: str) -> bool:
        return await self._repository.delete(user_id=user_id, filter_id=filter_id)


def _validate_name(name: str) -> str:
    clean = " ".join(name.split())
    if not 1 <= len(clean) <= 64:
        raise ValueError("Filter name must be 1-64 characters.")
    return clean
