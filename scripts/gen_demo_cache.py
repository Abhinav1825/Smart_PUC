#!/usr/bin/env python3
"""Generate pre-recorded simulation cache for 4 demo vehicle profiles."""
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
    # 1. Clean vehicle — all readings PASS, CES 0.4-0.7
    "MH12AB1234": {
        "display_name": "Maruti Ciaz (Sedan)",
        "vehicle_class": "SEDAN",
        "fuel_type": "petrol",
        "max_speed": 130,
        "ces_range": (0.4, 0.7),
        "co2_base": 90, "co_base": 0.30, "nox_base": 0.025,
        "hc_base": 0.04, "pm25_base": 0.002,
        "fail_prob": 0.0,
        "fraud_range": (0.03, 0.15),
        "idle_rpm": 750, "redline": 6500,
    },
    # 2. Degraded vehicle — some readings FAIL, CES 0.8-1.2, mix of PASS/FAIL
    "MH01CD5678": {
        "display_name": "Maruti WagonR (Hatchback)",
        "vehicle_class": "HATCHBACK",
        "fuel_type": "petrol",
        "max_speed": 120,
        "ces_range": (0.8, 1.2),
        "co2_base": 135, "co_base": 0.70, "nox_base": 0.055,
        "hc_base": 0.08, "pm25_base": 0.004,
        "fail_prob": 0.35,
        "fraud_range": (0.05, 0.20),
        "idle_rpm": 800, "redline": 6200,
    },
    # 3. Near-threshold diesel — CES hovering around 0.9-1.05
    "MH04EF9012": {
        "display_name": "Tata Nexon (Diesel)",
        "vehicle_class": "SUV",
        "fuel_type": "diesel",
        "max_speed": 130,
        "ces_range": (0.9, 1.05),
        "co2_base": 145, "co_base": 0.35, "nox_base": 0.058,
        "hc_base": 0.045, "pm25_base": 0.0042,
        "fail_prob": 0.25,
        "fraud_range": (0.06, 0.22),
        "idle_rpm": 800, "redline": 4500,
    },
    # 4. Fraud alert vehicle — 2-3 readings with elevated fraud scores (>0.5)
    "MH02GH3456": {
        "display_name": "Hyundai i20 (Hatchback)",
        "vehicle_class": "HATCHBACK",
        "fuel_type": "petrol",
        "max_speed": 125,
        "ces_range": (0.5, 0.85),
        "co2_base": 105, "co_base": 0.40, "nox_base": 0.032,
        "hc_base": 0.055, "pm25_base": 0.002,
        "fail_prob": 0.04,
        "fraud_range": (0.05, 0.18),
        "fraud_spike_count": 3,           # number of readings with elevated fraud
        "fraud_spike_range": (0.55, 0.85),  # fraud score range for spikes
        "idle_rpm": 800, "redline": 6500,
    },
}

base_ts = 1712500000  # ~ April 2024

result = {
    "generated_at": "2026-04-07",
    "description": "Pre-computed emission histories for 4 demo vehicle profiles "
                   "following realistic WLTC driving patterns.",
    "vehicles": {},
}

READINGS_PER_VEHICLE = 50

for vid, cfg in vehicles.items():
    readings = []
    ts = base_ts

    # Determine which reading indices get elevated fraud scores
    fraud_spike_indices = set()
    if cfg.get("fraud_spike_count"):
        fraud_spike_indices = set(
            random.sample(range(READINGS_PER_VEHICLE), cfg["fraud_spike_count"])
        )

    for i in range(READINGS_PER_VEHICLE):
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

        # Fraud score — elevated for spike indices
        if i in fraud_spike_indices:
            fraud = round(random.uniform(*cfg["fraud_spike_range"]), 2)
        else:
            fraud = round(random.uniform(*cfg["fraud_range"]), 2)

        readings.append(
            {
                "timestamp": ts,
                "vehicle_id": vid,
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

total = sum(len(v["readings"]) for v in result["vehicles"].values())
print(f"Generated {len(result['vehicles'])} vehicles, {READINGS_PER_VEHICLE} readings each ({total} total)")
print(f"Output: {out_path}")
for vid, v in result["vehicles"].items():
    ces_vals = [r["ces_score"] for r in v["readings"]]
    statuses = [r["status"] for r in v["readings"]]
    fraud_vals = [r["fraud_score"] for r in v["readings"]]
    fails = statuses.count("FAIL")
    high_fraud = sum(1 for f in fraud_vals if f > 0.5)
    print(
        f"  {vid}: CES {min(ces_vals):.2f}-{max(ces_vals):.2f}, "
        f"FAIL={fails}/{READINGS_PER_VEHICLE}, fraud>0.5={high_fraud}, "
        f"{v['display_name']}"
    )
