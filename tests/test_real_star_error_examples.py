from __future__ import annotations

import json
from pathlib import Path

from flow_control.star_ingest.case_data_loader import write_quality_report


ERROR_EXAMPLES = {
    "E01_format_time_nonmonotonic": "format_errors",
    "E02_name_coordinate_bad_lift_direction": "name_or_coordinate_errors",
    "E03_massflow_off_jet_actual_leak": "massflow_errors",
    "E04_force_accounting_missing_vehicle_force": "force_accounting_errors",
    "E05_numerical_instability_force_spike": "numerical_instability_warnings",
    "E06_physical_question_unconfirmed_direction": "physical_questions_for_haokun",
}


def test_real_star_error_examples_trigger_target_categories():
    root = Path("runs/error_case/ccm_physics")

    for case_id, category in ERROR_EXAMPLES.items():
        case_dir = root / case_id
        assert (case_dir / "raw_star").is_dir()
        assert (case_dir / "processed" / "timeseries.csv").is_file()
        assert (case_dir / "actuation_schedule.csv").is_file()
        assert (case_dir / "case_manifest.yaml").is_file()

        report = write_quality_report(case_dir, check_mode="ccm")
        assert report["check_profile"] == "ccm"
        assert "ccm_ingest_contract" in report
        assert "physics_consistency" in report

        physics = report["physics_consistency"]
        assert physics["summary"]["category_counts"][category] > 0

        serialized = json.dumps(report, ensure_ascii=False)
        assert "CSV没有NaN" not in serialized
        assert "CFD物理正确" not in serialized
