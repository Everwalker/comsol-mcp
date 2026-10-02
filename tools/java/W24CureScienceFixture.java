import com.comsol.model.Model;
import com.comsol.model.DatasetFeature;
import com.comsol.model.GeomSequence;
import com.comsol.model.NumericalFeature;
import com.comsol.model.MeshSequence;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsField;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverSequence;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.FeatureInfo;
import com.comsol.model.util.ModelUtil;
import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.lang.reflect.Array;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.zip.GZIPOutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/** Native W24 solve actions. Every Study.run call is durably counted first. */
public final class W24CureScienceFixture {
    private static final String COMPONENT = "comp1";
    private static final String GEOMETRY = "geom1";
    private static final String[] SOLVER_FIELDS = {
        "comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u"
    };
    private static final String[] ABSOLUTE_TOLERANCES = {
        "1e-4", "1e-8", "1e-8", "1e-8", "1e-12"
    };
    private static final String[] LOGICAL_SOLVER_FIELDS = {
        "comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u", "comp1_w"
    };
    private static final String[] LOGICAL_COMPONENTS = {"T", "alpha", "alpha_iso", "qpost", "u", "w"};
    private static final String[] LOGICAL_ENTRY_KEYS = {
        "comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u", "comp1_u"
    };
    private static final String[] LOGICAL_ABSOLUTE_TOLERANCES = {
        "1e-4", "1e-8", "1e-8", "1e-8", "1e-12", "1e-12"
    };
    private static final Set<String> STUDY_TAGS = new HashSet<>(Arrays.asList(
        "stdUV", "stdBake", "stdCool", "stdCont", "stdMech"));

    private W24CureScienceFixture() { }

    public static Object run(Model model, Map<String, Object> args) {
        String action = String.valueOf(args.getOrDefault("action", "readback"));
        if ("readback".equals(action)) return readback(model);
        if ("configure_continuous".equals(action)) return configureContinuous(model, args);
        if ("configure_scenario".equals(action)) return configureScenario(model, args);
        if ("study_run".equals(action)) return studyRun(model, args);
        if ("solution_snapshot".equals(action)) return solutionSnapshot(model, args);
        if ("cure_metrics_capture".equals(action)) return cureMetricsCapture(model, args);
        if ("history_capture_v2".equals(action)) return historyCaptureV2(model, args);
        if ("mechanics_build".equals(action)) return mechanicsBuild(model, args);
        if ("mechanics_study_run".equals(action)) return mechanicsStudyRun(model, args);
        if ("mechanics_capture".equals(action)) return mechanicsCapture(model, args);
        throw new IllegalArgumentException("unsupported science action: " + action);
    }

    /** Build an isolated homogeneous axisymmetric mechanics benchmark model.
     * This is intentionally separate from the four-material cure coupon: the
     * exact homogeneous eigenstrain has a closed-form free-expansion and fully
     * restrained solution and therefore validates all four mapped native
     * stress components before the cure-history solves.
     */
    private static Map<String, Object> mechanicsBuild(Model parent, Map<String, Object> args) {
        String caseId = safeToken(args.get("case_id"), "case_id");
        if (!("mechanics_free_expansion".equals(caseId) || "mechanics_fully_fixed".equals(caseId))) {
            throw new IllegalArgumentException("mechanics case is outside the two frozen analytic benchmarks");
        }
        Set<String> priorModelTags = new HashSet<>(Arrays.asList(ModelUtil.tags()));
        Model createdModel = ModelUtil.createUnique("w24mech_" + caseId);
        String modelTag = null;
        for (String candidate : ModelUtil.tags()) {
            if (!priorModelTags.contains(candidate) && candidate.startsWith("w24mech_" + caseId)) {
                if (modelTag != null) throw new IllegalStateException("COMSOL created multiple benchmark models");
                modelTag = candidate;
            }
        }
        if (modelTag == null) throw new IllegalStateException("COMSOL did not register the unique benchmark model");
        boolean complete = false;
        try {
            Model model = createdModel;
            model.component().create(COMPONENT);
            GeomSequence geom = model.component(COMPONENT).geom().create(GEOMETRY, 2);
            geom.lengthUnit("m");
            geom.axisymmetric(true);
            geom.feature().create("coupon", "Rectangle");
            geom.feature("coupon").set("base", "corner");
            geom.feature("coupon").set("pos", new String[]{"0[m]", "0[m]"});
            geom.feature("coupon").set("size", new String[]{"100[um]", "200[um]"});
            geom.run();
            if (geom.getSDim() != 2 || !geom.isAxisymmetric() || geom.getNDomains() != 1) {
                throw new IllegalStateException("mechanics benchmark geometry must be one 2-D axisymmetric domain");
            }

            final double tol = 1e-10;
            mechanicsBoxSelection(model, "sel_domain", 2, -tol, 100e-6 + tol,
                -tol, 200e-6 + tol);
            mechanicsBoxSelection(model, "sel_origin", 0, -tol, tol, -tol, tol);
            mechanicsBoxSelection(model, "sel_outer", 1, 100e-6 - tol, 100e-6 + tol,
                -tol, 200e-6 + tol);
            mechanicsBoxSelection(model, "sel_bottom", 1, -tol, 100e-6 + tol,
                -tol, tol);
            mechanicsBoxSelection(model, "sel_top", 1, -tol, 100e-6 + tol,
                200e-6 - tol, 200e-6 + tol);
            int[] domainIds = model.component(COMPONENT).selection("sel_domain").entities(2);
            int[] originIds = model.component(COMPONENT).selection("sel_origin").entities(0);
            int[] outerIds = model.component(COMPONENT).selection("sel_outer").entities(1);
            int[] bottomIds = model.component(COMPONENT).selection("sel_bottom").entities(1);
            int[] topIds = model.component(COMPONENT).selection("sel_top").entities(1);
            if (domainIds.length != 1 || originIds.length != 1 || outerIds.length != 1 ||
                bottomIds.length != 1 || topIds.length != 1 ||
                outerIds[0] == bottomIds[0] || outerIds[0] == topIds[0] ||
                bottomIds[0] == topIds[0]) {
                throw new IllegalStateException("mechanics benchmark named selections are not unique/exact");
            }

            model.param().set("Emech", "2[GPa]");
            model.param().set("nuMech", "0.35");
            model.param().set("epsVol", "3e-4");
            model.material().create("matMech", "Common", COMPONENT);
            model.material("matMech").selection().named("sel_domain");
            model.material("matMech").propertyGroup("def").set("density", "1200[kg/m^3]");
            model.material("matMech").propertyGroup("def").set("youngsmodulus", "Emech");
            model.material("matMech").propertyGroup("def").set("poissonsratio", "nuMech");

            model.component(COMPONENT).physics().create("solid", "SolidMechanics", GEOMETRY);
            model.physics("solid").prop("StructuralTransientBehavior")
                .set("StructuralTransientBehavior", "Quasistatic");
            PhysicsFeature linearElastic = linearElasticMaterial(model);
            PhysicsFeature eigenstrain = linearElastic.feature().create("estrain", "ExternalStrain", 2);
            if (!"ExternalStrain".equals(eigenstrain.getType())) {
                throw new IllegalStateException("mechanics External Strain child has wrong COMSOL type: " +
                    eigenstrain.getType());
            }
            eigenstrain.selection().named("sel_domain");
            eigenstrain.set("StrainInput", "VolumetricStrain");
            eigenstrain.set("dV", "epsVol");
            int[] selectedEigenstrainDomains = eigenstrain.selection().entities();
            String strainInput = eigenstrain.getString("StrainInput");
            String volumetricStrain = eigenstrain.getString("dV");
            if (!sameEntitySet(domainIds, selectedEigenstrainDomains) ||
                !"VolumetricStrain".equals(strainInput) || !"epsVol".equals(volumetricStrain)) {
                throw new IllegalStateException("mechanics benchmark volumetric eigenstrain readback mismatch");
            }

            if ("mechanics_free_expansion".equals(caseId)) {
                // The axisymmetric formulation already imposes ur=0 on r=0;
                // the origin Fixed point removes only rigid axial translation.
                model.physics("solid").create("anchor", "Fixed", 0);
                model.physics("solid").feature("anchor").selection().named("sel_origin");
                if (!sameEntitySet(originIds,
                        model.physics("solid").feature("anchor").selection().entities())) {
                    throw new IllegalStateException("free benchmark origin gauge selection readback mismatch");
                }
            } else {
                String[] fixedTags = {"fix_outer", "fix_bottom", "fix_top"};
                String[] selectionTags = {"sel_outer", "sel_bottom", "sel_top"};
                int[][] selectedIds = {outerIds, bottomIds, topIds};
                for (int i = 0; i < fixedTags.length; i++) {
                    model.physics("solid").create(fixedTags[i], "Fixed", 1);
                    PhysicsFeature fixed = model.physics("solid").feature(fixedTags[i]);
                    fixed.selection().named(selectionTags[i]);
                    if (!sameEntitySet(selectedIds[i], fixed.selection().entities())) {
                        throw new IllegalStateException("fully-fixed exterior boundary selection mismatch: " + fixedTags[i]);
                    }
                }
            }

            MeshSequence mesh = model.component(COMPONENT).mesh().create("mesh1", GEOMETRY);
            mesh.feature("size").set("custom", "on");
            mesh.feature("size").set("hmax", 10e-6);
            mesh.feature("size").set("hmin", 1e-6);
            mesh.run();

            Study study = model.study().create("stdMech");
            study.create("stat", "Stationary");
            study.createAutoSequences("sol");
            SolverSequence sequence = requireUniqueAttachedSolverSequence(
                model, study, "stdMech", "stat", "Stationary");

            String inventoryPath = String.valueOf(args.getOrDefault("equation_view_path", ""));
            if (inventoryPath.isBlank()) {
                throw new IllegalArgumentException("mechanics_build requires equation_view_path for raw native descriptors");
            }
            Map<String, Object> inventoryReceipt = mechanicsExpressionInventory(model,
                Path.of(inventoryPath));
            int fixedBoundaryCount = "mechanics_free_expansion".equals(caseId)
                ? model.physics("solid").feature("anchor").selection().entities().length
                : outerIds.length + bottomIds.length + topIds.length;
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("status", "MECHANICS_BENCHMARK_BUILT_NOT_SOLVED");
            result.put("case_id", caseId);
            result.put("model_tag", modelTag);
            result.put("source_parent_model_tag", parent.tag());
            result.put("study_tag", "stdMech");
            result.put("solver_sequence", sequence.tag());
            result.put("geometry_dimension", geom.getSDim());
            result.put("geometry_axisymmetric", geom.isAxisymmetric());
            result.put("domain_ids", boxed(domainIds));
            result.put("origin_point_ids", boxed(originIds));
            result.put("physical_exterior_boundary_ids", boxed(
                new int[]{outerIds[0], bottomIds[0], topIds[0]}));
            result.put("fixed_selection_entity_count", fixedBoundaryCount);
            result.put("free_axis_gauge", "Fixed point at r=0,z=0; axisymmetry supplies radial zero");
            result.put("youngs_modulus", model.param().get("Emech"));
            result.put("poisson_ratio", model.param().get("nuMech"));
            result.put("volumetric_strain", model.param().get("epsVol"));
            result.put("external_strain_parent_tag", "lemm1");
            result.put("external_strain_parent_feature_type", linearElastic.getType());
            result.put("external_strain_parent_child_tags", Arrays.asList(linearElastic.feature().tags()));
            result.put("external_strain_feature_tag", "estrain");
            result.put("external_strain_feature_type", eigenstrain.getType());
            result.put("external_strain_selection", boxed(selectedEigenstrainDomains));
            result.put("external_strain_type", strainInput);
            result.put("external_strain_expression", volumetricStrain);
            result.put("quasistatic_readback", model.physics("solid").prop("StructuralTransientBehavior")
                .getString("StructuralTransientBehavior"));
            result.put("study_run_calls", 0);
            result.put("equation_view_inventory", inventoryReceipt);
            complete = true;
            return result;
        } finally {
            if (!complete) ModelUtil.remove(modelTag);
        }
    }

    private static void mechanicsBoxSelection(Model model, String tag, int dimension,
            double xmin, double xmax, double ymin, double ymax) {
        model.component(COMPONENT).selection().create(tag, "Box");
        model.component(COMPONENT).selection(tag).set("entitydim", dimension);
        model.component(COMPONENT).selection(tag).set("xmin", xmin);
        model.component(COMPONENT).selection(tag).set("xmax", xmax);
        model.component(COMPONENT).selection(tag).set("ymin", ymin);
        model.component(COMPONENT).selection(tag).set("ymax", ymax);
        model.component(COMPONENT).selection(tag).set("condition", "inside");
    }

    private static PhysicsFeature linearElasticMaterial(Model model) {
        PhysicsFeature parent = model.physics("solid").feature("lemm1");
        String parentType = parent.getType();
        if (!"LinearElasticModel".equals(parentType)) {
            throw new IllegalStateException("solid.lemm1 must be the native LinearElasticModel feature; got " +
                parentType);
        }
        return parent;
    }

