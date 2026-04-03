"""
Ensemble fraud detection system for OBD-II data tampering detection.

This module implements a three-component ensemble approach combining
physics-based validation, statistical anomaly detection, and temporal
consistency checking to identify fraudulent or tampered OBD-II readings.

References:
    Liu, F. T., Ting, K. M., & Zhou, Z.-H. (2008). "Isolation Forest."
    In Proceedings of the IEEE International Conference on Data Mining (ICDM).

    Kwon, S., et al. (2021). "CAN Bus Anomaly Detection."
    IEEE Transactions on Information Forensics and Security (TIFS).
"""

from __future__ import annotations

from collections import deque
from typing import Any

try:
    from sklearn.ensemble import IsolationForest as _IsolationForest

    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False


class PhysicsConstraintValidator:
    """Validate OBD-II readings against hard physics rules.

    Each reading is checked against a fixed set of physical constraints.
    Any single violation forces the fraud score to be at least 0.5.
    """

    _NUM_CHECKS = 7

    def validate(self, reading: dict) -> tuple[float, list[str]]:
        """Validate a single OBD-II reading against physics constraints.

        Args:
            reading: Dictionary containing OBD-II sensor values.  Expected
                keys include ``speed``, ``rpm``, ``fuel_rate``, ``vsp``,
                ``acceleration``, and ``prev_speed``.

        Returns:
            A tuple of (violation_score, violation_descriptions) where
            violation_score is a float in [0.0, 1.0] and
            violation_descriptions is a list of human-readable strings
            describing each detected violation.
        """
        violations: list[str] = []

        speed = reading.get("speed", 0.0)
        rpm = reading.get("rpm", 0.0)
        fuel_rate = reading.get("fuel_rate", 0.0)
        vsp = reading.get("vsp", 0.0)
        acceleration = reading.get("acceleration", 0.0)

        # 1. RPM cannot be 0 while speed > 5 km/h
        if rpm == 0 and speed > 5:
            violations.append(
                f"RPM is 0 while speed is {speed} km/h (> 5 km/h)"
            )

        # 2. Fuel rate cannot be < 0.5 L/100km at VSP > 10 W/kg
        if fuel_rate < 0.5 and vsp > 10:
            violations.append(
                f"Fuel rate {fuel_rate} L/100km is below 0.5 while "
                f"VSP is {vsp} W/kg (> 10)"
            )

        # 3. RPM must be within bounds for speed (gear ratio check)
        if speed > 10:
            min_rpm = speed * 15
            max_rpm = speed * 80
            if rpm < min_rpm or rpm > max_rpm:
                violations.append(
                    f"RPM {rpm} is out of bounds [{min_rpm}, {max_rpm}] "
                    f"for speed {speed} km/h"
                )

        # 4. Speed change > 72 km/h in 5 seconds (accel > 4 m/s^2)
        if abs(acceleration) > 4:
            violations.append(
                f"Acceleration {acceleration} m/s^2 exceeds physical "
                f"limit of 4 m/s^2 (equivalent to 72 km/h in 5 s)"
            )

        # 5. Negative fuel rate
        if fuel_rate < 0:
            violations.append(f"Negative fuel rate: {fuel_rate} L/100km")

        # 6. RPM > 7000
        if rpm > 7000:
            violations.append(f"RPM {rpm} exceeds maximum of 7000")

        # 7. Speed > 250 km/h
        if speed > 250:
            violations.append(f"Speed {speed} km/h exceeds maximum of 250")

        # Score calculation
        score = len(violations) / self._NUM_CHECKS
        if violations:
            score = max(score, 0.5)

        return score, violations


