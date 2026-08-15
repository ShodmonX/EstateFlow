from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from estateflow.application.core.exceptions import EstateFlowError
from estateflow.services.search import SearchCriteria, SearchMetadata, SearchService

router = APIRouter(prefix="/search", tags=["search"])


class SearchItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    announcement_id: str
    price: Decimal | None
    currency: str | None
    price_period: str
    price_basis: str
    price_normalized_monthly: Decimal | None
    district: str | None
    rooms: int | None
    renovation_level: str | None
    description: str
    source_count: int
    source_url: str | None
    preview_url: str | None
    media_count: int


class SearchResponse(BaseModel):
    items: list[SearchItemResponse]
    metadata: SearchMetadata


@router.get("/announcements", response_model=SearchResponse)
async def search_announcements(
    request: Request,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    include_per_person: bool = False,
    district: str | None = None,
    rooms: Annotated[int | None, Query(ge=0)] = None,
    renovation_level: Literal["none", "basic", "good", "euro", "luxury"] | None = None,
    audience_tag: str | None = None,
    sort: Literal["newest", "cheapest", "price_desc"] = "newest",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    service: SearchService | None = getattr(request.app.state, "search_service", None)
    if service is None:
        raise EstateFlowError(
            code="search_unavailable",
            message="Search service is not configured.",
            status_code=503,
        )
    criteria = SearchCriteria(
        min_price=min_price,
        max_price=max_price,
        include_per_person=include_per_person,
        district=district,
        rooms=rooms,
        renovation_level=renovation_level,
        audience_tag=audience_tag,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    result = await service.search(criteria)
    return SearchResponse(
        items=[
            SearchItemResponse(
                announcement_id=item.announcement_id,
                price=item.canonical.price,
                currency=item.canonical.currency,
                price_period=item.canonical.price_period,
                price_basis=item.canonical.price_basis,
                price_normalized_monthly=item.canonical.price_normalized_monthly,
                district=item.canonical.district,
                rooms=item.canonical.rooms,
                renovation_level=item.canonical.renovation_level,
                description=_shorten(item.canonical.description),
                source_count=item.source_count,
                source_url=item.source_url,
                preview_url=item.media[0].storage_url if item.media else None,
                media_count=len(item.media),
            )
            for item in result.items
        ],
        metadata=result.metadata,
    )


def _shorten(value: str, *, limit: int = 240) -> str:
    stripped = " ".join(value.split())
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1].rstrip() + "..."
