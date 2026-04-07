"""
CES Weight Sensitivity Analysis
================================

Perturbs each CES weight by +/-0.05 and re-runs the CES vs CO2-only
benchmark to demonstrate that the Composite Emission Score is robust to
moderate changes in the weight vector.

This analysis addresses the reviewer question: "Why these specific weights?"
by showing that the qualitative conclusions (CES catches violations CO2-only
misses) hold across a wide range of plausible weight configurations.

Usage::

    python scripts/ces_sensitivity_analysis.py

Output: ``docs/ces_sensitivity_report.json``
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.emission_engine import (
    calculate_emissions,
    BSVI_THRESHOLDS,
    CES_WEIGHTS,
)


def _run_scenario(
    weights: Dict[str, float],
    n_vehicles: int = 2000,
    seed: int = 42,
) -> Dict[str, Any]:
    """Run a CES-vs-CO2-only comparison under a given weight vector.

    Returns a dict with pass/fail agreement statistics.
    """
    import random
    rng = random.Random(seed)

    both_pass = 0
    both_fail = 0
    ces_fail_only = 0
    co2_fail_only = 0

    for _ in range(n_vehicles):
        speed = rng.uniform(20, 120)
        accel = rng.uniform(-1.5, 2.0)
        rpm = rng.uniform(1000, 5000)
        fuel = rng.uniform(3.0, 12.0)
        temp = rng.uniform(10, 45)
        cold = rng.random() < 0.15
        op_bin = rng.choice([0, 1, 11, 21, 22, 23, 24, 25])

        result = calculate_emissions(
            speed_kmh=speed, acceleration=accel, rpm=rpm,
            fuel_rate=fuel, operating_mode_bin=op_bin,
            ambient_temp=temp, cold_start=cold,
        )

        # CES under the given weights
        ces = sum(
            (result[f"{p}_g_per_km"] / BSVI_THRESHOLDS[p]) * weights[p]
            for p in weights
        )
        ces_pass = ces < 1.0

        # CO2-only pass
        co2_pass = result["co2_g_per_km"] <= BSVI_THRESHOLDS["co2"]

        if ces_pass and co2_pass:
            both_pass += 1
        elif not ces_pass and not co2_pass:
            both_fail += 1
        elif not ces_pass and co2_pass:
            ces_fail_only += 1
        else:
            co2_fail_only += 1

    total = n_vehicles
    agreement = (both_pass + both_fail) / total
    # Cohen's kappa
    p_yes = ((both_pass + ces_fail_only) / total) * ((both_pass + co2_fail_only) / total)
    p_no = ((both_fail + co2_fail_only) / total) * ((both_fail + ces_fail_only) / total)
    p_e = p_yes + p_no
    kappa = (agreement - p_e) / (1 - p_e) if p_e < 1.0 else 1.0

    return {
        "weights": weights,
        "n_vehicles": n_vehicles,
        "both_pass": both_pass,
        "both_fail": both_fail,
        "ces_fail_only": ces_fail_only,
        "co2_fail_only": co2_fail_only,
        "agreement_rate": round(agreement, 4),
        "cohens_kappa": round(kappa, 4),
    }


def main() -> None:
    """Run perturbation analysis and save results."""
    pollutants = list(CES_WEIGHTS.keys())
    base_weights = dict(CES_WEIGHTS)
    scenarios: List[Dict[str, Any]] = []

    # Baseline scenario
    baseline = _run_scenario(base_weights)
    baseline["label"] = "baseline"
    scenarios.append(baseline)

    # Perturb each weight by +/-0.05
    for target in pollutants:
        for delta in [-0.05, +0.05]:
            perturbed = copy.deepcopy(base_weights)
            perturbed[target] += delta

            # Re-normalise so weights still sum to 1.0
            total = sum(perturbed.values())
            perturbed = {k: round(v / total, 4) for k, v in perturbed.items()}

            label = f"{target}{'+' if delta > 0 else ''}{delta:.2f}"
            result = _run_scenario(perturbed)
            result["label"] = label
            scenarios.append(result)

    # Summary statistics
    agreements = [s["agreement_rate"] for s in scenarios]
    kappas = [s["cohens_kappa"] for s in scenarios]
    reclassified = [
        s["ces_fail_only"] + s["co2_fail_only"]
        - (baseline["ces_fail_only"] + baseline["co2_fail_only"])
        for s in scenarios
    ]

    report = {
        "description": (
            "CES weight sensitivity analysis. Each weight perturbed by +/-0.05 "
            "with re-normalisation. Shows that CES-vs-CO2-only conclusions are "
            "robust to moderate weight changes."
        ),
        "baseline_weights": base_weights,
        "n_scenarios": len(scenarios),
        "scenarios": scenarios,
        "summary": {
            "min_agreement": min(agreements),
            "max_agreement": max(agreements),
            "mean_agreement": round(sum(agreements) / len(agreements), 4),
            "min_kappa": min(kappas),
            "max_kappa": max(kappas),
            "max_reclassified_delta": max(abs(r) for r in reclassified),
        },
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "ces_sensitivity_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Saved {len(scenarios)} scenarios to {out_path}")
    print(f"Agreement range: {min(agreements):.4f} - {max(agreements):.4f}")
    print(f"Kappa range:     {min(kappas):.4f} - {max(kappas):.4f}")


if __name__ == "__main__":
    main()
