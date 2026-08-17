"""Launch STAR-CCM+ with a generated flow-control actuation macro."""

from __future__ import annotations

import csv
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flow_control.adapters.starccm_adapter import FlowControlStarCCMAdapter
from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from flow_control.star_ingest.manifest_builder import finalize_manifest, prepare_preflight_manifest
from starccm.control.control_spec import DEFAULT_STARCCM_SPEC, JET_COLUMNS


_EXECUTION_MODES = {"run", "dry-run", "package-only", "validate-only"}


_STAR_BOTTOM_JET_BOUNDARY_RE = re.compile(r"^JET_?\d{1,2}$", re.IGNORECASE)


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
    execution_mode: str = "run"
    case_dir: Path | None = None
    require_complete_schema: bool = True
    manifest_template_path: Path | None = None


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
        mode = "dry-run" if config.dry_run else config.execution_mode.strip().lower()
        if mode not in _EXECUTION_MODES:
            raise ValueError(f"unsupported execution_mode {config.execution_mode!r}; expected one of {sorted(_EXECUTION_MODES)}")
        if mode == "run" and not sim_path.exists():
            raise FileNotFoundError(f"STAR-CCM+ .sim file not found: {sim_path}")

        output_dir.mkdir(parents=True, exist_ok=True)
        preflight_manifest_path: Path | None = None
        if config.manifest_template_path is not None:
            preflight_manifest_path = prepare_preflight_manifest(
                template_path=config.manifest_template_path.expanduser().resolve(),
                sim_path=sim_path,
                schedule_path=schedule_path,
                output_dir=output_dir,
                time_step=config.time_step,
            )
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
        timeseries_path = output_dir / "timeseries.csv"
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
        if mode == "dry-run":
            _print_progress("dry-run 完成，已生成宏和运行计划")
            return FlowControlStarCCMRunResult(
                macro_path=macro_path,
                runtime_plan_path=runtime_plan_path,
                log_path=log_path,
                command=tuple(command),
                result_sim_path=result_sim_path if config.save_result_sim else None,
                timeseries_path=timeseries_path,
            )

        # These modes intentionally do not invoke STAR.  They make it possible
        # to debug packaging and acceptance checks against an already-exported
        # runtime CSV without consuming a STAR license.
        if mode == "package-only":
            case_dir = config.case_dir or output_dir / "case_package"
            _package_runtime_csv(timeseries_path, copied_schedule, case_dir, config.require_complete_schema)
            _validate_case(case_dir, config.require_complete_schema)
            _print_progress(f"package-only 完成，已打包并校验 Case: {case_dir}")
            return FlowControlStarCCMRunResult(macro_path, runtime_plan_path, log_path, tuple(command), timeseries_path=timeseries_path)
        if mode == "validate-only":
            case_dir = config.case_dir or output_dir / "case_package"
            _validate_case(case_dir, config.require_complete_schema)
            _print_progress(f"validate-only 完成，Case: {case_dir}")
            return FlowControlStarCCMRunResult(macro_path, runtime_plan_path, log_path, tuple(command), timeseries_path=timeseries_path)

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

        if preflight_manifest_path is not None:
            snapshot_path = output_dir / "sim_template_snapshot.yaml"
            if not snapshot_path.is_file():
                raise RuntimeError(f"STAR completed without required template snapshot: {snapshot_path}")
            finalize_manifest(
                preflight_path=preflight_manifest_path,
                snapshot_path=snapshot_path,
                output_path=output_dir / "case_manifest.yaml",
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
    bottom_boundary_names = [
        name for name in boundary_names
        if _STAR_BOTTOM_JET_BOUNDARY_RE.fullmatch(name.strip())
    ]
    if bottom_boundary_names:
        raise ValueError(
            "mass-flow actuation must target STAR J01..J24 nozzle boundaries, "
            "not JET01..JET24 bottom-region boundaries: "
            + ", ".join(bottom_boundary_names)
        )
    effective_time_step = float(time_step or 0.0)
    csv_output_dir = output_dir or (result_sim_path.parent if result_sim_path else Path("."))
    return f"""import star.common.*;
import star.base.report.*;
import star.base.neo.*;
import star.flow.*;
import star.vis.*;
import java.io.*;
import java.util.*;

public class FlowControlRunMacro extends StarMacro {{
    static final String REGION_NAME = "{_java_literal(region_name)}";
    static final boolean STRICT_BOUNDARIES = {str(strict_boundaries).lower()};
    static final double REQUESTED_TIME_STEP = {_java_float(effective_time_step)};
    static final String OUTPUT_DIR = "{_java_literal(str(csv_output_dir.resolve()).replace(os.sep, '/'))}";
    static final String SCHEDULE_CSV_PATH = "{_java_literal(str((csv_output_dir / 'actuation_schedule.csv').resolve()).replace(os.sep, '/'))}";
    static final String RESULT_SIM_PATH = "{_java_literal(str(result_sim_path.resolve()).replace(os.sep, '/') if result_sim_path else '')}";
    static final String[] BOUNDARY_NAMES = new String[] {{{", ".join(_quoted(name) for name in boundary_names)}}};
    static final String[] REPORT_NAMES = new String[] {{{", ".join(_quoted(name) for name in report_names)}}};
    static final String[] ACTUAL_MASSFLOW_REPORT_NAMES = new String[] {{{", ".join(_quoted(f"actual_massflow_{idx:02d}") for idx in range(1, 25))}}};
    static final boolean[] ACTIVE_JETS = new boolean[] {{{_java_active_jet_flags(windows)}}};

    public void execute() {{
        Simulation sim = getActiveSimulation();
        File outDir = new File(normalizeStarPath(OUTPUT_DIR));
        outDir.mkdirs();
        writeTemplateSnapshot(sim, outDir);
        ScheduleData schedule = readSchedule(new File(normalizeStarPath(SCHEDULE_CSV_PATH)));
        File csv = new File(outDir, "timeseries.csv");
        ensureActualMassFlowReports(sim);
        writeHeader(csv);
        for (int window = 0; window < schedule.windowIds.length; window++) {{
            double duration = schedule.tEnd[window] - schedule.tStart[window];
            double step = REQUESTED_TIME_STEP > 0.0 ? REQUESTED_TIME_STEP : getTransientTimeStep(sim);
            if (step <= 0.0) {{
                throw new RuntimeException("Non-positive time step at window " + schedule.windowIds[window]);
            }}
            if (REQUESTED_TIME_STEP > 0.0) {{
                setTransientTimeStep(sim, step);
            }}
            for (int jet = 0; jet < BOUNDARY_NAMES.length; jet++) {{
                applyMassFlow(sim, BOUNDARY_NAMES[jet], schedule.massflow[window][jet]);
            }}
            int steps = Math.max(1, (int) Math.round(duration / step));
            sim.println("[flow_control] window=" + schedule.windowIds[window]
                + " t=[" + schedule.tStart[window] + "," + schedule.tEnd[window] + "]"
                + " duration=" + duration + " step=" + step + " solverSteps=" + steps);
            sim.getSimulationIterator().run(steps);
            appendRow(sim, csv, schedule.tEnd[window], schedule.windowIds[window], step, duration, duration, schedule.massflow[window]);
            sim.println("[flow_control] completed window=" + schedule.windowIds[window]
                + " csv=" + csv.getAbsolutePath());
        }}
        exportRequiredMonitorPlots(sim, outDir);
        if (RESULT_SIM_PATH != null && !RESULT_SIM_PATH.trim().isEmpty()) {{
            sim.saveState(normalizeStarPath(RESULT_SIM_PATH));
            sim.println("[flow_control] saved result sim -> " + RESULT_SIM_PATH);
        }}
    }}

    static class ScheduleData {{
        int[] windowIds;
        double[] tStart;
        double[] tEnd;
        double[][] massflow;
    }}

    private void writeTemplateSnapshot(Simulation sim, File outDir) {{
        File snapshot = new File(outDir, "sim_template_snapshot.yaml");
        try {{
            PrintWriter writer = new PrintWriter(new FileWriter(snapshot, false));
            writer.println("snapshot_status: ok");
            writer.println("snapshot_stage: star_loaded_pre_solve");
            writer.println("surface_properties:");
            writer.println("  source: STAR-CCM+ template inspection before solve");
            writer.println("  area_unit: m^2");
            writer.println("  normal_coordinate_system: Laboratory");
            writer.println("  surfaces:");
            for (int index = 1; index <= 24; index++) {{
                writeSurfaceSnapshot(writer, sim, "J" + twoDigit(index));
                writeSurfaceSnapshot(writer, sim, "JET" + twoDigit(index));
            }}
            writer.close();
            sim.println("[flow_control] wrote pre-solve STAR template snapshot -> " + snapshot.getAbsolutePath());
        }} catch (Exception e) {{
            throw new RuntimeException("Failed to inspect STAR template before solve: " + e.getMessage());
        }}
    }}

    private void writeSurfaceSnapshot(PrintWriter writer, Simulation sim, String boundaryName) {{
        Boundary boundary = findExactBoundary(sim, boundaryName);
        if (boundary == null) {{
            throw new RuntimeException("Required STAR template boundary missing: " + boundaryName
                + ". Available boundaries: " + availableBoundaryNames(sim));
        }}
        // STAR-CCM+ 17.06 does not expose the legacy area-report class. Integrating
        // the built-in Area field function over the boundary is version-safe.
        SurfaceIntegralReport areaReport = sim.getReportManager().createReport(SurfaceIntegralReport.class);
        areaReport.setFieldFunction(sim.getFieldFunctionManager().getFunction("Area"));
        areaReport.getParts().setObjects(new NeoObjectVector(new Object[] {{boundary}}));
        double area = areaReport.getValue();
        try {{ sim.getReportManager().remove(areaReport); }} catch (Exception ignored) {{}}
        writer.println("    " + boundaryName + ":");
        writer.println("      area_m2: " + area);
        writer.println("      normal_xyz: [0.0, 0.0, 1.0]");
        writer.println("      boundary_type: " + yamlQuote(boundaryTypeName(boundary)));
    }}

    private String yamlQuote(String value) {{
        return "\\\"" + value.replace("\\\"", "\\\\\\\"") + "\\\"";
    }}

    private Boundary findExactBoundary(Simulation sim, String boundaryName) {{
        for (Object regionObj : sim.getRegionManager().getObjects()) {{
            if (!(regionObj instanceof Region)) continue;
            Region region = (Region) regionObj;
            try {{
                Boundary exact = region.getBoundaryManager().getBoundary(boundaryName);
                if (exact != null) return exact;
            }} catch (Exception ignored) {{}}
            for (Object boundaryObj : region.getBoundaryManager().getObjects()) {{
                if (!(boundaryObj instanceof Boundary)) continue;
                Boundary boundary = (Boundary) boundaryObj;
                String presentationName = boundary.getPresentationName();
                if (boundaryName.equalsIgnoreCase(presentationName)
                    || presentationName.toLowerCase(Locale.ROOT).endsWith(
                        "." + boundaryName.toLowerCase(Locale.ROOT)
                    )) return boundary;
            }}
        }}
        return null;
    }}

    private ScheduleData readSchedule(File csv) {{
        ArrayList<Integer> windowIds = new ArrayList<Integer>();
        ArrayList<Double> tStart = new ArrayList<Double>();
        ArrayList<Double> tEnd = new ArrayList<Double>();
        ArrayList<double[]> massflow = new ArrayList<double[]>();
        try {{
            BufferedReader reader = new BufferedReader(new FileReader(csv));
            String headerLine = reader.readLine();
            if (headerLine == null) {{
                reader.close();
                throw new RuntimeException("empty schedule CSV: " + csv.getAbsolutePath());
            }}
            String[] headers = splitCsvLine(headerLine);
            HashMap<String, Integer> index = new HashMap<String, Integer>();
            for (int i = 0; i < headers.length; i++) {{
                index.put(headers[i].trim(), Integer.valueOf(i));
            }}
            for (int jet = 1; jet <= BOUNDARY_NAMES.length; jet++) {{
                requireColumn(index, "cmd_massflow_" + twoDigit(jet), csv);
            }}
            requireColumn(index, "window_id", csv);
            requireColumn(index, "t_start", csv);
            requireColumn(index, "t_end", csv);
            String line;
            while ((line = reader.readLine()) != null) {{
                if (line.trim().isEmpty()) continue;
                String[] values = splitCsvLine(line);
                int parsedWindowId = (int) Math.round(parseCsvDouble(values, index, "window_id"));
                double parsedStart = parseCsvDouble(values, index, "t_start");
                double parsedEnd = parseCsvDouble(values, index, "t_end");
                if (parsedEnd <= parsedStart) {{
                    throw new RuntimeException("non-positive physical time step in window " + parsedWindowId);
                }}
                double[] row = new double[BOUNDARY_NAMES.length];
                for (int jet = 1; jet <= BOUNDARY_NAMES.length; jet++) {{
                    row[jet - 1] = parseCsvDouble(values, index, "cmd_massflow_" + twoDigit(jet));
                }}
                if (windowIds.isEmpty() || windowIds.get(windowIds.size() - 1).intValue() != parsedWindowId) {{
                    if (!windowIds.isEmpty()) {{
                        int previous = windowIds.get(windowIds.size() - 1).intValue();
                        if (parsedWindowId != previous + 1) {{
                            throw new RuntimeException("window_id must increase continuously: "
                                + previous + " -> " + parsedWindowId);
                        }}
                        double previousEnd = tEnd.get(tEnd.size() - 1).doubleValue();
                        if (Math.abs(parsedStart - previousEnd) > 1.0e-12) {{
                            throw new RuntimeException("physical time is not continuous before window " + parsedWindowId);
                        }}
                    }}
                    windowIds.add(Integer.valueOf(parsedWindowId));
                    tStart.add(Double.valueOf(parsedStart));
                    tEnd.add(Double.valueOf(parsedEnd));
                    massflow.add(row);
                }} else {{
                    int last = windowIds.size() - 1;
                    if (Math.abs(parsedStart - tEnd.get(last).doubleValue()) > 1.0e-12) {{
                        throw new RuntimeException("physical time is not continuous inside window " + parsedWindowId);
                    }}
                    double[] command = massflow.get(last);
                    for (int jet = 0; jet < command.length; jet++) {{
                        if (Math.abs(row[jet] - command[jet]) > 1.0e-12) {{
                            throw new RuntimeException("mass-flow command changes inside window "
                                + parsedWindowId + " at jet " + twoDigit(jet + 1));
                        }}
                    }}
                    tEnd.set(last, Double.valueOf(parsedEnd));
                }}
            }}
            reader.close();
        }} catch (IOException e) {{
            throw new RuntimeException("failed to read schedule CSV " + csv.getAbsolutePath() + ": " + e.getMessage());
        }}
        if (windowIds.isEmpty()) {{
            throw new RuntimeException("schedule CSV contains no data rows: " + csv.getAbsolutePath());
        }}
        ScheduleData data = new ScheduleData();
        data.windowIds = new int[windowIds.size()];
        data.tStart = new double[tStart.size()];
        data.tEnd = new double[tEnd.size()];
        data.massflow = new double[massflow.size()][];
        for (int i = 0; i < windowIds.size(); i++) {{
            data.windowIds[i] = windowIds.get(i).intValue();
            data.tStart[i] = tStart.get(i).doubleValue();
            data.tEnd[i] = tEnd.get(i).doubleValue();
            data.massflow[i] = massflow.get(i);
        }}
        return data;
    }}

    private void requireColumn(HashMap<String, Integer> index, String column, File csv) {{
        if (!index.containsKey(column)) {{
            throw new RuntimeException("schedule CSV missing column '" + column + "': " + csv.getAbsolutePath());
        }}
    }}

    private double parseCsvDouble(String[] values, HashMap<String, Integer> index, String column) {{
        Integer pos = index.get(column);
        if (pos == null || pos.intValue() >= values.length) {{
            throw new RuntimeException("schedule CSV missing value for column '" + column + "'");
        }}
        return Double.parseDouble(values[pos.intValue()].trim());
    }}

    private String twoDigit(int value) {{
        return value < 10 ? "0" + value : String.valueOf(value);
    }}

    private String[] splitCsvLine(String line) {{
        ArrayList<String> fields = new ArrayList<String>();
        StringBuffer current = new StringBuffer();
        boolean inQuotes = false;
        for (int i = 0; i < line.length(); i++) {{
            char ch = line.charAt(i);
            if (ch == '"') {{
                if (inQuotes && i + 1 < line.length() && line.charAt(i + 1) == '"') {{
                    current.append('"');
                    i++;
                }} else {{
                    inQuotes = !inQuotes;
                }}
            }} else if (ch == ',' && !inQuotes) {{
                fields.add(current.toString());
                current.setLength(0);
            }} else {{
                current.append(ch);
            }}
        }}
        fields.add(current.toString());
        return fields.toArray(new String[fields.size()]);
    }}

    private void applyMassFlow(Simulation sim, String boundaryName, double value) {{
        if (isBottomJetBoundaryName(boundaryName)) {{
            throw new RuntimeException(
                "Refusing to apply mass flow to bottom-region boundary '" + boundaryName
                + "'. Use J01..J24 nozzle boundaries for actuation."
            );
        }}
        Boundary boundary = findBoundary(sim, boundaryName);
        if (boundary == null) {{
            String message = "Boundary '" + boundaryName + "' not found while setting mass flow "
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
            "J_" + digits
        }};
    }}

    private boolean isBottomJetBoundaryName(String boundaryName) {{
        String normalized = boundaryName == null
            ? ""
            : boundaryName.trim().replace("_", "").toUpperCase(Locale.ROOT);
        if (!normalized.startsWith("JET")) return false;
        if (normalized.length() <= 3) return false;
        for (int idx = 3; idx < normalized.length(); idx++) {{
            if (!Character.isDigit(normalized.charAt(idx))) return false;
        }}
        return true;
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

    private double getTransientTimeStep(Simulation sim) {{
        try {{
            ImplicitUnsteadySolver solver =
                (ImplicitUnsteadySolver) sim.getSolverManager().getSolver(ImplicitUnsteadySolver.class);
            if (solver != null) {{
                return solver.getTimeStep().getValue();
            }}
        }} catch (Exception e) {{
            sim.println("WARNING: transient time-step read failed: " + e.getMessage());
        }}
        throw new RuntimeException("Unable to read template transient time step.");
    }}

    private void writeHeader(File csv) {{
        try {{
            PrintWriter writer = new PrintWriter(new FileWriter(csv, false));
            writer.print("physical_time,window_id,solver_dt_s,action_window_s,sample_interval_s");
            for (int i = 0; i < BOUNDARY_NAMES.length; i++) {{
                writer.print(",cmd_massflow_" + twoDigit(i + 1));
            }}
            for (int i = 0; i < ACTUAL_MASSFLOW_REPORT_NAMES.length; i++) {{
                writer.print("," + ACTUAL_MASSFLOW_REPORT_NAMES[i]);
            }}
            for (int i = 0; i < REPORT_NAMES.length; i++) {{
                writer.print("," + REPORT_NAMES[i]);
            }}
            writer.println();
            writer.close();
        }} catch (Exception e) {{
            throw new RuntimeException("Failed to write " + csv + ": " + e.getMessage());
        }}
    }}

    private void appendRow(Simulation sim, File csv, double physicalTime, int windowId,
        double solverDt, double actionWindow, double sampleInterval, double[] commandMassflow) {{
        try {{
            PrintWriter writer = new PrintWriter(new FileWriter(csv, true));
            writer.print(physicalTime + "," + windowId + "," + solverDt + "," + actionWindow + "," + sampleInterval);
            for (int i = 0; i < commandMassflow.length; i++) writer.print("," + commandMassflow[i]);
            for (int i = 0; i < ACTUAL_MASSFLOW_REPORT_NAMES.length; i++) {{
                writer.print("," + requiredReportValue(sim, ACTUAL_MASSFLOW_REPORT_NAMES[i]));
            }}
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

    private double requiredReportValue(Simulation sim, String reportName) {{
        Report report = findReport(sim, reportName);
        if (report instanceof ScalarReport) return ((ScalarReport) report).getValue();
        return report.getReportMonitorValue();
    }}

    private void ensureActualMassFlowReports(Simulation sim) {{
        for (int jet = 0; jet < BOUNDARY_NAMES.length; jet++) {{
            Boundary boundary = findBoundary(sim, BOUNDARY_NAMES[jet]);
            if (boundary == null) {{
                throw new RuntimeException("Cannot create actual mass-flow report: boundary '" + BOUNDARY_NAMES[jet]
                    + "' is missing. Available boundaries: " + availableBoundaryNames(sim));
            }}
            String reportName = ACTUAL_MASSFLOW_REPORT_NAMES[jet];
            try {{
                Report existing = findReport(sim, reportName);
                if (existing instanceof MassFlowReport) continue;
                throw new RuntimeException("Report '" + reportName + "' exists but is not a MassFlowReport");
            }} catch (RuntimeException missing) {{
                if (!missing.getMessage().startsWith("report not found:")) throw missing;
            }}
            MassFlowReport report = sim.getReportManager().createReport(MassFlowReport.class);
            report.setPresentationName(reportName);
            report.getParts().setObjects(new NeoObjectVector(new Object[] {{boundary}}));
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
            return new String[] {{
                reportName,
                shortName,
                shortName + " Monitor",
                reportName + " Monitor"
            }};
        }}
        if (reportName.equalsIgnoreCase("drag")) {{
            return new String[] {{reportName, "Drag", "Drag Monitor"}};
        }}
        if (reportName.equalsIgnoreCase("total")) {{
            return new String[] {{
                reportName,
                "Fz",
                "Fz Monitor",
                "Fz Total",
                "Fz Total Monitor"
            }};
        }}
        if (reportName.equalsIgnoreCase("Fz_Total")) {{
            return new String[] {{reportName, "Fz", "Fz Monitor", "Fz Total", "Fz Total Monitor"}};
        }}
        if (reportName.equalsIgnoreCase("Drag_Total")) {{
            return new String[] {{reportName, "Drag", "Drag Monitor"}};
        }}
        if (reportName.equalsIgnoreCase("Pitch_Moment")) {{
            return new String[] {{reportName, "Pitch_Moment Monitor", "Pitch Moment", "Pitch Moment Monitor"}};
        }}
        if (reportName.equalsIgnoreCase("Roll_Moment")) {{
            return new String[] {{reportName, "Roll_Moment Monitor", "Roll Moment", "Roll Moment Monitor"}};
        }}
        if (reportName.equalsIgnoreCase("Jet_Reaction_Z")) {{
            return new String[] {{
                reportName,
                "Jet_Reaction_Z Monitor",
                "Jet Reaction Z",
                "Jet Reaction Z Monitor"
            }};
        }}
        return new String[] {{reportName, reportName + " Monitor"}};
    }}

    private void exportRequiredMonitorPlots(Simulation sim, File outDir) {{
        exportMonitorPlot(sim, outDir,
            new String[] {{"FZ", "FZ Plot", "FZ 绘图"}},
            "FZ_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {{"Fz Monitor", "Fz Monitor Plot", "Fz Monitor 绘图"}},
            "Fz_Monitor_绘图_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {{"Drag Monitor", "Drag Monitor Plot", "Drag Monitor 绘图"}},
            "Drag_Monitor_绘图_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {{
                "Pitch_Moment Monitor", "Pitch_Moment Monitor Plot", "Pitch_Moment Monitor 绘图"
            }},
            "Pitch_Moment_Monitor_绘图_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {{
                "Roll_Moment Monitor", "Roll_Moment Monitor Plot", "Roll_Moment Monitor 绘图"
            }},
            "Roll_Moment_Monitor_绘图_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {{
                "Jet_Reaction_Z Monitor",
                "Jet_Reaction_Z Monitor Plot",
                "Jet_Reaction_Z Monitor 绘图"
            }},
            "Jet_Reaction_Z_Monitor_绘图_image.csv");
    }}

    private void exportMonitorPlot(
        Simulation sim,
        File outDir,
        String[] candidateNames,
        String fileName
    ) {{
        StarPlot plot = findPlot(sim, candidateNames);
        if (plot == null) {{
            sim.println(
                "WARNING: monitor plot not found for CSV '" + fileName
                + "'. Tried " + Arrays.toString(candidateNames)
                + ". Available plots: " + availablePlotNames(sim)
            );
            return;
        }}
        File output = new File(outDir, fileName);
        try {{
            plot.export(normalizeStarPath(output.getAbsolutePath()), ",");
            sim.println("[flow_control] exported monitor CSV -> " + output.getAbsolutePath());
        }} catch (Exception e) {{
            sim.println(
                "WARNING: failed to export monitor plot '" + plot.getPresentationName()
                + "' to '" + output.getAbsolutePath() + "': " + e.getMessage()
            );
        }}
    }}

    private StarPlot findPlot(Simulation sim, String[] candidateNames) {{
        for (int idx = 0; idx < candidateNames.length; idx++) {{
            try {{
                StarPlot plot = sim.getPlotManager().getPlot(candidateNames[idx]);
                if (plot != null) return plot;
            }} catch (Exception ignored) {{}}
        }}
        // Prefer a case-sensitive English prefix before falling back to
        // case-insensitive matching.  Localized STAR installations can expose
        // the translated "Plot" suffix as mojibake in batch mode, while the
        // report/monitor prefix remains stable.
        for (Object obj : sim.getPlotManager().getObjects()) {{
            if (!(obj instanceof StarPlot)) continue;
            StarPlot plot = (StarPlot) obj;
            try {{
                String presentationName = plot.getPresentationName();
                for (int idx = 0; idx < candidateNames.length; idx++) {{
                    String candidate = candidateNames[idx];
                    if (presentationName.equals(candidate)
                        || presentationName.startsWith(candidate + " ")) {{
                        return plot;
                    }}
                }}
            }} catch (Exception ignored) {{}}
        }}
        for (Object obj : sim.getPlotManager().getObjects()) {{
            if (!(obj instanceof StarPlot)) continue;
            StarPlot plot = (StarPlot) obj;
            try {{
                String presentationName = plot.getPresentationName();
                for (int idx = 0; idx < candidateNames.length; idx++) {{
                    String candidate = candidateNames[idx];
                    if (presentationName.equalsIgnoreCase(candidate)
                        || presentationName.toLowerCase(Locale.ROOT).startsWith(
                            candidate.toLowerCase(Locale.ROOT) + " "
                        )) {{
                        return plot;
                    }}
                }}
            }} catch (Exception ignored) {{}}
        }}
        return null;
    }}

    private String availablePlotNames(Simulation sim) {{
        ArrayList<String> names = new ArrayList<String>();
        for (Object obj : sim.getPlotManager().getObjects()) {{
            if (!(obj instanceof StarPlot)) continue;
            try {{
                names.add(((StarPlot) obj).getPresentationName());
            }} catch (Exception ignored) {{}}
        }}
        return names.toString();
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
        massflows = tuple(values)
        if not windows or windows[-1].window_id != window_id:
            if windows:
                previous = windows[-1]
                if window_id != previous.window_id + 1:
                    raise ValueError(
                        f"row {fallback_idx} window_id must increase from "
                        f"{previous.window_id} to {previous.window_id + 1}"
                    )
                if abs(t_start - previous.t_end) > 1.0e-12:
                    raise ValueError(f"row {fallback_idx} is not contiguous with the previous window")
            windows.append(
                _ScheduleWindow(
                    window_id=window_id,
                    t_start=t_start,
                    t_end=t_end,
                    massflows=massflows,
                )
            )
            continue

        previous = windows[-1]
        if abs(t_start - previous.t_end) > 1.0e-12:
            raise ValueError(f"row {fallback_idx} is not contiguous inside window_id {window_id}")
        if any(abs(current - expected) > 1.0e-12 for current, expected in zip(massflows, previous.massflows)):
            raise ValueError(f"row {fallback_idx} changes mass flow inside window_id {window_id}")
        windows[-1] = _ScheduleWindow(
            window_id=window_id,
            t_start=previous.t_start,
            t_end=t_end,
            massflows=previous.massflows,
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


def _package_runtime_csv(
    timeseries_path: Path, schedule_path: Path, case_dir: Path, require_complete_schema: bool
) -> None:
    if not timeseries_path.is_file():
        raise FileNotFoundError(f"package-only requires an existing runtime CSV: {timeseries_path}")
    from flow_control.star_ingest.ccm_package import package_ccm_run_case

    result = package_ccm_run_case(
        ccm_timeseries_path=timeseries_path,
        schedule_path=schedule_path,
        case_dir=case_dir,
        require_complete_schema=require_complete_schema,
    )
    errors = result["quality_report"].get("errors", [])
    if errors:
        raise RuntimeError("package-only produced an incomplete case: " + "; ".join(errors))


def _validate_case(case_dir: Path, require_complete_schema: bool) -> None:
    from flow_control.star_ingest.case_data_loader import load_case

    result = load_case(case_dir, require_complete_schema=require_complete_schema, check_mode="ccm")
    if result["errors"]:
        raise RuntimeError("validate-only failed: " + "; ".join(result["errors"]))
