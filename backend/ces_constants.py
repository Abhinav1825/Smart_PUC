"""
Smart PUC — Generated CES Constants
===================================

**AUTO-GENERATED FILE. DO NOT EDIT BY HAND.**

Source of truth: ``config/ces_weights.json``
Generator      : ``scripts/gen_ces_consts.py``

To change any constant here, edit the JSON and re-run the generator:

    python scripts/gen_ces_consts.py

The generator also cross-checks the Solidity integer constants in
``contracts/EmissionRegistry.sol`` so the Python and on-chain sides
cannot silently drift.
"""

from __future__ import annotations

from typing import Dict

# ───────────────────────── CES weights (sum = 1.0) ─────────────────────────
CES_WEIGHTS: Dict[str, float] = {
    "co2": 0.35,
    "co": 0.15,
    "nox": 0.3,
    "hc": 0.12,
    "pm25": 0.08,
}
if abs(sum(CES_WEIGHTS.values()) - 1.0) >= 1e-9:
    raise ValueError("CES weights must sum to 1.0 (audit G6)")

# ───────────────────────── Compliance constants ────────────────────────────
CES_PASS_CEILING: float = 1.0
FRAUD_ALERT_THRESHOLD: float = 0.65
CONSECUTIVE_PASS_REQUIRED: int = 3

# ───────────────────────── BS-VI thresholds (g/km) ─────────────────────────
BSVI_THRESHOLDS_PETROL: Dict[str, float] = {
    "co2": 120.0,
    "co": 1.0,
    "nox": 0.06,
    "hc": 0.1,
    "pm25": 0.0045,
}

BSVI_THRESHOLDS_DIESEL: Dict[str, float] = {
    "co2": 120.0,
    "co": 0.5,
    "nox": 0.08,
    "hc": 0.09,
    "pm25": 0.0045,
}

# ───────────────────────── BS-IV thresholds (g/km) ─────────────────────────
BS4_THRESHOLDS_PETROL: Dict[str, float] = {
    "co2": 140.0,
    "co": 2.3,
    "nox": 0.15,
    "hc": 0.2,
    "pm25": 0.025,
}

BS4_THRESHOLDS_DIESEL: Dict[str, float] = {
    "co2": 140.0,
    "co": 0.5,
    "nox": 0.25,
    "hc": 0.05,
    "pm25": 0.025,
}
