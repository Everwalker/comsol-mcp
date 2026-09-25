/*
 * Configb1896dbbea5741038e464449ac797e8d.java
 */

import com.comsol.model.*;
import com.comsol.model.util.*;

/** Model exported on Sep 25 2026, 19:36 by COMSOL 6.4.0.293. */
public class Configb1896dbbea5741038e464449ac797e8d {

  public static Model run() {
    Model model = ModelUtil.create("Model");

    model.modelPath("C:\\Temp\\w21closure_20260925\\smoke64_09\\project\\w21\\config");

    model.label("W21Owned");

    model.param().set("L", "0.05[m]");
    model.param().set("k", "400[W/(m*K)]");
    model.param().set("rhoCp", "3.8e6[J/(m^3*K)]");

    model.component().create("comp1");

    model.component("comp1").geom().create("geom1", 3);
    model.component("comp1").geom("geom1").create("blk1", "Block");
    model.component("comp1").geom("geom1").feature("blk1").set("size", new String[]{"L", "L/5", "L/5"});
    model.component("comp1").geom("geom1").run();

    model.component("comp1").physics().create("ht", "HeatTransfer", "geom1");
    model.component("comp1").physics("ht").create("temp1", "TemperatureBoundary", 2);
    model.component("comp1").physics("ht").feature("temp1").selection().set(1);
    model.component("comp1").physics("ht").feature("temp1").set("T0", "300[K]");
    model.component("comp1").physics("ht").create("temp2", "TemperatureBoundary", 2);
    model.component("comp1").physics("ht").feature("temp2").selection().set(6);
    model.component("comp1").physics("ht").feature("temp2").set("T0", "300[K]");
    model.component("comp1").physics("ht").feature("init1").set("T", "300[K] + 10[K]*sin(pi*x/L)");

    model.component("comp1").material().create("mat1", "Common");
    model.component("comp1").material("mat1").selection().all();
    model.component("comp1").material("mat1").propertyGroup("def").set("thermalconductivity", new String[]{"k"});
    model.component("comp1").material("mat1").propertyGroup("def").set("density", "1.0[kg/m^3]");
    model.component("comp1").material("mat1").propertyGroup("def").set("heatcapacity", "rhoCp/(1[kg/m^3])");

    model.component("comp1").mesh().create("mesh1");
    model.component("comp1").mesh("mesh1").feature("size").set("hauto", 2);
    model.component("comp1").mesh("mesh1").run();

    model.study().create("std1");
    model.study("std1").create("time", "Transient");
    model.study("std1").feature("time").set("tlist", "0 0.5 1 1.5 2");
    model.study("std1").feature("time").set("usertol", "on");
    model.study("std1").feature("time").set("rtol", "1e-6");
    model.study().create("std2");
    model.study("std2").create("time", "Transient");
    model.study("std2").feature("time").set("tlist", "2 2.5 3 3.5 4");
    model.study("std2").feature("time").set("usertol", "on");
    model.study("std2").feature("time").set("rtol", "1e-6");

    return model;
  }

  public static void main(String[] args) {
    run();
  }

}
