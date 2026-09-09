from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from estateflow.services.source_config import parser_key_from_legacy_profile


@dataclass(frozen=True)
class TelegramSourceProfile:
    name: str
    description: str


class TelegramExtractionStrategy(Protocol):
    @property
    def profile_name(self) -> str: ...

    def extract(self, update: Any) -> Any: ...


@dataclass(frozen=True)
class PassthroughTelegramExtractionStrategy:
    profile_name: str = "default"

    def extract(self, update: Any) -> Any:
        return update


@dataclass(frozen=True)
class CaptionFirstTelegramExtractionStrategy:
    profile_name: str = "caption_first"

    def extract(self, update: Any) -> Any:
        text = update.text
        if text is None and update.media:
            text = ""
        return _replace_update(update, text=text)


@dataclass(frozen=True)
class AlbumTextMergeTelegramExtractionStrategy:
    profile_name: str = "album_text_merge"

    def extract(self, update: Any) -> Any:
        text = update.text
        if update.media_group_id and text is None:
            text = ""
        return _replace_update(update, text=text)


DEFAULT_TELEGRAM_SOURCE_PROFILES: dict[str, TelegramSourceProfile] = {
    "default": TelegramSourceProfile(
        name="default",
        description="Generic Telegram extraction profile.",
    ),
    "caption_first": TelegramSourceProfile(
        name="caption_first",
        description="Prefer caption text when the post contains media.",
    ),
    "album_text_merge": TelegramSourceProfile(
        name="album_text_merge",
        description="Merge album media with any accompanying text payload.",
    ),
}


def build_telegram_extraction_strategy(profile_name: str | None) -> TelegramExtractionStrategy:
    normalized = (profile_name or "default").strip() or "default"
    if normalized == "caption_first":
        return CaptionFirstTelegramExtractionStrategy()
    if normalized == "album_text_merge":
        return AlbumTextMergeTelegramExtractionStrategy()
    return PassthroughTelegramExtractionStrategy(
        profile_name=parser_key_from_legacy_profile(normalized)
    )


def _replace_update(update: Any, **changes: Any) -> Any:
    fields = {
        "update_type": update.update_type,
        "source_channel_id": update.source_channel_id,
        "source_message_id": update.source_message_id,
        "source_identifier": update.source_identifier,
        "occurred_at": update.occurred_at,
        "text": getattr(update, "text", None),
        "forward_metadata": getattr(update, "forward_metadata", None),
        "media": getattr(update, "media", []),
        "media_group_id": getattr(update, "media_group_id", None),
        "source_username": getattr(update, "source_username", None),
    }
    fields.update(changes)
    return type(update)(**fields)
