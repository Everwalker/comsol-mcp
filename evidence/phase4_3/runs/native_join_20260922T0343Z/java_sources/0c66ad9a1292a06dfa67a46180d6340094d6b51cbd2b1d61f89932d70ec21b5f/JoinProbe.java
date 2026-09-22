
import com.comsol.model.*; import java.util.*;
public final class JoinProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  DatasetFeature join=model.result().dataset().create("joinprobe","Join");
  join.set("data","dset1"); join.set("data2","dset1"); join.set("method","difference");
  join.set("solutions","all"); join.set("solutions2","all");
  String[] types={"Eval","AvSurface","IntSurface","EvalPoint","Interp"};
  for(int k=0;k<types.length;k++) {
   String tag="joinnum"+k; Map<String,Object> row=new LinkedHashMap<>(); String step="create";
   try {
    NumericalFeature n=model.result().numerical().create(tag,types[k]);
    step="setData"; n.set("data","joinprobe"); step="setExpr"; n.set("expr",new String[]{"T"});
    step="selection";
    if(types[k].equals("Interp")) n.set("coord",new double[][]{{.0125,.025,.0375},{.005,.005,.005}});
    else { n.selection().geom(types[k].equals("EvalPoint")?0:2); n.selection().all(); }
    step="read";
    if(types[k].equals("Interp")||types[k].equals("Eval")) row.put("values",n.getData());
    else row.put("values",n.getReal());
    row.put("units",n.getStringArray("unit")); row.put("status","READ");
   } catch(Exception e) { row.put("error",e.toString()); row.put("failed_step",step); }
   finally { if(Arrays.asList(model.result().numerical().tags()).contains(tag)) model.result().numerical().remove(tag); }
   out.put(types[k],row);
  }
  model.result().dataset().remove("joinprobe"); return out;
 }
}
