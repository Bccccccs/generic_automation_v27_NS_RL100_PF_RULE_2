"""Utilities for the B06 minimal input-output ARX ROM workflow."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

import numpy as np

from starccm.control.control_spec import JET_COLUMNS, LOAD_COLUMNS

MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
ROM_OUTPUT_COLUMNS = (*LOAD_COLUMNS, "Fz_Total")
ROM_INPUT_COLUMNS = (*JET_COLUMNS, *MASSFLOW_COLUMNS)
REGIONAL_OUTPUT_COLUMNS = tuple(LOAD_COLUMNS)


def load_case_table(case_dir: str | Path) -> list[dict[str, str]]:
    """Load and merge ``timeseries.csv`` with ``actuation_schedule.csv`` if present."""
    case_path = Path(case_dir)
    timeseries_path = case_path / "timeseries.csv"
    schedule_path = case_path / "actuation_schedule.csv"
    rows = read_csv_rows(timeseries_path)
    if not rows:
        raise ValueError(f"{timeseries_path} contains no rows")
    if schedule_path.exists():
        rows = merge_schedule_columns(rows, read_csv_rows(schedule_path))
    return rows


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def merge_schedule_columns(
    timeseries_rows: list[dict[str, str]],
    schedule_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Attach mass-flow command columns to timeseries rows.

    Prefer ``window_id`` when available, otherwise fall back to rounded
    ``physical_time``.  Existing timeseries columns are preserved.
    """
    if not schedule_rows:
        return [dict(row) for row in timeseries_rows]
    schedule_by_key = {_row_key(row): row for row in schedule_rows}
    merged: list[dict[str, str]] = []
    for row in timeseries_rows:
        record = dict(row)
        schedule = schedule_by_key.get(_row_key(row), {})
        for column in MASSFLOW_COLUMNS:
            if column not in record and column in schedule:
                record[column] = schedule[column]
        merged.append(record)
    return merged


def require_columns(rows: list[dict[str, str]], columns: tuple[str, ...] | list[str]) -> None:
    available = set(rows[0]) if rows else set()
    missing = [column for column in columns if column not in available]
    if missing:
        raise ValueError("missing required columns: " + ", ".join(missing))


def matrix_from_rows(rows: list[dict[str, str]], columns: tuple[str, ...] | list[str]) -> np.ndarray:
    require_columns(rows, columns)
    data = np.zeros((len(rows), len(columns)), dtype=float)
    for row_idx, row in enumerate(rows):
        for col_idx, column in enumerate(columns):
            data[row_idx, col_idx] = _to_float(row.get(column, "0"), column, row_idx)
    return data


def time_values_from_rows(rows: list[dict[str, str]]) -> np.ndarray:
    if rows and "physical_time" in rows[0]:
        return matrix_from_rows(rows, ["physical_time"]).ravel()
    return np.arange(len(rows), dtype=float)


def compute_metrics(
    truth: np.ndarray,
    prediction: np.ndarray,
    output_columns: tuple[str, ...] | list[str],
) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    errors = prediction - truth
    for idx, column in enumerate(output_columns):
        y = truth[:, idx]
        y_hat = prediction[:, idx]
        err = errors[:, idx]
        rmse = float(np.sqrt(np.mean(err * err)))
        value_range = float(np.max(y) - np.min(y))
        nrmse = rmse / value_range if value_range > 1.0e-12 else float("nan")
        if np.std(y) <= 1.0e-12 or np.std(y_hat) <= 1.0e-12:
            corr = float("nan")
        else:
            corr = float(np.corrcoef(y, y_hat)[0, 1])
        metrics[column] = {
            "rmse": rmse,
            "nrmse_range": float(nrmse),
            "correlation": corr,
            "mean_error": float(np.mean(err)),
            "max_abs_error": float(np.max(np.abs(err))),
        }
    return metrics


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=True) + "\n",
        encoding="utf-8",
    )


