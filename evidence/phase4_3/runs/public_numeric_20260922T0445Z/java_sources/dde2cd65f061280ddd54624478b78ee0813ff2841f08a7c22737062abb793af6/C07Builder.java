
import com.comsol.model.*;
import java.util.*;

public final class C07Builder {
    public static Object run(Model model, Map<String, Object> args) {
        model.modelNode().create("comp1", false);
        model.geom().create("geom1", 2);
        model.geom("geom1").axisymmetric(true);
        model.geom("geom1").create("r1", "Rectangle");
        model.geom("geom1").feature("r1").set("size", new String[]{"2.0", "3.0"});
        model.geom("geom1").feature("r1").set("pos", new String[]{"0.0", "0.0"});
        model.geom("geom1").run();
        
        model.physics().create("ht", "HeatTransfer", "geom1");
        model.physics("ht").create("temp1", "TemperatureBoundary", 1);
        model.physics("ht").feature("temp1").selection().all();
        model.physics("ht").feature("temp1").set("T0", "300.0[K]");
        
        model.material().create("mat1", "Common", "comp1");
        model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"400[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density", "8960[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity", "385[J/(kg*K)]");
        
        model.mesh().create("mesh1", "geom1");
        model.mesh("mesh1").autoMeshSize(3);
        model.mesh("mesh1").run();
        
        model.study().create("std1");
        model.study("std1").create("stat", "Stationary");
        model.study("std1").run();
        
        return Collections.singletonMap("status", "SOLVED");
    }
}
