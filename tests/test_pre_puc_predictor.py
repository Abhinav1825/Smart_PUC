"""Tests for the Pre-PUC failure predictor (ml/pre_puc_predictor.py)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ml.pre_puc_predictor import (
    PrePUCPredictor,
    _extract_features,
    _linear_slope,
    _safe_mean,
    _percentile,
)


# ─────────────────────────── Helpers ────────────────────────────────────

def test_safe_mean_empty():
    assert _safe_mean([]) == 0.0


def test_safe_mean_nonempty():
    assert _safe_mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_percentile_50th():
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)


def test_linear_slope_flat():
    assert _linear_slope([5.0, 5.0, 5.0, 5.0]) == pytest.approx(0.0)


def test_linear_slope_monotonic():
    slope = _linear_slope([1.0, 2.0, 3.0, 4.0, 5.0])
    assert slope == pytest.approx(1.0)


def test_linear_slope_decreasing():
    slope = _linear_slope([5.0, 4.0, 3.0, 2.0, 1.0])
    assert slope == pytest.approx(-1.0)


# ─────────────────────────── Feature extraction ─────────────────────────

def _make_records(n, ces_base, drift=0.0):
    records = []
    for i in range(n):
        ces = ces_base + drift * i
        records.append({
            "ces_score": ces,
            "co2": 120.0 * ces,
            "co": 1.0 * ces,
            "nox": 0.06 * ces,
            "hc": 0.10 * ces,
            "pm25": 0.0045 * ces,
        })
    return records


def test_extract_features_length():
    records = _make_records(10, 0.5)
    features = _extract_features(records)
    assert len(features) == 11  # matches module documentation


def test_extract_features_flat_ces_has_zero_slope():
    records = _make_records(10, 0.5, drift=0.0)
    features = _extract_features(records)
    slope_idx = 8
    assert features[slope_idx] == pytest.approx(0.0)


def test_extract_features_degrading_vehicle_has_positive_slope():
    records = _make_records(10, 0.5, drift=0.02)
    features = _extract_features(records)
    slope_idx = 8
    assert features[slope_idx] > 0


# ─────────────────────────── Predictor lifecycle ────────────────────────

@pytest.fixture(scope="module")
def trained_predictor():
    p = PrePUCPredictor(random_state=42)
    stats = p.train_synthetic(n_samples=800)
    assert stats["n_samples"] == 800
    return p


def test_predictor_rejects_prediction_before_training():
    fresh = PrePUCPredictor(random_state=0)
    with pytest.raises(RuntimeError):
        fresh.predict(_make_records(5, 0.5))


def test_synthetic_training_accuracy_reasonable(trained_predictor):
    # The problem is deliberately easy for the synthetic data, so we
    # expect quite high training-set accuracy.
    stats = trained_predictor.train_synthetic(n_samples=500)
    assert stats["accuracy"] >= 0.85
    assert stats["auc"] >= 0.85


def test_predict_insufficient_records_returns_low_confidence(trained_predictor):
    result = trained_predictor.predict(_make_records(2, 0.5))
    assert result["confidence"] == "low"
    assert result["will_fail"] is False
    assert result["dominant_pollutant"] is None


def test_predict_clean_vehicle_is_pass(trained_predictor):
    records = _make_records(10, ces_base=0.25, drift=0.0)
    result = trained_predictor.predict(records)
    assert result["will_fail"] is False
    assert 0.0 <= result["probability"] <= 0.6


def test_predict_degrading_vehicle_shows_risk(trained_predictor):
    records = _make_records(10, ces_base=0.80, drift=0.04)
    result = trained_predictor.predict(records)
    # Degrading vehicle should have at least elevated probability
    assert result["probability"] > 0.4
    assert result["dominant_pollutant"] is not None


def test_predict_outputs_recommended_action(trained_predictor):
    records = _make_records(10, ces_base=0.6)
    result = trained_predictor.predict(records)
    assert "recommended_action" in result
    assert isinstance(result["recommended_action"], str)
    assert len(result["recommended_action"]) > 0


def test_predict_result_keys(trained_predictor):
    records = _make_records(6, 0.5)
    result = trained_predictor.predict(records)
    for key in ("will_fail", "probability", "dominant_pollutant",
                "recommended_action", "confidence"):
        assert key in result
