#!/usr/bin/env python3
"""Step 1: generate standard case files from STAR CSV exports.

This step writes ``timeseries.csv`` and the surrounding case package skeleton.
It intentionally does not run quality checks or generate figures.

三步工作流的第 1 步:从 STAR 导出 CSV 生成标准 Case 文件。
这一步只生成 timeseries.csv 和 Case 包骨架,不运行质量检查或生成图表。

设计意图:
- 使用 write_final_quality_report=False 跳过质量验证,只生成数据
- 生成的 notes.md 提示用户下一步运行 step2
- 支持 --partial 模式用于部分导入(后续与其他导出合并)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .case_data_loader import ingest_star_export, ingest_star_product_dir
from ..case_paths import resolve_case_dir


def main() -> None:
    """
    三步流水线第 1 步的 CLI 入口。
    可选的数据源:
    - --star-dir: STAR 产品目录(自动发现监视器 CSV)
    - --star-file: 显式指定的 CSV 文件(可重复以合并多个文件)

    输出目录:
    - --case-id: 在 --runs-root 下创建 runs/<case-id> 目录
    - --case-dir: 直接指定输出目录
    """
    parser = argparse.ArgumentParser(
        description="Step 1: generate timeseries.csv from STAR exports."
    )
    # 数据源:产品目录或单个文件(互斥)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--star-dir", help="STAR product directory containing monitor CSVs.")
    source_group.add_argument(
        "--star-file",
        action="append",
        help="STAR monitor CSV. Repeat to merge multiple files on physical_time.",
    )
    # 输出目录:case-id(自动生成路径)或明确指定目录(互斥)
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument("--case-id", help="Case id written under --runs-root, default runs/<case-id>.")
    output_group.add_argument("--case-dir", help="Explicit output standard case directory.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id outputs.")
    parser.add_argument("--case-type", choices=("unknown", "no_jet", "jet_on"), default="unknown")
    parser.add_argument("--force", action="store_true", help="Overwrite the output case directory.")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Mark the generated package as partial_timeseries instead of full_case.",
    )
    parser.add_argument(
        "--check-mode",
        default="star_ingest",
        choices=("star_ingest", "mock", "arx_use", "ccm"),
        help="Quality-check mode recorded in case_manifest.yaml.",
    )
    args = parser.parse_args()

    # 解析输出目录(支持 case-id 自动路径或明确目录)
    try:
        case_dir = resolve_case_dir(
            case_id=args.case_id,
            case_dir=args.case_dir,
            runs_root=args.runs_root,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.star_dir:
        # 从产品目录摄入(自动发现 CSV 文件)
        result = ingest_star_product_dir(
            args.star_dir,
            case_dir=case_dir,
            case_type=args.case_type,
            overwrite=args.force,
            require_complete_schema=not args.partial,
            check_mode=args.check_mode,
            write_final_quality_report=False,  # Step 1 跳过质量验证
        )
    else:
        # 从显式指定的 CSV 文件列表摄入
        star_files = [Path(value) for value in args.star_file or []]
        missing = [path for path in star_files if not path.exists()]
        if missing:
            raise SystemExit(f"STAR file(s) not found: {missing}")
        result = ingest_star_export(
            star_files,
            case_dir=case_dir,
            manifest={
                "case_type": args.case_type,
                "check_mode": args.check_mode,
                "case_stage": "starccm_ingest",
            },
            overwrite=args.force,
            require_complete_schema=not args.partial,
            check_mode=args.check_mode,
            write_final_quality_report=False,  # Step 1 跳过质量验证
        )

    # 写入 notes.md,提示用户下一步操作
    (case_dir / "notes.md").write_text(
        "# STAR timeseries generation\n\n"
        "Step 1 completed. Run `python -m flow_control.star_ingest.step2_check_case` next.\n",
        encoding="utf-8",
    )

    # 输出生成结果摘要
    print(f"generated timeseries: {case_dir / 'timeseries.csv'}")
    print(
        "rows="
        f"{result['quality_report'].get('num_timeseries_rows', len(result.get('timeseries', [])))} "
        f"columns={result['quality_report'].get('num_timeseries_columns', 0)}"
    )


if __name__ == "__main__":
    main()
