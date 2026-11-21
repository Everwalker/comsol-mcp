package comsol_mcp.worker_java;

import com.comsol.model.Model;
import com.comsol.model.ModelNode;
import com.comsol.model.ModelNodeList;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import sun.misc.Unsafe;

/** Offline JDK-proxy exercise of the real private walk over Model.component(). */
public final class PersistentComsolWorkerNestedWalkHarness {
  private static Object worker(Object root) throws Exception {
    Field f = Unsafe.class.getDeclaredField("theUnsafe");
    f.setAccessible(true);
    Object w = ((Unsafe) f.get(null)).allocateInstance(PersistentComsolWorker.class);
    Field generation = PersistentComsolWorker.class.getDeclaredField("generation");
    generation.setAccessible(true);
    generation.set(w, new AtomicLong(9));
    Field connected = PersistentComsolWorker.class.getDeclaredField("connected");
    connected.setAccessible(true);
    connected.set(w, Boolean.TRUE);
    Field handles = PersistentComsolWorker.class.getDeclaredField("handles");
    handles.setAccessible(true);
    Map<String, Object> h = new ConcurrentHashMap<>();
    h.put("root", root);
    handles.set(w, h);
    return w;
  }

  @SuppressWarnings("unchecked")
  private static Map<String, Object> run(String failure, List<String> calls) throws Exception {
    ClassLoader loader = PersistentComsolWorkerNestedWalkHarness.class.getClassLoader();
    Object child = Proxy.newProxyInstance(loader, new Class<?>[] {ModelNode.class}, (p, m, a) -> {
      String name = m.getName();
      if (a == null || a.length == 0) {
        if (Arrays.asList("tag", "getType", "label").contains(name)) calls.add("child." + name);
        if (name.equals(failure)) throw new RuntimeException("synthetic completed " + name + " failure");
        if (name.equals("tag")) return "comp1";
        if (name.equals("getType")) return "ComponentType";
        if (name.equals("label")) return "Component 1";
      }
      return null;
    });
    Object children = Proxy.newProxyInstance(loader, new Class<?>[] {ModelNodeList.class}, (p, m, a) -> {
      if (m.getName().equals("tags")) { calls.add("list.tags"); return new String[] {"comp1"}; }
      if (m.getName().equals("get")) { calls.add("list.get"); return child; }
      return null;
    });
    Object root = Proxy.newProxyInstance(loader, new Class<?>[] {Model.class}, (p, m, a) -> {
      if (m.getName().equals("component") && (a == null || a.length == 0)) {
        calls.add("root.component");
        return children;
      }
      if (m.getName().equals("tag") && (a == null || a.length == 0)) return "root";
      if (m.getName().equals("label") && (a == null || a.length == 0)) return "Root";
      return null;
    });
    Map<String, Object> query = new LinkedHashMap<>();
    query.put("type", "ComponentType");
    Map<String, Object> candidate = new LinkedHashMap<>();
    candidate.put("collection", "component"); candidate.put("method", "component");
    Map<String, Object> request = new LinkedHashMap<>();
    request.put("handle", "root"); request.put("generation", Long.valueOf(9));
    request.put("query", query); request.put("candidates", Collections.singletonList(candidate));
    request.put("max_nodes", Integer.valueOf(50)); request.put("max_seconds", Double.valueOf(5));
    request.put("skip_visited", Integer.valueOf(0)); request.put("limit", Integer.valueOf(100));
    Method walk = PersistentComsolWorker.class.getDeclaredMethod("walk", Map.class);
    walk.setAccessible(true);
    return (Map<String, Object>) walk.invoke(worker(root), request);
  }

  public static void main(String[] args) throws Exception {
    int failed = 0;
    for (String failure : new String[] {"none", "getType", "label", "tag"}) {
      List<String> calls = new ArrayList<>();
      Map<String, Object> result = run(failure, calls);
      List<?> matches = (List<?>) result.get("matches");
      List<?> errors = (List<?>) result.get("errors");
      boolean reached = calls.contains("root.component") && calls.contains("list.tags") && calls.contains("list.get");
      boolean expected;
      if (failure.equals("none")) {
        expected = reached && matches.size() == 1 && errors.isEmpty()
            && Boolean.TRUE.equals(result.get("complete"));
      } else {
        expected = reached && calls.contains("child." + failure) && !errors.isEmpty()
            && Boolean.FALSE.equals(result.get("complete"));
      }
      System.out.println("SCENARIO " + failure + " reached=" + reached + " calls=" + calls
          + " matches=" + matches.size() + " complete=" + result.get("complete") + " errors=" + errors.size());
      if (!expected) { failed++; System.out.println("ASSERT-FAIL " + failure + " " + result); }
    }
    if (failed != 0) { System.out.println("NESTED_WALK_FAILURES=" + failed); System.exit(1); }
    System.out.println("NESTED_WALK_PASS");
  }
}
