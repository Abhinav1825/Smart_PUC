"""
SmartPUC -- Phase 3 API endpoint tests for tiered compliance and health reporting.

Tests the new endpoints added in Phase 3:
  - GET /api/vehicle/{vehicle_id}/tier
  - GET /api/vehicle/{vehicle_id}/health-report
  - GET /api/vehicle/{vehicle_id}/degradation
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

# Make the backend importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Seed environment before the module is imported
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-please-do-not-use-in-prod")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("AUTH_USERNAME", "admin")
os.environ.setdefault("AUTH_PASSWORD", "correct-horse-battery-staple")
os.environ.setdefault("RATE_LIMIT_MAX", "10000")

from backend import main as backend_main  # noqa: E402

app = backend_main.app
client = TestClient(app)

VID = "MH12AB1234"


class TestVehicleTier:
    """GET /api/vehicle/{vehicle_id}/tier"""

    def test_returns_tier_dict(self):
        resp = client.get(f"/api/vehicle/{VID}/tier")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "tier" in data
        assert "tier_name" in data
        assert "validity_days" in data
        assert "next_puc_due" in data

    def test_tier_name_is_valid_string(self):
        resp = client.get(f"/api/vehicle/{VID}/tier")
        data = resp.json()
        assert data["tier_name"] in ("Gold", "Silver", "Bronze", "Unclassified")

    def test_validity_days_positive(self):
        resp = client.get(f"/api/vehicle/{VID}/tier")
        data = resp.json()
        assert isinstance(data["validity_days"], int)
        assert data["validity_days"] > 0


class TestHealthReport:
    """GET /api/vehicle/{vehicle_id}/health-report"""

    def test_returns_report_dict(self):
        resp = client.get(f"/api/vehicle/{VID}/health-report")
        # Should succeed if micro-assessment engine is available,
        # or return 503 if not
        if resp.status_code == 200:
            data = resp.json()
            assert data["success"] is True
            report = data.get("report", {})
            assert "vehicle_id" in report
            assert "ces_mean" in report
            assert "degradation_risk" in report
            assert "tier" in report
            assert "recommendations" in report
        else:
            assert resp.status_code == 503

    def test_report_has_pollutants(self):
        resp = client.get(f"/api/vehicle/{VID}/health-report")
        if resp.status_code == 200:
            report = resp.json().get("report", {})
            pollutants = report.get("pollutants", {})
            assert "co2_mean" in pollutants
            assert "nox_mean" in pollutants


class TestDegradation:
    """GET /api/vehicle/{vehicle_id}/degradation"""

    def test_returns_degradation_info(self):
        resp = client.get(f"/api/vehicle/{VID}/degradation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "vehicle_id" in data
        assert "recommendation" in data
        # New response shape includes health forecast fields
        assert "current_ces" in data
        assert "months_until_failure" in data
        assert "catalyst_health_pct" in data

    def test_degradation_recommendation_is_string(self):
        resp = client.get(f"/api/vehicle/{VID}/degradation")
        data = resp.json()
        assert isinstance(data["recommendation"], str)
        assert len(data["recommendation"]) > 0
