"""
Ensemble fraud detection system for OBD-II data tampering detection.

This module implements a four-component ensemble (physics, Isolation
Forest, temporal consistency, Page-Hinkley drift) for detecting
fraudulent or tampered OBD-II readings. In addition:

* :class:`FraudReasonCode` exposes machine-readable reason codes so every
  score returned by :meth:`FraudDetector.analyze` carries an actionable
  diagnostic list (audit §12C / 13A #2).
* :class:`PerVINBaseline` is an **opt-in** 5th signal maintaining a
  per-VIN EWMA baseline (audit 13A #1). It is additive: when
  ``PER_VIN_BASELINE_ENABLED=1`` is set in the environment AND
  ``vehicle_id`` is supplied to :meth:`FraudDetector.analyze`, a
  per-VIN z-score ``> 3`` bumps the final fraud score by at most
  ``+0.10``. This preserves the 4-way ensemble weight invariant
  (0.45 + 0.30 + 0.15 + 0.10 = 1.0).

References:
    Liu, F. T., Ting, K. M., & Zhou, Z.-H. (2008). "Isolation Forest."
    In Proceedings of the IEEE International Conference on Data Mining (ICDM).

    Kwon, S., et al. (2021). "CAN Bus Anomaly Detection."
    IEEE Transactions on Information Forensics and Security (TIFS).
"""

from __future__ import annotations

import math
import os
from collections import deque
from enum import Enum
from typing import Any, Optional


class FraudReasonCode(str, Enum):
    """Machine-readable reason codes describing *why* a fraud score fired.

    Every call to :meth:`FraudDetector.analyze` returns a ``reason_codes``
    list whose entries are drawn from this enum. This turns the scalar
    fraud score into an actionable diagnostic for RTO auditors and the
    adversarial-robustness discussion in the paper (audit §12C / 13A #2).
    """

    # Physics validator
    PHYSICS_RPM_ZERO_SPEED_NONZERO = "PHYSICS_RPM_ZERO_SPEED_NONZERO"
    PHYSICS_VSP_FUEL_MISMATCH = "PHYSICS_VSP_FUEL_MISMATCH"
    PHYSICS_RPM_SPEED_BOUNDS_VIOLATION = "PHYSICS_RPM_SPEED_BOUNDS_VIOLATION"
    PHYSICS_ACCEL_CAP_EXCEEDED = "PHYSICS_ACCEL_CAP_EXCEEDED"
    PHYSICS_FUEL_NEGATIVE = "PHYSICS_FUEL_NEGATIVE"
    PHYSICS_RPM_REDLINE = "PHYSICS_RPM_REDLINE"
    PHYSICS_SPEED_MAX = "PHYSICS_SPEED_MAX"
    # Isolation Forest
    ISOLATION_FOREST_OUTLIER = "ISOLATION_FOREST_OUTLIER"
    # Temporal
    TEMPORAL_SPEED_JUMP = "TEMPORAL_SPEED_JUMP"
    TEMPORAL_RPM_JUMP = "TEMPORAL_RPM_JUMP"
    TEMPORAL_FUEL_JUMP = "TEMPORAL_FUEL_JUMP"
    TEMPORAL_REPLAY_STREAK = "TEMPORAL_REPLAY_STREAK"
    # Drift (Page-Hinkley)
    DRIFT_UPWARD_CES = "DRIFT_UPWARD_CES"
    DRIFT_DOWNWARD_CES = "DRIFT_DOWNWARD_CES"
    # Per-VIN baseline (opt-in, audit 13A #1)
    PER_VIN_BASELINE_DRIFT = "PER_VIN_BASELINE_DRIFT"
    # Sentinel
    NONE = "NONE"

try:
    from sklearn.ensemble import IsolationForest as _IsolationForest

    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False


