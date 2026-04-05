"""
Smart PUC — End-to-End Latency Benchmark
=========================================

Sends signed emission payloads sequentially to a running Smart PUC station
backend and measures the per-stage latency. Produces p50/p95/p99
statistics that match docs/BENCHMARKS.md.

Usage:
    python scripts/bench_latency.py --samples 1000 \
        --station-url http://localhost:5000 \
        --output docs/bench_latency.json

Requirements:
    - docker-compose up (or manual Ganache + backend + contracts deployed)
    - The OBD device private key (set in .env as OBD_DEVICE_PRIVATE_KEY, or
      passed via --device-key)
    - The station API key (if API_KEY is set in .env)

The script purposefully does NOT import the backend modules so it can run
against a remote station as well.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import statistics
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("Error: 'requests' is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    from web3 import Web3
except ImportError:
    print("Error: 'web3' and 'eth_account' are required. Install with: pip install web3", file=sys.stderr)
    sys.exit(1)


def build_signed_payload(
    vehicle_id: str,
    device_privkey: str,
    co2: int = 110000,
    co: int = 800,
    nox: int = 50,
    hc: int = 80,
    pm25: int = 4,
) -> dict[str, Any]:
    """Build and sign an emission payload exactly as the OBD device would."""
    timestamp = int(time.time())
    nonce = "0x" + secrets.token_hex(32)
    # Hash matches EmissionRegistry.getMessageHash()
    message_hash = Web3.solidity_keccak(
        ["string", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256", "bytes32"],
        [vehicle_id, co2, co, nox, hc, pm25, timestamp, nonce],
    )
    message = encode_defunct(message_hash)
    signed = Account.sign_message(message, private_key=device_privkey)
    device_address = Account.from_key(device_privkey).address

    return {
        "vehicle_id": vehicle_id,
        "speed": 45.0,
        "rpm": 2200,
        "fuel_rate": 4.5,
        "fuel_type": "petrol",
        "acceleration": 0.3,
        "co2": co2 / 1000.0,
        "co": co / 1000.0,
        "nox": nox / 1000.0,
        "hc": hc / 1000.0,
        "pm25": pm25 / 1000.0,
        "timestamp": timestamp,
        "nonce": nonce,
        "device_signature": signed.signature.hex(),
        "device_address": device_address,
    }


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0}
    s = sorted(values)
    n = len(s)

    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return s[idx]

    return {
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "min": s[0],
        "max": s[-1],
        "mean": statistics.fmean(s),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Smart PUC end-to-end latency benchmark.")
    ap.add_argument("--station-url", default="http://localhost:5000")
    ap.add_argument("--samples", type=int, default=1000)
    ap.add_argument("--vehicle-id", default="BENCHLAT0001")
    ap.add_argument("--device-key", default=None,
                    help="OBD device private key (hex, 0x-prefixed). Defaults to .env value.")
    ap.add_argument("--api-key", default=None,
                    help="Station API key (if set). Defaults to .env value.")
    ap.add_argument("--output", default="docs/bench_latency.json")
    ap.add_argument("--warmup", type=int, default=10,
                    help="Warmup requests excluded from statistics.")
    args = ap.parse_args()

    # Load .env if present
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip() and not line.strip().startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    device_key = args.device_key or os.environ.get("OBD_DEVICE_PRIVATE_KEY") or ""
    api_key = args.api_key or os.environ.get("API_KEY") or ""

    if not device_key:
        print("Error: OBD device private key not supplied (via --device-key or OBD_DEVICE_PRIVATE_KEY)", file=sys.stderr)
        return 2
    if not device_key.startswith("0x"):
        device_key = "0x" + device_key

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    url = args.station_url.rstrip("/") + "/api/record"

    print(f"Target: {url}")
    print(f"Samples: {args.samples} (plus {args.warmup} warmup)")

    # Warmup
    for i in range(args.warmup):
        payload = build_signed_payload(args.vehicle_id, device_key)
        try:
            requests.post(url, json=payload, headers=headers, timeout=30)
        except Exception:
            pass

    t_sign: list[float] = []
    t_http: list[float] = []
    t_total: list[float] = []
    errors = 0

    for i in range(args.samples):
        t0 = time.perf_counter()
        payload = build_signed_payload(args.vehicle_id, device_key)
        t1 = time.perf_counter()
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=30)
            t2 = time.perf_counter()
            if r.status_code >= 400:
                errors += 1
        except Exception:
            errors += 1
            continue

        t_sign.append((t1 - t0) * 1000.0)
        t_http.append((t2 - t1) * 1000.0)
        t_total.append((t2 - t0) * 1000.0)

        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{args.samples} … p50 total = {percentiles(t_total)['p50']:.1f} ms")

    report = {
        "generated_at": int(time.time()),
        "station_url": args.station_url,
        "samples": args.samples,
        "errors": errors,
        "t_sign_ms": percentiles(t_sign),
        "t_http_plus_chain_ms": percentiles(t_http),
        "t_total_ms": percentiles(t_total),
        "note": "t_http_plus_chain_ms includes backend processing, fraud detection, and chain inclusion.",
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print("\nResults")
    print("=======")
    print(f"Errors: {errors}/{args.samples}")
    print(f"t_sign (ms):                 p50 {report['t_sign_ms']['p50']:.2f}  p95 {report['t_sign_ms']['p95']:.2f}  p99 {report['t_sign_ms']['p99']:.2f}")
    print(f"t_http+fraud+chain (ms):     p50 {report['t_http_plus_chain_ms']['p50']:.2f}  p95 {report['t_http_plus_chain_ms']['p95']:.2f}  p99 {report['t_http_plus_chain_ms']['p99']:.2f}")
    print(f"t_total (ms):                p50 {report['t_total_ms']['p50']:.2f}  p95 {report['t_total_ms']['p95']:.2f}  p99 {report['t_total_ms']['p99']:.2f}")
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
