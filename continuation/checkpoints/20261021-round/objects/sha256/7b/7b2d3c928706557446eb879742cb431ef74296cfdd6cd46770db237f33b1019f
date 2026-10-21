import com.comsol.model.Model;
import java.util.LinkedHashMap;
import java.util.Map;

/** Task-owned read-only getter on the single Worker-injected Model. */
public final class P0ParameterReadback {
    // effect: READ
    private P0ParameterReadback() {}

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null || arguments == null || !arguments.isEmpty()) {
            throw new IllegalArgumentException("one injected Model and empty arguments required");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("name", "v3_marker");
        result.put("expression", model.param().get("v3_marker"));
        result.put("value", model.param().evaluate("v3_marker"));
        result.put("unit", model.param().evaluateUnit("v3_marker"));
        return result;
    }
}