def write_prediction_csv(
    path: str | Path,
    time_values: np.ndarray,
    truth: np.ndarray,
    prediction: np.ndarray,
    output_columns: tuple[str, ...] | list[str],
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["physical_time"]
    for column in output_columns:
        fieldnames.extend([f"{column}_true", f"{column}_pred", f"{column}_error"])
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for idx, t in enumerate(time_values):
            row: dict[str, Any] = {"physical_time": float(t)}
            for col_idx, column in enumerate(output_columns):
                row[f"{column}_true"] = float(truth[idx, col_idx])
                row[f"{column}_pred"] = float(prediction[idx, col_idx])
                row[f"{column}_error"] = float(prediction[idx, col_idx] - truth[idx, col_idx])
            writer.writerow(row)


def write_prediction_svg(
    path: str | Path,
    time_values: np.ndarray,
    truth: np.ndarray,
    prediction: np.ndarray,
    output_columns: tuple[str, ...] | list[str] = REGIONAL_OUTPUT_COLUMNS,
) -> None:
    panels = []
    for idx, column in enumerate(output_columns):
        panels.append((column, truth[:, idx], prediction[:, idx]))
    _write_panel_svg(path, time_values, panels, "ARX ROM validation: true vs prediction", pair_mode=True)


def write_error_svg(
    path: str | Path,
    time_values: np.ndarray,
    truth: np.ndarray,
    prediction: np.ndarray,
    output_columns: tuple[str, ...] | list[str] = REGIONAL_OUTPUT_COLUMNS,
) -> None:
    panels = []
    for idx, column in enumerate(output_columns):
        panels.append((column, prediction[:, idx] - truth[:, idx], None))
    _write_panel_svg(path, time_values, panels, "ARX ROM validation error", pair_mode=False)


def write_rmse_bar_svg(
    path: str | Path,
    metrics: dict[str, dict[str, float]],
    output_columns: tuple[str, ...] | list[str] = REGIONAL_OUTPUT_COLUMNS,
) -> None:
    width = 920
    height = 360
    margin_left = 72
    margin_bottom = 58
    plot_w = width - margin_left - 32
    plot_h = height - 70 - margin_bottom
    values = [float(metrics[column]["rmse"]) for column in output_columns]
    max_value = max(values) if values else 1.0
    max_value = max(max_value, 1.0e-12)
    bar_w = plot_w / max(len(values), 1) * 0.62
    gap = plot_w / max(len(values), 1)
    lines = _svg_header(width, height, "ARX ROM RMSE by load cell")
    lines.append(f'<rect x="{margin_left}" y="42" width="{plot_w}" height="{plot_h}" fill="#fbfcfe" stroke="#d0d7de"/>')
    for tick in range(5):
        y = 42 + plot_h - tick * plot_h / 4
        val = max_value * tick / 4
        lines.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + plot_w}" y2="{y:.2f}" stroke="#edf2f7"/>')
        lines.append(_text(8, y + 3, f"{val:.3g}", 10, "#57606a"))
    for idx, column in enumerate(output_columns):
        h = values[idx] / max_value * plot_h
        x = margin_left + idx * gap + (gap - bar_w) / 2
        y = 42 + plot_h - h
        lines.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{h:.2f}" fill="#1864ab"/>')
        lines.append(_text(x + bar_w / 2 - 24, 42 + plot_h + 22, column, 10, "#24292f"))
        lines.append(_text(x + bar_w / 2 - 20, y - 6, f"{values[idx]:.3g}", 10, "#24292f"))
    lines.append("</svg>")
    _write_text(path, "\n".join(lines))


