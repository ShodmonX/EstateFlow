from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from estateflow.api.app import create_app
from estateflow.application.core.config import Settings
from estateflow.services.extraction import (
    CanonicalListing,
    PriceBasis,
    PricePeriod,
    RenovationLevel,
)
from estateflow.services.post_ai_dedup import AnnouncementStatus, StructuredAnnouncement
from estateflow.services.search import InMemorySearchRepository, SearchCriteria, SearchService
from estateflow.services.telegram_webapp_auth import TelegramWebAppIdentity


def _canonical(
    *,
    price: Decimal | None = Decimal("500"),
    period: PricePeriod = "monthly",
    basis: PriceBasis = "total",
    district: str | None = "Yunusobod",
    rooms: int | None = 2,
    renovation: RenovationLevel | None = "good",
    audience_tags: list[str] | None = None,
    audience_excluded_tags: list[str] | None = None,
) -> CanonicalListing:
    return CanonicalListing(
        prompt_version="test",
        price=price,
        currency="USD",
        price_period=period,
        price_basis=basis,
        price_normalized_monthly=None
        if price is None or period == "one_time"
        else price * Decimal("30")
        if period == "daily"
        else price,
        listing_type="sale" if period == "one_time" else "rent",
        rooms=rooms,
        area_sqm=None,
        floor=None,
        total_floors=None,
        district=district,
        address=None,
        phone_numbers=[],
        owner_type="unknown",
        renovation_level=renovation,
        renovation_source="vision" if renovation else "unknown",
        furniture=None,
        description=f"{district or 'Unknown'} listing",
        audience_tags=audience_tags or [],
        audience_excluded_tags=audience_excluded_tags or [],
        confidence=0.9,
        field_confidence={},
    )


def _announcement(
    announcement_id: str,
    *,
    canonical: CanonicalListing | None = None,
    parent_id: str | None = None,
    status: AnnouncementStatus = "active",
    created_at: datetime | None = None,
) -> StructuredAnnouncement:
    created = created_at or datetime(2026, 8, 1, tzinfo=UTC)
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:-100:{announcement_id}:created",
        source_id="source-1",
        source_channel_id="-100",
        source_message_id=announcement_id,
        occurred_at=created,
        canonical=canonical or _canonical(),
        parent_id=parent_id,
        status=status,
        source_count=1,
        source_url=f"https://example.test/{announcement_id}",
        created_at=created,
        updated_at=created,
    )


@pytest.mark.asyncio
async def test_search_filters_canonical_monthly_price_and_price_basis_edges() -> None:
    repo = InMemorySearchRepository(
        [
            _announcement("monthly", canonical=_canonical(price=Decimal("500"))),
            _announcement("daily", canonical=_canonical(price=Decimal("20"), period="daily")),
            _announcement("sale", canonical=_canonical(price=Decimal("80000"), period="one_time")),
            _announcement("person", canonical=_canonical(price=Decimal("150"), basis="per_person")),
            _announcement("unknown-price", canonical=_canonical(price=None)),
            _announcement("child", parent_id="monthly"),
        ]
    )

    result = await repo.search(SearchCriteria(max_price=Decimal("650"), sort="cheapest"))

    assert [item.announcement_id for item in result.items] == ["monthly", "daily"]
    assert result.metadata.one_time_excluded is True
    assert result.metadata.per_person_included is False
    assert result.metadata.null_price_included is False

    with_per_person = await repo.search(
        SearchCriteria(max_price=Decimal("650"), include_per_person=True, sort="cheapest")
    )

    assert [item.announcement_id for item in with_per_person.items] == [
        "person",
        "monthly",
        "daily",
    ]


@pytest.mark.asyncio
async def test_search_uses_flat_audience_match_and_exclusion() -> None:
    repo = InMemorySearchRepository(
        [
            _announcement("accepted", canonical=_canonical(audience_tags=["family"])),
            _announcement(
                "excluded",
                canonical=_canonical(
                    audience_tags=["family"],
                    audience_excluded_tags=["family"],
                ),
            ),
            _announcement("implicit-is-not-expanded", canonical=_canonical(audience_tags=[])),
        ]
    )

    result = await repo.search(SearchCriteria(audience_tag="family"))

    assert [item.announcement_id for item in result.items] == ["accepted"]


@pytest.mark.asyncio
async def test_search_pagination_is_stable() -> None:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    repo = InMemorySearchRepository(
        [
            _announcement("older", created_at=base),
            _announcement("middle", created_at=base + timedelta(seconds=1)),
            _announcement("newer", created_at=base + timedelta(seconds=2)),
        ]
    )

    result = await repo.search(SearchCriteria(limit=1, offset=1))

    assert [item.announcement_id for item in result.items] == ["middle"]
    assert result.metadata.total == 3


@pytest.mark.asyncio
async def test_in_memory_search_repository_get_returns_user_facing_announcement() -> None:
    announcement = _announcement("detail")
    repo = InMemorySearchRepository([announcement])

    result = await repo.get("detail")

    assert result is announcement
    assert await repo.get("missing") is None


def test_search_api_contract_and_validation() -> None:
    app = create_app(Settings(environment="test"))
    app.state.search_service = SearchService(InMemorySearchRepository([_announcement("a1")]))

    response = TestClient(app).get(
        "/search/announcements",
        params={"district": "Yunusobod", "rooms": 2, "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["announcement_id"] == "a1"
    assert payload["metadata"]["price_field"] == "price_normalized_monthly"

    invalid = TestClient(app).get("/search/announcements", params={"limit": 500})
    assert invalid.status_code == 422


def test_webapp_announcement_detail_includes_source_link() -> None:
    app = create_app(Settings(environment="test"))
    app.state.search_service = SearchService(InMemorySearchRepository([_announcement("detail")]))
    session = app.state.webapp_auth_service.issue_session(TelegramWebAppIdentity(user_id=42))

    response = TestClient(app).get(
        "/webapp/announcements/detail",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["source_url"] == "https://example.test/detail"
