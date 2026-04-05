"""
Smart PUC — Fraud Detector Evaluation Harness
==============================================

Generates a **labelled adversarial dataset** and evaluates the three-
component fraud detector (``ml/fraud_detector.py``) against it, reporting
precision, recall, F1, and a confusion matrix. This is the evaluation
that the paper cites — without it, the ML contribution is not verifiable.

Attack types
------------
We synthesise seven distinct attack families, each designed to stress a
different detector component:

1. **Clean** — honest WLTC readings (negative class).
2. **Replay** — an older reading is re-submitted verbatim. Stresses the
   temporal consistency checker.
3. **Zero-pollutant spoofing** — the attacker sets all pollutant fields
   to zero while leaving speed/RPM realistic. Stresses the Isolation
   Forest (anomalous CO2-for-speed) and partially the physics validator.
4. **Physics violation** — a deliberately impossible row (RPM zero while
   speed > 5, or acceleration > 4 m/s², or fuel rate negative). Stresses
   the physics validator.
5. **Gradual drift** — all pollutant values are scaled down by 40 % to
   hide a failing vehicle. Stresses the Isolation Forest.
6. **Sudden spike** — a single burst of unrealistic speed / RPM values,
   simulating sensor tampering.
7. **Frozen sensor** — identical readings repeated N times, simulating a
   stuck sensor or canonicalised replay. Stresses temporal consistency.
8. **Source-aware adversary** — an attacker who has read
   ``ml/fraud_detector.py``, knows the 0.50 decision threshold and the
   four ensemble weights (physics 0.45, IF 0.30, temporal 0.15, drift
   0.10), and hand-crafts a reading that (a) is physically consistent
   (so physics_score ≈ 0); (b) lies close to the centre of the clean
   training distribution (so IF score stays below 0.30); (c) drifts
   within the temporal window's ±4 m/s² limit; (d) applies a tiny
   per-step CES delta beneath the Page-Hinkley cumulative threshold.
   The attacker's goal is to forge a faintly dirty reading whose
   ensemble fraud_score stays below 0.50 — the worst case the audit
   report calls out (§10, G11). This is intentionally harder than the
   first six attack families; a detection rate well below 1.0 here is
   expected and publishable as a discussion point, not a failure.

Each attack is labelled as ``is_fraud = 1`` and the ensemble's
``fraud_score >= FRAUD_THRESHOLD`` is taken as the positive prediction
boundary (default 0.5, matching the contract ``FRAUD_ALERT_THRESHOLD``).

Usage
-----
::

    python -m ml.fraud_evaluation --samples 5000 --output docs/fraud_eval_report.json

The output JSON contains per-class statistics and a global confusion
matrix suitable for the paper's §Results section. Reproducing the numbers
in ``docs/FRAUD_EVALUATION.md`` only requires running this script.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "backend"))

from ml.fraud_detector import FraudDetector  # noqa: E402

# Lazy imports that might not exist in minimal environments
try:
    from simulator import WLTCSimulator  # backend/simulator.py
    _HAS_SIMULATOR = True
except ImportError:
    _HAS_SIMULATOR = False


ATTACK_TYPES = [
    "replay",
    "zero_pollutant",
    "physics_violation",
    "gradual_drift",
    "sudden_spike",
    "frozen_sensor",
    "source_aware",  # knows the ensemble weights + decision threshold
]


# ─────────────────────────── Reading generators ───────────────────────────

def _wltc_reading(sim: "WLTCSimulator", t: int) -> dict[str, Any]:
    """Generate one honest-looking reading from the WLTC simulator.
    The physics/operating mode fields that the fraud detector inspects
    are filled with reasonable defaults consistent with the speed."""
    r = sim.generate_reading()
    speed = r["speed"]
    rpm = float(r.get("rpm", max(800, speed * 30)))
    fuel_rate = r.get("fuel_rate", 4.5)
    accel = r.get("acceleration", 0.2)
    # A plausible CO2 estimate in g/km that keeps clean readings inside
    # the Isolation Forest's learned distribution.
    co2 = 95.0 + speed * 0.5
    vsp = max(0.0, speed / 10.0 * max(0.1, accel + 0.5))
    return {
        "vehicle_id": "EVAL_CLEAN",
        "speed": speed,
        "rpm": rpm,
        "fuel_rate": fuel_rate,
        "acceleration": accel,
        "co2": co2,
        "vsp": vsp,
        "timestamp": 1_700_000_000 + t,
    }


def _synthetic_clean_reading(t: int) -> dict[str, Any]:
    """Zero-dependency clean reading generator used when the simulator
    module is not importable."""
    rng = random.Random(t)
    speed = max(0.0, 40.0 + rng.gauss(0.0, 15.0))
    rpm = max(800.0, min(6000.0, 1000.0 + speed * 35 + rng.gauss(0.0, 120.0)))
    fuel_rate = max(0.2, 3.5 + rng.gauss(0.0, 1.2))
    accel = rng.gauss(0.0, 0.6)
    co2 = 95.0 + speed * 0.5
    vsp = max(0.0, speed / 10.0 * max(0.1, accel + 0.5))
    return {
        "vehicle_id": "EVAL_CLEAN",
        "speed": round(speed, 2),
        "rpm": round(rpm, 0),
        "fuel_rate": round(fuel_rate, 3),
        "acceleration": round(accel, 3),
        "co2": round(co2, 2),
        "vsp": round(vsp, 3),
        "timestamp": 1_700_000_000 + t,
    }


def make_clean_reading(t: int, sim=None) -> dict[str, Any]:
    if sim is not None:
        return _wltc_reading(sim, t)
    return _synthetic_clean_reading(t)


# ─────────────────────────── Attack generators ────────────────────────────

def attack_replay(clean: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Re-submit the exact same reading. Timestamp unchanged → temporal
    detector should flag it after a few repeats."""
    return dict(clean)


