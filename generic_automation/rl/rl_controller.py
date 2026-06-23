from __future__ import annotations

import logging
import math
import random
from pathlib import Path
from typing import Any

from generic_automation.core.adapter_base import Case
from generic_automation.rl.residual_metrics import residual_diagnostics, residual_log_slope
from generic_automation.rl.rl_action_space import RLActionNames, RLActionSpace, RLInstabilityGuardConfig
from generic_automation.rl.rl_controller_settings import load_controller_settings
from generic_automation.rl.rl_controller_storage import (
    append_controller_trace,
    load_controller_state,
    save_controller_state,
)
from generic_automation.rl.rl_controller_utils import clip as _clip, parameter_changes as _parameter_changes
from generic_automation.rl.rl_observation_state import (
    attach_decision_chunk_metrics,
    build_state,
    residual_stage_label,
    summarize_observation,
)
from generic_automation.rl.rl_reward_components import (
    action_change_penalty as _action_change_penalty,
    baseline_speed_for_stage,
    chunk_cpu_seconds,
    chunk_wall_time_seconds,
    convergence_bonus,
    divergence_penalty as _divergence_penalty,
    oscillation_penalty as _oscillation_penalty,
    physics_drift_penalty,
    speed_score as _speed_score,
    stagnation_penalty as _stagnation_penalty,
    update_adaptive_stage_baseline,
)
from generic_automation.rl.rl_runtime_registry import (
    RL_RUNTIME_PARAMETER_NAMES,
    RL_RUNTIME_PARAMETER_SET,
)
from generic_automation.core.runtime_value_utils import (
    rounded_or_none as _rounded_or_none,
    safe_float as _safe_float,
)

log = logging.getLogger(__name__)


