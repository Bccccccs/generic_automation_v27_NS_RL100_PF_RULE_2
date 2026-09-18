"""Helpers for expanding control-window schedules to sample-level rows."""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml


ACTUATION_TIME_COLUMN = "time"
LEGACY_ACTUATION_TIME_COLUMN = "physical_time"


def actuation_time_column(row: dict[str, Any]) -> str:
    """Return the time-column name used by an actuation-schedule row.

    New schedules expose ``time`` to STAR/Haokun.  ``physical_time`` remains
    readable so existing schedules can still be replayed.
    """
    if ACTUATION_TIME_COLUMN in row:
        return ACTUATION_TIME_COLUMN
    if LEGACY_ACTUATION_TIME_COLUMN in row:
        return LEGACY_ACTUATION_TIME_COLUMN
    return ACTUATION_TIME_COLUMN


def actuation_time_value(row: dict[str, Any], default: Any = None) -> Any:
    """Read ``time`` from a schedule row, falling back to the legacy header."""
    for column in (ACTUATION_TIME_COLUMN, LEGACY_ACTUATION_TIME_COLUMN):
        value = row.get(column)
        if value not in (None, ""):
            return value
    return default


SAMPLE_OWNERSHIP_LEFT_CLOSED = "left_closed"
SAMPLE_OWNERSHIP_RIGHT_CLOSED = "right_closed"
SAMPLE_OWNERSHIP_EMBEDDED = "embedded"
SAMPLE_OWNERSHIP_AUTO = "auto"
SAMPLE_OWNERSHIP_MODES = (
    SAMPLE_OWNERSHIP_AUTO,
    SAMPLE_OWNERSHIP_LEFT_CLOSED,
    SAMPLE_OWNERSHIP_RIGHT_CLOSED,
    SAMPLE_OWNERSHIP_EMBEDDED,
)
# 与 B53 既有的 time_alignment_tolerance_s 保持一致，只用于吸收浮点噪声，
# 不得大到足以把一个真实位于边界另一侧的样本拨回来。
SCHEDULE_WINDOW_TOLERANCE_S = 1.0e-8

_LEFT_CLOSED_PROSE = "t_start<=t_sample<t_end"
_RIGHT_CLOSED_PROSE = "t_start<t_sample<=t_end"


class ScheduleWindowError(ValueError):
    """动作表窗口非法，或样本在声明的语义下无法归属任何窗口。"""


def normalize_sample_ownership(value: Any) -> str | None:
    """把 CLI/manifest 里的声明归一化为 ownership 模式，无法识别时返回 ``None``。

    仓库既有模板用不等式散文描述区间（见
    ``docs/week3/B02_case_manifest_template.yaml``），这里一并解析，避免把已
    声明的语义当成未声明。
    """
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    for mode in SAMPLE_OWNERSHIP_MODES:
        if text.startswith(mode):
            return mode
    compact = text.replace(" ", "")
    if _LEFT_CLOSED_PROSE in compact:
        return SAMPLE_OWNERSHIP_LEFT_CLOSED
    if _RIGHT_CLOSED_PROSE in compact:
        return SAMPLE_OWNERSHIP_RIGHT_CLOSED
    return None


def manifest_sample_ownership(manifest: Mapping[str, Any] | None) -> str | None:
    """读取 manifest 顶层或 ``actuation`` 块里的 sample ownership 声明。"""
    if not isinstance(manifest, Mapping):
        return None
    containers: list[Mapping[str, Any]] = [manifest]
    actuation = manifest.get("actuation")
    if isinstance(actuation, Mapping):
        containers.append(actuation)
    for container in containers:
        for key in ("sample_ownership_rule", "sample_ownership"):
            mode = normalize_sample_ownership(container.get(key))
            if mode is not None:
                return mode
    return None


