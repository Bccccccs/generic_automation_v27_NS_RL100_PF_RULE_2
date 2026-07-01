from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapter_base import Case
from rl_controller_utils import bucketize_range
from runtime_value_utils import rounded_or_none, safe_float


@dataclass(frozen=True)
class RLActionNames:
    hold: str
    relax_up: str
    relax_down: str
    relax_initial_up: str
    relax_initial_down: str
    relax_end_iter_up: str
    relax_end_iter_down: str
    pressure_amg_cycle_v: str
    pressure_amg_cycle_w: str
    velocity_amg_cycle_flex: str
    velocity_amg_cycle_v: str


@dataclass(frozen=True)
class RLInstabilityGuardConfig:
    enabled: bool
    residual_limit: float
    pressure_limit: float
    pressure_amplitude_limit: float
    turbulent_viscosity_cells_limit: int


class RLActionSpace:
    def __init__(
        self,
        *,
        case: Case,
        action_names: RLActionNames,
        allowed_actions: set[str],
        priority_disallow_hold: bool,
        relaxation_step: float,
        pressure_relaxation_initial_value_step: float,
        pressure_relaxation_end_iteration_step: int,
        instability_guard: RLInstabilityGuardConfig,
    ) -> None:
        self._case = case
        self._action_names = action_names
        self._allowed_actions = set(allowed_actions)
        self._priority_disallow_hold = bool(priority_disallow_hold)
        self._relaxation_step = float(relaxation_step)
        self._pressure_relaxation_initial_value_step = float(
            pressure_relaxation_initial_value_step
        )
        self._pressure_relaxation_end_iteration_step = int(
            pressure_relaxation_end_iteration_step
        )
        self._instability_guard = instability_guard

    def current_float_value(
        self,
        current_values: dict[str, Any],
        key: str,
        default: float,
    ) -> float:
        raw_value = current_values.get(key, default)
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return float(default)

    def current_int_value(
        self,
        current_values: dict[str, Any],
        key: str,
        default: int,
    ) -> int:
        raw_value = current_values.get(key, default)
        try:
            return int(round(float(raw_value)))
        except (TypeError, ValueError):
            return int(default)

    def current_pressure_amg_cycle(self, current_values: dict[str, Any]) -> int:
        return self._current_binary_cycle(
            current_values,
            "pressure_amg_cycle",
            int(getattr(self._case, "pressure_amg_cycle", 0)),
        )

    def current_velocity_amg_cycle(self, current_values: dict[str, Any]) -> int:
        return self._current_binary_cycle(
            current_values,
            "velocity_amg_cycle",
            int(getattr(self._case, "velocity_amg_cycle", 0)),
        )

    def float_bounds(
        self,
        constraints: dict[str, Any],
        key: str,
        default_lo: float,
        default_hi: float,
    ) -> tuple[float, float]:
        constraint = constraints.get(key, {})
        return (
            float(constraint.get("min", default_lo)),
            float(constraint.get("max", default_hi)),
        )

    def int_bounds(
        self,
        constraints: dict[str, Any],
        key: str,
        default_lo: int,
        default_hi: int,
    ) -> tuple[int, int]:
        constraint = constraints.get(key, {})
        return (
            int(round(float(constraint.get("min", default_lo)))),
            int(round(float(constraint.get("max", default_hi)))),
        )

    def clamp_float(
        self,
        value: float,
        constraints: dict[str, Any],
        key: str,
        default_lo: float,
        default_hi: float,
    ) -> float:
        lo, hi = self.float_bounds(constraints, key, default_lo, default_hi)
        return min(max(value, lo), hi)

    def clamp_int(
        self,
        value: int,
        constraints: dict[str, Any],
        key: str,
        default_lo: int,
        default_hi: int,
    ) -> int:
        lo, hi = self.int_bounds(constraints, key, default_lo, default_hi)
        return min(max(int(value), lo), hi)

    def pressure_relaxation_initial_value_bounds(
        self,
        current_values: dict[str, Any],
        constraints: dict[str, Any],
    ) -> tuple[float, float]:
        lo, hi = self.float_bounds(
            constraints,
            "pressure_relaxation_initial_value",
            0.0,
            0.50,
        )
        relax_factor = self.current_float_value(
            current_values,
            "pressure_relaxation_factor",
            float(getattr(self._case, "pressure_relaxation_factor", 0.5)),
        )
        hi = min(hi, relax_factor)
        if hi < lo:
            hi = lo
        return lo, hi

    def pressure_relaxation_end_iteration_bounds(
        self,
        current_values: dict[str, Any],
        constraints: dict[str, Any],
    ) -> tuple[int, int]:
        lo, hi = self.int_bounds(
            constraints,
            "pressure_relaxation_end_iteration",
            1,
            200,
        )
        start_iteration = self.current_int_value(
            current_values,
            "pressure_relaxation_start_iteration",
            int(getattr(self._case, "pressure_relaxation_start_iteration", 1)),
        )
        lo = max(lo, start_iteration)
        if hi < lo:
            hi = lo
        return lo, hi

    def bucketize_numeric(
        self,
        key: str,
        value: float | int,
        constraints: dict[str, Any],
        default_lo: float,
        default_hi: float,
    ) -> int:
        if key.endswith("_iteration"):
            lo, hi = self.int_bounds(
                constraints,
                key,
                int(round(default_lo)),
                int(round(default_hi)),
            )
            return bucketize_range(float(value), float(lo), float(hi))

        lo, hi = self.float_bounds(constraints, key, default_lo, default_hi)
        return bucketize_range(float(value), lo, hi)

    def valid_actions(
        self,
        current_values: dict[str, Any],
        constraints: dict[str, Any],
    ) -> list[str]:
        valid = [self._action_names.hold]
        (relax_value, relax_initial_value, relax_end_iteration,
         pressure_amg_cycle, velocity_amg_cycle) = self._current_relax_params(current_values)

        lo, hi = self.float_bounds(
            constraints,
            "pressure_relaxation_factor",
            0.05,
            0.95,
        )
        if relax_value + self._relaxation_step <= hi + 1.0e-9:
            valid.append(self._action_names.relax_up)
        if relax_value - self._relaxation_step >= lo - 1.0e-9:
            valid.append(self._action_names.relax_down)

        init_lo, init_hi = self.pressure_relaxation_initial_value_bounds(
            current_values,
            constraints,
        )
        if relax_initial_value + self._pressure_relaxation_initial_value_step <= init_hi + 1.0e-9:
            valid.append(self._action_names.relax_initial_up)
        if relax_initial_value - self._pressure_relaxation_initial_value_step >= init_lo - 1.0e-9:
            valid.append(self._action_names.relax_initial_down)

        end_lo, end_hi = self.pressure_relaxation_end_iteration_bounds(
            current_values,
            constraints,
        )
        if relax_end_iteration + self._pressure_relaxation_end_iteration_step <= end_hi:
            valid.append(self._action_names.relax_end_iter_up)
        if relax_end_iteration - self._pressure_relaxation_end_iteration_step >= end_lo:
            valid.append(self._action_names.relax_end_iter_down)

        if pressure_amg_cycle != 0:
            valid.append(self._action_names.pressure_amg_cycle_v)
        if pressure_amg_cycle != 1:
            valid.append(self._action_names.pressure_amg_cycle_w)
        if velocity_amg_cycle != 0:
            valid.append(self._action_names.velocity_amg_cycle_flex)
        if velocity_amg_cycle != 1:
            valid.append(self._action_names.velocity_amg_cycle_v)
        return self.filter_valid_actions(valid)

    def proposal_for_action(
        self,
        action: str,
        current_values: dict[str, Any],
        constraints: dict[str, Any],
    ) -> dict[str, Any]:
        (relax_value, relax_initial_value, relax_end_iteration,
         pressure_amg_cycle, velocity_amg_cycle) = self._current_relax_params(current_values)

        proposal: dict[str, Any] = {}
        if action == self._action_names.relax_up:
            proposal["pressure_relaxation_factor"] = self.clamp_float(
                relax_value + self._relaxation_step,
                constraints,
                "pressure_relaxation_factor",
                0.05,
                0.95,
            )
        elif action == self._action_names.relax_down:
            proposal["pressure_relaxation_factor"] = self.clamp_float(
                relax_value - self._relaxation_step,
                constraints,
                "pressure_relaxation_factor",
                0.05,
                0.95,
            )
        elif action == self._action_names.relax_initial_up:
            _, init_hi = self.pressure_relaxation_initial_value_bounds(
                current_values,
                constraints,
            )
            proposal["pressure_relaxation_initial_value"] = min(
                relax_initial_value + self._pressure_relaxation_initial_value_step,
                init_hi,
            )
        elif action == self._action_names.relax_initial_down:
            init_lo, _ = self.pressure_relaxation_initial_value_bounds(
                current_values,
                constraints,
            )
            proposal["pressure_relaxation_initial_value"] = max(
                relax_initial_value - self._pressure_relaxation_initial_value_step,
                init_lo,
            )
        elif action == self._action_names.relax_end_iter_up:
            _, end_hi = self.pressure_relaxation_end_iteration_bounds(
                current_values,
                constraints,
            )
            proposal["pressure_relaxation_end_iteration"] = min(
                relax_end_iteration + self._pressure_relaxation_end_iteration_step,
                end_hi,
            )
        elif action == self._action_names.relax_end_iter_down:
            end_lo, _ = self.pressure_relaxation_end_iteration_bounds(
                current_values,
                constraints,
            )
            proposal["pressure_relaxation_end_iteration"] = max(
                relax_end_iteration - self._pressure_relaxation_end_iteration_step,
                end_lo,
            )
        elif action == self._action_names.pressure_amg_cycle_v:
            proposal["pressure_amg_cycle"] = 0
        elif action == self._action_names.pressure_amg_cycle_w:
            proposal["pressure_amg_cycle"] = 1
        elif action == self._action_names.velocity_amg_cycle_flex:
            proposal["velocity_amg_cycle"] = 0
        elif action == self._action_names.velocity_amg_cycle_v:
            proposal["velocity_amg_cycle"] = 1

        current = {
            "pressure_relaxation_factor": relax_value,
            "pressure_relaxation_initial_value": relax_initial_value,
            "pressure_relaxation_end_iteration": relax_end_iteration,
            "pressure_amg_cycle": pressure_amg_cycle,
            "velocity_amg_cycle": velocity_amg_cycle,
        }
        return {k: v for k, v in proposal.items() if v != current[k]}

    def filter_instability_actions(
        self,
        valid_actions: list[str],
        observation: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any] | None]:
        if not self._instability_guard.enabled:
            return valid_actions, None

        latest_residual = safe_float(observation.get("max_residual"))
        pressure_latest = safe_float(observation.get("pressure_latest"))
        pressure_amplitude = safe_float(observation.get("pressure_recent_amplitude"))
        turbulent_viscosity_limited_cells = int(
            safe_float(observation.get("turbulent_viscosity_limited_cells")) or 0
        )
        guard = self._instability_guard
        unstable = (
            bool(observation.get("residual_rebounded"))
            or bool(observation.get("residual_oscillating"))
            or (latest_residual is not None and latest_residual > guard.residual_limit)
            or (pressure_latest is not None and pressure_latest > guard.pressure_limit)
            or (pressure_amplitude is not None and pressure_amplitude > guard.pressure_amplitude_limit)
            or turbulent_viscosity_limited_cells > guard.turbulent_viscosity_cells_limit
        )
        if not unstable or self._action_names.relax_up not in valid_actions:
            return valid_actions, None

        filtered = [
            action for action in valid_actions if action != self._action_names.relax_up
        ]
        guard = {
            "active": True,
            "reason": "blocked_pressure_relaxation_up_on_instability",
            "latest_residual": rounded_or_none(latest_residual),
            "pressure_latest": rounded_or_none(pressure_latest),
            "pressure_recent_amplitude": rounded_or_none(pressure_amplitude),
            "turbulent_viscosity_limited_cells": turbulent_viscosity_limited_cells,
            "residual_limit": guard.residual_limit,
            "pressure_limit": guard.pressure_limit,
            "pressure_amplitude_limit": guard.pressure_amplitude_limit,
            "turbulent_viscosity_limited_cells_limit": guard.turbulent_viscosity_cells_limit,
            "blocked_action": self._action_names.relax_up,
        }
        return filtered or [self._action_names.hold], guard

    def filter_valid_actions(self, valid_actions: list[str]) -> list[str]:
        filtered = [action for action in valid_actions if action in self._allowed_actions]
        if self._priority_disallow_hold:
            filtered = [
                action for action in filtered if action != self._action_names.hold
            ]
        return filtered or [self._action_names.hold]

    def _current_relax_params(
        self,
        current_values: dict[str, Any],
    ) -> tuple[float, float, int, int, int]:
        return (
            self.current_float_value(
                current_values, "pressure_relaxation_factor",
                float(getattr(self._case, "pressure_relaxation_factor", 0.5)),
            ),
            self.current_float_value(
                current_values, "pressure_relaxation_initial_value",
                float(getattr(self._case, "pressure_relaxation_initial_value", 0.0)),
            ),
            self.current_int_value(
                current_values, "pressure_relaxation_end_iteration",
                int(getattr(self._case, "pressure_relaxation_end_iteration", 1)),
            ),
            self.current_pressure_amg_cycle(current_values),
            self.current_velocity_amg_cycle(current_values),
        )

    def _current_binary_cycle(
        self,
        current_values: dict[str, Any],
        key: str,
        default: int,
    ) -> int:
        raw_value = current_values.get(key, default)
        try:
            numeric = int(round(float(raw_value)))
        except (TypeError, ValueError):
            numeric = int(default)
        return 1 if numeric >= 1 else 0