def attack_zero_pollutant(clean: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    r = dict(clean)
    r["co2"] = 0.0
    r["fuel_rate"] = 0.01  # extreme hypermiling
    return r


def attack_physics_violation(clean: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    r = dict(clean)
    choice = rng.randint(0, 3)
    if choice == 0:
        # RPM zero but speed significant
        r["rpm"] = 0
        r["speed"] = max(20.0, clean["speed"])
    elif choice == 1:
        # Acceleration way beyond physical limits
        r["acceleration"] = 8.5
    elif choice == 2:
        # Negative fuel rate
        r["fuel_rate"] = -2.0
    else:
        # Speed-RPM mismatch (extreme)
        r["speed"] = 120.0
        r["rpm"] = 500
    return r


def attack_gradual_drift(clean: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    r = dict(clean)
    r["co2"] = clean["co2"] * 0.55
    r["fuel_rate"] = clean["fuel_rate"] * 0.55
    return r


def attack_sudden_spike(clean: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    r = dict(clean)
    r["speed"] = clean["speed"] + 180.0  # unrealistic burst
    r["rpm"] = min(9500.0, clean["rpm"] + 6000.0)
    return r


def attack_frozen_sensor(clean: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """A frozen sensor repeats the same bits. The temporal checker catches
    ≥ 3 identical readings in its rolling window. The first few copies are
    undetectable by design."""
    return dict(clean)


def attack_source_aware(clean: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """**Source-aware adversary (audit report G11).**

    A motivated attacker who has read ``ml/fraud_detector.py`` knows:
      - Ensemble weights: physics 0.45 / IF 0.30 / temporal 0.15 / drift 0.10.
      - Decision threshold: ``fraud_score >= 0.50`` → flagged.
      - Temporal check bounds: ``|Δv|`` up to 4 m/s², ``|Δrpm|`` bounded.
      - Page-Hinkley δ / λ parameters (inspected in the source).

    The attacker crafts a reading that satisfies ALL four component
    constraints simultaneously:
      1. Physically consistent (RPM scaled to speed, fuel rate plausible,
         acceleration inside ±4 m/s²) → ``physics_score ≈ 0``.
      2. Pollutant values shifted by at most +8 % from the clean value
         (inside the IF training distribution's dense region) →
         ``isolation_score`` stays ≤ 0.30.
      3. Temporal delta < 2 m/s² (well inside ±4 m/s² bound) →
         ``temporal_score`` stays low.
      4. CES shift < +0.02 per sample (beneath the Page-Hinkley
         cumulative threshold) → drift component does not trip.

    The attacker's *goal* is to encode a subtly dirty vehicle (8 % above
    the BS-VI CO₂ cap, 5 % above the NOx cap) while keeping
    ``fraud_score < 0.50``. A detector that catches this case has
    demonstrable robustness against an informed adversary.
    """
    r = dict(clean)
    base_speed = clean["speed"]
    # (1) Physically consistent drift: keep RPM / speed ratio in range.
    r["speed"] = max(0.0, base_speed + rng.uniform(-1.5, 1.5))  # ±1.5 m/s
    r["rpm"] = max(800.0, min(6000.0, clean["rpm"] + rng.uniform(-80.0, 80.0)))
    # (2) Fuel rate nudged +6% (dirty but inside IF centre).
    r["fuel_rate"] = max(0.2, clean["fuel_rate"] * rng.uniform(1.04, 1.08))
    # (2) CO2 nudged +8% — crosses the BS-VI cap in aggregate but stays
    # inside the IF learned-distribution's dense region because the clean
    # training set's CO2 has σ ≈ 10 g/km and we shift by ~8 g/km.
    r["co2"] = clean["co2"] * rng.uniform(1.06, 1.08)
    # (3) Acceleration well within the ±4 m/s² temporal bound.
    r["acceleration"] = clean["acceleration"] + rng.uniform(-0.6, 0.6)
    # (4) VSP shifts proportionally with speed+accel — consistent with
    # the physics engine's own formula, so the physics validator sees
    # nothing wrong.
    r["vsp"] = max(
        0.0, r["speed"] / 10.0 * max(0.1, r["acceleration"] + 0.5)
    )
    # Timestamp strictly monotonic so the replay detector does not trip.
    r["timestamp"] = clean["timestamp"] + rng.randint(1, 3)
    return r


ATTACKS = {
    "replay": attack_replay,
    "zero_pollutant": attack_zero_pollutant,
    "physics_violation": attack_physics_violation,
    "gradual_drift": attack_gradual_drift,
    "sudden_spike": attack_sudden_spike,
    "frozen_sensor": attack_frozen_sensor,
    "source_aware": attack_source_aware,
}


# ─────────────────────────── Dataset construction ─────────────────────────

def build_dataset(n_clean: int, n_per_attack: int, seed: int = 42) -> list[tuple[dict, int, str]]:
    """Return a list of (reading, label, attack_type) triples.

    Label is 1 for fraud, 0 for clean. Clean readings are interleaved to
    refresh the temporal window and avoid degenerate behaviour of the
    ``frozen_sensor`` attack dominating a trailing block.
    """
    rng = random.Random(seed)
    sim = WLTCSimulator(vehicle_id="EVAL_CLEAN") if _HAS_SIMULATOR else None

    dataset: list[tuple[dict, int, str]] = []
    for t in range(n_clean):
        dataset.append((make_clean_reading(t, sim), 0, "clean"))

    for attack_name in ATTACK_TYPES:
        fn = ATTACKS[attack_name]
        for i in range(n_per_attack):
            base = make_clean_reading(10000 + i + hash(attack_name) % 1000, sim)
            dataset.append((fn(base, rng), 1, attack_name))

    rng.shuffle(dataset)
    return dataset


# ─────────────────────────── Evaluation ────────────────────────────────────

def train_detector(seed_samples: int = 600) -> FraudDetector:
    det = FraudDetector()
    sim = WLTCSimulator(vehicle_id="EVAL_TRAIN") if _HAS_SIMULATOR else None
    train = [make_clean_reading(t, sim) for t in range(seed_samples)]
    det.fit(train)
    return det


def evaluate(dataset: list[tuple[dict, int, str]],
             detector: FraudDetector,
             threshold: float = 0.5) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    per_class: dict[str, dict[str, int]] = {
        "clean": {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "total": 0},
    }
    for atk in ATTACK_TYPES:
        per_class[atk] = {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "total": 0}

    latencies: list[float] = []
    for reading, label, attack_name in dataset:
        t0 = time.perf_counter()
        result = detector.analyze(reading)
        latencies.append((time.perf_counter() - t0) * 1e6)  # microseconds
        pred = 1 if result["fraud_score"] >= threshold else 0
        bucket = per_class.setdefault(attack_name, {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "total": 0})
        bucket["total"] += 1
        if label == 1 and pred == 1:
            tp += 1
            bucket["tp"] += 1
        elif label == 1 and pred == 0:
            fn += 1
            bucket["fn"] += 1
        elif label == 0 and pred == 1:
            fp += 1
            bucket["fp"] += 1
        else:
            tn += 1
            bucket["tn"] += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / max(1, tp + tn + fp + fn)

    # Per-attack recall (detection rate)
    per_attack_recall = {}
    for atk in ATTACK_TYPES:
        b = per_class[atk]
        detected = b["tp"]  # label was 1 for these, so tp is "caught"
        total = b["total"]
        per_attack_recall[atk] = round(detected / total, 4) if total > 0 else 0.0

    # Latency stats
    latencies.sort()
    n = len(latencies)
    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return latencies[idx] if latencies else 0.0

    return {
        "threshold": threshold,
        "total_samples": len(dataset),
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "metrics": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "accuracy": round(accuracy, 4),
        },
        "per_attack_recall": per_attack_recall,
        "inference_latency_us": {
            "p50": round(pct(50), 2),
            "p95": round(pct(95), 2),
            "p99": round(pct(99), 2),
            "mean": round(sum(latencies) / max(1, n), 2),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fraud detector evaluation harness.")
    ap.add_argument("--samples", type=int, default=5000,
                    help="Approximate total dataset size.")
    ap.add_argument("--clean-ratio", type=float, default=0.7,
                    help="Fraction of the dataset that should be clean.")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="Fraud score threshold for positive classification.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", default="docs/fraud_eval_report.json")
    args = ap.parse_args()

    n_clean = int(args.samples * args.clean_ratio)
    n_fraud_total = args.samples - n_clean
    n_per_attack = max(1, n_fraud_total // len(ATTACK_TYPES))

    print(f"Dataset size: {n_clean} clean + {n_per_attack * len(ATTACK_TYPES)} fraud "
          f"({len(ATTACK_TYPES)} attack types)")
    dataset = build_dataset(n_clean, n_per_attack, seed=args.seed)
    print(f"Total: {len(dataset)} samples")

    print("Training Isolation Forest on 600 clean samples...")
    detector = train_detector()

    print(f"Evaluating at threshold = {args.threshold}...")
    report = evaluate(dataset, detector, threshold=args.threshold)

    print("\nResults")
    print("=======")
    cm = report["confusion"]
    m = report["metrics"]
    print(f"Confusion matrix: TP={cm['tp']} FP={cm['fp']} TN={cm['tn']} FN={cm['fn']}")
    print(f"Precision: {m['precision']}   Recall: {m['recall']}   F1: {m['f1']}   Acc: {m['accuracy']}")
    print("\nPer-attack detection rate:")
    for atk, rate in report["per_attack_recall"].items():
        print(f"  {atk:20s}  {rate * 100:5.1f} %")
    lat = report["inference_latency_us"]
    print(f"\nInference latency (µs): p50={lat['p50']}  p95={lat['p95']}  p99={lat['p99']}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_out = {
        "generated_at": int(time.time()),
        "args": vars(args),
        **report,
    }
    out_path.write_text(json.dumps(report_out, indent=2))
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
