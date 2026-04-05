"""
Smart PUC — Merkle Batching Module
===================================

Implements the hot-path / cold-path separation described in
`docs/ARCHITECTURE_TRADEOFFS.md` §6. Instead of writing every telemetry
reading to the blockchain, the station accumulates readings locally in the
SQLite cold store and periodically commits a **Merkle root** to a contract
event. Any individual reading can later be proved to have been part of the
committed batch using a standard Merkle inclusion proof.

Design
------
* Leaves are the 32-byte keccak256 hashes of the canonical JSON form of a
  reading. Canonical JSON means ``sort_keys=True`` and no whitespace — so
  the hash is deterministic across languages.
* The tree is a standard binary Merkle tree. Odd nodes on a level are
  duplicated (the Bitcoin convention) rather than hashed with zero, which
  keeps verification logic simple in Solidity.
* Proofs are returned as an ordered list of sibling hashes from the leaf
  to the root, accompanied by a bitfield indicating whether each sibling
  is on the left (0) or right (1). The Solidity verifier in
  ``contracts/EmissionRegistry.sol`` (future extension) uses the same
  convention.

Usage from the backend
----------------------
::

    batcher = MerkleBatcher(vehicle_id, batch_size=100)
    for reading in stream_of_readings:
        batcher.add(reading)
        if batcher.is_full():
            root, leaves = batcher.build()
            tx_hash = blockchain.submit_batch_root(vehicle_id, root)
            persistence_store.record_merkle_batch(
                vehicle_id, root.hex(), [l.hex() for l in leaves], tx_hash
            )
            batcher.reset()

The batcher is purely functional — it owns no persistence and no chain
connection. Callers decide when to commit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────── Hashing primitives ────────────────────────────

def _keccak256(data: bytes) -> bytes:
    """Keccak-256 hash. Prefers pysha3 / eth_utils but falls back to
    hashlib's sha3_256 for portability. Note: keccak256 and sha3_256
    differ in their padding; eth_utils.keccak is the correct match for
    EVM-side verification. We try it first and fall back only when
    unavailable (e.g. in minimal test environments).
    """
    try:
        from eth_utils import keccak  # type: ignore
        return keccak(data)
    except ImportError:
        # WARNING: sha3_256 is *not* bit-identical to keccak256. This
        # fallback exists only so the module is importable without
        # eth_utils; it is flagged at runtime by ``_USING_FALLBACK_HASH``.
        global _USING_FALLBACK_HASH
        _USING_FALLBACK_HASH = True
        return hashlib.sha3_256(data).digest()


_USING_FALLBACK_HASH = False


def canonical_leaf_hash(reading: dict[str, Any]) -> bytes:
    """Return the 32-byte leaf hash of a telemetry reading.

    Canonical JSON encoding (sorted keys, compact separators) guarantees
    the same bytes across different producers.
    """
    payload = json.dumps(
        reading, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return _keccak256(payload)


# ─────────────────────────── Tree construction ─────────────────────────────

def build_merkle_root(leaves: list[bytes]) -> bytes:
    """Build a Merkle root from a list of leaf hashes.

    Follows the Bitcoin convention: if a level has an odd number of
    nodes, the last one is duplicated and hashed with itself. The empty
    list hashes to 32 zero bytes by convention.
    """
    if not leaves:
        return b"\x00" * 32
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        next_level: list[bytes] = []
        for i in range(0, len(level), 2):
            next_level.append(_keccak256(level[i] + level[i + 1]))
        level = next_level
    return level[0]


def build_merkle_proof(leaves: list[bytes], index: int) -> tuple[list[bytes], int]:
    """Build an inclusion proof for ``leaves[index]``.

    Returns a tuple ``(siblings, direction_bits)`` where:

    * ``siblings`` is the ordered list of sibling hashes from the leaf
      level up to just below the root.
    * ``direction_bits`` is an integer whose i-th bit is 1 if the i-th
      sibling sits to the right of the current node (i.e. we hash
      ``current || sibling``) and 0 if it sits to the left (``sibling ||
      current``). The bit index matches the sibling index.
    """
    if not leaves:
        raise ValueError("Cannot build proof for empty tree")
    if index < 0 or index >= len(leaves):
        raise ValueError(f"index {index} out of range for {len(leaves)} leaves")

    level = list(leaves)
    idx = index
    siblings: list[bytes] = []
    direction_bits = 0
    bit_pos = 0

    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        is_right_child = (idx % 2) == 1
        sibling_idx = idx - 1 if is_right_child else idx + 1
        siblings.append(level[sibling_idx])
        # If the sibling is on the right (current is left), bit = 1.
        # Our convention: bit = 1 means hash as (current || sibling).
        if not is_right_child:
            direction_bits |= (1 << bit_pos)
        bit_pos += 1

        next_level: list[bytes] = []
        for i in range(0, len(level), 2):
            next_level.append(_keccak256(level[i] + level[i + 1]))
        level = next_level
        idx //= 2

    return siblings, direction_bits


def verify_merkle_proof(leaf: bytes, siblings: list[bytes],
                        direction_bits: int, root: bytes) -> bool:
    """Verify that ``leaf`` is part of the tree whose root is ``root``,
    given the sibling list and direction bits produced by
    :func:`build_merkle_proof`.
    """
    current = leaf
    for i, sibling in enumerate(siblings):
        bit = (direction_bits >> i) & 1
        if bit == 1:
            # current is left child, sibling is right
            current = _keccak256(current + sibling)
        else:
            current = _keccak256(sibling + current)
    return current == root


# ─────────────────────────── Batcher ───────────────────────────────────────

@dataclass
class MerkleBatcher:
    """Accumulates readings and builds a Merkle root on demand.

    ``batch_size`` bounds memory and defines when :meth:`is_full` returns
    True. The batcher does not automatically commit — it is the caller's
    responsibility to call :meth:`build`, submit the root on-chain, and
    then :meth:`reset`.
    """

    vehicle_id: str
    batch_size: int = 100
    _leaves: list[bytes] = field(default_factory=list)
    _readings: list[dict] = field(default_factory=list)

    def add(self, reading: dict[str, Any]) -> None:
        leaf = canonical_leaf_hash(reading)
        self._leaves.append(leaf)
        self._readings.append(reading)

    def is_full(self) -> bool:
        return len(self._leaves) >= self.batch_size

    def size(self) -> int:
        return len(self._leaves)

    def build(self) -> tuple[bytes, list[bytes]]:
        """Compute and return ``(root, leaves)``. Does not mutate state."""
        return build_merkle_root(self._leaves), list(self._leaves)

    def build_hex(self) -> tuple[str, list[str]]:
        root, leaves = self.build()
        return root.hex(), [leaf.hex() for leaf in leaves]

    def proof_for(self, index: int) -> tuple[list[bytes], int]:
        return build_merkle_proof(self._leaves, index)

    def reset(self) -> None:
        self._leaves.clear()
        self._readings.clear()

    def readings(self) -> list[dict]:
        return list(self._readings)
