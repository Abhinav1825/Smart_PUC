"""Tests for STATION_COUNTRY / SMART_PUC_DEFAULT_CYCLE env selection (audit 13A #10)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def _clean_env(monkeypatch):
    monkeypatch.delenv("STATION_COUNTRY", raising=False)
    monkeypatch.delenv("SMART_PUC_DEFAULT_CYCLE", raising=False)


def test_default_cycle_is_wltc_when_no_env(monkeypatch):
    _clean_env(monkeypatch)
    from simulator import WLTCSimulator
    sim = WLTCSimulator()
    assert sim._cycle_name == "wltc"


def test_station_country_in_switches_to_midc(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "IN")
    from simulator import WLTCSimulator
    sim = WLTCSimulator()
    assert sim._cycle_name == "midc"


def test_explicit_override_env_wins_over_country(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "IN")
    monkeypatch.setenv("SMART_PUC_DEFAULT_CYCLE", "wltc")
    from simulator import WLTCSimulator
    sim = WLTCSimulator()
    assert sim._cycle_name == "wltc"


def test_explicit_arg_beats_env(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "IN")
    from simulator import WLTCSimulator
    sim = WLTCSimulator(cycle="wltc")
    assert sim._cycle_name == "wltc"


def test_unknown_country_falls_back_to_wltc(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("STATION_COUNTRY", "JP")
    from simulator import WLTCSimulator
    sim = WLTCSimulator()
    assert sim._cycle_name == "wltc"
