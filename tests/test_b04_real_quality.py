import csv
import json
from pathlib import Path

import yaml

from flow_control.star_ingest.b04_real_quality import check_real_case, run_delivery


REGIONS = ("underbody_lift_s1l", "underbody_lift_s1r", "underbody_lift_s2l", "underbody_lift_s2r", "underbody_lift_s3l", "underbody_lift_s3r")


def _write_case(path: Path, *, jet: int | None = None, include_underbody: bool = True, include_actual: bool = True) -> Path:
    path.mkdir(parents=True)
    (path / "processed").mkdir()
    (path / "figures").mkdir()
    (path / "logs").mkdir()
    manifest = {"case_id": path.name, "case_type": "jet_on" if jet else "no_jet"}
    (path / "case_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    rows = []
    schedules = []
    for index, time in enumerate((0.1, 0.2, 0.3)):
        row = {"physical_time": time, "window_id": index, **{column: 1.0 for column in REGIONS}, "vehicle_lift": 20.0, "vehicle_pitch_moment": 0.1, "vehicle_roll_moment": 0.2, "jet_momentum_reaction_z": 1.0 if jet else 0.0, "j_surface_force_z": 0.5}
        if include_underbody:
            row["underbody_6zone_lift"] = 6.0
        action = {"physical_time": time, "window_id": index, "t_start": time, "t_end": time + 0.1}
        for jet_index in range(1, 25):
            on = jet_index == jet
            row[f"J{jet_index:02d}_switch"] = int(on)
            row[f"J{jet_index:02d}_cmd_massflow_kg_s"] = 2.0 if on else 0.0
            action[f"J{jet_index:02d}_switch"] = int(on)
            action[f"J{jet_index:02d}_cmd_massflow_kg_s"] = 2.0 if on else 0.0
            if include_actual:
                row[f"J{jet_index:02d}_actual_massflow_kg_s"] = 2.0 if on else 0.0
        rows.append(row)
        schedules.append(action)
    for target, data in ((path / "processed" / "timeseries.csv", rows), (path / "actuation_schedule.csv", schedules)):
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    (path / "quality_report.json").write_text("{}", encoding="utf-8")
    return path


def test_valid_j02_case_passes(tmp_path: Path):
    report = check_real_case(_write_case(tmp_path / "J02", jet=2))
    assert report["summary"]["run_success_flag"] is True
    assert report["metrics"]["active_jets"] == [2]


def test_missing_values_are_errors_and_not_filled(tmp_path: Path):
    case = _write_case(tmp_path / "J06", jet=6, include_actual=False, include_underbody=False)
    report = check_real_case(case)
    assert report["summary"]["run_success_flag"] is False
    assert report["data_policy"]["missing_fields_filled_with_zero"] is False
    assert report["categories"]["massflow_errors"]
    assert report["categories"]["force_definition_errors"]


def test_delivery_preserves_existing_quality_report_and_writes_outputs(tmp_path: Path):
    case = _write_case(tmp_path / "G00")
    (case / "quality_report.json").write_text(json.dumps({"existing": 1}), encoding="utf-8")
    output = tmp_path / "reports"
    result = run_delivery([case], output_dir=output, expected_case_count=3)
    saved = json.loads((case / "quality_report.json").read_text(encoding="utf-8"))
    assert saved["existing"] == 1
    assert "B04_real_quality" in saved
    assert Path(result["summary_csv"]).is_file()
    assert "尚缺 2 个" in Path(result["blocking_issues_md"]).read_text(encoding="utf-8")
