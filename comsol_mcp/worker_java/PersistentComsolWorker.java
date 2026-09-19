package comsol_mcp.worker_java;

import com.sun.security.auth.module.NTSystem;
import com.comsol.model.Model;
import com.comsol.model.util.ModelChangeInfo;
import com.comsol.model.util.ModelChangedHandler;
import com.comsol.model.util.ModelUtil;

import java.io.*;
import java.lang.reflect.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.UserPrincipal;
import java.nio.file.attribute.UserPrincipalLookupService;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.channels.OverlappingFileLockException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

/**
 * A deliberately small, persistent, local-only COMSOL API worker.
 *
 * <p>The protocol is newline-delimited JSON. It is internal to comsol-mcp,
 * authenticated before every request, and deliberately has no external MCP
 * exposure. One executor owns every ModelUtil/Model invocation. The socket
 * serving health/status never enters the engine, so it remains responsive when
 * a solve is running.</p>
 */
public final class PersistentComsolWorker {
  private static final Set<String> METHODS = new HashSet<>(Arrays.asList(
      "active", "author", "batch", "bem", "clear", "clearAll", "component", "coeff",
      "comments", "create", "createAutoSequences", "dataset", "descr", "disableUpdates",
      "feature", "geom", "get", "getAllowedPropertyValues", "getComsolVersion",
      "getEntityFromModelPath", "getFilePath", "getLastComputationDate",
      "getLastComputationTime", "getLastComputationVersion", "getPVals", "getReal",
      "getData", "getImag", "getBoolean", "getBooleanArray", "getBooleanMatrix",
      "getDouble", "getDoubleArray", "getDoubleMatrix", "getInt", "getIntArray", "getIntMatrix",
      "getString", "getStringArray", "getStringMatrix", "getType", "getValueType",
      "isActive", "isComplex", "isGeometryMeshDependent", "isInheriting", "isInitialized",
      "label", "location", "locationUri", "mesh", "model", "modelNode", "name", "numerical",
      "param", "physics", "properties", "remove", "rename", "result", "run", "runAll",
      "runNoGen", "save", "selection", "set", "setIndex", "setEntry", "sol", "study", "tag", "tags",
      "timeModified", "title", "update", "varnames", "variable", "all", "entities", "inherit", "named", "material"));
  private static final Set<String> MODEL_UTIL = new HashSet<>(Arrays.asList(
      "create", "load", "model", "remove", "tags", "uniquetag", "modelsUsedByOtherClients",
      "getComsolVersion"));

  private final String token;
  private final String instanceId = UUID.randomUUID().toString();
  private final ServerSocket server;
  private final Path serverLockRoot;
  private FileChannel serverLockChannel;
  private FileLock serverLock;
  private String lockedEndpoint = "";
  private final ExecutorService engine = Executors.newSingleThreadExecutor(r -> {
    Thread t = new Thread(r, "comsol-engine-serial"); t.setDaemon(false); return t;
  });
  private final ExecutorService sockets = Executors.newCachedThreadPool(r -> {
    Thread t = new Thread(r, "comsol-worker-control"); t.setDaemon(true); return t;
  });
  private final Map<String, Object> handles = new ConcurrentHashMap<>();
  private final Map<String, RequestState> requests = new ConcurrentHashMap<>();
  private final AtomicLong generation;
  private final AtomicLong changedCount = new AtomicLong();
  private final Set<String> changedTags = ConcurrentHashMap.newKeySet();
  private final ConcurrentMap<String, AtomicLong> changedByTag = new ConcurrentHashMap<>();
  private final Path codeRoot;
  private final ConcurrentMap<String, CompiledArtifact> compiledArtifacts = new ConcurrentHashMap<>();
  private volatile boolean connected;
  private volatile String serverIdentity = "";

  private PersistentComsolWorker(String token, int port, long initialGeneration, Path serverLockRoot) throws IOException {
    this.token = token;
    this.generation = new AtomicLong(initialGeneration);
    this.serverLockRoot = serverLockRoot;
    this.codeRoot = Files.createTempDirectory("comsol-mcp-java-code-");
    this.server = new ServerSocket();
    this.server.bind(new InetSocketAddress(InetAddress.getLoopbackAddress(), port));
  }

  public static void main(String[] args) throws Exception {
    Map<String, String> options = options(args);
    String token = System.getenv("COMSOL_MCP_WORKER_TOKEN");
    if (token == null || token.length() < 32) throw new IllegalArgumentException("COMSOL_MCP_WORKER_TOKEN requires at least 32 characters");
    int port = Integer.parseInt(options.getOrDefault("port", "0"));
    long generation = Long.parseLong(options.getOrDefault("generation", "1"));
    if (generation < 1) throw new IllegalArgumentException("generation must be positive");
    String endpointFile = options.get("endpoint-file");
    if (endpointFile == null) throw new IllegalArgumentException("--endpoint-file is required");
    String lockRoot = options.get("server-lock-root");
    if (lockRoot == null) throw new IllegalArgumentException("--server-lock-root is required");
    PersistentComsolWorker worker = new PersistentComsolWorker(token, port, generation, Paths.get(lockRoot));
    worker.publishEndpoint(Paths.get(endpointFile));
    worker.serve();
  }

