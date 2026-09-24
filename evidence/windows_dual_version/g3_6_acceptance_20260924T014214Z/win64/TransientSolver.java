
import com.comsol.model.*;
import java.util.*;

public final class TransientSolver {
    public static Object run(Model model, Map<String, Object> args) {
        model.study().create("std2");
        model.study("std2").create("time", "Transient");
        model.study("std2").feature("time").set("tlist", "range(0, 0.5, 1.0)");
        model.study("std2").run();
        return Collections.singletonMap("status", "SOLVED");
    }
}
