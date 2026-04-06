"""Tests for physics.degradation_model — COPERT 5 degradation model."""

from __future__ import annotations

import os
import sys

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from physics.degradation_model import DegradationModel, map_bs_to_euro


@pytest.fixture
def model() -> DegradationModel:
    """Return a DegradationModel loaded from the project's JSON."""
    return DegradationModel()


# ── map_bs_to_euro ────────────────────────────────────────────────────────────

class TestMapBsToEuro:
    def test_bs6_petrol(self):
        assert map_bs_to_euro("BS6", "petrol") == "euro6_petrol"

    def test_bsvi_petrol(self):
        assert map_bs_to_euro("BSVI", "petrol") == "euro6_petrol"

    def test_bs4_diesel(self):
        assert map_bs_to_euro("BS4", "diesel") == "euro4_diesel"

    def test_bsiv_diesel(self):
        assert map_bs_to_euro("BS-IV", "diesel") == "euro4_diesel"

    def test_case_insensitive(self):
        assert map_bs_to_euro("bs6", "Petrol") == "euro6_petrol"

    def test_unknown_bs_raises(self):
        with pytest.raises(ValueError, match="Cannot map"):
            map_bs_to_euro("BS3", "petrol")

    def test_unknown_fuel_raises(self):
        with pytest.raises(ValueError, match="Unknown fuel type"):
            map_bs_to_euro("BS6", "cng")


# ── degradation_factor ────────────────────────────────────────────────────────

class TestDegradationFactor:
    def test_zero_mileage_returns_one(self, model: DegradationModel):
        """At 0 km, factor should be exactly 1.0 (no degradation)."""
        for p in ("co2", "co", "nox", "hc", "pm25"):
            assert model.degradation_factor(p, 0.0, "euro6_petrol") == 1.0

    def test_factor_increases_with_mileage(self, model: DegradationModel):
        """Factor should increase linearly with mileage."""
        f_50k = model.degradation_factor("co", 50000, "euro6_petrol")
        f_100k = model.degradation_factor("co", 100000, "euro6_petrol")
        assert f_50k > 1.0
        assert f_100k > f_50k

    def test_linear_relationship(self, model: DegradationModel):
        """Doubling mileage should roughly double the excess above 1.0."""
        f_40k = model.degradation_factor("nox", 40000, "euro6_petrol")
        f_80k = model.degradation_factor("nox", 80000, "euro6_petrol")
        excess_40k = f_40k - 1.0
        excess_80k = f_80k - 1.0
        assert abs(excess_80k - 2 * excess_40k) < 1e-9

    def test_caps_at_cap_km(self, model: DegradationModel):
        """Factor should not increase beyond cap_km (160k for euro6)."""
        f_cap = model.degradation_factor("hc", 160000, "euro6_petrol")
        f_over = model.degradation_factor("hc", 250000, "euro6_petrol")
        assert f_cap == f_over

    def test_euro4_higher_rates(self, model: DegradationModel):
        """Euro 4 should degrade faster than Euro 6 at same mileage."""
        f_euro6 = model.degradation_factor("co", 50000, "euro6_petrol")
        f_euro4 = model.degradation_factor("co", 50000, "euro4_petrol")
        assert f_euro4 > f_euro6

    def test_negative_mileage_treated_as_zero(self, model: DegradationModel):
        """Negative mileage should be clamped to 0."""
        assert model.degradation_factor("co2", -1000, "euro6_petrol") == 1.0


# ── apply_degradation ────────────────────────────────────────────────────────

class TestApplyDegradation:
    @pytest.fixture
    def base_emissions(self) -> dict:
        return {
            "co2_g_per_km": 100.0,
            "co_g_per_km": 0.5,
            "nox_g_per_km": 0.04,
            "hc_g_per_km": 0.06,
            "pm25_g_per_km": 0.003,
            "ces_score": 0.7,
            "status": "PASS",
        }

    def test_all_pollutants_increase(self, model: DegradationModel, base_emissions: dict):
        degraded = model.apply_degradation(base_emissions, 80000, "euro6_petrol")
        for key in ("co2_g_per_km", "co_g_per_km", "nox_g_per_km",
                     "hc_g_per_km", "pm25_g_per_km"):
            assert degraded[key] >= base_emissions[key]

    def test_non_pollutant_keys_preserved(self, model: DegradationModel, base_emissions: dict):
        degraded = model.apply_degradation(base_emissions, 50000, "euro6_petrol")
        assert degraded["status"] == "PASS"

    def test_zero_mileage_no_change(self, model: DegradationModel, base_emissions: dict):
        degraded = model.apply_degradation(base_emissions, 0, "euro6_petrol")
        for key in ("co2_g_per_km", "co_g_per_km", "nox_g_per_km",
                     "hc_g_per_km", "pm25_g_per_km"):
            assert degraded[key] == base_emissions[key]

    def test_correct_multiplication(self, model: DegradationModel, base_emissions: dict):
        """Verify the multiplication is consistent with degradation_factor."""
        mileage = 60000
        standard = "euro6_petrol"
        degraded = model.apply_degradation(base_emissions, mileage, standard)
        for key, pollutant in [("co2_g_per_km", "co2"), ("co_g_per_km", "co")]:
            factor = model.degradation_factor(pollutant, mileage, standard)
            expected = base_emissions[key] * factor
            assert abs(degraded[key] - expected) < 1e-10


