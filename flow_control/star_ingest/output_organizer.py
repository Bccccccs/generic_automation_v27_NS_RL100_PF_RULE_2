"""Organize STAR/CCM output files into the current Week4 case structure."""

from __future__ import annotations

import bisect
import csv
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from starccm.control.control_spec import JET_COLUMNS

from .case_data_loader import current_git_commit
from .ccm_package import package_ccm_run_case
from .star_export_reader import discover_star_export_csvs, read_star_export_bundle


def organize_ccm_outputs(
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    star_output_dir: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Path]:
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
    bundle = read_star_export_bundle(star_files)
    consolidated = _attach_schedule(bundle["rows"], schedule_rows)
    with tempfile.TemporaryDirectory(prefix="flow_control_organize_") as temp_dir:
        consolidated_path = Path(temp_dir) / "star_monitor_merged.csv"
        _write_csv(consolidated_path, consolidated)
        _package(consolidated_path, schedule, target, case_type)

    input_target = target / "input"
    _copy_files(input_product_dir, input_target, input_files)

    raw_dir = target / "raw_star" / "out_put"
    _copy_files(product_dir, raw_dir, raw_files)

    report_path = target / "quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    report.update(
        {
            "status": "organized_only",
            "check_mode": "ccm",
            "source_files": [
                str(Path("raw_star") / "out_put" / path.relative_to(product_dir))
                for path in raw_files
            ],
        }
    )
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "case_dir": target,
        "timeseries_path": target / "processed" / "timeseries.csv",
        "schedule_path": target / "input" / "actuation_schedule.csv",
        "raw_star_dir": raw_dir,
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
        if files:
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


def _package(runtime_csv: Path, schedule: Path, target: Path, case_type: str) -> None:
    package_ccm_run_case(
        ccm_timeseries_path=runtime_csv,
        schedule_path=schedule,
        case_dir=target,
        manifest={
            "case_id": target.name,
            "case_type": case_type,
            "case_stage": "starccm_output_organized",
            "git_commit": current_git_commit(),
            "source_product_dir": "raw_star/out_put",
        },
        require_complete_schema=False,
        run_quality_check=False,
        generate_figures=False,
    )


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


def _attach_schedule(
    output_rows: list[dict[str, Any]], schedule_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not output_rows:
        raise ValueError("CCM output contains no rows")
    ends = [float(row["t_end"]) for row in schedule_rows]
    merged: list[dict[str, Any]] = []
    for row_idx, output in enumerate(output_rows):
        if len(output_rows) == len(schedule_rows):
            schedule = schedule_rows[row_idx]
        else:
            sample_time = float(output.get("physical_time", 0.0))
            schedule_idx = min(bisect.bisect_left(ends, sample_time - 1.0e-12), len(schedule_rows) - 1)
            schedule = schedule_rows[schedule_idx]
        record = dict(output)
        record["window_id"] = int(float(schedule.get("window_id", row_idx)))
        for column in (*JET_COLUMNS, *MASSFLOW_COLUMNS):
            record[column] = schedule.get(column, 0.0)
        merged.append(record)
    return merged
