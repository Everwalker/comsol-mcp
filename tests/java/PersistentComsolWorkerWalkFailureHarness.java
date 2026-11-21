package comsol_mcp.worker_java;

import com.comsol.model.ModelEntity;
import com.comsol.model.ModelNode;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import sun.misc.Unsafe;

/**
 * T13 expected-behaviour harness for PersistentComsolWorker.walk(Map)
 * getter failures (2026-11-09, RED by design on the current Worker).
 *
 * This drives the REAL private walk(Map) via reflection with JDK Proxy
 * receivers over the installed com.comsol.model interfaces.  No Worker
 * constructor, no worker service, no socket, no COMSOL process, no engine.
 * It is a software harness, NOT native COMSOL evidence.
 *
 * Contract under test (EXPECTED behaviour, not the current bug):
 * when a node getter (tag / getType / label) throws a COMPLETED ordinary
 * Java exception on the walk thread, the failure must be recorded in the
 * reply's {@code errors} list, because the Python consumer
 * ({@code _g2_engine.find_nodes}) computes
 * {@code complete = data["complete"] and not data["errors"]}: an empty
 * errors list plus complete=true makes a dropped would-be match
 * indistinguishable from a genuinely complete "not found" search.
 *
 * The harness does NOT pin error message text or error codes, and does NOT
 * claim such a field-level failure is (or must be reported as) a transport
 * EXECUTION_STATE_UNKNOWN.  It only requires the error to be retained so
 * the consumer can never see "complete, no errors, no match" for a node
 * whose metadata probe actually faulted.
 *
 * Every scenario runs regardless of earlier assertion failures (failures
 * accumulate), and every scenario prints one machine-parsable RESULT line
 * so the first failure never masks the remaining triggers.
 *
 * Positive controls (P1-P3, C1) assert behaviour that must keep working
 * before and after any fix: successful type/label/tag matching and a
 * genuine non-match stay clean (matches as expected, errors=[]).
 */
public final class PersistentComsolWorkerWalkFailureHarness {
  private static final List<String> invoked = new ArrayList<>();
  private static int failures = 0;
  private static int scenarioCount = 0;

  private static void check(boolean value, String message) {
    if (!value) { failures++; System.out.println("ASSERT-FAIL: " + message); }
  }

