from __future__ import annotations

import re

from estateflow.services.source_parsing.contracts import (
    CandidatePolicy,
    ParsedCandidate,
    ParseDecision,
    ParseOutcome,
    ParserRecipe,
    QuarantinedCandidate,
    candidate_idempotency_key,
    stable_candidate_id,
)
from estateflow.services.source_parsing.operators import (
    matched_patterns,
    normalize_and_cleanup,
    quality_signals,
    split_listing_blocks,
    transparent_candidate_score,
)
from estateflow.services.telegram_listener import RawTelegramEvent

_NUMBERED_DIGEST_SEGMENT = re.compile(r"^\s*#?\d{1,3}[.)]\s+")


class RecipeSourceParser:
    """Composable deterministic parser used by all declarative source recipes."""

    def parse(self, event: RawTelegramEvent, recipe: ParserRecipe) -> ParseOutcome:
        if recipe.family == "buffered_thread":
            return _empty_outcome(
                recipe,
                decision="buffer",
                reasons=("stateful_buffer_required", "buffer_not_configured"),
                diagnostics={"supported": False},
            )
        if recipe.family == "custom":
            return _empty_outcome(
                recipe,
                decision="quarantine",
                reasons=("custom_parser_plugin_required",),
            )

        cleanup = normalize_and_cleanup(event.text, recipe.cleanup)
        should_split = recipe.family == "digest_blocks" or bool(recipe.split.patterns)
        split = split_listing_blocks(
            cleanup.text,
            recipe.split,
            use_digest_defaults=recipe.family == "digest_blocks",
        )
        if split.overflow:
            return _empty_outcome(
                recipe,
                decision="quarantine",
                reasons=("candidate_limit_exceeded",),
                diagnostics={
                    "max_candidates": recipe.split.max_candidates,
                    "applied_split_pattern_count": split.applied_pattern_count,
                },
            )

        segments = split.segments if should_split else ((cleanup.text,) if cleanup.text else ())
        if not segments and event.media:
            segments = ("",)
        if not segments and recipe.filters.empty_text_policy == "emit":
            segments = ("",)
        if not segments:
            return _empty_outcome(
                recipe,
                decision=recipe.filters.empty_text_policy,
                reasons=("empty_text",),
                diagnostics={
                    "has_media": bool(event.media),
                    "cleanup_changed": cleanup.changed,
                    "applied_split_pattern_count": split.applied_pattern_count,
                    "discarded_short_segment_count": split.discarded_short_count,
                },
            )

        candidates: list[ParsedCandidate] = []
        quarantined_candidates: list[QuarantinedCandidate] = []
        rejected_reasons: list[str] = []
        rejected_decisions: list[ParseDecision] = []
        segment_count = len(segments)
        for index, segment in enumerate(segments):
            include_matches = matched_patterns(segment, recipe.filters.include_any)
            exclude_matches = matched_patterns(segment, recipe.filters.exclude_any)
            policy: CandidatePolicy
            policy_reason: str
            if _is_generic_digest_preamble(
                recipe=recipe,
                segments=segments,
                candidate_index=index,
            ):
                policy, policy_reason = "quarantine", "digest_preamble"
            else:
                policy, policy_reason = _candidate_policy(
                    segment=segment,
                    has_media=bool(event.media),
                    include_configured=bool(recipe.filters.include_any),
                    include_matches=include_matches,
                    exclude_matches=exclude_matches,
                    unmatched_policy=recipe.filters.unmatched_policy,
                    excluded_policy=recipe.filters.excluded_policy,
                    empty_text_policy=recipe.filters.empty_text_policy,
                )
            if policy != "emit":
                rejected_decisions.append(policy)
                rejected_reasons.append(policy_reason)
                if policy == "quarantine":
                    signals = quality_signals(segment, has_media=bool(event.media))
                    quarantined_candidates.append(
                        _quarantined_candidate(
                            event=event,
                            recipe=recipe,
                            index=index,
                            segment=segment,
                            reason=policy_reason,
                            score=transparent_candidate_score(
                                signals=signals,
                                include_match_count=len(include_matches),
                            ),
                        )
                    )
                continue

            signals = quality_signals(segment, has_media=bool(event.media))
            score = transparent_candidate_score(
                signals=signals,
                include_match_count=len(include_matches),
            )
            if score < recipe.score.minimum:
                below_policy = recipe.score.below_minimum_policy
                if below_policy != "emit":
                    rejected_decisions.append(below_policy)
                    rejected_reasons.append("below_minimum_parser_score")
                    if below_policy == "quarantine":
                        quarantined_candidates.append(
                            _quarantined_candidate(
                                event=event,
                                recipe=recipe,
                                index=index,
                                segment=segment,
                                reason="below_minimum_parser_score",
                                score=score,
                            )
                        )
                    continue

            cleaned_text: str | None = segment
            if not segment and event.text is None:
                cleaned_text = None
            include_media = _candidate_has_media(
                family=recipe.family,
                media_policy=recipe.media_policy,
                candidate_index=index,
                candidate_count=segment_count,
            )
            candidate_id = stable_candidate_id(
                raw_event_id=event.idempotency_key,
                parser_key=recipe.parser_key,
                parser_version=recipe.parser_version,
                candidate_index=index,
                cleaned_text=cleaned_text,
            )
            preserve_raw_id = (
                recipe.parser_key == "generic.single_listing"
                and segment_count == 1
                and not cleanup.changed
            )
            reasons = ["source_recipe_matched"]
            if cleanup.changed:
                reasons.append("text_normalized")
            if cleanup.removed_pattern_count:
                reasons.append("boilerplate_removed")
            if split.applied_pattern_count:
                reasons.append("digest_split")
            if include_matches:
                reasons.append("include_marker_matched")
            candidates.append(
                ParsedCandidate(
                    candidate_id=candidate_id,
                    idempotency_key=candidate_idempotency_key(
                        raw_event_id=event.idempotency_key,
                        candidate_id=candidate_id,
                        preserve_raw_id=preserve_raw_id,
                    ),
                    index=index,
                    cleaned_text=cleaned_text,
                    raw_text=event.text,
                    score=score,
                    reasons=tuple(reasons),
                    signals=signals,
                    parser_key=recipe.parser_key,
                    parser_version=recipe.parser_version,
                    parser_family=recipe.family,
                    defaults=dict(recipe.defaults),
                    required_fields=recipe.required_fields,
                    prompt_hints=recipe.prompt_hints,
                    diagnostics={
                        "segment_char_count": len(segment),
                        "include_match_count": len(include_matches),
                        "exclude_match_count": len(exclude_matches),
                        "media_attached": include_media,
                        "raw_media_count": len(event.media),
                        "source_identity_isolated": segment_count > 1,
                        "source_identity_strategy": "raw_message_id+candidate_index",
                        "source_identity_reorder_sensitive": segment_count > 1,
                    },
                    include_media=include_media,
                    isolate_source_identity=segment_count > 1,
                )
            )

        diagnostics = {
            "raw_char_count": len(event.text or ""),
            "cleaned_char_count": len(cleanup.text),
            "cleanup_changed": cleanup.changed,
            "removed_pattern_count": cleanup.removed_pattern_count,
            "segment_count": segment_count,
            "discarded_short_segment_count": split.discarded_short_count,
            "applied_split_pattern_count": split.applied_pattern_count,
            "rejected_candidate_count": len(rejected_decisions),
        }
        if candidates:
            return ParseOutcome(
                decision="emit",
                parser_key=recipe.parser_key,
                parser_version=recipe.parser_version,
                parser_family=recipe.family,
                candidates=tuple(candidates),
                quarantined_candidates=tuple(quarantined_candidates),
                reasons=tuple(dict.fromkeys(("candidates_emitted", *rejected_reasons))),
                score=min(candidate.score for candidate in candidates),
                diagnostics=diagnostics,
            )

        decision: ParseDecision = (
            "quarantine" if "quarantine" in rejected_decisions else "drop"
        )
        return _empty_outcome(
            recipe,
            decision=decision,
            reasons=tuple(dict.fromkeys(rejected_reasons or ["no_candidates"])),
            diagnostics=diagnostics,
            quarantined_candidates=tuple(quarantined_candidates),
        )


