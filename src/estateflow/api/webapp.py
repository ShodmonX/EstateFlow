from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from estateflow.api.search import SearchItemResponse, SearchResponse
from estateflow.application.core.exceptions import EstateFlowError
from estateflow.services.saved_filters import SavedFilterService, UserFilter
from estateflow.services.search import SearchCriteria
from estateflow.services.telegram_webapp_auth import (
    TelegramWebAppAuthService,
    TelegramWebAppIdentity,
)

router = APIRouter(prefix="/webapp", tags=["webapp"])


class WebAppAuthRequest(BaseModel):
    init_data: str = Field(min_length=1)


class WebAppUserResponse(BaseModel):
    user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class WebAppAuthResponse(BaseModel):
    access_token: str
    expires_in: int
    user: WebAppUserResponse


class SavedFilterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    filter_id: str
    name: str
    criteria: SearchCriteria
    enabled: bool
    created_at: str
    updated_at: str


class SavedFilterCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    criteria: SearchCriteria


class SavedFilterUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    criteria: SearchCriteria | None = None
    enabled: bool | None = None


class AnnouncementMediaResponse(BaseModel):
    media_id: str
    storage_url: str
    mime_type: str
    width: int | None = None
    height: int | None = None


class AnnouncementDetailResponse(BaseModel):
    announcement_id: str
    price: Decimal | None
    currency: str | None
    price_period: str
    price_basis: str
    price_normalized_monthly: Decimal | None
    district: str | None
    address: str | None
    rooms: int | None
    area_sqm: Decimal | None
    floor: int | None
    total_floors: int | None
    renovation_level: str | None
    furniture: bool | None
    description: str
    phone_numbers: list[str]
    source_count: int
    source_url: str | None
    media: list[AnnouncementMediaResponse]


@router.post("/auth", response_model=WebAppAuthResponse)
async def authenticate_webapp(request: Request, body: WebAppAuthRequest) -> WebAppAuthResponse:
    service = _auth_service(request)
    try:
        identity = service.authenticate(body.init_data)
        session = service.issue_session(identity)
    except ValueError as exc:
        raise EstateFlowError(
            code="webapp_auth_failed",
            message=str(exc),
            status_code=401,
        ) from exc
    user_service = getattr(request.app.state, "user_service", None)
    if user_service is not None:
        await user_service.register_or_touch(telegram_user_id=identity.user_id)
    return WebAppAuthResponse(
        access_token=session.access_token,
        expires_in=session.expires_in,
        user=_user_response(identity),
    )


@router.get("/me", response_model=WebAppUserResponse)
async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> WebAppUserResponse:
    return _user_response(_require_user(request, authorization))


@router.get("/search/announcements", response_model=SearchResponse)
async def webapp_search(
    request: Request,
    authorization: str | None = Header(default=None),
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    include_per_person: bool = False,
    district: str | None = None,
    rooms: Annotated[int | None, Query(ge=0)] = None,
    renovation_level: Literal["none", "basic", "good", "euro", "luxury"] | None = None,
    audience_tag: str | None = None,
    sort: Literal["newest", "cheapest", "price_desc"] = "newest",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    _require_user(request, authorization)
    service = getattr(request.app.state, "search_service", None)
    if service is None:
        raise EstateFlowError(
            code="search_unavailable",
            message="Search service is not configured.",
            status_code=503,
        )
    try:
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
    except ValueError as exc:
        raise EstateFlowError(code="invalid_search", message=str(exc), status_code=422) from exc
    referral_service = getattr(request.app.state, "referral_service", None)
    if referral_service is not None:
        action_key = hashlib.sha256(criteria.model_dump_json().encode()).hexdigest()
        await referral_service.record_qualifying_action(
            user_id=_require_user(request, authorization).user_id,
            action="search",
            action_id=action_key,
        )
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
                description=" ".join(item.canonical.description.split())[:240],
                source_count=item.source_count,
                source_url=item.source_url,
                preview_url=item.media[0].storage_url if item.media else None,
                media_count=len(item.media),
            )
            for item in result.items
        ],
        metadata=result.metadata,
    )


