"""Organize STAR/CCM output files into the current Week4 case structure."""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from flow_control.sampling import (
    SAMPLE_OWNERSHIP_AUTO,
    SAMPLE_OWNERSHIP_EMBEDDED,
    SAMPLE_OWNERSHIP_MODES,
    ScheduleWindowError,
    locate_schedule_window,
    manifest_sample_ownership,
    normalize_sample_ownership,
    parse_schedule_windows,
    schedule_window_id_lookup,
    schedule_window_spans,
    validate_embedded_window,
)
from starccm.control.control_spec import JET_COLUMNS

from .case_data_loader import current_git_commit, load_case, write_quality_report
from .ccm_package import _standard_timeseries_rows, package_ccm_run_case
from .figures_generator import generate_all_figures
from .star_export_reader import discover_star_export_csvs, read_star_export_bundle


def organize_ccm_outputs(
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    star_output_dir: str | Path | None = None,
    overwrite: bool = False,
    manifest: dict[str, Any] | None = None,
    run_quality_check: bool = False,
    sample_ownership: str = SAMPLE_OWNERSHIP_AUTO,
) -> dict[str, Any]:
    """将 Week4 形式的 STAR 监视器 CSV 目录整理为标准 Case。"""
    source_path = Path(input_dir).expanduser().resolve()
    star_source_path = (
        Path(star_output_dir).expanduser().resolve()
        if star_output_dir is not None
        else source_path
    )
    target = Path(output_dir).expanduser().resolve()
    if not source_path.is_dir():
        raise NotADirectoryError(f"input directory not found: {source_path}")
    if not star_source_path.is_dir():
        raise NotADirectoryError(f"STAR output directory not found: {star_source_path}")
    if target.exists() and any(target.iterdir()) and not overwrite:
        raise FileExistsError(f"target case is not empty: {target}; use --force to overwrite generated files")

    schedule = _find_schedule(source_path)
    schedule_rows = _read_csv(schedule)
    case_type = _infer_case_type(schedule_rows)
    product_dir, star_files = _find_monitor_outputs(star_source_path)
    input_product_dir = schedule.parent
    input_files = _collect_input_files(input_product_dir)
    raw_files = sorted(path for path in product_dir.rglob("*") if path.is_file())
    monitor_rows = read_star_export_bundle(star_files)["rows"] if star_files else []
    runtime_path = product_dir / "timeseries.csv"
    runtime_raw = _read_csv(runtime_path) if runtime_path.is_file() else []
    ownership_mode = _resolve_sample_ownership(
        runtime_raw or monitor_rows, requested=sample_ownership, manifest=manifest
    )
    runtime_rows = (
        _standard_timeseries_rows(runtime_raw, schedule_rows, ownership=ownership_mode)
        if runtime_raw
        else []
    )
    consolidated_rows = _merge_rows_by_physical_time(runtime_rows, monitor_rows)
    consolidated = _attach_schedule(consolidated_rows, schedule_rows, ownership=ownership_mode)
    with tempfile.TemporaryDirectory(prefix="flow_control_organize_") as temp_dir:
        consolidated_path = Path(temp_dir) / "star_monitor_merged.csv"
        _write_csv(consolidated_path, consolidated)
        _package(
            consolidated_path,
            schedule,
            target,
            case_type,
            manifest=manifest,
            sample_ownership=ownership_mode,
        )

    input_target = target / "input"
    _copy_files(input_product_dir, input_target, input_files)

    raw_dir = target / "raw_star" / "out_put"
    _copy_files(product_dir, raw_dir, raw_files)

    report_path = target / "quality_report.json"
    source_files = [
        str(Path("raw_star") / "out_put" / path.relative_to(product_dir))
        for path in raw_files
    ]
    if run_quality_check:
        report = write_quality_report(target, require_complete_schema=True, check_mode="ccm")
        checked = load_case(target, require_complete_schema=True, check_mode="ccm")
        figures = generate_all_figures(checked, target / "figures")
        report["figures"] = {
            name: str(path.relative_to(target)) if path else None
            for name, path in figures.items()
        }
        report["status"] = "organized_and_checked"
    else:
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        report["status"] = "organized_only"
    report.update({"check_mode": "ccm", "source_files": source_files})
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "case_dir": target,
        "timeseries_path": target / "processed" / "timeseries.csv",
        "schedule_path": target / "input" / "actuation_schedule.csv",
        "raw_star_dir": raw_dir,
        "sample_ownership": ownership_mode,
        "quality_report_path": report_path,
        "quality_report": report,
    }


def _collect_input_files(input_product_dir: Path) -> list[Path]:
    """收集输入侧产物。

    标准 input/ 目录整树复制；兼容动作表放在 case 根目录的旧结构时，
    只复制根目录文件，避免把 raw_star/processed/ 等再嵌入 input/。
    """
    if input_product_dir.name == "input":
        return sorted(path for path in input_product_dir.rglob("*") if path.is_file())
    return sorted(path for path in input_product_dir.iterdir() if path.is_file())


def _copy_files(source_root: Path, target_root: Path, files: list[Path]) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for source in files:
        target_path = target_root / source.relative_to(source_root)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target_path.resolve():
            shutil.copy2(source, target_path)


def _find_schedule(input_dir: Path) -> Path:
    candidates = (
        input_dir / "input" / "actuation_schedule.csv",
        input_dir / "actuation_schedule.csv",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "input directory must contain input/actuation_schedule.csv or actuation_schedule.csv: "
        f"{input_dir}"
    )


