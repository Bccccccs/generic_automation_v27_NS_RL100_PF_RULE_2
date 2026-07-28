"""Load a case from a standard ``case_id/`` directory and return structured data.

Standard case directory layout::

    case_id/
        case_manifest.yaml      —  case metadata (geometry, mesh, flow params)
        actuation_schedule.csv   —  jet actuation commands per window
        timeseries.csv           —  force/moment sensor readings + jet states
        quality_report.json      —  pre-computed quality metrics
        input/                   —  actuation inputs used by the backend
        figures/                 —  auto-generated diagnostic plots
        logs/                    —  solver/runtime logs, if available
        flow_snapshots/           —  flow-field snapshots, if available
        notes.md                 —  human-readable notes (optional)

This module extends ``flow_control.data_schema.CaseSchema`` with additional
validation logic for the STAR-export ingest path.

该模块是 STAR 数据摄入流程的核心,负责:
1. 从 STAR 导出的 CSV 文件生成标准 Case 目录结构
2. 对 Case 进行完整的 7 步验证(文件完整性 → Manifest → 时间序列 → 质量检查 → 驱动指令 → 质量报告 → 日志)
3. 提供统一的 load_case 接口供后续处理步骤使用

标准 Case 目录结构:
- case_manifest.yaml: Case 元数据(几何、网格、流动参数等)
- actuation_schedule.csv: 每个窗口的喷气驱动指令
- timeseries.csv: 力/力矩传感器读数 + 喷气状态
- quality_report.json: 预计算的质量度量
- input/: 后端使用的驱动输入副本
- figures/: 自动生成的诊断图表
- logs/: 求解器/运行时日志
- flow_snapshots/: 流场快照
- notes.md: 人工阅读的备注(可选)
"""

from __future__ import annotations

import json
import csv
import logging
import subprocess
from pathlib import Path
from typing import Any

import yaml

from flow_control.case_paths import (
    case_timeseries_path,
    find_case_timeseries_path,
    legacy_case_timeseries_path,
)

from .star_export_reader import (
    discover_star_export_csvs,
    read_star_export_csv,
    read_star_export_bundle,
    compute_fz_total,
    FZ_SENSOR_COLUMNS,
    GLOBAL_COLUMNS,
    JET_COLUMNS,
    CMD_MASSFLOW_COLUMNS,
    ACTUAL_MASSFLOW_COLUMNS,
)
from .quality_checker import QualityChecker

# 日志记录器,命名空间为 "case_data_loader"
logger = logging.getLogger("case_data_loader")


def current_git_commit() -> str:
    """Return the current repository commit for generated-data provenance."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"

# The 6 Fz sensor columns + 5 global columns that every case must have
# 每个 Case 的时间序列中必须包含的基础列:
# - physical_time: 仿真物理时间
# - window_id: 窗口编号(用于识别不同驱动周期)
# - Fz_S1L ~ Fz_S3R: 六个底部力传感器的法向力
# - Fz_Total: 总法向力(六传感器之和或直接导出)
# - Drag_Total: 总阻力
# - Pitch_Moment: 俯仰力矩
# - Roll_Moment: 滚转力矩
# - Jet_Reaction_Z: 喷气反力(Z 方向)
# - solver_status: 求解器状态("success" / "diverged" 等)
REQUIRED_TIMESERIES_COLUMNS = (
    "physical_time",
    "window_id",
    "Fz_S1L",
    "Fz_S1R",
    "Fz_S2L",
    "Fz_S2R",
    "Fz_S3L",
    "Fz_S3R",
    "Fz_Total",
    "Drag_Total",
    "Pitch_Moment",
    "Roll_Moment",
    "Jet_Reaction_Z",
    "solver_status",
)

# 对于有喷气的 Case,额外必需的列(24 个阀门的开关 + 指令流量 + 实际流量)
JET_REQUIRED_EXTRA_COLUMNS = (
    *JET_COLUMNS,
    *CMD_MASSFLOW_COLUMNS,
    *ACTUAL_MASSFLOW_COLUMNS,
)

# 标准 Case 目录中必须存在的文件。timeseries 的当前标准位置是
# processed/timeseries.csv，读取侧兼容旧根目录 timeseries.csv。
CASE_REQUIRED_FILES = (
    "case_manifest.yaml",
    "actuation_schedule.csv",
    "quality_report.json",
)

# 可选文件
CASE_OPTIONAL_FILES = ("notes.md",)
# 必须存在的子目录。input/ 和 flow_snapshots/ 是历史/可选目录。
CASE_REQUIRED_DIRS = ("processed", "figures", "logs")

MOCK_CHECK_MODES = {"mock", "arx_use"}
CCM_CHECK_MODES = {"ccm", "star_ingest"}


def load_case(
    case_dir: str | Path,
    *,
    require_complete_schema: bool | None = None,
    check_mode: str | None = None,
) -> dict[str, Any]:
    """Load a complete case from a standard ``case_id/`` directory.

    Parameters
    ----------
    case_dir
        Path to the case directory (named after the ``case_id``).

    Returns
    -------
    dict with keys:
        - ``case_id``: directory basename
        - ``case_dir``: resolved ``Path``
        - ``manifest``: parsed YAML dict
        - ``timeseries``: list of row dicts
        - ``actuation_schedule``: list of row dicts
        - ``quality_report``: parsed JSON dict
        - ``notes``: notes.md text, or ``""``
        - ``figures_dir``: ``Path`` to figures/
        - ``has_jet_data``: bool — whether the case has jet columns
        - ``require_complete_schema``: bool — whether full required-column
          validation was applied
        - ``errors``: list of validation errors (empty = valid)
        - ``warnings``: list of validation warnings

    从标准 Case 目录加载完整的 Case 数据,并执行 7 步验证。
    这是整个流水线中所有后续处理(ROM 训练、推理等)的统一数据入口。

    加载步骤:
    1. 检查文件完整性
    2. 加载 Manifest(元数据)
    3. 加载时间序列数据
    4. 执行 7 项质量检查
    5. 加载驱动指令表
    6. 加载质量报告
    7. 加载备注文件
    """
    case_dir = Path(case_dir).resolve()
    case_id = case_dir.name

    # 初始化结果字典,设置合理的默认值
    result: dict[str, Any] = {
        "case_id": case_id,
        "case_dir": case_dir,
        "manifest": {},
        "timeseries": [],
        "actuation_schedule": [],
        "quality_report": {},
        "notes": "",
        "figures_dir": case_dir / "figures",
        "has_jet_data": False,
        "require_complete_schema": True,
        "check_mode": check_mode or "star_ingest",
        "errors": [],
        "warnings": [],
    }

    # ── 1. File completeness ──────────────────────────────────────────────
    # 第 1 步:检查必须的文件和目录是否存在
    missing_files = _check_files(case_dir)
    if missing_files:
        result["errors"].append(
            f"Missing required files: {', '.join(missing_files)}"
        )
        return result  # cannot continue without core files / 缺少核心文件,无法继续

    # ── 2. Load manifest ──────────────────────────────────────────────────
    # 第 2 步:加载 YAML 格式的 Case 元数据(几何、网格、流动参数、验证模式等)
    with (case_dir / "case_manifest.yaml").open("r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f) or {}
    result["manifest"] = manifest
    if check_mode is None:
        check_mode = str(manifest.get("check_mode", manifest.get("case_stage", "star_ingest")))
    result["check_mode"] = check_mode
    if require_complete_schema is None:
        validation_mode = str(manifest.get("validation_mode", "full_case")).lower()
        # partial_timeseries 模式允许只包含部分列(适用于多步摄入场景)
        require_complete_schema = validation_mode != "partial_timeseries"
    result["require_complete_schema"] = bool(require_complete_schema)

    # ── 3. Load timeseries ────────────────────────────────────────────────
    # 第 3 步:加载时间序列数据(力/力矩/喷气信号等)
    timeseries_path = find_case_timeseries_path(case_dir)
    result["timeseries"] = _read_csv_rows(timeseries_path)
    if not result["timeseries"]:
        result["errors"].append("timeseries.csv is empty")
        return result

    # Detect if this is a jet case.  Manifest case_type is authoritative when
    # present; column names are only a fallback for legacy cases.
    # 判断是否为喷气工况:
    # - Manifest 中的 case_type 字段具有最高优先级
    # - 列名前缀检测作为向后兼容的 fallback
    ts_columns = list(result["timeseries"][0].keys()) if result["timeseries"] else []
    result["has_jet_data"] = _is_jet_case(manifest, ts_columns)
    jet_case = result["has_jet_data"]

    # ── 4. Quality checks ─────────────────────────────────────────────────
    # 第 4 步:执行质量检查(共 7 个子检查)
    checker = QualityChecker()

    # 4a. Column completeness.  Partial STAR timeseries imports are allowed to
    # omit columns that will arrive from later exports; full cases are strict.
    # 检查列完整性。部分导入允许缺失列;完整导入要求所有列都存在。
    if require_complete_schema:
        col_errors = checker.check_required_columns(result["timeseries"], REQUIRED_TIMESERIES_COLUMNS)
        result["errors"].extend(col_errors)
    else:
        # 非完整模式只检查 physical_time 列存在即可
        result["errors"].extend(
            checker.check_required_columns(result["timeseries"], ("physical_time",))
        )

    if jet_case and require_complete_schema:
        jet_col_errors = checker.check_required_columns(result["timeseries"], JET_REQUIRED_EXTRA_COLUMNS)
        result["errors"].extend(jet_col_errors)
    elif not jet_case and (require_complete_schema or "Jet_Reaction_Z" in ts_columns):
        # No-jet case: Jet_Reaction_Z should be 0 or N/A when present.
        # 无喷气工况:检查 Jet_Reaction_Z 的合理性
        jrz_check = checker.check_no_jet_jrz(result["timeseries"])
        result["warnings"].extend(jrz_check)

    # 4b. Monotonic time / 时间单调性检查
    time_errs = checker.check_monotonic_time(result["timeseries"])
    result["errors"].extend(time_errs)

    # 4c. NaN detection / NaN 值检查
    nan_errs = checker.check_nan_values(result["timeseries"])
    result["errors"].extend(nan_errs)

    # 4d. Units / direction warning / 单位与方向检查
    unit_warns = checker.check_units_and_direction(manifest)
    result["warnings"].extend(unit_warns)

    if jet_case:
        # 4e. Jet on/off vs massflow consistency / 喷气开关与质量流量一致性
        jet_mf_errs = checker.check_jet_massflow_consistency(result["timeseries"])
        result["errors"].extend(jet_mf_errs)

        # 4f. cmd vs actual massflow separation / 指令与实际质量流量分离性
        mf_sep_errs = checker.check_massflow_separation(
            result["timeseries"],
            allow_identical_actual=check_mode in {"mock", "arx_use", "ccm"},
        )
        result["errors"].extend(mf_sep_errs)

        # 4g. Jet_Reaction_Z in jet case should be present / 喷气工况必须有 Jet_Reaction_Z
        if "Jet_Reaction_Z" not in ts_columns:
            result["errors"].append(
                "Jet case with active jets must include Jet_Reaction_Z column"
            )

    # ── 5. Load actuation schedule ────────────────────────────────────────
    # 第 5 步:加载喷气驱动时间表(每个窗口的喷气阀门开关指令)
    result["actuation_schedule"] = _read_csv_rows(case_dir / "actuation_schedule.csv")

    # ── 6. Load quality report ────────────────────────────────────────────
    # 第 6 步:加载已有的质量报告
    with (case_dir / "quality_report.json").open("r", encoding="utf-8") as f:
        result["quality_report"] = json.load(f)

    # ── 7. Load notes ─────────────────────────────────────────────────────
    # 第 7 步:加载可选的备注文件
    notes_path = case_dir / "notes.md"
    if notes_path.exists():
        result["notes"] = notes_path.read_text(encoding="utf-8")

    logger.info(
        "Loaded case %s: %d timeseries rows, %d errors, %d warnings",
        case_id,
        len(result["timeseries"]),
        len(result["errors"]),
        len(result["warnings"]),
    )
    return result


# ── Public helper: ingest STAR export into a case directory ──────────────────


def ingest_star_export(
    star_files: list[str | Path],
    *,
    case_dir: str | Path,
    manifest: dict[str, Any] | None = None,
    actuation_schedule: list[dict[str, Any]] | None = None,
    notes: str | None = None,
    overwrite: bool = False,
    require_complete_schema: bool = True,
    check_mode: str = "star_ingest",
    write_final_quality_report: bool = True,
) -> dict[str, Any]:
    """Ingest raw STAR-CCM+ export file(s) into a standard case directory.

    This is the primary entry point for the STAR → standard case pipeline.

    Parameters
    ----------
    star_files
        One or more STAR-CCM+ export CSV paths (e.g. ``["FZ.csv"]``,
        or ``["FZ.csv", "jet_commands.csv"]``).
    case_dir
        Target case directory (will be created if needed).
    manifest
        Optional case manifest dict (auto-populated with defaults).
    actuation_schedule
        Optional pre-built actuation schedule rows.
    notes
        Optional notes text for ``notes.md``.
    overwrite
        If ``False`` (default), raises ``FileExistsError`` when case exists.
    require_complete_schema
        If ``True`` (default), missing required full-case columns are errors.
        Set to ``False`` for a single STAR timeseries export that will be merged
        with other exports later.

    Returns
    -------
    dict — the result of :func:`load_case` after ingestion.

    将原始 STAR-CCM+ 导出文件摄取为标准 Case 目录。
    这是 STAR → 标准 Case 流水线的主要入口函数。

    处理流程:
    1. 读取 STAR 导出数据(单个或多个 CSV 文件合并)
    2. 计算衍生量(如 Fz_Total)
    3. 写入 timeseries.csv
    4. 写入 actuation_schedule.csv(含 input/ 备份)
    5. 写入 case_manifest.yaml(自动填充默认值)
    6. 创建标准子目录
    7. 写入 notes.md
    8. 运行 load_case 验证并生成质量报告
    """
    case_path = Path(case_dir)
    if case_path.exists() and not overwrite:
        raise FileExistsError(
            f"Case directory already exists: {case_path}. "
            f"Set overwrite=True to replace."
        )
    case_path.mkdir(parents=True, exist_ok=True)

    # 1. Read STAR export data
    # 读取 STAR 导出数据:单个文件或批量合并
    if len(star_files) == 1:
        data = read_star_export_csv(star_files[0])
    else:
        data = read_star_export_bundle(star_files)

    rows = data["rows"]

    # 2. Compute Fz_Total if all six bottom-force sensors are present.
    # Missing STAR exports stay missing so quality checks can report them.
    # 计算 Fz_Total(如果六个底部传感器都存在)
    # 缺失的数据保持缺失,由质量检查器报告,而不是默默填充零值
    compute_fz_total(rows)
    _add_common_timeseries_fields(
        rows,
        case_type=str((manifest or {}).get("case_type", "unknown")),
    )

    # Re-compute column ordering after derived columns are added.
    # 添加衍生列后重新计算列顺序
    present_cols = _ordered_columns_from_rows(rows) if rows else data["columns"]

    # 3. Write timeseries.csv / 写入时间序列 CSV
    _write_csv_rows(case_timeseries_path(case_path), present_cols, rows)

    # 4. Write actuation_schedule.csv.  The root copy is the standard case
    # schema; input/ keeps the backend command source for traceability.
    # 写入驱动指令 CSV:根目录是标准副本,input/ 下保存后端命令源用于追溯
    if actuation_schedule is not None:
        sch_cols = list(actuation_schedule[0].keys()) if actuation_schedule else ["physical_time"]
        _write_csv_rows(case_path / "actuation_schedule.csv", sch_cols, actuation_schedule)
        _write_csv_rows(case_path / "input" / "actuation_schedule.csv", sch_cols, actuation_schedule)
    else:
        # Write an empty schedule with just the header
        # 写入只有表头的空驱动指令表
        _write_csv_rows(case_path / "actuation_schedule.csv", ["physical_time"], [])
        _write_csv_rows(case_path / "input" / "actuation_schedule.csv", ["physical_time"], [])

    # 5. Write case_manifest.yaml / 写入 Case 元数据(自动填充默认值)
    manifest_data = manifest or {}
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
    manifest_data.setdefault("geometry_version", "unknown")
    manifest_data.setdefault("mesh_version", "unknown")
    manifest_data.setdefault("flow_velocity", 0.0)
    manifest_data.setdefault("gap", 0.0)
    manifest_data.setdefault("time_step", 0.0)
    manifest_data.setdefault("jet_amplitude", 0.0)
    manifest_data.setdefault("window_duration", 0.0)
    manifest_data.setdefault("random_seed", 0)
    manifest_data.setdefault("git_commit", current_git_commit())
    manifest_data.setdefault("case_type", "unknown")
    manifest_data["check_mode"] = check_mode
    manifest_data["validation_mode"] = (
        "full_case" if require_complete_schema else "partial_timeseries"
    )
    with (case_path / "case_manifest.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(manifest_data, f, sort_keys=False, allow_unicode=True)

    # 6. Create standard case directories / 创建标准的 Case 子目录结构
    for directory_name in CASE_REQUIRED_DIRS:
        (case_path / directory_name).mkdir(exist_ok=True)

    # 7. Write notes.md / 写入备注文件(可选)
    if notes:
        (case_path / "notes.md").write_text(notes, encoding="utf-8")

    # 8. 写入初始质量报告(仅含数据源信息,未验证)
    report_seed = {
        "status": "generated_timeseries_only",
        "check_mode": check_mode,
        "source_files": data["source_files"],
        "star_column_mapping": data["mapping"],
        "detected_units": data["units"],
        "git_commit": manifest_data.get("git_commit", "unknown"),
        "num_timeseries_rows": len(rows),
        "num_timeseries_columns": len(present_cols),
    }
    with (case_path / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump(report_seed, f, indent=2, ensure_ascii=False)

    # 如果调用方不需要最终质量报告,提前返回
    if not write_final_quality_report:
        return {
            "case_id": case_path.name,
            "case_dir": case_path,
            "timeseries": rows,
            "quality_report": report_seed,
            "errors": [],
            "warnings": [],
        }

    # 执行完整的 load_case 验证并生成最终质量报告
    result = load_case(
        case_path,
        require_complete_schema=require_complete_schema,
        check_mode=check_mode,
    )
    quality_report = _build_quality_report(result)
    quality_report["source_files"] = data["source_files"]
    quality_report["star_column_mapping"] = data["mapping"]
    quality_report["detected_units"] = data["units"]
    if _should_run_physics_consistency(quality_report.get("check_mode")):
        quality_report = _attach_ccm_ingest_contract_to_quality_report(case_path, quality_report)
        quality_report = _attach_physics_consistency_to_quality_report(case_path, quality_report)
    with (case_path / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump(quality_report, f, indent=2, ensure_ascii=False)

    # Reload with updated quality report / 使用更新后的质量报告重新加载
    result = load_case(
        case_path,
        require_complete_schema=require_complete_schema,
        check_mode=check_mode,
    )
    return result


def write_quality_report(
    case_dir: str | Path,
    *,
    require_complete_schema: bool | None = None,
    check_mode: str | None = None,
) -> dict[str, Any]:
    """Validate an existing standard case directory and write quality_report.json.

    This is the standalone "check" step for the three-stage workflow:

    1. generate ``timeseries.csv`` and package files;
    2. validate the case and write ``quality_report.json``;
    3. generate diagnostic figures.

    对已存在的标准 Case 目录执行验证,并写入/更新 quality_report.json。
    这是三步工作流的第 2 步(检查步骤):
    1. 生成 timeseries.csv 和打包文件
    2. 验证 Case 并写入 quality_report.json ← 这里
    3. 生成诊断图表

    该函数会保留已有的数据源信息(source_files、star_column_mapping、detected_units),
    主要是更新验证结果(errors/warnings)。
    """
    case_path = Path(case_dir)
    existing: dict[str, Any] = {}
    report_path = case_path / "quality_report.json"
    if report_path.exists():
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    result = load_case(
        case_path,
        require_complete_schema=require_complete_schema,
        check_mode=check_mode,
    )
    quality_report = _build_quality_report(result)
    # 保留已有的数据源信息,仅更新验证结果
    for key in ("source_files", "star_column_mapping", "detected_units", "figures"):
        if key in existing:
            quality_report[key] = existing[key]
    if _should_run_physics_consistency(quality_report.get("check_mode")):
        quality_report = _attach_ccm_ingest_contract_to_quality_report(case_path, quality_report)
        quality_report = _attach_physics_consistency_to_quality_report(case_path, quality_report)
    report_path.write_text(
        json.dumps(quality_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return quality_report


def ingest_star_product_dir(
    product_dir: str | Path,
    *,
    case_dir: str | Path,
    case_type: str = "unknown",
    manifest: dict[str, Any] | None = None,
    overwrite: bool = False,
    require_complete_schema: bool = True,
    generate_no_jet_schedule: bool = True,
    check_mode: str = "star_ingest",
    write_final_quality_report: bool = True,
) -> dict[str, Any]:
    """Ingest a STAR-CCM+ result folder into a standard case directory.

    The current product folder convention is a set of monitor CSV exports such
    as ``FZ_image_30000.csv``, ``Drag_Monitor_...csv``,
    ``Pitch_Moment_Monitor_...csv`` and similar files.  The folder does not
    contain ``timeseries.csv``; this function discovers recognized monitor CSVs,
    merges them on ``physical_time``, and writes the standard case package.

    从 STAR-CCM+ 仿真结果目录摄入数据到标准 Case 目录。
    产品目录通常包含多个监视器 CSV 文件(力监视器、力矩监视器、喷气监视器等),
    但不会包含 timeseries.csv。此函数自动发现可识别的监视器 CSV,
    将其在 physical_time 上合并后生成标准 Case 包。

    参数:
        product_dir: STAR 产品目录路径(包含多个监视器 CSV)
        case_dir: 目标 Case 目录
        case_type: Case 类型(jet/no_jet/passive 等)
        manifest: 可选的 Manifest 元数据
        generate_no_jet_schedule: 无喷气工况是否自动生成空的驱动指令表
    """
    product_path = Path(product_dir)
    # 自动发现产品目录中可识别的监视器 CSV 文件
    star_files = discover_star_export_csvs(product_path)
    if not star_files:
        raise ValueError(f"no recognized STAR monitor CSVs found in {product_path}")

    manifest_data = dict(manifest or {})
    manifest_data.setdefault("case_type", case_type)
    manifest_data.setdefault("source_product_dir", str(product_path.resolve()))
    # 默认单位约定
    manifest_data.setdefault("units", {"force": "N", "moment": "N-m", "massflow": "kg/s"})
    manifest_data.setdefault(
        "sign_convention",
        (
            "positive Fz = STAR monitor convention; "
            "positive Drag = STAR drag monitor convention; "
            "positive Pitch/Roll = STAR moment monitor convention"
        ),
    )

    # 对于无喷气/参考工况,自动生成空驱动指令表
    actuation_schedule = None
    if generate_no_jet_schedule and str(case_type).lower() in {"no_jet", "passive", "reference"}:
        data = read_star_export_bundle(star_files)
        actuation_schedule = _build_no_jet_actuation_schedule(data["rows"])

    # 自动生成备注重述摄入来源
    notes = (
        "## STAR Product Directory Ingestion\n\n"
        f"- Source product directory: `{product_path.resolve()}`\n"
        "- Ingested monitor CSV files:\n"
        + "\n".join(f"  - `{path.name}`" for path in star_files)
        + "\n\n"
        "The standard `timeseries.csv` was generated by merging these STAR "
        "monitor exports on `physical_time`.\n"
    )

    return ingest_star_export(
        star_files,
        case_dir=case_dir,
        manifest=manifest_data,
        actuation_schedule=actuation_schedule,
        notes=notes,
        overwrite=overwrite,
        require_complete_schema=require_complete_schema,
        check_mode=check_mode,
        write_final_quality_report=write_final_quality_report,
    )


def _check_files(case_dir: Path) -> list[str]:
    """
    检查 Case 目录中必须存在的文件和子目录。
    返回缺失项列表,空列表表示完整。
    """
    missing: list[str] = []
    for file_name in CASE_REQUIRED_FILES:
        if not (case_dir / file_name).exists():
            missing.append(file_name)
    if not find_case_timeseries_path(case_dir).is_file():
        missing.append("processed/timeseries.csv")
    legacy_timeseries_exists = legacy_case_timeseries_path(case_dir).is_file()
    for dir_name in CASE_REQUIRED_DIRS:
        if dir_name == "processed" and legacy_timeseries_exists:
            continue
        if not (case_dir / dir_name).is_dir():
            missing.append(f"{dir_name}/")
    return missing


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    """
    读取 CSV 文件为字典列表,自动尝试将数值字符串转为 float。
    如果文件不存在返回空列表。
    如果数值转换失败则保留原始字符串(如 "NaN"、"success" 等状态值)。
    """
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, Any]] = []
        for row in reader:
            parsed: dict[str, Any] = {}
            for key, val in row.items():
                if key is None:
                    continue
                stripped = val.strip().strip('"') if val else ""
                try:
                    parsed[key] = float(stripped)
                except (ValueError, TypeError):
                    parsed[key] = stripped
            rows.append(parsed)
        return rows


def _build_no_jet_actuation_schedule(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    为无喷气工况构建空的喷气驱动指令表。
    所有 24 个阀门的 JET 都设为 0,cmd_massflow 都设为 0.0。
    每个窗口的 t_start/t_end 由时间序列中的 physical_time 推算得出。
    """
    schedule: list[dict[str, Any]] = []
    times = [row.get("physical_time") for row in rows]
    numeric_times = [float(t) for t in times if isinstance(t, (int, float))]
    default_dt = (
        numeric_times[1] - numeric_times[0]
        if len(numeric_times) >= 2
        else 0.0
    )
    for idx, row in enumerate(rows):
        t = row.get("physical_time")
        next_t = rows[idx + 1].get("physical_time") if idx + 1 < len(rows) else None
        t_end = next_t if next_t is not None else (
            float(t) + default_dt if isinstance(t, (int, float)) else t
        )
        record: dict[str, Any] = {
            "physical_time": t,
            "window_id": idx,
            "t_start": t,
            "t_end": t_end,
        }
        for column in JET_COLUMNS:
            record[column] = 0
        for column in CMD_MASSFLOW_COLUMNS:
            record[column] = 0.0
        schedule.append(record)
    return schedule


def _add_common_timeseries_fields(rows: list[dict[str, Any]], *, case_type: str) -> None:
    """
    为时间序列数据添加通用字段:
    - window_id: 窗口编号(按行索引自动分配)
    - solver_status: 求解器状态(默认为 "success")
    - case_stage: Case 阶段(默认为 "starccm_ingest")
    - 无喷气工况:所有 JET 列默认设为 0

    该函数直接修改 rows 列表(就地修改)。
    """
    for idx, row in enumerate(rows):
        row.setdefault("window_id", idx)
        row.setdefault("solver_status", "success")
        row.setdefault("case_stage", "starccm_ingest")
        if str(case_type).lower() in {"no_jet", "passive", "reference"}:
            for column in JET_COLUMNS:
                row.setdefault(column, 0)


def _is_jet_case(manifest: dict[str, Any], ts_columns: list[str]) -> bool:
    """
    判断一个 Case 是否为喷气工况。
    判断优先级:
    1. Manifest 中的 case_type 字段(最高优先级)
    2. 时间序列列名前缀检测(JET_/cmd_massflow_/actual_massflow_)
    优先级设计:Manifest 显式声明优先于列名推断,避免列名误匹配。
    """
    case_type = str(manifest.get("case_type", "")).strip().lower()
    if case_type in {"jet", "jet_on", "with_jet", "active_jet"}:
        return True
    if case_type in {"no_jet", "passive", "reference"}:
        return False
    return any(
        col.startswith("JET_")
        or col.startswith("cmd_massflow_")
        or col.startswith("actual_massflow_")
        for col in ts_columns
    )


