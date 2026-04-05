"""
Smart PUC — Fraud-detector checkpoint builder
==============================================

Trains the 4-way fraud detector on a reproducible synthetic corpus
(`WLTCSimulator` at seed 42 by default) and serializes the fitted
ensemble to ``data/fraud_detector_v3.2.pkl``.

This script closes audit-report Fix #8 / "Serialize fraud-detector
checkpoint": the paper can now ship a frozen, reproducible model
artefact so the numbers in ``docs/FRAUD_EVALUATION.md`` no longer
depend on the current numpy/sklearn seed behaviour at runtime.

Run
---
    python scripts/build_fraud_checkpoint.py
    python scripts/build_fraud_checkpoint.py --samples 1000 --out data/fraud_detector_v3.2.pkl

The produced file is ~200 KB and is loaded back via
``FraudDetector.load_checkpoint(path)``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from ml.fraud_detector import FraudDetector  # noqa: E402
from ml.fraud_evaluation import make_clean_reading  # noqa: E402

try:
    from simulator import WLTCSimulator  # backend/simulator.py
    _HAS_SIM = True
except Exception:  # noqa: BLE001
    _HAS_SIM = False


def build(samples: int, out_path: Path) -> None:
    print(f"[build_fraud_checkpoint] training on {samples} clean samples")
    sim = WLTCSimulator(vehicle_id="FRAUD_CKPT") if _HAS_SIM else None
    train = [make_clean_reading(t, sim) for t in range(samples)]
    det = FraudDetector()
    det.fit(train)

    # Warm the temporal + drift components with the first 200 clean
    # readings so the checkpoint can start scoring immediately without
    # a cold-start burn-in period.
    for r in train[: min(200, len(train))]:
        det.update(r)
        # Also feed the drift detector a normalised CES signal.
        r_with_ces = dict(r)
        r_with_ces["ces_score"] = r["co2"] / 120.0
        det.analyze(r_with_ces)

    det.save_checkpoint(out_path)
    print(f"[build_fraud_checkpoint] wrote {out_path}")

    # Round-trip: verify we can load it and score a canonical reading.
    reloaded = FraudDetector.load_checkpoint(out_path)
    canonical = make_clean_reading(999_999, sim)
    result = reloaded.analyze(canonical)
    print(
        f"[build_fraud_checkpoint] round-trip OK  "
        f"fraud_score={result['fraud_score']:.4f}  "
        f"severity={result['severity']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a frozen FraudDetector checkpoint.")
    ap.add_argument("--samples", type=int, default=600)
    ap.add_argument(
        "--out",
        default=str(ROOT / "data" / "fraud_detector_v3.2.pkl"),
        help="Destination pickle path",
    )
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build(args.samples, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
