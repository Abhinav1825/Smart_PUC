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
possible.  See ``docs/SCALABILITY_NOTES.md`` for capacity measurements and
the recommended migration path.

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

CREATE TABLE IF NOT EXISTS chain_outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   INTEGER NOT NULL,
    vehicle_id   TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    onchain_tx   TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON chain_outbox(status);

CREATE TABLE IF NOT EXISTS vehicle_health_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    report_date TEXT NOT NULL,
    period_days INTEGER DEFAULT 7,
    ces_mean REAL,
    ces_slope REAL,
    ces_max REAL,
    co2_mean REAL,
    nox_mean REAL,
    co_mean REAL,
    hc_mean REAL,
    pm25_mean REAL,
    driving_score REAL,
    degradation_risk TEXT DEFAULT 'low',
    tier TEXT DEFAULT 'Unclassified',
    report_json TEXT,
    UNIQUE(vehicle_id, report_date)
);
CREATE INDEX IF NOT EXISTS idx_health_vehicle ON vehicle_health_reports(vehicle_id);

CREATE TABLE IF NOT EXISTS degradation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    detected_at INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT DEFAULT 'warning',
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_degrad_vehicle ON degradation_events(vehicle_id);

CREATE TABLE IF NOT EXISTS privacy_consent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    consent_type TEXT NOT NULL,
    granted INTEGER NOT NULL DEFAULT 1,
    granted_at INTEGER NOT NULL,
    revoked_at INTEGER,
    ip_address TEXT,
    UNIQUE(vehicle_id, consent_type)
);

CREATE TABLE IF NOT EXISTS erasure_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    completed_at INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    data_types TEXT NOT NULL DEFAULT 'all',
    requester_ip TEXT
);

