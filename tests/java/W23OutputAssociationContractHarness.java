import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Invokes the production binding helper without constructing a COMSOL model. */
public final class W23OutputAssociationContractHarness {
    private static final String STUDY = "std3dFullBmaFrequencyProducerV1";
    private static final String SEQUENCE = "solW23FullBmaFreqV1";

    private W23OutputAssociationContractHarness() {}

    public static void main(String[] args) {
        List<Map<String, Object>> tree = new ArrayList<>();
        for (int index = 1; index <= 3; index++)
            tree.add(row("path", SEQUENCE + "/st" + index, "feature_type", "StudyStep"));

        List<Map<String, Object>> bindings = new ArrayList<>();
        String[] steps = {"producerBmaInput3d", "producerBmaOutput3d", "producerFreq3d"};
        for (int index = 0; index < steps.length; index++) {
            bindings.add(row("study", STUDY, "studystep", steps[index],
                    "feature_type", "StudyStep", "path", SEQUENCE + "/st" + (index + 1)));
        }

        Map<String, Object> sequence = new LinkedHashMap<>();
        sequence.put("tag", SEQUENCE);
        sequence.put("feature_type", "SolverSequence");
        sequence.put("parent_study", STUDY);
        sequence.put("solver_tree_features", tree);
        sequence.put("study_step_bindings_in_solver_tree_order", bindings);
        sequence.put("terminal_frequency_binding", bindings.get(2));
        sequence.put("output_association_status",
                "UNVERIFIED_UNTIL_EXACT_NEW_SOLUTIONINFO_AND_DATASET_READBACK");
        sequence.put("output_path_readback", "DIRECT_SEQUENCE_RESULT_CANDIDATE_NO_POST_FREQUENCY_STORE_SOLUTION");
        sequence.put("store_solution_feature_paths", new ArrayList<>());
        sequence.put("store_solution_paths_after_frequency", new ArrayList<>());

        Map<String, Object> runReadback = new LinkedHashMap<>();
        NativeW23Full3DFixture.bindFullBmaFrequencyOutputAssociation(runReadback, sequence);
        System.out.println(toJson(runReadback.get("output_association")));
    }

    private static Map<String, Object> row(Object... values) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index < values.length; index += 2)
            result.put((String) values[index], values[index + 1]);
        return result;
    }

    private static String toJson(Object value) {
        if (value == null) return "null";
        if (value instanceof String) return quote((String) value);
        if (value instanceof Number || value instanceof Boolean) return value.toString();
        if (value instanceof Map) {
            StringBuilder out = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!(entry.getKey() instanceof String))
                    throw new IllegalArgumentException("JSON object keys must be strings");
                if (!first) out.append(',');
                first = false;
                out.append(quote((String) entry.getKey())).append(':').append(toJson(entry.getValue()));
            }
            return out.append('}').toString();
        }
        if (value instanceof Iterable) {
            StringBuilder out = new StringBuilder("[");
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) out.append(',');
                first = false;
                out.append(toJson(item));
            }
            return out.append(']').toString();
        }
        throw new IllegalArgumentException("unsupported JSON fixture value: " + value.getClass());
    }

    private static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char current = value.charAt(index);
            switch (current) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (current < 0x20) out.append(String.format("\\u%04x", (int) current));
                    else out.append(current);
            }
        }
        return out.append('"').toString();
    }
}
