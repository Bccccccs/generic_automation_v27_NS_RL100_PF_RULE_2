from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from flow_control.star_ingest.physics_consistency_checker import check_case, check_cases


def _rows(*, bad_time: bool = False, bad_massflow: bool = False, missing_actual: bool = False) -> list[dict]:
    rows: list[dict] = []
    for index in range(4):
        time = (index + 1) * 0.1
        if bad_time and index == 2:
            time = 0.15
        row = {
            "physical_time": time,
            "window_id": index,
            "Fz_S1L": 1.0,
            "Fz_S1R": 2.0,
            "Fz_S2L": 3.0,
            "Fz_S2R": 4.0,
            "Fz_S3L": 5.0,
            "Fz_S3R": 6.0,
            "Fz_Total": 100.0,
            "Drag_Total": 10.0,
            "Pitch_Moment": 1.5,
            "Roll_Moment": 2.5,
            "Jet_Reaction_Z": 0.2,
        }
        for jet_idx in range(1, 25):
            jet_on = jet_idx == 1 and index in {0, 1}
            row[f"JET_{jet_idx:02d}"] = 1 if jet_on else 0
            row[f"cmd_massflow_{jet_idx:02d}"] = 2.0 if jet_on else 0.0
            if not missing_actual:
                if bad_massflow and jet_idx == 2 and index == 2:
                    row[f"actual_massflow_{jet_idx:02d}"] = 0.3
                else:
                    row[f"actual_massflow_{jet_idx:02d}"] = 2.1 if jet_on else 0.0
        rows.append(row)
    return rows


def _schedule() -> list[dict]:
    rows: list[dict] = []
    for index in range(4):
        row = {
            "physical_time": index * 0.1,
            "window_id": index,
            "t_start": index * 0.1,
            "t_end": (index + 1) * 0.1,
        }
        for jet_idx in range(1, 25):
            jet_on = jet_idx == 1 and index in {0, 1}
            row[f"JET_{jet_idx:02d}"] = 1 if jet_on else 0
            row[f"cmd_massflow_{jet_idx:02d}"] = 2.0 if jet_on else 0.0
        rows.append(row)
    return rows


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_case(tmp_path: Path, *, rows: list[dict] | None = None, manifest: dict | None = None) -> Path:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_csv(case_dir / "timeseries.csv", rows or _rows())
    _write_csv(case_dir / "actuation_schedule.csv", _schedule())
    manifest_data = manifest or {
        "case_type": "jet_on",
        "coordinates": {
            "lift_direction_vector": [0, 0, 1],
            "drag_direction_vector": [1, 0, 0],
            "pitch_moment_axis": [0, 1, 0],
            "roll_moment_axis": [1, 0, 0],
        },
    }
    (case_dir / "case_manifest.yaml").write_text(yaml.safe_dump(manifest_data), encoding="utf-8")
    (case_dir / "quality_report.json").write_text(json.dumps({}), encoding="utf-8")
    return case_dir


def _write_boundary_mapping(path: Path, *, duplicate: bool = False) -> None:
    rows = []
    for jet_idx in range(1, 25):
        boundary = "Boundary01" if duplicate and jet_idx == 2 else f"Boundary{jet_idx:02d}"
        rows.append(
            {
                "unified_jet_id": jet_idx,
                "unified_switch_column": f"JET_{jet_idx:02d}",
                "unified_cmd_massflow_column": f"cmd_massflow_{jet_idx:02d}",
                "star_boundary_fully_qualified_candidate": boundary,
                "area_m2": "0.1",
                "normal_vector_xyz": "0,0,1",
            }
        )
    _write_csv(path, rows)


def _write_report_mapping(path: Path, *, bad_lift: bool = False) -> None:
    vectors = {
        "Fz_Total": "0,0,-1" if bad_lift else "0,0,1",
        "Drag_Total": "1,0,0",
        "Pitch_Moment": "0,1,0",
        "Roll_Moment": "1,0,0",
    }
    rows = [
        {
            "quantity_group": "vehicle",
            "unified_column_name": column,
            "direction_vector_xyz": vector,
            "coordinate_system": "vehicle",
            "integrated_surfaces": "body",
        }
        for column, vector in vectors.items()
    ]
    _write_csv(path, rows)


