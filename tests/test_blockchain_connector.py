"""
Tests for the Blockchain Connector module (utility/parsing functions only).

Tests the scaling constants, WLTC phase mapping, and the _parse_record()
static method. Does NOT test actual blockchain connectivity since that
requires a running Ganache instance.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from backend.blockchain_connector import (
    SCALE_POLLUTANT,
    SCALE_SCORE,
    WLTC_PHASES,
    BlockchainConnector,
)


class TestScalingConstants(unittest.TestCase):
    """Tests that scaling constants match the Solidity contract expectations."""

    def test_scale_pollutant_value(self):
        """SCALE_POLLUTANT should be 1000 (3 decimal places for pollutants)."""
        self.assertEqual(SCALE_POLLUTANT, 1000)

    def test_scale_score_value(self):
        """SCALE_SCORE should be 10000 (4 decimal places for CES and fraud scores)."""
        self.assertEqual(SCALE_SCORE, 10000)

    def test_pollutant_scaling_roundtrip(self):
        """Scaling a float by SCALE_POLLUTANT and dividing back should preserve 3 decimals."""
        original = 123.456
        scaled = int(round(original * SCALE_POLLUTANT))
        recovered = scaled / SCALE_POLLUTANT
        self.assertAlmostEqual(recovered, original, places=3)

    def test_score_scaling_roundtrip(self):
        """Scaling a float by SCALE_SCORE and dividing back should preserve 4 decimals."""
        original = 0.8765
        scaled = int(round(original * SCALE_SCORE))
        recovered = scaled / SCALE_SCORE
        self.assertAlmostEqual(recovered, original, places=4)


class TestWLTCPhases(unittest.TestCase):
    """Tests that the WLTC phase mapping is complete and correct."""

    def test_all_phases_present(self):
        """WLTC_PHASES should contain keys 0 through 3."""
        for phase_id in range(4):
            self.assertIn(phase_id, WLTC_PHASES)

    def test_phase_names(self):
        """Each WLTC phase should have the expected name."""
        self.assertEqual(WLTC_PHASES[0], "Low")
        self.assertEqual(WLTC_PHASES[1], "Medium")
        self.assertEqual(WLTC_PHASES[2], "High")
        self.assertEqual(WLTC_PHASES[3], "Extra High")

    def test_exactly_four_phases(self):
        """WLTC_PHASES should contain exactly 4 entries."""
        self.assertEqual(len(WLTC_PHASES), 4)


class TestParseRecord(unittest.TestCase):
    """Tests for BlockchainConnector._parse_record() static method."""

    def test_parse_record_pass(self):
        """_parse_record() should correctly convert a Solidity tuple with status=True (PASS)."""
        mock_record = (
            "MH12AB1234",   # [0] vehicleId
            150000,         # [1] co2Level (150.0 * 1000)
            1200,           # [2] coLevel (1.2 * 1000)
            400,            # [3] noxLevel (0.4 * 1000)
            50,             # [4] hcLevel (0.05 * 1000)
            5,              # [5] pm25Level (0.005 * 1000)
            8500,           # [6] cesScore (0.85 * 10000)
            1200,           # [7] fraudScore (0.12 * 10000)
            12500,          # [8] vspValue (12.5 * 1000)
            2,              # [9] wltcPhase
            1700000000,     # [10] timestamp
            True,           # [11] status (pass)
        )

        result = BlockchainConnector._parse_record(mock_record)

        self.assertEqual(result["vehicleId"], "MH12AB1234")
        self.assertEqual(result["co2Level"], 150000)
        self.assertEqual(result["coLevel"], 1200)
        self.assertEqual(result["noxLevel"], 400)
        self.assertEqual(result["hcLevel"], 50)
        self.assertEqual(result["pm25Level"], 5)
        self.assertEqual(result["cesScore"], 8500)
        self.assertEqual(result["fraudScore"], 1200)
        self.assertEqual(result["vspValue"], 12500)
        self.assertEqual(result["wltcPhase"], 2)
        self.assertEqual(result["timestamp"], 1700000000)
        self.assertEqual(result["status"], "PASS")

    def test_parse_record_fail(self):
        """_parse_record() should return status='FAIL' when the status flag is False."""
        mock_record = (
            "DL01XY0001",
            200000,
            2500,
            800,
            100,
            15,
            15000,
            8000,
            25000,
            3,
            1700001000,
            False,
        )

        result = BlockchainConnector._parse_record(mock_record)

        self.assertEqual(result["vehicleId"], "DL01XY0001")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["wltcPhase"], 3)

    def test_parse_record_returns_all_keys(self):
        """_parse_record() result should contain all expected keys."""
        mock_record = ("V1", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, True)
        result = BlockchainConnector._parse_record(mock_record)

        expected_keys = {
            "vehicleId", "co2Level", "coLevel", "noxLevel", "hcLevel",
            "pm25Level", "cesScore", "fraudScore", "vspValue", "wltcPhase",
            "timestamp", "status",
        }
        self.assertEqual(set(result.keys()), expected_keys)


if __name__ == "__main__":
    unittest.main()