class ReinforcementLearningController:
    STATE_FILE = "rl/rl_controller_state.json"
    TRACE_FILE = "rl/rl_controller_trace.jsonl"

    ACTION_HOLD = "hold"
    ACTION_RELAX_UP = "pressure_relaxation_up"
    ACTION_RELAX_DOWN = "pressure_relaxation_down"
    ACTION_RELAX_INITIAL_UP = "pressure_relaxation_initial_value_up"
    ACTION_RELAX_INITIAL_DOWN = "pressure_relaxation_initial_value_down"
    ACTION_RELAX_END_ITER_UP = "pressure_relaxation_end_iteration_up"
    ACTION_RELAX_END_ITER_DOWN = "pressure_relaxation_end_iteration_down"
    ACTION_PRESSURE_AMG_CYCLE_V = "pressure_amg_cycle_v"
    ACTION_PRESSURE_AMG_CYCLE_W = "pressure_amg_cycle_w"
    ACTION_VELOCITY_AMG_CYCLE_FLEX = "velocity_amg_cycle_flex"
    ACTION_VELOCITY_AMG_CYCLE_V = "velocity_amg_cycle_v"

    ACTIONS = (
        ACTION_HOLD,
        ACTION_RELAX_UP,
        ACTION_RELAX_DOWN,
        ACTION_RELAX_INITIAL_UP,
        ACTION_RELAX_INITIAL_DOWN,
        ACTION_RELAX_END_ITER_UP,
        ACTION_RELAX_END_ITER_DOWN,
        ACTION_PRESSURE_AMG_CYCLE_V,
        ACTION_PRESSURE_AMG_CYCLE_W,
        ACTION_VELOCITY_AMG_CYCLE_FLEX,
        ACTION_VELOCITY_AMG_CYCLE_V,
    )

    SAFE_PARAMETER_NAMES = RL_RUNTIME_PARAMETER_NAMES

    PARAMETER_ACTIONS = {
        "pressure_relaxation_factor": (
            ACTION_RELAX_UP,
            ACTION_RELAX_DOWN,
        ),
        "pressure_relaxation_initial_value": (
            ACTION_RELAX_INITIAL_UP,
            ACTION_RELAX_INITIAL_DOWN,
        ),
        "pressure_relaxation_end_iteration": (
            ACTION_RELAX_END_ITER_UP,
            ACTION_RELAX_END_ITER_DOWN,
        ),
        "pressure_amg_cycle": (
            ACTION_PRESSURE_AMG_CYCLE_V,
            ACTION_PRESSURE_AMG_CYCLE_W,
        ),
        "velocity_amg_cycle": (
            ACTION_VELOCITY_AMG_CYCLE_FLEX,
            ACTION_VELOCITY_AMG_CYCLE_V,
        ),
    }

    OBSERVATION_PARAMETER_KEYS = RL_RUNTIME_PARAMETER_NAMES

    OBSERVATION_RESIDUAL_KEYS = {
        "continuity": "Continuity",
        "x_momentum": "X-momentum",
        "y_momentum": "Y-momentum",
        "z_momentum": "Z-momentum",
        "tke": "Tke",
        "sdr": "Sdr",
        "energy": "Energy",
    }

    RESIDUAL_STAGE_THRESHOLDS = (
        ("gt_1e-2", math.inf, 1.0e-2),
        ("1e-2_to_1e-3", 1.0e-2, 1.0e-3),
        ("1e-3_to_1e-4", 1.0e-3, 1.0e-4),
        ("1e-4_to_1e-5", 1.0e-4, 1.0e-5),
        ("le_1e-5", 1.0e-5, 0.0),
    )

    def __init__(
        self,
        rl_config: dict[str, Any],
        case_dir: Path,
        case: Case,
    ) -> None:
        self._rl_config = rl_config
        self._case_dir = case_dir
        self._case_dir.mkdir(parents=True, exist_ok=True)
        self._case = case
        self._drag_metric = case.drag_report_name
        self._total_metric = str(case.total_report_name or "").strip()
        self._pressure_metric = str(case.train_surface_pressure_report_name or "").strip()
        for attr_name, value in load_controller_settings(
            rl_config,
            safe_parameter_names=RL_RUNTIME_PARAMETER_NAMES,
            safe_parameter_set=RL_RUNTIME_PARAMETER_SET,
            parameter_actions=self.PARAMETER_ACTIONS,
            hold_action=self.ACTION_HOLD,
        ).items():
            setattr(self, attr_name, value)

        seed = self._random_seed
        self._rng = random.Random()
        if seed not in (None, ""):
            self._rng.seed(int(seed))

        self._action_space = RLActionSpace(
            case=self._case,
            action_names=RLActionNames(
                hold=self.ACTION_HOLD,
                relax_up=self.ACTION_RELAX_UP,
                relax_down=self.ACTION_RELAX_DOWN,
                relax_initial_up=self.ACTION_RELAX_INITIAL_UP,
                relax_initial_down=self.ACTION_RELAX_INITIAL_DOWN,
                relax_end_iter_up=self.ACTION_RELAX_END_ITER_UP,
                relax_end_iter_down=self.ACTION_RELAX_END_ITER_DOWN,
                pressure_amg_cycle_v=self.ACTION_PRESSURE_AMG_CYCLE_V,
                pressure_amg_cycle_w=self.ACTION_PRESSURE_AMG_CYCLE_W,
                velocity_amg_cycle_flex=self.ACTION_VELOCITY_AMG_CYCLE_FLEX,
                velocity_amg_cycle_v=self.ACTION_VELOCITY_AMG_CYCLE_V,
            ),
            allowed_actions=self._allowed_actions,
            priority_disallow_hold=self._priority_disallow_hold,
            relaxation_step=self._relaxation_step,
            pressure_relaxation_initial_value_step=(
                self._pressure_relaxation_initial_value_step
            ),
            pressure_relaxation_end_iteration_step=(
                self._pressure_relaxation_end_iteration_step
            ),
            instability_guard=RLInstabilityGuardConfig(
                enabled=self._block_pressure_relaxation_up_on_instability,
                residual_limit=self._block_pressure_relaxation_up_residual_limit,
                pressure_limit=self._block_pressure_relaxation_up_pressure_limit,
                pressure_amplitude_limit=(
                    self._block_pressure_relaxation_up_pressure_amplitude_limit
                ),
                turbulent_viscosity_cells_limit=(
                    self._block_pressure_relaxation_up_turbulent_viscosity_cells_limit
                ),
            ),
        )

        self._state_path = case_dir / self.STATE_FILE
        self._trace_path = case_dir / self.TRACE_FILE
        self._q_table: dict[str, dict[str, float]] = {}
        self._visit_counts: dict[str, int] = {}
        self._adaptive_stage_baselines: dict[str, float] = {}
        self._adaptive_stage_counts: dict[str, int] = {}
        self._previous_state: str | None = None
        self._previous_action: str | None = None
        self._previous_action_applied: bool = False
        self._previous_observation: dict[str, Any] | None = None
        self._last_suggest_metadata: dict[str, Any] | None = None
        self._load_state()

    @property
    def last_action(self) -> str | None:
        return self._previous_action

    @property
    def last_suggest_metadata(self) -> dict[str, Any] | None:
        return self._last_suggest_metadata

    def mark_last_action_applied(
        self,
        applied: bool,
        reason: str | None = None,
    ) -> None:
        self._previous_action_applied = bool(applied)
        if self._last_suggest_metadata is not None:
            self._last_suggest_metadata["action_applied"] = self._previous_action_applied
            if reason:
                self._last_suggest_metadata["action_application_reason"] = reason
            else:
                self._last_suggest_metadata.pop("action_application_reason", None)
        self._save_state()

    def get_window_diagnostics(self, window: list[dict[str, Any]]) -> dict[str, Any]:
        slope = residual_log_slope(window, lookback=20)
        flags = residual_diagnostics(
            window,
            lookback=20,
            stagnation_abs_slope_threshold=self._small_decay_per_iter_threshold,
        )
        return {
            "residual_log_slope": slope,
            "residual_rebounded": flags["rebounded"],
            "residual_oscillating": flags["oscillating"],
            "residual_stagnating": flags["stagnating"],
        }

    def suggest(
        self,
        window: list[dict[str, Any]],
        current_values: dict[str, Any],
        constraints: dict[str, Any] | None = None,
        trigger_iteration: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        constraints = constraints or {}
        observation = self._summarize_observation(window, current_values)
        observation = self._attach_decision_chunk_metrics(observation)
        state = self._build_state(observation, constraints)
        reward_info = self._update_from_latest_observation(
            current_state=state,
            current_observation=observation,
            constraints=constraints,
        )

        valid_actions = self._valid_actions(current_values, constraints)
        valid_actions, instability_guard = self._filter_instability_actions(
            valid_actions,
            observation,
        )
        action, decision_mode = self._select_action(state, valid_actions)
        proposal = self._proposal_for_action(action, current_values, constraints)
        current_snapshot = dict(observation.get("current_parameters", {}))
        proposed_snapshot = dict(current_snapshot)
        proposed_snapshot.update(proposal)

        metadata: dict[str, Any] = {
            "controller": "reinforcement_learning",
            "state": state,
            "action": action,
            "decision_mode": decision_mode,
            "epsilon": round(self._epsilon, 6),
            "observation": observation,
            "allowed_parameters": sorted(self._allowed_parameter_names),
            "valid_actions": valid_actions,
            "current_parameters": current_snapshot,
            "proposed_parameters": proposed_snapshot,
            "action_applied": None,
            "parameter_changes": _parameter_changes(
                current_snapshot,
                proposed_snapshot,
                keys=self._allowed_parameter_names,
            ),
        }
        if reward_info:
            metadata["reward"] = reward_info
        if instability_guard:
            metadata["instability_guard"] = instability_guard

        self._append_trace(
            trigger_iteration=trigger_iteration,
            metadata=metadata,
            proposal=proposal,
        )

        self._previous_state = state
        self._previous_action = action
        self._previous_action_applied = False
        self._previous_observation = observation
        self._last_suggest_metadata = metadata
        self._epsilon = max(self._min_epsilon, self._epsilon * self._epsilon_decay)
        self._save_state()
        return proposal, metadata

    def _load_state(self) -> None:
        for attr_name, value in load_controller_state(
            self._state_path,
            actions=self.ACTIONS,
            logger=log,
        ).items():
            setattr(self, attr_name, value)

    def _save_state(self) -> None:
        save_controller_state(
            self._state_path,
            {
            "q_table": self._q_table,
            "visit_counts": self._visit_counts,
            "epsilon": self._epsilon,
            "previous_state": self._previous_state,
            "previous_action": self._previous_action,
            "previous_action_applied": self._previous_action_applied,
            "previous_observation": self._previous_observation,
            "adaptive_stage_baselines": self._adaptive_stage_baselines,
            "adaptive_stage_counts": self._adaptive_stage_counts,
            },
        )

    def _append_trace(
        self,
        trigger_iteration: int | None,
        metadata: dict[str, Any],
        proposal: dict[str, Any],
    ) -> None:
        append_controller_trace(
            self._trace_path,
            trigger_iteration=trigger_iteration,
            metadata=metadata,
            proposal=proposal,
        )

    def _summarize_observation(
        self,
        window: list[dict[str, Any]],
        current_values: dict[str, Any],
    ) -> dict[str, Any]:
        return summarize_observation(
            case=self._case,
            observation_window=self._observation_window,
            drag_metric=self._drag_metric,
            total_metric=self._total_metric,
            pressure_metric=self._pressure_metric,
            observation_parameter_keys=self.OBSERVATION_PARAMETER_KEYS,
            observation_residual_keys=self.OBSERVATION_RESIDUAL_KEYS,
            window=window,
            current_values=current_values,
            residual_lookback=20,
            stagnation_abs_slope_threshold=self._small_decay_per_iter_threshold,
        )

    def _attach_decision_chunk_metrics(
        self,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        return attach_decision_chunk_metrics(
            observation,
            self._previous_observation,
        )

    def _build_state(
        self,
        observation: dict[str, Any],
        constraints: dict[str, Any],
    ) -> str:
        return build_state(
            case=self._case,
            action_space=self._action_space,
            residual_stage_thresholds=self.RESIDUAL_STAGE_THRESHOLDS,
            observation=observation,
            constraints=constraints,
        )

    def _update_from_latest_observation(
        self,
        current_state: str,
        current_observation: dict[str, Any],
        constraints: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            self._previous_state is None
            or self._previous_action is None
            or self._previous_observation is None
        ):
            return {}
        if not self._previous_action_applied:
            return {
                "skipped": True,
                "reason": "previous_action_not_applied",
                "previous_action": self._previous_action,
            }

        reward_info = self._compute_reward(
            previous_observation=self._previous_observation,
            current_observation=current_observation,
            constraints=constraints,
        )

        self._ensure_state(self._previous_state)
        self._ensure_state(current_state)

        reward_value = float(reward_info.get("value", 0.0))
        old_q = self._q_table[self._previous_state][self._previous_action]
        future_q = max(self._q_table[current_state].values())
        updated_q = old_q + self._learning_rate * (
            reward_value + self._discount_factor * future_q - old_q
        )
        self._q_table[self._previous_state][self._previous_action] = updated_q

        reward_info["old_q"] = round(old_q, 6)
        reward_info["new_q"] = round(updated_q, 6)
        reward_info["previous_action"] = self._previous_action
        return reward_info

    def _compute_reward(
        self,
        previous_observation: dict[str, Any],
        current_observation: dict[str, Any],
        constraints: dict[str, Any],
    ) -> dict[str, Any]:
        eps = 1.0e-30
        previous_residual = _safe_float(previous_observation.get("max_residual"))
        current_residual = _safe_float(current_observation.get("max_residual"))
        if previous_residual is None or previous_residual <= 0.0:
            previous_residual = eps
        if current_residual is None or current_residual <= 0.0:
            current_residual = eps

        residual_decay = math.log10(max(previous_residual, eps)) - math.log10(max(current_residual, eps))
        previous_iteration = int(previous_observation.get("iteration", 0))
        current_iteration = int(current_observation.get("iteration", previous_iteration))
        chunk_iterations = max(current_iteration - previous_iteration, 1)
        decay_per_iter = residual_decay / max(chunk_iterations, 1)

        chunk_wall_time_seconds_value = chunk_wall_time_seconds(previous_observation, current_observation)
        chunk_cpu_seconds_value = chunk_cpu_seconds(
            previous_observation,
            current_observation,
            chunk_wall_time_seconds_value=chunk_wall_time_seconds_value,
            default_num_cores=int(getattr(self._case, "num_cores", 1) or 1),
        )
        raw_speed = residual_decay / max(chunk_cpu_seconds_value, 1.0)

        residual_stage = residual_stage_label(previous_residual, self.RESIDUAL_STAGE_THRESHOLDS)
        baseline_speed = baseline_speed_for_stage(
            residual_stage,
            raw_speed,
            configured_stage_baselines=self._configured_stage_baselines,
            adaptive_stage_baselines=self._adaptive_stage_baselines,
            adaptive_stage_counts=self._adaptive_stage_counts,
            baseline_min_samples=self._baseline_min_samples,
        )

        recent_residuals = [
            value
            for value in current_observation.get("recent_max_residuals", [])
            if _safe_float(value) is not None
        ]
        default_convergence_residual = float(
            getattr(self._case, "convergence_residual", 1.0e-5) or 1.0e-5
        )

        speed_score_value = _speed_score(raw_speed, baseline_speed, self._speed_score_clip_range)
        stagnation_penalty_value = _stagnation_penalty(decay_per_iter, self._small_decay_per_iter_threshold)
        divergence_penalty_value = _divergence_penalty(current_residual, previous_residual)
        oscillation_penalty_value = _oscillation_penalty(recent_residuals)
        action_change_penalty_value = _action_change_penalty(
            previous_observation,
            current_observation,
            constraints,
            allowed_parameter_names=self._allowed_parameter_names,
            action_space=self._action_space,
        )
        physics_drift_penalty_value, physics_info = physics_drift_penalty(
            current_observation,
            current_residual,
            baseline_final_total=self._baseline_final_total,
            total_force_metric_name=self._total_metric,
            default_max_iterations=int(getattr(self._case, "max_iterations", 2000) or 2000),
            default_convergence_residual=default_convergence_residual,
            physics_drift_tolerance=self._physics_drift_tolerance,
            physics_force_epsilon=self._physics_force_epsilon,
            physics_gate_start_fraction=self._physics_gate_start_fraction,
            physics_gate_full_fraction=self._physics_gate_full_fraction,
            physics_gate_disable_residual_ratio=self._physics_gate_disable_residual_ratio,
            physics_gate_full_residual_ratio=self._physics_gate_full_residual_ratio,
            physics_gate_severe_drift_multiplier=self._physics_gate_severe_drift_multiplier,
            physics_gate_severe_min=self._physics_gate_severe_min,
        )
        convergence_bonus_value = convergence_bonus(
            current_observation,
            current_residual,
            default_convergence_residual=default_convergence_residual,
            stable_chunks=self._convergence_stable_chunks,
        )

        reward = (
            10.0 * speed_score_value
            - 2.0 * divergence_penalty_value
            - 0.5 * oscillation_penalty_value
            - 0.3 * stagnation_penalty_value
            - 0.05 * action_change_penalty_value
            - 1.0 * physics_drift_penalty_value
            + 1.0 * convergence_bonus_value
        )
        if not math.isfinite(reward):
            reward = 0.0

        update_adaptive_stage_baseline(
            residual_stage,
            raw_speed,
            configured_stage_baselines=self._configured_stage_baselines,
            adaptive_stage_baselines=self._adaptive_stage_baselines,
            adaptive_stage_counts=self._adaptive_stage_counts,
        )
        parameter_changes = _parameter_changes(
            previous_observation.get("current_parameters", {}),
            current_observation.get("current_parameters", {}),
            keys=self._allowed_parameter_names,
        )
        return {
            "value": round(reward, 6),
            "speed_score": round(speed_score_value, 6),
            "divergence_penalty": round(divergence_penalty_value, 6),
            "oscillation_penalty": round(oscillation_penalty_value, 6),
            "stagnation_penalty": round(stagnation_penalty_value, 6),
            "action_change_penalty": round(action_change_penalty_value, 6),
            "physics_drift_penalty": round(physics_drift_penalty_value, 6),
            "convergence_bonus": round(convergence_bonus_value, 6),
            "residual_decay": round(residual_decay, 6),
            "decay_per_iter": round(decay_per_iter, 6),
            "raw_speed": round(raw_speed, 6),
            "baseline_speed_same_stage": _rounded_or_none(baseline_speed),
            "baseline_speed_sample_count": int(self._adaptive_stage_counts.get(residual_stage, 0)),
            "residual_stage": residual_stage,
            "chunk_iterations": chunk_iterations,
            "chunk_wall_time_seconds": _rounded_or_none(chunk_wall_time_seconds_value),
            "chunk_cpu_seconds": _rounded_or_none(chunk_cpu_seconds_value),
            "applied_parameter_changes": parameter_changes,
            **physics_info,
        }

    def _select_action(
        self,
        state: str,
        valid_actions: list[str],
    ) -> tuple[str, str]:
        self._ensure_state(state)
        if not valid_actions:
            return self.ACTION_HOLD, "fallback"

        if self._rng.random() < self._epsilon:
            action = self._rng.choice(valid_actions)
            decision_mode = "explore"
        else:
            ranked = sorted(
                valid_actions,
                key=lambda item: (self._q_table[state][item], item),
                reverse=True,
            )
            action = ranked[0]
            decision_mode = "exploit"
            if (
                self._prefer_hold_on_nonpositive_q
                and self.ACTION_HOLD in valid_actions
                and self._q_table[state][action] <= 0.0
                and self._q_table[state][self.ACTION_HOLD]
                >= self._q_table[state][action] - 1.0e-12
            ):
                action = self.ACTION_HOLD
                decision_mode = "exploit_hold"

        visit_key = f"{state}|{action}"
        self._visit_counts[visit_key] = self._visit_counts.get(visit_key, 0) + 1
        return action, decision_mode

    def _valid_actions(
        self,
        current_values: dict[str, Any],
        constraints: dict[str, Any],
    ) -> list[str]:
        return self._action_space.valid_actions(current_values, constraints)

    def _proposal_for_action(
        self,
        action: str,
        current_values: dict[str, Any],
        constraints: dict[str, Any],
    ) -> dict[str, Any]:
        return self._action_space.proposal_for_action(
            action,
            current_values,
            constraints,
        )

    def _filter_instability_actions(
        self,
        valid_actions: list[str],
        observation: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any] | None]:
        return self._action_space.filter_instability_actions(valid_actions, observation)

    def _ensure_state(self, state: str) -> None:
        if state in self._q_table:
            return
        self._q_table[state] = {action: 0.0 for action in self.ACTIONS}
