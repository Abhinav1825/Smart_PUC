"""Tests for the fraud-detector reason-code framework (audit §12C, 13A #2).

Every physics rule, temporal rule, and drift direction should surface a
machine-readable :class:`FraudReasonCode` value on the ``analyze()``
result. Clean readings should return ``["NONE"]``.
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
    PhysicsConstraintValidator,
    TemporalConsistencyChecker,
)


# ─────────────────────────── Physics rule codes ────────────────────────────


@pytest.fixture
def physics():
    return PhysicsConstraintValidator()


def test_physics_rpm_zero_speed_nonzero(physics):
    score, _, codes = physics.validate(
        {"speed": 60.0, "rpm": 0, "fuel_rate": 7.0, "acceleration": 0.0}
    )
    assert FraudReasonCode.PHYSICS_RPM_ZERO_SPEED_NONZERO.value in codes


def test_physics_vsp_fuel_mismatch(physics):
    score, _, codes = physics.validate(
        {"speed": 60.0, "rpm": 3000, "fuel_rate": 0.1, "vsp": 15.0,
         "acceleration": 0.0}
    )
    assert FraudReasonCode.PHYSICS_VSP_FUEL_MISMATCH.value in codes


def test_physics_rpm_speed_bounds(physics):
    # Speed 50 km/h, RPM 500 → below 50*15=750 bound
    score, _, codes = physics.validate(
        {"speed": 50.0, "rpm": 500, "fuel_rate": 5.0, "acceleration": 0.0}
    )
    assert FraudReasonCode.PHYSICS_RPM_SPEED_BOUNDS_VIOLATION.value in codes


def test_physics_accel_cap_exceeded(physics):
    score, _, codes = physics.validate(
        {"speed": 60.0, "rpm": 3000, "fuel_rate": 7.0, "acceleration": 6.0}
    )
    assert FraudReasonCode.PHYSICS_ACCEL_CAP_EXCEEDED.value in codes


def test_physics_fuel_negative(physics):
    score, _, codes = physics.validate(
        {"speed": 30.0, "rpm": 1500, "fuel_rate": -1.0, "acceleration": 0.0}
    )
    assert FraudReasonCode.PHYSICS_FUEL_NEGATIVE.value in codes


def test_physics_rpm_redline(physics):
    score, _, codes = physics.validate(
        {"speed": 120.0, "rpm": 9000, "fuel_rate": 12.0, "acceleration": 0.0}
    )
    assert FraudReasonCode.PHYSICS_RPM_REDLINE.value in codes


def test_physics_speed_max(physics):
    score, _, codes = physics.validate(
        {"speed": 300.0, "rpm": 6000, "fuel_rate": 15.0, "acceleration": 0.0}
    )
    assert FraudReasonCode.PHYSICS_SPEED_MAX.value in codes


def test_physics_clean_reading_no_codes(physics):
    score, _, codes = physics.validate(
        {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5}
    )
    assert codes == []


# ─────────────────────────── Temporal codes ────────────────────────────────


def test_temporal_speed_jump_code():
    ch = TemporalConsistencyChecker()
    ch.update_and_check({"speed": 10.0, "rpm": 1000, "fuel_rate": 5.0, "timestamp": 0})
    ch.update_and_check({"speed": 120.0, "rpm": 4000, "fuel_rate": 9.0, "timestamp": 1})
    assert FraudReasonCode.TEMPORAL_SPEED_JUMP.value in ch._last_reason_codes


def test_temporal_replay_streak_code():
    ch = TemporalConsistencyChecker()
    reading = {"speed": 40.0, "rpm": 2000, "fuel_rate": 5.0, "timestamp": 0}
    for i in range(5):
        r = dict(reading)
        r["timestamp"] = i
        ch.update_and_check(r)
    assert FraudReasonCode.TEMPORAL_REPLAY_STREAK.value in ch._last_reason_codes


# ─────────────────────────── End-to-end analyze() ──────────────────────────


def test_analyze_clean_reading_returns_none_code():
    det = FraudDetector()
    result = det.analyze(
        {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5,
         "co2": 115.0, "vsp": 3.0}
    )
    assert "reason_codes" in result
    assert result["reason_codes"] == [FraudReasonCode.NONE.value]


def test_analyze_fraud_reading_includes_physics_codes():
    det = FraudDetector()
    result = det.analyze(
        {"speed": 300.0, "rpm": 0, "fuel_rate": -5.0, "acceleration": 10.0}
    )
    codes = result["reason_codes"]
    assert FraudReasonCode.PHYSICS_RPM_ZERO_SPEED_NONZERO.value in codes
    assert FraudReasonCode.PHYSICS_FUEL_NEGATIVE.value in codes
    assert FraudReasonCode.PHYSICS_SPEED_MAX.value in codes
    assert FraudReasonCode.PHYSICS_ACCEL_CAP_EXCEEDED.value in codes
    assert FraudReasonCode.NONE.value not in codes


def test_analyze_reason_codes_deduplicated():
    det = FraudDetector()
    result = det.analyze(
        {"speed": 60.0, "rpm": 0, "fuel_rate": 7.0, "acceleration": 0.5,
         "co2": 115.0}
    )
    codes = result["reason_codes"]
    assert len(codes) == len(set(codes))
