from pathlib import Path

from flow_control.adapters.starccm_runner import (
    FlowControlStarCCMRunConfig,
    FlowControlStarCCMRunner,
    build_flow_control_macro,
)
from flow_control.excitation_patterns.common import ActuationConfig, write_pattern_outputs
from flow_control.excitation_patterns.pulse import generate as generate_pulse


def test_build_flow_control_macro_embeds_massflow_windows(tmp_path):
    windows = [
        type("Window", (), {
            "window_id": 0,
            "t_start": 0.0,
            "t_end": 0.1,
            "massflows": tuple([0.0, 0.0, 0.025] + [0.0] * 21),
        })()
    ]

    macro = build_flow_control_macro(
        windows,
        region_name="Region",
        time_step=0.01,
        report_names=("drag",),
        strict_boundaries=True,
        result_sim_path=tmp_path / "result.sim",
    )

    assert "public class FlowControlRunMacro" in macro
    assert "fc_jet_03" in macro
    assert "0.025" in macro
    assert '"drag"' in macro
    assert "MassFlowRateProfile" in macro


def test_flow_control_runner_dry_run_writes_macro_and_plan(tmp_path):
    config = ActuationConfig(
        mode="pulse_singlejet",
        total_windows=2,
        window_duration=0.2,
        mass_flow_rate=0.025,
        jet_ids=(3,),
        pulse_windows=(1,),
        output_dir=tmp_path / "schedule",
    )
    table, extra, errors = generate_pulse(config)
    assert errors == []
    write_pattern_outputs(config, table, extra=extra)
    sim_path = tmp_path / "case.sim"
    sim_path.write_text("placeholder", encoding="utf-8")

    result = FlowControlStarCCMRunner().run(
        FlowControlStarCCMRunConfig(
            schedule_path=config.output_dir / "actuation_schedule.csv",
            sim_path=sim_path,
            output_dir=tmp_path / "run",
            starccm_path="/path/to/starccm+",
            num_cores=4,
            time_step=0.1,
            dry_run=True,
        )
    )

    assert result.macro_path.exists()
    assert result.runtime_plan_path.exists()
    assert result.returncode is None
    assert result.command[:3] == ("/path/to/starccm+", "-np", "4")
    assert str(sim_path.resolve()) == result.command[-1]
