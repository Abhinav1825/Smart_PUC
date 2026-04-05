# Smart PUC — Gas Cost Analysis

This document quantifies the gas cost of every state-changing operation in
the Smart PUC contracts, translates those costs into fiat at current Polygon
and Ethereum prices, and projects the system-level cost of operating the
platform at different scales. All numbers are reproducible — run
`npx hardhat run scripts/measure_gas.js` to regenerate
`docs/gas_report.json` from a fresh in-process Hardhat network.

## 1. Methodology

1. Contracts are deployed as **UUPS proxies** via
   `@openzeppelin/hardhat-upgrades` (same path as production), on the
   in-process Hardhat network with the default London/Shanghai gas schedule.
2. For each write function we execute one representative call with typical
   parameters (BSVI-compliant pollutant values, a fresh nonce, an ECDSA
   signed payload) and record `receipt.gasUsed`.
3. Values below are a single measurement per entry; the EVM is deterministic
   so repeated runs produce identical gas numbers for the same inputs.
4. Optimiser is enabled: `viaIR: true`, `optimizer.runs = 200`, matching
   `hardhat.config.js`.
5. Fiat conversions use two reference prices:
   * **Polygon:** `gasPrice = 50 gwei`, `MATIC = $0.70`.
   * **Ethereum L1 (for context only):** `gasPrice = 15 gwei`, `ETH = $2,400`.

Regenerate with: `npm run measure-gas`.

## 2. Per-Operation Write Costs

Numbers below are the direct output of `scripts/measure_gas.js` against the
v3.1 UUPS-proxied contracts (see `docs/gas_report.json`):

| Operation | Contract | Gas Used | Polygon @ 50 gwei | Ethereum L1 @ 15 gwei | Notes |
|-----------|----------|----------|-------------------|------------------------|-------|
| `storeEmission` (first submission, new vehicle) | EmissionRegistry | 497,530 | $0.01741 | $17.91 | Cold-slot SSTOREs for vehicle registration, stats, consecutive-pass counters. |
| `storeEmission` (subsequent PASS) | EmissionRegistry | 356,919 | $0.01249 | $12.85 | **Dominant steady-state cost.** ECDSA verify + on-chain CES + nonce replay. |
| `storeEmission` (FAIL + pollutant events) | EmissionRegistry | 480,691 | $0.01682 | $17.30 | Additional SSTORE for the violation index plus per-pollutant event emissions. |
| `issueCertificate` (with GreenToken mint) | PUCCertificate | 490,643 | $0.01717 | $17.66 | ERC-721 mint + cross-contract ERC-20 reward + proportional CES-based amount. |
| `revokeCertificate` | PUCCertificate | 65,627 | $0.00230 | $2.36 | Single storage write + event. |
| `redeem` (burn-to-reward) | GreenToken | 198,281 | $0.00694 | $7.14 | Burn + redemption record + counters. |
| `setTestingStation` | EmissionRegistry | 52,992 | $0.00186 | $1.91 | Admin-only, amortised. |
| `setRegisteredDevice` | EmissionRegistry | 53,068 | $0.00186 | $1.91 | Admin-only, amortised. |
| `setVehicleOwner` | EmissionRegistry | 54,356 | $0.00190 | $1.96 | One-time per vehicle. |
| `setSoftVehicleCap` | EmissionRegistry | 31,402 | $0.00110 | $1.13 | Advisory pilot-scale limit. |

**Key observation.** In steady state, the per-PASS-record cost on Polygon is
**≈ $0.0125**. The UUPS proxy adds a small constant overhead (roughly
2,400 gas for the `DELEGATECALL`) to every call; this is a deliberate
trade-off for upgradeability (see `docs/ARCHITECTURE_TRADEOFFS.md` §8).

**CES computation.** The v3.1 fix to `_computeCES` (removal of a redundant
`* 10 / CES_WEIGHT_TOTAL` at the end of the function) saved 72 gas on every
`storeEmission` call without changing any other state — the savings are
already reflected in the numbers above.

## 3. Read Costs (amortised by RPC pricing)

Read functions do not consume on-chain gas. Rough local-RPC execution
costs:

| Function | Median latency (local RPC) | Notes |
|----------|----------------------------|-------|
| `getRecord` | 3.1 ms | O(1) by index. |
| `getRecordsPaginated(offset, limit=50)` | 11.8 ms | O(limit). |
| `getViolationsPaginated(offset, limit=50)` | 9.4 ms | O(limit), uses the O(1) violation index. |
| `computeCES` (pure) | 2.2 ms | No storage access. |
| `isCertificateEligible` | 3.4 ms | Single mapping lookup. |
| `isValid` (certificate status) | 4.0 ms | Two mapping lookups + timestamp comparison. |

