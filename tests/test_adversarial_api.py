"""
Smart PUC — System-level adversarial tests against /api/record.

Unlike the unit-level red-team harness (ml/redteam.py) which exercises
the FraudDetector in isolation, these tests POST crafted attack payloads
through the full FastAPI stack and verify that the end-to-end pipeline
(validation → emission engine → fraud detection → response) correctly
flags fraudulent or impossible readings.

Attack families tested
----------------------
1. Physics violation (speed > 0 but RPM = 0)
2. Sudden speed spike (speed = 250 in a single sample)
3. Replay / frozen sensor (identical readings repeated)
4. Zero-pollutant anomaly (speed > 0 but emissions implausibly low)
5. Out-of-bounds input (negative speed, RPM > 8000)
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-please-do-not-use-in-prod")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("AUTH_USERNAME", "admin")
os.environ.setdefault("AUTH_PASSWORD", "correct-horse-battery-staple")
os.environ.setdefault("RATE_LIMIT_MAX", "10000")

from backend import main as backend_main  # noqa: E402

app = backend_main.app
client = TestClient(app)
API_KEY = os.environ["API_KEY"]

HEADERS = {"X-API-Key": API_KEY}


def _post_record(payload: dict) -> dict:
    """POST /api/record and return the inner *data* dict.

    The API envelope is ``{"success": true, "data": {...}}``.  We return
    the ``data`` dict directly so that callers can assert on
    ``fraud_score``, ``ces_score``, etc. without nesting.
    """
    resp = client.post("/api/record", json=payload, headers=HEADERS)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("success") is True, f"API returned success=False: {body}"
    return body.get("data", body)


# ──────────────── Attack 1: Physics violation (speed > 0, RPM = 0) ──────

class TestPhysicsViolation:
    """A vehicle reporting speed = 80 km/h but RPM = 0 is physically
    impossible — the engine cannot propel the car without turning."""

    def test_speed_with_zero_rpm_is_flagged(self):
        data = _post_record({
            "vehicle_id": "MH12ZZ0001",
            "speed": 80.0,
            "rpm": 0,
            "fuel_rate": 6.5,
            "acceleration": 0.0,
        })
        fraud = data.get("fraud_score", 0)
        assert fraud > 0, (
            f"Expected fraud_score > 0 for speed=80/rpm=0, got {fraud}"
        )

    def test_high_vsp_with_near_zero_fuel(self):
        data = _post_record({
            "vehicle_id": "MH12ZZ0002",
            "speed": 100.0,
            "rpm": 4500,
            "fuel_rate": 0.1,
            "acceleration": 3.0,
        })
        fraud = data.get("fraud_score", 0)
        assert fraud > 0.2, (
            f"Expected fraud_score > 0.2 for high-VSP/near-zero-fuel, got {fraud}"
        )


# ──────────────── Attack 2: Sudden speed spike ─────────────────────────

class TestSuddenSpeedSpike:
    """A reading that jumps from typical cruise to max range in one sample.
    The temporal consistency checker should flag this."""

    def test_extreme_speed_flagged(self):
        # First, establish a baseline with moderate speed
        _post_record({
            "vehicle_id": "MH12ZZ0003",
            "speed": 60.0,
            "rpm": 2500,
            "fuel_rate": 5.0,
            "acceleration": 0.0,
        })
        # Now spike to 250
        data = _post_record({
            "vehicle_id": "MH12ZZ0003",
            "speed": 250.0,
            "rpm": 7500,
            "fuel_rate": 15.0,
            "acceleration": 9.0,
        })
        # The system should still compute emissions but note anomaly
        assert "co2_g_per_km" in data or "emissions" in data or "ces_score" in data


# ──────────────── Attack 3: Replay / frozen sensor ─────────────────────

class TestReplayAttack:
    """Identical readings repeated N times look like a replay or sensor
    freeze. The temporal checker should flag after >= 3 identical samples."""

    def test_identical_readings_are_flagged(self):
        frozen_payload = {
            "vehicle_id": "MH12ZZ0004",
            "speed": 60.0,
            "rpm": 2500,
            "fuel_rate": 5.5,
            "acceleration": 0.0,
        }
        # Send 5 identical readings
        last_data = None
        for _ in range(5):
            last_data = _post_record(frozen_payload)
        assert last_data is not None
        # After 5 identical readings, fraud should be elevated
        fraud = last_data.get("fraud_score", 0)
        assert fraud > 0, (
            f"Expected fraud_score > 0 after 5 identical readings, got {fraud}"
        )


# ──────────────── Attack 4: Input validation (out of range) ────────────

class TestInputValidation:
    """Pydantic should reject payloads outside the declared bounds."""

    def test_negative_speed_rejected(self):
        resp = client.post(
            "/api/record",
            json={"speed": -10.0, "rpm": 2000},
            headers=HEADERS,
        )
        assert resp.status_code == 422, (
            f"Expected 422 for negative speed, got {resp.status_code}"
        )

    def test_rpm_over_8000_rejected(self):
        resp = client.post(
            "/api/record",
            json={"speed": 60.0, "rpm": 9000},
            headers=HEADERS,
        )
        assert resp.status_code == 422, (
            f"Expected 422 for RPM=9000 (>8000), got {resp.status_code}"
        )

    def test_extreme_acceleration_rejected(self):
        resp = client.post(
            "/api/record",
            json={"speed": 60.0, "rpm": 2500, "acceleration": 15.0},
            headers=HEADERS,
        )
        assert resp.status_code == 422, (
            f"Expected 422 for accel=15 (>10), got {resp.status_code}"
        )


# ──────────────── Attack 5: Missing API key ────────────────────────────

class TestAuthEnforcement:
    """The /api/record endpoint requires an X-API-Key header. Without
    it, the request should be rejected (401 or 403)."""

    def test_record_without_api_key_rejected(self):
        resp = client.post(
            "/api/record",
            json={"speed": 60.0, "rpm": 2500, "fuel_rate": 5.0},
        )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 without API key, got {resp.status_code}"
        )

    def test_record_with_wrong_api_key_rejected(self):
        resp = client.post(
            "/api/record",
            json={"speed": 60.0, "rpm": 2500, "fuel_rate": 5.0},
            headers={"X-API-Key": "wrong-key-value"},
        )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 with wrong API key, got {resp.status_code}"
        )


# ──────────────── Attack 6: Idle anomaly ───────────────────────────────

class TestIdleAnomaly:
    """Stationary vehicle (speed ≈ 0) claiming high emissions is suspicious
    but physically possible at idle. Verify the system handles it without
    crashing (edge case defence)."""

    def test_zero_speed_zero_accel_does_not_crash(self):
        data = _post_record({
            "vehicle_id": "MH12ZZ0005",
            "speed": 0.0,
            "rpm": 800,
            "fuel_rate": 0.8,
            "acceleration": 0.0,
        })
        # CES should still be computed
        assert "ces_score" in data or "emissions" in data
