import com.comsol.model.GeomSequence;
import com.comsol.model.MeshSequence;
import com.comsol.model.Model;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.SolverSequence;
import com.comsol.model.SolverFeature;
import com.comsol.model.Expr;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.FeatureInfo;
import com.comsol.model.physics.FeatureInfoList;
import com.comsol.model.physics.EquationViewParent;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * First executable COMSOL 6.4 W24 coupon fixture.
 *
 * This class only builds and reads back a model. It deliberately contains no
 * study.run/compute call. The native runner owns and accounts for every solve.
 */
public final class W24CureCouponFixture {
    private static final String COMPONENT = "comp1";
    private static final String GEOMETRY = "geom1";
    private static final double TOL = 1.0e-9;
    private static final double ADHESIVE_Z_MIN_M = 510.0e-6;
    private static final double ADHESIVE_Z_SURFACE_M = 550.0e-6;
    private static final double[] EXPECTED_VOLUMES = {
        9.81747704247e-11, 3.14159265359e-13,
        1.25663706144e-12, 6.13592315154e-12
    };
    private static final String[] DOMAIN_TAGS = {
        "sel_alumina", "sel_gold", "sel_adhesive", "sel_fiber"
    };
    private static final String[] DOMAIN_LABELS = {
        "alumina", "gold", "adhesive", "fiber"
    };
    private static final double[][] DOMAIN_BOXES = {
        {0.0, 250e-6, 0.0, 500e-6},
        {0.0, 100e-6, 500e-6, 510e-6},
        {0.0, 100e-6, 510e-6, 550e-6},
        {0.0, 62.5e-6, 550e-6, 1050e-6}
    };
    // Physical external boundaries; the r=0 symmetry axis is intentionally absent.
    private static final double[][] EXTERNAL_EDGE_BOXES = {
        {0, 250e-6, 0, 0}, {250e-6, 250e-6, 0, 500e-6},
        {100e-6, 250e-6, 500e-6, 500e-6}, {100e-6, 100e-6, 500e-6, 510e-6},
        {100e-6, 100e-6, 510e-6, 550e-6}, {62.5e-6, 100e-6, 550e-6, 550e-6},
        {62.5e-6, 62.5e-6, 550e-6, 1050e-6}, {0, 62.5e-6, 1050e-6, 1050e-6}
    };
    private static final String[] EDGE_TAGS = {
        "sel_ext_bottom", "sel_ext_alumina_outer", "sel_ext_alumina_shoulder",
        "sel_ext_gold_outer", "sel_ext_adhesive_outer", "sel_ext_adhesive_shoulder",
        "sel_ext_fiber_outer", "sel_ext_top"
    };
    private static final String[] SOLVER_FIELDS = {
        "comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u", "comp1_w"
    };
    private static final String[] ABSOLUTE_TOLERANCES = {
        "1e-4", "1e-8", "1e-8", "1e-8", "1e-12", "1e-12"
    };

    private W24CureCouponFixture() { }

    public static Object run(Model model, Map<String, Object> args) {
        String phase = String.valueOf(args.getOrDefault("phase", "build"));
        if ("build_v2".equals(phase)) return buildV2(model, args);
        if ("readback_v2".equals(phase)) return readbackV2(model);
        if ("readback".equals(phase)) return readback(model);
        if ("expression_inventory".equals(phase)) return expressionInventory(model, args);
        if ("equation_view_readback_v1".equals(phase)) return equationViewReadbackV1(model, args);
        if ("save".equals(phase)) {
            String path = String.valueOf(args.getOrDefault("path", ""));
            if (path.isBlank()) throw new IllegalArgumentException("save phase requires path");
            try {
                model.save(path);
            } catch (java.io.IOException exception) {
                throw new IllegalStateException("failed to save W24 template: " + path, exception);
            }
            return Map.of("status", "SAVED", "path", path, "solver_submissions", 0);
        }
        if (!"build".equals(phase)) {
            throw new IllegalArgumentException(
                "phase must be build, build_v2, readback, readback_v2, expression_inventory, or save");
        }
        if (model.component().tags().length != 0 || model.geom().tags().length != 0) {
            throw new IllegalStateException("W24 build requires a fresh empty model");
        }

        addParameters(model);
        buildGeometry(model);
        createSelections(model);
        addVariables(model);
        addMaterials(model);
        addPhysics(model);
        addMesh(model);
        addStudies(model);

        Map<String, Object> result = readback(model);
        result.put("status", "BUILT_NOT_SOLVED");
        result.put("native_study_run_calls", 0);
        return result;
    }

    /**
     * Additive cure-law v2 build path. The original "build" path above remains
     * behavior-compatible as the historical v1 fixture. This
     * path reuses only its pre-solve geometry, material and thermal/mesh setup,
     * then replaces the spatial cure source and adds dose/gel/Maxwell features
     * before any study sequence is created. It never submits a solve.
     */
    private static Map<String, Object> buildV2(Model model, Map<String, Object> args) {
        if (model.component().tags().length != 0 || model.geom().tags().length != 0) {
            throw new IllegalStateException("W24 cure-law v2 build requires a fresh empty model");
        }
        double muScale = requestedMuScale(args);
        addParameters(model);
        addV2Parameters(model, muScale);
        buildGeometry(model);
        createSelections(model);
        addVariables(model);
        configureV2ExposureVariables(model);
        addMaterials(model);
        configureV2LongTermAdhesiveMaterial(model);
        addPhysics(model);
        addDoseOde(model);
        addV2ActivationAndMaxwell(model);
        addMesh(model);
        addStudies(model);
        configureV2DoseSolverTolerance(model);
        Map<String, Object> result = readbackV2(model);
        result.put("status", "BUILT_NOT_SOLVED");
        result.put("native_study_run_calls", 0);
        result.put("native_semantics", "UNVERIFIED_FAIL_CLOSED");
        result.put("module_license", "UNVERIFIED_FAIL_CLOSED");
        return result;
    }

    private static double requestedMuScale(Map<String, Object> args) {
        Object raw = args.getOrDefault("mu_scale", 1.0);
        double scale;
        try {
            scale = Double.parseDouble(String.valueOf(raw));
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException("mu_scale must be a frozen numeric variant", exception);
        }
        if (!Double.isFinite(scale) || (scale != 0.5 && scale != 1.0 && scale != 2.0)) {
            throw new IllegalArgumentException("mu_scale must be exactly one of 0.5, 1.0, or 2.0");
        }
        return scale;
    }

    private static void addV2Parameters(Model model, double muScale) {
        model.param().set("zUVSurface", "550[um]");
        model.param().set("muScale", Double.toString(muScale));
        model.param().set("muUV", "muScale/(40[um])");
        model.param().set("Einf", "0.5[GPa]");
        model.param().set("Ebranch", "1.5[GPa]");
        model.param().set("nuVisco", "0.35");
        model.param().set("tauMaxwell", "300[s]");
        model.param().set("Kinf", "Einf/(3*(1-2*nuVisco))");
        model.param().set("Ginf", "Einf/(2*(1+nuVisco))");
        model.param().set("Kbranch", "Ebranch/(3*(1-2*nuVisco))");
        model.param().set("Gbranch", "Ebranch/(2*(1+nuVisco))");
    }

    private static void configureV2ExposureVariables(Model model) {
        model.component(COMPONENT).variable("v1").set("rate", "(kUV*Irel+kT)*(1-alpha)");
        model.component(COMPONENT).variable().create("vUVAdh");
        model.component(COMPONENT).variable("vUVAdh").selection().named("sel_adhesive");
        model.component(COMPONENT).variable("vUVAdh").set("S_uv", "if(t<120[s],1,0)");
        model.component(COMPONENT).variable("vUVAdh").set("Irel",
            "S_uv*exp(-muUV*(zUVSurface-z))");
    }

    private static void configureV2LongTermAdhesiveMaterial(Model model) {
        model.material("matAdh").propertyGroup("def").set("youngsmodulus", "Einf");
        model.material("matAdh").propertyGroup("def").set("poissonsratio", "nuVisco");
    }

    private static void addDoseOde(Model model) {
        model.component(COMPONENT).physics().create("odeDose", "DomainODE", GEOMETRY,
            new String[]{"Duv_rel"});
        Physics dose = model.physics("odeDose");
        dose.selection().named("sel_adhesive");
        dose.prop("Units").set("DependentVariableQuantity", "time");
        dose.prop("Units").set("CustomDependentVariableUnit", "s");
        dose.prop("Units").set("SourceTermQuantity", "dimensionless");
        dose.prop("Units").set("CustomSourceTermUnit", "1");
        PhysicsFeature equation = dose.feature("dodeq1");
        equation.set("ea", "0");
        equation.set("da", "1");
        equation.set("f", "Irel");
        dose.feature("init1").set("Duv_rel", "0[s]");
    }

    private static void addV2ActivationAndMaxwell(Model model) {
        PhysicsFeature parent = linearElasticMaterial(model);
        PhysicsFeature activation = parent.feature().create("actGel", "Activation", 2);
        activation.selection().named("sel_adhesive");
        activation.set("activation_expression", "alpha>=alpha_gel || solid.wasactive");
        // Keep COMSOL's documented default actfac untouched; readback below is
        // a pre-solve fail-closed gate and must report exactly 1e-5.

        PhysicsFeature visco = parent.feature().create("vis1", "Viscoelasticity", 2);
        visco.selection().named("sel_adhesive");
        visco.set("MaterialModel", "GeneralizedMaxwell");
        visco.set("deformationModel", "full");
        // These parallel arrays are one ordered Maxwell branch: K, G, and tau
        // at index 0 must always refer to the same branch.
        visco.set("Kvm_v", new String[]{"Kbranch"});
        visco.set("Gvm", new String[]{"Gbranch"});
        visco.set("tauvm", new String[]{"tauMaxwell"});
    }

