"""
Smart PUC — Testing Station Backend (FastAPI + Pydantic)
=========================================================

Node 2 of the 3-node architecture. Replaces the legacy Flask `app.py`
with a FastAPI application that:

  * Uses Pydantic models for request validation.
  * Exposes an automatic OpenAPI / Swagger UI at ``/docs``.
  * Runs asynchronously under ``uvicorn``.
  * Preserves every endpoint path and response shape from the Flask
    version, so the frontend, OBD simulator, and benchmark scripts work
    unchanged.

Run in development::

    cd backend && uvicorn main:app --host 0.0.0.0 --port 5000 --reload

Run in Docker: see Dockerfile.backend.
"""

from __future__ import annotations

import datetime
import json as _json
import os
import sys
import time
import traceback
from typing import Any, Optional

# Force utf-8 stdout/stderr so em-dashes and box-drawing characters in the
# banner don't crash into Windows' default cp1252 console codec and turn
# into `?` / replacement characters.
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    _reconfigure = getattr(_stream, "reconfigure", None) if _stream is not None else None
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import numpy as _np
import jwt
from dotenv import load_dotenv
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Add the repo root to sys.path (so physics/ml/integrations imports resolve)
# AND the backend directory itself (so sibling modules like simulator,
# emission_engine, blockchain_connector, persistence import cleanly whether
# the app is started via `uvicorn backend.main:app` or `python -m uvicorn
# main:app` from inside the backend directory).
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.join(_BACKEND_DIR, "..")
for _p in (_REPO_ROOT, _BACKEND_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from simulator import WLTCSimulator  # noqa: E402
from emission_engine import calculate_emissions  # noqa: E402
from blockchain_connector import BlockchainConnector  # noqa: E402
from persistence import PersistenceStore  # noqa: E402
from phase_listener import PhaseListener  # noqa: E402
import privacy as _privacy  # noqa: E402

try:
    from ml.station_fraud_detector import StationFraudDetector  # noqa: E402
    _station_fraud_available = True
except Exception:  # noqa: BLE001
    StationFraudDetector = None  # type: ignore[assignment,misc]
    _station_fraud_available = False

try:
    from ml.pre_puc_predictor import PrePUCPredictor  # noqa: E402
    _pre_puc_available = True
except Exception:  # noqa: BLE001
    PrePUCPredictor = None  # type: ignore[assignment,misc]
    _pre_puc_available = False

# Singleton: trained-once, re-used across requests. Training uses a fixed
# random_state so the model is deterministic across backend restarts.
_pre_puc_predictor: Any = None
if _pre_puc_available and PrePUCPredictor is not None:
    try:
        _pre_puc_predictor = PrePUCPredictor(random_state=42)
        _pre_puc_predictor.train_synthetic(n_samples=2000)
    except Exception as _ex:  # noqa: BLE001
        print(f"  PrePUCPredictor init failed: {_ex}")
        _pre_puc_predictor = None

from dependencies import (  # noqa: E402
    AUTH_PASSWORD,
    AUTH_USERNAME,
    JWT_ALGORITHM,
    JWT_EXPIRY_HOURS,
    JWT_SECRET,
    RATE_LIMIT_MAX,
    RATE_LIMIT_WINDOW,
    auth_is_configured,
    rate_limit_middleware,
    require_api_key,
    require_api_key_or_jwt,
    require_auth,
    verify_credentials,
)
from schemas import (  # noqa: E402
    EmissionRecordRequest,
    IssueCertificateRequest,
    LoginRequest,
    LoginResponse,
    RedeemTokensRequest,
    RevokeCertificateRequest,
    RTOEnforceRequest,
)


# ────────────────────────── Env / optional modules ───────────────────────

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

DEFAULT_VEHICLE_ID = os.getenv("DEFAULT_VEHICLE_ID", "MH12AB1234")

# ─── Privacy mode (audit G4) ────────────────────────────────────────────────
# When PRIVACY_MODE is enabled, the /api/record hot path replaces the raw
# vehicle_id with a salted pseudonym (see backend/privacy.py) before it is
# persisted in the SQLite telemetry mirror or forwarded to the blockchain
# connector. Read once at import time so per-process behaviour is
# deterministic; toggling the env var requires a restart.
PRIVACY_MODE_ENABLED: bool = os.getenv("PRIVACY_MODE", "").lower() in (
    "1", "true", "yes", "on",
)


def maybe_pseudonymize(vehicle_id: str) -> str:
    """Return a salted pseudonym for *vehicle_id* when PRIVACY_MODE is on.

    Idempotent: if the input already looks like an ``sp:`` pseudonym it is
    returned unchanged. When PRIVACY_MODE is off this is a no-op, so the
    function is safe to call unconditionally on the hot path.
    """
    if not PRIVACY_MODE_ENABLED:
        return vehicle_id
    if isinstance(vehicle_id, str) and vehicle_id.startswith("sp:"):
        return vehicle_id
    return _privacy.salted_pseudonym(vehicle_id)


# ─── Idempotency-Key cache (audit G7, Stripe-style) ─────────────────────────
# Small in-process LRU of (key -> (expiry_epoch, cached_json_body)) keyed on
# the Idempotency-Key header posted against /api/record. Retried submissions
# with the same key return the cached response instead of re-submitting to
# the blockchain. The cache is bounded (1024 entries) and entries expire
# after IDEMPOTENCY_TTL_SECONDS.
import threading as _threading_early  # noqa: E402
from collections import OrderedDict  # noqa: E402

IDEMPOTENCY_MAX_ENTRIES: int = 1024
IDEMPOTENCY_TTL_SECONDS: int = 600  # 10 minutes
_idempotency_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_idempotency_lock = _threading_early.Lock()


def _idempotency_lookup(key: str) -> Optional[dict]:
    """Return the cached response body for *key* if fresh, else None."""
    if not key:
        return None
    now = time.time()
    with _idempotency_lock:
        entry = _idempotency_cache.get(key)
        if entry is None:
            return None
        expiry, body = entry
        if expiry < now:
            _idempotency_cache.pop(key, None)
            return None
        _idempotency_cache.move_to_end(key)
        return body


def _idempotency_store(key: str, body: dict) -> None:
    if not key:
        return
    with _idempotency_lock:
        expiry = time.time() + IDEMPOTENCY_TTL_SECONDS
        _idempotency_cache[key] = (expiry, body)
        _idempotency_cache.move_to_end(key)
        while len(_idempotency_cache) > IDEMPOTENCY_MAX_ENTRIES:
            _idempotency_cache.popitem(last=False)

# ─── Calibration model (Phase 3, feature-flagged) ─────────────────────────
CALIBRATION_ENABLED = os.getenv("CALIBRATION_ENABLED", "").lower() in ("1", "true")
_calibration_model = None
if CALIBRATION_ENABLED:
    try:
        from ml.calibration_model import CalibrationModel
        _calibration_model = CalibrationModel.load_checkpoint("data/calibration_model_v1.pkl")
    except Exception:
        pass

# ─── Micro-assessment engine (Phase 3) ────────────────────────────────────
_micro_engine = None  # initialised after persistence store is ready

try:
    from ml.micro_assessment import MicroAssessmentEngine
    _micro_assessment_available = True
except ImportError:
    MicroAssessmentEngine = None  # type: ignore[assignment,misc]
    _micro_assessment_available = False

# Optional physics module
try:
    from physics.vsp_model import calculate_vsp, get_operating_mode_bin
    vsp_available = True
except ImportError:
    vsp_available = False

# Optional VAHAN bridge (simulated integration point — see integrations/vaahan_bridge.py)
try:
    from integrations.vaahan_bridge import VaahanBridge
    vaahan = VaahanBridge(use_mock=True)
    vaahan_available = True
except ImportError:
    vaahan = None
    vaahan_available = False

# Optional software OBD frame parser
try:
    from integrations.obd_adapter import parse_obd_frame  # noqa: F401
    obd_adapter_available = True
except ImportError:
    obd_adapter_available = False

# Optional real OBD-II hardware (python-obd + ELM327)
try:
    import obd as obd_lib
    obd_hardware_available = True
except ImportError:
    obd_lib = None
    obd_hardware_available = False

# Optional ML fraud detector
try:
    from ml.fraud_detector import FraudDetector
    fraud_detector = FraudDetector()
    fraud_available = True
    _baseline_data: list = []
    _training_data_path = os.path.join(os.path.dirname(__file__), "..", "ml", "training_data.npy")
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

# Optional LSTM / linear emission predictor
try:
    from ml.lstm_predictor import create_predictor
    predictor = create_predictor(use_lstm=False)
    predictor_available = True
except ImportError:
    predictor = None
    predictor_available = False


_PHASE_TO_INT = {"Low": 0, "Medium": 1, "High": 2, "Extra High": 3}


# ────────────────────────── Numpy-safe JSON encoder ──────────────────────

def _clean_numpy(obj: Any) -> Any:
    """Recursively convert numpy types to native Python so that
    FastAPI's default JSON encoder accepts the payload."""
    if isinstance(obj, dict):
        return {k: _clean_numpy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_numpy(v) for v in obj]
    if isinstance(obj, (_np.integer,)):
        return int(obj)
    if isinstance(obj, (_np.floating,)):
        return float(obj)
    if isinstance(obj, (_np.bool_,)):
        return bool(obj)
    if isinstance(obj, _np.ndarray):
        return obj.tolist()
    return obj


def ok(payload: Optional[dict] = None, **kwargs) -> JSONResponse:
    """Return a success JSON response with the legacy envelope shape."""
    data = {"success": True}
    if payload:
        data.update(payload)
    if kwargs:
        data.update(kwargs)
    return JSONResponse(_clean_numpy(data))


def err(message: str, status_code: int = 500) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


# ────────────────────────── Lifespan & FastAPI app ─────────────────────

@asynccontextmanager
async def _lifespan(application: FastAPI):
    """FastAPI lifespan handler — replaces the deprecated @app.on_event."""
    # ── startup ──
    print("=" * 65)
    print("Smart PUC — Testing Station Backend (Node 2 of 3)")
    print("=" * 65)
    print(f"  Framework        : FastAPI {application.version}")
    print(f"  Blockchain       : {'Connected' if blockchain_connected else 'Offline'}")
    if blockchain_connected:
        try:
            s = blockchain.get_status()
            print(f"  EmissionRegistry : {s.get('registry_address', 'N/A')}")
            print(f"  PUCCertificate   : {s.get('puc_cert_address', 'N/A')}")
            print(f"  GreenToken       : {s.get('green_token_address', 'N/A')}")
            print(f"  Station Account  : {s.get('account', 'N/A')}")
        except Exception:
            pass
    print(f"  VSP Model        : {'Available' if vsp_available else 'Not loaded'}")
    print(f"  Fraud Detector   : {'Available' if fraud_available else 'Not loaded'}")
    print(f"  LSTM Predictor   : {'Available' if predictor_available else 'Not loaded'}")
    print(f"  VAHAN Bridge     : {'Available (simulated)' if vaahan_available else 'Not loaded'}")
    print(f"  OBD Adapter      : {'Available' if obd_adapter_available else 'Not loaded'}")
    print(f"  OBD Hardware     : {'Connected' if (_obd_connection and _obd_connection.is_connected()) else 'Not connected' if obd_hardware_available else 'Not installed'}")
    print(f"  JWT Auth         : {'Configured' if auth_is_configured() else 'Disabled (not configured)'}")
    print(f"  Persistence      : {'SQLite' if store.enabled else 'in-memory'}")
    print(f"  Rate Limit       : {RATE_LIMIT_MAX} req/{RATE_LIMIT_WINDOW}s per IP")
    print(f"  Calibration      : {'Enabled' if (_calibration_model and getattr(_calibration_model, 'is_trained', False)) else 'Disabled'}")
    print(f"  Micro-Assessment : {'Available' if _micro_engine else 'Not loaded'}")
    print(f"  Privacy Mode     : {'On' if PRIVACY_MODE_ENABLED else 'Off'}")
    print(f"  OpenAPI / Swagger: http://localhost:5000/docs")
    print("=" * 65)
    yield
    # ── shutdown ──


app = FastAPI(
    title="Smart PUC — Testing Station API",
    version="3.1.0",
    description="Node 2 of the Smart PUC 3-node trust architecture. Serves "
                "signed emission telemetry from OBD devices and writes it to "
                "on-chain EmissionRegistry / PUCCertificate / GreenToken "
                "contracts. See /docs for the full OpenAPI schema.",
    lifespan=_lifespan,
)

# CORS
_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
if _cors_origins == "*":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

# Rate-limit middleware
app.middleware("http")(rate_limit_middleware)


# ────────────────────────── Persistence store ────────────────────────────

_persistence_path = os.getenv("PERSISTENCE_DB", "").strip()
if _persistence_path and not os.path.isabs(_persistence_path):
    _persistence_path = os.path.join(os.path.dirname(__file__), "..", _persistence_path)
store = PersistenceStore(_persistence_path or None)
app.state.store = store  # exposed to the rate-limit middleware

# Initialise micro-assessment engine now that persistence is ready
if _micro_assessment_available and MicroAssessmentEngine is not None:
    _micro_engine = MicroAssessmentEngine(store, calibration_model=_calibration_model)
else:
    _micro_engine = None


# ────────────────────────── Backend state ────────────────────────────────

simulator = WLTCSimulator(vehicle_id=DEFAULT_VEHICLE_ID)

try:
    blockchain: Optional[BlockchainConnector] = BlockchainConnector()
    blockchain_connected = True
except Exception as _e:  # noqa: BLE001
    print(f"Blockchain connection failed: {_e}")
    print("   API will run in offline mode (no on-chain writes).")
    blockchain = None
    blockchain_connected = False

# ─────────────────── Chain event projection (phase_listener) ───────────────
# Read-only projection of PhaseCompleted + BatchRootCommitted events into
# SQLite. Driven by an explicit /api/chain-events/sync pull, not a
# long-running websocket subscription — the backend stays stateless
# enough to restart without losing anything. Closes audit Fix #10.
phase_listener: Optional[PhaseListener] = None
_PHASE_DB_PATH = os.getenv(
    "PHASE_LISTENER_DB",
    os.path.join(os.path.dirname(__file__), "..", "data", "chain_events.db"),
)
if blockchain_connected and blockchain is not None:
    try:
        phase_listener = PhaseListener(
            blockchain,
            db_path=_PHASE_DB_PATH,
            persistence_store=store,
        )
    except Exception as _pe:  # noqa: BLE001
        print(f"  Phase listener init failed: {_pe}")
        phase_listener = None

_engine_start_time = time.time()
readings_count = 0

# Optional ELM327 hardware connection
_obd_connection = None
if obd_hardware_available:
    try:
        _obd_connection = obd_lib.OBD()
        if not _obd_connection.is_connected():
            _obd_connection = None
            print("  OBD-II: No ELM327 device detected (will use simulator).")
        else:
            print(f"  OBD-II: Connected to {_obd_connection.port_name()}")
    except Exception as exc:
        _obd_connection = None
        print(f"  OBD-II: Connection attempt failed: {exc}")


# Notifications (in-memory mirror backed by SQLite via `store`)
import threading  # noqa: E402

_notifications_lock = threading.Lock()
_notifications: list = []
_MAX_NOTIFICATIONS = 100


def _add_notification(notif_type: str, message: str, vehicle_id: str = "", severity: str = "info") -> None:
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
    try:
        store.add_notification(notif_type, message, vehicle_id=vehicle_id, severity=severity)
    except Exception:
        pass


def _check_cert_expiry_notifications() -> None:
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


# ────────────────────────── Emission helper ──────────────────────────────

def compute_full_emission(speed, rpm, fuel_rate, fuel_type="petrol",
                          acceleration=0.0, ambient_temp=25.0, altitude=0.0):
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


# ════════════════════════════════════════════════════════════════════════
#  ROUTES
# ════════════════════════════════════════════════════════════════════════

# ─────────────── Health / Auth ───────────────────────────────────────────

@app.get("/health")
def healthz() -> dict:
    """Lightweight liveness probe used by docker-compose healthchecks."""
    return {"status": "ok", "version": app.version}


@app.post("/api/auth/login", response_model=LoginResponse)
def auth_login(req: LoginRequest):
    if not auth_is_configured():
        raise HTTPException(status_code=503,
                            detail="Authority auth is not configured on this server")
    if not verify_credentials(req.username, req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": req.username,
        "iat": now,
        "exp": now + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
        "role": "authority",
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return LoginResponse(token=token, expires_in=JWT_EXPIRY_HOURS * 3600, username=req.username)


# ─────────────── Core pipeline ───────────────────────────────────────────

@app.get("/api/simulate")
def simulate(vehicle_id: str = Query(DEFAULT_VEHICLE_ID)):
    try:
        simulator.vehicle_id = vehicle_id
        reading = simulator.generate_reading()
        emission = compute_full_emission(
            speed=reading["speed"], rpm=reading["rpm"],
            fuel_rate=reading["fuel_rate"],
            acceleration=reading.get("acceleration", 0.0),
        )
        return ok(data={**reading, **emission})
    except Exception as exc:
        traceback.print_exc()
        return err(str(exc))


@app.post("/api/record", dependencies=[Depends(require_api_key_or_jwt)])
def record(body: EmissionRecordRequest, request: Request):
    global readings_count
    try:
        # Idempotency-Key short-circuit (audit G7). Return the cached
        # response body untouched on retries within the TTL window so the
        # blockchain is not re-submitted.
        idempotency_key = request.headers.get("Idempotency-Key") or ""
        if idempotency_key:
            cached = _idempotency_lookup(idempotency_key)
            if cached is not None:
                return JSONResponse(_clean_numpy(cached))

        data = body.model_dump()
        raw_vehicle_id = data.get("vehicle_id") or DEFAULT_VEHICLE_ID
        # Privacy wiring (audit G4): when PRIVACY_MODE is enabled, replace
        # the raw registration number with a salted pseudonym for both the
        # SQLite mirror write and the on-chain submission. The lookup
        # endpoints (/api/certificate/*, /api/history/*) still use the
        # raw id.
        vehicle_id = maybe_pseudonymize(raw_vehicle_id)

        # Step 0: VAHAN lookup (non-blocking)
        vehicle_info = None
        if vaahan_available and vaahan:
            try:
                eligibility = vaahan.validate_for_emission_test(vehicle_id)
                vehicle_info = eligibility.get("vehicle_info")
                if not eligibility.get("eligible"):
                    print(f"  VAHAN: Vehicle {vehicle_id} not eligible: {eligibility.get('reason')}")
            except Exception:
                pass

        # Step 1: Telemetry
        device_signature = data.get("device_signature") or ""
        device_address = data.get("device_address") or ""
        device_nonce = data.get("nonce") or None

        if data.get("speed") is not None and data.get("fuel_rate") is not None:
            fuel_rate = max(0.0, min(float(data["fuel_rate"]), 50.0))
            speed = max(0.0, min(float(data["speed"]), 250.0))
            rpm = max(0, min(int(data.get("rpm") or 2000), 8000))
            fuel_type = data.get("fuel_type") or "petrol"
            if fuel_type not in ("petrol", "diesel"):
                fuel_type = "petrol"
            acceleration = max(-10.0, min(float(data.get("acceleration") or 0.0), 10.0))
        else:
            reading = simulator.generate_reading()
            fuel_rate = reading["fuel_rate"]
            speed = reading["speed"]
            rpm = reading["rpm"]
            fuel_type = reading.get("fuel_type", "petrol")
            acceleration = reading.get("acceleration", 0.0)

        wltc_phase = int(data.get("wltc_phase") or 0)
        if wltc_phase == 0 and hasattr(simulator, "_current_time"):
            phase_obj = simulator.get_phase(simulator._current_time)
            phase_str = phase_obj.value if hasattr(phase_obj, "value") else str(phase_obj)
            wltc_phase = _PHASE_TO_INT.get(phase_str, 0)

        timestamp = int(data.get("timestamp") or time.time())

        # Step 2: Emission calculation
        emission = compute_full_emission(
            speed=speed, rpm=rpm, fuel_rate=fuel_rate,
            fuel_type=fuel_type, acceleration=acceleration,
        )
        readings_count += 1

        # Step 2b: Calibration correction (Phase 3, feature-flagged)
        if _calibration_model is not None and getattr(_calibration_model, "is_trained", False):
            try:
                _cal_input = {
                    "speed": speed, "rpm": rpm, "fuel_rate": fuel_rate,
                    "acceleration": acceleration,
                    "co2_g_per_km": emission.get("co2_g_per_km", 0),
                    "co_g_per_km": emission.get("co_g_per_km", 0),
                    "nox_g_per_km": emission.get("nox_g_per_km", 0),
                    "hc_g_per_km": emission.get("hc_g_per_km", 0),
                    "pm25_g_per_km": emission.get("pm25_g_per_km", 0),
                }
                _cal_result = _calibration_model.calibrate(_cal_input)
                emission["calibrated_co2"] = _cal_result.get("calibrated_co2", 0)
                emission["calibrated_co"] = _cal_result.get("calibrated_co", 0)
                emission["calibrated_nox"] = _cal_result.get("calibrated_nox", 0)
                emission["calibrated_hc"] = _cal_result.get("calibrated_hc", 0)
                emission["calibrated_pm25"] = _cal_result.get("calibrated_pm25", 0)
                emission["calibrated_ces"] = _cal_result.get("calibrated_ces", 0)
                emission["calibration_confidence"] = _cal_result.get("confidence", 0)
            except Exception:
                pass  # calibration is best-effort; never blocks the pipeline

        # Step 3: Fraud detection
        fraud_result: dict = {"fraud_score": 0.0, "is_fraud": False, "severity": "LOW", "violations": []}
        if fraud_available and fraud_detector:
            reading_for_fraud = {
                "speed": speed, "rpm": rpm, "fuel_rate": fuel_rate,
                "acceleration": acceleration,
                "co2": emission.get("co2_g_per_km", 0),
                "vsp": emission.get("vsp", 0),
            }
            fraud_result = fraud_detector.analyze(reading_for_fraud)

        if fraud_result.get("is_fraud"):
            _add_notification(
                "fraud_alert",
                f"Fraud detected for {vehicle_id}: score={fraud_result['fraud_score']:.2f}, "
                f"severity={fraud_result.get('severity', 'UNKNOWN')}",
                vehicle_id=vehicle_id, severity="critical",
            )
        if emission.get("status") == "FAIL":
            _add_notification(
                "violation_alert",
                f"Emission violation for {vehicle_id}: CES={emission.get('ces_score', 0):.3f}, status=FAIL",
                vehicle_id=vehicle_id, severity="high",
            )

        # Step 4: Optional emission forecast
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
            try:
                predictions = predictor.predict_next()
            except Exception:
                predictions = None

        # Step 5: Blockchain write
        tx_result: dict = {"tx_hash": None, "status": "offline", "block_number": None, "gas_used": 0}
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
                    nonce=device_nonce,
                    idempotency_key=idempotency_key or None,
                )
            except Exception as exc:
                print(f"Blockchain write failed: {exc}")
                tx_result = {"tx_hash": None, "status": "failed", "block_number": None, "gas_used": 0}

        # Mirror the (possibly pseudonymised) reading into the SQLite
        # telemetry cold-store. This is the write path that audit G4
        # expects to see honour PRIVACY_MODE.
        try:
            store.record_telemetry(
                vehicle_id=vehicle_id,
                reading={
                    "speed": speed,
                    "rpm": rpm,
                    "fuel_rate": fuel_rate,
                    "fuel_type": fuel_type,
                    "acceleration": acceleration,
                    "co2_g_per_km": emission.get("co2_g_per_km", 0),
                    "co_g_per_km": emission.get("co_g_per_km", 0),
                    "nox_g_per_km": emission.get("nox_g_per_km", 0),
                    "hc_g_per_km": emission.get("hc_g_per_km", 0),
                    "pm25_g_per_km": emission.get("pm25_g_per_km", 0),
                    "ces_score": emission.get("ces_score", 0),
                    "status": emission.get("status", "UNKNOWN"),
                    "wltc_phase": wltc_phase,
                    "timestamp": timestamp,
                },
                onchain_tx=tx_result.get("tx_hash"),
                is_violation=emission.get("status") == "FAIL",
            )
        except Exception:
            pass

        # Step 6 / 7: Certificate eligibility + vehicle stats
        cert_eligible = None
        vehicle_stats = None
        if blockchain_connected and blockchain:
            try:
                cert_eligible = blockchain.is_certificate_eligible(vehicle_id)
            except Exception:
                pass
            try:
                vehicle_stats = blockchain.get_vehicle_stats(vehicle_id)
            except Exception:
                pass

        response_data = {
            "vehicle_id": vehicle_id,
            "txHash": tx_result.get("tx_hash"),
            "blockNumber": tx_result.get("block_number"),
            "tx_status": tx_result.get("status"),
            "gas_used": tx_result.get("gas_used", 0),
            "speed": speed,
            "rpm": rpm,
            "fuel_rate": fuel_rate,
            "fuel_type": fuel_type,
            "acceleration": round(acceleration, 3),
            "co2_g_per_km": emission.get("co2_g_per_km", 0),
            "co_g_per_km": emission.get("co_g_per_km", 0),
            "nox_g_per_km": emission.get("nox_g_per_km", 0),
            "hc_g_per_km": emission.get("hc_g_per_km", 0),
            "pm25_g_per_km": emission.get("pm25_g_per_km", 0),
            "ces_score": emission.get("ces_score", 0),
            "status": emission.get("status", "UNKNOWN"),
            "compliance": emission.get("compliance", {}),
            "vsp": emission.get("vsp", 0),
            "operating_mode_bin": emission.get("operating_mode_bin", 0),
            "wltc_phase": wltc_phase,
            "fraud_score": fraud_result.get("fraud_score", 0),
            "fraud_status": {
                "is_fraud": fraud_result.get("is_fraud", False),
                "severity": fraud_result.get("severity", "LOW"),
                "violations": fraud_result.get("violations", []),
            },
            "device_address": device_address,
            "device_signed": bool(device_signature),
            "certificate_eligible": cert_eligible,
            "predictions": predictions,
            "vehicle_info": {
                "fuel_type": vehicle_info.get("fuel_type") if vehicle_info else None,
                "bs_norm": vehicle_info.get("bs_norm") if vehicle_info else None,
                "manufacturer": vehicle_info.get("manufacturer") if vehicle_info else None,
                "model": vehicle_info.get("model") if vehicle_info else None,
            } if vehicle_info else None,
            "vehicle_stats": vehicle_stats,
            "timestamp": timestamp,
        }
        # Attach pre-PUC failure forecast if predictor is available and
        # we have enough historical records for the vehicle.
        if _pre_puc_predictor is not None and blockchain_connected and blockchain is not None:
            try:
                hist = blockchain.get_emission_history(vehicle_id, limit=20)
                if len(hist) >= 5:
                    records_for_puc = [
                        {
                            "co2_g_per_km": r.get("co2Level", 0) / 1000,
                            "co_g_per_km": r.get("coLevel", 0) / 1000,
                            "nox_g_per_km": r.get("noxLevel", 0) / 1000,
                            "hc_g_per_km": r.get("hcLevel", 0) / 1000,
                            "pm25_g_per_km": r.get("pm25Level", 0) / 1000,
                            "ces_score": r.get("cesScore", 0) / 10000,
                        }
                        for r in hist
                    ]
                    response_data["pre_puc_forecast"] = _pre_puc_predictor.predict(records_for_puc)
            except Exception:  # noqa: BLE001 — non-critical augmentation
                pass
        cached_body = {"success": True, "data": response_data}
        if idempotency_key:
            _idempotency_store(idempotency_key, cached_body)
        return JSONResponse(_clean_numpy(cached_body))
    except Exception as exc:
        traceback.print_exc()
        return err(str(exc))


# ─────────────── Vehicle data (no auth) ──────────────────────────────────

@app.get("/api/history/{vehicle_id}")
def history(vehicle_id: str, page: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        records = blockchain.get_history_paginated(vehicle_id, page * limit, limit)
        total = blockchain.get_record_count(vehicle_id)
        return ok(vehicle_id=vehicle_id, count=total, page=page, limit=limit, records=records)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/violations")
def violations():
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        all_violations: list = []
        for vid in blockchain.get_registered_vehicles():
            all_violations.extend(blockchain.get_violations(vid))
        all_violations.sort(key=lambda x: x["timestamp"], reverse=True)
        return ok(count=len(all_violations), violations=all_violations)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/vehicle-stats/{vehicle_id}")
def vehicle_stats_ep(vehicle_id: str):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        stats = blockchain.get_vehicle_stats(vehicle_id)
        stats["certificate_eligible"] = blockchain.is_certificate_eligible(vehicle_id)
        return ok(stats=stats)
    except Exception as exc:
        return err(str(exc))


# ─────────────── Certificates ────────────────────────────────────────────

@app.get("/api/certificate/{vehicle_id}")
def certificate(vehicle_id: str):
    try:
        if blockchain_connected and blockchain:
            return ok(certificate=blockchain.check_certificate(vehicle_id))
        return ok(certificate={"valid": False, "token_id": 0})
    except Exception as exc:
        return err(str(exc))


@app.post("/api/certificate/issue")
def issue_certificate(body: IssueCertificateRequest, auth_user: str = Depends(require_auth)):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        kwargs: dict = {"vehicle_id": body.vehicle_id, "vehicle_owner": body.vehicle_owner}
        if body.metadata_uri:
            kwargs["metadata_uri"] = body.metadata_uri
        if body.is_first_puc is not None:
            kwargs["is_first_puc"] = body.is_first_puc
        result = blockchain.issue_certificate(**kwargs)
        _add_notification(
            "cert_issued",
            f"PUC certificate issued for {body.vehicle_id} by {auth_user}",
            vehicle_id=body.vehicle_id, severity="info",
        )
        return ok(result=result)
    except Exception as exc:
        return err(str(exc))


@app.post("/api/certificate/revoke")
def revoke_certificate(body: RevokeCertificateRequest, _auth=Depends(require_auth)):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        result = blockchain.revoke_certificate(body.token_id, body.reason)
        _add_notification(
            "cert_revoked",
            f"PUC certificate #{body.token_id} revoked: {body.reason}",
            severity="high",
        )
        return ok(result=result)
    except Exception as exc:
        return err(str(exc))


# ─────────────── Public verification ─────────────────────────────────────

@app.get("/api/verify/{vehicle_id}")
def verify(vehicle_id: str):
    if not (blockchain_connected and blockchain):
        return err("Blockchain not connected", 503)
    try:
        verification = blockchain.get_verification_data(vehicle_id)
        stats = blockchain.get_vehicle_stats(vehicle_id)
        return ok(
            verification=verification,
            stats={
                "total_records": stats["total_records"],
                "violations": stats["violations"],
                "avg_ces": stats["avg_ces"],
            },
        )
    except Exception as exc:
        return err(str(exc))


# ─────────────── Green token marketplace ─────────────────────────────────

@app.get("/api/green-tokens/{address}")
def green_tokens(address: str):
    if not (blockchain_connected and blockchain):
        return err("Blockchain not connected", 503)
    try:
        return ok(tokens=blockchain.get_green_token_balance(address))
    except Exception as exc:
        return err(str(exc))


@app.post("/api/tokens/redeem")
def redeem_tokens(body: RedeemTokensRequest, _auth=Depends(require_auth)):
    """Burn Green Tokens server-side from the station's account to
    redeem a reward. NOTE: this endpoint burns the STATION's tokens,
    not the caller's — which is only the right behaviour for admin
    / test utilities. Real end-user redemptions should call
    ``greenToken.connect(signer).redeem(rewardType)`` directly from
    the client via MetaMask (see frontend/marketplace.html)."""
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        result = blockchain.redeem_tokens(body.reward_type)
        _add_notification(
            "token_redemption",
            f"Token redemption type={body.reward_type} by station",
            severity="info",
        )
        return ok(result=result)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/tokens/rewards")
def token_rewards():
    """Return the four reward types defined in contracts/GreenToken.sol
    and their on-chain costs (in whole GCT, i.e. wei / 1e18)."""
    # Contract enum: see contracts/GreenToken.sol:40-44
    reward_types = [
        {"id": 0, "name": "toll_discount",    "display_name": "Toll Discount"},
        {"id": 1, "name": "parking_waiver",   "display_name": "Parking Waiver"},
        {"id": 2, "name": "tax_credit",       "display_name": "Tax Credit"},
        {"id": 3, "name": "priority_service", "display_name": "Priority Service"},
    ]
    rewards = []
    for rt in reward_types:
        cost_wei = 0
        if blockchain_connected and blockchain:
            try:
                cost_wei = blockchain.get_reward_cost(rt["id"])
            except Exception:
                cost_wei = 0
        cost_tokens = cost_wei // (10 ** 18) if cost_wei else 0
        rewards.append({
            "id":           rt["id"],
            "reward_type":  rt["name"],
            "display_name": rt["display_name"],
            "cost_tokens":  int(cost_tokens),
            "cost_wei":     int(cost_wei),
            "available":    cost_wei > 0,
        })
    return ok(rewards=rewards)


@app.get("/api/tokens/history/{address}")
def token_history(address: str):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        return ok(address=address, stats=blockchain.get_redemption_stats(address))
    except Exception as exc:
        return err(str(exc))


# ─────────────── Analytics ───────────────────────────────────────────────

@app.get("/api/analytics/trends/{vehicle_id}")
def analytics_trends(vehicle_id: str):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        records = blockchain.get_history(vehicle_id)
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
        trends.sort(key=lambda x: x["timestamp"])
        return ok(vehicle_id=vehicle_id, count=len(trends), trends=trends)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/analytics/fleet")
def analytics_fleet():
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        vehicles = blockchain.get_registered_vehicles()
        total_records = 0
        total_violations = 0
        ces_sum = 0.0
        ces_count = 0
        vehicle_stats_list: list = []
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
                    "vehicle_id": vid, "total_records": tr,
                    "violations": viol, "avg_ces": avg_ces,
                })
            except Exception:
                pass
        fleet_avg_ces = ces_sum / ces_count if ces_count > 0 else 0.0
        compliant = sum(1 for vs in vehicle_stats_list if vs["avg_ces"] < 1.0 and vs["total_records"] > 0)
        vehicles_with_records = sum(1 for vs in vehicle_stats_list if vs["total_records"] > 0)
        compliance_rate = (compliant / vehicles_with_records * 100) if vehicles_with_records > 0 else 0.0
        worst = sorted(vehicle_stats_list, key=lambda x: x["avg_ces"], reverse=True)[:10]
        return ok(fleet={
            "total_vehicles": len(vehicles),
            "total_records": total_records,
            "total_violations": total_violations,
            "avg_ces": round(fleet_avg_ces, 4),
            "compliance_rate": round(compliance_rate, 2),
            "worst_performers": worst,
        })
    except Exception as exc:
        return err(str(exc))


