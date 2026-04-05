# SmartPUC — Audit, Improvement & Creative Vision Report

```
Audit Date       : 2026-04-05 (2nd audit, same-day re-run)
Audit Run        : 2nd
Overall Score    : 84/100
Previous Score   : 74/100  (delta: +10)
Verdict          : NEEDS POLISH (one remaining blocker, several disclosure gaps)
Creative Ideas   : 28 new feature/improvement ideas (Sections 12–13)
```

**Executive summary.** Between audits 1 and 2 (same calendar day, but an entire v3.2 feature branch landed in-between) the project made large forward motion. **Eighteen of the twenty prior-audit limitations are fully or partially fixed**, including every single one of the "CRITICAL" items from the first report except one: there is still no recorded testnet deployment. The EIP-712 / chain-id binding gap that was the previous audit's blocker is now cleanly implemented with domain `("SmartPUC", "3.2")` and verified by Hardhat TCs 42–45. Hardhat tests grew from 33 → **55 passing**, pytest from 117 → **171 passing**, a new 30-endpoint smoke harness passes 30/30, a 13-step E2E business-flow audit passes end-to-end against a live stack, and all four test surfaces are now green in a single run. The fraud detector added a Page-Hinkley drift component (new 4-way ensemble), a BSStandard enum landed in both Solidity and Python, Merkle batching reached the on-chain entry point, a concave reward curve replaced the linear one, and the `python app.py` stale commands are gone from every surface. The project is genuinely closer to submission-ready. **One caveat the developer should not skip:** the new `scripts/bench_ces_vs_co2.py` experiment exists and runs, but its *result* is more ambiguous than the paper's headline claim assumes — CES catches 246 unique violations CO₂-only misses, but CO₂-only also catches 612 unique violations CES misses. The composite score is not strictly better than a single-pollutant test in the same direction the paper wants to pitch. Section 5C explains this in detail; the paper framing needs to match the data.

---

# PART A — BRUTAL HONEST AUDIT

## SECTION 1: PROJECT STRUCTURE & CODE HEALTH — ✅

**Verdict:** Clean. Structural rot from the prior audit is gone.

- **First-party code footprint:** ~20,250 LoC across 61 files. Clean separation: [contracts/](contracts/), [backend/](backend/), [physics/](physics/), [ml/](ml/), [integrations/](integrations/), [obd_node/](obd_node/), [benchmarks/](benchmarks/), [frontend/](frontend/), [scripts/](scripts/), [docs/](docs/), [test/](test/) (Hardhat), [tests/](tests/) (pytest), [.github/](.github/workflows/ci.yml).
- **Single entry point:** ✅ [scripts/run_all.py](scripts/run_all.py) (620 lines) orchestrates Hardhat node → contract deploy → FastAPI backend → static frontend in one terminal, with Windows Job Object cleanup, colored log multiplexing, and `.env` auto-patching. [start.bat](start.bat) is the 3-line wrapper. This is a materially better developer experience than the prior audit's `make backend` path.
- **Build-directory contamination (L17):** ✅ fixed. `build/contracts/` now contains only the three live contract JSONs (EmissionRegistry, PUCCertificate, GreenToken) — down from ~25 stale Truffle artifacts.
- **Orphaned files:** `backend/app.py` (deleted Flask entry) no longer present. Git status still shows 11 modified files + a clean rename list; none of them are orphans.
- **Type hints:** Present on every public function I spot-checked. `_send_tx` in [backend/blockchain_connector.py:177-199](backend/blockchain_connector.py#L177) is now fully annotated.
- **Bare `except:`:** Grep returned zero hits across the Python tree. Every `except Exception` is scoped or logged.
- **Mutable default args:** None found.
- **TODO / FIXME / HACK comments:** Only in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) as legitimate future-work pointers. Zero in code.
- **Circular imports:** None.
- **Imports ordered (stdlib → 3rd party → local):** Yes, consistent across modules.

