import com.comsol.model.*;
import java.util.*;
public final class W21Fixture {
    public static Object run(Model model, Map<String, Object> args) {
        model.param().set("L", "0.05[m]");
        model.param().set("k", "400[W/(m*K)]");
        model.param().set("rhoCp", "3.8e6[J/(m^3*K)]");
        model.modelNode().create("comp1");
        model.geom().create("geom1", 3);
        model.geom("geom1").create("blk1", "Block");
        model.geom("geom1").feature("blk1").set("size", new String[]{"L", "L/5", "L/5"});
        model.geom("geom1").run();

        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 2);
        model.physics("ht").feature("temp1").selection().set(new int[]{1});
        model.physics("ht").feature("temp1").set("T0", "300[K]");

        model.physics("ht").create("temp2", "TemperatureBoundary", 2);
        model.physics("ht").feature("temp2").selection().set(new int[]{6});
        model.physics("ht").feature("temp2").set("T0", "300[K]");

        model.physics("ht").feature("init1").set("T", "300[K] + 10[K]*sin(pi*x/L)");

        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"k"});
        model.material("mat1").propertyGroup("def").set("density", "1.0[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "rhoCp/(1[kg/m^3])");

        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").feature("size").set("hauto", 2);
        model.mesh("mesh1").run();

        model.study().create("std1");
        model.study("std1").create("time", "Transient");
        model.study("std1").feature("time").set("tlist", "0 0.5 1 1.5 2");
        model.study("std1").feature("time").set("usertol", "on");
        model.study("std1").feature("time").set("rtol", "1e-6");
        model.study().create("std2");
        model.study("std2").create("time", "Transient");
        model.study("std2").feature("time").set("tlist", "2 2.5 3 3.5 4");
        model.study("std2").feature("time").set("usertol", "on");
        model.study("std2").feature("time").set("rtol", "1e-6");

        return Collections.singletonMap("status", "BUILT_NOT_SOLVED");
    }
}