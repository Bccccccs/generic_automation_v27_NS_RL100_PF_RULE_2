"""统一 sample ownership 解析器的测试。

B52 monitor-only 数据用 left_closed ``[t_start, t_end)``，CLI runtime 数据用
right_closed ``(t_start, t_end]``。两种语义必须显式声明，不得按行数推断。
"""

from __future__ import annotations

import pytest

from flow_control.sampling import (
    SAMPLE_OWNERSHIP_EMBEDDED,
    SAMPLE_OWNERSHIP_LEFT_CLOSED,
    SAMPLE_OWNERSHIP_RIGHT_CLOSED,
    ScheduleWindowError,
    locate_schedule_window,
    manifest_sample_ownership,
    parse_schedule_windows,
    resolve_sample_window,
)


def _rows(starts: list[float], ends: list[float]) -> list[dict[str, object]]:
    return [
        {"time": start, "window_id": idx, "t_start": start, "t_end": end}
        for idx, (start, end) in enumerate(zip(starts, ends))
    ]


THREE_WINDOWS = _rows([0.0, 0.1, 0.2], [0.1, 0.2, 0.3])


def test_left_closed_assigns_boundary_sample_to_opening_window() -> None:
    assert resolve_sample_window(THREE_WINDOWS, 0.0, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED) == 0
    assert resolve_sample_window(THREE_WINDOWS, 0.05, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED) == 0
    assert resolve_sample_window(THREE_WINDOWS, 0.1, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED) == 1
    assert resolve_sample_window(THREE_WINDOWS, 0.2, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED) == 2


def test_left_closed_resolves_sub_tolerance_drift_by_real_side() -> None:
    assert resolve_sample_window(
        THREE_WINDOWS, 0.1 + 1.0e-13, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED
    ) == 1
    assert resolve_sample_window(
        THREE_WINDOWS, 0.1 - 1.0e-13, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED
    ) == 0


def test_right_closed_assigns_boundary_sample_to_closing_window() -> None:
    assert resolve_sample_window(THREE_WINDOWS, 0.1, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED) == 0
    assert resolve_sample_window(THREE_WINDOWS, 0.2, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED) == 1
    assert resolve_sample_window(THREE_WINDOWS, 0.3, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED) == 2


def test_right_closed_resolves_sub_tolerance_drift_by_real_side() -> None:
    assert resolve_sample_window(
        THREE_WINDOWS, 0.1 + 1.0e-13, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED
    ) == 1
    assert resolve_sample_window(
        THREE_WINDOWS, 0.1 - 1.0e-13, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED
    ) == 0


def test_no_fixed_negative_bias_pulls_drifted_sample_back() -> None:
    """历史 bug：``sample_time - 1e-12`` 把漂移 6.5e-13 的样本拨回上一个窗口。

    真实数据 runs/b52/training 第 22499 行 t=4.5000000000006475 就是这样被错标，
    导致 ``JET_03=0`` 而 ``actual_massflow_03=2.86``，被 B04 判成关阀泄漏。
    """
    drifted = 0.1 + 6.5e-13

    assert resolve_sample_window(THREE_WINDOWS, drifted, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED) == 1
    assert resolve_sample_window(THREE_WINDOWS, drifted, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED) == 1


def test_real_b52_boundary_times_resolve_consistently() -> None:
    """漂移跨过 1e-12 与否，两种语义都必须给出与真实时间同侧的窗口。

    时间值取自 runs/b52 真实数据：第 22499 行漂移 6.5e-13（小于旧实现的
    1e-12 容差），第 24499 行漂移 1.491e-12（大于该容差）。旧实现因此对
    两者给出互相矛盾的窗口归属。
    """
    rows = _rows(
        [4.4998, 4.5, 4.5002, 4.5004],
        [4.5, 4.5002, 4.5004, 4.5006],
    )

    assert resolve_sample_window(rows, 4.5000000000006475, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED) == 1
    assert resolve_sample_window(rows, 4.5000000000006475, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED) == 1
    assert resolve_sample_window(rows, 4.500200000001491, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED) == 2
    assert resolve_sample_window(rows, 4.500200000001491, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED) == 2


def test_final_row_clamps_small_float_overshoot() -> None:
    """validation 末行 t=5.300000000002336 超出 t_end=5.3 仅 2.336e-12 s。"""
    overshoot = 0.3 + 2.336e-12

    assert resolve_sample_window(
        THREE_WINDOWS, overshoot, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED, allow_final_clamp=True
    ) == 2
    assert resolve_sample_window(
        THREE_WINDOWS, overshoot, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED, allow_final_clamp=True
    ) == 2


