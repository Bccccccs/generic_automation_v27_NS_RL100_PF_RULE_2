import json

from flow_control.excitation_patterns.common import ActuationConfig
from flow_control.excitation_patterns.pulse import generate as generate_pulse
from flow_control.excitation_patterns.common import write_pattern_outputs
from flow_control.adapters.starccm_adapter import FlowControlStarCCMAdapter


def test_flow_control_starccm_adapter_writes_runtime_plan_from_schedule(tmp_path):
    config = ActuationConfig(
        mode="pulse_singlejet",
        total_windows=3,
        window_duration=0.2,
        mass_flow_rate=0.025,
        jet_ids=(3,),
        pulse_windows=(1,),
        output_dir=tmp_path,
    )
    table, extra, errors = generate_pulse(config)
    assert errors == []
    write_pattern_outputs(config, table, extra=extra)

    output_path = FlowControlStarCCMAdapter().write_runtime_plan(
        tmp_path / "actuation_schedule.csv"
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["source"] == "flow_control"
    assert payload["metadata"]["window_count"] == 3
    assert payload["metadata"]["window_ids"] == [0, 1, 2]
    assert payload["metadata"]["active_jets"] == ["JET_03"]
    assert payload["metadata"]["physical_time_start"] == 0.0
    assert payload["metadata"]["physical_time_end"] == 0.6

    set_boundary_commands = [
        command for command in payload["commands"]
        if command["kind"] == "set_boundary_profile"
    ]
    run_window_commands = [
        command for command in payload["commands"]
        if command["kind"] == "run_time_window"
    ]

    assert len(set_boundary_commands) == 72
    assert len(run_window_commands) == 3
    assert {command["duration"] for command in run_window_commands} == {0.2}
    assert [
        command["value"]
        for command in set_boundary_commands
        if command["column"] == "JET_03"
    ] == [0.025, 0.0, 0.0]


def test_flow_control_starccm_adapter_can_plan_rows_without_massflow_columns():
    rows = [
        {
            "physical_time": 0.0,
            "window_id": 0,
            "t_start": 0.0,
            "t_end": 1.0,
            "JET_01": 1.0,
            "JET_02": 0.5,
        }
    ]

    plan = FlowControlStarCCMAdapter().plan_from_schedule_rows(rows)
    payload = plan.to_dict()

    values = {
        command["column"]: command["value"]
        for command in payload["commands"]
        if command["kind"] == "set_boundary_profile"
    }
    assert values["JET_01"] == 1.0
    assert values["JET_02"] == 0.5
    assert values["JET_24"] == 0.0
