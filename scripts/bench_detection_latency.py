"""
Smart PUC — Detection Latency Benchmark: SmartPUC vs Periodic PUC
=================================================================

Monte Carlo simulation comparing SmartPUC continuous monitoring against
6-monthly periodic PUC tests. Simulates 1000 vehicles over 12 months
with three cohorts: clean (70%), gradual degradation (20%), and sudden
failure (10%).

Usage
-----
    python scripts/bench_detection_latency.py --vehicles 1000 --months 12 --seed 42 \
        --output docs/detection_latency_report.json

The experiment is deterministic under a fixed seed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Make sibling packages importable when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.ces_constants import (
    CES_PASS_CEILING,
    CES_WEIGHTS,
    BSVI_THRESHOLDS_PETROL,
)

# ---------------------------------------------------------------------------
# Inline degradation model (simplified COPERT 5)
# ---------------------------------------------------------------------------

POLLUTANTS = ["co2", "co", "nox", "hc", "pm25"]

# Monthly driving: ~1500 km/month (Indian average urban commuter)
KM_PER_MONTH = 1500


def simple_degradation_factor(pollutant: str, mileage_km: float,
                              standard: str = "bs6") -> float:
    """Inline COPERT 5 degradation for the benchmark."""
    rates = {
        "co2": 0.000002,
        "co": 0.000004,
        "nox": 0.000003,
        "hc": 0.000006,
        "pm25": 0.000002,
    }
    cap = 160000
    return 1.0 + rates.get(pollutant, 0) * min(mileage_km, cap)


# ---------------------------------------------------------------------------
# Baseline emission model (fraction of BSVI threshold)
# ---------------------------------------------------------------------------

def baseline_emissions(mileage_km: float, rng: random.Random) -> Dict[str, float]:
    """Return baseline emissions as fraction of BSVI threshold.

    A brand-new BS-VI vehicle operates at roughly 40-60% of its
    threshold. Normal COPERT aging pushes this up gradually.
    """
    fractions: Dict[str, float] = {}
    for p in POLLUTANTS:
        # Base fraction: 0.4-0.6 of threshold (randomized per vehicle)
        base = rng.uniform(0.40, 0.60)
        # Apply normal COPERT degradation
        deg = simple_degradation_factor(p, mileage_km)
        fractions[p] = base * deg
    return fractions


def compute_ces(fractions: Dict[str, float]) -> float:
    """Compute CES from pollutant fractions (each already in [0, inf) of threshold)."""
    ces = 0.0
    for p, w in CES_WEIGHTS.items():
        ces += w * fractions.get(p, 0.0)
    return ces


# ---------------------------------------------------------------------------
# Degradation scenarios
# ---------------------------------------------------------------------------

GRADUAL_TYPES = ["catalyst_aging", "o2_sensor_drift", "egr_valve_wear"]
SUDDEN_TYPES = ["catalyst_removal", "dpf_removal", "egr_failure", "injector_fouling"]


def apply_gradual_degradation(
    fractions: Dict[str, float],
    months_since_onset: int,
    rate_multiplier: float,
    degradation_type: str,
) -> Dict[str, float]:
    """Apply accelerated gradual degradation to emission fractions."""
    result = dict(fractions)
    # Each degradation type emphasises different pollutants
    profiles = {
        "catalyst_aging": {"co": 2.0, "nox": 1.8, "hc": 2.5, "pm25": 1.2, "co2": 1.1},
        "o2_sensor_drift": {"co": 2.5, "nox": 1.5, "hc": 2.0, "pm25": 1.0, "co2": 1.3},
        "egr_valve_wear": {"co": 1.3, "nox": 3.0, "hc": 1.5, "pm25": 1.5, "co2": 1.2},
    }
    profile = profiles.get(degradation_type, profiles["catalyst_aging"])

    for p in POLLUTANTS:
        # Gradual increase: rate_multiplier * profile weight * months
        monthly_increase = 0.03 * rate_multiplier * profile.get(p, 1.0)
        result[p] += monthly_increase * months_since_onset
    return result


def apply_sudden_failure(
    fractions: Dict[str, float],
    failure_type: str,
) -> Dict[str, float]:
    """Apply sudden failure — immediate large emission spike."""
    result = dict(fractions)
    spikes = {
        "catalyst_removal": {"co": 3.0, "nox": 2.5, "hc": 4.0, "pm25": 1.5, "co2": 1.2},
        "dpf_removal": {"co": 1.2, "nox": 1.3, "hc": 1.5, "pm25": 5.0, "co2": 1.0},
        "egr_failure": {"co": 1.5, "nox": 4.0, "hc": 1.8, "pm25": 2.0, "co2": 1.1},
        "injector_fouling": {"co": 2.0, "nox": 1.8, "hc": 2.5, "pm25": 2.5, "co2": 1.5},
    }
    spike = spikes.get(failure_type, spikes["catalyst_removal"])
    for p in POLLUTANTS:
        result[p] *= spike.get(p, 1.0)
    return result


# ---------------------------------------------------------------------------
# Vehicle simulation
# ---------------------------------------------------------------------------

def simulate_vehicle(
    vehicle_id: int,
    cohort: str,  # "clean", "gradual", "sudden"
    n_months: int,
    rng: random.Random,
) -> Dict[str, Any]:
    """Simulate one vehicle over n_months, returning detection results."""
    # Vehicle-level randomization
    initial_mileage = rng.uniform(0, 50000)

    # Degradation parameters
    onset_month: Optional[int] = None
    degradation_type: Optional[str] = None
    rate_multiplier: float = 1.0

    if cohort == "gradual":
        onset_month = rng.randint(1, min(10, n_months))
        degradation_type = rng.choice(GRADUAL_TYPES)
        rate_multiplier = rng.uniform(2.0, 5.0)
    elif cohort == "sudden":
        onset_month = rng.randint(1, min(11, n_months))
        degradation_type = rng.choice(SUDDEN_TYPES)

    # Month-by-month simulation
    monthly_ces: List[float] = []
    smartpuc_detection_month: Optional[int] = None
    puc_detection_month: Optional[int] = None

    for month in range(1, n_months + 1):
        mileage = initial_mileage + month * KM_PER_MONTH

        # Baseline emissions (normal aging)
        fractions = baseline_emissions(mileage, rng)

        # Apply degradation if active
        if cohort == "gradual" and onset_month is not None and month >= onset_month:
            months_since = month - onset_month
            fractions = apply_gradual_degradation(
                fractions, months_since, rate_multiplier, degradation_type
            )
        elif cohort == "sudden" and onset_month is not None and month >= onset_month:
            fractions = apply_sudden_failure(fractions, degradation_type)

        ces = compute_ces(fractions)
        monthly_ces.append(ces)

        # SmartPUC detection: CES crosses 1.0 OR month-to-month delta > 0.15
        if smartpuc_detection_month is None:
            if ces >= CES_PASS_CEILING:
                smartpuc_detection_month = month
            elif len(monthly_ces) >= 2:
                delta = monthly_ces[-1] - monthly_ces[-2]
                if delta > 0.15:
                    smartpuc_detection_month = month

    # PUC detection: scheduled at months 6 and 12 (semi-annual)
    puc_months = [m for m in [6, 12] if m <= n_months]
    for pm in puc_months:
        if monthly_ces[pm - 1] >= CES_PASS_CEILING:
            puc_detection_month = pm
            break

    return {
        "vehicle_id": vehicle_id,
        "cohort": cohort,
        "degradation_type": degradation_type,
        "onset_month": onset_month,
        "rate_multiplier": round(rate_multiplier, 2) if cohort == "gradual" else None,
        "monthly_ces": [round(c, 4) for c in monthly_ces],
        "smartpuc_detection_month": smartpuc_detection_month,
        "puc_detection_month": puc_detection_month,
    }


# ---------------------------------------------------------------------------
# Monte Carlo orchestrator
# ---------------------------------------------------------------------------

def run_simulation(
    n_vehicles: int,
    n_months: int,
    seed: int,
) -> Dict[str, Any]:
    """Run the full Monte Carlo simulation."""
    rng = random.Random(seed)
    t0 = time.time()

    # Assign cohorts: 70% clean, 20% gradual, 10% sudden
    cohorts: List[str] = []
    for _ in range(n_vehicles):
        r = rng.random()
        if r < 0.70:
            cohorts.append("clean")
        elif r < 0.90:
            cohorts.append("gradual")
        else:
            cohorts.append("sudden")

    results: List[Dict[str, Any]] = []
    for i, cohort in enumerate(cohorts):
        results.append(simulate_vehicle(i, cohort, n_months, rng))

    elapsed = time.time() - t0

    # -----------------------------------------------------------------------
    # Compute metrics
    # -----------------------------------------------------------------------
    clean_vehicles = [r for r in results if r["cohort"] == "clean"]
    degrading_vehicles = [r for r in results if r["cohort"] in ("gradual", "sudden")]
    gradual_vehicles = [r for r in results if r["cohort"] == "gradual"]
    sudden_vehicles = [r for r in results if r["cohort"] == "sudden"]

    # SmartPUC false positive rate on clean vehicles
    clean_flagged = sum(1 for r in clean_vehicles if r["smartpuc_detection_month"] is not None)
    clean_fp_rate = clean_flagged / len(clean_vehicles) if clean_vehicles else 0.0

    # Advantage days for degrading vehicles
    advantage_days: List[float] = []
    caught_before_puc = 0
    never_caught_by_puc = 0

    for r in degrading_vehicles:
        sp = r["smartpuc_detection_month"]
        puc = r["puc_detection_month"]

        if puc is None:
            never_caught_by_puc += 1

        if sp is not None and puc is not None:
            adv = (puc - sp) * 30  # approximate days
            advantage_days.append(adv)
            if sp < puc:
                caught_before_puc += 1
        elif sp is not None and puc is None:
            # SmartPUC caught it but PUC never did
            caught_before_puc += 1
            # Advantage is at least until end of simulation
            adv = (n_months - sp) * 30
            advantage_days.append(adv)

    mean_adv = sum(advantage_days) / len(advantage_days) if advantage_days else 0.0
    sorted_adv = sorted(advantage_days)
    median_adv = (sorted_adv[len(sorted_adv) // 2] if sorted_adv else 0.0)

    pct_caught_before = caught_before_puc / len(degrading_vehicles) if degrading_vehicles else 0.0
    pct_never_caught = never_caught_by_puc / len(degrading_vehicles) if degrading_vehicles else 0.0

    # Per-failure-type breakdown
    type_breakdown: Dict[str, Dict[str, Any]] = {}
    for r in degrading_vehicles:
        ft = r["degradation_type"]
        if ft not in type_breakdown:
            type_breakdown[ft] = {
                "count": 0,
                "smartpuc_caught": 0,
                "puc_caught": 0,
                "advantage_days_list": [],
            }
        tb = type_breakdown[ft]
        tb["count"] += 1
        if r["smartpuc_detection_month"] is not None:
            tb["smartpuc_caught"] += 1
        if r["puc_detection_month"] is not None:
            tb["puc_caught"] += 1
        sp = r["smartpuc_detection_month"]
        puc = r["puc_detection_month"]
        if sp is not None:
            if puc is not None:
                tb["advantage_days_list"].append((puc - sp) * 30)
            else:
                tb["advantage_days_list"].append((n_months - sp) * 30)

    type_summary = {}
    for ft, tb in type_breakdown.items():
        adv_list = tb["advantage_days_list"]
        type_summary[ft] = {
            "count": tb["count"],
            "smartpuc_detection_rate": tb["smartpuc_caught"] / tb["count"] if tb["count"] else 0.0,
            "puc_detection_rate": tb["puc_caught"] / tb["count"] if tb["count"] else 0.0,
            "mean_advantage_days": sum(adv_list) / len(adv_list) if adv_list else 0.0,
        }

    report = {
        "config": {
            "n_vehicles": n_vehicles,
            "n_months": n_months,
            "seed": seed,
            "cohort_distribution": {
                "clean": len(clean_vehicles),
                "gradual": len(gradual_vehicles),
                "sudden": len(sudden_vehicles),
            },
        },
        "metrics": {
            "mean_advantage_days": round(mean_adv, 1),
            "median_advantage_days": round(median_adv, 1),
            "pct_caught_before_puc": round(pct_caught_before * 100, 1),
            "pct_never_caught_by_puc": round(pct_never_caught * 100, 1),
            "clean_false_positive_rate": round(clean_fp_rate * 100, 2),
        },
        "per_failure_type": type_summary,
        "runtime_seconds": round(elapsed, 2),
    }

    return report


def print_summary(report: Dict[str, Any]) -> None:
    """Print human-readable summary."""
    print()
    print("=" * 70)
    print("SmartPUC vs Periodic PUC — Detection Latency Benchmark")
    print("=" * 70)
    cfg = report["config"]
    print(f"  Vehicles : {cfg['n_vehicles']}")
    print(f"  Months   : {cfg['n_months']}")
    print(f"  Seed     : {cfg['seed']}")
    print(f"  Cohorts  : clean={cfg['cohort_distribution']['clean']}, "
          f"gradual={cfg['cohort_distribution']['gradual']}, "
          f"sudden={cfg['cohort_distribution']['sudden']}")
    print()

    m = report["metrics"]
    print("KEY METRICS")
    print("-" * 70)
    print(f"  Mean advantage (days):          {m['mean_advantage_days']}")
    print(f"  Median advantage (days):        {m['median_advantage_days']}")
    print(f"  Caught before PUC (%):          {m['pct_caught_before_puc']}%")
    print(f"  Never caught by PUC (%):        {m['pct_never_caught_by_puc']}%")
    print(f"  Clean false-positive rate (%):  {m['clean_false_positive_rate']}%")
    print()

    print("PER-FAILURE-TYPE BREAKDOWN")
    print("-" * 70)
    for ft, info in report["per_failure_type"].items():
        print(f"  {ft}:")
        print(f"    Count:               {info['count']}")
        print(f"    SmartPUC detection:   {info['smartpuc_detection_rate']:.1%}")
        print(f"    PUC detection:        {info['puc_detection_rate']:.1%}")
        print(f"    Mean advantage (d):   {info['mean_advantage_days']:.1f}")
    print()
    print(f"  Runtime: {report['runtime_seconds']:.2f}s")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SmartPUC vs PUC detection latency benchmark"
    )
    parser.add_argument("--vehicles", type=int, default=1000,
                        help="Number of vehicles (default 1000)")
    parser.add_argument("--months", type=int, default=12,
                        help="Simulation duration in months (default 12)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default 42)")
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(__file__), "..", "docs",
                             "detection_latency_report.json"),
        help="Output JSON path",
    )
    args = parser.parse_args()

    report = run_simulation(args.vehicles, args.months, args.seed)
    print_summary(report)

    out = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Full report written to {out}")


if __name__ == "__main__":
    main()