@app.get("/api/analytics/distribution")
def analytics_distribution():
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        vehicles = blockchain.get_registered_vehicles()
        buckets = {
            "0.00-0.25": 0, "0.25-0.50": 0, "0.50-0.75": 0,
            "0.75-1.00": 0, "1.00+": 0,
        }
        total_samples = 0
        for vid in vehicles:
            try:
                stats = blockchain.get_vehicle_stats(vid)
                if stats.get("total_records", 0) == 0:
                    continue
                avg_ces = stats.get("avg_ces", 0.0)
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
            {
                "bucket": k, "count": v,
                "percentage": round(v / total_samples * 100, 1) if total_samples > 0 else 0.0,
            }
            for k, v in buckets.items()
        ]
        return ok(total_vehicles=total_samples, distribution=histogram)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/analytics/phase-breakdown/{vehicle_id}")
def analytics_phase_breakdown(vehicle_id: str):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        records = blockchain.get_history(vehicle_id)
        phase_names = {0: "Low", 1: "Medium", 2: "High", 3: "Extra High"}
        phase_data = {
            i: {
                "name": phase_names[i], "count": 0,
                "co2_sum": 0.0, "co_sum": 0.0, "nox_sum": 0.0,
                "hc_sum": 0.0, "pm25_sum": 0.0, "ces_sum": 0.0, "violations": 0,
            } for i in range(4)
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
        breakdown = []
        for i in range(4):
            pd = phase_data[i]
            c = pd["count"]
            breakdown.append({
                "phase": i, "phase_name": pd["name"], "record_count": c,
                "avg_co2": round(pd["co2_sum"] / c, 2) if c > 0 else 0.0,
                "avg_co": round(pd["co_sum"] / c, 2) if c > 0 else 0.0,
                "avg_nox": round(pd["nox_sum"] / c, 2) if c > 0 else 0.0,
                "avg_hc": round(pd["hc_sum"] / c, 2) if c > 0 else 0.0,
                "avg_pm25": round(pd["pm25_sum"] / c, 2) if c > 0 else 0.0,
                "avg_ces": round(pd["ces_sum"] / c, 4) if c > 0 else 0.0,
                "violations": pd["violations"],
            })
        return ok(vehicle_id=vehicle_id, total_records=len(records), phase_breakdown=breakdown)
    except Exception as exc:
        return err(str(exc))


# ─────────────── Fleet management ────────────────────────────────────────

@app.get("/api/fleet/vehicles")
def fleet_vehicles(_auth=Depends(require_auth)):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        vehicles = blockchain.get_registered_vehicles()
        out = []
        for vid in vehicles:
            try:
                stats = blockchain.get_vehicle_stats(vid)
                cert = blockchain.check_certificate(vid)
                out.append({
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
                out.append({
                    "vehicle_id": vid, "total_records": 0, "violations": 0,
                    "fraud_alerts": 0, "avg_ces": 0.0, "status": "UNKNOWN",
                    "certificate_valid": False, "certificate_expiry": 0,
                })
        return ok(count=len(out), vehicles=out)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/fleet/alerts")
def fleet_alerts(_auth=Depends(require_auth)):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
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
                    "vehicle_id": vid, "violations": viol,
                    "fraud_alerts": fraud, "avg_ces": avg_ces,
                    "severity": severity, "severity_score": severity_score,
                })
            except Exception:
                pass
        alerts.sort(key=lambda x: x["severity_score"], reverse=True)
        return ok(count=len(alerts), alerts=alerts)
    except Exception as exc:
        return err(str(exc))


# ─────────────── RTO integration ─────────────────────────────────────────

@app.get("/api/rto/check/{vehicle_id}")
def rto_check(vehicle_id: str, _auth=Depends(require_auth)):
    try:
        result: dict = {
            "vehicle_id": vehicle_id,
            "vahan_status": None,
            "blockchain_status": None,
            "certificate_status": None,
            "renewal_eligible": False,
            "issues": [],
        }
        if vaahan_available and vaahan:
            try:
                vahan_result = vaahan.validate_for_emission_test(vehicle_id)
                result["vahan_status"] = {
                    "eligible": vahan_result.get("eligible", False),
                    "reason": vahan_result.get("reason", ""),
                    "vehicle_info": vahan_result.get("vehicle_info"),
                }
                if not vahan_result.get("eligible"):
                    result["issues"].append(f"VAHAN: {vahan_result.get('reason', 'Not eligible')}")
            except Exception as exc:
                result["issues"].append(f"VAHAN check failed: {exc}")
        else:
            result["issues"].append("VAHAN bridge not available")

        if blockchain_connected and blockchain:
            try:
                stats = blockchain.get_vehicle_stats(vehicle_id)
                result["blockchain_status"] = stats
                if stats.get("total_records", 0) == 0:
                    result["issues"].append("No emission records on blockchain")
                if stats.get("violations", 0) > 0:
                    result["issues"].append(f"{stats['violations']} emission violation(s) on record")
                if stats.get("fraud_alerts", 0) > 0:
                    result["issues"].append(f"{stats['fraud_alerts']} fraud alert(s) on record")
            except Exception as exc:
                result["issues"].append(f"Blockchain check failed: {exc}")
            try:
                cert = blockchain.check_certificate(vehicle_id)
                result["certificate_status"] = cert
                if not cert.get("valid"):
                    result["issues"].append("No valid PUC certificate")
                else:
                    expiry = cert.get("expiry_timestamp", 0)
                    if expiry and expiry < int(time.time()):
                        result["issues"].append("PUC certificate has expired")
            except Exception as exc:
                result["issues"].append(f"Certificate check failed: {exc}")
        else:
            result["issues"].append("Blockchain not connected")
        result["renewal_eligible"] = len(result["issues"]) == 0
        return ok(rto_check=result)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/rto/flagged")
def rto_flagged(_auth=Depends(require_auth)):
    if not blockchain_connected:
        return err("Blockchain not connected", 503)
    try:
        vehicles = blockchain.get_registered_vehicles()
        flagged = []
        now = int(time.time())
        for vid in vehicles:
            try:
                reasons = []
                stats = blockchain.get_vehicle_stats(vid)
                cert = blockchain.check_certificate(vid)
                if not cert.get("valid"):
                    reasons.append("no_valid_certificate")
                elif cert.get("expiry_timestamp", 0) < now:
                    reasons.append("certificate_expired")
                if stats.get("violations", 0) >= 3:
                    reasons.append("multiple_violations")
                if stats.get("fraud_alerts", 0) > 0:
                    reasons.append("fraud_detected")
                if stats.get("avg_ces", 0) > 1.5 and stats.get("total_records", 0) > 0:
                    reasons.append("high_emissions")
                if reasons:
                    flagged.append({
                        "vehicle_id": vid, "reasons": reasons,
                        "violations": stats.get("violations", 0),
                        "fraud_alerts": stats.get("fraud_alerts", 0),
                        "avg_ces": stats.get("avg_ces", 0.0),
                        "certificate_valid": cert.get("valid", False),
                        "certificate_expiry": cert.get("expiry_timestamp", 0),
                    })
            except Exception:
                pass
        flagged.sort(key=lambda x: len(x["reasons"]), reverse=True)
        return ok(count=len(flagged), flagged_vehicles=flagged)
    except Exception as exc:
        return err(str(exc))


# In-memory RTO enforcement action log. Durable persistence can be
# wired in via `store.add_notification` if an operator enables the
# persistence layer — for the research prototype an in-memory ring
# buffer plus the notification side-effect is sufficient.
_rto_actions: list = []
_rto_actions_lock = threading.Lock()


@app.post("/api/rto/enforce")
def rto_enforce(body: RTOEnforceRequest, _auth=Depends(require_auth)):
    """Log an RTO enforcement action against a vehicle.

    Three action types are supported by the authority/RTO dashboard:
      * ``warning``    — Issue warning notice
      * ``retest``     — Schedule re-test
      * ``inspection`` — Flag for inspection

    The action is appended to an in-memory log (bounded to 500 entries)
    and also surfaced as a high-severity notification so it shows up on
    the authority dashboard.
    """
    action = body.action.strip().lower()
    if action not in ("warning", "retest", "inspection"):
        return err(f"Invalid action '{body.action}'. Expected warning|retest|inspection.", 422)
    entry = {
        "timestamp": int(time.time()),
        "vehicle_id": body.vehicle_id,
        "action": action,
        "remarks": body.remarks,
    }
    with _rto_actions_lock:
        _rto_actions.append(entry)
        # Bounded ring: keep the most recent 500 actions
        if len(_rto_actions) > 500:
            del _rto_actions[: len(_rto_actions) - 500]
    _add_notification(
        "rto_enforcement",
        f"RTO action '{action}' recorded for {body.vehicle_id}"
        + (f": {body.remarks}" if body.remarks else ""),
        vehicle_id=body.vehicle_id,
        severity="high",
    )
    return ok(action=entry)


@app.get("/api/rto/actions")
def rto_actions_list(
    vehicle_id: str = Query("", alias="vehicle_id"),
    limit: int = Query(50, ge=1, le=500),
    _auth=Depends(require_auth),
):
    """Return the RTO enforcement action log, newest first."""
    with _rto_actions_lock:
        items = list(_rto_actions)
    if vehicle_id:
        items = [a for a in items if a.get("vehicle_id") == vehicle_id]
    items.sort(key=lambda a: a.get("timestamp") or 0, reverse=True)
    return ok(count=len(items[:limit]), actions=items[:limit])


# ─────────────── Notifications ───────────────────────────────────────────

@app.get("/api/notifications")
def get_notifications(
    type: str = Query("", alias="type"),
    limit: int = Query(50, ge=1, le=100),
    since: int = Query(0, ge=0),
    _auth=Depends(require_auth),
):
    try:
        _check_cert_expiry_notifications()
        # Prefer the durable persistence store when enabled
        if store.enabled:
            result = store.recent_notifications(limit=500)
        else:
            with _notifications_lock:
                result = list(_notifications)
        if type:
            result = [n for n in result if n.get("type") == type]
        if since > 0:
            ts_key = "created_at" if (result and "created_at" in result[0]) else "timestamp"
            result = [n for n in result if (n.get(ts_key) or 0) > since]
        ts_key = "created_at" if (result and "created_at" in result[0]) else "timestamp"
        result.sort(key=lambda x: (x.get(ts_key) or 0), reverse=True)
        result = result[:limit]
        return ok(count=len(result), notifications=result)
    except Exception as exc:
        return err(str(exc))


# ─────────────── OBD-II hardware ─────────────────────────────────────────

@app.get("/api/obd/status")
def obd_status():
    try:
        hw_connected = False
        port_name = None
        protocol = None
        dtcs: list = []
        if obd_hardware_available and _obd_connection and _obd_connection.is_connected():
            hw_connected = True
            port_name = _obd_connection.port_name()
            protocol = str(_obd_connection.protocol_name()) if hasattr(_obd_connection, "protocol_name") else None
            try:
                dtc_response = _obd_connection.query(obd_lib.commands.GET_DTC)
                if dtc_response and not dtc_response.is_null():
                    dtcs = [{"code": c, "description": d} for c, d in dtc_response.value]
            except Exception:
                pass
        return ok(obd={
            "hardware_available": obd_hardware_available,
            "connected": hw_connected,
            "port": port_name,
            "protocol": protocol,
            "adapter_module": obd_adapter_available,
            "dtc_count": len(dtcs),
            "dtcs": dtcs,
        })
    except Exception as exc:
        return err(str(exc))


@app.post("/api/obd/read", dependencies=[Depends(require_api_key_or_jwt)])
def obd_read():
    try:
        if not obd_hardware_available:
            return err("python-obd library not installed", 503)
        if not _obd_connection or not _obd_connection.is_connected():
            return err("No OBD-II device connected", 503)
        frame: dict = {}
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
                    frame[key] = (response.value.magnitude
                                  if hasattr(response.value, "magnitude")
                                  else float(response.value))
                else:
                    frame[key] = None
            except Exception:
                frame[key] = None
        if frame.get("maf") is not None and frame.get("maf", 0) > 0:
            frame["fuel_rate_estimated"] = round(frame["maf"] / 14.7 / 750 * 3600, 3)
        frame["timestamp"] = int(time.time())
        frame["source"] = "hardware"
        return ok(data=frame)
    except Exception as exc:
        return err(str(exc))


# ─────────────── Vehicle verification ────────────────────────────────────

@app.get("/api/vehicle/verify/{registration}")
def verify_vehicle(registration: str):
    try:
        if vaahan_available and vaahan:
            return ok(result=vaahan.validate_for_emission_test(registration))
        return err("VAHAN bridge not available", 503)
    except Exception as exc:
        return err(str(exc))


# ─────────────── Status ──────────────────────────────────────────────────

# ─────────────── Chain event projection (phase_listener) ───────────────────
# Read-only dashboard endpoints for the PhaseCompleted + BatchRootCommitted
# events emitted by EmissionRegistry v3.2. Closes audit Fix #10.

@app.post("/api/chain-events/sync", dependencies=[Depends(require_auth)])
def chain_events_sync():
    """Pull new chain events into the SQLite projection. Auth-gated so
    only an authorised operator can trigger a chain scan."""
    if not phase_listener:
        return err("Chain event listener not available", 503)
    try:
        inserted = phase_listener.sync_from_chain()
        return ok(inserted=inserted, stats=phase_listener.stats())
    except Exception as exc:  # noqa: BLE001
        return err(str(exc))


@app.get("/api/chain-events/status")
def chain_events_status():
    """Return per-event counts and last-synced block for the dashboard."""
    if not phase_listener:
        return ok(available=False)
    try:
        return ok(available=True, **phase_listener.stats())
    except Exception as exc:  # noqa: BLE001
        return err(str(exc))


@app.get("/api/chain-events/phase/{vehicle_id}")
def chain_events_phase(vehicle_id: str, limit: int = 100):
    """Return recent PhaseCompleted events for a vehicle (newest first)."""
    if not phase_listener:
        return ok(events=[])
    try:
        events = phase_listener.get_phase_events(vehicle_id=vehicle_id, limit=limit)
        return ok(count=len(events), events=events)
    except Exception as exc:  # noqa: BLE001
        return err(str(exc))


@app.get("/api/chain-events/batch-roots/{vehicle_id}")
def chain_events_batch_roots(vehicle_id: str, limit: int = 100):
    """Return recent BatchRootCommitted events for a vehicle (newest first)."""
    if not phase_listener:
        return ok(roots=[])
    try:
        roots = phase_listener.get_batch_roots(vehicle_id=vehicle_id, limit=limit)
        return ok(count=len(roots), roots=roots)
    except Exception as exc:  # noqa: BLE001
        return err(str(exc))


# ─────────────── Pre-PUC failure forecast (ml/pre_puc_predictor) ───────────
# Audit 13B #3 + F1 — transform the binary classifier into a diagnostic
# tool by also returning SHAP contributions. Public read-only endpoint;
# no auth required because it operates on chain-public history.

@app.get("/api/predict-puc/{vehicle_id}")
def predict_puc(vehicle_id: str, explain: bool = False):
    """Forecast whether the given vehicle will fail its next PUC test.

    Args:
        vehicle_id: Vehicle registration number to query.
        explain: When ``true``, also return SHAP-style per-feature
            contributions so the frontend can show the owner *which*
            part of their vehicle is dragging the score down.
    """
    if not _pre_puc_available or _pre_puc_predictor is None:
        return err("Pre-PUC predictor not available", 503)
    if not (blockchain_connected and blockchain is not None):
        return err("Blockchain not connected", 503)
    try:
        history = blockchain.get_vehicle_history(vehicle_id) or []
        # Normalise record shape for the predictor
        records = []
        for rec in history[-20:]:  # last 20 readings
            records.append(
                {
                    "ces_score": float(rec.get("cesScore", 0)) / 10000.0
                    if rec.get("cesScore") is not None
                    else float(rec.get("ces_score", 0.0)),
                    "co2":  float(rec.get("co2Level", 0)) / 1000.0
                    if rec.get("co2Level") is not None
                    else float(rec.get("co2", 0.0)),
                    "co":   float(rec.get("coLevel", 0)) / 1000.0
                    if rec.get("coLevel") is not None
                    else float(rec.get("co", 0.0)),
                    "nox":  float(rec.get("noxLevel", 0)) / 1000.0
                    if rec.get("noxLevel") is not None
                    else float(rec.get("nox", 0.0)),
                    "hc":   float(rec.get("hcLevel", 0)) / 1000.0
                    if rec.get("hcLevel") is not None
                    else float(rec.get("hc", 0.0)),
                    "pm25": float(rec.get("pm25Level", 0)) / 1000.0
                    if rec.get("pm25Level") is not None
                    else float(rec.get("pm25", 0.0)),
                }
            )
        prediction = _pre_puc_predictor.predict(records)
        payload: Dict[str, Any] = {"vehicle_id": vehicle_id, "prediction": prediction}
        if explain:
            payload["explanation"] = _pre_puc_predictor.explain(records, top_k=5)
        return ok(**payload)
    except Exception as exc:  # noqa: BLE001
        return err(str(exc))


# ─────────────── Station-level fraud analysis (ml/station_fraud_detector) ──
# Exposes a dashboard endpoint for the audit report's 13B #14 suggestion.
# Read-only aggregate analytics — a corrupted testing centre that is
# rubber-stamping fails as passes, or manufacturing high-volume fakes,
# should show up here even when each individual reading passes the
# per-reading fraud detector. Auth-gated because it operates on a
# station's entire activity profile.

@app.get("/api/fraud/station-analysis", dependencies=[Depends(require_auth)])
def fraud_station_analysis(limit: int = 500):
    """Run station-level anomaly detection over the most recent chain
    records and return per-station risk reports (highest-risk first)."""
    if not _station_fraud_available or StationFraudDetector is None:
        return err("Station fraud detector not available", 503)
    if not (blockchain_connected and blockchain is not None):
        return err("Blockchain not connected", 503)
    try:
        records = blockchain.get_all_records(limit=limit) or []
        # The on-chain struct field that carries the station address is
        # ``stationAddress``; normalise to ``station_id`` for the detector.
        for rec in records:
            if "stationAddress" in rec and "station_id" not in rec:
                rec["station_id"] = rec["stationAddress"]
            if "cesScore" in rec and "ces_score" not in rec:
                try:
                    rec["ces_score"] = float(rec["cesScore"]) / 10000.0
                except (TypeError, ValueError):
                    pass
        detector = StationFraudDetector()
        reports = detector.analyse(records)
        return ok(
            count=len(reports),
            stations=[r.as_dict() for r in reports],
        )
    except Exception as exc:  # noqa: BLE001
        return err(str(exc))


# ─── v4.1: Tiered Compliance & Health Reporting ────────────────────

@app.get("/api/vehicle/{vehicle_id}/tier")
def get_vehicle_tier(vehicle_id: str):
    """Get the compliance tier of a vehicle (Gold/Silver/Bronze/Unclassified)."""
    try:
        tier_info: dict = {"vehicle_id": vehicle_id, "tier": 0, "tier_name": "Unclassified",
                           "validity_days": 180, "next_puc_due": None}

        # If blockchain connected: query on-chain tier
        if blockchain_connected and blockchain:
            try:
                chain_tier = blockchain.get_vehicle_tier(vehicle_id)
                tier_info["tier"] = chain_tier.get("tier", 0)
                tier_info["tier_name"] = chain_tier.get("tier_name", "Unclassified")
            except Exception:
                pass

        # Fallback / supplement: compute from local telemetry if no chain tier
        if tier_info["tier"] == 0 and _micro_engine is not None:
            try:
                report = _micro_engine.generate_weekly_report(vehicle_id)
                tier_info["tier_name"] = report.get("tier", "Unclassified")
                _name_to_int = {"Unclassified": 0, "Gold": 1, "Silver": 2, "Bronze": 3}
                tier_info["tier"] = _name_to_int.get(tier_info["tier_name"], 0)
            except Exception:
                pass

        from ml.micro_assessment import TIER_VALIDITY_DAYS as _tvd
        tier_info["validity_days"] = _tvd.get(tier_info["tier_name"], 180)

        import datetime as _dt
        next_due = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=tier_info["validity_days"])
        tier_info["next_puc_due"] = next_due.strftime("%Y-%m-%d")

        return ok(**tier_info)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/vehicle/{vehicle_id}/health-report")
