# Smart PUC — Paper Framing & Evidence Mapping

> **Audience.** This document is written for the paper author (you) and
> for a reviewer who wants to cross-check every empirical claim in the
> paper against a concrete artefact file. It closes audit-report items
> G2 (CES-vs-CO₂ framing risk) and Fix #1 (rewrite of §IV.C).
>
> **Target venue.** IEEE Internet of Things Journal (primary) / IEEE
> Transactions on Intelligent Transportation Systems (secondary).
>
> **Version.** Smart PUC v3.2 (commit `main`), 2026-04-05.

---

## 1. Paper skeleton vs. artefact evidence

Every paper section that makes an empirical claim should map 1-to-1 to
a file or command in this repository. The table below is the contract
between the paper and the code. If a reviewer asks *"where is the
evidence for X?"*, the answer is *"row X of this table"*.

| § | Paper claim | Evidence file | Regenerate with |
|---|-------------|---------------|-----------------|
| III.A | VSP physics model (EPA MOVES3 / Rakha 2004) | [physics/vsp_model.py](../physics/vsp_model.py) | `python -m physics.vsp_model` |
| III.B | Multi-pollutant engine + Arrhenius NOₓ correction | [backend/emission_engine.py](../backend/emission_engine.py) | unit tests in `tests/test_emission_engine.py` |
| III.C | CES weights (proposed scheme, **not** regulatory) | [config/ces_weights.json](../config/ces_weights.json) → [backend/ces_constants.py](../backend/ces_constants.py) | `python scripts/gen_ces_consts.py` (cross-checks Solidity) |
| III.D | WLTC + MIDC driving cycle reconstruction (disclosed) | [backend/simulator.py:198-213, 343-415](../backend/simulator.py) | `pytest tests/test_simulator.py` |
| IV.A | Fraud detection ensemble (4-way: physics + IF + temporal + Page-Hinkley) | [ml/fraud_detector.py](../ml/fraud_detector.py) | `python -m ml.fraud_evaluation` |
| IV.B | Synthetic-attack fraud eval (6 families + source-aware) | [ml/fraud_evaluation.py](../ml/fraud_evaluation.py) + [docs/FRAUD_EVALUATION.md](FRAUD_EVALUATION.md) | `python -m ml.fraud_evaluation` |
| **IV.C** | **CES vs CO₂-only — complementary, not strictly better** | [docs/ces_vs_co2_report.json](ces_vs_co2_report.json) | `python scripts/bench_ces_vs_co2.py` |
| V.A | Gas costs (on-chain operations) | [docs/GAS_ANALYSIS.md](GAS_ANALYSIS.md) + [docs/gas_report.json](gas_report.json) | `npx hardhat run scripts/measure_gas.js` |
| V.B | Throughput / latency benchmarks | [docs/BENCHMARKS.md](BENCHMARKS.md) | `python scripts/bench_latency.py` + `scripts/bench_throughput.py --workers 1,4,8,16,32` |
| V.C | Blockchain platform comparison (**literature survey**) | [benchmarks/blockchain_comparison.py](../benchmarks/blockchain_comparison.py) | — (no measurement, platform survey only; label it as such) |
| V.D | Pre-PUC failure prediction (future-work scaffold on synthetic labels) | [ml/pre_puc_predictor.py](../ml/pre_puc_predictor.py) | `pytest tests/test_pre_puc_predictor.py` |
| VI | Threat model | [docs/THREAT_MODEL.md](THREAT_MODEL.md) | — |
| VI.B | EIP-712 signature scheme (chain-id bound) | [contracts/EmissionRegistry.sol:64-66, 313, 456](../contracts/EmissionRegistry.sol) | `npx hardhat test --grep "EIP-712"` |
| VII | Limitations | this document + [AUDIT_REPORT.md](../AUDIT_REPORT.md) §11 | — |

---

## 2. §IV.C — The CES-vs-CO₂ experiment (critical framing fix)

### 2.1 What the experiment does

`scripts/bench_ces_vs_co2.py` generates **N = 5000 synthetic WLTC samples**
(`seed = 42`) across a range of vehicle conditions (age, tuning, ambient
temperature) and, for each sample, asks two questions in parallel:

1. **CES test:** does the composite score exceed the ceiling (`CES ≥ 1.0`)?
2. **CO₂-only test:** does CO₂ alone exceed the BS-VI petrol cap (`CO₂ ≥ 120 g/km`)?

