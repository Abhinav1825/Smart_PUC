"""Vehicle Specific Power (VSP) model for instantaneous emission estimation.

Implements the EPA MOVES Vehicle Specific Power framework and fuel-consumption
estimation using the Rakha polynomial model.  VSP captures the instantaneous
power demand per unit vehicle mass and is the primary explanatory variable for
modal emission rates in the US EPA MOVES inventory model.

References
----------
[1] US EPA, "MOVES3 Technical Guidance: Using MOVES to Prepare Emission
    Inventories for State Implementation Plans and Transportation Conformity,"
    EPA-420-B-20-052, November 2020.
[2] Rakha, H., Ahn, K., and Trani, A., "Requirements for Evaluating Traffic
    Signal Control Impacts on Energy and Emissions Based on Instantaneous
    Speed and Acceleration Measurements," *Transportation Research Record*,
    vol. 1738, pp. 56-67, 2004.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Vehicle parameter data class
# ---------------------------------------------------------------------------

@dataclass
class VehicleParams:
    """Physical and aerodynamic parameters for a specific vehicle class.

    Default values correspond to a representative Indian petrol hatchback
    (Maruti Suzuki Swift / Hyundai i20 class) operating under standard
    sea-level atmospheric conditions.

    Attributes
    ----------
    mass : float
        Vehicle mass including average payload, in kilograms (kg).
    drag_coefficient : float
        Aerodynamic drag coefficient Cd (dimensionless).
    frontal_area : float
        Effective frontal area A, in square metres (m²).
    rolling_resistance : float
        Tyre rolling-resistance coefficient μ (dimensionless).
    air_density : float
        Ambient air density ρ, in kg/m³.  Default 1.225 corresponds to the
        ISA standard atmosphere at sea level and 15 °C.
    gravity : float
        Gravitational acceleration g, in m/s².
    """

    mass: float = 1000.0
    drag_coefficient: float = 0.32
    frontal_area: float = 2.1
    rolling_resistance: float = 0.015
    air_density: float = 1.225
    gravity: float = 9.81


# Module-level default instance so callers can omit the parameter.
_DEFAULT_PARAMS = VehicleParams()


# ---------------------------------------------------------------------------
# Core VSP calculation
# ---------------------------------------------------------------------------

def calculate_vsp(
    speed_mps: float,
    accel: float,
    grade: float = 0.0,
    params: Optional[VehicleParams] = None,
) -> float:
    """Compute Vehicle Specific Power using the EPA MOVES formulation.

    The VSP equation adopted by MOVES3 [1]_ is:

    .. math::

        \\text{VSP} = v \\bigl[a + g\\sin(\\theta)
        + \\mu\\,g\\cos(\\theta)\\bigr]
        + \\frac{\\rho\\,C_d\\,A}{2m}\\,v^3

    where *v* is speed (m/s), *a* is acceleration (m/s²), *θ* is the road
    grade angle (radians, approximated from grade fraction), *g* is
    gravitational acceleration, *μ* is rolling-resistance coefficient,
    *ρ* is air density, *C_d* is the drag coefficient, *A* is frontal area,
    and *m* is vehicle mass.

    Parameters
    ----------
    speed_mps : float
        Instantaneous vehicle speed in metres per second (m/s).
    accel : float
        Instantaneous longitudinal acceleration in m/s².
    grade : float, optional
        Road grade expressed as a decimal fraction (e.g. 0.05 for a 5 %
        grade).  Defaults to 0.0 (flat road).
    params : VehicleParams or None, optional
        Vehicle-specific constants.  When *None*, the module defaults for
        a Maruti Swift class petrol hatchback are used.

    Returns
    -------
    float
        Vehicle Specific Power in watts per kilogram (W/kg).

    Notes
    -----
    Grade is converted to an angle via ``θ = arctan(grade)``.  For small
    grades (< 10 %) the difference from the simpler ``sin(θ) ≈ grade``
    approximation is negligible, but the exact form is retained for
    correctness on steep terrain.

    References
    ----------
    .. [1] US EPA, "MOVES3 Technical Guidance," EPA-420-B-20-052, 2020.
    """
    if params is None:
        params = _DEFAULT_PARAMS

    v: float = float(speed_mps)
    a: float = float(accel)
    g: float = params.gravity
    mu: float = params.rolling_resistance
    rho: float = params.air_density
    cd: float = params.drag_coefficient
    area: float = params.frontal_area
    m: float = params.mass

    # Road grade angle (radians)
    theta: float = float(np.arctan(grade))

    # Tractive power per unit mass
    tractive_term: float = v * (a + g * np.sin(theta) + mu * g * np.cos(theta))

    # Aerodynamic drag power per unit mass
    aero_term: float = (rho * cd * area) / (2.0 * m) * v ** 3

    vsp: float = tractive_term + aero_term
    return vsp


# ---------------------------------------------------------------------------
# EPA MOVES operating-mode binning
# ---------------------------------------------------------------------------

def get_operating_mode_bin(vsp: float, speed_mps: float) -> int:
    """Map a (VSP, speed) pair to an EPA MOVES operating-mode bin.

    The operating-mode bins partition the VSP–speed space into discrete
    regimes that share similar emission characteristics.  The bin
    definitions follow the light-duty vehicle scheme from MOVES3 [1]_.

    Parameters
    ----------
    vsp : float
        Vehicle Specific Power in W/kg (as returned by
        :func:`calculate_vsp`).
    speed_mps : float
        Instantaneous vehicle speed in metres per second (m/s).

    Returns
    -------
    int
        Operating-mode bin identifier.  The mapping is:

        ====  ============================================
        Bin   Condition
        ====  ============================================
         0    speed < 0.2778 m/s (≈ 1 km/h) – idle / stop
         1    braking / deceleration (VSP < 0)
        11    0 ≤ VSP < 3
        21    3 ≤ VSP < 6
        22    6 ≤ VSP < 9
        23    9 ≤ VSP < 12
        24    12 ≤ VSP < 18
        25    18 ≤ VSP < 24
        27    24 ≤ VSP < 30
        28    VSP ≥ 30
        ====  ============================================

    References
    ----------
    .. [1] US EPA, "MOVES3 Technical Guidance," EPA-420-B-20-052, 2020.
    """
    # Idle / stopped
    if speed_mps < 0.2778:
        return 0

    # Braking / deceleration
    if vsp < 0.0:
        return 1

    # Cruise / acceleration bins stratified by VSP
    if vsp < 3.0:
        return 11
    if vsp < 6.0:
        return 21
    if vsp < 9.0:
        return 22
    if vsp < 12.0:
        return 23
    if vsp < 18.0:
        return 24
    if vsp < 24.0:
        return 25
    if vsp < 30.0:
        return 27

    return 28


# ---------------------------------------------------------------------------
# Fuel-rate estimation (Rakha polynomial model)
# ---------------------------------------------------------------------------

def estimate_fuel_rate(vsp: float, speed_mps: float) -> float:
    """Estimate instantaneous fuel consumption rate from VSP.

    Uses a simplified polynomial fit inspired by the Rakha et al. [2]_
    model relating VSP to fuel consumption for light-duty petrol vehicles.
    The coefficients are calibrated for a typical Indian BS-VI petrol
    hatchback (1.0–1.2 L naturally-aspirated engine).

    The model returns fuel consumption normalised to litres per 100 km
    (L/100 km) so that it can be compared directly with official ARAI
    mileage figures.

    Parameters
    ----------
    vsp : float
        Vehicle Specific Power in W/kg.
    speed_mps : float
        Instantaneous vehicle speed in metres per second (m/s).

    Returns
    -------
    float
        Estimated fuel consumption rate in litres per 100 kilometres
        (L/100 km).  Returns 0.0 when the vehicle is effectively
        stationary (speed < 0.2778 m/s) to avoid division-by-zero
        artefacts; idle fuel consumption should be handled separately.

    Notes
    -----
    The instantaneous fuel rate *F* (mL/s) is first estimated via a
    third-order polynomial in VSP [2]_:

    .. math::

        F = \\max\\bigl(\\alpha_0 + \\alpha_1\\,P + \\alpha_2\\,P^2
            + \\alpha_3\\,P^3,\\; F_{\\min}\\bigr)

    where *P* = max(VSP, 0) (fuel cut-off during deceleration is
    approximated by clamping VSP to zero).  The result is then converted
    to L/100 km using the instantaneous speed.

    Coefficients (``alpha_*``) are derived from dynamometer data for
    a 1.2 L petrol engine representative of the Maruti Swift / Hyundai
    Grand i10 class and should be treated as indicative rather than
    regulatory-grade.

    References
    ----------
    .. [2] Rakha, H., Ahn, K., and Trani, A., "Requirements for Evaluating
       Traffic Signal Control Impacts on Energy and Emissions Based on
       Instantaneous Speed and Acceleration Measurements,"
       *Transportation Research Record*, 1738, 56-67, 2004.
    """
    # Guard: avoid division by zero at near-zero speeds.
    if speed_mps < 0.2778:
        return 0.0

    # Clamp VSP to zero during deceleration (fuel cut-off assumption).
    p: float = float(np.maximum(vsp, 0.0))

    # Polynomial coefficients (mL/s) – calibrated for 1.0-1.2 L NA petrol
    # engine, sea-level conditions, as per Rakha et al. methodology [2].
    alpha_0: float = 0.30   # idle-equivalent baseline (mL/s)
    alpha_1: float = 0.028  # linear term
    alpha_2: float = 0.0005 # quadratic term
    alpha_3: float = 3.0e-6 # cubic term

    # Instantaneous fuel rate in mL/s
    fuel_ml_per_s: float = alpha_0 + alpha_1 * p + alpha_2 * p ** 2 + alpha_3 * p ** 3

    # Floor at a small positive value (engine never consumes zero while
    # running).
    fuel_ml_per_s = float(np.maximum(fuel_ml_per_s, 0.1))

    # Convert mL/s → L/100 km
    # L/100km = (fuel_mL_per_s / 1000) / (speed_m_per_s / 1000) * 100
    #         = fuel_mL_per_s / speed_m_per_s * 100  (units simplify)
    fuel_l_per_100km: float = (fuel_ml_per_s / speed_mps) * 100.0

    return fuel_l_per_100km