    private static Map<String, Object> mechanicsExpressionInventory(Model model, Path output) {
        Path absolute = output.toAbsolutePath().normalize();
        Path parent = absolute.getParent();
        if (parent == null || !absolute.toString().startsWith("/private/tmp/comsol-mcp-w24-cure-") ||
            !Files.isDirectory(parent) || Files.exists(absolute)) {
            throw new IllegalArgumentException("mechanics Equation View inventory must be a new W24 private artifact");
        }
        String[] physicsTags = model.component(COMPONENT).physics().tags();
        List<Object> featureTables = new ArrayList<>();
        List<Object> errors = new ArrayList<>();
        List<String> solidTags = new ArrayList<>();
        int featureCount = 0;
        int expressionRows = 0;
        int candidateRows = 0;
        for (String physicsTag : physicsTags) {
            if (!"solid".equals(physicsTag)) continue;
            solidTags.add(physicsTag);
            Physics physics = model.component(COMPONENT).physics().get(physicsTag);
            for (String featureTag : physics.feature().tags()) {
                featureCount++;
                Map<String, Object> tableRow = new LinkedHashMap<>();
                tableRow.put("physics_tag", physicsTag);
                tableRow.put("feature_tag", featureTag);
                try {
                    FeatureInfo info = physics.feature().get(featureTag).featureInfo("info");
                    String[][] table = info.getInfoTable("Expression", "recursive", "all");
                    List<Object> rawRows = new ArrayList<>();
                    List<Object> candidates = new ArrayList<>();
                    for (int i = 0; i < table.length; i++) {
                        String[] row = table[i];
                        List<Object> raw = row == null ? null : new ArrayList<Object>(Arrays.asList(row.clone()));
                        rawRows.add(raw);
                        if (row != null) {
                            List<String> cues = mechanicsStressCues(row);
                            if (!cues.isEmpty()) {
                                Map<String, Object> candidate = new LinkedHashMap<>();
                                candidate.put("row_index", i);
                                candidate.put("cues", cues);
                                candidate.put("raw_row", raw);
                                candidates.add(candidate);
                            }
                        }
                    }
                    tableRow.put("status", "READ");
                    tableRow.put("row_count", table.length);
                    tableRow.put("raw_rows", rawRows);
                    tableRow.put("stress_candidate_rows", candidates);
                    expressionRows += table.length;
                    candidateRows += candidates.size();
                } catch (Exception exception) {
                    Map<String, Object> error = new LinkedHashMap<>();
                    error.put("physics_tag", physicsTag);
                    error.put("feature_tag", featureTag);
                    error.put("exception_type", exception.getClass().getName());
                    error.put("message", String.valueOf(exception.getMessage()));
                    errors.add(error);
                    tableRow.put("status", "READ_FAILED");
                    tableRow.put("error", error);
                }
                featureTables.add(tableRow);
            }
        }
        boolean complete = solidTags.size() == 1 && featureCount > 0 && errors.isEmpty();
        Map<String, Object> artifact = new LinkedHashMap<>();
        artifact.put("schema", "W24_COMSOL_EQUATION_VIEW_EXPRESSION_INVENTORY_V1");
        artifact.put("status", complete ? "COMPLETE_NOT_EVALUATED" : "INCOMPLETE_NOT_EVALUATED");
        artifact.put("complete", complete);
        artifact.put("read_only", true);
        artifact.put("model_mutations", 0);
        artifact.put("study_run_calls", 0);
        artifact.put("component_tag", COMPONENT);
        artifact.put("observed_component_physics_tags", Arrays.asList(physicsTags));
        artifact.put("solid_physics_tags", solidTags);
        artifact.put("feature_tags_are_native_observations", true);
        artifact.put("table_request", Arrays.asList("Expression", "recursive", "all"));
        artifact.put("table_request_source", "COMSOL 6.4 FeatureInfo.getInfoTable(String,String...) API documentation");
        artifact.put("candidate_rule",
            "candidate rows have case-insensitive stress/cauchy/shear text or a solid.s[a-z0-9_]* identifier; optional component cues are hoop/radial/circumferential/azimuthal; full raw rows are preserved; discovery only");
        artifact.put("feature_count", featureCount);
        artifact.put("expression_row_count", expressionRows);
        artifact.put("stress_candidate_row_count", candidateRows);
        artifact.put("errors", errors);
        artifact.put("feature_tables", featureTables);
        byte[] bytes = (toJson(artifact) + "\n").getBytes(StandardCharsets.UTF_8);
        writeNewAndSync(absolute, bytes);
        Map<String, Object> receipt = new LinkedHashMap<>();
        receipt.put("status", complete ? "COMPLETE_NOT_EVALUATED" : "INCOMPLETE_NOT_EVALUATED");
        receipt.put("path", absolute.toString());
        receipt.put("size_bytes", bytes.length);
        receipt.put("sha256", sha256(bytes));
        receipt.put("feature_count", featureCount);
        receipt.put("expression_row_count", expressionRows);
        receipt.put("stress_candidate_row_count", candidateRows);
        return receipt;
    }

    private static List<String> mechanicsStressCues(String[] row) {
        StringBuilder joined = new StringBuilder();
        for (String cell : row) if (cell != null) joined.append(cell).append('\u001f');
        String text = joined.toString().toLowerCase(java.util.Locale.ROOT);
        boolean solidStressIdentifier = java.util.regex.Pattern
            .compile("(?i)(?<![a-z0-9_])solid\\.s[a-z0-9_]*(?![a-z0-9_])")
            .matcher(text).find();
        List<String> cues = new ArrayList<>();
        for (String cue : new String[]{"stress", "cauchy", "shear"}) if (text.contains(cue)) cues.add(cue);
        if (cues.isEmpty() && !solidStressIdentifier) return cues;
        for (String cue : new String[]{"hoop", "radial", "circumferential", "azimuthal"}) {
            if (text.contains(cue)) cues.add(cue);
        }
        if (solidStressIdentifier) cues.add("solid.s-prefixed-identifier");
        return cues;
    }

