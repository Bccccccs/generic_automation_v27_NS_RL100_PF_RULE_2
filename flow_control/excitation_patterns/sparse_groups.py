"""Constrained sparse random groups for 24-jet screening."""

from __future__ import annotations

import itertools
import random

from .common import ActuationConfig, ScheduleTable, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    matrix = generate_actuation_matrix(config)
    errors = validate_sparse_matrix(config, matrix)
    table = table_from_switches(matrix, mass_flow_rate=config.mass_flow_rate)
    extra = {
        "n_excitation_windows": config.n_excitation_windows,
        "n_reference_windows": config.n_reference_windows,
        "n_active_per_window": config.n_active_per_window,
        "expected_count_per_jet": config.expected_count_per_jet,
        "random_seed": config.random_seed,
    }
    return table, extra, errors


def generate_actuation_matrix(config: ActuationConfig) -> list[list[int]]:
    """Generate a reproducible sparse matrix with excitation then reference windows."""

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


def validate_sparse_matrix(config: ActuationConfig, matrix: list[list[int]]) -> list[str]:
    errors: list[str] = []
    excitation = matrix[: config.n_excitation_windows]
    references = matrix[config.n_excitation_windows :]

    if len(matrix) != config.sparse_total_windows:
        errors.append(f"expected {config.sparse_total_windows} windows, got {len(matrix)}")
    if any(len(row) != config.n_jets for row in matrix):
        errors.append(f"all windows must have {config.n_jets} jet columns")

    for window_id, row in enumerate(excitation):
        active = sum(row)
        if active != config.n_active_per_window:
            errors.append(
                f"window {window_id} has {active} active jets, expected {config.n_active_per_window}"
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


def activation_counts(config: ActuationConfig, matrix: list[list[int]]) -> list[int]:
    excitation = matrix[: config.n_excitation_windows]
    return [sum(row[jet_idx] for row in excitation) for jet_idx in range(config.n_jets)]


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
        after = remaining[:]
        for jet_idx in combo:
            after[jet_idx] -= 1
        if any(count < 0 or count > windows_left_after_pick for count in after):
            continue
        scarcity_score = sum(remaining[jet_idx] for jet_idx in combo)
        balance_penalty = max(after) - min(after)
        candidates.append((scarcity_score * 10 - balance_penalty + rng.random(), combo))

    candidates.sort(reverse=True)
    return [combo for _, combo in candidates]


def _currently_blocked_jets(config: ActuationConfig, sequence: list[tuple[int, ...]]) -> set[int]:
    if config.max_consecutive_on <= 0 or len(sequence) < config.max_consecutive_on:
        return set()
    recent = sequence[-config.max_consecutive_on :]
    return {
        jet_idx
        for jet_idx in range(config.n_jets)
        if all(jet_idx in combo for combo in recent)
    }


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
        raise ValueError("sparse_random_groups requires equal_activation_count: true")
    _ = config.expected_count_per_jet
