"""Launch STAR-CCM+ with a generated flow-control actuation macro."""

from __future__ import annotations

import csv
import math
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flow_control.adapters.starccm_adapter import FlowControlStarCCMAdapter
from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from starccm.control.control_spec import DEFAULT_STARCCM_SPEC, JET_COLUMNS


@dataclass(frozen=True)
class FlowControlStarCCMRunConfig:
    schedule_path: Path
    sim_path: Path
    output_dir: Path
    starccm_path: str = "starccm+"
    num_cores: int = 1
    pod_key: str = ""
    region_name: str = "Region"
    time_step: float | None = None
    report_names: tuple[str, ...] = DEFAULT_STARCCM_SPEC.load_report_names
    strict_boundaries: bool = True
    save_result_sim: bool = True
    keep_macro: bool = True
    dry_run: bool = False


@dataclass(frozen=True)
class FlowControlStarCCMRunResult:
    macro_path: Path
    runtime_plan_path: Path
    log_path: Path
    command: tuple[str, ...]
    returncode: int | None = None
    result_sim_path: Path | None = None


@dataclass(frozen=True)
class _ScheduleWindow:
    window_id: int
    t_start: float
    t_end: float
    massflows: tuple[float, ...] = field(default_factory=tuple)

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


class FlowControlStarCCMRunner:
    """Turn ``actuation_schedule.csv`` into a STAR-CCM+ macro and run it."""

    def run(self, config: FlowControlStarCCMRunConfig) -> FlowControlStarCCMRunResult:
        schedule_path = config.schedule_path.expanduser().resolve()
        sim_path = config.sim_path.expanduser().resolve()
        output_dir = config.output_dir.expanduser().resolve()
        if not schedule_path.exists():
            raise FileNotFoundError(f"actuation schedule not found: {schedule_path}")
        if not sim_path.exists():
            raise FileNotFoundError(f"STAR-CCM+ .sim file not found: {sim_path}")

        output_dir.mkdir(parents=True, exist_ok=True)
        copied_schedule = output_dir / "actuation_schedule.csv"
        if copied_schedule != schedule_path:
            shutil.copy2(schedule_path, copied_schedule)

        runtime_plan_path = output_dir / "starccm_runtime_plan.json"
        FlowControlStarCCMAdapter().write_runtime_plan(
            copied_schedule,
            runtime_plan_path,
            time_step=config.time_step,
        )

        windows = _read_schedule(copied_schedule)
        result_sim_path = output_dir / "flow_control_result.sim"
        macro_path = output_dir / "FlowControlRunMacro.java"
        macro_path.write_text(
            build_flow_control_macro(
                windows,
                region_name=config.region_name,
                time_step=config.time_step,
                report_names=config.report_names,
                strict_boundaries=config.strict_boundaries,
                result_sim_path=result_sim_path if config.save_result_sim else None,
                boundary_names=tuple(jet.boundary_name for jet in DEFAULT_STARCCM_SPEC.jets),
            ),
            encoding="utf-8",
        )

        command = _build_starccm_command(
            config.starccm_path,
            macro_path,
            sim_path,
            num_cores=config.num_cores,
            pod_key=config.pod_key,
        )
        log_path = output_dir / "starccm_flow_control.log"
        if config.dry_run:
            return FlowControlStarCCMRunResult(
                macro_path=macro_path,
                runtime_plan_path=runtime_plan_path,
                log_path=log_path,
                command=tuple(command),
                result_sim_path=result_sim_path if config.save_result_sim else None,
            )

        with log_path.open("w", encoding="utf-8") as log_file:
            proc = _run_starccm_command(command, log_file=log_file, cwd=output_dir)

        if proc.returncode != 0:
            tail = _tail_text(log_path)
            raise RuntimeError(
                f"STAR-CCM+ exited with code {proc.returncode}. Log: {log_path}\n"
                f"--- last log lines ---\n{tail}"
            )

        if not config.keep_macro:
            macro_path.unlink(missing_ok=True)

        return FlowControlStarCCMRunResult(
            macro_path=macro_path,
            runtime_plan_path=runtime_plan_path,
            log_path=log_path,
            command=tuple(command),
            returncode=proc.returncode,
            result_sim_path=result_sim_path if config.save_result_sim else None,
        )