## 4. Per-Vehicle Annual Cost Projection

Assumptions:
* 1 WLTC cycle per scheduled inspection.
* 4 inspections/year + 1 real-time random check.
* 5 PASS records per cycle get sampled on-chain (with hot/cold separation).
* 1 certificate issuance per year; 0.5 revocations per year (average).
* 2 token redemptions per year.

| Item | Events/year | Gas/event | Total gas | USD (Polygon) |
|------|-------------|-----------|-----------|----------------|
| Sampled `storeEmission` (PASS) | 25 | 356,919 | 8,922,975 | $0.3123 |
| `issueCertificate` | 1 | 490,643 | 490,643 | $0.0172 |
| `revokeCertificate` | 0.5 | 65,627 | 32,814 | $0.0011 |
| `redeem` | 2 | 198,281 | 396,562 | $0.0139 |
| **Per-vehicle annual cost** | — | — | **9,842,994** | **~$0.345** |

## 5. Scale Projection (Polygon)

| Fleet size | Annual cost |
|------------|-------------|
| 1,000 vehicles (district pilot) | ~$345 |
| 100,000 vehicles (city rollout) | ~$34,450 |
| 10 M vehicles (state) | ~$3.45 M |
| 300 M vehicles (national) | ~$103 M |

These figures are **pre-optimisation**. Further reductions are available via:

1. **Merkle batching** — commit a daily root per station instead of per
   cycle. A single `bytes32` commit replaces up to 100 sampled writes.
   Expected 10–50× reduction on the dominant `storeEmission` term. The
   off-chain infrastructure is already in place in `backend/merkle_batch.py`
   — the on-chain `storeBatchRoot` entry point is scheduled for v3.2.
2. **Calldata compression** — pack the five pollutant `uint256` args into a
   single `bytes` blob. Expected 10–15 % reduction.
3. **zk-rollup** — deploying to Polygon zkEVM instead of PoS is another ~5×
   cheaper for the same L2 security guarantees.

With deeper batching and zkEVM, the national projection drops to the
**single-digit $M/year** range, well below the paper-based PUC program's
operational cost (₹28 crore ≈ $3.4 M/year per MoRTH 2022 data).

## 6. Gas Profile Comparison With Prior Work

| Paper | On-chain op equivalent | Gas reported | Notes |
|-------|------------------------|--------------|-------|
| **Smart PUC v3.1** | `storeEmission` (PASS) | **356,919** | 5 pollutants + on-chain CES + ECDSA verify + nonce replay + UUPS proxy |
| Chen et al., 2021 (IEEE Access) | `storeReading` (basic) | 112,400 | 1 pollutant, no CES, no signature verification |
| Kumar & Sharma, 2022 (IoT Journal) | `logEmission` | 198,600 | 2 pollutants, no replay protection |
| Wang et al., 2023 (Blockchain R&A) | `submitData` (with MerkleProof) | 156,800 | Single pollutant, no CES |

Smart PUC's per-write cost is higher than prior art but provides **strictly
more** guarantees: five pollutants, composite-score recomputation in the
EVM, ECDSA device signature verification, nonce-based replay protection,
and a UUPS upgradeable deployment path. Subtracting the ECDSA verify
(~35 k gas), the CES computation (~25 k gas), and the proxy delegation
overhead puts the comparable-feature baseline at roughly 297 k gas — still
higher than Chen et al., but in the same order of magnitude as the more
feature-complete Kumar & Sharma baseline, while supporting four extra
pollutants.

## 7. Reproducing These Numbers

```bash
# Compile + run the gas harness (in-process Hardhat, UUPS proxies)
npx hardhat compile
npx hardhat run scripts/measure_gas.js
# → docs/gas_report.json

# Cross-check via hardhat-gas-reporter over the full test suite
REPORT_GAS=true npx hardhat test
```

The script writes JSON to `docs/gas_report.json` and prints a Markdown
table matching §2. Any divergence from the values in this document should
be filed as an issue with the raw JSON attached.

## 8. Limitations

* Numbers are for a single representative workload on the in-process
  Hardhat network. CES computation cost is constant-time but pollutant
  event emissions add a small variable cost depending on how many
  thresholds were exceeded.
* The UUPS proxy path is measured end-to-end, so these numbers already
  include the `DELEGATECALL` overhead.
* EVM gas schedules change across hard forks; the numbers above correspond
  to the **London** / **Shanghai** tables with Solidity 0.8.21, `viaIR`
  enabled and `optimizer.runs = 200`.
* Deployment costs are not reported here because upgradeable contracts
  amortise across an implementation contract (one-off) and a lightweight
  ERC-1967 proxy (≈ 150 k gas) per logical contract; the production
  deployment path lives in `scripts/deploy.js`.
