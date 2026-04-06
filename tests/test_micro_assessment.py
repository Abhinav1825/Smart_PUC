"""
SmartPUC -- Unit tests for the MicroAssessmentEngine (ml/micro_assessment.py).

Tests weekly report generation, degradation risk classification,
and recommendation generation using mocked persistence.
"""

from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from ml.micro_assessment import MicroAssessmentEngine, _classify_tier  # noqa: E402


def _make_mock_store(readings: list[dict] | None = None):
    """Create a mock PersistenceStore with telemetry data."""
    store = MagicMock()
    store.enabled = True

    now = int(time.time())
    telemetry_rows = []
    if readings:
        for i, r in enumerate(readings):
            telemetry_rows.append({
                "id": i + 1,
                "observed_at": now - (len(readings) - i) * 3600,
                "reading": r,
                "onchain_tx": None,
                "batch_id": None,
                "is_violation": 0,
            })

    store.telemetry_for_vehicle.return_value = telemetry_rows
    store.store_health_report.return_value = 1
    store.store_degradation_event.return_value = 1
    store.get_health_reports.return_value = []
    store.get_degradation_events.return_value = []
    return store


def _make_readings(n: int, base_ces: float = 0.3, ces_drift: float = 0.0):
    """Generate n synthetic telemetry readings with optional CES drift."""
    readings = []
    for i in range(n):
        readings.append({
            "speed": 50.0 + i * 0.5,
            "rpm": 2000 + i * 10,
            "fuel_rate": 5.0,
            "fuel_type": "petrol",
            "acceleration": 0.5 if i % 3 == 0 else 0.0,
            "co2_g_per_km": 120.0 + i * 0.5,
            "co_g_per_km": 0.5 + i * 0.01,
            "nox_g_per_km": 0.03 + i * 0.001,
            "hc_g_per_km": 0.02 + i * 0.001,
            "pm25_g_per_km": 0.001,
            "ces_score": base_ces + i * ces_drift,
            "status": "PASS",
            "wltc_phase": 1,
            "timestamp": int(time.time()) - (n - i) * 3600,
        })
    return readings


class TestGenerateWeeklyReport:
    """Tests for generate_weekly_report()."""

    def test_basic_report_with_5_readings(self):
        readings = _make_readings(5, base_ces=0.3)
        store = _make_mock_store(readings)
        engine = MicroAssessmentEngine(store)

        report = engine.generate_weekly_report("TEST001")

        assert report["vehicle_id"] == "TEST001"
        assert report["period_days"] == 7
        assert report["readings_count"] == 5
        assert isinstance(report["ces_mean"], float)
        assert isinstance(report["ces_slope"], float)
        assert isinstance(report["ces_max"], float)
        assert isinstance(report["ces_p95"], float)
        assert isinstance(report["pollutants"], dict)
        assert "co2_mean" in report["pollutants"]
        assert "nox_mean" in report["pollutants"]
        assert isinstance(report["driving_score"], float)
        assert report["degradation_risk"] in ("low", "medium", "high")
        assert report["tier"] in ("Gold", "Silver", "Bronze", "Unclassified")
        assert isinstance(report["recommendations"], list)
        assert "generated_at" in report

    def test_empty_telemetry(self):
        store = _make_mock_store([])
        engine = MicroAssessmentEngine(store)
        report = engine.generate_weekly_report("EMPTY001")

        assert report["readings_count"] == 0
        assert report["ces_mean"] == 0.0
        assert report["ces_slope"] == 0.0
        assert report["degradation_risk"] == "low"

    def test_report_persisted(self):
        readings = _make_readings(3, base_ces=0.5)
        store = _make_mock_store(readings)
        engine = MicroAssessmentEngine(store)

        engine.generate_weekly_report("TEST002")
        store.store_health_report.assert_called_once()


