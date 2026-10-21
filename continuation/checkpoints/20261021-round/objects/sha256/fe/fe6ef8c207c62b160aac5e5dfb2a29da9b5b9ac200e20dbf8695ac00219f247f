import com.comsol.model.CommonFeature;
import com.comsol.model.Cpl;
import com.comsol.model.Model;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Installs the W21-to-W23 native field bridge without solving.
 *
 * The source is a registered W21 stage solution in this same COMSOL Model.
 * The managed Python adapter resolves the W21 checkpoint/ObservationRef and
 * supplies only the exact source tags, unit-bound field identifiers, and
 * native selection identities.  This class does not accept field arrays,
 * invoke Study.run, or invoke a solver.
 */
public final class NativeW23DeformationMappingFixture {
    private static final String FIXTURE_ID = "w23_w21_general_extrusion_deformation_v1";
    private static final Pattern TAG = Pattern.compile("^[A-Za-z][A-Za-z0-9_]{0,62}$");
    private static final Pattern EXPR = Pattern.compile("^[A-Za-z_][A-Za-z0-9_]*(?:\\.[A-Za-z_][A-Za-z0-9_]*)*$");
    private static final Pattern UNIT = Pattern.compile("^[A-Za-z][A-Za-z0-9*/^.-]*$");

    private NativeW23DeformationMappingFixture() {}

    public static Object run(Model model, Map<String, Object> args) {
        if (model == null) throw new IllegalArgumentException("model is required");
        if (args == null || !"configure".equals(String.valueOf(args.get("phase"))))
            throw new IllegalArgumentException("phase must be configure");
        return configure(model, args);
    }

