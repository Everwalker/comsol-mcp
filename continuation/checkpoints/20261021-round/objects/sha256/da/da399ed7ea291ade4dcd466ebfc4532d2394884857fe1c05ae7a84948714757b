package comsol_mcp.worker_java;

import com.comsol.model.ModelEntity;
import com.comsol.model.PropFeatureList;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.List;
import java.util.Map;
import sun.misc.Unsafe;

/** Pure receiver metadata and typed reflection; no Worker constructor or ModelUtil call. */
public final class PersistentComsolWorkerPublicApiHarness {
  private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
  @SuppressWarnings("unchecked")
  private static Map<String,Object> descriptor(Class<?> iface) throws Exception {
    Object proxy = Proxy.newProxyInstance(iface.getClassLoader(), new Class<?>[]{iface}, (p,m,a) -> null);
    Method describe = PersistentComsolWorker.class.getDeclaredMethod("publicReceiverMetadata", Class.class);
    describe.setAccessible(true);
    return (Map<String,Object>)describe.invoke(null, proxy.getClass());
  }
  @SuppressWarnings("unchecked")
  private static boolean signature(Map<String,Object> descriptor, String owner, String name, String parameters, String returns) {
    for (Object row : (List<Object>)descriptor.get("methods")) {
      Map<String,Object> method = (Map<String,Object>)row;
      if (owner.equals(method.get("interface")) && name.equals(method.get("method"))
          && parameters.equals(method.get("parameters").toString()) && returns.equals(method.get("returns"))) return true;
    }
    return false;
  }
  public static final class Overloads {
    public String comments(String text) { return "String:" + text; }
    public String comments(Object value) { return "Object:" + value; }
    public String active(boolean value) { return "boolean:" + value; }
    public String move(String tag, int index) { return "int:" + tag + ":" + index; }
  }
  private static Object unconstructedWorker() throws Exception {
    Field f=Unsafe.class.getDeclaredField("theUnsafe");f.setAccessible(true);
    return ((Unsafe)f.get(null)).allocateInstance(PersistentComsolWorker.class);
  }
  private static Object invoke(Object worker, String name, String json) throws Exception {
    Method method=PersistentComsolWorker.class.getDeclaredMethod("invoke",Object.class,Class.class,String.class,List.class);
    method.setAccessible(true);
    return method.invoke(worker,new Overloads(),Overloads.class,name,PersistentComsolWorker.Json.parse(json));
  }
  public static void main(String[] args) throws Exception {
    Map<String,Object> entity=descriptor(ModelEntity.class);
    check(signature(entity,"com.comsol.model.ModelEntity","comments","[]","java.lang.String"),"actual inherited comments getter");
    check(signature(entity,"com.comsol.model.ModelEntity","comments","[java.lang.String]","com.comsol.model.ModelEntity"),"actual inherited comments setter");
    check(signature(entity,"com.comsol.model.PrimitiveModelEntity","resolveModelPath","[]","java.lang.String"),"actual primitive identity getter");
    Map<String,Object> list=descriptor(PropFeatureList.class);
    check(signature(list,"com.comsol.model.PropFeatureList","create","[java.lang.String, java.lang.String]","com.comsol.model.PropFeature"),"actual typed list create");
    check(!entity.toString().contains("getClass"),"Object methods absent");
    check(!entity.toString().contains("ClassLoader"),"classloader absent");
    Object worker=unconstructedWorker();
    check("String:text".equals(invoke(worker,"comments","[{\"kind\":\"string\",\"shape\":[],\"data\":\"text\",\"java_signature\":\"java.lang.String\"}]")),"exact typed overload");
    check("boolean:false".equals(invoke(worker,"active","[{\"kind\":\"boolean\",\"shape\":[],\"data\":false,\"java_signature\":\"boolean\"}]")),"typed boolean preserves false");
    check("int:a:2".equals(invoke(worker,"move","[\"a\",{\"kind\":\"int32\",\"shape\":[],\"data\":2,\"java_signature\":\"int\"}]")),"typed move index");
    System.out.println("PUBLIC_RECEIVER_EXACT_SIGNATURE_SOFTWARE_PASS");
  }
}
