import star.common.*;
import star.base.report.*;
import star.flow.*;
import star.vis.*;
import java.io.*;
import java.util.*;

public class FlowControlRunMacro extends StarMacro {
    static final String REGION_NAME = "Region";
    static final boolean STRICT_BOUNDARIES = true;
    static final double REQUESTED_TIME_STEP = 0.1;
    static final String OUTPUT_DIR = "C:/Users/15465/generic_automation_v27_NS_RL100_PF_RULE_2/runs/ccm_runs/pulse_singlejet_test";
    static final String RESULT_SIM_PATH = "";
    static final String[] BOUNDARY_NAMES = new String[] {"fc_jet_01", "fc_jet_02", "fc_jet_03", "fc_jet_04", "fc_jet_05", "fc_jet_06", "fc_jet_07", "fc_jet_08", "fc_jet_09", "fc_jet_10", "fc_jet_11", "fc_jet_12", "fc_jet_13", "fc_jet_14", "fc_jet_15", "fc_jet_16", "fc_jet_17", "fc_jet_18", "fc_jet_19", "fc_jet_20", "fc_jet_21", "fc_jet_22", "fc_jet_23", "fc_jet_24"};
    static final String[] REPORT_NAMES = new String[] {"fc_load_S1L", "fc_load_S1R", "fc_load_S2L", "fc_load_S2R", "fc_load_S3L", "fc_load_S3R"};
    static final int[] WINDOW_IDS = new int[] {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};
    static final double[] T_START = new double[] {0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9};
    static final double[] T_END = new double[] {0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0};
    static final boolean[] ACTIVE_JETS = new boolean[] {false, false, true, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false};
    static final double[][] MASSFLOW = new double[][] {
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.02, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        new double[] {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}
    };

    public void execute() {
        Simulation sim = getActiveSimulation();
        File outDir = new File(normalizeStarPath(OUTPUT_DIR));
        outDir.mkdirs();
        File csv = new File(outDir, "flow_control_timeseries.csv");
        writeHeader(csv);
        for (int window = 0; window < WINDOW_IDS.length; window++) {
            double duration = T_END[window] - T_START[window];
            double step = REQUESTED_TIME_STEP > 0.0 ? REQUESTED_TIME_STEP : getTransientTimeStep(sim);
            if (step <= 0.0) {
                throw new RuntimeException("Non-positive time step at window " + WINDOW_IDS[window]);
            }
            if (REQUESTED_TIME_STEP > 0.0) {
                setTransientTimeStep(sim, step);
            }
            for (int jet = 0; jet < BOUNDARY_NAMES.length; jet++) {
                if (!ACTIVE_JETS[jet]) continue;
                applyMassFlow(sim, BOUNDARY_NAMES[jet], MASSFLOW[window][jet]);
            }
            int steps = Math.max(1, (int) Math.round(duration / step));
            sim.println("[flow_control] window=" + WINDOW_IDS[window]
                + " t=[" + T_START[window] + "," + T_END[window] + "]"
                + " duration=" + duration + " step=" + step + " solverSteps=" + steps);
            sim.getSimulationIterator().run(steps);
            appendRow(sim, csv, window);
            sim.println("[flow_control] completed window=" + WINDOW_IDS[window]
                + " csv=" + csv.getAbsolutePath());
        }
        exportRequiredMonitorPlots(sim, outDir);
        if (RESULT_SIM_PATH != null && !RESULT_SIM_PATH.trim().isEmpty()) {
            sim.saveState(normalizeStarPath(RESULT_SIM_PATH));
            sim.println("[flow_control] saved result sim -> " + RESULT_SIM_PATH);
        }
    }

    private void applyMassFlow(Simulation sim, String boundaryName, double value) {
        boolean requiresBoundary = Math.abs(value) > 1.0e-15;
        Boundary boundary = findBoundary(sim, boundaryName);
        if (boundary == null) {
            if (!requiresBoundary) return;
            String message = "Boundary '" + boundaryName + "' not found for nonzero mass flow "
                + value + ". Available boundaries: " + availableBoundaryNames(sim);
            if (STRICT_BOUNDARIES) throw new RuntimeException(message);
            sim.println("WARNING: " + message + " Skipping mass-flow update.");
            return;
        }
        try {
            ensureMassFlowBoundary(boundary, boundaryName);
            MassFlowRateProfile profile = boundary.getValues().get(MassFlowRateProfile.class);
            Units units = ((Units) sim.getUnitsManager().getObject("kg/s"));
            profile.getMethod(ConstantScalarProfileMethod.class)
                .getQuantity().setValueAndUnits(value, units);
        } catch (Exception e) {
            String message = "Failed to set mass flow on boundary '" + boundaryName + "': " + e.getMessage();
            if (STRICT_BOUNDARIES) throw new RuntimeException(message);
            sim.println("WARNING: " + message);
        }
    }

