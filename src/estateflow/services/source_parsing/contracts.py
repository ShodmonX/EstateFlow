from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from estateflow.services.telegram_listener import RawTelegramEvent


ParserFamily = Literal[
    "single_listing",
    "digest_blocks",
    "album_caption",
    "mixed_feed",
    "buffered_thread",
    "custom",
]
ParserMode = Literal["active", "shadow", "disabled"]
ParseDecision = Literal["emit", "drop", "buffer", "quarantine"]
CandidatePolicy = Literal["emit", "drop", "quarantine"]
MediaPolicy = Literal["auto", "all", "first_candidate", "none"]
FANOUT_SOURCE_MESSAGE_MARKER = "::estateflow-candidate::"

_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_MAX_REGEX_LENGTH = 1_000
_MAX_REGEX_COUNT = 100
_MAX_REGEX_REPEAT = 10_000
_UNSAFE_LOOKAROUND_PREFIXES = ("(?=", "(?!", "(?<=", "(?<!")
_ALLOWED_REQUIRED_FIELDS = frozenset(
    {
        "address",
        "area_sqm",
        "description",
        "district",
        "phone_numbers",
        "price",
        "rooms",
    }
)


class CleanupConfig(BaseModel):
    """Deterministic, source-owned text cleanup rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strip_patterns: tuple[str, ...] = ()
    unicode_form: Literal["NFC", "NFKC"] = "NFKC"
    collapse_inline_whitespace: bool = True
    collapse_blank_lines: bool = True
    trim: bool = True

    @field_validator("strip_patterns")
    @classmethod
    def validate_strip_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_regexes(value, field_name="cleanup.strip_patterns")


class SplitConfig(BaseModel):
    """Rules used by digest parsers to turn one message into listing-sized blocks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    patterns: tuple[str, ...] = ()
    min_segment_chars: int = Field(default=1, ge=0, le=10_000)
    max_candidates: int = Field(default=25, ge=1, le=100)
    keep_unsplit_if_no_match: bool = True

    @field_validator("patterns")
    @classmethod
    def validate_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_regexes(value, field_name="split.patterns")


class FilterConfig(BaseModel):
    """Positive and negative source markers with explicit miss policies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    include_any: tuple[str, ...] = ()
    exclude_any: tuple[str, ...] = ()
    unmatched_policy: CandidatePolicy = "emit"
    excluded_policy: Literal["drop", "quarantine"] = "drop"
    empty_text_policy: CandidatePolicy = "quarantine"

    @field_validator("include_any", "exclude_any")
    @classmethod
    def validate_patterns(cls, value: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _validate_regexes(value, field_name=f"filters.{info.field_name}")


class ScoreConfig(BaseModel):
    """Transparent quality score policy; it never relies on model self-confidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: float = Field(default=0.0, ge=0.0, le=1.0)
    below_minimum_policy: CandidatePolicy = "quarantine"