class TestDegradationRiskClassification:
    """Tests for degradation risk levels based on CES slope."""

    def test_low_risk(self):
        # Flat CES (no drift) -> low risk
        readings = _make_readings(10, base_ces=0.3, ces_drift=0.0)
        store = _make_mock_store(readings)
        engine = MicroAssessmentEngine(store)
        report = engine.generate_weekly_report("LOW_RISK")
        assert report["degradation_risk"] == "low"

    def test_medium_risk(self):
        # Moderate upward CES drift -> medium risk
        # slope needs to be > 0.02 per day; readings are 1 hr apart
        # so we need drift per reading that yields > 0.02/day
        # 24 readings/day * drift = 0.03 -> drift = 0.00125
        readings = _make_readings(24, base_ces=0.3, ces_drift=0.00125)
        store = _make_mock_store(readings)
        engine = MicroAssessmentEngine(store)
        report = engine.generate_weekly_report("MED_RISK")
        assert report["degradation_risk"] in ("medium", "high")

    def test_high_risk(self):
        # Steep upward CES drift -> high risk
        # slope > 0.05/day, 24 readings/day -> drift > 0.05/24 = 0.00208
        readings = _make_readings(24, base_ces=0.3, ces_drift=0.005)
        store = _make_mock_store(readings)
        engine = MicroAssessmentEngine(store)
        report = engine.generate_weekly_report("HIGH_RISK")
        assert report["degradation_risk"] == "high"

    def test_high_risk_triggers_degradation_event(self):
        readings = _make_readings(24, base_ces=0.3, ces_drift=0.005)
        store = _make_mock_store(readings)
        engine = MicroAssessmentEngine(store)
        engine.generate_weekly_report("HIGH_RISK_EVENT")
        store.store_degradation_event.assert_called_once()


class TestRecommendations:
    """Tests for actionable recommendations."""

    def test_healthy_vehicle_gets_positive_message(self):
        # Use flat readings (no per-reading increment) so pollutant slopes
        # stay near zero and the "healthy range" recommendation fires.
        readings = []
        now = int(time.time())
        for i in range(5):
            readings.append({
                "speed": 50.0,
                "rpm": 2000,
                "fuel_rate": 5.0,
                "fuel_type": "petrol",
                "acceleration": 0.5,
                "co2_g_per_km": 120.0,
                "co_g_per_km": 0.5,
                "nox_g_per_km": 0.03,
                "hc_g_per_km": 0.02,
                "pm25_g_per_km": 0.001,
                "ces_score": 0.2,
                "status": "PASS",
                "wltc_phase": 1,
                "timestamp": now - (5 - i) * 3600,
            })
        store = _make_mock_store(readings)
        engine = MicroAssessmentEngine(store)
        report = engine.generate_weekly_report("HEALTHY")
        assert len(report["recommendations"]) >= 1
        assert any("healthy" in r.lower() or "maintenance" in r.lower()
                    for r in report["recommendations"])

    def test_high_slope_gets_inspection_recommendation(self):
        readings = _make_readings(24, base_ces=0.3, ces_drift=0.005)
        store = _make_mock_store(readings)
        engine = MicroAssessmentEngine(store)
        report = engine.generate_weekly_report("DEGRADING")
        assert any("inspection" in r.lower() or "deteriorating" in r.lower()
                    for r in report["recommendations"])


class TestTierClassification:
    """Tests for _classify_tier()."""

    def test_gold_tier(self):
        assert _classify_tier(0.2, violation_count=0) == "Gold"

    def test_silver_tier(self):
        assert _classify_tier(0.5, violation_count=0) == "Silver"

    def test_bronze_tier(self):
        assert _classify_tier(0.8, violation_count=0) == "Bronze"

    def test_unclassified_high_ces(self):
        assert _classify_tier(1.2, violation_count=0) == "Unclassified"

    def test_gold_downgraded_with_violations(self):
        # Gold CES but has violations -> Silver
        assert _classify_tier(0.2, violation_count=1) == "Silver"


class TestFleetSummary:
    """Tests for generate_fleet_summary()."""

    def test_empty_fleet(self):
        store = _make_mock_store([])
        engine = MicroAssessmentEngine(store)
        summary = engine.generate_fleet_summary([])
        assert summary["total_vehicles"] == 0
        assert summary["fleet_health_score"] == 0.0

    def test_fleet_with_vehicles(self):
        store = MagicMock()
        store.enabled = True
        store.get_health_reports.return_value = [
            {"tier": "Gold", "ces_mean": 0.2, "degradation_risk": "low"}
        ]
        engine = MicroAssessmentEngine(store)
        summary = engine.generate_fleet_summary(["V1", "V2"])
        assert summary["total_vehicles"] == 2
