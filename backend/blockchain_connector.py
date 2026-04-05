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
# When PRIVATE_KEY is unset and the node exposes unlocked accounts, the
# backend uses w3.eth.accounts[0] by default. Override this with
# STATION_ADDRESS if the registered testing-station is a different
# unlocked account on the node (common when deploy.js registered
# signers[1] instead of signers[0]).
STATION_ADDRESS = os.getenv("STATION_ADDRESS", "")
# Optional dev-mode fallback: when an incoming /api/record submission has no
# OBD device signature (e.g. a dashboard "simulate" button, a backfill
# script, or a smoke test), the backend will EIP-712 sign the payload
# server-side using this key. The address derived from it MUST already be
# registered on-chain via EmissionRegistry.setRegisteredDevice — otherwise
# the on-chain verifier will still reject the write. Unset this variable
# in production deployments; real OBD submissions must sign client-side.
STATION_DEVICE_PRIVATE_KEY = os.getenv(
    "STATION_DEVICE_PRIVATE_KEY",
    os.getenv("OBD_DEVICE_PRIVATE_KEY", ""),
)
# Alternative to STATION_DEVICE_PRIVATE_KEY: if the connected RPC node has
# unlocked test accounts (Ganache, Hardhat node, Anvil), set this env to a
# registered device address and the backend will sign via JSON-RPC
# eth_signTypedData_v4 on the node. Use EITHER a private key OR an unlocked
# address — not both. Only intended for dev / smoke tests.
STATION_DEVICE_ADDRESS = os.getenv("STATION_DEVICE_ADDRESS", "")

BUILD_DIR = os.path.join(os.path.dirname(__file__), "..", "build", "contracts")

SCALE_POLLUTANT = 1000
SCALE_SCORE = 10000