    private static void configureV2DoseSolverTolerance(Model model) {
        String field = "comp1_Duv_rel";
        for (String studyTag : new String[]{"stdUV", "stdBake", "stdCool"}) {
            String[] attached = model.study(studyTag).getSolverSequences("SolverSequence");
            if (attached.length != 1) {
                throw new IllegalStateException("v2 dose solver must have one attached sequence: " + studyTag);
            }
            SolverFeature time = findUniqueTimeFeature(model.sol(attached[0]), studyTag);
            Set<String> entryKeys = new HashSet<>();
            for (String entry : time.getEntryKeys("atolmethod")) entryKeys.add(entry);
            if (!entryKeys.contains(field)) {
                throw new IllegalStateException("v2 dose field is absent from generated solver tolerances: " + field);
            }
            time.setEntry("atolmethod", field, "unscaled");
            time.setEntry("atolvaluemethod", field, "manual");
            time.setEntry("atol", field, "1e-8");
            if (!"unscaled".equals(time.getString("atolmethod", field)) ||
                !"manual".equals(time.getString("atolvaluemethod", field)) ||
                Math.abs(Double.parseDouble(time.getString("atol", field)) - 1e-8) > 1e-20) {
                throw new IllegalStateException("v2 relative-dose absolute tolerance readback mismatch");
            }
        }
    }

    private static Map<String, Object> readbackV2(Model model) {
        Map<String, Object> result = readback(model);
        if (!Arrays.asList(model.physics().tags()).contains("odeDose")) {
            throw new IllegalStateException("cure-law v2 readback requires the relative-dose Domain ODE");
        }
        int[] adhesive = model.component(COMPONENT).selection("sel_adhesive").entities();
        if (adhesive.length != 1) throw new IllegalStateException("v2 adhesive selection is not unique");
        model.component(COMPONENT).measure().selection().geom(GEOMETRY, 2).set(adhesive);
        double[] adhesiveBounds = model.component(COMPONENT).measure().getBoundingBox();
        if (adhesiveBounds == null || adhesiveBounds.length < 4 ||
            Math.abs(adhesiveBounds[2] - ADHESIVE_Z_MIN_M) > 1e-12 ||
            Math.abs(adhesiveBounds[3] - ADHESIVE_Z_SURFACE_M) > 1e-12 ||
            !"550[um]".equals(model.param().get("zUVSurface"))) {
            throw new IllegalStateException("v2 Beer-Lambert surface must match the adhesive geometry top at z=550 um");
        }

        Physics dose = model.physics("odeDose");
        PhysicsFeature doseEquation = dose.feature("dodeq1");
        PhysicsFeature doseInitial = dose.feature("init1");
        if (!sameEntitySet(adhesive, dose.selection().entities()) ||
            !"time".equals(dose.prop("Units").getString("DependentVariableQuantity")) ||
            !"s".equals(dose.prop("Units").getString("CustomDependentVariableUnit")) ||
            !"dimensionless".equals(dose.prop("Units").getString("SourceTermQuantity")) ||
            !"1".equals(dose.prop("Units").getString("CustomSourceTermUnit")) ||
            !"Irel".equals(doseEquation.getString("f")) ||
            !"0[s]".equals(doseInitial.getString("Duv_rel"))) {
            throw new IllegalStateException("relative-dose domain, units, source, or zero initial state readback mismatch");
        }

        Expr exposure = model.component(COMPONENT).variable("vUVAdh");
        if (!sameEntitySet(adhesive, exposure.selection().entities(2)) ||
            !"S_uv*exp(-muUV*(zUVSurface-z))".equals(exposure.get("Irel")) ||
            !"if(t<120[s],1,0)".equals(exposure.get("S_uv")) ||
            !"(kUV*Irel+kT)*(1-alpha)".equals(model.component(COMPONENT).variable("v1").get("rate"))) {
            throw new IllegalStateException("spatial UV rate must be defined only on the adhesive selection");
        }

        PhysicsFeature parent = linearElasticMaterial(model);
        PhysicsFeature activation = parent.feature("actGel");
        PhysicsFeature visco = parent.feature("vis1");
        if (!"Activation".equals(activation.getType()) ||
            !"Viscoelasticity".equals(visco.getType()) ||
            !sameEntitySet(adhesive, activation.selection().entities()) ||
            !sameEntitySet(adhesive, visco.selection().entities()) ||
            !"alpha>=alpha_gel || solid.wasactive".equals(activation.getString("activation_expression")) ||
            Math.abs(Double.parseDouble(activation.getString("actfac")) - 1e-5) > 1e-15 ||
            Math.abs(model.param().evaluate("alpha_gel") - 0.5) > 1e-12 ||
            Math.abs(model.param().evaluate("zUVSurface") - ADHESIVE_Z_SURFACE_M) > 1e-12 ||
            !Arrays.asList(0.5, 1.0, 2.0).contains(model.param().evaluate("muScale")) ||
            Math.abs(model.param().evaluate("muUV") - model.param().evaluate("muScale") / 40.0e-6) > 1e-9 ||
            Math.abs(model.param().evaluate("Einf") - 0.5e9) > 1e-3 ||
            Math.abs(model.param().evaluate("Ebranch") - 1.5e9) > 1e-3 ||
            Math.abs(model.param().evaluate("nuVisco") - 0.35) > 1e-12 ||
            Math.abs(model.param().evaluate("tauMaxwell") - 300.0) > 1e-12 ||
            !"GeneralizedMaxwell".equals(visco.getString("MaterialModel")) ||
            !"full".equals(visco.getString("deformationModel")) ||
            !Arrays.equals(new String[]{"Kbranch"}, visco.getStringArray("Kvm_v")) ||
            !Arrays.equals(new String[]{"Gbranch"}, visco.getStringArray("Gvm")) ||
            !Arrays.equals(new String[]{"tauMaxwell"}, visco.getStringArray("tauvm"))) {
            throw new IllegalStateException("adhesive Activation/default or ordered full-Maxwell branch readback mismatch");
        }
        if (!"Einf".equals(model.material("matAdh").propertyGroup("def").getString("youngsmodulus")) ||
            !"nuVisco".equals(model.material("matAdh").propertyGroup("def").getString("poissonsratio"))) {
            throw new IllegalStateException("v2 adhesive long-term modulus material readback mismatch");
        }

        result.put("cure_law_version", "W24_CURE_LAW_V2");
        result.put("relative_exposure_dose", Map.of(
            "field", "Duv_rel", "unit", "s", "dependent_variable_quantity", "time",
            "source", "Irel", "source_term_quantity", "dimensionless", "initial", "0[s]",
            "selection", boxed(adhesive), "source_scope", "adhesive_only"));
        result.put("spatial_uv_readback", Map.of(
            "synthetic_estimated", true, "absolute_irradiance", false,
            "z_surface_m", ADHESIVE_Z_SURFACE_M, "adhesive_bounds_m", boxed(adhesiveBounds),
            "mu_scale", model.param().evaluate("muScale"),
            "mu_expression", model.param().get("muUV"),
            "intensity_expression", exposure.get("Irel"),
            "envelope_expression", exposure.get("S_uv"),
            "selection", boxed(exposure.selection().entities(2))));
        result.put("activation_readback", Map.of(
            "feature_type", activation.getType(), "selection", boxed(activation.selection().entities()),
            "expression", activation.getString("activation_expression"),
            "alpha_gel", model.param().get("alpha_gel"),
            "actfac", activation.getString("actfac"), "actfac_was_set", false,
            "state", "NATIVE_ACTIVATION_SEMANTICS_UNVERIFIED_FAIL_CLOSED"));
        Map<String, Object> viscoReadback = new LinkedHashMap<>();
        viscoReadback.put("feature_type", visco.getType());
        viscoReadback.put("selection", boxed(visco.selection().entities()));
        viscoReadback.put("material_model", visco.getString("MaterialModel"));
        viscoReadback.put("deformation_model", visco.getString("deformationModel"));
        viscoReadback.put("E_long_term", "0.5[GPa]");
        viscoReadback.put("E_branch_0", "1.5[GPa]");
        viscoReadback.put("nu", "0.35");
        viscoReadback.put("tau_branch_0", "300[s]");
        viscoReadback.put("K_long_term", model.param().get("Kinf"));
        viscoReadback.put("G_long_term", model.param().get("Ginf"));
        viscoReadback.put("K_branch_0", model.param().get("Kbranch"));
        viscoReadback.put("G_branch_0", model.param().get("Gbranch"));
        viscoReadback.put("Kvm_v", Arrays.asList(visco.getStringArray("Kvm_v")));
        viscoReadback.put("Gvm", Arrays.asList(visco.getStringArray("Gvm")));
        viscoReadback.put("tauvm", Arrays.asList(visco.getStringArray("tauvm")));
        viscoReadback.put("branch_order_binding", "same ordered index across Kvm_v/Gvm/tauvm");
        viscoReadback.put("branch_reference_state", "NATIVE_REFERENCE_STATE_UNVERIFIED_FAIL_CLOSED");
        result.put("viscoelastic_readback", viscoReadback);
        result.put("dose_solver_tolerance_readbacks", readbackV2DoseSolverTolerance(model));
        result.put("native_study_run_calls", 0);
        result.put("native_semantics", "UNVERIFIED_FAIL_CLOSED");
        result.put("module_license", "UNVERIFIED_FAIL_CLOSED");
        return result;
    }