def build_flow_control_macro(
    windows: list[_ScheduleWindow],
    *,
    region_name: str,
    time_step: float | None,
    report_names: tuple[str, ...],
    strict_boundaries: bool,
    result_sim_path: Path | None,
    boundary_names: tuple[str, ...] = tuple(jet.boundary_name for jet in DEFAULT_STARCCM_SPEC.jets),
) -> str:
    if not windows:
        raise ValueError("actuation schedule must contain at least one row")
    if len(boundary_names) != len(JET_COLUMNS):
        raise ValueError(f"expected {len(JET_COLUMNS)} jet boundary names")
    effective_time_step = float(time_step or 0.0)
    return f"""import star.common.*;
import star.base.report.*;
import star.flow.*;
import java.io.*;
import java.util.*;

public class FlowControlRunMacro extends StarMacro {{
    static final String REGION_NAME = "{_java_literal(region_name)}";
    static final boolean STRICT_BOUNDARIES = {str(strict_boundaries).lower()};
    static final double REQUESTED_TIME_STEP = {_java_float(effective_time_step)};
    static final String RESULT_SIM_PATH = "{_java_literal(str(result_sim_path.resolve()).replace(os.sep, '/') if result_sim_path else '')}";
    static final String[] BOUNDARY_NAMES = new String[] {{{", ".join(_quoted(name) for name in boundary_names)}}};
    static final String[] REPORT_NAMES = new String[] {{{", ".join(_quoted(name) for name in report_names)}}};
    static final int[] WINDOW_IDS = new int[] {{{", ".join(str(w.window_id) for w in windows)}}};
    static final double[] T_START = new double[] {{{", ".join(_java_float(w.t_start) for w in windows)}}};
    static final double[] T_END = new double[] {{{", ".join(_java_float(w.t_end) for w in windows)}}};
    static final double[][] MASSFLOW = new double[][] {{
{_java_massflow_rows(windows)}
    }};

    public void execute() {{
        Simulation sim = getActiveSimulation();
        File outDir = new File(resolveOutputDir());
        outDir.mkdirs();
        File csv = new File(outDir, "flow_control_timeseries.csv");
        writeHeader(csv);
        for (int window = 0; window < WINDOW_IDS.length; window++) {{
            double duration = T_END[window] - T_START[window];
            double step = REQUESTED_TIME_STEP > 0.0 ? REQUESTED_TIME_STEP : duration;
            if (step <= 0.0) {{
                throw new RuntimeException("Non-positive time step at window " + WINDOW_IDS[window]);
            }}
            setTransientTimeStep(sim, step);
            for (int jet = 0; jet < BOUNDARY_NAMES.length; jet++) {{
                applyMassFlow(sim, BOUNDARY_NAMES[jet], MASSFLOW[window][jet]);
            }}
            int steps = Math.max(1, (int) Math.round(duration / step));
            sim.println("[flow_control] window=" + WINDOW_IDS[window]
                + " t=[" + T_START[window] + "," + T_END[window] + "]"
                + " duration=" + duration + " step=" + step + " solverSteps=" + steps);
            sim.getSimulationIterator().run(steps);
            appendRow(sim, csv, window);
        }}
        if (RESULT_SIM_PATH != null && !RESULT_SIM_PATH.trim().isEmpty()) {{
            sim.saveState(resolvePath(RESULT_SIM_PATH));
            sim.println("[flow_control] saved result sim -> " + RESULT_SIM_PATH);
        }}
    }}

    private void applyMassFlow(Simulation sim, String boundaryName, double value) {{
        Boundary boundary = findBoundary(sim, boundaryName);
        if (boundary == null) {{
            String message = "Boundary '" + boundaryName + "' not found.";
            if (STRICT_BOUNDARIES) throw new RuntimeException(message);
            sim.println("WARNING: " + message + " Skipping mass-flow update.");
            return;
        }}
        try {{
            MassFlowRateProfile profile = boundary.getValues().get(MassFlowRateProfile.class);
            Units units = ((Units) sim.getUnitsManager().getObject("kg/s"));
            profile.getMethod(ConstantScalarProfileMethod.class)
                .getQuantity().setValueAndUnits(value, units);
        }} catch (Exception e) {{
            String message = "Failed to set mass flow on boundary '" + boundaryName + "': " + e.getMessage();
            if (STRICT_BOUNDARIES) throw new RuntimeException(message);
            sim.println("WARNING: " + message);
        }}
    }}

    private Boundary findBoundary(Simulation sim, String boundaryName) {{
        if (REGION_NAME != null && !REGION_NAME.trim().isEmpty()) {{
            try {{
                Region region = sim.getRegionManager().getRegion(REGION_NAME);
                return region.getBoundaryManager().getBoundary(boundaryName);
            }} catch (Exception ignored) {{}}
        }}
        for (Object obj : sim.getRegionManager().getObjects()) {{
            if (!(obj instanceof Region)) continue;
            Region region = (Region) obj;
            try {{
                Boundary boundary = region.getBoundaryManager().getBoundary(boundaryName);
                if (boundary != null) return boundary;
            }} catch (Exception ignored) {{}}
        }}
        return null;
    }}

    private void setTransientTimeStep(Simulation sim, double step) {{
        try {{
            ImplicitUnsteadySolver solver =
                (ImplicitUnsteadySolver) sim.getSolverManager().getSolver(ImplicitUnsteadySolver.class);
            if (solver != null) {{
                solver.getTimeStep().setValue(step);
            }}
        }} catch (Exception e) {{
            sim.println("WARNING: transient time-step update skipped: " + e.getMessage());
        }}
    }}

    private void writeHeader(File csv) {{
        try {{
            PrintWriter writer = new PrintWriter(new FileWriter(csv, false));
            writer.print("physical_time,window_id");
            for (int i = 0; i < REPORT_NAMES.length; i++) {{
                writer.print("," + REPORT_NAMES[i]);
            }}
            writer.println();
            writer.close();
        }} catch (Exception e) {{
            throw new RuntimeException("Failed to write " + csv + ": " + e.getMessage());
        }}
    }}

    private void appendRow(Simulation sim, File csv, int window) {{
        try {{
            PrintWriter writer = new PrintWriter(new FileWriter(csv, true));
            writer.print(T_END[window] + "," + WINDOW_IDS[window]);
            for (int i = 0; i < REPORT_NAMES.length; i++) {{
                writer.print("," + reportValue(sim, REPORT_NAMES[i]));
            }}
            writer.println();
            writer.close();
        }} catch (Exception e) {{
            throw new RuntimeException("Failed to append " + csv + ": " + e.getMessage());
        }}
    }}

    private double reportValue(Simulation sim, String reportName) {{
        try {{
            Report report = (Report) sim.getReportManager().getObject(reportName);
            if (report instanceof ScalarReport) {{
                return ((ScalarReport) report).getValue();
            }}
            return report.getReportMonitorValue();
        }} catch (Exception e) {{
            sim.println("WARNING: report '" + reportName + "' unavailable: " + e.getMessage());
            return Double.NaN;
        }}
    }}

    private String resolveOutputDir() {{
        try {{
            File macroDir = new File(getClass().getProtectionDomain().getCodeSource().getLocation().toURI());
            if (macroDir.isFile()) macroDir = macroDir.getParentFile();
            if (macroDir != null) return macroDir.getAbsolutePath();
        }} catch (Exception ignored) {{}}
        return ".";
    }}

    private String resolvePath(String path) {{
        return path.replace("\\\\", "/");
    }}
}}
"""


