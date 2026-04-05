"""
Smart PUC — Pydantic Request / Response Schemas
================================================

Centralises the request-body and response shapes for the FastAPI backend.
Only the POST endpoints that accept JSON bodies need bound models; GET
endpoints use FastAPI Query parameters and path parameters inline.

Every body model is permissive: missing optional fields default to sane
values so that the simulator / demo paths in the existing frontend keep
working without code changes.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────── shared ──────────────────────────────────────

class ErrorResponse(BaseModel):
    """Generic error envelope matching the legacy Flask response shape."""
    success: bool = False
    error: str


class OkResponse(BaseModel):
    """Generic success envelope; data payload is free-form."""
    model_config = ConfigDict(extra="allow")
    success: bool = True


# ─────────────────────────── auth ────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class LoginResponse(BaseModel):
    success: bool = True
    token: str
    expires_in: int
    username: str


# ─────────────────────────── /api/record ─────────────────────────────────

class EmissionRecordRequest(BaseModel):
    """Payload posted by the OBD device (Node 1) or a manual tester.

    Every field is optional so that fallback-to-simulator behaviour in the
    legacy Flask code is preserved. Validation bounds match the ones
    hard-coded in the Flask handler.
    """
    model_config = ConfigDict(extra="allow")

    vehicle_id: Optional[str] = None
    speed: Optional[float] = Field(None, ge=0.0, le=250.0)
    rpm: Optional[int] = Field(None, ge=0, le=8000)
    fuel_rate: Optional[float] = Field(None, ge=0.0, le=50.0)
    fuel_type: Optional[str] = "petrol"
    acceleration: Optional[float] = Field(None, ge=-10.0, le=10.0)
    wltc_phase: Optional[int] = Field(0, ge=0, le=3)
    timestamp: Optional[int] = None

    # Device signature (from Node 1)
    device_signature: Optional[str] = ""
    device_address: Optional[str] = ""


# ─────────────────────────── certificates ────────────────────────────────

class IssueCertificateRequest(BaseModel):
    vehicle_id: str = Field(..., min_length=1, max_length=128)
    vehicle_owner: str = Field(..., min_length=42, max_length=42)
    metadata_uri: Optional[str] = None


class RevokeCertificateRequest(BaseModel):
    token_id: int = Field(..., ge=0)
    reason: str = Field("Revoked by authority", max_length=512)


# ─────────────────────────── tokens ──────────────────────────────────────

class RedeemTokensRequest(BaseModel):
    reward_type: str = Field(..., min_length=1, max_length=64)
    from_address: str = Field(..., min_length=42, max_length=42)


# ─────────────────────────── notifications ───────────────────────────────

class NotificationOut(BaseModel):
    timestamp: int
    type: str
    message: str
    vehicle_id: Optional[str] = ""
    severity: str = "info"
