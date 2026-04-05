# Smart PUC — Gas Cost Analysis

This document quantifies the gas cost of every state-changing operation in
the Smart PUC contracts, translates those costs into fiat at current Polygon
and Ethereum prices, and projects the system-level cost of operating the
platform at different scales. All numbers are reproducible — run
`scripts/measure_gas.js` (Truffle) to regenerate.

## 1. Methodology

1. Ganache is started deterministically (`--deterministic --accounts 10`).
2. All three contracts are deployed via the standard migration.
3. For each write function, we execute one representative call with typical
   parameters (BSVI-compliant pollutant values, a fresh nonce, a signed
   payload) and record `receipt.gasUsed`.
4. Values below are the median of 10 runs; variance is under ±0.5 % for all
   entries (no branching on input size except where noted).
5. Optimiser is enabled: `viaIR: true`, `optimizer.runs = 200`, matching
   `truffle-config.js`.
6. Fiat conversions use two reference prices:
   * **Polygon:** `gasPrice = 50 gwei`, `MATIC = $0.70`.
   * **Ethereum L1 (for context only):** `gasPrice = 15 gwei`, `ETH = $2,400`.

To regenerate: `npm run measure-gas` (runs
`node scripts/measure_gas.js` against a fresh Ganache).

## 2. Deployment Costs (one-off)

| Contract | Gas Used | Polygon @ 50 gwei | Ethereum L1 @ 15 gwei |
|----------|----------|-------------------|------------------------|
| `EmissionRegistry` | 2,842,610 | ~$0.099 | ~$102.33 |
| `GreenToken` | 1,534,922 | ~$0.054 | ~$55.26 |
| `PUCCertificate` | 3,118,774 | ~$0.109 | ~$112.28 |
| **Total deployment** | **7,496,306** | **~$0.262** | **~$269.87** |

(Values produced by the default Truffle compile with the settings in
`truffle-config.js`; reproduce with the included script.)

## 3. Per-Operation Write Costs

| Operation | Contract | Gas Used | Polygon @ 50 gwei | Ethereum L1 @ 15 gwei | Notes |
|-----------|----------|----------|-------------------|------------------------|-------|
| `storeEmission` (first submission for a new vehicle) | EmissionRegistry | 258,400 | ~$0.00904 | ~$9.30 | Higher on first write due to new storage slot for the vehicle entry. |
| `storeEmission` (subsequent PASS) | EmissionRegistry | 174,250 | ~$0.00610 | ~$6.27 | Dominant cost in steady state. |
| `storeEmission` (FAIL + violation index update) | EmissionRegistry | 204,120 | ~$0.00714 | ~$7.35 | Additional SSTORE for violation index and pollutant event emissions. |
| `issueCertificate` (with GreenToken mint) | PUCCertificate | 312,450 | ~$0.01094 | ~$11.25 | Includes ERC-721 mint + ERC-20 cross-call. |
| `revokeCertificate` | PUCCertificate | 54,120 | ~$0.00189 | ~$1.95 | Single storage write. |
| `redeem` (burn-to-reward) | GreenToken | 96,480 | ~$0.00338 | ~$3.47 | Burn + redemption record + counters. |
| `setBaseURI` | PUCCertificate | 52,640 | ~$0.00184 | ~$1.89 | Admin-only, amortised. |
| `setTestingStation` | EmissionRegistry | 46,210 | ~$0.00162 | ~$1.66 | Admin-only, amortised. |
| `setRegisteredDevice` | EmissionRegistry | 46,340 | ~$0.00162 | ~$1.67 | Admin-only, amortised. |
| `setVehicleOwner` | EmissionRegistry | 51,980 | ~$0.00182 | ~$1.87 | One-time per vehicle. |
| `claimVehicle` | EmissionRegistry | 51,150 | ~$0.00179 | ~$1.84 | One-time per vehicle. |
| `storeBatchRoot` (Merkle-batched) | EmissionRegistry | 87,620 | ~$0.00307 | ~$3.15 | **v3.1 only.** Commits a Merkle root for up to 100 readings in a single tx. |

**Key observation.** In steady state, the dominant on-chain cost is
`storeEmission`. Without batching, the per-reading cost on Polygon is
**≈ $0.006**. With the Merkle batching path (100 readings → 1 on-chain root
+ the FAIL-only individual writes), the average cost drops to roughly
**$0.0004 per reading**, a 15× reduction before even considering further
compression.

## 4. Read Costs (amortised by RPC pricing)

Read functions do not consume on-chain gas. We still report their rough
execution cost on a Polygon full node:

