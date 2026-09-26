/*
 * Configbc5f1c31108b47ffb1eb73e7b27cce52.java
 */

import com.comsol.model.*;
import com.comsol.model.util.*;

/** Model exported on Sep 26 2026, 07:57 by COMSOL 6.4.0.293. */
public class Configbc5f1c31108b47ffb1eb73e7b27cce52 {

  public static Model run() {
    Model model = ModelUtil.create("Model");

    model.modelPath("C:\\Temp\\w22_20260926\\full64_01\\project\\w21\\config");

    model.label("W22Owned");

    model.param().set("L", "0.01[m]");
    model.param().set("p0", "1.0[W]");
    model.param().set("p1", "0.65[W]");
    model.param().set("ptotal", "12.45[W]");
    model.param().set("p2", "(ptotal-p0-6*p1)/11");
    model.param().set("alpha", "0.6");
    model.param().set("Tamb", "300[K]");

    model.component().create("comp1", false);

    model.component("comp1").geom().create("geom1", 3);

    model.func().create("b0g0", "Interpolation");
    model.func().create("b0g1", "Interpolation");
    model.func().create("b0g2", "Interpolation");
    model.func().create("b1g0", "Interpolation");
    model.func().create("b1g1", "Interpolation");
    model.func().create("b1g2", "Interpolation");
    model.func().create("b2g0", "Interpolation");
    model.func().create("b2g1", "Interpolation");
    model.func().create("b2g2", "Interpolation");
    model.func("b0g0").set("source", "file");
    model.func("b0g0").set("importedname", "basis_l0_g0.txt");
    model.func("b0g0").set("filecolumns", 3);
    model.func("b0g0").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b0g0").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b0g0").set("extrap", "value");
    model.func("b0g0").set("fununit", new String[]{"1/m^2"});
    model.func("b0g0").set("argunit", new String[]{"m", "m"});
    model.func("b0g0")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\-1wwhsn0jxva82\\basis_l0_g0.txt");
    model.func("b0g0").importData();
    model.func("b0g1").set("source", "file");
    model.func("b0g1").set("importedname", "basis_l0_g1.txt");
    model.func("b0g1").set("filecolumns", 3);
    model.func("b0g1").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b0g1").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b0g1").set("extrap", "value");
    model.func("b0g1").set("fununit", new String[]{"1/m^2"});
    model.func("b0g1").set("argunit", new String[]{"m", "m"});
    model.func("b0g1")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\-1v8rfm88hrjux\\basis_l0_g1.txt");
    model.func("b0g1").importData();
    model.func("b0g2").set("source", "file");
    model.func("b0g2").set("importedname", "basis_l0_g2.txt");
    model.func("b0g2").set("filecolumns", 3);
    model.func("b0g2").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b0g2").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b0g2").set("extrap", "value");
    model.func("b0g2").set("fununit", new String[]{"1/m^2"});
    model.func("b0g2").set("argunit", new String[]{"m", "m"});
    model.func("b0g2")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\xa3967gmdozh\\basis_l0_g2.txt");
    model.func("b0g2").importData();
    model.func("b1g0").set("source", "file");
    model.func("b1g0").set("importedname", "basis_l1_g0.txt");
    model.func("b1g0").set("filecolumns", 3);
    model.func("b1g0").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b1g0").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b1g0").set("extrap", "value");
    model.func("b1g0").set("fununit", new String[]{"1/m^2"});
    model.func("b1g0").set("argunit", new String[]{"m", "m"});
    model.func("b1g0")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\owe9c3sympg3\\basis_l1_g0.txt");
    model.func("b1g0").importData();
    model.func("b1g1").set("source", "file");
    model.func("b1g1").set("importedname", "basis_l1_g1.txt");
    model.func("b1g1").set("filecolumns", 3);
    model.func("b1g1").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b1g1").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b1g1").set("extrap", "value");
    model.func("b1g1").set("fununit", new String[]{"1/m^2"});
    model.func("b1g1").set("argunit", new String[]{"m", "m"});
    model.func("b1g1")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\1k6ufvyqlf1y5\\basis_l1_g1.txt");
    model.func("b1g1").importData();
    model.func("b1g2").set("source", "file");
    model.func("b1g2").set("importedname", "basis_l1_g2.txt");
    model.func("b1g2").set("filecolumns", 3);
    model.func("b1g2").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b1g2").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b1g2").set("extrap", "value");
    model.func("b1g2").set("fununit", new String[]{"1/m^2"});
    model.func("b1g2").set("argunit", new String[]{"m", "m"});
    model.func("b1g2")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\-k5yb0sndnxr4\\basis_l1_g2.txt");
    model.func("b1g2").importData();
    model.func("b2g0").set("source", "file");
    model.func("b2g0").set("importedname", "basis_l2_g0.txt");
    model.func("b2g0").set("filecolumns", 3);
    model.func("b2g0").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b2g0").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b2g0").set("extrap", "value");
    model.func("b2g0").set("fununit", new String[]{"1/m^2"});
    model.func("b2g0").set("argunit", new String[]{"m", "m"});
    model.func("b2g0")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\1caab421pmcnq\\basis_l2_g0.txt");
    model.func("b2g0").importData();
    model.func("b2g1").set("source", "file");
    model.func("b2g1").set("importedname", "basis_l2_g1.txt");
    model.func("b2g1").set("filecolumns", 3);
    model.func("b2g1").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b2g1").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b2g1").set("extrap", "value");
    model.func("b2g1").set("fununit", new String[]{"1/m^2"});
    model.func("b2g1").set("argunit", new String[]{"m", "m"});
    model.func("b2g1")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\-19j7zou1amgps\\basis_l2_g1.txt");
    model.func("b2g1").importData();
    model.func("b2g2").set("source", "file");
    model.func("b2g2").set("importedname", "basis_l2_g2.txt");
    model.func("b2g2").set("filecolumns", 3);
    model.func("b2g2").set("columnKeys", new String[]{"col1", "col2", "col3"});
    model.func("b2g2").set("columnType", new String[]{"col1", "arg", "col2", "arg", "col3", "value"});
    model.func("b2g2").set("extrap", "value");
    model.func("b2g2").set("fununit", new String[]{"1/m^2"});
    model.func("b2g2").set("argunit", new String[]{"m", "m"});
    model.func("b2g2")
         .set("filename", "C:\\Temp\\w22_20260926\\full64_01\\runtime\\tmp\\csmphserver70524\\1foc8xdld0ttd\\basis_l2_g2.txt");
    model.func("b2g2").importData();

    model.component("comp1").mesh().create("mesh1");

    model.component("comp1").geom("geom1").geomRep("cadps");
    model.component("comp1").geom("geom1").designBooleans(true);
    model.component("comp1").geom("geom1").create("blk1", "Block");
    model.component("comp1").geom("geom1").feature("blk1").set("size", new double[]{0.04, 0.04, 0.001});
    model.component("comp1").geom("geom1").feature("blk1").set("pos", new double[]{-0.02, -0.02, 0});
    model.component("comp1").geom("geom1").create("cyl1", "Cylinder");
    model.component("comp1").geom("geom1").feature("cyl1").set("r", 0.015);
    model.component("comp1").geom("geom1").feature("cyl1").set("h", 0.001);
    model.component("comp1").geom("geom1").run();

    model.component("comp1").selection().create("top", "Box");
    model.component("comp1").selection("top").set("entitydim", 2);
    model.component("comp1").selection().create("bottom", "Box");
    model.component("comp1").selection("bottom").set("entitydim", 2);
    model.component("comp1").selection().create("roi_box", "Box");
    model.component("comp1").selection("roi_box").set("entitydim", 2);
    model.component("comp1").selection("top").set("zmin", 9.9999E-4);
    model.component("comp1").selection("top").set("zmax", 0.00100001);
    model.component("comp1").selection("top").set("condition", "inside");
    model.component("comp1").selection("bottom").set("zmin", -1.0E-8);
    model.component("comp1").selection("bottom").set("zmax", 1.0E-8);
    model.component("comp1").selection("bottom").set("condition", "inside");
    model.component("comp1").selection("roi_box").set("xmin", -0.01500001);
    model.component("comp1").selection("roi_box").set("xmax", 0.01500001);
    model.component("comp1").selection("roi_box").set("ymin", -0.01500001);
    model.component("comp1").selection("roi_box").set("ymax", 0.01500001);
    model.component("comp1").selection("roi_box").set("zmin", 9.9999E-4);
    model.component("comp1").selection("roi_box").set("zmax", 0.00100001);
    model.component("comp1").selection("roi_box").set("condition", "inside");

    model.component("comp1").variable().create("v1");
    model.component("comp1").variable("v1")
         .set("incident", "if(abs(L-0.01[m])<1e-9[m],p0*b0g0(x,y),0[W/m^2])+if(abs(L-0.01[m])<1e-9[m],p1*b0g1(x,y),0[W/m^2])+if(abs(L-0.01[m])<1e-9[m],p2*b0g2(x,y),0[W/m^2])+if(abs(L-0.02[m])<1e-9[m],p0*b1g0(x,y),0[W/m^2])+if(abs(L-0.02[m])<1e-9[m],p1*b1g1(x,y),0[W/m^2])+if(abs(L-0.02[m])<1e-9[m],p2*b1g2(x,y),0[W/m^2])+if(abs(L-0.03[m])<1e-9[m],p0*b2g0(x,y),0[W/m^2])+if(abs(L-0.03[m])<1e-9[m],p1*b2g1(x,y),0[W/m^2])+if(abs(L-0.03[m])<1e-9[m],p2*b2g2(x,y),0[W/m^2])");
    model.component("comp1").variable("v1").set("qabs", "alpha*incident");
    model.component("comp1").variable("v1").set("roi_area", "iroi(1)");
    model.component("comp1").variable("v1").set("roi_mean", "iroi(T-Tamb)/iroi(1)");
    model.component("comp1").variable("v1").set("roi_std", "sqrt(iroi((T-Tamb-roi_mean)^2)/iroi(1))");
    model.component("comp1").variable("v1").set("roi_cv_guarded", "roi_std/max(roi_mean,1e-12[K])");
    model.component("comp1").variable("v1").set("roi_max", "mxroi(T-Tamb)");
    model.component("comp1").variable("v1").set("roi_min", "mnroi(T-Tamb)");
    model.component("comp1").variable("v1").set("Pinc", "itop(incident)");
    model.component("comp1").variable("v1").set("Pabs", "itop(qabs)");
    model.component("comp1").variable("v1").set("Proi", "iroi(qabs)");
    model.component("comp1").variable("v1").set("Pout", "ibot(500[W/(m^2*K)]*(T-Tamb))");
    model.component("comp1").variable("v1").set("Pstore", "ivol(3000[kg/m^3]*700[J/(kg*K)]*d(T,t))");
    model.component("comp1").variable("v1").set("U", "ivol(3000[kg/m^3]*700[J/(kg*K)]*(T-Tamb))");

    model.component("comp1").material().create("mat1", "Common");

    model.component("comp1").cpl().create("iroi", "Integration");
    model.component("comp1").cpl().create("itop", "Integration");
    model.component("comp1").cpl().create("ibot", "Integration");
    model.component("comp1").cpl().create("ivol", "Integration");
    model.component("comp1").cpl().create("mxroi", "Maximum");
    model.component("comp1").cpl().create("mnroi", "Minimum");
    model.component("comp1").cpl("iroi").selection().named("roi_box");
    model.component("comp1").cpl("itop").selection().named("top");
    model.component("comp1").cpl("ibot").selection().named("bottom");
    model.component("comp1").cpl("ivol").selection().all();
    model.component("comp1").cpl("mxroi").selection().named("roi_box");
    model.component("comp1").cpl("mnroi").selection().named("roi_box");

    model.component("comp1").physics().create("ht", "HeatTransfer", "geom1");
    model.component("comp1").physics("ht").create("heat", "HeatFluxBoundary", 2);
    model.component("comp1").physics("ht").feature("heat").selection().named("top");
    model.component("comp1").physics("ht").create("cool", "HeatFluxBoundary", 2);
    model.component("comp1").physics("ht").feature("cool").selection().named("bottom");

    model.component("comp1").mesh("mesh1").create("auto_f1", "FreeTet");

    model.component("comp1").material("mat1").propertyGroup("def")
         .set("thermalconductivity", new String[]{"20[W/(m*K)]", "0", "0", "0", "20[W/(m*K)]", "0", "0", "0", "20[W/(m*K)]"});
    model.component("comp1").material("mat1").propertyGroup("def").set("density", "3000[kg/m^3]");
    model.component("comp1").material("mat1").propertyGroup("def").set("heatcapacity", "700[J/(kg*K)]");

    model.component("comp1").cpl("iroi").label("\u79ef\u5206 2.1");
    model.component("comp1").cpl("itop").label("\u79ef\u5206 3.1");
    model.component("comp1").cpl("ibot").label("\u79ef\u5206 4.1");
    model.component("comp1").cpl("ivol").label("\u79ef\u5206 5");

    model.component("comp1").physics("ht").feature("init1").set("Tinit", "Tamb");
    model.component("comp1").physics("ht").feature("heat").set("q0_input", "qabs");
    model.component("comp1").physics("ht").feature("cool").set("q0_input", "500[W/(m^2*K)]*(Tamb-T)");

    model.component("comp1").mesh("mesh1").feature("size").set("custom", "on");
    model.component("comp1").mesh("mesh1").feature("size").set("hmax", 7.5E-4);
    model.component("comp1").mesh("mesh1").feature("size").set("hmin", 1.5E-4);
    model.component("comp1").mesh("mesh1").run();

    model.study().create("std1");
    model.study("std1").create("time", "Transient");

    model.sol().create("sol1");
    model.sol("sol1").attach("std1");

    model.study("std1").feature("time").set("tlist", "0 1 5 20 60");
    model.study("std1").feature("time").set("usertol", true);
    model.study("std1").feature("time").set("rtol", "1e-6");

    model.sol("sol1").createAutoSequence("std1");

    model.study("std1").runNoGen();

    return model;
  }

  public static void main(String[] args) {
    run();
  }

}
