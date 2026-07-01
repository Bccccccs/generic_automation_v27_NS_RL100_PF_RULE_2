from __future__ import annotations

from typing import Any

from adapter_base import Case
from residual_metrics import (
    derive_relaxation_scheme,
    residual_diagnostics,
    residual_log_slope,
    residual_log_slope_for_key,
)
from runtime_metadata import PROTOCOL_VERSION
from runtime_value_utils import safe_float, safe_int

STARCCM_LOG_NAME = "logs/starccm.log"
OBS_STREAM_NAME = "rl/rl_observation_stream.jsonl"
ACTION_LOG_NAME = "rl/rl_action_events.jsonl"
SUMMARY_NAME = "experiment_summary.json"
PROFILING_TIMESERIES_NAME = "profiling/profiling_timeseries.jsonl"
PROFILING_TIMESERIES_CSV_NAME = "profiling/profiling_timeseries.csv"
PROFILING_ACTIONS_NAME = "profiling/profiling_actions.jsonl"
PROFILING_SUMMARY_NAME = "profiling/profiling_summary.json"
SOLVER_PROFILING_SUMMARY_NAME = "profiling/solver_profiling_summary.json"

_RESIDUAL_SLOPE_KEYS = (
    "continuity_residual",
    "x_momentum_residual",
    "y_momentum_residual",
    "z_momentum_residual",
    "tke_residual",
    "sdr_residual",
    "energy_residual",
)

_PROFILING_CAPTURED_OVERALL_RUNTIME_FIELDS = [
    "time_step",
    "iteration",
    "iteration_delta",
    "wall_time_since_start_s",
    "wall_time_per_chunk_s",
    "wall_time_per_iteration_s",
    "total_solver_cpu_time_s",
    "cpu_time_per_chunk_s",
    "cpu_time_per_iteration_s",
    "cpu_hours_so_far",
]

_PROFILING_CAPTURED_EQUATION_LEVEL_FIELDS = [
    "max_residual",
    "continuity_residual",
    "x_momentum_residual",
    "y_momentum_residual",
    "z_momentum_residual",
    "tke_residual",
    "sdr_residual",
    "energy_residual",
    "pressure_final_residual",
    "x_momentum_final_residual",
    "y_momentum_final_residual",
    "z_momentum_final_residual",
    "tke_final_residual",
    "sdr_final_residual",
    "energy_final_residual",
    "continuity_residual_slope_10",
    "continuity_residual_slope_50",
    "x_momentum_residual_slope_10",
    "x_momentum_residual_slope_50",
    "y_momentum_residual_slope_10",
    "y_momentum_residual_slope_50",
    "z_momentum_residual_slope_10",
    "z_momentum_residual_slope_50",
    "tke_residual_slope_10",
    "tke_residual_slope_50",
    "sdr_residual_slope_10",
    "sdr_residual_slope_50",
    "energy_residual_slope_10",
    "energy_residual_slope_50",
    "residual_slope_10",
    "residual_slope_50",
    "residual_rebounded",
    "residual_oscillating",
    "residual_stagnating",
]

_PROFILING_CAPTURED_ACTION_FIELDS = [
    "action_id",
    "current_parameters",
    "controller_proposed_changes",
    "applied_changes",
    "apply_success",
    "blocked_reason",
    "ack_status",
    "rl_intervention_enabled",
    "reward",
    "epsilon",
]

_PROFILING_CAPTURED_DERIVED_FIELDS = [
    "pressure_relaxation_scheme",
    "velocity_relaxation_scheme",
    "pressure_final_residual",
    "x_momentum_final_residual",
    "y_momentum_final_residual",
    "z_momentum_final_residual",
    "tke_final_residual",
    "sdr_final_residual",
    "energy_final_residual",
]

_PROFILING_PENDING_PHASE2_FIELDS = [
    "solver_type",
    "starccm_version",
    "mesh_cells",
    "equation_time",
    "linear_solver_iterations",
    "amg_cycles",
    "hit_max_cycles",
    "current_tolerance",
    "mass_imbalance",
    "cfl",
    "io_time",
]

