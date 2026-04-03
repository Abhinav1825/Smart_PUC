"""
Smart PUC — CO₂ Emission Calculation Engine
=============================================
Calculates CO₂ emissions in g/km from OBD-II telemetry data.

Formula (FR-05):
    CO₂ (g/km) = fuel_rate (L/100km) × emission_factor (g/L) / 100

Emission Factors (IPCC / ARAI / EURO 6 aligned):
    - Petrol  : 2,310 g CO₂ per litre  (2.31 kg/L)
    - Diesel  : 2,680 g CO₂ per litre  (2.68 kg/L)

Edge Cases:
    - Speed ≈ 0 (idle): fuel is burned but no distance covered, so g/km is
      theoretically infinite. We cap idle emissions at a high fixed value
      (300 g/km) to represent poor efficiency without breaking the math.

Supports FR-05 through FR-08.
"""

# ──────────────────────────── Constants ────────────────────────────────────────

EMISSION_FACTORS = {
    "petrol": 2310,   # g CO₂ per litre
    "diesel": 2680,   # g CO₂ per litre
}

# CO₂ threshold for compliance (default, can also be set on-chain)
DEFAULT_THRESHOLD = 120  # g/km — Bharat Stage VI for petrol

# Cap for idle emissions (speed ≈ 0) to avoid infinity
IDLE_CO2_CAP = 300  # g/km

# Minimum speed to consider vehicle "moving" (km/h)
MIN_MOVING_SPEED = 2.0

# ──────────────────────────── Core Function ────────────────────────────────────

def calculate_co2(fuel_rate, speed, fuel_type="petrol"):
    """
    Calculate CO₂ emission in grams per kilometre.

    Args:
        fuel_rate  (float): Fuel consumption in litres per 100 km
        speed      (float): Vehicle speed in km/h
        fuel_type  (str)  : "petrol" or "diesel"

    Returns:
        dict: {
            "co2_g_per_km" : float,   # Rounded CO₂ value
            "co2_int"      : int,     # Integer version for blockchain storage
            "fuel_type"    : str,
            "emission_factor": int,   # g/L used
            "threshold"    : int,     # default threshold g/km
            "status"       : str,     # "PASS" or "FAIL"
        }

    Raises:
        ValueError: If fuel_type is not recognized or inputs are negative
    """
    # ── Validation ─────────────────────────────────────────────────────────
    if fuel_type not in EMISSION_FACTORS:
        raise ValueError(
            f"Unknown fuel type '{fuel_type}'. Supported: {list(EMISSION_FACTORS.keys())}"
        )
    if fuel_rate < 0:
        raise ValueError(f"fuel_rate must be >= 0, got {fuel_rate}")
    if speed < 0:
        raise ValueError(f"speed must be >= 0, got {speed}")

    emission_factor = EMISSION_FACTORS[fuel_type]

    # ── Calculation ────────────────────────────────────────────────────────
    if speed < MIN_MOVING_SPEED:
        # Idle / near-stationary: cap at a high value
        co2_g_per_km = min(fuel_rate * emission_factor / 100, IDLE_CO2_CAP)
    else:
        # Normal driving:  CO₂ = fuel_rate(L/100km) × factor(g/L) / 100
        co2_g_per_km = fuel_rate * emission_factor / 100

    co2_g_per_km = round(co2_g_per_km, 2)
    co2_int = int(round(co2_g_per_km))

    # ── Compliance check ───────────────────────────────────────────────────
    status = "PASS" if co2_int <= DEFAULT_THRESHOLD else "FAIL"

    return {
        "co2_g_per_km": co2_g_per_km,
        "co2_int": co2_int,
        "fuel_type": fuel_type,
        "emission_factor": emission_factor,
        "threshold": DEFAULT_THRESHOLD,
        "status": status,
    }


# ──────────────────────────── Convenience ──────────────────────────────────────

def process_obd_reading(reading):
    """
    Convenience wrapper: takes a full OBD-II reading dict and returns
    the enriched result with CO₂ values appended.

    Args:
        reading (dict): Must contain keys: fuel_rate, speed, fuel_type

    Returns:
        dict: Original reading merged with CO₂ calculation results
    """
    result = calculate_co2(
        fuel_rate=reading["fuel_rate"],
        speed=reading["speed"],
        fuel_type=reading.get("fuel_type", "petrol"),
    )
    return {**reading, **result}


# ──────────────────────────── Standalone Test ──────────────────────────────────

if __name__ == "__main__":
    test_cases = [
        {"fuel_rate": 5.0,  "speed": 80,  "fuel_type": "petrol", "label": "Highway cruise"},
        {"fuel_rate": 8.0,  "speed": 40,  "fuel_type": "petrol", "label": "City driving"},
        {"fuel_rate": 12.0, "speed": 30,  "fuel_type": "petrol", "label": "Heavy traffic"},
        {"fuel_rate": 4.0,  "speed": 0,   "fuel_type": "petrol", "label": "Idle"},
        {"fuel_rate": 6.5,  "speed": 100, "fuel_type": "petrol", "label": "Fast highway"},
    ]

    print("🧪 CO₂ Emission Engine — Test Cases (Petrol)")
    print("=" * 65)
    for tc in test_cases:
        r = calculate_co2(tc["fuel_rate"], tc["speed"], tc["fuel_type"])
        print(
            f"  {tc['label']:>15s} | "
            f"Fuel: {tc['fuel_rate']:>5.1f} L/100km | "
            f"Speed: {tc['speed']:>5.1f} km/h | "
            f"CO₂: {r['co2_int']:>4d} g/km | "
            f"{r['status']}"
        )
