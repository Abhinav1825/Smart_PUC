"""
Smart PUC — Concurrent Throughput Benchmark
============================================

Sweeps worker counts and measures sustained records-per-second against a
running Smart PUC station backend. Produces the data table in
docs/BENCHMARKS.md §3.

Usage:
    python scripts/bench_throughput.py --workers 1,4,8,16,32 \
        --samples-per-worker 200 \
        --station-url http://localhost:5000 \
        --output docs/bench_throughput.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Reuse the payload builder from the sequential benchmark
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_latency import build_signed_payload, percentiles  # type: ignore  # noqa: E402

try:
    import requests
except ImportError:
    print("Error: 'requests' is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


def worker_task(url: str, headers: dict, device_key: str, vehicle_id: str, samples: int) -> list[float]:
    latencies: list[float] = []
    for _ in range(samples):
        payload = build_signed_payload(vehicle_id, device_key)
        t0 = time.perf_counter()
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=30)
            t1 = time.perf_counter()
            if r.status_code < 400:
                latencies.append((t1 - t0) * 1000.0)
        except Exception:
            pass
    return latencies


def run_once(station_url: str, api_key: str, device_key: str, workers: int, samples_per_worker: int) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    url = station_url.rstrip("/") + "/api/record"

    start = time.perf_counter()
    all_latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(worker_task, url, headers, device_key, f"BENCHTH{workers:02d}W{i:02d}", samples_per_worker)
            for i in range(workers)
        ]
        for f in as_completed(futures):
            all_latencies.extend(f.result())
    elapsed = time.perf_counter() - start

    successful = len(all_latencies)
    tps = successful / elapsed if elapsed > 0 else 0.0
    pct = percentiles(all_latencies)

    return {
        "workers": workers,
        "samples_attempted": workers * samples_per_worker,
        "samples_succeeded": successful,
        "elapsed_seconds": round(elapsed, 3),
        "sustained_tps": round(tps, 2),
        "latency_ms": {k: round(v, 2) for k, v in pct.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Smart PUC concurrent throughput benchmark.")
    ap.add_argument("--station-url", default="http://localhost:5000")
    ap.add_argument("--workers", default="1,4,8,16,32", help="Comma-separated worker counts to sweep.")
    ap.add_argument("--samples-per-worker", type=int, default=200)
    ap.add_argument("--device-key", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--output", default="docs/bench_throughput.json")
    args = ap.parse_args()

    # Load .env
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip() and not line.strip().startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    device_key = args.device_key or os.environ.get("OBD_DEVICE_PRIVATE_KEY") or ""
    api_key = args.api_key or os.environ.get("API_KEY") or ""

    if not device_key:
        print("Error: OBD device private key not supplied.", file=sys.stderr)
        return 2
    if not device_key.startswith("0x"):
        device_key = "0x" + device_key

    worker_counts = [int(x.strip()) for x in args.workers.split(",") if x.strip()]

    results = []
    for w in worker_counts:
        print(f"\nWorkers = {w} (samples/worker = {args.samples_per_worker})")
        res = run_once(args.station_url, api_key, device_key, w, args.samples_per_worker)
        print(f"  succeeded = {res['samples_succeeded']}/{res['samples_attempted']}")
        print(f"  sustained TPS = {res['sustained_tps']}")
        print(f"  latency p50/p95 = {res['latency_ms']['p50']} / {res['latency_ms']['p95']} ms")
        results.append(res)

    report = {
        "generated_at": int(time.time()),
        "station_url": args.station_url,
        "sweeps": results,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print("\nThroughput summary")
    print("==================")
    print("Workers | Succeeded | TPS   | p50 ms | p95 ms")
    print("--------|-----------|-------|--------|-------")
    for r in results:
        print(f"{r['workers']:>7} | {r['samples_succeeded']:>9} | {r['sustained_tps']:>5.1f} | {r['latency_ms']['p50']:>6.1f} | {r['latency_ms']['p95']:>6.1f}")
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