PROFILING_TIMESERIES_FIELDS = (
    "case_id",
    "case_name",
    "adapter",
    "controller",
    "simulation_type",
    "turbulence_model",
    "fluid",
    "energy_equation",
    "physics_models",
    "solver_type",
    "mesh_cells",
    "starccm_version",
    "time_step",
    "iteration",
    "iteration_delta",
    "wall_time_since_start_s",
    "wall_time_per_chunk_s",
    "wall_time_per_iteration_s",
    "total_solver_cpu_time_s",
    "cpu_time_per_chunk_s",
    "cpu_time_per_iteration_s",
    "cpu_hours_so_far",
    "max_residual",
    "continuity_residual",
    "x_momentum_residual",
    "y_momentum_residual",
    "z_momentum_residual",
    "tke_residual",
    "sdr_residual",
    "energy_residual",
    "pressure_final_residual",
    "x_momentum_final_residual",
    "y_momentum_final_residual",
    "z_momentum_final_residual",
    "tke_final_residual",
    "sdr_final_residual",
    "energy_final_residual",
    "pressure_relaxation_scheme",
    "velocity_relaxation_scheme",
    "continuity_residual_slope_10",
    "continuity_residual_slope_50",
    "x_momentum_residual_slope_10",
    "x_momentum_residual_slope_50",
    "y_momentum_residual_slope_10",
    "y_momentum_residual_slope_50",
    "z_momentum_residual_slope_10",
    "z_momentum_residual_slope_50",
    "tke_residual_slope_10",
    "tke_residual_slope_50",
    "sdr_residual_slope_10",
    "sdr_residual_slope_50",
    "energy_residual_slope_10",
    "energy_residual_slope_50",
    "residual_slope_10",
    "residual_slope_50",
    "residual_rebounded_10",
    "residual_oscillating_10",
    "residual_stagnating_10",
    "residual_rebounded_50",
    "residual_oscillating_50",
    "residual_stagnating_50",
    "drag_metric_name",
    "drag_metric_source",
    "drag_latest",
    "total_force_name",
    "total_force_source",
    "total_force_latest",
    "pressure_metric_name",
    "pressure_metric_source",
    "pressure_latest",
    "turbulent_viscosity_limited_cells",
    "current_parameters",
)


def _case_text(case: Case, field: str, default: str = "") -> str:
    return str(getattr(case, field, default) or default)


def _case_bool(case: Case, field: str, default: bool = False) -> bool:
    return bool(getattr(case, field, default))


def _case_float(case: Case, field: str) -> float | None:
    return safe_float(getattr(case, field, None))


def _case_identity(case: Case) -> dict[str, Any]:
    return {
        "case_id": _case_text(case, "case_name"),
        "case_name": _case_text(case, "case_name"),
    }


def _case_runtime_fields(case: Case, controller_mode: str) -> dict[str, Any]:
    return {
        **_case_identity(case),
        "adapter": "starccm",
        "controller": controller_mode,
        "simulation_type": _case_text(case, "simulation_type"),
        "turbulence_model": _case_text(case, "turbulence_model"),
        "fluid": _case_text(case, "fluid"),
        "energy_equation": _case_bool(case, "energy_equation"),
        "physics_models": physics_models(case),
    }


def _solver_fields(solver_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "solver_type": solver_meta.get("solver_type"),
        "mesh_cells": solver_meta.get("mesh_cells"),
        "starccm_version": solver_meta.get("starccm_version"),
    }


def _report_keys(case: Case) -> tuple[str, str, str]:
    return (
        _case_text(case, "drag_report_name"),
        _case_text(case, "total_report_name"),
        _case_text(case, "train_surface_pressure_report_name"),
    )


def _metric_value(row: dict[str, Any], key: str) -> float | None:
    if not key:
        return None
    return safe_float(row.get(key))


def _report_snapshot(
    row: dict[str, Any],
    drag_key: str,
    total_key: str,
    pressure_key: str,
) -> dict[str, Any]:
    return {
        "drag_metric_name": drag_key or None,
        "drag_metric_source": row.get("drag_metric_source"),
        "drag_latest": _metric_value(row, drag_key),
        "total_force_name": total_key or None,
        "total_force_source": row.get("total_force_source"),
        "total_force_latest": _metric_value(row, total_key),
        "pressure_metric_name": pressure_key or None,
        "pressure_metric_source": row.get("pressure_metric_source"),
        "pressure_latest": _metric_value(row, pressure_key),
    }


