#!/usr/bin/env python3
"""CCM 结果导入 Step 3：生成诊断图。"""

import json
from pathlib import Path

from _common import choose_path_or_prompt, configure_project_root, list_numbered_dirs, reexec_with_project_python


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    case_dirs = sorted(path.parent for path in Path("runs").glob("*/quality_report.json"))
    if not case_dirs:
        raise SystemExit("未找到可生成图片目录。需要目录中包含 quality_report.json。")

    print("当前可生成图片目录路径：")
    list_numbered_dirs(case_dirs)
    print("\n请输入目录编号，或直接输入目录路径：")
    case_dir = choose_path_or_prompt(case_dirs)

    from flow_control.star_ingest.case_data_loader import load_case
    from flow_control.star_ingest.figures_generator import generate_all_figures

    result = load_case(case_dir, require_complete_schema=True)
    figures = generate_all_figures(result, case_dir / "figures")

    report_path = case_dir / "quality_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        report = {}
    report["figures"] = {
        name: str(path.relative_to(case_dir)) if path else None
        for name, path in figures.items()
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\nStep 3 done. Figures:")
    print((case_dir / "figures").as_posix())
    for name, path in figures.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
