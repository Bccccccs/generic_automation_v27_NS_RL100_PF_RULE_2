from __future__ import annotations

import csv
from pathlib import Path

import pytest

from flow_control.cli.organize_outputs import (
    _choose_directory,
    _choose_target_dir,
    _discover_input_dirs,
)
from flow_control.sampling import (
    SAMPLE_OWNERSHIP_AUTO,
    SAMPLE_OWNERSHIP_EMBEDDED,
    SAMPLE_OWNERSHIP_LEFT_CLOSED,
    SAMPLE_OWNERSHIP_RIGHT_CLOSED,
    ScheduleWindowError,
)
from flow_control.star_ingest.output_organizer import (
    _attach_schedule,
    _find_schedule,
    _infer_case_type,
    organize_ccm_outputs,
)


def test_interactive_discovery_lists_directories_under_runs(tmp_path: Path) -> None:
    legacy = tmp_path / "runs" / "legacy_case"
    standard = tmp_path / "runs" / "standard_case"
    (legacy / "out_put").mkdir(parents=True)
    (standard / "raw_star" / "out_put").mkdir(parents=True)
    (legacy / "actuation_schedule.csv").write_text("window_id,t_end\n0,0.1\n", encoding="utf-8")
    (standard / "input").mkdir()
    (standard / "input" / "actuation_schedule.csv").write_text(
        "window_id,t_end\n0,0.1\n", encoding="utf-8"
    )

    assert _discover_input_dirs(tmp_path / "runs") == [legacy, standard]


