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
                model.result(tags[i]).feature("surf1").selection().named("top");
                model.result(tags[i]).run();
            }
        }
        result.put("dataset","dset1"); result.put("solution","sol1");result.put("time_s",60);
        result.put("parameters",new String[]{model.param().get("L"),model.param().get("p0"),model.param().get("p1"),model.param().get("p2")});
        return result;
    }
}
