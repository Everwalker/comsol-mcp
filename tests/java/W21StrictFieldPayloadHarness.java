package comsol_mcp.worker_java;

import java.util.Map;

/** Offline execution proof for the production Worker strict-field payload gate. */
public final class W21StrictFieldPayloadHarness {
  private static void require(boolean condition, String message) {
    if (!condition) throw new AssertionError(message);
  }

  private static void expectCode(String expected, Runnable action) {
    try {
      action.run();
    } catch (PersistentComsolWorker.WorkerFailure failure) {
      require(expected.equals(failure.code), "expected " + expected + " but received " + failure.code);
      return;
    }
    throw new AssertionError("expected WorkerFailure " + expected);
  }

  private static double[][][] real(int points) {
    return new double[][][] { new double[][] { new double[points] } };
  }

  private static double[][] coordinates(int dimensions, int points) {
    return new double[dimensions][points];
  }

  public static void main(String[] args) {
    Map<String, Object> atLimit = PersistentComsolWorker.strictFieldPayload(
        real(16_384), null, coordinates(3, 16_384), false, 65_536L, 8 * 1024 * 1024);
    require(Long.valueOf(65_536L).equals(atLimit.get("numeric_scalar_count")),
        "combined field plus coordinate count must admit exactly 65,536 scalars");
    require(((Number) atLimit.get("json_payload_bytes")).longValue() <= 8L * 1024L * 1024L,
        "strict payload JSON must fit the byte cap");

    expectCode("FIELD_READBACK_LIMIT_EXCEEDED", () ->
        PersistentComsolWorker.strictFieldPayload(real(16_385), null, coordinates(3, 16_385),
            false, 65_536L, 8 * 1024 * 1024));
    expectCode("FIELD_READBACK_LIMIT_EXCEEDED", () ->
        PersistentComsolWorker.strictFieldPayload(real(1), null, coordinates(1, 1),
            false, 65_536L, 16));

    double[][][] nonfinite = real(1);
    nonfinite[0][0][0] = Double.NaN;
    expectCode("FIELD_READBACK_INVALID_NUMERIC", () ->
        PersistentComsolWorker.strictFieldPayload(nonfinite, null, coordinates(1, 1),
            false, 65_536L, 8 * 1024 * 1024));

    expectCode("FIELD_READBACK_INVALID_SHAPE", () ->
        PersistentComsolWorker.strictFieldPayload(real(2), null, coordinates(2, 1),
            false, 65_536L, 8 * 1024 * 1024));

    double[][][] complexReal = real(8);
    double[][][] complexImag = real(8);
    Map<String, Object> complex = PersistentComsolWorker.strictFieldPayload(
        complexReal, complexImag, coordinates(3, 8), true, 65_536L, 8 * 1024 * 1024);
    require(Long.valueOf(40L).equals(complex.get("numeric_scalar_count")),
        "complex readback must count both real and imaginary values plus coordinates");
    System.out.println("W21_STRICT_FIELD_PAYLOAD_PASS");
  }
}
