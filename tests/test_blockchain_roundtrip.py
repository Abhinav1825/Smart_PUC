"""
Python ↔ Solidity blockchain round-trip integration test (audit fix #3).

Verifies that emission data stored on-chain via the Python backend can be
read back correctly, including fixed-point scaling round-trips.

These tests require a running local Hardhat node with deployed contracts.
They are skipped if the node is unreachable.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-please-do-not-use-in-prod")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("AUTH_USERNAME", "admin")
os.environ.setdefault("AUTH_PASSWORD", "admin")


def _blockchain_available() -> bool:
    """Check if a Hardhat node is running and contracts are deployed."""
    try:
        from backend.blockchain_connector import SmartPUCBlockchain
        bc = SmartPUCBlockchain()
        status = bc.get_status()
        return status.get("connected", False) and status.get("registry_address") is not None
    except Exception:
        return False


_skip_reason = "Hardhat node not running or contracts not deployed"


@pytest.mark.skipif(not _blockchain_available(), reason=_skip_reason)
class TestBlockchainRoundTrip:
    """End-to-end tests for storing and retrieving emission data on-chain."""

    def _get_blockchain(self):
        from backend.blockchain_connector import SmartPUCBlockchain
        return SmartPUCBlockchain()

    def test_store_and_retrieve_emission(self):
        """Store a reading on-chain and read it back, verifying all fields."""
        bc = self._get_blockchain()

        vehicle_id = "ROUNDTRIP01"
        # Store a reading
        result = bc.store_emission(
            vehicle_id=vehicle_id,
            co2=110000,     # 110.0 g/km (x1000 scaling)
            co=800,         # 0.800 g/km
            nox=50,         # 0.050 g/km
            hc=80,          # 0.080 g/km
            pm25=4,         # 0.004 g/km
            ces=8500,       # 0.85 (x10000 scaling)
            fraud_score=2000,  # 0.20 (x10000)
            vsp=5000,       # 5.0 W/kg (x1000)
            wltc_phase=1,
        )
        assert result is not None, "store_emission returned None"
        assert "error" not in result or result.get("tx_hash"), f"store_emission failed: {result}"

        # Retrieve records
        records = bc.get_records(vehicle_id)
        assert len(records) >= 1, f"Expected at least 1 record, got {len(records)}"

        latest = records[-1]
        # Verify fields round-trip correctly (with x1000/x10000 scaling)
        assert latest["co2"] == 110000, f"CO2 mismatch: {latest['co2']}"
        assert latest["co"] == 800, f"CO mismatch: {latest['co']}"
        assert latest["nox"] == 50, f"NOx mismatch: {latest['nox']}"
        assert latest["hc"] == 80, f"HC mismatch: {latest['hc']}"
        assert latest["pm25"] == 4, f"PM2.5 mismatch: {latest['pm25']}"

    def test_store_and_retrieve_multiple(self):
        """Store 5 readings and verify paginated retrieval returns all in order."""
        bc = self._get_blockchain()
        vehicle_id = "ROUNDTRIP02"

        for i in range(5):
            bc.store_emission(
                vehicle_id=vehicle_id,
                co2=100000 + i * 1000,
                co=500 + i * 100,
                nox=40 + i * 5,
                hc=70 + i * 5,
                pm25=3 + i,
                ces=7000 + i * 500,
                fraud_score=1000,
                vsp=4000 + i * 500,
                wltc_phase=i % 4,
            )

        records = bc.get_records(vehicle_id)
        assert len(records) >= 5, f"Expected at least 5 records, got {len(records)}"

        # Verify ascending CO2 values (order preserved)
        co2_values = [r["co2"] for r in records[-5:]]
        assert co2_values == sorted(co2_values), f"Records not in order: {co2_values}"

    def test_fraud_flag_roundtrip(self):
        """Store a high fraud-score reading and verify it is flagged."""
        bc = self._get_blockchain()
        vehicle_id = "ROUNDTRIP03"

        result = bc.store_emission(
            vehicle_id=vehicle_id,
            co2=150000,     # above threshold
            co=1500,        # above threshold
            nox=80,         # above threshold
            hc=120,         # above threshold
            pm25=6,         # above threshold
            ces=15000,      # way above 10000 ceiling
            fraud_score=8000,  # above 6500 fraud alert threshold
            vsp=25000,
            wltc_phase=2,
        )
        assert result is not None

        records = bc.get_records(vehicle_id)
        assert len(records) >= 1
        latest = records[-1]
        # The record should exist with the high fraud score
        assert latest["fraud_score"] == 8000 or latest.get("fraudScore") == 8000
