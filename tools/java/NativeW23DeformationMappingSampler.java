import com.comsol.model.Model;
import com.comsol.model.NumericalFeature;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Read-only native sampler for the W21-to-W23 deformation-map comparison.
 * It evaluates the registered W21 source fields directly and through the
 * destination GeneralExtrusion at identical coordinates, then removes only
 * the two request-owned Interp nodes. It never submits a study or solver.
 */
public final class NativeW23DeformationMappingSampler {
    private static final String[] AXES = {"x", "y", "z"};

    private NativeW23DeformationMappingSampler() {}

    public static Object run(Model model, Map<String, Object> args) {
        if (model == null) throw new IllegalArgumentException("model is required");
        if (args == null || !"sample_mapping".equals(String.valueOf(args.get("phase"))))
            throw new IllegalArgumentException("phase must be sample_mapping");
        Map<String, Object> contract = map(args.get("contract"), "sample contract");
        return sample(model, contract);
    }

    private static Map<String, Object> sample(Model model, Map<String, Object> contract) {
        if (!"READY_FOR_ONE_MANAGED_READBACK_CALL".equals(contract.get("status"))
                || !Boolean.FALSE.equals(contract.get("study_or_solver_invoked")))
            throw new IllegalArgumentException("a validated read-only native sampling contract is required");
        String contractId = string(contract.get("sample_contract_id"), "sample contract id");
        String coordinateHash = string(contract.get("coordinate_sha256"), "coordinate hash");
        String[] tags = stringArray(contract.get("temporary_interpolation_tags"), 2, "temporary tags");
        if (!tags[0].equals("w23direct" + contractId.substring(0, 10))
                || !tags[1].equals("w23mapped" + contractId.substring(0, 10)))
            throw new IllegalArgumentException("temporary tags do not derive from the contract id");
        List<List<Double>> points = points(contract.get("coordinates_m"));
        if (points.size() != integer(contract.get("sample_count"), 1, "sample count"))
            throw new IllegalArgumentException("sample count differs from the registered coordinates");
        double[][] coordinates = coordinateMatrix(points);

        Map<String, Object> binding = map(contract.get("plan_source"), "source binding");
        Map<String, Object> selections = map(contract.get("native_selections"), "native selections");
        Map<String, Object> sourceSelection = map(selections.get("source"), "source selection");
        Map<String, Object> targetSelection = map(selections.get("target"), "target selection");
        String modelTag = tag(binding.get("model_tag"), "model tag");
        String dataset = tag(binding.get("source_dataset"), "dataset tag");
        String solution = tag(binding.get("source_solution"), "solution tag");
        int outer = integer(binding.get("outer"), 1, "outer solution");
        int inner = integer(binding.get("inner"), 1, "inner solution");
        int solnum = integer(binding.get("solnum"), 1, "solution number");
        int revision = integer(binding.get("revision"), 0, "managed revision");
        if (!modelTag.equals(model.tag())
                || !Arrays.asList(model.sol().tags()).contains(solution)
                || !Arrays.asList(model.result().dataset().tags()).contains(dataset))
            throw new IllegalStateException("bound model, source solution, or dataset is absent");
        double[] storedTimes = model.sol(solution).getPVals();
        double time = numeric(binding.get("time_s"), "source time");
        if (storedTimes == null || inner > storedTimes.length
                || Math.abs(storedTimes[inner - 1] - time) > Math.max(1e-15, Math.abs(time) * 1e-12))
            throw new IllegalStateException("source solution/time index differs from the authenticated W21 checkpoint");
        verifySelection(model, sourceSelection, "source");
        verifySelection(model, targetSelection, "target");
        String[] directExpr = expressions(contract.get("direct_expressions"), "direct source");
        String[] mappedExpr = expressions(contract.get("mapped_expressions"), "mapped");
        if (containsNumerical(model, tags[0]) || containsNumerical(model, tags[1]))
            throw new IllegalStateException("refusing to replace pre-existing caller Interp nodes");

        NumericalFeature direct = null;
        NumericalFeature mapped = null;
        Map<String, Object> sourceRaw = null, mappedRaw = null;
        Throwable operationFailure = null, cleanupFailure = null;
        try {
            direct = model.result().numerical().create(tags[0], "Interp");
            mapped = model.result().numerical().create(tags[1], "Interp");
            configure(direct, dataset, directExpr, coordinates, outer, solnum, sourceSelection);
            configure(mapped, dataset, mappedExpr, coordinates, outer, solnum, targetSelection);
            sourceRaw = evaluate(direct, contractId, coordinateHash, points, binding,
                    sourceSelection, "direct_dataset_point_evaluation",
                    "native_source_component_expression", null);
            mappedRaw = evaluate(mapped, contractId, coordinateHash, points, binding,
                    targetSelection, "general_extrusion_destination_evaluation",
                    "destination_component_general_extrusion", tag(contract.get("operator_tag"), "operator tag"));
        } catch (Throwable error) {
            operationFailure = error;
        } finally {
            try {
                if (containsNumerical(model, tags[1])) model.result().numerical().remove(tags[1]);
                if (containsNumerical(model, tags[0])) model.result().numerical().remove(tags[0]);
                if (containsNumerical(model, tags[0]) || containsNumerical(model, tags[1]))
                    throw new IllegalStateException("request-owned interpolation node remains after cleanup");
            } catch (Throwable error) {
                cleanupFailure = error;
            }
        }
        Map<String, Object> cleanup = new LinkedHashMap<>();
        cleanup.put("created", direct != null || mapped != null);
        cleanup.put("removed", cleanupFailure == null);
        cleanup.put("cleanup_failed", cleanupFailure != null);
        cleanup.put("tags", Arrays.asList(tags));
        cleanup.put("error", cleanupFailure == null ? "" : cleanupFailure.toString());
        if (operationFailure != null)
            throw new IllegalStateException("native mapping samples failed; cleanup=" + cleanup, operationFailure);
        if (cleanupFailure != null)
            throw new IllegalStateException("native mapping interpolation cleanup failed", cleanupFailure);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("sample_contract_id", contractId);
        result.put("coordinate_sha256", coordinateHash);
        result.put("sample_count", points.size());
        result.put("coordinates_m", points);
        result.put("coordinate_unit", "m");
        result.put("value_unit", "m");
        result.put("native_result", "COMSOL_NATIVE_RAW");
        result.put("study_or_solver_invoked", false);
        result.put("managed_revision", revision);
        result.put("source", sourceRaw);
        result.put("mapped", mappedRaw);
        result.put("cleanup", cleanup);
        return result;
    }

