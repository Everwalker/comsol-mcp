import com.comsol.model.*;
import java.util.*;
public final class W20RefinementFixture {
    public static Object run(Model model, Map<String, Object> args) {
        model.param().set("L", "1[m]");
        model.param().set("k", "1[W/(m*K)]");
        model.param().set("rhoCp", "1[J/(m^3*K)]");
        model.modelNode().create("comp1");
        model.geom().create("geom1", 3);
        model.geom("geom1").create("blk1", "Block");
        model.geom("geom1").feature("blk1").set("size", new String[]{"L", "0.1[m]", "0.1[m]"});
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
        model.mesh("mesh1").feature("size").set("hauto", 5);
        model.mesh("mesh1").create("swe1", "Sweep");
        model.mesh("mesh1").feature("swe1").selection("sourceface").set(new int[]{1});
        model.mesh("mesh1").feature("swe1").selection("targetface").set(new int[]{6});
        model.mesh("mesh1").feature("swe1").create("dis1", "Distribution");
        int n=((Number)args.get("n")).intValue();
        double[] positions=new double[n+1];
        for(int i=0;i<=n;i++) positions[i]=(double)i/n;
        model.mesh("mesh1").feature("swe1").feature("dis1").set("explicit", positions);
        model.mesh("mesh1").run();

        model.study().create("std1");
        model.study("std1").create("time", "Transient");
        model.study("std1").feature("time").set("tlist", "0 0.01 0.03 0.1");
        model.study("std1").feature("time").set("usertol", "on");
        model.study("std1").feature("time").set("rtol", String.valueOf(args.get("rtol")));
        return Collections.singletonMap("status", "BUILT_NOT_SOLVED");
    }
}