def _find_monitor_outputs(input_dir: Path) -> tuple[Path, list[Path]]:
    candidates = (
        input_dir / "raw_star" / "output",
        input_dir / "output",
        input_dir / "raw_star" / "out_put",
        input_dir / "out_put",
        input_dir / "raw_star",
        input_dir,
    )
    for directory in candidates:
        if not directory.is_dir():
            continue
        files = [
            path
            for path in discover_star_export_csvs(directory)
            if path.name not in {"timeseries.csv", "actuation_schedule.csv"}
        ]
        if files or (directory / "timeseries.csv").is_file():
            return directory, files
    raise ValueError(
        "no recognized STAR monitor CSV files found under input directory; expected files in "
        "raw_star/out_put/, out_put/, raw_star/, or the input root"
    )


def _infer_case_type(schedule_rows: list[dict[str, Any]]) -> str:
    for row in schedule_rows:
        if any(abs(float(row.get(column, 0.0) or 0.0)) > 1.0e-15 for column in (*JET_COLUMNS, *MASSFLOW_COLUMNS)):
            return "jet_on"
    return "no_jet"


def _package(
    runtime_csv: Path,
    schedule: Path,
    target: Path,
    case_type: str,
    *,
    manifest: dict[str, Any] | None = None,
    sample_ownership: str = SAMPLE_OWNERSHIP_AUTO,
) -> None:
    manifest_data = dict(manifest or {})
    manifest_data.update(
        {
            "case_id": target.name,
            "case_type": case_type,
            "case_stage": "starccm_output_organized",
            "git_commit": current_git_commit(),
            "source_product_dir": "raw_star/out_put",
        }
    )
    actuation = dict(manifest_data.get("actuation") or {})
    actuation["sample_ownership_rule"] = sample_ownership
    manifest_data["actuation"] = actuation
    package_ccm_run_case(
        ccm_timeseries_path=runtime_csv,
        schedule_path=schedule,
        case_dir=target,
        manifest=manifest_data,
        require_complete_schema=False,
        run_quality_check=False,
        generate_figures=False,
        sample_ownership=sample_ownership,
    )


def _merge_rows_by_physical_time(
    runtime_rows: list[dict[str, Any]],
    monitor_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Outer-merge runtime and split monitor rows, preferring monitor exports."""

    merged: dict[float, dict[str, Any]] = {}
    for rows in (runtime_rows, monitor_rows):
        for row in rows:
            sample_time = float(row.get("physical_time", 0.0))
            key = round(sample_time, 12)
            merged.setdefault(key, {"physical_time": sample_time}).update(row)
    if not merged:
        raise ValueError("CCM output contains no runtime or monitor rows")
    return [merged[key] for key in sorted(merged)]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"CSV contains no rows: {path}")
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _embedded_window_id(row: dict[str, Any]) -> int | None:
    raw = row.get("window_id")
    if raw in (None, ""):
        return None
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return None


def _resolve_sample_ownership(
    output_rows: list[dict[str, Any]],
    *,
    requested: Any,
    manifest: dict[str, Any] | None = None,
) -> str:
    """把请求的 ownership 解析成一个具体模式，无法证明时拒绝猜测。"""
    mode = normalize_sample_ownership(requested)
    if mode is None:
        raise ScheduleWindowError(
            f"unknown sample_ownership {requested!r}; expected one of {list(SAMPLE_OWNERSHIP_MODES)}"
        )
    if mode != SAMPLE_OWNERSHIP_AUTO:
        return mode
    declared = manifest_sample_ownership(manifest)
    if declared is not None and declared != SAMPLE_OWNERSHIP_AUTO:
        return declared
    if output_rows and all(_embedded_window_id(row) is not None for row in output_rows):
        return SAMPLE_OWNERSHIP_EMBEDDED
    raise ScheduleWindowError(
        "monitor-only rows carry no window_id, so sample_ownership cannot be proven; "
        "pass --sample-ownership left_closed|right_closed, or declare "
        "actuation.sample_ownership_rule in case_manifest.yaml"
    )


def _attach_schedule(
    output_rows: list[dict[str, Any]],
    schedule_rows: list[dict[str, Any]],
    *,
    ownership: str = SAMPLE_OWNERSHIP_AUTO,
) -> list[dict[str, Any]]:
    if not output_rows:
        raise ValueError("CCM output contains no rows")
    mode = _resolve_sample_ownership(output_rows, requested=ownership)
    starts, ends = parse_schedule_windows(schedule_rows)
    window_lookup = schedule_window_id_lookup(schedule_rows)
    window_spans = schedule_window_spans(schedule_rows)
    merged: list[dict[str, Any]] = []
    for row_idx, output in enumerate(output_rows):
        raw_time = output.get("physical_time")
        if raw_time in (None, ""):
            raise ScheduleWindowError(
                f"output row {row_idx} has no physical_time; cannot resolve sample ownership"
            )
        sample_time = float(raw_time)
        if mode == SAMPLE_OWNERSHIP_EMBEDDED:
            window_id = _embedded_window_id(output)
            # 一个 window_id 跨多行采样，必须用整个窗口跨度校验，而不是首行边界；
            # window_id 缺失、不在动作表中或与样本时间矛盾都由该校验抛错
            hit_start, hit_end = validate_embedded_window(window_spans, window_id, sample_time)
            schedule_idx = window_lookup[window_id]
        else:
            schedule_idx = locate_schedule_window(starts, ends, sample_time, ownership=mode)
            hit_start, hit_end = starts[schedule_idx], ends[schedule_idx]
        schedule = schedule_rows[schedule_idx]
        record = dict(output)
        record["window_id"] = int(float(schedule.get("window_id", schedule_idx)))
        # 命中的窗口边界写回输出，使对齐结果可事后审计
        record["t_start"] = hit_start
        record["t_end"] = hit_end
        for column in (*JET_COLUMNS, *MASSFLOW_COLUMNS):
            record[column] = schedule.get(column, 0.0)
        merged.append(record)
    return merged
