"""
Smart PUC — Chain event listener for PhaseCompleted + BatchRootCommitted
=========================================================================

Closes audit-report Fix #10 ("Add backend/phase_listener.py that consumes
PhaseCompleted and BatchRootCommitted events into SQLite"). These two
events are emitted on-chain by the EmissionRegistry contract in v3.2 but
were never consumed by any off-chain projection. This module does three
things:

1. **Subscribe** to both events from a given block height (0 = full
   history) using the existing ``BlockchainConnector``'s web3 instance.
2. **Project** each event into a SQLite row in the ``chain_events``
   table (created here on first use). The projection is append-only and
   keyed on ``(tx_hash, log_index)`` so re-running the listener is
   idempotent — repeated runs against the same chain will not produce
   duplicate rows.
3. **Query helpers** expose the projected data to the FastAPI layer
   without the backend having to re-scan the chain on every request.
   ``get_phase_events(vehicle_id)`` and ``get_batch_roots(vehicle_id)``
   return paginated Python dicts ready to JSON-serialise.

The module is *pull-based*, not push-based: there is no long-running
subscription thread. Instead, the backend's FastAPI startup hook (or a
cron / systemd timer) calls ``sync_from_chain()`` periodically. This
avoids the need for a websocket RPC endpoint (Hardhat / Ganache support
only HTTP by default) and keeps the runtime footprint small.

Usage
-----
::

    from backend.blockchain_connector import BlockchainConnector
    from backend.phase_listener import PhaseListener

    conn = BlockchainConnector()
    listener = PhaseListener(conn, db_path="data/smart_puc.db")
    listener.sync_from_chain()  # catches up from last_synced_block

    events = listener.get_phase_events(vehicle_id="MH12AB1234")
    roots  = listener.get_batch_roots(vehicle_id="MH12AB1234")

Design notes
------------
- The listener is *not* a fraud detector or a compliance oracle. It is
  a read-projection. The on-chain event remains the authoritative
  source of truth.
- We use the contract's existing ABI (already loaded by
  ``BlockchainConnector._load_contract("EmissionRegistry")``) so no
  re-ABI-parsing is required.
- SQLite WAL mode is used for concurrent-read safety with the rest of
  the backend's persistence store.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


_LISTENER_SCHEMA = """
CREATE TABLE IF NOT EXISTS chain_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name    TEXT NOT NULL,
    vehicle_id    TEXT NOT NULL,
    block_number  INTEGER NOT NULL,
    tx_hash       TEXT NOT NULL,
    log_index     INTEGER NOT NULL,
    emitted_at    INTEGER NOT NULL,
    payload_json  TEXT NOT NULL,
    UNIQUE(tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_chain_events_vehicle ON chain_events(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_chain_events_name    ON chain_events(event_name);
CREATE INDEX IF NOT EXISTS idx_chain_events_block   ON chain_events(block_number);

CREATE TABLE IF NOT EXISTS chain_event_cursor (
    event_name        TEXT PRIMARY KEY,
    last_synced_block INTEGER NOT NULL
);
"""


class PhaseListener:
    """Pull-based projection of PhaseCompleted + BatchRootCommitted events."""

    # Events we care about. Name → human-friendly label used in SQLite.
    _EVENT_NAMES = ("PhaseCompleted", "BatchRootCommitted")

    def __init__(
        self,
        connector: Any,
        db_path: str | os.PathLike[str] = "data/smart_puc.db",
        max_blocks_per_scan: int = 5000,
    ) -> None:
        """
        Args:
            connector: A :class:`backend.blockchain_connector.BlockchainConnector`
                instance, already connected to a running chain node.
            db_path: SQLite file for the event projection. Parents are
                created on demand. Use the same DB as the main
                persistence store for a single-source-of-truth deployment.
            max_blocks_per_scan: Upper bound on the block range passed
                to a single ``eth_getLogs`` call. Hardhat/Ganache tolerate
                the full range in one shot; public RPCs (Alchemy, Infura)
                often cap at 2000–10000. The default 5000 is safe for
                both.
        """
        self._connector = connector
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._max_blocks_per_scan = int(max_blocks_per_scan)
        self._init_schema()

    # ─────────────────────── SQLite helpers ───────────────────────

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(
            str(self._path),
            isolation_level=None,  # autocommit
            timeout=10,
            check_same_thread=False,
        )
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.row_factory = sqlite3.Row
        return con

    def _init_schema(self) -> None:
        with self._lock, self._conn() as con:
            con.executescript(_LISTENER_SCHEMA)

    def _get_cursor(self, event_name: str) -> int:
        with self._lock, self._conn() as con:
            row = con.execute(
                "SELECT last_synced_block FROM chain_event_cursor WHERE event_name = ?",
                (event_name,),
            ).fetchone()
            return int(row["last_synced_block"]) if row else 0

    def _set_cursor(self, event_name: str, block_number: int) -> None:
        with self._lock, self._conn() as con:
            con.execute(
                "INSERT INTO chain_event_cursor(event_name, last_synced_block) "
                "VALUES (?, ?) "
                "ON CONFLICT(event_name) DO UPDATE SET last_synced_block = excluded.last_synced_block",
                (event_name, int(block_number)),
            )

    # ─────────────────────── Chain sync ───────────────────────

    def sync_from_chain(
        self,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
    ) -> Dict[str, int]:
        """Catch up from the last-synced block to the current head.

        Args:
            from_block: Explicit start block (inclusive). Defaults to
                the per-event cursor persisted in SQLite (0 on first run).
            to_block: Explicit end block (inclusive). Defaults to the
                chain head at call time.

        Returns:
            Dict mapping event name → number of new rows inserted during
            this scan. Zero for an up-to-date chain.
        """
        connector = self._connector
        registry = getattr(connector, "registry", None)
        if registry is None:
            return {name: 0 for name in self._EVENT_NAMES}

        w3 = connector.w3
        head = int(to_block) if to_block is not None else int(w3.eth.block_number)
        inserted: Dict[str, int] = {name: 0 for name in self._EVENT_NAMES}

        for event_name in self._EVENT_NAMES:
            cursor = int(from_block) if from_block is not None else self._get_cursor(event_name)
            if cursor > head:
                continue

            event_cls = getattr(registry.events, event_name, None)
            if event_cls is None:
                # Contract ABI does not expose this event — older deploy.
                continue

            # Scan in fixed chunks to stay inside RPC limits.
            start = cursor
            max_block_seen = cursor
            while start <= head:
                stop = min(start + self._max_blocks_per_scan - 1, head)
                try:
                    logs = event_cls.create_filter(
                        fromBlock=start, toBlock=stop
                    ).get_all_entries()
                except Exception:  # noqa: BLE001 — RPC hiccups are non-fatal here
                    logs = []
                for log in logs:
                    if self._insert_log(event_name, log):
                        inserted[event_name] += 1
                    max_block_seen = max(max_block_seen, int(log.blockNumber))
                start = stop + 1

            self._set_cursor(event_name, max_block_seen)

        return inserted

    def _insert_log(self, event_name: str, log: Any) -> bool:
        """Insert one event log into SQLite. Returns True if the row was
        new (i.e. not a duplicate from a re-scan)."""
        args = dict(log["args"])
        vehicle_id = args.get("vehicleId", "")

        # Normalise values for JSON serialisation: bytes → 0x-hex, int → int.
        payload: Dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, bytes):
                payload[k] = "0x" + v.hex()
            elif isinstance(v, (bytes, bytearray, memoryview)):
                payload[k] = "0x" + bytes(v).hex()
            else:
                payload[k] = v

        tx_hash = (
            log.transactionHash.hex()
            if hasattr(log.transactionHash, "hex")
            else str(log.transactionHash)
        )
        log_index = int(getattr(log, "logIndex", 0))
        block_number = int(log.blockNumber)

        try:
            with self._lock, self._conn() as con:
                con.execute(
                    "INSERT OR IGNORE INTO chain_events "
                    "(event_name, vehicle_id, block_number, tx_hash, log_index, "
                    " emitted_at, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, strftime('%s','now'), ?)",
                    (
                        event_name,
                        str(vehicle_id),
                        block_number,
                        tx_hash,
                        log_index,
                        json.dumps(payload, default=str),
                    ),
                )
                return con.total_changes > 0
        except Exception:  # noqa: BLE001
            return False

    # ─────────────────────── Query helpers ───────────────────────

    def get_phase_events(
        self, vehicle_id: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return recent PhaseCompleted events, newest first."""
        return self._query("PhaseCompleted", vehicle_id, limit)

    def get_batch_roots(
        self, vehicle_id: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return recent BatchRootCommitted events, newest first."""
        return self._query("BatchRootCommitted", vehicle_id, limit)

    def _query(
        self, event_name: str, vehicle_id: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        with self._lock, self._conn() as con:
            if vehicle_id:
                rows = con.execute(
                    "SELECT * FROM chain_events "
                    "WHERE event_name = ? AND vehicle_id = ? "
                    "ORDER BY block_number DESC, log_index DESC LIMIT ?",
                    (event_name, vehicle_id, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM chain_events "
                    "WHERE event_name = ? "
                    "ORDER BY block_number DESC, log_index DESC LIMIT ?",
                    (event_name, limit),
                ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            payload = {}
            try:
                payload = json.loads(r["payload_json"])
            except Exception:  # noqa: BLE001
                pass
            out.append(
                {
                    "id": r["id"],
                    "event_name": r["event_name"],
                    "vehicle_id": r["vehicle_id"],
                    "block_number": r["block_number"],
                    "tx_hash": r["tx_hash"],
                    "log_index": r["log_index"],
                    "emitted_at": r["emitted_at"],
                    **payload,
                }
            )
        return out

    # ─────────────────────── Bookkeeping ───────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return per-event row counts and cursor positions for the
        ``/api/chain-events/status`` dashboard endpoint."""
        with self._lock, self._conn() as con:
            counts = {}
            for name in self._EVENT_NAMES:
                row = con.execute(
                    "SELECT COUNT(*) AS c FROM chain_events WHERE event_name = ?",
                    (name,),
                ).fetchone()
                counts[name] = int(row["c"])
            cursors = {}
            for name in self._EVENT_NAMES:
                cursors[name] = self._get_cursor(name)
        return {"counts": counts, "cursors": cursors}