class IsolationForestDetector:
    """Statistical anomaly detector based on Isolation Forest.

    Uses a set of engineered features derived from OBD-II readings to
    detect statistically unusual observations that may indicate tampering.
    """

    _FEATURE_NAMES = [
        "speed",
        "rpm",
        "fuel_rate",
        "acceleration",
        "co2",
        "vsp",
        "fuel_efficiency",
        "rpm_speed_ratio",
    ]

    def __init__(self) -> None:
        """Initialise the detector.  The model remains un-fitted until
        :meth:`fit` is called explicitly."""
        self._model: Any | None = None
        self._is_fitted: bool = False

    def _extract_features(self, reading: dict) -> list[float]:
        """Extract the feature vector from a single reading.

        Args:
            reading: Dictionary of OBD-II sensor values.

        Returns:
            A list of floats representing the feature vector.
        """
        speed = reading.get("speed", 0.0)
        rpm = reading.get("rpm", 0.0)
        fuel_rate = reading.get("fuel_rate", 0.0)
        acceleration = reading.get("acceleration", 0.0)
        co2 = reading.get("co2", 0.0)
        vsp = reading.get("vsp", 0.0)

        fuel_efficiency = co2 / speed if speed > 0 else 0.0
        rpm_speed_ratio = rpm / speed if speed > 0 else 0.0

        return [
            speed,
            rpm,
            fuel_rate,
            acceleration,
            co2,
            vsp,
            fuel_efficiency,
            rpm_speed_ratio,
        ]

    def fit(self, historical_data: list[dict]) -> None:
        """Train the Isolation Forest model on historical OBD-II data.

        Args:
            historical_data: A list of reading dictionaries used as the
                training set for the Isolation Forest.
        """
        if not _HAS_SKLEARN:
            return

        features = [self._extract_features(r) for r in historical_data]
        self._model = _IsolationForest(
            contamination=0.05,
            n_estimators=100,
            random_state=42,
        )
        self._model.fit(features)
        self._is_fitted = True

    def predict(self, reading: dict) -> float:
        """Return an anomaly score for the given reading.

        Args:
            reading: Dictionary of OBD-II sensor values.

        Returns:
            A float in [0.0, 1.0] where higher values indicate greater
            anomaly.  Returns 0.0 if the model has not been fitted or
            scikit-learn is unavailable.
        """
        if not self._is_fitted or self._model is None:
            return 0.0

        features = [self._extract_features(reading)]
        # decision_function returns negative values for anomalies
        raw_score = self._model.decision_function(features)[0]
        # Convert: more negative → higher anomaly score, clamp to [0, 1]
        anomaly_score = max(0.0, min(1.0, -raw_score))
        return anomaly_score


class TemporalConsistencyChecker:
    """Check temporal consistency of sequential OBD-II readings.

    Maintains a rolling window of the last 10 readings and flags
    physically impossible transitions or replay-attack patterns.
    """

    _WINDOW_SIZE = 10

    def __init__(self) -> None:
        """Initialise the checker with an empty reading window."""
        self._window: deque[dict] = deque(maxlen=self._WINDOW_SIZE)

    def update_and_check(self, reading: dict) -> tuple[float, list[str]]:
        """Add a new reading and check for temporal anomalies.

        Args:
            reading: Dictionary of OBD-II sensor values.  Expected keys
                include ``speed``, ``rpm``, ``fuel_rate``, and
                ``timestamp`` (optional, seconds).

        Returns:
            A tuple of (score, issues) where score is a float in
            [0.0, 1.0] and issues is a list of human-readable strings
            describing each detected temporal anomaly.
        """
        issues: list[str] = []
        num_checks = 4

        if self._window:
            prev = self._window[-1]

            # Time delta (default to 1 second if no timestamps provided)
            dt = reading.get("timestamp", 0) - prev.get("timestamp", -1)
            if dt <= 0:
                dt = 1.0

            # 1. Speed trajectory physically possible
            speed_change = abs(
                reading.get("speed", 0.0) - prev.get("speed", 0.0)
            )
            # Max ~4 m/s^2 → ~14.4 km/h per second
            max_speed_change = 14.4 * dt
            if speed_change > max_speed_change:
                issues.append(
                    f"Speed changed by {speed_change:.1f} km/h in "
                    f"{dt:.1f}s (max plausible: {max_speed_change:.1f} km/h)"
                )

            # 2. Sudden impossible RPM jump (>3000 RPM in 1 second)
            rpm_change = abs(
                reading.get("rpm", 0.0) - prev.get("rpm", 0.0)
            )
            max_rpm_change = 3000 * dt
            if rpm_change > max_rpm_change:
                issues.append(
                    f"RPM changed by {rpm_change:.0f} in {dt:.1f}s "
                    f"(max plausible: {max_rpm_change:.0f})"
                )

            # 3. Fuel rate consistency (no sudden 0 to max jumps)
            fuel_change = abs(
                reading.get("fuel_rate", 0.0) - prev.get("fuel_rate", 0.0)
            )
            if fuel_change > 20:
                issues.append(
                    f"Fuel rate jumped by {fuel_change:.1f} L/100km "
                    f"in a single step"
                )

            # 4. Repeated exact identical readings (replay attack)
            identical_count = sum(
                1
                for past in self._window
                if (
                    past.get("speed") == reading.get("speed")
                    and past.get("rpm") == reading.get("rpm")
                    and past.get("fuel_rate") == reading.get("fuel_rate")
                )
            )
            if identical_count >= 3:
                issues.append(
                    f"Reading is identical to {identical_count} of the "
                    f"last {len(self._window)} readings (possible replay "
                    f"attack)"
                )
        else:
            # First reading; nothing to compare against
            pass

        self._window.append(reading)

        score = len(issues) / num_checks if issues else 0.0
        score = min(score, 1.0)
        return score, issues


