from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from estateflow.services.source_parsing.contracts import ParseDecision, ParseOutcome
from estateflow.services.source_parsing.router import SourceParserRouter
from estateflow.services.telegram_listener import RawTelegramEvent


@dataclass(frozen=True)
class LabeledParseCase:
    case_id: str
    event: RawTelegramEvent
    expected_decision: ParseDecision
    expected_candidate_texts: tuple[str, ...] = ()
    expected_candidate_count: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.expected_candidate_count is not None and self.expected_candidate_count < 0:
            raise ValueError("expected_candidate_count cannot be negative")
        if (
            self.expected_candidate_count is not None
            and self.expected_candidate_texts
            and self.expected_candidate_count != len(self.expected_candidate_texts)
        ):
            raise ValueError("expected candidate count and labeled texts disagree")


@dataclass(frozen=True)
class ParseCaseEvaluation:
    case_id: str
    source_id: str
    expected_decision: ParseDecision
    actual_decision: ParseDecision
    expected_candidate_count: int
    actual_candidate_count: int
    true_positive_candidates: int
    false_positive_candidates: int
    false_negative_candidates: int
    candidate_content_labeled: bool
    decision_correct: bool
    outcome: ParseOutcome


@dataclass(frozen=True)
class SourceParserEvaluationReport:
    case_count: int
    expected_candidate_count: int
    actual_candidate_count: int
    true_positive_candidates: int
    false_positive_candidates: int
    false_negative_candidates: int
    labeled_candidate_case_count: int
    precision: float | None
    recall: float | None
    decision_accuracy: float | None
    drop_accuracy: float | None
    candidate_count_accuracy: float | None
    cases: tuple[ParseCaseEvaluation, ...]


class SourceParserEvaluator:
    """Pure replay evaluator for labeled source posts and parser recipes."""

    def __init__(self, router: SourceParserRouter) -> None:
        self._router = router

    def evaluate(
        self,
        cases: list[LabeledParseCase] | tuple[LabeledParseCase, ...],
    ) -> SourceParserEvaluationReport:
        evaluations = tuple(self.evaluate_case(case) for case in cases)
        true_positive = sum(item.true_positive_candidates for item in evaluations)
        false_positive = sum(item.false_positive_candidates for item in evaluations)
        false_negative = sum(item.false_negative_candidates for item in evaluations)
        expected_count = sum(item.expected_candidate_count for item in evaluations)
        actual_count = sum(item.actual_candidate_count for item in evaluations)
        negative_cases = [item for item in evaluations if item.expected_decision != "emit"]
        exact_count_cases = [
            item
            for item in evaluations
            if item.expected_candidate_count == item.actual_candidate_count
        ]
        return SourceParserEvaluationReport(
            case_count=len(evaluations),
            expected_candidate_count=expected_count,
            actual_candidate_count=actual_count,
            true_positive_candidates=true_positive,
            false_positive_candidates=false_positive,
            false_negative_candidates=false_negative,
            labeled_candidate_case_count=sum(
                1 for item in evaluations if item.candidate_content_labeled
            ),
            precision=_ratio(true_positive, true_positive + false_positive),
            recall=_ratio(true_positive, true_positive + false_negative),
            decision_accuracy=_ratio(
                sum(1 for item in evaluations if item.decision_correct),
                len(evaluations),
            ),
            drop_accuracy=_ratio(
                sum(1 for item in negative_cases if item.decision_correct),
                len(negative_cases),
            ),
            candidate_count_accuracy=_ratio(len(exact_count_cases), len(evaluations)),
            cases=evaluations,
        )

    def evaluate_case(self, case: LabeledParseCase) -> ParseCaseEvaluation:
        outcome = self._router.parse(case.event, payload=case.payload)
        actual_texts = tuple(
            _normalized_label(candidate.cleaned_text or "") for candidate in outcome.candidates
        )
        expected_texts = tuple(_normalized_label(text) for text in case.expected_candidate_texts)
        expected_count = (
            len(expected_texts)
            if expected_texts
            else (
                case.expected_candidate_count
                if case.expected_candidate_count is not None
                else (1 if case.expected_decision == "emit" else 0)
            )
        )
        candidate_content_labeled = bool(case.expected_candidate_texts)
        if candidate_content_labeled:
            actual_counter = Counter(actual_texts)
            expected_counter = Counter(expected_texts)
            true_positive = sum((actual_counter & expected_counter).values())
        else:
            # Candidate counts still participate in candidate_count_accuracy, but
            # they cannot prove content precision/recall without content labels.
            true_positive = 0
        false_positive = (
            max(0, len(actual_texts) - true_positive) if candidate_content_labeled else 0
        )
        false_negative = (
            max(0, expected_count - true_positive) if candidate_content_labeled else 0
        )
        return ParseCaseEvaluation(
            case_id=case.case_id,
            source_id=case.event.source_id,
            expected_decision=case.expected_decision,
            actual_decision=outcome.decision,
            expected_candidate_count=expected_count,
            actual_candidate_count=len(outcome.candidates),
            true_positive_candidates=true_positive,
            false_positive_candidates=false_positive,
            false_negative_candidates=false_negative,
            candidate_content_labeled=candidate_content_labeled,
            decision_correct=outcome.decision == case.expected_decision,
            outcome=outcome,
        )


def _normalized_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator
