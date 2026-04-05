"""
Smart PUC — End-to-end business-flow audit.

Exercises the full happy + recovery path against a running local stack:
  1. Login (JWT)
  2. Post 4 PASS emission records (API-Key)
  3. Confirm certificate eligibility (consecutive_passes >= 3)
  4. Issue certificate (auth)
  5. Public verify
  6. GreenToken balance
  7. Reward catalogue
  8. Revoke certificate (auth)  <- admin-only, historically failed
  9. Verify post-revoke (must report invalid)
 10. RTO enforce (auth)
 11. Notifications list (auth)
 12. Fleet vehicles list (auth)

Any failed assertion halts the run with the offending endpoint + payload.
Every request uses the credentials/keys already present in the project .env
so no hard-coded secrets are needed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

# Force UTF-8 on Windows so unicode glyphs in status lines do not crash cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

# Parse .env without external deps
env: dict[str, str] = {}
for line in ENV.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    env[k.strip()] = v.strip()

BASE = "http://127.0.0.1:5000"
API_KEY = env.get("API_KEY", "")
USER = env.get("AUTH_USERNAME", "admin")
PASS = env.get("AUTH_PASSWORD", "admin")


def J(method: str, path: str, *, body: Any = None, headers: dict | None = None) -> tuple[int, Any]:
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    r = requests.request(method, BASE + path, json=body, headers=hdrs, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text}
    return r.status_code, data


def step(n: int, label: str) -> None:
    print(f"\n── Step {n}: {label}")


def ok(msg: str) -> None:
    print(f"   \u2713 {msg}")


def fail(msg: str) -> None:
    print(f"   \u2717 {msg}")
    sys.exit(1)


def main() -> None:
    vid = f"E2E{int(time.time())}"
    print(f"Smart PUC end-to-end audit — vehicle {vid}")

    # 1. Login
    step(1, "login")
    code, out = J("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if code != 200 or not out.get("token"):
        fail(f"login failed: {code} {out}")
    token = out["token"]
    auth = {"Authorization": f"Bearer {token}"}
    ok(f"token len={len(token)}")

    # 2. Post 4 PASS records (PASS = clean: low speed, low rpm, low fuel)
    step(2, "post 4 PASS emission records")
    headers_dev = {"X-API-Key": API_KEY}
    for i in range(4):
        payload = {
            "vehicle_id": vid,
            "speed": 40.0 + i,
            "rpm": 1500,
            "fuel_rate": 3.0,
            "acceleration": 0.1,
            "wltc_phase": 1,
            "fuel_type": "petrol",
        }
        code, out = J("POST", "/api/record", body=payload, headers=headers_dev)
        if code != 200 or not (out.get("success") or out.get("vehicle_id") == vid):
            fail(f"record {i} failed: {code} {out}")
    ok("4 records posted")

    # 3. Eligibility check
    step(3, "certificate eligibility")
    code, out = J("GET", f"/api/vehicle-stats/{vid}")
    if code != 200:
        fail(f"vehicle-stats failed: {code} {out}")
    stats = out.get("stats", out)
    elig = stats.get("certificate_eligible") or {}
    cp = elig.get("consecutive_passes") or stats.get("consecutive_passes") or 0
    ok(f"consecutive_passes={cp}")
    if cp < 3:
        fail(f"not eligible yet (cp={cp})")

    # 4. Issue certificate
    step(4, "issue certificate")
    owner = "0x41B687270944AB78dcFB19EcF6a189975D61753F"  # vehicleOwner signer[3]
    code, out = J(
        "POST",
        "/api/certificate/issue",
        body={"vehicle_id": vid, "vehicle_owner": owner},
        headers=auth,
    )
    if code != 200 or not out.get("success"):
        fail(f"issue failed: {code} {out}")
    result = out.get("result", {})
    ok(f"issue tx={str(result.get('tx_hash',''))[:24]}... block={result.get('block_number')}")

    # 5. Public verify (also the source of our token_id)
    step(5, "public verify")
    code, out = J("GET", f"/api/verify/{vid}")
    if code != 200:
        fail(f"verify failed: {code} {out}")
    vfy = out.get("verification", out)
    valid = vfy.get("valid")
    token_id = vfy.get("token_id")
    ok(f"valid={valid} tokenId={token_id} avg_ces={vfy.get('average_ces')}")
    if not valid:
        fail("expected cert to be valid right after issue")

    # 6. GreenToken balance
    step(6, "GreenToken balance")
    code, out = J("GET", f"/api/green-tokens/{owner}")
    if code != 200:
        fail(f"gct balance failed: {code} {out}")
    tokens = out.get("tokens", out)
    bal = tokens.get("balance", 0)
    earned = tokens.get("earned", 0)
    ok(f"balance={bal} earned={earned}")
    if float(bal) <= 0:
        fail("expected non-zero GCT balance after issuance")

    # 7. Reward catalogue
    step(7, "reward catalogue")
    code, out = J("GET", "/api/tokens/rewards")
    if code != 200:
        fail(f"rewards failed: {code} {out}")
    rewards = out.get("rewards", out if isinstance(out, list) else [])
    ok(f"{len(rewards)} rewards")
    if len(rewards) != 4:
        fail(f"expected 4 rewards (contract enum), got {len(rewards)}")

    # 7b. Tokens redeem (backend path — marketplace.html uses client-side ethers)
    step(70, "tokens redeem (reward_type=0)")
    code, out = J("POST", "/api/tokens/redeem", body={"reward_type": 0}, headers=auth)
    # Backend burns from the station account, which may have no GCT. Either
    # a successful burn (200/success) or a graceful 'insufficient balance'
    # error is acceptable — the assertion here is that the schema + arity
    # are correct (no 422, no TypeError).
    if code == 422:
        fail(f"redeem payload schema broke: {out}")
    if code == 500 and "positional argument" in str(out):
        fail(f"redeem arity regression: {out}")
    ok(f"redeem status={code} success={out.get('success')}")

    # 8. Revoke certificate — PRIOR BLOCKER
    step(8, "revoke certificate (admin-only)")
    code, out = J(
        "POST",
        "/api/certificate/revoke",
        body={"token_id": int(token_id), "reason": "E2E audit revocation test"},
        headers=auth,
    )
    if code != 200 or not out.get("success"):
        fail(f"revoke failed: {code} {out}")
    rres = out.get("result", out)
    ok(f"revoked tx={str(rres.get('tx_hash',''))[:24]}... block={rres.get('block_number')}")

    # 9. Verify post-revoke
    step(9, "post-revoke verify")
    code, out = J("GET", f"/api/verify/{vid}")
    if code != 200:
        fail(f"verify failed: {code} {out}")
    vfy = out.get("verification", out)
    if vfy.get("valid"):
        fail(f"cert still reports valid after revoke: {out}")
    ok(f"valid={vfy.get('valid')} revoked={vfy.get('revoked')}")

    # 10. RTO enforce
    step(10, "RTO enforce")
    code, out = J(
        "POST",
        "/api/rto/enforce",
        body={"vehicle_id": vid, "action": "inspection", "remarks": "E2E audit"},
        headers=auth,
    )
    if code != 200 or not out.get("success"):
        fail(f"rto enforce failed: {code} {out}")
    ok("enforced")

    # 11. Notifications
    step(11, "notifications list")
    code, out = J("GET", "/api/notifications", headers=auth)
    if code != 200:
        fail(f"notifications failed: {code} {out}")
    notes = out.get("notifications", out if isinstance(out, list) else [])
    ok(f"{len(notes)} notifications")

    # 12. Fleet vehicles
    step(12, "fleet vehicles list")
    code, out = J("GET", "/api/fleet/vehicles", headers=auth)
    if code != 200:
        fail(f"fleet failed: {code} {out}")
    vehicles = out.get("vehicles", out if isinstance(out, list) else [])
    ok(f"{len(vehicles)} vehicles in fleet")

    print("\nALL 12 STEPS PASSED ✓")


if __name__ == "__main__":
    main()
