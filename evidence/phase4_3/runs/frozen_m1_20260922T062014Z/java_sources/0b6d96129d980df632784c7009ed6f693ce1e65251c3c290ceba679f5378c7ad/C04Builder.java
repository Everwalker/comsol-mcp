
import com.comsol.model.*;
import java.util.*;

public final class C04Builder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1");
        model.geom().create("geom1", 3);
        model.geom("geom1").create("blk1", "Block");
        model.geom("geom1").feature("blk1").set("size", new String[]{"1.0", "1.0", "3.0"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");
        
        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").autoMeshSize(4);
        model.mesh("mesh1").run();
        
        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();
        
        return Collections.singletonMap("status", "SOLVED");
    }
}
