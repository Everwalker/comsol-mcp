package comsol_mcp.worker_java;

import com.comsol.model.LocalSelection;
import com.comsol.model.ModelEntity;
import com.comsol.model.Pair;
import com.comsol.model.Selection;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import sun.misc.Unsafe;

/**
 * T21 review fix (F4): exercises the production Worker call() dispatcher and
 * its validatePairSideGetterArguments/validateInputDimensionArguments gates
 * without constructing a Worker or starting COMSOL.  Receivers are JDK
 * Proxies over the real com.comsol.model interfaces from the installed API
 * jar; the reflective-call counters are the negative control: a rejected
 * dispatch must never reach the proxy handler.
 */
public final class PersistentComsolWorkerPairSideSelectionHarness {
  private static void check(boolean value, String message) {
    if (!value) throw new AssertionError(message);
  }

  private static Object allocateWithoutConstructor() throws Exception {
    Field field = Unsafe.class.getDeclaredField("theUnsafe");
    field.setAccessible(true);
    return ((Unsafe) field.get(null)).allocateInstance(PersistentComsolWorker.class);
  }

  private static void setField(Object target, String name, Object value) throws Exception {
    Field field = PersistentComsolWorker.class.getDeclaredField(name);
    field.setAccessible(true);
    field.set(target, value);
  }

  private static Object invokeCall(Object worker, String handle, String method, List<Object> args)
      throws Exception {
    Method call = PersistentComsolWorker.class.getDeclaredMethod("call", Map.class);
    call.setAccessible(true);
    Map<String, Object> request = new HashMap<>();
    request.put("generation", Long.valueOf(7));
    request.put("handle", handle);
    request.put("method", method);
    request.put("args", args);
    try {
      return call.invoke(worker, request);
    } catch (InvocationTargetException wrapped) {
      Throwable cause = wrapped.getCause();
      if (cause instanceof Exception) throw (Exception) cause;
      if (cause instanceof Error) throw (Error) cause;
      throw wrapped;
    }
  }

  private static boolean rejected(RunnableAttempt attempt, List<String> invoked, int expectedCalls)
      throws Exception {
    try {
      attempt.run();
      return false;
    } catch (SecurityException expected) {
      check("METHOD_REJECTED".equals(expected.getMessage()), "gate threw SecurityException without METHOD_REJECTED: "
          + expected.getMessage());
      check(invoked.size() == expectedCalls, "rejected dispatch reached reflective invocation (count "
          + invoked.size() + " != " + expectedCalls + ")");
      return true;
    }
  }

  private interface RunnableAttempt { void run() throws Exception; }

  public static void main(String[] args) throws Exception {
    final List<String> invoked = new ArrayList<>();
    final LocalSelection side = (LocalSelection) Proxy.newProxyInstance(
        LocalSelection.class.getClassLoader(), new Class<?>[] {LocalSelection.class},
        (proxy, method, values) -> {
          invoked.add(method.getName() + "/" + (values == null ? 0 : values.length));
          if ("inputDimension".equals(method.getName())) return Integer.valueOf(2);
          return null;
        });
    Object pair = Proxy.newProxyInstance(Pair.class.getClassLoader(), new Class<?>[] {Pair.class},
        (proxy, method, values) -> {
          String name = method.getName();
          invoked.add(name + "/" + (values == null ? 0 : values.length));
          if ("source".equals(name) || "destination".equals(name)) return side;
          return null;
        });
    Object entity = Proxy.newProxyInstance(ModelEntity.class.getClassLoader(),
        new Class<?>[] {ModelEntity.class}, (proxy, method, values) -> null);

    Object worker = allocateWithoutConstructor();
    setField(worker, "generation", new AtomicLong(7));
    setField(worker, "connected", Boolean.TRUE);
    Map<String, Object> handles = new ConcurrentHashMap<>();
    handles.put("pair", pair);
    handles.put("selection", side);
    handles.put("entity", entity);
    setField(worker, "handles", handles);

    // Positive: zero-argument Pair.source()/destination() dispatch through the
    // production gate and return the endpoint object.
    Object src = invokeCall(worker, "pair", "source", new ArrayList<Object>());
    check(src == side, "Pair.source() did not return the endpoint selection");
    check(invoked.contains("source/0"), "Pair.source() dispatch shape changed");
    Object dst = invokeCall(worker, "pair", "destination", new ArrayList<Object>());
    check(dst == side, "Pair.destination() did not return the endpoint selection");
    check(invoked.contains("destination/0"), "Pair.destination() dispatch shape changed");

    // Positive: zero-argument Selection.inputDimension() on a Selection
    // receiver returns the documented int.
    Object dim = invokeCall(worker, "selection", "inputDimension", new ArrayList<Object>());
    check(Integer.valueOf(2).equals(dim), "Selection.inputDimension() did not return the proxy value");
    check(invoked.contains("inputDimension/0"), "inputDimension dispatch shape changed");
    int baseline = invoked.size();

    // Negative controls: none of these may reach the proxy handler.
    check(rejected(() -> invokeCall(worker, "pair", "source", Arrays.<Object>asList("x")), invoked, baseline),
        "source(String) with an argument was not rejected on a Pair receiver");
    check(rejected(() -> invokeCall(worker, "entity", "source", new ArrayList<Object>()), invoked, baseline),
        "source() on a non-Pair receiver was not rejected");
    check(rejected(() -> invokeCall(worker, "selection", "destination", new ArrayList<Object>()), invoked, baseline),
        "destination() on a Selection (non-Pair) receiver was not rejected");
    check(rejected(() -> invokeCall(worker, "pair", "inputDimension", new ArrayList<Object>()), invoked, baseline),
        "inputDimension was widened to the Pair receiver");
    check(rejected(() -> invokeCall(worker, "entity", "inputDimension", new ArrayList<Object>()), invoked, baseline),
        "inputDimension on a non-Selection receiver was not rejected");
    check(rejected(() -> invokeCall(worker, "selection", "inputDimension", Arrays.<Object>asList(2)), invoked, baseline),
        "inputDimension(int) with an argument was not rejected on a Selection receiver");
    check(invoked.size() == baseline, "a rejected dispatch reached reflection");

    // The validator entry points refuse to be reused for unrelated names.
    boolean misuseRejected = false;
    try {
      PersistentComsolWorker.validatePairSideGetterArguments(pair, "named", new ArrayList<Object>());
    } catch (IllegalArgumentException expected) {
      misuseRejected = true;
    }
    check(misuseRejected, "validatePairSideGetterArguments accepted an unrelated method name");

    System.out.println("T21_PAIR_SIDE_GATE_HARNESS_PASS Worker_constructor=NOT_RUN COMSOL=NOT_RUN "
        + "accepted=3 rejected=7");
  }
}
