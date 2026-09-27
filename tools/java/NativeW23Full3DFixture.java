import com.comsol.model.Coordsys;
import com.comsol.model.GeomMeasure;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.NumericalFeature;
import com.comsol.model.ParameterEntity;
import com.comsol.model.PropFeature;
import com.comsol.model.SelectionFeature;
import com.comsol.model.SolutionInfo;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverSequence;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Owned 3-D full-vector W23 fiber/ball-lens recipe builder.
 *
 * The builder configures geometry, materials, a 3-D EWFD physics interface,
 * Numeric ports, BMA/frequency study features and mesh. It never calls a study
 * or solver. Angular cases rotate both output fiber solids around the registered
 * pivot, then configure and read back a local Numeric-port surface selection.
 */
public final class NativeW23Full3DFixture {
    private static final String FIXTURE_ID = "w23_full3d_fiber_ball_lens_vector_pml_v1";
    private static final String COMPONENT = "comp3d";
    private static final String GEOMETRY = "geom3d";
    private static final String BMA_PROBE_STUDY = "std3dBmaOutputProbe";
    private static final String BMA_PROBE_STEP = "bmaOutputProbe";
    private static final double PORT_SECTION_HALF_LENGTH_UM = 0.01;
    private static final double PORT_SELECTION_RADIAL_MARGIN_UM = 0.02;
    private static final String[] VECTOR_FIELDS = {
        "ewfd.Ex", "ewfd.Ey", "ewfd.Ez", "ewfd.Hx", "ewfd.Hy", "ewfd.Hz"
    };
    private static final String[] BMA_PAIR_FIELDS = {
        "ewfd.Ex", "ewfd.Ey", "ewfd.Ez", "ewfd.Hx", "ewfd.Hy", "ewfd.Hz",
        "ewfd.Emodex_2", "ewfd.Emodey_2", "ewfd.Emodez_2",
        "ewfd.Hmodex_2", "ewfd.Hmodey_2", "ewfd.Hmodez_2",
        "nx", "ny", "nz"
    };
    private static final String[] BMA_PAIR_E_FIELDS = {
        "ewfd.Ex", "ewfd.Ey", "ewfd.Ez",
        "ewfd.Emodex_2", "ewfd.Emodey_2", "ewfd.Emodez_2"
    };
    private static final String[] BMA_PAIR_H_FIELDS = {
        "ewfd.Hx", "ewfd.Hy", "ewfd.Hz",
        "ewfd.Hmodex_2", "ewfd.Hmodey_2", "ewfd.Hmodez_2"
    };
    private static final String[] BMA_PAIR_NORMAL_FIELDS = {"nx", "ny", "nz"};

    private NativeW23Full3DFixture() {}

    public static Object run(Model model, Map<String, Object> args) {
        if (model == null) throw new IllegalArgumentException("model is required");
        String phase = args == null ? "" : String.valueOf(args.get("phase"));
        if ("build".equals(phase)) return build(model, args);
        if ("apply_case".equals(phase)) return applyCase(model, args);
        if ("solution_inventory".equals(phase)) return solutionInventory(model);
        if ("prepare_bma_output_probe".equals(phase)) return prepareBmaOutputProbe(model, args);
        if ("run_bma_output_probe".equals(phase)) return runBmaOutputProbe(model, args);
        if ("bma_basis_fields".equals(phase)) return bmaBasisFields(model, args);
        if ("raw_fields".equals(phase)) return rawFields(model, args);
        if ("save".equals(phase)) return save(model, args);
        if ("identity".equals(phase)) return identity(model);
        throw new IllegalArgumentException("phase must be build, apply_case, solution_inventory, raw_fields, bma_basis_fields, save, or identity");
    }

    private static Map<String, Object> build(Model model, Map<String, Object> args) {
        Map<String, Object> managedIdentity = verifyManagedIdentity(model, args);
        Map<String, Object> meshLevel = requestedMeshLevel(args);
        String recipeSha = requiredSha256(args.get("recipe_sha256"), "recipe_sha256");
        if (!FIXTURE_ID.equals(args.get("fixture_id")))
            throw new IllegalArgumentException("registered full-3D fixture id differs");
        if (Arrays.asList(model.component().tags()).contains(COMPONENT))
            throw new IllegalStateException("refusing to replace an existing 3-D component");
        model.label("W23 owned full-3D vector fiber/lens mechanism fixture - not solved");
        model.param().set("lambda0", "1.55[um]");
        model.param().set("f0", "193.414489032258[THz]");
        model.param().set("w23Ncore", "1.47");
        model.param().set("w23Nclad", "1.44");
        model.param().set("w23Nlens", "1.52");
        model.param().set("w23CoreR", "1.2[um]");
        model.param().set("w23CladR", "2.5[um]");
        model.param().set("w23LensR", "4[um]");
        model.param().set("w23AirHalfY", "6[um]");
        model.param().set("w23AirHalfZ", "6[um]");
        model.param().set("w23PmlT", "2[um]");
        model.param().set("w23XIn", "-20[um]");
        model.param().set("w23XInEnd", "-5[um]");
        model.param().set("w23XOutStart", "5[um]");
        model.param().set("w23XOut", "20[um]");
        model.param().set("w23XDomainMax", "21[um]");
        model.param().set("w23OutDy", "0[um]");
        model.param().set("w23OutDz", "0[um]");
        model.param().set("w23ThetaY", "0[deg]");
        model.param().set("w23ThetaZ", "0[deg]");

        model.component().create(COMPONENT);
        GeomSequence geom = model.component(COMPONENT).geom().create(GEOMETRY, 3);
        geom.lengthUnit("um");

        block(geom, "outerBox", "w23XDomainMax-w23XIn", "2*(w23AirHalfY+w23PmlT)",
              "2*(w23AirHalfZ+w23PmlT)", "w23XIn", "-w23AirHalfY-w23PmlT",
              "-w23AirHalfZ-w23PmlT");
        block(geom, "innerBox", "w23XDomainMax-w23XIn", "2*w23AirHalfY", "2*w23AirHalfZ",
              "w23XIn", "-w23AirHalfY", "-w23AirHalfZ");

        cylinder(geom, "coreIn", "w23CoreR", "w23XInEnd-w23XIn", "w23XIn 0 0");
        cylinder(geom, "cladOuterIn", "w23CladR", "w23XInEnd-w23XIn", "w23XIn 0 0");
        difference(geom, "cladShellIn", "cladOuterIn", "coreIn", true);
        cylinder(geom, "coreOut", "w23CoreR", "w23XOut-w23XOutStart",
                 "w23XOutStart w23OutDy w23OutDz");
        cylinder(geom, "cladOuterOut", "w23CladR", "w23XOut-w23XOutStart",
                 "w23XOutStart w23OutDy w23OutDz");
        difference(geom, "cladShellOut", "cladOuterOut", "coreOut", true);
        rotate(geom, "rotCoreOutY", "coreOut", new String[]{"0", "1", "0"}, "w23ThetaY");
        rotate(geom, "rotCoreOutZ", "rotCoreOutY", new String[]{"0", "0", "1"}, "w23ThetaZ");
        rotate(geom, "rotCladShellOutY", "cladShellOut", new String[]{"0", "1", "0"}, "w23ThetaY");
        rotate(geom, "rotCladShellOutZ", "rotCladShellOutY", new String[]{"0", "0", "1"}, "w23ThetaZ");
        sphere(geom, "lensBall", "w23LensR", "0 0 0");

        geom.create("solidOptics", "Union");
        geom.feature("solidOptics").selection("input").set(
                new String[]{"coreIn", "cladShellIn", "rotCoreOutZ", "rotCladShellOutZ", "lensBall"});
        geom.feature("solidOptics").set("intbnd", "on");
        geom.feature("solidOptics").set("keep", "on");
        resultSelection(geom, "solidOptics");

        geom.create("airRemainder", "Difference");
        geom.feature("airRemainder").selection("input").set(new String[]{"innerBox"});
        geom.feature("airRemainder").selection("input2").set(new String[]{"solidOptics"});
        geom.feature("airRemainder").set("keepsubtract", "on");
        resultSelection(geom, "airRemainder");

        geom.create("pmlShell", "Difference");
        geom.feature("pmlShell").selection("input").set(new String[]{"outerBox"});
        geom.feature("pmlShell").selection("input2").set(new String[]{"innerBox"});
        geom.feature("pmlShell").set("keepsubtract", "on");
        resultSelection(geom, "pmlShell");

        geom.create("allDomains", "Union");
        geom.feature("allDomains").selection("input").set(
                new String[]{"airRemainder", "solidOptics", "pmlShell"});
        geom.feature("allDomains").set("intbnd", "on");
        resultSelection(geom, "allDomains");
        geom.run();

        int[] coreIn = domainSelection(model, "geom3d_coreIn_dom");
        int[] coreOut = domainSelection(model, "geom3d_rotCoreOutZ_dom");
        int[] cladIn = domainSelection(model, "geom3d_cladShellIn_dom");
        int[] cladOut = domainSelection(model, "geom3d_rotCladShellOutZ_dom");
        int[] lens = domainSelection(model, "geom3d_lensBall_dom");
        int[] air = domainSelection(model, "geom3d_airRemainder_dom");
        int[] pmlDomains = domainSelection(model, "geom3d_pmlShell_dom");
        assertDisjoint(coreIn, coreOut, cladIn, cladOut, lens, air, pmlDomains);
        SelectionFeature deformationTarget = createDeformationTargetSelection(
                model, coreIn, coreOut, cladIn, cladOut, lens);

        SelectionFeature inputPortSelection = box(model, "sel3dInputPort", -20.001, -19.999,
                -8.001, 8.001, -8.001, 8.001);
        SelectionFeature outputPortSelection = localPortCylinder(model, "sel3dOutputPort",
                new double[]{1.0, 0.0, 0.0}, new double[]{20.0, 0.0, 0.0},
                2.5, PORT_SELECTION_RADIAL_MARGIN_UM);
        SelectionFeature captureSelection = localPortCylinder(model, "sel3dOutputCoreCapture",
                new double[]{1.0, 0.0, 0.0}, new double[]{20.0, 0.0, 0.0}, 1.2, 0.0);
        if (inputPortSelection.entities(2).length == 0 || outputPortSelection.entities(2).length == 0)
            throw new IllegalStateException("3-D input Box or local output-port section selection is empty");

        Coordsys pml = model.component(COMPONENT).coordSystem().create("pmlYZ", GEOMETRY, "PML");
        pml.selection().named("geom3d_pmlShell_dom");
        pml.set("ScalingType", "Cartesian");
        pml.set("stretchingType", "polynomial");
        pml.set("typicalWavelength", "lambda0");

        material(model, "matCore3d", "geom3d_coreIn_dom", "w23Ncore^2");
        material(model, "matCoreOut3d", "geom3d_rotCoreOutZ_dom", "w23Ncore^2");
        material(model, "matCladIn3d", "geom3d_cladShellIn_dom", "w23Nclad^2");
        material(model, "matCladOut3d", "geom3d_rotCladShellOutZ_dom", "w23Nclad^2");
        material(model, "matLens3d", "geom3d_lensBall_dom", "w23Nlens^2");
        material(model, "matAir3d", "geom3d_airRemainder_dom", "1");
        material(model, "matPmlAir3d", "geom3d_pmlShell_dom", "1");

        Physics ewfd = model.component(COMPONENT).physics().create("ewfd", "ElectromagneticWaves", GEOMETRY);
        ewfd.selection().all();
        // True 3-D EWFD uses the documented full three-component field by
        // dimension. Do not set the 2-D-only out-of-plane property here.
        PhysicsFeature portIn = ewfd.feature().create("portIn3d", "Port", 2);
        PhysicsFeature portOut = ewfd.feature().create("portOut3d", "Port", 2);
        portIn.selection().named("sel3dInputPort");
        portOut.selection().named("sel3dOutputPort");
        configureNumericPort(portIn, "1", true);
        configureNumericPort(portOut, "2", false);

        model.study().create("std3d");
        StudyFeature bmaInput = model.study("std3d").feature().create("bmaInput3d", "BoundaryModeAnalysis");
        StudyFeature bmaOutput = model.study("std3d").feature().create("bmaOutput3d", "BoundaryModeAnalysis");
        StudyFeature frequency = model.study("std3d").feature().create("freq3d", "Frequency");
        configureBma(bmaInput, "1");
        configureBma(bmaOutput, "2");
        frequency.set("plist", "f0");

        model.component(COMPONENT).mesh().create("mesh3d", GEOMETRY);
        PropFeature size = model.component(COMPONENT).mesh("mesh3d").feature("size");
        size.set("custom", "on");
        size.set("hmax", (String) meshLevel.get("hmax_expression"));
        size.set("hmin", (String) meshLevel.get("hmin_expression"));
        model.component(COMPONENT).mesh("mesh3d").run();

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("fixture_id", FIXTURE_ID);
        result.put("recipe_sha256", recipeSha);
        result.put("managed_identity", managedIdentity);
        result.put("status", "BUILT_CONFIGURED_NOT_SOLVED");
        result.put("native_result", "NOT_RUN");
        result.put("study_or_solver_invoked", false);
        result.put("geometry", Map.of("dimension", 3, "length_unit", geom.lengthUnit(),
                "domain_count", geom.getNDomains(), "bounding_box", geom.getBoundingBox(),
                "propagation_axis", "+x", "lens", "parameterized sphere",
                "fiber", "two core/cladding cylinder pairs", "background", "air remainder",
                "pml", "transverse outer Cartesian shell; native directional readback required"));
        result.put("domain_selections", Map.of(
                "core_input", intList(coreIn), "core_output", intList(coreOut),
                "cladding_input", intList(cladIn), "cladding_output", intList(cladOut),
                "lens", intList(lens), "air", intList(air), "pml", intList(pmlDomains)));
        result.put("deformation_target_selection", deformationTargetReadback(model, deformationTarget));
        result.put("port_selections", Map.of(
                "input", planeSelectionReadback(model, inputPortSelection, new double[]{1.0, 0.0, 0.0}),
                "output", portSectionReadback(model, outputPortSelection,
                        new double[]{1.0, 0.0, 0.0}, new double[]{20.0, 0.0, 0.0},
                        2.5, PORT_SELECTION_RADIAL_MARGIN_UM),
                "capture", portSectionReadback(model, captureSelection,
                        new double[]{1.0, 0.0, 0.0}, new double[]{20.0, 0.0, 0.0}, 1.2, 0.0)));
        result.put("ports", List.of(
                Map.of("tag", "portIn3d", "properties", propertyReadback(portIn,
                        new String[]{"PortType", "PortName", "PortExcitation", "PortModeNumber", "Pin", "Thetap", "PortOrientation"})),
                Map.of("tag", "portOut3d", "properties", propertyReadback(portOut,
                        new String[]{"PortType", "PortName", "PortExcitation", "PortModeNumber", "Thetap", "PortOrientation"}))));
        result.put("pml", propertyReadback(pml, new String[]{"ScalingType", "stretchingType", "typicalWavelength"}));
        result.put("study_steps", List.of(
                Map.of("tag", "bmaInput3d", "type", bmaInput.getType(), "port", bmaInput.getString("PortName"),
                       "modeFreq", bmaInput.getString("modeFreq"), "neigs", bmaInput.getInt("neigs")),
                Map.of("tag", "bmaOutput3d", "type", bmaOutput.getType(), "port", bmaOutput.getString("PortName"),
                       "modeFreq", bmaOutput.getString("modeFreq"), "neigs", bmaOutput.getInt("neigs")),
                Map.of("tag", "freq3d", "type", frequency.getType(), "plist", frequency.getString("plist"))));
        result.put("fields_expected", Arrays.asList(VECTOR_FIELDS));
        result.put("vector_field_contract", "3-D ElectromagneticWaves interface; full vector by space dimension; native variable/readback still required");
        result.put("mode_basis_contract", Map.of("dimension", 2,
                "basis_id", "fundamental_spatial_mode_two_polarization_subspace_v1",
                "tracking", "complex power-overlap matrix; mode index labels alone are insufficient"));
        result.put("angular_cases", Map.of(
                "status", "ROTATION_AND_LOCAL_PORT_SELECTION_CONFIGURED_NOT_SOLVED",
                "rotation_order", "right-handed +global y then +global z; shared output-core/cladding pivot",
                "port_plane", "local finite Cylinder selection follows the transformed output axis",
                "native_port_selection_readback", portSectionReadback(model, outputPortSelection,
                        new double[]{1.0, 0.0, 0.0}, new double[]{20.0, 0.0, 0.0},
                        2.5, PORT_SELECTION_RADIAL_MARGIN_UM)));
        Map<String, Object> meshReadback = new LinkedHashMap<>();
        meshReadback.put("tag", "mesh3d");
        meshReadback.put("geometry", GEOMETRY);
        Map<String, Object> sizeReadback = propertyReadback(size, new String[]{"custom", "hmax", "hmin"});
        meshReadback.put("size_properties", sizeReadback);
        @SuppressWarnings("unchecked") Map<String, Object> requestedSize =
                (Map<String, Object>) sizeReadback.get("requested_properties");
        @SuppressWarnings("unchecked") Map<String, Object> customReadback =
                (Map<String, Object>) requestedSize.get("custom");
        @SuppressWarnings("unchecked") Map<String, Object> hmaxReadback =
                (Map<String, Object>) requestedSize.get("hmax");
        @SuppressWarnings("unchecked") Map<String, Object> hminReadback =
                (Map<String, Object>) requestedSize.get("hmin");
        String observedCustom = exactPropertyString(customReadback, "mesh custom");
        String observedHmax = exactPropertyString(hmaxReadback, "mesh hmax");
        String observedHmin = exactPropertyString(hminReadback, "mesh hmin");
        if (!"on".equals(observedCustom)
                || !observedHmax.equals(meshLevel.get("hmax_expression"))
                || !observedHmin.equals(meshLevel.get("hmin_expression")))
            throw new IllegalStateException("native mesh-size property readback differs from the requested frozen level");
        meshReadback.put("hmax", observedHmax);
        meshReadback.put("hmin", observedHmin);
        meshReadback.put("elements", model.component(COMPONENT).mesh("mesh3d").getNumElem());
        if (meshLevel.get("mesh_level_id") instanceof String) {
            meshReadback.put("mesh_level_id", meshLevel.get("mesh_level_id"));
            meshReadback.put("mesh_scale_factor", meshLevel.get("mesh_scale_factor"));
        }
        result.put("mesh", meshReadback);
        return result;
    }

