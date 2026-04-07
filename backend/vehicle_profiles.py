"""
Smart PUC — Vehicle Profile Registry
======================================
Provides per-vehicle physical and regulatory parameters so that the
simulator, emission engine, and fraud detector can produce vehicle-class-
specific behaviour instead of treating every registration as a generic
1.2 L petrol hatchback.

The registry is pre-loaded from ``config/demo_fleet.json`` at import time
and can be extended at runtime via :func:`register_vehicle`.

Usage
-----
::

    from vehicle_profiles import get_profile, DEMO_FLEET

    profile = get_profile("MH12AB1234")
    print(profile.vehicle_class, profile.curb_weight_kg)
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━ Enums ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VehicleClass(str, Enum):
    TWO_WHEELER = "TWO_WHEELER"
    HATCHBACK = "HATCHBACK"
    SEDAN = "SEDAN"
    SUV = "SUV"
    TRUCK = "TRUCK"
    AUTO_RICKSHAW = "AUTO_RICKSHAW"
    BUS = "BUS"


class FuelType(str, Enum):
    PETROL = "petrol"
    DIESEL = "diesel"
    CNG = "cng"
    LPG = "lpg"
    HYBRID_PETROL = "hybrid_petrol"
    ELECTRIC = "electric"


class TransmissionType(str, Enum):
    MANUAL_4 = "MANUAL_4"
    MANUAL_5 = "MANUAL_5"
    MANUAL_6 = "MANUAL_6"
    AMT = "AMT"
    CVT = "CVT"
    AUTOMATIC_TC = "AUTOMATIC_TC"
    DCT = "DCT"


# ━━━━━━━━━━━━━━━━━━━━━━━━━ Vehicle Profile ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class VehicleProfile:
    """Complete vehicle specification for physics-based simulation."""

    registration_no: str
    display_name: str = ""
    vehicle_class: str = "SEDAN"
    fuel_type: str = "petrol"
    transmission: str = "MANUAL_5"
    bs_standard: str = "BS6"

    # Engine
    engine_displacement_cc: int = 1200
    redline_rpm: int = 6500
    idle_rpm: int = 750

    # Chassis
    curb_weight_kg: float = 1200.0
    tire_radius_m: float = 0.31
    drag_coefficient: float = 0.30
    frontal_area_m2: float = 2.1

    # Drivetrain
    gear_ratios: List[float] = field(default_factory=lambda: [3.545, 1.904, 1.233, 0.885, 0.694])
    final_drive_ratio: float = 4.058

    # Speed & age
    max_speed_kmh: float = 185.0
    manufacture_year: int = 2022
    mileage_km: int = 35000

    # VAHAN-style registration info
    manufacturer: str = ""
    model: str = ""
    variant: str = ""
    owner_name: str = ""
    registration_date: str = ""
    insurance_valid_until: str = ""
    fitness_valid_until: str = ""

    # ── Derived properties ─────────────────────────────────────────────

    @property
    def num_gears(self) -> int:
        """Number of forward gears (0 for CVT when gear_ratios is empty)."""
        return len(self.gear_ratios)

    @property
    def is_cvt(self) -> bool:
        return self.transmission == TransmissionType.CVT.value or not self.gear_ratios

    @property
    def age_years(self) -> float:
        """Approximate vehicle age in years from manufacture_year."""
        import datetime
        return max(0.0, datetime.datetime.now().year - self.manufacture_year)

    @property
    def displacement_factor(self) -> float:
        """Emission scaling factor relative to a 1200cc baseline engine.

        Larger engines produce proportionally more emissions at the same
        operating point.  The relationship is sub-linear (square-root)
        because larger engines run at lower specific load for the same
        road-load power.
        """
        return math.sqrt(self.engine_displacement_cc / 1200.0)

    @property
    def mass_factor(self) -> float:
        """VSP mass scaling relative to 1200 kg baseline."""
        return self.curb_weight_kg / 1200.0

    @property
    def degradation_factor(self) -> float:
        """Age + mileage degradation multiplier (COPERT 5 inspired).

        Returns a factor >= 1.0 that increases emissions for older /
        high-mileage vehicles.  Caps at 1.50 (50% degradation).
        """
        age_deg = min(self.age_years * 0.015, 0.20)       # up to +20% for age
        km_deg = min(self.mileage_km / 500_000 * 0.30, 0.30)  # up to +30% for mileage
        return min(1.0 + age_deg + km_deg, 1.50)

    @property
    def fuel_type_for_engine(self) -> str:
        """Map extended fuel types to the emission engine's fuel_type param.

        The emission engine currently supports petrol/diesel/cng/lpg.
        Hybrid vehicles use petrol engine in combustion mode.
        Electric vehicles produce zero direct emissions.
        """
        ft = self.fuel_type.lower()
        if ft == "hybrid_petrol":
            return "petrol"
        if ft == "electric":
            return "electric"
        return ft

    @property
    def hybrid_electric_fraction(self) -> float:
        """Fraction of driving done on electric motor (0.0 for non-hybrids).

        Mild hybrids: ~15%, strong hybrids: ~40%, plug-in: ~60%.
        For demo purposes we assume a strong hybrid (Toyota-style).
        """
        if self.fuel_type.lower() == "hybrid_petrol":
            return 0.40
        return 0.0

    def gear_speed_bands(self) -> List[tuple]:
        """Generate speed-band gear selection thresholds for this vehicle.

        For manual/AMT/DCT transmissions, maps speed ranges to gears
        based on the number of gears and max speed.  For CVT, returns
        an empty list (continuous ratio selection).
        """
        if self.is_cvt or not self.gear_ratios:
            return []

        n = len(self.gear_ratios)
        max_spd = self.max_speed_kmh

        if n <= 4:
            # 4-speed: typical auto-rickshaw / old vehicles
            bands = [
                (max_spd * 0.12, 1),  # ~8 km/h
                (max_spd * 0.25, 2),  # ~18 km/h
                (max_spd * 0.45, 3),  # ~32 km/h
            ]
        elif n == 5:
            # 5-speed: standard Indian car
            bands = [
                (max_spd * 0.08, 1),   # ~15 km/h
                (max_spd * 0.16, 2),   # ~30 km/h
                (max_spd * 0.27, 3),   # ~50 km/h
                (max_spd * 0.43, 4),   # ~80 km/h
            ]
        else:
            # 6-speed: modern cars / trucks
            bands = [
                (max_spd * 0.07, 1),
                (max_spd * 0.14, 2),
                (max_spd * 0.23, 3),
                (max_spd * 0.35, 4),
                (max_spd * 0.55, 5),
            ]
        return bands

    def select_gear(self, speed_kmh: float) -> int:
        """Select gear for given speed using vehicle-specific bands."""
        if self.is_cvt:
            return 0  # CVT has no discrete gears
        bands = self.gear_speed_bands()
        if speed_kmh < 1.0:
            return 1
        for threshold, gear in bands:
            if speed_kmh <= threshold:
                return gear
        return len(self.gear_ratios)  # highest gear

    def calculate_rpm(self, speed_kmh: float) -> int:
        """Calculate engine RPM for given speed using this vehicle's drivetrain."""
        if speed_kmh < 1.0:
            return self.idle_rpm

        speed_mps = speed_kmh / 3.6

        if self.is_cvt:
            # CVT optimizes RPM for fuel efficiency. At low loads (constant speed),
            # RPM stays in the efficiency sweet spot. Under acceleration or high
            # speed, RPM increases toward the power band.
            speed_mps = speed_kmh / 3.6

            # Estimate power demand (simplified VSP proxy)
            # At steady state, power ~ drag + rolling resistance
            power_proxy = (0.5 * 1.225 * self.drag_coefficient * self.frontal_area_m2 * speed_mps**2
                           + self.curb_weight_kg * 9.81 * 0.015) * speed_mps

            # Normalize power to [0, 1] range based on max power (rough estimate)
            max_power = self.engine_displacement_cc * 0.05  # ~50W per cc at peak
            power_frac = min(power_proxy / max(max_power, 1.0), 1.0)

            # CVT targets efficiency RPM at low load, power RPM at high load
            efficiency_rpm = self.idle_rpm + (self.redline_rpm - self.idle_rpm) * 0.35
            power_rpm = self.redline_rpm * 0.85

            # Blend based on power demand (concave curve — stays low longer)
            rpm = efficiency_rpm + (power_rpm - efficiency_rpm) * (power_frac ** 1.5)
            return int(max(self.idle_rpm, min(self.redline_rpm, rpm)))

        gear = self.select_gear(speed_kmh)
        gear_ratio = self.gear_ratios[gear - 1] if gear <= len(self.gear_ratios) else self.gear_ratios[-1]

        rpm = (speed_mps * gear_ratio * self.final_drive_ratio) / (
            2.0 * math.pi * self.tire_radius_m
        ) * 60.0
        return int(max(self.idle_rpm, min(self.redline_rpm, rpm)))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VehicleProfile":
        """Create from a dict (e.g. loaded from JSON)."""
        # Filter to only known fields
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ━━━━━━━━━━━━━━━━━━━━━━━━ Default Profile ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DEFAULT_PROFILE = VehicleProfile(
    registration_no="UNKNOWN",
    display_name="Generic Sedan (Default)",
    vehicle_class="SEDAN",
    fuel_type="petrol",
    transmission="MANUAL_5",
    bs_standard="BS6",
    engine_displacement_cc=1200,
    curb_weight_kg=1200.0,
    tire_radius_m=0.31,
    manufacture_year=2022,
    mileage_km=35000,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━ Registry ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_registry: Dict[str, VehicleProfile] = {}


def register_vehicle(profile: VehicleProfile) -> None:
    """Add or update a vehicle in the registry."""
    _registry[profile.registration_no.upper()] = profile


def get_profile(vehicle_id: str) -> VehicleProfile:
    """Look up a vehicle profile by registration number.

    Returns the default profile (with the requested vehicle_id) if
    the vehicle is not registered.
    """
    vid = vehicle_id.upper().strip()
    if vid in _registry:
        return _registry[vid]

    # Return a copy of default with the requested ID
    default = VehicleProfile(
        registration_no=vid,
        display_name=f"Unknown Vehicle ({vid})",
        vehicle_class=DEFAULT_PROFILE.vehicle_class,
        fuel_type=DEFAULT_PROFILE.fuel_type,
        transmission=DEFAULT_PROFILE.transmission,
        bs_standard=DEFAULT_PROFILE.bs_standard,
        engine_displacement_cc=DEFAULT_PROFILE.engine_displacement_cc,
        curb_weight_kg=DEFAULT_PROFILE.curb_weight_kg,
        tire_radius_m=DEFAULT_PROFILE.tire_radius_m,
        manufacture_year=DEFAULT_PROFILE.manufacture_year,
        mileage_km=DEFAULT_PROFILE.mileage_km,
        gear_ratios=list(DEFAULT_PROFILE.gear_ratios),
        final_drive_ratio=DEFAULT_PROFILE.final_drive_ratio,
        drag_coefficient=DEFAULT_PROFILE.drag_coefficient,
        frontal_area_m2=DEFAULT_PROFILE.frontal_area_m2,
        redline_rpm=DEFAULT_PROFILE.redline_rpm,
        idle_rpm=DEFAULT_PROFILE.idle_rpm,
        max_speed_kmh=DEFAULT_PROFILE.max_speed_kmh,
    )
    return default


def get_all_profiles() -> Dict[str, VehicleProfile]:
    """Return a copy of the full registry."""
    return dict(_registry)


def list_vehicle_ids() -> List[str]:
    """Return sorted list of registered vehicle IDs."""
    return sorted(_registry.keys())


# ━━━━━━━━━━━━━━━━━━━━━━━━ Load Demo Fleet ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_demo_fleet() -> None:
    """Load demo fleet from config/demo_fleet.json at import time."""
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "config", "demo_fleet.json"
    )
    config_path = os.path.normpath(config_path)

    if not os.path.isfile(config_path):
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for v in data.get("vehicles", []):
            profile = VehicleProfile.from_dict(v)
            register_vehicle(profile)
    except Exception as exc:
        import warnings
        warnings.warn(f"Failed to load demo fleet from {config_path}: {exc}")


