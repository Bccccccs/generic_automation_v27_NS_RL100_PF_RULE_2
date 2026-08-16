"""B04 real-data quality checks and deterministic delivery generation.

The checker deliberately keeps missing measurements missing.  Plotting is a
presentation step only and never changes a check result or synthesizes zeros.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

from flow_control.case_paths import find_case_timeseries_path
from flow_control.star_ingest.star_export_reader import read_star_export_csv


REGION_COLUMNS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
LEGACY_REGION_COLUMNS = REGION_COLUMNS
UNDERBODY_TOTAL_COLUMN = "fz"
LEGACY_UNDERBODY_TOTAL_COLUMN = "legacy_underbody_report_force_z"
VEHICLE_LIFT_COLUMN = "Fz_Total"
LEGACY_AMBIGUOUS_LIFT_COLUMN = "Fz_Total"
MOMENT_COLUMNS = ("Pitch_Moment", "Roll_Moment")
LEGACY_MOMENT_COLUMNS = MOMENT_COLUMNS
JET_REACTION_COLUMN = "Jet_Momentum_Reaction_Z"
J_SURFACE_FORCE_COLUMNS = ("J_Surface_PressureShear_Force_Z", "J_Surface_Force_Z")
LEGACY_J_SURFACE_FORCE_COLUMN = "Jet_Reaction_Z"
JET_COLUMNS = tuple(f"JET_{index:02d}" for index in range(1, 25))
ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{index:02d}" for index in range(1, 25))
CMD_MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{index:02d}" for index in range(1, 25))
LEGACY_JET_COLUMNS = JET_COLUMNS
LEGACY_ACTUAL_MASSFLOW_COLUMNS = ACTUAL_MASSFLOW_COLUMNS
LEGACY_CMD_MASSFLOW_COLUMNS = CMD_MASSFLOW_COLUMNS
CATEGORY_KEYS = (
    "format_errors",
    "time_errors",
    "massflow_errors",
    "force_definition_errors",
    "physical_questions_for_haokun",
)
BLOCKING_CATEGORIES = CATEGORY_KEYS[:-1]


@dataclass(frozen=True)
class B04Thresholds:
    zero_massflow: float = 1e-8
    zero_force: float = 1e-8
    time_tolerance: float = 1e-9
    force_sum_abs: float = 1e-6
    force_sum_rel: float = 1e-4
    drift_relative: float = 0.15
    jump_mad_multiplier: float = 25.0
    asymmetry_relative: float = 0.35


def check_real_case(
    case_dir: str | Path,
    *,
    allowed_active_jets: Iterable[int] = (2, 6),
    thresholds: B04Thresholds | None = None,
) -> dict[str, Any]:
    """Check one standard real STAR case and return a JSON-safe report."""

    threshold = thresholds or B04Thresholds()
    case_path = Path(case_dir)
    report = {
        "schema_version": "B04_real_data_quality_v1",
        "case_id": case_path.name,
        "case_dir": str(case_path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "categories": {key: [] for key in CATEGORY_KEYS},
        "metrics": {},
        "plot_eligibility": {},
        "data_policy": {
            "missing_fields_filled_with_zero": False,
            "plot_success_can_override_quality_failure": False,
            "underbody_total_column": UNDERBODY_TOTAL_COLUMN,
            "vehicle_lift_column": VEHICLE_LIFT_COLUMN,
            "jet_momentum_reaction_column": JET_REACTION_COLUMN,
            "j_surface_pressure_shear_columns": list(J_SURFACE_FORCE_COLUMNS),
            "derived_underbody_total_is_not_zero_fill": True,
        },
    }
    timeseries_path = find_case_timeseries_path(case_path)
    rows, columns, read_error = _read_csv(timeseries_path)
    schedule, schedule_columns, schedule_error = _read_csv(case_path / "actuation_schedule.csv")
    manifest = _read_yaml(case_path / "case_manifest.yaml")

    if read_error:
        _issue(report, "format_errors", "error", read_error)
    if schedule_error:
        _issue(report, "format_errors", "error", schedule_error)
    if not rows:
        _issue(report, "format_errors", "error", "processed/timeseries.csv is missing or empty")
        return _finalize(report)
    if not schedule:
        _issue(report, "format_errors", "error", "actuation_schedule.csv is missing or empty")

    _check_numeric_columns(report, rows, columns)
    _check_time(report, rows, schedule, schedule_columns, threshold)
    active_jets = _check_massflow(
        report,
        rows,
        schedule,
        columns,
        allowed_active_jets=set(allowed_active_jets),
        threshold=threshold,
    )
    _check_force_definitions(report, case_path, rows, columns, active_jets, threshold)
    if _is_no_jet_case(manifest, active_jets):
        _check_no_jet_physics(report, rows, columns, threshold)

    report["metrics"]["row_count"] = len(rows)
    report["metrics"]["column_count"] = len(columns)
    report["metrics"]["active_jets"] = sorted(active_jets)
    report["metrics"]["declared_case_type"] = manifest.get("case_type", "unknown")
    report["plot_eligibility"] = _plot_eligibility(columns)
    return _finalize(report)


def generate_case_figures(case_dir: str | Path, output_dir: str | Path) -> dict[str, str]:
    """Generate the five required plot groups without fabricating values."""

    case_path = Path(case_dir)
    rows, columns, _ = _read_csv(find_case_timeseries_path(case_path))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if not rows:
        return {}
    time = _numeric_series(rows, "physical_time")
    generated: dict[str, str] = {}
    target_massflow_columns = _massflow_plot_columns(columns)
    region_plot_columns = _resolved_region_columns(columns)
    underbody_plot_column = _first_present(columns, UNDERBODY_TOTAL_COLUMN, LEGACY_UNDERBODY_TOTAL_COLUMN)
    specifications = (
        ("massflow", target_massflow_columns, "J02/J06 Commanded and Actual Mass-flow", "kg/s"),
        ("six_region_lift", list(region_plot_columns or REGION_COLUMNS), "Six Underbody-region Lift Time Histories", "Fz (N)"),
        ("underbody_total_lift", [underbody_plot_column or UNDERBODY_TOTAL_COLUMN], "Underbody Six-region Total Lift", "Fz (N)"),
        ("vehicle_lift", [VEHICLE_LIFT_COLUMN], "Whole-vehicle Lift", "Fz (N)"),
        ("moments", list(MOMENT_COLUMNS), "Whole-vehicle Moment Time Histories", "Moment (N-m)"),
    )
    for name, requested, title, ylabel in specifications:
        path = output / f"{name}.png"
        present = [column for column in requested if column in columns]
        _plot_or_explain(path, time, rows, present, title, ylabel, requested)
        generated[name] = str(path)
    return generated


def run_delivery(
    case_dirs: Iterable[str | Path],
    *,
    output_dir: str | Path = "artifacts/reports",
    expected_case_count: int = 3,
    allowed_active_jets: Iterable[int] = (2, 6),
) -> dict[str, Any]:
    """Check all supplied cases and write the complete B04 delivery bundle."""

    output = Path(output_dir)
    figures_root = output / "B04_figures"
    output.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    for value in case_dirs:
        case_path = Path(value)
        report = check_real_case(case_path, allowed_active_jets=allowed_active_jets)
        figures = generate_case_figures(case_path, figures_root / case_path.name)
        report["figures"] = figures
        quality_path = case_path / "quality_report.json"
        existing = _read_json(quality_path)
        existing["B04_real_quality"] = report
        quality_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        reports.append(report)

    summary_path = output / "B04_real_case_quality_summary.csv"
    _write_summary(summary_path, reports)
    blocking_path = output / "B04_current_blocking_issues.md"
    _write_blocking_issues(blocking_path, reports, expected_case_count)
    return {
        "reports": reports,
        "summary_csv": str(summary_path),
        "blocking_issues_md": str(blocking_path),
        "expected_case_count": expected_case_count,
        "available_case_count": len(reports),
    }


def _check_numeric_columns(report: dict[str, Any], rows: list[dict[str, str]], columns: list[str]) -> None:
    required = ["physical_time"]
    for column in required:
        if column not in columns:
            _issue(report, "format_errors", "error", "缺失必需字段", field=column)
    invalid_examples: list[dict[str, Any]] = []
    numeric_candidates = [column for column in columns if column != "solver_status" and column != "case_stage"]
    for row_index, row in enumerate(rows):
        for column in numeric_candidates:
            value = _number(row.get(column))
            if value is None and len(invalid_examples) < 10:
                invalid_examples.append({"row": row_index, "field": column, "value": row.get(column)})
    if invalid_examples:
        _issue(report, "format_errors", "error", "数值字段存在缺失、NaN、Inf 或非数值内容", examples=invalid_examples)


def _check_time(
    report: dict[str, Any],
    rows: list[dict[str, str]],
    schedule: list[dict[str, str]],
    schedule_columns: list[str],
    threshold: B04Thresholds,
) -> None:
    times = [_number(row.get("physical_time")) for row in rows]
    for index in range(1, len(times)):
        if times[index] is None or times[index - 1] is None or times[index] <= times[index - 1] + threshold.time_tolerance:
            _issue(report, "time_errors", "error", "physical_time 不是严格单调递增", row=index, previous=times[index - 1], current=times[index])
            break
    if not schedule:
        return
    if "t_start" not in schedule_columns or "t_end" not in schedule_columns:
        _issue(report, "time_errors", "error", "动作表缺少 t_start/t_end，无法验证动作窗口")
        return
    if len(rows) != len(schedule):
        _issue(report, "time_errors", "error", "采样行数与动作窗口行数不一致", timeseries_rows=len(rows), schedule_rows=len(schedule))
    outside: list[dict[str, Any]] = []
    misaligned_window_ids: list[dict[str, Any]] = []
    for index, (row, action) in enumerate(zip(rows, schedule)):
        sample = _number(row.get("physical_time"))
        start = _number(action.get("t_start"))
        end = _number(action.get("t_end"))
        if sample is None or start is None or end is None or not (start - threshold.time_tolerance <= sample <= end + threshold.time_tolerance):
            if len(outside) < 10:
                outside.append({"row": index, "physical_time": sample, "t_start": start, "t_end": end})
        row_window = _number(row.get("window_id"))
        action_window = _number(action.get("window_id"))
        if row_window is not None and action_window is not None and row_window != action_window and len(misaligned_window_ids) < 10:
            misaligned_window_ids.append({"row": index, "timeseries_window_id": row_window, "schedule_window_id": action_window})
    if outside:
        _issue(report, "time_errors", "error", "采样时间未落入配对动作窗口", examples=outside)
    if misaligned_window_ids:
        _issue(report, "time_errors", "error", "timeseries 与动作表的 window_id 未对齐", examples=misaligned_window_ids)
    report["metrics"]["time_alignment"] = {"rows_checked": min(len(rows), len(schedule)), "outside_window_count_capped": len(outside)}


def _check_massflow(
    report: dict[str, Any],
    rows: list[dict[str, str]],
    schedule: list[dict[str, str]],
    columns: list[str],
    *,
    allowed_active_jets: set[int],
    threshold: B04Thresholds,
) -> set[int]:
    action_rows = schedule or rows
    active: set[int] = set()
    for index in range(1, 25):
        jet_column = _action_column(action_rows, index, "switch")
        command_column = _action_column(action_rows, index, "cmd")
        if any(abs(_number(row.get(jet_column)) or 0.0) > 0.5 or abs(_number(row.get(command_column)) or 0.0) > threshold.zero_massflow for row in action_rows):
            active.add(index)
    unexpected = sorted(active - allowed_active_jets)
    if unexpected:
        _issue(report, "massflow_errors", "error", "存在目标动作 J02/J06 之外的喷气口开启", unexpected_active_jets=unexpected, allowed_active_jets=sorted(allowed_active_jets))

    leak_examples: list[dict[str, Any]] = []
    missing_examples: list[dict[str, Any]] = []
    for index in range(1, 25):
        jet_column = _action_column(rows, index, "switch")
        actual_column = _action_column(rows, index, "actual")
        for row_index, row in enumerate(rows):
            is_on = abs(_number(row.get(jet_column)) or 0.0) > 0.5
            actual = _number(row.get(actual_column)) if actual_column in columns else None
            if is_on and actual is None and len(missing_examples) < 10:
                missing_examples.append({"row": row_index, "jet": jet_column, "missing_field": actual_column})
            if not is_on and actual is not None and abs(actual) > threshold.zero_massflow and len(leak_examples) < 10:
                leak_examples.append({"row": row_index, "jet": jet_column, "actual_massflow": actual})
    if missing_examples:
        _issue(report, "massflow_errors", "error", "喷气口开启时缺少 actual_massflow；未使用 cmd_massflow 替代", examples=missing_examples)
    if leak_examples:
        _issue(report, "massflow_errors", "error", "关闭喷气口的 actual_massflow 未接近 0", tolerance=threshold.zero_massflow, examples=leak_examples)
    if active & allowed_active_jets:
        missing_target_columns = [f"actual_massflow_{index:02d}" for index in sorted(active & allowed_active_jets) if _action_column(rows, index, "actual") not in columns]
        if missing_target_columns:
            _issue(report, "massflow_errors", "error", "J02/J06 开启但缺少实际质量流量字段", missing_fields=missing_target_columns)
    report["metrics"]["massflow"] = {
        "allowed_active_jets": sorted(allowed_active_jets),
        "detected_active_jets": sorted(active),
        "actual_massflow_columns_present": max(
            sum(column in columns for column in ACTUAL_MASSFLOW_COLUMNS),
            sum(column in columns for column in LEGACY_ACTUAL_MASSFLOW_COLUMNS),
        ),
        "zero_tolerance": threshold.zero_massflow,
    }
    return active


def _check_force_definitions(
    report: dict[str, Any], case_path: Path, rows: list[dict[str, str]], columns: list[str], active_jets: set[int], threshold: B04Thresholds
) -> None:
    region_columns = _resolved_region_columns(columns)
    if not region_columns:
        _issue(report, "force_definition_errors", "error", "六个区域升力字段不齐全", required_fields=list(REGION_COLUMNS))
    underbody_column = _first_present(columns, UNDERBODY_TOTAL_COLUMN, LEGACY_UNDERBODY_TOTAL_COLUMN)
    derived_underbody = False
    if VEHICLE_LIFT_COLUMN not in columns:
        _issue(report, "force_definition_errors", "error", "缺少独立的整车升力字段", required_field=VEHICLE_LIFT_COLUMN)
    missing_moments = [column for column in MOMENT_COLUMNS if column not in columns]
    if missing_moments:
        _issue(report, "force_definition_errors", "error", "缺少力矩字段", missing_fields=missing_moments)
    mismatch: list[dict[str, Any]] = []
    if region_columns and underbody_column:
        for row_index, row in enumerate(rows):
            values = [_number(row.get(column)) for column in region_columns]
            total = _number(row.get(underbody_column))
            if total is None or any(value is None for value in values):
                continue
            regional_sum = sum(float(value) for value in values if value is not None)
            tolerance = max(threshold.force_sum_abs, threshold.force_sum_rel * max(abs(regional_sum), abs(total), 1.0))
            if abs(total - regional_sum) > tolerance and len(mismatch) < 10:
                mismatch.append({"row": row_index, "regional_sum": regional_sum, "underbody_total": total, "difference": total - regional_sum, "tolerance": tolerance})
    if mismatch:
        _issue(report, "force_definition_errors", "error", "车底六区合力不等于六个区域之和", examples=mismatch)
    raw_underbody_rows = _read_raw_underbody_rows(case_path)
    if region_columns and raw_underbody_rows:
        legacy_mismatch: list[dict[str, Any]] = []
        for row_index, (row, raw_row) in enumerate(zip(rows, raw_underbody_rows)):
            values = [_number(row.get(column)) for column in region_columns]
            legacy_total = _number(raw_row.get(LEGACY_UNDERBODY_TOTAL_COLUMN))
            if legacy_total is None or any(value is None for value in values):
                continue
            regional_sum = sum(float(value) for value in values if value is not None)
            tolerance = max(threshold.force_sum_abs, threshold.force_sum_rel * max(abs(regional_sum), abs(legacy_total), 1.0))
            if abs(legacy_total - regional_sum) > tolerance and len(legacy_mismatch) < 10:
                legacy_mismatch.append({"row": row_index, "regional_sum": regional_sum, "legacy_report_value": legacy_total, "difference": legacy_total - regional_sum, "tolerance": tolerance})
        if legacy_mismatch:
            _issue(report, "force_definition_errors", "error", "原始 fz区域合力不等于六个区域之和；需确认该 report 的积分表面和定义", source_field="fz Monitor", examples=legacy_mismatch)

    if LEGACY_J_SURFACE_FORCE_COLUMN in columns:
        _issue(report, "force_definition_errors", "error", "同名 Jet_Reaction_Z report 在无喷气基准中仍非零，不能作为喷气动量反作用力；若为 J 表面压力/剪切合力，必须使用不同名称", ambiguous_field=LEGACY_J_SURFACE_FORCE_COLUMN, allowed_surface_force_names=list(J_SURFACE_FORCE_COLUMNS))
    if not active_jets:
        if JET_REACTION_COLUMN not in columns:
            _issue(report, "force_definition_errors", "error", "无喷气算例缺少独立的喷气动量反作用力字段；缺失字段不能补 0", required_field=JET_REACTION_COLUMN)
        else:
            nonzero = _nonzero_examples(rows, JET_REACTION_COLUMN, threshold.zero_force)
            if nonzero:
                _issue(report, "force_definition_errors", "error", "无喷气算例的喷气动量反作用力不为 0", field=JET_REACTION_COLUMN, examples=nonzero)
    report["metrics"]["force_definition"] = {
        "regional_columns_present": list(region_columns),
        "underbody_total_present": underbody_column is not None or derived_underbody,
        "underbody_total_source": underbody_column or "missing_fz_report",
        "vehicle_lift_present": VEHICLE_LIFT_COLUMN in columns,
        "jet_reaction_present": JET_REACTION_COLUMN in columns,
        "j_surface_force_columns_present": [column for column in (*J_SURFACE_FORCE_COLUMNS, LEGACY_J_SURFACE_FORCE_COLUMN) if column in columns],
    }


def _check_no_jet_physics(report: dict[str, Any], rows: list[dict[str, str]], columns: list[str], threshold: B04Thresholds) -> None:
    region_columns = _resolved_region_columns(columns)
    underbody_column = _first_present(columns, UNDERBODY_TOTAL_COLUMN, LEGACY_UNDERBODY_TOTAL_COLUMN)
    series_names = [column for column in [*region_columns, underbody_column, VEHICLE_LIFT_COLUMN, *MOMENT_COLUMNS] if column and column in columns]
    drift_flags: list[dict[str, Any]] = []
    jump_flags: list[dict[str, Any]] = []
    for column in series_names:
        values = _numeric_series(rows, column)[1]
        if len(values) < 10:
            continue
        chunk = max(3, len(values) // 10)
        start_mean = sum(values[:chunk]) / chunk
        end_mean = sum(values[-chunk:]) / chunk
        scale = max(median(abs(value) for value in values), 1.0)
        drift_ratio = abs(end_mean - start_mean) / scale
        if drift_ratio > threshold.drift_relative:
            drift_flags.append({"series": column, "start_mean": start_mean, "end_mean": end_mean, "relative_drift": drift_ratio})
        differences = [abs(values[index] - values[index - 1]) for index in range(1, len(values))]
        baseline = median(differences)
        jump_threshold = max(threshold.zero_force, threshold.jump_mad_multiplier * baseline)
        max_jump = max(differences, default=0.0)
        if baseline > 0 and max_jump > jump_threshold:
            jump_flags.append({"series": column, "max_step_jump": max_jump, "median_step_change": baseline, "threshold": jump_threshold})
    asymmetry_flags: list[dict[str, Any]] = []
    for left, right in zip(region_columns[::2], region_columns[1::2]):
        if left not in columns or right not in columns:
            continue
        left_values = _numeric_series(rows, left)[1]
        right_values = _numeric_series(rows, right)[1]
        if not left_values or not right_values:
            continue
        left_mean = sum(left_values) / len(left_values)
        right_mean = sum(right_values) / len(right_values)
        relative = abs(left_mean - right_mean) / max(abs(left_mean), abs(right_mean), 1.0)
        if relative > threshold.asymmetry_relative:
            asymmetry_flags.append({"left": left, "right": right, "left_mean": left_mean, "right_mean": right_mean, "relative_asymmetry": relative})
    for message, flags in (
        ("无喷气基准存在明显漂移，请浩坤判断启动段、收敛性和取样区间", drift_flags),
        ("无喷气基准存在异常跳变，请浩坤判断求解稳定性或数据导出", jump_flags),
        ("无喷气基准左右严重不对称，请浩坤判断几何、网格、边界条件和物理合理性", asymmetry_flags),
    ):
        if flags:
            _issue(report, "physical_questions_for_haokun", "needs_review", message, findings=flags)
    report["metrics"]["no_jet_physics"] = {
        "drift_flags": drift_flags,
        "jump_flags": jump_flags,
        "asymmetry_flags": asymmetry_flags,
        "automatic_verdict": "仅筛查并交浩坤判断，不自动判定物理正确性",
    }


def _plot_or_explain(
    path: Path,
    time: tuple[list[int], list[float]],
    rows: list[dict[str, str]],
    present: list[str],
    title: str,
    ylabel: str,
    requested: list[str],
) -> None:
    fig, ax = plt.subplots(figsize=(12, 6))
    time_indices, time_values = time
    if not present or not time_values:
        ax.axis("off")
        ax.text(0.5, 0.58, title, ha="center", fontsize=16, transform=ax.transAxes)
        ax.text(0.5, 0.42, "DATA UNAVAILABLE (missing values were not filled with zero)", ha="center", color="#9c2f2f", fontsize=12, transform=ax.transAxes)
        ax.text(0.5, 0.28, "Missing fields: " + ", ".join(column for column in requested if column not in (rows[0] if rows else {})), ha="center", wrap=True, fontsize=9, transform=ax.transAxes)
    else:
        for column in present:
            indices, values = _numeric_series(rows, column)
            lookup = dict(zip(indices, values))
            paired_x = [value for index, value in zip(time_indices, time_values) if index in lookup]
            paired_y = [lookup[index] for index in time_indices if index in lookup]
            if paired_y:
                ax.plot(paired_x, paired_y, label=column, linewidth=0.8)
        ax.set_xlabel("physical_time (s)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        if ax.lines:
            ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _plot_eligibility(columns: list[str]) -> dict[str, Any]:
    region_columns = _resolved_region_columns(columns)
    underbody_column = _first_present(columns, UNDERBODY_TOTAL_COLUMN, LEGACY_UNDERBODY_TOTAL_COLUMN)
    return {
        "massflow": {"available": bool(_massflow_plot_columns(columns)), "missing_actual_fields": [column for column in ACTUAL_MASSFLOW_COLUMNS if column not in columns]},
        "six_region_lift": {"available": bool(region_columns), "missing_fields": [] if region_columns else list(REGION_COLUMNS)},
        "underbody_total_lift": {"available": underbody_column is not None, "missing_fields": [] if underbody_column else [UNDERBODY_TOTAL_COLUMN]},
        "vehicle_lift": {"available": VEHICLE_LIFT_COLUMN in columns, "missing_fields": [] if VEHICLE_LIFT_COLUMN in columns else [VEHICLE_LIFT_COLUMN]},
        "moments": {"available": all(column in columns for column in MOMENT_COLUMNS), "missing_fields": [column for column in MOMENT_COLUMNS if column not in columns]},
    }


def _resolved_region_columns(columns: Iterable[str]) -> tuple[str, ...]:
    present = set(columns)
    if all(column in present for column in REGION_COLUMNS):
        return REGION_COLUMNS
    if all(column in present for column in LEGACY_REGION_COLUMNS):
        return LEGACY_REGION_COLUMNS
    return ()


def _first_present(columns: Iterable[str], *candidates: str) -> str | None:
    present = set(columns)
    return next((column for column in candidates if column in present), None)


def _action_column(rows: list[dict[str, str]], index: int, kind: str) -> str:
    present = set(rows[0]) if rows else set()
    final = {
        "switch": f"J{index:02d}_switch",
        "cmd": f"J{index:02d}_cmd_massflow_kg_s",
        "actual": f"J{index:02d}_actual_massflow_kg_s",
    }[kind]
    legacy = {
        "switch": f"JET_{index:02d}",
        "cmd": f"cmd_massflow_{index:02d}",
        "actual": f"actual_massflow_{index:02d}",
    }[kind]
    return final if final in present else legacy if legacy in present else final


def _massflow_plot_columns(columns: list[str]) -> list[str]:
    rows = [{column: "" for column in columns}]
    result: list[str] = []
    for index in (2, 6):
        for kind in ("cmd", "actual"):
            column = _action_column(rows, index, kind)
            if column in columns:
                result.append(column)
    return result


def _write_summary(path: Path, reports: list[dict[str, Any]]) -> None:
    fields = ["case_id", "overall_status", "blocking_issue_count", "haokun_review_count", *CATEGORY_KEYS, "active_jets", "row_count"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for report in reports:
            counts = report["summary"]["category_counts"]
            writer.writerow({
                "case_id": report["case_id"],
                "overall_status": report["summary"]["overall_status"],
                "blocking_issue_count": report["summary"]["blocking_issue_count"],
                "haokun_review_count": counts["physical_questions_for_haokun"],
                **counts,
                "active_jets": ";".join(f"J{index:02d}" for index in report["metrics"].get("active_jets", [])),
                "row_count": report["metrics"].get("row_count", 0),
            })


def _write_blocking_issues(path: Path, reports: list[dict[str, Any]], expected_case_count: int) -> None:
    lines = ["# B04 当前阻塞问题", "", "本文件由真实数据质量检查自动生成。画图成功不代表数据通过，缺失字段未补 0。", ""]
    if len(reports) < expected_case_count:
        lines.extend([f"## 数据到位情况", "", f"- 计划检查 {expected_case_count} 个算例，当前仅发现/指定 {len(reports)} 个完整算例；尚缺 {expected_case_count - len(reports)} 个。", "- 当前两个算例仅用于打通整理流程，正确数据替换后需要重新运行。", ""])
    for report in reports:
        lines.extend([f"## {report['case_id']}", ""])
        found = False
        for category in CATEGORY_KEYS:
            for issue in report["categories"][category]:
                found = True
                tag = "需浩坤判断" if category == "physical_questions_for_haokun" else "阻塞"
                lines.append(f"- [{tag} / {category}] {issue['message']}")
        if not found:
            lines.append("- 未发现问题。")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str], str | None]:
    if not path.is_file():
        return [], [], f"文件不存在: {path}"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return [], [], f"CSV 缺少表头: {path}"
            return list(reader), list(reader.fieldnames), None
    except (OSError, csv.Error, UnicodeError) as exc:
        return [], [], f"CSV 读取失败: {path}: {exc}"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_raw_underbody_rows(case_path: Path) -> list[dict[str, Any]]:
    """Read the explicit raw ``fz Monitor`` series for an independent sum check."""
    raw_dir = case_path / "raw_star" / "out_put"
    if not raw_dir.is_dir():
        return []
    for path in sorted(raw_dir.glob("*.csv")):
        try:
            data = read_star_export_csv(path)
        except (OSError, ValueError):
            continue
        rows = data.get("rows", [])
        if rows and LEGACY_UNDERBODY_TOTAL_COLUMN in rows[0]:
            return rows
    return []


def _number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _numeric_series(rows: list[dict[str, str]], column: str) -> tuple[list[int], list[float]]:
    indices: list[int] = []
    values: list[float] = []
    for index, row in enumerate(rows):
        value = _number(row.get(column))
        if value is not None:
            indices.append(index)
            values.append(value)
    return indices, values


def _nonzero_examples(rows: list[dict[str, str]], column: str, tolerance: float) -> list[dict[str, Any]]:
    examples = []
    for index, row in enumerate(rows):
        value = _number(row.get(column))
        if value is not None and abs(value) > tolerance:
            examples.append({"row": index, "value": value})
            if len(examples) >= 10:
                break
    return examples


def _is_no_jet_case(manifest: dict[str, Any], active_jets: set[int]) -> bool:
    case_type = str(manifest.get("case_type", "")).lower()
    return not active_jets and case_type in {"no_jet", "nojet", "passive", "reference", "baseline"}


def _issue(report: dict[str, Any], category: str, severity: str, message: str, **details: Any) -> None:
    report["categories"][category].append({"severity": severity, "message": message, **details})


def _finalize(report: dict[str, Any]) -> dict[str, Any]:
    counts = {key: len(report["categories"][key]) for key in CATEGORY_KEYS}
    blocking = sum(counts[key] for key in BLOCKING_CATEGORIES)
    report["summary"] = {
        "category_counts": counts,
        "blocking_issue_count": blocking,
        "run_success_flag": blocking == 0,
        "overall_status": "PASS" if blocking == 0 else "FAIL",
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 B04 真实数据质量检查交付物")
    parser.add_argument("case_dirs", nargs="+", help="标准算例目录")
    parser.add_argument("--output-dir", default="artifacts/reports")
    parser.add_argument("--expected-case-count", type=int, default=3)
    parser.add_argument("--allowed-active-jets", default="2,6", help="允许开启的喷气口编号，逗号分隔")
    args = parser.parse_args(argv)
    allowed = [int(value.strip()) for value in args.allowed_active_jets.split(",") if value.strip()]
    result = run_delivery(args.case_dirs, output_dir=args.output_dir, expected_case_count=args.expected_case_count, allowed_active_jets=allowed)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if all(report["summary"]["run_success_flag"] for report in result["reports"]) and result["available_case_count"] >= result["expected_case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
