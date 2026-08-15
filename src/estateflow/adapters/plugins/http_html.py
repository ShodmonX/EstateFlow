# ruff: noqa: E501,I001,F401
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser

import aiohttp

from estateflow.adapters.contracts import (
    AdapterManifest,
    WebsiteDocumentModel,
    WebsiteScrapeResultModel,
)
from estateflow.application.core.config import Settings
from estateflow.services.source_config import SourceConfig

PLUGIN_MANIFEST = AdapterManifest(
    name="http_html",
    family="website",
    version="1.0.0",
    source_types=("website",),
    description="Fetches public HTML pages and extracts a lightweight summary.",
)


@dataclass
class HttpHtmlWebsiteAdapter:
    manifest: AdapterManifest = field(default_factory=lambda: PLUGIN_MANIFEST)
    output_model: type[WebsiteScrapeResultModel] = WebsiteScrapeResultModel
    _timeout_seconds: float = 15.0

    async def connect(self) -> None:
        return None

    async def scrape(self, source: SourceConfig) -> WebsiteScrapeResultModel:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                source.identifier,
                headers={"User-Agent": "EstateFlowBot/1.0"},
            ) as response:
                html = await response.text()
                parser = _LightweightHtmlParser()
                parser.feed(html)
                doc = WebsiteDocumentModel(
                    url=str(response.url),
                    title=parser.title or source.name,
                    text=_clean_text(parser.text or html),
                    canonical_url=str(response.url),
                    status_code=response.status,
                    content_type=response.headers.get("content-type"),
                    metadata={
                        "source_name": source.name,
                        "adapter": self.manifest.name,
                    },
                )
                return WebsiteScrapeResultModel(
                    source_id=source.source_id,
                    source_identifier=source.identifier,
                    adapter_name=self.manifest.name,
                    fetched_at=datetime.now(UTC),
                    documents=(doc,),
                    metadata={"final_url": str(response.url)},
                )

    async def disconnect(self) -> None:
        return None


def create_plugin(settings: Settings) -> HttpHtmlWebsiteAdapter:
    timeout = getattr(settings, "website_adapter_timeout_seconds", None)
    return HttpHtmlWebsiteAdapter(_timeout_seconds=float(timeout or 15.0))


class _LightweightHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.text_parts: list[str] = []
        self._in_title = False

    @property
    def text(self) -> str:
        return " ".join(part for part in self.text_parts if part).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._in_title and self.title is None:
            self.title = text
        self.text_parts.append(text)


def _clean_text(value: str) -> str:
    return " ".join(value.split())
