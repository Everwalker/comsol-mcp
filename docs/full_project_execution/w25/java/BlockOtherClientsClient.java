import com.comsol.model.util.ModelUtil;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Minimal, model-free COMSOL 6.4 API client for the frozen W25 isolation check.
 * The process role and trial are supplied by the supervising Python process.
 */
public final class BlockOtherClientsClient {
  private static final BufferedReader INPUT =
      new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));

  private static String enc(String value) {
    try {
      return URLEncoder.encode(value == null ? "" : value, "UTF-8");
    } catch (Exception impossible) {
      return "encoding_error";
    }
  }

  private static void event(String name, String detail) {
    System.out.println("EVENT\t" + name + "\t" + (detail == null ? "" : detail));
    System.out.flush();
  }

  private static String command(String expected) throws Exception {
    String line = INPUT.readLine();
    if (line == null || !expected.equals(line)) {
      throw new IllegalStateException("expected command " + expected + ", got " + line);
    }
    return line;
  }

  private static void connect(String host, int port, String role, String trial) {
    // Match the production worker's explicit WebSocket mode; the supervisor
    // supplies this JVM with the same task-private prefs directory as the server.
    ModelUtil.connect(host, port, false);
    event("CONNECTED", "role=" + role + "\ttrial=" + trial);
  }

  private static void disconnect(String trial) {
    event("DISCONNECT_REQUESTED", "trial=" + trial);
    ModelUtil.disconnect();
    event("DISCONNECTED", "trial=" + trial);
  }

  private static void finishReleaseHandshake(String trial) throws Exception {
    // Keep this connected, but make no COMSOL API calls while the supervisor
    // performs an independent B.tags() read. Only FINISH permits disconnect.
    event("OWNER_READY_FOR_POST_RELEASE_READ", "trial=" + trial);
    command("FINISH");
    event("OWNER_FINISH_ACCEPTED", "trial=" + trial);
  }

  private static void runBlockOwner(String mode, String host, int port, String trial)
      throws Exception {
    connect(host, port, "A", trial);
    event("ACQUIRE_REQUESTED", "trial=" + trial);
    ModelUtil.blockOtherClients(true);
    event("ACQUIRED", "trial=" + trial);

    if ("disconnect".equals(mode)) {
      // The supervisor terminates this exact, owned JVM to exercise disconnect recovery.
      command("HOLD");
      event("HOLD_ACCEPTED", "trial=" + trial);
      Thread.sleep(Long.MAX_VALUE);
      return;
    }

    if ("explicit".equals(mode)) {
      command("RELEASE");
      try {
        event("RELEASE_REQUESTED", "trial=" + trial);
        ModelUtil.blockOtherClients(false);
        event("RELEASE_RETURNED", "trial=" + trial);
        finishReleaseHandshake(trial);
      } finally {
        disconnect(trial);
      }
      return;
    }

    if ("finally".equals(mode)) {
      boolean sentinelObserved = false;
      try {
        command("INJECT");
        event("INJECT_ACCEPTED", "trial=" + trial);
        throw new InjectedSentinel();
      } catch (InjectedSentinel expected) {
        sentinelObserved = true;
        event("SENTINEL_CAUGHT", "trial=" + trial);
      } finally {
        try {
          event("RELEASE_REQUESTED", "trial=" + trial);
          ModelUtil.blockOtherClients(false);
          event("RELEASE_RETURNED", "trial=" + trial);
          finishReleaseHandshake(trial);
        } finally {
          disconnect(trial);
        }
      }
      if (!sentinelObserved) {
        throw new IllegalStateException("private sentinel was not observed");
      }
      return;
    }
    throw new IllegalArgumentException("unsupported owner mode " + mode);
  }

  private static void runObserver(String host, int port) throws Exception {
    connect(host, port, "B", "observer");
    String line;
    while ((line = INPUT.readLine()) != null) {
      if (line.startsWith("READ\t")) {
        String label = line.substring("READ\t".length());
        event("READ_CALL_ENTERED", "label=" + label);
        try {
          String[] tags = ModelUtil.tags();
          event("READ_CALL_RETURNED", "label=" + label + "\ttag_count=" + tags.length);
        } catch (Throwable failure) {
          event("READ_CALL_ERROR", "label=" + label
              + "\terror_class=" + failure.getClass().getName()
              + "\tmessage=" + enc(failure.getMessage() == null ? failure.toString() : failure.getMessage()));
        }
      } else if ("DISCONNECT".equals(line)) {
        disconnect("observer");
        return;
      } else {
        event("PROTOCOL_ERROR", "message=" + enc("unexpected observer command " + line));
        return;
      }
    }
  }

  public static void main(String[] args) {
    try {
      if (args.length != 4) {
        throw new IllegalArgumentException("usage: <observer|explicit|finally|disconnect> <host> <port> <trial>");
      }
      String role = args[0];
      String host = args[1];
      int port = Integer.parseInt(args[2]);
      String trial = args[3];
      if ("observer".equals(role)) {
        runObserver(host, port);
      } else {
        runBlockOwner(role, host, port, trial);
      }
    } catch (Throwable failure) {
      event("FATAL", "message=" + enc(failure.toString()));
      failure.printStackTrace(System.err);
      System.exit(2);
    }
  }

  private static final class InjectedSentinel extends RuntimeException {
    private static final long serialVersionUID = 1L;
  }
}
