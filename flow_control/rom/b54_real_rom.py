"""B54 真实 6x6 ARX+Ridge ROM 训练与独立验证工作流。

该模块有意与既有 mock/B06 ROM 分离。B54 的数据契约严格限定为：

* 输入：六个代表喷口的 ``actual_massflow_XX``；
* 输出：六个承载区域相对显式无喷气算例的气动力变化；
* 模型：带训练集标准化的多输出 ARX + 岭回归；
* 评价：独立 case 上的一步预测和连续滚动预测。

验证数据从不参与基准估计、标准化、模型拟合或超参数选择。数据不完整、
实际质量流量缺失、质量门禁失败或训练/验证不独立时，流程 fail-closed，
只写 BLOCKED 指标与说明，不生成可被误认为真实结果的模型。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import shlex
import sys
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import yaml

from .arx_model import ARXModel


REGION_FORCE_COLUMNS = (
    "Fz_S1L",
    "Fz_S1R",
    "Fz_S2L",
    "Fz_S2R",
    "Fz_S3L",
    "Fz_S3R",
)
DEFAULT_REPRESENTATIVE_JETS = (2, 3, 9, 11, 17, 19)
ALL_ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{index:02d}" for index in range(1, 25))
SCHEMA_VERSION = "B04_real_ROM_v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "b54" / "real_rom.yaml"
# This is intentionally a numerical, not physical, ceiling.  It is many orders
# above any plausible force/massflow while keeping all downstream differences,
# sums of squares and diagnostic statistics representable in float64.
NUMERIC_ABS_LIMIT = 1.0e100


class RealROMDataError(ValueError):
    """Raised when real-data or independence gates prevent a B54 run."""

    def __init__(self, issues: str | Sequence[str]):
        values = [str(issues)] if isinstance(issues, str) else [str(item) for item in issues]
        self.issues = tuple(values)
        super().__init__("; ".join(values))


@dataclass(frozen=True)
class RealROMCase:
    """One checked physical case after optional deterministic subsampling."""

    case_id: str
    case_dir: Path
    timeseries_path: Path
    timeseries_sha256: str
    quality_status: str
    quality_report_path: Path
    quality_report_sha256: str
    manifest_path: Path
    manifest_sha256: str
    provenance_tokens: tuple[str, ...]
    case_type: str
    nojet_drift_flag_count: int
    nojet_jump_flag_count: int
    baseline_review_approved: bool
    compatibility_signature: dict[str, Any]
    time: np.ndarray
    inputs: np.ndarray
    all_actual_massflows: np.ndarray
    all_actual_columns: tuple[str, ...]
    forces: np.ndarray
    raw_rows: int
    raw_time_step_s: float
    sample_stride: int = 1

    @property
    def effective_time_step_s(self) -> float:
        return self.raw_time_step_s * self.sample_stride

    def subsampled(self, stride: int) -> "RealROMCase":
        if stride < 1:
            raise ValueError("sample_stride must be at least 1")
        if stride == 1:
            return self
        return replace(
            self,
            time=self.time[::stride].copy(),
            inputs=self.inputs[::stride].copy(),
            all_actual_massflows=self.all_actual_massflows[::stride].copy(),
            forces=self.forces[::stride].copy(),
            sample_stride=stride,
        )


@dataclass(frozen=True)
class RealROMSequence:
    """Checked model sequence with baseline-relative outputs."""

    case: RealROMCase
    delta_forces: np.ndarray


@dataclass(frozen=True)
class PredictionBundle:
    """Truth and both requested prediction modes for one sequence."""

    sequence: RealROMSequence
    one_step: np.ndarray
    rolling: np.ndarray


@dataclass(frozen=True)
class RealROMRunResult:
    """Paths and key statistics from a successful B54 run."""

    output_dir: Path
    model_path: Path
    metrics_path: Path
    result_path: Path
    training_plot_path: Path
    validation_plot_path: Path
    training_predictions_path: Path
    validation_predictions_path: Path
    training_fit_rows: int
    validation_rows: int


@dataclass
class ScaledRealARXModel:
    """Training-scaled six-input/six-output ARX ridge model."""

    arx: ARXModel
    input_mean: np.ndarray
    input_scale: np.ndarray
    output_mean: np.ndarray
    output_scale: np.ndarray

    def __post_init__(self) -> None:
        """Reject snapshots that do not exactly implement the B54 6x6 contract."""

        if not isinstance(self.arx, ARXModel):
            raise ValueError("real ROM arx must be an ARXModel")
        if type(self.arx.input_lags) is not int or self.arx.input_lags < 1:
            raise ValueError("real ROM input_lags must be an integer >= 1")
        if type(self.arx.output_lags) is not int or self.arx.output_lags < 1:
            raise ValueError("real ROM output_lags must be an integer >= 1")
        if type(self.arx.include_current_input) is not bool:
            raise ValueError("real ROM include_current_input must be boolean")
        if (
            isinstance(self.arx.ridge_alpha, bool)
            or not isinstance(self.arx.ridge_alpha, (int, float))
            or not math.isfinite(float(self.arx.ridge_alpha))
            or float(self.arx.ridge_alpha) < 0.0
        ):
            raise ValueError("real ROM ridge_alpha must be finite and non-negative")

        input_names = tuple(self.arx.input_names_)
        expected_output_names = tuple(f"delta_{column}" for column in REGION_FORCE_COLUMNS)
        if (
            len(input_names) != 6
            or len(set(input_names)) != 6
            or any(name not in ALL_ACTUAL_MASSFLOW_COLUMNS for name in input_names)
        ):
            raise ValueError(
                "real ROM input_names must be six unique actual_massflow_01..24 columns"
            )
        if tuple(self.arx.output_names_) != expected_output_names:
            raise ValueError("real ROM output_names do not match the ordered six-region contract")

        expected_feature_names = ["intercept"]
        for lag in range(1, self.arx.output_lags + 1):
            expected_feature_names.extend(
                f"{name}(t-{lag})" for name in expected_output_names
            )
        start_lag = 0 if self.arx.include_current_input else 1
        for lag in range(start_lag, start_lag + self.arx.input_lags):
            suffix = "t" if lag == 0 else f"t-{lag}"
            expected_feature_names.extend(f"{name}({suffix})" for name in input_names)
        if list(self.arx.feature_names_) != expected_feature_names:
            raise ValueError("real ROM feature_names do not match its ARX lag definition")

        coefficients = np.asarray(self.arx.coefficients_, dtype=float)
        expected_shape = (len(expected_feature_names), len(expected_output_names))
        if coefficients.shape != expected_shape:
            raise ValueError(
                f"real ROM coefficients must have shape {expected_shape}, got {coefficients.shape}"
            )
        if not np.all(np.isfinite(coefficients)):
            raise ValueError("real ROM coefficients contain NaN or infinite values")
        if np.any(np.abs(coefficients) > NUMERIC_ABS_LIMIT):
            raise ValueError("real ROM coefficients exceed the numeric safety limit")
        self.arx.coefficients_ = coefficients.copy()

        self.input_mean = _validated_scaler_vector(self.input_mean, "input_mean")
        self.input_scale = _validated_scaler_vector(
            self.input_scale,
            "input_scale",
            require_training_scale=True,
        )
        self.output_mean = _validated_scaler_vector(self.output_mean, "output_mean")
        self.output_scale = _validated_scaler_vector(
            self.output_scale,
            "output_scale",
            require_training_scale=True,
        )

    def predict_one_step(self, inputs: np.ndarray, outputs: np.ndarray) -> np.ndarray:
        u = self._scale_inputs(inputs)
        y = self._scale_outputs(outputs)
        if u.shape[0] != y.shape[0]:
            raise ValueError("inputs and outputs must have the same row count")
        prediction = self.arx.predict_one_step(u, y)
        result = self._unscale_outputs(prediction)
        _validate_prediction_tail(result, self.arx.max_lag, "one-step prediction")
        return result

    def predict_rolling(self, inputs: np.ndarray, outputs: np.ndarray) -> np.ndarray:
        u = self._scale_inputs(inputs)
        y = self._scale_outputs(outputs)
        if u.shape[0] != y.shape[0]:
            raise ValueError("inputs and outputs must have the same row count")
        recursive = self.arx.predict_recursive(u, y, start_index=self.arx.max_lag)
        prediction = np.full_like(outputs, np.nan, dtype=float)
        prediction[self.arx.max_lag :] = self._unscale_outputs(recursive)
        _validate_prediction_tail(prediction, self.arx.max_lag, "rolling prediction")
        return prediction

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "model_type": "scaled_multioutput_ARX_ridge",
            "data_contract": {
                "input_columns": list(self.arx.input_names_),
                "input_count": 6,
                "output_columns": list(REGION_FORCE_COLUMNS),
                "output_count": 6,
            },
            "arx": self.arx.to_dict(),
            "scaling": {
                "fit_source": "training cases only",
                "input_mean": self.input_mean.tolist(),
                "input_scale": self.input_scale.tolist(),
                "output_mean": self.output_mean.tolist(),
                "output_scale": self.output_scale.tolist(),
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScaledRealARXModel":
        if not isinstance(payload, dict):
            raise ValueError("real ROM snapshot must contain a mapping")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported real ROM schema: {payload.get('schema_version')!r}")
        if payload.get("model_type") != "scaled_multioutput_ARX_ridge":
            raise ValueError("real ROM snapshot has an unsupported model_type")
        arx_payload = payload.get("arx")
        scaling = payload.get("scaling")
        data_contract = payload.get("data_contract")
        if (
            not isinstance(arx_payload, dict)
            or not isinstance(scaling, dict)
            or not isinstance(data_contract, dict)
        ):
            raise ValueError(
                "real ROM snapshot must contain data_contract, arx and scaling mappings"
            )
        if (
            data_contract.get("input_columns") != arx_payload.get("input_names")
            or data_contract.get("input_count") != 6
            or data_contract.get("output_columns") != list(REGION_FORCE_COLUMNS)
            or data_contract.get("output_count") != 6
        ):
            raise ValueError("real ROM data_contract does not match its ordered 6x6 model names")
        for field in ("input_lags", "output_lags"):
            if type(arx_payload.get(field)) is not int:
                raise ValueError(f"real ROM {field} must be a JSON integer")
        if type(arx_payload.get("include_current_input")) is not bool:
            raise ValueError("real ROM include_current_input must be a JSON boolean")
        ridge_alpha = arx_payload.get("ridge_alpha")
        if (
            isinstance(ridge_alpha, bool)
            or not isinstance(ridge_alpha, (int, float))
            or not math.isfinite(float(ridge_alpha))
            or float(ridge_alpha) < 0.0
        ):
            raise ValueError("real ROM ridge_alpha must be finite and non-negative")
        if scaling.get("fit_source") != "training cases only":
            raise ValueError("real ROM scaling must be fitted from training cases only")
        required_scalers = ("input_mean", "input_scale", "output_mean", "output_scale")
        if any(field not in scaling for field in required_scalers):
            raise ValueError("real ROM scaling is incomplete")
        try:
            return cls(
                arx=ARXModel.from_dict(arx_payload),
                input_mean=np.asarray(scaling["input_mean"], dtype=float),
                input_scale=np.asarray(scaling["input_scale"], dtype=float),
                output_mean=np.asarray(scaling["output_mean"], dtype=float),
                output_scale=np.asarray(scaling["output_scale"], dtype=float),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid real ROM snapshot: {exc}") from exc

    def _scale_inputs(self, values: np.ndarray) -> np.ndarray:
        array = _validated_model_matrix(values, "inputs")
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            scaled = array / self.input_scale - self.input_mean / self.input_scale
        if not np.all(np.isfinite(scaled)):
            raise ValueError("input scaling produced non-finite values")
        return scaled

    def _scale_outputs(self, values: np.ndarray) -> np.ndarray:
        array = _validated_model_matrix(values, "outputs")
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            scaled = array / self.output_scale - self.output_mean / self.output_scale
        if not np.all(np.isfinite(scaled)):
            raise ValueError("output scaling produced non-finite values")
        return scaled

    def _unscale_outputs(self, values: np.ndarray) -> np.ndarray:
        array = np.asarray(values, dtype=float)
        if array.ndim != 2 or array.shape[1] != 6:
            raise ValueError("scaled predictions must be a two-dimensional six-column array")
        with np.errstate(over="ignore", invalid="ignore"):
            return array * self.output_scale + self.output_mean


def _validated_scaler_vector(
    values: np.ndarray,
    label: str,
    *,
    require_training_scale: bool = False,
) -> np.ndarray:
    """Return one immutable-shape, finite six-element scaler vector."""

    array = np.asarray(values, dtype=float)
    if array.shape != (6,):
        raise ValueError(f"real ROM {label} must have shape (6,), got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"real ROM {label} contains NaN or infinite values")
    if np.any(np.abs(array) > NUMERIC_ABS_LIMIT):
        raise ValueError(f"real ROM {label} exceeds the numeric safety limit")
    if require_training_scale and np.any(array <= 1.0e-12):
        raise ValueError(f"real ROM {label} must be greater than 1e-12")
    return array.copy()


def _validated_model_matrix(values: np.ndarray, label: str) -> np.ndarray:
    """Require exact N-by-6 finite model I/O and forbid NumPy broadcasting."""

    array = np.asarray(values, dtype=float)
    if array.ndim != 2 or array.shape[1] != 6:
        raise ValueError(f"{label} must be a two-dimensional six-column array")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{label} contain NaN or infinite values")
    if np.any(np.abs(array) > NUMERIC_ABS_LIMIT):
        raise ValueError(f"{label} exceed the numeric safety limit")
    return array


def _validate_prediction_tail(values: np.ndarray, start: int, label: str) -> None:
    tail = np.asarray(values, dtype=float)[start:]
    if not np.all(np.isfinite(tail)):
        raise ValueError(f"{label} contains NaN or infinite values")
    if np.any(np.abs(tail) > NUMERIC_ABS_LIMIT):
        raise ValueError(f"{label} exceeds the numeric safety limit")


def actual_massflow_columns(jets: Sequence[int]) -> tuple[str, ...]:
    """Return the exact actual-flow columns for the six configured jets."""

    return tuple(f"actual_massflow_{int(jet):02d}" for jet in jets)


def run_b54_real_rom(
    config_path: str | Path,
    output_dir: str | Path | None = None,
) -> RealROMRunResult:
    """Train and independently validate the B54 real ROM.

    A failed data gate writes ``B04_real_ROM_metrics.json`` and
    ``B04_real_ROM_result.md`` with ``status=BLOCKED``, then raises
    :class:`RealROMDataError`. No model or prediction plot is written.
    """

    config_file = Path(config_path).resolve()
    try:
        raw_config = _read_yaml(config_file)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        data_error = RealROMDataError(
            f"invalid B54 configuration file: {type(exc).__name__}: {exc}"
        )
        fallback_output = _resolve_output_dir_from_raw(
            {},
            config_file=config_file,
            output_dir=output_dir,
        )
        _write_invalid_config_delivery(
            fallback_output,
            config_file=config_file,
            issues=data_error.issues,
        )
        raise data_error
    try:
        config = _normalise_config(raw_config, config_file=config_file, output_dir=output_dir)
    except (RealROMDataError, TypeError, ValueError) as exc:
        data_error = (
            exc
            if isinstance(exc, RealROMDataError)
            else RealROMDataError(f"invalid B54 configuration: {type(exc).__name__}: {exc}")
        )
        fallback_output = _resolve_output_dir_from_raw(
            raw_config,
            config_file=config_file,
            output_dir=output_dir,
        )
        if fallback_output is not None:
            _write_invalid_config_delivery(
                fallback_output,
                config_file=config_file,
                issues=data_error.issues,
            )
        raise data_error
    out_dir = Path(config["output_dir"])

    try:
        _validate_numeric_config(config)
        loaded = _load_and_check_cases(config)
        baseline_case = loaded["baseline"]
        training_cases = loaded["training"]
        validation_cases = loaded["validation"]
        _check_partition_independence(baseline_case, training_cases, validation_cases)
        _check_physical_compatibility([baseline_case, *training_cases, *validation_cases])
        baseline_vector, baseline_rows = _estimate_baseline(baseline_case, config)
        _check_nonrepresentative_jet_inactivity(training_cases, validation_cases, config)
        stride = int(config["preprocessing"]["sample_stride"])
        training = [
            RealROMSequence(case=case.subsampled(stride), delta_forces=case.forces[::stride] - baseline_vector)
            for case in training_cases
        ]
        validation = [
            RealROMSequence(case=case.subsampled(stride), delta_forces=case.forces[::stride] - baseline_vector)
            for case in validation_cases
        ]
        _check_effective_time_steps(training, validation)
        _check_excitation_coverage(
            [item.case for item in training],
            [item.case for item in validation],
            config,
        )
        model, training_fit_rows = _fit_scaled_model(training, config)
        _check_diagnostic_coverage(training, validation, model.arx.max_lag, config)
        training_predictions = _predict_sequences(model, training)
        validation_predictions = _predict_sequences(model, validation)
    except RealROMDataError as exc:
        _write_blocked_delivery(out_dir, config, exc.issues)
        raise
    except Exception as exc:
        issue = f"data/model stage failed closed: {type(exc).__name__}: {exc}"
        _write_blocked_delivery(out_dir, config, (issue,))
        raise RealROMDataError(issue) from exc

    try:
        paths = _delivery_paths(out_dir)
        metrics = _build_metrics_payload(
            config=config,
            model=model,
            baseline_case=baseline_case,
            baseline_vector=baseline_vector,
            baseline_rows=baseline_rows,
            training=training_predictions,
            validation=validation_predictions,
            training_fit_rows=training_fit_rows,
        )
        model_payload = model.to_dict()
        model_payload.update(
            {
                "data_contract": metrics["data_contract"],
                "baseline": metrics["baseline"],
                "training_data": metrics["data_partition"]["training"],
                "fit_policy": (
                    "all usable lagged rows from explicitly supplied training cases; "
                    "validation cases are never read during fitting or scaling"
                ),
                "config_file_sha256": metrics["reproducibility"]["config_file_sha256"],
                "effective_config_sha256": metrics["reproducibility"]["effective_config_sha256"],
                "reproducibility": metrics["reproducibility"],
            }
        )

        out_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".b54_real_rom_", dir=out_dir.parent) as temporary:
            staged_paths = _delivery_paths(Path(temporary))
            _write_strict_json(staged_paths["model"], model_payload)
            _write_prediction_csv(
                staged_paths["training_csv"], training_predictions, model.arx.max_lag, config
            )
            _write_prediction_csv(
                staged_paths["validation_csv"], validation_predictions, model.arx.max_lag, config
            )
            _write_prediction_plot(
                staged_paths["training_plot"], training_predictions, "Training predictions"
            )
            _write_prediction_plot(
                staged_paths["validation_plot"],
                validation_predictions,
                "Independent validation predictions",
            )
            _write_result_markdown(staged_paths["result"], metrics, staged_paths)
            # COMPLETE metrics is the final publication marker. All other artifacts
            # are fully rendered in a sibling staging directory before fixed names
            # are replaced.
            _write_strict_json(staged_paths["metrics"], metrics)
            out_dir.mkdir(parents=True, exist_ok=True)
            publish_order = (
                "model",
                "training_csv",
                "validation_csv",
                "training_plot",
                "validation_plot",
                "result",
                "metrics",
            )
            for key in publish_order:
                staged_paths[key].replace(paths[key])
    except RealROMDataError as exc:
        _write_blocked_delivery(out_dir, config, exc.issues)
        raise
    except Exception as exc:
        issue = f"delivery generation failed closed: {type(exc).__name__}: {exc}"
        _write_blocked_delivery(out_dir, config, (issue,))
        raise RealROMDataError(issue) from exc

    validation_rows = sum(max(0, len(item.sequence.case.time) - model.arx.max_lag) for item in validation_predictions)
    return RealROMRunResult(
        output_dir=out_dir,
        model_path=paths["model"],
        metrics_path=paths["metrics"],
        result_path=paths["result"],
        training_plot_path=paths["training_plot"],
        validation_plot_path=paths["validation_plot"],
        training_predictions_path=paths["training_csv"],
        validation_predictions_path=paths["validation_csv"],
        training_fit_rows=training_fit_rows,
        validation_rows=validation_rows,
    )


def load_real_arx_model(path: str | Path) -> ScaledRealARXModel:
    """Load a persisted B54 model snapshot."""

    return ScaledRealARXModel.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"B54 config not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"B54 config must contain a mapping: {path}")
    return payload


def _normalise_config(
    raw: dict[str, Any],
    *,
    config_file: Path,
    output_dir: str | Path | None,
) -> dict[str, Any]:
    project_root_value = raw.get("project_root", ".")
    project_root = Path(project_root_value)
    if not project_root.is_absolute():
        # ``configs/<task>/...`` 中的相对 project_root 以仓库根目录为锚点，
        # 从任意工作目录调用都得到同一数据路径。其他位置的配置以自身目录为锚点。
        config_anchor = _config_anchor(config_file)
        project_root = (config_anchor / project_root).resolve()

    jet_values = raw.get("representative_jets", DEFAULT_REPRESENTATIVE_JETS)
    if not isinstance(jet_values, (list, tuple)) or any(
        type(item) is not int for item in jet_values
    ):
        raise RealROMDataError(
            "representative_jets must contain six YAML integers; floats, strings and booleans are invalid"
        )
    jets = tuple(jet_values)
    if len(jets) != 6 or len(set(jets)) != 6 or any(jet < 1 or jet > 24 for jet in jets):
        raise RealROMDataError("representative_jets must contain six unique ids in [1, 24]")

    data = dict(raw.get("data") or {})
    baseline_case = _resolve_project_path(project_root, data.get("baseline_case", "runs/b52_nojet_baseline"))
    training_values = data.get("training_cases", ["runs/b52_training"])
    validation_values = data.get("validation_cases", ["runs/b52_validation"])
    if isinstance(training_values, (str, Path)):
        training_values = [training_values]
    if isinstance(validation_values, (str, Path)):
        validation_values = [validation_values]
    training_cases = [_resolve_project_path(project_root, item) for item in training_values]
    validation_cases = [_resolve_project_path(project_root, item) for item in validation_values]

    preprocessing = {"sample_stride": 10, **dict(raw.get("preprocessing") or {})}
    baseline = {"tail_fraction": 0.2, **dict(raw.get("baseline") or {})}
    model = {
        "input_lags": 50,
        "output_lags": 20,
        "ridge_alpha": 1.0,
        "include_current_input": False,
        **dict(raw.get("model") or {}),
    }
    diagnostics_raw = dict(raw.get("diagnostics") or {})
    diagnostics = {
        "active_threshold_kg_s": diagnostics_raw.pop(
            "active_threshold_kg_s",
            diagnostics_raw.pop("massflow_active_threshold_kg_s", 1.0e-8),
        ),
        "pre_event_s": diagnostics_raw.pop("pre_event_s", 0.02),
        "response_horizon_s": diagnostics_raw.pop("response_horizon_s", 0.25),
        "onset_consecutive_samples": diagnostics_raw.pop(
            "onset_consecutive_samples",
            diagnostics_raw.pop("sustained_steps", 5),
        ),
        "onset_sigma_multiplier": diagnostics_raw.pop(
            "onset_sigma_multiplier",
            diagnostics_raw.pop("response_threshold_sigma", 3.0),
        ),
        "onset_peak_fraction": diagnostics_raw.pop(
            "onset_peak_fraction",
            diagnostics_raw.pop("response_threshold_peak_fraction", 0.05),
        ),
        **diagnostics_raw,
    }
    quality = {"require_b04_pass": True, **dict(raw.get("quality") or {})}

    configured_output = output_dir if output_dir is not None else raw.get("output_dir", "artifacts")
    resolved_output = _resolve_project_path(project_root, configured_output)
    config = {
        "schema_version": SCHEMA_VERSION,
        "config_path": str(config_file),
        "project_root": str(project_root),
        "representative_jets": list(jets),
        "data": {
            "baseline_case": str(baseline_case),
            "training_cases": [str(item) for item in training_cases],
            "validation_cases": [str(item) for item in validation_cases],
        },
        "preprocessing": preprocessing,
        "baseline": baseline,
        "model": model,
        "diagnostics": diagnostics,
        "quality": quality,
        "output_dir": str(resolved_output),
    }
    return config


def _resolve_project_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _resolve_output_dir_from_raw(
    raw: dict[str, Any],
    *,
    config_file: Path,
    output_dir: str | Path | None,
) -> Path:
    """Best-effort output resolution used only to invalidate stale success on bad config."""

    config_anchor = _config_anchor(config_file)
    if output_dir is not None:
        try:
            explicit = Path(output_dir)
            if explicit.is_absolute():
                return explicit.resolve()
        except (OSError, TypeError, ValueError):
            return (config_anchor / "artifacts").resolve()
    else:
        explicit = None

    try:
        project_root = Path(raw.get("project_root", "."))
        if not project_root.is_absolute():
            project_root = (config_anchor / project_root).resolve()
        configured_output = explicit if explicit is not None else raw.get("output_dir", "artifacts")
        return _resolve_project_path(project_root, configured_output)
    except (OSError, TypeError, ValueError):
        if explicit is not None:
            try:
                return (config_anchor / explicit).resolve()
            except (OSError, TypeError, ValueError):
                pass
        return (config_anchor / "artifacts").resolve()


def _config_anchor(config_file: Path) -> Path:
    """Resolve the project/config anchor without depending on the process CWD."""

    return (
        config_file.parents[2]
        if len(config_file.parents) > 2 and config_file.parents[1].name == "configs"
        else config_file.parent
    )


def _validate_numeric_config(config: dict[str, Any]) -> None:
    issues: list[str] = []
    integer_rules = (
        ("preprocessing.sample_stride", config["preprocessing"]["sample_stride"], 1),
        ("model.input_lags", config["model"]["input_lags"], 1),
        ("model.output_lags", config["model"]["output_lags"], 1),
        (
            "diagnostics.onset_consecutive_samples",
            config["diagnostics"]["onset_consecutive_samples"],
            1,
        ),
    )
    for name, value, minimum in integer_rules:
        if type(value) is not int or value < minimum:
            issues.append(f"{name} must be an integer >= {minimum}")

    finite_rules = (
        ("baseline.tail_fraction", config["baseline"]["tail_fraction"], 0.0, 1.0, False),
        ("model.ridge_alpha", config["model"]["ridge_alpha"], 0.0, None, False),
        (
            "diagnostics.active_threshold_kg_s",
            config["diagnostics"]["active_threshold_kg_s"],
            0.0,
            None,
            True,
        ),
        ("diagnostics.pre_event_s", config["diagnostics"]["pre_event_s"], 0.0, None, False),
        (
            "diagnostics.response_horizon_s",
            config["diagnostics"]["response_horizon_s"],
            0.0,
            None,
            False,
        ),
        (
            "diagnostics.onset_sigma_multiplier",
            config["diagnostics"]["onset_sigma_multiplier"],
            0.0,
            None,
            False,
        ),
        (
            "diagnostics.onset_peak_fraction",
            config["diagnostics"]["onset_peak_fraction"],
            0.0,
            1.0,
            False,
        ),
    )
    for name, value, lower, upper, include_lower in finite_rules:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            issues.append(f"{name} must be a finite number")
            continue
        numeric = float(value)
        lower_ok = numeric >= lower if include_lower else numeric > lower
        upper_ok = upper is None or numeric <= upper
        if not lower_ok or not upper_ok:
            interval = f"{'[' if include_lower else '('}{lower}, {upper if upper is not None else 'inf'}]"
            issues.append(f"{name} must be in {interval}")
    if type(config["model"]["include_current_input"]) is not bool:
        issues.append("model.include_current_input must be a boolean")
    if type(config["quality"]["require_b04_pass"]) is not bool:
        issues.append("quality.require_b04_pass must be a boolean")
    elif config["quality"]["require_b04_pass"] is not True:
        issues.append(
            "quality.require_b04_pass must be true for the production B04 real ROM delivery"
        )
    if issues:
        raise RealROMDataError(issues)


def _load_and_check_cases(config: dict[str, Any]) -> dict[str, Any]:
    jets = tuple(config["representative_jets"])
    input_columns = actual_massflow_columns(jets)
    require_quality = bool(config["quality"]["require_b04_pass"])
    specs: list[tuple[str, Path]] = [
        ("baseline", Path(config["data"]["baseline_case"])),
        *(("training", Path(path)) for path in config["data"]["training_cases"]),
        *(("validation", Path(path)) for path in config["data"]["validation_cases"]),
    ]
    loaded: dict[str, Any] = {"baseline": None, "training": [], "validation": []}
    issues: list[str] = []
    if not config["data"]["training_cases"]:
        issues.append("at least one explicit training case is required")
    if not config["data"]["validation_cases"]:
        issues.append("at least one explicit validation case is required")

    for role, case_dir in specs:
        try:
            case = _load_real_case(
                case_dir,
                input_columns=input_columns,
                output_columns=REGION_FORCE_COLUMNS,
                require_quality=require_quality,
            )
        except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            issues.append(f"{role} case {case_dir}: {exc}")
            continue
        if role == "baseline":
            loaded["baseline"] = case
        else:
            loaded[role].append(case)

    if issues:
        raise RealROMDataError(issues)
    assert loaded["baseline"] is not None
    return loaded


def _load_real_case(
    case_dir: Path,
    *,
    input_columns: Sequence[str],
    output_columns: Sequence[str],
    require_quality: bool,
) -> RealROMCase:
    if not case_dir.is_dir():
        raise FileNotFoundError("case directory does not exist")
    timeseries_path = case_dir / "processed" / "timeseries.csv"
    if not timeseries_path.is_file():
        raise FileNotFoundError("processed/timeseries.csv does not exist")

    quality_report_path = case_dir / "quality_report.json"
    quality_status, quality_case_id, quality_metadata = _read_quality_gate(
        case_dir,
        require_quality=require_quality,
    )
    manifest_path, manifest, provenance_tokens = _read_case_manifest(
        case_dir,
        require_provenance=require_quality,
    )
    reported_manifest_sha256 = str(
        quality_metadata.get("case_manifest_sha256") or ""
    ).strip().lower()
    measured_manifest_sha256 = _sha256_file(manifest_path) if manifest_path.is_file() else ""
    if require_quality and reported_manifest_sha256 != measured_manifest_sha256:
        raise ValueError(
            "B04 quality case_manifest_sha256 does not match case_manifest.yaml; "
            "the quality report is missing or stale"
        )
    manifest_case_id = str(manifest.get("case_id") or "").strip()
    if quality_case_id and manifest_case_id and quality_case_id != manifest_case_id:
        raise ValueError(
            f"case_id mismatch between quality report ({quality_case_id}) and manifest ({manifest_case_id})"
        )
    case_id = manifest_case_id or quality_case_id or case_dir.name
    compatibility_signature = _manifest_compatibility_signature(
        manifest,
        case_dir=case_dir,
        require_complete=require_quality,
    )
    required = ("physical_time", *ALL_ACTUAL_MASSFLOW_COLUMNS, *output_columns)
    with timeseries_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        missing = [column for column in required if column not in fieldnames]
        if missing:
            raise ValueError("missing required columns: " + ", ".join(missing))
        rows = list(reader)
    if not rows:
        raise ValueError("timeseries contains no rows")
    timeseries_sha256 = _sha256_file(timeseries_path)
    reported_sha256 = str(quality_metadata.get("timeseries_sha256") or "").strip().lower()
    if require_quality and reported_sha256 != timeseries_sha256:
        raise ValueError(
            "B04 quality timeseries_sha256 does not match processed/timeseries.csv; "
            "the quality report is missing or stale"
        )
    reported_rows = quality_metadata.get("row_count")
    if require_quality and (type(reported_rows) is not int or reported_rows != len(rows)):
        raise ValueError(
            f"B04 quality row_count={reported_rows!r} does not match timeseries rows={len(rows)}"
        )

    time = _strict_matrix(rows, ("physical_time",), source=timeseries_path).ravel()
    inputs = _strict_matrix(rows, input_columns, source=timeseries_path)
    all_actual_columns = ALL_ACTUAL_MASSFLOW_COLUMNS
    all_actual_massflows = _strict_matrix(rows, all_actual_columns, source=timeseries_path)
    measured_active_jets = [
        index + 1
        for index in range(all_actual_massflows.shape[1])
        if float(np.max(np.abs(all_actual_massflows[:, index]))) > 1.0e-8
    ]
    reported_active_jets = quality_metadata.get("active_jets")
    if require_quality and reported_active_jets != measured_active_jets:
        raise ValueError(
            f"B04 quality active_jets={reported_active_jets!r} does not match measured "
            f"actual massflow active_jets={measured_active_jets}"
        )
    declared_case_type = str(quality_metadata.get("declared_case_type") or "").strip().lower()
    manifest_case_type = str(manifest.get("case_type") or "").strip().lower()
    if require_quality and declared_case_type != manifest_case_type:
        raise ValueError(
            f"B04 declared_case_type={declared_case_type!r} does not match manifest "
            f"case_type={manifest_case_type!r}"
        )
    forces = _strict_matrix(rows, output_columns, source=timeseries_path)
    if len(time) < 3:
        raise ValueError("timeseries must contain at least three rows")
    diffs = np.diff(time)
    if np.any(diffs <= 0.0):
        raise ValueError("physical_time must be strictly increasing")
    time_step = float(np.median(diffs))
    tolerance = max(1.0e-10, abs(time_step) * 1.0e-4)
    if float(np.max(np.abs(diffs - time_step))) > tolerance:
        raise ValueError("physical_time is not uniformly sampled")
    return RealROMCase(
        case_id=case_id or case_dir.name,
        case_dir=case_dir.resolve(),
        timeseries_path=timeseries_path.resolve(),
        timeseries_sha256=timeseries_sha256,
        quality_status=quality_status,
        quality_report_path=quality_report_path.resolve(),
        quality_report_sha256=(
            _sha256_file(quality_report_path) if quality_report_path.is_file() else ""
        ),
        manifest_path=manifest_path.resolve(),
        manifest_sha256=_sha256_file(manifest_path) if manifest_path.is_file() else "",
        provenance_tokens=provenance_tokens,
        case_type=str(manifest.get("case_type") or "").strip().lower(),
        nojet_drift_flag_count=int(quality_metadata.get("nojet_drift_flag_count", -1)),
        nojet_jump_flag_count=int(quality_metadata.get("nojet_jump_flag_count", -1)),
        baseline_review_approved=(
            manifest.get("b54_baseline_review_approved") is True
        ),
        compatibility_signature=compatibility_signature,
        time=time,
        inputs=inputs,
        all_actual_massflows=all_actual_massflows,
        all_actual_columns=all_actual_columns,
        forces=forces,
        raw_rows=len(rows),
        raw_time_step_s=time_step,
    )


def _read_quality_gate(
    case_dir: Path,
    *,
    require_quality: bool,
) -> tuple[str, str, dict[str, Any]]:
    path = case_dir / "quality_report.json"
    if not path.is_file():
        if require_quality:
            raise FileNotFoundError("quality_report.json is required")
        return "NOT_REQUIRED", case_dir.name, {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    case_id = str(payload.get("case_id") or case_dir.name)
    nested = payload.get("B04_real_quality")
    top_level_pass = payload.get("run_success_flag") is True
    if require_quality and not isinstance(nested, dict):
        raise ValueError("quality_report.json lacks the required B04_real_quality gate")
    if isinstance(nested, dict):
        schema_pass = nested.get("schema_version") == "B04_real_data_quality_v1"
        summary = nested.get("summary") if isinstance(nested.get("summary"), dict) else {}
        flag_pass = summary.get("run_success_flag") is True
        reported_status = str(summary.get("overall_status", "")).strip().upper()
        status_pass = reported_status == "PASS"
        blocking_count = summary.get("blocking_issue_count")
        blocking_pass = type(blocking_count) is int and blocking_count == 0
        passed = top_level_pass and schema_pass and flag_pass and status_pass and blocking_pass
        status = "PASS" if passed else "FAIL"
        nested_case_id = str(nested.get("case_id") or "").strip()
        if require_quality and (not nested_case_id or nested_case_id != case_id):
            raise ValueError(
                f"B04 quality case_id={nested_case_id!r} does not match report case_id={case_id!r}"
            )
        metrics = nested.get("metrics") if isinstance(nested.get("metrics"), dict) else {}
        nojet_physics = (
            metrics.get("no_jet_physics")
            if isinstance(metrics.get("no_jet_physics"), dict)
            else {}
        )
        drift_flags = nojet_physics.get("drift_flags")
        jump_flags = nojet_physics.get("jump_flags")
        metadata = {
            "row_count": metrics.get("row_count"),
            "active_jets": metrics.get("active_jets"),
            "declared_case_type": metrics.get("declared_case_type"),
            "timeseries_sha256": metrics.get("timeseries_sha256"),
            "case_manifest_sha256": metrics.get("case_manifest_sha256"),
            "nojet_drift_flag_count": len(drift_flags) if isinstance(drift_flags, list) else -1,
            "nojet_jump_flag_count": len(jump_flags) if isinstance(jump_flags, list) else -1,
        }
    else:
        status = "PASS" if payload.get("run_success_flag") is True else "FAIL"
        metadata = {}
    if require_quality and status != "PASS":
        raise ValueError("B04 real-data quality gate is not PASS")
    return status, case_id, metadata


def _read_case_manifest(
    case_dir: Path,
    *,
    require_provenance: bool,
) -> tuple[Path, dict[str, Any], tuple[str, ...]]:
    path = case_dir / "case_manifest.yaml"
    if not path.is_file():
        if require_provenance:
            raise FileNotFoundError("case_manifest.yaml is required for case-level provenance")
        return path, {}, (f"case_dir:{case_dir.resolve()}",)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("case_manifest.yaml must contain a mapping")
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    tokens: list[str] = []

    source_evidence_value = payload.get("source_run_evidence")
    if require_provenance and not isinstance(source_evidence_value, dict):
        raise ValueError(
            "case_manifest.yaml must contain source_run_evidence bound to a complete upstream run"
        )
    if isinstance(source_evidence_value, dict):
        evidence = source_evidence_value
        run_uuid_text = str(evidence.get("run_uuid") or "").strip().lower()
        try:
            parsed_uuid = uuid.UUID(run_uuid_text)
        except (ValueError, AttributeError) as exc:
            raise ValueError("source_run_evidence.run_uuid must be a canonical UUID") from exc
        if str(parsed_uuid) != run_uuid_text or parsed_uuid.int == 0:
            raise ValueError("source_run_evidence.run_uuid must be a canonical non-zero UUID")
        if str(evidence.get("sequence_scope") or "").strip() != "full_run":
            raise ValueError("source_run_evidence.sequence_scope must be full_run")
        artifact_kind = str(evidence.get("artifact_kind") or "").strip()
        if artifact_kind not in {"solver_result_sim", "raw_star_bundle"}:
            raise ValueError(
                "source_run_evidence.artifact_kind must be solver_result_sim or raw_star_bundle"
            )
        artifact_text = str(evidence.get("source_artifact_path") or "").strip()
        if not artifact_text:
            raise ValueError("source_run_evidence.source_artifact_path is required")
        source_artifact = Path(artifact_text).expanduser()
        if not source_artifact.is_absolute():
            source_artifact = case_dir / source_artifact
        source_artifact = source_artifact.resolve()
        processed_root = (case_dir / "processed").resolve()
        if source_artifact == processed_root or processed_root in source_artifact.parents:
            raise ValueError(
                "source_run_evidence must reference an upstream solver artifact, not processed output"
            )
        if artifact_kind == "solver_result_sim" and (
            not source_artifact.is_file() or source_artifact.suffix.lower() != ".sim"
        ):
            raise ValueError("solver_result_sim evidence must reference an existing .sim file")
        if artifact_kind == "raw_star_bundle" and not source_artifact.is_dir():
            raise ValueError("raw_star_bundle evidence must reference an existing directory")
        declared_artifact_hash = str(evidence.get("source_artifact_sha256") or "").strip().lower()
        if len(declared_artifact_hash) != 64 or any(
            character not in "0123456789abcdef" for character in declared_artifact_hash
        ):
            raise ValueError(
                "source_run_evidence.source_artifact_sha256 must be a 64-hex digest"
            )
        measured_artifact_hash = sha256_source_artifact(source_artifact)
        if measured_artifact_hash != declared_artifact_hash:
            raise ValueError(
                "source_run_evidence.source_artifact_sha256 does not match the upstream artifact"
            )
        tokens.extend(
            (
                f"source_run_uuid:{run_uuid_text}",
                f"source_artifact_path:{source_artifact}",
                f"source_artifact_sha256:{measured_artifact_hash}",
            )
        )

    # Legacy identifiers remain useful supplementary evidence, but are not
    # sufficient by themselves when the real-data quality gate is enabled.
    for value in (
        payload.get("run_id"),
        payload.get("source_run_id"),
        runtime.get("run_id"),
    ):
        text = str(value or "").strip()
        if text:
            tokens.append(f"run_identity:{text}")
    for value in (
        payload.get("raw_star_dir"),
        payload.get("source_ccm_timeseries"),
        runtime.get("result_sim_path"),
    ):
        text = str(value or "").strip()
        if text:
            candidate = Path(text).expanduser()
            if not candidate.is_absolute():
                candidate = case_dir / candidate
            tokens.append(f"source_path:{candidate.resolve()}")
    unique_tokens = tuple(dict.fromkeys(tokens))
    if require_provenance and not unique_tokens:
        raise ValueError(
            "case_manifest.yaml lacks verifiable complete-run source provenance"
        )
    return path, payload, unique_tokens or (f"case_dir:{case_dir.resolve()}",)


def _manifest_compatibility_signature(
    manifest: dict[str, Any],
    *,
    case_dir: Path,
    require_complete: bool,
) -> dict[str, Any]:
    """Resolve physical settings that must match across independent runs."""

    explicit_value = manifest.get("rom_compatibility")
    if require_complete and not isinstance(explicit_value, dict):
        raise ValueError(
            "case_manifest.yaml must contain an explicit rom_compatibility mapping"
        )
    explicit = explicit_value if isinstance(explicit_value, dict) else {}
    star = manifest.get("star") if isinstance(manifest.get("star"), dict) else {}
    units = manifest.get("units") if isinstance(manifest.get("units"), dict) else {}
    report_semantics = (
        manifest.get("report_semantics")
        if isinstance(manifest.get("report_semantics"), dict)
        else {}
    )
    regional_semantics = {
        key: value
        for key, value in report_semantics.items()
        if key in REGION_FORCE_COLUMNS or key == "Fz_S1L..Fz_S3R"
    }
    flow_condition = {
        "flow_velocity": manifest.get("flow_velocity"),
        "gap": manifest.get("gap"),
    }
    flow_condition_complete = all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        for value in flow_condition.values()
    )
    if require_complete:
        # The generic manifest fields used by older ingestion stages can be
        # placeholders.  A real ROM therefore requires the producer to assert
        # this dedicated, frozen signature explicitly rather than inferring it.
        signature = {
            "geometry_id": explicit.get("geometry_id"),
            "mesh_id": explicit.get("mesh_id"),
            "template_sim_sha256": explicit.get("template_sim_sha256"),
            "flow_condition_id": explicit.get("flow_condition_id"),
            "force_definition_id": explicit.get("force_definition_id"),
            "force_unit": explicit.get("force_unit"),
            "massflow_unit": explicit.get("massflow_unit"),
            "sign_convention_id": explicit.get("sign_convention_id"),
        }
        template_path_text = str(explicit.get("template_sim_path") or "").strip()
        if not template_path_text:
            raise ValueError("rom_compatibility.template_sim_path is required")
        template_path = Path(template_path_text).expanduser()
        if not template_path.is_absolute():
            template_path = case_dir / template_path
        template_path = template_path.resolve()
        if not template_path.is_file() or template_path.suffix.lower() != ".sim":
            raise ValueError(
                "rom_compatibility.template_sim_path must reference an existing .sim file"
            )
    else:
        signature = {
            "geometry_id": explicit.get("geometry_id")
            or manifest.get("geometry_version")
            or star.get("geometry_version"),
            "mesh_id": explicit.get("mesh_id")
            or manifest.get("mesh_version")
            or star.get("mesh_version"),
            "template_sim_sha256": explicit.get("template_sim_sha256")
            or star.get("sim_file_hash_sha256"),
            "flow_condition_id": explicit.get("flow_condition_id")
            or (_canonical_json(flow_condition) if flow_condition_complete else ""),
            "force_definition_id": explicit.get("force_definition_id")
            or (_canonical_json(regional_semantics) if regional_semantics else ""),
            "force_unit": explicit.get("force_unit") or units.get("force"),
            "massflow_unit": explicit.get("massflow_unit") or units.get("massflow"),
            "sign_convention_id": explicit.get("sign_convention_id")
            or manifest.get("sign_convention"),
        }
    signature = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in signature.items()
    }
    if isinstance(signature["template_sim_sha256"], str):
        signature["template_sim_sha256"] = signature["template_sim_sha256"].lower()
    if require_complete:
        missing = [key for key, value in signature.items() if _is_missing_provenance_value(value)]
        non_string = [key for key, value in signature.items() if not isinstance(value, str)]
        missing.extend(f"{key}(non-empty string)" for key in non_string)
        sim_hash = str(signature["template_sim_sha256"] or "")
        if len(sim_hash) != 64 or any(character not in "0123456789abcdefABCDEF" for character in sim_hash):
            missing.append("template_sim_sha256(valid 64-hex)")
        if missing:
            raise ValueError(
                "case_manifest.yaml lacks frozen ROM compatibility metadata: "
                + ", ".join(dict.fromkeys(missing))
            )
        if _sha256_file(template_path) != sim_hash:
            raise ValueError(
                "rom_compatibility.template_sim_sha256 does not match template_sim_path"
            )
        if signature["force_unit"] != "N" or signature["massflow_unit"] != "kg/s":
            raise ValueError("ROM requires force unit N and massflow unit kg/s")
    return signature


def _is_missing_provenance_value(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return not text or text in {"unknown", "none", "null", "待确认", "待浩坤确认"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _check_physical_compatibility(cases: Sequence[RealROMCase]) -> None:
    if not cases:
        raise RealROMDataError("no cases available for physical compatibility check")
    reference = cases[0].compatibility_signature
    mismatches = [
        case.case_id for case in cases[1:] if case.compatibility_signature != reference
    ]
    if mismatches:
        raise RealROMDataError(
            "baseline/training/validation physical compatibility signatures differ for cases: "
            + ", ".join(mismatches)
        )


def _strict_matrix(
    rows: Sequence[dict[str, str]],
    columns: Sequence[str],
    *,
    source: Path,
) -> np.ndarray:
    data = np.empty((len(rows), len(columns)), dtype=float)
    for row_index, row in enumerate(rows):
        for column_index, column in enumerate(columns):
            raw = row.get(column)
            if raw is None or str(raw).strip() == "":
                raise ValueError(f"blank value at row {row_index + 2}, column {column}; no zero fill allowed")
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(
                    f"non-numeric value at row {row_index + 2}, column {column}: {raw!r}"
                ) from exc
            if not math.isfinite(value):
                raise ValueError(f"non-finite value at row {row_index + 2}, column {column}")
            if abs(value) > NUMERIC_ABS_LIMIT:
                raise ValueError(
                    f"value exceeds the numeric safety limit at row {row_index + 2}, "
                    f"column {column}"
                )
            data[row_index, column_index] = value
    if data.size == 0:
        raise ValueError(f"no numeric data in {source}")
    return data


def _check_partition_independence(
    baseline: RealROMCase,
    training: Sequence[RealROMCase],
    validation: Sequence[RealROMCase],
) -> None:
    issues: list[str] = []
    groups: dict[str, tuple[RealROMCase, ...]] = {
        "baseline": (baseline,),
        "training": tuple(training),
        "validation": tuple(validation),
    }

    for role, cases in groups.items():
        for label, values in (
            ("case paths", [str(item.case_dir) for item in cases]),
            ("case_id values", [item.case_id for item in cases]),
            ("timeseries SHA256 values", [item.timeseries_sha256 for item in cases]),
        ):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                issues.append(f"{role} contains duplicate {label}: " + ", ".join(duplicates))
        provenance_values = [token for item in cases for token in item.provenance_tokens]
        duplicate_provenance = sorted(
            {token for token in provenance_values if provenance_values.count(token) > 1}
        )
        if duplicate_provenance:
            issues.append(
                f"{role} cases share source run provenance: "
                + ", ".join(duplicate_provenance)
            )

    for left_role, right_role in (
        ("baseline", "training"),
        ("baseline", "validation"),
        ("training", "validation"),
    ):
        left = groups[left_role]
        right = groups[right_role]
        overlap_dirs = sorted(
            str(value)
            for value in {item.case_dir for item in left} & {item.case_dir for item in right}
        )
        if overlap_dirs:
            issues.append(
                f"{left_role} and {right_role} case paths overlap: " + ", ".join(overlap_dirs)
            )
        overlap_ids = sorted(
            {item.case_id for item in left} & {item.case_id for item in right}
        )
        if overlap_ids:
            issues.append(
                f"{left_role} and {right_role} case_id values overlap: "
                + ", ".join(overlap_ids)
            )
        overlap_hashes = sorted(
            {item.timeseries_sha256 for item in left}
            & {item.timeseries_sha256 for item in right}
        )
        if overlap_hashes:
            issues.append(
                f"{left_role} and {right_role} contain identical timeseries SHA256 content"
            )
        overlapping_provenance = sorted(
            {token for item in left for token in item.provenance_tokens}
            & {token for item in right for token in item.provenance_tokens}
        )
        if overlapping_provenance:
            issues.append(
                f"{left_role} and {right_role} resolve to the same source run provenance: "
                + ", ".join(overlapping_provenance)
            )
    for role, cases in (("training", training), ("validation", validation)):
        for case in cases:
            start_tolerance = max(1.0e-10, 2.0 * case.raw_time_step_s)
            if abs(float(case.time[0])) > start_tolerance:
                issues.append(
                    f"{role} case {case.case_id} starts at physical_time={case.time[0]:.12g}s, "
                    "not at an independent run origin; a held-out slice is not allowed"
                )
    if issues:
        raise RealROMDataError(issues)


def _check_excitation_coverage(
    training: Sequence[RealROMCase],
    validation: Sequence[RealROMCase],
    config: dict[str, Any],
) -> None:
    threshold = float(config["diagnostics"]["active_threshold_kg_s"])
    input_columns = actual_massflow_columns(config["representative_jets"])
    issues: list[str] = []
    signatures: dict[str, tuple[tuple[int, float], ...]] = {}
    for role, cases in (("training", training), ("validation", validation)):
        all_inputs = np.vstack([item.inputs for item in cases])
        inactive = [
            input_columns[idx]
            for idx in range(all_inputs.shape[1])
            if float(np.max(np.abs(all_inputs[:, idx]))) <= threshold
        ]
        if inactive:
            issues.append(f"{role} does not excite every representative jet: {', '.join(inactive)}")
        rank = int(np.linalg.matrix_rank(all_inputs - np.mean(all_inputs, axis=0), tol=max(threshold, 1.0e-12)))
        if rank < len(input_columns):
            issues.append(f"{role} actual-massflow excitation rank is {rank}, expected {len(input_columns)}")
        signatures[role] = tuple(
            event
            for case in cases
            for event in _input_event_signature(case.inputs, threshold=threshold)
        )
        if not signatures[role]:
            issues.append(f"{role} contains no actual-massflow rising edges")
    if issues:
        raise RealROMDataError(issues)


def _check_nonrepresentative_jet_inactivity(
    training: Sequence[RealROMCase],
    validation: Sequence[RealROMCase],
    config: dict[str, Any],
) -> None:
    """Audit every raw row before stride subsampling can hide a short unmodelled pulse."""

    threshold = float(config["diagnostics"]["active_threshold_kg_s"])
    representative_set = set(actual_massflow_columns(config["representative_jets"]))
    excluded_columns = tuple(
        column for column in ALL_ACTUAL_MASSFLOW_COLUMNS if column not in representative_set
    )
    issues: list[str] = []
    for role, cases in (("training", training), ("validation", validation)):
        for case in cases:
            excluded_indices = [case.all_actual_columns.index(column) for column in excluded_columns]
            excluded_max = float(np.max(np.abs(case.all_actual_massflows[:, excluded_indices])))
            if excluded_max > threshold:
                issues.append(
                    f"{role} case {case.case_id} actuates a non-representative jet in raw data "
                    f"(max actual massflow={excluded_max:.6g} kg/s); a six-input model would be confounded"
                )
    if issues:
        raise RealROMDataError(issues)


def _input_event_signature(inputs: np.ndarray, *, threshold: float) -> tuple[tuple[int, float], ...]:
    events: list[tuple[int, float]] = []
    for row in range(len(inputs)):
        previous = inputs[row - 1] if row else np.zeros(inputs.shape[1], dtype=float)
        for column in range(inputs.shape[1]):
            if abs(inputs[row, column]) > threshold and abs(previous[column]) <= threshold:
                events.append((column, round(float(inputs[row, column]), 9)))
    return tuple(events)


def _estimate_baseline(case: RealROMCase, config: dict[str, Any]) -> tuple[np.ndarray, int]:
    threshold = float(config["diagnostics"]["active_threshold_kg_s"])
    if case.case_type not in {"no_jet", "nojet", "baseline"}:
        raise RealROMDataError(
            f"baseline case {case.case_id} manifest case_type must be no_jet, got {case.case_type!r}"
        )
    maximum = float(np.max(np.abs(case.all_actual_massflows)))
    if maximum > threshold:
        raise RealROMDataError(
            f"baseline case {case.case_id} is not no-jet: max available actual massflow="
            f"{maximum:.6g} kg/s across {len(case.all_actual_columns)} monitored jets"
        )
    if case.nojet_drift_flag_count < 0 or case.nojet_jump_flag_count < 0:
        raise RealROMDataError(
            f"baseline case {case.case_id} B04 report lacks no_jet_physics drift/jump evidence"
        )
    if (
        case.nojet_drift_flag_count > 0 or case.nojet_jump_flag_count > 0
    ) and not case.baseline_review_approved:
        raise RealROMDataError(
            f"baseline case {case.case_id} has unresolved B04 no-jet drift/jump findings; "
            "set b54_baseline_review_approved: true only after engineering review and rerun B04"
        )
    tail_fraction = float(config["baseline"]["tail_fraction"])
    start = int(math.floor(len(case.forces) * (1.0 - tail_fraction)))
    selected = case.forces[start:]
    if len(selected) < 2:
        raise RealROMDataError("baseline selection contains fewer than two rows")
    vector = np.mean(selected, axis=0)
    if not np.all(np.isfinite(vector)):
        raise RealROMDataError("baseline estimate contains non-finite values")
    return vector, len(selected)


def _check_effective_time_steps(
    training: Sequence[RealROMSequence],
    validation: Sequence[RealROMSequence],
) -> None:
    sequences = [*training, *validation]
    if not sequences:
        raise RealROMDataError("no model sequences were loaded")
    reference = sequences[0].case.effective_time_step_s
    tolerance = max(1.0e-10, abs(reference) * 1.0e-4)
    mismatches = [
        f"{item.case.case_id}={item.case.effective_time_step_s:.12g}s"
        for item in sequences
        if abs(item.case.effective_time_step_s - reference) > tolerance
    ]
    if mismatches:
        raise RealROMDataError(
            [f"training and validation effective time steps differ from {reference:.12g}s", *mismatches]
        )


def _check_diagnostic_coverage(
    training: Sequence[RealROMSequence],
    validation: Sequence[RealROMSequence],
    max_lag: int,
    config: dict[str, Any],
) -> None:
    """Require post-warm-up, full-horizon events for every jet in both splits."""

    settings = config["diagnostics"]
    threshold = float(settings["active_threshold_kg_s"])
    input_columns = actual_massflow_columns(config["representative_jets"])
    issues: list[str] = []
    for role, sequences in (("training", training), ("validation", validation)):
        eligible_by_input = {column: 0 for column in input_columns}
        detected_by_input = {column: 0 for column in input_columns}
        for sequence in sequences:
            case = sequence.case
            for _, start, input_index in _eligible_diagnostic_events(
                case,
                max_lag=max_lag,
                settings=settings,
                threshold=threshold,
            ):
                column = input_columns[input_index]
                eligible_by_input[column] += 1
                if _truth_response_detected(
                    sequence,
                    start=start,
                    settings=settings,
                ):
                    detected_by_input[column] += 1
        missing = [column for column, count in eligible_by_input.items() if count == 0]
        if missing:
            issues.append(
                f"{role} has no isolated post-warm-up event with a complete response horizon for: "
                + ", ".join(missing)
            )
        undetected = [column for column, count in detected_by_input.items() if count == 0]
        if undetected:
            issues.append(
                f"{role} has no isolated event with a detectable truth response for: "
                + ", ".join(undetected)
            )
    if issues:
        raise RealROMDataError(issues)


def _eligible_diagnostic_events(
    case: RealROMCase,
    *,
    max_lag: int,
    settings: dict[str, Any],
    threshold: float,
) -> list[tuple[int, int, int]]:
    """Return events whose attribution is not confounded by another jet."""

    dt = case.effective_time_step_s
    pre_rows = max(1, int(round(float(settings["pre_event_s"]) / dt)))
    horizon_rows = max(1, int(round(float(settings["response_horizon_s"]) / dt)))
    eligible: list[tuple[int, int, int]] = []
    for event_index, (start, input_index) in enumerate(
        _rising_edge_events(case.inputs, threshold=threshold)
    ):
        if start < max_lag + pre_rows or start + horizon_rows >= len(case.time):
            continue
        if np.any(np.abs(case.inputs[start - pre_rows : start]) > threshold):
            continue
        response_inputs = case.inputs[start : start + horizon_rows + 1]
        other_columns = [index for index in range(case.inputs.shape[1]) if index != input_index]
        if other_columns and np.any(np.abs(response_inputs[:, other_columns]) > threshold):
            continue
        target_active = np.abs(response_inputs[:, input_index]) > threshold
        target_rising_count = int(
            np.count_nonzero(target_active & ~np.r_[False, target_active[:-1]])
        )
        if target_rising_count != 1:
            continue
        eligible.append((event_index, start, input_index))
    return eligible


def _truth_response_detected(
    sequence: RealROMSequence,
    *,
    start: int,
    settings: dict[str, Any],
) -> bool:
    case = sequence.case
    dt = case.effective_time_step_s
    pre_rows = max(1, int(round(float(settings["pre_event_s"]) / dt)))
    horizon_rows = max(1, int(round(float(settings["response_horizon_s"]) / dt)))
    truth_pre = sequence.delta_forces[start - pre_rows : start]
    truth_window = sequence.delta_forces[start : start + horizon_rows + 1] - np.mean(
        truth_pre,
        axis=0,
    )
    return any(
        _onset_delay(
            truth_window[:, region],
            truth_pre[:, region],
            dt=dt,
            settings=settings,
        )
        is not None
        for region in range(truth_window.shape[1])
    )


def _fit_scaled_model(
    sequences: Sequence[RealROMSequence],
    config: dict[str, Any],
) -> tuple[ScaledRealARXModel, int]:
    if not sequences:
        raise RealROMDataError("training set contains no sequences")
    all_inputs = np.vstack([item.case.inputs for item in sequences])
    all_outputs = np.vstack([item.delta_forces for item in sequences])
    input_mean, input_scale = _stable_column_mean_std(all_inputs, "training inputs")
    output_mean, output_scale = _stable_column_mean_std(all_outputs, "training outputs")
    if np.any(input_scale <= 1.0e-12):
        bad = np.flatnonzero(input_scale <= 1.0e-12).tolist()
        raise RealROMDataError(f"training input scale is zero for columns {bad}")
    if np.any(output_scale <= 1.0e-12):
        bad = np.flatnonzero(output_scale <= 1.0e-12).tolist()
        raise RealROMDataError(f"training output scale is zero for regions {bad}")

    settings = config["model"]
    arx = ARXModel(
        input_lags=int(settings["input_lags"]),
        output_lags=int(settings["output_lags"]),
        include_current_input=bool(settings["include_current_input"]),
        ridge_alpha=float(settings["ridge_alpha"]),
    )
    arx.input_names_ = list(actual_massflow_columns(config["representative_jets"]))
    arx.output_names_ = [f"delta_{column}" for column in REGION_FORCE_COLUMNS]
    x_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    for sequence in sequences:
        if len(sequence.case.inputs) <= arx.max_lag:
            raise RealROMDataError(
                f"training case {sequence.case.case_id} has {len(sequence.case.inputs)} model rows, "
                f"not more than max_lag={arx.max_lag}"
            )
        scaled_inputs = sequence.case.inputs / input_scale - input_mean / input_scale
        scaled_outputs = sequence.delta_forces / output_scale - output_mean / output_scale
        if not np.all(np.isfinite(scaled_inputs)) or not np.all(np.isfinite(scaled_outputs)):
            raise RealROMDataError(
                f"training case {sequence.case.case_id} scaling produced non-finite values"
            )
        x_case, y_case = arx._design_matrix(scaled_inputs, scaled_outputs, arx.max_lag, len(scaled_inputs))
        x_parts.append(x_case)
        y_parts.append(y_case)
    x_train = np.vstack(x_parts)
    y_train = np.vstack(y_parts)
    if not np.all(np.isfinite(x_train)) or not np.all(np.isfinite(y_train)):
        raise RealROMDataError("ARX training design matrix contains non-finite values")
    alpha = float(settings["ridge_alpha"])
    if alpha > 0.0:
        penalty = math.sqrt(alpha) * np.eye(x_train.shape[1], dtype=float)
        penalty[0, 0] = 0.0
        x_solve = np.vstack((x_train, penalty))
        y_solve = np.vstack((y_train, np.zeros((x_train.shape[1], y_train.shape[1]))))
    else:
        x_solve = x_train
        y_solve = y_train
    try:
        arx.coefficients_ = np.linalg.lstsq(x_solve, y_solve, rcond=None)[0]
    except np.linalg.LinAlgError as exc:
        raise RealROMDataError("ARX ridge least-squares solver failed") from exc
    if not np.all(np.isfinite(arx.coefficients_)):
        raise RealROMDataError("ARX ridge solution contains non-finite coefficients")
    return (
        ScaledRealARXModel(
            arx=arx,
            input_mean=input_mean,
            input_scale=input_scale,
            output_mean=output_mean,
            output_scale=output_scale,
        ),
        len(y_train),
    )


def _stable_column_mean_std(
    values: np.ndarray,
    label: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute column statistics without overflow from summing large finite values."""

    array = np.asarray(values, dtype=float)
    if array.ndim != 2 or not np.all(np.isfinite(array)):
        raise RealROMDataError(f"{label} contain non-finite values")
    magnitude = np.max(np.abs(array), axis=0)
    divisor = np.where(magnitude > 0.0, magnitude, 1.0)
    normalized = array / divisor
    normalized_mean = np.mean(normalized, axis=0)
    centered = normalized - normalized_mean
    normalized_scale = np.sqrt(np.mean(centered * centered, axis=0))
    mean = normalized_mean * divisor
    scale = normalized_scale * divisor
    if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(scale)):
        raise RealROMDataError(f"{label} mean/scale is non-finite")
    return mean, scale


