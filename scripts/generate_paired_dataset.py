"""
Smart PUC -- Synthetic Paired Dataset Generator (OBD vs Tailpipe)
=================================================================

Generates a CSV dataset where each row contains both OBD-inferred and
simulated tailpipe emissions for a synthetic vehicle driving a full
certification cycle.  The gap between OBD and tailpipe readings is
modelled using COPERT 5 degradation curves plus Gaussian measurement
noise.

The dataset is designed for training and evaluating models that predict
whether OBD-based monitoring can replace or extend periodic PUC testing.

Usage
-----
    python scripts/generate_paired_dataset.py --vehicles 500 --seed 42 \\
        --output data/paired_dataset_synthetic.csv --cycle wltc

Output columns
--------------
    vehicle_id, vehicle_class, mileage_km, age_years, fuel_type, bs_standard,
    time_s, speed_kmh, rpm, fuel_rate, acceleration, coolant_temp, phase,
    obd_co2, obd_co, obd_nox, obd_hc, obd_pm25, obd_ces,
    tailpipe_co2, tailpipe_co, tailpipe_nox, tailpipe_hc, tailpipe_pm25, tailpipe_ces,
    has_failure, failure_type, failure_onset_s
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional

import numpy as np

# Make sibling packages importable when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.emission_engine import calculate_emissions, BSStandard, CES_WEIGHTS, get_thresholds
from backend.simulator import WLTCSimulator
from physics.degradation_model import DegradationModel, map_bs_to_euro

# ── Vehicle class definitions ────────────────────────────────────────────────

VEHICLE_CLASSES = {
    "Swift":  {"mass": 900,  "desc": "Maruti Suzuki Swift (hatchback)"},
    "i20":    {"mass": 1000, "desc": "Hyundai i20 (premium hatchback)"},
    "Creta":  {"mass": 1300, "desc": "Hyundai Creta (compact SUV)"},
    "Innova": {"mass": 1800, "desc": "Toyota Innova (MPV)"},
}

# Failure types suitable for petrol vs diesel
PETROL_FAILURES = ["catalyst_removal", "o2_sensor_drift", "egr_failure", "injector_fouling"]
DIESEL_FAILURES = ["dpf_removal_diesel", "egr_failure", "injector_fouling", "o2_sensor_drift"]

# ── Column order ─────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "vehicle_id", "vehicle_class", "mileage_km", "age_years",
    "fuel_type", "bs_standard",
    "time_s", "speed_kmh", "rpm", "fuel_rate", "acceleration",
    "coolant_temp", "phase",
    "obd_co2", "obd_co", "obd_nox", "obd_hc", "obd_pm25", "obd_ces",
    "tailpipe_co2", "tailpipe_co", "tailpipe_nox", "tailpipe_hc",
    "tailpipe_pm25", "tailpipe_ces",
    "has_failure", "failure_type", "failure_onset_s",
]

_POLLUTANT_KEYS = ["co2_g_per_km", "co_g_per_km", "nox_g_per_km", "hc_g_per_km", "pm25_g_per_km"]
_SHORT_NAMES = ["co2", "co", "nox", "hc", "pm25"]


def _compute_ces(emissions: dict, fuel_type: str, bs_standard: BSStandard) -> float:
    """Recompute CES from pollutant values and thresholds."""
    thresholds = get_thresholds(fuel_type, bs_standard)
    ces = 0.0
    key_map = {"co2": "co2_g_per_km", "co": "co_g_per_km", "nox": "nox_g_per_km",
               "hc": "hc_g_per_km", "pm25": "pm25_g_per_km"}
    for p, w in CES_WEIGHTS.items():
        val = emissions.get(key_map[p], 0.0)
        thr = thresholds.get(p, 1.0)
        ces += (val / thr) * w
    return round(ces, 4)


def generate_vehicle_profile(rng: random.Random, vehicle_idx: int) -> dict:
    """Randomise a vehicle's static attributes."""
    vehicle_class = rng.choice(list(VEHICLE_CLASSES.keys()))
    fuel_type = "diesel" if rng.random() < 0.20 else "petrol"
    bs_standard = "BS4" if rng.random() < 0.40 else "BS6"
    mileage_km = rng.uniform(10000, 150000)
    age_years = rng.uniform(1, 10)

    # Decide if this vehicle has a sudden failure (10% chance)
    has_failure = rng.random() < 0.10
    failure_type: Optional[str] = None
    failure_onset_frac: Optional[float] = None
    if has_failure:
        candidates = DIESEL_FAILURES if fuel_type == "diesel" else PETROL_FAILURES
        failure_type = rng.choice(candidates)
        failure_onset_frac = rng.uniform(0.3, 0.9)  # fraction of cycle

    return {
        "vehicle_id": f"VEH{vehicle_idx:05d}",
        "vehicle_class": vehicle_class,
        "fuel_type": fuel_type,
        "bs_standard": bs_standard,
        "mileage_km": round(mileage_km, 0),
        "age_years": round(age_years, 1),
        "has_failure": has_failure,
        "failure_type": failure_type,
        "failure_onset_frac": failure_onset_frac,
    }


