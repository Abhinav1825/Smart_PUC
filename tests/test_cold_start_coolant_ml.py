"""Tests for the COPERT 5 cold-start helper (audit 13A #6).

The helper should:
    * Return True when coolant temperature is below 70 °C.
    * Return False when coolant temperature is at/above 70 °C.
    * Fall back to the pre-existing ``cold_start`` boolean when no
      coolant PID/value is present.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations.obd_adapter import (
    COLD_START_COOLANT_THRESHOLD_C,
    is_cold_start,
)


def test_threshold_is_70c():
    # Documented COPERT 5 light-off temperature
    assert COLD_START_COOLANT_THRESHOLD_C == 70.0


def test_cold_when_coolant_below_70():
    assert is_cold_start({"coolant_temp": 40.0}) is True
    assert is_cold_start({"coolant_temp_c": 55.0}) is True


def test_hot_when_coolant_at_or_above_70():
    assert is_cold_start({"coolant_temp": 70.0}) is False
    assert is_cold_start({"coolant_temp": 90.0}) is False


def test_raw_pid_05_decoded():
    # PID 0x05 formula: value = A - 40. A=80 → 40 °C → cold.
    assert is_cold_start({0x05: [80]}) is True
    # A=130 → 90 °C → hot.
    assert is_cold_start({0x05: [130]}) is False


def test_fallback_to_existing_bool_when_no_coolant_data():
    assert is_cold_start({"cold_start": True}) is True
    assert is_cold_start({"cold_start": False}) is False
    # Empty dict → default False
    assert is_cold_start({}) is False


def test_coolant_value_takes_precedence_over_fallback_bool():
    # If coolant is explicitly hot, fallback bool is ignored.
    assert is_cold_start({"coolant_temp": 85.0, "cold_start": True}) is False
    assert is_cold_start({"coolant_temp": 30.0, "cold_start": False}) is True
