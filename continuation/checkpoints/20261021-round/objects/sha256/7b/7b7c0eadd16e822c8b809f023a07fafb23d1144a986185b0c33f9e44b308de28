package comsol_mcp.worker_java;

import com.comsol.model.ModelEntityList;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import sun.misc.Unsafe;

/** Exact typed receiver and dispatcher controls; no Worker constructor or COMSOL model is started. */
public final class PersistentComsolWorkerModelEntityListIndexHarness {
  private static void check(boolean value, String message) {
    if (!value) throw new AssertionError(message);
  }

  private static Object allocateWithoutConstructor() throws Exception {
    Field field = Unsafe.class.getDeclaredField("theUnsafe");
    field.setAccessible(true);
    return ((Unsafe) field.get(null)).allocateInstance(PersistentComsolWorker.class);
  }

  private static void set(Object target, String name, Object value) throws Exception {
    Field field = PersistentComsolWorker.class.getDeclaredField(name);
    field.setAccessible(true);
    field.set(target, value);
  }

  @SuppressWarnings("unchecked")
  private static Map<String, Object> descriptor(Object receiver) throws Exception {
    Method method = PersistentComsolWorker.class.getDeclaredMethod("publicReceiverMetadata", Object.class);
    method.setAccessible(true);
    return (Map<String, Object>) method.invoke(null, receiver);
  }

  @SuppressWarnings("unchecked")
  private static boolean hasIndexDescriptor(Map<String, Object> descriptor) {
    for (Object raw : (List<Object>) descriptor.get("methods")) {
      Map<String, Object> row = (Map<String, Object>) raw;
      if ("com.comsol.model.ModelEntityList".equals(row.get("interface"))
          && "index".equals(row.get("method"))
          && Arrays.asList("java.lang.String").equals(row.get("parameters"))
          && "int".equals(row.get("returns"))) return true;
    }
    return false;
  }

  @SuppressWarnings("unchecked")
  private static Object call(Object worker, String handle, List<?> args) throws Exception {
    Method method = PersistentComsolWorker.class.getDeclaredMethod("call", Map.class);
    method.setAccessible(true);
    Map<String, Object> request = new HashMap<>();
    request.put("generation", 7L);
    request.put("handle", handle);
    request.put("method", "index");
    request.put("args", args);
    try {
      return method.invoke(worker, request);
    } catch (java.lang.reflect.InvocationTargetException failure) {
      Throwable cause = failure.getCause();
      if (cause instanceof Exception) throw (Exception) cause;
      throw failure;
    }
  }

  private static Object proxy(Class<?> iface, InvocationHandler handler) {
    return Proxy.newProxyInstance(iface.getClassLoader(), new Class<?>[]{iface}, handler);
  }

  public interface ForeignStringIndex { int index(String tag); }
  public interface ForeignBoxedIndex { Integer index(String tag); }

  private static void rejected(Object worker, String handle, List<?> args, String label) throws Exception {
    try {
      call(worker, handle, args);
      throw new AssertionError(label + " unexpectedly dispatched");
    } catch (SecurityException expected) {
      check("METHOD_REJECTED".equals(expected.getMessage()), label + " used the wrong refusal");
    }
  }

  @SuppressWarnings("unchecked")
  public static void main(String[] args) throws Exception {
    final Map<String, Integer> indices = new HashMap<>();
    indices.put("zeta", Integer.valueOf(1));
    indices.put("alpha", Integer.valueOf(2));
    indices.put("middle", Integer.valueOf(0));
    Object list = proxy(ModelEntityList.class, (receiver, method, values) -> {
      if ("index".equals(method.getName()) && values != null && values.length == 1)
        return indices.get(values[0]);
      if ("tags".equals(method.getName())) return new String[]{"zeta", "alpha", "middle"};
      if (method.getReturnType() == boolean.class) return Boolean.FALSE;
      if (method.getReturnType() == int.class) return Integer.valueOf(0);
      if (method.getReturnType() == long.class) return Long.valueOf(0L);
      return null;
    });

    Map<String, Object> publicView = descriptor(list);
    check(hasIndexDescriptor(publicView), "actual ModelEntityList receiver did not expose exact primitive-int descriptor");

    Field methodsField = PersistentComsolWorker.class.getDeclaredField("METHODS");
    methodsField.setAccessible(true);
    Field d2Field = PersistentComsolWorker.class.getDeclaredField("D2_METADATA_METHODS");
    d2Field.setAccessible(true);
    check(!((Set<String>) methodsField.get(null)).contains("index"), "index was added to the global METHODS set");
    check(!((Set<String>) d2Field.get(null)).contains("index"), "index was added to the generic D2 metadata set");

    Object foreign = proxy(ForeignStringIndex.class, (receiver, method, values) -> Integer.valueOf(4));
    Object boxed = proxy(ForeignBoxedIndex.class, (receiver, method, values) -> Integer.valueOf(4));
    check(!hasIndexDescriptor(descriptor(foreign)), "foreign receiver advertised index");
    check(!hasIndexDescriptor(descriptor(boxed)), "boxed or foreign descriptor advertised index");

    Object worker = allocateWithoutConstructor();
    Map<String, Object> handles = new ConcurrentHashMap<>();
    handles.put("solver-feature-list", list);
    handles.put("foreign-list", foreign);
    handles.put("boxed-list", boxed);
    handles.put("wrong-object", new Object());
    set(worker, "generation", new AtomicLong(7L));
    set(worker, "handles", handles);
    set(worker, "connected", Boolean.TRUE);
    check(Integer.valueOf(1).equals(call(worker, "solver-feature-list", Collections.singletonList("zeta"))),
        "valid typed dispatcher call did not preserve native integer index");
    check(Integer.valueOf(2).equals(call(worker, "solver-feature-list", Collections.singletonList("alpha"))),
        "nonalphabetical tag index was not read through the typed receiver");
    rejected(worker, "foreign-list", Collections.singletonList("zeta"), "foreign receiver");
    rejected(worker, "boxed-list", Collections.singletonList("zeta"), "wrong signature receiver");
    rejected(worker, "wrong-object", Collections.singletonList("zeta"), "non-ModelEntityList receiver");
    rejected(worker, "solver-feature-list", Collections.emptyList(), "missing argument");
    rejected(worker, "solver-feature-list", Arrays.asList("zeta", "alpha"), "extra argument");
    rejected(worker, "solver-feature-list", Collections.singletonList(Integer.valueOf(2)), "non-String argument");
    System.out.println("MODEL_ENTITY_LIST_INDEX_TYPED_RECEIVER_DISPATCH_SOFTWARE_PASS");
  }
}