  private static Map<String, String> options(String[] args) {
    Map<String, String> out = new HashMap<>();
    for (int i = 0; i < args.length; i += 2) {
      if (!args[i].startsWith("--") || i + 1 >= args.length) throw new IllegalArgumentException("expected --key value arguments");
      out.put(args[i].substring(2), args[i + 1]);
    }
    return out;
  }
  private void publishEndpoint(Path endpoint) throws IOException {
    Files.createDirectories(endpoint.getParent());
    Path temporary = endpoint.resolveSibling(endpoint.getFileName().toString() + ".tmp");
    long processStartEpochMs = ProcessHandle.current().info().startInstant()
        .map(instant -> instant.toEpochMilli()).orElse(-1L);
    String data = Json.write(map("type", "ready", "host", "127.0.0.1", "port", server.getLocalPort(),
        "pid", ProcessHandle.current().pid(), "process_start_epoch_ms", processStartEpochMs,
        "generation", generation.get(), "instance_id", instanceId, "token", token));
    Files.write(temporary, (data + "\n").getBytes(StandardCharsets.UTF_8), StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
    try { Files.setPosixFilePermissions(temporary, EnumSet.of(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE)); }
    catch (UnsupportedOperationException ignored) { }
    try {
      Files.move(temporary, endpoint, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    } catch (AtomicMoveNotSupportedException ignored) {
      // A same-directory replacement is still private and complete. Some
      // Windows filesystems do not implement ATOMIC_MOVE for this path.
      Files.move(temporary, endpoint, StandardCopyOption.REPLACE_EXISTING);
    }
  }

  private void serve() throws IOException {
    while (!server.isClosed()) {
      try { Socket socket = server.accept(); sockets.submit(() -> serveSocket(socket)); }
      catch (RejectedExecutionException ignored) { break; }
    }
  }
  @SuppressWarnings("unchecked")
  private void serveSocket(Socket socket) {
    try (Socket ignored = socket;
         BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
         BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8))) {
      String line = reader.readLine();
      Map<String, Object> auth = object(line);
      if (!constantTimeEquals(token, string(auth.get("token")))) { reply(writer, error("AUTH_FAILED", "worker token rejected")); return; }
      reply(writer, map("ok", true, "type", "authenticated", "generation", generation.get()));
      while ((line = reader.readLine()) != null) {
        try { reply(writer, dispatch(object(line))); }
        catch (Throwable t) { reply(writer, failure("WORKER_PROTOCOL_ERROR", t, false)); }
      }
    } catch (IOException | UncheckedIOException ignored) {
      // The controller may time out while the engine continues. That must not interrupt engine work.
    }
  }
  private void reply(BufferedWriter writer, Map<String, Object> data) throws IOException {
    writer.write(Json.write(data)); writer.write("\n"); writer.flush();
  }

  private Map<String, Object> dispatch(Map<String, Object> request) throws Exception {
    String type = string(request.get("type"));
    if ("health".equals(type)) return health();
    if ("status".equals(type)) return status(string(request.get("request_id")));
    if ("codec_selftest".equals(type)) return map("ok", true, "result", encode(map("kind", "map", "nested", map("value", 7), "array", Arrays.asList("x", 2))));
    if ("reflection_selftest".equals(type)) return map("ok", true, "result", reflectionSelftest());
    if ("shutdown".equals(type)) return error("PERMISSION_DENIED", "worker shutdown is controlled by its owner process");
    if (!"connect".equals(type) && !"call".equals(type) && !"model".equals(type) && !"modelutil".equals(type) && !"model_snapshot".equals(type) && !"disconnect".equals(type) && !"lock_selftest".equals(type) && !"code_compile".equals(type) && !"code_execute".equals(type))
      return error("UNKNOWN_COMMAND", "unsupported internal worker command");
    String id = requiredId(request);
    RequestState old = requests.get(id);
    String requestHash = hashRequest(request);
    if (old != null) return old.requestHash.equals(requestHash) ? old.snapshot() : error("IDEMPOTENCY_KEY_CONFLICT", "request_id was previously used for different request content");
    RequestState state = new RequestState(id, type, requestHash);
    if (requests.putIfAbsent(id, state) != null) { old = requests.get(id); return old.requestHash.equals(requestHash) ? old.snapshot() : error("IDEMPOTENCY_KEY_CONFLICT", "request_id was previously used for different request content"); }
    engine.submit(() -> execute(state, request));
    boolean boundedWait = request.containsKey("queue_timeout_ms");
    long queueMillis = number(request.get("queue_timeout_ms"), 0);
    if (!boundedWait) {
      state.done.get();
    } else if (queueMillis > 0) {
      try { state.done.get(queueMillis, TimeUnit.MILLISECONDS); }
      catch (TimeoutException ignored) { return state.snapshot(); }
    }
    return state.snapshot();
  }
  private void execute(RequestState state, Map<String, Object> request) {
    state.start();
    try {
      String type = string(request.get("type")); Object result;
      if ("connect".equals(type)) result = connect(request);
      else if ("disconnect".equals(type)) result = disconnect();
      else if ("model".equals(type)) result = model(request);
      else if ("model_snapshot".equals(type)) result = modelSnapshot(request);
      else if ("lock_selftest".equals(type)) result = lockSelftest(request);
      else if ("modelutil".equals(type)) result = modelUtil(request);
      else if ("code_compile".equals(type)) result = compileJava(request);
      else if ("code_execute".equals(type)) result = executeJava(request);
      else result = call(request);
      state.succeed(encode(result));
    } catch (WorkerFailure t) {
      Map<String,Object> failure = error(t.code, t.getMessage());
      if (t.details != null) failure.putAll(t.details);
      state.fail(failure);
    }
    catch (Throwable t) { state.fail(failure("ENGINE_CALL_FAILED", t, true)); }
  }