  private static int callCount(String getter) {
    int n = 0;
    for (String entry : invoked) if (entry.equals(getter + "/0")) n++;
    return n;
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

  private static Object makeNode(boolean failGetType, boolean failLabel, boolean failTag) {
    return Proxy.newProxyInstance(
        PersistentComsolWorkerWalkFailureHarness.class.getClassLoader(),
        new Class<?>[] {ModelNode.class, ModelEntity.class},
        (proxy, method, args) -> {
          String name = method.getName();
          invoked.add(name + "/" + (args == null ? 0 : args.length));
          if ("getType".equals(name)) {
            if (failGetType) throw new RuntimeException("synthetic completed getType failure (T13 failure harness)");
            return "HeatTransfer";
          }
          if ("label".equals(name)) {
            if (failLabel) throw new RuntimeException("synthetic completed label failure (T13 failure harness)");
            return "Heat Transfer 1";
          }
          if ("tag".equals(name)) {
            if (failTag) throw new RuntimeException("synthetic completed tag failure (T13 failure harness)");
            return "ht1";
          }
          return null;
        });
  }

  @SuppressWarnings("unchecked")
  private static Map<String, Object> walk(Object rootNode, Map<String, Object> query, List<Object> candidates)
      throws Exception {
    Object worker = allocateWithoutConstructor();
    setField(worker, "generation", new AtomicLong(7));
    setField(worker, "connected", Boolean.TRUE);
    Map<String, Object> handles = new ConcurrentHashMap<>();
    handles.put("node", rootNode);
    setField(worker, "handles", handles);
    Method walk = PersistentComsolWorker.class.getDeclaredMethod("walk", Map.class);
    walk.setAccessible(true);
    Map<String, Object> request = new LinkedHashMap<>();
    request.put("handle", "node");
    request.put("generation", Long.valueOf(7));
    request.put("query", query);
    request.put("candidates", candidates);
    request.put("max_nodes", Integer.valueOf(50));
    request.put("max_seconds", Double.valueOf(5.0));
    request.put("skip_visited", Integer.valueOf(0));
    request.put("limit", Integer.valueOf(100));
    Object reply;
    try {
      reply = walk.invoke(worker, request);
    } catch (InvocationTargetException wrapped) {
      Throwable cause = wrapped.getCause();
      if (cause instanceof Exception) throw (Exception) cause;
      if (cause instanceof Error) throw (Error) cause;
      throw wrapped;
    }
    return (Map<String, Object>) reply;
  }

  /** Expected behaviour for the getter-failure scenarios (E1-E4):
   *  the completed getter exception must be retained in the reply errors,
   *  and the reply must not masquerade as a complete error-free empty
   *  search.  No fixed message/code is required. */
  private static void expectGetterFailureSurfaced(String failingGetter, String scenario) {
    check(callCount(failingGetter) >= 1,
        scenario + ": getter " + failingGetter + " was never invoked (handler counter proves injection)");
    List<?> errors = (List<?>) replyOf(scenario).get("errors");
    check(!errors.isEmpty(),
        scenario + ": EXPECTED the completed " + failingGetter + " failure to be retained in reply errors "
        + "(consumer computes complete = complete and not errors), got errors=" + errors
        + " with complete=" + replyOf(scenario).get("complete")
        + " matches=" + replyOf(scenario).get("matches"));
    boolean masquerading = ((List<?>) replyOf(scenario).get("matches")).isEmpty()
        && Boolean.TRUE.equals(replyOf(scenario).get("complete"))
        && ((List<?>) replyOf(scenario).get("errors")).isEmpty();
    check(!masquerading,
        scenario + ": EXPECTED the reply to be distinguishable from a complete empty search, "
        + "got matches=[] complete=true errors=[]");
  }

  // Tiny per-scenario holder so the consumer lambdas can read the reply
  // without repeating the map (the reply is stashed by runScenario usage).
  private static Map<String, Object> currentReply;
  private static Map<String, Object> replyOf(String scenario) { return currentReply; }

  public static void main(String[] args) throws Exception {
    Map<String, Object> qType = new LinkedHashMap<>();  qType.put("type", "HeatTransfer");
    Map<String, Object> qLabel = new LinkedHashMap<>(); qLabel.put("label", "Heat Transfer 1");
    Map<String, Object> qTag = new LinkedHashMap<>();   qTag.put("tag", "ht1");
    Map<String, Object> qCombo = new LinkedHashMap<>(); qCombo.put("tag", "ht1"); qCombo.put("type", "HeatTransfer");
    Map<String, Object> qUnknown = new LinkedHashMap<>(); qUnknown.put("type", "LaminarFlow");
    List<Object> noCandidates = new ArrayList<>();

    // Wrap the runner so the consumer lambdas can see the last reply.
    class Runner {
      void go(String id, Map<String, Object> query, List<Object> cands, Object root,
              java.util.function.Consumer<Map<String, Object>> expect) {
        invoked.clear();
        scenarioCount++;
        System.out.println("SCENARIO id=" + id + " query=" + new TreeMap<>(query) + " candidates=" + cands.size());
        Map<String, Object> reply = null;
        String walkThrew = "-";
        try {
          reply = walk(root, query, cands);
        } catch (Exception exc) {
          walkThrew = exc.getClass().getSimpleName();
          System.out.println("  walk_threw=" + walkThrew + " message=" + exc.getMessage());
        }
        currentReply = reply;
        int getTypeCalls = callCount("getType");
        int labelCalls = callCount("label");
        int tagCalls = callCount("tag");
        if (reply != null) {
          System.out.println("  invoked=" + invoked);
          System.out.println("  reply=" + new TreeMap<>(reply));
        }
        String visits = reply == null ? "-" : String.valueOf(reply.get("visited"));
        int matches = reply == null ? -1 : ((List<?>) reply.get("matches")).size();
        int matched = reply == null ? -1 : ((Number) reply.get("matched_in_call")).intValue();
        String complete = reply == null ? "-" : String.valueOf(reply.get("complete"));
        String truncated = reply == null ? "-" : String.valueOf(reply.get("truncated_reason"));
        int errors = reply == null ? -1 : ((List<?>) reply.get("errors")).size();
        String firstTypeId = "-";
        if (reply != null && matches > 0)
          firstTypeId = String.valueOf(((Map<?, ?>) ((List<?>) reply.get("matches")).get(0)).get("type_id"));
        System.out.println("RESULT id=" + id + " visits=" + visits + " matches=" + matches + " matched=" + matched
            + " complete=" + complete + " truncated=" + truncated + " errors=" + errors
            + " getType_calls=" + getTypeCalls + " label_calls=" + labelCalls + " tag_calls=" + tagCalls
            + " walk_threw=" + walkThrew + " first_type_id=" + firstTypeId);
        if (reply == null) {
          check(false, id + ": walk(Map) threw " + walkThrew + " instead of returning a reply; "
              + "a completed getter failure must not escape the walk as a walk-level exception");
        } else {
          expect.accept(reply);
        }
      }
    }
    Runner run = new Runner();

    // ---- positive controls: must stay green before AND after any fix ----
    run.go("P1", qType, noCandidates, makeNode(false, false, false), r -> {
      check(((List<?>) r.get("matches")).size() == 1, "P1 positive type query: expected exactly 1 match, got " + r.get("matches"));
      check("HeatTransfer".equals(((Map<?, ?>) ((List<?>) r.get("matches")).get(0)).get("type_id")), "P1: matched row type_id wrong");
      check(Boolean.TRUE.equals(r.get("complete")), "P1: expected complete=true");
      check(((List<?>) r.get("errors")).isEmpty(), "P1: expected errors=[] for a clean successful match");
      check(r.get("truncated_reason") == null, "P1: expected truncated_reason=null");
      check(callCount("getType") == 1 && callCount("label") == 1 && callCount("tag") == 1,
          "P1: all three metadata getters must be exercised once (counter)");
    });
    run.go("P2", qLabel, noCandidates, makeNode(false, false, false), r -> {
      check(((List<?>) r.get("matches")).size() == 1, "P2 positive label query: expected exactly 1 match, got " + r.get("matches"));
      check(((List<?>) r.get("errors")).isEmpty(), "P2: expected errors=[]");
      check(Boolean.TRUE.equals(r.get("complete")), "P2: expected complete=true");
    });
    run.go("P3", qTag, noCandidates, makeNode(false, false, false), r -> {
      check(((List<?>) r.get("matches")).size() == 1, "P3 positive tag query: expected exactly 1 match, got " + r.get("matches"));
      check(((List<?>) r.get("errors")).isEmpty(), "P3: expected errors=[]");
      check(Boolean.TRUE.equals(r.get("complete")), "P3: expected complete=true");
    });
    // Genuine non-match: the CLEAN empty-search signature.  C1 proves the
    // E-scenario failures below are about a faulted would-be match, not
    // about query filtering.
    run.go("C1", qUnknown, noCandidates, makeNode(false, false, false), r -> {
      check(((List<?>) r.get("matches")).isEmpty(), "C1 true non-match control: expected matches=[]");
      check(Boolean.TRUE.equals(r.get("complete")), "C1: expected complete=true");
      check(((List<?>) r.get("errors")).isEmpty(), "C1: expected errors=[] for a genuinely absent node");
    });

    // ---- expected behaviour on completed getter failures ----
    run.go("E1", qType, noCandidates, makeNode(true, false, false),
        r -> expectGetterFailureSurfaced("getType", "E1 getType throws on a would-be type match"));
    run.go("E2", qLabel, noCandidates, makeNode(false, true, false),
        r -> expectGetterFailureSurfaced("label", "E2 label throws on a would-be label match"));
    // Root node: the segments tag fallback does not apply at the root.
    run.go("E3", qTag, noCandidates, makeNode(false, false, true),
        r -> expectGetterFailureSurfaced("tag", "E3 tag throws on a would-be tag match (root, no fallback)"));
    // Combo query: tag succeeds but getType faults; the would-be match is
    // still lost, so the error must still surface.
    run.go("E4", qCombo, noCandidates, makeNode(true, false, false),
        r -> expectGetterFailureSurfaced("getType", "E4 combo tag+type query: tag fine, getType throws"));
    // Note: a deep (non-empty candidates) getter-fault scenario is NOT
    // attempted here; every ModelNode zero-arg collection accessor returns
    // a list type whose get(String) checkcast cannot be satisfied by a
    // {ModelNode, ModelEntity} proxy (JDK proxy return-type clash), so that
    // variant needs its own typed-proxy harness and is tracked separately
    // (see the read-only audit's open items).  The four scenarios above
    // inject each of the three getters individually plus a combo query.

    if (scenarioCount != 8) {
      System.out.println("ASSERT-FAIL: only " + scenarioCount + "/8 scenarios executed");
      failures++;
    }
    if (failures > 0) {
      System.out.println("T13_WALK_FAILURE_HARNESS_ASSERTIONS_FAILED failures=" + failures
          + " (expected-behaviour contract NOT satisfied by this Worker build)");
      System.exit(1);
    }
    System.out.println("T13_WALK_FAILURE_HARNESS_PASS getter failures surfaced; positive controls intact");
  }
}
