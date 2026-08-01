#!/usr/bin/env python3
"""交互式查看 case 溯源。

这个脚本只读取现有 case 产物，不重新整理 raw 数据，也不重跑质量检查。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from _common import (
    choose_path_or_prompt,
    configure_project_root,
    discover_case_dirs_with_quality_report,
    find_timeseries,
    list_numbered_dirs,
    reexec_with_project_python,
)


MAX_RAW_FILES_TO_PRINT = 40
MAX_COLUMNS_TO_PRINT = 18
MAX_ISSUES_TO_PRINT = 12


def main() -> None:
    reexec_with_project_python()
    configure_project_root()

    case_dirs = discover_case_dirs_with_quality_report()
    if not case_dirs:
        raise SystemExit("未找到可查看溯源的目录。需要 case 中包含 quality_report.json。")

    print("当前可查看溯源目录路径：")
    list_numbered_dirs(case_dirs)
    print("\n请输入目录编号，或直接输入目录路径：")
    case_dir = choose_path_or_prompt(case_dirs)
    if not case_dir.exists():
        raise SystemExit(f"目录不存在：{case_dir}")

    print_trace_report(case_dir)


def print_trace_report(case_dir: Path) -> None:
    manifest = read_yaml(case_dir / "case_manifest.yaml")
    quality = read_json(case_dir / "quality_report.json")
    timeseries_path = try_find_timeseries(case_dir)
    schedule_path = find_existing(case_dir / "actuation_schedule.csv", case_dir / "input" / "actuation_schedule.csv")
    raw_dir = resolve_raw_dir(case_dir, manifest)

    print(f"\n=== Case 溯源：{case_dir.as_posix()} ===")
    print_basic_summary(case_dir, manifest, quality)
    print_standard_files(case_dir, timeseries_path, schedule_path, raw_dir)
    print_raw_summary(raw_dir)
    print_timeseries_summary(timeseries_path)
    print_schedule_summary(schedule_path)
    print_quality_summary(quality)
    print_missing_matrix_summary(case_dir, manifest)
    print_legacy_hint(case_dir)


def print_basic_summary(case_dir: Path, manifest: dict[str, Any], quality: dict[str, Any]) -> None:
    print("\n[基本信息]")
    print_kv("case_id", manifest.get("case_id") or quality.get("case_id") or case_dir.name)
    print_kv("case_type", manifest.get("case_type", "未声明"))
    print_kv("status", manifest.get("status") or quality.get("status") or "未声明")
    print_kv("validation_mode", manifest.get("validation_mode") or quality.get("validation_mode") or "未声明")
    print_kv("check_profile", quality.get("check_profile") or quality.get("check_mode") or "未生成")
    print_kv("git_commit", manifest.get("git_commit", "未记录"))

    star = manifest.get("star")
    if isinstance(star, dict):
        print_kv("STAR sim_file", star.get("sim_file", "未记录"))
        print_kv("STAR sim hash", star.get("sim_file_hash_sha256", "未记录"))
        print_kv("STAR region_names", ", ".join(map(str, star.get("region_names", []))) or "未记录")


def print_standard_files(
    case_dir: Path,
    timeseries_path: Path | None,
    schedule_path: Path | None,
    raw_dir: Path | None,
) -> None:
    print("\n[标准文件]")
    entries = [
        ("raw_star", raw_dir),
        ("processed/timeseries.csv", timeseries_path),
        ("case_manifest.yaml", case_dir / "case_manifest.yaml"),
        ("actuation_schedule.csv", schedule_path),
        ("quality_report.json", case_dir / "quality_report.json"),
        ("figures/", case_dir / "figures"),
        ("logs/", case_dir / "logs"),
    ]
    for label, path in entries:
        if path is None:
            print(f"- {label}: 缺失")
        else:
            print(f"- {label}: {'存在' if path.exists() else '缺失'}  {path.as_posix()}")


def print_raw_summary(raw_dir: Path | None) -> None:
    print("\n[raw_star 原始数据]")
    if raw_dir is None or not raw_dir.exists():
        print("- 未找到 raw_star 或 manifest 中声明的原始目录。")
        return

    csv_files = sorted(raw_dir.rglob("*.csv"))
    print(f"- 原始 CSV 数量: {len(csv_files)}")
    for path in csv_files[:MAX_RAW_FILES_TO_PRINT]:
        info = inspect_csv(path)
        rel = relative_display(path)
        cols = shorten_list(info["columns"], MAX_COLUMNS_TO_PRINT)
        print(f"- {rel}: rows={info['rows']} cols={info['num_columns']} columns={cols}")
    if len(csv_files) > MAX_RAW_FILES_TO_PRINT:
        print(f"- ... 还有 {len(csv_files) - MAX_RAW_FILES_TO_PRINT} 个 raw CSV 未展开")


def print_timeseries_summary(timeseries_path: Path | None) -> None:
    print("\n[processed/timeseries.csv]")
    if timeseries_path is None or not timeseries_path.exists():
        print("- 未找到时序数据。")
        return

    info = inspect_csv(timeseries_path, inspect_time=True)
    print(f"- 路径: {timeseries_path.as_posix()}")
    print(f"- 行列: rows={info['rows']} cols={info['num_columns']}")
    print(f"- physical_time: {'存在' if info['has_physical_time'] else '缺失'}")
    if info["has_physical_time"]:
        print(f"- physical_time 单调: {'是' if info['time_monotonic'] else '否'}")
        print(f"- time 范围: {info['time_min']} -> {info['time_max']}")

    columns = set(info["columns"])
    fz_cols = [f"Fz_S{i}{side}" for i in range(1, 4) for side in ("L", "R")]
    actual_cols = [f"actual_massflow_{i:02d}" for i in range(1, 25)]
    cmd_cols = [f"cmd_massflow_{i:02d}" for i in range(1, 25)]
    print_missing_group("6 个区域升力", fz_cols, columns)
    print_missing_group("actual_massflow_01..24", actual_cols, columns)
    print_missing_group("cmd_massflow_01..24", cmd_cols, columns)


def print_schedule_summary(schedule_path: Path | None) -> None:
    print("\n[actuation_schedule.csv]")
    if schedule_path is None or not schedule_path.exists():
        print("- 未找到动作 schedule。")
        return
    info = inspect_csv(schedule_path)
    print(f"- 路径: {schedule_path.as_posix()}")
    print(f"- 行列: rows={info['rows']} cols={info['num_columns']}")
    print(f"- columns={shorten_list(info['columns'], MAX_COLUMNS_TO_PRINT)}")


def print_quality_summary(quality: dict[str, Any]) -> None:
    print("\n[quality_report.json]")
    if not quality:
        print("- 未找到或无法解析 quality_report.json。")
        return

    print_kv("num_errors", quality.get("num_errors", "未记录"))
    print_kv("num_warnings", quality.get("num_warnings", "未记录"))
    print_kv("run_success_flag", quality.get("run_success_flag", "未记录"))
    print_kv("ccm_contract_blocking", quality.get("num_ccm_contract_blocking_issues", "未记录"))
    print_kv("physics_blocking", quality.get("num_physics_blocking_issues", "未记录"))

    print_issue_list("ERROR", quality.get("errors", []))
    print_issue_list("WARNING", quality.get("warnings", []))

    contract = quality.get("ccm_ingest_contract")
    if isinstance(contract, dict):
        print("\n[ccm_ingest_contract]")
        summary = contract.get("summary", {})
        if isinstance(summary, dict):
            print_kv("blocking_issue_count", summary.get("blocking_issue_count", "未记录"))
        checks = contract.get("checks", [])
        if isinstance(checks, list):
            failed = [
                c for c in checks
                if isinstance(c, dict) and c.get("status") not in {"pass", "info"}
            ]
            print(f"- 非 pass 检查: {len(failed)}")
            for check in failed[:MAX_ISSUES_TO_PRINT]:
                print(f"  - {check.get('name')}: {check.get('status')} {check.get('message')}")

    physics = quality.get("physics_consistency")
    if isinstance(physics, dict):
        print("\n[physics_consistency]")
        summary = physics.get("summary", {})
        counts = summary.get("category_counts") if isinstance(summary, dict) else None
        if isinstance(counts, dict):
            for name, count in counts.items():
                print(f"- {name}: {count}")


def print_missing_matrix_summary(case_dir: Path, manifest: dict[str, Any]) -> None:
    matrix = Path("docs/week3/B03_missing_data_matrix.csv")
    case_id = str(manifest.get("case_id") or case_dir.name)
    print("\n[B03_missing_data_matrix.csv]")
    if not matrix.is_file():
        print("- 未找到 docs/week3/B03_missing_data_matrix.csv")
        return
    rows = []
    with matrix.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("case_id") == case_id:
                rows.append(row)
    print(f"- 匹配条目: {len(rows)}")
    for row in rows[:MAX_ISSUES_TO_PRINT]:
        print(f"- {row.get('field')}: {row.get('status')} / {row.get('category')} / {row.get('message')}")
    if len(rows) > MAX_ISSUES_TO_PRINT:
        print(f"- ... 还有 {len(rows) - MAX_ISSUES_TO_PRINT} 条未展开")


def print_legacy_hint(case_dir: Path) -> None:
    print("\n[legacy 对比]")
    if case_dir.parts[:2] == ("runs", "real_star"):
        legacy = Path("runs/real_star/legacy") / case_dir.name
        print(f"- legacy 路径: {'存在' if legacy.exists() else '缺失'}  {legacy.as_posix()}")
        if legacy.exists():
            files = sorted(path for path in legacy.rglob("*") if path.is_file())
            print(f"- legacy 文件数: {len(files)}")
    else:
        print("- 非 runs/real_star 标准 case，不检查 legacy 对比目录。")


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def try_find_timeseries(case_dir: Path) -> Path | None:
    try:
        return find_timeseries(case_dir)
    except SystemExit:
        return None


def find_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def resolve_raw_dir(case_dir: Path, manifest: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []
    source_product_dir = manifest.get("source_product_dir")
    if isinstance(source_product_dir, str) and source_product_dir:
        source_path = Path(source_product_dir)
        candidates.append(source_path if source_path.is_absolute() else case_dir / source_path)
    raw_star_dir = manifest.get("raw_star_dir")
    if isinstance(raw_star_dir, str) and raw_star_dir:
        raw_path = Path(raw_star_dir)
        candidates.append(raw_path if raw_path.is_absolute() else case_dir / raw_path)
    candidates.extend([case_dir / "raw_star" / "out_put", case_dir / "raw_star"])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def inspect_csv(path: Path, *, inspect_time: bool = False) -> dict[str, Any]:
    encoding_errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return inspect_csv_with_encoding(path, encoding, inspect_time=inspect_time)
        except UnicodeDecodeError as exc:
            encoding_errors.append(f"{encoding}: {exc}")
    return {
        "rows": "读取失败",
        "num_columns": "读取失败",
        "columns": [f"编码失败: {'; '.join(encoding_errors)}"],
        "has_physical_time": False,
        "time_monotonic": False,
        "time_min": "",
        "time_max": "",
    }


def inspect_csv_with_encoding(path: Path, encoding: str, *, inspect_time: bool) -> dict[str, Any]:
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = 0
        prev_time: float | None = None
        time_monotonic = True
        time_min: float | None = None
        time_max: float | None = None
        has_physical_time = "physical_time" in columns
        for row in reader:
            rows += 1
            if inspect_time and has_physical_time:
                value = row.get("physical_time")
                try:
                    current = float(value) if value not in (None, "") else None
                except ValueError:
                    current = None
                    time_monotonic = False
                if current is not None:
                    if prev_time is not None and current < prev_time:
                        time_monotonic = False
                    prev_time = current
                    time_min = current if time_min is None else min(time_min, current)
                    time_max = current if time_max is None else max(time_max, current)
    return {
        "rows": rows,
        "num_columns": len(columns),
        "columns": columns,
        "has_physical_time": has_physical_time,
        "time_monotonic": time_monotonic,
        "time_min": format_float(time_min),
        "time_max": format_float(time_max),
    }


def print_missing_group(label: str, expected: list[str], columns: set[str]) -> None:
    missing = [name for name in expected if name not in columns]
    if missing:
        print(f"- {label}: 缺失 {len(missing)} 个 -> {', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}")
    else:
        print(f"- {label}: 全部存在")


def print_issue_list(label: str, issues: Any) -> None:
    if not isinstance(issues, list) or not issues:
        return
    print(f"- {label} 条目: {len(issues)}")
    for issue in issues[:MAX_ISSUES_TO_PRINT]:
        print(f"  - {issue}")
    if len(issues) > MAX_ISSUES_TO_PRINT:
        print(f"  - ... 还有 {len(issues) - MAX_ISSUES_TO_PRINT} 条未展开")


def print_kv(key: str, value: Any) -> None:
    print(f"- {key}: {value}")


def shorten_list(values: list[str], limit: int) -> str:
    if len(values) <= limit:
        return ", ".join(values) if values else "-"
    head = ", ".join(values[:limit])
    return f"{head}, ... (+{len(values) - limit})"


def relative_display(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def format_float(value: float | None) -> str:
    if value is None:
        return "无法读取"
    return f"{value:.12g}"


if __name__ == "__main__":
    main()
