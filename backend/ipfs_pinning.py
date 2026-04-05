"""
Smart PUC — Optional IPFS pinning for certificate metadata
===========================================================

Closes audit item **L7** ("certificate tokenURI points at ipfs:// but
nothing actually pins the metadata, so a certificate can dangle after a
short IPFS cache window"). This module adds a pluggable pinning layer
that degrades gracefully to a no-op when no API key is configured.

Supported backends
------------------
- **web3.storage** (default, free tier): set ``IPFS_API_KEY`` to a
  web3.storage / Storacha API token.
- **pinata.cloud**: set ``IPFS_API_KEY`` to your JWT and
  ``IPFS_BACKEND=pinata``.
- **local ipfs daemon**: set ``IPFS_BACKEND=local`` and the daemon URL
  via ``IPFS_API_URL`` (default ``http://127.0.0.1:5001``).

Zero-cost policy
----------------
This module is entirely optional. If ``IPFS_API_KEY`` is not set AND the
backend is not ``local``, every call is a no-op that returns ``None``.
The repository therefore remains fully usable — tests, e2e flows, and
the software demo — without any paid service or extra install step.

API surface
-----------
::

    from backend.ipfs_pinning import IPFSPinner, build_certificate_metadata

    pinner = IPFSPinner.from_env()
    cid = pinner.pin_json(build_certificate_metadata(cert_data))
    if cid:
        token_uri = f"ipfs://{cid}"
    else:
        token_uri = ""   # Leave blank — the contract's base URI will apply.

``pin_json`` never raises on network failure; it logs a warning and
returns ``None`` so certificate issuance is never blocked by a flaky
pinning service. If the paper needs stronger durability guarantees,
switch to a dual-pin configuration (e.g. web3.storage + local daemon)
in a future iteration.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("smart_puc.ipfs")


@dataclass
class IPFSPinner:
    """Thin wrapper over whichever pinning backend the env selects.

    The constructor never performs network I/O; the first call to
    ``pin_json`` is when the HTTP client is actually contacted.
    """

    backend: str = "web3storage"
    api_key: Optional[str] = None
    api_url: str = "https://api.web3.storage/upload"
    timeout_seconds: float = 10.0

    # ────────────────────────── Factory ───────────────────────────────

    @classmethod
    def from_env(cls) -> "IPFSPinner":
        backend = os.getenv("IPFS_BACKEND", "web3storage").strip().lower()
        api_key = os.getenv("IPFS_API_KEY", "").strip() or None

        if backend == "pinata":
            api_url = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
        elif backend == "local":
            api_url = os.getenv("IPFS_API_URL", "http://127.0.0.1:5001/api/v0/add")
        else:
            api_url = os.getenv("IPFS_API_URL", "https://api.web3.storage/upload")

        return cls(backend=backend, api_key=api_key, api_url=api_url)

    # ────────────────────────── Public API ────────────────────────────

    def is_configured(self) -> bool:
        """True when a pin call will actually hit the network.

        - ``web3storage`` / ``pinata`` need an API key.
        - ``local`` needs no key, but relies on a daemon running at
          ``api_url``. We optimistically return True for ``local``; the
          first pin attempt is what actually verifies the daemon.
        """
        if self.backend == "local":
            return True
        return bool(self.api_key)

    def pin_json(self, payload: Dict[str, Any]) -> Optional[str]:
        """Pin a JSON-serialisable document. Returns the CID on success,
        ``None`` on any failure (including a missing API key)."""
        if not self.is_configured():
            logger.info("ipfs: pinning disabled (no IPFS_API_KEY for backend=%s)", self.backend)
            return None

        try:
            import requests  # local import so the rest of the backend works without it
        except Exception:  # pragma: no cover
            logger.warning("ipfs: `requests` not installed; skipping pin")
            return None

        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

        try:
            if self.backend == "pinata":
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                resp = requests.post(
                    self.api_url,
                    data=body,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                resp.raise_for_status()
                return resp.json().get("IpfsHash")
            elif self.backend == "local":
                # IPFS HTTP API /add expects multipart form data.
                files = {"file": ("metadata.json", body, "application/json")}
                resp = requests.post(self.api_url, files=files, timeout=self.timeout_seconds)
                resp.raise_for_status()
                # /add returns one line per file: {"Name":..., "Hash":..., "Size":...}
                first_line = resp.text.strip().split("\n")[0]
                return json.loads(first_line).get("Hash")
            else:  # web3.storage (default)
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                resp = requests.post(
                    self.api_url,
                    data=body,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                resp.raise_for_status()
                return resp.json().get("cid") or resp.json().get("Hash")
        except Exception as exc:  # noqa: BLE001 — pinning must never block issuance
            logger.warning("ipfs: pin failed on backend=%s: %s", self.backend, exc)
            return None


# ─────────────────────── Metadata builder ────────────────────────────

def build_certificate_metadata(
    vehicle_id: str,
    owner_address: str,
    ces_score: float,
    issued_at: int,
    expires_at: int,
    is_first_puc: bool,
    station_address: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an ERC-721 metadata JSON document suitable for IPFS pinning.

    The shape follows the OpenSea / ERC-721 metadata standard so that
    the NFT renders correctly in existing wallets and explorers while
    still carrying the Smart-PUC-specific attributes reviewers care
    about (CES score, first-PUC branch, issuance station).
    """
    attributes = [
        {"trait_type": "Vehicle ID", "value": vehicle_id},
        {"trait_type": "Owner", "value": owner_address},
        {"trait_type": "CES Score", "value": round(float(ces_score), 4)},
        {"trait_type": "First PUC", "value": "Yes" if is_first_puc else "No"},
        {"trait_type": "Issued (unix)", "value": int(issued_at)},
        {"trait_type": "Expires (unix)", "value": int(expires_at)},
    ]
    if station_address:
        attributes.append({"trait_type": "Issuing Station", "value": station_address})

    metadata: Dict[str, Any] = {
        "name": f"Smart PUC Certificate — {vehicle_id}",
        "description": (
            "Blockchain-anchored Pollution Under Control (PUC) certificate "
            "issued by the Smart PUC research prototype. The on-chain record "
            "is the authoritative source of truth; this metadata document "
            "provides a wallet-friendly summary pinned to IPFS for durability."
        ),
        "external_url": "https://github.com/your-org/smart-puc",
        "smart_puc_version": "3.2.2",
        "attributes": attributes,
    }
    if extra:
        metadata["smart_puc_extra"] = extra
    return metadata


__all__ = ["IPFSPinner", "build_certificate_metadata"]
