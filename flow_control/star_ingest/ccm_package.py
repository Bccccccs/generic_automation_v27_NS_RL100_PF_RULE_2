"""
Package CCM runtime output into a standard case and run star_ingest checks.

将 CCM(Co-Simulation Manager,联合仿真管理器)的运行时输出数据
打包为标准 Case 目录格式,然后运行 star_ingest 的质量检查。

CCM 运行时输出与 STAR 导出的 CSV 格式不同:
- CCM 的输出列使用 REPORT_TO_STANDARD 映射中的名称
- 喷气阀的实际质量流量必须来自 STAR actual_massflow 报告，不得由指令流量合成
- CCM 的检查模式为 "ccm"(允许 cmd=actual 在某些场景中)
"""


from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import yaml

from flow_control.case_paths import case_timeseries_path
from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from starccm.control.control_spec import JET_COLUMNS, LOAD_COLUMNS

from .case_data_loader import CASE_REQUIRED_DIRS, load_case, write_quality_report
from .figures_generator import generate_all_figures
from .star_export_reader import normalize_actual_massflow

# 实际质量流量列名(24 个阀门)
ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))
ACTUAL_MASSFLOW_PATTERN = re.compile(
    r"^(?:actual|real)[_\s-]*mass[_\s-]*flow[_\s-]*(\d{1,2})$",
    re.IGNORECASE,
)
STAR_JET_MASSFLOW_REPORT_PATTERN = re.compile(
    r"^j[_\s-]?(\d{1,2})[_\s-]*mass[_\s-]*flow(?:[_\s-]*report)?$",
    re.IGNORECASE,
)

# CCM 运行时报告列名  → 标准列名的映射表。
# CCM 输出使用 "total"、"drag"、"fc_load_S1L" 等列名,
# 需要映射为标准名称如 "Fz_Total"、"Drag_Total"、"Fz_S1L"。
REPORT_TO_STANDARD = {
    "total": "Fz_Total",
    "drag": "Drag_Total",
    "fc_load_S1L": "Fz_S1L",
    "fc_load_S1R": "Fz_S1R",
    "fc_load_S2L": "Fz_S2L",
    "fc_load_S2R": "Fz_S2R",
    "fc_load_S3L": "Fz_S3L",
    "fc_load_S3R": "Fz_S3R",
}


