from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from estateflow.services.extraction import CanonicalListing
from estateflow.services.post_ai_dedup import StructuredAnnouncement
from estateflow.services.telegram_listener import RawTelegramEvent, TelegramMediaReference

FROZEN_NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
TEST_SOURCE_ID = "source-fixture"
TEST_CHANNEL_ID = "-100777000"


@dataclass(frozen=True)
class TelegramPostFixture:
    fixture_id: str
    language: str
    text: str
    price_period: Literal["daily", "monthly", "one_time"]
    price_basis: Literal["total", "per_person"]
    expected_audience_tags: tuple[str, ...] = ()
    expected_audience_excluded_tags: tuple[str, ...] = ()
    media_count: int = 0
    duplicate_group: str | None = None

    def raw_event(self, *, message_id: str | None = None) -> RawTelegramEvent:
        resolved_message_id = message_id or self.fixture_id
        return RawTelegramEvent(
            schema_version="telegram.raw.v1",
            event_type="created",
            idempotency_key=f"telegram:{TEST_CHANNEL_ID}:{resolved_message_id}:created",
            account_key="listener-fixture-a",
            source_id=TEST_SOURCE_ID,
            source_channel_id=TEST_CHANNEL_ID,
            source_message_id=resolved_message_id,
            source_identifier="@estateflow_fixture",
            occurred_at=FROZEN_NOW,
            correlation_id=f"cid-{resolved_message_id}",
            text=self.text,
            forward_metadata=None,
            media=[
                TelegramMediaReference(
                    media_id=f"{resolved_message_id}-m{index}",
                    media_type="photo",
                    mime_type="image/jpeg",
                    size_bytes=2048,
                    access_hash=f"memory://{resolved_message_id}/image-{index}.jpg",
                )
                for index in range(self.media_count)
            ],
            source_message_ids=[resolved_message_id],
        )


REPRESENTATIVE_POSTS: tuple[TelegramPostFixture, ...] = (
    TelegramPostFixture(
        fixture_id="uz-monthly-family",
        language="uz",
        text=("Yunusobod 2 xona oila uchun ijaraga. Oyiga 500 USD. Rasmlar bor, telefon sintetik."),
        price_period="monthly",
        price_basis="total",
        expected_audience_tags=("family",),
        media_count=3,
    ),
    TelegramPostFixture(
        fixture_id="ru-daily",
        language="ru",
        text="Чиланзар, 1 комнатная, посуточно 35$, аккуратная квартира.",
        price_period="daily",
        price_basis="total",
        media_count=1,
    ),
    TelegramPostFixture(
        fixture_id="mixed-per-person-exclusion",
        language="mixed",
        text="Mirzo Ulugbek 3 xona, 4 ta qizga, 120$ с человека, bolali oilaga emas.",
        price_period="monthly",
        price_basis="per_person",
        expected_audience_tags=("group_of_girls",),
        expected_audience_excluded_tags=("family_with_children",),
        media_count=2,
    ),
    TelegramPostFixture(
        fixture_id="sale-one-time",
        language="uz",
        text="Sergeli 2 xona sotiladi. Narx 52000 USD bir martalik.",
        price_period="one_time",
        price_basis="total",
    ),
    TelegramPostFixture(
        fixture_id="exact-duplicate-a",
        language="uz",
        text="Olmazor 2 xona 450$ oyiga, oilaga. Synthetic duplicate sample.",
        price_period="monthly",
        price_basis="total",
        expected_audience_tags=("family",),
        duplicate_group="exact-duplicate",
    ),
    TelegramPostFixture(
        fixture_id="exact-duplicate-b",
        language="uz",
        text="Olmazor 2 xona 450$ oyiga, oilaga. Synthetic duplicate sample.",
        price_period="monthly",
        price_basis="total",
        expected_audience_tags=("family",),
        duplicate_group="exact-duplicate",
    ),
    TelegramPostFixture(
        fixture_id="ambiguous-duplicate",
        language="uz",
        text="Olmazor yaqinida 2 xona, 455$ atrofida. Makler raqami sintetik.",
        price_period="monthly",
        price_basis="total",
        duplicate_group="ambiguous",
    ),
)


def representative_posts() -> tuple[TelegramPostFixture, ...]:
    return REPRESENTATIVE_POSTS


def canonical_listing_fixture(
    *,
    announcement_id: str = "fixture-announcement",
    price: Decimal = Decimal("500"),
    price_period: Literal["daily", "monthly", "one_time"] = "monthly",
    price_basis: Literal["total", "per_person"] = "total",
    district: str = "Yunusobod",
    rooms: int = 2,
    audience_tags: tuple[str, ...] = (),
    audience_excluded_tags: tuple[str, ...] = (),
) -> StructuredAnnouncement:
    normalized_monthly = None if price_period == "one_time" else price
    if price_period == "daily":
        normalized_monthly = price * Decimal("30")
    listing = CanonicalListing(
        prompt_version="fixture",
        price=price,
        currency="USD",
        price_period=price_period,
        price_basis=price_basis,
        price_normalized_monthly=normalized_monthly,
        listing_type="sale" if price_period == "one_time" else "rent",
        rooms=rooms,
        area_sqm=None,
        floor=None,
        total_floors=None,
        district=district,
        address=None,
        phone_numbers=[],
        owner_type="unknown",
        renovation_level="good",
        renovation_source="vision",
        furniture=None,
        description=f"Synthetic {district} listing",
        audience_tags=list(audience_tags),
        audience_excluded_tags=list(audience_excluded_tags),
        confidence=0.9,
        field_confidence={},
    )
    return StructuredAnnouncement(
        announcement_id=announcement_id,
        idempotency_key=f"telegram:{TEST_CHANNEL_ID}:{announcement_id}:created",
        source_id=TEST_SOURCE_ID,
        source_channel_id=TEST_CHANNEL_ID,
        source_message_id=announcement_id,
        occurred_at=FROZEN_NOW,
        canonical=listing,
        parent_id=None,
        status="active",
        source_count=1,
        source_url=f"https://example.test/{announcement_id}",
        created_at=FROZEN_NOW,
        updated_at=FROZEN_NOW,
    )
