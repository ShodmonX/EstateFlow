# ruff: noqa: E501
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from estateflow.adapters.registry import AdapterRegistry
from estateflow.services.source_config import SourceConfig, SourceConfigProvider


@dataclass(frozen=True)
class WebsiteScrapeDocument:
    url: str
    title: str | None = None
    text: str | None = None
    canonical_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    links: tuple[str, ...] = ()
    media_urls: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WebsiteScrapeResult:
    source_id: str
    source_identifier: str
    adapter_name: str
    fetched_at: datetime
    documents: tuple[WebsiteScrapeDocument, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


class WebsiteSourceAdapter(Protocol):
    async def connect(self) -> None: ...

    async def scrape(self, source: SourceConfig) -> WebsiteScrapeResult: ...

    async def disconnect(self) -> None: ...


class WebsiteAdapterRegistry(Protocol):
    def resolve(self, *, source: SourceConfig) -> WebsiteSourceAdapter: ...


class WebsiteScrapeRepository(Protocol):
    async def save(self, result: WebsiteScrapeResult) -> None: ...


class NoWebsiteAdapterError(RuntimeError):
    pass


class InMemoryWebsiteScrapeRepository:
    def __init__(self) -> None:
        self.results: list[WebsiteScrapeResult] = []

    async def save(self, result: WebsiteScrapeResult) -> None:
        self.results.append(result)


class InMemoryWebsiteAdapterRegistry:
    def __init__(
        self,
        *,
        default_adapter_name: str = "http_html",
        adapters: dict[str, WebsiteSourceAdapter] | None = None,
    ) -> None:
        self._default_adapter_name = default_adapter_name
        self._adapters = dict(adapters or {})

    def register(self, name: str, adapter: WebsiteSourceAdapter) -> None:
        self._adapters[name] = adapter

    def resolve(self, *, source: SourceConfig) -> WebsiteSourceAdapter:
        adapter_name = source.adapter_name or self._default_adapter_name
        adapter = self._adapters.get(adapter_name)
        if adapter is None:
            raise NoWebsiteAdapterError(f"No website adapter registered for {adapter_name!r}.")
        return adapter


class PluginWebsiteAdapterRegistry:
    def __init__(
        self,
        registry: AdapterRegistry,
        *,
        default_adapter_name: str = "http_html",
    ) -> None:
        self._registry = registry
        self._default_adapter_name = default_adapter_name

    def resolve(self, *, source: SourceConfig) -> WebsiteSourceAdapter:
        adapter_name = source.adapter_name or self._default_adapter_name
        loaded = self._registry.get(adapter_name)
        if loaded is None:
            raise NoWebsiteAdapterError(f"No website adapter registered for {adapter_name!r}.")
        adapter = loaded.plugin
        if not all(hasattr(adapter, attr) for attr in ("connect", "scrape", "disconnect")):
            raise TypeError(
                f"Plugin {adapter_name!r} does not implement the website adapter contract."
            )
        return adapter  # type: ignore[return-value]


class StaticWebsiteAdapter:
    def __init__(
        self,
        *,
        adapter_name: str = "http_html",
        result_factory: Callable[[SourceConfig], WebsiteScrapeResult] | None = None,
    ) -> None:
        self._adapter_name = adapter_name
        self._result_factory = result_factory or self._default_result_factory
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def scrape(self, source: SourceConfig) -> WebsiteScrapeResult:
        return self._result_factory(source)

    async def disconnect(self) -> None:
        self.connected = False

    def _default_result_factory(self, source: SourceConfig) -> WebsiteScrapeResult:
        now = datetime.now(UTC)
        return WebsiteScrapeResult(
            source_id=source.source_id,
            source_identifier=source.identifier,
            adapter_name=self._adapter_name,
            fetched_at=now,
            documents=(
                WebsiteScrapeDocument(
                    url=source.identifier,
                    title=source.name,
                    text=None,
                    canonical_url=source.identifier,
                    status_code=200,
                    content_type="text/html",
                ),
            ),
        )


class WebsiteScraperService:
    def __init__(
        self,
        *,
        source_provider: SourceConfigProvider,
        adapter_registry: WebsiteAdapterRegistry,
        result_repository: WebsiteScrapeRepository | None = None,
    ) -> None:
        self._source_provider = source_provider
        self._adapter_registry = adapter_registry
        self._result_repository = result_repository

    async def list_active_sources(self) -> list[SourceConfig]:
        sources = await self._source_provider.list_active_sources()
        return sorted(
            [source for source in sources if source.source_type == "website"],
            key=lambda source: source.source_id,
        )

    async def scrape_source(self, *, source: SourceConfig) -> WebsiteScrapeResult:
        if source.source_type != "website":
            raise ValueError("WebsiteScraperService only accepts website sources.")
        adapter = self._adapter_registry.resolve(source=source)
        await adapter.connect()
        try:
            result = await adapter.scrape(source)
        finally:
            await adapter.disconnect()
        if self._result_repository is not None:
            await self._result_repository.save(result)
        return result

    async def scrape_all_active(self) -> list[WebsiteScrapeResult]:
        results: list[WebsiteScrapeResult] = []
        for source in await self.list_active_sources():
            results.append(await self.scrape_source(source=source))
        return results


def normalize_website_identifier(identifier: str) -> str:
    raw = identifier.strip()
    if not raw:
        raise ValueError("Website identifier cannot be empty.")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Website sources must use http or https.")
    if not parsed.netloc:
        raise ValueError("Website sources must include a host.")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("Website sources must include a valid host.")
    netloc = hostname.casefold()
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, parsed.fragment))


def build_website_source_config(
    *,
    identifier: str,
    name: str,
    adapter_name: str | None = None,
) -> SourceConfig:
    normalized_identifier = normalize_website_identifier(identifier)
    return SourceConfig(
        source_id=f"website:{normalized_identifier}",
        name=name,
        source_type="website",
        identifier=normalized_identifier,
        enabled=True,
        adapter_name=adapter_name,
        listener_account_key=None,
    )


def clone_for_adapter(source: SourceConfig, *, adapter_name: str | None = None) -> SourceConfig:
    return replace(source, adapter_name=adapter_name or source.adapter_name)
