# Smart PUC — Threat Model

This document defines the security assumptions, the adversary capabilities we
defend against, and the mitigations implemented (or proposed as future work)
for every component of the Smart PUC system. It is intended to accompany any
academic paper derived from this project and to make the security claims
falsifiable.

## 1. System Model

The system consists of three trust domains, each assumed to be operated by a
different principal:

| Node | Principal | Trust assumption |
|------|-----------|------------------|
| N1 — OBD Device | Vehicle owner / factory-installed module | Partially trusted. Holds a device-specific ECDSA key. Assumed honest-but-physically-exposed. |
| N2 — Testing Station | RTO-authorised testing centre | Partially trusted. Holds a station key with on-chain write permission. May be economically motivated to cheat. |
| N3 — Blockchain | Public or consortium chain (Polygon / Sepolia) | Trusted for availability, integrity, and ordering, under the standard honest-majority assumption of the underlying consensus. |
| N4 — Verification Portal | End user / auditor / law enforcement | Untrusted client reading from N3. No private keys. |
| N5 — Backend host | Testing-station infrastructure | Partially trusted. Compromise leaks the station's hot key but cannot forge OBD signatures. |

The system does **not** assume any node besides the blockchain is honest.
Every claim is backed by a cryptographic or protocol-level mitigation.

## 2. Security Goals

| ID | Goal | Type |
|----|------|------|
| G1 | Data provenance: every stored record is cryptographically bound to a registered OBD device. | Integrity |
| G2 | Tamper-evidence: no record can be modified after it is written on-chain. | Integrity |
| G3 | Non-repudiation: a testing station cannot deny submitting a record it signed. | Accountability |
| G4 | Replay resistance: a captured telemetry message cannot be resubmitted. | Freshness |
| G5 | Score integrity: the Composite Emission Score (CES) cannot be falsified by an intermediate party. | Integrity |
| G6 | Availability: the system remains available under reasonable rate-limit adversaries. | Availability |
| G7 | Privacy: vehicle and owner data are protected from unauthorised disclosure (see `PRIVACY_DPDP.md`). | Confidentiality |

## 3. Adversary Model

We consider a **polynomial-time active adversary** that may:

1. Eavesdrop on all network traffic.
2. Inject, replay, reorder, or drop any message outside the blockchain gossip layer.
3. Compromise any one node at a time (isolated compromise).
4. Possess arbitrary computational resources below cryptographic hardness assumptions (e.g., cannot forge ECDSA signatures).
5. Be economically rational: willing to pay gas and bribes up to the expected payoff of a successful attack.

We do **not** defend against:

* Simultaneous compromise of N1, N2 and the chain consensus.
* Hardware side-channel attacks on a secure element.
* Nation-state–level deanonymisation combining on-chain pseudonyms with off-chain ALPR data (flagged as open problem in §6).

## 4. Adversary → Capability → Mitigation Table

