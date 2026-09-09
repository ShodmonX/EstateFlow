from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic import ValidationError

from estateflow.services.source_parsing.contracts import (
    FilterConfig,
    ParsedCandidate,
    ParseOutcome,
    ParserMode,
    ParserRecipe,
    SourceParser,
    stable_candidate_id,
)
from estateflow.services.source_parsing.operators import quality_signals
from estateflow.services.source_parsing.parser import RecipeSourceParser
from estateflow.services.telegram_listener import RawTelegramEvent

_GENERIC_DIGEST_LISTING_MARKERS = (
    r"(?:\+?998[\s()\-]*)?(?:\d[\s()\-]*){9,12}",
    r"(?:\$\s*\d|\b\d[\d\s.,]{1,12}\s*(?:usd|dollar|so['ʻ’]?m)\b)",
    r"\b[1-9]\d?\s*(?:xona(?:li)?|room(?:s)?)\b",
    r"\b(?:kvartira|xonadon|hovli|uy|ijara|arenda|sotil)\w*\b",
)


def built_in_recipes() -> tuple[ParserRecipe, ...]:
    return (
        ParserRecipe(
            parser_key="generic.single_listing",
            family="single_listing",
            filters=FilterConfig(empty_text_policy="emit"),
            description="Safe one-message/one-candidate compatibility parser.",
        ),
        ParserRecipe(
            parser_key="generic.digest_blocks",
            family="digest_blocks",
            filters=FilterConfig(
                include_any=_GENERIC_DIGEST_LISTING_MARKERS,
                unmatched_policy="quarantine",
            ),
            description="Numbered or separator-delimited listing digest.",
        ),
        ParserRecipe(
            parser_key="generic.album_caption",
            family="album_caption",
            filters=FilterConfig(empty_text_policy="emit"),
            description="One album and its caption represent one listing.",
        ),
        ParserRecipe(
            parser_key="generic.mixed_feed",
            family="mixed_feed",
            filters=FilterConfig(
                include_any=(
                    r"\b(?:kvartira|квартир|xonadon|хонадон|hovli|ҳовли|uy|уй)\w*\b",
                    r"\b(?:ijara|ижара|arenda|аренд|sotil|сотил|прода(?:м|ется))\w*\b",
                ),
                exclude_any=(
                    r"\b(?:reklama|реклама|ish\s+qidir|работ[ау]|vakansi|ваканси)\w*\b",
                    r"\b(?:kredit|кредит|kurs|курс)\w*\b",
                ),
                unmatched_policy="quarantine",
                excluded_policy="drop",
            ),
            description="Conservative mixed channel parser with auditable filtering.",
        ),
    )


