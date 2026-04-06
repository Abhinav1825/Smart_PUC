"""
SmartPUC -- Weekly Micro-Assessment Engine
==========================================

Generates weekly emission health reports for each monitored vehicle.
Each report summarises the vehicle's emission trajectory, degradation
risk, and compliance tier status over the past 7 days.
"""

from __future__ import annotations

import datetime
import json
import time
from typing import Any, Dict, List, Optional

import numpy as np


# ── Tier classification thresholds (mirror of on-chain ComplianceTier) ──
# Gold:   avg CES < 0.40 and zero violations in period
# Silver: avg CES < 0.70
# Bronze: avg CES < 1.00
# Unclassified: everything else (or not enough data)

_TIER_THRESHOLDS = [
    ("Gold", 0.40),
    ("Silver", 0.70),
    ("Bronze", 1.00),
]

# Tier -> PUC validity in days (mirrors PUCCertificate.sol Phase 2)
TIER_VALIDITY_DAYS = {
    "Gold": 730,
    "Silver": 365,
    "Bronze": 180,
    "Unclassified": 180,
}


def _classify_tier(ces_mean: float, violation_count: int = 0) -> str:
    """Classify a vehicle into a compliance tier."""
    if ces_mean <= 0 or violation_count < 0:
        return "Unclassified"
    for tier_name, threshold in _TIER_THRESHOLDS:
        if ces_mean < threshold:
            if tier_name == "Gold" and violation_count > 0:
                return "Silver"
            return tier_name
    return "Unclassified"


