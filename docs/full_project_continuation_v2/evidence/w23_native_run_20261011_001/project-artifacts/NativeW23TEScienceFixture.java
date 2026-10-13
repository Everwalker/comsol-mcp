import com.comsol.model.Coordsys;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.NumericalFeature;
import com.comsol.model.ParameterEntity;
import com.comsol.model.PropFeature;
import com.comsol.model.SelectionFeature;
import com.comsol.model.StudyFeature;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverSequence;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.PhysicsField;
import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * W23 first real science fixture: 2-D reciprocal planar TE guide.
 *
 * This class builds/configures the model but never calls Study.run or a solver.
 * Solver submissions are made separately through the managed study.run route.
 * The x=8 um receiver is the science plane; x=10 um remains only the API
 * preflight's temporary outside boundary and is not used here.
 */
public final class NativeW23TEScienceFixture {
    private static final String FIXTURE_ID = "w23_planar_te_numeric_port_science_v1";
    private static final String[] RAW_FIELDS = {
        "ewfd.Ex", "ewfd.Ey", "ewfd.Ez", "ewfd.Hx", "ewfd.Hy", "ewfd.Hz",
        "ewfd.Emodex_2", "ewfd.Emodey_2", "ewfd.Emodez_2",
        "ewfd.Hmodex_2", "ewfd.Hmodey_2", "ewfd.Hmodez_2",
        "ewfd.Emodex_1", "ewfd.Emodey_1", "ewfd.Emodez_1",
        "ewfd.Hmodex_1", "ewfd.Hmodey_1", "ewfd.Hmodez_1",
        "ewfd.neff_1", "ewfd.neff_2", "nx", "ny"
    };

    private NativeW23TEScienceFixture() {}

    public static Object run(Model model, Map<String, Object> args) {
        if (model == null) throw new IllegalArgumentException("model is required");
        String phase = args == null ? "" : String.valueOf(args.get("phase"));
        if ("build".equals(phase)) return build(model);
        if ("set_phase".equals(phase)) return setNativePhase(model, args);
        if ("set_mesh".equals(phase)) return setMesh(model, args);
        if ("save".equals(phase)) return save(model, args);
        if ("identity".equals(phase)) return modelIdentity(model);
        if ("solution_inventory".equals(phase)) return solutionInventory(model);
        if ("raw_fields".equals(phase)) return rawFields(model, args);
        throw new IllegalArgumentException("phase must be build, set_phase, set_mesh, save, identity, solution_inventory, or raw_fields");
    }

