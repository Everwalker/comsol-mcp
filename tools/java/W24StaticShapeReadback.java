import com.comsol.model.GeomSequence;
import com.comsol.model.Material;
import com.comsol.model.Model;
import com.comsol.model.Study;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverFeatureList;
import com.comsol.model.SolverSequence;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.MultiphysicsCoupling;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Read-only native checks and immutable save for an unsolved W24 shape model. */
public final class W24StaticShapeReadback {
    private static final String COMPONENT = "comp1";
    private static final String GEOMETRY = "geom1";

    private W24StaticShapeReadback() { }

    public static Object run(Model model, Map<String, Object> args) throws IOException {
        String action = String.valueOf(args.getOrDefault("action", "readback"));
        if ("readback".equals(action)) return readback(model);
        if ("save".equals(action)) return saveUnsolved(model, args);
        throw new IllegalArgumentException("action must be exactly readback or save");
    }

    private static Map<String, Object> readback(Model model) {
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        int[] glueIds = model.component(COMPONENT).selection("sel_glue_domain").entities(2);
        int[] gasIds = model.component(COMPONENT).selection("sel_gas_domain").entities(2);
        if (geom.getSDim() != 2 || !geom.isAxisymmetric() || geom.getNDomains() != 2 ||
            glueIds.length != 1 || gasIds.length != 1 || glueIds[0] == gasIds[0]) {
            throw new IllegalStateException("saved static-shape model lost its two-domain axisymmetric geometry");
        }
        int[] allDomains = sortedUnion(glueIds, gasIds);

        String[] selectionTags = model.component(COMPONENT).selection().tags();
        Map<String, int[]> wetting = new LinkedHashMap<>();
        String caseId;
        if (contains(selectionTags, "sel_wet_flat_base")) {
            caseId = "flat";
            wetting.put("sel_wet_flat_base",
                model.component(COMPONENT).selection("sel_wet_flat_base").entities(1));
        } else if (contains(selectionTags, "sel_wet_mesa_top") &&
                   contains(selectionTags, "sel_wet_mesa_side") &&
                   contains(selectionTags, "sel_wet_lower_base")) {
            caseId = "step";
            wetting.put("sel_wet_mesa_top",
                model.component(COMPONENT).selection("sel_wet_mesa_top").entities(1));
            wetting.put("sel_wet_mesa_side",
                model.component(COMPONENT).selection("sel_wet_mesa_side").entities(1));
            wetting.put("sel_wet_lower_base",
                model.component(COMPONENT).selection("sel_wet_lower_base").entities(1));
        } else {
            throw new IllegalStateException("saved shape model has neither the exact flat nor stepped wetting selections");
        }
        Set<Integer> wetIds = new HashSet<>();
        Map<String, Object> wettingReadback = new LinkedHashMap<>();
        for (Map.Entry<String, int[]> entry : wetting.entrySet()) {
            int[] ids = entry.getValue();
            if (ids.length == 0) throw new IllegalStateException("saved substrate selection is empty: " + entry.getKey());
            for (int id : ids) {
                if (!wetIds.add(id)) throw new IllegalStateException("saved substrate selections overlap at boundary " + id);
            }
            wettingReadback.put(entry.getKey(), boxed(ids));
        }
        int[] axisIds = model.component(COMPONENT).selection("sel_axis").entities(1);
        for (int id : axisIds) {
            if (wetIds.contains(id)) throw new IllegalStateException("saved wetting selection includes the symmetry axis");
        }

        Material glue = model.material("matGlue");
        Material gas = model.material("matGas");
        Material multiphase = model.component(COMPONENT).material("mpmat1");
        int[] multiphaseDomains = multiphase.selection().entities(2);
        Physics flow = model.component(COMPONENT).physics("spf");
        Physics phase = model.component(COMPONENT).physics("pf");
        PhysicsFeature initGlue = phase.feature("init1");
        PhysicsFeature initGas = phase.feature("initfluid2");
        PhysicsFeature phaseModel = phase.feature("pfm1");
        PhysicsFeature pressureReference = flow.feature("pressureReference");
        String compressibility = flow.prop("PhysicalModel").getString("Compressibility");
        if (!"incompressible".equals(compressibility) ||
            !"off".equals(flow.prop("PhysicalModel").getString("IncludeGravity")) ||
            !"PressurePointConstraint".equals(pressureReference.getType()) ||
            !"0[Pa]".equals(pressureReference.getString("p0")) ||
            !sameIds(glueIds, initGlue.selection().entities(2)) ||
            !sameIds(gasIds, initGas.selection().entities(2)) ||
            !"Fluid1phipf".equals(initGlue.getString("FluidInDomain")) ||
            !"Fluid2phipf".equals(initGas.getString("FluidInDomain")) ||
            !sameIds(allDomains, multiphaseDomains)) {
            throw new IllegalStateException("saved material/initial-value domain mapping differs from the built model");
        }

        List<Map<String, Object>> wallFeatures = new ArrayList<>();
        List<String> wallTags = new ArrayList<>();
        for (String tag : phase.feature().tags()) {
            if ("WettedWall".equals(phase.feature(tag).getType())) wallTags.add(tag);
        }
        Collections.sort(wallTags);
        if (wallTags.size() != wetting.size()) {
            throw new IllegalStateException("saved model does not have one Wetted Wall feature per named substrate surface");
        }
        List<String> wettingNames = new ArrayList<>(wetting.keySet());
        for (int wallIndex = 0; wallIndex < wallTags.size(); wallIndex++) {
            String tag = wallTags.get(wallIndex);
            PhysicsFeature feature = phase.feature(tag);
            int[] selected = feature.selection().entities(1);
            if (selected.length == 0 || !"SpecifyContactAngleDirectly".equals(
                    feature.getString("SpecifyContactAngle")) ||
                !"thetaSubstrate".equals(feature.getString("thetaw")) ||
                !sameIds(wetting.get(wettingNames.get(wallIndex)), selected)) {
                throw new IllegalStateException("saved Wetted Wall feature configuration is incomplete: " + tag);
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("feature_tag", tag);
            row.put("surface_selection", wettingNames.get(wallIndex));
            row.put("boundary_ids", boxed(selected));
            row.put("contact_angle_expression", feature.getString("thetaw"));
            wallFeatures.add(row);
        }
        Material multiphaseMaterial = multiphase;
        if (!"pf".equals(multiphaseMaterial.getString("vfDefinition")) ||
            !"matGlue".equals(multiphaseMaterial.feature("phase1").getString("link")) ||
            !"matGas".equals(multiphaseMaterial.feature("phase2").getString("link"))) {
            throw new IllegalStateException("saved multiphase material links differ from the intended glue/gas phases");
        }

        Map<String, Object> parameterReadback = new LinkedHashMap<>();
        parameterReadback.put("rhoGlue", parameter(model, "rhoGlue", 1200.0, "kg/m^3"));
        parameterReadback.put("muGlue", parameter(model, "muGlue", 1.0, "Pa*s"));
        parameterReadback.put("rhoGas", parameter(model, "rhoGas", 1.2, "kg/m^3"));
        parameterReadback.put("muGas", parameter(model, "muGas", 0.018, "Pa*s"));
        parameterReadback.put("sigma0", parameter(model, "sigma0", 0.03, "N/m"));
        parameterReadback.put("epsPF", parameter(model, "epsPF", 8e-6, "m"));
        parameterReadback.put("Rdrop", parameter(model, "Rdrop", 500e-6, "m"));

        Study study = model.study("stdShape");
        String phaseInitializationType = study.feature("phasei").getType();
        String transientType = study.feature("time").getType();
        String[] solverTags = study.getSolverSequences("SolverSequence");
        if (!"PhaseInitialization".equals(phaseInitializationType) ||
            !"Transient".equals(transientType) || solverTags.length != 1) {
            throw new IllegalStateException("saved shape study lost its Phase Initialization/Transient sequence");
        }
        SolverSequence sequence = model.sol(solverTags[0]);
        if (!sequence.isAttached() || !"stdShape".equals(sequence.study())) {
            throw new IllegalStateException("saved shape solver sequence is detached from stdShape");
        }
        SolverFeature timeSolver = uniqueTimeFeature(sequence);
        String bdfOutput = timeSolver.getString("tstepsbdf");
        String outputMode = timeSolver.getString("tout");
        int storedSteps = timeSolver.getInt("tstepsstore");
        double maxStep = timeSolver.getDouble("maxstepbdf");
        if (!"strict".equals(bdfOutput) || !"tsteps".equals(outputMode) || storedSteps != 1 ||
            !Double.isFinite(maxStep) || maxStep <= 0.0) {
            throw new IllegalStateException("saved shape solver output/max-step settings differ from the configured study");
        }
        String[] meshTags = model.component(COMPONENT).mesh().tags();
        if (!contains(meshTags, "mesh1")) throw new IllegalStateException("saved static-shape mesh1 is absent");

        MultiphysicsCoupling coupling = model.component(COMPONENT).multiphysics("tpf1");
        String surfaceTensionEnabled = coupling.getString("IncludeSurfaceTension");
        if (!"TwoPhaseFlowPhaseField".equals(coupling.getType()) ||
            !"spf".equals(coupling.getString("Fluid_physics")) ||
            !"pf".equals(coupling.getString("Mathematics_physics")) ||
            !"mpmat1".equals(coupling.getString("multiphaseMaterialList")) ||
            !("on".equalsIgnoreCase(surfaceTensionEnabled) || "1".equals(surfaceTensionEnabled) ||
              "true".equalsIgnoreCase(surfaceTensionEnabled)) ||
            !"userdef".equals(coupling.getString("SurfaceTensionCoefficient")) ||
            !"sigma0".equals(coupling.getString("sigma"))) {
            throw new IllegalStateException("saved two-phase coupling/surface-tension readback is incomplete");
        }
        Map<String, Object> solutionState = requireNoStoredSolutionData(model);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "STATIC_SHAPE_NATIVE_CONFIGURATION_READBACK");
        result.put("native_acceptance", "NOT_RUN");
        result.put("model_tag", model.tag());
        result.put("case_id", caseId);
        result.put("geometry_dimension", geom.getSDim());
        result.put("geometry_axisymmetric", geom.isAxisymmetric());
        result.put("geometry_domain_count", geom.getNDomains());
        result.put("geometry_boundary_count", geom.getNBoundaries());
        result.put("glue_domain_ids", boxed(glueIds));
        result.put("gas_domain_ids", boxed(gasIds));
        result.put("multiphase_domain_ids", boxed(multiphaseDomains));
        result.put("multiphase_volume_fraction_definition", multiphaseMaterial.getString("vfDefinition"));
        result.put("phase1_material_link", multiphaseMaterial.feature("phase1").getString("link"));
        result.put("phase2_material_link", multiphaseMaterial.feature("phase2").getString("link"));
        result.put("substrate_wetting_selections", wettingReadback);
        result.put("axis_boundary_ids", boxed(axisIds));
        result.put("wetted_wall_features", wallFeatures);
        result.put("parameter_values_and_units", parameterReadback);
        result.put("glue_density_expression", glue.propertyGroup("def").getString("density"));
        result.put("gas_density_expression", gas.propertyGroup("def").getString("density"));
        result.put("glue_viscosity_expression", glue.propertyGroup("def").getString("dynamicviscosity"));
        result.put("gas_viscosity_expression", gas.propertyGroup("def").getString("dynamicviscosity"));
        result.put("phase_field_type", phase.getType());
        result.put("phase_field_model_type", phaseModel.getType());
        result.put("phase_field_epsilon_expression", phaseModel.getString("epsilon_pf"));
        result.put("phase_field_chi_expression", phaseModel.getString("chi"));
        result.put("phase1_initial_selection", boxed(initGlue.selection().entities(2)));
        result.put("phase1_initial_value", initGlue.getString("FluidInDomain"));
        result.put("phase2_initial_selection", boxed(initGas.selection().entities(2)));
        result.put("phase2_initial_value", initGas.getString("FluidInDomain"));
        result.put("flow_type", flow.getType());
        result.put("flow_compressibility", compressibility);
        result.put("gravity_enabled", flow.prop("PhysicalModel").getString("IncludeGravity"));
        result.put("pressure_reference_type", pressureReference.getType());
        result.put("pressure_reference_value", pressureReference.getString("p0"));
        result.put("pressure_reference_point_ids", boxed(pressureReference.selection().entities(0)));
        result.put("two_phase_coupling_type", coupling.getType());
        result.put("surface_tension_enabled", surfaceTensionEnabled);
        result.put("surface_tension_mode", coupling.getString("SurfaceTensionCoefficient"));
        result.put("surface_tension_expression", coupling.getString("sigma"));
        result.put("phase_initialization_step_type", phaseInitializationType);
        result.put("transient_step_type", transientType);
        result.put("time_list_expression", study.feature("time").getString("tlist"));
        result.put("solver_sequence_tags", Arrays.asList(solverTags));
        result.put("bdf_output_time_policy", bdfOutput);
        result.put("output_mode", outputMode);
        result.put("stored_time_steps_policy", storedSteps);
        result.put("maximum_step_s", maxStep);
        result.put("mesh_tags", Arrays.asList(meshTags));
        result.put("solution_state_readback", solutionState);
        result.put("study_run_calls_this_action", 0);
        return result;
    }

