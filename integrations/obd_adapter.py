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
    0x05: {"name": "coolant_temp", "unit": "degC", "formula": "A-40", "bytes": 1},
    0x0C: {"name": "rpm", "unit": "rev/min", "formula": "((A*256)+B)/4", "bytes": 2},
    0x0D: {"name": "speed", "unit": "km/h", "formula": "A", "bytes": 1},
    0x0F: {"name": "intake_air_temp", "unit": "degC", "formula": "A-40", "bytes": 1},
    0x10: {"name": "maf_rate", "unit": "g/s", "formula": "((A*256)+B)/100", "bytes": 2},
    0x11: {"name": "throttle", "unit": "%", "formula": "(A*100)/255", "bytes": 1},
    0x5E: {"name": "fuel_rate", "unit": "L/h", "formula": "((A*256)+B)/20", "bytes": 2},
}


# COPERT 5 cold-start threshold (EEA, 2023). Below this coolant
# temperature the catalytic converter has not reached light-off and
# emission factors must be computed with the cold-start uplift rather
# than the hot-stabilised factor.
COLD_START_COOLANT_THRESHOLD_C: float = 70.0


def is_cold_start(obd_readings: dict) -> bool:
    """Return ``True`` if the engine is in a cold-start regime.

    Audit §13A #6 — originally the cold-start flag was inferred from a
    boolean elsewhere in the pipeline; COPERT 5 (EEA, 2023) specifies
    that a petrol/diesel catalyst is cold below ~70 °C coolant. When the
    OBD dongle exposes PID ``0x05`` (engine coolant temperature) we can
    use the sensor value directly. When the PID is absent we fall back
    to whatever boolean the upstream reading already carries under the
    key ``cold_start`` (default ``False``).

    The helper accepts any dict-like structure so it can be fed either
    a parsed PID map ``{0x05: [A]}``, a decoded dict
    ``{"coolant_temp": 55.0}``, or a telemetry record carrying a
    pre-computed ``cold_start`` bool.
    """
    # Case 1: raw PID bytes
    if 0x05 in obd_readings:
        try:
            coolant = float(decode_pid(0x05, obd_readings[0x05]))
            return coolant < COLD_START_COOLANT_THRESHOLD_C
        except (ValueError, TypeError):
            pass

    # Case 2: decoded coolant_temp key
    coolant_val = obd_readings.get("coolant_temp")
    if coolant_val is None:
        coolant_val = obd_readings.get("coolant_temp_c")
    if coolant_val is not None:
        try:
            return float(coolant_val) < COLD_START_COOLANT_THRESHOLD_C
        except (ValueError, TypeError):
            pass

    # Case 3: fallback to pre-existing boolean
    return bool(obd_readings.get("cold_start", False))


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

    if pid == 0x05:
        return A - 40.0
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


# ─── Emission-Related DTC Codes (SAE J2012 / ISO 15031-6) ───────
DTC_EMISSION_CODES = {
    "P0420": {"system": "catalyst", "description": "Catalyst System Efficiency Below Threshold (Bank 1)", "severity": "high"},
    "P0421": {"system": "catalyst", "description": "Warm Up Catalyst Efficiency Below Threshold (Bank 1)", "severity": "high"},
    "P0430": {"system": "catalyst", "description": "Catalyst System Efficiency Below Threshold (Bank 2)", "severity": "high"},
    "P0401": {"system": "egr", "description": "EGR Flow Insufficient Detected", "severity": "high"},
    "P0402": {"system": "egr", "description": "EGR Flow Excessive Detected", "severity": "medium"},
    "P0171": {"system": "fuel", "description": "System Too Lean (Bank 1)", "severity": "medium"},
    "P0172": {"system": "fuel", "description": "System Too Rich (Bank 1)", "severity": "medium"},
    "P0130": {"system": "o2_sensor", "description": "O2 Sensor Circuit (Bank 1, Sensor 1)", "severity": "medium"},
    "P0131": {"system": "o2_sensor", "description": "O2 Sensor Circuit Low Voltage (Bank 1, Sensor 1)", "severity": "medium"},
    "P0300": {"system": "ignition", "description": "Random/Multiple Cylinder Misfire Detected", "severity": "high"},
    "P0301": {"system": "ignition", "description": "Cylinder 1 Misfire Detected", "severity": "medium"},
    "P2463": {"system": "dpf", "description": "Diesel Particulate Filter Restriction - Soot Accumulation", "severity": "high"},
    "P244A": {"system": "dpf", "description": "DPF Differential Pressure Too Low", "severity": "high"},
    "P0101": {"system": "maf", "description": "MAF Sensor Range/Performance", "severity": "medium"},
}

