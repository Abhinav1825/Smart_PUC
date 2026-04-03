"""
Smart PUC -- Scalability and Performance Benchmark Suite
========================================================

Provides five experiments for evaluating the Smart PUC system:

E1. **Throughput**: Measures simulated transactions per second (TPS) at
    varying concurrency levels using ``ThreadPoolExecutor``.
E2. **Latency**: Profiles end-to-end latency (generate reading, calculate
    emissions, mock tx signing, mock confirmation) over 100 samples.
E3. **Gas Cost**: Simulates per-transaction gas consumption for the
    ``storeEmission`` contract call and estimates MATIC cost.
E4. **Fraud Detection Accuracy**: Evaluates the ``FraudDetector`` on 500
    clean and 100 tampered samples, reporting precision, recall, F1, and
    AUC-ROC.
E5. **CES vs CO2-only**: Compares multi-pollutant Composite Emission Score
    violations against CO2-only violations over a full WLTC drive cycle.

Usage::

    from benchmarks.scalability_test import BenchmarkSuite
    suite = BenchmarkSuite()
    suite.run_all()
    tables = suite.generate_paper_tables()
    for name, latex in tables.items():
        print(latex)
"""

from __future__ import annotations

import hashlib
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Optional sklearn imports with graceful fallback
# ---------------------------------------------------------------------------
try:
    from sklearn.metrics import (
        precision_score,
        recall_score,
        f1_score,
        roc_auc_score,
    )
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.emission_engine import calculate_co2, EMISSION_FACTORS, DEFAULT_THRESHOLD
from backend.simulator import OBDSimulator
from ml.fraud_detector import FraudDetector
from physics.vsp_model import calculate_vsp, estimate_fuel_rate, VehicleParams


# ---------------------------------------------------------------------------
# WLTC speed profile (simplified, seconds vs km/h)
# ---------------------------------------------------------------------------

def _generate_wltc_profile() -> List[Tuple[float, float]]:
    """Generate a simplified WLTC Class 3 speed profile.

    Returns a list of (time_s, speed_kmh) tuples covering the four WLTC
    phases: Low, Medium, High, and Extra-High.  The profile is an
    approximation with 1-second resolution totalling 1800 seconds (the
    standard WLTC duration).

    Returns:
        List of (time_seconds, speed_km_h) tuples.
    """
    rng = np.random.RandomState(42)
    profile: List[Tuple[float, float]] = []
    t = 0.0

    phases = [
        ("Low",        589,  0, 56),
        ("Medium",     433, 15, 76),
        ("High",       455, 20, 97),
        ("Extra-High", 323, 25, 131),
    ]

    for _name, duration, lo, hi in phases:
        speed = float(rng.uniform(lo, hi))
        for _ in range(duration):
            delta = rng.uniform(-3.0, 3.0)
            speed = float(np.clip(speed + delta, lo, hi))
            profile.append((t, speed))
            t += 1.0

    return profile


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ThroughputResult:
    """Result of a throughput experiment at a single concurrency level."""
    concurrency: int = 0
    total_transactions: int = 0
    elapsed_seconds: float = 0.0
    tps: float = 0.0
    success_rate: float = 0.0
    successes: int = 0
    failures: int = 0


@dataclass
class LatencyResult:
    """Aggregated latency statistics over multiple samples."""
    num_samples: int = 0
    mean_ms: float = 0.0
    median_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    std_dev_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0


@dataclass
class GasCostResult:
    """Aggregated gas cost statistics."""
    num_samples: int = 0
    mean_gas: float = 0.0
    median_gas: float = 0.0
    min_gas: float = 0.0
    max_gas: float = 0.0
    estimated_cost_matic: float = 0.0


@dataclass
class FraudAccuracyResult:
    """Fraud detection evaluation metrics."""
    num_clean: int = 0
    num_tampered: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    auc_roc: float = 0.0


