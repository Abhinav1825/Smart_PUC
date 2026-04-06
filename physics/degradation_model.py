"""COPERT 5 emission degradation model.

Models how vehicle emissions deteriorate with mileage and age,
using published deterioration rates from NAEI/EEA Guidebook 2023.

The core equation is linear deterioration:

    EF(km) = EF_base * (1 + rate_per_km * min(mileage_km, cap_km))

where ``rate_per_km`` is a fractional increase per kilometre and ``cap_km``
is the mileage beyond which no further degradation is assumed (the
aftertreatment has fully deteriorated to its worst steady-state).

Sudden failures (catalyst removal, DPF removal, EGR failure, etc.) are
modelled as multiplicative step-changes applied on top of the gradual
degradation.

References
----------
[1] NAEI, "Emission Degradation Methodology", National Atmospheric
    Emissions Inventory, UK DESNZ, 2024.
[2] Emisia SA, "COPERT 5 v5.6 — Computer Programme to Calculate
    Emissions from Road Transport", 2023.
[3] EEA, "EMEP/EEA Air Pollutant Emission Inventory Guidebook 2023,
    Chapter 1.A.3.b: Road Transport", European Environment Agency.
"""

from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional

# Pollutant keys used in the rates JSON and in emission dicts
_POLLUTANTS = ("co2", "co", "nox", "hc", "pm25")

# Mapping from emission-engine dict keys (e.g. "co2_g_per_km") to short
# pollutant names used in the degradation-rates JSON.
_KEY_TO_POLLUTANT = {
    "co2_g_per_km": "co2",
    "co_g_per_km": "co",
    "nox_g_per_km": "nox",
    "hc_g_per_km": "hc",
    "pm25_g_per_km": "pm25",
}

_POLLUTANT_TO_KEY = {v: k for k, v in _KEY_TO_POLLUTANT.items()}


