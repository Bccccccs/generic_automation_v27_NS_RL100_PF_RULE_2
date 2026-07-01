from __future__ import annotations

from pathlib import Path
from typing import Any

from adapter_base import Case
from runtime_metadata import (
    ACTION_ACK_LOG_FILE,
    PARAM_ACK_FILE,
    PARAM_UPDATE_FILE,
    compute_mesh_cache_key,
    default_mesh_ready_filename,
    default_result_sim_filename,
    default_solver_init_filename,
    resolve_sims_dir,
)

_MACRO_TEMPLATE_PATH = Path(__file__).with_name("starccm_macro_template.java")


def build_macro(
    case: Case,
    case_dir: Path,
    check_interval: int,
    run_context: dict[str, Any] | None = None,
) -> str:
    output_dir = str(case_dir.resolve()).replace("\\", "/")
    run_context = run_context or {}
    run_mode = str(getattr(case, "run_mode", "full_run") or "full_run").strip().lower()
    mesh_cache_key = str(
        run_context.get("mesh_cache_key")
        or getattr(case, "mesh_cache_key", "")
        or compute_mesh_cache_key(case)
    )
    sims_dir = resolve_sims_dir(case_dir)
    sims_dir.mkdir(parents=True, exist_ok=True)
    mesh_ready_path = sims_dir / default_mesh_ready_filename(case, mesh_cache_key)
    result_sim_path = sims_dir / default_result_sim_filename(case)
    solver_init_path = sims_dir / default_solver_init_filename(case)
    subs = {
        "{{CASE_NAME}}": _java_str(case.case_name),
        "{{OUTPUT_DIR}}": output_dir,
        "{{RUN_MODE}}": _java_str(run_mode),
        "{{RUN_ID}}": _java_str(str(run_context.get("run_id", "") or "")),
        "{{MESH_CACHE_KEY}}": _java_str(mesh_cache_key),
        "{{SIM_DIR}}": str(sims_dir.resolve()).replace("\\", "/"),
        "{{MESH_READY_SIM_PATH}}": str(mesh_ready_path.resolve()).replace("\\", "/"),
        "{{RESULT_SIM_PATH}}": str(result_sim_path.resolve()).replace("\\", "/"),
        "{{SOLVER_INIT_SIM_PATH}}": str(solver_init_path.resolve()).replace("\\", "/"),
        "{{CHECKPOINT_INTERVAL}}": str(max(int(getattr(case, "checkpoint_interval", 0) or 0), 0)),
        "{{PARAM_ACK_FILE}}": str((case_dir / PARAM_ACK_FILE).resolve()).replace("\\", "/"),
        "{{ACTION_ACK_LOG_FILE}}": str((case_dir / ACTION_ACK_LOG_FILE).resolve()).replace("\\", "/"),
        "{{REGION_NAME}}": _java_str(case.region_name),
        "{{INLET_BOUNDARY}}": _java_str(case.inlet_boundary),
        "{{OUTLET_BOUNDARY}}": _java_str(case.outlet_boundary),
        "{{WALL_BOUNDARY}}": _java_str(case.wall_boundary),
        "{{GROUND_BOUNDARY}}": _java_str(case.ground_boundary),
        "{{SYMMETRY_BOUNDARY}}": _java_str(case.symmetry_boundary),
        "{{INLET_VELOCITY}}": str(case.inlet_velocity),
        "{{INLET_TEMPERATURE}}": str(case.inlet_temperature),
        "{{OUTLET_PRESSURE}}": str(case.outlet_pressure),
        "{{YAW_ANGLE}}": str(case.yaw_angle),
        "{{REFERENCE_AREA}}": str(case.reference_area),
        "{{REFERENCE_LENGTH}}": str(case.reference_length),
        "{{BASE_MESH_SIZE}}": str(case.base_mesh_size),
        "{{SURFACE_MESH_SIZE}}": str(case.surface_mesh_size),
        "{{MIN_SURFACE_SIZE}}": str(case.min_surface_size),
        "{{SURFACE_GROWTH_RATE}}": str(case.surface_growth_rate),
        "{{NUM_PRISM_LAYERS}}": str(case.num_prism_layers),
        "{{PRISM_LAYER_THICKNESS}}": str(case.prism_layer_thickness),
        "{{PRISM_LAYER_STRETCHING}}": str(case.prism_layer_stretching),
        "{{PRISM_WALL_THICKNESS}}": str(case.prism_wall_thickness),
        "{{TRAIN_TARGET_SIZE}}": str(case.train_target_size),
        "{{TRAIN_MIN_SIZE}}": str(case.train_min_size),
        "{{TRAIN_PRISM_THICKNESS}}": str(case.train_prism_thickness),
        "{{TRAIN_PRISM_LAYERS}}": str(case.train_prism_layers),
        "{{ZONE1_MESH_SIZE}}": str(case.zone1_mesh_size),
        "{{ZONE2_MESH_SIZE}}": str(case.zone2_mesh_size),
        "{{INLET_TURBULENCE_INTENSITY}}": str(case.inlet_turbulence_intensity),
        "{{INLET_TURBULENT_LENGTH_SCALE}}": str(case.inlet_turbulent_length_scale),
        "{{MAX_ITERATIONS}}": str(case.max_iterations),
        "{{PRESSURE_RELAXATION_FACTOR}}": str(case.pressure_relaxation_factor),
        "{{PRESSURE_RELAXATION_INITIAL_VALUE}}": str(case.pressure_relaxation_initial_value),
        "{{PRESSURE_RELAXATION_START_ITERATION}}": str(case.pressure_relaxation_start_iteration),
        "{{PRESSURE_RELAXATION_END_ITERATION}}": str(case.pressure_relaxation_end_iteration),
        "{{VELOCITY_RELAXATION_INITIAL_VALUE}}": str(case.velocity_relaxation_initial_value),
        "{{VELOCITY_RELAXATION_START_ITERATION}}": str(case.velocity_relaxation_start_iteration),
        "{{VELOCITY_RELAXATION_END_ITERATION}}": str(case.velocity_relaxation_end_iteration),
        "{{PRESSURE_AMG_CYCLE}}": str(case.pressure_amg_cycle),
        "{{VELOCITY_AMG_CYCLE}}": str(case.velocity_amg_cycle),
        "{{AMG_CYCLE}}": str(case.amg_cycle),
        "{{AMG_SOLVER}}": str(case.amg_solver),
        "{{PRESSURE_AMG_MAX_CYCLES}}": str(case.pressure_amg_max_cycles),
        "{{PRESSURE_AMG_CONVERGE_TOL}}": str(case.pressure_amg_converge_tol),
        "{{PRESSURE_AMG_EPSILON}}": str(case.pressure_amg_epsilon),
        "{{PRESSURE_AMG_SMOOTHER}}": _java_str(case.pressure_amg_smoother),
        "{{PRESSURE_AMG_ACCELERATION}}": _java_str(case.pressure_amg_acceleration),
        "{{PRESSURE_AMG_PRE_SWEEPS}}": str(case.pressure_amg_pre_sweeps),
        "{{PRESSURE_AMG_POST_SWEEPS}}": str(case.pressure_amg_post_sweeps),
        "{{PRESSURE_AMG_MAX_LEVELS}}": str(case.pressure_amg_max_levels),
        "{{TIME_STEP}}": str(case.time_step),
        "{{NUM_TIME_STEPS}}": str(case.num_time_steps),
        "{{SIMULATION_TYPE}}": case.simulation_type,
        "{{TURBULENCE_MODEL}}": case.turbulence_model,
        "{{ENERGY_EQUATION}}": "true" if case.energy_equation else "false",
        "{{DOMAIN_CORNER1}}": "{" + ", ".join(str(x) for x in case.domain_corner1) + "}",
        "{{DOMAIN_CORNER2}}": "{" + ", ".join(str(x) for x in case.domain_corner2) + "}",
        "{{INITIAL_VELOCITY}}": str(case.initial_velocity),
        "{{MONITOR_START_ITERATION}}": str(case.monitor_start_iteration),
        "{{MONITOR_UPDATE_FREQUENCY}}": str(case.monitor_update_frequency),
        "{{WALL_TREATMENT}}": case.wall_treatment,
        "{{CAD_SHARP_ANGLE}}": str(case.cad_sharp_angle),
        "{{LOG_FREQUENCY}}": str(case.log_frequency),
        "{{DRAG_REPORT_NAME}}": _java_str(case.drag_report_name),
        "{{TOTAL_REPORT_NAME}}": _java_str(case.total_report_name),
        "{{OUTLET_PRESSURE_REPORT_NAME}}": _java_str(case.outlet_pressure_report_name),
        "{{TRAIN_SURFACE_PRESSURE_REPORT_NAME}}": _java_str(case.train_surface_pressure_report_name),
        "{{DOMAIN_BLOCK_NAME}}": _java_str(case.domain_block_name),
        "{{ZONE1_NAME}}": _java_str(case.zone1_name),
        "{{ZONE2_NAME}}": _java_str(case.zone2_name),
        "{{TRAIN_SURFACE_CONTROL_NAME}}": _java_str(case.train_surface_control_name),
        "{{PRISM_MESHER_NAME}}": _java_str(case.prism_mesher_name),
        "{{MAX_STEPS_CRITERION_NAME}}": _java_str(case.max_steps_criterion_name),
        "{{GROUND_SLIDING}}": "true" if case.ground_sliding else "false",
        "{{PARAM_UPDATE_FILE}}": str((case_dir / PARAM_UPDATE_FILE).resolve()).replace("\\", "/"),
        "{{CHECK_INTERVAL}}": str(check_interval),
        "{{EXTRA_VOLUME_CONTROL_UPDATES}}": _build_extra_volume_control_updates(case),
        "{{EXTRA_SURFACE_CONTROL_UPDATES}}": _build_surface_control_updates(case),
    }

    macro = _load_macro_template()
    for key, value in subs.items():
        macro = macro.replace(key, value)
    return macro