def write_single_jet_response_summary(
    path: str | Path,
    rows: list[dict[str, str]],
    *,
    horizon_steps: int = 8,
    baseline_steps: int = 2,
) -> None:
    """Summarize output response after each jet rising edge."""
    output_columns = tuple(column for column in ROM_OUTPUT_COLUMNS if rows and column in rows[0])
    jet_columns = tuple(column for column in JET_COLUMNS if rows and column in rows[0])
    time_values = time_values_from_rows(rows) if rows else np.asarray([], dtype=float)
    fieldnames = [
        "jet",
        "event_count",
        "dominant_output",
        "peak_delta",
        "peak_abs_delta",
        "peak_lag_steps",
        "peak_lag_seconds",
        "note",
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if not rows or not jet_columns or not output_columns:
            writer.writerow(
                {
                    "jet": "NA",
                    "event_count": 0,
                    "dominant_output": "NA",
                    "peak_delta": "",
                    "peak_abs_delta": "",
                    "peak_lag_steps": "",
                    "peak_lag_seconds": "",
                    "note": "no jet/output columns available for single-jet response summary",
                }
            )
            return

        jets = matrix_from_rows(rows, jet_columns)
        outputs = matrix_from_rows(rows, output_columns)
        dt = float(np.median(np.diff(time_values))) if len(time_values) > 1 else 1.0
        for jet_idx, jet in enumerate(jet_columns):
            active = jets[:, jet_idx] > 0.5
            rising_edges = [idx for idx in range(1, len(active)) if active[idx] and not active[idx - 1]]
            best: dict[str, Any] | None = None
            for event_idx in rising_edges:
                baseline_start = max(0, event_idx - baseline_steps)
                baseline = np.mean(outputs[baseline_start:event_idx], axis=0) if event_idx > baseline_start else outputs[event_idx]
                stop = min(len(outputs), event_idx + horizon_steps + 1)
                delta = outputs[event_idx:stop] - baseline
                abs_delta = np.abs(delta)
                flat_idx = int(np.argmax(abs_delta))
                lag_idx, out_idx = np.unravel_index(flat_idx, abs_delta.shape)
                candidate = {
                    "dominant_output": output_columns[out_idx],
                    "peak_delta": float(delta[lag_idx, out_idx]),
                    "peak_abs_delta": float(abs_delta[lag_idx, out_idx]),
                    "peak_lag_steps": int(lag_idx),
                    "peak_lag_seconds": float(lag_idx * dt),
                }
                if best is None or candidate["peak_abs_delta"] > best["peak_abs_delta"]:
                    best = candidate
            if best is None:
                best = {
                    "dominant_output": "NA",
                    "peak_delta": "",
                    "peak_abs_delta": "",
                    "peak_lag_steps": "",
                    "peak_lag_seconds": "",
                }
            writer.writerow(
                {
                    "jet": jet,
                    "event_count": len(rising_edges),
                    "note": "rising edge summary from available case data",
                    **best,
                }
            )


def _row_key(row: dict[str, str]) -> tuple[str, str]:
    if "window_id" in row and row.get("window_id", "") != "":
        return ("window_id", str(int(float(row["window_id"]))))
    return ("physical_time", f"{float(row.get('physical_time', 0.0)):.12g}")


def _to_float(value: str | None, column: str, row_idx: int) -> float:
    try:
        return float(value if value not in (None, "") else 0.0)
    except ValueError as exc:
        raise ValueError(f"non-numeric value in row {row_idx}, column {column}: {value!r}") from exc


def _write_panel_svg(
    path: str | Path,
    time_values: np.ndarray,
    panels: list[tuple[str, np.ndarray, np.ndarray | None]],
    title: str,
    *,
    pair_mode: bool,
) -> None:
    width = 1040
    panel_h = 170
    top = 44
    height = top + panel_h * len(panels) + 30
    left = 76
    plot_w = width - left - 30
    plot_h = 116
    lines = _svg_header(width, height, title)
    for panel_idx, (label, first, second) in enumerate(panels):
        y0 = top + panel_idx * panel_h
        series_values = first if second is None else np.column_stack([first, second])
        min_y = float(np.min(series_values))
        max_y = float(np.max(series_values))
        if abs(max_y - min_y) < 1.0e-12:
            max_y += 1.0
            min_y -= 1.0
        lines.append(_text(12, y0 + 18, label, 13, "#24292f"))
        lines.append(f'<rect x="{left}" y="{y0}" width="{plot_w}" height="{plot_h}" fill="#fbfcfe" stroke="#d0d7de"/>')
        for tick in range(4):
            gy = y0 + tick * plot_h / 3
            lines.append(f'<line x1="{left}" y1="{gy:.2f}" x2="{left + plot_w}" y2="{gy:.2f}" stroke="#edf2f7"/>')
        lines.append(_polyline(time_values, first, left, y0, plot_w, plot_h, min_y, max_y, "#0b7285", 1.8))
        if second is not None:
            lines.append(_polyline(time_values, second, left, y0, plot_w, plot_h, min_y, max_y, "#c92a2a", 1.5))
            lines.append(_text(left + 10, y0 + 16, "true", 10, "#0b7285"))
            lines.append(_text(left + 54, y0 + 16, "pred", 10, "#c92a2a"))
        elif pair_mode:
            lines.append(_text(left + 10, y0 + 16, "series", 10, "#0b7285"))
        lines.append(_text(8, y0 + 42, f"{max_y:.3g}", 9, "#57606a"))
        lines.append(_text(8, y0 + plot_h, f"{min_y:.3g}", 9, "#57606a"))
    lines.append("</svg>")
    _write_text(path, "\n".join(lines))


def _polyline(
    time_values: np.ndarray,
    values: np.ndarray,
    left: float,
    top: float,
    width: float,
    height: float,
    min_y: float,
    max_y: float,
    color: str,
    stroke_width: float,
) -> str:
    min_t = float(np.min(time_values))
    max_t = float(np.max(time_values))
    if abs(max_t - min_t) < 1.0e-12:
        max_t = min_t + 1.0
    points = []
    for t, value in zip(time_values, values):
        x = left + (float(t) - min_t) / (max_t - min_t) * width
        y = top + height - (float(value) - min_y) / (max_y - min_y) * height
        points.append(f"{x:.2f},{y:.2f}")
    return f'<polyline fill="none" stroke="{color}" stroke-width="{stroke_width}" points="{" ".join(points)}"/>'


def _svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        _text(14, 24, title, 16, "#24292f"),
    ]


def _text(x: float, y: float, value: str, size: int, color: str) -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-family="Arial, sans-serif" '
        f'font-size="{size}" fill="{color}">{html.escape(str(value))}</text>'
    )


def _write_text(path: str | Path, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content + "\n", encoding="utf-8")
