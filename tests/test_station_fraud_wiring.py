"""Smart PUC — station fraud detector wiring into phase_listener (audit G9).

These tests do not require a running blockchain. They construct a
PhaseListener with a fake connector + a stubbed StationFraudDetector and
a stubbed persistence store, then directly exercise the wiring path
(``_run_station_fraud``) and confirm that MEDIUM/HIGH risk reports are
forwarded to ``persistence_store.add_notification`` with the expected
type and severity mapping.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from backend.phase_listener import PhaseListener


class _FakeConnector:
    registry = None
    w3 = None


class _StubReport:
    def __init__(self, station_id: str, risk_level: str, risk_score: float):
        self.station_id = station_id
        self.risk_level = risk_level
        self.risk_score = risk_score


class _StubDetector:
    def __init__(self, reports):
        self._reports = reports
        self.calls = 0

    def analyse(self, records):
        self.calls += 1
        self.last_records = list(records)
        return self._reports


class _StubPersistence:
    def __init__(self):
        self.notifications = []

    def add_notification(self, ntype, message, vehicle_id="", severity="info"):
        self.notifications.append({
            "type": ntype,
            "message": message,
            "vehicle_id": vehicle_id,
            "severity": severity,
        })
        return len(self.notifications)


def _make_listener(detector, persistence):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return PhaseListener(
        _FakeConnector(),
        db_path=tmp.name,
        persistence_store=persistence,
        station_fraud_detector=detector,
    )


def test_medium_and_high_reports_create_notifications():
    reports = [
        _StubReport("STATION_A", "HIGH", 0.91),
        _StubReport("STATION_B", "MEDIUM", 0.33),
        _StubReport("STATION_C", "LOW", 0.05),
        _StubReport("STATION_D", "INSUFFICIENT_DATA", 0.0),
    ]
    det = _StubDetector(reports)
    ps = _StubPersistence()
    listener = _make_listener(det, ps)

    listener._run_station_fraud([{"station_id": "STATION_A", "timestamp": 1}])
    assert det.calls == 1
    # Only HIGH + MEDIUM should be persisted.
    assert len(ps.notifications) == 2
    types = {n["type"] for n in ps.notifications}
    assert types == {"station_fraud_alert"}
    severities = {n["severity"] for n in ps.notifications}
    assert severities == {"critical", "warning"}
    # HIGH → critical, MEDIUM → warning.
    by_sev = {n["severity"]: n for n in ps.notifications}
    assert "STATION_A" in by_sev["critical"]["message"]
    assert "STATION_B" in by_sev["warning"]["message"]


def test_feature_flag_disabled_skips_detector(monkeypatch):
    monkeypatch.setenv("STATION_FRAUD_DETECTION_ENABLED", "0")
    # New PhaseListener reads the flag at init. We still pass a detector
    # explicitly to keep the instance, but _run_station_fraud must be a no-op
    # when the flag is off.
    det = _StubDetector([_StubReport("STATION_A", "HIGH", 0.9)])
    ps = _StubPersistence()
    listener = _make_listener(det, ps)
    # Force the flag off (init already captured the env var, but the check
    # is re-read inside _run_station_fraud via self._station_fraud_enabled).
    listener._station_fraud_enabled = False
    listener._run_station_fraud([{"station_id": "STATION_A", "timestamp": 1}])
    assert det.calls == 0
    assert ps.notifications == []


def test_detector_none_is_safe():
    """If no detector is configured, the wiring must be a silent no-op."""
    ps = _StubPersistence()
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    listener = PhaseListener(
        _FakeConnector(),
        db_path=tmp.name,
        persistence_store=ps,
        station_fraud_detector=None,
    )
    # Force detector to None so we exercise the None-guard inside
    # _run_station_fraud (some environments may have imported a real one).
    listener._station_fraud = None
    listener._run_station_fraud([{"station_id": "X", "timestamp": 1}])
    assert ps.notifications == []
