package comsol_mcp.worker_java;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.BindException;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;

/** Real production constructor/ready publisher, with no COMSOL connection or call. */
public final class PersistentComsolWorkerFixedListenerHarness {
  private static final String TOKEN = "SOFTWARE_ONLY_FIXED_LISTENER_HARNESS_TOKEN";

  private static void check(boolean condition, String message) {
    if (!condition) throw new AssertionError(message);
  }

  private static Object field(Object instance, String name) throws Exception {
    Field field = PersistentComsolWorker.class.getDeclaredField(name);
    field.setAccessible(true);
    return field.get(instance);
  }

  private static Object construct(int port, Path root) throws Exception {
    Constructor<?> constructor = PersistentComsolWorker.class.getDeclaredConstructor(
        String.class, int.class, long.class, Path.class);
    constructor.setAccessible(true);
    return constructor.newInstance(TOKEN, port, 1L, root.resolve("locks"));
  }

  private static Map<String, Object> exercise(int requestedPort, Path root) throws Exception {
    Files.createDirectory(root);
    Object worker = construct(requestedPort, root);
    ServerSocket original = (ServerSocket) field(worker, "server");
    try {
      int actualPort = original.getLocalPort();
      InetAddress address = original.getInetAddress();
      Map<String, Object> row = new LinkedHashMap<>();
      row.put("requested_port", requestedPort);
      row.put("actual_host", address.getHostAddress());
      row.put("actual_port", actualPort);
      row.put("default_loopback_host", InetAddress.getLoopbackAddress().getHostAddress());
      System.out.println("ACTUAL_BIND " + PersistentComsolWorker.Json.write(row));
      check(address instanceof Inet4Address && "127.0.0.1".equals(address.getHostAddress()),
          "production Worker must actually bind explicit IPv4 loopback");
      check(actualPort > 0 && (requestedPort == 0 || requestedPort == actualPort),
          "fixed port must be exact; ordinary port0 must be resolved by the original socket");

      Path endpoint = root.resolve("ready.json");
      Method publish = PersistentComsolWorker.class.getDeclaredMethod("publishEndpoint", Path.class);
      publish.setAccessible(true);
      publish.invoke(worker, endpoint);
      @SuppressWarnings("unchecked")
      Map<String, Object> ready = (Map<String, Object>) PersistentComsolWorker.Json.parse(
          new String(Files.readAllBytes(endpoint), StandardCharsets.UTF_8));
      check(address.getHostAddress().equals(ready.get("host")), "ready host must describe actual bind");
      check(((Number) ready.get("port")).intValue() == actualPort, "ready port must describe actual bind");
      check(((Number) ready.get("pid")).longValue() == ProcessHandle.current().pid(), "ready pid");
      check(((Number) ready.get("process_start_epoch_ms")).longValue() > 0, "ready process birth");
      check(((Number) ready.get("generation")).longValue() == 1L && TOKEN.equals(ready.get("token")), "ready identity");
      check(ready.get("instance_id") instanceof String && !((String) ready.get("instance_id")).isEmpty(), "ready instance");
      try (Socket client = new Socket()) {
        client.connect(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), actualPort), 2000);
        original.setSoTimeout(2000);
        try (Socket accepted = original.accept()) {
          check(accepted.getLocalAddress() instanceof Inet4Address, "actual accepted IPv4 listener");
          check(accepted.getLocalPort() == actualPort, "actual accepted port");
        }
      }
      row.put("ready_host", ready.get("host"));
      row.put("ready_port", ready.get("port"));
      row.put("ready_pid", ready.get("pid"));
      row.put("ready_birth", ready.get("process_start_epoch_ms"));
      row.put("ready_generation", ready.get("generation"));
      row.put("ready_token_matches", true);
      row.put("actual_tcp_accept", true);
      return row;
    } finally {
      original.close();
      ((ExecutorService) field(worker, "engine")).shutdownNow();
      ((ExecutorService) field(worker, "sockets")).shutdownNow();
      Files.deleteIfExists((Path) field(worker, "codeRoot"));
      check(original.isClosed(), "original socket cleanup");
    }
  }

  public static void main(String[] args) throws Exception {
    check(args.length == 1, "one new private root required");
    Path root = Paths.get(args[0]);
    Files.createDirectory(root);
    List<Map<String, Object>> rows = new ArrayList<>();
    rows.add(exercise(0, root.resolve("ordinary-dynamic")));
    InetAddress ipv4 = InetAddress.getByName("127.0.0.1");
    int fixed;
    try (ServerSocket reserved = new ServerSocket(0, 1, ipv4)) {
      fixed = reserved.getLocalPort();
    }
    // This exact number is attempted once; collision is an error, never next-free fallback.
    rows.add(exercise(fixed, root.resolve("explicit-fixed")));
    try (ServerSocket occupied = new ServerSocket(0, 1, ipv4)) {
      try {
        Object unexpected = construct(occupied.getLocalPort(), root);
        ((ServerSocket) field(unexpected, "server")).close();
        throw new AssertionError("occupied fixed port must not silently choose another port");
      } catch (InvocationTargetException expected) {
        check(expected.getCause() instanceof BindException, "occupied fixed listener must fail with BindException");
      }
    }
    Map<String, Object> result = new LinkedHashMap<>();
    result.put("scope", "REAL_JAVA_LOOPBACK_SOFTWARE_ONLY_NOT_T037_NATIVE");
    result.put("cases", rows);
    result.put("occupied_fixed_port_refused", true);
    result.put("COMSOL_Server_or_GUI_started", false);
    result.put("COMSOL_calls", 0);
    result.put("native_admission", "NOT_RUN");
    System.out.println("FIXED_LISTENER_SOFTWARE_PASS " + PersistentComsolWorker.Json.write(result));
  }
}
