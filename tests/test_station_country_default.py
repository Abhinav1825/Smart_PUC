"""Tests for the module-level ``default_cycle()`` helper and the
``STATION_COUNTRY=IN`` → MIDC wiring.

This complements ``tests/test_midc_default.py`` (which targets the
simulator-constructor path) by verifying the standalone helper function
introduced for audit Top-5 Addition #5 / prior-audit §13A #10.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def _clean_env(monkeypatch):
    monkeypatch.delenv("STATION_COUNTRY", raising=False)
    monkeypatch.delenv("SMART_PUC_DEFAULT_CYCLE", raising=False)


# ── default_cycle() helper ──────────────────────────────────────────────

def test_default_cycle_helper_wltc_when_env_unset(monkeypatch):
    _clean_env(monkeypatch)
    from simulator import default_cycle
    assert default_cycle() == "WLTC"


def test_default_cycle_helper_midc_when_station_country_in(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "IN")
    from simulator import default_cycle
    assert default_cycle() == "MIDC"


def test_default_cycle_helper_case_insensitive_in(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "in")
    from simulator import default_cycle
    assert default_cycle() == "MIDC"


def test_default_cycle_helper_other_country_is_wltc(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "DE")
    from simulator import default_cycle
    assert default_cycle() == "WLTC"


def test_default_cycle_helper_explicit_override_beats_country(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "IN")
    monkeypatch.setenv("SMART_PUC_DEFAULT_CYCLE", "wltc")
    from simulator import default_cycle
    assert default_cycle() == "WLTC"


# ── WLTCSimulator factory uses the helper ───────────────────────────────

def test_simulator_default_is_wltc_without_env(monkeypatch):
    _clean_env(monkeypatch)
    from simulator import WLTCSimulator
    sim = WLTCSimulator()
    assert sim._cycle_name == "wltc"


def test_simulator_default_is_midc_under_station_country_in(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "IN")
    from simulator import WLTCSimulator
    sim = WLTCSimulator()
    assert sim._cycle_name == "midc"


def test_simulator_explicit_cycle_arg_overrides_env(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "IN")
    from simulator import WLTCSimulator
    sim_wltc = WLTCSimulator(cycle="wltc")
    assert sim_wltc._cycle_name == "wltc"
    # And the reverse: WLTC machine that explicitly requests MIDC still
    # gets MIDC.
    monkeypatch.delenv("STATION_COUNTRY", raising=False)
    sim_midc = WLTCSimulator(cycle="midc")
    assert sim_midc._cycle_name == "midc"