def _java_str(value: str) -> str:
    """Escape non-ASCII characters as Java Unicode escapes to avoid encoding issues."""
    return "".join(f"\\u{ord(char):04X}" if ord(char) > 127 else char for char in value)


def _build_extra_volume_control_updates(case: Case) -> str:
    lines: list[str] = []
    for item in getattr(case, "volume_mesh_controls", []) or []:
        if not isinstance(item, dict):
            continue
        control_name = str(item.get("control_name", "")).strip()
        if not control_name:
            continue
        try:
            size = float(item.get("size", 0.0))
        except (TypeError, ValueError):
            continue
        if size <= 0.0:
            continue
        lines.append(
            '            applyNamedVolumeControlSize(sim, meshOp, '
            f'"{_java_str(control_name)}", {size}, units_m);'
        )
    if not lines:
        return "            // No extra volume mesh controls configured."
    return "\n".join(lines)


def _build_surface_control_updates(case: Case) -> str:
    lines: list[str] = []
    for item in getattr(case, "surface_mesh_controls", []) or []:
        if not isinstance(item, dict):
            continue
        control_name = str(item.get("control_name", "")).strip()
        if not control_name:
            continue
        try:
            target_size = float(item.get("target_size", 0.0))
        except (TypeError, ValueError):
            target_size = 0.0
        try:
            min_size = float(item.get("min_size", 0.0))
        except (TypeError, ValueError):
            min_size = 0.0
        try:
            prism_layers = int(item.get("prism_layers", 0))
        except (TypeError, ValueError):
            prism_layers = 0
        try:
            prism_thickness = float(item.get("prism_thickness", 0.0))
        except (TypeError, ValueError):
            prism_thickness = 0.0
        try:
            prism_wall_thickness = float(item.get("prism_wall_thickness", 0.0))
        except (TypeError, ValueError):
            prism_wall_thickness = 0.0

        if (
            target_size <= 0.0
            and min_size <= 0.0
            and prism_layers <= 0
            and prism_thickness <= 0.0
            and prism_wall_thickness <= 0.0
        ):
            continue

        lines.append(
            "            applyNamedSurfaceControlSettings("
            f'sim, meshOp, "{_java_str(control_name)}", '
            f"{target_size}, {min_size}, {prism_layers}, {prism_thickness}, {prism_wall_thickness});"
        )
    if not lines:
        return "            // No extra surface mesh controls configured."
    return "\n".join(lines)


def _load_macro_template() -> str:
    return _MACRO_TEMPLATE_PATH.read_text(encoding="utf-8")
