"""Shared data model and IO helpers for jet actuation patterns."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from flow_control.data_schema import JET_COLUMNS

MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
SUPPORTED_ACTUATION_MODES = {
    "no_jet_reference",
    "pulse_singlejet",
    "step_singlejet",
    "chirp_keyjets",
    "prbs_demo",
    "sparse_random_groups",
}


@dataclass(frozen=True)
class ActuationConfig:
    """Configuration for physical-time jet actuation schedule generation."""

    n_jets: int = 24
    mode: str = "sparse_random_groups"
    mass_flow_rate: float = 1.0
    command_amplitude: float | None = None
    window_duration: float = 0.1
    random_seed: int = 20260618
    output_dir: Path = Path("runs/schedule_examples/sparse24")
    total_windows: int = 10
    jet_ids: tuple[int, ...] = ()
    pulse_windows: tuple[int, ...] = ()
    step_start_window: int = 1
    step_end_window: int | None = None
    n_active_per_window: int = 3
    n_excitation_windows: int = 72
    n_reference_windows: int = 8
    max_consecutive_on: int = 2
    equal_activation_count: bool = True
    max_generation_attempts: int = 300
    max_active_jets: int | None = None
    max_total_mass_flow: float | None = None
    chirp_start_frequency_hz: float = 1.0
    chirp_end_frequency_hz: float = 8.0
    prbs_switch_probability: float = 0.35

    def __post_init__(self) -> None:
        if self.command_amplitude is not None:
            object.__setattr__(self, "mass_flow_rate", float(self.command_amplitude))
        else:
            object.__setattr__(self, "command_amplitude", float(self.mass_flow_rate))
        if not isinstance(self.output_dir, Path):
            object.__setattr__(self, "output_dir", Path(self.output_dir))

    @property
    def jet_names(self) -> list[str]:
        return [f"JET_{idx:02d}" for idx in range(1, self.n_jets + 1)]

    @property
    def massflow_names(self) -> list[str]:
        return [f"cmd_massflow_{idx:02d}" for idx in range(1, self.n_jets + 1)]

    @property
    def sparse_total_windows(self) -> int:
        return self.n_excitation_windows + self.n_reference_windows

    @property
    def expected_count_per_jet(self) -> int:
        total_activations = self.n_excitation_windows * self.n_active_per_window
        if total_activations % self.n_jets != 0:
            raise ValueError(
                "n_excitation_windows * n_active_per_window must be divisible by n_jets"
            )
        return total_activations // self.n_jets

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ActuationConfig":
        actuation = data.get("actuation", {})
        output = data.get("output", {})
        if not actuation:
            raise ValueError("config must contain an 'actuation' section")

        mode = str(actuation.get("mode", "sparse_random_groups")).strip()
        if mode not in SUPPORTED_ACTUATION_MODES:
            raise ValueError(
                f"unsupported actuation mode {mode!r}; expected one of "
                f"{sorted(SUPPORTED_ACTUATION_MODES)}"
            )

        total_windows = int(
            actuation.get(
                "total_windows",
                int(actuation.get("n_excitation_windows", 72))
                + int(actuation.get("n_reference_windows", 8))
                if mode == "sparse_random_groups"
                else 10,
            )
        )
        jet_ids = actuation.get("jet_ids", actuation.get("key_jets", []))
        pulse_windows = actuation.get("pulse_windows", [])
        return cls(
            n_jets=int(actuation.get("n_jets", 24)),
            mode=mode,
            mass_flow_rate=float(
                actuation.get("mass_flow_rate", actuation.get("command_amplitude", 1.0))
            ),
            window_duration=float(actuation.get("window_duration", 0.1)),
            random_seed=int(actuation.get("random_seed", 20260618)),
            output_dir=Path(output.get("run_dir", f"runs/schedule_examples/{mode}")),
            total_windows=total_windows,
            jet_ids=tuple(int(value) for value in jet_ids),
            pulse_windows=tuple(int(value) for value in pulse_windows),
            step_start_window=int(actuation.get("step_start_window", 1)),
            step_end_window=(
                int(actuation["step_end_window"])
                if "step_end_window" in actuation
                else None
            ),
            n_active_per_window=int(actuation.get("n_active_per_window", 3)),
            n_excitation_windows=int(actuation.get("n_excitation_windows", 72)),
            n_reference_windows=int(actuation.get("n_reference_windows", 8)),
            max_consecutive_on=int(actuation.get("max_consecutive_on", 2)),
            equal_activation_count=bool(actuation.get("equal_activation_count", True)),
            max_generation_attempts=int(actuation.get("max_generation_attempts", 300)),
            max_active_jets=(
                int(actuation["max_active_jets"])
                if "max_active_jets" in actuation
                else None
            ),
            max_total_mass_flow=(
                float(actuation["max_total_mass_flow"])
                if "max_total_mass_flow" in actuation
                else None
            ),
            chirp_start_frequency_hz=float(actuation.get("chirp_start_frequency_hz", 1.0)),
            chirp_end_frequency_hz=float(actuation.get("chirp_end_frequency_hz", 8.0)),
            prbs_switch_probability=float(actuation.get("prbs_switch_probability", 0.35)),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ActuationConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_mapping(yaml.safe_load(handle) or {})


@dataclass(frozen=True)
class ScheduleTable:
    """Unified schedule table: switch columns and commanded mass-flow columns."""

    switches: list[list[int]]
    massflows: list[list[float]]

    @property
    def n_windows(self) -> int:
        return len(self.switches)

    @property
    def n_jets(self) -> int:
        return len(self.switches[0]) if self.switches else 0


def table_from_switches(
    switches: list[list[int]],
    *,
    mass_flow_rate: float,
    massflows: list[list[float]] | None = None,
) -> ScheduleTable:
    if massflows is None:
        massflows = [
            [float(value) * mass_flow_rate for value in row]
            for row in switches
        ]
    return ScheduleTable(switches=switches, massflows=massflows)


def empty_switches(total_windows: int, n_jets: int) -> list[list[int]]:
    return [[0] * n_jets for _ in range(total_windows)]


def jet_index(jet_id: int, n_jets: int) -> int:
    if not 1 <= jet_id <= n_jets:
        raise ValueError(f"jet_id must be in [1, {n_jets}], got {jet_id}")
    return jet_id - 1


def rows_from_table(config: ActuationConfig, table: ScheduleTable) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for window_id, (switch_row, massflow_row) in enumerate(zip(table.switches, table.massflows)):
        start = round(window_id * config.window_duration, 12)
        end = round(start + config.window_duration, 12)
        record: dict[str, Any] = {
            "physical_time": start,
            "window_id": window_id,
            "t_start": start,
            "t_end": end,
        }
        for idx, column in enumerate(config.jet_names):
            record[column] = int(switch_row[idx]) if idx < len(switch_row) else 0
        for idx, column in enumerate(config.massflow_names):
            record[column] = float(massflow_row[idx]) if idx < len(massflow_row) else 0.0
        rows.append(record)
    return rows


def write_schedule_csv(config: ActuationConfig, table: ScheduleTable) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    columns = ["physical_time", "window_id", "t_start", "t_end", *config.jet_names, *config.massflow_names]
    with (config.output_dir / "actuation_schedule.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows_from_table(config, table):
            writer.writerow(row)


def write_config_summary(
    config: ActuationConfig,
    table: ScheduleTable,
    validation_errors: list[str],
    extra: dict[str, Any] | None = None,
) -> None:
    active_counts = [sum(row[idx] for row in table.switches) for idx in range(table.n_jets)]
    total_massflows = [sum(row) for row in table.massflows]
    summary = {
        "mode": config.mode,
        "random_seed": config.random_seed,
        "n_jets": config.n_jets,
        "total_windows": table.n_windows,
        "window_duration_seconds": config.window_duration,
        "mass_flow_rate": config.mass_flow_rate,
        "max_active_jets_observed": max((sum(row) for row in table.switches), default=0),
        "max_total_mass_flow_observed": max(total_massflows, default=0.0),
        "activation_counts": {
            config.jet_names[idx]: count for idx, count in enumerate(active_counts)
        },
        "outputs": {
            "schedule": "actuation_schedule.csv",
            "heatmap": "actuation_heatmap.svg",
            "total_mass_flow": "total_mass_flow.csv",
            "total_mass_flow_curve": "total_mass_flow_curve.svg",
            "validation_report": "validation_report.json",
        },
        "validation": {
            "passed": not validation_errors,
            "errors": validation_errors,
        },
        "notes": {
            "time_columns": "physical_time, t_start, and t_end are seconds, not solver iterations",
            "switch_columns": "JET_01..JET_24 are 0/1 valve states",
            "massflow_columns": "cmd_massflow_01..cmd_massflow_24 are commanded mass flow values",
        },
    }
    if extra:
        summary["extra"] = extra
    with (config.output_dir / "config_summary.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump(summary, handle, sort_keys=False, allow_unicode=True)
    with (config.output_dir / "validation_report.json").open("w", encoding="utf-8") as handle:
        json.dump(summary["validation"], handle, indent=2, ensure_ascii=False)


def write_total_mass_flow_csv(config: ActuationConfig, table: ScheduleTable) -> None:
    with (config.output_dir / "total_mass_flow.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["physical_time", "window_id", "t_start", "t_end", "active_jets", "total_mass_flow"])
        for window_id, (switch_row, massflow_row) in enumerate(zip(table.switches, table.massflows)):
            start = round(window_id * config.window_duration, 12)
            end = round(start + config.window_duration, 12)
            writer.writerow(
                [
                    start,
                    window_id,
                    start,
                    end,
                    sum(switch_row),
                    sum(massflow_row),
                ]
            )


def write_heatmap_svg(config: ActuationConfig, table: ScheduleTable) -> None:
    cell = 10
    label_width = 64
    label_height = 28
    title = f"{config.mode} switch heatmap"
    width = max(label_width + table.n_windows * cell + 16, 18 + len(title) * 8)
    height = label_height + table.n_jets * cell + 30
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="8" y="18" font-family="Arial" font-size="12" fill="#1f2328">{title}</text>',
    ]
    for jet_idx, jet_name in enumerate(config.jet_names):
        y = label_height + jet_idx * cell
        lines.append(f'<text x="6" y="{y + 8}" font-family="Arial" font-size="8" fill="#333">{jet_name}</text>')
        for window_idx in range(table.n_windows):
            x = label_width + window_idx * cell
            value = table.switches[window_idx][jet_idx]
            fill = "#1f77b4" if value else "#f1f3f5"
            lines.append(f'<rect x="{x}" y="{y}" width="{cell - 1}" height="{cell - 1}" fill="{fill}"/>')
    lines.append("</svg>")
    (config.output_dir / "actuation_heatmap.svg").write_text("\n".join(lines), encoding="utf-8")


def write_total_mass_flow_svg(config: ActuationConfig, table: ScheduleTable) -> None:
    points = [
        (window_id, sum(row))
        for window_id, row in enumerate(table.massflows)
    ]
    _write_line_svg(
        config.output_dir / "total_mass_flow_curve.svg",
        title=f"{config.mode} total commanded mass flow",
        points=points,
        y_min=0.0,
    )


def _write_line_svg(path: Path, title: str, points: list[tuple[float, float]], y_min: float = 0.0) -> None:
    width = 860
    height = 300
    left = 58
    top = 34
    plot_w = 776
    plot_h = 212
    x_values = [point[0] for point in points] or [0.0]
    y_values = [point[1] for point in points] or [0.0]
    x0 = min(x_values)
    x1 = max(x_values) if max(x_values) != x0 else x0 + 1.0
    y0 = min(y_min, min(y_values))
    y1 = max(y_values) if max(y_values) != y0 else y0 + 1.0
    pad = (y1 - y0) * 0.08
    y1 += pad

    def sx(value: float) -> float:
        return left + (value - x0) * plot_w / (x1 - x0)

    def sy(value: float) -> float:
        return top + (y1 - value) * plot_h / (y1 - y0)

    polyline = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in points)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="12" y="20" font-family="Arial" font-size="14" fill="#1f2328">{title}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fbfcfe" stroke="#d0d7de"/>',
        f'<polyline fill="none" stroke="#1864ab" stroke-width="2" points="{polyline}"/>',
        "</svg>",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def validate_table(config: ActuationConfig, table: ScheduleTable) -> list[str]:
    errors: list[str] = []
    if table.n_windows <= 0:
        errors.append("schedule must contain at least one window")
    if table.n_jets != config.n_jets:
        errors.append(f"expected {config.n_jets} jet columns, got {table.n_jets}")
    if len(table.massflows) != len(table.switches):
        errors.append("switch and mass-flow rows must have the same length")
    for window_id, (switch_row, massflow_row) in enumerate(zip(table.switches, table.massflows)):
        if len(switch_row) != config.n_jets or len(massflow_row) != config.n_jets:
            errors.append(f"window {window_id} must contain {config.n_jets} switch and mass-flow values")
            continue
        active = sum(int(value) for value in switch_row)
        total_mass_flow = sum(float(value) for value in massflow_row)
        if config.max_active_jets is not None and active > config.max_active_jets:
            errors.append(f"window {window_id} has {active} active jets, max is {config.max_active_jets}")
        if config.max_total_mass_flow is not None and total_mass_flow > config.max_total_mass_flow + 1e-12:
            errors.append(
                f"window {window_id} total mass flow {total_mass_flow} exceeds {config.max_total_mass_flow}"
            )
        for idx, (switch_value, massflow_value) in enumerate(zip(switch_row, massflow_row), start=1):
            if switch_value not in (0, 1):
                errors.append(f"window {window_id} JET_{idx:02d} must be 0 or 1")
            if massflow_value < 0.0 or math.isnan(massflow_value):
                errors.append(f"window {window_id} cmd_massflow_{idx:02d} must be non-negative")
            if switch_value == 0 and abs(massflow_value) > 1e-12:
                errors.append(f"window {window_id} JET_{idx:02d}=0 requires cmd_massflow_{idx:02d}=0")
            if switch_value == 1 and massflow_value <= 0.0:
                errors.append(f"window {window_id} JET_{idx:02d}=1 requires cmd_massflow_{idx:02d}>0")
    return errors


def write_pattern_outputs(
    config: ActuationConfig,
    table: ScheduleTable,
    *,
    validation_errors: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    errors = validate_table(config, table)
    if validation_errors:
        errors.extend(validation_errors)
    write_schedule_csv(config, table)
    write_total_mass_flow_csv(config, table)
    write_heatmap_svg(config, table)
    write_total_mass_flow_svg(config, table)
    write_config_summary(config, table, errors, extra=extra)
    if errors:
        raise RuntimeError(f"generated schedule failed validation: {errors}")


def generate_pattern_table(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, Any], list[str]]:
    generators: dict[str, Callable[[ActuationConfig], tuple[ScheduleTable, dict[str, Any], list[str]]]] = {}
    from .chirp import generate as generate_chirp
    from .prbs import generate as generate_prbs
    from .pulse import generate as generate_pulse
    from .reference import generate as generate_reference
    from .sparse_groups import generate as generate_sparse
    from .step import generate as generate_step

    generators.update(
        {
            "no_jet_reference": generate_reference,
            "pulse_singlejet": generate_pulse,
            "step_singlejet": generate_step,
            "chirp_keyjets": generate_chirp,
            "prbs_demo": generate_prbs,
            "sparse_random_groups": generate_sparse,
        }
    )
    return generators[config.mode](config)