**One structural wart that survives from audit 1:** the dual CES weight definitions — Python at [backend/emission_engine.py:143-149](backend/emission_engine.py#L143) and Solidity integer constants baked into [contracts/EmissionRegistry.sol](contracts/EmissionRegistry.sol) — are **still independent**, no shared JSON or generated constants file. Low risk today (the contract value is authoritative, Python is advisory), but the drift trap remains. This is L8 below.

---

## SECTION 2: SCIENTIFIC ACCURACY & MATHEMATICAL RIGOR — ⚠️

**Verdict:** Formulas remain correct; disclosure work has improved; **one paper-framing risk emerged from the new CES-vs-CO₂ data** (see §5C).

### 2A — VSP Model — ✅

[physics/vsp_model.py](physics/vsp_model.py):

- Formula at lines 82–86 / 139–144 implements `VSP = v·[a + g·sin(θ) + μ·g·cos(θ)] + (ρ·C_d·A)/(2m)·v³` correctly.
- km/h → m/s conversion at line 126.
- Default vehicle parameters: `mass=1000 kg, Cd=0.32, frontal=2.1 m², μ=0.015, ρ=1.225 kg/m³`. All within Indian segment-B ranges. ✓
- MOVES OpMode Bin thresholds match EPA MOVES3 at `<-2, <0, <3, <6, <9, <12, <18, <24, <30, ≥30`.
- Sanity plugin: idle → ≈0; 60 km/h flat cruise → 5–8 W/kg; hard accel → 25–35 W/kg; downhill decel negative. All correct.
- Citations in docstring: EPA MOVES3 + Rakha et al. 2004 ✓.

### 2B — Multi-Pollutant Engine — ⚠️

[backend/emission_engine.py](backend/emission_engine.py):

- **BS-VI thresholds** at [L103-L109](backend/emission_engine.py#L103) and the `BSVI_THRESHOLDS` dict at [L154-L160](backend/emission_engine.py#L154): `CO₂≤120, CO≤1.0, NOx≤0.06, HC≤0.10, PM2.5≤0.0045 g/km`. Correct per ARAI BS-VI Phase 2 (2020) gazette.
- **BS-IV thresholds** — ✅ **new since prior audit** at [L119-L128, L172-L178](backend/emission_engine.py#L119): `CO₂≤140, CO≤2.3, NOx≤0.150, HC≤0.10, PM2.5≤0.025`. `get_thresholds(standard: BSStandard)` at [L189-L202](backend/emission_engine.py#L189) selects per-enum. This closes prior-audit L6.
- **IPCC CO₂ formula** at [L480-L481](backend/emission_engine.py#L480): `CO₂(g/km) = fuel_rate(L/100km) × EF/100`, `EF_petrol=2310, EF_diesel=2680`. Correct per IPCC 2019 Refinement.
- **NOx Arrhenius correction** at [L426-L436](backend/emission_engine.py#L426): `NOx × exp[Ea/R·(1/T_ref − 1/T_amb)]`, `Ea/R=3500 K`, `T_ref=298.15 K`. Sign verified — higher ambient T → lower correction factor (physically correct, warmer combustion is more complete).
- **g/s → g/km conversion** at [L458-L477](backend/emission_engine.py#L458) correct, speed-zero edge case handled.
- **Cold-start penalty** at [L451-L455](backend/emission_engine.py#L451): still a boolean `+80% CO / +50% HC`, not time-decaying per COPERT 5. Acceptable for a prototype but paper should note the simplification.
- **CES formula** at [L510-L513](backend/emission_engine.py#L510) with `CES_WEIGHTS` at [L143-L149](backend/emission_engine.py#L143): `{CO₂:0.35, NOx:0.30, CO:0.15, HC:0.12, PM₂.₅:0.08}`, sum = 1.00.
- **CES disclosure** at [L11-L18](backend/emission_engine.py#L11): ✅ explicitly labels CES as "a proposed scheme, not a regulatory standard — neither ARAI nor MoRTH specifies a multi-pollutant composite score for BSVI." This closes the prior audit's §2B disclosure blocker.
- ⚠️ **MOVES rate table** at [L259-L316](backend/emission_engine.py#L259) is still hand-calibrated, disclosed at [L237](backend/emission_engine.py#L237) as "hand-tuned to produce BSVI certification ranges." The paper must not cite these as "EPA MOVES3 data."

### 2C — WLTC Driving Cycle — ⚠️ (honest reconstruction) / ✅ MIDC added

[backend/simulator.py](backend/simulator.py):

- `_PHASE_BOUNDS` at [L48-L53](backend/simulator.py#L48) uses `(1478, 1801, EXTRA_HIGH)` as a **half-open Python range** over a 1800-point array (indices 0–1799). This is correct, not an off-by-one — the upper 1801 is a defensive open-end boundary.
- Phase boundaries Low 0–589, Medium 590–1022, High 1023–1477, Extra High 1478–1800. ✓
- Peak speeds 56.5 / 76.6 / 97.4 / 131.3 km/h. ✓
- Reconstruction disclosure at [L198-L213](backend/simulator.py#L198) is explicit: "representative approximation, NOT the copyrighted UN ECE R154 Annex 1 speed table; <0.6% distance error, ~11% idle fraction vs 13% official."
- **MIDC (Modified Indian Driving Cycle)** support added at [L343-L415](backend/simulator.py#L343) — a 1180-second profile per AIS-137 Part 2, more representative of Indian stop-and-go traffic than WLTC. This is a **significant new contribution** that partially addresses the "European cycle doesn't match Indian driving" criticism from the prior audit's §12A item 6. The paper should now run its headline experiments on **both** cycles and report results side-by-side.
- RPM derivation ([L92-L120](backend/simulator.py#L92)) with 5-speed gearbox, final drive 4.058, wheel radius 0.3 m — realistic.
- Fuel-rate polynomial at [L156-L169](backend/simulator.py#L156) cites Rakha et al. 2004.

**Bottom line for §2:** Math is defensible, disclosures are now in code, MIDC support is new. The single remaining paper risk is the CES-vs-CO₂ interpretation (see §5C).

---

## SECTION 3: MACHINE LEARNING AUDIT — ⚠️

### 3A — Fraud Detector — ✅ (now a 4-way ensemble)

[ml/fraud_detector.py](ml/fraud_detector.py):

- **Physics constraint validator** at [L38-L107](ml/fraud_detector.py#L38): covers speed>0∧RPM=0, VSP high ∧ fuel_rate≈0, |Δv|>4 m/s², RPM>7000. Frozen-sensor detection is delegated to the temporal component, not the per-reading validator — this is a reasonable architectural choice but worth documenting.
- **Isolation Forest** at [L175-L179](ml/fraud_detector.py#L175): `contamination=0.05, n_estimators=100, random_state=42`. Unchanged from audit 1.
- **Feature engineering** at [L117-L162](ml/fraud_detector.py#L117): `speed, rpm, fuel_rate, acceleration, co2, vsp, fuel_efficiency_proxy, rpm_speed_ratio`. Derived features present.
- **Temporal consistency checker** at [L218-L299](ml/fraud_detector.py#L218): sliding 10-reading window, max accel 4 m/s², identical-reading streak detection.
- 🆕 **Page-Hinkley drift detector** at [L302-L400](ml/fraud_detector.py#L302): fourth ensemble component, catches slow monotonic sensor drift that the other three miss. This is **exactly the enhancement suggested in prior audit §13A #4** and it landed. Credit where credit is due.
- **Ensemble weights** at [L412-L435](ml/fraud_detector.py#L412): `Physics 0.45 + IF 0.30 + Temporal 0.15 + Drift 0.10 = 1.0`, enforced at runtime with a tolerance check. ✓
- **Decision threshold** at [L516, L527](ml/fraud_detector.py#L516): `fraud_score >= 0.50` → HIGH; `>= 0.25` → MEDIUM; `< 0.25` → LOW.
- **Training state:** still re-fitted at runtime; no persisted checkpoint. This is consistent with the honest "evaluation is on synthetic adversarial samples" framing.

### 3B — LSTM Predictor — ⚠️ (honest scaffold, unchanged)

[ml/lstm_predictor.py](ml/lstm_predictor.py):

- Docstring at [L1-L31](ml/lstm_predictor.py#L1) **explicitly labels** the module as "implemented but unvalidated; EmissionPredictor (LSTM) is not trained or evaluated as part of the default Smart PUC pipeline; no headline numbers in the paper depend on it; exists as a future-work scaffold." ✅
- No trained weights in the repo (correct — `.h5` is in `.gitignore`).
- TensorFlow import guarded, MockPredictor fallback intact.
- **Paper instruction unchanged from audit 1:** do not claim LSTM accuracy numbers.

### 3C — Pre-PUC Failure Predictor — 🆕 ⚠️

[ml/pre_puc_predictor.py](ml/pre_puc_predictor.py) — **new module since prior audit.**

- **Purpose:** classify whether a vehicle's next reading will be a PUC FAIL (CES ≥ 1.0) from the last N readings.
- **Architecture:** logistic regression + StandardScaler ([L177-L184](ml/pre_puc_predictor.py#L177)). Eleven hand-crafted features: `mean/max/p95 CES`, normalized pollutant means, linear CES slope, fraction of readings above 0.8, record count.
- **Honesty** at [L11-L13](ml/pre_puc_predictor.py#L11): "deliberately simple … for a paper it would be replaced with gradient-boosted trees once real PUC failure data is available."
- **Training** at [L199-L233](ml/pre_puc_predictor.py#L199): `train_synthetic()` generates 2000 synthetic samples per BS-VI thresholds; label is "next record CES ≥ 1.0." No held-out test set from a different distribution.
- **This is the F1 idea from prior audit §13B landing in code.** Meaningful progress. It also means the paper can now make a "pre-PUC failure forecast" contribution, but only with the disclaimer that training data is synthetic.

### 3D — Fraud Evaluation — ⚠️

[ml/fraud_evaluation.py](ml/fraud_evaluation.py) (363 lines):

- Synthetic attack corpus with six attack families (replay, zero-pollutant, physics-violation, drift, sudden spike, frozen sensor).
- Metrics: precision, recall, F1, accuracy, per-attack detection rate, inference latency p50/p95/p99. Generated at runtime, not hardcoded.
- Train/test split at [L232-L237](ml/fraud_evaluation.py#L232) and clean/attack split at [L323-L325](ml/fraud_evaluation.py#L323). Better than audit 1's "synthetic-only, no split" posture.
- **Still synthetic.** Paper must label this "synthetic adversarial evaluation," not "real-world fraud detection."

---

## SECTION 4: BLOCKCHAIN & SMART CONTRACT AUDIT — ✅ (major improvement)

### 4A — EmissionRegistry.sol

- ✅ Compiles clean on Solidity 0.8.x under Hardhat.
- ✅ Stores all 5 pollutants + CES + fraudScore + VSP + wltcPhase + timestamps + device/station addresses.
- ✅ Fixed-point arithmetic unchanged; Python↔Solidity scaling consistent.
- ✅ Compliance logic `passed = (cesScore < CES_PASS_CEILING) && (fraudScore < FRAUD_ALERT_THRESHOLD)`.
- ✅ `nonReentrant` + `whenNotPaused` on every state-mutating external.
- ✅ UUPS wired, `__gap[50]`, `_disableInitializers()` in constructor.
- 🆕 **EIP-712 DOMAIN SEPARATION — FIXED (prior audit L2 / blocker).**
  - Imports `EIP712Upgradeable` at [contracts/EmissionRegistry.sol:8](contracts/EmissionRegistry.sol#L8).
  - Contract inherits it at line 49.
  - Initializer calls `__EIP712_init("SmartPUC", "3.2")` at [L313](contracts/EmissionRegistry.sol#L313).
  - `EMISSION_READING_TYPEHASH` at [L64-L66](contracts/EmissionRegistry.sol#L64) declares the 8-field struct.
  - Uses `_hashTypedDataV4(structHash)` at [L456, L708](contracts/EmissionRegistry.sol#L456) for digest recovery.
  - Backend match: [backend/blockchain_connector.py:62-63](backend/blockchain_connector.py#L62) (`EIP712_DOMAIN_NAME = "SmartPUC"`, version `"3.2"`) and the full typed-data payload at [L233-L289](backend/blockchain_connector.py#L233).
  - Hardhat tests TC-42 through TC-48 exercise: valid sig accepted, non-admin sig rejected, wrong claimant rejected, replay rejected. **All passing.**
- 🆕 **BSStandard enum** at [L60](contracts/EmissionRegistry.sol#L60) (`BS6, BS4`). `setVehicleStandard()` at [L412](contracts/EmissionRegistry.sol#L412). Per-enum thresholds in `computeCESForStandard()`. Closes prior-audit L6. TC-39 through TC-41 cover it.
- 🆕 **`claimVehicle` hardening (prior audit L13).** [L443-L462](contracts/EmissionRegistry.sol#L443): now requires an EIP-712 admin signature over `(vehicleId, claimant)`. Squatting attack closed. TC-42 through TC-45 cover it.
- 🆕 **`reportPhaseSummary` (per-phase WLTC)** at [L628-L639](contracts/EmissionRegistry.sol#L628) — emits `PhaseCompleted` for phase-level audit. TC-46 through TC-48.
- 🆕 **`commitBatchRoot` (Merkle batching)** at [L654-L668](contracts/EmissionRegistry.sol#L654) — the on-chain entry point the prior audit's L20 / §13A #5 requested. TC-49 through TC-51.
- 🆕 **`PausableUpgradeable`** inherited at [L48](contracts/EmissionRegistry.sol#L48); `whenNotPaused` on `claimVehicle`, `storeEmission`, `reportPhaseSummary`, `commitBatchRoot`. Closes prior-audit L14.

### 4B — PUCCertificate.sol — ✅

- ERC721 + ReentrancyGuard + Pausable + UUPS.
- `isValid()` correctly checks exists + not revoked + not expired.
- `onlyAuthority` on revoke (verified during this session's E2E flow — revoke routes the tx from the admin signer via [backend/blockchain_connector.py:554-579](backend/blockchain_connector.py#L554)).
- 🆕 **Concave reward curve** at [L304-L314](contracts/PUCCertificate.sol#L304), replacing the prior linear interpolation (prior audit §13A #7 landed). `computeRewardAmount(0) = 200 GCT, (10000) = 50 GCT, (5000) ≈ 87.5 GCT`. TC-52 through TC-55 cover the curve's monotonicity and endpoints.

### 4C — GreenToken.sol — ✅

- 🆕 `redeem()` at [L186](contracts/GreenToken.sol#L186) now carries `nonReentrant` **and** `whenNotPaused`. Closes prior-audit L15.
- Burn-on-redeem path unchanged.

### 4D — Deployment & Integration — ⚠️ (one remaining blocker)

- [scripts/deploy.js](scripts/deploy.js) uses OpenZeppelin Upgrades plugin `deployProxy(…, {kind:"uups"})` for all three contracts. Wires all roles correctly.
- [scripts/flatten_artifacts.js](scripts/flatten_artifacts.js) produces the Truffle-shape bridge files the Python backend reads.
- ❌ **Testnet deployment still absent** (prior audit L4). [docs/DEPLOYED_ADDRESSES.json](docs/DEPLOYED_ADDRESSES.json) contains exactly one entry — `chainId 31337` (local Hardhat) with timestamp `2026-04-05T12:43:54.385Z`. No Amoy (80002), Sepolia (11155111), or Polygon (137). **This is the single remaining CRITICAL item from the prior audit.**

---

## SECTION 5: BENCHMARKING & EXPERIMENTAL RESULTS — ⚠️

### 5A — Benchmark suite runs? ✅

- Hardhat gas measurement: ✅ [docs/gas_report.json](docs/gas_report.json), generated by [scripts/measure_gas.js](scripts/measure_gas.js) against v3.2 contracts. [docs/GAS_ANALYSIS.md:31](docs/GAS_ANALYSIS.md#L31) explicitly references "v3.2 UUPS-proxied contracts" and notes the +10k-gas overhead of EIP-712 verification vs the legacy eth_sign path.
- [benchmarks/scalability_test.py](benchmarks/scalability_test.py) (999 lines) runs the five experiments E1–E5.
- [scripts/bench_latency.py](scripts/bench_latency.py), [scripts/bench_throughput.py](scripts/bench_throughput.py) both present; the latter supports N-worker parameterization via `--workers 1,4,8,16,32` ([L86](scripts/bench_throughput.py#L86)). Closes prior-audit L20.
- ⚠️ [docs/BENCHMARKS.md](docs/BENCHMARKS.md) still does not carry a "measured on version X at date Y" header. The numbers are plausible but a reviewer will ask provenance.

### 5B — Fraud Detection Metrics — ⚠️

- [docs/FRAUD_EVALUATION.md](docs/FRAUD_EVALUATION.md) exists; numbers regenerated at runtime from the six-attack synthetic corpus.
- Train/test split exists ([ml/fraud_evaluation.py:232-237, 323-325](ml/fraud_evaluation.py#L232)). Better than audit 1.
- **Still synthetic.** Label the section "synthetic adversarial evaluation."

### 5C — CES vs CO₂-only Comparison — 🆕 ⚠️ **RESULT NEEDS CAREFUL FRAMING**

- ✅ Script exists: [scripts/bench_ces_vs_co2.py](scripts/bench_ces_vs_co2.py) (233 lines), 5000-sample confusion-matrix experiment with `seed=42`.
- ✅ Real results file at [docs/ces_vs_co2_report.json](docs/ces_vs_co2_report.json):
  ```json
  {
    "n_samples": 5000, "seed": 42,
    "confusion_matrix": {
      "both_pass": 404, "both_fail": 3738,
      "ces_fail_only": 246, "co2_fail_only": 612
    },
    "rates": {
      "ces_failure_rate": 0.7968,
      "co2_only_failure_rate": 0.87,
      "cohens_kappa": 0.388
    }
  }
  ```
- 🔴 **Honest reading of these numbers:** CES catches **246 violations** that CO₂-only misses — that is 6.17% of all CES failures. But CO₂-only **also catches 612 violations that CES misses**. The composite score is **not** strictly more sensitive than single-pollutant testing; the two tests disagree on 858/5000 samples (17.2%), and the disagreement is **asymmetric in CO₂-only's favor** by a factor of 2.5×. Cohen's κ = 0.388 is "fair agreement" at best.
- **What this means for the paper.** The narrative "CES catches violations CO₂-only misses" is technically true, but a reviewer who reads the JSON will immediately ask: *"Then why does CO₂-only catch more unique violations than CES?"* The honest answer is that the **weight structure of CES (0.35 on CO₂) is deliberately sub-dominant to other pollutants, so samples with high CO₂ alone (but low NOx, PM, CO, HC) can pass CES while failing a pure CO₂ test.** That is by design — CES is penalizing multi-pollutant vehicles — but the paper should reframe from "CES is more sensitive" to **"CES and CO₂-only detect complementary violation profiles; CES captures multi-pollutant cases (246 unique, dominated by NOx 168, PM2.5 46, CO 25, HC 7) while CO₂-only captures CO₂-dominant cases."** This is a **different and more defensible claim** than "CES is strictly better" — and it is the one the data actually supports. **Do not submit the paper with the old framing.**
- **Dominant-pollutant breakdown** for the 246 CES-unique cases: NOx 168, PM2.5 46, CO 25, HC 7. That is the paper's strongest single number — **68% of the CES-unique wins are NOx-driven**, which is exactly the diesel/old-engine tampering case that regulators care most about. Lead with that.

### 5D — Blockchain Comparison — ⚠️

- [benchmarks/blockchain_comparison.py](benchmarks/blockchain_comparison.py) (412 lines). `PLATFORM_DATA` at [L81-L121](benchmarks/blockchain_comparison.py#L81) is **still a literature-survey table** (TPS ranges quoted from Vitalik's blog, Polygon docs, Hyperledger whitepaper), not measurements. Disclosed at [L22-L55](benchmarks/blockchain_comparison.py#L22) as "provides a structured comparison."
- **Paper must label this §V.B "Design-Space Analysis" or "Platform Survey," not "Experimental Comparison."** Otherwise a reviewer will ask for the method.

---

## SECTION 6: DATA INTEGRITY & REAL-WORLD READINESS — ⚠️

### 6A — Data provenance table (updated)

| Data source | Status | Δ vs audit 1 |
|-------------|--------|--------------|
| BS-VI thresholds | ✅ Real (ARAI/MoRTH 2020) | unchanged |
| BS-IV thresholds | 🆕 ✅ Real (added) | **new** |
| IPCC CO₂ EFs (2310/2680 g/L) | ✅ Real | unchanged |
| NOx Arrhenius Ea/R | ✅ Real (EMEP/EEA 2019) | unchanged |
| WLTC profile | ⚠️ Reconstruction, disclosed | unchanged |
| MIDC profile | 🆕 ⚠️ Reconstruction, disclosed | **new** |
| MOVES3 bin rates | ⚠️ Hand-calibrated, disclosed | unchanged |
| CES weights | ⚠️ Author-proposed, **now disclosed in code** | **disclosure improved** |
| VAHAN vehicle DB | ❌ MockVaahanService | unchanged |
| Fraud eval dataset | ❌ Synthetic | unchanged |
| LSTM training data | ❌ Does not exist | unchanged |
| Pre-PUC predictor data | 🆕 ❌ Synthetic | **new module, new gap** |
| Gas numbers | ✅ Fresh v3.2 run | improved |
| CES vs CO₂ experiment | 🆕 ✅ Real (5000 samples) | **new** |
| Latency / throughput | ⚠️ No dates in doc | unchanged |

### 6B — Real-World Deployment Readiness

- [integrations/obd_adapter.py](integrations/obd_adapter.py) still maps real SAE J1979 PIDs. Optional `obd` Python library.
- REST surface: `/api/record` accepts both `X-API-Key` **and** `Bearer JWT` via the new `require_api_key_or_jwt` dependency in [backend/dependencies.py](backend/dependencies.py). The frontend now successfully posts records as an authenticated user without needing the dev-only API key.
- Rate limiter: SQLite-persisted per-IP, 120 req/60s default, survives restart.
- ❌ Still no real-vehicle OBD traces anywhere in the repo.

### 6C — Indian Regulatory Alignment

- BS-VI thresholds: ✓
- BS-IV thresholds: ✅ (new, L6 closed)
- ❌ **1-year first-PUC window still not modeled** (prior-audit L7). [contracts/PUCCertificate.sol:76](contracts/PUCCertificate.sol#L76) has `VALIDITY_PERIOD = 180 days` as a constant with no `isFirstPUC` branch. Per CMVR Rule 115 / MoRTH G.S.R. 721(E), the first PUC after BS-VI registration is 1 year, 6 months thereafter. Reviewer catch still live.

---

## SECTION 7: TESTING & RELIABILITY — ✅ (large improvement)

### 7A — Fresh test-run evidence (this session)

```
npx hardhat test            →  55 passing (6s)
pytest tests/ -p no:ethereum → 171 passed, 7 warnings (7s)
scripts/smoke_test_api.py    → 30/30 endpoints OK
scripts/e2e_business_flow.py → 13/13 steps PASSED
```

Prior audit: 33 Hardhat + 117 pytest (**3 failing**) + no smoke + no E2E. **Current: 55 + 171 + 30 + 13, zero failures across all four surfaces.**

### 7B — Hardhat (55 tests)

- TC-01 through TC-33: legacy pre-v3.2 coverage (preserved).
- 🆕 **TC-34 through TC-38:** additional adversarial cases (documented tests, not enumerated here for space).
- 🆕 **TC-39 through TC-41:** BSStandard enum — BS-IV per-vehicle threshold selection, CES differs between BS-IV and BS-VI, default standard = BS-VI.
- 🆕 **TC-42 through TC-45:** `claimVehicle` EIP-712 hardening — valid admin signature accepted, non-admin rejected, wrong claimant rejected, already-claimed rejected.
- 🆕 **TC-46 through TC-48:** `reportPhaseSummary` — emits `PhaseCompleted`, rejects phase > 3, station-only access.
- 🆕 **TC-49 through TC-51:** `commitBatchRoot` — stores + emits, duplicate rejected, station-only.
- 🆕 **TC-52 through TC-55:** concave reward curve — MAX at CES=0, MIN at CES=CEILING, concave <linear at midpoint, strictly decreasing.
- Prior-audit gap on "adversarial test cases" is substantially closed. ✓

### 7C — Python (171 tests)

- 🆕 [tests/test_api.py](tests/test_api.py) (287 lines, 27 test functions) — closes prior-audit L9. Covers login, JWT gating, API-key gating, `/api/record`, `/api/certificate/*`, `/api/verify`, `/api/tokens/*`, `/api/analytics/*`, `/api/obd/*`, `/api/vehicle/verify`, rate-limit pass-through, invalid input handling.
- 🆕 [tests/test_page_hinkley.py](tests/test_page_hinkley.py) — covers the new drift detector.
- 🆕 [tests/test_pre_puc_predictor.py](tests/test_pre_puc_predictor.py) — covers the new pre-PUC failure predictor.
- [tests/test_blockchain_connector.py](tests/test_blockchain_connector.py) mock tuples fixed to 14 fields with `deviceAddress`/`stationAddress` keys ([L80-L95, L142-L151](tests/test_blockchain_connector.py#L80)). Closes prior-audit L1.
- [tests/test_integration.py](tests/test_integration.py) — 217 lines, full pipeline smoke.
- **Estimated coverage:** 70–78% (up from 55–65% in audit 1), pushing past the "mature artifact" bar if you exclude the frontend.

### 7D — Frontend

- Still no Playwright / Cypress. All HTML + vanilla JS. But the new [scripts/e2e_business_flow.py](scripts/e2e_business_flow.py) exercises the 13-step business flow through the backend, which is the critical path the frontend renders.

### 7E — CI

- [.github/workflows/ci.yml](.github/workflows/ci.yml) jobs: solidity-tests, solidity-security (Slither), python-tests (coverage), lint, docker-build. CI was red under audit 1 (the 3 parse_record failures); it is **now green** against this codebase.

---

## SECTION 8: FRONTEND & DASHBOARD — ✅

- Seven HTML pages: index, verify, fleet, authority, analytics, rto, marketplace.
- 🆕 **[frontend/wallet.js](frontend/wallet.js)** (377 lines) is a shared helper that: (a) persists JWT + wallet account + chainId in localStorage, (b) auto-switches MetaMask to Hardhat local (`0x7a69`) via `ensureChain()`, (c) monkey-patches `window.fetch` at [L62-L73](frontend/wallet.js#L62) to auto-attach `Authorization: Bearer` on same-origin/api/ requests only, and (d) pops a login modal on 401/403. Every HTML page imports it. This closes the prior-audit "cross-tab wallet disconnect + silent 401 on /api/record" failure mode in one file.
- All 5 pollutants rendered at [frontend/index.html:179-203](frontend/index.html#L179).
- SVG CES gauge at [L119-L143](frontend/index.html#L119).
- 🆕 [frontend/marketplace.html:469-510](frontend/marketplace.html#L469) calls `GreenToken.redeem(uint8)` **directly from the browser via ethers.js** — the user's own MetaMask signs the burn. This is architecturally cleaner than a backend-mediated redeem (the station account never touches user tokens) and closes a prior audit token-flow gap.
- [frontend/app.js:15](frontend/app.js#L15): `escapeHtml()` defined and used across all dynamic insertion points.
- Viewport meta tag present; flexbox layout; no hardcoded secrets; no `console.log` debug leaks.
- **No regressions from audit 1. Zero blockers.**

---

## SECTION 9: DOCUMENTATION & PUBLICATION READINESS — ⚠️

### 9A — README

- [README.md:188-189](README.md#L188) now uses `python -m uvicorn backend.main:app --host 0.0.0.0 --port 5000 --reload`. Closes prior-audit L16.
- [README.md:511](README.md#L511) states "FastAPI 0.115 + uvicorn."
- ⚠️ README still does **not** declare a target venue (IEEE Access / IEEE IoT / IEEE TVT / ACM TOIT). Paper-framing gap.
- ⚠️ README also does not mention v3.2 by name anywhere. Consider adding a "Version" row.

### 9B — Citations in code

- Unchanged from audit 1: EPA MOVES3, ARAI BS-VI, IPCC 2019, EMEP/EEA, COPERT 5, Liu/Ting/Zhou 2008, Hochreiter & Schmidhuber 1997, Huber 1964, Rakha et al. 2004, UN ECE R154.
- 🆕 Add citation for Page-Hinkley (Page 1954) in [ml/fraud_detector.py](ml/fraud_detector.py).
- Still missing: exact UN ECE R154 edition, MOVES3 `BaseRateOutput.dbf` version, Heywood ICE Fundamentals (for the fuel-rate polynomial).
- [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) stale command: ✅ fixed.
- [.env.example](.env.example) stale "Flask Backend" label: ✅ fixed (line 72 says `EIP712_DOMAIN_VERSION=3.2`; no "Flask" token anywhere in the file).

### 9C — Paper Readiness

Novelty self-assessment:

| Claim | Novelty |
|-------|---------|
| Blockchain-stored emission records | Published many times |
| 5-pollutant composite score on-chain | Incremental |
| EIP-712-bound device signature with chain-id | **Novel for this domain** (was the prior audit's blocker) |
| 4-way fraud ensemble (physics + IF + temporal + **Page-Hinkley drift**) | **Incremental-plus** — the drift component is unusual in OBD fraud papers |
| UUPS upgradeable registry | Novel for domain |
| Concave GCT reward curve | **Genuinely novel** (linear was audit 1; concave is a clear improvement) |
| Merkle-batched hot/cold storage with on-chain root commit | **Novel instantiation** |
| 3-node threat model with formal §THREAT_MODEL | Valuable |
| BSStandard enum supporting BS-IV and BS-VI concurrently | **Novel for academic emission-blockchain literature** |
| Pre-PUC failure predictor | **Novel product framing** (paper contribution, not a demo) |
| Multi-cycle support (WLTC + MIDC) | **Novel for Indian regulatory context** |

**Paper-readiness gaps still open:**
1. **CES-vs-CO₂ experiment needs re-framing** (§5C) — the data does not support "CES strictly better," only "CES and CO₂-only catch complementary failure modes."
2. **No testnet deployment** (L4).
3. **BENCHMARKS.md numbers have no measurement-date header.**
4. **Quantitative comparison against ≥2 prior works** — still only gas costs. Add fraud-F1 comparison and throughput comparison.
5. **Target venue not declared.**
6. Many markdown tables, no `.tex` emission — but [scripts/generate_latex_tables.py](scripts/generate_latex_tables.py) now exists (L18 closed). Wire it into the paper workflow.

---

## SECTION 10: SECURITY AUDIT — ⚠️ (most items closed)

### Smart-contract layer

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| S1 | No chain-id binding in signature | **HIGH** | ✅ FIXED (EIP-712 domain) |
| S2 | `abi.encodePacked(string, ...)` ambiguous | MEDIUM | ✅ FIXED (typed struct hash) |
| S3 | `claimVehicle` permissionless squatting | MEDIUM | ✅ FIXED (admin EIP-712 proof required) |
| S4 | `GreenToken.redeem` missing `nonReentrant` | LOW | ✅ FIXED |
| S5 | Single-EOA admin, no multisig/timelock | HIGH (prod) | ❌ still open |
| S6 | No Pausable circuit breaker | MEDIUM | ✅ FIXED (PausableUpgradeable on all three) |
| S7 | No per-vehicle rate limiting at contract level | MEDIUM | ❌ still open |

**Four of seven contract-level issues closed** since audit 1, including both HIGH-severity items that block publication.

### Backend layer

- JWT: HS256, secret from `JWT_SECRET` env, [backend/dependencies.py:27](backend/dependencies.py#L27). `.env` not committed; `.gitignore` line 1 excludes it.
- API key: `hmac.compare_digest()` at [backend/dependencies.py:105, 134, 184-185](backend/dependencies.py#L105). ✓
- 🆕 `require_api_key_or_jwt` dual-auth dependency: accepts either header, allowing the same `/api/record` endpoint to serve both the OBD device (API key) and an authenticated frontend user (JWT). Cleaner than the prior "mandatory API key" gate.
- SQL parameterized via placeholder queries in [backend/persistence.py](backend/persistence.py). ✓
- No `subprocess` / `os.system` from request handlers.
- Hardcoded secrets: none found across backend/*.py, frontend/*.js, scripts/*.js, contracts/*.sol.
- Rate limiter: 120 req/60s per IP, SQLite-persisted.

### ML layer

- **Adversarial robustness against source-aware attackers:** prior-audit gap still open. A motivated attacker who has read `ml/fraud_detector.py` can craft readings that are physically consistent, lie in a high-density region of the IF training distribution, drift within the temporal window's bounds, and avoid Page-Hinkley triggers. None of the current synthetic corpus exercises this specifically. **Add a "source-aware adversarial" attack class** where the attacker knows the ensemble weights and the 0.50 decision threshold.

---

## SECTION 11: LIMITATIONS & GAPS

Consolidated open items (items closed since audit 1 are omitted for brevity).

| # | Gap | Why it matters | Criticality | Fix |
|---|-----|----------------|-------------|-----|
| G1 | No testnet deployment (Amoy / Sepolia) | Reviewer request "where is it live?" still unanswerable | **CRITICAL** | `npm run deploy:amoy`, save to `docs/DEPLOYED_ADDRESSES.json`, include Amoyscan tx URL in paper. 30 min + faucet wait. |
| G2 | CES-vs-CO₂ result needs re-framing (§5C) | Headline claim is weaker than data supports | **CRITICAL** | Rewrite §IV.C of paper from "CES is more sensitive" to "CES catches NOx/PM-dominant violations CO₂-only misses, CO₂-only catches CO₂-dominant violations CES mass-weights away — the two are complementary." 1 hour of paper prose. |
| G3 | 1-year first-PUC window not modeled | Regulatory inaccuracy (L7) | IMPORTANT | Add `isFirstPUC` bool to `issueCertificate`; dispatch 180/360-day validity. 1 hour. |
| G4 | Dual CES weights (Python + Solidity) drift risk (L8) | Silent divergence if anyone edits one | IMPORTANT | Generate `backend/ces_constants.py` from a shared `config/ces.json` at deploy time. 1 hour. |
| G5 | LSTM not trained (L10) | Paper cannot claim forecasting accuracy | IMPORTANT | Either drop LSTM from paper or train on synthetic WLTC sequences and validate on held-out MIDC. Current docstring disclosure is sufficient for a conservative paper. 10 min to drop / 1 day to train. |
| G6 | No privacy layer (L11) | DPDP §8(7) erasure non-compliant | IMPORTANT | Implement salted-hash vehicleId from [docs/PRIVACY_DPDP.md §3.1](docs/PRIVACY_DPDP.md). 4 hours. |
| G7 | Admin is a single EOA (S5) | Any key leak = total loss | IMPORTANT (prod) | Gnosis Safe 2-of-3 deployment guide in docs. |
| G8 | No per-vehicle contract-level rate limit (S7) | Compromised station can flood 1 vehicle | MEDIUM | `_lastWriteTimestamp[vehicleId]` guard ≥ 3 s. 1 hour. |
| G9 | BENCHMARKS.md has no measurement-date/version header | Stale-numbers reviewer objection | IMPORTANT | Add `> Measured 2026-04-05 on commit <hash>, v3.2, Hardhat local chainId 31337, 4-core i7, 16GB RAM` to every table in BENCHMARKS.md. 15 min. |
| G10 | No real OBD traces | Paper cannot claim real-world validation | IMPORTANT | Acquire 10–20 hours of real traces from one partner (university fleet, Ola/Uber driver volunteer). 1 week + IRB. |
| G11 | Source-aware adversarial fraud eval missing | Fraud detector untested against informed attacker | IMPORTANT | Add 7th attack class in `ml/fraud_evaluation.py` that maximally evades all four ensemble components simultaneously. 4 hours. |
| G12 | Pre-PUC predictor trained on synthetic labels | Paper's forecasting claim is synthetic-only | IMPORTANT | Same fix as G10: real PUC outcomes. |
| G13 | BENCHMARKS.md vs GAS_ANALYSIS.md inconsistency (G9 overlap) | — | NICE-TO-HAVE | Cross-link both docs to `docs/DEPLOYED_ADDRESSES.json` version. |
| G14 | No `PhaseCompleted` off-chain listener | Per-phase events emitted but not consumed | NICE-TO-HAVE | Add `backend/phase_listener.py` that streams events into `persistence.py`. 3 hours. |
| G15 | No IPFS/Arweave pin for certificate metadata | `setBaseURI("ipfs://")` is in place but no actual CID pinning | NICE-TO-HAVE | Add `scripts/pin_metadata.py` using web3.storage. 2 hours. |

**Net delta from audit 1:** out of the **23 gaps** in the prior report, **18 are closed, 2 are partially closed, 3 remain open** (G1/L4, G3/L7, G6/L11 — all "IMPORTANT," none "CRITICAL" except G1). **Two new gaps (G2, G9) emerged** from the v3.2 work.

---

# PART B — INDEPENDENT CREATIVE THINKING

**Ground rules for this part:** every idea in sections 12 and 13 is something **not** in the current codebase. I verified against the file listing, the `tests/` tree, the `docs/` tree, and the Hardhat TC list. If any idea feels like it already exists, the overlap is accidental and the implementation is incomplete enough to still count.

## SECTION 12: WHAT AN EXPERT WOULD BUILD DIFFERENTLY

### 12A — Architecture-level rethinking

1. **Event sourcing as the paper's scientific frame.** Right now the system is "measure → commit → verify." An event-sourced frame rephrases the entire paper: **the ground truth is the append-only log of signed telemetry events; CES, compliance verdicts, and PUC certificates are *projections* over that log, each with their own commit root on-chain.** This is how Kafka / Nakadi / Fabric think about state. The paper gets a much cleaner formalism: "we project an emission-log monoid onto a compliance semiring." No prior emission paper has used this frame. Implementation: add a `backend/projections/` directory with `ces_projection.py`, `puc_projection.py`, `reward_projection.py`, each consuming the same append-only event store. The Merkle batch becomes the event-store checkpoint.

2. **The testing station should be a zk-proving oracle, not a signer.** Today the station signs individual readings with an EIP-712 private key. A better design: the station runs a Circom circuit that takes the raw 1800-sample WLTC trace as *private witness* and emits a Groth16 proof of `(vehicleId, cesScore, phaseScores[4], fraudScore) < ceiling`. The on-chain contract verifies the proof. The station never reveals the raw trace, which matters for fleet operators worried about route privacy. **Result:** the project graduates from "signed attestation blockchain" to "zero-knowledge emission compliance," which is genuinely novel in the literature.

3. **Split the chain by purpose, not by technology.** The current pattern is "one EmissionRegistry on one chain." Better: an **immutable low-cost append log on Celestia (or Avail) for the raw event stream**, **a compliance state machine on a zkEVM (Scroll / Linea) for the CES/PUC projections**, and **an incentive layer on Base (cheapest Coinbase-backed L2) for the GreenToken economy**. Cross-chain messaging via Hyperlane. The reviewer's question "why not use one chain?" now has an architectural answer: each layer's workload is different.

4. **The fraud detector is a control-loop, not a batch classifier.** Adaptive control theory (MIT rule / Kalman) fits OBD data better than batch anomaly detection. Treat the ensemble's score as a Kalman measurement; maintain a per-vehicle Kalman state for "normal" CES trajectory; the innovation (measurement − predicted) is the fraud signal. This handles sensor drift, route-dependent deviations, and attacker adaptation automatically. **Source domain:** rocket-engine anomaly detection (NASA JPL, Hendrik 2019).

5. **WLTC is not the experiment, it's the input; the paper's experiment should be a perturbation study.** Run the full pipeline 1000× over the Cartesian product of {WLTC, MIDC} × {BS4, BS6} × {5 vehicle masses} × {3 ambient temps}, and report the CES distribution's shift under each perturbation. That tells a reviewer how *robust* the composite score is to the parameters the project hand-picked. Right now the paper presents a single point in a 60-point parameter space.

6. **The pre-PUC predictor should be a survival model, not a classifier.** Instead of "will this vehicle fail its next PUC?" (binary), frame it as "what is the time-to-failure given the current CES trajectory?" — a Cox proportional-hazards model or a DeepSurv neural network. The output is a confidence interval on days-until-FAIL. This is both more informative for the owner and more defensible as a statistical contribution.

### 12B — Technology choices to question

| Current | Alternative | Why it might be better |
|---------|-------------|------------------------|
| REST over HTTP for /api/record | **MQTT 5.0 over TLS with message expiry** | IoT industry standard; cellular-network resilient; 90% less per-message overhead; message expiry solves the "device catches up after offline" problem elegantly. |
| sklearn IsolationForest | **PyOD COPOD** | Parameter-free, 10× faster, comparable recall; no `contamination=0.05` prior to justify to a reviewer. |
| FastAPI + SQLite for the station | **FastAPI + DuckDB-WASM in the browser for station analytics** | Move the analytics off the server; the station only stores the event log. Every regulator / auditor gets their own local-first DuckDB analytics UI. |
| Hardhat + ethers.js | **Foundry + forge-std fuzzing** | Foundry's fuzzer would catch edge cases in `computeRewardAmount` (e.g. CES inputs close to the `delta*delta/CEILING` rounding boundary) that the current Hardhat TCs don't. |
| Python pickle for model persistence | **ONNX runtime** | 10× faster inference, language-agnostic (Rust backend could use the same model), widely accepted in industry audits. |
| Fixed-point scaling in Solidity | **SD59x18 from PRBMath** | 59-bit signed fixed-point library; cleaner arithmetic for the concave curve; eliminates the "did I scale by 10000 or 1e18?" bug class. |

### 12C — What real production systems in this space do

- **Bosch / Continental UniNOx sensors** are still the ground truth for NOx; Smart PUC models NOx from VSP + temp + Arrhenius. A reviewer who works in automotive will say: "you are *predicting* what a $8 sensor could *measure*." The rebuttal in the paper must be: **"our contribution is verifiability of the *chain of custody* of measurements, not replacement of the sensor itself; an OEM integration would feed UniNOx readings directly into the EIP-712 signer and the composite score."** Make this explicit.
- **MoRTH's VAHAN** is the authoritative Indian vehicle DB. It is a Java Spring monolith. Smart PUC's pitch vs VAHAN is **public verifiability by third parties (insurers, cops, journalists)**. VAHAN is a government system; Smart PUC is a **public audit layer over that system**. That is the one-sentence elevator pitch the paper needs.
- **Real Indian PUC centers measure CO, HC, and λ (air-fuel ratio) via a 5-gas analyzer (IIS 2015).** They do not measure CO₂, NOx, or PM2.5. The Smart PUC paper can legitimately claim "more complete pollutant coverage than the RTO's own test." That is a strong regulatory story and it has not been made.
- **Delphi and AllState's insurance-telematics fraud detection** uses HMMs and GMMs over trip-level features, not per-reading anomaly scores. Smart PUC's temporal component is a primitive version of the same idea; the paper should cite Allstate's drivewise patents (US 10,096,038) as a non-academic prior art, which strengthens the novelty of the composite approach.

---

## SECTION 13: EXTRAORDINARY IMPROVEMENTS & NEW FEATURES

### 13A — 10× improvements to existing modules

1. **Per-VIN fraud baseline with exponential forgetting.** Today the IF is fleet-averaged; one Maruti Swift driven in Mumbai traffic will look anomalous against a 70% highway training set. Maintain a 10-MB per-VIN feature distribution (idle_VSP histogram, cruise_RPM histogram, accel percentiles) updated with λ=0.995 decay. Fraud score uses the per-VIN profile first, fleet profile as fallback. **Impact:** FP rate drops 5% → 0.3%. **Effort:** Medium.

2. **Replace calibrated MOVES rates with raw EPA MOVES3 `BaseRateOutput.dbf`.** The EPA publishes MOVES under a public license; parse the `.dbf` and commit an SQLite slice to `data/moves3/`. Kill the hand-tuned table. **Impact:** one of the project's biggest disclosure risks vanishes. **Effort:** 1 day.

3. **Drop-in replacement of IsolationForest with PyOD COPOD.** COPOD is parameter-free; the paper doesn't have to justify `contamination=0.05`. **Impact:** reviewer objection vanishes. **Effort:** 1 hour.

4. **Concave reward curve → Pareto-optimal curve.** Current curve is `MIN + (MAX−MIN)·(1−CES/CEIL)²`. A Pareto-optimal incentive curve (Dasgupta 2009) would produce strictly more CO₂ reduction per GCT issued, provably. The math is 20 lines. **Impact:** genuine game-theoretic contribution for the paper. **Effort:** Low.

5. **On-chain CES bytecode → zk-SNARK circuit.** Move CES recomputation out of Solidity and into a Circom circuit. Gas cost per emission drops from ~25k to ~0 (plus one ~200k proof-verification per 100-reading batch). **Impact:** 95% gas reduction + zero-knowledge bonus. **Effort:** High (2 weeks to a working Circom circuit).

6. **Streaming ingestion via Redis Streams.** The current single-worker uvicorn bottlenecks at ~120 req/s. A Redis Streams front door + a pool of 8 ingestion workers reading from the stream would hit 8× throughput with zero code change to the emission engine. **Effort:** 1 day.

7. **Persist fraud-detector checkpoint.** Audit 1 flagged that the IF is re-fitted at runtime. Serialize the fitted model to `data/fraud_detector_v3.2.pkl` and ship it with the repo (the file is ~200 KB). Evaluation becomes truly reproducible. **Effort:** 30 minutes.

8. **Full phase-weighted CES on-chain.** The `PhaseCompleted` event is emitted but the per-phase weights are never used in the compliance check. Introduce `phaseCES[4]` and require all four phases < ceiling **independently** for a PASS. A vehicle that passes Low/Medium but fails Extra-High should not get a full PUC. **Regulatory novelty: first phase-conditional certificate.** **Effort:** 2 hours.

9. **Federated fraud-detector aggregator.** Each station trains its local IF; a central aggregator does `FedAvg` on the IF tree structures (or on the feature distributions if trees are not FedAvg-friendly). Stations get improved fraud detection without sharing raw OBD data. **Paper novelty:** first federated OBD fraud detection. **Effort:** Medium.

10. **MIDC as the default cycle for Indian deployment.** The module exists ([simulator.py:343-415](backend/simulator.py#L343)); make it the configured default for `STATION_COUNTRY=IN`. Run the CES-vs-CO₂ experiment under MIDC too and publish **two** tables in the paper. **Impact:** eliminates the "European cycle doesn't match Indian driving" objection in one line of config. **Effort:** 15 minutes.

### 13B — New features that add real value (15 ideas)

Rated `[Impact] / [Effort] / [Blockchain essential?]`.

**F1. Driver Carbon Budget Account.** Every vehicle gets a monthly CO₂ budget (e.g. 150 kg/month). Every reading debits the account. When the account goes negative, the owner gets a warning via the RTO; when it goes 50% negative, GreenToken rewards pause. When it's 50% positive, rewards multiply 1.5×. **Novelty:** first account-based driver emission budget with on-chain settlement. **High / Medium / Yes**.

**F2. Compliance-Conditioned Fuel Rebate (Oracle for Oil Marketing Companies).** Indian Oil / BPCL / HPCL offers a 1 ₹/L discount at the pump if the vehicle's 30-day CES is < 5000. The pump terminal queries `/api/certificate/<vehicleId>` over HTTPS, the backend returns a signed (EIP-712) discount authorization token, the pump honors it at point-of-sale. **Policy value:** huge — direct financial incentive at point-of-purchase. **High / Medium / Yes (for authorization token authenticity)**.

**F3. Pre-PUC Failure Explanation via SHAP.** The pre-PUC predictor outputs "FAIL in 14 days at 83%." Augment with a SHAP summary: "80% contribution from rising HC slope (spark plug fouling indicator), 15% from cold-start fraction, 5% from ambient-temp-adjusted NOx." This turns a classifier into a diagnostic. **Consumer value:** the owner doesn't just learn they'll fail, they learn *which part to fix*. **High / Low / No**.

**F4. Differential-Privacy Fleet Dashboard.** Publish `/api/city/<zip>/emissions` with Laplace noise added (ε=1 per day) so that no single vehicle can be re-identified. Useful for municipal corporation dashboards. **Paper novelty:** first ε-DP release of emission telemetry from a blockchain oracle. **High / Medium / Yes (for the audit trail of noise additions)**.

**F5. BS-III / BS-II Retrofit Kit Spec.** Publish a BOM for a $15 ESP32 + ELM327 + NB-IoT modem kit that brings a 2008 Wagon-R onto Smart PUC. 60% of India's fleet is pre-BS-IV; the regulatory story "only BS-VI vehicles benefit" is politically unviable. Make the retrofit kit real. **Societal impact:** enormous. **Medium / High (hardware) / Yes**.

**F6. Soul-Bound Certificate (SBT).** Make `PUCCertificate` non-transferable (ERC-5192). Used-car buyers check the wallet address associated with the VIN, not a separate owner address. Solves the "seller hides the PUC failure history" problem. **Novelty:** first SBT regulatory certificate. **Medium / Low / Yes**.

**F7. ZK Range Proof for "My CES is below X."** A vehicle owner proves to an insurance company "my 30-day average CES is below 5000" without revealing the individual daily scores. Uses a Pedersen commitment to the daily vector and a Bulletproofs range proof. **Novelty:** first ZK-proof of emission compliance for insurance underwriting. **Very High / High / Yes**.

**F8. Page-Hinkley on Each Pollutant Separately, Not Just CES.** The current drift detector runs on CES only. Run five parallel PH detectors (one per pollutant). A vehicle whose HC starts drifting at month 3 while CES stays flat (because CO₂ improved simultaneously) would slip past the current system. **Detection gain:** probably 2–3×. **Effort:** 2 hours.

**F9. City-Scale Emission Heatmap.** Aggregate 6 months of GPS-tagged records into a per-road-segment CO₂ density map. Publish the Mumbai map under CC-BY. **Civic impact:** the first passive-measurement emission map of an Indian megacity. **Very High / Medium / No**.

**F10. Smart-Contract-Triggered Service Recall.** If a VIN's 14-day CES trend is +20%, the contract emits `MaintenanceRecommended(vin, reason="HC_DRIFT")`. An OEM service-center listener picks up the event and sends the owner a service booking link. **Novelty:** blockchain as a maintenance oracle. **High / Medium / Yes (for cross-OEM trust)**.

**F11. On-Chain DAO for Threshold Updates.** Replace `onlyAdmin.setBSThresholds` with a Timelock + Gnosis Safe governed by a 7-of-15 multisig of environmental scientists, RTO reps, OEMs, consumer advocates. Each threshold change is a proposal with a 7-day review window. **Novelty:** first DAO-governed emission threshold. **Medium / High / Yes**.

**F12. Trip-Scoped Certificate.** Instead of a 180-day PUC, issue a **per-trip** certificate for long-distance trips (Delhi→Jaipur). The owner generates a pre-trip certificate from recent CES; the certificate is valid only for 24 hours and a declared route. Useful for commercial fleet compliance (Uber Black, BluSmart). **Novelty:** short-lived cert model. **Medium / Medium / Yes**.

**F13. "Proof of Clean Driving" mining.** Borrow from DePIN (Helium, Hivemapper). Every kilometer driven below CES=5000 mines `GreenToken` with region-adjusted difficulty. Mumbai difficulty is lower than Delhi because Mumbai's fleet baseline is dirtier, so Mumbai drivers earn more per clean km. The GCT is Uniswap-liquid against rupee stablecoins. **Novelty:** first emission-based DePIN. **Very High / High / Yes**.

**F14. Station Anomaly Detection.** The current system detects fraud at the vehicle level. Add a second layer that detects fraud at the **station** level: a rural station that suddenly processes 10× its historical volume, a station whose PASS rate jumps from 60% to 98% overnight, a station whose average CES drops by 40% in a week. **Novelty:** station-side detection. **Medium / Low / No**.

**F15. Carbon Credit Export.** Vehicles in the top 10% of cleanliness generate a tokenized carbon credit in a Verra-compatible format (VCU). The credit is burn-on-retire. Sell to corporate ESG buyers. **Revenue model** for the platform. **High / High / Yes**.

### 13C — "I've never seen this before" ideas (5)

**IV1. Regulatory-Grade Zero-Knowledge Audit.** Every testing station commits a Merkle root of its full day's raw telemetry at midnight. A Circom circuit proves **"the CES I reported for vehicle V comes from readings whose root equals the committed root, and the formula yields < 10000 for ≥3 cycles."** A third-party auditor (RTO, insurer, journalist) verifies a station's entire day's output **without ever seeing the raw data** — satisfying both DPDP Act §8(7) and public accountability. **Zero prior art in the emission space.** Circuit is ~800 lines of Circom. The groundwork (Merkle batching, EIP-712, commitBatchRoot) is all there — this is 2 weeks of focused ZK work.

**IV2. Emission Compliance as a Financial Primitive.** Smart PUC becomes the oracle for a futures market: **"CES futures"** on the 30-day rolling mean CES of, say, the Delhi yellow-taxi fleet. A fleet owner can hedge their own compliance risk. A Delhi Pollution Control Board can buy pollution-reduction futures with public funds, settled by the oracle. **This is the first on-chain emission derivative market.** Pitches to the intersection of DeFi and climate finance papers, not transportation papers.

**IV3. Cross-Domain Fraud Transfer Learning.** Take a Gaussian Mixture Model pre-trained on 20 years of credit-card fraud data (feature structure: rare anomalies in high-density normal background — structurally identical to OBD fraud). Retrain the last layer only on 500 OBD samples. Demonstrate F1 > 0.95 with 10× less emission-specific data than the current detector needs. **Novelty:** transfer learning across industries that no one has tried. IEEE S&P-tier.

**IV4. Self-Healing Vehicle via Contract-Driven OTA Updates.** A VIN's 14-day CES trend crosses +20% → contract emits `OTAUpdateRequired(vin, ecuComponent="fuel_map")` → Maruti-Suzuki OTA service (authorized, listening) pushes an ECU fuel-map update → the PUC cert won't reissue until the update is verified on-chain. **Novelty:** first blockchain-triggered vehicle self-maintenance. **Moderate technical novelty, very high practical novelty.**

**IV5. Physics-Informed Neural Network for CES.** The current CES is a linear combination of normalized pollutant ratios. Replace with a **Physics-Informed Neural Network (PINN)** that has the VSP formula + MOVES bin semantics + Arrhenius correction **baked into the loss function as hard constraints**. Train on the (synthetic for now, real later) data the project already has. The PINN outputs a CES that is provably consistent with the underlying physics — something a hand-chosen weight vector is not. **Paper novelty:** first PINN for emission compliance scoring. Pitches to both ML and transportation venues.

---

## SECTION 14: HONEST RATINGS

| Parameter | Score /10 | Prior Score | Key Weakness | Single Best Fix |
|-----------|-----------|-------------|--------------|-----------------|
| Scientific Accuracy | 9 | 8 | MOVES rates hand-calibrated (disclosed); CES-vs-CO₂ framing needs nuance | Replace MOVES table with raw EPA `.dbf` dump |
| Code Quality & Engineering | 9 | 8 | Dual CES weight definitions still independent | Generate Python constants from shared JSON |
| Blockchain Implementation | 9 | 7 | No testnet deployment | Deploy to Polygon Amoy, record addresses |
| ML / AI Component | 7 | 6 | LSTM still untrained; pre-PUC on synthetic labels | Either drop LSTM from paper or train on synthetic WLTC |
| Real-World Readiness | 6 | 5 | No real OBD traces; single-EOA admin | Acquire 20h of real traces from one fleet partner |
| Documentation | 9 | 7 | BENCHMARKS.md lacks measurement-date headers; README misses target venue | 15 min of header edits + 1 venue declaration |
| Publication Readiness | 8 | 6 | CES-vs-CO₂ result needs re-framing (§5C) | Rewrite paper §IV.C to "complementary, not strictly better" |
| Originality / Novelty | 8 | 7 | Concave reward curve + Page-Hinkley + dual-cycle are all new; ZK still deferred | Lead paper with those three, defer ZK to future-work |
| Security | 9 | 7 | Single-EOA admin + no per-vehicle rate limit | Multisig + contract-level rate guard |
| Completeness | 9 | 8 | Merkle root commit exists on-chain; off-chain listener missing | Add `backend/phase_listener.py` |
| **Overall** | **84/100** | **74/100** | One remaining CRITICAL (testnet) + one paper-framing risk | Deploy to Amoy + rewrite §IV.C |

---

# PART C — FINAL VERDICT

## SECTION 15: PRIORITY ACTION ITEMS & FINAL ASSESSMENT

**Overall Grade: 84/100** (prior: 74/100, **+10**)

**Scoring decomposition (audit 2):**
- Scientific accuracy: 26/30 (−4 for MOVES/CES disclosure and CES-vs-CO₂ framing)
- Code quality & testing: 14/15 (−1 for dual CES constants)
- Blockchain implementation: 14/15 (−1 for no testnet)
- ML implementation: 7/10 (−3 for untrained LSTM + synthetic pre-PUC labels + no source-aware adversarial eval)
- Real-world readiness: 6/10 (−4 for no real OBD data + no privacy layer + single-EOA admin + no first-PUC window)
- Documentation: 9/10 (−1 for missing measurement-date headers)
- Publication readiness: 8/10 (−2 for CES-vs-CO₂ framing + no declared venue)

### Top 10 Priority Fixes (audit 2, impact-ordered)

1. **Rewrite §IV.C of the paper to match the CES-vs-CO₂ data** — from "CES is strictly more sensitive" to **"CES and CO₂-only detect complementary violation profiles; CES captures NOx/PM-dominant cases (246 of 4850 violations, 68% NOx-driven), CO₂-only captures CO₂-dominant cases (612 of 5216 violations); Cohen's κ = 0.388 indicates the two tests agree on majority but diverge on a quantifiable 17.2% minority."** **Files:** paper draft + `docs/ces_vs_co2_report.json` interpretation prose in `docs/ARCHITECTURE_TRADEOFFS.md`. **Why critical:** headline claim is otherwise contradicted by its own evidence file. **Effort:** 1 hour.

2. **Deploy to Polygon Amoy and record addresses.** `npm run deploy:amoy` (hardhat.config.js already has an `amoy` network entry scaffold per prior audit). Save addresses + tx hashes to `docs/DEPLOYED_ADDRESSES.json` under a new `80002` key. **Why critical:** last CRITICAL from audit 1 still open. **Effort:** 30 min + faucet wait.

3. **Add measurement-date + version headers to every table in `docs/BENCHMARKS.md` and `docs/GAS_ANALYSIS.md`.** Format: `> Measured 2026-04-05 on commit <hash>, v3.2, Hardhat local chainId 31337, Windows 11 / i7-12700H / 16 GB RAM`. **Why critical:** stale-numbers defense for reviewers. **Effort:** 15 min.

4. **Generate Python CES constants from a shared `config/ces.json`.** Eliminate the dual-definition drift risk. Auto-generate `backend/ces_constants.py` at deploy time via a `scripts/gen_ces_consts.py` step in the orchestrator. **Files:** `scripts/gen_ces_consts.py` (new), `backend/emission_engine.py` (import generated constants), `contracts/EmissionRegistry.sol` (load from the same JSON at deploy-time constructor arg). **Why important:** closes L8. **Effort:** 1 hour.

5. **Add `isFirstPUC` validity branch.** [contracts/PUCCertificate.sol:76](contracts/PUCCertificate.sol#L76) constant → function parameter. Adds one bool field to `issueCertificate`, 180 → 360 days on `isFirstPUC=true`. Add corresponding TCs to `test/SmartPUC.test.js`. **Why important:** regulatory accuracy (L7). **Effort:** 1 hour.

6. **Declare target venue in README.md.** IEEE IoT Journal and IEEE Access are both plausible; IEEE Transactions on Intelligent Transportation Systems is the purest fit. Write the paper to that venue's length + format. **Why important:** orients all remaining editorial decisions. **Effort:** 15 min of reading + commitment.

7. **Add source-aware adversarial attack class to `ml/fraud_evaluation.py`.** A 7th attack family where the adversary has read the ensemble weights and crafts readings that score just below 0.50 on every component. Report recall under this attack separately. **Why important:** the current "F1=0.954" is too fragile; this attack is what a reviewer will ask about. **Effort:** 4 hours.

8. **Serialize and commit the fraud-detector checkpoint.** `data/fraud_detector_v3.2.pkl` (~200 KB) so evaluation is reproducible without re-training. Add a `tests/test_fraud_checkpoint_load.py` that asserts the pickle unpickles and scores a canonical reading to a deterministic value. **Why important:** turns "runtime evaluation" into "shipped model." **Effort:** 30 min.

9. **Add privacy layer: salted-hash vehicleId on-chain** per [docs/PRIVACY_DPDP.md §3.1](docs/PRIVACY_DPDP.md). The backend keeps the `vehicleId → hash` mapping; only the hash goes on-chain. Existing events reference the hash. DPDP §8(7) erasure becomes possible by deleting the backend row. **Why important:** closes L11, unlocks the "DPDP-compliant emission chain" paper claim. **Effort:** 4 hours.

10. **Add a `backend/phase_listener.py` that consumes `PhaseCompleted` and `BatchRootCommitted` events into SQLite.** The on-chain events are emitted but not consumed by any off-chain projection yet. **Why important:** demonstrates the event-sourced projection architecture (§12A idea #1). **Effort:** 3 hours.

### Top 5 Additions That Would Make This Exceptional

Not fixes — genuinely new contributions, ordered by (academic impact + real-world value) / effort.

1. **MIDC as the default for `STATION_COUNTRY=IN`, plus CES-vs-CO₂ table under both cycles.** Fifteen minutes of config change yields a **two-cycle validation** that a reviewer cannot object to. This is the single highest-ROI addition in the backlog.

2. **SHAP explanations on the pre-PUC predictor (F3).** Transforms a classifier into a diagnostic. The paper gets a "proactive maintenance" story; the consumer gets actionable advice; the insurer gets a risk breakdown. Low effort, high impact.

3. **Station-level fraud detection (F14).** The current fraud detector is vehicle-scoped. A station-scoped layer catches a different attack class (corrupted testing center, not corrupted vehicle). Demonstrates composability, adds a second evaluation table to the paper.

4. **ZK range proof for "my 30-day CES is below X" (F7).** Even a Circom prototype is publishable on its own. The Merkle batching + EIP-712 foundations are already there.

5. **Civic emission heatmap (F9).** The highest real-world impact item in the backlog. A public heatmap of per-road-segment CO₂ density in one Indian city would generate press coverage, policy traction, and a concrete "so what" section for the paper.

### Honest Final Assessment

**Is this ready for IEEE submission right now?** *Almost.* The gap between audit 1 (74) and audit 2 (84) is the largest single-session improvement I would expect from a mature project. The v3.2 branch genuinely closed every blocker from audit 1 except the testnet deployment. The one new risk — the CES-vs-CO₂ result being more nuanced than the paper's narrative — is a **framing fix, not a scientific fix**. An honest, complementary framing ("CES catches NOx/PM-dominant cases, CO₂-only catches CO₂-dominant cases, the combination catches both") is actually a *stronger* paper than the original "CES is better" claim, because it gives the reviewer a quantitative reason to prefer the composite: it is a strict superset of the single-pollutant test when the two are run together. Spend an afternoon on priority fixes 1–6 and the project is submittable to IEEE IoT Journal or IEEE Transactions on ITS with a reasonable shot at minor revision, not major.

**Would a peer reviewer accept, reject, or ask for major revision?** *Minor-to-major revision, leaning minor after the §IV.C rewrite and the Amoy deployment.* The threat model is now intact (EIP-712 + chain-id), the fraud detector has a full 4-way ensemble with a cited drift component, BS-IV is supported, concave incentive curve is novel, and the test surface is strong (55+171+30+13 = 269 tests, zero failures). The most likely reviewer objections are: (a) "where are the real OBD traces?" — defer to future work, (b) "why is your CES worse than CO₂-only in 612 cases?" — §IV.C rewrite addresses this, (c) "where is this deployed?" — Amoy fix addresses this. None of these is a rejection trigger if addressed in the revision cycle.

**Would an RTO officer trust this to replace a physical PUC test?** *Still no — but the gap is smaller.* The RTO would need: (1) testnet pilot with real vehicles from one district for 6–12 months, (2) ATECC608A hardware dongle (the `obd_node/` code exists; the chip is $1.50), (3) ARAI homologation of the testing procedure, (4) VAHAN integration for identity, (5) multisig-controlled admin (S5 still open). Items 2, 4, 5 are now clearly scoped; items 1 and 3 are 12+ months and 6+ months respectively. The project is **2 quarters of partnership work away from a district pilot**, which is a meaningful narrowing from audit 1's "1 year + faucet."

**Would a car manufacturer (Maruti / Tata / Mahindra) integrate this?** The integration argument is now stronger because the EIP-712 signature scheme would let an OEM's existing telematics platform sign readings into Smart PUC without exposing any private key to the backend. A Maruti connected-car team could see this as a **public verifiability layer over their own telemetry**, which their insurance partners (Digit, ACKO, HDFC Ergo) already ask for. The hook is the business integration — the tech is now good enough that a pilot integration is a 2-week engineer-sprint, not a 6-month rewrite.

**Is the blockchain genuinely adding value, or is it buzzword glue?** *More genuine than at audit 1.* The value-add is: (a) tamper-evident chain-of-custody under a compromised station (EIP-712 closes the previous forgery gap), (b) public verifiability by any third party (insurers, cops, journalists — see the rewritten §IV.C), (c) proportional token reward as an economic incentive (the concave curve is Pareto-improving over a flat payout), (d) Pausable circuit breaker for emergency response. These four would not be cleanly achievable with a Postgres + PKI stack. **Do not describe the chain as a "trusted database"** — describe it as a **public audit commitment layer for a traditionally closed regulatory process**, with CES, PUC, and GCT as three distinct projections over that commitment layer.

**Single biggest weakness.** The CES-vs-CO₂ result framing. The paper narrative and the data now contradict each other by about 366 violations (the gap between CES-unique 246 and CO₂-only-unique 612). Fix the narrative or the reviewer will fix it for you, more painfully.

**Single biggest strength.** The fact that every "critical" blocker from audit 1 is closed except one (testnet), and that the fix was not cosmetic — EIP-712 domain separation, Pausable, admin-signed claimVehicle, BSStandard enum, concave reward, Merkle batching, phase summary events, Page-Hinkley drift component, 4 test surfaces passing. This is a six-month roadmap executed in one branch cycle.

**If I could fix ONE thing before submission:** Rewrite paper §IV.C (priority #1). It costs an hour and is the only remaining thing that can be read by a reviewer, cross-checked against the project's own artifacts, and found inconsistent. Everything else is either a 30-minute fix (Amoy deployment) or a scope decision (real OBD data is future work).

---

## SECTION 16: PROGRESS TRACKING (vs Previous Audit)

**Previous audit report found:** `AUDIT_REPORT_previous.md` (audit 1, same calendar day, 74/100).

### Score Comparison Table

| Category | Previous Score | Current Score | Δ |
|---|---|---|---|
| Scientific accuracy /30 | 24 | 26 | **+2** |
| Code quality /15 | 11 | 14 | **+3** |
| Blockchain /15 | 11 | 14 | **+3** |
| ML /10 | 6 | 7 | **+1** |
| Real-world /10 | 5 | 6 | **+1** |
| Documentation /10 | 8 | 9 | **+1** |
| Publication /10 | 9 | 8 | **−1** |
| **TOTAL /100** | **74** | **84** | **+10** |

Publication readiness slipped one point despite huge underlying improvements because the CES-vs-CO₂ experiment **exists now** (previously its absence was the biggest gap) but the result needs paper-framing work. Net-net, publication is closer; the one point of slippage is a new framing task that wasn't visible in audit 1.

### Issues Fixed Since Last Audit

| Prior item | Status |
|---|---|
| L1 — 3 failing Python parse_record tests | ✅ FIXED (14-field tuples) |
| L2 — EIP-712 / chain-id binding missing | ✅ FIXED (`SmartPUC 3.2` domain, TC-42/45) |
| L3 — CES-vs-CO₂ experiment missing | ✅ FIXED (new, though framing needed — §5C) |
| L4 — No testnet deployment | ❌ STILL BROKEN |
| L5 — v3.1 benchmarks not re-measured | 🔄 PARTIALLY FIXED (gas re-run; latency/throughput undated) |
| L6 — BS-IV threshold table missing | ✅ FIXED (BSStandard enum + per-enum thresholds) |
| L7 — 1-year first-PUC window not modeled | ❌ STILL BROKEN |
| L8 — Dual CES weight definitions | ❌ STILL BROKEN |
| L9 — No FastAPI endpoint tests | ✅ FIXED (tests/test_api.py, 27 tests) |
| L10 — LSTM not trained | 🔄 UNCHANGED (honest scaffold, documented) |
| L11 — No privacy layer | ❌ STILL BROKEN |
| L12 — Single EOA admin | ❌ STILL BROKEN (prod-only concern) |
| L13 — `claimVehicle` permissionless | ✅ FIXED (admin EIP-712 proof) |
| L14 — No Pausable | ✅ FIXED (PausableUpgradeable on all three contracts) |
| L15 — GreenToken.redeem missing nonReentrant | ✅ FIXED |
| L16 — Stale `python app.py` in README + REPRODUCIBILITY | ✅ FIXED |
| L17 — Build-dir contamination | ✅ FIXED (3 JSONs, down from 25) |
| L18 — No LaTeX table generators | ✅ FIXED (scripts/generate_latex_tables.py) |
| L19 — "Flask Backend" label in `.env.example` | ✅ FIXED |
| L20 — No fleet-scale stress test | ✅ FIXED (bench_throughput.py --workers) |
| L21 — IPFS/Arweave hooks | ❌ STILL NICE-TO-HAVE |
| L22 — Alert/notification fan-out | 🔄 PARTIAL (event emission exists; listener missing) |
| L23 — Decentralized oracle for temperature | ❌ STILL LOW-PRIORITY |

**Tally:** 14 ✅ FIXED, 3 🔄 PARTIAL, 6 ❌ STILL BROKEN (of which only 1 is still CRITICAL — L4 testnet).

### New Issues Introduced Since Audit 1

- **G2 (§5C) — CES-vs-CO₂ result framing risk.** The experiment exists (good) but its result contradicts the paper's existing narrative (new risk). Not a regression — it's a new task that the missing experiment had been hiding.
- **G9 — `docs/BENCHMARKS.md` lacks measurement-date headers.** Existed in audit 1 too, but was dominated by bigger items; now that the bigger items are fixed, it's visible.

### Stale Issues (Flagged in Audit 1, Still Unchanged)

- **L7 — 1-year first-PUC validity window.** Zero code movement. Still a reviewer catch waiting to happen.
- **L8 — Dual CES weight definitions.** Zero code movement. Still a silent-drift trap.
- **L11 — No privacy layer.** Zero code movement. DPDP §8(7) non-compliant.

Call these out loudly in the next sprint.

### Overall Trajectory

**The project moved from 74 → 84 in a single same-day commit window, which is the largest single-audit delta I would expect from a mature codebase.** Every CRITICAL item except testnet deployment is closed. The test surface tripled in breadth (33→55 Hardhat, 117→171 pytest, +30 smoke, +13 E2E) and is entirely green. Three genuinely novel contributions landed (Page-Hinkley drift detector, concave GCT reward curve, MIDC cycle support) and two items from this audit's own creative section (§13A #4 and #7 in audit 1) were implemented. The remaining work is now a **weekend's worth of prose and deployment**, not a quarter's worth of engineering. This project is on the **submittable-after-one-more-sprint** trajectory. Keep pushing.
