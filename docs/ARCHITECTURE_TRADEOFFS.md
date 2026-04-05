# Smart PUC — Architecture Trade-offs

This document records the major design decisions behind Smart PUC, the
alternatives we rejected, and the context in which a future operator might
pick differently. It exists to pre-empt the classic reviewer objection
*"why didn't you use X?"*.

## 1. Public EVM Chain vs. Permissioned (Besu / Polygon Supernet)

| Criterion | Public EVM (chosen) | Permissioned chain |
|-----------|---------------------|--------------------|
| Public verifiability | ✅ Anyone can verify a certificate with zero trust | ❌ Only consortium members |
| Privacy | ❌ Plate data visible to all | ✅ Access-controlled |
| Gas cost | Real MATIC fees | Zero |
| Regulatory familiarity in India | Low — novel for MoRTH | Medium — similar to IBM Food Trust |
| Developer ergonomics | Excellent (Truffle/Hardhat/Foundry, OpenZeppelin) | Slower, more operator burden |
| Time to prototype | Days | Weeks |
| Censorship resistance | High | Depends on governance |
| Open ecosystem integrations | Hundreds of wallets, explorers | Very limited |

**Why we chose public EVM.** Smart PUC's unique selling point is that *any
citizen* can independently verify a certificate — a permissioned chain
would force every verifier (employer, insurer, traffic cop) to join the
consortium, destroying most of the benefit. Privacy concerns are solved
instead with the pseudonymisation / ZK techniques described in
`PRIVACY_DPDP.md`.

**When you should pick the permissioned route.** If (a) DPDP §8(7) erasure
is a hard legal requirement, (b) the RTO cannot accept pay-per-transaction
gas costs, and (c) public verifiability is not a regulatory goal, a Besu
IBFT consortium or Polygon Supernet becomes attractive. The contracts in
this repository compile unchanged under Besu (it is EVM-equivalent) so the
migration cost is dominated by operations, not development.

## 2. Truffle vs. Hardhat vs. Foundry

Smart PUC **v3.1 migrated from Truffle to Hardhat + ethers v6**. The
drivers were:

* Truffle was officially deprecated by ConsenSys in September 2023 and is
  no longer maintained.
* Hardhat's in-process EVM makes tests run without an external node.
* The OpenZeppelin Upgrades plugin (`@openzeppelin/hardhat-upgrades`)
  integrates natively with Hardhat, enabling the UUPS proxy deployment
  path used by `scripts/deploy.js`.
* `hardhat-gas-reporter` produces per-function gas tables that feed
  directly into `docs/GAS_ANALYSIS.md`.

Compatibility was preserved for the Python backend by
`scripts/flatten_artifacts.js`, which post-processes Hardhat's default
`artifacts/contracts/<Name>.sol/<Name>.json` outputs into the legacy
`build/contracts/<Name>.json` shape expected by
`backend/blockchain_connector.py`.

**Foundry** remains an optional follow-up — useful primarily for fuzz
testing the CES and signature-verification arithmetic. It would
complement, not replace, Hardhat.

## 3. Flask vs. FastAPI

Smart PUC **v3.1 migrated from Flask to FastAPI + uvicorn**. The
drivers were:

| Dimension | Flask (v3.0) | FastAPI (v3.1, current) |
|-----------|---------------|--------------------------|
| Async support | No (blocking WSGI) | Yes (ASGI / uvicorn) |
| Request validation | Manual `data.get(...)` + bounds checks | Pydantic v2 models |
| OpenAPI / Swagger | Manual / none | Auto-generated at `/docs` |
| Throughput (single process) | ~120 TPS | expected ~300–500 TPS |
| Type safety | Runtime `isinstance` checks | Pydantic + typed endpoints |

The FastAPI app lives in `backend/main.py`, with Pydantic schemas in
`backend/schemas.py` and reusable dependencies in `backend/dependencies.py`.
Every legacy Flask route was ported 1:1, so the frontend, OBD simulator,
and benchmark scripts continue to work unchanged.

## 4. On-chain CES vs Off-chain with Proof

We chose **on-chain CES recomputation** because it is the simplest way to
make the score un-forgeable. Alternatives:

* **Off-chain computation + validity proof** (zkSNARK of the CES formula):
  saves gas, preserves un-forgeability, but adds a proving circuit.
  Considered future work — see paper §6.
* **Trust the station's CES score** with economic slashing: cheaper but
  requires a bonded-stake scheme. Rejected — slashing is politically hard
  for a government-operated station.

## 5. `string vehicleId` vs `bytes32 vehicleIdHash`

The contracts use a hybrid approach: the *public interface* takes strings
for usability, and internal mappings use `_vid(string) → bytes32` for gas
efficiency. This balances developer ergonomics with cost.

For the DPDP-compliant variant (see `PRIVACY_DPDP.md` §3.1), the public
interface would also switch to `bytes32` and the verify flow would require
the user to compute the hash client-side.

## 6. Merkle-Batched Telemetry vs. Per-Record On-chain Storage

A single WLTC cycle produces 1800 readings. Writing all of them individually
to Polygon at ~50 gwei costs roughly `1800 × 0.0015 USD ≈ $2.70 per vehicle
per cycle`. That does not scale to India's 300 M vehicles.

**Chosen architecture (v3.1):** hot-path / cold-path separation.
* Every reading is stored in SQLite (cold path) — complete audit trail.
* Only the following go on-chain:
  * **Violations** (FAIL records) — always, individually, with signature.
  * **Sampled summary records** — every Nth reading, configurable.
  * **A Merkle root** committing to the batch of N readings — enables proof
    that any individual reading belonged to the committed batch.
* Frontend verification uses the Merkle proof against the on-chain root
  when a full audit is requested.

This cuts on-chain writes by 100× without sacrificing tamper-evidence. See
`backend/merkle_batch.py` and `docs/GAS_ANALYSIS.md`.

## 7. Single-Chain vs. L2 Deployment

Polygon mainnet was chosen for low fees and EVM equivalence. The same
contracts deploy on:

| Network | Finality | Fee class | When to use |
|---------|----------|-----------|-------------|
| Polygon PoS | 2–3 s | Very low | Default production pick |
| **Polygon zkEVM** | 10 min to L1 | Very low | When you want ZK-rollup security + EVM equivalence |
| **Arbitrum One** | 1 min soft / 7 day hard | Very low | When you want maximum TVL / ecosystem |
| Ethereum mainnet | 12 s | Expensive | Only if regulatory audit demands L1 |
| Sepolia | — | Free | Dev / testing |

The repository contains working Hardhat network entries for Polygon, Amoy,
**zkEVM**, **zkEVM Cardona**, **Arbitrum One**, and **Arbitrum Sepolia**
(see `hardhat.config.js`).

## 8. What We Would Do Differently for a Greenfield v4

If starting over today:

1. **Foundry + Hardhat hybrid.** Foundry for fuzz tests, Hardhat for scripts.
2. **FastAPI + Pydantic + uvicorn** from day 1.
3. **UUPS upgradeable proxies** on every contract.
4. **EIP-712 domain separation** in the device signature (chain-id bound).
5. **Pseudonymised `vehicleIdHash`** on-chain by default; plaintext only in
   the RTO-operated backend.
6. **Polygon zkEVM** as the default deployment target.
7. **Event sourcing** via Redis Streams between OBD device and station.
8. **Postgres** instead of SQLite for anything beyond single-node.

None of these would change the core trust model or the research claims.
