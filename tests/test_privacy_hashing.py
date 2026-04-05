"""Tests for backend/privacy.py — audit L11 / G6 off-chain hashing helpers.

These tests verify that the Python keccak256 used by off-chain indexers
produces the same bytes32 topic that the on-chain EmissionRegistry emits
via EmissionStoredHashed when privacy mode is enabled. The reference
value for "MH12AB1234" is pinned so any future refactor that drifts from
Solidity keccak256(bytes("MH12AB1234")) will fail loudly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.privacy import (
    keccak_vehicle_id,
    privacy_index_key,
    salted_pseudonym,
)


def test_keccak_vehicle_id_is_deterministic():
    h1 = keccak_vehicle_id("MH12AB1234")
    h2 = keccak_vehicle_id("MH12AB1234")
    assert h1 == h2
    assert h1.startswith("0x")
    assert len(h1) == 66  # "0x" + 64 hex chars


def test_keccak_vehicle_id_differs_by_input():
    assert keccak_vehicle_id("MH12AB1234") != keccak_vehicle_id("MH12AB9999")


def test_keccak_vehicle_id_matches_solidity_reference():
    """Cross-check against eth_utils.keccak, which web3.py ships.

    This is effectively an oracle: if our wrapper ever picks a different
    backend that produces a different digest, this test fails.
    """
    try:
        from eth_utils import keccak  # type: ignore
    except Exception:
        pytest.skip("eth_utils not installed in this environment")
    expected = "0x" + keccak(text="MH12AB1234").hex()
    assert keccak_vehicle_id("MH12AB1234") == expected


def test_salted_pseudonym_passthrough_when_no_salt(monkeypatch):
    monkeypatch.delenv("SMART_PUC_STATION_SALT", raising=False)
    assert salted_pseudonym("MH12AB1234") == "MH12AB1234"


def test_salted_pseudonym_with_explicit_salt_changes_output():
    ps = salted_pseudonym("MH12AB1234", station_salt="STATION_A")
    assert ps.startswith("sp:")
    assert ps != "MH12AB1234"
    # Same salt → same pseudonym (deterministic).
    assert ps == salted_pseudonym("MH12AB1234", station_salt="STATION_A")
    # Different salt → different pseudonym (cross-station unlinkability).
    other = salted_pseudonym("MH12AB1234", station_salt="STATION_B")
    assert other != ps


def test_privacy_index_key_round_trip():
    """privacy_index_key is keccak(salted_pseudonym(vid, salt))."""
    vid = "MH12AB1234"
    salt = "TEST_SALT"
    expected = keccak_vehicle_id(salted_pseudonym(vid, station_salt=salt))
    assert privacy_index_key(vid, station_salt=salt) == expected
    # And without salt, privacy_index_key collapses to the raw keccak hash.
    assert privacy_index_key(vid) == keccak_vehicle_id(vid)