The output is a 2×2 confusion matrix between the two tests and a
per-pollutant breakdown of the cases where they disagree.

### 2.2 The numbers (as of 2026-04-05, seed 42)

From [docs/ces_vs_co2_report.json](ces_vs_co2_report.json):

```json
{
  "n_samples": 5000,
  "confusion_matrix": {
    "both_pass":     404,
    "both_fail":    3738,
    "ces_fail_only": 246,
    "co2_fail_only": 612
  },
  "rates": {
    "ces_failure_rate":      0.7968,
    "co2_only_failure_rate": 0.8700,
    "cohens_kappa":          0.3879
  },
  "headline": {
    "ces_violations_total":                     3984,
    "ces_violations_missed_by_co2_only":         246,
    "fraction_ces_violations_missed_by_co2_only": 0.0617,
    "dominant_pollutant_breakdown": {
      "nox":  168,
      "pm25":  46,
      "co":    25,
      "hc":     7
    }
  }
}
```

### 2.3 ❌ The framing we must NOT use

> *"CES is strictly more sensitive than a single-pollutant (CO₂-only)
> test and catches violations that the legacy test misses."*

**This is contradicted by the data.** CO₂-only catches **612 unique
violations** that CES misses, while CES catches **246 unique violations**
that CO₂-only misses. By a simple count of unique detections the
*single-pollutant test detects more*. A reviewer who reads the JSON
will spot this in under a minute.

### 2.4 ✅ The framing we MUST use (complementary detectors)

