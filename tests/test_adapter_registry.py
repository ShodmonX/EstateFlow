# ruff: noqa: E501
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from estateflow.adapters.registry import AdapterRegistry
from estateflow.application.core.config import Settings
from estateflow.services.source_config import InMemorySourceRegistry
from estateflow.services.website_scraper import (
    PluginWebsiteAdapterRegistry,
    WebsiteScraperService,
    build_website_source_config,
)


def test_adapter_registry_reload_keeps_previous_plugin_on_failure(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_path = plugin_dir / "demo_site.py"
    plugin_path.write_text(
        dedent(
            """
            from dataclasses import dataclass
            from datetime import UTC, datetime

            from estateflow.adapters.contracts import AdapterManifest, WebsiteDocumentModel, WebsiteScrapeResultModel
            from estateflow.services.source_config import SourceConfig

            MANIFEST = AdapterManifest(
                name="demo_site",
                family="website",
                version="1.0.0",
                source_types=("website",),
                description="Demo site adapter.",
            )

            @dataclass
            class DemoSiteAdapter:
                manifest = MANIFEST
                output_model = WebsiteScrapeResultModel

                async def connect(self) -> None:
                    return None

                async def scrape(self, source: SourceConfig) -> WebsiteScrapeResultModel:
                    return WebsiteScrapeResultModel(
                        source_id=source.source_id,
                        source_identifier=source.identifier,
                        adapter_name=self.manifest.name,
                        fetched_at=datetime(2026, 8, 2, tzinfo=UTC),
                        documents=(
                            WebsiteDocumentModel(
                                url=source.identifier,
                                title="Demo",
                                text="demo body",
                                canonical_url=source.identifier,
                                status_code=200,
                                content_type="text/html",
                            ),
                        ),
                    )

                async def disconnect(self) -> None:
                    return None

            def create_plugin(settings):
                return DemoSiteAdapter()
            """
        ),
        encoding="utf-8",
    )
    registry = AdapterRegistry(plugin_dir, Settings(environment="test"))

    initial = registry.reload_sync()
    assert initial.failed_count == 0
    assert registry.get("demo_site") is not None

    plugin_path.write_text(
        dedent(
            """
            from estateflow.adapters.contracts import AdapterManifest

            PLUGIN = AdapterManifest(
                name="demo_site",
                family="website",
                version="1.0.1",
                source_types=("website",),
                description="Broken demo site adapter.",
            )

            def create_plugin(settings):
                raise RuntimeError("boom")
            """
        ),
        encoding="utf-8",
    )

    second = registry.reload_sync()
    assert second.failed_count == 0
    assert registry.get("demo_site") is not None
    assert any(failure.plugin_name == "demo_site" for failure in second.failures)


@pytest.mark.asyncio
async def test_website_service_uses_plugin_registry(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "my_site.py").write_text(
        dedent(
            """
            from dataclasses import dataclass
            from datetime import UTC, datetime

            from estateflow.adapters.contracts import AdapterManifest, WebsiteDocumentModel, WebsiteScrapeResultModel
            from estateflow.services.source_config import SourceConfig

            MANIFEST = AdapterManifest(
                name="my_site",
                family="website",
                version="1.0.0",
                source_types=("website",),
                description="My site adapter.",
            )

            @dataclass
            class MySiteAdapter:
                manifest = MANIFEST
                output_model = WebsiteScrapeResultModel

                async def connect(self) -> None:
                    return None

                async def scrape(self, source: SourceConfig) -> WebsiteScrapeResultModel:
                    return WebsiteScrapeResultModel(
                        source_id=source.source_id,
                        source_identifier=source.identifier,
                        adapter_name=self.manifest.name,
                        fetched_at=datetime(2026, 8, 2, tzinfo=UTC),
                        documents=(
                            WebsiteDocumentModel(
                                url=source.identifier,
                                title="My Site",
                                text="fresh listing",
                                canonical_url=source.identifier,
                                status_code=200,
                                content_type="text/html",
                            ),
                        ),
                    )

                async def disconnect(self) -> None:
                    return None

            def create_plugin(settings):
                return MySiteAdapter()
            """
        ),
        encoding="utf-8",
    )
    registry = AdapterRegistry(plugin_dir, Settings(environment="test"))
    registry.reload_sync()
    source_registry = InMemorySourceRegistry(
        [
            build_website_source_config(
                identifier="https://example.test/listings",
                name="Example Site",
                adapter_name="my_site",
            )
        ]
    )
    service = WebsiteScraperService(
        source_provider=source_registry,
        adapter_registry=PluginWebsiteAdapterRegistry(registry),
    )

    result = await service.scrape_all_active()
    assert result


def test_telegram_existing_session_plugin_is_loadable() -> None:
    plugin_dir = Path("src/estateflow/adapters/plugins")
    registry = AdapterRegistry(plugin_dir, Settings(environment="test"))

    report = registry.reload_sync()

    assert any(item.plugin_name == "telegram_existing_session" for item in report.loaded_plugins)
    plugin = registry.get("telegram_existing_session")
    assert plugin is not None
    assert plugin.manifest.name == "telegram_existing_session"
    assert plugin.manifest.family == "telegram"
