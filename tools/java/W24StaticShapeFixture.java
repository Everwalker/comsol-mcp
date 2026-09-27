import com.comsol.model.GeomFeature;
import com.comsol.model.GeomSequence;
import com.comsol.model.Material;
import com.comsol.model.MeshSequence;
import com.comsol.model.Model;
import com.comsol.model.Study;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverFeatureList;
import com.comsol.model.SolverSequence;
import com.comsol.model.physics.MultiphysicsCoupling;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.util.ModelUtil;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * W24 synthetic static glue-shape setup fixture.
 *
 * This class only constructs geometry, materials, physics, mesh, and study
 * configuration. It deliberately contains no Study.run call. Phase
 * Initialization is a solver step and is not executed by this builder.
 */
public final class W24StaticShapeFixture {
    private static final String COMPONENT = "comp1";
    private static final String GEOMETRY = "geom1";

    // Paired cases have an equal analytic axisymmetric liquid volume.
    private static final double R_DROP = 500e-6;
    private static final double H_FLAT = 100e-6;
    private static final double R_MESA = 300e-6;
    private static final double H_MESA = 40e-6;
    private static final double Z_STEP_TOP = H_FLAT + square(R_MESA / R_DROP) * H_MESA;
    private static final double R_BOX = 1.25e-3;
    private static final double H_BOX = 0.75e-3;
    private static final double CHI = 50.0;
    private static final double RHO_GLUE = 1200.0;
    private static final double MU_GLUE = 1.0;
    private static final double RHO_GAS = 1.2;
    private static final double MU_GAS = 0.018;
    private static final double SIGMA = 0.03;
    private static final double THETA_WALL = Math.PI / 3.0;
    private static final double BOUNDARY_TOL = 1e-9;
    private static final double DOMAIN_PROBE_HALF_WIDTH = 0.5e-6;

    private W24StaticShapeFixture() { }

    public static Object run(Model parent, Map<String, Object> args) {
        String action = String.valueOf(args.getOrDefault("action", "build"));
        if (!"build".equals(action)) {
            throw new IllegalArgumentException("unsupported static-shape action: " + action);
        }
        String caseId = String.valueOf(args.getOrDefault("case_id", ""));
        if (!("flat".equals(caseId) || "step".equals(caseId))) {
            throw new IllegalArgumentException("case_id must be exactly flat or step");
        }
        String configurationId = String.valueOf(args.getOrDefault("configuration_id", "baseline"));
        return build(parent, caseId, configuration(configurationId));
    }

    private static Map<String, Object> build(Model parent, String caseId, Configuration configuration) {
        Set<String> priorModelTags = new HashSet<>(Arrays.asList(ModelUtil.tags()));
        Model created = ModelUtil.createUnique("w24shape_" + configuration.id + "_" + caseId);
        String modelTag = null;
        for (String candidate : ModelUtil.tags()) {
            if (!priorModelTags.contains(candidate) &&
                candidate.startsWith("w24shape_" + configuration.id + "_" + caseId)) {
                if (modelTag != null) {
                    throw new IllegalStateException("COMSOL created multiple static-shape models");
                }
                modelTag = candidate;
            }
        }
        if (modelTag == null) {
            throw new IllegalStateException("COMSOL did not register the unique static-shape model");
        }

        boolean complete = false;
        try {
            Model model = created;
            addParameters(model, configuration);
            model.component().create(COMPONENT);
            GeomSequence geom = model.component(COMPONENT).geom().create(GEOMETRY, 2);
            geom.lengthUnit("m");
            geom.axisymmetric(true);
            buildTwoFluidGeometry(geom, caseId);
            if (geom.getSDim() != 2 || !geom.isAxisymmetric() || geom.getNDomains() != 2) {
                throw new IllegalStateException("static-shape geometry must be exactly two 2-D axisymmetric fluid domains");
            }

            int[] glueDomainIds = createDomainProbe(model, "sel_glue_domain",
                caseId.equals("flat") ? R_DROP * 0.5 : R_MESA * 0.5,
                caseId.equals("flat") ? H_FLAT * 0.5 : H_MESA + (Z_STEP_TOP - H_MESA) * 0.5);
            int[] gasDomainIds = createDomainProbe(model, "sel_gas_domain", R_BOX * 0.75, H_BOX * 0.5);
            if (glueDomainIds.length != 1 || gasDomainIds.length != 1 ||
                glueDomainIds[0] == gasDomainIds[0] || geom.getNDomains() != 2) {
                throw new IllegalStateException("glue/gas selections do not resolve to two distinct domains");
            }
            int[] allDomainIds = sortedUnion(glueDomainIds, gasDomainIds);

            Map<String, int[]> wetSelections = createWettingSelections(model, caseId);
            assertDisjointAndExact(wetSelections, caseId.equals("step") ? 3 : 1);
            int[] axisIds = createBoundaryBox(model, "sel_axis", -BOUNDARY_TOL,
                BOUNDARY_TOL, -BOUNDARY_TOL, H_BOX + BOUNDARY_TOL);
            if (axisIds.length == 0) {
                throw new IllegalStateException("axis boundary probe did not resolve the symmetry axis");
            }
            for (int[] wetIds : wetSelections.values()) {
                if (intersects(axisIds, wetIds)) {
                    throw new IllegalStateException("axis boundary was accidentally assigned substrate wetting");
                }
            }

            Map<String, Object> materialReadback = addMaterials(model, allDomainIds);
            Map<String, Object> physicsReadback = addPhysics(model, caseId, glueDomainIds,
                gasDomainIds, wetSelections);
            addShapeMetricDefinitions(model);
            Map<String, Object> meshReadback = addMesh(model, configuration);
            Map<String, Object> studyReadback = addUnsolvedStudy(model, configuration);

            double flatVolume = Math.PI * square(R_DROP) * H_FLAT;
            double actualInitialVolume = caseId.equals("flat")
                ? flatVolume
                : Math.PI * (square(R_DROP) * Z_STEP_TOP - square(R_MESA) * H_MESA);
            if (Math.abs(actualInitialVolume - flatVolume) > Math.max(1e-18, flatVolume * 1e-13)) {
                throw new IllegalStateException("paired flat/step analytic glue volumes do not match");
            }

            Map<String, Object> result = new LinkedHashMap<>();
            result.put("status", "BUILT_NOT_SOLVED");
            result.put("native_acceptance", "NOT_RUN");
            result.put("case_id", caseId);
            result.put("configuration_id", configuration.id);
            result.put("model_tag", modelTag);
            result.put("source_parent_model_tag", parent.tag());
            result.put("geometry_dimension", geom.getSDim());
            result.put("geometry_axisymmetric", geom.isAxisymmetric());
            result.put("geometry_domain_count", geom.getNDomains());
            result.put("geometry_boundary_count", geom.getNBoundaries());
            result.put("glue_domain_ids", boxed(glueDomainIds));
            result.put("gas_domain_ids", boxed(gasDomainIds));
            result.put("all_domain_ids", boxed(allDomainIds));
            Map<String, Object> wettingIds = new LinkedHashMap<>();
            for (Map.Entry<String, int[]> entry : wetSelections.entrySet()) {
                wettingIds.put(entry.getKey(), boxed(entry.getValue()));
            }
            result.put("substrate_wetting_boundary_ids", wettingIds);
            result.put("mesa_is_impermeable_solid_wall_feature", false);
            result.put("glue_volume_analytic_m3", actualInitialVolume);
            result.put("paired_flat_volume_analytic_m3", flatVolume);
            result.put("step_top_height_m", Z_STEP_TOP);
            result.put("material_readback", materialReadback);
            result.put("physics_readback", physicsReadback);
            result.put("metric_definitions", metricDefinitions());
            result.put("mesh_readback", meshReadback);
            result.put("study_readback", studyReadback);
            result.put("study_run_calls", 0);
            result.put("phase_initialization_executed", false);
            result.put("result_capture_state", "CONFIGURED_NOT_EVALUATED");
            complete = true;
            return result;
        } finally {
            if (!complete) ModelUtil.remove(modelTag);
        }
    }

