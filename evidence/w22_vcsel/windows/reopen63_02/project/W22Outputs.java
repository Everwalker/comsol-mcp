import com.comsol.model.*;
import java.util.*;
public final class W22Outputs {
    public static Object run(Model model, Map<String,Object> args) {
        Map<String,Object> result=new LinkedHashMap<>();
        for(String tag:new String[]{"top","bottom","roi_box"})result.put(tag,model.selection(tag).entities());
        if(Boolean.TRUE.equals(args.get("plots"))) {
            String[] tags={"sourceplot","temperatureplot"};String[] expressions={"qabs","T"};
            for(int i=0;i<tags.length;i++) {
                model.result().create(tags[i],"PlotGroup3D");
                model.result(tags[i]).set("data","dset1");
                model.result(tags[i]).set("looplevel",new int[]{5});
                model.result(tags[i]).create("surf1","Surface");
                model.result(tags[i]).feature("surf1").set("expr",expressions[i]);
                model.result(tags[i]).feature("surf1").create("sel1","Selection");
                model.result(tags[i]).feature("surf1").feature("sel1").selection().set(model.selection("top").entities());
                model.result(tags[i]).run();
            }
        }
        Map<String,Object> settings=new LinkedHashMap<>();
        settings.put("block_size_m",model.geom("geom1").feature("blk1").getDoubleArray("size"));
        settings.put("block_position_m",model.geom("geom1").feature("blk1").getDoubleArray("pos"));
        settings.put("roi_radius_m",model.geom("geom1").feature("cyl1").getDouble("r"));
        settings.put("thermalconductivity",model.material("mat1").propertyGroup("def").getStringArray("thermalconductivity"));
        settings.put("density",model.material("mat1").propertyGroup("def").getStringArray("density"));
        settings.put("heatcapacity",model.material("mat1").propertyGroup("def").getStringArray("heatcapacity"));
        settings.put("initial_T",model.physics("ht").feature("init1").getString("T"));
        settings.put("top_q0",model.physics("ht").feature("heat").getString("q0"));
        settings.put("bottom_q0",model.physics("ht").feature("cool").getString("q0"));
        settings.put("tlist",model.study("std1").feature("time").getString("tlist"));
        settings.put("roi_integration_selection",model.component("comp1").cpl("iroi").selection().entities());
        settings.put("roi_mean_definition",model.component("comp1").variable("v1").get("roi_mean"));
        settings.put("roi_std_definition",model.component("comp1").variable("v1").get("roi_std"));
        settings.put("solution_tags",model.sol().tags());
        settings.put("dataset_solution",model.result().dataset("dset1").getString("solution"));
        result.put("settings",settings);
        result.put("parameters",new String[]{model.param().get("L"),model.param().get("p0"),model.param().get("p1"),model.param().get("p2")});
        return result;
    }
}
