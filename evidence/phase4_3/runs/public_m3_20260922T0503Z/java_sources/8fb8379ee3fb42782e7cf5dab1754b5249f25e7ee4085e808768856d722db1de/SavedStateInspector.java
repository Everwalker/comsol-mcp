
import com.comsol.model.*;
import java.util.*;
public final class SavedStateInspector {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out = new LinkedHashMap<>();
  out.put("datasets", model.result().dataset().tags());
  out.put("solutions", model.sol().tags());
  out.put("derived_values", model.result().numerical().tags());
  out.put("material_k", model.material("mat1").propertyGroup("def").getStringArray("thermalconductivity"));
  out.put("left_T", model.physics("ht").feature("temp1").getString("T0"));
  out.put("right_T", model.physics("ht").feature("temp2").getString("T0"));
  out.put("solver_features", model.sol("sol1").feature().tags());
  if (Boolean.TRUE.equals(args.get("transient"))) {
   out.put("solution_parameters", model.sol("sol1").getPNames());
   out.put("solution_values", model.sol("sol1").getPVals());
   out.put("study_tlist", model.study("std1").feature("time").getString("tlist"));
   out.put("solver_tlist", model.sol("sol1").feature("t1").getString("tlist"));
   out.put("solver_rtol", model.sol("sol1").feature("t1").getString("rtol"));
  }
  if (Boolean.TRUE.equals(args.get("continuation"))) {
   out.put("table", model.result().numerical("user_derived_probe").getString("table"));
   out.put("tables", model.result().table().tags());
  }
  return out;
 }
}
