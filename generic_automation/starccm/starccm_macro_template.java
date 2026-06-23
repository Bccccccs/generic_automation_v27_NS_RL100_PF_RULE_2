import star.common.*;
import star.base.neo.*;
import star.base.report.*;
import star.meshing.*;
import star.trimmer.*;
import star.resurfacer.*;
import star.prismmesher.*;
import star.flow.*;
import star.segregatedflow.*;
import star.turbulence.*;
import star.kwturb.*;
import star.energy.*;
import star.vis.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;

public class AutoSetupMacro extends StarMacro {

    private static boolean dragReportDebugLogged = false;
    private static boolean missingPrimaryReportWarned = false;
    private static boolean missingPressureReportWarned = false;
    private static boolean totalPrimaryFallbackWarned = false;
    private static double solverProfilingCsvWriteTimeS = 0.0;
    private static int solverProfilingRowsWritten = 0;
    private static final String CFL_STATUS_NOT_EVALUATED_YET = "not_evaluated_yet";
    private static final String CFL_STATUS_NOT_AVAILABLE_FOR_CURRENT_SOLVER_MODEL =
        "not_available_for_current_solver_model";
    private static final String CFL_STATUS_NOT_AVAILABLE_MAX_REPORT_FIELD_NOT_BINDABLE =
        "not_available_max_report_field_function_not_bindable";
    private static final String CFL_STATUS_NOT_AVAILABLE_MEAN_REPORT_FIELD_NOT_BINDABLE =
        "not_available_mean_report_field_function_not_bindable";
    private static final String CFL_STATUS_AVAILABLE_AND_ENABLED = "available_and_enabled";
    private static final String CFL_STATUS_SETUP_FAILED = "setup_failed";

    private static String cflProfilingStatus = CFL_STATUS_NOT_EVALUATED_YET;
    private static String cflFieldFunctionName = null;
    private final LinkedHashMap<String, Double> solverProfilingPreviousCumulativeMetrics =
        new LinkedHashMap<String, Double>();

    static final String CASE_NAME = "{{CASE_NAME}}";
    static final String OUTPUT_DIR = "{{OUTPUT_DIR}}";
    static final String SIM_DIR    = "{{SIM_DIR}}";
    static final String REGION_NAME = "{{REGION_NAME}}";
    static final String INLET_BC = "{{INLET_BOUNDARY}}";
    static final String OUTLET_BC = "{{OUTLET_BOUNDARY}}";
    static final String TRAIN_BC = "{{WALL_BOUNDARY}}";
    static final String GROUND_BC = "{{GROUND_BOUNDARY}}";
    static final String SYMMETRY_BC = "{{SYMMETRY_BOUNDARY}}";

    static final double INLET_VELOCITY = {{INLET_VELOCITY}};
    static final double INLET_TEMP = {{INLET_TEMPERATURE}};
    static final double OUTLET_PRESSURE = {{OUTLET_PRESSURE}};
    static final double YAW_ANGLE_DEG = {{YAW_ANGLE}};
    static final double REF_AREA = {{REFERENCE_AREA}};
    static final double REF_LENGTH = {{REFERENCE_LENGTH}};

    static final double BASE_MESH_SIZE = {{BASE_MESH_SIZE}};
    static final double SURF_MESH_SIZE = {{SURFACE_MESH_SIZE}};
    static final double MIN_SURFACE_SIZE = {{MIN_SURFACE_SIZE}};
    static final double SURFACE_GROWTH_RATE = {{SURFACE_GROWTH_RATE}};
    static final int    PRISM_LAYERS = {{NUM_PRISM_LAYERS}};
    static final double PRISM_THICK = {{PRISM_LAYER_THICKNESS}};
    static final double PRISM_STRETCH = {{PRISM_LAYER_STRETCHING}};
    static final double PRISM_WALL_THICKNESS = {{PRISM_WALL_THICKNESS}};

    static final double TRAIN_TARGET_SIZE = {{TRAIN_TARGET_SIZE}};
    static final double TRAIN_MIN_SIZE = {{TRAIN_MIN_SIZE}};
    static final double TRAIN_PRISM_THICKNESS = {{TRAIN_PRISM_THICKNESS}};
    static final int    TRAIN_PRISM_LAYERS = {{TRAIN_PRISM_LAYERS}};

    static final double ZONE1_MESH_SIZE = {{ZONE1_MESH_SIZE}};
    static final double ZONE2_MESH_SIZE = {{ZONE2_MESH_SIZE}};

    static final double TI = {{INLET_TURBULENCE_INTENSITY}};
    static final double TL = {{INLET_TURBULENT_LENGTH_SCALE}};
    static final int    MAX_ITER = {{MAX_ITERATIONS}};
    static final double PRESSURE_RELAXATION_FACTOR = {{PRESSURE_RELAXATION_FACTOR}};
    static final double PRESSURE_RELAXATION_INITIAL_VALUE = {{PRESSURE_RELAXATION_INITIAL_VALUE}};
    static final int    PRESSURE_RELAXATION_START_ITERATION = {{PRESSURE_RELAXATION_START_ITERATION}};
    static final int    PRESSURE_RELAXATION_END_ITERATION = {{PRESSURE_RELAXATION_END_ITERATION}};
    static final double VELOCITY_RELAXATION_INITIAL_VALUE = {{VELOCITY_RELAXATION_INITIAL_VALUE}};
    static final int    VELOCITY_RELAXATION_START_ITERATION = {{VELOCITY_RELAXATION_START_ITERATION}};
    static final int    VELOCITY_RELAXATION_END_ITERATION = {{VELOCITY_RELAXATION_END_ITERATION}};
    static final int    PRESSURE_AMG_CYCLE = {{PRESSURE_AMG_CYCLE}};
    static final int    VELOCITY_AMG_CYCLE = {{VELOCITY_AMG_CYCLE}};
    static final int    AMG_CYCLE = {{AMG_CYCLE}};
    static final int    AMG_SOLVER = {{AMG_SOLVER}};
    static final int    PRESSURE_AMG_MAX_CYCLES = {{PRESSURE_AMG_MAX_CYCLES}};
    static final double PRESSURE_AMG_CONVERGE_TOL = {{PRESSURE_AMG_CONVERGE_TOL}};
    static final double PRESSURE_AMG_EPSILON = {{PRESSURE_AMG_EPSILON}};
    static final String PRESSURE_AMG_SMOOTHER = "{{PRESSURE_AMG_SMOOTHER}}";
    static final String PRESSURE_AMG_ACCELERATION = "{{PRESSURE_AMG_ACCELERATION}}";
    static final int    PRESSURE_AMG_PRE_SWEEPS = {{PRESSURE_AMG_PRE_SWEEPS}};
    static final int    PRESSURE_AMG_POST_SWEEPS = {{PRESSURE_AMG_POST_SWEEPS}};
    static final int    PRESSURE_AMG_MAX_LEVELS = {{PRESSURE_AMG_MAX_LEVELS}};
    static final double TIME_STEP = {{TIME_STEP}};
    static final int    NUM_TIME_STEPS = {{NUM_TIME_STEPS}};
    static final String SIM_TYPE = "{{SIMULATION_TYPE}}";
    static final String TURB_MODEL = "{{TURBULENCE_MODEL}}";
    static final boolean SOLVE_ENERGY = {{ENERGY_EQUATION}};

    static final double[] DOMAIN_CORNER1 = {{DOMAIN_CORNER1}};
    static final double[] DOMAIN_CORNER2 = {{DOMAIN_CORNER2}};
    static final double INITIAL_VELOCITY = {{INITIAL_VELOCITY}};

    static final int    MONITOR_START_ITER  = {{MONITOR_START_ITERATION}};
    static final int    MONITOR_UPDATE_FREQ = {{MONITOR_UPDATE_FREQUENCY}};
    static final String WALL_TREATMENT      = "{{WALL_TREATMENT}}";
    static final double CAD_SHARP_ANGLE     = {{CAD_SHARP_ANGLE}};

    static final int    LOG_FREQ                          = {{LOG_FREQUENCY}};
    static final String DRAG_REPORT_NAME                  = "{{DRAG_REPORT_NAME}}";
    static final String TOTAL_REPORT_NAME                 = "{{TOTAL_REPORT_NAME}}";
    static final String OUTLET_PRESSURE_REPORT_NAME       = "{{OUTLET_PRESSURE_REPORT_NAME}}";
    static final String TRAIN_SURFACE_PRESSURE_REPORT_NAME = "{{TRAIN_SURFACE_PRESSURE_REPORT_NAME}}";
    static final String MAX_RESIDUAL_COLUMN_NAME          = "max_residual";
    static final String SOLVER_PROFILING_CSV_NAME         = "solver_profiling.csv";
    static final String SOLVER_PROFILING_SUMMARY_NAME     = "solver_profiling_summary.json";
    static final String INLET_MASS_FLOW_REPORT_NAME       = "__profiling_inlet_mass_flow";
    static final String OUTLET_MASS_FLOW_REPORT_NAME      = "__profiling_outlet_mass_flow";
    static final String CFL_MAX_REPORT_NAME               = "__profiling_cfl_max";
    static final String CFL_MEAN_REPORT_NAME              = "__profiling_cfl_mean";

    static final String  DOMAIN_BLOCK_NAME   = "{{DOMAIN_BLOCK_NAME}}";
    static final String  ZONE1_NAME         = "{{ZONE1_NAME}}";
    static final String  ZONE2_NAME         = "{{ZONE2_NAME}}";
    static final String  TRAIN_CTRL_NAME    = "{{TRAIN_SURFACE_CONTROL_NAME}}";
    static final String  PRISM_MESHER_NAME  = "{{PRISM_MESHER_NAME}}";
    static final String  MAX_STEPS_CRITERION = "{{MAX_STEPS_CRITERION_NAME}}";
    static final boolean GROUND_SLIDING     = {{GROUND_SLIDING}};

    static final String RUN_MODE = "{{RUN_MODE}}";
    static final String RUN_ID = "{{RUN_ID}}";
    static final String MESH_CACHE_KEY = "{{MESH_CACHE_KEY}}";
    static final int PROTOCOL_VERSION = 2;
    static final String MESH_READY_SIM_PATH = "{{MESH_READY_SIM_PATH}}";
    static final String RESULT_SIM_PATH = "{{RESULT_SIM_PATH}}";
    static final String SOLVER_INIT_SIM_PATH = "{{SOLVER_INIT_SIM_PATH}}";
    static final String PARAM_UPDATE_FILE = "{{PARAM_UPDATE_FILE}}";
    static final String PARAM_ACK_FILE = "{{PARAM_ACK_FILE}}";
    static final String ACTION_ACK_LOG_FILE = "{{ACTION_ACK_LOG_FILE}}";
    static final int    CHECK_INTERVAL    = {{CHECK_INTERVAL}};
    static final int    CHECKPOINT_INTERVAL_ITER = {{CHECKPOINT_INTERVAL}};

    private String normalizedRunMode() {
        if (RUN_MODE == null || RUN_MODE.trim().isEmpty()) {
            return "full_run";
        }
        return RUN_MODE.trim().toLowerCase(Locale.ROOT);
    }

    private boolean isFullRunMode() {
        return "full_run".equals(normalizedRunMode());
    }

    private boolean isMeshOnlyMode() {
        return "mesh_only".equals(normalizedRunMode());
    }

    private boolean isSolveOnlyMode() {
        return "solve_only".equals(normalizedRunMode());
    }

    private boolean isResumeMode() {
        return "resume".equals(normalizedRunMode());
    }

    public void execute() {
        new java.io.File(OUTPUT_DIR + "/profiling").mkdirs();
        new java.io.File(SIM_DIR).mkdirs();
        Simulation sim = getActiveSimulation();
        double simulationWallTimeS = Double.NaN;
        double exportWallTimeS = Double.NaN;
        double saveStateWallTimeS = 0.0;
        String runMode = normalizedRunMode();

        if (isFullRunMode() || isMeshOnlyMode()) {
            setDomainSize(sim);
            setBoundaryConditions(sim);
            setInitialConditions(sim);
            setWallTreatment(sim);
            setMeshParameters(sim);
            try {
                sim.getMeshPipelineController().generateVolumeMesh();
            } catch (Exception e) {
                sim.println("ERROR: Volume mesh generation failed: " + e.getMessage());
                throw e;
            }
            saveStateWallTimeS += saveSimulationState(
                sim,
                MESH_READY_SIM_PATH,
                "mesh-ready"
            );
            if (isMeshOnlyMode()) {
                writeSolverProfilingRunSummary(
                    sim,
                    simulationWallTimeS,
                    exportWallTimeS,
                    saveStateWallTimeS,
                    false,
                    "mesh_ready"
                );
                return;
            }
        } else if (isSolveOnlyMode()) {
            setBoundaryConditions(sim);
            setWallTreatment(sim);
        } else if (isResumeMode()) {
            setBoundaryConditions(sim);
        } else {
            sim.println("WARNING: Unknown run mode '" + runMode + "', falling back to full_run.");
            setDomainSize(sim);
            setBoundaryConditions(sim);
            setInitialConditions(sim);
            setWallTreatment(sim);
            setMeshParameters(sim);
            sim.getMeshPipelineController().generateVolumeMesh();
            saveStateWallTimeS += saveSimulationState(
                sim,
                MESH_READY_SIM_PATH,
                "mesh-ready"
            );
        }
        removeLegacyReportArtifacts(sim);
        ensureDragReport(sim);
        ensureTotalReport(sim);
        ensurePressureReports(sim);
        ensureAuxiliaryProfilingReports(sim);
        ensurePrimaryReportMonitors(sim);
        setSolverSettings(sim);
        setMonitors(sim);
        if (!isResumeMode()) {
            saveStateWallTimeS += saveSimulationState(
                sim,
                SOLVER_INIT_SIM_PATH,
                "solver-init"
            );
        }
        writeSolverProfilingRunSummary(
            sim,
            simulationWallTimeS,
            exportWallTimeS,
            saveStateWallTimeS,
            false,
            "running"
        );
        try {
            simulationWallTimeS = runSimulation(sim);
        } catch (Exception e) {
            sim.println("ERROR: Simulation run failed: " + e.getMessage());
            long exportStartNs = System.nanoTime();
            exportResults(sim);
            exportWallTimeS = (System.nanoTime() - exportStartNs) / 1.0e9;
            writeSolverProfilingRunSummary(
                sim,
                simulationWallTimeS,
                exportWallTimeS,
                saveStateWallTimeS,
                true,
                "failed"
            );
            throw e;
        }
        long exportStartNs = System.nanoTime();
        exportResults(sim);
        exportWallTimeS = (System.nanoTime() - exportStartNs) / 1.0e9;
        saveStateWallTimeS += saveSimulationState(sim, RESULT_SIM_PATH, "result");
        writeSolverProfilingRunSummary(
            sim,
            simulationWallTimeS,
            exportWallTimeS,
            saveStateWallTimeS,
            false,
            "completed"
        );
    }

    private double saveSimulationState(Simulation sim, String path, String label) {
        if (path == null || path.trim().isEmpty()) {
            return 0.0;
        }
        try {
            long saveStateStartNs = System.nanoTime();
            sim.saveState(resolvePath(path));
            double elapsed = (System.nanoTime() - saveStateStartNs) / 1.0e9;
            sim.println("[RUN] Saved " + label + " state -> " + path);
            return elapsed;
        } catch (Exception e) {
            sim.println("WARNING: Failed to save " + label + " .sim file: " + e.getMessage());
            return 0.0;
        }
    }

    private void setBoundaryConditions(Simulation sim) {
        double yawRad = Math.toRadians(YAW_ANGLE_DEG);
        double vx = INLET_VELOCITY * Math.cos(yawRad);
        double vy = INLET_VELOCITY * Math.sin(yawRad);

        Region region = null;
        try {
            region = sim.getRegionManager().getRegion(REGION_NAME);
        } catch (Exception e) {
            sim.println("WARNING: Region '" + REGION_NAME + "' not found: " + e.getMessage()
                + ". Boundary conditions will not be applied.");
        }
        if (region == null) return;
        Units units_ms = ((Units) sim.getUnitsManager().getObject("m/s"));

        if (!INLET_BC.isEmpty()) {
            try {
                Boundary inlet = region.getBoundaryManager().getBoundary(INLET_BC);
                inlet.getConditions().get(InletVelocityOption.class)
                     .setSelected(InletVelocityOption.Type.COMPONENTS);
                VelocityProfile vp = inlet.getValues().get(VelocityProfile.class);
                vp.getMethod(ConstantVectorProfileMethod.class)
                  .getQuantity().setComponentsAndUnits(vx, vy, 0.0, units_ms);
                if (!TURB_MODEL.equals("laminar")) {
                    try {
                        inlet.getValues().get(TurbulenceIntensityProfile.class)
                             .getMethod(ConstantScalarProfileMethod.class)
                             .getQuantity().setValue(TI);
                    } catch (Exception e) {
                        sim.println("TurbulenceIntensityProfile N/A: " + e.getMessage());
                    }
                    try {
                        if (!tryApplyInletTurbulentLengthScale(sim, inlet, TL)) {
                            sim.println(
                                "Turbulent length scale profile unavailable on inlet '"
                                + INLET_BC + "', skipping. Available candidates: "
                                + describeObjectClasses(collectBoundaryValueCandidates(inlet), 12)
                            );
                        }
                    } catch (Exception e) {
                        sim.println("Turbulent length scale fallback failed: " + e.getMessage());
                    }
                }
                if (SOLVE_ENERGY) {
                    inlet.getValues().get(StaticTemperatureProfile.class)
                         .getMethod(ConstantScalarProfileMethod.class)
                         .getQuantity().setValue(INLET_TEMP);
                }
            } catch (Exception e) {
                sim.println("Inlet boundary '" + INLET_BC + "' not found: " + e.getMessage());
            }
        }

        if (!OUTLET_BC.isEmpty()) {
            try {
                Boundary outlet = region.getBoundaryManager().getBoundary(OUTLET_BC);
                outlet.getValues().get(StaticPressureProfile.class)
                      .getMethod(ConstantScalarProfileMethod.class)
                      .getQuantity().setValue(OUTLET_PRESSURE);
            } catch (Exception e) {
                sim.println("Outlet boundary '" + OUTLET_BC + "' not found: " + e.getMessage());
            }
        }

        if (!GROUND_BC.isEmpty() && GROUND_SLIDING) {
            try {
                Boundary ground = region.getBoundaryManager().getBoundary(GROUND_BC);
                ground.getConditions().get(WallSlidingOption.class)
                      .setSelected(WallSlidingOption.Type.VECTOR);
                ground.getValues().get(WallRelativeVelocityProfile.class)
                      .getMethod(ConstantVectorProfileMethod.class)
                      .getQuantity().setComponentsAndUnits(vx, 0.0, 0.0, units_ms);
            } catch (Exception e) {
                sim.println("Ground boundary '" + GROUND_BC + "' not found: " + e.getMessage());
            }
        }

        if (!SYMMETRY_BC.isEmpty()) {
            try {
                region.getBoundaryManager().getBoundary(SYMMETRY_BC);
            } catch (Exception e) {
                sim.println("Symmetry boundary '" + SYMMETRY_BC + "' not found.");
            }
        }
    }

    private boolean tryApplyInletTurbulentLengthScale(
            Simulation sim,
            Boundary inlet,
            double value) {
        if (inlet == null) {
            return false;
        }
        try {
            inlet.getValues().get(TurbulentLengthScaleProfile.class)
                 .getMethod(ConstantScalarProfileMethod.class)
                 .getQuantity().setValue(value);
            return true;
        } catch (Exception ignored) {}

        Units units_m = ((Units) sim.getUnitsManager().getObject("m"));
        ArrayList<Object> candidates = collectBoundaryValueCandidates(inlet);
        for (Object candidate : candidates) {
            if (!matchesClassFragments(
                    candidate,
                    "TurbulentLengthScaleProfile",
                    "LengthScaleProfile",
                    "TurbulentLengthScale",
                    "LengthScale")) {
                continue;
            }
            if (tryApplyScalarProfileCandidate(candidate, value, units_m)) {
                return true;
            }
        }
        return false;
    }

    private ArrayList<Object> collectBoundaryValueCandidates(Boundary boundary) {
        ArrayList<Object> candidates = new ArrayList<Object>();
        if (boundary == null) {
            return candidates;
        }
        Object valuesManager = boundary.getValues();
        addUniqueCandidate(candidates, valuesManager);
        addCandidateObjects(
            candidates,
            tryInvokeNoArg(valuesManager, "getObjects", "getValues", "values", "toArray")
        );

        if (valuesManager == null) {
            return candidates;
        }
        for (Method method : valuesManager.getClass().getMethods()) {
            if (method.getParameterCount() != 0) {
                continue;
            }
            String methodName = method.getName();
            if (!methodName.startsWith("get") || methodName.equals("getClass")) {
                continue;
            }
            if (
                !methodName.contains("Profile")
                && !methodName.contains("Value")
                && !methodName.contains("Condition")
                && !methodName.contains("Scalar")
            ) {
                continue;
            }
            Class<?> returnType = method.getReturnType();
            if (returnType.isPrimitive()) {
                continue;
            }
            try {
                Object raw = method.invoke(valuesManager);
                addCandidateObjects(candidates, raw);
            } catch (Exception ignored) {}
        }
        return candidates;
    }

    private void addCandidateObjects(ArrayList<Object> candidates, Object raw) {
        if (raw == null) {
            return;
        }
        ArrayList<Object> collected = collectChoiceObjects(raw);
        if (!collected.isEmpty()) {
            for (Object item : collected) {
                addUniqueCandidate(candidates, item);
            }
            return;
        }
        addUniqueCandidate(candidates, raw);
    }

    private void addUniqueCandidate(ArrayList<Object> candidates, Object candidate) {
        if (candidate == null || candidates == null || candidates.contains(candidate)) {
            return;
        }
        candidates.add(candidate);
    }

    private boolean matchesClassFragments(Object candidate, String... fragments) {
        if (candidate == null || fragments == null || fragments.length == 0) {
            return false;
        }
        String className = candidate.getClass().getName();
        String candidateLabel = describeNamedObject(candidate);
        String normalizedClass = normalizeLabel(className);
        String normalizedLabel = normalizeLabel(candidateLabel);
        for (String fragment : fragments) {
            String normalizedFragment = normalizeLabel(fragment);
            if (normalizedFragment.isEmpty()) {
                continue;
            }
            if (
                normalizedClass.contains(normalizedFragment)
                || normalizedLabel.contains(normalizedFragment)
            ) {
                return true;
            }
        }
        return false;
    }

    private boolean tryApplyScalarProfileCandidate(Object candidate, double value, Units units) {
        if (candidate == null) {
            return false;
        }
        Object methodObj = tryInvokeWithClassArg(candidate, "getMethod", ConstantScalarProfileMethod.class);
        if (methodObj != null && trySetScalarQuantity(methodObj, value, units)) {
            return true;
        }
        if (trySetScalarQuantity(candidate, value, units)) {
            return true;
        }
        Object quantity = tryInvokeNoArg(
            candidate,
            "getQuantity",
            "getScalar",
            "getValue",
            "getDefinition",
            "getAbsoluteSizeValue"
        );
        return quantity != null && quantity != candidate && trySetScalarQuantity(quantity, value, units);
    }

    private boolean trySetScalarQuantity(Object target, double value, Units units) {
        if (target == null) {
            return false;
        }
        if (invokeDoubleUnitsSetter(target, value, units, "setValueAndUnits")) {
            return true;
        }
        if (invokeDoubleSetter(target, value, "setValue", "setQuantityValue")) {
            return true;
        }
        Object nested = tryInvokeNoArg(
            target,
            "getQuantity",
            "getScalar",
            "getValue",
            "getDefinition",
            "getAbsoluteSizeValue",
            "getStretchingQuantity"
        );
        if (nested != null && nested != target) {
            if (invokeDoubleUnitsSetter(nested, value, units, "setValueAndUnits")) {
                return true;
            }
            if (invokeDoubleSetter(nested, value, "setValue", "setQuantityValue")) {
                return true;
            }
        }
        return false;
    }

    private PrismAutoMesher findPrismAutoMesher(
            AutoMeshOperation meshOp,
            String preferredName) {
        if (meshOp == null) {
            return null;
        }
        try {
            Object direct = meshOp.getMeshers().getObject(preferredName);
            if (direct instanceof PrismAutoMesher) {
                return (PrismAutoMesher) direct;
            }
        } catch (Exception ignored) {}

        ArrayList<PrismAutoMesher> prismMeshers = new ArrayList<PrismAutoMesher>();
        PrismAutoMesher best = null;
        int bestScore = 0;
        int secondScore = 0;
        try {
            for (Object mesherObj : meshOp.getMeshers().getObjects()) {
                if (!(mesherObj instanceof PrismAutoMesher)) {
                    continue;
                }
                PrismAutoMesher candidate = (PrismAutoMesher) mesherObj;
                prismMeshers.add(candidate);
                int score = scoreRequestedName(preferredName, candidate.getPresentationName());
                if (score > bestScore) {
                    secondScore = bestScore;
                    bestScore = score;
                    best = candidate;
                } else if (score > secondScore) {
                    secondScore = score;
                }
            }
        } catch (Exception ignored) {}

        if (best != null && (bestScore >= 95 || bestScore > secondScore)) {
            return best;
        }
        if (prismMeshers.size() == 1) {
            return prismMeshers.get(0);
        }
        return null;
    }

    private boolean tryApplyPrismLayerStretchingFallback(
            AutoMeshOperation meshOp,
            double value,
            Units units_none) {
        if (meshOp == null) {
            return false;
        }
        try {
            meshOp.getDefaultValues()
                  .get(PrismLayerStretching.class)
                  .getStretchingQuantity()
                  .setValueAndUnits(value, units_none);
            return true;
        } catch (Exception ignored) {}

        Object defaults = meshOp.getDefaultValues();
        if (trySetNumericValueOnGetter(
                defaults,
                value,
                "getPrismLayerStretching",
                "getPrismStretching",
                "getStretching",
                "getStretchingQuantity",
                "getStretchingRatio")) {
            return true;
        }
        Object nested = tryInvokeNoArg(
            defaults,
            "getPrismLayerStretching",
            "getPrismStretching",
            "getStretching",
            "getStretchingQuantity",
            "getStretchingRatio"
        );
        return nested != null && trySetScalarQuantity(nested, value, units_none);
    }

    private SurfaceCustomMeshControl findSurfaceCustomMeshControl(
            AutoMeshOperation meshOp,
            String controlName) {
        if (meshOp == null || controlName == null || controlName.trim().isEmpty()) {
            return null;
        }
        try {
            Object direct = meshOp.getCustomMeshControls().getObject(controlName);
            if (direct instanceof SurfaceCustomMeshControl) {
                return (SurfaceCustomMeshControl) direct;
            }
        } catch (Exception ignored) {}

        SurfaceCustomMeshControl best = null;
        int bestScore = 0;
        int secondScore = 0;
        for (Object ctrl : meshOp.getCustomMeshControls().getObjects()) {
            if (!(ctrl instanceof SurfaceCustomMeshControl)) continue;
            SurfaceCustomMeshControl candidate = (SurfaceCustomMeshControl) ctrl;
            int score = scoreRequestedName(controlName, candidate.getPresentationName());
            if (score > bestScore) {
                secondScore = bestScore;
                bestScore = score;
                best = candidate;
            } else if (score > secondScore) {
                secondScore = score;
            }
        }
        if (best != null && (bestScore >= 95 || bestScore > secondScore)) {
            return best;
        }
        return null;
    }

    private VolumeCustomMeshControl findVolumeCustomMeshControl(
            AutoMeshOperation meshOp,
            String controlName) {
        if (meshOp == null || controlName == null || controlName.trim().isEmpty()) {
            return null;
        }
        try {
            Object direct = meshOp.getCustomMeshControls().getObject(controlName);
            if (direct instanceof VolumeCustomMeshControl) {
                return (VolumeCustomMeshControl) direct;
            }
        } catch (Exception ignored) {}

        VolumeCustomMeshControl best = null;
        int bestScore = 0;
        int secondScore = 0;
        for (Object ctrl : meshOp.getCustomMeshControls().getObjects()) {
            if (!(ctrl instanceof VolumeCustomMeshControl)) continue;
            VolumeCustomMeshControl candidate = (VolumeCustomMeshControl) ctrl;
            int score = scoreRequestedName(controlName, candidate.getPresentationName());
            if (score > bestScore) {
                secondScore = bestScore;
                bestScore = score;
                best = candidate;
            } else if (score > secondScore) {
                secondScore = score;
            }
        }
        if (best != null && (bestScore >= 95 || bestScore > secondScore)) {
            return best;
        }
        return null;
    }

    private void setMeshParameters(Simulation sim) {
        Units units_none = ((Units) sim.getUnitsManager().getObject(""));
        Units units_m    = ((Units) sim.getUnitsManager().getObject("m"));
        for (Object obj : sim.get(MeshOperationManager.class).getObjects()) {
            if (!(obj instanceof AutoMeshOperation)) continue;
            AutoMeshOperation meshOp = (AutoMeshOperation) obj;

            meshOp.getDefaultValues().get(BaseSize.class)
                  .setValueAndUnits(BASE_MESH_SIZE, units_m);

            PartsTargetSurfaceSize pts =
                meshOp.getDefaultValues().get(PartsTargetSurfaceSize.class);
            pts.getRelativeOrAbsoluteOption()
               .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
            ((ScalarPhysicalQuantity) pts.getAbsoluteSizeValue())
               .setValueAndUnits(SURF_MESH_SIZE, units_m);

            PartsMinimumSurfaceSize pms =
                meshOp.getDefaultValues().get(PartsMinimumSurfaceSize.class);
            pms.getRelativeOrAbsoluteOption()
               .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
            ((ScalarPhysicalQuantity) pms.getAbsoluteSizeValue())
               .setValueAndUnits(MIN_SURFACE_SIZE, units_m);

            try {
                SurfaceGrowthRate sgr =
                    meshOp.getDefaultValues().get(SurfaceGrowthRate.class);
                sgr.setGrowthRateOption(
                    SurfaceGrowthRate.GrowthRateOption.USER_SPECIFIED);
                sgr.getGrowthRateScalar().setValueAndUnits(SURFACE_GROWTH_RATE, units_none);
            } catch (Exception e) {
                sim.println("SurfaceGrowthRate not available: " + e.getMessage());
            }

            try {
                meshOp.getDefaultValues()
                      .get(NumPrismLayers.class)
                      .getNumLayersValue().getQuantity().setValue((double) PRISM_LAYERS);
            } catch (Exception e) {
                sim.println("NumPrismLayers not available, skipping: " + e.getMessage());
            }

            boolean prismStretchConfigured = false;
            PrismAutoMesher pam = findPrismAutoMesher(meshOp, PRISM_MESHER_NAME);
            if (pam != null) {
                try {
                    pam.getPrismStretchingOption()
                       .setSelected(PrismStretchingOption.Type.WALL_THICKNESS);
                    prismStretchConfigured = true;
                } catch (Exception e) {
                    sim.println(
                        "PrismAutoMesher located but stretching option unavailable, trying fallback: "
                        + e.getMessage()
                    );
                }
            } else {
                sim.println(
                    "PrismAutoMesher alias lookup failed for '" + PRISM_MESHER_NAME
                    + "', trying fallback stretching. Available meshers: "
                    + describeMeshers(meshOp, 16)
                );
            }
            if (!prismStretchConfigured) {
                if (!tryApplyPrismLayerStretchingFallback(meshOp, PRISM_STRETCH, units_none)) {
                    sim.println("Prism stretching fallback unavailable, keeping template default.");
                }
            }

            try {
                meshOp.getDefaultValues()
                      .get(PrismWallThickness.class)
                      .setValueAndUnits(PRISM_WALL_THICKNESS, units_m);
            } catch (Exception e) {
                sim.println("PrismWallThickness not available: " + e.getMessage());
            }

            break;
        }

        setupTrainSurfaceMesh(sim);
        setupNamedSurfaceControls(sim);
        setupVolumeControls(sim);
    }

