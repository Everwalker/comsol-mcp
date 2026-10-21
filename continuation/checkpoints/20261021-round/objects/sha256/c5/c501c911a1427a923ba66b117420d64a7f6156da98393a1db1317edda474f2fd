import com.comsol.model.Model;
import java.util.LinkedHashMap;
import java.util.Map;

/** Fixed W10 readback source; it only observes the injected Model. */
public final class Phase3Readback {
    // effect: READ
    private Phase3Readback() {}

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null) {
            throw new IllegalArgumentException("injected Model is required");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("model_tag", model.tag());
        result.put("comments", model.comments());
        Map<String, String> parameters = new LinkedHashMap<>();
        for (int index = 0; index < 3; index++) {
            String name = "phase3_api_probe_" + index;
            parameters.put(name, model.param().get(name));
        }
        result.put("parameters", parameters);
        boolean guardPresent = false;
        for (String name : model.param().varnames()) {
            if ("phase3_code_guard".equals(name)) {
                guardPresent = true;
                break;
            }
        }
        String guard = guardPresent ? model.param().get("phase3_code_guard") : null;
        result.put("phase3_code_guard", guard);
        result.put("phase3_code_guard_present", guardPresent);
        return result;
    }
}