def test_directory_browser_shows_level_then_selects_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = tmp_path / "runs" / "week4" / "case_a"
    case.mkdir(parents=True)
    answers = iter(["1", "1", "0"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert _choose_directory(tmp_path / "runs", label="输入目录") == case


def test_new_target_prompts_for_directory_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    parent = runs / "week4"
    parent.mkdir()
    answers = iter(["1", "1", "0", "merged_case"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert _choose_target_dir(runs) == (parent / "merged_case", False)


def test_schedule_is_found_from_input_subdirectory(tmp_path: Path) -> None:
    schedule = tmp_path / "input" / "actuation_schedule.csv"
    schedule.parent.mkdir()
    schedule.write_text("physical_time,window_id\n0.0,0\n", encoding="utf-8")

    assert _find_schedule(tmp_path) == schedule


def test_case_type_is_inferred_from_schedule() -> None:
    assert _infer_case_type([{"JET_02": "0", "cmd_massflow_02": "0"}]) == "no_jet"
    assert _infer_case_type([{"JET_02": "1", "cmd_massflow_02": "1.0"}]) == "jet_on"


def test_schedule_is_attached_by_physical_sample_time() -> None:
    outputs = [{"physical_time": 0.1}, {"physical_time": 0.2}]
    schedule = [
        {"window_id": 0, "t_start": 0.0, "t_end": 0.1, "JET_02": 1, "cmd_massflow_02": 1.0},
        {"window_id": 1, "t_start": 0.1, "t_end": 0.2, "JET_02": 0, "cmd_massflow_02": 0.0},
    ]

    merged = _attach_schedule(outputs, schedule, ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED)

    assert [row["window_id"] for row in merged] == [0, 1]
    assert [row["JET_02"] for row in merged] == [1, 0]
    assert [row["cmd_massflow_02"] for row in merged] == [1.0, 0.0]
    # 命中的窗口边界必须写入输出，便于事后审计对齐结果
    assert [row["t_start"] for row in merged] == [0.0, 0.1]
    assert [row["t_end"] for row in merged] == [0.1, 0.2]


def test_equal_row_count_still_resolves_by_time_not_index() -> None:
    """行数相等不得成为按下标拼接的理由：同样的数据换语义必须给出不同结果。"""
    outputs = [{"physical_time": 0.1}, {"physical_time": 0.2}]
    schedule = [
        {"window_id": 0, "t_start": 0.0, "t_end": 0.1, "JET_02": 1, "cmd_massflow_02": 1.0},
        {"window_id": 1, "t_start": 0.1, "t_end": 0.2, "JET_02": 0, "cmd_massflow_02": 0.0},
    ]

    merged = _attach_schedule(outputs, schedule, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED)

    # t=0.1 属于 [0.1, 0.2) 即 window 1；t=0.2 超出末行 t_end，按浮点容差 clamp 到 window 1
    assert [row["window_id"] for row in merged] == [1, 1]
    assert [row["JET_02"] for row in merged] == [0, 0]


def test_left_closed_event_onset_takes_opening_window() -> None:
    """行数不等且带亚容差漂移：事件起点必须取新窗口。

    取自 runs/b52/training 第 22499 行，t=4.5000000000006475（漂移 6.5e-13）。
    旧实现用 bisect_left(t_end, sample - 1e-12) 把它拨回 window 20，使
    JET_03=0 而 STAR 实测 actual_massflow_03=2.86，被 B04 误判为关阀泄漏。
    """
    schedule = [
        {"window_id": 20, "t_start": 4.4998, "t_end": 4.5, "JET_03": 0, "cmd_massflow_03": 0.0},
        {"window_id": 21, "t_start": 4.5, "t_end": 4.5002, "JET_03": 1, "cmd_massflow_03": 2.86},
        {"window_id": 22, "t_start": 4.5002, "t_end": 4.5004, "JET_03": 1, "cmd_massflow_03": 2.86},
        {"window_id": 23, "t_start": 4.5004, "t_end": 4.5006, "JET_03": 1, "cmd_massflow_03": 2.86},
    ]
    outputs = [
        {"physical_time": 4.5000000000006475},  # 漂移 6.5e-13 < 旧实现的 1e-12 容差
        {"physical_time": 4.500200000001491},  # 漂移 1.491e-12 > 旧实现的 1e-12 容差
    ]

    merged = _attach_schedule(outputs, schedule, ownership=SAMPLE_OWNERSHIP_LEFT_CLOSED)

    assert [row["window_id"] for row in merged] == [21, 22]
    assert [row["JET_03"] for row in merged] == [1, 1]
    assert [row["cmd_massflow_03"] for row in merged] == [2.86, 2.86]
    assert [row["t_start"] for row in merged] == [4.5, 4.5002]


def test_embedded_window_id_is_preserved_and_validated() -> None:
    """runtime CSV 自带可信 window_id 时优先信任，不重新按时间猜测。"""
    schedule = [
        {"window_id": 0, "t_start": 0.0, "t_end": 0.1, "JET_01": 1, "cmd_massflow_01": 1.0},
        {"window_id": 1, "t_start": 0.1, "t_end": 0.2, "JET_01": 0, "cmd_massflow_01": 0.0},
    ]
    outputs = [
        {"physical_time": 0.1, "window_id": 0},
        {"physical_time": 0.2, "window_id": 1},
    ]

    merged = _attach_schedule(outputs, schedule, ownership=SAMPLE_OWNERSHIP_EMBEDDED)

    assert [row["window_id"] for row in merged] == [0, 1]
    assert [row["cmd_massflow_01"] for row in merged] == [1.0, 0.0]
    assert [row["t_end"] for row in merged] == [0.1, 0.2]


def test_embedded_window_id_absent_from_schedule_is_rejected() -> None:
    schedule = [
        {"window_id": 0, "t_start": 0.0, "t_end": 0.1, "JET_01": 1, "cmd_massflow_01": 1.0},
        {"window_id": 1, "t_start": 0.1, "t_end": 0.2, "JET_01": 0, "cmd_massflow_01": 0.0},
    ]
    outputs = [{"physical_time": 0.1, "window_id": 99}]

    with pytest.raises(ScheduleWindowError, match="window_id"):
        _attach_schedule(outputs, schedule, ownership=SAMPLE_OWNERSHIP_EMBEDDED)


def test_embedded_window_id_contradicting_sample_time_is_rejected() -> None:
    """自带 window_id 与样本时间矛盾时必须明确失败，不能静默采信。"""
    schedule = [
        {"window_id": 0, "t_start": 0.0, "t_end": 0.1, "JET_01": 1, "cmd_massflow_01": 1.0},
        {"window_id": 1, "t_start": 0.1, "t_end": 0.2, "JET_01": 0, "cmd_massflow_01": 0.0},
    ]
    outputs = [{"physical_time": 0.5, "window_id": 0}]

    with pytest.raises(ScheduleWindowError, match="contradict"):
        _attach_schedule(outputs, schedule, ownership=SAMPLE_OWNERSHIP_EMBEDDED)


def test_auto_prefers_embedded_when_runtime_carries_window_id() -> None:
    schedule = [
        {"window_id": 0, "t_start": 0.0, "t_end": 0.1, "JET_01": 1, "cmd_massflow_01": 1.0},
        {"window_id": 1, "t_start": 0.1, "t_end": 0.2, "JET_01": 0, "cmd_massflow_01": 0.0},
    ]
    outputs = [{"physical_time": 0.1, "window_id": 0}, {"physical_time": 0.2, "window_id": 1}]

    merged = _attach_schedule(outputs, schedule, ownership=SAMPLE_OWNERSHIP_AUTO)

    assert [row["window_id"] for row in merged] == [0, 1]


def test_auto_refuses_to_guess_for_monitor_only_rows() -> None:
    """monitor-only 导出无法证明样本语义，必须要求显式声明而不是静默猜测。"""
    schedule = [
        {"window_id": 0, "t_start": 0.0, "t_end": 0.1, "JET_01": 1, "cmd_massflow_01": 1.0},
        {"window_id": 1, "t_start": 0.1, "t_end": 0.2, "JET_01": 0, "cmd_massflow_01": 0.0},
    ]
    outputs = [{"physical_time": 0.1}, {"physical_time": 0.15}]

    with pytest.raises(ScheduleWindowError, match="sample_ownership"):
        _attach_schedule(outputs, schedule, ownership=SAMPLE_OWNERSHIP_AUTO)


def test_input_directory_requires_actuation_schedule(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="actuation_schedule.csv"):
        organize_ccm_outputs(
            input_dir=tmp_path,
            output_dir=tmp_path / "case",
        )


def test_schedule_and_star_outputs_can_come_from_separate_directories(tmp_path: Path) -> None:
    input_dir = tmp_path / "schedule_source" / "input"
    star_output_dir = tmp_path / "star_source" / "out_put"
    target = tmp_path / "merged_case"
    input_dir.mkdir(parents=True)
    star_output_dir.mkdir(parents=True)
    (input_dir / "actuation_schedule.csv").write_text(
        "window_id,t_start,t_end,JET_01,cmd_massflow_01\n0,0.0,0.1,1,1.0\n",
        encoding="utf-8",
    )
    (input_dir / "actuation_heatmap.svg").write_text("<svg/>\n", encoding="utf-8")
    (star_output_dir / "force.csv").write_text(
        '"时间","Fz Monitor: Fz Monitor (N)"\n0.1,-10.0\n',
        encoding="utf-8",
    )
    (star_output_dir / "screenshots").mkdir()
    (star_output_dir / "screenshots" / "flow.png").write_bytes(b"png-data")

    result = organize_ccm_outputs(
        input_dir=input_dir,
        star_output_dir=star_output_dir,
        output_dir=target,
        sample_ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED,
    )

    assert result["timeseries_path"].is_file()
    rows = list(csv.DictReader(result["timeseries_path"].open(encoding="utf-8")))
    assert rows[0]["window_id"] == "0"
    assert rows[0]["Fz_Total"] == "-10.0"
    assert (target / "input" / "actuation_heatmap.svg").read_text(encoding="utf-8") == "<svg/>\n"
    assert (target / "raw_star" / "out_put" / "screenshots" / "flow.png").read_bytes() == b"png-data"
    manifest_text = (target / "case_manifest.yaml").read_text(encoding="utf-8")
    assert "sample_ownership_rule: right_closed" in manifest_text


def test_organize_monitor_only_requires_explicit_sample_ownership(tmp_path: Path) -> None:
    """monitor-only 导出不得静默猜测样本语义。"""
    input_dir = tmp_path / "schedule_source" / "input"
    star_output_dir = tmp_path / "star_source" / "out_put"
    input_dir.mkdir(parents=True)
    star_output_dir.mkdir(parents=True)
    (input_dir / "actuation_schedule.csv").write_text(
        "window_id,t_start,t_end,JET_01,cmd_massflow_01\n0,0.0,0.1,1,1.0\n",
        encoding="utf-8",
    )
    (star_output_dir / "force.csv").write_text(
        '"时间","Fz Monitor: Fz Monitor (N)"\n0.05,-10.0\n',
        encoding="utf-8",
    )

    with pytest.raises(ScheduleWindowError, match="sample_ownership"):
        organize_ccm_outputs(
            input_dir=input_dir,
            star_output_dir=star_output_dir,
            output_dir=tmp_path / "merged_case",
        )


def test_organize_merges_step_runtime_actual_massflow_with_split_monitors(tmp_path: Path) -> None:
    input_dir = tmp_path / "case" / "input"
    output_dir = tmp_path / "case" / "raw_star" / "output"
    target = tmp_path / "organized"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (input_dir / "actuation_schedule.csv").write_text(
        "window_id,t_start,t_end,JET_01,cmd_massflow_01\n"
        "0,0.0,0.1,1,0.015\n"
        "0,0.1,0.2,1,0.015\n",
        encoding="utf-8",
    )
    (output_dir / "timeseries.csv").write_text(
        "physical_time,window_id,actual_massflow_01\n"
        "0.1,0,-0.015\n"
        "0.2,0,-0.015\n",
        encoding="utf-8",
    )
    (output_dir / "force.csv").write_text(
        '"Time","Fz Monitor: Fz Monitor (N)"\n'
        "0.1,-10.0\n"
        "0.2,-11.0\n",
        encoding="utf-8",
    )

    result = organize_ccm_outputs(
        input_dir=input_dir,
        star_output_dir=output_dir,
        output_dir=target,
    )

    rows = list(csv.DictReader(result["timeseries_path"].open(encoding="utf-8")))
    assert [row["physical_time"] for row in rows] == ["0.1", "0.2"]
    assert [row["Fz_Total"] for row in rows] == ["-10.0", "-11.0"]
    assert [row["actual_massflow_01"] for row in rows] == ["0.015", "0.015"]
    assert [row["cmd_massflow_01"] for row in rows] == ["0.015", "0.015"]


def test_organize_can_run_final_quality_check_for_complete_runtime_output(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "raw_star" / "output"
    target = tmp_path / "case"
    input_dir.mkdir()
    output_dir.mkdir(parents=True)
    jet_columns = [f"JET_{idx:02d}" for idx in range(1, 25)]
    command_columns = [f"cmd_massflow_{idx:02d}" for idx in range(1, 25)]
    actual_columns = [f"actual_massflow_{idx:02d}" for idx in range(1, 25)]
    with (input_dir / "actuation_schedule.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["physical_time", "window_id", "t_start", "t_end", *jet_columns, *command_columns],
        )
        writer.writeheader()
        writer.writerow(
            {
                "physical_time": 0.1,
                "window_id": 0,
                "t_start": 0.0,
                "t_end": 0.1,
                **{column: 1 if column == "JET_01" else 0 for column in jet_columns},
                **{column: 0.015 if column == "cmd_massflow_01" else 0.0 for column in command_columns},
            }
        )
    load_values = {
        "fc_load_S1L": -1.0,
        "fc_load_S1R": -1.0,
        "fc_load_S2L": -1.0,
        "fc_load_S2R": -1.0,
        "fc_load_S3L": -1.0,
        "fc_load_S3R": -1.0,
    }
    with (output_dir / "timeseries.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "physical_time",
                "window_id",
                *actual_columns,
                "total",
                "drag",
                "Pitch_Moment",
                "Roll_Moment",
                "Jet_Reaction_Z",
                *load_values,
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "physical_time": 0.1,
                "window_id": 0,
                **{column: -0.015 if column == "actual_massflow_01" else 0.0 for column in actual_columns},
                "total": -10.0,
                "drag": 20.0,
                "Pitch_Moment": 1.0,
                "Roll_Moment": 2.0,
                "Jet_Reaction_Z": -3.0,
                **load_values,
            }
        )

    result = organize_ccm_outputs(
        input_dir=input_dir,
        star_output_dir=output_dir,
        output_dir=target,
        run_quality_check=True,
    )

    assert result["quality_report"]["status"] == "organized_and_checked"
    assert result["quality_report"]["run_success_flag"] is True
    assert (target / "figures" / "quality_summary.png").is_file()
