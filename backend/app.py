"""
Smart PUC — Testing Station Backend (Node 2 of 3)
===================================================
Central API server that acts as the Testing Station in the 3-node architecture:
  1. Receives signed telemetry from OBD Device (Node 1)
  2. Validates data, runs fraud detection, calculates emissions
  3. Submits to blockchain with device signature for on-chain verification
  4. Manages PUC certificate issuance and Green Token rewards

Endpoints:
    # Authentication
    POST  /api/auth/login               — Login and receive JWT token

    # Core pipeline
    POST  /api/record                   — Full pipeline: validate -> fraud -> blockchain
    GET   /api/simulate                 — Trigger WLTC simulation (demo mode)
    GET   /api/history/<vehicleId>      — All on-chain records for a vehicle
    GET   /api/violations               — All FAIL records across vehicles
    GET   /api/vehicle-stats/<vid>      — Aggregated stats for a vehicle

    # Certificates (JWT-protected)
    GET   /api/certificate/<vid>        — PUC certificate status (from chain)
    POST  /api/certificate/issue        — Issue PUC certificate NFT
    POST  /api/certificate/revoke       — Revoke a PUC certificate

    # Public verification
    GET   /api/verify/<vid>             — Public verification (no auth needed)

    # Green Tokens
    GET   /api/green-tokens/<address>   — Green Token balance
    POST  /api/tokens/redeem            — Redeem tokens for rewards
    GET   /api/tokens/rewards           — List available rewards and costs
    GET   /api/tokens/history/<address> — Redemption history

    # Analytics
    GET   /api/analytics/trends/<vid>   — Emission trends for charting
    GET   /api/analytics/fleet          — Fleet-wide statistics
    GET   /api/analytics/distribution   — CES distribution histogram
    GET   /api/analytics/phase-breakdown/<vid> — WLTC phase breakdown

    # Fleet management
    GET   /api/fleet/vehicles           — All vehicles with summary stats
    GET   /api/fleet/alerts             — Vehicles with violations/fraud

    # RTO integration
    GET   /api/rto/check/<vid>          — Combined VAHAN + blockchain check
    GET   /api/rto/flagged              — Vehicles with expired PUC / violations

    # Notifications
    GET   /api/notifications            — Recent system notifications

    # OBD-II hardware
    GET   /api/obd/status               — Real OBD device connection status
    POST  /api/obd/read                 — Read single frame from OBD device

    # Vehicle verification
    GET   /api/vehicle/verify/<reg>     — Verify via VAHAN bridge

    # System
    GET   /api/status                   — System health + contract addresses

3-Node Trust Model:
  Node 1: OBD Device — collects telemetry, signs with device key
  Node 2 (this): Testing Station — validates, fraud detection, submits to chain
  Node 3: Verification Portal — read-only, verifies from chain

References:
    EPA MOVES3 (2020), ARAI BSVI (2020), UN ECE R154 (WLTP)
"""

import datetime
import functools
import hashlib
import hmac
import os
import sys
import threading
import time
import traceback
from typing import Optional

import jwt  # PyJWT
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

# Add parent directory to path for physics/ml/integrations imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulator import WLTCSimulator
from emission_engine import calculate_emissions
from blockchain_connector import BlockchainConnector
from persistence import PersistenceStore

# Import VSP model
try:
    from physics.vsp_model import calculate_vsp, get_operating_mode_bin
    vsp_available = True
except ImportError:
    vsp_available = False

# Import VAHAN bridge for vehicle verification
try:
    from integrations.vaahan_bridge import VaahanBridge
    vaahan = VaahanBridge(use_mock=True)
    vaahan_available = True
except ImportError:
    vaahan = None
    vaahan_available = False

# Import OBD adapter (software adapter for parsing frames)
try:
    from integrations.obd_adapter import parse_obd_frame
    obd_adapter_available = True
except ImportError:
    obd_adapter_available = False

# Import python-obd for real ELM327 hardware support
try:
    import obd as obd_lib
    obd_hardware_available = True
except ImportError:
    obd_lib = None
    obd_hardware_available = False

# Import fraud detector
try:
    from ml.fraud_detector import FraudDetector
    import numpy as _np

    fraud_detector = FraudDetector()
    fraud_available = True

    _training_data_path = os.path.join(os.path.dirname(__file__), "..", "ml", "training_data.npy")
    _baseline_data = []

    if os.path.exists(_training_data_path):
        _raw = _np.load(_training_data_path)
        for row in _raw[:600]:
            _baseline_data.append({
                "speed": float(row[0]), "rpm": float(row[1]),
                "fuel_rate": float(row[2]), "acceleration": float(row[3]),
                "co2": float(row[4]), "vsp": float(row[6]),
            })
    else:
        _tmp_sim = WLTCSimulator(vehicle_id="IF_TRAINING", dt=1.0)
        for _ in range(600):
            _r = _tmp_sim.generate_reading()
            _baseline_data.append({
                "speed": _r["speed"], "rpm": float(_r["rpm"]),
                "fuel_rate": _r["fuel_rate"], "acceleration": _r.get("acceleration", 0.0),
                "co2": 130.0, "vsp": 5.0,
            })
        del _tmp_sim

    fraud_detector.fit(_baseline_data)
    del _baseline_data
except ImportError:
    fraud_detector = None
    fraud_available = False

# Import LSTM predictor
try:
    from ml.lstm_predictor import create_predictor
    predictor = create_predictor(use_lstm=False)
    predictor_available = True
except ImportError:
    predictor = None
    predictor_available = False

# WLTC phase mapping
_PHASE_TO_INT = {"Low": 0, "Medium": 1, "High": 2, "Extra High": 3}

# ────────────────────────── App Setup ──────────────────────────────────────

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

app = Flask(__name__)

# Numpy-safe JSON encoding
import json as _json
import numpy as _np_json

from flask.json.provider import DefaultJSONProvider


class _NumpyJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, (_np_json.integer,)):
            return int(obj)
        if isinstance(obj, (_np_json.floating,)):
            return float(obj)
        if isinstance(obj, (_np_json.bool_,)):
            return bool(obj)
        if isinstance(obj, _np_json.ndarray):
            return obj.tolist()
        return super().default(obj)


app.json_provider_class = _NumpyJSONProvider
app.json = _NumpyJSONProvider(app)

# CORS
_cors_origins = os.getenv("CORS_ORIGINS", "*")
if _cors_origins == "*":
    CORS(app)
else:
    CORS(app, origins=_cors_origins.split(","))

# ────────────────────────── Authentication Config ────────────────────────

# API key auth for OBD device write endpoints (Node 1 -> Node 2)
API_KEY = os.getenv("API_KEY", "")