@router.get("/announcements/{announcement_id}", response_model=AnnouncementDetailResponse)
async def webapp_announcement_detail(
    request: Request,
    announcement_id: str,
    authorization: str | None = Header(default=None),
) -> AnnouncementDetailResponse:
    _require_user(request, authorization)
    service = getattr(request.app.state, "search_service", None)
    if service is None:
        raise EstateFlowError(
            code="search_unavailable",
            message="Search service is not configured.",
            status_code=503,
        )
    item = await service.get(announcement_id)
    if item is None:
        raise EstateFlowError(
            code="announcement_not_found",
            message="Announcement not found.",
            status_code=404,
        )
    return AnnouncementDetailResponse(
        announcement_id=item.announcement_id,
        price=item.canonical.price,
        currency=item.canonical.currency,
        price_period=item.canonical.price_period,
        price_basis=item.canonical.price_basis,
        price_normalized_monthly=item.canonical.price_normalized_monthly,
        district=item.canonical.district,
        address=item.canonical.address,
        rooms=item.canonical.rooms,
        area_sqm=item.canonical.area_sqm,
        floor=item.canonical.floor,
        total_floors=item.canonical.total_floors,
        renovation_level=item.canonical.renovation_level,
        furniture=item.canonical.furniture,
        description=item.canonical.description,
        phone_numbers=item.canonical.phone_numbers,
        source_count=item.source_count,
        source_url=item.source_url,
        media=[
            AnnouncementMediaResponse(
                media_id=media.media_id,
                storage_url=media.storage_url,
                mime_type=media.mime_type,
                width=media.width,
                height=media.height,
            )
            for media in item.media
        ],
    )


@router.get("/filters", response_model=list[SavedFilterResponse])
async def list_filters(
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[SavedFilterResponse]:
    user = _require_user(request, authorization)
    service = _saved_filter_service(request)
    return [_filter_response(item) for item in await service.list_for_user(user_id=user.user_id)]


@router.post("/filters", response_model=SavedFilterResponse)
async def create_filter(
    request: Request,
    body: SavedFilterCreateRequest,
    authorization: str | None = Header(default=None),
) -> SavedFilterResponse:
    user = _require_user(request, authorization)
    service = _saved_filter_service(request)
    try:
        item = await service.create(user_id=user.user_id, name=body.name, criteria=body.criteria)
    except ValueError as exc:
        raise EstateFlowError(
            code="invalid_saved_filter",
            message=str(exc),
            status_code=422,
        ) from exc
    return _filter_response(item)


@router.patch("/filters/{filter_id}", response_model=SavedFilterResponse)
async def update_filter(
    request: Request,
    filter_id: str,
    body: SavedFilterUpdateRequest,
    authorization: str | None = Header(default=None),
) -> SavedFilterResponse:
    user = _require_user(request, authorization)
    service = _saved_filter_service(request)
    try:
        item = await service.update(
            user_id=user.user_id,
            filter_id=filter_id,
            name=body.name,
            criteria=body.criteria,
            enabled=body.enabled,
        )
    except KeyError as exc:
        raise EstateFlowError(
            code="saved_filter_not_found",
            message="Saved filter not found.",
            status_code=404,
        ) from exc
    except ValueError as exc:
        raise EstateFlowError(
            code="invalid_saved_filter",
            message=str(exc),
            status_code=422,
        ) from exc
    return _filter_response(item)


@router.delete("/filters/{filter_id}", status_code=204)
async def delete_filter(
    request: Request,
    filter_id: str,
    authorization: str | None = Header(default=None),
) -> None:
    user = _require_user(request, authorization)
    service = _saved_filter_service(request)
    if not await service.delete(user_id=user.user_id, filter_id=filter_id):
        raise EstateFlowError(
            code="saved_filter_not_found",
            message="Saved filter not found.",
            status_code=404,
        )


def _auth_service(request: Request) -> TelegramWebAppAuthService:
    service = getattr(request.app.state, "webapp_auth_service", None)
    if service is None:
        raise EstateFlowError(
            code="webapp_auth_unavailable",
            message="WebApp auth is not configured.",
            status_code=503,
        )
    return cast(TelegramWebAppAuthService, service)


def _require_user(request: Request, authorization: str | None) -> TelegramWebAppIdentity:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise EstateFlowError(
            code="webapp_unauthorized",
            message="WebApp authorization is required.",
            status_code=401,
        )
    try:
        return _auth_service(request).verify_session(authorization[7:].strip())
    except ValueError as exc:
        raise EstateFlowError(
            code="webapp_unauthorized",
            message=str(exc),
            status_code=401,
        ) from exc


def _saved_filter_service(request: Request) -> SavedFilterService:
    service = getattr(request.app.state, "saved_filter_service", None)
    if service is None:
        raise EstateFlowError(
            code="saved_filters_unavailable",
            message="Saved filters are not configured.",
            status_code=503,
        )
    return cast(SavedFilterService, service)


def _user_response(user: TelegramWebAppIdentity) -> WebAppUserResponse:
    return WebAppUserResponse(
        user_id=user.user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )


def _filter_response(item: UserFilter) -> SavedFilterResponse:
    return SavedFilterResponse(
        filter_id=item.filter_id,
        name=item.name,
        criteria=item.criteria,
        enabled=item.enabled,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )
