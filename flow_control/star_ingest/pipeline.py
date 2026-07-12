"""
One-step STAR ingest pipeline.

一键式 STAR 数据摄入流水线,将读取、质量检查和画图封装为一个命令。
在用户需要立即获取完整 Case 包时使用。

流程:
1. 读取 STAR 导出 CSV 并写入标准 Case 包骨架
2. 写入 quality_report.json(质量验证)
3. 写入诊断图表

此模块是自动化的一键入口；人工操作入口在 examples/run_ccm_ingest_step*.py。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .case_data_loader import ingest_star_export, ingest_star_product_dir
from .figures_generator import generate_all_figures
from .star_export_reader import (
    compute_fz_total,
    discover_star_export_csvs,
    read_star_export_bundle,
    read_star_export_csv,
)


def run_star_ingest_pipeline(
    *,
    case_dir: str | Path,
    star_files: list[str | Path] | None = None,
    star_dir: str | Path | None = None,
    force: bool = False,
    jet: bool = False,
    case_type: str | None = None,
    partial: bool = False,
    check_mode: str = "star_ingest",
) -> dict[str, Any]:
    """Run STAR export ingestion, quality checks, and figure generation.

    运行完整的 STAR 数据摄入流水线,包含 5 个步骤:
    [1/5] 读取 STAR 导出 CSV 文件并映射列名
    [2/5] 计算衍生量(如 Fz_Total)
    [3/5] 摄入到标准 Case 目录(包括写入 Manifest/驱动指令等)
    [4/5] 生成诊断图表(4 张)
    [5/5] 输出质量检查结果和文件路径

    参数:
        case_dir: 目标 Case 目录路径
        star_files: STAR 导出 CSV 文件列表(可与 star_dir 二选一)
        star_dir: STAR 产品目录(可与 star_files 二选一)
        force: 是否覆盖已存在的 Case 目录
        jet: 是否标记为喷气工况
        case_type: Case 类型(no_jet/jet_on/unknown)
        partial: 是否允许部分列(不要求所有列都存在)
        check_mode: 质量检查模式
    """

    # 解析数据源:可以是单个文件列表或产品目录
    source = _resolve_sources(star_files=star_files, star_dir=star_dir)
    resolved_case_type = case_type or ("jet_on" if jet else "unknown")
    case_path = Path(case_dir)
    manifest = _default_manifest(resolved_case_type, check_mode=check_mode)

    # 打印起始信息
    print(f"\n{'=' * 60}")
    print("STAR Export Ingestion")
    print(f"{'=' * 60}")
    if source["product_dir"] is not None:
        print(f"  Product: {source['product_dir']}")
    print(f"  Sources: {len(source['star_paths'])} file(s)")
    for star_path in source["star_paths"]:
        print(f"           {star_path}")
    print(f"  Target:  {case_path}")

    print("\n[1/5] Reading STAR export CSV ...")
    data = _read_sources(source["star_paths"])
    print(f"  Detected {len(data['rows'])} rows, {len(data['columns'])} columns")
    print("  Column mapping:")
    for standard_name, raw_name in data["mapping"].items():
        print(f"    {standard_name:25s} <- {raw_name}")

    print("\n[2/5] Computing derived quantities ...")
    compute_fz_total(data["rows"])
    if data["rows"] and "Fz_Total" in data["rows"][0]:
        print("  Fz_Total computed from sensor columns")

    print("\n[3/5] Ingesting into case directory ...")
    # 根据数据源类型选择不同的摄入方式
    if source["product_dir"] is not None:
        result = ingest_star_product_dir(
            source["product_dir"],
            case_dir=case_path,
            case_type=resolved_case_type,
            manifest=manifest,
            overwrite=force,
            require_complete_schema=not partial,
            check_mode=check_mode,
        )
    else:
        result = ingest_star_export(
            source["star_paths"],
            case_dir=case_path,
            manifest=manifest,
            overwrite=force,
            require_complete_schema=not partial,
            check_mode=check_mode,
            notes=(
                f"Auto-ingested from STAR exports: {[path.name for path in source['star_paths']]}\n"
                f"Original columns: {list(data['mapping'].keys())}\n"
                f"Sources: {[str(path.resolve()) for path in source['star_paths']]}"
            ),
        )

    print("\n[4/5] Generating figures ...")
    figures = generate_all_figures(result, case_path / "figures")

    print("\n[5/5] Quality check results:")
    print(f"  Errors:   {len(result['errors'])}")
    print(f"  Warnings: {len(result['warnings'])}")
    _print_quality_messages(result)

    # 将图表路径信息附加到质量报告中
    _attach_figures_to_quality_report(case_path, figures)
    # 打印生成的输出文件列表
    _print_output_paths(case_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    """
    CLI 入口函数,使用 argparse 解析命令行参数。
    提供 --star-file(单个 CSV)和 --star-dir(产品目录)两种互斥的数据源选择。
    支持 --force(覆盖)、--jet(喷气工况)、--partial(部分列)和 --check-mode(检查模式)等选项。
    """
    parser = argparse.ArgumentParser(
        description="One-step STAR ingest: read exports, package case, check quality, and generate figures."
    )
    # 数据源:--star-file 和 --star-dir 是互斥的(二选一)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--star-file",
        action="append",
        help=(
            "Path to a STAR-CCM+ export CSV. Repeat --star-file to merge "
            "separate Fz/drag/moment/jet exports on physical_time."
        ),
    )
    source_group.add_argument(
        "--star-dir",
        help=(
            "Path to a STAR-CCM+ product directory containing monitor CSVs. "
            "Recognized force/moment CSVs are merged into timeseries.csv."
        ),
    )
    parser.add_argument("--case-dir", required=True, help="Target case directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing case directory.")
    parser.add_argument("--jet", action="store_true", help="Mark as jet case.")
    parser.add_argument(
        "--case-type",
        choices=("unknown", "no_jet", "jet_on"),
        default=None,
        help="Case type recorded in case_manifest.yaml. Defaults to jet_on with --jet, otherwise unknown.",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Allow a single STAR timeseries subset without full required-column errors.",
    )
    parser.add_argument(
        "--check-mode",
        default="star_ingest",
        choices=("star_ingest", "mock", "arx_use", "ccm"),
        help="Quality-check mode written to the manifest and quality report.",
    )
    args = parser.parse_args(argv)

    run_star_ingest_pipeline(
        star_files=args.star_file,
        star_dir=args.star_dir,
        case_dir=args.case_dir,
        force=args.force,
        jet=args.jet,
        case_type=args.case_type,
        partial=args.partial,
        check_mode=args.check_mode,
    )
    return 0


def _resolve_sources(
    *,
    star_files: list[str | Path] | None,
    star_dir: str | Path | None,
) -> dict[str, Any]:
    """
    解析数据源输入,统一为 star_paths 列表。
    支持两种方式:
    - star_dir: 产品目录路径,自动发现其中的监视器 CSV
    - star_files: 显式指定的 CSV 文件路径列表

    返回字典包含:
    - product_dir: 产品目录(如是通过目录发现的)或 None
    - star_paths: 解析后的 Path 对象列表
    """
    if star_dir is not None:
        product_dir = Path(star_dir)
        if not product_dir.is_dir():
            raise SystemExit(f"ERROR: STAR product directory not found: {product_dir}")
        star_paths = discover_star_export_csvs(product_dir)
        if not star_paths:
            raise SystemExit(f"ERROR: no recognized STAR monitor CSVs found in {product_dir}")
        return {"product_dir": product_dir, "star_paths": star_paths}

    star_paths = [Path(value) for value in star_files or []]
    missing = [path for path in star_paths if not path.exists()]
    if missing:
        raise SystemExit(f"ERROR: STAR file(s) not found: {missing}")
    return {"product_dir": None, "star_paths": star_paths}


def _read_sources(star_paths: list[Path]) -> dict[str, Any]:
    """
    根据文件数量选择合适的读取方式:
    - 1 个文件:使用 read_star_export_csv(单个读取)
    - 多个文件:使用 read_star_export_bundle(批量合并)
    """
    return (
        read_star_export_csv(star_paths[0])
        if len(star_paths) == 1
        else read_star_export_bundle(star_paths)
    )


def _default_manifest(case_type: str, *, check_mode: str) -> dict[str, Any]:
    """
    生成默认的 Case Manifest 字典。
    包含默认的单位约定、符号约定和几何参数占位值。
    调用方可以在此基础上覆盖或补充特定字段。
    """
    return {
        "case_type": case_type,
        "check_mode": check_mode,
        "geometry_version": "unknown",
        "mesh_version": "unknown",
        "flow_velocity": 0.0,
        "gap": 0.0,
        "time_step": 0.0,
        "jet_amplitude": 0.0,
        "window_duration": 0.0,
        "random_seed": 0,
        "units": {
            "force": "N",
            "moment": "Nm",
            "massflow": "kg/s",
        },
        "sign_convention": (
            "positive Fz = lift upward; "
            "positive Drag = downstream; "
            "positive Pitch = nose up; "
            "positive Roll = right wing down"
        ),
    }


def _print_quality_messages(result: dict[str, Any]) -> None:
    """
    打印质量检查结果的详细信息。
    分别列出错误(以 ! 标识)和警告(以 ? 标识)。
    如果无错误则提示"Case passed all checks. "。
    """
    if result["errors"]:
        print("\n  ERRORS:")
        for error in result["errors"]:
            print(f"    ! {error}")
    if result["warnings"]:
        print("\n  WARNINGS:")
        for warning in result["warnings"]:
            print(f"    ? {warning}")
    if not result["errors"]:
        print("  Case passed all checks.")
    else:
        print(f"\n  Case has {len(result['errors'])} error(s); see above.")


def _attach_figures_to_quality_report(case_path: Path, figures: dict[str, Path | None]) -> None:
    """
    将生成的图表文件路径写入 quality_report.json。
    路径使用相对于 Case 目录的相对路径,便于在不同环境中使用。
    """
    report_path = case_path / "quality_report.json"
    quality_report: dict[str, Any] = {}
    if report_path.exists():
        try:
            quality_report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            quality_report = {}
    quality_report["figures"] = {
        name: str(path.relative_to(case_path)) if path else None
        for name, path in figures.items()
    }
    report_path.write_text(
        json.dumps(quality_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _print_output_paths(case_path: Path, result: dict[str, Any]) -> None:
    """
    打印生成的输出文件路径列表,供用户查看摄入结果。
    """
    print("\nOutput files:")
    print(f"  {case_path / 'case_manifest.yaml'}")
    print(f"  {case_path / 'timeseries.csv'}")
    print(f"  {case_path / 'actuation_schedule.csv'}")
    print(f"  {case_path / 'quality_report.json'}")
    print(f"  {case_path / 'figures' / ''}")
    print(f"  {case_path / 'notes.md'}")
    print(f"\n{'=' * 60}")
    print(f"Ingestion complete. Case ID: {result['case_id']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    raise SystemExit(main())
