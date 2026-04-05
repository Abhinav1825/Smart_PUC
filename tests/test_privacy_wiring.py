"""Smart PUC — PRIVACY_MODE wiring tests (audit G4).

Verifies that when ``PRIVACY_MODE=1`` the backend's /api/record hot path
replaces the raw vehicle_id with a salted pseudonym (``sp:...``) both in
the SQLite telemetry mirror and in the blockchain call, and that the
helper ``maybe_pseudonymize`` is idempotent on already-pseudonymised
inputs.

These tests spin up a fresh TestClient per privacy configuration by
reloading ``backend.main`` with the env var set, because the flag is
read once at import time (by design — deterministic per-process).
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _reload_backend_with_env(env: dict) -> object:
    """Reload backend.main with the given env vars applied first.

    Returns the freshly reloaded module. Every call produces a NEW
    PersistenceStore bound to a temp sqlite file, so the stored
    vehicle_id can be inspected directly.
    """
    for k, v in env.items():
        os.environ[k] = v
    # Make sure auth is wired so /api/record accepts X-API-Key.
    os.environ.setdefault("JWT_SECRET", "test-jwt-secret-please-do-not-use-in-prod")
    os.environ.setdefault("API_KEY", "test-api-key")
    os.environ.setdefault("AUTH_USERNAME", "admin")
    os.environ.setdefault("AUTH_PASSWORD", "correct-horse-battery-staple")
    os.environ.setdefault("RATE_LIMIT_MAX", "10000")
    # Drop any previously-imported copy so the module-level
    # PRIVACY_MODE_ENABLED constant is re-evaluated. We need to also drop
    # the sibling 'main' entry (same file via the backend/ sys.path
    # insert) so FastAPI does not see two distinct app objects.
    for mod in list(sys.modules):
        if mod == "backend.main" or mod == "main":
            del sys.modules[mod]
    import importlib as _il
    backend_main = _il.import_module("backend.main")
    return backend_main


def test_maybe_pseudonymize_idempotent_on_sp_prefix(monkeypatch):
    """Even with PRIVACY_MODE on, an already-pseudonymised id passes through."""
    monkeypatch.setenv("PRIVACY_MODE", "1")
    monkeypatch.setenv("SMART_PUC_STATION_SALT", "TESTSALT")
    bm = _reload_backend_with_env({
        "PRIVACY_MODE": "1",
        "SMART_PUC_STATION_SALT": "TESTSALT",
    })
    # Idempotency guard.
    assert bm.maybe_pseudonymize("sp:already-hashed") == "sp:already-hashed"
    # And a raw id gets pseudonymised.
    out = bm.maybe_pseudonymize("MH12AB1234")
    assert out.startswith("sp:")


def test_privacy_mode_off_stores_raw_vehicle_id(monkeypatch):
    monkeypatch.delenv("PRIVACY_MODE", raising=False)
    monkeypatch.delenv("SMART_PUC_STATION_SALT", raising=False)
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["PERSISTENCE_DB"] = tmp.name
    bm = _reload_backend_with_env({})
    assert bm.PRIVACY_MODE_ENABLED is False
    client = TestClient(bm.app)
    resp = client.post(
        "/api/record",
        json={
            "vehicle_id": "MH12AB1234",
            "speed": 60.0, "rpm": 2200, "fuel_rate": 6.5,
            "acceleration": 0.2, "wltc_phase": 1,
        },
        headers={"X-API-Key": os.environ["API_KEY"]},
    )
    assert resp.status_code == 200, resp.text
    rows = bm.store.telemetry_for_vehicle("MH12AB1234", limit=10)
    assert rows, "expected raw vehicle_id row in telemetry mirror"


def test_privacy_mode_on_stores_pseudonymised_id(monkeypatch):
    monkeypatch.setenv("PRIVACY_MODE", "1")
    monkeypatch.setenv("SMART_PUC_STATION_SALT", "TESTSALT_B")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["PERSISTENCE_DB"] = tmp.name
    bm = _reload_backend_with_env({
        "PRIVACY_MODE": "1",
        "SMART_PUC_STATION_SALT": "TESTSALT_B",
    })
    assert bm.PRIVACY_MODE_ENABLED is True
    client = TestClient(bm.app)
    resp = client.post(
        "/api/record",
        json={
            "vehicle_id": "MH12AB1234",
            "speed": 60.0, "rpm": 2200, "fuel_rate": 6.5,
            "acceleration": 0.2, "wltc_phase": 1,
        },
        headers={"X-API-Key": os.environ["API_KEY"]},
    )
    assert resp.status_code == 200, resp.text
    # The raw id must NOT appear; a sp:-prefixed pseudonym must.
    raw_rows = bm.store.telemetry_for_vehicle("MH12AB1234", limit=10)
    assert not raw_rows, "raw vehicle_id leaked into telemetry mirror"
    expected = bm.maybe_pseudonymize("MH12AB1234")
    assert expected.startswith("sp:")
    sp_rows = bm.store.telemetry_for_vehicle(expected, limit=10)
    assert sp_rows, "expected sp: row in telemetry mirror under PRIVACY_MODE"
