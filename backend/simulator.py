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
using numpy linear interpolation.  A 5-speed manual gearbox model
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

# Import the physics VSP module for consistent calculations
try:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from physics.vsp_model import calculate_vsp as _physics_vsp
    from physics.vsp_model import estimate_fuel_rate as _physics_fuel_rate
    _HAS_PHYSICS_MODULE = True
except ImportError:
    _HAS_PHYSICS_MODULE = False


def _estimate_fuel_rate(speed_kmh: float, acceleration_mps2: float) -> float:
    """Estimate instantaneous fuel consumption using the VSP physics model.

    When the ``physics.vsp_model`` module is available, delegates to the
    Rakha polynomial model (``physics.vsp_model.estimate_fuel_rate``).
    Otherwise falls back to an inline VSP-based piecewise linear mapping.

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

    References
    ----------
    Rakha, H., Ahn, K., and Trani, A., 2004 — polynomial fuel model.
    """
    if speed_kmh < 1.0:
        return 1.5  # Idle fuel consumption baseline (L/100km)

    v_mps = speed_kmh / 3.6

    if _HAS_PHYSICS_MODULE:
        vsp = _physics_vsp(v_mps, acceleration_mps2)
        rate = _physics_fuel_rate(vsp, v_mps)
        # _physics_fuel_rate returns 0.0 at very low speed; use idle baseline
        return round(max(rate, 1.0), 2)

    # Fallback: inline simplified VSP model
    vsp = v_mps * (1.1 * acceleration_mps2 + 9.81 * 0.0 + 0.132) + 0.000302 * v_mps ** 3

    if vsp < 0:
        fuel_rate = 1.0
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

    The profile is built from key waypoints that capture the characteristic
    shape of each phase (idle periods, ramps, plateaus, decelerations) and
    linearly interpolated with numpy to produce the full second-by-second
    trace.  Waypoints are tuned to produce a total cycle distance of
    approximately 23.27 km, matching the official UN ECE R154 specification.

    Returns
    -------
    np.ndarray
        1-D array of length 1800, speed in km/h at each second.
    """
    # fmt: off
    # Waypoints: (time_s, speed_km/h)
    # Target total distance: 23.27 km (official WLTC Class 3b)
    # Target idle fraction: ~13% (~234 seconds)
    #
    # ── LOW PHASE (0 -- 589 s) ── approx 3.09 km ─────────────────────
    waypoints = [
        # Initial idle (long)
        (0, 0.0), (15, 0.0),
        # First urban micro-trip
        (28, 18.0), (40, 28.0), (52, 38.0), (62, 46.0),
        (72, 44.0), (82, 35.0), (95, 15.0), (105, 0.0),
        # Idle
        (120, 0.0),
        # Second urban micro-trip
        (133, 15.0), (148, 30.0), (160, 38.0), (172, 42.0),
        (182, 40.0), (192, 28.0), (205, 10.0), (215, 0.0),
        # Idle (extended)
        (240, 0.0),
        # Third urban micro-trip — peak at 56.5
        (255, 12.0), (270, 25.0), (285, 38.0), (300, 48.0),
        (315, 56.5), (325, 54.0), (340, 45.0), (355, 32.0),
        (370, 18.0), (385, 0.0),
        # Idle
        (405, 0.0),
        # Fourth urban micro-trip
        (418, 10.0), (432, 22.0), (450, 35.0), (462, 42.0),
        (475, 38.0), (488, 28.0), (500, 16.0), (512, 0.0),
        # Idle
        (530, 0.0),
        # Fifth short burst
        (542, 8.0), (555, 18.0), (565, 25.0), (572, 18.0),
        (580, 8.0), (585, 0.0),
        # Final idle
        (589, 0.0),

        # ── MEDIUM PHASE (590 -- 1022 s) ── approx 4.76 km ───────────
        (590, 0.0), (605, 0.0),
        # Suburban acceleration
        (620, 20.0), (635, 38.0), (650, 55.0), (665, 68.0),
        (678, 76.6), (695, 72.0), (710, 62.0),
        # Moderate cruise
        (730, 48.0), (742, 42.0), (756, 50.0), (772, 60.0),
        (790, 68.0), (805, 64.0), (820, 50.0), (835, 35.0),
        (850, 18.0), (862, 0.0),
        # Idle
        (880, 0.0),
        # Second suburban segment
        (895, 15.0), (910, 32.0), (925, 48.0), (940, 60.0),
        (955, 66.0), (965, 62.0), (978, 48.0), (992, 28.0),
        (1005, 12.0), (1015, 0.0),
        # Final idle
        (1022, 0.0),

        # ── HIGH PHASE (1023 -- 1477 s) ── approx 7.16 km ────────────
        (1023, 0.0), (1038, 0.0),
        # Rural acceleration
        (1058, 28.0), (1078, 52.0), (1092, 68.0), (1108, 82.0),
        (1122, 90.0), (1138, 97.4), (1152, 94.0),
        # Cruise and variation
        (1172, 82.0), (1190, 74.0), (1205, 66.0), (1220, 75.0),
        (1240, 85.0), (1255, 92.0), (1270, 84.0), (1288, 70.0),
        (1305, 55.0), (1320, 40.0), (1335, 25.0), (1348, 12.0),
        (1360, 0.0),
        # Idle
        (1378, 0.0),
        # Short rural burst
        (1395, 22.0), (1412, 50.0), (1428, 72.0), (1442, 80.0),
        (1455, 65.0), (1468, 35.0), (1477, 0.0),

        # ── EXTRA HIGH PHASE (1478 -- 1800 s) ── approx 8.25 km ──────
        (1478, 0.0), (1492, 0.0),
        # Motorway acceleration
        (1512, 32.0), (1530, 60.0), (1548, 88.0), (1562, 108.0),
        (1578, 124.0), (1592, 131.3),
        # High-speed cruise
        (1612, 128.0), (1630, 124.0), (1648, 131.0), (1662, 122.0),
        (1678, 114.0), (1695, 106.0),
        # Deceleration to moderate speed
        (1715, 92.0), (1730, 78.0), (1744, 68.0), (1756, 80.0),
        (1770, 95.0),
        # Final deceleration to stop
        (1785, 75.0), (1793, 45.0), (1798, 18.0), (1800, 0.0),
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
