"""Tests for the opt-in per-VIN EWMA baseline (audit 13A #1).

Verifies:
    * Two VINs with different baselines do not interfere.
    * A >3σ outlier after ≥20 observations scores high.
    * ``save_state`` / ``load_state`` round-trips cleanly.
    * The ``FraudDetector.analyze`` bump is capped at +0.10.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.fraud_detector import (
    FraudDetector,
    FraudReasonCode,
    PerVINBaseline,
)


def _reading(co2: float, fuel: float, rpm: float, speed: float) -> dict:
    return {
        "co2": co2,
        "fuel_rate": fuel,
        "rpm": rpm,
        "speed": speed,
        "acceleration": 0.2,
    }


def test_two_vins_do_not_interfere():
    b = PerVINBaseline(lam=0.9, min_samples=5)
    # VIN A: clean family car profile
    for _ in range(10):
        b.update("VIN_A", _reading(110.0, 6.0, 2200.0, 55.0))
    # VIN B: heavy truck profile
    for _ in range(10):
        b.update("VIN_B", _reading(260.0, 18.0, 1800.0, 70.0))
    # A normal truck reading for VIN_B should NOT fire against VIN_A's
    # baseline, but VIN_A's profile fed through VIN_B should.
    z_b_truck = b.z_score("VIN_B", _reading(260.0, 18.0, 1800.0, 70.0))
    z_a_truck = b.z_score("VIN_A", _reading(260.0, 18.0, 1800.0, 70.0))
    assert z_b_truck < 1.0
    assert z_a_truck > z_b_truck


def test_outlier_after_twenty_observations_scores_high():
    b = PerVINBaseline(lam=0.95, min_samples=20)
    for _ in range(25):
        b.update("VIN_X", _reading(110.0, 6.0, 2200.0, 55.0))
    z = b.z_score("VIN_X", _reading(400.0, 30.0, 6500.0, 180.0))
    assert z > 3.0


def test_insufficient_history_returns_zero():
    b = PerVINBaseline(min_samples=20)
    for _ in range(5):
        b.update("VIN_Y", _reading(110.0, 6.0, 2200.0, 55.0))
    z = b.z_score("VIN_Y", _reading(400.0, 30.0, 6500.0, 180.0))
    assert z == 0.0


def test_save_load_state_round_trip():
    b = PerVINBaseline(lam=0.97, min_samples=5)
    for _ in range(10):
        b.update("VIN_Z", _reading(115.0, 6.5, 2400.0, 60.0))
    snap = b.save_state()

    b2 = PerVINBaseline()
    b2.load_state(snap)
    assert b2._lam == pytest.approx(0.97)
    assert b2._min_samples == 5
    # Same reading should yield the same z-score
    test_reading = _reading(115.0, 6.5, 2400.0, 60.0)
    assert b.z_score("VIN_Z", test_reading) == pytest.approx(
        b2.z_score("VIN_Z", test_reading), abs=1e-9
    )


def test_z_score_is_capped_at_five():
    b = PerVINBaseline(lam=0.9, min_samples=5)
    for _ in range(20):
        b.update("VIN_C", _reading(110.0, 6.0, 2200.0, 55.0))
    z = b.z_score("VIN_C", _reading(10000.0, 999.0, 15000.0, 500.0))
    assert z <= 5.0


# ─────────────── FraudDetector integration (feature flag) ───────────────


def test_analyze_without_flag_has_no_bump(monkeypatch):
    monkeypatch.delenv("PER_VIN_BASELINE_ENABLED", raising=False)
    det = FraudDetector()
    for _ in range(25):
        det.analyze(
            {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5,
             "co2": 115.0},
            vehicle_id="CAR_1",
        )
    # With the flag off the baseline state should still be empty
    assert det._per_vin_baseline._state == {}


def test_analyze_with_flag_bump_capped_at_010(monkeypatch):
    monkeypatch.setenv("PER_VIN_BASELINE_ENABLED", "1")
    det = FraudDetector()
    # Train a tight baseline
    for _ in range(25):
        det.analyze(
            {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5,
             "co2": 115.0},
            vehicle_id="CAR_2",
        )

    # Baseline result (clean reading, physics-clean):
    base = det.analyze(
        {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5,
         "co2": 115.0},
        vehicle_id="CAR_2",
    )

    # Strong outlier:
    outlier = det.analyze(
        {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5,
         "co2": 9999.0},
        vehicle_id="CAR_2",
    )

    # The difference is entirely attributable to the per-VIN bump; must
    # be <= 0.10 per the design contract.
    bump = outlier["fraud_score"] - base["fraud_score"]
    assert bump <= 0.10 + 1e-9
    assert outlier["per_vin_z_score"] > 3.0
    assert FraudReasonCode.PER_VIN_BASELINE_DRIFT.value in outlier["reason_codes"]


def test_weight_invariant_preserved():
    """The 4-way ensemble weights must still sum to 1.0 — per-VIN is
    additive, not a 5th weight (audit 13A #1)."""
    det = FraudDetector()
    total = (
        det._physics_weight
        + det._isolation_weight
        + det._temporal_weight
        + det._drift_weight
    )
    assert abs(total - 1.0) < 1e-9
