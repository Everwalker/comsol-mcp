
import com.comsol.model.*; import java.util.*;
public final class MeasureProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  String[] types={"IntPoint","AvPoint","EvalPoint","MaxPoint","MinPoint","IntLine","IntSurface","IntVolume"};
  int[] dims={0,0,0,0,0,1,2,3};
  for(int k=0;k<types.length;k++) {
   String tag="measureprobe"+k; Map<String,Object> row=new LinkedHashMap<>();
   try {
    NumericalFeature n=model.result().numerical().create(tag,types[k]);
    row.put("created_type",n.getType()); n.set("data","dset1"); n.set("expr",new String[]{"2","1"});
    n.selection().geom(dims[k]); n.selection().all();
    row.put("real",n.getReal()); row.put("data",n.getData());
    row.put("coordinates",n.getCoordinates()); row.put("units",n.getStringArray("unit"));
   } catch(Exception e) { row.put("error",e.toString()); }
   finally { if(Arrays.asList(model.result().numerical().tags()).contains(tag)) model.result().numerical().remove(tag); }
   out.put(types[k],row);
  }
  return out;
 }
}
