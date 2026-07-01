from __future__ import annotations

import json
import math
from typing import Any


def safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def safe_int(value: Any) -> int | None:
    numeric = safe_float(value)
    if numeric is None:
        return None
    return int(round(numeric))


def rounded_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 6)


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return text


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def range_amplitude(values: list[float]) -> float:
    if not values:
        return 0.0
    return max(values) - min(values)


def first_finite_float(*values: Any) -> float | None:
    for value in values:
        numeric = safe_float(value)
        if numeric is not None:
            return numeric
    return None


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def binary_cycle_choice(value: Any, default: int) -> int:
    numeric = safe_float(value)
    if numeric is None:
        return int(default)
    return 1 if numeric >= 0.5 else 0


def coerce_slope_bucket_value(value: float | None) -> float:
    numeric = safe_float(value)
    if numeric is None:
        return 0.0
    return numeric


def to_csv_scalar(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value
