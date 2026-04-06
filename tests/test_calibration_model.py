"""
Tests for SmartPUC CalibrationModel (OBD-to-tailpipe calibration).

Generates a small paired dataset via the paired-dataset generator script,
trains a CalibrationModel, and validates training, calibration, checkpoint
round-trip, and evaluation outputs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

# Resolve project root so imports work
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from ml.calibration_model import CalibrationModel

PYTHON = os.path.join(PROJECT_ROOT, "backend", "venv", "Scripts", "python.exe")
GENERATOR = os.path.join(PROJECT_ROOT, "scripts", "generate_paired_dataset.py")


@pytest.fixture(scope="module")
def paired_csv(tmp_path_factory):
    """Generate a small paired dataset (10 vehicles) for testing."""
    tmpdir = tmp_path_factory.mktemp("cal_data")
    csv_path = str(tmpdir / "paired_test.csv")

    result = subprocess.run(
        [
            PYTHON,
            GENERATOR,
            "--vehicles", "10",
            "--seed", "42",
            "--output", csv_path,
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"Dataset generator failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert os.path.exists(csv_path), "CSV not generated"
    return csv_path


@pytest.fixture(scope="module")
def trained_model(paired_csv):
    """Return a CalibrationModel trained on the small dataset."""
    model = CalibrationModel(n_estimators=50, max_depth=4, random_state=42)
    model.train(paired_csv)
    return model


# ── Test 1: train produces models ────────────────────────────────────────

def test_train_produces_models(trained_model):
    """After training, models exist for all 5 pollutants."""
    assert trained_model.is_trained
    for p in CalibrationModel.POLLUTANTS:
        assert p in trained_model._models, f"Missing model for {p}"


# ── Test 2: evaluate returns R2 per pollutant ────────────────────────────

def test_evaluate_returns_r2_per_pollutant(trained_model):
    """evaluate() returns R2, MAE, RMSE keys for all 5 pollutants."""
    metrics = trained_model.evaluate()
    for p in CalibrationModel.POLLUTANTS:
        assert p in metrics, f"Missing metrics for {p}"
        assert "r2" in metrics[p]
        assert "mae" in metrics[p]
        assert "rmse" in metrics[p]


# ── Test 3: R2 above threshold for CO2 ──────────────────────────────────

def test_r2_above_threshold(trained_model):
    """R2 for CO2 should be > 0.5 even on a small dataset."""
    metrics = trained_model.evaluate()
    r2_co2 = metrics["co2"]["r2"]
    assert r2_co2 > 0.5, f"CO2 R2 = {r2_co2}, expected > 0.5"


# ── Test 4: calibrate returns all expected keys ──────────────────────────

def test_calibrate_returns_all_keys(trained_model):
    """calibrate() returns calibrated_, raw_, gap_ for each pollutant + CES."""
    obd_reading = {
        "speed_kmh": 60.0,
        "rpm": 2500,
        "fuel_rate": 1.5,
        "acceleration": 0.5,
        "co2_g_per_km": 130.0,
        "co_g_per_km": 0.5,
        "nox_g_per_km": 0.04,
        "hc_g_per_km": 0.05,
        "pm25_g_per_km": 0.002,
    }
    result = trained_model.calibrate(obd_reading)
    for p in CalibrationModel.POLLUTANTS:
        assert f"calibrated_{p}" in result, f"Missing calibrated_{p}"
        assert f"raw_{p}" in result, f"Missing raw_{p}"
        assert f"gap_{p}" in result, f"Missing gap_{p}"
    assert "calibrated_ces" in result
    assert "confidence" in result


# ── Test 5: calibrated values differ from raw ────────────────────────────

def test_calibrated_values_differ_from_raw(trained_model):
    """The model should learn a non-zero gap (at least for some pollutants)."""
    obd_reading = {
        "speed_kmh": 80.0,
        "rpm": 3000,
        "fuel_rate": 2.0,
        "acceleration": 0.3,
        "co2_g_per_km": 140.0,
        "co_g_per_km": 0.8,
        "nox_g_per_km": 0.05,
        "hc_g_per_km": 0.06,
        "pm25_g_per_km": 0.003,
    }
    result = trained_model.calibrate(obd_reading)
    any_nonzero = any(
        abs(result[f"gap_{p}"]) > 1e-9
        for p in CalibrationModel.POLLUTANTS
    )
    assert any_nonzero, "All gaps are zero — model learned nothing"


# ── Test 6: save/load checkpoint round-trip ──────────────────────────────

def test_save_load_checkpoint(trained_model, tmp_path):
    """Checkpoint save + load should preserve evaluation metrics."""
    ckpt = str(tmp_path / "test_ckpt.pkl")
    trained_model.save_checkpoint(ckpt)
    assert os.path.exists(ckpt)

    loaded = CalibrationModel.load_checkpoint(ckpt)
    assert loaded.is_trained

    orig_metrics = trained_model.evaluate()
    loaded_metrics = loaded.evaluate()
    for p in CalibrationModel.POLLUTANTS:
        assert orig_metrics[p]["r2"] == loaded_metrics[p]["r2"], (
            f"R2 mismatch for {p}: {orig_metrics[p]['r2']} vs {loaded_metrics[p]['r2']}"
        )


# ── Test 7: feature importance returns dict ──────────────────────────────

def test_feature_importance_returns_dict(trained_model):
    """feature_importance('co2') returns a dict of feature → score."""
    fi = trained_model.feature_importance("co2")
    assert isinstance(fi, dict)
    assert len(fi) > 0
    # All values should be numeric
    for k, v in fi.items():
        assert isinstance(v, float), f"Feature {k} importance is not float"


# ── Test 8: untrained calibrate raises ───────────────────────────────────

def test_untrained_calibrate_raises():
    """Calling calibrate() before train() should raise RuntimeError."""
    model = CalibrationModel()
    with pytest.raises(RuntimeError, match="not been trained"):
        model.calibrate({"speed_kmh": 50, "rpm": 2000, "fuel_rate": 1.0,
                         "acceleration": 0.0})


# ── Test 9: calibrated CES is computed and positive ──────────────────────

def test_calibrated_ces_computed(trained_model):
    """calibrated_ces should be present and > 0."""
    obd_reading = {
        "speed_kmh": 60.0,
        "rpm": 2500,
        "fuel_rate": 1.5,
        "acceleration": 0.5,
        "co2_g_per_km": 130.0,
        "co_g_per_km": 0.5,
        "nox_g_per_km": 0.04,
        "hc_g_per_km": 0.05,
        "pm25_g_per_km": 0.002,
    }
    result = trained_model.calibrate(obd_reading)
    assert result["calibrated_ces"] > 0, f"calibrated_ces = {result['calibrated_ces']}"
