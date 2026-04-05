"""Tests for backend/phase_listener.py (audit Fix #10).

The tests exercise the SQLite schema, cursor bookkeeping, and the query
helpers WITHOUT requiring a running chain node. We feed synthetic log
objects into the internal ``_insert_log`` method so we cover the
projection logic deterministically.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.phase_listener import PhaseListener


class _FakeConnector:
    """Minimal stand-in for BlockchainConnector — phase_listener only
    needs ``.registry`` to exist for sync_from_chain to early-return,
    which is not exercised in these tests (we call _insert_log directly)."""
    def __init__(self):
        self.registry = None
        self.w3 = types.SimpleNamespace(eth=types.SimpleNamespace(block_number=0))


def _make_log(tx_hash, log_index, block_number, vehicle_id, extra):
    class _L:
        pass
    L = _L()
    L.blockNumber = block_number
    L.transactionHash = types.SimpleNamespace(hex=lambda: tx_hash)
    L.logIndex = log_index
    L.args = {"vehicleId": vehicle_id, **extra}
    L["args"] = L.args  # dict-like access used by the listener
    return L


def _make_log_dict(tx_hash, log_index, block_number, vehicle_id, extra):
    """PhaseListener._insert_log uses dict-like log['args'] access."""
    log = {
        "args": {"vehicleId": vehicle_id, **extra},
    }
    # Attach the rest as object attributes via a small wrapper.
    class _Wrap:
        def __init__(self, inner):
            self._inner = inner
            self.blockNumber = block_number
            self.transactionHash = types.SimpleNamespace(hex=lambda: tx_hash)
            self.logIndex = log_index

        def __getitem__(self, key):
            return self._inner[key]

    return _Wrap(log)


def test_insert_log_and_query_phase_events(tmp_path):
    listener = PhaseListener(_FakeConnector(), db_path=tmp_path / "events.db")
    log = _make_log_dict(
        "0xabc123",
        0,
        42,
        "MH12AB1234",
        {"phase": 1, "avgCES": 5500, "distanceMeters": 12000, "timestamp": 1_700_000_000},
    )
    inserted = listener._insert_log("PhaseCompleted", log)
    assert inserted is True

    events = listener.get_phase_events("MH12AB1234")
    assert len(events) == 1
    assert events[0]["event_name"] == "PhaseCompleted"
    assert events[0]["block_number"] == 42
    assert events[0]["vehicle_id"] == "MH12AB1234"
    assert events[0]["phase"] == 1
    assert events[0]["avgCES"] == 5500


def test_duplicate_log_is_idempotent(tmp_path):
    listener = PhaseListener(_FakeConnector(), db_path=tmp_path / "events.db")
    log = _make_log_dict("0xdeadbeef", 3, 100, "VID", {"phase": 2, "avgCES": 3000, "distanceMeters": 5000, "timestamp": 1})
    assert listener._insert_log("PhaseCompleted", log) is True
    # Re-insert the same (tx_hash, log_index) — listener should ignore it.
    listener._insert_log("PhaseCompleted", log)
    events = listener.get_phase_events("VID")
    assert len(events) == 1  # still exactly one row


def test_batch_root_query_path(tmp_path):
    listener = PhaseListener(_FakeConnector(), db_path=tmp_path / "events.db")
    log = _make_log_dict(
        "0xdeadc0de",
        5,
        7,
        "MH12RT",
        {"dayIndex": 12, "root": b"\xde" * 32, "count": 1000},
    )
    listener._insert_log("BatchRootCommitted", log)
    roots = listener.get_batch_roots("MH12RT")
    assert len(roots) == 1
    assert roots[0]["dayIndex"] == 12
    assert roots[0]["count"] == 1000
    # bytes should be normalised to 0x-hex strings.
    assert isinstance(roots[0]["root"], str) and roots[0]["root"].startswith("0x")


def test_stats_reports_counts_and_cursors(tmp_path):
    listener = PhaseListener(_FakeConnector(), db_path=tmp_path / "events.db")
    listener._insert_log(
        "PhaseCompleted",
        _make_log_dict("0xa", 0, 1, "A", {"phase": 0, "avgCES": 100, "distanceMeters": 1, "timestamp": 1}),
    )
    listener._insert_log(
        "BatchRootCommitted",
        _make_log_dict("0xb", 0, 1, "A", {"dayIndex": 1, "root": b"\x00" * 32, "count": 1}),
    )
    stats = listener.stats()
    assert stats["counts"]["PhaseCompleted"] == 1
    assert stats["counts"]["BatchRootCommitted"] == 1
    assert "PhaseCompleted" in stats["cursors"]
    assert "BatchRootCommitted" in stats["cursors"]
