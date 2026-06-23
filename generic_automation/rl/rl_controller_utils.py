from __future__ import annotations

from typing import Any

from generic_automation.core.runtime_value_utils import safe_float


def clip(value: float, lo: float, hi: float) -> float:
    return min(max(value, lo), hi)


def resolve_allowed_parameter_names(allowed_parameters: Any) -> set[str]:
    if not allowed_parameters:
        return set()
    if isinstance(allowed_parameters, str):
        return {item.strip() for item in allowed_parameters.split(",") if item.strip()}
    if isinstance(allowed_parameters, list):
        return {str(item).strip() for item in allowed_parameters if str(item).strip()}
    return set()


def resolve_allowed_actions(
    allowed_parameters: set[str],
    parameter_actions: dict[str, tuple[str, ...]],
    hold_action: str,
) -> set[str]:
    allowed_actions = {hold_action}
    for parameter_name in allowed_parameters:
        allowed_actions.update(parameter_actions.get(parameter_name, ()))
    return allowed_actions


def collect_metric_values(
    window: list[dict[str, Any]],
    metric_name: str,
) -> list[float]:
    values: list[float] = []
    for row in window:
        numeric_value = safe_float(row.get(metric_name))
        if numeric_value is None:
            continue
        values.append(numeric_value)
    return values


def bucketize_range(value: float, lo: float, hi: float) -> int:
    if hi <= lo:
        return 0
    normalized = (value - lo) / (hi - lo)
    clipped = min(max(normalized, 0.0), 1.0)
    return int(round(clipped * 4.0))


def parse_stage_baselines(raw_value: Any) -> dict[str, float]:
    parsed: dict[str, float] = {}
    if isinstance(raw_value, dict):
        for key, value in raw_value.items():
            numeric = safe_float(value)
            if numeric is None or numeric <= 0.0:
                continue
            parsed[str(key).strip()] = numeric
        return parsed

    if isinstance(raw_value, list):
        for item in raw_value:
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage", "")).strip()
            speed = safe_float(item.get("speed"))
            if not stage or speed is None or speed <= 0.0:
                continue
            parsed[stage] = speed
    return parsed


def parameter_changes(
    before: dict[str, Any],
    after: dict[str, Any],
    keys: set[str] | tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for key in sorted(keys):
        before_value = before.get(key)
        after_value = after.get(key)
        if before_value == after_value:
            continue
        record: dict[str, Any] = {
            "before": before_value,
            "after": after_value,
        }
        before_numeric = safe_float(before_value)
        after_numeric = safe_float(after_value)
        if before_numeric is not None and after_numeric is not None:
            delta = after_numeric - before_numeric
            if key.endswith("_iteration") or key.endswith("_cycle"):
                record["delta"] = int(round(delta))
            else:
                record["delta"] = round(delta, 6)
        changes[key] = record
    return changes
