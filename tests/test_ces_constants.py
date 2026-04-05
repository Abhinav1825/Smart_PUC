"""
Smart PUC — CES constants consistency tests
============================================

These tests lock down the single-source-of-truth invariant between:

  config/ces_weights.json  →  backend/ces_constants.py  →  backend/emission_engine.py

They also exercise the generator's --check mode (audit L8 / G4).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def test_ces_weights_sum_to_one():
    from backend.ces_constants import CES_WEIGHTS
    total = sum(CES_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"CES weights must sum to 1.0, got {total}"


def test_ces_weights_have_all_five_pollutants():
    from backend.ces_constants import CES_WEIGHTS
    assert set(CES_WEIGHTS.keys()) == {"co2", "nox", "co", "hc", "pm25"}


def test_bsvi_thresholds_match_regulated_values():
    """BS-VI thresholds from the generated module must match ARAI/MoRTH."""
    from backend.ces_constants import BSVI_THRESHOLDS_PETROL
    assert BSVI_THRESHOLDS_PETROL["co2"] == 120.0
    assert BSVI_THRESHOLDS_PETROL["co"] == 1.0
    assert BSVI_THRESHOLDS_PETROL["nox"] == 0.06
    assert BSVI_THRESHOLDS_PETROL["hc"] == 0.10
    assert BSVI_THRESHOLDS_PETROL["pm25"] == 0.0045


def test_bs4_thresholds_are_looser_than_bsvi():
    """BS-IV is strictly looser than BS-VI on CO, NOx, HC, PM2.5."""
    from backend.ces_constants import BSVI_THRESHOLDS_PETROL, BS4_THRESHOLDS_PETROL
    for pollutant in ("co", "nox", "hc", "pm25"):
        assert BS4_THRESHOLDS_PETROL[pollutant] > BSVI_THRESHOLDS_PETROL[pollutant], (
            f"BS-IV {pollutant} threshold must be > BS-VI threshold"
        )


def test_emission_engine_uses_generated_constants():
    """The engine's exported names must come from the generator."""
    from backend import emission_engine as ee
    from backend.ces_constants import CES_WEIGHTS as gen_weights
    assert ee.CES_WEIGHTS == gen_weights
    # Diesel HC+NOx alias stayed backward-compatible
    assert ee.DIESEL_HC_NOX_THRESHOLD == ee.DIESEL_HC_THRESHOLD + ee.DIESEL_NOX_THRESHOLD


def test_generator_check_mode_exits_zero_on_clean_tree():
    """scripts/gen_ces_consts.py --check should succeed on a clean tree."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_ces_consts.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"gen_ces_consts --check failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Solidity cross-check OK" in result.stdout


def test_json_solidity_int_consistency():
    """Each ces_weights.ces_weights[x] * 10000 must match ces_weights_solidity[x]."""
    data = json.loads((ROOT / "config" / "ces_weights.json").read_text(encoding="utf-8"))
    scale = data["solidity_scale"]
    for key, fv in data["ces_weights"].items():
        iv = data["ces_weights_solidity"][key]
        assert round(fv * scale) == iv, (
            f"ces_weights_solidity.{key}={iv}, expected {round(fv * scale)}"
        )
