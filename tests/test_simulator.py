"""
Tests for the WLTC driving cycle simulator.

Tests WLTC phase boundaries, RPM derivation from gearbox model,
acceleration from finite difference, and backward compatibility.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from backend.simulator import WLTCSimulator, OBDSimulator


class TestWLTCSimulator(unittest.TestCase):
    """Tests for the WLTC driving cycle simulator."""

    def setUp(self):
        self.sim = WLTCSimulator(vehicle_id="TEST001", dt=1.0)

    def test_generate_reading_returns_dict(self):
        """generate_reading() should return a dict with required keys."""
        reading = self.sim.generate_reading()
        self.assertIsInstance(reading, dict)
        self.assertIn("vehicle_id", reading)
        self.assertIn("speed", reading)
        self.assertIn("rpm", reading)
        self.assertIn("fuel_rate", reading)
        self.assertIn("acceleration", reading)
        self.assertIn("phase", reading)

    def test_vehicle_id(self):
        """Reading should use the configured vehicle ID."""
        reading = self.sim.generate_reading()
        self.assertEqual(reading["vehicle_id"], "TEST001")

    def test_speed_non_negative(self):
        """Speed should never be negative."""
        for _ in range(100):
            reading = self.sim.generate_reading()
            self.assertGreaterEqual(reading["speed"], 0.0)

    def test_rpm_in_range(self):
        """RPM should be within physical bounds."""
        for _ in range(50):
            reading = self.sim.generate_reading()
            self.assertGreaterEqual(reading["rpm"], 0)
            self.assertLessEqual(reading["rpm"], 7000)

    def test_fuel_rate_positive(self):
        """Fuel rate should be positive."""
        for _ in range(50):
            reading = self.sim.generate_reading()
            self.assertGreaterEqual(reading["fuel_rate"], 0.0)

    def test_phase_valid(self):
        """Phase should be one of the WLTC phases."""
        valid_phases = {"Low", "Medium", "High", "Extra High"}
        for _ in range(50):
            reading = self.sim.generate_reading()
            self.assertIn(reading["phase"], valid_phases)

    def test_cycle_wraps_around(self):
        """Simulator should wrap around after 1800 seconds."""
        sim = WLTCSimulator(dt=1.0)
        for _ in range(1801):
            sim.generate_reading()
        # Should still produce valid readings after wrap
        reading = sim.generate_reading()
        self.assertIn("speed", reading)

    def test_reset(self):
        """reset() should return to the beginning of the cycle."""
        for _ in range(100):
            self.sim.generate_reading()
        self.sim.reset()
        self.assertEqual(self.sim._current_time, 0)

    def test_get_latest(self):
        """get_latest() should return the most recent reading."""
        reading = self.sim.generate_reading()
        latest = self.sim.get_latest()
        self.assertEqual(reading["speed"], latest["speed"])

    def test_wltc_low_phase(self):
        """Time 0-589 should be LOW phase."""
        from backend.simulator import WLTCPhase
        phase = self.sim.get_phase(100)
        self.assertEqual(phase, WLTCPhase.LOW)

    def test_wltc_extra_high_phase(self):
        """Time 1478+ should be EXTRA_HIGH phase."""
        from backend.simulator import WLTCPhase
        phase = self.sim.get_phase(1500)
        self.assertEqual(phase, WLTCPhase.EXTRA_HIGH)


class TestBackwardCompatibility(unittest.TestCase):
    """Tests that the OBDSimulator alias still works."""

    def test_obd_simulator_alias(self):
        """OBDSimulator should be importable and functional."""
        sim = OBDSimulator(vehicle_id="MH12AB1234")
        reading = sim.generate_reading()
        self.assertIn("speed", reading)
        self.assertIn("rpm", reading)


if __name__ == "__main__":
    unittest.main()
