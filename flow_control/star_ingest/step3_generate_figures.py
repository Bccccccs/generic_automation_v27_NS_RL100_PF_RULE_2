#!/usr/bin/env python3
"""Step 3: generate diagnostic figures for a validated case directory.

三步工作流的第 3 步:为已验证的 Case 目录生成诊断图表。
在 Step 1(生成数据)和 Step 2(验证质量)的基础上,
生成 4 张诊断图表并更新 quality_report.json 中的图表路径。

生成的图表:
1. force_timeseries.png — 力时间序列
2. jet_schedule.png — 喷气热力图(无喷气时生成占位图)
3. massflow_check.png — 质量流量对比(无喷气时生成占位图)
4. quality_summary.png — 质量摘要
"""

from __future__ import annotations

import argparse
import json

from .case_data_loader import load_case
from .figures_generator import generate_all_figures
from ..case_paths import resolve_case_dir


def main() -> None:
    """
    三步流水线第 3 步的 CLI 入口。
    加载已验证的 Case,生成诊断图表,并将图表路径写入质量报告。

    选项:
    - --case-id / --case-dir: 指定 Case
    - --partial: 使用部分时间序列验证模式加载
    """
    parser = argparse.ArgumentParser(description="Step 3: generate STAR ingest figures.")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--case-id", help="Case id under --runs-root, default runs/<case-id>.")
    target_group.add_argument("--case-dir", help="Explicit standard case directory.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id inputs.")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Use partial-timeseries validation mode when loading the case.",
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

    # 加载 Case 数据(含质量检查)
    result = load_case(case_dir, require_complete_schema=not args.partial)
    # 生成全部 4 张诊断图表
    figures = generate_all_figures(result, case_dir / "figures")

    # 更新 quality_report.json,添加图表路径
    report_path = case_dir / "quality_report.json"
    report = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = {}
    report["figures"] = {
        name: str(path.relative_to(case_dir)) if path else None
        for name, path in figures.items()
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 输出图表目录和文件路径
    print(f"figures directory: {case_dir / 'figures'}")
    for name, path in figures.items():
        print(f"{name}: {path}")

if __name__ == "__main__":
    main()
