"""Write standard mock-plant run bundles for flow-control workflows."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from starccm_control import StarCCMControlLayer

from .excitation_patterns import ActuationConfig
from .data_schema import CaseSchema, JET_COLUMNS
from .excitation_patterns.common import MASSFLOW_COLUMNS
from .mock_plant import MockPlantConfig, _HIDDEN_CRITICAL_JET_INDICES


def write_mock_case_bundle(
    *,
    config_path: str | Path,
    actuation: ActuationConfig,
    raw_config: dict[str, Any],
    plant_config: MockPlantConfig,
    plant_seed: int,
    inputs: np.ndarray,
    outputs: np.ndarray,
    stability: dict[str, Any],
    correlations: list[dict[str, Any]],
    ranking: list[dict[str, Any]],
) -> dict[str, Any]:
    actuation.output_dir.mkdir(parents=True, exist_ok=True)

    write_input_heatmap_svg(actuation.output_dir / "mock_input_heatmap.svg", inputs)
    write_output_timeseries_svg(actuation.output_dir / "mock_output_timeseries.svg", outputs)
    write_matrix_csv(actuation.output_dir / "mock_inputs.csv", inputs, "jet")
    write_matrix_csv(actuation.output_dir / "mock_outputs.csv", outputs, "output")
    write_correlations_csv(actuation.output_dir / "mock_input_output_correlations.csv", correlations)
    write_ranking_csv(actuation.output_dir / "mock_hidden_jet_influence_ranking.csv", ranking)

    schema_result = write_mock_schema_case(
        actuation=actuation,
        raw_config=raw_config,
        plant_config=plant_config,
        plant_seed=plant_seed,
        inputs=inputs,
        outputs=outputs,
        stability=stability,
    )
    copy_mock_figures_to_standard_dir(actuation.output_dir)

    top5 = [str(item["jet_id"]) for item in ranking[:5]]
    hidden_labels = [f"J{idx + 1:02d}" for idx in _HIDDEN_CRITICAL_JET_INDICES]
    hidden_hits = set(top5).intersection(hidden_labels)
    summary = {
        "config": str(config_path),
        "seed": plant_seed,
        "shape": {"inputs": list(inputs.shape), "outputs": list(outputs.shape)},
        "plant": {
            "delay_steps": plant_config.delay_steps,
            "delay_decay": plant_config.delay_decay,
            "output_beta": plant_config.output_beta,
            "noise_std": plant_config.noise_std,
        },
        "stability": stability,
        "hidden_jet_learning_check": {
            "top5_ranked_jets": top5,
            "top5_hidden_hit_count": len(hidden_hits),
            "passes": len(hidden_hits) >= 4,
        },
        "outputs": {
            "input_heatmap": "mock_input_heatmap.svg",
            "output_timeseries": "mock_output_timeseries.svg",
            "inputs_csv": "mock_inputs.csv",
            "outputs_csv": "mock_outputs.csv",
            "input_output_correlations": "mock_input_output_correlations.csv",
            "hidden_jet_influence_ranking": "mock_hidden_jet_influence_ranking.csv",
            "case_manifest": "case_manifest.yaml",
            "actuation_schedule": "actuation_schedule.csv",
            "timeseries": "timeseries.csv",
            "quality_report": "quality_report.json",
            "case_io_log": "logs/case_io.log",
        },
        "schema": {
            "case_id": schema_result["case_id"],
            "run_dir": str(schema_result["run_dir"]),
            "standard_layout": True,
        },
    }
    with (actuation.output_dir / "mock_demo_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
    return summary


def write_mock_schema_case(
    actuation: ActuationConfig,
    raw_config: dict[str, Any],
    plant_config: MockPlantConfig,
    plant_seed: int,
    inputs: np.ndarray,
    outputs: np.ndarray,
    stability: dict[str, Any],
) -> dict[str, Any]:
    timeseries = mock_timeseries_rows(actuation, inputs, outputs, stability)
    schedule = mock_actuation_schedule_rows(actuation, inputs)
    manifest = mock_manifest(actuation, raw_config, plant_config, plant_seed)
    quality_report = {
        "stability_score": 1.0 if stability["stable"] else 0.0,
        "constraint_violation_count": 0 if stability["stable"] else outputs.shape[1],
        "data_completeness": {
            "missing_count": 0,
            "total_cells": len(timeseries) * len(timeseries[0]) if timeseries else 0,
            "complete": True,
        },
        "run_success_flag": bool(stability["stable"]),
        "mock_plant_stability": stability,
        "case_stage": "mock_plant_rollout",
    }
    old_root = CaseSchema.runs_root
    CaseSchema.runs_root = actuation.output_dir.parent
    try:
        return CaseSchema.write_case(
            {
                "case_id": actuation.output_dir.name,
                "manifest": manifest,
                "timeseries": timeseries,
                "actuation_schedule": schedule,
                "quality_report": quality_report,
            }
        )
    finally:
        CaseSchema.runs_root = old_root


def mock_manifest(
    actuation: ActuationConfig,
    raw_config: dict[str, Any],
    plant_config: MockPlantConfig,
    plant_seed: int,
) -> dict[str, Any]:
    case = raw_config.get("case", {})
    flow = raw_config.get("flow", {})
    geometry = raw_config.get("geometry", {})
    mesh = raw_config.get("mesh", {})
    return {
        "geometry_version": geometry.get("version", case.get("geometry_version", "mock-plant-virtual")),
        "mesh_version": mesh.get("version", case.get("mesh_version", "not-applicable")),
        "flow_velocity": float(flow.get("velocity", case.get("flow_velocity", 0.0))),
        "gap": float(geometry.get("gap", case.get("gap", 0.0))),
        "time_step": actuation.window_duration,
        "jet_amplitude": actuation.command_amplitude,
        "window_duration": actuation.window_duration,
        "random_seed": plant_seed,
        "actuation_random_seed": actuation.random_seed,
        "n_jets": actuation.n_jets,
        "n_outputs": plant_config.n_outputs,
        "delay_steps": plant_config.delay_steps,
        "case_stage": "mock_plant_rollout",
    }


def mock_timeseries_rows(
    actuation: ActuationConfig,
    inputs: np.ndarray,
    outputs: np.ndarray,
    stability: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    control_layer = StarCCMControlLayer()
    for window_id in range(inputs.shape[1]):
        start, _ = window_time_bounds(window_id, actuation.window_duration)
        y = outputs[:, window_id]
        finite = bool(np.all(np.isfinite(y)))
        report_values = {
            point.report_name: float(y[idx])
            for idx, point in enumerate(control_layer.spec.load_points)
        }
        report_values["Drag_Total"] = float(np.sqrt(np.mean(y * y)))
        jet_commands = {
            jet_name: float(inputs[jet_idx, window_id]) if jet_idx < inputs.shape[0] else 0.0
            for jet_idx, jet_name in enumerate(JET_COLUMNS)
        }
        record = control_layer.map_timeseries_row(
            report_values,
            jet_commands,
            physical_time=start,
            window_id=window_id,
            solver_status="success" if finite and stability["stable"] else "failed",
        )
        record["case_stage"] = "mock_plant_rollout"
        rows.append(record)
    return rows


def mock_actuation_schedule_rows(
    actuation: ActuationConfig,
    inputs: np.ndarray,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for window_id in range(inputs.shape[1]):
        start, end = window_time_bounds(window_id, actuation.window_duration)
        record: dict[str, Any] = {
            "physical_time": start,
            "window_id": window_id,
            "t_start": start,
            "t_end": end,
        }
        for jet_idx, jet_name in enumerate(JET_COLUMNS):
            value = float(inputs[jet_idx, window_id]) if jet_idx < inputs.shape[0] else 0.0
            record[jet_name] = 1 if value > 0.0 else 0
        for jet_idx, column in enumerate(MASSFLOW_COLUMNS):
            record[column] = float(inputs[jet_idx, window_id]) if jet_idx < inputs.shape[0] else 0.0
        rows.append(record)
    return rows


def window_time_bounds(window_id: int, window_duration: float) -> tuple[float, float]:
    start = round(window_id * window_duration, 12)
    end = round(start + window_duration, 12)
    return start, end


def copy_mock_figures_to_standard_dir(output_dir: Path) -> None:
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    for file_name in ("mock_input_heatmap.svg", "mock_output_timeseries.svg"):
        source = output_dir / file_name
        if source.exists():
            (figures_dir / file_name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def write_matrix_csv(path: Path, matrix: np.ndarray, label_prefix: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([label_prefix, *[f"t{idx}" for idx in range(matrix.shape[1])]])
        for row_idx, row in enumerate(matrix, start=1):
            writer.writerow([f"{label_prefix}_{row_idx:02d}", *[f"{value:.8g}" for value in row]])


def write_correlations_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["jet_id", "output_id", "best_lag", "correlation", "abs_correlation"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_ranking_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rank", "jet_id", "score", "peak_delta"])
        writer.writeheader()
        writer.writerows(rows)


def write_input_heatmap_svg(path: Path, inputs: np.ndarray) -> None:
    cell_w = 9
    cell_h = 10
    left = 48
    top = 26
    width = left + inputs.shape[1] * cell_w + 18
    height = top + inputs.shape[0] * cell_h + 28
    max_abs = max(float(np.max(np.abs(inputs))), 1.0)
    lines = svg_header(width, height, "24xT input heatmap")
    for row_idx in range(inputs.shape[0]):
        y = top + row_idx * cell_h
        lines.append(svg_text(6, y + 8, f"J{row_idx + 1:02d}", 8, "#2b2f33"))
        for col_idx in range(inputs.shape[1]):
            x = left + col_idx * cell_w
            value = float(inputs[row_idx, col_idx] / max_abs)
            fill = blend("#f4f7fb", "#1864ab", max(0.0, value))
            lines.append(f'<rect x="{x}" y="{y}" width="8" height="9" fill="{fill}"/>')
    append_time_ticks(lines, left, top + inputs.shape[0] * cell_h + 12, inputs.shape[1], cell_w)
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_output_timeseries_svg(path: Path, outputs: np.ndarray) -> None:
    width = 920
    height = 360
    left = 54
    top = 28
    plot_w = width - left - 24
    plot_h = height - top - 42
    min_y = float(np.min(outputs))
    max_y = float(np.max(outputs))
    pad = max((max_y - min_y) * 0.12, 0.1)
    min_y -= pad
    max_y += pad
    colors = ["#0b7285", "#e67700", "#2b8a3e", "#c92a2a", "#5f3dc4", "#087f5b"]
    lines = svg_header(width, height, "6 output time series")
    lines.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fbfcfe" stroke="#d0d7de"/>')
    for idx in range(5):
        y = top + idx * plot_h / 4
        lines.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#edf2f7"/>')
    for output_idx, series in enumerate(outputs):
        points = []
        for time_idx, value in enumerate(series):
            x = left + time_idx * plot_w / max(outputs.shape[1] - 1, 1)
            y = top + (max_y - float(value)) * plot_h / (max_y - min_y)
            points.append(f"{x:.2f},{y:.2f}")
        color = colors[output_idx % len(colors)]
        lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.8" points="{" ".join(points)}"/>')
        lines.append(svg_text(left + 8 + output_idx * 58, height - 12, f"Y{output_idx + 1}", 11, color))
    lines.append(svg_text(8, top + 12, f"{max_y:.2f}", 9, "#57606a"))
    lines.append(svg_text(8, top + plot_h, f"{min_y:.2f}", 9, "#57606a"))
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(8, 16, title, 13, "#1f2328"),
    ]


def svg_text(x: float, y: float, text: str, size: int, fill: str) -> str:
    return f'<text x="{x}" y="{y}" font-family="Arial, sans-serif" font-size="{size}" fill="{fill}">{text}</text>'


def append_time_ticks(lines: list[str], left: int, y: int, count: int, cell_w: int) -> None:
    step = max(1, count // 10)
    for col_idx in range(0, count, step):
        x = left + col_idx * cell_w
        lines.append(svg_text(x, y, str(col_idx), 8, "#57606a"))


def blend(left_hex: str, right_hex: str, amount: float) -> str:
    amount = min(max(amount, 0.0), 1.0)
    left = np.array([int(left_hex[idx : idx + 2], 16) for idx in (1, 3, 5)])
    right = np.array([int(right_hex[idx : idx + 2], 16) for idx in (1, 3, 5)])
    rgb = np.round(left * (1.0 - amount) + right * amount).astype(int)
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
