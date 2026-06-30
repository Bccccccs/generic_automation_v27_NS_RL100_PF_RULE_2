from pathlib import Path

import pytest

from flow_control.data_schema import CaseSchema, JET_COLUMNS, TIMESERIES_REQUIRED_COLUMNS


def _manifest() -> dict:
    return {
        "geometry_version": "geom-v1",
        "mesh_version": "mesh-v1",
        "flow_velocity": 45.0,
        "gap": 0.012,
        "time_step": 0.01,
        "jet_amplitude": 1.0,
        "window_duration": 0.1,
        "random_seed": 1234,
        "git_commit": "abc123",
        "created_time": "2026-06-25T00:00:00+00:00",
    }


def _timeseries_rows(count: int = 3) -> list[dict]:
    rows = []
    for window_id in range(count):
        row = {
            "physical_time": window_id * 0.01,
            "window_id": window_id,
            "Fz_S1L": 1.0 + window_id,
            "Fz_S1R": 1.1 + window_id,
            "Fz_S2L": 1.2 + window_id,
            "Fz_S2R": 1.3 + window_id,
            "Fz_S3L": 1.4 + window_id,
            "Fz_S3R": 1.5 + window_id,
            "Fz_Total": 7.5 + window_id,
            "Drag_Total": 0.8 + window_id * 0.01,
            "Pitch_Moment": 0.05 + window_id * 0.01,
            "Roll_Moment": 0.02 + window_id * 0.01,
            "Jet_Reaction_Z": 0.2 + window_id * 0.01,
            "solver_status": "success",
        }
        row.update({jet: 1.0 if jet == "JET_01" and window_id % 2 == 0 else 0.0 for jet in JET_COLUMNS})
        rows.append(row)
    return rows


@pytest.fixture
def isolated_runs_root(tmp_path: Path):
    old_root = CaseSchema.runs_root
    CaseSchema.runs_root = tmp_path / "runs"
    try:
        yield CaseSchema.runs_root
    finally:
        CaseSchema.runs_root = old_root


def test_write_and_load_case_creates_standard_bundle(isolated_runs_root):
    result = CaseSchema.write_case(
        {
            "case_id": "case_schema_smoke",
            "manifest": _manifest(),
            "timeseries": _timeseries_rows(),
        }
    )

    run_dir = isolated_runs_root / "case_schema_smoke"
    assert result["run_dir"] == run_dir
    assert (run_dir / "case_manifest.yaml").exists()
    assert (run_dir / "actuation_schedule.csv").exists()
    assert (run_dir / "timeseries.csv").exists()
    assert (run_dir / "quality_report.json").exists()
    assert (run_dir / "figures").is_dir()
    assert (run_dir / "logs" / "case_io.log").exists()

    loaded = CaseSchema.load_case("case_schema_smoke")
    assert loaded["manifest"]["geometry_version"] == "geom-v1"
    assert loaded["quality_report"]["run_success_flag"] is True
    assert list(loaded["timeseries"][0].keys())[: len(TIMESERIES_REQUIRED_COLUMNS)] == list(
        TIMESERIES_REQUIRED_COLUMNS
    )


def test_validate_timeseries_rejects_missing_jet_column():
    rows = _timeseries_rows()
    for row in rows:
        row.pop("JET_24")

    errors = CaseSchema.validate_timeseries(rows)

    assert any("JET_24" in error for error in errors)


def test_validate_timeseries_rejects_nan_values():
    rows = _timeseries_rows()
    rows[1]["Drag_Total"] = float("nan")

    errors = CaseSchema.validate_timeseries(rows)

    assert any("NaN" in error for error in errors)


def test_validate_manifest_rejects_missing_required_field():
    manifest = _manifest()
    manifest.pop("mesh_version")

    errors = CaseSchema.validate_manifest(manifest)

    assert any("mesh_version" in error for error in errors)


def test_validate_timeseries_rejects_nonconsecutive_window_id():
    rows = _timeseries_rows()
    rows[2]["window_id"] = 4

    errors = CaseSchema.validate_timeseries(rows)

    assert any("window_id must be consecutive" in error for error in errors)
