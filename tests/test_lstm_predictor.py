"""
Tests for the LSTM emission predictor module.

Tests the MockPredictor (fallback) and the factory function.
TensorFlow-dependent tests are skipped if TF is not installed.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.lstm_predictor import MockPredictor, create_predictor


class TestMockPredictor(unittest.TestCase):
    """Tests for the MockPredictor (no-TF fallback)."""

    def setUp(self):
        self.predictor = MockPredictor()

    def test_predict_none_without_data(self):
        """predict_next() should return None before window is full."""
        result = self.predictor.predict_next()
        self.assertIsNone(result)

    def test_update_fills_window(self):
        """update() should accumulate readings."""
        reading = {
            "speed": 60.0, "rpm": 2500, "fuel_rate": 7.0,
            "acceleration": 0.5, "co2": 100.0, "nox": 0.03,
            "vsp": 10.0, "ces_score": 0.7,
        }
        for _ in range(20):
            self.predictor.update(reading)

        result = self.predictor.predict_next()
        self.assertIsNotNone(result)

    def test_prediction_structure(self):
        """Prediction should contain expected keys."""
        reading = {
            "speed": 60.0, "rpm": 2500, "fuel_rate": 7.0,
            "acceleration": 0.5, "co2": 100.0, "nox": 0.03,
            "vsp": 10.0, "ces_score": 0.7,
        }
        for _ in range(20):
            self.predictor.update(reading)

        result = self.predictor.predict_next()
        self.assertIn("predictions", result)
        self.assertIn("warning", result)
        self.assertEqual(len(result["predictions"]), 5)

    def test_prediction_values(self):
        """Each prediction step should have co2, nox, ces."""
        reading = {
            "speed": 60.0, "rpm": 2500, "fuel_rate": 7.0,
            "acceleration": 0.5, "co2": 100.0, "nox": 0.03,
            "vsp": 10.0, "ces_score": 0.7,
        }
        for _ in range(20):
            self.predictor.update(reading)

        result = self.predictor.predict_next()
        for pred in result["predictions"]:
            self.assertIn("co2", pred)
            self.assertIn("nox", pred)
            self.assertIn("ces", pred)

    def test_warning_detection(self):
        """High CES readings should trigger a warning."""
        reading = {
            "speed": 60.0, "rpm": 2500, "fuel_rate": 7.0,
            "acceleration": 0.5, "co2": 200.0, "nox": 0.1,
            "vsp": 25.0, "ces_score": 0.90,
        }
        for _ in range(20):
            self.predictor.update(reading)

        result = self.predictor.predict_next()
        self.assertTrue(result["warning"])


class TestCreatePredictor(unittest.TestCase):
    """Tests for the factory function."""

    def test_create_mock(self):
        """create_predictor(use_lstm=False) should return MockPredictor."""
        pred = create_predictor(use_lstm=False)
        self.assertIsInstance(pred, MockPredictor)

    def test_create_default(self):
        """create_predictor() should return some predictor."""
        pred = create_predictor()
        self.assertIsNotNone(pred)


if __name__ == "__main__":
    unittest.main()
