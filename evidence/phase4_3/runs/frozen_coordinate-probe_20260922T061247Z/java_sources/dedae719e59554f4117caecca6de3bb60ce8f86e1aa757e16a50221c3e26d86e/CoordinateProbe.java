
import com.comsol.model.*; import java.util.*;
public final class CoordinateProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  out.put("geometry_unit",model.geom("geom1").lengthUnit());
  for(int mode=0;mode<2;mode++) {
   String tag="coordprobe"+mode;
   NumericalFeature n=model.result().numerical().create(tag,"Interp");
   Map<String,Object> row=new LinkedHashMap<>();
   try {
    n.set("data","dset1"); n.set("expr",new String[]{"x","y","T"});
    double scale=mode==0?1:1000;
    n.set("coord",new double[][]{{.0125*scale,.025*scale,.0375*scale},{.005*scale,.005*scale,.005*scale}});
    row.put("data",n.getData()); row.put("coordinates",n.getCoordinates());
    row.put("units",n.getStringArray("unit")); row.put("expressions",n.getStringArray("expr"));
   } catch(Exception e) { row.put("error",e.toString()); }
   finally { model.result().numerical().remove(tag); }
   out.put(mode==0?"SI_input":"geometry_input",row);
  }
  return out;
 }
}
