import com.comsol.model.GeomSequence;
import com.comsol.model.DatasetFeature;
import com.comsol.model.Model;
import com.comsol.model.NumericalFeature;
import com.comsol.model.SolverSequence;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.physics.PhysicsFeature;
import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.zip.GZIPOutputStream;

/**
 * Build-only W24 cure-law v2 controls. No action in this class submits a solve.
 * Native Activation and Maxwell reference/history semantics remain fail-closed
 * until captured by a separately approved native campaign.
 */
public final class W24CureLawV2ControlFixture {
    private static final String COMPONENT = "comp1";
    private static final String GEOMETRY = "geom1";
    private static final double SIDE_M = 100.0e-6;
    private static final double BOUND_TOL_M = 1.0e-10;

    private W24CureLawV2ControlFixture() { }

    public static Object run(Model model, Map<String, Object> args) {
        String action = String.valueOf(args.getOrDefault("action", "build_maxwell_ramp_hold"));
        if ("build_maxwell_ramp_hold".equals(action)) return buildMaxwellRampHold(model);
        if ("readback_maxwell_ramp_hold".equals(action)) return readbackMaxwellRampHold(model);
        if ("build_gel_stress_free".equals(action)) return buildGelStressFree(model);
        if ("readback_gel_stress_free".equals(action)) return readbackGelStressFree(model);
        if ("capture_maxwell_control".equals(action)) return captureControl(model, args, true);
        if ("capture_gel_control".equals(action)) return captureControl(model, args, false);
        if ("solution_snapshot_v2".equals(action)) return solutionSnapshotV2(model, args);
        throw new IllegalArgumentException("unsupported cure-law v2 control action: " + action);
    }

    private static Map<String, Object> buildMaxwellRampHold(Model model) {
        requireFreshModel(model);
        commonParameters(model);
        buildCube(model);
        createSelections(model);
        buildMaterial(model);
        PhysicsFeature visco = addSolidMechanics(model);
        addMaxwellBranch(visco, 3);
        addZeroInitialDisplacement(model);
        addAffineRampBoundary(model);
        addTransientStudy(model, "stdMaxwell", "range(0[s],1[s],901[s])");
        Map<String, Object> result = readbackMaxwellRampHold(model);
        result.put("status", "BUILT_NOT_SOLVED");
        result.put("native_study_run_calls", 0);
        result.put("activation", "material active from initial time; no Activation gate in this control");
        result.put("native_branch_initial_reference_state", "UNVERIFIED_FAIL_CLOSED");
        result.put("module_license", "UNVERIFIED_FAIL_CLOSED");
        return result;
    }

    private static Map<String, Object> readbackMaxwellRampHold(Model model) {
        GeometryReadback geometry = verifyCubeAndSelections(model);
        verifyCommonParameters(model);
        PhysicsFeature solid = model.physics("solid").feature("lemm1");
        if (!"LinearElasticModel".equals(solid.getType())) {
            throw new IllegalStateException("Maxwell control requires solid.lemm1 LinearElasticModel");
        }
        PhysicsFeature visco = solid.feature("visMaxwell");
        if (!"Viscoelasticity".equals(visco.getType()) ||
            !sameEntitySet(model.component(COMPONENT).selection("sel_domain").entities(3),
                           visco.selection().entities()) ||
            !"GeneralizedMaxwell".equals(visco.getString("MaterialModel")) ||
            !"full".equals(visco.getString("deformationModel")) ||
            !Arrays.equals(new String[]{"Gbranch"}, visco.getStringArray("Gvm")) ||
            !Arrays.equals(new String[]{"Kbranch"}, visco.getStringArray("Kvm_v")) ||
            !Arrays.equals(new String[]{"tauMaxwell"}, visco.getStringArray("tauvm"))) {
            throw new IllegalStateException("Maxwell control material/ordered branch readback mismatch");
        }
        String[] directions = model.physics("solid").feature("dispRamp").getStringArray("Direction");
        String[] displacements = model.physics("solid").feature("dispRamp").getStringArray("U0");
        if (!Arrays.equals(new String[]{"prescribed", "prescribed", "prescribed"}, directions) ||
            !Arrays.equals(new String[]{"epsFinal*min(t/Tramp,1)*x", "0[m]", "0[m]"}, displacements) ||
            !sameEntitySet(geometry.boundaryIds,
                           model.physics("solid").feature("dispRamp").selection().entities())) {
            throw new IllegalStateException("Maxwell control must impose the complete affine displacement on all exterior faces");
        }
        PhysicsFeature initial = model.physics("solid").feature("init1");
        if (!Arrays.equals(new String[]{"0[m]", "0[m]", "0[m]"}, initial.getStringArray("u")) ||
            !Arrays.equals(new String[]{"0[m/s]", "0[m/s]", "0[m/s]"}, initial.getStringArray("ut"))) {
            throw new IllegalStateException("Maxwell control initial displacement/velocity readback is not zero");
        }
        if (Arrays.asList(model.physics().tags()).contains("ht") ||
            Arrays.asList(solid.feature().tags()).contains("actGel") ||
            Arrays.asList(solid.feature().tags()).contains("estrain")) {
            throw new IllegalStateException("Maxwell control must have no thermal, chemical, activation, or eigenstrain feature");
        }
        StudyFeature time = model.study("stdMaxwell").feature("time1");
        if (!"range(0[s],1[s],901[s])".equals(time.getString("tlist")) ||
            !oneAttachedSolver(model, "stdMaxwell")) {
            throw new IllegalStateException("Maxwell control transient output/solver attachment readback mismatch");
        }

        Map<String, Object> result = commonReadback(geometry);
        result.put("quasistatic_readback", requireQuasistatic(model));
        result.put("case_id", "maxwell_ramp_hold_control");
        result.put("study_tag", "stdMaxwell");
        result.put("study_output_times", time.getString("tlist"));
        result.put("direction", Arrays.asList(directions));
        result.put("prescribed_displacement", Arrays.asList(displacements));
        result.put("initial_displacement", Arrays.asList(initial.getStringArray("u")));
        result.put("initial_velocity", Arrays.asList(initial.getStringArray("ut")));
        result.put("thermal_or_chemical_strain_features", 0);
        result.put("activation_feature", "none; solid material is active from initial time");
        result.put("material_model", visco.getString("MaterialModel"));
        result.put("deformation_model", visco.getString("deformationModel"));
        result.put("E_long_term", "Einf=0.5[GPa]");
        result.put("E_branch_0", "Ebranch=1.5[GPa]");
        result.put("nu", "nuVisco=0.35");
        result.put("K_long_term", model.param().get("Kinf"));
        result.put("G_long_term", model.param().get("Ginf"));
        result.put("K_branch_0", model.param().get("Kbranch"));
        result.put("G_branch_0", model.param().get("Gbranch"));
        result.put("Kvm_v", Arrays.asList(visco.getStringArray("Kvm_v")));
        result.put("Gvm", Arrays.asList(visco.getStringArray("Gvm")));
        result.put("tauvm", Arrays.asList(visco.getStringArray("tauvm")));
        result.put("ordered_branch_binding", "index 0 binds Kbranch, Gbranch, tauMaxwell");
        result.put("native_branch_initial_reference_state", "UNVERIFIED_FAIL_CLOSED");
        result.put("solver_submissions", 0);
        return result;
    }

