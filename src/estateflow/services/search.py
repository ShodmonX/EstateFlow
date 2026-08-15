from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from estateflow.services.audience_tags import normalize_tag_key
from estateflow.services.extraction import RenovationLevel
from estateflow.services.post_ai_dedup import StructuredAnnouncement

SearchSort = Literal["newest", "cheapest", "price_desc"]


class SearchCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["estateflow.search.v1"] = "estateflow.search.v1"
    min_price: Decimal | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    include_per_person: bool = False
    district: str | None = None
    rooms: int | None = Field(default=None, ge=0)
    renovation_level: RenovationLevel | None = None
    audience_tag: str | None = None
    sort: SearchSort = "newest"
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)

    @field_validator("district")
    @classmethod
    def normalize_district(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = " ".join(value.split())
        return stripped or None

    @field_validator("audience_tag")
    @classmethod
    def validate_audience_tag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_tag_key(value)

    @model_validator(mode="after")
    def validate_price_range(self) -> SearchCriteria:
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price must be less than or equal to max_price")
        return self

    @property
    def has_price_filter(self) -> bool:
        return self.min_price is not None or self.max_price is not None


class SearchMetadata(BaseModel):
    schema_version: Literal["estateflow.search.meta.v1"] = "estateflow.search.meta.v1"
    returned: int
    limit: int
    offset: int
    total: int | None = None
    price_field: Literal["price_normalized_monthly"] = "price_normalized_monthly"
    price_periods_included: tuple[Literal["daily", "monthly"], ...] = ("daily", "monthly")
    one_time_excluded: bool = True
    per_person_included: bool
    null_price_included: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResult:
    items: tuple[StructuredAnnouncement, ...]
    metadata: SearchMetadata


class AnnouncementSearchRepository(Protocol):
    async def search(self, criteria: SearchCriteria) -> SearchResult: ...

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None: ...


class SearchService:
    def __init__(self, repository: AnnouncementSearchRepository) -> None:
        self._repository = repository

    async def search(self, criteria: SearchCriteria) -> SearchResult:
        return await self._repository.search(criteria)

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None:
        return await self._repository.get(announcement_id)


class InMemorySearchRepository:
    def __init__(self, announcements: list[StructuredAnnouncement] | None = None) -> None:
        self._announcements = list(announcements or [])

    async def search(self, criteria: SearchCriteria) -> SearchResult:
        filtered = [item for item in self._announcements if announcement_matches(item, criteria)]
        filtered.sort(key=_sort_key(criteria), reverse=criteria.sort in {"newest", "price_desc"})
        page = tuple(filtered[criteria.offset : criteria.offset + criteria.limit])
        return SearchResult(
            items=page,
            metadata=_metadata(criteria, returned=len(page), total=len(filtered)),
        )

    async def get(self, announcement_id: str) -> StructuredAnnouncement | None:
        for item in self._announcements:
            if item.announcement_id == announcement_id and announcement_matches(
                item, SearchCriteria()
            ):
                return item
        return None


def announcement_matches(announcement: StructuredAnnouncement, criteria: SearchCriteria) -> bool:
    canonical = announcement.canonical
    if announcement.status != "active" or announcement.parent_id is not None:
        return False
    if canonical.price_period == "one_time":
        return False
    if canonical.price_basis == "per_person" and not criteria.include_per_person:
        return False
    if criteria.has_price_filter:
        if canonical.price_normalized_monthly is None:
            return False
        if (
            criteria.min_price is not None
            and canonical.price_normalized_monthly < criteria.min_price
        ):
            return False
        if (
            criteria.max_price is not None
            and canonical.price_normalized_monthly > criteria.max_price
        ):
            return False
    if criteria.district and (canonical.district or "").casefold() != criteria.district.casefold():
        return False
    if criteria.rooms is not None and canonical.rooms != criteria.rooms:
        return False
    if (
        criteria.renovation_level is not None
        and canonical.renovation_level != criteria.renovation_level
    ):
        return False
    if criteria.audience_tag is not None:
        tags = {tag.casefold() for tag in canonical.audience_tags}
        excluded = {tag.casefold() for tag in canonical.audience_excluded_tags}
        if criteria.audience_tag not in tags or criteria.audience_tag in excluded:
            return False
    return True


def _sort_key(criteria: SearchCriteria) -> Callable[[StructuredAnnouncement], tuple[Any, ...]]:
    if criteria.sort == "cheapest":
        return lambda item: (
            item.canonical.price_normalized_monthly is None,
            item.canonical.price_normalized_monthly or Decimal("0"),
            item.created_at,
            item.announcement_id,
        )
    if criteria.sort == "price_desc":
        return lambda item: (
            item.canonical.price_normalized_monthly is not None,
            item.canonical.price_normalized_monthly or Decimal("0"),
            item.created_at,
            item.announcement_id,
        )
    return lambda item: (item.created_at, item.announcement_id)


def _metadata(
    criteria: SearchCriteria,
    *,
    returned: int,
    total: int | None = None,
) -> SearchMetadata:
    notes: list[str] = [
        "Search returns active canonical parent announcements only.",
        "Sale/one_time listings are excluded from monthly rent search.",
    ]
    if criteria.has_price_filter:
        notes.append("Listings with null price_normalized_monthly are excluded by price filters.")
    if criteria.include_per_person:
        notes.append(
            "Per-person prices are matched as per-person values; they are not converted to total."
        )
    return SearchMetadata(
        returned=returned,
        limit=criteria.limit,
        offset=criteria.offset,
        total=total,
        per_person_included=criteria.include_per_person,
        null_price_included=not criteria.has_price_filter,
        notes=tuple(notes),
    )
