"""External source and service adapters."""

from estateflow.adapters.contracts import (
    AdapterLoadFailure,
    AdapterLoadResult,
    AdapterManifest,
    AdapterReloadReport,
    WebsiteDocumentModel,
    WebsiteScrapeResultModel,
)
from estateflow.adapters.registry import AdapterRegistry, LoadedAdapterPlugin

__all__ = [
    "AdapterLoadFailure",
    "AdapterLoadResult",
    "AdapterManifest",
    "AdapterRegistry",
    "AdapterReloadReport",
    "LoadedAdapterPlugin",
    "WebsiteDocumentModel",
    "WebsiteScrapeResultModel",
]