    private static void verifySelection(Model model, Map<String, Object> sel, String role) {
        String comp = tag(sel.get("component"), role + " component");
        String geom = tag(sel.get("geometry"), role + " geometry");
        String selectionTag = tag(sel.get("tag"), role + " selection");
        if (!Arrays.asList(model.component().tags()).contains(comp)
                || !Arrays.asList(model.component(comp).geom().tags()).contains(geom)
                || !Arrays.asList(model.component(comp).selection().tags()).contains(selectionTag))
            throw new IllegalStateException(role + " native component/geometry/selection is absent");
        int dimension = integer(sel.get("entity_dimension"), 3, role + " selection dimension");
        int[] actual = model.component(comp).selection(selectionTag).entities(dimension);
        int[] expected = integers(sel.get("entity_ids"), role + " selection IDs");
        if (dimension != 3 || !sameSet(expected, actual))
            throw new IllegalStateException(role + " native domain selection changed after plan creation");
        if (!"m".equals(sel.get("coordinate_unit")))
            throw new IllegalStateException(role + " coordinate units must be metres");
    }

    private static void configure(NumericalFeature feature, String dataset, String[] expr,
            double[][] coords, int outer, int solnum, Map<String, Object> sel) {
        feature.set("data", dataset);
        feature.set("expr", expr);
        feature.set("unit", new String[]{"m", "m", "m"});
        feature.set("solnum", Integer.toString(solnum));
        feature.set("outersolnum", outer);
        feature.set("coorderr", "on");
        feature.set("matherr", "on");
        feature.set("ext", 0.0);
        feature.setInterpolationCoordinates(coords);
        feature.selection().named(String.valueOf(sel.get("tag")));
        if (!dataset.equals(feature.getString("data"))
                || !Arrays.equals(expr, feature.getStringArray("expr"))
                || !Arrays.equals(new String[]{"m", "m", "m"}, feature.getStringArray("unit"))
                || !Integer.toString(solnum).equals(feature.getString("solnum"))
                || !Integer.toString(outer).equals(feature.getString("outersolnum"))
                || !feature.getBoolean("coorderr") || !feature.getBoolean("matherr")
                || feature.getDouble("ext") != 0.0
                || !String.valueOf(sel.get("tag")).equals(feature.selection().named())
                || !String.valueOf(sel.get("geometry")).equals(feature.selection().geom())
                || feature.selection().dim() != 3
                || !sameSet(integers(sel.get("entity_ids"), "selection IDs"), feature.selection().entities()))
            throw new IllegalStateException("Interp configuration or native selection readback mismatch");
    }

