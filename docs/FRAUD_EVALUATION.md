# Smart PUC — Fraud Detector Evaluation

This document reports the quantitative performance of the three-component
fraud detector (`ml/fraud_detector.py`) against a **labelled adversarial
dataset** covering six attack families. It is the evaluation that the paper
cites and the baseline that any future ML improvement must beat.

> All numbers in this document are reproducible:
> ```bash
> python -m ml.fraud_evaluation --samples 5000 --output docs/fraud_eval_report.json
> ```

## 1. Dataset

The evaluation harness in [`ml/fraud_evaluation.py`](../ml/fraud_evaluation.py)
synthesises a mixed dataset. For each run we draw:

| Class | Count | Source |
|-------|-------|--------|
| Clean (label = 0) | 3,500 | WLTC Class 3b simulator (`backend/simulator.py`) |
| Fraud (label = 1) | 1,500 | Hand-crafted attacks (250 per type × 6 types) |

The labels are **exact ground truth** because both the clean readings and
the attacks are generated in-process — there is no ambiguity about which
samples are fraudulent. Clean and fraud samples are interleaved randomly
(fixed seed = 42 for reproducibility).

### Attack families

| ID | Name | Description | Targets which detector |
|----|------|-------------|------------------------|
| A1 | `replay` | Re-submit a captured honest reading verbatim | Temporal |
| A2 | `zero_pollutant` | Zero CO2 / fuel rate with realistic speed + RPM | Isolation Forest |
| A3 | `physics_violation` | One hard-rule break (RPM = 0 at high speed, accel > 4 m/s², negative fuel rate, impossible gear ratio) | Physics |
| A4 | `gradual_drift` | All pollutants scaled × 0.55 to hide a failing vehicle | Isolation Forest |
| A5 | `sudden_spike` | Speed + RPM burst (sensor tampering / glitch) | Physics + Temporal |
| A6 | `frozen_sensor` | Identical repeated readings (stuck sensor / replay batch) | Temporal |

## 2. Metrics

* **Precision** = TP / (TP + FP). How often a "fraud" alert is correct.
* **Recall** = TP / (TP + FN). How many real frauds we catch.
* **F1** = harmonic mean of precision and recall.
* **Per-attack detection rate.** Recall restricted to each attack family.
* **Inference latency.** Per-sample time for `FraudDetector.analyze()`
  at p50 / p95 / p99.

The ensemble's decision threshold is **0.5**, matching the on-chain
`FRAUD_ALERT_THRESHOLD = 0.65` after the x10000 scaling (see
`contracts/EmissionRegistry.sol`). We report both the 0.5 and 0.65 cut-offs
for completeness.

## 3. Representative Results

These numbers are taken from a run of the harness on a 4-core laptop
(`N = 5000`). Absolute figures will vary by ±2 pp across runs because of
the random Isolation Forest training split; rerun the script to verify.

### 3.1 Global confusion matrix (threshold = 0.5, 30% held-out test set)

|                | Predicted fraud | Predicted clean |
|----------------|-----------------|-----------------|
| **Actual fraud** | TP = 134 | FN = 308 |
| **Actual clean** | FP = 57  | TN = 1,001 |

Derived metrics (on held-out test set only, N = 1,500):

| Metric | Value |
|--------|-------|
| Precision | **0.702** |
| Recall    | **0.303** |
| F1        | **0.423** |
| Accuracy  | **0.757** |

> **Methodology note (v3.2.1):** These numbers are evaluated on a
> **30% held-out test set** (1,500 samples) that the detector never
> sees during training or fitting.  The Isolation Forest is trained on
> 2,000 clean samples from the remaining 70%.  Earlier drafts of this
> document reported aspirational metrics (P ≈ 0.96, F1 ≈ 0.95) that
> were never reproducible.  The ensemble is effective at catching
> physically impossible readings (A3, A5) but **weak on replay, drift,
> and source-aware attacks** — see §4 for discussion and planned
> improvements.

### 3.2 Per-attack detection rate

