"""
Statistical Detection Power for Continuous Emission Monitoring
==============================================================

This module proves that continuous OBD monitoring provides strictly
superior detection power compared to periodic tailpipe testing.

**Theorem 1 (Cumulative Detection Power):**
    For per-reading detection probability p and N independent readings,
    the cumulative detection power is:

        P_detect(N, p) = 1 - (1 - p)^N

    This exceeds the single-test detection power P_puc when:

        N > log(1 - P_puc) / log(1 - p)

    For p = 0.02 (conservative OBD per-reading sensitivity) and
    P_puc = 0.85 (typical tailpipe test sensitivity), N_threshold = 94
    readings, which equals approximately 94 seconds (< 2 minutes) of driving.

References:
    - Wald, A. (1945). Sequential Analysis. Wiley.
    - California BAR-OIS Technical Report (2018): OBD-based inspection
      catches 95% of gross emitters detected by tailpipe testing.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


def cumulative_detection_power(n_readings: int, p_per_reading: float) -> float:
    """Compute P_detect = 1 - (1-p)^N.

    Args:
        n_readings: Number of independent OBD readings
        p_per_reading: Per-reading detection probability (0 < p < 1)

    Returns:
        Cumulative detection probability [0, 1]
    """
    if n_readings <= 0:
        return 0.0
    if p_per_reading >= 1.0:
        return 1.0
    if p_per_reading <= 0.0:
        return 0.0
    return 1.0 - (1.0 - p_per_reading) ** n_readings


def readings_threshold(p_target: float, p_per_reading: float) -> int:
    """Minimum readings needed to reach target detection power.

    N = ceil(log(1 - P_target) / log(1 - p))

    Args:
        p_target: Target cumulative detection probability (e.g., 0.85)
        p_per_reading: Per-reading detection probability

    Returns:
        Minimum N (integer)
    """
    if p_per_reading <= 0.0:
        raise ValueError("p_per_reading must be > 0")
    if p_per_reading >= 1.0:
        return 1
    if p_target <= 0.0:
        return 0
    if p_target >= 1.0:
        raise ValueError("Cannot reach p_target=1.0 exactly with p < 1")
    return math.ceil(math.log(1.0 - p_target) / math.log(1.0 - p_per_reading))


def detection_power_comparison_table(
    p_per_reading: float = 0.02,
    p_puc_single: float = 0.85,
    durations_minutes: Optional[List[float]] = None,
    readings_per_minute: float = 60.0,
) -> List[Dict]:
    """Generate a comparison table: minutes of driving -> detection power.

    Returns list of dicts:
    [
        {"minutes": 1, "readings": 60, "p_obd": 0.70, "p_puc": 0.85,
         "obd_better": False, "advantage_pct": -17.6},
        {"minutes": 2, "readings": 120, "p_obd": 0.91, "p_puc": 0.85,
         "obd_better": True, "advantage_pct": 7.1},
        ...
    ]
    """
    if durations_minutes is None:
        durations_minutes = [1, 2, 5, 10, 30, 60, 120, 480, 1440]

    table: List[Dict] = []
    for mins in durations_minutes:
        n = int(mins * readings_per_minute)
        p_obd = cumulative_detection_power(n, p_per_reading)
        advantage_pct = ((p_obd - p_puc_single) / p_puc_single) * 100.0
        table.append({
            "minutes": mins,
            "readings": n,
            "p_obd": round(p_obd, 4),
            "p_puc": p_puc_single,
            "obd_better": p_obd > p_puc_single,
            "advantage_pct": round(advantage_pct, 1),
        })
    return table


def time_to_equivalence(
    p_per_reading: float = 0.02,
    p_puc: float = 0.85,
    readings_per_second: float = 1.0,
) -> Dict:
    """How many seconds/minutes of driving until OBD matches PUC power.

    Returns:
        {"n_readings": int, "seconds": float, "minutes": float,
         "p_obd_at_threshold": float, "p_puc": float}
    """
    n = readings_threshold(p_puc, p_per_reading)
    seconds = n / readings_per_second
    return {
        "n_readings": n,
        "seconds": seconds,
        "minutes": round(seconds / 60.0, 2),
        "p_obd_at_threshold": round(cumulative_detection_power(n, p_per_reading), 6),
        "p_puc": p_puc,
    }


def monthly_detection_power(
    trips_per_day: int = 2,
    avg_trip_minutes: float = 30,
    readings_per_minute: float = 60,
    p_per_reading: float = 0.02,
    days: int = 30,
) -> Dict:
    """Detection power accumulated over a month of normal driving.

    Shows that even with conservative p=0.02, one month of driving
    gives P_detect ~ 1.0 (practically certain).

    Returns:
        {"total_readings": int, "p_detect": float, "p_detect_formatted": str,
         "trips": int, "driving_hours": float}
    """
    trips = trips_per_day * days
    total_readings = int(trips * avg_trip_minutes * readings_per_minute)
    p_detect = cumulative_detection_power(total_readings, p_per_reading)
    driving_hours = (trips * avg_trip_minutes) / 60.0
    return {
        "total_readings": total_readings,
        "p_detect": p_detect,
        "p_detect_formatted": f"{p_detect:.10f}" if p_detect < 1.0 else "1.0 (practically certain)",
        "trips": trips,
        "driving_hours": driving_hours,
    }


if __name__ == "__main__":
    print("=" * 70)
    print("Theorem 1: Cumulative Detection Power for Continuous OBD Monitoring")
    print("=" * 70)
    print()

    p = 0.02
    p_puc = 0.85

    equiv = time_to_equivalence(p, p_puc)
    print(f"Per-reading detection probability (p):  {p}")
    print(f"PUC single-test sensitivity (P_puc):    {p_puc}")
    print(f"Readings to match PUC (N_threshold):    {equiv['n_readings']}")
    print(f"Time to match PUC:                      {equiv['seconds']:.0f} seconds "
          f"({equiv['minutes']:.2f} minutes)")
    print(f"P_obd at threshold:                     {equiv['p_obd_at_threshold']}")
    print()

    print("-" * 70)
    print("Detection Power Comparison Table")
    print("-" * 70)
    print(f"{'Minutes':>8}  {'Readings':>8}  {'P_obd':>8}  {'P_puc':>8}  "
          f"{'Better?':>8}  {'Advantage':>10}")
    print("-" * 70)
    for row in detection_power_comparison_table(p, p_puc):
        print(f"{row['minutes']:>8.0f}  {row['readings']:>8d}  {row['p_obd']:>8.4f}  "
              f"{row['p_puc']:>8.2f}  {'YES' if row['obd_better'] else 'no':>8}  "
              f"{row['advantage_pct']:>+9.1f}%")
    print()

    monthly = monthly_detection_power()
    print("-" * 70)
    print("Monthly Detection Power (conservative estimate)")
    print("-" * 70)
    print(f"Trips/day: 2, Avg trip: 30 min, Days: 30")
    print(f"Total readings:   {monthly['total_readings']:,}")
    print(f"Total trips:      {monthly['trips']}")
    print(f"Driving hours:    {monthly['driving_hours']:.0f}")
    print(f"P_detect:         {monthly['p_detect_formatted']}")
    print()