    private static Map<String, Object> configure(Model model, Map<String, Object> args) {
        String modelTag = requiredTag(args, "model_tag");
        if (!modelTag.equals(model.tag())) throw new IllegalStateException("bound Model tag differs from W21 mapping plan");
        String sourceComponent = requiredTag(args, "source_component");
        String targetComponent = requiredTag(args, "target_component");
        String sourceGeometry = requiredTag(args, "source_geometry");
        String targetGeometry = requiredTag(args, "target_geometry");
        String sourceSelection = requiredTag(args, "source_selection");
        String targetSelection = requiredTag(args, "target_selection");
        String sourceSolution = requiredTag(args, "source_solution");
        String sourceDataset = requiredTag(args, "source_dataset");
        String sourceStudy = requiredTag(args, "source_study");
        String sourceFrame = requiredTag(args, "source_frame");
        if (!"material".equals(sourceFrame))
            throw new IllegalArgumentException("source_frame must be material to avoid deformation-coordinate feedback");
        int sourceOuter = requiredPositiveInt(args, "source_outer");
        int sourceInner = requiredPositiveInt(args, "source_inner");
        int sourceSolnum = requiredPositiveInt(args, "source_solnum");
        double sourceTimeS = requiredFiniteDouble(args, "source_time_s");
        if (sourceTimeS < 0.0) throw new IllegalArgumentException("source_time_s must be nonnegative");
        String operatorTag = optionalTag(args, "operator_tag", "w23defmap");
        String deformationTag = optionalTag(args, "deformation_tag", "w23prescr");
        Map<String, String> sourceExpressions = requiredExpressions(
                args.get("source_expressions"), model.component().tags(), sourceComponent);
        Map<String, String> sourceUnits = requiredUnits(args.get("source_units"));
        for (String axis : new String[] {"x", "y", "z"}) {
            if (!"m".equals(sourceUnits.get(axis)))
                throw new IllegalArgumentException("source displacement " + axis + " must have native unit m");
        }
        Map<String, Object> outerParameters = requiredOuterParameters(args.get("outer_parameters"));
        requireContains(model.component().tags(), sourceComponent, "source component");
        requireContains(model.component().tags(), targetComponent, "target component");
        requireContains(model.component(sourceComponent).geom().tags(), sourceGeometry, "source geometry");
        requireContains(model.component(targetComponent).geom().tags(), targetGeometry, "target geometry");
        requireContains(model.component(sourceComponent).selection().tags(), sourceSelection, "source selection");
        requireContains(model.component(targetComponent).selection().tags(), targetSelection, "target selection");
        requireContains(model.sol().tags(), sourceSolution, "source solver sequence");
        requireContains(model.result().dataset().tags(), sourceDataset, "source dataset");
        if (!sourceStudy.equals(model.sol(sourceSolution).study()))
            throw new IllegalStateException("native source solver study differs from the W21 checkpoint");
        double[] storedTimes = model.sol(sourceSolution).getPVals();
        if (storedTimes == null || sourceInner > storedTimes.length
                || Math.abs(storedTimes[sourceInner - 1] - sourceTimeS)
                   > Math.max(1e-15, Math.abs(sourceTimeS) * 1e-12))
            throw new IllegalStateException("native source solution does not contain the exact W21 stored time/index");

        int[] sourceEntities = model.component(sourceComponent).selection(sourceSelection).entities(3);
        int[] targetEntities = model.component(targetComponent).selection(targetSelection).entities(3);
        if (sourceEntities == null || sourceEntities.length == 0)
            throw new IllegalStateException("native source domain selection is empty");
        if (targetEntities == null || targetEntities.length == 0)
            throw new IllegalStateException("native optical deformation selection is empty");
        if (!sameSet(sourceEntities, requiredEntityIds(args.get("source_selection_entity_ids"), "source selection IDs")))
            throw new IllegalStateException("native source selection IDs changed after mapping-plan creation");
        if (!sameSet(targetEntities, requiredEntityIds(args.get("target_selection_entity_ids"), "target selection IDs")))
            throw new IllegalStateException("native target selection IDs changed after mapping-plan creation");

        Cpl coupling = model.component(targetComponent).cpl().create(
                operatorTag, "GeneralExtrusion", sourceGeometry);
        coupling.selection().named(sourceSelection);
        coupling.set("opname", operatorTag);
        coupling.set("dstmap", new String[] {"x", "y", "z"});
        coupling.set("srcframe", sourceFrame);
        coupling.set("usesrcmap", "off");
        coupling.set("method", "usetol");
        coupling.set("usenan", "on");
        coupling.set("exttol", 0.0);

        String[] displacement = new String[] {
            mappedField(targetComponent, sourceComponent, sourceSolution, operatorTag,
                    sourceExpressions.get("x"), sourceInner, outerParameters),
            mappedField(targetComponent, sourceComponent, sourceSolution, operatorTag,
                    sourceExpressions.get("y"), sourceInner, outerParameters),
            mappedField(targetComponent, sourceComponent, sourceSolution, operatorTag,
                    sourceExpressions.get("z"), sourceInner, outerParameters)
        };
        CommonFeature deformation = model.component(targetComponent).common().create(
                deformationTag, "PrescribedDeformation");
        deformation.selection().named(targetSelection);
        deformation.set("prescribedDeformation", displacement);

        Map<String, Object> couplingReadback = new LinkedHashMap<>();
        couplingReadback.put("operator_name", coupling.getString("opname"));
        couplingReadback.put("source_selection_tag", coupling.selection().named());
        couplingReadback.put("destination_map", Arrays.asList(coupling.getStringArray("dstmap")));
        couplingReadback.put("source_frame", coupling.getString("srcframe"));
        couplingReadback.put("use_source_map", coupling.getString("usesrcmap"));
        couplingReadback.put("mesh_search_method", coupling.getString("method"));
        couplingReadback.put("outside_source_behavior", coupling.getString("usenan"));
        couplingReadback.put("extrapolation_tolerance", coupling.getDouble("exttol"));
        couplingReadback.put("source_entity_ids", intList(sourceEntities));
        Map<String, Object> deformationReadback = new LinkedHashMap<>();
        deformationReadback.put("feature_type", "PrescribedDeformation");
        deformationReadback.put("selection_tag", deformation.selection().named());
        deformationReadback.put("expressions", Arrays.asList(deformation.getStringArray("prescribedDeformation")));
        deformationReadback.put("entity_ids", intList(deformation.selection().entities(3)));

        if (!operatorTag.equals(couplingReadback.get("operator_name"))
                || !sourceSelection.equals(couplingReadback.get("source_selection_tag"))
                || !Arrays.asList("x", "y", "z").equals(couplingReadback.get("destination_map"))
                || !sourceFrame.equals(couplingReadback.get("source_frame"))
                || !"off".equals(couplingReadback.get("use_source_map"))
                || !"usetol".equals(couplingReadback.get("mesh_search_method"))
                || !"on".equals(couplingReadback.get("outside_source_behavior"))
                || ((Double) couplingReadback.get("extrapolation_tolerance")) != 0.0)
            throw new IllegalStateException("General Extrusion property readback differs from its frozen plan");
        if (!targetSelection.equals(deformationReadback.get("selection_tag"))
                || !Arrays.asList(displacement).equals(deformationReadback.get("expressions")))
            throw new IllegalStateException("Prescribed Deformation readback differs from its frozen plan");

        Map<String, Object> source = new LinkedHashMap<>();
        source.put("model_tag", modelTag);
        source.put("component", sourceComponent);
        source.put("geometry", sourceGeometry);
        source.put("selection", sourceSelection);
        source.put("solution", sourceSolution);
        source.put("dataset", sourceDataset);
        source.put("study", sourceStudy);
        source.put("outer", sourceOuter);
        source.put("inner", sourceInner);
        source.put("solnum", sourceSolnum);
        source.put("time_s", sourceTimeS);
        source.put("field_expressions", sourceExpressions);
        source.put("field_units", sourceUnits);
        source.put("solution_selector_expression", "setind(t," + sourceInner + ")");
        source.put("native_stored_time_at_inner_index_s", storedTimes[sourceInner - 1]);
        source.put("outer_parameters", outerParameters);
        source.put("component_qualified_expression_prefix", sourceComponent + ".");

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("fixture_id", FIXTURE_ID);
        result.put("status", "NATIVE_ADAPTER_CONFIGURED_NOT_SOLVED");
        result.put("native_result", "NOT_RUN");
        result.put("mapping_complete", false);
        result.put("study_or_solver_invoked", false);
        result.put("source", source);
        result.put("target", Map.of("component", targetComponent, "geometry", targetGeometry,
                "selection", targetSelection, "entity_dimension", 3, "entity_ids", intList(targetEntities),
                "coordinate_frame", "spatial", "coordinate_unit", "m", "vector_basis", "global_xyz"));
        result.put("general_extrusion", couplingReadback);
        result.put("prescribed_deformation", deformationReadback);
        result.put("expression_context", Map.of(
                "operator_component", targetComponent,
                "source_field_component", sourceComponent,
                "order", "targetComponent.operator(withsol(sourceSolution,sourceComponent.field,selectors))"));
        result.put("held_out_residual", Map.of(
                "status", "NOT_RUN",
                "source_route", "direct W21 source Dataset point evaluation without General Extrusion",
                "mapped_route", "destination evaluation through the configured General Extrusion",
                "required", "identical held-out spatial coordinates, solution/time identity, units, and pre-frozen absolute/relative residual thresholds"));
        return result;
    }

