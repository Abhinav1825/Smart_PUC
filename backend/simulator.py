"""
Smart PUC -- WLTC Class 3b Driving-Cycle Simulator
====================================================
Generates second-by-second vehicle telemetry that follows the Worldwide
harmonized Light vehicles Test Cycle (WLTC) Class 3b speed profile as
defined in **UN ECE Regulation No. 154 (WLTP), Annex 1**.

The 1800-second cycle is divided into four phases:
    Low        (  0 -- 589 s) : urban driving,   0 -- 56.5  km/h
    Medium     (590 -- 1022 s): suburban driving, 0 -- 76.6  km/h
    High       (1023 -- 1477 s): rural driving,  0 -- 97.4  km/h
    Extra High (1478 -- 1800 s): motorway,       0 -- 131.3 km/h

A representative speed profile is reconstructed from ~100 key waypoints
using numpy cubic interpolation.  A 5-speed manual gearbox model
translates road speed into engine RPM, and fuel consumption is estimated
via a Vehicle Specific Power (VSP) approach.

References
----------
UNECE Regulation No. 154 -- WLTP, Annex 1, Sub-Annex 1,
Appendix 1: WLTC Class 3b driving cycle.
"""

from __future__ import annotations

import math
import time
import threading
from enum import Enum
from typing import Callable, Dict, List, Optional

import numpy as np

