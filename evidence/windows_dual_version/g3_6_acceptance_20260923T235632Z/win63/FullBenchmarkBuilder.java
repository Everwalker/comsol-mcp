
import com.comsol.model.*;
import java.util.*;

public final class FullBenchmarkBuilder {
    public static Object run(Model model, Map<String, Object> args) {
        // WD07: Parametric model setup with units
        model.param().set("L", "0.05[m]");
        model.param().set("T_left", "300[K]");
        model.param().set("T_right", "350[K]");
        model.param().set("k_val", "400[W/(m*K)]");

        // WD08: Component, 3D Block geometry, WorkPlane with 2D shape, and Selection
        model.modelNode().create("comp1");
        model.geom().create("geom1", 3);
        model.geom("geom1").create("blk1", "Block");
        model.geom("geom1").feature("blk1").set("size", new String[]{"L", "0.02[m]", "0.01[m]"});
        model.geom("geom1").run();

        model.geom().create("geom2", 3);
        model.geom("geom2").create("wp1", "WorkPlane");
        model.geom("geom2").feature("wp1").set("quickplane", "xy");
        GeomSequence local = model.geom("geom2").feature("wp1").geom();
        local.create("rectA", "Rectangle");
        local.feature("rectA").set("size", new String[]{"0.01[m]", "0.01[m]"});
        model.geom("geom2").run();

        model.selection().create("sel1", "Explicit");
        model.selection("sel1").geom("geom1", 2);
        model.selection("sel1").set(new int[]{1, 6});

        // WD09: Heat transfer physics, boundary conditions, material and mesh
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

        // WD10: Stationary Study
        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();

        // WD13: Result plot groups
        model.result().create("pg3d", 3);
        model.result("pg3d").set("data", "dset1");
        model.result("pg3d").create("surf1", "Surface");
        model.result("pg3d").feature("surf1").set("expr", "T");

        model.result().create("pg1d", 1);
        model.result("pg1d").set("data", "dset1");
        model.result("pg1d").create("ptg1", "PointGraph");
        model.result("pg1d").feature("ptg1").set("expr", "T");

        return Collections.singletonMap("status", "SOLVED");
    }
}