@dataclass
class CESComparisonResult:
    """CES vs CO2-only comparison over a drive cycle."""
    total_points: int = 0
    co2_only_violations: int = 0
    ces_violations: int = 0
    co2_only_violation_rate: float = 0.0
    ces_violation_rate: float = 0.0
    additional_detections: int = 0


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _mock_store_emission(vehicle_id: str, co2_value: int) -> Dict[str, Any]:
    """Simulate a blockchain ``storeEmission`` call.

    Mimics network and confirmation delay with a short sleep and returns a
    mock transaction receipt.

    Args:
        vehicle_id: Vehicle registration string.
        co2_value: CO2 value in g/km.

    Returns:
        Dictionary with ``tx_hash``, ``status``, ``block_number``, and
        ``gas_used`` keys.
    """
    # Simulate network + confirmation latency (10-40 ms)
    time.sleep(random.uniform(0.010, 0.040))

    tx_hash = hashlib.sha256(
        f"{vehicle_id}{co2_value}{time.time_ns()}".encode()
    ).hexdigest()

    gas_used = random.randint(250_000, 350_000)

    return {
        "tx_hash": tx_hash,
        "status": "success",
        "block_number": random.randint(1_000_000, 9_999_999),
        "gas_used": gas_used,
    }


def _mock_tx_sign(data: bytes) -> bytes:
    """Simulate ECDSA transaction signing.

    Args:
        data: Raw transaction bytes to sign.

    Returns:
        A mock 64-byte signature.
    """
    time.sleep(random.uniform(0.001, 0.003))
    return hashlib.sha256(data).digest() + hashlib.sha256(data[::-1]).digest()


def _mock_confirmation() -> Dict[str, Any]:
    """Simulate waiting for block confirmation.

    Returns:
        Dictionary with ``confirmed`` flag and ``block_number``.
    """
    time.sleep(random.uniform(0.005, 0.020))
    return {"confirmed": True, "block_number": random.randint(1_000_000, 9_999_999)}


# ---------------------------------------------------------------------------
# Sample generation helpers for fraud detection
# ---------------------------------------------------------------------------

def _generate_clean_sample(rng: np.random.RandomState) -> Dict[str, Any]:
    """Generate a single clean (non-tampered) OBD-II reading.

    Args:
        rng: Numpy random state for reproducibility.

    Returns:
        Dictionary of OBD-II sensor values within normal ranges.
    """
    speed = float(rng.uniform(10, 100))
    rpm = float(rng.uniform(speed * 20, speed * 50))
    fuel_rate = float(rng.uniform(4.0, 12.0))
    acceleration = float(rng.uniform(-2.0, 2.0))
    speed_mps = speed / 3.6
    vsp = calculate_vsp(speed_mps, acceleration)
    co2 = fuel_rate * EMISSION_FACTORS["petrol"] / 100.0

    return {
        "speed": speed,
        "rpm": rpm,
        "fuel_rate": fuel_rate,
        "acceleration": acceleration,
        "vsp": vsp,
        "co2": co2,
        "timestamp": int(time.time()) + rng.randint(0, 10000),
    }


def _generate_tampered_sample(rng: np.random.RandomState) -> Dict[str, Any]:
    """Generate a tampered OBD-II reading with physics violations.

    Introduces at least one physically impossible condition such as zero RPM
    at high speed, extreme acceleration, or negative fuel rate.

    Args:
        rng: Numpy random state for reproducibility.

    Returns:
        Dictionary of OBD-II sensor values with deliberate anomalies.
    """
    tampering_type = rng.choice(["zero_rpm", "extreme_accel", "neg_fuel", "impossible_speed"])

    if tampering_type == "zero_rpm":
        speed = float(rng.uniform(60, 120))
        rpm = 0.0
        fuel_rate = float(rng.uniform(4.0, 8.0))
        acceleration = 0.0
    elif tampering_type == "extreme_accel":
        speed = float(rng.uniform(30, 80))
        rpm = float(rng.uniform(speed * 20, speed * 50))
        fuel_rate = float(rng.uniform(4.0, 8.0))
        acceleration = float(rng.uniform(5.0, 10.0))
    elif tampering_type == "neg_fuel":
        speed = float(rng.uniform(30, 80))
        rpm = float(rng.uniform(speed * 20, speed * 50))
        fuel_rate = float(rng.uniform(-5.0, -0.1))
        acceleration = 0.0
    else:  # impossible_speed
        speed = float(rng.uniform(260, 400))
        rpm = float(rng.uniform(3000, 7500))
        fuel_rate = float(rng.uniform(4.0, 8.0))
        acceleration = 0.0

    speed_mps = speed / 3.6
    vsp = calculate_vsp(speed_mps, acceleration)
    co2 = max(0.0, fuel_rate) * EMISSION_FACTORS["petrol"] / 100.0

    return {
        "speed": speed,
        "rpm": rpm,
        "fuel_rate": fuel_rate,
        "acceleration": acceleration,
        "vsp": vsp,
        "co2": co2,
        "timestamp": int(time.time()) + rng.randint(0, 10000),
    }