class SourceParserRouter:
    """Resolve a versioned parser by event metadata, source id, then generic fallback."""

    def __init__(
        self,
        *,
        recipes: Iterable[ParserRecipe | Mapping[str, Any]] = (),
        source_recipes: Mapping[str, str | ParserRecipe | Mapping[str, Any]] | None = None,
        plugins: Mapping[str, SourceParser] | None = None,
        fallback_key: str = "generic.single_listing",
    ) -> None:
        self._recipes: dict[tuple[str, str], ParserRecipe] = {}
        self._latest_recipe: dict[str, ParserRecipe] = {}
        for recipe in (*built_in_recipes(), *tuple(recipes)):
            self.register_recipe(recipe)
        if fallback_key not in self._latest_recipe:
            raise ValueError(f"unknown parser fallback_key: {fallback_key}")
        self._fallback_key = fallback_key
        self._source_recipes = dict(source_recipes or {})
        self._plugins = dict(plugins or {})
        self._recipe_parser = RecipeSourceParser()

    def register_recipe(self, recipe: ParserRecipe | Mapping[str, Any]) -> ParserRecipe:
        validated = (
            recipe if isinstance(recipe, ParserRecipe) else ParserRecipe.model_validate(recipe)
        )
        identity = (validated.parser_key, validated.parser_version)
        self._recipes[identity] = validated
        self._latest_recipe[validated.parser_key] = validated
        return validated

    def parse(
        self,
        event: RawTelegramEvent,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> ParseOutcome:
        metadata = self._metadata(event, payload or {})
        mode, mode_error = _parser_mode(metadata.get("parser_mode"))
        if mode_error is not None:
            return _routing_error(
                event,
                parser_key=_safe_metadata_text(metadata.get("parser_key"), "invalid.config"),
                parser_version=_safe_metadata_text(metadata.get("parser_version"), "unknown"),
                mode="active",
                reason=mode_error,
            )
        if mode == "disabled":
            return build_passthrough_outcome(
                event,
                parser_key=_safe_metadata_text(metadata.get("parser_key"), "parser.disabled"),
                parser_version=_safe_metadata_text(metadata.get("parser_version"), "1"),
                mode="disabled",
                reasons=("source_parser_disabled",),
            )

        try:
            recipe, route_reasons, route_diagnostics = self._resolve_recipe(event, metadata)
        except (TypeError, ValueError, ValidationError) as exc:
            return _routing_error(
                event,
                parser_key=_safe_metadata_text(metadata.get("parser_key"), "invalid.config"),
                parser_version=_safe_metadata_text(metadata.get("parser_version"), "unknown"),
                mode=mode,
                reason="invalid_parser_config",
                diagnostics={"error_type": type(exc).__name__},
            )

        parser = self._plugins.get(recipe.parser_key) if recipe.family == "custom" else None
        if recipe.family == "custom" and parser is None:
            outcome = _routing_error(
                event,
                parser_key=recipe.parser_key,
                parser_version=recipe.parser_version,
                mode=mode,
                reason="custom_parser_plugin_not_registered",
                family="custom",
            )
        else:
            try:
                outcome = (
                    parser.parse(event, recipe)
                    if parser is not None
                    else self._recipe_parser.parse(event, recipe)
                )
                if not isinstance(outcome, ParseOutcome):
                    raise TypeError("source parser plugin returned an invalid outcome")
            except Exception as exc:
                outcome = _routing_error(
                    event,
                    parser_key=recipe.parser_key,
                    parser_version=recipe.parser_version,
                    mode=mode,
                    reason="source_parser_failed",
                    family=recipe.family,
                    diagnostics={"error_type": type(exc).__name__},
                )
        return replace(
            outcome,
            parser_key=recipe.parser_key,
            parser_version=recipe.parser_version,
            parser_family=recipe.family,
            mode=mode,
            reasons=tuple(dict.fromkeys((*route_reasons, *outcome.reasons))),
            diagnostics={**route_diagnostics, **outcome.diagnostics},
        )

    def _resolve_recipe(
        self,
        event: RawTelegramEvent,
        metadata: Mapping[str, Any],
    ) -> tuple[ParserRecipe, tuple[str, ...], dict[str, Any]]:
        requested_key = _optional_text(metadata.get("parser_key"))
        requested_version = _optional_text(metadata.get("parser_version"))
        raw_config = metadata.get("parser_config")

        source_binding = self._source_recipes.get(event.source_id)
        if requested_key is None and source_binding is not None:
            if isinstance(source_binding, str):
                requested_key = source_binding
            elif isinstance(source_binding, ParserRecipe):
                return source_binding, ("source_id_recipe_match",), {"route": "source_id"}
            else:
                source_recipe = ParserRecipe.model_validate(source_binding)
                return source_recipe, ("source_id_recipe_match",), {"route": "source_id"}

        requested_key = _legacy_alias(requested_key)
        base_recipe = self._registered_recipe(requested_key, requested_version)
        if raw_config not in (None, {}):
            if not isinstance(raw_config, Mapping):
                raise TypeError("parser_config must be an object")
            config = _normalize_inline_config(dict(raw_config))
            if "recipe" in config:
                nested = config.pop("recipe")
                if not isinstance(nested, Mapping):
                    raise TypeError("parser_config.recipe must be an object")
                config = _deep_merge(dict(nested), config)
            hidden_identity_fields = sorted(
                field_name
                for field_name in ("parser_key", "parser_version")
                if field_name in config
            )
            if hidden_identity_fields:
                raise ValueError(
                    "parser_config cannot define binding identity fields: "
                    + ", ".join(hidden_identity_fields)
                )
            if base_recipe is not None:
                config = _deep_merge(base_recipe.model_dump(mode="python"), config)
            config["parser_key"] = requested_key or f"source.{_safe_source_key(event.source_id)}"
            config["parser_version"] = requested_version or (
                base_recipe.parser_version if base_recipe is not None else "1"
            )
            config.setdefault("family", "single_listing")
            recipe = ParserRecipe.model_validate(config)
            return recipe, ("inline_parser_config",), {"route": "event_config"}

        if base_recipe is not None:
            route = "event_key" if requested_key is not None else "source_id"
            return base_recipe, (f"{route}_recipe_match",), {"route": route}

        fallback = self._latest_recipe[self._fallback_key]
        if requested_key is not None:
            return (
                fallback,
                ("generic_fallback", "unknown_parser_key_or_version"),
                {
                    "route": "fallback",
                    "requested_parser_key": requested_key,
                    "requested_parser_version": requested_version,
                },
            )
        return fallback, ("generic_fallback",), {"route": "fallback"}

    def _registered_recipe(
        self,
        parser_key: str | None,
        parser_version: str | None,
    ) -> ParserRecipe | None:
        if parser_key is None:
            return None
        if parser_version is not None:
            return self._recipes.get((parser_key, parser_version))
        return self._latest_recipe.get(parser_key)

    @staticmethod
    def _metadata(event: RawTelegramEvent, payload: Mapping[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for field_name in ("parser_key", "parser_version", "parser_config", "parser_mode"):
            event_value = getattr(event, field_name, None)
            if event_value not in (None, {}, ""):
                metadata[field_name] = event_value
            payload_value = payload.get(field_name)
            if payload_value not in (None, {}, ""):
                metadata[field_name] = payload_value
        nested = payload.get("source_parser")
        if isinstance(nested, Mapping):
            for field_name in ("parser_key", "parser_version", "parser_config", "parser_mode"):
                if nested.get(field_name) not in (None, {}, ""):
                    metadata[field_name] = nested[field_name]
        return metadata


def validate_source_parser_binding(
    *,
    source_id: str,
    parser_key: str | None,
    parser_version: str | None,
    parser_config: Mapping[str, Any] | None,
    parser_mode: object = "active",
    router: SourceParserRouter | None = None,
) -> ParserRecipe:
    """Resolve and validate a binding exactly as the runtime router will.

    This is intended for configuration entry points (admin/bootstrap).  Runtime
    parsing remains fail-safe, but invalid active configuration should never be
    persisted in the first place.
    """

    _, mode_error = _parser_mode(parser_mode)
    if mode_error is not None:
        raise ValueError(mode_error)
    active_router = router or SourceParserRouter()
    event = cast(RawTelegramEvent, _BindingEvent(source_id=source_id))
    recipe, reasons, _ = active_router._resolve_recipe(
        event,
        {
            "parser_key": parser_key,
            "parser_version": parser_version,
            "parser_config": dict(parser_config or {}),
            # Validate the recipe even when the requested rollout mode is disabled.
            "parser_mode": "active",
        },
    )
    if "unknown_parser_key_or_version" in reasons:
        requested = parser_key or "<default>"
        version = parser_version or "<latest>"
        raise ValueError(f"unknown parser binding: {requested}@{version}")
    if recipe.family == "custom" and recipe.parser_key not in active_router._plugins:
        raise ValueError(f"custom parser plugin is not registered: {recipe.parser_key}")
    return recipe


@dataclass(frozen=True)
class _BindingEvent:
    source_id: str


def build_passthrough_outcome(
    event: RawTelegramEvent,
    *,
    parser_key: str,
    parser_version: str,
    mode: ParserMode,
    reasons: tuple[str, ...],
) -> ParseOutcome:
    candidate_id = stable_candidate_id(
        raw_event_id=event.idempotency_key,
        parser_key=parser_key,
        parser_version=parser_version,
        candidate_index=0,
        cleaned_text=event.text,
    )
    signals = quality_signals(event.text or "", has_media=bool(event.media))
    candidate = ParsedCandidate(
        candidate_id=candidate_id,
        idempotency_key=event.idempotency_key,
        index=0,
        cleaned_text=event.text,
        raw_text=event.text,
        score=0.0,
        reasons=reasons,
        signals=signals,
        parser_key=parser_key,
        parser_version=parser_version,
        parser_family="single_listing",
        diagnostics={"passthrough": True},
    )
    return ParseOutcome(
        decision="emit",
        parser_key=parser_key,
        parser_version=parser_version,
        parser_family="single_listing",
        mode=mode,
        candidates=(candidate,),
        reasons=reasons,
        diagnostics={"passthrough": True},
    )


def _routing_error(
    event: RawTelegramEvent,
    *,
    parser_key: str,
    parser_version: str,
    mode: ParserMode,
    reason: str,
    family: Any = "single_listing",
    diagnostics: dict[str, Any] | None = None,
) -> ParseOutcome:
    return ParseOutcome(
        decision="quarantine",
        parser_key=parser_key,
        parser_version=parser_version,
        parser_family=family,
        mode=mode,
        reasons=(reason,),
        diagnostics={
            "source_id": event.source_id,
            **dict(diagnostics or {}),
        },
    )


def _parser_mode(value: Any) -> tuple[ParserMode, str | None]:
    if value in (None, ""):
        return "active", None
    normalized = str(value).strip().lower()
    if normalized in {"active", "shadow", "disabled"}:
        return normalized, None  # type: ignore[return-value]
    return "active", "invalid_parser_mode"


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip()


def _safe_metadata_text(value: Any, default: str) -> str:
    normalized = _optional_text(value)
    return normalized[:128] if normalized else default


def _safe_source_key(value: str) -> str:
    safe = "".join(character if character.isalnum() else "-" for character in value.lower())
    return safe.strip("-")[:80] or "unknown"


def _legacy_alias(parser_key: str | None) -> str | None:
    aliases = {
        "passthrough": "generic.single_listing",
        "caption_first": "generic.album_caption",
        "album_text_merge": "generic.album_caption",
        "single_listing": "generic.single_listing",
        "digest_blocks": "generic.digest_blocks",
        "album_caption": "generic.album_caption",
        "mixed_feed": "generic.mixed_feed",
    }
    return aliases.get(parser_key or "", parser_key)


def _normalize_inline_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    cleanup = dict(normalized.get("cleanup") or {})
    split = dict(normalized.get("split") or {})
    filters = dict(normalized.get("filters") or {})
    for field_name in (
        "strip_patterns",
        "unicode_form",
        "collapse_inline_whitespace",
        "collapse_blank_lines",
        "trim",
    ):
        if field_name in normalized:
            cleanup[field_name] = normalized.pop(field_name)
    if "split_patterns" in normalized:
        split["patterns"] = normalized.pop("split_patterns")
    for field_name in ("min_segment_chars", "max_candidates", "keep_unsplit_if_no_match"):
        if field_name in normalized:
            split[field_name] = normalized.pop(field_name)
    for field_name in (
        "include_any",
        "exclude_any",
        "unmatched_policy",
        "excluded_policy",
        "empty_text_policy",
    ):
        if field_name in normalized:
            filters[field_name] = normalized.pop(field_name)
    if cleanup:
        normalized["cleanup"] = cleanup
    if split:
        normalized["split"] = split
    if filters:
        normalized["filters"] = filters
    normalized.pop("parser_mode", None)
    return normalized


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged
