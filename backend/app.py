"""
Smart PUC — Flask REST API
============================
Central backend API that orchestrates:
    OBD-II Simulator → Emission Engine → Blockchain Connector

Endpoints (Section 9.1):
    GET   /api/simulate            — Trigger simulation, return latest telemetry + CO₂
    POST  /api/record              — Calculate emission & write to blockchain
    GET   /api/history/<vehicleId> — All on-chain records for a vehicle
    GET   /api/violations          — All FAIL records across all vehicles
    GET   /api/status              — Backend health & blockchain connection status

Requires:
    - Flask, flask-cors
    - Web3.py (via blockchain_connector)
    - Ganache running on port 7545
    - Truffle migration completed (contract deployed)
"""

import os
import time
import traceback

from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

from simulator import OBDSimulator
from emission_engine import calculate_co2, process_obd_reading
from blockchain_connector import BlockchainConnector

# ──────────────────────────── App Setup ────────────────────────────────────────

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend consumption (R-06 mitigation)

# Default vehicle ID (can be overridden per request)
DEFAULT_VEHICLE_ID = os.getenv("DEFAULT_VEHICLE_ID", "MH12AB1234")

# ──────────────────────────── Initialise Components ────────────────────────────

simulator = OBDSimulator(vehicle_id=DEFAULT_VEHICLE_ID, interval=5)

try:
    blockchain = BlockchainConnector()
    blockchain_connected = True
except Exception as e:
    print(f"⚠️  Blockchain connection failed: {e}")
    print("   API will run in offline mode (no on-chain writes).")
    blockchain = None
    blockchain_connected = False

# ──────────────────────────── API Endpoints ────────────────────────────────────

@app.route("/api/simulate", methods=["GET"])
def simulate():
    """
    GET /api/simulate
    Triggers OBD-II simulation and returns the latest telemetry enriched with CO₂.
    
    Query params:
        vehicle_id (optional): Override the default vehicle ID
    
    Response: { rpm, speed, fuel_rate, fuel_type, mode, co2_g_per_km, co2_int, status, timestamp }
    """
    try:
        vehicle_id = request.args.get("vehicle_id", DEFAULT_VEHICLE_ID)
        simulator.vehicle_id = vehicle_id

        reading = simulator.generate_reading()
        enriched = process_obd_reading(reading)

        return jsonify({
            "success": True,
            "data": enriched,
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/record", methods=["POST"])
def record():
    """
    POST /api/record
    Calculates emission from simulation data and writes to blockchain.

    Request body (JSON, all optional — defaults from simulator):
        {
            "vehicle_id": "MH12AB1234",
            "fuel_rate": 7.5,
            "speed": 45.0,
            "fuel_type": "petrol"
        }

    Response: { txHash, status, co2, compliance }
    """
    if not blockchain_connected:
        return jsonify({
            "success": False,
            "error": "Blockchain not connected. Start Ganache and restart the API.",
        }), 503

    try:
        data = request.get_json(silent=True) or {}

        # Use provided values or generate from simulator
        vehicle_id = data.get("vehicle_id", DEFAULT_VEHICLE_ID)

        if "fuel_rate" in data and "speed" in data:
            fuel_rate = float(data["fuel_rate"])
            speed = float(data["speed"])
            fuel_type = data.get("fuel_type", "petrol")
        else:
            reading = simulator.generate_reading()
            fuel_rate = reading["fuel_rate"]
            speed = reading["speed"]
            fuel_type = reading.get("fuel_type", "petrol")

        # Calculate CO₂
        emission = calculate_co2(fuel_rate, speed, fuel_type)
        co2_int = emission["co2_int"]
        timestamp = int(time.time())

        # Write to blockchain
        tx_result = blockchain.store_emission(vehicle_id, co2_int, timestamp)

        return jsonify({
            "success": True,
            "data": {
                "txHash": tx_result["tx_hash"],
                "status": tx_result["status"],
                "blockNumber": tx_result["block_number"],
                "co2": co2_int,
                "co2_g_per_km": emission["co2_g_per_km"],
                "compliance": emission["status"],
                "vehicle_id": vehicle_id,
                "fuel_rate": fuel_rate,
                "speed": speed,
                "fuel_type": fuel_type,
                "timestamp": timestamp,
            },
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/history/<vehicle_id>", methods=["GET"])
def history(vehicle_id):
    """
    GET /api/history/<vehicleId>
    Returns all on-chain emission records for the given vehicle.

    Response: [ { co2Level, timestamp, status } ]
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

    Response: [ { vehicleId, co2Level, timestamp } ]
    """
    if not blockchain_connected:
        return jsonify({"success": False, "error": "Blockchain not connected"}), 503

    try:
        all_violations = []
        vehicles = blockchain.get_registered_vehicles()

        for vid in vehicles:
            vehicle_violations = blockchain.get_violations(vid)
            all_violations.extend(vehicle_violations)

        # Sort by timestamp descending (most recent first)
        all_violations.sort(key=lambda x: x["timestamp"], reverse=True)

        return jsonify({
            "success": True,
            "count": len(all_violations),
            "violations": all_violations,
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/status", methods=["GET"])
def status():
    """
    GET /api/status
    Returns backend health and blockchain connection status.

    Response: { connected, blockNumber, contractAddress }
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
            }), 200
        else:
            return jsonify({
                "success": True,
                "connected": False,
                "blockNumber": None,
                "contractAddress": None,
                "account": None,
                "networkId": None,
            }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ──────────────────────────── Run Server ───────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"

    print("=" * 60)
    print("🚀 Smart PUC — Backend API Server")
    print(f"   Port           : {port}")
    print(f"   Blockchain     : {'✅ Connected' if blockchain_connected else '❌ Offline'}")
    if blockchain_connected:
        print(f"   Contract       : {blockchain.contract_address}")
        print(f"   Account        : {blockchain.address}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug)
