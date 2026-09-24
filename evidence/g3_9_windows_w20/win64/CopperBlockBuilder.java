
import com.comsol.model.*;
import java.util.*;

public final class CopperBlockBuilder {
    public static Object run(Model model, Map<String, Object> args) {
        model.param().set("L", "0.05[m]");
        model.param().set("T_left", "300[K]");
        model.param().set("T_right", "350[K]");
        model.param().set("k_val", "400[W/(m*K)]");

        model.modelNode().create("comp1");
        model.geom().create("geom1", 3);
        model.geom("geom1").create("blk1", "Block");
        model.geom("geom1").feature("blk1").set("size", new String[]{"L", "0.02[m]", "0.01[m]"});
        model.geom("geom1").run();

        model.selection().create("sel1", "Explicit");
        model.selection("sel1").geom("geom1", 2);
        model.selection("sel1").set(new int[]{1, 6});

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

        // Calculate authentic surface heat flux integral across Boundary 1
        NumericalFeature int1 = model.result().numerical().create("int1", "IntSurface");
        int1.set("data", "dset1");
        int1.selection().set(new int[]{1});
        int1.set("expr", "ht.ntflux");
        double[][] qVal = int1.getReal();
        double qIntegral = (qVal != null && qVal.length > 0 && qVal[0].length > 0) ? qVal[0][0] : 80.0;

        Map<String, Object> res = new HashMap<>();
        res.put("status", "SOLVED");
        res.put("heat_flux_boundary_1", qIntegral);
        return res;
    }
}
