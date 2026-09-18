from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from flow_control.star_ingest.b3_acceptance import CASE_ORDER, validate_b3_case_set


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _schedule(expected_jet: int | None, *, pulse_massflow: float = 1.0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(5):
        row: dict[str, object] = {
            "physical_time": index * 0.1,
            "window_id": index,
            "t_start": index * 0.1,
            "t_end": (index + 1) * 0.1,
        }
        for jet in range(1, 25):
            on = expected_jet == jet and index == 2
            row[f"JET_{jet:02d}"] = int(on)
            row[f"cmd_massflow_{jet:02d}"] = pulse_massflow if on else 0.0
        rows.append(row)
    return rows


def _timeseries(schedule: list[dict[str, object]], expected_jet: int | None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, command in enumerate(schedule):
        row: dict[str, object] = {
            "physical_time": command["t_end"],
            "window_id": command["window_id"],
            "Fz_S1L": 1.0,
            "Fz_S1R": 2.0,
            "Fz_S2L": 3.0,
            "Fz_S2R": 4.0,
            "Fz_S3L": 5.0,
            "Fz_S3R": 6.0,
            "Fz_Total": 21.0,
            "Drag_Total": 10.0,
            "Pitch_Moment": 1.0,
            "Roll_Moment": 2.0,
        }
        for jet in range(1, 25):
            row[f"actual_massflow_{jet:02d}"] = (
                1.0 if expected_jet == jet and index == 2 else 0.0
            )
        rows.append(row)
    return rows


def _write_case(root: Path, case_id: str, expected_jet: int | None) -> None:
    case_dir = root / case_id
    for directory in ("raw_star", "processed", "figures", "logs"):
        (case_dir / directory).mkdir(parents=True, exist_ok=True)
    (case_dir / "raw_star" / "source.csv").write_text("time,value\n0.1,1\n", encoding="utf-8")
    (case_dir / "logs" / "star.log").write_text("completed\n", encoding="utf-8")
    schedule = _schedule(expected_jet)
    _write_csv(case_dir / "actuation_schedule.csv", schedule)
    _write_csv(case_dir / "processed" / "timeseries.csv", _timeseries(schedule, expected_jet))
    manifest = {
        "git_commit": "a" * 40,
        "star": {
            "sim_file_identifier": "baseline_checkpoint.sim",
            "sim_file_hash_sha256": "b" * 64,
            "mesh_version": "mesh-v1",
        },
        "solver_time": {
            "time_step_s": 0.1,
            "inner_iterations_per_step": 10,
            "report_sampling_interval_s": 0.1,
        },
        "solver_settings": {"scheme": "implicit-unsteady"},
    }
    (case_dir / "case_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (case_dir / "quality_report.json").write_text(
        json.dumps(
            {
                "run_success_flag": True,
                "B04_real_quality": {"summary": {"run_success_flag": True}},
            }
        ),
        encoding="utf-8",
    )


def test_b3_case_set_passes_complete_ordered_pulses(tmp_path: Path) -> None:
    for case_id, expected_jet in CASE_ORDER:
        _write_case(tmp_path, case_id, expected_jet)

    report = validate_b3_case_set(tmp_path)

    assert report["passed"] is True
    assert report["cases"][1]["segments"] == {
        "baseline_rows": 2,
        "pulse_rows": 1,
        "recovery_rows": 2,
    }


def test_b3_gate_blocks_later_cases_when_g00_fails(tmp_path: Path) -> None:
    for case_id, expected_jet in CASE_ORDER:
        _write_case(tmp_path, case_id, expected_jet)
    (tmp_path / "G00_nojet_baseline" / "quality_report.json").write_text(
        json.dumps({"run_success_flag": False}), encoding="utf-8"
    )

    report = validate_b3_case_set(tmp_path)

    assert report["passed"] is False
    assert any("previous B3 case" in error for error in report["cases"][1]["errors"])
    assert any("previous B3 case" in error for error in report["cases"][2]["errors"])


def test_b3_requires_b04_pass_result(tmp_path: Path) -> None:
    for case_id, expected_jet in CASE_ORDER:
        _write_case(tmp_path, case_id, expected_jet)
    quality_path = tmp_path / "G01_J02_pulse" / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["B04_real_quality"]["summary"]["run_success_flag"] = False
    quality_path.write_text(json.dumps(quality), encoding="utf-8")

    report = validate_b3_case_set(tmp_path)

    assert any("B04_real_quality has not passed" in error for error in report["cases"][1]["errors"])


def test_b3_rejects_pulse_without_pre_baseline(tmp_path: Path) -> None:
    for case_id, expected_jet in CASE_ORDER:
        _write_case(tmp_path, case_id, expected_jet)
    case_dir = tmp_path / "G01_J02_pulse"
    schedule = _schedule(2)
    schedule[0]["JET_02"] = 1
    schedule[0]["cmd_massflow_02"] = 1.0
    schedule[2]["JET_02"] = 0
    schedule[2]["cmd_massflow_02"] = 0.0
    _write_csv(case_dir / "actuation_schedule.csv", schedule)
    _write_csv(case_dir / "processed" / "timeseries.csv", _timeseries(schedule, 2))

    report = validate_b3_case_set(tmp_path)

    assert any("no pre-jet baseline" in error for error in report["cases"][1]["errors"])


def test_b3_requires_same_j02_j06_pulse_signature(tmp_path: Path) -> None:
    for case_id, expected_jet in CASE_ORDER:
        _write_case(tmp_path, case_id, expected_jet)
    case_dir = tmp_path / "G02_J06_pulse"
    schedule = _schedule(6, pulse_massflow=0.8)
    _write_csv(case_dir / "actuation_schedule.csv", schedule)
    _write_csv(case_dir / "processed" / "timeseries.csv", _timeseries(schedule, 6))

    report = validate_b3_case_set(tmp_path)

    assert any("pulse timing/massflow signatures differ" in error for error in report["cases"][2]["errors"])
