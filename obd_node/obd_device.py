"""
Smart PUC — OBD Device Simulator Node (Node 1 of 3)
====================================================
Simulates an OBD-II device that:
  1. Collects vehicle telemetry (speed, RPM, fuel rate, etc.)
  2. Cryptographically signs the data with its device private key
  3. Sends signed data to the Testing Station backend

In production, this would run on a Raspberry Pi with a real ELM327
Bluetooth adapter. For the software demo, it uses the WLTC simulator
and signs data with a Ganache account key.

3-Node Trust Model:
  Node 1 (this): OBD Device — signs data, proves data provenance
  Node 2: Testing Station — validates, runs fraud detection, submits to chain
  Node 3: Verification Portal — read-only, verifies certificates

Usage:
  python -m obd_node.obd_device                      # continuous mode
  python -m obd_node.obd_device --single              # single reading
  python -m obd_node.obd_device --count 10            # N readings
  python -m obd_node.obd_device --vehicle MH14CD5678  # specific vehicle
  python -m obd_node.obd_device --real                 # real OBD-II adapter
"""

import argparse
import json
import os
import sys
import time
import warnings
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.simulator import WLTCSimulator
from backend.emission_engine import calculate_emissions
from integrations.obd_adapter import parse_obd_frame, OBDReading

try:
    from physics.vsp_model import calculate_vsp, get_operating_mode_bin
    _vsp_available = True
except ImportError:
    _vsp_available = False

# Optional real OBD-II support via python-obd
try:
    import obd as _obd_lib
    _real_obd_available = True
except ImportError:
    _obd_lib = None
    _real_obd_available = False

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


# ─────────────────────── OBD Device Configuration ──────────────────────────

# Default: Ganache account[2] is the OBD device
DEVICE_PRIVATE_KEY = os.getenv(
    "OBD_DEVICE_PRIVATE_KEY",
    os.getenv("DEVICE_PRIVATE_KEY", "")
)
STATION_URL = os.getenv("STATION_URL", "http://127.0.0.1:5000")
DEFAULT_VEHICLE_ID = os.getenv("DEFAULT_VEHICLE_ID", "MH12AB1234")

# Scaling factors matching Solidity contracts
SCALE_POLLUTANT = 1000
SCALE_SCORE = 10000


class RealOBDReader:
    """
    Reads live telemetry from a real ELM327 OBD-II adapter using python-obd.

    Maps standard OBD-II PIDs to the telemetry fields required by the
    emission engine:
      - PID 0x0D -> speed (km/h)
      - PID 0x0C -> RPM
      - PID 0x10 -> MAF air flow (g/s), used for fuel rate estimation
      - PID 0x05 -> coolant temperature (C)
    """

    def __init__(self, vehicle_id: str):
        self.vehicle_id = vehicle_id
        self._prev_speed = 0.0
        self._prev_time = time.time()
        self.connection = _obd_lib.OBD()  # auto-connects to first available adapter
        if not self.connection.is_connected():
            raise ConnectionError(
                "Could not connect to ELM327 adapter. "
                "Check Bluetooth pairing and adapter connection."
            )
        print(f"  Connected to ELM327: {self.connection.port_name()}")

    def read(self) -> dict:
        """
        Query live PIDs and return a telemetry dict compatible with
        generate_reading() output format.
        """
        now = time.time()
        dt = now - self._prev_time if now > self._prev_time else 1.0

        # Query PIDs
        speed_resp = self.connection.query(_obd_lib.commands.SPEED)          # PID 0x0D
        rpm_resp = self.connection.query(_obd_lib.commands.RPM)              # PID 0x0C
        maf_resp = self.connection.query(_obd_lib.commands.MAF)              # PID 0x10
        coolant_resp = self.connection.query(_obd_lib.commands.COOLANT_TEMP) # PID 0x05

        speed = speed_resp.value.magnitude if not speed_resp.is_null() else 0.0
        rpm = rpm_resp.value.magnitude if not rpm_resp.is_null() else 800.0
        maf = maf_resp.value.magnitude if not maf_resp.is_null() else 0.0
        coolant_temp = coolant_resp.value.magnitude if not coolant_resp.is_null() else 25.0

        # Estimate fuel rate from MAF (g/s) -> L/h
        # Stoichiometric ratio ~14.7:1, petrol density ~740 g/L
        fuel_rate = (maf / 14.7) * (3600.0 / 740.0) if maf > 0 else 0.0

        # Calculate acceleration from speed delta
        acceleration = (speed - self._prev_speed) / (dt * 3.6) if dt > 0 else 0.0
        self._prev_speed = speed
        self._prev_time = now

        return {
            "speed": speed,
            "rpm": rpm,
            "fuel_rate": fuel_rate,
            "acceleration": round(acceleration, 3),
            "coolant_temp": coolant_temp,
        }


