"""
SmartPUC — OBD-to-Tailpipe Calibration Model
=============================================

Learns the systematic gap between OBD-inferred emissions and real tailpipe
measurements using gradient-boosted regression (XGBoost). Once trained on
paired (OBD, tailpipe) observations, the model corrects future OBD readings
to predict what a 5-gas analyzer would measure.

**Training data:** Synthetic paired dataset grounded in COPERT 5 degradation
curves and published PEMS measurement noise. See ``scripts/generate_paired_dataset.py``.

**Architecture:** Five independent XGBoost regressors, one per pollutant:
    CO2, CO, NOx, HC, PM2.5
Each predicts the *gap* (tailpipe - OBD) from OBD features + vehicle metadata.

**Disclosure:** The current model is trained on synthetic paired data. Real-world
paired measurements from PUC centers are required before regulatory deployment.
The architecture is designed to hot-swap from synthetic to real training data
without code changes.

References:
    - Chen, T., Guestrin, C. (2016). XGBoost: A Scalable Tree Boosting System. KDD.
    - NAEI Emission Degradation Methodology (2024)
    - COPERT 5 v5.6, Emisia
"""

from __future__ import annotations

import os
import pickle
from typing import Dict, Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

try:
    from xgboost import XGBRegressor
    _HAS_XGBOOST = True
except ImportError:
    from sklearn.ensemble import GradientBoostingRegressor as XGBRegressor
    _HAS_XGBOOST = False

# CES weights and thresholds for calibrated CES computation
from backend.ces_constants import (
    CES_WEIGHTS,
    BSVI_THRESHOLDS_PETROL,
    BSVI_THRESHOLDS_DIESEL,
    BS4_THRESHOLDS_PETROL,
    BS4_THRESHOLDS_DIESEL,
)


def _get_thresholds(fuel_type: str, bs_standard: str) -> Dict[str, float]:
    """Return the pollutant thresholds for a given fuel type and BS standard."""
    if bs_standard.upper() in ("BS6", "BSVI"):
        return BSVI_THRESHOLDS_PETROL if fuel_type == "petrol" else BSVI_THRESHOLDS_DIESEL
    else:
        return BS4_THRESHOLDS_PETROL if fuel_type == "petrol" else BS4_THRESHOLDS_DIESEL


