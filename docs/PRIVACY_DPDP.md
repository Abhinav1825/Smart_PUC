# Smart PUC — Privacy Analysis and DPDP Act Compliance

This document analyses the privacy posture of Smart PUC against India's
**Digital Personal Data Protection Act, 2023 (DPDP Act)** and the EU GDPR,
identifies the data flows that contain personal data, and proposes the
technical mitigations that would be required for a production rollout.

> ⚠️ **Status.** The prototype stores vehicle registration numbers in plaintext
> on-chain. This is deliberately permissive for academic reproducibility; a
> production deployment **MUST** implement §3 below before handling real
> registrations.

## 1. Personal Data Inventory

The Digital Personal Data Protection Act 2023 defines **personal data** as
"any data about an individual who is identifiable by or in relation to such
data." Vehicle registration numbers are personal data under the DPDP Act
because they can be linked — via the VAHAN registry — to a named owner.

| Field | Storage location | DPDP classification | Notes |
|-------|------------------|---------------------|-------|
| `vehicleId` (e.g. MH12AB1234) | On-chain (EmissionRegistry, PUCCertificate) | **Personal data** | Directly deanonymisable via VAHAN lookup |
| `vehicleOwner` (wallet address) | On-chain (PUCCertificate) | **Pseudonymous** | Can be clustered with chain analytics |
| `owner_name`, `chassis`, `engine` | Off-chain (VAHAN bridge responses) | **Personal data** | Only present in backend memory; never written on-chain |
| Telemetry (speed, RPM, fuel_rate) | On-chain | Non-personal, but location-adjacent | Aggregated driving behaviour can be sensitive |
| Route / GPS | **Not collected** | N/A | Out of scope; design choice |
| CES / pollutant values | On-chain | Non-personal | Environmental measurements |
| Certificate expiry | On-chain | Non-personal | Public by design |
| API access tokens (JWT) | Backend memory + SQLite | Authentication data | Short-lived |
| Audit log entries | `backend/data/smartpuc.db` | Mixed | Contains IP addresses (pseudonymous) |

## 2. DPDP Act Principles — Gap Analysis

| § | Principle | Current implementation | Gap |
|---|-----------|------------------------|-----|
| 4(1)(a) | Lawful purpose | Emission compliance — clear public-interest ground | OK |
| 4(1)(b) | Consent / legitimate use | Not captured in prototype | **Gap — need consent flow for vehicle owners** |
| 5(1) | Notice | Not present | **Gap — need DPDP notice on authority portal and VAHAN bridge call** |
| 7 | Purpose limitation | Data used only for compliance + reward | OK (by design) |
| 8(1) | Accuracy | On-chain data is immutable; corrections would require a new record, not mutation | Partial — see §4 |
| 8(3) | Storage limitation | Records are permanent; DPDP allows longer retention for legal obligations | OK for compliance use case |
| 8(4) | Security safeguards | ECDSA signatures, JWT, rate limiting, TLS (deployment) | OK |
| 8(7) | Erasure on withdrawal of consent | **Impossible on a public chain** | **Gap — requires architectural mitigation (§3)** |
| 9 | Children's data | Not applicable | N/A |
| 11 | Right to correction and erasure | Cannot be satisfied if `vehicleId` is plaintext on-chain | **Gap** |

The **erasure gap (§8(7), §11)** is the central privacy problem. Once a vehicle
registration is written to a public blockchain, it is impossible to delete.
Three mitigation patterns address this.

## 3. Mitigation Patterns

### 3.1 Pseudonymisation via Salted Hash (Recommended for v1)

Store `keccak256(vehicleId || salt)` on-chain instead of the raw plate.
The mapping `hash → plate` lives in a **private, encrypted off-chain store**
operated by the RTO.

**Properties**

* On-chain records reveal nothing about the plate without the salt + mapping.
* DPDP erasure is achievable by destroying the off-chain mapping entry — the
  on-chain hash becomes unlinked and falls outside the DPDP scope (it is no
  longer "personal data about an identifiable person").
* Verification flow: scanning a physical QR gives the verifier the plate; the
  verifier locally computes the hash and queries the chain.

**Cost**

* Small — change `string vehicleId` in contracts to `bytes32 vehicleIdHash`
  for internal indexing (the registry already uses `bytes32` hashes
  internally — see `_vid` helper; §3.1 only requires dropping the
  `registeredVehicles` and per-record `vehicleId` plaintext fields).
* Slight UX change on the verify portal — verifier must input the plate, not
  read it from the chain.

**Limitation**