# JWT auth for authority endpoints (dashboard -> Node 2)
# SECURITY: JWT_SECRET MUST be set via environment; refuse to start otherwise.
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

# Authority credentials — MUST be supplied via environment variables.
# The backend refuses to authenticate anyone if either is empty, making it
# impossible to ship with a default "admin/admin"-style password.
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")

if not JWT_SECRET:
    print("  [warn] JWT_SECRET is not set — authority endpoints will reject all logins.")
    print("         Set JWT_SECRET in .env before serving any real traffic.")
if not AUTH_USERNAME or not AUTH_PASSWORD:
    print("  [warn] AUTH_USERNAME / AUTH_PASSWORD are not set — authority login is disabled.")
    print("         Set both in .env before serving any real traffic.")

# ────────────────────────── Persistence ──────────────────────────────────
# SQLite-backed persistence for rate limiter, notifications, telemetry cold
# store, Merkle batches, and audit log. Set PERSISTENCE_DB to a file path to
# enable; leave blank to run with in-memory fallback.

_persistence_path = os.getenv("PERSISTENCE_DB", "").strip()
if _persistence_path:
    if not os.path.isabs(_persistence_path):
        _persistence_path = os.path.join(os.path.dirname(__file__), "..", _persistence_path)
store = PersistenceStore(_persistence_path or None)
if store.enabled:
    print(f"  Persistence: SQLite at {_persistence_path}")
else:
    print("  Persistence: disabled (in-memory fallback)")

# ────────────────────────── Rate Limiting ────────────────────────────────
# Primary: SQLite-backed rate limiter via PersistenceStore (survives restart,
# shareable across multi-process deployments via WAL).
# Fallback: thread-safe in-memory store when persistence is disabled.

_rate_limit_lock = threading.Lock()
_rate_limit_store: dict = {}
_RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "120"))
_RATE_LIMIT_WINDOW = 60

DEFAULT_VEHICLE_ID = os.getenv("DEFAULT_VEHICLE_ID", "MH12AB1234")

# ────────────────────────── Notification System ──────────────────────────
# In-memory event log. Last 100 notifications are kept.
# Each notification: { timestamp, type, message, vehicle_id, severity }

_notifications_lock = threading.Lock()
_notifications: list = []
_MAX_NOTIFICATIONS = 100


def _add_notification(notif_type: str, message: str, vehicle_id: str = "",
                      severity: str = "info"):
    """Add a notification to the event log.

    When the SQLite persistence store is enabled, notifications are written
    durably and survive restarts. The in-memory ring buffer is always
    updated as well so that the most recent entries are cheap to read.
    """
    notif = {
        "timestamp": int(time.time()),
        "type": notif_type,
        "message": message,
        "vehicle_id": vehicle_id,
        "severity": severity,
    }
    with _notifications_lock:
        _notifications.append(notif)
        if len(_notifications) > _MAX_NOTIFICATIONS:
            del _notifications[:-_MAX_NOTIFICATIONS]
    # Durable persistence (no-op if disabled)
    try:
        store.add_notification(notif_type, message, vehicle_id=vehicle_id, severity=severity)
    except Exception:
        pass  # never let persistence failures break the hot path


# ────────────────────────── Auth Decorators ──────────────────────────────

