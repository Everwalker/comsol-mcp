import com.comsol.model.Coordsys;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.ParameterEntity;
import com.comsol.model.PropFeature;
import com.comsol.model.SelectionFeature;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.NumericalFeature;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.PhysicsField;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * W23 planar TE fixture scaffold and native property preflight.
 *
 * This builds geometry and COMSOL feature nodes, then reads back the exact
 * Port-boundary and PML-domain selections. It does not assign materials, set
 * an unverified Numeric property, create a solver sequence, or run any study
 * or solver. The returned report keeps those gates explicit so a successful
 * Java call cannot be mistaken for a native optical result.
 */
public final class NativeW23TEFixture {
    private static final String FIXTURE_ID = "w23_planar_te_port_api_preflight_v2";

    private NativeW23TEFixture() {}

    public static Object run(Model model, Map<String, Object> args) {
        if (model == null) throw new IllegalArgumentException("model is required");
        if (args != null && args.containsKey("phase")
                && !"api_preflight".equals(String.valueOf(args.get("phase")))) {
            throw new IllegalArgumentException("phase must be api_preflight");
        }

        model.label("W23 2D TE port API preflight - no solve");
        model.param().set("lambda0", "1.55[um]");
        model.param().set("f0", "193.414489032258[THz]");
        model.param().set("n_core", "1.60");
        model.param().set("n_clad", "1.45");
        model.param().set("h_core", "1[um]");
        model.param().set("x_in", "-10[um]");
        model.param().set("x_receiver", "8[um]");
        model.param().set("x_pml", "2[um]");
        model.param().set("y_extent", "8[um]");

        model.component().create("comp1");
        GeomSequence geom = model.component("comp1").geom().create("geom1", 2);
        geom.lengthUnit("um");

        // Tile the guide and receiver backing region without Boolean overlap:
        // normal guide x=[-10, 8] um, backing column x=[8, 10] um.
        rectangle(geom, "cladBottom", "x_in", "-y_extent", "x_receiver-x_in", "y_extent-h_core/2");
        rectangle(geom, "coreGuide", "x_in", "-h_core/2", "x_receiver-x_in", "h_core");
        rectangle(geom, "cladTop", "x_in", "h_core/2", "x_receiver-x_in", "y_extent-h_core/2");
        rectangle(geom, "cladBottomPml", "x_receiver", "-y_extent", "x_pml", "y_extent-h_core/2");
        rectangle(geom, "corePml", "x_receiver", "-h_core/2", "x_pml", "h_core");
        rectangle(geom, "cladTopPml", "x_receiver", "h_core/2", "x_pml", "y_extent-h_core/2");
        geom.run();

        SelectionFeature inputSelection = model.component("comp1").selection().create("selInputPort", "Box");
        SelectionFeature outputSelection = model.component("comp1").selection().create("selOutputPort", "Box");
        SelectionFeature pmlSelection = model.component("comp1").selection().create("selPmlDomains", "Box");

        // BoxSelection defaults entitydim to the geometry space dimension (2
        // here). Port features select boundaries, so set the exact entity
        // dimension before assigning the named selections. Bounds are in the
        // geometry length unit (um); the 1 nm tolerance keeps each boundary
        // edge fully inside its box without including a neighboring x-face.
        configureBox(inputSelection, 1, -10.001, -9.999, -8.001, 8.001);
        // This outer-edge output Port exists only to discover/validate the
        // installed Port and selection APIs. It is not the W23 science
        // receiver or a signal/reference coupling plane. The later science
        // builder must create its shared native measurement plane at x=8 um.
        configureBox(outputSelection, 1, 9.999, 10.001, -8.001, 8.001);
        // The PML region is the three domain tiles in x=[8,10] um.
        configureBox(pmlSelection, 2, 7.999, 10.001, -8.001, 8.001);

        Coordsys pml = model.component("comp1").coordSystem().create("pmlX", "geom1", "PML");
        Physics ewfd = model.component("comp1").physics().create("ewfd", "ElectromagneticWaves", "geom1");
        PhysicsFeature inputPort = ewfd.feature().create("portIn", "Port", 1);
        PhysicsFeature outputPort = ewfd.feature().create("portOut", "Port", 1);
        inputPort.selection().named("selInputPort");
        outputPort.selection().named("selOutputPort");

        int[] inputEntities = inputSelection.entities(1);
        int[] outputEntities = outputSelection.entities(1);
        int[] pmlEntities = pmlSelection.entities(2);
        int[] inputPortEntities = inputPort.selection().entities(1);
        int[] outputPortEntities = outputPort.selection().entities(1);

        Study study = model.study().create("std1");
        StudyFeature bmaInput = study.feature().create("bmaInput", "BoundaryModeAnalysis");
        StudyFeature bmaOutput = study.feature().create("bmaOutput", "BoundaryModeAnalysis");
        StudyFeature frequency = study.feature().create("freq", "Frequency");

        NumericalFeature intLine = model.result().numerical().create("intOutput", "IntLine");

        List<Map<String, Object>> fields = new ArrayList<>();
        for (String tag : ewfd.field().tags()) {
            PhysicsField field = ewfd.field(tag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tag);
            row.put("field", field.field());
            row.put("fieldname", Arrays.asList(field.fieldname()));
            row.put("components", Arrays.asList(field.component()));
            fields.add(row);
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("fixture_id", FIXTURE_ID);
        result.put("status", "BUILT_FOR_API_PREFLIGHT_ONLY");
        result.put("geometry", Map.of(
                "space_dimension", geom.getSDim(),
                "domains", geom.getNDomains(),
                "bounding_box", geom.getBoundingBox(),
                "length_unit", geom.lengthUnit(),
                "normal_guide_x_um", List.of(-10.0, 8.0),
                "receiver_backing_x_um", List.of(8.0, 10.0),
                "core_y_um", List.of(-0.5, 0.5),
                "outer_y_um", List.of(-8.0, 8.0)));
        result.put("port_plane_semantics", Map.of(
                "scope", "API_PROPERTY_READBACK_ONLY",
                "api_only_output_port_x_um", 10.0,
                "science_receiver_plane_x_um", 8.0,
                "output_selection_is_science_receiver", false,
                "must_not_reuse_as_coupling_plane", true,
                "science_receiver_status", "REQUIRED_IN_LATER_SCIENCE_BUILDER_NOT_BUILT"));
        result.put("ports", List.of(
                inspect(inputPort, new String[]{"PortType", "PortName", "PortExcitation", "SlitType"}),
                inspect(outputPort, new String[]{"PortType", "PortName", "PortExcitation", "SlitType"})));
        result.put("port_selections", List.of(
                inspect(inputSelection, new String[]{"entitydim", "condition", "xmin", "xmax", "ymin", "ymax"}),
                inspect(outputSelection, new String[]{"entitydim", "condition", "xmin", "xmax", "ymin", "ymax"})));
        result.put("pml", inspect(pml, new String[]{"ScalingType", "stretchingType", "PMLfactor", "PMLgamma", "typicalWavelength"}));
        result.put("pml_selection", inspect(pmlSelection, new String[]{"entitydim", "condition", "xmin", "xmax", "ymin", "ymax"}));
        result.put("study_steps", List.of(
                inspect(bmaInput, new String[]{"PortName", "ModeAnalysisFrequency", "DesiredNumberOfModes"}),
                inspect(bmaOutput, new String[]{"PortName", "ModeAnalysisFrequency", "DesiredNumberOfModes"}),
                inspect(frequency, new String[]{"plist"})));
        result.put("ewfd_fields", fields);
        result.put("intline", inspect(intLine, new String[]{"data", "expr", "unit", "selection"}));
        result.put("materials", "NOT_CONFIGURED");
        result.put("port_type_numeric", "NOT_SET; exact property/readback gate only");
        result.put("port_entity_readback", List.of(
                selectionReadback("selInputPort", 1, inputEntities, inputPortEntities),
                selectionReadback("selOutputPort", 1, outputEntities, outputPortEntities)));
        result.put("pml_entity_readback", selectionReadback("selPmlDomains", 2, pmlEntities, null));
        result.put("te_field_configuration", "NOT_SET; exact field/component readback gate only");
        result.put("study_or_solver_invoked", false);
        result.put("native_result", "NOT_RUN");
        return result;
    }

    private static void rectangle(GeomSequence geom, String tag, String x, String y, String width, String height) {
        geom.feature().create(tag, "Rectangle");
        geom.feature(tag).set("base", "corner");
        geom.feature(tag).set("pos", new String[]{x, y});
        geom.feature(tag).set("size", new String[]{width, height});
    }

    private static void configureBox(SelectionFeature selection, int entityDimension,
                                     double xmin, double xmax, double ymin, double ymax) {
        selection.set("entitydim", entityDimension);
        selection.set("condition", "inside");
        selection.set("xmin", xmin);
        selection.set("xmax", xmax);
        selection.set("ymin", ymin);
        selection.set("ymax", ymax);
    }

    private static Map<String, Object> selectionReadback(String tag, int entityDimension,
                                                          int[] entities, int[] assignedEntities) {
        List<Integer> entityIds = safeIntList(entities);
        List<Integer> assignedIds = safeIntList(assignedEntities);
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("selection_tag", tag);
        report.put("entity_dimension", entityDimension);
        report.put("entity_ids", entityIds);
        report.put("entity_count", entityIds.size());
        if (assignedEntities != null) {
            report.put("assigned_entity_ids", assignedIds);
            report.put("assigned_entity_count", assignedIds.size());
            report.put("matches_assigned_selection", entityIds.equals(assignedIds));
        }
        return report;
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
        }, entity.getClass().getName(), entity.getClass().getSimpleName(), keys);
    }

    private static Map<String, Object> inspect(PropFeature entity, String[] keys) {
        return inspect(new PropertyReader() {
            public String[] properties() { return entity.properties(); }
            public boolean hasProperty(String name) { return entity.hasProperty(name); }
            public String[] allowed(String name) { return entity.getAllowedPropertyValues(name); }
            public String valueType(String name) { return entity.getValueType(name); }
            public String stringValue(String name) { return entity.getString(name); }
        }, entity.getClass().getName(), entity.getClass().getSimpleName(), keys);
    }

    private static Map<String, Object> inspect(PropertyReader reader, String className, String type, String[] keys) {
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("feature_type", type);
        report.put("class_name", className);
        report.put("property_names", safeList(reader.properties()));
        Map<String, Object> properties = new LinkedHashMap<>();
        for (String key : keys) {
            Map<String, Object> entry = new LinkedHashMap<>();
            try {
                boolean present = reader.hasProperty(key);
                entry.put("has_property_exact", present);
                if (present) {
                    entry.put("value_type", reader.valueType(key));
                    try { entry.put("allowed_values", safeList(reader.allowed(key))); }
                    catch (Throwable error) { entry.put("allowed_values_error", error.getClass().getName()); }
                    try { entry.put("string_readback", reader.stringValue(key)); }
                    catch (Throwable error) { entry.put("string_readback_error", error.getClass().getName()); }
                }
            } catch (Throwable error) {
                entry.put("probe_error", error.getClass().getName());
            }
            properties.put(key, entry);
        }
        report.put("requested_properties", properties);
        return report;
    }

    private static List<String> safeList(String[] values) {
        return values == null ? List.of() : Arrays.asList(values);
    }

    private static List<Integer> safeIntList(int[] values) {
        List<Integer> result = new ArrayList<>();
        if (values != null) {
            for (int value : values) result.add(value);
        }
        return result;
    }
}