_load_demo_fleet()

DEMO_FLEET = list(_registry.keys())


# ━━━━━━━━━━━━━━━━━━━━━━━━ Emission Rate Scalers ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# These functions provide vehicle-class-specific scaling factors for the
# emission engine's base rates.  The base rates in emission_engine.py are
# calibrated for a 1.2L petrol sedan — these scalers adjust them for
# different vehicle types.

# IPCC fuel-based CO2 emission factors (g CO2 per litre of fuel)
FUEL_CO2_FACTORS: Dict[str, float] = {
    "petrol": 2310.0,
    "diesel": 2680.0,
    "cng": 1840.0,      # Natural gas: ~20% less CO2 than petrol
    "lpg": 1665.0,      # LPG: ~28% less CO2 than petrol
    "hybrid_petrol": 2310.0,  # Same factor, but applied to reduced fuel consumption
    "electric": 0.0,
}

# Fuel-type-specific pollutant scaling factors relative to petrol baseline.
# These represent the characteristic emission profile of each fuel type.
FUEL_EMISSION_SCALERS: Dict[str, Dict[str, float]] = {
    "petrol": {
        "co2": 1.00, "co": 1.00, "nox": 1.00, "hc": 1.00, "pm25": 1.00,
    },
    "diesel": {
        "co2": 1.08,   # ~8% more CO2 per km (heavier fuel, more energy/L)
        "co": 0.40,    # Diesel has much lower CO (lean burn)
        "nox": 2.50,   # Diesel NOx is significantly higher
        "hc": 0.60,    # Lower HC
        "pm25": 8.00,  # Much higher particulates (even with DPF)
    },
    "cng": {
        "co2": 0.75,   # ~25% less CO2 vs petrol
        "co": 0.60,    # Lower CO due to cleaner combustion
        "nox": 0.85,   # Slightly lower NOx
        "hc": 1.80,    # Higher methane/HC (unburned gas slip)
        "pm25": 0.05,  # Negligible PM2.5
    },
    "lpg": {
        "co2": 0.82,   # ~18% less CO2 vs petrol
        "co": 0.70,    # Lower CO
        "nox": 0.90,   # Slightly lower NOx
        "hc": 1.20,    # Slightly higher HC
        "pm25": 0.10,  # Very low PM2.5
    },
    "hybrid_petrol": {
        "co2": 0.60,   # Strong hybrid: ~40% less CO2 (electric assist)
        "co": 0.55,    # Less cold-start, more efficient combustion
        "nox": 0.65,   # Optimized engine operation point
        "hc": 0.50,    # Less transient operation
        "pm25": 0.70,  # Slightly less PM
    },
    "electric": {
        "co2": 0.00, "co": 0.00, "nox": 0.00, "hc": 0.00, "pm25": 0.00,
    },
}

