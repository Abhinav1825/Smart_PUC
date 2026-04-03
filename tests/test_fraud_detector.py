"""
Tests for the ML fraud detection module.

Tests physics constraint validation, temporal consistency checking,
and the ensemble fraud detector with both clean and tampered readings.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.fraud_detector import (
    PhysicsConstraintValidator,
    TemporalConsistencyChecker,
    FraudDetector,
)


class TestPhysicsConstraintValidator(unittest.TestCase):
    """Tests for physics-based fraud validation."""

    def setUp(self):
        self.validator = PhysicsConstraintValidator()

    def test_clean_reading_low_score(self):
        """A physically plausible reading should have low violation score."""
        reading = {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5}
        score, violations = self.validator.validate(reading)
        self.assertLess(score, 0.5)

    def test_zero_rpm_while_moving(self):
        """RPM=0 while speed>5 should be flagged."""
        reading = {"speed": 60.0, "rpm": 0, "fuel_rate": 7.0, "acceleration": 0.0}
        score, violations = self.validator.validate(reading)
        self.assertGreater(score, 0.0)
        self.assertTrue(any("RPM" in v for v in violations))

    def test_impossible_acceleration(self):
        """Acceleration > 4 m/s^2 should be flagged."""
        reading = {"speed": 60.0, "rpm": 4000, "fuel_rate": 10.0, "acceleration": 5.0}
        score, violations = self.validator.validate(reading)
        self.assertGreater(score, 0.0)

    def test_negative_fuel_rate(self):
        """Negative fuel rate should be flagged."""
        reading = {"speed": 30.0, "rpm": 1500, "fuel_rate": -1.0, "acceleration": 0.0}
        score, violations = self.validator.validate(reading)
        self.assertGreater(score, 0.0)

    def test_extreme_rpm(self):
        """RPM > 7000 should be flagged."""
        reading = {"speed": 80.0, "rpm": 8000, "fuel_rate": 7.0, "acceleration": 0.0}
        score, violations = self.validator.validate(reading)
        self.assertGreater(score, 0.0)


class TestTemporalConsistencyChecker(unittest.TestCase):
    """Tests for temporal consistency checks."""

    def setUp(self):
        self.checker = TemporalConsistencyChecker()

    def test_consistent_sequence(self):
        """A smooth sequence of readings should have low score."""
        for speed in range(0, 60, 5):
            reading = {"speed": float(speed), "rpm": 700 + speed * 30, "fuel_rate": 5.0 + speed * 0.05}
            score, _ = self.checker.update_and_check(reading)
        self.assertLess(score, 0.5)

    def test_speed_teleportation(self):
        """A sudden jump from 0 to 100 km/h should be flagged."""
        # Fill window with slow readings
        for _ in range(5):
            self.checker.update_and_check({"speed": 10.0, "rpm": 1000, "fuel_rate": 5.0})
        # Sudden jump
        score, violations = self.checker.update_and_check({"speed": 100.0, "rpm": 1000, "fuel_rate": 5.0})
        self.assertGreater(score, 0.0)


class TestFraudDetector(unittest.TestCase):
    """Tests for the ensemble fraud detector."""

    def setUp(self):
        self.detector = FraudDetector()

    def test_clean_reading(self):
        """A normal reading should not be flagged as fraud."""
        reading = {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5}
        result = self.detector.analyze(reading)
        self.assertIn("fraud_score", result)
        self.assertIn("is_fraud", result)
        self.assertIn("severity", result)
        self.assertFalse(result["is_fraud"])

    def test_obvious_fraud(self):
        """A clearly impossible reading should be flagged."""
        reading = {"speed": 300.0, "rpm": 0, "fuel_rate": -5.0, "acceleration": 10.0}
        result = self.detector.analyze(reading)
        self.assertTrue(result["is_fraud"])
        self.assertEqual(result["severity"], "HIGH")

    def test_severity_levels(self):
        """Severity should be LOW, MEDIUM, or HIGH."""
        reading = {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5}
        result = self.detector.analyze(reading)
        self.assertIn(result["severity"], ["LOW", "MEDIUM", "HIGH"])

    def test_fraud_score_range(self):
        """Fraud score should be between 0.0 and 1.0."""
        reading = {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5}
        result = self.detector.analyze(reading)
        self.assertGreaterEqual(result["fraud_score"], 0.0)
        self.assertLessEqual(result["fraud_score"], 1.0)

    def test_components_in_result(self):
        """Result should include individual component scores."""
        reading = {"speed": 60.0, "rpm": 2500, "fuel_rate": 7.0, "acceleration": 0.5}
        result = self.detector.analyze(reading)
        self.assertIn("components", result)


if __name__ == "__main__":
    unittest.main()
