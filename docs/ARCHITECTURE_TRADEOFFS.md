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

The prototype uses **Truffle 5.11** because it was chosen before ConsenSys
deprecated Truffle in September 2023. Truffle still works but is no longer
maintained.

Recommended migration path (not yet applied — see `docs/ROADMAP.md`):

1. **Hardhat + ethers v6** — near drop-in replacement, TypeScript support,
   better stack traces. Low-risk migration; estimated half-day.
2. **Foundry** — faster tests and fuzz testing. Higher-risk migration
   because the test suite (`test/TestEmission.js`) would have to be rewritten
   in Solidity; estimated two days.

We recommend **Hardhat** as the first step and leave Foundry as an optional
second step for the fuzz test harness only.

## 3. Flask vs. FastAPI

Flask was chosen for familiarity and its tiny footprint in Docker. Tradeoffs:

| Dimension | Flask (current) | FastAPI (proposed) |
|-----------|-----------------|---------------------|
| Async support | No (blocking WSGI) | Yes (ASGI / uvicorn) |
| Request validation | Manual `data.get(...)` | Pydantic models |
| OpenAPI / Swagger | Manual | Generated automatically |
| Throughput (uvicorn vs gunicorn sync) | ~800 req/s | ~2500 req/s |
| Learning curve | Negligible | 1 day |
| Code churn for migration | Mostly mechanical | ~50 routes × 20 min each |

Migration is recommended but non-trivial. Current Flask code remains correct
and is adequate for a pilot-scale deployment. See `docs/ROADMAP.md`.

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

The repository contains working Truffle network entries for Polygon, Amoy,
**zkEVM**, and **Arbitrum** (see `truffle-config.js`).

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
