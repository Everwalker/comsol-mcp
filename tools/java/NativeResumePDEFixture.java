import com.comsol.model.Model;
import java.util.Collections;
import java.util.Map;

/** Synthetic, single-study COMSOL 6.4 restart smoke fixture. It builds only; it never solves. */
public final class NativeResumePDEFixture {
    public static Object run(Model model, Map<String, Object> args) {
        model.param().set("smoke_marker", "0.3141592653589793");

        model.component().create("comp1");
        model.component("comp1").geom().create("geom1", 2);
        model.component("comp1").geom("geom1").lengthUnit("m");
        model.component("comp1").geom("geom1").feature().create("rect1", "Rectangle");
        model.component("comp1").geom("geom1").feature("rect1").set("base", "corner");
        model.component("comp1").geom("geom1").feature("rect1").set("pos", new String[]{"0[m]", "0[m]"});
        model.component("comp1").geom("geom1").feature("rect1").set("size", new String[]{"1[m]", "1[m]"});
        model.component("comp1").geom("geom1").run();

        model.component("comp1").physics().create("pde", "CoefficientFormPDE", "geom1");
        model.component("comp1").physics("pde").feature("cfeq1").set("c", "1");
        model.component("comp1").physics("pde").feature("cfeq1").set("a", "0");
        model.component("comp1").physics("pde").feature("cfeq1").set("f", "0");
        model.component("comp1").physics("pde").feature().create("dirSmoke", "DirichletBoundary", 1);
        model.component("comp1").physics("pde").feature("dirSmoke").selection().all();
        model.component("comp1").physics("pde").feature("dirSmoke").set("r", "1");

        model.component("comp1").mesh().create("mesh1", "geom1");
        model.component("comp1").mesh("mesh1").feature("size").set("hauto", 5);
        model.component("comp1").mesh("mesh1").run();

        model.study().create("std1");
        model.study("std1").feature().create("stat1", "Stationary");

        return Collections.singletonMap("fixture_state", "BUILT_WITHOUT_SOLVE");
    }
}
