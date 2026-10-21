import com.comsol.model.Model;
import com.comsol.model.MeshSequence;
import com.comsol.model.util.ModelUtil;
import java.nio.file.Files;
import java.nio.file.Path;

/** Test preparation only: a refined copy of the accepted analytic W02 model.
 * Not a registered MCP capability and not evidence of MCP execution.
 * Run exclusively while the managed engine queue is idle.
 * API source: COMSOL 6.4 Application Programming Guide, Mesh, chunk 16484;
 * source SHA256 6558506a313e2cb8ac7b9c6ec129bc3acf37281b38e2f96bdb8b90ac91bff97d.
 */
public final class Phase2Fixture {
  public static void main(String[] args) {
    int exit = 1;
    Model model = null;
    boolean connected = false;
    try {
      if (args.length != 5) throw new IllegalArgumentException("host port source destination hmax");
      Path source = Path.of(args[2]).toRealPath();
      Path destination = Path.of(args[3]).toAbsolutePath();
      if (Files.exists(destination) || source.equals(destination))
        throw new IllegalArgumentException("destination must be a new fixture file");
      double hmax = Double.parseDouble(args[4]);
      if (hmax < .006 || hmax > .05) throw new IllegalArgumentException("fixture size outside bounded range");
      ModelUtil.connect(args[0], Integer.parseInt(args[1]));
      connected = true;
      model = ModelUtil.load(ModelUtil.uniquetag("phase2fixture"), source.toString());
      MeshSequence mesh = model.component("comp1").mesh("mesh1");
      mesh.automatic(false);
      mesh.feature("size").set("custom", "on");
      mesh.feature("size").set("hmax", Double.toString(hmax));
      mesh.feature("size").set("hmin", Double.toString(hmax / 2));
      mesh.feature("size").set("hgrad", "1.2");
      long start = System.nanoTime();
      mesh.run();
      int elements = mesh.getNumElem();
      if (elements > 160000) throw new IllegalStateException("element budget exceeded: " + elements);
      model.save(destination.toString(), true);
      System.out.println("PHASE2_FIXTURE engine=" + ModelUtil.getComsolVersion()
          + " elements=" + elements + " vertices=" + mesh.getNumVertex()
          + " hmax=" + hmax + " mesh_and_save_seconds=" + (System.nanoTime() - start) / 1e9
          + " solve_performed=false");
      exit = 0;
    } catch (Throwable failure) {
      failure.printStackTrace(System.err);
    } finally {
      try { if (model != null) ModelUtil.remove(model.tag()); }
      catch (Throwable failure) { failure.printStackTrace(System.err); exit = 1; }
      try { if (connected) ModelUtil.disconnect(); }
      catch (Throwable failure) { failure.printStackTrace(System.err); exit = 1; }
    }
    System.exit(exit); // COMSOL client pools must not retain this preparation JVM.
  }
}
