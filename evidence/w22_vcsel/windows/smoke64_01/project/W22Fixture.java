import com.comsol.model.*;
import java.util.*;
public final class W22Fixture {
    static void box(Model m,String tag,double z) {
        m.selection().create(tag,"Box");
        m.selection(tag).set("entitydim",2);
        m.selection(tag).set("zmin",z-1e-8);
        m.selection(tag).set("zmax",z+1e-8);
        m.selection(tag).set("condition","inside");
    }
    static void coupling(Model m,String tag,String type,String selection,int dim) {
        m.component("comp1").cpl().create(tag,type);
        m.component("comp1").cpl(tag).selection().geom("geom1",dim);
        if(selection==null) m.component("comp1").cpl(tag).selection().all();
        else m.component("comp1").cpl(tag).selection().named(selection);
    }
    @SuppressWarnings("unchecked")
    public static Object run(Model model, Map<String,Object> args) {
        com.comsol.model.util.ModelUtil.showProgress(false);
        List<Map<String,Object>> sources=(List<Map<String,Object>>)args.get("sources");
        String sourceRoot=(String)args.get("source_root");
        boolean reload=Boolean.TRUE.equals(args.get("reload"));
        if(reload) {
            for(Map<String,Object> src:sources) {
                String fn=(String)src.get("function");
                model.func(fn).set("filename",sourceRoot+"/"+src.get("file"));
                model.func(fn).discardData(); model.func(fn).importData();
            }
            return Collections.singletonMap("status","INPUTS_RELOADED");
        }
        model.param().set("L","0.02[m]");
        model.param().set("p0","0.8[W]"); model.param().set("p1","0.75[W]");
        model.param().set("ptotal","12.45[W]");
        model.param().set("p2","(ptotal-p0-6*p1)/11");
        model.param().set("alpha","0.6");
        model.param().set("Tamb","300[K]");
        model.modelNode().create("comp1"); model.geom().create("geom1",3);
        model.geom("geom1").create("blk1","Block");
        model.geom("geom1").feature("blk1").set("size",new double[]{.04,.04,.001});
        model.geom("geom1").feature("blk1").set("pos",new double[]{-.02,-.02,0});
        model.geom("geom1").create("cyl1","Cylinder");
        model.geom("geom1").feature("cyl1").set("r",.015);
        model.geom("geom1").feature("cyl1").set("h",.001);
        model.geom("geom1").run();
        box(model,"top",.001); box(model,"bottom",0);
        model.selection().create("roi_box","Box");
        model.selection("roi_box").set("entitydim",2);
        model.selection("roi_box").set("xmin",-.01500001);model.selection("roi_box").set("xmax",.01500001);
        model.selection("roi_box").set("ymin",-.01500001);model.selection("roi_box").set("ymax",.01500001);
        model.selection("roi_box").set("zmin",.00099999);model.selection("roi_box").set("zmax",.00100001);
        model.selection("roi_box").set("condition","inside");
        StringBuilder expr=new StringBuilder();
        for(Map<String,Object> src:sources) {
            String fn=(String)src.get("function");
            model.func().create(fn,"Interpolation");
            model.func(fn).set("source","file"); model.func(fn).set("filename",sourceRoot+"/"+src.get("file"));
            model.func(fn).set("struct","spreadsheet"); model.func(fn).set("nargs",2);
            model.func(fn).set("funcs",new String[][]{{fn,"1"}});
            model.func(fn).set("argunit",new String[]{"m","m"});
            model.func(fn).set("fununit",new String[]{"1/m^2"});
            model.func(fn).set("interp","linear");model.func(fn).set("extrap","value");model.func(fn).set("extrapvalue",0);
            model.func(fn).importData();
            if(expr.length()>0)expr.append("+");
            expr.append("if(abs(L-").append(src.get("L_m")).append("[m])<1e-9[m],p").append(src.get("group_index")).append("*").append(fn).append("(x,y),0[W/m^2])");
        }
        model.variable().create("v1");
        model.variable("v1").set("incident",expr.toString());model.variable("v1").set("qabs","alpha*incident");
        model.physics().create("ht","HeatTransfer","geom1");
        model.physics("ht").feature("init1").set("T","Tamb");
        model.physics("ht").create("heat","HeatFluxBoundary",2);
        model.physics("ht").feature("heat").selection().named("top");
        model.physics("ht").feature("heat").set("HeatFluxType","GeneralInwardHeatFlux");
        model.physics("ht").feature("heat").set("q0","qabs");
        model.physics("ht").create("cool","HeatFluxBoundary",2);
        model.physics("ht").feature("cool").selection().named("bottom");
        model.physics("ht").feature("cool").set("HeatFluxType","GeneralInwardHeatFlux");
        model.physics("ht").feature("cool").set("q0","500[W/(m^2*K)]*(Tamb-T)");
        model.material().create("mat1","Common","comp1");model.material("mat1").selection().all();
        model.material("mat1").propertyGroup("def").set("thermalconductivity",new String[]{"20[W/(m*K)]"});
        model.material("mat1").propertyGroup("def").set("density","3000[kg/m^3]");
        model.material("mat1").propertyGroup("def").set("heatcapacity","700[J/(kg*K)]");
        coupling(model,"iroi","Integration","roi_box",2);coupling(model,"itop","Integration","top",2);
        coupling(model,"ibot","Integration","bottom",2);coupling(model,"ivol","Integration",null,3);
        coupling(model,"mxroi","Maximum","roi_box",2);coupling(model,"mnroi","Minimum","roi_box",2);
        model.variable("v1").set("roi_area","iroi(1)");
        model.variable("v1").set("roi_mean","iroi(T-Tamb)/iroi(1)");
        model.variable("v1").set("roi_std","sqrt(iroi((T-Tamb-roi_mean)^2)/iroi(1))");
        model.variable("v1").set("roi_cv","roi_std/max(roi_mean,1e-12[K])");
        model.variable("v1").set("roi_max","mxroi(T-Tamb)");model.variable("v1").set("roi_min","mnroi(T-Tamb)");
        model.variable("v1").set("Pinc","itop(incident)");model.variable("v1").set("Pabs","itop(qabs)");
        model.variable("v1").set("Proi","iroi(qabs)");model.variable("v1").set("Pout","ibot(500[W/(m^2*K)]*(T-Tamb))");
        model.variable("v1").set("Pstore","ivol(3000[kg/m^3]*700[J/(kg*K)]*d(T,t))");
        model.variable("v1").set("U","ivol(3000[kg/m^3]*700[J/(kg*K)]*(T-Tamb))");
        model.mesh().create("mesh1","geom1");model.mesh("mesh1").feature("size").set("custom","on");
        model.mesh("mesh1").feature("size").set("hmax",.00075);model.mesh("mesh1").feature("size").set("hmin",.00015);model.mesh("mesh1").run();
        model.study().create("std1");model.study("std1").create("time","Transient");
        model.study("std1").feature("time").set("tlist","0 1 5 20 60");
        model.study("std1").feature("time").set("usertol","on");model.study("std1").feature("time").set("rtol","1e-6");
        Map<String,Object> result=new LinkedHashMap<>();result.put("status","BUILT_NOT_SOLVED");
        result.put("top",model.selection("top").entities());result.put("bottom",model.selection("bottom").entities());result.put("roi",model.selection("roi_box").entities());
        result.put("incident_expression",expr.toString());return result;
    }
}
