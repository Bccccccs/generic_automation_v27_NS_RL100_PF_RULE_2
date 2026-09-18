from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from flow_control.b53_dataset import B53Config, CaseSource, build_b53_outputs
from flow_control.sampling import SAMPLE_OWNERSHIP_RIGHT_CLOSED, resolve_sample_window


REGIONS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
REPRESENTATIVE_JETS = (2, 3, 9, 11, 17, 19)
OUTPUT_FILES = (
    "training_dataset.csv",
    "validation_dataset.csv",
    "B03_data_quality_summary.csv",
    "B03_six_jet_response_summary.csv",
    "B03_anomalous_windows.csv",
)
REJECTION_CODES = {
    "missing_actual": "ACTUAL_MASSFLOW_MISSING",
    "baseline_drift": "BASELINE_DRIFT",
    "low_snr": "RESPONSE_BELOW_NOISE",
    "time_misalignment": "TIME_MISALIGNMENT",
}


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _fieldnames(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle).fieldnames or ())


def _report_value(report: Any, name: str) -> Any:
    if isinstance(report, dict):
        return report[name]
    return getattr(report, name)


def _jet_number(value: object) -> int:
    digits = "".join(character for character in str(value) if character.isdigit())
    return int(digits)


def _config(**overrides: object) -> B53Config:
    values: dict[str, object] = {
        "representative_jets": REPRESENTATIVE_JETS,
        "baseline_duration_s": 0.4,
        "minimum_initial_discard_s": 0.3,
        "recovery_search_s": 0.55,
        "minimum_baseline_samples": 6,
        "minimum_response_samples": 4,
        "time_alignment_tolerance_s": 0.011,
        "baseline_drift_noise_multiplier": 4.0,
        "baseline_drift_relative_limit": 0.03,
        "response_sigma_threshold": 5.0,
        "response_hold_s": 0.1,
        "recovery_hold_s": 0.1,
        "minimum_snr_linear": 5.0,
        "max_massflow_lag_s": 0.1,
        "validation_fraction": 0.2,
        "split_seed": 53,
    }
    values.update(overrides)
    return replace(B53Config.default(), **values)


def _action_row(
    window_id: int,
    start: float,
    end: float,
    *,
    active_jet: int | None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "time": start,
        "window_id": window_id,
        "t_start": start,
        "t_end": end,
    }
    for jet in range(1, 25):
        is_active = jet == active_jet
        row[f"JET_{jet:02d}"] = int(is_active)
        row[f"cmd_massflow_{jet:02d}"] = 1.0 if is_active else 0.0
    return row


def _schedule(*, time_misalignment: bool) -> list[dict[str, object]]:
    rows = [
        _action_row(0, 0.0, 0.5, active_jet=None),
        _action_row(1, 0.5, 1.0, active_jet=None),
        _action_row(2, 1.0, 1.4, active_jet=2),
    ]
    if time_misalignment:
        # A gap well above the configured tolerance leaves the t=1.70 solver
        # sample without a source action window.  The response itself remains
        # otherwise valid, keeping this fixture specific to alignment quality.
        rows.extend(
            (
                _action_row(3, 1.4, 1.65, active_jet=None),
                _action_row(4, 1.75, 2.0, active_jet=None),
            )
        )
    else:
        rows.append(_action_row(3, 1.4, 2.0, active_jet=None))
    return rows