* Anyone who learns the salt can brute-force the ~30 million Indian plate
  space in seconds. The salt must be kept secret by the RTO.

### 3.2 Commitment + Off-chain Plaintext

Use a Pedersen commitment `C = g^vehicleId · h^r` on-chain. The commitment
binds the plate without revealing it, and the opening `(vehicleId, r)` is
stored off-chain encrypted. Supports DPDP erasure (delete the opening).

Pros: stronger than a salted hash (commitment hiding holds against *any*
PPT adversary if `g`, `h` are chosen correctly).
Cons: no native Pedersen support in EVM — requires a precompile or
off-chain proof library.

### 3.3 ZK Proof of Compliance (zkPUC — Future Work)

The most powerful option: the OBD device (or station) generates a
zero-knowledge proof of the statement:

> "There exists a plate *p*, a sequence of three emission records
> *(r₁, r₂, r₃)* signed by a key registered to *p*, such that for each record
> `CES(rᵢ) < PASS_CEILING` and the timestamps are within the last 90 days,
> and the hash of *p* is committed to in the public VAHAN registry."

On-chain we store only the proof and a nullifier (to prevent double-minting).
The raw plate never touches the chain.

**Benefits**
* Vehicle owner retains full cryptographic privacy against any external observer.
* Insurer/employer queries of "does vehicle X have a valid cert?" become
  the user's choice, not a public broadcast.
* Compatible with later addition of an accountable-anonymity layer (e.g.
  anonymous credentials) for enforcement.

**Cost**
* One-off: a Circom or Noir circuit (~2,000 constraints for ECDSA + three CES range checks).
* Per-proof: ~1–3 seconds on a mobile CPU with Groth16 or PLONK.

See §6 of the paper for a full zkPUC circuit sketch.

### 3.4 Permissioned Chain (Alternative)

A permissioned chain (Hyperledger Besu IBFT, Polygon Supernet, Quorum) keeps
the data inside an RTO consortium; nodes outside the consortium cannot read
raw records. This is the simplest way to get DPDP compliance but it sacrifices
the public verifiability that makes blockchain interesting in the first
place. We recommend it only if §3.1 + §3.3 are both infeasible.

See `docs/ARCHITECTURE_TRADEOFFS.md` for a deeper discussion.

## 4. Right to Correction

The on-chain storage is append-only. A correction is implemented as a new
record that references (via event log) the prior incorrect one. The
recommended pattern:

1. Authority flags the erroneous record via a new `RecordCorrected` event.
2. Analytics endpoints filter out records marked as corrected.
3. The original is retained on-chain to preserve auditability — consistent
   with DPDP §8(1) which requires *accuracy*, not *deletion*.

## 5. Cross-Border Transfer (DPDP §16)

Smart PUC's RPC layer may interact with globally distributed Ethereum nodes.
Under DPDP §16, cross-border transfer is permitted unless explicitly
restricted by a future Central Government notification. The mitigation is to
use only Indian validator nodes in a consortium deployment, or to restrict
the hash-only design of §3.1 so that no personal data leaves the country.

## 6. Incident Response

The DPDP Act §8(6) requires notification of a personal-data breach to the
Data Protection Board *and* affected data principals. Smart PUC operators
must have:

1. A monitoring pipeline that detects unauthorised `RecordStored` events from
   non-registered devices (already emitted via `FraudDetected`).
2. A breach-notification template.
3. A 72-hour notification SLA (aligning with GDPR best practice; DPDP does not
   yet prescribe a window).

## 7. Recommended Minimum Viable Privacy Profile (MVPP)

For a DPDP-compliant pilot deployment:

1. Replace plaintext `vehicleId` with `bytes32 vehicleIdHash` everywhere on-chain (§3.1).
2. Store the salt and hash→plate mapping in an encrypted Postgres managed by the RTO.
3. Require DPDP notice and consent in the onboarding UX.
4. Add a `RecordCorrected` event for §4.
5. Deploy admin ownership behind a 2-of-3 multisig.
6. Restrict backend to Indian RPC providers.
7. Publish a public privacy policy document.

## 8. References

1. Digital Personal Data Protection Act, 2023, Government of India.
2. Regulation (EU) 2016/679 (GDPR), Article 17.
3. Ben-Sasson, E. et al. *Zerocash: Decentralized Anonymous Payments*, IEEE S&P 2014.
4. Bellare, M. & Rogaway, P. *Collision-resistant hashing*, CRYPTO 1997.
5. Camenisch, J. & Stadler, M. *Proof systems for general statements about discrete logarithms*, 1997.
6. Polygon Miden whitepaper, *Privacy on programmable blockchains*, 2023.
