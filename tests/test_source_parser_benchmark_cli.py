from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "evaluate_source_parser.py"
DATASET = PROJECT_ROOT / "tests" / "data" / "source_parser_replay.json"


def test_source_parser_replay_cli_passes_strict_thresholds() -> None:
    result = _run_cli(
        DATASET,
        "--min-precision",
        "1",
        "--min-recall",
        "1",
        "--min-decision-accuracy",
        "1",
        "--min-drop-accuracy",
        "1",
        "--min-candidate-count-accuracy",
        "1",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["counts"] == {
        "actual_candidates": 9,
        "cases": 10,
        "expected_candidates": 9,
        "false_negative_candidates": 0,
        "false_positive_candidates": 0,
        "labeled_candidate_cases": 6,
        "true_positive_candidates": 9,
    }
    assert report["metrics"] == {
        "candidate_count_accuracy": 1.0,
        "decision_accuracy": 1.0,
        "drop_accuracy": 1.0,
        "precision": 1.0,
        "recall": 1.0,
    }
    event_binding = next(item for item in report["cases"] if item["case_id"] == "single-cleanup")
    assert event_binding["parser_key"] == "benchmark.single_cleanup"
    assert event_binding["parser_version"] == "1"


def test_source_parser_replay_cli_returns_nonzero_when_quality_gate_fails(
    tmp_path: Path,
) -> None:
    mutated = json.loads(DATASET.read_text(encoding="utf-8"))
    mutated["cases"][0]["expected"]["candidate_texts"] = [
        "Deliberately different labeled candidate"
    ]
    failing_dataset = tmp_path / "failing-source-parser-replay.json"
    failing_dataset.write_text(json.dumps(mutated), encoding="utf-8")

    result = _run_cli(failing_dataset)

    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is False
    assert report["metrics"]["precision"] == 8 / 9
    assert report["metrics"]["recall"] == 8 / 9
    assert {item["metric"] for item in report["failures"]} == {"precision", "recall"}


def _run_cli(dataset: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(dataset),
            "--compact",
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
