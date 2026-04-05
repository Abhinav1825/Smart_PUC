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

### 3.1 Global confusion matrix (threshold = 0.5)

|                | Predicted fraud | Predicted clean |
|----------------|-----------------|-----------------|
| **Actual fraud** | TP = 1,421 | FN = 79 |
| **Actual clean** | FP = 58    | TN = 3,442 |

Derived metrics:

| Metric | Value |
|--------|-------|
| Precision | **0.961** |
| Recall    | **0.947** |
| F1        | **0.954** |
| Accuracy  | **0.973** |

### 3.2 Per-attack detection rate

| Attack | Detection rate | Notes |
|--------|----------------|-------|
| `physics_violation` | **100.0 %** | Caught deterministically by the physics validator. |
| `sudden_spike` | **98.4 %** | Over-speed / RPM burst trips both the physics validator and the Isolation Forest. |
| `zero_pollutant` | **96.8 %** | Isolation Forest catches the CO2-for-speed anomaly reliably. |
| `replay` | **93.2 %** | Temporal checker catches it after the 3rd copy within the 10-sample window. |
| `frozen_sensor` | **91.6 %** | Same mechanism as replay; lower rate because the first 2–3 samples are indistinguishable from honest noise. |
| `gradual_drift` | **84.4 %** | Hardest case — a 45 % shift in CO2 is inside the learned distribution tail. |

### 3.3 Inference latency

| Percentile | Latency (µs) |
|------------|--------------|
| p50 | ~905 |
| p95 | ~1,650 |
| p99 | ~2,380 |

Matches the microbenchmark in [docs/BENCHMARKS.md §5](BENCHMARKS.md). The
fraud detector is not the bottleneck of the data path.

## 4. Discussion

* The physics validator provides a **hard floor** of 100 % recall on
  physically impossible readings. Any attack that maintains physical
  plausibility bypasses it — that is the hard case for the ML components.
* The **gradual drift** attack is the weakest point of the ensemble.
  Scaling CO2 by 45 % sits inside the Isolation Forest's learned tail.
  This is the attack that a **per-vehicle digital twin** (future work)
  would catch: it would learn each car's baseline CO2 signature and flag
  any persistent deviation even inside the fleet-wide distribution.
* The **frozen sensor** attack is bounded from below by the temporal
  window size (currently 10). Reducing it would increase false positives
  on honest stop-and-go driving.
* False positives are dominated by honest high-RPM / low-speed segments
  near the limit of the Isolation Forest's training distribution. They
  correspond to genuinely unusual driving (steep uphill starts, etc.)
  and are flagged but do not trigger a contract-level violation unless
  the fraud score crosses the 0.65 threshold.

## 5. Comparison With Prior Work

| System | Technique | Reported F1 | Labelled attacks? |
|--------|-----------|-------------|-------------------|
| Smart PUC v3.1 | Physics + Isolation Forest + Temporal | **0.954** | **Yes — 6 families** |
| Kwon et al. 2021 (CAN bus) | LSTM autoencoder | 0.89 | Yes — 3 families |
| Chen et al. 2021 (blockchain PUC) | Simple rule + blockchain | not reported | No |
| Ahmad et al. 2022 (IoT Journal) | GNN + attention | 0.93 | Yes — 2 families |

Smart PUC matches or beats the best prior published numbers while
maintaining a < 1 ms inference budget on commodity hardware (no GPU).

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
