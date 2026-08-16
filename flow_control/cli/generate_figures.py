"""CLI for generating diagnostic figures from a checked standard Case."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from flow_control.cli.organize_outputs import _choose_directory
from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from flow_control.mock.mock_plant import spatial_nonuniformity, write_plots
from flow_control.star_ingest.case_data_loader import load_case
from flow_control.star_ingest.star_export_reader import ACTUAL_MASSFLOW_COLUMNS, FZ_SENSOR_COLUMNS
from starccm.control.control_spec import JET_COLUMNS


def _float_value(row: dict[str, object], column: str) -> float:
    try:
        return float(row.get(column, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rows_to_matrix(rows: list[dict[str, object]], columns: tuple[str, ...]) -> np.ndarray:
    return np.asarray(
        [[_float_value(row, column) for column in columns] for row in rows],
        dtype=float,
    )


def _effective_inputs(rows: list[dict[str, object]]) -> np.ndarray:
    if rows and all(column in rows[0] for column in ACTUAL_MASSFLOW_COLUMNS):
        return _rows_to_matrix(rows, ACTUAL_MASSFLOW_COLUMNS)
    switches = _rows_to_matrix(rows, JET_COLUMNS)
    commands = _rows_to_matrix(rows, MASSFLOW_COLUMNS)
    return switches * commands


def _downsample_by_max(values: np.ndarray, max_rows: int = 1200) -> np.ndarray:
    if values.shape[0] <= max_rows:
        return values
    block_size = int(np.ceil(values.shape[0] / max_rows))
    padded_rows = int(np.ceil(values.shape[0] / block_size)) * block_size
    padded = np.zeros((padded_rows, values.shape[1]), dtype=float)
    padded[: values.shape[0]] = values
    return padded.reshape(-1, block_size, values.shape[1]).max(axis=1)


def _summary_plot_data(checked: dict[str, object]) -> dict[str, object]:
    rows = checked.get("timeseries", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("Case timeseries is empty; cannot generate summary figures")
    time_values = np.asarray([_float_value(row, "physical_time") for row in rows], dtype=float)
    outputs = _rows_to_matrix(rows, FZ_SENSOR_COLUMNS)
    inputs = _effective_inputs(rows)
    fz_total = np.asarray(
        [
            _float_value(row, "Fz_Total")
            if "Fz_Total" in row
            else float(np.sum(outputs[idx]))
            for idx, row in enumerate(rows)
        ],
        dtype=float,
    )
    return {
        "physical_time": time_values,
        "inputs": _downsample_by_max(inputs),
        "outputs": outputs,
        "totals": {
            "Fz_Total": fz_total,
            "total_massflow": inputs.sum(axis=1),
        },
        "spatial_nonuniformity": spatial_nonuniformity(outputs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate diagnostic figures for a standard Case."
    )
    parser.add_argument(
        "--case-dir",
        help="Standard Case directory; omit to choose interactively under runs.",
    )
    parser.add_argument("--mode", choices=("ccm", "mock"), default="ccm")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Allow an incomplete schema for debugging.",
    )
    args = parser.parse_args(argv)

    case_dir = (
        Path(args.case_dir).expanduser()
        if args.case_dir
        else _choose_directory(Path("runs"), label="图片生成 Case 目录")
    )
    report_path = case_dir / "quality_report.json"
    if not report_path.is_file():
        raise FileNotFoundError(
            f"未找到质量报告：{report_path}；请先运行 python scripts/workflow.py check"
        )

    print(f"[1/2] 正在读取 Case：{case_dir}")
    checked = load_case(
        case_dir,
        require_complete_schema=not args.partial,
        check_mode=args.mode,
    )
    print("[2/2] 正在生成 5 张 SVG 汇总图…")
    write_plots(case_dir, _summary_plot_data(checked))
    summary_names = (
        "input_heatmap",
        "fz_regions",
        "fz_total",
        "spatial_nonuniformity",
        "total_massflow",
    )
    figures = {
        name: case_dir / "figures" / f"{name}.svg"
        for name in summary_names
    }

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        report = {}
    report["summary_figures"] = {
        name: str(path.relative_to(case_dir)) if path else None
        for name, path in figures.items()
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"summary figures: {case_dir / 'figures'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