    private void ensureMassFlowBoundary(Boundary boundary, String requestedName) {
        try {
            boundary.setBoundaryType(MassFlowBoundary.class);
        } catch (Exception e) {
            throw new RuntimeException(
                "Failed to set boundary '" + requestedName + "' / '"
                + boundary.getPresentationName() + "' to MassFlowBoundary from "
                + boundaryTypeName(boundary) + ": " + e.getMessage()
            );
        }
    }

    private String boundaryTypeName(Boundary boundary) {
        try {
            return boundary.getBoundaryType().getPresentationName();
        } catch (Exception ignored) {
            return "(unknown boundary type)";
        }
    }

    private Boundary findBoundary(Simulation sim, String boundaryName) {
        if (REGION_NAME != null && !REGION_NAME.trim().isEmpty()) {
            try {
                Region region = sim.getRegionManager().getRegion(REGION_NAME);
                Boundary boundary = findBoundaryInRegion(region, boundaryName);
                if (boundary != null) return boundary;
            } catch (Exception ignored) {}
        }
        for (Object obj : sim.getRegionManager().getObjects()) {
            if (!(obj instanceof Region)) continue;
            Region region = (Region) obj;
            Boundary boundary = findBoundaryInRegion(region, boundaryName);
            if (boundary != null) return boundary;
        }
        return null;
    }

    private Boundary findBoundaryInRegion(Region region, String boundaryName) {
        try {
            Boundary boundary = region.getBoundaryManager().getBoundary(boundaryName);
            if (boundary != null) return boundary;
        } catch (Exception ignored) {}
        String[] candidateNames = boundaryNameCandidates(boundaryName);
        for (int idx = 0; idx < candidateNames.length; idx++) {
            try {
                Boundary boundary = region.getBoundaryManager().getBoundary(candidateNames[idx]);
                if (boundary != null) return boundary;
            } catch (Exception ignored) {}
        }
        for (int idx = 0; idx < candidateNames.length; idx++) {
            Boundary boundary = findBoundaryByPresentationName(region, candidateNames[idx]);
            if (boundary != null) return boundary;
        }
        return null;
    }

    private Boundary findBoundaryByPresentationName(Region region, String candidateName) {
        for (Object obj : region.getBoundaryManager().getObjects()) {
            if (!(obj instanceof Boundary)) continue;
            Boundary boundary = (Boundary) obj;
            try {
                String presentationName = boundary.getPresentationName();
                if (presentationName.equalsIgnoreCase(candidateName)) return boundary;
                if (presentationName.toLowerCase(Locale.ROOT).endsWith("." + candidateName.toLowerCase(Locale.ROOT))) {
                    return boundary;
                }
            } catch (Exception ignored) {}
        }
        return null;
    }

    private String[] boundaryNameCandidates(String boundaryName) {
        String digits = trailingDigits(boundaryName);
        if (digits.length() == 0) {
            return new String[] {boundaryName};
        }
        return new String[] {
            boundaryName,
            "J" + digits,
            "J_" + digits,
            "JET" + digits,
            "JET_" + digits
        };
    }

    private String trailingDigits(String value) {
        int end = value.length();
        int start = end;
        while (start > 0 && Character.isDigit(value.charAt(start - 1))) {
            start--;
        }
        return value.substring(start, end);
    }

    private String availableBoundaryNames(Simulation sim) {
        ArrayList<String> names = new ArrayList<String>();
        for (Object regionObj : sim.getRegionManager().getObjects()) {
            if (!(regionObj instanceof Region)) continue;
            Region region = (Region) regionObj;
            String regionName = region.getPresentationName();
            for (Object boundaryObj : region.getBoundaryManager().getObjects()) {
                if (!(boundaryObj instanceof Boundary)) continue;
                Boundary boundary = (Boundary) boundaryObj;
                names.add(regionName + "/" + boundary.getPresentationName());
            }
        }
        return names.toString();
    }

    private void setTransientTimeStep(Simulation sim, double step) {
        try {
            ImplicitUnsteadySolver solver =
                (ImplicitUnsteadySolver) sim.getSolverManager().getSolver(ImplicitUnsteadySolver.class);
            if (solver != null) {
                solver.getTimeStep().setValue(step);
            }
        } catch (Exception e) {
            sim.println("WARNING: transient time-step update skipped: " + e.getMessage());
        }
    }