def _read_schedule(path: Path) -> list[_ScheduleWindow]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"actuation schedule is empty: {path}")

    has_massflow = all(column in rows[0] for column in MASSFLOW_COLUMNS)
    windows: list[_ScheduleWindow] = []
    for fallback_idx, row in enumerate(rows):
        window_id = int(float(row.get("window_id", fallback_idx)))
        t_start = _float_field(row, "t_start")
        t_end = _float_field(row, "t_end")
        if t_end <= t_start:
            raise ValueError(f"row {fallback_idx} has non-positive duration: {t_start} -> {t_end}")
        values: list[float] = []
        for jet_column, massflow_column in zip(JET_COLUMNS, MASSFLOW_COLUMNS):
            values.append(_float_field(row, massflow_column if has_massflow else jet_column))
        windows.append(
            _ScheduleWindow(
                window_id=window_id,
                t_start=t_start,
                t_end=t_end,
                massflows=tuple(values),
            )
        )
    return windows


def _build_starccm_command(
    starccm_path: str,
    macro_path: Path,
    sim_path: Path,
    *,
    num_cores: int,
    pod_key: str,
) -> list[str]:
    command = [starccm_path]
    if num_cores > 1:
        command += ["-np", str(num_cores)]
    if pod_key:
        command += ["-podkey", pod_key]
    command += ["-batch", str(macro_path), str(sim_path)]
    return command


def _run_starccm_command(
    command: list[str],
    *,
    log_file: Any,
    cwd: Path,
) -> subprocess.CompletedProcess[Any]:
    launcher = command[0].lower()
    if launcher.endswith((".bat", ".cmd")):
        inner = subprocess.list2cmdline(command)
        return subprocess.run(
            f'cmd /c "{inner}"',
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=cwd,
        )
    return subprocess.run(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=cwd,
    )


def _float_field(row: dict[str, Any], name: str) -> float:
    try:
        return float(row.get(name, 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid numeric field {name}={row.get(name)!r}") from exc


def _java_massflow_rows(windows: list[_ScheduleWindow]) -> str:
    lines = []
    for window in windows:
        values = ", ".join(_java_float(value) for value in window.massflows)
        lines.append(f"        new double[] {{{values}}}")
    return ",\n".join(lines)


def _java_float(value: float) -> str:
    if not math.isfinite(float(value)):
        raise ValueError(f"non-finite float is not supported in STAR macro: {value}")
    return repr(float(value))


def _java_literal(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _quoted(value: str) -> str:
    return f'"{_java_literal(value)}"'


def _tail_text(path: Path, line_count: int = 80) -> str:
    try:
        return "".join(path.read_text(encoding="utf-8", errors="replace").splitlines(True)[-line_count:]).strip()
    except Exception:
        return "(log not readable)"
