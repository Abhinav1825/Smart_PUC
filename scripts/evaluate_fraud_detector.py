"""
Evaluate the FraudDetector against the labelled fraud dataset.

Loads ``data/fraud_labelled_dataset.json``, splits into 70% train / 30%
test (stratified by attack type), fits the IsolationForest on train-set
genuine data only, evaluates on the held-out test set, and computes
standard classification metrics (accuracy, precision, recall, F1,
confusion matrix).

Usage::

    python scripts/evaluate_fraud_detector.py

Produces:
    - Console summary of all metrics
    - docs/fraud_evaluation_report.json  (machine-readable results)
    - data/fraud_detector_v3.2.pkl       (trained model checkpoint)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from collections import Counter, defaultdict

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
    """Compute accuracy, precision, recall, and F1 for binary classification."""
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


def _stratified_split(dataset: list[dict], train_ratio: float = 0.7, seed: int = 42):
    """Split dataset into train/test, keeping attack types proportional.

    For replay attacks and drift manipulation, entire vehicle sequences
    are kept together (all readings from one vehicle_id go to either train
    or test) so temporal/replay detection can work properly.
    """
    import random
    rng = random.Random(seed)

    # Attack types that need whole-vehicle-sequence preservation
    sequence_types = {"replay_attack", "drift_manipulation"}

    train_indices = []
    test_indices = []

    # Group by attack_type
    groups: dict[str | None, list[int]] = defaultdict(list)
    for i, r in enumerate(dataset):
        groups[r.get("attack_type")].append(i)

    for attack_type, indices in groups.items():
        if attack_type in sequence_types:
            # Group indices by vehicle_id, then split at vehicle level
            vid_groups: dict[str, list[int]] = defaultdict(list)
            for i in indices:
                vid = dataset[i].get("vehicle_id", "")
                vid_groups[vid].append(i)
            vids = list(vid_groups.keys())
            rng.shuffle(vids)
            split_point = max(1, int(len(vids) * train_ratio))
            for vid in vids[:split_point]:
                train_indices.extend(vid_groups[vid])
            for vid in vids[split_point:]:
                test_indices.extend(vid_groups[vid])
        else:
            shuffled = list(indices)
            rng.shuffle(shuffled)
            split_point = int(len(shuffled) * train_ratio)
            train_indices.extend(shuffled[:split_point])
            test_indices.extend(shuffled[split_point:])

    train_data = [dataset[i] for i in sorted(train_indices)]
    test_data = [dataset[i] for i in sorted(test_indices)]

    return train_data, test_data


def _normalize_reading(reading: dict) -> dict:
    """Build the input dict expected by FraudDetector.analyze().

    Handles both old-format (co2_g_per_km) and new-format (co2) field names.
    """
    def _get(key: str, alt_key: str, default: float = 0.0) -> float:
        v = reading.get(key)
        if v is not None:
            return float(v)
        v = reading.get(alt_key)
        if v is not None:
            return float(v)
        return default

    return {
        "speed": float(reading.get("speed", 0.0)),
        "rpm": float(reading.get("rpm", 0.0)),
        "fuel_rate": float(reading.get("fuel_rate", 0.0)),
        "acceleration": float(reading.get("acceleration", 0.0)),
        "co2": _get("co2", "co2_g_per_km"),
        "co": _get("co", "co_g_per_km"),
        "nox": _get("nox", "nox_g_per_km"),
        "hc": _get("hc", "hc_g_per_km"),
        "pm25": _get("pm25", "pm25_g_per_km"),
        "ces_score": float(reading.get("ces_score", 0.0)),
        "vsp": float(reading.get("vsp", 0.0)),
        "timestamp": reading.get("timestamp", 0),
    }


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the full evaluation pipeline and save results."""

    # -- 1. Load labelled dataset ----------------------------------------
    dataset_path = PROJECT_ROOT / "data" / "fraud_labelled_dataset.json"
    if not dataset_path.exists():
        print(f"ERROR: Dataset not found at {dataset_path}")
        return 1

    with open(str(dataset_path), "r", encoding="utf-8") as f:
        dataset = json.load(f)

    # Normalise labels: any non-genuine label is "fraud"
    for r in dataset:
        if r.get("label") not in ("genuine", "fraud"):
            r["label"] = "fraud"

    print(f"Loaded {len(dataset)} labelled readings from {dataset_path.name}")
    label_counts = Counter(r["label"] for r in dataset)
    attack_type_counts = Counter(r.get("attack_type") or "genuine" for r in dataset)
    print(f"  genuine: {label_counts.get('genuine', 0)}")
    print(f"  fraud:   {label_counts.get('fraud', 0)}")
    for at, cnt in sorted(attack_type_counts.items()):
        print(f"    {at}: {cnt}")
    print()

    # -- 2. Stratified train/test split ----------------------------------
    train_data, test_data = _stratified_split(dataset, train_ratio=0.7)

    train_genuine = [r for r in train_data if r["label"] == "genuine"]
    train_fraud = [r for r in train_data if r["label"] == "fraud"]
    test_genuine = [r for r in test_data if r["label"] == "genuine"]
    test_fraud = [r for r in test_data if r["label"] == "fraud"]

    print(f"Train set: {len(train_data)} ({len(train_genuine)} genuine, {len(train_fraud)} fraud)")
    print(f"Test set:  {len(test_data)} ({len(test_genuine)} genuine, {len(test_fraud)} fraud)")
    print()

    # -- 3. Initialise and fit the FraudDetector -------------------------
    # Use higher contamination to better detect anomalous readings
    detector = FraudDetector(if_contamination=0.10)

    # Fit Isolation Forest on training genuine data only
    genuine_features = [_normalize_reading(r) for r in train_genuine]
    detector.fit(genuine_features)
    print(f"Fitted IsolationForest on {len(genuine_features)} genuine training samples")
    print()

    # -- 4. Evaluate on the test set ------------------------------------
    y_true: list[str] = []
    y_pred: list[str] = []
    per_reading_results: list[dict] = []
    attack_type_results: dict[str, list[dict]] = {}

    # Group test readings by vehicle_id to feed them sequentially per vehicle
    # (important for temporal and replay detection)
    from itertools import groupby

    # Sort test data by vehicle_id then timestamp for proper temporal ordering
    test_sorted = sorted(test_data, key=lambda r: (r.get("vehicle_id", ""), r.get("timestamp", 0)))

    # Process readings grouped by vehicle_id. Each vehicle gets a fresh
    # detector instance (sharing the fitted IF model) so the temporal
    # window and drift state are per-vehicle.
    for vid, group in groupby(test_sorted, key=lambda r: r.get("vehicle_id", "")):
        group_list = list(group)

        # Create a fresh detector for each vehicle to get clean temporal state,
        # but reuse the fitted IF model
        vid_detector = FraudDetector(if_contamination=0.10)
        vid_detector._isolation = detector._isolation  # share fitted IF model

        for reading in group_list:
            detector_input = _normalize_reading(reading)

            result = vid_detector.analyze(
                detector_input,
                vehicle_id=reading.get("vehicle_id"),
            )

            true_label = reading["label"]
            predicted_label = "fraud" if result["is_fraud"] else "genuine"

            y_true.append(true_label)
            y_pred.append(predicted_label)

            entry = {
                "index": reading.get("timestamp", 0),
                "vehicle_id": reading.get("vehicle_id", ""),
                "true_label": true_label,
                "predicted_label": predicted_label,
                "fraud_score": round(result["fraud_score"], 4),
                "severity": result["severity"],
                "correct": true_label == predicted_label,
                "attack_type": reading.get("attack_type"),
                "reason_codes": result.get("reason_codes", []),
            }
            per_reading_results.append(entry)

            attack_type = reading.get("attack_type") or "genuine"
            attack_type_results.setdefault(attack_type, []).append(entry)

    # -- 5. Compute metrics ----------------------------------------------
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

    # -- 6. Print results ------------------------------------------------
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
        for m in misclassified[:50]:  # show at most 50
            print(
                f"    [{m['index']:>5d}] {m['vehicle_id']:>20s}  "
                f"true={m['true_label']:>7s}  pred={m['predicted_label']:>7s}  "
                f"score={m['fraud_score']:.4f}  attack={m['attack_type']}"
            )
        if len(misclassified) > 50:
            print(f"    ... and {len(misclassified) - 50} more")
    else:
        print("  No misclassified readings -- perfect detection!")
    print()

    # -- 7. Save checkpoint ----------------------------------------------
    checkpoint_path = PROJECT_ROOT / "data" / "fraud_detector_v3.2.pkl"
    detector.save_checkpoint(str(checkpoint_path))
    print(f"Model checkpoint saved to {checkpoint_path}")

    # -- 8. Save report --------------------------------------------------
    report = {
        "dataset": str(dataset_path),
        "total_readings": len(dataset),
        "genuine_count": label_counts.get("genuine", 0),
        "fraud_count": label_counts.get("fraud", 0),
        "train_size": len(train_data),
        "test_size": len(test_data),
        "train_genuine": len(train_genuine),
        "train_fraud": len(train_fraud),
        "test_genuine": len(test_genuine),
        "test_fraud": len(test_fraud),
        "metrics": metrics,
        "confusion_matrix": confusion,
        "per_attack_type": per_attack_metrics,
        "misclassified_count": len(misclassified),
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
            "IsolationForest fitted on genuine training data only (70/30 split)",
            "Temporal checks process readings grouped by vehicle_id",
            "Replay attack detection uses 60-reading temporal window",
            "Per-vehicle fresh temporal state for proper sequential analysis",
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
