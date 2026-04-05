"""
Smart PUC — FastAPI Dependencies
=================================

Authentication, rate limiting, and shared-state injection helpers used
across `main.py`. Replaces the Flask `@require_api_key` / `@require_auth`
decorators and `@app.before_request` rate limiter with idiomatic FastAPI
`Depends(...)` functions and middleware.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from typing import Optional

import jwt  # PyJWT
from fastapi import Header, HTTPException, Query, Request, status


# ────────────────────────── Configuration ────────────────────────────────

API_KEY = os.getenv("API_KEY", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")

RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "120"))
RATE_LIMIT_WINDOW = 60  # seconds


# ────────────────────────── Rate Limiter ─────────────────────────────────
# SQLite-backed primary via persistence.PersistenceStore, with an in-memory
# thread-safe fallback so the module keeps working in the (rare) case where
# the persistence store is disabled. main.py attaches the store instance to
# app.state; middleware picks it up from Request.app.state.

_rate_limit_lock = threading.Lock()
_rate_limit_store: dict = {}


def _memory_rate_limit_check(client_ip: str) -> bool:
    """Return True if the request is allowed, False if limited."""
    now = time.time()
    with _rate_limit_lock:
        expired = [ip for ip, (_, ts) in _rate_limit_store.items() if now - ts > RATE_LIMIT_WINDOW]
        for ip in expired:
            del _rate_limit_store[ip]
        if client_ip in _rate_limit_store:
            count, window_start = _rate_limit_store[client_ip]
            if now - window_start < RATE_LIMIT_WINDOW:
                if count >= RATE_LIMIT_MAX:
                    return False
                _rate_limit_store[client_ip] = (count + 1, window_start)
            else:
                _rate_limit_store[client_ip] = (1, now)
        else:
            _rate_limit_store[client_ip] = (1, now)
    return True


async def rate_limit_middleware(request: Request, call_next):
    """ASGI middleware replacing Flask's @before_request rate limiter.

    Uses the SQLite PersistenceStore attached to ``app.state.store`` if
    enabled, otherwise falls back to the in-memory implementation.
    """
    client_ip = request.client.host if request.client else "unknown"

    store = getattr(request.app.state, "store", None)
    if store is not None and getattr(store, "enabled", False):
        allowed, _count = store.rate_limit_check(client_ip, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW)
    else:
        allowed = _memory_rate_limit_check(client_ip)

    if not allowed:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={"success": False, "error": "Rate limit exceeded"},
        )
    return await call_next(request)


# ────────────────────────── API Key Auth ─────────────────────────────────

def require_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(None, alias="api_key"),
) -> None:
    """FastAPI dependency enforcing the X-API-Key header (or ?api_key=).

    Disabled if ``API_KEY`` is not set in the environment — the prototype
    remains openable without auth for local demos, matching the legacy
    Flask behaviour.
    """
    if not API_KEY:
        return
    provided = x_api_key or api_key or ""
    if not hmac.compare_digest(provided, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


def require_api_key_or_jwt(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(None, alias="api_key"),
    authorization: Optional[str] = Header(None),
) -> None:
    """Accept either a valid X-API-Key header (device path) OR a valid
    Bearer JWT (authority-dashboard path) for device-write endpoints.

    Motivation: the frontend dashboards are legitimate operator tools
    and should be able to POST /api/record using the operator's
    already-authenticated JWT session. Requiring them to also obtain an
    X-API-Key would either leak the key through the browser or force a
    double-auth flow. With this dependency the dashboard's existing
    Authorization: Bearer header is sufficient, while raw OBD devices
    can still use X-API-Key as before.

    If API_KEY is unset the dependency is a no-op (dev mode).
    """
    if not API_KEY:
        return
    # Path 1 — API key header/query
    provided = x_api_key or api_key or ""
    if provided and hmac.compare_digest(provided, API_KEY):
        return
    # Path 2 — Bearer JWT
    if authorization and authorization.startswith("Bearer ") and JWT_SECRET:
        token = authorization[7:]
        try:
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return
        except jwt.InvalidTokenError:
            pass
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid credentials (X-API-Key or Bearer JWT required)",
    )


# ────────────────────────── JWT Auth ─────────────────────────────────────

def require_auth(authorization: Optional[str] = Header(None)) -> str:
    """Validate an Authorization: Bearer <token> header and return the
    authenticated subject (username)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization[7:]
    if not JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authority auth is not configured on this server",
        )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload.get("sub", "unknown")


# ────────────────────────── Credential helpers ──────────────────────────

def verify_credentials(username: str, password: str) -> bool:
    """Constant-time credential comparison. Returns False unconditionally
    when the server was not configured with credentials — preventing the
    default 'admin/admin' failure mode."""
    if not JWT_SECRET or not AUTH_USERNAME or not AUTH_PASSWORD:
        return False
    return (
        hmac.compare_digest(username, AUTH_USERNAME)
        and hmac.compare_digest(password, AUTH_PASSWORD)
    )


def auth_is_configured() -> bool:
    return bool(JWT_SECRET and AUTH_USERNAME and AUTH_PASSWORD)
