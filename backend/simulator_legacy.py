"""
Smart PUC — OBD-II Data Simulator
==================================
Simulates real-time vehicle telemetry data for petrol vehicles:
  - Engine RPM     : 600–4000
  - Speed           : 0–120 km/h
  - Fuel consumption: 3–15 L/100km

Three driving modes are simulated:
  - idle    : engine running, vehicle stationary
  - city    : moderate speed, variable RPM
  - highway : high speed, steady RPM

Configurable interval (default 5 seconds).
Exposes data via a callable function and optional REST endpoint (FR-01 to FR-04).
"""

import random
import time
import threading

# ──────────────────────────── Driving Mode Profiles ────────────────────────────

DRIVING_MODES = {
    "idle": {
        "rpm_range": (600, 900),
        "speed_range": (0, 5),
        "fuel_rate_range": (3.0, 5.0),   # L/100km (idling, low efficiency)
    },
    "city": {
        "rpm_range": (1000, 2800),
        "speed_range": (15, 60),
        "fuel_rate_range": (6.0, 10.0),  # L/100km
    },
    "highway": {
        "rpm_range": (2000, 4000),
        "speed_range": (60, 120),
        "fuel_rate_range": (5.0, 8.0),   # L/100km (more efficient at cruise)
    },
}

# Weighted random mode selection to mimic realistic driving patterns
MODE_WEIGHTS = {
    "idle": 0.15,
    "city": 0.55,
    "highway": 0.30,
}

# ──────────────────────────── Simulator Class ──────────────────────────────────

class OBDSimulator:
    """
    Simulates OBD-II telemetry for a petrol vehicle.
    """

    def __init__(self, vehicle_id="MH12AB1234", interval=5):
        """
        Args:
            vehicle_id: Vehicle registration number
            interval:   Data generation interval in seconds (default 5)
        """
        self.vehicle_id = vehicle_id
        self.interval = interval
        self._running = False
        self._thread = None
        self._latest_data = None

    def _select_mode(self):
        """Weighted random driving mode selection."""
        modes = list(MODE_WEIGHTS.keys())
        weights = list(MODE_WEIGHTS.values())
        return random.choices(modes, weights=weights, k=1)[0]

    def generate_reading(self):
        """
        Generate a single OBD-II telemetry reading.

        Returns:
            dict with keys: vehicle_id, rpm, speed, fuel_rate, mode, timestamp
        """
        mode = self._select_mode()
        profile = DRIVING_MODES[mode]

        rpm = random.randint(*profile["rpm_range"])
        speed = round(random.uniform(*profile["speed_range"]), 1)
        fuel_rate = round(random.uniform(*profile["fuel_rate_range"]), 2)

        reading = {
            "vehicle_id": self.vehicle_id,
            "rpm": rpm,
            "speed": speed,
            "fuel_rate": fuel_rate,       # L/100km
            "fuel_type": "petrol",
            "mode": mode,
            "timestamp": int(time.time()),
        }

        self._latest_data = reading
        return reading

    def get_latest(self):
        """Return the most recent reading (or generate one if none exists)."""
        if self._latest_data is None:
            return self.generate_reading()
        return self._latest_data

    def start_continuous(self, callback=None):
        """
        Start continuous data generation in a background thread.

        Args:
            callback: Optional function called with each new reading dict
        """
        if self._running:
            return

        self._running = True

        def _loop():
            while self._running:
                reading = self.generate_reading()
                if callback:
                    callback(reading)
                time.sleep(self.interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop continuous data generation."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self.interval + 1)


# ──────────────────────────── Standalone Test ──────────────────────────────────

if __name__ == "__main__":
    sim = OBDSimulator(vehicle_id="MH12AB1234", interval=2)
    print("🚗 OBD-II Simulator — Petrol Vehicle")
    print("=" * 50)

    for i in range(10):
        data = sim.generate_reading()
        print(
            f"[{i+1:02d}] Mode: {data['mode']:>8s} | "
            f"RPM: {data['rpm']:>4d} | "
            f"Speed: {data['speed']:>6.1f} km/h | "
            f"Fuel: {data['fuel_rate']:>5.2f} L/100km"
        )
        time.sleep(1)