  private Object connect(Map<String, Object> request) {
    String host = string(request.get("host")); int port = (int) number(request.get("port"), -1);
    if (host.isEmpty() || port < 1 || port > 65535) throw new IllegalArgumentException("host and port required");
    String canonicalHost;
    try { canonicalHost = InetAddress.getByName(host).getHostAddress(); }
    catch (IOException exc) { throw new WorkerFailure("ENGINE_UNRESPONSIVE", "server hostname could not be resolved"); }
    acquireServerLock(canonicalHost + ":" + port);
    boolean encrypted = Boolean.TRUE.equals(request.get("encrypted"));
    // Credentials are accepted only as in-memory request values and never returned/logged.
    String user = string(request.get("user")), pass = string(request.get("password"));
    try {
      if (user.isEmpty() && pass.isEmpty()) ModelUtil.connect(host, port, encrypted);
      else ModelUtil.connect(host, port, encrypted, user, pass);
    } catch (RuntimeException exc) { releaseServerLock(); throw exc; }
    ModelUtil.setModelChangedHandler(new ChangeHandler());
    connected = true; serverIdentity = host + ":" + port; generation.incrementAndGet(); handles.clear();
    return map("connected", true, "server", serverIdentity, "generation", generation.get(), "instance_id", instanceId, "engine_version", ModelUtil.getComsolVersion());
  }
  private Object disconnect() {
    try { if (connected) ModelUtil.disconnect(); }
    finally { connected = false; serverIdentity = ""; generation.incrementAndGet(); handles.clear(); releaseServerLock(); }
    return map("connected", false, "generation", generation.get(), "instance_id", instanceId);
  }
  private Object lockSelftest(Map<String, Object> request) {
    int port = (int) number(request.get("port"), -1); if (port < 1 || port > 65535) throw new IllegalArgumentException("port required");
    acquireServerLock("127.0.0.1:" + port); return map("locked", true, "endpoint", lockedEndpoint);
  }
  private static boolean isWindows() {
    return System.getProperty("os.name", "").toLowerCase(Locale.ROOT).contains("win");
  }
  private UserPrincipal currentProcessOwner() throws IOException {
    if (!isWindows()) return Files.getOwner(Paths.get(System.getProperty("user.home")));
    try {
      // NTSystem reads the native process token. Environment variables and
      // user.home can describe the administrator or service account instead.
      NTSystem nativeIdentity = new NTSystem();
      String domain = nativeIdentity.getDomain(), name = nativeIdentity.getName();
      if (domain == null || domain.isEmpty() || name == null || name.isEmpty())
        throw new IOException("native Windows token has no domain-qualified user");
      String qualifiedName = domain + "\\" + name;
      UserPrincipalLookupService lookup = serverLockRoot.getFileSystem().getUserPrincipalLookupService();
      return lookup.lookupPrincipalByName(qualifiedName);
    } catch (IOException failure) {
      throw failure;
    } catch (RuntimeException failure) {
      throw new IOException("could not resolve native Windows token user", failure);
    }
  }
  private void prepareServerLockRoot() throws IOException {
    UserPrincipal processOwner = currentProcessOwner();
    Path parent = serverLockRoot.getParent();
    if (parent != null) Files.createDirectories(parent);
    boolean created = false;
    try {
      // createDirectory, rather than createDirectories, tells us whether this
      // process created the final root. That distinction prevents changing a
      // foreign existing root's owner after a race.
      Files.createDirectory(serverLockRoot);
      created = true;
    } catch (FileAlreadyExistsException ignored) {
      // Reconcile the existing root below; do not take ownership of it.
    }
    if (Files.isSymbolicLink(serverLockRoot)) throw new WorkerFailure("PERMISSION_DENIED", "server lock root must not be a symlink");
    if (!Files.isDirectory(serverLockRoot)) throw new WorkerFailure("PERMISSION_DENIED", "server lock root must be a directory");
    if (isWindows() && created) {
      // setOwner changes only the owner field and leaves the inherited DACL
      // intact. Never apply this to a root we did not create in this call.
      Files.setOwner(serverLockRoot, processOwner);
      if (Files.isSymbolicLink(serverLockRoot)) throw new WorkerFailure("PERMISSION_DENIED", "server lock root must not be a symlink");
    }
    UserPrincipal lockOwner = Files.getOwner(serverLockRoot);
    if (!lockOwner.equals(processOwner)) throw new WorkerFailure("PERMISSION_DENIED", "server lock root is not owned by this user");
    if (!isWindows()) {
      try { Files.setPosixFilePermissions(serverLockRoot, EnumSet.of(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE, PosixFilePermission.OWNER_EXECUTE)); }
      catch (UnsupportedOperationException ignored) { }
    }
  }
  private void acquireServerLock(String endpoint) {
    if (endpoint.equals(lockedEndpoint) && serverLock != null && serverLock.isValid()) return;
    if (serverLock != null) throw new WorkerFailure("ENGINE_BUSY", "worker is already bound to a different server endpoint");
    try {
      prepareServerLockRoot();
      String endpointDigest;
      try { endpointDigest = sha256(endpoint); }
      catch (Exception failure) { throw new WorkerFailure("ENGINE_UNRESPONSIVE", "could not derive endpoint lock identity"); }
      Path lockPath = serverLockRoot.resolve("endpoint-" + endpointDigest + ".lock");
      if (Files.isSymbolicLink(lockPath)) throw new WorkerFailure("PERMISSION_DENIED", "server lock path must not be a symlink");
      serverLockChannel = FileChannel.open(lockPath, StandardOpenOption.CREATE, StandardOpenOption.WRITE);
      try { serverLock = serverLockChannel.tryLock(); }
      catch (OverlappingFileLockException conflict) { serverLock = null; }
      if (serverLock == null) { serverLockChannel.close(); serverLockChannel = null; throw new WorkerFailure("ENGINE_BUSY", "another persistent worker owns this server endpoint"); }
      lockedEndpoint = endpoint;
    } catch (WorkerFailure failure) { throw failure; }
    catch (IOException failure) { releaseServerLock(); throw new WorkerFailure("PERMISSION_DENIED", "could not acquire private endpoint lock"); }
  }
  private void releaseServerLock() {
    try { if (serverLock != null) serverLock.release(); } catch (IOException ignored) { }
    try { if (serverLockChannel != null) serverLockChannel.close(); } catch (IOException ignored) { }
    serverLock = null; serverLockChannel = null; lockedEndpoint = "";
  }
  private Object model(Map<String, Object> request) {
    ensureConnected(); String tag = string(request.get("tag")); if (tag.isEmpty()) throw new IllegalArgumentException("model tag required");
    return handle(ModelUtil.model(tag));
  }
  private Object modelSnapshot(Map<String, Object> request) throws Exception {
    ensureConnected(); String tag = string(request.get("tag")); if (tag.isEmpty()) throw new IllegalArgumentException("model tag required");
    Model model = ModelUtil.model(tag);
    String[] parameterNames = model.param().varnames(); Arrays.sort(parameterNames);
    StringBuilder parameterFingerprint = new StringBuilder();
    for (String name : parameterNames) parameterFingerprint.append(name).append('=').append(model.param().get(name)).append('\u0000');
    String source = tag + "\u0000" + model.label() + "\u0000" + model.getFilePath() + "\u0000" + model.timeModified() + "\u0000" + parameterFingerprint;
    if (source.isEmpty()) throw new IllegalStateException("MODEL_FINGERPRINT_UNAVAILABLE");
    return map("tag", tag, "model_tag", tag, "fingerprint", sha256(source), "fingerprint_scope", Arrays.asList("tag", "label", "file_path", "time_modified", "parameters"),
        "cas_limit", "control-plane revision plus observed change events; not COMSOL atomic CAS", "external_event_counter", changedByTag.computeIfAbsent(tag, ignored -> new AtomicLong()).get(),
        "changed_tags", new ArrayList<>(changedTags), "server_instance_id", serverIdentity, "worker_instance_id", instanceId, "instance_id", instanceId, "generation", generation.get());
  }
  private Object compileJava(Map<String, Object> request) throws Exception {
    SourceSpec source = sourceSpec(request);
    String key = source.sha256 + "|" + source.entrypoint;
    CompiledArtifact existing = compiledArtifacts.get(key);
    if (existing != null && Files.isDirectory(existing.classes)) {
      return map("compiled", true, "source_sha256", source.sha256, "entrypoint", source.entrypoint,
          "artifact", existing.classes.toString(), "diagnostics", Collections.emptyList());
    }
    if (source.text.matches("(?s).*\\bstatic\\s*\\{.*"))
      throw new WorkerFailure("TRUSTED_CODE_REJECTED", "Java source contains a static initializer", map("source_sha256", source.sha256, "entrypoint", source.entrypoint, "diagnostics", Collections.emptyList()));
    JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
    if (compiler == null) throw new WorkerFailure("COMPILE_UNAVAILABLE", "the Worker JVM does not expose javac", map("source_sha256", source.sha256, "entrypoint", source.entrypoint, "diagnostics", Collections.emptyList()));
    Path classes = codeRoot.resolve(source.sha256.substring(0, 24));
    Files.createDirectories(classes);
    DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
    boolean success;
    try (StandardJavaFileManager manager = compiler.getStandardFileManager(diagnostics, Locale.ROOT, StandardCharsets.UTF_8)) {
      Iterable<? extends JavaFileObject> files = manager.getJavaFileObjectsFromFiles(Collections.singletonList(source.path.toFile()));
      List<String> options = Arrays.asList("-classpath", System.getProperty("java.class.path", ""), "-d", classes.toString());
      JavaCompiler.CompilationTask task = compiler.getTask(null, manager, diagnostics, options, null, files);
      success = Boolean.TRUE.equals(task.call());
    }
    List<Object> diagnosticRows = new ArrayList<>();
    for (Diagnostic<? extends JavaFileObject> item : diagnostics.getDiagnostics()) {
      diagnosticRows.add(map("kind", item.getKind().name(), "source", item.getSource() == null ? "" : item.getSource().getName(),
          "line", item.getLineNumber(), "column", item.getColumnNumber(), "start", item.getStartPosition(), "end", item.getEndPosition(),
          "message", item.getMessage(Locale.ROOT)));
    }
    if (!success) throw new WorkerFailure("COMPILE_ERROR", "Java compilation failed", map("source_sha256", source.sha256, "entrypoint", source.entrypoint, "diagnostics", diagnosticRows, "artifact", classes.toString()));
    compiledArtifacts.put(key, new CompiledArtifact(classes, source.sha256, source.entrypoint));
    return map("compiled", true, "source_sha256", source.sha256, "entrypoint", source.entrypoint,
        "artifact", classes.toString(), "diagnostics", diagnosticRows);
  }
  @SuppressWarnings("unchecked")
  private Object executeJava(Map<String, Object> request) throws Exception {
    ensureConnected();
    String tag = string(request.get("tag"));
    if (tag.isEmpty()) throw new IllegalArgumentException("model tag required for Java execution");
    SourceSpec source = sourceSpec(request);
    String key = source.sha256 + "|" + source.entrypoint;
    CompiledArtifact artifact = compiledArtifacts.get(key);
    if (artifact == null || !Files.isDirectory(artifact.classes)) { compileJava(request); artifact = compiledArtifacts.get(key); }
    if (artifact == null) throw new WorkerFailure("COMPILE_ERROR", "compiled Java artifact is unavailable");
    Model model = ModelUtil.model(tag); // identity is resolved inside the same serial Worker queue
    String className = source.entrypoint;
    String methodName = "run";
    int hash = source.entrypoint.indexOf('#');
    if (hash >= 0) { className = source.entrypoint.substring(0, hash); methodName = source.entrypoint.substring(hash + 1); }
    if (className.indexOf('.') < 0) {
      java.util.regex.Matcher pkg = java.util.regex.Pattern.compile("(?m)^\\s*package\\s+([A-Za-z_$][\\w$]*(?:\\.[A-Za-z_$][\\w$]*)*)\\s*;").matcher(source.text);
      if (pkg.find()) className = pkg.group(1) + "." + className;
    }
    Map<String, Object> arguments = request.get("arguments") instanceof Map ? (Map<String, Object>) request.get("arguments") : Collections.emptyMap();
    try (URLClassLoader loader = new URLClassLoader(new URL[]{artifact.classes.toUri().toURL()}, getClass().getClassLoader())) {
      Class<?> clazz = Class.forName(className, false, loader);
      Method selected = null;
      for (Method method : clazz.getMethods()) {
        if (!method.getName().equals(methodName) || !Modifier.isPublic(method.getModifiers()) || !Modifier.isStatic(method.getModifiers())) continue;
        Class<?>[] types = method.getParameterTypes();
        if (types.length == 2 && Model.class.isAssignableFrom(types[0]) && Map.class.isAssignableFrom(types[1])) { selected = method; break; }
        if (types.length == 1 && Model.class.isAssignableFrom(types[0])) selected = method;
      }
      if (selected == null) throw new WorkerFailure("ENTRYPOINT_REJECTED", "entrypoint must expose public static run(Model[, Map])");
      Object value = selected.getParameterCount() == 2 ? selected.invoke(null, model, arguments) : selected.invoke(null, model);
      return map("executed", true, "model_tag", tag, "source_sha256", source.sha256, "entrypoint", source.entrypoint, "readback", encode(value));
    }
  }
  private SourceSpec sourceSpec(Map<String, Object> request) throws Exception {
    String raw = string(request.get("source_artifact"));
    if (raw.isEmpty()) throw new WorkerFailure("ARTIFACT_MISSING", "source_artifact is required");
    Path path = Paths.get(raw).toAbsolutePath().normalize();
    if (Files.isSymbolicLink(path) || !Files.isRegularFile(path) || !path.getFileName().toString().endsWith(".java"))
      throw new WorkerFailure("ARTIFACT_MISSING", "source artifact must be a regular Java file");
    byte[] bytes = Files.readAllBytes(path);
    String text = new String(bytes, StandardCharsets.UTF_8);
    String entrypoint = string(request.get("entrypoint"));
    if (entrypoint.isEmpty()) {
      java.util.regex.Matcher cls = java.util.regex.Pattern.compile("(?:class|interface|record)\\s+([A-Za-z_$][\\w$]*)").matcher(text);
      if (!cls.find()) throw new WorkerFailure("INVALID_REQUEST", "source has no Java class entrypoint");
      entrypoint = cls.group(1);
    }
    return new SourceSpec(path, text, sha256Bytes(bytes), entrypoint);
  }
  @SuppressWarnings("unchecked")
  private Object modelUtil(Map<String, Object> request) throws Exception {
    String method = string(request.get("method")); if (!MODEL_UTIL.contains(method)) throw new SecurityException("MODEL_UTIL_METHOD_REJECTED");
    List<Object> args = list(request.get("args"));
    if (("create".equals(method) || "load".equals(method)) && !connected) throw new IllegalStateException("not connected");
    return invoke(null, ModelUtil.class, method, args);
  }
  @SuppressWarnings("unchecked")
  private Object call(Map<String, Object> request) throws Exception {
    ensureConnected(); long claimed = number(request.get("generation"), -1);
    if (claimed != generation.get()) throw new IllegalStateException("STALE_WORKER_HANDLE");
    String handle = string(request.get("handle")); Object target = handles.get(handle);
    if (target == null) throw new IllegalArgumentException("UNKNOWN_WORKER_HANDLE");
    String method = string(request.get("method")); if (!METHODS.contains(method)) throw new SecurityException("METHOD_REJECTED");
    return invoke(target, target.getClass(), method, list(request.get("args")));
  }
  private Object invoke(Object target, Class<?> type, String name, List<Object> args) throws Exception {
    Method selected = null; Object[] selectedArgs = null; int selectedScore = Integer.MAX_VALUE; boolean ambiguous = false;
    for (Method method : publicMethods(type)) {
      if (!method.getName().equals(name) || method.getParameterCount() != args.size()) continue;
      if (!Modifier.isPublic(method.getModifiers()) || method.getDeclaringClass().equals(Object.class)) continue;
      if (target != null && Modifier.isStatic(method.getModifiers())) continue;
      Conversion converted = convert(method.getParameterTypes(), args); if (converted == null) continue;
      if (converted.score < selectedScore) { selected = method; selectedArgs = converted.values; selectedScore = converted.score; ambiguous = false; }
      else if (converted.score == selectedScore) ambiguous = true;
    }
    if (selected == null) throw new NoSuchMethodException("no permitted public overload for " + name + "/" + args.size());
    if (ambiguous) throw new IllegalArgumentException("ambiguous permitted overload for " + name + "/" + args.size());
    try { return selected.invoke(target, selectedArgs); }
    catch (InvocationTargetException e) { throw unwrap(e); }
  }
  private static Exception unwrap(InvocationTargetException e) throws Exception {
    Throwable cause = e.getCause(); if (cause instanceof Exception) return (Exception) cause;
    if (cause instanceof Error) throw (Error) cause; return new Exception(cause);
  }
  private static List<Method> publicMethods(Class<?> type) {
    LinkedHashMap<String, Method> out = new LinkedHashMap<>();
    collectInterfaces(type, out);
    if (Modifier.isPublic(type.getModifiers())) for (Method method : type.getMethods()) out.putIfAbsent(invocationSignature(method), method);
    return new ArrayList<>(out.values());
  }
  private static void collectInterfaces(Class<?> type, Map<String, Method> out) {
    if (type == null) return;
    for (Class<?> iface : type.getInterfaces()) {
      for (Method method : iface.getMethods()) if (Modifier.isPublic(method.getModifiers())) out.putIfAbsent(invocationSignature(method), method);
      collectInterfaces(iface, out);
    }
    collectInterfaces(type.getSuperclass(), out);
  }
  private static String invocationSignature(Method method) {
    StringBuilder signature = new StringBuilder(method.getName()).append('(');
    for (Class<?> parameter : method.getParameterTypes()) signature.append(parameter.getName()).append(';');
    return signature.append(')').toString();
  }
  private Object reflectionSelftest() throws Exception {
    return map("duplicate_interface_tag", invoke(new DuplicateTagFixture(), DuplicateTagFixture.class, "tag", Collections.emptyList()),
        "numerical_allowed", METHODS.contains("numerical"));
  }
  public interface TaggableA { String tag(); }
  public interface TaggableB { String tag(); }
  public static final class DuplicateTagFixture implements TaggableA, TaggableB {
    public String tag() { return "resolved"; }
  }
  private Conversion convert(Class<?>[] types, List<Object> args) {
    Object[] out = new Object[types.length]; int score = 0;
    try { for (int i = 0; i < types.length; i++) { out[i] = convert(types[i], args.get(i)); score += conversionScore(types[i], args.get(i)); } return new Conversion(out, score); }
    catch (IllegalArgumentException bad) { return null; }
  }
  @SuppressWarnings("unchecked")
  private int conversionScore(Class<?> type, Object value) {
    String declared = typedSignature(value);
    if (declared != null && !signatureMatches(type, declared)) return 100000;
    value = typedData(value);
    if (value == null) return type.isPrimitive() ? 100000 : 0;
    if (type.isInstance(value)) return 0;
    if (type.equals(String.class)) return value instanceof String ? 0 : 100000;
    if (type.equals(boolean.class) || type.equals(Boolean.class)) return value instanceof Boolean ? 0 : 100000;
    if (value instanceof Long) {
      long n = (Long)value;
      if ((type.equals(int.class) || type.equals(Integer.class)) && n >= Integer.MIN_VALUE && n <= Integer.MAX_VALUE) return 0;
      if (type.equals(long.class) || type.equals(Long.class)) return 1;
      if (type.equals(double.class) || type.equals(Double.class)) return 2;
      if (type.equals(float.class) || type.equals(Float.class)) return 3;
    }
    if (value instanceof Double) {
      if (type.equals(double.class) || type.equals(Double.class)) return 0;
      if (type.equals(float.class) || type.equals(Float.class)) return 1;
    }
    if (type.isArray() && value instanceof List) {
      int score=0; for(Object item:(List<Object>)value) { int itemScore = conversionScore(type.getComponentType(), item); if (itemScore >= 100000) return 100000; score += itemScore; }
      return score;
    }
    return 100000;
  }
  @SuppressWarnings("unchecked")
  private Object convert(Class<?> type, Object value) {
    String declared = typedSignature(value);
    if (declared != null && !signatureMatches(type, declared)) throw new IllegalArgumentException("declared Java signature does not match overload");
    value = typedData(value);
    if (value == null) { if (type.isPrimitive()) throw new IllegalArgumentException(); return null; }
    if (type.equals(String.class)) { if (value instanceof String) return value; throw new IllegalArgumentException("string required"); }
    if (type.equals(boolean.class) || type.equals(Boolean.class)) { if (value instanceof Boolean) return value; throw new IllegalArgumentException("boolean required"); }
    if (Number.class.isAssignableFrom(value.getClass())) {
      Number n = (Number) value;
      if (type.equals(int.class) || type.equals(Integer.class)) return n.intValue();
      if (type.equals(long.class) || type.equals(Long.class)) return n.longValue();
      if (type.equals(double.class) || type.equals(Double.class)) return n.doubleValue();
      if (type.equals(float.class) || type.equals(Float.class)) return n.floatValue();
    }
    if (type.isEnum() && value instanceof String) {
      @SuppressWarnings({"unchecked", "rawtypes"}) Object enumValue = Enum.valueOf((Class<? extends Enum>) type, (String) value); return enumValue;
    }
    if (type.isArray() && value instanceof List) {
      List<Object> values = (List<Object>) value; Class<?> component = type.getComponentType(); Object array = Array.newInstance(component, values.size());
      for (int i = 0; i < values.size(); i++) Array.set(array, i, convert(component, values.get(i)));
      return array;
    }
    if (type.isInstance(value)) return value;
    throw new IllegalArgumentException("argument type mismatch");
  }
  @SuppressWarnings("unchecked")
  private static Object typedData(Object value) {
    if (!(value instanceof Map)) return value;
    Map<Object,Object> map = (Map<Object,Object>) value;
    return map.containsKey("kind") && map.containsKey("shape") && map.containsKey("data") ? map.get("data") : value;
  }
  @SuppressWarnings("unchecked")
  private static String typedSignature(Object value) {
    if (!(value instanceof Map)) return null;
    Map<Object,Object> map = (Map<Object,Object>) value;
    Object kind = map.get("kind"), signature = map.get("java_signature");
    return kind instanceof String && signature instanceof String ? (String) signature : null;
  }
  private static boolean signatureMatches(Class<?> type, String declared) {
    return canonicalSignature(declared).equals(canonicalSignature(type.getName()));
  }
  private static String canonicalSignature(String value) {
    String actual = value == null ? "" : value.trim();
    if (actual.equals("String")) return "java.lang.String";
    if (actual.equals("boolean")) return "boolean";
    if (actual.equals("int")) return "int";
    if (actual.equals("long")) return "long";
    if (actual.equals("double")) return "double";
    if (actual.equals("[Z")) return "boolean[]";
    if (actual.equals("[I")) return "int[]";
    if (actual.equals("[J")) return "long[]";
    if (actual.equals("[D")) return "double[]";
    if (actual.equals("[Ljava.lang.String;")) return "java.lang.String[]";
    if (actual.equals("String[]")) return "java.lang.String[]";
    if (actual.equals("String[][]")) return "java.lang.String[][]";
    if (actual.equals("boolean[]") || actual.equals("int[]") || actual.equals("long[]") || actual.equals("double[]")) return actual;
    if (actual.equals("boolean[][]") || actual.equals("int[][]") || actual.equals("long[][]") || actual.equals("double[][]")) return actual;
    if (actual.equals("[[Z")) return "boolean[][]";
    if (actual.equals("[[I")) return "int[][]";
    if (actual.equals("[[J")) return "long[][]";
    if (actual.equals("[[D")) return "double[][]";
    if (actual.equals("[[Ljava.lang.String;")) return "java.lang.String[][]";
    return actual;
  }
  private Object encode(Object value) {
    if (value == null || value instanceof String || value instanceof Number || value instanceof Boolean) return value;
    if (value instanceof Map) { Map<String,Object> out = new LinkedHashMap<>(); for (Map.Entry<?,?> entry : ((Map<?,?>)value).entrySet()) out.put(String.valueOf(entry.getKey()), encode(entry.getValue())); return out; }
    if (value.getClass().isArray()) { int n = Array.getLength(value); List<Object> out = new ArrayList<>(); for (int i=0;i<n;i++) out.add(encode(Array.get(value,i))); return out; }
    if (value instanceof Collection) { List<Object> out = new ArrayList<>(); for (Object v : (Collection<?>) value) out.add(encode(v)); return out; }
    return handle(value);
  }
  private Map<String, Object> handle(Object value) {
    String id = "h-" + UUID.randomUUID(); handles.put(id, value);
    return map("$worker_handle", id, "generation", generation.get(), "java_type", value.getClass().getInterfaces().length > 0 ? value.getClass().getInterfaces()[0].getName() : value.getClass().getName());
  }
  private Map<String, Object> health() { return map("ok", true, "status", "HEALTHY", "generation", generation.get(), "instance_id", instanceId, "connected", connected, "server", serverIdentity, "changed_count", changedCount.get(), "queued_or_running", requests.values().stream().filter(RequestState::active).count()); }
  private Map<String, Object> status(String id) {
    if (id.isEmpty()) { List<Object> all = new ArrayList<>(); for (RequestState state : requests.values()) all.add(state.snapshot()); return map("ok", true, "requests", all, "generation", generation.get()); }
    RequestState state = requests.get(id); return state == null ? error("REQUEST_NOT_FOUND", "unknown request_id") : state.snapshot();
  }
  private void ensureConnected() { if (!connected) throw new IllegalStateException("WORKER_NOT_CONNECTED"); }
  private final class ChangeHandler implements ModelChangedHandler {
    public void handleModelChangeOnServer(ModelChangeInfo info) {
      changedCount.incrementAndGet();
      for (String tag : info.getModelTags()) { changedTags.add(tag); changedByTag.computeIfAbsent(tag, ignored -> new AtomicLong()).incrementAndGet(); }
    }
  }
  private static String requiredId(Map<String,Object> request) { String id=string(request.get("request_id")); if (id.isEmpty()) throw new IllegalArgumentException("request_id required"); return id; }
  private static String string(Object value) { return value == null ? "" : String.valueOf(value); }
  @SuppressWarnings("unchecked") private static List<Object> list(Object value) { return value instanceof List ? (List<Object>) value : Collections.emptyList(); }
  @SuppressWarnings("unchecked") private static Map<String,Object> object(String input) { Object value=Json.parse(input); if (!(value instanceof Map)) throw new IllegalArgumentException("JSON object required"); return (Map<String,Object>) value; }
  private static long number(Object value, long fallback) { return value instanceof Number ? ((Number)value).longValue() : fallback; }
  private static Map<String,Object> map(Object... values) { Map<String,Object> out=new LinkedHashMap<>(); for(int i=0;i<values.length;i+=2) out.put(String.valueOf(values[i]),values[i+1]); return out; }
  private static Map<String,Object> error(String code,String message) { return map("ok",false,"code",code,"message",message); }
  private static Map<String,Object> failure(String code,Throwable t,boolean unknown) { return map("ok",false,"code",code,"message",t.getClass().getSimpleName()+": "+String.valueOf(t.getMessage()),"execution_state_unknown",unknown); }
  private static boolean constantTimeEquals(String a,String b) { return MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8)); }
  private static String sha256(String text) throws Exception { byte[] bytes = MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)); StringBuilder out = new StringBuilder(); for(byte b:bytes) out.append(String.format("%02x", b)); return out.toString(); }
  private static String sha256Bytes(byte[] bytes) throws Exception { byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes); StringBuilder out = new StringBuilder(); for(byte b:digest) out.append(String.format("%02x", b)); return out.toString(); }
  private static String hashRequest(Map<String,Object> request) throws Exception { Map<String,Object> copy = new TreeMap<>(request); copy.remove("request_id"); copy.remove("queue_timeout_ms"); return sha256(Json.write(copy)); }

  private static final class RequestState {
    final String id, type, requestHash, queuedAt = Long.toString(System.currentTimeMillis()); final CompletableFuture<Void> done = new CompletableFuture<>();
    volatile String status="QUEUED", startedAt="", completedAt=""; volatile Object result=null; volatile Map<String,Object> failure=null;
    RequestState(String id,String type,String requestHash){this.id=id;this.type=type;this.requestHash=requestHash;}
    void start(){status="RUNNING";startedAt=Long.toString(System.currentTimeMillis());}
    void succeed(Object value){result=value;status="SUCCEEDED";completedAt=Long.toString(System.currentTimeMillis());done.complete(null);}
    void fail(Map<String,Object> value){failure=value;status="FAILED";completedAt=Long.toString(System.currentTimeMillis());done.complete(null);}
    boolean active(){return "QUEUED".equals(status)||"RUNNING".equals(status);}
    Map<String,Object> snapshot(){Map<String,Object> out=map("ok",!"FAILED".equals(status),"request_id",id,"type",type,"status",status,"queued_at_ms",queuedAt,"started_at_ms",startedAt,"completed_at_ms",completedAt);if(result!=null)out.put("result",result);if(failure!=null)out.put("failure",failure);return out;}
  }
  private static final class WorkerFailure extends RuntimeException {
    final String code; final Map<String,Object> details;
    WorkerFailure(String code,String message){this(code,message,null);}
    WorkerFailure(String code,String message,Map<String,Object> details){super(message);this.code=code;this.details=details;}
  }
  private static final class SourceSpec {
    final Path path; final String text, sha256, entrypoint;
    SourceSpec(Path path,String text,String sha256,String entrypoint){this.path=path;this.text=text;this.sha256=sha256;this.entrypoint=entrypoint;}
  }
  private static final class CompiledArtifact {
    final Path classes; final String sha256, entrypoint;
    CompiledArtifact(Path classes,String sha256,String entrypoint){this.classes=classes;this.sha256=sha256;this.entrypoint=entrypoint;}
  }
  private static final class Conversion { final Object[] values; final int score; Conversion(Object[] values,int score){this.values=values;this.score=score;} }

  /** Tiny JSON subset codec: objects, arrays, strings, booleans, null, and finite numbers. */
  static final class Json {
    static Object parse(String text) { return new Parser(text).value(); }
    static String write(Object value) { StringBuilder out=new StringBuilder(); write(out,value); return out.toString(); }
    @SuppressWarnings("unchecked") static void write(StringBuilder out,Object v){
      if(v==null)out.append("null"); else if(v instanceof String){out.append('"'); for(char c:((String)v).toCharArray()){switch(c){case '"':out.append("\\\"");break;case '\\':out.append("\\\\");break;case '\n':out.append("\\n");break;case '\r':out.append("\\r");break;case '\t':out.append("\\t");break;default:if(c<32)out.append(String.format("\\u%04x",(int)c));else out.append(c);}}out.append('"');}
      else if(v instanceof Number||v instanceof Boolean)out.append(v); else if(v instanceof Map){out.append('{');boolean first=true;for(Map.Entry<?,?> e:((Map<?,?>)v).entrySet()){if(!first)out.append(',');first=false;write(out,String.valueOf(e.getKey()));out.append(':');write(out,e.getValue());}out.append('}');}
      else if(v instanceof Iterable){out.append('[');boolean first=true;for(Object x:(Iterable<?>)v){if(!first)out.append(',');first=false;write(out,x);}out.append(']');} else write(out,String.valueOf(v)); }
    static final class Parser { final String s; int p; Parser(String s){this.s=s==null?"":s;} Object value(){ws();Object v=raw();ws();if(p!=s.length())throw new IllegalArgumentException("trailing JSON");return v;} Object raw(){ws();if(p>=s.length())throw new IllegalArgumentException("empty JSON");char c=s.charAt(p);if(c=='{')return obj();if(c=='[')return arr();if(c=='\"')return str();if(s.startsWith("true",p)){p+=4;return true;}if(s.startsWith("false",p)){p+=5;return false;}if(s.startsWith("null",p)){p+=4;return null;}return num();} Map<String,Object> obj(){p++;Map<String,Object>m=new LinkedHashMap<>();ws();if(t('}'))return m;while(true){ws();String k=str();ws();need(':');m.put(k,raw());ws();if(t('}'))return m;need(',');}} List<Object> arr(){p++;List<Object>a=new ArrayList<>();ws();if(t(']'))return a;while(true){a.add(raw());ws();if(t(']'))return a;need(',');}} String str(){need('\"');StringBuilder b=new StringBuilder();while(p<s.length()){char c=s.charAt(p++);if(c=='\"')return b.toString();if(c=='\\'){if(p>=s.length())break;char e=s.charAt(p++);if(e=='n')b.append('\n');else if(e=='r')b.append('\r');else if(e=='t')b.append('\t');else if(e=='\"'||e=='\\'||e=='/')b.append(e);else if(e=='u'){if(p+4>s.length())throw new IllegalArgumentException("bad unicode");b.append((char)Integer.parseInt(s.substring(p,p+4),16));p+=4;}else throw new IllegalArgumentException("bad escape");}else b.append(c);}throw new IllegalArgumentException("unterminated string");} Number num(){int q=p;while(p<s.length()&&"-+0123456789.eE".indexOf(s.charAt(p))>=0)p++;String n=s.substring(q,p);try{return n.contains(".")||n.contains("e")||n.contains("E")?Double.valueOf(n):Long.valueOf(n);}catch(Exception e){throw new IllegalArgumentException("bad number");}}void ws(){while(p<s.length()&&Character.isWhitespace(s.charAt(p)))p++;}boolean t(char c){if(p<s.length()&&s.charAt(p)==c){p++;return true;}return false;}void need(char c){if(!t(c))throw new IllegalArgumentException("expected "+c);}}
  }
}
