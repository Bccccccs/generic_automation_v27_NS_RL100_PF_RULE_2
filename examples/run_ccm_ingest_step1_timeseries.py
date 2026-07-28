#!/usr/bin/env python3
"""CCM 结果导入 Step 1：生成标准 timeseries。"""

from __future__ import annotations

import csv
import json
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
    raise SystemExit(f"未找到 STAR out_put 目录：{path}")


def source_case_dir_from_product(product_dir: Path) -> Path:
    if product_dir.parent.name == "raw_star":
        return product_dir.parent.parent
    return product_dir.parent


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
    if source_case_dir.parts[:2] == ("runs", "real_star") and len(source_case_dir.parts) >= 3:
        return Path(*source_case_dir.parts[:3])
    default_id = default_real_star_case_id(source_case_dir, has_active_jet)
    print(f"\n默认输出 case_id：{default_id}")
    value = input("直接回车使用默认 case_id，或输入新的 case_id：").strip()
    case_id = value or default_id
    if Path(case_id).name != case_id:
        raise SystemExit(f"case_id 不能包含路径分隔符：{case_id}")
    return Path("runs") / "real_star" / case_id


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


def ingest_case(case_dir: Path, schedule_path: Path) -> None:
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

    schedule_rows = read_csv_rows(schedule_path)
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
        "sign_convention": "positive values follow the STAR-CCM+ monitor export convention",
    }

    data = read_star_export_bundle(star_files)
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
                f"source_schedule={schedule_path}",
                f"target_case_dir={target_case_dir}",
                f"case_type={case_type}",
                f"source_csv_files={len(star_files)}",
                "actual_massflow columns were not synthesized from schedule commands.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (target_case_dir / "logs" / "B03_step1_notes.md").write_text(
        "# STAR-CCM+ output ingest\n\n"
        "Step 1 completed from out_put monitor CSV files and generated quick SVG data plots. "
        "Schedule commands were merged as JET_* and cmd_massflow_* only; actual_massflow_* remains missing unless present in STAR output. "
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
    product_dirs = sorted(
        path
        for path in Path("runs").rglob("out_put")
        if path.is_dir() and not any(part in {"legacy", "error_case"} for part in path.parts)
    )
    if not product_dirs:
        raise SystemExit("未找到 CCM 输出目录。需要 out_put。")

    print("当前可整理为 real_star 标准 case 的 STAR out_put 目录：")
    list_numbered_dirs(product_dirs)
    print("\n请输入目录编号，或直接输入目录路径：")
    selected = choose_path_or_prompt(product_dirs)
    product_dir = resolve_product_dir(selected)
    source_case_dir = source_case_dir_from_product(product_dir)

    ingest_case(source_case_dir, find_schedule(source_case_dir))

    print("\nStep 1 done. Next:")
    print("python examples/run_ccm_ingest_step2_check.py")


if __name__ == "__main__":
    main()
