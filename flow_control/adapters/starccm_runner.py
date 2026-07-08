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
    timeseries_path: Path | None = None


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
        timeseries_path = output_dir / "flow_control_timeseries.csv"
        macro_path = output_dir / "FlowControlRunMacro.java"
        macro_path.write_text(
            build_flow_control_macro(
                windows,
                output_dir=output_dir,
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
            _print_progress("dry-run 完成，已生成宏和运行计划")
            return FlowControlStarCCMRunResult(
                macro_path=macro_path,
                runtime_plan_path=runtime_plan_path,
                log_path=log_path,
                command=tuple(command),
                result_sim_path=result_sim_path if config.save_result_sim else None,
                timeseries_path=timeseries_path,
            )

        _print_progress(f"开始启动 STAR-CCM+，日志: {log_path}")
        with log_path.open("w", encoding="utf-8") as log_file:
            proc = _run_starccm_command(command, log_file=log_file, cwd=output_dir)

        if proc.returncode != 0:
            _print_progress(f"STAR-CCM+ 失败退出，返回码 {proc.returncode}")
            tail = _tail_text(log_path)
            raise RuntimeError(
                f"STAR-CCM+ exited with code {proc.returncode}. Log: {log_path}\n"
                f"--- last log lines ---\n{tail}"
            )

        if not config.keep_macro:
            macro_path.unlink(missing_ok=True)

        _print_progress("STAR-CCM+ 已完成")
        return FlowControlStarCCMRunResult(
            macro_path=macro_path,
            runtime_plan_path=runtime_plan_path,
            log_path=log_path,
            command=tuple(command),
            returncode=proc.returncode,
            result_sim_path=result_sim_path if config.save_result_sim else None,
            timeseries_path=timeseries_path,
        )


def build_flow_control_macro(
    windows: list[_ScheduleWindow],
    *,
    output_dir: Path | None = None,
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
    csv_output_dir = output_dir or (result_sim_path.parent if result_sim_path else Path("."))
    return f"""import star.common.*;
import star.base.report.*;
import star.flow.*;
import java.io.*;
import java.util.*;

public class FlowControlRunMacro extends StarMacro {{
    static final String REGION_NAME = "{_java_literal(region_name)}";
    static final boolean STRICT_BOUNDARIES = {str(strict_boundaries).lower()};
    static final double REQUESTED_TIME_STEP = {_java_float(effective_time_step)};
    static final String OUTPUT_DIR = "{_java_literal(str(csv_output_dir.resolve()).replace(os.sep, '/'))}";
    static final String RESULT_SIM_PATH = "{_java_literal(str(result_sim_path.resolve()).replace(os.sep, '/') if result_sim_path else '')}";
    static final String[] BOUNDARY_NAMES = new String[] {{{", ".join(_quoted(name) for name in boundary_names)}}};
    static final String[] REPORT_NAMES = new String[] {{{", ".join(_quoted(name) for name in report_names)}}};
    static final int[] WINDOW_IDS = new int[] {{{", ".join(str(w.window_id) for w in windows)}}};
    static final double[] T_START = new double[] {{{", ".join(_java_float(w.t_start) for w in windows)}}};
    static final double[] T_END = new double[] {{{", ".join(_java_float(w.t_end) for w in windows)}}};
    static final boolean[] ACTIVE_JETS = new boolean[] {{{_java_active_jet_flags(windows)}}};
    static final double[][] MASSFLOW = new double[][] {{
{_java_massflow_rows(windows)}
    }};

    public void execute() {{
        Simulation sim = getActiveSimulation();
        File outDir = new File(normalizeStarPath(OUTPUT_DIR));
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
                if (!ACTIVE_JETS[jet]) continue;
                applyMassFlow(sim, BOUNDARY_NAMES[jet], MASSFLOW[window][jet]);
            }}
            int steps = Math.max(1, (int) Math.round(duration / step));
            sim.println("[flow_control] window=" + WINDOW_IDS[window]
                + " t=[" + T_START[window] + "," + T_END[window] + "]"
                + " duration=" + duration + " step=" + step + " solverSteps=" + steps);
            sim.getSimulationIterator().run(steps);
            appendRow(sim, csv, window);
            sim.println("[flow_control] completed window=" + WINDOW_IDS[window]
                + " csv=" + csv.getAbsolutePath());
        }}
        if (RESULT_SIM_PATH != null && !RESULT_SIM_PATH.trim().isEmpty()) {{
            sim.saveState(normalizeStarPath(RESULT_SIM_PATH));
            sim.println("[flow_control] saved result sim -> " + RESULT_SIM_PATH);
        }}
    }}

    private void applyMassFlow(Simulation sim, String boundaryName, double value) {{
        boolean requiresBoundary = Math.abs(value) > 1.0e-15;
        Boundary boundary = findBoundary(sim, boundaryName);
        if (boundary == null) {{
            if (!requiresBoundary) return;
            String message = "Boundary '" + boundaryName + "' not found for nonzero mass flow "
                + value + ". Available boundaries: " + availableBoundaryNames(sim);
            if (STRICT_BOUNDARIES) throw new RuntimeException(message);
            sim.println("WARNING: " + message + " Skipping mass-flow update.");
            return;
        }}
        try {{
            ensureMassFlowBoundary(boundary, boundaryName);
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

    private void ensureMassFlowBoundary(Boundary boundary, String requestedName) {{
        try {{
            boundary.setBoundaryType(MassFlowBoundary.class);
        }} catch (Exception e) {{
            throw new RuntimeException(
                "Failed to set boundary '" + requestedName + "' / '"
                + boundary.getPresentationName() + "' to MassFlowBoundary from "
                + boundaryTypeName(boundary) + ": " + e.getMessage()
            );
        }}
    }}

    private String boundaryTypeName(Boundary boundary) {{
        try {{
            return boundary.getBoundaryType().getPresentationName();
        }} catch (Exception ignored) {{
            return "(unknown boundary type)";
        }}
    }}

    private Boundary findBoundary(Simulation sim, String boundaryName) {{
        if (REGION_NAME != null && !REGION_NAME.trim().isEmpty()) {{
            try {{
                Region region = sim.getRegionManager().getRegion(REGION_NAME);
                Boundary boundary = findBoundaryInRegion(region, boundaryName);
                if (boundary != null) return boundary;
            }} catch (Exception ignored) {{}}
        }}
        for (Object obj : sim.getRegionManager().getObjects()) {{
            if (!(obj instanceof Region)) continue;
            Region region = (Region) obj;
            Boundary boundary = findBoundaryInRegion(region, boundaryName);
            if (boundary != null) return boundary;
        }}
        return null;
    }}

    private Boundary findBoundaryInRegion(Region region, String boundaryName) {{
        try {{
            Boundary boundary = region.getBoundaryManager().getBoundary(boundaryName);
            if (boundary != null) return boundary;
        }} catch (Exception ignored) {{}}
        String[] candidateNames = boundaryNameCandidates(boundaryName);
        for (int idx = 0; idx < candidateNames.length; idx++) {{
            try {{
                Boundary boundary = region.getBoundaryManager().getBoundary(candidateNames[idx]);
                if (boundary != null) return boundary;
            }} catch (Exception ignored) {{}}
        }}
        for (int idx = 0; idx < candidateNames.length; idx++) {{
            Boundary boundary = findBoundaryByPresentationName(region, candidateNames[idx]);
            if (boundary != null) return boundary;
        }}
        return null;
    }}

    private Boundary findBoundaryByPresentationName(Region region, String candidateName) {{
        for (Object obj : region.getBoundaryManager().getObjects()) {{
            if (!(obj instanceof Boundary)) continue;
            Boundary boundary = (Boundary) obj;
            try {{
                String presentationName = boundary.getPresentationName();
                if (presentationName.equalsIgnoreCase(candidateName)) return boundary;
                if (presentationName.toLowerCase(Locale.ROOT).endsWith("." + candidateName.toLowerCase(Locale.ROOT))) {{
                    return boundary;
                }}
            }} catch (Exception ignored) {{}}
        }}
        return null;
    }}

    private String[] boundaryNameCandidates(String boundaryName) {{
        String digits = trailingDigits(boundaryName);
        if (digits.length() == 0) {{
            return new String[] {{boundaryName}};
        }}
        return new String[] {{
            boundaryName,
            "J" + digits,
            "J_" + digits,
            "JET" + digits,
            "JET_" + digits
        }};
    }}

    private String trailingDigits(String value) {{
        int end = value.length();
        int start = end;
        while (start > 0 && Character.isDigit(value.charAt(start - 1))) {{
            start--;
        }}
        return value.substring(start, end);
    }}

    private String availableBoundaryNames(Simulation sim) {{
        ArrayList<String> names = new ArrayList<String>();
        for (Object regionObj : sim.getRegionManager().getObjects()) {{
            if (!(regionObj instanceof Region)) continue;
            Region region = (Region) regionObj;
            String regionName = region.getPresentationName();
            for (Object boundaryObj : region.getBoundaryManager().getObjects()) {{
                if (!(boundaryObj instanceof Boundary)) continue;
                Boundary boundary = (Boundary) boundaryObj;
                names.add(regionName + "/" + boundary.getPresentationName());
            }}
        }}
        return names.toString();
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
            Report report = findReport(sim, reportName);
            if (report instanceof ScalarReport) {{
                return ((ScalarReport) report).getValue();
            }}
            return report.getReportMonitorValue();
        }} catch (Exception e) {{
            sim.println("WARNING: report '" + reportName + "' unavailable: " + e.getMessage());
            return Double.NaN;
        }}
    }}

    private Report findReport(Simulation sim, String reportName) {{
        String[] candidateNames = reportNameCandidates(reportName);
        for (int idx = 0; idx < candidateNames.length; idx++) {{
            try {{
                Report report = (Report) sim.getReportManager().getObject(candidateNames[idx]);
                if (report != null) return report;
            }} catch (Exception ignored) {{}}
        }}
        for (Object obj : sim.getReportManager().getObjects()) {{
            if (!(obj instanceof Report)) continue;
            Report report = (Report) obj;
            try {{
                String presentationName = report.getPresentationName();
                for (int idx = 0; idx < candidateNames.length; idx++) {{
                    if (presentationName.equalsIgnoreCase(candidateNames[idx])) return report;
                }}
            }} catch (Exception ignored) {{}}
        }}
        throw new RuntimeException("report not found: " + reportName);
    }}

    private String[] reportNameCandidates(String reportName) {{
        if (reportName.startsWith("fc_load_")) {{
            String shortName = reportName.substring("fc_load_".length());
            return new String[] {{reportName, shortName}};
        }}
        if (reportName.equalsIgnoreCase("drag")) {{
            return new String[] {{reportName, "Drag"}};
        }}
        if (reportName.equalsIgnoreCase("total")) {{
            return new String[] {{reportName, "Fz"}};
        }}
        return new String[] {{reportName}};
    }}

    private String resolveOutputDir() {{
        try {{
            File macroDir = new File(getClass().getProtectionDomain().getCodeSource().getLocation().toURI());
            if (macroDir.isFile()) macroDir = macroDir.getParentFile();
            if (macroDir != null) return macroDir.getAbsolutePath();
        }} catch (Exception ignored) {{}}
        return ".";
    }}

    private String normalizeStarPath(String path) {{
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
        popen_command: str | list[str] = f'cmd /c "{inner}"'
    else:
        popen_command = command

    proc = subprocess.Popen(
        popen_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log_file.write(line)
        log_file.flush()
        progress = _progress_from_starccm_line(line)
        if progress is not None:
            _print_progress(progress)
    returncode = proc.wait()
    return subprocess.CompletedProcess(command, returncode)


def _progress_from_starccm_line(line: str) -> str | None:
    text = line.strip()
    if not text:
        return None
    if text.startswith("Loading:"):
        return "正在加载仿真文件"
    if text.startswith("Loading/configuring connectivity"):
        return "正在配置并行分区"
    if text.startswith("Configuring finished"):
        return "并行分区配置完成"
    if text.startswith("[flow_control] window="):
        return "正在执行 " + text.removeprefix("[flow_control] ")
    if text.startswith("[flow_control] completed window="):
        return "已完成 " + text.removeprefix("[flow_control] completed ")
    if text.startswith("[flow_control] saved result sim"):
        return "已保存结果 sim"
    if text.startswith("Saving:") and "flow_control_result.sim" in text:
        return "正在保存结果 sim"
    return None


def _print_progress(message: str) -> None:
    print(f"[flow_control] {message}", flush=True)


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


def _java_active_jet_flags(windows: list[_ScheduleWindow]) -> str:
    flags = [
        any(abs(window.massflows[idx]) > 1.0e-15 for window in windows)
        for idx in range(len(JET_COLUMNS))
    ]
    return ", ".join("true" if flag else "false" for flag in flags)


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