    private static Map<String, Object> build(Model model) {
        model.label("W23 2D TE Numeric-port science checkpoint - not solved");
        model.param().set("lambda0", "1.55[um]");
        model.param().set("f0", "193.414489032258[THz]");
        model.param().set("n_core", "1.60");
        model.param().set("n_clad", "1.45");
        model.param().set("h_core", "1[um]");
        model.param().set("x_in", "-10[um]");
        model.param().set("x_receiver", "8[um]");
        model.param().set("x_pml", "2[um]");
        model.param().set("y_extent", "8[um]");
        model.param().set("phi_in", "0[deg]");

        model.component().create("comp1");
        GeomSequence geom = model.component("comp1").geom().create("geom1", 2);
        geom.lengthUnit("um");
        rectangle(geom, "cladBottom", "x_in", "-y_extent", "x_receiver-x_in", "y_extent-h_core/2");
        rectangle(geom, "coreGuide", "x_in", "-h_core/2", "x_receiver-x_in", "h_core");
        rectangle(geom, "cladTop", "x_in", "h_core/2", "x_receiver-x_in", "y_extent-h_core/2");
        rectangle(geom, "cladBottomPml", "x_receiver", "-y_extent", "x_pml", "y_extent-h_core/2");
        rectangle(geom, "corePml", "x_receiver", "-h_core/2", "x_pml", "h_core");
        rectangle(geom, "cladTopPml", "x_receiver", "h_core/2", "x_pml", "y_extent-h_core/2");
        geom.run();

        SelectionFeature input = box(model, "selInputPort", 1, -10.001, -9.999, -8.001, 8.001);
        SelectionFeature receiver = box(model, "selReceiverX8", 1, 7.999, 8.001, -8.001, 8.001);
        SelectionFeature capture = box(model, "selCoreCaptureX8", 1, 7.999, 8.001, -0.501, 0.501);
        SelectionFeature core = box(model, "selCoreMaterial", 2, -10.001, 10.001, -0.501, 0.501);
        SelectionFeature claddingBottom = box(model, "selCladdingBottom", 2, -10.001, 10.001, -8.001, -0.499);
        SelectionFeature claddingTop = box(model, "selCladdingTop", 2, -10.001, 10.001, 0.499, 8.001);
        SelectionFeature pmlDomains = box(model, "selPmlDomains", 2, 7.999, 10.001, -8.001, 8.001);

        Coordsys pml = model.component("comp1").coordSystem().create("pmlX", "geom1", "PML");
        pml.selection().named("selPmlDomains");
        pml.set("ScalingType", "Cartesian");
        pml.set("stretchingType", "polynomial");
        pml.set("typicalWavelength", "lambda0");

        material(model, "matCore", "selCoreMaterial", "n_core^2");
        material(model, "matCladdingBottom", "selCladdingBottom", "n_clad^2");
        material(model, "matCladdingTop", "selCladdingTop", "n_clad^2");

        Physics ewfd = model.component("comp1").physics().create("ewfd", "ElectromagneticWaves", "geom1");
        ewfd.selection().all();
        ewfd.prop("components").set("components", "outofplane");
        PhysicsFeature inputPort = ewfd.feature().create("portIn", "Port", 1);
        PhysicsFeature outputPort = ewfd.feature().create("portOut", "Port", 1);
        inputPort.selection().named("selInputPort");
        outputPort.selection().named("selReceiverX8");
        configurePort(inputPort, "1", true, false);
        configurePort(outputPort, "2", false, true);

        model.study().create("std1");
        StudyFeature bmaInput = model.study("std1").feature().create("bmaInput", "BoundaryModeAnalysis");
        StudyFeature bmaOutput = model.study("std1").feature().create("bmaOutput", "BoundaryModeAnalysis");
        StudyFeature frequency = model.study("std1").feature().create("freq", "Frequency");
        configureBma(bmaInput, "1");
        configureBma(bmaOutput, "2");
        frequency.set("plist", "f0");

        model.component("comp1").mesh().create("mesh1", "geom1");
        model.component("comp1").mesh("mesh1").feature("size").set("custom", "on");
        model.component("comp1").mesh("mesh1").feature("size").set("hmax", "0.16[um]");
        model.component("comp1").mesh("mesh1").feature("size").set("hmin", "0.04[um]");
        model.component("comp1").mesh("mesh1").run();

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("fixture_id", FIXTURE_ID);
        result.put("status", "BUILT_CONFIGURED_NOT_SOLVED");
        result.put("study_or_solver_invoked", false);
        result.put("native_result", "NOT_RUN");
        result.put("science_plane", Map.of(
                "selection_tag", "selReceiverX8", "x_um", 8.0,
                "entity_dimension", 1, "is_science_receiver", true,
                "api_only_x10_boundary_reused", false));
        result.put("geometry", Map.of(
                "dimension", 2, "length_unit", geom.lengthUnit(),
                "normal_guide_x_um", List.of(-10.0, 8.0),
                "pml_x_um", List.of(8.0, 10.0), "core_y_um", List.of(-0.5, 0.5),
                "capture_aperture", Map.of("plane_x_um", 8.0, "y_um", List.of(-0.5, 0.5),
                        "selection_tag", "selCoreCaptureX8", "basis", "native core-only boundary entities"),
                "outer_y_um", List.of(-8.0, 8.0), "domain_count", geom.getNDomains(),
                "bounding_box", geom.getBoundingBox()));
        result.put("selections", List.of(
                selectionReadback("selInputPort", 1, input.entities(1), inputPort.selection().entities(1)),
                selectionReadback("selReceiverX8", 1, receiver.entities(1), outputPort.selection().entities(1)),
                selectionReadback("selCoreCaptureX8", 1, capture.entities(1), null),
                selectionReadback("selCoreMaterial", 2, core.entities(2), null),
                selectionReadback("selCladdingBottom", 2, claddingBottom.entities(2), null),
                selectionReadback("selCladdingTop", 2, claddingTop.entities(2), null),
                selectionReadback("selPmlDomains", 2, pmlDomains.entities(2), pml.selection().entities(2))));
        result.put("materials", List.of(
                materialReadback(model, "matCore", "selCoreMaterial", "n_core^2"),
                materialReadback(model, "matCladdingBottom", "selCladdingBottom", "n_clad^2"),
                materialReadback(model, "matCladdingTop", "selCladdingTop", "n_clad^2")));
        result.put("te_field_configuration", inspect(ewfd.prop("components"), new String[]{"components"}));
        result.put("ports", List.of(
                inspect(inputPort, new String[]{"PortType", "PortName", "PortExcitation", "Pin", "Thetap", "PortSlit", "SlitType", "PortOrientation"}),
                inspect(outputPort, new String[]{"PortType", "PortName", "PortExcitation", "Pin", "Thetap", "PortSlit", "SlitType", "PortOrientation"})));
        result.put("pml", inspect(pml, new String[]{"ScalingType", "stretchingType", "typicalWavelength"}));
        result.put("bma_steps", List.of(
                inspect(bmaInput, new String[]{"PortName", "modeFreq", "neigs", "eigwhich", "shiftactive", "shift"}),
                inspect(bmaOutput, new String[]{"PortName", "modeFreq", "neigs", "eigwhich", "shiftactive", "shift"})));
        result.put("frequency_step", inspect(frequency, new String[]{"plist"}));
        result.put("mesh", Map.of("tag", "mesh1", "hmax_um", 0.16, "hmin_um", 0.04,
                                   "elements", model.component("comp1").mesh("mesh1").getNumElem()));
        result.put("raw_field_variables", Arrays.asList(RAW_FIELDS));
        result.put("native_overlap_route", "result.mode_overlap through managed operation_call; no caller arrays");
        result.put("native_phase_control", Map.of("parameter", "phi_in", "property", "Thetap",
                "initial", "0[deg]", "second_case", "90[deg]", "study_runs_required", 2));
        result.put("port_reference_identity", Map.of("output_port_number", "2", "input_port_number", "1",
                "mode_axis_parameter", "modeIndex", "mode_index", 1));
        return result;
    }

