import com.comsol.model.Model;
import com.comsol.model.GeomSequence;
import java.util.LinkedHashMap;
import java.util.Map;

/** Fixed T044 owned-model builder. Solving occurs only through public study.run. */
public final class T044ThermalFixture {
    private T044ThermalFixture() {}
    public static Object run(Model model, Map<String,Object> arguments) {
        if (model == null || arguments == null || arguments.size()!=1
                || !arguments.containsKey("elements"))
            throw new IllegalArgumentException("sole owned Model and elements required");
        Object value=arguments.get("elements");
        if (!(value instanceof Number) || (((Number)value).doubleValue()!=20.0
                && ((Number)value).doubleValue()!=40.0))
            throw new IllegalArgumentException("fixed coarse20 or fine40 elements required");
        int count=((Number)value).intValue();
        if (model.study().tags().length!=0 || model.sol().tags().length!=0)
            throw new IllegalArgumentException("fresh unsolved model required");
        String[] components=model.component().tags();
        if (components.length!=1 || !components[0].equals("comp1"))
            throw new IllegalArgumentException("fresh comp1 owned model required");
        model.component().remove("comp1");
        model.component().create("comp1",true);
        model.component("comp1").geom().create("geom1",1);
        model.component("comp1").mesh().create("mesh1");
        model.param().set("L","0.01[m]","T044 length");
        model.param().set("k","10[W/(m*K)]","T044 conductivity");
        model.param().set("Tleft","300[K]","T044 left temperature");
        model.param().set("Tright","400[K]","T044 right temperature");
        GeomSequence geometry=model.component("comp1").geom("geom1");
        geometry.lengthUnit("m");
        geometry.create("i1","Interval");
        geometry.feature("i1").setIndex("coord","0",0);
        geometry.feature("i1").setIndex("coord","L",1);
        geometry.run();
        model.component("comp1").physics().create("ht","HeatTransfer","geom1");
        model.component("comp1").material().create("mat1","Common");
        model.component("comp1").material("mat1").propertyGroup("def").set("density","7850[kg/m^3]");
        model.component("comp1").material("mat1").propertyGroup("def").set("heatcapacity","475[J/(kg*K)]");
        model.component("comp1").material("mat1").propertyGroup("def").set("thermalconductivity",new String[]{"k"});
        model.component("comp1").physics("ht").create("left","TemperatureBoundary",0);
        model.component("comp1").physics("ht").feature("left").selection().set(1);
        model.component("comp1").physics("ht").feature("left").set("T0","Tleft");
        model.component("comp1").physics("ht").create("right","TemperatureBoundary",0);
        model.component("comp1").physics("ht").feature("right").selection().set(2);
        model.component("comp1").physics("ht").feature("right").set("T0","Tright");
        model.component("comp1").mesh("mesh1").create("edg1","Edge");
        model.component("comp1").mesh("mesh1").feature("edg1").create("dis1","Distribution");
        model.component("comp1").mesh("mesh1").feature("edg1").feature("dis1").set("numelem",count);
        model.component("comp1").mesh("mesh1").run();
        model.study().create("std1");
        model.study("std1").create("stat","Stationary");
        model.study("std1").feature("stat").setSolveFor("/physics/ht",true);
        Map<String,Object> out=new LinkedHashMap<>();
        out.put("model_tag",model.tag());
        out.put("dimension",model.component("comp1").mesh("mesh1").getSDim());
        out.put("elements",model.component("comp1").mesh("mesh1").getNumElem("edg"));
        out.put("all_element_count",model.component("comp1").mesh("mesh1").getNumElem());
        out.put("mesh_types",model.component("comp1").mesh("mesh1").getTypes());
        out.put("vertices",model.component("comp1").mesh("mesh1").getVertex());
        out.put("L",model.param().evaluate("L"));out.put("k",model.param().evaluate("k"));
        out.put("Tleft",model.param().evaluate("Tleft"));out.put("Tright",model.param().evaluate("Tright"));
        out.put("physics_features",model.component("comp1").physics("ht").feature().tags());
        Map<String,Object> physicsTypes=new LinkedHashMap<>();
        for (String tag:model.component("comp1").physics("ht").feature().tags())
            physicsTypes.put(tag,model.component("comp1").physics("ht").feature(tag).getType());
        out.put("physics_feature_types",physicsTypes);
        out.put("geometry_length_unit",geometry.lengthUnit());
        out.put("conductivity",model.component("comp1").material("mat1").propertyGroup("def").getStringArray("thermalconductivity"));
        out.put("left_type",model.component("comp1").physics("ht").feature("left").getType());
        out.put("right_type",model.component("comp1").physics("ht").feature("right").getType());
        out.put("left_selection",model.component("comp1").physics("ht").feature("left").selection().entities(0));
        out.put("right_selection",model.component("comp1").physics("ht").feature("right").selection().entities(0));
        out.put("left_T0",model.component("comp1").physics("ht").feature("left").getString("T0"));
        out.put("right_T0",model.component("comp1").physics("ht").feature("right").getString("T0"));
        out.put("solution_tags",model.sol().tags());
        out.put("semantics","T044_UNSOLVED_FIXED_1D_NO_SOURCE_NO_SAVE");
        return out;
    }
}
