"""Tests for hardware/atecc608a_interface.py — audit "hardware compat seam".

These tests verify:
1. The software stub satisfies the Atecc608AInterface contract.
2. Signatures round-trip through eth_account recover, so a real
   on-chain ECDSA verifier would accept them.
3. The factory picks the stub by default and only attempts the real
   driver when SMART_PUC_HARDWARE is set.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hardware import (
    Atecc608AInterface,
    SoftwareStubAtecc608A,
    get_default_secure_element,
)


def test_stub_implements_interface():
    stub = SoftwareStubAtecc608A()
    assert isinstance(stub, Atecc608AInterface)


def test_stub_public_key_and_address_are_deterministic():
    a = SoftwareStubAtecc608A()
    b = SoftwareStubAtecc608A()
    assert a.get_public_key() == b.get_public_key()
    assert a.get_address() == b.get_address()
    # Address is 42 chars (0x + 40 hex).
    addr = a.get_address()
    assert addr.startswith("0x") and len(addr) == 42


def test_stub_sign_digest_length_and_v_normalisation():
    stub = SoftwareStubAtecc608A()
    digest = b"\x11" * 32
    sig = stub.sign_emission_digest(digest)
    assert len(sig) == 65
    v = sig[-1]
    assert v in (27, 28), f"v must be normalised to 27/28, got {v}"


def test_stub_signature_round_trips_via_eth_account():
    """If the signature recovers to the stub's own address via
    eth_account, then an on-chain ECDSA recover would accept it too."""
    try:
        from eth_account._utils.signing import to_standard_v
        from eth_keys import keys
    except Exception:
        pytest.skip("eth_keys not available in this environment")

    stub = SoftwareStubAtecc608A()
    digest = b"\x42" * 32
    sig_bytes = stub.sign_emission_digest(digest)
    r = int.from_bytes(sig_bytes[:32], "big")
    s = int.from_bytes(sig_bytes[32:64], "big")
    v = sig_bytes[64]
    sig = keys.Signature(vrs=(to_standard_v(v), r, s))
    recovered_pub = sig.recover_public_key_from_msg_hash(digest)
    assert recovered_pub.to_checksum_address() == stub.get_address()


def test_stub_sign_digest_rejects_wrong_length():
    stub = SoftwareStubAtecc608A()
    with pytest.raises(ValueError, match="32 bytes"):
        stub.sign_emission_digest(b"\x00" * 31)


def test_stub_attestation_report_shape():
    stub = SoftwareStubAtecc608A()
    report = stub.attest_config()
    assert report.is_hardware is False
    assert report.device_serial.startswith("STUB-")
    assert len(report.config_digest_hex) == 64  # sha256 hex length
    assert report.firmware_version == "software-stub-1.0"


def test_factory_returns_stub_by_default(monkeypatch):
    monkeypatch.delenv("SMART_PUC_HARDWARE", raising=False)
    se = get_default_secure_element()
    assert isinstance(se, SoftwareStubAtecc608A)


def test_factory_falls_back_to_stub_when_real_driver_missing(monkeypatch):
    """Setting SMART_PUC_HARDWARE=atecc608a without cryptoauthlib
    installed must gracefully fall back to the software stub so the
    software demo never breaks on a misconfigured env var."""
    monkeypatch.setenv("SMART_PUC_HARDWARE", "atecc608a")
    # cryptoauthlib is not installed in the test env, so the factory
    # should degrade gracefully to the software stub (not raise).
    se = get_default_secure_element()
    assert isinstance(se, SoftwareStubAtecc608A)
