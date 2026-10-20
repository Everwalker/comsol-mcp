package comsol_mcp.worker_java;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Pure same-sample fingerprint/schema harness; it never constructs a Worker or calls ModelUtil. */
public final class PersistentComsolWorkerFingerprintComponentsHarness {
  @SuppressWarnings("unchecked")
  private static Map<String, Object> asMap(Object value) {
    if (!(value instanceof Map)) throw new AssertionError("expected object");
    return (Map<String, Object>) value;
  }

  private static void check(boolean value, String message) {
    if (!value) throw new AssertionError(message);
  }

  private static String sha256(String value) throws Exception {
    byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
    StringBuilder output = new StringBuilder();
    for (byte item : digest) output.append(String.format("%02x", item));
    return output.toString();
  }

  @SuppressWarnings("unchecked")
  public static void main(String[] args) throws Exception {
    Map<String, String> sorted = PersistentComsolWorker.parameterExpressionsFromSample(
        new String[]{"zeta", "alpha"}, new String[]{"2[mm]", "0.875"});
    check(sorted.keySet().toString().equals("[alpha, zeta]"), "parameter name order mismatch");
    check("0.875".equals(sorted.get("alpha")), "sampled expression association changed");
    boolean duplicateRejected = false;
    try {
      PersistentComsolWorker.parameterExpressionsFromSample(
          new String[]{"alpha", "alpha"}, new String[]{"0.5", "0.7"});
    } catch (IllegalStateException expected) { duplicateRejected = true; }
    check(duplicateRejected, "duplicate parameter name was accepted");
    boolean lengthMismatchRejected = false;
    try {
      PersistentComsolWorker.parameterExpressionsFromSample(
          new String[]{"alpha"}, new String[]{});
    } catch (IllegalStateException expected) { lengthMismatchRejected = true; }
    check(lengthMismatchRejected, "unpaired parameter sample was accepted");

    Map<String, String> parameters = new LinkedHashMap<>();
    parameters.put("alpha", "0.875");
    parameters.put("zeta", "2[mm]");
    Map<String, Object> result = PersistentComsolWorker.modelFingerprintFromSample(
        "mcp_w25", "W25 owned blank", "/private/tmp/w25.mph", 1791969237281L, parameters);
    Map<String, Object> components = asMap(result.get("fingerprint_components"));

    String legacySource = "mcp_w25\u0000W25 owned blank\u0000/private/tmp/w25.mph\u0000"
        + "1791969237281\u0000alpha=0.875\u0000zeta=2[mm]\u0000";
    check(sha256(legacySource).equals(result.get("fingerprint")), "legacy digest changed");
    check(Integer.valueOf(1).equals(components.get("schema_version")), "schema version mismatch");
    check("mcp_w25".equals(components.get("tag")), "tag mismatch");
    check("W25 owned blank".equals(components.get("label")), "label mismatch");
    check("/private/tmp/w25.mph".equals(components.get("file_path")), "path mismatch");
    check(Long.valueOf(1791969237281L).equals(components.get("time_modified_epoch_ms")), "timeModified must remain long");
    List<Object> rows = (List<Object>) components.get("parameters");
    check(rows.size() == 2, "full parameter count mismatch");
    check("alpha".equals(asMap(rows.get(0)).get("name")), "parameter rows not sorted");
    check("0.875".equals(asMap(rows.get(0)).get("expression")), "parameter expression mismatch");
    check("zeta".equals(asMap(rows.get(1)).get("name")), "parameter row omitted");
    System.out.println("WORKER_FINGERPRINT_COMPONENTS_V1_SOFTWARE_PASS");
  }
}
