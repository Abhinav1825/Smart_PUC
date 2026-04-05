"""
Tests for the OBD-II PID Adapter module.

Covers PID decoding for all supported PIDs (RPM, Speed, IAT, MAF, Fuel Rate),
error handling for unknown/insufficient PIDs, fuel rate conversions,
MAF-to-fuel-rate estimation, and full/partial OBD frame parsing.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations"))

from integrations.obd_adapter import (
    decode_pid,
    fuel_rate_lph_to_l100km,
    maf_to_fuel_rate,
    parse_obd_frame,
    OBD_PIDS,
)


class TestDecodePid(unittest.TestCase):
    """Tests for decode_pid() across all supported OBD-II PIDs."""

    def test_rpm_pid_0x0C(self):
        """PID 0x0C (RPM): ((A*256)+B)/4 should decode correctly."""
        # A=0x1A (26), B=0xF8 (248) -> ((26*256)+248)/4 = (6904)/4 = 1726.0
        result = decode_pid(0x0C, [0x1A, 0xF8])
        self.assertAlmostEqual(result, 1726.0)

    def test_rpm_zero(self):
        """PID 0x0C with zero bytes should return 0.0 RPM."""
        result = decode_pid(0x0C, [0, 0])
        self.assertAlmostEqual(result, 0.0)

    def test_speed_pid_0x0D(self):
        """PID 0x0D (Speed): A should decode to vehicle speed in km/h."""
        result = decode_pid(0x0D, [80])
        self.assertAlmostEqual(result, 80.0)

    def test_speed_zero(self):
        """PID 0x0D with A=0 should return 0.0 km/h."""
        result = decode_pid(0x0D, [0])
        self.assertAlmostEqual(result, 0.0)

    def test_intake_air_temp_pid_0x0F(self):
        """PID 0x0F (IAT): A-40 should decode intake air temperature."""
        # A=65 -> 65-40 = 25 deg C
        result = decode_pid(0x0F, [65])
        self.assertAlmostEqual(result, 25.0)

    def test_intake_air_temp_below_zero(self):
        """PID 0x0F with A=20 should return negative temperature (-20 deg C)."""
        result = decode_pid(0x0F, [20])
        self.assertAlmostEqual(result, -20.0)

    def test_maf_pid_0x10(self):
        """PID 0x10 (MAF): ((A*256)+B)/100 should decode MAF rate in g/s."""
        # A=1, B=0x2C (44) -> ((256)+44)/100 = 300/100 = 3.0
        result = decode_pid(0x10, [1, 0x2C])
        self.assertAlmostEqual(result, 3.0)

    def test_fuel_rate_pid_0x5E(self):
        """PID 0x5E (Fuel Rate): ((A*256)+B)/20 should decode fuel rate in L/h."""
        # A=0, B=100 -> 100/20 = 5.0 L/h
        result = decode_pid(0x5E, [0, 100])
        self.assertAlmostEqual(result, 5.0)

    def test_unknown_pid_raises_value_error(self):
        """decode_pid() should raise ValueError for an unknown PID."""
        with self.assertRaises(ValueError) as ctx:
            decode_pid(0xFF, [0, 0])
        self.assertIn("Unknown OBD-II PID", str(ctx.exception))

    def test_insufficient_bytes_raises_value_error_two_byte_pid(self):
        """decode_pid() should raise ValueError when too few bytes are given for a 2-byte PID."""
        # PID 0x0C requires 2 bytes
        with self.assertRaises(ValueError) as ctx:
            decode_pid(0x0C, [0x1A])
        self.assertIn("requires 2 bytes", str(ctx.exception))

    def test_insufficient_bytes_raises_value_error_empty(self):
        """decode_pid() should raise ValueError when an empty byte list is given."""
        with self.assertRaises(ValueError):
            decode_pid(0x0D, [])


class TestFuelRateConversion(unittest.TestCase):
    """Tests for fuel_rate_lph_to_l100km() conversion."""

    def test_normal_conversion(self):
        """Normal conversion: 5 L/h at 50 km/h -> 10.0 L/100km."""
        result = fuel_rate_lph_to_l100km(5.0, 50.0)
        self.assertAlmostEqual(result, 10.0)

    def test_zero_speed_returns_zero(self):
        """Zero speed should return 0.0 to avoid division by zero."""
        result = fuel_rate_lph_to_l100km(5.0, 0.0)
        self.assertAlmostEqual(result, 0.0)

    def test_very_low_speed_returns_zero(self):
        """Speed below 1.0 km/h should return 0.0."""
        result = fuel_rate_lph_to_l100km(5.0, 0.5)
        self.assertAlmostEqual(result, 0.0)

    def test_high_speed(self):
        """High speed conversion: 6 L/h at 120 km/h -> 5.0 L/100km."""
        result = fuel_rate_lph_to_l100km(6.0, 120.0)
        self.assertAlmostEqual(result, 5.0)


class TestMafToFuelRate(unittest.TestCase):
    """Tests for maf_to_fuel_rate() estimation."""

    def test_default_afr(self):
        """With default AFR (14.7), fuel rate = MAF / 14.7."""
        result = maf_to_fuel_rate(14.7)
        self.assertAlmostEqual(result, 1.0)

    def test_custom_afr(self):
        """With custom AFR, fuel rate = MAF / AFR."""
        result = maf_to_fuel_rate(30.0, afr=15.0)
        self.assertAlmostEqual(result, 2.0)

    def test_zero_maf(self):
        """Zero MAF should return zero fuel rate."""
        result = maf_to_fuel_rate(0.0)
        self.assertAlmostEqual(result, 0.0)


class TestParseObdFrame(unittest.TestCase):
    """Tests for parse_obd_frame() with complete and partial PID sets."""

    def test_complete_pids(self):
        """parse_obd_frame() with all PIDs should return a fully populated dict."""
        raw_pids = {
            0x0D: [60],           # 60 km/h
            0x0C: [0x0C, 0x00],   # ((12*256)+0)/4 = 768 RPM
            0x5E: [0, 100],       # 100/20 = 5.0 L/h
            0x0F: [65],           # 65-40 = 25 deg C
        }
        result = parse_obd_frame(raw_pids, speed_prev=50.0, dt=1.0)

        self.assertEqual(result["speed"], 60.0)
        self.assertEqual(result["rpm"], 768)
        self.assertEqual(result["fuel_type"], "petrol")
        self.assertEqual(result["ambient_temp"], 25.0)
        # fuel_rate = (5.0 / 60) * 100 = 8.33 L/100km
        self.assertAlmostEqual(result["fuel_rate"], 8.33, places=2)
        # acceleration = (60-50) / (3.6 * 1) = 2.778 m/s^2
        self.assertAlmostEqual(result["acceleration"], 2.778, places=3)

    def test_partial_pids_missing_fuel_and_temp(self):
        """parse_obd_frame() with only speed and RPM should use defaults for missing PIDs."""
        raw_pids = {
            0x0D: [40],           # 40 km/h
            0x0C: [0x08, 0x00],   # ((8*256)+0)/4 = 512 RPM
        }
        result = parse_obd_frame(raw_pids, speed_prev=40.0, dt=1.0)

        self.assertEqual(result["speed"], 40.0)
        self.assertEqual(result["rpm"], 512)
        self.assertEqual(result["fuel_rate"], 0.0)  # no fuel PID
        self.assertEqual(result["ambient_temp"], 25.0)  # default

    def test_maf_fallback_for_fuel_rate(self):
        """When PID 0x5E is missing but 0x10 is present, fuel rate uses MAF estimate."""
        raw_pids = {
            0x0D: [80],           # 80 km/h
            0x0C: [0x10, 0x00],   # some RPM
            0x10: [0, 200],       # MAF = 200/100 = 2.0 g/s
        }
        result = parse_obd_frame(raw_pids, speed_prev=80.0, dt=1.0)

        # fuel_gs = 2.0 / 14.7 = ~0.136 g/s
        # fuel_lph = 0.136 * 3600 / 740 = ~0.661 L/h
        # fuel_rate = (0.661 / 80) * 100 = ~0.83 L/100km
        self.assertGreater(result["fuel_rate"], 0.0)
        self.assertLess(result["fuel_rate"], 5.0)

    def test_empty_pids(self):
        """parse_obd_frame() with empty dict should return defaults."""
        result = parse_obd_frame({}, speed_prev=0.0, dt=1.0)
        self.assertEqual(result["speed"], 0.0)
        self.assertEqual(result["rpm"], 0)
        self.assertEqual(result["fuel_rate"], 0.0)
        self.assertEqual(result["ambient_temp"], 25.0)


if __name__ == "__main__":
    unittest.main()
