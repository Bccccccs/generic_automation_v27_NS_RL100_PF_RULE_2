from pathlib import Path
from unittest.mock import Mock, patch

from flow_control.adapters.starccm_runner import (
    FlowControlStarCCMRunConfig,
    FlowControlStarCCMRunner,
    build_flow_control_macro,
    _progress_from_starccm_line,
    _run_starccm_command,
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
        output_dir=tmp_path,
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
    assert "static final String OUTPUT_DIR" in macro
    assert 'new File(normalizeStarPath(OUTPUT_DIR))' in macro
    assert "findReport(sim, reportName)" in macro
    assert 'reportName.startsWith("fc_load_")' in macro
    assert 'shortName + " Monitor"' in macro
    assert '"Drag Monitor"' in macro
    assert '"Fz Monitor"' in macro
    assert '"Pitch_Moment Monitor"' in macro
    assert '"Roll_Moment Monitor"' in macro
    assert '"Jet_Reaction_Z Monitor"' in macro
    assert "import star.vis.*;" in macro
    assert "exportRequiredMonitorPlots(sim, outDir);" in macro
    assert 'plot.export(normalizeStarPath(output.getAbsolutePath()), ",");' in macro
    assert '"FZ_image.csv"' in macro
    assert '"Fz_Monitor_绘图_image.csv"' in macro
    assert '"Drag_Monitor_绘图_image.csv"' in macro
    assert '"Pitch_Moment_Monitor_绘图_image.csv"' in macro
    assert '"Roll_Moment_Monitor_绘图_image.csv"' in macro
    assert '"Jet_Reaction_Z_Monitor_绘图_image.csv"' in macro
    assert "static final boolean[] ACTIVE_JETS" in macro
    assert "false, false, true, false" in macro
    assert "if (!ACTIVE_JETS[jet]) continue;" in macro
    assert "setBoundaryType(MassFlowBoundary.class)" in macro
    assert "[flow_control] completed window=" in macro
    assert "normalizeStarPath(RESULT_SIM_PATH)" in macro
    assert macro.index('"J" + digits') < macro.index('"JET" + digits')
    assert '"JET" + digits' in macro
    assert '"J" + digits' in macro
    assert "private String resolvePath" not in macro


def test_build_flow_control_macro_uses_template_time_step_when_not_overridden(tmp_path):
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
        output_dir=tmp_path,
        region_name="Region",
        time_step=None,
        report_names=("drag",),
        strict_boundaries=True,
        result_sim_path=tmp_path / "result.sim",
    )

    assert "static final double REQUESTED_TIME_STEP = 0.0;" in macro
    assert "REQUESTED_TIME_STEP > 0.0 ? REQUESTED_TIME_STEP : getTransientTimeStep(sim)" in macro
    assert "if (REQUESTED_TIME_STEP > 0.0)" in macro
    assert "private double getTransientTimeStep" in macro


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
    assert result.timeseries_path == tmp_path / "run" / "flow_control_timeseries.csv"
    assert result.returncode is None
    assert result.command[:3] == ("/path/to/starccm+", "-np", "4")
    assert str(sim_path.resolve()) == result.command[-1]


def test_run_starccm_command_wraps_windows_batch_launchers(tmp_path):
    log_file = Mock()
    proc = Mock()
    proc.stdout = iter(["[flow_control] window=0 t=[0.0,0.1] duration=0.1 step=0.01 solverSteps=10\n"])
    proc.wait.return_value = 0
    with patch("flow_control.adapters.starccm_runner.subprocess.Popen", return_value=proc) as popen:
        result = _run_starccm_command(
            ["C:/Program Files/Siemens/STAR-CCM+/starccm+.bat", "-batch", "macro.java", "case.sim"],
            log_file=log_file,
            cwd=tmp_path,
        )

    assert result.returncode == 0
    command = popen.call_args.args[0]
    assert command.startswith('cmd /c "')
    assert "starccm+.bat" in command
    assert popen.call_args.kwargs["cwd"] == tmp_path
    assert popen.call_args.kwargs["stdout"] is not None
    log_file.write.assert_called_once()


def test_progress_from_starccm_line_keeps_console_output_brief():
    assert _progress_from_starccm_line("Loading: C:/case.sim") == "正在加载仿真文件"
    assert _progress_from_starccm_line("Loading/configuring connectivity (old|new partitions: 1|20)") == "正在配置并行分区"
    assert (
        _progress_from_starccm_line("[flow_control] window=3 t=[0.3,0.4] duration=0.1 step=0.01 solverSteps=10")
        == "正在执行 window=3 t=[0.3,0.4] duration=0.1 step=0.01 solverSteps=10"
    )
    assert _progress_from_starccm_line("[flow_control] completed window=3 csv=C:/x.csv") == "已完成 window=3 csv=C:/x.csv"
    assert _progress_from_starccm_line("1 copy of ccmpsuite checked out") is None
