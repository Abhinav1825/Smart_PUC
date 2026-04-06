"""Tests for scripts/generate_paired_dataset.py — paired OBD/tailpipe dataset."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile

import pytest

# Project root and Python executable
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
PYTHON = os.path.join(PROJECT_ROOT, "backend", "venv", "Scripts", "python.exe")
SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "generate_paired_dataset.py")

EXPECTED_COLUMNS = [
    "vehicle_id", "vehicle_class", "mileage_km", "age_years",
    "fuel_type", "bs_standard",
    "time_s", "speed_kmh", "rpm", "fuel_rate", "acceleration",
    "coolant_temp", "phase",
    "obd_co2", "obd_co", "obd_nox", "obd_hc", "obd_pm25", "obd_ces",
    "tailpipe_co2", "tailpipe_co", "tailpipe_nox", "tailpipe_hc",
    "tailpipe_pm25", "tailpipe_ces",
    "has_failure", "failure_type", "failure_onset_s",
]


def _run_generator(output_path: str, vehicles: int = 5, seed: int = 42,
                   cycle: str = "wltc") -> subprocess.CompletedProcess:
    """Run the generator script and return the CompletedProcess."""
    cmd = [
        PYTHON, SCRIPT,
        "--vehicles", str(vehicles),
        "--seed", str(seed),
        "--output", output_path,
        "--cycle", cycle,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
        cwd=PROJECT_ROOT,
    )
    return result


@pytest.fixture(scope="module")
def generated_csv(tmp_path_factory) -> str:
    """Generate a small CSV once for the module's tests."""
    tmp_dir = tmp_path_factory.mktemp("paired_data")
    output_path = str(tmp_dir / "test_paired.csv")
    result = _run_generator(output_path, vehicles=5, seed=42)
    assert result.returncode == 0, f"Generator failed:\n{result.stderr}"
    return output_path


class TestGeneratorRuns:
    def test_exit_code_zero(self, generated_csv: str):
        """The generator should complete without errors."""
        assert os.path.exists(generated_csv)

    def test_csv_not_empty(self, generated_csv: str):
        size = os.path.getsize(generated_csv)
        assert size > 0


class TestCSVColumns:
    def test_has_all_expected_columns(self, generated_csv: str):
        with open(generated_csv, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames is not None
            for col in EXPECTED_COLUMNS:
                assert col in reader.fieldnames, f"Missing column: {col}"

    def test_no_extra_columns(self, generated_csv: str):
        with open(generated_csv, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames is not None
            for col in reader.fieldnames:
                assert col in EXPECTED_COLUMNS, f"Unexpected column: {col}"


class TestDataQuality:
    def test_correct_number_of_vehicles(self, generated_csv: str):
        """5 vehicles should produce 5 unique vehicle IDs."""
        vehicle_ids = set()
        with open(generated_csv, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                vehicle_ids.add(row["vehicle_id"])
        assert len(vehicle_ids) == 5

    def test_correct_row_count_wltc(self, generated_csv: str):
        """5 vehicles x 1800 seconds (WLTC) = 9000 data rows."""
        with open(generated_csv, "r", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader)  # skip header
            row_count = sum(1 for _ in reader)
        assert row_count == 5 * 1800

    def test_tailpipe_higher_on_average(self, generated_csv: str):
        """Tailpipe values should be >= OBD on average for degraded vehicles.

        Because degradation factors are >= 1.0, the mean tailpipe value
        should exceed the mean OBD value (noise averages out over many samples).
        """
        obd_sums = {"co2": 0.0, "co": 0.0, "nox": 0.0, "hc": 0.0, "pm25": 0.0}
        tp_sums = {"co2": 0.0, "co": 0.0, "nox": 0.0, "hc": 0.0, "pm25": 0.0}
        n = 0
        with open(generated_csv, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                n += 1
                for p in obd_sums:
                    obd_sums[p] += float(row[f"obd_{p}"])
                    tp_sums[p] += float(row[f"tailpipe_{p}"])

        assert n > 0
        for p in obd_sums:
            mean_obd = obd_sums[p] / n
            mean_tp = tp_sums[p] / n
            # Tailpipe should be at least as high as OBD on average
            # (allow small tolerance for noise)
            assert mean_tp >= mean_obd * 0.98, (
                f"{p}: mean tailpipe ({mean_tp:.6f}) < mean OBD ({mean_obd:.6f})"
            )


class TestReproducibility:
    def test_same_seed_same_output(self, tmp_path):
        """Running with the same seed should produce identical output."""
        path1 = str(tmp_path / "run1.csv")
        path2 = str(tmp_path / "run2.csv")

        r1 = _run_generator(path1, vehicles=3, seed=99)
        r2 = _run_generator(path2, vehicles=3, seed=99)

        assert r1.returncode == 0, f"Run 1 failed:\n{r1.stderr}"
        assert r2.returncode == 0, f"Run 2 failed:\n{r2.stderr}"

        with open(path1, "r") as f1, open(path2, "r") as f2:
            content1 = f1.read()
            content2 = f2.read()

        assert content1 == content2, "Same seed produced different outputs"
