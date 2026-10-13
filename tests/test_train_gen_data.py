"""train_gen_data：manifest 瞬态裁剪与训练表生成的单元测试。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from flow_control.train_gen_data import SUMMARY_FIELDS, TRAINING_TABLE_FIELDS
from flow_control.train_gen_data.builder import build_training_tables, main

REGIONS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
ACTUALS = tuple(f"actual_massflow_{index:02d}" for index in range(1, 25))
TIMES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
SCHEDULE_FIELDS = ("window_id", "time", "t_start", "t_end", "JET_02", "cmd_massflow_02")


def _write_csv(path: Path, fields, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _schedule_rows(gap: bool) -> list[dict[str, str]]:
    second_start = "0.4" if gap else "0.3"
    return [
        {
            "window_id": "0",
            "time": "0.0",
            "t_start": "0.0",
            "t_end": "0.3",
            "JET_02": "0",
            "cmd_massflow_02": "0.0",
        },
        {
            "window_id": "1",
            "time": second_start,
            "t_start": second_start,
            "t_end": "0.6",
            "JET_02": "1",
            "cmd_massflow_02": "0.01",
        },
    ]


def _true_window(time: float, ownership: str) -> int:
    if ownership == "right_closed":
        return 0 if time <= 0.3 else 1
    return 0 if time < 0.3 else 1


def _make_case(
    tmp_path: Path,
    name: str,
    *,
    ownership: str = "right_closed",
    crop: float = 0.2,
    omit_crop: bool = False,
    times: list[float] | None = None,
    window_ids: bool = True,
    window_id_override: dict[float, str] | None = None,
    schedule_gap: bool = False,
    drop_columns: tuple[str, ...] = (),
    overrides: dict[float, dict[str, str]] | None = None,
) -> Path:
    times = TIMES if times is None else times
    case_dir = tmp_path / name
    (case_dir / "processed").mkdir(parents=True)
    ts_fields = ["physical_time"]
    if window_ids:
        ts_fields.append("window_id")
    ts_fields.extend(ACTUALS)
    ts_fields.extend(REGIONS)
    ts_fields.append("Fz_Total")
    ts_fields = [column for column in ts_fields if column not in drop_columns]
    rows = []
    for time in times:
        row: dict[str, str] = {"physical_time": f"{time:.4f}"}
        if window_ids:
            override = (window_id_override or {}).get(time)
            row["window_id"] = override if override is not None else str(_true_window(time, ownership))
        for column in ACTUALS:
            row[column] = "1.0e-4"
        for region in REGIONS:
            row[region] = "-10.0"
        row["Fz_Total"] = "-60.0"
        row.update((overrides or {}).get(time, {}))
        rows.append({key: value for key, value in row.items() if key in ts_fields})
    _write_csv(case_dir / "processed" / "timeseries.csv", ts_fields, rows)
    _write_csv(case_dir / "actuation_schedule.csv", SCHEDULE_FIELDS, _schedule_rows(schedule_gap))
    manifest: dict = {"case_id": name, "sample_ownership_rule": ownership}
    if not omit_crop:
        manifest["initial_transient_crop"] = {
            "end_time_s": crop,
            "keep_rule": f"physical_time >= {crop} s",
        }
    (case_dir / "case_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )
    return case_dir


def _summary_row(output_dir: Path, index: int = 0) -> dict[str, str]:
    _, rows = _read_csv(output_dir / "transient_crop_summary.csv")
    return rows[index]


def test_crop_uses_manifest_end_time_and_table_columns(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, "case_a")
    output = tmp_path / "out"

    report = build_training_tables([case_dir], output)

    assert report["ok"] == 1 and report["rejected"] == 0
    assert report["total_kept_rows"] == 4
    fields, rows = _read_csv(output / "case_a" / "training_table.csv")
    assert fields == list(TRAINING_TABLE_FIELDS)
    assert [float(row["physical_time"]) for row in rows] == pytest.approx([0.2, 0.3, 0.4, 0.5])
    # right_closed 语义下 t=0.3 仍归属窗口 0，JET/cmd 以动作表为权威
    assert [row["JET_02"] for row in rows] == ["0", "0", "1", "1"]
    assert [row["cmd_massflow_02"] for row in rows] == ["0.0", "0.0", "0.01", "0.01"]
    assert rows[0]["source_timeseries_row"] == "4"
    summary = _summary_row(output)
    assert summary["status"] == "PASS"
    assert summary["cutoff_s"] == "0.2" and summary["cutoff_source"] == "manifest"
    assert summary["removed_transient_rows"] == "2" and summary["kept_rows"] == "4"
    assert summary["dropped_missing_value_rows"] == "0"


def test_missing_crop_field_falls_back_to_half_second(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, "case_nocrop", omit_crop=True)
    output = tmp_path / "out"

    build_training_tables([case_dir], output)

    _, rows = _read_csv(output / "case_nocrop" / "training_table.csv")
    assert [float(row["physical_time"]) for row in rows] == pytest.approx([0.5])
    summary = _summary_row(output)
    assert summary["cutoff_s"] == "0.5"
    assert summary["cutoff_source"] == "fallback_default"
    assert "回退" in summary["notes"]


def test_ownership_boundary_sample_window(tmp_path: Path) -> None:
    right = _make_case(tmp_path, "rc", ownership="right_closed", crop=0.0)
    left = _make_case(tmp_path, "lc", ownership="left_closed", crop=0.0)
    output = tmp_path / "out"

    report = build_training_tables([right, left], output)

    assert report["ok"] == 2
    _, right_rows = _read_csv(output / "rc" / "training_table.csv")
    _, left_rows = _read_csv(output / "lc" / "training_table.csv")
    boundary_right = next(row for row in right_rows if float(row["physical_time"]) == pytest.approx(0.3))
    boundary_left = next(row for row in left_rows if float(row["physical_time"]) == pytest.approx(0.3))
    assert boundary_right["JET_02"] == "0" and boundary_right["window_id"] == "0"
    assert boundary_left["JET_02"] == "1" and boundary_left["window_id"] == "1"
    assert _summary_row(output, 0)["removed_transient_rows"] == "0"


def test_embedded_window_id_contradiction_rejects(tmp_path: Path) -> None:
    case_dir = _make_case(
        tmp_path,
        "emb_bad",
        ownership="embedded",
        times=[0.1, 0.25, 0.4],
        window_id_override={0.25: "1"},
    )
    output = tmp_path / "out"

    report = build_training_tables([case_dir], output)

    assert report == {**report, "ok": 0, "rejected": 1, "total_kept_rows": 0}
    assert not (output / "emb_bad" / "training_table.csv").exists()
    summary = _summary_row(output)
    assert summary["status"] == "REJECTED"
    assert summary["reason_codes"] == "TIME_MISALIGNMENT"


def test_embedded_missing_window_id_column_rejects(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, "emb_nowid", ownership="embedded", window_ids=False)
    output = tmp_path / "out"

    report = build_training_tables([case_dir], output)

    assert report["rejected"] == 1
    assert _summary_row(output)["reason_codes"] == "TIME_MISALIGNMENT"


def test_missing_value_rows_dropped_and_counted(tmp_path: Path) -> None:
    case_dir = _make_case(
        tmp_path,
        "case_gaps",
        overrides={0.3: {"actual_massflow_05": ""}, 0.4: {"Fz_S2L": ""}},
    )
    output = tmp_path / "out"

    report = build_training_tables([case_dir], output)

    assert report["total_kept_rows"] == 2
    _, rows = _read_csv(output / "case_gaps" / "training_table.csv")
    assert [float(row["physical_time"]) for row in rows] == pytest.approx([0.2, 0.5])
    summary = _summary_row(output)
    assert summary["dropped_missing_value_rows"] == "2"
    assert summary["kept_rows"] == "2"


def test_cutoff_beyond_last_time_rejects(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, "case_late", crop=9.9)
    output = tmp_path / "out"

    report = build_training_tables([case_dir], output)

    assert report["rejected"] == 1 and report["total_kept_rows"] == 0
    assert not (output / "case_late" / "training_table.csv").exists()
    summary = _summary_row(output)
    assert summary["reason_codes"] == "NO_ROWS_AFTER_CROP"
    assert summary["removed_transient_rows"] == "6"


def test_zero_cutoff_keeps_all(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, "case_zero", crop=0.0)
    output = tmp_path / "out"

    build_training_tables([case_dir], output)

    summary = _summary_row(output)
    assert summary["removed_transient_rows"] == "0"
    assert summary["kept_rows"] == "6"


def test_non_monotonic_time_rejects(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, "case_nonmono", times=[0.1, 0.05, 0.2, 0.3, 0.4, 0.5])
    output = tmp_path / "out"

    report = build_training_tables([case_dir], output)

    assert report["rejected"] == 1
    assert _summary_row(output)["reason_codes"] == "TIME_MISALIGNMENT"


def test_schedule_gap_rejects(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, "case_gap", schedule_gap=True)
    output = tmp_path / "out"

    report = build_training_tables([case_dir], output)

    assert report["rejected"] == 1
    assert _summary_row(output)["reason_codes"] == "TIME_MISALIGNMENT"


def test_required_column_missing_rejects(tmp_path: Path) -> None:
    force_case = _make_case(tmp_path, "case_noforce", drop_columns=("Fz_S3R",))
    actual_case = _make_case(tmp_path, "case_noactual", drop_columns=("actual_massflow_10",))
    output = tmp_path / "out"

    report = build_training_tables([force_case, actual_case], output)

    assert report["rejected"] == 2
    assert _summary_row(output, 0)["reason_codes"] == "SIX_REGION_FORCE_MISSING"
    assert _summary_row(output, 1)["reason_codes"] == "ACTUAL_MASSFLOW_MISSING"


def test_main_report_and_require_data(tmp_path: Path, capsys) -> None:
    good = _make_case(tmp_path, "case_ok")
    bad = _make_case(tmp_path, "case_bad", crop=9.9)
    output = tmp_path / "out"

    assert main(["--case-dir", str(good), "--output-dir", str(output), "--require-data"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "train_gen_data_v1"
    assert report["ok"] == 1 and report["total_kept_rows"] == 4

    assert main(["--case-dir", str(bad), "--output-dir", str(output), "--require-data"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] == 0

    assert main(["--case-dir", str(bad), "--output-dir", str(output)]) == 0
