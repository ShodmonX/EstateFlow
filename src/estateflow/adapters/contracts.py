from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AdapterFamily = Literal["telegram", "website"]
AdapterLoadState = Literal["loaded", "failed"]


class AdapterManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    family: AdapterFamily
    version: str = Field(min_length=1, default="1.0.0")
    source_types: tuple[str, ...] = Field(default_factory=tuple)
    description: str = Field(default="")
    enabled: bool = True
    supports_hot_reload: bool = True


class AdapterLoadFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_name: str
    module_path: str
    error: str


class AdapterLoadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_name: str
    module_path: str
    state: AdapterLoadState
    manifest: AdapterManifest | None = None
    loaded_at: datetime | None = None
    error: str | None = None


class AdapterReloadReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_directory: str
    loaded_count: int
    failed_count: int
    loaded_plugins: list[AdapterLoadResult] = Field(default_factory=list)
    failures: list[AdapterLoadFailure] = Field(default_factory=list)


class WebsiteDocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str | None = None
    text: str | None = None
    canonical_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    links: tuple[str, ...] = ()
    media_urls: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebsiteScrapeResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_identifier: str
    adapter_name: str
    fetched_at: datetime
    documents: tuple[WebsiteDocumentModel, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)


class TelegramUpdateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    update_type: Literal["created", "edited", "deleted"]
    source_channel_id: str
    source_message_id: str
    source_identifier: str
    occurred_at: datetime
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def normalize_plugin_name(path: Path) -> str:
    return path.stem
