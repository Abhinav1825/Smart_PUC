"""Tests for physics.detection_power — statistical detection power module."""

from __future__ import annotations

import pytest

from physics.detection_power import (
    cumulative_detection_power,
    detection_power_comparison_table,
    monthly_detection_power,
    readings_threshold,
    time_to_equivalence,
)


class TestCumulativeDetectionPower:
    """Tests for cumulative_detection_power()."""

    def test_zero_readings_returns_zero(self):
        assert cumulative_detection_power(0, 0.02) == 0.0

    def test_single_reading_with_p_one(self):
        assert cumulative_detection_power(1, 1.0) == 1.0

    def test_94_readings_exceeds_puc(self):
        """94 readings at p=0.02 should exceed P_puc=0.85."""
        p = cumulative_detection_power(94, 0.02)
        assert p > 0.85

    def test_monotonic_increase(self):
        """Detection power should increase with more readings."""
        prev = 0.0
        for n in [1, 10, 50, 100, 500]:
            curr = cumulative_detection_power(n, 0.02)
            assert curr > prev
            prev = curr

    def test_approaches_one(self):
        """With many readings, detection power approaches 1.0."""
        p = cumulative_detection_power(10000, 0.02)
        assert p > 0.9999

    def test_zero_p_returns_zero(self):
        assert cumulative_detection_power(100, 0.0) == 0.0


class TestReadingsThreshold:
    """Tests for readings_threshold()."""

    def test_threshold_85_at_p002(self):
        assert readings_threshold(0.85, 0.02) == 94

    def test_threshold_99_exceeds_94(self):
        n = readings_threshold(0.99, 0.02)
        assert n > 94

    def test_verification(self):
        """The threshold N should actually achieve the target power."""
        n = readings_threshold(0.85, 0.02)
        p = cumulative_detection_power(n, 0.02)
        assert p >= 0.85

    def test_one_less_is_below(self):
        """N-1 readings should be below the target."""
        n = readings_threshold(0.85, 0.02)
        p = cumulative_detection_power(n - 1, 0.02)
        assert p < 0.85


class TestTimeToEquivalence:
    """Tests for time_to_equivalence()."""

    def test_default_returns_94_readings(self):
        result = time_to_equivalence()
        assert result["n_readings"] == 94

    def test_default_about_1_57_minutes(self):
        result = time_to_equivalence()
        assert 1.5 <= result["minutes"] <= 1.6

    def test_p_obd_exceeds_p_puc(self):
        result = time_to_equivalence()
        assert result["p_obd_at_threshold"] >= result["p_puc"]

    def test_keys_present(self):
        result = time_to_equivalence()
        expected_keys = {"n_readings", "seconds", "minutes",
                         "p_obd_at_threshold", "p_puc"}
        assert set(result.keys()) == expected_keys


class TestDetectionPowerComparisonTable:
    """Tests for detection_power_comparison_table()."""

    def test_default_returns_nine_rows(self):
        table = detection_power_comparison_table()
        assert len(table) == 9

    def test_custom_durations(self):
        table = detection_power_comparison_table(
            durations_minutes=[1, 5, 10]
        )
        assert len(table) == 3

    def test_obd_better_flag(self):
        """After ~2 min at p=0.02, OBD should be better than PUC."""
        table = detection_power_comparison_table()
        # 2 minutes = 120 readings
        two_min_row = [r for r in table if r["minutes"] == 2][0]
        assert two_min_row["obd_better"] is True

    def test_one_minute_not_better(self):
        """1 minute at p=0.02 should NOT yet beat PUC."""
        table = detection_power_comparison_table()
        one_min_row = [r for r in table if r["minutes"] == 1][0]
        assert one_min_row["obd_better"] is False

    def test_row_keys(self):
        table = detection_power_comparison_table()
        expected = {"minutes", "readings", "p_obd", "p_puc",
                    "obd_better", "advantage_pct"}
        assert set(table[0].keys()) == expected


class TestMonthlyDetectionPower:
    """Tests for monthly_detection_power()."""

    def test_p_detect_practically_certain(self):
        result = monthly_detection_power()
        assert result["p_detect"] > 0.9999

    def test_total_readings_correct(self):
        result = monthly_detection_power()
        # 2 trips/day * 30 days * 30 min * 60 readings/min = 108,000
        assert result["total_readings"] == 108000

    def test_keys_present(self):
        result = monthly_detection_power()
        expected = {"total_readings", "p_detect", "p_detect_formatted",
                    "trips", "driving_hours"}
        assert set(result.keys()) == expected
