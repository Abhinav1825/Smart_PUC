"""Smoke test for the adversarial red-team CLI (audit §13B N4).

Runs a tiny-budget search and asserts the output JSON is well-formed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.redteam import main as redteam_main
from ml.redteam import run_redteam


def test_run_redteam_emits_valid_json(tmp_path):
    out = tmp_path / "report.json"
    report = run_redteam(
        attack="smoke",
        iterations=5,
        target_recall=0.5,
        seed=123,
        output_path=out,
    )
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload == report
    # Schema checks
    for key in (
        "attack_type",
        "iterations",
        "worst_case_score",
        "worst_case_reading",
        "detector_still_fires",
    ):
        assert key in payload
    assert 0.0 <= payload["worst_case_score"] <= 1.0
    assert isinstance(payload["worst_case_reading"], dict)
    assert isinstance(payload["detector_still_fires"], bool)


def test_cli_main_exits_zero(tmp_path, capsys):
    out = tmp_path / "cli_report.json"
    exit_code = redteam_main(
        [
            "--attack", "smoke",
            "--iterations", "3",
            "--seed", "7",
            "--target_recall", "0.4",
            "--output", str(out),
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[redteam]" in captured.out
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["iterations"] == 3
    assert payload["attack_type"] == "smoke"