    private double getTransientTimeStep(Simulation sim) {
        try {
            ImplicitUnsteadySolver solver =
                (ImplicitUnsteadySolver) sim.getSolverManager().getSolver(ImplicitUnsteadySolver.class);
            if (solver != null) {
                return solver.getTimeStep().getValue();
            }
        } catch (Exception e) {
            sim.println("WARNING: transient time-step read failed: " + e.getMessage());
        }
        throw new RuntimeException("Unable to read template transient time step.");
    }

    private void writeHeader(File csv) {
        try {
            PrintWriter writer = new PrintWriter(new FileWriter(csv, false));
            writer.print("physical_time,window_id");
            for (int i = 0; i < REPORT_NAMES.length; i++) {
                writer.print("," + REPORT_NAMES[i]);
            }
            writer.println();
            writer.close();
        } catch (Exception e) {
            throw new RuntimeException("Failed to write " + csv + ": " + e.getMessage());
        }
    }

    private void appendRow(Simulation sim, File csv, int window) {
        try {
            PrintWriter writer = new PrintWriter(new FileWriter(csv, true));
            writer.print(T_END[window] + "," + WINDOW_IDS[window]);
            for (int i = 0; i < REPORT_NAMES.length; i++) {
                writer.print("," + reportValue(sim, REPORT_NAMES[i]));
            }
            writer.println();
            writer.close();
        } catch (Exception e) {
            throw new RuntimeException("Failed to append " + csv + ": " + e.getMessage());
        }
    }

    private double reportValue(Simulation sim, String reportName) {
        try {
            Report report = findReport(sim, reportName);
            if (report instanceof ScalarReport) {
                return ((ScalarReport) report).getValue();
            }
            return report.getReportMonitorValue();
        } catch (Exception e) {
            sim.println("WARNING: report '" + reportName + "' unavailable: " + e.getMessage());
            return Double.NaN;
        }
    }

    private Report findReport(Simulation sim, String reportName) {
        String[] candidateNames = reportNameCandidates(reportName);
        for (int idx = 0; idx < candidateNames.length; idx++) {
            try {
                Report report = (Report) sim.getReportManager().getObject(candidateNames[idx]);
                if (report != null) return report;
            } catch (Exception ignored) {}
        }
        for (Object obj : sim.getReportManager().getObjects()) {
            if (!(obj instanceof Report)) continue;
            Report report = (Report) obj;
            try {
                String presentationName = report.getPresentationName();
                for (int idx = 0; idx < candidateNames.length; idx++) {
                    if (presentationName.equalsIgnoreCase(candidateNames[idx])) return report;
                }
            } catch (Exception ignored) {}
        }
        throw new RuntimeException("report not found: " + reportName);
    }

    private String[] reportNameCandidates(String reportName) {
        if (reportName.startsWith("fc_load_")) {
            String shortName = reportName.substring("fc_load_".length());
            return new String[] {
                reportName,
                shortName,
                shortName + " Monitor",
                reportName + " Monitor"
            };
        }
        if (reportName.equalsIgnoreCase("drag")) {
            return new String[] {reportName, "Drag", "Drag Monitor"};
        }
        if (reportName.equalsIgnoreCase("total")) {
            return new String[] {
                reportName,
                "Fz",
                "Fz Monitor",
                "Fz Total",
                "Fz Total Monitor"
            };
        }
        if (reportName.equalsIgnoreCase("Fz_Total")) {
            return new String[] {reportName, "Fz", "Fz Monitor", "Fz Total", "Fz Total Monitor"};
        }
        if (reportName.equalsIgnoreCase("Drag_Total")) {
            return new String[] {reportName, "Drag", "Drag Monitor"};
        }
        if (reportName.equalsIgnoreCase("Pitch_Moment")) {
            return new String[] {reportName, "Pitch_Moment Monitor", "Pitch Moment", "Pitch Moment Monitor"};
        }
        if (reportName.equalsIgnoreCase("Roll_Moment")) {
            return new String[] {reportName, "Roll_Moment Monitor", "Roll Moment", "Roll Moment Monitor"};
        }
        if (reportName.equalsIgnoreCase("Jet_Reaction_Z")) {
            return new String[] {
                reportName,
                "Jet_Reaction_Z Monitor",
                "Jet Reaction Z",
                "Jet Reaction Z Monitor"
            };
        }
        return new String[] {reportName, reportName + " Monitor"};
    }