    private static String mappedField(String targetComponent, String sourceComponent,
                                      String solution, String operator, String sourceExpression,
                                      int timeIndex, Map<String, Object> parameters) {
        List<String> selectors = new ArrayList<>();
        selectors.add("setind(t," + timeIndex + ")");
        for (Map.Entry<String, Object> entry : parameters.entrySet()) {
            @SuppressWarnings("unchecked")
            Map<String, Object> item = (Map<String, Object>) entry.getValue();
            selectors.add("setval(" + entry.getKey() + "," + item.get("value") + "[" + item.get("unit") + "])");
        }
        String exactSourceField = sourceComponent + "." + sourceExpression;
        String sourceValue = "withsol('" + solution + "'," + exactSourceField + ","
                + String.join(",", selectors) + ")";
        return targetComponent + "." + operator + "(" + sourceValue + ")";
    }

    private static Map<String, String> requiredExpressions(Object raw, String[] componentTags,
                                                           String sourceComponent) {
        if (!(raw instanceof Map)) throw new IllegalArgumentException("source_expressions must map x/y/z");
        @SuppressWarnings("unchecked") Map<String, Object> input = (Map<String, Object>) raw;
        if (!input.keySet().equals(java.util.Set.of("x", "y", "z")))
            throw new IllegalArgumentException("source_expressions must contain exactly x, y, and z");
        Map<String, String> result = new LinkedHashMap<>();
        for (String axis : new String[] {"x", "y", "z"}) {
            Object item = input.get(axis);
            if (!(item instanceof String) || !EXPR.matcher((String) item).matches())
                throw new IllegalArgumentException("source displacement fields must be simple native identifiers");
            String expression = (String) item;
            for (String componentTag : componentTags) {
                if (!expression.startsWith(componentTag + ".")) continue;
                if (!sourceComponent.equals(componentTag))
                    throw new IllegalArgumentException("source displacement expression belongs to another component");
                expression = expression.substring(componentTag.length() + 1);
                break;
            }
            if (!EXPR.matcher(expression).matches())
                throw new IllegalArgumentException("source displacement identifier is malformed after qualification");
            result.put(axis, expression);
        }
        if (new java.util.HashSet<>(result.values()).size() != 3)
            throw new IllegalArgumentException("source displacement expressions must be distinct");
        return result;
    }