class PhysicsConstraintValidator:
    """Validate OBD-II readings against hard physics rules.

    Each reading is checked against a fixed set of physical constraints.
    Any single violation forces the fraud score to be at least 0.7.
    """

    _NUM_CHECKS = 7

    def validate(
        self, reading: dict
    ) -> tuple[float, list[str], list[str]]:
        """Validate a single OBD-II reading against physics constraints.

        Args:
            reading: Dictionary containing OBD-II sensor values.  Expected
                keys include ``speed``, ``rpm``, ``fuel_rate``, ``vsp``,
                ``acceleration``, and ``prev_speed``.

        Returns:
            A tuple ``(violation_score, violation_descriptions, reason_codes)``
            where ``violation_score`` is a float in ``[0.0, 1.0]``,
            ``violation_descriptions`` is a list of human-readable strings
            and ``reason_codes`` is a list of :class:`FraudReasonCode`
            string values explaining which rules fired (audit §12C).
        """
        violations: list[str] = []
        codes: list[str] = []

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
            codes.append(FraudReasonCode.PHYSICS_RPM_ZERO_SPEED_NONZERO.value)

        # 2. Fuel rate cannot be < 0.5 L/100km at VSP > 10 W/kg
        if fuel_rate < 0.5 and vsp > 10:
            violations.append(
                f"Fuel rate {fuel_rate} L/100km is below 0.5 while "
                f"VSP is {vsp} W/kg (> 10)"
            )
            codes.append(FraudReasonCode.PHYSICS_VSP_FUEL_MISMATCH.value)

        # 3. RPM must be within bounds for speed (gear ratio check)
        if speed > 10:
            min_rpm = speed * 15
            max_rpm = speed * 80
            if rpm < min_rpm or rpm > max_rpm:
                violations.append(
                    f"RPM {rpm} is out of bounds [{min_rpm}, {max_rpm}] "
                    f"for speed {speed} km/h"
                )
                codes.append(
                    FraudReasonCode.PHYSICS_RPM_SPEED_BOUNDS_VIOLATION.value
                )

        # 4. Speed change > 72 km/h in 5 seconds (accel > 4 m/s^2)
        if abs(acceleration) > 4:
            violations.append(
                f"Acceleration {acceleration} m/s^2 exceeds physical "
                f"limit of 4 m/s^2 (equivalent to 72 km/h in 5 s)"
            )
            codes.append(FraudReasonCode.PHYSICS_ACCEL_CAP_EXCEEDED.value)

        # 5. Negative fuel rate
        if fuel_rate < 0:
            violations.append(f"Negative fuel rate: {fuel_rate} L/100km")
            codes.append(FraudReasonCode.PHYSICS_FUEL_NEGATIVE.value)

        # 6. RPM > 7000
        if rpm > 7000:
            violations.append(f"RPM {rpm} exceeds maximum of 7000")
            codes.append(FraudReasonCode.PHYSICS_RPM_REDLINE.value)

        # 7. Speed > 250 km/h
        if speed > 250:
            violations.append(f"Speed {speed} km/h exceeds maximum of 250")
            codes.append(FraudReasonCode.PHYSICS_SPEED_MAX.value)

        # Score calculation
        score = len(violations) / self._NUM_CHECKS
        if violations:
            score = max(score, 0.7)

        return score, violations, codes


