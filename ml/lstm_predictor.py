"""Emission prediction for preventive compliance monitoring.

This module provides two predictor implementations for forecasting future
emission levels (CO2, NOx) and the composite emission score (CES) from a
sliding window of recent sensor readings:

1. **EmissionPredictor** (LSTM-based) — requires TensorFlow.  Uses a
   stacked LSTM architecture (128 -> 64 units) with dropout and batch
   normalisation.  Must be trained before use via :meth:`train`.
2. **MockPredictor** (linear extrapolation) — no dependencies.  Fits a
   first-order linear trend across the sliding window and extrapolates.
   Used as the default when TensorFlow is not installed.

When predicted values breach configurable thresholds the system can issue
early warnings *before* a compliance violation occurs, giving the driver
or fleet operator time to react.

Architecture overview (EmissionPredictor)
-----------------------------------------
Two LSTM layers (128 -> 64 units) with dropout regularisation (p=0.2) and
batch normalisation are followed by a dense projection that outputs a
(forecast_horizon x 3) tensor representing the predicted CO2, NOx, and CES
values for the next ``forecast_horizon`` time-steps (default 5,
corresponding to 25 s at a 5 s sampling interval).

Input features (8)
------------------
speed, rpm, fuel_rate, acceleration, co2, nox, vsp, ces_score

Training data
-------------
Pre-generated training data is available at ``ml/training_data.npy``
(9000 samples from 5 WLTC cycles).  Regenerate with::

    python -m ml.generate_training_data --cycles 5 --output ml/training_data.npy

References
----------
* Hochreiter, S. & Schmidhuber, J. (1997). Long Short-Term Memory.
  Neural Computation, 9(8), 1735-1780.
* Cho, K. et al. (2014). Learning Phrase Representations using RNN
  Encoder-Decoder for Statistical Machine Translation. arXiv:1406.1078.
* Graves, A. (2013). Generating Sequences With Recurrent Neural Networks.
  arXiv:1308.0850.
* Huber, P. J. (1964). Robust Estimation of a Location Parameter.
  Annals of Mathematical Statistics, 35(1), 73-101.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful TensorFlow import
# ---------------------------------------------------------------------------
_TF_AVAILABLE: bool = False
try:
    import tensorflow as tf  # type: ignore[import-untyped]
    from tensorflow import keras  # type: ignore[import-untyped]

    _TF_AVAILABLE = True
except ImportError:
    tf = None  # type: ignore[assignment]
    keras = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_FEATURE_NAMES: List[str] = [
    "speed",
    "rpm",
    "fuel_rate",
    "acceleration",
    "co2",
    "nox",
    "vsp",
    "ces_score",
]

CES_VIOLATION_THRESHOLD: float = 0.85
SAMPLING_INTERVAL_SECONDS: int = 5


# ===================================================================
# EmissionPredictor – LSTM-based predictor (requires TensorFlow)
# ===================================================================
class EmissionPredictor:
    """LSTM-based emission predictor for preventive compliance monitoring.

    Architecture
    ------------
    * LSTM layer 1: 128 units, return_sequences=True
    * Dropout: 0.2
    * Batch normalisation
    * LSTM layer 2: 64 units
    * Dropout: 0.2
    * Batch normalisation
    * Dense: forecast_horizon * 3 outputs (CO2, NOx, CES per step)
    * Reshape: (forecast_horizon, 3)

    Parameters
    ----------
    window_size : int
        Number of historical readings kept in the sliding window (default 20).
    forecast_horizon : int
        Number of future time-steps to predict (default 5).
    feature_names : list[str] | None
        Ordered list of input feature names.  Defaults to
        ``DEFAULT_FEATURE_NAMES`` (8 features).
    """

    def __init__(
        self,
        window_size: int = 20,
        forecast_horizon: int = 5,
        feature_names: Optional[List[str]] = None,
    ) -> None:
        if not _TF_AVAILABLE:
            raise ImportError(
                "TensorFlow is required to use EmissionPredictor. "
                "Install it with: pip install tensorflow"
            )

        self.window_size: int = window_size
        self.forecast_horizon: int = forecast_horizon
        self.feature_names: List[str] = (
            list(feature_names) if feature_names is not None else list(DEFAULT_FEATURE_NAMES)
        )
        self.n_features: int = len(self.feature_names)

        # Sliding window buffer -- stores the most recent *window_size* readings
        self._buffer: List[np.ndarray] = []

        # Build the Keras model
        self.model: keras.Model = self.build_model()

    # ------------------------------------------------------------------
    # Model construction
    # ------------------------------------------------------------------
    def build_model(self) -> keras.Model:
        """Create and compile the Keras Sequential LSTM model.

        Returns
        -------
        keras.Model
            Compiled model with Huber loss and Adam optimiser.
        """
        model = keras.Sequential(
            [
                keras.layers.LSTM(
                    128,
                    return_sequences=True,
                    input_shape=(self.window_size, self.n_features),
                    name="lstm_1",
                ),
                keras.layers.Dropout(0.2, name="dropout_1"),
                keras.layers.BatchNormalization(name="bn_1"),
                keras.layers.LSTM(64, return_sequences=False, name="lstm_2"),
                keras.layers.Dropout(0.2, name="dropout_2"),
                keras.layers.BatchNormalization(name="bn_2"),
                keras.layers.Dense(
                    self.forecast_horizon * 3,
                    activation="linear",
                    name="dense_output",
                ),
                keras.layers.Reshape(
                    (self.forecast_horizon, 3), name="reshape_output"
                ),
            ],
            name="emission_predictor",
        )
        model.compile(
            optimizer="adam",
            loss=keras.losses.Huber(),
            metrics=["mae"],
        )
        logger.info("EmissionPredictor model compiled successfully.")
        return model

    # ------------------------------------------------------------------
    # Sliding-window management
    # ------------------------------------------------------------------
    def update(self, reading: Dict[str, float]) -> None:
        """Append a single sensor reading to the sliding window buffer.

        Parameters
        ----------
        reading : dict[str, float]
            Mapping of feature name -> numeric value.  Must contain all
            keys in ``self.feature_names``.
        """
        vector = np.array(
            [float(reading[f]) for f in self.feature_names], dtype=np.float32
        )
        self._buffer.append(vector)
        if len(self._buffer) > self.window_size:
            self._buffer.pop(0)

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------
    def predict_next(self) -> Optional[Dict[str, Any]]:
        """Predict emission values for the next ``forecast_horizon`` time-steps.

        Returns ``None`` if the sliding window is not yet full.

        Returns
        -------
        dict | None
            A dictionary with keys:

            * **predictions** – list of *forecast_horizon* dicts, each
              containing ``co2``, ``nox``, and ``ces`` floats.
            * **warning** – ``True`` if any predicted CES exceeds the
              violation threshold (0.85).
            * **warning_message** – human-readable warning string.
            * **seconds_to_violation** – estimated seconds until the
              first threshold breach, or ``None``.
        """
        if len(self._buffer) < self.window_size:
            return None

        window = np.array(self._buffer[-self.window_size :], dtype=np.float32)
        x_input = window.reshape(1, self.window_size, self.n_features)

        raw_pred: np.ndarray = self.model.predict(x_input, verbose=0)
        # raw_pred shape: (1, forecast_horizon, 3)
        pred = raw_pred[0]  # (forecast_horizon, 3)

        return self._format_prediction(pred)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------
    def train(
        self,
        data: List[Dict[str, float]],
        epochs: int = 50,
        batch_size: int = 32,
    ) -> Any:
        """Train the model on historical sensor data.

        The method constructs input/output pairs using a sliding window
        approach over the provided data sequence.

        Parameters
        ----------
        data : list[dict[str, float]]
            Chronologically ordered sensor readings.
        epochs : int
            Number of training epochs (default 50).
        batch_size : int
            Mini-batch size (default 32).

        Returns
        -------
        keras.callbacks.History
            Training history object.
        """
        if len(data) < self.window_size + self.forecast_horizon:
            raise ValueError(
                f"Need at least {self.window_size + self.forecast_horizon} "
                f"data points, got {len(data)}."
            )

        # Build feature matrix
        feature_matrix = np.array(
            [[float(d[f]) for f in self.feature_names] for d in data],
            dtype=np.float32,
        )

        # Target indices inside the feature vector: co2=4, nox=5, ces_score=7
        target_indices = [
            self.feature_names.index("co2"),
            self.feature_names.index("nox"),
            self.feature_names.index("ces_score"),
        ]

        xs: List[np.ndarray] = []
        ys: List[np.ndarray] = []

        for i in range(len(feature_matrix) - self.window_size - self.forecast_horizon + 1):
            x_window = feature_matrix[i : i + self.window_size]
            y_horizon = feature_matrix[
                i + self.window_size : i + self.window_size + self.forecast_horizon
            ][:, target_indices]
            xs.append(x_window)
            ys.append(y_horizon)

        x_train = np.array(xs, dtype=np.float32)
        y_train = np.array(ys, dtype=np.float32)

        logger.info(
            "Training on %d samples (window=%d, horizon=%d).",
            len(x_train),
            self.window_size,
            self.forecast_horizon,
        )

        history = self.model.fit(
            x_train,
            y_train,
            epochs=epochs,
            batch_size=batch_size,
            validation_split=0.1,
            verbose=1,
        )
        return history

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save_model(self, path: str) -> None:
        """Save the trained Keras model to disk.

        Parameters
        ----------
        path : str
            File-system path (directory or ``.keras`` / ``.h5`` file).
        """
        self.model.save(path)
        logger.info("Model saved to %s", path)

    def load_model(self, path: str) -> None:
        """Load a previously saved Keras model from disk.

        Parameters
        ----------
        path : str
            File-system path used in a prior ``save_model`` call.
        """
        self.model = keras.models.load_model(path)
        logger.info("Model loaded from %s", path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_prediction(pred: np.ndarray) -> Dict[str, Any]:
        """Convert raw prediction array to the standard result dictionary.

        Parameters
        ----------
        pred : np.ndarray
            Array of shape ``(forecast_horizon, 3)`` with columns
            [co2, nox, ces].

        Returns
        -------
        dict
            Formatted prediction result.
        """
        predictions: List[Dict[str, float]] = []
        warning = False
        seconds_to_violation: Optional[int] = None

        for step_idx in range(pred.shape[0]):
            co2_val = float(pred[step_idx, 0])
            nox_val = float(pred[step_idx, 1])
            ces_val = float(pred[step_idx, 2])

            predictions.append({"co2": co2_val, "nox": nox_val, "ces": ces_val})

            if ces_val > CES_VIOLATION_THRESHOLD and not warning:
                warning = True
                seconds_to_violation = (step_idx + 1) * SAMPLING_INTERVAL_SECONDS

        warning_message = ""
        if warning and seconds_to_violation is not None:
            warning_message = (
                f"Predicted CES violation in {seconds_to_violation} seconds"
            )

        return {
            "predictions": predictions,
            "warning": warning,
            "warning_message": warning_message,
            "seconds_to_violation": seconds_to_violation,
        }


# ===================================================================
# MockPredictor – lightweight fallback without TensorFlow
# ===================================================================
class MockPredictor:
    """Fallback predictor that uses simple linear extrapolation.

    Provides the same public interface as :class:`EmissionPredictor` but
    does **not** require TensorFlow.  Useful for testing, demos, and
    environments where installing a full deep-learning stack is
    impractical.

    The prediction strategy fits a first-order linear trend across the
    sliding window for each target feature (CO2, NOx, CES) and
    extrapolates it forward.

    Parameters
    ----------
    window_size : int
        Number of historical readings kept in the sliding window (default 20).
    forecast_horizon : int
        Number of future time-steps to predict (default 5).
    feature_names : list[str] | None
        Ordered list of input feature names.  Defaults to
        ``DEFAULT_FEATURE_NAMES``.
    """

    def __init__(
        self,
        window_size: int = 20,
        forecast_horizon: int = 5,
        feature_names: Optional[List[str]] = None,
    ) -> None:
        self.window_size: int = window_size
        self.forecast_horizon: int = forecast_horizon
        self.feature_names: List[str] = (
            list(feature_names) if feature_names is not None else list(DEFAULT_FEATURE_NAMES)
        )
        self.n_features: int = len(self.feature_names)

        self._buffer: List[Dict[str, float]] = []
        self.model: Optional[Any] = None  # no real model

    # ------------------------------------------------------------------
    def build_model(self) -> None:
        """No-op model builder (mock does not use a neural network).

        Returns
        -------
        None
        """
        logger.info("MockPredictor: no model to build.")
        return None

    # ------------------------------------------------------------------
    def update(self, reading: Dict[str, float]) -> None:
        """Append a single sensor reading to the sliding window buffer.

        Parameters
        ----------
        reading : dict[str, float]
            Mapping of feature name -> numeric value.
        """
        self._buffer.append(dict(reading))
        if len(self._buffer) > self.window_size:
            self._buffer.pop(0)

    # ------------------------------------------------------------------
    def predict_next(self) -> Optional[Dict[str, Any]]:
        """Predict future emissions via linear extrapolation.

        Returns ``None`` if the sliding window is not yet full.

        Returns
        -------
        dict | None
            Same structure as :meth:`EmissionPredictor.predict_next`.
        """
        if len(self._buffer) < self.window_size:
            return None

        window = self._buffer[-self.window_size :]
        target_keys = ["co2", "nox", "ces_score"]

        pred = np.zeros((self.forecast_horizon, 3), dtype=np.float64)

        for col_idx, key in enumerate(target_keys):
            values = np.array([r.get(key, 0.0) for r in window], dtype=np.float64)
            # Simple least-squares linear fit: y = slope * t + intercept
            t = np.arange(len(values), dtype=np.float64)
            slope, intercept = np.polyfit(t, values, 1)
            for step in range(self.forecast_horizon):
                future_t = float(len(values) + step)
                pred[step, col_idx] = slope * future_t + intercept

        return EmissionPredictor._format_prediction(pred)

    # ------------------------------------------------------------------
    def train(
        self,
        data: List[Dict[str, float]],
        epochs: int = 50,
        batch_size: int = 32,
    ) -> None:
        """No-op training (mock predictor uses linear extrapolation).

        Parameters
        ----------
        data : list[dict[str, float]]
            Ignored – kept for interface compatibility.
        epochs : int
            Ignored.
        batch_size : int
            Ignored.

        Returns
        -------
        None
        """
        logger.info(
            "MockPredictor: train() is a no-op. %d records ignored.", len(data)
        )

    # ------------------------------------------------------------------
    def save_model(self, path: str) -> None:
        """No-op model save (mock predictor has no persistent state).

        Parameters
        ----------
        path : str
            Ignored.
        """
        logger.info("MockPredictor: save_model() is a no-op (path=%s).", path)

    def load_model(self, path: str) -> None:
        """No-op model load (mock predictor has no persistent state).

        Parameters
        ----------
        path : str
            Ignored.
        """
        logger.info("MockPredictor: load_model() is a no-op (path=%s).", path)


# ===================================================================
# Factory
# ===================================================================
def create_predictor(
    use_lstm: bool = True,
) -> Union[EmissionPredictor, MockPredictor]:
    """Create an emission predictor instance.

    Returns an :class:`EmissionPredictor` when TensorFlow is available
    and ``use_lstm`` is ``True``; otherwise falls back to
    :class:`MockPredictor`.

    Parameters
    ----------
    use_lstm : bool
        If ``True`` (default), attempt to build the LSTM predictor.

    Returns
    -------
    EmissionPredictor | MockPredictor
        Ready-to-use predictor instance.
    """
    if use_lstm and _TF_AVAILABLE:
        logger.info("Creating LSTM EmissionPredictor (TensorFlow available).")
        return EmissionPredictor()

    if use_lstm and not _TF_AVAILABLE:
        logger.warning(
            "TensorFlow not available — falling back to MockPredictor."
        )

    return MockPredictor()