def test_final_row_rejects_clear_overshoot() -> None:
    with pytest.raises(ScheduleWindowError, match="beyond"):
        resolve_sample_window(
            THREE_WINDOWS, 0.31, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED, allow_final_clamp=True
        )


def test_final_clamp_can_be_disabled() -> None:
    with pytest.raises(ScheduleWindowError):
        resolve_sample_window(
            THREE_WINDOWS,
            0.3 + 2.336e-12,
            ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED,
            allow_final_clamp=False,
        )


def test_sample_before_schedule_span_is_rejected() -> None:
    with pytest.raises(ScheduleWindowError, match="before"):
        resolve_sample_window(
            THREE_WINDOWS, -0.05, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED, allow_final_clamp=True
        )


def test_sample_in_interior_hole_is_rejected() -> None:
    starts, ends = [0.0, 0.2], [0.1, 0.3]

    with pytest.raises(ScheduleWindowError, match="hole"):
        locate_schedule_window(starts, ends, 0.15, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED)


def test_empty_schedule_is_rejected() -> None:
    with pytest.raises(ScheduleWindowError, match="empty"):
        parse_schedule_windows([])


def test_unsorted_schedule_is_rejected() -> None:
    with pytest.raises(ScheduleWindowError, match="not sorted"):
        parse_schedule_windows(_rows([0.1, 0.0], [0.2, 0.1]))


def test_overlapping_schedule_is_rejected() -> None:
    with pytest.raises(ScheduleWindowError, match="overlap"):
        parse_schedule_windows(_rows([0.0, 0.05], [0.1, 0.15]))


def test_gap_in_schedule_is_rejected() -> None:
    with pytest.raises(ScheduleWindowError, match="gap"):
        parse_schedule_windows(_rows([0.0, 0.2], [0.1, 0.3]))


def test_zero_width_window_is_rejected() -> None:
    with pytest.raises(ScheduleWindowError, match="t_end"):
        parse_schedule_windows(_rows([0.0, 0.1], [0.1, 0.1]))


def test_missing_t_start_or_t_end_is_rejected() -> None:
    with pytest.raises(ScheduleWindowError, match="t_start"):
        parse_schedule_windows([{"window_id": 0, "t_end": 0.1}])
    with pytest.raises(ScheduleWindowError, match="t_end"):
        parse_schedule_windows([{"time": 0.0, "window_id": 0, "t_start": 0.0}])


def test_legacy_time_column_is_accepted_as_t_start_fallback() -> None:
    rows = [
        {"physical_time": 0.0, "window_id": 0, "t_end": 0.1},
        {"physical_time": 0.1, "window_id": 1, "t_end": 0.2},
    ]

    assert resolve_sample_window(rows, 0.1, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED) == 1


def test_manifest_declaration_is_normalized() -> None:
    assert (
        manifest_sample_ownership({"sample_ownership_rule": "left_closed"})
        == SAMPLE_OWNERSHIP_LEFT_CLOSED
    )
    assert (
        manifest_sample_ownership(
            {"actuation": {"sample_ownership_rule": "right_closed"}}
        )
        == SAMPLE_OWNERSHIP_RIGHT_CLOSED
    )
    assert manifest_sample_ownership({"sample_ownership_rule": "embedded"}) == SAMPLE_OWNERSHIP_EMBEDDED


def test_manifest_legacy_prose_declaration_is_normalized() -> None:
    """仓库既有模板用散文描述区间，必须仍可解析而不是当成未声明。"""
    prose = (
        "t_start < t_sample <= t_end for current CLI macro outputs; "
        "re-confirm if using STAR GUI table(time) profile or another macro"
    )

    assert (
        manifest_sample_ownership({"actuation": {"sample_ownership_rule": prose}})
        == SAMPLE_OWNERSHIP_RIGHT_CLOSED
    )
    assert (
        manifest_sample_ownership(
            {"actuation": {"sample_ownership_rule": "t_start <= t_sample < t_end"}}
        )
        == SAMPLE_OWNERSHIP_LEFT_CLOSED
    )


def test_undeclared_manifest_returns_none() -> None:
    assert manifest_sample_ownership({}) is None
    assert (
        manifest_sample_ownership(
            {"actuation": {"sample_ownership_rule": "Each 0.0001 s sample belongs to the active window."}}
        )
        is None
    )
