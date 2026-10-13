"""B54（任务 B4）：以 B53 数据集为输入的第一版真实 ARX+岭回归 ROM。

数据契约：

* 输入：六个代表喷口的 ``actual_massflow_XX``（B53 已核验非代表喷口全关）；
* 输出：B53 的 ``delta_Fz_*``，即相对同一时刻无喷气参考算例的六区升力变化；
* 拟合单元：B53 的连续片段 ``segment_id``。滞后项只在片段内构造，不跨越被
  剔除事件留下的空洞，也不跨 Case。

训练/验证严格按整个 STAR Case 划分（B53 已保证），验证集从不参与标准化、
超参数选择或拟合。滞后阶数与岭系数只在训练集内部用分块交叉验证选取：把每个
训练片段按时间切成连续块，留出一块做连续滚动预测，取平均 RMSE 最小的组合。

验证集为空时仍会拟合并输出训练诊断，但状态为 ``TRAINING_ONLY``，结果说明中
不给出任何验证结论。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import Any, Sequence

import numpy as np
import yaml

from .arx_model import ARXModel
from .b54_real_rom import (
    REGION_FORCE_COLUMNS,
    ScaledRealARXModel,
    _compute_region_metrics,
    _stable_column_mean_std,
    actual_massflow_columns,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "b54" / "real_rom_b53.yaml"
ALL_ACTUAL_COLUMNS = tuple(f"actual_massflow_{index:02d}" for index in range(1, 25))
OUTPUT_COLUMNS = tuple(f"delta_{region}" for region in REGION_FORCE_COLUMNS)
NOISE_COLUMNS = tuple(f"baseline_noise_{region}" for region in REGION_FORCE_COLUMNS)
BASE_COLUMNS = (
    "source_case_id",
    "segment_id",
    "physical_time",
    "event_id",
    "jet_id",
    "phase",
    "action_start_s",
    "baseline_mode",
    *ALL_ACTUAL_COLUMNS,
)
REQUIRED_COLUMNS = (*BASE_COLUMNS, *OUTPUT_COLUMNS, *NOISE_COLUMNS)
# 主模型输出六区升力；auxiliary 输出整车量（B53 同样已做同时刻相减）。
AUXILIARY_OUTPUTS = ("fz", "Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment", "Jet_Reaction_Z")
OUTPUT_SETS = {"regions": REGION_FORCE_COLUMNS, "auxiliary": AUXILIARY_OUTPUTS}
UNITS = {"Pitch_Moment": "N·m", "Roll_Moment": "N·m"}
LABELS = {
    "fz": "车底总升力 fz",
    "Fz_Total": "整车升力 Fz_Total",
    "Drag_Total": "阻力 Drag_Total",
    "Pitch_Moment": "俯仰力矩 Pitch_Moment",
    "Roll_Moment": "侧倾力矩 Roll_Moment",
    "Jet_Reaction_Z": "喷口反作用力 Jet_Reaction_Z",
}


def unit(name: str) -> str:
    return UNITS.get(name, "N")


def short_name(name: str) -> str:
    return name[3:] if name in REGION_FORCE_COLUMNS else name


class FlexibleScaledARXModel(ScaledRealARXModel):
    """非六区输出（整车量）的 ARX 快照：沿用缩放与预测，放开六区命名契约。"""

    def __post_init__(self) -> None:
        pass

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["model_type"] = "scaled_multioutput_ARX_ridge_auxiliary"
        payload["data_contract"]["output_columns"] = [name.removeprefix("delta_") for name in self.arx.output_names_]
        payload["data_contract"]["output_count"] = len(self.arx.output_names_)
        return payload
REFERENCE_BASELINE_MODE = "reference_time_aligned"


class ROMDataError(ValueError):
    """数据不满足 ROM 训练前提。"""


@dataclass(frozen=True)
class Segment:
    """一个时间连续、可构造滞后项的 B53 片段。"""

    case_id: str
    segment_id: str
    time: np.ndarray
    inputs: np.ndarray
    all_actual: np.ndarray
    outputs: np.ndarray
    noise: np.ndarray
    event_id: np.ndarray
    jet_id: np.ndarray
    phase: np.ndarray
    action_start: np.ndarray

    def __len__(self) -> int:
        return len(self.time)

    def take(self, index: np.ndarray | slice) -> "Segment":
        return replace(
            self,
            time=self.time[index],
            inputs=self.inputs[index],
            all_actual=self.all_actual[index],
            outputs=self.outputs[index],
            noise=self.noise[index],
            event_id=self.event_id[index],
            jet_id=self.jet_id[index],
            phase=self.phase[index],
            action_start=self.action_start[index],
        )


@dataclass(frozen=True)
class Hyper:
    input_lags: int
    output_lags: int
    ridge_alpha: float

    def as_dict(self, dt: float) -> dict[str, Any]:
        return {
            "input_lags": self.input_lags,
            "input_history_s": self.input_lags * dt,
            "output_lags": self.output_lags,
            "output_history_s": self.output_lags * dt,
            "ridge_alpha": self.ridge_alpha,
        }


# ---------------------------------------------------------------- 配置与数据


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    root = Path(raw.get("project_root", "."))
    if not root.is_absolute():
        anchor = (
            config_path.parents[2]
            if len(config_path.parents) > 2 and config_path.parents[1].name == "configs"
            else config_path.parent
        )
        root = (anchor / root).resolve()

    def resolve(value: str | Path) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else (root / candidate).resolve()

    data = dict(raw.get("data") or {})
    model = dict(raw.get("model") or {})
    diagnostics = dict(raw.get("diagnostics") or {})
    config = {
        "config_path": str(config_path),
        "project_root": str(root),
        "representative_jets": [int(value) for value in raw.get("representative_jets", (2, 3, 9, 11, 17, 19))],
        "training_dataset": str(resolve(data.get("training_dataset", "artifacts/B53_training_data/training_dataset.csv"))),
        "validation_dataset": str(
            resolve(data.get("validation_dataset", "artifacts/B53_training_data/validation_dataset.csv"))
        ),
        "sample_stride": int((raw.get("preprocessing") or {}).get("sample_stride", 10)),
        "include_current_input": bool(model.get("include_current_input", False)),
        "input_history_s": [float(value) for value in model.get("input_history_s", [0.1, 0.2])],
        "output_lags": [int(value) for value in model.get("output_lags", [2, 5, 10])],
        "ridge_alpha": [float(value) for value in model.get("ridge_alpha", [1.0, 10.0, 100.0])],
        "cv_folds": int(model.get("cv_folds", 4)),
        "massflow_active_threshold_kg_s": float(diagnostics.get("massflow_active_threshold_kg_s", 1.0e-8)),
        "response_horizon_s": float(diagnostics.get("response_horizon_s", 0.15)),
        # 响应随时间转移：先本区，再同段两侧，再相邻段；主响应区按窗口分别判定。
        "response_windows_s": [
            [float(a), float(b)]
            for a, b in diagnostics.get("response_windows_s", [[0.0, 0.005], [0.005, 0.02], [0.02, 0.1]])
        ],
        "onset_sigma_multiplier": float(diagnostics.get("onset_sigma_multiplier", 3.0)),
        "onset_peak_fraction": float(diagnostics.get("onset_peak_fraction", 0.2)),
        "onset_consecutive_samples": int(diagnostics.get("onset_consecutive_samples", 3)),
        "output_dir": str(resolve(raw.get("output_dir", "artifacts"))),
        "output_set": str(raw.get("output_set", "regions")),
        "artifact_prefix": str(raw.get("artifact_prefix", "B04_real_ROM")),
    }
    if config["output_set"] not in OUTPUT_SETS:
        raise ROMDataError(f"output_set 必须是 {sorted(OUTPUT_SETS)} 之一")
    config["outputs"] = list(OUTPUT_SETS[config["output_set"]])
    jets = config["representative_jets"]
    if len(jets) != 6 or len(set(jets)) != 6 or any(jet < 1 or jet > 24 for jet in jets):
        raise ROMDataError("representative_jets 必须是 6 个不重复的 1..24 编号")
    if config["sample_stride"] < 1 or config["cv_folds"] < 2:
        raise ROMDataError("sample_stride 必须 >=1，cv_folds 必须 >=2")
    if not config["input_history_s"] or not config["output_lags"] or not config["ridge_alpha"]:
        raise ROMDataError("模型超参数候选不能为空")
    return config


def _float(value: str, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ROMDataError(f"{label} 不是数值: {value!r}") from None
    if not math.isfinite(result):
        raise ROMDataError(f"{label} 非有限值: {value!r}")
    return result


def read_dataset(path: Path, jets: Sequence[int], names: Sequence[str] = REGION_FORCE_COLUMNS) -> list[Segment]:
    """读取 B53 数据集 CSV，按 (Case, segment_id) 拆成连续片段。"""

    if not path.is_file():
        return []
    output_columns = [f"delta_{name}" for name in names]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        noise_columns = [f"baseline_noise_{name}" if f"baseline_noise_{name}" in headers else "" for name in names]
        missing = [column for column in (*BASE_COLUMNS, *output_columns) if column not in headers]
        if missing:
            raise ROMDataError(f"{path} 缺少列: {', '.join(missing)}")
        rows = list(reader)
    if not rows:
        return []
    modes = {row["baseline_mode"] for row in rows}
    if modes != {REFERENCE_BASELINE_MODE}:
        raise ROMDataError(
            f"{path} 的 baseline_mode={sorted(modes)}；ROM 要求 B53 使用时间对齐无喷气参考基准"
        )
    input_columns = actual_massflow_columns(jets)
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault((row["source_case_id"], row["segment_id"]), []).append(row)
    segments: list[Segment] = []
    for (case_id, segment_id), group in sorted(groups.items()):
        group.sort(key=lambda row: float(row["physical_time"]))
        line = f"{path.name}:{case_id}/{segment_id}"
        segments.append(
            Segment(
                case_id=case_id,
                segment_id=segment_id,
                time=np.array([_float(row["physical_time"], line) for row in group]),
                inputs=np.array([[_float(row[column], f"{line} {column}") for column in input_columns] for row in group]),
                all_actual=np.array(
                    [[_float(row[column], f"{line} {column}") for column in ALL_ACTUAL_COLUMNS] for row in group]
                ),
                outputs=np.array([[_float(row[column], f"{line} {column}") for column in output_columns] for row in group]),
                noise=np.array(
                    [[_float(row[column], f"{line} {column}") if column else np.nan for column in noise_columns] for row in group]
                ),
                event_id=np.array([row["event_id"] for row in group], dtype=object),
                jet_id=np.array([row["jet_id"] for row in group], dtype=object),
                phase=np.array([row["phase"] for row in group], dtype=object),
                action_start=np.array([_float(row["action_start_s"], line) for row in group]),
            )
        )
    return segments


def _raw_dt(segments: Sequence[Segment], label: str) -> float:
    steps: list[float] = []
    for segment in segments:
        difference = np.diff(segment.time)
        if np.any(difference <= 0):
            raise ROMDataError(f"{label} 片段 {segment.segment_id} 的时间不是严格递增")
        local = float(np.median(difference))
        if np.max(np.abs(difference - local)) > 1.0e-3 * local:
            raise ROMDataError(f"{label} 片段 {segment.segment_id} 不是均匀采样")
        steps.append(local)
    if max(steps) - min(steps) > 1.0e-6 * max(steps):
        raise ROMDataError(f"{label} 各片段采样步长不一致: {sorted(set(steps))}")
    return steps[0]


def check_partitions(
    training: Sequence[Segment], validation: Sequence[Segment], config: dict[str, Any]
) -> dict[str, Any]:
    """训练/验证独立、非代表喷口全关、代表喷口在训练中均被激励。"""

    issues: list[str] = []
    overlap = {item.case_id for item in training} & {item.case_id for item in validation}
    if overlap:
        issues.append(f"同一 STAR Case 同时出现在训练和验证中: {sorted(overlap)}")
    threshold = config["massflow_active_threshold_kg_s"]
    representative = {f"actual_massflow_{jet:02d}" for jet in config["representative_jets"]}
    for label, segments in (("training", training), ("validation", validation)):
        for segment in segments:
            for index, column in enumerate(ALL_ACTUAL_COLUMNS):
                if column not in representative and np.max(np.abs(segment.all_actual[:, index])) > threshold:
                    issues.append(f"{label} 片段 {segment.segment_id} 中非代表喷口 {column} 有流量")
    if training:
        excitation = np.max(np.abs(np.vstack([item.inputs for item in training])), axis=0)
        silent = [
            jet for jet, value in zip(config["representative_jets"], excitation) if value <= threshold
        ]
        if silent:
            issues.append(f"训练集中代表喷口未被激励: {silent}")
    if issues:
        raise ROMDataError(issues[0] if len(issues) == 1 else "; ".join(issues))
    dt_train = _raw_dt(training, "training")
    if validation:
        dt_val = _raw_dt(validation, "validation")
        if not math.isclose(dt_train, dt_val, rel_tol=1.0e-6):
            raise ROMDataError(f"训练 dt={dt_train:g}s 与验证 dt={dt_val:g}s 不一致")
    return {"raw_dt_s": dt_train}


def subsample(segments: Sequence[Segment], stride: int) -> list[Segment]:
    return [segment.take(slice(None, None, stride)) for segment in segments]


# ---------------------------------------------------------------- ARX 拟合与预测


def _design(u: np.ndarray, y: np.ndarray, hyper: Hyper, current: bool) -> tuple[np.ndarray, np.ndarray]:
    """与 ARXModel._feature_at 相同列序的向量化设计矩阵。"""

    first_input_lag = 0 if current else 1
    lag = max(hyper.output_lags, hyper.input_lags - 1 + first_input_lag)
    n = len(u)
    blocks = [np.ones((n - lag, 1))]
    blocks.extend(y[lag - k : n - k] for k in range(1, hyper.output_lags + 1))
    blocks.extend(
        u[lag - k : n - k] for k in range(first_input_lag, first_input_lag + hyper.input_lags)
    )
    return np.hstack(blocks), y[lag:]


def fit_model(
    segments: Sequence[Segment],
    hyper: Hyper,
    current: bool,
    jets: Sequence[int],
    output_names: Sequence[str] = REGION_FORCE_COLUMNS,
) -> ScaledRealARXModel:
    usable = [item for item in segments if len(item) > max(hyper.input_lags, hyper.output_lags) + 1]
    if not usable:
        raise ROMDataError("没有长度超过最大滞后的训练片段")
    input_mean, input_scale = _stable_column_mean_std(np.vstack([item.inputs for item in usable]), "training inputs")
    output_mean, output_scale = _stable_column_mean_std(
        np.vstack([item.outputs for item in usable]), "training outputs"
    )
    if np.any(input_scale <= 1.0e-12) or np.any(output_scale <= 1.0e-12):
        raise ROMDataError("训练输入或输出存在零方差列")
    x_parts, y_parts = [], []
    for item in usable:
        x, y = _design((item.inputs - input_mean) / input_scale, (item.outputs - output_mean) / output_scale, hyper, current)
        x_parts.append(x)
        y_parts.append(y)
    x_all, y_all = np.vstack(x_parts), np.vstack(y_parts)
    penalty = hyper.ridge_alpha * np.eye(x_all.shape[1])
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(x_all.T @ x_all + penalty, x_all.T @ y_all)

    arx = ARXModel(
        input_lags=hyper.input_lags,
        output_lags=hyper.output_lags,
        include_current_input=current,
        ridge_alpha=hyper.ridge_alpha,
    )
    arx.input_names_ = list(actual_massflow_columns(jets))
    arx.output_names_ = [f"delta_{name}" for name in output_names]
    names = ["intercept"]
    for k in range(1, hyper.output_lags + 1):
        names.extend(f"{name}(t-{k})" for name in arx.output_names_)
    first = 0 if current else 1
    for k in range(first, first + hyper.input_lags):
        names.extend(f"{name}({'t' if k == 0 else f't-{k}'})" for name in arx.input_names_)
    arx.feature_names_ = names
    arx.coefficients_ = coefficients
    model_class = ScaledRealARXModel if tuple(output_names) == REGION_FORCE_COLUMNS else FlexibleScaledARXModel
    return model_class(
        arx=arx,
        input_mean=input_mean,
        input_scale=input_scale,
        output_mean=output_mean,
        output_scale=output_scale,
    )


def predict(model: ScaledRealARXModel, segment: Segment) -> tuple[np.ndarray, np.ndarray]:
    """返回 (一步预测, 连续滚动预测)；两者前 max_lag 行为 NaN。

    滚动预测只用片段前 max_lag 行真实输出初始化，之后完全反馈自身输出。
    """

    arx = model.arx
    lag = arx.max_lag
    u = (segment.inputs - model.input_mean) / model.input_scale
    y = (segment.outputs - model.output_mean) / model.output_scale
    hyper = Hyper(arx.input_lags, arx.output_lags, arx.ridge_alpha)
    x, _ = _design(u, y, hyper, arx.include_current_input)
    one_step = np.full_like(segment.outputs, np.nan)
    one_step[lag:] = (x @ arx.coefficients_) * model.output_scale + model.output_mean

    coefficients = arx.coefficients_
    na = arx.output_lags
    ny, nu = y.shape[1], u.shape[1]
    ar = [coefficients[1 + ny * (k - 1) : 1 + ny * k] for k in range(1, na + 1)]
    exogenous = np.tile(coefficients[0], (len(u), 1))
    first = 0 if arx.include_current_input else 1
    for position, k in enumerate(range(first, first + arx.input_lags)):
        block = coefficients[1 + ny * na + nu * position : 1 + ny * na + nu * (position + 1)]
        exogenous[k:] += u[: len(u) - k] @ block
    work = y.copy()
    for t in range(lag, len(u)):
        value = exogenous[t].copy()
        for k in range(1, na + 1):
            value += work[t - k] @ ar[k - 1]
        work[t] = value
    rolling = np.full_like(segment.outputs, np.nan)
    rolling[lag:] = work[lag:] * model.output_scale + model.output_mean
    return one_step, rolling


def _split_for_cv(segments: Sequence[Segment], folds: int) -> list[tuple[list[Segment], list[Segment]]]:
    """把每个片段按时间切成 folds 个连续块；第 k 折留出所有片段的第 k 块。"""

    pieces = [np.array_split(np.arange(len(segment)), folds) for segment in segments]
    output = []
    for fold in range(folds):
        train: list[Segment] = []
        test: list[Segment] = []
        for segment, chunks in zip(segments, pieces):
            test.append(segment.take(chunks[fold]))
            before = np.concatenate(chunks[:fold]) if fold else np.array([], dtype=int)
            after = np.concatenate(chunks[fold + 1 :]) if fold + 1 < folds else np.array([], dtype=int)
            train.extend(segment.take(index) for index in (before, after) if len(index))
        output.append((train, test))
    return output


def _output_std(segments: Sequence[Segment]) -> np.ndarray:
    values = np.vstack([segment.outputs for segment in segments])
    return np.maximum(values.std(axis=0), 1.0e-12)


def select_hyperparameters(
    segments: Sequence[Segment], config: dict[str, Any], dt: float
) -> tuple[Hyper, list[dict[str, Any]]]:
    current = config["include_current_input"]
    candidates = [
        Hyper(max(1, round(history / dt)), output_lags, alpha)
        for history, output_lags, alpha in itertools.product(
            config["input_history_s"], config["output_lags"], config["ridge_alpha"]
        )
    ]
    splits = _split_for_cv(segments, config["cv_folds"])
    table: list[dict[str, Any]] = []
    for hyper in candidates:
        lag = max(hyper.input_lags, hyper.output_lags)
        fold_rmse: list[np.ndarray] = []
        stable = True
        for train, test in splits:
            model = fit_model(train, hyper, current, config["representative_jets"], config["outputs"])
            for segment in test:
                if len(segment) <= lag + 1:
                    continue
                _, rolling = predict(model, segment)
                error = rolling[lag:] - segment.outputs[lag:]
                if not np.all(np.isfinite(error)) or np.max(np.abs(error)) > 1.0e7:
                    stable = False
                    break
                fold_rmse.append(np.sqrt(np.mean(error**2, axis=0)))
            if not stable:
                break
        region_rmse = np.mean(fold_rmse, axis=0) if stable and fold_rmse else np.full(len(config["outputs"]), np.inf)
        table.append(
            {
                **hyper.as_dict(dt),
                "stable": stable,
                # 整车量单位不一（N 与 N·m），先除以训练标准差再平均，避免力矩主导选参；
                # 六区同为 N，保持原始 RMSE 平均。
                "cv_rolling_rmse_mean_N": float(
                    np.mean(region_rmse if config["output_set"] == "regions" else region_rmse / _output_std(segments))
                ),
                "cv_rolling_rmse_by_region_N": dict(zip(config["outputs"], map(float, region_rmse))),
            }
        )
    best = min(range(len(candidates)), key=lambda index: table[index]["cv_rolling_rmse_mean_N"])
    if not math.isfinite(table[best]["cv_rolling_rmse_mean_N"]):
        raise ROMDataError("所有超参数组合的滚动预测都发散")
    return candidates[best], table


# ---------------------------------------------------------------- 指标与诊断


def _evaluation_rows(segments: Sequence[Segment], predictions: Sequence[np.ndarray], lag: int) -> tuple[np.ndarray, np.ndarray]:
    truth = np.vstack([segment.outputs[lag:] for segment in segments if len(segment) > lag])
    guess = np.vstack([prediction[lag:] for segment, prediction in zip(segments, predictions) if len(segment) > lag])
    return truth, guess


def split_metrics(
    segments: Sequence[Segment],
    bundles: Sequence[tuple[np.ndarray, np.ndarray]],
    lag: int,
    names: Sequence[str] = REGION_FORCE_COLUMNS,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for index, mode in enumerate(("one_step", "rolling")):
        truth, guess = _evaluation_rows(segments, [bundle[index] for bundle in bundles], lag)
        computed, macro = _compute_region_metrics(truth, guess)
        # _compute_region_metrics 按列序以六区命名，这里换成实际输出名。
        by_region = {name: computed[region] for name, region in zip(names, REGION_FORCE_COLUMNS)}
        output[mode] = {"by_region": by_region, "macro_average": macro, "rows": int(len(truth))}
    return output


def _onset(signal: np.ndarray, threshold: float, count: int) -> int | None:
    above = np.abs(signal) >= threshold
    for index in range(0, len(above) - count + 1):
        if above[index : index + count].all():
            return index
    return None


def event_diagnostics(
    segments: Sequence[Segment],
    bundles: Sequence[tuple[np.ndarray, np.ndarray]],
    lag: int,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """逐事件比较真值与连续滚动预测的主响应区、方向和延迟。"""

    names = config.get("outputs", REGION_FORCE_COLUMNS)

    rows: list[dict[str, Any]] = []
    for segment, (_, rolling) in zip(segments, bundles):
        for event_id in dict.fromkeys(segment.event_id):
            members = np.flatnonzero(segment.event_id == event_id)
            start = float(segment.action_start[members[0]])
            pre = members[segment.phase[members] == "baseline"]
            window = members[
                (segment.time[members] > start) & (segment.time[members] <= start + config["response_horizon_s"])
            ]
            record: dict[str, Any] = {
                "case_id": segment.case_id,
                "event_id": str(event_id),
                "jet_id": str(segment.jet_id[members[0]]),
                "action_start_s": start,
            }
            if len(pre) < 2 or len(window) < config["onset_consecutive_samples"] or pre[0] < lag:
                record["evaluable"] = False
                record["note"] = "事件前基准落在滚动预测初始化段内或样本不足"
                rows.append(record)
                continue
            truth_rel = segment.outputs[window] - segment.outputs[pre].mean(axis=0)
            pred_rel = rolling[window] - rolling[pre].mean(axis=0)
            sigma = segment.outputs[pre].std(axis=0)
            truth_peak_index = np.argmax(np.abs(truth_rel), axis=0)
            pred_peak_index = np.argmax(np.abs(pred_rel), axis=0)
            truth_peak = truth_rel[truth_peak_index, range(6)]
            pred_peak = pred_rel[pred_peak_index, range(6)]
            dominant_truth = int(np.argmax(np.abs(truth_peak)))
            dominant_pred = int(np.argmax(np.abs(pred_peak)))
            region = dominant_truth
            threshold = max(
                config["onset_sigma_multiplier"] * float(sigma[region]),
                config["onset_peak_fraction"] * abs(float(truth_peak[region])),
            )
            count = config["onset_consecutive_samples"]
            truth_onset = _onset(truth_rel[:, region], threshold, count)
            pred_onset = _onset(pred_rel[:, region], threshold, count)
            times = segment.time[window]
            windows: dict[str, Any] = {}
            for lower, upper in config["response_windows_s"]:
                chosen = members[(segment.time[members] > start + lower) & (segment.time[members] <= start + upper)]
                if len(chosen) == 0:
                    continue
                truth_mean = segment.outputs[chosen].mean(axis=0) - segment.outputs[pre].mean(axis=0)
                pred_mean = rolling[chosen].mean(axis=0) - rolling[pre].mean(axis=0)
                truth_region = int(np.argmax(np.abs(truth_mean)))
                pred_region = int(np.argmax(np.abs(pred_mean)))
                windows[_window_label(lower, upper)] = {
                    "truth_dominant_region": names[truth_region],
                    "pred_dominant_region": names[pred_region],
                    "dominant_region_match": truth_region == pred_region,
                    "direction_match": bool(np.sign(truth_mean[truth_region]) == np.sign(pred_mean[truth_region])),
                    "truth_mean_by_region_N": dict(zip(names, map(float, truth_mean))),
                    "pred_mean_by_region_N": dict(zip(names, map(float, pred_mean))),
                }
            record["windows"] = windows
            record.update(
                {
                    "evaluable": True,
                    "truth_dominant_region": names[dominant_truth],
                    "pred_dominant_region": names[dominant_pred],
                    "dominant_region_match": dominant_truth == dominant_pred,
                    "truth_peak_N": float(truth_peak[region]),
                    "pred_peak_N": float(pred_peak[region]),
                    "direction_match": bool(np.sign(truth_peak[region]) == np.sign(pred_peak[region])),
                    "truth_delay_s": None if truth_onset is None else float(times[truth_onset] - start),
                    "pred_delay_s": None if pred_onset is None else float(times[pred_onset] - start),
                    "truth_peak_by_region_N": dict(zip(names, map(float, truth_peak))),
                    "pred_peak_by_region_N": dict(zip(names, map(float, pred_peak))),
                }
            )
            rows.append(record)
    return rows


def _window_label(lower: float, upper: float) -> str:
    return f"{lower * 1000:g}-{upper * 1000:g}ms"


def summarize_by_jet(events: Sequence[dict[str, Any]], jets: Sequence[int]) -> list[dict[str, Any]]:
    summary = []
    for position, jet in enumerate(jets):
        jet_id = f"J{jet:02d}"
        chosen = [row for row in events if row["jet_id"] == jet_id and row.get("evaluable")]
        if not chosen:
            summary.append({"jet_id": jet_id, "nominal_region": REGION_FORCE_COLUMNS[position], "evaluable_events": 0})
            continue
        truth_regions = [row["truth_dominant_region"] for row in chosen]
        delays = [
            (row["truth_delay_s"], row["pred_delay_s"])
            for row in chosen
            if row["truth_delay_s"] is not None and row["pred_delay_s"] is not None
        ]
        summary.append(
            {
                "jet_id": jet_id,
                "nominal_region": REGION_FORCE_COLUMNS[position],
                "evaluable_events": len(chosen),
                "truth_dominant_region": max(set(truth_regions), key=truth_regions.count),
                "dominant_region_match_rate": sum(row["dominant_region_match"] for row in chosen) / len(chosen),
                "direction_match_rate": sum(row["direction_match"] for row in chosen) / len(chosen),
                "median_truth_peak_N": median(row["truth_peak_N"] for row in chosen),
                "median_pred_peak_N": median(row["pred_peak_N"] for row in chosen),
                "median_truth_delay_s": median(pair[0] for pair in delays) if delays else None,
                "median_pred_delay_s": median(pair[1] for pair in delays) if delays else None,
                "median_abs_delay_error_s": median(abs(pair[1] - pair[0]) for pair in delays) if delays else None,
                "windows": _summarize_windows(chosen),
                "peak_by_output": _summarize_peaks(chosen),
            }
        )
    return summary


def _summarize_peaks(chosen: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """逐输出汇总喷气后 response_horizon 内的带符号峰值：真值、预测与方向一致率。"""

    output: dict[str, Any] = {}
    for name in chosen[0]["truth_peak_by_region_N"]:
        truth = [row["truth_peak_by_region_N"][name] for row in chosen]
        pred = [row["pred_peak_by_region_N"][name] for row in chosen]
        output[name] = {
            "median_truth_peak": median(truth),
            "median_pred_peak": median(pred),
            "direction_match_rate": sum(np.sign(a) == np.sign(b) for a, b in zip(truth, pred)) / len(truth),
        }
    return output


def _summarize_windows(chosen: Sequence[dict[str, Any]]) -> dict[str, Any]:
    labels = list(dict.fromkeys(label for row in chosen for label in row.get("windows", {})))
    output: dict[str, Any] = {}
    for label in labels:
        items = [row["windows"][label] for row in chosen if label in row.get("windows", {})]
        truth_regions = [item["truth_dominant_region"] for item in items]
        output[label] = {
            "events": len(items),
            "truth_dominant_region": max(set(truth_regions), key=truth_regions.count),
            "dominant_region_match_rate": sum(item["dominant_region_match"] for item in items) / len(items),
            "direction_match_rate": sum(item["direction_match"] for item in items) / len(items),
        }
    return output


def noise_floor(segments: Sequence[Segment], names: Sequence[str] = REGION_FORCE_COLUMNS) -> dict[str, float]:
    """喷气前差值噪声的中位数：ROM 无法解释的残差量级。

    六区直接取 B53 的去趋势基准噪声；其他输出 B53 未计算，按每个事件喷气前
    段差值的标准差取中位数。
    """

    values = np.vstack([segment.noise for segment in segments])
    result = np.median(values, axis=0)
    missing = ~np.isfinite(result)
    if missing.any():
        per_event = []
        for segment in segments:
            for event_id in dict.fromkeys(segment.event_id):
                pre = (segment.event_id == event_id) & (segment.phase == "baseline")
                if pre.sum() >= 2:
                    per_event.append(segment.outputs[pre].std(axis=0))
        if per_event:
            result = np.where(missing, np.median(per_event, axis=0), result)
    return dict(zip(names, map(float, result)))


# ---------------------------------------------------------------- 交付物


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_predictions_csv(
    path: Path,
    segments: Sequence[Segment],
    bundles: Sequence[tuple[np.ndarray, np.ndarray]],
    jets: Sequence[int],
    names: Sequence[str] = REGION_FORCE_COLUMNS,
) -> None:
    inputs = actual_massflow_columns(jets)
    fields = ["case_id", "segment_id", "physical_time", "event_id", "jet_id", "prediction_available", *inputs]
    for name in (f"delta_{item}" for item in names):
        fields.extend([f"{name}_truth", f"{name}_one_step", f"{name}_rolling"])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for segment, (one_step, rolling) in zip(segments, bundles):
            for row in range(len(segment)):
                values: list[Any] = [
                    segment.case_id,
                    segment.segment_id,
                    f"{segment.time[row]:.6f}",
                    segment.event_id[row],
                    segment.jet_id[row],
                    bool(np.isfinite(rolling[row, 0])),
                    *segment.inputs[row],
                ]
                for region in range(len(names)):
                    values.append(segment.outputs[row, region])
                    values.extend(
                        "" if not np.isfinite(item) else float(item)
                        for item in (one_step[row, region], rolling[row, region])
                    )
                writer.writerow(values)


def write_prediction_plot(
    path: Path,
    segments: Sequence[Segment],
    bundles: Sequence[tuple[np.ndarray, np.ndarray]],
    title: str,
    time_window: tuple[float, float] | None = None,
    names: Sequence[str] = REGION_FORCE_COLUMNS,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(len(names) + 1, 1, figsize=(14, 16), sharex=True, constrained_layout=True)
    for segment, (one_step, rolling) in zip(segments, bundles):
        mask = np.ones(len(segment), dtype=bool)
        if time_window is not None:
            mask = (segment.time >= time_window[0]) & (segment.time <= time_window[1])
        if not mask.any():
            continue
        t = segment.time[mask]
        axes[0].plot(t, segment.inputs[mask], linewidth=0.8)
        for region, axis in enumerate(axes[1:]):
            axis.plot(t, segment.outputs[mask, region], color="#222222", linewidth=0.9, label="truth")
            axis.plot(t, one_step[mask, region], color="#1971c2", linewidth=0.8, alpha=0.8, label="one-step")
            axis.plot(t, rolling[mask, region], color="#e03131", linewidth=0.9, alpha=0.85, label="rolling")
    axes[0].set_ylabel("actual massflow [kg/s]")
    axes[0].set_title(title)
    for region, axis in enumerate(axes[1:]):
        axis.axhline(0.0, color="#adb5bd", linewidth=0.6)
        name = names[region]
        label = f"ΔFz {short_name(name)}" if name in REGION_FORCE_COLUMNS else f"Δ{name}"
        axis.set_ylabel(f"{label} [{unit(name)}]")
        axis.grid(True, color="#e9ecef", linewidth=0.5)
    handles, labels = axes[1].get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    axes[1].legend(unique.values(), unique.keys(), loc="upper right", ncol=3)
    axes[-1].set_xlabel("physical_time [s]")
    figure.savefig(path, dpi=140)
    plt.close(figure)


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def write_result_markdown(path: Path, metrics: dict[str, Any]) -> None:
    names = metrics["data_contract"]["output_names"]
    regions = tuple(names) == REGION_FORCE_COLUMNS
    heads = " | ".join(short_name(name) for name in names)
    lines = [
        "# B04 第一版真实 ROM 结果" if regions else "# B04 整车量 ROM 结果（辅助模型）",
        "",
        f"- 运行状态：**{metrics['run_status']}**；工程验收：`{metrics['acceptance_status']}`",
        f"- 输入：{', '.join(metrics['data_contract']['input_columns'])}（STAR 实际质量流量）",
        (
            "- 输出：六区 ΔFz = Fz(t) − 同一时刻无喷气参考 Fz_ref(t)（B53 `reference_time_aligned`）"
            if regions
            else "- 输出：" + "、".join(LABELS.get(name, name) for name in names) + "，均为相对同一时刻无喷气参考的变化量"
        ),
        f"- 模型：ARX + 岭回归，有效步长 {metrics['model']['effective_dt_s']:.4g} s，"
        f"输入历史 {metrics['model']['selected']['input_history_s']:.3g} s，"
        f"输出滞后 {metrics['model']['selected']['output_lags']}，α={metrics['model']['selected']['ridge_alpha']:g}",
        "- 超参数只在训练集内部用分块交叉验证（连续滚动预测 RMSE）选取，验证集未参与。",
        "",
    ]
    floor = metrics["noise_floor_N"]["training"]
    lines += [
        "## 残差底噪",
        "",
        "喷气算例与参考算例从同一初始条件出发，喷气扰动后流场逐渐分岔，产生与输入无关的残差。"
        "下表是 B53 各事件喷气前差值噪声的中位数，可视为确定性 ROM 不可解释的误差量级。",
        "",
        "| 输出 | " + heads + " |",
        "|---|" + "---|" * len(names),
        "| 底噪 | " + " | ".join(f"{floor[name]:.0f} {unit(name)}" for name in names) + " |",
        "",
    ]
    for split in ("validation", "training"):
        block = metrics["metrics"].get(split)
        title = "独立验证" if split == "validation" else "训练集（仅拟合诊断，不代表泛化能力）"
        lines += [f"## {title}", ""]
        if not block:
            lines += ["**未执行**：验证数据集为空，不得据此给出任何泛化结论。", ""]
            continue
        lines += [
            "| 模式 | 指标 | " + heads + " |",
            "|---|---|" + "---|" * len(names),
        ]
        for mode, label in (("one_step", "一步"), ("rolling", "滚动")):
            by_region = block[mode]["by_region"]
            for key, metric_name in (("rmse", "RMSE" if regions else "RMSE（单位同底噪）"), ("nrmse", "NRMSE"), ("correlation", "相关系数")):
                lines.append(
                    f"| {label} | {metric_name} | "
                    + " | ".join(_fmt(by_region[name][key]) for name in names)
                    + " |"
                )
        if not regions:
            lines += [
                "",
                "逐喷口喷气后峰值（中位数；格式：真值 / 预测 / 方向一致率）：",
                "",
                "| 喷口 | 事件数 | " + heads + " |",
                "|---|---|" + "---|" * len(names),
            ]
            for row in metrics["response_by_jet"][split]:
                peaks = row.get("peak_by_output", {})
                cells = [
                    "—"
                    if name not in peaks
                    else f"{peaks[name]['median_truth_peak']:.4g} / {peaks[name]['median_pred_peak']:.4g} / {peaks[name]['direction_match_rate']:.2f}"
                    for name in names
                ]
                lines.append(f"| {row['jet_id']} | {row['evaluable_events']} | " + " | ".join(cells) + " |")
            lines.append("")
            continue
        lines += ["", "逐喷口响应（真值 vs 连续滚动预测）：", ""]
        lines += [
            "| 喷口 | 名义区域 | 事件数 | 真值主响应区 | 主响应区一致率 | 方向一致率 | 真值峰值 [N] | 预测峰值 [N] | 真值延迟 [s] | 预测延迟 [s] |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in metrics["response_by_jet"][split]:
            lines.append(
                "| {jet_id} | {nominal} | {count} | {region} | {match} | {direction} | {tpeak} | {ppeak} | {tdelay} | {pdelay} |".format(
                    jet_id=row["jet_id"],
                    nominal=row["nominal_region"][3:],
                    count=row["evaluable_events"],
                    region=_fmt(row.get("truth_dominant_region")),
                    match=_fmt(row.get("dominant_region_match_rate"), 2),
                    direction=_fmt(row.get("direction_match_rate"), 2),
                    tpeak=_fmt(row.get("median_truth_peak_N"), 4),
                    ppeak=_fmt(row.get("median_pred_peak_N"), 4),
                    tdelay=_fmt(row.get("median_truth_delay_s"), 2),
                    pdelay=_fmt(row.get("median_pred_delay_s"), 2),
                )
            )
        labels = list(dict.fromkeys(label for row in metrics["response_by_jet"][split] for label in row.get("windows", {})))
        if labels:
            lines += [
                "",
                "分时间窗口主响应区（窗口内平均 ΔFz；格式：真值主响应区 / 主响应区一致率 / 方向一致率）：",
                "",
                "| 喷口 | 名义区域 | " + " | ".join(labels) + " |",
                "|---|---|" + "---|" * len(labels),
            ]
            for row in metrics["response_by_jet"][split]:
                cells = []
                for label in labels:
                    item = row.get("windows", {}).get(label)
                    cells.append(
                        "—"
                        if not item
                        else f"{item['truth_dominant_region'][3:]} / {item['dominant_region_match_rate']:.2f} / {item['direction_match_rate']:.2f}"
                    )
                lines.append(f"| {row['jet_id']} | {row['nominal_region'][3:]} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += [
        "## 说明",
        "",
        "- 一步预测每步使用真实历史输出；连续滚动预测只用每个片段前 max_lag 行真实值初始化，之后完全反馈自身预测。",
        "- 主响应区/方向/延迟只在事件前基准不落在滚动初始化段内的事件上评价；延迟阈值取 max(kσ_pre, 比例×真值峰值)。",
        "- 单喷口响应随时间转移（先本区、再同段两侧、再相邻段），因此“主要响应区域”须按时间窗口判读；全程峰值表仅作参考。",
        "- 未设定 RMSE/相关性/延迟的自动通过阈值，`acceptance_status` 保持 `REVIEW_REQUIRED`，需人工判断。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- 主流程


def run(config: dict[str, Any]) -> dict[str, Any]:
    jets = config["representative_jets"]
    training_path = Path(config["training_dataset"])
    validation_path = Path(config["validation_dataset"])
    names = config["outputs"]
    training_raw = read_dataset(training_path, jets, names)
    validation_raw = read_dataset(validation_path, jets, names)
    if not training_raw:
        raise ROMDataError(f"训练数据集为空或不存在: {training_path}")
    timing = check_partitions(training_raw, validation_raw, config)
    stride = config["sample_stride"]
    dt = timing["raw_dt_s"] * stride
    training = subsample(training_raw, stride)
    validation = subsample(validation_raw, stride)

    hyper, cv_table = select_hyperparameters(training, config, dt)
    model = fit_model(training, hyper, config["include_current_input"], jets, names)
    lag = model.arx.max_lag
    training_bundles = [predict(model, segment) for segment in training]
    validation_bundles = [predict(model, segment) for segment in validation]

    out = Path(config["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    prefix = config["artifact_prefix"]
    paths = {
        "model": out / f"{prefix}_model.json",
        "metrics": out / f"{prefix}_metrics.json",
        "result": out / f"{prefix}_result.md",
        "training_csv": out / f"{prefix}_training_predictions.csv",
        "validation_csv": out / f"{prefix}_validation_predictions.csv",
        "training_plot": out / f"{prefix}_training_prediction.png",
        "validation_plot": out / f"{prefix}_validation_prediction.png",
        "training_zoom": out / f"{prefix}_training_prediction_zoom.png",
        "validation_zoom": out / f"{prefix}_validation_prediction_zoom.png",
    }

    validated = bool(validation)
    metrics_by_split: dict[str, Any] = {"training": split_metrics(training, training_bundles, lag, names)}
    events = {"training": event_diagnostics(training, training_bundles, lag, config)}
    if validated:
        metrics_by_split["validation"] = split_metrics(validation, validation_bundles, lag, names)
        events["validation"] = event_diagnostics(validation, validation_bundles, lag, config)

    def data_block(path: Path, segments: Sequence[Segment]) -> dict[str, Any]:
        return {
            "dataset": str(path),
            "dataset_sha256": _sha256(path) if path.is_file() else None,
            "case_ids": sorted({segment.case_id for segment in segments}),
            "segments": [
                {"segment_id": segment.segment_id, "rows": len(segment), "t_start": float(segment.time[0]), "t_end": float(segment.time[-1])}
                for segment in segments
            ],
            "model_rows": sum(len(segment) for segment in segments),
        }

    metrics = {
        "schema_version": "B04_real_ROM_from_B53_v1",
        "run_status": "COMPLETE" if validated else "TRAINING_ONLY",
        "acceptance_status": "REVIEW_REQUIRED" if validated else "NOT_EVALUATED",
        "validation_performed": validated,
        "data_contract": {
            "input_columns": list(actual_massflow_columns(jets)),
            "output_columns": [f"delta_{name}" for name in names],
            "output_names": list(names),
            "baseline": "B53 reference_time_aligned: delta = Fz(t) - Fz_nojet_reference(t)",
            "split_policy": "whole STAR cases; validation never used for scaling, CV or fitting",
        },
        "data": {"training": data_block(training_path, training), "validation": data_block(validation_path, validation)},
        "model": {
            "type": "ARX + ridge (training-standardised)",
            "raw_dt_s": timing["raw_dt_s"],
            "sample_stride": stride,
            "effective_dt_s": dt,
            "include_current_input": config["include_current_input"],
            "max_lag": lag,
            "selected": hyper.as_dict(dt),
            "cv_folds": config["cv_folds"],
            "cv_table": cv_table,
        },
        "noise_floor_N": {
            "training": noise_floor(training, names),
            **({"validation": noise_floor(validation, names)} if validated else {}),
        },
        "metrics": metrics_by_split,
        "response_by_jet": {split: summarize_by_jet(rows, jets) for split, rows in events.items()},
        "response_events": events,
        "config": config,
        "artifacts": {key: str(value) for key, value in paths.items()},
    }

    model_payload = model.to_dict()
    model_payload.update(
        {
            "baseline": metrics["data_contract"]["baseline"],
            "effective_dt_s": dt,
            "sample_stride": stride,
            "training_data": metrics["data"]["training"],
        }
    )
    _write_json(paths["model"], model_payload)
    write_predictions_csv(paths["training_csv"], training, training_bundles, jets, names)
    write_prediction_plot(paths["training_plot"], training, training_bundles, "Training fit (diagnostic only)", names=names)
    zoom_start = float(training[0].time[0]) + lag * dt
    write_prediction_plot(
        paths["training_zoom"],
        training,
        training_bundles,
        "Training fit (zoom)",
        (zoom_start, zoom_start + 1.6),
        names=names,
    )
    if validated:
        write_predictions_csv(paths["validation_csv"], validation, validation_bundles, jets, names)
        write_prediction_plot(paths["validation_plot"], validation, validation_bundles, "Independent validation", names=names)
        zoom_start = float(validation[0].time[0]) + lag * dt
        write_prediction_plot(
            paths["validation_zoom"],
            validation,
            validation_bundles,
            "Independent validation (zoom)",
            (zoom_start, zoom_start + 1.6),
            names=names,
        )
    else:
        for key in ("validation_csv", "validation_plot", "validation_zoom"):
            paths[key].unlink(missing_ok=True)
    write_result_markdown(paths["result"], metrics)
    _write_json(paths["metrics"], metrics)
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--output-dir")
    parser.add_argument("--require-validation", action="store_true", help="验证集为空时返回非零")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.output_dir:
        config["output_dir"] = str(Path(args.output_dir).resolve())
    try:
        metrics = run(config)
    except ROMDataError as exc:
        out = Path(config["output_dir"])
        _write_json(
            out / f"{prefix}_metrics.json",
            {"schema_version": "B04_real_ROM_from_B53_v1", "run_status": "BLOCKED", "issues": [str(exc)], "config": config},
        )
        (out / f"{prefix}_result.md").write_text(
            f"# B04 第一版真实 ROM 结果\n\n**BLOCKED**：{exc}\n", encoding="utf-8"
        )
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    summary = {
        "run_status": metrics["run_status"],
        "selected": metrics["model"]["selected"],
        "training_rolling_macro": metrics["metrics"]["training"]["rolling"]["macro_average"],
        "validation_rolling_macro": (
            metrics["metrics"]["validation"]["rolling"]["macro_average"] if metrics["validation_performed"] else None
        ),
    }
    print(json.dumps(_json_safe(summary), ensure_ascii=False, indent=2))
    return 1 if args.require_validation and not metrics["validation_performed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
