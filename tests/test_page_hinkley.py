"""Tests for the Page-Hinkley drift detector (ml.fraud_detector)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.fraud_detector import PageHinkleyDriftDetector, FraudDetector


def test_no_drift_under_stable_stream():
    ph = PageHinkleyDriftDetector(delta=0.005, lambda_threshold=0.05, min_samples=10)
    # Feed 100 stable samples
    for _ in range(100):
        score, direction = ph.update(0.5)
    assert direction == "none"
    assert score < 1.0


def test_detects_upward_drift():
    ph = PageHinkleyDriftDetector(delta=0.005, lambda_threshold=0.05, min_samples=10)
    # 50 stable samples, then 50 with upward drift
    for _ in range(50):
        ph.update(0.5)
    triggered = False
    for step in range(50):
        score, direction = ph.update(0.5 + 0.01 * step)
        if direction == "upward":
            triggered = True
            break
    assert triggered, "Page-Hinkley failed to detect upward drift"


def test_detects_downward_drift():
    ph = PageHinkleyDriftDetector(delta=0.005, lambda_threshold=0.05, min_samples=10)
    for _ in range(50):
        ph.update(0.8)
    triggered = False
    for step in range(50):
        score, direction = ph.update(0.8 - 0.01 * step)
        if direction == "downward":
            triggered = True
            break
    assert triggered, "Page-Hinkley failed to detect downward drift"


def test_reset_clears_state():
    ph = PageHinkleyDriftDetector()
    for _ in range(20):
        ph.update(0.5)
    ph.reset()
    score, direction = ph.update(0.5)
    assert direction == "none"
    assert score == 0.0


def test_minimum_samples_before_firing():
    ph = PageHinkleyDriftDetector(min_samples=20)
    # Even a huge spike should not fire before min_samples
    for i in range(19):
        score, direction = ph.update(0.5 + i * 0.1)
        assert direction == "none"
        assert score == 0.0


def test_fraud_detector_includes_drift_component():
    fd = FraudDetector()
    # Feed in a clean reading
    reading = {
        "speed": 60.0, "rpm": 2200, "fuel_rate": 6.0,
        "acceleration": 0.0, "co2": 110.0, "vsp": 10.0,
        "ces_score": 0.5,
    }
    result = fd.analyze(reading)
    assert "drift" in result["components"]
    assert "drift_direction" in result


def test_fraud_detector_weights_sum_to_one():
    # Default weights must sum to exactly 1.0
    fd = FraudDetector()
    total = (
        fd._physics_weight
        + fd._isolation_weight
        + fd._temporal_weight
        + fd._drift_weight
    )
    assert abs(total - 1.0) < 1e-6


def test_fraud_detector_rejects_bad_weights():
    import pytest
    with pytest.raises(ValueError):
        FraudDetector(physics_weight=0.5, isolation_weight=0.5,
                      temporal_weight=0.5, drift_weight=0.5)
