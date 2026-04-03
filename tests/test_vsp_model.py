"""
Tests for the VSP (Vehicle Specific Power) physics model.

Tests VSP calculations with known input/output pairs for:
  - Idle conditions
  - Cruising at 60 km/h
  - Hard acceleration
  - Operating mode bin mapping
  - Fuel rate estimation
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from physics.vsp_model import (
    calculate_vsp,
    get_operating_mode_bin,
    estimate_fuel_rate,
)


class TestVSPModel(unittest.TestCase):
    """Tests for Vehicle Specific Power calculations."""

    def test_vsp_idle(self):
        """VSP at idle (speed=0, accel=0) should be near zero."""
        vsp = calculate_vsp(speed_mps=0.0, accel=0.0)
        self.assertAlmostEqual(vsp, 0.0, places=2)

    def test_vsp_cruising_60kmh(self):
        """VSP at 60 km/h steady cruise (no acceleration) should be positive."""
        speed_mps = 60.0 / 3.6  # ~16.67 m/s
        vsp = calculate_vsp(speed_mps=speed_mps, accel=0.0)
        # At cruise, VSP is mainly rolling resistance + aero drag
        self.assertGreater(vsp, 0.0)
        self.assertLess(vsp, 20.0)  # Should be moderate

    def test_vsp_hard_acceleration(self):
        """VSP during hard acceleration should be high."""
        speed_mps = 40.0 / 3.6  # ~11.1 m/s
        vsp = calculate_vsp(speed_mps=speed_mps, accel=3.0)  # 3 m/s^2
        self.assertGreater(vsp, 20.0)

    def test_vsp_deceleration(self):
        """VSP during deceleration can be negative."""
        speed_mps = 50.0 / 3.6
        vsp = calculate_vsp(speed_mps=speed_mps, accel=-2.0)
        self.assertLess(vsp, 0.0)

    def test_vsp_uphill(self):
        """VSP on uphill grade should be higher than flat."""
        speed_mps = 60.0 / 3.6
        vsp_flat = calculate_vsp(speed_mps=speed_mps, accel=0.0, grade=0.0)
        vsp_uphill = calculate_vsp(speed_mps=speed_mps, accel=0.0, grade=5.0)
        self.assertGreater(vsp_uphill, vsp_flat)

    def test_operating_mode_idle(self):
        """Very low speed should map to idle bin 0."""
        bin_id = get_operating_mode_bin(vsp=0.0, speed_mps=0.0)
        self.assertEqual(bin_id, 0)

    def test_operating_mode_braking(self):
        """Negative VSP at speed should map to bin 1 (braking)."""
        bin_id = get_operating_mode_bin(vsp=-5.0, speed_mps=10.0)
        self.assertEqual(bin_id, 1)

    def test_operating_mode_low_vsp(self):
        """Low positive VSP should map to bin 11."""
        bin_id = get_operating_mode_bin(vsp=1.5, speed_mps=10.0)
        self.assertEqual(bin_id, 11)

    def test_operating_mode_high_vsp(self):
        """High VSP (>=30) should map to bin 28."""
        bin_id = get_operating_mode_bin(vsp=35.0, speed_mps=20.0)
        self.assertEqual(bin_id, 28)

    def test_fuel_rate_positive(self):
        """Fuel rate should be positive for normal driving."""
        rate = estimate_fuel_rate(vsp=10.0, speed_mps=16.67)
        self.assertGreater(rate, 0.0)

    def test_fuel_rate_idle_low(self):
        """Fuel rate at idle should be a small positive value."""
        rate = estimate_fuel_rate(vsp=0.0, speed_mps=0.0)
        self.assertGreater(rate, 0.0)  # Should be ~2.0 L/100km idle baseline
        self.assertLess(rate, 10.0)

    def test_fuel_rate_increases_with_vsp(self):
        """Higher VSP should generally mean higher fuel rate."""
        rate_low = estimate_fuel_rate(vsp=5.0, speed_mps=10.0)
        rate_high = estimate_fuel_rate(vsp=25.0, speed_mps=20.0)
        self.assertGreater(rate_high, rate_low)


if __name__ == "__main__":
    unittest.main()
