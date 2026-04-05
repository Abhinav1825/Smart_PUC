"""Tests for backend/ipfs_pinning.py — audit L7 (certificate metadata pinning).

The tests verify three things without ever making a real network call:

1. ``IPFSPinner.from_env()`` respects backend selection and reads the
   API key from the ``IPFS_API_KEY`` env var.
2. ``is_configured()`` is False when no key is set for web3storage /
   pinata (the production default), so ``pin_json`` becomes a no-op.
3. ``build_certificate_metadata()`` produces an ERC-721-shape JSON
   document with the Smart PUC specific attributes the reviewer
   expects.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.ipfs_pinning import IPFSPinner, build_certificate_metadata


def test_from_env_defaults_to_web3storage(monkeypatch):
    monkeypatch.delenv("IPFS_BACKEND", raising=False)
    monkeypatch.delenv("IPFS_API_KEY", raising=False)
    pinner = IPFSPinner.from_env()
    assert pinner.backend == "web3storage"
    assert pinner.api_key is None
    assert pinner.api_url.endswith("/upload")


def test_from_env_selects_pinata(monkeypatch):
    monkeypatch.setenv("IPFS_BACKEND", "pinata")
    monkeypatch.setenv("IPFS_API_KEY", "fake-jwt-token")
    pinner = IPFSPinner.from_env()
    assert pinner.backend == "pinata"
    assert pinner.api_key == "fake-jwt-token"
    assert "pinata" in pinner.api_url


def test_from_env_selects_local(monkeypatch):
    monkeypatch.setenv("IPFS_BACKEND", "local")
    monkeypatch.delenv("IPFS_API_KEY", raising=False)
    pinner = IPFSPinner.from_env()
    assert pinner.backend == "local"
    assert pinner.api_key is None  # local daemon needs no key
    # Local daemon is considered configured even without a key.
    assert pinner.is_configured() is True


def test_is_configured_requires_key_for_web3storage(monkeypatch):
    monkeypatch.delenv("IPFS_API_KEY", raising=False)
    pinner = IPFSPinner(backend="web3storage", api_key=None)
    assert pinner.is_configured() is False


def test_pin_json_is_noop_without_key(monkeypatch):
    """The core zero-cost guarantee: no key → no network call → None."""
    monkeypatch.delenv("IPFS_API_KEY", raising=False)
    pinner = IPFSPinner(backend="web3storage", api_key=None)
    result = pinner.pin_json({"name": "Smart PUC Certificate"})
    assert result is None


def test_build_certificate_metadata_has_erc721_shape():
    meta = build_certificate_metadata(
        vehicle_id="MH12AB1234",
        owner_address="0x" + "a" * 40,
        ces_score=0.8123,
        issued_at=1_700_000_000,
        expires_at=1_700_000_000 + 180 * 86400,
        is_first_puc=False,
        station_address="0x" + "b" * 40,
    )
    # ERC-721 / OpenSea required-ish keys
    assert "name" in meta and "MH12AB1234" in meta["name"]
    assert "description" in meta
    assert "attributes" in meta and isinstance(meta["attributes"], list)
    # Smart PUC specific version tag
    assert meta["smart_puc_version"] == "3.2.2"
    # Attributes contain the reviewer-visible traits.
    trait_types = {a["trait_type"] for a in meta["attributes"]}
    assert {"Vehicle ID", "Owner", "CES Score", "First PUC", "Issuing Station"} <= trait_types


def test_build_certificate_metadata_first_puc_flag():
    meta = build_certificate_metadata(
        vehicle_id="MH12FIRST",
        owner_address="0x" + "c" * 40,
        ces_score=0.65,
        issued_at=1_700_000_000,
        expires_at=1_700_000_000 + 360 * 86400,
        is_first_puc=True,
    )
    first_puc_attr = next(a for a in meta["attributes"] if a["trait_type"] == "First PUC")
    assert first_puc_attr["value"] == "Yes"