    private static void addParameters(Model model, Configuration configuration) {
        model.param().set("Rdrop", meters(R_DROP));
        model.param().set("hFlat", meters(H_FLAT));
        model.param().set("Rmesa", meters(R_MESA));
        model.param().set("hMesa", meters(H_MESA));
        model.param().set("zStepTop", "hFlat+(Rmesa/Rdrop)^2*hMesa");
        model.param().set("Rbox", meters(R_BOX));
        model.param().set("Hbox", meters(H_BOX));
        model.param().set("rhoGlue", number(RHO_GLUE) + "[kg/m^3]");
        model.param().set("muGlue", number(MU_GLUE) + "[Pa*s]");
        model.param().set("rhoGas", number(RHO_GAS) + "[kg/m^3]");
        model.param().set("muGas", number(MU_GAS) + "[Pa*s]");
        model.param().set("sigma0", number(SIGMA) + "[N/m]");
        model.param().set("thetaSubstrate", "pi/3[rad]");
        model.param().set("epsPF", meters(configuration.epsilonM));
        model.param().set("chiPF", number(CHI));
        model.param().set("lambdaPF", "3*sigma0*epsPF/(2*sqrt(2))");
        model.param().set("tCapillary", "muGlue*Rdrop/sigma0");
        model.param().set("tShapeEnd", "20*tCapillary");
    }

    private static void buildTwoFluidGeometry(GeomSequence geom, String caseId) {
        if ("flat".equals(caseId)) {
            solidPolygon(geom, "glue", new double[]{0.0, R_DROP, R_DROP, 0.0},
                new double[]{0.0, 0.0, H_FLAT, H_FLAT});
            solidPolygon(geom, "gas", new double[]{0.0, R_DROP, R_DROP, R_BOX, R_BOX, 0.0},
                new double[]{H_FLAT, H_FLAT, 0.0, 0.0, H_BOX, H_BOX});
        } else {
            // The connected glue polygon wraps the raised mesa: top, side, and
            // lower substrate remain ordinary wetting surfaces. No wall feature
            // is inserted through the liquid/gas domain.
            solidPolygon(geom, "glue", new double[]{0.0, R_MESA, R_MESA, R_DROP, R_DROP, 0.0},
                new double[]{H_MESA, H_MESA, 0.0, 0.0, Z_STEP_TOP, Z_STEP_TOP});
            solidPolygon(geom, "gas", new double[]{0.0, R_DROP, R_DROP, R_BOX, R_BOX, 0.0},
                new double[]{Z_STEP_TOP, Z_STEP_TOP, 0.0, 0.0, H_BOX, H_BOX});
        }
        geom.feature().create("uni1", "Union");
        geom.feature("uni1").selection("input").set(new String[]{"glue", "gas"});
        geom.feature("uni1").set("intbnd", "on");
        geom.feature("fin").set("action", "union");
        geom.run();
    }

    private static void solidPolygon(GeomSequence geom, String tag, double[] r, double[] z) {
        if (r.length != z.length || r.length < 3) {
            throw new IllegalArgumentException("invalid polygon coordinate arrays for " + tag);
        }
        GeomFeature polygon = geom.feature().create(tag, "Polygon");
        polygon.set("type", "solid");
        polygon.set("x", r);
        polygon.set("y", z);
    }

    private static int[] createDomainProbe(Model model, String tag, double r, double z) {
        double half = DOMAIN_PROBE_HALF_WIDTH;
        model.component(COMPONENT).selection().create(tag, "Box");
        model.component(COMPONENT).selection(tag).set("entitydim", 2);
        model.component(COMPONENT).selection(tag).set("xmin", r - half);
        model.component(COMPONENT).selection(tag).set("xmax", r + half);
        model.component(COMPONENT).selection(tag).set("ymin", z - half);
        model.component(COMPONENT).selection(tag).set("ymax", z + half);
        model.component(COMPONENT).selection(tag).set("condition", "intersects");
        return model.component(COMPONENT).selection(tag).entities(2);
    }

    private static Map<String, int[]> createWettingSelections(Model model, String caseId) {
        Map<String, double[]> bounds = new LinkedHashMap<>();
        if ("flat".equals(caseId)) {
            // Apply the same contact-angle law to the wettable substrate even
            // after the contact line passes its initial radius.
            bounds.put("sel_wet_flat_base", new double[]{-BOUNDARY_TOL, R_BOX + BOUNDARY_TOL,
                -BOUNDARY_TOL, BOUNDARY_TOL});
        } else {
            bounds.put("sel_wet_mesa_top", new double[]{-BOUNDARY_TOL, R_MESA + BOUNDARY_TOL,
                H_MESA - BOUNDARY_TOL, H_MESA + BOUNDARY_TOL});
            bounds.put("sel_wet_mesa_side", new double[]{R_MESA - BOUNDARY_TOL,
                R_MESA + BOUNDARY_TOL, -BOUNDARY_TOL, H_MESA + BOUNDARY_TOL});
            bounds.put("sel_wet_lower_base", new double[]{R_MESA - BOUNDARY_TOL,
                R_BOX + BOUNDARY_TOL, -BOUNDARY_TOL, BOUNDARY_TOL});
        }

        Map<String, int[]> selected = new LinkedHashMap<>();
        for (Map.Entry<String, double[]> entry : bounds.entrySet()) {
            double[] b = entry.getValue();
            int[] ids = createBoundaryBox(model, entry.getKey(), b[0], b[1], b[2], b[3]);
            if (ids.length == 0) {
                throw new IllegalStateException("substrate surface did not map to any boundary: " +
                    entry.getKey() + " -> " + Arrays.toString(ids));
            }
            selected.put(entry.getKey(), ids);
        }
        assertWettingCoverage(model, selected, caseId);
        return selected;
    }

    private static void assertWettingCoverage(Model model, Map<String, int[]> selections, String caseId) {
        Map<String, double[][]> physicalSegments = new LinkedHashMap<>();
        if ("flat".equals(caseId)) {
            physicalSegments.put("sel_wet_flat_base", new double[][]{
                {0.0, R_DROP, 0.0, 0.0}, {R_DROP, R_BOX, 0.0, 0.0}});
        } else {
            physicalSegments.put("sel_wet_mesa_top", new double[][]{{0.0, R_MESA, H_MESA, H_MESA}});
            physicalSegments.put("sel_wet_mesa_side", new double[][]{{R_MESA, R_MESA, 0.0, H_MESA}});
            physicalSegments.put("sel_wet_lower_base", new double[][]{
                {R_MESA, R_DROP, 0.0, 0.0}, {R_DROP, R_BOX, 0.0, 0.0}});
        }
        int probeIndex = 0;
        for (Map.Entry<String, double[][]> entry : physicalSegments.entrySet()) {
            int[] allowedIds = selections.get(entry.getKey());
            for (double[] segment : entry.getValue()) {
                double dr = segment[1] - segment[0];
                double dz = segment[3] - segment[2];
                double length = Math.hypot(dr, dz);
                if (length <= 0.0) throw new IllegalStateException("empty physical substrate segment");
                for (int sample = 0; sample < 9; sample++) {
                    double fraction = (sample + 0.5) / 9.0;
                    double r = segment[0] + fraction * dr;
                    double z = segment[2] + fraction * dz;
                    double halfWidth = Math.min(DOMAIN_PROBE_HALF_WIDTH, length / 40.0);
                    int[] pointProbe = createBoundaryBox(model, "sel_wet_probe_" + (++probeIndex),
                        r - halfWidth, r + halfWidth, z - halfWidth, z + halfWidth, "intersects");
                    if (pointProbe.length == 0 || !isSubset(pointProbe, allowedIds)) {
                        throw new IllegalStateException("substrate wetting selection has a gap or includes a nonmatching edge: " +
                            entry.getKey() + " at (r,z)=" + r + "," + z +
                            " probe=" + Arrays.toString(pointProbe) +
                            " selected=" + Arrays.toString(allowedIds));
                    }
                }
            }
        }
    }

    private static int[] createBoundaryBox(Model model, String tag, double rMin, double rMax,
                                            double zMin, double zMax) {
        return createBoundaryBox(model, tag, rMin, rMax, zMin, zMax, "inside");
    }

    private static int[] createBoundaryBox(Model model, String tag, double rMin, double rMax,
                                            double zMin, double zMax, String condition) {
        model.component(COMPONENT).selection().create(tag, "Box");
        model.component(COMPONENT).selection(tag).set("entitydim", 1);
        model.component(COMPONENT).selection(tag).set("xmin", rMin);
        model.component(COMPONENT).selection(tag).set("xmax", rMax);
        model.component(COMPONENT).selection(tag).set("ymin", zMin);
        model.component(COMPONENT).selection(tag).set("ymax", zMax);
        // Strict containment selects the aggregate physical surface; local
        // intersects probes below verify that every expected segment is covered.
        model.component(COMPONENT).selection(tag).set("condition", condition);
        return model.component(COMPONENT).selection(tag).entities(1);
    }

    private static void assertDisjointAndExact(Map<String, int[]> selections, int expectedCount) {
        if (selections.size() != expectedCount) {
            throw new IllegalStateException("unexpected substrate wetting surface count");
        }
        Set<Integer> ids = new HashSet<>();
        for (Map.Entry<String, int[]> entry : selections.entrySet()) {
            if (entry.getValue().length == 0) {
                throw new IllegalStateException("physical wetting surface is empty: " + entry.getKey());
            }
            for (int boundaryId : entry.getValue()) {
                if (!ids.add(boundaryId)) {
                    throw new IllegalStateException("two physical substrate surfaces overlap at boundary " + boundaryId);
                }
            }
        }
        if (selections.size() != expectedCount) throw new IllegalStateException("wetting surface count mismatch");
    }

    private static boolean isSubset(int[] candidate, int[] allowed) {
        Set<Integer> allowedSet = new HashSet<>();
        for (int value : allowed) allowedSet.add(value);
        for (int value : candidate) if (!allowedSet.contains(value)) return false;
        return true;
    }

    private static Map<String, Object> addMaterials(Model model, int[] expectedDomainIds) {
        Map<String, Object> parameterReadback = readbackMaterialParameters(model);
        Material glue = model.material().create("matGlue", "Common");
        glue.materialType("NON_SOLID");
        glue.propertyGroup("def").set("density", "rhoGlue");
        glue.propertyGroup("def").set("dynamicviscosity", "muGlue");

        Material gas = model.material().create("matGas", "Common");
        gas.materialType("NON_SOLID");
        gas.propertyGroup("def").set("density", "rhoGas");
        gas.propertyGroup("def").set("dynamicviscosity", "muGas");

        Material multiphase = model.component(COMPONENT).material().create("mpmat1", "Multiphase");
        multiphase.selection().geom(GEOMETRY, 2);
        multiphase.selection().all();
        multiphase.set("vfDefinition", "pf");
        if (!contains(multiphase.feature().tags(), "phase1")) {
            multiphase.feature().create("phase1", "PhaseLink", COMPONENT);
        }
        if (!contains(multiphase.feature().tags(), "phase2")) {
            multiphase.feature().create("phase2", "PhaseLink", COMPONENT);
        }
        multiphase.feature("phase1").set("link", "matGlue");
        multiphase.feature("phase2").set("link", "matGas");
        if (!"rhoGlue".equals(glue.propertyGroup("def").getString("density")) ||
            !"muGlue".equals(glue.propertyGroup("def").getString("dynamicviscosity")) ||
            !"rhoGas".equals(gas.propertyGroup("def").getString("density")) ||
            !"muGas".equals(gas.propertyGroup("def").getString("dynamicviscosity")) ||
            !"matGlue".equals(multiphase.feature("phase1").getString("link")) ||
            !"matGas".equals(multiphase.feature("phase2").getString("link"))) {
            throw new IllegalStateException("two-fluid material expressions or phase links failed readback");
        }
        int[] materialDomainIds = multiphase.selection().entities(2);
        if (!sameIds(expectedDomainIds, materialDomainIds)) {
            throw new IllegalStateException("multiphase material must cover both selected fluid domains");
        }

        Map<String, Object> readback = new LinkedHashMap<>();
        readback.put("parameter_values_and_units", parameterReadback);
        readback.put("glue_material_type", glue.materialType());
        readback.put("glue_density_expression", glue.propertyGroup("def").getString("density"));
        readback.put("glue_dynamic_viscosity_expression", glue.propertyGroup("def").getString("dynamicviscosity"));
        readback.put("gas_material_type", gas.materialType());
        readback.put("gas_density_expression", gas.propertyGroup("def").getString("density"));
        readback.put("gas_dynamic_viscosity_expression", gas.propertyGroup("def").getString("dynamicviscosity"));
        readback.put("multiphase_type", multiphase.getType());
        readback.put("multiphase_volume_fraction_definition", multiphase.getString("vfDefinition"));
        readback.put("phase1_material_link", multiphase.feature("phase1").getString("link"));
        readback.put("phase2_material_link", multiphase.feature("phase2").getString("link"));
        readback.put("multiphase_domain_ids", boxed(materialDomainIds));
        return readback;
    }

    private static Map<String, Object> readbackMaterialParameters(Model model) {
        Map<String, Object> readback = new LinkedHashMap<>();
        addParameterReadback(model, readback, "rhoGlue", RHO_GLUE, "kg/m^3");
        addParameterReadback(model, readback, "muGlue", MU_GLUE, "Pa*s");
        addParameterReadback(model, readback, "rhoGas", RHO_GAS, "kg/m^3");
        addParameterReadback(model, readback, "muGas", MU_GAS, "Pa*s");
        addParameterReadback(model, readback, "sigma0", SIGMA, "N/m");
        return readback;
    }