    private void exportRequiredMonitorPlots(Simulation sim, File outDir) {
        exportMonitorPlot(sim, outDir,
            new String[] {"FZ", "FZ Plot", "FZ 绘图"},
            "FZ_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {"Fz Monitor", "Fz Monitor Plot", "Fz Monitor 绘图"},
            "Fz_Monitor_绘图_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {"Drag Monitor", "Drag Monitor Plot", "Drag Monitor 绘图"},
            "Drag_Monitor_绘图_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {
                "Pitch_Moment Monitor", "Pitch_Moment Monitor Plot", "Pitch_Moment Monitor 绘图"
            },
            "Pitch_Moment_Monitor_绘图_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {
                "Roll_Moment Monitor", "Roll_Moment Monitor Plot", "Roll_Moment Monitor 绘图"
            },
            "Roll_Moment_Monitor_绘图_image.csv");
        exportMonitorPlot(sim, outDir,
            new String[] {
                "Jet_Reaction_Z Monitor",
                "Jet_Reaction_Z Monitor Plot",
                "Jet_Reaction_Z Monitor 绘图"
            },
            "Jet_Reaction_Z_Monitor_绘图_image.csv");
    }

    private void exportMonitorPlot(
        Simulation sim,
        File outDir,
        String[] candidateNames,
        String fileName
    ) {
        StarPlot plot = findPlot(sim, candidateNames);
        if (plot == null) {
            sim.println(
                "WARNING: monitor plot not found for CSV '" + fileName
                + "'. Tried " + Arrays.toString(candidateNames)
                + ". Available plots: " + availablePlotNames(sim)
            );
            return;
        }
        File output = new File(outDir, fileName);
        try {
            plot.export(normalizeStarPath(output.getAbsolutePath()), ",");
            sim.println("[flow_control] exported monitor CSV -> " + output.getAbsolutePath());
        } catch (Exception e) {
            sim.println(
                "WARNING: failed to export monitor plot '" + plot.getPresentationName()
                + "' to '" + output.getAbsolutePath() + "': " + e.getMessage()
            );
        }
    }

    private StarPlot findPlot(Simulation sim, String[] candidateNames) {
        for (int idx = 0; idx < candidateNames.length; idx++) {
            try {
                StarPlot plot = sim.getPlotManager().getPlot(candidateNames[idx]);
                if (plot != null) return plot;
            } catch (Exception ignored) {}
        }
        // Prefer a case-sensitive English prefix before falling back to
        // case-insensitive matching.  Localized STAR installations can expose
        // the translated "Plot" suffix as mojibake in batch mode, while the
        // report/monitor prefix remains stable.
        for (Object obj : sim.getPlotManager().getObjects()) {
            if (!(obj instanceof StarPlot)) continue;
            StarPlot plot = (StarPlot) obj;
            try {
                String presentationName = plot.getPresentationName();
                for (int idx = 0; idx < candidateNames.length; idx++) {
                    String candidate = candidateNames[idx];
                    if (presentationName.equals(candidate)
                        || presentationName.startsWith(candidate + " ")) {
                        return plot;
                    }
                }
            } catch (Exception ignored) {}
        }
        for (Object obj : sim.getPlotManager().getObjects()) {
            if (!(obj instanceof StarPlot)) continue;
            StarPlot plot = (StarPlot) obj;
            try {
                String presentationName = plot.getPresentationName();
                for (int idx = 0; idx < candidateNames.length; idx++) {
                    String candidate = candidateNames[idx];
                    if (presentationName.equalsIgnoreCase(candidate)
                        || presentationName.toLowerCase(Locale.ROOT).startsWith(
                            candidate.toLowerCase(Locale.ROOT) + " "
                        )) {
                        return plot;
                    }
                }
            } catch (Exception ignored) {}
        }
        return null;
    }

    private String availablePlotNames(Simulation sim) {
        ArrayList<String> names = new ArrayList<String>();
        for (Object obj : sim.getPlotManager().getObjects()) {
            if (!(obj instanceof StarPlot)) continue;
            try {
                names.add(((StarPlot) obj).getPresentationName());
            } catch (Exception ignored) {}
        }
        return names.toString();
    }

    private String resolveOutputDir() {
        try {
            File macroDir = new File(getClass().getProtectionDomain().getCodeSource().getLocation().toURI());
            if (macroDir.isFile()) macroDir = macroDir.getParentFile();
            if (macroDir != null) return macroDir.getAbsolutePath();
        } catch (Exception ignored) {}
        return ".";
    }

    private String normalizeStarPath(String path) {
        return path.replace("\\", "/");
    }
}
