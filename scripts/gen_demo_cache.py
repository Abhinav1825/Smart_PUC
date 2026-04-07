#!/usr/bin/env python3
"""Generate pre-recorded simulation cache for 10 demo vehicles."""
import json
import random
import os

random.seed(42)

WLTC_PHASES = [
    ("Low", 0.23),
    ("Medium", 0.27),
    ("High", 0.27),
    ("Extra High", 0.23),
]


def pick_phase():
    r = random.random()
    cum = 0
    for name, prob in WLTC_PHASES:
        cum += prob
        if r < cum:
            return name
    return "Medium"


def speed_for_phase(phase, max_speed):
    ranges = {
        "Low": (0, min(45, max_speed * 0.35)),
        "Medium": (min(25, max_speed * 0.2), min(75, max_speed * 0.55)),
        "High": (min(55, max_speed * 0.4), min(100, max_speed * 0.75)),
        "Extra High": (min(80, max_speed * 0.6), max_speed),
    }
    lo, hi = ranges.get(phase, (0, max_speed))
    return round(random.uniform(lo, hi), 1)


def rpm_from_speed(speed, max_speed, idle_rpm=800, redline=6000):
    if speed < 2:
        return idle_rpm + random.randint(-50, 50)
    ratio = speed / max_speed
    rpm = idle_rpm + ratio * (redline - idle_rpm) * 0.85
    rpm += random.uniform(-200, 200)
    return max(idle_rpm, min(redline, int(rpm)))


vehicles = {
    "MH12AB1234": {
        "display_name": "Maruti Ciaz (Sedan)",
        "vehicle_class": "SEDAN",
        "fuel_type": "petrol",
        "max_speed": 130,
        "ces_range": (0.5, 0.9),
        "co2_base": 120, "co_base": 0.45, "nox_base": 0.035,
        "hc_base": 0.06, "pm25_base": 0.003,
        "fail_prob": 0.05,
        "fraud_range": (0.05, 0.20),
        "idle_rpm": 750, "redline": 6500,
    },
    "MH01CD5678": {
        "display_name": "Maruti WagonR (Hatchback)",
        "vehicle_class": "HATCHBACK",
        "fuel_type": "petrol",
        "max_speed": 120,
        "ces_range": (0.4, 0.7),
        "co2_base": 95, "co_base": 0.35, "nox_base": 0.028,
        "hc_base": 0.045, "pm25_base": 0.002,
        "fail_prob": 0.0,
        "fraud_range": (0.03, 0.15),
        "idle_rpm": 800, "redline": 6200,
    },
    "MH04EF9012": {
        "display_name": "Hyundai Creta (SUV)",
        "vehicle_class": "SUV",
        "fuel_type": "diesel",
        "max_speed": 130,
        "ces_range": (0.7, 1.1),
        "co2_base": 155, "co_base": 0.30, "nox_base": 0.12,
        "hc_base": 0.04, "pm25_base": 0.008,
        "fail_prob": 0.15,
        "fraud_range": (0.08, 0.25),
        "idle_rpm": 800, "redline": 4500,
    },
    "MH02GH3456": {
        "display_name": "Bajaj RE (Auto-rickshaw)",
        "vehicle_class": "AUTO_RICKSHAW",
        "fuel_type": "cng",
        "max_speed": 70,
        "ces_range": (0.3, 0.6),
        "co2_base": 65, "co_base": 0.55, "nox_base": 0.02,
        "hc_base": 0.08, "pm25_base": 0.001,
        "fail_prob": 0.0,
        "fraud_range": (0.04, 0.18),
        "idle_rpm": 900, "redline": 5500,
    },
    "MH14JK7890": {
        "display_name": "Tata LPT 1613 (Truck)",
        "vehicle_class": "TRUCK",
        "fuel_type": "diesel",
        "max_speed": 90,
        "ces_range": (0.9, 1.5),
        "co2_base": 320, "co_base": 0.80, "nox_base": 0.35,
        "hc_base": 0.10, "pm25_base": 0.025,
        "fail_prob": 0.45,
        "fraud_range": (0.10, 0.35),
        "idle_rpm": 700, "redline": 3200,
    },
    "MH03LM2345": {
        "display_name": "Honda Activa (Two-wheeler)",
        "vehicle_class": "TWO_WHEELER",
        "fuel_type": "petrol",
        "max_speed": 85,
        "ces_range": (0.3, 0.5),
        "co2_base": 42, "co_base": 0.60, "nox_base": 0.015,
        "hc_base": 0.10, "pm25_base": 0.001,
        "fail_prob": 0.0,
        "fraud_range": (0.02, 0.12),
        "idle_rpm": 1400, "redline": 9000,
    },
    "MH09NP6789": {
        "display_name": "Ashok Leyland Viking (Bus)",
        "vehicle_class": "BUS",
        "fuel_type": "diesel",
        "max_speed": 90,
        "ces_range": (1.0, 1.8),
        "co2_base": 480, "co_base": 1.10, "nox_base": 0.55,
        "hc_base": 0.14, "pm25_base": 0.035,
        "fail_prob": 0.60,
        "fraud_range": (0.12, 0.40),
        "idle_rpm": 650, "redline": 2800,
    },
    "MH05QR0123": {
        "display_name": "Toyota Hyryder (Hybrid)",
        "vehicle_class": "SUV",
        "fuel_type": "hybrid_petrol",
        "max_speed": 130,
        "ces_range": (0.2, 0.4),
        "co2_base": 68, "co_base": 0.15, "nox_base": 0.012,
        "hc_base": 0.02, "pm25_base": 0.001,
        "fail_prob": 0.0,
        "fraud_range": (0.02, 0.10),
        "idle_rpm": 0, "redline": 5500,
    },
    "MH06ST4567": {
        "display_name": "Maruti Ertiga (MPV - CNG)",
        "vehicle_class": "SEDAN",
        "fuel_type": "cng",
        "max_speed": 120,
        "ces_range": (0.4, 0.65),
        "co2_base": 85, "co_base": 0.30, "nox_base": 0.025,
        "hc_base": 0.05, "pm25_base": 0.001,
        "fail_prob": 0.0,
        "fraud_range": (0.03, 0.14),
        "idle_rpm": 750, "redline": 6000,
    },
    "MH07UV8901": {
        "display_name": "Tata Nexon (LPG Retro)",
        "vehicle_class": "SUV",
        "fuel_type": "lpg",
        "max_speed": 125,
        "ces_range": (0.5, 0.8),
        "co2_base": 105, "co_base": 0.40, "nox_base": 0.030,
        "hc_base": 0.055, "pm25_base": 0.002,
        "fail_prob": 0.08,
        "fraud_range": (0.05, 0.18),
        "idle_rpm": 800, "redline": 6000,
    },
}