def _ordered_columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    """
    从数据行中提取列名并按标准优先级顺序排列。
    标准顺序:
    physical_time → window_id → Fz 传感器 → 全局量 → solver_status
    → JET 信号 → cmd_massflow → actual_massflow → case_stage
    → 其他未识别列(按首次出现的顺序)
    """
    priority = (
        "physical_time",
        "window_id",
        *FZ_SENSOR_COLUMNS,
        *GLOBAL_COLUMNS,
        "solver_status",
        *JET_COLUMNS,
        *CMD_MASSFLOW_COLUMNS,
        *ACTUAL_MASSFLOW_COLUMNS,
        "case_stage",
    )
    present: set[str] = set()
    first_seen: list[str] = []
    for row in rows:
        for col in row:
            if col not in present:
                present.add(col)
                first_seen.append(col)
    return [col for col in priority if col in present] + [
        col for col in first_seen if col not in priority
    ]


def _write_csv_rows(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    """
    将数据写入 CSV 文件。
    自动创建父目录,只写入指定的列(忽略行中的其他键)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_quality_report(result: dict[str, Any]) -> dict[str, Any]:
    """Build a quality report dict from the case data and check results.

    从 load_case 的结果中提取关键信息,构建标准格式的质量报告。
    质量报告包含:
    - 错误/警告的数量和具体内容
    - 验证模式(完整验证/部分验证)
    - 时间序列维度信息
    - 运行成功标志(无错误 = 成功)
    """
    return {
        "case_id": result["case_id"],
        "num_errors": len(result["errors"]),
        "num_warnings": len(result["warnings"]),
        "errors": result["errors"],
        "warnings": result["warnings"],
        "has_jet_data": result["has_jet_data"],
        "check_mode": result.get("check_mode", "star_ingest"),
        "check_profile": _quality_check_profile(result.get("check_mode", "star_ingest")),
        "validation_mode": (
            "full_case" if result.get("require_complete_schema") else "partial_timeseries"
        ),
        "num_timeseries_rows": len(result["timeseries"]),
        "num_timeseries_columns": len(result["timeseries"][0]) if result["timeseries"] else 0,
        "run_success_flag": len(result["errors"]) == 0,
    }


def _attach_physics_consistency_to_quality_report(
    case_path: Path,
    quality_report: dict[str, Any],
) -> dict[str, Any]:
    """Append B04 physics consistency results to the standard quality report."""

    try:
        from .physics_consistency_checker import check_case

        physics_report = check_case(case_path)
    except Exception as exc:  # pragma: no cover - defensive report preservation
        physics_report = {
            "schema_version": "B04_physics_consistency_v1",
            "case_id": case_path.name,
            "summary": {
                "category_counts": {},
                "blocking_issue_count": 1,
                "run_success_flag": False,
            },
            "categories": {
                "format_errors": [
                    {
                        "severity": "error",
                        "message": f"physics consistency checker failed: {exc}",
                    }
                ]
            },
            "summaries": {},
        }

    quality_report["physics_consistency"] = physics_report
    quality_report["num_physics_blocking_issues"] = int(
        physics_report.get("summary", {}).get("blocking_issue_count", 0)
    )
    quality_report["run_success_flag"] = bool(quality_report.get("run_success_flag")) and bool(
        physics_report.get("summary", {}).get("run_success_flag")
    )
    return quality_report


def _attach_ccm_ingest_contract_to_quality_report(
    case_path: Path,
    quality_report: dict[str, Any],
) -> dict[str, Any]:
    """Append B03/B33 real-CCM standard-directory contract checks."""

    manifest = _read_manifest_yaml(case_path / "case_manifest.yaml")
    checks: list[dict[str, Any]] = []
    blocking = 0

    def add(name: str, ok: bool, message: str, *, severity: str = "error", path: str | None = None) -> None:
        nonlocal blocking
        record = {
            "name": name,
            "status": "pass" if ok else "fail",
            "severity": "info" if ok else severity,
            "message": message,
        }
        if path is not None:
            record["path"] = path
        checks.append(record)
        if not ok and severity == "error":
            blocking += 1

    timeseries_path = case_timeseries_path(case_path)
    add(
        "processed_timeseries",
        timeseries_path.is_file(),
        "standard timeseries must be stored at processed/timeseries.csv",
        path=str(timeseries_path),
    )
    for file_name in ("case_manifest.yaml", "actuation_schedule.csv", "quality_report.json"):
        file_path = case_path / file_name
        add(
            f"required_file_{file_name}",
            file_path.is_file(),
            f"standard case requires {file_name}",
            path=str(file_path),
        )
    for dir_name in ("processed", "figures", "logs"):
        dir_path = case_path / dir_name
        add(
            f"required_dir_{dir_name}",
            dir_path.is_dir(),
            f"standard case requires {dir_name}/",
            path=str(dir_path),
        )

    raw_star_dir = _manifest_path(case_path, manifest.get("raw_star_dir"))
    source_product_dir = _manifest_path(case_path, manifest.get("source_product_dir"))
    source_ccm_timeseries = _manifest_path(case_path, manifest.get("source_ccm_timeseries"))
    source_files = [
        Path(value)
        for value in quality_report.get("source_files", [])
        if isinstance(value, str)
    ]
    source_evidence = [
        path
        for path in (raw_star_dir, source_product_dir, source_ccm_timeseries, *source_files)
        if path is not None and path.exists()
    ]
    add(
        "source_evidence",
        bool(source_evidence),
        "ccm/star ingest must keep evidence of raw STAR output or CCM runtime source files",
        path=", ".join(str(path) for path in source_evidence) if source_evidence else None,
    )

    if raw_star_dir is not None:
        add(
            "raw_star_readonly_evidence",
            raw_star_dir.is_dir(),
            "declared raw_star_dir must exist and remain separate from processed outputs",
            path=str(raw_star_dir),
        )

    quality_report["ccm_ingest_contract"] = {
        "schema_version": "B03_B33_ccm_ingest_contract_v1",
        "case_id": case_path.name,
        "summary": {
            "blocking_issue_count": blocking,
            "run_success_flag": blocking == 0,
        },
        "checks": checks,
    }
    quality_report["num_ccm_contract_blocking_issues"] = blocking
    quality_report["run_success_flag"] = bool(quality_report.get("run_success_flag")) and blocking == 0
    return quality_report


def _manifest_path(case_path: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    case_relative = case_path / path
    if case_relative.exists():
        return case_relative
    return path


def _read_manifest_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _should_run_physics_consistency(check_mode: Any) -> bool:
    """Return whether B04 STAR physics-interface checks apply to this report."""

    return _quality_check_profile(check_mode) == "ccm"


def _quality_check_profile(check_mode: Any) -> str:
    """Map historical check modes to the two supported data-check profiles."""

    normalized = str(check_mode or "ccm").strip().lower()
    if normalized in MOCK_CHECK_MODES:
        return "mock"
    if normalized in CCM_CHECK_MODES:
        return "ccm"
    return "ccm"
