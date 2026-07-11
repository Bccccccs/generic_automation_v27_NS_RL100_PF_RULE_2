import csv
import json

from flow_control.star_ingest.ccm_package import package_ccm_run_case


def test_package_ccm_run_case_generates_standard_timeseries_and_quality_report(tmp_path):
    schedule_path = tmp_path / "actuation_schedule.csv"
    raw_timeseries_path = tmp_path / "flow_control_timeseries.csv"
    case_dir = tmp_path / "ccm_case"

    with schedule_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "physical_time",
                "window_id",
                "t_start",
                "t_end",
                "JET_01",
                "cmd_massflow_01",
                *[f"JET_{idx:02d}" for idx in range(2, 25)],
                *[f"cmd_massflow_{idx:02d}" for idx in range(2, 25)],
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "physical_time": 0.1,
                "window_id": 0,
                "t_start": 0.0,
                "t_end": 0.1,
                "JET_01": 1,
                "cmd_massflow_01": 0.02,
                **{f"JET_{idx:02d}": 0 for idx in range(2, 25)},
                **{f"cmd_massflow_{idx:02d}": 0.0 for idx in range(2, 25)},
            }
        )

    with raw_timeseries_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "physical_time",
                "window_id",
                "fc_load_S1L",
                "fc_load_S1R",
                "fc_load_S2L",
                "fc_load_S2R",
                "fc_load_S3L",
                "fc_load_S3R",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "physical_time": 0.1,
                "window_id": 0,
                "fc_load_S1L": 1.0,
                "fc_load_S1R": 2.0,
                "fc_load_S2L": 3.0,
                "fc_load_S2R": 4.0,
                "fc_load_S3L": 5.0,
                "fc_load_S3R": 6.0,
            }
        )

    result = package_ccm_run_case(
        ccm_timeseries_path=raw_timeseries_path,
        schedule_path=schedule_path,
        case_dir=case_dir,
    )

    assert result["timeseries_path"].exists()
    assert result["quality_report_path"].exists()
    report = json.loads(result["quality_report_path"].read_text(encoding="utf-8"))
    assert report["check_mode"] == "ccm"
    assert any("Drag_Total" in error for error in report["errors"])
    with result["timeseries_path"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["Fz_Total"] == "21.0"
    assert rows[0]["actual_massflow_01"] == "0.02"
