"""Tests for FraudDetector checkpoint round-trip (audit Fix #8)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from ml.fraud_detector import FraudDetector


def _canonical_reading():
    return {
        "vehicle_id": "UNITTEST",
        "speed": 60.0,
        "rpm": 2100,
        "fuel_rate": 4.5,
        "acceleration": 0.2,
        "co2": 115.0,
        "co": 0.8,
        "nox": 0.05,
        "hc": 0.09,
        "pm25": 0.003,
        "vsp": 3.5,
        "timestamp": 1_700_000_000,
    }


def test_save_and_load_checkpoint_round_trip(tmp_path):
    det = FraudDetector()
    training = [_canonical_reading() for _ in range(40)]
    det.fit(training)

    out = tmp_path / "fraud_ckpt.pkl"
    det.save_checkpoint(out)
    assert out.exists()
    assert out.stat().st_size > 1000  # at least a KB of pickled sklearn state

    reloaded = FraudDetector.load_checkpoint(out)
    # Same canonical reading should produce the same ensemble score.
    r1 = det.analyze(_canonical_reading())
    r2 = reloaded.analyze(_canonical_reading())
    assert r1["components"]["isolation"] == pytest.approx(r2["components"]["isolation"], abs=1e-9)
    assert r1["fraud_score"] == pytest.approx(r2["fraud_score"], abs=1e-9)


def test_load_rejects_unsupported_schema(tmp_path):
    import pickle
    bad = {"schema_version": 999, "weights": {"physics": 0.25}}
    out = tmp_path / "bad.pkl"
    out.write_bytes(pickle.dumps(bad))
    with pytest.raises(ValueError, match="Unsupported FraudDetector checkpoint schema"):
        FraudDetector.load_checkpoint(out)


def test_shipped_checkpoint_file_exists_and_loads():
    """The pickle produced by scripts/build_fraud_checkpoint.py should
    live in data/ and round-trip cleanly (loose skip if missing in
    clean-room CI)."""
    ckpt = ROOT / "data" / "fraud_detector_v3.2.pkl"
    if not ckpt.exists():
        pytest.skip("fraud_detector_v3.2.pkl not built in this environment")
    det = FraudDetector.load_checkpoint(ckpt)
    result = det.analyze(_canonical_reading())
    assert "fraud_score" in result
    assert 0.0 <= result["fraud_score"] <= 1.0


def test_per_pollutant_drift_is_in_analyze_result():
    """Audit 13A #8 — per-pollutant Page-Hinkley bank is exposed in the
    analyze() result as a ``pollutant_drift`` sub-dict with one entry
    per pollutant channel."""
    det = FraudDetector()
    result = det.analyze(_canonical_reading())
    assert "pollutant_drift" in result
    assert "per_channel" in result["pollutant_drift"]
    assert set(result["pollutant_drift"]["per_channel"].keys()) == {
        "co2", "co", "nox", "hc", "pm25"
    }
