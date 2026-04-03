"""
Integration tests for the full SmartPUC pipeline.

Tests the complete flow: WLTC Simulator -> VSP Model -> Multi-Pollutant
Emission Engine -> Fraud Detector -> LSTM Predictor, verifying that all
modules work together end-to-end without blockchain dependency.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from backend.simulator import WLTCSimulator, WLTCPhase
from backend.emission_engine import calculate_emissions, BSVI_THRESHOLDS
from physics.vsp_model import calculate_vsp, get_operating_mode_bin
from ml.fraud_detector import FraudDetector
from ml.lstm_predictor import create_predictor


class TestFullPipeline(unittest.TestCase):
    """End-to-end integration tests for the SmartPUC pipeline."""

    def setUp(self):
        self.sim = WLTCSimulator(vehicle_id="INTEG_TEST", dt=1.0)
        self.fraud = FraudDetector()
        # Train IF on simulator data
        training_data = []
        tmp_sim = WLTCSimulator(vehicle_id="TRAIN", dt=1.0)
        for _ in range(200):
            r = tmp_sim.generate_reading()
            training_data.append({
                "speed": r["speed"], "rpm": float(r["rpm"]),
                "fuel_rate": r["fuel_rate"],
                "acceleration": r.get("acceleration", 0.0),
                "co2": 130.0, "vsp": 5.0,
            })
        self.fraud.fit(training_data)
        self.predictor = create_predictor(use_lstm=False)

    def test_full_pipeline_100_readings(self):
        """Run 100 readings through the full pipeline without errors."""
        for i in range(100):
            # Step 1: Simulator generates telemetry
            reading = self.sim.generate_reading()
            self.assertIn("speed", reading)
            self.assertIn("rpm", reading)
            self.assertIn("fuel_rate", reading)
            self.assertIn("acceleration", reading)
            self.assertIn("phase", reading)

            speed = reading["speed"]
            accel = reading["acceleration"]
            speed_mps = speed / 3.6

            # Step 2: VSP + operating mode
            vsp = calculate_vsp(speed_mps, accel)
            op_bin = get_operating_mode_bin(vsp, speed_mps)
            self.assertIsInstance(vsp, float)
            self.assertIn(op_bin, [0, 1, 11, 21, 22, 23, 24, 25, 27, 28])

            # Step 3: Multi-pollutant emission calculation
            emission = calculate_emissions(
                speed_kmh=speed,
                acceleration=accel,
                rpm=reading["rpm"],
                fuel_rate=reading["fuel_rate"],
                operating_mode_bin=op_bin,
                cold_start=(i < 30),
            )
            self.assertIn("co2_g_per_km", emission)
            self.assertIn("ces_score", emission)
            self.assertIn("status", emission)
            self.assertGreaterEqual(emission["co2_g_per_km"], 0)
            self.assertGreaterEqual(emission["ces_score"], 0)

            # Step 4: Fraud detection
            fraud_reading = {
                "speed": speed, "rpm": reading["rpm"],
                "fuel_rate": reading["fuel_rate"],
                "acceleration": accel,
                "co2": emission["co2_g_per_km"],
                "vsp": vsp,
            }
            fraud_result = self.fraud.analyze(fraud_reading)
            self.assertIn("fraud_score", fraud_result)
            self.assertGreaterEqual(fraud_result["fraud_score"], 0.0)
            self.assertLessEqual(fraud_result["fraud_score"], 1.0)

            # Step 5: LSTM predictor update
            self.predictor.update({
                "speed": speed, "rpm": float(reading["rpm"]),
                "fuel_rate": reading["fuel_rate"],
                "acceleration": accel,
                "co2": emission["co2_g_per_km"],
                "nox": emission["nox_g_per_km"],
                "vsp": vsp,
                "ces_score": emission["ces_score"],
            })

        # After 100 readings, predictor should have predictions
        prediction = self.predictor.predict_next()
        self.assertIsNotNone(prediction)
        self.assertIn("predictions", prediction)
        self.assertEqual(len(prediction["predictions"]), 5)

    def test_all_phases_produce_valid_emissions(self):
        """Each WLTC phase should produce valid emission readings."""
        phase_seen = set()
        for _ in range(1800):
            reading = self.sim.generate_reading()
            phase_seen.add(reading["phase"])

            if reading["speed"] > 2.0:
                speed_mps = reading["speed"] / 3.6
                vsp = calculate_vsp(speed_mps, reading["acceleration"])
                op_bin = get_operating_mode_bin(vsp, speed_mps)

                emission = calculate_emissions(
                    speed_kmh=reading["speed"],
                    acceleration=reading["acceleration"],
                    rpm=reading["rpm"],
                    fuel_rate=reading["fuel_rate"],
                    operating_mode_bin=op_bin,
                )

                # All pollutants should be non-negative
                for key in ["co2_g_per_km", "co_g_per_km", "nox_g_per_km", "hc_g_per_km", "pm25_g_per_km"]:
                    self.assertGreaterEqual(emission[key], 0.0, f"{key} negative at phase {reading['phase']}")

        # All 4 phases should have been visited
        self.assertEqual(phase_seen, {"Low", "Medium", "High", "Extra High"})

    def test_fraud_detects_tampered_readings(self):
        """Fraud detector should flag physically impossible readings."""
        # Feed some normal readings first for temporal context
        for _ in range(5):
            self.fraud.analyze({
                "speed": 60, "rpm": 2500, "fuel_rate": 7.0,
                "acceleration": 0.3, "co2": 130, "vsp": 5.0,
            })

        # Now send an obviously tampered reading
        result = self.fraud.analyze({
            "speed": 300, "rpm": 0, "fuel_rate": -5.0,
            "acceleration": 10.0, "co2": 5, "vsp": 50,
        })
        # Should be flagged (physics override kicks in)
        self.assertTrue(result["is_fraud"])
        self.assertEqual(result["severity"], "HIGH")

    def test_ces_is_truly_multipollutant(self):
        """CES should have meaningful contributions from non-CO2 pollutants."""
        emission = calculate_emissions(
            speed_kmh=60, acceleration=0.5, rpm=2500,
            fuel_rate=7.0, operating_mode_bin=22,
        )
        ces = emission["ces_score"]
        co2_contrib = (emission["co2_g_per_km"] / BSVI_THRESHOLDS["co2"]) * 0.35
        nox_contrib = (emission["nox_g_per_km"] / BSVI_THRESHOLDS["nox"]) * 0.30

        # NOx should contribute at least 5% of the total CES
        nox_fraction = nox_contrib / ces if ces > 0 else 0
        self.assertGreater(nox_fraction, 0.05,
                           f"NOx contributes only {nox_fraction:.1%} of CES -- CES is CO2-dominated")

    def test_cold_start_increases_emissions(self):
        """Cold-start should increase CO and HC vs warm operation."""
        warm = calculate_emissions(
            speed_kmh=40, acceleration=0, rpm=1800,
            fuel_rate=8.0, operating_mode_bin=21, cold_start=False,
        )
        cold = calculate_emissions(
            speed_kmh=40, acceleration=0, rpm=1800,
            fuel_rate=8.0, operating_mode_bin=21, cold_start=True,
        )
        self.assertGreater(cold["co_g_per_km"], warm["co_g_per_km"])
        self.assertGreater(cold["hc_g_per_km"], warm["hc_g_per_km"])

    def test_edge_case_zero_speed(self):
        """Speed=0 should produce capped emissions, not crash or infinity."""
        emission = calculate_emissions(
            speed_kmh=0, acceleration=0, rpm=700,
            fuel_rate=2.0, operating_mode_bin=0,
        )
        self.assertLessEqual(emission["co2_g_per_km"], 300)
        self.assertGreaterEqual(emission["co2_g_per_km"], 0)
        self.assertIsNotNone(emission["ces_score"])

    def test_edge_case_max_speed(self):
        """Very high speed should produce valid (not NaN/Inf) emissions."""
        emission = calculate_emissions(
            speed_kmh=200, acceleration=0, rpm=5500,
            fuel_rate=15.0, operating_mode_bin=28,
        )
        self.assertGreater(emission["co2_g_per_km"], 0)
        self.assertFalse(emission["co2_g_per_km"] != emission["co2_g_per_km"])  # not NaN

    def test_edge_case_extreme_temperature(self):
        """Extreme temperatures should modify NOx but not crash."""
        cold = calculate_emissions(
            speed_kmh=60, acceleration=0, rpm=2000,
            fuel_rate=6.0, operating_mode_bin=21, ambient_temp=-10.0,
        )
        hot = calculate_emissions(
            speed_kmh=60, acceleration=0, rpm=2000,
            fuel_rate=6.0, operating_mode_bin=21, ambient_temp=50.0,
        )
        self.assertGreater(hot["nox_g_per_km"], cold["nox_g_per_km"])
        self.assertGreater(cold["nox_g_per_km"], 0)
        self.assertGreater(hot["nox_g_per_km"], 0)


if __name__ == "__main__":
    unittest.main()