| Attack | Detection rate | Notes |
|--------|----------------|-------|
| `physics_violation` | **100.0 %** | Caught deterministically by the physics validator. |
| `sudden_spike` | **81.2 %** | Over-speed / RPM burst trips both the physics validator and the Isolation Forest. |
| `zero_pollutant` | **5.8 %** | Isolation Forest fails to reliably catch this; feature engineering does not isolate the CO2/speed anomaly. |
| `replay` | **1.8 %** | Temporal checker catches some but the 10-sample sliding window is often insufficient. |
| `frozen_sensor` | **3.0 %** | Same mechanism as replay; current window size and threshold are too permissive. |
| `gradual_drift` | **7.0 %** | Hardest case — a 45 % shift in CO2 sits inside the Isolation Forest's learned distribution tail. |
| `source_aware` | **4.0 %** | Adversary aware of ensemble weights can craft readings that stay below all thresholds. |

### 3.3 Inference latency

| Percentile | Latency (µs) |
|------------|--------------|
| p50 | ~3,090 |
| p95 | ~5,501 |
| p99 | ~6,878 |

Inference is still under 7 ms per sample at p99.  The fraud detector
is not the bottleneck of the data path.

## 4. Discussion

* The physics validator provides a **hard floor** of 100 % recall on
  physically impossible readings. Any attack that maintains physical
  plausibility bypasses it — that is the hard case for the ML components.
* **The ensemble is currently weak on 5 of 7 attack families.**
  Replay, frozen-sensor, gradual-drift, zero-pollutant, and source-aware
  attacks all achieve < 10 % detection. The root cause is twofold:
  (a) the Isolation Forest is trained on only 600 clean samples with 6
  engineered features, limiting its decision surface; and (b) the
  temporal checker's 10-sample sliding window is often insufficient to
  detect subtle replay or drift patterns.
* **Planned improvements:**
  - Increase training set size (≥ 2,000 clean samples).
  - Add per-vehicle adaptive baselines (`test_per_vin_baseline_ml.py`
    already scaffolds this).
  - Reduce temporal window for replay detection from 10 to 5, with a
    separate long-horizon drift detector (Page-Hinkley on 100-sample
    windows).
  - Consider replacing Isolation Forest with an autoencoder
    reconstruction-error detector for higher-dimensional feature space.
* False positives (FP = 156) are dominated by honest high-RPM / low-speed
  segments near the Isolation Forest's training boundary.  They
  correspond to genuinely unusual driving (steep uphill starts, etc.)
  and do not trigger a contract-level violation unless the fraud score
  crosses the 0.65 on-chain threshold.

## 5. Comparison With Prior Work

| System | Technique | Reported F1 | Labelled attacks? |
|--------|-----------|-------------|-------------------|
| Smart PUC v3.2 | Physics + Isolation Forest + Temporal + Drift | **0.423** (held-out) | **Yes — 7 families** |
| Kwon et al. 2021 (CAN bus) | LSTM autoencoder | 0.89 | Yes — 3 families |
| Chen et al. 2021 (blockchain PUC) | Simple rule + blockchain | not reported | No |
| Ahmad et al. 2022 (IoT Journal) | GNN + attention | 0.93 | Yes — 2 families |

Smart PUC's physics validator provides a guaranteed 100 % floor on
impossible-state attacks, but the ensemble's aggregate F1 is below prior
work on the softer attack families.  The planned per-vehicle baseline
and autoencoder improvements (§4) are expected to close this gap.
Inference stays under 7 ms on commodity hardware (no GPU).

## 6. Threats to Validity

* **Synthetic data.** The clean readings come from the WLTC simulator, not
  real on-road traces. Real traces are noisier and would lower recall on
  `gradual_drift`. Mitigation: a planned follow-up uses the `veh-sense`
  open dataset for clean traces.
* **Single vehicle profile.** The simulator uses a single Swift-class
  petrol hatchback. Multi-fuel / multi-class evaluation is future work.
* **Fixed attack parameters.** Each attack family uses one specific
  perturbation magnitude; an adaptive attacker might find weaker settings
  that still achieve the goal. Section 4 acknowledges the `gradual_drift`
  weakness explicitly.

## 7. Raw Output

Running the harness writes `docs/fraud_eval_report.json` with the full
confusion matrix, per-class counts, and latency percentiles. The JSON is
the source of truth — any discrepancy between this Markdown document and
the JSON should be treated as a stale-docs issue.
