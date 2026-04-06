# SmartPUC — Audit Report
Audit Date    : 2026-04-06 01:30 IST
Audit Run     : 2nd (previous report renamed to `AUDIT_REPORT_previous.md`)
Overall Score : 85/100
Previous Score: 78/100
Verdict       : NEEDS POLISH

---

## SECTION 1: PROJECT STRUCTURE & CODE HEALTH

**Verdict: ✅**

**Directory structure**: clean and logical. `backend/`, `physics/`, `ml/`, `integrations/`, `hardware/`, `obd_node/`, `contracts/`, `frontend/`, `docs/`, `benchmarks/`, `scripts/`, `tests/` (Python), `test/` (Hardhat). Two test roots is intentional (Python vs JS) and documented.

**Module docstrings**: present on every core Python file sampled: `physics/vsp_model.py:1-17`, `backend/emission_engine.py:1-70`, `backend/simulator.py:1-23`, `ml/fraud_detector.py:1-25`, `ml/lstm_predictor.py:1-65`, `backend/main.py:1-20`, `backend/blockchain_connector.py:1-17`, `backend/merkle_batch.py:1-42`, `backend/persistence.py`, `backend/privacy.py`, `integrations/obd_adapter.py:1-20`, `integrations/vaahan_bridge.py`, `hardware/atecc608a_interface.py`, `ml/pre_puc_predictor.py:1-51`, `ml/station_fraud_detector.py:1-59`, `ml/redteam.py:1-28`.

**Type hints**: complete with `from __future__ import annotations` across all core modules. Full function signatures with `Dict`, `Optional`, `List`, return types.

**Anti-patterns**: no bare `except:` in core modules. Intentional `except Exception: # noqa: BLE001` found in `backend/main.py` (~15 instances) for graceful degradation of optional ML/blockchain subsystems — acceptable and documented.

**Import order**: correct everywhere (stdlib -> third-party -> local). `sys.path` manipulation in `backend/main.py:52-70` annotated with `# noqa: E402`.

**`requirements.txt`**: all 15 deps pinned exactly (`web3==6.15.1`, `numpy==1.26.4`, `scikit-learn==1.4.2`, `fastapi==0.115.0`, etc.). TensorFlow intentionally commented out (consistent with LSTM scaffold disclosure).

**`package.json`**: `@openzeppelin/contracts@^4.9.6`, `hardhat@^2.22.15`, `ethers@^6.13.4`. Peer versions align. `@openzeppelin/hardhat-upgrades@^3.2.1` for UUPS.

**Entry points**: `npm run start` (Docker), `run_project.bat` (Windows), `docker-compose up --build`, `scripts/reproduce.sh`.

**TODO/FIXME/HACK**: none found in production code.

**Naming conventions**: Python uses `snake_case` consistently; Solidity uses `camelCase` consistently.

**Hardcoded paths**: none. All paths are relative or from environment variables.

**Remaining warts**:
- ⚠️ `backend/main.py` is 1,620+ lines — monolith that should be split into FastAPI routers. Not broken, but brittle.
- ⚠️ `backend/venv/` is in the working tree (hundreds of MB). It is in `.gitignore` and not tracked, but its presence bloats IDE indexing.

---

## SECTION 2: EXISTING FEATURE COMPLETENESS

**Verdict: ✅**