def run_cycle_for_vehicle(
    profile: dict,
    degradation_model: DegradationModel,
    cycle: str,
    rng: random.Random,
    np_rng: np.random.RandomState,
) -> List[dict]:
    """Run a full driving cycle for one vehicle and return paired rows."""

    sim = WLTCSimulator(vehicle_id=profile["vehicle_id"], dt=1.0, cycle=cycle)
    cycle_length = sim._cycle_length

    bs_enum = BSStandard.BS6 if profile["bs_standard"] == "BS6" else BSStandard.BS4
    euro_std = map_bs_to_euro(profile["bs_standard"], profile["fuel_type"])
    mileage = profile["mileage_km"]
    fuel_type = profile["fuel_type"]

    # Failure onset in seconds
    failure_onset_s: Optional[int] = None
    if profile["has_failure"]:
        failure_onset_s = int(profile["failure_onset_frac"] * cycle_length)

    # Coolant temperature model: starts cold, warms up
    cold_start = True
    coolant_base = rng.uniform(20, 35)  # ambient

    rows: List[dict] = []
    for t in range(cycle_length):
        reading = sim.generate_reading()
        speed = reading["speed"]
        accel = reading["acceleration"]
        rpm = reading["rpm"]
        fuel_rate = reading["fuel_rate"]
        phase = reading["phase"]

        # Coolant temperature ramp (reaches ~90C after ~200s)
        coolant_temp = min(90.0, coolant_base + t * (90.0 - coolant_base) / 200.0)
        is_cold = t < 120

        # ── OBD-inferred emissions (what the OBD system reports) ─────
        obd_result = calculate_emissions(
            speed_kmh=speed,
            acceleration=accel,
            rpm=rpm,
            fuel_rate=fuel_rate,
            fuel_type=fuel_type,
            operating_mode_bin=reading.get("operating_mode_bin", 11),
            ambient_temp=25.0,
            altitude=0.0,
            cold_start=is_cold,
            bs_standard=bs_enum,
        )

        # ── Tailpipe emissions (degraded + noise) ────────────────────
        tailpipe = degradation_model.apply_degradation(
            obd_result, mileage, euro_std,
        )

        # Inject sudden failure if applicable
        if (failure_onset_s is not None
                and profile["failure_type"] is not None
                and t >= failure_onset_s):
            tailpipe = degradation_model.apply_sudden_failure(
                tailpipe, profile["failure_type"],
            )

        # Add Gaussian measurement noise (sigma = 5% of value)
        for key in _POLLUTANT_KEYS:
            val = tailpipe.get(key, 0.0)
            if val > 0:
                noise = np_rng.normal(0, 0.05 * val)
                tailpipe[key] = max(0.0, val + noise)

        # Recompute tailpipe CES
        tailpipe_ces = _compute_ces(tailpipe, fuel_type, bs_enum)

        row = {
            "vehicle_id": profile["vehicle_id"],
            "vehicle_class": profile["vehicle_class"],
            "mileage_km": int(profile["mileage_km"]),
            "age_years": profile["age_years"],
            "fuel_type": fuel_type,
            "bs_standard": profile["bs_standard"],
            "time_s": t,
            "speed_kmh": round(speed, 1),
            "rpm": rpm,
            "fuel_rate": round(fuel_rate, 2),
            "acceleration": round(accel, 3),
            "coolant_temp": round(coolant_temp, 1),
            "phase": phase,
            # OBD columns
            "obd_co2": round(obd_result.get("co2_g_per_km", 0.0), 4),
            "obd_co": round(obd_result.get("co_g_per_km", 0.0), 6),
            "obd_nox": round(obd_result.get("nox_g_per_km", 0.0), 6),
            "obd_hc": round(obd_result.get("hc_g_per_km", 0.0), 6),
            "obd_pm25": round(obd_result.get("pm25_g_per_km", 0.0), 8),
            "obd_ces": round(obd_result.get("ces_score", 0.0), 4),
            # Tailpipe columns
            "tailpipe_co2": round(tailpipe.get("co2_g_per_km", 0.0), 4),
            "tailpipe_co": round(tailpipe.get("co_g_per_km", 0.0), 6),
            "tailpipe_nox": round(tailpipe.get("nox_g_per_km", 0.0), 6),
            "tailpipe_hc": round(tailpipe.get("hc_g_per_km", 0.0), 6),
            "tailpipe_pm25": round(tailpipe.get("pm25_g_per_km", 0.0), 8),
            "tailpipe_ces": tailpipe_ces,
            # Failure metadata
            "has_failure": int(profile["has_failure"]),
            "failure_type": profile["failure_type"] or "",
            "failure_onset_s": failure_onset_s if failure_onset_s is not None else "",
        }
        rows.append(row)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic paired OBD-vs-tailpipe emission dataset.",
    )
    parser.add_argument("--vehicles", type=int, default=500,
                        help="Number of vehicles to simulate (default: 500)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--output", type=str,
                        default="data/paired_dataset_synthetic.csv",
                        help="Output CSV path (default: data/paired_dataset_synthetic.csv)")
    parser.add_argument("--cycle", type=str, default="wltc",
                        choices=["wltc", "midc"],
                        help="Driving cycle (default: wltc)")
    args = parser.parse_args()

    # Resolve output path relative to project root
    project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    if not os.path.isabs(args.output):
        output_path = os.path.join(project_root, args.output)
    else:
        output_path = args.output

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    rng = random.Random(args.seed)
    np_rng = np.random.RandomState(args.seed)
    degradation_model = DegradationModel()

    t0 = time.time()
    print(f"Generating paired dataset: {args.vehicles} vehicles, "
          f"cycle={args.cycle}, seed={args.seed}")
    print(f"Output: {output_path}")

    # Accumulators for summary stats
    total_rows = 0
    gap_sums = {p: 0.0 for p in _SHORT_NAMES}
    obd_sums = {p: 0.0 for p in _SHORT_NAMES}
    tailpipe_sums = {p: 0.0 for p in _SHORT_NAMES}
    obd_sq_sums = {p: 0.0 for p in _SHORT_NAMES}
    tailpipe_sq_sums = {p: 0.0 for p in _SHORT_NAMES}
    cross_sums = {p: 0.0 for p in _SHORT_NAMES}
    n_failures = 0

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for i in range(1, args.vehicles + 1):
            profile = generate_vehicle_profile(rng, i)
            if profile["has_failure"]:
                n_failures += 1

            rows = run_cycle_for_vehicle(
                profile, degradation_model, args.cycle, rng, np_rng,
            )
            for row in rows:
                writer.writerow(row)
                total_rows += 1
                for p, obd_col, tp_col in zip(
                    _SHORT_NAMES,
                    ["obd_co2", "obd_co", "obd_nox", "obd_hc", "obd_pm25"],
                    ["tailpipe_co2", "tailpipe_co", "tailpipe_nox",
                     "tailpipe_hc", "tailpipe_pm25"],
                ):
                    o = row[obd_col]
                    t = row[tp_col]
                    gap_sums[p] += (t - o)
                    obd_sums[p] += o
                    tailpipe_sums[p] += t
                    obd_sq_sums[p] += o * o
                    tailpipe_sq_sums[p] += t * t
                    cross_sums[p] += o * t

            if i % max(1, args.vehicles // 10) == 0:
                print(f"  ... {i}/{args.vehicles} vehicles done")

    elapsed = time.time() - t0

    # ── Summary statistics ────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"Dataset generated in {elapsed:.1f}s")
    print(f"  Vehicles:       {args.vehicles}")
    print(f"  Total readings: {total_rows}")
    print(f"  With failures:  {n_failures} ({100*n_failures/args.vehicles:.1f}%)")
    print(f"\nMean gap (tailpipe - OBD) per pollutant:")
    for p in _SHORT_NAMES:
        mean_gap = gap_sums[p] / max(total_rows, 1)
        print(f"  {p:>5s}: {mean_gap:+.6f} g/km")

    print(f"\nPearson correlation (OBD vs tailpipe):")
    for p in _SHORT_NAMES:
        n = total_rows
        if n < 2:
            print(f"  {p:>5s}: N/A (too few samples)")
            continue
        mean_o = obd_sums[p] / n
        mean_t = tailpipe_sums[p] / n
        var_o = obd_sq_sums[p] / n - mean_o ** 2
        var_t = tailpipe_sq_sums[p] / n - mean_t ** 2
        cov = cross_sums[p] / n - mean_o * mean_t
        denom = (max(var_o, 1e-30) * max(var_t, 1e-30)) ** 0.5
        corr = cov / denom
        print(f"  {p:>5s}: {corr:.4f}")

    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