class MicroAssessmentEngine:
    """Generates periodic health reports from telemetry history."""

    def __init__(self, persistence_store: Any, calibration_model: Any = None):
        self._store = persistence_store
        self._calibration = calibration_model

    # ── Public API ──────────────────────────────────────────────────────

    def generate_weekly_report(
        self,
        vehicle_id: str,
        as_of_date: Optional[str] = None,
    ) -> dict:
        """Generate a 7-day health report for a vehicle.

        Steps:
        1. Query last 7 days of telemetry from persistence
        2. Compute CES statistics (mean, max, p95, slope via linear regression)
        3. Compute per-pollutant means
        4. Compute driving behaviour score (idle fraction, hard-accel count)
        5. Assess degradation risk: 'low', 'medium', 'high' based on CES slope
        6. If calibration model available, compute calibrated CES and compare
        """
        now_iso = as_of_date or datetime.datetime.now(
            datetime.timezone.utc
        ).strftime("%Y-%m-%d")

        # 1. Pull telemetry
        telemetry = self._store.telemetry_for_vehicle(vehicle_id, limit=5000)
        # Filter to last 7 days
        cutoff = int(time.time()) - 7 * 86400
        if as_of_date:
            try:
                dt = datetime.datetime.fromisoformat(as_of_date)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                cutoff = int(dt.timestamp()) - 7 * 86400
            except ValueError:
                pass

        readings = []
        for t in telemetry:
            observed = t.get("observed_at", 0)
            if observed >= cutoff:
                r = t.get("reading", {})
                if isinstance(r, str):
                    try:
                        r = json.loads(r)
                    except Exception:
                        r = {}
                readings.append({**r, "_observed_at": observed})

        readings.sort(key=lambda x: x.get("_observed_at", 0))

        readings_count = len(readings)

        # 2. CES statistics
        ces_values = [float(r.get("ces_score", 0)) for r in readings]
        if ces_values:
            ces_mean = float(np.mean(ces_values))
            ces_max = float(np.max(ces_values))
            ces_p95 = float(np.percentile(ces_values, 95)) if len(ces_values) >= 2 else ces_max
        else:
            ces_mean = 0.0
            ces_max = 0.0
            ces_p95 = 0.0

        # CES slope via linear regression (per day)
        ces_slope = 0.0
        if len(ces_values) >= 2:
            timestamps = [float(r.get("_observed_at", 0)) for r in readings]
            days = np.array([(t - timestamps[0]) / 86400.0 for t in timestamps])
            try:
                slope, _ = np.polyfit(days, ces_values, 1)
                ces_slope = float(slope)
            except (np.linalg.LinAlgError, ValueError):
                ces_slope = 0.0

        # 3. Per-pollutant means
        pollutant_keys = {
            "co2_mean": "co2_g_per_km",
            "nox_mean": "nox_g_per_km",
            "co_mean": "co_g_per_km",
            "hc_mean": "hc_g_per_km",
            "pm25_mean": "pm25_g_per_km",
        }
        pollutants: Dict[str, float] = {}
        pollutant_slopes: Dict[str, float] = {}
        for out_key, src_key in pollutant_keys.items():
            vals = [float(r.get(src_key, 0)) for r in readings]
            pollutants[out_key] = float(np.mean(vals)) if vals else 0.0
            # Compute slope for recommendations
            if len(vals) >= 2:
                timestamps = [float(r.get("_observed_at", 0)) for r in readings]
                days = np.array([(t - timestamps[0]) / 86400.0 for t in timestamps])
                try:
                    s, _ = np.polyfit(days, vals, 1)
                    pollutant_slopes[out_key] = float(s)
                except (np.linalg.LinAlgError, ValueError):
                    pollutant_slopes[out_key] = 0.0
            else:
                pollutant_slopes[out_key] = 0.0

        # 4. Driving behaviour score
        driving_score = self._compute_driving_score(readings)

        # 5. Degradation risk
        if ces_slope > 0.05:
            degradation_risk = "high"
        elif ces_slope > 0.02:
            degradation_risk = "medium"
        else:
            degradation_risk = "low"

        # Projected failure days (CES >= 1.0 means FAIL)
        projected_failure_days: Optional[int] = None
        if ces_slope > 0.001 and ces_mean < 1.0:
            days_to_fail = (1.0 - ces_mean) / ces_slope
            projected_failure_days = max(1, int(days_to_fail))

        # Violation count in period
        violation_count = sum(
            1 for r in readings if r.get("status") == "FAIL"
        )

        # Tier
        tier = _classify_tier(ces_mean, violation_count)

        # 6. Calibration (optional)
        calibrated_ces_mean: Optional[float] = None
        if self._calibration is not None and hasattr(self._calibration, "is_trained"):
            if self._calibration.is_trained and readings:
                try:
                    mid_reading = readings[len(readings) // 2]
                    cal_result = self._calibration.calibrate(mid_reading)
                    calibrated_ces_mean = cal_result.get("calibrated_ces")
                except Exception:
                    pass

        # Recommendations
        recommendations = self._generate_recommendations(
            ces_slope, degradation_risk, pollutant_slopes, pollutants
        )

        report = {
            "vehicle_id": vehicle_id,
            "report_date": now_iso,
            "period_days": 7,
            "readings_count": readings_count,
            "ces_mean": round(ces_mean, 4),
            "ces_slope": round(ces_slope, 6),
            "ces_max": round(ces_max, 4),
            "ces_p95": round(ces_p95, 4),
            "pollutants": {k: round(v, 6) for k, v in pollutants.items()},
            "driving_score": round(driving_score, 1),
            "degradation_risk": degradation_risk,
            "tier": tier,
            "projected_failure_days": projected_failure_days,
            "calibrated_ces_mean": (
                round(calibrated_ces_mean, 4) if calibrated_ces_mean is not None else None
            ),
            "recommendations": recommendations,
            "generated_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
        }

        # Persist the report
        try:
            self._store.store_health_report(vehicle_id, now_iso, {
                "period_days": 7,
                "ces_mean": report["ces_mean"],
                "ces_slope": report["ces_slope"],
                "ces_max": report["ces_max"],
                "co2_mean": pollutants.get("co2_mean", 0),
                "nox_mean": pollutants.get("nox_mean", 0),
                "co_mean": pollutants.get("co_mean", 0),
                "hc_mean": pollutants.get("hc_mean", 0),
                "pm25_mean": pollutants.get("pm25_mean", 0),
                "driving_score": report["driving_score"],
                "degradation_risk": degradation_risk,
                "tier": tier,
            })
        except Exception:
            pass

        # Store degradation event if risk is high
        if degradation_risk == "high":
            try:
                self._store.store_degradation_event(
                    vehicle_id,
                    event_type="ces_slope_high",
                    severity="warning",
                    details={
                        "ces_slope": report["ces_slope"],
                        "ces_mean": report["ces_mean"],
                        "report_date": now_iso,
                    },
                )
            except Exception:
                pass

        return report

    def generate_fleet_summary(
        self, vehicle_ids: Optional[List[str]] = None
    ) -> dict:
        """Aggregate health reports across a fleet."""
        if vehicle_ids is None:
            vehicle_ids = []

        tier_distribution: Dict[str, int] = {
            "Gold": 0, "Silver": 0, "Bronze": 0, "Unclassified": 0,
        }
        ces_values: List[float] = []
        degradation_alerts = 0

        for vid in vehicle_ids:
            reports = self._store.get_health_reports(vid, limit=1)
            if not reports:
                tier_distribution["Unclassified"] += 1
                continue
            latest = reports[0]
            tier = latest.get("tier", "Unclassified")
            if tier in tier_distribution:
                tier_distribution[tier] += 1
            else:
                tier_distribution["Unclassified"] += 1
            ces_val = latest.get("ces_mean")
            if ces_val is not None:
                ces_values.append(float(ces_val))
            if latest.get("degradation_risk") == "high":
                degradation_alerts += 1

        avg_ces = float(np.mean(ces_values)) if ces_values else 0.0
        total = len(vehicle_ids)
        # Fleet health score: 100 * fraction of Gold+Silver / total
        good_count = tier_distribution["Gold"] + tier_distribution["Silver"]
        fleet_health_score = (good_count / total * 100) if total > 0 else 0.0

        return {
            "total_vehicles": total,
            "tier_distribution": tier_distribution,
            "avg_ces": round(avg_ces, 4),
            "degradation_alerts": degradation_alerts,
            "fleet_health_score": round(fleet_health_score, 1),
        }

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _compute_driving_score(readings: List[dict]) -> float:
        """Compute a 0-100 driving behaviour score.

        ``100 - (idle_fraction * 30 + hard_accel_pct * 40 + high_speed_pct * 30)``
        """
        if not readings:
            return 50.0  # neutral default
        n = len(readings)
        idle_count = sum(1 for r in readings if float(r.get("speed", 0)) < 5.0)
        hard_accel_count = sum(
            1 for r in readings if abs(float(r.get("acceleration", 0))) > 3.0
        )
        high_speed_count = sum(
            1 for r in readings if float(r.get("speed", 0)) > 120.0
        )
        idle_fraction = idle_count / n
        hard_accel_pct = hard_accel_count / n
        high_speed_pct = high_speed_count / n
        score = 100.0 - (idle_fraction * 30 + hard_accel_pct * 40 + high_speed_pct * 30)
        return max(0.0, min(100.0, score))

    @staticmethod
    def _generate_recommendations(
        ces_slope: float,
        degradation_risk: str,
        pollutant_slopes: Dict[str, float],
        pollutant_means: Dict[str, float],
    ) -> List[str]:
        """Produce actionable recommendation strings."""
        recs: List[str] = []

        if degradation_risk == "high":
            recs.append(
                "Your emission score is deteriorating rapidly -- schedule a service inspection soon."
            )

        # Pollutant-specific recommendations
        _pollutant_advice = {
            "hc_mean": "Your HC emissions are rising -- inspect spark plugs and ignition system.",
            "co_mean": "CO levels are increasing -- check fuel mixture and catalytic converter.",
            "nox_mean": "NOx trend is upward -- inspect EGR valve and SCR system.",
            "pm25_mean": "Particulate matter is rising -- check DPF and air filter.",
            "co2_mean": "CO2 output is climbing -- consider fuel system and engine tune-up.",
        }

        for key, advice in _pollutant_advice.items():
            slope = pollutant_slopes.get(key, 0)
            if slope > 0.01:
                recs.append(advice)

        if not recs and degradation_risk == "low":
            recs.append("Vehicle emissions are within healthy range. Keep up regular maintenance.")

        return recs
