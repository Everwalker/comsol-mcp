import com.comsol.model.*;
import java.util.*;
public final class W20SolutionStats {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> result=new LinkedHashMap<>();
  int[] size=model.sol("sol1").getSize();
  result.put("dofs",size[0]); result.put("solutions",size[1]);
  result.put("mesh_hauto",model.mesh("mesh1").feature("size").getString("hauto"));
  result.put("sweep_positions",model.mesh("mesh1").feature("swe1").feature("dis1").getDoubleArray("explicit"));
  result.put("rtol",model.study("std1").feature("time").getString("rtol"));
  result.put("tlist",model.study("std1").feature("time").getString("tlist"));
  return result;
 }
}
