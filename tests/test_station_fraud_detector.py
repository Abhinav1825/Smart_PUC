"""Tests for ml/station_fraud_detector.py (audit 13B #14)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import pytest
from ml.station_fraud_detector import StationFraudDetector, MIN_RECORDS_FOR_BASELINE


NOW = 1_700_000_000


def _rec(station_id, ts, status="PASS", ces=0.5):
    return {"station_id": station_id, "timestamp": ts, "status": status, "ces_score": ces}


def test_insufficient_data_is_scored_zero():
    d = StationFraudDetector()
    records = [_rec("S1", NOW - 100 * i) for i in range(5)]
    reports = d.analyse(records, now=NOW)
    assert len(reports) == 1
    assert reports[0].risk_level == "INSUFFICIENT_DATA"
    assert reports[0].risk_score == 0.0


def test_normal_station_is_low_risk():
    d = StationFraudDetector()
    records = []
    # 30 PASS records spread evenly over 5 days
    for i in range(30):
        records.append(_rec("NORMAL", NOW - (i * 4 * 3600), status="PASS", ces=0.5))
    reports = d.analyse(records, now=NOW)
    normal = next(r for r in reports if r.station_id == "NORMAL")
    assert normal.risk_level == "LOW"
    assert normal.risk_score < 0.25


def test_volume_spike_flags_station():
    """A station that suddenly processes 10× its historical volume."""
    d = StationFraudDetector(volume_z_alarm=2.5)
    records = []
    # 7-day baseline: 2 records/hour
    for day in range(1, 7):
        for i in range(2):
            records.append(
                _rec("FLOOD", NOW - (day * 86400) + (i * 1800), status="PASS", ces=0.5)
            )
    # Current hour: 30 records — 15× baseline
    for i in range(30):
        records.append(_rec("FLOOD", NOW - (i * 60), status="PASS", ces=0.5))
    reports = d.analyse(records, now=NOW)
    flood = next(r for r in reports if r.station_id == "FLOOD")
    assert flood.current_rate_per_hour > flood.baseline_rate_per_hour * 5
    assert flood.risk_level in ("MEDIUM", "HIGH")


def test_pass_rate_jump_flags_station():
    """A station whose PASS rate jumps 60% → 98%."""
    d = StationFraudDetector()
    records = []
    # 7-day baseline: 50 records, 60% PASS
    for i in range(50):
        status = "PASS" if (i % 10) < 6 else "FAIL"
        records.append(_rec("LENIENT", NOW - (2 * 86400) - (i * 3600), status=status, ces=0.5))
    # Current 24h: 50 records, 98% PASS
    for i in range(50):
        status = "PASS" if (i % 50) < 49 else "FAIL"
        records.append(_rec("LENIENT", NOW - (i * 1500), status=status, ces=0.5))
    reports = d.analyse(records, now=NOW)
    lenient = next(r for r in reports if r.station_id == "LENIENT")
    assert lenient.pass_rate_current > 0.9
    assert lenient.pass_rate_baseline < 0.75
    assert lenient.risk_level in ("MEDIUM", "HIGH")


def test_as_dict_shape_has_expected_keys():
    d = StationFraudDetector()
    records = [_rec("S", NOW - i * 1000) for i in range(25)]
    reports = d.analyse(records, now=NOW)
    assert reports
    asd = reports[0].as_dict()
    for k in (
        "station_id",
        "total_records",
        "records_in_window",
        "baseline_rate_per_hour",
        "current_rate_per_hour",
        "volume_z_score",
        "pass_rate_baseline",
        "pass_rate_current",
        "pass_rate_delta",
        "avg_ces_baseline",
        "avg_ces_current",
        "avg_ces_delta_pct",
        "risk_score",
        "risk_level",
        "violations",
    ):
        assert k in asd, f"missing key {k}"
