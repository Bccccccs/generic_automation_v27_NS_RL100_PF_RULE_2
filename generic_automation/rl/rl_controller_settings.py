from __future__ import annotations

from typing import Any

from generic_automation.rl.rl_controller_utils import (
    clip,
    parse_stage_baselines,
    resolve_allowed_actions,
    resolve_allowed_parameter_names,
)
from generic_automation.core.runtime_value_utils import as_bool, first_finite_float


def load_controller_settings(
    rl_config: dict[str, Any],
    *,
    safe_parameter_names: tuple[str, ...],
    safe_parameter_set: set[str],
    parameter_actions: dict[str, tuple[str, ...]],
    hold_action: str,
) -> dict[str, Any]:
    configured_allowed = resolve_allowed_parameter_names(
        rl_config.get("allowed_parameters")
    )
    safe_allowed = configured_allowed & safe_parameter_set
    if safe_allowed:
        allowed_parameter_names = safe_allowed
    else:
        allowed_parameter_names = set(safe_parameter_names)

    physics_gate_start_fraction = clip(
        float(rl_config.get("physics_gate_start_fraction", 0.10)),
        0.0,
        1.0,
    )

    return {
        "_learning_rate": float(rl_config.get("learning_rate", 0.85)),
        "_discount_factor": float(rl_config.get("discount_factor", 0.90)),
        "_epsilon": float(rl_config.get("epsilon", 0.20)),
        "_epsilon_decay": float(rl_config.get("epsilon_decay", 0.995)),
        "_min_epsilon": float(rl_config.get("min_epsilon", 0.05)),
        "_relaxation_step": float(rl_config.get("relaxation_step", 0.05)),
        "_pressure_relaxation_initial_value_step": float(
            rl_config.get(
                "pressure_relaxation_initial_value_step",
                rl_config.get("relaxation_step", 0.05),
            )
        ),
        "_pressure_relaxation_end_iteration_step": max(
            1,
            int(rl_config.get("pressure_relaxation_end_iteration_step", 5)),
        ),
        "_observation_window": max(
            4,
            int(rl_config.get("observation_window", 8)),
        ),
        "_priority_disallow_hold": as_bool(
            rl_config.get("priority_disallow_hold", False)
        ),
        "_prefer_hold_on_nonpositive_q": as_bool(
            rl_config.get("prefer_hold_on_nonpositive_q", True)
        ),
        "_baseline_min_samples": max(
            1,
            int(rl_config.get("baseline_min_samples", 3)),
        ),
        "_speed_score_clip_range": max(
            1.0,
            float(rl_config.get("speed_score_clip_range", 2.0)),
        ),
        "_small_decay_per_iter_threshold": float(
            rl_config.get("small_threshold_per_iter", 0.001)
        ),
        "_physics_drift_tolerance": float(
            rl_config.get("physics_drift_tolerance", 0.02)
        ),
        "_physics_force_epsilon": float(
            rl_config.get("physics_force_epsilon", 1.0e-12)
        ),
        "_physics_gate_start_fraction": physics_gate_start_fraction,
        "_physics_gate_full_fraction": max(
            physics_gate_start_fraction + 1.0e-9,
            clip(
                float(rl_config.get("physics_gate_full_fraction", 0.50)),
                0.0,
                1.0,
            ),
        ),
        "_physics_gate_disable_residual_ratio": max(
            float(rl_config.get("physics_gate_disable_residual_ratio", 100.0)),
            1.0,
        ),
        "_physics_gate_full_residual_ratio": max(
            float(rl_config.get("physics_gate_full_residual_ratio", 5.0)),
            1.0e-9,
        ),
        "_physics_gate_severe_drift_multiplier": max(
            float(rl_config.get("physics_gate_severe_drift_multiplier", 5.0)),
            1.0,
        ),
        "_physics_gate_severe_min": clip(
            float(rl_config.get("physics_gate_severe_min", 0.25)),
            0.0,
            1.0,
        ),
        "_convergence_stable_chunks": max(
            1,
            int(rl_config.get("convergence_stable_chunks", 3)),
        ),
        "_allowed_parameter_names": allowed_parameter_names,
        "_allowed_actions": resolve_allowed_actions(
            allowed_parameter_names,
            parameter_actions,
            hold_action,
        ),
        "_block_pressure_relaxation_up_on_instability": as_bool(
            rl_config.get("block_pressure_relaxation_up_on_instability", True)
        ),
        "_block_pressure_relaxation_up_residual_limit": float(
            rl_config.get("block_pressure_relaxation_up_residual_limit", 1.0)
        ),
        "_block_pressure_relaxation_up_pressure_limit": float(
            rl_config.get("block_pressure_relaxation_up_pressure_limit", 1.0e4)
        ),
        "_block_pressure_relaxation_up_pressure_amplitude_limit": float(
            rl_config.get(
                "block_pressure_relaxation_up_pressure_amplitude_limit",
                5.0e3,
            )
        ),
        "_block_pressure_relaxation_up_turbulent_viscosity_cells_limit": int(
            rl_config.get(
                "block_pressure_relaxation_up_turbulent_viscosity_limited_cells",
                80,
            )
        ),
        "_configured_stage_baselines": parse_stage_baselines(
            rl_config.get("baseline_stage_speeds")
        ),
        "_baseline_final_total": first_finite_float(
            rl_config.get("baseline_final_total"),
            rl_config.get("baseline_final_primary_metric"),
            rl_config.get("baseline_final_total_force"),
            rl_config.get("baseline_final_drag"),
            rl_config.get("baseline_final_metric"),
        ),
        "_random_seed": rl_config.get("random_seed"),
    }
