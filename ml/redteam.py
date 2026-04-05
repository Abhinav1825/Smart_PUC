"""Adversarial red-team CLI for the SmartPUC fraud detector (audit §13B N4).

Generates adversarial OBD-II readings by randomly perturbing a clean
baseline and tracks the *lowest* fraud score the detector produces
across ``--iterations`` trials. The worst-case reading is a useful:

1. Regression-test fixture: if an innocent refactor collapses detector
   recall, the worst-case score shifts.
2. Paper-methods section artefact: documents the security posture of
   the four-way ensemble under gradient-free black-box attacks.

Usage
-----
    python -m ml.redteam --attack source_aware --iterations 100 \\
           --target_recall 0.5 --seed 42

Outputs
-------
* ``docs/redteam_report.json`` — machine-readable summary.
* Final line of stdout — human-readable worst-case score.

Notes
-----
This is intentionally a *simple* random-search attacker, not a full
Bayesian-optimisation or projected-gradient adversary. The CLI is
designed to run in seconds as part of a CI suite; heavyweight attacks
belong in a dedicated experiment script, not in the regression harness.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.fraud_detector import FraudDetector  # noqa: E402


_CLEAN_BASELINE: dict[str, float] = {
    "speed": 60.0,
    "rpm": 2200.0,
    "fuel_rate": 6.5,
    "acceleration": 0.2,
    "co2": 115.0,
    "co": 0.6,
    "nox": 0.04,
    "hc": 0.08,
    "pm25": 0.003,
    "vsp": 3.5,
    "timestamp": 1_700_000_000,
}

# Per-feature (min, max) perturbation bounds for the random attacker.
# Chosen to stay *inside* the physics-plausibility envelope so the
# attacker does not trivially win by tripping PHYSICS_* reason codes.
_FEATURE_BOUNDS: dict[str, tuple[float, float]] = {
    "speed": (30.0, 90.0),
    "rpm": (1200.0, 3800.0),
    "fuel_rate": (3.0, 10.0),
    "acceleration": (-1.5, 1.5),
    "co2": (70.0, 135.0),
    "co": (0.2, 0.95),
    "nox": (0.02, 0.055),
    "hc": (0.04, 0.095),
    "pm25": (0.0015, 0.004),
    "vsp": (0.5, 9.0),
}


def _load_detector() -> FraudDetector:
    ckpt = ROOT / "data" / "fraud_detector_v3.2.pkl"
    if ckpt.exists():
        try:
            return FraudDetector.load_checkpoint(ckpt)
        except Exception:  # noqa: BLE001 — fall back to fresh detector
            pass
    det = FraudDetector()
    # Lightweight fit on synthetic clean data so the Isolation Forest
    # component is active (even if sklearn isn't installed this is a
    # no-op).
    det.fit([dict(_CLEAN_BASELINE) for _ in range(50)])
    return det


def _sample_reading(rng: random.Random) -> dict[str, float]:
    r = dict(_CLEAN_BASELINE)
    for key, (lo, hi) in _FEATURE_BOUNDS.items():
        r[key] = rng.uniform(lo, hi)
    return r


def run_redteam(
    attack: str,
    iterations: int,
    target_recall: float,
    seed: int,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Execute the red-team search and return the summary dict.

    Parameters
    ----------
    attack : str
        Attack identifier (free-form label, used for reporting only).
    iterations : int
        Number of random perturbations to try.
    target_recall : float
        Reporting threshold: ``detector_still_fires`` is True if the
        worst-case fraud score is still above it.
    seed : int
        RNG seed for reproducibility.
    output_path : Path, optional
        JSON output path. If None, writes to ``docs/redteam_report.json``.
    """
    rng = random.Random(seed)
    detector = _load_detector()

    worst_score = 1.0
    worst_reading: dict[str, float] = dict(_CLEAN_BASELINE)
    for _ in range(max(1, iterations)):
        candidate = _sample_reading(rng)
        # Fresh detector per trial so temporal-window state from
        # previous candidates doesn't leak into the score.
        det = _load_detector()
        result = det.analyze(candidate)
        score = float(result.get("fraud_score", 0.0))
        if score < worst_score:
            worst_score = score
            worst_reading = candidate

    report = {
        "attack_type": attack,
        "iterations": iterations,
        "seed": seed,
        "target_recall": target_recall,
        "worst_case_score": round(worst_score, 6),
        "worst_case_reading": {k: round(float(v), 6) for k, v in worst_reading.items()},
        "detector_still_fires": worst_score >= target_recall,
    }

    if output_path is None:
        output_path = ROOT / "docs" / "redteam_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return report


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ml.redteam",
        description="Adversarial red-team CLI for the SmartPUC fraud detector.",
    )
    p.add_argument("--attack", default="source_aware",
                   help="Attack label written to the report.")
    p.add_argument("--iterations", type=int, default=100,
                   help="Number of random perturbations to try.")
    p.add_argument("--target_recall", type=float, default=0.5,
                   help="Score threshold for 'detector_still_fires'.")
    p.add_argument("--seed", type=int, default=42, help="RNG seed.")
    p.add_argument("--output", type=Path, default=None,
                   help="Output JSON path (default docs/redteam_report.json).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = run_redteam(
        attack=args.attack,
        iterations=args.iterations,
        target_recall=args.target_recall,
        seed=args.seed,
        output_path=args.output,
    )
    print(
        f"[redteam] attack={report['attack_type']} "
        f"iters={report['iterations']} "
        f"worst_score={report['worst_case_score']:.4f} "
        f"still_fires={report['detector_still_fires']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