class CalibrationModel:
    """XGBoost-based OBD-to-tailpipe calibration engine."""

    POLLUTANTS = ["co2", "co", "nox", "hc", "pm25"]

    # Features used for training (must exist in paired CSV or be derivable)
    FEATURE_COLUMNS = [
        "speed_kmh", "rpm", "fuel_rate", "acceleration",
        "mileage_km", "age_years",
        "bs_standard_encoded",   # 0 = BS6, 1 = BS4
        "fuel_type_encoded",     # 0 = petrol, 1 = diesel
        # Derived features:
        "fuel_efficiency",       # co2 / speed if speed > 0
        "power_proxy",           # speed * acceleration
        "rpm_speed_ratio",       # rpm / speed if speed > 0
    ]

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: int = 6,
        learning_rate: float = 0.1,
        subsample: float = 0.8,
        random_state: int = 42,
    ):
        """Initialize with XGBoost hyperparameters."""
        self._models: Dict[str, XGBRegressor] = {}
        self._is_trained: bool = False
        self._eval_metrics: dict = {}
        self._feature_names: list = list(self.FEATURE_COLUMNS)
        self._trained_on: Optional[str] = None
        self._n_train: int = 0
        self._n_test: int = 0

        # Store hyperparams for model construction
        self._hparams = dict(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            subsample=subsample,
            random_state=random_state,
        )

    # ── helpers ──────────────────────────────────────────────────────────

    def _make_regressor(self) -> XGBRegressor:
        """Construct a fresh regressor with stored hyperparameters."""
        if _HAS_XGBOOST:
            return XGBRegressor(
                n_estimators=self._hparams["n_estimators"],
                max_depth=self._hparams["max_depth"],
                learning_rate=self._hparams["learning_rate"],
                subsample=self._hparams["subsample"],
                random_state=self._hparams["random_state"],
                verbosity=0,
            )
        else:
            return XGBRegressor(
                n_estimators=self._hparams["n_estimators"],
                max_depth=self._hparams["max_depth"],
                learning_rate=self._hparams["learning_rate"],
                subsample=self._hparams["subsample"],
                random_state=self._hparams["random_state"],
            )

    @staticmethod
    def _add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
        """Add derived feature columns in-place and return the dataframe."""
        # Encode categoricals
        if "bs_standard" in df.columns and "bs_standard_encoded" not in df.columns:
            df["bs_standard_encoded"] = (
                df["bs_standard"].str.upper().map({"BS4": 1, "BS6": 0}).fillna(0).astype(int)
            )
        if "fuel_type" in df.columns and "fuel_type_encoded" not in df.columns:
            df["fuel_type_encoded"] = (
                df["fuel_type"].str.lower().map({"petrol": 0, "diesel": 1}).fillna(0).astype(int)
            )

        # Derived features
        speed = df["speed_kmh"].replace(0, np.nan)
        df["fuel_efficiency"] = df.get("obd_co2", pd.Series(0, index=df.index)) / speed
        df["fuel_efficiency"] = df["fuel_efficiency"].fillna(0.0)

        df["power_proxy"] = df["speed_kmh"] * df["acceleration"]

        df["rpm_speed_ratio"] = df["rpm"] / speed
        df["rpm_speed_ratio"] = df["rpm_speed_ratio"].fillna(0.0)

        return df

    # ── public API ───────────────────────────────────────────────────────

    def train(self, paired_csv_path: str, test_size: float = 0.2) -> dict:
        """Train on a paired dataset CSV.

        CSV expected columns: obd_co2, obd_co, ..., tailpipe_co2, tailpipe_co, ...
        plus feature columns (speed_kmh, rpm, fuel_rate, etc.)

        Returns: dict with R2, MAE, RMSE per pollutant.
        """
        df = pd.read_csv(paired_csv_path)

        # Add derived features and encode categoricals
        df = self._add_derived_features(df)

        # Compute gap targets
        for p in self.POLLUTANTS:
            df[f"gap_{p}"] = df[f"tailpipe_{p}"] - df[f"obd_{p}"]

        # Ensure all feature columns exist
        for col in self.FEATURE_COLUMNS:
            if col not in df.columns:
                df[col] = 0.0

        X = df[self.FEATURE_COLUMNS].copy()
        X = X.replace([np.inf, -np.inf], 0.0).fillna(0.0)

        # Train/test split (stratified by vehicle_class if available)
        stratify_col = None
        if "vehicle_class" in df.columns:
            stratify_col = df["vehicle_class"]

        X_train, X_test, idx_train, idx_test = train_test_split(
            X, df.index, test_size=test_size,
            random_state=self._hparams["random_state"],
            stratify=stratify_col,
        )

        metrics: dict = {}
        for p in self.POLLUTANTS:
            y = df[f"gap_{p}"]
            y_train = y.loc[idx_train]
            y_test = y.loc[idx_test]

            model = self._make_regressor()
            model.fit(X_train, y_train)

            y_pred = model.predict(X_test)
            r2 = r2_score(y_test, y_pred)
            mae = mean_absolute_error(y_test, y_pred)
            rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))

            self._models[p] = model
            metrics[p] = {"r2": round(r2, 4), "mae": round(mae, 6), "rmse": round(rmse, 6)}

        # Overall summary
        r2_values = [metrics[p]["r2"] for p in self.POLLUTANTS]
        metrics["overall_r2_mean"] = round(float(np.mean(r2_values)), 4)
        metrics["trained_on"] = str(paired_csv_path)
        metrics["n_train"] = len(idx_train)
        metrics["n_test"] = len(idx_test)

        self._eval_metrics = metrics
        self._is_trained = True
        self._trained_on = str(paired_csv_path)
        self._n_train = len(idx_train)
        self._n_test = len(idx_test)

        return metrics

    def calibrate(
        self,
        obd_reading: dict,
        mileage_km: float = 50000,
        age_years: float = 3,
        bs_standard: str = "BS6",
        fuel_type: str = "petrol",
    ) -> dict:
        """Calibrate an OBD reading to predict tailpipe values.

        Args:
            obd_reading: dict with keys speed (or speed_kmh), rpm, fuel_rate,
                         acceleration, co2_g_per_km, co_g_per_km, etc.
            mileage_km: vehicle mileage for the correction model
            age_years: vehicle age
            bs_standard: "BS6" or "BS4"
            fuel_type: "petrol" or "diesel"

        Returns: dict with calibrated values, raw values, gaps, and calibrated CES.
        """
        if not self._is_trained:
            raise RuntimeError(
                "CalibrationModel has not been trained yet. Call train() first."
            )

        # Normalise speed key
        speed = obd_reading.get("speed_kmh", obd_reading.get("speed", 0.0))
        rpm = obd_reading.get("rpm", 0.0)
        fuel_rate = obd_reading.get("fuel_rate", 0.0)
        acceleration = obd_reading.get("acceleration", 0.0)

        # Raw OBD pollutant values (accept both short and _g_per_km forms)
        raw: Dict[str, float] = {}
        for p in self.POLLUTANTS:
            raw[p] = obd_reading.get(
                f"{p}_g_per_km",
                obd_reading.get(f"obd_{p}", obd_reading.get(p, 0.0)),
            )

        # Build feature vector
        bs_enc = 1 if bs_standard.upper() == "BS4" else 0
        ft_enc = 1 if fuel_type.lower() == "diesel" else 0
        safe_speed = speed if speed > 0 else np.nan

        features = {
            "speed_kmh": speed,
            "rpm": rpm,
            "fuel_rate": fuel_rate,
            "acceleration": acceleration,
            "mileage_km": mileage_km,
            "age_years": age_years,
            "bs_standard_encoded": bs_enc,
            "fuel_type_encoded": ft_enc,
            "fuel_efficiency": (raw["co2"] / safe_speed) if safe_speed and safe_speed > 0 else 0.0,
            "power_proxy": speed * acceleration,
            "rpm_speed_ratio": (rpm / safe_speed) if safe_speed and safe_speed > 0 else 0.0,
        }

        X = pd.DataFrame([features], columns=self.FEATURE_COLUMNS)
        X = X.replace([np.inf, -np.inf], 0.0).fillna(0.0)

        # Predict gaps and compute calibrated values
        result: dict = {}
        calibrated: Dict[str, float] = {}
        for p in self.POLLUTANTS:
            gap = float(self._models[p].predict(X)[0])
            cal = max(0.0, raw[p] + gap)
            calibrated[p] = cal
            result[f"calibrated_{p}"] = round(cal, 6)
            result[f"raw_{p}"] = round(raw[p], 6)
            result[f"gap_{p}"] = round(gap, 6)

        # Compute calibrated CES
        thresholds = _get_thresholds(fuel_type, bs_standard)
        ces = sum(
            (calibrated[p] / thresholds[p]) * CES_WEIGHTS[p]
            for p in self.POLLUTANTS
        )
        result["calibrated_ces"] = round(ces, 4)

        # Confidence proxy: mean R2 of trained models
        if self._eval_metrics:
            r2_vals = [
                self._eval_metrics[p]["r2"]
                for p in self.POLLUTANTS
                if p in self._eval_metrics
            ]
            result["confidence"] = round(float(np.mean(r2_vals)), 4) if r2_vals else 0.0
        else:
            result["confidence"] = 0.0

        return result

    def evaluate(self) -> dict:
        """Return the holdout evaluation metrics from the last train() call."""
        return dict(self._eval_metrics)

    def feature_importance(self, pollutant: str = "co2") -> dict:
        """Return feature importance scores for a pollutant model.

        Returns: dict mapping feature_name -> importance_score (gain-based).
        """
        if not self._is_trained or pollutant not in self._models:
            raise RuntimeError(
                f"No trained model for pollutant '{pollutant}'. Call train() first."
            )

        model = self._models[pollutant]
        if _HAS_XGBOOST:
            importances = model.feature_importances_
        else:
            importances = model.feature_importances_

        return {
            name: round(float(score), 6)
            for name, score in zip(self.FEATURE_COLUMNS, importances)
        }

    def save_checkpoint(self, path: str = "data/calibration_model_v1.pkl") -> None:
        """Serialize all 5 models + metadata to a pickle file."""
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        payload = {
            "models": self._models,
            "is_trained": self._is_trained,
            "eval_metrics": self._eval_metrics,
            "feature_names": self._feature_names,
            "hparams": self._hparams,
            "trained_on": self._trained_on,
            "n_train": self._n_train,
            "n_test": self._n_test,
            "has_xgboost": _HAS_XGBOOST,
        }
        with open(path, "wb") as f:
            pickle.dump(payload, f)

    @classmethod
    def load_checkpoint(cls, path: str = "data/calibration_model_v1.pkl") -> "CalibrationModel":
        """Load a previously trained model from checkpoint."""
        with open(path, "rb") as f:
            payload = pickle.load(f)

        obj = cls()
        obj._models = payload["models"]
        obj._is_trained = payload["is_trained"]
        obj._eval_metrics = payload["eval_metrics"]
        obj._feature_names = payload.get("feature_names", list(cls.FEATURE_COLUMNS))
        obj._hparams = payload.get("hparams", obj._hparams)
        obj._trained_on = payload.get("trained_on")
        obj._n_train = payload.get("n_train", 0)
        obj._n_test = payload.get("n_test", 0)
        return obj

    @property
    def is_trained(self) -> bool:
        return self._is_trained
