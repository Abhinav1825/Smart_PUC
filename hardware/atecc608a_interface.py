"""
Smart PUC — ATECC608A secure-element abstraction
=================================================

Closes the "hardware compatibility future-proofing" goal from the
project scope: v3.2 is a software-only demonstration, but the OBD
signing path needs a clean seam where a real Microchip ATECC608A
secure element can be substituted without touching any higher-layer
code. That seam is ``Atecc608AInterface``.

Why ATECC608A?
--------------
The ATECC608A is the de-facto standard secure element for automotive /
IoT signing applications: it stores a private key inside a tamper-
resistant boundary, exposes ECDSA-P256 signing as a hardware op, and
is cheap (~$0.80/unit in volume). Microchip's CryptoAuthLib provides
a C driver; a thin Python binding (``cryptoauthlib``) is available on
PyPI when the host platform has I2C / SWI access to the chip.

This module does three things:

1. **Defines the interface** (``Atecc608AInterface``) as an abstract
   base class listing the three operations the Smart PUC OBD client
   actually uses: ``get_public_key()``, ``sign_emission_digest()``,
   and ``attest_config()``.
2. **Ships a software stub** (``SoftwareStubAtecc608A``) that
   implements the interface using ``eth_keys`` ECDSA over secp256k1
   so every test, the simulator, and the e2e flow work end-to-end on
   a developer laptop with no physical hardware.
3. **Provides a factory** (``get_default_secure_element``) that picks
   the real driver when the ``SMART_PUC_HARDWARE=atecc608a`` env var is
   set AND the ``cryptoauthlib`` package imports successfully,
   otherwise returns the software stub.

Note on curve choice
--------------------
The on-chain verifier in EmissionRegistry is an EIP-712 ECDSA verifier
on **secp256k1**, which is what the Ethereum toolchain natively
supports. Real ATECC608A hardware supports NIST P-256 (prime256v1)
rather than secp256k1, so a v3.3 follow-up will need one of:

    (a) a second on-chain verifier branch for P-256 signatures, or
    (b) a host-side secp256k1 signing key kept in software with the
        ATECC608A providing attested config + monotonic counters.

For v3.2's software demo we implement (b)-style behaviour in the stub:
the ECDSA signing is done in software with the existing secp256k1 key,
and the ATECC608A seam only wraps the key-retrieval and attestation
hooks. This keeps the demo working today while leaving the research
claim ("hardware attestation is pluggable") honest.

See docs/PAPER_FRAMING.md for the disclosure paragraph that must
accompany any reference to "hardware attestation" in the paper.
"""

from __future__ import annotations

import abc
import hashlib
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AttestationReport:
    """Minimal attestation payload returned by a secure element.

    Real ATECC608A hardware returns a signed blob covering its config
    zone + slot contents; the software stub returns a deterministic
    fake that is still useful for asserting the call site wires data
    through correctly in tests.
    """
    device_serial: str
    config_digest_hex: str
    firmware_version: str
    is_hardware: bool


class Atecc608AInterface(abc.ABC):
    """Abstract interface that the Smart PUC OBD client codes against.

    The intent is that higher-level code (``backend/simulator.py``,
    ``scripts/e2e_business_flow.py``) imports the factory function
    ``get_default_secure_element()`` and never knows whether it is
    talking to real silicon or the software stub.
    """

    # ──────────────────────── Identity ──────────────────────────

    @abc.abstractmethod
    def get_public_key(self) -> bytes:
        """Return the 65-byte uncompressed SECP256k1 public key.

        The on-chain EmissionRegistry's `_verifyDeviceSignature`
        recovers the signer from this key, so the caller uses it to
        register the device via `setRegisteredDevice`.
        """

    @abc.abstractmethod
    def get_address(self) -> str:
        """Return the Ethereum-style 20-byte address derived from the
        public key as a 0x-prefixed hex string."""

    # ──────────────────────── Signing ───────────────────────────

    @abc.abstractmethod
    def sign_emission_digest(self, digest: bytes) -> bytes:
        """Sign a 32-byte EIP-712 digest with the device's private key.

        Returns the 65-byte (r || s || v) signature format that the
        on-chain ECDSA recover expects.
        """

    # ──────────────────────── Attestation ───────────────────────

    @abc.abstractmethod
    def attest_config(self) -> AttestationReport:
        """Return an attestation report describing the secure element's
        current configuration. On real hardware this is the chip's
        locked config zone + slot digest; the stub returns a stable
        fake so tests can round-trip the shape."""


