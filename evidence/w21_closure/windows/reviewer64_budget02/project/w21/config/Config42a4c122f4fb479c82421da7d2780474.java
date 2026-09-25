/*
 * Config42a4c122f4fb479c82421da7d2780474.java
 */

import com.comsol.model.*;
import com.comsol.model.util.*;

/** Model exported on Sep 25 2026, 20:13 by COMSOL 6.4.0.293. */
public class Config42a4c122f4fb479c82421da7d2780474 {

  public static Model run() {
    Model model = ModelUtil.create("Model");

    model.modelPath("C:\\Temp\\w21closure_20260925\\reviewer64_budget02\\project\\w21\\config");

    model.label("ReviewerIndependent");

    model.param().set("L", "0.05[m]");
    model.param().set("k", "381.0[W/(m*K)]");
    model.param().set("rhoCp", "3500000.0[J/(m^3*K)]");

    model.component().create("comp1", false);

    model.component("comp1").geom().create("geom1", 3);

    model.component("comp1").mesh().create("mesh1");

    model.component("comp1").geom("geom1").geomRep("cadps");
    model.component("comp1").geom("geom1").designBooleans(true);
    model.component("comp1").geom("geom1").create("blk1", "Block");
    model.component("comp1").geom("geom1").feature("blk1").set("size", new String[]{"L", "L/5", "L/5"});
    model.component("comp1").geom("geom1").run();

    model.component("comp1").material().create("mat1", "Common");

    model.component("comp1").physics().create("ht", "HeatTransfer", "geom1");
    model.component("comp1").physics("ht").create("temp1", "TemperatureBoundary", 2);
    model.component("comp1").physics("ht").feature("temp1").selection().set(1);
    model.component("comp1").physics("ht").create("temp2", "TemperatureBoundary", 2);
    model.component("comp1").physics("ht").feature("temp2").selection().set(6);

    model.component("comp1").mesh("mesh1").create("auto_f1", "FreeTet");

    model.component("comp1").material("mat1").propertyGroup("def")
         .set("thermalconductivity", new String[]{"k", "0", "0", "0", "k", "0", "0", "0", "k"});
    model.component("comp1").material("mat1").propertyGroup("def").set("density", "1.0[kg/m^3]");
    model.component("comp1").material("mat1").propertyGroup("def").set("heatcapacity", "rhoCp/(1[kg/m^3])");

    model.component("comp1").physics("ht").feature("init1").set("Tinit", "300[K] + 10[K]*sin(pi*x/L)");
    model.component("comp1").physics("ht").feature("temp1").set("T0", "300[K]");
    model.component("comp1").physics("ht").feature("temp2").set("T0", "300[K]");

    model.component("comp1").mesh("mesh1").feature("size").set("hauto", 2);
    model.component("comp1").mesh("mesh1").run();

    model.study().create("std1");
    model.study("std1").create("time", "Transient");
    model.study().create("std2");
    model.study("std2").create("time", "Transient");

    model.sol().create("sol1");
    model.sol("sol1").attach("std1");

    model.study("std1").feature("time").set("tlist", "0 0.5 1 1.5 2");
    model.study("std1").feature("time").set("usertol", true);
    model.study("std1").feature("time").set("rtol", "1e-6");
    model.study("std2").feature("time").set("tlist", "2 2.5 3 3.5 4");
    model.study("std2").feature("time").set("usertol", true);
    model.study("std2").feature("time").set("rtol", "1e-6");

    model.sol("sol1").createAutoSequence("std1");

    model.study("std1").runNoGen();

    return model;
  }

  public static void main(String[] args) {
    run();
  }

}
