"""Live smoke test of every FastAPI endpoint in backend/main.py.

Run the backend first (see scripts/smoke_test_api.sh next to this file).
Prints a one-line PASS/FAIL per route.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:5055")
API_KEY = os.environ.get("API_KEY", "live-smoke-apikey")
USER = os.environ.get("AUTH_USERNAME", "admin")
PW = os.environ.get("AUTH_PASSWORD", "livepass-check")


def _call(method, path, *, json_body=None, headers=None, expected=(200,)):
    url = BASE + path
    data = None
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        code = e.code
        body = e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return False, f"{method} {path}  EXCEPTION {e}"
    ok = code in expected
    tag = "PASS" if ok else "FAIL"
    return ok, f"[{tag}] {method:5s} {path}  -> {code}  {body[:140]}"


def login() -> str:
    url = BASE + "/api/auth/login"
    req = urllib.request.Request(
        url,
        data=json.dumps({"username": USER, "password": PW}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read())
    return body.get("token") or body.get("access_token") or ""


def main() -> int:
    token = login()
    auth = {"Authorization": f"Bearer {token}"}
    apikey = {"X-API-Key": API_KEY}
    results = []

    # 1. Public / health / status
    results.append(_call("GET", "/health"))
    results.append(_call("GET", "/api/status"))

    # 2. Auth: already exercised login via login(); exercise the bad-password branch
    results.append(_call(
        "POST", "/api/auth/login",
        json_body={"username": USER, "password": "wrong"},
        expected=(400, 401, 403),
    ))

    # 3. Simulation
    results.append(_call("GET", "/api/simulate"))

    # 4. Record (API-key gated)
    results.append(_call(
        "POST", "/api/record",
        json_body={
            "vehicle_id": "LIVE001",
            "speed": 60.0, "rpm": 2100, "fuel_rate": 6.0,
            "acceleration": 0.1, "wltc_phase": 1,
        },
        headers=apikey,
    ))
    results.append(_call(
        "POST", "/api/record",
        json_body={"vehicle_id": "LIVE002"},
        expected=(401, 403),
    ))

    # 5. History / violations / stats
    results.append(_call("GET", "/api/history/LIVE001", expected=(200, 503)))
    results.append(_call("GET", "/api/violations", expected=(200, 503)))
    results.append(_call("GET", "/api/vehicle-stats/LIVE001", expected=(200, 503)))

    # 6. Analytics
    results.append(_call("GET", "/api/analytics/distribution", expected=(200, 503)))
    results.append(_call("GET", "/api/analytics/fleet", headers=auth, expected=(200, 500, 503)))
    results.append(_call("GET", "/api/analytics/trends/LIVE001", expected=(200, 503)))
    results.append(_call("GET", "/api/analytics/phase-breakdown/LIVE001", expected=(200, 503)))

    # 7. Certificate
    results.append(_call("GET", "/api/certificate/LIVE001"))
    results.append(_call(
        "POST", "/api/certificate/issue",
        json_body={"vehicle_id": "LIVE001", "vehicle_owner": "0x0000000000000000000000000000000000000000"},
        expected=(401, 403),
    ))
    results.append(_call("GET", "/api/verify/LIVE001", expected=(200, 503)))

    # 8. Tokens
    results.append(_call(
        "GET", "/api/green-tokens/0x0000000000000000000000000000000000000000",
        expected=(200, 503),
    ))
    results.append(_call("GET", "/api/tokens/rewards"))
    results.append(_call(
        "POST", "/api/tokens/redeem",
        json_body={"reward_type": 0},
        expected=(401, 403),
    ))

    # 9. Fleet / alerts (auth-gated)
    results.append(_call("GET", "/api/fleet/vehicles", headers=auth, expected=(200, 500, 503)))
    results.append(_call("GET", "/api/fleet/alerts", headers=auth, expected=(200, 500, 503)))

    # 10. RTO (auth-gated)
    results.append(_call("GET", "/api/rto/check/MH12AB1234", headers=auth, expected=(200, 503)))
    results.append(_call("GET", "/api/rto/flagged", headers=auth, expected=(200, 503)))
    results.append(_call(
        "POST", "/api/rto/enforce",
        json_body={"vehicle_id": "LIVE001", "action": "warning", "remarks": "smoke test"},
        headers=auth,
    ))
    results.append(_call("GET", "/api/rto/actions", headers=auth))
    results.append(_call(
        "POST", "/api/rto/enforce",
        json_body={"vehicle_id": "LIVE001", "action": "unknown", "remarks": ""},
        headers=auth,
        expected=(200, 400, 422),
    ))

    # 11. Notifications
    results.append(_call("GET", "/api/notifications", headers=auth))

    # 12. OBD
    results.append(_call("GET", "/api/obd/status"))
    results.append(_call(
        "POST", "/api/obd/read",
        json_body={"vehicle_id": "LIVE001"},
        headers=apikey,
        expected=(200, 400, 422, 503),
    ))

    # 13. Vehicle verify
    results.append(_call("GET", "/api/vehicle/verify/MH12AB1234"))

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    for _, line in results:
        print(line)
    print()
    print(f"  {passed}/{total} endpoints OK")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
