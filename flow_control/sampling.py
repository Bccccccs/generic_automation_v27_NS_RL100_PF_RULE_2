"""Helpers for expanding control-window schedules to sample-level rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def expand_schedule_rows(
    schedule_rows: list[dict[str, Any]],
    *,
    time_step: float | None = None,
) -> list[dict[str, Any]]:
    """Return sample-level rows for a control-window actuation schedule.

    With ``time_step`` unset or non-positive, the original window-level rows are
    returned.  With a positive step, each schedule window is expanded into
    ``ceil(window_duration / time_step)`` rows. The expanded rows keep the
    original ``window_id`` and actuation commands, while ``physical_time`` /
    ``t_start`` / ``t_end`` describe the sample interval.
    """
    if not schedule_rows:
        return []
    dt = float(time_step or 0.0)
    if dt <= 0.0:
        return [dict(row) for row in schedule_rows]

    expanded: list[dict[str, Any]] = []
    for row_idx, row in enumerate(schedule_rows):
        start = _row_start(row, row_idx)
        end = _row_end(schedule_rows, row_idx, start, dt)
        if end <= start:
            end = start + dt
        current = start
        while current < end - 1.0e-12:
            next_time = min(current + dt, end)
            sample = dict(row)
            sample["physical_time"] = round(current, 12)
            sample["t_start"] = round(current, 12)
            sample["t_end"] = round(next_time, 12)
            expanded.append(sample)
            current = next_time
    return expanded


def infer_time_step(rows: list[dict[str, Any]]) -> float:
    """Infer the row-to-row sample interval from sample-level rows."""
    if len(rows) < 2:
        return _row_duration(rows[0], 0.0) if rows else 0.0
    return float(rows[1].get("physical_time", 0.0)) - float(rows[0].get("physical_time", 0.0))


def infer_window_duration(rows: list[dict[str, Any]]) -> float:
    """Infer the first control-window duration from schedule or sample rows."""
    if not rows:
        return 0.0
    row = rows[0]
    if "window_id" in row and "physical_time" in row:
        first_window = str(row.get("window_id"))
        first_time = float(row.get("physical_time", 0.0))
        for later in rows[1:]:
            if str(later.get("window_id")) != first_window:
                return float(later.get("physical_time", first_time)) - first_time
    if "t_start" in row and "t_end" in row:
        return float(row["t_end"]) - float(row["t_start"])
    if len(rows) > 1:
        return float(rows[1].get("physical_time", 0.0)) - float(rows[0].get("physical_time", 0.0))
    return 0.0


def resolve_schedule_time_step(
    schedule_path: str | Path,
    *,
    explicit_time_step: float | None = None,
) -> tuple[float | None, str]:
    """Resolve the sample time step for an existing schedule.

    Priority order:
    1. an explicit function/CLI argument;
    2. ``config_summary.yaml`` next to the schedule or its sibling ``input/``;
    3. no override, which lets callers fall back to schedule window rows.
    """
    if explicit_time_step is not None:
        return float(explicit_time_step), "argument"
    config_time_step = read_schedule_config_time_step(schedule_path)
    if config_time_step is not None:
        return config_time_step, "config_summary"
    return None, "schedule_window"


def read_schedule_config_time_step(schedule_path: str | Path) -> float | None:
    """Read ``time_step`` from schedule generation metadata when present."""
    for config_path in schedule_config_candidates(schedule_path):
        if not config_path.is_file():
            continue
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        value = data.get("time_step_seconds")
        if value is None:
            value = data.get("time_step")
        if value is None and isinstance(data.get("actuation"), dict):
            value = data["actuation"].get("time_step")
        if value in (None, ""):
            continue
        time_step = float(value)
        if time_step > 0.0:
            return time_step
    return None


def schedule_config_candidates(schedule_path: str | Path) -> list[Path]:
    """Return likely config-summary locations for a schedule CSV."""
    parent = Path(schedule_path).parent
    candidates = [parent / "config_summary.yaml"]
    if parent.name == "input":
        candidates.append(parent.parent / "config_summary.yaml")
    else:
        candidates.append(parent / "input" / "config_summary.yaml")
    return candidates


def _row_start(row: dict[str, Any], row_idx: int) -> float:
    for key in ("t_start", "physical_time"):
        value = row.get(key)
        if value not in (None, ""):
            return float(value)
    return float(row_idx)


def _row_end(rows: list[dict[str, Any]], row_idx: int, start: float, fallback_dt: float) -> float:
    row = rows[row_idx]
    value = row.get("t_end")
    if value not in (None, ""):
        return float(value)
    if row_idx + 1 < len(rows):
        next_start = _row_start(rows[row_idx + 1], row_idx + 1)
        if next_start > start:
            return next_start
    return start + fallback_dt


def _row_duration(row: dict[str, Any], default: float) -> float:
    if "t_start" in row and "t_end" in row:
        return float(row["t_end"]) - float(row["t_start"])
    return default
