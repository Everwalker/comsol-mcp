package comsol_mcp.worker_java;

import com.comsol.model.Model;
import com.comsol.model.ModelNode;
import com.comsol.model.ModelNodeList;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import sun.misc.Unsafe;

/**
 * T13 fix-control harness (qwen, 2026-11-09) for the metadata-getter repair in
 * PersistentComsolWorker.walk(Map) / safeInvokeString.  Offline JDK-proxy
 * exercise of the real private walk(Map) only: no Worker constructor, no
 * service, no socket, no COMSOL process, no engine.  NOT native evidence.
 *
 * Controls pinned here (each prints one CTRL line; failures accumulate):
 *  A1 clean healthy nested traversal stays clean (errors=[], complete=true);
 *     the Model root has no getType() API and that normal absence must stay
 *     a non-error, exactly like before the fix.
 *  B1 declared zero-arg getType whose getter body throws NoSuchMethodException
 *     itself (interface declares the checked throw, so the exception reaches
 *     the worker unwrapped): a handler counter proves the body executed, so
 *     the exception NAME alone must not misclassify the fault as "API absent".
 *     The completed fault must be in errors and complete must be false even
 *     though the tag-only query still yields a surviving match.
 *  C1 declared getType whose body throws an undeclared checked exception
 *     (java.io.IOException, surfaced by the JDK proxy as an
 *     UndeclaredThrowableException after unwrapping): recorded as a fault.
 *     A SENTINEL string embedded in the fault message must never appear
 *     anywhere in the reply (no exception-detail leak).
 *  D1 fault-error bound: 30 children faulting on all three getters produce
 *     90 completed faults but the reply keeps at most 50 error entries
 *     (same bound as the collection-probe errors), stays complete=false, and
 *     leaks no SENTINEL.
 *  E1 a declared getter that merely returns null is NOT a fault: clean
 *     errors=[] complete=true (normal API shape, no blanket error).
 */
public final class PersistentComsolWorkerWalkFixControlHarness {
  private static int failures = 0;

  private static final String SENTINEL = "SENTINEL_DO_NOT_LEAK_getter_detail";

  public interface NoSuchMethodFaultNode {
    String tag();
    String label();
    String getType() throws java.lang.NoSuchMethodException;
  }

  public interface WrappedFaultNode {
    String tag();
    String label();
    String getType();
  }