    private static Map<String, Object> evaluate(NumericalFeature feature, String contractId,
            String coordinateHash, List<List<Double>> points, Map<String, Object> binding,
            Map<String, Object> sel, String route, String expressionRoute, String operatorTag) {
        feature.run();
        double[][][] real = feature.getData();
        double[][][] imag = feature.getImagData();
        double[][] coords = feature.getCoordinates();
        if (real == null || real.length != 3 || coords == null || coords.length != 3)
            throw new IllegalStateException("Interp did not return three field components and xyz coordinates");
        if (imag == null) {
            if (feature.isComplex()) throw new IllegalStateException("complex Interp response lost its imaginary values");
            imag = new double[3][][];
            for (int axis = 0; axis < 3; axis++) {
                imag[axis] = new double[real[axis].length][];
                for (int solution = 0; solution < real[axis].length; solution++)
                    imag[axis][solution] = new double[real[axis][solution].length];
            }
        }
        List<List<Double>> reRows = new ArrayList<>(), imRows = new ArrayList<>();
        for (int axis = 0; axis < 3; axis++) {
            if (real[axis] == null || imag[axis] == null || real[axis].length != 1 || imag[axis].length != 1
                    || real[axis][0].length != points.size() || imag[axis][0].length != points.size()
                    || coords[axis].length != points.size())
                throw new IllegalStateException("Interp returned unexpected solution or point dimensions");
        }
        for (int point = 0; point < points.size(); point++) {
            List<Double> re = new ArrayList<>(), im = new ArrayList<>();
            for (int axis = 0; axis < 3; axis++) {
                double expected = points.get(point).get(axis);
                if (Math.abs(coords[axis][point] - expected) > 1e-12)
                    throw new IllegalStateException("Interp coordinates differ from W21's registered point list");
                double rv = real[axis][0][point], iv = imag[axis][0][point];
                if (!Double.isFinite(rv) || !Double.isFinite(iv))
                    throw new IllegalStateException("native sample is NaN/outside source; extrapolation is disabled");
                re.add(rv); im.add(iv);
            }
            reRows.add(re); imRows.add(im);
        }
        Map<String, Object> rawBinding = new LinkedHashMap<>();
        for (String key : new String[]{"project_id", "model_ref", "model_tag", "checkpoint_id",
                "observation_ref", "source_solution", "source_dataset", "outer", "inner", "solnum",
                "time_s", "source_solution_fingerprint"}) rawBinding.put(key, binding.get(key));
        Map<String, Object> raw = new LinkedHashMap<>();
        raw.put("evidence_scope", "COMSOL_NATIVE_RAW");
        raw.put("sample_contract_id", contractId);
        raw.put("coordinate_sha256", coordinateHash);
        raw.put("sample_count", points.size());
        raw.put("coordinates_m", points);
        raw.put("coordinate_unit", "m");
        raw.put("value_unit", "m");
        raw.put("binding", rawBinding);
        raw.put("route", route);
        raw.put("source_expression_route", expressionRoute);
        raw.put("selection", sel.get("tag"));
        raw.put("selection_geometry", sel.get("geometry"));
        raw.put("selection_entity_dimension", sel.get("entity_dimension"));
        raw.put("selection_entity_ids", sel.get("entity_ids"));
        raw.put("geometry_frame", sel.get("frame"));
        raw.put("dataset", feature.getString("data"));
        raw.put("expressions", Arrays.asList(feature.getStringArray("expr")));
        raw.put("real_solution_selection", feature.getString("solnum"));
        raw.put("outer_solution_selection", feature.getString("outersolnum"));
        raw.put("complex_readback", feature.isComplex());
        raw.put("displacement_real_m", reRows);
        raw.put("displacement_imag_m", imRows);
        if (operatorTag != null) raw.put("operator_tag", operatorTag);
        return raw;
    }

