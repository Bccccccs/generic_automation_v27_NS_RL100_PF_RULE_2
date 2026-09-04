"""B53：从真实 STAR 标准 Case 包构建可追溯训练/验证数据。

输入约定沿用 ``flow_control.star_ingest`` 的标准 Case 包：

* ``actuation_schedule.csv`` 是窗口级动作表；
* ``processed/timeseries.csv`` 是每个物理时间步的 STAR 结果；
* 样本归属哪个动作窗口由 ``case_manifest.yaml`` 的 ``sample_ownership_rule``
  声明：``left_closed`` 表示 ``[t_start, t_end)``，``right_closed`` 表示
  ``(t_start, t_end]``。未声明的历史 CLI Case 保持 ``right_closed`` 兼容并在
  质量报告中标为 legacy default；不得根据行数推断语义。

整个动作事件是最小放行单元。时间错位、实际质量流量缺失、喷气前基准漂移或
六区响应均低于基准噪声时，该事件的所有样本都会被原子剔除。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence

import yaml

from flow_control.sampling import (
    SAMPLE_OWNERSHIP_EMBEDDED,
    SAMPLE_OWNERSHIP_LEFT_CLOSED,
    SAMPLE_OWNERSHIP_RIGHT_CLOSED,
    ScheduleWindowError,
    actuation_time_value,
    locate_schedule_window,
    resolve_declared_ownership,
    validate_embedded_window,
)

N_JETS = 24
REGION_COLUMNS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
REGION_ALIASES = {
    "Fz_S1L": ("Fz_S1L", "underbody_lift_s1l"),
    "Fz_S1R": ("Fz_S1R", "underbody_lift_s1r"),
    "Fz_S2L": ("Fz_S2L", "underbody_lift_s2l"),
    "Fz_S2R": ("Fz_S2R", "underbody_lift_s2r"),
    "Fz_S3L": ("Fz_S3L", "underbody_lift_s3l"),
    "Fz_S3R": ("Fz_S3R", "underbody_lift_s3r"),
}
JET_COLUMNS = tuple(f"JET_{index:02d}" for index in range(1, N_JETS + 1))
COMMAND_COLUMNS = tuple(f"cmd_massflow_{index:02d}" for index in range(1, N_JETS + 1))
ACTUAL_COLUMNS = tuple(f"actual_massflow_{index:02d}" for index in range(1, N_JETS + 1))

TRACE_FIELDS = (
    "sample_id",
    "split",
    "source_case_id",
    "source_case_dir",
    "source_raw_star_dir",
    "source_timeseries_csv",
    "source_schedule_csv",
    "source_timeseries_row",
    "source_schedule_row",
    "source_git_commit",
    "source_star_sim_file",
    "source_star_sim_hash_sha256",
    "physical_time",
    "action_window_id",
    "event_id",
    "jet_id",
    "phase",
    "time_from_action_s",
    "action_start_s",
    "action_end_s",
    "actual_massflow_onset_s",
    "actual_massflow_off_s",
    "initial_transient_cutoff_s",
    "quality_status",
)
FORCE_FIELDS = tuple(
    name
    for region in REGION_COLUMNS
    for name in (
        region,
        f"baseline_mean_{region}",
        f"baseline_noise_{region}",
        f"delta_{region}",
    )
)
DATASET_FIELDS = (*TRACE_FIELDS, *JET_COLUMNS, *COMMAND_COLUMNS, *ACTUAL_COLUMNS, *FORCE_FIELDS)

QUALITY_FIELDS = (
    "source_case_id",
    "source_case_dir",
    "requested_split",
    "assigned_split",
    "status",
    "training_ready",
    "reason_codes",
    "reason_detail",
    "timeseries_rows",
    "schedule_rows",
    "aligned_rows",
    "time_alignment_mode",
    "time_alignment_mismatch_rows",
    "max_time_alignment_error_s",
    "initial_transient_cutoff_s",
    "initial_transient_removed_rows",
    "candidate_windows",
    "accepted_windows",
    "rejected_windows",
    "training_samples",
    "validation_samples",
    "actual_massflow_columns_present",
    "six_region_force_columns_present",
    "source_timeseries_csv",
    "source_schedule_csv",
)

_RESPONSE_BASE_FIELDS = (
    "jet_id",
    "representative_region",
    "status",
    "accepted_window_count",
    "rejected_window_count",
    "dominant_region",
    "median_response_delay_s",
    "median_peak_delta_N",
    "median_recovery_time_s",
    "median_snr_linear",
    "median_snr_db",
    "source_case_ids",
    "notes",
)
_RESPONSE_REGION_FIELDS = tuple(
    name
    for region in REGION_COLUMNS
    for name in (
        f"{region}_median_peak_delta_N",
        f"{region}_median_response_delay_s",
        f"{region}_median_recovery_time_s",
        f"{region}_median_snr_linear",
        f"{region}_median_snr_db",
    )
)
RESPONSE_FIELDS = (*_RESPONSE_BASE_FIELDS, *_RESPONSE_REGION_FIELDS)

ABNORMAL_FIELDS = (
    "scope",
    "source_case_id",
    "source_case_dir",
    "assigned_split",
    "event_id",
    "action_window_id",
    "jet_id",
    "action_start_s",
    "action_end_s",
    "window_status",
    "severity",
    "reason_codes",
    "reason_detail",
    "baseline_start_s",
    "baseline_end_s",
    "actual_massflow_column",
    "missing_value_count",
    "max_time_alignment_error_s",
    "max_baseline_drift_ratio",
    "max_region_snr_linear",
    "recovery_observed_all_regions",
    "region_metrics_json",
)


@dataclass(frozen=True)
class CaseSource:
    """一个标准 Case 目录及其数据集角色。"""

    case_dir: Path | str
    split: str = "auto"

    def normalized(self) -> "CaseSource":
        split = self.split.lower().strip()
        aliases = {"train": "training", "val": "validation", "valid": "validation"}
        split = aliases.get(split, split)
        if split not in {"auto", "training", "validation"}:
            raise ValueError(f"不支持的数据集角色: {self.split}")
        return CaseSource(Path(self.case_dir), split)


@dataclass(frozen=True)
class B53Config:
    """B53 的可审计阈值；时间参数均以秒为单位。"""

    representative_jets: tuple[int, ...] = (2, 3, 9, 11, 17, 19)
    sources: tuple[CaseSource, ...] = ()
    baseline_duration_s: float = 0.10
    minimum_initial_discard_s: float = 0.05
    recovery_search_s: float = 0.20
    minimum_baseline_samples: int = 20
    minimum_response_samples: int = 5
    time_alignment_tolerance_s: float = 1.0e-8
    baseline_drift_noise_multiplier: float = 4.0
    baseline_drift_relative_limit: float = 0.03
    response_sigma_threshold: float = 3.0
    response_absolute_floor_n: float = 1.0e-9
    response_hold_s: float = 0.0005
    recovery_noise_multiplier: float = 3.0
    recovery_peak_fraction: float = 0.10
    recovery_hold_s: float = 0.01
    minimum_snr_linear: float = 3.0
    massflow_on_threshold_kg_s: float = 1.0e-8
    max_massflow_lag_s: float = 0.005
    validation_fraction: float = 0.20
    split_seed: int = 20260823

    @classmethod
    def default(cls) -> "B53Config":
        return cls()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "B53Config":
        data = dict(raw.get("b53", raw))
        source_rows = data.pop("sources", []) or []
        sources = tuple(
            CaseSource(row["case_dir"], row.get("split", "auto"))
            if isinstance(row, Mapping)
            else CaseSource(row)
            for row in source_rows
        )
        if "representative_jets" in data:
            data["representative_jets"] = tuple(int(value) for value in data["representative_jets"])
        config = cls(sources=sources, **data)
        config.require_valid()
        return config

    def require_valid(self) -> None:
        if len(self.representative_jets) != 6 or len(set(self.representative_jets)) != 6:
            raise ValueError("representative_jets 必须包含 6 个不重复喷口")
        if any(jet < 1 or jet > N_JETS for jet in self.representative_jets):
            raise ValueError("代表喷口编号必须在 1..24")
        positive = (
            self.baseline_duration_s,
            self.recovery_search_s,
            self.time_alignment_tolerance_s,
            self.response_hold_s,
            self.recovery_hold_s,
            self.baseline_drift_noise_multiplier,
            self.response_sigma_threshold,
            self.minimum_snr_linear,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("B53 的时长和倍率参数必须为正")
        if self.minimum_initial_discard_s < 0 or self.max_massflow_lag_s < 0:
            raise ValueError("启动剔除和质量流量延迟阈值不得为负")
        if self.minimum_baseline_samples < 2 or self.minimum_response_samples < 1:
            raise ValueError("样本数下限无效")
        if not 0.0 <= self.validation_fraction < 1.0:
            raise ValueError("validation_fraction 必须在 [0, 1) 内")
        for source in self.sources:
            source.normalized()


@dataclass
class _ScheduleInterval:
    start: float
    end: float
    window_id: str
    row_index: int
    row: dict[str, str]


@dataclass
class _Event:
    jet_indices: tuple[int, ...]
    start: float
    end: float
    window_ids: tuple[str, ...]
    schedule_indices: list[int]
    event_id: str = ""

    @property
    def jet_id(self) -> str:
        if len(self.jet_indices) == 1:
            return f"J{self.jet_indices[0]:02d}"
        return "+".join(f"J{value:02d}" for value in self.jet_indices)

    @property
    def action_window_id(self) -> str:
        return "|".join(self.window_ids)


@dataclass
class _AlignedRow:
    source_index: int
    time: float
    schedule_index: int
    row: dict[str, str]


@dataclass
class _RegionMetric:
    baseline_mean: float
    baseline_noise: float
    baseline_drift: float
    baseline_drift_threshold: float
    response_delay_s: float | None
    peak_delta: float
    peak_time_s: float
    recovery_time_s: float | None
    snr_linear: float
    snr_db: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_mean": self.baseline_mean,
            "baseline_noise": self.baseline_noise,
            "baseline_drift": self.baseline_drift,
            "baseline_drift_threshold": self.baseline_drift_threshold,
            "response_delay_s": self.response_delay_s,
            "peak_delta": self.peak_delta,
            "peak_time_s": self.peak_time_s,
            "recovery_time_s": self.recovery_time_s,
            "snr_linear": self.snr_linear,
            "snr_db": self.snr_db,
        }


@dataclass
class _AcceptedWindow:
    event: _Event
    split: str
    dataset_rows: list[dict[str, Any]]
    metrics: dict[str, _RegionMetric]


@dataclass
class _CaseResult:
    quality: dict[str, Any]
    accepted: list[_AcceptedWindow] = field(default_factory=list)
    anomalies: list[dict[str, Any]] = field(default_factory=list)


def load_b53_config(path: str | Path) -> B53Config:
    with Path(path).open(encoding="utf-8") as handle:
        return B53Config.from_mapping(yaml.safe_load(handle) or {})


def discover_case_dirs(root: str | Path) -> list[Path]:
    """递归发现标准 Case 包，但不会由默认配置自动扫描任何历史目录。"""

    root_path = Path(root)
    if not root_path.exists():
        return []
    candidates: set[Path] = set()
    for timeseries in root_path.rglob("timeseries.csv"):
        if timeseries.parent.name == "processed":
            case_dir = timeseries.parent.parent
        else:
            case_dir = timeseries.parent
        if _schedule_path(case_dir) is not None:
            candidates.add(case_dir)
    return sorted(candidates, key=lambda path: path.as_posix())


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 使用标准 UTF-8，避免 BOM 被严格 CSV/ML 读取器误当成首列名的一部分。
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field, "")) for field in fields})


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _path_text(path: Path) -> str:
    return path.resolve().as_posix()


def _timeseries_path(case_dir: Path) -> Path | None:
    for candidate in (case_dir / "processed" / "timeseries.csv", case_dir / "timeseries.csv"):
        if candidate.is_file():
            return candidate
    return None


def _schedule_path(case_dir: Path) -> Path | None:
    for candidate in (case_dir / "actuation_schedule.csv", case_dir / "input" / "actuation_schedule.csv"):
        if candidate.is_file():
            return candidate
    return None


def _manifest(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "case_manifest.yaml"
    if not path.exists():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _manifest_star_value(manifest: Mapping[str, Any], name: str) -> str:
    star = manifest.get("star", {})
    aliases = {
        "sim_file": ("sim_file", "sim_file_identifier"),
        "sim_file_hash_sha256": ("sim_file_hash_sha256", "sim_hash_sha256"),
    }.get(name, (name,))
    for key in aliases:
        if isinstance(star, Mapping) and star.get(key) is not None:
            return str(star[key])
        if manifest.get(key) is not None:
            return str(manifest[key])
    return ""


def _case_id(case_dir: Path, manifest: Mapping[str, Any]) -> str:
    return str(manifest.get("case_id") or case_dir.name)


def _assign_split(source: CaseSource, case_id: str, manifest: Mapping[str, Any], config: B53Config) -> str:
    normalized = source.normalized().split
    if normalized != "auto":
        return normalized
    for key in ("dataset_split", "data_split", "split"):
        value = str(manifest.get(key, "")).lower().strip()
        if value in {"train", "training"}:
            return "training"
        if value in {"val", "valid", "validation"}:
            return "validation"
    lowered = f"{case_id} {Path(source.case_dir).name}".lower()
    if "validation" in lowered or re.search(r"(^|[_-])val([_-]|$)", lowered):
        return "validation"
    if "training" in lowered or re.search(r"(^|[_-])train([_-]|$)", lowered):
        return "training"
    digest = hashlib.sha256(f"{config.split_seed}:{case_id}".encode()).digest()
    ratio = int.from_bytes(digest[:8], "big") / float(2**64)
    return "validation" if ratio < config.validation_fraction else "training"


def _resolve_regions(headers: Sequence[str]) -> dict[str, str]:
    available = set(headers)
    return {
        canonical: next((alias for alias in aliases if alias in available), "")
        for canonical, aliases in REGION_ALIASES.items()
    }


def _parse_schedule(rows: list[dict[str, str]], tolerance: float) -> tuple[list[_ScheduleInterval], list[str]]:
    intervals: list[_ScheduleInterval] = []
    errors: list[str] = []
    previous_end: float | None = None
    for index, row in enumerate(rows):
        start = _number(row.get("t_start", actuation_time_value(row)))
        end = _number(row.get("t_end"))
        if start is None:
            errors.append(f"第 {index + 2} 行缺少数值 t_start/time")
            continue
        if end is None:
            if index + 1 < len(rows):
                end = _number(
                    rows[index + 1].get(
                        "t_start", actuation_time_value(rows[index + 1])
                    )
                )
            if end is None and previous_end is not None:
                end = start + max(start - (intervals[-1].start if intervals else 0.0), tolerance)
        if end is None or end <= start:
            errors.append(f"第 {index + 2} 行动作区间无效")
            continue
        if previous_end is not None:
            if start < previous_end - tolerance:
                errors.append(f"第 {index + 2} 行动作区间与前一行重叠")
            elif start > previous_end + tolerance:
                errors.append(f"第 {index + 2} 行动作区间与前一行存在空洞")
        raw_window = row.get("window_id")
        if raw_window in (None, ""):
            # 不得用行下标伪造 window_id：embedded 模式会拿它去校验 timeseries 的
            # window_id，伪造值会形成自证一致而静默放行错误对齐
            errors.append(f"第 {index + 2} 行缺少 window_id，无法确定动作窗口归属")
            continue
        interval = _ScheduleInterval(
            start=start,
            end=end,
            window_id=str(raw_window),
            row_index=index,
            row=row,
        )
        intervals.append(interval)
        previous_end = end
    return intervals, errors


def _active_jets(row: Mapping[str, Any], threshold: float) -> tuple[int, ...]:
    result = []
    for jet in range(1, N_JETS + 1):
        switch = _number(row.get(f"JET_{jet:02d}")) or 0.0
        command = abs(_number(row.get(f"cmd_massflow_{jet:02d}")) or 0.0)
        if switch > 0.5 or command > threshold:
            result.append(jet)
    return tuple(result)


def _events(intervals: list[_ScheduleInterval], threshold: float, case_id: str) -> list[_Event]:
    output: list[_Event] = []
    current: _Event | None = None
    for schedule_index, interval in enumerate(intervals):
        jets = _active_jets(interval.row, threshold)
        if not jets:
            current = None
            continue
        can_merge = (
            current is not None
            and current.jet_indices == jets
            and current.window_ids[-1] == interval.window_id
            and math.isclose(current.end, interval.start, abs_tol=1.0e-8)
        )
        if can_merge:
            current.end = interval.end
            current.schedule_indices.append(schedule_index)
            continue
        current = _Event(
            jet_indices=jets,
            start=interval.start,
            end=interval.end,
            window_ids=(interval.window_id,),
            schedule_indices=[schedule_index],
        )
        jet_label = current.jet_id.replace("+", "-") or "NOJET"
        current.event_id = f"{case_id}__w{interval.window_id}__{jet_label}__{interval.start:.9f}"
        output.append(current)
    return output


def _align_rows(
    rows: list[dict[str, str]],
    intervals: list[_ScheduleInterval],
    tolerance: float,
    ownership: str,
) -> tuple[list[_AlignedRow], list[int], float, list[str]]:
    errors: list[str] = []
    times: list[float] = []
    for index, row in enumerate(rows):
        value = _number(row.get("physical_time"))
        if value is None:
            errors.append(f"timeseries 第 {index + 2} 行 physical_time 缺失或非数值")
            continue
        if times and value <= times[-1]:
            errors.append(f"timeseries 第 {index + 2} 行 physical_time 非严格递增")
        times.append(value)
    if errors or len(times) != len(rows) or not intervals:
        return [], list(range(len(rows))), math.inf, errors
    starts = [interval.start for interval in intervals]
    ends = [interval.end for interval in intervals]
    embedded = ownership == SAMPLE_OWNERSHIP_EMBEDDED
    embedded_spans: dict[int, tuple[float, float]] = {}
    embedded_first: dict[int, int] = {}
    if embedded:
        for position, interval in enumerate(intervals):
            try:
                key = int(float(str(interval.window_id)))
            except (TypeError, ValueError):
                continue
            current = embedded_spans.get(key)
            embedded_spans[key] = (
                (interval.start, interval.end)
                if current is None
                else (min(current[0], interval.start), max(current[1], interval.end))
            )
            embedded_first.setdefault(key, position)
    aligned: list[_AlignedRow] = []
    mismatches: list[int] = []
    max_error = 0.0
    for index, (time_value, row) in enumerate(zip(times, rows)):
        if embedded:
            # window_id 是数据自带的声明，只校验它与样本时间是否矛盾；
            # 误差按整个窗口跨度算，一个 window_id 通常跨多行采样
            raw_window = row.get("window_id")
            try:
                span_start, span_end = validate_embedded_window(
                    embedded_spans, raw_window, time_value, tolerance_s=tolerance
                )
            except ScheduleWindowError:
                mismatches.append(index)
                continue
            key = int(float(str(raw_window)))
            max_error = max(
                max_error, max(span_start - time_value, time_value - span_end, 0.0)
            )
            aligned.append(_AlignedRow(index, time_value, embedded_first[key], row))
            continue
        try:
            schedule_index = locate_schedule_window(
                starts, ends, time_value, ownership=ownership, clamp_tolerance_s=tolerance
            )
        except ScheduleWindowError:
            mismatches.append(index)
            continue
        interval = intervals[schedule_index]
        error = max(interval.start - time_value, time_value - interval.end, 0.0)
        max_error = max(max_error, error)
        if error > tolerance:
            mismatches.append(index)
            continue
        observed_window = row.get("window_id")
        if observed_window not in (None, "") and str(observed_window) != interval.window_id:
            try:
                same_window = int(float(str(observed_window))) == int(float(interval.window_id))
            except ValueError:
                same_window = False
            if not same_window:
                mismatches.append(index)
                continue
        aligned.append(_AlignedRow(index, time_value, schedule_index, row))
    return aligned, mismatches, max_error, errors


def _sample_dt(aligned: Sequence[_AlignedRow]) -> float:
    differences = [aligned[index].time - aligned[index - 1].time for index in range(1, len(aligned))]
    return median(differences) if differences else math.nan


def _mean_or_blank(values: Sequence[float | None]) -> float | str:
    numeric = [value for value in values if value is not None]
    return median(numeric) if numeric else ""


def _first_sustained(
    values: Sequence[float], threshold: float, start: int, stop: int, count: int
) -> int | None:
    stop = min(stop, len(values))
    if count < 1 or stop - start < count:
        return None
    for index in range(start, stop - count + 1):
        if all(abs(values[position]) >= threshold for position in range(index, index + count)):
            return index
    return None


def _first_recovery(values: Sequence[float], start: int, band: float, count: int) -> int | None:
    if count < 1 or len(values) - start < count:
        return None
    for index in range(start, len(values) - count + 1):
        if all(abs(values[position]) <= band for position in range(index, index + count)):
            return index + count - 1
    return None


def _baseline_drift(values: Sequence[float], config: B53Config) -> tuple[float, float, float, float]:
    count = max(1, len(values) // 4)
    drift = fmean(values[-count:]) - fmean(values[:count])
    mean_value = median(values)
    # 先移除一阶线性趋势再估计基准噪声，避免把持续漂移本身计入噪声、从而
    # 人为放宽漂移阈值。这里保留真实漂移用于门禁，不用去趋势值替代原始力。
    if len(values) > 1:
        level_mean = fmean(values)
        center = (len(values) - 1) / 2.0
        denominator = sum((index - center) ** 2 for index in range(len(values)))
        slope = (
            sum((index - center) * (value - level_mean) for index, value in enumerate(values))
            / denominator
            if denominator
            else 0.0
        )
        residuals = [
            value - (level_mean + slope * (index - center))
            for index, value in enumerate(values)
        ]
        noise = pstdev(residuals)
    else:
        noise = 0.0
    threshold = max(
        config.baseline_drift_noise_multiplier * noise,
        config.baseline_drift_relative_limit * max(abs(mean_value), 1.0),
        config.response_absolute_floor_n,
    )
    return mean_value, noise, drift, threshold


def _region_metric(
    baseline_values: Sequence[float],
    response_values: Sequence[float],
    response_times: Sequence[float],
    onset_position: int,
    off_position: int,
    dt: float,
    event: _Event,
    config: B53Config,
) -> _RegionMetric:
    mean_value, noise, drift, drift_threshold = _baseline_drift(baseline_values, config)
    delta = [value - mean_value for value in response_values]
    peak_position = max(range(len(delta)), key=lambda index: abs(delta[index]))
    peak_delta = delta[peak_position]
    hold = max(1, math.ceil(config.response_hold_s / dt))
    threshold = max(config.response_sigma_threshold * noise, config.response_absolute_floor_n)
    onset = _first_sustained(delta, threshold, onset_position, max(onset_position + 1, off_position + 1), hold)
    on_delta = delta[onset_position : max(onset_position + 1, off_position + 1)]
    signal_rms = math.sqrt(fmean(value * value for value in on_delta)) if on_delta else 0.0
    if noise <= config.response_absolute_floor_n:
        snr_linear = math.inf if signal_rms > config.response_absolute_floor_n else 0.0
    else:
        snr_linear = signal_rms / noise
    snr_db = 20.0 * math.log10(snr_linear) if snr_linear > 0 and math.isfinite(snr_linear) else snr_linear
    recovery_band = max(
        config.recovery_noise_multiplier * noise,
        config.recovery_peak_fraction * abs(peak_delta),
        config.response_absolute_floor_n,
    )
    recovery_hold = max(1, math.ceil(config.recovery_hold_s / dt))
    recovery = _first_recovery(delta, off_position + 1, recovery_band, recovery_hold)
    return _RegionMetric(
        baseline_mean=mean_value,
        baseline_noise=noise,
        baseline_drift=drift,
        baseline_drift_threshold=drift_threshold,
        response_delay_s=None if onset is None else response_times[onset] - response_times[onset_position],
        peak_delta=peak_delta,
        peak_time_s=response_times[peak_position] - event.start,
        recovery_time_s=None if recovery is None else response_times[recovery] - event.end,
        snr_linear=snr_linear,
        snr_db=snr_db,
    )


def _alignment_mode_label(ownership: str, source: str) -> str:
    interval = {
        SAMPLE_OWNERSHIP_LEFT_CLOSED: "left_closed_[t_start,t_end)",
        SAMPLE_OWNERSHIP_RIGHT_CLOSED: "right_closed_(t_start,t_end]",
        SAMPLE_OWNERSHIP_EMBEDDED: "embedded_window_id",
    }.get(ownership, "undeclared")
    return f"{interval}_legacy_default" if source == "legacy_default" else interval


def _empty_quality(reason: str = "NO_FORMAL_STAR_DATA") -> dict[str, Any]:
    row = {field: "" for field in QUALITY_FIELDS}
    row.update(
        {
            "status": "NO_DATA",
            "training_ready": False,
            "reason_codes": reason,
            "reason_detail": "当前配置未提供正式 STAR Case；仅生成可直接复用的标准表头",
            "timeseries_rows": 0,
            "schedule_rows": 0,
            "aligned_rows": 0,
            "time_alignment_mode": "undeclared",
            "time_alignment_mismatch_rows": 0,
            "candidate_windows": 0,
            "accepted_windows": 0,
            "rejected_windows": 0,
            "training_samples": 0,
            "validation_samples": 0,
            "actual_massflow_columns_present": 0,
            "six_region_force_columns_present": 0,
        }
    )
    return row


def _anomaly(
    *,
    case_id: str,
    case_dir: Path,
    split: str,
    reasons: Sequence[str],
    detail: str,
    event: _Event | None = None,
    severity: str = "ERROR",
    status: str = "REJECTED",
    **metrics: Any,
) -> dict[str, Any]:
    row = {field: "" for field in ABNORMAL_FIELDS}
    row.update(
        {
            "scope": "window" if event is not None else "case",
            "source_case_id": case_id,
            "source_case_dir": _path_text(case_dir),
            "assigned_split": split,
            "event_id": event.event_id if event else "",
            "action_window_id": event.action_window_id if event else "",
            "jet_id": event.jet_id if event else "",
            "action_start_s": event.start if event else "",
            "action_end_s": event.end if event else "",
            "window_status": status,
            "severity": severity,
            "reason_codes": "|".join(dict.fromkeys(reasons)),
            "reason_detail": detail,
        }
    )
    row.update(metrics)
    return row


def _fatal_case_result(
    *,
    case_id: str,
    case_dir: Path,
    source: CaseSource,
    split: str,
    reason: str,
    detail: str,
    timeseries_path: Path | None,
    schedule_path: Path | None,
    timeseries_rows: int = 0,
    schedule_rows: int = 0,
    aligned_rows: int = 0,
    mismatch_rows: int = 0,
    max_error: float | str = "",
    actual_present: int = 0,
    force_present: int = 0,
    candidate_windows: int = 0,
) -> _CaseResult:
    alignment_mode = _alignment_mode_label(*resolve_declared_ownership(_manifest(case_dir)))
    quality = {field: "" for field in QUALITY_FIELDS}
    quality.update(
        {
            "source_case_id": case_id,
            "source_case_dir": _path_text(case_dir),
            "requested_split": source.split,
            "assigned_split": split,
            "status": "REJECTED",
            "training_ready": False,
            "reason_codes": reason,
            "reason_detail": detail,
            "timeseries_rows": timeseries_rows,
            "schedule_rows": schedule_rows,
            "aligned_rows": aligned_rows,
            "time_alignment_mode": alignment_mode,
            "time_alignment_mismatch_rows": mismatch_rows,
            "max_time_alignment_error_s": max_error,
            "candidate_windows": candidate_windows,
            "accepted_windows": 0,
            "rejected_windows": candidate_windows,
            "training_samples": 0,
            "validation_samples": 0,
            "actual_massflow_columns_present": actual_present,
            "six_region_force_columns_present": force_present,
            "source_timeseries_csv": _path_text(timeseries_path) if timeseries_path else "",
            "source_schedule_csv": _path_text(schedule_path) if schedule_path else "",
        }
    )
    anomaly = _anomaly(
        case_id=case_id,
        case_dir=case_dir,
        split=split,
        reasons=[reason],
        detail=detail,
        missing_value_count="",
        max_time_alignment_error_s=max_error,
    )
    return _CaseResult(quality=quality, anomalies=[anomaly])


def _process_case(source: CaseSource, config: B53Config) -> _CaseResult:
    source = source.normalized()
    case_dir = Path(source.case_dir)
    manifest = _manifest(case_dir)
    case_id = _case_id(case_dir, manifest)
    split = _assign_split(source, case_id, manifest, config)
    ownership, ownership_source = resolve_declared_ownership(manifest)
    alignment_mode = _alignment_mode_label(ownership, ownership_source)
    timeseries_path = _timeseries_path(case_dir)
    schedule_path = _schedule_path(case_dir)
    if timeseries_path is None or schedule_path is None:
        return _fatal_case_result(
            case_id=case_id,
            case_dir=case_dir,
            source=source,
            split=split,
            reason="REQUIRED_FILE_MISSING",
            detail="需要 actuation_schedule.csv 和 processed/timeseries.csv",
            timeseries_path=timeseries_path,
            schedule_path=schedule_path,
        )
    time_headers, timeseries_rows = _read_csv(timeseries_path)
    _, schedule_rows = _read_csv(schedule_path)
    region_mapping = _resolve_regions(time_headers)
    actual_present = sum(column in time_headers for column in ACTUAL_COLUMNS)
    force_present = sum(bool(value) for value in region_mapping.values())
    intervals, schedule_errors = _parse_schedule(schedule_rows, config.time_alignment_tolerance_s)
    events = _events(intervals, config.massflow_on_threshold_kg_s, case_id)
    if schedule_errors:
        return _fatal_case_result(
            case_id=case_id,
            case_dir=case_dir,
            source=source,
            split=split,
            reason="TIME_MISALIGNMENT",
            detail="; ".join(schedule_errors),
            timeseries_path=timeseries_path,
            schedule_path=schedule_path,
            timeseries_rows=len(timeseries_rows),
            schedule_rows=len(schedule_rows),
            actual_present=actual_present,
            force_present=force_present,
            candidate_windows=len(events),
        )
    aligned, mismatch_rows, max_error, time_errors = _align_rows(
        timeseries_rows, intervals, config.time_alignment_tolerance_s, ownership
    )
    if time_errors or mismatch_rows or len(aligned) != len(timeseries_rows):
        detail_parts = list(time_errors)
        if mismatch_rows:
            detail_parts.append(f"{len(mismatch_rows)} 行无法按 {alignment_mode} 与 window_id 一致对齐")
        return _fatal_case_result(
            case_id=case_id,
            case_dir=case_dir,
            source=source,
            split=split,
            reason="TIME_MISALIGNMENT",
            detail="; ".join(detail_parts) or "physical_time 对齐失败",
            timeseries_path=timeseries_path,
            schedule_path=schedule_path,
            timeseries_rows=len(timeseries_rows),
            schedule_rows=len(schedule_rows),
            aligned_rows=len(aligned),
            mismatch_rows=len(mismatch_rows),
            max_error=max_error,
            actual_present=actual_present,
            force_present=force_present,
            candidate_windows=len(events),
        )
    if actual_present < len(ACTUAL_COLUMNS):
        missing = [column for column in ACTUAL_COLUMNS if column not in time_headers]
        return _fatal_case_result(
            case_id=case_id,
            case_dir=case_dir,
            source=source,
            split=split,
            reason="ACTUAL_MASSFLOW_MISSING",
            detail=f"缺少实际质量流量列: {', '.join(missing)}",
            timeseries_path=timeseries_path,
            schedule_path=schedule_path,
            timeseries_rows=len(timeseries_rows),
            schedule_rows=len(schedule_rows),
            aligned_rows=len(aligned),
            max_error=max_error,
            actual_present=actual_present,
            force_present=force_present,
            candidate_windows=len(events),
        )
    if force_present < len(REGION_COLUMNS):
        missing = [region for region, source_name in region_mapping.items() if not source_name]
        return _fatal_case_result(
            case_id=case_id,
            case_dir=case_dir,
            source=source,
            split=split,
            reason="SIX_REGION_FORCE_MISSING",
            detail=f"缺少六区力列: {', '.join(missing)}",
            timeseries_path=timeseries_path,
            schedule_path=schedule_path,
            timeseries_rows=len(timeseries_rows),
            schedule_rows=len(schedule_rows),
            aligned_rows=len(aligned),
            max_error=max_error,
            actual_present=actual_present,
            force_present=force_present,
            candidate_windows=len(events),
        )
    if not events:
        return _fatal_case_result(
            case_id=case_id,
            case_dir=case_dir,
            source=source,
            split=split,
            reason="NO_ACTION_WINDOW",
            detail="动作表中没有喷气事件",
            timeseries_path=timeseries_path,
            schedule_path=schedule_path,
            timeseries_rows=len(timeseries_rows),
            schedule_rows=len(schedule_rows),
            aligned_rows=len(aligned),
            max_error=max_error,
            actual_present=actual_present,
            force_present=force_present,
        )

    dt = _sample_dt(aligned)
    if not math.isfinite(dt) or dt <= 0:
        return _fatal_case_result(
            case_id=case_id,
            case_dir=case_dir,
            source=source,
            split=split,
            reason="TIME_MISALIGNMENT",
            detail="无法从 timeseries 推断正采样步长",
            timeseries_path=timeseries_path,
            schedule_path=schedule_path,
            timeseries_rows=len(timeseries_rows),
            schedule_rows=len(schedule_rows),
            aligned_rows=len(aligned),
            max_error=max_error,
            actual_present=actual_present,
            force_present=force_present,
            candidate_windows=len(events),
        )

    result = _CaseResult(quality={})
    first_time = aligned[0].time
    initial_floor = first_time + config.minimum_initial_discard_s
    accepted_cutoffs: list[float] = []
    all_reason_codes: list[str] = []
    for event_index, event in enumerate(events):
        reasons: list[str] = []
        details: list[str] = []
        if len(event.jet_indices) != 1:
            reasons.append("MULTI_JET_ACTIVE")
            details.append("同一动作事件同时开启多个喷口")
        elif event.jet_indices[0] not in config.representative_jets:
            reasons.append("NON_REPRESENTATIVE_JET")
            details.append("该喷口不在 B52/B53 六代表喷口配置中")

        previous_end = events[event_index - 1].end if event_index else first_time
        baseline_start_target = max(event.start - config.baseline_duration_s, previous_end, initial_floor)
        baseline = [
            item
            for item in aligned
            if item.time > baseline_start_target - config.time_alignment_tolerance_s
            and item.time <= event.start + config.time_alignment_tolerance_s
            and not _active_jets(intervals[item.schedule_index].row, config.massflow_on_threshold_kg_s)
        ]
        if len(baseline) < config.minimum_baseline_samples:
            reasons.append("INITIAL_TRANSIENT_OR_SHORT_BASELINE")
            details.append(
                f"稳定基准仅 {len(baseline)} 点，低于 {config.minimum_baseline_samples} 点；启动段不会进入数据集"
            )

        next_start = events[event_index + 1].start if event_index + 1 < len(events) else math.inf
        response_end = min(event.end + config.recovery_search_s, next_start - config.baseline_duration_s)
        if response_end <= event.end:
            response_end = min(event.end + config.recovery_search_s, next_start)
        response = [
            item
            for item in aligned
            if item.time > event.start + config.time_alignment_tolerance_s
            and item.time <= response_end + config.time_alignment_tolerance_s
        ]
        event_samples = [item for item in response if item.schedule_index in event.schedule_indices]
        if len(event_samples) < config.minimum_response_samples:
            reasons.append("RESPONSE_WINDOW_TOO_SHORT")
            details.append(f"喷气段仅 {len(event_samples)} 点")

        context = [*baseline, *response]
        missing_actual = sum(
            _number(item.row.get(column)) is None for item in context for column in ACTUAL_COLUMNS
        )
        missing_force = sum(
            _number(item.row.get(source_name)) is None
            for item in context
            for source_name in region_mapping.values()
        )
        if missing_actual:
            reasons.append("ACTUAL_MASSFLOW_MISSING")
            details.append(f"事件上下文有 {missing_actual} 个实际质量流量空值/非数值")
        if missing_force:
            reasons.append("SIX_REGION_FORCE_MISSING")
            details.append(f"事件上下文有 {missing_force} 个六区力空值/非数值")

        metrics_by_region: dict[str, _RegionMetric] = {}
        actual_column = f"actual_massflow_{event.jet_indices[0]:02d}" if len(event.jet_indices) == 1 else ""
        actual_onset_s: float | None = None
        actual_off_s: float | None = None
        response_on_position = 0
        response_off_position = max(0, len(event_samples) - 1)
        if not reasons and actual_column:
            command_values = [
                abs(_number(intervals[index].row.get(f"cmd_massflow_{event.jet_indices[0]:02d}")) or 0.0)
                for index in event.schedule_indices
            ]
            threshold = max(
                config.massflow_on_threshold_kg_s,
                0.05 * max(command_values, default=0.0),
            )
            actual_positions = [
                index
                for index, item in enumerate(response)
                if abs(_number(item.row.get(actual_column)) or 0.0) > threshold
            ]
            if not actual_positions:
                reasons.append("MASSFLOW_NOT_DETECTED")
                details.append(f"{actual_column} 未在动作窗口检测到有效流量")
            else:
                response_on_position = actual_positions[0]
                response_off_position = actual_positions[-1]
                actual_onset_s = response[response_on_position].time
                actual_off_s = response[response_off_position].time
                expected_onset = event_samples[0].time
                expected_off = event_samples[-1].time
                lag = max(abs(actual_onset_s - expected_onset), abs(actual_off_s - expected_off))
                if lag > config.max_massflow_lag_s + config.time_alignment_tolerance_s:
                    reasons.append("TIME_MISALIGNMENT")
                    details.append(f"动作与实际质量流量边沿最大错位 {lag:.6g}s")

        if not reasons:
            baseline_values_by_region = {
                region: [_number(item.row[region_mapping[region]]) for item in baseline]
                for region in REGION_COLUMNS
            }
            drift_ratios: list[float] = []
            for region, optional_values in baseline_values_by_region.items():
                values = [value for value in optional_values if value is not None]
                mean_value, noise, drift, threshold = _baseline_drift(values, config)
                del mean_value, noise
                drift_ratios.append(abs(drift) / threshold if threshold else math.inf)
                if abs(drift) > threshold:
                    reasons.append("BASELINE_DRIFT")
                    details.append(f"{region} 基准漂移 {drift:.6g}N 超过 {threshold:.6g}N")
            if not reasons:
                response_times = [item.time for item in response]
                for region in REGION_COLUMNS:
                    baseline_values = [
                        float(value)
                        for value in baseline_values_by_region[region]
                        if value is not None
                    ]
                    response_values = [float(_number(item.row[region_mapping[region]])) for item in response]
                    metrics_by_region[region] = _region_metric(
                        baseline_values,
                        response_values,
                        response_times,
                        response_on_position,
                        response_off_position,
                        dt,
                        event,
                        config,
                    )
                max_snr = max(metric.snr_linear for metric in metrics_by_region.values())
                if max_snr < config.minimum_snr_linear:
                    reasons.append("RESPONSE_BELOW_NOISE")
                    details.append(
                        f"六区最大 SNR={max_snr:.6g}，低于 {config.minimum_snr_linear:.6g}"
                    )
        else:
            drift_ratios = []

        if reasons:
            all_reason_codes.extend(reasons)
            result.anomalies.append(
                _anomaly(
                    case_id=case_id,
                    case_dir=case_dir,
                    split=split,
                    reasons=reasons,
                    detail="; ".join(details),
                    event=event,
                    baseline_start_s=baseline[0].time if baseline else baseline_start_target,
                    baseline_end_s=baseline[-1].time if baseline else event.start,
                    actual_massflow_column=actual_column,
                    missing_value_count=missing_actual + missing_force,
                    max_time_alignment_error_s=max_error,
                    max_baseline_drift_ratio=max(drift_ratios, default=""),
                    max_region_snr_linear=max(
                        (metric.snr_linear for metric in metrics_by_region.values()), default=""
                    ),
                    recovery_observed_all_regions=(
                        all(metric.recovery_time_s is not None for metric in metrics_by_region.values())
                        if metrics_by_region
                        else ""
                    ),
                    region_metrics_json=(
                        json.dumps(
                            {region: metric.as_dict() for region, metric in metrics_by_region.items()},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        if metrics_by_region
                        else ""
                    ),
                )
            )
            continue

        baseline_start = baseline[0].time
        accepted_cutoffs.append(baseline_start)
        dataset_rows = _dataset_rows(
            case_id=case_id,
            case_dir=case_dir,
            manifest=manifest,
            split=split,
            timeseries_path=timeseries_path,
            schedule_path=schedule_path,
            intervals=intervals,
            event=event,
            baseline=baseline,
            response=response,
            actual_onset_s=actual_onset_s,
            actual_off_s=actual_off_s,
            baseline_start=baseline_start,
            metrics=metrics_by_region,
            region_mapping=region_mapping,
        )
        accepted = _AcceptedWindow(event, split, dataset_rows, metrics_by_region)
        result.accepted.append(accepted)
        if not all(metric.recovery_time_s is not None for metric in metrics_by_region.values()):
            result.anomalies.append(
                _anomaly(
                    case_id=case_id,
                    case_dir=case_dir,
                    split=split,
                    reasons=["RECOVERY_NOT_OBSERVED"],
                    detail="至少一个区域在可用恢复段内未持续回到恢复带",
                    event=event,
                    severity="WARNING",
                    status="ACCEPTED_WITH_WARNING",
                    baseline_start_s=baseline_start,
                    baseline_end_s=baseline[-1].time,
                    actual_massflow_column=actual_column,
                    missing_value_count=0,
                    max_time_alignment_error_s=max_error,
                    max_baseline_drift_ratio=max(
                        abs(metric.baseline_drift) / metric.baseline_drift_threshold
                        for metric in metrics_by_region.values()
                    ),
                    max_region_snr_linear=max(metric.snr_linear for metric in metrics_by_region.values()),
                    recovery_observed_all_regions=False,
                    region_metrics_json=json.dumps(
                        {region: metric.as_dict() for region, metric in metrics_by_region.items()},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            )

    accepted_count = len(result.accepted)
    rejected_count = len(events) - accepted_count
    sample_count = sum(len(window.dataset_rows) for window in result.accepted)
    status = "PASS" if accepted_count == len(events) else "PARTIAL" if accepted_count else "REJECTED"
    quality = {field: "" for field in QUALITY_FIELDS}
    quality.update(
        {
            "source_case_id": case_id,
            "source_case_dir": _path_text(case_dir),
            "requested_split": source.split,
            "assigned_split": split,
            "status": status,
            "training_ready": accepted_count > 0,
            "reason_codes": "|".join(dict.fromkeys(all_reason_codes)),
            "reason_detail": "" if accepted_count == len(events) else "详见 B03_anomalous_windows.csv",
            "timeseries_rows": len(timeseries_rows),
            "schedule_rows": len(schedule_rows),
            "aligned_rows": len(aligned),
            "time_alignment_mode": alignment_mode,
            "time_alignment_mismatch_rows": 0,
            "max_time_alignment_error_s": max_error,
            "initial_transient_cutoff_s": min(accepted_cutoffs, default=""),
            "initial_transient_removed_rows": (
                sum(item.time < min(accepted_cutoffs) for item in aligned) if accepted_cutoffs else 0
            ),
            "candidate_windows": len(events),
            "accepted_windows": accepted_count,
            "rejected_windows": rejected_count,
            "training_samples": sample_count if split == "training" else 0,
            "validation_samples": sample_count if split == "validation" else 0,
            "actual_massflow_columns_present": actual_present,
            "six_region_force_columns_present": force_present,
            "source_timeseries_csv": _path_text(timeseries_path),
            "source_schedule_csv": _path_text(schedule_path),
        }
    )
    result.quality = quality
    return result


def _dataset_rows(
    *,
    case_id: str,
    case_dir: Path,
    manifest: Mapping[str, Any],
    split: str,
    timeseries_path: Path,
    schedule_path: Path,
    intervals: Sequence[_ScheduleInterval],
    event: _Event,
    baseline: Sequence[_AlignedRow],
    response: Sequence[_AlignedRow],
    actual_onset_s: float | None,
    actual_off_s: float | None,
    baseline_start: float,
    metrics: Mapping[str, _RegionMetric],
    region_mapping: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_star = case_dir / "raw_star"
    for item in [*baseline, *response]:
        schedule = intervals[item.schedule_index]
        phase = "baseline" if item.time <= event.start else "actuation" if item.time <= event.end else "recovery"
        row: dict[str, Any] = {
            "sample_id": f"{event.event_id}__r{item.source_index + 2}",
            "split": split,
            "source_case_id": case_id,
            "source_case_dir": _path_text(case_dir),
            "source_raw_star_dir": _path_text(raw_star) if raw_star.exists() else "",
            "source_timeseries_csv": _path_text(timeseries_path),
            "source_schedule_csv": _path_text(schedule_path),
            "source_timeseries_row": item.source_index + 2,
            "source_schedule_row": schedule.row_index + 2,
            "source_git_commit": manifest.get("git_commit", ""),
            "source_star_sim_file": _manifest_star_value(manifest, "sim_file"),
            "source_star_sim_hash_sha256": _manifest_star_value(manifest, "sim_file_hash_sha256"),
            "physical_time": item.time,
            "action_window_id": event.action_window_id,
            "event_id": event.event_id,
            "jet_id": event.jet_id,
            "phase": phase,
            "time_from_action_s": item.time - event.start,
            "action_start_s": event.start,
            "action_end_s": event.end,
            "actual_massflow_onset_s": actual_onset_s,
            "actual_massflow_off_s": actual_off_s,
            "initial_transient_cutoff_s": baseline_start,
            "quality_status": "PASS",
        }
        for column in JET_COLUMNS:
            row[column] = int((_number(schedule.row.get(column)) or 0.0) > 0.5)
        for column in COMMAND_COLUMNS:
            row[column] = _number(schedule.row.get(column)) or 0.0
        for column in ACTUAL_COLUMNS:
            row[column] = _number(item.row.get(column))
        for region in REGION_COLUMNS:
            raw_force = _number(item.row.get(region_mapping[region]))
            metric = metrics[region]
            row[region] = raw_force
            row[f"baseline_mean_{region}"] = metric.baseline_mean
            row[f"baseline_noise_{region}"] = metric.baseline_noise
            row[f"delta_{region}"] = None if raw_force is None else raw_force - metric.baseline_mean
        rows.append(row)
    return rows


def _median_metric(values: Sequence[float | None]) -> float | str:
    numeric = [value for value in values if value is not None]
    return median(numeric) if numeric else ""


def _response_summary(
    config: B53Config,
    accepted: Sequence[_AcceptedWindow],
    anomalies: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position, jet in enumerate(config.representative_jets):
        jet_id = f"J{jet:02d}"
        windows = [window for window in accepted if window.event.jet_id == jet_id]
        rejected = [
            row
            for row in anomalies
            if row.get("jet_id") == jet_id and row.get("window_status") == "REJECTED"
        ]
        row = {field: "" for field in RESPONSE_FIELDS}
        row.update(
            {
                "jet_id": jet_id,
                "representative_region": REGION_COLUMNS[position].removeprefix("Fz_"),
                "status": "PASS" if windows else "REJECTED" if rejected else "NO_DATA",
                "accepted_window_count": len(windows),
                "rejected_window_count": len(rejected),
                "source_case_ids": "|".join(
                    sorted({window.event.event_id.split("__", 1)[0] for window in windows})
                ),
                "notes": "" if windows else "尚无通过质量门禁的正式 STAR 响应窗口",
            }
        )
        if windows:
            region_peaks: dict[str, float] = {}
            for region in REGION_COLUMNS:
                region_metrics = [window.metrics[region] for window in windows]
                peak = _median_metric([metric.peak_delta for metric in region_metrics])
                delay = _median_metric([metric.response_delay_s for metric in region_metrics])
                recovery = _median_metric([metric.recovery_time_s for metric in region_metrics])
                snr_linear = _median_metric([metric.snr_linear for metric in region_metrics])
                snr_db = _median_metric([metric.snr_db for metric in region_metrics])
                row[f"{region}_median_peak_delta_N"] = peak
                row[f"{region}_median_response_delay_s"] = delay
                row[f"{region}_median_recovery_time_s"] = recovery
                row[f"{region}_median_snr_linear"] = snr_linear
                row[f"{region}_median_snr_db"] = snr_db
                region_peaks[region] = abs(float(peak)) if peak != "" else 0.0
            dominant = max(region_peaks, key=region_peaks.get)
            row.update(
                {
                    "dominant_region": dominant,
                    "median_response_delay_s": row[f"{dominant}_median_response_delay_s"],
                    "median_peak_delta_N": row[f"{dominant}_median_peak_delta_N"],
                    "median_recovery_time_s": row[f"{dominant}_median_recovery_time_s"],
                    "median_snr_linear": row[f"{dominant}_median_snr_linear"],
                    "median_snr_db": row[f"{dominant}_median_snr_db"],
                }
            )
        rows.append(row)
    return rows


def build_b53_outputs(
    config: B53Config,
    output_dir: str | Path,
    *,
    sources: Sequence[CaseSource | str | Path] | None = None,
) -> dict[str, Any]:
    """运行完整门禁并写出五个稳定 schema 的 CSV。"""

    config.require_valid()
    chosen = config.sources if sources is None else tuple(
        value if isinstance(value, CaseSource) else CaseSource(value) for value in sources
    )
    normalized = [source.normalized() for source in chosen]
    seen: dict[Path, str] = {}
    unique_sources: list[CaseSource] = []
    for source in normalized:
        resolved = Path(source.case_dir).resolve()
        if resolved in seen and seen[resolved] != source.split:
            raise ValueError(f"同一 Case 不能同时进入训练与验证: {resolved}")
        if resolved in seen:
            continue
        seen[resolved] = source.split
        unique_sources.append(source)

    results = [_process_case(source, config) for source in unique_sources]
    accepted = [window for result in results for window in result.accepted]
    anomalies = [row for result in results for row in result.anomalies]
    training_rows = [row for window in accepted if window.split == "training" for row in window.dataset_rows]
    validation_rows = [row for window in accepted if window.split == "validation" for row in window.dataset_rows]
    quality_rows = [result.quality for result in results] or [_empty_quality()]
    response_rows = _response_summary(config, accepted, anomalies)

    output = Path(output_dir)
    _write_csv(output / "training_dataset.csv", DATASET_FIELDS, training_rows)
    _write_csv(output / "validation_dataset.csv", DATASET_FIELDS, validation_rows)
    _write_csv(output / "B03_data_quality_summary.csv", QUALITY_FIELDS, quality_rows)
    _write_csv(output / "B03_six_jet_response_summary.csv", RESPONSE_FIELDS, response_rows)
    _write_csv(output / "B03_anomalous_windows.csv", ABNORMAL_FIELDS, anomalies)

    rejected_windows = sum(int(result.quality.get("rejected_windows") or 0) for result in results)
    if not unique_sources:
        overall_status = "NO_DATA"
    elif accepted and not rejected_windows:
        overall_status = "PASS"
    elif accepted:
        overall_status = "PARTIAL"
    else:
        overall_status = "REJECTED"
    report = {
        "schema_version": "B53_real_training_dataset_v1",
        "overall_status": overall_status,
        "training_ready": bool(accepted),
        "source_case_count": len(unique_sources),
        "accepted_window_count": len(accepted),
        "rejected_window_count": rejected_windows,
        "training_rows": len(training_rows),
        "validation_rows": len(validation_rows),
        "output_dir": _path_text(output),
    }
    return report


def _parse_source(value: str) -> CaseSource:
    if "::" in value:
        path, split = value.rsplit("::", 1)
        return CaseSource(path, split)
    return CaseSource(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/b53/data_filter.yaml")
    parser.add_argument("--output-dir", default="artifacts/B53_training_data")
    parser.add_argument(
        "--case-dir",
        action="append",
        default=[],
        metavar="PATH[::training|validation]",
        help="可重复指定；显式参数会替代配置中的 sources",
    )
    parser.add_argument(
        "--input-root",
        action="append",
        default=[],
        help="递归发现标准 Case；仅在显式传入时扫描",
    )
    parser.add_argument("--require-data", action="store_true", help="没有通过门禁的数据时返回非零")
    args = parser.parse_args(argv)
    config = load_b53_config(args.config)
    sources: list[CaseSource] | None = None
    if args.case_dir or args.input_root:
        sources = [_parse_source(value) for value in args.case_dir]
        for root in args.input_root:
            sources.extend(CaseSource(path) for path in discover_case_dirs(root))
    report = build_b53_outputs(config, args.output_dir, sources=sources)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.require_data and not report["training_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
