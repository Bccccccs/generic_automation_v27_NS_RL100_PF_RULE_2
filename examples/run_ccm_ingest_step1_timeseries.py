#!/usr/bin/env python3
"""CCM 结果导入 Step 1：生成标准 timeseries。"""

from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

from _common import (
    configure_project_root,
    choose_path_or_prompt,
    find_schedule,
    list_numbered_dirs,
    reexec_with_project_python,
)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv_rows(path: Path, columns: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def float_value(row: dict[str, object], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def rows_to_matrix(rows: list[dict[str, object]], columns: tuple[str, ...]) -> np.ndarray:
    import numpy as np

    values = np.zeros((len(rows), len(columns)), dtype=float)
    for row_idx, row in enumerate(rows):
        for column_idx, column in enumerate(columns):
            values[row_idx, column_idx] = float_value(row, column)
    return values


def downsample_by_max(values: np.ndarray, max_rows: int = 1200) -> np.ndarray:
    import numpy as np

    if values.shape[0] <= max_rows:
        return values
    block_size = int(np.ceil(values.shape[0] / max_rows))
    trimmed_rows = int(np.ceil(values.shape[0] / block_size)) * block_size
    padded = np.zeros((trimmed_rows, values.shape[1]), dtype=float)
    padded[: values.shape[0], :] = values
    return padded.reshape(-1, block_size, values.shape[1]).max(axis=1)


def resolve_product_dir(path: Path) -> Path:
    if path.name == "out_put" and path.is_dir():
        return path
    if (path / "out_put").is_dir():
        return path / "out_put"
    if (path / "raw_star" / "out_put").is_dir():
        return path / "raw_star" / "out_put"
    if is_flat_star_product_dir(path):
        return path
    raise SystemExit(f"未找到 STAR 导出目录（out_put 或直接存放 monitor CSV 的目录）：{path}")


def is_flat_star_product_dir(path: Path) -> bool:
    """判断目录根层是否是扁平 STAR monitor 导出。

    除了可识别 CSV，还要求至少一个载荷或实际质量流量列，
    避免把只有动作表的 ``input/`` 误认为 CCM 结果。
    """
    if not path.is_dir() or (path / "case_manifest.yaml").is_file() or (path / "quality_report.json").is_file():
        return False

    from flow_control.star_ingest.star_export_reader import (
        ACTUAL_MASSFLOW_COLUMNS,
        STANDARD_LOAD_COLUMNS,
        detect_star_column_mapping,
        discover_star_export_csvs,
    )

    expected_columns = {*STANDARD_LOAD_COLUMNS, *ACTUAL_MASSFLOW_COLUMNS}
    for csv_path in discover_star_export_csvs(path):
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            headers = next(csv.reader(handle), [])
        if expected_columns.intersection(detect_star_column_mapping(headers)):
            return True
    return False


def discover_product_dirs(root: Path = Path("runs")) -> list[Path]:
    """发现旧 ``out_put`` 目录和 week4 这类扁平 monitor CSV 目录。"""
    excluded_parts = {"legacy", "error_case", "input", "processed", "figures", "logs", "raw_star"}
    candidates = {
        path
        for path in root.rglob("out_put")
        if path.is_dir() and not any(part in {"legacy", "error_case"} for part in path.parts)
    }
    flat_parents = {path.parent for path in root.rglob("*.csv")}
    candidates.update(
        path
        for path in flat_parents
        if not any(part in excluded_parts for part in path.parts) and is_flat_star_product_dir(path)
    )
    return sorted(candidates)


def source_case_dir_from_product(product_dir: Path) -> Path:
    if product_dir.parent.name == "raw_star":
        return product_dir.parent.parent
    if product_dir.name == "out_put":
        return product_dir.parent
    return product_dir


def _jet_key(path: Path) -> str | None:
    """从 j02、J02_* 或 G01_J02_pulse 提取喷口编号。"""
    match = re.search(r"(?i)(?:^|_)j(\d{1,2})(?:$|[_-])", path.name)
    if not match:
        return None
    return f"J{int(match.group(1)):02d}"


def find_companion_case_dir(product_dir: Path) -> Path | None:
    """将 week4/j02 配对到同级 J02_* 动作算例骨架。"""
    key = _jet_key(product_dir)
    if key is None or key == "J00":
        return None
    matches = sorted(
        candidate
        for candidate in product_dir.parent.iterdir()
        if candidate.is_dir()
        and candidate != product_dir
        and _jet_key(candidate) == key
        and ((candidate / "input" / "actuation_schedule.csv").is_file()
             or (candidate / "actuation_schedule.csv").is_file())
    )
    if len(matches) > 1:
        raise SystemExit(
            f"{product_dir} 匹配到多个 {key} 动作算例："
            + ", ".join(path.as_posix() for path in matches)
        )
    return matches[0] if matches else None


def find_schedule_for_product(product_dir: Path) -> Path | None:
    """优先使用导出目录内动作表，其次使用同级配对算例的动作表。"""
    for case_dir in (source_case_dir_from_product(product_dir), find_companion_case_dir(product_dir)):
        if case_dir is None:
            continue
        for candidate in (
            case_dir / "input" / "actuation_schedule.csv",
            case_dir / "actuation_schedule.csv",
        ):
            if candidate.is_file():
                return candidate
    if _jet_key(product_dir) == "J00":
        return None
    raise SystemExit(f"未找到与 STAR 导出配对的动作表：{product_dir}")


def build_no_jet_schedule(timeseries_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """按 STAR 采样时刻生成 ``t_start < t_sample <= t_end`` 的全零动作表。"""
    from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
    from flow_control.star_ingest.star_export_reader import JET_COLUMNS

    sample_times = [float_value(row, "physical_time") for row in timeseries_rows]
    if not sample_times:
        raise SystemExit("无喷气 STAR 导出中没有可用采样时刻")
    positive_steps = [b - a for a, b in zip(sample_times, sample_times[1:]) if b > a]
    if positive_steps:
        ordered_steps = sorted(positive_steps)
        first_step = ordered_steps[len(ordered_steps) // 2]
    else:
        first_step = sample_times[0]
    if first_step <= 0:
        raise SystemExit("无法从无喷气 STAR 导出推导动作窗口宽度")

    rows: list[dict[str, object]] = []
    previous_end = round(sample_times[0] - first_step, 12)
    for window_id, sample_time in enumerate(sample_times):
        sample_time = round(sample_time, 12)
        row: dict[str, object] = {
            "physical_time": previous_end,
            "window_id": window_id,
            "t_start": previous_end,
            "t_end": sample_time,
        }
        row.update({column: 0 for column in JET_COLUMNS})
        row.update({column: 0.0 for column in MASSFLOW_COLUMNS})
        rows.append(row)
        previous_end = sample_time
    return rows


def default_real_star_case_id(source_case_dir: Path, has_active_jet: bool) -> str:
    name = source_case_dir.name
    if name.startswith("G") and name.endswith("_existing"):
        return name
    lowered = name.lower()
    if "no_jet" in lowered or "nojet" in lowered:
        return "G00_nojet_existing"
    if "j01" in lowered or "jet01" in lowered or "jet_01" in lowered:
        return "G01_JET01_existing"
    suffix = "jet_existing" if has_active_jet else "nojet_existing"
    return f"{name}_{suffix}"


def resolve_real_star_target_case_dir(source_case_dir: Path, has_active_jet: bool) -> Path:
    companion = find_companion_case_dir(source_case_dir)
    if companion is not None:
        return companion
    if source_case_dir.parts[:2] == ("runs", "real_star") and len(source_case_dir.parts) >= 3:
        return Path(*source_case_dir.parts[:3])
    target_root = source_case_dir.parent if is_flat_star_product_dir(source_case_dir) else Path("runs") / "real_star"
    default_id = default_real_star_case_id(source_case_dir, has_active_jet)
    print(f"\n默认输出目录：{target_root / default_id}")
    value = input("直接回车使用默认 case_id，或输入新的 case_id：").strip()
    case_id = value or default_id
    if Path(case_id).name != case_id:
        raise SystemExit(f"case_id 不能包含路径分隔符：{case_id}")
    return target_root / case_id


def copy_raw_star(product_dir: Path, raw_product_dir: Path) -> None:
    if product_dir.resolve() == raw_product_dir.resolve():
        return
    raw_product_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(path for path in product_dir.rglob("*") if path.is_file()):
        target = raw_product_dir / source.relative_to(product_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.chmod(0o644)
        shutil.copy2(source, target)
        target.chmod(0o444)


def ingest_case(case_dir: Path, schedule_path: Path | None) -> None:
    from flow_control.case_paths import case_timeseries_path
    import numpy as np
    import yaml

    from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
    from flow_control.mock.mock_plant import spatial_nonuniformity, write_plots
    from flow_control.star_ingest.case_data_loader import (
        CASE_REQUIRED_DIRS,
        compute_fz_total,
        current_git_commit,
    )
    from flow_control.star_ingest.star_export_reader import (
        FZ_SENSOR_COLUMNS,
        JET_COLUMNS,
        discover_star_export_csvs,
        read_star_export_bundle,
    )

    product_dir = resolve_product_dir(case_dir)
    source_case_dir = source_case_dir_from_product(product_dir)

    star_files = discover_star_export_csvs(product_dir)
    if not star_files:
        raise SystemExit(f"未在 {product_dir} 找到可识别的 STAR-CCM+ monitor CSV")

    data = read_star_export_bundle(star_files)
    schedule_rows = read_csv_rows(schedule_path) if schedule_path is not None else build_no_jet_schedule(data["rows"])
    if not schedule_rows:
        raise SystemExit(f"动作表为空：{schedule_path}")

    has_active_jet = any(
        any(float_value(row, jet_col) > 0.5 for jet_col in JET_COLUMNS)
        for row in schedule_rows
    )
    target_case_dir = resolve_real_star_target_case_dir(source_case_dir, has_active_jet)
    target_case_dir.mkdir(parents=True, exist_ok=True)
    copy_raw_star(product_dir, target_case_dir / "raw_star" / "out_put")
    case_type = "jet_on" if has_active_jet else "no_jet"
    window_duration = float_value(schedule_rows[0], "t_end") - float_value(
        schedule_rows[0], "t_start"
    )
    jet_amplitude = max(sum(float_value(row, col) for col in MASSFLOW_COLUMNS) for row in schedule_rows)

    manifest = {
        "geometry_version": "starccm-runtime",
        "mesh_version": "unknown",
        "git_commit": current_git_commit(),
        "flow_velocity": 0.0,
        "gap": 0.0,
        "time_step": window_duration,
        "jet_amplitude": jet_amplitude,
        "window_duration": window_duration,
        "random_seed": 0,
        "case_type": case_type,
        "case_stage": "starccm_output_ingest",
        "units": {"force": "N", "moment": "N-m", "massflow": "kg/s"},
        "sign_convention": (
            "jet massflow is positive for injection into the flow domain; "
            "negative STAR inlet-report values are normalized to positive magnitudes; "
            "force and moment values preserve the STAR-CCM+ monitor convention"
        ),
    }

    timeseries_rows = data["rows"]
    compute_fz_total(timeseries_rows)
    if len(timeseries_rows) != len(schedule_rows):
        raise SystemExit(
            "CCM 输出行数和动作表行数不一致："
            f"timeseries={len(timeseries_rows)}, schedule={len(schedule_rows)}"
        )

    merged_rows: list[dict[str, object]] = []
    for row_idx, (ts_row, schedule_row) in enumerate(zip(timeseries_rows, schedule_rows)):
        row: dict[str, object] = dict(ts_row)
        row["window_id"] = schedule_row.get("window_id", row_idx)
        for column in JET_COLUMNS:
            row[column] = schedule_row.get(column, 0.0)
        for column in MASSFLOW_COLUMNS:
            row[column] = schedule_row.get(column, 0.0)
        row.setdefault("solver_status", "success")
        row["case_stage"] = "starccm_output_ingest"
        merged_rows.append(row)

    compute_fz_total(merged_rows)

    preferred_columns = [
        "physical_time",
        "window_id",
        *JET_COLUMNS,
        *MASSFLOW_COLUMNS,
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
    ]
    columns = [column for column in preferred_columns if any(column in row for row in merged_rows)]
    for row in merged_rows:
        for column in row:
            if column not in columns:
                columns.append(column)

    timeseries_path = case_timeseries_path(target_case_dir)
    write_csv_rows(timeseries_path, columns, merged_rows)
    write_csv_rows(target_case_dir / "actuation_schedule.csv", list(schedule_rows[0]), schedule_rows)

    for directory_name in CASE_REQUIRED_DIRS:
        (target_case_dir / directory_name).mkdir(exist_ok=True)

    time_values = np.array([float_value(row, "physical_time") for row in merged_rows], dtype=float)
    effective_inputs = rows_to_matrix(merged_rows, MASSFLOW_COLUMNS)
    outputs = rows_to_matrix(merged_rows, FZ_SENSOR_COLUMNS)
    quick_plot_result = {
        "physical_time": time_values,
        "inputs": downsample_by_max(effective_inputs),
        "outputs": outputs,
        "totals": {
            "Fz_Total": np.array([float_value(row, "Fz_Total") for row in merged_rows], dtype=float),
            "total_massflow": effective_inputs.sum(axis=1),
        },
        "spatial_nonuniformity": spatial_nonuniformity(outputs),
    }
    write_plots(target_case_dir, quick_plot_result)

    manifest_path = target_case_dir / "case_manifest.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest_data.update(manifest)
    manifest_data.update(
        {
            "case_type": case_type,
            "case_stage": "starccm_output_ingest",
            "check_mode": "ccm",
            "validation_mode": "full_case",
            "source_product_dir": "raw_star/out_put",
            "source_schedule": "actuation_schedule.csv",
            "git_commit": manifest_data.get("git_commit", "unknown"),
            "window_duration": window_duration,
            "time_step": window_duration,
            "jet_amplitude": jet_amplitude,
        }
    )
    manifest_path.write_text(
        yaml.safe_dump(manifest_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    report_path = target_case_dir / "quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    report.update(
        {
            "status": "generated_timeseries_only",
            "check_mode": "ccm",
            "case_type": case_type,
            "source_product_dir": "raw_star/out_put",
            "source_schedule": "actuation_schedule.csv",
            "git_commit": manifest_data.get("git_commit", "unknown"),
            "source_files": [str(Path("raw_star") / "out_put" / path.relative_to(product_dir)) for path in star_files],
            "star_column_mapping": data["mapping"],
            "detected_units": data["units"],
            "num_timeseries_rows": len(merged_rows),
            "num_timeseries_columns": len(columns),
            "quick_figures": {
                "input_heatmap": "figures/input_heatmap.svg",
                "fz_regions": "figures/fz_regions.svg",
                "fz_total": "figures/fz_total.svg",
                "spatial_nonuniformity": "figures/spatial_nonuniformity.svg",
                "total_massflow": "figures/total_massflow.svg",
            },
        }
    )
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    (target_case_dir / "logs" / "B03_step1_ingest.log").write_text(
        "\n".join(
            [
                f"source_product_dir={product_dir}",
                f"source_schedule={schedule_path or 'generated:no_jet_from_sample_times'}",
                f"target_case_dir={target_case_dir}",
                f"case_type={case_type}",
                f"source_csv_files={len(star_files)}",
                "actual_massflow columns came from STAR reports, were not synthesized from commands, "
                "and were normalized so injection into the flow domain is positive.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (target_case_dir / "logs" / "B03_step1_notes.md").write_text(
        "# STAR-CCM+ output ingest\n\n"
        "Step 1 completed from out_put monitor CSV files and generated quick SVG data plots. "
        "Schedule commands were merged as JET_* and cmd_massflow_* only; actual_massflow_* remains missing unless present in STAR output. "
        "STAR inlet-report values were normalized so injection into the flow domain is positive. "
        "Run step2 quality check and step3 figure generation next.\n",
        encoding="utf-8",
    )

    print(f"source product dir: {product_dir}")
    print(f"source csv files: {len(star_files)}")
    print(f"target real_star case: {target_case_dir}")
    print(f"case_type: {case_type}")
    print(f"generated standard timeseries: {timeseries_path}")
    print(f"generated quick figures: {target_case_dir / 'figures'}")
    print(f"quality report placeholder: {report_path}")


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    product_dirs = discover_product_dirs()
    if not product_dirs:
        raise SystemExit("未找到 CCM 输出目录。支持 out_put 或根层 monitor CSV。")

    print("当前可整理为标准 case 的 STAR 导出目录：")
    list_numbered_dirs(product_dirs)
    print("\n请输入目录编号，或直接输入目录路径：")
    selected = choose_path_or_prompt(product_dirs)
    product_dir = resolve_product_dir(selected)
    ingest_case(product_dir, find_schedule_for_product(product_dir))

    print("\nStep 1 done. Next:")
    print("python examples/run_ccm_ingest_step2_check.py")


if __name__ == "__main__":
    main()
