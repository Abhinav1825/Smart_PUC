"""
Smart PUC — Privacy-preserving vehicle id hashing helpers
==========================================================

Closes audit items L11 / G6 (salted-hash vehicleId) at the *off-chain*
layer. The on-chain EmissionRegistry exposes:

    - ``privacyMode``         (bool, admin-settable, defaults to false)
    - ``setPrivacyMode(bool)``
    - ``computeVehicleIdHash(string)`` — public pure helper
    - ``EmissionStoredHashed(bytes32 indexed vehicleIdHash, ...)`` event
      additionally emitted by storeEmission when privacy mode is enabled.

This module is the canonical Python implementation of the same hash so
that off-chain indexers (phase_listener, analytics dashboards, fraud
detection telemetry) produce identical keys to the on-chain events.

Design notes
------------
The v3.2.2 privacy mode is INTENTIONALLY opt-in and non-breaking:

1. Plaintext event topics (RecordStored, ViolationDetected, etc.) are
   preserved in full, so existing frontends and indexers continue to
   work without modification.
2. When the admin opts in, every storeEmission additionally emits a
   twin event (EmissionStoredHashed) whose indexed topic is the Keccak
   hash of the vehicle id. Off-chain consumers that need privacy-
   preserving logs can subscribe to ONLY the hashed event and ignore
   the plaintext one.
3. A station-side salt — never exposed on-chain — can be prepended to
   the vehicle id before hashing, producing a station-specific pseudonym
   that resists cross-station correlation attacks.

For a richer threat-model discussion see ``docs/PRIVACY_MODEL.md``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional


def keccak_vehicle_id(vehicle_id: str) -> str:
    """Compute keccak256(bytes(vehicle_id)) as a 0x-prefixed hex string.

    This exactly matches the on-chain helper
    ``EmissionRegistry.computeVehicleIdHash(string)`` so that off-chain
    indexers can key their tables by the same value the EVM event topic
    carries.

    Args:
        vehicle_id: The registration number (e.g. "MH12AB1234") or
            a station-salted pseudonym produced by :func:`salted_pseudonym`.

    Returns:
        Hex string of length 66 ("0x" + 64 hex chars).
    """
    if not isinstance(vehicle_id, str):
        raise TypeError("vehicle_id must be a string")
    # Prefer the pysha3 / eth_utils keccak if available (they are shipped
    # transitively via web3), but fall back to a pure-python implementation
    # so this module has no hard dependencies.
    try:  # pragma: no cover — import path depends on environment
        from eth_utils import keccak  # type: ignore
        return "0x" + keccak(text=vehicle_id).hex()
    except Exception:  # noqa: BLE001
        pass
    try:  # pragma: no cover
        import sha3  # type: ignore
        k = sha3.keccak_256()
        k.update(vehicle_id.encode("utf-8"))
        return "0x" + k.hexdigest()
    except Exception:  # noqa: BLE001
        pass
    # Last-resort: pycryptodome's keccak (the pytest venv ships it via
    # web3's dependency tree).
    try:  # pragma: no cover
        from Crypto.Hash import keccak as _keccak  # type: ignore
        k = _keccak.new(digest_bits=256)
        k.update(vehicle_id.encode("utf-8"))
        return "0x" + k.hexdigest()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "No keccak256 implementation available. Install one of: "
            "eth-utils, pysha3, or pycryptodome."
        ) from exc


def salted_pseudonym(vehicle_id: str, station_salt: Optional[str] = None) -> str:
    """Build a station-specific pseudonym for a vehicle id.

    The returned string is itself a valid input to :func:`keccak_vehicle_id`
    or to the on-chain ``computeVehicleIdHash`` helper. Two different
    stations using different salts will produce different keccak hashes
    for the same underlying vehicle, defeating cross-station correlation
    even if an attacker obtains all on-chain event topics.

    Args:
        vehicle_id: Raw registration number.
        station_salt: Per-station secret salt. If ``None`` the value is
            read from the ``SMART_PUC_STATION_SALT`` environment variable.
            If still unset the function returns the raw vehicle_id (no
            pseudonymisation), which matches the default / backward-
            compatible behaviour.

    Returns:
        Either the raw vehicle_id (no salt configured) or a string of
        the form ``"sp:<HMAC-SHA256-hex>"`` suitable for feeding into
        :func:`keccak_vehicle_id`.
    """
    salt = station_salt if station_salt is not None else os.getenv("SMART_PUC_STATION_SALT", "")
    if not salt:
        return vehicle_id
    mac = hmac.new(salt.encode("utf-8"), vehicle_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sp:{mac}"


def privacy_index_key(vehicle_id: str, station_salt: Optional[str] = None) -> str:
    """Convenience wrapper: salt the vehicle id then keccak-hash it.

    This is the single call most off-chain indexers want — given a raw
    registration number and an optional station salt, it produces the
    exact bytes32 topic that the EmissionStoredHashed event will carry
    when the station configures the same salt on both sides.
    """
    return keccak_vehicle_id(salted_pseudonym(vehicle_id, station_salt))


__all__ = ["keccak_vehicle_id", "salted_pseudonym", "privacy_index_key"]