class ParserRecipe(BaseModel):
    """Versioned declarative recipe for one source or a reusable source family."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    parser_key: str
    parser_version: str = "1"
    family: ParserFamily = "single_listing"
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    split: SplitConfig = Field(default_factory=SplitConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    media_policy: MediaPolicy = "auto"
    defaults: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    required_fields: tuple[str, ...] = ()
    prompt_hints: tuple[str, ...] = ()
    description: str = ""

    @field_validator("parser_key")
    @classmethod
    def validate_parser_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KEY_PATTERN.fullmatch(normalized):
            raise ValueError(
                "parser_key must start with a lowercase letter/digit and contain only "
                "lowercase letters, digits, dots, underscores, or hyphens"
            )
        return normalized

    @field_validator("parser_version")
    @classmethod
    def validate_parser_version(cls, value: str) -> str:
        normalized = str(value).strip()
        if not _VERSION_PATTERN.fullmatch(normalized):
            raise ValueError("parser_version contains unsupported characters")
        return normalized

    @field_validator("prompt_hints")
    @classmethod
    def validate_prompt_hints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 30:
            raise ValueError("prompt_hints may contain at most 30 entries")
        normalized = tuple(item.strip() for item in value if item.strip())
        if any(len(item) > 500 for item in normalized):
            raise ValueError("each prompt hint must be at most 500 characters")
        return normalized

    @field_validator("required_fields")
    @classmethod
    def validate_required_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        unsupported = sorted(set(normalized) - _ALLOWED_REQUIRED_FIELDS)
        if unsupported:
            raise ValueError(
                "unsupported required listing fields: " + ", ".join(unsupported)
            )
        return normalized

    @model_validator(mode="after")
    def validate_family(self) -> ParserRecipe:
        if self.family == "custom" and self.parser_key.startswith("generic."):
            raise ValueError("generic recipes cannot use the custom family")
        return self


@dataclass(frozen=True)
class ParsedCandidate:
    """One deterministic AI candidate derived from an immutable raw event."""

    candidate_id: str
    idempotency_key: str
    index: int
    cleaned_text: str | None
    raw_text: str | None
    score: float
    reasons: tuple[str, ...]
    signals: tuple[str, ...]
    parser_key: str
    parser_version: str
    parser_family: ParserFamily
    defaults: dict[str, str | int | float | bool | None] = field(default_factory=dict)
    required_fields: tuple[str, ...] = ()
    prompt_hints: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
    include_media: bool = True
    isolate_source_identity: bool = False

    def to_event(
        self,
        raw_event: RawTelegramEvent,
        *,
        force_source_identity_isolation: bool = False,
    ) -> RawTelegramEvent:
        """Build the event consumed by dedup/AI without mutating the raw event."""

        isolate_source_identity = (
            self.isolate_source_identity or force_source_identity_isolation
        )
        updates: dict[str, Any] = {
            "idempotency_key": self.idempotency_key,
            "text": self.cleaned_text,
        }
        candidate_source_message_id = raw_event.source_message_id
        if isolate_source_identity:
            candidate_source_message_id = fanout_candidate_source_message_id(
                raw_event.source_message_id,
                self.index,
            )
            updates["source_message_id"] = candidate_source_message_id
            # source_message_ids intentionally remain the original Telegram message
            # IDs. The synthetic primary identity exists only to let independent
            # listings from one Telegram post satisfy persistence uniqueness.
            updates["source_message_ids"] = list(raw_event.source_message_ids)
        if not self.include_media:
            updates["media"] = []
        field_names = getattr(raw_event, "__dataclass_fields__", {})
        if "parser_key" in field_names:
            updates["parser_key"] = self.parser_key
        if "parser_version" in field_names:
            updates["parser_version"] = self.parser_version
        if "parser_diagnostics" in field_names:
            updates["parser_diagnostics"] = {
                **dict(getattr(raw_event, "parser_diagnostics", {}) or {}),
                **self.diagnostics,
            }
        if "parser_provenance" in field_names:
            updates["parser_provenance"] = {
                **dict(getattr(raw_event, "parser_provenance", {}) or {}),
                "raw_event_id": raw_event.idempotency_key,
                "candidate_id": self.candidate_id,
                "candidate_index": self.index,
                "candidate_source_message_id": candidate_source_message_id,
                "raw_source_message_id": raw_event.source_message_id,
                "raw_source_message_ids": list(raw_event.source_message_ids),
                "raw_source_url": raw_event.source_url,
                "raw_forward_metadata": (
                    asdict(raw_event.forward_metadata)
                    if raw_event.forward_metadata is not None
                    else None
                ),
                "source_identity_isolated": isolate_source_identity,
                "source_identity_strategy": "raw_message_id+candidate_index",
                "source_identity_reorder_sensitive": isolate_source_identity,
            }
        return replace(raw_event, **updates)

    def to_queue_metadata(
        self,
        *,
        raw_event: RawTelegramEvent,
        candidate_count: int,
        decision: ParseDecision,
        mode: ParserMode,
        outcome_reasons: tuple[str, ...],
        outcome_diagnostics: dict[str, Any],
        shadow_outcome: ParseOutcome | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision": decision,
            "mode": mode,
            "parser_key": self.parser_key,
            "parser_version": self.parser_version,
            "parser_family": self.parser_family,
            "candidate_id": self.candidate_id,
            "candidate_index": self.index,
            "candidate_count": candidate_count,
            "score": self.score,
            "reasons": list(dict.fromkeys((*outcome_reasons, *self.reasons))),
            "signals": list(self.signals),
            "defaults": dict(self.defaults),
            "required_fields": list(self.required_fields),
            "prompt_hints": list(self.prompt_hints),
            "diagnostics": {**outcome_diagnostics, **self.diagnostics},
            "provenance": {
                "raw_event_id": raw_event.idempotency_key,
                "raw_schema_version": raw_event.schema_version,
                "raw_text": self.raw_text,
                "source_id": raw_event.source_id,
                "source_channel_id": raw_event.source_channel_id,
                "source_message_id": raw_event.source_message_id,
                "source_message_ids": list(raw_event.source_message_ids),
                "source_url": raw_event.source_url,
                "raw_forward_metadata": (
                    asdict(raw_event.forward_metadata)
                    if raw_event.forward_metadata is not None
                    else None
                ),
                "raw_media": [asdict(item) for item in raw_event.media],
            },
        }
        if shadow_outcome is not None:
            payload["shadow"] = shadow_outcome.to_audit_payload()
        return payload


@dataclass(frozen=True)
class QuarantinedCandidate:
    """A listing-sized segment retained for review instead of being discarded."""

    candidate_id: str
    index: int
    cleaned_text: str | None
    reason: str
    score: float

    def to_audit_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_index": self.index,
            "reason": self.reason,
            "score": self.score,
        }


@dataclass(frozen=True)
class ParseOutcome:
    decision: ParseDecision
    parser_key: str
    parser_version: str
    parser_family: ParserFamily
    mode: ParserMode = "active"
    candidates: tuple[ParsedCandidate, ...] = ()
    quarantined_candidates: tuple[QuarantinedCandidate, ...] = ()
    reasons: tuple[str, ...] = ()
    score: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision == "emit" and not self.candidates:
            raise ValueError("emit outcomes require at least one candidate")
        if self.decision != "emit" and self.candidates:
            raise ValueError("only emit outcomes may contain candidates")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("outcome score must be between 0 and 1")

    def to_audit_payload(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "parser_key": self.parser_key,
            "parser_version": self.parser_version,
            "parser_family": self.parser_family,
            "mode": self.mode,
            "candidate_count": len(self.candidates),
            "candidate_ids": [item.candidate_id for item in self.candidates],
            "quarantined_candidate_count": len(self.quarantined_candidates),
            "quarantined_candidate_ids": [
                item.candidate_id for item in self.quarantined_candidates
            ],
            "reasons": list(self.reasons),
            "score": self.score,
            "diagnostics": dict(self.diagnostics),
        }


class SourceParser(Protocol):
    def parse(self, event: RawTelegramEvent, recipe: ParserRecipe) -> ParseOutcome: ...


def stable_candidate_id(
    *,
    raw_event_id: str,
    parser_key: str,
    parser_version: str,
    candidate_index: int,
    cleaned_text: str | None,
) -> str:
    canonical = "\x1f".join(
        (
            raw_event_id,
            parser_key,
            parser_version,
            str(candidate_index),
            cleaned_text or "",
        )
    )
    return f"spc_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


def candidate_idempotency_key(
    *,
    raw_event_id: str,
    candidate_id: str,
    preserve_raw_id: bool,
) -> str:
    if preserve_raw_id:
        return raw_event_id
    return f"{raw_event_id}:candidate:{candidate_id}"


def fanout_candidate_source_message_id(
    raw_source_message_id: str,
    candidate_index: int,
) -> str:
    """Return the slot-stable persistence identity for one listing in a digest post."""

    if candidate_index < 0:
        raise ValueError("candidate_index must be non-negative")
    return f"{raw_source_message_id}{FANOUT_SOURCE_MESSAGE_MARKER}{candidate_index}"


def fanout_source_message_identity(value: str) -> tuple[str, int] | None:
    """Decode an EstateFlow fan-out identity without changing Telegram IDs."""

    raw_source_message_id, marker, candidate_index_text = value.rpartition(
        FANOUT_SOURCE_MESSAGE_MARKER
    )
    if (
        not marker
        or not raw_source_message_id
        or not candidate_index_text.isdecimal()
    ):
        return None
    return raw_source_message_id, int(candidate_index_text)


def are_fanout_source_message_siblings(left: str, right: str) -> bool:
    """Return whether two identities are distinct candidates of the same raw post."""

    left_identity = fanout_source_message_identity(left)
    right_identity = fanout_source_message_identity(right)
    return bool(
        left_identity
        and right_identity
        and left_identity[0] == right_identity[0]
        and left_identity[1] != right_identity[1]
    )


def _validate_regexes(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if len(value) > _MAX_REGEX_COUNT:
        raise ValueError(f"{field_name} may contain at most {_MAX_REGEX_COUNT} patterns")
    for pattern in value:
        if not pattern:
            raise ValueError(f"{field_name} cannot contain an empty pattern")
        if len(pattern) > _MAX_REGEX_LENGTH:
            raise ValueError(
                f"{field_name} patterns must be at most {_MAX_REGEX_LENGTH} characters"
            )
        try:
            re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            raise ValueError(f"invalid regex in {field_name}: {exc}") from exc
        unsafe_reason = _regex_safety_error(pattern)
        if unsafe_reason is not None:
            raise ValueError(f"unsafe regex in {field_name}: {unsafe_reason}")
    return value


def _regex_safety_error(pattern: str) -> str | None:
    """Reject configurable constructs that can cause unbounded backtracking.

    Source recipes are operator-controlled data, not trusted application code.  The
    standard ``re`` engine has no execution timeout, so validation intentionally
    accepts a conservative regular-expression subset.
    """

    # Each stack item tracks whether a group contains a repeat or alternation.
    groups: list[list[bool]] = [[False, False]]
    index = 0
    in_character_class = False
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            if index + 1 < len(pattern) and pattern[index + 1] in "123456789":
                return "backreferences are not allowed"
            index += 2
            continue
        if character == "[":
            in_character_class = True
            index += 1
            continue
        if character == "]" and in_character_class:
            in_character_class = False
            index += 1
            continue
        if in_character_class:
            index += 1
            continue
        if character == "(":
            if pattern.startswith(_UNSAFE_LOOKAROUND_PREFIXES, index) and not pattern.startswith(
                "(?=^", index
            ):
                return "lookaround assertions are not allowed"
            if pattern.startswith("(?P=", index):
                return "backreferences are not allowed"
            groups.append([False, False])
            index += 1
            continue
        if character == "|":
            groups[-1][1] = True
            index += 1
            continue
        if character == ")" and len(groups) > 1:
            contains_repeat, contains_alternation = groups.pop()
            repeated, unbounded, _, excessive = _regex_repeat_at(pattern, index + 1)
            if excessive:
                return f"repeat bounds may not exceed {_MAX_REGEX_REPEAT}"
            if repeated and unbounded and (contains_repeat or contains_alternation):
                return "nested or ambiguous unbounded repeats are not allowed"
            groups[-1][0] = groups[-1][0] or contains_repeat or repeated
            index += 1
            continue
        repeated, _, repeat_end, excessive = _regex_repeat_at(pattern, index)
        if excessive:
            return f"repeat bounds may not exceed {_MAX_REGEX_REPEAT}"
        if repeated:
            groups[-1][0] = True
            index = repeat_end
            continue
        index += 1
    return None


def _regex_repeat_at(pattern: str, index: int) -> tuple[bool, bool, int, bool]:
    if index >= len(pattern):
        return False, False, index, False
    character = pattern[index]
    if character in "*+":
        return True, True, index + 1, False
    if character == "?":
        # ``?`` immediately after ``(`` introduces a group extension such as
        # ``(?:...)`` or ``(?i:...)``; it is not a repeat.
        return (False, False, index + 1, False) if index and pattern[index - 1] == "(" else (
            True,
            False,
            index + 1,
            False,
        )
    if character != "{":
        return False, False, index, False
    closing = pattern.find("}", index + 1)
    if closing == -1:
        return False, False, index, False
    body = pattern[index + 1 : closing]
    if not re.fullmatch(r"\d+(?:,\d*)?", body):
        return False, False, index, False
    lower_text, separator, upper_text = body.partition(",")
    upper_bound = int(upper_text or lower_text) if not separator or upper_text else None
    if int(lower_text) > _MAX_REGEX_REPEAT or (
        upper_bound is not None and upper_bound > _MAX_REGEX_REPEAT
    ):
        return True, False, closing + 1, True
    return True, bool(separator and not upper_text), closing + 1, False
