package comsol_mcp.phase1_java;
import com.comsol.model.Model;
import com.comsol.model.util.ModelUtil;
/** Fresh Java process reopening the W02 artifact on its owned server. */
public final class BoundPhase1Reopen {
 public static void main(String[] a) throws Exception {
  ModelUtil.connect(a[0], Integer.parseInt(a[1]));
  try { Model m=ModelUtil.load(ModelUtil.uniquetag("w02reopen"), a[2]); m.result().numerical().create("maxr","MaxSurface"); m.result().numerical("maxr").selection().set(new int[]{1}); m.result().numerical("maxr").set("expr","abs(u-2)"); System.out.println("W02_REOPEN model_tag="+m.tag()+" maxabs="+m.result().numerical("maxr").getReal()[0][0]); }
  finally { ModelUtil.disconnect(); }
  System.exit(0);
 }
}