    private static Map<String, String> requiredUnits(Object raw) {
        if (!(raw instanceof Map)) throw new IllegalArgumentException("source_units must map x/y/z");
        @SuppressWarnings("unchecked") Map<String, Object> input = (Map<String, Object>) raw;
        if (!input.keySet().equals(java.util.Set.of("x", "y", "z")))
            throw new IllegalArgumentException("source_units must contain exactly x, y, and z");
        Map<String, String> result = new LinkedHashMap<>();
        for (String axis : new String[] {"x", "y", "z"}) {
            Object item = input.get(axis);
            if (!(item instanceof String) || !UNIT.matcher((String) item).matches())
                throw new IllegalArgumentException("source displacement unit is malformed");
            result.put(axis, (String) item);
        }
        return result;
    }

    private static Map<String, Object> requiredOuterParameters(Object raw) {
        if (!(raw instanceof List)) throw new IllegalArgumentException("outer_parameters must be a native list");
        @SuppressWarnings("unchecked") List<Object> list = (List<Object>) raw;
        Map<String, Object> result = new LinkedHashMap<>();
        for (Object value : list) {
            if (!(value instanceof Map)) throw new IllegalArgumentException("outer parameter row is malformed");
            @SuppressWarnings("unchecked") Map<String, Object> row = (Map<String, Object>) value;
            if (!row.keySet().equals(java.util.Set.of("name", "value", "unit")))
                throw new IllegalArgumentException("outer parameter row must contain name, value, and unit");
            String name = String.valueOf(row.get("name"));
            if (!Pattern.matches("^[A-Za-z_][A-Za-z0-9_]*$", name) || result.containsKey(name))
                throw new IllegalArgumentException("outer parameter names must be unique COMSOL identifiers");
            double number = requiredFiniteNumber(row.get("value"), "outer parameter value");
            Object unit = row.get("unit");
            if (!(unit instanceof String) || !UNIT.matcher((String) unit).matches())
                throw new IllegalArgumentException("outer parameter unit is malformed");
            result.put(name, Map.of("value", Double.toString(number), "unit", unit));
        }
        return result;
    }

    private static String requiredTag(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (!(value instanceof String) || !TAG.matcher((String) value).matches())
            throw new IllegalArgumentException(key + " must be a simple COMSOL tag");
        return (String) value;
    }

    private static String optionalTag(Map<String, Object> args, String key, String fallback) {
        Object value = args.get(key);
        if (value == null) return fallback;
        if (!(value instanceof String) || !TAG.matcher((String) value).matches())
            throw new IllegalArgumentException(key + " must be a simple COMSOL tag");
        return (String) value;
    }

    private static int requiredPositiveInt(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (!(value instanceof Number) || value instanceof Boolean || ((Number) value).intValue() < 1
                || ((Number) value).doubleValue() != ((Number) value).intValue())
            throw new IllegalArgumentException(key + " must be a positive integer");
        return ((Number) value).intValue();
    }

    private static double requiredFiniteDouble(Map<String, Object> args, String key) {
        return requiredFiniteNumber(args.get(key), key);
    }

    private static double requiredFiniteNumber(Object value, String label) {
        if (!(value instanceof Number) || value instanceof Boolean
                || !Double.isFinite(((Number) value).doubleValue()))
            throw new IllegalArgumentException(label + " must be finite numeric data");
        return ((Number) value).doubleValue();
    }

    private static void requireContains(String[] values, String expected, String label) {
        if (values == null || !Arrays.asList(values).contains(expected))
            throw new IllegalStateException("native " + label + " is missing: " + expected);
    }

    private static List<Integer> intList(int[] values) {
        List<Integer> result = new ArrayList<>();
        for (int value : values) result.add(value);
        return result;
    }

    private static int[] requiredEntityIds(Object raw, String label) {
        if (!(raw instanceof List) || ((List<?>) raw).isEmpty())
            throw new IllegalArgumentException(label + " must be a nonempty registered list");
        List<?> values = (List<?>) raw;
        int[] result = new int[values.size()];
        java.util.Set<Integer> unique = new java.util.LinkedHashSet<>();
        for (int i = 0; i < result.length; i++) {
            Object value = values.get(i);
            if (!(value instanceof Number) || value instanceof Boolean
                    || ((Number) value).longValue() < 1
                    || ((Number) value).doubleValue() != ((Number) value).longValue())
                throw new IllegalArgumentException(label + " must contain unique positive integer entity IDs");
            result[i] = ((Number) value).intValue();
            if (!unique.add(result[i]))
                throw new IllegalArgumentException(label + " must contain unique positive integer entity IDs");
        }
        return result;
    }

    private static boolean sameSet(int[] left, int[] right) {
        if (left == null || right == null || left.length != right.length) return false;
        int[] a = left.clone(), b = right.clone();
        Arrays.sort(a); Arrays.sort(b);
        return Arrays.equals(a, b);
    }
}
