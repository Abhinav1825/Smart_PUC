# Smart PUC — Privacy Model (v3.2.2)

This document closes audit items **L11** and **G6** on the privacy
threat model of the EmissionRegistry contract.

## What the plaintext events leak

Before v3.2.2, every call to `storeEmission` emitted the following
events carrying the vehicle registration number verbatim in an indexed
topic, which is visible to any node operator or block explorer:

- `RecordStored(string indexed vehicleId, ...)`
- `ViolationDetected(string indexed vehicleId, ...)`
- `FraudDetected(string indexed vehicleId, ...)`
- `CertificateEligible(string indexed vehicleId, ...)`
- `PhaseCompleted(string indexed vehicleId, ...)`
- `BatchRootCommitted(string indexed vehicleId, ...)`

For a public chain this means the full driving profile of every vehicle
(when it was tested, whether it passed, how often it failed, which
pollutants violated which threshold) can be reconstructed by anyone.

## The v3.2.2 fix (opt-in, non-breaking)

We deliberately do **not** remove the plaintext events, because doing
so would be a breaking change for every integrator that listens for
them (the frontend event pane, `scripts/e2e_business_flow.py`, the
`phase_listener` projection, third-party indexers, etc.). Instead we
add a privacy-preserving twin channel:

1. **`bool public privacyMode`** — admin-toggled flag stored in
   contract state, default `false`.
2. **`setPrivacyMode(bool)`** — admin-only setter that emits
   `PrivacyModeSet(bool)`.
3. **`EmissionStoredHashed(bytes32 indexed vehicleIdHash, ...)`** —
   twin event emitted by `storeEmission` only when privacy mode is on.
   The indexed topic is `keccak256(bytes(vehicleId))`, never the
   plaintext string.
4. **`computeVehicleIdHash(string) external pure returns (bytes32)`** —
   public helper that off-chain indexers call to reproduce the exact
   topic the event will carry.

When privacy mode is **off** (the default), nothing changes. When
privacy mode is **on**, integrators who care about privacy can
subscribe to `EmissionStoredHashed` only and ignore the plaintext
events entirely.

## Off-chain companion: station salt

Keccak alone does not fully anonymise a vehicle id — an attacker who
knows or guesses the registration plate space (which is small: the
MH12 prefix plus four digits plus two letters ≈ 2^26 possibilities)
can brute-force the hash offline. To resist this we add a station-side
salt applied **before** the vehicle id is hashed:

```python
from backend.privacy import privacy_index_key

# STATION_A salt is never committed to chain; it is held by the station
# operator's HSM / ATECC608A and loaded via the SMART_PUC_STATION_SALT
# environment variable.
index = privacy_index_key("MH12AB1234", station_salt="STATION_A")
```

Because the salt is per-station, two stations cannot link their
pseudonymised logs for the same vehicle without colluding to share the
salt. This gives us the "cross-station unlinkability" property
discussed in §V of the paper.

## What is still open for v3.3

- **Full replacement** of the plaintext events would remove the subtle
  timing correlation between the two event streams. This is a breaking
  change and is deferred to v3.3.
- **Private-key aggregation** (commit-reveal of certificate issuance)
  remains future work; see the "Novelty claim ladder" in
  `docs/PAPER_FRAMING.md`.

## Reviewer note

The paper should position v3.2.2 privacy mode as a **pragmatic defence
in depth**, not as a formal privacy guarantee. It raises the cost of
passive observation — an attacker must now obtain station salts — but
it does not hide the presence of an event (count-based inference is
still possible) or hide the station-to-vehicle mapping when the station
salt is compromised.
