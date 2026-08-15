from __future__ import annotations

import os
from collections import Counter
from decimal import Decimal

from tests.conftest import FLOW_MARKERS_BY_FILE, PROJECT_ROOT
from tests.fixtures import TelegramPostFixture, canonical_listing_fixture


def test_representative_post_fixtures_cover_core_edge_cases(
    telegram_post_fixtures: tuple[TelegramPostFixture, ...],
) -> None:
    languages = {fixture.language for fixture in telegram_post_fixtures}
    price_periods = {fixture.price_period for fixture in telegram_post_fixtures}
    price_bases = {fixture.price_basis for fixture in telegram_post_fixtures}
    duplicate_groups = Counter(
        fixture.duplicate_group for fixture in telegram_post_fixtures if fixture.duplicate_group
    )
    excluded = [
        fixture for fixture in telegram_post_fixtures if fixture.expected_audience_excluded_tags
    ]
    album = [fixture for fixture in telegram_post_fixtures if fixture.media_count > 1]

    assert {"uz", "ru", "mixed"} <= languages
    assert {"daily", "monthly", "one_time"} <= price_periods
    assert {"total", "per_person"} <= price_bases
    assert duplicate_groups["exact-duplicate"] >= 2
    assert duplicate_groups["ambiguous"] >= 1
    assert excluded
    assert album


def test_fixture_events_are_synthetic_and_pii_safe(
    telegram_post_fixtures: tuple[TelegramPostFixture, ...],
) -> None:
    for fixture in telegram_post_fixtures:
        event = fixture.raw_event()
        text = event.text or ""
        assert event.source_identifier == "@estateflow_fixture"
        assert event.source_channel_id == "-100777000"
        assert "+998" not in text
        assert "private" not in text.casefold()
        assert all((media.access_hash or "").startswith("memory://") for media in event.media)


def test_canonical_fixture_factory_is_order_independent() -> None:
    first = canonical_listing_fixture(announcement_id="a")
    second = canonical_listing_fixture(announcement_id="b", price_period="daily")
    third = canonical_listing_fixture(announcement_id="c", price_period="one_time")

    assert first.announcement_id == "a"
    assert first.canonical.price is not None
    assert second.canonical.price is not None
    assert first.canonical.price_normalized_monthly == first.canonical.price
    assert second.canonical.price_normalized_monthly == second.canonical.price * Decimal("30")
    assert third.canonical.price_normalized_monthly is None
    assert first.created_at == second.created_at == third.created_at


def test_test_inventory_maps_every_test_file_to_layer_and_flow() -> None:
    test_files = {
        path.name
        for path in (PROJECT_ROOT / "tests").glob("test_*.py")
        if path.name != "test_sprint7_test_foundation.py"
    }

    missing = test_files - set(FLOW_MARKERS_BY_FILE)
    assert not missing
    for markers in FLOW_MARKERS_BY_FILE.values():
        assert {"unit", "integration", "e2e"} & set(markers)


def test_default_test_environment_strips_real_external_credentials() -> None:
    assert os.environ["ENVIRONMENT"] == "test"
    assert "TELEGRAM_BOT_TOKEN" not in os.environ
    assert "OPENROUTER_API_KEY" not in os.environ
    assert "R2_SECRET_ACCESS_KEY" not in os.environ


def test_migration_contract_mentions_all_migration_files() -> None:
    docs = (PROJECT_ROOT / "docs" / "sprint_7_test_matrix.md").read_text(encoding="utf-8")
    for migration in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        assert migration.name in docs