    private static Map<String, Object> buildGelStressFree(Model model) {
        requireFreshModel(model);
        commonParameters(model);
        model.param().set("tGel", "2[s]");
        model.param().set("epsPreGel", "1e-3");
        buildCube(model);
        createSelections(model);
        buildMaterial(model);
        PhysicsFeature lemm = addSolidMechanics(model);
        PhysicsFeature activation = lemm.feature().create("actGel", "Activation", 3);
        activation.selection().named("sel_domain");
        activation.set("activation_expression", "t>=tGel || solid.wasactive");
        PhysicsFeature initial = model.physics("solid").feature("init1");
        initial.set("u", new String[]{"0[m]", "0[m]", "0[m]"});
        initial.set("ut", new String[]{"0[m/s]", "0[m/s]", "0[m/s]"});
        PhysicsFeature displacement = model.physics("solid").create("dispPreGel", "Displacement", 2);
        displacement.selection().named("sel_all_bnd");
        displacement.set("Direction", new String[]{"prescribed", "prescribed", "prescribed"});
        displacement.set("U0", new String[]{"epsPreGel*min(t/Tramp,1)*x", "0[m]", "0[m]"});
        addTransientStudy(model, "stdGel", "range(0[s],0.5[s],3[s])");
        Map<String, Object> result = readbackGelStressFree(model);
        result.put("status", "BUILT_NOT_SOLVED");
        result.put("native_study_run_calls", 0);
        result.put("native_activation_reference_state", "UNVERIFIED_FAIL_CLOSED");
        result.put("module_license", "UNVERIFIED_FAIL_CLOSED");
        return result;
    }

    private static Map<String, Object> readbackGelStressFree(Model model) {
        GeometryReadback geometry = verifyCubeAndSelections(model);
        verifyCommonParameters(model);
        if (Math.abs(model.param().evaluate("tGel") - 2.0) > 1e-12 ||
            Math.abs(model.param().evaluate("epsPreGel") - 1e-3) > 1e-15) {
            throw new IllegalStateException("gel stress-free control timing/strain readback mismatch");
        }
        PhysicsFeature lemm = model.physics("solid").feature("lemm1");
        PhysicsFeature activation = lemm.feature("actGel");
        PhysicsFeature displacement = model.physics("solid").feature("dispPreGel");
        PhysicsFeature initial = model.physics("solid").feature("init1");
        if (!"Activation".equals(activation.getType()) ||
            !sameEntitySet(model.component(COMPONENT).selection("sel_domain").entities(3),
                           activation.selection().entities()) ||
            !"t>=tGel || solid.wasactive".equals(activation.getString("activation_expression")) ||
            Math.abs(Double.parseDouble(activation.getString("actfac")) - 1e-5) > 1e-15 ||
            !Arrays.equals(new String[]{"prescribed", "prescribed", "prescribed"},
                           displacement.getStringArray("Direction")) ||
            !Arrays.equals(new String[]{"epsPreGel*min(t/Tramp,1)*x", "0[m]", "0[m]"},
                           displacement.getStringArray("U0")) ||
            !sameEntitySet(geometry.boundaryIds, displacement.selection().entities()) ||
            !Arrays.equals(new String[]{"0[m]", "0[m]", "0[m]"}, initial.getStringArray("u")) ||
            !Arrays.equals(new String[]{"0[m/s]", "0[m/s]", "0[m/s]"}, initial.getStringArray("ut"))) {
            throw new IllegalStateException("gel stress-free activation/load/initial-state readback mismatch");
        }
        if (Arrays.asList(model.physics().tags()).contains("ht") ||
            Arrays.asList(model.physics().tags()).contains("odeAlpha") ||
            Arrays.asList(model.physics().tags()).contains("odeQpost") ||
            Arrays.asList(lemm.feature().tags()).contains("estrain") ||
            Arrays.asList(lemm.feature().tags()).contains("visMaxwell")) {
            throw new IllegalStateException("gel stress-free control must exclude thermal, chemical, eigenstrain, and Maxwell features");
        }
        StudyFeature time = model.study("stdGel").feature("time1");
        if (!"range(0[s],0.5[s],3[s])".equals(time.getString("tlist")) ||
            !oneAttachedSolver(model, "stdGel")) {
            throw new IllegalStateException("gel stress-free transient output/solver attachment readback mismatch");
        }
        Map<String, Object> result = commonReadback(geometry);
        result.put("quasistatic_readback", requireQuasistatic(model));
        result.put("case_id", "gel_stress_free_control");
        result.put("study_tag", "stdGel");
        result.put("study_output_times", time.getString("tlist"));
        result.put("gel_time_s", model.param().evaluate("tGel"));
        result.put("pre_gel_affine_strain", model.param().get("epsPreGel"));
        result.put("activation_expression", activation.getString("activation_expression"));
        result.put("actfac", activation.getString("actfac"));
        result.put("actfac_was_set", false);
        result.put("native_activation_reference_state", "UNVERIFIED_FAIL_CLOSED");
        result.put("solver_submissions", 0);
        return result;
    }