def _candidate_policy(
    *,
    segment: str,
    has_media: bool,
    include_configured: bool,
    include_matches: tuple[str, ...],
    exclude_matches: tuple[str, ...],
    unmatched_policy: CandidatePolicy,
    excluded_policy: str,
    empty_text_policy: CandidatePolicy,
) -> tuple[CandidatePolicy, str]:
    if exclude_matches:
        return (excluded_policy, "exclude_marker_matched")  # type: ignore[return-value]
    if not segment.strip() and not has_media:
        return empty_text_policy, "empty_text"
    if include_configured and not include_matches:
        return unmatched_policy, "include_marker_missing"
    return "emit", "candidate_accepted"


def _is_generic_digest_preamble(
    *,
    recipe: ParserRecipe,
    segments: tuple[str, ...],
    candidate_index: int,
) -> bool:
    if (
        recipe.parser_key != "generic.digest_blocks"
        or candidate_index != 0
        or len(segments) < 2
        or _NUMBERED_DIGEST_SEGMENT.match(segments[0])
    ):
        return False
    return any(_NUMBERED_DIGEST_SEGMENT.match(segment) for segment in segments[1:])


def _empty_outcome(
    recipe: ParserRecipe,
    *,
    decision: ParseDecision,
    reasons: tuple[str, ...],
    diagnostics: object | None = None,
    quarantined_candidates: tuple[QuarantinedCandidate, ...] = (),
) -> ParseOutcome:
    safe_diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    return ParseOutcome(
        decision=decision,
        parser_key=recipe.parser_key,
        parser_version=recipe.parser_version,
        parser_family=recipe.family,
        quarantined_candidates=quarantined_candidates,
        reasons=reasons,
        diagnostics=dict(safe_diagnostics),
    )


def _candidate_has_media(
    *,
    family: str,
    media_policy: str,
    candidate_index: int,
    candidate_count: int,
) -> bool:
    if media_policy == "all":
        return True
    if media_policy == "none":
        return False
    if media_policy == "first_candidate":
        return candidate_index == 0
    # Reusing the same album on every digest block would make sibling candidates
    # look like pHash duplicates. Keep it only when the mapping is unambiguous.
    return family != "digest_blocks" or candidate_count == 1


def _quarantined_candidate(
    *,
    event: RawTelegramEvent,
    recipe: ParserRecipe,
    index: int,
    segment: str,
    reason: str,
    score: float,
) -> QuarantinedCandidate:
    return QuarantinedCandidate(
        candidate_id=stable_candidate_id(
            raw_event_id=event.idempotency_key,
            parser_key=recipe.parser_key,
            parser_version=recipe.parser_version,
            candidate_index=index,
            cleaned_text=segment,
        ),
        index=index,
        cleaned_text=segment,
        reason=reason,
        score=score,
    )