# ---------------------------------------------------------------------------
# Composite Emission Score helpers
# ---------------------------------------------------------------------------

def _compute_ces(co2_g_km: float, nox_g_km: float, pm_g_km: float) -> float:
    """Compute a Composite Emission Score from multiple pollutants.

    The CES is a weighted normalised sum designed so that a vehicle can fail
    even if CO2 alone is within limits.

    Args:
        co2_g_km: CO2 emissions in g/km.
        nox_g_km: NOx emissions in g/km.
        pm_g_km: Particulate matter (PM2.5) emissions in g/km.

    Returns:
        Composite score in [0, 1] where values above 0.7 indicate a
        violation.
    """
    # BS-VI limits (petrol)
    co2_limit = 120.0   # g/km
    nox_limit = 0.06    # g/km
    pm_limit = 0.0045   # g/km

    co2_norm = min(co2_g_km / co2_limit, 2.0)
    nox_norm = min(nox_g_km / nox_limit, 2.0)
    pm_norm = min(pm_g_km / pm_limit, 2.0)

    # Weights: CO2 50%, NOx 30%, PM 20%
    ces = 0.50 * co2_norm + 0.30 * nox_norm + 0.20 * pm_norm
    return ces


# ===========================================================================
# BenchmarkSuite
# ===========================================================================