    /** Capture results from an already-solved frozen control; never calls Study.run. */
    private static Map<String, Object> captureControl(Model model, Map<String, Object> args,
                                                       boolean maxwell) {
        String solverTag = safeToken(args.get("solver_tag"), "solver_tag");
        String pathText = String.valueOf(args.getOrDefault("path", ""));
        if (pathText.isBlank()) throw new IllegalArgumentException("control capture requires a new output path");
        String caseId = maxwell ? "maxwell_ramp_hold_control" : "gel_stress_free_control";
        String studyTag = maxwell ? "stdMaxwell" : "stdGel";
        Map<String, Object> configuration = maxwell ? readbackMaxwellRampHold(model) : readbackGelStressFree(model);
        if (!"Quasistatic".equals(configuration.get("quasistatic_readback"))) {
            throw new IllegalStateException("native control capture requires the actual Quasistatic property readback");
        }
        String[] solvers = model.study(studyTag).getSolverSequences("SolverSequence");
        if (solvers.length != 1 || !solverTag.equals(solvers[0])) {
            throw new IllegalStateException("capture solver must be the exact unique solver attached to the frozen study");
        }
        SolverSequence solution = model.sol(solverTag);
        if (!solution.isAttached() || !studyTag.equals(solution.study())) {
            throw new IllegalStateException("capture SolverSequence is detached or belongs to another study");
        }
        double[] times = solution.getPVals();
        verifyControlStoredTimes(times, maxwell);

        String[] expressions;
        String[] units;
        if (maxwell) {
            expressions = new String[]{"solid.sx", "solid.sy"};
            units = new String[]{"Pa", "Pa"};
        } else {
            expressions = new String[]{"solid.sx", "solid.sy", "solid.sz", "solid.sxy",
                "solid.sxz", "solid.syz", "solid.isactive", "solid.wasactive"};
            units = new String[]{"Pa", "Pa", "Pa", "Pa", "Pa", "Pa", "1", "1"};
        }
        double[][] coordinates = new double[][]{{SIDE_M / 2.0}, {SIDE_M / 2.0}, {SIDE_M / 2.0}};
        String datasetTag = "w24v2d" + Long.toUnsignedString(System.nanoTime(), 36);
        String interpolationTag = "w24v2i" + Long.toUnsignedString(System.nanoTime(), 36);
        boolean datasetCreated = false;
        boolean interpolationCreated = false;
        try {
            DatasetFeature dataset = model.result().dataset().create(datasetTag, "Solution");
            datasetCreated = true;
            dataset.set("solution", solverTag);
            if (!solverTag.equals(dataset.getString("solution"))) {
                throw new IllegalStateException("capture dataset does not read back the exact SolverSequence");
            }
            NumericalFeature interpolation = model.result().numerical().create(interpolationTag, "Interp");
            interpolationCreated = true;
            interpolation.set("data", datasetTag);
            interpolation.set("expr", expressions);
            interpolation.set("unit", units);
            interpolation.set("solnum", "all");
            interpolation.set("coorderr", "on");
            interpolation.set("matherr", "on");
            interpolation.setInterpolationCoordinates(coordinates);
            if (!datasetTag.equals(interpolation.getString("data")) ||
                !Arrays.equals(expressions, interpolation.getStringArray("expr")) ||
                !Arrays.equals(units, interpolation.getStringArray("unit")) ||
                !"all".equals(interpolation.getString("solnum")) ||
                !interpolation.getBoolean("coorderr") || !interpolation.getBoolean("matherr")) {
                throw new IllegalStateException("native control Interp readback differs from the frozen expression/unit/dataset contract");
            }
            interpolation.run();
            double[][][] raw = interpolation.getData();
            if (raw == null || raw.length != expressions.length) {
                throw new IllegalStateException("native control Interp expression axis has an unexpected shape");
            }
            List<Object> series = new ArrayList<>();
            for (int expression = 0; expression < expressions.length; expression++) {
                if (raw[expression] == null || raw[expression].length != times.length) {
                    throw new IllegalStateException("native control Interp stored-time axis differs from SolverSequence.getPVals");
                }
                List<Object> expressionTimes = new ArrayList<>();
                for (int time = 0; time < times.length; time++) {
                    if (raw[expression][time] == null || raw[expression][time].length != 1 ||
                        !Double.isFinite(raw[expression][time][0])) {
                        throw new IllegalStateException("native control Interp coordinate/value shape is incomplete or nonfinite");
                    }
                    double value = raw[expression][time][0];
                    if ("1".equals(units[expression]) && value != 0.0 && value != 1.0) {
                        throw new IllegalStateException("native Activation variable is not exactly binary");
                    }
                    expressionTimes.add(Arrays.asList(value));
                }
                series.add(expressionTimes);
            }
            Map<String, Object> interpolationReadback = new LinkedHashMap<>();
            interpolationReadback.put("type", "Interp");
            interpolationReadback.put("dataset", interpolation.getString("data"));
            interpolationReadback.put("expressions", Arrays.asList(interpolation.getStringArray("expr")));
            interpolationReadback.put("units", Arrays.asList(interpolation.getStringArray("unit")));
            interpolationReadback.put("solnum", interpolation.getString("solnum"));
            interpolationReadback.put("coorderr", interpolation.getString("coorderr"));
            interpolationReadback.put("matherr", interpolation.getString("matherr"));
            interpolationReadback.put("coordinates_m", Arrays.asList(
                Arrays.asList(coordinates[0][0], coordinates[1][0], coordinates[2][0])));
            interpolationReadback.put("coordinate_source", "fixed 3D Java coordinates passed to setInterpolationCoordinates");
            interpolationReadback.put("shape", Arrays.asList(expressions.length, times.length, 1));

            Map<String, Object> artifact = new LinkedHashMap<>();
            artifact.put("schema", "W24_CURE_V2_NATIVE_CONTROL_CAPTURE_V1");
            artifact.put("status", "NATIVE_CONTROL_CAPTURED_NO_SOLVE_SUBMITTED");
            artifact.put("case_id", caseId);
            artifact.put("study_tag", studyTag);
            artifact.put("solver_tag", solverTag);
            artifact.put("dataset_tag", datasetTag);
            artifact.put("dataset_type_requested", "Solution");
            artifact.put("dataset_solution_readback", dataset.getString("solution"));
            artifact.put("stored_times_s", boxed(times));
            artifact.put("time_source", "SolverSequence.getPVals");
            artifact.put("expressions", Arrays.asList(expressions));
            artifact.put("units", Arrays.asList(units));
            artifact.put("coordinates_m", interpolationReadback.get("coordinates_m"));
            artifact.put("shape", Arrays.asList(expressions.length, times.length, 1));
            artifact.put("data", series);
            artifact.put("feature_readback", interpolationReadback);
            artifact.put("quasistatic_readback", configuration.get("quasistatic_readback"));
            artifact.put("study_tlist_readback", model.study(studyTag).feature("time1").getString("tlist"));
            artifact.put("native_study_run_calls", 0);
            artifact.put("activation_semantics", maxwell ? "NO_ACTIVATION_FEATURE" : "EXPRESSION_VALUES_CAPTURED");
            artifact.put("maxwell_branch_reference_state", "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE");
            byte[] bytes = (toJson(artifact) + "\n").getBytes(StandardCharsets.UTF_8);
            Path output = Path.of(pathText);
            writeNewAndSync(output, bytes);
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("status", "NATIVE_CONTROL_CAPTURE_WRITTEN");
            result.put("case_id", caseId);
            result.put("solver_tag", solverTag);
            result.put("study_tag", studyTag);
            result.put("dataset_tag", datasetTag);
            result.put("path", output.toString());
            result.put("size_bytes", bytes.length);
            result.put("sha256", sha256(bytes));
            result.put("stored_time_count", times.length);
            result.put("expression_count", expressions.length);
            result.put("shape", Arrays.asList(expressions.length, times.length, 1));
            result.put("quasistatic_readback", configuration.get("quasistatic_readback"));
            result.put("native_study_run_calls", 0);
            result.put("maxwell_branch_reference_state", "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE");
            return result;
        } finally {
            RuntimeException cleanupFailure = null;
            if (interpolationCreated) {
                try { model.result().numerical().remove(interpolationTag); }
                catch (RuntimeException exception) { cleanupFailure = exception; }
            }
            if (datasetCreated) {
                try { model.result().dataset().remove(datasetTag); }
                catch (RuntimeException exception) { if (cleanupFailure == null) cleanupFailure = exception; }
            }
            if (cleanupFailure != null) {
                throw new IllegalStateException("native control artifact may exist but temporary result cleanup failed", cleanupFailure);
            }
        }
    }

