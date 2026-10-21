package comsol_mcp.worker_java;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.List;
import java.util.Map;
import sun.misc.Unsafe;

/** Regression against the actual compiled production Json parser and overload dispatcher. */
public final class PersistentComsolWorkerJsonNumberHarness {
  private static void check(boolean condition, String message) {
    if (!condition) throw new AssertionError(message);
  }

  @SuppressWarnings("unchecked")
  private static List<Object> list(Object value) { return (List<Object>) value; }

  @SuppressWarnings("unchecked")
  private static Map<String,Object> map(Object value) { return (Map<String,Object>) value; }

  private static PersistentComsolWorker unconstructedWorker() throws Exception {
    Field field = Unsafe.class.getDeclaredField("theUnsafe");
    field.setAccessible(true);
    return (PersistentComsolWorker) ((Unsafe) field.get(null)).allocateInstance(PersistentComsolWorker.class);
  }

  private static String dispatch(Object worker, List<Object> args) throws Exception {
    Method invoke = PersistentComsolWorker.class.getDeclaredMethod(
        "invoke", Object.class, Class.class, String.class, List.class);
    invoke.setAccessible(true);
    return (String) invoke.invoke(worker, null, OverloadFixture.class, "getISol", args);
  }

  public static final class OverloadFixture {
    public static String getISol(int a, int b) { return "int-int:" + a + ":" + b; }
    public static String getISol(int a, double b) { return "int-double:" + a + ":" + b; }
  }

  public static void main(String[] args) throws Exception {
    Object low = PersistentComsolWorker.Json.parse("-9223372036854775808");
    Object high = PersistentComsolWorker.Json.parse("9223372036854775807");
    Object precise = PersistentComsolWorker.Json.parse("9007199254740993");
    check(low instanceof Long && ((Long) low).longValue() == Long.MIN_VALUE, "Long.MIN_VALUE must parse exactly");
    check(high instanceof Long && ((Long) high).longValue() == Long.MAX_VALUE, "Long.MAX_VALUE must parse exactly");
    check(precise instanceof Long && ((Long) precise).longValue() == 9007199254740993L,
        "integer above 2^53 must remain exact Long");

    Object nested = PersistentComsolWorker.Json.parse(
        "{\"outer\":{\"values\":[9007199254740993,-9007199254740993,9223372036854775807,-9223372036854775808]}}");
    List<Object> values = list(map(map(nested).get("outer")).get("values"));
    check(values.size() == 4, "nested integer list length");
    check(values.get(0) instanceof Long && ((Long) values.get(0)) == 9007199254740993L, "nested >2^53 integer");
    check(values.get(1) instanceof Long && ((Long) values.get(1)) == -9007199254740993L, "nested negative >2^53 integer");
    check(values.get(2) instanceof Long && ((Long) values.get(2)) == Long.MAX_VALUE, "nested upper endpoint");
    check(values.get(3) instanceof Long && ((Long) values.get(3)) == Long.MIN_VALUE, "nested lower endpoint");
    String serialized = PersistentComsolWorker.Json.write(nested);
    Object roundTrip = PersistentComsolWorker.Json.parse(serialized);
    List<Object> roundTripValues = list(map(map(roundTrip).get("outer")).get("values"));
    check(roundTripValues.equals(values), "integer JSON write/parse round-trip values");
    for (Object value : roundTripValues) check(value instanceof Long, "integer JSON write/parse round-trip types");

    check(PersistentComsolWorker.Json.parse("1.0") instanceof Double, "decimal lexeme must remain Double");
    check(PersistentComsolWorker.Json.parse("1e0") instanceof Double, "exponent lexeme must remain Double");
    try {
      PersistentComsolWorker.Json.parse("9223372036854775808");
      throw new AssertionError("integer overflow must be refused");
    } catch (IllegalArgumentException expected) { }

    Object worker = unconstructedWorker();
    check("int-int:1:5".equals(dispatch(worker, list(PersistentComsolWorker.Json.parse("[1,5]")))),
        "integer getISol fixture must select int,int overload");
    check("int-double:1:5.0".equals(dispatch(worker, list(PersistentComsolWorker.Json.parse("[1.0,5.0]")))),
        "decimal getISol fixture must select int,double overload");
    check("int-double:1:5.0".equals(dispatch(worker, list(PersistentComsolWorker.Json.parse("[1e0,5e0]")))),
        "exponent getISol fixture must select int,double overload");
    System.out.println("PARSER_NUM_INTEGER_PRESERVATION_PASS");
  }
}