def _predict_sequences(
    model: ScaledRealARXModel,
    sequences: Sequence[RealROMSequence],
) -> list[PredictionBundle]:
    predictions: list[PredictionBundle] = []
    for sequence in sequences:
        if len(sequence.case.inputs) <= model.arx.max_lag:
            raise RealROMDataError(
                f"case {sequence.case.case_id} is too short for max_lag={model.arx.max_lag}"
            )
        with np.errstate(over="ignore", invalid="ignore"):
            one_step = model.predict_one_step(sequence.case.inputs, sequence.delta_forces)
            rolling = model.predict_rolling(sequence.case.inputs, sequence.delta_forces)
        evaluation_slice = slice(model.arx.max_lag, None)
        if not np.all(np.isfinite(one_step[evaluation_slice])):
            raise RealROMDataError(
                f"one-step prediction is non-finite for case {sequence.case.case_id}"
            )
        if not np.all(np.isfinite(rolling[evaluation_slice])):
            raise RealROMDataError(
                f"continuous rolling prediction is unstable/non-finite for case {sequence.case.case_id}"
            )
        predictions.append(PredictionBundle(sequence=sequence, one_step=one_step, rolling=rolling))
    return predictions


def _build_metrics_payload(
    *,
    config: dict[str, Any],
    model: ScaledRealARXModel,
    baseline_case: RealROMCase,
    baseline_vector: np.ndarray,
    baseline_rows: int,
    training: Sequence[PredictionBundle],
    validation: Sequence[PredictionBundle],
    training_fit_rows: int,
) -> dict[str, Any]:
    input_columns = actual_massflow_columns(config["representative_jets"])
    output_columns = REGION_FORCE_COLUMNS
    training_metrics = _metrics_for_bundles(training, model.arx.max_lag)
    validation_metrics = _metrics_for_bundles(validation, model.arx.max_lag)
    diagnostics = {
        "training": _response_diagnostics(training, model.arx.max_lag, config),
        "validation": _response_diagnostics(validation, model.arx.max_lag, config),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "COMPLETE",
        "run_status": "COMPLETE",
        "acceptance_status": "REVIEW_REQUIRED",
        "model_type": "ARX with ridge regression",
        "data_contract": {
            "input_columns": list(input_columns),
            "input_count": len(input_columns),
            "input_source_policy": "actual_massflow only; command massflow and JET switches are never used",
            "source_audit_columns": list(ALL_ACTUAL_MASSFLOW_COLUMNS),
            "nonrepresentative_jet_policy": (
                "the other 18 actual_massflow columns are required for audit and must remain inactive; "
                "they are never model features"
            ),
            "output_columns": list(output_columns),
            "output_count": len(output_columns),
            "output_definition": (
                "each named Fz_region is represented as Fz_region(t) minus the frozen mean "
                "of the no-jet baseline tail"
            ),
            "force_unit": "N",
            "massflow_unit": "kg/s",
        },
        "baseline": {
            **_case_metadata(baseline_case),
            "estimator": "arithmetic mean",
            "tail_fraction": float(config["baseline"]["tail_fraction"]),
            "selected_rows": baseline_rows,
            "force_columns": list(REGION_FORCE_COLUMNS),
            "force_vector_N": baseline_vector.tolist(),
            "fit_source": "explicit no-jet case only; validation data not used",
            "B04_nojet_stability_review": {
                "drift_flag_count": baseline_case.nojet_drift_flag_count,
                "jump_flag_count": baseline_case.nojet_jump_flag_count,
                "explicit_review_approved": baseline_case.baseline_review_approved,
                "policy": (
                    "unresolved drift/jump findings block the ROM; explicit approval must be "
                    "bound into the manifest and followed by a fresh B04 report"
                ),
            },
            "tail_stability_diagnostics": _baseline_tail_diagnostics(
                baseline_case,
                baseline_rows,
            ),
        },
        "data_partition": {
            "policy": (
                "three-way case-level separation among baseline, training and validation; no row-level "
                "random split; paths, case ids, timeseries hashes and source provenance must be disjoint"
            ),
            "training": [_case_metadata(item.sequence.case) for item in training],
            "validation": [_case_metadata(item.sequence.case) for item in validation],
            "independence_checks": {
                "baseline_training_validation_case_paths_disjoint": True,
                "baseline_training_validation_case_ids_disjoint": True,
                "baseline_training_validation_timeseries_sha256_disjoint": True,
                "baseline_training_validation_source_run_provenance_disjoint": True,
                "case_paths_disjoint": True,
                "case_ids_disjoint": True,
                "timeseries_sha256_disjoint": True,
                "source_run_provenance_disjoint": True,
                "validation_used_for_fit_or_scaling": False,
            },
        },
        "preprocessing": {
            "sample_stride": int(config["preprocessing"]["sample_stride"]),
            "method": "deterministic stride subsampling without shuffling",
            "effective_time_step_s": training[0].sequence.case.effective_time_step_s,
            "scaler_fit_source": "training cases only",
        },
        "model": {
            "input_lags": model.arx.input_lags,
            "output_lags": model.arx.output_lags,
            "include_current_input": model.arx.include_current_input,
            "ridge_alpha": model.arx.ridge_alpha,
            "max_lag_rows": model.arx.max_lag,
            "max_lag_s": model.arx.max_lag * training[0].sequence.case.effective_time_step_s,
            "feature_count": int(model.arx.coefficients_.shape[0]),
            "training_fit_rows": int(training_fit_rows),
            "hyperparameter_policy": "frozen in config before independent validation; no validation tuning",
        },
        "prediction_definitions": {
            "one_step": "uses measured output history up to t-1 for every predicted row",
            "rolling": (
                "uses measured outputs only for the first max_lag warm-up rows, then continuously feeds back "
                "model predictions without reset or future measurements"
            ),
        },
        "metrics": {
            "normalization": "NRMSE = RMSE / (max(truth) - min(truth)) within each complete split",
            "training": training_metrics,
            "validation": validation_metrics,
        },
        "response_diagnostics": diagnostics,
        "reproducibility": _reproducibility_payload(config, include_runtime=True),
    }


