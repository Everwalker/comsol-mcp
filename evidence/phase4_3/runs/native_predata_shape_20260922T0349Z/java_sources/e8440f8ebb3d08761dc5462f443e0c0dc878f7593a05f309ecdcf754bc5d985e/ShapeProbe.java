
import com.comsol.model.*; import java.util.*;
public final class ShapeProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  NumericalFeature n=model.result().numerical().create("shapeprobe","Eval");
  try {
   n.set("data","dset1"); n.set("expr",new String[]{"T","ht.tfluxx"});
   double[][] coords=n.getCoordinates();
   int[] lengths=new int[coords.length]; for(int j=0;j<coords.length;j++) lengths[j]=coords[j].length;
   out.put("before_getData_coordinate_rows",coords.length); out.put("before_getData_coordinate_row_lengths",lengths);
   out.put("nData",n.getNData());
   double[][][] data=n.getData();
   out.put("after_getData_shape",new int[]{data.length,data[0].length,data[0][0].length});
   out.put("shape_matches",coords.length==2 && coords[0].length==data[0][0].length && coords[1].length==coords[0].length);
   out.put("ordering","getCoordinates then getNData then getData; no run/getData before coordinate shape");
   return out;
  } finally { model.result().numerical().remove("shapeprobe"); }
 }
}