    private static Map<String, Object> readbackV2DoseSolverTolerance(Model model) {
        Map<String, Object> readbacks = new LinkedHashMap<>();
        String field = "comp1_Duv_rel";
        for (String studyTag : new String[]{"stdUV", "stdBake", "stdCool"}) {
            String[] attached = model.study(studyTag).getSolverSequences("SolverSequence");
            if (attached.length != 1) {
                throw new IllegalStateException("v2 dose solver must have one attached sequence: " + studyTag);
            }
            SolverFeature time = findUniqueTimeFeature(model.sol(attached[0]), studyTag);
            Set<String> entryKeys = new HashSet<>();
            for (String entry : time.getEntryKeys("atolmethod")) entryKeys.add(entry);
            if (!entryKeys.contains(field) ||
                !"unscaled".equals(time.getString("atolmethod", field)) ||
                !"manual".equals(time.getString("atolvaluemethod", field)) ||
                Math.abs(Double.parseDouble(time.getString("atol", field)) - 1e-8) > 1e-20) {
                throw new IllegalStateException("v2 relative-dose absolute tolerance readback mismatch: " + studyTag);
            }
            readbacks.put(studyTag, Map.of("field", field, "scale", time.getString("atolmethod", field),
                "method", time.getString("atolvaluemethod", field), "absolute_tolerance", time.getString("atol", field)));
        }
        return readbacks;
    }

    /**
     * Save the complete native Equation View Expression table for every
     * feature of the configured Solid Mechanics interface. This action only
     * calls documented FeatureInfo getters; it never changes locks or model
     * properties and never evaluates a stress expression.
     */
    private static Map<String, Object> expressionInventory(Model model, Map<String, Object> args) {
        String pathText = String.valueOf(args.getOrDefault("output_path", ""));
        if (pathText.isBlank()) {
            throw new IllegalArgumentException("expression_inventory requires output_path");
        }
        Path output = Path.of(pathText).toAbsolutePath().normalize();
        Path parent = output.getParent();
        if (parent == null || !output.toString().startsWith(
                "/private/tmp/comsol-mcp-w24-cure-") || !Files.isDirectory(parent) ||
                Files.exists(output)) {
            throw new IllegalArgumentException(
                "expression inventory must be a new file under the task-owned W24 private work tree");
        }

        String[] observedPhysicsTags = model.component(COMPONENT).physics().tags();
        List<Object> featureTables = new ArrayList<>();
        List<Object> errors = new ArrayList<>();
        List<String> solidTags = new ArrayList<>();
        int featureCount = 0;
        int expressionRowCount = 0;
        int candidateRowCount = 0;
        for (String physicsTag : observedPhysicsTags) {
            if (!"solid".equals(physicsTag)) continue;
            solidTags.add(physicsTag);
            Physics physics = model.component(COMPONENT).physics().get(physicsTag);
            String[] observedFeatureTags = physics.feature().tags();
            for (String featureTag : observedFeatureTags) {
                featureCount++;
                Map<String, Object> featureTable = new LinkedHashMap<>();
                featureTable.put("physics_tag", physicsTag);
                featureTable.put("feature_tag", featureTag);
                try {
                    PhysicsFeature feature = physics.feature().get(featureTag);
                    FeatureInfo info = feature.featureInfo("info");
                    String[][] table = info.getInfoTable("Expression", "recursive", "all");
                    List<Object> rawRows = new ArrayList<>();
                    List<Object> candidates = new ArrayList<>();
                    for (int rowIndex = 0; rowIndex < table.length; rowIndex++) {
                        String[] row = table[rowIndex];
                        Object rawRow = row == null
                            ? null
                            : new ArrayList<Object>(Arrays.asList(row.clone()));
                        rawRows.add(rawRow);
                        List<String> cues = row == null
                            ? Collections.emptyList()
                            : stressCandidateCues(row);
                        if (!cues.isEmpty()) {
                            Map<String, Object> candidate = new LinkedHashMap<>();
                            candidate.put("row_index", rowIndex);
                            candidate.put("cues", cues);
                            candidate.put("raw_row", rawRow);
                            candidates.add(candidate);
                        }
                    }
                    featureTable.put("status", "READ");
                    featureTable.put("row_count", table.length);
                    featureTable.put("raw_rows", rawRows);
                    featureTable.put("stress_candidate_rows", candidates);
                    expressionRowCount += table.length;
                    candidateRowCount += candidates.size();
                } catch (Exception exception) {
                    Map<String, Object> error = new LinkedHashMap<>();
                    error.put("physics_tag", physicsTag);
                    error.put("feature_tag", featureTag);
                    error.put("exception_type", exception.getClass().getName());
                    error.put("message", String.valueOf(exception.getMessage()));
                    errors.add(error);
                    featureTable.put("status", "READ_FAILED");
                    featureTable.put("error", error);
                }
                featureTables.add(featureTable);
            }
        }
        boolean complete = solidTags.size() == 1 && featureCount > 0 && errors.isEmpty();
        Map<String, Object> inventory = new LinkedHashMap<>();
        inventory.put("schema", "W24_COMSOL_EQUATION_VIEW_EXPRESSION_INVENTORY_V1");
        inventory.put("status", complete ? "COMPLETE_NOT_EVALUATED" : "INCOMPLETE_NOT_EVALUATED");
        inventory.put("complete", complete);
        inventory.put("read_only", true);
        inventory.put("model_mutations", 0);
        inventory.put("study_run_calls", 0);
        inventory.put("component_tag", COMPONENT);
        inventory.put("observed_component_physics_tags", Arrays.asList(observedPhysicsTags));
        inventory.put("solid_physics_tags", solidTags);
        inventory.put("feature_tags_are_native_observations", true);
        inventory.put("table_request", Arrays.asList("Expression", "recursive", "all"));
        inventory.put("table_request_source",
            "COMSOL 6.4 FeatureInfo.getInfoTable(String,String...) API documentation");
        inventory.put("candidate_rule",
            "candidate rows have case-insensitive stress/cauchy/shear text or a solid.s[a-z0-9_]* identifier; optional component cues are hoop/radial/circumferential/azimuthal; full raw rows are preserved; discovery only");
        inventory.put("feature_count", featureCount);
        inventory.put("expression_row_count", expressionRowCount);
        inventory.put("stress_candidate_row_count", candidateRowCount);
        inventory.put("errors", errors);
        inventory.put("feature_tables", featureTables);

        byte[] bytes = (toJson(inventory) + "\n").getBytes(StandardCharsets.UTF_8);
        try (FileChannel channel = FileChannel.open(output, StandardOpenOption.CREATE_NEW,
                StandardOpenOption.WRITE)) {
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            while (buffer.hasRemaining()) channel.write(buffer);
            channel.force(true);
        } catch (IOException exception) {
            throw new IllegalStateException("failed to durably write native Equation View inventory", exception);
        }
        Map<String, Object> receipt = new LinkedHashMap<>();
        receipt.put("status", complete
            ? "NATIVE_EXPRESSION_INVENTORY_SAVED_NOT_EVALUATED"
            : "NATIVE_EXPRESSION_INVENTORY_INCOMPLETE_NOT_EVALUATED");
        receipt.put("path", output.toString());
        receipt.put("size_bytes", bytes.length);
        receipt.put("sha256", sha256(bytes));
        receipt.put("complete", complete);
        receipt.put("feature_count", featureCount);
        receipt.put("expression_row_count", expressionRowCount);
        receipt.put("stress_candidate_row_count", candidateRowCount);
        receipt.put("error_count", errors.size());
        receipt.put("read_only", true);
        receipt.put("model_mutations", 0);
        receipt.put("study_run_calls", 0);
        return receipt;
    }

