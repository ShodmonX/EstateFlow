from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from estateflow.services.source_parsing import (  # noqa: E402
    LabeledParseCase,
    ParseDecision,
    SourceParserEvaluationReport,
    SourceParserEvaluator,
    SourceParserRouter,
)
from estateflow.services.telegram_listener import (  # noqa: E402
    RAW_EVENT_SCHEMA_VERSION,
    RawTelegramEvent,
)

DEFAULT_DATASET = PROJECT_ROOT / "tests" / "data" / "source_parser_replay.json"
METRIC_NAMES = (
    "precision",
    "recall",
    "decision_accuracy",
    "drop_accuracy",
    "candidate_count_accuracy",
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay labeled Telegram events through EstateFlow source parsers.",
    )
    parser.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"Labeled replay JSON (default: {DEFAULT_DATASET})",
    )
    parser.add_argument("--min-precision", type=_ratio_argument, default=0.95)
    parser.add_argument("--min-recall", type=_ratio_argument, default=0.95)
    parser.add_argument("--min-decision-accuracy", type=_ratio_argument, default=0.95)
    parser.add_argument("--min-drop-accuracy", type=_ratio_argument, default=0.95)
    parser.add_argument("--min-candidate-count-accuracy", type=_ratio_argument, default=0.95)
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Write compact JSON instead of indented JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        router, cases, dataset_name = load_replay_dataset(args.dataset)
        report = SourceParserEvaluator(router).evaluate(cases)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        error = {
            "passed": False,
            "error": "invalid_replay_dataset",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "dataset": str(args.dataset),
        }
        print(json.dumps(error, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2

    thresholds = {
        "precision": args.min_precision,
        "recall": args.min_recall,
        "decision_accuracy": args.min_decision_accuracy,
        "drop_accuracy": args.min_drop_accuracy,
        "candidate_count_accuracy": args.min_candidate_count_accuracy,
    }
    result = benchmark_result(
        report,
        dataset_name=dataset_name,
        dataset_path=args.dataset,
        thresholds=thresholds,
    )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0 if result["passed"] else 1


def load_replay_dataset(
    path: Path,
) -> tuple[SourceParserRouter, tuple[LabeledParseCase, ...], str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    root = _mapping(raw, field_name="dataset")
    recipes = _mapping_sequence(root.get("recipes", ()), field_name="recipes")
    source_recipes = _mapping(root.get("source_recipes", {}), field_name="source_recipes")
    raw_cases = _mapping_sequence(root.get("cases"), field_name="cases")
    if not raw_cases:
        raise ValueError("cases must contain at least one labeled replay case")
    router = SourceParserRouter(
        recipes=recipes,
        source_recipes={str(key): value for key, value in source_recipes.items()},
    )
    cases = tuple(_labeled_case(item, index=index) for index, item in enumerate(raw_cases))
    dataset_name = str(root.get("name") or path.stem)
    return router, cases, dataset_name


def benchmark_result(
    report: SourceParserEvaluationReport,
    *,
    dataset_name: str,
    dataset_path: Path,
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    metrics: dict[str, float | None] = {
        "precision": report.precision,
        "recall": report.recall,
        "decision_accuracy": report.decision_accuracy,
        "drop_accuracy": report.drop_accuracy,
        "candidate_count_accuracy": report.candidate_count_accuracy,
    }
    failures = [
        {
            "metric": metric_name,
            "actual": metrics[metric_name],
            "required": thresholds[metric_name],
        }
        for metric_name in METRIC_NAMES
        if metrics[metric_name] is None
        or cast(float, metrics[metric_name]) < thresholds[metric_name]
    ]
    return {
        "dataset": dataset_name,
        "dataset_path": str(dataset_path.resolve()),
        "passed": not failures,
        "metrics": metrics,
        "counts": {
            "cases": report.case_count,
            "expected_candidates": report.expected_candidate_count,
            "actual_candidates": report.actual_candidate_count,
            "true_positive_candidates": report.true_positive_candidates,
            "false_positive_candidates": report.false_positive_candidates,
            "false_negative_candidates": report.false_negative_candidates,
            "labeled_candidate_cases": report.labeled_candidate_case_count,
        },
        "thresholds": dict(thresholds),
        "failures": failures,
        "cases": [
            {
                "case_id": item.case_id,
                "source_id": item.source_id,
                "expected_decision": item.expected_decision,
                "actual_decision": item.actual_decision,
                "expected_candidate_count": item.expected_candidate_count,
                "actual_candidate_count": item.actual_candidate_count,
                "candidate_content_labeled": item.candidate_content_labeled,
                "decision_correct": item.decision_correct,
                "parser_key": item.outcome.parser_key,
                "parser_version": item.outcome.parser_version,
                "reasons": list(item.outcome.reasons),
            }
            for item in report.cases
        ],
    }


def _labeled_case(raw: Mapping[str, Any], *, index: int) -> LabeledParseCase:
    event_payload = _mapping(raw.get("event"), field_name=f"cases[{index}].event")
    label = _mapping(raw.get("expected"), field_name=f"cases[{index}].expected")
    payload = _mapping(raw.get("payload", {}), field_name=f"cases[{index}].payload")
    decision_value = str(label.get("decision") or "")
    if decision_value not in {"emit", "drop", "buffer", "quarantine"}:
        raise ValueError(f"cases[{index}].expected.decision is invalid")
    expected_texts_raw = label.get("candidate_texts", ())
    if not isinstance(expected_texts_raw, list | tuple) or not all(
        isinstance(item, str) for item in expected_texts_raw
    ):
        raise TypeError(f"cases[{index}].expected.candidate_texts must be a string array")
    expected_count_raw = label.get("candidate_count")
    if expected_count_raw is not None and (
        not isinstance(expected_count_raw, int) or isinstance(expected_count_raw, bool)
    ):
        raise TypeError(f"cases[{index}].expected.candidate_count must be an integer")
    return LabeledParseCase(
        case_id=str(raw.get("case_id") or f"case-{index + 1}"),
        event=_raw_event(event_payload, index=index),
        expected_decision=cast(ParseDecision, decision_value),
        expected_candidate_texts=tuple(cast(Sequence[str], expected_texts_raw)),
        expected_candidate_count=expected_count_raw,
        payload=dict(payload),
    )


def _raw_event(raw: Mapping[str, Any], *, index: int) -> RawTelegramEvent:
    message_id = str(raw.get("source_message_id") or index + 1)
    source_channel_id = str(raw.get("source_channel_id") or "-1000000000001")
    source_identifier = str(raw.get("source_identifier") or "@synthetic_parser_benchmark")
    occurred_at = _datetime(raw.get("occurred_at"), index=index)
    media = [_media_payload(item, index=index) for item in _mapping_sequence(raw.get("media", ()))]
    text_value = raw.get("text")
    if text_value is not None and not isinstance(text_value, str):
        raise TypeError(f"cases[{index}].event.text must be a string or null")
    event_type = str(raw.get("event_type") or "created")
    if event_type not in {"created", "edited", "deleted"}:
        raise ValueError(f"cases[{index}].event.event_type is invalid")
    source_id = str(raw.get("source_id") or "synthetic:source")
    idempotency_key = str(
        raw.get("idempotency_key")
        or f"telegram:{source_channel_id}:{message_id}:{event_type}"
    )
    parser_mode = raw.get("parser_mode", "active")
    if parser_mode not in {"active", "shadow", "disabled"}:
        raise ValueError(f"cases[{index}].event.parser_mode is invalid")
    source_message_ids = raw.get("source_message_ids", [message_id])
    if not isinstance(source_message_ids, list | tuple):
        raise TypeError(f"cases[{index}].event.source_message_ids must be an array")
    parser_key = _optional_string(raw.get("parser_key"), field_name="parser_key", index=index)
    parser_version = _optional_string(
        raw.get("parser_version"),
        field_name="parser_version",
        index=index,
    )
    queue_payload: dict[str, Any] = {
        "schema_version": str(raw.get("schema_version") or RAW_EVENT_SCHEMA_VERSION),
        "event_type": event_type,
        "idempotency_key": idempotency_key,
        "account_key": str(raw.get("account_key") or "synthetic-listener"),
        "source_id": source_id,
        "source_channel_id": source_channel_id,
        "source_message_id": message_id,
        "source_identifier": source_identifier,
        "occurred_at": occurred_at.isoformat(),
        "correlation_id": str(raw.get("correlation_id") or f"benchmark-{index + 1}"),
        "text": text_value,
        "forward_metadata": _optional_mapping(
            raw.get("forward_metadata"),
            field_name="forward_metadata",
            index=index,
        ),
        "media": media,
        "media_group_id": (
            str(raw["media_group_id"]) if raw.get("media_group_id") is not None else None
        ),
        "source_message_ids": [str(item) for item in source_message_ids],
        "source_url": str(raw["source_url"]) if raw.get("source_url") is not None else None,
        "parser_key": parser_key,
        "parser_version": parser_version,
        "parser_config": _optional_mapping(
            raw.get("parser_config"),
            field_name="parser_config",
            index=index,
        ),
        "parser_mode": parser_mode,
        "parser_diagnostics": _optional_mapping(
            raw.get("parser_diagnostics"),
            field_name="parser_diagnostics",
            index=index,
        ),
        "parser_provenance": _optional_mapping(
            raw.get("parser_provenance"),
            field_name="parser_provenance",
            index=index,
        ),
    }
    return RawTelegramEvent.from_queue_payload(queue_payload)


def _media_payload(raw: Mapping[str, Any], *, index: int) -> dict[str, object]:
    media_id = raw.get("media_id")
    media_type = raw.get("media_type")
    if not isinstance(media_id, str) or not media_id:
        raise ValueError(f"cases[{index}].event.media[].media_id is required")
    if not isinstance(media_type, str) or not media_type:
        raise ValueError(f"cases[{index}].event.media[].media_type is required")
    return {
        "media_id": media_id,
        "media_type": media_type,
        "mime_type": str(raw["mime_type"]) if raw.get("mime_type") is not None else None,
        "size_bytes": raw.get("size_bytes"),
        "access_hash": raw.get("access_hash"),
        "source_channel_id": raw.get("source_channel_id"),
        "source_message_id": raw.get("source_message_id"),
    }


def _datetime(value: object, *, index: int) -> datetime:
    if value is None:
        return datetime(2026, 8, 20, 12, index % 60, tzinfo=UTC)
    if not isinstance(value, str):
        raise TypeError(f"cases[{index}].event.occurred_at must be an ISO datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"cases[{index}].event.occurred_at must include a timezone")
    return parsed


def _mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return cast(Mapping[str, Any], value)


def _optional_mapping(
    value: object,
    *,
    field_name: str,
    index: int,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"cases[{index}].event.{field_name} must be an object")
    return dict(value)


def _optional_string(value: object, *, field_name: str, index: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"cases[{index}].event.{field_name} must be a string or null")
    return value


def _mapping_sequence(
    value: object,
    *,
    field_name: str = "value",
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list | tuple):
        raise TypeError(f"{field_name} must be an array")
    if not all(isinstance(item, Mapping) for item in value):
        raise TypeError(f"{field_name} entries must be objects")
    return tuple(cast(Mapping[str, Any], item) for item in value)


def _ratio_argument(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("metric thresholds must be between 0 and 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