def _timeseries(
    *,
    response_amplitude: float,
    baseline_drift_rate: float,
    include_actual_jet_02: bool,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    region_baselines = dict(zip(REGIONS, (10.0, 20.0, 30.0, 40.0, 50.0, 60.0)))
    for index in range(1, 41):
        # Like the 0816 data, measurements are solver-step right endpoints,
        # while action rows identify intervals by their left endpoints.
        physical_time = round(index * 0.05, 10)
        noise = 0.01 if index % 2 else -0.01
        startup_transient = max(0.0, 0.3 - physical_time) * 1000.0
        drift = (
            baseline_drift_rate * (physical_time - 0.55)
            if 0.55 <= physical_time <= 1.0
            else 0.0
        )
        if 1.1 <= physical_time <= 1.4:
            response = response_amplitude
        elif 1.4 < physical_time < 1.5:
            response = response_amplitude * (1.5 - physical_time) / 0.1
        else:
            response = 0.0

        row: dict[str, object] = {
            "physical_time": physical_time,
            "solver_status": "success",
            "case_stage": "synthetic_star",
        }
        for jet in range(1, 25):
            if jet == 2 and not include_actual_jet_02:
                continue
            row[f"actual_massflow_{jet:02d}"] = (
                1.0 if jet == 2 and 1.05 <= physical_time <= 1.4 else 0.0
            )
        for region, baseline in region_baselines.items():
            row[region] = baseline + noise + startup_transient + drift + response
        row["Fz_Total"] = sum(float(row[region]) for region in REGIONS)
        rows.append(row)
    return rows


def _write_case(root: Path, case_id: str, *, defect: str | None = None) -> Path:
    case_dir = root / case_id
    (case_dir / "processed").mkdir(parents=True)
    _write_csv(
        case_dir / "actuation_schedule.csv",
        _schedule(time_misalignment=defect == "time_misalignment"),
    )
    _write_csv(
        case_dir / "processed" / "timeseries.csv",
        _timeseries(
            response_amplitude=0.02 if defect == "low_snr" else 2.0,
            baseline_drift_rate=4.0 if defect == "baseline_drift" else 0.0,
            include_actual_jet_02=defect != "missing_actual",
        ),
    )
    (case_dir / "case_manifest.yaml").write_text(
        "\n".join(
            (
                f"case_id: {case_id}",
                "case_type: jet_on",
                "case_stage: starccm_output_organized",
                "star:",
                "  sim_file_identifier: synthetic_b53.sim",
                "  sim_file_hash_sha256: " + "b" * 64,
                "time_step: 0.05",
                "sampling_interval: 0.05",
            )
        )
        + "\n",
        encoding="utf-8",
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
    return case_dir


def _case_reason_codes(path: Path, case_id: str) -> set[str]:
    codes: set[str] = set()
    for row in _read_csv(path):
        if row.get("source_case_id", row.get("case_id")) != case_id:
            continue
        raw = row.get("reason_codes", row.get("reason_code", ""))
        normalized = raw.replace(";", ",").replace("|", ",")
        codes.update(code.strip() for code in normalized.split(",") if code.strip())
    return codes


def test_empty_sources_write_all_contract_csvs_and_six_jet_no_data_rows(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "delivery"

    report = build_b53_outputs(_config(), output_dir, sources=[])

    assert _report_value(report, "overall_status") == "NO_DATA"
    assert _report_value(report, "training_rows") == 0
    assert _report_value(report, "validation_rows") == 0
    for filename in OUTPUT_FILES:
        path = output_dir / filename
        assert path.is_file(), filename
        assert _fieldnames(path), f"{filename} must keep a stable header before real data arrive"

    assert _read_csv(output_dir / "training_dataset.csv") == []
    assert _read_csv(output_dir / "validation_dataset.csv") == []
    jet_rows = _read_csv(output_dir / "B03_six_jet_response_summary.csv")
    assert len(jet_rows) == 6
    assert {_jet_number(row["jet_id"]) for row in jet_rows} == set(
        REPRESENTATIVE_JETS
    )
    assert {row["status"] for row in jet_rows} == {"NO_DATA"}


def test_stable_response_is_accepted_with_metrics_and_star_traceability(
    tmp_path: Path,
) -> None:
    case_dir = _write_case(tmp_path, "STAR_case_good")
    output_dir = tmp_path / "delivery"

    report = build_b53_outputs(
        _config(), output_dir, sources=[CaseSource(case_dir, "train")]
    )

    training_rows = _read_csv(output_dir / "training_dataset.csv")
    assert _report_value(report, "training_rows") == len(training_rows) > 0
    assert _report_value(report, "validation_rows") == 0
    assert _read_csv(output_dir / "validation_dataset.csv") == []

    trace_columns = {
        "sample_id",
        "split",
        "source_case_id",
        "source_case_dir",
        "source_timeseries_row",
        "physical_time",
        "action_window_id",
        "event_id",
        "phase",
        "time_from_action_s",
        "jet_id",
        "initial_transient_cutoff_s",
    }
    assert trace_columns <= set(training_rows[0])
    for row in training_rows:
        assert all(row[column] != "" for column in trace_columns)
        assert row["source_case_id"] == case_dir.name
        assert Path(row["source_case_dir"]).resolve() == case_dir.resolve()
        assert row["split"] == "training"
        assert float(row["physical_time"]) >= float(row["initial_transient_cutoff_s"])
        assert float(row["physical_time"]) >= 0.3
        assert int(float(row["source_timeseries_row"])) >= 1

    assert len({row["sample_id"] for row in training_rows}) == len(training_rows)
    assert any(float(row["cmd_massflow_02"]) > 0.0 for row in training_rows)
    assert any(float(row["actual_massflow_02"]) > 0.0 for row in training_rows)
    for region in REGIONS:
        assert region in training_rows[0]
        assert f"delta_{region}" in training_rows[0]
        # The deliberately huge t<0.3 startup load must never leak into samples.
        assert max(abs(float(row[region])) for row in training_rows) < 100.0

    response_rows = _read_csv(output_dir / "B03_six_jet_response_summary.csv")
    assert len(response_rows) == 6
    jet_02 = next(row for row in response_rows if _jet_number(row["jet_id"]) == 2)
    common_metrics = {
        "dominant_region",
        "median_response_delay_s",
        "median_peak_delta_N",
        "median_recovery_time_s",
        "median_snr_linear",
        "median_snr_db",
    }
    assert common_metrics <= set(jet_02)
    assert all(jet_02[column] != "" for column in common_metrics)
    assert float(jet_02["median_response_delay_s"]) >= 0.0
    assert abs(float(jet_02["median_peak_delta_N"])) > 1.0
    assert float(jet_02["median_recovery_time_s"]) >= 0.0
    assert float(jet_02["median_snr_linear"]) >= 5.0
    for region in REGIONS:
        columns = {
            f"{region}_median_peak_delta_N",
            f"{region}_median_response_delay_s",
            f"{region}_median_recovery_time_s",
            f"{region}_median_snr_linear",
            f"{region}_median_snr_db",
        }
        assert columns <= set(jet_02)
        assert all(jet_02[column] != "" for column in columns)

    assert _case_reason_codes(
        output_dir / "B03_data_quality_summary.csv", case_dir.name
    ) == set()
    assert _read_csv(output_dir / "B03_anomalous_windows.csv") == []


@pytest.mark.parametrize(("defect", "expected_code"), REJECTION_CODES.items())
def test_each_quality_fault_atomically_rejects_the_source_window(
    tmp_path: Path,
    defect: str,
    expected_code: str,
) -> None:
    case_dir = _write_case(tmp_path, f"STAR_bad_{defect}", defect=defect)
    output_dir = tmp_path / "delivery"

    report = build_b53_outputs(
        _config(), output_dir, sources=[CaseSource(case_dir, "train")]
    )

    assert _report_value(report, "training_rows") == 0
    assert _report_value(report, "validation_rows") == 0
    assert _read_csv(output_dir / "training_dataset.csv") == []
    quality_codes = _case_reason_codes(
        output_dir / "B03_data_quality_summary.csv", case_dir.name
    )
    anomaly_codes = _case_reason_codes(
        output_dir / "B03_anomalous_windows.csv", case_dir.name
    )
    assert expected_code in quality_codes
    assert expected_code in anomaly_codes
    assert not (set(REJECTION_CODES.values()) - {expected_code}) & quality_codes


def test_explicit_case_split_never_leaks_between_training_and_validation(
    tmp_path: Path,
) -> None:
    train_case = _write_case(tmp_path, "STAR_train_case")
    validation_case = _write_case(tmp_path, "STAR_validation_case")
    output_dir = tmp_path / "delivery"

    report = build_b53_outputs(
        _config(),
        output_dir,
        sources=(
            CaseSource(train_case, "train"),
            CaseSource(validation_case, "validation"),
        ),
    )

    training_rows = _read_csv(output_dir / "training_dataset.csv")
    validation_rows = _read_csv(output_dir / "validation_dataset.csv")
    assert _report_value(report, "training_rows") == len(training_rows) > 0
    assert _report_value(report, "validation_rows") == len(validation_rows) > 0
    assert {row["source_case_id"] for row in training_rows} == {train_case.name}
    assert {row["source_case_id"] for row in validation_rows} == {
        validation_case.name
    }
    assert {row["split"] for row in training_rows} == {"training"}
    assert {row["split"] for row in validation_rows} == {"validation"}
    assert {row["sample_id"] for row in training_rows}.isdisjoint(
        row["sample_id"] for row in validation_rows
    )


def _quality_row(output_dir: Path, case_id: str) -> dict[str, str]:
    for row in _read_csv(output_dir / "B03_data_quality_summary.csv"):
        if row.get("source_case_id", row.get("case_id")) == case_id:
            return row
    raise AssertionError(f"质量汇总里没有 {case_id} 的行")


def _declare_ownership(case_dir: Path, ownership: str) -> None:
    manifest_path = case_dir / "case_manifest.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + f"sample_ownership_rule: {ownership}\n",
        encoding="utf-8",
    )


def _stamp_right_closed_window_ids(case_dir: Path) -> None:
    """按 (t_start,t_end] 给 timeseries 盖上正确的 window_id，使语义差异可观测。"""
    schedule = _read_csv(case_dir / "actuation_schedule.csv")
    path = case_dir / "processed" / "timeseries.csv"
    rows = _read_csv(path)
    for row in rows:
        index = resolve_sample_window(
            schedule, float(row["physical_time"]), ownership=SAMPLE_OWNERSHIP_RIGHT_CLOSED
        )
        row["window_id"] = schedule[index]["window_id"]
    _write_csv(path, rows)


def test_undeclared_ownership_keeps_right_closed_and_is_marked_legacy(tmp_path: Path) -> None:
    """历史 CLI Case 未声明语义时保持 right_closed 兼容，但必须标为 legacy default。"""
    case_dir = _write_case(tmp_path, "STAR_legacy_cli")
    _stamp_right_closed_window_ids(case_dir)
    output_dir = tmp_path / "legacy"

    build_b53_outputs(_config(), output_dir, sources=(CaseSource(case_dir, "train"),))

    row = _quality_row(output_dir, "STAR_legacy_cli")
    assert row["time_alignment_mode"] == "right_closed_(t_start,t_end]_legacy_default"
    assert _read_csv(output_dir / "training_dataset.csv")


def test_declared_left_closed_drives_alignment_not_just_the_label(tmp_path: Path) -> None:
    """同一份右闭区间形态的数据声明 left_closed 后必须错位失败。

    只断言标签字符串不足以证明声明生效；这里用对齐结果反证模式确实被采用。
    """
    case_dir = _write_case(tmp_path, "STAR_declared_left")
    _stamp_right_closed_window_ids(case_dir)
    _declare_ownership(case_dir, "left_closed")
    output_dir = tmp_path / "declared"

    build_b53_outputs(_config(), output_dir, sources=(CaseSource(case_dir, "train"),))

    row = _quality_row(output_dir, "STAR_declared_left")
    assert row["time_alignment_mode"] == "left_closed_[t_start,t_end)"
    assert "TIME_MISALIGNMENT" in _case_reason_codes(
        output_dir / "B03_data_quality_summary.csv", "STAR_declared_left"
    )
    assert _read_csv(output_dir / "training_dataset.csv") == []


def test_schedule_row_without_window_id_is_not_fabricated_from_index() -> None:
    """缺 window_id 不得用行下标伪造：那会让 embedded 校验自证一致而静默放行。"""
    from flow_control.b53_dataset.builder import _parse_schedule

    intervals, errors = _parse_schedule(
        [{"time": 0.0, "t_start": 0.0, "t_end": 0.1}], 1.0e-8
    )

    assert intervals == []
    assert any("window_id" in error for error in errors)