def parse_schedule_windows(
    schedule_rows: Sequence[Mapping[str, Any]],
    *,
    tolerance_s: float = SCHEDULE_WINDOW_TOLERANCE_S,
) -> tuple[list[float], list[float]]:
    """解析并校验动作表窗口，返回 ``(starts, ends)``。

    动作表必须非空、按时间排序、区间正宽、首尾相接。任何不满足都抛
    ``ScheduleWindowError``，不允许静默继续。
    """
    if not schedule_rows:
        raise ScheduleWindowError("actuation schedule is empty")
    starts: list[float] = []
    ends: list[float] = []
    for index, row in enumerate(schedule_rows):
        raw_start = row.get("t_start")
        if raw_start in (None, ""):
            raw_start = actuation_time_value(row)
        if raw_start in (None, ""):
            raise ScheduleWindowError(f"actuation schedule row {index} has no numeric t_start")
        raw_end = row.get("t_end")
        if raw_end in (None, ""):
            raise ScheduleWindowError(f"actuation schedule row {index} has no numeric t_end")
        try:
            start = float(raw_start)
            end = float(raw_end)
        except (TypeError, ValueError) as exc:
            raise ScheduleWindowError(
                f"actuation schedule row {index} has non-numeric t_start/t_end"
            ) from exc
        if end <= start:
            raise ScheduleWindowError(
                f"actuation schedule row {index} has t_end <= t_start ({start!r} .. {end!r})"
            )
        if starts:
            if start < starts[-1]:
                raise ScheduleWindowError(
                    f"actuation schedule row {index} is not sorted: t_start {start!r} "
                    f"precedes the previous row's t_start {starts[-1]!r}"
                )
            if start < ends[-1] - tolerance_s:
                raise ScheduleWindowError(
                    f"actuation schedule row {index} overlaps the previous window "
                    f"(previous t_end {ends[-1]!r}, this t_start {start!r})"
                )
            if start > ends[-1] + tolerance_s:
                raise ScheduleWindowError(
                    f"actuation schedule row {index} leaves a gap after the previous window "
                    f"(previous t_end {ends[-1]!r}, this t_start {start!r})"
                )
        starts.append(start)
        ends.append(end)
    return starts, ends


def locate_schedule_window(
    starts: Sequence[float],
    ends: Sequence[float],
    sample_time: float,
    *,
    ownership: str,
    allow_final_clamp: bool = True,
    clamp_tolerance_s: float = SCHEDULE_WINDOW_TOLERANCE_S,
) -> int:
    """返回 ``sample_time`` 所属的动作表行下标。

    ``left_closed`` 解 ``t_start <= t < t_end``，``right_closed`` 解
    ``t_start < t <= t_end``。两者都直接比较真实时间，不做任何固定偏置：
    历史实现用 ``sample_time - 1e-12`` 查找 ``t_end``，会把漂移小于该容差的
    边界样本拨回上一个窗口。

    仅当样本落在动作表整体跨度之外、且超出量不超过 ``clamp_tolerance_s``
    时才 clamp 到首/末行；明显越界或落入内部空洞都抛 ``ScheduleWindowError``。
    """
    if ownership not in (SAMPLE_OWNERSHIP_LEFT_CLOSED, SAMPLE_OWNERSHIP_RIGHT_CLOSED):
        raise ScheduleWindowError(
            f"locate_schedule_window needs left_closed or right_closed, got {ownership!r}"
        )
    if not starts or not ends:
        raise ScheduleWindowError("actuation schedule is empty")
    if ownership == SAMPLE_OWNERSHIP_LEFT_CLOSED:
        index = bisect.bisect_right(starts, sample_time) - 1
        if index >= 0 and sample_time < ends[index]:
            return index
    else:
        index = bisect.bisect_left(ends, sample_time)
        if index < len(ends) and starts[index] < sample_time:
            return index
    if sample_time <= starts[0]:
        if allow_final_clamp and starts[0] - sample_time <= clamp_tolerance_s:
            return 0
        raise ScheduleWindowError(
            f"sample time {sample_time!r} is before the actuation schedule span "
            f"[{starts[0]!r}, {ends[-1]!r}]"
        )
    if sample_time >= ends[-1]:
        if allow_final_clamp and sample_time - ends[-1] <= clamp_tolerance_s:
            return len(starts) - 1
        raise ScheduleWindowError(
            f"sample time {sample_time!r} is beyond the actuation schedule span "
            f"[{starts[0]!r}, {ends[-1]!r}]"
        )
    raise ScheduleWindowError(
        f"sample time {sample_time!r} falls in a hole of the actuation schedule"
    )


def resolve_sample_window(
    schedule_rows: Sequence[Mapping[str, Any]],
    sample_time: float,
    *,
    ownership: str,
    allow_final_clamp: bool = True,
    tolerance_s: float = SCHEDULE_WINDOW_TOLERANCE_S,
) -> int:
    """一次性接口：解析动作表并定位单个样本所属窗口。

    批量对齐时应先用 ``parse_schedule_windows`` 解析一次，再对每个样本调用
    ``locate_schedule_window``，避免重复解析。
    """
    starts, ends = parse_schedule_windows(schedule_rows, tolerance_s=tolerance_s)
    return locate_schedule_window(
        starts,
        ends,
        sample_time,
        ownership=ownership,
        allow_final_clamp=allow_final_clamp,
        clamp_tolerance_s=tolerance_s,
    )