    private static Map<String, Object> saveUnsolved(Model model, Map<String, Object> args) throws IOException {
        String workspaceText = String.valueOf(args.getOrDefault("workspace_path", ""));
        String destinationText = String.valueOf(args.getOrDefault("path", ""));
        if (workspaceText.isEmpty() || destinationText.isEmpty()) {
            throw new IllegalArgumentException("save requires workspace_path and path");
        }
        Path workspace = Paths.get(workspaceText).toAbsolutePath().normalize();
        Path destination = Paths.get(destinationText).toAbsolutePath().normalize();
        if (destination.equals(workspace) || !destination.startsWith(workspace) ||
            !destination.getFileName().toString().endsWith(".mph")) {
            throw new IllegalArgumentException("save destination must be a child .mph file in the registered workspace");
        }
        Path parent = destination.getParent();
        if (parent == null || !Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS) ||
            Files.isSymbolicLink(parent) || !parent.toRealPath().equals(parent) ||
            Files.exists(destination, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalStateException("save destination parent is unsafe or destination already exists");
        }
        Map<String, Object> solutionState = requireNoStoredSolutionData(model);
        model.save(destination.toString());
        if (!Files.isRegularFile(destination, LinkOption.NOFOLLOW_LINKS) ||
            Files.isSymbolicLink(destination) || Files.size(destination) <= 0) {
            throw new IllegalStateException("COMSOL save returned without a new nonempty regular MPH file");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "SAVED_UNSOLVED_STATIC_SHAPE_MODEL");
        result.put("model_tag", model.tag());
        result.put("path", destination.toString());
        result.put("size_bytes", Files.size(destination));
        result.put("sha256", sha256(Files.readAllBytes(destination)));
        result.put("solution_data_present", false);
        result.put("solution_state_readback", solutionState);
        result.put("study_run_calls_this_action", 0);
        return result;
    }

