
import com.comsol.model.*; import java.util.*;
public final class OuterSweep {
 public static Object run(Model model, Map<String,Object> args) {
  model.param().set("p1", "1"); model.param().set("p2", "3");
  model.physics("ht").feature("temp2").set("T0", "293.15[K]+p1*10[K]+p2*1[K]");
  model.study("std1").create("param", "Parametric");
  model.study("std1").feature("param").set("pname", new String[]{"p1","p2"});
  model.study("std1").feature("param").set("plistarr", new String[]{"1 2","3 4"});
  model.study("std1").feature("param").set("punit", new String[]{"",""});
  model.study("std1").feature("param").set("sweeptype", "filled");
  model.study("std1").run(); return Collections.singletonMap("status", "SOLVED");
 }
}