    private static Map<String, Object> requestedMeshLevel(Map<String, Object> args) {
        Object rawLevel = args.get("mesh_level_id");
        Object rawScale = args.get("mesh_scale_factor");
        if (rawLevel == null && rawScale == null) {
            return Map.of("hmax_expression", "lambda0/(5*w23Nlens)",
                    "hmin_expression", "lambda0/(12*w23Nlens)");
        }
        if (!(rawLevel instanceof String) || !(rawScale instanceof Number)
                || rawScale instanceof Boolean) {
            throw new IllegalArgumentException("mesh convergence identity requires exact level ID and numeric scale");
        }
        String level = (String) rawLevel;
        double scale = ((Number) rawScale).doubleValue();
        if (!Double.isFinite(scale))
            throw new IllegalArgumentException("mesh convergence scale must be finite");
        String scaleText;
        if ("mesh1".equals(level) && Double.compare(scale, 1.0) == 0) scaleText = null;
        else if ("mesh2".equals(level) && Double.compare(scale, 0.8) == 0) scaleText = "0.8";
        else if ("mesh3".equals(level) && Double.compare(scale, 0.64) == 0) scaleText = "0.64";
        else throw new IllegalArgumentException("mesh convergence level/scale is outside the frozen W23 sequence");
        String hmax = scaleText == null ? "lambda0/(5*w23Nlens)"
                : "(lambda0/(5*w23Nlens))*" + scaleText;
        String hmin = scaleText == null ? "lambda0/(12*w23Nlens)"
                : "(lambda0/(12*w23Nlens))*" + scaleText;
        return Map.of("mesh_level_id", level, "mesh_scale_factor", scale,
                "hmax_expression", hmax, "hmin_expression", hmin);
    }

    private static String exactPropertyString(Map<String, Object> row, String label) {
        Object value = row == null ? null : row.get("string_readback");
        if (row == null || !Boolean.TRUE.equals(row.get("has_property_exact"))
                || !(value instanceof String) || ((String) value).trim().isEmpty())
            throw new IllegalStateException(label + " lacks an exact native String property readback");
        return (String) value;
    }

    private static Map<String, Object> applyCase(Model model, Map<String, Object> args) {
        Map<String, Object> managedIdentity = verifyManagedIdentity(model, args);
        String recipeSha = requiredSha256(args.get("recipe_sha256"), "recipe_sha256");
        if (!FIXTURE_ID.equals(args.get("fixture_id")))
            throw new IllegalArgumentException("registered full-3D fixture id differs");
        if (!Arrays.asList(model.component().tags()).contains(COMPONENT))
            throw new IllegalStateException("the canonical full-3D fixture must be built first");
        Object raw = args == null ? null : args.get("case");
        if (!(raw instanceof Map)) throw new IllegalArgumentException("a managed immutable case record is required");
        @SuppressWarnings("unchecked") Map<String, Object> row = (Map<String, Object>) raw;
        String factor = String.valueOf(row.get("factor"));
        Object numeric = row.get("value");
        if (!(numeric instanceof Number) || numeric instanceof Boolean)
            throw new IllegalArgumentException("case factor value must be numeric");
        double value = ((Number) numeric).doubleValue();
        if (!Double.isFinite(value)) throw new IllegalArgumentException("case factor value must be finite");
        requireRegisteredCaseValue(factor, value);
        PortFrame frame = casePortFrame(factor, value);
        verifyReceiverFrame(row.get("receiver_transform"), frame);
        for (String key : new String[]{"project_id", "model_ref", "model_tag", "expected_revision"}) {
            if (!managedIdentity.get(key).equals(row.get(key)))
                throw new IllegalArgumentException("case row is not bound to the managed " + key);
        }
        if (!recipeSha.equals(row.get("recipe_sha256")))
            throw new IllegalArgumentException("case row belongs to another full-3D recipe");
        resetGeometryParameters(model);
        if (factor.equals("baseline")) {
        } else if (factor.equals("receiver_gap_x_um")) {
            model.param().set("w23XOutStart", "(5[um])+((" + value + ")[um])");
        } else if (factor.equals("receiver_dy_um")) {
            model.param().set("w23OutDy", "(" + value + ")[um]");
        } else if (factor.equals("receiver_dz_um")) {
            model.param().set("w23OutDz", "(" + value + ")[um]");
        } else if (factor.equals("receiver_theta_y_deg")) {
            model.param().set("w23ThetaY", Double.toString(value) + "[deg]");
        } else if (factor.equals("receiver_theta_z_deg")) {
            model.param().set("w23ThetaZ", Double.toString(value) + "[deg]");
        } else if (factor.equals("core_radius_relative")) {
            model.param().set("w23CoreR", "1.2[um]*(1+(" + value + "))");
        } else if (factor.equals("cladding_radius_relative")) {
            model.param().set("w23CladR", "2.5[um]*(1+(" + value + "))");
        } else if (factor.equals("lens_radius_relative")) {
            model.param().set("w23LensR", "4[um]*(1+(" + value + "))");
        } else if (factor.equals("lens_index_delta")) {
            model.param().set("w23Nlens", "1.52+(" + value + ")");
        } else {
            throw new IllegalArgumentException("unregistered full-3D case factor: " + factor);
        }
        model.component(COMPONENT).geom(GEOMETRY).run();
        SelectionFeature deformationTarget = refreshDeformationTargetSelection(model);
        SelectionFeature portSelection = model.component(COMPONENT).selection("sel3dOutputPort");
        configureLocalPortCylinder(portSelection, frame.axis, frame.center, frame.claddingRadius,
                PORT_SELECTION_RADIAL_MARGIN_UM);
        SelectionFeature captureSelection = model.component(COMPONENT).selection("sel3dOutputCoreCapture");
        configureLocalPortCylinder(captureSelection, frame.axis, frame.center, frame.coreRadius, 0.0);
        model.component(COMPONENT).mesh("mesh3d").run();
        Map<String, Object> result = new LinkedHashMap<>();
        for (String key : new String[]{"project_id", "model_ref", "model_tag", "experiment_id",
                                       "expected_revision", "case_id", "case_identity_sha256", "recipe_sha256"}) {
            if (!row.containsKey(key)) throw new IllegalArgumentException("case lacks immutable " + key);
            result.put(key, row.get(key));
        }
        result.put("factor", factor);
        result.put("factor_value", value);
        result.put("fixture_id", FIXTURE_ID);
        result.put("managed_identity", managedIdentity);
        result.put("status", "GEOMETRY_CASE_CONFIGURED_NOT_SOLVED");
        result.put("native_result", "NOT_RUN");
        result.put("study_or_solver_invoked", false);
        result.put("geometry", Map.of("dimension", 3, "domain_count", model.component(COMPONENT).geom(GEOMETRY).getNDomains(),
                "input_port_entities", intList(model.component(COMPONENT).selection("sel3dInputPort").entities(2)),
                "output_port_entities", intList(model.component(COMPONENT).selection("sel3dOutputPort").entities(2))));
        result.put("receiver_port_section", portSectionReadback(model, portSelection,
                frame.axis, frame.center, frame.claddingRadius, PORT_SELECTION_RADIAL_MARGIN_UM));
        result.put("receiver_core_capture_section", portSectionReadback(model, captureSelection,
                frame.axis, frame.center, frame.coreRadius, 0.0));
        result.put("deformation_target_selection", deformationTargetReadback(model, deformationTarget));
        result.put("receiver_transform", Map.of("rotation_order", "right-handed global +y then +z",
                "axis_xyz", boxed(frame.axis), "center_xyz_um", boxed(frame.center),
                "theta_y_deg", model.param().get("w23ThetaY"), "theta_z_deg", model.param().get("w23ThetaZ")));
        result.put("geometry_parameters", Map.of(
                "core_radius", model.param().get("w23CoreR"),
                "cladding_radius", model.param().get("w23CladR"),
                "lens_radius", model.param().get("w23LensR"),
                "lens_index", model.param().get("w23Nlens"),
                "receiver_translation_y", model.param().get("w23OutDy"),
                "receiver_translation_z", model.param().get("w23OutDz"),
                "receiver_start_x", model.param().get("w23XOutStart"),
                "receiver_theta_y", model.param().get("w23ThetaY"),
                "receiver_theta_z", model.param().get("w23ThetaZ")));
        return result;
    }

