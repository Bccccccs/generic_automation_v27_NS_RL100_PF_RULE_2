from __future__ import annotations

import math
from typing import Any

from generic_automation.core.runtime_value_utils import mean, safe_float, safe_int


def derive_relaxation_scheme(
    initial_value: Any,
    start_iteration: Any,
    end_iteration: Any,
    current_value: Any | None = None,
) -> str | None:
    initial = safe_float(initial_value)
    start = safe_int(start_iteration)
    end = safe_int(end_iteration)
    current = safe_float(current_value)
    if initial is not None and start is not None and end is not None:
        return "linear_ramp"
    if current is not None:
        return "constant_urf"
    return None


def recent_residuals(
    window: list[dict[str, Any]],
    lookback: int,
    field_name: str = "max_residual",
) -> list[float]:
    residuals: list[float] = []
    for row in window[-lookback:]:
        value = safe_float(row.get(field_name))
        if value is None or value <= 0.0:
            continue
        residuals.append(value)
    return residuals


def residual_log_slope(
    window: list[dict[str, Any]],
    lookback: int,
    field_name: str = "max_residual",
) -> float | None:
    return residual_log_slope_for_key(
        window,
        field_name=field_name,
        lookback=lookback,
    )


def residual_log_slope_for_key(
    window: list[dict[str, Any]],
    field_name: str,
    lookback: int,
) -> float | None:
    samples: list[tuple[float, float]] = []
    for row in window[-lookback:]:
        iteration = safe_float(row.get("iteration"))
        residual = safe_float(row.get(field_name))
        if iteration is None or residual is None or residual <= 0.0:
            continue
        samples.append((iteration, math.log10(residual)))

    if len(samples) < 2:
        return None

    xs = [item[0] for item in samples]
    ys = [item[1] for item in samples]
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in samples)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator <= 0.0:
        return None
    return numerator / denominator


def residual_diagnostics(
    window: list[dict[str, Any]],
    lookback: int,
    *,
    field_name: str = "max_residual",
    stagnation_abs_slope_threshold: float = 0.001,
) -> dict[str, bool]:
    residuals = recent_residuals(
        window,
        lookback=lookback,
        field_name=field_name,
    )
    if len(residuals) < 3:
        return {
            "rebounded": False,
            "oscillating": False,
            "stagnating": False,
        }

    log_residuals = [math.log10(value) for value in residuals]
    slope = residual_log_slope(
        window,
        lookback=lookback,
        field_name=field_name,
    )
    previous_best = min(log_residuals[:-1]) if len(log_residuals) > 1 else log_residuals[-1]
    rebounded = (
        len(log_residuals) >= 2
        and log_residuals[-1] > log_residuals[-2]
        and log_residuals[-1] > previous_best + 0.10
    )

    sign_changes = 0
    previous_sign = 0
    for diff in (
        log_residuals[idx] - log_residuals[idx - 1]
        for idx in range(1, len(log_residuals))
    ):
        if abs(diff) < 1.0e-3:
            continue
        current_sign = 1 if diff > 0.0 else -1
        if previous_sign and current_sign != previous_sign:
            sign_changes += 1
        previous_sign = current_sign
    oscillating = sign_changes >= 2 and (max(log_residuals) - min(log_residuals)) > 0.05

    stagnating = slope is not None and abs(slope) < stagnation_abs_slope_threshold

    return {
        "rebounded": rebounded,
        "oscillating": oscillating,
        "stagnating": stagnating,
    }
