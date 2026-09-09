from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from estateflow.services.source_parsing.contracts import CleanupConfig, SplitConfig

_ZERO_WIDTH_PATTERN = re.compile("[\u200b\u200c\u200d\ufeff]")
_INLINE_WHITESPACE_PATTERN = re.compile(r"[^\S\r\n]+")
_BLANK_LINES_PATTERN = re.compile(r"\n\s*\n(?:\s*\n)+")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?998)?[\s()\-]*(?:\d[\s()\-]*){9,12}(?!\d)")
_PRICE_PATTERN = re.compile(
    r"(?i)(?:\b\d[\d\s.,]{1,12}\s*(?:usd|у\.?\s*е\.?|dollar|доллар|so['ʻ’]?m|сум)\b|[$€]\s*\d)"
)
_ROOM_PATTERN = re.compile(
    r"(?i)\b(?:[1-9]\d?\s*(?:xona(?:li)?|хон(?:а|али)?|комнат(?:а|ы|ная)?|room(?:s)?))\b"
)
_REAL_ESTATE_PATTERN = re.compile(
    r"(?i)\b(?:kvartira|квартир|xonadon|хонадон|hovli|ҳовли|uy|уй|ijara|ижара|"
    r"arenda|аренд|sotil|сотил|прода(?:м|ется)|novostroy|новостро)\w*\b"
)

DEFAULT_DIGEST_SPLIT_PATTERNS: tuple[str, ...] = (
    r"(?m)(?=^\s*#?\d{1,3}[.)]\s+)",
    r"(?m)^\s*[-=_━]{3,}\s*$",
)


@dataclass(frozen=True)
class CleanupResult:
    text: str
    removed_pattern_count: int
    changed: bool


@dataclass(frozen=True)
class SplitResult:
    segments: tuple[str, ...]
    applied_pattern_count: int
    discarded_short_count: int
    overflow: bool


def normalize_and_cleanup(text: str | None, config: CleanupConfig) -> CleanupResult:
    raw = text or ""
    cleaned = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    cleaned = unicodedata.normalize(config.unicode_form, cleaned)
    cleaned = _ZERO_WIDTH_PATTERN.sub("", cleaned)
    removed_count = 0
    for pattern in config.strip_patterns:
        cleaned, count = re.subn(
            pattern,
            "",
            cleaned,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        removed_count += count
    if config.collapse_inline_whitespace:
        cleaned = "\n".join(
            _INLINE_WHITESPACE_PATTERN.sub(" ", line) for line in cleaned.split("\n")
        )
    if config.collapse_blank_lines:
        cleaned = _BLANK_LINES_PATTERN.sub("\n\n", cleaned)
    if config.trim:
        cleaned = cleaned.strip()
    return CleanupResult(
        text=cleaned,
        removed_pattern_count=removed_count,
        changed=cleaned != raw,
    )


def split_listing_blocks(
    text: str,
    config: SplitConfig,
    *,
    use_digest_defaults: bool,
) -> SplitResult:
    patterns = config.patterns or (DEFAULT_DIGEST_SPLIT_PATTERNS if use_digest_defaults else ())
    chunks = [text]
    applied_patterns = 0
    for pattern in patterns:
        next_chunks: list[str] = []
        pattern_applied = False
        for chunk in chunks:
            parts = re.split(pattern, chunk, flags=re.IGNORECASE | re.MULTILINE)
            meaningful_parts = [part for part in parts if part.strip()]
            if len(meaningful_parts) > 1:
                pattern_applied = True
            next_chunks.extend(meaningful_parts)
        if pattern_applied:
            applied_patterns += 1
            chunks = next_chunks

    if patterns and applied_patterns == 0 and not config.keep_unsplit_if_no_match:
        chunks = []
    elif not chunks and config.keep_unsplit_if_no_match and text.strip():
        chunks = [text]
    discarded = sum(1 for chunk in chunks if len(chunk.strip()) < config.min_segment_chars)
    segments = tuple(
        chunk.strip() for chunk in chunks if len(chunk.strip()) >= config.min_segment_chars
    )
    return SplitResult(
        segments=segments[: config.max_candidates],
        applied_pattern_count=applied_patterns,
        discarded_short_count=discarded,
        overflow=len(segments) > config.max_candidates,
    )


def matched_patterns(text: str, patterns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        pattern
        for pattern in patterns
        if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) is not None
    )


def quality_signals(text: str, *, has_media: bool) -> tuple[str, ...]:
    signals: list[str] = []
    if text.strip():
        signals.append("has_text")
    if _PHONE_PATTERN.search(text):
        signals.append("has_phone")
    if _PRICE_PATTERN.search(text):
        signals.append("has_price")
    if _ROOM_PATTERN.search(text):
        signals.append("has_rooms")
    if _REAL_ESTATE_PATTERN.search(text):
        signals.append("has_real_estate_terms")
    if has_media:
        signals.append("has_media")
    return tuple(signals)


def transparent_candidate_score(
    *,
    signals: tuple[str, ...],
    include_match_count: int,
) -> float:
    score = 0.05
    weights = {
        "has_text": 0.20,
        "has_phone": 0.20,
        "has_price": 0.20,
        "has_rooms": 0.10,
        "has_real_estate_terms": 0.15,
        "has_media": 0.05,
    }
    score += sum(weights.get(signal, 0.0) for signal in signals)
    if include_match_count:
        score += min(0.20, include_match_count * 0.10)
    return min(1.0, round(score, 4))
