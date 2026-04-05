"""
Smart PUC — Flask REST API (Multi-Pollutant Version)
=====================================================
Central backend API that orchestrates the upgraded pipeline:
    WLTC Simulator -> VSP Model -> Multi-Pollutant Engine ->
    Fraud Detector -> Blockchain Storage -> Dashboard

Endpoints:
    GET   /api/simulate            — Trigger simulation, return telemetry + all pollutants
    POST  /api/record              — Calculate emissions & write to blockchain
    GET   /api/history/<vehicleId> — All on-chain records for a vehicle
    GET   /api/violations          — All FAIL records across all vehicles
    GET   /api/status              — Backend health & blockchain connection status
    GET   /api/certificate/<vid>   — PUC certificate status for a vehicle
    GET   /api/vehicle-stats/<vid> — Aggregated stats for a vehicle

References:
    EPA MOVES3 (2020), ARAI BSVI (2020), UN ECE R154 (WLTP)

Requires:
    - Flask, flask-cors, Web3.py, numpy, scikit-learn (optional)
    - Ganache running on port 7545
    - Truffle migration completed
"""

import functools
import hashlib
import hmac
import os
import sys
import threading
import time
import traceback
from typing import Optional

from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

# Add parent directory to path for physics/ml imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulator import WLTCSimulator, OBDSimulator
from emission_engine import calculate_emissions, calculate_co2, process_obd_reading
from blockchain_connector import BlockchainConnector

# Import VSP model
try:
    from physics.vsp_model import calculate_vsp, get_operating_mode_bin
    vsp_available = True
except ImportError:
    vsp_available = False

# Import fraud detector
try:
    from ml.fraud_detector import FraudDetector
    fraud_detector = FraudDetector()
    fraud_available = True

    # Train Isolation Forest on realistic WLTC cycle data (if available)
    # or fall back to correlated synthetic data derived from the simulator
    import numpy as _np
    _training_data_path = os.path.join(os.path.dirname(__file__), "..", "ml", "training_data.npy")
    _baseline_data = []

    if os.path.exists(_training_data_path):
        # Use pre-generated WLTC cycle data (columns: speed, rpm, fuel_rate, accel, co2, nox, vsp, ces)
        _raw = _np.load(_training_data_path)
        for row in _raw[:600]:  # use first 600 points (~1/3 cycle)
            _baseline_data.append({
                "speed": float(row[0]), "rpm": float(row[1]),
                "fuel_rate": float(row[2]), "acceleration": float(row[3]),
                "co2": float(row[4]), "vsp": float(row[6]),
            })
    else:
        # Fallback: generate correlated data from WLTC simulator
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
    del _baseline_data, _np
except ImportError:
    fraud_detector = None
    fraud_available = False

# Import LSTM predictor
try:
    from ml.lstm_predictor import create_predictor
    predictor = create_predictor(use_lstm=False)  # Use mock by default
    predictor_available = True
except ImportError:
    predictor = None
    predictor_available = False

# WLTC phase enum -> integer mapping for blockchain storage
_PHASE_TO_INT = {"Low": 0, "Medium": 1, "High": 2, "Extra High": 3}

# ────────────────────────── App Setup ────────────────────────────────────

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

app = Flask(__name__)

# Custom JSON encoder to handle numpy types
import json as _json
import numpy as _np_json

class _NumpyJSONEncoder(_json.JSONEncoder):
    """Handle numpy types in Flask JSON responses."""
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

app.json_encoder = _NumpyJSONEncoder  # type: ignore[attr-defined]
# Also set via the modern Flask way
app.json_provider_class = None  # Use default but override serializer

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
# CORS: allow all origins in development, restrict via CORS_ORIGINS env var in production
_cors_origins = os.getenv("CORS_ORIGINS", "*")
if _cors_origins == "*":
    CORS(app)
else:
    CORS(app, origins=_cors_origins.split(","))

# API key authentication for write endpoints
API_KEY = os.getenv("API_KEY", "")  # Set in .env for production; empty = auth disabled

# Simple in-memory rate limiting (per-IP, per-minute) — thread-safe
_rate_limit_lock = threading.Lock()
_rate_limit_store: dict = {}
_RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "120"))  # requests per minute
_RATE_LIMIT_WINDOW = 60  # seconds