def package_ccm_run_case(
    *,
    ccm_timeseries_path: str | Path,
    schedule_path: str | Path,
    case_dir: str | Path,
    manifest: dict[str, Any] | None = None,
    require_complete_schema: bool = True,
    run_quality_check: bool = True,
    generate_figures: bool = True,
) -> dict[str, Any]:
    """Write standard case files from CCM's raw runtime CSV and check them.

    从 CCM 的原始运行时 CSV 文件生成标准 Case 文件并执行质量检查。
    这是 CCM 运行时输出 → 标准 Case 的入口函数。

    处理步骤:
    1. 读取 CCM 运行时 CSV 和驱动指令 CSV
    2. 将 CCM 列名转换为标准列名
    3. 写入 processed/timeseries.csv
    4. 写入 actuation_schedule.csv
    5. 写入 case_manifest.yaml(自动填充默认值)
    6. 创建标准目录
    7. 执行 write_quality_report 验证
    8. 生成力、喷气时序、质量流量对齐和质量摘要图
    """

    raw_rows = _read_csv_rows(ccm_timeseries_path)
    schedule_rows = _read_csv_rows(schedule_path)
    rows = _standard_timeseries_rows(raw_rows, schedule_rows)
    case_path = Path(case_dir)
    case_path.mkdir(parents=True, exist_ok=True)
    for directory_name in CASE_REQUIRED_DIRS:
        (case_path / directory_name).mkdir(exist_ok=True)

    timeseries_path = case_timeseries_path(case_path)
    _write_csv(timeseries_path, _ordered_timeseries_columns(rows), rows)
    schedule_columns = list(schedule_rows[0]) if schedule_rows else ["physical_time"]
    _write_csv(case_path / "actuation_schedule.csv", schedule_columns, schedule_rows)
    _write_csv(case_path / "input" / "actuation_schedule.csv", schedule_columns, schedule_rows)

    manifest_data = dict(manifest or {})
    manifest_data.setdefault(
        "star",
        {
            "version": "待浩坤确认",
            "sim_file": "待浩坤确认",
            "sim_file_hash_sha256": "待浩坤确认",
            "geometry_version": "待浩坤确认",
            "mesh_version": "待浩坤确认",
            "region_names": ["减运算"],
        },
    )
    manifest_data.setdefault("geometry_version", "starccm-runtime")
    manifest_data.setdefault("mesh_version", "unknown")
    manifest_data.setdefault("flow_velocity", 0.0)
    manifest_data.setdefault("gap", 0.0)
    manifest_data.setdefault("time_step", _infer_time_step(rows))
    manifest_data.setdefault("jet_amplitude", _max_total_massflow(schedule_rows))
    manifest_data.setdefault("window_duration", _infer_time_step(rows))
    manifest_data.setdefault("random_seed", 0)
    manifest_data.setdefault("case_type", "jet_on")
    manifest_data.setdefault("case_stage", "starccm_runtime")
    manifest_data["check_mode"] = "ccm"
    manifest_data["validation_mode"] = "full_case" if require_complete_schema else "partial_timeseries"
    manifest_data.setdefault("source_ccm_timeseries", str(ccm_timeseries_path))
    manifest_data.setdefault("source_schedule", str(schedule_path))
    manifest_data.setdefault("units", {"force": "N", "moment": "N-m", "massflow": "kg/s"})
    manifest_data.setdefault(
        "sign_convention",
        (
            "jet massflow is positive for injection into the flow domain; "
            "negative STAR inlet-report values are normalized to positive magnitudes; "
            "force and moment values preserve the STAR-CCM+ report convention"
        ),
    )
    (case_path / "case_manifest.yaml").write_text(
        yaml.safe_dump(manifest_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (case_path / "quality_report.json").write_text("{}", encoding="utf-8")
    (case_path / "notes.md").write_text(
        "# STAR-CCM+ runtime case\n\n"
        "Generated from STAR runtime reports and actuation_schedule.csv, then checked by star_ingest.\n",
        encoding="utf-8",
    )
    quality_report = (
        write_quality_report(
            case_path,
            require_complete_schema=require_complete_schema,
            check_mode="ccm",
        )
        if run_quality_check
        else {}
    )
    figures: dict[str, Path | None] = {}
    if generate_figures:
        checked_case = load_case(
            case_path,
            require_complete_schema=require_complete_schema,
            check_mode="ccm",
        )
        figures = generate_all_figures(checked_case, case_path / "figures")
        quality_report["figures"] = {
            name: str(path.relative_to(case_path)) if path else None
            for name, path in figures.items()
        }
        (case_path / "quality_report.json").write_text(
            json.dumps(quality_report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return {
        "case_dir": case_path,
        "timeseries_path": timeseries_path,
        "quality_report_path": case_path / "quality_report.json",
        "quality_report": quality_report,
        "figures": figures,
    }


def _standard_timeseries_rows(
    raw_rows: list[dict[str, str]],
    schedule_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """
    将 CCM 原始运行时数据行转换为标准时间序列行。

    转换逻辑:
    1. 从驱动指令表中按 window_id 获取喷气阀门状态和质量流量指令
    2. 只读取 CCM/STAR 回写的 actual_massflow_NN；绝不由 cmd_massflow 合成。
    3. 将 CCM 的报告列名映射为标准列名
    4. 如果六个传感器都存在且 Fz_Total 缺失,则求和计算 Fz_Total

    参数:
        raw_rows: CCM 运行时输出的原始数据行
        schedule_rows: 驱动指令表行

    返回:
        标准化后的时间序列行列表
    """
    # 将驱动指令表按 window_id 建立索引,便于快速查找
    schedule_by_window = {
        int(float(row.get("window_id", idx))): row
        for idx, row in enumerate(schedule_rows)
    }
    rows: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_rows):
        window_id = int(float(raw.get("window_id", idx)))
        schedule = schedule_by_window.get(window_id, {})
        record: dict[str, Any] = {
            "physical_time": float(raw.get("physical_time", schedule.get("physical_time", idx))),
            "window_id": window_id,
        }
        # 从驱动指令表中复制喷气阀门开关状态
        for jet_column in JET_COLUMNS:
            record[jet_column] = float(schedule.get(jet_column, 0.0) or 0.0)
        # 从驱动指令表中复制指令质量流量
        for column in MASSFLOW_COLUMNS:
            record[column] = float(schedule.get(column, 0.0) or 0.0)
        # 映射 CCM 报告列名为标准列名
        for raw_column, raw_value in raw.items():
            standard = _standard_report_column(raw_column)
            if standard is not None and raw_value not in {None, ""}:
                value = float(raw_value)
                if _is_actual_massflow_column(standard):
                    value = normalize_actual_massflow(value)
                record[standard] = value
        # 浩坤确认：六传感器之和是 fz 车底六区合力，不能写成带车壳的 Fz_Total。
        if all(column in record for column in LOAD_COLUMNS) and "fz" not in record:
            record["fz"] = sum(float(record[column]) for column in LOAD_COLUMNS)
        record.setdefault("solver_status", "success")
        record["case_stage"] = "starccm_runtime"
        rows.append(record)
    return rows


def _standard_report_column(column: str) -> str | None:
    """
    将 CCM 报告的原始列名映射为标准列名。
    先检查是否为已知标准列名,再查 REPORT_TO_STANDARD 映射表,
    最后尝试去除 " Monitor" 后缀后查找映射。
    对于 physical_time 和 window_id 返回 None(不需要映射,直接使用)。
    """
    normalized = _strip_monitor_suffix(column.strip())
    if normalized in {"physical_time", "window_id"}:
        return None
    if normalized in ACTUAL_MASSFLOW_COLUMNS:
        return normalized
    actual_match = ACTUAL_MASSFLOW_PATTERN.match(normalized)
    if actual_match:
        idx = int(actual_match.group(1))
        if 1 <= idx <= 24:
            return f"actual_massflow_{idx:02d}"
    star_report_match = STAR_JET_MASSFLOW_REPORT_PATTERN.match(normalized)
    if star_report_match:
        idx = int(star_report_match.group(1))
        if 1 <= idx <= 24:
            return f"actual_massflow_{idx:02d}"
    if normalized in LOAD_COLUMNS or normalized in {"Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment", "Jet_Reaction_Z"}:
        return normalized
    if normalized in REPORT_TO_STANDARD:
        return REPORT_TO_STANDARD[normalized]
    return None


def _strip_monitor_suffix(column: str) -> str:
    suffix = " Monitor"
    if column.endswith(suffix):
        return column[: -len(suffix)]
    return column


def _is_actual_massflow_column(column: str | None) -> bool:
    return bool(column and column in ACTUAL_MASSFLOW_COLUMNS)


def _ordered_timeseries_columns(rows: list[dict[str, Any]]) -> list[str]:
    """
    返回按标准优先级排序的时间序列列名列表。
    优先级顺序: physical_time → window_id → JET → cmd_massflow
    → actual_massflow → 载荷列(LOAD_COLUMNS) → 全局量 → solver_status → case_stage
    → 其他未识别列(按字母排序以确保确定性)。
    """
    priority = (
        "physical_time",
        "window_id",
        *JET_COLUMNS,
        *MASSFLOW_COLUMNS,
        *ACTUAL_MASSFLOW_COLUMNS,
        *LOAD_COLUMNS,
        "Fz_Total",
        "Drag_Total",
        "Pitch_Moment",
        "Roll_Moment",
        "Jet_Reaction_Z",
        "solver_status",
        "case_stage",
    )
    present = set().union(*(row.keys() for row in rows)) if rows else set()
    return [column for column in priority if column in present] + sorted(present - set(priority))


def _read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    """
    读取 CSV 文件并返回字典列表(所有值保持字符串格式)。
    与 case_data_loader 中的版本不同,此处不做 float 转换,
    因为后续 _standard_timeseries_rows 会对每个字段单独处理。
    """
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    """
    将数据写入 CSV 文件。
    自动创建父目录,只写入指定的列(忽略行中的其他键)。
    使用 writer.writerows 批量写入(性能优化)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _infer_time_step(rows: list[dict[str, Any]]) -> float:
    """
    从时间序列的前两行推断时间步长。
    用于 Manifest 中未提供 time_step 时自动填充。
    如果数据少于 2 行则返回 0.0。
    """
    if len(rows) < 2:
        return 0.0
    return float(rows[1]["physical_time"]) - float(rows[0]["physical_time"])


def _max_total_massflow(rows: list[dict[str, str]]) -> float:
    """
    计算驱动指令表中所有阀门的最大总质量流量。
    用于 Manifest 中自动填充 jet_amplitude(喷气幅值)参数。
    """
    max_value = 0.0
    for row in rows:
        total = sum(float(row.get(column, 0.0) or 0.0) for column in MASSFLOW_COLUMNS)
        max_value = max(max_value, total)
    return max_value
