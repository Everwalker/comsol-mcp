
import com.comsol.model.*;
import java.util.*;
public class FastSolve {
    public static Object run(Model model, Map<String, Object> args) {
        model.param().set("T_left", "300[K]");
        model.param().set("T_right", "350[K]");
        model.param().set("k_val", "400[W/(m*K)]");

        model.modelNode().create("comp1");
        model.geom().create("geom1", 3);
        model.geom("geom1").create("blk1", "Block");
        model.geom("geom1").feature("blk1").set("size", new String[]{"0.05[m]", "0.02[m]", "0.01[m]"});
        model.geom("geom1").run();

        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 2);
        model.physics("ht").feature("temp1").selection().set(new int[]{1});
        model.physics("ht").feature("temp1").set("T0", "T_left");

        model.physics("ht").create("temp2", "TemperatureBoundary", 2);
        model.physics("ht").feature("temp2").selection().set(new int[]{6});
        model.physics("ht").feature("temp2").set("T0", "T_right");

        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"k_val"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");

        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").run();

        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();

        model.result().create("pg3d", 3);
        model.result("pg3d").set("data", "dset1");
        model.result("pg3d").create("surf1", "Surface");
        model.result("pg3d").feature("surf1").set("expr", "T");

        return Collections.singletonMap("status", "SOLVED");
    }
}