    private static void addParameterReadback(Model model, Map<String, Object> readback,
                                              String name, double expected, String expectedUnit) {
        String expression = model.param().get(name);
        double value = model.param().evaluate(name);
        String unit = model.param().evaluateUnit(name);
        String normalizedUnit = unit.replace(" ", "");
        if (!Double.isFinite(value) || Math.abs(value - expected) > Math.max(1e-14, Math.abs(expected) * 1e-12) ||
            !expectedUnit.equals(normalizedUnit)) {
            throw new IllegalStateException("parameter value/unit mismatch for " + name +
                ": expression=" + expression + ", value=" + value + ", unit=" + unit);
        }
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("expression", expression);
        row.put("value_si", value);
        row.put("unit", unit);
        readback.put(name, row);
    }

    private static Map<String, Object> addPhysics(Model model, String caseId,
            int[] glueDomainIds, int[] gasDomainIds, Map<String, int[]> wetSelections) {
        Physics spf = model.component(COMPONENT).physics().create("spf", "LaminarFlow", GEOMETRY);
        spf.selection().geom(GEOMETRY, 2);
        spf.selection().all();
        spf.prop("PhysicalModel").set("IncludeGravity", "off");
        spf.prop("PhysicalModel").set("Compressibility", "incompressible");
        Map<String, Object> pressureReference = addClosedCavityPressureReference(model, spf);
        if (!"incompressible".equals(spf.prop("PhysicalModel").getString("Compressibility"))) {
            throw new IllegalStateException("closed-cavity Laminar Flow must read back as incompressible");
        }

        Physics pf = model.component(COMPONENT).physics().create("pf", "PhaseFieldInFluids", GEOMETRY);
        pf.selection().geom(GEOMETRY, 2);
        pf.selection().all();
        PhysicsFeature phaseFieldModel = pf.feature("pfm1");
        phaseFieldModel.set("epsilon_pf", "epsPF");
        phaseFieldModel.set("chi", "chiPF");

        PhysicsFeature initGlue = pf.feature("init1");
        initGlue.set("InitialValuesOption", "SpecifyPhase");
        initGlue.set("FluidInDomain", "Fluid1phipf");
        initGlue.selection().named("sel_glue_domain");
        PhysicsFeature initGas = pf.feature("initfluid2");
        initGas.set("InitialValuesOption", "SpecifyPhase");
        initGas.set("FluidInDomain", "Fluid2phipf");
        initGas.selection().named("sel_gas_domain");
        if (!sameIds(glueDomainIds, initGlue.selection().entities(2)) ||
            !sameIds(gasDomainIds, initGas.selection().entities(2))) {
            throw new IllegalStateException("Phase Field fluid initial-value domain mapping mismatch");
        }

        String[] existingWettedWallTags = wettedWallTags(pf);
        List<String> requestedSurfaceTags = new ArrayList<>(wetSelections.keySet());
        if (existingWettedWallTags.length > requestedSurfaceTags.size()) {
            throw new IllegalStateException("unexpected extra default Wetted Wall features in fresh fixture");
        }
        Map<String, Object> wettingReadback = new LinkedHashMap<>();
        int index = 0;
        for (Map.Entry<String, int[]> entry : wetSelections.entrySet()) {
            String tag;
            if (index < existingWettedWallTags.length) {
                tag = existingWettedWallTags[index];
            } else {
                tag = "wwshape" + (index + 1);
                pf.create(tag, "WettedWall", 1);
            }
            PhysicsFeature wetted = pf.feature(tag);
            if (!"WettedWall".equals(wetted.getType())) {
                throw new IllegalStateException("unexpected COMSOL feature type for " + tag + ": " + wetted.getType());
            }
            wetted.selection().named(entry.getKey());
            wetted.set("SpecifyContactAngle", "SpecifyContactAngleDirectly");
            wetted.set("thetaw", "thetaSubstrate");
            int[] observed = wetted.selection().entities(1);
            if (!sameIds(entry.getValue(), observed) ||
                !"SpecifyContactAngleDirectly".equals(wetted.getString("SpecifyContactAngle")) ||
                !"thetaSubstrate".equals(wetted.getString("thetaw"))) {
                throw new IllegalStateException("Wetted Wall readback mismatch for " + tag);
            }
            Map<String, Object> surface = new LinkedHashMap<>();
            surface.put("feature_tag", tag);
            surface.put("boundary_ids", boxed(observed));
            surface.put("contact_angle_expression", wetted.getString("thetaw"));
            wettingReadback.put(entry.getKey(), surface);
            index++;
        }

        MultiphysicsCoupling tpf = model.component(COMPONENT).multiphysics()
            .create("tpf1", "TwoPhaseFlowPhaseField", 2);
        tpf.set("Fluid_physics", "spf");
        tpf.set("Mathematics_physics", "pf");
        tpf.set("multiphaseMaterialList", "mpmat1");
        tpf.set("IncludeSurfaceTension", "on");
        tpf.set("SurfaceTensionCoefficient", "userdef");
        tpf.set("sigma", "sigma0");
        if (!"TwoPhaseFlowPhaseField".equals(tpf.getType()) ||
            !"spf".equals(tpf.getString("Fluid_physics")) ||
            !"pf".equals(tpf.getString("Mathematics_physics")) ||
            !"mpmat1".equals(tpf.getString("multiphaseMaterialList")) ||
            !"userdef".equals(tpf.getString("SurfaceTensionCoefficient")) ||
            !"sigma0".equals(tpf.getString("sigma"))) {
            throw new IllegalStateException("Two-Phase Flow, Phase Field coupling readback mismatch");
        }
        String includeSurfaceTension = tpf.getString("IncludeSurfaceTension");
        if (!("on".equalsIgnoreCase(includeSurfaceTension) || "1".equals(includeSurfaceTension) ||
              "true".equalsIgnoreCase(includeSurfaceTension))) {
            throw new IllegalStateException("surface-tension force must be enabled, got " + includeSurfaceTension);
        }

        Map<String, Object> readback = new LinkedHashMap<>();
        readback.put("laminar_flow_type", spf.getType());
        readback.put("laminar_flow_domain_ids", boxed(spf.selection().entities(2)));
        readback.put("gravity_readback", spf.prop("PhysicalModel").getString("IncludeGravity"));
        readback.put("compressibility_readback", spf.prop("PhysicalModel").getString("Compressibility"));
        readback.put("closed_cavity_pressure_reference", pressureReference);
        readback.put("phase_field_type", pf.getType());
        readback.put("phase_field_model_type", phaseFieldModel.getType());
        readback.put("phase_field_epsilon_expression", phaseFieldModel.getString("epsilon_pf"));
        readback.put("phase_field_chi_expression", phaseFieldModel.getString("chi"));
        readback.put("phase1_initial_values_selection", boxed(initGlue.selection().entities(2)));
        readback.put("phase1_initial_value", initGlue.getString("FluidInDomain"));
        readback.put("phase2_initial_values_selection", boxed(initGas.selection().entities(2)));
        readback.put("phase2_initial_value", initGas.getString("FluidInDomain"));
        readback.put("wetting_surfaces", wettingReadback);
        readback.put("multiphysics_type", tpf.getType());
        readback.put("coupling_fluid_physics", tpf.getString("Fluid_physics"));
        readback.put("coupling_phase_field_physics", tpf.getString("Mathematics_physics"));
        readback.put("coupling_multiphase_material", tpf.getString("multiphaseMaterialList"));
        readback.put("surface_tension_force", includeSurfaceTension);
        readback.put("surface_tension_mode", tpf.getString("SurfaceTensionCoefficient"));
        readback.put("surface_tension_expression", tpf.getString("sigma"));
        readback.put("case_id", caseId);
        return readback;
    }

