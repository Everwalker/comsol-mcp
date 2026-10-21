import com.comsol.model.Model;
import com.comsol.model.util.ModelUtil;

/**
 * Bounded T011 supplemental probe. It is an independent official Java API
 * client, intentionally mutating only the explicitly named test model/tag and
 * scalar parameter supplied by the production evidence driver. It never saves,
 * removes a model, or controls the server lifecycle.
 */
public final class Phase2ExternalMutation {
  public static void main(String[] args) {
    boolean connected = false;
    try {
      if (args.length != 5) throw new IllegalArgumentException("host port model_tag parameter expression required");
      String host = args[0], tag = args[2], parameter = args[3], expression = args[4];
      if (!"127.0.0.1".equals(host) || tag.isEmpty() || parameter.isEmpty() || expression.isEmpty())
        throw new IllegalArgumentException("bounded loopback target and nonempty tag/parameter/expression required");
      int port = Integer.parseInt(args[1]);
      ModelUtil.connect(host, port);
      connected = true;
      Model model = ModelUtil.model(tag);
      model.param().set(parameter, expression);
      System.out.println("PHASE2_EXTERNAL_MUTATION tag=" + tag + " parameter=" + parameter + " expression=" + expression);
    } catch (Throwable failure) {
      failure.printStackTrace(System.err);
      System.exit(1);
    } finally {
      if (connected) {
        try { ModelUtil.disconnect(); }
        catch (Throwable failure) { failure.printStackTrace(System.err); System.exit(1); }
      }
    }
  }
}
