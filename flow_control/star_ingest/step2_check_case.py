#!/usr/bin/env python3
"""Step 2: validate a generated standard case directory.

三步工作流的第 2 步:验证已生成的 Case 目录并写入 quality_report.json。
对 Step 1 生成的 timeseries.csv 运行 7 项质量检查,
将验证结果写入 quality_report.json。

输出:
- quality_report.json: 包含 errors/warnings 列表和汇总统计
- 控制台输出:显示错误和警告的详细信息(各最多 20 条)
"""

from __future__ import annotations

import argparse

from .case_data_loader import write_quality_report
from ..case_paths import resolve_case_dir


def main() -> None:
    """
    三步流水线第 2 步的 CLI 入口。
    对 Step 1 生成的 Case 目录运行完整的质量验证。

    选项:
    - --case-id / --case-dir: 指定要验证的 Case
    - --partial: 宽松模式(只检查 physical_time)
    - --check-mode: 覆盖 Manifest 中的检查模式
    """
    parser = argparse.ArgumentParser(description="Step 2: validate STAR ingest case data.")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--case-id", help="Case id under --runs-root, default runs/<case-id>.")
    target_group.add_argument("--case-dir", help="Explicit standard case directory to check.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id inputs.")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Only require physical_time; skip full required-column checks.",
    )
    parser.add_argument(
        "--check-mode",
        default=None,
        choices=("star_ingest", "mock", "arx_use", "ccm"),
        help="Override the check mode recorded in the manifest.",
    )
    args = parser.parse_args()

    try:
        case_dir = resolve_case_dir(
            case_id=args.case_id,
            case_dir=args.case_dir,
            runs_root=args.runs_root,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    # 调用 write_quality_report 执行完整验证并写入 JSON
    report = write_quality_report(
        case_dir,
        require_complete_schema=not args.partial,
        check_mode=args.check_mode,
    )

    # 输出验证结果
    print(f"quality report: {case_dir / 'quality_report.json'}")
    print(f"errors={report['num_errors']} warnings={report['num_warnings']}")
    # 最多显示 20 条错误,避免输出过长
    for error in report["errors"][:20]:
        print(f"ERROR: {error}")
    if len(report["errors"]) > 20:
        print(f"... {len(report['errors']) - 20} more errors")
    # 最多显示 20 条警告
    for warning in report["warnings"][:20]:
        print(f"WARNING: {warning}")
    if len(report["warnings"]) > 20:
        print(f"... {len(report['warnings']) - 20} more warnings")

if __name__ == "__main__":
    main()
