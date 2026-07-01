from __future__ import annotations

from typing import Any

from adapter_base import Case
from residual_metrics import recent_residuals, residual_diagnostics, residual_log_slope
from rl_action_space import RLActionSpace
from rl_controller_utils import bucketize_range, collect_metric_values
from rl_runtime_registry import rl_runtime_parameter_snapshot
from runtime_value_utils import coerce_slope_bucket_value, mean, range_amplitude, safe_float


def current_parameter_snapshot(
    case: Case,
    observation_parameter_keys: tuple[str, ...],
    current_values: dict[str, Any],
) -> dict[str, Any]:
    merged_values: dict[str, Any] = {}
    for key in observation_parameter_keys:
        default_value = getattr(case, key, None)
        value = current_values.get(key, default_value)
        if value is None:
            continue
        merged_values[key] = value
    return rl_runtime_parameter_snapshot(merged_values)


def summarize_observation(
    *,
    case: Case,
    observation_window: int,
    drag_metric: str,
    total_metric: str,
    pressure_metric: str,
    observation_parameter_keys: tuple[str, ...],
    observation_residual_keys: dict[str, str],
    window: list[dict[str, Any]],
    current_values: dict[str, Any],
    residual_lookback: int,
    stagnation_abs_slope_threshold: float,
) -> dict[str, Any]:
    view = window[-observation_window:]
    drag_values = collect_metric_values(view, drag_metric)
    if not drag_values:
        raise ValueError("reinforcement learning controller requires primary observations")

    latest = view[-1]
    residual_columns = {
        public_name: safe_float(latest.get(source_name))
        for public_name, source_name in observation_residual_keys.items()
    }
    drag_mean = mean(drag_values)
    drag_amplitude = range_amplitude(drag_values)
    recent_max_residuals = recent_residuals(
        window,
        lookback=residual_lookback,
        field_name="max_residual",
    )
    residual_slope = residual_log_slope(window, lookback=residual_lookback)
    residual_flags = residual_diagnostics(
        window,
        lookback=residual_lookback,
        stagnation_abs_slope_threshold=stagnation_abs_slope_threshold,
    )

    observation: dict[str, Any] = {
        "iteration": int(latest.get("iteration", 0)),
        "num_cores": int(getattr(case, "num_cores", 1) or 1),
        "total_solver_cpu_time": safe_float(latest.get("total_solver_cpu_time")),
        "wall_time_since_start": safe_float(latest.get("wall_time_since_start")),
        "wall_time_per_chunk": safe_float(latest.get("wall_time_per_chunk")),
        "cpu_hours_so_far": safe_float(latest.get("cpu_hours_so_far")),
        "max_residual": safe_float(latest.get("max_residual")),
        "recent_max_residuals": recent_max_residuals,
        "residual_columns": residual_columns,
        "residual_log_slope": residual_slope,
        "residual_rebounded": residual_flags["rebounded"],
        "residual_oscillating": residual_flags["oscillating"],
        "residual_stagnating": residual_flags["stagnating"],
        "primary_metric_name": drag_metric,
        "primary_metric_recent_mean": drag_mean,
        "primary_metric_recent_amplitude": drag_amplitude,
        "primary_metric_latest": drag_values[-1],
        "drag_mean": drag_mean,
        "drag_latest": drag_values[-1],
        "drag_recent_mean": drag_mean,
        "drag_recent_amplitude": drag_amplitude,
        "current_parameters": current_parameter_snapshot(
            case,
            observation_parameter_keys,
            current_values,
        ),
        "convergence_residual_target": float(
            getattr(case, "convergence_residual", 1.0e-5)
        ),
        "max_iterations_target": int(getattr(case, "max_iterations", 2000)),
    }
    for public_name, value in residual_columns.items():
        observation[f"{public_name}_residual"] = value

    if total_metric:
        total_values = collect_metric_values(view, total_metric)
        if total_values:
            total_mean = mean(total_values)
            total_amplitude = range_amplitude(total_values)
            observation["total_force_name"] = total_metric
            observation["total_force_mean"] = total_mean
            observation["total_force_latest"] = total_values[-1]
            observation["total_force_recent_mean"] = total_mean
            observation["total_force_recent_amplitude"] = total_amplitude
            observation["total_mean"] = total_mean
            observation["total_latest"] = total_values[-1]
            observation["total_recent_mean"] = total_mean
            observation["total_recent_amplitude"] = total_amplitude

    if pressure_metric:
        pressure_values = collect_metric_values(view, pressure_metric)
        if pressure_values:
            pressure_mean = mean(pressure_values)
            observation["pressure_mean"] = pressure_mean
            observation["pressure_latest"] = pressure_values[-1]
            observation["pressure_recent_mean"] = pressure_mean
            observation["pressure_recent_amplitude"] = range_amplitude(pressure_values)

    turbulent_viscosity_limited_values = [
        int(value)
        for value in (safe_float(row.get("turbulent_viscosity_limited_cells")) for row in view)
        if value is not None
    ]
    if turbulent_viscosity_limited_values:
        observation["turbulent_viscosity_limited_cells"] = (
            turbulent_viscosity_limited_values[-1]
        )
        observation["turbulent_viscosity_limited_cells_recent_max"] = max(
            turbulent_viscosity_limited_values
        )
    return observation