class IsolationForestDetector:
    """Statistical anomaly detector based on Isolation Forest.

    Uses a set of engineered features derived from OBD-II readings to
    detect statistically unusual observations that may indicate tampering.
    """

    _IF_OUTLIER_THRESHOLD = 0.30  # See reason_codes_for() docstring for rationale

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

    def __init__(self, contamination: float = 0.05) -> None:
        """Initialise the detector.  The model remains un-fitted until
        :meth:`fit` is called explicitly.

        Args:
            contamination: contamination rate for IsolationForest (fraction
                of expected outliers). Default 0.05 per EPA MOVES3 baseline.
                Adjust if actual fraud rate differs.
        """
        self._contamination = contamination
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
        # Isolation Forest (Liu, Ting & Zhou, 2008, ICDM)
        self._model = _IsolationForest(
            contamination=self._contamination,
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

    def reason_codes_for(self, score: float) -> list[str]:
        """Return reason codes if the IF anomaly score exceeds the outlier threshold.

        The threshold of 0.30 is set to align with the MEDIUM severity cutoff
        on a single ensemble component. At default ensemble weights (IF weight = 0.30),
        an IF score of 0.30 contributes 0.30 x 0.30 = 0.09 to the final fraud_score,
        which alone is insufficient to trigger the 0.50 ensemble threshold.

        This threshold fires the ISOLATION_FOREST_OUTLIER reason code for diagnostic
        purposes -- it indicates the reading is unusual in feature space, even if the
        overall ensemble score stays below the fraud threshold. Users who adjust
        ensemble weights must recalibrate this threshold proportionally.

        Args:
            score: IF anomaly score in [0.0, 1.0]

        Returns:
            List of FraudReasonCode values
        """
        if score >= self._IF_OUTLIER_THRESHOLD:
            return [FraudReasonCode.ISOLATION_FOREST_OUTLIER.value]
        return []


class TemporalConsistencyChecker:
    """Check temporal consistency of sequential OBD-II readings.

    Maintains a rolling window of the last 60 readings and flags
    physically impossible transitions or replay-attack patterns.
    """

    _WINDOW_SIZE = 60

    def __init__(self) -> None:
        """Initialise the checker with an empty reading window."""
        self._window: deque[dict] = deque(maxlen=self._WINDOW_SIZE)
        # Reason codes from the most recent call to ``update_and_check``.
        # Stored as instance state (instead of widening the return tuple)
        # so the pre-existing ``(score, issues)`` call signature used by
        # the unit tests continues to work unchanged — audit §12C fix.
        self._last_reason_codes: list[str] = []

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
        codes: list[str] = []
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
                codes.append(FraudReasonCode.TEMPORAL_SPEED_JUMP.value)

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
                codes.append(FraudReasonCode.TEMPORAL_RPM_JUMP.value)

            # 3. Fuel rate consistency (no sudden 0 to max jumps)
            fuel_change = abs(
                reading.get("fuel_rate", 0.0) - prev.get("fuel_rate", 0.0)
            )
            if fuel_change > 20:
                issues.append(
                    f"Fuel rate jumped by {fuel_change:.1f} L/100km "
                    f"in a single step"
                )
                codes.append(FraudReasonCode.TEMPORAL_FUEL_JUMP.value)

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
            if identical_count >= 2:
                issues.append(
                    f"Reading is identical to {identical_count} of the "
                    f"last {len(self._window)} readings (possible replay "
                    f"attack)"
                )
                codes.append(FraudReasonCode.TEMPORAL_REPLAY_STREAK.value)
        else:
            # First reading; nothing to compare against
            pass

        self._window.append(reading)

        score = len(issues) / num_checks if issues else 0.0
        score = min(score, 1.0)
        self._last_reason_codes = codes
        return score, issues


class PageHinkleyDriftDetector:
    """
    Page-Hinkley change-point test for slow sensor drift.

    The Page-Hinkley (PH) test is a sequential statistical test that
    detects small-but-sustained upward drifts in a stochastic signal.
    It catches exactly the attack class the temporal consistency checker
    cannot see: an adversary who nudges a pollutant reading downward by a
    tiny amount every day for weeks (scaling CO2 × 0.999 per reading, say)
    until the vehicle falsely passes emissions.

    Reference
    ---------
    E. S. Page, "Continuous Inspection Schemes", Biometrika 41(1-2):100-115,
    1954. (Applied to concept drift in data-stream mining by Gama et al.,
    2004.)

    Test statistic
    --------------
    Given a stream of observations ``x_t`` (here: CES score), track

        m_t = sum_{i=1..t} (x_i - x_bar - delta)
        M_t = min_{i<=t} m_i
        PH_t = m_t - M_t

    When ``PH_t > lambda_threshold`` a drift is flagged. The signed version
    also detects downward drift by running the same test on ``-x_t``.

    Parameters
    ----------
    delta : float
        Magnitude of allowed change before the test starts accumulating
        evidence. Smaller = more sensitive. Default 0.005 (0.5% of CES
        range).
    lambda_threshold : float
        Alarm threshold. Larger = fewer false positives. Default 0.05.
    min_samples : int
        Minimum number of samples before the test can fire, to avoid
        alarm-on-the-first-sample.
    """

    def __init__(
        self,
        delta: float = 0.005,
        lambda_threshold: float = 0.05,
        min_samples: int = 30,
    ) -> None:
        self._delta = delta
        self._lambda = lambda_threshold
        self._min_samples = min_samples
        self.reset()

    def reset(self) -> None:
        """Clear the running state (e.g. after a confirmed drift alarm)."""
        self._n = 0
        self._mean = 0.0
        self._m_up = 0.0        # cumulative sum for upward drift
        self._m_down = 0.0      # cumulative sum for downward drift
        self._min_up = 0.0
        self._max_down = 0.0

    def update(self, value: float) -> tuple[float, str]:
        """
        Feed a new observation and return ``(score, direction)``.

        - ``score`` is the Page-Hinkley statistic scaled to ``[0, 1]``.
          A value < 1 means "no drift detected yet"; exactly 1.0 means
          the alarm threshold was crossed on this observation.
        - ``direction`` is one of ``"none"``, ``"upward"``, ``"downward"``.
          ``"upward"`` drift in CES means the vehicle is getting *dirtier*
          (genuine degradation); ``"downward"`` drift means the readings
          are decreasing unnaturally (possible sensor tampering).
        """
        self._n += 1
        # Running mean (Welford)
        self._mean += (value - self._mean) / self._n

        if self._n < self._min_samples:
            return 0.0, "none"

        # Upward drift: (x - mean) - delta
        self._m_up += (value - self._mean) - self._delta
        self._min_up = min(self._min_up, self._m_up)
        ph_up = self._m_up - self._min_up

        # Downward drift: (mean - x) - delta  (equivalent to running the
        # upward test on -x)
        self._m_down += (self._mean - value) - self._delta
        self._max_down = min(self._max_down, self._m_down)
        ph_down = self._m_down - self._max_down

        ph = max(ph_up, ph_down)
        score = min(1.0, ph / self._lambda) if self._lambda > 0 else 0.0

        if ph_up >= self._lambda and ph_up >= ph_down:
            return score, "upward"
        if ph_down >= self._lambda:
            return score, "downward"
        return score, "none"


class MultiSignalPageHinkleyBank:
    """Parallel bank of Page-Hinkley detectors, one per pollutant channel.

    The single CES-level ``PageHinkleyDriftDetector`` used by the ensemble
    catches drift in the composite score. It misses a specific attack
    class where a vehicle's HC starts drifting at month 3 while CES stays
    flat because CO₂ simultaneously improves (a tuned-engine scenario or
    a partially-failing catalytic converter). This class runs five
    independent Page-Hinkley tests — one per pollutant — so such
    per-channel drifts are surfaced individually.

    Audit-report section 13A #8 (improvement: per-pollutant Page-Hinkley).

    The bank is not part of the ensemble weight sum — its output is
    additive information for the analyst (returned in ``components``
    and ``violations`` lists), not a fourth decision weight.
    """

    # Pollutant keys we monitor. The detector pulls the reading value for
    # each one and normalises to roughly the same scale (value / threshold)
    # before feeding to the PH test — so the δ / λ hyper-parameters are
    # comparable across channels.
    _POLLUTANTS: tuple[tuple[str, float], ...] = (
        ("co2",  120.0),
        ("co",   1.0),
        ("nox",  0.06),
        ("hc",   0.10),
        ("pm25", 0.0045),
    )

    def __init__(
        self,
        delta: float = 0.008,
        lambda_threshold: float = 0.08,
        min_samples: int = 20,
    ) -> None:
        """Initialise one PageHinkleyDriftDetector per pollutant channel."""
        self._detectors: dict[str, PageHinkleyDriftDetector] = {
            name: PageHinkleyDriftDetector(
                delta=delta,
                lambda_threshold=lambda_threshold,
                min_samples=min_samples,
            )
            for name, _ in self._POLLUTANTS
        }

    def update(self, reading: dict) -> tuple[float, list[str], dict[str, dict]]:
        """Feed a new reading and return (max_score, issues, per_channel).

        - ``max_score`` is the highest Page-Hinkley score across all
          pollutant channels, clamped to ``[0, 1]``.
        - ``issues`` is a list of human-readable descriptions of any
          channels that crossed their alarm threshold.
        - ``per_channel`` is a ``{pollutant: {score, direction}}`` dict
          suitable for JSON serialisation into the analyst dashboard.
        """
        max_score = 0.0
        issues: list[str] = []
        per_channel: dict[str, dict] = {}
        for name, threshold in self._POLLUTANTS:
            raw = reading.get(name)
            if raw is None:
                # Try common aliases exposed by emission_engine output.
                alt = reading.get(f"{name}_g_per_km")
                if alt is None:
                    per_channel[name] = {"score": 0.0, "direction": "none"}
                    continue
                raw = alt
            # Normalise to "fraction of BS-VI threshold" so δ/λ are comparable
            # across channels of very different numeric ranges.
            try:
                normalised = float(raw) / float(threshold) if threshold > 0 else 0.0
            except (TypeError, ValueError):
                normalised = 0.0
            score, direction = self._detectors[name].update(normalised)
            per_channel[name] = {"score": round(score, 4), "direction": direction}
            if direction != "none" and score >= 1.0:
                issues.append(
                    f"Per-pollutant Page-Hinkley drift on {name} ({direction}); "
                    f"possible per-channel sensor tampering or ECU degradation"
                )
            if score > max_score:
                max_score = score
        return max_score, issues, per_channel


class PerVINBaseline:
    """Per-VIN EWMA baseline for fraud detection (audit 13A #1).

    The fleet-wide IsolationForest in :class:`IsolationForestDetector` uses
    an averaged baseline across all vehicles. That masks vehicle-specific
    anomalies: a well-maintained 2023 hatchback and a 2005 diesel truck
    have very different "normal" CO2, fuel-rate, and RPM distributions,
    but both would score identically against a fleet baseline.

    This class maintains a per-VIN exponentially-weighted moving average
    (EWMA) of five features and exposes a streaming z-score. A VIN whose
    latest reading deviates by more than ``3σ`` from its own historical
    EWMA is flagged — something the fleet-level IsolationForest cannot
    catch.

    Feature vector (order-sensitive for :meth:`save_state`)::

        [co2, fuel_rate, rpm, speed, fuel_efficiency_proxy]

    where ``fuel_efficiency_proxy = co2 / speed`` when ``speed > 0``,
    else ``0``. Requires at least ``min_samples`` observations before the
    z-score becomes non-zero — stops the first-sample alarm problem.

    Parameters
    ----------
    lam : float
        EWMA decay parameter, ``0 < lam < 1``. Default ``0.99`` (slow
        adaptation, roughly a 100-sample window).
    min_samples : int
        Minimum observations per VIN before :meth:`z_score` returns
        non-zero values. Default ``20``.
    """

    _FEATURES = ("co2", "fuel_rate", "rpm", "speed", "fuel_efficiency_proxy")
    _NUM_FEATURES = len(_FEATURES)

    def __init__(self, lam: float = 0.99, min_samples: int = 20) -> None:
        if not 0.0 < lam < 1.0:
            raise ValueError(f"lam must be in (0, 1); got {lam}")
        self._lam = lam
        self._min_samples = min_samples
        # {vid: {"n": int, "mean": [5 floats], "var": [5 floats]}}
        self._state: dict[str, dict] = {}

    @staticmethod
    def _feature_vector(features: dict) -> list[float]:
        speed = float(features.get("speed", 0.0))
        co2 = float(features.get("co2", 0.0))
        fuel_rate = float(features.get("fuel_rate", 0.0))
        rpm = float(features.get("rpm", 0.0))
        fuel_eff = co2 / speed if speed > 0 else 0.0
        return [co2, fuel_rate, rpm, speed, fuel_eff]

    # Per-feature absolute variance floors. These prevent a genuinely
    # constant stream (e.g. a synthetic test rig feeding identical
    # readings) from producing a zero denominator in :meth:`z_score`.
    # Values correspond to ~(5% of a typical reading)^2 for the engine
    # channels and are deliberately loose so the z-score only fires for
    # clearly out-of-band values when the empirical variance is zero.
    _VAR_FLOORS = (
        36.0,   # co2 (stddev floor ~6 g/km)
        0.25,   # fuel_rate (stddev floor ~0.5 L/100km)
        10000.0,  # rpm (stddev floor ~100 rpm)
        25.0,   # speed (stddev floor ~5 km/h)
        0.01,   # fuel_efficiency_proxy (stddev floor ~0.1)
    )

    def update(self, vid: str, features: dict) -> None:
        """Fold a new reading for ``vid`` into its EWMA baseline.

        Uses the standard exponentially-weighted moving variance (EWMV)
        recursion:

            mean_t = lam * mean_{t-1} + (1 - lam) * x_t
            var_t  = lam * (var_{t-1} + (1 - lam) * (x_t - mean_{t-1})^2)

        (Roberts, 1959 / West, 1979). The ``(1 - lam)`` factor inside
        the variance update is what makes the recursion converge to the
        true variance under stationary inputs.
        """
        vec = self._feature_vector(features)
        s = self._state.get(vid)
        if s is None:
            self._state[vid] = {
                "n": 1,
                "mean": list(vec),
                "var": [0.0] * self._NUM_FEATURES,
            }
            return
        s["n"] += 1
        lam = self._lam
        for i, x in enumerate(vec):
            old_mean = s["mean"][i]
            delta = x - old_mean
            new_mean = lam * old_mean + (1.0 - lam) * x
            s["var"][i] = lam * (s["var"][i] + (1.0 - lam) * delta * delta)
            s["mean"][i] = new_mean

    def z_score(self, vid: str, features: dict) -> float:
        """Return the maximum absolute z-score across the five features.

        Returns ``0.0`` if the VIN has fewer than ``min_samples``
        observations. Capped at ``5.0`` to prevent a single pathological
        reading from dominating the final fraud-score bump.

        When the empirical EWMV for a feature is smaller than the
        per-feature floor (e.g. a perfectly constant training stream),
        the floor is used instead so the z-score still reflects clearly
        out-of-band inputs.
        """
        s = self._state.get(vid)
        if s is None or s["n"] < self._min_samples:
            return 0.0
        vec = self._feature_vector(features)
        best = 0.0
        for i, x in enumerate(vec):
            var = max(s["var"][i], self._VAR_FLOORS[i])
            if var <= 1e-12:
                continue
            z = abs(x - s["mean"][i]) / math.sqrt(var)
            if z > best:
                best = z
        return min(best, 5.0)

    def save_state(self) -> dict:
        """Return a picklable snapshot of the per-VIN state."""
        return {
            "lam": self._lam,
            "min_samples": self._min_samples,
            "state": {
                vid: {"n": s["n"], "mean": list(s["mean"]), "var": list(s["var"])}
                for vid, s in self._state.items()
            },
        }

    def load_state(self, snapshot: dict) -> None:
        """Restore state produced by :meth:`save_state` (in-place)."""
        self._lam = float(snapshot.get("lam", self._lam))
        self._min_samples = int(snapshot.get("min_samples", self._min_samples))
        self._state = {
            vid: {"n": int(s["n"]), "mean": list(s["mean"]), "var": list(s["var"])}
            for vid, s in snapshot.get("state", {}).items()
        }


class FraudDetector:
    """Ensemble fraud detector combining physics, statistical, temporal and drift checks.

    The final fraud score is a weighted combination of four component
    scores.  Default weights emphasise the physics validator (0.45),
    followed by the Isolation Forest detector (0.30), the temporal
    consistency checker (0.15) and the Page-Hinkley drift detector (0.10).
    """

    def __init__(
        self,
        physics_weight: float = 0.45,
        isolation_weight: float = 0.30,
        temporal_weight: float = 0.15,
        drift_weight: float = 0.10,
        if_contamination: float = 0.05,
    ) -> None:
        """Initialise the ensemble detector.

        Args:
            physics_weight: Weight for the physics constraint validator.
            isolation_weight: Weight for the Isolation Forest detector.
            temporal_weight: Weight for the temporal consistency checker.
            drift_weight: Weight for the Page-Hinkley drift detector.
            if_contamination: contamination rate for IsolationForest (fraction
                of expected outliers). Default 0.05 per EPA MOVES3 baseline.
                Adjust if actual fraud rate differs.
        """
        total = physics_weight + isolation_weight + temporal_weight + drift_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Fraud detector weights must sum to 1.0, got {total}"
            )
        self._physics_weight = physics_weight
        self._isolation_weight = isolation_weight
        self._temporal_weight = temporal_weight
        self._drift_weight = drift_weight

        self._physics = PhysicsConstraintValidator()
        self._isolation = IsolationForestDetector(contamination=if_contamination)
        self._temporal = TemporalConsistencyChecker()
        self._drift = PageHinkleyDriftDetector()
        # Opt-in per-VIN baseline (audit 13A #1). Always instantiated so
        # checkpoint round-trips are uniform, but only consulted when both
        # PER_VIN_BASELINE_ENABLED=1 is set AND vehicle_id is supplied.
        self._per_vin_baseline = PerVINBaseline()
        # Parallel per-pollutant drift bank (audit 13A #8). Feeds informational
        # output only — does NOT contribute to the ensemble fraud_score, so
        # adding it cannot regress any existing detection result.
        self._pollutant_drift = MultiSignalPageHinkleyBank()

    def fit(self, historical_data: list[dict]) -> None:
        """Train the Isolation Forest component on historical data.

        Args:
            historical_data: A list of OBD-II reading dictionaries.
        """
        self._isolation.fit(historical_data)

    # ──────────────────────── Checkpoint persistence ────────────────────────
    # Closes audit L-item "Persist fraud-detector checkpoint". Serializing
    # the fitted detector lets the paper ship a frozen, reproducible model
    # (~200 KB pickle), so the evaluation numbers in docs/FRAUD_EVALUATION.md
    # are no longer dependent on re-training at runtime with the current
    # `numpy`/`scikit-learn` seed behaviour.

    _CHECKPOINT_SCHEMA_VERSION: int = 1

    def save_checkpoint(self, path: str | "Path") -> None:  # type: ignore[name-defined]
        """Persist a fitted FraudDetector to a pickle file.

        The pickle holds exactly the four ensemble weights, the fitted
        Isolation Forest estimator (if sklearn is installed), the rolling
        temporal window, and the Page-Hinkley accumulator — everything
        needed to score a fresh reading identically across processes.

        Args:
            path: Destination pickle path. Parent directories are created
                on demand.
        """
        import pickle
        from pathlib import Path as _Path
        payload = {
            "schema_version": self._CHECKPOINT_SCHEMA_VERSION,
            "weights": {
                "physics": self._physics_weight,
                "isolation": self._isolation_weight,
                "temporal": self._temporal_weight,
                "drift": self._drift_weight,
            },
            "isolation_model": getattr(self._isolation, "_model", None),
            "isolation_is_fitted": getattr(self._isolation, "_is_fitted", False),
            "temporal_window": list(self._temporal._window),
            "drift_state": {
                "n": getattr(self._drift, "_n", 0),
                "mean": getattr(self._drift, "_mean", 0.0),
                "m_up": getattr(self._drift, "_m_up", 0.0),
                "m_down": getattr(self._drift, "_m_down", 0.0),
                "min_up": getattr(self._drift, "_min_up", 0.0),
                "max_down": getattr(self._drift, "_max_down", 0.0),
            },
        }
        p = _Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            pickle.dump(payload, f)

    @classmethod
    def load_checkpoint(cls, path: str | "Path") -> "FraudDetector":  # type: ignore[name-defined]
        """Restore a FraudDetector from a pickle written by :meth:`save_checkpoint`.

        Args:
            path: Source pickle path.

        Returns:
            A fully initialised FraudDetector instance with the fitted IF
            model, temporal window, and drift-detector state restored.

        Raises:
            ValueError: If the checkpoint schema version is unsupported.
        """
        import pickle
        with open(path, "rb") as f:
            payload = pickle.load(f)  # nosec B301 — trusted local file
        schema = int(payload.get("schema_version", 0))
        if schema != cls._CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported FraudDetector checkpoint schema {schema}; "
                f"expected {cls._CHECKPOINT_SCHEMA_VERSION}. Regenerate with "
                f"python scripts/build_fraud_checkpoint.py"
            )
        w = payload["weights"]
        det = cls(
            physics_weight=w["physics"],
            isolation_weight=w["isolation"],
            temporal_weight=w["temporal"],
            drift_weight=w["drift"],
        )
        if payload.get("isolation_model") is not None:
            det._isolation._model = payload["isolation_model"]
            det._isolation._is_fitted = bool(payload.get("isolation_is_fitted"))
        # Restore temporal window
        for reading in payload.get("temporal_window", []):
            det._temporal._window.append(reading)
        # Restore Page-Hinkley drift state
        drift_state = payload.get("drift_state", {})
        for attr, key in [
            ("_n", "n"),
            ("_mean", "mean"),
            ("_m_up", "m_up"),
            ("_m_down", "m_down"),
            ("_min_up", "min_up"),
            ("_max_down", "max_down"),
        ]:
            if hasattr(det._drift, attr) and key in drift_state:
                setattr(det._drift, attr, drift_state[key])
        return det

    def update(self, reading: dict) -> None:
        """Update the temporal consistency checker with a new reading.

        Args:
            reading: Dictionary of OBD-II sensor values.
        """
        self._temporal.update_and_check(reading)

    def analyze(self, reading: dict, vehicle_id: Optional[str] = None) -> dict:
        """Analyse a single OBD-II reading for potential fraud.

        This method runs all three detection components, combines their
        scores using the configured weights, and returns a comprehensive
        result dictionary.

        Args:
            reading: Dictionary of OBD-II sensor values.

        Returns:
            A dictionary with the following keys:

            - **fraud_score** (*float*): Combined score in [0.0, 1.0].
            - **is_fraud** (*bool*): ``True`` if fraud_score >= 0.50.
            - **severity** (*str*): ``"LOW"`` (< 0.25), ``"MEDIUM"``
              (0.25 -- 0.50), or ``"HIGH"`` (>= 0.50).
            - **components** (*dict*): Individual scores from each
              component (``physics``, ``isolation``, ``temporal``).
            - **violations** (*list[str]*): All violation descriptions
              aggregated from every component.
        """
        physics_score, physics_violations, physics_codes = self._physics.validate(
            reading
        )
        isolation_score = self._isolation.predict(reading)
        isolation_codes = self._isolation.reason_codes_for(isolation_score)
        temporal_score, temporal_issues = self._temporal.update_and_check(
            reading
        )
        temporal_codes = list(self._temporal._last_reason_codes)

        # Page-Hinkley drift on the CES score (falls back to CO2 if
        # ces_score is not in the reading; falls back to 0 if neither is).
        drift_signal = reading.get("ces_score")
        if drift_signal is None:
            drift_signal = reading.get("co2", 0.0) / 120.0  # normalise to ~1
        drift_score, drift_direction = self._drift.update(float(drift_signal))

        drift_issues: list[str] = []
        drift_codes: list[str] = []
        if drift_direction != "none" and drift_score >= 1.0:
            drift_issues.append(
                f"Page-Hinkley drift detected ({drift_direction}); "
                f"possible gradual sensor tampering"
            )
            if drift_direction == "upward":
                drift_codes.append(FraudReasonCode.DRIFT_UPWARD_CES.value)
            elif drift_direction == "downward":
                drift_codes.append(FraudReasonCode.DRIFT_DOWNWARD_CES.value)

        # Per-pollutant Page-Hinkley bank (audit 13A #8). Informational
        # output only; the result is reported in ``pollutant_drift`` and
        # its violations are appended to the main violation list, but it
        # is NOT a weighted ensemble component — so the fraud_score
        # weighting (physics/isolation/temporal/drift = 0.45/0.30/0.15/0.10)
        # stays unchanged and every existing test result is preserved.
        pollutant_drift_score, pollutant_drift_issues, pollutant_drift_detail = (
            self._pollutant_drift.update(reading)
        )

        fraud_score = (
            self._physics_weight * physics_score
            + self._isolation_weight * isolation_score
            + self._temporal_weight * temporal_score
            + self._drift_weight * drift_score
        )
        fraud_score = min(fraud_score, 1.0)

        # Physics override: if physics validator detects any violation
        # (score >= 0.5, i.e. at least one rule broken), override the
        # ensemble score to ensure physically impossible readings are
        # always flagged, regardless of IF/temporal/drift contributions.
        physics_override = physics_score >= 0.5
        if physics_override:
            fraud_score = max(fraud_score, 0.55)

        # ── Per-VIN baseline bump (audit 13A #1) ───────────────────────
        # Opt-in: requires PER_VIN_BASELINE_ENABLED=1 in env AND a
        # vehicle_id argument. The bump is additive (<= +0.10) rather
        # than a 5th ensemble weight — this preserves the 4-way weight
        # invariant documented in the class docstring so all existing
        # tests continue to pass.
        per_vin_enabled = os.environ.get("PER_VIN_BASELINE_ENABLED", "0") == "1"
        per_vin_codes: list[str] = []
        per_vin_z = 0.0
        if per_vin_enabled and vehicle_id is not None:
            per_vin_z = self._per_vin_baseline.z_score(vehicle_id, reading)
            if per_vin_z > 3.0:
                # Linear ramp: z=3 → +0.00, z=5 → +0.10
                bump = min(0.10, max(0.0, (per_vin_z - 3.0) * 0.05))
                fraud_score = min(1.0, fraud_score + bump)
                per_vin_codes.append(
                    FraudReasonCode.PER_VIN_BASELINE_DRIFT.value
                )
            # Update AFTER scoring so the current reading doesn't absorb
            # itself into its own baseline — standard streaming-anomaly
            # protocol.
            self._per_vin_baseline.update(vehicle_id, reading)

        if fraud_score >= 0.50:
            severity = "HIGH"
        elif fraud_score >= 0.25:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        all_violations = (
            physics_violations
            + temporal_issues
            + drift_issues
            + pollutant_drift_issues
        )

        reason_codes = (
            physics_codes
            + isolation_codes
            + temporal_codes
            + drift_codes
            + per_vin_codes
        )
        # Deduplicate while preserving order so the list is a clean set of
        # reasons even if (somehow) a rule fires twice.
        seen: set[str] = set()
        deduped: list[str] = []
        for c in reason_codes:
            if c not in seen:
                seen.add(c)
                deduped.append(c)
        reason_codes = deduped if deduped else [FraudReasonCode.NONE.value]

        return {
            "fraud_score": fraud_score,
            "is_fraud": fraud_score >= 0.50,
            "severity": severity,
            "physics_override": physics_override,
            "components": {
                "physics": physics_score,
                "isolation": isolation_score,
                "temporal": temporal_score,
                "drift": drift_score,
            },
            "drift_direction": drift_direction,
            "pollutant_drift": {
                "max_score": round(pollutant_drift_score, 4),
                "per_channel": pollutant_drift_detail,
            },
            "per_vin_z_score": round(per_vin_z, 4),
            "reason_codes": reason_codes,
            "violations": all_violations,
        }
