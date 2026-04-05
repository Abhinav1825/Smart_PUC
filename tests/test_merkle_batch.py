"""Tests for the Merkle batching module.

Covers:
    * Deterministic leaf hashing.
    * Root stability across calls.
    * Proof round-trip for every leaf in a batch.
    * Odd-sized batches (duplicate-last-node convention).
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "backend"))

from merkle_batch import (  # type: ignore  # noqa: E402
    MerkleBatcher,
    build_merkle_proof,
    build_merkle_root,
    canonical_leaf_hash,
    verify_merkle_proof,
)


def _reading(i: int) -> dict:
    return {
        "vehicle_id": "TESTMERKLE01",
        "speed": 40.0 + i,
        "rpm": 2000 + i,
        "fuel_rate": 4.5,
        "timestamp": 1700000000 + i,
    }


def test_canonical_leaf_is_deterministic() -> None:
    r = _reading(0)
    a = canonical_leaf_hash(r)
    b = canonical_leaf_hash(dict(reversed(list(r.items()))))  # reorder keys
    assert a == b, "canonical hash must ignore key order"
    assert len(a) == 32


def test_root_stable_across_calls() -> None:
    leaves = [canonical_leaf_hash(_reading(i)) for i in range(17)]
    r1 = build_merkle_root(leaves)
    r2 = build_merkle_root(leaves)
    assert r1 == r2
    assert len(r1) == 32


def test_root_empty_tree() -> None:
    assert build_merkle_root([]) == b"\x00" * 32


def test_proof_roundtrip_even_batch() -> None:
    leaves = [canonical_leaf_hash(_reading(i)) for i in range(8)]
    root = build_merkle_root(leaves)
    for idx, leaf in enumerate(leaves):
        siblings, bits = build_merkle_proof(leaves, idx)
        assert verify_merkle_proof(leaf, siblings, bits, root), f"proof failed at idx {idx}"


def test_proof_roundtrip_odd_batch() -> None:
    leaves = [canonical_leaf_hash(_reading(i)) for i in range(13)]
    root = build_merkle_root(leaves)
    for idx, leaf in enumerate(leaves):
        siblings, bits = build_merkle_proof(leaves, idx)
        assert verify_merkle_proof(leaf, siblings, bits, root), f"proof failed at idx {idx}"


def test_proof_fails_for_wrong_leaf() -> None:
    leaves = [canonical_leaf_hash(_reading(i)) for i in range(8)]
    root = build_merkle_root(leaves)
    siblings, bits = build_merkle_proof(leaves, 3)
    wrong_leaf = canonical_leaf_hash(_reading(99))
    assert not verify_merkle_proof(wrong_leaf, siblings, bits, root)


def test_batcher_build_and_reset() -> None:
    b = MerkleBatcher(vehicle_id="V1", batch_size=4)
    for i in range(3):
        b.add(_reading(i))
    assert not b.is_full()
    b.add(_reading(3))
    assert b.is_full()
    root, leaves = b.build()
    assert len(leaves) == 4
    assert len(root) == 32
    b.reset()
    assert b.size() == 0
