"""
Smart PUC — Blockchain Connector (Web3.py)
============================================
Provides Python ↔ Ethereum interaction for the Smart PUC system.

Responsibilities:
  - Connect to Ganache (local) or Sepolia (testnet) via RPC
  - Load the compiled EmissionContract ABI + address from Truffle build artefacts
  - Expose helper functions for:
      • store_emission()   — write an emission record on-chain
      • get_history()      — read all records for a vehicle
      • get_violations()   — read FAIL records for a vehicle
      • get_record_count() — number of records for a vehicle
      • get_status()       — connection health check
  - Sign transactions using a private key loaded from .env (FR-17, FR-18)

Requires:
  - web3>=6.0  (Web3.py v6 API)
  - python-dotenv
  - Truffle build output at ../build/contracts/EmissionContract.json
"""

import os
import json
import time

from web3 import Web3
from dotenv import load_dotenv

# ──────────────────────────── Configuration ────────────────────────────────────

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# RPC endpoint (default: Ganache localhost)
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:7545")

# Private key for signing transactions (from .env — never hardcoded)
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")

# Contract address — can be set in .env or auto-detected from Truffle build
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "")

# Path to Truffle build artefact
BUILD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "build", "contracts", "EmissionContract.json"
)

# ──────────────────────────── Connector Class ──────────────────────────────────

class BlockchainConnector:
    """
    Web3.py wrapper for interacting with the EmissionContract on Ethereum.
    """

    def __init__(self, rpc_url=None, private_key=None, contract_address=None):
        """
        Initialise the connector.

        Args:
            rpc_url          : Ethereum RPC URL (default from .env or Ganache)
            private_key      : Hex private key for tx signing (from .env)
            contract_address : Deployed contract address (from .env or auto-detect)
        """
        self.rpc_url = rpc_url or RPC_URL
        self.private_key = private_key or PRIVATE_KEY
        self.contract_address = contract_address or CONTRACT_ADDRESS

        # Connect to Ethereum node
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError(
                f"Cannot connect to Ethereum node at {self.rpc_url}. "
                "Make sure Ganache is running."
            )

        # Derive account from private key
        if self.private_key:
            self.account = self.w3.eth.account.from_key(self.private_key)
            self.address = self.account.address
        else:
            # Fallback: use first Ganache account
            self.address = self.w3.eth.accounts[0]
            self.account = None

        # Load contract
        self.contract = self._load_contract()

    def _load_contract(self):
        """Load ABI from Truffle build artefact and return a Contract instance."""
        if not os.path.exists(BUILD_PATH):
            raise FileNotFoundError(
                f"Contract build artefact not found at {BUILD_PATH}. "
                "Run 'truffle compile && truffle migrate' first."
            )

        with open(BUILD_PATH, "r") as f:
            build = json.load(f)

        abi = build["abi"]

        # Auto-detect contract address from Truffle build if not set
        if not self.contract_address:
            networks = build.get("networks", {})
            if networks:
                # Pick the most recent deployment
                latest_network = list(networks.values())[-1]
                self.contract_address = latest_network["address"]
            else:
                raise ValueError(
                    "Contract address not found. Set CONTRACT_ADDRESS in .env "
                    "or run 'truffle migrate'."
                )

        return self.w3.eth.contract(
            address=Web3.to_checksum_address(self.contract_address),
            abi=abi,
        )

    # ───────────────────── Write Operations ────────────────────────────────

    def store_emission(self, vehicle_id, co2_value, timestamp=None):
        """
        Store an emission record on-chain.

        Args:
            vehicle_id : str  — vehicle registration number
            co2_value  : int  — CO₂ in g/km
            timestamp  : int  — Unix epoch (default: now)

        Returns:
            dict: { tx_hash, status, block_number }
        """
        if timestamp is None:
            timestamp = int(time.time())

        # Build transaction
        tx = self.contract.functions.storeEmission(
            vehicle_id, co2_value, timestamp
        ).build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "gas": 500000,
            "gasPrice": self.w3.eth.gas_price,
        })

        # Sign and send
        if self.account:
            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        else:
            # Ganache unlocked account fallback
            tx_hash = self.w3.eth.send_transaction(tx)

        # Wait for receipt
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

        return {
            "tx_hash": receipt.transactionHash.hex(),
            "status": "success" if receipt.status == 1 else "failed",
            "block_number": receipt.blockNumber,
        }

    # ───────────────────── Read Operations ─────────────────────────────────

    def get_history(self, vehicle_id):
        """
        Get all emission records for a vehicle from the blockchain.

        Returns:
            list[dict]: Each dict has keys: vehicleId, co2Level, timestamp, status
        """
        records = self.contract.functions.getAllRecords(vehicle_id).call()
        return [self._parse_record(r) for r in records]

    def get_violations(self, vehicle_id):
        """
        Get all FAIL records for a vehicle.

        Returns:
            list[dict]
        """
        records = self.contract.functions.getViolations(vehicle_id).call()
        return [self._parse_record(r) for r in records]

    def get_record_count(self, vehicle_id):
        """Get total number of records for a vehicle."""
        return self.contract.functions.getRecordCount(vehicle_id).call()

    def get_threshold(self):
        """Get current CO₂ threshold from the contract."""
        return self.contract.functions.threshold().call()

    def get_registered_vehicles(self):
        """Get list of all vehicle IDs with records."""
        return self.contract.functions.getRegisteredVehicles().call()

    # ───────────────────── Status ──────────────────────────────────────────

    def get_status(self):
        """
        Health check: returns connection status, block number, and contract address.
        """
        return {
            "connected": self.w3.is_connected(),
            "block_number": self.w3.eth.block_number,
            "contract_address": self.contract_address,
            "account": self.address,
            "network_id": self.w3.net.version,
        }

    # ───────────────────── Helpers ─────────────────────────────────────────

    @staticmethod
    def _parse_record(record):
        """Convert a Solidity EmissionRecord tuple to a Python dict."""
        return {
            "vehicleId": record[0],
            "co2Level": record[1],
            "timestamp": record[2],
            "status": "PASS" if record[3] else "FAIL",
        }


# ──────────────────────────── Standalone Test ──────────────────────────────────

if __name__ == "__main__":
    try:
        connector = BlockchainConnector()
        status = connector.get_status()
        print("✅ Blockchain Connection Status:")
        for k, v in status.items():
            print(f"   {k}: {v}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
