
import com.comsol.model.*;
import java.util.*;

public final class ParamModifier {
    public static Object run(Model model, Map<String, Object> args) {
        model.param().set("T_right", "380[K]");
        model.study("std1").run();
        return Collections.singletonMap("status", "SOLVED");
    }
}
