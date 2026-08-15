import csv
import json

import pytest
import yaml

from flow_control.star_ingest.case_data_loader import write_quality_report
from flow_control.star_ingest.final_contract import validate_final_contract_columns


def test_final_contract_rejects_legacy_report_names_without_guessing():
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="Fz_Total"):
        validate_final_contract_columns(["physical_time", "Fz_Total"], table_kind="timeseries")


def test_final_contract_rejects_legacy_action_names_without_guessing():
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="JET_01"):
        validate_final_contract_columns(["JET_01", "cmd_massflow_01"], table_kind="actuation")


def test_final_contract_accepts_explicit_j_action_names():
    assert validate_final_contract_columns(
        ["J01_switch", "J01_cmd_massflow_kg_s", "J24_actual_massflow_kg_s"],
        table_kind="actuation",
    ) == []


def test_quality_report_runs_contract_when_manifest_is_final_strict(tmp_path):
    case_dir = tmp_path / "case"
    (case_dir / "processed").mkdir(parents=True)
    with (case_dir / "processed" / "timeseries.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["physical_time", "Fz_Total"])
        writer.writeheader()
        writer.writerow({"physical_time": 0.1, "Fz_Total": 1.0})
    with (case_dir / "actuation_schedule.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["physical_time", "JET_01", "cmd_massflow_01"])
        writer.writeheader()
        writer.writerow({"physical_time": 0.0, "JET_01": 0, "cmd_massflow_01": 0.0})
    (case_dir / "case_manifest.yaml").write_text(
        yaml.safe_dump({"validation": {"mode": "final_strict"}}), encoding="utf-8"
    )
    (case_dir / "quality_report.json").write_text(json.dumps({}), encoding="utf-8")

    with pytest.warns(DeprecationWarning):
        report = write_quality_report(
            case_dir,
            require_complete_schema=False,
            check_mode="mock",
        )

    assert report["final_contract"]["run_success_flag"] is False
    assert any("Fz_Total" in error for error in report["errors"])
