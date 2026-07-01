from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from adapter_base import Case


REQUIRED_CASE_FIELDS = (
    "inlet_velocity",
    "inlet_temperature",
    "outlet_pressure",
    "base_mesh_size",
)

_DISABLED_TEXT_VALUES = {"", "none", "null", "disabled"}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _as_mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_float_list(value: Any, default: list[float]) -> list[float]:
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [float(item.strip()) for item in value.split(",") if item.strip()]
    return list(default)


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _dedupe_items(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        item_key = str(item.get(key, ""))
        if not item_key or item_key in seen:
            continue
        seen.add(item_key)
        deduped.append(item)
    return deduped


def _append_volume_control(
    controls: list[dict[str, Any]],
    control_name: str,
    size: float,
) -> None:
    name = _text(control_name)
    if not name or size <= 0.0:
        return
    controls.append({"control_name": name, "size": float(size)})


def load_config(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_config(cfg: dict[str, Any]) -> None:
    case = cfg.get("case")
    if not isinstance(case, dict):
        raise ValueError("config must contain a mapping at 'case'")

    missing = [field for field in REQUIRED_CASE_FIELDS if field not in case]
    if missing:
        raise ValueError("missing required case fields: " + ", ".join(missing))

    for field in REQUIRED_CASE_FIELDS:
        try:
            float(case[field])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"case.{field} must be numeric, got {case[field]!r}") from exc


def normalize_case_values(case: dict[str, Any]) -> dict[str, float]:
    return {field: float(case[field]) for field in REQUIRED_CASE_FIELDS}


def resolve_result_root(config_path: Path, cfg: dict[str, Any]) -> Path:
    result_root = Path(str(cfg.get("result_root", "results")))
    if not result_root.is_absolute():
        result_root = (config_path.parent / result_root).resolve()
    return result_root


def resolve_case_dir(
    config_path: Path,
    cfg: dict[str, Any],
    case_name: str | None = None,
) -> Path:
    resolved_case_name = case_name or str(cfg.get("case_name", "case"))
    return resolve_result_root(config_path, cfg) / resolved_case_name


def parse_case(cfg: dict[str, Any]) -> Case:
    validate_config(cfg)
    case_cfg = cfg["case"]
    numeric = normalize_case_values(case_cfg)

    def case_value(key: str, default: Any = None) -> Any:
        return case_cfg.get(key, default)

    def config_or_case(key: str, default: Any) -> Any:
        return cfg.get(key, case_cfg.get(key, default))

    def default_train_surface_pressure_report_name() -> str:
        explicit = case_value("train_surface_pressure_report_name")
        if isinstance(explicit, str):
            explicit_name = explicit.strip()
            if explicit_name.lower() in _DISABLED_TEXT_VALUES:
                return ""
            return explicit_name
        if explicit is not None:
            return str(explicit)

        legacy_pressure_name = case_value("pressure_report_name")
        if isinstance(legacy_pressure_name, str):
            legacy_name = legacy_pressure_name.strip()
            if legacy_name.lower() in _DISABLED_TEXT_VALUES - {""}:
                return ""
            if legacy_name and legacy_name != "pressure":
                return legacy_name
        elif legacy_pressure_name not in (None, "", "pressure"):
            return str(legacy_pressure_name)

        return "train_surface_pressure_max"

    def default_pressure_relaxation_factor() -> float:
        value = case_value(
            "pressure_relaxation_factor",
            case_value("relaxation_factor", 0.3),
        )
        return _as_float(value, 0.3)

    def default_pressure_relaxation_initial_value() -> float:
        return _as_float(case_value("pressure_relaxation_initial_value", 0.07), 0.07)

    def default_pressure_relaxation_start_iteration() -> int:
        return _as_int(case_value("pressure_relaxation_start_iteration", 1), 1)

    def default_pressure_relaxation_end_iteration() -> int:
        return _as_int(case_value("pressure_relaxation_end_iteration", 10), 10)

    def default_velocity_relaxation_initial_value() -> float:
        return _as_float(case_value("velocity_relaxation_initial_value", 0.07), 0.07)

    def default_velocity_relaxation_start_iteration() -> int:
        return _as_int(case_value("velocity_relaxation_start_iteration", 1), 1)

    def default_velocity_relaxation_end_iteration() -> int:
        return _as_int(case_value("velocity_relaxation_end_iteration", 10), 10)

    def default_pressure_amg_cycle() -> int:
        return _as_int(
            case_value("pressure_amg_cycle", case_value("amg_cycle", 0)),
            0,
        )

    def default_velocity_amg_cycle() -> int:
        return _as_int(
            case_value("velocity_amg_cycle", case_value("amg_cycle", 0)),
            0,
        )

    def default_amg_cycle() -> int:
        return _as_int(
            case_value("amg_cycle", case_value("pressure_amg_cycle", 0)),
            0,
        )

    def default_amg_solver() -> int:
        return _as_int(case_value("amg_solver", case_value("amg_cycle", 1)), 1)

    def default_pressure_amg_max_cycles() -> int:
        return _as_int(case_value("pressure_amg_max_cycles", 20), 20)

    def default_pressure_amg_converge_tol() -> float:
        return _as_float(case_value("pressure_amg_converge_tol", 0.1), 0.1)

    def default_pressure_amg_epsilon() -> float:
        return _as_float(case_value("pressure_amg_epsilon", 0.0), 0.0)

    def default_pressure_amg_smoother() -> str:
        return str(case_value("pressure_amg_smoother", "")).strip()

    def default_pressure_amg_acceleration() -> str:
        return str(case_value("pressure_amg_acceleration", "")).strip()

    def default_pressure_amg_pre_sweeps() -> int:
        return _as_int(case_value("pressure_amg_pre_sweeps", 0), 0)

    def default_pressure_amg_post_sweeps() -> int:
        return _as_int(case_value("pressure_amg_post_sweeps", 0), 0)

    def default_pressure_amg_max_levels() -> int:
        return _as_int(case_value("pressure_amg_max_levels", 0), 0)

    def default_run_mode() -> str:
        raw = str(config_or_case("run_mode", "full_run") or "full_run").strip().lower()
        return raw or "full_run"

    def default_input_sim() -> str:
        for key in ("input_sim", "resume_checkpoint", "checkpoint_sim", "mesh_ready_sim"):
            value = config_or_case(key, "")
            if value in (None, ""):
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def default_mesh_cache_key() -> str:
        return str(config_or_case("mesh_cache_key", "") or "").strip()

    def default_checkpoint_interval() -> int:
        return max(0, _as_int(config_or_case("checkpoint_interval", 0), 0))

    def normalized_report_names() -> list[str]:
        names = _as_string_list(case_value("report_names", ""))
        if not names:
            return names

        train_pressure_name = default_train_surface_pressure_report_name()
        normalized: list[str] = []
        for name in names:
            if name == "pressure":
                name = train_pressure_name
            if not name:
                continue
            if name not in normalized:
                normalized.append(name)
        return normalized

    def normalized_volume_mesh_controls() -> list[dict[str, Any]]:
        controls: list[dict[str, Any]] = []

        for item in _as_mapping_list(case_value("volume_mesh_controls", [])):
            name = str(item.get("control_name", item.get("name", ""))).strip()
            size = _as_float(item.get("size", item.get("mesh_size", 0.0)), 0.0)
            _append_volume_control(controls, name, size)

        _append_volume_control(controls, config_or_case("zone3_name", "zone3"), _as_float(case_value("zone3_mesh_size", 0.0), 0.0))
        _append_volume_control(controls, config_or_case("zone4_name", "zone4"), _as_float(case_value("zone4_mesh_size", 0.0), 0.0))
        _append_volume_control(
            controls,
            config_or_case("bogie_volume_control_name", "bogies"),
            _as_float(case_value("bogie_volume_mesh_size", 0.0), 0.0),
        )
        _append_volume_control(
            controls,
            config_or_case("ground_volume_control_name", "ground"),
            _as_float(case_value("ground_volume_mesh_size", 0.0), 0.0),
        )
        _append_volume_control(
            controls,
            config_or_case("pantograph_coarse_volume_control_name", "pantograph_corse"),
            _as_float(case_value("pantograph_coarse_volume_size", 0.0), 0.0),
        )
        _append_volume_control(
            controls,
            config_or_case("pantograph_fine_volume_control_name", "pantograph_fine"),
            _as_float(case_value("pantograph_fine_volume_size", 0.0), 0.0),
        )

        return _dedupe_items(controls, "control_name")

    def normalized_surface_mesh_controls() -> list[dict[str, Any]]:
        raw_controls = case_value("surface_mesh_controls")
        if raw_controls in (None, "", []):
            raw_controls = case_value("train_surface_groups", [])

        controls: list[dict[str, Any]] = []
        for item in _as_mapping_list(raw_controls):
            control_name = str(item.get("control_name", item.get("name", ""))).strip()
            if not control_name:
                continue

            target_size = _as_float(
                item.get("target_size", item.get("mesh_size", 0.0)),
                0.0,
            )
            min_size = _as_float(item.get("min_size", 0.0), 0.0)
            prism_layers = _as_int(item.get("prism_layers", 0), 0)
            prism_thickness = _as_float(
                item.get("prism_thickness", item.get("total_prism_thickness", 0.0)),
                0.0,
            )
            prism_wall_thickness = _as_float(item.get("prism_wall_thickness", 0.0), 0.0)

            if (
                target_size <= 0.0
                and min_size <= 0.0
                and prism_layers <= 0
                and prism_thickness <= 0.0
                and prism_wall_thickness <= 0.0
            ):
                continue

            controls.append(
                {
                    "control_name": control_name,
                    "target_size": target_size,
                    "min_size": min_size,
                    "prism_layers": prism_layers,
                    "prism_thickness": prism_thickness,
                    "prism_wall_thickness": prism_wall_thickness,
                }
            )

        return _dedupe_items(controls, "control_name")

    case = Case(
        case_name=_text(cfg.get("case_name", "case_001")),
        inlet_velocity=numeric["inlet_velocity"],
        inlet_temperature=numeric["inlet_temperature"],
        outlet_pressure=numeric["outlet_pressure"],
        base_mesh_size=numeric["base_mesh_size"],
        yaw_angle=_as_float(case_value("yaw_angle", 0.0), 0.0),
        reference_area=_as_float(case_value("reference_area", 10.0), 10.0),
        reference_length=_as_float(case_value("reference_length", 25.0), 25.0),
        geometry_file=_text(case_value("geometry_file", "")),
        surface_mesh_size=_as_float(case_value("surface_mesh_size", 0.08), 0.08),
        min_surface_size=_as_float(case_value("min_surface_size", 0.08), 0.08),
        surface_growth_rate=_as_float(case_value("surface_growth_rate", 1.2), 1.2),
        num_prism_layers=_as_int(case_value("num_prism_layers", 13), 13),
        prism_layer_thickness=_as_float(case_value("prism_layer_thickness", 0.01), 0.01),
        prism_layer_stretching=_as_float(case_value("prism_layer_stretching", 1.2), 1.2),
        prism_wall_thickness=_as_float(case_value("prism_wall_thickness", 1e-4), 1e-4),
        train_target_size=_as_float(case_value("train_target_size", 0.08), 0.08),
        train_min_size=_as_float(case_value("train_min_size", 0.04), 0.04),
        train_prism_thickness=_as_float(case_value("train_prism_thickness", 0.15), 0.15),
        train_prism_layers=_as_int(case_value("train_prism_layers", 0), 0),
        zone1_mesh_size=_as_float(case_value("zone1_mesh_size", 0.64), 0.64),
        zone2_mesh_size=_as_float(case_value("zone2_mesh_size", 0.16), 0.16),
        zone3_mesh_size=_as_float(case_value("zone3_mesh_size", 0.0), 0.0),
        zone4_mesh_size=_as_float(case_value("zone4_mesh_size", 0.0), 0.0),
        domain_block_name=_text(config_or_case("domain_block_name", "base")),
        zone1_name=_text(config_or_case("zone1_name", "zone1")),
        zone2_name=_text(config_or_case("zone2_name", "zone2")),
        zone3_name=_text(config_or_case("zone3_name", "zone3")),
        zone4_name=_text(config_or_case("zone4_name", "zone4")),
        bogie_volume_control_name=_text(config_or_case("bogie_volume_control_name", "bogies")),
        bogie_volume_mesh_size=_as_float(case_value("bogie_volume_mesh_size", 0.0), 0.0),
        ground_volume_control_name=_text(config_or_case("ground_volume_control_name", "ground")),
        ground_volume_mesh_size=_as_float(case_value("ground_volume_mesh_size", 0.0), 0.0),
        pantograph_coarse_volume_control_name=_text(
            config_or_case("pantograph_coarse_volume_control_name", "pantograph_corse")
        ),
        pantograph_coarse_volume_size=_as_float(case_value("pantograph_coarse_volume_size", 0.0), 0.0),
        pantograph_fine_volume_control_name=_text(
            config_or_case("pantograph_fine_volume_control_name", "pantograph_fine")
        ),
        pantograph_fine_volume_size=_as_float(case_value("pantograph_fine_volume_size", 0.0), 0.0),
        train_surface_control_name=_text(config_or_case("train_surface_control_name", "")),
        prism_mesher_name=_text(config_or_case("prism_mesher_name", "Prism Layer Mesher")),
        max_steps_criterion_name=_text(config_or_case("max_steps_criterion_name", "Maximum Steps")),
        log_frequency=_as_int(case_value("log_frequency", 100), 100),
        drag_report_name=_text(case_value("drag_report_name", "drag")),
        total_report_name=_text(case_value("total_report_name", "total")),
        pressure_report_name=_text(case_value("pressure_report_name", "train_surface_pressure_max")),
        outlet_pressure_report_name=_text(case_value("outlet_pressure_report_name", "outlet_pressure_avg")),
        train_surface_pressure_report_name=default_train_surface_pressure_report_name(),
        turbulence_model=_text(case_value("turbulence_model", "k-omega-sst")),
        fluid=_text(case_value("fluid", "air")),
        density=_as_float(case_value("density", 1.225), 1.225),
        dynamic_viscosity=_as_float(case_value("dynamic_viscosity", 1.81e-5), 1.81e-5),
        energy_equation=_as_bool(case_value("energy_equation", False), False),
        simulation_type=_text(case_value("simulation_type", "steady")),
        inlet_turbulence_intensity=_as_float(case_value("inlet_turbulence_intensity", 0.01), 0.01),
        inlet_turbulent_length_scale=_as_float(case_value("inlet_turbulent_length_scale", 0.5), 0.5),
        max_iterations=_as_int(case_value("max_iterations", 2000), 2000),
        convergence_residual=_as_float(case_value("convergence_residual", 1e-5), 1e-5),
        pressure_relaxation_factor=default_pressure_relaxation_factor(),
        pressure_relaxation_initial_value=default_pressure_relaxation_initial_value(),
        pressure_relaxation_start_iteration=default_pressure_relaxation_start_iteration(),
        pressure_relaxation_end_iteration=default_pressure_relaxation_end_iteration(),
        velocity_relaxation_initial_value=default_velocity_relaxation_initial_value(),
        velocity_relaxation_start_iteration=default_velocity_relaxation_start_iteration(),
        velocity_relaxation_end_iteration=default_velocity_relaxation_end_iteration(),
        pressure_amg_cycle=default_pressure_amg_cycle(),
        velocity_amg_cycle=default_velocity_amg_cycle(),
        amg_cycle=default_amg_cycle(),
        amg_solver=default_amg_solver(),
        pressure_amg_max_cycles=default_pressure_amg_max_cycles(),
        pressure_amg_converge_tol=default_pressure_amg_converge_tol(),
        pressure_amg_epsilon=default_pressure_amg_epsilon(),
        pressure_amg_smoother=default_pressure_amg_smoother(),
        pressure_amg_acceleration=default_pressure_amg_acceleration(),
        pressure_amg_pre_sweeps=default_pressure_amg_pre_sweeps(),
        pressure_amg_post_sweeps=default_pressure_amg_post_sweeps(),
        pressure_amg_max_levels=default_pressure_amg_max_levels(),
        time_step=_as_float(case_value("time_step", 0.001), 0.001),
        num_time_steps=_as_int(case_value("num_time_steps", 500), 500),
        run_mode=default_run_mode(),
        input_sim=default_input_sim(),
        mesh_cache_key=default_mesh_cache_key(),
        checkpoint_interval=default_checkpoint_interval(),
        starccm_path=_text(cfg.get("starccm_path", "starccm+")),
        template_sim=_text(cfg.get("template_sim", "")),
        num_cores=_as_int(cfg.get("num_cores", 8), 8),
        pod_key=_text(cfg.get("pod_key", "")),
        region_name=_text(config_or_case("region_name", "Region")),
        inlet_boundary=_text(config_or_case("inlet_boundary", "Inlet")),
        outlet_boundary=_text(config_or_case("outlet_boundary", "Outlet")),
        wall_boundary=_text(config_or_case("wall_boundary", "TrainSurface")),
        ground_boundary=_text(config_or_case("ground_boundary", "Ground")),
        ground_sliding=_as_bool(config_or_case("ground_sliding", True), True),
        symmetry_boundary=_text(config_or_case("symmetry_boundary", "Symmetry")),
        report_names=normalized_report_names(),
        volume_mesh_controls=normalized_volume_mesh_controls(),
        surface_mesh_controls=normalized_surface_mesh_controls(),
        domain_corner1=_as_float_list(case_value("domain_corner1", [-50.0, -50.0, 0.0]), [-50.0, -50.0, 0.0]),
        domain_corner2=_as_float_list(case_value("domain_corner2", [120.0, 120.0, 50.0]), [120.0, 120.0, 50.0]),
        initial_velocity=_as_float(case_value("initial_velocity", 0.0), 0.0),
        monitor_start_iteration=_as_int(case_value("monitor_start_iteration", 0), 0),
        monitor_update_frequency=_as_int(case_value("monitor_update_frequency", 1), 1),
        wall_treatment=_text(case_value("wall_treatment", "all-y-plus")),
        cad_sharp_angle=_as_float(case_value("cad_sharp_angle", 30.0), 30.0),
    )

    if case.surface_mesh_size <= case.min_surface_size:
        raise ValueError(
            "Invalid meshing settings: case.surface_mesh_size must be greater than "
            f"case.min_surface_size (got {case.surface_mesh_size} <= {case.min_surface_size})."
        )
    if case.train_target_size <= case.train_min_size:
        raise ValueError(
            "Invalid train surface mesh settings: case.train_target_size must be greater than "
            f"case.train_min_size (got {case.train_target_size} <= {case.train_min_size})."
        )

    return case