| # | Adversary | Capability | Target goal | Attack Surface | Mitigation (implemented) | Residual risk |
|---|-----------|------------|-------------|----------------|---------------------------|----------------|
| A1 | Malicious testing station | Fabricate emission data and submit to chain | G1, G5 | `EmissionRegistry.storeEmission` | Contract requires a valid ECDSA signature from a **registered** OBD device; CES is recomputed on-chain (§5.2). | Key extracted from a compromised OBD device (A3). |
| A2 | Malicious testing station | Supply an artificially low CES score | G5 | `storeEmission` payload | `_computeCES` recomputes the score on-chain from raw pollutant values; station-supplied score is ignored. | None under current design. |
| A3 | Physical attacker with OBD access | Extract device private key | G1 | OBD device firmware / storage | Current prototype stores the key in `.env`. **Mitigation plan (§5.7):** move key into a secure element (ATECC608A), TPM 2.0, or ARM TrustZone-backed keystore. | Non-HSM builds remain vulnerable; documented limitation. |
| A4 | Passive MITM between OBD and station | Capture signed telemetry and replay it later | G4 | HTTP POST to `/api/record` | Every telemetry payload binds a fresh 32-byte `nonce` into the signed hash; contract rejects any previously seen nonce via `usedNonces` mapping. | None. |
| A5 | Active MITM | Tamper with the in-flight payload | G1 | Network link | Payload hash includes vehicle ID, five pollutants, timestamp, and nonce; any mutation invalidates the ECDSA signature. | None. |
| A6 | Attacker with stolen station key | Submit signed records for vehicles they do not own | G5 | `onlyStation` modifier | Station write still requires a valid OBD device signature (A1). Admin can revoke the station via `setTestingStation(addr, false)`. | Window between compromise and revocation; mitigated by monitoring (§5.9). |
| A7 | Sybil attacker | Register thousands of rogue OBD devices | G1 | `setRegisteredDevice` | `onlyAdmin` modifier restricts device registration to the admin account; in a production deployment the admin is controlled by RTO via a multisig. | Social engineering of the RTO admin. |
| A8 | Eclipse attack on RPC node | Feed the backend a stale view of the chain | G2, G6 | Web3 RPC layer | Backend reads from a configurable RPC; production deployments should use ≥ 3 independent providers (Infura, Alchemy, self-hosted) and cross-validate block hashes. | Not yet implemented in code — future work. |
| A9 | Replay across chains | Replay a mainnet transaction on a testnet (or vice versa) | G4 | Signed payload | Payload does **not** yet include chain-id in the hash. **Known gap** — see §6 Future Work (EIP-712 domain separation). | Currently mitigated only by the `usedNonces` mapping being chain-local. |
| A10 | Greedy miner / censor | Front-run or drop emission submissions | G6 | Tx ordering | Contract logic is order-independent (nonce uniqueness is checked, not ordering). No MEV opportunity exists in storing a record. | Temporary censorship possible; mitigated by using PoS L2. |
| A11 | DoS against backend | Flood `/api/record` | G6 | Flask endpoint | Per-IP token-bucket rate limiter (default 120 req/min); JWT required for authority endpoints; API key required for write endpoints. SQLite-persisted rate limits (see `backend/persistence.py`). | Distributed (botnet) DoS still possible; recommend CloudFlare or WAF in front. |
| A12 | Replay via nonce grinding | Exhaust nonce space of a device | G4 | `usedNonces` | Nonces are 32 random bytes (2²⁵⁶ space); grinding is infeasible. | None. |
| A13 | Fraudulent sensor spoofing | Feed the OBD board plausible but false readings | G1 | Upstream of the signer | Off-chain 3-layer fraud detector: hard physics constraints + Isolation Forest + temporal consistency. Evaluated in `/docs/FRAUD_EVALUATION.md`. | Adversarial perturbations below detection threshold; open research problem. |
| A14 | Fraudulent gradual drift | Slowly shift sensor readings to hide a failing vehicle | G1 | Over many submissions | Temporal consistency checker flags monotonic drift; per-vehicle digital-twin baseline (future work) would tighten this. | Slow drift close to the noise floor may evade detection. |
| A15 | Malicious admin | Revoke arbitrary stations / devices to censor honest data | G6 | `onlyAdmin` | Assumes admin is a multisig controlled by RTO / MoRTH. Documented requirement, not enforced in prototype. | Non-multisig deployments are vulnerable. |
| A16 | Economic attacker | Mint tokens by obtaining fake certificates | G5 | Certificate issuance | Certificate contract requires ≥ 3 consecutive PASS records, each independently signature-verified; PUC issuance itself requires an authorised station. | Equivalent to A1+A3 chain. |
| A17 | Frontend XSS | Inject script via vehicle ID or owner field | Integrity of the UI | Frontend rendering | All user-supplied strings are HTML-escaped before insertion; inline handlers removed; CSP header recommended (see `frontend/*.html`). | Third-party CDN compromise — mitigated by SRI hashes added in v3.1. |
| A18 | Downgrade attack on TLS | Force HTTP instead of HTTPS | Confidentiality | Transport | Production deployments must terminate TLS at a reverse proxy; backend exposes no transport-level upgrade logic. Not enforced in the Docker compose demo. | Documented. |
| A19 | Privacy deanonymisation | Link on-chain records to a real identity via the vehicle plate | G7 | Public chain | Pseudonymisation of `vehicleId` via salted hash proposed in `PRIVACY_DPDP.md` §3. Currently plain text for demo. | Open — see `PRIVACY_DPDP.md`. |
| A20 | Supply-chain attack on dependencies | Malicious package in `requirements.txt` / `package.json` | Integrity | Build pipeline | `requirements.txt` / `package.json` are pinned; CI has Slither + flake8; no post-install scripts from third parties. | Transitive dependencies not audited. |

## 5. Implemented Mitigations (Mapping to Code)