    private static Map<String, Object> setNativePhase(Model model, Map<String, Object> args) {
        Object requested = args == null ? null : args.get("phase_value");
        if (!(requested instanceof String)) throw new IllegalArgumentException("phase_value must be an exact frozen string");
        String value = (String) requested;
        if (!"0[deg]".equals(value) && !"90[deg]".equals(value))
            throw new IllegalArgumentException("native phase control is frozen to 0[deg] or 90[deg]");
        model.param().set("phi_in", value);
        model.physics("ewfd").feature("portIn").set("Thetap", value);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("phase_value", value);
        result.put("port_thetap_readback", model.physics("ewfd").feature("portIn").getString("Thetap"));
        result.put("output_reference_phase_unchanged",
                "0[deg]".equals(model.physics("ewfd").feature("portOut").getString("Thetap")));
        result.put("output_reference_phase_readback", model.physics("ewfd").feature("portOut").getString("Thetap"));
        result.put("study_or_solver_invoked", false);
        return result;
    }

    private static Map<String, Object> setMesh(Model model, Map<String, Object> args) {
        Object requested = args == null ? null : args.get("mesh_level");
        if (!(requested instanceof String)) throw new IllegalArgumentException("mesh_level must be coarse or fine");
        String level = (String) requested;
        String hmax;
        if ("coarse".equals(level)) hmax = "0.16[um]";
        else if ("fine".equals(level)) hmax = "0.08[um]";
        else throw new IllegalArgumentException("mesh_level is frozen to coarse or fine");
        model.component("comp1").mesh("mesh1").feature("size").set("hmax", hmax);
        model.component("comp1").mesh("mesh1").run();
        String readback = model.component("comp1").mesh("mesh1").feature("size").getString("hmax");
        if (!hmax.equals(readback)) throw new IllegalStateException("mesh hmax readback differs from the frozen level");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("mesh_level", level);
        result.put("hmax", readback);
        result.put("hmin", model.component("comp1").mesh("mesh1").feature("size").getString("hmin"));
        result.put("elements", model.component("comp1").mesh("mesh1").getNumElem());
        result.put("study_or_solver_invoked", false);
        return result;
    }

