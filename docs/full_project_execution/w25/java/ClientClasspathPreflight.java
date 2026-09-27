import java.security.CodeSource;

/**
 * Offline linkage probe for the exact W25 client classpath.
 * It loads classes without initialization and never calls a COMSOL API method.
 */
public final class ClientClasspathPreflight {
  private static final String[] REQUIRED_CLASSES = {
      "BlockOtherClientsClient",
      "com.comsol.model.util.ModelUtil",
      "org.eclipse.core.runtime.spi.RegistryStrategy"
  };

  public static void main(String[] args) throws Exception {
    ClassLoader loader = Thread.currentThread().getContextClassLoader();
    for (String name : REQUIRED_CLASSES) {
      Class<?> type = Class.forName(name, false, loader);
      int methodCount = type.getDeclaredMethods().length;
      CodeSource source = type.getProtectionDomain().getCodeSource();
      String location = source == null ? "bootstrap" : source.getLocation().toString();
      System.out.println("CLASS_LOADED\t" + name + "\t" + methodCount + "\t" + location);
    }
  }
}
