"""train_gen_data：读 manifest 初始瞬态裁剪契约，生成可训练的连续时序表。

裁剪值以 ``case_manifest.yaml`` 的 ``initial_transient_crop.end_time_s`` 为唯一
真源（经 ``flow_control.data_schema.initial_transient_crop_end_s`` 读取，字段
缺失时回退契约默认 0.5 s），本模块不做自动稳态检测。动作表 join 语义遵循
manifest 声明的 ``sample_ownership_rule``：JET 开关与命令质量流量以动作表行为
权威，实际质量流量与六区力以 timeseries 行为权威；``physical_time`` 小于裁剪
值的行视为吹入初始瞬态整体剔除，不进入训练表。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from flow_control.b53_dataset import discover_case_dirs
from flow_control.case_paths import find_case_timeseries_path
from flow_control.data_schema import initial_transient_crop_end_s
from flow_control.sampling import (
    SAMPLE_OWNERSHIP_EMBEDDED,
    SAMPLE_OWNERSHIP_LEFT_CLOSED,
    SAMPLE_OWNERSHIP_RIGHT_CLOSED,
    SCHEDULE_WINDOW_TOLERANCE_S,
    ScheduleWindowError,
    locate_schedule_window,
    parse_schedule_windows,
    resolve_declared_ownership,
    schedule_window_id_lookup,
    schedule_window_spans,
    validate_embedded_window,
)

N_JETS = 24
REGION_COLUMNS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
# 与 docs/week4/B01_final_data_contract.md 同源；包内自有定义，避免 B53 重构波及训练表。
REGION_FORCE_ALIASES = {
    "Fz_S1L": ("Fz_S1L", "underbody_lift_s1l"),
    "Fz_S1R": ("Fz_S1R", "underbody_lift_s1r"),
    "Fz_S2L": ("Fz_S2L", "underbody_lift_s2l"),
    "Fz_S2R": ("Fz_S2R", "underbody_lift_s2r"),
    "Fz_S3L": ("Fz_S3L", "underbody_lift_s3l"),
    "Fz_S3R": ("Fz_S3R", "underbody_lift_s3r"),
}
TOTAL_FORCE_COLUMN = "Fz_Total"
TOTAL_FORCE_ALIASES = ("Fz_Total", "vehicle_lift")
JET_COLUMNS = tuple(f"JET_{index:02d}" for index in range(1, N_JETS + 1))
COMMAND_COLUMNS = tuple(f"cmd_massflow_{index:02d}" for index in range(1, N_JETS + 1))
ACTUAL_COLUMNS = tuple(f"actual_massflow_{index:02d}" for index in range(1, N_JETS + 1))

TRAINING_TABLE_FIELDS = (
    "source_case_id",
    "physical_time",
    "window_id",
    *JET_COLUMNS,
    *COMMAND_COLUMNS,
    *ACTUAL_COLUMNS,
    *REGION_COLUMNS,
    TOTAL_FORCE_COLUMN,
    "source_timeseries_row",
)

SUMMARY_FIELDS = (
    "case_id",
    "case_dir",
    "cutoff_s",
    "cutoff_source",
    "timeseries_rows",
    "removed_transient_rows",
    "kept_rows",
    "dropped_missing_value_rows",
    "first_kept_time_s",
    "last_time_s",
    "alignment_mode",
    "status",
    "reason_codes",
    "notes",
)

DEFAULT_OUTPUT_DIR = "artifacts/train_gen_data"
SCHEMA_VERSION = "train_gen_data_v1"


@dataclass
class _CaseOutcome:
    summary: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)


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


def _schedule_path(case_dir: Path) -> Path | None:
    for candidate in (case_dir / "actuation_schedule.csv", case_dir / "input" / "actuation_schedule.csv"):
        if candidate.is_file():
            return candidate
    return None


def _load_manifest(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "case_manifest.yaml"
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _case_id(case_dir: Path, manifest: Mapping[str, Any]) -> str:
    return str(manifest.get("case_id") or case_dir.name)


def _cutoff_with_source(manifest: Mapping[str, Any]) -> tuple[float, str]:
    """裁剪值一律取自契约读取函数；来源判据与其内部逻辑保持一致。"""

    source = "fallback_default"
    crop = manifest.get("initial_transient_crop")
    if isinstance(crop, Mapping):
        try:
            value = float(crop.get("end_time_s"))
        except (TypeError, ValueError):
            value = math.nan
        if math.isfinite(value) and value >= 0.0:
            source = "manifest"
    return initial_transient_crop_end_s(manifest), source


def _alignment_mode_label(ownership: str, source: str) -> str:
    interval = {
        SAMPLE_OWNERSHIP_LEFT_CLOSED: "left_closed_[t_start,t_end)",
        SAMPLE_OWNERSHIP_RIGHT_CLOSED: "right_closed_(t_start,t_end]",
        SAMPLE_OWNERSHIP_EMBEDDED: "embedded_window_id",
    }.get(ownership, "undeclared")
    return f"{interval}_legacy_default" if source == "legacy_default" else interval


def _resolve_region_columns(headers: Sequence[str]) -> dict[str, str]:
    available = set(headers)
    return {
        canonical: next((alias for alias in aliases if alias in available), "")
        for canonical, aliases in REGION_FORCE_ALIASES.items()
    }


def _process_case(case_dir: Path) -> _CaseOutcome:
    manifest = _load_manifest(case_dir)
    case_id = _case_id(case_dir, manifest)
    cutoff, cutoff_source = _cutoff_with_source(manifest)
    ownership, ownership_source = resolve_declared_ownership(manifest)
    alignment_mode = _alignment_mode_label(ownership, ownership_source)
    notes: list[str] = []
    if cutoff_source == "fallback_default":
        notes.append("manifest 缺少合法 initial_transient_crop，回退契约默认 0.5 s")

    def fatal(reason: str, detail: str, **counts: Any) -> _CaseOutcome:
        summary = {name: "" for name in SUMMARY_FIELDS}
        summary.update(
            {
                "case_id": case_id,
                "case_dir": _path_text(case_dir),
                "cutoff_s": cutoff,
                "cutoff_source": cutoff_source,
                "alignment_mode": alignment_mode,
                "status": "REJECTED",
                "reason_codes": reason,
                "notes": "; ".join([*notes, detail]),
            }
        )
        summary.update(counts)
        return _CaseOutcome(summary=summary)

    timeseries_path = find_case_timeseries_path(case_dir)
    if not timeseries_path.is_file():
        return fatal("REQUIRED_FILE_MISSING", f"timeseries 不存在: {timeseries_path}")
    schedule_path = _schedule_path(case_dir)
    if schedule_path is None:
        return fatal("REQUIRED_FILE_MISSING", "缺少 actuation_schedule.csv（case 根目录或 input/）")
    headers, ts_rows = _read_csv(timeseries_path)
    _, schedule_rows = _read_csv(schedule_path)
    if not ts_rows:
        return fatal("REQUIRED_FILE_MISSING", "timeseries 无数据行")

    region_mapping = _resolve_region_columns(headers)
    total_source = next((alias for alias in TOTAL_FORCE_ALIASES if alias in headers), "")
    missing_regions = [region for region, source_name in region_mapping.items() if not source_name]
    if missing_regions or not total_source:
        detail = f"缺少六区力/总力列: {', '.join([*missing_regions, *([] if total_source else [TOTAL_FORCE_COLUMN])])}"
        return fatal(
            "SIX_REGION_FORCE_MISSING",
            detail,
            timeseries_rows=len(ts_rows),
            schedule_rows=len(schedule_rows),
        )
    missing_actual = [column for column in ACTUAL_COLUMNS if column not in headers]
    if missing_actual:
        return fatal(
            "ACTUAL_MASSFLOW_MISSING",
            f"缺少实际质量流量列: {', '.join(missing_actual)}",
            timeseries_rows=len(ts_rows),
            schedule_rows=len(schedule_rows),
        )

    times: list[float] = []
    for index, row in enumerate(ts_rows):
        value = _number(row.get("physical_time"))
        if value is None:
            return fatal(
                "TIME_MISALIGNMENT",
                f"timeseries 第 {index + 2} 行 physical_time 缺失或非数值",
                timeseries_rows=len(ts_rows),
                schedule_rows=len(schedule_rows),
            )
        if times and value <= times[-1]:
            return fatal(
                "TIME_MISALIGNMENT",
                f"timeseries 第 {index + 2} 行 physical_time 非严格递增",
                timeseries_rows=len(ts_rows),
                schedule_rows=len(schedule_rows),
            )
        times.append(value)

    try:
        starts, ends = parse_schedule_windows(schedule_rows, tolerance_s=SCHEDULE_WINDOW_TOLERANCE_S)
        spans = (
            schedule_window_spans(schedule_rows)
            if ownership == SAMPLE_OWNERSHIP_EMBEDDED
            else {}
        )
        window_lookup = (
            schedule_window_id_lookup(schedule_rows)
            if ownership == SAMPLE_OWNERSHIP_EMBEDDED
            else {}
        )
    except ScheduleWindowError as exc:
        return fatal(
            "TIME_MISALIGNMENT",
            str(exc),
            timeseries_rows=len(ts_rows),
            schedule_rows=len(schedule_rows),
        )

    aligned: list[dict[str, Any]] = []
    mismatch = 0
    for index, (time_value, row) in enumerate(zip(times, ts_rows)):
        if ownership == SAMPLE_OWNERSHIP_EMBEDDED:
            try:
                validate_embedded_window(
                    spans, row.get("window_id"), time_value, tolerance_s=SCHEDULE_WINDOW_TOLERANCE_S
                )
                key = int(float(str(row.get("window_id"))))
                schedule_index = window_lookup[key]
            except (ScheduleWindowError, KeyError, TypeError, ValueError):
                mismatch += 1
                continue
        else:
            try:
                schedule_index = locate_schedule_window(
                    starts,
                    ends,
                    time_value,
                    ownership=ownership,
                    clamp_tolerance_s=SCHEDULE_WINDOW_TOLERANCE_S,
                )
            except ScheduleWindowError:
                mismatch += 1
                continue
            observed = row.get("window_id")
            if observed not in (None, ""):
                declared = schedule_rows[schedule_index].get("window_id")
                try:
                    same_window = int(float(str(observed))) == int(float(str(declared)))
                except (TypeError, ValueError):
                    same_window = False
                if not same_window:
                    mismatch += 1
                    continue
        schedule_row = schedule_rows[schedule_index]
        joined: dict[str, Any] = {
            "source_case_id": case_id,
            "physical_time": time_value,
            "window_id": schedule_row.get("window_id", ""),
            "source_timeseries_row": index + 2,
        }
        for column in JET_COLUMNS:
            joined[column] = int((_number(schedule_row.get(column)) or 0.0) > 0.5)
        for column in COMMAND_COLUMNS:
            joined[column] = _number(schedule_row.get(column)) or 0.0
        for column in ACTUAL_COLUMNS:
            joined[column] = _number(row.get(column))
        for region in REGION_COLUMNS:
            joined[region] = _number(row.get(region_mapping[region]))
        joined[TOTAL_FORCE_COLUMN] = _number(row.get(total_source))
        aligned.append(joined)

    if mismatch:
        return fatal(
            "TIME_MISALIGNMENT",
            f"{mismatch} 行在 {alignment_mode} 语义下与动作表不一致",
            timeseries_rows=len(ts_rows),
            schedule_rows=len(schedule_rows),
        )

    removed = sum(1 for item in aligned if item["physical_time"] < cutoff)
    kept = [item for item in aligned if item["physical_time"] >= cutoff]
    if not kept:
        return fatal(
            "NO_ROWS_AFTER_CROP",
            f"裁剪值 {cutoff:g} s 不早于任何样本（末样本 {times[-1]:g} s），无剩余训练行",
            timeseries_rows=len(ts_rows),
            schedule_rows=len(schedule_rows),
            removed_transient_rows=removed,
        )

    dropped = 0
    dropped_actual = 0
    dropped_force = 0
    table_rows: list[dict[str, Any]] = []
    for item in kept:
        actual_missing = any(item[column] is None for column in ACTUAL_COLUMNS)
        force_missing = any(item[region] is None for region in REGION_COLUMNS)
        if actual_missing or force_missing:
            dropped += 1
            dropped_actual += actual_missing
            dropped_force += force_missing
            continue
        table_rows.append(item)
    if not table_rows:
        reason = "ACTUAL_MASSFLOW_MISSING" if dropped_actual else "SIX_REGION_FORCE_MISSING"
        return fatal(
            reason,
            f"裁剪后 {len(kept)} 行全部因缺失值被剔除",
            timeseries_rows=len(ts_rows),
            schedule_rows=len(schedule_rows),
            removed_transient_rows=removed,
            dropped_missing_value_rows=dropped,
        )

    summary = {name: "" for name in SUMMARY_FIELDS}
    summary.update(
        {
            "case_id": case_id,
            "case_dir": _path_text(case_dir),
            "cutoff_s": cutoff,
            "cutoff_source": cutoff_source,
            "timeseries_rows": len(ts_rows),
            "removed_transient_rows": removed,
            "kept_rows": len(table_rows),
            "dropped_missing_value_rows": dropped,
            "first_kept_time_s": table_rows[0]["physical_time"],
            "last_time_s": table_rows[-1]["physical_time"],
            "alignment_mode": alignment_mode,
            "status": "PASS",
            "notes": "; ".join(notes),
        }
    )
    return _CaseOutcome(summary=summary, rows=table_rows)


def build_training_tables(
    case_dirs: Sequence[str | Path],
    output_dir: str | Path,
) -> dict[str, Any]:
    """逐 Case 裁瞬态、对齐动作表并写训练表与裁剪摘要，返回 JSON 可序列化报告。"""

    output = Path(output_dir)
    outcomes = [_process_case(Path(case_dir)) for case_dir in case_dirs]
    for outcome in outcomes:
        if outcome.rows:
            _write_csv(
                output / str(outcome.summary["case_id"]) / "training_table.csv",
                TRAINING_TABLE_FIELDS,
                outcome.rows,
            )
    _write_csv(
        output / "transient_crop_summary.csv",
        SUMMARY_FIELDS,
        [outcome.summary for outcome in outcomes],
    )
    ok = sum(1 for outcome in outcomes if outcome.summary["status"] == "PASS")
    return {
        "schema_version": SCHEMA_VERSION,
        "cases": len(outcomes),
        "ok": ok,
        "rejected": len(outcomes) - ok,
        "total_kept_rows": sum(len(outcome.rows) for outcome in outcomes),
        "output_dir": _path_text(output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", action="append", default=[], metavar="PATH", help="可重复指定标准 Case 目录")
    parser.add_argument("--input-root", action="append", default=[], help="递归发现标准 Case；仅在显式传入时扫描")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--require-data", action="store_true", help="没有产出任何训练行时返回非零")
    args = parser.parse_args(argv)
    case_dirs: list[str] = list(args.case_dir)
    for root in args.input_root:
        case_dirs.extend(path.as_posix() for path in discover_case_dirs(root))
    report = build_training_tables(case_dirs, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.require_data and report["ok"] == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