    private static boolean containsNumerical(Model model, String tag) {
        return Arrays.asList(model.result().numerical().tags()).contains(tag);
    }

    private static String[] expressions(Object raw, String label) {
        Map<String, Object> map = map(raw, label);
        String[] values = new String[3];
        Set<String> unique = new LinkedHashSet<>();
        for (int i = 0; i < AXES.length; i++) {
            values[i] = string(map.get(AXES[i]), label + " " + AXES[i]);
            if (!unique.add(values[i])) throw new IllegalArgumentException(label + " contains duplicate expressions");
        }
        return values;
    }

    private static List<List<Double>> points(Object raw) {
        if (!(raw instanceof List) || ((List<?>) raw).isEmpty())
            throw new IllegalArgumentException("registered xyz coordinates are required");
        List<List<Double>> result = new ArrayList<>();
        Set<String> unique = new LinkedHashSet<>();
        for (Object item : (List<?>) raw) {
            if (!(item instanceof List) || ((List<?>) item).size() != 3)
                throw new IllegalArgumentException("every registered point must be xyz");
            List<Double> point = new ArrayList<>();
            for (Object coordinate : (List<?>) item) point.add(numeric(coordinate, "coordinate"));
            String key = point.toString();
            if (!unique.add(key)) throw new IllegalArgumentException("duplicate registered query coordinate");
            result.add(point);
        }
        return result;
    }

    private static double[][] coordinateMatrix(List<List<Double>> points) {
        double[][] result = new double[3][points.size()];
        for (int p = 0; p < points.size(); p++)
            for (int a = 0; a < 3; a++) result[a][p] = points.get(p).get(a);
        return result;
    }

    private static boolean sameSet(int[] a, int[] b) {
        if (a == null || b == null || a.length != b.length) return false;
        int[] x = Arrays.copyOf(a, a.length), y = Arrays.copyOf(b, b.length);
        Arrays.sort(x); Arrays.sort(y);
        return Arrays.equals(x, y);
    }

    private static int[] integers(Object raw, String label) {
        if (!(raw instanceof List) || ((List<?>) raw).isEmpty())
            throw new IllegalArgumentException(label + " must be nonempty");
        int[] values = new int[((List<?>) raw).size()];
        for (int i = 0; i < values.length; i++) values[i] = integer(((List<?>) raw).get(i), 1, label);
        return values;
    }

    private static int integer(Object raw, int min, String label) {
        if (!(raw instanceof Number) || raw instanceof Boolean || ((Number) raw).intValue() < min
                || ((Number) raw).doubleValue() != ((Number) raw).intValue())
            throw new IllegalArgumentException(label + " must be an integer >= " + min);
        return ((Number) raw).intValue();
    }

    private static double numeric(Object raw, String label) {
        if (!(raw instanceof Number) || raw instanceof Boolean || !Double.isFinite(((Number) raw).doubleValue()))
            throw new IllegalArgumentException(label + " must be finite numeric data");
        return ((Number) raw).doubleValue();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object raw, String label) {
        if (!(raw instanceof Map)) throw new IllegalArgumentException(label + " must be an object");
        return (Map<String, Object>) raw;
    }

    private static String[] stringArray(Object raw, int count, String label) {
        if (!(raw instanceof List) || ((List<?>) raw).size() != count)
            throw new IllegalArgumentException(label + " must contain exactly " + count + " entries");
        String[] result = new String[count];
        for (int i = 0; i < count; i++) result[i] = string(((List<?>) raw).get(i), label);
        return result;
    }

    private static String tag(Object raw, String label) {
        String value = string(raw, label);
        if (!value.matches("[A-Za-z][A-Za-z0-9_]{0,62}"))
            throw new IllegalArgumentException(label + " is not a native COMSOL tag");
        return value;
    }

    private static String string(Object raw, String label) {
        if (!(raw instanceof String) || ((String) raw).trim().isEmpty())
            throw new IllegalArgumentException(label + " must be a nonempty string");
        return (String) raw;
    }
}