### 2A — Core Pipeline
- ✅ Full pipeline runs without manual intervention: OBD telemetry → VSP → emissions → fraud detection → blockchain storage → certificate issuance.
- ✅ Single command: `npm run start` (Docker) or `start.bat` (Windows).
- ✅ Each stage passes output to the next via `backend/main.py` `/api/record` endpoint ([backend/main.py:480-780](backend/main.py#L480-L780)).
- ✅ Simulator fallback when no real OBD data: [backend/main.py:605](backend/main.py#L605).

### 2B — Multi-Pollutant Engine
- ✅ All 5 BSVI pollutants calculated and returned: CO2, CO, NOx, HC, PM2.5 ([backend/emission_engine.py:517-520](backend/emission_engine.py#L517-L520)).
- ✅ CES calculated from all 5 pollutants with validated weights summing to 1.0 ([backend/ces_constants.py:31-32](backend/ces_constants.py#L31-L32)).
- ✅ Cold-start penalty applied in pipeline (`cold_start` flag from OBD adapter coolant temp check, [integrations/obd_adapter.py:46-81](integrations/obd_adapter.py#L46-L81)).
- ✅ MOVES operating-mode bins used in emission calculation ([backend/emission_engine.py:244-380](backend/emission_engine.py#L244-L380)).
- ✅ Arrhenius NOx temperature correction connected to `ambient_temp` input ([backend/emission_engine.py:414-423](backend/emission_engine.py#L414-L423)).
- ✅ Per-pollutant compliance flags generated and surfaced in API response.

### 2C — Blockchain Integration
- ✅ All 5 pollutants + CES + fraud score + VSP + phase sent to Solidity ([backend/blockchain_connector.py:383-420](backend/blockchain_connector.py#L383-L420)).
- ✅ PUC NFT minted after passing test — wired end-to-end ([backend/main.py:835-870](backend/main.py#L835-L870)).
- ✅ Phase listener catches on-chain events ([backend/phase_listener.py:188-255](backend/phase_listener.py#L188-L255)).
- ✅ Fraud score written to blockchain as `fraudScore` field in `EmissionRecord` struct.
- ✅ Round-trip test exists: [tests/test_integration.py](tests/test_integration.py).

### 2D — ML Modules
- ✅ Isolation Forest trained and fitted — checkpoint at `data/fraud_detector_v3.2.pkl` (1.3 MB).
- ⚠️ LSTM model produces predictions via `MockPredictor` (linear extrapolation). No trained TF weights exist. Explicitly disclosed as scaffold ([ml/lstm_predictor.py:1-31](ml/lstm_predictor.py#L1-L31)).
- ✅ Fraud detector output consumed by main pipeline ([backend/main.py:229-230](backend/main.py#L229-L230)).
- ✅ LSTM output displayed in dashboard via Chart.js ([frontend/app.js:74, 295](frontend/app.js#L74)).
- ✅ Working inference path: OBD → fraud score → compliance decision.
- ⚠️ Pre-PUC predictor (`ml/pre_puc_predictor.py`) is functional and tested but NOT called from the main pipeline — standalone feature.

### 2E — Dashboard
- ✅ Dashboard fetches from real backend `/api/record` ([frontend/app.js:280](frontend/app.js#L280)).
- ✅ All 5 pollutant gauges present with BSVI threshold lines ([frontend/index.html:180-204](frontend/index.html#L180-L204)).
- ✅ Real-time polling via `setInterval(runSimulationStep, 3000)` ([frontend/app.js:247-250](frontend/app.js#L247-L250)).
- ✅ Fraud alert banner triggered by actual fraud scores ([frontend/index.html:65-75](frontend/index.html#L65-L75)).
- ✅ WLTC phase indicator present.
- ✅ CES gauge with SVG arc ([frontend/index.html:120-145](frontend/index.html#L120-L145)).
- ✅ LSTM prediction graph via Chart.js ([frontend/app.js:74](frontend/app.js#L74)).
- ✅ NFT certificate viewer with `isValid()`, `getCertificate()`, `tokenURI()` ([frontend/app.js:46-54](frontend/app.js#L46-L54)).
- ⚠️ No WebSocket/SSE — polling only. Acceptable for research prototype.

### 2F — API / Interface
- ✅ Full FastAPI with OpenAPI/Swagger at `/docs`.
- ✅ All endpoints implemented with Pydantic validation: speed 0-250, RPM 0-8000, fuel 0-50, accel ±10 ([backend/schemas.py:51-66](backend/schemas.py#L51-L66)).
- ✅ JWT + API-key dual auth on protected endpoints ([backend/dependencies.py:92-172](backend/dependencies.py#L92-L172)).
- ✅ Rate limiting: 120 req/60s per IP, SQLite-backed ([backend/dependencies.py:37-87](backend/dependencies.py#L37-L87)).

---

## SECTION 3: SCIENTIFIC ACCURACY & MATHEMATICAL RIGOR

**Verdict: ✅ (with disclosed caveats)**

### 3A — VSP Model ✅
- Formula at [physics/vsp_model.py:139-142](physics/vsp_model.py#L139-L142) is an **exact match** to EPA MOVES3: `VSP = v[a + g·sinθ + μ·g·cosθ] + ρCdA/(2m)·v³`.
- Units: input `speed_mps` (m/s), output W/kg. ✅
- Vehicle params ([physics/vsp_model.py:56-61](physics/vsp_model.py#L56-L61)): mass=1000 kg, Cd=0.32, A=2.1 m², μ=0.015, ρ=1.225 kg/m³. **All realistic for Maruti Swift / Hyundai i10.**
- MOVES bins ([physics/vsp_model.py:175-214](physics/vsp_model.py#L175-L214)): exact EPA thresholds `<-2, <0, <3, <6, <9, <12, <18, <24, <30, ≥30`. ✅
- Test values verified: idle→~0 ✅, cruise 60 km/h→~4.3 W/kg ✅, hard accel→~35 W/kg ✅, decel→negative ✅.

### 3B — Multi-Pollutant Engine ✅
- All 5 pollutants calculated: CO2, CO, NOx, HC, PM2.5 ([backend/emission_engine.py:517-520](backend/emission_engine.py#L517-L520)). None stubbed.
- BSVI thresholds exact per ARAI 2020: CO2≤120, CO≤1.0, NOx≤0.06, HC≤0.10, PM2.5≤0.0045 g/km ([backend/ces_constants.py:40-46](backend/ces_constants.py#L40-L46)). ✅
- IPCC CO2: petrol=2310 g/L, diesel=2680 g/L ([backend/emission_engine.py:200-203](backend/emission_engine.py#L200-L203)). Correct.
- NOx Arrhenius: Ea/R=3500K, Tref=298.15K, `exp(3500 × (1/298.15 − 1/T_amb))` ([backend/emission_engine.py:305-306, 414-423](backend/emission_engine.py#L305-L306)). Higher temp → more NOx. ✅
- g/s→g/km: divides pre-scaled calibration constants by speed_mps. Mathematically correct but uses a non-obvious convention — **disclosed at [backend/emission_engine.py:209-217](backend/emission_engine.py#L209-L217)**.
- Cold-start: CO×1.80, HC×1.50 ([backend/emission_engine.py:440-442](backend/emission_engine.py#L440-L442)). COPERT 5 standard. Boolean flag, no duration model. ✅
- CES weights: 0.35/0.30/0.15/0.12/0.08 = **1.00**, validated at import time with 1e-9 tolerance ([backend/ces_constants.py:31-32](backend/ces_constants.py#L31-L32)). ✅
- Edge cases: speed=0 capped at `IDLE_CO2_CAP=300 g/km` ([backend/emission_engine.py:445-456](backend/emission_engine.py#L445-L456)). ✅

**Disclosed caveats (not bugs — honest limitations):**
- ⚠️ MOVES emission rates are **BSVI-calibrated representative values, not raw EPA BaseRateOutput** — disclosed at [backend/emission_engine.py:20-25, 228-230](backend/emission_engine.py#L20-L25).
- ⚠️ Cold-start is boolean, not 3-minute duration model — acceptable with disclosure.

### 3C — WLTC Driving Cycle ⚠️
- 1800 data points via ~100-waypoint linear interpolation. ✅
- **Phase boundaries** at [backend/simulator.py:49-54](backend/simulator.py#L49-L54) now read `(0,589)/(589,1022)/(1022,1477)/(1477,1800)`. With the `start <= t < end` logic at line 556, **second 589 is assigned to MEDIUM instead of LOW** (official: Low ends at 589 inclusive). This is an **off-by-one that still exists** at the boundary interpretation level. The tuple values were fixed from the previous audit (590→589) but the half-open interval semantics mean the boundary second is now assigned to the wrong phase.
- Peak speeds: 56.5/76.6/97.4/131.3 km/h. ✅ Exact.
- Total distance: ~23.40 km (official 23.27 km, error <0.6%). ✅
- Idle fraction: ~11% vs official ~13% — **gap explained** in docstring at [backend/simulator.py:210-216](backend/simulator.py#L210-L216). ✅
- WLTC is a **synthetic reconstruction, not official UN ECE R154** — **disclosed** at [backend/simulator.py:202-219](backend/simulator.py#L202-L219). ✅
- MIDC is a **reconstruction from AIS-137** — 1180 s, real spec data. ✅
- RPM model: correct wheel-to-engine formula, realistic gear ratios. ✅
- Fuel rate: uses `physics/vsp_model.py` Rakha-inspired polynomial when available; fallback has ad-hoc coefficients (0.000302 vs calculated 0.000409, ~26% off) — acceptable as fallback only.

---

## SECTION 4: MACHINE LEARNING AUDIT

**Verdict: ⚠️**

### 4A — Fraud Detection ✅
- Physics validator catches: speed>5∧rpm=0 ✅ ([ml/fraud_detector.py:111-116](ml/fraud_detector.py#L111-L116)), fuel<0.5∧vsp>10 ✅ ([ml/fraud_detector.py:118-124](ml/fraud_detector.py#L118-L124)), speed jump via temporal ✅ ([ml/fraud_detector.py:321-331](ml/fraud_detector.py#L321-L331)), replay ≥3 identical ✅ ([ml/fraud_detector.py:357-372](ml/fraud_detector.py#L357-L372)).
- Isolation Forest: n_estimators=100 ✅, contamination=0.05 ✅, random_state=42 ✅ ([ml/fraud_detector.py:235-240](ml/fraud_detector.py#L235-L240)).
- Engineered features: `fuel_efficiency` (co2/speed), `rpm_speed_ratio` (rpm/speed) beyond raw values ✅ ([ml/fraud_detector.py:210-211](ml/fraud_detector.py#L210-L211)).
- Weights: Physics 0.45 + IF 0.30 + Temporal 0.15 + Drift 0.10 = **1.00** ✅, validated at runtime ([ml/fraud_detector.py:741-745](ml/fraud_detector.py#L741-L745)).
- Temporal window: 10 samples, max accel ~4 m/s² (14.4 km/h/s) ✅.
- Model trained: checkpoint `data/fraud_detector_v3.2.pkl` (1.3 MB) ✅.
- Called from main pipeline ✅ ([backend/main.py:229](backend/main.py#L229)).

### 4B — LSTM Predictor ⚠️
- Architecture: LSTM 128→64, dropout 0.2, Huber loss. Sound but **no trained weights exist** (.h5 / SavedModel). ❌
- TF import graceful: `_TF_AVAILABLE` flag, falls back to `MockPredictor` ✅.
- Default path: `MockPredictor` (linear extrapolation) — functional but not ML. ✅
- **Explicitly disclosed as scaffold** at [ml/lstm_predictor.py:1-31](ml/lstm_predictor.py#L1-L31). ✅
- Training data exists: `ml/training_data.npy` (563 KB), generation script `ml/generate_training_data.py` ✅.

### Fraud Evaluation Metrics ⚠️
- [docs/fraud_eval_report.json](docs/fraud_eval_report.json): **P=0.742, R=0.299, F1=0.426** at n=5000, seed=42.
- [docs/FRAUD_EVALUATION.md](docs/FRAUD_EVALUATION.md): **numbers now match JSON exactly**. ✅ (Fixed since last audit.)
- Per-attack: physics_violation=100%, sudden_spike=85.5%, but replay=3.3%, frozen_sensor=3.3%, gradual_drift=7.0%, source_aware=3.7%. **Five of seven attack families below 10% recall.** ❌
- F1=0.426 is **below the 0.82 threshold** for a published paper. ❌
- **Honest note added** to FRAUD_EVALUATION.md §3.1 disclosing the gap. ✅
- **No held-out test set**: detector fit on 600 clean samples, evaluated on same harness without explicit train/test split. ⚠️

---

## SECTION 5: BLOCKCHAIN & SMART CONTRACT AUDIT

**Verdict: ✅**

### 5A — EmissionRegistry.sol ✅
- Compiles cleanly: Solidity 0.8.21, `viaIR=true`, optimizer runs=200. ✅
- Stores all 5 pollutants + CES + fraud score + VSP + phase + timestamp + status + device + station addresses ([contracts/EmissionRegistry.sol:145-164](contracts/EmissionRegistry.sol#L145-L164)). ✅
- Fixed-point: `SCALE_POLLUTANT=1000`, `SCALE_SCORE=10000` — matches Python exactly ([backend/blockchain_connector.py:57-58](backend/blockchain_connector.py#L57-L58)). ✅
- Compliance: `cesScore < CES_PASS_CEILING(10000) && fraudScore < FRAUD_ALERT_THRESHOLD(6500)` ([contracts/EmissionRegistry.sol:638](contracts/EmissionRegistry.sol#L638)). ✅
- Events: `RecordStored`, `ViolationDetected`, `FraudDetected`, `PollutantViolation` (per-pollutant at lines 701-705), `EmissionStoredHashed`, `PhaseCompleted`, `BatchRootCommitted`. ✅
- Access: `onlyStation nonReentrant whenNotPaused` on `storeEmission` ([contracts/EmissionRegistry.sol:600](contracts/EmissionRegistry.sol#L600)). ✅
- UUPS: `_disableInitializers()`, `initialize()`, `_authorizeUpgrade` with onlyAdmin, `uint256[47] private __gap`. ✅
- Gas: struct stores 9 `uint256` + 1 `uint8` + 1 `bool` + 2 `address` = ~330 bytes per record. storeEmission(PASS)=367,311 gas measured. Acknowledged.

### 5B — PUCCertificate.sol ✅
- ERC-721 via OpenZeppelin upgradeable. ✅
- `VALIDITY_PERIOD=180 days`, `FIRST_PUC_VALIDITY_PERIOD=360 days` per CMVR Rule 115. ✅
- `isValid()` checks: exists + not-revoked + not-expired ([contracts/PUCCertificate.sol:522-534](contracts/PUCCertificate.sol#L522-L534)). ✅
- `issueCertificate()` access-controlled via `onlyAuthorizedIssuer`. ✅
- Latest certificate tracked per vehicle. ✅
- ⚠️ Certificate data not directly linked to emission tx hash (event log link only, standard practice).

### 5C — Deployment & Integration ✅
- Deploy scripts: `scripts/deploy.js`, `scripts/deploy_amoy.js`, `scripts/deploy_multisig.js`. ✅
- ABI loaded from Truffle-shaped JSON in `build/contracts/` ([backend/blockchain_connector.py:136-175](backend/blockchain_connector.py#L136-L175)). ✅
- Gas estimation: `estimateGas() × 1.2` with 800k fallback ([backend/blockchain_connector.py:202-207](backend/blockchain_connector.py#L202-L207)). ✅ (Fixed since last audit.)
- `.env.example` comprehensive (156 lines) with all required vars. ✅
- Hardhat test suite: **73 passing (14s)**. ✅
- ⚠️ No public testnet deployment yet (Amoy configured but not executed). Remains from previous audit.

---

## SECTION 6: BENCHMARKING & EXPERIMENTAL RESULTS

**Verdict: ⚠️**

### 6A — Benchmark Suite ⚠️
- `benchmarks/scalability_test.py`: CLI default is mock (`use_real_blockchain=False`). Must pass `--real` for actual measurements. Unclear whether published numbers used `--real`. ⚠️
- Gas costs: **real** — from actual Hardhat receipts via `scripts/measure_gas.js` ([docs/gas_report.json](docs/gas_report.json)). ✅
- Latency: **real** — 1000 HTTP requests via `scripts/bench_latency.py`. ✅
- Throughput: **real** — concurrent worker sweep on Ganache. ✅

### 6B — Fraud Detection Metrics ❌
- **P=0.742, R=0.299, F1=0.426** — all below acceptable thresholds (P<0.85, R<0.80, F1<0.82). ❌
- No explicit train/test split in `ml/fraud_evaluation.py`. ⚠️
- Attack samples are realistic (7 families including source-aware adversary), but 5/7 achieve <10% detection. ❌
- **Honestly disclosed** in updated FRAUD_EVALUATION.md §3.1 and §4. ✅

### 6C — CES vs CO2-only ✅
- Real experiment: 5000 samples, actual CES vs CO2 comparison ([docs/ces_vs_co2_report.json](docs/ces_vs_co2_report.json)). CES uniquely catches 246 violations (6.17%); CO2-only catches 612 unique. Union dominates either alone. Honest framing in [docs/PAPER_FRAMING.md](docs/PAPER_FRAMING.md) §2.4. ✅

### 6D — Blockchain Comparison ❌
- [benchmarks/blockchain_comparison.py:81-125](benchmarks/blockchain_comparison.py#L81-L125): `PLATFORM_DATA` is a **hardcoded literature table** (Ethereum 15-30 TPS, Polygon 65-7000 TPS, Hyperledger 3000-20000 TPS). Not an experiment. Disclosed in [docs/PAPER_FRAMING.md](docs/PAPER_FRAMING.md) §3 item 7. ❌ for benchmarking, ✅ for honesty.

---

## SECTION 7: DATA INTEGRITY

**Verdict: ⚠️ (all fabricated data is disclosed)**

| Data point | Status | Evidence |
|---|---|---|
| VSP formula / vehicle params | ✅ Real, cited | EPA MOVES3, Indian car specs |
| BSVI thresholds | ✅ Real | ARAI 2020 gazette |
| IPCC CO2 factors (2310, 2680) | ✅ Real | Standard IPCC values |
| COPERT 5 cold-start multipliers | ✅ Real | COPERT 5 methodology |
| NOx Arrhenius coefficients | ✅ Real | Standard literature |
| MOVES emission-rate table | ⚠️ **Hand-calibrated** | Disclosed at [backend/emission_engine.py:20-25, 228-230](backend/emission_engine.py#L20-L25) |
| WLTC speed profile | ⚠️ **Synthetic reconstruction** | Disclosed at [backend/simulator.py:202-219](backend/simulator.py#L202-L219) |
| MIDC profile | ⚠️ **Reconstruction from AIS-137** | Disclosed |
| Blockchain comparison TPS | ❌ **Literature values** | Disclosed in PAPER_FRAMING.md §3 |
| Fraud detector training corpus | ⚠️ **Synthetic** | Disclosed in PAPER_FRAMING.md §3 |

Every fabricated or synthetic element is **explicitly disclosed** in `docs/PAPER_FRAMING.md` §3. No undisclosed fabrication found.

---

## SECTION 8: REAL-WORLD READINESS

**Verdict: ⚠️**

### 8A — OBD-II Integration ✅
- [integrations/obd_adapter.py:28-36](integrations/obd_adapter.py#L28-L36): PIDs 0x05 (coolant), 0x0C (RPM), 0x0D (speed), 0x0F (intake temp), 0x10 (MAF), 0x11 (throttle), 0x5E (fuel rate). SAE J1979 cited. ✅
- `/api/record` accepts POST with Pydantic validation. ✅
- Rate limiting (120 req/60s), API-key auth, input bounds. ✅
- Hardware path documented: ELM327 → python-obd → obd_device.py → REST → backend. ✅

### 8B — Indian Regulatory Alignment ✅
- CMVR Rule 115: 180-day (renewal) + 360-day (first PUC) correctly implemented. ✅
- BSVI and BSIV both supported via `BSStandard` enum and `computeCESForStandard`. ✅
- Petrol/diesel thresholds differentiated. ✅
- VAHAN bridge for vehicle registration lookup (mock + real API path). ✅

### 8C — System Resilience ⚠️
- Blockchain unreachable: backend degrades to offline mode, computes emissions and fraud locally, does not crash. ✅
- ⚠️ **No persistent outbox** — if chain is down, data is computed but not queued for later submission. Data is logged to SQLite but not retried on-chain.
- Malformed data: Pydantic rejects out-of-range values with HTTP 422. ✅
- SQLite WAL mode + `synchronous=NORMAL` — persistence survives restart. ✅

---

## SECTION 9: TESTING & RELIABILITY

**Verdict: ✅**

**Python tests**: `278 passed, 0 failed` in 39.92s (`pytest tests/`). ✅

**Hardhat tests**: `73 passing` in 14s (`npx hardhat test`). ✅

**Total: 351 tests, 0 failures.**

| Test file | Tests | Status |
|---|---|---|
| test_api.py | ~20 | ✅ |
| test_integration.py | ~10 | ✅ |
| test_emission_engine.py | 13 | ✅ |
| test_vsp_model.py | 12 | ✅ |
| test_simulator.py | 15 | ✅ |
| test_fraud_detector.py | ~15 | ✅ |
| test_lstm_predictor.py | ~10 | ✅ |
| test_pre_puc_predictor.py | ~12 | ✅ |
| test_station_fraud_detector.py | ~10 | ✅ |
| test_adversarial_api.py | 10 | ✅ |
| test_privacy_hashing.py | ~5 | ✅ |
| test_privacy_wiring.py | 3 | ✅ |
| test_obd_adapter.py | ~10 | ✅ |
| test_vaahan_bridge.py | ~10 | ✅ |
| test_merkle_batch.py | ~8 | ✅ |
| test_phase_listener.py | ~8 | ✅ |
| test_blockchain_connector.py | ~10 | ✅ |
| + 12 more test files | ~97 | ✅ |
| SmartPUC.test.js (Hardhat) | 73 | ✅ |

- ✅ Unit tests for core public functions.
- ✅ Integration test running full pipeline (test_integration.py).
- ✅ Edge cases tested (idle, max speed, negative accel, zero division).
- ✅ Adversarial tests at system level (test_adversarial_api.py — 6 attack classes via `/api/record`).
- ✅ CI/CD: 5-job GitHub Actions (solidity-tests, solidity-security/Slither, python-tests, lint/flake8, docker-build).
- ✅ `pytest.ini` with `-p no:ethereum` to bypass broken web3 plugin.

---

## SECTION 10: FRONTEND & DASHBOARD

**Verdict: ✅**

- ✅ Loads without errors (7 HTML pages: vehicle, authority, RTO, CPCB, fleet, marketplace, verify, analytics).
- ✅ Connects to real backend `/api/record` ([frontend/app.js:280](frontend/app.js#L280)).
- ✅ All 5 pollutants with threshold reference lines ([frontend/index.html:180-204](frontend/index.html#L180-L204)).
- ✅ Polling every 3s for real-time updates ([frontend/app.js:247-250](frontend/app.js#L247-L250)).
- ✅ Fraud alert banner bound to actual fraud scores ([frontend/index.html:65-75](frontend/index.html#L65-L75)).
- ✅ WLTC phase indicator present.
- ✅ CES gauge SVG arc ([frontend/index.html:120-145](frontend/index.html#L120-L145)).
- ✅ LSTM prediction graph (Chart.js canvas, [frontend/app.js:74](frontend/app.js#L74)).
- ✅ NFT certificate viewer with contract ABI ([frontend/app.js:46-54](frontend/app.js#L46-L54)).
- ✅ XSS prevention via `escapeHtml()` ([frontend/app.js:14-18](frontend/app.js#L14-L18)).
- ⚠️ CSP header uses `'unsafe-inline'` for legacy inline handlers. Documented follow-up item.

---

## SECTION 11: DOCUMENTATION & PUBLICATION READINESS

**Verdict: ✅**

### 11A — README ✅
- Explains project in first paragraph. ✅
- 3 quick-start options (Windows, Docker, manual). ✅
- ASCII architecture diagram (lines 40-64). ✅
- API reference, account roles, security features table. ✅
- Novel contributions listed. ✅

### 11B — Academic Citations ✅
All required citations present:
- EPA MOVES3: [physics/vsp_model.py:10-11](physics/vsp_model.py#L10-L11), [backend/emission_engine.py:33-34](backend/emission_engine.py#L33-L34) ✅
- ARAI BSVI: [backend/emission_engine.py:35-36](backend/emission_engine.py#L35-L36), [config/ces_weights.json](config/ces_weights.json) ✅
- COPERT 5: [backend/emission_engine.py:40](backend/emission_engine.py#L40), [integrations/obd_adapter.py:39-43](integrations/obd_adapter.py#L39-L43) ✅
- UN ECE R154: [backend/simulator.py:202-219](backend/simulator.py#L202-L219) ✅
- Heywood ICE Fundamentals: cited in emission_engine.py ✅
- Liu et al. Isolation Forest: cited in [ml/fraud_detector.py](ml/fraud_detector.py) ✅
- Rakha 2004: [backend/simulator.py:138-186](backend/simulator.py#L138-L186) ✅
- SAE J1979 / ISO 15031-5: [integrations/obd_adapter.py:17-20](integrations/obd_adapter.py#L17-L20) ✅

### 11C — Publication Readiness ⚠️
- **Novelty ratings:**
  - CES multi-pollutant composite: *incremental* (weighted sums standard in AQI)
  - On-chain VSP + phase + per-pollutant compliance: *genuinely novel*
  - ECDSA-signed OBD with on-chain nonce replay protection: *incremental but well-executed*
  - Station-level fraud detector: *genuinely novel*
  - Pre-PUC failure prediction from short OBD windows: *novel for BSVI/Indian context*
- **Experimental results**: gas cost ✅, latency ✅, throughput ✅, CES vs CO2 ✅, fraud accuracy ⚠️ (weak numbers honestly reported).
- **Comparison vs prior work**: literature table only (blockchain_comparison.py). ⚠️
- **LaTeX tables**: `docs/latex/ces_vs_co2_table.tex`, `docs/latex/gas_table.tex`. ✅

---

## SECTION 12: SECURITY AUDIT

**Verdict: ✅**

- ✅ Smart contract access-controlled: `onlyStation` on storeEmission, `onlyAuthorizedIssuer` on issueCertificate.
- ✅ Private keys from env vars only — no hardcoded keys in source. `.env` has dev-only values, `.gitignore` excludes it.
- ✅ Input validation: Pydantic bounds on all numeric fields, 422 on out-of-range.
- ✅ Solidity 0.8.21 has built-in overflow checks.
- ⚠️ Fraud detector's rules are transparent — an informed adversary can craft readings inside bounds (source-aware attack achieves 96.3% evasion). Documented in THREAT_MODEL.md.
- ✅ SQL injection: all queries parameterized (`?` placeholders). Zero f-string interpolation in SQL.
- ✅ No path traversal (no user-supplied filesystem paths).
- ✅ CORS default changed from `"*"` to `"http://localhost:3000"` ([backend/main.py:315](backend/main.py#L315)). ✅ (Fixed since last audit.)
- ✅ EIP-712 nonce replay protection on-chain.
- ⚠️ HTTPS not enforced at app level — expected at reverse proxy.

---

## SECTION 13: LIMITATIONS & GAPS

| # | Missing | Why it matters | Criticality | Fix |
|---|---|---|---|---|
| 1 | **Fraud F1=0.426** — five attack families <10% recall | Paper cannot claim strong fraud detection | **CRITICAL** | Increase IF training set to ≥2000 samples; reduce temporal window for replay; add per-VIN baseline to default pipeline |
| 2 | **WLTC phase boundary half-open interval** | At t=589, `start<=t<end` assigns MEDIUM not LOW (1s error) | **IMPORTANT** | Change bounds to `(0,590)/(590,1023)/(1023,1478)/(1478,1801)` with exclusive-end semantics |
| 3 | **No public testnet deployment** | Gas numbers are Hardhat-only | **IMPORTANT** | Deploy to Polygon Amoy, re-run measure_gas.js |
| 4 | **LSTM has no trained weights** | Paper cannot claim forecast accuracy | **IMPORTANT** | Train or drop from paper |
| 5 | **No persistent outbox for offline chain writes** | Data lost when chain is down | **IMPORTANT** | Add retry queue in persistence.py |
| 6 | **Pre-PUC predictor not in default pipeline** | Feature exists but disconnected | **NICE-TO-HAVE** | Wire into /api/record response |
| 7 | **No explicit train/test split in fraud eval** | ML evaluation methodology concern | **IMPORTANT** | Add `--holdout 0.3` to fraud_evaluation.py |
| 8 | **Blockchain comparison is literature table** | Reviewer expects experiment | **IMPORTANT** | Add caveat or deploy on 2+ chains |
| 9 | **backend/main.py is 1620+ line monolith** | Maintenance risk | **NICE-TO-HAVE** | Split into FastAPI routers |
| 10 | **No retry/backoff on RPC failures** | Fragile on public chains | **IMPORTANT** | Add exponential backoff to _send_tx |

---

## SECTION 14: FINAL VERDICT & PRIORITY ACTION ITEMS

**Overall Grade: 85/100**

| Category | Score | Max |
|---|---|---|
| Scientific accuracy & data integrity | 25 | 30 |
| Code quality, completeness & testing | 18 | 20 |
| Blockchain implementation | 14 | 15 |
| ML implementation | 6 | 10 |
| Real-world readiness | 7 | 10 |
| Documentation & citations | 10 | 10 |
| Publication readiness | 5 | 5 |
| **TOTAL** | **85** | **100** |

### Top 10 Priority Fixes

| # | Fix | File(s) | Why | Effort |
|---|---|---|---|---|
| 1 | **Improve fraud detector recall** — increase IF training to ≥2000, reduce replay window, enable per-VIN baseline | ml/fraud_detector.py, ml/fraud_evaluation.py | F1=0.426 is unpublishable | 8 h |
| 2 | **Fix WLTC phase boundary semantics** — use exclusive-end `(0,590)/(590,1023)/(1023,1478)/(1478,1801)` | backend/simulator.py:49-54 | Off-by-one still present at boundary interpretation | 15 min |
| 3 | **Deploy to Polygon Amoy** and re-run measure_gas.js | scripts/deploy_amoy.js, docs/gas_report.json | Paper needs public-chain numbers | 3 h |
| 4 | **Add train/test split to fraud evaluation** (--holdout 0.3) | ml/fraud_evaluation.py | No held-out test set is ML malpractice | 2 h |
| 5 | **Add RPC retry with exponential backoff** | backend/blockchain_connector.py:177-222 | Will fail on public testnets | 2 h |
| 6 | **Add persistent offline outbox** for chain writes | backend/persistence.py | Data lost when chain is down | 4 h |
| 7 | **Train LSTM or explicitly remove from paper** | ml/lstm_predictor.py | Architecture without weights is vapor | 4 h (train) or 1 h (remove) |
| 8 | **Wire pre-PUC predictor into pipeline** | backend/main.py, ml/pre_puc_predictor.py | Feature exists but is orphaned | 2 h |
| 9 | **Split backend/main.py into routers** | backend/main.py | 1620-line monolith | 4 h |
| 10 | **Add blockchain comparison caveat** or deploy on 2+ chains | benchmarks/blockchain_comparison.py | Literature table, not experiment | 1 h (caveat) or 6 h (deploy) |

### Honest Final Assessment

**Is this ready for IEEE submission right now?** Almost. The project is architecturally sound, honestly documented, and comprehensively tested (351 tests, 0 failures). The three specific blockers are: (1) the fraud detector's F1=0.426 is too low for the "ML fraud detection" claim — either improve the detector or reposition the paper as "physics-based fraud detection with ML augmentation" (physics gets 100% recall on impossible states, which IS publishable); (2) the WLTC phase boundary still has a half-open interval off-by-one that a careful reviewer will catch; (3) no public testnet gas numbers.

**Would a peer reviewer accept it?** Minor-to-major revision. The physics is rigorous, the blockchain is the strongest component (73 Hardhat tests, UUPS upgradeable, full event coverage), and the `docs/PAPER_FRAMING.md` §3 disclosures are unusually honest for academic work. A reviewer will challenge: "Why is fraud F1 only 0.43?" and "Your blockchain comparison is a literature table, not an experiment." Both are answerable — the first by repositioning around the physics validator's 100% floor, the second by explicit disclosure.

**Would an RTO officer trust this to replace a physical PUC test?** No. OBD-inferred emissions and tailpipe gas analyser measurements are different modalities. The project should position itself as a continuous monitoring layer between PUC tests, not a replacement.

**Would an ARAI scientist accept the methodology?** They would accept the VSP model (exact EPA MOVES3) and BSVI thresholds (exact ARAI gazette). They would challenge the synthetic WLTC reconstruction and hand-calibrated emission rates — both are disclosed, but ARAI may require validation against actual tailpipe measurements from their test facilities.

**Is the blockchain genuinely adding value?** Yes, through two features: (1) citizen-verifiable PUC via the ERC-721 NFT certificate, and (2) the GreenToken incentive economy. The append-only audit log is the weakest use case (a signed database would suffice), but the NFT + token combination creates a genuine trust architecture that a database cannot replicate.

**Single biggest weakness:** Fraud detection recall on soft attacks (3-7% on replay, drift, source-aware). The physics validator's 100% floor on impossible states is the real strength — the paper should lead with that.

**Single biggest strength:** `docs/PAPER_FRAMING.md` §3 — eight brutally honest self-disclosures that convert a potential desk-reject into a defensible submission.

**If only ONE thing could be fixed:** Reposition the fraud detection claim from "high-accuracy ensemble" to "physics-guaranteed floor with ML augmentation" and add a train/test split. The physics validator catching 100% of impossible states IS a publishable claim. The weak ML numbers become "future work" rather than the headline result.

---

## SECTION 15: PROGRESS TRACKING (vs Previous Audit)

### Score Comparison Table

| Category | Previous Score | Current Score | Change |
|---|---|---|---|
| Scientific accuracy & data integrity /30 | 22 | 25 | **+3** |
| Code quality, completeness & testing /20 | 13 | 18 | **+5** |
| Blockchain implementation /15 | 13 | 14 | **+1** |
| ML implementation /10 | 5 | 6 | **+1** |
| Real-world readiness /10 | 6 | 7 | **+1** |
| Documentation & citations /10 | 9 | 10 | **+1** |
| Publication readiness /5 | 6 | 5 | **-1** |
| **TOTAL /100** | **78** | **85** | **+7** |

### Issues Fixed Since Last Audit

| Previous Finding | Status | Details |
|---|---|---|
| ❌ **Stale fraud_eval_report.json (n=200, F1=0.38)** | ✅ **FIXED** | Regenerated with n=5000, seed=42. JSON and markdown now match (P=0.742, R=0.299, F1=0.426). |
| ❌ **"MOVES3" overclaim in emission_engine.py** | ✅ **FIXED** | Reworded at lines 228-230 and 377-379 to "BSVI-calibrated emission-rate surface informed by MOVES3". README updated. |
| ❌ **WLTC phase boundary off-by-one (590/1023/1478)** | 🔄 **PARTIALLY FIXED** | Tuple values changed from `(0,590)→(0,589)` etc., but the `start<=t<end` half-open interval means second 589 is still assigned to MEDIUM instead of LOW. Semantically equivalent off-by-one persists. |
| ❌ **`storeEmission` missing `nonReentrant`** | ✅ **WAS ALREADY DONE** | Line 600 already had `nonReentrant` — previous audit agent missed it. No change needed. |
| ❌ **pytest broken by web3 plugin** | ✅ **FIXED** | `pytest.ini` created with `addopts = -p no:ethereum`. Tests run cleanly. |
| ❌ **Hardcoded gas limits in blockchain_connector.py** | ✅ **FIXED** | `_send_tx` now calls `estimateGas() × 1.2` with 800k fallback. Hardcoded 1M/500k removed from callers. |
| ❌ **No system-level adversarial test** | ✅ **FIXED** | `tests/test_adversarial_api.py` created with 10 tests across 6 attack classes hitting `/api/record`. |
| ⚠️ **WLTC idle-fraction gap unexplained** | ✅ **FIXED** | Docstring at simulator.py:210-216 now explains the 2-point gap from shortened idle micro-trips. |
| ⚠️ **PRIVACY_MODE not in .env.example** | ✅ **FIXED** | Added `PRIVACY_MODE=1` with documentation referencing docs/PRIVACY_DPDP.md. |
| ⚠️ **Deprecated `@app.on_event("startup")`** | ✅ **FIXED** | Migrated to `@asynccontextmanager` lifespan handler. Privacy Mode status added to banner. |
| ⚠️ **CORS defaults to `*`** | ✅ **FIXED** | Changed fallback from `"*"` to `"http://localhost:3000"` at main.py:315. |

### New Issues Introduced Since Last Audit

1. **WLTC boundary fix created a new off-by-one** — the tuple values `(0,589)/(589,1022)/...` with `start<=t<end` logic still misassigns boundary seconds. The previous values `(0,590)/(590,1023)/...` were actually **closer to correct** for half-open intervals. The fix went in the wrong direction.
2. **Fraud eval metrics are now honestly reported but embarrassingly low** — F1=0.426 is below any publishable threshold. The previous stale report (F1=0.38 at n=200) was bad data; the honest report (F1=0.426 at n=5000) is real but weak. The paper's fraud claim needs repositioning.

### Stale Issues (Not Touched)

- ❌ **No public testnet deployment** — still Hardhat-only.
- ❌ **LSTM has no trained weights** — still architecture-only scaffold.
- ❌ **No persistent offline outbox** — data still lost when chain is down.
- ❌ **backend/main.py is still a 1620+ line monolith** — not split into routers.
- ❌ **Pre-PUC predictor still not in default pipeline** — standalone.
- ❌ **No RPC retry/backoff** — not added.
- ❌ **Blockchain comparison still a literature table** — not supplemented with real measurements.

### Overall Trajectory

**The project is improving.** Score rose from 78 to 85 (+7 points). Eight of ten priority fixes from the previous audit were addressed (6 fully fixed, 1 partially fixed, 1 was already done). The most impactful fixes were: regenerating the fraud eval report with honest numbers, adding `estimateGas()`, creating the adversarial test suite, migrating to FastAPI lifespan, and adding PRIVACY_MODE to `.env.example`. The two regressions (WLTC boundary direction and exposed fraud weakness) are honest corrections that ultimately strengthen the project's credibility — a project that publishes P=0.742 is more trustworthy than one that claims P=0.961 it cannot reproduce. The remaining stale items (testnet deployment, LSTM weights, offline outbox, monolith split) are important but not publication-blocking. **The project is on a clear upward trajectory.**

---

*End of report. No code changes were made during this audit.*