class SoftwareStubAtecc608A(Atecc608AInterface):
    """Software implementation of the secure-element API.

    Wraps an in-memory secp256k1 private key. Intended for tests, the
    software demo, and developer laptops with no physical hardware.
    The API surface is intentionally identical to what a real driver
    would expose so swapping in real hardware requires zero changes at
    the call sites.
    """

    def __init__(self, private_key_hex: Optional[str] = None) -> None:
        # Lazy-import eth_keys/eth_account so this module can still be
        # imported on systems that do not have them (e.g. a CI lane
        # that only exercises the hardware interface surface).
        try:
            from eth_keys import keys  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "eth_keys is required for the SoftwareStubAtecc608A. "
                "Install it via `pip install eth-keys`."
            ) from exc

        if private_key_hex is None:
            # Deterministic test key derived from a well-known seed so
            # every test run produces the same address. Real hardware
            # would generate the key inside the secure element and
            # never expose it.
            seed = hashlib.sha256(b"smart-puc-software-stub-v3.2.2").digest()
            private_key_hex = "0x" + seed.hex()
        if private_key_hex.startswith("0x"):
            private_key_hex = private_key_hex[2:]

        self._pk = keys.PrivateKey(bytes.fromhex(private_key_hex))

    def get_public_key(self) -> bytes:
        # eth_keys returns the 64-byte uncompressed form (no 0x04 prefix).
        return b"\x04" + self._pk.public_key.to_bytes()

    def get_address(self) -> str:
        return self._pk.public_key.to_checksum_address()

    def sign_emission_digest(self, digest: bytes) -> bytes:
        if len(digest) != 32:
            raise ValueError(f"digest must be 32 bytes, got {len(digest)}")
        sig = self._pk.sign_msg_hash(digest)
        # eth_keys Signature has v ∈ {0,1}; Ethereum ECDSA verifiers
        # expect v ∈ {27,28}. Normalise on the way out so the bytes
        # match what the on-chain verifier will accept.
        r = sig.r.to_bytes(32, "big")
        s = sig.s.to_bytes(32, "big")
        v = bytes([sig.v + 27])
        return r + s + v

    def attest_config(self) -> AttestationReport:
        pub_key_digest = hashlib.sha256(self.get_public_key()).hexdigest()
        return AttestationReport(
            device_serial="STUB-" + pub_key_digest[:16].upper(),
            config_digest_hex=pub_key_digest,
            firmware_version="software-stub-1.0",
            is_hardware=False,
        )


def get_default_secure_element(
    private_key_hex: Optional[str] = None,
) -> Atecc608AInterface:
    """Factory returning the appropriate secure-element implementation.

    - When ``SMART_PUC_HARDWARE=atecc608a`` AND the ``cryptoauthlib``
      Python package imports successfully, returns the real hardware
      driver (not implemented in v3.2 — this branch is a placeholder
      that raises NotImplementedError so operators know they need to
      wire the real driver in v3.3).
    - Otherwise returns a ``SoftwareStubAtecc608A`` instance, which is
      the default for the software demonstration.

    The caller should treat the returned object as an opaque
    Atecc608AInterface — never cast back to the concrete class.
    """
    if os.getenv("SMART_PUC_HARDWARE", "").strip().lower() == "atecc608a":
        try:
            import cryptoauthlib  # noqa: F401
        except Exception:  # noqa: BLE001
            # Fall through to the software stub — the operator has
            # requested hardware but the driver is not available. The
            # software stub keeps the demo alive.
            pass
        else:
            raise NotImplementedError(
                "Real ATECC608A driver wiring is deferred to v3.3. "
                "Unset SMART_PUC_HARDWARE or install the v3.3 driver."
            )
    return SoftwareStubAtecc608A(private_key_hex=private_key_hex)
