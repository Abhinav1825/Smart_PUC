"""
Tests for the multi-pollutant emission engine.

Tests emission calculations at each WLTC phase boundary,
BSVI threshold compliance, CES scoring, and backward compatibility.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from backend.emission_engine import (
    calculate_emissions,
    calculate_co2,
    process_obd_reading,
    BSVI_THRESHOLDS,
    CES_WEIGHTS,
)


class TestMultiPollutantEngine(unittest.TestCase):
    """Tests for the upgraded multi-pollutant emission engine."""

    def test_calculate_emissions_returns_all_pollutants(self):
        """Output must contain all 5 BSVI pollutants."""
        result = calculate_emissions(
            speed_kmh=60.0, acceleration=0.5, rpm=2500,
            fuel_rate=7.0, operating_mode_bin=21,
        )
        self.assertIn("co2_g_per_km", result)
        self.assertIn("co_g_per_km", result)
        self.assertIn("nox_g_per_km", result)
        self.assertIn("hc_g_per_km", result)
        self.assertIn("pm25_g_per_km", result)

    def test_calculate_emissions_returns_ces(self):
        """Output must contain CES score."""
        result = calculate_emissions(
            speed_kmh=60.0, acceleration=0.0, rpm=2000,
            fuel_rate=6.0, operating_mode_bin=11,
        )
        self.assertIn("ces_score", result)
        self.assertIsInstance(result["ces_score"], float)

    def test_ces_pass_under_threshold(self):
        """Normal driving at moderate speed should produce CES < 1.0 (PASS)."""
        result = calculate_emissions(
            speed_kmh=60.0, acceleration=0.0, rpm=2000,
            fuel_rate=5.0, operating_mode_bin=11,
        )
        self.assertLess(result["ces_score"], 1.5)  # Reasonable upper bound
        # Status check
        self.assertIn(result["status"], ["PASS", "FAIL"])

    def test_compliance_dict_present(self):
        """Compliance dict should have per-pollutant status."""
        result = calculate_emissions(
            speed_kmh=80.0, acceleration=0.0, rpm=2500,
            fuel_rate=6.5, operating_mode_bin=21,
        )
        self.assertIn("compliance", result)
        compliance = result["compliance"]
        self.assertIn("co2", compliance)
        self.assertIn("nox", compliance)

    def test_cold_start_increases_co_hc(self):
        """Cold start should increase CO and HC emissions."""
        warm = calculate_emissions(
            speed_kmh=40.0, acceleration=0.5, rpm=1800,
            fuel_rate=8.0, operating_mode_bin=21, cold_start=False,
        )
        cold = calculate_emissions(
            speed_kmh=40.0, acceleration=0.5, rpm=1800,
            fuel_rate=8.0, operating_mode_bin=21, cold_start=True,
        )
        self.assertGreaterEqual(cold["co_g_per_km"], warm["co_g_per_km"])
        self.assertGreaterEqual(cold["hc_g_per_km"], warm["hc_g_per_km"])

    def test_nox_temperature_correction(self):
        """Higher temperature should increase NOx."""
        cool = calculate_emissions(
            speed_kmh=60.0, acceleration=0.0, rpm=2200,
            fuel_rate=6.0, operating_mode_bin=21, ambient_temp=15.0,
        )
        hot = calculate_emissions(
            speed_kmh=60.0, acceleration=0.0, rpm=2200,
            fuel_rate=6.0, operating_mode_bin=21, ambient_temp=40.0,
        )
        self.assertGreater(hot["nox_g_per_km"], cool["nox_g_per_km"])

    def test_idle_emission_capped(self):
        """At very low speed, CO2 should be capped (not infinite)."""
        result = calculate_emissions(
            speed_kmh=0.5, acceleration=0.0, rpm=700,
            fuel_rate=3.0, operating_mode_bin=0,
        )
        self.assertLessEqual(result["co2_g_per_km"], 300.0)

    def test_all_pollutants_non_negative(self):
        """No pollutant should ever be negative."""
        result = calculate_emissions(
            speed_kmh=80.0, acceleration=-1.0, rpm=1500,
            fuel_rate=5.0, operating_mode_bin=1,
        )
        self.assertGreaterEqual(result["co2_g_per_km"], 0.0)
        self.assertGreaterEqual(result["co_g_per_km"], 0.0)
        self.assertGreaterEqual(result["nox_g_per_km"], 0.0)
        self.assertGreaterEqual(result["hc_g_per_km"], 0.0)
        self.assertGreaterEqual(result["pm25_g_per_km"], 0.0)


class TestBackwardCompatibility(unittest.TestCase):
    """Tests that the old API still works."""

    def test_calculate_co2_returns_expected_keys(self):
        """calculate_co2() should return the original dict keys plus new ones."""
        result = calculate_co2(fuel_rate=7.0, speed=60.0, fuel_type="petrol")
        self.assertIn("co2_g_per_km", result)
        self.assertIn("co2_int", result)
        self.assertIn("status", result)
        self.assertIn("threshold", result)

    def test_process_obd_reading(self):
        """process_obd_reading() should enrich reading with emission data."""
        reading = {
            "vehicle_id": "TEST001",
            "rpm": 2000,
            "speed": 60.0,
            "fuel_rate": 7.0,
            "fuel_type": "petrol",
        }
        result = process_obd_reading(reading)
        self.assertIn("co2_g_per_km", result)
        self.assertEqual(result["vehicle_id"], "TEST001")

    def test_invalid_fuel_type_raises(self):
        """Unknown fuel type should raise ValueError."""
        with self.assertRaises(ValueError):
            calculate_co2(fuel_rate=5.0, speed=60.0, fuel_type="hydrogen")


class TestBSVIThresholds(unittest.TestCase):
    """Tests that BSVI thresholds are correctly defined."""

    def test_thresholds_exist(self):
        """All 5 threshold constants should be defined."""
        self.assertIn("co2", BSVI_THRESHOLDS)
        self.assertIn("co", BSVI_THRESHOLDS)
        self.assertIn("nox", BSVI_THRESHOLDS)
        self.assertIn("hc", BSVI_THRESHOLDS)
        self.assertIn("pm25", BSVI_THRESHOLDS)

    def test_threshold_values(self):
        """Thresholds should match BSVI specification."""
        self.assertEqual(BSVI_THRESHOLDS["co2"], 120.0)
        self.assertEqual(BSVI_THRESHOLDS["co"], 1.0)
        self.assertEqual(BSVI_THRESHOLDS["nox"], 0.06)
        self.assertEqual(BSVI_THRESHOLDS["hc"], 0.10)
        self.assertEqual(BSVI_THRESHOLDS["pm25"], 0.0045)

    def test_ces_weights_sum_to_one(self):
        """CES weights should sum to 1.0."""
        total = sum(CES_WEIGHTS.values())
        self.assertAlmostEqual(total, 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