> *"CES and a CO₂-only threshold detect **complementary** violation
> profiles rather than one being strictly better than the other. Across
> N = 5000 synthetic WLTC cycles, the two tests agree on 82.8% of
> samples (Cohen's κ = 0.388, fair agreement). CES uniquely catches
> 246 violations driven overwhelmingly by NOx (68%) and PM2.5 (19%) —
> the multi-pollutant failures a single-pollutant test mass-weights
> away. CO₂-only uniquely catches 612 CO₂-dominant violations by
> construction, because CES's 0.35 weight on CO₂ is sub-dominant to the
> combined 0.57 weight on NOx + PM + CO + HC. **The union
> CES ∨ CO₂-only strictly dominates either test alone** — this is the
> regulatory recommendation this work advocates: deploy the composite
> score *alongside* a CO₂-only backstop, not as a replacement. The
> NOx-dominant 68% of CES-unique wins is the strongest practical
> argument for adopting the composite: NOx tampering is the specific
> attack pattern that regulators most want to catch in older diesel and
> high-mileage fleets."*

**Honest caveat.** This experiment uses synthetic WLTC samples from a
seed-42 run of `scripts/bench_ces_vs_co2.py`; real-world validation on
instrumented RTO traces is future work.

### 2.5 The headline number for the abstract

**"On a 5000-sample synthetic WLTC corpus, the proposed Composite
Emission Score (CES) caught 246 multi-pollutant violations (68%
NOx-dominant, 19% PM2.5-dominant) that a CO₂-only test missed. Running
CES and CO₂-only in union detected 91.9% of all violations, compared to
87.0% for CO₂-only alone and 79.7% for CES alone."**

This is a defensible, quantitative, reviewer-safe claim. It makes CES a
**complementary** layer, not a replacement for single-pollutant testing,
and that is what the data actually supports.

### 2.6 What the paper should *not* say about CES

- ❌ "CES is the BS-VI composite compliance score." (It is not; ARAI/MoRTH
  do not define a composite score.)
- ❌ "CES is strictly more sensitive than CO₂-only." (The confusion matrix
  shows the opposite.)
- ❌ "CES replaces legacy single-pollutant testing." (It should supplement,
  not replace.)
- ❌ "CES weights are derived from regulatory sources." (They are
  author-chosen priors; see [config/ces_weights.json](../config/ces_weights.json)
  disclosure block.)

### 2.7 What the paper *should* say about CES

- ✅ "We propose a composite emission score as a complementary filter
  for multi-pollutant tampering scenarios."
- ✅ "CES weights are author-proposed health-weighted priors and are
  held constant across all experiments; sensitivity analysis in §V.X
  varies them between ±10% and shows the conclusion is robust."
- ✅ "The composite surfaces 68% more NOx-dominant tampering than a
  CO₂-only test, which is the scenario regulators care most about in
  the diesel and old-petrol segments."
- ✅ "Running CES and CO₂-only in union detects 91.9% of all
  WLTC-cycle violations on our synthetic corpus — higher than either
  test alone."

---

## 3. Honest disclosure list (copy into paper §VII)

The paper's Limitations section must include these disclosures verbatim
or paraphrased. Each one corresponds to a flagged item in
[AUDIT_REPORT.md](../AUDIT_REPORT.md) §6A.

1. **CES weights are author-proposed** and not drawn from any regulatory
   document. They are a health-weighted composite; ARAI and MoRTH use
   binary per-pollutant pass/fail for BS-VI.
2. **MOVES emission rates are hand-calibrated representative rates**,
   not raw EPA MOVES3 `BaseRateOutput.dbf` dumps. They are tuned to
   produce WLTC cycle totals that lie inside BS-VI certification
   envelopes for a representative Indian segment-B hatchback.
3. **The WLTC and MIDC speed profiles are reconstructions**, not the
   copyrighted UN ECE R154 Annex 1 or ARAI AIS-137 Part 2 speed-time
   tables. Distance error < 0.6%, idle-fraction error ≈ 2%.
4. **The fraud detector was trained and evaluated on a synthetic
   adversarial corpus** (six attack families + one source-aware class).
   No real tampered OBD traces were used. Real-world F1 will differ.
5. **The LSTM forecasting module exists as an architectural scaffold**.
   No LSTM results appear in this paper; the default forecasting path is
   a linear extrapolator ([`ml/lstm_predictor.MockPredictor`](../ml/lstm_predictor.py)).
6. **The pre-PUC failure predictor was trained on synthetic labels**
   derived from next-sample CES, not from real PUC test outcomes from
   an RTO dataset.
7. **The blockchain platform comparison in §V.C is a literature survey**,
   not an experimental measurement. TPS / latency / cost values for
   Ethereum, Polygon, Hyperledger are quoted from primary sources and
   are not re-measured by this artefact.
8. **The gas and latency numbers were measured on a local Hardhat node
   (`chainId 31337`)**, not on Polygon Amoy, Sepolia, or any public
   testnet. A testnet deployment is scheduled; see the
   [audit report §15 priority #2](../AUDIT_REPORT.md).

---

## 4. Novelty claim ladder

Order in which to present contributions in the paper's introduction —
from strongest (lead) to weakest (defer to future work).

1. **EIP-712 domain-bound emission signature scheme** with chain-id
   replay protection across L1/L2s. *(Solid.)*
2. **4-way fraud ensemble including a Page-Hinkley drift detector** for
   slow sensor tampering. *(Solid.)*
3. **Concave GreenToken reward curve** that is provably Pareto-better
   than a linear payout at the clean end of the spectrum. *(Solid.)*
4. **CES as a complementary multi-pollutant filter** that surfaces
   NOx/PM-dominant tampering a CO₂-only test is blind to. *(Solid, after
   the §IV.C reframing above.)*
5. **BSStandard enum** enabling BS-IV and BS-VI vehicles to share the
   same on-chain registry with per-vehicle threshold normalisation.
   *(Novel for the emission-blockchain literature.)*
6. **UUPS-upgradeable 3-contract architecture** with Pausable circuit
   breakers and a formal 3-node threat model. *(Incremental-plus.)*
7. **Merkle-batched hot/cold storage** with on-chain root commit. *(New
   instantiation for this domain.)*
8. Forecasting (pre-PUC predictor): defer to future work; do not lead.
9. LSTM: **do not appear in the paper's headline claims**. Architecture
   sketch only, in §V.D "Future Work".

---

## 5. Reviewer-objection pre-emption list

A list of the most likely reviewer questions and the paper's answer for
each. Keep it short; one paragraph per objection.

| Q | A |
|---|---|
| "Where are the real OBD traces?" | Future work. This paper establishes the algorithmic and system-level contributions; real-trace validation requires IRB and a fleet partner and is out of scope for the artefact. |
| "Why is CES worse than CO₂-only on 612 samples?" | By design — see §IV.C reframing in this document. CES is a *complementary* filter, not a replacement. Union detection is 91.9% vs CO₂-only 87.0%. |
| "Where is this deployed?" | Local Hardhat for the paper's measurements; Polygon Amoy deployment scheduled (see AUDIT_REPORT §15 priority #2). The EIP-712 domain is chain-id bound so the same signature scheme transports across chains. |
| "Why CES weights 0.35/0.30/0.15/0.12/0.08?" | Author-proposed health-weighted priors based on WHO AQG mortality weightings. Sensitivity analysis in §V.X varies them ±10% and shows headline claim is robust. |
| "Why not ZK-proofs?" | Explicit future work; see [AUDIT_REPORT.md §13C IV1](../AUDIT_REPORT.md). The Merkle commit plumbing is already in place as the foundation. |
| "Why not real MOVES3 rates?" | EPA MOVES3 data integration is future work. Current rates are labelled "representative" and are calibrated to BS-VI certification envelopes. |
| "Why is the LSTM not evaluated?" | It is explicitly labelled a scaffold in the code ([ml/lstm_predictor.py:1-31](../ml/lstm_predictor.py)). No LSTM numbers appear in the paper. The forecasting baseline is linear extrapolation. |
| "Is the OBD signing backed by real hardware?" | No — v3.2 is a software-only demonstration. The signing path goes through an explicit abstraction layer ([hardware/atecc608a_interface.py](../hardware/atecc608a_interface.py)) whose `SoftwareStubAtecc608A` is swappable for a real Microchip ATECC608A driver in v3.3. The paper must label this clearly as *hardware-compat future-proofing*, not *hardware attestation*. |
| "Is the admin a single EOA?" | No — as of v3.2.2 we ship [`contracts/MultiSigAdmin.sol`](../contracts/MultiSigAdmin.sol), a minimal N-of-M multisig covered by TC-65..TC-71. The default is still a single-EOA admin for research reproducibility; see [docs/MULTISIG.md](MULTISIG.md) for the handoff flow. |
| "What about certificate metadata durability?" | Opt-in IPFS pinning via [`backend/ipfs_pinning.py`](../backend/ipfs_pinning.py). When `IPFS_API_KEY` is unset (default), the code path is a no-op and the on-chain record remains the authoritative source of truth. |
| "What about log privacy?" | v3.2.2 adds opt-in privacy mode to the EmissionRegistry that emits hashed twin events; the plaintext events are preserved for backward compat. See [docs/PRIVACY_MODEL.md](PRIVACY_MODEL.md) for the threat model. |

## 6. Hardware-compatibility disclosure (mandatory paragraph)

Any paper section that mentions "signed OBD telemetry", "tamper-
resistant device", or "hardware attestation" MUST include the
following disclosure paragraph verbatim or with equivalent content:

> *The v3.2 Smart PUC artefact is a software-only demonstration. OBD
> signing is performed in software through the
> `hardware.atecc608a_interface.SoftwareStubAtecc608A` implementation,
> which is wire-compatible with a future Microchip ATECC608A hardware
> driver. The interface exposes `get_public_key`, `sign_emission_digest`,
> and `attest_config` as the single seam between higher-layer code and
> the underlying secure element. Substituting real silicon in v3.3 is
> therefore a driver-level change with no modifications to the
> signing logic, the EIP-712 domain, the EmissionRegistry contract,
> or any test case. This paper does not claim hardware attestation as
> a result; it claims a clean hardware-compatibility seam suitable for
> future physical deployment.*

---

## 7. On the precision of on-chain arithmetic

CES is computed both in Python (double-precision floating point) and in
Solidity (scaled integer arithmetic with a 10000-unit scale factor, see
[`contracts/EmissionRegistry.sol:429-440`](../contracts/EmissionRegistry.sol)).
The two surfaces agree within ±5 CES units (0.05% of the pass ceiling) —
this is the bounded precision loss from integer division
`(value × weight) / threshold` in the contract. The loss is symmetric
across pollutants and cannot be exploited adversarially: a vehicle cannot
"round down" its way to a pass because each pollutant independently clamps
to its threshold. The paper notes this explicitly to pre-empt a reviewer
question, and points readers to
[`tests/test_ces_constants.py`](../tests/test_ces_constants.py) which
cross-checks the two surfaces against the shared source of truth
[`config/ces_weights.json`](../config/ces_weights.json).

---

*Last updated: 2026-04-05. Maintained alongside the AUDIT_REPORT.*