# Vehicle class emission scaling (relative to sedan baseline)
VEHICLE_CLASS_SCALERS: Dict[str, Dict[str, float]] = {
    "TWO_WHEELER": {
        "co2": 0.35, "co": 1.20, "nox": 0.30, "hc": 2.50, "pm25": 0.20,
    },
    "HATCHBACK": {
        "co2": 0.85, "co": 0.90, "nox": 0.85, "hc": 0.90, "pm25": 0.85,
    },
    "SEDAN": {
        "co2": 1.00, "co": 1.00, "nox": 1.00, "hc": 1.00, "pm25": 1.00,
    },
    "SUV": {
        "co2": 1.35, "co": 1.15, "nox": 1.25, "hc": 1.10, "pm25": 1.20,
    },
    "TRUCK": {
        "co2": 3.50, "co": 2.00, "nox": 4.00, "hc": 1.80, "pm25": 5.00,
    },
    "AUTO_RICKSHAW": {
        "co2": 0.40, "co": 2.50, "nox": 0.35, "hc": 3.00, "pm25": 0.30,
    },
    "BUS": {
        "co2": 5.00, "co": 2.50, "nox": 5.50, "hc": 2.00, "pm25": 6.00,
    },
}


def get_emission_scalers(profile: VehicleProfile) -> Dict[str, float]:
    """Compute combined emission scaling factors for a vehicle profile.

    Combines: fuel type × vehicle class × displacement × degradation.
    Returns a dict of per-pollutant multipliers to apply to the base
    emission rates.
    """
    fuel_scaler = FUEL_EMISSION_SCALERS.get(
        profile.fuel_type.lower(),
        FUEL_EMISSION_SCALERS["petrol"],
    )
    class_scaler = VEHICLE_CLASS_SCALERS.get(
        profile.vehicle_class.upper(),
        VEHICLE_CLASS_SCALERS["SEDAN"],
    )

    disp = profile.displacement_factor
    degrad = profile.degradation_factor

    combined = {}
    for pollutant in ("co2", "co", "nox", "hc", "pm25"):
        combined[pollutant] = (
            fuel_scaler[pollutant]
            * class_scaler[pollutant]
            * disp
            * degrad
        )
    return combined


def get_fuel_co2_factor(fuel_type: str) -> float:
    """Return IPCC CO2 emission factor (g/L) for the given fuel type."""
    return FUEL_CO2_FACTORS.get(fuel_type.lower(), FUEL_CO2_FACTORS["petrol"])