def attach_decision_chunk_metrics(
    observation: dict[str, Any],
    previous_observation: dict[str, Any] | None,
) -> dict[str, Any]:
    updated = dict(observation)
    row_wall_time = safe_float(observation.get("wall_time_per_chunk"))
    updated["wall_time_per_row"] = row_wall_time

    previous = previous_observation or {}
    current_iteration = int(observation.get("iteration", 0))
    previous_iteration = int(previous.get("iteration", current_iteration))
    chunk_iterations = max(current_iteration - previous_iteration, 0)

    wall_time_since_start = safe_float(observation.get("wall_time_since_start"))
    previous_wall_time = safe_float(previous.get("wall_time_since_start"))
    chunk_wall_time = None
    if (
        wall_time_since_start is not None
        and previous_wall_time is not None
        and wall_time_since_start >= previous_wall_time
    ):
        chunk_wall_time = wall_time_since_start - previous_wall_time
    elif row_wall_time is not None:
        chunk_wall_time = row_wall_time

    total_solver_cpu_time = safe_float(observation.get("total_solver_cpu_time"))
    previous_cpu_time = safe_float(previous.get("total_solver_cpu_time"))
    chunk_cpu_seconds = None
    if (
        total_solver_cpu_time is not None
        and previous_cpu_time is not None
        and total_solver_cpu_time >= previous_cpu_time
    ):
        chunk_cpu_seconds = total_solver_cpu_time - previous_cpu_time
    elif chunk_wall_time is not None:
        num_cores = max(int(observation.get("num_cores", 1) or 1), 1)
        chunk_cpu_seconds = chunk_wall_time * float(num_cores)

    updated["wall_time_per_chunk"] = chunk_wall_time
    updated["chunk_iterations"] = chunk_iterations
    updated["chunk_cpu_seconds"] = chunk_cpu_seconds
    return updated


def build_state(
    *,
    case: Case,
    action_space: RLActionSpace,
    residual_stage_thresholds: tuple[tuple[str, float, float], ...],
    observation: dict[str, Any],
    constraints: dict[str, Any],
) -> str:
    max_residual = safe_float(observation.get("max_residual")) or 0.0
    slope = safe_float(observation.get("residual_log_slope"))
    current_parameters = observation.get("current_parameters", {})
    relax_factor = action_space.current_float_value(
        current_parameters,
        "pressure_relaxation_factor",
        float(getattr(case, "pressure_relaxation_factor", 0.5)),
    )
    relax_initial = action_space.current_float_value(
        current_parameters,
        "pressure_relaxation_initial_value",
        float(getattr(case, "pressure_relaxation_initial_value", 0.0)),
    )
    relax_end_iteration = action_space.current_int_value(
        current_parameters,
        "pressure_relaxation_end_iteration",
        int(getattr(case, "pressure_relaxation_end_iteration", 1)),
    )
    relax_initial_lo, relax_initial_hi = action_space.pressure_relaxation_initial_value_bounds(
        current_parameters,
        constraints,
    )
    relax_end_lo, relax_end_hi = action_space.pressure_relaxation_end_iteration_bounds(
        current_parameters,
        constraints,
    )
    pressure_amg_cycle = action_space.current_pressure_amg_cycle(current_parameters)
    velocity_amg_cycle = action_space.current_velocity_amg_cycle(current_parameters)

    return (
        f"stage:{residual_stage_label(max_residual, residual_stage_thresholds)}|"
        f"slope:{bucketize_range(coerce_slope_bucket_value(slope), -0.10, 0.10)}|"
        f"reb:{int(bool(observation.get('residual_rebounded')))}|"
        f"osc:{int(bool(observation.get('residual_oscillating')))}|"
        f"stag:{int(bool(observation.get('residual_stagnating')))}|"
        f"relax:{action_space.bucketize_numeric('pressure_relaxation_factor', relax_factor, constraints, 0.05, 0.95)}|"
        f"relax_init:{bucketize_range(relax_initial, relax_initial_lo, relax_initial_hi)}|"
        f"relax_end:{bucketize_range(float(relax_end_iteration), float(relax_end_lo), float(relax_end_hi))}|"
        f"pressure_amg:{pressure_amg_cycle}|"
        f"velocity_amg:{velocity_amg_cycle}"
    )


def residual_stage_label(
    residual: float,
    residual_stage_thresholds: tuple[tuple[str, float, float], ...],
) -> str:
    value = max(safe_float(residual) or 0.0, 0.0)
    for label, upper, lower in residual_stage_thresholds:
        if value <= upper and value > lower:
            return label
    return "le_1e-5"
