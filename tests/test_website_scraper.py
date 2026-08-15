# ruff: noqa: E501
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from estateflow.services.source_config import InMemorySourceRegistry
from estateflow.services.website_scraper import (
    InMemoryWebsiteAdapterRegistry,
    InMemoryWebsiteScrapeRepository,
    NoWebsiteAdapterError,
    StaticWebsiteAdapter,
    WebsiteScrapeDocument,
    WebsiteScrapeResult,
    WebsiteScraperService,
    build_website_source_config,
    normalize_website_identifier,
)


def test_normalize_website_identifier_handles_plain_domain_and_path() -> None:
    assert normalize_website_identifier("Example.COM/listings/") == "https://example.com/listings"
    assert normalize_website_identifier("https://example.com") == "https://example.com"


@pytest.mark.asyncio
async def test_website_scraper_uses_registered_adapter_and_persists_result() -> None:
    source_registry = InMemorySourceRegistry(
        [
            build_website_source_config(
                identifier="https://estate.example/listings",
                name="Estate Example",
                adapter_name="sitemap",
            ),
        ]
    )
    adapter_result = WebsiteScrapeResult(
        source_id="website:https://estate.example/listings",
        source_identifier="https://estate.example/listings",
        adapter_name="sitemap",
        fetched_at=datetime(2026, 8, 2, tzinfo=UTC),
        documents=(
            WebsiteScrapeDocument(
                url="https://estate.example/listings",
                title="Listings",
                text="2 rooms, Tashkent",
                canonical_url="https://estate.example/listings",
                status_code=200,
                content_type="text/html",
            ),
        ),
    )

    adapter = StaticWebsiteAdapter(
        adapter_name="sitemap",
        result_factory=lambda source: replace(
            adapter_result,
            source_id=source.source_id,
            source_identifier=source.identifier,
        ),
    )
    registry = InMemoryWebsiteAdapterRegistry(adapters={"sitemap": adapter})
    repository = InMemoryWebsiteScrapeRepository()
    service = WebsiteScraperService(
        source_provider=source_registry,
        adapter_registry=registry,
        result_repository=repository,
    )

    sources = await service.list_active_sources()
    assert len(sources) == 1
    result = await service.scrape_source(source=sources[0])

    assert result.adapter_name == "sitemap"
    assert result.documents[0].title == "Listings"
    assert len(repository.results) == 1
    assert repository.results[0].source_identifier == "https://estate.example/listings"


@pytest.mark.asyncio
async def test_website_scraper_requires_registered_adapter() -> None:
    source_registry = InMemorySourceRegistry(
        [
            build_website_source_config(
                identifier="https://estate.example/listings",
                name="Estate Example",
                adapter_name="playwright",
            ),
        ]
    )
    service = WebsiteScraperService(
        source_provider=source_registry,
        adapter_registry=InMemoryWebsiteAdapterRegistry(adapters={}),
    )

    with pytest.raises(NoWebsiteAdapterError):
        await service.scrape_all_active()