    private static void verifyControlStoredTimes(double[] times, boolean maxwell) {
        int expectedCount = maxwell ? 902 : 7;
        if (times == null || times.length != expectedCount) {
            throw new IllegalStateException("SolverSequence stored time count differs from the frozen full output schedule");
        }
        double step = maxwell ? 1.0 : 0.5;
        for (int i = 0; i < times.length; i++) {
            double expected = i * step;
            if (!Double.isFinite(times[i]) || Math.abs(times[i] - expected) > 1e-12) {
                throw new IllegalStateException("SolverSequence stored time differs from the exact frozen control time at index " + i);
            }
        }
    }

    /** Serialize all actual 3D Xmesh DOFs and real solution vectors, including every stored time. */
    private static Map<String, Object> solutionSnapshotV2(Model model, Map<String, Object> args) {
        String studyTag = safeToken(args.get("study_tag"), "study_tag");
        String solverTag = safeToken(args.get("solver_tag"), "solver_tag");
        String pathText = String.valueOf(args.getOrDefault("path", ""));
        if (pathText.isBlank() || !Arrays.asList(model.study().tags()).contains(studyTag)) {
            throw new IllegalArgumentException("solution_snapshot_v2 requires an existing exact study and output path");
        }
        String[] attached = model.study(studyTag).getSolverSequences("SolverSequence");
        if (attached.length != 1 || !solverTag.equals(attached[0])) {
            throw new IllegalStateException("V2 snapshot requires the exact unique solver attached to its study");
        }
        SolverSequence solution = model.sol(solverTag);
        double[] times = solution.getPVals();
        if (!solution.isAttached() || !studyTag.equals(solution.study()) || times == null || times.length == 0) {
            throw new IllegalStateException("V2 snapshot solver attachment differs from its exact study or has no stored times");
        }
        String quasistatic = requireQuasistatic(model);
        com.comsol.model.XmeshInfoDofs dofs = solution.xmeshInfo().dofs();
        int[] geometryNumbers = dofs.geomNums();
        int[] nodes = dofs.nodes();
        int[] nameIndices = dofs.nameInds();
        int[] vectorIndices = dofs.solVectorInds();
        double[][] coordinates = dofs.coords();
        String[] names = dofs.dofNames();
        int count = geometryNumbers.length;
        int axes = coordinates == null ? 0 : coordinates.length;
        if ((axes != 2 && axes != 3) || count <= 0 || names == null || names.length == 0 || nodes.length != count ||
            nameIndices.length != count || vectorIndices.length != count) {
            throw new IllegalStateException("W24 snapshot V2 requires complete 2D or 3D XmeshInfoDofs metadata");
        }
        for (int axis = 0; axis < axes; axis++) {
            if (coordinates[axis] == null || coordinates[axis].length != count) {
                throw new IllegalStateException("3D Xmesh coordinates do not align with the complete DOF mapping");
            }
        }
        Path output = Path.of(pathText);
        Path parent = output.getParent();
        if (parent == null || !Files.isDirectory(parent) || Files.exists(output)) {
            throw new IllegalArgumentException("V2 snapshot output must be a new file in an existing task directory");
        }
        int maxVectorIndex = -1;
        for (int i = 0; i < count; i++) {
            if (nameIndices[i] < 0 || nameIndices[i] >= names.length || vectorIndices[i] < 0) {
                throw new IllegalStateException("V2 Xmesh DOF index is outside its native name/solution mapping");
            }
            maxVectorIndex = Math.max(maxVectorIndex, vectorIndices[i]);
            for (int axis = 0; axis < axes; axis++) {
                if (!Double.isFinite(coordinates[axis][i])) throw new IllegalStateException("V2 DOF coordinate is nonfinite");
            }
        }
        try (DataOutputStream out = new DataOutputStream(new BufferedOutputStream(
                new GZIPOutputStream(Files.newOutputStream(output, StandardOpenOption.CREATE_NEW))))) {
            out.writeUTF("W24-DOF-SNAPSHOT-2");
            out.writeInt(axes);
            out.writeInt(names.length);
            for (String name : names) out.writeUTF(name);
            out.writeInt(count);
            for (int i = 0; i < count; i++) {
                out.writeInt(geometryNumbers[i]);
                out.writeInt(nodes[i]);
                out.writeInt(nameIndices[i]);
                out.writeInt(vectorIndices[i]);
                for (int axis = 0; axis < axes; axis++) out.writeDouble(coordinates[axis][i]);
            }
            out.writeInt(times.length);
            double prior = Double.NEGATIVE_INFINITY;
            for (int solnum = 1; solnum <= times.length; solnum++) {
                double time = times[solnum - 1];
                if (!Double.isFinite(time) || time <= prior || !solution.isRealU(solnum, "Sol")) {
                    throw new IllegalStateException("V2 snapshot requires finite increasing times and real native solution vectors");
                }
                double[] values = solution.getU(solnum, "Sol");
                if (values == null || values.length <= maxVectorIndex) {
                    throw new IllegalStateException("V2 solution vector does not cover every Xmesh DOF");
                }
                out.writeDouble(time);
                out.writeInt(values.length);
                for (double value : values) {
                    if (!Double.isFinite(value)) throw new IllegalStateException("V2 solution vector contains a nonfinite value");
                    out.writeDouble(value);
                }
                prior = time;
            }
            out.flush();
        } catch (IOException exception) {
            throw new IllegalStateException("failed to write compressed W24 3D solution snapshot V2", exception);
        }
        try (FileChannel channel = FileChannel.open(output, StandardOpenOption.WRITE)) { channel.force(true); }
        catch (IOException exception) { throw new IllegalStateException("failed to fsync W24 3D solution snapshot V2", exception); }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "SOLUTION_SNAPSHOT_V2_WRITTEN");
        result.put("schema", "W24-DOF-SNAPSHOT-2");
        result.put("study_tag", studyTag);
        result.put("solver_tag", solverTag);
        result.put("study_tlist_readback", model.study(studyTag).feature("time1").getString("tlist"));
        result.put("quasistatic_readback", quasistatic);
        result.put("path", output.toString());
        result.put("size_bytes", safeFileSize(output));
        result.put("sha256", sha256(output));
        result.put("stored_time_count", times.length);
        result.put("dof_count", count);
        result.put("dof_names", Arrays.asList(names));
        result.put("coordinate_axes", axes);
        result.put("real_solution", true);
        result.put("complete_xmesh_dofs", true);
        result.put("max_solution_vector_index", maxVectorIndex);
        return result;
    }

    private static String requireQuasistatic(Model model) {
        String value = model.physics("solid").prop("StructuralTransientBehavior")
            .getString("StructuralTransientBehavior");
        if (!"Quasistatic".equals(value)) {
            throw new IllegalStateException("actual Solid Mechanics StructuralTransientBehavior is not Quasistatic");
        }
        return value;
    }

    private static long safeFileSize(Path path) {
        try { return Files.size(path); }
        catch (IOException exception) { throw new IllegalStateException("cannot read V2 snapshot size", exception); }
    }

    private static String safeToken(Object value, String label) {
        if (!(value instanceof String)) throw new IllegalArgumentException(label + " must be a string token");
        String result = (String) value;
        if (!result.matches("[A-Za-z0-9_-]{1,64}")) {
            throw new IllegalArgumentException(label + " contains unsupported characters");
        }
        return result;
    }

    private static void writeNewAndSync(Path output, byte[] bytes) {
        Path parent = output.getParent();
        if (parent == null || !Files.isDirectory(parent) || Files.exists(output)) {
            throw new IllegalArgumentException("native control metrics must be new in an existing task directory");
        }
        try (FileChannel channel = FileChannel.open(output, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            while (buffer.hasRemaining()) channel.write(buffer);
            channel.force(true);
        } catch (IOException exception) {
            throw new IllegalStateException("failed to write and fsync native control metrics", exception);
        }
    }

    private static String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder text = new StringBuilder();
            for (byte value : digest) text.append(String.format("%02x", value & 0xff));
            return text.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static String sha256(Path path) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[1024 * 1024];
            try (InputStream input = Files.newInputStream(path)) {
                int count;
                while ((count = input.read(buffer)) >= 0) {
                    if (count > 0) digest.update(buffer, 0, count);
                }
            }
            StringBuilder text = new StringBuilder();
            for (byte value : digest.digest()) text.append(String.format("%02x", value & 0xff));
            return text.toString();
        } catch (IOException | NoSuchAlgorithmException exception) {
            throw new IllegalStateException("failed to hash complete V2 solution snapshot", exception);
        }
    }

    private static String toJson(Object value) {
        StringBuilder out = new StringBuilder();
        appendJson(out, value);
        return out.toString();
    }

    private static void appendJson(StringBuilder out, Object value) {
        if (value == null) { out.append("null"); return; }
        if (value instanceof String || value instanceof Character) {
            out.append('"');
            String text = String.valueOf(value);
            for (int i = 0; i < text.length(); i++) {
                char ch = text.charAt(i);
                switch (ch) {
                    case '"': out.append("\\\""); break;
                    case '\\': out.append("\\\\"); break;
                    case '\n': out.append("\\n"); break;
                    case '\r': out.append("\\r"); break;
                    case '\t': out.append("\\t"); break;
                    default: if (ch < 0x20) out.append(String.format("\\u%04x", (int) ch)); else out.append(ch);
                }
            }
            out.append('"');
        } else if (value instanceof Number) {
            if (!Double.isFinite(((Number) value).doubleValue())) throw new IllegalArgumentException("JSON cannot contain nonfinite values");
            out.append(value.toString());
        } else if (value instanceof Boolean) {
            out.append(value.toString());
        } else if (value instanceof Map<?, ?>) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) out.append(',');
                first = false;
                appendJson(out, String.valueOf(entry.getKey()));
                out.append(':');
                appendJson(out, entry.getValue());
            }
            out.append('}');
        } else if (value instanceof Iterable<?>) {
            out.append('[');
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) out.append(',');
                first = false;
                appendJson(out, item);
            }
            out.append(']');
        } else if (value.getClass().isArray()) {
            out.append('[');
            for (int i = 0; i < java.lang.reflect.Array.getLength(value); i++) {
                if (i > 0) out.append(',');
                appendJson(out, java.lang.reflect.Array.get(value, i));
            }
            out.append(']');
        } else {
            throw new IllegalArgumentException("unsupported native control JSON type " + value.getClass().getName());
        }
    }

    private static void commonParameters(Model model) {
        model.param().set("Einf", "0.5[GPa]");
        model.param().set("Ebranch", "1.5[GPa]");
        model.param().set("nuVisco", "0.35");
        model.param().set("tauMaxwell", "300[s]");
        model.param().set("Tramp", "1[s]");
        model.param().set("epsFinal", "1e-3");
        model.param().set("Kinf", "Einf/(3*(1-2*nuVisco))");
        model.param().set("Ginf", "Einf/(2*(1+nuVisco))");
        model.param().set("Kbranch", "Ebranch/(3*(1-2*nuVisco))");
        model.param().set("Gbranch", "Ebranch/(2*(1+nuVisco))");
    }

    private static void verifyCommonParameters(Model model) {
        if (Math.abs(model.param().evaluate("Einf") - 0.5e9) > 1e-3 ||
            Math.abs(model.param().evaluate("Ebranch") - 1.5e9) > 1e-3 ||
            Math.abs(model.param().evaluate("nuVisco") - 0.35) > 1e-12 ||
            Math.abs(model.param().evaluate("tauMaxwell") - 300.0) > 1e-12 ||
            Math.abs(model.param().evaluate("Tramp") - 1.0) > 1e-12 ||
            Math.abs(model.param().evaluate("epsFinal") - 1e-3) > 1e-15 ||
            Math.abs(model.param().evaluate("Kinf") - 0.5e9 / 0.9) > 1e-3 ||
            Math.abs(model.param().evaluate("Ginf") - 0.5e9 / 2.7) > 1e-3 ||
            Math.abs(model.param().evaluate("Kbranch") - 1.5e9 / 0.9) > 1e-3 ||
            Math.abs(model.param().evaluate("Gbranch") - 1.5e9 / 2.7) > 1e-3) {
            throw new IllegalStateException("cure-law v2 control parameters do not match the frozen recipe");
        }
    }

    private static void buildCube(Model model) {
        model.component().create(COMPONENT);
        GeomSequence geom = model.component(COMPONENT).geom().create(GEOMETRY, 3);
        geom.lengthUnit("m");
        geom.feature().create("blk1", "Block");
        geom.feature("blk1").set("base", "corner");
        geom.feature("blk1").set("pos", new String[]{"0[m]", "0[m]", "0[m]"});
        geom.feature("blk1").set("size", new String[]{"100[um]", "100[um]", "100[um]"});
        geom.run();
        if (geom.getSDim() != 3 || geom.getNDomains() != 1) {
            throw new IllegalStateException("v2 control geometry must be one three-dimensional block");
        }
    }

    private static void createSelections(Model model) {
        boxSelection(model, "sel_domain", 3,
            -BOUND_TOL_M, SIDE_M + BOUND_TOL_M, -BOUND_TOL_M, SIDE_M + BOUND_TOL_M,
            -BOUND_TOL_M, SIDE_M + BOUND_TOL_M);
        boxSelection(model, "sel_all_bnd", 2,
            -BOUND_TOL_M, SIDE_M + BOUND_TOL_M, -BOUND_TOL_M, SIDE_M + BOUND_TOL_M,
            -BOUND_TOL_M, SIDE_M + BOUND_TOL_M);
    }

    private static void boxSelection(Model model, String tag, int entityDim,
                                     double xmin, double xmax, double ymin, double ymax,
                                     double zmin, double zmax) {
        model.component(COMPONENT).selection().create(tag, "Box");
        model.component(COMPONENT).selection(tag).set("entitydim", entityDim);
        model.component(COMPONENT).selection(tag).set("xmin", xmin);
        model.component(COMPONENT).selection(tag).set("xmax", xmax);
        model.component(COMPONENT).selection(tag).set("ymin", ymin);
        model.component(COMPONENT).selection(tag).set("ymax", ymax);
        model.component(COMPONENT).selection(tag).set("zmin", zmin);
        model.component(COMPONENT).selection(tag).set("zmax", zmax);
        model.component(COMPONENT).selection(tag).set("condition", "inside");
    }

    private static void buildMaterial(Model model) {
        model.material().create("matMax", "Common", COMPONENT);
        model.material("matMax").selection().named("sel_domain");
        model.material("matMax").propertyGroup("def").set("density", "1200[kg/m^3]");
        model.material("matMax").propertyGroup("def").set("youngsmodulus", "Einf");
        model.material("matMax").propertyGroup("def").set("poissonsratio", "nuVisco");
    }

    private static PhysicsFeature addSolidMechanics(Model model) {
        model.component(COMPONENT).physics().create("solid", "SolidMechanics", GEOMETRY);
        model.physics("solid").prop("StructuralTransientBehavior")
            .set("StructuralTransientBehavior", "Quasistatic");
        PhysicsFeature lemm = model.physics("solid").feature("lemm1");
        if (!"LinearElasticModel".equals(lemm.getType())) {
            throw new IllegalStateException("solid.lemm1 is not the native LinearElasticModel feature");
        }
        return lemm;
    }

    private static void addMaxwellBranch(PhysicsFeature lemm, int dimension) {
        PhysicsFeature visco = lemm.feature().create("visMaxwell", "Viscoelasticity", dimension);
        visco.selection().named("sel_domain");
        visco.set("MaterialModel", "GeneralizedMaxwell");
        visco.set("deformationModel", "full");
        visco.set("Kvm_v", new String[]{"Kbranch"});
        visco.set("Gvm", new String[]{"Gbranch"});
        visco.set("tauvm", new String[]{"tauMaxwell"});
    }

    private static void addZeroInitialDisplacement(Model model) {
        PhysicsFeature initial = model.physics("solid").feature("init1");
        initial.set("u", new String[]{"0[m]", "0[m]", "0[m]"});
        initial.set("ut", new String[]{"0[m/s]", "0[m/s]", "0[m/s]"});
    }

    private static void addAffineRampBoundary(Model model) {
        PhysicsFeature displacement = model.physics("solid").create("dispRamp", "Displacement", 2);
        displacement.selection().named("sel_all_bnd");
        displacement.set("Direction", new String[]{"prescribed", "prescribed", "prescribed"});
        displacement.set("U0", new String[]{"epsFinal*min(t/Tramp,1)*x", "0[m]", "0[m]"});
    }

    private static void addTransientStudy(Model model, String studyTag, String times) {
        Study study = model.study().create(studyTag);
        study.create("time1", "Transient");
        study.feature("time1").set("tlist", times);
        study.createAutoSequences("sol");
        if (!oneAttachedSolver(model, studyTag)) {
            throw new IllegalStateException("control transient study must own one attached solver sequence: " + studyTag);
        }
    }

    private static boolean oneAttachedSolver(Model model, String studyTag) {
        String[] sequences = model.study(studyTag).getSolverSequences("SolverSequence");
        if (sequences.length != 1) return false;
        SolverSequence sequence = model.sol(sequences[0]);
        return sequence.isAttached() && studyTag.equals(sequence.study());
    }

    private static GeometryReadback verifyCubeAndSelections(Model model) {
        if (!Arrays.asList(model.component().tags()).contains(COMPONENT) ||
            !Arrays.asList(model.component(COMPONENT).geom().tags()).contains(GEOMETRY) ||
            model.component(COMPONENT).geom(GEOMETRY).getSDim() != 3 ||
            model.component(COMPONENT).geom(GEOMETRY).getNDomains() != 1) {
            throw new IllegalStateException("control readback requires the one-domain three-dimensional cube");
        }
        int[] domainIds = model.component(COMPONENT).selection("sel_domain").entities(3);
        int[] boundaryIds = model.component(COMPONENT).selection("sel_all_bnd").entities(2);
        if (domainIds.length != 1 || boundaryIds.length != 6) {
            throw new IllegalStateException("control domain or six-face exterior selection is not exact");
        }
        int[] materialIds = model.material("matMax").selection().entities();
        if (!sameEntitySet(domainIds, materialIds)) {
            throw new IllegalStateException("control material domain is not the exact full cube");
        }
        if (!"Einf".equals(model.material("matMax").propertyGroup("def").getString("youngsmodulus")) ||
            !"nuVisco".equals(model.material("matMax").propertyGroup("def").getString("poissonsratio"))) {
            throw new IllegalStateException("control long-term material E/nu readback mismatch");
        }
        if (!Arrays.asList(model.physics().tags()).contains("solid") ||
            model.physics().tags().length != 1) {
            throw new IllegalStateException("control model must contain Solid Mechanics only");
        }
        model.component(COMPONENT).measure().selection().geom(GEOMETRY, 3).set(domainIds);
        double[] bounds = model.component(COMPONENT).measure().getBoundingBox();
        if (bounds == null || bounds.length < 6 ||
            Math.abs(bounds[0]) > 1e-12 || Math.abs(bounds[1] - SIDE_M) > 1e-12 ||
            Math.abs(bounds[2]) > 1e-12 || Math.abs(bounds[3] - SIDE_M) > 1e-12 ||
            Math.abs(bounds[4]) > 1e-12 || Math.abs(bounds[5] - SIDE_M) > 1e-12) {
            throw new IllegalStateException("control block actual bounding box differs from 100 um cube");
        }
        return new GeometryReadback(domainIds, boundaryIds, bounds);
    }

    private static Map<String, Object> commonReadback(GeometryReadback geometry) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "W24_CURE_LAW_V2_CONTROL_READBACK_V1");
        result.put("geometry_dimension", 3);
        result.put("geometry_domain_count", 1);
        result.put("domain_ids", boxed(geometry.domainIds));
        result.put("boundary_ids", boxed(geometry.boundaryIds));
        result.put("bounding_box_m", boxed(geometry.boundingBox));
        result.put("all_six_exterior_faces_selected", geometry.boundaryIds.length == 6);
        result.put("material_youngs_modulus", "Einf=0.5[GPa]");
        result.put("material_poisson_ratio", "nuVisco=0.35");
        result.put("thermal_physics", "absent");
        result.put("chemical_physics", "absent");
        result.put("absolute_irradiance_or_thermal_load", "absent");
        return result;
    }

    private static void requireFreshModel(Model model) {
        if (model.component().tags().length != 0 || model.geom().tags().length != 0 ||
            model.physics().tags().length != 0 || model.material().tags().length != 0 ||
            model.study().tags().length != 0 || model.sol().tags().length != 0) {
            throw new IllegalStateException("v2 control build requires a new empty model with no prior solver/history state");
        }
    }

    private static boolean sameEntitySet(int[] a, int[] b) {
        int[] left = a.clone();
        int[] right = b.clone();
        Arrays.sort(left);
        Arrays.sort(right);
        return Arrays.equals(left, right);
    }

    private static java.util.List<Integer> boxed(int[] values) {
        java.util.List<Integer> result = new java.util.ArrayList<>();
        for (int value : values) result.add(value);
        return result;
    }

    private static java.util.List<Double> boxed(double[] values) {
        java.util.List<Double> result = new java.util.ArrayList<>();
        for (double value : values) result.add(value);
        return result;
    }

    private static final class GeometryReadback {
        final int[] domainIds;
        final int[] boundaryIds;
        final double[] boundingBox;

        GeometryReadback(int[] domainIds, int[] boundaryIds, double[] boundingBox) {
            this.domainIds = domainIds;
            this.boundaryIds = boundaryIds;
            this.boundingBox = boundingBox;
        }
    }
}
