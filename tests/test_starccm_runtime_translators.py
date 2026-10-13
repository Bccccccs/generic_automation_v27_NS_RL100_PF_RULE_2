import json

from flow_control.starccm_translator import FlowControlStarCCMTranslator
from generic_automation.adapters.starccm_adapter import StarCCMAdapter
from generic_automation.core.adapter_base import Case
from generic_automation.starccm_translator import GenericAutomationStarCCMTranslator


def test_flow_control_translator_emits_jet_load_window_plan():
    translator = FlowControlStarCCMTranslator()
    jet_commands = {"JET_03": 1.0, "JET_07": 0.5}

    plan = translator.translate_window(jet_commands, window_id=4, duration=1.5)
    payload = plan.to_dict()

    assert payload["source"] == "flow_control"
    assert payload["metadata"]["window_id"] == 4
    assert payload["metadata"]["active_jets"] == ["JET_03", "JET_07"]
    assert len([cmd for cmd in payload["commands"] if cmd["kind"] == "set_report_binding"]) == 6
    assert len([cmd for cmd in payload["commands"] if cmd["kind"] == "set_boundary_profile"]) == 24
    assert any(
        cmd["kind"] == "set_boundary_profile"
        and cmd["column"] == "JET_03"
        and cmd["value"] == 1.0
        for cmd in payload["commands"]
    )
    assert payload["commands"][-2]["kind"] == "run_time_window"
    assert payload["commands"][-1]["kind"] == "read_reports"


def test_generic_automation_translator_emits_solver_plan():
    case = Case(
        case_name="solver_case",
        inlet_velocity=30.0,
        inlet_temperature=300.0,
        outlet_pressure=0.0,
        base_mesh_size=0.1,
        pressure_relaxation_factor=0.31,
        pressure_amg_cycle=1,
        velocity_amg_cycle=0,
        max_iterations=1000,
        report_names=["custom_report"],
    )

    plan = GenericAutomationStarCCMTranslator().translate(case, check_interval=50)
    payload = plan.to_dict()

    assert payload["source"] == "generic_automation"
    assert payload["metadata"]["case_name"] == "solver_case"
    assert any(
        cmd["kind"] == "set_solver_parameter"
        and cmd["parameter_name"] == "pressure_relaxation_factor"
        and cmd["value"] == 0.31
        for cmd in payload["commands"]
    )
    assert any(cmd["kind"] == "run_iterations" and cmd["iterations"] == 50 for cmd in payload["commands"])
    assert payload["commands"][-1]["kind"] == "read_reports"
    assert "custom_report" in payload["commands"][-1]["report_names"]


def test_starccm_adapter_writes_runtime_plan(tmp_path):
    case = Case(
        case_name="adapter_case",
        inlet_velocity=30.0,
        inlet_temperature=300.0,
        outlet_pressure=0.0,
        base_mesh_size=0.1,
    )

    StarCCMAdapter(check_interval=25)._write_runtime_plan(case, tmp_path)

    payload = json.loads((tmp_path / "starccm_runtime_plan.json").read_text(encoding="utf-8"))
    assert payload["source"] == "generic_automation"
    assert any(cmd["kind"] == "run_iterations" and cmd["iterations"] == 25 for cmd in payload["commands"])
