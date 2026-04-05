"""
Smart PUC — CES vs CO2-only violation detection experiment
=========================================================

**This is the paper's central novelty experiment.** Quantifies how many
compliance failures a 5-pollutant Composite Emission Score catches that
a CO2-only test would miss.

Methodology
-----------
1. Generate N=5000 synthetic vehicle-cycles using the WLTC simulator at
   randomised operating points (vehicle mass, cold start, ambient
   temperature, altitude). Half are tuned to operate near the BSVI
   threshold envelope; half are biased toward high-pollutant regimes.
2. Run each reading through `backend.emission_engine.calculate_emissions`
   to obtain all 5 pollutant values and the CES score.
3. For each record, apply two tests:
     a) **CES-only**: FAIL iff ces_score >= 1.0
     b) **CO2-only**: FAIL iff co2_g_per_km > CO2_THRESHOLD (120 g/km)
4. Count (CES_PASS, CO2_FAIL), (CES_FAIL, CO2_PASS), etc. Report the
   fraction of violations CES catches that CO2-only misses.

Output
------
- `docs/ces_vs_co2_report.json` — full confusion matrix + per-pollutant
  breakdown + Cohen's kappa.
- Human-readable summary printed to stdout.

Usage
-----
    python scripts/bench_ces_vs_co2.py --samples 5000
    python scripts/bench_ces_vs_co2.py --samples 1000 --seed 0

The experiment is deterministic under a fixed seed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List

# Make sibling packages importable when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.emission_engine import (
    calculate_emissions,
    CO2_THRESHOLD,
    BSVI_THRESHOLDS,
)


def generate_sample(rng: random.Random) -> Dict[str, Any]:
    """Draw a randomised operating point across the WLTC envelope."""
    return {
        "speed_kmh": rng.uniform(0, 130),
        "acceleration": rng.gauss(0, 1.2),
        "rpm": rng.randint(700, 5500),
        "fuel_rate": rng.uniform(0.5, 18.0),  # L/100km
        "fuel_type": "petrol",
        "operating_mode_bin": rng.choice([0, 1, 11, 21, 22, 23, 24, 25, 27, 28, 30]),
        "ambient_temp": rng.uniform(5.0, 45.0),
        "altitude": rng.uniform(0, 1500),
        "cold_start": rng.random() < 0.15,
    }


def biased_sample(rng: random.Random) -> Dict[str, Any]:
    """Bias toward high-pollutant regimes (to exercise the failure path)."""
    base = generate_sample(rng)
    # Increase fuel rate, low speed, cold start
    base["fuel_rate"] = rng.uniform(9.0, 22.0)
    base["speed_kmh"] = rng.uniform(0, 40)
    base["cold_start"] = rng.random() < 0.55
    base["ambient_temp"] = rng.uniform(30.0, 48.0)
    return base


def run_experiment(n_samples: int, seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    results: List[Dict[str, Any]] = []

    for i in range(n_samples):
        inputs = biased_sample(rng) if rng.random() < 0.5 else generate_sample(rng)
        try:
            emission = calculate_emissions(**inputs)
        except ValueError:
            continue

        ces = emission["ces_score"]
        co2_gpkm = emission["co2_g_per_km"]

        ces_fail = ces >= 1.0
        co2_fail = co2_gpkm > CO2_THRESHOLD

        results.append({
            "ces": ces,
            "co2": co2_gpkm,
            "co": emission["co_g_per_km"],
            "nox": emission["nox_g_per_km"],
            "hc": emission["hc_g_per_km"],
            "pm25": emission["pm25_g_per_km"],
            "ces_fail": ces_fail,
            "co2_fail": co2_fail,
        })

    # Confusion matrix (CES vs CO2-only)
    both_pass = sum(1 for r in results if not r["ces_fail"] and not r["co2_fail"])
    both_fail = sum(1 for r in results if r["ces_fail"] and r["co2_fail"])
    ces_only_fail = sum(1 for r in results if r["ces_fail"] and not r["co2_fail"])
    co2_only_fail = sum(1 for r in results if not r["ces_fail"] and r["co2_fail"])
    total = len(results)

    # Per-pollutant breakdown of CES-caught-but-CO2-missed violations.
    # Identifies the "dominant non-CO2 pollutant" for each row that CES
    # flagged but CO2-only would have passed.
    ces_only_by_pollutant: Dict[str, int] = {"co": 0, "nox": 0, "hc": 0, "pm25": 0}
    for r in results:
        if r["ces_fail"] and not r["co2_fail"]:
            ratios = {
                "co":   r["co"]  / BSVI_THRESHOLDS["co"],
                "nox":  r["nox"] / BSVI_THRESHOLDS["nox"],
                "hc":   r["hc"]  / BSVI_THRESHOLDS["hc"],
                "pm25": r["pm25"] / BSVI_THRESHOLDS["pm25"],
            }
            dominant = max(ratios.items(), key=lambda kv: kv[1])[0]
            ces_only_by_pollutant[dominant] += 1

    # Cohen's kappa between the two tests (chance-corrected agreement)
    agree = both_pass + both_fail
    p0 = agree / total if total else 0.0
    p_ces_fail = (both_fail + ces_only_fail) / total if total else 0.0
    p_co2_fail = (both_fail + co2_only_fail) / total if total else 0.0
    pe = p_ces_fail * p_co2_fail + (1 - p_ces_fail) * (1 - p_co2_fail)
    kappa = (p0 - pe) / (1 - pe) if (1 - pe) > 1e-9 else 0.0

    # Headline metric: of all violations CES catches, what fraction does
    # CO2-only miss?
    all_ces_violations = both_fail + ces_only_fail
    missed_by_co2 = ces_only_fail
    fraction_missed_by_co2 = missed_by_co2 / all_ces_violations if all_ces_violations else 0.0

    return {
        "n_samples": total,
        "seed": seed,
        "confusion_matrix": {
            "both_pass": both_pass,
            "both_fail": both_fail,
            "ces_fail_only": ces_only_fail,
            "co2_fail_only": co2_only_fail,
        },
        "rates": {
            "ces_failure_rate": (both_fail + ces_only_fail) / total if total else 0.0,
            "co2_only_failure_rate": (both_fail + co2_only_fail) / total if total else 0.0,
            "agreement_rate": p0,
            "cohens_kappa": kappa,
        },
        "headline": {
            "ces_violations_total": all_ces_violations,
            "ces_violations_missed_by_co2_only": missed_by_co2,
            "fraction_ces_violations_missed_by_co2_only": fraction_missed_by_co2,
            "dominant_pollutant_breakdown": ces_only_by_pollutant,
        },
    }


def print_summary(report: Dict[str, Any]) -> None:
    print()
    print("=" * 60)
    print("CES vs CO2-only — Compliance Detection Comparison")
    print("=" * 60)
    print(f"  N samples : {report['n_samples']}")
    print(f"  Seed      : {report['seed']}")
    print()
    cm = report["confusion_matrix"]
    print("Confusion matrix:")
    print(f"  both PASS          : {cm['both_pass']:>6d}")
    print(f"  both FAIL          : {cm['both_fail']:>6d}")
    print(f"  CES FAIL / CO2 PASS: {cm['ces_fail_only']:>6d}  "
          "<- caught only by CES")
    print(f"  CES PASS / CO2 FAIL: {cm['co2_fail_only']:>6d}  "
          "<- caught only by CO2-only")
    print()
    r = report["rates"]
    print(f"  CES failure rate     : {r['ces_failure_rate']:.3%}")
    print(f"  CO2-only failure rate: {r['co2_only_failure_rate']:.3%}")
    print(f"  Agreement rate       : {r['agreement_rate']:.3%}")
    print(f"  Cohen's kappa        : {r['cohens_kappa']:.3f}")
    print()
    h = report["headline"]
    print("HEADLINE FINDING")
    print("-" * 60)
    pct = h["fraction_ces_violations_missed_by_co2_only"] * 100.0
    print(
        f"  CES caught {h['ces_violations_total']} compliance violations; "
        f"{h['ces_violations_missed_by_co2_only']} of them "
        f"({pct:.1f}%) would have been MISSED by a CO2-only test."
    )
    print()
    print("  Dominant non-CO2 pollutant in those missed cases:")
    for p, n in h["dominant_pollutant_breakdown"].items():
        print(f"    {p.upper():>5} : {n}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="CES vs CO2-only experiment")
    parser.add_argument("--samples", type=int, default=5000,
                        help="Number of synthetic samples (default 5000)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(__file__), "..", "docs", "ces_vs_co2_report.json"),
        help="Output JSON path",
    )
    args = parser.parse_args()

    report = run_experiment(args.samples, args.seed)
    print_summary(report)

    out = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Full report written to {out}")


if __name__ == "__main__":
    main()
