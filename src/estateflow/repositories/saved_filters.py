from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]

from estateflow.services.saved_filters import SavedFilterPayload, UserFilter
from estateflow.services.search import SearchCriteria


class InMemorySavedFilterRepository:
    def __init__(self) -> None:
        self._filters: dict[str, UserFilter] = {}

    async def create(self, *, user_id: int, name: str, criteria: SearchCriteria) -> UserFilter:
        item = UserFilter(filter_id=str(uuid4()), user_id=user_id, name=name, criteria=criteria)
        self._filters[item.filter_id] = item
        return item

    async def list_for_user(self, *, user_id: int) -> list[UserFilter]:
        return sorted(
            [item for item in self._filters.values() if item.user_id == user_id],
            key=lambda item: item.created_at,
            reverse=True,
        )

    async def get_for_user(self, *, user_id: int, filter_id: str) -> UserFilter | None:
        item = self._filters.get(filter_id)
        if item is None or item.user_id != user_id:
            return None
        return item

    async def update(
        self,
        *,
        user_id: int,
        filter_id: str,
        name: str | None = None,
        criteria: SearchCriteria | None = None,
        enabled: bool | None = None,
    ) -> UserFilter:
        item = await self.get_for_user(user_id=user_id, filter_id=filter_id)
        if item is None:
            raise KeyError("Saved filter not found.")
        updated = replace(
            item,
            name=item.name if name is None else name,
            criteria=item.criteria if criteria is None else criteria,
            enabled=item.enabled if enabled is None else enabled,
            updated_at=datetime.now(UTC),
        )
        self._filters[filter_id] = updated
        return updated

    async def delete(self, *, user_id: int, filter_id: str) -> bool:
        item = await self.get_for_user(user_id=user_id, filter_id=filter_id)
        if item is None:
            return False
        del self._filters[filter_id]
        return True


class AsyncpgSavedFilterRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, *, user_id: int, name: str, criteria: SearchCriteria) -> UserFilter:
        record = await self._pool.fetchrow(
            """
            insert into user_filters(user_id, name, filters)
            values ($1, $2, $3::jsonb)
            returning *
            """,
            user_id,
            name,
            _payload(criteria),
        )
        assert record is not None
        return _from_record(record)

    async def list_for_user(self, *, user_id: int) -> list[UserFilter]:
        records = await self._pool.fetch(
            """
            select *
            from user_filters
            where user_id = $1
            order by created_at desc, filter_id desc
            """,
            user_id,
        )
        return [_from_record(record) for record in records]

    async def get_for_user(self, *, user_id: int, filter_id: str) -> UserFilter | None:
        record = await self._pool.fetchrow(
            """
            select *
            from user_filters
            where user_id = $1 and filter_id = $2::uuid
            """,
            user_id,
            filter_id,
        )
        return None if record is None else _from_record(record)

    async def update(
        self,
        *,
        user_id: int,
        filter_id: str,
        name: str | None = None,
        criteria: SearchCriteria | None = None,
        enabled: bool | None = None,
    ) -> UserFilter:
        current = await self.get_for_user(user_id=user_id, filter_id=filter_id)
        if current is None:
            raise KeyError("Saved filter not found.")
        record = await self._pool.fetchrow(
            """
            update user_filters
            set name = $3,
                filters = $4::jsonb,
                enabled = $5,
                updated_at = now()
            where user_id = $1 and filter_id = $2::uuid
            returning *
            """,
            user_id,
            filter_id,
            current.name if name is None else name,
            _payload(current.criteria if criteria is None else criteria),
            current.enabled if enabled is None else enabled,
        )
        assert record is not None
        return _from_record(record)

    async def delete(self, *, user_id: int, filter_id: str) -> bool:
        result = await self._pool.execute(
            "delete from user_filters where user_id = $1 and filter_id = $2::uuid",
            user_id,
            filter_id,
        )
        return bool(result.endswith(" 1"))


def _payload(criteria: SearchCriteria) -> str:
    return SavedFilterPayload(criteria=criteria).model_dump_json()


def _from_record(record: Any) -> UserFilter:
    raw = record["filters"]
    payload = raw if isinstance(raw, dict) else json.loads(raw)
    criteria_payload = payload.get("criteria", payload)
    return UserFilter(
        filter_id=str(record["filter_id"]),
        user_id=int(record["user_id"]),
        name=str(record["name"]),
        criteria=SearchCriteria.model_validate(criteria_payload),
        enabled=bool(record["enabled"]),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )
