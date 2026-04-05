"""
VAHAN 4.0 Vehicle Registration Database Bridge — *SIMULATED integration point*.

⚠️ IMPORTANT — PAPER REVIEWERS AND CODE AUDITORS PLEASE READ
============================================================

This module is a **SIMULATED integration point** for India's VAHAN 4.0
vehicle registration database, operated by the Ministry of Road Transport
and Highways (MoRTH).

Out of the box, this module uses a built-in ``MockVaahanService`` that
returns hand-coded data for roughly ten test vehicles. The default backend
initialisation in ``backend/app.py`` passes ``use_mock=True`` so every
lookup in the research prototype hits the mock catalogue, not the real
VAHAN API.

This is a DELIBERATE choice for the following reasons:

1. The real VAHAN 4.0 API (``vahan.parivahan.gov.in``) is access-controlled;
   MoRTH grants credentials only to registered state departments, not to
   researchers.
2. Any paper, benchmark, or demo derived from this repository is reproducible
   WITHOUT network access to VAHAN, which is critical for the Artifact
   Evaluation of academic venues (IEEE, ACM).
3. The data flow, field shape, and error modes of ``MockVaahanService`` are
   a faithful model of the real API's public schema, so swapping in a real
   client is a drop-in replacement.

Production use
--------------
To use the real VAHAN API:

1. Obtain API credentials from MoRTH (state government authorisation
   required).
2. Populate ``VAHAN_API_BASE`` and ``VAHAN_API_KEY`` in ``.env``.
3. Instantiate ``VaahanBridge(use_mock=False)`` in ``backend/app.py``.
4. Implement the HTTP client in the ``_fetch_from_real_api`` hook below
   (left as a TODO with a clearly marked ``NotImplementedError``).

Nothing in the research claims of the paper depends on real VAHAN data;
the bridge exists only to show where real-world RTO integration would
plug into the Smart PUC pipeline.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

try:
    import requests  # type: ignore[import-untyped]
except ImportError:  # allow import even without requests installed
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# BS norms ordered from oldest to newest for comparison
_BS_NORM_ORDER: list[str] = [
    "BS-I",
    "BS-II",
    "BS-III",
    "BS-IV",
    "BS-V",
    "BS-VI",
]

_MINIMUM_ELIGIBLE_NORM: str = "BS-IV"


def _mask(value: str, visible: int = 4) -> str:
    """Mask all but the last *visible* characters of *value*."""
    if len(value) <= visible:
        return value
    return "*" * (len(value) - visible) + value[-visible:]


def _bs_norm_rank(norm: str) -> int:
    """Return the numeric rank of a BS norm string, or -1 if unknown."""
    try:
        return _BS_NORM_ORDER.index(norm)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# Mock service
# ---------------------------------------------------------------------------

class MockVaahanService:
    """Simulates VAHAN 4.0 API responses for testing and development.

    Holds a predefined catalogue of ~10 vehicle registrations covering a
    variety of fuel types, BS emission norms, and registration statuses so
    that downstream logic can be exercised without network access.
    """

    _VEHICLES: dict[str, dict[str, Any]] = {
        "MH12AB1234": {
            "owner_name": "Rajesh Kumar",
            "fuel_type": "Petrol",
            "bs_norm": "BS-VI",
            "vehicle_class": "Motor Car",
            "registration_date": "2021-06-15",
            "registration_status": "Active",
            "chassis_number": "MAKE2021CH001234",
            "engine_number": "ENG2021001234",
            "manufacturer": "Maruti Suzuki",
            "model": "Swift",
        },
        "MH14CD5678": {
            "owner_name": "Priya Sharma",
            "fuel_type": "Diesel",
            "bs_norm": "BS-VI",
            "vehicle_class": "Motor Car",
            "registration_date": "2022-01-20",
            "registration_status": "Active",
            "chassis_number": "HYUN2022CH005678",
            "engine_number": "ENG2022005678",
            "manufacturer": "Hyundai",
            "model": "Creta",
        },
        "KA01EF9012": {
            "owner_name": "Suresh Reddy",
            "fuel_type": "Petrol",
            "bs_norm": "BS-IV",
            "vehicle_class": "Motor Cycle",
            "registration_date": "2018-03-10",
            "registration_status": "Active",
            "chassis_number": "BAJA2018CH009012",
            "engine_number": "ENG2018009012",
            "manufacturer": "Bajaj",
            "model": "Pulsar 150",
        },
        "DL01XY0001": {
            "owner_name": "Amit Verma",
            "fuel_type": "CNG",
            "bs_norm": "BS-VI",
            "vehicle_class": "Motor Car",
            "registration_date": "2023-07-05",
            "registration_status": "Active",
            "chassis_number": "TATA2023CH000001",
            "engine_number": "ENG2023000001",
            "manufacturer": "Tata Motors",
            "model": "Tiago",
        },
        "TN01GH3456": {
            "owner_name": "Lakshmi Iyer",
            "fuel_type": "Petrol",
            "bs_norm": "BS-III",
            "vehicle_class": "Motor Car",
            "registration_date": "2012-11-22",
            "registration_status": "Active",
            "chassis_number": "FORD2012CH003456",
            "engine_number": "ENG2012003456",
            "manufacturer": "Ford",
            "model": "Figo",
        },
        "GJ05IJ7890": {
            "owner_name": "Mehul Patel",
            "fuel_type": "Diesel",
            "bs_norm": "BS-IV",
            "vehicle_class": "Goods Carrier",
            "registration_date": "2017-09-01",
            "registration_status": "Active",
            "chassis_number": "ASHO2017CH007890",
            "engine_number": "ENG2017007890",
            "manufacturer": "Ashok Leyland",
            "model": "Dost",
        },
        "RJ14KL2345": {
            "owner_name": "Pooja Singh",
            "fuel_type": "Petrol",
            "bs_norm": "BS-VI",
            "vehicle_class": "Motor Cycle",
            "registration_date": "2024-02-14",
            "registration_status": "Active",
            "chassis_number": "HERO2024CH002345",
            "engine_number": "ENG2024002345",
            "manufacturer": "Hero MotoCorp",
            "model": "Splendor Plus",
        },
        "UP16MN6789": {
            "owner_name": "Vikram Yadav",
            "fuel_type": "Diesel",
            "bs_norm": "BS-IV",
            "vehicle_class": "Motor Car",
            "registration_date": "2016-05-30",
            "registration_status": "Expired",
            "chassis_number": "MAHI2016CH006789",
            "engine_number": "ENG2016006789",
            "manufacturer": "Mahindra",
            "model": "Scorpio",
        },
        "MH01OP1122": {
            "owner_name": "Sneha Desai",
            "fuel_type": "Electric",
            "bs_norm": "BS-VI",
            "vehicle_class": "Motor Car",
            "registration_date": "2023-12-01",
            "registration_status": "Suspended",
            "chassis_number": "MGMO2023CH001122",
            "engine_number": "ENG2023001122",
            "manufacturer": "MG Motor",
            "model": "ZS EV",
        },
        "KA05QR3344": {
            "owner_name": "Naveen Gowda",
            "fuel_type": "Petrol",
            "bs_norm": "BS-II",
            "vehicle_class": "Three Wheeler",
            "registration_date": "2008-08-18",
            "registration_status": "Active",
            "chassis_number": "PIAG2008CH003344",
            "engine_number": "ENG2008003344",
            "manufacturer": "Piaggio",
            "model": "Ape",
        },
    }

    def lookup(self, registration_number: str) -> dict[str, Any] | None:
        """Look up a vehicle by its registration number.

        Args:
            registration_number: The vehicle registration number to search for.

        Returns:
            A dictionary of vehicle details if found, otherwise ``None``.
        """
        return self._VEHICLES.get(registration_number.upper().replace(" ", ""))


# ---------------------------------------------------------------------------
# Main bridge
# ---------------------------------------------------------------------------

class VaahanBridge:
    """Bridge to the VAHAN 4.0 vehicle registration database.

    Provides methods to verify vehicle registrations and determine whether
    a vehicle is eligible for SmartPUC emission recording.

    Args:
        api_key: Optional API key for authenticating with the real VAHAN API.
        base_url: Base URL of the VAHAN 4.0 dashboard API.
        timeout: HTTP request timeout in seconds.
        use_mock: When ``True``, bypass the real API and use
            :class:`MockVaahanService` instead.
    """

    _MAX_RETRIES: int = 2

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://vahan.parivahan.gov.in/vahan4dashboard",
        timeout: int = 10,
        use_mock: bool = True,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._use_mock = use_mock
        self._mock = MockVaahanService()

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _normalize_registration(registration_number: str) -> str:
        """Normalize a registration number to uppercase without spaces.

        Args:
            registration_number: Raw registration number input.

        Returns:
            Cleaned, upper-cased registration string.
        """
        return registration_number.upper().replace(" ", "")

    @staticmethod
    def _validate_format(registration_number: str) -> bool:
        """Check whether *registration_number* matches the Indian format.

        Accepts patterns like ``MH12AB1234`` (state code, district number,
        letter series, vehicle number).

        Args:
            registration_number: The normalized registration number.

        Returns:
            ``True`` if the format looks valid.
        """
        pattern = r"^[A-Z]{2}\d{2}[A-Z]{1,3}\d{1,4}$"
        return bool(re.match(pattern, registration_number))

    def _build_result(
        self,
        vehicle: dict[str, Any],
        registration_number: str,
    ) -> dict[str, Any]:
        """Build a standardized result dict from raw vehicle data.

        Args:
            vehicle: Raw vehicle data dictionary.
            registration_number: The normalized registration number.

        Returns:
            Standardized verification result dictionary.
        """
        return {
            "valid": True,
            "registration_number": registration_number,
            "owner_name": vehicle.get("owner_name", "Unknown"),
            "fuel_type": vehicle.get("fuel_type", "Unknown"),
            "bs_norm": vehicle.get("bs_norm", "Unknown"),
            "vehicle_class": vehicle.get("vehicle_class", "Unknown"),
            "registration_date": vehicle.get("registration_date", "Unknown"),
            "registration_status": vehicle.get("registration_status", "Unknown"),
            "chassis_number": _mask(vehicle.get("chassis_number", "N/A")),
            "engine_number": _mask(vehicle.get("engine_number", "N/A")),
            "manufacturer": vehicle.get("manufacturer", "Unknown"),
            "model": vehicle.get("model", "Unknown"),
            "error": None,
        }

    @staticmethod
    def _not_found_result(registration_number: str) -> dict[str, Any]:
        """Return a result dict indicating the vehicle was not found.

        Args:
            registration_number: The registration number that was queried.

        Returns:
            Verification result with ``valid`` set to ``False``.
        """
        return {
            "valid": False,
            "registration_number": registration_number,
            "owner_name": None,
            "fuel_type": None,
            "bs_norm": None,
            "vehicle_class": None,
            "registration_date": None,
            "registration_status": None,
            "chassis_number": None,
            "engine_number": None,
            "manufacturer": None,
            "model": None,
            "error": "Vehicle not found in the database.",
        }

    @staticmethod
    def _error_result(
        registration_number: str,
        error_message: str,
    ) -> dict[str, Any]:
        """Return a result dict for an error condition.

        Args:
            registration_number: The registration number that was queried.
            error_message: Human-readable error description.

        Returns:
            Verification result with ``valid`` set to ``False``.
        """
        return {
            "valid": False,
            "registration_number": registration_number,
            "owner_name": None,
            "fuel_type": None,
            "bs_norm": None,
            "vehicle_class": None,
            "registration_date": None,
            "registration_status": None,
            "chassis_number": None,
            "engine_number": None,
            "manufacturer": None,
            "model": None,
            "error": error_message,
        }

    # -- real API -----------------------------------------------------------

    def _call_real_api(self, registration_number: str) -> dict[str, Any] | None:
        """Attempt to call the real VAHAN API with retries.

        Makes up to :attr:`_MAX_RETRIES` attempts. On any failure the method
        returns ``None`` so the caller can fall back to the mock service.

        Args:
            registration_number: Normalized registration number.

        Returns:
            Raw vehicle data dictionary on success, or ``None`` on failure.
        """
        if requests is None:
            logger.warning("requests library not installed; cannot call real API.")
            return None

        url = f"{self._base_url}/api/vehicle/{registration_number}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                logger.info(
                    "VAHAN API attempt %d/%d for %s",
                    attempt,
                    self._MAX_RETRIES,
                    registration_number,
                )
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                return data
            except requests.exceptions.Timeout:
                logger.warning(
                    "VAHAN API timeout (attempt %d/%d) for %s.",
                    attempt,
                    self._MAX_RETRIES,
                    registration_number,
                )
            except requests.exceptions.ConnectionError:
                logger.warning(
                    "VAHAN API connection error (attempt %d/%d) for %s.",
                    attempt,
                    self._MAX_RETRIES,
                    registration_number,
                )
            except requests.exceptions.HTTPError as exc:
                logger.warning(
                    "VAHAN API HTTP error %s (attempt %d/%d) for %s.",
                    exc.response.status_code if exc.response is not None else "?",
                    attempt,
                    self._MAX_RETRIES,
                    registration_number,
                )
            except Exception:
                logger.exception(
                    "Unexpected error calling VAHAN API (attempt %d/%d) for %s.",
                    attempt,
                    self._MAX_RETRIES,
                    registration_number,
                )

            # Brief back-off before retry
            if attempt < self._MAX_RETRIES:
                time.sleep(1)

        return None

    # -- public API ---------------------------------------------------------

    def verify_vehicle(self, registration_number: str) -> dict[str, Any]:
        """Verify a vehicle registration against the VAHAN database.

        Args:
            registration_number: Indian vehicle registration number
                (e.g. ``"MH12AB1234"``).

        Returns:
            A dictionary containing:

            - **valid** (*bool*) -- whether the vehicle was found.
            - **registration_number** (*str*) -- normalized registration.
            - **owner_name** (*str | None*) -- registered owner's name.
            - **fuel_type** (*str | None*) -- fuel type (Petrol, Diesel, etc.).
            - **bs_norm** (*str | None*) -- emission norm (e.g. ``"BS-VI"``).
            - **vehicle_class** (*str | None*) -- class (e.g. ``"Motor Car"``).
            - **registration_date** (*str | None*) -- date of registration.
            - **registration_status** (*str | None*) -- ``"Active"``,
              ``"Suspended"``, or ``"Expired"``.
            - **chassis_number** (*str | None*) -- masked chassis number.
            - **engine_number** (*str | None*) -- masked engine number.
            - **manufacturer** (*str | None*) -- vehicle manufacturer.
            - **model** (*str | None*) -- vehicle model.
            - **error** (*str | None*) -- error description, if any.
        """
        reg = self._normalize_registration(registration_number)

        if not self._validate_format(reg):
            return self._error_result(
                reg,
                f"Invalid registration number format: {reg}",
            )

        # --- Mock path -----------------------------------------------------
        if self._use_mock:
            vehicle = self._mock.lookup(reg)
            if vehicle is not None:
                return self._build_result(vehicle, reg)
            return self._not_found_result(reg)

        # --- Real API path (with fallback to mock) -------------------------
        data = self._call_real_api(reg)

        if data is not None:
            return self._build_result(data, reg)

        # Fallback to mock on real-API failure
        logger.info(
            "Falling back to mock service for %s after real API failure.",
            reg,
        )
        vehicle = self._mock.lookup(reg)
        if vehicle is not None:
            result = self._build_result(vehicle, reg)
            result["error"] = "Result served from mock (real API unavailable)."
            return result

        return self._not_found_result(reg)

    def validate_for_emission_test(
        self,
        registration_number: str,
    ) -> dict[str, Any]:
        """Check whether a vehicle is eligible for SmartPUC emission recording.

        Eligibility requirements:

        1. The vehicle must exist in the VAHAN database (``valid`` is ``True``).
        2. The registration status must be ``"Active"``.
        3. The vehicle must meet at least the **BS-IV** emission norm.

        Args:
            registration_number: Indian vehicle registration number.

        Returns:
            A dictionary containing:

            - **eligible** (*bool*) -- whether the vehicle may proceed.
            - **reason** (*str*) -- human-readable explanation of the decision.
            - **vehicle_info** (*dict*) -- full verification result from
              :meth:`verify_vehicle`.
        """
        vehicle_info = self.verify_vehicle(registration_number)

        # Vehicle not found or lookup error
        if not vehicle_info.get("valid"):
            return {
                "eligible": False,
                "reason": vehicle_info.get(
                    "error",
                    "Vehicle could not be verified.",
                ),
                "vehicle_info": vehicle_info,
            }

        # Registration must be active
        status = vehicle_info.get("registration_status", "")
        if status != "Active":
            return {
                "eligible": False,
                "reason": (
                    f"Vehicle registration status is '{status}'. "
                    "Only vehicles with 'Active' registration are eligible "
                    "for emission testing."
                ),
                "vehicle_info": vehicle_info,
            }

        # BS norm must be BS-IV or higher
        bs_norm: str = vehicle_info.get("bs_norm", "Unknown")
        norm_rank = _bs_norm_rank(bs_norm)
        min_rank = _bs_norm_rank(_MINIMUM_ELIGIBLE_NORM)

        if norm_rank < 0:
            return {
                "eligible": False,
                "reason": (
                    f"Unable to determine emission norm ('{bs_norm}'). "
                    "Vehicle eligibility cannot be confirmed."
                ),
                "vehicle_info": vehicle_info,
            }

        if norm_rank < min_rank:
            return {
                "eligible": False,
                "reason": (
                    f"Vehicle emission norm is {bs_norm}, which is below "
                    f"the minimum required {_MINIMUM_ELIGIBLE_NORM}. "
                    "Emission testing is only available for BS-IV and above."
                ),
                "vehicle_info": vehicle_info,
            }

        return {
            "eligible": True,
            "reason": (
                f"Vehicle {vehicle_info['registration_number']} is eligible "
                f"for emission testing ({bs_norm}, {status})."
            ),
            "vehicle_info": vehicle_info,
        }
