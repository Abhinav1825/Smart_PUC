"""
Smart PUC — SQLite Persistence Layer
=====================================

Provides durable, thread-safe storage for:

1. **Rate limiter** — per-IP request counters with a rolling window.
2. **Notifications** — audit log of events (violations, fraud alerts,
   cert expiry warnings, admin actions).
3. **Cold-path telemetry store** — every emission reading observed by the
   testing station, regardless of whether it was sampled to chain.
4. **Merkle batch roots** — tracks the off-chain Merkle roots submitted to
   the EmissionRegistry so that proofs can be reconstructed client-side.

Design rationale
----------------
Earlier versions of Smart PUC stored all three of these in Python
dictionaries, which meant they were lost on restart and did not survive
multi-process deployments. SQLite is the minimum viable durable store:
zero external dependencies, single file, WAL mode for concurrent readers,
and fast enough for the pilot-scale workloads discussed in
`docs/BENCHMARKS.md`.

For larger deployments this module can be swapped for PostgreSQL by
changing only the connection string; all queries use ANSI-SQL syntax where
possible.

Thread safety
-------------
Every public method acquires a module-level lock before opening a short-
lived connection. This keeps the code simple at the cost of serialising
writes; given the benchmark numbers (~120 writes/sec peak), that is
adequate for pilot scale. For higher throughput, use a connection pool or
move to Postgres.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_limit (
    client_ip    TEXT PRIMARY KEY,
    window_start REAL NOT NULL,
    count        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    type       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    message    TEXT NOT NULL,
    vehicle_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_vehicle ON notifications(vehicle_id);

CREATE TABLE IF NOT EXISTS telemetry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id    TEXT NOT NULL,
    observed_at   INTEGER NOT NULL,
    reading_json  TEXT NOT NULL,
    onchain_tx    TEXT,
    batch_id      INTEGER,
    is_violation  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_telemetry_vehicle_time ON telemetry(vehicle_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_batch ON telemetry(batch_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_violation ON telemetry(is_violation);

CREATE TABLE IF NOT EXISTS merkle_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id      TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    leaf_count      INTEGER NOT NULL,
    merkle_root     TEXT NOT NULL,
    onchain_tx      TEXT,
    leaves_json     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_merkle_vehicle ON merkle_batches(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_merkle_root ON merkle_batches(merkle_root);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT,
    details    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);
"""