base_ts = 1712500000  # ~ April 2024

result = {
    "generated_at": "2026-04-07",
    "description": "Pre-computed emission histories for 10 demo vehicles "
                   "following realistic WLTC driving patterns.",
    "vehicles": {},
}

for vid, cfg in vehicles.items():
    readings = []
    ts = base_ts
    for i in range(50):
        phase = pick_phase()
        speed = speed_for_phase(phase, cfg["max_speed"])
        rpm = rpm_from_speed(
            speed, cfg["max_speed"], cfg["idle_rpm"], cfg["redline"]
        )

        # Speed factor: emissions scale with speed/load
        speed_factor = 0.6 + 0.8 * (speed / cfg["max_speed"])
        noise = random.uniform(0.85, 1.15)

        co2 = round(cfg["co2_base"] * speed_factor * noise, 1)
        co = round(cfg["co_base"] * speed_factor * random.uniform(0.7, 1.3), 3)
        nox = round(
            cfg["nox_base"] * speed_factor * random.uniform(0.6, 1.4), 4
        )
        hc = round(cfg["hc_base"] * speed_factor * random.uniform(0.7, 1.3), 4)
        pm25 = round(
            cfg["pm25_base"] * speed_factor * random.uniform(0.5, 1.5), 5
        )

        # Fuel rate correlates with speed and engine size
        fuel_rate = round(
            2.0
            + (speed / cfg["max_speed"])
            * 12.0
            * (cfg["co2_base"] / 120.0)
            * random.uniform(0.85, 1.15),
            1,
        )

        # CES score within vehicle range
        ces_lo, ces_hi = cfg["ces_range"]
        ces = round(random.uniform(ces_lo, ces_hi), 2)

        # Status
        if random.random() < cfg["fail_prob"]:
            status = "FAIL"
            # Bump CES higher for fails
            ces = round(max(ces, ces_hi * random.uniform(0.9, 1.1)), 2)
        else:
            status = "PASS"

        fraud = round(random.uniform(*cfg["fraud_range"]), 2)

        readings.append(
            {
                "timestamp": ts,
                "speed": speed,
                "rpm": rpm,
                "fuel_rate": fuel_rate,
                "co2_g_per_km": co2,
                "co_g_per_km": co,
                "nox_g_per_km": nox,
                "hc_g_per_km": hc,
                "pm25_g_per_km": pm25,
                "ces_score": ces,
                "status": status,
                "fraud_score": fraud,
                "wltc_phase": phase,
            }
        )
        ts += random.randint(30, 120)  # 30-120 seconds between readings

    result["vehicles"][vid] = {
        "display_name": cfg["display_name"],
        "vehicle_class": cfg["vehicle_class"],
        "fuel_type": cfg["fuel_type"],
        "readings": readings,
    }

out_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data",
    "demo_simulation_cache.json",
)
out_path = os.path.normpath(out_path)

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

print(f"Generated {len(result['vehicles'])} vehicles, 50 readings each")
print(f"Output: {out_path}")
for vid, v in result["vehicles"].items():
    ces_vals = [r["ces_score"] for r in v["readings"]]
    statuses = [r["status"] for r in v["readings"]]
    fails = statuses.count("FAIL")
    print(
        f"  {vid}: CES {min(ces_vals):.2f}-{max(ces_vals):.2f}, "
        f"FAIL={fails}/50, {v['display_name']}"
    )
