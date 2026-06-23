"""Generate first-pass sparse-jet schedules from configuration."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

from .data_schema import ControlAction, ExperimentConfig, Schedule, ScheduleStep


def generate_schedule(config: ExperimentConfig) -> Schedule:
    """Build a simple uniform schedule that can be replaced by optimization later."""

    steps: list[ScheduleStep] = []
    interval = config.control_interval_iterations

    for step_id, iteration in enumerate(range(0, config.max_iterations, interval)):
        remaining = config.max_iterations - iteration
        duration = min(interval, remaining)
        actions = tuple(
            ControlAction(
                jet_id=jet_id,
                enabled=config.default_mass_flow_rate > 0.0,
                mass_flow_rate=config.default_mass_flow_rate,
                duty_cycle=config.default_duty_cycle,
                frequency_hz=config.default_frequency_hz,
            )
            for jet_id in config.jet_ids
        )
        steps.append(
            ScheduleStep(
                step_id=step_id,
                iteration=iteration,
                duration_iterations=duration,
                actions=actions,
            )
        )

    return Schedule(name=f"{config.case_name}_schedule", steps=tuple(steps))


@dataclass(frozen=True)
class ActuationConfig:
    """Configuration for constrained sparse random actuation windows."""

    n_jets: int
    n_active_per_window: int
    n_excitation_windows: int
    n_reference_windows: int
    command_amplitude: float
    window_duration: float
    max_consecutive_on: int
    equal_activation_count: bool
    random_seed: int
    output_dir: Path
    max_generation_attempts: int = 300

    @property
    def total_windows(self) -> int:
        return self.n_excitation_windows + self.n_reference_windows

    @property
    def expected_count_per_jet(self) -> int:
        total_activations = self.n_excitation_windows * self.n_active_per_window
        if total_activations % self.n_jets != 0:
            raise ValueError(
                "n_excitation_windows * n_active_per_window must be divisible by n_jets"
            )
        return total_activations // self.n_jets

    @property
    def jet_names(self) -> list[str]:
        return [f"JET_{idx:02d}" for idx in range(1, self.n_jets + 1)]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ActuationConfig":
        actuation = data.get("actuation", {})
        output = data.get("output", {})
        if not actuation:
            raise ValueError("config must contain an 'actuation' section for B3 generation")

        return cls(
            n_jets=int(actuation.get("n_jets", 24)),
            n_active_per_window=int(actuation.get("n_active_per_window", 3)),
            n_excitation_windows=int(actuation.get("n_excitation_windows", 72)),
            n_reference_windows=int(actuation.get("n_reference_windows", 8)),
            command_amplitude=float(actuation.get("command_amplitude", 1.0)),
            window_duration=float(actuation.get("window_duration", 1.0)),
            max_consecutive_on=int(actuation.get("max_consecutive_on", 2)),
            equal_activation_count=bool(actuation.get("equal_activation_count", True)),
            random_seed=int(actuation.get("random_seed", 20260618)),
            output_dir=Path(output.get("run_dir", "runs/pilot_sparse24")),
            max_generation_attempts=int(actuation.get("max_generation_attempts", 300)),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ActuationConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return cls.from_mapping(data)


def generate_actuation_matrix(config: ActuationConfig) -> list[list[int]]:
    """Generate a reproducible sparse random matrix with excitation then reference windows."""

    _validate_config_shape(config)
    base_rng = random.Random(config.random_seed)
    target_count = config.expected_count_per_jet

    for _ in range(config.max_generation_attempts):
        attempt_seed = base_rng.randrange(0, 2**63)
        rng = random.Random(attempt_seed)
        excitation = _build_excitation_windows(config, [target_count] * config.n_jets, rng)
        if excitation is not None:
            references = [[0] * config.n_jets for _ in range(config.n_reference_windows)]
            return excitation + references

    raise RuntimeError(
        "failed to generate a schedule satisfying all constraints; "
        "increase max_generation_attempts or relax constraints"
    )


def validate_actuation_matrix(config: ActuationConfig, matrix: list[list[int]]) -> list[str]:
    """Return validation errors for B3 actuation constraints."""

    errors: list[str] = []
    excitation = matrix[: config.n_excitation_windows]
    references = matrix[config.n_excitation_windows :]

    if len(matrix) != config.total_windows:
        errors.append(f"expected {config.total_windows} windows, got {len(matrix)}")
    if any(len(row) != config.n_jets for row in matrix):
        errors.append(f"all windows must have {config.n_jets} jet columns")

    for window_id, row in enumerate(excitation):
        active = sum(row)
        if active != config.n_active_per_window:
            errors.append(
                f"window {window_id} has {active} active jets, "
                f"expected {config.n_active_per_window}"
            )

    for ref_id, row in enumerate(references, start=config.n_excitation_windows):
        if sum(row) != 0:
            errors.append(f"reference window {ref_id} must have no active jets")

    if config.equal_activation_count:
        expected = config.expected_count_per_jet
        counts = activation_counts(config, matrix)
        for jet_name, count in zip(config.jet_names, counts):
            if count != expected:
                errors.append(f"{jet_name} appears {count} times, expected {expected}")

    combos = [tuple(idx for idx, value in enumerate(row) if value) for row in excitation]
    duplicates = sorted(combo for combo in set(combos) if combos.count(combo) > 1)
    if duplicates:
        rendered = ["+".join(config.jet_names[idx] for idx in combo) for combo in duplicates]
        errors.append(f"duplicate excitation combinations: {', '.join(rendered)}")

    for jet_idx, jet_name in enumerate(config.jet_names):
        streak = 0
        for window_id, row in enumerate(matrix):
            streak = streak + 1 if row[jet_idx] else 0
            if streak > config.max_consecutive_on:
                errors.append(
                    f"{jet_name} exceeds consecutive-on limit at window {window_id} "
                    f"(streak={streak})"
                )
                break

    return errors


def write_actuation_outputs(config: ActuationConfig, matrix: list[list[int]]) -> None:
    """Write schedule, diagnostics, and summary artifacts for B3."""

    config.output_dir.mkdir(parents=True, exist_ok=True)
    _write_schedule_csv(config, matrix)
    _write_counts_csv(config, matrix)
    _write_square_csv(config.output_dir / "pairwise_cooccurrence.csv", config.jet_names, pairwise_counts(matrix))
    _write_square_csv(
        config.output_dir / "input_correlation_matrix.csv",
        config.jet_names,
        correlation_matrix(matrix),
        float_format="{:.8f}",
    )
    _write_mass_flow_csv(config, matrix)
    _write_heatmap_svg(config, matrix)

    same_seed_matrix = generate_actuation_matrix(config)
    changed_seed_config = ActuationConfig(
        n_jets=config.n_jets,
        n_active_per_window=config.n_active_per_window,
        n_excitation_windows=config.n_excitation_windows,
        n_reference_windows=config.n_reference_windows,
        command_amplitude=config.command_amplitude,
        window_duration=config.window_duration,
        max_consecutive_on=config.max_consecutive_on,
        equal_activation_count=config.equal_activation_count,
        random_seed=config.random_seed + 1,
        output_dir=config.output_dir,
        max_generation_attempts=config.max_generation_attempts,
    )
    changed_seed_matrix = generate_actuation_matrix(changed_seed_config)
    validation_errors = validate_actuation_matrix(config, matrix)
    reproducibility = {
        "same_seed_reproduces": matrix == same_seed_matrix,
        "different_seed_changes_sequence": matrix != changed_seed_matrix,
    }
    summary = {
        "config": _config_summary(config),
        "random_seed": config.random_seed,
        "outputs": {
            "schedule": "actuation_schedule.csv",
            "heatmap": "actuation_heatmap.svg",
            "activation_counts": "activation_counts.csv",
            "pairwise_cooccurrence": "pairwise_cooccurrence.csv",
            "input_correlation_matrix": "input_correlation_matrix.csv",
            "mass_flow": "mass_flow.csv",
        },
        "validation": {
            "passed": not validation_errors and all(reproducibility.values()),
            "errors": validation_errors,
            **reproducibility,
        },
    }
    with (config.output_dir / "config_summary.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump(summary, handle, sort_keys=False, allow_unicode=True)
    with (config.output_dir / "validation_report.json").open("w", encoding="utf-8") as handle:
        json.dump(summary["validation"], handle, indent=2, ensure_ascii=False)

    if summary["validation"]["passed"] is not True:
        raise RuntimeError(f"generated schedule failed validation: {summary['validation']}")


def activation_counts(config: ActuationConfig, matrix: list[list[int]]) -> list[int]:
    excitation = matrix[: config.n_excitation_windows]
    return [sum(row[jet_idx] for row in excitation) for jet_idx in range(config.n_jets)]


def pairwise_counts(matrix: list[list[int]]) -> list[list[int]]:
    n_jets = len(matrix[0]) if matrix else 0
    return [
        [
            sum(1 for row in matrix if row[left] and row[right])
            for right in range(n_jets)
        ]
        for left in range(n_jets)
    ]


def correlation_matrix(matrix: list[list[int]]) -> list[list[float]]:
    n_jets = len(matrix[0]) if matrix else 0
    columns = [[row[jet_idx] for row in matrix] for jet_idx in range(n_jets)]
    means = [mean(column) for column in columns]
    variances = [
        sum((value - means[idx]) ** 2 for value in column)
        for idx, column in enumerate(columns)
    ]

    correlations: list[list[float]] = []
    for left in range(n_jets):
        row: list[float] = []
        for right in range(n_jets):
            denom = math.sqrt(variances[left] * variances[right])
            if denom == 0:
                row.append(1.0 if left == right else 0.0)
            else:
                numerator = sum(
                    (columns[left][idx] - means[left]) * (columns[right][idx] - means[right])
                    for idx in range(len(matrix))
                )
                row.append(numerator / denom)
        correlations.append(row)
    return correlations


def _build_excitation_windows(
    config: ActuationConfig, remaining: list[int], rng: random.Random
) -> list[list[int]] | None:
    sequence: list[tuple[int, ...]] = []
    used_combos: set[tuple[int, ...]] = set()

    def recurse(window_idx: int) -> bool:
        if window_idx == config.n_excitation_windows:
            return all(count == 0 for count in remaining)

        candidates = _candidate_combinations(config, remaining, sequence, used_combos, rng)
        for combo in candidates:
            for jet_idx in combo:
                remaining[jet_idx] -= 1
            sequence.append(combo)
            used_combos.add(combo)

            if recurse(window_idx + 1):
                return True

            used_combos.remove(combo)
            sequence.pop()
            for jet_idx in combo:
                remaining[jet_idx] += 1

        return False

    if not recurse(0):
        return None

    rows: list[list[int]] = []
    for combo in sequence:
        row = [0] * config.n_jets
        for jet_idx in combo:
            row[jet_idx] = 1
        rows.append(row)
    return rows


def _candidate_combinations(
    config: ActuationConfig,
    remaining: list[int],
    sequence: list[tuple[int, ...]],
    used_combos: set[tuple[int, ...]],
    rng: random.Random,
) -> list[tuple[int, ...]]:
    windows_left_after_pick = config.n_excitation_windows - len(sequence) - 1
    blocked = _currently_blocked_jets(config, sequence)
    available = [
        jet_idx for jet_idx, count in enumerate(remaining) if count > 0 and jet_idx not in blocked
    ]

    candidates: list[tuple[float, tuple[int, ...]]] = []
    for combo in itertools.combinations(available, config.n_active_per_window):
        if combo in used_combos:
            continue
        if any(remaining[jet_idx] <= 0 for jet_idx in combo):
            continue
        after = remaining[:]
        for jet_idx in combo:
            after[jet_idx] -= 1
        if any(count < 0 or count > windows_left_after_pick for count in after):
            continue

        scarcity_score = sum(remaining[jet_idx] for jet_idx in combo)
        balance_penalty = max(after) - min(after)
        score = scarcity_score * 10 - balance_penalty + rng.random()
        candidates.append((score, combo))

    candidates.sort(reverse=True)
    return [combo for _, combo in candidates]


def _currently_blocked_jets(config: ActuationConfig, sequence: list[tuple[int, ...]]) -> set[int]:
    if config.max_consecutive_on <= 0 or len(sequence) < config.max_consecutive_on:
        return set()

    recent = sequence[-config.max_consecutive_on :]
    blocked: set[int] = set()
    for jet_idx in range(config.n_jets):
        if all(jet_idx in combo for combo in recent):
            blocked.add(jet_idx)
    return blocked


def _validate_config_shape(config: ActuationConfig) -> None:
    if config.n_jets <= 0:
        raise ValueError("n_jets must be positive")
    if not 0 < config.n_active_per_window <= config.n_jets:
        raise ValueError("n_active_per_window must be in [1, n_jets]")
    if config.n_excitation_windows <= 0:
        raise ValueError("n_excitation_windows must be positive")
    if config.n_reference_windows < 0:
        raise ValueError("n_reference_windows must be non-negative")
    if config.window_duration <= 0:
        raise ValueError("window_duration must be positive")
    if config.max_consecutive_on <= 0:
        raise ValueError("max_consecutive_on must be positive")
    if not config.equal_activation_count:
        raise ValueError("B3 first version requires equal_activation_count: true")
    _ = config.expected_count_per_jet


def _write_schedule_csv(config: ActuationConfig, matrix: list[list[int]]) -> None:
    with (config.output_dir / "actuation_schedule.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["window_id", "t_start", "t_end", *config.jet_names])
        for window_id, row in enumerate(matrix):
            start = window_id * config.window_duration
            end = start + config.window_duration
            writer.writerow([window_id, f"{start:.8g}", f"{end:.8g}", *row])


def _write_counts_csv(config: ActuationConfig, matrix: list[list[int]]) -> None:
    with (config.output_dir / "activation_counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["jet_id", "activation_count"])
        for jet_name, count in zip(config.jet_names, activation_counts(config, matrix)):
            writer.writerow([jet_name, count])


def _write_square_csv(
    path: Path,
    labels: list[str],
    matrix: list[list[int]] | list[list[float]],
    float_format: str | None = None,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["jet_id", *labels])
        for label, row in zip(labels, matrix):
            if float_format:
                writer.writerow([label, *(float_format.format(value) for value in row)])
            else:
                writer.writerow([label, *row])


def _write_mass_flow_csv(config: ActuationConfig, matrix: list[list[int]]) -> None:
    with (config.output_dir / "mass_flow.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["window_id", "t_start", "t_end", "active_jets", "total_mass_flow"])
        for window_id, row in enumerate(matrix):
            start = window_id * config.window_duration
            end = start + config.window_duration
            active = sum(row)
            writer.writerow(
                [
                    window_id,
                    f"{start:.8g}",
                    f"{end:.8g}",
                    active,
                    f"{active * config.command_amplitude:.8g}",
                ]
            )


def _write_heatmap_svg(config: ActuationConfig, matrix: list[list[int]]) -> None:
    cell = 10
    label_width = 54
    label_height = 24
    width = label_width + config.total_windows * cell + 12
    height = label_height + config.n_jets * cell + 28
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="8" y="16" font-family="Arial" font-size="12">24x80 actuation heatmap</text>',
    ]
    for jet_idx, jet_name in enumerate(config.jet_names):
        y = label_height + jet_idx * cell
        lines.append(
            f'<text x="6" y="{y + 8}" font-family="Arial" font-size="8" fill="#333">{jet_name}</text>'
        )
        for window_idx in range(config.total_windows):
            x = label_width + window_idx * cell
            fill = "#1f77b4" if matrix[window_idx][jet_idx] else "#f1f3f5"
            lines.append(
                f'<rect x="{x}" y="{y}" width="{cell - 1}" height="{cell - 1}" fill="{fill}"/>'
            )
    for window_idx in range(0, config.total_windows, 8):
        x = label_width + window_idx * cell
        lines.append(
            f'<text x="{x}" y="{height - 8}" font-family="Arial" font-size="8" fill="#555">{window_idx}</text>'
        )
    lines.append("</svg>")
    (config.output_dir / "actuation_heatmap.svg").write_text("\n".join(lines), encoding="utf-8")


def _config_summary(config: ActuationConfig) -> dict[str, Any]:
    return {
        "n_jets": config.n_jets,
        "n_active_per_window": config.n_active_per_window,
        "n_excitation_windows": config.n_excitation_windows,
        "n_reference_windows": config.n_reference_windows,
        "total_windows": config.total_windows,
        "expected_count_per_jet": config.expected_count_per_jet,
        "command_amplitude": config.command_amplitude,
        "window_duration": config.window_duration,
        "max_consecutive_on": config.max_consecutive_on,
        "equal_activation_count": config.equal_activation_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a sparse-jet control schedule.")
    parser.add_argument("--config", default="configs/maglev_sparse_jet_9w.yaml")
    parser.add_argument(
        "--output-dir",
        help="Override output.run_dir for actuation configs.",
    )
    args = parser.parse_args()

    with Path(args.config).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if "actuation" in data:
        config = ActuationConfig.from_mapping(data)
        if args.output_dir:
            config = ActuationConfig(
                n_jets=config.n_jets,
                n_active_per_window=config.n_active_per_window,
                n_excitation_windows=config.n_excitation_windows,
                n_reference_windows=config.n_reference_windows,
                command_amplitude=config.command_amplitude,
                window_duration=config.window_duration,
                max_consecutive_on=config.max_consecutive_on,
                equal_activation_count=config.equal_activation_count,
                random_seed=config.random_seed,
                output_dir=Path(args.output_dir),
                max_generation_attempts=config.max_generation_attempts,
            )
        matrix = generate_actuation_matrix(config)
        write_actuation_outputs(config, matrix)
        print(
            "generated actuation schedule: "
            f"windows={config.total_windows}, jets={config.n_jets}, output={config.output_dir}"
        )
    else:
        config = ExperimentConfig.from_mapping(data)
        schedule = generate_schedule(config)
        print(f"generated schedule: {schedule.name}, steps={len(schedule.steps)}")


if __name__ == "__main__":
    main()
