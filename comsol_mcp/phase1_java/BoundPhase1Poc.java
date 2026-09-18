package comsol_mcp.phase1_java;

import com.comsol.model.Model;
import com.comsol.model.util.ModelUtil;

/** W02's deliberately narrow, server-bound Java API recipe. */
public final class BoundPhase1Poc {
  public static void main(String[] args) throws Exception {
    if (args.length != 5) throw new IllegalArgumentException("host port modelTag mphPath pngPath");
    String host = args[0]; int port = Integer.parseInt(args[1]); String tag = args[2];
    String mph = args[3]; String png = args[4];
    ModelUtil.connect(host, port);
    try {
    System.out.println("W02_BUILD " + ModelUtil.getComsolVersion());
    for (String existing : ModelUtil.tags()) if (existing.equals(tag)) throw new IllegalStateException("Model tag already exists");
    Model model = ModelUtil.create(tag);
    model.component().create("comp1", true);
    model.component("comp1").geom().create("geom1", 2);
    model.component("comp1").geom("geom1").feature().create("r1", "Rectangle");
    model.component("comp1").geom("geom1").feature("r1").set("size", new String[]{"1", "1"});
    model.component("comp1").geom("geom1").run();
    model.component("comp1").physics().create("c", "CoefficientFormPDE", "geom1");
    model.component("comp1").physics("c").feature("cfeq1").set("c", "1");
    model.component("comp1").physics("c").feature("cfeq1").set("a", "1");
    model.component("comp1").physics("c").feature("cfeq1").set("f", "2");
    model.component("comp1").mesh().create("mesh1");
    model.component("comp1").mesh("mesh1").automatic(true);
    model.study().create("std1");
    model.study("std1").feature().create("stat", "Stationary");
    model.study("std1").run();
    model.result().create("pg1", "PlotGroup2D");
    model.result("pg1").feature().create("surf1", "Surface");
    model.result("pg1").feature("surf1").set("expr", "u");
    model.result("pg1").run();
    model.result().numerical().create("max1", "MaxSurface");
    model.result().numerical("max1").selection().set(new int[]{1});
    model.result().numerical("max1").set("expr", "abs(u-2)");
    double value = model.result().numerical("max1").getReal()[0][0];
    // Persist the numerical solution before starting an independently fallible render.
    model.save(mph);
    model.result().export().create("img1", "pg1", "Image");
    model.result().export("img1").set("pngfilename", png);
    model.result().export("img1").run();
    System.out.println("W02_RESULT model_tag=" + model.tag() + " u=" + value + " mph=" + mph + " png=" + png);
    } finally {
      ModelUtil.disconnect();
    }
    // Only after successful recipe and disconnect: release vendor non-daemon pool.
    System.exit(0);
  }
}
