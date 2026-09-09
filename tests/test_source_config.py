from __future__ import annotations

import math

import pytest

from estateflow.services.source_config import InMemorySourceRegistry, SourceConfig


def _source(**overrides: object) -> SourceConfig:
    values: dict[str, object] = {
        "source_id": "telegram_channel:@source",
        "name": "Source",
        "source_type": "telegram_channel",
        "identifier": "@source",
    }
    values.update(overrides)
    return SourceConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("legacy_profile", "expected_key"),
    [
        (None, "generic.single_listing"),
        ("default", "generic.single_listing"),
        ("caption_first", "generic.album_caption"),
        ("album_text_merge", "generic.album_caption"),
        ("unregistered-old-profile", "generic.single_listing"),
    ],
)
def test_legacy_profiles_map_only_to_known_safe_parsers(
    legacy_profile: str | None,
    expected_key: str,
) -> None:
    source = _source(source_profile=legacy_profile)

    assert source.effective_parser_key == expected_key
    assert source.effective_parser_version == "1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"parser_key": "Contains Spaces"},
        {"parser_version": "version/1"},
        {"parser_mode": "observe"},
        {"parser_config": {"threshold": math.nan}},
        {"parser_config": {"unsupported": object()}},
    ],
)
def test_source_parser_policy_rejects_invalid_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _source(**overrides)


def test_source_parser_config_is_normalized_to_an_owned_json_copy() -> None:
    original = {"filters": {"include": ["xona"]}}

    source = _source(
        parser_key="Agency.Custom",
        parser_version="2026.08+1",
        parser_config=original,
        parser_mode="SHADOW",
    )
    original["filters"]["include"].append("hovli")

    assert source.parser_key == "agency.custom"
    assert source.parser_version == "2026.08+1"
    assert source.parser_mode == "shadow"
    assert source.parser_config == {"filters": {"include": ["xona"]}}


@pytest.mark.asyncio
async def test_registry_preserves_policy_when_legacy_admin_payload_omits_it() -> None:
    registry = InMemorySourceRegistry(
        [
            _source(
                parser_key="agency.custom",
                parser_version="3",
                parser_config={
                    "family": "single_listing",
                    "score": {"minimum": 0.8},
                },
                parser_mode="shadow",
            )
        ]
    )

    source, created = await registry.create_or_enable_source(
        source_type="telegram_channel",
        identifier="@source",
        name="Source renamed",
    )

    assert created is False
    assert source.parser_key == "agency.custom"
    assert source.parser_version == "3"
    assert source.parser_config == {
        "family": "single_listing",
        "score": {"minimum": 0.8},
    }
    assert source.parser_mode == "shadow"


@pytest.mark.asyncio
async def test_registry_key_only_update_drops_stale_version_and_recipe_config() -> None:
    registry = InMemorySourceRegistry(
        [
            _source(
                parser_key="agency.old",
                parser_version="9",
                parser_config={"family": "digest_blocks"},
                parser_mode="shadow",
            )
        ]
    )

    source, created = await registry.create_or_enable_source(
        source_type="telegram_channel",
        identifier="@source",
        name="Source",
        parser_key="generic.mixed_feed",
    )

    assert created is False
    assert source.parser_key == "generic.mixed_feed"
    assert source.parser_version is None
    assert source.effective_parser_version == "1"
    assert source.parser_config == {}
    assert source.parser_mode == "shadow"
