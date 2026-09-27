import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.SolverSequence;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.physics.PhysicsFeature;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

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
