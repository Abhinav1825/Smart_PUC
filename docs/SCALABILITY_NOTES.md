# SmartPUC — Storage Layer Scalability

## Current Architecture

The persistence layer (`backend/persistence.py`) uses **SQLite with WAL mode**
for durable, thread-safe storage of telemetry, notifications, rate-limiter
counters, and Merkle batch metadata.

## Measured Capacity

- **~120 sequential writes/second** (WAL mode, SSD, single-writer lock)
- **Concurrent readers**: unlimited (WAL allows readers during writes)
- **Database size**: grows at approximately 1 KB per emission record

## Implications

| Deployment scale           | Vehicles × Hz | Writes/sec | Sufficient? |
|----------------------------|---------------|------------|-------------|
| Lab / demo                 | 1 × 1 Hz     | 1          | ✅ Yes      |
| Pilot (single PUC centre)  | 10 × 1 Hz    | 10         | ✅ Yes      |
| District-level pilot       | 50 × 1 Hz    | 50         | ✅ Yes      |
| City-scale deployment      | 500 × 1 Hz   | 500        | ❌ No       |
| State-level production     | 5000 × 1 Hz  | 5000       | ❌ No       |

## Recommended Migration Path for Production

1. **PostgreSQL + TimescaleDB** for time-series telemetry data
   - Hypertable partitioning by `(vehicle_id, time)` for efficient range queries
   - Compression policies for data older than 30 days
2. **Connection pooling** via PgBouncer or SQLAlchemy's built-in pool
3. **Read replicas** for dashboard queries (separate from write path)
4. **Redis** for rate-limiter counters (in-memory, sub-millisecond)

## Design Rationale

SQLite was chosen for the research prototype because:

- **Zero configuration**: no external database server to install or manage
- **Single-file deployment**: the entire database is one file (`smartpuc.db`)
- **Reproducibility**: any reviewer can run the system without setting up Postgres
- **Sufficiency**: pilot-scale workloads (< 50 concurrent vehicles) are well
  within SQLite's capacity

This is a known, documented trade-off — not an oversight.

---

*See also: `backend/persistence.py` module docstring, `docs/ARCHITECTURE_TRADEOFFS.md`*
