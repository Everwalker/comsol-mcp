// effect: TRUSTED_CODE
import com.comsol.model.Model;
import com.comsol.model.MeshFeature;
import com.comsol.model.MeshSequence;
import com.comsol.model.ParameterEntity;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

/** Target-injected loaded model only. Remesh; never study, solve, load or ModelUtil. */
public final class T044MeshAdapter {
    private static String required(Map<String,Object> args, String name) {
        Object value=args.get(name);
        if (!(value instanceof String) || ((String)value).isEmpty())
            throw new IllegalArgumentException("missing explicit task tag: "+name);
        return (String)value;
    }
    private static boolean isMatrix(String type) {
        String t=type==null?"":type.toLowerCase();
        return t.contains("matrix") || t.endsWith("[][]");
    }
    private static boolean isArray(String type) {
        String t=type==null?"":type.toLowerCase();
        return !isMatrix(type) && (t.contains("array") || t.endsWith("[]"));
    }
    private static String readStringProperty(ParameterEntity feature, String name) {
        if (!feature.hasProperty(name)) throw new IllegalStateException("missing property "+name);
        String type=feature.getValueType(name);
        if (isMatrix(type)) {
            String[][] v=feature.getStringMatrix(name);
            if (v==null || v.length==0 || v[0]==null || v[0].length==0) throw new IllegalStateException("empty property "+name);
            return v[0][0];
        }
        if (isArray(type)) {
            String[] v=feature.getStringArray(name);
            if (v==null || v.length==0) throw new IllegalStateException("empty property "+name);
            return v[0];
        }
        return feature.getString(name);
    }
    public static Object run(Model model, Map<String,Object> args) {
        if (model==null || args==null) throw new IllegalArgumentException("injected model required");
        String component=required(args,"component_tag"), geometry=required(args,"geometry_tag");
        String meshTag=required(args,"mesh_tag"), physics=required(args,"physics_tag");
        String distTag=required(args,"distribution_tag");
        Object raw=args.get("elements");
        if (!(raw instanceof Number)) throw new IllegalArgumentException("finite requested element count required");
        int elements=((Number)raw).intValue();
        if (((Number)raw).doubleValue()!=elements || (elements!=20 && elements!=40 && elements!=80))
            throw new IllegalArgumentException("only frozen20/40/80 task levels");
        boolean allowExisting=Boolean.TRUE.equals(args.get("existing_distribution_expected"));
        String length=required(args,"length_parameter"), conductivity=required(args,"conductivity_parameter");
        String left=required(args,"left_temperature_parameter"), right=required(args,"right_temperature_parameter");
        double L=model.param().evaluate(length), k=model.param().evaluate(conductivity);
        double tl=model.param().evaluate(left), tr=model.param().evaluate(right);
        String c=readStringProperty(model.component(component).physics(physics).feature("cfeq1"),"c");
        String f=readStringProperty(model.component(component).physics(physics).feature("cfeq1"),"f");
        String dependent=readStringProperty(model.component(component).physics(physics).prop("Units"),"DependentVariableQuantity");
        String sourceUnit=readStringProperty(model.component(component).physics(physics).prop("Units"),"CustomSourceTermUnit");
        if (L!=0.01 || k!=10.0 || tl!=300.0 || tr!=400.0 || !conductivity.equals(c)
                || !"0[W/m^3]".equals(f) || !"temperature".equals(dependent) || !"W/m^3".equals(sourceUnit)
                || model.component(component).geom(geometry).getSDim()!=1
                || model.component(component).geom(geometry).getNDomains()!=1
                || !"m".equals(model.component(component).geom(geometry).lengthUnit()))
            throw new IllegalStateException("loaded physical/source/unit benchmark differs; no mesh mutation");
        MeshSequence mesh=model.component(component).mesh(meshTag);
        String[] rootTags=mesh.feature().tags();
        MeshFeature edge=null;
        String edgeTag=null;
        for (String tag:rootTags) if ("Edge".equals(mesh.feature(tag).getType())) {
            if (edge!=null) throw new IllegalStateException("ambiguous Edge feature");
            edge=mesh.feature(tag);edgeTag=tag;
        }
        if (edge==null) throw new IllegalStateException("known loaded one-dimensional Edge feature unavailable");
        String[] childTags=edge.feature().tags();
        boolean found=false;
        for (String tag:childTags) {
            String type=edge.feature(tag).getType();
            if ("Distribution".equals(type)) {
                if (!tag.equals(distTag)) throw new IllegalStateException("unrelated Distribution retained; no override");
                found=true;
            }
            if (tag.equals(distTag) && !"Distribution".equals(type)) throw new IllegalStateException("distribution tag type collision");
        }
        if (found!=allowExisting) throw new IllegalStateException("distribution creation/history collision");
        mesh.automatic(false);
        if (!found) edge.create(distTag,"Distribution");
        MeshFeature distribution=edge.feature(distTag);
        distribution.selection().all();
        distribution.set("numelem",elements);
        mesh.run();
        int actual=mesh.getNumElem("edg"), all=mesh.getNumElem(), stat=mesh.stat().getNumElem();
        double[][] vertices=mesh.getVertex();
        if (actual!=elements || all<=0 || stat<=0 || vertices==null || vertices.length!=1
                || vertices[0]==null || vertices[0].length!=actual+1)
            throw new IllegalStateException("actual bounded 1D mesh/count/vertices mismatch");
        double[] x=vertices[0].clone();Arrays.sort(x);
        double min=Double.POSITIVE_INFINITY,max=0;
        for (int i=0;i<x.length;i++) {
            if (!Double.isFinite(x[i])) throw new IllegalStateException("nonfinite native vertex");
            if (i>0) {double h=x[i]-x[i-1];if (!(h>0)) throw new IllegalStateException("duplicate/unordered vertex");min=Math.min(min,h);max=Math.max(max,h);}
        }
        if (Math.abs(x[0])>1e-12 || Math.abs(x[x.length-1]-L)>1e-12) throw new IllegalStateException("native mesh span differs");
        Map<String,Object> out=new LinkedHashMap<String,Object>();
        out.put("model_tag",model.tag());out.put("component_tag",component);out.put("geometry_tag",geometry);
        out.put("mesh_tag",meshTag);out.put("edge_tag",edgeTag);out.put("distribution_tag",distTag);
        out.put("requested_elements",elements);out.put("element_count",actual);out.put("all_element_count",all);
        out.put("stat_element_count",stat);out.put("vertex_count",x.length);out.put("h_min_m",min);out.put("h_max_m",max);
        out.put("length_m",L);out.put("conductivity_W_per_mK",k);out.put("left_K",tl);out.put("right_K",tr);
        out.put("coefficient_c",c);out.put("source_f",f);out.put("dependent_quantity",dependent);out.put("source_unit",sourceUnit);
        out.put("semantics","REMESH_ONLY_NO_STUDY_NO_SAVE_NO_GLOBAL_MODEL_OPERATION");
        return out;
    }
}
