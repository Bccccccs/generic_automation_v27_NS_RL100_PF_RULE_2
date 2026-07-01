from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_metadata import (
    ACTION_ACK_LOG_FILE as ACTION_ACK_LOG_FILE_NAME,
    PARAM_ACK_FILE as PARAM_ACK_FILE_NAME,
    PARAM_UPDATE_FILE as PARAM_UPDATE_FILE_NAME,
    PENDING_ACTION_FILE as PENDING_ACTION_FILE_NAME,
    PROTOCOL_VERSION,
    read_json,
    utc_now_iso,
    write_json,
)

log = logging.getLogger(__name__)

_MODIFIABLE_STEADY: frozenset[str] = frozenset(
    {
        "inlet_velocity",
        "inlet_temperature",
        "outlet_pressure",
        "inlet_turbulence_intensity",
        "inlet_turbulent_length_scale",
        "max_iterations",
        "convergence_residual",
        "pressure_relaxation_factor",
        "pressure_relaxation_initial_value",
        "pressure_relaxation_start_iteration",
        "pressure_relaxation_end_iteration",
        "velocity_relaxation_initial_value",
        "velocity_relaxation_start_iteration",
        "velocity_relaxation_end_iteration",
        "pressure_amg_cycle",
        "velocity_amg_cycle",
        "amg_cycle",
        "amg_solver",
        "pressure_amg_max_cycles",
        "pressure_amg_converge_tol",
        "pressure_amg_epsilon",
    }
)

_MODIFIABLE_TRANSIENT: frozenset[str] = _MODIFIABLE_STEADY | frozenset({"time_step"})

_FIELD_TYPES: dict[str, type] = {
    "inlet_velocity": float,
    "inlet_temperature": float,
    "outlet_pressure": float,
    "inlet_turbulence_intensity": float,
    "inlet_turbulent_length_scale": float,
    "max_iterations": int,
    "convergence_residual": float,
    "pressure_relaxation_factor": float,
    "pressure_relaxation_initial_value": float,
    "pressure_relaxation_start_iteration": int,
    "pressure_relaxation_end_iteration": int,
    "velocity_relaxation_initial_value": float,
    "velocity_relaxation_start_iteration": int,
    "velocity_relaxation_end_iteration": int,
    "pressure_amg_cycle": int,
    "velocity_amg_cycle": int,
    "amg_cycle": int,
    "amg_solver": int,
    "pressure_amg_max_cycles": int,
    "pressure_amg_converge_tol": float,
    "pressure_amg_epsilon": float,
    "time_step": float,
}

_REASON_IMMUTABLE = "immutable: requires remesh or restart"
_REASON_UNKNOWN = "unknown parameter"
_REASON_WRONG_SIM_TYPE = "not applicable for this simulation_type"