    private static Map<String, Object> mechanicsStudyRun(Model model, Map<String, Object> args) {
        String caseId = safeToken(args.get("case_id"), "case_id");
        String ledgerText = String.valueOf(args.getOrDefault("ledger_path", ""));
        if (!("mechanics_free_expansion".equals(caseId) || "mechanics_fully_fixed".equals(caseId)) ||
            ledgerText.isBlank() || !Arrays.asList(model.study().tags()).contains("stdMech")) {
            throw new IllegalArgumentException("mechanics_study_run requires a built benchmark model and ledger");
        }
        requireMechanicsModel(model, caseId);
        Study study = model.study("stdMech");
        SolverSequence sequence = requireUniqueAttachedSolverSequence(
            model, study, "stdMech", "stat", "Stationary");
        int ordinal = appendInvocationBeforeStudyRun(Path.of(ledgerText), caseId, "stdMech");
        long started = System.nanoTime();
        study.run();
        double elapsed = (System.nanoTime() - started) / 1.0e9;
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "NATIVE_STUDY_RUN_RETURNED");
        result.put("submission_index", ordinal);
        result.put("case_id", caseId);
        result.put("study_tag", "stdMech");
        result.put("solver_sequence", sequence.tag());
        result.put("elapsed_s", elapsed);
        result.put("study_run_calls_from_this_action", 1);
        return result;
    }

    private static Map<String, Object> mechanicsCapture(Model model, Map<String, Object> args) {
        String caseId = safeToken(args.get("case_id"), "case_id");
        String solverTag = safeToken(args.get("solver_tag"), "solver_tag");
        String pathText = String.valueOf(args.getOrDefault("path", ""));
        Map<String, Map<String, String>> stress = readStressDescriptors(args.get("stress_components"));
        if (!("mechanics_free_expansion".equals(caseId) || "mechanics_fully_fixed".equals(caseId)) ||
            pathText.isBlank() || !Arrays.asList(model.sol().tags()).contains(solverTag)) {
            throw new IllegalArgumentException("mechanics_capture requires a solved benchmark, solver, and new artifact path");
        }
        requireMechanicsModel(model, caseId);
        SolverSequence solution = model.sol(solverTag);
        double[] times = solution.getPVals();
        if (times == null || times.length != 1 || !Double.isFinite(times[0])) {
            throw new IllegalStateException("stationary benchmark must contain exactly one native solution parameter");
        }
        String datasetTag = "w24mds" + Long.toUnsignedString(System.nanoTime(), 36);
        List<String> numericalTags = new ArrayList<>();
        boolean datasetCreated = false;
        Map<String, Object> receipt = new LinkedHashMap<>();
        try {
            DatasetFeature dataset = model.result().dataset().create(datasetTag, "Solution");
            datasetCreated = true;
            dataset.set("solution", solverTag);
            if (!solverTag.equals(dataset.getString("solution"))) {
                throw new IllegalStateException("mechanics dataset solution binding readback mismatch");
            }
            int[] domainIds = model.component(COMPONENT).selection("sel_domain").entities(2);
            if (domainIds.length != 1) throw new IllegalStateException("mechanics capture requires one selected domain");

            String[] roles = {"radial_normal", "hoop_normal", "axial_normal", "rz_shear"};
            String[] expressions = new String[roles.length];
            String[] stressUnits = new String[roles.length];
            String[] absoluteExpressions = new String[roles.length];
            for (int i = 0; i < roles.length; i++) {
                expressions[i] = stress.get(roles[i]).get("expression");
                stressUnits[i] = stress.get(roles[i]).get("unit");
                absoluteExpressions[i] = "abs(" + expressions[i] + ")";
            }
            NumericalValues mean = mechanicsNumerical(model, datasetTag, numericalTags,
                "AvVolume", "stress_mean", expressions, stressUnits, domainIds);
            NumericalValues stressMaximum = mechanicsNumerical(model, datasetTag, numericalTags,
                "MaxVolume", "stress_abs_max", absoluteExpressions, stressUnits, domainIds);
            NumericalValues displacementMaximum = mechanicsNumerical(model, datasetTag, numericalTags,
                "MaxVolume", "displacement_abs_max", new String[]{"abs(u)", "abs(w)"},
                new String[]{"m", "m"}, domainIds);

            String interpTag = "w24mi" + Long.toUnsignedString(System.nanoTime(), 36);
            NumericalFeature interp = model.result().numerical().create(interpTag, "Interp");
            numericalTags.add(interpTag);
            double[][] coordinates = new double[][]{{100e-6}, {200e-6}};
            interp.set("data", datasetTag);
            interp.set("expr", new String[]{"u", "w"});
            interp.set("unit", new String[]{"m", "m"});
            interp.set("solnum", "all");
            interp.set("coorderr", "on");
            interp.set("matherr", "on");
            interp.setInterpolationCoordinates(coordinates);
            if (!datasetTag.equals(interp.getString("data")) ||
                !Arrays.equals(new String[]{"u", "w"}, interp.getStringArray("expr")) ||
                !Arrays.equals(new String[]{"m", "m"}, interp.getStringArray("unit")) ||
                !"all".equals(interp.getString("solnum")) ||
                !interp.getBoolean("coorderr") || !interp.getBoolean("matherr")) {
                throw new IllegalStateException("mechanics displacement probe configuration readback mismatch");
            }
            interp.run();
            double[][][] displacementProbe = interp.getData();
            if (displacementProbe == null || displacementProbe.length != 2 ||
                displacementProbe[0].length != 1 || displacementProbe[1].length != 1 ||
                displacementProbe[0][0].length != 1 || displacementProbe[1][0].length != 1 ||
                !Double.isFinite(displacementProbe[0][0][0]) || !Double.isFinite(displacementProbe[1][0][0])) {
                throw new IllegalStateException("mechanics displacement probe returned invalid native values");
            }
            Map<String, Object> descriptors = new LinkedHashMap<>();
            Map<String, Object> meanValues = new LinkedHashMap<>();
            Map<String, Object> maximumValues = new LinkedHashMap<>();
            for (int i = 0; i < roles.length; i++) {
                descriptors.put(roles[i], new LinkedHashMap<>(stress.get(roles[i])));
                meanValues.put(roles[i], mean.values[i][0]);
                maximumValues.put(roles[i], stressMaximum.values[i][0]);
            }
            Map<String, Object> readbacks = new LinkedHashMap<>();
            readbacks.put("stress_mean", mean.readback);
            readbacks.put("stress_abs_max", stressMaximum.readback);
            readbacks.put("displacement_abs_max", displacementMaximum.readback);
            Map<String, Object> interpReadback = new LinkedHashMap<>();
            interpReadback.put("type", "Interp");
            interpReadback.put("dataset", interp.getString("data"));
            interpReadback.put("expression", Arrays.asList(interp.getStringArray("expr")));
            interpReadback.put("unit", Arrays.asList(interp.getStringArray("unit")));
            interpReadback.put("solution_selection", interp.getString("solnum"));
            interpReadback.put("coordinate_error", interp.getString("coorderr"));
            interpReadback.put("math_error", interp.getString("matherr"));
            interpReadback.put("coordinates_m", Arrays.asList(Arrays.asList(100e-6, 200e-6)));
            interpReadback.put("shape", Arrays.asList(2, 1, 1));
            readbacks.put("displacement_probe", interpReadback);

            Map<String, Object> artifact = new LinkedHashMap<>();
            artifact.put("schema", "W24_NATIVE_MECHANICS_METRICS_V1");
            artifact.put("status", "NATIVE_MECHANICS_METRICS_CAPTURED");
            artifact.put("native", true);
            artifact.put("native_study_run_calls", 0);
            artifact.put("case_id", caseId);
            artifact.put("solver_tag", solverTag);
            artifact.put("dataset_tag", datasetTag);
            artifact.put("stored_times_s", boxed(times));
            artifact.put("stress_component_descriptors", descriptors);
            artifact.put("stress_mean_pa", meanValues);
            artifact.put("stress_abs_max_pa", maximumValues);
            artifact.put("displacement_abs_max_m", Arrays.asList(
                displacementMaximum.values[0][0], displacementMaximum.values[1][0]));
            artifact.put("displacement_probe_m", Arrays.asList(
                displacementProbe[0][0][0], displacementProbe[1][0][0]));
            artifact.put("displacement_probe_coordinate_m", Arrays.asList(100e-6, 200e-6));
            artifact.put("feature_readbacks", readbacks);
            Path output = Path.of(pathText);
            byte[] bytes = (toJson(artifact) + "\n").getBytes(StandardCharsets.UTF_8);
            writeNewAndSync(output, bytes);

            receipt.put("status", "NATIVE_MECHANICS_METRICS_CAPTURED");
            receipt.put("path", output.toString());
            receipt.put("size_bytes", bytes.length);
            receipt.put("sha256", sha256(bytes));
            receipt.put("case_id", caseId);
            receipt.put("solver_tag", solverTag);
            receipt.put("dataset_tag", datasetTag);
            receipt.put("native_study_run_calls", 0);
        } finally {
            RuntimeException cleanupFailure = null;
            List<String> removedTags = new ArrayList<>();
            for (String tag : numericalTags) {
                try { model.result().numerical().remove(tag); removedTags.add(tag); }
                catch (RuntimeException exception) { if (cleanupFailure == null) cleanupFailure = exception; }
            }
            boolean removedDataset = false;
            if (datasetCreated) {
                try { model.result().dataset().remove(datasetTag); removedDataset = true; }
                catch (RuntimeException exception) { if (cleanupFailure == null) cleanupFailure = exception; }
            }
            receipt.put("temporary_numerical_tags_removed", removedTags);
            receipt.put("temporary_dataset_removed", removedDataset);
            if (cleanupFailure != null) {
                throw new IllegalStateException("mechanics artifact captured but temporary result cleanup failed",
                    cleanupFailure);
            }
        }
        return receipt;
    }

    private static void requireMechanicsModel(Model model, String caseId) {
        String expectedPrefix = "w24mech_" + caseId;
        String actualTag = model.tag();
        if (actualTag == null || !actualTag.startsWith(expectedPrefix)) {
            throw new IllegalArgumentException("mechanics action ModelRef must identify its individually adopted native model; expected tag prefix " +
                expectedPrefix + ", got " + actualTag);
        }
    }

    private static NumericalValues mechanicsNumerical(Model model, String datasetTag,
            List<String> tags, String type, String role, String[] expressions,
            String[] units, int[] domainIds) {
        String tag = "w24m" + Long.toUnsignedString(System.nanoTime(), 36);
        NumericalFeature feature = model.result().numerical().create(tag, type);
        tags.add(tag);
        feature.set("data", datasetTag);
        feature.selection().geom(GEOMETRY, 2);
        feature.selection().set(domainIds);
        feature.set("expr", expressions);
        feature.set("unit", units);
        feature.set("solnum", "all");
        if ("AvVolume".equals(type)) feature.set("intvolume", "on");
        if (!datasetTag.equals(feature.getString("data")) ||
            !Arrays.equals(expressions, feature.getStringArray("expr")) ||
            !Arrays.equals(units, feature.getStringArray("unit")) ||
            !"all".equals(feature.getString("solnum")) ||
            feature.selection().dim() != 2 || !"geom1".equals(feature.selection().geom()) ||
            !sameEntitySet(domainIds, feature.selection().entities())) {
            throw new IllegalStateException("mechanics numerical readback mismatch for " + role);
        }
        if ("AvVolume".equals(type) && !"on".equals(feature.getString("intvolume"))) {
            throw new IllegalStateException("mechanics stress mean lacks axisymmetric physical measure");
        }
        feature.run();
        double[][] values = feature.getReal();
        verifyMatrix(values, expressions.length, 1, role);
        Map<String, Object> readback = new LinkedHashMap<>();
        readback.put("native_tag", tag);
        readback.put("type", type);
        readback.put("dataset", feature.getString("data"));
        readback.put("expression", Arrays.asList(feature.getStringArray("expr")));
        readback.put("unit", Arrays.asList(feature.getStringArray("unit")));
        readback.put("solution_selection", feature.getString("solnum"));
        readback.put("geometry", feature.selection().geom());
        readback.put("entity_dimension", feature.selection().dim());
        readback.put("entity_ids", boxed(feature.selection().entities()));
        readback.put("axisymmetric_measure_property", "AvVolume".equals(type) ? "intvolume" : null);
        readback.put("axisymmetric_measure_value", "AvVolume".equals(type) ? feature.getString("intvolume") : null);
        readback.put("shape", Arrays.asList(values.length, values[0].length));
        return new NumericalValues(values, readback);
    }

    /** Capture native averages, axisymmetric heat integrals, and mapped stress values.
     * Every numerical result is tied to the exact stored solution dataset and is
     * written as a separate, hash-bound artifact before this action returns.
     */
    private static Map<String, Object> cureMetricsCapture(Model model, Map<String, Object> args) {
        String solverTag = safeToken(args.get("solver_tag"), "solver_tag");
        String pathText = String.valueOf(args.getOrDefault("path", ""));
        if (pathText.isBlank()) throw new IllegalArgumentException("cure_metrics_capture requires output path");
        Map<String, Map<String, String>> stress = readStressDescriptors(args.get("stress_components"));
        SolverSequence solution = model.sol(solverTag);
        double[] times = solution.getPVals();
        if (times == null || times.length < 2) {
            throw new IllegalStateException("native cure metric capture requires at least two stored solution times");
        }
        for (int i = 0; i < times.length; i++) {
            if (!Double.isFinite(times[i]) || (i > 0 && times[i] <= times[i - 1])) {
                throw new IllegalStateException("native solution times are nonfinite or not strictly increasing");
            }
        }

        String suffix = Long.toUnsignedString(System.nanoTime(), 36);
        String datasetTag = "w24ds" + suffix;
        List<String> numericalTags = new ArrayList<>();
        Map<String, Object> featureReadbacks = new LinkedHashMap<>();
        Map<String, Object> receipt = new LinkedHashMap<>();
        boolean datasetCreated = false;
        try {
            DatasetFeature dataset = model.result().dataset().create(datasetTag, "Solution");
            datasetCreated = true;
            dataset.set("solution", solverTag);
            if (!solverTag.equals(dataset.getString("solution"))) {
                throw new IllegalStateException("native solution dataset did not read back its exact solver sequence");
            }

            NumericalValues means = evaluateNumerical(model, datasetTag, numericalTags,
                "AvVolume", "alpha", new String[]{"alpha_iso", "alpha", "qpost"},
                new String[]{"1", "1", "1"}, 2,
                model.component(COMPONENT).selection("sel_adhesive").entities(2),
                "on", times.length);
            featureReadbacks.put("adhesive_cure_averages", means.readback);

            String[] domainSelections = {"sel_alumina", "sel_gold", "sel_adhesive", "sel_fiber"};
            String[] domainLabels = {"alumina", "gold", "adhesive", "fiber"};
            String[] sensibleHeatExpressions = nativeSensibleHeatExpressions(model);
            Map<String, double[]> storedEnergyByDomain = new LinkedHashMap<>();
            Map<String, Object> energyReadbacks = new LinkedHashMap<>();
            double[] storedEnergy = new double[times.length];
            for (int i = 0; i < domainSelections.length; i++) {
                NumericalValues energy = evaluateNumerical(model, datasetTag, numericalTags,
                    "IntVolume", "energy_" + domainLabels[i],
                    new String[]{sensibleHeatExpressions[i]}, new String[]{"J"}, 2,
                    model.component(COMPONENT).selection(domainSelections[i]).entities(2),
                    "on", times.length);
                storedEnergyByDomain.put(domainLabels[i], energy.values[0]);
                energyReadbacks.put("energy_" + domainLabels[i], energy.readback);
                for (int timeIndex = 0; timeIndex < times.length; timeIndex++) {
                    storedEnergy[timeIndex] += energy.values[0][timeIndex];
                }
            }
            NumericalValues reaction = evaluateNumerical(model, datasetTag, numericalTags,
                "IntVolume", "reaction_power", new String[]{"Qrxn"}, new String[]{"W"}, 2,
                model.component(COMPONENT).selection("sel_adhesive").entities(2), "on", times.length);
            energyReadbacks.put("reaction_power", reaction.readback);

            int[] externalEdges = uniqueExternalEdges(model);
            NumericalValues outward = evaluateNumerical(model, datasetTag, numericalTags,
                "IntSurface", "outward_power", new String[]{"hconv*(T-Tenv)"}, new String[]{"W"}, 1,
                externalEdges, "on", times.length);
            energyReadbacks.put("outward_power", outward.readback);
            featureReadbacks.putAll(energyReadbacks);

            String[] stressRoles = {"radial_normal", "hoop_normal", "axial_normal", "rz_shear"};
            String[] stressExpressions = new String[stressRoles.length];
            String[] stressUnits = new String[stressRoles.length];
            for (int i = 0; i < stressRoles.length; i++) {
                Map<String, String> descriptor = stress.get(stressRoles[i]);
                stressExpressions[i] = descriptor.get("expression");
                stressUnits[i] = descriptor.get("unit");
            }
            NumericalValues stressMean = evaluateNumerical(model, datasetTag, numericalTags,
                "AvVolume", "adhesive_mean_stress", stressExpressions, stressUnits, 2,
                model.component(COMPONENT).selection("sel_adhesive").entities(2), "on", times.length);
            featureReadbacks.put("adhesive_mean_stress", stressMean.readback);

            double[][] coordinates = new double[][]{
                new double[]{25e-6, 50e-6, 75e-6},
                new double[]{520e-6, 530e-6, 540e-6}
            };
            NumericalValues probes = evaluateInterpolation(model, datasetTag, numericalTags,
                "stress_probes", stressExpressions, stressUnits, coordinates, times.length);
            featureReadbacks.put("stress_probes", probes.readback);

            // Frozen coarse/fine sensitivity metric: a native displacement
            // interpolation at the upper fiber end, not a summary maximum.
            double[][] fiberEndCoordinates = new double[][]{
                new double[]{62.5e-6}, new double[]{1050e-6}
            };
            NumericalValues fiberEnd = evaluateInterpolation(model, datasetTag, numericalTags,
                "fiber_end_displacement", new String[]{"u", "w"},
                new String[]{"m", "m"}, fiberEndCoordinates, times.length);
            featureReadbacks.put("fiber_end_displacement", fiberEnd.readback);

            List<Double> timeRows = boxed(times);
            List<Double> alphaIso = boxed(means.values[0]);
            List<Double> alpha = boxed(means.values[1]);
            List<Double> qpost = boxed(means.values[2]);
            List<Map<String, Object>> energySamples = new ArrayList<>();
            for (int i = 0; i < times.length; i++) {
                Map<String, Object> sample = new LinkedHashMap<>();
                sample.put("time_s", times[i]);
                sample.put("stored_energy_j", storedEnergy[i]);
                Map<String, Double> domainEnergy = new LinkedHashMap<>();
                for (String label : domainLabels) domainEnergy.put(label, storedEnergyByDomain.get(label)[i]);
                sample.put("stored_energy_by_domain_j", domainEnergy);
                sample.put("reaction_power_w", reaction.values[0][i]);
                sample.put("outward_power_w", outward.values[0][i]);
                sample.put("outward_sign", "positive_outward");
                energySamples.add(sample);
            }

            Map<String, Object> stressDescriptors = new LinkedHashMap<>();
            Map<String, Object> meanStress = new LinkedHashMap<>();
            Map<String, Object> probeStress = new LinkedHashMap<>();
            double[][] stressProbeValues = probes.values;
            double[][] stressMeanValues = stressMean.values;
            for (int i = 0; i < stressRoles.length; i++) {
                String role = stressRoles[i];
                stressDescriptors.put(role, new LinkedHashMap<>(stress.get(role)));
                meanStress.put(role, boxed(stressMeanValues[i]));
                List<List<Double>> perTime = new ArrayList<>();
                for (int t = 0; t < times.length; t++) {
                    perTime.add(Arrays.asList(stressProbeValues[i][t * 3],
                        stressProbeValues[i][t * 3 + 1], stressProbeValues[i][t * 3 + 2]));
                }
                probeStress.put(role, perTime);
            }

            Map<String, Object> artifact = new LinkedHashMap<>();
            artifact.put("schema", "W24_NATIVE_CURE_METRICS_V1");
            artifact.put("status", "NATIVE_CURE_METRICS_CAPTURED");
            artifact.put("native", true);
            artifact.put("native_study_run_calls", 0);
            artifact.put("solver_tag", solverTag);
            artifact.put("dataset_tag", datasetTag);
            artifact.put("stored_times_s", timeRows);
            artifact.put("alpha_iso_mean", alphaIso);
            artifact.put("alpha_mean", alpha);
            artifact.put("qpost_mean", qpost);
            artifact.put("energy_samples", energySamples);
            artifact.put("stress_component_descriptors", stressDescriptors);
            artifact.put("adhesive_mean_stress_pa", meanStress);
            artifact.put("stress_probe_coordinates_m", Arrays.asList(
                Arrays.asList(25e-6, 520e-6), Arrays.asList(50e-6, 530e-6),
                Arrays.asList(75e-6, 540e-6)));
            artifact.put("stress_probe_values_pa", probeStress);
            List<List<Double>> fiberEndHistory = new ArrayList<>();
            for (int i = 0; i < times.length; i++) {
                fiberEndHistory.add(Arrays.asList(fiberEnd.values[0][i], fiberEnd.values[1][i]));
            }
            artifact.put("fiber_end_displacement_m", fiberEndHistory);
            artifact.put("feature_readbacks", featureReadbacks);
            artifact.put("material_heat_capacity_readback", nativeThermalCapacityReadback(model));
            artifact.put("numerical_shape_contract",
                "IntVolume/IntSurface/AvVolume getReal[expr][stored_solution]; Interp getData[expr][stored_solution][point]");
            artifact.put("axisymmetric_physical_measure_readback", "intvolume=on; intsurface=on");

            Path output = Path.of(pathText);
            byte[] bytes = (toJson(artifact) + "\n").getBytes(StandardCharsets.UTF_8);
            writeNewAndSync(output, bytes);
            receipt.put("status", "NATIVE_CURE_METRICS_CAPTURED");
            receipt.put("path", output.toString());
            receipt.put("size_bytes", bytes.length);
            receipt.put("sha256", sha256(bytes));
            receipt.put("solver_tag", solverTag);
            receipt.put("dataset_tag", datasetTag);
            receipt.put("stored_time_count", times.length);
            receipt.put("stress_descriptors", stressDescriptors);
            receipt.put("feature_readbacks", featureReadbacks);
            receipt.put("native_study_run_calls", 0);
        } finally {
            List<String> removedTags = new ArrayList<>();
            RuntimeException cleanupFailure = null;
            for (String tag : numericalTags) {
                try {
                    model.result().numerical().remove(tag);
                    removedTags.add(tag);
                } catch (RuntimeException exception) {
                    if (cleanupFailure == null) cleanupFailure = exception;
                }
            }
            boolean removedDataset = false;
            if (datasetCreated) {
                try {
                    model.result().dataset().remove(datasetTag);
                    removedDataset = true;
                } catch (RuntimeException exception) {
                    if (cleanupFailure == null) cleanupFailure = exception;
                }
            }
            receipt.put("temporary_numerical_tags_removed", removedTags);
            receipt.put("temporary_dataset_removed", removedDataset);
            if (cleanupFailure != null) {
                throw new IllegalStateException("native metric artifact was captured but temporary numerical cleanup failed",
                    cleanupFailure);
            }
        }
        return receipt;
    }

    /** Capture all visible cure/thermal/displacement/activation histories at fixed adhesive probes.
     * This action only reads an already-solved model; it does not submit Study.run.
     */
    private static Map<String, Object> historyCaptureV2(Model model, Map<String, Object> args) {
        String studyTag = safeToken(args.get("study_tag"), "study_tag");
        String solverTag = safeToken(args.get("solver_tag"), "solver_tag");
        String pathText = String.valueOf(args.getOrDefault("path", ""));
        if (pathText.isBlank() || !Arrays.asList(model.study().tags()).contains(studyTag)) {
            throw new IllegalArgumentException("history_capture_v2 requires an existing study and a new output path");
        }
        if (model.component(COMPONENT).geom(GEOMETRY).getSDim() != 2) {
            throw new IllegalStateException("history_capture_v2 currently accepts only the frozen 2D axisymmetric W24 cure coupon");
        }
        String[] attached = model.study(studyTag).getSolverSequences("SolverSequence");
        if (attached.length != 1 || !solverTag.equals(attached[0])) {
            throw new IllegalStateException("history capture requires the exact unique solver attached to its study");
        }
        SolverSequence solution = model.sol(solverTag);
        if (!solution.isAttached() || !studyTag.equals(solution.study())) {
            throw new IllegalStateException("history capture solver attachment does not match the requested study");
        }
        double[] times = solution.getPVals();
        if (times == null || times.length == 0) {
            throw new IllegalStateException("history capture requires every native stored solution time");
        }
        for (int i = 0; i < times.length; i++) {
            if (!Double.isFinite(times[i]) || (i > 0 && times[i] <= times[i - 1])) {
                throw new IllegalStateException("history capture stored times are nonfinite or non-increasing");
            }
        }
        String[] expressions = {"T", "alpha", "Duv_rel", "qpost", "u", "w",
            "solid.isactive", "solid.wasactive"};
        String[] units = {"K", "1", "s", "1", "m", "m", "1", "1"};
        double[][] coordinates = new double[][]{
            new double[]{25e-6, 50e-6, 75e-6},
            new double[]{520e-6, 530e-6, 540e-6}
        };
        String datasetTag = "w24v2histds" + Long.toUnsignedString(System.nanoTime(), 36);
        List<String> numericalTags = new ArrayList<>();
        boolean datasetCreated = false;
        Map<String, Object> receipt = new LinkedHashMap<>();
        try {
            DatasetFeature dataset = model.result().dataset().create(datasetTag, "Solution");
            datasetCreated = true;
            dataset.set("solution", solverTag);
            if (!solverTag.equals(dataset.getString("solution"))) {
                throw new IllegalStateException("history dataset did not read back the exact SolverSequence");
            }
            NumericalValues values = evaluateInterpolation(model, datasetTag, numericalTags,
                "history_v2", expressions, units, coordinates, times.length);
            List<Object> data = new ArrayList<>();
            for (int expression = 0; expression < expressions.length; expression++) {
                List<Object> expressionTimes = new ArrayList<>();
                for (int time = 0; time < times.length; time++) {
                    List<Double> points = new ArrayList<>();
                    for (int point = 0; point < coordinates[0].length; point++) {
                        double value = values.values[expression][time * coordinates[0].length + point];
                        if (("solid.isactive".equals(expressions[expression]) ||
                             "solid.wasactive".equals(expressions[expression])) && value != 0.0 && value != 1.0) {
                            throw new IllegalStateException("native Activation history values must be exactly zero or one");
                        }
                        points.add(value);
                    }
                    expressionTimes.add(points);
                }
                data.add(expressionTimes);
            }
            StudyFeature timeFeature = model.study(studyTag).feature("time");
            String quasistatic = model.physics("solid").prop("StructuralTransientBehavior")
                .getString("StructuralTransientBehavior");
            if (!"Quasistatic".equals(quasistatic)) {
                throw new IllegalStateException("history capture requires an actual Quasistatic Solid Mechanics readback");
            }
            Map<String, Object> interpolationReadback = new LinkedHashMap<>();
            interpolationReadback.put("type", values.readback.get("type"));
            interpolationReadback.put("dataset", values.readback.get("dataset"));
            interpolationReadback.put("expressions", values.readback.get("expression"));
            interpolationReadback.put("units", values.readback.get("unit"));
            interpolationReadback.put("solnum", values.readback.get("solution_selection"));
            interpolationReadback.put("coorderr", values.readback.get("coordinate_error"));
            interpolationReadback.put("matherr", values.readback.get("math_error"));
            interpolationReadback.put("coordinates_m", values.readback.get("coordinates_m"));
            interpolationReadback.put("coordinate_source", "fixed axisymmetric Java coordinates passed to setInterpolationCoordinates");
            interpolationReadback.put("shape", Arrays.asList(expressions.length, times.length, coordinates[0].length));
            Map<String, Object> artifact = new LinkedHashMap<>();
            artifact.put("schema", "W24_CURE_LAW_V2_HISTORY_CAPTURE_V1");
            artifact.put("status", "NATIVE_HISTORY_CAPTURED_NO_SOLVE_SUBMITTED");
            artifact.put("study_tag", studyTag);
            artifact.put("solver_tag", solverTag);
            artifact.put("dataset_tag", datasetTag);
            artifact.put("dataset_type_requested", "Solution");
            artifact.put("dataset_solution_readback", dataset.getString("solution"));
            artifact.put("stored_times_s", boxed(times));
            artifact.put("time_source", "SolverSequence.getPVals");
            artifact.put("study_tlist_readback", timeFeature.getString("tlist"));
            artifact.put("quasistatic_readback", quasistatic);
            artifact.put("expressions", Arrays.asList(expressions));
            artifact.put("units", Arrays.asList(units));
            artifact.put("coordinates_m", Arrays.asList(Arrays.asList(25e-6, 520e-6),
                Arrays.asList(50e-6, 530e-6), Arrays.asList(75e-6, 540e-6)));
            artifact.put("shape", Arrays.asList(expressions.length, times.length, coordinates[0].length));
            artifact.put("data", data);
            artifact.put("feature_readback", interpolationReadback);
            artifact.put("activation_variables_documented_by", "COMSOL 6.4 Structural Mechanics Module User's Guide page 317");
            artifact.put("maxwell_branch_state", "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE");
            artifact.put("native_study_run_calls", 0);
            byte[] bytes = (toJson(artifact) + "\n").getBytes(StandardCharsets.UTF_8);
            Path output = Path.of(pathText);
            writeNewAndSync(output, bytes);
            receipt.put("status", "NATIVE_HISTORY_CAPTURE_WRITTEN");
            receipt.put("schema", "W24_CURE_LAW_V2_HISTORY_CAPTURE_V1");
            receipt.put("path", output.toString());
            receipt.put("size_bytes", bytes.length);
            receipt.put("sha256", sha256(bytes));
            receipt.put("study_tag", studyTag);
            receipt.put("solver_tag", solverTag);
            receipt.put("dataset_tag", datasetTag);
            receipt.put("stored_time_count", times.length);
            receipt.put("expression_count", expressions.length);
            receipt.put("shape", Arrays.asList(expressions.length, times.length, coordinates[0].length));
            receipt.put("quasistatic_readback", quasistatic);
            receipt.put("native_study_run_calls", 0);
            receipt.put("maxwell_branch_state", "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE");
        } finally {
            RuntimeException cleanupFailure = null;
            for (String tag : numericalTags) {
                try { model.result().numerical().remove(tag); }
                catch (RuntimeException exception) { if (cleanupFailure == null) cleanupFailure = exception; }
            }
            if (datasetCreated) {
                try { model.result().dataset().remove(datasetTag); }
                catch (RuntimeException exception) { if (cleanupFailure == null) cleanupFailure = exception; }
            }
            if (cleanupFailure != null) {
                throw new IllegalStateException("history artifact may be written but temporary result cleanup failed", cleanupFailure);
            }
        }
        return receipt;
    }

    private static final class NumericalValues {
        final double[][] values;
        final Map<String, Object> readback;
        NumericalValues(double[][] values, Map<String, Object> readback) {
            this.values = values;
            this.readback = readback;
        }
    }

    private static NumericalValues evaluateNumerical(Model model, String datasetTag,
            List<String> numericalTags, String type, String role, String[] expressions,
            String[] units, int dimension, int[] entities, String axisymmetricFlag,
            int expectedTimeCount) {
        String tag = "w24n" + Long.toUnsignedString(System.nanoTime(), 36);
        NumericalFeature feature = model.result().numerical().create(tag, type);
        numericalTags.add(tag);
        feature.set("data", datasetTag);
        feature.selection().geom(GEOMETRY, dimension);
        feature.selection().set(entities);
        feature.set("expr", expressions);
        feature.set("unit", units);
        feature.set("solnum", "all");
        String measureKey = dimension == 2 ? "intvolume" : "intsurface";
        feature.set(measureKey, axisymmetricFlag);
        if (!datasetTag.equals(feature.getString("data")) ||
            !Arrays.equals(expressions, feature.getStringArray("expr")) ||
            !Arrays.equals(units, feature.getStringArray("unit")) ||
            !"all".equals(feature.getString("solnum")) ||
            !axisymmetricFlag.equals(feature.getString(measureKey)) ||
            feature.selection().dim() != dimension ||
            !sameEntitySet(entities, feature.selection().entities())) {
            throw new IllegalStateException("native numerical configuration readback mismatch for " + role);
        }
        feature.run();
        double[][] values = feature.getReal();
        verifyMatrix(values, expressions.length, expectedTimeCount, role);
        Map<String, Object> readback = new LinkedHashMap<>();
        readback.put("role", role);
        readback.put("native_tag", tag);
        readback.put("type", type);
        readback.put("dataset", feature.getString("data"));
        readback.put("expression", Arrays.asList(feature.getStringArray("expr")));
        readback.put("unit", Arrays.asList(feature.getStringArray("unit")));
        readback.put("solution_selection", feature.getString("solnum"));
        readback.put("geometry", feature.selection().geom());
        readback.put("entity_dimension", feature.selection().dim());
        readback.put("entity_ids", boxed(feature.selection().entities()));
        readback.put("axisymmetric_measure_property", measureKey);
        readback.put("axisymmetric_measure_value", feature.getString(measureKey));
        readback.put("shape", Arrays.asList(values.length, values[0].length));
        return new NumericalValues(values, readback);
    }

    private static NumericalValues evaluateInterpolation(Model model, String datasetTag,
            List<String> numericalTags, String role, String[] expressions, String[] units,
            double[][] coordinates, int expectedTimeCount) {
        String tag = "w24i" + Long.toUnsignedString(System.nanoTime(), 36);
        NumericalFeature feature = model.result().numerical().create(tag, "Interp");
        numericalTags.add(tag);
        feature.set("data", datasetTag);
        feature.set("expr", expressions);
        feature.set("unit", units);
        feature.set("solnum", "all");
        feature.set("coorderr", "on");
        feature.set("matherr", "on");
        feature.setInterpolationCoordinates(coordinates);
        if (!datasetTag.equals(feature.getString("data")) ||
            !Arrays.equals(expressions, feature.getStringArray("expr")) ||
            !Arrays.equals(units, feature.getStringArray("unit")) ||
            !"all".equals(feature.getString("solnum")) ||
            !feature.getBoolean("coorderr") || !feature.getBoolean("matherr")) {
            throw new IllegalStateException("native interpolation configuration readback mismatch for " + role);
        }
        feature.run();
        double[][][] data = feature.getData();
        if (data == null || data.length != expressions.length) {
            throw new IllegalStateException("native interpolation expression count mismatch for " + role);
        }
        if (coordinates == null || coordinates.length != 2 || coordinates[0] == null ||
            coordinates[1] == null || coordinates[0].length == 0 ||
            coordinates[0].length != coordinates[1].length) {
            throw new IllegalArgumentException("native interpolation coordinates must be matching nonempty r/z arrays");
        }
        int pointCount = coordinates[0].length;
        double[][] values = new double[data.length][expectedTimeCount * pointCount];
        for (int expression = 0; expression < data.length; expression++) {
            if (data[expression] == null || data[expression].length != expectedTimeCount) {
                throw new IllegalStateException("native interpolation accepted-time count mismatch for " + role);
            }
            for (int timeIndex = 0; timeIndex < expectedTimeCount; timeIndex++) {
                if (data[expression][timeIndex] == null ||
                data[expression][timeIndex].length != pointCount) {
                    throw new IllegalStateException("native interpolation point count mismatch for " + role);
                }
                for (int point = 0; point < pointCount; point++) {
                    double value = data[expression][timeIndex][point];
                    if (!Double.isFinite(value)) throw new IllegalStateException("native stress probe is nonfinite");
                    values[expression][timeIndex * coordinates[0].length + point] = value;
                }
            }
        }
        Map<String, Object> readback = new LinkedHashMap<>();
        readback.put("role", role);
        readback.put("native_tag", tag);
        readback.put("type", "Interp");
        readback.put("dataset", feature.getString("data"));
        readback.put("expression", Arrays.asList(feature.getStringArray("expr")));
        readback.put("unit", Arrays.asList(feature.getStringArray("unit")));
        readback.put("solution_selection", feature.getString("solnum"));
        readback.put("coordinate_error", feature.getString("coorderr"));
        readback.put("math_error", feature.getString("matherr"));
        List<List<Double>> coordinateRows = new ArrayList<>();
        for (int point = 0; point < pointCount; point++) {
            coordinateRows.add(Arrays.asList(coordinates[0][point], coordinates[1][point]));
        }
        readback.put("coordinates_m", coordinateRows);
        readback.put("shape", Arrays.asList(data.length, expectedTimeCount, pointCount));
        return new NumericalValues(values, readback);
    }

    private static Map<String, Map<String, String>> readStressDescriptors(Object raw) {
        if (!(raw instanceof Map<?, ?>)) throw new IllegalArgumentException("stress_components must be an object");
        Map<?, ?> source = (Map<?, ?>) raw;
        String[] roles = {"radial_normal", "hoop_normal", "axial_normal", "rz_shear"};
        if (source.size() != roles.length) throw new IllegalArgumentException("exactly four stress roles are required");
        Map<String, Map<String, String>> out = new LinkedHashMap<>();
        Set<String> expressions = new HashSet<>();
        for (String role : roles) {
            Object rowValue = source.get(role);
            if (!(rowValue instanceof Map<?, ?>)) throw new IllegalArgumentException("missing stress role " + role);
            Map<?, ?> row = (Map<?, ?>) rowValue;
            Object expression = row.get("expression");
            Object unit = row.get("unit");
            Object description = row.get("description");
            if (!(expression instanceof String) || ((String) expression).isBlank() ||
                !(unit instanceof String) || !"Pa".equals(unit) ||
                !(description instanceof String) || ((String) description).isBlank() ||
                !expressions.add((String) expression)) {
                throw new IllegalArgumentException("stress descriptors must be unique native expressions with Pa units and descriptions");
            }
            Map<String, String> item = new LinkedHashMap<>();
            item.put("expression", (String) expression);
            item.put("unit", (String) unit);
            item.put("description", (String) description);
            out.put(role, item);
        }
        return out;
    }

    private static int[] uniqueExternalEdges(Model model) {
        String[] tags = {"sel_ext_bottom", "sel_ext_alumina_outer", "sel_ext_alumina_shoulder",
            "sel_ext_gold_outer", "sel_ext_adhesive_outer", "sel_ext_adhesive_shoulder",
            "sel_ext_fiber_outer", "sel_ext_top"};
        Set<Integer> seen = new java.util.TreeSet<>();
        List<Integer> ordered = new ArrayList<>();
        for (String tag : tags) {
            int[] ids = model.component(COMPONENT).selection(tag).entities(1);
            if (ids == null || ids.length != 1) {
                throw new IllegalStateException("each frozen external boundary selection must contain one edge: " + tag);
            }
            for (int id : ids) {
                if (!seen.add(id)) throw new IllegalStateException("external edge is duplicated across named selections: " + id);
                ordered.add(id);
            }
        }
        int[] result = new int[ordered.size()];
        for (int i = 0; i < result.length; i++) result[i] = ordered.get(i);
        return result;
    }

    private static String[] nativeSensibleHeatExpressions(Model model) {
        String[][] rows = nativeThermalCapacityReadback(model);
        String[] expressions = new String[rows.length];
        for (int i = 0; i < rows.length; i++) {
            expressions[i] = "(" + rows[i][1] + ")*(" + rows[i][2] + ")*(T-T0)";
        }
        return expressions;
    }

    /** Read current density parameters and heat-capacity properties from the model.
     * The frozen baseline literals are checked here so a sensitivity copy cannot
     * silently integrate energy with stale Java constants after a material change.
     */
    private static String[][] nativeThermalCapacityReadback(Model model) {
        String[][] expected = {
            {"alumina", "rhoAl", "matAl", "3900", "880"},
            {"gold", "rhoAu", "matAu", "19300", "129"},
            {"adhesive", "rhoAdh", "matAdh", "1200", "1000"},
            {"fiber", "rhoFiber", "matFiber", "2200", "703"}
        };
        String[][] actual = new String[expected.length][4];
        for (int i = 0; i < expected.length; i++) {
            String[] row = expected[i];
            String densityParameter = model.param().get(row[1]);
            String densityBinding = model.material(row[2]).propertyGroup("def").getString("density");
            String heatCapacity = model.material(row[2]).propertyGroup("def").getString("heatcapacity");
            if (densityParameter == null || densityBinding == null || heatCapacity == null ||
                !densityBinding.replace(" ", "").equals(row[1])) {
                throw new IllegalStateException("native material density/Cp readback is missing or detached for " + row[0]);
            }
            String densityNormalized = densityParameter.replace(" ", "");
            String cpNormalized = heatCapacity.replace(" ", "");
            if (!(densityNormalized.equals(row[3]) || densityNormalized.startsWith(row[3] + "[")) ||
                !(cpNormalized.equals(row[4]) || cpNormalized.startsWith(row[4] + "["))) {
                throw new IllegalStateException("native density or heat capacity differs from frozen W24 material for " +
                    row[0] + ": density=" + densityParameter + ", Cp=" + heatCapacity);
            }
            actual[i] = new String[]{row[0], densityParameter, heatCapacity, densityBinding};
        }
        return actual;
    }

    private static void verifyMatrix(double[][] values, int rows, int columns, String label) {
        if (values == null || values.length != rows) {
            throw new IllegalStateException("native numerical expression count mismatch for " + label);
        }
        for (int i = 0; i < rows; i++) {
            if (values[i] == null || values[i].length != columns) {
                throw new IllegalStateException("native numerical accepted-time count mismatch for " + label);
            }
            for (double value : values[i]) {
                if (!Double.isFinite(value)) throw new IllegalStateException("native numerical result is nonfinite for " + label);
            }
        }
    }

    private static boolean sameEntitySet(int[] left, int[] right) {
        if (left == null || right == null || left.length != right.length) return false;
        Set<Integer> a = new HashSet<>();
        Set<Integer> b = new HashSet<>();
        for (int value : left) a.add(value);
        for (int value : right) b.add(value);
        return a.size() == left.length && b.size() == right.length && a.equals(b);
    }

    private static List<Double> boxed(double[] values) {
        List<Double> out = new ArrayList<>(values.length);
        for (double value : values) {
            if (!Double.isFinite(value)) throw new IllegalStateException("native result contains a nonfinite value");
            out.add(value);
        }
        return out;
    }

    private static List<Integer> boxed(int[] values) {
        List<Integer> out = new ArrayList<>(values.length);
        for (int value : values) out.add(value);
        return out;
    }

    private static void writeNewAndSync(Path output, byte[] bytes) {
        Path parent = output.getParent();
        if (parent == null || !Files.isDirectory(parent) || Files.exists(output)) {
            throw new IllegalArgumentException("native metric artifact must be new in an existing task-owned directory");
        }
        try (FileChannel channel = FileChannel.open(output, StandardOpenOption.CREATE_NEW,
                StandardOpenOption.WRITE)) {
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            while (buffer.hasRemaining()) channel.write(buffer);
            channel.force(true);
        } catch (IOException exception) {
            throw new IllegalStateException("failed to fsync native W24 metric artifact", exception);
        }
    }

    private static String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder text = new StringBuilder();
            for (byte value : digest) text.append(String.format("%02x", value & 0xff));
            return text.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }

    private static String sha256File(Path path) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
        try (BufferedInputStream input = new BufferedInputStream(Files.newInputStream(path))) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        StringBuilder text = new StringBuilder();
        for (byte value : digest.digest()) text.append(String.format("%02x", value & 0xff));
        return text.toString();
    }

    private static String toJson(Object value) {
        StringBuilder text = new StringBuilder();
        appendJson(text, value);
        return text.toString();
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
            double number = ((Number) value).doubleValue();
            if (!Double.isFinite(number)) throw new IllegalArgumentException("JSON artifact cannot contain nonfinite numbers");
            out.append(value.toString());
        } else if (value instanceof Boolean) {
            out.append(value.toString());
        } else if (value instanceof Map<?, ?>) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) out.append(',');
                first = false;
                appendJson(out, String.valueOf(entry.getKey())); out.append(':'); appendJson(out, entry.getValue());
            }
            out.append('}');
        } else if (value instanceof Iterable<?>) {
            out.append('['); boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) out.append(','); first = false; appendJson(out, item);
            }
            out.append(']');
        } else if (value.getClass().isArray()) {
            out.append('[');
            for (int i = 0; i < Array.getLength(value); i++) {
                if (i > 0) out.append(','); appendJson(out, Array.get(value, i));
            }
            out.append(']');
        } else {
            throw new IllegalArgumentException("unsupported W24 JSON artifact value: " + value.getClass().getName());
        }
    }

    private static Map<String, Object> configureScenario(Model model, Map<String, Object> args) {
        String scenario = safeToken(args.get("scenario"), "scenario");
        Map<String, Object> result = new LinkedHashMap<>();
        if ("baseline".equals(scenario) || "continuous_comparator".equals(scenario)) {
            result.put("scenario", scenario);
        } else if ("reset_negative_control".equals(scenario)) {
            StudyFeature time = model.study("stdBake").feature("time");
            time.set("useinitsol", "off");
            if (!"off".equals(time.getString("useinitsol"))) {
                throw new IllegalStateException("stage-2 reset control did not read back useinitsol=off");
            }
            result.put("scenario", scenario);
            result.put("stdBake_useinitsol", time.getString("useinitsol"));
            result.put("native_solver_submissions", 0);
        } else if ("coarse_mesh".equals(scenario)) {
            double hmax = ((Number) args.getOrDefault("adhesive_hmax_m", 20e-6)).doubleValue();
            if (Math.abs(hmax - 20e-6) > 1e-15) {
                throw new IllegalArgumentException("coarse-mesh adhesive hmax is frozen at 20 um");
            }
            model.component(COMPONENT).mesh("mesh1").feature("sizeAdh").set("hmax", hmax);
            model.component(COMPONENT).mesh("mesh1").run();
            double actual = model.component(COMPONENT).mesh("mesh1").feature("sizeAdh").getDouble("hmax");
            if (Math.abs(actual - hmax) > 1e-15) {
                throw new IllegalStateException("coarse-mesh hmax readback mismatch");
            }
            result.put("scenario", scenario);
            result.put("adhesive_hmax_m", actual);
            result.put("mesh_rebuilt", true);
            result.put("native_solver_submissions", 0);
        } else if ("dose_sensitivity".equals(scenario)) {
            model.param().set("kUV", "1.1e-2[1/s]");
            String actual = model.param().get("kUV");
            if (actual == null || !actual.replace(" ", "").contains("1.1e-2")) {
                throw new IllegalStateException("dose control parameter did not read back as 1.1e-2 1/s: " + actual);
            }
            result.put("scenario", scenario);
            result.put("kUV_readback", actual);
            result.put("native_solver_submissions", 0);
        } else if ("tight_time".equals(scenario)) {
            result.put("scenario", scenario);
            result.put("native_solver_submissions", 0);
        } else {
            throw new IllegalArgumentException("unrecognized frozen W24 scenario: " + scenario);
        }
        result.put("status", "SCENARIO_CONFIGURED_NOT_SOLVED");
        return result;
    }

    private static Map<String, Object> configureContinuous(Model model, Map<String, Object> args) {
        if (Arrays.asList(model.study().tags()).contains("stdCont")) {
            throw new IllegalStateException("stdCont already exists; refusing an ambiguous duplicate");
        }
        double maxStep = ((Number) args.getOrDefault("max_step_s", 1.0)).doubleValue();
        if (Math.abs(maxStep - 1.0) > 1e-12 && Math.abs(maxStep - 0.5) > 1e-12) {
            throw new IllegalArgumentException("continuous max step must be frozen at 1.0 or 0.5 seconds");
        }
        String tlist = maxStep == 0.5
            ? "range(0[s],0.5[s],120[s]) range(130[s],10[s],1500[s])"
            : "range(0[s],1[s],120[s]) range(130[s],10[s],1500[s])";
        Study study = model.study().create("stdCont");
        study.create("time", "Transient");
        StudyFeature time = study.feature("time");
        time.set("tlist", tlist);
        time.set("usertol", "on");
        time.set("rtol", "1e-5");
        time.set("useinitsol", "off");
        study.createAutoSequences("sol");
        SolverSequence sequence = requireUniqueAttachedSolverSequence(
            model, study, "stdCont", "time", "Time");
        SolverFeature solverTime = findUniqueTimeFeature(sequence);
        Map<String, Object> fieldReadbacks = configureTimeSolver(model, solverTime, maxStep);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "CONTINUOUS_STUDY_CONFIGURED_NOT_SOLVED");
        result.put("study_tag", "stdCont");
        result.put("sequence_tag", sequence.tag());
        result.put("tlist", time.getString("tlist"));
        result.put("useinitsol", time.getString("useinitsol"));
        result.put("max_step_s", solverTime.getDouble("maxstepbdf"));
        result.put("tstepsbdf", solverTime.getString("tstepsbdf"));
        result.put("tout", solverTime.getString("tout"));
        result.put("tstepsstore", solverTime.getInt("tstepsstore"));
        result.putAll(fieldReadbacks);
        result.put("native_solver_submissions", 0);
        return result;
    }

    private static Map<String, Object> configureTimeSolver(Model model, SolverFeature time, double maxStep) {
        Map<String, Object> fieldContract = readSolverFieldContract(model, time);
        Set<String> entryKeys = new HashSet<>((List<String>) fieldContract.get("solver_entry_keys"));
        time.set("timemethod", "bdf");
        time.set("tunit", "s");
        time.set("tstepsbdf", "strict");
        time.set("maxstepconstraintbdf", "const");
        time.set("maxstepbdf", maxStep);
        time.set("tout", "tsteps");
        time.set("tstepsstore", 1);
        time.set("rtol", 1e-5);
        time.set("atolglobalmethod", "unscaled");
        time.set("atolglobal", 1e-8);
        for (int i = 0; i < SOLVER_FIELDS.length; i++) {
            String field = SOLVER_FIELDS[i];
            if (!entryKeys.contains(field)) {
                throw new IllegalStateException("time solver lacks frozen dependent field " + field +
                    "; actual=" + entryKeys);
            }
            time.setEntry("atolmethod", field, "unscaled");
            time.setEntry("atolvaluemethod", field, "manual");
            time.setEntry("atol", field, ABSOLUTE_TOLERANCES[i]);
        }
        String doseField = "comp1_Duv_rel";
        if (entryKeys.contains(doseField)) {
            time.setEntry("atolmethod", doseField, "unscaled");
            time.setEntry("atolvaluemethod", doseField, "manual");
            time.setEntry("atol", doseField, "1e-8");
        }
        if (!"strict".equals(time.getString("tstepsbdf")) ||
            !"tsteps".equals(time.getString("tout")) || time.getInt("tstepsstore") != 1 ||
            Math.abs(time.getDouble("maxstepbdf") - maxStep) > 1e-12) {
            throw new IllegalStateException("continuous BDF output/time readback mismatch");
        }
        Map<String, Object> readbacks = solverToleranceReadbacks(model, time);
        assertLogicalToleranceRows(readbacks, "continuous solver");
        return readbacks;
    }

    /** Pure fail-closed field/entry mapping gate, also exercised by the offline proxy harness. */
    static Map<String, Object> validateSolverFieldContract(int geometryDimension, boolean axisymmetric,
            String[] fieldTags, String[] fieldNames, String[][] fieldComponents, String[] actualEntryKeys) {
        if (geometryDimension != 2 || !axisymmetric) {
            throw new IllegalStateException("W24 displacement tolerance mapping requires 2-D axisymmetric geometry");
        }
        if (fieldTags == null || fieldNames == null || fieldComponents == null || fieldTags.length == 0 ||
            fieldNames.length != fieldTags.length || fieldComponents.length != fieldTags.length) {
            throw physicsFieldContractFailure("validate:field=u descriptor arrays null/empty/misaligned", fieldTags, fieldNames, fieldComponents, null);
        }
        Set<String> tags = new LinkedHashSet<>();
        List<Map<String, Object>> descriptors = new ArrayList<>();
        int selected = -1;
        int displacementCount = 0;
        for (int i = 0; i < fieldTags.length; i++) {
            if (fieldTags[i] == null || fieldTags[i].isBlank() || !tags.add(fieldTags[i]) ||
                fieldNames[i] == null || fieldNames[i].isBlank() || fieldComponents[i] == null || fieldComponents[i].length == 0) {
                throw physicsFieldContractFailure("validate:field=u descriptor incomplete or ambiguous", fieldTags, fieldNames, fieldComponents, null);
            }
            Set<String> components = new LinkedHashSet<>();
            for (String component : fieldComponents[i]) {
                if (component == null || component.isBlank() || !components.add(component)) {
                    throw physicsFieldContractFailure("validate:component descriptor empty or ambiguous", fieldTags, fieldNames, fieldComponents, null);
                }
            }
            if ("u".equals(fieldNames[i])) {
                displacementCount++;
                selected = i;
                if (components.size() != 2 || !components.contains("u") || !components.contains("w")) {
                    throw physicsFieldContractFailure("validate:field=u components must be exactly {u,w}", fieldTags, fieldNames, fieldComponents, null);
                }
            }
            Map<String, Object> observed = new LinkedHashMap<>();
            observed.put("physics_tag", "solid");
            observed.put("physics_field_count", fieldTags.length);
            observed.put("field_tag", fieldTags[i]);
            observed.put("field", fieldNames[i]);
            observed.put("components", new ArrayList<>(Arrays.asList(fieldComponents[i].clone())));
            descriptors.add(observed);
        }
        if (displacementCount != 1) {
            throw physicsFieldContractFailure("validate:field=u selection missing or ambiguous; selected_count=" + displacementCount,
                fieldTags, fieldNames, fieldComponents, null);
        }
        for (int i = 0; i < fieldTags.length; i++) {
            if (i == selected) continue;
            for (String component : fieldComponents[i]) {
                if ("u".equals(component) || "w".equals(component)) {
                    throw physicsFieldContractFailure("validate:nonselected descriptor aliases selected u/w component ownership",
                        fieldTags, fieldNames, fieldComponents, null);
                }
            }
        }
        if (actualEntryKeys == null) {
            throw new IllegalStateException("native time solver returned a null atolmethod entry table");
        }
        Set<String> actual = new LinkedHashSet<>();
        for (String key : actualEntryKeys) {
            if (key == null || key.isBlank() || !actual.add(key)) {
                throw new IllegalStateException("native time solver entry table is empty or ambiguous");
            }
        }
        Set<String> expected = new LinkedHashSet<>(Arrays.asList(SOLVER_FIELDS));
        Set<String> withDose = new LinkedHashSet<>(expected);
        withDose.add("comp1_Duv_rel");
        if (!actual.equals(expected) && !actual.equals(withDose)) {
            throw new IllegalStateException("native time solver entry table does not match observed fields; actual=" + actual);
        }
        List<String> sortedKeys = new ArrayList<>(actual);
        Collections.sort(sortedKeys);
        Map<String, Object> descriptor = descriptors.get(selected);
        Map<String, Object> bindings = new LinkedHashMap<>();
        bindings.put("u", "comp1_u");
        bindings.put("w", "comp1_u");
        Map<String, Object> contract = new LinkedHashMap<>();
        contract.put("geometry_dimension", geometryDimension);
        contract.put("geometry_axisymmetric", axisymmetric);
        contract.put("physics_field_count", fieldTags.length);
        contract.put("physics_field_descriptors", descriptors);
        contract.put("selected_displacement_field_count", displacementCount);
        contract.put("physics_field_descriptor", descriptor);
        contract.put("component_solver_entry_bindings", bindings);
        contract.put("solver_entry_keys", sortedKeys);
        return contract;
    }

    private static IllegalStateException physicsFieldContractFailure(String getterStage, String[] tags,
            String[] names, String[][] components, RuntimeException cause) {
        String message = "solid PhysicsField field=u contract failure; getter_stage=" + getterStage +
            "; actual_tags=" + Arrays.toString(tags) + "; actual_total_count=" + (tags == null ? "UNOBSERVED" : tags.length) +
            "; actual_field_names=" + Arrays.toString(names) + "; actual_components=" + Arrays.deepToString(components) +
            (cause == null ? "" : "; cause=" + cause.getClass().getSimpleName() + ": " + cause.getMessage());
        return cause == null ? new IllegalStateException(message) : new IllegalStateException(message, cause);
    }

    private static Map<String, Object> readSolverFieldContract(Model model, SolverFeature time) {
        String[] fieldTags = null;
        String[] fieldNames = null;
        String[][] components = null;
        String getterStage = "geometry";
        try {
            GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
            getterStage = "geometry.getSDim";
            int dimension = geom.getSDim();
            getterStage = "geometry.isAxisymmetric";
            boolean axisymmetric = geom.isAxisymmetric();
            if (dimension != 2 || !axisymmetric) {
                throw new IllegalStateException("W24 displacement tolerance mapping requires 2-D axisymmetric geometry");
            }
            getterStage = "model.physics(solid)";
            Physics solid = model.physics("solid");
            getterStage = "solid.field().tags()";
            fieldTags = solid.field().tags();
            if (fieldTags == null || fieldTags.length == 0) {
                throw new IllegalStateException("field=u descriptor list is null or empty");
            }
            fieldNames = new String[fieldTags.length];
            components = new String[fieldTags.length][];
            Set<String> uniqueTags = new LinkedHashSet<>();
            for (int i = 0; i < fieldTags.length; i++) {
                getterStage = "field_tag[" + i + "]";
                if (fieldTags[i] == null || fieldTags[i].isBlank() || !uniqueTags.add(fieldTags[i])) {
                    throw new IllegalStateException("field=u descriptor tags incomplete or ambiguous");
                }
                getterStage = "solid.field(" + fieldTags[i] + ")";
                PhysicsField field = solid.field(fieldTags[i]);
                if (field == null) throw new IllegalStateException("PhysicsField getter returned null");
                getterStage = "solid.field(" + fieldTags[i] + ").field()";
                fieldNames[i] = field.field();
                getterStage = "solid.field(" + fieldTags[i] + ").component()";
                components[i] = field.component();
            }
            getterStage = "time.getEntryKeys(atolmethod)";
            String[] entryKeys = time.getEntryKeys("atolmethod");
            getterStage = "validateSolverFieldContract";
            return validateSolverFieldContract(dimension, axisymmetric, fieldTags, fieldNames, components, entryKeys);
        } catch (RuntimeException cause) {
            throw physicsFieldContractFailure(getterStage, fieldTags, fieldNames, components, cause);
        }
    }

    private static Map<String, Object> solverToleranceReadbacks(Model model, SolverFeature time) {
        Map<String, Object> contract = readSolverFieldContract(model, time);
        Map<String, Object> actualRows = new LinkedHashMap<>();
        for (String field : (List<String>) contract.get("solver_entry_keys")) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("atolmethod", time.getString("atolmethod", field));
            row.put("atolvaluemethod", time.getString("atolvaluemethod", field));
            row.put("atol", time.getString("atol", field));
            actualRows.put(field, row);
        }
        Map<String, Object> logicalRows = validateSolverToleranceRows(contract, actualRows);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("solver_field_contract", contract);
        result.put("field_tolerances", actualRows);
        result.put("logical_component_tolerances", logicalRows);
        return result;
    }

    /** Validate actual solver-entry readbacks before deriving the six logical inputs. */
    static Map<String, Object> validateSolverToleranceRows(Map<String, Object> contract,
            Map<String, Object> actualRows) {
        if (contract == null || actualRows == null ||
            !actualRows.keySet().equals(new LinkedHashSet<>((List<String>) contract.get("solver_entry_keys")))) {
            throw new IllegalStateException("actual solver tolerance rows differ from their observed entry-key table");
        }
        for (int i = 0; i < SOLVER_FIELDS.length; i++) {
            requireToleranceRow(actualRows, SOLVER_FIELDS[i], ABSOLUTE_TOLERANCES[i], "native");
        }
        if (actualRows.containsKey("comp1_Duv_rel")) {
            requireToleranceRow(actualRows, "comp1_Duv_rel", "1e-8", "native dose");
        }
        Map<String, Object> logicalRows = new LinkedHashMap<>();
        for (int i = 0; i < LOGICAL_SOLVER_FIELDS.length; i++) {
            String logicalField = LOGICAL_SOLVER_FIELDS[i];
            String entry = LOGICAL_ENTRY_KEYS[i];
            Map<String, Object> observed = (Map<String, Object>) actualRows.get(entry);
            Map<String, Object> logical = new LinkedHashMap<>(observed);
            logical.put("component", LOGICAL_COMPONENTS[i]);
            logical.put("solver_entry_key", entry);
            logical.put("source", "DERIVED_FROM_OBSERVED_PHYSICS_FIELD_BINDING");
            logicalRows.put(logicalField, logical);
        }
        return logicalRows;
    }

    private static void requireToleranceRow(Map<String, Object> rows, String field,
            String expectedAtol, String label) {
        Map<String, Object> row = (Map<String, Object>) rows.get(field);
        double atol;
        try {
            atol = Double.parseDouble(String.valueOf(row == null ? null : row.get("atol")));
        } catch (NumberFormatException exception) {
            throw new IllegalStateException(label + " solver atol readback is not numeric for " + field, exception);
        }
        if (row == null || !"unscaled".equals(row.get("atolmethod")) ||
            !"manual".equals(row.get("atolvaluemethod")) || !Double.isFinite(atol) ||
            Math.abs(atol - Double.parseDouble(expectedAtol)) > 1e-20) {
            throw new IllegalStateException(label + " solver tolerance readback mismatch for " + field);
        }
    }

    private static void assertLogicalToleranceRows(Map<String, Object> readbacks, String label) {
        Map<String, Object> logicalRows = (Map<String, Object>) readbacks.get("logical_component_tolerances");
        if (logicalRows == null || !logicalRows.keySet().equals(new LinkedHashSet<>(Arrays.asList(LOGICAL_SOLVER_FIELDS)))) {
            throw new IllegalStateException(label + " omitted one or more derived logical field rows");
        }
        for (int i = 0; i < LOGICAL_SOLVER_FIELDS.length; i++) {
            Map<String, Object> row = (Map<String, Object>) logicalRows.get(LOGICAL_SOLVER_FIELDS[i]);
            double atol = Double.parseDouble(String.valueOf(row.get("atol")));
            if (!"unscaled".equals(row.get("atolmethod")) ||
                !"manual".equals(row.get("atolvaluemethod")) || !Double.isFinite(atol) ||
                Math.abs(atol - Double.parseDouble(LOGICAL_ABSOLUTE_TOLERANCES[i])) > 1e-20 ||
                !LOGICAL_ENTRY_KEYS[i].equals(row.get("solver_entry_key")) ||
                !LOGICAL_COMPONENTS[i].equals(row.get("component")) ||
                !"DERIVED_FROM_OBSERVED_PHYSICS_FIELD_BINDING".equals(row.get("source"))) {
                throw new IllegalStateException(label + " logical field tolerance readback mismatch for " +
                    LOGICAL_SOLVER_FIELDS[i]);
            }
        }
    }

    private static Map<String, Object> studyRun(Model model, Map<String, Object> args) {
        String caseId = safeToken(args.get("case_id"), "case_id");
        String studyTag = safeToken(args.get("study_tag"), "study_tag");
        String ledgerText = String.valueOf(args.getOrDefault("ledger_path", ""));
        if (!STUDY_TAGS.contains(studyTag)) {
            throw new IllegalArgumentException("study tag is outside the frozen campaign set: " + studyTag);
        }
        if (ledgerText.isBlank()) throw new IllegalArgumentException("study_run requires an fsynced ledger_path");
        Study study = model.study(studyTag);
        SolverSequence sequence = requireUniqueAttachedSolverSequence(
            model, study, studyTag, "time", "Time");
        SolverFeature time = findUniqueSolverFeature(sequence, "Time", studyTag);
        if (!"bdf".equals(time.getString("timemethod")) ||
            !"strict".equals(time.getString("tstepsbdf")) ||
            !"tsteps".equals(time.getString("tout")) || time.getInt("tstepsstore") != 1) {
            throw new IllegalStateException("native BDF/output settings fail the pre-run readback gate");
        }

        int ordinal = appendInvocationBeforeStudyRun(Path.of(ledgerText), caseId, studyTag);
        long started = System.nanoTime();
        study.run();
        long elapsed = System.nanoTime() - started;
        String savePath = String.valueOf(args.getOrDefault("save_after_success_path", ""));
        Map<String, Object> immediateSaveReceipt = null;
        if (!savePath.isBlank()) {
            try {
                model.save(savePath);
            } catch (IOException exception) {
                throw new IllegalStateException("study returned but immediate baseline MPH save failed", exception);
            }
            Path savedPath = Path.of(savePath);
            if (Files.isSymbolicLink(savedPath) ||
                !Files.isRegularFile(savedPath, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalStateException("Study.run save did not produce a regular nonsymlink MPH file");
            }
            try {
                long sizeBeforeHash = Files.size(savedPath);
                String digest = sha256File(savedPath);
                long sizeAfterHash = Files.size(savedPath);
                if (sizeBeforeHash <= 0 || sizeBeforeHash != sizeAfterHash) {
                    throw new IllegalStateException("immediate MPH save changed size while its receipt was created");
                }
                immediateSaveReceipt = new LinkedHashMap<>();
                immediateSaveReceipt.put("status", "STUDY_RUN_MPH_SAVED_AND_HASHED");
                immediateSaveReceipt.put("path", savePath);
                immediateSaveReceipt.put("size_bytes", sizeAfterHash);
                immediateSaveReceipt.put("sha256", digest);
            } catch (IOException exception) {
                throw new IllegalStateException("study returned but immediate baseline MPH receipt failed", exception);
            }
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "NATIVE_STUDY_RUN_RETURNED");
        result.put("submission_index", ordinal);
        result.put("case_id", caseId);
        result.put("study_tag", studyTag);
        result.put("solver_sequence", sequence.tag());
        result.put("time_feature", time.tag());
        result.put("elapsed_s", elapsed / 1.0e9);
        result.put("immediate_save_path", savePath.isBlank() ? null : savePath);
        result.put("immediate_save_receipt", immediateSaveReceipt);
        result.put("study_run_calls_from_this_action", 1);
        return result;
    }

    private static int appendInvocationBeforeStudyRun(Path path, String caseId, String studyTag) {
        if (!caseId.matches("[A-Za-z0-9_-]{1,64}") || !studyTag.matches("[A-Za-z0-9_]{1,64}")) {
            throw new IllegalArgumentException("ledger case/study tokens contain unsupported characters");
        }
        try {
            Path parent = path.getParent();
            if (parent == null || !Files.isDirectory(parent)) {
                throw new IllegalArgumentException("study ledger parent directory must already exist");
            }
            int prior = 0;
            if (Files.exists(path)) {
                for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
                    if (line.isEmpty()) continue;
                    String prefix = "{\"event\":\"study_run_submitted\",\"submission_index\":";
                    if (!line.startsWith(prefix)) throw new IllegalStateException("unexpected line in native study ledger");
                    int end = line.indexOf(',', prefix.length());
                    if (end < 0) throw new IllegalStateException("malformed native study ledger ordinal");
                    int ordinal = Integer.parseInt(line.substring(prefix.length(), end));
                    if (ordinal != prior + 1) throw new IllegalStateException("nonconsecutive native study ledger");
                    prior = ordinal;
                }
            }
            if (prior >= 10) throw new IllegalStateException("ten-submission W24 study.run cap exhausted");
            int ordinal = prior + 1;
            String line = "{\"event\":\"study_run_submitted\",\"submission_index\":" + ordinal +
                ",\"case_id\":\"" + caseId + "\",\"study_tag\":\"" + studyTag +
                "\",\"at_utc\":\"" + Instant.now().toString() + "\"}\n";
            byte[] bytes = line.getBytes(StandardCharsets.UTF_8);
            try (FileChannel channel = FileChannel.open(path, StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE, StandardOpenOption.APPEND)) {
                ByteBuffer buffer = ByteBuffer.wrap(bytes);
                while (buffer.hasRemaining()) channel.write(buffer);
                channel.force(true);
            }
            return ordinal;
        } catch (IOException exception) {
            throw new IllegalStateException("could not fsync W24 study.run submission ledger", exception);
        }
    }

    private static Map<String, Object> solutionSnapshot(Model model, Map<String, Object> args) {
        String solverTag = safeToken(args.get("solver_tag"), "solver_tag");
        String pathText = String.valueOf(args.getOrDefault("path", ""));
        if (pathText.isBlank()) throw new IllegalArgumentException("solution_snapshot requires output path");
        SolverSequence solution = model.sol(solverTag);
        double[] times = solution.getPVals();
        if (times == null || times.length == 0) {
            throw new IllegalStateException("native solution sequence has no stored parameter/time values");
        }
        if (!solution.isRealU(1, "Sol")) {
            throw new IllegalStateException("W24 cure snapshot expected a real solution vector");
        }
        com.comsol.model.XmeshInfoDofs dofs = solution.xmeshInfo().dofs();
        int[] geometryNumbers = dofs.geomNums();
        int[] nodes = dofs.nodes();
        int[] nameIndices = dofs.nameInds();
        int[] vectorIndices = dofs.solVectorInds();
        double[][] coordinates = dofs.coords();
        String[] names = dofs.dofNames();
        int count = geometryNumbers.length;
        if (count == 0 || nodes.length != count || nameIndices.length != count || vectorIndices.length != count ||
            coordinates.length != 2 || coordinates[0].length != count || coordinates[1].length != count) {
            throw new IllegalStateException("XmeshInfoDofs metadata shapes are inconsistent with the 2-D coupon");
        }

        Path output = Path.of(pathText);
        Path parent = output.getParent();
        if (parent == null || !Files.isDirectory(parent) || Files.exists(output)) {
            throw new IllegalArgumentException("snapshot output must be a new file in an existing task directory");
        }
        try (DataOutputStream out = new DataOutputStream(new BufferedOutputStream(
                new GZIPOutputStream(Files.newOutputStream(output, StandardOpenOption.CREATE_NEW))))) {
            out.writeUTF("W24-DOF-SNAPSHOT-1");
            out.writeInt(2);
            out.writeInt(names.length);
            for (String name : names) out.writeUTF(name);
            out.writeInt(count);
            for (int i = 0; i < count; i++) {
                out.writeInt(geometryNumbers[i]);
                out.writeInt(nodes[i]);
                out.writeInt(nameIndices[i]);
                out.writeInt(vectorIndices[i]);
                out.writeDouble(coordinates[0][i]);
                out.writeDouble(coordinates[1][i]);
            }
            out.writeInt(times.length);
            int expectedVectorLength = -1;
            for (int solnum = 1; solnum <= times.length; solnum++) {
                if (!solution.isRealU(solnum, "Sol")) {
                    throw new IllegalStateException("W24 snapshot supports only real native solution vectors; solnum=" + solnum);
                }
                double[] values = solution.getU(solnum, "Sol");
                if (expectedVectorLength < 0) expectedVectorLength = values.length;
                if (values.length != expectedVectorLength) {
                    throw new IllegalStateException("solution-vector length changed across accepted times");
                }
                for (int dof = 0; dof < count; dof++) {
                    if (vectorIndices[dof] < 0 || vectorIndices[dof] >= values.length ||
                        nameIndices[dof] < 0 || nameIndices[dof] >= names.length) {
                        throw new IllegalStateException("native DOF mapping points outside the solution vector or names");
                    }
                }
                out.writeDouble(times[solnum - 1]);
                out.writeInt(values.length);
                for (double value : values) out.writeDouble(value);
            }
            out.flush();
        } catch (IOException exception) {
            throw new IllegalStateException("failed to write compressed W24 solution snapshot", exception);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "SOLUTION_SNAPSHOT_WRITTEN");
        result.put("solver_tag", solverTag);
        result.put("path", output.toString());
        result.put("stored_time_count", times.length);
        result.put("dof_count", count);
        result.put("dof_names", names);
        result.put("coordinate_axes", 2);
        result.put("real_solution", true);
        return result;
    }

    private static Map<String, Object> readback(Model model) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "SCIENCE_ACTIONS_READY_NOT_SOLVED");
        result.put("studies", model.study().tags());
        result.put("mesh_tags", model.component(COMPONENT).mesh().tags());
        result.put("quasistatic_readback", model.physics("solid").prop("StructuralTransientBehavior")
            .getString("StructuralTransientBehavior"));
        Map<String, Object> solvers = new LinkedHashMap<>();
        Map<String, Object> studyTimes = new LinkedHashMap<>();
        for (String tag : model.study().tags()) {
            if (Arrays.asList(model.study(tag).feature().tags()).contains("time")) {
                StudyFeature studyTime = model.study(tag).feature("time");
                Map<String, Object> studyReadback = new LinkedHashMap<>();
                studyReadback.put("tlist", studyTime.getString("tlist"));
                studyReadback.put("usertol", studyTime.getString("usertol"));
                studyReadback.put("rtol", studyTime.getString("rtol"));
                studyReadback.put("useinitsol", studyTime.getString("useinitsol"));
                studyReadback.put("initmethod", studyTime.getString("initmethod"));
                studyReadback.put("initstudy", studyTime.getString("initstudy"));
                studyReadback.put("solnum", studyTime.getString("solnum"));
                studyTimes.put(tag, studyReadback);
            }
            if (Arrays.asList("stdUV", "stdBake", "stdCool", "stdCont").contains(tag)) {
                Study study = model.study(tag);
                SolverSequence sequence = requireUniqueAttachedSolverSequence(
                    model, study, tag, "time", "Time");
                SolverFeature time = findUniqueTimeFeature(sequence);
                Map<String, Object> solverReadback = new LinkedHashMap<>();
                solverReadback.put("sequence_tag", sequence.tag());
                solverReadback.put("sequence_attached", sequence.isAttached());
                solverReadback.put("sequence_study", sequence.study());
                solverReadback.put("time_feature_tag", time.tag());
                solverReadback.put("timemethod", time.getString("timemethod"));
                solverReadback.put("tstepsbdf", time.getString("tstepsbdf"));
                solverReadback.put("maxstepconstraintbdf", time.getString("maxstepconstraintbdf"));
                solverReadback.put("maxstepbdf", time.getDouble("maxstepbdf"));
                solverReadback.put("tout", time.getString("tout"));
                solverReadback.put("tstepsstore", time.getInt("tstepsstore"));
                solverReadback.putAll(solverToleranceReadbacks(model, time));
                solvers.put(tag, solverReadback);
            } else {
                String[] attached = model.study(tag).getSolverSequences("SolverSequence");
                if (attached.length == 1) {
                    SolverSequence sequence = model.sol(attached[0]);
                    SolverFeature time = findUniqueTimeFeature(sequence);
                    solvers.put(tag, Map.of("sequence_tag", attached[0], "sequence_attached", sequence.isAttached(),
                        "sequence_study", sequence.study(), "time_feature_tag", time.tag(),
                        "timemethod", time.getString("timemethod"), "tstepsbdf", time.getString("tstepsbdf"),
                        "maxstepconstraintbdf", time.getString("maxstepconstraintbdf"),
                        "maxstepbdf", time.getDouble("maxstepbdf"), "tout", time.getString("tout"),
                        "tstepsstore", time.getInt("tstepsstore")));
                }
            }
        }
        result.put("solver_readbacks", solvers);
        result.put("study_time_readbacks", studyTimes);
        result.put("study_run_calls", 0);
        return result;
    }

    private static SolverSequence requireUniqueAttachedSolverSequence(
            Model model, Study study, String studyTag, String studyStepTag, String expectedSolverType) {
        Map<String, String> categories = new LinkedHashMap<>();
        for (String category : new String[]{"SolverSequence", "None"}) {
            String[] tags;
            try {
                tags = study.getSolverSequences(category);
            } catch (RuntimeException failure) {
                throw new IllegalStateException("failed to read required " + category +
                    " filter for " + studyTag, failure);
            }
            if (tags == null) {
                throw new IllegalStateException(category + " filter returned null for " + studyTag);
            }
            Set<String> seen = new HashSet<>();
            for (String tag : tags) {
                if (tag == null || tag.isBlank()) {
                    throw new IllegalStateException(category + " filter returned a blank/null tag for " +
                        studyTag + ": " + sequenceTagsPreview(tags));
                }
                if (!seen.add(tag)) {
                    throw new IllegalStateException(category + " filter returned duplicate tag " + tag +
                        " for " + studyTag);
                }
                String priorCategory = categories.putIfAbsent(tag, category);
                if (priorCategory != null) {
                    throw new IllegalStateException("solver tag overlaps required filters for " + studyTag +
                        ": " + tag + " in " + priorCategory + " and " + category);
                }
            }
        }
        if (categories.size() != 1) {
            throw new IllegalStateException("expected one attached solver sequence for " + studyTag +
                ", got " + sequenceCategoryPreview(categories));
        }
        Map.Entry<String, String> candidate = categories.entrySet().iterator().next();
        String sequenceTag = candidate.getKey();
        SolverSequence sequence;
        try {
            sequence = model.sol(sequenceTag);
        } catch (RuntimeException failure) {
            throw new IllegalStateException("failed to resolve solver sequence " + sequenceTag +
                " for " + studyTag, failure);
        }
        if (sequence == null) {
            throw new IllegalStateException("required solver tag is absent from model.sol(): " + sequenceTag);
        }
        try {
            String actualCategory = sequence.getSequenceType();
            if (!candidate.getValue().equals(actualCategory)) {
                throw new IllegalStateException("solver category mismatch for " + sequenceTag +
                    ": filter=" + candidate.getValue() + ", getSequenceType()=" + actualCategory);
            }
            boolean attached = sequence.isAttached();
            String actualStudy = sequence.study();
            if (!attached || !studyTag.equals(actualStudy)) {
                throw new IllegalStateException("generated solver sequence is not attached to " + studyTag +
                    ": " + sequenceTag + " -> " + actualStudy + ", isAttached=" + attached);
            }
            verifySolverRootFeatures(sequence, sequenceTag, studyTag, studyStepTag);
            findUniqueSolverFeature(sequence, expectedSolverType, studyTag);
        } catch (IllegalStateException failure) {
            throw failure;
        } catch (RuntimeException failure) {
            throw new IllegalStateException("failed to verify generated solver sequence " + sequenceTag +
                " for " + studyTag, failure);
        }
        return sequence;
    }

    private static void verifySolverRootFeatures(SolverSequence sequence, String sequenceTag,
            String studyTag, String studyStepTag) {
        com.comsol.model.SolverFeatureList root = sequence.feature();
        String[] tags = root.tags();
        if (tags == null) throw new IllegalStateException("solver root feature tags are null for " + sequenceTag);
        Set<String> seenTags = new HashSet<>();
        Map<String, SolverFeature> required = new LinkedHashMap<>();
        for (String tag : tags) {
            if (tag == null || tag.isBlank() || !seenTags.add(tag)) {
                throw new IllegalStateException("solver root has blank/null/duplicate feature tags for " +
                    sequenceTag + ": " + sequenceTagsPreview(tags));
            }
            SolverFeature feature = root.get(tag);
            if (feature == null) throw new IllegalStateException("solver root feature lookup returned null: " + tag);
            String type = feature.getType();
            if ("StudyStep".equals(type) || "Variables".equals(type)) {
                if (required.putIfAbsent(type, feature) != null) {
                    throw new IllegalStateException("solver root has duplicate " + type +
                        " features for " + sequenceTag);
                }
            }
        }
        SolverFeature studyStep = required.get("StudyStep");
        if (studyStep == null || !required.containsKey("Variables")) {
            throw new IllegalStateException("solver root requires unique StudyStep and Variables features for " +
                sequenceTag + "; found=" + required.keySet());
        }
        String linkedStudy = studyStep.getString("study");
        String linkedStep = studyStep.getString("studystep");
        if (!studyTag.equals(linkedStudy) || !studyStepTag.equals(linkedStep)) {
            throw new IllegalStateException("solver StudyStep relation mismatch for " + sequenceTag +
                ": study=" + linkedStudy + ", studystep=" + linkedStep +
                ", expected=" + studyTag + "/" + studyStepTag);
        }
    }

    private static SolverFeature findUniqueSolverFeature(
            SolverSequence sequence, String expectedType, String studyTag) {
        Map<String, SolverFeature> matches = new LinkedHashMap<>();
        collectSolverFeatures(sequence.feature().tags(), sequence.feature(), "", expectedType, matches);
        if (matches.size() != 1) {
            throw new IllegalStateException("expected exactly one native " + expectedType +
                " solver feature for " + studyTag + ", found=" + matches.keySet());
        }
        return matches.values().iterator().next();
    }

    private static void collectSolverFeatures(String[] tags, com.comsol.model.SolverFeatureList list,
            String parent, String expectedType, Map<String, SolverFeature> matches) {
        for (String tag : tags) {
            SolverFeature feature = list.get(tag);
            String path = parent.isEmpty() ? tag : parent + "/" + tag;
            if (expectedType.equals(feature.getType())) matches.put(path, feature);
            String[] childTags = feature.feature().tags();
            if (childTags.length > 0) {
                collectSolverFeatures(childTags, feature.feature(), path, expectedType, matches);
            }
        }
    }

    private static String sequenceTagsPreview(String[] tags) {
        int count = Math.min(tags.length, 16);
        String[] preview = new String[count];
        for (int i = 0; i < count; i++) preview[i] = boundedSequenceToken(tags[i]);
        String text = Arrays.toString(preview);
        return tags.length > count ? text + "...[TRUNCATED " + count + "/" + tags.length + "]" : text;
    }

    private static String sequenceCategoryPreview(Map<String, String> categories) {
        List<String> preview = new ArrayList<>();
        for (Map.Entry<String, String> entry : categories.entrySet()) {
            if (preview.size() >= 16) break;
            preview.add(boundedSequenceToken(entry.getKey()) + "(" + entry.getValue() + ")");
        }
        String text = preview.toString();
        return categories.size() > preview.size()
            ? text + "...[TRUNCATED " + preview.size() + "/" + categories.size() + "]" : text;
    }

    private static String boundedSequenceToken(String value) {
        if (value == null || value.length() <= 64) return value;
        return value.substring(0, 64) + "...[TRUNCATED]";
    }

    private static SolverFeature findUniqueTimeFeature(SolverSequence sequence) {
        return findUniqueSolverFeature(sequence, "Time", "sequence");
    }

    private static String safeToken(Object value, String label) {
        if (!(value instanceof String)) throw new IllegalArgumentException(label + " must be a string token");
        String result = (String) value;
        if (!result.matches("[A-Za-z0-9_-]{1,64}")) {
            throw new IllegalArgumentException(label + " contains unsupported characters");
        }
        return result;
    }
}