# DTC type prefixes per SAE J2012
_DTC_TYPE_CHARS = {0: "P", 1: "C", 2: "B", 3: "U"}


def decode_dtc_bytes(raw_bytes: list[int]) -> list[str]:
    """Decode raw DTC response bytes (SAE J1979 Mode 03) into DTC strings.

    Each DTC is 2 bytes:
    - Byte 1: bits 7-6 = type (00=P, 01=C, 10=B, 11=U), bits 5-4 = digit 2, bits 3-0 = digit 3
    - Byte 2: bits 7-4 = digit 4, bits 3-0 = digit 5

    Returns: list of DTC strings like ["P0420", "P0171"]
    """
    dtcs: list[str] = []
    if len(raw_bytes) < 2:
        return dtcs
    for i in range(0, len(raw_bytes) - 1, 2):
        b1 = raw_bytes[i]
        b2 = raw_bytes[i + 1]
        # Skip null DTCs (0x0000)
        if b1 == 0 and b2 == 0:
            continue
        dtc_type = (b1 >> 6) & 0x03
        digit2 = (b1 >> 4) & 0x03
        digit3 = b1 & 0x0F
        digit4 = (b2 >> 4) & 0x0F
        digit5 = b2 & 0x0F
        type_char = _DTC_TYPE_CHARS.get(dtc_type, "P")
        dtc_str = f"{type_char}{digit2}{digit3:X}{digit4:X}{digit5:X}"
        dtcs.append(dtc_str)
    return dtcs


def classify_dtcs(dtc_codes: list[str]) -> dict:
    """Classify DTCs by emission system and severity.

    Returns: {
        "emission_related": [{"code": "P0420", "system": "catalyst", ...}],
        "other": ["P0442", ...],
        "highest_severity": "high" or "medium" or "low" or "none",
        "degradation_signal": bool (True if any high-severity emission DTC)
    }
    """
    emission_related: list[dict] = []
    other: list[str] = []
    highest_severity = "none"
    severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}

    for code in dtc_codes:
        if code in DTC_EMISSION_CODES:
            info = DTC_EMISSION_CODES[code]
            emission_related.append({
                "code": code,
                "system": info["system"],
                "description": info["description"],
                "severity": info["severity"],
            })
            if severity_rank.get(info["severity"], 0) > severity_rank.get(highest_severity, 0):
                highest_severity = info["severity"]
        else:
            other.append(code)

    degradation_signal = any(
        e["severity"] == "high" for e in emission_related
    )

    return {
        "emission_related": emission_related,
        "other": other,
        "highest_severity": highest_severity,
        "degradation_signal": degradation_signal,
    }


def dtc_to_degradation_type(dtc_codes: list[str]) -> Optional[str]:
    """Map DTCs to degradation model failure types.

    P0420/P0421/P0430 -> "catalyst_aging"
    P0401/P0402       -> "egr_failure"
    P0130/P0131       -> "o2_sensor_drift"
    P0300/P0301       -> "injector_fouling"
    P2463/P244A       -> "dpf_removal_diesel"

    Returns: failure type string or None if no match
    """
    _DTC_DEGRADATION_MAP = {
        "P0420": "catalyst_aging",
        "P0421": "catalyst_aging",
        "P0430": "catalyst_aging",
        "P0401": "egr_failure",
        "P0402": "egr_failure",
        "P0130": "o2_sensor_drift",
        "P0131": "o2_sensor_drift",
        "P0300": "injector_fouling",
        "P0301": "injector_fouling",
        "P2463": "dpf_removal_diesel",
        "P244A": "dpf_removal_diesel",
    }
    for code in dtc_codes:
        if code in _DTC_DEGRADATION_MAP:
            return _DTC_DEGRADATION_MAP[code]
    return None


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