def get_health_report(vehicle_id: str):
    """Get the latest weekly health report for a vehicle."""
    try:
        if _micro_engine is None:
            return err("Micro-assessment engine not available", 503)
        report = _micro_engine.generate_weekly_report(vehicle_id)
        return ok(report=report)
    except Exception as exc:
        return err(str(exc))


@app.get("/api/vehicle/{vehicle_id}/degradation")
def get_degradation_status(vehicle_id: str):
    """Get degradation analysis for a vehicle."""
    try:
        result: dict = {
            "vehicle_id": vehicle_id,
            "current_tier": "Unclassified",
            "degradation_risk": "low",
            "projected_failure_days": None,
            "dtc_codes": [],
            "events": [],
        }

        # Get health report for tier and degradation risk
        if _micro_engine is not None:
            try:
                report = _micro_engine.generate_weekly_report(vehicle_id)
                result["current_tier"] = report.get("tier", "Unclassified")
                result["degradation_risk"] = report.get("degradation_risk", "low")
                result["projected_failure_days"] = report.get("projected_failure_days")
            except Exception:
                pass

        # Get degradation events from persistence
        try:
            events = store.get_degradation_events(vehicle_id, limit=50)
            result["events"] = events
        except Exception:
            pass

        return ok(**result)
    except Exception as exc:
        return err(str(exc))


