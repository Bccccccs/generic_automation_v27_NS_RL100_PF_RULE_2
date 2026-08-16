from __future__ import annotations

import csv
from pathlib import Path

import pytest

from flow_control.cli.organize_outputs import (
    _choose_directory,
    _choose_target_dir,
    _discover_input_dirs,
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

    merged = _attach_schedule(outputs, schedule)

    assert [row["window_id"] for row in merged] == [0, 1]
    assert [row["JET_02"] for row in merged] == [1, 0]
    assert [row["cmd_massflow_02"] for row in merged] == [1.0, 0.0]


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
    )

    assert result["timeseries_path"].is_file()
    rows = list(csv.DictReader(result["timeseries_path"].open(encoding="utf-8")))
    assert rows[0]["window_id"] == "0"
    assert rows[0]["Fz_Total"] == "-10.0"
    assert (target / "input" / "actuation_heatmap.svg").read_text(encoding="utf-8") == "<svg/>\n"
    assert (target / "raw_star" / "out_put" / "screenshots" / "flow.png").read_bytes() == b"png-data"
