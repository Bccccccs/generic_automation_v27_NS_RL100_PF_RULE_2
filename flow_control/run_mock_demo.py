"""Run the 24-input, 6-output virtual CFD plant demo."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .mock_plant import MockPlant, MockPlantConfig, _HIDDEN_CRITICAL_JET_INDICES
from .schedule_generator import ActuationConfig, generate_actuation_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MockPlant CFD/RL validation demo.")
    parser.add_argument("--config", default="configs/pilot_sparse24.yaml")
    parser.add_argument("--output-dir", help="Override output.run_dir from config.")
    parser.add_argument("--seed", type=int, help="Override actuation random_seed for the plant.")
    argv = [arg.replace("–", "--", 1) if arg.startswith("–") else arg for arg in sys.argv[1:]]
    args = parser.parse_args(argv)

    raw_config = _read_yaml(args.config)
    actuation = ActuationConfig.from_mapping(raw_config)
    if args.output_dir:
        actuation = _replace_actuation_output_dir(actuation, Path(args.output_dir))

    plant_seed = int(args.seed if args.seed is not None else actuation.random_seed + 404)
    plant_config = _mock_config_from_mapping(raw_config)
    actuation.output_dir.mkdir(parents=True, exist_ok=True)

    matrix = generate_actuation_matrix(actuation)
    inputs = np.asarray(matrix, dtype=float).T * actuation.command_amplitude
    plant = MockPlant(plant_config).reset(plant_seed)
    outputs = _run_plant(plant, inputs, actuation.window_duration)

    stability = _stability_check(outputs)
    correlations = _input_output_correlations(inputs, outputs, max_lag=plant_config.delay_steps)
    ranking = _influence_ranking(plant_config, plant_seed, horizon=40, amplitude=actuation.command_amplitude)

    _write_input_heatmap_svg(actuation.output_dir / "mock_input_heatmap.svg", inputs)
    _write_output_timeseries_svg(actuation.output_dir / "mock_output_timeseries.svg", outputs)
    _write_matrix_csv(actuation.output_dir / "mock_inputs.csv", inputs, "jet")
    _write_matrix_csv(actuation.output_dir / "mock_outputs.csv", outputs, "output")
    _write_correlations_csv(actuation.output_dir / "mock_input_output_correlations.csv", correlations)
    _write_ranking_csv(actuation.output_dir / "mock_hidden_jet_influence_ranking.csv", ranking)

    top5 = [item["jet_id"] for item in ranking[:5]]
    hidden_labels = [f"J{idx + 1:02d}" for idx in _HIDDEN_CRITICAL_JET_INDICES]
    hidden_hits = set(top5).intersection(hidden_labels)
    summary = {
        "config": str(args.config),
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
        },
    }
    with (actuation.output_dir / "mock_demo_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    print(f"MockPlant demo complete: {actuation.output_dir}")
    print(f"Stable: {stability['stable']} max_abs_y={stability['max_abs_y']:.4f}")
    print("Top influence jets:", ", ".join(top5))


def _read_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _mock_config_from_mapping(data: dict[str, Any]) -> MockPlantConfig:
    values = data.get("mock_plant", {})
    allowed = {field.name for field in fields(MockPlantConfig)}
    kwargs = {key: value for key, value in values.items() if key in allowed}
    return MockPlantConfig(**kwargs)


def _replace_actuation_output_dir(config: ActuationConfig, output_dir: Path) -> ActuationConfig:
    return ActuationConfig(
        n_jets=config.n_jets,
        n_active_per_window=config.n_active_per_window,
        n_excitation_windows=config.n_excitation_windows,
        n_reference_windows=config.n_reference_windows,
        command_amplitude=config.command_amplitude,
        window_duration=config.window_duration,
        max_consecutive_on=config.max_consecutive_on,
        equal_activation_count=config.equal_activation_count,
        random_seed=config.random_seed,
        output_dir=output_dir,
        max_generation_attempts=config.max_generation_attempts,
    )


def _run_plant(plant: MockPlant, inputs: np.ndarray, dt: float) -> np.ndarray:
    outputs = []
    for step_idx in range(inputs.shape[1]):
        outputs.append(plant.step(inputs[:, step_idx], dt=dt))
    return np.asarray(outputs, dtype=float).T


def _stability_check(outputs: np.ndarray) -> dict[str, Any]:
    finite = bool(np.all(np.isfinite(outputs)))
    max_abs = float(np.max(np.abs(outputs))) if outputs.size else 0.0
    rms_tail = float(np.sqrt(np.mean(outputs[:, -10:] ** 2))) if outputs.shape[1] >= 10 else max_abs
    return {
        "stable": bool(finite and max_abs < 20.0 and rms_tail < 10.0),
        "finite": finite,
        "max_abs_y": max_abs,
        "tail_rms_y": rms_tail,
        "divergence_threshold": 20.0,
    }


def _input_output_correlations(inputs: np.ndarray, outputs: np.ndarray, max_lag: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for input_idx in range(inputs.shape[0]):
        for output_idx in range(outputs.shape[0]):
            best_lag = 0
            best_corr = 0.0
            for lag in range(max_lag + 1):
                x = inputs[input_idx, : inputs.shape[1] - lag if lag else inputs.shape[1]]
                y = outputs[output_idx, lag:]
                corr = _safe_corr(x, y)
                if abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_lag = lag
            rows.append(
                {
                    "jet_id": f"J{input_idx + 1:02d}",
                    "output_id": f"Y{output_idx + 1}",
                    "best_lag": best_lag,
                    "correlation": best_corr,
                    "abs_correlation": abs(best_corr),
                }
            )
    return rows


def _safe_corr(left: np.ndarray, right: np.ndarray) -> float:
    if left.size < 2 or right.size < 2 or np.std(left) == 0.0 or np.std(right) == 0.0:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def _influence_ranking(
    config: MockPlantConfig, seed: int, horizon: int, amplitude: float
) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    baseline = _impulse_response(config, seed, None, horizon, amplitude)
    for input_idx in range(config.n_inputs):
        response = _impulse_response(config, seed, input_idx, horizon, amplitude)
        delta = response - baseline
        score = float(np.sqrt(np.sum(delta * delta)))
        peak = float(np.max(np.linalg.norm(delta, axis=0)))
        scores.append({"jet_id": f"J{input_idx + 1:02d}", "score": score, "peak_delta": peak})
    scores.sort(key=lambda item: item["score"], reverse=True)
    for rank, item in enumerate(scores, start=1):
        item["rank"] = rank
    return scores


def _impulse_response(
    config: MockPlantConfig, seed: int, input_idx: int | None, horizon: int, amplitude: float
) -> np.ndarray:
    plant = MockPlant(config).reset(seed)
    rows = []
    for step_idx in range(horizon):
        u = np.zeros(config.n_inputs)
        if input_idx is not None and step_idx == 0:
            u[input_idx] = amplitude
        rows.append(plant.step(u))
    return np.asarray(rows, dtype=float).T


def _write_matrix_csv(path: Path, matrix: np.ndarray, label_prefix: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([label_prefix, *[f"t{idx}" for idx in range(matrix.shape[1])]])
        for row_idx, row in enumerate(matrix, start=1):
            writer.writerow([f"{label_prefix}_{row_idx:02d}", *[f"{value:.8g}" for value in row]])


def _write_correlations_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["jet_id", "output_id", "best_lag", "correlation", "abs_correlation"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_ranking_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rank", "jet_id", "score", "peak_delta"])
        writer.writeheader()
        writer.writerows(rows)


def _write_input_heatmap_svg(path: Path, inputs: np.ndarray) -> None:
    cell_w = 9
    cell_h = 10
    left = 48
    top = 26
    width = left + inputs.shape[1] * cell_w + 18
    height = top + inputs.shape[0] * cell_h + 28
    max_abs = max(float(np.max(np.abs(inputs))), 1.0)
    lines = _svg_header(width, height, "24xT input heatmap")
    for row_idx in range(inputs.shape[0]):
        y = top + row_idx * cell_h
        lines.append(_svg_text(6, y + 8, f"J{row_idx + 1:02d}", 8, "#2b2f33"))
        for col_idx in range(inputs.shape[1]):
            x = left + col_idx * cell_w
            value = float(inputs[row_idx, col_idx] / max_abs)
            fill = _blend("#f4f7fb", "#1864ab", max(0.0, value))
            lines.append(f'<rect x="{x}" y="{y}" width="8" height="9" fill="{fill}"/>')
    _append_time_ticks(lines, left, top + inputs.shape[0] * cell_h + 12, inputs.shape[1], cell_w)
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_output_timeseries_svg(path: Path, outputs: np.ndarray) -> None:
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
    lines = _svg_header(width, height, "6 output time series")
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
        lines.append(_svg_text(left + 8 + output_idx * 58, height - 12, f"Y{output_idx + 1}", 11, color))
    lines.append(_svg_text(8, top + 12, f"{max_y:.2f}", 9, "#57606a"))
    lines.append(_svg_text(8, top + plot_h, f"{min_y:.2f}", 9, "#57606a"))
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def _svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        _svg_text(8, 16, title, 13, "#1f2328"),
    ]


def _svg_text(x: float, y: float, text: str, size: int, fill: str) -> str:
    return f'<text x="{x}" y="{y}" font-family="Arial, sans-serif" font-size="{size}" fill="{fill}">{text}</text>'


def _append_time_ticks(lines: list[str], left: int, y: int, count: int, cell_w: int) -> None:
    step = max(1, count // 10)
    for col_idx in range(0, count, step):
        x = left + col_idx * cell_w
        lines.append(_svg_text(x, y, str(col_idx), 8, "#57606a"))


def _blend(left_hex: str, right_hex: str, amount: float) -> str:
    amount = min(max(amount, 0.0), 1.0)
    left = np.array([int(left_hex[idx : idx + 2], 16) for idx in (1, 3, 5)])
    right = np.array([int(right_hex[idx : idx + 2], 16) for idx in (1, 3, 5)])
    rgb = np.round(left * (1.0 - amount) + right * amount).astype(int)
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


if __name__ == "__main__":
    main()
