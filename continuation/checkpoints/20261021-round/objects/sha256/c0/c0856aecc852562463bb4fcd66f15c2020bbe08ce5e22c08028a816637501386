import com.comsol.model.Model;
import com.comsol.model.GeomSequence;
import java.util.LinkedHashMap;
import java.util.Map;

/** Fixed task-owned unsolved model; no save, file I/O, ModelUtil or other model. */
public final class T050CostModelFixture {
    private T050CostModelFixture() {}
    public static Object run(Model model, Map<String,Object> arguments) {
        if (model == null || arguments == null || !arguments.isEmpty())
            throw new IllegalArgumentException("sole injected Model and empty arguments required");
        GeomSequence geometry = model.component("comp1").geom("geom1");
        for (int i=0; i<4096; i++) {
            String name=String.format(java.util.Locale.ROOT,"t050_p%04d", i);
            model.param().set(name, Integer.toString(i+1)+"[mm]", "T050 baseline "+i);
        }
        for (int i=0; i<128; i++) {
            String tag=String.format(java.util.Locale.ROOT,"t050_r%03d", i);
            geometry.create(tag, "Rectangle");
            geometry.feature(tag).set("size", new double[]{0.001,0.001});
            geometry.feature(tag).set("pos", new double[]{(i%16)*0.003,(i/16)*0.003});
        }
        geometry.run();
        int parameters=0, rectangles=0;
        for (String name: model.param().varnames()) if (name.startsWith("t050_p")) parameters++;
        for (String tag: geometry.feature().tags()) if (tag.startsWith("t050_r")) rectangles++;
        Map<String,Object> out=new LinkedHashMap<>();
        out.put("model_tag",model.tag());out.put("parameters",parameters);out.put("rectangles",rectangles);
        out.put("dimension",2);out.put("semantics","TASK_OWNED_UNSOLVED_NO_SAVE");
        return out;
    }
}
