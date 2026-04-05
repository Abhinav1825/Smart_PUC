"""
Smart PUC — Multi-Contract Blockchain Connector (3-Node Architecture)
=====================================================================
Python <-> Ethereum interface for the 3-contract system:
  - EmissionRegistry: stores emission records with device signatures
  - PUCCertificate: issues/verifies NFT certificates
  - GreenToken: manages reward tokens

Supports the 3-node trust model where:
  - OBD Device (Node 1) signs telemetry data
  - Testing Station (Node 2) submits to blockchain
  - Verification Portal (Node 3) reads from blockchain

References:
    ARAI BSVI Notification, MoRTH India (2020)
    US EPA MOVES3 Technical Report (2020)
"""

import os
import json
import time
from typing import Optional

from web3 import Web3
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:7545")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")  # Testing Station key (account[1])

BUILD_DIR = os.path.join(os.path.dirname(__file__), "..", "build", "contracts")

SCALE_POLLUTANT = 1000
SCALE_SCORE = 10000

WLTC_PHASES = {0: "Low", 1: "Medium", 2: "High", 3: "Extra High"}


class BlockchainConnector:
    """
    Multi-contract Web3.py connector for the Smart PUC 3-node architecture.

    Manages connections to:
      - EmissionRegistry (read/write emission records)
      - PUCCertificate (issue/verify NFT certificates)
      - GreenToken (check reward balances, redeem tokens)
    """

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
    ):
        self.rpc_url = rpc_url or RPC_URL
        self.private_key = private_key or PRIVATE_KEY

        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError(
                f"Cannot connect to Ethereum node at {self.rpc_url}. "
                "Make sure Ganache is running."
            )

        if self.private_key:
            self.account = self.w3.eth.account.from_key(self.private_key)
            self.address = self.account.address
        else:
            self.address = self.w3.eth.accounts[0]
            self.account = None

        # Load all 3 contracts
        self.registry = self._load_contract("EmissionRegistry")
        self.puc_cert = self._load_contract("PUCCertificate")
        self.green_token = self._load_contract("GreenToken")

        # Store addresses for status reporting
        self.registry_address = self.registry.address if self.registry else None
        self.puc_cert_address = self.puc_cert.address if self.puc_cert else None
        self.green_token_address = self.green_token.address if self.green_token else None

    def _load_contract(self, name: str):
        """Load a contract from Truffle build artifacts."""
        build_path = os.path.join(BUILD_DIR, f"{name}.json")
        if not os.path.exists(build_path):
            print(f"  Warning: {name}.json not found at {build_path}")
            return None

        with open(build_path, "r") as f:
            build = json.load(f)

        abi = build["abi"]
        networks = build.get("networks", {})

        if not networks:
            print(f"  Warning: {name} not deployed (no network entries)")
            return None

        latest_network = list(networks.values())[-1]
        address = latest_network["address"]

        return self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=abi,
        )

    def _send_tx(self, tx_func, gas=800000):
        """Build, sign, and send a transaction."""
        tx = tx_func.build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "gas": gas,
            "gasPrice": self.w3.eth.gas_price,
        })

        if self.account:
            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            raw_tx = getattr(signed, 'raw_transaction', None) or getattr(signed, 'rawTransaction', None)
            tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
        else:
            tx_hash = self.w3.eth.send_transaction(tx)

        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return {
            "tx_hash": receipt.transactionHash.hex(),
            "status": "success" if receipt.status == 1 else "failed",
            "block_number": receipt.blockNumber,
            "gas_used": receipt.gasUsed,
        }

    # ─────────────────── Nonce Generation ────────────────────────────

    def _generate_nonce(self, vehicle_id: str, timestamp: int) -> bytes:
        """
        Generate a unique bytes32 nonce for an emission record.

        Combines vehicle_id, timestamp, and 16 bytes of OS randomness,
        then hashes with Keccak-256 to produce a deterministic-length
        32-byte value suitable for the contract's _nonce parameter.
        """
        return Web3.keccak(text=f"{vehicle_id}{timestamp}{os.urandom(16).hex()}")

    # ─────────────────── EmissionRegistry Operations ──────────────────

    def store_emission(
        self,
        vehicle_id: str,
        co2: float,
        co: float = 0.0,
        nox: float = 0.0,
        hc: float = 0.0,
        pm25: float = 0.0,
        fraud_score: float = 0.0,
        vsp: float = 0.0,
        wltc_phase: int = 0,
        timestamp: Optional[int] = None,
        device_signature: bytes = b"",
    ) -> dict:
        """
        Store a multi-pollutant emission record with device signature.

        CES is now computed on-chain by the EmissionRegistry contract,
        so it is no longer passed as a parameter. A unique nonce (bytes32)
        is generated automatically for each submission.

        Args:
            vehicle_id:       Vehicle registration number
            co2-pm25:         Pollutant values in g/km
            fraud_score:      Fraud detection score (0.0-1.0)
            vsp:              Vehicle Specific Power in W/kg
            wltc_phase:       WLTC phase (0-3)
            timestamp:        Unix epoch (default: now)
            device_signature: ECDSA signature from OBD device

        Returns:
            dict: { tx_hash, status, block_number, gas_used }
        """
        if timestamp is None:
            timestamp = int(time.time())

        # Scale to Solidity integers
        co2_s = int(round(co2 * SCALE_POLLUTANT))
        co_s = int(round(co * SCALE_POLLUTANT))
        nox_s = int(round(nox * SCALE_POLLUTANT))
        hc_s = int(round(hc * SCALE_POLLUTANT))
        pm25_s = int(round(pm25 * SCALE_POLLUTANT))
        fraud_s = int(round(fraud_score * SCALE_SCORE))
        vsp_s = int(round(vsp * SCALE_POLLUTANT))
        phase = min(max(int(wltc_phase), 0), 3)

        # Generate unique nonce for this submission
        nonce = self._generate_nonce(vehicle_id, timestamp)

        # Ensure device_signature is bytes
        if isinstance(device_signature, str):
            device_signature = bytes.fromhex(device_signature.replace("0x", ""))

        tx_func = self.registry.functions.storeEmission(
            vehicle_id,
            co2_s, co_s, nox_s, hc_s, pm25_s,
            fraud_s, vsp_s,
            phase, timestamp,
            nonce,
            device_signature,
        )

        return self._send_tx(tx_func, gas=1000000)

    def compute_ces(
        self,
        co2: float,
        co: float = 0.0,
        nox: float = 0.0,
        hc: float = 0.0,
        pm25: float = 0.0,
    ) -> float:
        """
        Call the on-chain computeCES function to get the Composite
        Emission Score for the given pollutant values.

        Args:
            co2-pm25: Pollutant values in g/km

        Returns:
            float: CES value (descaled from SCALE_SCORE)
        """
        co2_s = int(round(co2 * SCALE_POLLUTANT))
        co_s = int(round(co * SCALE_POLLUTANT))
        nox_s = int(round(nox * SCALE_POLLUTANT))
        hc_s = int(round(hc * SCALE_POLLUTANT))
        pm25_s = int(round(pm25 * SCALE_POLLUTANT))

        raw = self.registry.functions.computeCES(
            co2_s, co_s, nox_s, hc_s, pm25_s,
        ).call()

        return raw / SCALE_SCORE

    def get_history(self, vehicle_id: str) -> list:
        """Get all emission records for a vehicle."""
        records = self.registry.functions.getAllRecords(vehicle_id).call()
        return [self._parse_record(r) for r in records]

    def get_history_paginated(self, vehicle_id: str, offset: int = 0, limit: int = 20) -> list:
        """Get paginated emission records for a vehicle (gas-safe)."""
        records = self.registry.functions.getRecordsPaginated(vehicle_id, offset, limit).call()
        return [self._parse_record(r) for r in records]

    def get_violations(self, vehicle_id: str) -> list:
        """Get all FAIL records for a vehicle."""
        records = self.registry.functions.getViolations(vehicle_id).call()
        return [self._parse_record(r) for r in records]

    def get_violation_count(self, vehicle_id: str) -> int:
        """Get the total number of violations for a vehicle."""
        return self.registry.functions.getViolationCount(vehicle_id).call()

    def get_violations_paginated(self, vehicle_id: str, offset: int = 0, limit: int = 20) -> list:
        """Get paginated violation records for a vehicle (gas-safe)."""
        records = self.registry.functions.getViolationsPaginated(vehicle_id, offset, limit).call()
        return [self._parse_record(r) for r in records]

    def get_record_count(self, vehicle_id: str) -> int:
        """Get total number of records for a vehicle."""
        return self.registry.functions.getRecordCount(vehicle_id).call()

    def get_registered_vehicles(self) -> list:
        """Get list of all vehicle IDs with records."""
        return self.registry.functions.getRegisteredVehicles().call()

    def get_vehicle_stats(self, vehicle_id: str) -> dict:
        """Get aggregated stats for a vehicle."""
        try:
            result = self.registry.functions.getVehicleStats(vehicle_id).call()
            return {
                "total_records": result[0],
                "violations": result[1],
                "fraud_alerts": result[2],
                "avg_ces": result[3] / SCALE_SCORE if result[0] > 0 else 0.0,
            }
        except Exception:
            return {"total_records": 0, "violations": 0, "fraud_alerts": 0, "avg_ces": 0.0}

    def is_certificate_eligible(self, vehicle_id: str) -> dict:
        """Check if a vehicle is eligible for a PUC certificate."""
        eligible, passes = self.registry.functions.isCertificateEligible(vehicle_id).call()
        return {"eligible": eligible, "consecutive_passes": passes}

    # ─────────────────── PUCCertificate Operations ────────────────────

    def issue_certificate(
        self,
        vehicle_id: str,
        vehicle_owner: str,
        metadata_uri: Optional[str] = None,
    ) -> dict:
        """
        Issue a PUC certificate NFT for a vehicle.

        Args:
            vehicle_id:    Vehicle registration number
            vehicle_owner: Wallet address of the vehicle owner
            metadata_uri:  Optional token metadata URI (IPFS or HTTP)

        Returns:
            dict: { tx_hash, status, block_number, gas_used }
        """
        owner_addr = Web3.to_checksum_address(vehicle_owner)

        if metadata_uri is not None:
            tx_func = self.puc_cert.functions.issueCertificate(
                vehicle_id,
                owner_addr,
                metadata_uri,
            )
        else:
            tx_func = self.puc_cert.functions.issueCertificate(
                vehicle_id,
                owner_addr,
            )

        return self._send_tx(tx_func, gas=500000)

    def set_token_uri(self, token_id: int, uri: str) -> dict:
        """Set the metadata URI for a specific certificate token."""
        tx_func = self.puc_cert.functions.setTokenURI(token_id, uri)
        return self._send_tx(tx_func)

    def set_base_uri(self, uri: str) -> dict:
        """Set the base URI for all certificate token metadata."""
        tx_func = self.puc_cert.functions.setBaseURI(uri)
        return self._send_tx(tx_func)

    def check_certificate(self, vehicle_id: str) -> dict:
        """Check if a vehicle has a valid PUC certificate."""
        try:
            valid, token_id, expiry = self.puc_cert.functions.isValid(vehicle_id).call()
            cert_data = None
            if token_id > 0:
                raw = self.puc_cert.functions.getCertificate(token_id).call()
                cert_data = {
                    "vehicleId": raw[0],
                    "vehicleOwner": raw[1],
                    "issueTimestamp": raw[2],
                    "expiryTimestamp": raw[3],
                    "averageCES": raw[4] / SCALE_SCORE,
                    "totalRecordsAtIssue": raw[5],
                    "issuedByStation": raw[6],
                    "revoked": raw[7],
                    "revokeReason": raw[8],
                }
            return {
                "valid": valid,
                "token_id": token_id,
                "expiry_timestamp": expiry,
                "certificate": cert_data,
            }
        except Exception:
            return {"valid": False, "token_id": 0, "expiry_timestamp": 0, "certificate": None}

    def get_verification_data(self, vehicle_id: str) -> dict:
        """Get certificate verification data (for QR code / public portal)."""
        try:
            result = self.puc_cert.functions.getVerificationData(vehicle_id).call()
            return {
                "valid": result[0],
                "token_id": result[1],
                "vehicle_id": result[2],
                "vehicle_owner": result[3],
                "issue_date": result[4],
                "expiry_date": result[5],
                "average_ces": result[6] / SCALE_SCORE if result[6] > 0 else 0.0,
                "revoked": result[7],
            }
        except Exception:
            return {
                "valid": False, "token_id": 0, "vehicle_id": vehicle_id,
                "vehicle_owner": "0x0", "issue_date": 0, "expiry_date": 0,
                "average_ces": 0.0, "revoked": False,
            }

    def revoke_certificate(self, token_id: int, reason: str) -> dict:
        """Revoke a PUC certificate (authority only)."""
        tx_func = self.puc_cert.functions.revokeCertificate(token_id, reason)
        return self._send_tx(tx_func)

    # ─────────────────── GreenToken Operations ────────────────────────

    def get_green_token_balance(self, address: str) -> dict:
        """Get Green Credit Token balance for an address."""
        try:
            addr = Web3.to_checksum_address(address)
            balance, earned = self.green_token.functions.getRewardSummary(addr).call()
            return {
                "balance": balance / 10**18,
                "earned": earned / 10**18,
                "balance_wei": balance,
            }
        except Exception:
            return {"balance": 0.0, "earned": 0.0, "balance_wei": 0}

    def redeem_tokens(self, reward_type: int) -> dict:
        """
        Burn GreenTokens to redeem a reward.

        Args:
            reward_type: Reward type ID (uint8) defined in the contract

        Returns:
            dict: { tx_hash, status, block_number, gas_used }
        """
        tx_func = self.green_token.functions.redeem(reward_type)
        return self._send_tx(tx_func)

    def get_reward_cost(self, reward_type: int) -> int:
        """
        Get the token cost for a given reward type.

        Args:
            reward_type: Reward type ID (uint8)

        Returns:
            int: Cost in token wei units
        """
        return self.green_token.functions.getRewardCost(reward_type).call()

    def get_redemption(self, redemption_id: int) -> dict:
        """
        Get details of a specific redemption by ID.

        Args:
            redemption_id: On-chain redemption ID

        Returns:
            dict: Redemption record fields
        """
        raw = self.green_token.functions.getRedemption(redemption_id).call()
        return {
            "id": raw[0],
            "redeemer": raw[1],
            "rewardType": raw[2],
            "cost": raw[3],
            "timestamp": raw[4],
        }

    def get_redemption_stats(self, address: str) -> dict:
        """
        Get aggregated redemption statistics for an address.

        Args:
            address: Wallet address to query

        Returns:
            dict: Redemption stats from the contract
        """
        addr = Web3.to_checksum_address(address)
        raw = self.green_token.functions.getRedemptionStats(addr).call()
        return {
            "total_redemptions": raw[0],
            "total_spent": raw[1],
        }

    # ─────────────────── Status ───────────────────────────────────────

    def get_status(self) -> dict:
        """Health check: connection status, contract addresses, account info."""
        return {
            "connected": self.w3.is_connected(),
            "block_number": self.w3.eth.block_number,
            "registry_address": self.registry_address,
            "puc_cert_address": self.puc_cert_address,
            "green_token_address": self.green_token_address,
            "account": self.address,
            "network_id": self.w3.net.version,
        }

    # ─────────────────── Helpers ──────────────────────────────────────

    @staticmethod
    def _parse_record(record) -> dict:
        """Convert a Solidity EmissionRecord tuple to a Python dict."""
        return {
            "vehicleId": record[0],
            "co2Level": record[1],
            "coLevel": record[2],
            "noxLevel": record[3],
            "hcLevel": record[4],
            "pm25Level": record[5],
            "cesScore": record[6],
            "fraudScore": record[7],
            "vspValue": record[8],
            "wltcPhase": record[9],
            "timestamp": record[10],
            "status": "PASS" if record[11] else "FAIL",
            "deviceAddress": record[12],
            "stationAddress": record[13],
        }


if __name__ == "__main__":
    try:
        connector = BlockchainConnector()
        status = connector.get_status()
        print("Blockchain Connection Status:")
        for k, v in status.items():
            print(f"   {k}: {v}")
    except Exception as e:
        print(f"Connection failed: {e}")
