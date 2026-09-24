
import com.comsol.model.*;
import java.util.*;

public final class TransientDiffusionBuilder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 3);
        model.geom("geom1").create("blk1", "Block");
        model.geom("geom1").feature("blk1").set("size", new String[]{"1.0[m]", "0.1[m]", "0.1[m]"});
        model.geom("geom1").run();

        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 2);
        model.physics("ht").feature("temp1").selection().set(new int[]{1});
        model.physics("ht").feature("temp1").set("T0", "300[K]");

        model.physics("ht").create("temp2", "TemperatureBoundary", 2);
        model.physics("ht").feature("temp2").selection().set(new int[]{6});
        model.physics("ht").feature("temp2").set("T0", "300[K]");

        model.physics("ht").feature("init1").set("T", "300[K] + 10[K]*sin(pi*x/(1.0[m]))");

        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"1.0[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "1.0[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "1.0[J/(kg*K)]");

        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").feature("size").set("hauto", 3);
        model.mesh("mesh1").run();

        model.study().create("std1");
        model.study("std1").create("time", "Transient");
        model.study("std1").feature("time").set("tlist", "0 0.01 0.03 0.1");
        model.study("std1").run();

        return Collections.singletonMap("status", "SOLVED");
    }
}
