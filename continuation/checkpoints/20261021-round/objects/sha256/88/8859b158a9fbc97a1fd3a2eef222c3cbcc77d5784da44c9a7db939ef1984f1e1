package comsol_mcp.worker_java;

import java.util.Arrays;
import java.util.Collections;

/** Exercises the exact argument validator called by PersistentComsolWorker.call. */
public final class W21MeshBlockArgumentsHarness {
  private interface Check { void run() throws Exception; }

  private static void rejects(Check check) throws Exception {
    try {
      check.run();
    } catch (IllegalArgumentException expected) {
      return;
    }
    throw new AssertionError("expected bounded mesh block arguments to be rejected");
  }

  public static void main(String[] args) throws Exception {
    PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(0, 1024));
    PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(2147482623L, 1024L));
    PersistentComsolWorker.validateMeshBlockArguments("getElem", Arrays.asList("tet", 12, 16));
    PersistentComsolWorker.validateMeshBlockArguments("getElemEntity", Arrays.asList("tet", 0, 1));

    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Collections.emptyList()));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(0, 1025)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(-1, 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(2147483648L, 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(2147483640L, 16)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getElem", Arrays.asList("tet", 0, 0)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getElem", Arrays.asList("", 0, 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getElemEntity", Arrays.asList("tet", 0)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getTypes", Collections.emptyList()));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", null));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(true, 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(0, false)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(0.5, 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(0, 1.5)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(Double.NaN, 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(0, Double.POSITIVE_INFINITY)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList(Long.MAX_VALUE, 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getVertex", Arrays.asList("0", 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getElem", Arrays.asList(null, 0, 1)));
    rejects(() -> PersistentComsolWorker.validateMeshBlockArguments("getElemEntity", Arrays.asList("tet", 0, 1, 2)));

    System.out.println("W21_MESH_BLOCK_ARGUMENTS_PASS: 4 accepted, 20 rejected");
  }
}
