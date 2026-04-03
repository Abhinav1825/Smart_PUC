"""
Smart PUC — Blockchain Connector (Web3.py) — Multi-Pollutant Version
======================================================================
Provides Python <-> Ethereum interaction for the Smart PUC system.

Supports the upgraded EmissionContract with 5 BSVI pollutants (CO2, CO,
NOx, HC, PM2.5), Composite Emission Score (CES), fraud detection score,
VSP values, and WLTC driving phases.

Responsibilities:
  - Connect to Ganache (local) or Sepolia (testnet) via RPC
  - Load compiled EmissionContract ABI + address from Truffle build artefacts
  - Expose helper functions for multi-pollutant emission storage and retrieval

References:
    ARAI BSVI Notification, MoRTH India (2020) — emission thresholds
    US EPA MOVES3 Technical Report (2020) — operating mode framework

Requires:
  - web3>=6.0 (Web3.py v6 API)
  - python-dotenv
  - Truffle build output at ../build/contracts/EmissionContract.json
"""

import os
import json
import time
from typing import Optional

from web3 import Web3
from dotenv import load_dotenv

# ────────────────────────── Configuration ────────────────────────────────

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:7545")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "")

BUILD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "build", "contracts", "EmissionContract.json"
)

# Scaling factors matching the Solidity contract
SCALE_3 = 1000       # 3 decimal places (CO2, CO, HC, VSP)
SCALE_5 = 100000     # 5 decimal places (PM2.5)
SCALE_4 = 10000      # 4 decimal places (CES, fraud score, NOx)

# WLTC phase mapping
WLTC_PHASES = {0: "Low", 1: "Medium", 2: "High", 3: "Extra High"}


class BlockchainConnector:
    """
    Web3.py wrapper for interacting with the upgraded multi-pollutant
    EmissionContract on Ethereum.
    """

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        contract_address: Optional[str] = None,
    ):
        """
        Initialise the connector.

        Args:
            rpc_url:          Ethereum RPC URL (default from .env or Ganache)
            private_key:      Hex private key for tx signing (from .env)
            contract_address: Deployed contract address (from .env or auto-detect)
        """
        self.rpc_url = rpc_url or RPC_URL
        self.private_key = private_key or PRIVATE_KEY
        self.contract_address = contract_address or CONTRACT_ADDRESS

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

        if not self.contract_address:
            networks = build.get("networks", {})
            if networks:
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

    # ─────────────────── Write Operations ─────────────────────────────

    def store_emission(
        self,
        vehicle_id: str,
        co2: float,
        co: float = 0.0,
        nox: float = 0.0,
        hc: float = 0.0,
        pm25: float = 0.0,
        ces_score: float = 0.0,
        fraud_score: float = 0.0,
        vsp: float = 0.0,
        wltc_phase: int = 0,
        timestamp: Optional[int] = None,
    ) -> dict:
        """
        Store a multi-pollutant emission record on-chain.

        Args:
            vehicle_id:  Vehicle registration number
            co2:         CO2 in g/km
            co:          CO in g/km
            nox:         NOx in g/km
            hc:          HC in g/km
            pm25:        PM2.5 in g/km
            ces_score:   Composite Emission Score (0.0-2.0+)
            fraud_score: Fraud detection score (0.0-1.0)
            vsp:         Vehicle Specific Power in W/kg
            wltc_phase:  WLTC phase (0=Low, 1=Medium, 2=High, 3=ExtraHigh)
            timestamp:   Unix epoch (default: now)

        Returns:
            dict: { tx_hash, status, block_number }
        """
        if timestamp is None:
            timestamp = int(time.time())

        # Scale float values to integers for Solidity
        co2_scaled = int(round(co2 * SCALE_3))
        co_scaled = int(round(co * SCALE_3))
        nox_scaled = int(round(nox * SCALE_3))
        hc_scaled = int(round(hc * SCALE_3))
        pm25_scaled = int(round(pm25 * SCALE_5))
        ces_scaled = int(round(ces_score * SCALE_4))
        fraud_scaled = int(round(fraud_score * SCALE_4))
        vsp_scaled = int(round(vsp * SCALE_3))
        phase = min(max(int(wltc_phase), 0), 3)

        tx = self.contract.functions.storeEmission(
            vehicle_id,
            co2_scaled,
            co_scaled,
            nox_scaled,
            hc_scaled,
            pm25_scaled,
            ces_scaled,
            fraud_scaled,
            vsp_scaled,
            phase,
            timestamp,
        ).build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "gas": 800000,
            "gasPrice": self.w3.eth.gas_price,
        })

        if self.account:
            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        else:
            tx_hash = self.w3.eth.send_transaction(tx)

        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

        return {
            "tx_hash": receipt.transactionHash.hex(),
            "status": "success" if receipt.status == 1 else "failed",
            "block_number": receipt.blockNumber,
        }

    # ─────────────────── Read Operations ──────────────────────────────

    def get_history(self, vehicle_id: str) -> list:
        """
        Get all emission records for a vehicle from the blockchain.

        Returns:
            list[dict]: Each dict has multi-pollutant fields
        """
        records = self.contract.functions.getAllRecords(vehicle_id).call()
        return [self._parse_record(r) for r in records]

    def get_violations(self, vehicle_id: str) -> list:
        """Get all FAIL records for a vehicle."""
        records = self.contract.functions.getViolations(vehicle_id).call()
        return [self._parse_record(r) for r in records]

    def get_record_count(self, vehicle_id: str) -> int:
        """Get total number of records for a vehicle."""
        return self.contract.functions.getRecordCount(vehicle_id).call()

    def get_registered_vehicles(self) -> list:
        """Get list of all vehicle IDs with records."""
        return self.contract.functions.getRegisteredVehicles().call()

    def get_vehicle_stats(self, vehicle_id: str) -> dict:
        """
        Get aggregated stats for a vehicle.

        Returns:
            dict: { total_records, violations, fraud_alerts, avg_ces }
        """
        try:
            result = self.contract.functions.getVehicleStats(vehicle_id).call()
            return {
                "total_records": result[0],
                "violations": result[1],
                "fraud_alerts": result[2],
                "avg_ces": result[3] / SCALE_4 if result[0] > 0 else 0.0,
            }
        except Exception:
            return {
                "total_records": 0,
                "violations": 0,
                "fraud_alerts": 0,
                "avg_ces": 0.0,
            }

    # ─────────────────── Status ───────────────────────────────────────

    def get_status(self) -> dict:
        """Health check: connection status, block number, contract address."""
        return {
            "connected": self.w3.is_connected(),
            "block_number": self.w3.eth.block_number,
            "contract_address": self.contract_address,
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
        }


# ────────────────────────── Standalone Test ──────────────────────────────

if __name__ == "__main__":
    try:
        connector = BlockchainConnector()
        status = connector.get_status()
        print("Blockchain Connection Status:")
        for k, v in status.items():
            print(f"   {k}: {v}")
    except Exception as e:
        print(f"Connection failed: {e}")
