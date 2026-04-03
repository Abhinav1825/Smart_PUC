"""
Generate synthetic training data for the LSTM emission predictor.

Runs the full SmartPUC pipeline (WLTC simulator -> VSP -> emission engine)
over multiple WLTC cycles to produce a labelled dataset suitable for
training the ``EmissionPredictor`` model.

Usage::

    python -m ml.generate_training_data --cycles 3 --output ml/training_data.npy

The output is a NumPy ``.npy`` file containing an array of shape
``(N, 8)`` where each row corresponds to one second of the WLTC cycle
with columns: speed, rpm, fuel_rate, acceleration, co2, nox, vsp, ces_score.
"""

from __future__ import annotations

import argparse
import sys
import os
from typing import Dict, List

import numpy as np

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.simulator import WLTCSimulator
from backend.emission_engine import calculate_emissions

try:
    from physics.vsp_model import calculate_vsp, get_operating_mode_bin
    _HAS_VSP = True
except ImportError:
    _HAS_VSP = False


FEATURE_NAMES = ["speed", "rpm", "fuel_rate", "acceleration", "co2", "nox", "vsp", "ces_score"]


def generate_cycle_data(cycle_id: int = 0, ambient_temp: float = 25.0) -> List[Dict[str, float]]:
    """Run one full WLTC cycle and return a list of feature dicts.

    Parameters
    ----------
    cycle_id : int
        Cycle index (used for slight parameter variation).
    ambient_temp : float
        Ambient temperature in Celsius.

    Returns
    -------
    list[dict[str, float]]
        One dict per second, with keys matching ``FEATURE_NAMES``.
    """
    sim = WLTCSimulator(vehicle_id=f"TRAIN{cycle_id:04d}", dt=1.0)
    records: List[Dict[str, float]] = []

    # Add slight temperature variation across cycles
    temp = ambient_temp + (cycle_id % 5 - 2) * 3.0  # 19-31 C range

    for t in range(1800):
        reading = sim.generate_reading()
        speed = reading["speed"]
        rpm = reading["rpm"]
        fuel_rate = reading["fuel_rate"]
        accel = reading.get("acceleration", 0.0)

        speed_mps = speed / 3.6
        vsp_val = 0.0
        op_bin = 11
        if _HAS_VSP:
            vsp_val = calculate_vsp(speed_mps, accel)
            op_bin = get_operating_mode_bin(vsp_val, speed_mps)

        cold_start = t < 120
        emission = calculate_emissions(
            speed_kmh=speed,
            acceleration=accel,
            rpm=rpm,
            fuel_rate=fuel_rate,
            operating_mode_bin=op_bin,
            ambient_temp=temp,
            cold_start=cold_start,
        )

        records.append({
            "speed": speed,
            "rpm": float(rpm),
            "fuel_rate": fuel_rate,
            "acceleration": accel,
            "co2": emission["co2_g_per_km"],
            "nox": emission["nox_g_per_km"],
            "vsp": round(vsp_val, 3),
            "ces_score": emission["ces_score"],
        })

    return records


def generate_dataset(n_cycles: int = 3, ambient_temp: float = 25.0) -> np.ndarray:
    """Generate a full training dataset from multiple WLTC cycles.

    Parameters
    ----------
    n_cycles : int
        Number of WLTC cycles to simulate (default 3).
    ambient_temp : float
        Base ambient temperature in Celsius.

    Returns
    -------
    np.ndarray
        Array of shape ``(n_cycles * 1800, 8)`` with feature columns.
    """
    all_records: List[Dict[str, float]] = []

    for cycle in range(n_cycles):
        print(f"  Generating cycle {cycle + 1}/{n_cycles}...")
        records = generate_cycle_data(cycle_id=cycle, ambient_temp=ambient_temp)
        all_records.extend(records)

    # Convert to numpy array
    data = np.array(
        [[r[f] for f in FEATURE_NAMES] for r in all_records],
        dtype=np.float64,
    )
    return data


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate LSTM training data")
    parser.add_argument("--cycles", type=int, default=3, help="Number of WLTC cycles")
    parser.add_argument("--output", type=str, default="ml/training_data.npy", help="Output file path")
    parser.add_argument("--temp", type=float, default=25.0, help="Base ambient temperature (C)")
    args = parser.parse_args()

    print(f"Generating {args.cycles} WLTC cycles of training data...")
    data = generate_dataset(n_cycles=args.cycles, ambient_temp=args.temp)
    print(f"Dataset shape: {data.shape}")
    print(f"Features: {FEATURE_NAMES}")

    output_path = os.path.join(os.path.dirname(__file__), "..", args.output)
    np.save(output_path, data)
    print(f"Saved to {output_path}")

    # Print summary statistics
    print("\nFeature statistics:")
    for i, name in enumerate(FEATURE_NAMES):
        col = data[:, i]
        print(f"  {name:>14s}: mean={np.mean(col):>10.4f}  std={np.std(col):>10.4f}  "
              f"min={np.min(col):>10.4f}  max={np.max(col):>10.4f}")


if __name__ == "__main__":
    main()
