from __future__ import annotations

import csv
from pathlib import Path

import pytest

from flow_control.star_ingest.output_organizer import (
    _attach_schedule,
    _find_schedule,
    _infer_case_type,
    organize_ccm_outputs,
)


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