# ── apply_sudden_failure ─────────────────────────────────────────────────────

class TestApplySuddenFailure:
    @pytest.fixture
    def base_emissions(self) -> dict:
        return {
            "co2_g_per_km": 100.0,
            "co_g_per_km": 0.5,
            "nox_g_per_km": 0.04,
            "hc_g_per_km": 0.06,
            "pm25_g_per_km": 0.003,
        }

    def test_catalyst_removal_large_co_hc(self, model: DegradationModel, base_emissions: dict):
        result = model.apply_sudden_failure(base_emissions, "catalyst_removal")
        # CO should be multiplied by 5.0
        assert abs(result["co_g_per_km"] - 0.5 * 5.0) < 1e-10
        # HC should be multiplied by 8.0
        assert abs(result["hc_g_per_km"] - 0.06 * 8.0) < 1e-10

    def test_dpf_removal_massive_pm(self, model: DegradationModel, base_emissions: dict):
        result = model.apply_sudden_failure(base_emissions, "dpf_removal_diesel")
        assert result["pm25_g_per_km"] == pytest.approx(0.003 * 50.0)

    def test_original_not_mutated(self, model: DegradationModel, base_emissions: dict):
        original_co = base_emissions["co_g_per_km"]
        model.apply_sudden_failure(base_emissions, "catalyst_removal")
        assert base_emissions["co_g_per_km"] == original_co


# ── simulate_degradation_trajectory ──────────────────────────────────────────

class TestTrajectory:
    @pytest.fixture
    def base_emissions(self) -> dict:
        return {
            "co2_g_per_km": 100.0,
            "co_g_per_km": 0.5,
            "nox_g_per_km": 0.04,
            "hc_g_per_km": 0.06,
            "pm25_g_per_km": 0.003,
            "ces_score": 0.7,
        }

    def test_trajectory_length(self, model: DegradationModel, base_emissions: dict):
        traj = model.simulate_degradation_trajectory(
            base_emissions, 0, 10000, step_km=2000,
        )
        # 0, 2000, 4000, 6000, 8000, 10000 = 6 points
        assert len(traj) == 6

    def test_trajectory_monotonically_increases(self, model: DegradationModel, base_emissions: dict):
        traj = model.simulate_degradation_trajectory(
            base_emissions, 0, 50000, step_km=5000,
        )
        for p in ("co2", "co", "nox", "hc", "pm25"):
            values = [row[p] for row in traj]
            for i in range(1, len(values)):
                assert values[i] >= values[i - 1]

    def test_failure_injection(self, model: DegradationModel, base_emissions: dict):
        traj = model.simulate_degradation_trajectory(
            base_emissions, 0, 20000, step_km=5000,
            failure_at_km=10000, failure_type="catalyst_removal",
        )
        # Before failure (0, 5000) CO should be lower than after (10000, 15000, 20000)
        co_before = traj[1]["co"]  # 5000 km
        co_after = traj[2]["co"]   # 10000 km (failure kicks in)
        assert co_after > co_before * 2  # catalyst removal = 5x


# ── estimate_time_to_failure ─────────────────────────────────────────────────

class TestEstimateTimeToFailure:
    def test_returns_dict_with_expected_keys(self, model: DegradationModel):
        base = {
            "co2_g_per_km": 110.0,
            "co_g_per_km": 0.8,
            "nox_g_per_km": 0.05,
            "hc_g_per_km": 0.08,
            "pm25_g_per_km": 0.004,
        }
        result = model.estimate_time_to_failure(base, 50000, "euro6_petrol")
        assert "months_to_failure" in result
        assert "projected_mileage_at_failure" in result
        assert "dominant_pollutant" in result
        assert "confidence" in result

    def test_high_emitter_fails_quickly(self, model: DegradationModel):
        """A vehicle already near thresholds should fail soon."""
        base = {
            "co2_g_per_km": 118.0,
            "co_g_per_km": 0.95,
            "nox_g_per_km": 0.055,
            "hc_g_per_km": 0.095,
            "pm25_g_per_km": 0.004,
        }
        result = model.estimate_time_to_failure(base, 80000, "euro6_petrol")
        if result["months_to_failure"] is not None:
            assert result["months_to_failure"] <= 60  # within 5 years

    def test_clean_vehicle_survives_longer(self, model: DegradationModel):
        """A very clean vehicle should either never fail or fail much later."""
        base = {
            "co2_g_per_km": 60.0,
            "co_g_per_km": 0.2,
            "nox_g_per_km": 0.01,
            "hc_g_per_km": 0.02,
            "pm25_g_per_km": 0.001,
        }
        result = model.estimate_time_to_failure(base, 10000, "euro6_petrol")
        # Should either never fail or take many months
        if result["months_to_failure"] is not None:
            assert result["months_to_failure"] > 12
        else:
            assert result["confidence"] == "low"