class FraudDetector:
    """Ensemble fraud detector combining physics, statistical, and temporal checks.

    The final fraud score is a weighted combination of the three component
    scores.  Default weights emphasise the physics validator (0.50),
    followed by the Isolation Forest detector (0.35) and the temporal
    consistency checker (0.15).
    """

    def __init__(
        self,
        physics_weight: float = 0.50,
        isolation_weight: float = 0.35,
        temporal_weight: float = 0.15,
    ) -> None:
        """Initialise the ensemble detector.

        Args:
            physics_weight: Weight for the physics constraint validator.
            isolation_weight: Weight for the Isolation Forest detector.
            temporal_weight: Weight for the temporal consistency checker.
        """
        self._physics_weight = physics_weight
        self._isolation_weight = isolation_weight
        self._temporal_weight = temporal_weight

        self._physics = PhysicsConstraintValidator()
        self._isolation = IsolationForestDetector()
        self._temporal = TemporalConsistencyChecker()

    def fit(self, historical_data: list[dict]) -> None:
        """Train the Isolation Forest component on historical data.

        Args:
            historical_data: A list of OBD-II reading dictionaries.
        """
        self._isolation.fit(historical_data)

    def update(self, reading: dict) -> None:
        """Update the temporal consistency checker with a new reading.

        Args:
            reading: Dictionary of OBD-II sensor values.
        """
        self._temporal.update_and_check(reading)

    def analyze(self, reading: dict) -> dict:
        """Analyse a single OBD-II reading for potential fraud.

        This method runs all three detection components, combines their
        scores using the configured weights, and returns a comprehensive
        result dictionary.

        Args:
            reading: Dictionary of OBD-II sensor values.

        Returns:
            A dictionary with the following keys:

            - **fraud_score** (*float*): Combined score in [0.0, 1.0].
            - **is_fraud** (*bool*): ``True`` if fraud_score >= 0.65.
            - **severity** (*str*): ``"LOW"`` (< 0.35), ``"MEDIUM"``
              (0.35 -- 0.65), or ``"HIGH"`` (>= 0.65).
            - **components** (*dict*): Individual scores from each
              component (``physics``, ``isolation``, ``temporal``).
            - **violations** (*list[str]*): All violation descriptions
              aggregated from every component.
        """
        physics_score, physics_violations = self._physics.validate(reading)
        isolation_score = self._isolation.predict(reading)
        temporal_score, temporal_issues = self._temporal.update_and_check(
            reading
        )

        fraud_score = (
            self._physics_weight * physics_score
            + self._isolation_weight * isolation_score
            + self._temporal_weight * temporal_score
        )
        fraud_score = min(fraud_score, 1.0)

        if fraud_score >= 0.65:
            severity = "HIGH"
        elif fraud_score >= 0.35:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        all_violations = physics_violations + temporal_issues

        return {
            "fraud_score": fraud_score,
            "is_fraud": fraud_score >= 0.65,
            "severity": severity,
            "components": {
                "physics": physics_score,
                "isolation": isolation_score,
                "temporal": temporal_score,
            },
            "violations": all_violations,
        }