CREATE TABLE IF NOT EXISTS data_retention_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purged_at INTEGER NOT NULL,
    data_type TEXT NOT NULL,
    records_purged INTEGER NOT NULL DEFAULT 0,
    retention_days INTEGER NOT NULL
);
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

    # ─── Chain outbox (offline queue for failed chain writes) ────────

    def enqueue_chain_write(self, vehicle_id: str, payload: dict) -> int:
        """Queue a chain write for later retry when the RPC is down."""
        if not self.enabled:
            return 0
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT INTO chain_outbox(created_at, vehicle_id, payload_json, status) "
                "VALUES (?, ?, ?, 'pending')",
                (int(time.time()), vehicle_id, json.dumps(payload, default=str)),
            )
            return int(cur.lastrowid or 0)

    def get_pending_chain_writes(self, limit: int = 50) -> list[dict]:
        """Retrieve pending outbox entries for retry."""
        if not self.enabled:
            return []
        with self._lock, self._conn() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT id, vehicle_id, payload_json, attempts "
                "FROM chain_outbox WHERE status = 'pending' "
                "ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_chain_write_done(self, outbox_id: int, tx_hash: str) -> None:
        """Mark an outbox entry as successfully submitted."""
        if not self.enabled:
            return
        with self._lock, self._conn() as con:
            con.execute(
                "UPDATE chain_outbox SET status = 'done', onchain_tx = ?, "
                "attempts = attempts + 1 WHERE id = ?",
                (tx_hash, outbox_id),
            )

    def mark_chain_write_failed(self, outbox_id: int, error: str) -> None:
        """Record a failed retry attempt on an outbox entry."""
        if not self.enabled:
            return
        with self._lock, self._conn() as con:
            con.execute(
                "UPDATE chain_outbox SET attempts = attempts + 1, "
                "last_error = ? WHERE id = ?",
                (error, outbox_id),
            )

    # ─── Vehicle health reports ─────────────────────────────────────────

    def store_health_report(self, vehicle_id: str, report_date: str,
                            report_data: dict) -> int:
        """Insert or replace a weekly health report."""
        if not self.enabled:
            return 0
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT OR REPLACE INTO vehicle_health_reports("
                "vehicle_id, report_date, period_days, ces_mean, ces_slope, "
                "ces_max, co2_mean, nox_mean, co_mean, hc_mean, pm25_mean, "
                "driving_score, degradation_risk, tier, report_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    vehicle_id,
                    report_date,
                    report_data.get("period_days", 7),
                    report_data.get("ces_mean"),
                    report_data.get("ces_slope"),
                    report_data.get("ces_max"),
                    report_data.get("co2_mean"),
                    report_data.get("nox_mean"),
                    report_data.get("co_mean"),
                    report_data.get("hc_mean"),
                    report_data.get("pm25_mean"),
                    report_data.get("driving_score"),
                    report_data.get("degradation_risk", "low"),
                    report_data.get("tier", "Unclassified"),
                    json.dumps(report_data, default=str),
                ),
            )
            return int(cur.lastrowid or 0)

    def get_health_reports(self, vehicle_id: str, limit: int = 12) -> list[dict]:
        """Get latest N health reports for a vehicle."""
        if not self.enabled:
            return []
        with self._lock, self._conn() as con:
            rows = con.execute(
                "SELECT id, vehicle_id, report_date, period_days, ces_mean, "
                "ces_slope, ces_max, co2_mean, nox_mean, co_mean, hc_mean, "
                "pm25_mean, driving_score, degradation_risk, tier, report_json "
                "FROM vehicle_health_reports WHERE vehicle_id = ? "
                "ORDER BY report_date DESC LIMIT ?",
                (vehicle_id, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["report"] = json.loads(d.pop("report_json"))
                except Exception:
                    d["report"] = {}
                out.append(d)
            return out

    # ─── Degradation events ─────────────────────────────────────────────

    def store_degradation_event(self, vehicle_id: str, event_type: str,
                                severity: str, details: dict | str | None = None) -> int:
        """Record a degradation detection event."""
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
                "INSERT INTO degradation_events("
                "vehicle_id, detected_at, event_type, severity, details_json"
                ") VALUES (?, ?, ?, ?, ?)",
                (vehicle_id, int(time.time()), event_type, severity, details_s),
            )
            return int(cur.lastrowid or 0)

    def get_degradation_events(self, vehicle_id: str, limit: int = 50) -> list[dict]:
        """Get degradation events for a vehicle."""
        if not self.enabled:
            return []
        with self._lock, self._conn() as con:
            rows = con.execute(
                "SELECT id, vehicle_id, detected_at, event_type, severity, details_json "
                "FROM degradation_events WHERE vehicle_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (vehicle_id, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["details"] = json.loads(d.pop("details_json")) if d.get("details_json") else {}
                except Exception:
                    d["details"] = {}
                out.append(d)
            return out

    # ─── DPDP Act Privacy Compliance ───────────────────────────────────

    def record_consent(self, vehicle_id: str, consent_type: str,
                       granted: bool = True,
                       ip_address: str | None = None) -> int:
        """Record or update a privacy consent decision for a vehicle.

        Compliant with the Digital Personal Data Protection (DPDP) Act 2023,
        Section 6 -- consent must be free, specific, informed, unconditional,
        and unambiguous.  Each ``consent_type`` (e.g. ``"telemetry_collection"``,
        ``"data_sharing"``, ``"analytics"``) is stored independently so that
        data principals can grant granular permissions.

        Returns the row id of the consent record.
        """
        if not self.enabled:
            return 0
        now = int(time.time())
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT INTO privacy_consent"
                "(vehicle_id, consent_type, granted, granted_at, ip_address) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(vehicle_id, consent_type) DO UPDATE SET "
                "granted = excluded.granted, granted_at = excluded.granted_at, "
                "revoked_at = NULL, ip_address = excluded.ip_address",
                (vehicle_id, consent_type, 1 if granted else 0, now, ip_address),
            )
            return int(cur.lastrowid or 0)

    def get_consent(self, vehicle_id: str) -> list[dict]:
        """Return all consent records for a vehicle (active and revoked)."""
        if not self.enabled:
            return []
        with self._lock, self._conn() as con:
            rows = con.execute(
                "SELECT id, vehicle_id, consent_type, granted, granted_at, "
                "revoked_at, ip_address FROM privacy_consent "
                "WHERE vehicle_id = ? ORDER BY id",
                (vehicle_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def revoke_consent(self, vehicle_id: str, consent_type: str) -> bool:
        """Revoke a previously granted consent.

        Per DPDP Act Section 6(6), the data principal may withdraw consent
        at any time with the ease of giving it.  Returns ``True`` if a
        matching active consent was found and revoked.
        """
        if not self.enabled:
            return False
        now = int(time.time())
        with self._lock, self._conn() as con:
            cur = con.execute(
                "UPDATE privacy_consent SET granted = 0, revoked_at = ? "
                "WHERE vehicle_id = ? AND consent_type = ? AND granted = 1",
                (now, vehicle_id, consent_type),
            )
            return (cur.rowcount or 0) > 0

    def request_erasure(self, vehicle_id: str, data_types: str = "all",
                        requester_ip: str | None = None) -> int:
        """File a right-to-erasure request (DPDP Act Section 12).

        The request is logged with ``status='pending'`` and must be
        fulfilled by calling :meth:`execute_erasure` within the statutory
        time-frame.  Returns the erasure request id.
        """
        if not self.enabled:
            return 0
        now = int(time.time())
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT INTO erasure_requests"
                "(vehicle_id, requested_at, status, data_types, requester_ip) "
                "VALUES (?, ?, 'pending', ?, ?)",
                (vehicle_id, now, data_types, requester_ip),
            )
            return int(cur.lastrowid or 0)

    def execute_erasure(self, vehicle_id: str) -> dict:
        """Execute data erasure for a vehicle.

        Deletes telemetry, notifications, and health reports associated
        with the vehicle.  Updates the earliest pending erasure request to
        ``'completed'``.  Returns a dict with counts of purged records per
        table, suitable for audit logging.
        """
        if not self.enabled:
            return {}
        now = int(time.time())
        counts: dict[str, int] = {}
        with self._lock, self._conn() as con:
            # Delete telemetry
            cur = con.execute(
                "DELETE FROM telemetry WHERE vehicle_id = ?", (vehicle_id,)
            )
            counts["telemetry"] = cur.rowcount or 0

            # Delete notifications
            cur = con.execute(
                "DELETE FROM notifications WHERE vehicle_id = ?", (vehicle_id,)
            )
            counts["notifications"] = cur.rowcount or 0

            # Delete health reports
            cur = con.execute(
                "DELETE FROM vehicle_health_reports WHERE vehicle_id = ?",
                (vehicle_id,),
            )
            counts["health_reports"] = cur.rowcount or 0

            # Delete degradation events
            cur = con.execute(
                "DELETE FROM degradation_events WHERE vehicle_id = ?",
                (vehicle_id,),
            )
            counts["degradation_events"] = cur.rowcount or 0

            # Mark the earliest pending erasure request as completed
            con.execute(
                "UPDATE erasure_requests SET status = 'completed', "
                "completed_at = ? WHERE vehicle_id = ? AND status = 'pending' "
                "AND id = ("
                "  SELECT id FROM erasure_requests "
                "  WHERE vehicle_id = ? AND status = 'pending' "
                "  ORDER BY id ASC LIMIT 1"
                ")",
                (now, vehicle_id, vehicle_id),
            )
            counts["vehicle_id"] = vehicle_id  # type: ignore[assignment]
            return counts

    def get_erasure_requests(self, status: str | None = None) -> list[dict]:
        """Return erasure requests, optionally filtered by status."""
        if not self.enabled:
            return []
        with self._lock, self._conn() as con:
            if status:
                rows = con.execute(
                    "SELECT id, vehicle_id, requested_at, completed_at, "
                    "status, data_types, requester_ip "
                    "FROM erasure_requests WHERE status = ? ORDER BY id DESC",
                    (status,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT id, vehicle_id, requested_at, completed_at, "
                    "status, data_types, requester_ip "
                    "FROM erasure_requests ORDER BY id DESC",
                ).fetchall()
            return [dict(r) for r in rows]

    def purge_old_data(self, retention_days: int = 365) -> dict:
        """Auto-purge telemetry records older than the retention period.

        Implements the storage-limitation principle under DPDP Act
        Section 8(7) -- personal data shall not be retained beyond the
        period necessary for the purpose.  Each purge is logged to the
        ``data_retention_log`` table for audit trail.

        Returns a dict with counts of purged records per data type.
        """
        if not self.enabled:
            return {}
        cutoff = int(time.time()) - retention_days * 86400
        now = int(time.time())
        counts: dict[str, int] = {}
        with self._lock, self._conn() as con:
            # Purge old telemetry
            cur = con.execute(
                "DELETE FROM telemetry WHERE observed_at < ?", (cutoff,)
            )
            counts["telemetry"] = cur.rowcount or 0
            con.execute(
                "INSERT INTO data_retention_log"
                "(purged_at, data_type, records_purged, retention_days) "
                "VALUES (?, 'telemetry', ?, ?)",
                (now, counts["telemetry"], retention_days),
            )

            # Purge old notifications
            cur = con.execute(
                "DELETE FROM notifications WHERE created_at < ?", (cutoff,)
            )
            counts["notifications"] = cur.rowcount or 0
            con.execute(
                "INSERT INTO data_retention_log"
                "(purged_at, data_type, records_purged, retention_days) "
                "VALUES (?, 'notifications', ?, ?)",
                (now, counts["notifications"], retention_days),
            )

            # Purge old audit log entries
            cur = con.execute(
                "DELETE FROM audit_log WHERE created_at < ?", (cutoff,)
            )
            counts["audit_log"] = cur.rowcount or 0
            con.execute(
                "INSERT INTO data_retention_log"
                "(purged_at, data_type, records_purged, retention_days) "
                "VALUES (?, 'audit_log', ?, ?)",
                (now, counts["audit_log"], retention_days),
            )

            return counts