def _relaxation_scheme_fields(case: Case) -> dict[str, Any]:
    return {
        "pressure_relaxation_scheme": derive_relaxation_scheme(
            getattr(case, "pressure_relaxation_initial_value", None),
            getattr(case, "pressure_relaxation_start_iteration", None),
            getattr(case, "pressure_relaxation_end_iteration", None),
            getattr(case, "pressure_relaxation_factor", None),
        ),
        "velocity_relaxation_scheme": derive_relaxation_scheme(
            getattr(case, "velocity_relaxation_initial_value", None),
            getattr(case, "velocity_relaxation_start_iteration", None),
            getattr(case, "velocity_relaxation_end_iteration", None),
        ),
    }


def _final_residual_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pressure_final_residual": safe_float(row.get("continuity_residual")),
        "x_momentum_final_residual": safe_float(row.get("x_momentum_residual")),
        "y_momentum_final_residual": safe_float(row.get("y_momentum_residual")),
        "z_momentum_final_residual": safe_float(row.get("z_momentum_residual")),
        "tke_final_residual": safe_float(row.get("tke_residual")),
        "sdr_final_residual": safe_float(row.get("sdr_residual")),
        "energy_final_residual": safe_float(row.get("energy_residual")),
    }


def _residual_slope_fields(window_slice: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in _RESIDUAL_SLOPE_KEYS:
        fields[f"{key}_slope_10"] = residual_log_slope_for_key(window_slice, key, lookback=10)
        fields[f"{key}_slope_50"] = residual_log_slope_for_key(window_slice, key, lookback=50)
    return fields


def build_observation_record(
    row: dict[str, Any],
    diagnostics: dict[str, Any],
    last_action: str | None,
    current_params: dict[str, Any],
) -> dict[str, Any]:
    record = {key: value for key, value in row.items() if not str(key).startswith("_")}
    record.update(diagnostics)
    record["last_action"] = last_action
    record["current_parameters"] = current_params
    return record


def physics_models(case: Case) -> list[str]:
    models: list[str] = []
    turbulence_model = str(getattr(case, "turbulence_model", "") or "").strip()
    if turbulence_model:
        models.append(turbulence_model)
    simulation_type = str(getattr(case, "simulation_type", "") or "").strip()
    if simulation_type:
        models.append(simulation_type)
    if bool(getattr(case, "energy_equation", False)):
        models.append("energy")
    return models


def build_profiling_timeseries_record(
    case: Case,
    controller_mode: str,
    row: dict[str, Any],
    row_index: int,
    window: list[dict[str, Any]],
    current_params: dict[str, Any],
    solver_meta: dict[str, Any],
) -> dict[str, Any]:
    window_slice = window[: row_index + 1]
    diag10 = residual_diagnostics(window_slice, lookback=10)
    diag50 = residual_diagnostics(window_slice, lookback=50)
    drag_key, total_key, pressure_key = _report_keys(case)

    return {
        **_case_runtime_fields(case, controller_mode),
        **_solver_fields(solver_meta),
        "time_step": _case_float(case, "time_step"),
        "iteration": int(row.get("iteration", 0)),
        "iteration_delta": safe_int(row.get("iteration_delta")),
        "wall_time_since_start_s": safe_float(row.get("wall_time_since_start")),
        "wall_time_per_chunk_s": safe_float(row.get("wall_time_per_chunk")),
        "wall_time_per_iteration_s": safe_float(row.get("wall_time_per_iteration")),
        "total_solver_cpu_time_s": safe_float(row.get("total_solver_cpu_time")),
        "cpu_time_per_chunk_s": safe_float(row.get("cpu_time_per_chunk")),
        "cpu_time_per_iteration_s": safe_float(row.get("cpu_time_per_iteration")),
        "cpu_hours_so_far": safe_float(row.get("cpu_hours_so_far")),
        "max_residual": safe_float(row.get("max_residual")),
        "continuity_residual": safe_float(row.get("continuity_residual")),
        "x_momentum_residual": safe_float(row.get("x_momentum_residual")),
        "y_momentum_residual": safe_float(row.get("y_momentum_residual")),
        "z_momentum_residual": safe_float(row.get("z_momentum_residual")),
        "tke_residual": safe_float(row.get("tke_residual")),
        "sdr_residual": safe_float(row.get("sdr_residual")),
        "energy_residual": safe_float(row.get("energy_residual")),
        **_final_residual_fields(row),
        **_relaxation_scheme_fields(case),
        **_residual_slope_fields(window_slice),
        "residual_slope_10": residual_log_slope(window_slice, lookback=10),
        "residual_slope_50": residual_log_slope(window_slice, lookback=50),
        "residual_rebounded_10": diag10["rebounded"],
        "residual_oscillating_10": diag10["oscillating"],
        "residual_stagnating_10": diag10["stagnating"],
        "residual_rebounded_50": diag50["rebounded"],
        "residual_oscillating_50": diag50["oscillating"],
        "residual_stagnating_50": diag50["stagnating"],
        **_report_snapshot(row, drag_key, total_key, pressure_key),
        "turbulent_viscosity_limited_cells": safe_int(row.get("turbulent_viscosity_limited_cells")),
        "current_parameters": current_params,
    }


def build_action_event_record(
    case: Case,
    controller_mode: str,
    run_id: str,
    current_iter: int,
    params_changed: bool,
    meta: dict[str, Any],
    update: dict[str, Any],
    solver_meta: dict[str, Any],
    intervention_enabled: bool,
    pending_action_id: str | None,
) -> dict[str, Any]:
    action = meta.get("action", "")
    observation = meta.get("observation", {})
    if not isinstance(observation, dict):
        observation = {}
    return {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id or None,
        **_case_runtime_fields(case, controller_mode),
        "rl_intervention_enabled": intervention_enabled,
        "iteration": current_iter,
        "action_id": update.get("action_id"),
        "action": action,
        "decision_mode": meta.get("decision_mode"),
        "state": meta.get("state"),
        "controller_proposed_changes": update.get("controller_proposed_changes", {}),
        "applied_changes": update.get("applied_changes", {}),
        "apply_success": update.get("apply_success", params_changed),
        "blocked_reason": update.get("blocked_reason"),
        "ack_status": update.get("ack_status"),
        "pending_action_id": pending_action_id,
        "parameters_before": meta.get("current_parameters", {}),
        "parameters_after": (
            meta.get("proposed_parameters", {})
            if params_changed
            else meta.get("current_parameters", {})
        ),
        "reward": meta.get("reward", {}).get("value") if meta.get("reward") else None,
        "epsilon": meta.get("epsilon"),
        "instability_guard_active": bool(
            (meta.get("instability_guard") or {}).get("active")
        ),
        "safety_override_active": bool(
            (meta.get("safety_override") or {}).get("active")
        ),
        "wall_time_since_start_s": safe_float(observation.get("wall_time_since_start")),
        "wall_time_per_chunk_s": safe_float(observation.get("wall_time_per_chunk")),
        "chunk_iterations": safe_int(observation.get("chunk_iterations")),
        "chunk_cpu_seconds": safe_float(observation.get("chunk_cpu_seconds")),
        "total_solver_cpu_time_s": safe_float(observation.get("total_solver_cpu_time")),
        "max_residual": safe_float(observation.get("max_residual")),
        "residual_log_slope": safe_float(observation.get("residual_log_slope")),
        "residual_rebounded": bool(observation.get("residual_rebounded")),
        "residual_oscillating": bool(observation.get("residual_oscillating")),
        "residual_stagnating": bool(observation.get("residual_stagnating")),
        "drag_latest": safe_float(observation.get("drag_latest")),
        "total_force_latest": safe_float(observation.get("total_force_latest")),
        "pressure_latest": safe_float(observation.get("pressure_latest")),
        "turbulent_viscosity_limited_cells": safe_int(
            observation.get("turbulent_viscosity_limited_cells")
        ),
        "drag_metric_source": observation.get("drag_metric_source"),
        "total_force_source": observation.get("total_force_source"),
        "pressure_metric_source": observation.get("pressure_metric_source"),
        "time_step": _case_float(case, "time_step"),
        **_solver_fields(solver_meta),
    }


def build_experiment_summary(
    case: Case,
    controller_mode: str,
    run_id: str,
    window: list[dict[str, Any]],
    attempt_count: int,
    trigger_count: int,
    blocked_action_count: int,
    divergence_events: int,
    intervention_enabled: bool,
) -> dict[str, Any]:
    convergence_target = float(getattr(case, "convergence_residual", 1.0e-5) or 1.0e-5)
    drag_key, total_key, pressure_key = _report_keys(case)

    last_row = window[-1] if window else {}
    total_iterations = int(last_row.get("iteration", 0))
    final_max_residual = safe_float(last_row.get("max_residual"))

    iterations_to_threshold: int | None = None
    for row in window:
        max_residual = safe_float(row.get("max_residual"))
        if max_residual is not None and max_residual <= convergence_target:
            iterations_to_threshold = int(row.get("iteration", 0))
            break

    return {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id or None,
        "run_mode": _case_text(case, "run_mode", "full_run"),
        "input_sim": _case_text(case, "input_sim"),
        "mesh_cache_key": _case_text(case, "mesh_cache_key"),
        "case_name": _case_text(case, "case_name"),
        "controller": controller_mode,
        "num_cores": int(getattr(case, "num_cores", 1) or 1),
        "total_wall_time_s": safe_float(last_row.get("wall_time_since_start")),
        "cpu_hours": safe_float(last_row.get("cpu_hours_so_far")),
        "total_iterations": total_iterations,
        "convergence_residual_target": convergence_target,
        "iterations_to_residual_threshold": iterations_to_threshold,
        "final_max_residual": final_max_residual,
        "final_drag": safe_float(last_row.get(drag_key)) if drag_key else None,
        "final_total_force": safe_float(last_row.get(total_key)) if total_key else None,
        "final_pressure": safe_float(last_row.get(pressure_key)) if pressure_key else None,
        "num_rl_trigger_points": attempt_count,
        "num_actions": trigger_count,
        "num_blocked_actions": blocked_action_count,
        "num_failed_actions": blocked_action_count,
        "rl_intervention_enabled": intervention_enabled,
        "rl_operation_mode": (
            "intervention_enabled" if intervention_enabled else "observe_only"
        ),
        "divergence_events": divergence_events,
        "success": (
            final_max_residual is not None and final_max_residual <= convergence_target
        ),
    }


def build_profiling_summary(
    case: Case,
    base_summary: dict[str, Any],
    last_row: dict[str, Any],
    solver_meta: dict[str, Any],
    profiling_outputs: dict[str, str],
) -> dict[str, Any]:
    profiling_summary = dict(base_summary)
    profiling_summary.update(
        {
            **_case_identity(case),
            "adapter": "starccm",
            "simulation_type": _case_text(case, "simulation_type"),
            "turbulence_model": _case_text(case, "turbulence_model"),
            "fluid": _case_text(case, "fluid"),
            "energy_equation": _case_bool(case, "energy_equation"),
            "physics_models": physics_models(case),
            **_solver_fields(solver_meta),
            "time_step": _case_float(case, "time_step"),
            "profiling_phase": "phase1_existing_signals_plus_macro_metadata",
            "profiling_outputs": profiling_outputs,
            "captured_overall_runtime_fields": _PROFILING_CAPTURED_OVERALL_RUNTIME_FIELDS,
            "captured_equation_level_fields": _PROFILING_CAPTURED_EQUATION_LEVEL_FIELDS,
            "captured_action_fields": _PROFILING_CAPTURED_ACTION_FIELDS,
            "captured_derived_fields": _PROFILING_CAPTURED_DERIVED_FIELDS,
            "pending_phase2_fields": _PROFILING_PENDING_PHASE2_FIELDS,
            "final_wall_time_per_iteration_s": safe_float(
                last_row.get("wall_time_per_iteration")
            ),
            "final_cpu_time_per_iteration_s": safe_float(
                last_row.get("cpu_time_per_iteration")
            ),
            "final_total_solver_cpu_time_s": safe_float(
                last_row.get("total_solver_cpu_time")
            ),
            "final_continuity_residual": safe_float(last_row.get("continuity_residual")),
            "final_x_momentum_residual": safe_float(last_row.get("x_momentum_residual")),
            "final_y_momentum_residual": safe_float(last_row.get("y_momentum_residual")),
            "final_z_momentum_residual": safe_float(last_row.get("z_momentum_residual")),
            "final_tke_residual": safe_float(last_row.get("tke_residual")),
            "final_sdr_residual": safe_float(last_row.get("sdr_residual")),
            "final_energy_residual": safe_float(last_row.get("energy_residual")),
            "final_pressure_residual": safe_float(last_row.get("continuity_residual")),
            **_relaxation_scheme_fields(case),
        }
    )
    return profiling_summary
