from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_value_utils import safe_float


def load_controller_state(
    state_path: Path,
    *,
    actions: tuple[str, ...],
    logger: logging.Logger,
) -> dict[str, Any]:
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("RL 状态文件读取失败，已忽略：%s", exc)
        return {}

    parsed: dict[str, Any] = {}

    q_table = payload.get("q_table", {})
    if isinstance(q_table, dict):
        parsed["_q_table"] = {
            str(state): {
                str(action): float(value)
                for action, value in actions_map.items()
                if str(action) in actions
            }
            for state, actions_map in q_table.items()
            if isinstance(actions_map, dict)
        }

    visit_counts = payload.get("visit_counts", {})
    if isinstance(visit_counts, dict):
        parsed["_visit_counts"] = {
            str(key): int(value) for key, value in visit_counts.items()
        }

    previous_state = payload.get("previous_state")
    parsed["_previous_state"] = (
        str(previous_state) if previous_state not in (None, "") else None
    )

    previous_action = payload.get("previous_action")
    parsed["_previous_action"] = (
        str(previous_action) if previous_action in actions else None
    )

    parsed["_previous_action_applied"] = bool(payload.get("previous_action_applied"))

    previous_observation = payload.get("previous_observation")
    if isinstance(previous_observation, dict):
        parsed["_previous_observation"] = previous_observation

    epsilon = payload.get("epsilon")
    if epsilon not in (None, ""):
        try:
            parsed["_epsilon"] = float(epsilon)
        except (TypeError, ValueError):
            pass

    adaptive_stage_baselines = payload.get("adaptive_stage_baselines")
    if isinstance(adaptive_stage_baselines, dict):
        parsed["_adaptive_stage_baselines"] = {
            str(key): float(value)
            for key, value in adaptive_stage_baselines.items()
            if safe_float(value) is not None
        }

    adaptive_stage_counts = payload.get("adaptive_stage_counts")
    if isinstance(adaptive_stage_counts, dict):
        parsed["_adaptive_stage_counts"] = {
            str(key): int(value)
            for key, value in adaptive_stage_counts.items()
            if safe_float(value) is not None
        }

    return parsed


def save_controller_state(state_path: Path, payload: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def append_controller_trace(
    trace_path: Path,
    *,
    trigger_iteration: int | None,
    metadata: dict[str, Any],
    proposal: dict[str, Any],
) -> None:
    record = {
        "timestamp": datetime.now().isoformat(),
        "trigger_iteration": trigger_iteration,
        "metadata": metadata,
        "proposal": proposal,
    }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