### 5.1 ECDSA Device Signatures — G1, G3, A1, A4, A5
* Contract: `EmissionRegistry._verifyDeviceSignature` ([contracts/EmissionRegistry.sol:407](../contracts/EmissionRegistry.sol#L407)).
* Device side: `obd_node/obd_device.py` signs `keccak256(vehicleId || co2 || co || nox || hc || pm25 || timestamp || nonce)` with the Ethereum-signed-message prefix.
* Registration: only keys added via `setRegisteredDevice(addr, true)` are accepted.

### 5.2 On-chain CES Recomputation — G5, A2
* `_computeCES` is pure, public, and re-derives the composite score from raw pollutants on every submission ([contracts/EmissionRegistry.sol:194](../contracts/EmissionRegistry.sol#L194)). The station cannot influence it.

### 5.3 Nonce Replay Protection — G4, A4, A12
* `bytes32 _nonce` is bound into the signed payload and checked against `usedNonces[_nonce]`. Each nonce is single-use.

### 5.4 Role-Based Access Control — A1, A6, A7
* `onlyAdmin`, `onlyStation` modifiers in `EmissionRegistry`.
* `onlyAuthority`, `onlyAuthorizedIssuer` in `PUCCertificate`.
* `authorizedMinters` mapping in `GreenToken` — only `PUCCertificate` can mint.

### 5.5 Reentrancy Protection — G1/G5
* Every state-changing external function is `nonReentrant` (OpenZeppelin 4.9.6).

### 5.6 Backend Authentication — A6, A11
* JWT (HS256) for authority endpoints (`/api/certificate/*`, `/api/analytics/fleet`, `/api/fleet/*`, `/api/rto/*`, `/api/notifications`).
* HMAC-based API key (`hmac.compare_digest`) for OBD write endpoints.
* SQLite-backed per-IP rate limiter (`backend/persistence.py`) — survives restarts and multi-process deployments.
* Credentials must be supplied via environment variables; the repository no longer ships default admin credentials (see §7).

### 5.7 Key Protection Plan — A3 (Future Work)
The prototype stores the OBD device private key in `.env` for reproducibility.
For production we recommend:

| Option | Cost / complexity | Security level | Recommendation |
|--------|-------------------|----------------|----------------|
| Microchip **ATECC608A** secure element (I²C) | ~$1/unit + 1 day firmware | Private key never leaves the chip; ECC-P256 signing on-die | Best cost/security for fleet OBD dongles |
| **TPM 2.0** (on automotive SoM) | Built into modern ECUs | Hardware-backed, measured boot, attestation | Natural fit when the OBD module is part of the head unit |
| **ARM TrustZone / OP-TEE** | Software-only if SoC supports it | Isolated Trusted Execution Environment | For Linux-based telematics control units |
| **OS keyring** (libsecret / DPAPI) | Trivial | Software-only; vulnerable to root compromise | Developer machines only |

The system's signature verification path is agnostic to where the key lives;
swapping in a secure element requires only a new `sign(message)` implementation
inside `obd_node/obd_device.py`.

### 5.8 Paginated Reads — A11 (gas DoS)
All collection read functions expose paginated variants (`getRecordsPaginated`,
`getViolationsPaginated`, `getRegisteredVehiclesPaginated`) so that an attacker
cannot force a node OOM by inflating the collection size.

### 5.9 Monitoring — A6, A11
Notification system (`backend/persistence.py`) durably stores:
* Violation events
* Fraud alerts above threshold
* Certificate expiry warnings
* Rate limit trips
This gives operators a forensic trail; in a production deployment it would
feed into SIEM.

## 6. Known Gaps and Future Work

| Gap | Severity | Tracked by |
|-----|----------|------------|
| EIP-712 domain separation (chain-id binding) | Medium — enables A9 cross-chain replay | TODO issue |
| Multi-RPC cross-validation (A8 eclipse defence) | Medium | TODO issue |
| Pseudonymisation of on-chain vehicle IDs | High (GDPR/DPDP) | `PRIVACY_DPDP.md` |
| Admin multisig enforcement | High (A7, A15) | Deployment checklist |
| Per-vehicle digital twin for slow-drift detection (A14) | Low-medium | Paper §6 — future work |
| Formal verification of `_computeCES` arithmetic | Low | Paper §6 |
| Post-quantum signatures on device key | Low (no near-term threat) | Paper §6 |

## 7. Hardening Checklist Before Production

- [ ] Rotate all keys; never reuse prototype keys.
- [ ] Deploy admin contract ownership to a 2-of-3 multisig.
- [ ] Terminate TLS at a hardened reverse proxy (nginx, Caddy).
- [ ] Front the API with a WAF (CloudFlare, AWS WAF) to absorb DDoS.
- [ ] Run ≥ 2 independent RPC providers and cross-validate block hashes.
- [ ] Add Slither + Mythril runs to CI on every PR.
- [ ] Configure log aggregation (Loki / CloudWatch) and alert on rate-limit trips and fraud events.
- [ ] Enable Content-Security-Policy headers on all HTML pages.
- [ ] Remove the default `AUTH_USERNAME` / `AUTH_PASSWORD`; require operators to set them.
- [ ] Annual third-party smart-contract audit.

## 8. References

1. Wood, G. *Ethereum: A Secure Decentralised Generalised Transaction Ledger.* Yellow Paper.
2. OWASP Smart Contract Top 10, 2023.
3. NIST SP 800-57, *Recommendation for Key Management.*
4. Microchip ATECC608A datasheet, 2019.
5. ISO/IEC 11889:2015, *Trusted Platform Module 2.0.*
6. Bhat, A. et al., *Replay Attacks on Blockchain-Based Vehicle Identity Systems*, IEEE VNC 2022.