    private void setupTrainSurfaceMesh(Simulation sim) {
        if (TRAIN_TARGET_SIZE <= 0.0) return;
        Units units_m = ((Units) sim.getUnitsManager().getObject("m"));
        String[] bcParts = TRAIN_BC.split("\\\\.");
        String trainName = bcParts.length >= 3 ? bcParts[bcParts.length - 2]
                         : bcParts.length == 2 ? bcParts[0] : TRAIN_BC;

        for (Object meshObj : sim.get(MeshOperationManager.class).getObjects()) {
            if (!(meshObj instanceof AutoMeshOperation)) continue;
            AutoMeshOperation meshOp = (AutoMeshOperation) meshObj;

            SurfaceCustomMeshControl trainCtrl = null;
            if (!TRAIN_CTRL_NAME.isEmpty()) {
                trainCtrl = findSurfaceCustomMeshControl(meshOp, TRAIN_CTRL_NAME);
            }
            if (trainCtrl == null) {
                for (Object ctrl : meshOp.getCustomMeshControls().getObjects()) {
                    if (!(ctrl instanceof SurfaceCustomMeshControl)) continue;
                    SurfaceCustomMeshControl sc = (SurfaceCustomMeshControl) ctrl;
                    for (Object geom : sc.getGeometryObjects().getObjects()) {
                        if (geom instanceof PartSurface) {
                            String n = ((PartSurface) geom).getPresentationName();
                            if (n.contains(trainName)) { trainCtrl = sc; break; }
                        }
                    }
                    if (trainCtrl != null) break;
                }
            }
            if (trainCtrl == null) {
                sim.println("Train surface mesh control not found, skipping.");
                break;
            }

            trainCtrl.getCustomConditions()
                     .get(PartsTargetSurfaceSizeOption.class)
                     .setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
            PartsTargetSurfaceSize ptss =
                trainCtrl.getCustomValues().get(PartsTargetSurfaceSize.class);
            ptss.getRelativeOrAbsoluteOption()
                .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
            ((ScalarPhysicalQuantity) ptss.getAbsoluteSizeValue())
                .setValueAndUnits(TRAIN_TARGET_SIZE, units_m);

            trainCtrl.getCustomConditions()
                     .get(PartsMinimumSurfaceSizeOption.class)
                     .setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
            PartsMinimumSurfaceSize pmss =
                trainCtrl.getCustomValues().get(PartsMinimumSurfaceSize.class);
            pmss.getRelativeOrAbsoluteOption()
                .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
            ((ScalarPhysicalQuantity) pmss.getAbsoluteSizeValue())
                .setValueAndUnits(TRAIN_MIN_SIZE, units_m);

            if (TRAIN_PRISM_THICKNESS > 0.0) {
                try {
                    PartsCustomizePrismMesh pcpm =
                        trainCtrl.getCustomConditions()
                                 .get(PartsCustomizePrismMesh.class);
                    pcpm.getCustomPrismOptions()
                        .setSelected(PartsCustomPrismsOption.Type.CUSTOMIZE);
                    PartsCustomizePrismMeshControls pcp =
                        pcpm.getCustomPrismControls();
                    pcp.setCustomizeNumLayers(true);
                    pcp.setCustomizeTotalThickness(true);
                    pcp.setCustomizeStretching(true);

                    int nLayers = (TRAIN_PRISM_LAYERS > 0)
                        ? TRAIN_PRISM_LAYERS : PRISM_LAYERS;
                    trainCtrl.getCustomValues()
                             .get(CustomPrismValuesManager.class)
                             .get(NumPrismLayers.class)
                             .getNumLayersValue().getQuantity()
                             .setValue((double) nLayers);

                    PrismThickness pt =
                        trainCtrl.getCustomValues()
                                 .get(CustomPrismValuesManager.class)
                                 .get(PrismThickness.class);
                    pt.getRelativeOrAbsoluteOption()
                      .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
                    ((ScalarPhysicalQuantity) pt.getAbsoluteSizeValue())
                        .setValueAndUnits(TRAIN_PRISM_THICKNESS, units_m);
                } catch (Exception e) {
                    sim.println("Custom prism for train failed: " + e.getMessage());
                }
            }
            break;
        }
    }

    private void setupVolumeControls(Simulation sim) {
        Units units_m = ((Units) sim.getUnitsManager().getObject("m"));
        for (Object meshObj : sim.get(MeshOperationManager.class).getObjects()) {
            if (!(meshObj instanceof AutoMeshOperation)) continue;
            AutoMeshOperation meshOp = (AutoMeshOperation) meshObj;
            applyNamedVolumeControlSize(sim, meshOp, ZONE1_NAME, ZONE1_MESH_SIZE, units_m);
            applyNamedVolumeControlSize(sim, meshOp, ZONE2_NAME, ZONE2_MESH_SIZE, units_m);
{{EXTRA_VOLUME_CONTROL_UPDATES}}
            break;
        }
    }

    private void applyNamedVolumeControlSize(
            Simulation sim,
            AutoMeshOperation meshOp,
            String controlName,
            double size,
            Units units_m) {
        if (controlName == null || controlName.isEmpty() || size <= 0.0) {
            return;
        }

        try {
            VolumeCustomMeshControl vc = findVolumeCustomMeshControl(meshOp, controlName);

            if (vc == null) {
                sim.println(
                    "Volume mesh control '" + controlName
                    + "' not found after alias lookup, skipping. Available controls: "
                    + describeVolumeMeshControls(meshOp, 16)
                );
                return;
            }

            VolumeControlSize vcs =
                vc.getCustomValues().get(VolumeControlSize.class);
            vcs.getRelativeOrAbsoluteOption()
               .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
            ((ScalarPhysicalQuantity) vcs.getAbsoluteSizeValue())
                .setValueAndUnits(size, units_m);
        } catch (Exception e) {
            sim.println("Failed to update volume mesh control '" + controlName
                        + "': " + e.getMessage());
        }
    }

    private void setupNamedSurfaceControls(Simulation sim) {
        for (Object meshObj : sim.get(MeshOperationManager.class).getObjects()) {
            if (!(meshObj instanceof AutoMeshOperation)) continue;
            AutoMeshOperation meshOp = (AutoMeshOperation) meshObj;
{{EXTRA_SURFACE_CONTROL_UPDATES}}
            break;
        }
    }

    private void applyNamedSurfaceControlSettings(
            Simulation sim,
            AutoMeshOperation meshOp,
            String controlName,
            double targetSize,
            double minSize,
            int prismLayers,
            double prismThickness,
            double prismWallThickness) {
        if (controlName == null || controlName.isEmpty()) {
            return;
        }
        if (
            targetSize <= 0.0
            && minSize <= 0.0
            && prismLayers <= 0
            && prismThickness <= 0.0
            && prismWallThickness <= 0.0
        ) {
            return;
        }

        Units units_m = ((Units) sim.getUnitsManager().getObject("m"));
        SurfaceCustomMeshControl ctrl = findSurfaceCustomMeshControl(meshOp, controlName);

        if (ctrl == null) {
            sim.println(
                "Surface mesh control '" + controlName
                + "' not found after alias lookup, skipping. Available controls: "
                + describeSurfaceMeshControls(meshOp, 16)
            );
            return;
        }

        try {
            if (targetSize > 0.0) {
                ctrl.getCustomConditions()
                    .get(PartsTargetSurfaceSizeOption.class)
                    .setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
                PartsTargetSurfaceSize ptss =
                    ctrl.getCustomValues().get(PartsTargetSurfaceSize.class);
                ptss.getRelativeOrAbsoluteOption()
                    .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
                ((ScalarPhysicalQuantity) ptss.getAbsoluteSizeValue())
                    .setValueAndUnits(targetSize, units_m);
            }

            if (minSize > 0.0) {
                ctrl.getCustomConditions()
                    .get(PartsMinimumSurfaceSizeOption.class)
                    .setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
                PartsMinimumSurfaceSize pmss =
                    ctrl.getCustomValues().get(PartsMinimumSurfaceSize.class);
                pmss.getRelativeOrAbsoluteOption()
                    .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
                ((ScalarPhysicalQuantity) pmss.getAbsoluteSizeValue())
                    .setValueAndUnits(minSize, units_m);
            }

            if (prismLayers > 0 || prismThickness > 0.0 || prismWallThickness > 0.0) {
                PartsCustomizePrismMesh pcpm =
                    ctrl.getCustomConditions().get(PartsCustomizePrismMesh.class);
                pcpm.getCustomPrismOptions()
                    .setSelected(PartsCustomPrismsOption.Type.CUSTOMIZE);
                PartsCustomizePrismMeshControls pcp =
                    pcpm.getCustomPrismControls();
                if (prismLayers > 0) {
                    pcp.setCustomizeNumLayers(true);
                    ctrl.getCustomValues()
                        .get(CustomPrismValuesManager.class)
                        .get(NumPrismLayers.class)
                        .getNumLayersValue().getQuantity()
                        .setValue((double) prismLayers);
                }
                if (prismThickness > 0.0) {
                    pcp.setCustomizeTotalThickness(true);
                    PrismThickness pt =
                        ctrl.getCustomValues()
                            .get(CustomPrismValuesManager.class)
                            .get(PrismThickness.class);
                    pt.getRelativeOrAbsoluteOption()
                        .setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
                    ((ScalarPhysicalQuantity) pt.getAbsoluteSizeValue())
                        .setValueAndUnits(prismThickness, units_m);
                }
                if (prismWallThickness > 0.0) {
                    pcp.setOverrideBoundaryDefault(true);
                    ctrl.getCustomValues()
                        .get(CustomPrismValuesManager.class)
                        .get(PrismWallThickness.class)
                        .setValueAndUnits(prismWallThickness, units_m);
                }
            }
        } catch (Exception e) {
            sim.println("Failed to update surface mesh control '" + controlName
                        + "': " + e.getMessage());
        }
    }

    private void setSolverSettings(Simulation sim) {
        if (SIM_TYPE.equals("transient")) {
            ImplicitUnsteadySolver unsteady =
                (ImplicitUnsteadySolver) sim.getSolverManager()
                    .getSolver(ImplicitUnsteadySolver.class);
            unsteady.getTimeStep().setValue(TIME_STEP);
        } else {
            StepStoppingCriterion sc =
                (StepStoppingCriterion) sim.getSolverStoppingCriterionManager()
                    .getSolverStoppingCriterion(MAX_STEPS_CRITERION);
            if (sc != null) {
                sc.setMaximumNumberSteps(MAX_ITER);
            } else {
                sim.println("WARNING: StepStoppingCriterion '" + MAX_STEPS_CRITERION + "' not found, skipping.");
            }
        }
        applyPressureRelaxationFactor(sim, PRESSURE_RELAXATION_FACTOR);
        applyPressureRelaxationRampSettings(
            sim,
            PRESSURE_RELAXATION_INITIAL_VALUE,
            PRESSURE_RELAXATION_START_ITERATION,
            PRESSURE_RELAXATION_END_ITERATION
        );
        applyVelocityRelaxationRampSettings(
            sim,
            VELOCITY_RELAXATION_INITIAL_VALUE,
            VELOCITY_RELAXATION_START_ITERATION,
            VELOCITY_RELAXATION_END_ITERATION
        );
        applyPressureAmgCycleSetting(sim, PRESSURE_AMG_CYCLE);
        applyPressureAmgNumericSettings(
            sim,
            PRESSURE_AMG_MAX_CYCLES,
            PRESSURE_AMG_CONVERGE_TOL,
            PRESSURE_AMG_EPSILON,
            PRESSURE_AMG_SMOOTHER,
            PRESSURE_AMG_ACCELERATION,
            PRESSURE_AMG_PRE_SWEEPS,
            PRESSURE_AMG_POST_SWEEPS,
            PRESSURE_AMG_MAX_LEVELS
        );
        applyVelocityAmgCycleSetting(sim, VELOCITY_AMG_CYCLE);
        enableSolverProfilingVerbosity(sim);
    }

    private void applyPressureRelaxationFactor(Simulation sim, double value) {
        if (SIM_TYPE.equals("transient")) {
            sim.println("pressure_relaxation_factor skipped for transient case.");
            return;
        }

        try {
            Object segregated = getNamedSolver(sim, "star.flow.SegregatedFlowSolver");
            if (segregated == null) {
                segregated = findSolverByClassFragment(sim, "SegregatedFlowSolver");
            }
            if (segregated == null) {
                Object coupled = getNamedSolver(sim, "star.flow.CoupledFlowSolver");
                if (coupled == null) {
                    coupled = findSolverByClassFragment(sim, "CoupledFlowSolver");
                }
                if (coupled != null) {
                    sim.println(
                        "WARNING: CoupledFlowSolver detected; pressure_relaxation_factor is not applied "
                        + "because this case does not expose a segregated pressure solver. Coupled solver class: "
                        + coupled.getClass().getName()
                    );
                } else {
                    sim.println(
                        "WARNING: SegregatedFlowSolver not found, unable to set pressure_relaxation_factor. "
                        + "Available solvers: " + describeAvailableSolvers(sim, 24)
                    );
                }
                return;
            }

            Object pressureSolver = tryInvokeNoArg(
                segregated,
                "getPressureSolver",
                "getPressureLinearSolver",
                "getPressureCorrectionSolver"
            );
            boolean changed = invokeDoubleSetter(
                pressureSolver != null ? pressureSolver : segregated,
                value,
                "setUrf",
                "setURF",
                "setUnderRelaxationFactor"
            );
            if (!changed && pressureSolver != null) {
                changed = invokeDoubleSetter(
                    segregated,
                    value,
                    "setUrf",
                    "setURF",
                    "setUnderRelaxationFactor"
                );
            }
            if (changed) {
                sim.println("pressure_relaxation_factor -> " + value);
            } else {
                sim.println("WARNING: pressure relaxation setter not available, keeping template default.");
            }
        } catch (Exception e) {
            sim.println("pressure_relaxation_factor update failed: " + e.getMessage());
        }
    }

    private void applyPressureRelaxationRampSettings(
            Simulation sim,
            double initialValue,
            int startIteration,
            int endIteration) {
        applyPressureRelaxationRampInitialValue(sim, initialValue);
        applyPressureRelaxationRampStartIteration(sim, startIteration);
        applyPressureRelaxationRampEndIteration(sim, endIteration);
    }

    private void applyVelocityRelaxationRampSettings(
            Simulation sim,
            double initialValue,
            int startIteration,
            int endIteration) {
        applyVelocityRelaxationRampInitialValue(sim, initialValue);
        applyVelocityRelaxationRampStartIteration(sim, startIteration);
        applyVelocityRelaxationRampEndIteration(sim, endIteration);
    }

    private void applyPressureRelaxationRampInitialValue(Simulation sim, double value) {
        if (tryApplyRecordedPressureRelaxationInitialValue(sim, value)) {
            sim.println("pressure_relaxation_initial_value -> " + value);
            return;
        }
        applyNamedRelaxationRampParameter(
            sim,
            "pressure_relaxation_initial_value",
            value,
            "initial",
            collectPressureRelaxationRampCandidates(sim),
            "Pressure relaxation ramp initial value not found via macro reflection; keeping template default."
        );
    }

    private void applyPressureRelaxationRampStartIteration(Simulation sim, int value) {
        if (tryApplyRecordedPressureRelaxationStartIteration(sim, value)) {
            sim.println("pressure_relaxation_start_iteration -> " + value);
            return;
        }
        applyNamedRelaxationRampParameter(
            sim,
            "pressure_relaxation_start_iteration",
            value,
            "start",
            collectPressureRelaxationRampCandidates(sim),
            "Pressure relaxation ramp start iteration not found via macro reflection; keeping template default."
        );
    }

    private void applyPressureRelaxationRampEndIteration(Simulation sim, int value) {
        if (tryApplyRecordedPressureRelaxationEndIteration(sim, value)) {
            sim.println("pressure_relaxation_end_iteration -> " + value);
            return;
        }
        applyNamedRelaxationRampParameter(
            sim,
            "pressure_relaxation_end_iteration",
            value,
            "end",
            collectPressureRelaxationRampCandidates(sim),
            "Pressure relaxation ramp end iteration not found via macro reflection; keeping template default."
        );
    }

    private void applyVelocityRelaxationRampInitialValue(Simulation sim, double value) {
        if (tryApplyRecordedVelocityRelaxationInitialValue(sim, value)) {
            sim.println("velocity_relaxation_initial_value -> " + value);
            return;
        }
        applyNamedRelaxationRampParameter(
            sim,
            "velocity_relaxation_initial_value",
            value,
            "initial",
            collectVelocityRelaxationRampCandidates(sim),
            "Velocity relaxation ramp initial value not found via macro reflection; keeping template default."
        );
    }

    private void applyVelocityRelaxationRampStartIteration(Simulation sim, int value) {
        if (tryApplyRecordedVelocityRelaxationStartIteration(sim, value)) {
            sim.println("velocity_relaxation_start_iteration -> " + value);
            return;
        }
        applyNamedRelaxationRampParameter(
            sim,
            "velocity_relaxation_start_iteration",
            value,
            "start",
            collectVelocityRelaxationRampCandidates(sim),
            "Velocity relaxation ramp start iteration not found via macro reflection; keeping template default."
        );
    }

    private void applyVelocityRelaxationRampEndIteration(Simulation sim, int value) {
        if (tryApplyRecordedVelocityRelaxationEndIteration(sim, value)) {
            sim.println("velocity_relaxation_end_iteration -> " + value);
            return;
        }
        applyNamedRelaxationRampParameter(
            sim,
            "velocity_relaxation_end_iteration",
            value,
            "end",
            collectVelocityRelaxationRampCandidates(sim),
            "Velocity relaxation ramp end iteration not found via macro reflection; keeping template default."
        );
    }