class OBDDeviceSimulator:
    """
    Simulates an OBD-II device that generates, signs, and transmits
    emission telemetry data.

    In the 3-node architecture, this is Node 1. Its private key signature
    proves that the data originated from a registered device, not from
    someone manually entering values.

    Args:
        device_private_key: Hex private key for ECDSA signing
        vehicle_id: Vehicle registration number
        station_url: Testing Station backend URL
        use_real_obd: If True, attempt to use a real ELM327 adapter
    """

    def __init__(
        self,
        device_private_key: str,
        vehicle_id: str = DEFAULT_VEHICLE_ID,
        station_url: str = STATION_URL,
        use_real_obd: bool = False,
    ):
        self.vehicle_id = vehicle_id
        self.station_url = station_url
        self._real_obd_reader = None

        # Initialize device signing account
        if not device_private_key:
            raise ValueError(
                "OBD Device private key not set. "
                "Set OBD_DEVICE_PRIVATE_KEY in .env or pass it directly."
            )
        self.account = Account.from_key(device_private_key)
        self.device_address = self.account.address

        # Initialize data source: real OBD-II or WLTC simulator
        if use_real_obd:
            if not _real_obd_available:
                warnings.warn(
                    "python-obd library not installed. "
                    "Falling back to WLTC simulator. "
                    "Install with: pip install obd",
                    RuntimeWarning,
                    stacklevel=2,
                )
            else:
                try:
                    self._real_obd_reader = RealOBDReader(vehicle_id=vehicle_id)
                except ConnectionError as e:
                    warnings.warn(
                        f"Could not connect to OBD adapter: {e}. "
                        "Falling back to WLTC simulator.",
                        RuntimeWarning,
                        stacklevel=2,
                    )

        # Always keep simulator available as fallback
        self.simulator = WLTCSimulator(vehicle_id=vehicle_id, dt=1.0)
        self._prev_speed = 0.0
        self._engine_start_time = time.time()

        mode_label = "Real OBD-II" if self._real_obd_reader else "WLTC Simulator"
        print(f"OBD Device initialized:")
        print(f"  Device Address : {self.device_address}")
        print(f"  Vehicle ID     : {self.vehicle_id}")
        print(f"  Station URL    : {self.station_url}")
        print(f"  Data Source    : {mode_label}")

    def generate_reading(self) -> dict:
        """
        Generate a telemetry reading from the data source (real OBD-II or
        WLTC simulator), run it through the emission engine, and return the
        full data package ready for signing.

        Returns:
            dict with telemetry, emissions, and metadata
        """
        # Get telemetry from real adapter or simulator
        if self._real_obd_reader is not None:
            raw = self._real_obd_reader.read()
            speed = raw["speed"]
            rpm = raw["rpm"]
            fuel_rate = raw["fuel_rate"]
            acceleration = raw["acceleration"]
        else:
            reading = self.simulator.generate_reading()
            speed = reading["speed"]
            rpm = reading["rpm"]
            fuel_rate = reading["fuel_rate"]
            acceleration = reading.get("acceleration", 0.0)

        # Calculate VSP and operating mode
        speed_mps = speed / 3.6
        vsp_value = 0.0
        op_mode_bin = 11
        if _vsp_available:
            vsp_value = calculate_vsp(speed_mps, acceleration)
            op_mode_bin = get_operating_mode_bin(vsp_value, speed_mps)

        # Cold start check (first 180 seconds)
        cold_start = (time.time() - self._engine_start_time) < 180.0

        # Calculate multi-pollutant emissions
        emission = calculate_emissions(
            speed_kmh=speed,
            acceleration=acceleration,
            rpm=rpm,
            fuel_rate=fuel_rate,
            fuel_type="petrol",
            operating_mode_bin=op_mode_bin,
            ambient_temp=25.0,
            altitude=0.0,
            cold_start=cold_start,
        )

        # Get WLTC phase (only relevant in simulator mode)
        wltc_phase = 0
        if self._real_obd_reader is None and hasattr(self.simulator, '_current_time'):
            phase_obj = self.simulator.get_phase(self.simulator._current_time)
            phase_map = {"Low": 0, "Medium": 1, "High": 2, "Extra High": 3}
            phase_str = phase_obj.value if hasattr(phase_obj, 'value') else str(phase_obj)
            wltc_phase = phase_map.get(phase_str, 0)

        timestamp = int(time.time())

        return {
            "vehicle_id": self.vehicle_id,
            "speed": speed,
            "rpm": rpm,
            "fuel_rate": fuel_rate,
            "acceleration": round(acceleration, 3),
            "co2": emission.get("co2_g_per_km", 0),
            "co": emission.get("co_g_per_km", 0),
            "nox": emission.get("nox_g_per_km", 0),
            "hc": emission.get("hc_g_per_km", 0),
            "pm25": emission.get("pm25_g_per_km", 0),
            "ces_score": emission.get("ces_score", 0),
            "vsp": round(vsp_value, 3),
            "wltc_phase": wltc_phase,
            "timestamp": timestamp,
            "compliance": emission.get("compliance", {}),
            "status": emission.get("status", "UNKNOWN"),
        }

    def sign_reading(self, reading: dict) -> dict:
        """
        Cryptographically sign the emission data with the device's private key.

        The signature covers:
            vehicleId + co2 + co + nox + hc + pm25 + timestamp + nonce
        (matching the contract's _verifyDeviceSignature method)

        A unique bytes32 nonce is generated per reading to prevent replay
        attacks. The nonce is included in both the signature hash and the
        payload sent to the station.

        Args:
            reading: Telemetry + emission data from generate_reading()

        Returns:
            dict with original reading + scaled values + nonce + device signature
        """
        # Scale values to match Solidity contract (integers)
        co2_scaled = int(round(reading["co2"] * SCALE_POLLUTANT))
        co_scaled = int(round(reading["co"] * SCALE_POLLUTANT))
        nox_scaled = int(round(reading["nox"] * SCALE_POLLUTANT))
        hc_scaled = int(round(reading["hc"] * SCALE_POLLUTANT))
        pm25_scaled = int(round(reading["pm25"] * SCALE_POLLUTANT))
        ces_scaled = int(round(reading["ces_score"] * SCALE_SCORE))
        vsp_scaled = int(round(reading["vsp"] * SCALE_POLLUTANT))

        # Generate unique bytes32 nonce for replay protection
        nonce = Web3.keccak(
            text=f"{reading['vehicle_id']}{reading['timestamp']}{os.urandom(16).hex()}"
        )

        # Create message hash matching the Solidity contract
        # keccak256(abi.encodePacked(vehicleId, co2, co, nox, hc, pm25, timestamp, nonce))
        message_hash = Web3.solidity_keccak(
            ["string", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256", "bytes32"],
            [
                reading["vehicle_id"],
                co2_scaled,
                co_scaled,
                nox_scaled,
                hc_scaled,
                pm25_scaled,
                reading["timestamp"],
                nonce,
            ]
        )

        # Sign with device private key (eth_sign format with "\x19Ethereum Signed Message:\n32" prefix)
        signable = encode_defunct(message_hash)
        signed = self.account.sign_message(signable)

        return {
            **reading,
            "scaled": {
                "co2": co2_scaled,
                "co": co_scaled,
                "nox": nox_scaled,
                "hc": hc_scaled,
                "pm25": pm25_scaled,
                "ces_score": ces_scaled,
                "vsp": vsp_scaled,
            },
            "nonce": nonce.hex(),
            "device_address": self.device_address,
            "device_signature": signed.signature.hex(),
        }

    def send_to_station(self, signed_reading: dict) -> dict:
        """
        Send signed telemetry data to the Testing Station backend.

        Args:
            signed_reading: Output from sign_reading()

        Returns:
            dict with station response
        """
        import requests

        payload = {
            "vehicle_id": signed_reading["vehicle_id"],
            "speed": signed_reading["speed"],
            "rpm": signed_reading["rpm"],
            "fuel_rate": signed_reading["fuel_rate"],
            "acceleration": signed_reading["acceleration"],
            "co2": signed_reading["co2"],
            "co": signed_reading["co"],
            "nox": signed_reading["nox"],
            "hc": signed_reading["hc"],
            "pm25": signed_reading["pm25"],
            "ces_score": signed_reading["ces_score"],
            "vsp": signed_reading["vsp"],
            "wltc_phase": signed_reading["wltc_phase"],
            "timestamp": signed_reading["timestamp"],
            "nonce": signed_reading["nonce"],
            "device_address": signed_reading["device_address"],
            "device_signature": signed_reading["device_signature"],
        }

        try:
            resp = requests.post(
                f"{self.station_url}/api/record",
                json=payload,
                timeout=10,
            )
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def run_single(self) -> dict:
        """Generate, sign, and send a single reading."""
        reading = self.generate_reading()
        signed = self.sign_reading(reading)
        result = self.send_to_station(signed)
        return {**signed, "station_response": result}

    def run_continuous(self, interval: float = 3.0, count: Optional[int] = None):
        """
        Run the OBD device in continuous mode, sending readings at regular intervals.

        Args:
            interval: Seconds between readings (default 3.0)
            count: Number of readings to send (None = infinite)
        """
        print(f"\nStarting continuous mode (interval={interval}s, count={count or 'infinite'})...")
        print("Press Ctrl+C to stop.\n")

        i = 0
        try:
            while count is None or i < count:
                reading = self.generate_reading()
                signed = self.sign_reading(reading)
                result = self.send_to_station(signed)

                i += 1
                status_icon = "PASS" if reading["status"] == "PASS" else "FAIL"
                station_ok = result.get("success", False)

                print(
                    f"[{i:04d}] {status_icon} | "
                    f"CES={reading['ces_score']:.3f} | "
                    f"CO2={reading['co2']:.1f} | "
                    f"NOx={reading['nox']:.4f} | "
                    f"Speed={reading['speed']:.1f} | "
                    f"Phase={reading['wltc_phase']} | "
                    f"Station={'OK' if station_ok else 'ERR'}"
                )

                if count is None or i < count:
                    time.sleep(interval)

        except KeyboardInterrupt:
            print(f"\nStopped after {i} readings.")

        print(f"Total readings sent: {i}")


# ──────────────────────── Hardware Integration Path ────────────────────────
#
# For Raspberry Pi deployment:
#   1. Connect ELM327 Bluetooth dongle to vehicle OBD-II port
#   2. Pair ELM327 with Raspberry Pi via Bluetooth SPP
#   3. Use --real flag to enable live OBD-II reading:
#      python -m obd_node.obd_device --real
#   4. Store device private key in hardware security module (HSM)
#      or secure enclave on the Pi
#   5. Run this script as a systemd service
#
# The signing and transmission logic remains identical — only the
# data source changes from WLTCSimulator to real ELM327 via python-obd.
# ────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart PUC OBD Device Simulator (Node 1)")
    parser.add_argument("--vehicle", default=DEFAULT_VEHICLE_ID, help="Vehicle registration number")
    parser.add_argument("--station", default=STATION_URL, help="Testing Station URL")
    parser.add_argument("--key", default=DEVICE_PRIVATE_KEY, help="Device private key (hex)")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between readings")
    parser.add_argument("--count", type=int, default=None, help="Number of readings (default: infinite)")
    parser.add_argument("--single", action="store_true", help="Send a single reading and exit")
    parser.add_argument(
        "--real", action="store_true",
        help="Use real ELM327 OBD-II adapter via python-obd (falls back to simulator if unavailable)"
    )

    args = parser.parse_args()

    device = OBDDeviceSimulator(
        device_private_key=args.key,
        vehicle_id=args.vehicle,
        station_url=args.station,
        use_real_obd=args.real,
    )

    if args.single:
        result = device.run_single()
        print(json.dumps(result, indent=2, default=str))
    else:
        device.run_continuous(interval=args.interval, count=args.count)