def schedule_window_id_lookup(
    schedule_rows: Sequence[Mapping[str, Any]],
) -> dict[int, int]:
    """返回 ``window_id`` 到**首个**拥有该 id 的行下标的映射。

    一个 window_id 通常跨多行采样，窗口内的指令是恒定的；保留首行而不是末行，
    使回退时间取窗口起点。
    """
    lookup: dict[int, int] = {}
    for index, row in enumerate(schedule_rows):
        raw = row.get("window_id")
        if raw in (None, ""):
            continue
        try:
            key = int(float(str(raw)))
        except (TypeError, ValueError):
            continue
        lookup.setdefault(key, index)
    return lookup


def schedule_window_spans(
    schedule_rows: Sequence[Mapping[str, Any]],
) -> dict[int, tuple[float, float]]:
    """返回 ``window_id`` 到 ``(最早 t_start, 最晚 t_end)`` 的映射。

    一个 window_id 通常跨多行采样，校验样本时间是否属于该窗口时必须用整个
    窗口的跨度，而不是其中某一行的边界。
    """
    starts, ends = parse_schedule_windows(schedule_rows)
    spans: dict[int, tuple[float, float]] = {}
    for index, row in enumerate(schedule_rows):
        raw = row.get("window_id")
        if raw in (None, ""):
            continue
        try:
            key = int(float(str(raw)))
        except (TypeError, ValueError):
            continue
        current = spans.get(key)
        if current is None:
            spans[key] = (starts[index], ends[index])
        else:
            spans[key] = (min(current[0], starts[index]), max(current[1], ends[index]))
    return spans


def resolve_declared_ownership(manifest: Mapping[str, Any] | None) -> tuple[str, str]:
    """审计侧解析 manifest 声明，返回 ``(ownership, source)``。

    与 organizer 的推断不同，这里接受 ``embedded``：organizer 会把自动推断出的
    模式写回 manifest，因此 ``embedded`` 是一条真实声明，不能因为审计侧只认
    left/right 就回落成 ``right_closed`` 并误标为 ``legacy_default``。
    """
    declared = manifest_sample_ownership(manifest)
    if declared in (
        SAMPLE_OWNERSHIP_LEFT_CLOSED,
        SAMPLE_OWNERSHIP_RIGHT_CLOSED,
        SAMPLE_OWNERSHIP_EMBEDDED,
    ):
        return declared, "manifest"
    # 未声明语义的历史 CLI Case 保持既有 right_closed 兼容，但必须标出来
    return SAMPLE_OWNERSHIP_RIGHT_CLOSED, "legacy_default"


def validate_embedded_window(
    window_spans: Mapping[int, tuple[float, float]],
    window_id: Any,
    sample_time: float,
    *,
    tolerance_s: float = SCHEDULE_WINDOW_TOLERANCE_S,
) -> tuple[float, float]:
    """校验行自带的 ``window_id`` 与样本时间一致，返回该窗口跨度。

    ``window_id`` 不在动作表中、非数值，或样本时间落在该窗口跨度之外时抛
    ``ScheduleWindowError``；不得静默采信。
    """
    key: int | None = None
    if window_id not in (None, ""):
        try:
            key = int(float(str(window_id)))
        except (TypeError, ValueError):
            key = None
    if key is None or key not in window_spans:
        raise ScheduleWindowError(
            f"row carries window_id {window_id!r} which is absent from the actuation schedule"
        )
    span_start, span_end = window_spans[key]
    if not (span_start - tolerance_s <= sample_time <= span_end + tolerance_s):
        raise ScheduleWindowError(
            f"row window_id {key} contradicts its sample time {sample_time!r}, which is "
            f"outside the window span [{span_start!r}, {span_end!r}]"
        )
    return span_start, span_end