def test_reports_bad_lift_direction(tmp_path):
    case_dir = _write_case(tmp_path)
    boundary_path = tmp_path / "boundary.csv"
    report_path = tmp_path / "reports.csv"
    _write_boundary_mapping(boundary_path)
    _write_report_mapping(report_path, bad_lift=True)

    report = check_case(case_dir, boundary_mapping_path=boundary_path, report_mapping_path=report_path)

    assert any("Fz_Total" in issue["message"] for issue in report["categories"]["name_or_coordinate_errors"])


def test_reports_non_monotonic_time(tmp_path):
    case_dir = _write_case(tmp_path, rows=_rows(bad_time=True))
    boundary_path = tmp_path / "boundary.csv"
    report_path = tmp_path / "reports.csv"
    _write_boundary_mapping(boundary_path)
    _write_report_mapping(report_path)

    report = check_case(case_dir, boundary_mapping_path=boundary_path, report_mapping_path=report_path)

    assert any("not strictly increasing" in issue["message"] for issue in report["categories"]["format_errors"])


def test_reports_off_jet_actual_massflow_leak(tmp_path):
    case_dir = _write_case(tmp_path, rows=_rows(bad_massflow=True))
    boundary_path = tmp_path / "boundary.csv"
    report_path = tmp_path / "reports.csv"
    _write_boundary_mapping(boundary_path)
    _write_report_mapping(report_path)

    report = check_case(case_dir, boundary_mapping_path=boundary_path, report_mapping_path=report_path)

    assert any("JET is OFF" in issue["message"] for issue in report["categories"]["massflow_errors"])


def test_missing_actual_massflow_warns_without_substitution(tmp_path):
    case_dir = _write_case(tmp_path, rows=_rows(missing_actual=True))
    boundary_path = tmp_path / "boundary.csv"
    report_path = tmp_path / "reports.csv"
    _write_boundary_mapping(boundary_path)
    _write_report_mapping(report_path)

    report = check_case(case_dir, boundary_mapping_path=boundary_path, report_mapping_path=report_path)

    assert any("cmd_massflow was not used as a substitute" in issue["message"] for issue in report["categories"]["massflow_errors"])


def test_no_jet_does_not_treat_legacy_j_surface_force_as_massflow(tmp_path):
    rows = _rows()
    for row in rows:
        for jet_idx in range(1, 25):
            row[f"JET_{jet_idx:02d}"] = 0
            row[f"cmd_massflow_{jet_idx:02d}"] = 0.0
            row[f"actual_massflow_{jet_idx:02d}"] = 0.0
        row["Jet_Reaction_Z"] = 123.0
    case_dir = _write_case(tmp_path, rows=rows, manifest={"case_type": "no_jet"})
    boundary_path = tmp_path / "boundary.csv"
    report_path = tmp_path / "reports.csv"
    _write_boundary_mapping(boundary_path)
    _write_report_mapping(report_path)

    report = check_case(case_dir, boundary_mapping_path=boundary_path, report_mapping_path=report_path)

    assert not report["categories"]["massflow_errors"]


def test_duplicate_boundary_mapping_is_error(tmp_path):
    case_dir = _write_case(tmp_path)
    boundary_path = tmp_path / "boundary.csv"
    report_path = tmp_path / "reports.csv"
    _write_boundary_mapping(boundary_path, duplicate=True)
    _write_report_mapping(report_path)

    report = check_case(case_dir, boundary_mapping_path=boundary_path, report_mapping_path=report_path)

    assert any("same STAR boundary" in issue["message"] for issue in report["categories"]["name_or_coordinate_errors"])


def test_aggregated_report_does_not_call_nan_free_physics_correct(tmp_path):
    case_dir = _write_case(tmp_path)
    boundary_path = tmp_path / "boundary.csv"
    report_path = tmp_path / "reports.csv"
    _write_boundary_mapping(boundary_path)
    _write_report_mapping(report_path)

    report = check_cases([case_dir], boundary_mapping_path=boundary_path, report_mapping_path=report_path)
    serialized = json.dumps(report, ensure_ascii=False)

    assert "CSV没有NaN" not in serialized
    assert "CFD物理正确" not in serialized
    assert "force_accounting" in report["cases"][0]["summaries"]
