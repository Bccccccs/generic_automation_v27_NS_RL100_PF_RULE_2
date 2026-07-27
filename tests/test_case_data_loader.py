"""Tests for ``case_data_loader`` and ``star_export_reader``.

Covers all acceptance criteria:

1. Missing required columns → **ERROR**
2. ``physical_time`` not monotonically increasing → **ERROR**
3. NaN values → **ERROR**
4. Missing units/direction → **WARNING**
5. Jet on/off vs massflow consistency → **ERROR**
6. cmd vs actual massflow separation → **ERROR**
7. No-jet case Jet_Reaction_Z handling → **WARNING**
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest
import yaml

from flow_control.star_ingest.case_data_loader import (
    load_case,
    ingest_star_export,
    REQUIRED_TIMESERIES_COLUMNS,
    JET_REQUIRED_EXTRA_COLUMNS,
)
from flow_control.star_ingest.star_export_reader import (
    read_star_export_csv,
    detect_star_column_mapping,
    compute_fz_total,
    STANDARD_LOAD_COLUMNS,
)
from flow_control.star_ingest.quality_checker import QualityChecker
from flow_control.star_ingest.figures_generator import generate_all_figures


# ── Helpers ──────────────────────────────────────────────────────────────────


def _good_rows(n: int = 5, *, jet: bool = False) -> list[dict]:
    rows = []
    for i in range(n):
        row = {
            "physical_time": i * 0.01,
            "window_id": i,
            "Fz_S1L": 10.0 + i,
            "Fz_S1R": 10.1 + i,
            "Fz_S2L": 10.2 + i,
            "Fz_S2R": 10.3 + i,
            "Fz_S3L": 10.4 + i,
            "Fz_S3R": 10.5 + i,
            "Fz_Total": 61.5 + i * 6,
            "Drag_Total": 1.0 + i * 0.01,
            "Pitch_Moment": 0.05 + i * 0.001,
            "Roll_Moment": 0.02 + i * 0.001,
            "Jet_Reaction_Z": 0.2 + i * 0.001,
            "solver_status": "success",
            "case_stage": "test_fixture",
        }
        if jet:
            for idx in range(1, 25):
                row[f"JET_{idx:02d}"] = 1.0 if idx == 1 and i % 2 == 0 else 0.0
                row[f"cmd_massflow_{idx:02d}"] = 0.01 if idx == 1 and i % 2 == 0 else 0.0
                row[f"actual_massflow_{idx:02d}"] = 0.0095 if idx == 1 and i % 2 == 0 else 0.0
        rows.append(row)
    return rows


def _minimal_manifest() -> dict:
    return {
        "geometry_version": "geom-v1",
        "mesh_version": "mesh-v1",
        "flow_velocity": 45.0,
        "gap": 0.012,
        "time_step": 0.01,
        "jet_amplitude": 1.0,
        "window_duration": 0.1,
        "random_seed": 1234,
    }


def _write_case(tmp_path: Path, case_id: str, rows: list[dict], *,
                manifest: dict | None = None,
                jet: bool = False) -> Path:
    case_dir = tmp_path / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    for directory_name in ("input", "figures", "logs", "flow_snapshots"):
        (case_dir / directory_name).mkdir(exist_ok=True)

    # manifest
    m = manifest or _minimal_manifest()
    with (case_dir / "case_manifest.yaml").open("w") as f:
        yaml.safe_dump(m, f)

    # timeseries
    cols = list(rows[0].keys()) if rows else ["physical_time"]
    import csv
    with (case_dir / "timeseries.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    # actuation_schedule
    with (case_dir / "actuation_schedule.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["physical_time"])
        w.writeheader()
        for row in rows:
            w.writerow({"physical_time": row.get("physical_time", 0.0)})

    # quality_report (placeholder)
    with (case_dir / "quality_report.json").open("w") as f:
        json.dump({"run_success_flag": True}, f)

    return case_dir


# ── 1. Missing required columns → ERROR ─────────────────────────────────────


class TestRequiredColumns:
    def test_missing_base_column_raises_error(self, tmp_path):
        rows = _good_rows()
        for row in rows:
            row.pop("Fz_Total")
        case_dir = _write_case(tmp_path, "missing_base", rows)
        result = load_case(case_dir)
        assert any("Fz_Total" in e for e in result["errors"])

    def test_missing_jet_column_raises_error(self, tmp_path):
        rows = _good_rows(jet=True)
        for row in rows:
            row.pop("JET_24")
        case_dir = _write_case(tmp_path, "missing_jet", rows)
        result = load_case(case_dir)
        assert any("JET_24" in e for e in result["errors"])

    def test_missing_cmd_massflow_raises_error(self, tmp_path):
        rows = _good_rows(jet=True)
        for row in rows:
            row.pop("cmd_massflow_01")
        case_dir = _write_case(tmp_path, "missing_cmd_mf", rows)
        result = load_case(case_dir)
        assert any("cmd_massflow_01" in e for e in result["errors"])

    def test_missing_actual_massflow_raises_error(self, tmp_path):
        rows = _good_rows(jet=True)
        for row in rows:
            row.pop("actual_massflow_01")
        case_dir = _write_case(tmp_path, "missing_actual_mf", rows)
        result = load_case(case_dir)
        assert any("actual_massflow_01" in e for e in result["errors"])

    def test_all_required_columns_present_passes(self, tmp_path):
        rows = _good_rows(jet=True)
        case_dir = _write_case(tmp_path, "all_cols_ok", rows)
        result = load_case(case_dir)
        col_errors = [e for e in result["errors"] if "Missing required column" in e]
        assert col_errors == []


# ── 2. Monotonic time → ERROR ──────────────────────────────────────────────


class TestMonotonicTime:
    def test_non_monotonic_time_raises_error(self, tmp_path):
        rows = _good_rows()
        rows[2]["physical_time"] = rows[1]["physical_time"] - 0.005  # go backwards
        case_dir = _write_case(tmp_path, "non_monotonic", rows)
        result = load_case(case_dir)
        assert any("monotonically" in e for e in result["errors"])

    def test_duplicate_time_raises_error(self, tmp_path):
        rows = _good_rows()
        rows[2]["physical_time"] = rows[1]["physical_time"]  # duplicate
        case_dir = _write_case(tmp_path, "dup_time", rows)
        result = load_case(case_dir)
        assert any("duplicate" in e.lower() for e in result["errors"])

    def test_strictly_increasing_time_passes(self, tmp_path):
        rows = _good_rows()
        case_dir = _write_case(tmp_path, "good_time", rows)
        result = load_case(case_dir)
        time_errors = [e for e in result["errors"] if "time" in e.lower()]
        assert time_errors == []


# ── 3. NaN values → ERROR ──────────────────────────────────────────────────


class TestNaNValues:
    def test_nan_column_raises_error(self, tmp_path):
        rows = _good_rows()
        rows[1]["Drag_Total"] = float("nan")
        case_dir = _write_case(tmp_path, "has_nan", rows)
        result = load_case(case_dir)
        assert any("NaN" in e for e in result["errors"])

    def test_none_value_raises_error(self, tmp_path):
        rows = _good_rows()
        rows[2]["Fz_S1L"] = None
        case_dir = _write_case(tmp_path, "has_none", rows)
        result = load_case(case_dir)
        assert any("NaN" in e or "missing" in e.lower() for e in result["errors"])


# ── 4. Units/direction → WARNING ───────────────────────────────────────────


class TestUnitsAndDirection:
    def test_missing_units_warns(self):
        manifest = _minimal_manifest()  # no units/direction keys
        result = QualityChecker.run_all(_good_rows(), manifest, has_jet_data=False)
        assert any("units" in w.lower() for w in result["warnings"])

    def test_documented_units_no_warning(self):
        manifest = _minimal_manifest()
        manifest["units"] = {"force": "N", "moment": "Nm", "massflow": "kg/s"}
        manifest["sign_convention"] = "positive Fz = lift upward"
        result = QualityChecker.run_all(_good_rows(), manifest, has_jet_data=False)
        unit_warnings = [w for w in result["warnings"] if "Units" in w or "Sign" in w]
        assert unit_warnings == []


# ── 5. Jet on/off vs massflow consistency → ERROR ──────────────────────────


class TestJetMassflowConsistency:
    def test_jet_on_but_zero_massflow(self, tmp_path):
        rows = _good_rows(jet=True)
        # Row 0: JET_01 = 1 (ON) but cmd_massflow_01 = 0
        rows[0]["JET_01"] = 1.0
        rows[0]["cmd_massflow_01"] = 0.0
        case_dir = _write_case(tmp_path, "jet_on_no_mf", rows)
        result = load_case(case_dir)
        assert any("inconsistent" in e.lower() for e in result["errors"])

    def test_jet_off_but_nonzero_massflow(self, tmp_path):
        rows = _good_rows(jet=True)
        rows[0]["JET_01"] = 0.0
        rows[0]["cmd_massflow_01"] = 0.05
        case_dir = _write_case(tmp_path, "jet_off_has_mf", rows)
        result = load_case(case_dir)
        assert any("inconsistent" in e.lower() for e in result["errors"])


# ── 6. cmd vs actual massflow separation → ERROR ────────────────────────────


class TestMassflowSeparation:
    def test_cmd_without_actual_raises_error(self, tmp_path):
        rows = _good_rows(jet=True)
        for row in rows:
            row.pop("actual_massflow_01")
            row.pop("actual_massflow_02")
            row.pop("actual_massflow_03")
            # ... (they're all jet rows with "jet=True", but let's remove all actual)
        # Actually, _good_rows(jet=True) creates all_actual. Let me be explicit.
        rows2 = _good_rows(jet=True)
        for row in rows2:
            for k in list(row.keys()):
                if k.startswith("actual_massflow"):
                    del row[k]
        case_dir = _write_case(tmp_path, "no_actual_mf", rows2)
        result = load_case(case_dir)
        assert any("actual_massflow" in e and "missing" in e.lower()
                    for e in result["errors"])

    def test_actual_without_cmd_raises_error(self, tmp_path):
        rows = _good_rows(jet=True)
        for row in rows:
            for k in list(row.keys()):
                if k.startswith("cmd_massflow"):
                    del row[k]
        case_dir = _write_case(tmp_path, "no_cmd_mf", rows)
        result = load_case(case_dir)
        assert any("cmd_massflow" in e and "missing" in e.lower()
                    for e in result["errors"])

    def test_both_cmd_and_actual_present_passes(self, tmp_path):
        rows = _good_rows(jet=True)
        case_dir = _write_case(tmp_path, "both_mf_ok", rows)
        result = load_case(case_dir)
        mf_errors = [e for e in result["errors"] if "massflow" in e.lower()]
        # Should only be jet/massflow consistency errors if any
        assert all("massflow" not in e.lower() or "inconsistent" in e.lower()
                   for e in result["errors"]) or mf_errors == []


# ── 7. No-jet case Jet_Reaction_Z → WARNING ────────────────────────────────


class TestNoJetJRZ:
    def test_no_jet_jrz_zero_is_not_data_loss(self, tmp_path):
        """No-jet case with Jet_Reaction_Z ≈ 0 should produce info WARNING, not ERROR."""
        rows = _good_rows(jet=False)
        for row in rows:
            row["Jet_Reaction_Z"] = 0.0  # explicitly zero
        case_dir = _write_case(tmp_path, "no_jet_zero_jrz", rows)
        result = load_case(case_dir)
        # Should NOT have errors about Jet_Reaction_Z
        jrz_errors = [e for e in result["errors"] if "Jet_Reaction_Z" in e]
        assert jrz_errors == []
        # Should have a warning noting this is expected
        jrz_warnings = [w for w in result["warnings"] if "NOT data loss" in w]
        assert len(jrz_warnings) >= 1

    def test_no_jet_no_jrz_column_is_missing_column_error(self, tmp_path):
        """No-jet case without Jet_Reaction_Z column → Jet_Reaction_Z is still a
        required column in the schema, so its absence IS a missing-column error.
        The 'not data loss' check only applies when the column IS present and is 0."""
        rows = _good_rows(jet=False)
        for row in rows:
            row.pop("Jet_Reaction_Z")
        case_dir = _write_case(tmp_path, "no_jet_no_jrz", rows)
        result = load_case(case_dir)
        # Jet_Reaction_Z is in REQUIRED_TIMESERIES_COLUMNS → error when missing
        assert any("Jet_Reaction_Z" in e for e in result["errors"])


# ── STAR export reader tests ────────────────────────────────────────────────


class TestStarExportReader:
    def test_excel_bom_and_independent_fz_total_monitor(self, tmp_path):
        csv_path = tmp_path / "Fz_Monitor.csv"
        csv_path.write_text(
            '"时间","Fz Monitor: Fz Monitor (N)"\n0.0001,-23301.5\n',
            encoding="utf-8-sig",
        )
        data = read_star_export_csv(csv_path)
        assert data["rows"][0]["physical_time"] == pytest.approx(0.0001)
        assert data["rows"][0]["Fz_Total"] == pytest.approx(-23301.5)
        assert data["units"]["Fz_Total"] == "N"

    def test_detect_star_column_mapping(self):
        """Verify standard column mapping from STAR headers."""
        headers = [
            '"时间"',
            '"S1L Monitor: S1L Monitor (N)"',
            '"S1R Monitor: S1R Monitor (N)"',
            '"S2L Monitor: S2L Monitor (N)"',
            '"S2R Monitor: S2R Monitor (N)"',
            '"S3L Monitor: S3L Monitor (N)"',
            '"S3R Monitor: S3R Monitor (N)"',
        ]
        mapping = detect_star_column_mapping(headers)
        assert mapping["physical_time"] == '"时间"'
        assert mapping["Fz_S1L"] == '"S1L Monitor: S1L Monitor (N)"'
        assert mapping["Fz_S3R"] == '"S3R Monitor: S3R Monitor (N)"'
        assert len(mapping) == 7

    def test_jet_column_mapping(self):
        """Verify JET_NN column detection."""
        headers = [
            '"时间"',
            '"S1L Monitor: S1L Monitor (N)"',
            '"JET_01"',
            '"JET_24"',
            '"cmd_massflow_01"',
            '"actual_massflow_01"',
        ]
        mapping = detect_star_column_mapping(headers)
        assert mapping["JET_01"] == '"JET_01"'
        assert mapping["JET_24"] == '"JET_24"'
        assert mapping["cmd_massflow_01"] == '"cmd_massflow_01"'
        assert mapping["actual_massflow_01"] == '"actual_massflow_01"'

    def test_star_bottom_jet_boundary_name_is_not_switch_column(self):
        """STAR JET01 is a bottom region, not the algorithm JET_01 switch."""
        headers = [
            '"时间"',
            '"JET01"',
            '"JET24"',
            '"J01"',
            '"cmd_massflow_01"',
        ]

        mapping = detect_star_column_mapping(headers)

        assert "JET_01" not in mapping
        assert "JET_24" not in mapping
        assert mapping["cmd_massflow_01"] == '"cmd_massflow_01"'

    def test_read_star_export_csv(self, tmp_path):
        """Read a minimal STAR-like CSV and verify output structure."""
        csv_content = (
            '"时间","S1L Monitor: S1L Monitor (N)","S1R Monitor: S1R Monitor (N)"\n'
            '0.0,1.0,2.0\n'
            '0.1,3.0,4.0\n'
        )
        csv_path = tmp_path / "test_export.csv"
        csv_path.write_text(csv_content, encoding="utf-8")

        data = read_star_export_csv(csv_path)
        assert "physical_time" in data["mapping"]
        assert len(data["rows"]) == 2
        assert data["rows"][0]["Fz_S1L"] == 1.0
        assert data["rows"][1]["Fz_S1R"] == 4.0
        assert data["units"].get("Fz_S1L") == "N"

    def test_compute_fz_total(self):
        """Verify Fz_Total computation from sensor columns."""
        rows = [
            {"Fz_S1L": 10.0, "Fz_S1R": 11.0, "Fz_S2L": 12.0, "Fz_S2R": 13.0,
             "Fz_S3L": 14.0, "Fz_S3R": 15.0},
        ]
        compute_fz_total(rows)
        assert rows[0]["Fz_Total"] == pytest.approx(75.0)

    def test_read_star_export_missing_time_raises(self, tmp_path):
        """CSV without a time column raises ValueError."""
        csv_content = '"FakeCol1","FakeCol2"\n1.0,2.0\n'
        csv_path = tmp_path / "bad_export.csv"
        csv_path.write_text(csv_content, encoding="utf-8")
        with pytest.raises(ValueError, match="physical_time"):
            read_star_export_csv(csv_path)

    def test_ingest_star_export_creates_case_and_reports_missing_globals(self, tmp_path):
        """End-to-end: STAR export → case directory via ingest_star_export."""
        csv_content = (
            '"时间","S1L Monitor: S1L Monitor (N)","S1R Monitor: S1R Monitor (N)",'
            '"S2L Monitor: S2L Monitor (N)","S2R Monitor: S2R Monitor (N)",'
            '"S3L Monitor: S3L Monitor (N)","S3R Monitor: S3R Monitor (N)"\n'
            '0.0,1.0,2.0,3.0,4.0,5.0,6.0\n'
            '0.1,7.0,8.0,9.0,10.0,11.0,12.0\n'
        )
        csv_path = tmp_path / "demo_fz.csv"
        csv_path.write_text(csv_content, encoding="utf-8")

        result = ingest_star_export(
            [csv_path],
            case_dir=tmp_path / "demo_case",
            manifest={
                "geometry_version": "geom-v1",
                "mesh_version": "mesh-v1",
                "flow_velocity": 45.0,
                "gap": 0.012,
                "time_step": 0.01,
                "jet_amplitude": 0.0,
                "window_duration": 0.1,
                "random_seed": 0,
                "units": {"force": "N"},
                "sign_convention": "positive Fz = lift upward",
            },
            notes="Demo case from STAR export",
        )
        assert result["case_id"] == "demo_case"
        assert any("Drag_Total" in e for e in result["errors"])
        assert any("Pitch_Moment" in e for e in result["errors"])
        assert any("Roll_Moment" in e for e in result["errors"])
        assert any("Jet_Reaction_Z" in e for e in result["errors"])
        assert (tmp_path / "demo_case" / "timeseries.csv").exists()
        assert (tmp_path / "demo_case" / "figures").is_dir()
        assert (tmp_path / "demo_case" / "notes.md").exists()
        assert result["has_jet_data"] is False  # no jet columns

    def test_missing_jet_exports_still_generate_all_four_figures(self, tmp_path):
        rows = _good_rows()
        result = {
            "case_id": "missing_jet_exports",
            "timeseries": rows,
            "has_jet_data": True,
            "errors": ["Missing required column: JET_01"],
            "warnings": [],
        }
        figures = generate_all_figures(result, tmp_path / "figures")
        assert set(figures) == {
            "force_timeseries", "jet_schedule", "massflow_check", "quality_summary"
        }
        assert all(path is not None and path.exists() for path in figures.values())

    def test_massflow_check_generates_all_24_jets_in_four_figures(self, tmp_path):
        rows = _good_rows(jet=True)
        result = {
            "case_id": "jet_exports",
            "timeseries": rows,
            "has_jet_data": True,
            "errors": [],
            "warnings": [],
        }
        figures = generate_all_figures(result, tmp_path / "figures")
        for key in (
            "massflow_check_01_06",
            "massflow_check_07_12",
            "massflow_check_13_18",
            "massflow_check_19_24",
        ):
            assert key in figures
            assert figures[key] is not None
            assert figures[key].exists()

    def test_missing_file_errors(self, tmp_path):
        """Missing required files produce file-completeness error."""
        case_dir = tmp_path / "incomplete_case"
        case_dir.mkdir()
        (case_dir / "input").mkdir()
        (case_dir / "figures").mkdir()
        (case_dir / "logs").mkdir()
        (case_dir / "flow_snapshots").mkdir()
        (case_dir / "case_manifest.yaml").write_text("key: value\n")
        # No timeseries.csv, actuation_schedule.csv, or quality_report.json
        result = load_case(case_dir)
        assert any("Missing required files" in e for e in result["errors"])

    def test_ingest_star_export_no_overwrite_raises(self, tmp_path):
        """Overwrite=False raises FileExistsError for existing case."""
        csv_content = '"时间","S1L Monitor: S1L Monitor (N)"\n0.0,1.0\n'
        csv_path = tmp_path / "test.csv"
        csv_path.write_text(csv_content)
        case_dir = tmp_path / "exists"
        case_dir.mkdir()

        with pytest.raises(FileExistsError):
            ingest_star_export([csv_path], case_dir=case_dir)
