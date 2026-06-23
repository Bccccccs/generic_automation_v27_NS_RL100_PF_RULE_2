from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
try:
    from typing import Any, List, Protocol
except ImportError:
    from typing import Any, List
    from typing_extensions import Protocol


@dataclass
class Case:
    case_name: str
    inlet_velocity: float
    inlet_temperature: float
    outlet_pressure: float
    base_mesh_size: float

    yaw_angle: float = 0.0
    reference_area: float = 10.0
    reference_length: float = 25.0

    geometry_file: str = ""

    surface_mesh_size: float = 0.08
    min_surface_size: float = 0.08
    surface_growth_rate: float = 1.2
    num_prism_layers: int = 13
    prism_layer_thickness: float = 0.01
    prism_layer_stretching: float = 1.2
    prism_wall_thickness: float = 1e-4

    train_target_size: float = 0.08
    train_min_size: float = 0.04
    train_prism_thickness: float = 0.15
    train_prism_layers: int = 0

    zone1_mesh_size: float = 0.64
    zone2_mesh_size: float = 0.16
    zone3_mesh_size: float = 0.0
    zone4_mesh_size: float = 0.0

    domain_block_name: str = "base"
    zone1_name: str = "zone1"
    zone2_name: str = "zone2"
    zone3_name: str = "zone3"
    zone4_name: str = "zone4"
    bogie_volume_control_name: str = "bogies"
    bogie_volume_mesh_size: float = 0.0
    ground_volume_control_name: str = "ground"
    ground_volume_mesh_size: float = 0.0
    pantograph_coarse_volume_control_name: str = "pantograph_corse"
    pantograph_coarse_volume_size: float = 0.0
    pantograph_fine_volume_control_name: str = "pantograph_fine"
    pantograph_fine_volume_size: float = 0.0
    train_surface_control_name: str = ""
    prism_mesher_name: str = "Prism Layer Mesher"
    max_steps_criterion_name: str = "Maximum Steps"

    log_frequency: int = 100
    drag_report_name: str = "drag"
    total_report_name: str = "total"
    pressure_report_name: str = "train_surface_pressure_max"
    outlet_pressure_report_name: str = "outlet_pressure_avg"
    train_surface_pressure_report_name: str = "train_surface_pressure_max"

    turbulence_model: str = "k-omega-sst"
    fluid: str = "air"
    density: float = 1.225
    dynamic_viscosity: float = 1.81e-5
    energy_equation: bool = False
    simulation_type: str = "steady"

    inlet_turbulence_intensity: float = 0.01
    inlet_turbulent_length_scale: float = 0.5

    max_iterations: int = 2000
    convergence_residual: float = 1.0e-5
    pressure_relaxation_factor: float = 0.3
    pressure_relaxation_initial_value: float = 0.07
    pressure_relaxation_start_iteration: int = 1
    pressure_relaxation_end_iteration: int = 10
    velocity_relaxation_initial_value: float = 0.07
    velocity_relaxation_start_iteration: int = 1
    velocity_relaxation_end_iteration: int = 10
    pressure_amg_cycle: int = 0
    velocity_amg_cycle: int = 0
    amg_cycle: int = 0
    amg_solver: int = 1
    pressure_amg_max_cycles: int = 20
    pressure_amg_converge_tol: float = 0.1
    pressure_amg_epsilon: float = 0.0
    pressure_amg_smoother: str = ""
    pressure_amg_acceleration: str = ""
    pressure_amg_pre_sweeps: int = 0
    pressure_amg_post_sweeps: int = 0
    pressure_amg_max_levels: int = 0
    time_step: float = 0.001
    num_time_steps: int = 500
    run_mode: str = "full_run"
    input_sim: str = ""
    mesh_cache_key: str = ""
    checkpoint_interval: int = 0
    config_path: str = ""
    config_dir: str = ""

    starccm_path: str = "starccm+"
    template_sim: str = ""
    num_cores: int = 8
    pod_key: str = ""

    region_name: str = "Region"
    inlet_boundary: str = "Inlet"
    outlet_boundary: str = "Outlet"
    wall_boundary: str = "TrainSurface"
    ground_boundary: str = "Ground"
    ground_sliding: bool = True
    symmetry_boundary: str = "Symmetry"

    report_names: List[str] = field(default_factory=list)
    volume_mesh_controls: List[dict[str, Any]] = field(default_factory=list)
    surface_mesh_controls: List[dict[str, Any]] = field(default_factory=list)

    domain_corner1: List[float] = field(default_factory=lambda: [-50.0, -50.0, 0.0])
    domain_corner2: List[float] = field(default_factory=lambda: [120.0, 50.0, 50.0])
    initial_velocity: float = 0.0
    monitor_start_iteration: int = 0
    monitor_update_frequency: int = 100
    wall_treatment: str = "all-y-plus"
    cad_sharp_angle: float = 30.0

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, default=str)


class Adapter(Protocol):
    def run(self, case: Case, case_dir: Path) -> None:
        ...