  private static Object worker(Object root) throws Exception {
    Field f = Unsafe.class.getDeclaredField("theUnsafe");
    f.setAccessible(true);
    Object w = ((Unsafe) f.get(null)).allocateInstance(PersistentComsolWorker.class);
    Field generation = PersistentComsolWorker.class.getDeclaredField("generation");
    generation.setAccessible(true);
    generation.set(w, new AtomicLong(11));
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
  private static Map<String, Object> walk(Object root, Map<String, Object> query, List<Object> candidates)
      throws Exception {
    Method walk = PersistentComsolWorker.class.getDeclaredMethod("walk", Map.class);
    walk.setAccessible(true);
    Map<String, Object> request = new LinkedHashMap<>();
    request.put("handle", "root");
    request.put("generation", Long.valueOf(11));
    request.put("query", query);
    request.put("candidates", candidates);
    request.put("max_nodes", Integer.valueOf(200));
    request.put("max_seconds", Double.valueOf(5));
    request.put("skip_visited", Integer.valueOf(0));
    request.put("limit", Integer.valueOf(100));
    return (Map<String, Object>) walk.invoke(worker(root), request);
  }

  private static void check(boolean value, String message) {
    if (!value) { failures++; System.out.println("ASSERT-FAIL: " + message); }
  }

  private static ModelNodeList childList(Object[] childHolder) {
    return (ModelNodeList) Proxy.newProxyInstance(
        PersistentComsolWorkerWalkFixControlHarness.class.getClassLoader(),
        new Class<?>[] {ModelNodeList.class}, (p, m, a) -> {
          if (m.getName().equals("tags")) return new String[] {"comp1"};
          if (m.getName().equals("get")) return childHolder[0];
          return null;
        });
  }

  private static Object modelRoot(Object[] childHolder) {
    return Proxy.newProxyInstance(
        PersistentComsolWorkerWalkFixControlHarness.class.getClassLoader(),
        new Class<?>[] {Model.class}, (p, m, a) -> {
          String name = m.getName();
          if (name.equals("component") && (a == null || a.length == 0)) return childList(childHolder);
          if (name.equals("tag") && (a == null || a.length == 0)) return "root";
          if (name.equals("label") && (a == null || a.length == 0)) return "Root";
          return null;
        });
  }

  private static List<Object> componentCandidate() {
    Map<String, Object> candidate = new LinkedHashMap<>();
    candidate.put("collection", "component");
    candidate.put("method", "component");
    return Collections.singletonList(candidate);
  }

  private static void report(String id, Map<String, Object> reply, String threw) {
    List<?> matches = (List<?>) reply.get("matches");
    List<?> errors = (List<?>) reply.get("errors");
    String rendered = String.valueOf(new TreeMap<>(reply).toString());
    System.out.println("CTRL id=" + id + " matches=" + matches.size() + " complete=" + reply.get("complete")
        + " errors=" + errors.size() + " truncated=" + reply.get("truncated_reason")
        + " threw=" + threw + " sentinelInReply=" + rendered.contains(SENTINEL));
  }

  public static void main(String[] args) throws Exception {
    ClassLoader loader = PersistentComsolWorkerWalkFixControlHarness.class.getClassLoader();

    // ---- A1: healthy nested traversal; Model root's missing getType stays clean ----
    {
      Object[] childHolder = new Object[1];
      childHolder[0] = Proxy.newProxyInstance(loader, new Class<?>[] {ModelNode.class}, (p, m, a) -> {
        String name = m.getName();
        if (a == null || a.length == 0) {
          if (name.equals("tag")) return "comp1";
          if (name.equals("getType")) return "ComponentType";
          if (name.equals("label")) return "Component 1";
        }
        return null;
      });
      Map<String, Object> query = new LinkedHashMap<>();
      query.put("type", "ComponentType");
      Map<String, Object> reply = walk(modelRoot(childHolder), query, componentCandidate());
      report("A1", reply, "-");
      check(((List<?>) reply.get("matches")).size() == 1, "A1 healthy child must still match once");
      check(((List<?>) reply.get("errors")).isEmpty(),
          "A1 clean traversal (incl. Model root with NO getType API) must keep errors=[]");
      check(Boolean.TRUE.equals(reply.get("complete")), "A1 clean traversal must stay complete=true");
      check(!String.valueOf(reply).contains(SENTINEL), "A1 reply must not contain the sentinel");
    }

    // ---- B1: declared getter whose body throws NoSuchMethodException itself ----
    {
      final int[] getTypeCalls = {0};
      Object node = Proxy.newProxyInstance(loader, new Class<?>[] {NoSuchMethodFaultNode.class}, (p, m, a) -> {
        String name = m.getName();
        if (name.equals("tag")) return "t1";
        if (name.equals("label")) return "Label 1";
        if (name.equals("getType")) {
          getTypeCalls[0]++;
          throw new java.lang.NoSuchMethodException(SENTINEL + " getType body");
        }
        return null;
      });
      Map<String, Object> query = new LinkedHashMap<>();
      query.put("tag", "t1");
      Map<String, Object> reply = walk(node, query, new ArrayList<>());
      report("B1", reply, "-");
      check(getTypeCalls[0] >= 1, "B1 getType body must have executed (handler counter)");
      check(!((List<?>) reply.get("errors")).isEmpty(),
          "B1 getter-execution NoSuchMethodException must be recorded as a fault, not treated as API absence");
      check(Boolean.FALSE.equals(reply.get("complete")),
          "B1 completed getter fault must force complete=false even with a surviving tag match");
      check(((List<?>) reply.get("matches")).size() == 1, "B1 tag-only query may keep its surviving match");
      check(!String.valueOf(reply).contains(SENTINEL), "B1 exception message must not leak into the reply");
    }

    // ---- C1: declared getter body throws an undeclared checked exception (wrapped) ----
    {
      final int[] getTypeCalls = {0};
      Object node = Proxy.newProxyInstance(loader, new Class<?>[] {WrappedFaultNode.class}, (p, m, a) -> {
        String name = m.getName();
        if (name.equals("tag")) return "t2";
        if (name.equals("label")) return "Label 2";
        if (name.equals("getType")) {
          getTypeCalls[0]++;
          throw new java.io.IOException(SENTINEL + " wrapped IOException");
        }
        return null;
      });
      Map<String, Object> query = new LinkedHashMap<>();
      query.put("tag", "t2");
      Map<String, Object> reply = walk(node, query, new ArrayList<>());
      report("C1", reply, "-");
      check(getTypeCalls[0] >= 1, "C1 getType body must have executed (handler counter)");
      check(!((List<?>) reply.get("errors")).isEmpty(),
          "C1 wrapped completed getter fault must be recorded in errors");
      check(Boolean.FALSE.equals(reply.get("complete")), "C1 completed getter fault must force complete=false");
      check(!String.valueOf(reply).contains(SENTINEL), "C1 wrapped exception detail must not leak into the reply");
    }

    // ---- D1: 30 children x 3 faulted getters -> bounded error list, never complete ----
    {
      Object[] childHolder = new Object[1];
      childHolder[0] = Proxy.newProxyInstance(loader, new Class<?>[] {ModelNode.class}, (p, m, a) -> {
        String name = m.getName();
        if ((a == null || a.length == 0)
            && (name.equals("tag") || name.equals("getType") || name.equals("label"))) {
          throw new RuntimeException(SENTINEL + " child " + name);
        }
        return null;
      });
      // Root Model list with 30 tags, each resolving to a faulting child.
      Object root = Proxy.newProxyInstance(loader, new Class<?>[] {Model.class}, (p, m, a) -> {
        String name = m.getName();
        if (name.equals("component") && (a == null || a.length == 0)) {
          return Proxy.newProxyInstance(loader, new Class<?>[] {ModelNodeList.class}, (lp, lm, la) -> {
            if (lm.getName().equals("tags")) {
              String[] tags = new String[30];
              for (int i = 0; i < tags.length; i++) tags[i] = "child" + i;
              return tags;
            }
            if (lm.getName().equals("get")) return childHolder[0];
            return null;
          });
        }
        if (name.equals("tag") && (a == null || a.length == 0)) return "root";
        if (name.equals("label") && (a == null || a.length == 0)) return "Root";
        return null;
      });
      Map<String, Object> reply = walk(root, new LinkedHashMap<>(), componentCandidate());
      report("D1", reply, "-");
      int errorCount = ((List<?>) reply.get("errors")).size();
      check(errorCount == 50,
          "D1 error list must stay bounded at the existing 50-entry cap despite 90 completed faults (got "
          + errorCount + ")");
      check(Boolean.FALSE.equals(reply.get("complete")), "D1 faulted traversal must be complete=false");
      check(reply.get("truncated_reason") == null,
          "D1 the walk loop itself drained normally; incompleteness comes from the recorded faults");
      check(!String.valueOf(reply).contains(SENTINEL), "D1 fault messages must not leak into the reply");
    }

    // ---- E1: declared getter returning null is normal, not a fault ----
    {
      Object node = Proxy.newProxyInstance(loader, new Class<?>[] {ModelNode.class}, (p, m, a) -> {
        String name = m.getName();
        if (a == null || a.length == 0) {
          if (name.equals("tag")) return "t3";
          if (name.equals("label")) return "Label 3";
          return null; // getType and every other zero-arg getter: null value
        }
        return null;
      });
      Map<String, Object> query = new LinkedHashMap<>();
      query.put("tag", "t3");
      Map<String, Object> reply = walk(node, query, new ArrayList<>());
      report("E1", reply, "-");
      check(((List<?>) reply.get("errors")).isEmpty(),
          "E1 a getter that returns null is not a fault; errors must stay empty");
      check(Boolean.TRUE.equals(reply.get("complete")), "E1 null-valued getter must keep complete=true");
      check(((List<?>) reply.get("matches")).size() == 1, "E1 tag match must survive a null type_id");
    }

    if (failures > 0) {
      System.out.println("WALKFIX_CTRL_FAILURES=" + failures + " (fix controls NOT satisfied by this Worker build)");
      System.exit(1);
    }
    System.out.println("WALKFIX_CTRL_PASS absent-stays-clean; executed-faults-recorded; errors bounded; no detail leak");
  }
}
