"""Tests for PrePUCPredictor.explain() — audit 13B #3 (SHAP-lite for linear models)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from ml.pre_puc_predictor import PrePUCPredictor


def _vehicle_history(base_ces: float = 0.8, slope: float = 0.02) -> list[dict]:
    """10-record synthetic history for a slowly-degrading vehicle."""
    return [
        {
            "ces_score": base_ces + slope * i,
            "co2":  115.0 + i,
            "co":   0.85 + 0.01 * i,
            "nox":  0.052 + 0.001 * i,
            "hc":   0.09,
            "pm25": 0.004,
        }
        for i in range(10)
    ]


@pytest.fixture(scope="module")
def trained_predictor():
    p = PrePUCPredictor(random_state=42)
    p.train_synthetic(n_samples=2000)
    return p


def test_explain_returns_all_required_keys(trained_predictor):
    explanation = trained_predictor.explain(_vehicle_history())
    for key in (
        "probability",
        "will_fail",
        "base_value",
        "shap_values",
        "feature_names",
        "feature_values",
        "top_contributions",
        "method",
    ):
        assert key in explanation


def test_explain_top_contributions_are_sorted_by_absolute_value(trained_predictor):
    explanation = trained_predictor.explain(_vehicle_history(), top_k=5)
    top = explanation["top_contributions"]
    assert len(top) == 5
    magnitudes = [abs(entry["value"]) for entry in top]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_explain_feature_names_and_values_have_same_length(trained_predictor):
    explanation = trained_predictor.explain(_vehicle_history())
    names = explanation["feature_names"]
    values = explanation["feature_values"]
    shap_values = explanation["shap_values"]
    assert len(names) == len(values) == len(shap_values)
    assert len(names) == 11  # matches _FEATURE_NAMES in the predictor


def test_explain_matches_predict_probability(trained_predictor):
    records = _vehicle_history()
    pred = trained_predictor.predict(records)
    explanation = trained_predictor.explain(records)
    assert explanation["probability"] == pytest.approx(pred["probability"], rel=1e-6)


def test_explain_direction_labels_are_valid(trained_predictor):
    explanation = trained_predictor.explain(_vehicle_history())
    for entry in explanation["top_contributions"]:
        assert entry["direction"] in ("push_fail", "push_pass")


def test_explain_with_insufficient_history_returns_placeholder(trained_predictor):
    # Fewer than _MIN_RECORDS should return a sane placeholder, not raise.
    short = _vehicle_history()[:2]
    explanation = trained_predictor.explain(short)
    assert explanation["probability"] == 0.0
    assert explanation["top_contributions"] == []
    assert "note" in explanation
