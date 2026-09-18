"""生成的物理时间激励计划 CSV 文件的验证工具。

验证规则：
  1. 列顺序必须与 B02 统一格式一致
  2. 至少包含一个窗口
  3. window_id 必须保持不变或连续递增（每次最多增加 1）
  4. time 必须等于 t_start（仍可读取旧表头 physical_time）
  5. t_end 必须大于 t_start
  6. 时间窗口必须连续（t_start 等于上一行的 t_end）
  7. JET_NN 值只能是 0 或 1
  8. JET=0 时对应的 massflow 必须为 0
  9. JET=1 时对应的 massflow 必须 > 0
  10. 活跃喷口数和总质量流量不超过上限
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..excitation_patterns.common import MASSFLOW_COLUMNS
from ..data_schema import JET_COLUMNS
from ..sampling import (
    ACTUATION_TIME_COLUMN,
    LEGACY_ACTUATION_TIME_COLUMN,
    actuation_time_value,
)


def validate_actuation_schedule_csv(
    path: str | Path,
    *,
    n_jets: int = 24,
    max_active_jets: int | None = None,
    max_total_mass_flow: float | None = None,
) -> list[str]:
    """验证统一格式的 actuation_schedule.csv。

    Args:
        path: CSV 文件路径。
        n_jets: 喷口数量（默认 24）。
        max_active_jets: 每窗口最大允许活跃喷口数。
        max_total_mass_flow: 每窗口最大允许总质量流量。

    Returns:
        错误字符串列表，空列表表示验证通过。
    """
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])

    jet_columns = list(JET_COLUMNS[:n_jets])
    massflow_columns = list(MASSFLOW_COLUMNS[:n_jets])
    expected = [ACTUATION_TIME_COLUMN, "window_id", "t_start", "t_end", *jet_columns, *massflow_columns]
    legacy_expected = [LEGACY_ACTUATION_TIME_COLUMN, *expected[1:]]
    errors: list[str] = []
    if header not in (expected, legacy_expected):
        errors.append("actuation_schedule.csv columns do not match the unified B02 format")
    if not rows:
        errors.append("actuation_schedule.csv must contain at least one window")
        return errors

    previous_window_id: int | None = None
    previous_t_end: float | None = None
    for row_idx, row in enumerate(rows):
        try:
            window_id = int(row["window_id"])
            schedule_time = float(actuation_time_value(row))
            t_start = float(row["t_start"])
            t_end = float(row["t_end"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"row {row_idx} has invalid time/window fields: {exc}")
            continue
        if previous_window_id is not None and window_id not in {
            previous_window_id,
            previous_window_id + 1,
        }:
            errors.append(
                f"row {row_idx} window_id must stay unchanged or increase by 1"
            )
        if abs(schedule_time - t_start) > 1e-12:
            errors.append(f"row {row_idx} time must equal t_start")
        if t_end <= t_start:
            errors.append(f"row {row_idx} t_end must be greater than t_start")
        if previous_t_end is not None and abs(t_start - previous_t_end) > 1e-12:
            errors.append(f"row {row_idx} t_start must equal previous t_end")

        active = 0
        total_mass_flow = 0.0
        for jet_column, massflow_column in zip(jet_columns, massflow_columns):
            try:
                jet_value = int(float(row[jet_column]))
                massflow_value = float(row[massflow_column])
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"row {row_idx} has invalid {jet_column}/{massflow_column}: {exc}")
                continue
            if jet_value not in (0, 1):
                errors.append(f"row {row_idx} {jet_column} must be 0 or 1")
            if jet_value == 0 and abs(massflow_value) > 1e-12:
                errors.append(f"row {row_idx} {jet_column}=0 requires {massflow_column}=0")
            if jet_value == 1 and massflow_value <= 0:
                errors.append(f"row {row_idx} {jet_column}=1 requires {massflow_column}>0")
            active += jet_value
            total_mass_flow += massflow_value
        if max_active_jets is not None and active > max_active_jets:
            errors.append(f"row {row_idx} active jets {active} exceed {max_active_jets}")
        if max_total_mass_flow is not None and total_mass_flow > max_total_mass_flow + 1e-12:
            errors.append(
                f"row {row_idx} total mass flow {total_mass_flow} exceeds {max_total_mass_flow}"
            )
        previous_window_id = window_id
        previous_t_end = t_end
    return errors