@app.post("/api/vehicle/{vehicle_id}/paired-reading")
def submit_paired_reading(vehicle_id: str, body: dict):
    """Submit a paired OBD+tailpipe reading for calibration training.

    Body: {"obd": {speed, rpm, ...}, "tailpipe": {co2, co, nox, hc, pm25}}
    """
    try:
        obd_data = body.get("obd", {})
        tailpipe_data = body.get("tailpipe", {})

        paired_record = {
            "vehicle_id": vehicle_id,
            "type": "paired_reading",
            "obd": obd_data,
            "tailpipe": tailpipe_data,
            "timestamp": int(time.time()),
        }

        row_id = store.record_telemetry(
            vehicle_id=vehicle_id,
            reading=paired_record,
            is_violation=False,
        )

        # Count total paired readings for this vehicle
        all_telemetry = store.telemetry_for_vehicle(vehicle_id, limit=10000)
        paired_count = sum(
            1 for t in all_telemetry
            if isinstance(t.get("reading"), dict) and t["reading"].get("type") == "paired_reading"
        )

        return ok(
            vehicle_id=vehicle_id,
            calibration_data_points=paired_count,
            record_id=row_id,
        )
    except Exception as exc:
        return err(str(exc))


@app.get("/api/status")
def status_ep():
    try:
        bc_status = blockchain.get_status() if (blockchain_connected and blockchain) else {}
        return ok(
            connected=bc_status.get("connected", False),
            blockNumber=bc_status.get("block_number"),
            registryAddress=bc_status.get("registry_address"),
            pucCertAddress=bc_status.get("puc_cert_address"),
            greenTokenAddress=bc_status.get("green_token_address"),
            account=bc_status.get("account"),
            networkId=bc_status.get("network_id"),
            modules={
                "vsp": vsp_available,
                "fraud_detector": fraud_available,
                "lstm_predictor": predictor_available,
                "vaahan_bridge": vaahan_available,
                "obd_adapter": obd_adapter_available,
                "obd_hardware": obd_hardware_available,
                "obd_connected": bool(_obd_connection and _obd_connection.is_connected())
                                  if obd_hardware_available else False,
                "jwt_auth": auth_is_configured(),
                "persistence": store.enabled,
                "chain_events": bool(phase_listener),
                "pre_puc_predictor": bool(_pre_puc_predictor),
                "station_fraud_detector": bool(_station_fraud_available),
                "calibration_model": bool(_calibration_model and getattr(_calibration_model, "is_trained", False)),
                "micro_assessment": bool(_micro_engine),
            },
            architecture="3-node",
            node="testing-station",
        )
    except Exception as exc:
        return err(str(exc))


# ────────────────────────── Startup banner ───────────────────────────────
# (moved to _lifespan() above — keeping this comment as a breadcrumb)
