from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from flow_control.adapters.starccm_runner import (
    FlowControlStarCCMRunConfig,
    FlowControlStarCCMRunner,
    _read_schedule,
    build_flow_control_macro,
    _progress_from_starccm_line,
    _run_starccm_command,
)
from flow_control.excitation_patterns.common import ActuationConfig, write_pattern_outputs
from flow_control.excitation_patterns.pulse import generate as generate_pulse


def test_build_flow_control_macro_reads_schedule_csv_at_runtime(tmp_path):
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
    assert '"J03"' in macro
    assert '"drag"' in macro
    assert "MassFlowRateProfile" in macro
    assert "static final String SCHEDULE_CSV_PATH" in macro
    assert "ScheduleData schedule = readSchedule" in macro
    assert "cmd_massflow_" in macro
    assert "double[][] MASSFLOW" not in macro
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
    assert 'presentationName.startsWith(candidate + " ")' in macro
    assert "presentationName.toLowerCase(Locale.ROOT).startsWith(" in macro
    assert '"FZ_image.csv"' in macro
    assert '"Fz_Monitor_绘图_image.csv"' in macro
    assert '"Drag_Monitor_绘图_image.csv"' in macro
    assert '"Pitch_Moment_Monitor_绘图_image.csv"' in macro
    assert '"Roll_Moment_Monitor_绘图_image.csv"' in macro
    assert '"Jet_Reaction_Z_Monitor_绘图_image.csv"' in macro
    assert "static final boolean[] ACTIVE_JETS" in macro
    assert "false, false, true, false" in macro
    assert "if (!ACTIVE_JETS[jet]) continue;" not in macro
    assert "ensureActualMassFlowReports(sim);" in macro
    assert "writeTemplateSnapshot(sim, outDir);" in macro
    assert '"sim_template_snapshot.yaml"' in macro
    assert 'presentationName.toLowerCase(Locale.ROOT).endsWith(' in macro
    assert '"." + boundaryName.toLowerCase(Locale.ROOT)' in macro
    assert 'writeSurfaceSnapshot(writer, sim, "JET" + twoDigit(index));' in macro
    assert '"actual_massflow_01"' in macro
    assert "solver_dt_s,action_window_s,sample_interval_s" in macro
    assert "requiredReportValue(sim, ACTUAL_MASSFLOW_REPORT_NAMES[i])" in macro
    assert "for (int stepIndex = 0; stepIndex < steps; stepIndex++)" in macro
    assert "sim.getSimulationIterator().run(1);" in macro
    assert "schedule.tStart[window] + (stepIndex + 1) * step" in macro
    assert "sim.getSimulationIterator().run(steps);" not in macro
    assert "schedule.tEnd[window], schedule.windowIds[window], step, duration, duration" not in macro
    assert "SurfaceIntegralReport areaReport" in macro
    assert 'getFunction("Area")' in macro
    assert "AreaReport" not in macro
    assert "areaReport.getParts().setObjects(new NeoObjectVector" in macro
    assert "report.getParts().setObjects(new NeoObjectVector" in macro
    assert ".setParts(new NeoObjectVector" not in macro
    assert "mass-flow command changes inside window" in macro
    assert "setBoundaryType(MassFlowBoundary.class)" in macro
    assert "[flow_control] completed window=" in macro
    assert "normalizeStarPath(RESULT_SIM_PATH)" in macro
    assert '"J" + digits' in macro
    assert '"JET" + digits' not in macro
    assert '"JET_" + digits' not in macro
    assert "isBottomJetBoundaryName" in macro
    assert "private String resolvePath" not in macro


def test_build_flow_control_macro_rejects_star_bottom_jet_boundaries(tmp_path):
    windows = [
        type("Window", (), {
            "window_id": 0,
            "t_start": 0.0,
            "t_end": 0.1,
            "massflows": tuple([0.025] + [0.0] * 23),
        })()
    ]

    with pytest.raises(ValueError, match="not JET01..JET24 bottom-region boundaries"):
        build_flow_control_macro(
            windows,
            output_dir=tmp_path,
            region_name="Region",
            time_step=0.01,
            report_names=("drag",),
            strict_boundaries=True,
            result_sim_path=tmp_path / "result.sim",
            boundary_names=tuple(["JET01"] + [f"J{idx:02d}" for idx in range(2, 25)]),
        )


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
        time_step=0.1,
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
    assert result.timeseries_path == tmp_path / "run" / "timeseries.csv"
    assert result.returncode is None
    assert result.command[:3] == ("/path/to/starccm+", "-np", "4")
    assert str(sim_path.resolve()) == result.command[-1]
    macro = result.macro_path.read_text(encoding="utf-8")
    assert '"total", "drag", "Pitch_Moment", "Roll_Moment", "Jet_Reaction_Z"' in macro
    assert '"fc_load_S1L"' in macro
    assert '"fc_load_S3R"' in macro
    windows = _read_schedule(config.output_dir / "actuation_schedule.csv")
    assert [(window.window_id, window.t_start, window.t_end) for window in windows] == [
        (0, 0.0, 0.2),
        (1, 0.2, 0.4),
    ]


def test_package_only_packages_and_validates_in_one_mode(tmp_path):
    config = ActuationConfig(
        mode="no_jet_reference",
        total_windows=1,
        window_duration=0.1,
        output_dir=tmp_path / "schedule",
    )
    table, extra, errors = generate_pulse(config)
    assert errors == []
    write_pattern_outputs(config, table, extra=extra)
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    (output_dir / "timeseries.csv").write_text(
        "physical_time,window_id\n0.1,0\n", encoding="utf-8"
    )
    case_dir = tmp_path / "case"

    with (
        patch("flow_control.adapters.starccm_runner._package_runtime_csv") as package,
        patch("flow_control.adapters.starccm_runner._validate_case") as validate,
    ):
        FlowControlStarCCMRunner().run(
            FlowControlStarCCMRunConfig(
                schedule_path=config.output_dir / "actuation_schedule.csv",
                sim_path=tmp_path / "not-needed.sim",
                output_dir=output_dir,
                execution_mode="package-only",
                case_dir=case_dir,
            )
        )

    package.assert_called_once()
    validate.assert_called_once_with(case_dir, True)


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