    private static Map<String, Object> save(Model model, Map<String, Object> args) {
        Object rawPath = args == null ? null : args.get("path");
        if (!(rawPath instanceof String) || ((String) rawPath).isBlank())
            throw new IllegalArgumentException("save path is required");
        String path = (String) rawPath;
        File target = new File(path);
        if (target.exists()) throw new IllegalStateException("refusing to overwrite an existing model artifact");
        try {
            model.save(path);
        } catch (java.io.IOException error) {
            throw new IllegalStateException("COMSOL model save failed", error);
        }
        if (!target.isFile() || target.length() <= 0)
            throw new IllegalStateException("COMSOL model save returned without a nonempty file");
        return Map.of("path", target.getAbsolutePath(), "bytes", target.length(),
                      "study_or_solver_invoked", false, "fixture_id", FIXTURE_ID);
    }

    private static Map<String, Object> modelIdentity(Model model) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("solver_tags", Arrays.asList(model.sol().tags()));
        result.put("dataset_tags", Arrays.asList(model.result().dataset().tags()));
        result.put("model_tag", model.tag());
        result.put("geometry_domains", model.component("comp1").geom("geom1").getNDomains());
        result.put("receiver_entity_ids", safeIntList(model.component("comp1").selection("selReceiverX8").entities(1)));
        result.put("input_entity_ids", safeIntList(model.component("comp1").selection("selInputPort").entities(1)));
        return result;
    }

    private static Map<String, Object> solutionInventory(Model model) {
        Map<String, Object> result = new LinkedHashMap<>();
        List<Map<String, Object>> studySteps = new ArrayList<>();
        for (String stepTag : model.study("std1").feature().tags()) {
            StudyFeature feature = model.study("std1").feature(stepTag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", stepTag);
            row.put("feature_type", feature.getType());
            try {
                row.put("property_names", Arrays.asList(feature.properties()));
                for (String property : new String[]{"PortName", "modeFreq", "plist"}) {
                    if (feature.hasProperty(property)) row.put(property, feature.getString(property));
                }
            } catch (Throwable error) {
                row.put("readback_error", error.toString());
            }
            studySteps.add(row);
        }
        String[] solverSequenceTags = model.study("std1").getSolverSequences("SolverSequence");
        List<Map<String, Object>> solverSequences = new ArrayList<>();
        for (String sequenceTag : solverSequenceTags) {
            SolverSequence sequence = model.sol(sequenceTag);
            Map<String, Object> sequenceRow = new LinkedHashMap<>();
            sequenceRow.put("tag", sequenceTag);
            sequenceRow.put("feature_type", sequence.getType());
            List<Map<String, Object>> studyStepBindings = new ArrayList<>();
            List<Map<String, Object>> solverTree = new ArrayList<>();
            for (String featureTag : sequence.feature().tags()) {
                collectSolverFeature(sequence.feature(featureTag), featureTag,
                        solverTree, studyStepBindings);
            }
            sequenceRow.put("solver_tree_features", solverTree);
            sequenceRow.put("study_step_bindings_in_solver_tree_order", studyStepBindings);
            solverSequences.add(sequenceRow);
        }
        List<Map<String, Object>> datasets = new ArrayList<>();
        for (String tag : model.result().dataset().tags()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tag);
            row.put("feature_type", model.result().dataset(tag).getClass().getName());
            row.put("property_names", Arrays.asList(model.result().dataset(tag).properties()));
            for (String property : new String[]{"solution", "data", "solnum", "outersolnum"}) {
                try {
                    if (model.result().dataset(tag).hasProperty(property))
                        row.put(property, model.result().dataset(tag).getString(property));
                } catch (Throwable error) {
                    row.put(property + "_readback_error", error.toString());
                }
            }
            datasets.add(row);
        }
        result.put("fixture_id", FIXTURE_ID);
        result.put("study_steps_in_configured_order", studySteps);
        result.put("solver_sequences_for_study", solverSequences);
        result.put("solver_sequence_query_type", "SolverSequence");
        result.put("solver_execution_count", "UNKNOWN_WITHOUT_RUNTIME_SOLVER_EVIDENCE");
        result.put("solver_tags", Arrays.asList(model.sol().tags()));
        result.put("datasets", datasets);
        result.put("model_tag", model.tag());
        result.put("geometry_frame", Map.of("component", "comp1", "geometry", "geom1",
                "dimension", 2, "length_unit", model.component("comp1").geom("geom1").lengthUnit()));
        result.put("native_solution_parameter_values_required_before_overlap", true);
        return result;
    }

    private static void collectSolverFeature(SolverFeature feature, String path,
                                             List<Map<String, Object>> tree,
                                             List<Map<String, Object>> bindings) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("path", path);
        String type = feature.getType();
        row.put("feature_type", type);
        tree.add(row);
        if ("StudyStep".equals(type)) {
            Map<String, Object> binding = new LinkedHashMap<>();
            binding.put("path", path);
            binding.put("feature_type", type);
            for (String property : new String[]{"study", "studystep"}) {
                if (feature.hasProperty(property)) {
                    try {
                        binding.put(property, feature.getString(property));
                    } catch (Throwable error) {
                        binding.put(property + "_readback_error", error.toString());
                    }
                }
            }
            bindings.add(binding);
        }
        for (String childTag : feature.feature().tags()) {
            collectSolverFeature(feature.feature(childTag), path + "/" + childTag,
                    tree, bindings);
        }
    }

    private static Map<String, Object> rawFields(Model model, Map<String, Object> args) {
        String dataset = args == null ? null : String.valueOf(args.get("dataset"));
        String plane = args == null ? null : String.valueOf(args.get("plane"));
        Object sampleRaw = args == null ? null : args.get("samples");
        int samples = sampleRaw instanceof Number ? ((Number) sampleRaw).intValue() : -1;
        Object innerRaw = args == null ? null : args.get("inner_index");
        Object outerRaw = args == null ? null : args.get("outer_index");
        int innerIndex = innerRaw instanceof Number ? ((Number) innerRaw).intValue() : -1;
        int outerIndex = outerRaw instanceof Number ? ((Number) outerRaw).intValue() : -1;
        if (dataset == null || !dataset.matches("[A-Za-z][A-Za-z0-9_]{0,62}"))
            throw new IllegalArgumentException("dataset must be an exact native dataset tag");
        if (!("output_x8".equals(plane) || "input_xminus10".equals(plane)))
            throw new IllegalArgumentException("plane must be output_x8 or input_xminus10");
        if (samples != 321 && samples != 641)
            throw new IllegalArgumentException("the registered quadrature grids are exactly 321 or 641 samples");
        if (innerIndex != 1 || outerIndex != 1)
            throw new IllegalArgumentException("the fixture has exactly one frozen frequency solution at inner/outer index 1");
        // NumericalFeature Interp coordinates are global coordinates in SI
        // metres even though geom1 is presented in micrometres.
        double x = "output_x8".equals(plane) ? 8e-6 : -10e-6;
        double[][] coordinates = new double[2][samples];
        for (int i = 0; i < samples; i++) {
            coordinates[0][i] = x;
            coordinates[1][i] = -8e-6 + 16e-6 * i / (samples - 1.0);
        }
        String tag = "w23raw";
        if (model.result().numerical().tags().length > 0
                && Arrays.asList(model.result().numerical().tags()).contains(tag))
            throw new IllegalStateException("registered raw-field numerical tag already exists");
        NumericalFeature interp = model.result().numerical().create(tag, "Interp");
        Map<String, Object> result = new LinkedHashMap<>();
        Throwable operationFailure = null;
        Throwable cleanupFailure = null;
        try {
            interp.set("data", dataset);
            interp.set("expr", RAW_FIELDS);
            interp.set("coord", coordinates);
            interp.set("solnum", "1");
            interp.run();
            double[][][] real = interp.getData();
            double[][][] imaginary = interp.getImagData();
            double[][] coordinatesReadback = interp.getCoordinates();
            if (!interp.isComplex()) throw new IllegalStateException("native complex data is required; Interp reported real-only output");
            if (real == null || imaginary == null || real.length != RAW_FIELDS.length || imaginary.length != RAW_FIELDS.length)
                throw new IllegalStateException("Interp real/imaginary expression dimensions did not match the frozen field list");
            if (coordinatesReadback == null || coordinatesReadback.length != 2 || coordinatesReadback[0].length != samples
                    || coordinatesReadback[1].length != samples)
                throw new IllegalStateException("Interp coordinate readback did not match the registered 2-D grid");
            int nxIndex = RAW_FIELDS.length - 2;
            int nyIndex = RAW_FIELDS.length - 1;
            double firstNx = real[nxIndex][0][0];
            int nativeNormalSign;
            if (Math.abs(Math.abs(firstNx) - 1.0) > 1e-10)
                throw new IllegalStateException("native x-plane normal is not a unit x normal");
            nativeNormalSign = firstNx > 0 ? 1 : -1;
            for (int sample = 0; sample < samples; sample++) {
                if (Math.abs(coordinatesReadback[0][sample] - coordinates[0][sample]) > 1e-12
                        || Math.abs(coordinatesReadback[1][sample] - coordinates[1][sample]) > 1e-12)
                    throw new IllegalStateException("Interp global-coordinate readback differs from the frozen SI-metre coordinates");
                double nx = real[nxIndex][0][sample];
                double nxImag = imaginary[nxIndex][0][sample];
                double ny = real[nyIndex][0][sample];
                double nyImag = imaginary[nyIndex][0][sample];
                if (Math.abs(nx - nativeNormalSign) > 1e-10 || Math.abs(nxImag) > 1e-12
                        || Math.abs(ny) > 1e-10 || Math.abs(nyImag) > 1e-12)
                    throw new IllegalStateException("native boundary normal is not constant and parallel to x");
            }
            List<Map<String, Object>> records = new ArrayList<>();
            for (int expressionIndex = 0; expressionIndex < RAW_FIELDS.length; expressionIndex++) {
                if (real[expressionIndex].length != 1 || imaginary[expressionIndex].length != 1
                        || real[expressionIndex][0].length != samples || imaginary[expressionIndex][0].length != samples)
                    throw new IllegalStateException("Interp returned an unexpected solution/sample axis for " + RAW_FIELDS[expressionIndex]);
                records.add(Map.of("expression", RAW_FIELDS[expressionIndex],
                        "real", real[expressionIndex][0], "imag", imaginary[expressionIndex][0]));
            }
            result.put("status", "RAW_NATIVE_COMPLEX_FIELDS_READ");
            result.put("dataset", dataset);
            result.put("plane", plane);
            result.put("selection_tag", "output_x8".equals(plane) ? "selReceiverX8" : "selInputPort");
            result.put("entity_dimension", 1);
            result.put("geometry_frame", Map.of("component", "comp1", "geometry", "geom1",
                    "dimension", 2, "length_unit", model.component("comp1").geom("geom1").lengthUnit()));
            result.put("x_m", x);
            result.put("coordinate_unit", "m");
            result.put("sample_count", samples);
            result.put("inner_index", innerIndex);
            result.put("outer_index", outerIndex);
            // Set normal_sign from the native nx readback so sign*nx points
            // along physical +x at both the x=8 um receiver and x=-10 um input.
            result.put("native_normal_x", nativeNormalSign);
            result.put("normal_sign", nativeNormalSign);
            result.put("expressions", records);
            result.put("coordinates_m", coordinatesReadback);
            result.put("complex_readback", true);
        } catch (Throwable error) {
            operationFailure = error;
        } finally {
            try {
                model.result().numerical().remove(tag);
                if (Arrays.asList(model.result().numerical().tags()).contains(tag))
                    throw new IllegalStateException("temporary Interp tag remains after remove");
            } catch (Throwable error) {
                cleanupFailure = error;
            }
        }
        result.put("cleanup", Map.of("created", true, "removed", cleanupFailure == null,
                "cleanup_failed", cleanupFailure != null, "tag", tag,
                "error", cleanupFailure == null ? "" : cleanupFailure.toString()));
        if (operationFailure != null)
            throw new IllegalStateException("raw native field extraction failed; cleanup=" + result.get("cleanup"), operationFailure);
        if (cleanupFailure != null) throw new IllegalStateException("raw field Interp cleanup failed", cleanupFailure);
        return result;
    }

    private static SelectionFeature box(Model model, String tag, int entityDimension,
                                        double xmin, double xmax, double ymin, double ymax) {
        SelectionFeature selection = model.component("comp1").selection().create(tag, "Box");
        selection.set("entitydim", entityDimension);
        selection.set("condition", "inside");
        selection.set("xmin", xmin);
        selection.set("xmax", xmax);
        selection.set("ymin", ymin);
        selection.set("ymax", ymax);
        return selection;
    }

    private static void rectangle(GeomSequence geom, String tag, String x, String y, String width, String height) {
        geom.feature().create(tag, "Rectangle");
        geom.feature(tag).set("base", "corner");
        geom.feature(tag).set("pos", new String[]{x, y});
        geom.feature(tag).set("size", new String[]{width, height});
    }

    private static void material(Model model, String tag, String selection, String eps) {
        model.material().create(tag, "Common", "comp1");
        model.material(tag).selection().named(selection);
        String[][] epsilon = {{eps, "0", "0"}, {"0", eps, "0"}, {"0", "0", eps}};
        String[][] mu = {{"1", "0", "0"}, {"0", "1", "0"}, {"0", "0", "1"}};
        model.material(tag).propertyGroup("def").set("relpermittivity", epsilon);
        model.material(tag).propertyGroup("def").set("relpermeability", mu);
    }

    private static Map<String, Object> materialReadback(Model model, String tag, String selection, String expectedEps) {
        List<List<String>> epsilon = stringMatrix(model.material(tag).propertyGroup("def").getStringMatrix("relpermittivity"));
        List<List<String>> mu = stringMatrix(model.material(tag).propertyGroup("def").getStringMatrix("relpermeability"));
        return Map.of("tag", tag, "selection", selection, "expected_diagonal_relative_permittivity", expectedEps,
                      "relative_permittivity", epsilon, "relative_permeability", mu,
                      "electric_lossless_assumption", "zero_imaginary_relative_permittivity_requested");
    }

    private static void configurePort(PhysicsFeature port, String number, boolean excited, boolean interior) {
        port.set("PortType", "Numeric");
        port.set("PortName", number);
        port.set("PortExcitation", excited ? "on" : "off");
        port.set("PortOrientation", "ForwardPort");
        port.set("Thetap", "0[deg]");
        port.set("PortModeNumber", 1);
        if (excited) port.set("Pin", "1[W/m]");
        if (interior) {
            port.set("PortSlit", 1);
            port.set("SlitType", "DomainBacked");
        }
    }

    private static void configureBma(StudyFeature bma, String portNumber) {
        bma.set("PortName", portNumber);
        bma.set("modeFreq", "f0");
        bma.set("neigs", 1);
        bma.set("eigwhich", "effective_mode_index");
        bma.set("shiftactive", "on");
        bma.set("shift", "1.54");
    }

    private static Map<String, Object> selectionReadback(String tag, int dimension,
                                                         int[] entities, int[] assigned) {
        Map<String, Object> row = new LinkedHashMap<>();
        List<Integer> ids = safeIntList(entities);
        row.put("tag", tag);
        row.put("entity_dimension", dimension);
        row.put("entity_ids", ids);
        row.put("entity_count", ids.size());
        if (assigned != null) {
            List<Integer> assignedIds = safeIntList(assigned);
            row.put("assigned_entity_ids", assignedIds);
            row.put("matches_assigned_selection", ids.equals(assignedIds));
        }
        return row;
    }

    private interface PropertyReader {
        String[] properties();
        boolean hasProperty(String name);
        String[] allowed(String name);
        String valueType(String name);
        String stringValue(String name);
    }

    private static Map<String, Object> inspect(ParameterEntity entity, String[] keys) {
        return inspect(new PropertyReader() {
            public String[] properties() { return entity.properties(); }
            public boolean hasProperty(String name) { return entity.hasProperty(name); }
            public String[] allowed(String name) { return entity.getAllowedPropertyValues(name); }
            public String valueType(String name) { return entity.getValueType(name); }
            public String stringValue(String name) { return entity.getString(name); }
        }, entity.getClass().getSimpleName(), keys);
    }

    private static Map<String, Object> inspect(PropFeature entity, String[] keys) {
        return inspect(new PropertyReader() {
            public String[] properties() { return entity.properties(); }
            public boolean hasProperty(String name) { return entity.hasProperty(name); }
            public String[] allowed(String name) { return entity.getAllowedPropertyValues(name); }
            public String valueType(String name) { return entity.getValueType(name); }
            public String stringValue(String name) { return entity.getString(name); }
        }, entity.getClass().getSimpleName(), keys);
    }

    private static Map<String, Object> inspect(PropertyReader reader, String type, String[] keys) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("feature_type", type);
        row.put("property_names", Arrays.asList(reader.properties()));
        Map<String, Object> properties = new LinkedHashMap<>();
        for (String key : keys) {
            Map<String, Object> item = new LinkedHashMap<>();
            try {
                boolean present = reader.hasProperty(key);
                item.put("has_property_exact", present);
                if (present) {
                    item.put("value_type", reader.valueType(key));
                    try { item.put("allowed_values", safeStringList(reader.allowed(key))); }
                    catch (Throwable error) { item.put("allowed_values_error", error.getClass().getName()); }
                    try { item.put("string_readback", reader.stringValue(key)); }
                    catch (Throwable error) { item.put("string_readback_error", error.getClass().getName()); }
                }
            } catch (Throwable error) {
                item.put("probe_error", error.getClass().getName());
            }
            properties.put(key, item);
        }
        row.put("requested_properties", properties);
        return row;
    }

    private static List<String> safeStringList(String[] values) {
        return values == null ? List.of() : Arrays.asList(values);
    }

    private static List<List<String>> stringMatrix(String[][] matrix) {
        List<List<String>> rows = new ArrayList<>();
        if (matrix != null) for (String[] row : matrix) rows.add(row == null ? List.of() : Arrays.asList(row));
        return rows;
    }

    private static List<Integer> safeIntList(int[] values) {
        List<Integer> result = new ArrayList<>();
        if (values != null) for (int value : values) result.add(value);
        return result;
    }
}