    /**
     * Use the documented SolverSequence.getSize() readback, whose two entries
     * are degrees-of-freedom and stored solution count. Never mutate or clear
     * a solution to manufacture an "unsolved" status.
     */
    private static Map<String, Object> requireNoStoredSolutionData(Model model) {
        String[] tags = model.sol().tags();
        Arrays.sort(tags);
        List<Map<String, Object>> rows = new ArrayList<>();
        for (String tag : tags) {
            int[] size = model.sol(tag).getSize();
            if (size == null || size.length != 2 || size[0] < 0 || size[1] < 0 ||
                ((size[0] == 0) != (size[1] == 0))) {
                throw new IllegalStateException("solver sequence solution-size readback is incomplete or inconsistent: " + tag);
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("solver_sequence_tag", tag);
            row.put("degrees_of_freedom", size[0]);
            row.put("stored_solution_count", size[1]);
            rows.add(row);
            if (size[0] > 0 && size[1] > 0) {
                throw new IllegalStateException("existing stored solution data prevents the unsolved setup claim: " + tag);
            }
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "NO_STORED_SOLUTION_DATA");
        result.put("readback_method", "SolverSequence.getSize() [degrees_of_freedom, stored_solution_count]");
        result.put("solver_sequences", rows);
        return result;
    }

    private static SolverFeature uniqueTimeFeature(SolverSequence sequence) {
        Map<String, SolverFeature> matches = new LinkedHashMap<>();
        collectTimeFeatures(sequence.feature().tags(), sequence.feature(), matches);
        if (matches.size() != 1) throw new IllegalStateException("expected one saved Time solver feature");
        return matches.values().iterator().next();
    }

    private static void collectTimeFeatures(String[] tags, SolverFeatureList list,
                                           Map<String, SolverFeature> matches) {
        for (String tag : tags) {
            SolverFeature feature = list.get(tag);
            if ("Time".equals(feature.getType())) matches.put(tag, feature);
            String[] children = feature.feature().tags();
            if (children.length > 0) collectTimeFeatures(children, feature.feature(), matches);
        }
    }

    private static boolean contains(String[] values, String expected) {
        for (String value : values) if (expected.equals(value)) return true;
        return false;
    }

    private static int[] sortedUnion(int[] a, int[] b) {
        int[] merged = new int[a.length + b.length];
        System.arraycopy(a, 0, merged, 0, a.length);
        System.arraycopy(b, 0, merged, a.length, b.length);
        Arrays.sort(merged);
        int count = 0;
        for (int value : merged) {
            if (count == 0 || merged[count - 1] != value) merged[count++] = value;
        }
        return Arrays.copyOf(merged, count);
    }

    private static boolean sameIds(int[] a, int[] b) {
        int[] left = a.clone();
        int[] right = b.clone();
        Arrays.sort(left);
        Arrays.sort(right);
        return Arrays.equals(left, right);
    }

    private static List<Integer> boxed(int[] values) {
        List<Integer> rows = new ArrayList<>();
        for (int value : values) rows.add(value);
        return rows;
    }

    private static Map<String, Object> parameter(Model model, String name, double expected,
                                                 String expectedUnit) {
        String expression = model.param().get(name);
        double value = model.param().evaluate(name);
        String unit = model.param().evaluateUnit(name).replace(" ", "");
        if (!Double.isFinite(value) || Math.abs(value - expected) > Math.max(1e-14, Math.abs(expected) * 1e-12) ||
            !expectedUnit.equals(unit)) {
            throw new IllegalStateException("saved parameter value/unit mismatch for " + name);
        }
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("expression", expression);
        row.put("value_si", value);
        row.put("unit", unit);
        return row;
    }

    private static String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder hex = new StringBuilder(digest.length * 2);
            for (byte value : digest) hex.append(String.format("%02x", value & 0xff));
            return hex.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("JVM lacks SHA-256", impossible);
        }
    }
}
