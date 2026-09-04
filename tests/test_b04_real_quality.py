import csv
import hashlib
import json
from pathlib import Path

import yaml

from flow_control.star_ingest.b04_real_quality import check_real_case, run_delivery


REGIONS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")


def _write_case(
    path: Path,
    *,
    jet: int | None = None,
    include_underbody: bool = True,
    include_actual: bool = True,
    ownership: str = "left_closed",
    validation_mode: str | None = None,
) -> Path:
    path.mkdir(parents=True)
    (path / "processed").mkdir()
    (path / "figures").mkdir()
    (path / "logs").mkdir()
    manifest = {
        "case_id": path.name,
        "case_type": "jet_on" if jet else "no_jet",
        # 本夹具的动作表 t_start 等于采样时间、window_id 随行递增，即 left_closed 语义。
        # 不声明会落到 legacy right_closed 默认值，从而误报 window_id 未对齐。
        "sample_ownership_rule": ownership,
    }
    if validation_mode is not None:
        manifest["validation_mode"] = validation_mode
    (path / "case_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    rows = []
    schedules = []
    for index, time in enumerate((0.1, 0.2, 0.3)):
        row = {"physical_time": time, "window_id": index, **{column: 1.0 for column in REGIONS}, "Fz_Total": 20.0, "Pitch_Moment": 0.1, "Roll_Moment": 0.2, "Jet_Momentum_Reaction_Z": 1.0 if jet else 0.0, "J_Surface_Force_Z": 0.5}
        if include_underbody:
            row["fz"] = 6.0
        action = {"physical_time": time, "window_id": index, "t_start": time, "t_end": time + 0.1}
        for jet_index in range(1, 25):
            on = jet_index == jet
            row[f"JET_{jet_index:02d}"] = int(on)
            row[f"cmd_massflow_{jet_index:02d}"] = 2.0 if on else 0.0
            action[f"JET_{jet_index:02d}"] = int(on)
            action[f"cmd_massflow_{jet_index:02d}"] = 2.0 if on else 0.0
            if include_actual:
                row[f"actual_massflow_{jet_index:02d}"] = 2.0 if on else 0.0
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
    case = _write_case(tmp_path / "J02", jet=2)
    report = check_real_case(case)
    assert report["summary"]["run_success_flag"] is True
    assert report["metrics"]["active_jets"] == [2]
    assert report["metrics"]["timeseries_sha256"] == hashlib.sha256(
        (case / "processed" / "timeseries.csv").read_bytes()
    ).hexdigest()
    assert report["metrics"]["case_manifest_sha256"] == hashlib.sha256(
        (case / "case_manifest.yaml").read_bytes()
    ).hexdigest()


def test_missing_values_are_errors_and_not_filled(tmp_path: Path):
    case = _write_case(tmp_path / "J06", jet=6, include_actual=False, include_underbody=False)
    report = check_real_case(case)
    assert report["summary"]["run_success_flag"] is False
    assert report["data_policy"]["missing_fields_filled_with_zero"] is False
    assert report["categories"]["massflow_errors"]
    assert report["metrics"]["force_definition"]["underbody_total_source"] == "missing_fz_report"


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


def _write_alignment_case(
    path: Path,
    *,
    schedule_row_count: int,
    samples: list[tuple[float, int, int, float]],
    ownership: str,
    validation_mode: str | None = None,
    jet: int = 2,
    dt: float = 0.1,
) -> Path:
    """构造一个只用于对齐检查的算例。

    ``samples`` 每项为 ``(physical_time, window_id 标签, JET 标签, 实测流量)``，
    以便分别制造标签正确与标签错误两种情形。
    """
    path.mkdir(parents=True)
    (path / "processed").mkdir()
    (path / "figures").mkdir()
    (path / "logs").mkdir()
    manifest = {
        "case_id": path.name,
        "case_type": "jet_on",
        "sample_ownership_rule": ownership,
    }
    if validation_mode is not None:
        manifest["validation_mode"] = validation_mode
    (path / "case_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    schedules = []
    for index in range(schedule_row_count):
        action = {
            "time": round(index * dt, 12),
            "window_id": index,
            "t_start": round(index * dt, 12),
            "t_end": round((index + 1) * dt, 12),
        }
        for jet_index in range(1, 25):
            on = jet_index == jet
            action[f"JET_{jet_index:02d}"] = int(on)
            action[f"cmd_massflow_{jet_index:02d}"] = 2.0 if on else 0.0
        schedules.append(action)

    rows = []
    for time, window_id, jet_label, actual in samples:
        row = {
            "physical_time": time,
            "window_id": window_id,
            **{column: 1.0 for column in REGIONS},
            "Fz_Total": 20.0,
            "fz": 6.0,
            "Pitch_Moment": 0.1,
            "Roll_Moment": 0.2,
            "Jet_Momentum_Reaction_Z": 1.0,
            "J_Surface_Force_Z": 0.5,
        }
        for jet_index in range(1, 25):
            on = jet_index == jet
            row[f"JET_{jet_index:02d}"] = jet_label if on else 0
            row[f"cmd_massflow_{jet_index:02d}"] = 2.0 if (on and jet_label) else 0.0
            row[f"actual_massflow_{jet_index:02d}"] = actual if on else 0.0
        rows.append(row)

    for target, data in (
        (path / "processed" / "timeseries.csv", rows),
        (path / "actuation_schedule.csv", schedules),
    ):
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    (path / "quality_report.json").write_text("{}", encoding="utf-8")
    return path


def test_left_closed_boundary_samples_are_not_misreported(tmp_path: Path):
    """采样正好落在 t_start 上时，下标配对会误报 window_id 未对齐。"""
    case = _write_alignment_case(
        tmp_path / "left",
        schedule_row_count=4,
        samples=[(0.1, 1, 1, 2.0), (0.2, 2, 1, 2.0), (0.3, 3, 1, 2.0)],
        ownership="left_closed",
        validation_mode="partial_timeseries",
    )

    report = check_real_case(case)

    assert [i for i in report["categories"]["time_errors"] if i["severity"] == "error"] == []
    assert report["metrics"]["time_alignment"]["sample_ownership_rule"] == "left_closed"
    assert report["metrics"]["time_alignment"]["sample_ownership_source"] == "manifest"
    assert report["metrics"]["time_alignment"]["aligned_row_count"] == 3
    assert report["metrics"]["time_alignment"]["unmatched_row_count"] == 0
    assert report["metrics"]["time_alignment"]["window_id_mismatch_count"] == 0


def test_right_closed_cli_samples_still_pass(tmp_path: Path):
    """CLI 宏在推进到 t_end 后采样，边界样本归属关闭该边界的那个窗口。"""
    case = _write_alignment_case(
        tmp_path / "right",
        schedule_row_count=4,
        samples=[(0.1, 0, 1, 2.0), (0.2, 1, 1, 2.0), (0.3, 2, 1, 2.0)],
        ownership="right_closed",
        validation_mode="partial_timeseries",
    )

    report = check_real_case(case)

    assert [i for i in report["categories"]["time_errors"] if i["severity"] == "error"] == []
    assert report["metrics"]["time_alignment"]["sample_ownership_rule"] == "right_closed"
    assert report["metrics"]["time_alignment"]["window_id_mismatch_count"] == 0


def test_undeclared_ownership_is_reported_as_legacy_default(tmp_path: Path):
    case = _write_alignment_case(
        tmp_path / "legacy",
        schedule_row_count=4,
        samples=[(0.1, 0, 1, 2.0), (0.2, 1, 1, 2.0), (0.3, 2, 1, 2.0)],
        ownership="right_closed",
    )
    manifest = yaml.safe_load((case / "case_manifest.yaml").read_text(encoding="utf-8"))
    manifest.pop("sample_ownership_rule")
    (case / "case_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    report = check_real_case(case)

    assert report["metrics"]["time_alignment"]["sample_ownership_rule"] == "right_closed"
    assert report["metrics"]["time_alignment"]["sample_ownership_source"] == "legacy_default"


def test_embedded_declaration_is_honoured_not_mislabelled_legacy(tmp_path: Path):
    """organizer 会把自动推断出的 embedded 写回 manifest，审计侧必须承认它是真实声明。"""
    case = _write_alignment_case(
        tmp_path / "embedded",
        schedule_row_count=3,
        samples=[(0.1, 0, 1, 2.0), (0.2, 1, 1, 2.0), (0.3, 2, 1, 2.0)],
        ownership="embedded",
    )

    report = check_real_case(case)

    alignment = report["metrics"]["time_alignment"]
    assert alignment["sample_ownership_rule"] == "embedded"
    assert alignment["sample_ownership_source"] == "manifest"
    assert alignment["aligned_row_count"] == 3
    assert alignment["unmatched_row_count"] == 0
    assert [i for i in report["categories"]["time_errors"] if i["severity"] == "error"] == []


def test_embedded_window_id_contradicting_time_blocks(tmp_path: Path):
    case = _write_alignment_case(
        tmp_path / "embedded_bad",
        schedule_row_count=3,
        # 第二个样本自称属于 window 0，但 t=0.2 已超出该窗口跨度 [0.0, 0.1]
        samples=[(0.1, 0, 1, 2.0), (0.2, 0, 1, 2.0), (0.3, 2, 1, 2.0)],
        ownership="embedded",
    )

    report = check_real_case(case)

    assert report["summary"]["run_success_flag"] is False
    # 时间本身是能归属的，错的是行自带的 window_id，因此计入 mismatch 而非 unmatched
    assert report["metrics"]["time_alignment"]["window_id_mismatch_count"] == 1
    assert report["metrics"]["time_alignment"]["unmatched_row_count"] == 0
    assert any(
        "矛盾" in issue["message"] for issue in report["categories"]["time_errors"]
    )


def test_legal_partial_prefix_is_warning_only(tmp_path: Path):
    """timeseries 是动作表的连续前缀且已声明 partial_timeseries 时不阻塞。"""
    case = _write_alignment_case(
        tmp_path / "prefix",
        schedule_row_count=6,
        samples=[(0.1, 1, 1, 2.0), (0.2, 2, 1, 2.0), (0.3, 3, 1, 2.0)],
        ownership="left_closed",
        validation_mode="partial_timeseries",
    )

    report = check_real_case(case)

    warnings = [i for i in report["categories"]["time_errors"] if i["severity"] == "warning"]
    errors = [i for i in report["categories"]["time_errors"] if i["severity"] == "error"]
    assert errors == []
    assert len(warnings) == 1
    assert report["summary"]["run_success_flag"] is True
    alignment = report["metrics"]["time_alignment"]
    assert alignment["partial_completion_ratio"] == 0.5
    assert alignment["result_end_time"] == 0.3
    assert alignment["scheduled_end_time"] == 0.6


def test_partial_prefix_without_manifest_declaration_still_blocks(tmp_path: Path):
    case = _write_alignment_case(
        tmp_path / "undeclared_prefix",
        schedule_row_count=6,
        samples=[(0.1, 1, 1, 2.0), (0.2, 2, 1, 2.0), (0.3, 3, 1, 2.0)],
        ownership="left_closed",
    )

    report = check_real_case(case)

    assert report["summary"]["run_success_flag"] is False
    assert any(i["severity"] == "error" for i in report["categories"]["time_errors"])


def test_missing_leading_rows_is_not_a_prefix(tmp_path: Path):
    case = _write_alignment_case(
        tmp_path / "late_start",
        schedule_row_count=6,
        samples=[(0.3, 3, 1, 2.0), (0.4, 4, 1, 2.0)],
        ownership="left_closed",
        validation_mode="partial_timeseries",
    )

    report = check_real_case(case)

    assert report["summary"]["run_success_flag"] is False
    assert any("连续前缀" in i["message"] for i in report["categories"]["time_errors"])


def test_hole_in_the_middle_is_an_error(tmp_path: Path):
    case = _write_alignment_case(
        tmp_path / "hole",
        schedule_row_count=6,
        samples=[(0.1, 1, 1, 2.0), (0.3, 3, 1, 2.0)],
        ownership="left_closed",
        validation_mode="partial_timeseries",
    )

    report = check_real_case(case)

    assert report["summary"]["run_success_flag"] is False
    assert any("连续前缀" in i["message"] for i in report["categories"]["time_errors"])


def test_timeseries_longer_than_schedule_blocks(tmp_path: Path):
    case = _write_alignment_case(
        tmp_path / "overrun",
        schedule_row_count=2,
        samples=[(0.1, 1, 1, 2.0), (0.2, 1, 1, 2.0), (0.3, 1, 1, 2.0)],
        ownership="left_closed",
        validation_mode="partial_timeseries",
    )

    report = check_real_case(case)

    assert report["summary"]["run_success_flag"] is False
    assert any("多于" in i["message"] for i in report["categories"]["time_errors"])


def test_corrected_labels_remove_closed_jet_leak_report(tmp_path: Path):
    """标签修正后关阀泄漏报警消失；错误标签下同一条数据仍会报。"""
    corrected = _write_alignment_case(
        tmp_path / "corrected",
        schedule_row_count=4,
        samples=[(0.1, 1, 1, 2.0), (0.2, 2, 1, 2.0), (0.3, 3, 1, 2.0)],
        ownership="left_closed",
    )
    mislabeled = _write_alignment_case(
        tmp_path / "mislabeled",
        schedule_row_count=4,
        # 第一个样本的 JET 标签被错标成关闭，而实测流量仍为 2.0
        samples=[(0.1, 1, 0, 2.0), (0.2, 2, 1, 2.0), (0.3, 3, 1, 2.0)],
        ownership="left_closed",
    )

    assert check_real_case(corrected)["categories"]["massflow_errors"] == []
    assert any(
        "未接近 0" in issue["message"]
        for issue in check_real_case(mislabeled)["categories"]["massflow_errors"]
    )
