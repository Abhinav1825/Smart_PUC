"""
Evaluate the FraudDetector against the labelled fraud dataset.

Loads ``data/fraud_labelled_dataset.json``, runs each reading through
:class:`ml.fraud_detector.FraudDetector`, and computes standard
classification metrics (accuracy, precision, recall, F1, confusion matrix).

Usage::

    python scripts/evaluate_fraud_detector.py

Produces:
    - Console summary of all metrics
    - docs/fraud_evaluation_report.json  (machine-readable results)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from collections import Counter

# Ensure project root is on the import path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ml.fraud_detector import FraudDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_confusion_matrix(
    y_true: list[str], y_pred: list[str], labels: list[str]
) -> dict:
    """Build a confusion matrix dict from true/predicted label lists.

    Returns a nested dict: ``{actual_label: {predicted_label: count}}``.
    """
    matrix: dict[str, dict[str, int]] = {
        actual: {pred: 0 for pred in labels} for actual in labels
    }
    for true, pred in zip(y_true, y_pred):
        matrix[true][pred] += 1
    return matrix


def _compute_metrics(
    y_true: list[str], y_pred: list[str], positive_label: str = "fraud"
) -> dict:
    """Compute accuracy, precision, recall, and F1 for binary classification.

    Parameters
    ----------
    y_true : list[str]
        Ground-truth labels (``"fraud"`` or ``"genuine"``).
    y_pred : list[str]
        Predicted labels.
    positive_label : str
        The label treated as positive (default ``"fraud"``).

    Returns
    -------
    dict
        Dictionary with ``accuracy``, ``precision``, ``recall``, ``f1``.
    """
    tp = fp = fn = tn = 0
    for true, pred in zip(y_true, y_pred):
        if true == positive_label and pred == positive_label:
            tp += 1
        elif true != positive_label and pred == positive_label:
            fp += 1
        elif true == positive_label and pred != positive_label:
            fn += 1
        else:
            tn += 1

    total = tp + fp + fn + tn
    accuracy = (tp + tn) / total if total > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
    }


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the full evaluation pipeline and save results."""

    # ── 1. Load labelled dataset ────────────────────────────────────────
    dataset_path = PROJECT_ROOT / "data" / "fraud_labelled_dataset.json"
    if not dataset_path.exists():
        print(f"ERROR: Dataset not found at {dataset_path}")
        return 1

    with open(str(dataset_path), "r", encoding="utf-8") as f:
        dataset = json.load(f)

    print(f"Loaded {len(dataset)} labelled readings from {dataset_path.name}")
    label_counts = Counter(r["label"] for r in dataset)
    print(f"  genuine: {label_counts.get('genuine', 0)}")
    print(f"  fraud:   {label_counts.get('fraud', 0)}")
    print()

    # ── 2. Initialise the FraudDetector ─────────────────────────────────
    detector = FraudDetector()

    # Optionally fit the Isolation Forest on the genuine readings so it
    # has a baseline distribution for anomaly scoring.
    genuine_readings = [
        {
            "speed": r["speed"],
            "rpm": r["rpm"],
            "fuel_rate": r["fuel_rate"],
            "acceleration": r["acceleration"],
            "co2": r["co2_g_per_km"],
            "nox": r["nox_g_per_km"],
            "vsp": 0.0,  # VSP not in dataset; default to 0
        }
        for r in dataset
        if r["label"] == "genuine"
    ]
    detector.fit(genuine_readings)
    print(f"Fitted IsolationForest on {len(genuine_readings)} genuine samples")
    print()

    # ── 3. Run each reading through the detector ────────────────────────
    y_true: list[str] = []
    y_pred: list[str] = []
    per_reading_results: list[dict] = []
    attack_type_results: dict[str, list[dict]] = {}

    for idx, reading in enumerate(dataset):
        # Build the input dict expected by FraudDetector.analyze()
        detector_input = {
            "speed": reading["speed"],
            "rpm": reading["rpm"],
            "fuel_rate": reading["fuel_rate"],
            "acceleration": reading["acceleration"],
            "co2": reading["co2_g_per_km"],
            "co": reading.get("co_g_per_km", 0.0),
            "nox": reading["nox_g_per_km"],
            "hc": reading.get("hc_g_per_km", 0.0),
            "pm25": reading.get("pm25_g_per_km", 0.0),
            "ces_score": reading["ces_score"],
            "vsp": 0.0,  # Not available in labelled dataset
            "timestamp": idx,  # Sequential timestamps for temporal checks
        }

        result = detector.analyze(
            detector_input,
            vehicle_id=reading.get("vehicle_id"),
        )

        true_label = reading["label"]
        predicted_label = "fraud" if result["is_fraud"] else "genuine"

        y_true.append(true_label)
        y_pred.append(predicted_label)

        entry = {
            "index": idx,
            "vehicle_id": reading["vehicle_id"],
            "true_label": true_label,
            "predicted_label": predicted_label,
            "fraud_score": round(result["fraud_score"], 4),
            "severity": result["severity"],
            "correct": true_label == predicted_label,
            "attack_type": reading.get("attack_type"),
            "reason_codes": result.get("reason_codes", []),
        }
        per_reading_results.append(entry)

        # Track per-attack-type performance
        attack_type = reading.get("attack_type") or "genuine"
        attack_type_results.setdefault(attack_type, []).append(entry)

    # ── 4. Compute metrics ──────────────────────────────────────────────
    metrics = _compute_metrics(y_true, y_pred, positive_label="fraud")
    labels = ["genuine", "fraud"]
    confusion = _build_confusion_matrix(y_true, y_pred, labels)

    # Per-attack-type detection rates
    per_attack_metrics: dict[str, dict] = {}
    for attack_type, entries in attack_type_results.items():
        total = len(entries)
        correct = sum(1 for e in entries if e["correct"])
        per_attack_metrics[attack_type] = {
            "total": total,
            "correctly_classified": correct,
            "detection_rate": round(correct / total, 4) if total > 0 else 0.0,
        }

    # ── 5. Print results ────────────────────────────────────────────────
    print("=" * 60)
    print("  FRAUD DETECTOR EVALUATION RESULTS")
    print("=" * 60)
    print()
    print(f"  Accuracy:  {metrics['accuracy']:.4f}")
    print(f"  Precision: {metrics['precision']:.4f}")
    print(f"  Recall:    {metrics['recall']:.4f}")
    print(f"  F1 Score:  {metrics['f1']:.4f}")
    print()
    print("  Confusion Matrix:")
    print(f"                  Predicted")
    print(f"                  genuine  fraud")
    print(
        f"  Actual genuine  "
        f"{confusion['genuine']['genuine']:>5d}   "
        f"{confusion['genuine']['fraud']:>5d}"
    )
    print(
        f"  Actual fraud    "
        f"{confusion['fraud']['genuine']:>5d}   "
        f"{confusion['fraud']['fraud']:>5d}"
    )
    print()
    print("  Detection Rate by Attack Type:")
    for attack_type in sorted(per_attack_metrics.keys()):
        info = per_attack_metrics[attack_type]
        print(
            f"    {attack_type:>25s}: "
            f"{info['correctly_classified']}/{info['total']} "
            f"({info['detection_rate']:.1%})"
        )
    print()

    # Misclassified readings
    misclassified = [e for e in per_reading_results if not e["correct"]]
    if misclassified:
        print(f"  Misclassified readings ({len(misclassified)}):")
        for m in misclassified:
            print(
                f"    [{m['index']:>3d}] {m['vehicle_id']:>20s}  "
                f"true={m['true_label']:>7s}  pred={m['predicted_label']:>7s}  "
                f"score={m['fraud_score']:.4f}  attack={m['attack_type']}"
            )
    else:
        print("  No misclassified readings -- perfect detection!")
    print()

    # ── 6. Save report ──────────────────────────────────────────────────
    report = {
        "dataset": str(dataset_path),
        "total_readings": len(dataset),
        "genuine_count": label_counts.get("genuine", 0),
        "fraud_count": label_counts.get("fraud", 0),
        "metrics": metrics,
        "confusion_matrix": confusion,
        "per_attack_type": per_attack_metrics,
        "misclassified": [
            {
                "index": m["index"],
                "vehicle_id": m["vehicle_id"],
                "true_label": m["true_label"],
                "predicted_label": m["predicted_label"],
                "fraud_score": m["fraud_score"],
                "attack_type": m["attack_type"],
                "reason_codes": m["reason_codes"],
            }
            for m in misclassified
        ],
        "notes": [
            "IsolationForest fitted on genuine readings from the same dataset",
            "VSP not available in labelled dataset (defaulted to 0.0)",
            "Temporal checks rely on sequential ordering of dataset entries",
            "Replay attack detection requires consecutive identical readings",
            "Station fraud detection depends on cross-vehicle CES similarity "
            "(may not be fully captured by single-reading analysis)",
        ],
    }

    report_path = PROJECT_ROOT / "docs" / "fraud_evaluation_report.json"
    os.makedirs(str(report_path.parent), exist_ok=True)
    with open(str(report_path), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Report saved to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