    /**
     * Capture the exact current-model Equation View tables without solving or
     * changing model state. The API returns cell arrays but no separate column
     * heading API, so this artifact preserves every raw cell and row width and
     * records that labels are not exposed instead of inventing them.
     */
    private static Map<String, Object> equationViewReadbackV1(Model model, Map<String, Object> args) {
        String studyTag = safeApiToken(args.get("study_tag"), "study_tag");
        String solverTag = safeApiToken(args.get("solver_tag"), "solver_tag");
        String pathText = String.valueOf(args.getOrDefault("path", ""));
        if (pathText.isBlank() || !Arrays.asList(model.study().tags()).contains(studyTag)) {
            throw new IllegalArgumentException("equation_view_readback_v1 requires its exact attached study and output path");
        }
        String[] attached = model.study(studyTag).getSolverSequences("SolverSequence");
        if (attached.length != 1 || !solverTag.equals(attached[0])) {
            throw new IllegalStateException("Equation View readback requires the exact unique solver attached to its study");
        }
        SolverSequence solver = model.sol(solverTag);
        double[] storedTimes = solver.getPVals();
        if (!solver.isAttached() || !studyTag.equals(solver.study()) ||
            storedTimes == null || storedTimes.length == 0) {
            throw new IllegalStateException("Equation View readback solver is detached or has no stored solution times");
        }
        double previous = Double.NEGATIVE_INFINITY;
        for (double time : storedTimes) {
            if (!Double.isFinite(time) || time <= previous) {
                throw new IllegalStateException("Equation View readback stored solution times are not finite/increasing");
            }
            previous = time;
        }
        String quasistatic = model.physics("solid").prop("StructuralTransientBehavior")
            .getString("StructuralTransientBehavior");
        if (!"Quasistatic".equals(quasistatic)) {
            throw new IllegalStateException("Equation View capture requires actual Quasistatic Solid Mechanics readback");
        }
        String tlist = model.study(studyTag).feature("time1").getString("tlist");
        if (tlist == null || tlist.isBlank()) {
            throw new IllegalStateException("Equation View readback omitted the actual study time-list property");
        }

        String[] physicsTags = model.component(COMPONENT).physics().tags();
        List<Object> physicsRows = new ArrayList<>();
        List<Object> errors = new ArrayList<>();
        int featureCount = 0;
        int infoOwnerCount = 0;
        int tableCount = 0;
        int expressionRowCount = 0;
        for (String physicsTag : physicsTags) {
            Physics physics = model.component(COMPONENT).physics().get(physicsTag);
            Map<String, Object> physicsRow = new LinkedHashMap<>();
            physicsRow.put("physics_tag", physicsTag);
            physicsRow.put("physics_type", physics.getType());
            physicsRow.put("physics_path", COMPONENT + "/" + physicsTag);
            List<Object> physicsInfo = equationViewOwner(
                physics, "physics", physicsTag, COMPONENT + "/" + physicsTag, errors);
            infoOwnerCount++;
            tableCount += equationViewTableCount(physicsInfo);
            expressionRowCount += equationViewExpressionRows(physicsInfo);
            physicsRow.put("feature_info_tags", equationViewInfoTags(physicsInfo));
            physicsRow.put("equation_view", physicsInfo);
            List<Object> features = new ArrayList<>();
            for (String featureTag : physics.feature().tags()) {
                PhysicsFeature feature = physics.feature().get(featureTag);
                String featurePath = COMPONENT + "/" + physicsTag + "/" + featureTag;
                Map<String, Object> featureRow = equationViewFeature(
                    feature, featureTag, featurePath, errors);
                features.add(featureRow);
                featureCount += equationViewNestedFeatureCount(featureRow);
                infoOwnerCount += equationViewNestedInfoOwnerCount(featureRow);
                tableCount += equationViewNestedTableCount(featureRow);
                expressionRowCount += equationViewNestedExpressionRows(featureRow);
            }
            physicsRow.put("features", features);
            physicsRows.add(physicsRow);
        }
        boolean complete = physicsTags.length > 0 && featureCount > 0 && errors.isEmpty();
        Map<String, Object> artifact = new LinkedHashMap<>();
        artifact.put("schema", "W24_COMSOL_EQUATION_VIEW_READBACK_V1");
        artifact.put("status", complete ? "COMPLETE_RAW_TABLES_NOT_EVALUATED" : "INCOMPLETE_RAW_TABLES_NOT_EVALUATED");
        artifact.put("complete", complete);
        artifact.put("read_only", true);
        artifact.put("model_mutations", 0);
        artifact.put("native_study_run_calls", 0);
        artifact.put("component_tag", COMPONENT);
        artifact.put("component_physics_tags", Arrays.asList(physicsTags));
        artifact.put("study_tag", studyTag);
        artifact.put("solver_tag", solverTag);
        artifact.put("attached_solver_sequences", Arrays.asList(attached));
        artifact.put("study_tlist_readback", tlist);
        artifact.put("quasistatic_readback", quasistatic);
        artifact.put("stored_times_s", boxed(storedTimes));
        artifact.put("equation_view_table_types", Arrays.asList("Expression", "Shape", "Weak", "Constraint"));
        artifact.put("table_options", Arrays.asList("recursive", "all"));
        artifact.put("table_request_source", "COMSOL 6.4 FeatureInfo.getInfoTable(String,String...) API documentation");
        artifact.put("feature_info_tag_source", "COMSOL 6.4 EquationViewParent.featureInfo() and FeatureInfoList.tags() API");
        artifact.put("feature_info_api_sha256", "4235da3e67011348aeed61e3ca50fcdec888068f2152ac5d659b0280c68d03a0");
        artifact.put("equation_view_parent_api_sha256", "98f7b2c54e2a31dc52f9c0fdfae5073d343b035cb0d295130011f4b4ca0e732b");
        artifact.put("column_labels", "API_NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE");
        artifact.put("raw_cells_preserved", true);
        artifact.put("physics_count", physicsTags.length);
        artifact.put("physics_feature_count", featureCount);
        artifact.put("feature_info_owner_count", infoOwnerCount);
        artifact.put("table_count", tableCount);
        artifact.put("expression_row_count", expressionRowCount);
        artifact.put("errors", errors);
        artifact.put("physics", physicsRows);
        Path output = Path.of(pathText).toAbsolutePath().normalize();
        if (output.getParent() == null ||
            !output.toString().startsWith("/private/tmp/comsol-mcp-w24-cure-") ||
            !Files.isDirectory(output.getParent()) || Files.exists(output) || Files.isSymbolicLink(output)) {
            throw new IllegalArgumentException("Equation View artifact must be a new file in the private W24 task tree");
        }
        byte[] bytes = (toJson(artifact) + "\n").getBytes(StandardCharsets.UTF_8);
        writeEquationViewNewAndSync(output, bytes);
        Map<String, Object> receipt = new LinkedHashMap<>();
        receipt.put("status", complete ? "EQUATION_VIEW_RAW_TABLES_CAPTURED" : "EQUATION_VIEW_RAW_TABLES_INCOMPLETE");
        receipt.put("schema", "W24_COMSOL_EQUATION_VIEW_READBACK_V1");
        receipt.put("study_tag", studyTag);
        receipt.put("solver_tag", solverTag);
        receipt.put("study_tlist_readback", tlist);
        receipt.put("quasistatic_readback", quasistatic);
        receipt.put("stored_times_s", boxed(storedTimes));
        receipt.put("path", output.toString());
        receipt.put("size_bytes", bytes.length);
        receipt.put("sha256", sha256(bytes));
        receipt.put("complete", complete);
        receipt.put("physics_count", physicsTags.length);
        receipt.put("physics_feature_count", featureCount);
        receipt.put("feature_info_owner_count", infoOwnerCount);
        receipt.put("table_count", tableCount);
        receipt.put("expression_row_count", expressionRowCount);
        receipt.put("native_acceptance", "NOT_RUN");
        return receipt;
    }