def _baseline_tail_diagnostics(case: RealROMCase, selected_rows: int) -> dict[str, Any]:
    selected = case.forces[-selected_rows:]
    endpoint_rows = max(1, selected_rows // 10)
    start_mean = np.mean(selected[:endpoint_rows], axis=0)
    end_mean = np.mean(selected[-endpoint_rows:], axis=0)
    payload: dict[str, Any] = {}
    for index, region in enumerate(REGION_FORCE_COLUMNS):
        values = selected[:, index]
        peak_to_peak = float(np.max(values) - np.min(values))
        drift = float(end_mean[index] - start_mean[index])
        payload[region] = {
            "std_N": float(np.std(values)),
            "peak_to_peak_N": peak_to_peak,
            "endpoint_drift_N": drift,
            "endpoint_window_rows": endpoint_rows,
            "relative_abs_drift_to_peak_to_peak": (
                abs(drift) / peak_to_peak if peak_to_peak > 1.0e-12 else None
            ),
        }
    return payload


def _metrics_for_bundles(
    bundles: Sequence[PredictionBundle],
    max_lag: int,
) -> dict[str, Any]:
    truth = np.vstack([item.sequence.delta_forces[max_lag:] for item in bundles])
    one_step = np.vstack([item.one_step[max_lag:] for item in bundles])
    rolling = np.vstack([item.rolling[max_lag:] for item in bundles])
    one_step_metrics, one_step_macro = _compute_region_metrics(truth, one_step)
    rolling_metrics, rolling_macro = _compute_region_metrics(truth, rolling)
    return {
        "evaluation_rows": int(len(truth)),
        "one_step": one_step_metrics,
        "rolling": rolling_metrics,
        "macro_average": {
            "one_step": one_step_macro,
            "rolling": rolling_macro,
        },
    }


def _compute_region_metrics(
    truth: np.ndarray,
    prediction: np.ndarray,
) -> tuple[dict[str, Any], dict[str, float | None]]:
    result: dict[str, Any] = {}
    for column_index, source_column in enumerate(REGION_FORCE_COLUMNS):
        name = source_column
        y = truth[:, column_index]
        y_hat = prediction[:, column_index]
        error = y_hat - y
        if not np.all(np.isfinite(y)) or not np.all(np.isfinite(y_hat)) or not np.all(np.isfinite(error)):
            raise RealROMDataError(f"non-finite metric inputs for region {name}")
        error_magnitude = float(np.max(np.abs(error)))
        rmse = (
            error_magnitude
            * float(np.sqrt(np.mean(np.square(error / error_magnitude))))
            if error_magnitude > 0.0
            else 0.0
        )
        value_range = float(np.max(y) - np.min(y))
        if not math.isfinite(rmse) or not math.isfinite(value_range):
            raise RealROMDataError(f"metric magnitude overflow for region {name}")
        nrmse = rmse / value_range if value_range > 1.0e-12 else None
        correlation = _stable_correlation(y, y_hat)
        result[name] = {
            "rmse": rmse,
            "nrmse": nrmse,
            "correlation": correlation,
            "undefined_reason": (
                "truth range or truth/prediction variance is zero"
                if nrmse is None or correlation is None
                else None
            ),
        }
    valid_nrmse = [item["nrmse"] for item in result.values() if item["nrmse"] is not None]
    valid_corr = [item["correlation"] for item in result.values() if item["correlation"] is not None]
    macro_average = {
        "rmse": float(np.mean([item["rmse"] for item in result.values()])),
        "nrmse": float(np.mean(valid_nrmse)) if valid_nrmse else None,
        "correlation": float(np.mean(valid_corr)) if valid_corr else None,
    }
    return result, macro_average


def _stable_correlation(left: np.ndarray, right: np.ndarray) -> float | None:
    left_magnitude = float(np.max(np.abs(left)))
    right_magnitude = float(np.max(np.abs(right)))
    if left_magnitude == 0.0 or right_magnitude == 0.0:
        return None
    left_scaled = left / left_magnitude
    right_scaled = right / right_magnitude
    left_centered = left_scaled - np.mean(left_scaled)
    right_centered = right_scaled - np.mean(right_scaled)
    left_energy = float(np.sum(left_centered * left_centered))
    right_energy = float(np.sum(right_centered * right_centered))
    if left_energy <= 1.0e-24 or right_energy <= 1.0e-24:
        return None
    correlation = float(
        np.sum(left_centered * right_centered) / math.sqrt(left_energy * right_energy)
    )
    if not math.isfinite(correlation):
        raise RealROMDataError("correlation calculation produced a non-finite value")
    return float(np.clip(correlation, -1.0, 1.0))


def _response_diagnostics(
    bundles: Sequence[PredictionBundle],
    max_lag: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    settings = config["diagnostics"]
    threshold = float(settings["active_threshold_kg_s"])
    records_by_mode: dict[str, list[dict[str, Any]]] = {"one_step": [], "rolling": []}
    input_columns = actual_massflow_columns(config["representative_jets"])
    for bundle in bundles:
        case = bundle.sequence.case
        events = _eligible_diagnostic_events(
            case,
            max_lag=max_lag,
            settings=settings,
            threshold=threshold,
        )
        for mode, prediction in (("one_step", bundle.one_step), ("rolling", bundle.rolling)):
            for event_index, start, input_index in events:
                record = _diagnose_event(
                    case_id=case.case_id,
                    event_index=event_index,
                    jet_column=input_columns[input_index],
                    start=start,
                    time=case.time,
                    truth=bundle.sequence.delta_forces,
                    prediction=prediction,
                    settings=settings,
                )
                if record is not None:
                    records_by_mode[mode].append(record)

    payload: dict[str, Any] = {}
    for mode, records in records_by_mode.items():
        region_matches = [
            item["dominant_region_match"]
            for item in records
            if item["dominant_region_match"] is not None
        ]
        direction_matches = [
            item["direction_match_on_truth_dominant_region"]
            for item in records
            if item["direction_match_on_truth_dominant_region"] is not None
        ]
        delay_errors = [
            abs(float(item["onset_delay_error_s"]))
            for item in records
            if item["onset_delay_error_s"] is not None
        ]
        payload[mode] = {
            "event_count": len(records),
            "truth_response_detected_event_count": sum(
                bool(item["truth_response_detected"]) for item in records
            ),
            "prediction_response_detected_event_count": sum(
                bool(item["prediction_response_detected"]) for item in records
            ),
            "delay_defined_event_count": len(delay_errors),
            "dominant_region_evaluable_event_count": len(region_matches),
            "direction_evaluable_event_count": len(direction_matches),
            "dominant_region_match_rate": float(np.mean(region_matches)) if region_matches else None,
            "direction_match_rate": float(np.mean(direction_matches)) if direction_matches else None,
            "onset_delay_mae_s": float(np.mean(delay_errors)) if delay_errors else None,
            "events": records,
        }
    return payload


def _rising_edge_events(inputs: np.ndarray, *, threshold: float) -> list[tuple[int, int]]:
    events: list[tuple[int, int]] = []
    for row in range(len(inputs)):
        previous = inputs[row - 1] if row else np.zeros(inputs.shape[1], dtype=float)
        for column in range(inputs.shape[1]):
            if abs(inputs[row, column]) > threshold and abs(previous[column]) <= threshold:
                events.append((row, column))
    return events


def _diagnose_event(
    *,
    case_id: str,
    event_index: int,
    jet_column: str,
    start: int,
    time: np.ndarray,
    truth: np.ndarray,
    prediction: np.ndarray,
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    dt = float(np.median(np.diff(time)))
    pre_rows = max(1, int(round(float(settings["pre_event_s"]) / dt)))
    horizon_rows = max(1, int(round(float(settings["response_horizon_s"]) / dt)))
    pre_start = max(0, start - pre_rows)
    end = min(len(time), start + horizon_rows + 1)
    if start >= end or pre_start >= start:
        return None
    truth_pre = truth[pre_start:start]
    prediction_pre = prediction[pre_start:start]
    truth_window = truth[start:end] - np.mean(truth_pre, axis=0)
    prediction_window = prediction[start:end] - np.nanmean(prediction_pre, axis=0)
    if not np.all(np.isfinite(prediction_window)):
        return None
    truth_delays = [
        _onset_delay(
            truth_window[:, region],
            truth_pre[:, region],
            dt=dt,
            settings=settings,
        )
        for region in range(truth_window.shape[1])
    ]
    prediction_delays = [
        _onset_delay(
            prediction_window[:, region],
            prediction_pre[:, region],
            dt=dt,
            settings=settings,
        )
        for region in range(prediction_window.shape[1])
    ]
    truth_detected_regions = [
        region for region, delay in enumerate(truth_delays) if delay is not None
    ]
    prediction_detected_regions = [
        region for region, delay in enumerate(prediction_delays) if delay is not None
    ]
    common = {
        "case_id": case_id,
        "event_index": event_index,
        "jet": jet_column,
        "opening_time_s": float(time[start]),
        "isolated_event": True,
        "truth_response_detected": bool(truth_detected_regions),
        "prediction_response_detected": bool(prediction_detected_regions),
    }
    if not truth_detected_regions:
        return {
            **common,
            "truth_dominant_region": None,
            "prediction_dominant_region": None,
            "dominant_region_match": None,
            "truth_peak_delta_N": None,
            "prediction_peak_delta_on_truth_region_N": None,
            "direction_match_on_truth_dominant_region": None,
            "truth_peak_lag_s": None,
            "prediction_peak_lag_s": None,
            "truth_onset_delay_s": None,
            "prediction_onset_delay_s": None,
            "onset_delay_error_s": None,
        }

    truth_region = max(
        truth_detected_regions,
        key=lambda region: float(np.max(np.abs(truth_window[:, region]))),
    )
    truth_lag = int(np.argmax(np.abs(truth_window[:, truth_region])))
    truth_peak = float(truth_window[truth_lag, truth_region])
    truth_delay = truth_delays[truth_region]
    assert truth_delay is not None

    prediction_region = (
        max(
            prediction_detected_regions,
            key=lambda region: float(np.max(np.abs(prediction_window[:, region]))),
        )
        if prediction_detected_regions
        else None
    )
    prediction_lag = (
        int(np.argmax(np.abs(prediction_window[:, prediction_region])))
        if prediction_region is not None
        else None
    )
    prediction_delay = prediction_delays[truth_region]
    truth_region_prediction = prediction_window[:, truth_region]
    prediction_truth_region_lag = int(np.argmax(np.abs(truth_region_prediction)))
    predicted_peak_same_region = (
        float(truth_region_prediction[prediction_truth_region_lag])
        if prediction_delay is not None
        else None
    )
    return {
        **common,
        "truth_dominant_region": f"delta_{REGION_FORCE_COLUMNS[truth_region]}",
        "prediction_dominant_region": (
            f"delta_{REGION_FORCE_COLUMNS[prediction_region]}"
            if prediction_region is not None
            else None
        ),
        "dominant_region_match": bool(truth_region == prediction_region),
        "truth_peak_delta_N": truth_peak,
        "prediction_peak_delta_on_truth_region_N": predicted_peak_same_region,
        "direction_match_on_truth_dominant_region": (
            bool(np.sign(truth_peak) == np.sign(predicted_peak_same_region))
            if predicted_peak_same_region is not None
            else False
        ),
        "truth_peak_lag_s": float(truth_lag * dt),
        "prediction_peak_lag_s": (
            float(prediction_lag * dt) if prediction_lag is not None else None
        ),
        "truth_onset_delay_s": truth_delay,
        "prediction_onset_delay_s": prediction_delay,
        "onset_delay_error_s": (
            float(prediction_delay - truth_delay)
            if truth_delay is not None and prediction_delay is not None
            else None
        ),
    }


def _onset_delay(
    response: np.ndarray,
    pre_values: np.ndarray,
    *,
    dt: float,
    settings: dict[str, Any],
) -> float | None:
    peak = float(np.max(np.abs(response)))
    noise = float(np.std(pre_values))
    limit = max(
        float(settings["onset_sigma_multiplier"]) * noise,
        float(settings["onset_peak_fraction"]) * peak,
        1.0e-12,
    )
    required = int(settings["onset_consecutive_samples"])
    above = np.abs(response) >= limit
    if required > len(above):
        return None
    for index in range(0, len(above) - required + 1):
        if bool(np.all(above[index : index + required])):
            return float(index * dt)
    return None


def _case_metadata(case: RealROMCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "case_dir": str(case.case_dir),
        "timeseries_path": str(case.timeseries_path),
        "timeseries_sha256": case.timeseries_sha256,
        "quality_status": case.quality_status,
        "quality_report_path": str(case.quality_report_path),
        "quality_report_sha256": case.quality_report_sha256,
        "manifest_path": str(case.manifest_path),
        "manifest_sha256": case.manifest_sha256,
        "provenance_tokens": list(case.provenance_tokens),
        "case_type": case.case_type,
        "nojet_drift_flag_count": case.nojet_drift_flag_count,
        "nojet_jump_flag_count": case.nojet_jump_flag_count,
        "baseline_review_approved": case.baseline_review_approved,
        "compatibility_signature": case.compatibility_signature,
        "raw_rows": case.raw_rows,
        "model_rows": int(len(case.time)),
        "raw_time_step_s": case.raw_time_step_s,
        "sample_stride": case.sample_stride,
        "effective_time_step_s": case.effective_time_step_s,
    }


def _delivery_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "model": output_dir / "B04_real_ROM_model.json",
        "metrics": output_dir / "B04_real_ROM_metrics.json",
        "result": output_dir / "B04_real_ROM_result.md",
        "training_plot": output_dir / "B04_real_ROM_training_prediction.png",
        "validation_plot": output_dir / "B04_real_ROM_validation_prediction.png",
        "training_csv": output_dir / "B04_real_ROM_training_predictions.csv",
        "validation_csv": output_dir / "B04_real_ROM_validation_predictions.csv",
    }


def _write_prediction_csv(
    path: Path,
    bundles: Sequence[PredictionBundle],
    max_lag: int,
    config: dict[str, Any],
) -> None:
    input_columns = actual_massflow_columns(config["representative_jets"])
    fieldnames = ["case_id", "sample_index", "physical_time", "prediction_available"]
    fieldnames.extend(input_columns)
    for region in REGION_FORCE_COLUMNS:
        name = f"delta_{region}"
        fieldnames.extend(
            [
                f"{name}_truth",
                f"{name}_one_step",
                f"{name}_one_step_error",
                f"{name}_rolling",
                f"{name}_rolling_error",
            ]
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for bundle in bundles:
            case = bundle.sequence.case
            for row_index, time_value in enumerate(case.time):
                record: dict[str, Any] = {
                    "case_id": case.case_id,
                    "sample_index": row_index,
                    "physical_time": float(time_value),
                    "prediction_available": row_index >= max_lag,
                }
                for column_index, column in enumerate(input_columns):
                    record[column] = float(case.inputs[row_index, column_index])
                for region_index, region in enumerate(REGION_FORCE_COLUMNS):
                    name = f"delta_{region}"
                    truth = float(bundle.sequence.delta_forces[row_index, region_index])
                    one_step = float(bundle.one_step[row_index, region_index])
                    rolling = float(bundle.rolling[row_index, region_index])
                    record[f"{name}_truth"] = truth
                    record[f"{name}_one_step"] = _csv_number(one_step)
                    record[f"{name}_one_step_error"] = _csv_number(one_step - truth)
                    record[f"{name}_rolling"] = _csv_number(rolling)
                    record[f"{name}_rolling_error"] = _csv_number(rolling - truth)
                writer.writerow(record)


def _csv_number(value: float) -> float | str:
    return float(value) if math.isfinite(float(value)) else ""


def _write_prediction_plot(
    path: Path,
    bundles: Sequence[PredictionBundle],
    title: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    time_values, truth, one_step, rolling, boundaries = _concatenate_for_plot(bundles)
    figure, axes = plt.subplots(6, 1, figsize=(14, 15), sharex=True, constrained_layout=True)
    for index, axis in enumerate(axes):
        axis.plot(time_values, truth[:, index], color="#222222", linewidth=1.0, label="truth")
        axis.plot(time_values, one_step[:, index], color="#1971c2", linewidth=0.9, alpha=0.9, label="one-step")
        axis.plot(time_values, rolling[:, index], color="#e03131", linewidth=0.9, alpha=0.85, label="rolling")
        axis.axhline(0.0, color="#adb5bd", linewidth=0.6)
        for boundary in boundaries:
            axis.axvline(boundary, color="#ced4da", linestyle="--", linewidth=0.7)
        axis.set_ylabel(f"Delta {REGION_FORCE_COLUMNS[index]} [N]")
        axis.grid(True, color="#e9ecef", linewidth=0.5)
    axes[0].legend(loc="upper right", ncol=3)
    axes[0].set_title(title)
    axes[-1].set_xlabel("concatenated case time [s]")
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _concatenate_for_plot(
    bundles: Sequence[PredictionBundle],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[float]]:
    times: list[np.ndarray] = []
    truths: list[np.ndarray] = []
    one_steps: list[np.ndarray] = []
    rollings: list[np.ndarray] = []
    boundaries: list[float] = []
    offset = 0.0
    for index, bundle in enumerate(bundles):
        local = bundle.sequence.case.time - bundle.sequence.case.time[0]
        shifted = local + offset
        if index:
            boundaries.append(float(shifted[0]))
        times.append(shifted)
        truths.append(bundle.sequence.delta_forces)
        one_steps.append(bundle.one_step)
        rollings.append(bundle.rolling)
        offset = float(shifted[-1] + bundle.sequence.case.effective_time_step_s)
    return (
        np.concatenate(times),
        np.vstack(truths),
        np.vstack(one_steps),
        np.vstack(rollings),
        boundaries,
    )


def _write_result_markdown(path: Path, metrics: dict[str, Any], paths: dict[str, Path]) -> None:
    validation = metrics["metrics"]["validation"]
    training = metrics["metrics"]["training"]
    diagnostic = metrics["response_diagnostics"]["validation"]
    lines = [
        "# B04 第一版真实 ROM 结果",
        "",
        "## 结论",
        "",
        "运行状态：**COMPLETE**；验收状态：**REVIEW_REQUIRED**。模型严格使用 6 路代表喷口实际质量流量，输出为 6 个承载区域相对无喷气基准的气动力变化。训练和验证按完整 case 分离，没有随机拆分时序；验证数据未参与基准、缩放、拟合或调参。由于任务未规定数值阈值，需根据下述独立验证指标与诊断完成工程验收。",
        "",
        "## 数据与模型",
        "",
        f"- 输入：`{', '.join(metrics['data_contract']['input_columns'])}`。",
        f"- 输出：`{', '.join(metrics['data_contract']['output_columns'])}`。",
        f"- 基准：`{metrics['baseline']['case_id']}` 尾部 {metrics['baseline']['tail_fraction']:.0%} 的均值。",
        f"- ARX：输入滞后 {metrics['model']['input_lags']}，输出滞后 {metrics['model']['output_lags']}，岭系数 {metrics['model']['ridge_alpha']:.6g}，有效时间步 {metrics['preprocessing']['effective_time_step_s']:.6g} s。",
        f"- 训练有效行 {metrics['model']['training_fit_rows']}；独立验证评价行 {validation['evaluation_rows']}。",
        "",
        "### Case 级独立性证据",
        "",
        "| 分区 | case_id | 行数 | dt (s) | SHA256 | B04质量 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for role in ("training", "validation"):
        for item in metrics["data_partition"][role]:
            lines.append(
                f"| {role} | {item['case_id']} | {item['raw_rows']} | {item['raw_time_step_s']:.6g} | "
                f"`{item['timeseries_sha256']}` | {item['quality_status']} |"
            )
    lines.extend(
        [
            "",
            "### 无喷气基准尾段诊断",
            "",
            f"B04 漂移发现 {metrics['baseline']['B04_nojet_stability_review']['drift_flag_count']} 项，"
            f"跳变发现 {metrics['baseline']['B04_nojet_stability_review']['jump_flag_count']} 项，"
            f"显式工程复核批准={metrics['baseline']['B04_nojet_stability_review']['explicit_review_approved']}。",
            "",
            "以下数值供工程稳定性复核；它们不会在未给定阈值时被擅自解读为自动 PASS。",
            "",
            "| 区域 | 标准差 (N) | 峰峰值 (N) | 首尾窗口漂移 (N) |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for region, item in metrics["baseline"]["tail_stability_diagnostics"].items():
        lines.append(
            f"| {region} | {_md_number(item['std_N'])} | "
            f"{_md_number(item['peak_to_peak_N'])} | {_md_number(item['endpoint_drift_N'])} |"
        )
    lines.extend(
        [
            "",
            "## 独立验证指标",
            "",
            "NRMSE 采用验证真值极差归一化。一步预测每步使用此前真实输出；连续滚动只用最初 `max_lag` 行暖启动，此后不再读取真实输出。",
            "",
            "| 区域 | 一步 RMSE (N) | 一步 NRMSE | 一步相关系数 | 滚动 RMSE (N) | 滚动 NRMSE | 滚动相关系数 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for region in metrics["data_contract"]["output_columns"]:
        one = validation["one_step"][region]
        rolling = validation["rolling"][region]
        lines.append(
            f"| {region} | {_md_number(one['rmse'])} | {_md_number(one['nrmse'])} | "
            f"{_md_number(one['correlation'])} | {_md_number(rolling['rmse'])} | "
            f"{_md_number(rolling['nrmse'])} | {_md_number(rolling['correlation'])} |"
        )
    lines.extend(
        [
            "",
            "## 主要区域、方向与延迟诊断",
            "",
            "| 模式 | 隔离事件数 | 真值响应检出 | 预测响应检出 | 延迟可定义 | 主响应区域匹配率 | 方向匹配率 | 起响应延迟 MAE (s) |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for mode in ("one_step", "rolling"):
        item = diagnostic[mode]
        lines.append(
            f"| {mode} | {item['event_count']} | "
            f"{item['truth_response_detected_event_count']} | "
            f"{item['prediction_response_detected_event_count']} | "
            f"{item['delay_defined_event_count']} | "
            f"{_md_number(item['dominant_region_match_rate'])} | "
            f"{_md_number(item['direction_match_rate'])} | {_md_number(item['onset_delay_mae_s'])} |"
        )
    lines.extend(
        [
            "",
            "### 独立验证连续滚动逐事件明细",
            "",
            "方向列均在真值主响应区域上比较；`NA` 表示按冻结阈值无法确定起响应时刻。",
            "",
            "| Case | 事件 | 喷口 | 真值检出 | 预测检出 | 真值主区域 | 预测主区域 | 真值方向 | 预测方向 | 真值延迟 (s) | 预测延迟 (s) | 区域匹配 | 方向匹配 |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for event in diagnostic["rolling"]["events"]:
        lines.append(
            f"| {event['case_id']} | {event['event_index']} | {event['jet']} | "
            f"{_md_bool(event['truth_response_detected'])} | "
            f"{_md_bool(event['prediction_response_detected'])} | "
            f"{_md_text(event['truth_dominant_region'])} | "
            f"{_md_text(event['prediction_dominant_region'])} | "
            f"{_md_direction(event['truth_peak_delta_N'])} | "
            f"{_md_direction(event['prediction_peak_delta_on_truth_region_N'])} | "
            f"{_md_number(event['truth_onset_delay_s'])} | "
            f"{_md_number(event['prediction_onset_delay_s'])} | "
            f"{_md_bool(event['dominant_region_match'])} | "
            f"{_md_bool(event['direction_match_on_truth_dominant_region'])} |"
        )
    lines.extend(
        [
            "",
            "这些诊断逐个实际质量流量上升沿比较真值与预测的主响应区域、峰值方向和起响应时刻；一步与滚动的全部逐事件数据保存在指标 JSON 中。任务未给定数值通过阈值，因此报告事实指标，不擅自把工程判断改写成自动 PASS。",
            "",
            "## 训练集参考指标",
            "",
            f"- 一步宏平均：RMSE={_md_number(training['macro_average']['one_step']['rmse'])} N，NRMSE={_md_number(training['macro_average']['one_step']['nrmse'])}，相关系数={_md_number(training['macro_average']['one_step']['correlation'])}。",
            f"- 滚动宏平均：RMSE={_md_number(training['macro_average']['rolling']['rmse'])} N，NRMSE={_md_number(training['macro_average']['rolling']['nrmse'])}，相关系数={_md_number(training['macro_average']['rolling']['correlation'])}。",
            "",
            "## 图与可复现产物",
            "",
            f"- 训练预测图：[{paths['training_plot'].name}]({paths['training_plot'].name})",
            f"- 独立验证预测图：[{paths['validation_plot'].name}]({paths['validation_plot'].name})",
            f"- 训练预测明细：[{paths['training_csv'].name}]({paths['training_csv'].name})",
            f"- 验证预测明细：[{paths['validation_csv'].name}]({paths['validation_csv'].name})",
            f"- 模型：[{paths['model'].name}]({paths['model'].name})",
            f"- 指标：[{paths['metrics'].name}]({paths['metrics'].name})",
            "",
            "重新运行：",
            "",
            "```bash",
            metrics["reproducibility"]["command"],
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_blocked_delivery(output_dir: Path, config: dict[str, Any], issues: Sequence[str]) -> None:
    paths = _delivery_paths(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    quarantined = _quarantine_stale_success_artifacts(paths)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "run_status": "BLOCKED",
        "acceptance_status": "NOT_EVALUATED",
        "model_trained": False,
        "validation_performed": False,
        "issues": list(issues),
        "stale_success_artifacts_quarantined": quarantined,
        "data_contract": {
            "input_columns": list(actual_massflow_columns(config["representative_jets"])),
            "output_columns": list(REGION_FORCE_COLUMNS),
            "input_source_policy": "actual_massflow only; no fallback or zero fill",
            "source_audit_columns": list(ALL_ACTUAL_MASSFLOW_COLUMNS),
            "nonrepresentative_jet_policy": (
                "the other 18 actual_massflow columns must be present and inactive"
            ),
        },
        "expected_cases": config["data"],
        "reproducibility": _reproducibility_payload(config, include_runtime=False),
    }
    _write_strict_json(paths["metrics"], payload)
    lines = [
        "# B04 第一版真实 ROM 结果",
        "",
        "## 状态",
        "",
        "**BLOCKED：未训练模型，也未生成或展示伪造的真实验证指标。**",
        "",
        "## 阻塞项",
        "",
    ]
    lines.extend(f"- {issue}" for issue in issues)
    lines.extend(
        [
            "",
            "动作表不是实际质量流量或气动力结果。必须先完成独立训练/验证 STAR 算例，生成各自的 `processed/timeseries.csv`，由上游 runner 在 `case_manifest.yaml` 中签发 `source_run_evidence` 和 `rom_compatibility`，再重跑 B04 以生成绑定当前 CSV/manifest 哈希的 PASS `quality_report.json`，最后重跑 ROM：",
            "",
            "```bash",
            payload["reproducibility"]["command"],
            "```",
            "",
        ]
    )
    paths["result"].write_text("\n".join(lines), encoding="utf-8")


def _write_invalid_config_delivery(
    output_dir: Path,
    *,
    config_file: Path,
    issues: Sequence[str],
) -> None:
    """Replace any fixed-name success delivery when normalization itself fails."""

    paths = _delivery_paths(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    quarantined = _quarantine_stale_success_artifacts(paths)
    script_path = PROJECT_ROOT / "scripts" / "analysis" / "run_b54_real_rom.py"
    arx_module_path = Path(__file__).resolve().with_name("arx_model.py")
    quality_gate_module_path = PROJECT_ROOT / "flow_control" / "star_ingest" / "b04_real_quality.py"
    command_argv = [
        sys.executable,
        str(script_path),
        "--config",
        str(config_file),
        "--output-dir",
        str(output_dir),
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "run_status": "BLOCKED",
        "acceptance_status": "NOT_EVALUATED",
        "model_trained": False,
        "validation_performed": False,
        "invalid_configuration": True,
        "issues": list(issues),
        "stale_success_artifacts_quarantined": quarantined,
        "reproducibility": {
            "config_path": str(config_file),
            "config_file_exists": config_file.is_file(),
            "config_file_sha256": (
                _sha256_file(config_file) if config_file.is_file() else None
            ),
            "command_argv": command_argv,
            "command": shlex.join(command_argv),
            "runner_sha256": _sha256_file(script_path),
            "module_sha256": _sha256_file(Path(__file__).resolve()),
            "arx_module_sha256": _sha256_file(arx_module_path),
            "quality_gate_module_sha256": _sha256_file(quality_gate_module_path),
            "random_operations": False,
        },
    }
    _write_strict_json(paths["metrics"], payload)
    lines = [
        "# B04 第一版真实 ROM 结果",
        "",
        "## 状态",
        "",
        "**BLOCKED：B54 配置无效，旧的固定文件名成功产物已隔离。**",
        "",
        "## 配置问题",
        "",
        *(f"- {issue}" for issue in issues),
        "",
        "修正配置后重跑：",
        "",
        "```bash",
        payload["reproducibility"]["command"],
        "```",
        "",
    ]
    paths["result"].write_text("\n".join(lines), encoding="utf-8")


def _quarantine_stale_success_artifacts(paths: dict[str, Path]) -> list[str]:
    """Move prior success-only files out of the fixed delivery names on BLOCKED runs."""

    success_keys = ("model", "training_plot", "validation_plot", "training_csv", "validation_csv")
    existing = [paths[key] for key in success_keys if paths[key].is_file()]
    if not existing:
        return []
    stale_dir = paths["metrics"].parent / "B04_real_ROM_stale"
    stale_dir.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for source in existing:
        digest = _sha256_file(source)[:12]
        target = stale_dir / f"{source.stem}_{digest}{source.suffix}"
        source.replace(target)
        moved.append(str(target))
    return moved


def _md_number(value: float | None) -> str:
    return "NA" if value is None else f"{float(value):.6g}"


def _md_direction(value: float | None) -> str:
    if value is None:
        return "NA"
    numeric = float(value)
    if numeric > 0.0:
        return "正"
    if numeric < 0.0:
        return "负"
    return "零"


def _md_bool(value: bool | None) -> str:
    if value is None:
        return "NA"
    return "是" if value else "否"


def _md_text(value: str | None) -> str:
    return value if value else "NA"


def _write_strict_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_source_artifact(path: str | Path) -> str:
    """Hash a complete upstream .sim file or raw STAR directory deterministically."""

    path = Path(path).resolve()
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if not path.is_dir():
        raise ValueError(f"source artifact does not exist: {path}")

    digest.update(b"raw-star-directory\0")
    files: list[Path] = []
    for candidate in path.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"source artifact directory may not contain symlinks: {candidate}")
        if candidate.is_file():
            files.append(candidate)
        elif not candidate.is_dir():
            raise ValueError(f"unsupported source artifact entry: {candidate}")
    if not files:
        raise ValueError(f"source artifact directory contains no files: {path}")
    for candidate in sorted(files, key=lambda item: item.relative_to(path).as_posix()):
        relative = candidate.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, byteorder="big"))
        digest.update(relative)
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _reproducibility_payload(
    config: dict[str, Any],
    *,
    include_runtime: bool,
) -> dict[str, Any]:
    script_path = PROJECT_ROOT / "scripts" / "analysis" / "run_b54_real_rom.py"
    arx_module_path = Path(__file__).resolve().with_name("arx_model.py")
    quality_gate_module_path = PROJECT_ROOT / "flow_control" / "star_ingest" / "b04_real_quality.py"
    command_argv = [
        sys.executable,
        str(script_path),
        "--config",
        config["config_path"],
        "--output-dir",
        config["output_dir"],
    ]
    effective_hash = _sha256_json(config)
    payload: dict[str, Any] = {
        "config_path": config["config_path"],
        "config_file_sha256": _sha256_file(Path(config["config_path"])),
        "effective_config_sha256": effective_hash,
        # Compatibility alias retained for readers of the initial B54 draft.
        "config_sha256": effective_hash,
        "command_argv": command_argv,
        "command": shlex.join(command_argv),
        "runner_sha256": _sha256_file(script_path),
        "module_sha256": _sha256_file(Path(__file__).resolve()),
        "arx_module_sha256": _sha256_file(arx_module_path),
        "quality_gate_module_sha256": _sha256_file(quality_gate_module_path),
        "random_operations": False,
    }
    if include_runtime:
        payload["runtime_versions"] = {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pyyaml": str(yaml.__version__),
            "matplotlib": importlib.metadata.version("matplotlib"),
        }
    return payload


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point for the reproducible B54 delivery."""

    parser = argparse.ArgumentParser(
        description="Train the six-input/six-output real ARX ridge ROM and validate on independent cases."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"B54 YAML configuration (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument("--output-dir", help="Optional output directory override.")
    args = parser.parse_args(argv)
    try:
        result = run_b54_real_rom(args.config, output_dir=args.output_dir)
    except RealROMDataError as exc:
        print("B54 real ROM blocked:")
        for issue in exc.issues:
            print(f"- {issue}")
        return 2
    print(f"B54 real ROM complete: {result.output_dir}")
    print(f"model: {result.model_path}")
    print(f"metrics: {result.metrics_path}")
    print(f"result: {result.result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
