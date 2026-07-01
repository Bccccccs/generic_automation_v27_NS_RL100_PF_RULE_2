from __future__ import annotations

import logging
from typing import Any

from adapter_base import Case
from ai_case_modifier import AICaseModifier
from rl_controller import ReinforcementLearningController
from rl_manual_rules import ManualInterventionRules
from rl_runtime_registry import RL_RUNTIME_PARAMETER_NAMES, rl_runtime_parameter_snapshot
from rl_safety_override import RLSafetyOverride
from runtime_value_utils import as_bool, rounded_or_none as _rounded_or_none, safe_float as _safe_float

log = logging.getLogger(__name__)


class RLRuntimeService:
    def __init__(
        self,
        rl_config: dict[str, Any],
        case: Case,
        modifier: AICaseModifier,
        controller: ReinforcementLearningController | None,
        decision_interval: int,
    ) -> None:
        self._case = case
        self._modifier = modifier
        self._controller = controller
        self._last_attempt_iter = -max(1, decision_interval)
        self._attempt_count = 0
        self._trigger_count = 0
        self._intervention_enabled = as_bool(rl_config.get("intervention_enabled", False))
        self._last_update_result: dict[str, Any] = {}
        self._pending_action_id: str | None = None

        self._baseline_rl_values = {
            key: getattr(case, key)
            for key in RL_RUNTIME_PARAMETER_NAMES
            if hasattr(case, key)
        }
        self._safety = RLSafetyOverride(rl_config, case, self._baseline_rl_values)
        self._manual_rules = ManualInterventionRules(rl_config, case)

    # --- public properties ---

    @property
    def trigger_count(self) -> int:
        return self._trigger_count

    @property
    def attempt_count(self) -> int:
        return self._attempt_count

    @property
    def last_attempt_iter(self) -> int:
        return self._last_attempt_iter

    @property
    def last_update_result(self) -> dict[str, Any]:
        return self._last_update_result

    @property
    def intervention_enabled(self) -> bool:
        return self._intervention_enabled

    @property
    def pending_action_id(self) -> str | None:
        return self._pending_action_id

    # --- public methods ---

    def consume_action_ack_events(self, ack_events: list[dict[str, Any]]) -> None:
        for ack in ack_events:
            self._handle_action_ack(ack)

    def perform_update(
        self,
        current_iter: int,
        latest_obs: dict[str, Any],
        window: list[dict[str, Any]],
        current_values: dict[str, Any],
    ) -> None:
        self._last_attempt_iter = current_iter
        self._attempt_count += 1
        rl_values_before = self._rl_parameter_snapshot(current_values)

        pending_action_id = self._refresh_pending_action()
        if pending_action_id is not None:
            self._set_update_result(
                action_id=pending_action_id,
                controller_proposed_changes={},
                applied_changes={},
                apply_success=False,
                blocked_reason="awaiting_action_ack",
                ack_status="pending",
                decision_emitted=False,
            )
            log.info(
                "[RLRuntime] previous action still awaiting ack; skip new proposal at iter=%d action_id=%s",
                current_iter, pending_action_id,
            )
            return

        ai_params, observations = self._run_rl_request(current_iter, latest_obs, current_values, window)
        controller_meta = observations.get("controller_meta", {})
        controller_proposed_changes: dict[str, Any] = controller_meta.get("parameter_changes", {})
        selected_action = str(controller_meta.get("action", "") or "")

        ai_params, safety_reason = self._apply_safety_and_rules(ai_params, observations, current_values, window)
        reward_info = controller_meta.get("reward")
        if reward_info:
            log.debug("[RLRuntime] reward breakdown: %s", reward_info)

        if not ai_params:
            log.info(
                "[RLRuntime] no parameter changes proposed; rl_values=%s reward=%s safety_reason=%s",
                rl_values_before, reward_info, safety_reason,
            )
            blocked_reason = safety_reason or "no_proposal"
            self._set_update_result(
                controller_proposed_changes=controller_proposed_changes,
                applied_changes={},
                apply_success=False,
                blocked_reason=blocked_reason,
                decision_emitted=True,
            )
            self._record_last_action_outcome(
                applied=(selected_action == ReinforcementLearningController.ACTION_HOLD),
                reason="hold" if selected_action == ReinforcementLearningController.ACTION_HOLD else blocked_reason,
            )
            return

        if not self._intervention_enabled:
            log.info(
                "[RLRuntime] observe-only; proposal not applied. before=%s proposal=%s",
                rl_values_before, ai_params,
            )
            self._set_update_result(
                controller_proposed_changes=controller_proposed_changes,
                applied_changes={},
                apply_success=False,
                blocked_reason="intervention_disabled",
                decision_emitted=True,
                intervention_enabled=False,
            )
            self._record_last_action_outcome(applied=False, reason="intervention_disabled")
            return

        applied = self._modifier.apply(
            ai_params, current_values=current_values,
            observations=observations, trigger_iteration=current_iter,
        )
        action_id = self._refresh_pending_action() or ""
        if action_id:
            controller_meta["action_id"] = action_id
        self._sync_case_values(applied)

        if not applied:
            self._set_update_result(
                controller_proposed_changes=controller_proposed_changes,
                applied_changes={},
                apply_success=False,
                blocked_reason="no_effective_change",
                action_id=action_id or None,
                ack_status=None,
                decision_emitted=True,
            )
            self._record_last_action_outcome(applied=False, reason="no_effective_change")
            return

        applied_changes = self._build_applied_changes(current_values, applied)
        self._set_update_result(
            controller_proposed_changes=controller_proposed_changes,
            applied_changes=applied_changes,
            apply_success=True,
            blocked_reason=None,
            action_id=action_id or None,
            ack_status="pending",
            decision_emitted=True,
        )
        self._trigger_count += 1
        rl_values_after = {**rl_values_before, **self._rl_parameter_snapshot(applied)}
        log.info(
            "[RLRuntime] update #%d queued: before=%s proposal=%s applied=%s after=%s action_id=%s",
            self._trigger_count, rl_values_before, ai_params, applied, rl_values_after,
            action_id or "<none>",
        )

    # --- private helpers ---

    def _set_update_result(self, **fields: Any) -> None:
        payload = {"intervention_enabled": self._intervention_enabled}
        payload.update(fields)
        self._last_update_result = payload

    def _refresh_pending_action(self) -> str | None:
        pending_action = self._modifier.peek_pending_action()
        if pending_action is None:
            self._pending_action_id = None
            return None
        action_id = str(pending_action.get("action_id", "") or "").strip() or None
        self._pending_action_id = action_id
        return action_id

    def _build_applied_changes(
        self,
        current_values: dict[str, Any],
        applied: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return {
            param: {"before": current_values.get(param), "after": new_val}
            for param, new_val in applied.items()
        }

    def _handle_action_ack(self, ack: dict[str, Any]) -> None:
        action_id = str(ack.get("action_id", "") or "").strip()
        if not action_id:
            return
        ack_status = str(ack.get("status", "") or "acknowledged").strip().lower()
        apply_result = str(ack.get("apply_result", ack_status) or ack_status).strip().lower()
        action_applied = apply_result in {"applied", "partial", "consumed", "acknowledged"}
        reason = None if action_applied else (ack_status or "ack_failed")
        if self._pending_action_id == action_id:
            self._record_last_action_outcome(applied=action_applied, reason=reason)
            self._pending_action_id = None
            self._modifier.clear_pending_action(action_id)
        if self._last_update_result.get("action_id") == action_id:
            self._last_update_result["ack_status"] = ack_status
            self._last_update_result["acknowledged_at"] = ack.get("acknowledged_at")
            self._last_update_result["ack_payload"] = ack
            if not action_applied and not self._last_update_result.get("blocked_reason"):
                self._last_update_result["blocked_reason"] = reason

    def _run_rl_request(
        self,
        current_iter: int,
        latest_obs: dict[str, Any],
        current_values: dict[str, Any],
        window: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._controller is None:
            raise RuntimeError("reinforcement learning controller was not initialized")
        ai_params, controller_meta = self._controller.suggest(
            window=window, current_values=current_values,
            constraints=self._modifier.constraints, trigger_iteration=current_iter,
        )
        observations = {k: v for k, v in latest_obs.items() if not str(k).startswith("_")}
        observations["rl_observation"] = controller_meta.get("observation", {})
        observations["controller_meta"] = controller_meta
        return ai_params, observations

    def _apply_safety_and_rules(
        self,
        ai_params: dict[str, Any],
        observations: dict[str, Any],
        current_values: dict[str, Any],
        window: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        controller_meta = observations.setdefault("controller_meta", {})
        rl_observation = observations.get("rl_observation", {}) or {}

        # 1. catastrophic trip check
        ai_params, trip_reason = self._safety.check_trip(
            ai_params, controller_meta, rl_observation, current_values, window
        )
        if trip_reason is not None:
            return ai_params, trip_reason

        # 2. unstable pressure relaxation block
        ai_params, block_reason = self._safety.check_pressure_block(
            ai_params, controller_meta, rl_observation
        )

        # 3. manual rules
        ai_params, guided_reason = self._manual_rules.apply(
            ai_params, controller_meta, rl_observation, current_values, window
        )

        if block_reason and guided_reason:
            return ai_params, f"{block_reason}+{guided_reason}"
        return ai_params, guided_reason or block_reason

    def _sync_case_values(self, applied: dict[str, Any]) -> None:
        for key, value in applied.items():
            self._set_case_value(key, value)

    def _set_case_value(self, key: str, value: Any) -> None:
        if hasattr(self._case, key):
            setattr(self._case, key, value)

        if key == "amg_cycle":
            for alias in ("pressure_amg_cycle", "velocity_amg_cycle"):
                if hasattr(self._case, alias):
                    setattr(self._case, alias, value)
            if hasattr(self._case, "amg_solver"):
                setattr(self._case, "amg_solver", value)
            return

        if key in {"pressure_amg_cycle", "velocity_amg_cycle"}:
            if hasattr(self._case, "amg_cycle"):
                setattr(self._case, "amg_cycle", value)
            if hasattr(self._case, "amg_solver"):
                setattr(self._case, "amg_solver", value)
            return

        if key == "amg_solver" and hasattr(self._case, "amg_cycle"):
            setattr(self._case, "amg_cycle", value)
            for alias in ("pressure_amg_cycle", "velocity_amg_cycle"):
                if hasattr(self._case, alias):
                    setattr(self._case, alias, value)

    def _record_last_action_outcome(self, applied: bool, reason: str | None = None) -> None:
        if self._controller is not None:
            self._controller.mark_last_action_applied(applied=applied, reason=reason)

    def _rl_parameter_snapshot(self, values: dict[str, Any]) -> dict[str, Any]:
        return rl_runtime_parameter_snapshot(values)
