#!/usr/bin/env python3
"""
CES vs CO2-Only Compliance Detection — Quantitative Comparison
===============================================================
Generates the key experimental evidence for the SmartPUC IEEE paper:
a multi-pollutant Composite Emission Score (CES) detects more real-world
violations than a single-pollutant CO2-only compliance check.

The script runs second-by-second emission calculations over WLTC and MIDC
driving cycles for multiple vehicle profiles and ambient conditions, then
compares three compliance paradigms:

    1. **CO2-only**     — PASS iff CO2 <= 120 g/km
    2. **Per-pollutant** — FAIL if *any* individual pollutant exceeds its
                           BS-VI threshold
    3. **CES**          — FAIL if CES >= 1.0 (weighted multi-pollutant score)

The headline metric is the number of seconds where CES flags FAIL but CO2-
only says PASS — i.e., violations that a single-pollutant system misses.

Output
------
- ``docs/ces_vs_co2_comparison.json`` — full machine-readable results
- stdout — formatted summary tables

References
----------
[1] SmartPUC CES definition: ``config/ces_weights.json``
[2] Emission engine: ``backend/emission_engine.py``
[3] Driving-cycle simulator: ``backend/simulator.py``
[4] VSP model: ``physics/vsp_model.py``
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Path setup — ensure project root is importable
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_SCRIPT_DIR, "..")
sys.path.insert(0, _PROJECT_ROOT)

from backend.emission_engine import (
    calculate_emissions,
    CES_WEIGHTS,
    BSVI_THRESHOLDS,
    BSVI_DIESEL_THRESHOLDS,
    get_thresholds,
    BSStandard,
)
from backend.simulator import (
    _generate_wltc_profile,
    _generate_midc_profile,
    _estimate_fuel_rate,
    calculate_rpm_from_speed,
)
from physics.vsp_model import calculate_vsp, get_operating_mode_bin

# ---------------------------------------------------------------------------
# Vehicle profiles — five representative archetypes
# ---------------------------------------------------------------------------
# Each profile defines emission scaling factors applied on top of the base
# MOVES rates.  A scaler of 1.0 means the vehicle emits at the BSVI
# certification baseline; values > 1.0 represent degradation / higher
# emitting vehicles.
#
# These are intentionally kept as simple dicts (not VehicleProfile objects)
# so the script is self-contained and does not depend on the optional
# backend.vehicle_profiles module.

VEHICLE_PROFILES: List[Dict[str, Any]] = [
    {
        "name": "Clean Petrol (new BS-VI)",
        "fuel_type": "petrol",
        "mass_kg": 1100.0,
        "scalers": {"co2": 0.85, "co": 0.70, "nox": 0.65, "hc": 0.70, "pm25": 0.60},
        "description": "A brand-new BS-VI petrol hatchback with fully functional "
                       "catalytic converter and OBD-II within spec.",
    },
    {
        "name": "Normal Petrol (2-year-old)",
        "fuel_type": "petrol",
        "mass_kg": 1200.0,
        "scalers": {"co2": 1.00, "co": 1.00, "nox": 1.00, "hc": 1.00, "pm25": 1.00},
        "description": "Baseline BS-VI petrol sedan with average wear. Represents "
                       "the calibration reference vehicle.",
    },
    {
        "name": "Degraded Petrol (ageing catalyst)",
        "fuel_type": "petrol",
        "mass_kg": 1250.0,
        "scalers": {"co2": 1.10, "co": 2.20, "nox": 2.50, "hc": 2.00, "pm25": 1.80},
        "description": "5-year-old petrol vehicle with catalyst degradation. CO2 "
                       "stays near-normal but CO/NOx/HC spike — the critical case "
                       "where CO2-only monitoring fails to catch real violations.",
    },
    {
        "name": "Cold-Start Petrol",
        "fuel_type": "petrol",
        "mass_kg": 1200.0,
        "scalers": {"co2": 1.05, "co": 1.80, "nox": 1.30, "hc": 1.50, "pm25": 1.20},
        "description": "Normal petrol car during cold-start phase (first 180 s). "
                       "Catalyst light-off delay causes elevated CO and HC.",
    },
    {
        "name": "Diesel Sedan (BS-VI DPF-equipped)",
        "fuel_type": "diesel",
        "mass_kg": 1350.0,
        "scalers": {"co2": 0.95, "co": 0.60, "nox": 1.80, "hc": 0.70, "pm25": 1.50},
        "description": "BS-VI diesel sedan with DPF. Lower CO2 and CO than petrol "
                       "but higher NOx — another case where CO2-only misses failures.",
    },
    {
        "name": "High-Mileage Diesel (DPF clogged)",
        "fuel_type": "diesel",
        "mass_kg": 1400.0,
        "scalers": {"co2": 1.05, "co": 0.80, "nox": 2.80, "hc": 1.20, "pm25": 3.50},
        "description": "Older diesel with partially clogged DPF. PM2.5 and NOx are "
                       "dramatically elevated while CO2 remains near-threshold.",
    },
]

# Ambient temperature scenarios (degrees Celsius)
AMBIENT_TEMPS: List[float] = [15.0, 25.0, 35.0, 45.0]

# Cold-start duration (seconds from engine start)
COLD_START_DURATION: int = 180

# Driving cycles to evaluate
CYCLES: Dict[str, Any] = {
    "WLTC": {"generator": _generate_wltc_profile, "length": 1800},
    "MIDC": {"generator": _generate_midc_profile, "length": 1180},
}


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyse_cycle(
    speed_profile: np.ndarray,
    profile: Dict[str, Any],
    ambient_temp: float,
    apply_cold_start: bool = True,
) -> Dict[str, Any]:
    """Run second-by-second compliance analysis on a driving cycle.

    Returns per-second emission data and aggregate compliance statistics.
    """
    fuel_type = profile["fuel_type"]
    mass_kg = profile["mass_kg"]
    scalers = profile["scalers"]
    thresholds = get_thresholds(fuel_type, BSStandard.BS6)
    co2_threshold = thresholds["co2"]

    n = len(speed_profile)

    # Compute acceleration via finite differences (m/s^2)
    speed_mps = speed_profile / 3.6
    accel = np.zeros(n)
    accel[1:] = np.diff(speed_mps)  # dt = 1 s

    # Counters
    total_seconds = 0
    ces_fail = 0
    co2_only_fail = 0
    per_pollutant_fail = 0

    # Key disagreement counters
    ces_fail_co2_pass = 0  # CES catches it, CO2-only misses it
    co2_fail_ces_pass = 0  # CO2-only catches it, CES misses it (rare)

    # Per-pollutant violation counters
    pollutant_violations: Dict[str, int] = {p: 0 for p in CES_WEIGHTS}

    # CES score distribution
    ces_scores: List[float] = []
    co2_values: List[float] = []

    for t in range(n):
        spd = float(speed_profile[t])
        acc = float(accel[t])

        # Skip true idle (speed = 0) to avoid division artefacts
        # but still count them for completeness
        total_seconds += 1

        # VSP and operating mode bin
        v_mps = spd / 3.6
        vsp = calculate_vsp(v_mps, acc)
        op_bin = get_operating_mode_bin(vsp, v_mps)

        # RPM and fuel rate
        rpm = calculate_rpm_from_speed(spd)
        fuel_rate = _estimate_fuel_rate(spd, acc, mass_kg)

        # Cold-start flag
        cold_start = apply_cold_start and (t < COLD_START_DURATION)

        # Base emission calculation
        result = calculate_emissions(
            speed_kmh=spd,
            acceleration=acc,
            rpm=float(rpm),
            fuel_rate=fuel_rate,
            fuel_type=fuel_type,
            operating_mode_bin=op_bin,
            ambient_temp=ambient_temp,
            altitude=0.0,
            cold_start=cold_start,
            bs_standard=BSStandard.BS6,
            vehicle_profile=None,  # we apply scalers manually below
        )

        # Apply vehicle-profile emission scalers manually
        co2_val = result["co2_g_per_km"] * scalers["co2"]
        co_val = result["co_g_per_km"] * scalers["co"]
        nox_val = result["nox_g_per_km"] * scalers["nox"]
        hc_val = result["hc_g_per_km"] * scalers["hc"]
        pm25_val = result["pm25_g_per_km"] * scalers["pm25"]

        pollutant_values = {
            "co2": co2_val,
            "co": co_val,
            "nox": nox_val,
            "hc": hc_val,
            "pm25": pm25_val,
        }

        # --- Recompute CES with scaled values ---
        ces_score = sum(
            (pollutant_values[p] / thresholds[p]) * CES_WEIGHTS[p]
            for p in CES_WEIGHTS
        )

        ces_scores.append(ces_score)
        co2_values.append(co2_val)

        # --- Compliance checks ---
        # 1) CO2-only: FAIL if CO2 > 120 g/km
        co2_only_pass = co2_val <= co2_threshold

        # 2) CES: FAIL if CES >= 1.0
        ces_pass = ces_score < 1.0

        # 3) Per-pollutant: FAIL if ANY pollutant exceeds its threshold
        any_pollutant_fail = False
        for p in CES_WEIGHTS:
            if pollutant_values[p] > thresholds[p]:
                pollutant_violations[p] += 1
                any_pollutant_fail = True

        if not ces_pass:
            ces_fail += 1
        if not co2_only_pass:
            co2_only_fail += 1
        if any_pollutant_fail:
            per_pollutant_fail += 1

        # Disagreements
        if not ces_pass and co2_only_pass:
            ces_fail_co2_pass += 1
        if not co2_only_pass and ces_pass:
            co2_fail_ces_pass += 1

    # Aggregate statistics
    ces_arr = np.array(ces_scores)
    co2_arr = np.array(co2_values)

    return {
        "total_seconds": total_seconds,
        "ces_fail_count": ces_fail,
        "co2_only_fail_count": co2_only_fail,
        "per_pollutant_fail_count": per_pollutant_fail,
        "ces_fail_co2_pass": ces_fail_co2_pass,
        "co2_fail_ces_pass": co2_fail_ces_pass,
        "pollutant_violations": pollutant_violations,
        "ces_detection_rate_pct": round(100.0 * ces_fail / total_seconds, 2),
        "co2_detection_rate_pct": round(100.0 * co2_only_fail / total_seconds, 2),
        "per_pollutant_detection_rate_pct": round(
            100.0 * per_pollutant_fail / total_seconds, 2
        ),
        "ces_mean": round(float(np.mean(ces_arr)), 4),
        "ces_median": round(float(np.median(ces_arr)), 4),
        "ces_p95": round(float(np.percentile(ces_arr, 95)), 4),
        "ces_max": round(float(np.max(ces_arr)), 4),
        "co2_mean_gpkm": round(float(np.mean(co2_arr)), 2),
        "co2_max_gpkm": round(float(np.max(co2_arr)), 2),
    }


# ---------------------------------------------------------------------------
# Main experiment runner
# ---------------------------------------------------------------------------

def run_full_experiment() -> Dict[str, Any]:
    """Execute the complete CES vs CO2-only comparison experiment."""
    print("=" * 78)
    print("  SmartPUC — CES vs CO2-Only Compliance Detection Comparison")
    print("=" * 78)
    print()

    results: Dict[str, Any] = {
        "experiment": "CES vs CO2-only compliance detection",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ces_weights": dict(CES_WEIGHTS),
        "bsvi_thresholds_petrol": dict(BSVI_THRESHOLDS),
        "bsvi_thresholds_diesel": dict(BSVI_DIESEL_THRESHOLDS),
        "cold_start_duration_s": COLD_START_DURATION,
        "ambient_temperatures_degC": AMBIENT_TEMPS,
        "vehicle_profiles": [
            {"name": p["name"], "fuel_type": p["fuel_type"],
             "mass_kg": p["mass_kg"], "scalers": p["scalers"],
             "description": p["description"]}
            for p in VEHICLE_PROFILES
        ],
        "cycle_results": {},
        "summary": {},
    }

    # Accumulators for grand summary
    grand_total_seconds = 0
    grand_ces_fail_co2_pass = 0
    grand_co2_fail_ces_pass = 0
    grand_ces_fail = 0
    grand_co2_fail = 0
    grand_per_pollutant_fail = 0

    for cycle_name, cycle_info in CYCLES.items():
        print(f"--- Cycle: {cycle_name} ({cycle_info['length']} seconds) ---")
        speed_profile = cycle_info["generator"]()
        results["cycle_results"][cycle_name] = {}

        for profile in VEHICLE_PROFILES:
            pname = profile["name"]
            results["cycle_results"][cycle_name][pname] = {}

            for temp in AMBIENT_TEMPS:
                label = f"{temp}C"
                print(f"  {pname:40s} | T={temp:4.0f} C ... ", end="", flush=True)

                stats = analyse_cycle(
                    speed_profile=speed_profile,
                    profile=profile,
                    ambient_temp=temp,
                    apply_cold_start=True,
                )
                results["cycle_results"][cycle_name][pname][label] = stats

                grand_total_seconds += stats["total_seconds"]
                grand_ces_fail_co2_pass += stats["ces_fail_co2_pass"]
                grand_co2_fail_ces_pass += stats["co2_fail_ces_pass"]
                grand_ces_fail += stats["ces_fail_count"]
                grand_co2_fail += stats["co2_only_fail_count"]
                grand_per_pollutant_fail += stats["per_pollutant_fail_count"]

                extra = stats["ces_fail_co2_pass"]
                print(
                    f"CES_fail={stats['ces_fail_count']:5d}  "
                    f"CO2_fail={stats['co2_only_fail_count']:5d}  "
                    f"CES>CO2=+{extra:4d}  "
                    f"CES_mean={stats['ces_mean']:.3f}"
                )

        print()

    # Grand summary
    results["summary"] = {
        "total_seconds_analysed": grand_total_seconds,
        "total_ces_fail": grand_ces_fail,
        "total_co2_only_fail": grand_co2_fail,
        "total_per_pollutant_fail": grand_per_pollutant_fail,
        "total_ces_fail_but_co2_pass": grand_ces_fail_co2_pass,
        "total_co2_fail_but_ces_pass": grand_co2_fail_ces_pass,
        "ces_detection_rate_pct": round(100.0 * grand_ces_fail / grand_total_seconds, 2),
        "co2_only_detection_rate_pct": round(100.0 * grand_co2_fail / grand_total_seconds, 2),
        "per_pollutant_detection_rate_pct": round(
            100.0 * grand_per_pollutant_fail / grand_total_seconds, 2
        ),
        "additional_violations_caught_by_ces": grand_ces_fail_co2_pass,
        "additional_violations_caught_pct": round(
            100.0 * grand_ces_fail_co2_pass / max(grand_total_seconds, 1), 2
        ),
    }

    return results


def print_summary_tables(results: Dict[str, Any]) -> None:
    """Print formatted summary tables to stdout."""
    print()
    print("=" * 94)
    print("  SUMMARY: CES vs CO2-Only Detection Rate Comparison")
    print("=" * 94)

    # ── Table 1: Per-vehicle, per-cycle detection rates (averaged across temps) ──
    header = (
        f"{'Vehicle Profile':<40s} | {'Cycle':<5s} | "
        f"{'CES %':>7s} | {'CO2 %':>7s} | {'Per-P %':>7s} | "
        f"{'CES>CO2':>7s} | {'Mean CES':>8s}"
    )
    print()
    print("Table 1: Detection rates (averaged across ambient temperatures)")
    print("-" * 94)
    print(header)
    print("-" * 94)

    for cycle_name in CYCLES:
        for profile in VEHICLE_PROFILES:
            pname = profile["name"]
            temps_data = results["cycle_results"][cycle_name][pname]

            # Average across temperatures
            n_temps = len(temps_data)
            avg_ces_rate = sum(v["ces_detection_rate_pct"] for v in temps_data.values()) / n_temps
            avg_co2_rate = sum(v["co2_detection_rate_pct"] for v in temps_data.values()) / n_temps
            avg_pp_rate = sum(v["per_pollutant_detection_rate_pct"] for v in temps_data.values()) / n_temps
            avg_ces_co2 = sum(v["ces_fail_co2_pass"] for v in temps_data.values()) / n_temps
            avg_ces_mean = sum(v["ces_mean"] for v in temps_data.values()) / n_temps

            print(
                f"{pname:<40s} | {cycle_name:<5s} | "
                f"{avg_ces_rate:6.1f}% | {avg_co2_rate:6.1f}% | {avg_pp_rate:6.1f}% | "
                f"{avg_ces_co2:7.0f} | {avg_ces_mean:8.3f}"
            )
        print("-" * 94)

    # ── Table 2: Temperature sensitivity ──
    print()
    print("Table 2: Temperature sensitivity — CES failures caught that CO2-only missed")
    print("-" * 78)
    header2 = f"{'Vehicle Profile':<40s} |"
    for temp in AMBIENT_TEMPS:
        header2 += f" {temp:4.0f}C |"
    print(header2)
    print("-" * 78)

    for cycle_name in CYCLES:
        print(f"  [{cycle_name}]")
        for profile in VEHICLE_PROFILES:
            pname = profile["name"]
            row = f"  {pname:<38s} |"
            for temp in AMBIENT_TEMPS:
                label = f"{temp}C"
                val = results["cycle_results"][cycle_name][pname][label]["ces_fail_co2_pass"]
                row += f" {val:5d} |"
            print(row)
        print("-" * 78)

    # ── Table 3: Per-pollutant violation breakdown ──
    print()
    print("Table 3: Per-pollutant violation breakdown (total seconds across all conditions)")
    print("-" * 78)
    pollutants = list(CES_WEIGHTS.keys())
    header3 = f"{'Vehicle Profile':<40s} |"
    for p in pollutants:
        header3 += f" {p:>6s} |"
    print(header3)
    print("-" * 78)

    for cycle_name in CYCLES:
        print(f"  [{cycle_name}]")
        for profile in VEHICLE_PROFILES:
            pname = profile["name"]
            row = f"  {pname:<38s} |"
            for p in pollutants:
                total_viol = sum(
                    results["cycle_results"][cycle_name][pname][f"{t}C"]["pollutant_violations"][p]
                    for t in AMBIENT_TEMPS
                )
                row += f" {total_viol:6d} |"
            print(row)
        print("-" * 78)

    # ── Grand totals ──
    s = results["summary"]
    print()
    print("=" * 78)
    print("  GRAND TOTALS")
    print("=" * 78)
    print(f"  Total seconds analysed:                   {s['total_seconds_analysed']:>10,d}")
    print(f"  CES failures (CES >= 1.0):                {s['total_ces_fail']:>10,d}  "
          f"({s['ces_detection_rate_pct']:.1f}%)")
    print(f"  CO2-only failures (CO2 > 120 g/km):       {s['total_co2_only_fail']:>10,d}  "
          f"({s['co2_only_detection_rate_pct']:.1f}%)")
    print(f"  Per-pollutant failures (any > threshold):  {s['total_per_pollutant_fail']:>10,d}  "
          f"({s['per_pollutant_detection_rate_pct']:.1f}%)")
    print()
    print(f"  ** CES catches FAIL but CO2-only says PASS: "
          f"{s['total_ces_fail_but_co2_pass']:>8,d} seconds **")
    print(f"  ** CO2-only catches FAIL but CES says PASS: "
          f"{s['total_co2_fail_but_ces_pass']:>8,d} seconds **")
    print()
    print(f"  Additional violation detection by CES:     "
          f"+{s['additional_violations_caught_pct']:.1f}% of all seconds")
    print("=" * 78)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    t0 = time.time()
    results = run_full_experiment()
    elapsed = time.time() - t0

    # Print formatted tables
    print_summary_tables(results)

    # Save to JSON
    output_path = os.path.join(_PROJECT_ROOT, "docs", "ces_vs_co2_comparison.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print()
    print(f"  Results saved to: {os.path.abspath(output_path)}")
    print(f"  Elapsed time: {elapsed:.1f} s")
    print()


if __name__ == "__main__":
    main()