    private static Map<String, Object> verifyManagedIdentity(Model model, Map<String, Object> args) {
        if (args == null || !Boolean.FALSE.equals(args.get("study_or_solver_invoked"))
                || !"NOT_RUN".equals(args.get("native_result")))
            throw new IllegalArgumentException("full-3D fixture entrypoint is configuration-only and must remain NOT_RUN");
        Object raw = args.get("managed_identity");
        if (!(raw instanceof Map)) throw new IllegalArgumentException("managed project/model identity is required");
        @SuppressWarnings("unchecked") Map<String, Object> identity = (Map<String, Object>) raw;
        String projectId = nonemptyString(identity.get("project_id"), "project_id");
        Object modelRef = identity.get("model_ref");
        if (!(modelRef instanceof Map) || ((Map<?, ?>) modelRef).isEmpty())
            throw new IllegalArgumentException("persisted managed ModelRef is required");
        String modelTag = nonemptyString(identity.get("model_tag"), "model_tag");
        if (!modelTag.equals(model.tag())) throw new IllegalStateException("managed model tag differs from native Model");
        Object revision = identity.get("expected_revision");
        if (!(revision instanceof Number) || revision instanceof Boolean
                || ((Number) revision).longValue() < 0
                || ((Number) revision).doubleValue() != ((Number) revision).longValue())
            throw new IllegalArgumentException("managed expected revision must be a nonnegative integer");
        Map<String, Object> bound = new LinkedHashMap<>();
        bound.put("project_id", projectId);
        Map<String, Object> modelRefCopy = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : ((Map<?, ?>) modelRef).entrySet()) {
            if (!(entry.getKey() instanceof String))
                throw new IllegalArgumentException("managed ModelRef keys must be strings");
            modelRefCopy.put((String) entry.getKey(), entry.getValue());
        }
        bound.put("model_ref", modelRefCopy);
        bound.put("model_tag", modelTag);
        bound.put("expected_revision", ((Number) revision).longValue());
        return bound;
    }

    private static void requireRegisteredCaseValue(String factor, double value) {
        double[] registered;
        switch (factor) {
            case "baseline": registered = new double[]{0.0}; break;
            case "receiver_gap_x_um":
            case "receiver_dy_um":
            case "receiver_dz_um": registered = new double[]{-0.25, 0.25}; break;
            case "receiver_theta_y_deg":
            case "receiver_theta_z_deg": registered = new double[]{-0.5, 0.5}; break;
            case "core_radius_relative":
            case "cladding_radius_relative":
            case "lens_radius_relative": registered = new double[]{-0.02, 0.02}; break;
            case "lens_index_delta": registered = new double[]{-0.005, 0.005}; break;
            default: throw new IllegalArgumentException("unregistered full-3D case factor: " + factor);
        }
        for (double allowed : registered)
            if (Math.abs(value - allowed) <= 1e-12) return;
        throw new IllegalArgumentException("case value is outside the frozen full-3D one-factor matrix");
    }

    private static String nonemptyString(Object raw, String label) {
        if (!(raw instanceof String) || ((String) raw).trim().isEmpty())
            throw new IllegalArgumentException(label + " must be a nonempty string");
        return (String) raw;
    }

    private static String requiredSha256(Object raw, String label) {
        if (!(raw instanceof String) || !((String) raw).matches("^[0-9a-f]{64}$"))
            throw new IllegalArgumentException(label + " must be a lowercase SHA-256 digest");
        return (String) raw;
    }

    private static void resetGeometryParameters(Model model) {
        model.param().set("w23CoreR", "1.2[um]");
        model.param().set("w23CladR", "2.5[um]");
        model.param().set("w23LensR", "4[um]");
        model.param().set("w23Nlens", "1.52");
        model.param().set("w23OutDy", "0[um]");
        model.param().set("w23OutDz", "0[um]");
        model.param().set("w23XOutStart", "5[um]");
        model.param().set("w23XDomainMax", "21[um]");
        model.param().set("w23ThetaY", "0[deg]");
        model.param().set("w23ThetaZ", "0[deg]");
    }

    private static Map<String, Object> identity(Model model) {
        if (!Arrays.asList(model.component().tags()).contains(COMPONENT))
            throw new IllegalStateException("full-3D fixture component is absent");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("fixture_id", FIXTURE_ID);
        result.put("model_tag", model.tag());
        result.put("component", COMPONENT);
        result.put("geometry", GEOMETRY);
        result.put("dimension", model.component(COMPONENT).geom(GEOMETRY).getSDim());
        result.put("length_unit", model.component(COMPONENT).geom(GEOMETRY).lengthUnit());
        result.put("domain_count", model.component(COMPONENT).geom(GEOMETRY).getNDomains());
        result.put("solver_tags", Arrays.asList(model.sol().tags()));
        result.put("study_tags", Arrays.asList(model.study().tags()));
        result.put("dataset_tags", Arrays.asList(model.result().dataset().tags()));
        result.put("native_result", "COMSOL_NATIVE_MODEL_IDENTITY_READBACK");
        result.put("solve_history", "NOT_REPORTED_BY_IDENTITY_PHASE");
        return result;
    }

    /** Read configured study/solver/dataset identity without running a solver. */
    private static Map<String, Object> solutionInventory(Model model) {
        if (!Arrays.asList(model.study().tags()).contains("std3d"))
            throw new IllegalStateException("the owned std3d study is absent");
        Map<String, Object> result = new LinkedHashMap<>();
        List<Map<String, Object>> studySteps = new ArrayList<>();
        for (String tag : model.study("std3d").feature().tags()) {
            StudyFeature feature = model.study("std3d").feature(tag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tag);
            row.put("feature_type", feature.getType());
            for (String property : new String[]{"PortName", "modeFreq", "plist"}) {
                if (feature.hasProperty(property)) row.put(property, feature.getString(property));
            }
            if (feature.hasProperty("neigs")) row.put("neigs", feature.getInt("neigs"));
            studySteps.add(row);
        }
        List<Map<String, Object>> solverSequences = new ArrayList<>();
        for (String tag : model.study("std3d").getSolverSequences("SolverSequence")) {
            SolverSequence sequence = model.sol(tag);
            Map<String, Object> sequenceRow = new LinkedHashMap<>();
            sequenceRow.put("tag", tag);
            sequenceRow.put("feature_type", sequence.getType());
            List<Map<String, Object>> tree = new ArrayList<>();
            List<Map<String, Object>> bindings = new ArrayList<>();
            for (String featureTag : sequence.feature().tags())
                collectSolverFeature(sequence.feature(featureTag), featureTag, tree, bindings);
            sequenceRow.put("solver_tree_features", tree);
            sequenceRow.put("study_step_bindings_in_solver_tree_order", bindings);
            solverSequences.add(sequenceRow);
        }
        List<Map<String, Object>> datasets = new ArrayList<>();
        for (String tag : model.result().dataset().tags()) {
            PropFeature dataset = model.result().dataset(tag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tag);
            row.put("feature_type", dataset.getType());
            for (String property : new String[]{"solution", "data", "solnum", "outersolnum"}) {
                if (dataset.hasProperty(property)) row.put(property, dataset.getString(property));
            }
            datasets.add(row);
        }
        result.put("fixture_id", FIXTURE_ID);
        result.put("model_tag", model.tag());
        result.put("study_steps_in_configured_order", studySteps);
        result.put("solver_sequences_for_study", solverSequences);
        result.put("solver_sequence_query_type", "SolverSequence");
        result.put("datasets", datasets);
        result.put("solver_execution_count", "UNKNOWN_WITHOUT_RUNTIME_SOLVER_EVIDENCE");
        result.put("native_result", "COMSOL_NATIVE_MODEL_SOURCE_INVENTORY");
        return result;
    }

    /**
     * Create an isolated one-step output-Port BMA study and generate its full
     * default solver sequence. This phase configures only; it never executes a
     * Study or SolverSequence.
     */
    private static Map<String, Object> prepareBmaOutputProbe(Model model, Map<String, Object> args) {
        Map<String, Object> managedIdentity = readManagedIdentity(model, args);
        if (!Arrays.asList(model.component().tags()).contains(COMPONENT)
                || !Arrays.asList(model.study().tags()).contains("std3d"))
            throw new IllegalStateException("the owned full-3D model and original std3d study are required");
        if (Arrays.asList(model.study().tags()).contains(BMA_PROBE_STUDY))
            throw new IllegalStateException("the one-shot BMA producer probe study already exists; refusing replay");

        PhysicsFeature port = model.component(COMPONENT).physics("ewfd").feature("portOut3d");
        int[] nativePortFaces = port.selection().entities();
        int[] registeredPortFaces = model.component(COMPONENT).selection("sel3dOutputPort").entities(2);
        String portModeNumberReadback = port.getString("PortModeNumber");
        if (!"Numeric".equals(port.getString("PortType"))
                || !"2".equals(port.getString("PortName"))
                || !"1".equals(portModeNumberReadback)
                || !"sel3dOutputPort".equals(port.selection().named())
                || port.selection().dim() != 2
                || !sameSet(nativePortFaces, registeredPortFaces))
            throw new IllegalStateException("actual receiver Port feature/configuration/selection differs from the frozen Numeric Port 2");

        List<Map<String, Object>> originalStepsBefore = studyStepReadback(model, "std3d");
        requireOriginalStudyConfiguration(originalStepsBefore);
        Study probe = model.study().create(BMA_PROBE_STUDY);
        StudyFeature bma = probe.feature().create(BMA_PROBE_STEP, "BoundaryModeAnalysis");
        configureBma(bma, "2");
        List<Map<String, Object>> probeSteps = studyStepReadback(model, BMA_PROBE_STUDY);
        requireProbeStudyConfiguration(probeSteps);

        probe.createAutoSequences("sol");
        String[] sequenceTags = probe.getSolverSequences("SolverSequence");
        if (sequenceTags == null || sequenceTags.length != 1 || sequenceTags[0] == null
                || sequenceTags[0].trim().isEmpty())
            throw new IllegalStateException("isolated BMA study did not generate exactly one solver sequence");
        SolverSequence sequence = model.sol(sequenceTags[0]);
        Map<String, Object> sequenceReadback = solverSequenceReadback(sequence, BMA_PROBE_STUDY);
        Map<String, Object> preSolveState = solutionState(sequence, sequenceTags[0]);
        if (!(preSolveState.get("solution_pairs") instanceof List)
                || !((List<?>) preSolveState.get("solution_pairs")).isEmpty())
            throw new IllegalStateException("new BMA producer sequence already contains solutions; refusing to attribute old data");
        List<Map<String, Object>> originalStepsAfter = studyStepReadback(model, "std3d");
        if (!originalStepsBefore.equals(originalStepsAfter))
            throw new IllegalStateException("isolated BMA probe changed the original three-step std3d baseline");

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("fixture_id", FIXTURE_ID);
        result.put("managed_identity", managedIdentity);
        result.put("status", "BMA_OUTPUT_PROBE_CONFIGURED_NOT_SOLVED");
        result.put("native_result", "COMSOL_NATIVE_BMA_PROBE_CONFIGURATION_READBACK");
        result.put("study_or_solver_invoked", false);
        result.put("producer_status", "PREPARED_ONLY_NOT_PRODUCER_EVIDENCE");
        result.put("field_mapping_status", "UNVERIFIED");
        result.put("receiver_port", Map.of("feature_tag", "portOut3d",
                "feature_type", port.getType(), "port_type", port.getString("PortType"),
                "port_name", port.getString("PortName"),
                "port_mode_number_readback", portModeNumberReadback,
                "selection_tag", port.selection().named(), "entity_dimension", port.selection().dim(),
                "boundary_ids", intList(nativePortFaces)));
        result.put("original_std3d_steps", originalStepsAfter);
        result.put("probe_study", Map.of("study_tag", BMA_PROBE_STUDY,
                "study_steps", probeSteps));
        result.put("solver_sequence", sequenceReadback);
        result.put("pre_solve_solution_state", preSolveState);
        return result;
    }

    /** Run only the full generated sequence attached to the isolated BMA-only study. */
    private static Map<String, Object> runBmaOutputProbe(Model model, Map<String, Object> args) {
        Map<String, Object> managedIdentity = readManagedIdentity(model, args);
        if (args == null || !BMA_PROBE_STUDY.equals(args.get("study_tag")))
            throw new IllegalArgumentException("exact isolated BMA probe study tag is required");
        String expectedSequenceTag = nonemptyString(args.get("solver_sequence_tag"), "solver_sequence_tag");
        if (!Arrays.asList(model.study().tags()).contains(BMA_PROBE_STUDY))
            throw new IllegalStateException("the isolated BMA probe study is absent");
        List<Map<String, Object>> probeSteps = studyStepReadback(model, BMA_PROBE_STUDY);
        requireProbeStudyConfiguration(probeSteps);
        Study probe = model.study(BMA_PROBE_STUDY);
        String[] sequenceTags = probe.getSolverSequences("SolverSequence");
        if (sequenceTags == null || sequenceTags.length != 1
                || !expectedSequenceTag.equals(sequenceTags[0]))
            throw new IllegalStateException("the isolated BMA study no longer resolves to the exact prepared solver sequence");
        SolverSequence sequence = model.sol(expectedSequenceTag);
        Map<String, Object> sequenceReadback = solverSequenceReadback(sequence, BMA_PROBE_STUDY);
        Map<String, Object> preSolveState = solutionState(sequence, expectedSequenceTag);
        if (!(preSolveState.get("solution_pairs") instanceof List)
                || !((List<?>) preSolveState.get("solution_pairs")).isEmpty())
            throw new IllegalStateException("BMA producer run is one-shot and refuses preexisting solution data");

        // This exact sequence is attached to a parent study with one BMA step;
        // runAll executes its complete generated solver tree, including all
        // COMSOL-generated variable/eigenvalue/store features.
        sequence.runAll();
        Map<String, Object> postSolveState = solutionState(sequence, expectedSequenceTag);
        boolean twoDistinctSolutions = hasTwoDistinctInnerSolutions(postSolveState, expectedSequenceTag);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("fixture_id", FIXTURE_ID);
        result.put("managed_identity", managedIdentity);
        result.put("status", twoDistinctSolutions
                ? "BMA_OUTPUT_PROBE_SOLVER_SEQUENCE_RETURNED_TWO_SOLUTION_ROWS"
                : "BMA_OUTPUT_PRODUCER_READBACK_INCOMPLETE");
        result.put("native_result", "COMSOL_NATIVE_BMA_PRODUCER_RUN_READBACK");
        result.put("study_or_solver_invoked", true);
        result.put("solver_calls", 1);
        result.put("study_run_calls", 0);
        result.put("producer_status", twoDistinctSolutions
                ? "CONTROLLED_SINGLE_STEP_BMA_PRODUCER_VERIFIED"
                : "UNVERIFIED_SOLUTION_COUNT_OR_AXIS_READBACK");
        result.put("field_mapping_status", "UNVERIFIED");
        result.put("invocation", Map.of("method", "SolverSequence.runAll",
                "solver_sequence_tag", expectedSequenceTag,
                "parent_study_tag", BMA_PROBE_STUDY,
                "parent_study_step_tag", BMA_PROBE_STEP,
                "method_returned", true));
        result.put("probe_study", Map.of("study_tag", BMA_PROBE_STUDY,
                "study_steps", probeSteps));
        result.put("solver_sequence", sequenceReadback);
        result.put("pre_solve_solution_state", preSolveState);
        result.put("post_solve_solution_state", postSolveState);
        result.put("basis_ordinal_mapping", "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED");
        return result;
    }

    private static Map<String, Object> readManagedIdentity(Model model, Map<String, Object> args) {
        if (args == null) throw new IllegalArgumentException("managed identity arguments are required");
        Object raw = args.get("managed_identity");
        if (!(raw instanceof Map)) throw new IllegalArgumentException("persisted managed ModelRef is required");
        @SuppressWarnings("unchecked") Map<String, Object> identity = (Map<String, Object>) raw;
        String projectId = nonemptyString(identity.get("project_id"), "project_id");
        Object modelRef = identity.get("model_ref");
        if (!(modelRef instanceof Map) || ((Map<?, ?>) modelRef).isEmpty())
            throw new IllegalArgumentException("persisted managed ModelRef is required");
        String modelTag = nonemptyString(identity.get("model_tag"), "model_tag");
        if (!modelTag.equals(model.tag())) throw new IllegalStateException("managed model tag differs from native Model");
        Object revision = identity.get("expected_revision");
        if (!(revision instanceof Number) || revision instanceof Boolean
                || ((Number) revision).longValue() < 0
                || ((Number) revision).doubleValue() != ((Number) revision).longValue())
            throw new IllegalArgumentException("managed expected revision must be a nonnegative integer");
        Map<String, Object> bound = new LinkedHashMap<>();
        bound.put("project_id", projectId);
        Map<String, Object> modelRefCopy = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : ((Map<?, ?>) modelRef).entrySet()) {
            if (!(entry.getKey() instanceof String))
                throw new IllegalArgumentException("managed ModelRef keys must be strings");
            modelRefCopy.put((String) entry.getKey(), entry.getValue());
        }
        bound.put("model_ref", modelRefCopy);
        bound.put("model_tag", modelTag);
        bound.put("expected_revision", ((Number) revision).longValue());
        return bound;
    }

    private static List<Map<String, Object>> studyStepReadback(Model model, String studyTag) {
        List<Map<String, Object>> steps = new ArrayList<>();
        for (String tag : model.study(studyTag).feature().tags()) {
            StudyFeature feature = model.study(studyTag).feature(tag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tag);
            row.put("feature_type", feature.getType());
            for (String property : new String[]{"PortName", "modeFreq", "plist"})
                if (feature.hasProperty(property)) row.put(property, feature.getString(property));
            if (feature.hasProperty("neigs")) row.put("neigs", feature.getInt("neigs"));
            steps.add(row);
        }
        return steps;
    }

    private static void requireOriginalStudyConfiguration(List<Map<String, Object>> steps) {
        if (steps.size() != 3 || !"bmaInput3d".equals(steps.get(0).get("tag"))
                || !"bmaOutput3d".equals(steps.get(1).get("tag"))
                || !"freq3d".equals(steps.get(2).get("tag"))
                || !"BoundaryModeAnalysis".equals(steps.get(0).get("feature_type"))
                || !"BoundaryModeAnalysis".equals(steps.get(1).get("feature_type"))
                || !"Frequency".equals(steps.get(2).get("feature_type"))
                || !"1".equals(steps.get(0).get("PortName"))
                || !"2".equals(steps.get(1).get("PortName"))
                || !"f0".equals(steps.get(0).get("modeFreq"))
                || !"f0".equals(steps.get(1).get("modeFreq"))
                || !Integer.valueOf(2).equals(steps.get(0).get("neigs"))
                || !Integer.valueOf(2).equals(steps.get(1).get("neigs"))
                || !"f0".equals(steps.get(2).get("plist")))
            throw new IllegalStateException("original std3d input-BMA/output-BMA/frequency baseline differs from its frozen configuration");
    }

    private static void requireProbeStudyConfiguration(List<Map<String, Object>> steps) {
        if (steps.size() != 1 || !BMA_PROBE_STEP.equals(steps.get(0).get("tag"))
                || !"BoundaryModeAnalysis".equals(steps.get(0).get("feature_type"))
                || !"2".equals(steps.get(0).get("PortName"))
                || !"f0".equals(steps.get(0).get("modeFreq"))
                || !Integer.valueOf(2).equals(steps.get(0).get("neigs")))
            throw new IllegalStateException("probe parent study must contain exactly the receiver Port 2 BMA step with two requested eigensolutions at f0");
    }

    private static Map<String, Object> solverSequenceReadback(SolverSequence sequence, String expectedStudy) {
        String solverStudy = sequence.study();
        if (!expectedStudy.equals(solverStudy))
            throw new IllegalStateException("generated solver sequence belongs to a different parent study");
        List<Map<String, Object>> tree = new ArrayList<>();
        List<Map<String, Object>> bindings = new ArrayList<>();
        for (String featureTag : sequence.feature().tags())
            collectSolverFeature(sequence.feature(featureTag), featureTag, tree, bindings);
        if (tree.isEmpty() || bindings.size() != 1
                || !expectedStudy.equals(bindings.get(0).get("study"))
                || !BMA_PROBE_STEP.equals(bindings.get(0).get("studystep")))
            throw new IllegalStateException("generated solver tree does not bind exactly the isolated receiver BMA StudyStep");
        Set<String> featureTypes = new LinkedHashSet<>();
        for (Map<String, Object> row : tree)
            if (row.get("feature_type") instanceof String)
                featureTypes.add((String) row.get("feature_type"));
        if (!featureTypes.containsAll(Arrays.asList("Variables", "Eigenvalue", "StoreSolution")))
            throw new IllegalStateException("generated BMA solver tree lacks the Variables/Eigenvalue/StoreSolution computation path");
        Map<String, Object> output = new LinkedHashMap<>();
        output.put("tag", sequence.tag());
        output.put("feature_type", sequence.getType());
        output.put("parent_study", solverStudy);
        output.put("solver_tree_features", tree);
        output.put("study_step_bindings_in_solver_tree_order", bindings);
        return output;
    }

    private static Map<String, Object> solutionState(SolverSequence sequence, String expectedSequenceTag) {
        SolutionInfo info = sequence.getSolutioninfo();
        boolean valid = info.isValid();
        int[] outerSolnums = info.getOuterSolnum();
        if (outerSolnums == null)
            throw new IllegalStateException("SolutionInfo returned no outer-solution axis readback");
        Set<Integer> seenOuter = new LinkedHashSet<>();
        List<Map<String, Object>> pairs = new ArrayList<>();
        for (int outer : outerSolnums) {
            if (outer < 1 || !seenOuter.add(outer))
                throw new IllegalStateException("SolutionInfo outer-solution axis is invalid or duplicated");
            int[] innerSolnums = info.getSolnum(outer, true);
            if (innerSolnums == null || innerSolnums.length == 0)
                throw new IllegalStateException("SolutionInfo has no strict inner-solution axis for a returned outer index");
            Set<Integer> seenInner = new LinkedHashSet<>();
            String mappedSequence = info.getSolverSequence(outer);
            if (!expectedSequenceTag.equals(mappedSequence))
                throw new IllegalStateException("SolutionInfo outer solution maps to a different solver sequence");
            for (int inner : innerSolnums) {
                if (inner < 1 || !seenInner.add(inner))
                    throw new IllegalStateException("SolutionInfo inner-solution axis is invalid or duplicated");
                pairs.add(Map.of("outer_index", outer, "inner_index", inner,
                        "solnum", inner, "solver_sequence_tag", mappedSequence));
            }
        }
        return Map.of("is_valid", valid, "solver_sequence_is_empty", sequence.isEmpty(),
                "outer_solnums", intList(outerSolnums),
                "solution_pairs", pairs, "pair_count", pairs.size());
    }

    private static boolean hasTwoDistinctInnerSolutions(Map<String, Object> state,
            String expectedSequenceTag) {
        Object rawOuters = state.get("outer_solnums");
        Object rawPairs = state.get("solution_pairs");
        if (!Boolean.TRUE.equals(state.get("is_valid"))
                || !Boolean.FALSE.equals(state.get("solver_sequence_is_empty"))
                || !(rawOuters instanceof List) || ((List<?>) rawOuters).size() != 1
                || !(rawPairs instanceof List) || ((List<?>) rawPairs).size() != 2
                || !(state.get("pair_count") instanceof Integer)
                || ((Integer) state.get("pair_count")).intValue() != 2)
            return false;
        Object rawOuter = ((List<?>) rawOuters).get(0);
        if (!(rawOuter instanceof Integer) || ((Integer) rawOuter).intValue() < 1)
            return false;
        int outer = ((Integer) rawOuter).intValue();
        Set<Integer> innerIndices = new LinkedHashSet<>();
        for (Object rawPair : (List<?>) rawPairs) {
            if (!(rawPair instanceof Map)) return false;
            Map<?, ?> pair = (Map<?, ?>) rawPair;
            Object pairOuter = pair.get("outer_index");
            Object pairInner = pair.get("inner_index");
            if (!(pairOuter instanceof Integer) || ((Integer) pairOuter).intValue() != outer
                    || !(pairInner instanceof Integer) || ((Integer) pairInner).intValue() < 1
                    || !pairInner.equals(pair.get("solnum"))
                    || !expectedSequenceTag.equals(pair.get("solver_sequence_tag")))
                return false;
            innerIndices.add((Integer) pairInner);
        }
        return innerIndices.size() == 2;
    }

    private static void collectSolverFeature(SolverFeature feature, String path,
            List<Map<String, Object>> tree, List<Map<String, Object>> bindings) {
        String type = feature.getType();
        tree.add(Map.of("path", path, "feature_type", type));
        if ("StudyStep".equals(type)) {
            Map<String, Object> binding = new LinkedHashMap<>();
            binding.put("path", path);
            binding.put("feature_type", type);
            for (String property : new String[]{"study", "studystep"})
                if (feature.hasProperty(property)) binding.put(property, feature.getString(property));
            bindings.add(binding);
        }
        for (String child : feature.feature().tags())
            collectSolverFeature(feature.feature(child), path + "/" + child, tree, bindings);
    }

    /**
     * Read the bulk BMA eigensolution fields beside Numeric Port 2's configured
     * mode field at one exact native solution tuple. Three Interp features are
     * used so E, H, and dimensionless normals each retain an explicit unit.
     * This phase never invokes a Study or solver.
     */
    @SuppressWarnings("unchecked")
    private static Map<String, Object> bmaBasisFields(Model model, Map<String, Object> args) {
        if (args == null || !(args.get("contract") instanceof Map)
                || !(args.get("coordinates_m") instanceof List))
            throw new IllegalArgumentException("bma_basis_fields requires one bound contract and its exact coordinates");
        Map<String, Object> contract = (Map<String, Object>) args.get("contract");
        if (!"bma_basis_mapping_pair".equals(contract.get("role"))
                || !"w23.full3d.numeric_port_bma_basis_pair.v1".equals(contract.get("provenance_schema"))
                || !"NOT_RUN".equals(args.get("native_result"))
                || !Boolean.FALSE.equals(args.get("study_or_solver_invoked")))
            throw new IllegalArgumentException("paired BMA field extraction must use its exact read-only versioned contract");
        List<String> allExpressions = requireStringList(contract.get("field_names"), "field_names");
        if (!Arrays.equals(BMA_PAIR_FIELDS, allExpressions.toArray(new String[0])))
            throw new IllegalArgumentException("paired BMA contract does not request the exact generic/Port/normal fields");

        Map<String, Object> source = requireMap(contract.get("source"), "source");
        Map<String, Object> plane = requireMap(contract.get("plane"), "plane");
        Map<String, Object> basisAxis = requireMap(contract.get("basis_axis"), "basis_axis");
        Map<String, Object> portAxis = requireMap(contract.get("port_mode_axis"), "port_mode_axis");
        String datasetTag = requiredTag(source.get("dataset_id"), "dataset_id");
        String solutionTag = requiredTag(source.get("solution_id"), "solution_id");
        String selectionTag = requiredTag(plane.get("selection_tag"), "selection_tag");
        int inner = requiredPositiveIndex(source.get("inner_index"), "inner_index");
        int outer = requiredPositiveIndex(source.get("outer_index"), "outer_index");
        int solnum = requiredPositiveIndex(source.get("solnum"), "solnum");
        Object ordinalValue = basisAxis.get("ordinal");
        if (!(ordinalValue instanceof Number) || ordinalValue instanceof Boolean
                || (((Number) ordinalValue).doubleValue() != 1.0
                    && ((Number) ordinalValue).doubleValue() != 2.0)
                || !"ordered SolutionInfo.getSolnum(outer,true) row ordinal".equals(basisAxis.get("axis"))
                || !solutionTag.equals(basisAxis.get("solution_id"))
                || !solutionTag.equals(basisAxis.get("solver_sequence_tag"))
                || !Integer.valueOf(outer).equals(basisAxis.get("outer_index"))
                || !Integer.valueOf(inner).equals(basisAxis.get("inner_index"))
                || !Integer.valueOf(solnum).equals(basisAxis.get("solnum")))
            throw new IllegalArgumentException("basis ordinal is not bound to its exact SolutionInfo/source tuple");
        if (!"portOut3d".equals(portAxis.get("feature_tag"))
                || !"Port".equals(portAxis.get("feature_type"))
                || !"Numeric".equals(portAxis.get("port_type"))
                || !"2".equals(portAxis.get("port_name"))
                || !"1".equals(portAxis.get("port_mode_number_readback"))
                || !selectionTag.equals(portAxis.get("selection_tag"))
                || !"sel3dOutputPort".equals(selectionTag))
            throw new IllegalArgumentException("separate Numeric Port name/PortModeNumber readback is incomplete");

        if (!Arrays.asList(model.result().dataset().tags()).contains(datasetTag))
            throw new IllegalStateException("exact BMA solution dataset is absent: " + datasetTag);
        PropFeature dataset = model.result().dataset(datasetTag);
        if (!"Solution".equals(dataset.getType()) || !dataset.hasProperty("solution")
                || !solutionTag.equals(dataset.getString("solution")))
            throw new IllegalStateException("dataset does not bind to the exact BMA producer solver sequence");
        if (!Arrays.asList(model.sol().tags()).contains(solutionTag))
            throw new IllegalStateException("exact BMA producer solver sequence is absent");
        Map<String, Object> solutionState = solutionState(model.sol(solutionTag), solutionTag);
        Object rawPairs = solutionState.get("solution_pairs");
        boolean exactTupleFound = false;
        if (rawPairs instanceof List) {
            for (Object rawPair : (List<?>) rawPairs) {
                if (!(rawPair instanceof Map)) continue;
                Map<?, ?> pair = (Map<?, ?>) rawPair;
                if (Integer.valueOf(outer).equals(pair.get("outer_index"))
                        && Integer.valueOf(inner).equals(pair.get("inner_index"))
                        && Integer.valueOf(solnum).equals(pair.get("solnum"))
                        && solutionTag.equals(pair.get("solver_sequence_tag")))
                    exactTupleFound = true;
            }
        }
        if (solutionState.get("is_valid") != Boolean.TRUE || !exactTupleFound)
            throw new IllegalStateException("requested dataset tuple is not present in exact live SolutionInfo readback");

        Map<String, Object> cohortBefore = rawSourceCohortSnapshot(
                model, datasetTag, solutionTag, outer, inner, solnum, selectionTag);

        if (!Arrays.asList(model.component(COMPONENT).selection().tags()).contains(selectionTag))
            throw new IllegalStateException("requested output Port boundary selection is absent");
        SelectionFeature selection = model.component(COMPONENT).selection(selectionTag);
        PhysicsFeature port = model.component(COMPONENT).physics("ewfd").feature("portOut3d");
        int[] nativePortFaces = port.selection().entities();
        if (!"Numeric".equals(port.getString("PortType"))
                || !"2".equals(port.getString("PortName"))
                || !"1".equals(port.getString("PortModeNumber"))
                || !selectionTag.equals(port.selection().named())
                || selection.getInt("entitydim") != 2 || selection.entities(2).length == 0
                || selection.dim() != 2 || !sameSet(nativePortFaces, selection.entities(2))
                || !intList(nativePortFaces).equals(portAxis.get("boundary_ids")))
            throw new IllegalStateException("live Numeric Port 2 identity/selection differs from the prepared mode-axis evidence");

        Object groupObj = contract.get("unit_groups");
        if (!(groupObj instanceof Map)) throw new IllegalArgumentException("explicit E/H/normal unit groups are required");
        Map<?, ?> unitGroups = (Map<?, ?>) groupObj;
        Map<String, List<String>> expressionsByGroup = new LinkedHashMap<>();
        expressionsByGroup.put("electric", Arrays.asList(BMA_PAIR_E_FIELDS));
        expressionsByGroup.put("magnetic", Arrays.asList(BMA_PAIR_H_FIELDS));
        expressionsByGroup.put("normal", Arrays.asList(BMA_PAIR_NORMAL_FIELDS));
        Map<String, String> expectedUnits = new LinkedHashMap<>();
        expectedUnits.put("electric", "V/m");
        expectedUnits.put("magnetic", "A/m");
        expectedUnits.put("normal", "1");
        for (String groupName : expressionsByGroup.keySet()) {
            Object rawGroup = unitGroups.get(groupName);
            if (!(rawGroup instanceof Map))
                throw new IllegalArgumentException("unit group is missing: " + groupName);
            Map<?, ?> group = (Map<?, ?>) rawGroup;
            if (!expectedUnits.get(groupName).equals(group.get("unit"))
                    || !expressionsByGroup.get(groupName).equals(group.get("expressions")))
                throw new IllegalArgumentException("field unit/expression group differs from the frozen SI contract");
        }

        Object rawCoordinateList = args.get("coordinates_m");
        List<?> rawCoordinates = (List<?>) rawCoordinateList;
        if (rawCoordinates.isEmpty()) throw new IllegalArgumentException("paired field quadrature must not be empty");
        double[][] coordinates = new double[3][rawCoordinates.size()];
        for (int point = 0; point < rawCoordinates.size(); point++) {
            Object rawPoint = rawCoordinates.get(point);
            if (!(rawPoint instanceof List) || ((List<?>) rawPoint).size() != 3)
                throw new IllegalArgumentException("paired field coordinate must be an xyz metre triple");
            List<?> tuple = (List<?>) rawPoint;
            for (int axis = 0; axis < 3; axis++) {
                Object value = tuple.get(axis);
                if (!(value instanceof Number) || value instanceof Boolean
                        || !Double.isFinite(((Number) value).doubleValue()))
                    throw new IllegalArgumentException("paired field coordinates must be finite SI metres");
                coordinates[axis][point] = ((Number) value).doubleValue();
            }
        }
        String contractId = requiredSha256(contract.get("contract_id"), "contract_id");
        String baseTag = "w23bm" + contractId.substring(0, 10);
        List<String> tags = Arrays.asList(baseTag + "e", baseTag + "h", baseTag + "n");
        for (String tag : tags)
            if (containsNumerical(model, tag))
                throw new IllegalStateException("request-owned paired-field Interp tag already exists: " + tag);

        List<NumericalFeature> created = new ArrayList<>();
        Throwable operationFailure = null;
        Throwable cleanupFailure = null;
        List<Map<String, Object>> rows = new ArrayList<>();
        List<List<Double>> sharedCoordinateReadback = null;
        Map<String, String> unitReadback = new LinkedHashMap<>();
        int groupIndex = 0;
        for (String groupName : expressionsByGroup.keySet()) {
            if (operationFailure != null) break;
            String tag = tags.get(groupIndex++);
            List<String> expressions = expressionsByGroup.get(groupName);
            String requestedUnit = expectedUnits.get(groupName);
            NumericalFeature interp = null;
            try {
                interp = model.result().numerical().create(tag, "Interp");
                created.add(interp);
                interp.set("data", datasetTag);
                interp.set("expr", expressions.toArray(new String[0]));
                interp.set("unit", requestedUnit);
                interp.set("solnum", Integer.toString(solnum));
                interp.set("outersolnum", outer);
                interp.set("coorderr", "on");
                interp.set("matherr", "on");
                interp.set("ext", 0.0);
                interp.setInterpolationCoordinates(coordinates);
                interp.selection().named(selectionTag);
                if (!datasetTag.equals(interp.getString("data"))
                        || !Arrays.equals(expressions.toArray(new String[0]), interp.getStringArray("expr"))
                        || !requestedUnit.equals(interp.getString("unit"))
                        || !Integer.toString(solnum).equals(interp.getString("solnum"))
                        || !Integer.toString(outer).equals(interp.getString("outersolnum"))
                        || !interp.getBoolean("coorderr") || !interp.getBoolean("matherr")
                        || interp.getDouble("ext") != 0.0
                        || !selectionTag.equals(interp.selection().named())
                        || interp.selection().dim() != 2
                        || !Arrays.equals(selection.entities(2), interp.selection().entities()))
                    throw new IllegalStateException("paired-field Interp configuration/unit/selection readback differs");
                interp.run();
                double[][][] real = interp.getData();
                double[][][] imaginary = interp.getImagData();
                double[][] coordinateReadback = interp.getCoordinates();
                boolean complexGroup = interp.isComplex();
                if ("normal".equals(groupName) ? complexGroup : (!complexGroup || imaginary == null))
                    throw new IllegalStateException("paired-field complex/readback kind differs for " + groupName);
                if (real == null || real.length != expressions.size()
                        || coordinateReadback == null || coordinateReadback.length != 3)
                    throw new IllegalStateException("paired-field Interp omitted expression or coordinate axes");
                List<List<Double>> coordinateRows = new ArrayList<>();
                for (int axis = 0; axis < 3; axis++) {
                    if (coordinateReadback[axis] == null || coordinateReadback[axis].length != rawCoordinates.size())
                        throw new IllegalStateException("paired-field coordinate axis differs from the frozen point count");
                    List<Double> axisValues = new ArrayList<>();
                    for (int point = 0; point < rawCoordinates.size(); point++) {
                        double observed = coordinateReadback[axis][point];
                        if (!Double.isFinite(observed)
                                || Math.abs(observed - coordinates[axis][point]) > 2e-12
                                || (sharedCoordinateReadback != null
                                    && Math.abs(observed - sharedCoordinateReadback.get(axis).get(point)) > 2e-12))
                            throw new IllegalStateException("paired E/H/normal Interp groups do not share the exact frozen coordinates");
                        axisValues.add(observed);
                    }
                    coordinateRows.add(axisValues);
                }
                if (sharedCoordinateReadback == null) sharedCoordinateReadback = coordinateRows;
                unitReadback.put(groupName, interp.getString("unit"));
                for (int expr = 0; expr < expressions.size(); expr++) {
                    if (real[expr] == null || real[expr].length != 1
                            || real[expr][0].length != rawCoordinates.size()
                            || (imaginary != null && (imaginary.length != expressions.size()
                                || imaginary[expr] == null || imaginary[expr].length != 1
                                || imaginary[expr][0].length != rawCoordinates.size())))
                        throw new IllegalStateException("paired field expression solution/point axes differ from its contract");
                    List<Double> realValues = new ArrayList<>(), imaginaryValues = new ArrayList<>();
                    for (int point = 0; point < rawCoordinates.size(); point++) {
                        double rv = real[expr][0][point];
                        double iv = imaginary == null ? 0.0 : imaginary[expr][0][point];
                        if (!Double.isFinite(rv) || !Double.isFinite(iv)
                                || ("normal".equals(groupName) && Math.abs(iv) > 1e-10))
                            throw new IllegalStateException("paired field sample is nonfinite or a surface normal is complex");
                        realValues.add(rv);
                        imaginaryValues.add(iv);
                    }
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("expression", expressions.get(expr));
                    row.put("real", realValues);
                    row.put("imag", imaginaryValues);
                    row.put("unit", interp.getString("unit"));
                    row.put("unit_group", groupName);
                    rows.add(row);
                }
            } catch (Throwable error) {
                operationFailure = error;
            }
        }
        for (String tag : tags) {
            try {
                if (containsNumerical(model, tag)) model.result().numerical().remove(tag);
                if (containsNumerical(model, tag))
                    throw new IllegalStateException("request-owned paired-field Interp remains after cleanup: " + tag);
            } catch (Throwable error) {
                if (cleanupFailure == null) cleanupFailure = error;
                else cleanupFailure.addSuppressed(error);
            }
        }
        Map<String, Object> cleanup = new LinkedHashMap<>();
        cleanup.put("created_count", created.size());
        cleanup.put("expected_count", tags.size());
        cleanup.put("removed", cleanupFailure == null);
        cleanup.put("cleanup_failed", cleanupFailure != null);
        cleanup.put("tags", tags);
        cleanup.put("error", cleanupFailure == null ? "" : cleanupFailure.toString());
        if (operationFailure != null)
            throw new IllegalStateException("paired BMA field extraction failed; cleanup=" + cleanup, operationFailure);
        if (cleanupFailure != null)
            throw new IllegalStateException("paired BMA field Interp cleanup failed", cleanupFailure);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("native_result", "COMSOL_NATIVE_RAW");
        result.put("study_or_solver_invoked", false);
        result.put("complex_readback", true);
        result.put("units_preserved", true);
        result.put("contract_id", contractId);
        result.put("quadrature_sha256", contract.get("quadrature_sha256"));
        result.put("source", source);
        result.put("plane", plane);
        result.put("basis_axis", basisAxis);
        result.put("port_mode_axis", portAxis);
        result.put("sample_count", rawCoordinates.size());
        result.put("coordinates_m", sharedCoordinateReadback);
        result.put("unit_readback", unitReadback);
        result.put("expressions", rows);
        Map<String, Object> cohortAfter = rawSourceCohortSnapshot(
                model, datasetTag, solutionTag, outer, inner, solnum, selectionTag);
        if (!cohortBefore.equals(cohortAfter))
            throw new IllegalStateException("native source-cohort identity/configuration changed during read-only field sampling");
        result.put("source_cohort", Map.of(
                "schema_id", "urn:comsol-mcp:w23:source-cohort-snapshot:1.0.0",
                "native_result", "COMSOL_NATIVE_SOURCE_COHORT_SNAPSHOTS",
                "before", cohortBefore, "after", cohortAfter));
        result.put("cleanup", cleanup);
        result.put("field_mapping_status", "UNVERIFIED");
        result.put("basis_ordinal_mapping", "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED");
        result.put("numeric_port_mode_field_mapping", "UNVERIFIED");
        return result;
    }

    /**
     * Evaluate one exact native source/plane contract at supplied quadrature
     * coordinates. This phase never invokes Study.run or a solver.
     */
    @SuppressWarnings("unchecked")
    private static Map<String, Object> rawFields(Model model, Map<String, Object> args) {
        if (args == null || !(args.get("contract") instanceof Map)
                || !(args.get("coordinates_m") instanceof List))
            throw new IllegalArgumentException("raw_fields requires a bound contract and its exact coordinates");
        Map<String, Object> contract = (Map<String, Object>) args.get("contract");
        if (!"NOT_RUN".equals(args.get("native_result"))
                || !Boolean.FALSE.equals(args.get("study_or_solver_invoked")))
            throw new IllegalArgumentException("raw field extraction is read-only and must be marked NOT_RUN/no-solve");
        Map<String, Object> source = requireMap(contract.get("source"), "source");
        Map<String, Object> plane = requireMap(contract.get("plane"), "plane");
        String datasetTag = requiredTag(source.get("dataset_id"), "dataset_id");
        String solutionTag = requiredTag(source.get("solution_id"), "solution_id");
        String selectionTag = requiredTag(plane.get("selection_tag"), "selection_tag");
        int inner = requiredPositiveIndex(source.get("inner_index"), "inner_index");
        int outer = requiredPositiveIndex(source.get("outer_index"), "outer_index");
        int solnum = requiredPositiveIndex(source.get("solnum"), "solnum");
        if (!Arrays.asList(model.result().dataset().tags()).contains(datasetTag))
            throw new IllegalStateException("exact native dataset is absent: " + datasetTag);
        PropFeature dataset = model.result().dataset(datasetTag);
        if (!dataset.hasProperty("solution") || !solutionTag.equals(dataset.getString("solution")))
            throw new IllegalStateException("dataset does not bind to the exact requested native solution");
        if (!Arrays.asList(model.component(COMPONENT).selection().tags()).contains(selectionTag))
            throw new IllegalStateException("requested native boundary selection is absent");
        SelectionFeature selection = model.component(COMPONENT).selection(selectionTag);
        if (selection.getInt("entitydim") != 2 || selection.entities(2).length == 0)
            throw new IllegalStateException("requested named port selection is not a nonempty 2-D boundary selection");

        Map<String, Object> cohortBefore = rawSourceCohortSnapshot(
                model, datasetTag, solutionTag, outer, inner, solnum, selectionTag);
        List<?> rawCoordinates = (List<?>) args.get("coordinates_m");
        if (rawCoordinates.isEmpty()) throw new IllegalArgumentException("raw field quadrature must not be empty");
        double[][] coordinates = new double[3][rawCoordinates.size()];
        List<String> expressions = requireStringList(contract.get("field_names"), "field_names");
        if (expressions.isEmpty() || new LinkedHashSet<>(expressions).size() != expressions.size())
            throw new IllegalArgumentException("raw field expression list is empty or duplicated");
        for (int point = 0; point < rawCoordinates.size(); point++) {
            Object rawPoint = rawCoordinates.get(point);
            if (!(rawPoint instanceof List) || ((List<?>) rawPoint).size() != 3)
                throw new IllegalArgumentException("raw field coordinate must be an xyz metre triple");
            List<?> tuple = (List<?>) rawPoint;
            for (int axis = 0; axis < 3; axis++) {
                Object value = tuple.get(axis);
                if (!(value instanceof Number) || value instanceof Boolean
                        || !Double.isFinite(((Number) value).doubleValue()))
                    throw new IllegalArgumentException("raw field coordinates must be finite metres");
                coordinates[axis][point] = ((Number) value).doubleValue();
            }
        }
        String contractId = requiredSha256(contract.get("contract_id"), "contract_id");
        String baseTag = "w23rf" + contractId.substring(0, 10);
        Map<String, List<String>> expressionsByGroup = new LinkedHashMap<>();
        expressionsByGroup.put("electric", new ArrayList<>());
        expressionsByGroup.put("magnetic", new ArrayList<>());
        expressionsByGroup.put("normal", new ArrayList<>());
        for (String expression : expressions) {
            if (expression.startsWith("ewfd.E")) expressionsByGroup.get("electric").add(expression);
            else if (expression.startsWith("ewfd.H")) expressionsByGroup.get("magnetic").add(expression);
            else if (Arrays.asList("nx", "ny", "nz").contains(expression))
                expressionsByGroup.get("normal").add(expression);
            else throw new IllegalArgumentException("raw-field expression is outside the frozen E/H/normal groups");
        }
        Map<String, String> expectedUnits = new LinkedHashMap<>();
        expectedUnits.put("electric", "V/m");
        expectedUnits.put("magnetic", "A/m");
        expectedUnits.put("normal", "1");
        Map<?, ?> rawUnitGroups = requireMap(contract.get("unit_groups"), "unit_groups");
        for (String groupName : expressionsByGroup.keySet()) {
            Map<?, ?> group = requireMap(rawUnitGroups.get(groupName), "unit_groups." + groupName);
            if (expressionsByGroup.get(groupName).isEmpty()
                    || !expectedUnits.get(groupName).equals(group.get("unit"))
                    || !expressionsByGroup.get(groupName).equals(group.get("expressions")))
                throw new IllegalArgumentException("raw-field SI unit group differs from the frozen contract");
        }
        List<String> tags = Arrays.asList(baseTag + "e", baseTag + "h", baseTag + "n");
        for (String ownedTag : tags)
            if (containsNumerical(model, ownedTag))
                throw new IllegalStateException("request-owned raw-field Interp tag already exists: " + ownedTag);
        List<NumericalFeature> created = new ArrayList<>();
        List<Map<String, Object>> rows = new ArrayList<>();
        List<List<Double>> sharedCoordinates = null;
        Map<String, String> unitReadback = new LinkedHashMap<>();
        Throwable operationFailure = null;
        Throwable cleanupFailure = null;
        try {
            int groupIndex = 0;
            for (String groupName : expressionsByGroup.keySet()) {
                List<String> groupExpressions = expressionsByGroup.get(groupName);
                String requestedUnit = expectedUnits.get(groupName);
                String ownedTag = tags.get(groupIndex++);
                NumericalFeature interp = model.result().numerical().create(ownedTag, "Interp");
                created.add(interp);
                interp.set("data", datasetTag);
                interp.set("expr", groupExpressions.toArray(new String[0]));
                interp.set("unit", requestedUnit);
                interp.set("solnum", Integer.toString(solnum));
                interp.set("outersolnum", outer);
                interp.set("coorderr", "on");
                interp.set("matherr", "on");
                interp.set("ext", 0.0);
                interp.setInterpolationCoordinates(coordinates);
                interp.selection().named(selectionTag);
                if (!datasetTag.equals(interp.getString("data"))
                        || !Arrays.equals(groupExpressions.toArray(new String[0]), interp.getStringArray("expr"))
                        || !requestedUnit.equals(interp.getString("unit"))
                        || !Integer.toString(solnum).equals(interp.getString("solnum"))
                        || !Integer.toString(outer).equals(interp.getString("outersolnum"))
                        || !interp.getBoolean("coorderr") || !interp.getBoolean("matherr")
                        || interp.getDouble("ext") != 0.0
                        || !selectionTag.equals(interp.selection().named())
                        || interp.selection().dim() != 2
                        || !Arrays.equals(selection.entities(2), interp.selection().entities()))
                    throw new IllegalStateException("raw-field Interp configuration/unit/selection readback differs");
                interp.run();
                double[][][] real = interp.getData();
                double[][][] imaginary = interp.getImagData();
                double[][] coordinateReadback = interp.getCoordinates();
                boolean complexGroup = interp.isComplex();
                if ("normal".equals(groupName) ? complexGroup : (!complexGroup || imaginary == null))
                    throw new IllegalStateException("raw-field complex/readback kind differs for " + groupName);
                if (real == null || real.length != groupExpressions.size()
                        || coordinateReadback == null || coordinateReadback.length != 3)
                    throw new IllegalStateException("raw-field Interp omitted expression/coordinate axes");
                List<List<Double>> coordinateRows = new ArrayList<>();
                for (int axis = 0; axis < 3; axis++) {
                    if (coordinateReadback[axis] == null
                            || coordinateReadback[axis].length != rawCoordinates.size())
                        throw new IllegalStateException("raw-field coordinate axis differs from the frozen point count");
                    List<Double> coordinateValues = new ArrayList<>();
                    for (int point = 0; point < rawCoordinates.size(); point++) {
                        double observed = coordinateReadback[axis][point];
                        if (!Double.isFinite(observed)
                                || Math.abs(observed - coordinates[axis][point]) > 2e-12
                                || (sharedCoordinates != null
                                    && Math.abs(observed - sharedCoordinates.get(axis).get(point)) > 2e-12))
                            throw new IllegalStateException("raw E/H/normal groups differ from the exact SI coordinate grid");
                        coordinateValues.add(observed);
                    }
                    coordinateRows.add(coordinateValues);
                }
                if (sharedCoordinates == null) sharedCoordinates = coordinateRows;
                unitReadback.put(groupName, interp.getString("unit"));
                for (int expressionIndex = 0; expressionIndex < groupExpressions.size(); expressionIndex++) {
                    if (real[expressionIndex] == null || real[expressionIndex].length != 1
                            || real[expressionIndex][0].length != rawCoordinates.size()
                            || (imaginary != null && (imaginary.length != groupExpressions.size()
                                || imaginary[expressionIndex] == null
                                || imaginary[expressionIndex].length != 1
                                || imaginary[expressionIndex][0].length != rawCoordinates.size())))
                        throw new IllegalStateException("raw-field solution/point axes differ from their contract");
                    List<Double> realValues = new ArrayList<>(), imaginaryValues = new ArrayList<>();
                    for (int point = 0; point < rawCoordinates.size(); point++) {
                        double rv = real[expressionIndex][0][point];
                        double iv = imaginary == null ? 0.0 : imaginary[expressionIndex][0][point];
                        if (!Double.isFinite(rv) || !Double.isFinite(iv)
                                || ("normal".equals(groupName) && Math.abs(iv) > 1e-10))
                            throw new IllegalStateException("raw field is nonfinite or its surface normal is complex");
                        realValues.add(rv);
                        imaginaryValues.add(iv);
                    }
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("expression", groupExpressions.get(expressionIndex));
                    row.put("real", realValues);
                    row.put("imag", imaginaryValues);
                    row.put("unit_group", groupName);
                    row.put("unit", interp.getString("unit"));
                    rows.add(row);
                }
            }
        } catch (Throwable error) {
            operationFailure = error;
        } finally {
            for (String ownedTag : tags) {
                try {
                    if (containsNumerical(model, ownedTag)) model.result().numerical().remove(ownedTag);
                    if (containsNumerical(model, ownedTag))
                        throw new IllegalStateException("request-owned raw-field Interp remains after cleanup: " + ownedTag);
                } catch (Throwable error) {
                    if (cleanupFailure == null) cleanupFailure = error;
                    else cleanupFailure.addSuppressed(error);
                }
            }
        }
        Map<String, Object> cleanup = new LinkedHashMap<>();
        cleanup.put("created_count", created.size());
        cleanup.put("expected_count", tags.size());
        cleanup.put("removed", cleanupFailure == null);
        cleanup.put("cleanup_failed", cleanupFailure != null);
        cleanup.put("tags", tags);
        cleanup.put("error", cleanupFailure == null ? "" : cleanupFailure.toString());
        if (operationFailure != null)
            throw new IllegalStateException("native raw field extraction failed; cleanup=" + cleanup, operationFailure);
        if (cleanupFailure != null)
            throw new IllegalStateException("native raw-field Interp cleanup failed", cleanupFailure);
        Map<String, Object> cohortAfter = rawSourceCohortSnapshot(
                model, datasetTag, solutionTag, outer, inner, solnum, selectionTag);
        if (!cohortBefore.equals(cohortAfter))
            throw new IllegalStateException("native source-cohort identity/configuration changed during read-only field sampling");
        if (sharedCoordinates == null || unitReadback.size() != 3 || rows.size() != expressions.size())
            throw new IllegalStateException("native E/H/normal unit groups are incomplete");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("native_result", "COMSOL_NATIVE_RAW");
        result.put("study_or_solver_invoked", false);
        result.put("complex_readback", true);
        result.put("units_preserved", true);
        result.put("contract_id", contractId);
        result.put("quadrature_sha256", contract.get("quadrature_sha256"));
        result.put("source", source);
        result.put("plane", plane);
        result.put("sample_count", rawCoordinates.size());
        result.put("coordinates_m", sharedCoordinates);
        result.put("unit_readback", unitReadback);
        result.put("expressions", rows);
        result.put("source_cohort", Map.of(
                "schema_id", "urn:comsol-mcp:w23:source-cohort-snapshot:1.0.0",
                "native_result", "COMSOL_NATIVE_SOURCE_COHORT_SNAPSHOTS",
                "before", cohortBefore, "after", cohortAfter));
        result.put("cleanup", cleanup);
        return result;
    }

    private static Map<String, Object> rawSourceCohortSnapshot(
            Model model, String datasetTag, String solutionTag, int outer, int inner,
            int solnum, String selectionTag) {
        if (!Arrays.asList(model.result().dataset().tags()).contains(datasetTag)
                || !Arrays.asList(model.sol().tags()).contains(solutionTag))
            throw new IllegalStateException("native source cohort dataset or solver sequence is absent");
        PropFeature dataset = model.result().dataset(datasetTag);
        if (!"Solution".equals(dataset.getType()) || !dataset.hasProperty("solution")
                || !solutionTag.equals(dataset.getString("solution")))
            throw new IllegalStateException("native source cohort dataset-to-solution identity is incomplete");
        Map<String, Object> datasetIdentity = new LinkedHashMap<>();
        datasetIdentity.put("tag", datasetTag);
        datasetIdentity.put("feature_type", dataset.getType());
        Map<String, String> datasetProperties = new LinkedHashMap<>();
        for (String property : new String[]{"solution", "data", "solnum", "outersolnum"})
            if (dataset.hasProperty(property)) datasetProperties.put(property, dataset.getString(property));
        datasetIdentity.put("properties", datasetProperties);

        SolverSequence sequence = model.sol(solutionTag);
        String studyTag = sequence.study();
        if (studyTag == null || studyTag.trim().isEmpty()
                || !Arrays.asList(model.study().tags()).contains(studyTag))
            throw new IllegalStateException("native stored solution does not resolve to its producer Study");
        Study producerStudy = model.study(studyTag);
        long computationDate = producerStudy.getLastComputationDate();
        String computationVersion = producerStudy.getLastComputationVersion();
        if (computationDate <= 0 || computationVersion == null || computationVersion.trim().isEmpty())
            throw new IllegalStateException("native stored solution computation date/version identity is unavailable");
        Map<String, Object> solutionReadback = solutionState(sequence, solutionTag);
        Object rawPairs = solutionReadback.get("solution_pairs");
        Map<String, Object> selectedTuple = null;
        if (rawPairs instanceof List) {
            for (Object rawPair : (List<?>) rawPairs) {
                if (!(rawPair instanceof Map)) continue;
                Map<?, ?> pair = (Map<?, ?>) rawPair;
                if (Integer.valueOf(outer).equals(pair.get("outer_index"))
                        && Integer.valueOf(inner).equals(pair.get("inner_index"))
                        && Integer.valueOf(solnum).equals(pair.get("solnum"))
                        && solutionTag.equals(pair.get("solver_sequence_tag")))
                    selectedTuple = new LinkedHashMap<>((Map<String, Object>) rawPair);
            }
        }
        if (solutionReadback.get("is_valid") != Boolean.TRUE
                || solutionReadback.get("solver_sequence_is_empty") != Boolean.FALSE
                || selectedTuple == null)
            throw new IllegalStateException("native source cohort lacks the exact selected SolutionInfo tuple");
        String[] parameterNames = sequence.getPNames();
        double[] parameterValues = sequence.getPVals();
        if (parameterNames == null || parameterValues == null || parameterNames.length != parameterValues.length)
            throw new IllegalStateException("native stored-solution parameter axes are unavailable or inconsistent");
        List<Map<String, Object>> parameterAxis = new ArrayList<>();
        for (int index = 0; index < parameterNames.length; index++) {
            if (parameterNames[index] == null || parameterNames[index].trim().isEmpty()
                    || !Double.isFinite(parameterValues[index]))
                throw new IllegalStateException("native stored-solution parameter axis is malformed");
            parameterAxis.add(Map.of("name", parameterNames[index], "value", parameterValues[index]));
        }
        Map<String, Object> storedSolution = new LinkedHashMap<>();
        storedSolution.put("solution_tag", solutionTag);
        storedSolution.put("study_tag", studyTag);
        storedSolution.put("computation_date_ms", computationDate);
        storedSolution.put("computation_version", computationVersion);
        storedSolution.put("parameter_axis", parameterAxis);
        storedSolution.put("solution_info", solutionReadback);
        storedSolution.put("selected_tuple", selectedTuple);

        String[] parameterTags = {"lambda0", "f0", "w23Ncore", "w23Nclad", "w23Nlens",
            "w23CoreR", "w23CladR", "w23LensR", "w23AirHalfY", "w23AirHalfZ", "w23PmlT",
            "w23XIn", "w23XInEnd", "w23XOutStart", "w23XOut", "w23XDomainMax",
            "w23OutDy", "w23OutDz", "w23ThetaY", "w23ThetaZ"};
        Map<String, String> parameters = new LinkedHashMap<>();
        for (String parameter : parameterTags) {
            String value = model.param().get(parameter);
            if (value == null || value.trim().isEmpty())
                throw new IllegalStateException("explicit fixture parameter readback is missing: " + parameter);
            parameters.put(parameter, value);
        }
        GeomSequence geometry = model.component(COMPONENT).geom(GEOMETRY);
        Map<String, Object> geometryReadback = new LinkedHashMap<>();
        geometryReadback.put("dimension", 3);
        geometryReadback.put("length_unit", geometry.lengthUnit());
        geometryReadback.put("domain_count", geometry.getNDomains());
        geometryReadback.put("bounding_box", boxed(geometry.getBoundingBox()));

        if (!Arrays.asList(model.component(COMPONENT).mesh().tags()).contains("mesh3d"))
            throw new IllegalStateException("fixture mesh3d is missing from source-cohort snapshot");
        PropFeature size = model.component(COMPONENT).mesh("mesh3d").feature("size");
        Map<String, Object> meshReadback = new LinkedHashMap<>();
        meshReadback.put("tag", "mesh3d");
        meshReadback.put("geometry", GEOMETRY);
        meshReadback.put("elements", model.component(COMPONENT).mesh("mesh3d").getNumElem());
        meshReadback.put("size_properties", propertyReadback(size, new String[]{"custom", "hmax", "hmin"}));

        List<Map<String, Object>> materials = new ArrayList<>();
        String[] materialTags = model.material().tags();
        Arrays.sort(materialTags);
        for (String tag : materialTags) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tag);
            row.put("feature_type", model.material(tag).getType());
            row.put("selection_tag", model.material(tag).selection().named());
            row.put("domain_ids", intList(model.material(tag).selection().entities(3)));
            row.put("relative_permittivity", stringMatrixRows(
                    model.material(tag).propertyGroup("def").getStringMatrix("relpermittivity")));
            row.put("relative_permeability", stringMatrixRows(
                    model.material(tag).propertyGroup("def").getStringMatrix("relpermeability")));
            materials.add(row);
        }
        Physics physics = model.component(COMPONENT).physics("ewfd");
        List<Map<String, Object>> physicsFeatures = new ArrayList<>();
        for (String tag : physics.feature().tags()) {
            PhysicsFeature feature = physics.feature(tag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tag);
            row.put("feature_type", feature.getType());
            row.put("selection_tag", feature.selection().named());
            row.put("selection_dimension", feature.selection().dim());
            row.put("selection_ids", intList(feature.selection().entities()));
            row.put("property_names", Arrays.asList(feature.properties()));
            if ("Port".equals(feature.getType()))
                row.put("port_properties", propertyReadback(feature, new String[]{"PortType", "PortName",
                    "PortExcitation", "PortModeNumber", "Pin", "Thetap", "PortOrientation"}));
            physicsFeatures.add(row);
        }

        Map<String, Object> selections = new LinkedHashMap<>();
        for (String tag : new String[]{"sel3dInputPort", "sel3dOutputPort", "sel3dOutputCoreCapture",
                "geom3d_coreIn_dom", "geom3d_rotCoreOutZ_dom", "geom3d_cladShellIn_dom",
                "geom3d_rotCladShellOutZ_dom", "geom3d_lensBall_dom", "geom3d_airRemainder_dom",
                "geom3d_pmlShell_dom"}) {
            if (!Arrays.asList(model.component(COMPONENT).selection().tags()).contains(tag))
                throw new IllegalStateException("fixture selection is missing from source-cohort snapshot: " + tag);
            SelectionFeature item = model.component(COMPONENT).selection(tag);
            int dimension = item.getInt("entitydim");
            selections.put(tag, Map.of("entity_dimension", dimension,
                    "entity_ids", intList(item.entities(dimension))));
        }
        Map<String, Object> studies = new LinkedHashMap<>();
        String[] studyTags = model.study().tags();
        Arrays.sort(studyTags);
        for (String tag : studyTags) studies.put(tag, studyStepReadback(model, tag));
        Map<String, Object> pml = propertyReadback(
                model.component(COMPONENT).coordSystem("pmlYZ"),
                new String[]{"ScalingType", "stretchingType", "typicalWavelength"});

        Map<String, Object> fixtureConfiguration = new LinkedHashMap<>();
        fixtureConfiguration.put("schema_id", "urn:comsol-mcp:w23:fixture-explicit-config:1.0.0");
        fixtureConfiguration.put("fixture_id", FIXTURE_ID);
        fixtureConfiguration.put("parameters", parameters);
        fixtureConfiguration.put("geometry", geometryReadback);
        fixtureConfiguration.put("mesh", meshReadback);
        fixtureConfiguration.put("materials", materials);
        fixtureConfiguration.put("physics", Map.of("tag", "ewfd", "feature_type", physics.getType(),
                "features", physicsFeatures));
        fixtureConfiguration.put("pml", pml);
        fixtureConfiguration.put("selections", selections);
        fixtureConfiguration.put("study_steps", studies);
        return Map.of("dataset", datasetIdentity, "stored_solution", storedSolution,
                "fixture_explicit_configuration", fixtureConfiguration);
    }

    private static List<List<String>> stringMatrixRows(String[][] values) {
        if (values == null) throw new IllegalStateException("material matrix readback is unavailable");
        List<List<String>> rows = new ArrayList<>();
        for (String[] row : values) {
            if (row == null) throw new IllegalStateException("material matrix contains a missing row");
            List<String> cells = new ArrayList<>();
            for (String value : row) {
                if (value == null || value.trim().isEmpty())
                    throw new IllegalStateException("material matrix contains an empty value");
                cells.add(value);
            }
            rows.add(cells);
        }
        return rows;
    }

    private static Map<String, Object> save(Model model, Map<String, Object> args) {
        if (args == null) throw new IllegalArgumentException("save phase arguments are required");
        String path = nonemptyString(args.get("path"), "path");
        File target = new File(path).getAbsoluteFile();
        if (target.exists()) throw new IllegalStateException("refusing to overwrite a preexisting managed MPH");
        File parent = target.getParentFile();
        if (parent == null || !parent.isDirectory())
            throw new IllegalArgumentException("managed MPH parent must be an existing registered project directory");
        try {
            model.save(target.getAbsolutePath());
        } catch (IOException error) {
            throw new IllegalStateException("native Model.save failed for the registered workspace path", error);
        }
        if (!target.isFile() || target.length() <= 0)
            throw new IllegalStateException("Model.save returned without a nonempty managed MPH");
        return Map.of("status", "MODEL_SAVED_TO_REGISTERED_PROJECT_WORKSPACE",
                "path", target.getAbsolutePath(), "bytes", target.length(),
                "fixture_id", FIXTURE_ID,
                "native_result", "COMSOL_NATIVE_SAVE_READBACK",
                "solve_history", "NOT_REPORTED_BY_SAVE_PHASE");
    }

    private static boolean containsNumerical(Model model, String tag) {
        return Arrays.asList(model.result().numerical().tags()).contains(tag);
    }

    private static Map<String, Object> requireMap(Object value, String label) {
        if (!(value instanceof Map)) throw new IllegalArgumentException(label + " must be an object");
        return (Map<String, Object>) value;
    }

    private static List<String> requireStringList(Object value, String label) {
        if (!(value instanceof List)) throw new IllegalArgumentException(label + " must be a list");
        List<String> result = new ArrayList<>();
        for (Object item : (List<?>) value) {
            if (!(item instanceof String) || ((String) item).trim().isEmpty())
                throw new IllegalArgumentException(label + " must contain nonempty strings");
            result.add((String) item);
        }
        return result;
    }

    private static int requiredPositiveIndex(Object value, String label) {
        if (!(value instanceof Number) || value instanceof Boolean
                || ((Number) value).doubleValue() != ((Number) value).intValue()
                || ((Number) value).intValue() < 1)
            throw new IllegalArgumentException(label + " must be a positive exact integer");
        return ((Number) value).intValue();
    }

    private static String requiredTag(Object value, String label) {
        String tag = nonemptyString(value, label);
        if (!tag.matches("[A-Za-z][A-Za-z0-9_]{0,62}"))
            throw new IllegalArgumentException(label + " is not an exact native tag");
        return tag;
    }

    private static void block(GeomSequence geom, String tag, String sx, String sy, String sz,
                              String px, String py, String pz) {
        geom.create(tag, "Block");
        geom.feature(tag).set("size", new String[]{sx, sy, sz});
        geom.feature(tag).set("base", "corner");
        geom.feature(tag).set("pos", new String[]{px, py, pz});
    }

    private static void cylinder(GeomSequence geom, String tag, String radius, String height, String position) {
        geom.create(tag, "Cylinder");
        geom.feature(tag).set("r", radius);
        geom.feature(tag).set("h", height);
        geom.feature(tag).set("pos", position.split(" "));
        geom.feature(tag).set("axis", new String[]{"1", "0", "0"});
        resultSelection(geom, tag);
    }

    private static void rotate(GeomSequence geom, String tag, String input, String[] axis,
                               String angleParameter) {
        geom.create(tag, "Rotate");
        geom.feature(tag).selection("input").set(new String[]{input});
        geom.feature(tag).set("specify", "axis");
        geom.feature(tag).set("specifypoint", "coord");
        geom.feature(tag).set("axistype", "cartesian");
        geom.feature(tag).set("axis", axis);
        geom.feature(tag).set("pos", new String[]{"w23XOutStart", "w23OutDy", "w23OutDz"});
        geom.feature(tag).set("rot", new String[]{angleParameter});
        geom.feature(tag).set("keep", "off");
        geom.feature(tag).set("propagatesel", "on");
        resultSelection(geom, tag);
    }

    private static void sphere(GeomSequence geom, String tag, String radius, String position) {
        geom.create(tag, "Sphere");
        geom.feature(tag).set("r", radius);
        geom.feature(tag).set("pos", position.split(" "));
        resultSelection(geom, tag);
    }

    private static void difference(GeomSequence geom, String tag, String add, String subtract,
                                   boolean keepSubtract) {
        geom.create(tag, "Difference");
        geom.feature(tag).selection("input").set(new String[]{add});
        geom.feature(tag).selection("input2").set(new String[]{subtract});
        geom.feature(tag).set("keepsubtract", keepSubtract ? "on" : "off");
        resultSelection(geom, tag);
    }

    private static void resultSelection(GeomSequence geom, String tag) {
        geom.feature(tag).set("selresult", "on");
        geom.feature(tag).set("selresultshow", "dom");
    }

    private static SelectionFeature box(Model model, String tag, double xmin, double xmax,
                                        double ymin, double ymax, double zmin, double zmax) {
        SelectionFeature selection = model.component(COMPONENT).selection().create(tag, "Box");
        selection.set("entitydim", 2);
        selection.set("condition", "inside");
        selection.set("xmin", xmin); selection.set("xmax", xmax);
        selection.set("ymin", ymin); selection.set("ymax", ymax);
        selection.set("zmin", zmin); selection.set("zmax", zmax);
        return selection;
    }

    private static SelectionFeature localPortCylinder(Model model, String tag, double[] axis,
            double[] center, double nominalRadius, double selectionMargin) {
        SelectionFeature selection = model.component(COMPONENT).selection().create(tag, "Cylinder");
        selection.geom(GEOMETRY, 2);
        configureLocalPortCylinder(selection, axis, center, nominalRadius, selectionMargin);
        return selection;
    }

    private static SelectionFeature createDeformationTargetSelection(Model model,
            int[] coreIn, int[] coreOut, int[] cladIn, int[] cladOut, int[] lens) {
        SelectionFeature selection = model.component(COMPONENT).selection().create(
                "sel3dDeformationTarget", "Explicit");
        selection.geom(GEOMETRY);
        selection.set("entitydim", 3);
        int[] entities = concatenate(coreIn, coreOut, cladIn, cladOut, lens);
        selection.set(entities);
        verifyDeformationTargetSelection(selection, entities);
        return selection;
    }

    private static SelectionFeature refreshDeformationTargetSelection(Model model) {
        int[] coreIn = domainSelection(model, "geom3d_coreIn_dom");
        int[] coreOut = domainSelection(model, "geom3d_rotCoreOutZ_dom");
        int[] cladIn = domainSelection(model, "geom3d_cladShellIn_dom");
        int[] cladOut = domainSelection(model, "geom3d_rotCladShellOutZ_dom");
        int[] lens = domainSelection(model, "geom3d_lensBall_dom");
        int[] entities = concatenate(coreIn, coreOut, cladIn, cladOut, lens);
        SelectionFeature selection = model.component(COMPONENT).selection("sel3dDeformationTarget");
        selection.geom(GEOMETRY);
        selection.set("entitydim", 3);
        selection.clear();
        selection.set(entities);
        verifyDeformationTargetSelection(selection, entities);
        return selection;
    }

    private static void verifyDeformationTargetSelection(SelectionFeature selection, int[] expected) {
        if (selection.getInt("entitydim") != 3 || !GEOMETRY.equals(selection.geom())
                || !sameSet(expected, selection.entities(3)))
            throw new IllegalStateException("native deformation target selection differs from the generated optical material domains");
    }

    private static Map<String, Object> deformationTargetReadback(Model model, SelectionFeature selection) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("tag", selection.tag());
        result.put("evidence_scope", "COMSOL_NATIVE_GEOMETRY_READBACK");
        result.put("selection_type", "Explicit");
        result.put("component_tag", COMPONENT);
        result.put("geometry_tag", selection.geom());
        result.put("entity_dimension", selection.getInt("entitydim"));
        result.put("entity_ids", intList(selection.entities(3)));
        result.put("geometry_length_unit", model.component(COMPONENT).geom(GEOMETRY).lengthUnit());
        result.put("coordinate_frame", "spatial");
        result.put("coordinate_unit", "m");
        result.put("vector_basis", "global_xyz");
        result.put("selected_material_domains", Arrays.asList("core input", "core output",
                "cladding input", "cladding output", "ball lens"));
        result.put("excluded_domains", Arrays.asList("air remainder", "PML shell"));
        result.put("entity_ids_derived_from", "native geometry result selections after geometry build");
        result.put("deformation_mapping", "W21 source displacement field; no caller arrays");
        return result;
    }

    private static int[] concatenate(int[]... groups) {
        List<Integer> values = new ArrayList<>();
        Set<Integer> seen = new LinkedHashSet<>();
        for (int[] group : groups) {
            if (group == null || group.length == 0)
                throw new IllegalStateException("deformation target domain group is empty");
            for (int value : group) {
                if (value < 1 || !seen.add(value))
                    throw new IllegalStateException("deformation target domain IDs are invalid or overlapping");
                values.add(value);
            }
        }
        int[] result = new int[values.size()];
        for (int i = 0; i < result.length; i++) result[i] = values.get(i);
        return result;
    }

    private static boolean sameSet(int[] left, int[] right) {
        if (left == null || right == null || left.length != right.length) return false;
        int[] a = left.clone(), b = right.clone();
        Arrays.sort(a); Arrays.sort(b);
        return Arrays.equals(a, b);
    }

    private static void configureLocalPortCylinder(SelectionFeature selection, double[] axis,
            double[] center, double nominalRadius, double selectionMargin) {
        if (axis == null || axis.length != 3 || center == null || center.length != 3
                || !(nominalRadius > 0.0) || !Double.isFinite(selectionMargin) || selectionMargin < 0.0)
            throw new IllegalArgumentException("local receiver section needs a finite axis, center, and positive radius");
        double norm = Math.sqrt(axis[0]*axis[0] + axis[1]*axis[1] + axis[2]*axis[2]);
        if (!Double.isFinite(norm) || Math.abs(norm - 1.0) > 1e-10)
            throw new IllegalArgumentException("receiver selection axis must be a unit vector");
        double halfLength = PORT_SECTION_HALF_LENGTH_UM;
        double[] base = new double[]{center[0] - halfLength*axis[0],
                center[1] - halfLength*axis[1], center[2] - halfLength*axis[2]};
        selection.set("entitydim", 2);
        selection.set("condition", "inside");
        selection.set("axistype", "cartesian");
        selection.set("axis", axis);
        selection.set("pos", base);
        selection.set("bottom", 0.0);
        selection.set("top", 2.0*halfLength);
        selection.set("r", nominalRadius + selectionMargin);
        selection.set("rin", 0.0);
        if (selection.getInt("entitydim") != 2 || !"inside".equals(selection.getString("condition"))
                || !"cartesian".equals(selection.getString("axistype"))
                || Math.abs(selection.getDouble("bottom")) > 1e-15
                || Math.abs(selection.getDouble("top") - 2.0*halfLength) > 1e-12
                || Math.abs(selection.getDouble("r") - (nominalRadius + selectionMargin)) > 1e-12)
            throw new IllegalStateException("local output port Cylinder selection readback differs from the registered section");
        requireVectorNear(selection.getDoubleArray("axis"), axis, 1e-10, "local section axis");
        requireVectorNear(selection.getDoubleArray("pos"), base, 1e-10, "local section base point");
    }

    private static PortFrame casePortFrame(String factor, double value) {
        double startX = 5.0, dy = 0.0, dz = 0.0, radius = 2.5, coreRadius = 1.2,
                thetaY = 0.0, thetaZ = 0.0;
        if ("receiver_gap_x_um".equals(factor)) startX += value;
        else if ("receiver_dy_um".equals(factor)) dy = value;
        else if ("receiver_dz_um".equals(factor)) dz = value;
        else if ("receiver_theta_y_deg".equals(factor)) thetaY = value;
        else if ("receiver_theta_z_deg".equals(factor)) thetaZ = value;
        else if ("cladding_radius_relative".equals(factor)) radius *= (1.0 + value);
        else if ("core_radius_relative".equals(factor)) coreRadius *= (1.0 + value);
        double ry = Math.toRadians(thetaY), rz = Math.toRadians(thetaZ);
        double[] axis = new double[]{Math.cos(rz)*Math.cos(ry), Math.sin(rz)*Math.cos(ry), -Math.sin(ry)};
        double length = 20.0 - startX;
        double[] center = new double[]{startX + length*axis[0], dy + length*axis[1], dz + length*axis[2]};
        return new PortFrame(new double[]{startX, dy, dz}, axis, center, radius, coreRadius, thetaY, thetaZ);
    }

    private static void verifyReceiverFrame(Object raw, PortFrame frame) {
        if (!(raw instanceof Map)) throw new IllegalArgumentException("registered receiver transform is required");
        @SuppressWarnings("unchecked") Map<String, Object> expected = (Map<String, Object>) raw;
        if (!"right-handed global +y then +z".equals(expected.get("rotation_order")))
            throw new IllegalArgumentException("receiver rotation order differs from the frozen transform");
        requireVectorNear(requiredDoubleVector(expected.get("pivot_xyz_um"), "receiver pivot"),
                frame.pivot, 1e-10, "receiver pivot");
        requireVectorNear(requiredDoubleVector(expected.get("axis_xyz"), "receiver axis"),
                frame.axis, 1e-10, "receiver axis");
        requireVectorNear(requiredDoubleVector(expected.get("center_xyz_um"), "receiver center"),
                frame.center, 1e-9, "receiver center");
        requireNear(requiredFiniteNumber(expected.get("cladding_radius_um"), "cladding radius"),
                frame.claddingRadius, 1e-10, "receiver cladding radius");
        requireNear(requiredFiniteNumber(expected.get("theta_y_deg"), "theta_y"),
                frame.thetaY, 1e-10, "receiver theta_y");
        requireNear(requiredFiniteNumber(expected.get("theta_z_deg"), "theta_z"),
                frame.thetaZ, 1e-10, "receiver theta_z");
        requireNear(requiredFiniteNumber(expected.get("selection_half_length_um"), "selection half length"),
                PORT_SECTION_HALF_LENGTH_UM, 1e-10, "receiver selection half length");
        requireNear(requiredFiniteNumber(expected.get("selection_radial_margin_um"), "selection radial margin"),
                PORT_SELECTION_RADIAL_MARGIN_UM, 1e-10, "receiver selection radial margin");
    }

    private static double[] requiredDoubleVector(Object raw, String label) {
        if (!(raw instanceof List) || ((List<?>) raw).size() != 3)
            throw new IllegalArgumentException(label + " must have exactly three coordinates");
        List<?> input = (List<?>) raw;
        double[] values = new double[3];
        for (int i = 0; i < values.length; i++)
            values[i] = requiredFiniteNumber(input.get(i), label);
        return values;
    }

    private static double requiredFiniteNumber(Object raw, String label) {
        if (!(raw instanceof Number) || raw instanceof Boolean
                || !Double.isFinite(((Number) raw).doubleValue()))
            throw new IllegalArgumentException(label + " must be finite numeric data");
        return ((Number) raw).doubleValue();
    }

    private static void requireNear(double actual, double expected, double tolerance, String label) {
        if (!Double.isFinite(actual) || Math.abs(actual - expected) > tolerance)
            throw new IllegalStateException(label + " readback differs from the frozen receiver transform");
    }

    private static Map<String, Object> portSectionReadback(Model model, SelectionFeature selection,
            double[] expectedAxis, double[] expectedCenter, double nominalRadius,
            double selectionMargin) {
        int[] entities = selection.entities(2);
        if (entities == null || entities.length == 0)
            throw new IllegalStateException("local output Numeric-port section selected no native faces");
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        GeomMeasure measure = geom.measure();
        measure.selection().init(2);
        measure.selection().set("allDomains", entities);
        double area = measure.getArea();
        double[] bounds = measure.getBoundingBox();
        if (!Double.isFinite(area) || area <= 0.0 || bounds == null || bounds.length != 6)
            throw new IllegalStateException("native port-face area/bounds readback is invalid");
        double[] centroid = new double[]{(bounds[0] + bounds[1]) / 2.0,
                (bounds[2] + bounds[3]) / 2.0, (bounds[4] + bounds[5]) / 2.0};
        double[] selectionAxis = selection.getDoubleArray("axis");
        double[] selectionBase = selection.getDoubleArray("pos");
        double selectionBottom = selection.getDouble("bottom");
        double selectionTop = selection.getDouble("top");
        double[] selectionCenter = new double[3];
        for (int axis = 0; axis < selectionCenter.length; axis++)
            selectionCenter[axis] = selectionBase[axis]
                    + (selectionBottom + selectionTop) * 0.5 * selectionAxis[axis];
        double centroidError = Math.sqrt(square(centroid[0]-expectedCenter[0])
                + square(centroid[1]-expectedCenter[1]) + square(centroid[2]-expectedCenter[2]));
        if (centroidError > 0.005)
            throw new IllegalStateException("native local port-section AABB centroid differs from rigidly transformed cap center");
        double expectedArea = Math.PI * nominalRadius * nominalRadius;
        double relativeAreaError = Math.abs(area - expectedArea) / expectedArea;
        if (relativeAreaError > 0.03)
            throw new IllegalStateException("native output port face selection area differs by more than the frozen 3% geometry-measure tolerance");

        List<Map<String, Object>> faces = new ArrayList<>();
        int normalSign = 0;
        for (int entity : entities) {
            double[] range = geom.faceParamRange(entity);
            if (range == null || range.length != 4)
                throw new IllegalStateException("native output port face has no 2D parameter range");
            double[][] params = new double[][]{{(range[0]+range[1])/2.0, (range[2]+range[3])/2.0}};
            double[][] xyz = geom.faceX(entity, params);
            double[][] normal = geom.faceNormal(entity, params);
            if (xyz == null || xyz.length != 1 || xyz[0].length != 3
                    || normal == null || normal.length != 1 || normal[0].length != 3)
                throw new IllegalStateException("native output port face point/normal readback has unexpected shape");
            double normalNorm = Math.sqrt(normal[0][0]*normal[0][0] + normal[0][1]*normal[0][1]
                    + normal[0][2]*normal[0][2]);
            if (!Double.isFinite(normalNorm) || normalNorm == 0.0)
                throw new IllegalStateException("native output port face has a nonfinite/zero normal");
            double[] unitNormal = new double[]{normal[0][0]/normalNorm, normal[0][1]/normalNorm,
                    normal[0][2]/normalNorm};
            double dot = unitNormal[0]*expectedAxis[0] + unitNormal[1]*expectedAxis[1]
                    + unitNormal[2]*expectedAxis[2];
            if (Math.abs(Math.abs(dot) - 1.0) > 1e-5)
                throw new IllegalStateException("native output port face normal is not parallel to the transformed receiver axis");
            int sign = dot >= 0.0 ? 1 : -1;
            if (normalSign != 0 && sign != normalSign)
                throw new IllegalStateException("selected port faces do not share one native normal orientation");
            normalSign = sign;
            faces.add(Map.of("boundary_id", entity, "point_um", boxed(xyz[0]), "unit_normal_xyz", boxed(unitNormal),
                    "axis_dot", dot));
        }
        Map<String, Object> output = new LinkedHashMap<>();
        output.put("tag", selection.tag());
        output.put("evidence_scope", "COMSOL_NATIVE_GEOMETRY_READBACK");
        output.put("selection_type", "Cylinder");
        output.put("entity_dimension", 2);
        output.put("entity_ids", intList(entities));
        output.put("selection_geometry", selection.selection().geom());
        output.put("selection_condition", selection.getString("condition"));
        output.put("selection_axis_type", selection.getString("axistype"));
        output.put("selection_axis_xyz", boxed(selectionAxis));
        output.put("selection_center_um", boxed(selectionCenter));
        output.put("selection_base_um", boxed(selectionBase));
        output.put("selection_radius_um", selection.getDouble("r"));
        output.put("nominal_aperture_radius_um", nominalRadius);
        output.put("selection_margin_um", selectionMargin);
        output.put("selection_bottom_um", selectionBottom);
        output.put("selection_top_um", selectionTop);
        output.put("area_um2", area);
        output.put("expected_circle_area_um2", expectedArea);
        output.put("area_relative_error", relativeAreaError);
        output.put("area_tolerance", "3% geometry-measure approximation gate");
        output.put("centroid_um", boxed(centroid));
        output.put("expected_transformed_center_um", boxed(expectedCenter));
        output.put("centroid_error_um", centroidError);
        output.put("bounding_box_um", boxed(bounds));
        output.put("native_face_oriented_axis_sign", normalSign);
        output.put("faces", faces);
        output.put("coordinate_unit", "um");
        output.put("normal_basis", "global_xyz");
        if (Math.abs(selection.getDouble("r") - (nominalRadius + selectionMargin)) > 1e-10)
            throw new IllegalStateException("native aperture selection radius differs from nominal aperture plus its registered margin");
        return output;
    }

    /** Read back every selected input-port face normal and the selected area. */
    private static Map<String, Object> planeSelectionReadback(Model model,
            SelectionFeature selection, double[] expectedAxis) {
        int[] entities = selection.entities(2);
        if (entities == null || entities.length == 0)
            throw new IllegalStateException("native input Numeric-port selection has no 2-D boundaries");
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        GeomMeasure measure = geom.measure();
        measure.selection().init(2);
        measure.selection().set("allDomains", entities);
        double area = measure.getArea();
        double[] bounds = measure.getBoundingBox();
        if (!Double.isFinite(area) || area <= 0.0 || bounds == null || bounds.length != 6)
            throw new IllegalStateException("native input-port area/bounds readback is invalid");
        List<Map<String, Object>> faces = new ArrayList<>();
        int normalSign = 0;
        for (int entity : entities) {
            double[] range = geom.faceParamRange(entity);
            if (range == null || range.length != 4)
                throw new IllegalStateException("native input-port face lacks a 2-D parameter range");
            double[][] params = new double[][]{{(range[0]+range[1])/2.0, (range[2]+range[3])/2.0}};
            double[][] xyz = geom.faceX(entity, params);
            double[][] normal = geom.faceNormal(entity, params);
            if (xyz == null || xyz.length != 1 || xyz[0].length != 3
                    || normal == null || normal.length != 1 || normal[0].length != 3)
                throw new IllegalStateException("native input-port point/normal readback has an unexpected shape");
            double norm = Math.sqrt(normal[0][0]*normal[0][0] + normal[0][1]*normal[0][1]
                    + normal[0][2]*normal[0][2]);
            if (!Double.isFinite(norm) || norm <= 0.0)
                throw new IllegalStateException("native input-port boundary normal is nonfinite or zero");
            double[] unit = new double[]{normal[0][0]/norm, normal[0][1]/norm, normal[0][2]/norm};
            double dot = unit[0]*expectedAxis[0] + unit[1]*expectedAxis[1] + unit[2]*expectedAxis[2];
            if (Math.abs(Math.abs(dot) - 1.0) > 1e-6)
                throw new IllegalStateException("native input-port boundary normal is not parallel to +x");
            int sign = dot >= 0.0 ? 1 : -1;
            if (normalSign != 0 && sign != normalSign)
                throw new IllegalStateException("native input-port faces do not share one outward normal orientation");
            normalSign = sign;
            faces.add(Map.of("boundary_id", entity, "point_um", boxed(xyz[0]),
                    "unit_normal_xyz", boxed(unit), "axis_dot", dot));
        }
        double[] center = new double[]{(bounds[0]+bounds[1])/2.0,
                (bounds[2]+bounds[3])/2.0, (bounds[4]+bounds[5])/2.0};
        Map<String, Object> output = new LinkedHashMap<>();
        output.put("tag", selection.tag());
        output.put("evidence_scope", "COMSOL_NATIVE_GEOMETRY_READBACK");
        output.put("selection_type", "Box");
        output.put("selection_geometry", selection.selection().geom());
        output.put("selection_condition", selection.getString("condition"));
        output.put("entity_dimension", selection.getInt("entitydim"));
        output.put("entity_ids", intList(entities));
        output.put("area_um2", area);
        output.put("bounding_box_um", boxed(bounds));
        output.put("centroid_um", boxed(center));
        output.put("native_face_oriented_axis_sign", normalSign);
        output.put("faces", faces);
        output.put("coordinate_unit", "um");
        output.put("normal_basis", "global_xyz");
        return output;
    }

    private static void requireVectorNear(double[] actual, double[] expected, double tolerance, String label) {
        if (actual == null || expected == null || actual.length != expected.length)
            throw new IllegalStateException(label + " readback dimension differs");
        for (int i = 0; i < actual.length; i++)
            if (!Double.isFinite(actual[i]) || Math.abs(actual[i] - expected[i]) > tolerance)
                throw new IllegalStateException(label + " readback differs from the frozen rigid transform");
    }

    private static double square(double value) { return value * value; }

    private static List<Double> boxed(double[] values) {
        List<Double> result = new ArrayList<>();
        if (values != null) for (double value : values) result.add(value);
        return result;
    }

    private static final class PortFrame {
        final double[] pivot;
        final double[] axis;
        final double[] center;
        final double claddingRadius;
        final double coreRadius;
        final double thetaY;
        final double thetaZ;
        PortFrame(double[] pivot, double[] axis, double[] center, double claddingRadius,
                  double coreRadius,
                  double thetaY, double thetaZ) {
            this.pivot = pivot; this.axis = axis; this.center = center; this.claddingRadius = claddingRadius;
            this.coreRadius = coreRadius;
            this.thetaY = thetaY; this.thetaZ = thetaZ;
        }
    }

    private static int[] domainSelection(Model model, String tag) {
        if (!Arrays.asList(model.component(COMPONENT).selection().tags()).contains(tag))
            throw new IllegalStateException("geometry result-domain selection is missing: " + tag);
        int[] ids = model.component(COMPONENT).selection(tag).entities(3);
        if (ids == null || ids.length == 0)
            throw new IllegalStateException("geometry result-domain selection is empty: " + tag);
        return ids;
    }

    private static void assertDisjoint(int[]... selections) {
        Set<Integer> all = new LinkedHashSet<>();
        for (int[] selection : selections) {
            for (int id : selection) {
                if (id < 1 || !all.add(id))
                    throw new IllegalStateException("material/PML domain selections are invalid or overlap");
            }
        }
    }

    private static void material(Model model, String tag, String selection, String eps) {
        model.material().create(tag, "Common", COMPONENT);
        model.material(tag).selection().named(selection);
        String[][] epsilon = {{eps, "0", "0"}, {"0", eps, "0"}, {"0", "0", eps}};
        String[][] mu = {{"1", "0", "0"}, {"0", "1", "0"}, {"0", "0", "1"}};
        model.material(tag).propertyGroup("def").set("relpermittivity", epsilon);
        model.material(tag).propertyGroup("def").set("relpermeability", mu);
    }

    private static void configureNumericPort(PhysicsFeature port, String number, boolean excited) {
        port.set("PortType", "Numeric");
        port.set("PortName", number);
        port.set("PortExcitation", excited ? "on" : "off");
        port.set("PortOrientation", "ForwardPort");
        port.set("PortModeNumber", 1);
        port.set("Thetap", "0[deg]");
        if (excited) port.set("Pin", "1[W]");
    }

    private static void configureBma(StudyFeature feature, String port) {
        feature.set("PortName", port);
        feature.set("modeFreq", "f0");
        feature.set("neigs", 2);
        feature.set("eigwhich", "effective_mode_index");
        feature.set("shiftactive", "on");
        feature.set("shift", "1.45");
    }

    private static Map<String, Object> selectionReadback(String tag, int[] entities) {
        return Map.of("tag", tag, "entity_dimension", 2, "entity_ids", intList(entities),
                      "entity_count", entities == null ? 0 : entities.length);
    }

    private static List<Integer> intList(int[] values) {
        List<Integer> output = new ArrayList<>();
        if (values != null) for (int value : values) output.add(value);
        return output;
    }

    private static Map<String, Object> propertyReadback(ParameterEntity entity, String[] keys) {
        Map<String, Object> output = new LinkedHashMap<>();
        output.put("properties", Arrays.asList(entity.properties()));
        Map<String, Object> requested = new LinkedHashMap<>();
        for (String key : keys) {
            Map<String, Object> row = new LinkedHashMap<>();
            boolean present = entity.hasProperty(key);
            row.put("has_property_exact", present);
            if (present) {
                try { row.put("string_readback", entity.getString(key)); }
                catch (Throwable error) { row.put("readback_error", error.getClass().getName()); }
                try { row.put("allowed_values", entity.getAllowedPropertyValues(key)); }
                catch (Throwable error) { row.put("allowed_values_error", error.getClass().getName()); }
            }
            requested.put(key, row);
        }
        output.put("requested_properties", requested);
        return output;
    }

    private static Map<String, Object> propertyReadback(PropFeature entity, String[] keys) {
        Map<String, Object> output = new LinkedHashMap<>();
        output.put("properties", Arrays.asList(entity.properties()));
        Map<String, Object> requested = new LinkedHashMap<>();
        for (String key : keys) {
            Map<String, Object> row = new LinkedHashMap<>();
            boolean present = entity.hasProperty(key);
            row.put("has_property_exact", present);
            if (present) {
                try { row.put("string_readback", entity.getString(key)); }
                catch (Throwable error) { row.put("readback_error", error.getClass().getName()); }
                try { row.put("allowed_values", entity.getAllowedPropertyValues(key)); }
                catch (Throwable error) { row.put("allowed_values_error", error.getClass().getName()); }
            }
            requested.put(key, row);
        }
        output.put("requested_properties", requested);
        return output;
    }
}
