import com.comsol.model.Model;
import java.util.Map;

/** Fixed W10 acceptance source for a checkpointed partial execution. */
public final class Phase3PartialFailure {
    // effect: WRITE
    private Phase3PartialFailure() {}

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null) {
            throw new IllegalArgumentException("injected Model is required");
        }
        String name = "phase3_code_guard";
        if (arguments != null && arguments.get("parameter") instanceof String) {
            name = (String) arguments.get("parameter");
        }
        model.param().set(name, "1");
        throw new IllegalStateException("phase3 deliberate partial failure after parameter write");
    }
}
