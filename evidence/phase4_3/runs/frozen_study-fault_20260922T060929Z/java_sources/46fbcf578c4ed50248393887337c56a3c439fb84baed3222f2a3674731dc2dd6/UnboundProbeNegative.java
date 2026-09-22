import com.comsol.model.*; import java.util.*;
public final class UnboundProbeNegative {
 public static Object run(Model model, Map<String,Object> args) {
  model.probe().create("unbound_negative", "GlobalVariable");
  model.probe("unbound_negative").set("expr", "2");
  model.probe("unbound_negative").genResult("sol1");
  return Collections.singletonMap("negative_fixture", "unbound native probe");
 }
}