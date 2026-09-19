import com.comsol.model.Model;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Fixed W10 acceptance source.  It uses the public Model API directly and
 * deliberately has no domain wrapper in the MCP server.
 */
public final class Phase3NoWrapper {
    // effect: WRITE
    private Phase3NoWrapper() {}

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null) {
            throw new IllegalArgumentException("injected Model is required");
        }
        String before = model.comments();
        String marker = "phase3-public-api-probe";
        if (arguments != null && arguments.get("marker") instanceof String
                && !((String) arguments.get("marker")).isEmpty()) {
            marker = (String) arguments.get("marker");
        }
        model.comments(marker);

        List<String> parameterNames = new ArrayList<>();
        List<String> parameterValues = new ArrayList<>();
        for (int index = 0; index < 3; index++) {
            String name = "phase3_api_probe_" + index;
            model.param().set(name, Integer.toString(index + 1));
            parameterNames.add(name);
            parameterValues.add(model.param().get(name));
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("model_tag", model.tag());
        result.put("before_comments", before);
        result.put("after_comments", model.comments());
        result.put("marker", marker);
        result.put("parameter_names", parameterNames);
        result.put("parameter_values", parameterValues);
        result.put("parameter_count", parameterNames.size());
        return result;
    }
}
