from __future__ import annotations

import math
from typing import Any

from generic_automation.rl.rl_action_space import RLActionSpace
from generic_automation.rl.rl_controller_utils import clip
from generic_automation.core.runtime_value_utils import rounded_or_none, safe_float


def chunk_wall_time_seconds(
    previous_observation: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    previous_value = safe_float(previous_observation.get("wall_time_since_start"))
    current_value = safe_float(current_observation.get("wall_time_since_start"))
    if (
        previous_value is not None
        and current_value is not None
        and current_value >= previous_value
    ):
        return current_value - previous_value

    current_chunk = safe_float(current_observation.get("wall_time_per_chunk"))
    if current_chunk is not None:
        return current_chunk
    return 0.0


def chunk_cpu_seconds(
    previous_observation: dict[str, Any],
    current_observation: dict[str, Any],
    *,
    chunk_wall_time_seconds_value: float,
    default_num_cores: int,
) -> float:
    previous_value = safe_float(previous_observation.get("total_solver_cpu_time"))
    current_value = safe_float(current_observation.get("total_solver_cpu_time"))
    if (
        previous_value is not None
        and current_value is not None
        and current_value >= previous_value
    ):
        return current_value - previous_value

    num_cores = max(int(current_observation.get("num_cores", default_num_cores) or 1), 1)
    return max(chunk_wall_time_seconds_value, 0.0) * float(num_cores)


def baseline_speed_for_stage(
    stage_label: str,
    raw_speed: float,
    *,
    configured_stage_baselines: dict[str, float],
    adaptive_stage_baselines: dict[str, float],
    adaptive_stage_counts: dict[str, int],
    baseline_min_samples: int,
) -> float | None:
    configured = configured_stage_baselines.get(stage_label)
    if configured is not None and configured > 0.0:
        return configured

    adaptive = adaptive_stage_baselines.get(stage_label)
    adaptive_count = adaptive_stage_counts.get(stage_label, 0)
    if adaptive is not None and adaptive > 0.0 and adaptive_count >= baseline_min_samples:
        return adaptive

    if math.isfinite(raw_speed) and raw_speed > 0.0:
        return raw_speed
    return None


def update_adaptive_stage_baseline(
    stage_label: str,
    raw_speed: float,
    *,
    configured_stage_baselines: dict[str, float],
    adaptive_stage_baselines: dict[str, float],
    adaptive_stage_counts: dict[str, int],
) -> None:
    if stage_label in configured_stage_baselines:
        return
    if not math.isfinite(raw_speed) or raw_speed <= 0.0:
        return

    count = adaptive_stage_counts.get(stage_label, 0)
    baseline = adaptive_stage_baselines.get(stage_label, raw_speed)
    updated = (baseline * count + raw_speed) / float(count + 1)
    adaptive_stage_baselines[stage_label] = updated
    adaptive_stage_counts[stage_label] = count + 1


def physics_drift_penalty(
    current_observation: dict[str, Any],
    current_residual: float,
    *,
    baseline_final_total: float | None,
    total_force_metric_name: str,
    default_max_iterations: int,
    default_convergence_residual: float,
    physics_drift_tolerance: float,
    physics_force_epsilon: float,
    physics_gate_start_fraction: float,
    physics_gate_full_fraction: float,
    physics_gate_disable_residual_ratio: float,
    physics_gate_full_residual_ratio: float,
    physics_gate_severe_drift_multiplier: float,
    physics_gate_severe_min: float,
) -> tuple[float, dict[str, Any]]:
    current_total_mean = safe_float(current_observation.get("total_force_recent_mean"))

    if baseline_final_total is None or current_total_mean is None:
        return 0.0, {
            "physics_reference_available": baseline_final_total is not None,
            "total_force_observation_available": current_total_mean is not None,
            "baseline_final_total": rounded_or_none(baseline_final_total),
            "total_force_metric_name": total_force_metric_name or None,
            "current_total_mean": rounded_or_none(current_total_mean),
            "relative_total_drift": None,
            "physics_gate": 0.0,
        }

    relative_total_drift = abs(current_total_mean - baseline_final_total) / max(
        abs(baseline_final_total),
        physics_force_epsilon,
    )
    physics_drift_score = min(
        1.0,
        relative_total_drift / max(physics_drift_tolerance, 1.0e-12),
    )

    current_iteration = int(current_observation.get("iteration", 0))
    max_iterations = max(
        int(current_observation.get("max_iterations_target", default_max_iterations) or 1),
        1,
    )
    convergence_residual = max(
        safe_float(
            current_observation.get(
                "convergence_residual_target",
                default_convergence_residual,
            )
        )
        or 1.0e-5,
        1.0e-30,
    )
    iter_fraction = current_iteration / float(max_iterations)
    residual_ratio = current_residual / convergence_residual

    severe_drift = relative_total_drift >= (
        physics_drift_tolerance * physics_gate_severe_drift_multiplier
    )

    if (
        iter_fraction >= physics_gate_full_fraction
        or residual_ratio <= physics_gate_full_residual_ratio
    ):
        physics_gate = 1.0
    else:
        iter_gate = clip(
            (iter_fraction - physics_gate_start_fraction)
            / max(
                physics_gate_full_fraction - physics_gate_start_fraction,
                1.0e-9,
            ),
            0.0,
            1.0,
        )
        residual_gate = 0.0
        if residual_ratio < physics_gate_disable_residual_ratio:
            residual_gate = clip(
                (physics_gate_disable_residual_ratio - residual_ratio)
                / max(
                    physics_gate_disable_residual_ratio
                    - physics_gate_full_residual_ratio,
                    1.0e-9,
                ),
                0.0,
                1.0,
            )
        physics_gate = max(iter_gate, residual_gate)
        if severe_drift:
            physics_gate = max(physics_gate, physics_gate_severe_min)

    penalty = physics_gate * physics_drift_score
    return penalty, {
        "physics_reference_available": True,
        "total_force_observation_available": True,
        "baseline_final_total": round(baseline_final_total, 6),
        "baseline_final_primary_metric": round(baseline_final_total, 6),
        "total_force_metric_name": total_force_metric_name or None,
        "current_total_mean": round(current_total_mean, 6),
        "relative_total_drift": round(relative_total_drift, 6),
        "physics_gate": round(physics_gate, 6),
        "physics_gate_start_fraction": round(physics_gate_start_fraction, 6),
        "physics_gate_full_fraction": round(physics_gate_full_fraction, 6),
        "physics_gate_disable_residual_ratio": round(
            physics_gate_disable_residual_ratio,
            6,
        ),
        "physics_gate_full_residual_ratio": round(
            physics_gate_full_residual_ratio,
            6,
        ),
        "physics_gate_severe_drift": bool(severe_drift),
    }


def speed_score(
    raw_speed: float,
    baseline_speed: float | None,
    clip_range: float,
) -> float:
    if baseline_speed is None or baseline_speed <= 0.0:
        return 0.0
    relative_speed = raw_speed / max(baseline_speed, 1.0e-30) - 1.0
    return clip(relative_speed / clip_range, -1.0, 1.0)


def stagnation_penalty(decay_per_iter: float, threshold: float) -> float:
    return 1.0 if decay_per_iter < threshold else 0.0


def divergence_penalty(current_residual: float, previous_residual: float) -> float:
    if not math.isfinite(current_residual):
        return 1.0
    if current_residual > 10.0 * previous_residual:
        return 1.0
    if current_residual > 3.0 * previous_residual:
        return 0.5
    return 0.0


def oscillation_penalty(recent_residuals: list[float]) -> float:
    increase_count = sum(
        1
        for idx in range(1, len(recent_residuals))
        if recent_residuals[idx] > recent_residuals[idx - 1]
    )
    increase_ratio = increase_count / max(len(recent_residuals) - 1, 1)
    return min(1.0, increase_ratio / 0.5)


def action_change_penalty(
    previous_observation: dict[str, Any],
    current_observation: dict[str, Any],
    constraints: dict[str, Any],
    *,
    allowed_parameter_names: set[str],
    action_space: RLActionSpace,
) -> float:
    previous_parameters = previous_observation.get("current_parameters", {})
    current_parameters = current_observation.get("current_parameters", {})
    if not isinstance(previous_parameters, dict) or not isinstance(current_parameters, dict):
        return 0.0

    scores: list[float] = []
    for key in sorted(allowed_parameter_names):
        previous_value = previous_parameters.get(key)
        current_value = current_parameters.get(key)
        if previous_value is None or current_value is None:
            continue

        if key in {"pressure_amg_cycle", "velocity_amg_cycle"}:
            scores.append(1.0 if int(previous_value) != int(current_value) else 0.0)
            continue

        numeric_previous = safe_float(previous_value)
        numeric_current = safe_float(current_value)
        if numeric_previous is None or numeric_current is None:
            continue

        if key == "pressure_relaxation_factor":
            lo, hi = action_space.float_bounds(constraints, key, 0.05, 0.95)
            denominator = max(hi - lo, 1.0e-12)
        elif key == "pressure_relaxation_initial_value":
            lo, hi = action_space.float_bounds(constraints, key, 0.0, 0.50)
            denominator = max(hi - lo, 1.0e-12)
        elif key.endswith("_iteration"):
            lo, hi = action_space.int_bounds(constraints, key, 1, 200)
            denominator = max(float(hi - lo), 1.0)
        else:
            lo, hi = action_space.float_bounds(constraints, key, 0.01, 0.95)
            denominator = max(hi - lo, 1.0e-12)
        scores.append(min(1.0, abs(numeric_current - numeric_previous) / denominator))

    if not scores:
        return 0.0
    return min(1.0, sum(scores))


def convergence_bonus(
    current_observation: dict[str, Any],
    current_residual: float,
    *,
    default_convergence_residual: float,
    stable_chunks: int,
) -> float:
    convergence_target = max(
        safe_float(
            current_observation.get(
                "convergence_residual_target",
                default_convergence_residual,
            )
        )
        or 1.0e-5,
        1.0e-30,
    )
    recent_residuals = [
        value
        for value in current_observation.get("recent_max_residuals", [])
        if safe_float(value) is not None
    ]
    recent_k = recent_residuals[-stable_chunks:]
    stable_for_k_chunks = (
        len(recent_k) >= stable_chunks
        and all(value <= convergence_target for value in recent_k)
    )
    if current_residual <= convergence_target and stable_for_k_chunks:
        return 1.0
    return 0.0
