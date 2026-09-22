
import com.comsol.model.*;
import java.util.*;
public final class ContinueC {
 public static Object run(Model model, Map<String,Object> args) {
  model.study("std1").feature("time").set("tlist", "range(0, 1.0, 8.0)");
  model.sol("sol1").feature("t1").set("tlist", "range(0, 1.0, 8.0)");
  model.sol("sol1").feature("t1").set("rtol", "0.00001");
  model.result().table().create("user_table", "Table");
  model.result().numerical("user_derived_probe").set("table", "user_table");
  model.result().numerical("user_derived_probe").set("data", "dset1");
  model.result().numerical("user_derived_probe").selection().geom(2);
  model.result().numerical("user_derived_probe").selection().all();
  model.sol("sol1").runAll();
  model.result().numerical("user_derived_probe").setResult();
  return Collections.singletonMap("status", "CONTINUED_MANUAL_SOLVER");
 }
}