# EIP-712 domain constants — must match EmissionRegistry's
# EIP712Upgradeable initializer (__EIP712_init("SmartPUC", "3.2")).
EIP712_DOMAIN_NAME = "SmartPUC"
EIP712_DOMAIN_VERSION = "3.2"

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
        elif STATION_ADDRESS:
            self.address = Web3.to_checksum_address(STATION_ADDRESS)
            self.account = None
        else:
            self.address = self.w3.eth.accounts[0]
            self.account = None

        # Optional dev-mode OBD device auto-signer — two flavours:
        # (1) unlocked node account via STATION_DEVICE_ADDRESS (JSON-RPC
        #     eth_signTypedData_v4) — preferred when available because it
        #     avoids storing private keys in env files.
        # (2) local private key via STATION_DEVICE_PRIVATE_KEY.
        # STATION_DEVICE_ADDRESS is checked first so that an explicit
        # env-var override wins over a stale OBD_DEVICE_PRIVATE_KEY that
        # might still be lingering in a checked-in .env file.
        self._device_account = None
        self._device_rpc_address: Optional[str] = None
        if STATION_DEVICE_ADDRESS:
            try:
                self._device_rpc_address = Web3.to_checksum_address(STATION_DEVICE_ADDRESS)
            except Exception as exc:  # noqa: BLE001
                print(f"  Warning: STATION_DEVICE_ADDRESS invalid: {exc}")
                self._device_rpc_address = None
        elif STATION_DEVICE_PRIVATE_KEY:
            try:
                self._device_account = self.w3.eth.account.from_key(STATION_DEVICE_PRIVATE_KEY)
            except Exception as exc:  # noqa: BLE001
                print(f"  Warning: STATION_DEVICE_PRIVATE_KEY invalid: {exc}")
                self._device_account = None

        # Load all 3 contracts
        self.registry = self._load_contract("EmissionRegistry")
        self.puc_cert = self._load_contract("PUCCertificate")
        self.green_token = self._load_contract("GreenToken")

        # Store addresses for status reporting
        self.registry_address = self.registry.address if self.registry else None
        self.puc_cert_address = self.puc_cert.address if self.puc_cert else None
        self.green_token_address = self.green_token.address if self.green_token else None

    def _load_contract(self, name: str):
        """Load a contract from Truffle-shape build artifacts.

        Resolution order:
          1. Exact match on the live RPC's chainId (net.version). This is
             the only safe choice when an artifact contains stale entries
             from previous deploys against other networks.
          2. Fallback to the last entry in insertion order — preserves the
             legacy Truffle behaviour for artifacts with a single entry.
        """
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

        entry = None
        try:
            live_id = str(self.w3.net.version)
            if live_id in networks:
                entry = networks[live_id]
        except Exception:
            entry = None
        if entry is None:
            entry = list(networks.values())[-1]

        address = entry["address"]
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=abi,
        )

    def _send_tx(self, tx_func, gas=800000, from_address: Optional[str] = None):
        """Build, sign, and send a transaction.

        ``from_address`` overrides the default station/admin account. This is
        used by admin-only calls (e.g. ``revokeCertificate``) where the
        contract's ``onlyAuthority`` modifier requires ``msg.sender`` to be the
        deployer (signers[0] on Hardhat) rather than the station signer
        (signers[1]) used for routine writes. When an override is provided and
        no local ``self.account`` private key is configured, the tx is sent
        via the node's unlocked-account path (``eth_sendTransaction``) — the
        standard Hardhat/Ganache dev-chain behaviour.
        """
        sender = Web3.to_checksum_address(from_address) if from_address else self.address
        tx = tx_func.build_transaction({
            "from": sender,
            "nonce": self.w3.eth.get_transaction_count(sender),
            "gas": gas,
            "gasPrice": self.w3.eth.gas_price,
        })

        if self.account and sender == self.address:
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

    def _sign_emission_eip712(
        self,
        vehicle_id: str,
        co2_s: int,
        co_s: int,
        nox_s: int,
        hc_s: int,
        pm25_s: int,
        timestamp: int,
        nonce: bytes,
    ) -> bytes:
        """EIP-712 sign an EmissionReading payload with the configured
        dev-mode OBD device key. The struct / domain MUST match
        EmissionRegistry._verifyDeviceSignature exactly."""
        from eth_account.messages import encode_typed_data  # lazy import

        if self._device_account is None and self._device_rpc_address is None:
            return b""

        chain_id = int(self.w3.net.version) if self.w3.net.version else 31337
        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "EmissionReading": [
                    {"name": "vehicleId", "type": "string"},
                    {"name": "co2", "type": "uint256"},
                    {"name": "co", "type": "uint256"},
                    {"name": "nox", "type": "uint256"},
                    {"name": "hc", "type": "uint256"},
                    {"name": "pm25", "type": "uint256"},
                    {"name": "timestamp", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "primaryType": "EmissionReading",
            "domain": {
                "name": EIP712_DOMAIN_NAME,
                "version": EIP712_DOMAIN_VERSION,
                "chainId": chain_id,
                "verifyingContract": self.registry_address,
            },
            "message": {
                "vehicleId": vehicle_id,
                "co2": co2_s,
                "co": co_s,
                "nox": nox_s,
                "hc": hc_s,
                "pm25": pm25_s,
                "timestamp": int(timestamp),
                "nonce": nonce,
            },
        }
        if self._device_account is not None:
            signable = encode_typed_data(full_message=typed_data)
            signed = self._device_account.sign_message(signable)
            return bytes(signed.signature)

        # JSON-RPC path: sign via the node's unlocked account. The node
        # expects the typed_data payload as a JSON STRING (per MetaMask's
        # v4 spec), and bytes32 nonces must be 0x-hex. Normalise because
        # HexBytes.hex() in web3.py 6.x already prepends "0x" but plain
        # bytes.hex() does not.
        rpc_message = dict(typed_data["message"])
        nonce_hex = nonce.hex() if isinstance(nonce, (bytes, bytearray)) else str(nonce)
        if not nonce_hex.startswith("0x"):
            nonce_hex = "0x" + nonce_hex
        rpc_message["nonce"] = nonce_hex
        rpc_payload = {
            "types": typed_data["types"],
            "primaryType": typed_data["primaryType"],
            "domain": typed_data["domain"],
            "message": rpc_message,
        }
        sig_hex = self.w3.manager.request_blocking(
            "eth_signTypedData_v4",
            [self._device_rpc_address, json.dumps(rpc_payload)],
        )
        return bytes.fromhex(sig_hex[2:] if sig_hex.startswith("0x") else sig_hex)

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
        nonce: Optional[bytes] = None,
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

        # Use provided nonce (from the OBD device, so the device's EIP-712
        # signature covers the same nonce the contract will check). Fall
        # back to locally generated nonce only if the caller did not
        # supply one (e.g. server-generated synthetic records).
        if nonce is None:
            nonce = self._generate_nonce(vehicle_id, timestamp)
        elif isinstance(nonce, str):
            nonce = bytes.fromhex(nonce.replace("0x", ""))

        # Ensure device_signature is bytes
        if isinstance(device_signature, str):
            device_signature = bytes.fromhex(device_signature.replace("0x", ""))

        # Dev-mode fallback: if the caller did not supply a device signature
        # AND an auto-signer is configured (STATION_DEVICE_PRIVATE_KEY or
        # STATION_DEVICE_ADDRESS), sign the payload server-side. The device
        # address MUST already be registered via setRegisteredDevice.
        if not device_signature and (
            self._device_account is not None or self._device_rpc_address is not None
        ):
            device_signature = self._sign_emission_eip712(
                vehicle_id, co2_s, co_s, nox_s, hc_s, pm25_s, timestamp, nonce,
            )

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
        is_first_puc: Optional[bool] = None,
        auto_pin_metadata: bool = True,
    ) -> dict:
        """
        Issue a PUC certificate NFT for a vehicle.

        Args:
            vehicle_id:    Vehicle registration number
            vehicle_owner: Wallet address of the vehicle owner
            metadata_uri:  Optional token metadata URI (IPFS or HTTP)
            is_first_puc:  Optional explicit override for the CMVR Rule 115
                           first-PUC (360-day) validity branch. When ``None``
                           (default), the contract auto-detects based on
                           whether the vehicle has any prior certificates.
                           When ``True``/``False``, the explicit 4-arg
                           overload ``issueCertificateWithFirstFlag`` is
                           called instead.

        Returns:
            dict: { tx_hash, status, block_number, gas_used }
        """
        owner_addr = Web3.to_checksum_address(vehicle_owner)

        # Optional IPFS auto-pin path (audit L7). When no metadata_uri was
        # supplied and auto_pin_metadata is enabled, attempt to pin a
        # standard ERC-721 metadata document via backend.ipfs_pinning.
        # The call is a no-op (returns None) when IPFS_API_KEY is unset,
        # so this does not affect the default zero-cost flow.
        if metadata_uri is None and auto_pin_metadata:
            try:
                from backend.ipfs_pinning import IPFSPinner, build_certificate_metadata
                pinner = IPFSPinner.from_env()
                if pinner.is_configured():
                    now = int(time.time())
                    meta = build_certificate_metadata(
                        vehicle_id=vehicle_id,
                        owner_address=owner_addr,
                        ces_score=0.0,  # real value is on-chain; placeholder for metadata doc
                        issued_at=now,
                        expires_at=now + (360 if bool(is_first_puc) else 180) * 86400,
                        is_first_puc=bool(is_first_puc) if is_first_puc is not None else False,
                    )
                    cid = pinner.pin_json(meta)
                    if cid:
                        metadata_uri = f"ipfs://{cid}"
            except Exception:  # noqa: BLE001 — pinning must never block issuance
                pass

        if is_first_puc is not None:
            # Explicit control path — always pass a metadata URI (empty ok).
            tx_func = self.puc_cert.functions.issueCertificateWithFirstFlag(
                vehicle_id,
                owner_addr,
                metadata_uri or "",
                bool(is_first_puc),
            )
        elif metadata_uri is not None:
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
                    # CertificateData grew an ``isFirstPUC`` field in v3.2 for
                    # the 360-day first-PUC validity branch (audit L7). Older
                    # proxies returning a 9-tuple are handled gracefully.
                    "isFirstPUC": bool(raw[9]) if len(raw) > 9 else False,
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

    def _resolve_puc_authority(self) -> Optional[str]:
        """Return the PUCCertificate's on-chain authority (deployer) address.

        Used for admin-only calls like ``revokeCertificate``. On Hardhat /
        Ganache dev nodes the authority account is unlocked, so txs can be
        sent from it via ``eth_sendTransaction`` without a private key.
        """
        try:
            return self.puc_cert.functions.authority().call()
        except Exception:
            return None

    def revoke_certificate(self, token_id: int, reason: str) -> dict:
        """Revoke a PUC certificate (authority only).

        The PUCCertificate's ``revokeCertificate`` is guarded by
        ``onlyAuthority``, so the tx must originate from the contract
        deployer (signers[0]) rather than the station signer used for
        issuance. We resolve that address at call time and route the tx
        through the node's unlocked-account path.
        """
        tx_func = self.puc_cert.functions.revokeCertificate(token_id, reason)
        authority = self._resolve_puc_authority()
        return self._send_tx(tx_func, from_address=authority)

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
