
import com.comsol.model.*; import java.util.*;
public final class TableAxisInspector {
 private static Object safe(java.util.concurrent.Callable<Object> body) {
  try { return body.call(); } catch (Exception e) { return "ERROR: " + e.toString(); }
 }
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out = new LinkedHashMap<>();
  out.put("study_tlist", safe(() -> model.study("std1").feature("time").getString("tlist")));
  out.put("solver_tlist", safe(() -> model.sol("sol1").feature("t1").getString("tlist")));
  out.put("solver_pvals", safe(() -> model.sol("sol1").getPVals()));
  out.put("solver_pnames", safe(() -> model.sol("sol1").getPNames()));
  out.put("solver_study", safe(() -> model.sol("sol1").study()));
  SolutionInfo info = model.sol("sol1").getSolutioninfo();
  out.put("levels", safe(() -> info.getLevelNames()));
  out.put("outer", safe(() -> info.getOuterSolnum()));
  out.put("maxinner_1", safe(() -> info.getMaxInner(1)));
  out.put("solnum_1_strict", safe(() -> info.getSolnum(1, true)));
  out.put("solnums_t", safe(() -> info.getSolnums(new String[]{"t"})));
  out.put("vals", safe(() -> info.getVals("t")));
  out.put("datasets", safe(() -> model.result().dataset().tags()));
  out.put("tables", safe(() -> model.result().table().tags()));
  for (String tag : model.result().table().tags()) {
   Map<String,Object> row = new LinkedHashMap<>();
   TableFeature t = model.result().table(tag);
   row.put("type", safe(() -> t.getType()));
   row.put("headers", safe(() -> t.getColumnHeaders()));
   row.put("row_headers", safe(() -> t.getRowHeaders()));
   row.put("real", safe(() -> t.getReal()));
   for (String getter : new String[]{"getTimeValues","getTimes","getTime","getPVals"}) {
    row.put(getter, safe(() -> {
     java.lang.reflect.Method m = TableFeature.class.getMethod(getter);
     return m.invoke(t);
    }));
   }
   out.put("table:" + tag, row);
  }
  for (String tag : model.probe().tags()) {
   Map<String,Object> row = new LinkedHashMap<>();
   row.put("expr", safe(() -> model.probe(tag).getString("expr")));
   row.put("table", safe(() -> model.probe(tag).getString("table")));
   try {
    row.put("model", model.probe(tag).model());
   } catch (Exception e) { row.put("model", "ERROR: " + e.toString()); }
   out.put("probe:" + tag, row);
  }
  return out;
 }
}