class AICaseModifier:
    PARAM_UPDATE_FILE = PARAM_UPDATE_FILE_NAME
    PARAM_ACK_FILE = PARAM_ACK_FILE_NAME
    PENDING_ACTION_FILE = PENDING_ACTION_FILE_NAME
    ACTION_ACK_LOG_FILE = ACTION_ACK_LOG_FILE_NAME
    HISTORY_FILE = "rl/ai_update_history.jsonl"

    def __init__(
        self,
        case_dir: Path,
        sim_type: str,
        constraints: dict[str, Any] | None = None,
        run_context: dict[str, Any] | None = None,
    ) -> None:
        self.case_dir = case_dir
        self.sim_type = sim_type.lower()
        self.constraints: dict[str, Any] = constraints or {}
        self._whitelist = (
            _MODIFIABLE_TRANSIENT if self.sim_type == "transient" else _MODIFIABLE_STEADY
        )
        self._run_context = run_context or {}
        self._run_id = str(self._run_context.get("run_id", "") or "")
        self._ack_log_path = self.case_dir / self.ACTION_ACK_LOG_FILE
        self._ack_log_pos = self._ack_log_path.stat().st_size if self._ack_log_path.exists() else 0

    def apply(
        self,
        ai_params: dict[str, Any],
        current_values: dict[str, Any] | None = None,
        observations: dict[str, Any] | None = None,
        trigger_iteration: int | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        applied: dict[str, Any] = {}
        rejected: dict[str, str] = {}
        clamped: dict[str, dict[str, Any]] = {}

        for param, value in ai_params.items():
            reject_reason = self._check_whitelist(param)
            if reject_reason:
                rejected[param] = reject_reason
                log.warning("AI 参数 %s 被拒绝：%s", param, reject_reason)
                continue

            try:
                value = self._coerce_value(param, value)
            except (ValueError, TypeError) as exc:
                rejected[param] = f"type coercion failed: {exc}"
                log.warning("AI 参数 %s 类型转换失败：%s", param, exc)
                continue

            original = value
            value, was_clamped, clamp_info = self._clamp(param, value)
            if was_clamped:
                clamped[param] = clamp_info
                log.warning(
                    "AI 参数 %s 超出约束，裁剪：%s → %s",
                    param, original, value,
                )

            applied[param] = value

        if current_values:
            unchanged: list[str] = []
            for param, value in list(applied.items()):
                if param not in current_values:
                    continue
                current_value = current_values[param]
                if value == current_value:
                    unchanged.append(param)
                    applied.pop(param, None)

            if unchanged:
                log.info("AI 参数与当前值相同，跳过：%s", ", ".join(sorted(unchanged)))

        history_keys = sorted(set(ai_params) | set(applied) | set(rejected))
        current_values_before = {
            key: current_values[key]
            for key in history_keys
            if current_values and key in current_values
        }
        effective_values_after = dict(current_values_before)
        effective_values_after.update(applied)
        applied_changes = {
            key: {
                "before": current_values_before.get(key),
                "after": effective_values_after.get(key),
            }
            for key in applied
        }

        resolved_action_id = action_id
        if not applied:
            log.info("AI 参数经过滤后为空，不写入握手文件")
        else:
            resolved_action_id = action_id or self._build_action_id(trigger_iteration)
            self._write_param_update(
                applied,
                action_id=resolved_action_id,
                trigger_iteration=trigger_iteration,
                current_values_before=current_values_before,
                observations=observations or {},
            )
            log.info("握手文件已写入，参数：%s", applied)

        self._append_history(
            run_id=self._run_id,
            action_id=resolved_action_id,
            trigger_iteration=trigger_iteration,
            observations=observations or {},
            ai_suggested=ai_params,
            applied=applied,
            rejected=rejected,
            clamped=clamped,
            current_values_before=current_values_before,
            effective_values_after=effective_values_after,
            applied_changes=applied_changes,
        )

        return applied

    def peek_pending_action(self) -> dict[str, Any] | None:
        payload = read_json(self.case_dir / self.PENDING_ACTION_FILE, default=None)
        if not isinstance(payload, dict):
            return None
        if str(payload.get("status", "pending")).strip().lower() != "pending":
            return None
        return payload

    def has_pending_action(self) -> bool:
        return self.peek_pending_action() is not None

    def clear_pending_action(self, action_id: str | None = None) -> None:
        pending_path = self.case_dir / self.PENDING_ACTION_FILE
        pending = self.peek_pending_action()
        if pending is None:
            try:
                pending_path.unlink()
            except FileNotFoundError:
                pass
            return
        if action_id and str(pending.get("action_id", "") or "") != action_id:
            return
        try:
            pending_path.unlink()
        except FileNotFoundError:
            pass

    def read_new_ack_events(self) -> list[dict[str, Any]]:
        if not self._ack_log_path.exists():
            return []
        try:
            current_size = self._ack_log_path.stat().st_size
            if current_size < self._ack_log_pos:
                self._ack_log_pos = 0
            with self._ack_log_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._ack_log_pos)
                lines = handle.readlines()
                self._ack_log_pos = handle.tell()
        except OSError as exc:
            log.debug("[Modifier] failed to read ack log: %s", exc)
            return []

        events: list[dict[str, Any]] = []
        for raw_line in lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                log.debug("[Modifier] invalid ack line ignored: %s", raw_line)
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _check_whitelist(self, param: str) -> str:
        if param not in _FIELD_TYPES:
            return _REASON_UNKNOWN
        if param not in self._whitelist:
            if param in _MODIFIABLE_TRANSIENT and self.sim_type == "steady":
                return _REASON_WRONG_SIM_TYPE
            return _REASON_IMMUTABLE
        return ""

    def _clamp(
        self, param: str, value: Any
    ) -> tuple[Any, bool, dict[str, Any]]:
        if param not in self.constraints:
            return value, False, {}

        constraint = self.constraints[param]
        lo = constraint.get("min")
        hi = constraint.get("max")
        original = value
        was_clamped = False

        if lo is not None and value < lo:
            value = self._coerce_value(param, lo)
            was_clamped = True
        if hi is not None and value > hi:
            value = self._coerce_value(param, hi)
            was_clamped = True

        if was_clamped:
            return value, True, {"original": original, "clamped_to": value}
        return value, False, {}

    def _coerce_value(self, param: str, value: Any) -> Any:
        if param == "amg_solver":
            return 1 if float(value) >= 0.5 else 0
        if param in {
            "amg_cycle",
            "pressure_amg_cycle",
            "velocity_amg_cycle",
            "pressure_relaxation_start_iteration",
            "pressure_relaxation_end_iteration",
            "velocity_relaxation_start_iteration",
            "velocity_relaxation_end_iteration",
            "pressure_amg_max_cycles",
        }:
            return int(round(float(value)))
        if _FIELD_TYPES[param] is int:
            return int(round(float(value)))
        return _FIELD_TYPES[param](value)

    def _build_action_id(self, trigger_iteration: int | None) -> str:
        iteration_part = "na" if trigger_iteration is None else str(int(trigger_iteration))
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        run_part = self._run_id or self.case_dir.name
        return f"{run_part}:iter{iteration_part}:{timestamp}"

    def _write_param_update(
        self,
        params: dict[str, Any],
        *,
        action_id: str,
        trigger_iteration: int | None,
        current_values_before: dict[str, Any],
        observations: dict[str, Any],
    ) -> None:
        self.case_dir.mkdir(parents=True, exist_ok=True)
        target = self.case_dir / self.PARAM_UPDATE_FILE
        tmp = self.case_dir / (self.PARAM_UPDATE_FILE + ".tmp")
        request_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": self._run_id or None,
            "action_id": action_id,
            "status": "pending",
            "source": "python_monitor",
            "created_at": utc_now_iso(),
            "trigger_iteration": trigger_iteration,
            "requested_changes": params,
        }
        log.debug("[Modifier] writing handshake file: %s", target.resolve())
        tmp.write_text(json.dumps(request_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        write_json(
            self.case_dir / self.PENDING_ACTION_FILE,
            {
                **request_payload,
                "current_values_before": current_values_before,
                "observations": observations,
            },
        )
        log.debug("[Modifier] handshake file written: %s", target.resolve())

    def _append_history(
        self,
        run_id: str,
        action_id: str | None,
        trigger_iteration: int | None,
        observations: dict[str, Any],
        ai_suggested: dict[str, Any],
        applied: dict[str, Any],
        rejected: dict[str, str],
        clamped: dict[str, dict[str, Any]],
        current_values_before: dict[str, Any],
        effective_values_after: dict[str, Any],
        applied_changes: dict[str, dict[str, Any]],
    ) -> None:
        record: dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": run_id or None,
            "action_id": action_id,
            "timestamp": datetime.now().isoformat(),
            "trigger_iteration": trigger_iteration,
            "observations": observations,
            "ai_suggested": ai_suggested,
            "applied": applied,
            "rejected": rejected,
            "clamped": clamped,
            "current_values_before": current_values_before,
            "effective_values_after": effective_values_after,
            "applied_changes": applied_changes,
        }
        self.case_dir.mkdir(parents=True, exist_ok=True)
        history_path = self.case_dir / self.HISTORY_FILE
        log.debug(
            "[Modifier] appending history: path=%s before=%s applied=%s after=%s",
            history_path.resolve(),
            current_values_before,
            applied,
            effective_values_after,
        )
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.debug("[Modifier] history appended")
