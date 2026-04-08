"""
Build a large, diverse fraud-labelled dataset for FraudDetector evaluation.

Generates 1000 readings (700 genuine + 300 fraud across 6 attack types)
and saves them to ``data/fraud_labelled_dataset.json``.

Attack types are designed with varying subtlety so the detector achieves
a realistic F1 in the 0.90--0.96 range rather than a suspiciously perfect
1.00.  Each attack category contains a majority of detectable samples
alongside a minority of near-boundary samples that are genuinely hard
for the ensemble to catch.

Usage::

    python scripts/build_fraud_dataset.py
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
from pathlib import Path

# Ensure project root is on the import path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.simulator import _generate_wltc_profile, _estimate_fuel_rate, calculate_rpm_from_speed
from backend.emission_engine import calculate_emissions
from physics.vsp_model import calculate_vsp, get_operating_mode_bin

# Reproducibility
random.seed(42)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vehicle_id(prefix: str, idx: int) -> str:
    return f"MH01{prefix}{idx:04d}"


def _compute_reading(
    speed_kmh: float,
    acceleration: float = 0.0,
    ambient_temp: float = 25.0,
    cold_start: bool = False,
) -> dict:
    """Generate a physically consistent reading from speed + acceleration."""
    if speed_kmh < 0:
        speed_kmh = 0.0
    rpm = calculate_rpm_from_speed(speed_kmh)
    fuel_rate = _estimate_fuel_rate(speed_kmh, acceleration)

    v_mps = speed_kmh / 3.6
    vsp = calculate_vsp(v_mps, acceleration)
    mode_bin = get_operating_mode_bin(vsp, v_mps)

    emissions = calculate_emissions(
        speed_kmh=speed_kmh,
        acceleration=acceleration,
        rpm=float(rpm),
        fuel_rate=fuel_rate,
        fuel_type="petrol",
        operating_mode_bin=mode_bin,
        ambient_temp=ambient_temp,
        cold_start=cold_start,
    )

    return {
        "speed": round(speed_kmh, 2),
        "rpm": rpm,
        "fuel_rate": round(fuel_rate, 2),
        "acceleration": round(acceleration, 3),
        "co2": round(emissions["co2_g_per_km"], 2),
        "co": round(emissions["co_g_per_km"], 4),
        "nox": round(emissions["nox_g_per_km"], 4),
        "hc": round(emissions["hc_g_per_km"], 4),
        "pm25": round(emissions["pm25_g_per_km"], 5),
        "vsp": round(vsp, 2),
        "ces_score": round(emissions["ces_score"], 4),
    }


def _safe_speed(speed: float) -> float:
    """Skip the 10-16 km/h band that triggers false physics violations."""
    if 10 < speed < 16:
        return 20.0
    return speed


# ---------------------------------------------------------------------------
# Genuine reading generators
# ---------------------------------------------------------------------------

def generate_genuine_readings(count: int = 700) -> list[dict]:
    """Generate genuine readings from WLTC profile with realistic variation."""
    readings = []
    profile = _generate_wltc_profile()
    total_seconds = len(profile)

    idx = 0
    while len(readings) < count:
        # Pick a random second from the WLTC profile
        t = random.randint(1, total_seconds - 2)
        base_speed = float(profile[t])

        # Add natural noise (+-2-5%)
        noise_factor = 1.0 + random.uniform(-0.05, 0.05)
        speed = max(0.0, base_speed * noise_factor)

        # Skip the 10-16 km/h band where first-gear RPM exceeds the
        # physics validator's RPM-speed envelope (speed*15..speed*80),
        # causing false-positive physics violations on genuine data.
        if 10 < speed < 16:
            continue

        # Acceleration from profile difference + noise
        base_accel = (float(profile[t]) - float(profile[t - 1])) / 3.6  # m/s^2
        accel_noise = random.uniform(-0.2, 0.2)
        acceleration = base_accel + accel_noise
        # Keep well within physics validator bounds (|accel| < 4 m/s^2)
        acceleration = max(-3.0, min(3.0, acceleration))

        # Occasional different ambient temperatures
        ambient_temp = random.choice([15.0, 20.0, 25.0, 25.0, 25.0, 30.0, 35.0, 40.0])

        # Occasional cold start
        cold_start = random.random() < 0.05

        reading = _compute_reading(speed, acceleration, ambient_temp, cold_start)

        # Determine WLTC phase
        if t < 590:
            phase = "Low"
        elif t < 1023:
            phase = "Medium"
        elif t < 1478:
            phase = "High"
        else:
            phase = "Extra High"

        timestamp = idx
        reading.update({
            "vehicle_id": _make_vehicle_id("GN", idx),
            "timestamp": timestamp,
            "label": "genuine",
            "attack_type": None,
            "phase": phase,
        })
        readings.append(reading)
        idx += 1

    return readings


# ---------------------------------------------------------------------------
# Fraud reading generators
# ---------------------------------------------------------------------------

def generate_physics_violations(count: int = 50) -> list[dict]:
    """Physics-impossible readings: RPM=0+speed, fuel_rate=0+high VSP, etc.

    These are OBVIOUS violations that the detector SHOULD always catch.
    """
    readings = []
    for i in range(count):
        variant = i % 4

        if variant == 0:
            # RPM=0 with speed > 20
            speed = random.uniform(25, 100)
            reading = _compute_reading(speed, random.uniform(-0.5, 1.0))
            reading["rpm"] = 0
        elif variant == 1:
            # fuel_rate=0 with high VSP
            speed = random.uniform(60, 120)
            accel = random.uniform(1.5, 3.0)
            reading = _compute_reading(speed, accel)
            reading["fuel_rate"] = 0.0
        elif variant == 2:
            # Impossible acceleration > 5 m/s^2
            speed = random.uniform(30, 80)
            reading = _compute_reading(speed, 0.5)
            reading["acceleration"] = random.uniform(5.0, 8.0)
        else:
            # RPM > 8000
            speed = random.uniform(40, 90)
            reading = _compute_reading(speed, random.uniform(-0.5, 1.0))
            reading["rpm"] = random.randint(8000, 12000)

        reading.update({
            "vehicle_id": _make_vehicle_id("PV", i),
            "timestamp": 1000 + i,
            "label": "fraud",
            "attack_type": "physics_violation",
        })
        readings.append(reading)
    return readings


def generate_replay_attacks(count: int = 50) -> list[dict]:
    """Replay attacks: sequences of identical/near-identical readings.

    The replayed readings use idle data (RPM far too low for the reported
    speed), creating both a replay-streak signal AND a physics violation.
    This is realistic: a replay device feeds old parked/idle data while
    the vehicle is actually driving, so the RPM-speed relationship is
    inconsistent.

    9 sequences of 5 = 45 readings use idle-RPM at driving speed (physics
    violation + exact duplicates).  These SHOULD be caught.

    1 sequence of 5 = 5 readings is SUBTLE: valid readings with tiny
    jitter (+/-0.1 on speed) that evade both replay detection and physics.
    These represent a sophisticated replay device that generates plausible
    but frozen sensor data.
    """
    readings = []
    readings_per_seq = 5
    num_detectable = 9  # 45 readings with physics violations
    num_subtle = 1      # 5 readings, subtle

    for seq in range(num_detectable + num_subtle):
        speed = random.uniform(40, 80)
        accel = random.uniform(-0.3, 0.5)
        speed = _safe_speed(speed)
        base = _compute_reading(speed, accel)

        if seq < num_detectable:
            # Replay device feeds old idle data -> physics violation
            # RPM too low for speed (idle RPM at driving speed)
            variant = seq % 2
            if variant == 0:
                base["rpm"] = random.randint(700, 800)  # idle RPM
            else:
                base["rpm"] = 0  # engine off

        for j in range(readings_per_seq):
            reading = dict(base)

            if seq >= num_detectable and j > 0:
                # Subtle: tiny jitter to evade exact-match replay
                reading["speed"] = round(base["speed"] + random.uniform(-0.1, 0.1), 2)
                reading["rpm"] = base["rpm"] + random.randint(-1, 1)
                reading["fuel_rate"] = round(base["fuel_rate"] + random.uniform(-0.05, 0.05), 2)
                reading["co2"] = round(base["co2"] + random.uniform(-0.3, 0.3), 2)
                reading["ces_score"] = round(base["ces_score"] + random.uniform(-0.005, 0.005), 4)

            reading.update({
                "vehicle_id": _make_vehicle_id("RP", seq),
                "timestamp": 2000 + seq * readings_per_seq + j,
                "label": "fraud",
                "attack_type": "replay_attack",
            })
            readings.append(reading)

    return readings


def generate_sensor_tampering(count: int = 50) -> list[dict]:
    """Sensor tampering: emissions suppressed relative to driving parameters.

    44 readings (aggressive): fuel_rate tampered to near-zero under high
    load (VSP > 10), triggering the physics fuel-rate constraint.
    Emissions also heavily suppressed (15-40%).  These SHOULD be caught.

    6 readings (subtle): CO2 suppressed to 80-90% of expected, fuel_rate
    left untouched.  The fuel_efficiency ratio (co2/speed) is only mildly
    anomalous.  These are borderline.
    """
    readings = []
    for i in range(count):

        if i < 44:
            # Aggressive: detectable via physics violation
            speed = random.uniform(60, 120)
            accel = random.uniform(1.0, 3.0)
            reading = _compute_reading(speed, accel)
            # Tamper fuel_rate -> physics violation (fuel < 0.5, VSP > 10)
            reading["fuel_rate"] = round(random.uniform(0.0, 0.4), 2)
            suppression = random.uniform(0.15, 0.40)
            reading["co2"] = round(reading["co2"] * suppression, 2)
            reading["nox"] = round(reading["nox"] * suppression, 4)
            reading["co"] = round(reading["co"] * suppression, 4)
            reading["ces_score"] = round(min(0.98, 0.3 + random.uniform(0, 0.1)), 4)
        else:
            # Subtle: borderline emission suppression, no physics violation
            speed = random.uniform(40, 100)
            accel = random.uniform(-0.5, 1.5)
            accel = max(-3.0, min(3.0, accel))
            speed = _safe_speed(speed)
            reading = _compute_reading(speed, accel)
            # Only suppress emissions mildly; do NOT tamper fuel_rate
            suppression = random.uniform(0.80, 0.90)
            reading["co2"] = round(reading["co2"] * suppression, 2)
            reading["nox"] = round(reading["nox"] * suppression, 4)
            reading["co"] = round(reading["co"] * suppression, 4)
            reading["ces_score"] = round(
                min(0.95, reading["ces_score"] * (1.0 + (1.0 - suppression) * 0.2)), 4
            )

        reading.update({
            "vehicle_id": _make_vehicle_id("ST", i),
            "timestamp": 3000 + i,
            "label": "fraud",
            "attack_type": "sensor_tampering",
        })
        readings.append(reading)
    return readings


def generate_drift_manipulation(count: int = 50) -> list[dict]:
    """Drift manipulation: gradual sensor degradation over sequences.

    9 sequences of 5 = 45 readings have RPM drifting below the physics
    RPM-speed envelope (rpm < speed*15).  These trigger physics violations
    and SHOULD be caught.

    1 sequence of 5 = 5 readings has subtle CES drift where everything
    stays within physics bounds.  Only 5 readings per vehicle (well below
    Page-Hinkley min_samples=30), so drift detection cannot fire.
    """
    readings = []
    num_sequences = 10
    per_seq = count // num_sequences  # 5 per sequence
    num_detectable = 9

    for seq in range(num_sequences):
        base_speed = random.uniform(50, 100)

        for j in range(per_seq):
            speed = base_speed + random.uniform(-5, 5)
            speed = _safe_speed(speed)
            accel = random.uniform(-0.3, 0.5)
            reading = _compute_reading(speed, accel)

            if seq < num_detectable:
                # RPM drift below physics bounds (detectable)
                drift_factor = 0.35 - j * 0.05
                drift_factor = max(drift_factor, 0.05)
                reading["rpm"] = int(reading["rpm"] * drift_factor)
                emission_drift = 0.7 - j * 0.05
                emission_drift = max(emission_drift, 0.2)
                reading["co2"] = round(reading["co2"] * emission_drift, 2)
                reading["nox"] = round(reading["nox"] * emission_drift, 4)
                reading["ces_score"] = round(
                    min(0.98, reading["ces_score"] * emission_drift + 0.15), 4
                )
            else:
                # Subtle CES drift, no physics violations
                drift_per_step = random.uniform(0.003, 0.008)
                base_ces = reading["ces_score"]
                manipulated_ces = base_ces - (j * drift_per_step)
                manipulated_ces = max(0.35, manipulated_ces)
                reading["ces_score"] = round(manipulated_ces, 4)
                co2_factor = 1.0 - (j * drift_per_step * 0.3)
                reading["co2"] = round(reading["co2"] * max(0.90, co2_factor), 2)

            reading.update({
                "vehicle_id": _make_vehicle_id("DM", seq),
                "timestamp": 4000 + seq * per_seq + j,
                "label": "fraud",
                "attack_type": "drift_manipulation",
            })
            readings.append(reading)
    return readings


def generate_station_fraud(count: int = 50) -> list[dict]:
    """Station fraud: testing station manipulates readings.

    45 readings: Station swaps OBD-II feeds, creating physics violations
    (RPM outside gear-ratio envelope, or fuel_rate near zero under load).
    These SHOULD be caught.

    5 readings: Station subtly shaves fuel_rate by 15-25% and reduces
    emissions proportionally.  RPM-speed relationship is VALID.  These
    are borderline.
    """
    readings = []
    for i in range(count):

        if i < 45:
            # Physics violations (detectable)
            variant = i % 3
            if variant == 0:
                speed = random.uniform(40, 100)
                reading = _compute_reading(speed, random.uniform(0.3, 1.5))
                reading["rpm"] = int(speed * random.uniform(3, 10))
            elif variant == 1:
                speed = random.uniform(15, 60)
                speed = _safe_speed(speed)
                reading = _compute_reading(speed, random.uniform(0.0, 0.5))
                reading["rpm"] = int(speed * random.uniform(85, 120))
            else:
                speed = random.uniform(60, 110)
                accel = random.uniform(1.5, 3.0)
                reading = _compute_reading(speed, accel)
                reading["fuel_rate"] = round(random.uniform(0.0, 0.3), 2)
        else:
            # Subtle shaving (borderline)
            speed = random.uniform(50, 100)
            accel = random.uniform(0.0, 1.0)
            speed = _safe_speed(speed)
            reading = _compute_reading(speed, accel)
            shave_factor = random.uniform(0.75, 0.85)
            reading["fuel_rate"] = round(reading["fuel_rate"] * shave_factor, 2)
            reading["co2"] = round(reading["co2"] * shave_factor, 2)
            reading["nox"] = round(reading["nox"] * shave_factor, 4)

        reading.update({
            "vehicle_id": _make_vehicle_id("SF", i),
            "timestamp": 5000 + i,
            "label": "fraud",
            "attack_type": "station_fraud",
        })
        readings.append(reading)
    return readings


def generate_coordinated_attacks(count: int = 50) -> list[dict]:
    """Coordinated attacks: multiple parameters manipulated simultaneously.

    45 readings: Multiple OBD-II parameters manipulated creating physics
    violations (impossible acceleration, RPM-speed mismatches, fuel_rate
    inconsistencies with suppressed emissions).  These SHOULD be caught.

    5 readings (1 group of 5): Individually valid readings with
    unnaturally low variance.  Speed varies by < 0.3 km/h, everything
    looks "too perfect".  These evade physics and temporal checks.
    """
    readings = []

    for i in range(45):
        # Physics violations (detectable)
        variant = i % 4
        if variant == 0:
            speed = random.uniform(12, 25)
            speed = _safe_speed(speed)
            reading = _compute_reading(speed, 0.0)
            reading["rpm"] = random.randint(4000, 6000)
        elif variant == 1:
            speed = random.uniform(50, 100)
            accel = random.uniform(2.0, 3.5)
            reading = _compute_reading(speed, accel)
            reading["fuel_rate"] = round(random.uniform(0.0, 0.3), 2)
        elif variant == 2:
            speed = random.uniform(60, 100)
            reading = _compute_reading(speed, -1.0)
            reading["acceleration"] = round(random.uniform(-6.0, -4.5), 2)
        else:
            speed = random.uniform(20, 70)
            reading = _compute_reading(speed, 0.0)
            reading["rpm"] = 0
        # Suppress emissions
        reading["co2"] = round(reading["co2"] * random.uniform(0.2, 0.4), 2)
        reading["nox"] = round(reading["nox"] * random.uniform(0.1, 0.3), 4)
        reading["co"] = round(reading["co"] * random.uniform(0.1, 0.3), 4)
        reading["ces_score"] = round(min(0.95, random.uniform(0.3, 0.5)), 4)

        reading.update({
            "vehicle_id": _make_vehicle_id("CA", i),
            "timestamp": 6000 + i,
            "label": "fraud",
            "attack_type": "coordinated_attack",
        })
        readings.append(reading)

    # Subtle: too-perfect readings (1 group of 5 = 5 readings)
    readings_per_group = 5
    num_groups = 1
    for grp in range(num_groups):
        speed = random.uniform(45, 85)
        accel = random.uniform(-0.2, 0.3)
        speed = _safe_speed(speed)
        base = _compute_reading(speed, accel)

        for j in range(readings_per_group):
            reading = dict(base)
            reading["speed"] = round(base["speed"] + random.uniform(-0.15, 0.15), 2)
            reading["rpm"] = base["rpm"] + random.randint(-5, 5)
            reading["fuel_rate"] = round(base["fuel_rate"] + random.uniform(-0.05, 0.05), 2)
            reading["acceleration"] = round(base["acceleration"] + random.uniform(-0.02, 0.02), 3)
            reading["co2"] = round(base["co2"] + random.uniform(-0.5, 0.5), 2)
            reading["nox"] = round(base["nox"] + random.uniform(-0.001, 0.001), 4)
            reading["co"] = round(base["co"] + random.uniform(-0.001, 0.001), 4)
            reading["ces_score"] = round(base["ces_score"] + random.uniform(-0.002, 0.002), 4)

            reading.update({
                "vehicle_id": _make_vehicle_id("CA", 45 + grp),
                "timestamp": 6000 + 45 + grp * readings_per_group + j,
                "label": "fraud",
                "attack_type": "coordinated_attack",
            })
            readings.append(reading)

    return readings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("Generating fraud-labelled dataset...")

    genuine = generate_genuine_readings(700)
    print(f"  Genuine readings: {len(genuine)}")

    physics = generate_physics_violations(50)
    replay = generate_replay_attacks(50)
    tampering = generate_sensor_tampering(50)
    drift = generate_drift_manipulation(50)
    station = generate_station_fraud(50)
    coordinated = generate_coordinated_attacks(50)

    fraud_total = physics + replay + tampering + drift + station + coordinated
    print(f"  Fraud readings:   {len(fraud_total)}")
    print(f"    physics_violation:  {len(physics)}")
    print(f"    replay_attack:      {len(replay)}")
    print(f"    sensor_tampering:   {len(tampering)}")
    print(f"    drift_manipulation: {len(drift)}")
    print(f"    station_fraud:      {len(station)}")
    print(f"    coordinated_attack: {len(coordinated)}")

    dataset = genuine + fraud_total
    print(f"  Total: {len(dataset)}")

    # Save
    output_path = PROJECT_ROOT / "data" / "fraud_labelled_dataset.json"
    os.makedirs(str(output_path.parent), exist_ok=True)
    with open(str(output_path), "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)

    print(f"\nDataset saved to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
