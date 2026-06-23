from __future__ import annotations

from typing import Any

from generic_automation.core.adapter_base import Case
from generic_automation.core.runtime_value_utils import as_bool, binary_cycle_choice, rounded_or_none, safe_float


class ManualInterventionRules:
    def __init__(self, rl_config: dict[str, Any], case: Case) -> None:
        self._case = case
        cfg = rl_config.get("manual_rules", {}) or {}
        if not isinstance(cfg, dict):
            cfg = {}

        self._enabled = as_bool(cfg.get("enabled", True))
        self._startup_end_iteration = max(1, int(cfg.get("startup_end_iteration", 50)))
        self._startup_prf_max = float(cfg.get("startup_pressure_relaxation_factor_max", 0.32))
        self._startup_pri_max = float(cfg.get("startup_pressure_relaxation_initial_value_max", 0.10))
        self._startup_pre_min = max(1, int(cfg.get("startup_pressure_relaxation_end_iteration_min", 30)))
        self._startup_block_vel = as_bool(cfg.get("startup_block_velocity_cycle_changes", True))

        self._ps_lookback = max(1, int(cfg.get("pressure_stress_lookback_windows", 3)))
        self._ps_required_hits = max(1, int(cfg.get("pressure_stress_required_hits", 2)))
        self._ps_amg_hit_ratio = max(0.1, float(cfg.get("pressure_stress_amg_cycle_hit_ratio", 0.95)))
        self._ps_solver_iter_threshold = max(1, int(cfg.get("pressure_stress_solver_iterations_threshold", 15)))
        self._ps_prf_max = float(cfg.get("pressure_stress_pressure_relaxation_factor_max", 0.30))
        self._ps_pri_max = float(cfg.get("pressure_stress_pressure_relaxation_initial_value_max", 0.08))
        self._ps_pre_min = max(1, int(cfg.get("pressure_stress_pressure_relaxation_end_iteration_min", 50)))
        self._ps_force_pressure_amg = binary_cycle_choice(cfg.get("pressure_stress_force_pressure_amg_cycle", 1), default=1)
        self._ps_block_vel = as_bool(cfg.get("pressure_stress_block_velocity_cycle_changes", True))

        self._stable_min_iter = max(1, int(cfg.get("stable_min_iteration", 120)))
        self._stable_lookback = max(2, int(cfg.get("stable_lookback_windows", 4)))
        self._stable_cont_drop = max(0.0, float(cfg.get("stable_continuity_drop_ratio", 0.80)))
        self._stable_drag_amp_max = max(0.0, float(cfg.get("stable_drag_amplitude_ratio_max", 0.10)))
        self._stable_tke_max = max(0.0, float(cfg.get("stable_tke_residual_max", 1.0)))
        self._stable_force_vel_amg = binary_cycle_choice(cfg.get("stable_force_velocity_amg_cycle", 1), default=1)
        self._stable_force_pres_amg = binary_cycle_choice(cfg.get("stable_force_pressure_amg_cycle", 0), default=0)

        self._tke_lookback = max(2, int(cfg.get("tke_guard_lookback_windows", 4)))
        self._tke_rebound_ratio = max(1.0, float(cfg.get("tke_rebound_growth_ratio", 1.8)))
        self._tke_recent_ratio = max(1.0, float(cfg.get("tke_recent_increase_ratio", 1.25)))
        self._tke_abs_limit = max(0.0, float(cfg.get("tke_rebound_absolute_limit", 1.0)))
        self._tke_prf_max = float(cfg.get("tke_guard_pressure_relaxation_factor_max", 0.30))
        self._tke_pri_max = float(cfg.get("tke_guard_pressure_relaxation_initial_value_max", 0.08))
        self._tke_pre_min = max(1, int(cfg.get("tke_guard_pressure_relaxation_end_iteration_min", 50)))
        self._tke_force_pres_amg = binary_cycle_choice(cfg.get("tke_guard_force_pressure_amg_cycle", 1), default=1)
        self._tke_force_vel_amg = binary_cycle_choice(cfg.get("tke_guard_force_velocity_amg_cycle", 0), default=0)

    def apply(
        self,
        ai_params: dict[str, Any],
        controller_meta: dict[str, Any],
        rl_observation: dict[str, Any],
        current_values: dict[str, Any],
        window: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        if not self._enabled:
            return ai_params, None

        current_parameters = controller_meta.get("current_parameters", {})
        if not isinstance(current_parameters, dict) or not current_parameters:
            current_parameters = dict(current_values)

        updated = dict(ai_params)
        activated_rules: list[str] = []
        rule_details: list[dict[str, Any]] = []
        iteration = int(rl_observation.get("iteration", 0) or 0)

        pressure_stress = self._pressure_stress_snapshot(window)
        tke_guard = self._tke_guard_snapshot(window, rl_observation)
        stable_speedup = self._stable_speedup_snapshot(
            window=window,
            rl_observation=rl_observation,
            pressure_stress_active=pressure_stress["active"],
            tke_guard_active=tke_guard["active"],
        )

        if iteration > 0 and iteration <= self._startup_end_iteration:
            changes = self._apply_startup_pressure_guard(updated, current_parameters)
            if changes:
                activated_rules.append("startup_pressure_guard")
                rule_details.append({
                    "rule": "startup_pressure_guard",
                    "iteration": iteration,
                    "changes": changes,
                    "startup_end_iteration": self._startup_end_iteration,
                })

        if pressure_stress["active"]:
            changes = self._apply_pressure_stress_recovery(updated, current_parameters)
            if changes:
                activated_rules.append("pressure_stress_recovery")
                rule_details.append({"rule": "pressure_stress_recovery", "changes": changes, **pressure_stress})

        if tke_guard["active"]:
            changes = self._apply_tke_guard_recovery(updated, current_parameters)
            if changes:
                activated_rules.append("tke_guard_recovery")
                rule_details.append({"rule": "tke_guard_recovery", "changes": changes, **tke_guard})

        if stable_speedup["active"]:
            changes = self._apply_stable_velocity_acceleration(updated, current_parameters)
            if changes:
                activated_rules.append("stable_velocity_acceleration")
                rule_details.append({"rule": "stable_velocity_acceleration", "changes": changes, **stable_speedup})

        if not activated_rules:
            return updated, None

        reason = "+".join(activated_rules)
        controller_meta["manual_rules"] = {"active": True, "reason": reason, "rules": rule_details}
        controller_meta["safety_override"] = {
            "active": True,
            "reason": reason,
            "manual_rules": True,
            "rules": rule_details,
            "recovery_parameters": updated,
            "pause_after_trip": False,
        }
        return updated, reason

    # --- rule appliers ---

    def _apply_startup_pressure_guard(
        self, updated: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if self._cap_float(updated, current, "pressure_relaxation_factor", self._startup_prf_max):
            changes["pressure_relaxation_factor"] = updated.get("pressure_relaxation_factor")
        if self._cap_float(updated, current, "pressure_relaxation_initial_value", self._startup_pri_max):
            changes["pressure_relaxation_initial_value"] = updated.get("pressure_relaxation_initial_value")
        if self._floor_int(updated, current, "pressure_relaxation_end_iteration", self._startup_pre_min):
            changes["pressure_relaxation_end_iteration"] = updated.get("pressure_relaxation_end_iteration")
        if self._startup_block_vel and self._remove_change(updated, current, "velocity_amg_cycle"):
            changes["velocity_amg_cycle"] = "blocked_during_startup"
        return changes

    def _apply_pressure_stress_recovery(
        self, updated: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if self._cap_float(updated, current, "pressure_relaxation_factor", self._ps_prf_max):
            changes["pressure_relaxation_factor"] = updated.get("pressure_relaxation_factor")
        if self._cap_float(updated, current, "pressure_relaxation_initial_value", self._ps_pri_max):
            changes["pressure_relaxation_initial_value"] = updated.get("pressure_relaxation_initial_value")
        if self._floor_int(updated, current, "pressure_relaxation_end_iteration", self._ps_pre_min):
            changes["pressure_relaxation_end_iteration"] = updated.get("pressure_relaxation_end_iteration")
        if self._set_cycle(updated, current, "pressure_amg_cycle", self._ps_force_pressure_amg):
            changes["pressure_amg_cycle"] = updated.get("pressure_amg_cycle")
        if self._ps_block_vel and self._remove_change(updated, current, "velocity_amg_cycle"):
            changes["velocity_amg_cycle"] = "blocked_under_pressure_stress"
        return changes

    def _apply_tke_guard_recovery(
        self, updated: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if self._cap_float(updated, current, "pressure_relaxation_factor", self._tke_prf_max):
            changes["pressure_relaxation_factor"] = updated.get("pressure_relaxation_factor")
        if self._cap_float(updated, current, "pressure_relaxation_initial_value", self._tke_pri_max):
            changes["pressure_relaxation_initial_value"] = updated.get("pressure_relaxation_initial_value")
        if self._floor_int(updated, current, "pressure_relaxation_end_iteration", self._tke_pre_min):
            changes["pressure_relaxation_end_iteration"] = updated.get("pressure_relaxation_end_iteration")
        if self._set_cycle(updated, current, "pressure_amg_cycle", self._tke_force_pres_amg):
            changes["pressure_amg_cycle"] = updated.get("pressure_amg_cycle")
        if self._set_cycle(updated, current, "velocity_amg_cycle", self._tke_force_vel_amg):
            changes["velocity_amg_cycle"] = updated.get("velocity_amg_cycle")
        return changes

    def _apply_stable_velocity_acceleration(
        self, updated: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if self._set_cycle(updated, current, "velocity_amg_cycle", self._stable_force_vel_amg):
            changes["velocity_amg_cycle"] = updated.get("velocity_amg_cycle")
        if self._set_cycle(updated, current, "pressure_amg_cycle", self._stable_force_pres_amg):
            changes["pressure_amg_cycle"] = updated.get("pressure_amg_cycle")
        return changes

    # --- condition snapshots ---

    def _pressure_stress_snapshot(self, window: list[dict[str, Any]]) -> dict[str, Any]:
        view = window[-self._ps_lookback:]
        max_cycles = max(1, int(getattr(self._case, "pressure_amg_max_cycles", 20) or 20))
        amg_hit_threshold = float(max_cycles) * self._ps_amg_hit_ratio
        amg_hits = solver_hits = 0
        latest_amg = latest_solver_iter = None
        for row in view:
            amg = safe_float(row.get("pressure_amg_cycles"))
            si = safe_float(row.get("pressure_solver_iterations"))
            if amg is not None and amg >= amg_hit_threshold - 1.0e-9:
                amg_hits += 1
            if si is not None and si >= self._ps_solver_iter_threshold:
                solver_hits += 1
            if amg is not None:
                latest_amg = amg
            if si is not None:
                latest_solver_iter = si
        active = amg_hits >= self._ps_required_hits or solver_hits >= self._ps_required_hits
        return {
            "active": active,
            "lookback_windows": self._ps_lookback,
            "required_hits": self._ps_required_hits,
            "pressure_amg_cycle_hits": amg_hits,
            "pressure_solver_iteration_hits": solver_hits,
            "pressure_amg_cycle_hit_threshold": round(amg_hit_threshold, 6),
            "pressure_solver_iterations_threshold": self._ps_solver_iter_threshold,
            "latest_pressure_amg_cycles": rounded_or_none(latest_amg),
            "latest_pressure_solver_iterations": rounded_or_none(latest_solver_iter),
        }

    def _tke_guard_snapshot(
        self, window: list[dict[str, Any]], rl_observation: dict[str, Any]
    ) -> dict[str, Any]:
        view = window[-self._tke_lookback:]
        tke_values = [
            v for v in (safe_float(r.get("tke_residual")) for r in view) if v is not None
        ]
        if not tke_values:
            cur = safe_float(rl_observation.get("tke_residual"))
            tke_values = [cur] if cur is not None else []
        latest = tke_values[-1] if tke_values else None
        previous = tke_values[-2] if len(tke_values) >= 2 else None
        prev_mean = (
            sum(tke_values[:-1]) / float(len(tke_values) - 1)
            if len(tke_values) >= 2 else None
        )
        g_prev = (latest / previous if latest is not None and previous not in (None, 0.0) else None)
        g_mean = (latest / prev_mean if latest is not None and prev_mean not in (None, 0.0) else None)
        active = bool(
            latest is not None
            and latest >= self._tke_abs_limit
            and (
                (g_prev is not None and g_prev >= self._tke_rebound_ratio)
                or (g_mean is not None and g_mean >= self._tke_recent_ratio)
            )
        )
        return {
            "active": active,
            "lookback_windows": self._tke_lookback,
            "latest_tke_residual": rounded_or_none(latest),
            "previous_tke_residual": rounded_or_none(previous),
            "recent_tke_mean": rounded_or_none(prev_mean),
            "growth_vs_previous": rounded_or_none(g_prev),
            "growth_vs_recent_mean": rounded_or_none(g_mean),
            "absolute_limit": self._tke_abs_limit,
            "growth_ratio_limit": self._tke_rebound_ratio,
            "recent_increase_ratio_limit": self._tke_recent_ratio,
        }

    def _stable_speedup_snapshot(
        self,
        window: list[dict[str, Any]],
        rl_observation: dict[str, Any],
        pressure_stress_active: bool,
        tke_guard_active: bool,
    ) -> dict[str, Any]:
        iteration = int(rl_observation.get("iteration", 0) or 0)
        view = window[-self._stable_lookback:]
        cont_values = [v for v in (safe_float(r.get("continuity_residual")) for r in view) if v is not None]
        drag_key = str(getattr(self._case, "drag_report_name", "") or "")
        drag_values = [v for v in (safe_float(r.get(drag_key)) for r in view) if v is not None] if drag_key else []
        earliest_cont = cont_values[0] if cont_values else None
        latest_cont = cont_values[-1] if cont_values else None
        cont_ratio = (
            latest_cont / earliest_cont
            if earliest_cont not in (None, 0.0) and latest_cont is not None else None
        )
        drag_mean = (
            sum(drag_values) / float(len(drag_values)) if drag_values
            else safe_float(rl_observation.get("drag_recent_mean"))
        )
        drag_amp = (
            max(drag_values) - min(drag_values) if len(drag_values) >= 2
            else safe_float(rl_observation.get("drag_recent_amplitude"))
        )
        drag_amp_ratio = (
            abs(drag_amp) / max(abs(drag_mean), 1.0)
            if drag_amp is not None and drag_mean is not None else None
        )
        latest_tke = safe_float(rl_observation.get("tke_residual"))
        active = bool(
            iteration >= self._stable_min_iter
            and not pressure_stress_active
            and not tke_guard_active
            and cont_ratio is not None and cont_ratio <= self._stable_cont_drop
            and drag_amp_ratio is not None and drag_amp_ratio <= self._stable_drag_amp_max
            and latest_tke is not None and latest_tke <= self._stable_tke_max
            and not bool(rl_observation.get("residual_rebounded"))
            and not bool(rl_observation.get("residual_oscillating"))
        )
        return {
            "active": active,
            "iteration": iteration,
            "lookback_windows": self._stable_lookback,
            "continuity_ratio": rounded_or_none(cont_ratio),
            "continuity_drop_ratio_limit": self._stable_cont_drop,
            "drag_amplitude_ratio": rounded_or_none(drag_amp_ratio),
            "drag_amplitude_ratio_limit": self._stable_drag_amp_max,
            "latest_tke_residual": rounded_or_none(latest_tke),
            "tke_residual_limit": self._stable_tke_max,
        }

    # --- param mutation utilities ---

    def _cap_float(
        self, updated: dict[str, Any], current: dict[str, Any], param: str, max_val: float
    ) -> bool:
        proposed = safe_float(updated.get(param)) if param in updated else None
        cur = safe_float(current.get(param))
        if proposed is not None and proposed > max_val + 1.0e-9:
            updated[param] = float(max_val)
            return True
        if proposed is None and cur is not None and cur > max_val + 1.0e-9:
            updated[param] = float(max_val)
            return True
        return False

    def _floor_int(
        self, updated: dict[str, Any], current: dict[str, Any], param: str, min_val: int
    ) -> bool:
        proposed = safe_float(updated.get(param)) if param in updated else None
        cur = safe_float(current.get(param))
        if proposed is not None and int(round(proposed)) < min_val:
            updated[param] = int(min_val)
            return True
        if proposed is None and cur is not None and int(round(cur)) < min_val:
            updated[param] = int(min_val)
            return True
        return False

    def _set_cycle(
        self, updated: dict[str, Any], current: dict[str, Any], param: str, target: int
    ) -> bool:
        proposed = safe_float(updated.get(param)) if param in updated else None
        cur = safe_float(current.get(param))
        if proposed is not None and int(round(proposed)) != target:
            updated[param] = int(target)
            return True
        if proposed is None and cur is not None and int(round(cur)) != target:
            updated[param] = int(target)
            return True
        return False

    def _remove_change(
        self, updated: dict[str, Any], current: dict[str, Any], param: str
    ) -> bool:
        if param not in updated:
            return False
        proposed = safe_float(updated.get(param))
        cur = safe_float(current.get(param))
        if proposed is not None and cur is not None and int(round(proposed)) == int(round(cur)):
            return False
        updated.pop(param, None)
        return True
