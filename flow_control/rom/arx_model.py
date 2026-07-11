"""Minimal multi-output ARX model for input-output ROM identification.

The model is intentionally small and explicit.  It fits a linear map from
current/past exogenous inputs and past outputs to the current output:

    y[t] = c + A_1 y[t-1] + ... + A_na y[t-na]
           + B_0 u[t] + ... + B_nb u[t-nb+1]

During validation, recursive prediction uses measured history before the
validation boundary and then feeds back its own previous predictions.  It does
not use future outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ARXModel:
    """Small multi-output ARX model fitted by ridge-regularized least squares."""

    input_lags: int = 4
    output_lags: int = 3
    include_current_input: bool = True
    ridge_alpha: float = 1.0e-8
    coefficients_: np.ndarray | None = field(default=None, init=False, repr=False)
    feature_names_: list[str] = field(default_factory=list, init=False)
    input_names_: list[str] = field(default_factory=list, init=False)
    output_names_: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.input_lags < 1:
            raise ValueError("input_lags must be at least 1")
        if self.output_lags < 1:
            raise ValueError("output_lags must be at least 1")
        if self.ridge_alpha < 0:
            raise ValueError("ridge_alpha must be non-negative")

    @property
    def max_lag(self) -> int:
        input_history_lag = self.input_lags - 1 if self.include_current_input else self.input_lags
        return max(self.output_lags, input_history_lag)

    def fit(
        self,
        inputs: np.ndarray,
        outputs: np.ndarray,
        *,
        input_names: list[str] | tuple[str, ...] | None = None,
        output_names: list[str] | tuple[str, ...] | None = None,
    ) -> "ARXModel":
        """Fit on every usable row of the explicitly supplied arrays."""
        inputs = _as_2d_float(inputs, "inputs")
        outputs = _as_2d_float(outputs, "outputs")
        if inputs.shape[0] != outputs.shape[0]:
            raise ValueError("inputs and outputs must have the same row count")

        if inputs.shape[0] <= self.max_lag:
            raise ValueError(
                "training data is too short: "
                f"need more than max_lag={self.max_lag}, got {inputs.shape[0]}"
            )

        self.input_names_ = list(input_names or [f"u{idx}" for idx in range(inputs.shape[1])])
        self.output_names_ = list(output_names or [f"y{idx}" for idx in range(outputs.shape[1])])
        x_train, y_train = self._design_matrix(inputs, outputs, self.max_lag, inputs.shape[0])
        penalty = self.ridge_alpha * np.eye(x_train.shape[1], dtype=float)
        penalty[0, 0] = 0.0
        lhs = x_train.T @ x_train + penalty
        rhs = x_train.T @ y_train
        try:
            self.coefficients_ = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            self.coefficients_ = np.linalg.pinv(lhs) @ rhs
        return self

    def predict_one_step(self, inputs: np.ndarray, outputs: np.ndarray) -> np.ndarray:
        """Predict each row using measured past outputs."""
        self._require_fitted()
        inputs = _as_2d_float(inputs, "inputs")
        outputs = _as_2d_float(outputs, "outputs")
        predictions = np.full_like(outputs, np.nan, dtype=float)
        x_all, _ = self._design_matrix(inputs, outputs, self.max_lag, inputs.shape[0])
        predictions[self.max_lag :] = x_all @ self.coefficients_
        return predictions

    def predict_recursive(
        self,
        inputs: np.ndarray,
        observed_outputs: np.ndarray,
        *,
        start_index: int,
        end_index: int | None = None,
    ) -> np.ndarray:
        """Predict rows ``start_index <= t < end_index`` recursively.

        Rows before ``start_index`` are copied from ``observed_outputs`` as
        known history.  Rows at and after ``start_index`` never read measured
        outputs; previous predicted rows are fed back instead.
        """
        self._require_fitted()
        inputs = _as_2d_float(inputs, "inputs")
        observed_outputs = _as_2d_float(observed_outputs, "observed_outputs")
        if inputs.shape[0] != observed_outputs.shape[0]:
            raise ValueError("inputs and observed_outputs must have the same row count")
        start = int(start_index)
        end = inputs.shape[0] if end_index is None else int(end_index)
        if start < self.max_lag:
            raise ValueError(f"start_index must be >= max_lag={self.max_lag}")
        if end < start or end > inputs.shape[0]:
            raise ValueError("end_index must satisfy start_index <= end_index <= row count")

        work_outputs = np.asarray(observed_outputs, dtype=float).copy()
        predictions = np.full((end - start, observed_outputs.shape[1]), np.nan, dtype=float)
        for row_idx, t in enumerate(range(start, end)):
            feature = self._feature_at(t, inputs, work_outputs)
            y_hat = feature @ self.coefficients_
            work_outputs[t] = y_hat
            predictions[row_idx] = y_hat
        return predictions

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable model snapshot."""
        self._require_fitted()
        return {
            "model_type": "ARX",
            "input_lags": self.input_lags,
            "output_lags": self.output_lags,
            "include_current_input": self.include_current_input,
            "ridge_alpha": self.ridge_alpha,
            "input_names": self.input_names_,
            "output_names": self.output_names_,
            "feature_names": self.feature_names_,
            "coefficients": self.coefficients_.tolist(),
            "formula": (
                "y[t] = c + sum_i A_i*y[t-i] + "
                "sum_j B_j*u[t-j], with recursive validation feedback"
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ARXModel":
        """Restore an ARX model from ``to_dict()`` output."""
        if payload.get("model_type") != "ARX":
            raise ValueError("model snapshot must have model_type='ARX'")
        model = cls(
            input_lags=int(payload["input_lags"]),
            output_lags=int(payload["output_lags"]),
            include_current_input=bool(payload.get("include_current_input", True)),
            ridge_alpha=float(payload.get("ridge_alpha", 0.0)),
        )
        model.input_names_ = list(payload.get("input_names", []))
        model.output_names_ = list(payload.get("output_names", []))
        model.feature_names_ = list(payload.get("feature_names", []))
        model.coefficients_ = np.asarray(payload["coefficients"], dtype=float)
        return model

    def _design_matrix(
        self,
        inputs: np.ndarray,
        outputs: np.ndarray,
        start: int,
        end: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        rows = [self._feature_at(t, inputs, outputs) for t in range(start, end)]
        return np.vstack(rows), outputs[start:end]

    def _feature_at(self, t: int, inputs: np.ndarray, outputs: np.ndarray) -> np.ndarray:
        values: list[float] = [1.0]
        names: list[str] = ["intercept"]

        for lag in range(1, self.output_lags + 1):
            for col_idx, name in enumerate(self.output_names_ or [f"y{idx}" for idx in range(outputs.shape[1])]):
                values.append(float(outputs[t - lag, col_idx]))
                names.append(f"{name}(t-{lag})")

        start_lag = 0 if self.include_current_input else 1
        stop_lag = start_lag + self.input_lags
        for lag in range(start_lag, stop_lag):
            suffix = "t" if lag == 0 else f"t-{lag}"
            for col_idx, name in enumerate(self.input_names_ or [f"u{idx}" for idx in range(inputs.shape[1])]):
                values.append(float(inputs[t - lag, col_idx]))
                names.append(f"{name}({suffix})")

        if not self.feature_names_:
            self.feature_names_ = names
        return np.asarray(values, dtype=float)

    def _require_fitted(self) -> None:
        if self.coefficients_ is None:
            raise RuntimeError("ARXModel has not been fitted")


def _as_2d_float(values: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim != 2:
        raise ValueError(f"{name} must be a 2-D array")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} contains NaN or infinite values")
    return array