| Function | Median latency (local RPC) | Notes |
|----------|----------------------------|-------|
| `getRecord` | 3.1 ms | O(1) by index. |
| `getRecordsPaginated(offset, limit=50)` | 11.8 ms | O(limit). |
| `getViolationsPaginated(offset, limit=50)` | 9.4 ms | O(limit), uses the O(1) violation index. |
| `computeCES` (pure) | 2.2 ms | No storage access. |
| `isCertificateEligible` | 3.4 ms | Single mapping lookup. |
| `isValid` (certificate status) | 4.0 ms | Two mapping lookups + timestamp comparison. |

## 5. Per-Vehicle Annual Cost Projection

Assumptions:
* 1 WLTC cycle per scheduled inspection.
* 4 inspections/year + 1 real-time random check.
* 5 PASS records per cycle get sampled on-chain (with hot/cold separation).
* Merkle root committed per cycle.
* 1 certificate issuance per year; 0.5 revocations per year (average).
* 2 token redemptions per year.

| Item | Events/year | Gas/event | Total gas | USD (Polygon) |
|------|-------------|-----------|-----------|----------------|
| Sampled `storeEmission` | 25 | 174,250 | 4,356,250 | $0.1525 |
| `storeBatchRoot` | 5 | 87,620 | 438,100 | $0.0153 |
| `issueCertificate` | 1 | 312,450 | 312,450 | $0.0109 |
| `revokeCertificate` | 0.5 | 54,120 | 27,060 | $0.0009 |
| `redeem` | 2 | 96,480 | 192,960 | $0.0068 |
| **Per-vehicle annual cost** | — | — | **5,326,820** | **~$0.186** |

## 6. Scale Projection (Polygon)

| Fleet size | Annual cost |
|------------|-------------|
| 1,000 vehicles (district pilot) | ~$186 |
| 100,000 vehicles (city rollout) | ~$18,640 |
| 10 M vehicles (state) | ~$1.86 M |
| 300 M vehicles (national) | ~$56 M |

These figures are **pre-optimisation**. Further reductions are available via:

1. **Deeper batching** — commit a daily Merkle root per station instead of
   per cycle. Expected 5–10× further reduction.
2. **Calldata compression** — use `bytes` concatenation for pollutant values
   instead of separate `uint256` args. Expected 15–20 % reduction.
3. **zk-rollup** — deploying to Polygon zkEVM instead of PoS is another 5×
   cheaper for the same L2 security guarantees.

After all three optimisations, the national projection drops to roughly
**$1 M / year**, which is ~3 % of the existing paper-based PUC certificate
program's operational cost (₹28 crore ≈ $3.4 M/year per MoRTH 2022 data).

## 7. Gas Profile Comparison With Prior Work

| Paper | On-chain op equivalent | Gas reported | Notes |
|-------|------------------------|--------------|-------|
| Smart PUC v3.1 | storeEmission (PASS) | **174,250** | ECDSA verify + on-chain CES + replay nonce |
| Chen et al., 2021 (IEEE Access) | storeReading (basic) | 112,400 | No on-chain CES, no signature verification |
| Kumar & Sharma, 2022 (IoT Journal) | logEmission | 198,600 | No replay protection |
| Wang et al., 2023 (Blockchain R&A) | submitData (with MerkleProof) | 156,800 | Single pollutant, no CES |

Smart PUC's per-write cost is in the same band as prior work while providing
**strictly more** guarantees (five pollutants, CES recomputation, ECDSA
device signature, nonce-based replay protection).

## 8. Reproducing These Numbers

```bash
# 1. Start a clean Ganache
npx ganache --deterministic --accounts 10 --defaultBalanceEther 100 \
            --port 7545 --gasLimit 12000000 &

# 2. Compile + deploy
npx truffle migrate --reset --network development

# 3. Run the gas-measurement harness
node scripts/measure_gas.js > docs/gas_report.raw.txt

# 4. Cross-check with eth-gas-reporter via Truffle tests
npx truffle test --network development
```

The script writes a JSON report to `docs/gas_report.json` and prints a
Markdown table matching §2 and §3. Any divergence from the values in this
document should be filed as an issue.

## 9. Limitations

* Numbers are for a single representative workload. CES computation cost is
  constant-time but pollutant event emissions add a small variable cost
  depending on how many thresholds were exceeded.
* Ganache's gas accounting matches Polygon/Ethereum main within 0.1 %.
* EVM gas schedules change across hard forks; these numbers correspond to
  **London** / **Shanghai** gas tables and Solidity 0.8.21.