# ━━━━━━━━━━━━━━━━━━━━━ WLTC Phase Definitions ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class WLTCPhase(Enum):
    """WLTC Class 3b cycle phases."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    EXTRA_HIGH = "Extra High"


# Phase time boundaries (inclusive start, exclusive end except last)
_PHASE_BOUNDS = [
    (0, 590, WLTCPhase.LOW),
    (590, 1023, WLTCPhase.MEDIUM),
    (1023, 1478, WLTCPhase.HIGH),
    (1478, 1801, WLTCPhase.EXTRA_HIGH),
]

# ━━━━━━━━━━━━━━━━━━━━━ 5-Speed Manual Gearbox Model ━━━━━━━━━━━━━━━━━━━━━━━

GEAR_RATIOS: List[float] = [3.545, 1.904, 1.233, 0.885, 0.694]
FINAL_DRIVE: float = 4.058
TIRE_RADIUS: float = 0.3  # metres

# Speed-band gear selection heuristic (km/h thresholds)
_GEAR_SPEED_BANDS: List[tuple[float, int]] = [
    (15.0, 1),   # 0 -- 15 km/h  -> gear 1
    (30.0, 2),   # 15 -- 30 km/h -> gear 2
    (50.0, 3),   # 30 -- 50 km/h -> gear 3
    (80.0, 4),   # 50 -- 80 km/h -> gear 4
]
_DEFAULT_GEAR: int = 5  # > 80 km/h


def select_gear(speed_kmh: float) -> int:
    """Return the appropriate gear (1--5) for a given road speed.

    Parameters
    ----------
    speed_kmh : float
        Vehicle speed in km/h.

    Returns
    -------
    int
        Gear number (1 to 5).
    """
    if speed_kmh < 1.0:
        return 1  # clutch in / idle -- nominal first gear
    for threshold, gear in _GEAR_SPEED_BANDS:
        if speed_kmh <= threshold:
            return gear
    return _DEFAULT_GEAR


def calculate_rpm_from_speed(speed_kmh: float) -> int:
    """Compute engine RPM for *speed_kmh* using the gearbox model.

    Formula
    -------
    speed_mps = speed_kmh / 3.6
    RPM = (speed_mps x gear_ratio x final_drive) / (2 pi x tire_radius) x 60

    The result is clamped to [700, 6500].

    Parameters
    ----------
    speed_kmh : float
        Vehicle speed in km/h.

    Returns
    -------
    int
        Engine RPM (clamped 700 -- 6500).
    """
    if speed_kmh < 1.0:
        return 700  # idle RPM

    gear = select_gear(speed_kmh)
    gear_ratio = GEAR_RATIOS[gear - 1]
    speed_mps = speed_kmh / 3.6

    rpm = (speed_mps * gear_ratio * FINAL_DRIVE) / (2.0 * math.pi * TIRE_RADIUS) * 60.0
    return int(np.clip(rpm, 700, 6500))


# ━━━━━━━━━━━━━━━━━━━━━ VSP-Based Fuel Rate Estimation ━━━━━━━━━━━━━━━━━━━━━


def _estimate_fuel_rate(speed_kmh: float, acceleration_mps2: float) -> float:
    """Estimate instantaneous fuel consumption using a simplified VSP model.

    Vehicle Specific Power (kW/tonne):
        VSP = v * (1.1 * a + 9.81 * sin(grade) + 0.132) + 0.000302 * v^3

    We assume grade = 0.  Fuel rate in L/100 km is derived from VSP via a
    piecewise linear mapping calibrated to typical petrol engines.

    Parameters
    ----------
    speed_kmh : float
        Vehicle speed in km/h.
    acceleration_mps2 : float
        Longitudinal acceleration in m/s^2.

    Returns
    -------
    float
        Fuel consumption in L/100 km (>= 0.0).
    """
    if speed_kmh < 1.0:
        # Idle fuel consumption
        return 1.5  # L/100km equivalent (idle baseline)

    v = speed_kmh / 3.6  # m/s
    vsp = v * (1.1 * acceleration_mps2 + 9.81 * 0.0 + 0.132) + 0.000302 * v ** 3

    # Map VSP (kW/tonne) to fuel rate (L/100km)
    if vsp < 0:
        fuel_rate = 1.0  # deceleration / engine braking
    elif vsp < 5:
        fuel_rate = 3.0 + vsp * 0.6
    elif vsp < 15:
        fuel_rate = 6.0 + (vsp - 5) * 0.5
    elif vsp < 25:
        fuel_rate = 11.0 + (vsp - 15) * 0.4
    else:
        fuel_rate = 15.0 + (vsp - 25) * 0.3

    return round(max(fuel_rate, 0.5), 2)


# ━━━━━━━━━━━━━━━━━━━ WLTC Class 3b Speed Profile Generator ━━━━━━━━━━━━━━━━


def _generate_wltc_profile() -> np.ndarray:
    """Generate a 1800-point speed array (km/h) approximating the WLTC Class 3b cycle.

    The profile is built from ~100 key waypoints that capture the
    characteristic shape of each phase (idle periods, ramps, plateaus,
    decelerations) and interpolated with numpy to produce the full
    second-by-second trace.

    Returns
    -------
    np.ndarray
        1-D array of length 1800, speed in km/h at each second.
    """
    # fmt: off
    # Waypoints: (time_s, speed_km/h)
    # ── LOW PHASE (0 -- 589 s) ──────────────────────────────────────────
    waypoints = [
        # Initial idle
        (0, 0.0), (10, 0.0),
        # First urban acceleration
        (23, 20.0), (35, 30.0), (50, 45.0), (60, 50.0),
        # Cruise and deceleration
        (80, 48.0), (95, 35.0), (110, 0.0),
        # Idle
        (125, 0.0),
        # Second urban segment
        (140, 18.0), (155, 32.0), (170, 40.0), (185, 46.0),
        (200, 45.0), (215, 30.0), (230, 0.0),
        # Idle
        (245, 0.0),
        # Third urban segment
        (260, 15.0), (275, 28.0), (295, 42.0), (315, 50.0),
        (335, 56.5), (350, 54.0), (370, 44.0), (385, 30.0),
        (400, 15.0), (415, 0.0),
        # Idle
        (430, 0.0),
        # Fourth urban segment
        (445, 12.0), (460, 25.0), (480, 38.0), (500, 45.0),
        (515, 42.0), (530, 32.0), (545, 20.0), (560, 10.0),
        (575, 0.0),
        # Final idle of LOW phase
        (589, 0.0),

        # ── MEDIUM PHASE (590 -- 1022 s) ────────────────────────────────
        (590, 0.0), (600, 0.0),
        # Suburban acceleration
        (615, 22.0), (630, 40.0), (650, 58.0), (665, 68.0),
        (680, 76.6), (700, 74.0), (715, 65.0),
        # Moderate cruise
        (735, 50.0), (750, 45.0), (765, 55.0), (780, 65.0),
        (800, 72.0), (815, 68.0), (830, 55.0), (845, 38.0),
        (860, 20.0), (875, 0.0),
        # Idle
        (890, 0.0),
        # Second suburban segment
        (905, 18.0), (920, 35.0), (935, 52.0), (950, 64.0),
        (965, 70.0), (975, 66.0), (990, 50.0), (1005, 30.0),
        (1015, 12.0), (1022, 0.0),

        # ── HIGH PHASE (1023 -- 1477 s) ─────────────────────────────────
        (1023, 0.0), (1035, 0.0),
        # Rural acceleration
        (1055, 30.0), (1075, 55.0), (1090, 72.0), (1105, 85.0),
        (1120, 92.0), (1140, 97.4), (1155, 95.0),
        # Cruise and variation
        (1175, 85.0), (1195, 78.0), (1210, 70.0), (1225, 80.0),
        (1245, 90.0), (1260, 95.0), (1275, 88.0), (1295, 75.0),
        (1310, 60.0), (1325, 45.0), (1340, 30.0), (1355, 15.0),
        (1370, 0.0),
        # Idle
        (1385, 0.0),
        # Short rural burst
        (1400, 25.0), (1420, 55.0), (1435, 75.0), (1450, 82.0),
        (1462, 70.0), (1472, 40.0), (1477, 0.0),

        # ── EXTRA HIGH PHASE (1478 -- 1800 s) ──────────────────────────
        (1478, 0.0), (1490, 0.0),
        # Motorway acceleration
        (1510, 35.0), (1530, 65.0), (1550, 90.0), (1565, 110.0),
        (1580, 125.0), (1595, 131.3),
        # High-speed cruise
        (1615, 130.0), (1635, 128.0), (1650, 131.0), (1665, 125.0),
        (1680, 118.0), (1700, 110.0),
        # Deceleration to moderate speed
        (1720, 95.0), (1735, 80.0), (1748, 70.0), (1760, 85.0),
        (1775, 100.0),
        # Final deceleration to stop
        (1788, 80.0), (1795, 50.0), (1798, 20.0), (1800, 0.0),
    ]
    # fmt: on

    times = np.array([w[0] for w in waypoints], dtype=np.float64)
    speeds = np.array([w[1] for w in waypoints], dtype=np.float64)

    # Interpolate to 1 Hz (seconds 0 .. 1799 inclusive)
    full_time = np.arange(0, 1800, dtype=np.float64)
    profile = np.interp(full_time, times, speeds)

    # Clamp: no negative speeds
    profile = np.clip(profile, 0.0, None)

    return profile


# Module-level cached profile (generated once)
_WLTC_SPEED_PROFILE: np.ndarray = _generate_wltc_profile()

# ━━━━━━━━━━━━━━━━━━━━━━━━ WLTC Simulator Class ━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class WLTCSimulator:
    """Second-by-second WLTC Class 3b driving-cycle simulator.

    Each call to :meth:`generate_reading` advances the internal clock by
    *dt* seconds and returns a telemetry dict containing speed, RPM,
    acceleration, fuel rate, phase label, and timestamp.

    Parameters
    ----------
    vehicle_id : str
        Vehicle registration number (default ``"MH12AB1234"``).
    dt : float
        Timestep in seconds (default ``1.0``).
    """

    def __init__(self, vehicle_id: str = "MH12AB1234", dt: float = 1.0, **kwargs) -> None:
        """Initialise the WLTC simulator.

        Parameters
        ----------
        vehicle_id : str
            Vehicle registration / identification string.
        dt : float
            Time step between consecutive readings in seconds.
        **kwargs
            Accepted for backward compatibility (e.g. ``interval``).
        """
        self.vehicle_id: str = vehicle_id
        self.dt: float = dt
        self._current_time: int = 0
        self._latest_data: Optional[Dict] = None
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None

        # Accept legacy `interval` kwarg for backward compat
        if "interval" in kwargs:
            self.dt = float(kwargs["interval"])

    # ── Phase helper ──────────────────────────────────────────────────────

    def get_phase(self, time_s: int) -> WLTCPhase:
        """Return the WLTC phase for a given cycle time.

        Parameters
        ----------
        time_s : int
            Position in seconds within the 1800 s cycle (0-based).

        Returns
        -------
        WLTCPhase
            The phase enum value.
        """
        t = time_s % 1800
        for start, end, phase in _PHASE_BOUNDS:
            if start <= t < end:
                return phase
        return WLTCPhase.EXTRA_HIGH  # fallback (should not reach)

    # ── RPM calculation ───────────────────────────────────────────────────

    def calculate_rpm(self, speed_kmh: float) -> int:
        """Calculate engine RPM for the given speed using the gearbox model.

        RPM = (speed_mps x gear_ratio x final_drive) / (2 pi x tire_radius) x 60
        Clamped to [700, 6500].

        Parameters
        ----------
        speed_kmh : float
            Vehicle speed in km/h.

        Returns
        -------
        int
            Engine RPM.
        """
        return calculate_rpm_from_speed(speed_kmh)

    # ── Core telemetry generation ─────────────────────────────────────────

    def generate_reading(self) -> Dict:
        """Generate the **next** telemetry reading and advance the cycle clock.

        Acceleration is computed via central difference on the speed
        profile.  Fuel rate is derived using a simplified VSP model.

        Returns
        -------
        dict
            Keys: ``vehicle_id``, ``speed``, ``acceleration``, ``rpm``,
            ``fuel_rate``, ``fuel_type``, ``phase``, ``time_in_cycle``,
            ``timestamp``.
        """
        profile = _WLTC_SPEED_PROFILE
        total = len(profile)  # 1800

        idx = self._current_time % total
        speed = float(profile[idx])

        # Central-difference acceleration (m/s^2)
        idx_prev = (idx - 1) % total
        idx_next = (idx + 1) % total
        accel = (profile[idx_next] - profile[idx_prev]) / (2.0 * self.dt)
        accel_mps2 = accel / 3.6  # km/h/s -> m/s^2

        rpm = self.calculate_rpm(speed)
        phase = self.get_phase(idx)
        fuel_rate = _estimate_fuel_rate(speed, accel_mps2)

        reading: Dict = {
            "vehicle_id": self.vehicle_id,
            "speed": round(speed, 1),
            "acceleration": round(accel_mps2, 3),
            "rpm": rpm,
            "fuel_rate": fuel_rate,
            "fuel_type": "petrol",
            "phase": phase.value,
            "time_in_cycle": idx,
            "timestamp": int(time.time()),
        }

        self._latest_data = reading
        self._current_time += max(1, int(self.dt))

        return reading

    # ── Backward-compatible convenience methods ───────────────────────────

    def get_latest(self) -> Dict:
        """Return the most recent reading, generating one if none exists.

        Returns
        -------
        dict
            The latest telemetry reading.
        """
        if self._latest_data is None:
            return self.generate_reading()
        return self._latest_data

    def start_continuous(self, callback: Optional[Callable] = None) -> None:
        """Start continuous data generation in a background thread.

        Parameters
        ----------
        callback : callable, optional
            Function called with each new reading dict.
        """
        if self._running:
            return

        self._running = True

        def _loop() -> None:
            while self._running:
                reading = self.generate_reading()
                if callback is not None:
                    callback(reading)
                time.sleep(self.dt)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop continuous data generation."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self.dt + 1)
            self._thread = None

    def reset(self) -> None:
        """Reset the simulator to the beginning of the cycle (time 0)."""
        self.stop()
        self._current_time = 0
        self._latest_data = None


# ━━━━━━━━━━━━━━━━━━━ Backward-Compatible Alias ━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class OBDSimulator(WLTCSimulator):
    """Legacy alias wrapping :class:`WLTCSimulator`.

    Accepts the same constructor arguments as the original ``OBDSimulator``
    (including ``interval``) so that existing call-sites continue to work
    unchanged.

    Parameters
    ----------
    vehicle_id : str
        Vehicle registration number.
    interval : float
        Data generation interval in seconds (maps to ``dt``).
    **kwargs
        Forwarded to :class:`WLTCSimulator`.
    """

    def __init__(self, vehicle_id: str = "MH12AB1234", interval: float = 5, **kwargs) -> None:
        super().__init__(vehicle_id=vehicle_id, dt=interval, **kwargs)


# ━━━━━━━━━━━━━━━━━━━━━ Standalone Test ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    sim = WLTCSimulator(vehicle_id="MH12AB1234", dt=1.0)
    print("WLTC Class 3b Simulator -- Sample Readings")
    print("=" * 80)

    # Print a reading every 30 seconds of cycle time
    for _ in range(60):
        data = sim.generate_reading()
        # Advance by 29 more seconds silently
        for __ in range(29):
            sim.generate_reading()
        print(
            f"  t={data['time_in_cycle']:>5d}s | "
            f"Phase: {data['phase']:>10s} | "
            f"Speed: {data['speed']:>6.1f} km/h | "
            f"RPM: {data['rpm']:>5d} | "
            f"Accel: {data['acceleration']:>+6.3f} m/s2 | "
            f"Fuel: {data['fuel_rate']:>5.2f} L/100km"
        )