    private static Map<String, Object> addClosedCavityPressureReference(Model model, Physics spf) {
        String selectionTag = "sel_pressure_reference";
        model.component(COMPONENT).selection().create(selectionTag, "Box");
        model.component(COMPONENT).selection(selectionTag).set("entitydim", 0);
        model.component(COMPONENT).selection(selectionTag).set("xmin", R_BOX - BOUNDARY_TOL);
        model.component(COMPONENT).selection(selectionTag).set("xmax", R_BOX + BOUNDARY_TOL);
        model.component(COMPONENT).selection(selectionTag).set("ymin", H_BOX - BOUNDARY_TOL);
        model.component(COMPONENT).selection(selectionTag).set("ymax", H_BOX + BOUNDARY_TOL);
        model.component(COMPONENT).selection(selectionTag).set("condition", "intersects");
        int[] pointIds = model.component(COMPONENT).selection(selectionTag).entities(0);
        if (pointIds.length != 1) {
            throw new IllegalStateException("closed-cavity pressure reference must resolve to the single outer-corner point");
        }
        String featureTag = "pressureReference";
        if (contains(spf.feature().tags(), featureTag)) {
            throw new IllegalStateException("unexpected existing pressure-reference feature in fresh Laminar Flow");
        }
        PhysicsFeature reference = spf.create(featureTag, "PressurePointConstraint", 0);
        reference.selection().named(selectionTag);
        reference.set("p0", "0[Pa]");
        int[] readbackIds = reference.selection().entities(0);
        if (!"PressurePointConstraint".equals(reference.getType()) ||
            !"0[Pa]".equals(reference.getString("p0")) ||
            !sameIds(pointIds, readbackIds)) {
            throw new IllegalStateException("closed-cavity pressure point constraint failed property/selection readback");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("feature_tag", featureTag);
        result.put("feature_type", reference.getType());
        result.put("selection_tag", selectionTag);
        result.put("point_ids", boxed(readbackIds));
        result.put("reference_pressure_expression", reference.getString("p0"));
        return result;
    }

    private static String[] wettedWallTags(Physics pf) {
        List<String> tags = new ArrayList<>();
        for (String tag : pf.feature().tags()) {
            if ("WettedWall".equals(pf.feature(tag).getType())) tags.add(tag);
        }
        Collections.sort(tags);
        return tags.toArray(new String[0]);
    }

    private static void addShapeMetricDefinitions(Model model) {
        model.component(COMPONENT).cpl().create("ivol", "Integration");
        model.component(COMPONENT).cpl("ivol").selection().geom(GEOMETRY, 2);
        model.component(COMPONENT).cpl("ivol").selection().all();
        model.component(COMPONENT).cpl().create("maxspeed", "Maximum");
        model.component(COMPONENT).cpl("maxspeed").selection().geom(GEOMETRY, 2);
        model.component(COMPONENT).cpl("maxspeed").selection().all();

        model.component(COMPONENT).variable().create("shapeMetrics");
        model.component(COMPONENT).variable("shapeMetrics").set("shape_phi", "pf.phipf");
        model.component(COMPONENT).variable("shapeMetrics").set("phase1_volume",
            "ivol((1-pf.phipf)/2)");
        model.component(COMPONENT).variable("shapeMetrics").set("phase2_volume",
            "ivol((1+pf.phipf)/2)");
        model.component(COMPONENT).variable("shapeMetrics").set("phase1_mass",
            "ivol(rhoGlue*(1-pf.phipf)/2)");
        model.component(COMPONENT).variable("shapeMetrics").set("phasefield_bulk_free_energy",
            "ivol(lambdaPF/2*(d(pf.phipf,r)^2+d(pf.phipf,z)^2)+" +
            "lambdaPF/(4*epsPF^2)*(pf.phipf^2-1)^2)");
        model.component(COMPONENT).variable("shapeMetrics").set("kinetic_energy",
            "ivol(0.5*(rhoGlue*(1-pf.phipf)/2+rhoGas*(1+pf.phipf)/2)*" +
            "(spf.u^2+spf.w^2))");
        model.component(COMPONENT).variable("shapeMetrics").set("maximum_speed",
            "maxspeed(sqrt(spf.u^2+spf.w^2))");
    }

    private static Map<String, Object> addMesh(Model model, Configuration configuration) {
        MeshSequence mesh = model.component(COMPONENT).mesh().create("mesh1", GEOMETRY);
        mesh.feature("size").set("custom", "on");
        mesh.feature("size").set("hmax", configuration.meshHmaxM);
        mesh.feature("size").set("hmin", configuration.meshHminM);
        mesh.run();
        double hmax = mesh.feature("size").getDouble("hmax");
        double hmin = mesh.feature("size").getDouble("hmin");
        if (!Double.isFinite(hmax) || !Double.isFinite(hmin) ||
            Math.abs(hmax - configuration.meshHmaxM) > Math.max(1e-15, configuration.meshHmaxM * 1e-12) ||
            Math.abs(hmin - configuration.meshHminM) > Math.max(1e-15, configuration.meshHminM * 1e-12) ||
            !"on".equalsIgnoreCase(mesh.feature("size").getString("custom"))) {
            throw new IllegalStateException("native sensitivity mesh size readback differs from the frozen configuration");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("mesh_tag", "mesh1");
        result.put("custom", true);
        result.put("hmax_m", hmax);
        result.put("hmin_m", hmin);
        result.put("interface_width_m", configuration.epsilonM);
        result.put("mesh_built", true);
        result.put("mesh_acceptance", "NOT_EVALUATED_AGAINST_SENSITIVITY_MATRIX");
        return result;
    }

    private static Map<String, Object> addUnsolvedStudy(Model model, Configuration configuration) {
        Study study = model.study().create("stdShape");
        study.create("phasei", "PhaseInitialization");
        study.create("time", "Transient");
        study.feature("time").set("initstudy", "stdShape");
        study.feature("time").set("useinitsol", "on");
        study.feature("time").set("usertol", "on");
        study.feature("time").set("rtol", "1e-4");
        study.feature("time").set("tlist", "range(0[s],tCapillary/2,20*tCapillary)");
        study.feature("time").set("tout", "tsteps");
        study.feature("time").set("tstepsbdf", "strict");
        study.feature("time").set("tstepsstore", "1");
        study.createAutoSequences("sol");
        String[] sequenceTags = study.getSolverSequences("SolverSequence");
        if (sequenceTags.length != 1) {
            throw new IllegalStateException("static-shape study must generate exactly one solver sequence");
        }
        SolverSequence sequence = model.sol(sequenceTags[0]);
        if (!sequence.isAttached() || !"stdShape".equals(sequence.study())) {
            throw new IllegalStateException("static-shape solver sequence is not attached to stdShape");
        }
        SolverFeature solverTime = findUniqueTimeFeature(sequence);
        configureTimeSolver(solverTime, configuration.maximumStepS);
        if (!Double.isFinite(solverTime.getDouble("maxstepbdf")) ||
            Math.abs(solverTime.getDouble("maxstepbdf") - configuration.maximumStepS) >
                Math.max(1e-15, configuration.maximumStepS * 1e-12) ||
            !"const".equals(solverTime.getString("maxstepconstraintbdf")) ||
            !"strict".equals(solverTime.getString("tstepsbdf")) ||
            !"tsteps".equals(solverTime.getString("tout")) ||
            solverTime.getInt("tstepsstore") != 1) {
            throw new IllegalStateException("static-shape BDF maximum-step/output settings failed readback");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("study_tag", "stdShape");
        result.put("configuration_id", configuration.id);
        result.put("study_step_order", Arrays.asList("phasei", "time"));
        result.put("phase_initialization_step_type", study.feature("phasei").getType());
        result.put("time_step_type", study.feature("time").getType());
        result.put("time_list_expression", study.feature("time").getString("tlist"));
        result.put("output_mode", study.feature("time").getString("tout"));
        result.put("bdf_output_time_policy", study.feature("time").getString("tstepsbdf"));
        result.put("stored_time_steps_policy", study.feature("time").getString("tstepsstore"));
        result.put("solver_sequences", Arrays.asList(sequenceTags));
        result.put("max_step_s", solverTime.getDouble("maxstepbdf"));
        result.put("max_step_constraint", solverTime.getString("maxstepconstraintbdf"));
        result.put("solver_tstepsbdf", solverTime.getString("tstepsbdf"));
        result.put("solver_tout", solverTime.getString("tout"));
        result.put("solver_tstepsstore", solverTime.getInt("tstepsstore"));
        result.put("study_run_calls", 0);
        result.put("phase_initialization_executed", false);
        return result;
    }

    private static Configuration configuration(String id) {
        switch (id) {
            case "baseline":
                return new Configuration(id, 8e-6, 4e-6, 2e-6, 0.10 * (MU_GLUE * R_DROP / SIGMA));
            case "mesh_ratio_1_3":
                return new Configuration(id, 8e-6, 8e-6 / 3.0, 2e-6,
                    0.10 * (MU_GLUE * R_DROP / SIGMA));
            case "mesh_ratio_1_1":
                return new Configuration(id, 8e-6, 8e-6, 2e-6,
                    0.10 * (MU_GLUE * R_DROP / SIGMA));
            case "epsilon_6um":
                return new Configuration(id, 6e-6, 3e-6, 1.5e-6,
                    0.10 * (MU_GLUE * R_DROP / SIGMA));
            case "epsilon_10um":
                return new Configuration(id, 10e-6, 5e-6, 2.5e-6,
                    0.10 * (MU_GLUE * R_DROP / SIGMA));
            case "step_0_05Tc":
                return new Configuration(id, 8e-6, 4e-6, 2e-6,
                    0.05 * (MU_GLUE * R_DROP / SIGMA));
            case "step_0_20Tc":
                return new Configuration(id, 8e-6, 4e-6, 2e-6,
                    0.20 * (MU_GLUE * R_DROP / SIGMA));
            default:
                throw new IllegalArgumentException("configuration_id must be one of the seven preregistered W24 ids");
        }
    }

    private static final class Configuration {
        final String id;
        final double epsilonM;
        final double meshHmaxM;
        final double meshHminM;
        final double maximumStepS;

        Configuration(String id, double epsilonM, double meshHmaxM, double meshHminM,
                      double maximumStepS) {
            this.id = id;
            this.epsilonM = epsilonM;
            this.meshHmaxM = meshHmaxM;
            this.meshHminM = meshHminM;
            this.maximumStepS = maximumStepS;
        }
    }

    private static void configureTimeSolver(SolverFeature time, double maxStep) {
        time.set("timemethod", "bdf");
        time.set("tunit", "s");
        time.set("tstepsbdf", "strict");
        time.set("maxstepconstraintbdf", "const");
        time.set("maxstepbdf", maxStep);
        time.set("tout", "tsteps");
        time.set("tstepsstore", 1);
    }

    private static SolverFeature findUniqueTimeFeature(SolverSequence sequence) {
        Map<String, SolverFeature> matches = new LinkedHashMap<>();
        collectTimeFeatures(sequence.feature().tags(), sequence.feature(), "", matches);
        if (matches.size() != 1) {
            throw new IllegalStateException("expected one Time solver feature; found " + matches.keySet());
        }
        return matches.values().iterator().next();
    }

    private static void collectTimeFeatures(String[] tags, SolverFeatureList list, String parent,
                                            Map<String, SolverFeature> matches) {
        for (String tag : tags) {
            SolverFeature feature = list.get(tag);
            String path = parent.isEmpty() ? tag : parent + "/" + tag;
            if ("Time".equals(feature.getType())) matches.put(path, feature);
            String[] childTags = feature.feature().tags();
            if (childTags.length > 0) collectTimeFeatures(childTags, feature.feature(), path, matches);
        }
    }

    private static Map<String, Object> metricDefinitions() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("volume_fraction_phase1", "(1-pf.phipf)/2");
        result.put("axisymmetric_measure", "2*pi*r*dr*dz; COMSOL axisymmetric Integration coupling applies radial measure");
        result.put("phase1_volume", "ivol((1-pf.phipf)/2)");
        result.put("phase1_mass", "ivol(rhoGlue*(1-pf.phipf)/2)");
        result.put("bulk_phasefield_energy", "integral(lambda/2*|grad(phi)|^2+lambda/(4*epsilon^2)*(phi^2-1)^2)dV; diagnostic only, not total energy");
        result.put("total_free_energy", "NOT_COMPUTED: direct contact-angle boundary input has no explicit wall surface-energy density readback");
        result.put("lambda_from_sigma_epsilon", "3*sigma*epsilon/(2*sqrt(2))");
        result.put("kinetic_energy", "ivol(0.5*rho(phi)*(spf.u^2+spf.w^2))");
        result.put("contact_line_source", "zero-level-set crossings on the named solid substrate boundaries; retain native field for extraction");
        result.put("normalization_floor", "fixed denominator sigma*pi*Rdrop^2 only for reported bulk-energy diagnostic; weighted native L2 for phi");
        result.put("convergence_window", "last 4 capillary times; volume drift<1e-3; adjacent upper-interface displacement<0.25*epsilon; contact-line motion<0.25*epsilon; max speed<1e-3*sigma/muGlue; bulk-energy diagnostic is not a gate");
        result.put("status", "PROPOSED_ACCEPTANCE_RULES_NOT_SOLVED");
        return result;
    }

    private static int[] sortedUnion(int[] first, int[] second) {
        int[] combined = new int[first.length + second.length];
        System.arraycopy(first, 0, combined, 0, first.length);
        System.arraycopy(second, 0, combined, first.length, second.length);
        Arrays.sort(combined);
        int uniqueCount = 0;
        for (int id : combined) {
            if (uniqueCount == 0 || combined[uniqueCount - 1] != id) combined[uniqueCount++] = id;
        }
        return Arrays.copyOf(combined, uniqueCount);
    }

    private static boolean sameIds(int[] expected, int[] observed) {
        if (expected.length != observed.length) return false;
        int[] a = expected.clone();
        int[] b = observed.clone();
        Arrays.sort(a);
        Arrays.sort(b);
        return Arrays.equals(a, b);
    }

    private static boolean intersects(int[] first, int[] second) {
        for (int a : first) for (int b : second) if (a == b) return true;
        return false;
    }

    private static boolean contains(String[] values, String candidate) {
        for (String value : values) if (candidate.equals(value)) return true;
        return false;
    }

    private static List<Integer> boxed(int[] values) {
        List<Integer> result = new ArrayList<>();
        for (int value : values) result.add(value);
        return result;
    }

    private static String meters(double value) { return number(value) + "[m]"; }
    private static String number(double value) { return Double.toString(value); }
    private static double square(double value) { return value * value; }
}