def expand_schedule_rows(
    schedule_rows: list[dict[str, Any]],
    *,
    time_step: float | None = None,
) -> list[dict[str, Any]]:
    """Return sample-level rows for a control-window actuation schedule.

    With ``time_step`` unset or non-positive, the original window-level rows are
    returned.  With a positive step, each schedule window is expanded into
    ``ceil(window_duration / time_step)`` rows. The expanded rows keep the
    original ``window_id`` and actuation commands, while the schedule time
    column (``time`` for new files, legacy ``physical_time`` for old files),
    ``t_start`` and ``t_end`` describe the sample interval.
    """
    if not schedule_rows:
        return []
    dt = float(time_step or 0.0)
    if dt <= 0.0:
        return [dict(row) for row in schedule_rows]

    expanded: list[dict[str, Any]] = []
    for row_idx, row in enumerate(schedule_rows):
        time_column = actuation_time_column(row)
        start = _row_start(row, row_idx)
        end = _row_end(schedule_rows, row_idx, start, dt)
        if end <= start:
            end = start + dt
        current = start
        while current < end - 1.0e-12:
            next_time = min(current + dt, end)
            sample = dict(row)
            sample[time_column] = round(current, 12)
            sample["t_start"] = round(current, 12)
            sample["t_end"] = round(next_time, 12)
            expanded.append(sample)
            current = next_time
    return expanded


def infer_time_step(rows: list[dict[str, Any]]) -> float:
    """Infer the row-to-row sample interval from sample-level rows."""
    if len(rows) < 2:
        return _row_duration(rows[0], 0.0) if rows else 0.0
    return float(actuation_time_value(rows[1], 0.0)) - float(
        actuation_time_value(rows[0], 0.0)
    )


def infer_window_duration(rows: list[dict[str, Any]]) -> float:
    """Infer the first control-window duration from schedule or sample rows."""
    if not rows:
        return 0.0
    row = rows[0]
    if "window_id" in row and actuation_time_value(row) is not None:
        first_window = str(row.get("window_id"))
        first_time = float(actuation_time_value(row, 0.0))
        for later in rows[1:]:
            if str(later.get("window_id")) != first_window:
                return float(actuation_time_value(later, first_time)) - first_time
    if "t_start" in row and "t_end" in row:
        return float(row["t_end"]) - float(row["t_start"])
    if len(rows) > 1:
        return float(actuation_time_value(rows[1], 0.0)) - float(
            actuation_time_value(rows[0], 0.0)
        )
    return 0.0


def resolve_schedule_time_step(
    schedule_path: str | Path,
    *,
    explicit_time_step: float | None = None,
) -> tuple[float | None, str]:
    """Resolve the sample time step for an existing schedule.

    Priority order:
    1. an explicit function/CLI argument;
    2. ``config_summary.yaml`` next to the schedule or its sibling ``input/``;
    3. no override, which lets callers fall back to schedule window rows.
    """
    if explicit_time_step is not None:
        return float(explicit_time_step), "argument"
    config_time_step = read_schedule_config_time_step(schedule_path)
    if config_time_step is not None:
        return config_time_step, "config_summary"
    return None, "schedule_window"


def read_schedule_config_time_step(schedule_path: str | Path) -> float | None:
    """Read ``time_step`` from schedule generation metadata when present."""
    for config_path in schedule_config_candidates(schedule_path):
        if not config_path.is_file():
            continue
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        value = data.get("solver_time_step_seconds")
        if value is None:
            value = data.get("time_step_seconds")
        if value is None:
            value = data.get("time_step")
        if value is None and isinstance(data.get("actuation"), dict):
            value = data["actuation"].get(
                "solver_time_step", data["actuation"].get("time_step")
            )
        if value in (None, ""):
            continue
        time_step = float(value)
        if time_step > 0.0:
            return time_step
    return None


def schedule_config_candidates(schedule_path: str | Path) -> list[Path]:
    """Return likely config-summary locations for a schedule CSV."""
    parent = Path(schedule_path).parent
    candidates = [parent / "config_summary.yaml"]
    if parent.name == "input":
        candidates.append(parent.parent / "config_summary.yaml")
    else:
        candidates.append(parent / "input" / "config_summary.yaml")
    return candidates


def _row_start(row: dict[str, Any], row_idx: int) -> float:
    for key in ("t_start", ACTUATION_TIME_COLUMN, LEGACY_ACTUATION_TIME_COLUMN):
        value = row.get(key)
        if value not in (None, ""):
            return float(value)
    return float(row_idx)


def _row_end(rows: list[dict[str, Any]], row_idx: int, start: float, fallback_dt: float) -> float:
    row = rows[row_idx]
    value = row.get("t_end")
    if value not in (None, ""):
        return float(value)
    if row_idx + 1 < len(rows):
        next_start = _row_start(rows[row_idx + 1], row_idx + 1)
        if next_start > start:
            return next_start
    return start + fallback_dt


def _row_duration(row: dict[str, Any], default: float) -> float:
    if "t_start" in row and "t_end" in row:
        return float(row["t_end"]) - float(row["t_start"])
    return default
