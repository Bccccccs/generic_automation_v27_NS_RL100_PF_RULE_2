#!/usr/bin/env python3
"""CCM 结果导入 Step 1：生成标准 timeseries。"""

from __future__ import annotations

import csv
import json
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


def ingest_case(case_dir: Path, schedule_path: Path) -> None:
    import numpy as np
    import yaml

    from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
    from flow_control.mock.mock_plant import spatial_nonuniformity, write_plots
    from flow_control.star_ingest.case_data_loader import (
        CASE_REQUIRED_DIRS,
        compute_fz_total,
        current_git_commit,
        ingest_star_export,
    )
    from flow_control.star_ingest.star_export_reader import (
        ACTUAL_MASSFLOW_COLUMNS,
        FZ_SENSOR_COLUMNS,
        JET_COLUMNS,
        discover_star_export_csvs,
    )

    product_dir = case_dir / "out_put"
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

    ingest_star_export(
        star_files,
        case_dir=case_dir,
        manifest=manifest,
        actuation_schedule=schedule_rows,
        overwrite=True,
        require_complete_schema=True,
        check_mode="ccm",
        write_final_quality_report=False,
    )

    timeseries_path = case_dir / "timeseries.csv"
    timeseries_rows = read_csv_rows(timeseries_path)
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
        for jet_column, cmd_column, actual_column in zip(
            JET_COLUMNS,
            MASSFLOW_COLUMNS,
            ACTUAL_MASSFLOW_COLUMNS,
        ):
            row[actual_column] = float_value(row, jet_column) * float_value(row, cmd_column)
        row.setdefault("solver_status", "success")
        merged_rows.append(row)

    compute_fz_total(merged_rows)

    preferred_columns = [
        "physical_time",
        "window_id",
        *JET_COLUMNS,
        *MASSFLOW_COLUMNS,
        *ACTUAL_MASSFLOW_COLUMNS,
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

    write_csv_rows(timeseries_path, columns, merged_rows)
    write_csv_rows(case_dir / "actuation_schedule.csv", list(schedule_rows[0]), schedule_rows)
    write_csv_rows(case_dir / "input" / "actuation_schedule.csv", list(schedule_rows[0]), schedule_rows)

    for directory_name in CASE_REQUIRED_DIRS:
        (case_dir / directory_name).mkdir(exist_ok=True)

    time_values = np.array([float_value(row, "physical_time") for row in merged_rows], dtype=float)
    effective_inputs = rows_to_matrix(merged_rows, ACTUAL_MASSFLOW_COLUMNS)
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
    write_plots(case_dir, quick_plot_result)

    manifest_path = case_dir / "case_manifest.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest_data.update(
        {
            "case_type": case_type,
            "case_stage": "starccm_output_ingest",
            "check_mode": "ccm",
            "validation_mode": "full_case",
            "source_product_dir": str(product_dir),
            "source_schedule": str(schedule_path),
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

    report_path = case_dir / "quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    report.update(
        {
            "status": "generated_timeseries_only",
            "check_mode": "ccm",
            "case_type": case_type,
            "source_product_dir": str(product_dir),
            "source_schedule": str(schedule_path),
            "git_commit": manifest_data.get("git_commit", "unknown"),
            "source_files": [str(path) for path in star_files],
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

    (case_dir / "notes.md").write_text(
        "# STAR-CCM+ output ingest\n\n"
        "Step 1 completed from out_put monitor CSV files and generated quick SVG data plots. "
        "Run step2 quality check and step3 figure generation next.\n",
        encoding="utf-8",
    )

    print(f"source product dir: {product_dir}")
    print(f"source csv files: {len(star_files)}")
    print(f"case_type: {case_type}")
    print(f"generated standard timeseries: {timeseries_path}")
    print(f"generated quick figures: {case_dir / 'figures'}")
    print(f"quality report placeholder: {report_path}")


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    case_dirs = sorted(path.parent for path in Path("runs").glob("*/out_put") if path.is_dir())
    if not case_dirs:
        raise SystemExit("未找到 CCM 输出目录。需要 runs/<目录>/out_put。")

    print("当前可生成标准 timeseries 的 CCM case 目录路径：")
    list_numbered_dirs(case_dirs)
    print("\n请输入目录编号，或直接输入目录路径：")
    case_dir = choose_path_or_prompt(case_dirs)
    if not (case_dir / "out_put").is_dir():
        raise SystemExit(f"未找到 CCM 输出目录：{case_dir}/out_put")

    ingest_case(case_dir, find_schedule(case_dir))

    print("\nStep 1 done. Next:")
    print("python examples/run_ccm_ingest_step2_check.py")


if __name__ == "__main__":
    main()