class PersistenceStore:
    """Thread-safe SQLite persistence wrapper.

    Instantiate once at backend startup and pass the instance into the
    modules that need it (rate limiter, notification logger, Merkle
    batcher, etc.). A ``None`` path disables persistence entirely — useful
    for unit tests and for the most restrictive ephemeral-only
    deployments.
    """

    def __init__(self, db_path: str | os.PathLike[str] | None) -> None:
        self._lock = threading.Lock()
        self._path: Path | None = None
        if db_path:
            self._path = Path(db_path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    # ─── Connection helpers ──────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        assert self._path is not None
        con = sqlite3.connect(
            str(self._path),
            isolation_level=None,   # autocommit
            timeout=10,
            check_same_thread=False,
        )
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.row_factory = sqlite3.Row
        return con

    def _init_schema(self) -> None:
        with self._lock, self._conn() as con:
            con.executescript(_SCHEMA)

    @property
    def enabled(self) -> bool:
        return self._path is not None

    # ─── Rate limiter ────────────────────────────────────────────────

    def rate_limit_check(self, client_ip: str, max_per_window: int,
                         window_seconds: int) -> tuple[bool, int]:
        """Increment the counter for ``client_ip`` and return
        ``(allowed, current_count)``. If persistence is disabled this
        degrades gracefully to ``(True, 0)`` and the caller should fall
        back to the in-memory limiter.
        """
        if not self.enabled:
            return True, 0
        now = time.time()
        with self._lock, self._conn() as con:
            row = con.execute(
                "SELECT window_start, count FROM rate_limit WHERE client_ip = ?",
                (client_ip,),
            ).fetchone()
            if row is None or (now - row["window_start"] > window_seconds):
                con.execute(
                    "INSERT OR REPLACE INTO rate_limit(client_ip, window_start, count) "
                    "VALUES (?, ?, ?)",
                    (client_ip, now, 1),
                )
                return True, 1
            count = row["count"]
            if count >= max_per_window:
                return False, count
            con.execute(
                "UPDATE rate_limit SET count = count + 1 WHERE client_ip = ?",
                (client_ip,),
            )
            return True, count + 1

    def rate_limit_purge(self, max_age_seconds: int = 3600) -> int:
        """Remove rate-limit rows older than ``max_age_seconds``. Returns
        the number of rows removed. Safe to call periodically from a
        scheduled job."""
        if not self.enabled:
            return 0
        cutoff = time.time() - max_age_seconds
        with self._lock, self._conn() as con:
            cur = con.execute("DELETE FROM rate_limit WHERE window_start < ?", (cutoff,))
            return cur.rowcount or 0

    # ─── Notifications ───────────────────────────────────────────────

    def add_notification(self, ntype: str, message: str,
                         vehicle_id: str = "", severity: str = "info") -> int:
        if not self.enabled:
            return 0
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT INTO notifications(created_at, type, severity, message, vehicle_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), ntype, severity, message, vehicle_id or None),
            )
            return int(cur.lastrowid or 0)

    def recent_notifications(self, limit: int = 100,
                              vehicle_id: str | None = None) -> list[dict]:
        if not self.enabled:
            return []
        with self._lock, self._conn() as con:
            if vehicle_id:
                rows = con.execute(
                    "SELECT id, created_at, type, severity, message, vehicle_id "
                    "FROM notifications WHERE vehicle_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (vehicle_id, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT id, created_at, type, severity, message, vehicle_id "
                    "FROM notifications ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def purge_notifications_older_than(self, days: int) -> int:
        if not self.enabled:
            return 0
        cutoff = int(time.time()) - days * 86400
        with self._lock, self._conn() as con:
            cur = con.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,))
            return cur.rowcount or 0

    # ─── Telemetry cold store ────────────────────────────────────────

    def record_telemetry(self, vehicle_id: str, reading: dict[str, Any],
                         onchain_tx: str | None = None,
                         is_violation: bool = False) -> int:
        if not self.enabled:
            return 0
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT INTO telemetry(vehicle_id, observed_at, reading_json, "
                "onchain_tx, is_violation) VALUES (?, ?, ?, ?, ?)",
                (vehicle_id, int(time.time()), json.dumps(reading, default=str),
                 onchain_tx, 1 if is_violation else 0),
            )
            return int(cur.lastrowid or 0)

    def telemetry_for_vehicle(self, vehicle_id: str,
                              limit: int = 1000,
                              only_violations: bool = False) -> list[dict]:
        if not self.enabled:
            return []
        q = ("SELECT id, observed_at, reading_json, onchain_tx, batch_id, is_violation "
             "FROM telemetry WHERE vehicle_id = ?")
        args: list[Any] = [vehicle_id]
        if only_violations:
            q += " AND is_violation = 1"
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._conn() as con:
            rows = con.execute(q, args).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["reading"] = json.loads(d.pop("reading_json"))
                except Exception:
                    d["reading"] = {}
                out.append(d)
            return out

    # ─── Merkle batches ──────────────────────────────────────────────

    def record_merkle_batch(self, vehicle_id: str, merkle_root: str,
                             leaves: Iterable[str],
                             onchain_tx: str | None = None) -> int:
        if not self.enabled:
            return 0
        leaves_list = list(leaves)
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT INTO merkle_batches(vehicle_id, created_at, leaf_count, "
                "merkle_root, onchain_tx, leaves_json) VALUES (?, ?, ?, ?, ?, ?)",
                (vehicle_id, int(time.time()), len(leaves_list), merkle_root,
                 onchain_tx, json.dumps(leaves_list)),
            )
            batch_id = int(cur.lastrowid or 0)
            # Link buffered telemetry rows to this batch (best-effort).
            con.execute(
                "UPDATE telemetry SET batch_id = ? "
                "WHERE vehicle_id = ? AND batch_id IS NULL AND is_violation = 0",
                (batch_id, vehicle_id),
            )
            return batch_id

    def get_merkle_batch(self, batch_id: int) -> dict | None:
        if not self.enabled:
            return None
        with self._lock, self._conn() as con:
            row = con.execute(
                "SELECT * FROM merkle_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["leaves"] = json.loads(d.pop("leaves_json"))
            except Exception:
                d["leaves"] = []
            return d

    def merkle_batches_for_vehicle(self, vehicle_id: str,
                                    limit: int = 100) -> list[dict]:
        if not self.enabled:
            return []
        with self._lock, self._conn() as con:
            rows = con.execute(
                "SELECT id, created_at, leaf_count, merkle_root, onchain_tx "
                "FROM merkle_batches WHERE vehicle_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (vehicle_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Audit log ───────────────────────────────────────────────────

    def audit(self, actor: str, action: str,
              target: str | None = None,
              details: dict | str | None = None) -> int:
        if not self.enabled:
            return 0
        if isinstance(details, dict):
            details_s = json.dumps(details, default=str)
        elif details is None:
            details_s = None
        else:
            details_s = str(details)
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT INTO audit_log(created_at, actor, action, target, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), actor, action, target, details_s),
            )
            return int(cur.lastrowid or 0)
