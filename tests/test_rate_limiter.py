"""
Concurrent rate-limiter tests (audit fix #11).

Verifies that the per-IP rate limiter correctly handles concurrent requests.

NOTE: These tests reset the rate limiter store before each test to avoid
interference from other test modules that share the same TestClient.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-please-do-not-use-in-prod")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("AUTH_USERNAME", "admin")
os.environ.setdefault("AUTH_PASSWORD", "admin")

from fastapi.testclient import TestClient


def _get_client_and_reset():
    """Import app and reset the rate-limiter store so tests start clean."""
    from backend.main import app, store
    # Clear rate-limiter entries so we start from a clean state
    if store and store.enabled:
        try:
            with store._conn() as conn:
                conn.execute("DELETE FROM rate_limit")
        except Exception:
            pass
    return TestClient(app)


def test_concurrent_requests_are_handled():
    """Fire concurrent requests; the server handles them (200 or 429)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    client = _get_client_and_reset()
    n_requests = 50

    def _hit(_i: int) -> int:
        resp = client.get("/health")
        return resp.status_code

    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_hit, i) for i in range(n_requests)]
        for f in as_completed(futures):
            results.append(f.result())

    ok_count = results.count(200)
    rate_limited = results.count(429)

    # All requests should either succeed or be rate-limited
    assert all(r in (200, 429) for r in results), (
        f"Unexpected status codes: {set(results) - {200, 429}}"
    )
    assert ok_count > 0, "No requests succeeded after resetting rate limiter"


def test_rate_limiter_enforces_limit():
    """The rate limiter store correctly tracks and limits requests."""
    from backend.main import store
    from backend.dependencies import RATE_LIMIT_WINDOW

    # Directly test the persistence store's rate_limit_check()
    # This avoids sending thousands of HTTP requests when the limit is high
    test_ip = "10.99.99.99"
    limit = 5  # small limit for the test

    # Clear any prior state for this IP
    if store and store.enabled:
        with store._conn() as conn:
            conn.execute("DELETE FROM rate_limit WHERE client_ip = ?", (test_ip,))

    # First `limit` checks should succeed
    for i in range(limit):
        allowed, count = store.rate_limit_check(test_ip, limit, RATE_LIMIT_WINDOW)
        assert allowed, f"Request {i+1} of {limit} should be allowed (count={count})"

    # Next check should be denied
    allowed, count = store.rate_limit_check(test_ip, limit, RATE_LIMIT_WINDOW)
    assert not allowed, f"Request {limit+1} should be denied (count={count})"
