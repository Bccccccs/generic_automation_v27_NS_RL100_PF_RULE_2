import pytest

from generic_automation.adapters.starccm_adapter import StarCCMAdapter
from starccm.control import (
    DEFAULT_LOAD_POINTS,
    DEFAULT_STARCCM_JETS,
    StarCCMControlLayer,
)
from starccm.control.control_spec import JET_COLUMNS, LOAD_COLUMNS
from flow_control.data_schema import CaseSchema


def test_default_control_spec_fixes_24_jets_and_6_load_points():
    layer = StarCCMControlLayer()
    context = layer.macro_context()

    assert [jet["column"] for jet in context["jets"]] == list(JET_COLUMNS)
    assert [point["column"] for point in context["load_points"]] == list(LOAD_COLUMNS)
    assert len(DEFAULT_STARCCM_JETS) == 24
    assert len(DEFAULT_LOAD_POINTS) == 6
    assert context["load_points"][0]["report_name"] == "fc_load_S1L"
    assert context["load_points"][-1]["report_name"] == "fc_load_S3R"


def test_result_mapper_builds_case_schema_compatible_row():
    layer = StarCCMControlLayer()
    report_values = {
        "fc_load_S1L": 1.0,
        "fc_load_S1R": 1.2,
        "fc_load_S2L": 2.0,
        "fc_load_S2R": 2.2,
        "fc_load_S3L": 3.0,
        "fc_load_S3R": 3.2,
        "drag": 0.7,
    }
    jet_commands = {"JET_01": 1.0, "JET_07": 0.5}

    row = layer.map_timeseries_row(
        report_values,
        jet_commands,
        physical_time=0.25,
        window_id=2,
    )

    assert row["Fz_S1L"] == 1.0
    assert row["Fz_S3R"] == 3.2
    assert row["Fz_Total"] == pytest.approx(12.6)
    assert row["Drag_Total"] == 0.7
    assert row["Pitch_Moment"] == pytest.approx(4.0)
    assert row["Roll_Moment"] == pytest.approx(0.6)
    assert row["Jet_Reaction_Z"] == 1.5
    assert row["JET_01"] == 1.0
    assert row["JET_07"] == 0.5
    assert row["JET_24"] == 0.0
    assert CaseSchema.validate_timeseries([row]) == []


def test_starccm_adapter_writes_shared_control_context(tmp_path):
    StarCCMAdapter()._write_control_context(tmp_path)

    context_path = tmp_path / "starccm_control_context.json"
    text = context_path.read_text(encoding="utf-8")

    assert "fc_load_S1L" in text
    assert "fc_jet_01_mass_flow" in text