    private static Map<String, Object> equationViewFeature(PhysicsFeature feature, String tag,
            String path, List<Object> errors) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("feature_tag", tag);
        row.put("feature_path", path);
        row.put("feature_type", feature.getType());
        List<Object> info = equationViewOwner(feature, "physics_feature", tag, path, errors);
        row.put("feature_info_tags", equationViewInfoTags(info));
        row.put("equation_view", info);
        List<Object> children = new ArrayList<>();
        for (String childTag : feature.feature().tags()) {
            children.add(equationViewFeature(feature.feature().get(childTag), childTag,
                path + "/" + childTag, errors));
        }
        row.put("children", children);
        return row;
    }

    private static List<Object> equationViewOwner(EquationViewParent owner, String ownerKind,
            String ownerTag, String ownerPath, List<Object> errors) {
        List<Object> result = new ArrayList<>();
        FeatureInfoList infoList = owner.featureInfo();
        String[] infoTags = infoList.tags();
        for (String infoTag : infoTags) {
            FeatureInfo info = infoList.get(infoTag);
            Map<String, Object> infoRow = new LinkedHashMap<>();
            infoRow.put("owner_kind", ownerKind);
            infoRow.put("owner_tag", ownerTag);
            infoRow.put("owner_path", ownerPath);
            infoRow.put("feature_info_tag", infoTag);
            infoRow.put("feature_info_native_tag", info.tag());
            infoRow.put("feature_info_name", info.name());
            List<Object> tables = new ArrayList<>();
            for (String tableType : new String[]{"Expression", "Shape", "Weak", "Constraint"}) {
                Map<String, Object> tableRow = new LinkedHashMap<>();
                tableRow.put("table_type", tableType);
                tableRow.put("options", Arrays.asList("recursive", "all"));
                tableRow.put("column_labels", "API_NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE");
                try {
                    String[][] table = info.getInfoTable(tableType, "recursive", "all");
                    if (table == null) throw new IllegalStateException("getInfoTable returned null");
                    List<Object> rawRows = new ArrayList<>();
                    List<Integer> rowWidths = new ArrayList<>();
                    int maxWidth = 0;
                    for (int rowIndex = 0; rowIndex < table.length; rowIndex++) {
                        String[] nativeRow = table[rowIndex];
                        if (nativeRow == null) {
                            rawRows.add(null);
                            rowWidths.add(-1);
                            continue;
                        }
                        List<Object> cells = new ArrayList<>();
                        for (String cell : nativeRow) cells.add(cell);
                        rawRows.add(cells);
                        rowWidths.add(nativeRow.length);
                        maxWidth = Math.max(maxWidth, nativeRow.length);
                    }
                    List<Integer> columnIndices = new ArrayList<>();
                    for (int i = 0; i < maxWidth; i++) columnIndices.add(i);
                    tableRow.put("status", "READ");
                    tableRow.put("row_count", table.length);
                    tableRow.put("max_column_count", maxWidth);
                    tableRow.put("column_indices_zero_based", columnIndices);
                    tableRow.put("row_widths", rowWidths);
                    tableRow.put("raw_rows", rawRows);
                } catch (RuntimeException exception) {
                    Map<String, Object> error = new LinkedHashMap<>();
                    error.put("owner_path", ownerPath);
                    error.put("feature_info_tag", infoTag);
                    error.put("table_type", tableType);
                    error.put("exception_type", exception.getClass().getName());
                    error.put("message", String.valueOf(exception.getMessage()));
                    errors.add(error);
                    tableRow.put("status", "READ_FAILED");
                    tableRow.put("error", error);
                }
                tables.add(tableRow);
            }
            infoRow.put("tables", tables);
            result.add(infoRow);
        }
        return result;
    }

    private static List<Object> equationViewInfoTags(List<Object> infoRows) {
        List<Object> tags = new ArrayList<>();
        for (Object raw : infoRows) {
            if (raw instanceof Map<?, ?>) tags.add(((Map<?, ?>) raw).get("feature_info_tag"));
        }
        return tags;
    }

    private static int equationViewTableCount(List<Object> infoRows) {
        int count = 0;
        for (Object raw : infoRows) {
            if (raw instanceof Map<?, ?> && ((Map<?, ?>) raw).get("tables") instanceof List<?>) {
                count += ((List<?>) ((Map<?, ?>) raw).get("tables")).size();
            }
        }
        return count;
    }

    private static int equationViewExpressionRows(List<Object> infoRows) {
        int count = 0;
        for (Object raw : infoRows) {
            if (!(raw instanceof Map<?, ?>) || !(((Map<?, ?>) raw).get("tables") instanceof List<?>)) continue;
            for (Object tableRaw : (List<?>) ((Map<?, ?>) raw).get("tables")) {
                if (tableRaw instanceof Map<?, ?> && "Expression".equals(((Map<?, ?>) tableRaw).get("table_type")) &&
                    ((Map<?, ?>) tableRaw).get("row_count") instanceof Number) {
                    count += ((Number) ((Map<?, ?>) tableRaw).get("row_count")).intValue();
                }
            }
        }
        return count;
    }

    private static int equationViewNestedInfoOwnerCount(Map<String, Object> feature) {
        int count = 1;
        Object children = feature.get("children");
        if (children instanceof List<?>) {
            for (Object child : (List<?>) children) if (child instanceof Map<?, ?>) {
                count += equationViewNestedInfoOwnerCount((Map<String, Object>) child);
            }
        }
        return count;
    }

    private static int equationViewNestedFeatureCount(Map<String, Object> feature) {
        int count = 1;
        Object children = feature.get("children");
        if (children instanceof List<?>) {
            for (Object child : (List<?>) children) if (child instanceof Map<?, ?>) {
                count += equationViewNestedFeatureCount((Map<String, Object>) child);
            }
        }
        return count;
    }

    private static int equationViewNestedTableCount(Map<String, Object> feature) {
        int count = equationViewTableCount((List<Object>) feature.get("equation_view"));
        Object children = feature.get("children");
        if (children instanceof List<?>) {
            for (Object child : (List<?>) children) if (child instanceof Map<?, ?>) {
                count += equationViewNestedTableCount((Map<String, Object>) child);
            }
        }
        return count;
    }

    private static int equationViewNestedExpressionRows(Map<String, Object> feature) {
        int count = equationViewExpressionRows((List<Object>) feature.get("equation_view"));
        Object children = feature.get("children");
        if (children instanceof List<?>) {
            for (Object child : (List<?>) children) if (child instanceof Map<?, ?>) {
                count += equationViewNestedExpressionRows((Map<String, Object>) child);
            }
        }
        return count;
    }

    private static String safeApiToken(Object value, String label) {
        if (!(value instanceof String) || !((String) value).matches("[A-Za-z0-9_-]{1,64}")) {
            throw new IllegalArgumentException(label + " must be a bounded API token");
        }
        return (String) value;
    }

    private static void writeEquationViewNewAndSync(Path output, byte[] bytes) {
        Path parent = output.getParent();
        if (parent == null || !Files.isDirectory(parent) || Files.exists(output) || Files.isSymbolicLink(output)) {
            throw new IllegalArgumentException("Equation View artifact must be a new file in an existing private directory");
        }
        try (FileChannel channel = FileChannel.open(output, StandardOpenOption.CREATE_NEW,
                StandardOpenOption.WRITE)) {
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            while (buffer.hasRemaining()) channel.write(buffer);
            channel.force(true);
        } catch (IOException exception) {
            throw new IllegalStateException("failed to fsync raw Equation View tables", exception);
        }
    }

    private static List<String> stressCandidateCues(String[] row) {
        List<String> cues = new ArrayList<>();
        if (row == null) return cues;
        StringBuilder joined = new StringBuilder();
        for (String cell : row) {
            if (cell != null) joined.append(cell).append('\u001f');
        }
        String text = joined.toString().toLowerCase(Locale.ROOT);
        boolean solidStressIdentifier = Pattern
            .compile("(?i)(?<![a-z0-9_])solid\\.s[a-z0-9_]*(?![a-z0-9_])")
            .matcher(text).find();
        for (String cue : new String[]{"stress", "cauchy", "shear"}) {
            if (text.contains(cue)) cues.add(cue);
        }
        if (cues.isEmpty() && !solidStressIdentifier) return cues;
        for (String cue : new String[]{"hoop", "radial", "circumferential", "azimuthal"}) {
            if (text.contains(cue)) cues.add(cue);
        }
        if (solidStressIdentifier) cues.add("solid.s-prefixed-identifier");
        return cues;
    }

    private static String toJson(Object value) {
        if (value == null) return "null";
        if (value instanceof Boolean || value instanceof Number) return value.toString();
        if (value instanceof String) return quoteJson((String) value);
        if (value instanceof Map) {
            StringBuilder out = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) out.append(',');
                first = false;
                out.append(quoteJson(String.valueOf(entry.getKey()))).append(':')
                   .append(toJson(entry.getValue()));
            }
            return out.append('}').toString();
        }
        if (value instanceof Collection) {
            StringBuilder out = new StringBuilder("[");
            boolean first = true;
            for (Object item : (Collection<?>) value) {
                if (!first) out.append(',');
                first = false;
                out.append(toJson(item));
            }
            return out.append(']').toString();
        }
        if (value.getClass().isArray()) {
            List<Object> items = new ArrayList<>();
            int length = java.lang.reflect.Array.getLength(value);
            for (int i = 0; i < length; i++) items.add(java.lang.reflect.Array.get(value, i));
            return toJson(items);
        }
        return quoteJson(value.toString());
    }

    private static String quoteJson(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            switch (ch) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (ch < 0x20) out.append(String.format("\\u%04x", (int) ch));
                    else out.append(ch);
            }
        }
        return out.append('"').toString();
    }

    private static String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder out = new StringBuilder();
            for (byte value : digest) out.append(String.format("%02x", value & 0xff));
            return out.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static void addParameters(Model model) {
        model.param().set("T0", "298.15[K]");
        model.param().set("alpha0", "0.20");
        model.param().set("alpha_gel", "0.50");
        model.param().set("A", "1.0e5[1/s]");
        model.param().set("Ea", "55.0[kJ/mol]");
        model.param().set("Rgas", "8.31446261815324[J/(mol*K)]");
        model.param().set("kUV", "1.0e-2[1/s]");
        model.param().set("Hrxn", "50.0[kJ/kg]");
        model.param().set("hconv", "250[W/(m^2*K)]");
        model.param().set("epsShrink", "0.015");
        model.param().set("rhoAl", "3900[kg/m^3]");
        model.param().set("rhoAu", "19300[kg/m^3]");
        model.param().set("rhoAdh", "1200[kg/m^3]");
        model.param().set("rhoFiber", "2200[kg/m^3]");
    }

    private static void buildGeometry(Model model) {
        model.component().create(COMPONENT);
        GeomSequence geom = model.component(COMPONENT).geom().create(GEOMETRY, 2);
        geom.lengthUnit("m");
        geom.axisymmetric(true);
        rectangle(geom, "rAl", 0, 0, 250e-6, 500e-6);
        rectangle(geom, "rAu", 0, 500e-6, 100e-6, 10e-6);
        rectangle(geom, "rAdh", 0, 510e-6, 100e-6, 40e-6);
        rectangle(geom, "rFiber", 0, 550e-6, 62.5e-6, 500e-6);
        geom.feature().create("uni1", "Union");
        geom.feature("uni1").selection("input").set(
            new String[]{"rAl", "rAu", "rAdh", "rFiber"});
        geom.feature("uni1").set("intbnd", "on");
        geom.feature("fin").set("action", "union");
        geom.run();
        if (geom.getSDim() != 2 || !geom.isAxisymmetric()) {
            throw new IllegalStateException("native geometry is not 2-D axisymmetric");
        }
    }

    private static void rectangle(GeomSequence geom, String tag, double r, double z,
                                  double width, double height) {
        geom.feature().create(tag, "Rectangle");
        geom.feature(tag).set("base", "corner");
        geom.feature(tag).set("pos", new String[]{meters(r), meters(z)});
        geom.feature(tag).set("size", new String[]{meters(width), meters(height)});
    }

    private static String meters(double value) { return Double.toString(value) + "[m]"; }

    private static void createSelections(Model model) {
        for (int i = 0; i < DOMAIN_TAGS.length; i++) {
            double[] b = DOMAIN_BOXES[i];
            createBox(model, DOMAIN_TAGS[i], 2,
                      b[0] + 10e-9, b[1] - 10e-9, b[2] + 10e-9, b[3] - 10e-9,
                      "intersects");
        }
        for (int i = 0; i < EDGE_TAGS.length; i++) {
            double[] b = EXTERNAL_EDGE_BOXES[i];
            createBox(model, EDGE_TAGS[i], 1,
                      b[0] - TOL, b[1] + TOL, b[2] - TOL, b[3] + TOL,
                      "inside");
        }
        createBox(model, "sel_axis", 1, -TOL, TOL, -TOL, 1050e-6 + TOL, "inside");
    }

    private static void createBox(Model model, String tag, int entityDim,
                                  double rMin, double rMax, double zMin, double zMax,
                                  String condition) {
        model.component(COMPONENT).selection().create(tag, "Box");
        model.component(COMPONENT).selection(tag).set("entitydim", entityDim);
        model.component(COMPONENT).selection(tag).set("xmin", rMin);
        model.component(COMPONENT).selection(tag).set("xmax", rMax);
        model.component(COMPONENT).selection(tag).set("ymin", zMin);
        model.component(COMPONENT).selection(tag).set("ymax", zMax);
        model.component(COMPONENT).selection(tag).set("condition", condition);
    }

    private static void addVariables(Model model) {
        model.component(COMPONENT).variable().create("v1");
        model.component(COMPONENT).variable("v1").set("Tenv",
            "if(t<120[s],T0,if(t<240[s],T0+(t-120[s])*(95[K]/120[s])," +
            "if(t<960[s],393.15[K],if(t<1500[s],393.15[K]-(t-960[s])*(95[K]/540[s]),T0))))");
        model.component(COMPONENT).variable("v1").set("IUV", "if(t<120[s],1,0)");
        model.component(COMPONENT).variable("v1").set("kT", "A*exp(-Ea/(Rgas*T))");
        model.component(COMPONENT).variable("v1").set("kT0", "A*exp(-Ea/(Rgas*T0))");
        model.component(COMPONENT).variable("v1").set("rate", "(kUV*IUV+kT)*(1-alpha)");
        model.component(COMPONENT).variable("v1").set("Qrxn", "rhoAdh*Hrxn*rate");
        model.component(COMPONENT).variable("v1").set("epsVolAl", "3*7.5e-6[1/K]*(T-T0)");
        model.component(COMPONENT).variable("v1").set("epsVolAu", "3*14.2e-6[1/K]*(T-T0)");
        model.component(COMPONENT).variable("v1").set("epsVolAdh",
            "3*50e-6[1/K]*(T-T0)-epsShrink*qpost");
        model.component(COMPONENT).variable("v1").set("epsVolFiber", "3*0.55e-6[1/K]*(T-T0)");
    }

    private static void addMaterials(Model model) {
        material(model, "matAl", "sel_alumina", "25[W/(m*K)]", "rhoAl",
                 "880[J/(kg*K)]", "370[GPa]", "0.22");
        material(model, "matAu", "sel_gold", "318[W/(m*K)]", "rhoAu",
                 "129[J/(kg*K)]", "78[GPa]", "0.44");
        material(model, "matAdh", "sel_adhesive", "0.25[W/(m*K)]", "rhoAdh",
                 "1000[J/(kg*K)]", "2[GPa]", "0.35");
        material(model, "matFiber", "sel_fiber", "1.38[W/(m*K)]", "rhoFiber",
                 "703[J/(kg*K)]", "72[GPa]", "0.17");
    }

    private static void material(Model model, String tag, String selection,
                                 String conductivity, String density, String heatCapacity,
                                 String youngsModulus, String poissonRatio) {
        model.material().create(tag, "Common", COMPONENT);
        model.material(tag).selection().named(selection);
        model.material(tag).propertyGroup("def").set("thermalconductivity", new String[]{conductivity});
        model.material(tag).propertyGroup("def").set("density", density);
        model.material(tag).propertyGroup("def").set("heatcapacity", heatCapacity);
        model.material(tag).propertyGroup("def").set("youngsmodulus", youngsModulus);
        model.material(tag).propertyGroup("def").set("poissonsratio", poissonRatio);
    }

    private static void addPhysics(Model model) {
        model.component(COMPONENT).physics().create("ht", "HeatTransfer", GEOMETRY);
        model.physics("ht").feature("init1").set("T", "T0");
        for (int i = 0; i < EDGE_TAGS.length; i++) {
            String tag = "conv" + (i + 1);
            model.physics("ht").create(tag, "HeatFluxBoundary", 1);
            PhysicsFeature flux = model.physics("ht").feature(tag);
            flux.selection().named(EDGE_TAGS[i]);
            flux.set("HeatFluxType", "ConvectiveHeatFlux");
            flux.set("h", "hconv");
            flux.set("Text", "Tenv");
        }
        model.physics("ht").create("rxnheat", "HeatSource", 2);
        model.physics("ht").feature("rxnheat").selection().named("sel_adhesive");
        model.physics("ht").feature("rxnheat").set("Q0", "Qrxn");

        model.component(COMPONENT).physics().create("solid", "SolidMechanics", GEOMETRY);
        model.physics("solid").prop("StructuralTransientBehavior")
             .set("StructuralTransientBehavior", "Quasistatic");
        model.physics("solid").create("fixbottom", "Fixed", 1);
        model.physics("solid").feature("fixbottom").selection().named("sel_ext_bottom");
        externalStrain(model, "estrAl", "sel_alumina", "epsVolAl");
        externalStrain(model, "estrAu", "sel_gold", "epsVolAu");
        externalStrain(model, "estrAdh", "sel_adhesive", "epsVolAdh");
        externalStrain(model, "estrFiber", "sel_fiber", "epsVolFiber");

        createOde(model, "odeAlpha", "alpha", "rate", "alpha0", "sel_adhesive");
        createOde(model, "odeIso", "alpha_iso", "(kUV*IUV+kT0)*(1-alpha_iso)",
                  "alpha0", "sel_adhesive");
        createOde(model, "odeQpost", "qpost",
                  "if(alpha>=alpha_gel,rate/(1-alpha_gel),0[1/s])", "0", "sel_adhesive");
    }

    private static PhysicsFeature linearElasticMaterial(Model model) {
        PhysicsFeature parent = model.physics("solid").feature("lemm1");
        String parentType = parent.getType();
        if (!"LinearElasticModel".equals(parentType)) {
            throw new IllegalStateException("solid.lemm1 must be the native LinearElasticModel feature; got " + parentType);
        }
        return parent;
    }

    private static PhysicsFeature externalStrain(Model model, String tag, String selection, String expression) {
        PhysicsFeature parent = linearElasticMaterial(model);
        if (Arrays.asList(parent.feature().tags()).contains(tag)) {
            throw new IllegalStateException("External Strain child already exists under solid.lemm1: " + tag);
        }
        PhysicsFeature feature = parent.feature().create(tag, "ExternalStrain", 2);
        if (!"ExternalStrain".equals(feature.getType())) {
            throw new IllegalStateException("solid.lemm1 child has unexpected COMSOL feature type: " + feature.getType());
        }
        feature.selection().named(selection);
        feature.set("StrainInput", "VolumetricStrain");
        feature.set("dV", expression);
        int[] expectedEntities = model.component(COMPONENT).selection(selection).entities(2);
        int[] observedEntities = feature.selection().entities();
        int[] sortedExpected = expectedEntities.clone();
        int[] sortedObserved = observedEntities.clone();
        Arrays.sort(sortedExpected);
        Arrays.sort(sortedObserved);
        if (!Arrays.equals(sortedExpected, sortedObserved) || expectedEntities.length == 0) {
            throw new IllegalStateException("External Strain child domain selection readback mismatch: " + tag);
        }
        if (!"VolumetricStrain".equals(feature.getString("StrainInput")) ||
            !expression.equals(feature.getString("dV"))) {
            throw new IllegalStateException("External Strain child property readback mismatch: " + tag);
        }
        return feature;
    }

    private static void createOde(Model model, String tag, String dependent,
                                  String source, String initial, String selection) {
        model.component(COMPONENT).physics().create(tag, "DomainODE", GEOMETRY,
            new String[]{dependent});
        model.physics(tag).selection().named(selection);
        PhysicsFeature ode = model.physics(tag).feature("dodeq1");
        ode.set("ea", "0");
        ode.set("da", "1");
        ode.set("f", source);
        PhysicsFeature init = model.physics(tag).feature("init1");
        init.set(dependent, initial);
    }

    private static void addMesh(Model model) {
        MeshSequence mesh = model.component(COMPONENT).mesh().create("mesh1", GEOMETRY);
        mesh.feature("size").set("custom", "on");
        mesh.feature("size").set("hmax", 50e-6);
        mesh.feature("size").set("hmin", 5e-6);
        localSize(model, mesh, "sizeAl", "sel_alumina", 50e-6, 10e-6);
        localSize(model, mesh, "sizeAu", "sel_gold", 50e-6, 5e-6);
        localSize(model, mesh, "sizeAdh", "sel_adhesive", 8e-6, 2e-6);
        localSize(model, mesh, "sizeFiber", "sel_fiber", 25e-6, 5e-6);
        mesh.run();
    }

    private static void localSize(Model model, MeshSequence mesh, String tag, String selection,
                                  double hmax, double hmin) {
        mesh.feature().create(tag, "Size");
        mesh.feature(tag).selection().geom(GEOMETRY, 2);
        mesh.feature(tag).selection().named(selection);
        mesh.feature(tag).set("custom", "on");
        mesh.feature(tag).set("hmax", hmax);
        mesh.feature(tag).set("hmin", hmin);
    }

    private static void addStudies(Model model) {
        transientStudy(model, "stdUV", "range(0[s],1[s],120[s])");
        transientStudy(model, "stdBake", "range(120[s],10[s],960[s])");
        transientStudy(model, "stdCool", "range(960[s],10[s],1500[s])");
        configureStudySolver(model, "stdUV", "", "1.0");
        configureStudySolver(model, "stdBake", "stdUV", "1.0");
        configureStudySolver(model, "stdCool", "stdBake", "1.0");
    }

    private static void transientStudy(Model model, String tag, String tlist) {
        Study study = model.study().create(tag);
        study.create("time", "Transient");
        study.feature("time").set("tlist", tlist);
        study.feature("time").set("usertol", "on");
        study.feature("time").set("rtol", "1e-5");
    }

    private static void configureStudySolver(Model model, String studyTag,
                                             String sourceStudy, String maxStepSeconds) {
        Study study = model.study(studyTag);
        StudyFeature step = study.feature("time");
        if (sourceStudy.isEmpty()) {
            step.set("useinitsol", "off");
        } else {
            step.set("useinitsol", "on");
            step.set("initmethod", "sol");
            step.set("initstudy", sourceStudy);
            step.set("solnum", "last");
        }
        study.createAutoSequences("sol");
        String[] attached = study.getSolverSequences("SolverSequence");
        if (attached.length != 1) {
            throw new IllegalStateException("expected one generated solver sequence for " + studyTag +
                ", got " + java.util.Arrays.toString(attached));
        }
        SolverSequence sequence = model.sol(attached[0]);
        if (!sequence.isAttached() || !studyTag.equals(sequence.study())) {
            throw new IllegalStateException("generated solver sequence is not attached to " + studyTag +
                ": " + attached[0] + " -> " + sequence.study());
        }
        SolverFeature time = findUniqueTimeFeature(sequence, studyTag);
        time.set("timemethod", "bdf");
        time.set("tunit", "s");
        time.set("tstepsbdf", "strict");
        time.set("maxstepconstraintbdf", "const");
        time.set("maxstepbdf", Double.parseDouble(maxStepSeconds));
        time.set("tout", "tsteps");
        time.set("tstepsstore", 1);
        time.set("rtol", 1.0e-5);
        time.set("atolglobalmethod", "unscaled");
        time.set("atolglobal", 1.0e-8);

        Set<String> entryKeys = new HashSet<>();
        for (String entryKey : time.getEntryKeys("atolmethod")) entryKeys.add(entryKey);
        for (int i = 0; i < SOLVER_FIELDS.length; i++) {
            String field = SOLVER_FIELDS[i];
            if (!entryKeys.contains(field)) {
                throw new IllegalStateException("generated time solver lacks the frozen dependent field " +
                    field + "; actual atolmethod entry keys=" + entryKeys);
            }
            time.setEntry("atolmethod", field, "unscaled");
            time.setEntry("atolvaluemethod", field, "manual");
            time.setEntry("atol", field, ABSOLUTE_TOLERANCES[i]);
        }
        assertSolverSettings(time, studyTag, maxStepSeconds);
    }

    private static SolverFeature findUniqueTimeFeature(SolverSequence sequence, String studyTag) {
        Map<String, SolverFeature> matches = new LinkedHashMap<>();
        collectTimeFeatures(sequence.feature().tags(), sequence.feature(), "", matches);
        if (matches.size() != 1) {
            throw new IllegalStateException("expected exactly one native Time solver feature for " + studyTag +
                ", found=" + matches.keySet());
        }
        return matches.values().iterator().next();
    }

    private static void collectTimeFeatures(String[] tags, com.comsol.model.SolverFeatureList list,
                                            String parent, Map<String, SolverFeature> matches) {
        for (String tag : tags) {
            SolverFeature feature = list.get(tag);
            String path = parent.isEmpty() ? tag : parent + "/" + tag;
            if ("Time".equals(feature.getType())) matches.put(path, feature);
            String[] childTags = feature.feature().tags();
            if (childTags.length > 0) collectTimeFeatures(childTags, feature.feature(), path, matches);
        }
    }

    private static void assertSolverSettings(SolverFeature time, String studyTag, String maxStepSeconds) {
        if (!"bdf".equals(time.getString("timemethod")) ||
            !"s".equals(time.getString("tunit")) ||
            !"strict".equals(time.getString("tstepsbdf")) ||
            !"const".equals(time.getString("maxstepconstraintbdf")) ||
            Math.abs(time.getDouble("maxstepbdf") - Double.parseDouble(maxStepSeconds)) > 1.0e-12 ||
            !"tsteps".equals(time.getString("tout")) || time.getInt("tstepsstore") != 1 ||
            Math.abs(time.getDouble("rtol") - 1.0e-5) > 1.0e-15 ||
            !"unscaled".equals(time.getString("atolglobalmethod")) ||
            Math.abs(time.getDouble("atolglobal") - 1.0e-8) > 1.0e-18) {
            throw new IllegalStateException("native BDF/output/tolerance readback mismatch for " + studyTag);
        }
        for (int i = 0; i < SOLVER_FIELDS.length; i++) {
            String field = SOLVER_FIELDS[i];
            if (!"unscaled".equals(time.getString("atolmethod", field)) ||
                !"manual".equals(time.getString("atolvaluemethod", field)) ||
                Math.abs(Double.parseDouble(time.getString("atol", field)) -
                    Double.parseDouble(ABSOLUTE_TOLERANCES[i])) > 1.0e-20) {
                throw new IllegalStateException("native field-specific absolute tolerance mismatch for " +
                    studyTag + "/" + field);
            }
        }
    }

    private static Map<String, Object> readback(Model model) {
        Map<String, Object> result = new LinkedHashMap<>();
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        result.put("geometry_dimension", geom.getSDim());
        result.put("geometry_axisymmetric", geom.isAxisymmetric());
        result.put("geometry_length_unit", geom.lengthUnit());
        result.put("geometry_domain_count", geom.getNDomains());
        if (geom.getNDomains() != 4) {
            throw new IllegalStateException("coupon geometry must retain four touching material domains; got " +
                geom.getNDomains());
        }
        result.put("geometry_bounding_box", geom.getBoundingBox());
        result.put("geometry_entity_counts", model.component(COMPONENT).measure().getNEntities());
        Map<String, Object> domains = new LinkedHashMap<>();
        for (int i = 0; i < DOMAIN_TAGS.length; i++) {
            int[] entities = model.component(COMPONENT).selection(DOMAIN_TAGS[i]).entities();
            if (entities.length != 1) {
                throw new IllegalStateException("domain center selection must resolve to exactly one domain: " +
                    DOMAIN_TAGS[i] + " -> " + java.util.Arrays.toString(entities));
            }
            double volume = Double.NaN;
            if (entities.length > 0) {
                model.component(COMPONENT).measure().selection().geom(GEOMETRY, 2).set(entities);
                volume = model.component(COMPONENT).measure().getVolume();
            }
            double relativeError = Math.abs(volume - EXPECTED_VOLUMES[i]) / EXPECTED_VOLUMES[i];
            domains.put(DOMAIN_LABELS[i], Map.of(
                "selection", DOMAIN_TAGS[i], "entities", entities, "volume_m3", volume,
                "expected_volume_m3", EXPECTED_VOLUMES[i],
                "relative_volume_error", relativeError
            ));
            if (!Double.isFinite(volume) || relativeError > 1.0e-6) {
                throw new IllegalStateException("axisymmetric physical domain volume failed pre-solve gate for " +
                    DOMAIN_LABELS[i] + ": native=" + volume + ", expected=" + EXPECTED_VOLUMES[i] +
                    ", relative_error=" + relativeError);
            }
        }
        result.put("domain_readbacks", domains);
        Map<String, Object> externalEdges = new LinkedHashMap<>();
        Set<Integer> externalEntityIds = new HashSet<>();
        for (int i = 0; i < EDGE_TAGS.length; i++) {
            String tag = EDGE_TAGS[i];
            int[] entities = model.component(COMPONENT).selection(tag).entities();
            if (entities.length != 1) {
                throw new IllegalStateException("external boundary selection must identify one edge: " + tag +
                    " -> " + java.util.Arrays.toString(entities));
            }
            for (int entity : entities) {
                if (!externalEntityIds.add(entity)) {
                    throw new IllegalStateException("external boundary entity is selected more than once: " + entity);
                }
            }
            model.component(COMPONENT).measure().selection().geom(GEOMETRY, 1).set(entities);
            double[] boundingBox = model.component(COMPONENT).measure().getBoundingBox();
            assertBoundingBox(tag, boundingBox, EXTERNAL_EDGE_BOXES[i]);
            externalEdges.put(tag, Map.of("entities", entities, "bounding_box", boundingBox,
                "expected_box", EXTERNAL_EDGE_BOXES[i]));
        }
        int[] axisEntities = model.component(COMPONENT).selection("sel_axis").entities();
        if (axisEntities.length == 0) throw new IllegalStateException("axis symmetry boundary readback is empty");
        Set<Integer> axisIds = new HashSet<>();
        for (int entity : axisEntities) axisIds.add(entity);
        for (int entity : externalEntityIds) {
            if (axisIds.contains(entity)) {
                throw new IllegalStateException("axis-of-symmetry edge selected for convection: " + entity);
            }
        }
        result.put("external_boundary_entity_count", externalEntityIds.size());
        result.put("axis_entity_ids", axisEntities);
        result.put("external_boundary_readbacks", externalEdges);
        result.put("physics_tags", model.physics().tags());
        result.put("study_tags", model.study().tags());
        result.put("material_tags", model.material().tags());
        result.put("mesh_tags", model.component(COMPONENT).mesh().tags());
        result.put("ode_field_readbacks", odeReadbacks(model));
        result.put("solid_quasistatic_readback", model.physics("solid").prop("StructuralTransientBehavior")
            .getString("StructuralTransientBehavior"));
        if (!"Quasistatic".equals(result.get("solid_quasistatic_readback"))) {
            throw new IllegalStateException("Solid Mechanics did not read back the frozen quasistatic setting");
        }
        result.put("external_strain_readbacks", externalStrainReadbacks(model));
        result.put("heat_readbacks", heatReadbacks(model));
        result.put("study_time_readbacks", studyTimeReadbacks(model));
        result.put("solver_readbacks", solverReadbacks(model));
        result.put("solver_submissions", 0);
        return result;
    }

    private static void assertBoundingBox(String tag, double[] actual, double[] expected) {
        if (actual.length < 4) throw new IllegalStateException("unexpected 2-D selection bounding box for " + tag);
        double[] observed = {actual[0], actual[1], actual[2], actual[3]};
        for (int i = 0; i < observed.length; i++) {
            if (Math.abs(observed[i] - expected[i]) > TOL) {
                throw new IllegalStateException("boundary selection includes wrong geometry for " + tag +
                    ": observed=" + java.util.Arrays.toString(observed) +
                    ", expected=" + java.util.Arrays.toString(expected));
            }
        }
    }

    private static boolean sameEntitySet(int[] left, int[] right) {
        if (left == null || right == null || left.length != right.length) return false;
        int[] a = left.clone();
        int[] b = right.clone();
        Arrays.sort(a);
        Arrays.sort(b);
        return Arrays.equals(a, b);
    }

    private static List<Integer> boxed(int[] values) {
        List<Integer> out = new ArrayList<>();
        for (int value : values) out.add(value);
        return out;
    }

    private static List<Double> boxed(double[] values) {
        List<Double> out = new ArrayList<>();
        for (double value : values) out.add(value);
        return out;
    }

    private static Map<String, Object> odeReadbacks(Model model) {
        Map<String, Object> out = new LinkedHashMap<>();
        odeReadback(model, out, "odeAlpha", "alpha");
        odeReadback(model, out, "odeIso", "alpha_iso");
        odeReadback(model, out, "odeQpost", "qpost");
        return out;
    }

    private static void odeReadback(Model model, Map<String, Object> out, String tag, String field) {
        PhysicsFeature equation = model.physics(tag).feature("dodeq1");
        PhysicsFeature initial = model.physics(tag).feature("init1");
        out.put(tag, Map.of("selection", model.physics(tag).selection().entities(),
            "field", field, "ea", equation.getString("ea"), "da", equation.getString("da"),
            "f", equation.getString("f"), "initial", initial.getString(field)));
    }

    private static Map<String, Object> externalStrainReadbacks(Model model) {
        Map<String, Object> out = new LinkedHashMap<>();
        PhysicsFeature parent = linearElasticMaterial(model);
        String[] tags = {"estrAl", "estrAu", "estrAdh", "estrFiber"};
        String[] selections = {"sel_alumina", "sel_gold", "sel_adhesive", "sel_fiber"};
        String[] expressions = {"epsVolAl", "epsVolAu", "epsVolAdh", "epsVolFiber"};
        for (int i = 0; i < tags.length; i++) {
            String tag = tags[i];
            if (!Arrays.asList(parent.feature().tags()).contains(tag)) {
                throw new IllegalStateException("missing External Strain child under solid.lemm1: " + tag);
            }
            PhysicsFeature feature = parent.feature(tag);
            if (!"ExternalStrain".equals(feature.getType())) {
                throw new IllegalStateException("wrong COMSOL feature type under solid.lemm1: " + tag);
            }
            int[] expectedEntities = model.component(COMPONENT).selection(selections[i]).entities(2);
            int[] observedEntities = feature.selection().entities();
            int[] sortedExpected = expectedEntities.clone();
            int[] sortedObserved = observedEntities.clone();
            Arrays.sort(sortedExpected);
            Arrays.sort(sortedObserved);
            String strainInput = feature.getString("StrainInput");
            String volumetricStrain = feature.getString("dV");
            if (expectedEntities.length == 0 || !Arrays.equals(sortedExpected, sortedObserved) ||
                !"VolumetricStrain".equals(strainInput) || !expressions[i].equals(volumetricStrain)) {
                throw new IllegalStateException("External Strain child readback mismatch: " + tag);
            }
            out.put(tag, Map.of("parent_feature_tag", "lemm1", "parent_feature_type", parent.getType(),
                "feature_type", feature.getType(), "selection", observedEntities,
                "StrainInput", strainInput, "dV", volumetricStrain));
        }
        return out;
    }

    private static Map<String, Object> heatReadbacks(Model model) {
        Map<String, Object> out = new LinkedHashMap<>();
        PhysicsFeature init = model.physics("ht").feature("init1");
        out.put("initial_temperature", init.getString("T"));
        PhysicsFeature source = model.physics("ht").feature("rxnheat");
        out.put("reaction_heat", Map.of("selection", source.selection().entities(), "Q0", source.getString("Q0")));
        Map<String, Object> convection = new LinkedHashMap<>();
        for (int i = 1; i <= EDGE_TAGS.length; i++) {
            PhysicsFeature flux = model.physics("ht").feature("conv" + i);
            convection.put("conv" + i, Map.of("selection", flux.selection().entities(),
                "HeatFluxType", flux.getString("HeatFluxType"), "h", flux.getString("h"),
                "Text", flux.getString("Text")));
        }
        out.put("convection", convection);
        return out;
    }

    private static Map<String, Object> studyTimeReadbacks(Model model) {
        Map<String, Object> out = new LinkedHashMap<>();
        String[] studies = {"stdUV", "stdBake", "stdCool"};
        String[] sources = {"", "stdUV", "stdBake"};
        for (int i = 0; i < studies.length; i++) {
            String tag = studies[i];
            StudyFeature time = model.study(tag).feature("time");
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tlist", time.getString("tlist"));
            row.put("rtol", time.getString("rtol"));
            row.put("usertol", time.getString("usertol"));
            row.put("useinitsol", time.getString("useinitsol"));
            row.put("initmethod", time.getString("initmethod"));
            row.put("initstudy", time.getString("initstudy"));
            row.put("solnum", time.getString("solnum"));
            String expectedUse = sources[i].isEmpty() ? "off" : "on";
            if (!expectedUse.equals(time.getString("useinitsol"))) {
                throw new IllegalStateException("stage transfer enablement readback mismatch for " + tag);
            }
            if (!sources[i].isEmpty() &&
                (!"sol".equals(time.getString("initmethod")) ||
                 !sources[i].equals(time.getString("initstudy")) ||
                 !"last".equals(time.getString("solnum")))) {
                throw new IllegalStateException("stage source study/solution readback mismatch for " + tag);
            }
            out.put(tag, row);
        }
        return out;
    }

    private static Map<String, Object> solverReadbacks(Model model) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (String studyTag : new String[]{"stdUV", "stdBake", "stdCool"}) {
            String[] attached = model.study(studyTag).getSolverSequences("SolverSequence");
            if (attached.length != 1) {
                throw new IllegalStateException("solver association changed for " + studyTag);
            }
            SolverSequence sequence = model.sol(attached[0]);
            SolverFeature time = findUniqueTimeFeature(sequence, studyTag);
            Map<String, Object> values = new LinkedHashMap<>();
            values.put("sequence_tag", attached[0]);
            values.put("sequence_attached", sequence.isAttached());
            values.put("sequence_study", sequence.study());
            values.put("sequence_type", sequence.getType());
            values.put("time_feature_tag", time.tag());
            values.put("time_feature_type", time.getType());
            values.put("timemethod", time.getString("timemethod"));
            values.put("tunit", time.getString("tunit"));
            values.put("tstepsbdf", time.getString("tstepsbdf"));
            values.put("maxstepconstraintbdf", time.getString("maxstepconstraintbdf"));
            values.put("maxstepbdf", time.getDouble("maxstepbdf"));
            values.put("tout", time.getString("tout"));
            values.put("tstepsstore", time.getInt("tstepsstore"));
            values.put("rtol", time.getDouble("rtol"));
            values.put("atolglobalmethod", time.getString("atolglobalmethod"));
            values.put("atolglobal", time.getDouble("atolglobal"));
            Map<String, Object> fields = new LinkedHashMap<>();
            for (String field : SOLVER_FIELDS) {
                fields.put(field, Map.of(
                    "atolmethod", time.getString("atolmethod", field),
                    "atolvaluemethod", time.getString("atolvaluemethod", field),
                    "atol", time.getString("atol", field)));
            }
            values.put("field_tolerances", fields);
            out.put(studyTag, values);
            assertSolverSettings(time, studyTag, "1.0");
        }
        return out;
    }
}