def _default_rates_path() -> str:
    """Return the default path to the COPERT 5 degradation rates JSON."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "data", "copert5_degradation_rates.json",
    )


def map_bs_to_euro(bs_standard: str, fuel_type: str = "petrol") -> str:
    """Map an Indian BS standard string to the COPERT Euro equivalent key.

    Accepts a variety of formats: ``"BS6"``, ``"BS-VI"``, ``"bsvi"``,
    ``"BS4"``, ``"BS-IV"``, ``"bsiv"``, etc.

    Parameters
    ----------
    bs_standard : str
        Indian Bharat Stage standard identifier.
    fuel_type : str
        ``"petrol"`` or ``"diesel"`` (default ``"petrol"``).

    Returns
    -------
    str
        Key into the degradation-rates JSON, e.g. ``"euro6_petrol"``.

    Raises
    ------
    ValueError
        If the BS standard cannot be mapped.
    """
    s = bs_standard.upper().replace("-", "").replace(" ", "")
    fuel = fuel_type.lower().strip()
    if fuel not in ("petrol", "diesel"):
        raise ValueError(f"Unknown fuel type: {fuel_type!r}")

    if s in ("BS6", "BSVI", "BS06"):
        return f"euro6_{fuel}"
    if s in ("BS4", "BSIV", "BS04"):
        return f"euro4_{fuel}"

    raise ValueError(
        f"Cannot map BS standard {bs_standard!r} to a COPERT Euro equivalent. "
        f"Supported: BS6/BSVI, BS4/BSIV."
    )


class DegradationModel:
    """COPERT 5 emission degradation model.

    Models how vehicle emissions deteriorate with mileage and age,
    using published deterioration rates from NAEI/EEA Guidebook 2023.

    References
    ----------
    [1] NAEI Emission Degradation Methodology (2024)
    [2] COPERT 5 v5.6, Emisia
    [3] EEA EMEP/EEA Guidebook 2023, Chapter 1.A.3.b
    """

    def __init__(self, rates_path: Optional[str] = None) -> None:
        if rates_path is None:
            rates_path = _default_rates_path()
        with open(rates_path, "r", encoding="utf-8") as fh:
            self._data: dict = json.load(fh)

    # ------------------------------------------------------------------
    # Core degradation factor
    # ------------------------------------------------------------------

    def degradation_factor(
        self,
        pollutant: str,
        mileage_km: float,
        standard: str = "euro6_petrol",
    ) -> float:
        """Return multiplicative degradation factor for a pollutant.

        EF(km) = 1.0 + rate_per_km * min(mileage_km, cap_km)

        Parameters
        ----------
        pollutant : str
            Short pollutant name: ``"co2"``, ``"co"``, ``"nox"``,
            ``"hc"``, or ``"pm25"``.
        mileage_km : float
            Vehicle's total accumulated mileage in kilometres.
        standard : str
            Key into the rates JSON, e.g. ``"euro6_petrol"``.

        Returns
        -------
        float
            Factor >= 1.0 (1.0 = no degradation, 1.5 = 50 % increase).
        """
        entry = self._data[standard]
        rate = entry["rates"].get(pollutant, 0.0)
        cap = entry["cap_km"]
        effective_km = min(max(mileage_km, 0.0), cap)
        return 1.0 + rate * effective_km

    # ------------------------------------------------------------------
    # Apply degradation to a full emission reading
    # ------------------------------------------------------------------

    def apply_degradation(
        self,
        base_emissions: dict,
        mileage_km: float,
        standard: str = "euro6_petrol",
    ) -> dict:
        """Apply mileage degradation to a full emission reading.

        Parameters
        ----------
        base_emissions : dict
            Dict with keys like ``co2_g_per_km``, ``co_g_per_km``, etc.
        mileage_km : float
            Vehicle's total mileage.
        standard : str
            ``"euro6_petrol"``, ``"euro4_petrol"``, ``"euro6_diesel"``,
            or ``"euro4_diesel"``.

        Returns
        -------
        dict
            Copy of *base_emissions* with pollutant values multiplied by
            degradation factors. Non-pollutant keys are passed through
            unchanged.
        """
        result = dict(base_emissions)
        for key, pollutant in _KEY_TO_POLLUTANT.items():
            if key in result:
                factor = self.degradation_factor(pollutant, mileage_km, standard)
                result[key] = result[key] * factor
        return result

    # ------------------------------------------------------------------
    # Sudden failure
    # ------------------------------------------------------------------

    def apply_sudden_failure(
        self,
        base_emissions: dict,
        failure_type: str,
    ) -> dict:
        """Apply a sudden failure (catalyst removal, DPF removal, etc.).

        Parameters
        ----------
        base_emissions : dict
            Dict with keys like ``co2_g_per_km``, ``co_g_per_km``, etc.
        failure_type : str
            Key from ``"sudden_failures"`` in the rates JSON, e.g.
            ``"catalyst_removal"``, ``"dpf_removal_diesel"``.

        Returns
        -------
        dict
            Copy of *base_emissions* with affected values multiplied.
        """
        multipliers = self._data["sudden_failures"][failure_type]
        result = dict(base_emissions)
        for key, pollutant in _KEY_TO_POLLUTANT.items():
            if key in result and pollutant in multipliers:
                result[key] = result[key] * multipliers[pollutant]
        return result

    # ------------------------------------------------------------------
    # Trajectory simulation
    # ------------------------------------------------------------------

    def simulate_degradation_trajectory(
        self,
        base_emissions: dict,
        mileage_start: float,
        mileage_end: float,
        step_km: float = 1000,
        standard: str = "euro6_petrol",
        failure_at_km: Optional[float] = None,
        failure_type: Optional[str] = None,
    ) -> List[dict]:
        """Generate a trajectory of emissions over a mileage range.

        Parameters
        ----------
        base_emissions : dict
            Baseline (new-vehicle) emission reading.
        mileage_start, mileage_end : float
            Mileage window to simulate (km).
        step_km : float
            Step size in km (default 1000).
        standard : str
            COPERT standard key.
        failure_at_km : float or None
            If set, inject *failure_type* at this mileage.
        failure_type : str or None
            Sudden-failure key (required if *failure_at_km* is set).

        Returns
        -------
        list[dict]
            Each entry has ``mileage_km``, ``co2``, ``co``, ``nox``,
            ``hc``, ``pm25``, ``ces_score``.
        """
        trajectory: List[dict] = []
        km = mileage_start
        while km <= mileage_end:
            degraded = self.apply_degradation(base_emissions, km, standard)
            if (failure_at_km is not None
                    and failure_type is not None
                    and km >= failure_at_km):
                degraded = self.apply_sudden_failure(degraded, failure_type)
            row = {"mileage_km": km}
            for pollutant in _POLLUTANTS:
                key = _POLLUTANT_TO_KEY[pollutant]
                row[pollutant] = degraded.get(key, 0.0)
            row["ces_score"] = degraded.get("ces_score", 0.0)
            trajectory.append(row)
            km += step_km
        return trajectory

    # ------------------------------------------------------------------
    # Time-to-failure estimation
    # ------------------------------------------------------------------

    def estimate_time_to_failure(
        self,
        base_emissions: dict,
        mileage_km: float,
        standard: str = "euro6_petrol",
        km_per_month: float = 1500,
    ) -> dict:
        """Estimate months until CES crosses 1.0 threshold.

        Uses a simple forward projection: starting from *mileage_km*,
        step month-by-month, recompute degraded emissions, and compute
        CES.  Stops when CES >= 1.0 or the degradation cap is reached.

        Parameters
        ----------
        base_emissions : dict
            Baseline (new-vehicle) emission reading.  Must include
            ``ces_score`` or the individual pollutant keys.
        mileage_km : float
            Current mileage.
        standard : str
            COPERT standard key.
        km_per_month : float
            Assumed driving rate (default 1500 km/month).

        Returns
        -------
        dict
            ``months_to_failure``: int or None if CES never reaches 1.0
            within the cap.
            ``projected_mileage_at_failure``: float or None.
            ``dominant_pollutant``: str — the pollutant contributing most
            to CES at the failure point (or at cap).
            ``confidence``: str — "high", "medium", or "low".
        """
        # We need the thresholds to compute CES.  Import here to avoid
        # circular imports at module level.
        try:
            from backend.emission_engine import get_thresholds, CES_WEIGHTS
        except ImportError:
            # Fallback: use BS-VI petrol defaults
            from backend.ces_constants import (
                CES_WEIGHTS,
                BSVI_THRESHOLDS_PETROL,
            )
            get_thresholds = None

        # Determine thresholds
        if get_thresholds is not None:
            fuel = "diesel" if "diesel" in standard else "petrol"
            bs = "BS6" if "euro6" in standard else "BS4"
            from backend.emission_engine import BSStandard
            thresholds = get_thresholds(fuel, BSStandard(bs))
        else:
            thresholds = BSVI_THRESHOLDS_PETROL

        cap_km = self._data[standard]["cap_km"]
        max_months = int(math.ceil((cap_km - mileage_km) / max(km_per_month, 1)))
        max_months = max(max_months, 1)

        for month in range(1, max_months + 1):
            projected_km = mileage_km + month * km_per_month
            degraded = self.apply_degradation(base_emissions, projected_km, standard)

            # Compute CES
            ces = 0.0
            contributions: Dict[str, float] = {}
            for pollutant in _POLLUTANTS:
                key = _POLLUTANT_TO_KEY[pollutant]
                val = degraded.get(key, 0.0)
                threshold = thresholds.get(pollutant, 1.0)
                weight = CES_WEIGHTS.get(pollutant, 0.0)
                contrib = (val / threshold) * weight
                contributions[pollutant] = contrib
                ces += contrib

            if ces >= 1.0:
                dominant = max(contributions, key=contributions.get)  # type: ignore[arg-type]
                return {
                    "months_to_failure": month,
                    "projected_mileage_at_failure": projected_km,
                    "dominant_pollutant": dominant,
                    "confidence": "high" if month <= 24 else "medium",
                }

        # CES never crossed 1.0 within the degradation cap
        # Compute final contributions for dominant pollutant
        degraded = self.apply_degradation(base_emissions, cap_km, standard)
        contributions_final: Dict[str, float] = {}
        for pollutant in _POLLUTANTS:
            key = _POLLUTANT_TO_KEY[pollutant]
            val = degraded.get(key, 0.0)
            threshold = thresholds.get(pollutant, 1.0)
            weight = CES_WEIGHTS.get(pollutant, 0.0)
            contributions_final[pollutant] = (val / threshold) * weight

        dominant = max(contributions_final, key=contributions_final.get)  # type: ignore[arg-type]
        return {
            "months_to_failure": None,
            "projected_mileage_at_failure": None,
            "dominant_pollutant": dominant,
            "confidence": "low",
        }
