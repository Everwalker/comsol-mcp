import com.comsol.model.Model;
import java.util.Map;

/** Fixed W10 acceptance source whose syntax error must remain compile-only. */
public final class Phase3SyntaxFailure {
    // effect: READ
    private Phase3SyntaxFailure() {}

    public static Object run(Model model, Map<String, Object> arguments) {
        return model.label(;
    }
}