DEFAULT_VEHICLE_ID = os.getenv("DEFAULT_VEHICLE_ID", "MH12AB1234")


def require_api_key(f):
    """Decorator: require valid API key for write endpoints.

    When API_KEY is empty (dev mode), authentication is skipped.
    The key must be sent as ``X-API-Key`` header or ``api_key`` query parameter.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not API_KEY:
            return f(*args, **kwargs)  # auth disabled in dev mode
        provided = request.headers.get("X-API-Key") or request.args.get("api_key", "")
        if not provided or not hmac.compare_digest(provided, API_KEY):
            return jsonify({"success": False, "error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated


@app.before_request
def _check_rate_limit():
    """Thread-safe per-IP rate limiting to prevent API abuse."""
    client_ip = request.remote_addr or "unknown"
    now = time.time()

    with _rate_limit_lock:
        # Clean expired entries
        expired = [ip for ip, (_, ts) in _rate_limit_store.items() if now - ts > _RATE_LIMIT_WINDOW]
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

# ────────────────────────── Initialise Components ────────────────────────

simulator = WLTCSimulator(vehicle_id=DEFAULT_VEHICLE_ID)

try:
    blockchain = BlockchainConnector()
    blockchain_connected = True
except Exception as e:
    print(f"Blockchain connection failed: {e}")
    print("   API will run in offline mode (no on-chain writes).")
    blockchain = None
    blockchain_connected = False

# Track readings count for LSTM window and cold-start timing (thread-safe)
_readings_lock = threading.Lock()
readings_count = 0
_engine_start_time: float = time.time()  # timestamp of first reading for cold-start

# ────────────────────────── Helper Functions ─────────────────────────────

def compute_full_emission(
    speed: float,
    rpm: int,
    fuel_rate: float,
    fuel_type: str = "petrol",
    acceleration: float = 0.0,
    ambient_temp: float = 25.0,
    altitude: float = 0.0,
) -> dict:
    """
    Run the full emission pipeline: VSP -> operating mode -> multi-pollutant engine.

    Args:
        speed:        Vehicle speed in km/h
        rpm:          Engine RPM
        fuel_rate:    Fuel consumption in L/100km
        fuel_type:    'petrol' or 'diesel'
        acceleration: Vehicle acceleration in m/s^2
        ambient_temp: Ambient temperature in Celsius
        altitude:     Altitude in meters

    Returns:
        dict with all emission values, CES score, and compliance status
    """
    speed_mps = speed / 3.6

    # Calculate VSP and operating mode
    vsp_value = 0.0
    op_mode_bin = 11  # default
    if vsp_available:
        vsp_value = calculate_vsp(speed_mps, acceleration)
        op_mode_bin = get_operating_mode_bin(vsp_value, speed_mps)

    # Determine cold start: first 180 seconds after engine start (COPERT 5)
    cold_start = (time.time() - _engine_start_time) < 180.0

    # Calculate multi-pollutant emissions
    emission = calculate_emissions(
        speed_kmh=speed,
        acceleration=acceleration,
        rpm=rpm,
        fuel_rate=fuel_rate,
        fuel_type=fuel_type,
        operating_mode_bin=op_mode_bin,
        ambient_temp=ambient_temp,
        altitude=altitude,
        cold_start=cold_start,
    )

    emission["vsp"] = round(vsp_value, 3)
    emission["operating_mode_bin"] = op_mode_bin
    return emission


# ────────────────────────── API Endpoints ────────────────────────────────

@app.route("/api/simulate", methods=["GET"])
def simulate():
    """
    GET /api/simulate
    Triggers WLTC simulation and returns telemetry enriched with all 5 pollutants.
    """
    try:
        vehicle_id = request.args.get("vehicle_id", DEFAULT_VEHICLE_ID)
        simulator.vehicle_id = vehicle_id

        reading = simulator.generate_reading()

        emission = compute_full_emission(
            speed=reading["speed"],
            rpm=reading["rpm"],
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
    Full pipeline: simulate/accept data -> VSP -> emissions -> fraud check ->
    LSTM predict -> blockchain store -> return enriched result.
    """
    global readings_count

    try:
        data = request.get_json(silent=True) or {}
        vehicle_id = data.get("vehicle_id", DEFAULT_VEHICLE_ID)

        # Get telemetry data with input validation
        if "fuel_rate" in data and "speed" in data:
            fuel_rate = max(0.0, min(float(data["fuel_rate"]), 50.0))
            speed = max(0.0, min(float(data["speed"]), 250.0))
            rpm = max(0, min(int(data.get("rpm", 2000)), 8000))
            fuel_type = data.get("fuel_type", "petrol")
            if fuel_type not in ("petrol", "diesel"):
                fuel_type = "petrol"
            acceleration = max(-10.0, min(float(data.get("acceleration", 0.0)), 10.0))
        else:
            reading = simulator.generate_reading()
            fuel_rate = reading["fuel_rate"]
            speed = reading["speed"]
            rpm = reading["rpm"]
            fuel_type = reading.get("fuel_type", "petrol")
            acceleration = reading.get("acceleration", 0.0)

        # Get WLTC phase from simulator (map enum string to integer 0-3)
        wltc_phase = 0
        if hasattr(simulator, '_current_time'):
            phase_obj = simulator.get_phase(simulator._current_time)
            phase_str = phase_obj.value if hasattr(phase_obj, 'value') else str(phase_obj)
            wltc_phase = _PHASE_TO_INT.get(phase_str, 0)

        timestamp = int(time.time())

        # Step 1: Calculate multi-pollutant emissions
        emission = compute_full_emission(
            speed=speed,
            rpm=rpm,
            fuel_rate=fuel_rate,
            fuel_type=fuel_type,
            acceleration=acceleration,
        )

        with _readings_lock:
            readings_count += 1

        # Step 2: Fraud detection
        fraud_result = {"fraud_score": 0.0, "is_fraud": False, "severity": "LOW", "violations": []}
        if fraud_available and fraud_detector:
            reading_for_fraud = {
                "speed": speed,
                "rpm": rpm,
                "fuel_rate": fuel_rate,
                "acceleration": acceleration,
                "co2": emission.get("co2_g_per_km", 0),
                "vsp": emission.get("vsp", 0),
            }
            fraud_result = fraud_detector.analyze(reading_for_fraud)
            # Note: analyze() already updates the temporal checker internally,
            # so we do NOT call fraud_detector.update() separately to avoid
            # double-inserting into the temporal window.

        # Step 3: LSTM prediction
        predictions = None
        if predictor_available and predictor:
            predictor.update({
                "speed": speed,
                "rpm": rpm,
                "fuel_rate": fuel_rate,
                "acceleration": acceleration,
                "co2": emission.get("co2_g_per_km", 0),
                "nox": emission.get("nox_g_per_km", 0),
                "vsp": emission.get("vsp", 0),
                "ces_score": emission.get("ces_score", 0),
            })
            predictions = predictor.predict_next()

        # Step 4: Write to blockchain
        tx_result = {"tx_hash": None, "status": "offline", "block_number": None}
        if blockchain_connected and blockchain:
            try:
                tx_result = blockchain.store_emission(
                    vehicle_id=vehicle_id,
                    co2=emission.get("co2_g_per_km", 0),
                    co=emission.get("co_g_per_km", 0),
                    nox=emission.get("nox_g_per_km", 0),
                    hc=emission.get("hc_g_per_km", 0),
                    pm25=emission.get("pm25_g_per_km", 0),
                    ces_score=emission.get("ces_score", 0),
                    fraud_score=fraud_result.get("fraud_score", 0),
                    vsp=emission.get("vsp", 0),
                    wltc_phase=wltc_phase,
                    timestamp=timestamp,
                )
            except Exception as e:
                print(f"Blockchain write failed: {e}")
                tx_result = {"tx_hash": None, "status": "failed", "block_number": None}

        # Step 5: Get vehicle stats
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
            # Telemetry
            "speed": speed,
            "rpm": rpm,
            "fuel_rate": fuel_rate,
            "fuel_type": fuel_type,
            "acceleration": round(acceleration, 3),
            # Emissions (all 5 pollutants)
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
            # Predictions
            "predictions": predictions,
            # Vehicle stats
            "vehicle_stats": vehicle_stats,
            "timestamp": timestamp,
        }

        return jsonify({"success": True, "data": response_data}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/history/<vehicle_id>", methods=["GET"])
def history(vehicle_id: str):
    """
    GET /api/history/<vehicleId>
    Returns all on-chain emission records for the given vehicle.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503

    try:
        records = blockchain.get_history(vehicle_id)
        return jsonify({
            "success": True,
            "vehicle_id": vehicle_id,
            "count": len(records),
            "records": records,
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/violations", methods=["GET"])
def violations():
    """
    GET /api/violations
    Returns all FAIL records across all registered vehicles.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503

    try:
        all_violations = []
        vehicles = blockchain.get_registered_vehicles()

        for vid in vehicles:
            vehicle_violations = blockchain.get_violations(vid)
            all_violations.extend(vehicle_violations)

        all_violations.sort(key=lambda x: x["timestamp"], reverse=True)

        return jsonify({
            "success": True,
            "count": len(all_violations),
            "violations": all_violations,
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vehicle-stats/<vehicle_id>", methods=["GET"])
def vehicle_stats(vehicle_id: str):
    """
    GET /api/vehicle-stats/<vehicleId>
    Returns aggregated stats for a vehicle.
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503

    try:
        stats = blockchain.get_vehicle_stats(vehicle_id)
        return jsonify({"success": True, "stats": stats}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/certificate/<vehicle_id>", methods=["GET"])
def certificate(vehicle_id: str):
    """
    GET /api/certificate/<vehicleId>
    Returns PUC certificate status for a vehicle.
    Currently returns mock data — will integrate with PUCCertificate.sol.
    """
    try:
        # Mock certificate response until PUCCertificate contract is deployed
        cert = {
            "valid": False,
            "revoked": False,
            "token_id": None,
            "issue_date": None,
            "expiry_date": None,
            "avg_ces": None,
            "vehicle_id": vehicle_id,
        }

        if blockchain_connected and blockchain:
            try:
                stats = blockchain.get_vehicle_stats(vehicle_id)
                if stats["total_records"] > 0 and stats["avg_ces"] < 1.0:
                    cert["valid"] = True
                    cert["avg_ces"] = int(stats["avg_ces"] * 10000)
                    cert["issue_date"] = int(time.time()) - 86400
                    cert["expiry_date"] = cert["issue_date"] + (180 * 86400)
                    cert["token_id"] = hash(vehicle_id) % 100000
            except Exception:
                pass

        return jsonify({"success": True, "certificate": cert}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/status", methods=["GET"])
def status():
    """
    GET /api/status
    Returns backend health, blockchain connection, and module availability.
    """
    try:
        if blockchain_connected:
            bc_status = blockchain.get_status()
            return jsonify({
                "success": True,
                "connected": bc_status["connected"],
                "blockNumber": bc_status["block_number"],
                "contractAddress": bc_status["contract_address"],
                "account": bc_status["account"],
                "networkId": bc_status["network_id"],
                "modules": {
                    "vsp": vsp_available,
                    "fraud_detector": fraud_available,
                    "lstm_predictor": predictor_available,
                },
            }), 200
        else:
            return jsonify({
                "success": True,
                "connected": False,
                "blockNumber": None,
                "contractAddress": None,
                "account": None,
                "networkId": None,
                "modules": {
                    "vsp": vsp_available,
                    "fraud_detector": fraud_available,
                    "lstm_predictor": predictor_available,
                },
            }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ────────────────────────── Run Server ───────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"

    print("=" * 60)
    print("Smart PUC — Backend API Server (Multi-Pollutant)")
    print(f"   Port           : {port}")
    print(f"   Blockchain     : {'Connected' if blockchain_connected else 'Offline'}")
    if blockchain_connected:
        print(f"   Contract       : {blockchain.contract_address}")
        print(f"   Account        : {blockchain.address}")
    print(f"   VSP Model      : {'Available' if vsp_available else 'Not loaded'}")
    print(f"   Fraud Detector : {'Available' if fraud_available else 'Not loaded'}")
    print(f"   LSTM Predictor : {'Available' if predictor_available else 'Not loaded'}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug)