def require_api_key(f):
    """Decorator: require API key for OBD device endpoints (disabled if no key set)."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not API_KEY:
            return f(*args, **kwargs)
        provided = request.headers.get("X-API-Key") or request.args.get("api_key", "")
        if not provided or not hmac.compare_digest(provided, API_KEY):
            return jsonify({"success": False, "error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated


def require_auth(f):
    """Decorator: require valid JWT token in Authorization: Bearer <token> header."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "error": "Missing or invalid Authorization header"}), 401
        token = auth_header[7:]  # Strip "Bearer "
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            # Attach user info to request context
            request.auth_user = payload.get("sub", "unknown")
        except jwt.ExpiredSignatureError:
            return jsonify({"success": False, "error": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"success": False, "error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


@app.before_request
def _check_rate_limit():
    """Per-IP rate limiting. Uses SQLite when persistence is enabled,
    otherwise falls back to the thread-safe in-memory store."""
    client_ip = request.remote_addr or "unknown"

    if store.enabled:
        allowed, _count = store.rate_limit_check(
            client_ip, _RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW
        )
        if not allowed:
            return jsonify({"success": False, "error": "Rate limit exceeded"}), 429
        return  # proceed

    # In-memory fallback (no persistence)
    now = time.time()
    with _rate_limit_lock:
        expired = [ip for ip, (_, ts) in _rate_limit_store.items()
                   if now - ts > _RATE_LIMIT_WINDOW]
        for ip in expired:
            del _rate_limit_store[ip]
        if client_ip in _rate_limit_store:
            count, window_start = _rate_limit_store[client_ip]
            if now - window_start < _RATE_LIMIT_WINDOW:
                if count >= _RATE_LIMIT_MAX:
                    return jsonify({"success": False, "error": "Rate limit exceeded"}), 429
                _rate_limit_store[client_ip] = (count + 1, window_start)
            else:
                _rate_limit_store[client_ip] = (1, now)
        else:
            _rate_limit_store[client_ip] = (1, now)


# ────────────────────────── Initialize Components ─────────────────────────

simulator = WLTCSimulator(vehicle_id=DEFAULT_VEHICLE_ID)

try:
    blockchain = BlockchainConnector()
    blockchain_connected = True
except Exception as e:
    print(f"Blockchain connection failed: {e}")
    print("   API will run in offline mode (no on-chain writes).")
    blockchain = None
    blockchain_connected = False

_readings_lock = threading.Lock()
readings_count = 0
_engine_start_time = time.time()

# Try to connect to real OBD-II ELM327 device
_obd_connection = None
if obd_hardware_available:
    try:
        _obd_connection = obd_lib.OBD()  # auto-detect port
        if not _obd_connection.is_connected():
            _obd_connection = None
            print("  OBD-II: No ELM327 device detected (will use simulator).")
        else:
            print(f"  OBD-II: Connected to {_obd_connection.port_name()}")
    except Exception as exc:
        _obd_connection = None
        print(f"  OBD-II: Connection attempt failed: {exc}")


# ────────────────────────── Helper Functions ───────────────────────────────

def compute_full_emission(speed, rpm, fuel_rate, fuel_type="petrol", acceleration=0.0,
                          ambient_temp=25.0, altitude=0.0):
    """Run the full emission pipeline: VSP -> operating mode -> multi-pollutant engine."""
    speed_mps = speed / 3.6
    vsp_value = 0.0
    op_mode_bin = 11
    if vsp_available:
        vsp_value = calculate_vsp(speed_mps, acceleration)
        op_mode_bin = get_operating_mode_bin(vsp_value, speed_mps)

    cold_start = (time.time() - _engine_start_time) < 180.0

    emission = calculate_emissions(
        speed_kmh=speed, acceleration=acceleration, rpm=rpm,
        fuel_rate=fuel_rate, fuel_type=fuel_type,
        operating_mode_bin=op_mode_bin, ambient_temp=ambient_temp,
        altitude=altitude, cold_start=cold_start,
    )

    emission["vsp"] = round(vsp_value, 3)
    emission["operating_mode_bin"] = op_mode_bin
    return emission


def _check_cert_expiry_notifications():
    """Check all vehicles for certificates expiring within 7 days and generate alerts."""
    if not blockchain_connected or not blockchain:
        return
    try:
        vehicles = blockchain.get_registered_vehicles()
        now = int(time.time())
        seven_days = 7 * 24 * 3600
        for vid in vehicles:
            try:
                cert = blockchain.check_certificate(vid)
                if cert.get("valid") and cert.get("expiry_timestamp"):
                    remaining = cert["expiry_timestamp"] - now
                    if 0 < remaining < seven_days:
                        days_left = remaining // 86400
                        _add_notification(
                            "cert_expiry_warning",
                            f"PUC certificate for {vid} expires in {days_left} day(s)",
                            vehicle_id=vid,
                            severity="warning",
                        )
            except Exception:
                pass
    except Exception:
        pass


# ────────────────────────── Authentication Endpoints ─────────────────────

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """
    POST /api/auth/login
    Authenticate with username/password and receive a JWT token.

    Body: { "username": "<AUTH_USERNAME>", "password": "<AUTH_PASSWORD>" }
    (values come from the backend's environment; no default is shipped.)
    Returns: { "success": true, "token": "<jwt>", "expires_in": 86400 }
    """
    try:
        data = request.get_json(silent=True) or {}
        username = data.get("username", "")
        password = data.get("password", "")

        if not username or not password:
            return jsonify({"success": False, "error": "Username and password required"}), 400

        # Refuse to authenticate if the server was not configured with
        # credentials. This prevents the default "admin/admin" failure mode
        # that has plagued many research prototypes.
        if not JWT_SECRET or not AUTH_USERNAME or not AUTH_PASSWORD:
            return jsonify({
                "success": False,
                "error": "Authority auth is not configured on this server"
            }), 503

        # Constant-time comparison to prevent timing attacks
        username_match = hmac.compare_digest(username, AUTH_USERNAME)
        password_match = hmac.compare_digest(password, AUTH_PASSWORD)

        if not username_match or not password_match:
            return jsonify({"success": False, "error": "Invalid credentials"}), 401

        # Generate JWT token
        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            "sub": username,
            "iat": now,
            "exp": now + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
            "role": "authority",
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

        return jsonify({
            "success": True,
            "token": token,
            "expires_in": JWT_EXPIRY_HOURS * 3600,
            "username": username,
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ────────────────────────── Core API Endpoints ───────────────────────────

@app.route("/api/simulate", methods=["GET"])
def simulate():
    """GET /api/simulate — Generate WLTC telemetry with all 5 pollutants."""
    try:
        vehicle_id = request.args.get("vehicle_id", DEFAULT_VEHICLE_ID)
        simulator.vehicle_id = vehicle_id
        reading = simulator.generate_reading()
        emission = compute_full_emission(
            speed=reading["speed"], rpm=reading["rpm"],
            fuel_rate=reading["fuel_rate"],
            acceleration=reading.get("acceleration", 0.0),
        )
        result = {**reading, **emission}
        return jsonify({"success": True, "data": result}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/record", methods=["POST"])
@require_api_key
def record():
    """
    POST /api/record
    Full Testing Station pipeline:
      1. Receive telemetry (from OBD device or simulator)
      2. Verify vehicle via VAHAN bridge
      3. Calculate/validate emissions
      4. Run fraud detection
      5. LSTM prediction
      6. Submit to blockchain with device signature
         (CES is computed on-chain; we still compute off-chain for immediate response)
      7. Check certificate eligibility
    """
    global readings_count

    try:
        data = request.get_json(silent=True) or {}
        vehicle_id = data.get("vehicle_id", DEFAULT_VEHICLE_ID)

        # ── Step 0: Vehicle verification via VAHAN ──
        vehicle_info = None
        if vaahan_available and vaahan:
            eligibility = vaahan.validate_for_emission_test(vehicle_id)
            vehicle_info = eligibility.get("vehicle_info")
            # Log but don't block — mock vehicles should still work
            if not eligibility.get("eligible"):
                print(f"  VAHAN: Vehicle {vehicle_id} not eligible: {eligibility.get('reason')}")

        # ── Step 1: Get telemetry data ──
        device_signature = data.get("device_signature", "")
        device_address = data.get("device_address", "")

        if "speed" in data and "fuel_rate" in data:
            # Data from OBD device or manual input
            fuel_rate = max(0.0, min(float(data["fuel_rate"]), 50.0))
            speed = max(0.0, min(float(data["speed"]), 250.0))
            rpm = max(0, min(int(data.get("rpm", 2000)), 8000))
            fuel_type = data.get("fuel_type", "petrol")
            if fuel_type not in ("petrol", "diesel"):
                fuel_type = "petrol"
            acceleration = max(-10.0, min(float(data.get("acceleration", 0.0)), 10.0))
        else:
            # Fallback: generate from WLTC simulator
            reading = simulator.generate_reading()
            fuel_rate = reading["fuel_rate"]
            speed = reading["speed"]
            rpm = reading["rpm"]
            fuel_type = reading.get("fuel_type", "petrol")
            acceleration = reading.get("acceleration", 0.0)

        # Get WLTC phase
        wltc_phase = data.get("wltc_phase", 0)
        if wltc_phase == 0 and hasattr(simulator, '_current_time'):
            phase_obj = simulator.get_phase(simulator._current_time)
            phase_str = phase_obj.value if hasattr(phase_obj, 'value') else str(phase_obj)
            wltc_phase = _PHASE_TO_INT.get(phase_str, 0)

        timestamp = data.get("timestamp", int(time.time()))

        # ── Step 2: Calculate emissions ──
        emission = compute_full_emission(
            speed=speed, rpm=rpm, fuel_rate=fuel_rate,
            fuel_type=fuel_type, acceleration=acceleration,
        )

        with _readings_lock:
            readings_count += 1

        # ── Step 3: Fraud detection ──
        fraud_result = {"fraud_score": 0.0, "is_fraud": False, "severity": "LOW", "violations": []}
        if fraud_available and fraud_detector:
            reading_for_fraud = {
                "speed": speed, "rpm": rpm, "fuel_rate": fuel_rate,
                "acceleration": acceleration,
                "co2": emission.get("co2_g_per_km", 0),
                "vsp": emission.get("vsp", 0),
            }
            fraud_result = fraud_detector.analyze(reading_for_fraud)

        # Generate fraud notification if detected
        if fraud_result.get("is_fraud"):
            _add_notification(
                "fraud_alert",
                f"Fraud detected for {vehicle_id}: score={fraud_result['fraud_score']:.2f}, "
                f"severity={fraud_result.get('severity', 'UNKNOWN')}",
                vehicle_id=vehicle_id,
                severity="critical",
            )

        # Generate violation notification if emission fails
        if emission.get("status") == "FAIL":
            _add_notification(
                "violation_alert",
                f"Emission violation for {vehicle_id}: CES={emission.get('ces_score', 0):.3f}, "
                f"status=FAIL",
                vehicle_id=vehicle_id,
                severity="high",
            )

        # ── Step 4: LSTM prediction ──
        predictions = None
        if predictor_available and predictor:
            predictor.update({
                "speed": speed, "rpm": rpm, "fuel_rate": fuel_rate,
                "acceleration": acceleration,
                "co2": emission.get("co2_g_per_km", 0),
                "nox": emission.get("nox_g_per_km", 0),
                "vsp": emission.get("vsp", 0),
                "ces_score": emission.get("ces_score", 0),
            })
            predictions = predictor.predict_next()

        # ── Step 5: Write to blockchain with device signature ──
        # CES is computed on-chain now, so we do NOT send ces_score to the contract.
        # The nonce is generated by the connector automatically.
        # We still compute CES off-chain (above) for the immediate API response.
        tx_result = {"tx_hash": None, "status": "offline", "block_number": None, "gas_used": 0}
        if blockchain_connected and blockchain:
            try:
                tx_result = blockchain.store_emission(
                    vehicle_id=vehicle_id,
                    co2=emission.get("co2_g_per_km", 0),
                    co=emission.get("co_g_per_km", 0),
                    nox=emission.get("nox_g_per_km", 0),
                    hc=emission.get("hc_g_per_km", 0),
                    pm25=emission.get("pm25_g_per_km", 0),
                    fraud_score=fraud_result.get("fraud_score", 0),
                    vsp=emission.get("vsp", 0),
                    wltc_phase=wltc_phase,
                    timestamp=timestamp,
                    device_signature=device_signature,
                )
            except Exception as e:
                print(f"Blockchain write failed: {e}")
                tx_result = {"tx_hash": None, "status": "failed", "block_number": None, "gas_used": 0}

        # ── Step 6: Check certificate eligibility ──
        cert_eligible = None
        if blockchain_connected and blockchain:
            try:
                cert_eligible = blockchain.is_certificate_eligible(vehicle_id)
            except Exception:
                pass

        # ── Step 7: Get vehicle stats ──
        vehicle_stats = None
        if blockchain_connected and blockchain:
            try:
                vehicle_stats = blockchain.get_vehicle_stats(vehicle_id)
            except Exception:
                pass

        # Build response
        response_data = {
            "vehicle_id": vehicle_id,
            "txHash": tx_result.get("tx_hash"),
            "blockNumber": tx_result.get("block_number"),
            "tx_status": tx_result.get("status"),
            "gas_used": tx_result.get("gas_used", 0),
            # Telemetry
            "speed": speed,
            "rpm": rpm,
            "fuel_rate": fuel_rate,
            "fuel_type": fuel_type,
            "acceleration": round(acceleration, 3),
            # Emissions
            "co2_g_per_km": emission.get("co2_g_per_km", 0),
            "co_g_per_km": emission.get("co_g_per_km", 0),
            "nox_g_per_km": emission.get("nox_g_per_km", 0),
            "hc_g_per_km": emission.get("hc_g_per_km", 0),
            "pm25_g_per_km": emission.get("pm25_g_per_km", 0),
            # Scores
            "ces_score": emission.get("ces_score", 0),
            "status": emission.get("status", "UNKNOWN"),
            "compliance": emission.get("compliance", {}),
            "vsp": emission.get("vsp", 0),
            "operating_mode_bin": emission.get("operating_mode_bin", 0),
            "wltc_phase": wltc_phase,
            # Fraud
            "fraud_score": fraud_result.get("fraud_score", 0),
            "fraud_status": {
                "is_fraud": fraud_result.get("is_fraud", False),
                "severity": fraud_result.get("severity", "LOW"),
                "violations": fraud_result.get("violations", []),
            },
            # Device provenance
            "device_address": device_address,
            "device_signed": bool(device_signature),
            # Certificate eligibility
            "certificate_eligible": cert_eligible,
            # Predictions
            "predictions": predictions,
            # Vehicle verification
            "vehicle_info": {
                "fuel_type": vehicle_info.get("fuel_type") if vehicle_info else None,
                "bs_norm": vehicle_info.get("bs_norm") if vehicle_info else None,
                "manufacturer": vehicle_info.get("manufacturer") if vehicle_info else None,
                "model": vehicle_info.get("model") if vehicle_info else None,
            } if vehicle_info else None,
            # Stats
            "vehicle_stats": vehicle_stats,
            "timestamp": timestamp,
        }

        return jsonify({"success": True, "data": response_data}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/history/<vehicle_id>", methods=["GET"])
def history(vehicle_id: str):
    """GET /api/history/<vehicleId> — All on-chain records."""
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        page = int(request.args.get("page", 0))
        limit = min(int(request.args.get("limit", 50)), 100)
        records = blockchain.get_history_paginated(vehicle_id, page * limit, limit)
        total = blockchain.get_record_count(vehicle_id)
        return jsonify({
            "success": True, "vehicle_id": vehicle_id,
            "count": total, "page": page, "limit": limit,
            "records": records,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/violations", methods=["GET"])
def violations():
    """GET /api/violations — All FAIL records across all vehicles."""
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        all_violations = []
        vehicles = blockchain.get_registered_vehicles()
        for vid in vehicles:
            vehicle_violations = blockchain.get_violations(vid)
            all_violations.extend(vehicle_violations)
        all_violations.sort(key=lambda x: x["timestamp"], reverse=True)
        return jsonify({"success": True, "count": len(all_violations), "violations": all_violations}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vehicle-stats/<vehicle_id>", methods=["GET"])
def vehicle_stats(vehicle_id: str):
    """GET /api/vehicle-stats/<vehicleId> — Aggregated stats."""
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        stats = blockchain.get_vehicle_stats(vehicle_id)
        cert_eligible = blockchain.is_certificate_eligible(vehicle_id)
        stats["certificate_eligible"] = cert_eligible
        return jsonify({"success": True, "stats": stats}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── Certificate Endpoints ─────────────────────────────────

@app.route("/api/certificate/<vehicle_id>", methods=["GET"])
def certificate(vehicle_id: str):
    """GET /api/certificate/<vehicleId> — PUC certificate status from chain."""
    try:
        if blockchain_connected and blockchain:
            cert = blockchain.check_certificate(vehicle_id)
            return jsonify({"success": True, "certificate": cert}), 200
        return jsonify({"success": True, "certificate": {"valid": False, "token_id": 0}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/certificate/issue", methods=["POST"])
@require_auth
def issue_certificate():
    """POST /api/certificate/issue — Issue PUC certificate NFT (JWT required)."""
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        data = request.get_json(silent=True) or {}
        vehicle_id = data.get("vehicle_id")
        vehicle_owner = data.get("vehicle_owner")
        metadata_uri = data.get("metadata_uri")
        if not vehicle_id or not vehicle_owner:
            return jsonify({"success": False, "error": "vehicle_id and vehicle_owner required"}), 400

        # Build kwargs for issue_certificate; include metadata_uri if provided
        kwargs = {"vehicle_id": vehicle_id, "vehicle_owner": vehicle_owner}
        if metadata_uri:
            kwargs["metadata_uri"] = metadata_uri

        result = blockchain.issue_certificate(**kwargs)

        _add_notification(
            "cert_issued",
            f"PUC certificate issued for {vehicle_id} by {getattr(request, 'auth_user', 'unknown')}",
            vehicle_id=vehicle_id,
            severity="info",
        )

        return jsonify({"success": True, "result": result}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/certificate/revoke", methods=["POST"])
@require_auth
def revoke_certificate():
    """POST /api/certificate/revoke — Revoke a PUC certificate (JWT required)."""
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        data = request.get_json(silent=True) or {}
        token_id = data.get("token_id")
        reason = data.get("reason", "Revoked by authority")
        if token_id is None:
            return jsonify({"success": False, "error": "token_id required"}), 400

        result = blockchain.revoke_certificate(int(token_id), reason)

        _add_notification(
            "cert_revoked",
            f"PUC certificate #{token_id} revoked: {reason}",
            vehicle_id="",
            severity="high",
        )

        return jsonify({"success": True, "result": result}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── Public Verification (No Auth) ─────────────────────────

@app.route("/api/verify/<vehicle_id>", methods=["GET"])
def verify(vehicle_id: str):
    """GET /api/verify/<vehicleId> — Public PUC verification (no auth needed)."""
    try:
        if blockchain_connected and blockchain:
            verification = blockchain.get_verification_data(vehicle_id)
            stats = blockchain.get_vehicle_stats(vehicle_id)
            return jsonify({
                "success": True,
                "verification": verification,
                "stats": {
                    "total_records": stats["total_records"],
                    "violations": stats["violations"],
                    "avg_ces": stats["avg_ces"],
                },
            }), 200
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── Green Token Endpoints ───────────────────────────────

@app.route("/api/green-tokens/<address>", methods=["GET"])
def green_tokens(address: str):
    """GET /api/green-tokens/<address> — Green Credit Token balance."""
    try:
        if blockchain_connected and blockchain:
            balance = blockchain.get_green_token_balance(address)
            return jsonify({"success": True, "tokens": balance}), 200
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tokens/redeem", methods=["POST"])
@require_auth
def redeem_tokens():
    """
    POST /api/tokens/redeem — Redeem Green Tokens for a reward.
    Body: { "reward_type": "toll_discount", "from_address": "0x..." }
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        data = request.get_json(silent=True) or {}
        reward_type = data.get("reward_type", "")
        from_address = data.get("from_address", "")

        if not reward_type or not from_address:
            return jsonify({"success": False, "error": "reward_type and from_address required"}), 400

        result = blockchain.redeem_tokens(reward_type, from_address)

        _add_notification(
            "token_redemption",
            f"Token redemption: {reward_type} by {from_address[:10]}...",
            vehicle_id="",
            severity="info",
        )

        return jsonify({"success": True, "result": result}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tokens/rewards", methods=["GET"])
def token_rewards():
    """GET /api/tokens/rewards — List available rewards and their costs."""
    try:
        # Define available reward types and query costs from blockchain
        reward_types = ["toll_discount", "tax_rebate", "insurance_discount",
                        "parking_credit", "fuel_voucher"]
        rewards = []

        for rtype in reward_types:
            cost = 0
            if blockchain_connected and blockchain:
                try:
                    cost = blockchain.get_reward_cost(rtype)
                except Exception:
                    cost = 0

            rewards.append({
                "reward_type": rtype,
                "display_name": rtype.replace("_", " ").title(),
                "cost_tokens": cost,
                "available": cost > 0,
            })

        return jsonify({"success": True, "rewards": rewards}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tokens/history/<address>", methods=["GET"])
def token_history(address: str):
    """GET /api/tokens/history/<address> — Redemption history for an address."""
    try:
        if not blockchain_connected:
            return jsonify({"success": False, "error": "Blockchain not connected"}), 503

        stats = blockchain.get_redemption_stats(address)
        return jsonify({"success": True, "address": address, "stats": stats}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── Analytics Endpoints ─────────────────────────────────

@app.route("/api/analytics/trends/<vehicle_id>", methods=["GET"])
def analytics_trends(vehicle_id: str):
    """
    GET /api/analytics/trends/<vehicle_id>
    Returns emission trends: all records with timestamps for charting.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        records = blockchain.get_history(vehicle_id)
        # Build trend data points for charts
        trends = []
        for rec in records:
            trends.append({
                "timestamp": rec.get("timestamp", 0),
                "co2": rec.get("co2Level", 0),
                "co": rec.get("coLevel", 0),
                "nox": rec.get("noxLevel", 0),
                "hc": rec.get("hcLevel", 0),
                "pm25": rec.get("pm25Level", 0),
                "ces_score": rec.get("cesScore", 0),
                "fraud_score": rec.get("fraudScore", 0),
                "vsp": rec.get("vspValue", 0),
                "wltc_phase": rec.get("wltcPhase", 0),
                "status": rec.get("status", "UNKNOWN"),
            })
        # Sort by timestamp ascending for charting
        trends.sort(key=lambda x: x["timestamp"])

        return jsonify({
            "success": True,
            "vehicle_id": vehicle_id,
            "count": len(trends),
            "trends": trends,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/analytics/fleet", methods=["GET"])
def analytics_fleet():
    """
    GET /api/analytics/fleet
    Fleet-wide stats: total vehicles, avg CES, compliance rate, worst performers.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        vehicles = blockchain.get_registered_vehicles()
        total_vehicles = len(vehicles)
        total_records = 0
        total_violations = 0
        ces_sum = 0.0
        ces_count = 0
        vehicle_stats_list = []

        for vid in vehicles:
            try:
                stats = blockchain.get_vehicle_stats(vid)
                tr = stats.get("total_records", 0)
                viol = stats.get("violations", 0)
                avg_ces = stats.get("avg_ces", 0.0)

                total_records += tr
                total_violations += viol
                if tr > 0:
                    ces_sum += avg_ces
                    ces_count += 1

                vehicle_stats_list.append({
                    "vehicle_id": vid,
                    "total_records": tr,
                    "violations": viol,
                    "avg_ces": avg_ces,
                })
            except Exception:
                pass

        fleet_avg_ces = ces_sum / ces_count if ces_count > 0 else 0.0
        # Compliance = vehicles with avg CES < 1.0 (PASS threshold)
        compliant = sum(1 for vs in vehicle_stats_list if vs["avg_ces"] < 1.0 and vs["total_records"] > 0)
        vehicles_with_records = sum(1 for vs in vehicle_stats_list if vs["total_records"] > 0)
        compliance_rate = (compliant / vehicles_with_records * 100) if vehicles_with_records > 0 else 0.0

        # Worst performers: sorted by avg CES descending (higher = worse)
        worst = sorted(vehicle_stats_list, key=lambda x: x["avg_ces"], reverse=True)[:10]

        return jsonify({
            "success": True,
            "fleet": {
                "total_vehicles": total_vehicles,
                "total_records": total_records,
                "total_violations": total_violations,
                "avg_ces": round(fleet_avg_ces, 4),
                "compliance_rate": round(compliance_rate, 2),
                "worst_performers": worst,
            },
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/analytics/distribution", methods=["GET"])
def analytics_distribution():
    """
    GET /api/analytics/distribution
    CES distribution histogram data across all vehicles.
    Buckets: 0-0.25, 0.25-0.5, 0.5-0.75, 0.75-1.0, 1.0+
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        vehicles = blockchain.get_registered_vehicles()

        buckets = {
            "0.00-0.25": 0,
            "0.25-0.50": 0,
            "0.50-0.75": 0,
            "0.75-1.00": 0,
            "1.00+": 0,
        }
        total_samples = 0

        for vid in vehicles:
            try:
                stats = blockchain.get_vehicle_stats(vid)
                avg_ces = stats.get("avg_ces", 0.0)
                if stats.get("total_records", 0) == 0:
                    continue
                total_samples += 1

                if avg_ces < 0.25:
                    buckets["0.00-0.25"] += 1
                elif avg_ces < 0.50:
                    buckets["0.25-0.50"] += 1
                elif avg_ces < 0.75:
                    buckets["0.50-0.75"] += 1
                elif avg_ces < 1.00:
                    buckets["0.75-1.00"] += 1
                else:
                    buckets["1.00+"] += 1
            except Exception:
                pass

        histogram = [
            {"bucket": k, "count": v, "percentage": round(v / total_samples * 100, 1) if total_samples > 0 else 0.0}
            for k, v in buckets.items()
        ]

        return jsonify({
            "success": True,
            "total_vehicles": total_samples,
            "distribution": histogram,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/analytics/phase-breakdown/<vehicle_id>", methods=["GET"])
def analytics_phase_breakdown(vehicle_id: str):
    """
    GET /api/analytics/phase-breakdown/<vehicle_id>
    WLTC phase breakdown stats for a vehicle.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        records = blockchain.get_history(vehicle_id)

        phase_names = {0: "Low", 1: "Medium", 2: "High", 3: "Extra High"}
        phase_data = {}
        for phase_id in range(4):
            phase_data[phase_id] = {
                "name": phase_names[phase_id],
                "count": 0,
                "co2_sum": 0.0,
                "co_sum": 0.0,
                "nox_sum": 0.0,
                "hc_sum": 0.0,
                "pm25_sum": 0.0,
                "ces_sum": 0.0,
                "violations": 0,
            }

        for rec in records:
            phase = rec.get("wltcPhase", 0)
            if phase not in phase_data:
                phase = 0
            pd = phase_data[phase]
            pd["count"] += 1
            pd["co2_sum"] += rec.get("co2Level", 0)
            pd["co_sum"] += rec.get("coLevel", 0)
            pd["nox_sum"] += rec.get("noxLevel", 0)
            pd["hc_sum"] += rec.get("hcLevel", 0)
            pd["pm25_sum"] += rec.get("pm25Level", 0)
            pd["ces_sum"] += rec.get("cesScore", 0)
            if rec.get("status") == "FAIL":
                pd["violations"] += 1

        # Compute averages
        breakdown = []
        for phase_id in range(4):
            pd = phase_data[phase_id]
            count = pd["count"]
            breakdown.append({
                "phase": phase_id,
                "phase_name": pd["name"],
                "record_count": count,
                "avg_co2": round(pd["co2_sum"] / count, 2) if count > 0 else 0.0,
                "avg_co": round(pd["co_sum"] / count, 2) if count > 0 else 0.0,
                "avg_nox": round(pd["nox_sum"] / count, 2) if count > 0 else 0.0,
                "avg_hc": round(pd["hc_sum"] / count, 2) if count > 0 else 0.0,
                "avg_pm25": round(pd["pm25_sum"] / count, 2) if count > 0 else 0.0,
                "avg_ces": round(pd["ces_sum"] / count, 4) if count > 0 else 0.0,
                "violations": pd["violations"],
            })

        return jsonify({
            "success": True,
            "vehicle_id": vehicle_id,
            "total_records": len(records),
            "phase_breakdown": breakdown,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── Fleet Management Endpoints ─────────────────────────

@app.route("/api/fleet/vehicles", methods=["GET"])
def fleet_vehicles():
    """
    GET /api/fleet/vehicles
    List all vehicles with summary stats.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        vehicles = blockchain.get_registered_vehicles()
        vehicle_list = []

        for vid in vehicles:
            try:
                stats = blockchain.get_vehicle_stats(vid)
                cert = blockchain.check_certificate(vid)

                vehicle_list.append({
                    "vehicle_id": vid,
                    "total_records": stats.get("total_records", 0),
                    "violations": stats.get("violations", 0),
                    "fraud_alerts": stats.get("fraud_alerts", 0),
                    "avg_ces": stats.get("avg_ces", 0.0),
                    "status": "PASS" if stats.get("avg_ces", 0) < 1.0 and stats.get("total_records", 0) > 0 else "FAIL",
                    "certificate_valid": cert.get("valid", False),
                    "certificate_expiry": cert.get("expiry_timestamp", 0),
                })
            except Exception:
                vehicle_list.append({
                    "vehicle_id": vid,
                    "total_records": 0,
                    "violations": 0,
                    "fraud_alerts": 0,
                    "avg_ces": 0.0,
                    "status": "UNKNOWN",
                    "certificate_valid": False,
                    "certificate_expiry": 0,
                })

        return jsonify({
            "success": True,
            "count": len(vehicle_list),
            "vehicles": vehicle_list,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/fleet/alerts", methods=["GET"])
def fleet_alerts():
    """
    GET /api/fleet/alerts
    Vehicles with recent violations or fraud alerts, sorted by severity.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        vehicles = blockchain.get_registered_vehicles()
        alerts = []

        for vid in vehicles:
            try:
                stats = blockchain.get_vehicle_stats(vid)
                viol = stats.get("violations", 0)
                fraud = stats.get("fraud_alerts", 0)
                avg_ces = stats.get("avg_ces", 0.0)

                if viol == 0 and fraud == 0:
                    continue

                # Severity score: higher = worse
                severity_score = viol * 2 + fraud * 3 + (avg_ces * 5 if avg_ces > 1.0 else 0)

                if fraud > 0:
                    severity = "critical"
                elif viol > 3:
                    severity = "high"
                elif viol > 0:
                    severity = "medium"
                else:
                    severity = "low"

                alerts.append({
                    "vehicle_id": vid,
                    "violations": viol,
                    "fraud_alerts": fraud,
                    "avg_ces": avg_ces,
                    "severity": severity,
                    "severity_score": severity_score,
                })
            except Exception:
                pass

        # Sort by severity_score descending (worst first)
        alerts.sort(key=lambda x: x["severity_score"], reverse=True)

        return jsonify({
            "success": True,
            "count": len(alerts),
            "alerts": alerts,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── RTO Integration Endpoints ──────────────────────────

@app.route("/api/rto/check/<vehicle_id>", methods=["GET"])
def rto_check(vehicle_id: str):
    """
    GET /api/rto/check/<vehicle_id>
    Combined VAHAN + blockchain check for RTO renewal eligibility.
    """
    try:
        result = {
            "vehicle_id": vehicle_id,
            "vahan_status": None,
            "blockchain_status": None,
            "certificate_status": None,
            "renewal_eligible": False,
            "issues": [],
        }

        # VAHAN check
        if vaahan_available and vaahan:
            try:
                vahan_result = vaahan.validate_for_emission_test(vehicle_id)
                result["vahan_status"] = {
                    "eligible": vahan_result.get("eligible", False),
                    "reason": vahan_result.get("reason", ""),
                    "vehicle_info": vahan_result.get("vehicle_info"),
                }
                if not vahan_result.get("eligible"):
                    result["issues"].append(
                        f"VAHAN: {vahan_result.get('reason', 'Not eligible')}"
                    )
            except Exception as e:
                result["issues"].append(f"VAHAN check failed: {str(e)}")
        else:
            result["issues"].append("VAHAN bridge not available")

        # Blockchain check
        if blockchain_connected and blockchain:
            try:
                stats = blockchain.get_vehicle_stats(vehicle_id)
                result["blockchain_status"] = stats

                if stats.get("total_records", 0) == 0:
                    result["issues"].append("No emission records on blockchain")
                if stats.get("violations", 0) > 0:
                    result["issues"].append(
                        f"{stats['violations']} emission violation(s) on record"
                    )
                if stats.get("fraud_alerts", 0) > 0:
                    result["issues"].append(
                        f"{stats['fraud_alerts']} fraud alert(s) on record"
                    )
            except Exception as e:
                result["issues"].append(f"Blockchain check failed: {str(e)}")

            # Certificate check
            try:
                cert = blockchain.check_certificate(vehicle_id)
                result["certificate_status"] = cert
                if not cert.get("valid"):
                    result["issues"].append("No valid PUC certificate")
                else:
                    expiry = cert.get("expiry_timestamp", 0)
                    if expiry and expiry < int(time.time()):
                        result["issues"].append("PUC certificate has expired")
            except Exception as e:
                result["issues"].append(f"Certificate check failed: {str(e)}")
        else:
            result["issues"].append("Blockchain not connected")

        # Determine overall eligibility
        result["renewal_eligible"] = len(result["issues"]) == 0

        return jsonify({"success": True, "rto_check": result}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/rto/flagged", methods=["GET"])
def rto_flagged():
    """
    GET /api/rto/flagged
    Vehicles with expired PUC or multiple violations — flagged for RTO attention.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503
    try:
        vehicles = blockchain.get_registered_vehicles()
        flagged = []
        now = int(time.time())

        for vid in vehicles:
            try:
                reasons = []
                stats = blockchain.get_vehicle_stats(vid)
                cert = blockchain.check_certificate(vid)

                # Check for expired or missing certificate
                if not cert.get("valid"):
                    reasons.append("no_valid_certificate")
                elif cert.get("expiry_timestamp", 0) < now:
                    reasons.append("certificate_expired")

                # Multiple violations
                if stats.get("violations", 0) >= 3:
                    reasons.append("multiple_violations")

                # Fraud alerts
                if stats.get("fraud_alerts", 0) > 0:
                    reasons.append("fraud_detected")

                # High average CES
                if stats.get("avg_ces", 0) > 1.5 and stats.get("total_records", 0) > 0:
                    reasons.append("high_emissions")

                if reasons:
                    flagged.append({
                        "vehicle_id": vid,
                        "reasons": reasons,
                        "violations": stats.get("violations", 0),
                        "fraud_alerts": stats.get("fraud_alerts", 0),
                        "avg_ces": stats.get("avg_ces", 0.0),
                        "certificate_valid": cert.get("valid", False),
                        "certificate_expiry": cert.get("expiry_timestamp", 0),
                    })
            except Exception:
                pass

        # Sort by number of reasons (most flagged first)
        flagged.sort(key=lambda x: len(x["reasons"]), reverse=True)

        return jsonify({
            "success": True,
            "count": len(flagged),
            "flagged_vehicles": flagged,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── Notification Endpoint ───────────────────────────────

@app.route("/api/notifications", methods=["GET"])
def get_notifications():
    """
    GET /api/notifications
    Get recent system notifications (fraud alerts, cert expiry warnings, violations).
    Query params:
        type    — Filter by notification type (e.g., fraud_alert, violation_alert)
        limit   — Max results (default 50, max 100)
        since   — Only notifications after this unix timestamp
    """
    try:
        notif_type = request.args.get("type", "")
        limit = min(int(request.args.get("limit", 50)), 100)
        since = int(request.args.get("since", 0))

        # Check certificate expiry warnings (lazy generation)
        _check_cert_expiry_notifications()

        with _notifications_lock:
            result = list(_notifications)

        # Filter by type
        if notif_type:
            result = [n for n in result if n["type"] == notif_type]

        # Filter by timestamp
        if since > 0:
            result = [n for n in result if n["timestamp"] > since]

        # Most recent first, limited
        result.sort(key=lambda x: x["timestamp"], reverse=True)
        result = result[:limit]

        return jsonify({
            "success": True,
            "count": len(result),
            "notifications": result,
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── OBD-II Hardware Endpoints ───────────────────────────

@app.route("/api/obd/status", methods=["GET"])
def obd_status():
    """
    GET /api/obd/status
    Check if a real OBD-II ELM327 device is connected.
    """
    try:
        hw_connected = False
        port_name = None
        protocol = None
        dtcs = []

        if obd_hardware_available and _obd_connection and _obd_connection.is_connected():
            hw_connected = True
            port_name = _obd_connection.port_name()
            protocol = str(_obd_connection.protocol_name()) if hasattr(_obd_connection, 'protocol_name') else None

            # Try to read DTCs (diagnostic trouble codes)
            try:
                dtc_response = _obd_connection.query(obd_lib.commands.GET_DTC)
                if dtc_response and not dtc_response.is_null():
                    dtcs = [{"code": code, "description": desc} for code, desc in dtc_response.value]
            except Exception:
                pass

        return jsonify({
            "success": True,
            "obd": {
                "hardware_available": obd_hardware_available,
                "connected": hw_connected,
                "port": port_name,
                "protocol": protocol,
                "adapter_module": obd_adapter_available,
                "dtc_count": len(dtcs),
                "dtcs": dtcs,
            },
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/obd/read", methods=["POST"])
def obd_read():
    """
    POST /api/obd/read
    Read a single data frame from the connected OBD-II device.
    Returns: speed, rpm, coolant_temp, fuel_rate (if available).
    """
    try:
        if not obd_hardware_available:
            return jsonify({
                "success": False,
                "error": "python-obd library not installed",
            }), 503

        if not _obd_connection or not _obd_connection.is_connected():
            return jsonify({
                "success": False,
                "error": "No OBD-II device connected",
            }), 503

        frame = {}

        # Read standard PIDs
        pid_map = {
            "speed": obd_lib.commands.SPEED,
            "rpm": obd_lib.commands.RPM,
            "coolant_temp": obd_lib.commands.COOLANT_TEMP,
            "intake_temp": obd_lib.commands.INTAKE_TEMP,
            "throttle_pos": obd_lib.commands.THROTTLE_POS,
            "engine_load": obd_lib.commands.ENGINE_LOAD,
            "fuel_pressure": obd_lib.commands.FUEL_PRESSURE,
            "maf": obd_lib.commands.MAF,
        }

        for key, cmd in pid_map.items():
            try:
                response = _obd_connection.query(cmd)
                if response and not response.is_null():
                    frame[key] = response.value.magnitude if hasattr(response.value, 'magnitude') else float(response.value)
                else:
                    frame[key] = None
            except Exception:
                frame[key] = None

        # Estimate fuel rate from MAF if direct fuel rate not available
        if frame.get("maf") is not None and frame.get("maf", 0) > 0:
            # Approximate: fuel_rate (L/h) = MAF (g/s) / AFR / fuel_density * 3600
            # Stoichiometric AFR for petrol ~ 14.7, density ~ 750 g/L
            frame["fuel_rate_estimated"] = round(frame["maf"] / 14.7 / 750 * 3600, 3)

        frame["timestamp"] = int(time.time())
        frame["source"] = "hardware"

        return jsonify({"success": True, "data": frame}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── Vehicle Verification ──────────────────────────────────

@app.route("/api/vehicle/verify/<registration>", methods=["GET"])
def verify_vehicle(registration: str):
    """GET /api/vehicle/verify/<registration> — Verify via VAHAN bridge."""
    try:
        if vaahan_available and vaahan:
            result = vaahan.validate_for_emission_test(registration)
            return jsonify({"success": True, "result": result}), 200
        return jsonify({"success": False, "error": "VAHAN bridge not available"}), 503
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────── Status ────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def status():
    """GET /api/status — System health, contract addresses, module availability."""
    try:
        bc_status = blockchain.get_status() if blockchain_connected else {}
        return jsonify({
            "success": True,
            "connected": bc_status.get("connected", False),
            "blockNumber": bc_status.get("block_number"),
            "registryAddress": bc_status.get("registry_address"),
            "pucCertAddress": bc_status.get("puc_cert_address"),
            "greenTokenAddress": bc_status.get("green_token_address"),
            "account": bc_status.get("account"),
            "networkId": bc_status.get("network_id"),
            "modules": {
                "vsp": vsp_available,
                "fraud_detector": fraud_available,
                "lstm_predictor": predictor_available,
                "vaahan_bridge": vaahan_available,
                "obd_adapter": obd_adapter_available,
                "obd_hardware": obd_hardware_available,
                "obd_connected": bool(_obd_connection and _obd_connection.is_connected()) if obd_hardware_available else False,
                "jwt_auth": True,
            },
            "architecture": "3-node",
            "node": "testing-station",
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ────────────────────────── Run Server ─────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"

    print("=" * 65)
    print("Smart PUC — Testing Station Backend (Node 2 of 3)")
    print("=" * 65)
    print(f"  Port             : {port}")
    print(f"  Blockchain       : {'Connected' if blockchain_connected else 'Offline'}")
    if blockchain_connected:
        status_info = blockchain.get_status()
        print(f"  EmissionRegistry : {status_info.get('registry_address', 'N/A')}")
        print(f"  PUCCertificate   : {status_info.get('puc_cert_address', 'N/A')}")
        print(f"  GreenToken       : {status_info.get('green_token_address', 'N/A')}")
        print(f"  Station Account  : {status_info.get('account', 'N/A')}")
    print(f"  VSP Model        : {'Available' if vsp_available else 'Not loaded'}")
    print(f"  Fraud Detector   : {'Available' if fraud_available else 'Not loaded'}")
    print(f"  LSTM Predictor   : {'Available' if predictor_available else 'Not loaded'}")
    print(f"  VAHAN Bridge     : {'Available' if vaahan_available else 'Not loaded'}")
    print(f"  OBD Adapter      : {'Available' if obd_adapter_available else 'Not loaded'}")
    print(f"  OBD Hardware     : {'Connected' if (_obd_connection and _obd_connection.is_connected()) else 'Not connected' if obd_hardware_available else 'Not installed'}")
    print(f"  JWT Auth         : Enabled (admin login at /api/auth/login)")
    print(f"  Rate Limit       : {_RATE_LIMIT_MAX} req/{_RATE_LIMIT_WINDOW}s per IP")
    print("=" * 65)

    app.run(host="0.0.0.0", port=port, debug=debug)