    private boolean tryApplyRecordedPressureRelaxationInitialValue(Simulation sim, double value) {
        try {
            PressureSolver pressureSolver = getTypedPressureSolver(sim);
            if (pressureSolver == null) {
                return false;
            }
            LinearRampCalculator linearRamp = ensureTypedLinearRamp(pressureSolver);
            if (linearRamp == null) {
                return false;
            }
            Units units = ((Units) sim.getUnitsManager().getObject(""));
            linearRamp.getInitialRampValueQuantity().setValueAndUnits(value, units);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private boolean tryApplyRecordedPressureRelaxationEndIteration(Simulation sim, int value) {
        try {
            PressureSolver pressureSolver = getTypedPressureSolver(sim);
            if (pressureSolver == null) {
                return false;
            }
            LinearRampCalculator linearRamp = ensureTypedLinearRamp(pressureSolver);
            if (linearRamp == null) {
                return false;
            }
            IntegerValue endIterationValue = linearRamp.getEndIterationValue();
            endIterationValue.getQuantity().setValue((double) value);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private boolean tryApplyRecordedPressureRelaxationStartIteration(Simulation sim, int value) {
        try {
            PressureSolver pressureSolver = getTypedPressureSolver(sim);
            if (pressureSolver == null) {
                return false;
            }
            LinearRampCalculator linearRamp = ensureTypedLinearRamp(pressureSolver);
            if (linearRamp == null) {
                return false;
            }
            IntegerValue startIterationValue = linearRamp.getStartIterationValue();
            startIterationValue.getQuantity().setValue((double) value);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private boolean tryApplyRecordedVelocityRelaxationInitialValue(Simulation sim, double value) {
        try {
            VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
            if (velocitySolver == null) {
                return false;
            }
            LinearRampCalculator linearRamp = ensureTypedLinearRamp(velocitySolver);
            if (linearRamp == null) {
                return false;
            }
            Units units = ((Units) sim.getUnitsManager().getObject(""));
            linearRamp.getInitialRampValueQuantity().setValueAndUnits(value, units);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private boolean tryApplyRecordedVelocityRelaxationEndIteration(Simulation sim, int value) {
        try {
            VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
            if (velocitySolver == null) {
                return false;
            }
            LinearRampCalculator linearRamp = ensureTypedLinearRamp(velocitySolver);
            if (linearRamp == null) {
                return false;
            }
            IntegerValue endIterationValue = linearRamp.getEndIterationValue();
            endIterationValue.getQuantity().setValue((double) value);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private boolean tryApplyRecordedVelocityRelaxationStartIteration(Simulation sim, int value) {
        try {
            VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
            if (velocitySolver == null) {
                return false;
            }
            LinearRampCalculator linearRamp = ensureTypedLinearRamp(velocitySolver);
            if (linearRamp == null) {
                return false;
            }
            IntegerValue startIterationValue = linearRamp.getStartIterationValue();
            startIterationValue.getQuantity().setValue((double) value);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private PressureSolver getTypedPressureSolver(Simulation sim) {
        try {
            SegregatedFlowSolver segregatedFlowSolver =
                (SegregatedFlowSolver) sim.getSolverManager().getSolver(SegregatedFlowSolver.class);
            if (segregatedFlowSolver == null) {
                return null;
            }
            return segregatedFlowSolver.getPressureSolver();
        } catch (Exception ignored) {}
        return null;
    }

    private VelocitySolver getTypedVelocitySolver(Simulation sim) {
        try {
            SegregatedFlowSolver segregatedFlowSolver =
                (SegregatedFlowSolver) sim.getSolverManager().getSolver(SegregatedFlowSolver.class);
            if (segregatedFlowSolver == null) {
                return null;
            }
            return segregatedFlowSolver.getVelocitySolver();
        } catch (Exception ignored) {}
        return null;
    }

    private AMGLinearSolver getTypedPressureAmgLinearSolver(Simulation sim) {
        try {
            PressureSolver pressureSolver = getTypedPressureSolver(sim);
            if (pressureSolver == null) {
                return null;
            }
            return pressureSolver.getAMGLinearSolver();
        } catch (Exception ignored) {}
        return null;
    }

    private AMGLinearSolver getTypedVelocityAmgLinearSolver(Simulation sim) {
        try {
            VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
            if (velocitySolver == null) {
                return null;
            }
            return velocitySolver.getAMGLinearSolver();
        } catch (Exception ignored) {}
        return null;
    }

    private void enableSolverProfilingVerbosity(Simulation sim) {
        ArrayList<Object> targets = new ArrayList<Object>();
        addUniqueObject(targets, getTypedPressureAmgLinearSolver(sim));
        addUniqueObject(targets, getTypedVelocityAmgLinearSolver(sim));
        addUniqueObject(
            targets,
            findSolverCandidateByTokenGroups(sim, new String[] {"PRESSURE", "CONTINUITY"}, new String[] {"AMG", "MULTIGRID"})
        );
        addUniqueObject(
            targets,
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"VELOCITY", "XMOMENTUM", "YMOMENTUM", "ZMOMENTUM"},
                new String[] {"AMG", "MULTIGRID"}
            )
        );
        addUniqueObject(
            targets,
            findSolverCandidateByTokenGroups(sim, new String[] {"TKE"}, new String[] {"AMG", "MULTIGRID"})
        );
        addUniqueObject(
            targets,
            findSolverCandidateByTokenGroups(sim, new String[] {"SDR"}, new String[] {"AMG", "MULTIGRID"})
        );
        addUniqueObject(
            targets,
            findSolverCandidateByTokenGroups(sim, new String[] {"ENERGY"}, new String[] {"AMG", "MULTIGRID"})
        );

        int configuredCount = 0;
        for (Object target : targets) {
            if (tryConfigureVerbosityLow(target)) {
                configuredCount += 1;
            }
        }

        if (configuredCount > 0) {
            sim.println("solver_profiling_verbosity -> low (" + configuredCount + " targets)");
        } else {
            sim.println(
                "WARNING: AMG solver verbosity option not found via macro reflection; "
                + "inner solver iteration logging may remain unavailable."
            );
        }
    }

    private void addUniqueObject(ArrayList<Object> targets, Object candidate) {
        if (candidate == null || targets.contains(candidate)) {
            return;
        }
        targets.add(candidate);
    }

    private boolean tryConfigureVerbosityLow(Object target) {
        if (target == null) {
            return false;
        }
        if (trySelectOptionOnGetter(
                target,
                new String[] {"LOW"},
                "getVerbosityOption",
                "getVerbosityTypeOption",
                "getSolverVerbosityOption",
                "getOutputVerbosityOption",
                "getResidualVerbosityOption",
                "getConvergenceVerbosityOption",
                "getLoggingVerbosityOption",
                "getVerbosity")) {
            return true;
        }
        Object option = tryInvokeNoArg(
            target,
            "getVerbosityOption",
            "getVerbosityTypeOption",
            "getSolverVerbosityOption",
            "getOutputVerbosityOption",
            "getResidualVerbosityOption",
            "getConvergenceVerbosityOption",
            "getLoggingVerbosityOption",
            "getVerbosity"
        );
        if (option != null && option != target) {
            if (tryConfigureOption(option, "LOW")) {
                return true;
            }
            if (trySetStringLikeOption(option, new ArrayList<String>(Arrays.asList("LOW")))) {
                return true;
            }
        }
        return false;
    }

    private LinearRampCalculator ensureTypedLinearRamp(PressureSolver solver) {
        if (solver == null) {
            return null;
        }
        try {
            solver.getRampCalculatorManager()
                .getRampCalculatorOption()
                .setSelected(RampCalculatorOption.Type.LINEAR_RAMP);
            return ((LinearRampCalculator) solver.getRampCalculatorManager().getCalculator());
        } catch (Exception ignored) {}
        return null;
    }

    private LinearRampCalculator ensureTypedLinearRamp(VelocitySolver solver) {
        if (solver == null) {
            return null;
        }
        try {
            solver.getRampCalculatorManager()
                .getRampCalculatorOption()
                .setSelected(RampCalculatorOption.Type.LINEAR_RAMP);
            return ((LinearRampCalculator) solver.getRampCalculatorManager().getCalculator());
        } catch (Exception ignored) {}
        return null;
    }

    private boolean applyNamedRelaxationRampParameter(
            Simulation sim,
            String logKey,
            double value,
            String propertyKind,
            ArrayList<Object> candidates,
            String warningMessage) {
        boolean changed = false;
        if (logKey != null) {
            if (logKey.startsWith("pressure_")) {
                changed = tryApplyDirectRelaxationRampParameter(
                    sim,
                    true,
                    propertyKind,
                    value
                );
            } else if (logKey.startsWith("velocity_")) {
                changed = tryApplyDirectRelaxationRampParameter(
                    sim,
                    false,
                    propertyKind,
                    value
                );
            }
        }
        for (Object candidate : candidates) {
            if (changed) break;
            if (candidate == null) continue;
            tryConfigureLinearRamp(candidate);
            if (tryApplyRelaxationRampProperty(candidate, propertyKind, value)) {
                changed = true;
            }
        }
        if (!changed) {
            for (Object candidate : candidates) {
                if (candidate == null) continue;
                tryConfigureLinearRamp(candidate);
                Object freshCalc = tryInvokeNoArg(
                    candidate,
                    "getRampCalculator",
                    "getCalculator",
                    "getSelectedElement",
                    "getSelected"
                );
                if (freshCalc != null && freshCalc != candidate) {
                    if (tryApplyRelaxationRampProperty(freshCalc, propertyKind, value)) {
                        changed = true;
                        break;
                    }
                }
            }
        }
        if (changed) {
            if ("initial".equals(propertyKind)) {
                sim.println(logKey + " -> " + value);
            } else {
                sim.println(logKey + " -> " + ((int) Math.round(value)));
            }
        } else {
            sim.println(
                "WARNING: " + warningMessage + " Candidates: "
                + describeObjectClasses(candidates, 32)
            );
        }
        return changed;
    }

    private boolean tryApplyDirectRelaxationRampParameter(
            Simulation sim,
            boolean pressureRamp,
            String propertyKind,
            double value) {
        Object segregated = getSegregatedFlowSolver(sim);
        if (segregated == null) {
            return false;
        }
        Object solver = pressureRamp
            ? tryInvokeNoArg(
                segregated,
                "getPressureSolver",
                "getPressureLinearSolver",
                "getPressureCorrectionSolver"
            )
            : tryInvokeNoArg(
                segregated,
                "getVelocitySolver",
                "getMomentumSolver"
            );
        return tryApplyRelaxationRampParameterOnSolver(solver, propertyKind, value);
    }

    private boolean tryApplyRelaxationRampParameterOnSolver(
            Object solver,
            String propertyKind,
            double value) {
        if (solver == null) {
            return false;
        }
        Object manager = tryResolveRelaxationRampManager(solver);
        if (manager == null) {
            return false;
        }
        tryConfigureLinearRampOnManager(manager);
        Object calculator = tryResolveLinearRampCalculator(manager);
        if (calculator != null && tryApplyRelaxationRampProperty(calculator, propertyKind, value)) {
            return true;
        }
        if (tryApplyRelaxationRampProperty(manager, propertyKind, value)) {
            return true;
        }
        return tryApplyRelaxationRampProperty(solver, propertyKind, value);
    }

    private Object tryResolveRelaxationRampManager(Object solver) {
        return tryInvokeNoArg(
            solver,
            "getRampCalculatorManager",
            "getUnderRelaxationFactorRamp",
            "getUnderRelaxationRamp",
            "getRampManager",
            "getCalculatorManager"
        );
    }

    private boolean tryConfigureLinearRampOnManager(Object manager) {
        if (manager == null) {
            return false;
        }
        Object option = tryInvokeNoArg(
            manager,
            "getRampCalculatorOption",
            "getUnderRelaxationFactorRampOption",
            "getUnderRelaxationRampOption",
            "getRampOption",
            "getRampTypeOption",
            "getCalculatorOption",
            "getCalculatorTypeOption"
        );
        if (option != null && tryConfigureOption(option, "LINEAR_RAMP", "LINEAR RAMP", "LINEAR")) {
            return true;
        }
        return tryConfigureLinearRamp(manager);
    }

    private Object tryResolveLinearRampCalculator(Object manager) {
        if (manager == null) {
            return null;
        }
        Object calculator = tryInvokeNoArg(
            manager,
            "getCalculator",
            "getRampCalculator",
            "getLinearRamp",
            "getSelectedElement",
            "getSelected"
        );
        if (calculator != null && !isNoRampCalculator(calculator)) {
            return calculator;
        }
        return null;
    }

    private boolean isNoRampCalculator(Object candidate) {
        if (candidate == null) {
            return false;
        }
        String className = candidate.getClass().getName();
        return className != null && className.contains("NoRampCalculator");
    }

    private ArrayList<Object> collectPressureRelaxationRampCandidates(Simulation sim) {
        Object segregated = getSegregatedFlowSolver(sim);
        Object pressureSolver = tryInvokeNoArg(
            segregated,
            "getPressureSolver",
            "getPressureLinearSolver",
            "getPressureCorrectionSolver"
        );
        return collectRelaxationRampCandidates(pressureSolver, segregated);
    }

    private ArrayList<Object> collectVelocityRelaxationRampCandidates(Simulation sim) {
        Object segregated = getSegregatedFlowSolver(sim);
        Object velocitySolver = tryInvokeNoArg(
            segregated,
            "getVelocitySolver",
            "getMomentumSolver"
        );
        return collectRelaxationRampCandidates(velocitySolver, segregated);
    }

    private ArrayList<Object> collectRelaxationRampCandidates(Object primary, Object secondary) {
        ArrayList<Object> candidates = new ArrayList<Object>();
        addCandidateGraph(candidates, primary, 4);
        if (secondary != primary) {
            addCandidateGraph(candidates, secondary, 4);
        }
        return candidates;
    }

    private boolean tryConfigureLinearRamp(Object candidate) {
        String[] linearTokens = new String[] {"LINEAR RAMP", "LINEAR"};
        if (tryConfigureOption(candidate, linearTokens)) {
            return true;
        }
        return trySelectOptionOnGetter(
            candidate,
            linearTokens,
            "getUnderRelaxationFactorRampOption",
            "getUnderRelaxationRampOption",
            "getRampCalculatorOption",
            "getRampOption",
            "getRampTypeOption",
            "getCalculatorOption",
            "getCalculatorTypeOption",
            "getSelectedElement",
            "getOptionInput",
            "getSelectedInput",
            "getEnumeratedOptionInput"
        );
    }

    private boolean tryApplyRelaxationRampProperty(
            Object candidate,
            String propertyKind,
            double value) {
        if (candidate == null) {
            return false;
        }
        if ("initial".equals(propertyKind)) {
            if (invokeDoubleSetter(
                    candidate,
                    value,
                    "setInitialRampValue",
                    "setInitialValue",
                    "setInitialFactor",
                    "setInitialUrf",
                    "setInitialURF")) {
                return true;
            }
            return trySetNumericValueOnGetter(
                candidate,
                value,
                "getInitialRampValue",
                "getInitialValue",
                "getInitialFactor",
                "getInitialUrf",
                "getInitialURF"
            );
        }
        if ("start".equals(propertyKind)) {
            if (invokeIntegerSetter(candidate, (int) Math.round(value), "setStartIteration")) {
                return true;
            }
            return trySetNumericValueOnGetter(
                candidate,
                value,
                "getStartIteration",
                "getStartStep",
                "getStartValue"
            );
        }
        if ("end".equals(propertyKind)) {
            if (invokeIntegerSetter(candidate, (int) Math.round(value), "setEndIteration")) {
                return true;
            }
            return trySetNumericValueOnGetter(
                candidate,
                value,
                "getEndIteration",
                "getEndStep",
                "getEndValue"
            );
        }
        return false;
    }

    private void applyPressureAmgNumericSettings(
            Simulation sim,
            int maxCycles,
            double convergeTol,
            double epsilon,
            String smoother,
            String acceleration,
            int preSweeps,
            int postSweeps,
            int maxLevels) {
        applyPressureAmgMaxCyclesSetting(sim, maxCycles);
        applyPressureAmgConvergeTolSetting(sim, convergeTol);
        applyPressureAmgEpsilonSetting(sim, epsilon);
        applyPressureAmgSmootherSetting(sim, smoother);
        applyPressureAmgAccelerationSetting(sim, acceleration);
        applyPressureAmgPreSweepsSetting(sim, preSweeps);
        applyPressureAmgPostSweepsSetting(sim, postSweeps);
        applyPressureAmgMaxLevelsSetting(sim, maxLevels);
    }

    private void applyPressureAmgMaxCyclesSetting(Simulation sim, int value) {
        if (tryApplyRecordedPressureAmgMaxCycles(sim, value)) {
            sim.println("pressure_amg_max_cycles -> " + value);
            return;
        }
        sim.println("WARNING: pressure AMG max cycles setter not available, keeping template default.");
    }

    private boolean tryApplyRecordedPressureAmgMaxCycles(Simulation sim, int value) {
        try {
            AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
            if (amgLinearSolver == null) {
                return false;
            }
            amgLinearSolver.setMaxCycles(value);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private void applyPressureAmgConvergeTolSetting(Simulation sim, double value) {
        if (tryApplyRecordedPressureAmgConvergeTol(sim, value)) {
            sim.println("pressure_amg_converge_tol -> " + value);
            return;
        }
        sim.println("WARNING: pressure AMG converge tolerance setter not available, keeping template default.");
    }

    private boolean tryApplyRecordedPressureAmgConvergeTol(Simulation sim, double value) {
        try {
            AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
            if (amgLinearSolver == null) {
                return false;
            }
            amgLinearSolver.setConvergeTol(value);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private void applyPressureAmgEpsilonSetting(Simulation sim, double value) {
        if (tryApplyRecordedPressureAmgEpsilon(sim, value)) {
            sim.println("pressure_amg_epsilon -> " + value);
            return;
        }
        sim.println("WARNING: pressure AMG epsilon setter not available, keeping template default.");
    }

    private boolean tryApplyRecordedPressureAmgEpsilon(Simulation sim, double value) {
        try {
            AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
            if (amgLinearSolver == null) {
                return false;
            }
            amgLinearSolver.setEpsilon(value);
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private void applyPressureAmgSmootherSetting(Simulation sim, String value) {
        String trimmedValue = value == null ? "" : value.trim();
        String[] desiredTokens = getPressureAmgSmootherTokens(trimmedValue);
        if (desiredTokens.length == 0) {
            return;
        }
        if (tryApplyRecordedPressureAmgSmoother(sim, desiredTokens)) {
            sim.println("pressure_amg_smoother -> " + trimmedValue);
            return;
        }
        sim.println("WARNING: pressure AMG smoother setter not available, keeping template default.");
    }

    private boolean tryApplyRecordedPressureAmgSmoother(Simulation sim, String[] desiredTokens) {
        try {
            AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
            if (amgLinearSolver == null) {
                return false;
            }
            return trySelectOptionOnGetter(amgLinearSolver, desiredTokens, "getSmootherOption");
        } catch (Exception ignored) {}
        return false;
    }

    private void applyPressureAmgAccelerationSetting(Simulation sim, String value) {
        String trimmedValue = value == null ? "" : value.trim();
        String[] desiredTokens = getPressureAmgAccelerationTokens(trimmedValue);
        if (desiredTokens.length == 0) {
            return;
        }
        if (tryApplyRecordedPressureAmgAcceleration(sim, desiredTokens)) {
            sim.println("pressure_amg_acceleration -> " + trimmedValue);
            return;
        }
        sim.println("WARNING: pressure AMG acceleration setter not available, keeping template default.");
    }

    private boolean tryApplyRecordedPressureAmgAcceleration(Simulation sim, String[] desiredTokens) {
        try {
            AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
            if (amgLinearSolver == null) {
                return false;
            }
            return trySelectOptionOnGetter(amgLinearSolver, desiredTokens, "getAccelerationOption");
        } catch (Exception ignored) {}
        return false;
    }

    private void applyPressureAmgPreSweepsSetting(Simulation sim, int value) {
        if (value <= 0) {
            return;
        }
        if (tryApplyRecordedPressureAmgPreSweeps(sim, value)) {
            sim.println("pressure_amg_pre_sweeps -> " + value);
            return;
        }
        sim.println("WARNING: pressure AMG pre sweeps setter not available, keeping template default.");
    }

    private boolean tryApplyRecordedPressureAmgPreSweeps(Simulation sim, int value) {
        try {
            AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
            if (amgLinearSolver == null) {
                return false;
            }
            Object cycleType = amgLinearSolver.getCycleType();
            if (cycleType == null) {
                return false;
            }
            if (invokeIntegerSetter(cycleType, value, "setPreSweeps")) {
                return true;
            }
            return trySetNumericValueOnGetter(cycleType, value, "getPreSweeps");
        } catch (Exception ignored) {}
        return false;
    }

    private void applyPressureAmgPostSweepsSetting(Simulation sim, int value) {
        if (value <= 0) {
            return;
        }
        if (tryApplyRecordedPressureAmgPostSweeps(sim, value)) {
            sim.println("pressure_amg_post_sweeps -> " + value);
            return;
        }
        sim.println("WARNING: pressure AMG post sweeps setter not available, keeping template default.");
    }

    private boolean tryApplyRecordedPressureAmgPostSweeps(Simulation sim, int value) {
        try {
            AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
            if (amgLinearSolver == null) {
                return false;
            }
            Object cycleType = amgLinearSolver.getCycleType();
            if (cycleType == null) {
                return false;
            }
            if (invokeIntegerSetter(cycleType, value, "setPostSweeps")) {
                return true;
            }
            return trySetNumericValueOnGetter(cycleType, value, "getPostSweeps");
        } catch (Exception ignored) {}
        return false;
    }

    private void applyPressureAmgMaxLevelsSetting(Simulation sim, int value) {
        if (value <= 0) {
            return;
        }
        if (tryApplyRecordedPressureAmgMaxLevels(sim, value)) {
            sim.println("pressure_amg_max_levels -> " + value);
            return;
        }
        sim.println("WARNING: pressure AMG max levels setter not available, keeping template default.");
    }

    private boolean tryApplyRecordedPressureAmgMaxLevels(Simulation sim, int value) {
        try {
            AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
            if (amgLinearSolver == null) {
                return false;
            }
            Object cycleType = amgLinearSolver.getCycleType();
            if (cycleType == null) {
                return false;
            }
            if (invokeIntegerSetter(cycleType, value, "setMaxLevels")) {
                return true;
            }
            return trySetNumericValueOnGetter(cycleType, value, "getMaxLevels");
        } catch (Exception ignored) {}
        return false;
    }

    private String[] getPressureAmgSmootherTokens(String value) {
        String normalized = normalizeConfigKey(value);
        if (normalized.isEmpty()) {
            return new String[0];
        }
        if (normalized.equals("gauss_seidel")) {
            return new String[] {"GAUSS_SEIDEL", "GAUSS SEIDEL", "GAUSS-SEIDEL", "SEIDEL"};
        }
        if (normalized.equals("jacobi")) {
            return new String[] {"JACOBI"};
        }
        if (normalized.equals("ilu")) {
            return new String[] {"ILU"};
        }
        return new String[] {value, normalized};
    }

    private String[] getPressureAmgAccelerationTokens(String value) {
        String normalized = normalizeConfigKey(value);
        if (normalized.isEmpty()) {
            return new String[0];
        }
        if (normalized.equals("cg")) {
            return new String[] {"CG"};
        }
        if (normalized.equals("bicgstab")) {
            return new String[] {"BICGSTAB", "BICG_STAB", "BICG-STAB", "BI-CG-STAB"};
        }
        if (normalized.equals("gmres")) {
            return new String[] {"GMRES"};
        }
        return new String[] {value, normalized};
    }

    private String normalizeConfigKey(String value) {
        if (value == null) {
            return "";
        }
        String normalized = value.trim().toLowerCase(Locale.ROOT);
        normalized = normalized.replaceAll("[^a-z0-9]+", "_");
        while (normalized.startsWith("_")) {
            normalized = normalized.substring(1);
        }
        while (normalized.endsWith("_")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }

    private void applyPressureAmgCycleSetting(Simulation sim, int cycleMode) {
        if (tryApplyRecordedPressureAmgCycle(sim, cycleMode)) {
            sim.println("pressure_amg_cycle -> " + cycleMode);
            return;
        }
        boolean changed = tryApplyDirectAmgCycleSetting(
            sim,
            true,
            cycleMode <= 0
                ? new String[] {"V-CYCLE", "V CYCLE", "V_CYCLE", "VCYCLE"}
                : new String[] {"W-CYCLE", "W CYCLE", "W_CYCLE", "WCYCLE"}
        );
        if (!changed) {
            changed = applyNamedAmgCycleSetting(
            sim,
            "pressure_amg_cycle",
            cycleMode,
            collectPressureCycleCandidates(sim),
            cycleMode <= 0
                ? new String[] {"V-CYCLE", "V CYCLE", "V_CYCLE", "VCYCLE"}
                : new String[] {"W-CYCLE", "W CYCLE", "W_CYCLE", "WCYCLE"},
            "Pressure AMG cycle option not found via macro reflection; keeping template default."
            );
        }
        if (changed) {
            return;
        }
    }

    private boolean tryApplyRecordedPressureAmgCycle(Simulation sim, int cycleMode) {
        try {
            PressureSolver pressureSolver = getTypedPressureSolver(sim);
            if (pressureSolver == null) {
                return false;
            }
            AMGLinearSolver amgLinearSolver = pressureSolver.getAMGLinearSolver();
            if (amgLinearSolver == null) {
                return false;
            }
            if (cycleMode <= 0) {
                amgLinearSolver.getCycleOption().setSelected(AMGCycleOption.Type.V_CYCLE);
            } else {
                amgLinearSolver.getCycleOption().setSelected(AMGCycleOption.Type.W_CYCLE);
            }
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private void applyVelocityAmgCycleSetting(Simulation sim, int cycleMode) {
        if (tryApplyRecordedVelocityAmgCycle(sim, cycleMode)) {
            sim.println("velocity_amg_cycle -> " + cycleMode);
            return;
        }
        boolean changed = tryApplyDirectAmgCycleSetting(
            sim,
            false,
            cycleMode <= 0
                ? new String[] {"FLEX-CYCLE", "FLEX CYCLE", "FLEX_CYCLE", "FLEXCYCLE", "FLEX"}
                : new String[] {"V-CYCLE", "V CYCLE", "V_CYCLE", "VCYCLE"}
        );
        if (!changed) {
            changed = applyNamedAmgCycleSetting(
            sim,
            "velocity_amg_cycle",
            cycleMode,
            collectVelocityCycleCandidates(sim),
            cycleMode <= 0
                ? new String[] {"FLEX-CYCLE", "FLEX CYCLE", "FLEX_CYCLE", "FLEXCYCLE", "FLEX"}
                : new String[] {"V-CYCLE", "V CYCLE", "V_CYCLE", "VCYCLE"},
            "Velocity AMG cycle option not found via macro reflection; keeping template default."
            );
        }
        if (changed) {
            return;
        }
    }

    private boolean tryApplyRecordedVelocityAmgCycle(Simulation sim, int cycleMode) {
        try {
            VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
            if (velocitySolver == null) {
                return false;
            }
            AMGLinearSolver amgLinearSolver = velocitySolver.getAMGLinearSolver();
            if (amgLinearSolver == null) {
                return false;
            }
            if (cycleMode <= 0) {
                amgLinearSolver.getCycleOption().setSelected(AMGCycleOption.Type.F_CYCLE);
            } else {
                amgLinearSolver.getCycleOption().setSelected(AMGCycleOption.Type.V_CYCLE);
            }
            return true;
        } catch (Exception ignored) {}
        return false;
    }

    private void applyAmgCycleSetting(Simulation sim, int cycleMode) {
        String[] pressureTokens = cycleMode <= 0
            ? new String[] {"V-CYCLE", "V CYCLE", "V_CYCLE", "VCYCLE"}
            : new String[] {"W-CYCLE", "W CYCLE", "W_CYCLE", "WCYCLE"};
        String[] velocityTokens = cycleMode <= 0
            ? new String[] {"FLEX-CYCLE", "FLEX CYCLE", "FLEX_CYCLE", "FLEXCYCLE", "FLEX"}
            : new String[] {"V-CYCLE", "V CYCLE", "V_CYCLE", "VCYCLE"};
        boolean pressureChanged = tryApplyDirectAmgCycleSetting(
            sim,
            true,
            pressureTokens
        );
        if (!pressureChanged) {
            pressureChanged = applyNamedAmgCycleSetting(
                sim,
                "pressure_amg_cycle",
                cycleMode,
                collectPressureCycleCandidates(sim),
                pressureTokens,
                null
            );
        }
        boolean velocityChanged = tryApplyDirectAmgCycleSetting(
            sim,
            false,
            velocityTokens
        );
        if (!velocityChanged) {
            velocityChanged = applyNamedAmgCycleSetting(
                sim,
                "velocity_amg_cycle",
                cycleMode,
                collectVelocityCycleCandidates(sim),
                velocityTokens,
                null
            );
        }
        if (pressureChanged || velocityChanged) {
            sim.println("amg_cycle -> " + cycleMode);
        } else {
            sim.println(
                "WARNING: AMG cycle option not found via macro reflection; keeping template default. "
                + "Candidates: " + describeObjectClasses(collectSolverCandidates(sim), 32)
            );
        }
    }

    private boolean applyNamedAmgCycleSetting(
            Simulation sim,
            String logKey,
            int cycleMode,
            ArrayList<Object> candidates,
            String[] optionTokens,
            String warningMessage) {
        boolean changed = false;
        for (Object candidate : candidates) {
            if (candidate == null) continue;
            if (tryConfigureOption(candidate, optionTokens)) {
                changed = true;
            }
            if (trySelectOptionOnGetter(
                    candidate,
                    optionTokens,
                    "getCycleOption",
                    "getCycleTypeOption",
                    "getAmgCycleOption",
                    "getAMGCycleOption",
                    "getCycleMethodOption",
                    "getAMGLinearSolver",
                    "getAmgLinearSolver",
                    "getLinearSolver",
                    "getSelectedElement",
                    "getOptionInput",
                    "getSelectedInput",
                    "getEnumeratedOptionInput")) {
                changed = true;
            }
        }
        if (warningMessage != null) {
            if (changed) {
                sim.println(logKey + " -> " + cycleMode);
            } else {
                sim.println(
                    "WARNING: " + warningMessage + " Candidates: "
                    + describeObjectClasses(candidates, 32)
                );
            }
        }
        return changed;
    }

    private boolean tryApplyDirectAmgCycleSetting(
            Simulation sim,
            boolean pressureCycle,
            String[] optionTokens) {
        Object segregated = getSegregatedFlowSolver(sim);
        if (segregated == null) {
            return false;
        }
        Object solver = pressureCycle
            ? tryInvokeNoArg(
                segregated,
                "getPressureSolver",
                "getPressureLinearSolver",
                "getPressureCorrectionSolver"
            )
            : tryInvokeNoArg(
                segregated,
                "getVelocitySolver",
                "getMomentumSolver"
            );
        if (solver == null) {
            return false;
        }
        Object amgLinearSolver = tryInvokeNoArg(
            solver,
            "getAMGLinearSolver",
            "getAmgLinearSolver",
            "getLinearSolver"
        );
        if (amgLinearSolver == null) {
            return false;
        }
        if (tryConfigureOption(amgLinearSolver, optionTokens)) {
            return true;
        }
        return trySelectOptionOnGetter(
            amgLinearSolver,
            optionTokens,
            "getCycleOption",
            "getCycleTypeOption",
            "getAmgCycleOption",
            "getAMGCycleOption"
        );
    }

    private ArrayList<Object> collectPressureCycleCandidates(Simulation sim) {
        Object segregated = getSegregatedFlowSolver(sim);
        Object pressureSolver = tryInvokeNoArg(
            segregated,
            "getPressureSolver",
            "getPressureLinearSolver",
            "getPressureCorrectionSolver"
        );
        return collectObjectCandidates(pressureSolver, segregated);
    }

    private ArrayList<Object> collectVelocityCycleCandidates(Simulation sim) {
        Object segregated = getSegregatedFlowSolver(sim);
        Object velocitySolver = tryInvokeNoArg(
            segregated,
            "getVelocitySolver",
            "getMomentumSolver"
        );
        return collectObjectCandidates(velocitySolver, segregated);
    }

    private Object getSegregatedFlowSolver(Simulation sim) {
        Object segregated = getNamedSolver(sim, "star.flow.SegregatedFlowSolver");
        if (segregated == null) {
            segregated = findSolverByClassFragment(sim, "SegregatedFlowSolver");
        }
        return segregated;
    }

    private ArrayList<Object> collectObjectCandidates(Object primary, Object secondary) {
        ArrayList<Object> candidates = new ArrayList<Object>();
        addCandidateGraph(candidates, primary, 3);
        if (secondary != primary) {
            addCandidateGraph(candidates, secondary, 3);
        }
        return candidates;
    }

    private void applyAmgSolverSetting(Simulation sim, boolean enabled) {
        ArrayList<Object> candidates = collectSolverCandidates(sim);
        boolean changed = false;
        String[] optionTokens = enabled
            ? new String[] {"AMG", "MULTIGRID"}
            : new String[] {"BICGSTAB", "BCGSTAB", "BICG", "CGSTAB"};

        for (Object candidate : candidates) {
            if (candidate == null) continue;
            if (invokeBooleanSetter(
                    candidate,
                    enabled,
                    "setUseAlgebraicMultigrid",
                    "setUseAlgebraicMultiGrid",
                    "setUseMultigrid",
                    "setUseMultiGrid",
                    "setUseAmg",
                    "setUseAMG",
                    "setAmgEnabled",
                    "setAMGEnabled",
                    "setMultigridEnabled",
                    "setMultiGridEnabled")) {
                changed = true;
            }
            if (invokeBooleanSetterByKeyword(
                    candidate,
                    enabled,
                    "AMG",
                    "Amg",
                    "Multigrid",
                    "MultiGrid",
                    "AlgebraicMultigrid",
                    "AlgebraicMultiGrid")) {
                changed = true;
            }
            if (tryConfigureOption(candidate, optionTokens)) {
                changed = true;
            }
            if (trySelectOptionOnGetter(
                    candidate,
                    optionTokens,
                    "getLinearSolverOption",
                    "getLinearSolverTypeOption",
                    "getLinearSolverMethodOption",
                    "getSolverOption",
                    "getSolverTypeOption",
                    "getMatrixSolverOption",
                    "getMatrixSolverTypeOption",
                    "getPreconditionerOption",
                    "getPreconditionerTypeOption",
                    "getPreconditionerMethodOption",
                    "getKrylovSolverOption",
                    "getKrylovSolverTypeOption",
                    "getMethodOption",
                    "getAmgOption",
                    "getAMGOption")) {
                changed = true;
            }
        }

        if (changed) {
            sim.println("amg_solver -> " + (enabled ? 1 : 0));
        } else {
            sim.println(
                "WARNING: AMG solver option not found via macro reflection; keeping template default. "
                + "Candidates: " + describeObjectClasses(candidates, 32)
            );
        }
    }

    private ArrayList<Object> collectSolverCandidates(Simulation sim) {
        ArrayList<Object> candidates = new ArrayList<Object>();
        try {
            for (Object solverObj : sim.getSolverManager().getObjects()) {
                addCandidateGraph(candidates, solverObj, 3);
            }
        } catch (Exception ignored) {}

        try {
            Object segregated = getNamedSolver(sim, "star.flow.SegregatedFlowSolver");
            addCandidateGraph(candidates, segregated, 3);
            Object coupled = getNamedSolver(sim, "star.flow.CoupledFlowSolver");
            addCandidateGraph(candidates, coupled, 3);
            Object pressureSolver = tryInvokeNoArg(
                segregated,
                "getPressureSolver",
                "getPressureLinearSolver",
                "getPressureCorrectionSolver"
            );
            addCandidateGraph(candidates, pressureSolver, 2);
            Object velocitySolver = tryInvokeNoArg(
                segregated,
                "getVelocitySolver",
                "getMomentumSolver"
            );
            addCandidateGraph(candidates, velocitySolver, 2);
        } catch (Exception ignored) {}
        return candidates;
    }

    private void addCandidateGraph(ArrayList<Object> candidates, Object root, int depth) {
        if (root == null || depth < 0 || candidates.contains(root)) {
            return;
        }
        candidates.add(root);
        if (depth == 0) {
            return;
        }

        String[] commonGetterNames = new String[] {
            "getSolver",
            "getLinearSolver",
            "getLinearSolverManager",
            "getLinearSolverProperties",
            "getPressureSolver",
            "getPressureLinearSolver",
            "getPressureCorrectionSolver",
            "getVelocitySolver",
            "getMomentumSolver",
            "getCoupledSolver",
            "getCoupledFlowSolver",
            "getAssembledSolver",
            "getAssembledLinearSolver",
            "getPreconditioner",
            "getPreconditionerOption",
            "getPreconditionerTypeOption",
            "getLinearSolverOption",
            "getLinearSolverTypeOption",
            "getLinearSolverMethodOption",
            "getSolverOption",
            "getSolverTypeOption",
            "getMatrixSolverOption",
            "getMatrixSolverTypeOption",
            "getKrylovSolverOption",
            "getKrylovSolverTypeOption",
            "getMethodOption",
            "getCycleOption",
            "getCycleTypeOption",
            "getAmgCycleOption",
            "getAMGCycleOption",
            "getUnderRelaxationFactorRamp",
            "getUnderRelaxationRamp",
            "getUnderRelaxationFactorRampOption",
            "getUnderRelaxationRampOption",
            "getLinearRamp",
            "getRamp",
            "getRampCalculator",
            "getRampCalculatorManager",
            "getCalculator",
            "getCalculatorManager",
            "getSelectedElement",
            "getOptionInput",
            "getSelectedInput",
            "getEnumeratedOptionInput",
            "getAmgOption",
            "getAMGOption",
            "getAmgSolver",
            "getAMGSolver"
        };
        for (String getterName : commonGetterNames) {
            Object nested = tryInvokeNoArg(root, getterName);
            if (nested != null && nested != root) {
                addCandidateGraph(candidates, nested, depth - 1);
            }
        }

        for (Method method : root.getClass().getMethods()) {
            if (method.getParameterCount() != 0) {
                continue;
            }
            String methodName = method.getName();
            if (!methodName.startsWith("get") || methodName.equals("getClass")) {
                continue;
            }
            if (
                !methodName.contains("Solver")
                && !methodName.contains("Option")
                && !methodName.contains("Precondition")
                && !methodName.contains("Multigrid")
                && !methodName.contains("AMG")
                && !methodName.contains("Amg")
                && !methodName.contains("Ramp")
                && !methodName.contains("Relax")
                && !methodName.contains("Calculator")
            ) {
                continue;
            }
            Class<?> returnType = method.getReturnType();
            if (returnType.isPrimitive()) {
                continue;
            }
            String returnTypeName = returnType.getName();
            if (returnTypeName.startsWith("java.") || returnTypeName.startsWith("javax.")) {
                continue;
            }
            try {
                Object nested = method.invoke(root);
                if (nested != null && nested != root) {
                    addCandidateGraph(candidates, nested, depth - 1);
                }
            } catch (Exception ignored) {}
        }
    }

    private Object getNamedSolver(Simulation sim, String className) {
        if (className == null || className.isEmpty()) {
            return null;
        }
        String simpleName = className;
        int lastDot = className.lastIndexOf('.');
        if (lastDot >= 0 && lastDot + 1 < className.length()) {
            simpleName = className.substring(lastDot + 1);
        }
        try {
            Class<?> solverClass = Class.forName(className);
            Object solver = sim.getSolverManager().getClass()
                .getMethod("getSolver", Class.class)
                .invoke(sim.getSolverManager(), solverClass);
            if (solver != null) {
                return solver;
            }
        } catch (Exception ignored) {}
        return findSolverByClassFragment(sim, simpleName);
    }

    private Object findSolverByClassFragment(Simulation sim, String fragment) {
        if (fragment == null || fragment.isEmpty()) {
            return null;
        }
        try {
            for (Object solverObj : sim.getSolverManager().getObjects()) {
                if (solverObj == null) continue;
                String className = solverObj.getClass().getName();
                if (className != null && className.contains(fragment)) {
                    return solverObj;
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private String describeAvailableSolvers(Simulation sim, int limit) {
        LinkedHashSet<String> names = new LinkedHashSet<String>();
        try {
            for (Object solverObj : sim.getSolverManager().getObjects()) {
                if (solverObj == null) continue;
                names.add(solverObj.getClass().getName());
                if (limit > 0 && names.size() >= limit) {
                    break;
                }
            }
        } catch (Exception ignored) {}
        if (names.isEmpty()) {
            return "<none>";
        }
        StringBuilder sb = new StringBuilder();
        int idx = 0;
        for (String name : names) {
            if (idx > 0) sb.append(", ");
            sb.append(name);
            idx += 1;
        }
        return sb.toString();
    }

    private String describeMeshers(AutoMeshOperation meshOp, int limit) {
        ArrayList<Object> meshers = new ArrayList<Object>();
        if (meshOp == null) {
            return "<none>";
        }
        try {
            for (Object mesherObj : meshOp.getMeshers().getObjects()) {
                if (mesherObj != null) {
                    meshers.add(mesherObj);
                }
            }
        } catch (Exception ignored) {}
        return describeNamedObjects(meshers, limit);
    }

    private String describeSurfaceMeshControls(AutoMeshOperation meshOp, int limit) {
        ArrayList<Object> controls = new ArrayList<Object>();
        if (meshOp == null) {
            return "<none>";
        }
        try {
            for (Object ctrl : meshOp.getCustomMeshControls().getObjects()) {
                if (ctrl instanceof SurfaceCustomMeshControl) {
                    controls.add(ctrl);
                }
            }
        } catch (Exception ignored) {}
        return describeNamedObjects(controls, limit);
    }

    private String describeVolumeMeshControls(AutoMeshOperation meshOp, int limit) {
        ArrayList<Object> controls = new ArrayList<Object>();
        if (meshOp == null) {
            return "<none>";
        }
        try {
            for (Object ctrl : meshOp.getCustomMeshControls().getObjects()) {
                if (ctrl instanceof VolumeCustomMeshControl) {
                    controls.add(ctrl);
                }
            }
        } catch (Exception ignored) {}
        return describeNamedObjects(controls, limit);
    }

    private String describeObjectClasses(List<Object> objects, int limit) {
        LinkedHashSet<String> names = new LinkedHashSet<String>();
        if (objects == null) {
            return "<none>";
        }
        for (Object obj : objects) {
            if (obj == null) continue;
            names.add(obj.getClass().getName());
            if (limit > 0 && names.size() >= limit) {
                break;
            }
        }
        if (names.isEmpty()) {
            return "<none>";
        }
        StringBuilder sb = new StringBuilder();
        int idx = 0;
        for (String name : names) {
            if (idx > 0) sb.append(", ");
            sb.append(name);
            idx += 1;
        }
        return sb.toString();
    }

    private String describeNamedObjects(List<Object> objects, int limit) {
        LinkedHashSet<String> names = new LinkedHashSet<String>();
        if (objects == null) {
            return "<none>";
        }
        for (Object obj : objects) {
            if (obj == null) continue;
            names.add(describeNamedObject(obj));
            if (limit > 0 && names.size() >= limit) {
                break;
            }
        }
        if (names.isEmpty()) {
            return "<none>";
        }
        StringBuilder sb = new StringBuilder();
        int idx = 0;
        for (String name : names) {
            if (idx > 0) sb.append(", ");
            sb.append(name);
            idx += 1;
        }
        return sb.toString();
    }

    private String describeNamedObject(Object obj) {
        if (obj == null) {
            return "<null>";
        }
        Object named = tryInvokeNoArg(obj, "getPresentationName", "getDisplayName", "getName");
        if (named instanceof CharSequence) {
            String value = named.toString().trim();
            if (!value.isEmpty()) {
                return value;
            }
        }
        return obj.getClass().getSimpleName();
    }

    private int scoreRequestedName(String requested, String candidate) {
        if (requested == null || candidate == null) {
            return 0;
        }
        String requestedTrimmed = requested.trim();
        String candidateTrimmed = candidate.trim();
        if (requestedTrimmed.isEmpty() || candidateTrimmed.isEmpty()) {
            return 0;
        }
        if (requestedTrimmed.equals(candidateTrimmed)) {
            return 100;
        }
        String requestedNormalized = normalizeLabel(requestedTrimmed);
        String candidateNormalized = normalizeLabel(candidateTrimmed);
        if (requestedNormalized.isEmpty() || candidateNormalized.isEmpty()) {
            return 0;
        }
        if (requestedNormalized.equals(candidateNormalized)) {
            return 95;
        }
        if (
            candidateNormalized.startsWith(requestedNormalized)
            || candidateNormalized.endsWith(requestedNormalized)
            || candidateNormalized.contains(requestedNormalized)
        ) {
            return 80;
        }
        if (requestedNormalized.contains(candidateNormalized)) {
            return 70;
        }

        ArrayList<String> requestedTokens = splitNameTokens(requestedTrimmed);
        ArrayList<String> candidateTokens = splitNameTokens(candidateTrimmed);
        int overlap = 0;
        for (String requestedToken : requestedTokens) {
            if (candidateTokens.contains(requestedToken)) {
                overlap += 1;
            }
        }
        if (overlap == 0) {
            return 0;
        }
        if (overlap >= Math.min(requestedTokens.size(), candidateTokens.size())) {
            return 65;
        }
        if (overlap >= 2) {
            return 55;
        }
        if (overlap == 1 && (requestedTokens.size() == 1 || candidateTokens.size() == 1)) {
            return 45;
        }
        return 0;
    }

    private ArrayList<String> splitNameTokens(String text) {
        ArrayList<String> tokens = new ArrayList<String>();
        if (text == null) {
            return tokens;
        }
        for (String token : text.split("[^A-Za-z0-9]+")) {
            if (token == null) continue;
            String normalized = normalizeLabel(token);
            if (normalized.isEmpty() || tokens.contains(normalized)) {
                continue;
            }
            tokens.add(normalized);
        }
        return tokens;
    }

    private void logSolverTreeSnapshotAfterUpdate(
            Simulation sim,
            LinkedHashSet<String> updatedKeys,
            String rawContent) {
        sim.println("[AI] 参数更新已应用，开始回读 solver tree...");
        if (rawContent != null && !rawContent.isEmpty()) {
            sim.println("[AI] RL payload: " + rawContent);
        }
        if (updatedKeys == null || updatedKeys.isEmpty()) {
            sim.println("[AI][solver-tree] <no parsed keys>");
            sim.println("[AI] solver tree 回读完成。");
            return;
        }
        for (String key : updatedKeys) {
            logSolverTreeValue(sim, key);
        }
        sim.println("[AI] solver tree 回读完成。");
    }

    private void logSolverTreeValue(Simulation sim, String key) {
        if (key == null || key.isEmpty()) {
            return;
        }
        try {
            switch (key) {
                case "pressure_relaxation_factor": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadPressureRelaxationFactor(sim),
                        false
                    );
                    return;
                }
                case "pressure_relaxation_initial_value": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadPressureRelaxationInitialValue(sim),
                        false
                    );
                    return;
                }
                case "pressure_relaxation_start_iteration": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadPressureRelaxationStartIteration(sim),
                        true
                    );
                    return;
                }
                case "pressure_relaxation_end_iteration": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadPressureRelaxationEndIteration(sim),
                        true
                    );
                    return;
                }
                case "velocity_relaxation_initial_value": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadVelocityRelaxationInitialValue(sim),
                        false
                    );
                    return;
                }
                case "velocity_relaxation_start_iteration": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadVelocityRelaxationStartIteration(sim),
                        true
                    );
                    return;
                }
                case "velocity_relaxation_end_iteration": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadVelocityRelaxationEndIteration(sim),
                        true
                    );
                    return;
                }
                case "pressure_amg_cycle": {
                    logAmgCycleSolverTreeValue(sim, key, tryReadPressureAmgCycleLabel(sim), true);
                    return;
                }
                case "velocity_amg_cycle": {
                    logAmgCycleSolverTreeValue(sim, key, tryReadVelocityAmgCycleLabel(sim), false);
                    return;
                }
                case "amg_cycle": {
                    String pressureLabel = tryReadPressureAmgCycleLabel(sim);
                    String velocityLabel = tryReadVelocityAmgCycleLabel(sim);
                    Integer pressureMode = mapPressureAmgCycleLabelToMode(pressureLabel);
                    Integer velocityMode = mapVelocityAmgCycleLabelToMode(velocityLabel);
                    if (
                        pressureMode != null
                        && velocityMode != null
                        && pressureMode.intValue() == velocityMode.intValue()
                    ) {
                        sim.println(
                            "[AI][solver-tree] amg_cycle = " + pressureMode
                            + " (pressure=" + safeLabel(pressureLabel)
                            + ", velocity=" + safeLabel(velocityLabel) + ")"
                        );
                    } else {
                        sim.println(
                            "[AI][solver-tree] amg_cycle = <mixed/unavailable>"
                            + " (pressure=" + safeLabel(pressureLabel)
                            + ", velocity=" + safeLabel(velocityLabel) + ")"
                        );
                    }
                    return;
                }
                case "amg_solver": {
                    String amgSolverSummary = summarizeAmgSolverState(sim);
                    sim.println("[AI][solver-tree] amg_solver = " + safeLabel(amgSolverSummary));
                    return;
                }
                case "pressure_amg_max_cycles": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadPressureAmgMaxCycles(sim),
                        true
                    );
                    return;
                }
                case "pressure_amg_converge_tol": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadPressureAmgConvergeTol(sim),
                        false
                    );
                    return;
                }
                case "pressure_amg_epsilon": {
                    logNumericSolverTreeValue(
                        sim,
                        key,
                        tryReadPressureAmgEpsilon(sim),
                        false
                    );
                    return;
                }
                default:
                    sim.println(
                        "[AI][solver-tree] " + key
                        + " = <verification not implemented in macro>"
                    );
            }
        } catch (Exception e) {
            sim.println(
                "[AI][solver-tree] " + key + " = <readback failed: "
                + e.getMessage() + ">"
            );
        }
    }

    private void logNumericSolverTreeValue(
            Simulation sim,
            String key,
            Double value,
            boolean integerLike) {
        sim.println(
            "[AI][solver-tree] " + key + " = "
            + formatNumericSolverTreeValue(value, integerLike)
        );
    }

    private void logAmgCycleSolverTreeValue(
            Simulation sim,
            String key,
            String selectedLabel,
            boolean pressureCycle) {
        Integer mode = pressureCycle
            ? mapPressureAmgCycleLabelToMode(selectedLabel)
            : mapVelocityAmgCycleLabelToMode(selectedLabel);
        if (mode == null) {
            sim.println(
                "[AI][solver-tree] " + key + " = <unavailable>"
                + " (selected=" + safeLabel(selectedLabel) + ")"
            );
            return;
        }
        sim.println(
            "[AI][solver-tree] " + key + " = " + mode
            + " (selected=" + safeLabel(selectedLabel) + ")"
        );
    }

    private String formatNumericSolverTreeValue(Double value, boolean integerLike) {
        if (value == null) {
            return "<unavailable>";
        }
        if (integerLike) {
            return Integer.toString((int) Math.round(value.doubleValue()));
        }
        return Double.toString(value.doubleValue());
    }

    private String safeLabel(String value) {
        if (value == null || value.trim().isEmpty()) {
            return "<unavailable>";
        }
        return value.trim();
    }

    private Double tryReadPressureRelaxationFactor(Simulation sim) {
        PressureSolver pressureSolver = getTypedPressureSolver(sim);
        return tryReadNumericFromTarget(
            pressureSolver,
            "getUrf",
            "getURF",
            "getUnderRelaxationFactor"
        );
    }

    private Double tryReadPressureRelaxationInitialValue(Simulation sim) {
        PressureSolver pressureSolver = getTypedPressureSolver(sim);
        LinearRampCalculator linearRamp = ensureTypedLinearRamp(pressureSolver);
        return tryReadNumericFromTarget(
            linearRamp,
            "getInitialRampValueQuantity",
            "getInitialValue",
            "getInitialRampValue"
        );
    }

    private Double tryReadPressureRelaxationStartIteration(Simulation sim) {
        PressureSolver pressureSolver = getTypedPressureSolver(sim);
        LinearRampCalculator linearRamp = ensureTypedLinearRamp(pressureSolver);
        return tryReadNumericFromTarget(linearRamp, "getStartIterationValue");
    }

    private Double tryReadPressureRelaxationEndIteration(Simulation sim) {
        PressureSolver pressureSolver = getTypedPressureSolver(sim);
        LinearRampCalculator linearRamp = ensureTypedLinearRamp(pressureSolver);
        return tryReadNumericFromTarget(linearRamp, "getEndIterationValue");
    }

    private Double tryReadVelocityRelaxationInitialValue(Simulation sim) {
        VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
        LinearRampCalculator linearRamp = ensureTypedLinearRamp(velocitySolver);
        return tryReadNumericFromTarget(
            linearRamp,
            "getInitialRampValueQuantity",
            "getInitialValue",
            "getInitialRampValue"
        );
    }

    private Double tryReadVelocityRelaxationStartIteration(Simulation sim) {
        VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
        LinearRampCalculator linearRamp = ensureTypedLinearRamp(velocitySolver);
        return tryReadNumericFromTarget(linearRamp, "getStartIterationValue");
    }

    private Double tryReadVelocityRelaxationEndIteration(Simulation sim) {
        VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
        LinearRampCalculator linearRamp = ensureTypedLinearRamp(velocitySolver);
        return tryReadNumericFromTarget(linearRamp, "getEndIterationValue");
    }

    private Double tryReadPressureAmgMaxCycles(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
        return tryReadNumericFromTarget(amgLinearSolver, "getMaxCycles");
    }

    private Double tryReadPressureAmgConvergeTol(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
        return tryReadNumericFromTarget(amgLinearSolver, "getConvergeTol");
    }

    private Double tryReadPressureAmgEpsilon(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
        return tryReadNumericFromTarget(amgLinearSolver, "getEpsilon");
    }

    private Double tryReadSolverAmgMaxCyclesByTokenGroups(Simulation sim, String[]... tokenGroups) {
        return tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(sim, tokenGroups),
            4,
            new String[] {"getMaxCycles"},
            getSolverMetricChildGetterNames()
        );
    }

    private Double tryReadSolverAmgConvergeTolByTokenGroups(Simulation sim, String[]... tokenGroups) {
        return tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(sim, tokenGroups),
            4,
            new String[] {"getConvergeTol"},
            getSolverMetricChildGetterNames()
        );
    }

    private String tryReadSolverAmgCycleLabelByTokenGroups(Simulation sim, String[]... tokenGroups) {
        return tryReadOptionLabelFromTargetGraph(
            findSolverCandidateByTokenGroups(sim, tokenGroups),
            4,
            new String[] {
                "getCycleOption",
                "getCycleTypeOption",
                "getAmgCycleOption",
                "getAMGCycleOption"
            },
            getSolverMetricChildGetterNames()
        );
    }

    private String tryReadPressureAmgCycleLabel(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
        return tryReadOptionLabelFromTarget(
            amgLinearSolver,
            "getCycleOption",
            "getCycleTypeOption",
            "getAmgCycleOption",
            "getAMGCycleOption"
        );
    }

    private String tryReadVelocityAmgCycleLabel(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedVelocityAmgLinearSolver(sim);
        return tryReadOptionLabelFromTarget(
            amgLinearSolver,
            "getCycleOption",
            "getCycleTypeOption",
            "getAmgCycleOption",
            "getAMGCycleOption"
        );
    }

    private Double tryReadTkeAmgMaxCycles(Simulation sim) {
        return tryReadSolverAmgMaxCyclesByTokenGroups(
            sim,
            new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private Double tryReadSdrAmgMaxCycles(Simulation sim) {
        return tryReadSolverAmgMaxCyclesByTokenGroups(
            sim,
            new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private Double tryReadEnergyAmgMaxCycles(Simulation sim) {
        return tryReadSolverAmgMaxCyclesByTokenGroups(
            sim,
            new String[] {"ENERGY", "TEMPERATURE"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private Double tryReadTkeAmgConvergeTol(Simulation sim) {
        return tryReadSolverAmgConvergeTolByTokenGroups(
            sim,
            new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private Double tryReadSdrAmgConvergeTol(Simulation sim) {
        return tryReadSolverAmgConvergeTolByTokenGroups(
            sim,
            new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private Double tryReadEnergyAmgConvergeTol(Simulation sim) {
        return tryReadSolverAmgConvergeTolByTokenGroups(
            sim,
            new String[] {"ENERGY", "TEMPERATURE"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private String tryReadTkeAmgCycleLabel(Simulation sim) {
        return tryReadSolverAmgCycleLabelByTokenGroups(
            sim,
            new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private String tryReadSdrAmgCycleLabel(Simulation sim) {
        return tryReadSolverAmgCycleLabelByTokenGroups(
            sim,
            new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private String tryReadEnergyAmgCycleLabel(Simulation sim) {
        return tryReadSolverAmgCycleLabelByTokenGroups(
            sim,
            new String[] {"ENERGY", "TEMPERATURE"},
            new String[] {"AMG", "MULTIGRID"}
        );
    }

    private Integer mapPressureAmgCycleLabelToMode(String label) {
        String normalized = normalizeLabel(label);
        if (normalized.isEmpty()) {
            return null;
        }
        Integer numericSelection = tryMapPressureAmgCycleNumericSelection(normalized);
        if (numericSelection != null) {
            return numericSelection;
        }
        if (normalized.contains("WCYCLE")) {
            return Integer.valueOf(1);
        }
        if (normalized.contains("VCYCLE")) {
            return Integer.valueOf(0);
        }
        return null;
    }

    private Integer mapGenericAmgCycleLabelToMode(String label) {
        String normalized = normalizeLabel(label);
        if (normalized.isEmpty()) {
            return null;
        }
        Integer pressureMode = tryMapPressureAmgCycleNumericSelection(normalized);
        if (pressureMode != null) {
            return pressureMode;
        }
        Integer velocityMode = tryMapVelocityAmgCycleNumericSelection(normalized);
        if (velocityMode != null) {
            return velocityMode;
        }
        if (normalized.contains("VCYCLE")) {
            return Integer.valueOf(1);
        }
        if (normalized.contains("FCYCLE") || normalized.contains("FLEXCYCLE")) {
            return Integer.valueOf(0);
        }
        return null;
    }

    private Integer mapVelocityAmgCycleLabelToMode(String label) {
        String normalized = normalizeLabel(label);
        if (normalized.isEmpty()) {
            return null;
        }
        Integer numericSelection = tryMapVelocityAmgCycleNumericSelection(normalized);
        if (numericSelection != null) {
            return numericSelection;
        }
        if (normalized.contains("VCYCLE")) {
            return Integer.valueOf(1);
        }
        if (normalized.contains("FCYCLE") || normalized.contains("FLEXCYCLE")) {
            return Integer.valueOf(0);
        }
        return null;
    }

    private Integer tryMapPressureAmgCycleNumericSelection(String normalized) {
        Integer rawValue = tryParseIntegerLabel(normalized);
        if (rawValue == null) {
            return null;
        }
        // Some STAR-CCM+ builds expose enum ordinals instead of labels during readback.
        if (rawValue.intValue() == 2) {
            return Integer.valueOf(0);
        }
        if (rawValue.intValue() == 3) {
            return Integer.valueOf(1);
        }
        return null;
    }

    private Integer tryMapVelocityAmgCycleNumericSelection(String normalized) {
        Integer rawValue = tryParseIntegerLabel(normalized);
        if (rawValue == null) {
            return null;
        }
        // Observed STAR-CCM+ enum ordinals: 0=FLEX-CYCLE, 2=V-CYCLE.
        if (rawValue.intValue() == 0) {
            return Integer.valueOf(0);
        }
        if (rawValue.intValue() == 2) {
            return Integer.valueOf(1);
        }
        return null;
    }

    private Integer tryParseIntegerLabel(String normalized) {
        if (normalized == null || normalized.isEmpty()) {
            return null;
        }
        for (int idx = 0; idx < normalized.length(); idx += 1) {
            if (!Character.isDigit(normalized.charAt(idx))) {
                return null;
            }
        }
        try {
            return Integer.valueOf(Integer.parseInt(normalized));
        } catch (Exception ignored) {}
        return null;
    }

    private String summarizeAmgSolverState(Simulation sim) {
        LinkedHashSet<String> labels = new LinkedHashSet<String>();
        Boolean booleanState = null;
        for (Object candidate : collectSolverCandidates(sim)) {
            if (candidate == null) {
                continue;
            }
            if (booleanState == null) {
                booleanState = tryReadBooleanFromTarget(
                    candidate,
                    "isUseAlgebraicMultigrid",
                    "getUseAlgebraicMultigrid",
                    "isUseAlgebraicMultiGrid",
                    "getUseAlgebraicMultiGrid",
                    "isUseMultigrid",
                    "getUseMultigrid",
                    "isUseMultiGrid",
                    "getUseMultiGrid",
                    "isUseAmg",
                    "getUseAmg",
                    "isUseAMG",
                    "getUseAMG",
                    "isAmgEnabled",
                    "getAmgEnabled",
                    "isAMGEnabled",
                    "getAMGEnabled"
                );
            }
            String label = tryReadOptionLabelFromTarget(
                candidate,
                "getLinearSolverOption",
                "getLinearSolverTypeOption",
                "getLinearSolverMethodOption",
                "getSolverOption",
                "getSolverTypeOption",
                "getMatrixSolverOption",
                "getMatrixSolverTypeOption",
                "getPreconditionerOption",
                "getPreconditionerTypeOption",
                "getPreconditionerMethodOption",
                "getKrylovSolverOption",
                "getKrylovSolverTypeOption",
                "getMethodOption",
                "getAmgOption",
                "getAMGOption"
            );
            if (label != null) {
                String normalized = normalizeLabel(label);
                if (
                    normalized.contains("AMG")
                    || normalized.contains("MULTIGRID")
                    || normalized.contains("BICGSTAB")
                    || normalized.contains("BCGSTAB")
                    || normalized.contains("CGSTAB")
                    || normalized.contains("GMRES")
                ) {
                    labels.add(label.trim());
                }
            }
        }

        if (booleanState != null) {
            return (booleanState.booleanValue() ? "1" : "0")
                + " (boolean=" + booleanState + ")";
        }
        for (String label : labels) {
            String normalized = normalizeLabel(label);
            if (normalized.contains("AMG") || normalized.contains("MULTIGRID")) {
                return "1 (selected=" + label + ")";
            }
            if (
                normalized.contains("BICGSTAB")
                || normalized.contains("BCGSTAB")
                || normalized.contains("CGSTAB")
                || normalized.contains("GMRES")
            ) {
                return "0 (selected=" + label + ")";
            }
        }
        if (!labels.isEmpty()) {
            return "unknown (selected=" + labels.iterator().next() + ")";
        }
        return null;
    }

    private Double tryReadNumericFromTarget(Object target, String... getterNames) {
        if (target == null || getterNames == null) {
            return null;
        }
        for (String getterName : getterNames) {
            Object raw = tryInvokeNoArg(target, getterName);
            Double value = tryExtractNumericValue(raw, 4);
            if (value != null) {
                return value;
            }
        }
        return null;
    }

    private Double tryReadNumericFromTargetGraph(
            Object target,
            int depth,
            String[] getterNames,
            String[] childGetterNames) {
        if (target == null || depth < 0) {
            return null;
        }
        Double direct = tryReadNumericFromTarget(target, getterNames);
        if (direct != null) {
            return direct;
        }
        if (depth == 0 || childGetterNames == null) {
            return null;
        }
        for (String childGetterName : childGetterNames) {
            Object child = tryInvokeNoArg(target, childGetterName);
            if (child == null || child == target) {
                continue;
            }
            Double nested = tryReadNumericFromTargetGraph(
                child,
                depth - 1,
                getterNames,
                childGetterNames
            );
            if (nested != null) {
                return nested;
            }
        }
        return null;
    }

    private String[] getSolverMetricChildGetterNames() {
        return new String[] {
            "getSolver",
            "getLinearSolver",
            "getAMGLinearSolver",
            "getAmgLinearSolver",
            "getSolverStatistics",
            "getPerformanceMetrics",
            "getPerformanceMonitor",
            "getIterationMonitor",
            "getIterationCountMonitor",
            "getCycleMonitor",
            "getCycleCountMonitor",
            "getPressureSolver",
            "getPressureLinearSolver",
            "getPressureCorrectionSolver",
            "getVelocitySolver",
            "getMomentumSolver",
            "getLinearSolverManager",
            "getLinearSolverProperties",
            "getPreconditioner",
            "getCycleType",
            "getCycleOption",
            "getStatistics",
            "getStatus",
            "getMonitorData",
            "getDataSet",
            "getSamples",
            "getHistory",
            "getData",
            "getCurrentData",
            "getIterationData",
            "getPerformanceData",
            "getSolverData"
        };
    }

    private String[] getSolverIterationGetterNames() {
        return new String[] {
            "getLinearIterations",
            "getLinearIterationCount",
            "getInnerIterations",
            "getInnerIterationCount",
            "getSolverIterations",
            "getSolverIterationCount",
            "getKrylovIterations",
            "getKrylovIterationCount",
            "getIterations",
            "getIterationCount",
            "getNumIterations",
            "getNumberIterations",
            "getCurrentLinearIterations",
            "getCurrentLinearIterationCount",
            "getCurrentInnerIterations",
            "getCurrentInnerIterationCount",
            "getCurrentSolverIterations",
            "getCurrentSolverIterationCount",
            "getCurrentKrylovIterations",
            "getCurrentKrylovIterationCount",
            "getCurrentIterations",
            "getCurrentIterationCount",
            "getLastLinearIterations",
            "getLastLinearIterationCount",
            "getLastInnerIterations",
            "getLastInnerIterationCount",
            "getLastSolverIterations",
            "getLastSolverIterationCount",
            "getLastKrylovIterations",
            "getLastKrylovIterationCount",
            "getLastIterations",
            "getLastIterationCount"
        };
    }

    private String[] getAmgCycleGetterNames() {
        return new String[] {
            "getAMGCycles",
            "getAMGCycleCount",
            "getAmgCycles",
            "getMultigridCycles",
            "getMultigridCycleCount",
            "getCycles",
            "getCycleCount",
            "getNumCycles",
            "getNumberCycles",
            "getCurrentAMGCycles",
            "getCurrentAMGCycleCount",
            "getCurrentAmgCycles",
            "getCurrentMultigridCycles",
            "getCurrentMultigridCycleCount",
            "getCurrentCycles",
            "getCurrentCycleCount",
            "getCurrentNumCycles",
            "getLastAMGCycles",
            "getLastAMGCycleCount",
            "getLastAmgCycles",
            "getLastMultigridCycles",
            "getLastMultigridCycleCount",
            "getLastCycles",
            "getLastCycleCount",
            "getLastNumCycles"
        };
    }

    private String[] getTotalAmgCycleGetterNames() {
        return new String[] {
            "getTotalNumberCycles",
            "getTotalCycleCount",
            "getTotalCycles",
            "getTotalAMGCycles",
            "getTotalAMGCycleCount",
            "getTotalAmgCycles",
            "getCumulativeNumberCycles",
            "getCumulativeCycleCount",
            "getCumulativeCycles",
            "getCumulativeAMGCycles",
            "getCumulativeAMGCycleCount",
            "getCumulativeAmgCycles"
        };
    }

    private String[] getTotalSolveElapsedTimeGetterNames() {
        return new String[] {
            "getTotalSolveElapsedTime",
            "getTotalElapsedTime",
            "getCumulativeSolveElapsedTime",
            "getCumulativeElapsedTime",
            "getSolveElapsedTimeTotal",
            "getElapsedTimeTotal"
        };
    }

    private Double sanitizeFiniteMetricValue(Double value) {
        if (value == null) {
            return null;
        }
        double raw = value.doubleValue();
        if (Double.isNaN(raw) || Double.isInfinite(raw) || isInvalidReportValue(raw)) {
            return null;
        }
        return Double.valueOf(raw);
    }

    private Double computeCumulativeMetricDelta(String metricKey, Double cumulativeValue) {
        if (metricKey == null || metricKey.trim().isEmpty()) {
            return sanitizeFiniteMetricValue(cumulativeValue);
        }
        Double sanitized = sanitizeFiniteMetricValue(cumulativeValue);
        if (sanitized == null) {
            return null;
        }
        double current = sanitized.doubleValue();
        Double previous = solverProfilingPreviousCumulativeMetrics.get(metricKey);
        solverProfilingPreviousCumulativeMetrics.put(metricKey, Double.valueOf(current));
        if (previous == null) {
            return Double.valueOf(Math.max(0.0, current));
        }
        double delta = current - previous.doubleValue();
        if (delta < -1.0e-9) {
            return Double.valueOf(Math.max(0.0, current));
        }
        if (delta < 0.0) {
            delta = 0.0;
        }
        return Double.valueOf(delta);
    }

    private Double resolveSolverProfilingDelta(
            String metricKey,
            Double cumulativeValue,
            Double fallbackCurrentValue) {
        Double delta = computeCumulativeMetricDelta(metricKey, cumulativeValue);
        if (delta != null) {
            return delta;
        }
        return sanitizeFiniteMetricValue(fallbackCurrentValue);
    }

    private Double tryReadSolverIterationHeuristic(Object target) {
        return tryReadNumericByMethodPattern(
            target,
            5,
            new String[] {
                "LINEARITERATION",
                "INNERITERATION",
                "SOLVERITERATION",
                "KRYLOVITERATION",
                "ITERATIONCOUNT",
                "ITERATIONS",
                "ITERATION"
            },
            new String[] {
                "SOLVER",
                "LINEAR",
                "AMG",
                "STAT",
                "PERFORMANCE",
                "MONITOR",
                "DATA",
                "VALUE",
                "HISTORY",
                "COUNT",
                "ITERATION"
            },
            new String[] {
                "STARTITERATION",
                "ENDITERATION",
                "RELAX",
                "TOL",
                "EPSILON",
                "MAXCYCLE",
                "MAXLEVEL",
                "PRESWEEP",
                "POSTSWEEP",
                "TIMESTEP",
                "PHYSICALTIME"
            }
        );
    }

    private Double tryReadAmgCycleHeuristic(Object target) {
        return tryReadNumericByMethodPattern(
            target,
            5,
            new String[] {
                "AMGCYCLE",
                "MULTIGRIDCYCLE",
                "CYCLECOUNT",
                "CYCLES",
                "CYCLE"
            },
            new String[] {
                "SOLVER",
                "LINEAR",
                "AMG",
                "MULTIGRID",
                "STAT",
                "PERFORMANCE",
                "MONITOR",
                "DATA",
                "VALUE",
                "HISTORY",
                "CYCLE"
            },
            new String[] {
                "CYCLETYPE",
                "CYCLEOPTION",
                "MAXCYCLE",
                "RELAX",
                "TOL",
                "EPSILON",
                "PRESWEEP",
                "POSTSWEEP",
                "MAXLEVEL",
                "TIMESTEP",
                "PHYSICALTIME"
            }
        );
    }

    private Double tryReadMonitorValueByHeuristic(Object target) {
        return tryReadNumericByMethodPattern(
            target,
            4,
            new String[] {
                "REPORTMONITORVALUE",
                "MONITORVALUE",
                "CURRENTVALUE",
                "LATESTVALUE",
                "LASTVALUE",
                "VALUE",
                "YVALUE",
                "YVALUES",
                "SAMPLE",
                "SCALAR",
                "QUANTITY"
            },
            new String[] {
                "MONITOR",
                "VALUE",
                "DATA",
                "SET",
                "SAMPLE",
                "HISTORY",
                "SERIES",
                "CURVE",
                "SCALAR",
                "QUANTITY",
                "YVALUE"
            },
            new String[] {
                "CLASS",
                "UNIT",
                "DIMENSION",
                "PRESENTATIONNAME",
                "DISPLAYNAME",
                "NAME",
                "PLOTTITLE"
            }
        );
    }

    private Double tryReadMeshCellCountByHeuristic(Object target) {
        return tryReadNumericByMethodPattern(
            target,
            4,
            new String[] {
                "NUMBEROFCELLS",
                "CELLSCOUNT",
                "CELLCOUNT",
                "NUMCELLS"
            },
            new String[] {
                "MESH",
                "REGION",
                "PART",
                "CELL",
                "COUNT",
                "MANAGER",
                "DATA"
            },
            new String[] {
                "FACE",
                "EDGE",
                "VERTEX",
                "NODE",
                "QUALITY",
                "SIZE",
                "LAYER",
                "THICKNESS"
            }
        );
    }

    private Double tryReadNumericByMethodPattern(
            Object target,
            int depth,
            String[] preferredTokens,
            String[] traversalTokens,
            String[] excludedTokens) {
        return tryReadNumericByMethodPattern(
            target,
            depth,
            preferredTokens,
            traversalTokens,
            excludedTokens,
            new IdentityHashMap<Object, Boolean>()
        );
    }

    private Double tryReadNumericByMethodPattern(
            Object target,
            int depth,
            String[] preferredTokens,
            String[] traversalTokens,
            String[] excludedTokens,
            IdentityHashMap<Object, Boolean> visited) {
        if (target == null || depth < 0 || visited == null) {
            return null;
        }
        if (visited.containsKey(target)) {
            return null;
        }
        visited.put(target, Boolean.TRUE);

        for (Method method : target.getClass().getMethods()) {
            if (!isUsableNoArgGetter(method)) {
                continue;
            }
            String normalizedName = normalizeLabel(method.getName());
            if (containsAnyToken(normalizedName, excludedTokens)) {
                continue;
            }
            if (!containsAnyToken(normalizedName, preferredTokens)) {
                continue;
            }
            try {
                Object raw = method.invoke(target);
                Double numeric = tryExtractNumericValue(raw, 4);
                if (numeric != null && !isInvalidReportValue(numeric.doubleValue())) {
                    return numeric;
                }
                Double latestSample = tryExtractLatestNumericSample(raw, 4);
                if (latestSample != null && !isInvalidReportValue(latestSample.doubleValue())) {
                    return latestSample;
                }
            } catch (Exception ignored) {}
        }

        if (depth == 0) {
            return null;
        }

        for (Method method : target.getClass().getMethods()) {
            if (!isUsableNoArgGetter(method)) {
                continue;
            }
            String normalizedName = normalizeLabel(method.getName());
            if (containsAnyToken(normalizedName, excludedTokens)) {
                continue;
            }
            if (!containsAnyToken(normalizedName, traversalTokens)) {
                continue;
            }
            try {
                Object child = method.invoke(target);
                if (child == null || child == target) {
                    continue;
                }
                Double nested = tryReadNumericByMethodPattern(
                    child,
                    depth - 1,
                    preferredTokens,
                    traversalTokens,
                    excludedTokens,
                    visited
                );
                if (nested != null) {
                    return nested;
                }
            } catch (Exception ignored) {}
        }
        return null;
    }

    private boolean isUsableNoArgGetter(Method method) {
        if (method == null || method.getParameterCount() != 0) {
            return false;
        }
        String name = method.getName();
        if (name == null) {
            return false;
        }
        if (name.equals("getClass")) {
            return false;
        }
        if (!(name.startsWith("get") || name.startsWith("is"))) {
            return false;
        }
        return method.getReturnType() != Void.TYPE;
    }

    private boolean containsAnyToken(String text, String[] tokens) {
        String normalized = normalizeLabel(text);
        if (normalized.isEmpty() || tokens == null || tokens.length == 0) {
            return false;
        }
        for (String token : tokens) {
            String normalizedToken = normalizeLabel(token);
            if (!normalizedToken.isEmpty() && normalized.contains(normalizedToken)) {
                return true;
            }
        }
        return false;
    }

    private Double tryExtractNumericValue(Object raw, int depth) {
        if (raw == null || depth < 0) {
            return null;
        }
        if (raw instanceof Number) {
            return Double.valueOf(((Number) raw).doubleValue());
        }
        Object nested = tryInvokeNoArg(
            raw,
            "getRawValue",
            "getSIValue",
            "getDoubleValue",
            "getNumber",
            "getValue"
        );
        if (nested != null && nested != raw) {
            Double nestedValue = tryExtractNumericValue(nested, depth - 1);
            if (nestedValue != null) {
                return nestedValue;
            }
        }
        Object quantity = tryInvokeNoArg(
            raw,
            "getQuantity",
            "getValueQuantity",
            "getQuantityValue",
            "getScalar",
            "getDefinition"
        );
        if (quantity != null && quantity != raw) {
            return tryExtractNumericValue(quantity, depth - 1);
        }
        return null;
    }

    private Boolean tryReadBooleanFromTarget(Object target, String... getterNames) {
        if (target == null || getterNames == null) {
            return null;
        }
        for (String getterName : getterNames) {
            Object raw = tryInvokeNoArg(target, getterName);
            if (raw instanceof Boolean) {
                return ((Boolean) raw);
            }
        }
        return null;
    }

    private String tryReadOptionLabelFromTarget(Object target, String... getterNames) {
        if (target == null || getterNames == null) {
            return null;
        }
        for (String getterName : getterNames) {
            Object option = tryInvokeNoArg(target, getterName);
            String label = describeSelectedOption(option, 4);
            if (label != null && !label.isEmpty()) {
                return label;
            }
        }
        return null;
    }

    private String tryReadOptionLabelFromTargetGraph(
            Object target,
            int depth,
            String[] getterNames,
            String[] childGetterNames) {
        if (target == null || depth < 0) {
            return null;
        }
        String direct = tryReadOptionLabelFromTarget(target, getterNames);
        if (direct != null && !direct.isEmpty()) {
            return direct;
        }
        if (depth == 0 || childGetterNames == null) {
            return null;
        }
        for (String childGetterName : childGetterNames) {
            Object child = tryInvokeNoArg(target, childGetterName);
            if (child == null || child == target) {
                continue;
            }
            String nested = tryReadOptionLabelFromTargetGraph(
                child,
                depth - 1,
                getterNames,
                childGetterNames
            );
            if (nested != null && !nested.isEmpty()) {
                return nested;
            }
        }
        return null;
    }

    private String describeSelectedOption(Object option, int depth) {
        if (option == null || depth < 0) {
            return null;
        }
        if (option instanceof Enum || option instanceof CharSequence) {
            return option.toString();
        }
        Object selected = tryInvokeNoArg(
            option,
            "getSelected",
            "getSelectedElement",
            "getSelectedInput",
            "getValue",
            "getOptionInput"
        );
        if (selected != null && selected != option) {
            String selectedLabel = describeSelectedOption(selected, depth - 1);
            if (selectedLabel != null && !selectedLabel.isEmpty()) {
                return selectedLabel;
            }
        }
        Object named = tryInvokeNoArg(
            option,
            "getPresentationName",
            "getDisplayName",
            "getName"
        );
        if (named instanceof CharSequence) {
            return named.toString();
        }
        String text = option.toString();
        if (text == null || text.trim().isEmpty()) {
            return null;
        }
        String trimmed = text.trim();
        if (trimmed.contains("@") && trimmed.contains(".")) {
            return null;
        }
        return trimmed;
    }

    private Object tryInvokeNoArg(Object target, String... methodNames) {
        if (target == null) return null;
        for (String methodName : methodNames) {
            try {
                return target.getClass().getMethod(methodName).invoke(target);
            } catch (Exception ignored) {}
        }
        return null;
    }

    private Object tryInvokeWithClassArg(Object target, String methodName, Class<?> arg) {
        if (target == null || methodName == null || methodName.isEmpty() || arg == null) {
            return null;
        }
        try {
            return target.getClass()
                .getMethod(methodName, Class.class)
                .invoke(target, arg);
        } catch (Exception ignored) {}
        return null;
    }

    private boolean invokeDoubleSetter(Object target, double value, String... methodNames) {
        if (target == null) return false;
        for (String methodName : methodNames) {
            for (Method method : target.getClass().getMethods()) {
                if (!method.getName().equals(methodName) || method.getParameterCount() != 1) {
                    continue;
                }
                Class<?> paramType = method.getParameterTypes()[0];
                if (
                    paramType != double.class
                    && paramType != Double.class
                    && !Number.class.isAssignableFrom(paramType)
                ) {
                    continue;
                }
                try {
                    method.invoke(target, Double.valueOf(value));
                    return true;
                } catch (Exception ignored) {}
            }
        }
        return false;
    }

    private boolean invokeDoubleUnitsSetter(
            Object target,
            double value,
            Object units,
            String... methodNames) {
        if (target == null || units == null) {
            return false;
        }
        for (String methodName : methodNames) {
            for (Method method : target.getClass().getMethods()) {
                if (!method.getName().equals(methodName) || method.getParameterCount() != 2) {
                    continue;
                }
                Class<?>[] paramTypes = method.getParameterTypes();
                Class<?> valueType = paramTypes[0];
                Class<?> unitsType = paramTypes[1];
                if (
                    valueType != double.class
                    && valueType != Double.class
                    && !Number.class.isAssignableFrom(valueType)
                ) {
                    continue;
                }
                if (
                    unitsType.isPrimitive()
                    || (!unitsType.isInstance(units) && unitsType != Object.class)
                ) {
                    continue;
                }
                try {
                    method.invoke(target, Double.valueOf(value), units);
                    return true;
                } catch (Exception ignored) {}
            }
        }
        return false;
    }

    private boolean invokeIntegerSetter(Object target, int value, String... methodNames) {
        if (target == null) return false;
        for (String methodName : methodNames) {
            for (Method method : target.getClass().getMethods()) {
                if (!method.getName().equals(methodName) || method.getParameterCount() != 1) {
                    continue;
                }
                Class<?> paramType = method.getParameterTypes()[0];
                try {
                    if (paramType == int.class || paramType == Integer.class) {
                        method.invoke(target, Integer.valueOf(value));
                        return true;
                    }
                    if (paramType == double.class || paramType == Double.class) {
                        method.invoke(target, Double.valueOf((double) value));
                        return true;
                    }
                    if (Number.class.isAssignableFrom(paramType)) {
                        method.invoke(target, Integer.valueOf(value));
                        return true;
                    }
                } catch (Exception ignored) {}
            }
        }
        return false;
    }

    private boolean trySetNumericValueOnGetter(
            Object target,
            double value,
            String... getterNames) {
        if (target == null) {
            return false;
        }
        for (String getterName : getterNames) {
            Object nested = tryInvokeNoArg(target, getterName);
            if (nested == null || nested == target) {
                continue;
            }
            if (invokeDoubleSetter(nested, value, "setValue", "setQuantityValue", "setInitialRampValue")) {
                return true;
            }
            if (invokeIntegerSetter(nested, (int) Math.round(value), "setValue", "setIteration", "setStep")) {
                return true;
            }
            Object quantity = tryInvokeNoArg(nested, "getQuantity", "getValue", "getScalar", "getDefinition");
            if (quantity != null && quantity != nested) {
                if (invokeDoubleSetter(quantity, value, "setValue", "setQuantityValue")) {
                    return true;
                }
                if (invokeIntegerSetter(quantity, (int) Math.round(value), "setValue", "setIteration", "setStep")) {
                    return true;
                }
            }
        }
        return false;
    }

    private boolean invokeBooleanSetter(Object target, boolean value, String... methodNames) {
        if (target == null) return false;
        for (String methodName : methodNames) {
            for (Method method : target.getClass().getMethods()) {
                if (!method.getName().equals(methodName) || method.getParameterCount() != 1) {
                    continue;
                }
                Class<?> paramType = method.getParameterTypes()[0];
                if (paramType != boolean.class && paramType != Boolean.class) {
                    continue;
                }
                try {
                    method.invoke(target, Boolean.valueOf(value));
                    return true;
                } catch (Exception ignored) {}
            }
        }
        return false;
    }

    private boolean invokeBooleanSetterByKeyword(
            Object target,
            boolean value,
            String... keywords) {
        if (target == null || keywords == null || keywords.length == 0) {
            return false;
        }
        for (Method method : target.getClass().getMethods()) {
            if (method.getParameterCount() != 1) {
                continue;
            }
            if (!method.getName().startsWith("set")) {
                continue;
            }
            Class<?> paramType = method.getParameterTypes()[0];
            if (paramType != boolean.class && paramType != Boolean.class) {
                continue;
            }
            String methodNameUpper = method.getName().toUpperCase(Locale.ROOT);
            boolean matched = false;
            for (String keyword : keywords) {
                if (keyword == null || keyword.isEmpty()) continue;
                if (methodNameUpper.contains(keyword.toUpperCase(Locale.ROOT))) {
                    matched = true;
                    break;
                }
            }
            if (!matched) {
                continue;
            }
            try {
                method.invoke(target, Boolean.valueOf(value));
                return true;
            } catch (Exception ignored) {}
        }
        return false;
    }

    private boolean trySelectOptionOnGetter(
            Object target,
            String[] desiredTokens,
            String... getterNames) {
        if (target == null) return false;
        for (String getterName : getterNames) {
            try {
                Object option = target.getClass().getMethod(getterName).invoke(target);
                if (tryConfigureOption(option, desiredTokens)) {
                    return true;
                }
            } catch (Exception ignored) {}
        }
        return false;
    }

    private boolean tryConfigureOption(Object option, String... desiredTokens) {
        if (option == null || desiredTokens == null || desiredTokens.length == 0) {
            return false;
        }
        if (trySetSelectedEnumByTokens(option, desiredTokens)) {
            return true;
        }
        ArrayList<String> normalizedTokens = normalizeTokens(desiredTokens);
        if (normalizedTokens.isEmpty()) {
            return false;
        }
        if (tryConfigureNestedOptionInput(option, normalizedTokens)) {
            return true;
        }
        if (trySetChoiceObject(option, normalizedTokens)) {
            return true;
        }
        if (trySetStringLikeOption(option, normalizedTokens)) {
            return true;
        }
        return false;
    }

    private ArrayList<String> normalizeTokens(String... desiredTokens) {
        ArrayList<String> normalizedTokens = new ArrayList<String>();
        if (desiredTokens == null) {
            return normalizedTokens;
        }
        for (String token : desiredTokens) {
            if (token == null || token.isEmpty()) continue;
            String upper = normalizeLabel(token);
            if (!upper.isEmpty() && !normalizedTokens.contains(upper)) {
                normalizedTokens.add(upper);
            }
        }
        return normalizedTokens;
    }

    private boolean trySetSelectedEnumByTokens(Object option, String... desiredTokens) {
        if (option == null || desiredTokens == null || desiredTokens.length == 0) {
            return false;
        }
        ArrayList<String> normalizedTokens = normalizeTokens(desiredTokens);
        if (normalizedTokens.isEmpty()) {
            return false;
        }
        for (Method method : option.getClass().getMethods()) {
            if (!method.getName().equals("setSelected") || method.getParameterCount() != 1) {
                continue;
            }

            Class<?> paramType = method.getParameterTypes()[0];
            if (!paramType.isEnum()) {
                continue;
            }

            Object[] constants = paramType.getEnumConstants();
            if (constants == null) {
                continue;
            }

            for (Object constant : constants) {
                String label = normalizeLabel(constant.toString());
                for (String desiredUpper : normalizedTokens) {
                    if (label.contains(desiredUpper)) {
                        try {
                            method.invoke(option, constant);
                            return true;
                        } catch (Exception ignored) {}
                    }
                }
            }
        }
        return false;
    }

    private boolean tryConfigureNestedOptionInput(Object option, ArrayList<String> desiredTokens) {
        String[] getterNames = new String[] {
            "getSelectedElement",
            "getOptionInput",
            "getSelectedInput",
            "getEnumeratedOptionInput",
            "getInput"
        };
        for (String getterName : getterNames) {
            Object nested = tryInvokeNoArg(option, getterName);
            if (nested == null || nested == option) {
                continue;
            }
            if (trySetChoiceObject(nested, desiredTokens)) {
                return true;
            }
            if (trySetStringLikeOption(nested, desiredTokens)) {
                return true;
            }
        }
        return false;
    }

    private boolean trySetChoiceObject(Object option, ArrayList<String> desiredTokens) {
        for (Method getter : option.getClass().getMethods()) {
            if (getter.getParameterCount() != 0) {
                continue;
            }
            String getterName = getter.getName();
            if (!getterName.startsWith("get")) {
                continue;
            }
            if (
                !getterName.contains("Option")
                && !getterName.contains("Value")
                && !getterName.contains("Choice")
                && !getterName.contains("Allowed")
                && !getterName.contains("Available")
                && !getterName.contains("Enum")
            ) {
                continue;
            }
            Object rawChoices = null;
            try {
                rawChoices = getter.invoke(option);
            } catch (Exception ignored) {}
            if (rawChoices == null) {
                continue;
            }

            ArrayList<Object> choices = collectChoiceObjects(rawChoices);
            if (choices.isEmpty()) {
                continue;
            }

            for (int idx = 0; idx < choices.size(); idx++) {
                Object choice = choices.get(idx);
                if (!matchesAnyToken(choice.toString(), desiredTokens)) {
                    continue;
                }
                if (tryInvokeChoiceSetter(option, choice, idx)) {
                    return true;
                }
            }
        }
        return false;
    }

    private ArrayList<Object> collectChoiceObjects(Object rawChoices) {
        ArrayList<Object> choices = new ArrayList<Object>();
        if (rawChoices == null) {
            return choices;
        }
        if (rawChoices instanceof Iterable) {
            for (Object item : (Iterable<?>) rawChoices) {
                if (item != null) choices.add(item);
            }
            return choices;
        }
        if (rawChoices.getClass().isArray()) {
            int len = java.lang.reflect.Array.getLength(rawChoices);
            for (int idx = 0; idx < len; idx++) {
                Object item = java.lang.reflect.Array.get(rawChoices, idx);
                if (item != null) choices.add(item);
            }
            return choices;
        }

        Object nested = tryInvokeNoArg(
            rawChoices,
            "toArray",
            "getObjects",
            "getValues",
            "getOptions",
            "getAllowedValues",
            "getAvailableValues",
            "values"
        );
        if (nested != null && nested != rawChoices) {
            return collectChoiceObjects(nested);
        }

        try {
            Method sizeMethod = rawChoices.getClass().getMethod("size");
            Method getMethod = rawChoices.getClass().getMethod("get", int.class);
            Object sizeObj = sizeMethod.invoke(rawChoices);
            int size = ((Number) sizeObj).intValue();
            for (int idx = 0; idx < size; idx++) {
                Object item = getMethod.invoke(rawChoices, Integer.valueOf(idx));
                if (item != null) choices.add(item);
            }
            if (!choices.isEmpty()) {
                return choices;
            }
        } catch (Exception ignored) {}

        return choices;
    }

    private boolean tryInvokeChoiceSetter(Object option, Object choice, int index) {
        String[] setterNames = new String[] {
            "setSelected",
            "setValue",
            "setOption",
            "setChoice",
            "select",
            "choose",
            "setInputValue"
        };
        for (Method method : option.getClass().getMethods()) {
            if (method.getParameterCount() != 1) {
                continue;
            }
            boolean matchedName = false;
            for (String setterName : setterNames) {
                if (method.getName().equals(setterName)) {
                    matchedName = true;
                    break;
                }
            }
            if (!matchedName) {
                continue;
            }
            Class<?> paramType = method.getParameterTypes()[0];
            try {
                if (choice != null && (paramType.isInstance(choice) || paramType == Object.class)) {
                    method.invoke(option, choice);
                    return true;
                }
                if (
                    (paramType == int.class || paramType == Integer.class)
                    && index >= 0
                ) {
                    method.invoke(option, Integer.valueOf(index));
                    return true;
                }
                if (paramType == String.class || CharSequence.class.isAssignableFrom(paramType)) {
                    method.invoke(option, choice.toString());
                    return true;
                }
                if (paramType.isEnum()) {
                    Object[] constants = paramType.getEnumConstants();
                    if (constants == null) continue;
                    String upperChoice = normalizeLabel(choice.toString());
                    for (Object constant : constants) {
                        if (normalizeLabel(constant.toString()).contains(upperChoice)) {
                            method.invoke(option, constant);
                            return true;
                        }
                    }
                }
            } catch (Exception ignored) {}
        }
        return false;
    }

    private boolean trySetStringLikeOption(Object option, ArrayList<String> desiredTokens) {
        String[] setterNames = new String[] {
            "setSelected",
            "setValue",
            "setOption",
            "setChoice",
            "select",
            "choose",
            "setInputValue"
        };
        for (Method method : option.getClass().getMethods()) {
            if (method.getParameterCount() != 1) {
                continue;
            }
            boolean matchedName = false;
            for (String setterName : setterNames) {
                if (method.getName().equals(setterName)) {
                    matchedName = true;
                    break;
                }
            }
            if (!matchedName) {
                continue;
            }
            Class<?> paramType = method.getParameterTypes()[0];
            for (String token : desiredTokens) {
                try {
                    if (paramType == String.class || CharSequence.class.isAssignableFrom(paramType)) {
                        method.invoke(option, token);
                        return true;
                    }
                    if (paramType == Object.class) {
                        method.invoke(option, token);
                        return true;
                    }
                } catch (Exception ignored) {}
            }
        }
        return false;
    }

    private boolean matchesAnyToken(String label, ArrayList<String> desiredTokens) {
        if (label == null || desiredTokens == null || desiredTokens.isEmpty()) {
            return false;
        }
        String upper = normalizeLabel(label);
        for (String token : desiredTokens) {
            if (upper.contains(token)) {
                return true;
            }
        }
        return false;
    }

    private String normalizeLabel(String text) {
        if (text == null) {
            return "";
        }
        return text
            .toUpperCase(Locale.ROOT)
            .replaceAll("[^A-Z0-9]+", "");
    }

    private PrintWriter openSolverProfilingWriter(Simulation sim) {
        String csvPath = resolvePath(OUTPUT_DIR + "/profiling/" + SOLVER_PROFILING_CSV_NAME);
        try {
            long writeStartNs = System.nanoTime();
            PrintWriter pw = new PrintWriter(new BufferedWriter(new FileWriter(csvPath)));
            pw.println(
                "phase,case_id,case_name,starccm_version,solver_type,physics_models,mesh_cells,time_step,"
                + "iteration,chunk_size,physical_time,chunk_wall_time_s,cumulative_wall_time_s,"
                + "drag,total_force,train_surface_pressure,inlet_mass_flow,outlet_mass_flow,"
                + "mass_imbalance_abs,mass_imbalance_relative,max_cfl,mean_cfl,max_residual,"
                + "continuity_residual,x_momentum_residual,y_momentum_residual,z_momentum_residual,"
                + "tke_residual,sdr_residual,energy_residual,"
                + "pressure_final_residual,x_momentum_final_residual,y_momentum_final_residual,z_momentum_final_residual,"
                + "tke_final_residual,sdr_final_residual,energy_final_residual,"
                + "pressure_current_urf,velocity_current_urf,tke_current_urf,sdr_current_urf,energy_current_urf,"
                + "pressure_relaxation_initial_value,pressure_relaxation_start_iteration,pressure_relaxation_end_iteration,"
                + "velocity_relaxation_initial_value,velocity_relaxation_start_iteration,velocity_relaxation_end_iteration,"
                + "pressure_relaxation_scheme,velocity_relaxation_scheme,"
                + "pressure_current_cycle_mode,pressure_current_cycle_label,velocity_current_cycle_mode,velocity_current_cycle_label,"
                + "tke_current_cycle_mode,tke_current_cycle_label,sdr_current_cycle_mode,sdr_current_cycle_label,energy_current_cycle_mode,energy_current_cycle_label,"
                + "pressure_current_tolerance,velocity_current_tolerance,tke_current_tolerance,sdr_current_tolerance,energy_current_tolerance,"
                + "pressure_current_max_cycles,velocity_current_max_cycles,tke_current_max_cycles,sdr_current_max_cycles,energy_current_max_cycles,"
                + "pressure_amg_epsilon,pressure_amg_solver_state,"
                + "pressure_solver_iterations,velocity_solver_iterations,tke_solver_iterations,sdr_solver_iterations,energy_solver_iterations,"
                + "pressure_amg_cycles,velocity_amg_cycles,tke_amg_cycles,sdr_amg_cycles,energy_amg_cycles,"
                + "pressure_equation_time_s,velocity_equation_time_s,tke_equation_time_s,sdr_equation_time_s,energy_equation_time_s,"
                + "pressure_hit_max_cycles,velocity_hit_max_cycles,tke_hit_max_cycles,sdr_hit_max_cycles,energy_hit_max_cycles"
            );
            pw.flush();
            solverProfilingCsvWriteTimeS += (System.nanoTime() - writeStartNs) / 1.0e9;
            return pw;
        } catch (IOException e) {
            sim.println("WARNING: Unable to open solver profiling CSV: " + e.getMessage());
            return null;
        }
    }

    private void writeSolverProfilingRow(
            Simulation sim,
            PrintWriter pw,
            String phase,
            int iteration,
            int chunkSize,
            double chunkWallTimeS,
            double cumulativeWallTimeS) {
        if (pw == null) {
            return;
        }

        Double dragVal = tryGetReportValue(sim, DRAG_REPORT_NAME);
        Double totalVal = tryGetReportValue(sim, TOTAL_REPORT_NAME);
        Double pressureVal = tryGetReportValue(sim, TRAIN_SURFACE_PRESSURE_REPORT_NAME);
        Double maxResidualVal = null;
        try {
            double rawMaxResidual = getMaxResidualValue(sim);
            if (!isInvalidReportValue(rawMaxResidual)) {
                maxResidualVal = Double.valueOf(rawMaxResidual);
            }
        } catch (Exception ignored) {}
        Double continuityResidual = tryReadResidualMonitorValue(
            sim,
            new String[] {"CONTINUITY"},
            new String[] {"RESIDUAL"}
        );
        Double xMomentumResidual = tryReadResidualMonitorValue(
            sim,
            new String[] {"XMOMENTUM"},
            new String[] {"RESIDUAL"}
        );
        Double yMomentumResidual = tryReadResidualMonitorValue(
            sim,
            new String[] {"YMOMENTUM"},
            new String[] {"RESIDUAL"}
        );
        Double zMomentumResidual = tryReadResidualMonitorValue(
            sim,
            new String[] {"ZMOMENTUM"},
            new String[] {"RESIDUAL"}
        );
        Double tkeResidual = tryReadResidualMonitorValue(
            sim,
            new String[] {"TKE", "TURBULENTKINETIC"},
            new String[] {"RESIDUAL"}
        );
        Double sdrResidual = tryReadResidualMonitorValue(
            sim,
            new String[] {"SDR", "SPECIFICDISSIPATION"},
            new String[] {"RESIDUAL"}
        );
        Double energyResidual = tryReadResidualMonitorValue(
            sim,
            new String[] {"ENERGY", "TEMPERATURE"},
            new String[] {"RESIDUAL"}
        );

        Double pressureCurrentUrf = tryReadPressureRelaxationFactor(sim);
        Double velocityCurrentUrf = tryReadVelocityRelaxationFactor(sim);
        Double tkeCurrentUrf = tryReadSolverUrfByTokenGroups(
            sim,
            new String[] {"TKE", "TURBULENTKINETIC"}
        );
        Double sdrCurrentUrf = tryReadSolverUrfByTokenGroups(
            sim,
            new String[] {"SDR", "SPECIFICDISSIPATION"}
        );
        Double energyCurrentUrf = tryReadSolverUrfByTokenGroups(
            sim,
            new String[] {"TEMPERATURE", "ENERGY"}
        );

        Double pressureRelaxInitial = tryReadPressureRelaxationInitialValue(sim);
        Double pressureRelaxStart = tryReadPressureRelaxationStartIteration(sim);
        Double pressureRelaxEnd = tryReadPressureRelaxationEndIteration(sim);
        Double velocityRelaxInitial = tryReadVelocityRelaxationInitialValue(sim);
        Double velocityRelaxStart = tryReadVelocityRelaxationStartIteration(sim);
        Double velocityRelaxEnd = tryReadVelocityRelaxationEndIteration(sim);
        String pressureRelaxationScheme = deriveRelaxationScheme(
            pressureRelaxInitial,
            pressureRelaxStart,
            pressureRelaxEnd,
            pressureCurrentUrf
        );
        String velocityRelaxationScheme = deriveRelaxationScheme(
            velocityRelaxInitial,
            velocityRelaxStart,
            velocityRelaxEnd,
            velocityCurrentUrf
        );

        String pressureCycleLabel = tryReadPressureAmgCycleLabel(sim);
        String velocityCycleLabel = tryReadVelocityAmgCycleLabel(sim);
        String tkeCycleLabel = tryReadTkeAmgCycleLabel(sim);
        String sdrCycleLabel = tryReadSdrAmgCycleLabel(sim);
        String energyCycleLabel = tryReadEnergyAmgCycleLabel(sim);
        Integer pressureCycleMode = mapPressureAmgCycleLabelToMode(pressureCycleLabel);
        Integer velocityCycleMode = mapVelocityAmgCycleLabelToMode(velocityCycleLabel);
        Integer tkeCycleMode = mapGenericAmgCycleLabelToMode(tkeCycleLabel);
        Integer sdrCycleMode = mapGenericAmgCycleLabelToMode(sdrCycleLabel);
        Integer energyCycleMode = mapGenericAmgCycleLabelToMode(energyCycleLabel);
        Double pressureCurrentTol = tryReadPressureAmgConvergeTol(sim);
        Double velocityCurrentTol = tryReadVelocityAmgConvergeTol(sim);
        Double tkeCurrentTol = tryReadTkeAmgConvergeTol(sim);
        Double sdrCurrentTol = tryReadSdrAmgConvergeTol(sim);
        Double energyCurrentTol = tryReadEnergyAmgConvergeTol(sim);
        Double pressureCurrentMaxCycles = tryReadPressureAmgMaxCycles(sim);
        Double velocityCurrentMaxCycles = tryReadVelocityAmgMaxCycles(sim);
        Double tkeCurrentMaxCycles = tryReadTkeAmgMaxCycles(sim);
        Double sdrCurrentMaxCycles = tryReadSdrAmgMaxCycles(sim);
        Double energyCurrentMaxCycles = tryReadEnergyAmgMaxCycles(sim);
        Double pressureAmgEpsilon = tryReadPressureAmgEpsilon(sim);
        String pressureAmgSolverState = summarizeAmgSolverState(sim);

        Double pressureSolverIterations = tryReadPressureSolverIterations(sim);
        Double velocitySolverIterations = tryReadVelocitySolverIterations(sim);
        Double tkeSolverIterations = tryReadTkeSolverIterations(sim);
        Double sdrSolverIterations = tryReadSdrSolverIterations(sim);
        Double energySolverIterations = tryReadEnergySolverIterations(sim);
        Double pressureAmgCycles = resolveSolverProfilingDelta(
            "pressure_amg_cycles_total",
            tryReadPressureTotalAmgCycles(sim),
            tryReadPressureAmgCycles(sim)
        );
        Double velocityAmgCycles = resolveSolverProfilingDelta(
            "velocity_amg_cycles_total",
            tryReadVelocityTotalAmgCycles(sim),
            tryReadVelocityAmgCycles(sim)
        );
        Double tkeAmgCycles = resolveSolverProfilingDelta(
            "tke_amg_cycles_total",
            tryReadTkeTotalAmgCycles(sim),
            tryReadTkeAmgCycles(sim)
        );
        Double sdrAmgCycles = resolveSolverProfilingDelta(
            "sdr_amg_cycles_total",
            tryReadSdrTotalAmgCycles(sim),
            tryReadSdrAmgCycles(sim)
        );
        Double energyAmgCycles = resolveSolverProfilingDelta(
            "energy_amg_cycles_total",
            tryReadEnergyTotalAmgCycles(sim),
            tryReadEnergyAmgCycles(sim)
        );
        Double pressureEquationTimeS = computeCumulativeMetricDelta(
            "pressure_equation_time_s_total",
            tryReadPressureTotalSolveElapsedTime(sim)
        );
        Double velocityEquationTimeS = computeCumulativeMetricDelta(
            "velocity_equation_time_s_total",
            tryReadVelocityTotalSolveElapsedTime(sim)
        );
        Double tkeEquationTimeS = computeCumulativeMetricDelta(
            "tke_equation_time_s_total",
            tryReadTkeTotalSolveElapsedTime(sim)
        );
        Double sdrEquationTimeS = computeCumulativeMetricDelta(
            "sdr_equation_time_s_total",
            tryReadSdrTotalSolveElapsedTime(sim)
        );
        Double energyEquationTimeS = computeCumulativeMetricDelta(
            "energy_equation_time_s_total",
            tryReadEnergyTotalSolveElapsedTime(sim)
        );
        Double inletMassFlow = tryGetReportValue(sim, INLET_MASS_FLOW_REPORT_NAME);
        Double outletMassFlow = tryGetReportValue(sim, OUTLET_MASS_FLOW_REPORT_NAME);
        Double massImbalanceAbs = computeMassImbalanceAbs(inletMassFlow, outletMassFlow);
        Double massImbalanceRelative = computeMassImbalanceRelative(
            inletMassFlow,
            outletMassFlow
        );
        Double maxCfl = tryGetConfiguredFieldReportValue(sim, CFL_MAX_REPORT_NAME);
        Double meanCfl = tryGetConfiguredFieldReportValue(sim, CFL_MEAN_REPORT_NAME);

        StringBuilder row = new StringBuilder();
        appendCsvField(row, phase);
        appendCsvField(row, CASE_NAME);
        appendCsvField(row, CASE_NAME);
        appendCsvField(row, tryReadStarCcmVersion(sim));
        appendCsvField(row, detectSolverTypeLabel(sim));
        appendCsvField(row, detectPhysicsModelsLabel());
        appendCsvField(row, tryReadMeshCellCount(sim));
        appendCsvField(row, Double.valueOf(TIME_STEP));
        appendCsvField(row, Integer.valueOf(iteration));
        appendCsvField(row, Integer.valueOf(chunkSize));
        appendCsvField(row, tryReadPhysicalTime(sim, iteration));
        appendCsvField(row, Double.valueOf(chunkWallTimeS));
        appendCsvField(row, Double.valueOf(cumulativeWallTimeS));
        appendCsvField(row, dragVal);
        appendCsvField(row, totalVal);
        appendCsvField(row, pressureVal);
        appendCsvField(row, inletMassFlow);
        appendCsvField(row, outletMassFlow);
        appendCsvField(row, massImbalanceAbs);
        appendCsvField(row, massImbalanceRelative);
        appendCsvField(row, maxCfl);
        appendCsvField(row, meanCfl);
        appendCsvField(row, maxResidualVal);
        appendCsvField(row, continuityResidual);
        appendCsvField(row, xMomentumResidual);
        appendCsvField(row, yMomentumResidual);
        appendCsvField(row, zMomentumResidual);
        appendCsvField(row, tkeResidual);
        appendCsvField(row, sdrResidual);
        appendCsvField(row, energyResidual);
        appendCsvField(row, continuityResidual);
        appendCsvField(row, xMomentumResidual);
        appendCsvField(row, yMomentumResidual);
        appendCsvField(row, zMomentumResidual);
        appendCsvField(row, tkeResidual);
        appendCsvField(row, sdrResidual);
        appendCsvField(row, energyResidual);
        appendCsvField(row, pressureCurrentUrf);
        appendCsvField(row, velocityCurrentUrf);
        appendCsvField(row, tkeCurrentUrf);
        appendCsvField(row, sdrCurrentUrf);
        appendCsvField(row, energyCurrentUrf);
        appendCsvField(row, pressureRelaxInitial);
        appendCsvField(row, pressureRelaxStart);
        appendCsvField(row, pressureRelaxEnd);
        appendCsvField(row, velocityRelaxInitial);
        appendCsvField(row, velocityRelaxStart);
        appendCsvField(row, velocityRelaxEnd);
        appendCsvField(row, pressureRelaxationScheme);
        appendCsvField(row, velocityRelaxationScheme);
        appendCsvField(row, pressureCycleMode);
        appendCsvField(row, pressureCycleLabel);
        appendCsvField(row, velocityCycleMode);
        appendCsvField(row, velocityCycleLabel);
        appendCsvField(row, tkeCycleMode);
        appendCsvField(row, tkeCycleLabel);
        appendCsvField(row, sdrCycleMode);
        appendCsvField(row, sdrCycleLabel);
        appendCsvField(row, energyCycleMode);
        appendCsvField(row, energyCycleLabel);
        appendCsvField(row, pressureCurrentTol);
        appendCsvField(row, velocityCurrentTol);
        appendCsvField(row, tkeCurrentTol);
        appendCsvField(row, sdrCurrentTol);
        appendCsvField(row, energyCurrentTol);
        appendCsvField(row, pressureCurrentMaxCycles);
        appendCsvField(row, velocityCurrentMaxCycles);
        appendCsvField(row, tkeCurrentMaxCycles);
        appendCsvField(row, sdrCurrentMaxCycles);
        appendCsvField(row, energyCurrentMaxCycles);
        appendCsvField(row, pressureAmgEpsilon);
        appendCsvField(row, pressureAmgSolverState);
        appendCsvField(row, pressureSolverIterations);
        appendCsvField(row, velocitySolverIterations);
        appendCsvField(row, tkeSolverIterations);
        appendCsvField(row, sdrSolverIterations);
        appendCsvField(row, energySolverIterations);
        appendCsvField(row, pressureAmgCycles);
        appendCsvField(row, velocityAmgCycles);
        appendCsvField(row, tkeAmgCycles);
        appendCsvField(row, sdrAmgCycles);
        appendCsvField(row, energyAmgCycles);
        appendCsvField(row, pressureEquationTimeS);
        appendCsvField(row, velocityEquationTimeS);
        appendCsvField(row, tkeEquationTimeS);
        appendCsvField(row, sdrEquationTimeS);
        appendCsvField(row, energyEquationTimeS);
        appendCsvField(row, computeHitMaxCycles(pressureAmgCycles, pressureCurrentMaxCycles));
        appendCsvField(row, computeHitMaxCycles(velocityAmgCycles, velocityCurrentMaxCycles));
        appendCsvField(row, null);
        appendCsvField(row, null);
        appendCsvField(row, null);
        long writeStartNs = System.nanoTime();
        pw.println(row.toString());
        pw.flush();
        solverProfilingCsvWriteTimeS += (System.nanoTime() - writeStartNs) / 1.0e9;
        solverProfilingRowsWritten += 1;
    }

    private void writeSolverProfilingRunSummary(
            Simulation sim,
            double simulationWallTimeS,
            double exportWallTimeS,
            double saveStateWallTimeS,
            boolean runFailed,
            String runStatus) {
        String summaryPath = resolvePath(OUTPUT_DIR + "/profiling/" + SOLVER_PROFILING_SUMMARY_NAME);
        try {
            double knownIoTimeS = sumFiniteDurations(
                exportWallTimeS,
                saveStateWallTimeS,
                solverProfilingCsvWriteTimeS
            );
            PrintWriter pw = new PrintWriter(new BufferedWriter(new FileWriter(summaryPath)));
            pw.println("{");
            pw.println("  \"case_id\": " + jsonQuote(CASE_NAME) + ",");
            pw.println("  \"case_name\": " + jsonQuote(CASE_NAME) + ",");
            pw.println("  \"profiling_phase\": " + jsonQuote("phase2_macro_solver_tree") + ",");
            pw.println("  \"run_status\": " + jsonQuote(runStatus) + ",");
            pw.println("  \"starccm_version\": " + jsonQuote(tryReadStarCcmVersion(sim)) + ",");
            pw.println("  \"solver_type\": " + jsonQuote(detectSolverTypeLabel(sim)) + ",");
            pw.println("  \"physics_models\": " + jsonQuote(detectPhysicsModelsLabel()) + ",");
            pw.println("  \"mesh_cells\": " + jsonLong(tryReadMeshCellCount(sim)) + ",");
            pw.println("  \"cfl_status\": " + jsonQuote(cflProfilingStatus) + ",");
            pw.println("  \"cfl_field_function_name\": " + jsonQuote(cflFieldFunctionName) + ",");
            pw.println("  \"cfl_field_function_candidates_sample\": " + jsonQuote(describeCourantFieldFunctionCandidates(sim, 20)) + ",");
            pw.println("  \"cfl_rejected_field_function_candidates_sample\": " + jsonQuote(describeRejectedCourantFieldFunctionCandidates(sim, 20)) + ",");
            pw.println("  \"simulation_wall_time_s\": " + jsonNumber(Double.valueOf(simulationWallTimeS)) + ",");
            pw.println("  \"export_io_time_s\": " + jsonNumber(Double.valueOf(exportWallTimeS)) + ",");
            pw.println("  \"io_breakdown_s\": {");
            pw.println("    \"result_reports_export_s\": " + jsonNumber(Double.valueOf(exportWallTimeS)) + ",");
            pw.println("    \"solver_profiling_csv_write_s\": " + jsonNumber(Double.valueOf(solverProfilingCsvWriteTimeS)) + ",");
            pw.println("    \"solver_profiling_rows_written\": " + jsonNumber(Double.valueOf(solverProfilingRowsWritten)) + ",");
            pw.println("    \"result_sim_save_s\": " + jsonNumber(Double.valueOf(saveStateWallTimeS)) + ",");
            pw.println("    \"known_total_io_s\": " + jsonNumber(Double.valueOf(knownIoTimeS)));
            pw.println("  },");
            pw.println("  \"run_failed\": " + jsonBoolean(runFailed) + ",");
            pw.println("  \"artifacts\": {");
            pw.println("    \"solver_profiling_csv\": " + jsonQuote(SOLVER_PROFILING_CSV_NAME) + ",");
            pw.println("    \"solver_profiling_summary_json\": " + jsonQuote(SOLVER_PROFILING_SUMMARY_NAME));
            pw.println("  },");
            pw.println("  \"captured_exact_fields\": " + jsonStringArray(
                "chunk_wall_time_s",
                "cumulative_wall_time_s",
                "time_step",
                "solver_type",
                "physics_models",
                "pressure_current_urf",
                "velocity_current_urf",
                "pressure_relaxation_scheme",
                "velocity_relaxation_scheme",
                "pressure_current_tolerance",
                "velocity_current_tolerance",
                "pressure_current_max_cycles",
                "velocity_current_max_cycles",
                "pressure_current_cycle_label",
                "velocity_current_cycle_label"
            ) + ",");
            pw.println("  \"captured_best_effort_fields\": " + jsonStringArray(
                "starccm_version",
                "mesh_cells",
                "physical_time",
                "energy_residual",
                "inlet_mass_flow",
                "outlet_mass_flow",
                "mass_imbalance_abs",
                "mass_imbalance_relative",
                "max_cfl",
                "mean_cfl",
                "pressure_final_residual",
                "x_momentum_final_residual",
                "y_momentum_final_residual",
                "z_momentum_final_residual",
                "tke_final_residual",
                "sdr_final_residual",
                "energy_final_residual",
                "tke_current_urf",
                "sdr_current_urf",
                "energy_current_urf",
                "tke_current_cycle_label",
                "sdr_current_cycle_label",
                "energy_current_cycle_label",
                "tke_current_tolerance",
                "sdr_current_tolerance",
                "energy_current_tolerance",
                "tke_current_max_cycles",
                "sdr_current_max_cycles",
                "energy_current_max_cycles",
                "pressure_solver_iterations",
                "velocity_solver_iterations",
                "tke_solver_iterations",
                "sdr_solver_iterations",
                "energy_solver_iterations",
                "pressure_amg_cycles",
                "velocity_amg_cycles",
                "tke_amg_cycles",
                "sdr_amg_cycles",
                "energy_amg_cycles",
                "pressure_equation_time_s",
                "velocity_equation_time_s",
                "tke_equation_time_s",
                "sdr_equation_time_s",
                "energy_equation_time_s"
            ) + ",");
            pw.println("  \"pending_deeper_instrumentation\": " + jsonStringArray(
                "reliable_hit_max_cycles_for_all_equations",
                "pressure_solver_iterations",
                "exact_full_equation_wall_time"
            ) + ",");
            pw.println("  \"available_solver_classes_sample\": " + jsonQuote(describeAvailableSolvers(sim, 20)) + ",");
            pw.println("  \"available_monitor_names_sample\": " + jsonQuote(describeAvailableMonitors(sim, 30)) + ",");
            pw.println("  \"solver_metric_primary_targets_sample\": "
                + jsonQuote(describeSolverMetricPrimaryTargets(sim)) + ",");
            pw.println("  \"solver_metric_monitor_candidates_sample\": "
                + jsonQuote(describeSolverMetricMonitorCandidates(sim, 20)));
            pw.println("}");
            pw.close();
        } catch (IOException e) {
            sim.println("WARNING: Unable to write solver profiling summary: " + e.getMessage());
        }
    }

    private Double tryGetReportValue(Simulation sim, String reportName) {
        if (reportName == null || reportName.trim().isEmpty()) {
            return null;
        }
        try {
            double value = getSafeReportValue(sim, reportName);
            if (isInvalidReportValue(value)) {
                return null;
            }
            return Double.valueOf(value);
        } catch (Exception ignored) {}
        return null;
    }

    private Double tryGetConfiguredFieldReportValue(Simulation sim, String reportName) {
        if (reportName == null || reportName.trim().isEmpty()) {
            return null;
        }
        try {
            Report report = (Report) sim.getReportManager().getObject(reportName);
            if (!hasConfiguredFieldFunction(report)) {
                return null;
            }
        } catch (Exception ignored) {
            return null;
        }
        return tryGetReportValue(sim, reportName);
    }

    private Double computeMassImbalanceAbs(Double inletMassFlow, Double outletMassFlow) {
        if (inletMassFlow == null || outletMassFlow == null) {
            return null;
        }
        return Double.valueOf(Math.abs(inletMassFlow.doubleValue() + outletMassFlow.doubleValue()));
    }

    private Double computeMassImbalanceRelative(Double inletMassFlow, Double outletMassFlow) {
        Double absolute = computeMassImbalanceAbs(inletMassFlow, outletMassFlow);
        if (absolute == null) {
            return null;
        }
        double scale = Math.max(
            Math.abs(inletMassFlow.doubleValue()),
            Math.abs(outletMassFlow.doubleValue())
        );
        if (scale <= 1.0e-30) {
            return null;
        }
        return Double.valueOf(absolute.doubleValue() / scale);
    }

    private double sumFiniteDurations(double... values) {
        double total = 0.0;
        for (double value : values) {
            if (Double.isNaN(value) || Double.isInfinite(value) || value < 0.0) {
                continue;
            }
            total += value;
        }
        return total;
    }

    private String tryReadStarCcmVersion(Simulation sim) {
        Object raw = tryInvokeNoArg(
            sim,
            "getVersion",
            "getProductVersion",
            "getReleaseVersion"
        );
        if (raw != null) {
            String text = raw.toString();
            if (text != null && !text.trim().isEmpty()) {
                return text.trim();
            }
        }
        Package pkg = sim.getClass().getPackage();
        if (pkg != null && pkg.getImplementationVersion() != null) {
            return pkg.getImplementationVersion();
        }
        return null;
    }

    private String detectSolverTypeLabel(Simulation sim) {
        boolean hasSegregated = getNamedSolver(sim, "star.flow.SegregatedFlowSolver") != null
            || findSolverByClassFragment(sim, "SegregatedFlowSolver") != null;
        boolean hasCoupled = getNamedSolver(sim, "star.flow.CoupledFlowSolver") != null
            || findSolverByClassFragment(sim, "CoupledFlowSolver") != null;
        if (hasSegregated && hasCoupled) {
            return "mixed";
        }
        if (hasCoupled) {
            return "coupled";
        }
        if (hasSegregated) {
            return "segregated";
        }
        return null;
    }

    private String detectPhysicsModelsLabel() {
        ArrayList<String> models = new ArrayList<String>();
        if (TURB_MODEL != null && !TURB_MODEL.trim().isEmpty()) {
            models.add(TURB_MODEL.trim());
        }
        if (SIM_TYPE != null && !SIM_TYPE.trim().isEmpty()) {
            models.add(SIM_TYPE.trim());
        }
        if (SOLVE_ENERGY) {
            models.add("energy");
        }
        if (models.isEmpty()) {
            return null;
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < models.size(); i++) {
            if (i > 0) sb.append("|");
            sb.append(models.get(i));
        }
        return sb.toString();
    }

    private Long tryReadMeshCellCount(Simulation sim) {
        long total = 0L;
        boolean found = false;
        try {
            for (Object regionObj : sim.getRegionManager().getObjects()) {
                Double value = tryReadNumericFromTarget(
                    regionObj,
                    "getCellCount",
                    "getCellsCount",
                    "getNumberOfCells"
                );
                if (value == null) {
                    value = tryReadMeshCellCountByHeuristic(regionObj);
                }
                if (value == null) {
                    continue;
                }
                total += Math.max(0L, Math.round(value.doubleValue()));
                found = true;
            }
        } catch (Exception ignored) {}
        if (found) {
            return Long.valueOf(total);
        }
        Object meshManager = tryInvokeNoArg(sim, "getMeshManager");
        Double meshCount = tryReadNumericFromTarget(
            meshManager,
            "getCellCount",
            "getCellsCount",
            "getNumberOfCells"
        );
        if (meshCount == null) {
            meshCount = tryReadMeshCellCountByHeuristic(meshManager);
        }
        if (meshCount != null) {
            return Long.valueOf(Math.max(0L, Math.round(meshCount.doubleValue())));
        }
        return null;
    }

    private Double tryReadPhysicalTime(Simulation sim, int iteration) {
        Object iterator = tryInvokeNoArg(sim, "getSimulationIterator", "getIterator");
        Double physicalTime = tryReadNumericFromTarget(
            iterator,
            "getCurrentTime",
            "getPhysicalTime",
            "getSimulationTime",
            "getTime"
        );
        if (physicalTime != null) {
            return physicalTime;
        }
        if (SIM_TYPE.equals("transient")) {
            return Double.valueOf(TIME_STEP * Math.max(0, iteration));
        }
        return null;
    }

    private Double tryReadVelocityRelaxationFactor(Simulation sim) {
        VelocitySolver velocitySolver = getTypedVelocitySolver(sim);
        return tryReadNumericFromTarget(
            velocitySolver,
            "getUrf",
            "getURF",
            "getUnderRelaxationFactor"
        );
    }

    private Double tryReadVelocityAmgMaxCycles(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedVelocityAmgLinearSolver(sim);
        return tryReadNumericFromTarget(amgLinearSolver, "getMaxCycles");
    }

    private Double tryReadVelocityAmgConvergeTol(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedVelocityAmgLinearSolver(sim);
        return tryReadNumericFromTarget(amgLinearSolver, "getConvergeTol");
    }

    private Double tryReadSolverUrfByTokenGroups(Simulation sim, String[]... tokenGroups) {
        Object solver = findSolverCandidateByTokenGroups(sim, tokenGroups);
        Double direct = tryReadNumericFromTargetGraph(
            solver,
            4,
            new String[] {"getUrf", "getURF", "getUnderRelaxationFactor"},
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        return tryReadNumericFromTarget(
            solver,
            "getUrf",
            "getURF",
            "getUnderRelaxationFactor"
        );
    }

    private Double tryReadResidualMonitorValue(Simulation sim, String[]... tokenGroups) {
        Double exact = tryReadMonitorValueByTokenGroups(sim, tokenGroups);
        if (exact != null) {
            return exact;
        }
        String[][] relaxedTokenGroups = dropResidualOnlyTokenGroups(tokenGroups);
        if (relaxedTokenGroups == null || relaxedTokenGroups.length == 0) {
            return null;
        }
        return tryReadMonitorValueByTokenGroups(sim, relaxedTokenGroups);
    }

    private Double tryReadMonitorValueByTokenGroups(Simulation sim, String[]... tokenGroups) {
        if (sim == null || tokenGroups == null || tokenGroups.length == 0) {
            return null;
        }
        try {
            for (Object monitorObj : sim.getMonitorManager().getObjects()) {
                if (monitorObj == null) continue;
                String combined = safePresentationName(monitorObj)
                    + " " + monitorObj.getClass().getName();
                if (!matchesTokenGroups(combined, tokenGroups)) {
                    continue;
                }
                Double value = extractNumericMonitorValue(monitorObj);
                if (value != null && !isInvalidReportValue(value.doubleValue())) {
                    return value;
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private Object findSolverByTokenGroups(Simulation sim, String[]... tokenGroups) {
        if (sim == null || tokenGroups == null || tokenGroups.length == 0) {
            return null;
        }
        try {
            for (Object solverObj : sim.getSolverManager().getObjects()) {
                if (solverObj == null) continue;
                String combined = safePresentationName(solverObj)
                    + " " + solverObj.getClass().getName();
                if (matchesTokenGroups(combined, tokenGroups)) {
                    return solverObj;
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private Object findSolverCandidateByTokenGroups(Simulation sim, String[]... tokenGroups) {
        if (sim == null || tokenGroups == null || tokenGroups.length == 0) {
            return null;
        }
        try {
            for (Object solverObj : collectSolverCandidates(sim)) {
                if (solverObj == null) continue;
                String combined = safePresentationName(solverObj)
                    + " " + solverObj.getClass().getName();
                if (matchesTokenGroups(combined, tokenGroups)) {
                    return solverObj;
                }
            }
        } catch (Exception ignored) {}
        return findSolverByTokenGroups(sim, tokenGroups);
    }

    private boolean matchesTokenGroups(String text, String[]... tokenGroups) {
        String normalized = normalizeLabel(text);
        if (normalized.isEmpty()) {
            return false;
        }
        for (String[] group : tokenGroups) {
            boolean matched = false;
            if (group != null) {
                for (String token : group) {
                    String normalizedToken = normalizeLabel(token);
                    if (!normalizedToken.isEmpty() && normalized.contains(normalizedToken)) {
                        matched = true;
                        break;
                    }
                }
            }
            if (!matched) {
                return false;
            }
        }
        return true;
    }

    private Double tryReadPressureSolverIterations(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            getTypedPressureSolver(sim),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(getTypedPressureSolver(sim));
        if (direct != null) {
            return direct;
        }
        direct = tryReadNumericFromTargetGraph(
            getTypedPressureAmgLinearSolver(sim),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(getTypedPressureAmgLinearSolver(sim));
        if (direct != null) {
            return direct;
        }
        direct = tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(sim, new String[] {"PRESSURE", "CONTINUITY"}),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(
            findSolverCandidateByTokenGroups(sim, new String[] {"PRESSURE", "CONTINUITY"})
        );
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"PRESSURE", "CONTINUITY"},
            new String[] {"LINEAR", "SOLVER", "INNER"},
            new String[] {"ITER", "ITERATION"}
        );
    }

    private Double tryReadVelocitySolverIterations(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            getTypedVelocitySolver(sim),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(getTypedVelocitySolver(sim));
        if (direct != null) {
            return direct;
        }
        direct = tryReadNumericFromTargetGraph(
            getTypedVelocityAmgLinearSolver(sim),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(getTypedVelocityAmgLinearSolver(sim));
        if (direct != null) {
            return direct;
        }
        direct = tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"VELOCITY", "XMOMENTUM", "YMOMENTUM", "ZMOMENTUM"}
            ),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"VELOCITY", "XMOMENTUM", "YMOMENTUM", "ZMOMENTUM"}
            )
        );
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"VELOCITY", "XMOMENTUM", "YMOMENTUM", "ZMOMENTUM"},
            new String[] {"LINEAR", "SOLVER", "INNER"},
            new String[] {"ITER", "ITERATION"}
        );
    }

    private Double tryReadTkeSolverIterations(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"}
            ),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"}
            )
        );
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"TKE", "TURBULENTKINETIC"},
            new String[] {"LINEAR", "SOLVER", "INNER"},
            new String[] {"ITER", "ITERATION"}
        );
    }

    private Double tryReadSdrSolverIterations(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"}
            ),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"}
            )
        );
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"SDR", "SPECIFICDISSIPATION"},
            new String[] {"LINEAR", "SOLVER", "INNER"},
            new String[] {"ITER", "ITERATION"}
        );
    }

    private Double tryReadEnergySolverIterations(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"ENERGY", "TEMPERATURE"}
            ),
            4,
            getSolverIterationGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadSolverIterationHeuristic(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"ENERGY", "TEMPERATURE"}
            )
        );
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"ENERGY", "TEMPERATURE"},
            new String[] {"LINEAR", "SOLVER", "INNER"},
            new String[] {"ITER", "ITERATION"}
        );
    }

    private Double tryReadCumulativeAmgCyclesFromTarget(Object target) {
        return tryReadNumericFromTargetGraph(
            target,
            4,
            getTotalAmgCycleGetterNames(),
            getSolverMetricChildGetterNames()
        );
    }

    private Double tryReadCumulativeSolveElapsedTimeFromTarget(Object target) {
        return tryReadNumericFromTargetGraph(
            target,
            4,
            getTotalSolveElapsedTimeGetterNames(),
            getSolverMetricChildGetterNames()
        );
    }

    private Double tryReadPressureTotalAmgCycles(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
        if (amgLinearSolver != null) {
            try {
                return Double.valueOf((double) amgLinearSolver.getTotalNumberCycles());
            } catch (Exception ignored) {}
        }
        return tryReadCumulativeAmgCyclesFromTarget(amgLinearSolver);
    }

    private Double tryReadVelocityTotalAmgCycles(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedVelocityAmgLinearSolver(sim);
        if (amgLinearSolver != null) {
            try {
                return Double.valueOf((double) amgLinearSolver.getTotalNumberCycles());
            } catch (Exception ignored) {}
        }
        return tryReadCumulativeAmgCyclesFromTarget(amgLinearSolver);
    }

    private Double tryReadTkeTotalAmgCycles(Simulation sim) {
        return tryReadCumulativeAmgCyclesFromTarget(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"}
            )
        );
    }

    private Double tryReadSdrTotalAmgCycles(Simulation sim) {
        return tryReadCumulativeAmgCyclesFromTarget(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"}
            )
        );
    }

    private Double tryReadEnergyTotalAmgCycles(Simulation sim) {
        return tryReadCumulativeAmgCyclesFromTarget(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"ENERGY", "TEMPERATURE"}
            )
        );
    }

    private Double tryReadPressureTotalSolveElapsedTime(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedPressureAmgLinearSolver(sim);
        if (amgLinearSolver != null) {
            try {
                return Double.valueOf(amgLinearSolver.getTotalSolveElapsedTime());
            } catch (Exception ignored) {}
        }
        return tryReadCumulativeSolveElapsedTimeFromTarget(amgLinearSolver);
    }

    private Double tryReadVelocityTotalSolveElapsedTime(Simulation sim) {
        AMGLinearSolver amgLinearSolver = getTypedVelocityAmgLinearSolver(sim);
        if (amgLinearSolver != null) {
            try {
                return Double.valueOf(amgLinearSolver.getTotalSolveElapsedTime());
            } catch (Exception ignored) {}
        }
        return tryReadCumulativeSolveElapsedTimeFromTarget(amgLinearSolver);
    }

    private Double tryReadTkeTotalSolveElapsedTime(Simulation sim) {
        return tryReadCumulativeSolveElapsedTimeFromTarget(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"}
            )
        );
    }

    private Double tryReadSdrTotalSolveElapsedTime(Simulation sim) {
        return tryReadCumulativeSolveElapsedTimeFromTarget(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"}
            )
        );
    }

    private Double tryReadEnergyTotalSolveElapsedTime(Simulation sim) {
        return tryReadCumulativeSolveElapsedTimeFromTarget(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"ENERGY", "TEMPERATURE"}
            )
        );
    }

    private Double tryReadPressureAmgCycles(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            getTypedPressureAmgLinearSolver(sim),
            4,
            getAmgCycleGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadAmgCycleHeuristic(getTypedPressureAmgLinearSolver(sim));
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"PRESSURE", "CONTINUITY"},
            new String[] {"AMG", "MULTIGRID"},
            new String[] {"CYCLE"}
        );
    }

    private Double tryReadVelocityAmgCycles(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            getTypedVelocityAmgLinearSolver(sim),
            4,
            getAmgCycleGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadAmgCycleHeuristic(getTypedVelocityAmgLinearSolver(sim));
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"VELOCITY", "XMOMENTUM", "YMOMENTUM", "ZMOMENTUM"},
            new String[] {"AMG", "MULTIGRID"},
            new String[] {"CYCLE"}
        );
    }

    private Double tryReadTkeAmgCycles(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"}
            ),
            4,
            getAmgCycleGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadAmgCycleHeuristic(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"TKE", "TURBULENTKINETIC", "KWTURB"}
            )
        );
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"TKE", "TURBULENTKINETIC"},
            new String[] {"AMG", "MULTIGRID"},
            new String[] {"CYCLE"}
        );
    }

    private Double tryReadSdrAmgCycles(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"}
            ),
            4,
            getAmgCycleGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadAmgCycleHeuristic(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"SDR", "SPECIFICDISSIPATION", "KWTURB"}
            )
        );
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"SDR", "SPECIFICDISSIPATION"},
            new String[] {"AMG", "MULTIGRID"},
            new String[] {"CYCLE"}
        );
    }

    private Double tryReadEnergyAmgCycles(Simulation sim) {
        Double direct = tryReadNumericFromTargetGraph(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"ENERGY", "TEMPERATURE"}
            ),
            4,
            getAmgCycleGetterNames(),
            getSolverMetricChildGetterNames()
        );
        if (direct != null) {
            return direct;
        }
        direct = tryReadAmgCycleHeuristic(
            findSolverCandidateByTokenGroups(
                sim,
                new String[] {"ENERGY", "TEMPERATURE"}
            )
        );
        if (direct != null) {
            return direct;
        }
        return tryReadMonitorValueByTokenGroups(
            sim,
            new String[] {"ENERGY", "TEMPERATURE"},
            new String[] {"AMG", "MULTIGRID"},
            new String[] {"CYCLE"}
        );
    }

    private Boolean computeHitMaxCycles(Double cycles, Double maxCycles) {
        if (cycles == null || maxCycles == null) {
            return null;
        }
        return Boolean.valueOf(cycles.doubleValue() >= maxCycles.doubleValue() - 1.0e-9);
    }

    private String deriveRelaxationScheme(
            Double initialValue,
            Double startIteration,
            Double endIteration,
            Double currentValue) {
        if (initialValue != null && startIteration != null && endIteration != null) {
            return "linear_ramp";
        }
        if (currentValue != null) {
            return "constant_urf";
        }
        return null;
    }

    private String describeAvailableMonitors(Simulation sim, int limit) {
        LinkedHashSet<String> names = new LinkedHashSet<String>();
        try {
            for (Object monitorObj : sim.getMonitorManager().getObjects()) {
                if (monitorObj == null) continue;
                names.add(safePresentationName(monitorObj));
                if (limit > 0 && names.size() >= limit) {
                    break;
                }
            }
        } catch (Exception ignored) {}
        if (names.isEmpty()) {
            return "<none>";
        }
        StringBuilder sb = new StringBuilder();
        int idx = 0;
        for (String name : names) {
            if (idx > 0) sb.append(", ");
            sb.append(name);
            idx += 1;
        }
        return sb.toString();
    }

    private String describeSolverMetricMonitorCandidates(Simulation sim, int limit) {
        LinkedHashSet<String> names = new LinkedHashSet<String>();
        try {
            for (Object monitorObj : sim.getMonitorManager().getObjects()) {
                if (monitorObj == null) continue;
                String combined = safePresentationName(monitorObj)
                    + " " + monitorObj.getClass().getName();
                if (!containsAnyNormalizedToken(
                        combined,
                        "LINEAR",
                        "INNER",
                        "AMG",
                        "MULTIGRID",
                        "CYCLE",
                        "ITER")) {
                    continue;
                }
                names.add(safePresentationName(monitorObj));
                if (limit > 0 && names.size() >= limit) {
                    break;
                }
            }
        } catch (Exception ignored) {}
        if (names.isEmpty()) {
            return "<none>";
        }
        StringBuilder sb = new StringBuilder();
        int idx = 0;
        for (String name : names) {
            if (idx > 0) sb.append(", ");
            sb.append(name);
            idx += 1;
        }
        return sb.toString();
    }

    private String describeSolverMetricPrimaryTargets(Simulation sim) {
        ArrayList<String> targets = new ArrayList<String>();
        String pressureSolver = describeObjectTarget(getTypedPressureSolver(sim));
        String velocitySolver = describeObjectTarget(getTypedVelocitySolver(sim));
        String pressureAmg = describeObjectTarget(getTypedPressureAmgLinearSolver(sim));
        String velocityAmg = describeObjectTarget(getTypedVelocityAmgLinearSolver(sim));
        if (pressureSolver != null) targets.add("pressure_solver=" + pressureSolver);
        if (velocitySolver != null) targets.add("velocity_solver=" + velocitySolver);
        if (pressureAmg != null) targets.add("pressure_amg_solver=" + pressureAmg);
        if (velocityAmg != null) targets.add("velocity_amg_solver=" + velocityAmg);
        if (targets.isEmpty()) {
            return "<none>";
        }
        StringBuilder sb = new StringBuilder();
        for (int idx = 0; idx < targets.size(); idx += 1) {
            if (idx > 0) sb.append(", ");
            sb.append(targets.get(idx));
        }
        return sb.toString();
    }

    private boolean containsAnyNormalizedToken(String text, String... tokens) {
        String normalized = normalizeLabel(text);
        if (normalized.isEmpty() || tokens == null || tokens.length == 0) {
            return false;
        }
        for (String token : tokens) {
            String normalizedToken = normalizeLabel(token);
            if (!normalizedToken.isEmpty() && normalized.contains(normalizedToken)) {
                return true;
            }
        }
        return false;
    }

    private String describeObjectTarget(Object obj) {
        if (obj == null) {
            return null;
        }
        String name = safePresentationName(obj);
        String className = obj.getClass().getName();
        if (name == null || name.trim().isEmpty()) {
            return className;
        }
        return name + " [" + className + "]";
    }

    private void appendCsvField(StringBuilder row, Object value) {
        if (row.length() > 0) {
            row.append(",");
        }
        row.append(csvScalar(value));
    }

    private String csvScalar(Object value) {
        if (value == null) {
            return "";
        }
        if (value instanceof Double) {
            Double numeric = (Double) value;
            if (numeric.isNaN() || numeric.isInfinite()) {
                return "";
            }
            return numeric.toString();
        }
        if (value instanceof Float) {
            Float numeric = (Float) value;
            if (numeric.isNaN() || numeric.isInfinite()) {
                return "";
            }
            return numeric.toString();
        }
        String text = value.toString();
        if (text == null) {
            return "";
        }
        boolean needsQuotes = text.contains(",")
            || text.contains("\"")
            || text.contains("\n")
            || text.contains("\r");
        String escaped = text.replace("\"", "\"\"");
        return needsQuotes ? "\"" + escaped + "\"" : escaped;
    }

    private String jsonQuote(String value) {
        if (value == null) {
            return "null";
        }
        return "\"" + value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            + "\"";
    }

    private String jsonNumber(Double value) {
        if (value == null || value.isNaN() || value.isInfinite()) {
            return "null";
        }
        return value.toString();
    }

    private String jsonLong(Long value) {
        if (value == null) {
            return "null";
        }
        return value.toString();
    }

    private String jsonBoolean(boolean value) {
        return value ? "true" : "false";
    }

    private String jsonStringArray(String... values) {
        if (values == null || values.length == 0) {
            return "[]";
        }
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < values.length; i++) {
            if (i > 0) sb.append(", ");
            sb.append(jsonQuote(values[i]));
        }
        sb.append("]");
        return sb.toString();
    }

    private int resolveCurrentMaxIterations(Simulation sim, int fallback) {
        int resolved = Math.max(1, fallback);
        try {
            StepStoppingCriterion sc = (StepStoppingCriterion)
                sim.getSolverStoppingCriterionManager()
                   .getSolverStoppingCriterion(MAX_STEPS_CRITERION);
            if (sc == null) {
                return resolved;
            }
            Double value = tryReadNumericFromTarget(
                sc,
                "getMaximumNumberSteps",
                "getMaximumSteps",
                "getMaxSteps"
            );
            if (value != null) {
                resolved = Math.max(1, (int) Math.round(value.doubleValue()));
            }
        } catch (Exception ignored) {}
        return resolved;
    }

    private double runSimulation(Simulation sim) {
        double cumulativeWallTimeS = 0.0;
        PrintWriter profilingWriter = openSolverProfilingWriter(sim);
        try {
            if (profilingWriter != null) {
                writeSolverProfilingRow(
                    sim,
                    profilingWriter,
                    "initial",
                    0,
                    0,
                    0.0,
                    0.0
                );
            }
            if (SIM_TYPE.equals("transient")) {
                int done = 0;
                while (done < NUM_TIME_STEPS) {
                    int chunk = Math.min(CHECK_INTERVAL, NUM_TIME_STEPS - done);
                    long chunkStartNs = System.nanoTime();
                    sim.getSimulationIterator().run(chunk);
                    double chunkWallTimeS = (System.nanoTime() - chunkStartNs) / 1.0e9;
                    cumulativeWallTimeS += chunkWallTimeS;
                    done += chunk;
                    if (profilingWriter != null) {
                        writeSolverProfilingRow(
                            sim,
                            profilingWriter,
                            "post_chunk",
                            done,
                            chunk,
                            chunkWallTimeS,
                            cumulativeWallTimeS
                        );
                    }
                    maybeSavePeriodicCheckpoint(sim, done);
                    boolean updated = applyParamUpdatesIfPresent(sim, done);
                    if (updated && profilingWriter != null) {
                        writeSolverProfilingRow(
                            sim,
                            profilingWriter,
                            "post_update",
                            done,
                            0,
                            0.0,
                            cumulativeWallTimeS
                        );
                    }
                }
            } else {
                int done = 0;
                int currentMaxIter = resolveCurrentMaxIterations(sim, MAX_ITER);
                while (done < currentMaxIter) {
                    int chunk = Math.min(LOG_FREQ, currentMaxIter - done);
                    long chunkStartNs = System.nanoTime();
                    sim.getSimulationIterator().run(chunk);
                    double chunkWallTimeS = (System.nanoTime() - chunkStartNs) / 1.0e9;
                    cumulativeWallTimeS += chunkWallTimeS;
                    done += chunk;
                    if (profilingWriter != null) {
                        writeSolverProfilingRow(
                            sim,
                            profilingWriter,
                            "post_chunk",
                            done,
                            chunk,
                            chunkWallTimeS,
                            cumulativeWallTimeS
                        );
                    }
                    maybeSavePeriodicCheckpoint(sim, done);
                    boolean updated = applyParamUpdatesIfPresent(sim, done);
                    currentMaxIter = resolveCurrentMaxIterations(sim, currentMaxIter);
                    if (updated && profilingWriter != null) {
                        writeSolverProfilingRow(
                            sim,
                            profilingWriter,
                            "post_update",
                            done,
                            0,
                            0.0,
                            cumulativeWallTimeS
                        );
                    }
                }
            }
        } finally {
            if (profilingWriter != null) {
                profilingWriter.close();
            }
        }
        return cumulativeWallTimeS;
    }

    private void maybeSavePeriodicCheckpoint(Simulation sim, int currentIteration) {
        if (CHECKPOINT_INTERVAL_ITER <= 0 || currentIteration <= 0) {
            return;
        }
        if (currentIteration % CHECKPOINT_INTERVAL_ITER != 0) {
            return;
        }
        String checkpointPath =
            SIM_DIR + "/" + CASE_NAME + "_periodic_checkpoint_iter" + currentIteration + ".sim";
        saveSimulationState(sim, checkpointPath, "periodic-checkpoint");
    }

    private boolean applyParamUpdatesIfPresent(Simulation sim, int currentIteration) {
        if (PARAM_UPDATE_FILE == null || PARAM_UPDATE_FILE.isEmpty()) return false;
        File f = new File(PARAM_UPDATE_FILE);
        if (!f.exists()) return false;

        sim.println("[AI] 检测到参数更新文件，开始读取...");
        String content = "";
        try {
            BufferedReader br = new BufferedReader(new FileReader(f));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
            br.close();
            content = sb.toString().trim();
        } catch (IOException e) {
            sim.println("[AI] 读取参数更新文件失败: " + e.getMessage());
            return false;
        }

        f.delete();

        if (content.isEmpty()) return false;

        String runId = extractJsonStringField(content, "run_id");
        if (runId == null || runId.trim().isEmpty()) {
            runId = RUN_ID;
        }
        String actionId = extractJsonStringField(content, "action_id");
        String requestedChangesJson = extractRequestedChangesObject(content);
        LinkedHashMap<String, Double> requestedChanges = parseNumericJsonMap(
            requestedChangesJson != null ? requestedChangesJson : content
        );
        LinkedHashSet<String> updatedKeys = new LinkedHashSet<String>();
        for (Map.Entry<String, Double> entry : requestedChanges.entrySet()) {
            String key = entry.getKey();
            double value = entry.getValue().doubleValue();
            updatedKeys.add(key);
            applyOneParam(sim, key, value);
        }

        String applyResult = updatedKeys.isEmpty() ? "noop" : "applied";
        writeActionAck(
            sim,
            runId,
            actionId,
            currentIteration,
            requestedChanges,
            updatedKeys,
            applyResult
        );
        if (updatedKeys.isEmpty()) {
            return false;
        }
        logSolverTreeSnapshotAfterUpdate(sim, updatedKeys, content);
        return true;
    }

    private String extractJsonStringField(String content, String fieldName) {
        if (content == null || fieldName == null || fieldName.isEmpty()) {
            return null;
        }
        java.util.regex.Pattern pat = java.util.regex.Pattern.compile(
            "\"" + java.util.regex.Pattern.quote(fieldName) + "\"\\s*:\\s*\"([^\"]*)\""
        );
        java.util.regex.Matcher matcher = pat.matcher(content);
        if (!matcher.find()) {
            return null;
        }
        return matcher.group(1);
    }

    private String extractRequestedChangesObject(String content) {
        if (content == null || content.isEmpty()) {
            return null;
        }
        java.util.regex.Pattern pat = java.util.regex.Pattern.compile(
            "\"requested_changes\"\\s*:\\s*(\\{.*?\\})",
            java.util.regex.Pattern.DOTALL
        );
        java.util.regex.Matcher matcher = pat.matcher(content);
        if (!matcher.find()) {
            return null;
        }
        return matcher.group(1);
    }

    private LinkedHashMap<String, Double> parseNumericJsonMap(String content) {
        LinkedHashMap<String, Double> parsed = new LinkedHashMap<String, Double>();
        if (content == null || content.trim().isEmpty()) {
            return parsed;
        }
        java.util.regex.Pattern kvPat = java.util.regex.Pattern.compile(
            "\"(\\w+)\"\\s*:\\s*([+-]?[0-9]*\\.?[0-9]+(?:[eE][+-]?[0-9]+)?)"
        );
        java.util.regex.Matcher matcher = kvPat.matcher(content);
        while (matcher.find()) {
            parsed.put(matcher.group(1), Double.valueOf(Double.parseDouble(matcher.group(2))));
        }
        return parsed;
    }

    private void writeActionAck(
            Simulation sim,
            String runId,
            String actionId,
            int currentIteration,
            LinkedHashMap<String, Double> requestedChanges,
            LinkedHashSet<String> updatedKeys,
            String applyResult) {
        if (actionId == null || actionId.trim().isEmpty()) {
            return;
        }
        String ackJson =
            "{"
            + "\"protocol_version\": " + PROTOCOL_VERSION + ", "
            + "\"run_id\": " + jsonQuote(runId) + ", "
            + "\"action_id\": " + jsonQuote(actionId) + ", "
            + "\"status\": " + jsonQuote("acknowledged") + ", "
            + "\"apply_result\": " + jsonQuote(applyResult) + ", "
            + "\"acknowledged_at\": " + jsonQuote(Long.toString(System.currentTimeMillis())) + ", "
            + "\"applied_iteration\": " + currentIteration + ", "
            + "\"requested_changes\": " + jsonNumericMap(requestedChanges) + ", "
            + "\"updated_keys\": " + jsonStringCollection(updatedKeys)
            + "}";
        writeTextFile(PARAM_ACK_FILE, ackJson + "\n", false);
        writeTextFile(ACTION_ACK_LOG_FILE, ackJson + "\n", true);
        sim.println("[AI] action ack written: " + actionId + " result=" + applyResult);
    }

    private String jsonNumericMap(LinkedHashMap<String, Double> values) {
        if (values == null || values.isEmpty()) {
            return "{}";
        }
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Double> entry : values.entrySet()) {
            if (!first) {
                sb.append(", ");
            }
            sb.append(jsonQuote(entry.getKey())).append(": ").append(jsonNumber(entry.getValue()));
            first = false;
        }
        sb.append("}");
        return sb.toString();
    }

    private String jsonStringCollection(Collection<String> values) {
        if (values == null || values.isEmpty()) {
            return "[]";
        }
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (String value : values) {
            if (!first) {
                sb.append(", ");
            }
            sb.append(jsonQuote(value));
            first = false;
        }
        sb.append("]");
        return sb.toString();
    }

    private void writeTextFile(String path, String content, boolean append) {
        if (path == null || path.trim().isEmpty() || content == null) {
            return;
        }
        try {
            File file = new File(path);
            File parent = file.getParentFile();
            if (parent != null && !parent.exists()) {
                parent.mkdirs();
            }
            FileWriter writer = new FileWriter(file, append);
            writer.write(content);
            writer.close();
        } catch (IOException e) {
            // Intentionally best-effort: ack loss should not stop the solver.
        }
    }

    private void applyOneParam(Simulation sim, String key, double value) {
        try {
            Region region = sim.getRegionManager().getRegion(REGION_NAME);
            Units units_ms = ((Units) sim.getUnitsManager().getObject("m/s"));

            switch (key) {
                case "inlet_velocity": {
                    double yawRad = Math.toRadians(YAW_ANGLE_DEG);
                    double vx = value * Math.cos(yawRad);
                    double vy = value * Math.sin(yawRad);
                    if (!INLET_BC.isEmpty()) {
                        Boundary inlet = region.getBoundaryManager().getBoundary(INLET_BC);
                        inlet.getValues().get(VelocityProfile.class)
                             .getMethod(ConstantVectorProfileMethod.class)
                             .getQuantity().setComponentsAndUnits(vx, vy, 0.0, units_ms);
                    }
                    if (GROUND_SLIDING && !GROUND_BC.isEmpty()) {
                        double vxGround = value * Math.cos(yawRad);
                        Boundary ground = region.getBoundaryManager().getBoundary(GROUND_BC);
                        ground.getValues().get(WallRelativeVelocityProfile.class)
                              .getMethod(ConstantVectorProfileMethod.class)
                              .getQuantity().setComponentsAndUnits(vxGround, 0.0, 0.0, units_ms);
                    }
                    sim.println("[AI] inlet_velocity → " + value + " m/s");
                    break;
                }
                case "inlet_temperature": {
                    if (!INLET_BC.isEmpty() && SOLVE_ENERGY) {
                        Boundary inlet = region.getBoundaryManager().getBoundary(INLET_BC);
                        inlet.getValues().get(StaticTemperatureProfile.class)
                             .getMethod(ConstantScalarProfileMethod.class)
                             .getQuantity().setValue(value);
                    }
                    sim.println("[AI] inlet_temperature → " + value + " K");
                    break;
                }
                case "outlet_pressure": {
                    if (!OUTLET_BC.isEmpty()) {
                        Boundary outlet = region.getBoundaryManager().getBoundary(OUTLET_BC);
                        outlet.getValues().get(StaticPressureProfile.class)
                              .getMethod(ConstantScalarProfileMethod.class)
                              .getQuantity().setValue(value);
                    }
                    sim.println("[AI] outlet_pressure → " + value + " Pa");
                    break;
                }
                case "inlet_turbulence_intensity": {
                    if (!INLET_BC.isEmpty()) {
                        Boundary inlet = region.getBoundaryManager().getBoundary(INLET_BC);
                        inlet.getValues().get(TurbulenceIntensityProfile.class)
                             .getMethod(ConstantScalarProfileMethod.class)
                             .getQuantity().setValue(value);
                    }
                    sim.println("[AI] inlet_turbulence_intensity → " + value);
                    break;
                }
                case "inlet_turbulent_length_scale": {
                    if (!INLET_BC.isEmpty()) {
                        Boundary inlet = region.getBoundaryManager().getBoundary(INLET_BC);
                        if (!tryApplyInletTurbulentLengthScale(sim, inlet, value)) {
                            sim.println(
                                "[AI] inlet_turbulent_length_scale skipped: profile unavailable on boundary '"
                                + INLET_BC + "'. Candidates: "
                                + describeObjectClasses(collectBoundaryValueCandidates(inlet), 12)
                            );
                            break;
                        }
                    }
                    sim.println("[AI] inlet_turbulent_length_scale → " + value);
                    break;
                }
                case "max_iterations": {
                    try {
                        StepStoppingCriterion sc = (StepStoppingCriterion)
                            sim.getSolverStoppingCriterionManager()
                               .getSolverStoppingCriterion(MAX_STEPS_CRITERION);
                        if (sc != null) sc.setMaximumNumberSteps((int) value);
                    } catch (Exception e) {
                        sim.println("[AI] max_iterations 更新失败: " + e.getMessage());
                    }
                    sim.println("[AI] max_iterations → " + (int) value);
                    break;
                }
                case "convergence_residual": {
                    try {
                        for (Object sc : sim.getSolverStoppingCriterionManager().getObjects()) {
                            String scClass = sc.getClass().getSimpleName();
                            if (scClass.contains("Residual")) {
                                try {
                                    sc.getClass().getMethod("setCriterionValue", double.class)
                                      .invoke(sc, value);
                                } catch (NoSuchMethodException nsm) {
                                    try {
                                        Object limit = sc.getClass().getMethod("getCriterionMinimumLimit")
                                                         .invoke(sc);
                                        limit.getClass().getMethod("setValue", double.class)
                                             .invoke(limit, value);
                                    } catch (Exception inner) {
                                        sim.println("[AI] convergence_residual 方法未找到: " + inner.getMessage());
                                    }
                                }
                            }
                        }
                    } catch (Exception e) {
                        sim.println("[AI] convergence_residual 更新失败: " + e.getMessage());
                    }
                    sim.println("[AI] convergence_residual → " + value);
                    break;
                }
                case "pressure_relaxation_factor": {
                    applyPressureRelaxationFactor(sim, value);
                    break;
                }
                case "pressure_relaxation_initial_value": {
                    applyPressureRelaxationRampInitialValue(sim, value);
                    break;
                }
                case "pressure_relaxation_start_iteration": {
                    applyPressureRelaxationRampStartIteration(sim, (int) Math.round(value));
                    break;
                }
                case "pressure_relaxation_end_iteration": {
                    applyPressureRelaxationRampEndIteration(sim, (int) Math.round(value));
                    break;
                }
                case "velocity_relaxation_initial_value": {
                    applyVelocityRelaxationRampInitialValue(sim, value);
                    break;
                }
                case "velocity_relaxation_start_iteration": {
                    applyVelocityRelaxationRampStartIteration(sim, (int) Math.round(value));
                    break;
                }
                case "velocity_relaxation_end_iteration": {
                    applyVelocityRelaxationRampEndIteration(sim, (int) Math.round(value));
                    break;
                }
                case "pressure_amg_cycle": {
                    applyPressureAmgCycleSetting(sim, (int) Math.round(value));
                    break;
                }
                case "pressure_amg_max_cycles": {
                    applyPressureAmgMaxCyclesSetting(sim, (int) Math.round(value));
                    break;
                }
                case "pressure_amg_converge_tol": {
                    applyPressureAmgConvergeTolSetting(sim, value);
                    break;
                }
                case "pressure_amg_epsilon": {
                    applyPressureAmgEpsilonSetting(sim, value);
                    break;
                }
                case "velocity_amg_cycle": {
                    applyVelocityAmgCycleSetting(sim, (int) Math.round(value));
                    break;
                }
                case "amg_cycle": {
                    applyAmgCycleSetting(sim, (int) Math.round(value));
                    break;
                }
                case "amg_solver": {
                    applyAmgSolverSetting(sim, value >= 0.5);
                    break;
                }
                case "time_step": {
                    try {
                        ImplicitUnsteadySolver unsteady = (ImplicitUnsteadySolver)
                            sim.getSolverManager().getSolver(ImplicitUnsteadySolver.class);
                        unsteady.getTimeStep().setValue(value);
                    } catch (Exception e) {
                        sim.println("[AI] time_step 更新失败: " + e.getMessage());
                    }
                    sim.println("[AI] time_step → " + value + " s");
                    break;
                }
                default:
                    sim.println("[AI] 未知参数，跳过: " + key);
            }
        } catch (Exception e) {
            sim.println("[AI] applyOneParam(" + key + ") 异常: " + e.getMessage());
        }
    }

    private void writeIterationLog(Simulation sim, PrintWriter pw, int iter) {
        double dragVal                 = Double.NaN;
        double totalVal                = Double.NaN;
        double trainSurfacePressureVal = Double.NaN;
        double maxResidualVal          = Double.NaN;
        try {
            dragVal = getSafeReportValue(sim, DRAG_REPORT_NAME);
        } catch (Exception e) {
            if (!missingPrimaryReportWarned) {
                sim.println("Primary report '" + DRAG_REPORT_NAME
                            + "' not found: " + e.getMessage());
                missingPrimaryReportWarned = true;
            }
        }
        if (!TOTAL_REPORT_NAME.isEmpty()) {
            try {
                totalVal = getSafeReportValue(sim, TOTAL_REPORT_NAME);
            } catch (Exception e) {
                sim.println("Total report '" + TOTAL_REPORT_NAME
                            + "' not found: " + e.getMessage());
            }
        }
        if (!TRAIN_SURFACE_PRESSURE_REPORT_NAME.isEmpty()) {
            try {
                trainSurfacePressureVal =
                    getSafeReportValue(sim, TRAIN_SURFACE_PRESSURE_REPORT_NAME);
            } catch (Exception e) {
                if (!missingPressureReportWarned) {
                    sim.println("Pressure report '" + TRAIN_SURFACE_PRESSURE_REPORT_NAME
                                + "' not found: " + e.getMessage());
                    missingPressureReportWarned = true;
                }
            }
        }
        try {
            maxResidualVal = getMaxResidualValue(sim);
        } catch (Exception e) {
            sim.println("Max residual not found: " + e.getMessage());
        }
        if (isInvalidReportValue(dragVal) && !isInvalidReportValue(totalVal)) {
            dragVal = totalVal;
            if (!totalPrimaryFallbackWarned) {
                sim.println("Primary report '" + DRAG_REPORT_NAME
                            + "' unavailable; falling back to total report '"
                            + TOTAL_REPORT_NAME + "' in iteration log.");
                totalPrimaryFallbackWarned = true;
            }
        }
        StringBuilder row = new StringBuilder();
        row.append(iter).append(",").append(dragVal);
        if (!TOTAL_REPORT_NAME.isEmpty()) {
            row.append(",").append(totalVal);
        }
        if (!TRAIN_SURFACE_PRESSURE_REPORT_NAME.isEmpty()) {
            row.append(",").append(trainSurfacePressureVal);
        }
        row.append(",").append(maxResidualVal);
        pw.println(row.toString());
    }

    private double getMaxResidualValue(Simulation sim) {
        double maxResidual = Double.NaN;
        maxResidual = updateAbsMax(
            maxResidual,
            tryReadResidualMonitorValue(sim, new String[] {"CONTINUITY"}, new String[] {"RESIDUAL"})
        );
        maxResidual = updateAbsMax(
            maxResidual,
            tryReadResidualMonitorValue(sim, new String[] {"XMOMENTUM"}, new String[] {"RESIDUAL"})
        );
        maxResidual = updateAbsMax(
            maxResidual,
            tryReadResidualMonitorValue(sim, new String[] {"YMOMENTUM"}, new String[] {"RESIDUAL"})
        );
        maxResidual = updateAbsMax(
            maxResidual,
            tryReadResidualMonitorValue(sim, new String[] {"ZMOMENTUM"}, new String[] {"RESIDUAL"})
        );
        maxResidual = updateAbsMax(
            maxResidual,
            tryReadResidualMonitorValue(sim, new String[] {"TKE", "TURBULENTKINETIC"}, new String[] {"RESIDUAL"})
        );
        maxResidual = updateAbsMax(
            maxResidual,
            tryReadResidualMonitorValue(sim, new String[] {"SDR", "SPECIFICDISSIPATION"}, new String[] {"RESIDUAL"})
        );
        maxResidual = updateAbsMax(
            maxResidual,
            tryReadResidualMonitorValue(sim, new String[] {"ENERGY", "TEMPERATURE"}, new String[] {"RESIDUAL"})
        );
        if (!Double.isNaN(maxResidual)) {
            return maxResidual;
        }

        for (Object obj : sim.getMonitorManager().getObjects()) {
            String name = safePresentationName(obj);
            if (name == null || name.isEmpty()) continue;
            if (!isResidualMonitorCandidate(name)) continue;

            Double candidate = extractNumericMonitorValue(obj);
            if (candidate == null || isInvalidReportValue(candidate.doubleValue())) {
                continue;
            }

            double absValue = Math.abs(candidate.doubleValue());
            if (Double.isNaN(maxResidual) || absValue > maxResidual) {
                maxResidual = absValue;
            }
        }
        return maxResidual;
    }

    private double updateAbsMax(double currentMax, Double candidate) {
        if (candidate == null || isInvalidReportValue(candidate.doubleValue())) {
            return currentMax;
        }
        double absValue = Math.abs(candidate.doubleValue());
        if (Double.isNaN(currentMax) || absValue > currentMax) {
            return absValue;
        }
        return currentMax;
    }

    private boolean isResidualMonitorCandidate(String name) {
        String normalized = normalizeLabel(name);
        if (normalized.isEmpty()) {
            return false;
        }
        if (normalized.contains("RESIDUAL")) {
            return true;
        }
        return normalized.contains("CONTINUITY")
            || normalized.contains("XMOMENTUM")
            || normalized.contains("YMOMENTUM")
            || normalized.contains("ZMOMENTUM")
            || normalized.contains("TKE")
            || normalized.contains("TURBULENTKINETIC")
            || normalized.contains("SDR")
            || normalized.contains("SPECIFICDISSIPATION")
            || normalized.contains("ENERGY")
            || normalized.contains("TEMPERATURE");
    }

    private String[][] dropResidualOnlyTokenGroups(String[]... tokenGroups) {
        if (tokenGroups == null || tokenGroups.length == 0) {
            return null;
        }
        ArrayList<String[]> relaxed = new ArrayList<String[]>();
        boolean removedResidualGroup = false;
        for (String[] group : tokenGroups) {
            if (isResidualOnlyTokenGroup(group)) {
                removedResidualGroup = true;
                continue;
            }
            relaxed.add(group);
        }
        if (!removedResidualGroup || relaxed.isEmpty()) {
            return null;
        }
        return relaxed.toArray(new String[relaxed.size()][]);
    }

    private boolean isResidualOnlyTokenGroup(String[] group) {
        if (group == null || group.length == 0) {
            return false;
        }
        for (String token : group) {
            String normalizedToken = normalizeLabel(token);
            if (
                !normalizedToken.equals("RESIDUAL")
                && !normalizedToken.equals("RES")
            ) {
                return false;
            }
        }
        return true;
    }

    private Double extractNumericMonitorValue(Object obj) {
        String[] methodNames = new String[] {
            "getReportMonitorValue",
            "getMonitorValue",
            "getValue",
            "getCurrentValue",
            "getLatestValue",
            "getLastValue",
            "getCurrentMonitorValue"
        };
        for (String methodName : methodNames) {
            try {
                Object raw = obj.getClass().getMethod(methodName).invoke(obj);
                Double numeric = tryExtractNumericValue(raw, 4);
                if (numeric != null) {
                    return numeric;
                }
                Double latestSample = tryExtractLatestNumericSample(raw, 4);
                if (latestSample != null) {
                    return latestSample;
                }
            } catch (Exception ignored) {}
        }
        Object nestedData = tryInvokeNoArg(
            obj,
            "getMonitorData",
            "getDataSet",
            "getSamples",
            "getHistory",
            "getData"
        );
        if (nestedData != null && nestedData != obj) {
            Double latestSample = tryExtractLatestNumericSample(nestedData, 4);
            if (latestSample != null) {
                return latestSample;
            }
        }
        Double heuristic = tryReadMonitorValueByHeuristic(obj);
        if (heuristic != null) {
            return heuristic;
        }
        return null;
    }

    private Double tryExtractLatestNumericSample(Object raw, int depth) {
        if (raw == null || depth < 0) {
            return null;
        }
        Double direct = tryExtractNumericValue(raw, depth);
        if (direct != null) {
            return direct;
        }
        if (raw instanceof Iterable) {
            Double latest = null;
            for (Object item : (Iterable<?>) raw) {
                Double candidate = tryExtractLatestNumericSample(item, depth - 1);
                if (candidate != null) {
                    latest = candidate;
                }
            }
            if (latest != null) {
                return latest;
            }
        }
        if (raw.getClass().isArray()) {
            int length = java.lang.reflect.Array.getLength(raw);
            for (int index = length - 1; index >= 0; index--) {
                Object item = java.lang.reflect.Array.get(raw, index);
                Double candidate = tryExtractLatestNumericSample(item, depth - 1);
                if (candidate != null) {
                    return candidate;
                }
            }
        }
        Object nested = tryInvokeNoArg(
            raw,
            "getYValues",
            "getValues",
            "getData",
            "getDataSet",
            "getSeries",
            "getDependentValues",
            "getOrdinateValues",
            "getSamples",
            "getMonitorData"
        );
        if (nested != null && nested != raw) {
            Double candidate = tryExtractLatestNumericSample(nested, depth - 1);
            if (candidate != null) {
                return candidate;
            }
        }
        try {
            Method sizeMethod = raw.getClass().getMethod("size");
            Method getMethod = raw.getClass().getMethod("get", int.class);
            Object sizeObj = sizeMethod.invoke(raw);
            int size = ((Number) sizeObj).intValue();
            for (int index = size - 1; index >= 0; index--) {
                Object item = getMethod.invoke(raw, Integer.valueOf(index));
                Double candidate = tryExtractLatestNumericSample(item, depth - 1);
                if (candidate != null) {
                    return candidate;
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private String safePresentationName(Object obj) {
        try {
            Object raw = obj.getClass().getMethod("getPresentationName").invoke(obj);
            if (raw != null) return raw.toString();
        } catch (Exception ignored) {}
        return "";
    }

    private ArrayList<String> buildTrainBoundaryPrefixes() {
        ArrayList<String> prefixes = new ArrayList<String>();
        String configured = TRAIN_BC == null ? "" : TRAIN_BC.trim();
        if (configured.isEmpty()) {
            return prefixes;
        }

        if (configured.endsWith(".Faces") && configured.length() > ".Faces".length()) {
            prefixes.add(
                configured.substring(0, configured.length() - ".Faces".length()) + "."
            );
        }

        int lastDot = configured.lastIndexOf('.');
        if (lastDot > 0) {
            prefixes.add(configured.substring(0, lastDot + 1));
        }

        ArrayList<String> deduped = new ArrayList<String>();
        for (String prefix : prefixes) {
            if (prefix == null) continue;
            String trimmed = prefix.trim();
            if (trimmed.isEmpty() || deduped.contains(trimmed)) continue;
            deduped.add(trimmed);
        }
        return deduped;
    }

    private boolean matchesAnyPrefix(String value, ArrayList<String> prefixes) {
        if (value == null || prefixes == null || prefixes.isEmpty()) {
            return false;
        }
        for (String prefix : prefixes) {
            if (prefix != null && !prefix.isEmpty() && value.startsWith(prefix)) {
                return true;
            }
        }
        return false;
    }

    private String joinBoundaryNames(ArrayList<Boundary> boundaries) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < boundaries.size(); i++) {
            if (i > 0) sb.append(", ");
            sb.append(safePresentationName(boundaries.get(i)));
        }
        return sb.toString();
    }

    private ArrayList<Boundary> resolveTrainBoundaries(Simulation sim) {
        ArrayList<Boundary> boundaries = new ArrayList<Boundary>();
        if (TRAIN_BC == null || TRAIN_BC.trim().isEmpty()) {
            return boundaries;
        }

        Region region = sim.getRegionManager().getRegion(REGION_NAME);
        try {
            Boundary exact = region.getBoundaryManager().getBoundary(TRAIN_BC);
            if (exact != null) {
                boundaries.add(exact);
                return boundaries;
            }
        } catch (Exception ignored) {}

        ArrayList<String> prefixes = buildTrainBoundaryPrefixes();
        if (prefixes.isEmpty()) {
            return boundaries;
        }

        for (Object obj : region.getBoundaryManager().getObjects()) {
            if (!(obj instanceof Boundary)) continue;
            Boundary boundary = (Boundary) obj;
            String name = safePresentationName(boundary);
            if (!matchesAnyPrefix(name, prefixes)) continue;
            boundaries.add(boundary);
        }

        if (!boundaries.isEmpty()) {
            sim.println(
                "Resolved train boundary fallback for '" + TRAIN_BC + "' to "
                + boundaries.size() + " boundaries: " + joinBoundaryNames(boundaries)
            );
        }
        return boundaries;
    }

    private void setBoundaryParts(ForceReport report, ArrayList<Boundary> boundaries) {
        report.getParts().setQuery(null);
        if (boundaries.size() == 1) {
            report.getParts().setObjects(boundaries.get(0));
            return;
        }
        report.getParts().setObjects(boundaries);
    }

    private void setBoundaryParts(MaxReport report, ArrayList<Boundary> boundaries) {
        report.getParts().setQuery(null);
        if (boundaries.size() == 1) {
            report.getParts().setObjects(boundaries.get(0));
            return;
        }
        report.getParts().setObjects(boundaries);
    }

    private void setBoundaryParts(MassFlowReport report, Boundary boundary) {
        report.getParts().setQuery(null);
        report.getParts().setObjects(boundary);
    }

    private void setRegionParts(MaxReport report, Region region) {
        report.getParts().setQuery(null);
        report.getParts().setObjects(region);
    }

    private void setRegionParts(VolumeAverageReport report, Region region) {
        report.getParts().setQuery(null);
        report.getParts().setObjects(region);
    }

    private void ensureDragReport(Simulation sim) {
        try {
            if (TRAIN_BC.isEmpty()) {
                sim.println("TRAIN_BC is empty; drag report setup skipped.");
                return;
            }

            ArrayList<Boundary> trainBoundaries = resolveTrainBoundaries(sim);
            if (trainBoundaries.isEmpty()) {
                sim.println(
                    "Unable to ensure drag report: no train boundaries resolved for '" + TRAIN_BC + "'."
                );
                return;
            }
            ForceReport dragReport =
                getOrCreateReport(sim, DRAG_REPORT_NAME, ForceReport.class);
            setBoundaryParts(dragReport, trainBoundaries);
            double yawRad = Math.toRadians(YAW_ANGLE_DEG);
            double dragDirX = Math.cos(yawRad);
            double dragDirY = Math.sin(yawRad);
            dragReport.getDirection().setComponents(dragDirX, dragDirY, 0.0);
            sim.println("Drag report '" + DRAG_REPORT_NAME
                        + "' ensured as ForceReport on "
                        + trainBoundaries.size() + " train boundary/boundaries derived from '" + TRAIN_BC
                        + "' with direction (" + dragDirX + ", " + dragDirY
                        + ", 0.0).");
            logReportDiagnostics(sim, DRAG_REPORT_NAME, "after ensureDragReport");
        } catch (Exception e) {
            sim.println("Unable to ensure drag report: " + e.getMessage());
        }
    }

    private void ensureTotalReport(Simulation sim) {
        try {
            if (TOTAL_REPORT_NAME.isEmpty()) {
                return;
            }
            if (TRAIN_BC.isEmpty()) {
                sim.println("TRAIN_BC is empty; total report setup skipped.");
                return;
            }

            ArrayList<Boundary> trainBoundaries = resolveTrainBoundaries(sim);
            if (trainBoundaries.isEmpty()) {
                sim.println(
                    "Unable to ensure total report: no train boundaries resolved for '" + TRAIN_BC + "'."
                );
                return;
            }
            ForceReport totalReport =
                getOrCreateReport(sim, TOTAL_REPORT_NAME, ForceReport.class);
            setBoundaryParts(totalReport, trainBoundaries);
            double yawRad = Math.toRadians(YAW_ANGLE_DEG);
            double dragDirX = Math.cos(yawRad);
            double dragDirY = Math.sin(yawRad);
            totalReport.getDirection().setComponents(dragDirX, dragDirY, 0.0);
            sim.println("Total report '" + TOTAL_REPORT_NAME
                        + "' ensured as ForceReport on "
                        + trainBoundaries.size() + " train boundary/boundaries derived from '" + TRAIN_BC
                        + "' with direction (" + dragDirX + ", " + dragDirY
                        + ", 0.0).");
            logReportDiagnostics(sim, TOTAL_REPORT_NAME, "after ensureTotalReport");
        } catch (Exception e) {
            sim.println("Unable to ensure total report: " + e.getMessage());
        }
    }

    private void ensurePressureReports(Simulation sim) {
        try {
            Region region = sim.getRegionManager().getRegion(REGION_NAME);
            PrimitiveFieldFunction pressureField =
                ((PrimitiveFieldFunction) sim.getFieldFunctionManager()
                    .getFunction("Pressure"));

            if (!OUTLET_BC.isEmpty()) {
                Boundary outlet = region.getBoundaryManager().getBoundary(OUTLET_BC);
                AreaAverageReport outletReport =
                    getOrCreateReport(sim, OUTLET_PRESSURE_REPORT_NAME,
                                      AreaAverageReport.class);
                outletReport.setFieldFunction(pressureField);
                outletReport.getParts().setQuery(null);
                outletReport.getParts().setObjects(outlet);
            }

            if (!TRAIN_SURFACE_PRESSURE_REPORT_NAME.isEmpty() && !TRAIN_BC.isEmpty()) {
                ArrayList<Boundary> trainBoundaries = resolveTrainBoundaries(sim);
                if (trainBoundaries.isEmpty()) {
                    sim.println(
                        "Unable to ensure pressure reports: no train boundaries resolved for '"
                        + TRAIN_BC + "'."
                    );
                    return;
                }
                MaxReport trainReport =
                    getOrCreateReport(sim, TRAIN_SURFACE_PRESSURE_REPORT_NAME,
                                      MaxReport.class);
                trainReport.setFieldFunction(pressureField);
                setBoundaryParts(trainReport, trainBoundaries);
            }
        } catch (Exception e) {
            sim.println("Unable to ensure pressure reports: " + e.getMessage());
        }
    }

    private void ensureAuxiliaryProfilingReports(Simulation sim) {
        ensureMassFlowReports(sim);
        ensureCflReports(sim);
    }

    private void ensureMassFlowReports(Simulation sim) {
        if (INLET_BC.isEmpty() || OUTLET_BC.isEmpty()) {
            return;
        }
        try {
            Region region = sim.getRegionManager().getRegion(REGION_NAME);
            Boundary inlet = region.getBoundaryManager().getBoundary(INLET_BC);
            Boundary outlet = region.getBoundaryManager().getBoundary(OUTLET_BC);

            MassFlowReport inletReport =
                getOrCreateReport(sim, INLET_MASS_FLOW_REPORT_NAME, MassFlowReport.class);
            setBoundaryParts(inletReport, inlet);

            MassFlowReport outletReport =
                getOrCreateReport(sim, OUTLET_MASS_FLOW_REPORT_NAME, MassFlowReport.class);
            setBoundaryParts(outletReport, outlet);
        } catch (Exception e) {
            sim.println("Unable to ensure mass-flow reports: " + e.getMessage());
        }
    }

    private void ensureCflReports(Simulation sim) {
        try {
            Region region = sim.getRegionManager().getRegion(REGION_NAME);
            FieldFunction courantField = tryFindCourantFieldFunction(sim);
            if (courantField == null) {
                cflProfilingStatus = CFL_STATUS_NOT_AVAILABLE_FOR_CURRENT_SOLVER_MODEL;
                cflFieldFunctionName = null;
                removeCflReports(sim);
                sim.println(
                    "Courant field function not found for the current solver/model; "
                    + "CFL profiling marked unavailable."
                );
                return;
            }
            cflFieldFunctionName = safePresentationName(courantField);

            MaxReport maxCflReport =
                getOrCreateReport(sim, CFL_MAX_REPORT_NAME, MaxReport.class);
            maxCflReport.setFieldFunction(courantField);
            setRegionParts(maxCflReport, region);
            if (!hasConfiguredFieldFunction(maxCflReport)) {
                cflProfilingStatus = CFL_STATUS_NOT_AVAILABLE_MAX_REPORT_FIELD_NOT_BINDABLE;
                removeCflReports(sim);
                sim.println(
                    "Unable to bind the CFL field function to the max report; "
                    + "CFL profiling marked unavailable."
                );
                return;
            }

            VolumeAverageReport meanCflReport =
                getOrCreateReport(sim, CFL_MEAN_REPORT_NAME, VolumeAverageReport.class);
            meanCflReport.setFieldFunction(courantField);
            setRegionParts(meanCflReport, region);
            if (!hasConfiguredFieldFunction(meanCflReport)) {
                cflProfilingStatus = CFL_STATUS_NOT_AVAILABLE_MEAN_REPORT_FIELD_NOT_BINDABLE;
                removeCflReports(sim);
                sim.println(
                    "Unable to bind the CFL field function to the mean report; "
                    + "CFL profiling marked unavailable."
                );
                return;
            }
            cflProfilingStatus = CFL_STATUS_AVAILABLE_AND_ENABLED;
        } catch (Exception e) {
            cflProfilingStatus = CFL_STATUS_SETUP_FAILED;
            removeCflReports(sim);
            sim.println("Unable to ensure CFL reports: " + e.getMessage());
        }
    }

    private FieldFunction tryFindCourantFieldFunction(Simulation sim) {
        String[] exactNames = new String[] {
            "Courant Number",
            "CourantNumber",
            "CFL",
            "Courant"
        };
        for (String exactName : exactNames) {
            try {
                Object function = sim.getFieldFunctionManager().getFunction(exactName);
                if (isUsableCourantFieldFunctionCandidate(function)) {
                    if (cflFieldFunctionName == null || cflFieldFunctionName.isEmpty()) {
                        cflFieldFunctionName = safePresentationName(function);
                        if (cflFieldFunctionName == null || cflFieldFunctionName.isEmpty()) {
                            cflFieldFunctionName = exactName;
                        }
                    }
                    return (FieldFunction) function;
                }
            } catch (Exception ignored) {}
        }
        try {
            for (Object obj : sim.getFieldFunctionManager().getObjects()) {
                if (isUsableCourantFieldFunctionCandidate(obj)) {
                    if (cflFieldFunctionName == null || cflFieldFunctionName.isEmpty()) {
                        cflFieldFunctionName = safePresentationName(obj);
                    }
                    return (FieldFunction) obj;
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private String describeCourantFieldFunctionCandidates(Simulation sim, int limit) {
        LinkedHashSet<String> names = new LinkedHashSet<String>();
        try {
            for (Object obj : sim.getFieldFunctionManager().getObjects()) {
                if (isUsableCourantFieldFunctionCandidate(obj)) {
                    names.add(describeCourantFieldFunction(obj));
                }
                if (limit > 0 && names.size() >= limit) {
                    break;
                }
            }
        } catch (Exception ignored) {}
        if (names.isEmpty()) {
            return "<none>";
        }
        StringBuilder sb = new StringBuilder();
        int idx = 0;
        for (String name : names) {
            if (idx > 0) sb.append(", ");
            sb.append(name);
            idx += 1;
        }
        return sb.toString();
    }

    private String describeRejectedCourantFieldFunctionCandidates(Simulation sim, int limit) {
        LinkedHashSet<String> names = new LinkedHashSet<String>();
        try {
            for (Object obj : sim.getFieldFunctionManager().getObjects()) {
                String reason = getCourantFieldFunctionRejectionReason(obj);
                if (reason == null) {
                    continue;
                }
                names.add(describeCourantFieldFunction(obj) + " [" + reason + "]");
                if (limit > 0 && names.size() >= limit) {
                    break;
                }
            }
        } catch (Exception ignored) {}
        if (names.isEmpty()) {
            return "<none>";
        }
        StringBuilder sb = new StringBuilder();
        int idx = 0;
        for (String name : names) {
            if (idx > 0) sb.append(", ");
            sb.append(name);
            idx += 1;
        }
        return sb.toString();
    }

    private boolean isUsableCourantFieldFunctionCandidate(Object obj) {
        return getCourantFieldFunctionRejectionReason(obj) == null;
    }

    private String getCourantFieldFunctionRejectionReason(Object obj) {
        if (!(obj instanceof FieldFunction)) {
            return "not_field_function";
        }
        String combined = safePresentationName(obj) + " " + obj.getClass().getName();
        String normalized = normalizeLabel(combined);
        if (
            !normalized.contains("COURANT")
            && !normalized.equals("CFL")
            && !normalized.contains("CFL")
        ) {
            return "not_cfl_like";
        }
        String presentation = safePresentationName(obj);
        String className = obj.getClass().getName();
        String normalizedPresentation = normalizeLabel(presentation);
        String normalizedClass = normalizeLabel(className);
        if (
            presentation != null
            && (presentation.contains("__profiling_cfl_") || presentation.startsWith("Report: __profiling_cfl_"))
        ) {
            return "report_derived_field";
        }
        if (normalizedClass.contains("REPORTFIELDFUNCTION")) {
            return "report_derived_field";
        }
        if (
            presentation != null
            && (presentation.contains("选择函数") || presentation.equalsIgnoreCase("<Select Function>"))
        ) {
            return "placeholder_selection";
        }
        if (normalizedPresentation.isEmpty()) {
            return "empty_presentation_name";
        }
        return null;
    }

    private String describeCourantFieldFunction(Object obj) {
        if (obj == null) {
            return "<null>";
        }
        String name = safePresentationName(obj);
        if (name == null || name.trim().isEmpty()) {
            name = "<unnamed>";
        }
        return name + " [" + obj.getClass().getName() + "]";
    }

    private void removeCflReports(Simulation sim) {
        removeReportIfExists(sim, CFL_MAX_REPORT_NAME);
        removeReportIfExists(sim, CFL_MEAN_REPORT_NAME);
        removeMonitorIfExists(sim, CFL_MAX_REPORT_NAME + " Monitor");
        removeMonitorIfExists(sim, CFL_MEAN_REPORT_NAME + " Monitor");
    }

    private boolean hasConfiguredFieldFunction(Object report) {
        if (report == null) {
            return false;
        }
        Object field = tryInvokeNoArg(report, "getFieldFunction", "getField");
        return field != null;
    }

    private void ensurePrimaryReportMonitors(Simulation sim) {
        Report dragReport = null;
        Report totalReport = null;
        Report pressureReport = null;

        try {
            dragReport = (Report) sim.getReportManager().getObject(DRAG_REPORT_NAME);
        } catch (Exception e) {
            sim.println("Unable to get drag report for monitor setup: " + e.getMessage());
        }
        if (!TOTAL_REPORT_NAME.isEmpty()) {
            try {
                totalReport = (Report) sim.getReportManager().getObject(TOTAL_REPORT_NAME);
            } catch (Exception e) {
                sim.println("Unable to get total report for monitor setup: " + e.getMessage());
            }
        }
        if (!TRAIN_SURFACE_PRESSURE_REPORT_NAME.isEmpty()) {
            try {
                pressureReport =
                    (Report) sim.getReportManager().getObject(TRAIN_SURFACE_PRESSURE_REPORT_NAME);
            } catch (Exception e) {
                sim.println("Unable to get pressure report for monitor setup: " + e.getMessage());
            }
        }

        removeMonitorIfExists(sim, DRAG_REPORT_NAME + " Monitor");
        if (!TOTAL_REPORT_NAME.isEmpty()) {
            removeMonitorIfExists(sim, TOTAL_REPORT_NAME + " Monitor");
        }
        if (!TRAIN_SURFACE_PRESSURE_REPORT_NAME.isEmpty()) {
            removeMonitorIfExists(sim, TRAIN_SURFACE_PRESSURE_REPORT_NAME + " Monitor");
        }
        removePlotIfExists(sim, "Reports Plot");

        ArrayList<Object> reports = new ArrayList<Object>();
        if (dragReport != null) reports.add(dragReport);
        if (totalReport != null) reports.add(totalReport);
        if (pressureReport != null) reports.add(pressureReport);
        if (reports.isEmpty()) {
            sim.println("Primary report monitor setup skipped because no reports were available.");
            return;
        }

        try {
            sim.getMonitorManager().createMonitorAndPlot(
                new NeoObjectVector(reports.toArray()),
                true,
                "Reports Plot");
        } catch (Exception e) {
            sim.println("Unable to create primary report monitors: " + e.getMessage());
            return;
        }

        configureReportMonitor(sim, DRAG_REPORT_NAME + " Monitor");
        if (!TOTAL_REPORT_NAME.isEmpty()) {
            configureReportMonitor(sim, TOTAL_REPORT_NAME + " Monitor");
        }
        configureReportMonitor(sim, TRAIN_SURFACE_PRESSURE_REPORT_NAME + " Monitor");
    }

    private void removeLegacyReportArtifacts(Simulation sim) {
        removeMonitorIfExists(sim, "pressure");
        removeMonitorIfExists(sim, "pressure Monitor");
        removeMonitorIfExists(sim, OUTLET_PRESSURE_REPORT_NAME + " Monitor");
        removePlotIfExists(sim, "Reports Plot");
        removeReportIfExists(sim, "pressure");
        removeReportIfExists(sim, OUTLET_PRESSURE_REPORT_NAME);
    }

    private void removeMonitorIfExists(Simulation sim, String monitorName) {
        try {
            Object existing = sim.getMonitorManager().getMonitor(monitorName);
            if (existing != null) {
                sim.getMonitorManager().removeObjects(
                    new NeoObjectVector(new Object[] {existing}));
                sim.println("Removed existing monitor '" + monitorName + "'.");
            }
        } catch (Exception ignored) {}
    }

    private void removePlotIfExists(Simulation sim, String plotName) {
        try {
            StarPlot existing = sim.getPlotManager().getPlot(plotName);
            if (existing != null) {
                sim.getPlotManager().removeObjects(
                    new NeoObjectVector(new Object[] {existing}));
                sim.println("Removed existing plot '" + plotName + "'.");
            }
        } catch (Exception ignored) {}
    }

    private void removeReportIfExists(Simulation sim, String reportName) {
        try {
            Report existing = (Report) sim.getReportManager().getObject(reportName);
            if (existing != null) {
                sim.getReportManager().removeObjects(
                    new NeoObjectVector(new Object[] {existing}));
                sim.println("Removed existing report '" + reportName + "'.");
            }
        } catch (Exception ignored) {}
    }

    private void configureReportMonitor(Simulation sim, String monitorName) {
        try {
            ReportMonitor monitor =
                (ReportMonitor) sim.getMonitorManager().getMonitor(monitorName);
            StarUpdate su = monitor.getStarUpdate();
            try {
                su.getUpdateModeOption()
                  .setSelected(StarUpdateModeOption.Type.ITERATION);
            } catch (Exception e) {
                sim.println("Unable to force report monitor iteration mode: "
                            + e.getMessage());
            }
            su.getIterationUpdateFrequency().setStart(MONITOR_START_ITER);
            su.getIterationUpdateFrequency().setIterations(MONITOR_UPDATE_FREQ);
            sim.println("Report monitor '" + monitorName
                        + "' configured for iteration updates.");
        } catch (Exception e) {
            sim.println("Unable to configure report monitor '" + monitorName
                        + "': " + e.getMessage());
        }
    }

    private <T extends Report> T getOrCreateReport(
            Simulation sim, String reportName, Class<T> reportType) {
        Report existing = null;
        try {
            existing = (Report) sim.getReportManager().getObject(reportName);
        } catch (Exception ignored) {}

        if (existing != null && reportType.isInstance(existing)) {
            return reportType.cast(existing);
        }

        if (existing != null) {
            sim.println("Replacing report '" + reportName + "' because it is a "
                        + existing.getClass().getSimpleName() + ", not "
                        + reportType.getSimpleName() + ".");
            sim.getReportManager().removeObjects(
                new NeoObjectVector(new Object[] {existing}));
        }

        T created = sim.getReportManager().createReport(reportType);
        created.setPresentationName(reportName);
        return created;
    }

    private double getSafeReportValue(Simulation sim, String reportName) {
        Report report = (Report) sim.getReportManager().getObject(reportName);
        double val = Double.NaN;
        double scalarVal = Double.NaN;
        double monitorVal = Double.NaN;

        if (report instanceof ScalarReport) {
            try {
                scalarVal = ((ScalarReport) report).getValue();
                val = scalarVal;
            } catch (Exception ignored) {}
        }

        if (isInvalidReportValue(val)) {
            try {
                monitorVal = report.getReportMonitorValue();
                val = monitorVal;
            } catch (Exception ignored) {}
        } else {
            try {
                monitorVal = report.getReportMonitorValue();
            } catch (Exception ignored) {}
        }

        maybeLogReportValueDebug(sim, reportName, report, scalarVal, monitorVal, val);

        if (isInvalidReportValue(val)) {
            sim.println("Report '" + reportName
                        + "' has no valid numeric value yet; writing NaN instead.");
            return Double.NaN;
        }
        return val;
    }

    private boolean isInvalidReportValue(double val) {
        return Double.isNaN(val)
            || Double.isInfinite(val)
            || val <= (-Double.MAX_VALUE / 2.0);
    }

    private void logReportDiagnostics(Simulation sim, String reportName, String context) {
        try {
            Report report = (Report) sim.getReportManager().getObject(reportName);
            String typeName = report == null ? "null" : report.getClass().getSimpleName();
            double scalarVal = Double.NaN;
            double monitorVal = Double.NaN;
            if (report instanceof ScalarReport) {
                try {
                    scalarVal = ((ScalarReport) report).getValue();
                } catch (Exception ignored) {}
            }
            if (report != null) {
                try {
                    monitorVal = report.getReportMonitorValue();
                } catch (Exception ignored) {}
            }
            sim.println("Report diagnostics [" + context + "] '" + reportName
                        + "': type=" + typeName
                        + ", scalarValue=" + scalarVal
                        + ", monitorValue=" + monitorVal);
        } catch (Exception e) {
            sim.println("Unable to inspect report '" + reportName + "': " + e.getMessage());
        }
    }

    private void maybeLogReportValueDebug(
            Simulation sim,
            String reportName,
            Report report,
            double scalarVal,
            double monitorVal,
            double chosenVal) {
        if (!reportName.equals(DRAG_REPORT_NAME) || dragReportDebugLogged) {
            return;
        }
        String typeName = report == null ? "null" : report.getClass().getSimpleName();
        sim.println("Drag report debug: type=" + typeName
                    + ", scalarValue=" + scalarVal
                    + ", monitorValue=" + monitorVal
                    + ", chosenValue=" + chosenVal);
        dragReportDebugLogged = true;
    }

    private void setDomainSize(Simulation sim) {
        Units units = sim.getUnitsManager().getPreferredUnits(
            Dimensions.Builder().length(1).build());
        for (Object obj : sim.get(SimulationPartManager.class).getObjects()) {
            if (!(obj instanceof SimpleBlockPart)) continue;
            SimpleBlockPart block = (SimpleBlockPart) obj;
            if (!block.getPresentationName().equals(DOMAIN_BLOCK_NAME)) continue;
            try {
                block.getCorner1().setCoordinate(
                    units, units, units, new DoubleVector(DOMAIN_CORNER1));
                block.getCorner2().setCoordinate(
                    units, units, units, new DoubleVector(DOMAIN_CORNER2));
                block.rebuildSimpleShapePart();
            } catch (Exception e) {
                sim.println("WARNING: Domain block resize failed: " + e.getMessage());
            }
            break;
        }
    }

    private void setInitialConditions(Simulation sim) {
        if (INITIAL_VELOCITY == 0.0) return;
        double yawRad = Math.toRadians(YAW_ANGLE_DEG);
        double vx = INITIAL_VELOCITY * Math.cos(yawRad);
        double vy = INITIAL_VELOCITY * Math.sin(yawRad);
        for (Object obj : sim.getContinuumManager().getObjects()) {
            if (!(obj instanceof PhysicsContinuum)) continue;
            PhysicsContinuum phys = (PhysicsContinuum) obj;
            VelocityProfile vp =
                phys.getInitialConditions().get(VelocityProfile.class);
            vp.getMethod(ConstantVectorProfileMethod.class)
              .getQuantity().setComponents(vx, vy, 0.0);
            break;
        }
    }

    private void setWallTreatment(Simulation sim) {
        for (Object obj : sim.getContinuumManager().getObjects()) {
            if (!(obj instanceof PhysicsContinuum)) continue;
            PhysicsContinuum phys = (PhysicsContinuum) obj;
            try {
                if (WALL_TREATMENT.equals("all-y-plus")) {
                    phys.enable(KwAllYplusWallTreatment.class);
                } else if (WALL_TREATMENT.equals("high-y-plus")) {
                    phys.enable(KwHighYplusWallTreatment.class);
                } else if (WALL_TREATMENT.equals("low-y-plus")) {
                    phys.enable(KwLowYplusWallTreatment.class);
                }
            } catch (Exception e) {
                sim.println("Wall treatment setting skipped: " + e.getMessage());
            }
            break;
        }
    }

    private void setMonitors(Simulation sim) {
        for (Object obj : sim.getMonitorManager().getObjects()) {
            StarUpdate su = null;
            if (obj instanceof FieldMeanMonitor) {
                FieldMeanMonitor fm = (FieldMeanMonitor) obj;
                su = fm.getStarUpdate();
            } else if (obj instanceof ReportMonitor) {
                su = ((ReportMonitor) obj).getStarUpdate();
            }
            if (su != null) {
                try {
                    su.getUpdateModeOption()
                      .setSelected(StarUpdateModeOption.Type.ITERATION);
                } catch (Exception e) {
                    sim.println("Unable to force monitor iteration update mode: "
                                + e.getMessage());
                }
                su.getIterationUpdateFrequency().setStart(MONITOR_START_ITER);
                su.getIterationUpdateFrequency().setIterations(MONITOR_UPDATE_FREQ);
            }
        }
    }

    private void exportResults(Simulation sim) {
        String csvPath = resolvePath(OUTPUT_DIR + "/result_reports.csv");
        try {
            PrintWriter pw = new PrintWriter(new FileWriter(csvPath));
            pw.println("report_name,value,units");
            for (Object obj : sim.getReportManager().getObjects()) {
                if (!(obj instanceof Report)) continue;
                Report r = (Report) obj;
                String name = r.getPresentationName();
                String units = "";
                double val;
                try {
                    val = getSafeReportValue(sim, name);
                    try { units = r.getUnits().toString(); } catch (Exception ignored) {}
                } catch (Exception e) {
                    sim.println("Skipping report '" + name + "': " + e.getMessage());
                    continue;
                }
                pw.println(name + "," + val + "," + units);
            }
            pw.close();
        } catch (IOException e) {
            sim.println("ERROR writing reports: " + e.getMessage());
        }
    }
}
