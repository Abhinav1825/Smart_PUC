"""
Smart PUC — Station-level fraud detector
=========================================

Closes audit-report 13B #14 ("Station Anomaly Detection"). Where
``ml/fraud_detector.py`` decides whether a single OBD reading is
suspicious, this module decides whether a whole **testing station's
activity profile** is suspicious. The two detectors are composable:
the per-reading detector catches vehicle-level fraud, and this
station-level detector catches *corrupted testing centres* — stations
that are either rubber-stamping failing vehicles or manufacturing
fake PASS records at suspicious volume.

Three complementary signals are combined into a station risk score:

1. **Volume anomaly** — sudden jumps in records/hour against the
   station's own 7-day baseline. A rural station that normally
   processes 5 vehicles/hour and suddenly processes 80 is a red flag.

2. **Pass-rate anomaly** — a station whose PASS rate jumps from
   60% to 98% overnight is a red flag (either they bought cleaner
   customers or they stopped enforcing the standard).

3. **Average-CES shift** — a station whose mean CES drops by 40%
   in a week without a plausible seasonal explanation is a red flag
   (could indicate coordinated tampering with an ECU reflash shop
   operating across the station's clients).

This is a batch-mode detector: feed it a list of recent records
tagged with ``station_id``/``station_address``/``issuedByStation``
and it returns per-station risk scores. There is no sklearn
dependency — the statistics are plain numpy-free Python so the
module runs in the same environments the rest of Smart PUC runs in.

Design notes
------------
- The detector assumes **each record carries a stable station
  identifier**. In Smart PUC v3.2 this is the ``issuedByStation``
  field written on-chain by the EmissionRegistry, surfaced to the
  backend via ``blockchain_connector.get_records_paginated``.
- Rates are computed per sliding window. Default windows are
  1 hour for volume and 24 hours for pass-rate and CES shift.
- A station that has fewer than ``MIN_RECORDS_FOR_BASELINE`` records
  in its historical baseline is flagged as INSUFFICIENT_DATA and
  scored zero — we never accuse a station on too little data.

References
----------
- Chandola, Banerjee & Kumar, "Anomaly Detection: A Survey",
  ACM Computing Surveys 41(3), 2009.
- AllState's drivewise patents (US 10,096,038) — applied
  trip-level anomaly detection to telematics; we apply an analogous
  pattern to *station-level* behaviour rather than *driver-level*.

See also
--------
- ``ml/fraud_detector.py`` — per-reading fraud detector (vehicle-side).
- ``docs/THREAT_MODEL.md`` §A10 — corrupted-testing-station attack.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


MIN_RECORDS_FOR_BASELINE: int = 20
DEFAULT_WINDOW_SECONDS: int = 3600        # 1 hour for volume
DEFAULT_BEHAVIOUR_WINDOW: int = 86400     # 24 h for pass-rate + CES shift


# ───────────────────────── Data classes ──────────────────────────────────

@dataclass
class StationSignalReport:
    """Per-station aggregate computed from the input records."""

    station_id: str
    total_records: int
    records_in_window: int
    baseline_rate_per_hour: float
    current_rate_per_hour: float
    volume_z_score: float
    pass_rate_baseline: float
    pass_rate_current: float
    pass_rate_delta: float
    avg_ces_baseline: float
    avg_ces_current: float
    avg_ces_delta_pct: float
    risk_score: float              # 0 = safe, 1 = highly suspicious
    risk_level: str                # LOW, MEDIUM, HIGH, INSUFFICIENT_DATA
    violations: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "station_id": self.station_id,
            "total_records": self.total_records,
            "records_in_window": self.records_in_window,
            "baseline_rate_per_hour": round(self.baseline_rate_per_hour, 3),
            "current_rate_per_hour": round(self.current_rate_per_hour, 3),
            "volume_z_score": round(self.volume_z_score, 3),
            "pass_rate_baseline": round(self.pass_rate_baseline, 4),
            "pass_rate_current": round(self.pass_rate_current, 4),
            "pass_rate_delta": round(self.pass_rate_delta, 4),
            "avg_ces_baseline": round(self.avg_ces_baseline, 4),
            "avg_ces_current": round(self.avg_ces_current, 4),
            "avg_ces_delta_pct": round(self.avg_ces_delta_pct, 4),
            "risk_score": round(self.risk_score, 4),
            "risk_level": self.risk_level,
            "violations": list(self.violations),
        }


# ───────────────────────── Core detector ────────────────────────────────

class StationFraudDetector:
    """Batch station-level anomaly detector.

    Feed a list of records into :meth:`analyse` and receive one
    :class:`StationSignalReport` per station touched by the input.

    Expected record shape (any mapping-like object with these keys):

    * ``station_id`` or ``issuedByStation`` or ``station_address``
      — any string is fine; exact format is not interpreted.
    * ``timestamp`` — unix seconds (int or float).
    * ``status`` — ``"PASS"`` or ``"FAIL"``. A missing field is
      treated as PASS only if ``ces_score`` is present and < 1.0.
    * ``ces_score`` — float, the reading's CES value (required for
      the CES-shift signal; a station without this signal is scored
      only on volume + pass-rate).

    The input does **not** need to be sorted; the detector sorts
    each station's records by timestamp internally.
    """

    def __init__(
        self,
        volume_window_seconds: int = DEFAULT_WINDOW_SECONDS,
        behaviour_window_seconds: int = DEFAULT_BEHAVIOUR_WINDOW,
        baseline_multiplier: int = 7,
        volume_z_alarm: float = 3.0,
        pass_rate_jump: float = 0.25,
        ces_shift_pct: float = 0.30,
    ) -> None:
        """
        Args:
            volume_window_seconds: Sliding window for the "current" volume
                signal. Default 1 hour.
            behaviour_window_seconds: Sliding window for the current
                pass-rate and CES-shift signals. Default 24 hours.
            baseline_multiplier: Historical baseline window is
                ``behaviour_window_seconds × baseline_multiplier``.
                Default 7 → a 7-day baseline for a 1-day current window.
            volume_z_alarm: A volume Z-score above this triggers a
                HIGH risk level on the volume signal alone.
            pass_rate_jump: Absolute pass-rate delta (current − baseline)
                above which the pass-rate signal trips.
            ces_shift_pct: Fractional CES drop (current / baseline − 1)
                below which the CES-shift signal trips (negative = drop).
        """
        self._volume_window = int(volume_window_seconds)
        self._behaviour_window = int(behaviour_window_seconds)
        self._baseline_multiplier = int(baseline_multiplier)
        self._volume_z_alarm = float(volume_z_alarm)
        self._pass_rate_jump = float(pass_rate_jump)
        self._ces_shift_pct = float(ces_shift_pct)

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _station_of(rec: Dict[str, Any]) -> str:
        return (
            rec.get("station_id")
            or rec.get("issuedByStation")
            or rec.get("station_address")
            or "UNKNOWN"
        )

    @staticmethod
    def _is_pass(rec: Dict[str, Any]) -> bool:
        status = rec.get("status")
        if status is not None:
            return str(status).upper() == "PASS"
        ces = rec.get("ces_score")
        if ces is not None:
            try:
                return float(ces) < 1.0
            except (TypeError, ValueError):
                return False
        return True  # neutral default

    @staticmethod
    def _mean(xs: List[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    @staticmethod
    def _stddev(xs: List[float]) -> float:
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
        return math.sqrt(var)

    # ── Core analysis ────────────────────────────────────────────────────

    def analyse(
        self, records: Iterable[Dict[str, Any]], now: Optional[float] = None
    ) -> List[StationSignalReport]:
        """Group *records* by station and compute a risk report per station.

        Args:
            records: Iterable of dict-like records. See class docstring
                for the expected key set.
            now: Override for the "current time" reference point. Default
                is ``time.time()`` at call time. Tests should pass a
                fixed value so the rolling windows are deterministic.

        Returns:
            List of :class:`StationSignalReport`, one per station found
            in the input, sorted by descending ``risk_score`` (highest
            risk first).
        """
        reference_now = float(now if now is not None else time.time())

        by_station: Dict[str, List[Dict[str, Any]]] = {}
        for rec in records:
            sid = self._station_of(rec)
            by_station.setdefault(sid, []).append(rec)

        reports: List[StationSignalReport] = []
        for sid, recs in by_station.items():
            recs_sorted = sorted(recs, key=lambda r: float(r.get("timestamp", 0) or 0))
            report = self._analyse_one(sid, recs_sorted, reference_now)
            reports.append(report)

        reports.sort(key=lambda r: r.risk_score, reverse=True)
        return reports

    def _analyse_one(
        self,
        station_id: str,
        recs: List[Dict[str, Any]],
        now: float,
    ) -> StationSignalReport:
        total = len(recs)

        # 1. Volume in the current window vs per-hour baseline rate.
        current_start = now - self._volume_window
        current_recs = [
            r for r in recs
            if float(r.get("timestamp", 0) or 0) >= current_start
        ]
        baseline_start = now - (self._volume_window * self._baseline_multiplier)
        baseline_recs = [
            r for r in recs
            if baseline_start <= float(r.get("timestamp", 0) or 0) < current_start
        ]

        baseline_hours = max(
            1.0, (self._volume_window * (self._baseline_multiplier - 1)) / 3600.0
        )
        baseline_rate = len(baseline_recs) / baseline_hours
        current_hours = max(1.0, self._volume_window / 3600.0)
        current_rate = len(current_recs) / current_hours

        # Per-hour rates in sliding bins of the baseline for stddev.
        bin_counts: List[float] = []
        for k in range(1, self._baseline_multiplier):
            bin_end = now - (self._volume_window * k)
            bin_start = bin_end - self._volume_window
            cnt = sum(
                1 for r in recs
                if bin_start <= float(r.get("timestamp", 0) or 0) < bin_end
            )
            bin_counts.append(cnt / current_hours)
        sigma = self._stddev(bin_counts) or 1.0
        z = (current_rate - baseline_rate) / sigma if sigma > 0 else 0.0

        # 2. Pass-rate baseline vs current.
        beh_current_start = now - self._behaviour_window
        beh_current = [
            r for r in recs
            if float(r.get("timestamp", 0) or 0) >= beh_current_start
        ]
        beh_baseline_start = now - (self._behaviour_window * self._baseline_multiplier)
        beh_baseline = [
            r for r in recs
            if beh_baseline_start
            <= float(r.get("timestamp", 0) or 0) < beh_current_start
        ]
        pr_cur = (
            sum(1 for r in beh_current if self._is_pass(r)) / max(1, len(beh_current))
            if beh_current
            else 0.0
        )
        pr_base = (
            sum(1 for r in beh_baseline if self._is_pass(r)) / max(1, len(beh_baseline))
            if beh_baseline
            else 0.0
        )
        pr_delta = pr_cur - pr_base

        # 3. Average CES baseline vs current.
        def _ces_mean(xs: List[Dict[str, Any]]) -> float:
            vals = []
            for r in xs:
                v = r.get("ces_score")
                if v is None:
                    continue
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
            return self._mean(vals)

        ces_cur = _ces_mean(beh_current)
        ces_base = _ces_mean(beh_baseline)
        ces_delta_pct = (
            (ces_cur - ces_base) / ces_base if ces_base > 0 else 0.0
        )

        # ── Risk scoring ────────────────────────────────────────────────
        risk = 0.0
        violations: List[str] = []

        if total < MIN_RECORDS_FOR_BASELINE:
            return StationSignalReport(
                station_id=station_id,
                total_records=total,
                records_in_window=len(current_recs),
                baseline_rate_per_hour=baseline_rate,
                current_rate_per_hour=current_rate,
                volume_z_score=0.0,
                pass_rate_baseline=pr_base,
                pass_rate_current=pr_cur,
                pass_rate_delta=pr_delta,
                avg_ces_baseline=ces_base,
                avg_ces_current=ces_cur,
                avg_ces_delta_pct=ces_delta_pct,
                risk_score=0.0,
                risk_level="INSUFFICIENT_DATA",
                violations=[
                    f"Only {total} records on file; need {MIN_RECORDS_FOR_BASELINE} "
                    "for a reliable baseline."
                ],
            )

        # Volume component (0.40 weight).
        if z >= self._volume_z_alarm:
            risk += 0.40 * min(1.0, z / (self._volume_z_alarm * 2))
            violations.append(
                f"Volume Z-score {z:.2f} ≥ {self._volume_z_alarm:.1f} "
                f"({current_rate:.1f} rec/h vs baseline {baseline_rate:.1f} rec/h)"
            )

        # Pass-rate component (0.35 weight).
        if pr_delta >= self._pass_rate_jump:
            risk += 0.35 * min(1.0, pr_delta / (self._pass_rate_jump * 2))
            violations.append(
                f"Pass-rate jumped {pr_delta * 100:.1f}% "
                f"(baseline {pr_base * 100:.1f}% → current {pr_cur * 100:.1f}%)"
            )

        # CES-shift component (0.25 weight). We care about DROPS only —
        # rising CES is not a sign of fraud, it's a sign of a dirty fleet.
        if ces_delta_pct <= -self._ces_shift_pct:
            risk += 0.25 * min(1.0, abs(ces_delta_pct) / (self._ces_shift_pct * 2))
            violations.append(
                f"Avg CES dropped {ces_delta_pct * 100:.1f}% "
                f"(baseline {ces_base:.3f} → current {ces_cur:.3f})"
            )

        risk = min(1.0, risk)
        if risk >= 0.50:
            level = "HIGH"
        elif risk >= 0.25:
            level = "MEDIUM"
        else:
            level = "LOW"

        return StationSignalReport(
            station_id=station_id,
            total_records=total,
            records_in_window=len(current_recs),
            baseline_rate_per_hour=baseline_rate,
            current_rate_per_hour=current_rate,
            volume_z_score=z,
            pass_rate_baseline=pr_base,
            pass_rate_current=pr_cur,
            pass_rate_delta=pr_delta,
            avg_ces_baseline=ces_base,
            avg_ces_current=ces_cur,
            avg_ces_delta_pct=ces_delta_pct,
            risk_score=risk,
            risk_level=level,
            violations=violations,
        )
