"""
OBD-II PID Adapter for SmartPUC.

Maps standard OBD-II Parameter IDs (PIDs) from ELM327-compatible
dongles to the SmartPUC telemetry format. Provides a clear data
contract for real-world vehicle integration.

Standard OBD-II PIDs used:
    PID 0x0C — Engine RPM (A*256+B)/4
    PID 0x0D — Vehicle Speed (A) km/h
    PID 0x0F — Intake Air Temperature (A-40) deg C
    PID 0x10 — MAF Air Flow Rate (A*256+B)/100 g/s
    PID 0x11 — Throttle Position (A*100/255) %
    PID 0x2F — Fuel Tank Level (A*100/255) %
    PID 0x5E — Engine Fuel Rate (A*256+B)/20 L/h

References:
    SAE J1979 — OBD-II PIDs standard
    ISO 15031-5 — Vehicle diagnostics
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional


# Standard OBD-II PID definitions
OBD_PIDS = {
    0x0C: {"name": "rpm", "unit": "rev/min", "formula": "((A*256)+B)/4", "bytes": 2},
    0x0D: {"name": "speed", "unit": "km/h", "formula": "A", "bytes": 1},
    0x0F: {"name": "intake_air_temp", "unit": "degC", "formula": "A-40", "bytes": 1},
    0x10: {"name": "maf_rate", "unit": "g/s", "formula": "((A*256)+B)/100", "bytes": 2},
    0x11: {"name": "throttle", "unit": "%", "formula": "(A*100)/255", "bytes": 1},
    0x5E: {"name": "fuel_rate", "unit": "L/h", "formula": "((A*256)+B)/20", "bytes": 2},
}


@dataclass
class OBDReading:
    """Parsed OBD-II reading in SmartPUC format."""
    speed: float = 0.0
    rpm: int = 0
    fuel_rate: float = 0.0  # L/100km
    intake_air_temp: float = 25.0
    throttle: float = 0.0
    maf_rate: float = 0.0
    timestamp: int = 0


def decode_pid(pid: int, data_bytes: list[int]) -> float:
    """Decode raw OBD-II PID response bytes to a numeric value.

    Args:
        pid: OBD-II PID number (e.g., 0x0C for RPM).
        data_bytes: Raw response bytes [A, B, ...].

    Returns:
        Decoded numeric value in the PID's native unit.

    Raises:
        ValueError: If PID is unknown or data_bytes is too short.
    """
    if pid not in OBD_PIDS:
        raise ValueError(f"Unknown OBD-II PID: 0x{pid:02X}")

    spec = OBD_PIDS[pid]
    if len(data_bytes) < spec["bytes"]:
        raise ValueError(f"PID 0x{pid:02X} requires {spec['bytes']} bytes, got {len(data_bytes)}")

    A = data_bytes[0]
    B = data_bytes[1] if len(data_bytes) > 1 else 0

    if pid == 0x0C:
        return ((A * 256) + B) / 4.0
    elif pid == 0x0D:
        return float(A)
    elif pid == 0x0F:
        return A - 40.0
    elif pid == 0x10:
        return ((A * 256) + B) / 100.0
    elif pid == 0x11:
        return (A * 100) / 255.0
    elif pid == 0x5E:
        return ((A * 256) + B) / 20.0
    else:
        raise ValueError(f"No decoder for PID 0x{pid:02X}")


def fuel_rate_lph_to_l100km(fuel_rate_lph: float, speed_kmh: float) -> float:
    """Convert fuel rate from L/h to L/100km.

    Args:
        fuel_rate_lph: Fuel rate in litres per hour.
        speed_kmh: Vehicle speed in km/h.

    Returns:
        Fuel rate in L/100km. Returns 0.0 if speed is near zero.
    """
    if speed_kmh < 1.0:
        return 0.0
    return (fuel_rate_lph / speed_kmh) * 100.0


def maf_to_fuel_rate(maf_gs: float, afr: float = 14.7) -> float:
    """Estimate fuel rate from MAF sensor using stoichiometric ratio.

    Args:
        maf_gs: Mass Air Flow rate in grams per second.
        afr: Air-Fuel Ratio (default 14.7 for petrol stoichiometric).

    Returns:
        Estimated fuel rate in grams per second.
    """
    return maf_gs / afr


def parse_obd_frame(raw_pids: Dict[int, list[int]], speed_prev: float = 0.0, dt: float = 1.0) -> Dict:
    """Parse a set of OBD-II PID responses into a SmartPUC telemetry dict.

    Args:
        raw_pids: Dict mapping PID numbers to their raw response bytes.
        speed_prev: Previous speed reading for acceleration calculation.
        dt: Time delta since previous reading in seconds.

    Returns:
        Dict compatible with SmartPUC's /api/record endpoint format with keys:
        speed, rpm, fuel_rate, fuel_type, acceleration, ambient_temp.
    """
    speed = decode_pid(0x0D, raw_pids[0x0D]) if 0x0D in raw_pids else 0.0
    rpm = int(decode_pid(0x0C, raw_pids[0x0C])) if 0x0C in raw_pids else 0

    # Fuel rate: prefer direct PID 0x5E, fallback to MAF-based estimate
    fuel_rate_l100km = 0.0
    if 0x5E in raw_pids:
        fuel_lph = decode_pid(0x5E, raw_pids[0x5E])
        fuel_rate_l100km = fuel_rate_lph_to_l100km(fuel_lph, speed)
    elif 0x10 in raw_pids:
        maf = decode_pid(0x10, raw_pids[0x10])
        fuel_gs = maf_to_fuel_rate(maf)
        fuel_lph = fuel_gs * 3600 / 740  # g/s -> L/h (petrol density ~740 g/L)
        fuel_rate_l100km = fuel_rate_lph_to_l100km(fuel_lph, speed)

    intake_temp = decode_pid(0x0F, raw_pids[0x0F]) if 0x0F in raw_pids else 25.0
    acceleration = (speed - speed_prev) / (3.6 * dt) if dt > 0 else 0.0

    return {
        "speed": round(speed, 1),
        "rpm": rpm,
        "fuel_rate": round(fuel_rate_l100km, 2),
        "fuel_type": "petrol",
        "acceleration": round(acceleration, 3),
        "ambient_temp": intake_temp,
    }


# Hardware integration path (documented, not implemented):
#
# ELM327 Bluetooth dongle
#   -> Android companion app (sends AT commands via SPP)
#   -> HTTP POST to SmartPUC REST API /api/record
#   -> SmartPUC pipeline (VSP -> emissions -> fraud -> blockchain)
#
# Recommended ELM327 commands:
#   AT Z       — reset
#   AT E0      — echo off
#   AT SP 0    — auto protocol
#   01 0C      — request RPM
#   01 0D      — request speed
#   01 0F      — request intake air temp
#   01 5E      — request fuel rate
