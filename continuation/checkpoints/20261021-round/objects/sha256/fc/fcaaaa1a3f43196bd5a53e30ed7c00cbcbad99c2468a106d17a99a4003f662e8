package comsol_mcp.worker_java;

import com.comsol.model.ModelEntity;
import com.comsol.model.physics.Physics;
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

/** Exercises the production Worker call gate and reflection without constructing a Worker. */
public final class PersistentComsolWorkerPhysicsIdentifierHarness {
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

  @SuppressWarnings("unchecked")
  private static Map<String, Object> descriptor(Object target) throws Exception {
    Method describe = PersistentComsolWorker.class.getDeclaredMethod("publicReceiverMetadata", Class.class);
    describe.setAccessible(true);
    return (Map<String, Object>) describe.invoke(null, target.getClass());
  }

  @SuppressWarnings("unchecked")
  private static void checkDescriptor(Object physicsProxy) throws Exception {
    Map<String, Object> descriptor = descriptor(physicsProxy);
    List<Object> methods = (List<Object>) descriptor.get("methods");
    boolean getter = false;
    boolean setter = false;
    for (Object raw : methods) {
      Map<String, Object> row = (Map<String, Object>) raw;
      if (!"com.comsol.model.physics.Physics".equals(row.get("interface"))
          || !"identifier".equals(row.get("method"))) continue;
      List<String> parameters = (List<String>) row.get("parameters");
      if (parameters.isEmpty() && "java.lang.String".equals(row.get("returns"))) getter = true;
      if (parameters.equals(Arrays.asList("java.lang.String"))) setter = true;
    }
    check(getter, "exact Physics.identifier() getter not advertised");
    check(!setter, "deprecated identifier(String) setter was advertised");
  }

  public static void main(String[] args) throws Exception {
    final List<String> invoked = new ArrayList<>();
    Object physics = Proxy.newProxyInstance(Physics.class.getClassLoader(), new Class<?>[] {Physics.class},
        (proxy, method, values) -> {
          if ("identifier".equals(method.getName())) {
            invoked.add(method.toGenericString() + "/" + (values == null ? 0 : values.length));
            return "ewfd";
          }
          return null;
        });
    Object modelEntity = Proxy.newProxyInstance(ModelEntity.class.getClassLoader(),
        new Class<?>[] {ModelEntity.class}, (proxy, method, values) -> null);

    Object worker = allocateWithoutConstructor();
    setField(worker, "generation", new AtomicLong(7));
    setField(worker, "connected", Boolean.TRUE);
    Map<String, Object> handles = new ConcurrentHashMap<>();
    handles.put("physics", physics);
    handles.put("entity", modelEntity);
    setField(worker, "handles", handles);

    checkDescriptor(physics);
    Object observed = invokeCall(worker, "physics", "identifier", new ArrayList<Object>());
    check("ewfd".equals(observed), "actual Physics.identifier() reflection did not return proxy value");
    check(invoked.size() == 1 && invoked.get(0).endsWith("/0"), "no-argument getter dispatch shape changed");

    boolean setterRejected = false;
    try {
      invokeCall(worker, "physics", "identifier", Arrays.<Object>asList("emw"));
    } catch (SecurityException expected) {
      setterRejected = "METHOD_REJECTED".equals(expected.getMessage());
    }
    check(setterRejected, "identifier(String) setter was not rejected by the production Worker handler");
    check(invoked.size() == 1, "setter reached Java reflection before rejection");

    boolean wrongReceiverRejected = false;
    try {
      invokeCall(worker, "entity", "identifier", new ArrayList<Object>());
    } catch (SecurityException expected) {
      wrongReceiverRejected = "METHOD_REJECTED".equals(expected.getMessage());
    }
    check(wrongReceiverRejected, "identifier() on a non-Physics receiver was not rejected");
    check(invoked.size() == 1, "wrong receiver reached Java reflection before rejection");
    System.out.println("W23_R26_WORKER_CALL_HANDLER_PHYSICS_IDENTIFIER_PASS Worker_constructor=NOT_RUN COMSOL=NOT_RUN");
  }
}
