from __future__ import annotations

from typing import Any

from adapter_base import Case
from runtime_value_utils import as_bool, rounded_or_none, safe_float


class RLSafetyOverride:
    def __init__(
        self,
        rl_config: dict[str, Any],
        case: Case,
        baseline_rl_values: dict[str, Any],
    ) -> None:
        self._case = case
        self._baseline_rl_values = baseline_rl_values

        self._tripped = False
        self._cooldown_cycles = max(0, int(rl_config.get("catastrophic_recovery_cooldown_cycles", 3)))
        self._cooldown_remaining = 0
        self._residual_limit = float(rl_config.get("catastrophic_residual_limit", 1.0e6))
        self._growth_ratio = float(rl_config.get("catastrophic_growth_ratio", 10.0))
        self._pause_after_trip = as_bool(rl_config.get("pause_after_catastrophic_trip", True))
        self._pressure_limit = float(rl_config.get("catastrophic_pressure_limit", 3.0e4))
        self._pressure_amp_limit = float(rl_config.get("catastrophic_pressure_amplitude_limit", 2.0e4))
        self._pressure_growth_ratio = float(rl_config.get("catastrophic_pressure_growth_ratio", 8.0))
        self._tv_cells_limit = int(rl_config.get("catastrophic_turbulent_viscosity_limited_cells", 200))

        self._block_prf_on_instability = as_bool(
            rl_config.get("block_pressure_relaxation_up_on_instability", True)
        )
        self._block_prf_residual_limit = float(
            rl_config.get("block_pressure_relaxation_up_residual_limit", 1.0)
        )
        self._block_prf_pressure_limit = float(
            rl_config.get("block_pressure_relaxation_up_pressure_limit", 1.0e4)
        )
        self._block_prf_pressure_amp_limit = float(
            rl_config.get("block_pressure_relaxation_up_pressure_amplitude_limit", 5.0e3)
        )
        self._block_prf_tv_cells_limit = int(
            rl_config.get("block_pressure_relaxation_up_turbulent_viscosity_limited_cells", 80)
        )

    def check_trip(
        self,
        ai_params: dict[str, Any],
        controller_meta: dict[str, Any],
        rl_observation: dict[str, Any],
        current_values: dict[str, Any],
        window: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        """Return (params, trip_reason). If trip_reason is not None, caller should stop."""
        if self._tripped and self._pause_after_trip:
            controller_meta["safety_override"] = {
                "active": True, "reason": "catastrophic_trip_paused",
                "recovery_parameters": {}, "residual_limit": self._residual_limit,
                "growth_ratio_limit": self._growth_ratio,
            }
            return {}, "catastrophic_trip_paused"

        if self._tripped and self._cooldown_remaining > 0:
            remaining_before = self._cooldown_remaining
            self._cooldown_remaining -= 1
            if self._cooldown_remaining <= 0:
                self._tripped = False
            controller_meta["safety_override"] = {
                "active": True, "reason": "catastrophic_trip_cooldown",
                "recovery_parameters": {}, "residual_limit": self._residual_limit,
                "growth_ratio_limit": self._growth_ratio,
                "cooldown_cycles_remaining": self._cooldown_remaining,
                "cooldown_cycles_total": self._cooldown_cycles,
                "cooldown_cycle_index": remaining_before,
            }
            return {}, "catastrophic_trip_cooldown"

        if self._tripped and not self._pause_after_trip:
            self._tripped = False

        latest_residual = safe_float(rl_observation.get("max_residual"))
        previous_residual = safe_float(window[-2].get("max_residual")) if len(window) >= 2 else None
        pressure_latest = safe_float(rl_observation.get("pressure_latest"))
        pressure_amplitude = safe_float(rl_observation.get("pressure_recent_amplitude"))
        previous_pressure = None
        if len(window) >= 2:
            previous_pressure = safe_float(
                window[-2].get(self._case.train_surface_pressure_report_name)
            )
        tv_cells = int(safe_float(rl_observation.get("turbulent_viscosity_limited_cells")) or 0)

        trip_reason: str | None = None
        if latest_residual is None:
            trip_reason = "missing_max_residual"
        elif latest_residual > self._residual_limit:
            trip_reason = "residual_limit_exceeded"
        elif (
            previous_residual is not None and previous_residual > 0.0
            and latest_residual > max(
                previous_residual * self._growth_ratio,
                self._residual_limit * 0.1,
            )
        ):
            trip_reason = "residual_growth_ratio_exceeded"
        elif pressure_latest is not None and pressure_latest > self._pressure_limit:
            trip_reason = "pressure_limit_exceeded"
        elif pressure_amplitude is not None and pressure_amplitude > self._pressure_amp_limit:
            trip_reason = "pressure_amplitude_limit_exceeded"
        elif (
            pressure_latest is not None and previous_pressure is not None
            and previous_pressure > 0.0
            and pressure_latest > previous_pressure * self._pressure_growth_ratio
        ):
            trip_reason = "pressure_growth_ratio_exceeded"
        elif tv_cells > self._tv_cells_limit:
            trip_reason = "turbulent_viscosity_limit_exceeded"

        if trip_reason is None:
            return ai_params, None

        self._tripped = True
        self._cooldown_remaining = (
            0 if self._pause_after_trip else self._cooldown_cycles
        )
        safe_recovery = self._build_recovery(current_values)
        controller_meta["safety_override"] = {
            "active": True, "reason": trip_reason,
            "latest_residual": rounded_or_none(latest_residual),
            "previous_residual": rounded_or_none(previous_residual),
            "pressure_latest": rounded_or_none(pressure_latest),
            "pressure_recent_amplitude": rounded_or_none(pressure_amplitude),
            "previous_pressure_latest": rounded_or_none(previous_pressure),
            "turbulent_viscosity_limited_cells": tv_cells,
            "residual_limit": self._residual_limit,
            "growth_ratio_limit": self._growth_ratio,
            "pressure_limit": self._pressure_limit,
            "pressure_amplitude_limit": self._pressure_amp_limit,
            "pressure_growth_ratio_limit": self._pressure_growth_ratio,
            "turbulent_viscosity_limited_cells_limit": self._tv_cells_limit,
            "recovery_parameters": safe_recovery,
            "pause_after_trip": self._pause_after_trip,
            "cooldown_cycles_total": self._cooldown_cycles,
            "cooldown_cycles_remaining": self._cooldown_remaining,
        }
        return safe_recovery, trip_reason

    def check_pressure_block(
        self,
        ai_params: dict[str, Any],
        controller_meta: dict[str, Any],
        rl_observation: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        """Block unsafe pressure_relaxation_factor increases when unstable."""
        if not self._block_prf_on_instability or "pressure_relaxation_factor" not in ai_params:
            return ai_params, None

        current_parameters = controller_meta.get("current_parameters", {})
        cur_prf = safe_float(current_parameters.get("pressure_relaxation_factor"))
        new_prf = safe_float(ai_params.get("pressure_relaxation_factor"))
        if cur_prf is None or new_prf is None or new_prf <= cur_prf:
            return ai_params, None

        latest_residual = safe_float(rl_observation.get("max_residual"))
        pressure_latest = safe_float(rl_observation.get("pressure_latest"))
        pressure_amplitude = safe_float(rl_observation.get("pressure_recent_amplitude"))
        tv_cells = int(safe_float(rl_observation.get("turbulent_viscosity_limited_cells")) or 0)
        unstable = (
            bool(rl_observation.get("residual_rebounded"))
            or bool(rl_observation.get("residual_oscillating"))
            or (latest_residual is not None and latest_residual > self._block_prf_residual_limit)
            or (pressure_latest is not None and pressure_latest > self._block_prf_pressure_limit)
            or (pressure_amplitude is not None and pressure_amplitude > self._block_prf_pressure_amp_limit)
            or tv_cells > self._block_prf_tv_cells_limit
        )
        if not unstable:
            return ai_params, None

        blocked = {k: v for k, v in ai_params.items() if k != "pressure_relaxation_factor"}
        reason = "blocked_pressure_relaxation_factor_up_on_instability"
        controller_meta["safety_override"] = {
            "active": True, "reason": reason,
            "latest_residual": rounded_or_none(latest_residual),
            "pressure_latest": rounded_or_none(pressure_latest),
            "pressure_recent_amplitude": rounded_or_none(pressure_amplitude),
            "turbulent_viscosity_limited_cells": tv_cells,
            "residual_limit": self._block_prf_residual_limit,
            "pressure_limit": self._block_prf_pressure_limit,
            "pressure_amplitude_limit": self._block_prf_pressure_amp_limit,
            "turbulent_viscosity_limited_cells_limit": self._block_prf_tv_cells_limit,
            "recovery_parameters": blocked,
            "pause_after_trip": False,
        }
        return blocked, reason

    def _build_recovery(self, current_values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: baseline
            for key, baseline in self._baseline_rl_values.items()
            if current_values.get(key) != baseline
        }
