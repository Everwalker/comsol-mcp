import com.comsol.model.Coordsys;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.MaterialModel;
import com.comsol.model.MeshFeature;
import com.comsol.model.MeshSequence;
import com.comsol.model.NumericalFeature;
import com.comsol.model.ParameterEntity;
import com.comsol.model.PropFeature;
import com.comsol.model.SelectionFeature;
import com.comsol.model.SolutionInfo;
import com.comsol.model.StudyFeature;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverSequence;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.PhysicsField;
import com.comsol.model.physics.FeatureInfo;
import com.comsol.model.physics.FeatureInfoList;
import com.comsol.model.physics.EquationViewParent;
import java.io.File;
import java.lang.reflect.Array;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
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
        if ("saved_solution_diagnostic".equals(phase)) return savedSolutionDiagnostic(model);
        if ("definition_boundary_metadata".equals(phase)) return definitionBoundaryMetadata(model);
        if ("raw_fields".equals(phase)) return rawFields(model, args);
        throw new IllegalArgumentException("phase must be build, set_phase, set_mesh, save, identity, solution_inventory, saved_solution_diagnostic, or raw_fields");
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

        MeshSequence mesh = model.component("comp1").mesh().create("mesh1", "geom1");
        MeshFeature freeTri = mesh.create("ftri1", "FreeTri");
        // Selection.all() operates in the selection's current dimension.
        freeTri.selection().geom(2);
        freeTri.selection().all();
        int[] selectedMeshDomains = freeTri.selection().entities(2);
        if (freeTri.selection().dim() != 2 || selectedMeshDomains.length != geom.getNDomains()
                || selectedMeshDomains.length == 0)
            throw new IllegalStateException("FreeTri must explicitly select every 2D geometry domain");
        mesh.feature("size").set("custom", "on");
        mesh.feature("size").set("hmax", "0.16[um]");
        mesh.feature("size").set("hmin", "0.04[um]");
        mesh.run();

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
        result.put("mesh", meshReadback(mesh, freeTri, "coarse", geom.getNDomains()));
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
        MeshSequence mesh = model.component("comp1").mesh("mesh1");
        MeshFeature freeTri = mesh.feature("ftri1");
        mesh.feature("size").set("hmax", hmax);
        mesh.run();
        Map<String, Object> result = meshReadback(mesh, freeTri, level,
                model.component("comp1").geom("geom1").getNDomains());
        if (!hmax.equals(result.get("hmax")))
            throw new IllegalStateException("mesh hmax readback differs from the frozen level");
        return result;
    }

    private static Map<String, Object> meshReadback(MeshSequence mesh, MeshFeature freeTri,
                                                    String level, int geometryDomainCount) {
        int[] selectedDomains = freeTri.selection().entities(2);
        List<Integer> selectedDomainIds = new ArrayList<>();
        for (int domain : selectedDomains) selectedDomainIds.add(domain);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("mesh_level", level);
        result.put("hmax", mesh.feature("size").getString("hmax"));
        result.put("hmin", mesh.feature("size").getString("hmin"));
        result.put("elements", mesh.getNumElem());
        result.put("tri_elements", mesh.getNumElem("tri"));
        result.put("element_types", Arrays.asList(mesh.getTypes()));
        result.put("mesh_is_empty", mesh.isEmpty());
        result.put("mesh_is_complete", mesh.isComplete());
        result.put("free_tri_tag", freeTri.tag());
        result.put("free_tri_built", freeTri.isBuilt());
        result.put("free_tri_selection_dimension", freeTri.selection().dim());
        result.put("free_tri_selected_domains", selectedDomainIds);
        result.put("geometry_domain_count", geometryDomainCount);
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

    /**
     * Read-only diagnostic of a previously saved W23 model. This method only
     * calls metadata/property getters; it never invokes Study.run, a solver
     * sequence run method, sequence generation, solution mutation, or save.
     */
    // A read-only metadata phase. No feature/selection creation, setters,
    // geometry run, numerical feature, save, Study.run, or SolverSequence.run.
    private static final int METADATA_MAX_FEATURES = 32;
    private static final int METADATA_MAX_INFO_TAGS = 8;
    private static final int METADATA_MAX_ENTITIES = 512;
    private static final int METADATA_MAX_ROWS = 16384;
    private static final int METADATA_MAX_COLUMNS = 128;
    private static final long METADATA_MAX_UTF8_BYTES = 8388608L;

    private static void metadataStrings(String[] values, long[] budget) {
        if (values == null) return;
        if (values.length > METADATA_MAX_COLUMNS) throw new IllegalStateException("metadata column/tag count exceeds bound");
        for (String value : values) if (value != null) {
            budget[1] += value.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
            if (budget[1] > METADATA_MAX_UTF8_BYTES) throw new IllegalStateException("metadata UTF8 bytes exceeds bound");
        }
    }

    private static void metadataOwner(EquationViewParent owner, String kind, String tag,
            List<Object> tables, long[] budget) {
        FeatureInfoList infos = owner.featureInfo();
        String[] infoTags = infos.tags();
        if (infoTags == null || infoTags.length > METADATA_MAX_INFO_TAGS) throw new IllegalStateException("metadata info tags unavailable/exceeds bound");
        metadataStrings(infoTags, budget);
        for (String infoTag : infoTags) {
            FeatureInfo info = infos.get(infoTag);
            String[][] table = info.getInfoTable("Expression", "recursive", "all");
            if (table == null) throw new IllegalStateException("metadata Expression table unavailable");
            budget[0] += table.length;
            if (budget[0] > METADATA_MAX_ROWS) throw new IllegalStateException("metadata rows exceeds bound");
            List<Object> rows = new ArrayList<>();
            List<Integer> widths = new ArrayList<>();
            for (String[] row : table) {
                metadataStrings(row, budget);
                rows.add(row == null ? null : new ArrayList<String>(Arrays.asList(row.clone())));
                widths.add(row == null ? -1 : row.length);
            }
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("owner_kind", kind); entry.put("owner_tag", tag);
            entry.put("info_tag", infoTag); entry.put("native_info_tag", info.tag());
            entry.put("table_type", "Expression"); entry.put("options", List.of("recursive", "all"));
            entry.put("column_labels", "API_NOT_EXPOSED_BY_GETINFOTABLE");
            entry.put("raw_rows", rows); entry.put("row_widths", widths);
            entry.put("status", "CAPTURED_ALL_NATIVE_COLUMNS_NOT_INTERPRETED");
            tables.add(entry);
        }
    }

    private static void metadataInts(int[] values) {
        if (values == null || values.length > METADATA_MAX_ENTITIES) throw new IllegalStateException("metadata entity array unavailable/exceeds bound");
    }

    private static Map<String, Object> definitionBoundaryMetadata(Model model) {
        long[] budget = new long[]{0L, 0L};
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("diagnostic_phase", "definition_boundary_metadata");
        List<Object> tables = new ArrayList<>();
        List<Object> selections = new ArrayList<>();
        result.put("definition_tables", tables); result.put("named_selections", selections);
        try {
        Physics physics = model.component("comp1").physics("ewfd");
        GeomSequence geom = model.component("comp1").geom("geom1");
        int dim = geom.getSDim(), boundaries = geom.getNBoundaries(), domains = geom.getNDomains();
        if (dim != 2 || boundaries < 1 || boundaries > METADATA_MAX_ENTITIES || domains < 1 || domains > METADATA_MAX_ENTITIES)
            throw new IllegalStateException("metadata exact 2D geometry/count exceeds bound");
        result.put("component_tag", "comp1"); result.put("geometry_tag", "geom1");
        result.put("physics_tag", physics.tag()); result.put("physics_name_raw", physics.name());
        result.put("physics_scope_raw", physics.scope()); result.put("physics_type", physics.getType());
        result.put("polarization_components_raw", physics.prop("components").getString("components"));
        PhysicsField field = physics.field("electricfield");
        result.put("electric_field_name_raw", field.field());
        result.put("electric_field_components_raw", safeStringList(field.component()));
        result.put("electric_field_fieldname_raw", safeStringList(field.fieldname()));
        result.put("physics_selected_domain_entities", physics.selection().entities(2));
        result.put("geometry_dimension", dim); result.put("geometry_boundary_count", boundaries);
        result.put("geometry_domain_count", domains); result.put("geometry_bounding_box_raw", geom.getBoundingBox());
        result.put("geometry_length_unit_raw", geom.lengthUnit());
        int[][] upDown = geom.getUpDown();
        if (upDown == null || upDown.length != 2) throw new IllegalStateException("metadata up/down array is not two rows");
        metadataInts(upDown[0]); metadataInts(upDown[1]);
        result.put("boundary_up_down_raw", upDown);
        result.put("boundary_up_down_column_to_entity_mapping", "RAW_COLUMN_INDEX_ONLY_NO_UNDOCUMENTED_OFFSET_INFERRED");
        metadataOwner(physics, "physics", "ewfd", tables, budget);
        String[] features = physics.feature().tags();
        if (features == null || features.length > METADATA_MAX_FEATURES) throw new IllegalStateException("metadata feature count unavailable/exceeds bound");
        metadataStrings(features, budget);
        for (String featureTag : features) metadataOwner(physics.feature(featureTag), "physics_feature", featureTag, tables, budget);
        result.put("definition_tables", tables); result.put("definition_feature_tags", safeStringList(features));
        for (String selectionTag : new String[]{"selReceiverX8", "selInputPort", "selCoreCaptureX8", "selCoreMaterial", "selPmlDomains", "selCladdingBottom", "selCladdingTop"}) {
            SelectionFeature selection = model.component("comp1").selection(selectionTag);
            int selectionDim = selection.dim();
            if (selectionDim != 1 && selectionDim != 2) throw new IllegalStateException("metadata named selection dimension unexpected");
            int[] entities = selection.entities(selectionDim); metadataInts(entities);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("selection_tag", selectionTag); row.put("native_tag", selection.tag());
            row.put("geometry_raw", selection.geom()); row.put("dimension_raw", selectionDim);
            row.put("entities_raw", entities); List<Object> adjacent = new ArrayList<>();
            for (int entity : entities) {
                if (entity < 1 || entity > (selectionDim == 1 ? boundaries : domains)) throw new IllegalStateException("metadata entity outside native count");
                Map<String, Object> edge = new LinkedHashMap<>(); edge.put("entity", entity);
                int targetDim = selectionDim == 1 ? 2 : 1;
                int[] neighbors = geom.getAdj(selectionDim, targetDim, entity);
                int[] orientations = geom.getAdjOrient(selectionDim, targetDim, entity);
                metadataInts(neighbors); metadataInts(orientations);
                edge.put("adjacent_dimension", targetDim); edge.put("adjacent_entities_raw", neighbors);
                edge.put("adjacency_orientation_flags_raw", orientations);
                if (selectionDim == 1) {
                    double[] range = geom.edgeParamRange(entity);
                    if (range == null || range.length != 2 || !Double.isFinite(range[0]) || !Double.isFinite(range[1])) throw new IllegalStateException("metadata edge range missing");
                    double[] sample = {range[0], 0.5 * (range[0] + range[1]), range[1]};
                    edge.put("edge_parameter_range_raw", range); edge.put("sample_parameter_raw", sample);
                    edge.put("edge_coordinates_raw", geom.edgeX(entity, sample));
                    edge.put("edge_normal_raw", geom.edgeNormal(entity, sample));
                    edge.put("normal_scope", "GEOMETRIC_EDGE_NORMAL_NOT_ASSERTED_AS_SELECTED_PHYSICS_SIDE");
                }
                adjacent.add(edge);
            }
            row.put("adjacency_and_edge_samples_raw", adjacent); selections.add(row);
        }
        result.put("named_selections", selections);
        List<Object> ports = new ArrayList<>();
        for (String portTag : new String[]{"portIn", "portOut"}) {
            PhysicsFeature port = physics.feature(portTag); int[] entities = port.selection().entities(1); metadataInts(entities);
            Map<String, Object> row = new LinkedHashMap<>(); row.put("tag", portTag); row.put("type", port.getType());
            row.put("PortName", port.getString("PortName")); row.put("entities_raw", entities);
            ports.add(row);
        }
        result.put("ports", ports); result.put("definition_rows_total", budget[0]);
        result.put("definition_UTF8_bytes_total", budget[1]);
        result.put("limits", Map.of("max_features", METADATA_MAX_FEATURES, "max_info_tags", METADATA_MAX_INFO_TAGS,
            "max_entities", METADATA_MAX_ENTITIES, "max_rows", METADATA_MAX_ROWS,
            "max_columns", METADATA_MAX_COLUMNS, "max_UTF8_bytes", METADATA_MAX_UTF8_BYTES));
        result.put("bound_scope", "CHECKS_BEFORE_LOOPS_AND_AFTER_NATIVE_TABLE_RETURN_NO_HARD_ENGINE_ALLOCATION_QUOTA");
        result.put("numerical_feature_dispatch_count", 0); result.put("study_or_solver_invoked", false);
        result.put("physics_or_selection_mutated", false); result.put("scientific_acceptance", "NOT_EVALUATED");
        result.put("status", "READONLY_METADATA_CAPTURE_COMPLETE");
        return result;
        } catch (Throwable failure) {
            result.put("status", "READ_FAILED_STOPPED_NO_FURTHER_GETTERS");
            result.put("execution_state_unknown", true); result.put("safe_retry", false);
            result.put("failure_type", failure.getClass().getName());
            String message = String.valueOf(failure.getMessage());
            result.put("failure_message", message.substring(0, Math.min(2048, message.length())));
            result.put("captured_prefix_preserved", true);
            result.put("scientific_acceptance", "NOT_EVALUATED");
            // Metadata publication does not downgrade a failed native getter.
            // The original runner rejects this status before any later read.
            return result;
        }
    }

    private static Map<String, Object> savedSolutionDiagnostic(Model model) {
        Map<String, Object> result = new LinkedHashMap<>();
        String studyTag = "std1";
        com.comsol.model.Study study = model.study(studyTag);
        String[] queryTypes = {
            "All", "None", "SolverSequence", "CopySolution",
            "ParametricStore", "Stored", "Parametric"
        };
        List<Map<String, Object>> queryRows = new ArrayList<>();
        Map<String, List<String>> membership = new LinkedHashMap<>();
        List<Map<String, Object>> configuredStudySteps = captureConfiguredStudySteps(study);
        for (String queryType : queryTypes) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("receiver", "model.study(" + studyTag + ")");
            row.put("receiver_type", study.getClass().getName());
            row.put("method", "Study.getSolverSequences");
            row.put("declared_return_type", "java.lang.String[]");
            row.put("argument_types", List.of("java.lang.String"));
            row.put("arguments", List.of(queryType));
            try {
                String[] tags = study.getSolverSequences(queryType);
                row.put("status", "CAPTURED");
                row.put("actual_return_type", tags == null ? "null" : tags.getClass().getName());
                row.put("return_value", tags == null ? null : safeStringList(tags));
                if (tags != null) {
                    for (String tag : tags) {
                        membership.computeIfAbsent(tag, ignored -> new ArrayList<>()).add(queryType);
                    }
                }
            } catch (Throwable error) {
                row.put("status", "ERROR");
                row.put("error_type", error.getClass().getName());
                row.put("error", String.valueOf(error));
            }
            queryRows.add(row);
        }

        String[] allSolverTags = model.sol().tags();
        List<Map<String, Object>> solverRows = new ArrayList<>();
        if (allSolverTags != null) {
            for (String solverTag : allSolverTags) {
                SolverSequence sequence = null;
                Map<String, Object> sequenceRow = new LinkedHashMap<>();
                sequenceRow.put("tag", solverTag);
                sequenceRow.put("study_type_query_memberships_observed",
                        membership.getOrDefault(solverTag, List.of()));
                try {
                    sequence = model.sol(solverTag);
                    sequenceRow.put("sequence_receiver_type", sequence.getClass().getName());
                } catch (Throwable error) {
                    sequenceRow.put("sequence_access_status", "ERROR");
                    sequenceRow.put("sequence_access_error_type", error.getClass().getName());
                    sequenceRow.put("sequence_access_error", String.valueOf(error));
                }
                if (sequence != null) {
                    sequenceRow.put("sequence_type_getter", captureScalarRead(
                            "model.sol(" + solverTag + ")", sequence, "getType",
                            "java.lang.String", new String[0], new Object[0], sequence::getType));
                    sequenceRow.put("sequence_kind_getter", captureScalarRead(
                            "model.sol(" + solverTag + ")", sequence, "getSequenceType",
                            "java.lang.String", new String[0], new Object[0], sequence::getSequenceType));
                    sequenceRow.put("attached_study_getter", captureScalarRead(
                            "model.sol(" + solverTag + ")", sequence, "study",
                            "java.lang.String", new String[0], new Object[0], sequence::study));
                    sequenceRow.put("is_attached_getter", captureScalarRead(
                            "model.sol(" + solverTag + ")", sequence, "isAttached",
                            "boolean", new String[0], new Object[0], sequence::isAttached));
                    sequenceRow.put("parameter_names_getter", captureScalarRead(
                            "model.sol(" + solverTag + ")", sequence, "getPNames",
                            "java.lang.String[]", new String[0], new Object[0], sequence::getPNames));
                    sequenceRow.put("direct_sequence_parameter_axis_observation",
                            captureDirectSequenceParameterAxis(sequence, solverTag));
                    sequenceRow.put("solver_tree", captureSolverTree(sequence));
                    sequenceRow.put("solution_info", captureSolutionInfo(solverTag, sequence));
                }
                solverRows.add(sequenceRow);
            }
        }

        List<Map<String, Object>> datasetRows = new ArrayList<>();
        Map<String, Boolean> solutionTagsFromDatasets = new LinkedHashMap<>();
        for (String datasetTag : model.result().dataset().tags()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", datasetTag);
            try {
                Object dataset = model.result().dataset(datasetTag);
                row.put("receiver_type", dataset.getClass().getName());
                row.put("property_names", Arrays.asList(model.result().dataset(datasetTag).properties()));
                Map<String, Object> selected = new LinkedHashMap<>();
                for (String property : new String[]{"solution", "data", "solnum", "outersolnum"}) {
                    Map<String, Object> value = new LinkedHashMap<>();
                    try {
                        boolean present = model.result().dataset(datasetTag).hasProperty(property);
                        value.put("has_property", present);
                        if (present) {
                            value.put("value_type", model.result().dataset(datasetTag).getValueType(property));
                            String raw = model.result().dataset(datasetTag).getString(property);
                            value.put("getter", "Dataset.getString");
                            value.put("status", "CAPTURED");
                            value.put("return_value", raw);
                            if ("solution".equals(property) && raw != null && !raw.isEmpty()) {
                                solutionTagsFromDatasets.put(raw, Boolean.TRUE);
                            }
                        } else {
                            value.put("status", "PROPERTY_ABSENT");
                        }
                    } catch (Throwable error) {
                        value.put("status", "ERROR");
                        value.put("error_type", error.getClass().getName());
                        value.put("error", String.valueOf(error));
                    }
                    selected.put(property, value);
                }
                row.put("selected_properties", selected);
            } catch (Throwable error) {
                row.put("status", "ERROR");
                row.put("error_type", error.getClass().getName());
                row.put("error", String.valueOf(error));
            }
            datasetRows.add(row);
        }

        List<Map<String, Object>> datasetSolutionInfoRows = new ArrayList<>();
        for (String solutionTag : solutionTagsFromDatasets.keySet()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("source", "exact Dataset.getString(\"solution\") readback");
            row.put("solution_tag", solutionTag);
            try {
                SolverSequence sequence = model.sol(solutionTag);
                row.put("solver_sequence_type", sequence.getClass().getName());
                row.put("solution_info", captureSolutionInfo(solutionTag, sequence));
            } catch (Throwable error) {
                row.put("status", "ERROR");
                row.put("error_type", error.getClass().getName());
                row.put("error", String.valueOf(error));
            }
            datasetSolutionInfoRows.add(row);
        }

        result.put("fixture_id", FIXTURE_ID);
        result.put("diagnostic_phase", "saved_solution_diagnostic");
        result.put("study_tag", studyTag);
        result.put("study_steps_in_configured_order", configuredStudySteps);
        result.put("study_solver_sequence_queries", queryRows);
        result.put("model_solver_sequence_tags", allSolverTags == null ? List.of() : safeStringList(allSolverTags));
        result.put("solver_sequences", solverRows);
        result.put("datasets", datasetRows);
        result.put("dataset_solution_info", datasetSolutionInfoRows);
        result.put("explicit_study_or_solver_run_calls_in_this_phase", 0);
        result.put("internal_solver_execution_count", "UNKNOWN_NOT_INFERRED_FROM_READBACK");
        result.put("native_scientific_acceptance", "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY");
        return result;
    }

    private static List<Map<String, Object>> captureConfiguredStudySteps(
            com.comsol.model.Study study) {
        List<Map<String, Object>> rows = new ArrayList<>();
        try {
            String[] tags = study.feature().tags();
            if (tags == null) return rows;
            for (int order = 0; order < tags.length; order++) {
                String tag = tags[order];
                StudyFeature feature = study.feature(tag);
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("tag", tag);
                row.put("configured_order", order);
                row.put("receiver_type", feature.getClass().getName());
                row.put("feature_type_getter", captureRead(
                        "model.study(std1).feature(" + tag + ")", feature, "getType",
                        "java.lang.String", new String[0], new Object[0], feature::getType));
                Map<String, Object> properties = new LinkedHashMap<>();
                for (String property : new String[]{"PortName", "modeFreq", "plist"}) {
                    properties.put(property, captureStudyStringProperty(feature, tag, property));
                }
                row.put("selected_properties", properties);
                rows.add(row);
            }
        } catch (Throwable error) {
            Map<String, Object> failure = new LinkedHashMap<>();
            failure.put("status", "ERROR");
            failure.put("error_type", error.getClass().getName());
            failure.put("error", String.valueOf(error));
            rows.add(failure);
        }
        return rows;
    }

    private static Map<String, Object> captureStudyStringProperty(
            StudyFeature feature, String tag, String property) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("property", property);
        try {
            boolean present = feature.hasProperty(property);
            value.put("has_property", present);
            if (!present) {
                value.put("status", "PROPERTY_ABSENT");
                return value;
            }
            value.put("value_type", feature.getValueType(property));
            value.put("getter", "StudyFeature.getString");
            value.put("return_value", feature.getString(property));
            value.put("status", "CAPTURED");
        } catch (Throwable error) {
            value.put("status", "ERROR");
            value.put("error_type", error.getClass().getName());
            value.put("error", String.valueOf(error));
        }
        return value;
    }

    private static Map<String, Object> captureDirectSequenceParameterAxis(
            SolverSequence sequence, String solverTag) {
        Map<String, Object> result = new LinkedHashMap<>();
        Object[] sizeHolder = new Object[1];
        Object[] namesHolder = new Object[1];
        result.put("get_size", captureRead("model.sol(" + solverTag + ")", sequence,
                "getSize", "int[]", new String[0], new Object[0], () -> {
                    int[] value = sequence.getSize(); sizeHolder[0] = value; return value;
                }));
        result.put("get_size_multi", captureRead("model.sol(" + solverTag + ")", sequence,
                "getSizeMulti", "int[]", new String[0], new Object[0], () -> {
                    return sequence.getSizeMulti();
                }));
        result.put("parameter_names", captureRead("model.sol(" + solverTag + ")", sequence,
                "getPNames", "java.lang.String[]", new String[0], new Object[0], () -> {
                    String[] value = sequence.getPNames(); namesHolder[0] = value; return value;
                }));
        int[] size = sizeHolder[0] instanceof int[] ? (int[]) sizeHolder[0] : null;
        String[] names = namesHolder[0] instanceof String[] ? (String[]) namesHolder[0] : null;
        List<Map<String, Object>> rows = new ArrayList<>();
        List<Map<String, Object>> units = new ArrayList<>();
        SolutionInfo observedInfo = null;
        Object[] infoHolder = new Object[1];
        result.put("solution_info_getter", captureRead("model.sol(" + solverTag + ")",
                sequence, "getSolutioninfo", "com.comsol.model.SolutionInfo",
                new String[0], new Object[0], () -> {
                    SolutionInfo value = sequence.getSolutioninfo();
                    infoHolder[0] = value;
                    return value;
                }));
        if (infoHolder[0] instanceof SolutionInfo) observedInfo = (SolutionInfo) infoHolder[0];
        final SolutionInfo info = observedInfo;
        if (names != null && info != null) {
            for (String name : names) {
                Map<String, Object> unit = new LinkedHashMap<>();
                unit.put("name", name);
                unit.put("readback", captureRead("SolutionInfo(" + solverTag + ")", info,
                        "getUnit", "java.lang.String", new String[]{"java.lang.String"},
                        new Object[]{name}, () -> info.getUnit(name)));
                units.add(unit);
            }
        }
        result.put("parameter_units", units);
        boolean rawValuesComplete = size != null && size.length == 2
                && size[1] >= 1 && size[1] <= 4096
                && names != null && names.length > 0 && names.length <= 64;
        if (rawValuesComplete) {
            for (int solnum = 1; solnum <= size[1]; solnum++) {
                final int selectedSolnum = solnum;
                Map<String, Object> value = new LinkedHashMap<>();
                value.put("real", captureRead("model.sol(" + solverTag + ")", sequence,
                        "getPVals", "double[]", new String[]{"int"}, new Object[]{selectedSolnum},
                        () -> sequence.getPVals(selectedSolnum)));
                value.put("imaginary", captureRead("model.sol(" + solverTag + ")", sequence,
                        "getPValsImag", "double[]", new String[]{"int"}, new Object[]{selectedSolnum},
                        () -> sequence.getPValsImag(selectedSolnum)));
                value.put("solnum", selectedSolnum);
                for (Map<String, Object> unit : units) {
                    Object unitName = unit.get("name");
                    Object unitReadback = unit.get("readback");
                    Map<String, Object> unitRow = new LinkedHashMap<>();
                    unitRow.put("name", unitName);
                    if (unitReadback instanceof Map<?, ?>) {
                        Map<?, ?> rawUnit = (Map<?, ?>) unitReadback;
                        unitRow.put("status", rawUnit.get("status"));
                        unitRow.put("value", rawUnit.get("return_value"));
                        unitRow.put("error_type", rawUnit.get("error_type"));
                        unitRow.put("error", rawUnit.get("error"));
                    }
                    value.computeIfAbsent("parameter_units", ignored -> new ArrayList<Map<String, Object>>());
                    @SuppressWarnings("unchecked")
                    List<Map<String, Object>> unitRows = (List<Map<String, Object>>) value.get("parameter_units");
                    unitRows.add(unitRow);
                }
                rows.add(value);
            }
        }
        boolean shapeComplete = rawValuesComplete;
        for (Map<String, Object> value : rows) {
            for (String component : new String[]{"real", "imaginary"}) {
                Object rawCall = value.get(component);
                if (!(rawCall instanceof Map<?, ?>)
                        || !"CAPTURED".equals(((Map<?, ?>) rawCall).get("status"))) {
                    shapeComplete = false;
                    continue;
                }
                Object returned = ((Map<?, ?>) rawCall).get("return_value");
                if (!(returned instanceof List<?>) || ((List<?>) returned).size() != names.length) {
                    shapeComplete = false;
                }
            }
            Object rawUnits = value.get("parameter_units");
            if (!(rawUnits instanceof List<?>) || ((List<?>) rawUnits).size() != names.length) {
                shapeComplete = false;
            } else {
                for (Object unitRow : (List<?>) rawUnits) {
                    if (!(unitRow instanceof Map<?, ?>)
                            || !"CAPTURED".equals(((Map<?, ?>) unitRow).get("status"))
                            || !(((Map<?, ?>) unitRow).get("value") instanceof String)) {
                        shapeComplete = false;
                    }
                }
            }
        }
        result.put("status", shapeComplete
                ? "RAW_TYPED_COMPLEX_VALUES_CAPTURED_NOT_INTERPRETED"
                : "UNVERIFIED_DIRECT_SEQUENCE_SHAPE_OR_UNIT");
        result.put("solution_numbers", rows);
        result.put("get_size_semantics", "[degrees of freedom, solution number count] per COMSOL 6.4 doc7714/chunk23082");
        result.put("get_size_multi_semantics", "[local solution count in multisolution, total solution number count] per COMSOL 6.4 doc7714/chunk23082");
        result.put("interpretation", "direct SolverSequence one-based solnum reads only; no outer/inner tuple mapping or physical-axis role inferred");
        return result;
    }

    private static Map<String, Object> captureSolutionInfo(String solverTag, SolverSequence sequence) {
        Map<String, Object> result = new LinkedHashMap<>();
        Object[] infoHolder = new Object[1];
        Map<String, Object> infoGetter = captureRead(
                "model.sol(" + solverTag + ")", sequence, "getSolutioninfo",
                "com.comsol.model.SolutionInfo", new String[0], new Object[0], () -> {
                    SolutionInfo info = sequence.getSolutioninfo();
                    infoHolder[0] = info;
                    return info;
                });
        result.put("solution_info_getter", infoGetter);
        if (!(infoHolder[0] instanceof SolutionInfo)) {
            result.put("metadata_calls", List.of());
            result.put("observed_one_based_outer_inner_pairs", List.of());
            result.put("solver_sequence_parameter_reads", List.of());
            return result;
        }
        SolutionInfo info = (SolutionInfo) infoHolder[0];
        result.put("solution_info_receiver_type", info.getClass().getName());
        List<Map<String, Object>> calls = new ArrayList<>();
        captureAndAdd(calls, "SolutionInfo.getLevelNames", info, "getLevelNames",
                "java.lang.String[]", new String[0], new Object[0], info::getLevelNames);
        captureAndAdd(calls, "SolutionInfo.getLevels", info, "getLevels",
                "int", new String[0], new Object[0], info::getLevels);
        captureAndAdd(calls, "SolutionInfo.getOuterSolnum", info, "getOuterSolnum",
                "int[]", new String[0], new Object[0], info::getOuterSolnum);
        captureAndAdd(calls, "SolutionInfo.getPNamesOuter", info, "getPNamesOuter",
                "java.lang.String[]", new String[0], new Object[0], info::getPNamesOuter);
        captureAndAdd(calls, "SolutionInfo.getPUnitsOuter", info, "getPUnitsOuter",
                "java.lang.String[]", new String[0], new Object[0], info::getPUnitsOuter);
        captureAndAdd(calls, "SolutionInfo.isStructured", info, "isStructured",
                "boolean", new String[0], new Object[0], info::isStructured);
        captureAndAdd(calls, "SolutionInfo.isValid", info, "isValid",
                "boolean", new String[0], new Object[0], info::isValid);

        int[] outers = null;
        for (Map<String, Object> call : calls) {
            if ("getOuterSolnum".equals(call.get("method"))
                    && "CAPTURED".equals(call.get("status"))) {
                Object raw = call.get("return_value");
                if (raw instanceof List<?>) {
                    List<?> values = (List<?>) raw;
                    outers = new int[values.size()];
                    for (int index = 0; index < values.size(); index++) {
                        Object value = values.get(index);
                        if (!(value instanceof Number)) { outers = null; break; }
                        outers[index] = ((Number) value).intValue();
                    }
                }
            }
        }
        List<List<Integer>> observedPairs = new ArrayList<>();
        List<Map<String, Object>> sequenceValueRows = new ArrayList<>();
        LinkedHashSet<Integer> outerProbes = new LinkedHashSet<>();
        if (outers != null) {
            for (int outer : outers) outerProbes.add(outer);
        }
        // Activity009 issued getSolnum(1,true) for this same SolutionInfo
        // receiver. Keep that exact documented one-based probe even when
        // getOuterSolnum() reports the documented empty-array/no-outer case.
        outerProbes.add(1);
        for (int outer : outerProbes) {
            Object[] innerHolder = new Object[1];
            Map<String, Object> innerCall = captureRead(
                    "SolutionInfo(" + solverTag + ")", info, "getSolnum", "int[]",
                    new String[]{"int", "boolean"}, new Object[]{outer, Boolean.TRUE},
                    () -> {
                        int[] values = info.getSolnum(outer, true);
                        innerHolder[0] = values;
                        return values;
                    });
            calls.add(innerCall);
            final int sequenceOuter = outer;
            captureAndAdd(calls, "SolutionInfo.getSol(" + solverTag + ")", info,
                    "getSol", "java.lang.String", new String[]{"int"},
                    new Object[]{sequenceOuter}, () -> info.getSol(sequenceOuter));
            captureAndAdd(calls, "SolutionInfo.getSolverSequence(" + solverTag + ")", info,
                    "getSolverSequence", "java.lang.String", new String[]{"int"},
                    new Object[]{sequenceOuter}, () -> info.getSolverSequence(sequenceOuter));
            if (!(innerHolder[0] instanceof int[])) continue;
            for (int inner : (int[]) innerHolder[0]) {
                List<Integer> pairRecord = Arrays.asList(outer, inner);
                if (!observedPairs.contains(pairRecord)) observedPairs.add(pairRecord);
                final int pairOuter = outer;
                final int pairInner = inner;
                Map<String, Object> sequenceValues = new LinkedHashMap<>();
                sequenceValues.put("outer", pairOuter);
                sequenceValues.put("inner_solnum", pairInner);
                sequenceValues.put("real_parameter_values", captureScalarRead(
                        "model.sol(" + solverTag + ")", sequence, "getPVals",
                        "double[]", new String[]{"int"}, new Object[]{pairInner},
                        () -> sequence.getPVals(pairInner)));
                sequenceValues.put("imaginary_parameter_values", captureScalarRead(
                        "model.sol(" + solverTag + ")", sequence, "getPValsImag",
                        "double[]", new String[]{"int"}, new Object[]{pairInner},
                        () -> sequence.getPValsImag(pairInner)));
                sequenceValueRows.add(sequenceValues);
                int[][] pair = new int[][]{{pairOuter, pairInner}};
                captureAndAdd(calls, "SolutionInfo.getPNames(" + solverTag + ")", info,
                        "getPNames", "java.lang.String[][]", new String[]{"int[][]"},
                        new Object[]{pair}, () -> info.getPNames(pair));
                captureAndAdd(calls, "SolutionInfo.getPvals(" + solverTag + ")", info,
                        "getPvals", "double[][]", new String[]{"int[][]"},
                        new Object[]{pair}, () -> info.getPvals(pair));
                captureAndAdd(calls, "SolutionInfo.getPvalsImag(" + solverTag + ")", info,
                        "getPvalsImag", "double[][]", new String[]{"int[][]"},
                        new Object[]{pair}, () -> info.getPvalsImag(pair));
                captureAndAdd(calls, "SolutionInfo.getUnits(" + solverTag + ")", info,
                        "getUnits", "java.lang.String[][]", new String[]{"int[][]"},
                        new Object[]{pair}, () -> info.getUnits(pair));
                captureAndAdd(calls, "SolutionInfo.getISol(" + solverTag + ")", info,
                        "getISol", "int[]", new String[]{"int", "int"},
                        new Object[]{pairOuter, pairInner}, () -> info.getISol(pairOuter, pairInner));
            }
        }
        result.put("observed_one_based_outer_inner_pairs", observedPairs);
        result.put("solver_sequence_parameter_reads", sequenceValueRows);
        result.put("metadata_calls", calls);
        result.put("typed_matrix_argument_contract",
                "COMSOL 6.4 documents each solnums[n] as one-based (outer,inner); the exact prior Activity009 probe is repeated on this receiver as int[][] {{1,1}} only when getSolnum(1,true) returns inner 1; no alternate matrix orientation is inferred");
        return result;
    }

    private static Map<String, Object> captureSolverTree(SolverSequence sequence) {
        Map<String, Object> result = new LinkedHashMap<>();
        List<Map<String, Object>> rows = new ArrayList<>();
        try {
            String[] tags = sequence.feature().tags();
            result.put("root_feature_tags", tags == null ? List.of() : safeStringList(tags));
            if (tags != null) {
                for (String tag : tags) {
                    captureSolverFeature(sequence.feature(tag), tag, rows);
                }
            }
            result.put("status", "CAPTURED");
        } catch (Throwable error) {
            result.put("status", "PARTIAL_OR_ERROR");
            result.put("error_type", error.getClass().getName());
            result.put("error", String.valueOf(error));
        }
        result.put("features", rows);
        return result;
    }

    private static void captureSolverFeature(SolverFeature feature, String path,
                                             List<Map<String, Object>> rows) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("path", path);
        row.put("tree_order", rows.size());
        try {
            String type = feature.getType();
            row.put("feature_type", type);
            row.put("receiver_type", feature.getClass().getName());
            String[] properties = feature.properties();
            row.put("property_names", properties == null ? List.of() : safeStringList(properties));
            if ("StudyStep".equals(type)) {
                captureStringProperty(feature, row, "study");
                captureStringProperty(feature, row, "studystep");
            }
            if ("StoreSolution".equals(type) || "CopySolution".equals(type)) {
                captureStringProperty(feature, row, "sol");
            }
        } catch (Throwable error) {
            row.put("status", "PARTIAL_OR_ERROR");
            row.put("error_type", error.getClass().getName());
            row.put("error", String.valueOf(error));
        }
        rows.add(row);
        try {
            String[] children = feature.feature().tags();
            row.put("child_tags", children == null ? List.of() : safeStringList(children));
            if (children != null) {
                for (String child : children) {
                    captureSolverFeature(feature.feature(child), path + "/" + child, rows);
                }
            }
        } catch (Throwable error) {
            row.put("child_readback_status", "ERROR");
            row.put("child_readback_error_type", error.getClass().getName());
            row.put("child_readback_error", String.valueOf(error));
        }
    }

    private static void captureStringProperty(SolverFeature feature, Map<String, Object> row,
                                             String property) {
        Map<String, Object> value = new LinkedHashMap<>();
        try {
            boolean present = feature.hasProperty(property);
            value.put("has_property", present);
            if (present) {
                value.put("value_type", feature.getValueType(property));
                value.put("getter", "SolverFeature.getString");
                value.put("return_value", feature.getString(property));
                value.put("status", "CAPTURED");
            } else {
                value.put("status", "PROPERTY_ABSENT");
            }
        } catch (Throwable error) {
            value.put("status", "ERROR");
            value.put("error_type", error.getClass().getName());
            value.put("error", String.valueOf(error));
        }
        row.put("property_" + property, value);
    }

    private interface ReadOnlyCall { Object invoke() throws Throwable; }

    private static void captureAndAdd(List<Map<String, Object>> rows, String receiver,
                                      Object receiverObject, String method, String returnType,
                                      String[] argumentTypes, Object[] arguments,
                                      ReadOnlyCall call) {
        rows.add(captureRead(receiver, receiverObject, method, returnType,
                             argumentTypes, arguments, call));
    }

    private static Map<String, Object> captureScalarRead(String receiver, Object receiverObject,
                                                         String method, String returnType,
                                                         String[] argumentTypes, Object[] arguments,
                                                         ReadOnlyCall call) {
        return captureRead(receiver, receiverObject, method, returnType,
                          argumentTypes, arguments, call);
    }

    private static Map<String, Object> captureRead(String receiver, Object receiverObject,
                                                   String method, String returnType,
                                                   String[] argumentTypes, Object[] arguments,
                                                   ReadOnlyCall call) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("receiver", receiver);
        row.put("receiver_type", receiverObject == null ? "null" : receiverObject.getClass().getName());
        row.put("method", method);
        row.put("declared_return_type", returnType);
        row.put("argument_types", Arrays.asList(argumentTypes));
        row.put("arguments", rawValue(arguments));
        try {
            Object value = call.invoke();
            row.put("status", "CAPTURED");
            row.put("actual_return_type", value == null ? "null" : value.getClass().getName());
            row.put("return_value", rawValue(value));
        } catch (Throwable error) {
            row.put("status", "ERROR");
            row.put("error_type", error.getClass().getName());
            row.put("error", String.valueOf(error));
        }
        return row;
    }

    private static Object rawValue(Object value) {
        if (value == null) return null;
        Class<?> type = value.getClass();
        if (!type.isArray()) {
            if (value instanceof String || value instanceof Number || value instanceof Boolean) return value;
            return String.valueOf(value);
        }
        int length = Array.getLength(value);
        List<Object> result = new ArrayList<>(length);
        for (int index = 0; index < length; index++) result.add(rawValue(Array.get(value, index)));
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
        // COMSOL 6.4 accepts nine entries for a full tensor. These requested
        // diagonal tensors are isotropic, so COMSOL may canonicalize them to
        // a one-entry isotropic value on readback. The consumer records the
        // actual property size and reconstructs only documented shapes; no
        // nonsymmetric full-tensor ordering is claimed by this fixture.
        String[] epsilon = {eps, "0", "0", "0", eps, "0", "0", "0", eps};
        String[] mu = {"1", "0", "0", "0", "1", "0", "0", "0", "1"};
        model.material(tag).propertyGroup("def").set("relpermittivity", epsilon);
        model.material(tag).propertyGroup("def").set("relpermeability", mu);
        model.material(tag).propertyGroup("def").set("electricconductivity", new String[] {"0[S/m]"});
    }

    private static Map<String, Object> materialReadback(Model model, String tag, String selection, String expectedEps) {
        MaterialModel group = model.material(tag).propertyGroup("def");
        return Map.of("tag", tag, "selection", selection, "expected_diagonal_relative_permittivity", expectedEps,
                      "relative_permittivity", materialPropertyReadback(group, "relpermittivity"),
                      "relative_permeability", materialPropertyReadback(group, "relpermeability"),
                      "electric_conductivity", materialPropertyReadback(group, "electricconductivity"),
                      "electric_lossless_assumption", "real_relative_permittivity_with_electric_conductivity_set_to_zero_S_per_m");
    }

    private static Map<String, Object> materialPropertyReadback(MaterialModel group, String property) {
        Map<String, Object> result = new LinkedHashMap<>();
        String valueType = group.getValueType(property);
        result.put("value_type", valueType);
        result.put("size_getter", "MaterialModel.size");
        try {
            int[] storedSize = group.size(property);
            result.put("stored_size", storedSize == null ? null : integerList(storedSize));
            result.put("stored_size_status", storedSize == null ? "UNVERIFIED_NULL_SIZE" : "CAPTURED");
        } catch (Throwable error) {
            result.put("stored_size", null);
            result.put("stored_size_status", "UNVERIFIED_SIZE_ERROR");
            result.put("stored_size_error", error.toString());
        }
        if ("String".equals(valueType)) {
            String value = group.getString(property);
            result.put("getter", "MaterialModel.getString");
            result.put("raw_value", value);
            result.put("raw_shape", List.of());
            result.put("readback_status", value == null ? "UNVERIFIED_NULL_VALUE" : "CAPTURED");
            return result;
        }
        if ("StringArray".equals(valueType)) {
            String[] values = group.getStringArray(property);
            result.put("getter", "MaterialModel.getStringArray");
            result.put("raw_value", values == null ? null : safeStringList(values));
            result.put("raw_shape", values == null ? null : List.of(values.length));
            result.put("readback_status", values == null ? "UNVERIFIED_NULL_VALUE" : "CAPTURED");
            return result;
        }
        if ("StringMatrix".equals(valueType)) {
            String[][] values = group.getStringMatrix(property);
            result.put("getter", "MaterialModel.getStringMatrix");
            result.put("raw_value", values == null ? null : stringMatrix(values));
            result.put("raw_shape", stringMatrixShape(values));
            result.put("readback_status", values == null ? "UNVERIFIED_NULL_VALUE" : "CAPTURED");
            return result;
        }
        result.put("getter", "UNAVAILABLE_UNSUPPORTED_VALUE_TYPE");
        result.put("raw_value", null);
        result.put("raw_shape", null);
        result.put("readback_status", "UNVERIFIED_UNSUPPORTED_VALUE_TYPE");
        return result;
    }

    private static List<Integer> stringMatrixShape(String[][] matrix) {
        if (matrix == null) return null;
        List<Integer> shape = new ArrayList<>();
        shape.add(matrix.length);
        int columns = matrix.length == 0 || matrix[0] == null ? -1 : matrix[0].length;
        boolean rectangular = columns >= 0;
        for (String[] row : matrix) {
            if (row == null || row.length != columns) rectangular = false;
        }
        if (rectangular) {
            shape.add(columns);
        } else {
            for (String[] row : matrix) shape.add(row == null ? -1 : row.length);
        }
        return shape;
    }

    private static List<Integer> integerList(int[] values) {
        List<Integer> result = new ArrayList<>();
        for (int value : values) result.add(value);
        return result;
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
        // COMSOL 6.4 documents lm|lr|sr|li|si with lm as the shift-search default.
        // Its ARPACK notes relate eigenvalues closest to shift to the largest
        // eigenvalues of (A - shift*B)^-1 B. This selects a search method only;
        // it does not establish the TE mode result or replace analytic acceptance.
        bma.set("eigwhich", "lm");
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