class BenchmarkSuite:
    """Main benchmark suite containing five experiments for Smart PUC evaluation.

    Attributes:
        results: Dictionary mapping experiment names to their result objects.
        seed: Random seed used for reproducibility.
    """

    def __init__(self, seed: int = 42) -> None:
        """Initialise the benchmark suite.

        Args:
            seed: Random seed for reproducibility across all experiments.
        """
        self.seed: int = seed
        self.results: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # E1 -- Throughput
    # -----------------------------------------------------------------------

    def experiment_throughput(
        self,
        concurrency_levels: Optional[List[int]] = None,
        transactions_per_level: int = 50,
    ) -> List[ThroughputResult]:
        """E1: Measure simulated TPS at varying concurrency levels.

        Submits mock ``store_emission`` calls using a ``ThreadPoolExecutor``
        at each concurrency level, timing total elapsed wall-clock time and
        computing TPS and success rate.

        Args:
            concurrency_levels: List of concurrent worker counts to test.
                Defaults to ``[1, 5, 10, 25, 50]``.
            transactions_per_level: Number of transactions to submit at each
                concurrency level.

        Returns:
            List of :class:`ThroughputResult` objects, one per concurrency
            level.
        """
        if concurrency_levels is None:
            concurrency_levels = [1, 5, 10, 25, 50]

        sim = OBDSimulator(vehicle_id="BENCH_THROUGHPUT")
        results: List[ThroughputResult] = []

        for n_workers in concurrency_levels:
            successes = 0
            failures = 0

            def _task() -> bool:
                reading = sim.generate_reading()
                co2_result = calculate_co2(
                    reading["fuel_rate"], reading["speed"], reading["fuel_type"]
                )
                receipt = _mock_store_emission("BENCH", co2_result["co2_int"])
                return receipt["status"] == "success"

            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                futures = [executor.submit(_task) for _ in range(transactions_per_level)]
                for future in as_completed(futures):
                    try:
                        if future.result():
                            successes += 1
                        else:
                            failures += 1
                    except Exception:
                        failures += 1
            elapsed = time.perf_counter() - start

            tps = transactions_per_level / elapsed if elapsed > 0 else 0.0
            total = successes + failures
            success_rate = successes / total if total > 0 else 0.0

            res = ThroughputResult(
                concurrency=n_workers,
                total_transactions=transactions_per_level,
                elapsed_seconds=round(elapsed, 4),
                tps=round(tps, 2),
                success_rate=round(success_rate, 4),
                successes=successes,
                failures=failures,
            )
            results.append(res)

        self.results["throughput"] = results
        return results

    # -----------------------------------------------------------------------
    # E2 -- Latency
    # -----------------------------------------------------------------------

    def experiment_latency(self, num_samples: int = 100) -> LatencyResult:
        """E2: Measure end-to-end latency over multiple samples.

        Each sample simulates the full pipeline: generate OBD-II reading,
        calculate emissions, sign the transaction, and wait for mock block
        confirmation.  Latency is measured in milliseconds.

        Args:
            num_samples: Number of latency samples to collect.

        Returns:
            :class:`LatencyResult` with descriptive statistics.
        """
        sim = OBDSimulator(vehicle_id="BENCH_LATENCY")
        latencies_ms: List[float] = []

        for _ in range(num_samples):
            t0 = time.perf_counter()

            # Step 1: Generate OBD-II reading
            reading = sim.generate_reading()

            # Step 2: Calculate emissions
            co2_result = calculate_co2(
                reading["fuel_rate"], reading["speed"], reading["fuel_type"]
            )

            # Step 3: Mock transaction signing
            tx_data = f"{reading['vehicle_id']}{co2_result['co2_int']}{reading['timestamp']}".encode()
            _mock_tx_sign(tx_data)

            # Step 4: Mock block confirmation
            _mock_confirmation()

            t1 = time.perf_counter()
            latencies_ms.append((t1 - t0) * 1000.0)

        latencies = np.array(latencies_ms)
        result = LatencyResult(
            num_samples=num_samples,
            mean_ms=round(float(np.mean(latencies)), 3),
            median_ms=round(float(np.median(latencies)), 3),
            p95_ms=round(float(np.percentile(latencies, 95)), 3),
            p99_ms=round(float(np.percentile(latencies, 99)), 3),
            std_dev_ms=round(float(np.std(latencies)), 3),
            min_ms=round(float(np.min(latencies)), 3),
            max_ms=round(float(np.max(latencies)), 3),
        )

        self.results["latency"] = result
        return result

    # -----------------------------------------------------------------------
    # E3 -- Gas Cost
    # -----------------------------------------------------------------------

    def experiment_gas_cost(
        self,
        num_samples: int = 30,
        gas_price_gwei: float = 30.0,
        matic_usd: float = 0.70,
    ) -> GasCostResult:
        """E3: Simulate gas consumption per storeEmission call.

        Uses realistic gas estimates for the Smart PUC emission storage
        contract (approximately 250k--350k gas per call on Polygon).

        Args:
            num_samples: Number of mock transactions to sample.
            gas_price_gwei: Gas price in Gwei for cost estimation.
            matic_usd: MATIC/USD exchange rate for cost estimation.

        Returns:
            :class:`GasCostResult` with gas statistics and estimated MATIC
            cost.
        """
        rng = np.random.RandomState(self.seed)
        gas_values: List[int] = []

        for _ in range(num_samples):
            receipt = _mock_store_emission("BENCH_GAS", rng.randint(50, 200))
            gas_values.append(receipt["gas_used"])

        gas_arr = np.array(gas_values, dtype=np.float64)
        mean_gas = float(np.mean(gas_arr))

        # Cost: gas * gas_price_gwei * 1e-9 MATIC
        mean_cost_matic = mean_gas * gas_price_gwei * 1e-9

        result = GasCostResult(
            num_samples=num_samples,
            mean_gas=round(mean_gas, 1),
            median_gas=round(float(np.median(gas_arr)), 1),
            min_gas=round(float(np.min(gas_arr)), 1),
            max_gas=round(float(np.max(gas_arr)), 1),
            estimated_cost_matic=round(mean_cost_matic, 8),
        )

        self.results["gas_cost"] = result
        return result

    # -----------------------------------------------------------------------
    # E4 -- Fraud Detection Accuracy
    # -----------------------------------------------------------------------

    def experiment_fraud_accuracy(
        self,
        num_clean: int = 500,
        num_tampered: int = 100,
    ) -> FraudAccuracyResult:
        """E4: Evaluate FraudDetector precision, recall, F1, and AUC-ROC.

        Generates ``num_clean`` legitimate readings and ``num_tampered``
        readings with deliberate physics violations, then runs each through
        the :class:`~ml.fraud_detector.FraudDetector` ensemble.

        When scikit-learn is unavailable, metrics are computed using manual
        confusion-matrix counts and AUC-ROC is set to 0.0.

        Args:
            num_clean: Number of clean (legitimate) samples.
            num_tampered: Number of tampered (fraudulent) samples.

        Returns:
            :class:`FraudAccuracyResult` with classification metrics.
        """
        rng = np.random.RandomState(self.seed)

        # Generate samples
        clean_samples = [_generate_clean_sample(rng) for _ in range(num_clean)]
        tampered_samples = [_generate_tampered_sample(rng) for _ in range(num_tampered)]

        # Train fraud detector on clean data
        detector = FraudDetector()
        detector.fit(clean_samples)

        # Ground truth: 0 = clean, 1 = tampered
        y_true: List[int] = [0] * num_clean + [1] * num_tampered
        y_scores: List[float] = []
        y_pred: List[int] = []

        all_samples = clean_samples + tampered_samples
        for sample in all_samples:
            result = detector.analyze(sample)
            y_scores.append(result["fraud_score"])
            y_pred.append(1 if result["is_fraud"] else 0)

        if _HAS_SKLEARN:
            prec = float(precision_score(y_true, y_pred, zero_division=0))
            rec = float(recall_score(y_true, y_pred, zero_division=0))
            f1 = float(f1_score(y_true, y_pred, zero_division=0))
            try:
                auc = float(roc_auc_score(y_true, y_scores))
            except ValueError:
                auc = 0.0
        else:
            # Manual computation
            tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
            fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
            fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)

            prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
            auc = 0.0  # Cannot compute without sklearn

        result = FraudAccuracyResult(
            num_clean=num_clean,
            num_tampered=num_tampered,
            precision=round(prec, 4),
            recall=round(rec, 4),
            f1=round(f1, 4),
            auc_roc=round(auc, 4),
        )

        self.results["fraud_accuracy"] = result
        return result

    # -----------------------------------------------------------------------
    # E5 -- CES vs CO2-only
    # -----------------------------------------------------------------------

    def experiment_ces_vs_co2(self) -> CESComparisonResult:
        """E5: Compare multi-pollutant CES violations vs CO2-only violations.

        Runs the full simplified WLTC drive cycle, computing both CO2-only
        compliance (pass/fail against the BS-VI threshold) and a Composite
        Emission Score (CES) that also accounts for NOx and PM2.5.

        NOx and PM emissions are estimated from VSP using simplified modal
        emission factors derived from MOVES operating-mode rates.

        Returns:
            :class:`CESComparisonResult` comparing the two approaches.
        """
        profile = _generate_wltc_profile()
        rng = np.random.RandomState(self.seed)
        params = VehicleParams()

        co2_only_violations = 0
        ces_violations = 0
        total_points = 0
        prev_speed_mps = 0.0

        for i, (t_s, speed_kmh) in enumerate(profile):
            speed_mps = speed_kmh / 3.6
            dt = 1.0
            accel = (speed_mps - prev_speed_mps) / dt if i > 0 else 0.0

            vsp = calculate_vsp(speed_mps, accel, grade=0.0, params=params)
            fuel_rate_l100 = estimate_fuel_rate(vsp, speed_mps)

            if speed_mps < 0.2778:
                prev_speed_mps = speed_mps
                continue

            # CO2
            co2_result = calculate_co2(fuel_rate_l100, speed_kmh, "petrol")
            co2_g_km = co2_result["co2_g_per_km"]

            # Simplified NOx and PM estimation from VSP
            # Based on MOVES modal rates for light-duty petrol
            vsp_clamped = max(vsp, 0.0)
            nox_g_km = 0.005 + 0.002 * vsp_clamped + float(rng.normal(0, 0.003))
            nox_g_km = max(nox_g_km, 0.0)
            pm_g_km = 0.001 + 0.0002 * vsp_clamped + float(rng.normal(0, 0.0005))
            pm_g_km = max(pm_g_km, 0.0)

            # CO2-only check
            if co2_result["status"] == "FAIL":
                co2_only_violations += 1

            # CES check
            ces = _compute_ces(co2_g_km, nox_g_km, pm_g_km)
            if ces > 0.7:
                ces_violations += 1

            total_points += 1
            prev_speed_mps = speed_mps

        result = CESComparisonResult(
            total_points=total_points,
            co2_only_violations=co2_only_violations,
            ces_violations=ces_violations,
            co2_only_violation_rate=round(co2_only_violations / total_points, 4) if total_points > 0 else 0.0,
            ces_violation_rate=round(ces_violations / total_points, 4) if total_points > 0 else 0.0,
            additional_detections=ces_violations - co2_only_violations,
        )

        self.results["ces_vs_co2"] = result
        return result

    # -----------------------------------------------------------------------
    # Run all
    # -----------------------------------------------------------------------

    def run_all(self) -> Dict[str, Any]:
        """Run all five benchmark experiments sequentially.

        Returns:
            Dictionary mapping experiment names to their result objects.
        """
        print("=" * 70)
        print("  Smart PUC Benchmark Suite")
        print("=" * 70)

        print("\n[E1] Throughput benchmark...")
        throughput = self.experiment_throughput()
        for r in throughput:
            print(f"  Concurrency={r.concurrency:>3d}  TPS={r.tps:>8.2f}  "
                  f"Success={r.success_rate:.2%}")

        print("\n[E2] Latency benchmark...")
        latency = self.experiment_latency()
        print(f"  Mean={latency.mean_ms:.2f}ms  Median={latency.median_ms:.2f}ms  "
              f"P95={latency.p95_ms:.2f}ms  P99={latency.p99_ms:.2f}ms")

        print("\n[E3] Gas cost benchmark...")
        gas = self.experiment_gas_cost()
        print(f"  Mean gas={gas.mean_gas:.0f}  Median={gas.median_gas:.0f}  "
              f"Cost={gas.estimated_cost_matic:.8f} MATIC")

        print("\n[E4] Fraud detection accuracy...")
        fraud = self.experiment_fraud_accuracy()
        print(f"  Precision={fraud.precision:.4f}  Recall={fraud.recall:.4f}  "
              f"F1={fraud.f1:.4f}  AUC-ROC={fraud.auc_roc:.4f}")

        print("\n[E5] CES vs CO2-only comparison...")
        ces = self.experiment_ces_vs_co2()
        print(f"  CO2-only violations: {ces.co2_only_violations}/{ces.total_points} "
              f"({ces.co2_only_violation_rate:.2%})")
        print(f"  CES violations:      {ces.ces_violations}/{ces.total_points} "
              f"({ces.ces_violation_rate:.2%})")
        print(f"  Additional detections by CES: {ces.additional_detections}")

        print("\n" + "=" * 70)
        print("  All benchmarks complete.")
        print("=" * 70)

        return self.results

    # -----------------------------------------------------------------------
    # LaTeX table generation
    # -----------------------------------------------------------------------

    def generate_paper_tables(self) -> Dict[str, str]:
        """Generate LaTeX table strings for all completed experiments.

        Each table is formatted for inclusion in an academic paper using
        the ``booktabs`` package.

        Returns:
            Dictionary mapping table names to LaTeX source strings.  Keys
            are ``"throughput"``, ``"latency"``, ``"gas_cost"``,
            ``"fraud_accuracy"``, and ``"ces_comparison"``.
        """
        tables: Dict[str, str] = {}

        # -- Throughput table --
        if "throughput" in self.results:
            rows = self.results["throughput"]
            lines = [
                r"\begin{table}[htbp]",
                r"\centering",
                r"\caption{Throughput at varying concurrency levels}",
                r"\label{tab:throughput}",
                r"\begin{tabular}{r r r r r}",
                r"\toprule",
                r"Concurrency & Transactions & Elapsed (s) & TPS & Success (\%) \\",
                r"\midrule",
            ]
            for r in rows:
                lines.append(
                    f"  {r.concurrency} & {r.total_transactions} & "
                    f"{r.elapsed_seconds:.3f} & {r.tps:.2f} & "
                    f"{r.success_rate * 100:.1f} \\\\"
                )
            lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
            tables["throughput"] = "\n".join(lines)

        # -- Latency table --
        if "latency" in self.results:
            r = self.results["latency"]
            lines = [
                r"\begin{table}[htbp]",
                r"\centering",
                r"\caption{End-to-end latency statistics (ms)}",
                r"\label{tab:latency}",
                r"\begin{tabular}{l r}",
                r"\toprule",
                r"Metric & Value (ms) \\",
                r"\midrule",
                f"  Mean & {r.mean_ms:.2f} \\\\",
                f"  Median & {r.median_ms:.2f} \\\\",
                f"  P95 & {r.p95_ms:.2f} \\\\",
                f"  P99 & {r.p99_ms:.2f} \\\\",
                f"  Std.~Dev. & {r.std_dev_ms:.2f} \\\\",
                f"  Min & {r.min_ms:.2f} \\\\",
                f"  Max & {r.max_ms:.2f} \\\\",
                r"\bottomrule",
                r"\end{tabular}",
                r"\end{table}",
            ]
            tables["latency"] = "\n".join(lines)

        # -- Gas cost table --
        if "gas_cost" in self.results:
            r = self.results["gas_cost"]
            lines = [
                r"\begin{table}[htbp]",
                r"\centering",
                r"\caption{Gas cost per \texttt{storeEmission} transaction}",
                r"\label{tab:gascost}",
                r"\begin{tabular}{l r}",
                r"\toprule",
                r"Metric & Value \\",
                r"\midrule",
                f"  Samples & {r.num_samples} \\\\",
                f"  Mean gas & {r.mean_gas:.0f} \\\\",
                f"  Median gas & {r.median_gas:.0f} \\\\",
                f"  Min gas & {r.min_gas:.0f} \\\\",
                f"  Max gas & {r.max_gas:.0f} \\\\",
                f"  Est.~cost (MATIC) & {r.estimated_cost_matic:.8f} \\\\",
                r"\bottomrule",
                r"\end{tabular}",
                r"\end{table}",
            ]
            tables["gas_cost"] = "\n".join(lines)

        # -- Fraud accuracy table --
        if "fraud_accuracy" in self.results:
            r = self.results["fraud_accuracy"]
            lines = [
                r"\begin{table}[htbp]",
                r"\centering",
                r"\caption{Fraud detection accuracy (500 clean + 100 tampered)}",
                r"\label{tab:fraud}",
                r"\begin{tabular}{l r}",
                r"\toprule",
                r"Metric & Value \\",
                r"\midrule",
                f"  Precision & {r.precision:.4f} \\\\",
                f"  Recall & {r.recall:.4f} \\\\",
                f"  F1 Score & {r.f1:.4f} \\\\",
                f"  AUC-ROC & {r.auc_roc:.4f} \\\\",
                r"\bottomrule",
                r"\end{tabular}",
                r"\end{table}",
            ]
            tables["fraud_accuracy"] = "\n".join(lines)

        # -- CES comparison table --
        if "ces_vs_co2" in self.results:
            r = self.results["ces_vs_co2"]
            lines = [
                r"\begin{table}[htbp]",
                r"\centering",
                r"\caption{CES multi-pollutant vs.\ CO\textsubscript{2}-only compliance}",
                r"\label{tab:ces}",
                r"\begin{tabular}{l r r}",
                r"\toprule",
                r"Method & Violations & Rate (\%) \\",
                r"\midrule",
                f"  CO$_2$-only & {r.co2_only_violations} & "
                f"{r.co2_only_violation_rate * 100:.2f} \\\\",
                f"  CES (multi-pollutant) & {r.ces_violations} & "
                f"{r.ces_violation_rate * 100:.2f} \\\\",
                r"\midrule",
                f"  Additional detections & {r.additional_detections} & -- \\\\",
                r"\bottomrule",
                r"\end{tabular}",
                r"\end{table}",
            ]
            tables["ces_comparison"] = "\n".join(lines)

        return tables


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    suite = BenchmarkSuite(seed=42)
    suite.run_all()

    print("\n\nLaTeX Tables:")
    print("-" * 70)
    for name, latex in suite.generate_paper_tables().items():
        print(f"\n% --- {name} ---")
        print(latex)
