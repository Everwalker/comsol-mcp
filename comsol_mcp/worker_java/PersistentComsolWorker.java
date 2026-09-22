package comsol_mcp.worker_java;

import com.sun.security.auth.module.NTSystem;
import com.comsol.model.Model;
import com.comsol.model.NumericalFeature;
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
      "getEntryKeys", "getEntryKeyIndex",
      "isActive", "isComplex", "isGeometryMeshDependent", "isInheriting", "isInitialized",
      "label", "location", "locationUri", "mesh", "model", "modelNode", "name", "numerical",
      "param", "physics", "properties", "remove", "rename", "result", "run", "runAll",
      "runNoGen", "save", "selection", "set", "setIndex", "setEntry", "sol", "study", "tag", "tags",
      "timeModified", "title", "update", "varnames", "variable", "all", "entities", "inherit", "named", "material",
      // G3: accessors verified against the installed COMSOL 6.4.0.293 API
      // (javap of apiplugins/com.comsol.api_1.0.0.jar).
      "func", "multiphysics", "pair", "cpl", "coordSystem", "propertyGroup", "extraDim",
      "probe", "view", "getUsedProducts",
      // G3 W13: parameter/variable group handling, function evaluation and
      // import, measure selections, and geometry adjacency accessors (javap of
      // com.comsol.model_1.0.0.jar + the local COMSOL 6.4 knowledge base).
      "group", "move", "evaluate", "evaluateUnit", "evaluateComplex", "scope", "dim", "dimension",
      "functionNames", "importData", "refresh", "measure",
      "getArea", "getVolume", "getLength", "getPerimeter", "getBoundaryArea", "getBoundaryVolume",
      "getBoundingBox", "getNEntities", "getNFiniteVoids", "getVtxCoord", "getVtxDistance",
      "getEdgeAngle", "getAdj", "getSDim", "lengthUnit",
      // G3 W15 (material/physics/multiphysics) and W16 (mesh/study/solver)
      // engine surface, javap-verified by those workstreams (2026-09-20).
      "automatic", "buildTime", "clearMesh", "current", "getDefaultSolnum", "getErrorMessage",
      "getGeomEntities", "getInformationMessage", "getM", "getMaxDimension", "getMaxGrowthRate",
      "getMaxVolume", "getMeanGrowthRate", "getMeanQuality", "getMinQuality", "getMinVolume",
      "getN", "getNStepsBack", "getNnz", "getNumElem", "getNumVertex", "getPNames",
      "getParamNames", "getParamVals", "getQualityDistr", "getQualityMeasure", "getSequenceType",
      "getTypes", "getWarningMessage", "hasError", "hasInformation", "hasProblem",
      "hasProblemOrInformation", "hasProblems", "hasProblemsOrInformation", "hasSecondOrderElements",
      "hasWarning", "isAttached", "isAutomatic", "isComplete", "isEmpty", "isGenConv",
      "isGenIntermediatePlots", "isGenPlots", "isPlotUndefVals", "isStoreCompleteHistory",
      "isStoreSolution", "problem", "problems", "setQualityMeasure", "setSolveFor", "solveFor",
      "type", "hasProperty", "materialType", "addInput", "removeInput", "input",
      // G3 C06 allow-list merge (2026-09-21).  The entries below were dispatched
      // by the G3 modules but were absent here, so the live worker refused them
      // with METHOD_REJECTED (the W16_T018 evidence shows mesh.statistics
      // reporting no counts for exactly that reason).  Each one is javap-verified
      // against the installed API on this machine (com.comsol.api.model jar sha256 9bdc47a9e320be57...):
      //   MeshSequence.stat() -> com.comsol.model.MeshStatistics
      //   MeshSequence.isGeometry() -> boolean   (Geometry vs mesh-mode sequence)
      //   GeomObjectSelection.objects() -> int[]  (entity indices)
      //   GeomObjectSelection.object(int) -> int  (single entity index)
      //   ModelNode.func() -> com.comsol.model.FunctionFeatureList
      "stat", "isGeometry", "objects", "object", "func", "table", "export",
      // G3 sampling: minimal API methods for Interp result extraction
      // (NumericalFeature.setInterpolationCoordinates, getCoordinates, getNData).
      // getCoordinatesShape is a Worker adapter: it calls the native getter but
      // returns only a validated [dimension, point_count] shape, never the
      // coordinate matrix itself.
      "setInterpolationCoordinates", "getCoordinates", "getCoordinatesShape", "getNData",
      // W17: result, numerical and table API methods javap-verified
      // TableBaseFeature.setColumnHeaders(String[]) is present in the local
      // COMSOL 6.4 API probe; keep the setter reachable with its readback
      // getter so table header writes cannot fail at the worker gate.
      "getImagData", "clearTableData", "getColumnHeaders", "setColumnHeaders", "getRowHeaders",
      "getTableData", "getNRows", "setTableData", "addRow", "addRows",
      "setResult", "appendResult", "getFilledReal", "getFilledImag",
      // G3.3 §4 (F04): the SolutionInfo route for real stored-solution
      // metadata.  javap -cp apiplugins/com.comsol.api_1.0.0.jar (installed
      // COMSOL 6.4.0.293):
      //   SolverSequence.getSolutioninfo() -> com.comsol.model.SolutionInfo
      //   SolutionInfo.getOuterSolnum() -> int[]
      //   SolutionInfo.getMaxInner(int[]) -> int
      //   SolutionInfo.getLevelNames() -> java.lang.String[]
      // Published here so dataset.solution_indices reads the outer/inner axes
      // from the engine instead of fabricating outer_indices=[1].
      "getSolutioninfo", "getOuterSolnum", "getMaxInner", "getLevelNames",
      // G3.3 independent 6.4 javap verification: native geometry and per-solution metadata.
      "isAxisymmetric", "getSolnum", "getSolnums", "getPvals", "getUnits", "getUnit",
      "getPNamesOuter", "getPUnitsOuter", "getSolverSequence",
      // W18: plot group and export inspection/execution
      "isPlotGroup", "axis", "camera", "showFrame",
      // ProbeFeature.genResult(String): explicit write, never history-read preparation.
      "genResult"));
  private static final Set<String> MODEL_UTIL = new HashSet<>(Arrays.asList(
      "create", "load", "model", "remove", "tags", "uniquetag", "modelsUsedByOtherClients",
      "getComsolVersion",
      // G3 runtime licence probe: documented, non-seat-consuming query
      // (javap com.comsol.model.util.ModelUtil; KB Programming Reference p.42).
      "hasProduct"));

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
    String payload;
    try {
      payload = Json.write(data);
    } catch (Json.NonFiniteJsonValue failure) {
      // A native result must never escape as invalid NDJSON. Keep the
      // rejection machine-readable so the controller can classify it.
      Map<String, Object> refusal = failure("NON_FINITE_JSON_VALUE", failure, true);
      refusal.put("serialization_failed", true);
      refusal.put("post_dispatch", true);
      if (data.containsKey("request_id")) refusal.put("request_id", data.get("request_id"));
      if (data.containsKey("type")) refusal.put("type", data.get("type"));
      payload = Json.write(refusal);
    }
    writer.write(payload); writer.write("\n"); writer.flush();
  }

  private Map<String, Object> dispatch(Map<String, Object> request) throws Exception {
    String type = string(request.get("type"));
    if ("health".equals(type)) return health();
    if ("status".equals(type)) return status(string(request.get("request_id")));
    if ("codec_selftest".equals(type)) return map("ok", true, "result", codecSelftest());
    if ("reflection_selftest".equals(type)) return map("ok", true, "result", reflectionSelftest());
    if ("marshalling_selftest".equals(type)) return map("ok", true, "result", marshallingSelftest());
    if ("shutdown".equals(type)) return error("PERMISSION_DENIED", "worker shutdown is controlled by its owner process");
    if (!"connect".equals(type) && !"call".equals(type) && !"model".equals(type) && !"modelutil".equals(type) && !"model_snapshot".equals(type) && !"disconnect".equals(type) && !"lock_selftest".equals(type) && !"code_compile".equals(type) && !"code_execute".equals(type) && !"children".equals(type) && !"walk".equals(type))
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
      else if ("children".equals(type)) result = childrenProbe(request);
      else if ("walk".equals(type)) result = walk(request);
      else result = call(request);
      state.succeed(encode(result));
    } catch (WorkerFailure t) {
      Map<String,Object> failure = error(t.code, t.getMessage());
      if (t.details != null) failure.putAll(t.details);
      if ("NON_FINITE_JSON_VALUE".equals(t.code)) {
        // The native call already ran; the result could not be published as
        // JSON, so the caller must retain the dispatched operation as unknown.
        failure.put("execution_state_unknown", true);
        failure.put("post_dispatch", true);
        failure.put("serialization_failed", true);
      }
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
    // C05: a licence query such as ``hasProduct(String...)`` is a varargs call,
    // so a bare scalar argument cannot be converted to its declared
    // ``String[]`` parameter.  The two marshalling refusals are reported as
    // their own codes - an API that the installed jar does not declare is a
    // different fact from an argument shape this Worker cannot marshal - and
    // neither may be read as "the product is not licensed".
    try {
      return invoke(null, ModelUtil.class, method, args);
    } catch (NoSuchMethodException absent) {
      throw new WorkerFailure("MODEL_UTIL_METHOD_ABSENT", absent.getMessage(), map("method", method,
          "argument_count", (long) args.size()));
    } catch (IllegalArgumentException mismatch) {
      throw new WorkerFailure("MODEL_UTIL_ARGUMENT_CONVERSION_FAILED", mismatch.getMessage(),
          map("method", method, "argument_count", (long) args.size()));
    }
  }
  @SuppressWarnings("unchecked")
  private Object call(Map<String, Object> request) throws Exception {
    ensureConnected(); long claimed = number(request.get("generation"), -1);
    if (claimed != generation.get()) throw new IllegalStateException("STALE_WORKER_HANDLE");
    String handle = string(request.get("handle")); Object target = handles.get(handle);
    if (target == null) throw new IllegalArgumentException("UNKNOWN_WORKER_HANDLE");
    String method = string(request.get("method")); if (!METHODS.contains(method)) throw new SecurityException("METHOD_REJECTED");
    List<Object> args = list(request.get("args"));
    if ("getCoordinatesShape".equals(method)) {
      if (!args.isEmpty()) throw new IllegalArgumentException("getCoordinatesShape takes no arguments");
      return getCoordinatesShape(target);
    }
    return invoke(target, target.getClass(), method, args);
  }

  /**
   * Return a native NumericalFeature coordinate shape without putting the
   * coordinate values on the worker wire.  This is intentionally a special
   * call rather than a generic reflection alias: the budget guard needs the
   * point count before getData(), while a full getCoordinates() response would
   * itself materialize and transport the array it is meant to bound.
   */
  private Object getCoordinatesShape(Object target) throws Exception {
    if (!(target instanceof NumericalFeature)) {
      throw new WorkerFailure("COORDINATES_SHAPE_TARGET_INVALID",
          "getCoordinatesShape is only valid for a NumericalFeature handle");
    }
    double[][] coordinates = ((NumericalFeature) target).getCoordinates();
    if (coordinates == null) {
      throw new WorkerFailure("COORDINATES_SHAPE_NULL",
          "NumericalFeature.getCoordinates() returned null");
    }
    if (coordinates.length == 0) {
      throw new WorkerFailure("COORDINATES_SHAPE_EMPTY",
          "NumericalFeature.getCoordinates() returned zero coordinate dimensions");
    }
    int pointCount = -1;
    for (int dimension = 0; dimension < coordinates.length; dimension++) {
      double[] row = coordinates[dimension];
      if (row == null) {
        throw new WorkerFailure("COORDINATES_SHAPE_NULL_ROW",
            "NumericalFeature.getCoordinates() returned a null coordinate row",
            map("dimension", (long) dimension));
      }
      if (pointCount < 0) pointCount = row.length;
      if (row.length != pointCount) {
        throw new WorkerFailure("COORDINATES_SHAPE_RAGGED",
            "NumericalFeature.getCoordinates() returned a ragged matrix",
            map("dimension", (long) dimension, "expected_point_count", (long) pointCount,
                "actual_point_count", (long) row.length));
      }
      for (double value : row) {
        if (!Double.isFinite(value)) {
          throw new WorkerFailure("COORDINATES_SHAPE_NONFINITE",
              "NumericalFeature.getCoordinates() returned a non-finite coordinate",
              map("dimension", (long) dimension));
        }
      }
    }
    if (pointCount <= 0) {
      throw new WorkerFailure("COORDINATES_SHAPE_EMPTY",
          "NumericalFeature.getCoordinates() returned zero points");
    }
    return map("kind", "coordinates_shape",
        "shape", Arrays.asList((long) coordinates.length, (long) pointCount),
        "dimension", (long) coordinates.length,
        "point_count", (long) pointCount,
        "source", "native NumericalFeature.getCoordinates()",
        "values_transmitted", false,
        "wire_payload", "shape-only");
  }

  // ---- G3 R02: batch collection discovery and a deterministic tree walk ----
  // Both commands run inside the serial engine task.  Child objects are kept
  // as transient Java references (never registered as wire handles), so a
  // walk of thousands of nodes does not grow the handle table.

  private Object childrenProbe(Map<String, Object> request) throws Exception {
    ensureConnected();
    requireGeneration(request);
    Object target = requireHandle(request);
    List<Object> errors = new ArrayList<>();
    List<Object> rows = new ArrayList<>();
    for (Object row : probeChildren(target, list(request.get("candidates")), errors)) {
      @SuppressWarnings("unchecked")
      Map<String, Object> wire = new LinkedHashMap<>((Map<String, Object>) row);
      wire.remove("_node");
      rows.add(wire);
    }
    return map("children", rows, "errors", errors);
  }

  private Object walk(Map<String, Object> request) throws Exception {
    ensureConnected();
    requireGeneration(request);
    Object start = requireHandle(request);
    List<Object> candidates = list(request.get("candidates"));
    @SuppressWarnings("unchecked")
    Map<String, Object> query = request.get("query") instanceof Map ? (Map<String, Object>) request.get("query") : new LinkedHashMap<>();
    boolean hasTag = query.containsKey("tag");
    boolean hasType = query.containsKey("type_id") || query.containsKey("type");
    boolean hasLabel = query.containsKey("label");
    String wantedTag = string(query.get("tag"));
    String wantedType = string(query.containsKey("type_id") ? query.get("type_id") : query.get("type"));
    String wantedLabel = string(query.get("label"));
    int maxNodes = (int) number(request.get("max_nodes"), 2000);
    double maxSeconds = request.get("max_seconds") instanceof Number ? ((Number) request.get("max_seconds")).doubleValue() : 20.0;
    int skipVisited = (int) number(request.get("skip_visited"), 0);
    int limit = (int) number(request.get("limit"), 100);
    if (maxNodes < 1 || limit < 1 || skipVisited < 0 || !(maxSeconds > 0)) throw new IllegalArgumentException("walk budget is invalid");
    long deadline = System.currentTimeMillis() + (long) Math.ceil(maxSeconds * 1000.0);
    Deque<Object[]> queue = new ArrayDeque<>();
    queue.add(new Object[]{start, new ArrayList<Object>()});
    int visited = 0;
    int matchedInCall = 0;
    List<Object> matches = new ArrayList<>();
    List<Object> errors = new ArrayList<>();
    String truncated = null;
    boolean complete = false;
    while (true) {
      if (queue.isEmpty()) { complete = true; break; }
      if (visited - skipVisited >= maxNodes) { truncated = "node_budget"; break; }
      if (System.currentTimeMillis() > deadline) { truncated = "time_budget"; break; }
      Object[] item = queue.poll();
      Object node = item[0];
      @SuppressWarnings("unchecked")
      List<Object> segments = (List<Object>) item[1];
      visited++;
      if (visited > skipVisited) {
        String tag = safeInvokeString(node, "tag");
        if ((tag == null || tag.isEmpty()) && !segments.isEmpty()) {
          Object lastSeg = segments.get(segments.size() - 1);
          if (lastSeg instanceof Map) {
            Object segTag = ((Map<?, ?>) lastSeg).get("tag");
            if (segTag != null) tag = String.valueOf(segTag);
          }
        }
        String typeId = safeInvokeString(node, "getType");
        String label = safeInvokeString(node, "label");
        boolean matchesQuery = (!hasTag || wantedTag.equals(tag)) && (!hasType || wantedType.equals(typeId)) && (!hasLabel || wantedLabel.equals(label));
        if (matchesQuery) {
          matchedInCall++;
          matches.add(map("segments", segments, "tag", tag, "type_id", typeId, "label", label));
          if (matches.size() >= limit) { truncated = "match_limit"; break; }
        }
      }
      for (Object child : probeChildren(node, candidates, errors)) {
        @SuppressWarnings("unchecked")
        Map<String, Object> row = (Map<String, Object>) child;
        List<Object> childSegments = new ArrayList<>(segments);
        if (Boolean.TRUE.equals(row.get("accessor"))) childSegments.add(map("accessor", row.get("collection")));
        else childSegments.add(map("collection", row.get("collection"), "tag", row.get("tag")));
        queue.add(new Object[]{row.get("_node"), childSegments});
      }
    }
    return map("matches", matches, "visited", visited, "matched_in_call", matchedInCall,
        "complete", complete, "truncated_reason", truncated, "errors", errors, "generation", generation.get());
  }

  @SuppressWarnings("unchecked")
  private List<Object> probeChildren(Object node, List<Object> candidates, List<Object> errors) {
    List<Object> rows = new ArrayList<>();
    for (Object entry : candidates) {
      if (!(entry instanceof Map)) continue;
      Map<String, Object> spec = (Map<String, Object>) entry;
      String collection = string(spec.get("collection"));
      String method = string(spec.get("method"));
      if (collection.isEmpty() || method.isEmpty()) continue;
      if (!METHODS.contains(method)) {
        continue;
      }
      Object container;
      try { container = invoke(node, node.getClass(), method, Collections.emptyList()); }
      catch (NoSuchMethodException notApplicable) { continue; } // this node does not expose the collection
      catch (Exception exc) {
        if (errors.size() < 50) errors.add(map("collection", collection, "code", "COLLECTION_PROBE_FAILED", "message", exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage())));
        continue;
      }
      List<Object> tags;
      try { tags = list(invoke(container, container.getClass(), "tags", Collections.emptyList())); }
      catch (NoSuchMethodException nodeLike) {
        // A zero-arg accessor that returns a node (for example a Work Plane's
        // inner geometry) rather than a tag container.
        rows.add(map("collection", collection, "accessor", true, "_node", container));
        continue;
      } catch (Exception exc) {
        if (errors.size() < 50) errors.add(map("collection", collection, "code", "TAGS_PROBE_FAILED", "message", exc.getClass().getSimpleName()));
        continue;
      }
      for (Object tagObject : tags) {
        String tag = string(tagObject);
        if (tag.isEmpty()) continue;
        Object child;
        try { child = invoke(container, container.getClass(), "get", Collections.singletonList(tag)); }
        catch (Exception exc) {
          if (errors.size() < 50) errors.add(map("collection", collection, "code", "CHILD_RESOLVE_FAILED", "message", exc.getClass().getSimpleName()));
          continue;
        }
        rows.add(map("collection", collection, "tag", tag, "_node", child));
      }
    }
    return rows;
  }

  private void requireGeneration(Map<String, Object> request) {
    long claimed = number(request.get("generation"), -1);
    if (claimed != generation.get()) throw new IllegalStateException("STALE_WORKER_HANDLE");
  }

  private Object requireHandle(Map<String, Object> request) {
    String handle = string(request.get("handle"));
    Object target = handles.get(handle);
    if (target == null) throw new IllegalArgumentException("UNKNOWN_WORKER_HANDLE");
    return target;
  }

  private String safeInvokeString(Object node, String method) {
    try {
      Object value = invoke(node, node.getClass(), method, Collections.emptyList());
      return value == null ? null : String.valueOf(value);
    } catch (Exception ignored) {
      return null;
    }
  }
  private Object invoke(Object target, Class<?> type, String name, List<Object> args) throws Exception {
    Method selected = null; Object[] selectedArgs = null; int selectedScore = Integer.MAX_VALUE; boolean ambiguous = false;
    boolean named = false; boolean arity = false;
    for (Method method : publicMethods(type)) {
      if (!method.getName().equals(name)) continue;
      named = true;
      if (method.getParameterCount() != args.size()) continue;
      arity = true;
      if (!Modifier.isPublic(method.getModifiers()) || method.getDeclaringClass().equals(Object.class)) continue;
      if (target != null && Modifier.isStatic(method.getModifiers())) continue;
      Conversion converted = convert(method.getParameterTypes(), args); if (converted == null) continue;
      if (converted.score < selectedScore) { selected = method; selectedArgs = converted.values; selectedScore = converted.score; ambiguous = false; }
      else if (converted.score == selectedScore) ambiguous = true;
    }
    // A name the type does not declare, an arity it does not declare, and an
    // argument shape it cannot marshal are three different refusals: the caller
    // has to be able to tell "this build has no such API" from "this call did
    // not marshal" without reading a message string.
    if (selected == null) {
      if (!named) throw new NoSuchMethodException(name + " is not declared by " + type.getName());
      if (!arity) throw new NoSuchMethodException(name + " declares no overload taking " + args.size() + " argument(s)");
      throw new IllegalArgumentException("arguments are not convertible to " + name + "/" + args.size());
    }
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
  /**
   * C05: prove the argument-marshalling path for a varargs licence query without
   * attaching COMSOL.  ``ModelUtil.hasProduct`` is declared as
   * ``hasProduct(java.lang.String...)`` - one ``String[]`` parameter - so the
   * three encodings below are the ones the control plane can send today.  The
   * fixture is a local class, so this runs offline and cannot consume a seat.
   */
  private Object marshallingSelftest() throws Exception {
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("typed_string_array", probeHasProductArgs(Arrays.asList((Object) map(
        "kind", "string", "shape", Arrays.asList(1L), "data", Arrays.asList("ACDC"),
        "java_signature", "java.lang.String[]"))));
    out.put("nested_list", probeHasProductArgs(Arrays.asList((Object) Arrays.asList("ACDC", "HeatTransfer"))));
    out.put("bare_string", probeOutcome(() -> invoke(null, HasProductFixture.class, "hasProduct",
        Arrays.asList((Object) "ACDC"))));
    out.put("signature_mismatch", probeOutcome(() -> invoke(null, HasProductFixture.class, "hasProduct",
        Arrays.asList((Object) map("kind", "string", "shape", Arrays.asList(1L), "data",
            Arrays.asList("ACDC"), "java_signature", "java.lang.String[][]")))));
    out.put("undeclared_method", probeOutcome(() -> invoke(null, HasProductFixture.class, "hasProductForFile",
        Arrays.asList((Object) "model.mph"))));
    out.put("undeclared_arity", probeOutcome(() -> invoke(null, HasProductFixture.class, "hasProduct",
        Arrays.asList((Object) "ACDC", (Object) "HeatTransfer"))));
    return out;
  }
  private Object probeHasProductArgs(List<Object> args) {
    return probeOutcome(() -> invoke(null, HasProductFixture.class, "hasProductArgs", args));
  }
  private Object probeOutcome(Callable<Object> call) {
    try {
      return map("ok", true, "value", call.call());
    } catch (Exception refused) {
      String message = String.valueOf(refused.getMessage());
      String code = refused instanceof NoSuchMethodException ? "MODEL_UTIL_METHOD_ABSENT"
          : refused instanceof IllegalArgumentException ? "MODEL_UTIL_ARGUMENT_CONVERSION_FAILED"
          : "ENGINE_CALL_FAILED";
      return map("ok", false, "code", code, "exception", refused.getClass().getSimpleName(), "message", message);
    }
  }
  public static final class HasProductFixture {
    public static boolean hasProduct(String... product) {
      return product != null && product.length == 1 && "ACDC".equals(product[0]);
    }
    public static List<Object> hasProductArgs(String... product) {
      return new ArrayList<Object>(Arrays.asList((Object[]) product));
    }
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
      // Prefer the least-nested array when an empty (or partial) list matches
      // several array overloads (String[] over String[][]) so that an empty
      // argument is not reported as an ambiguous overload.
      int depth = 0; for (Class<?> component = type.getComponentType(); component != null && component.isArray(); component = component.getComponentType()) depth++;
      return score + depth;
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
    if (value == null || value instanceof String || value instanceof Boolean) return value;
    if (value instanceof Number) {
      if (!Json.isFiniteNumber((Number) value)) {
        throw new WorkerFailure("NON_FINITE_JSON_VALUE", "worker result contains a non-finite number");
      }
      return value;
    }
    if (value instanceof Map) { Map<String,Object> out = new LinkedHashMap<>(); for (Map.Entry<?,?> entry : ((Map<?,?>)value).entrySet()) out.put(String.valueOf(entry.getKey()), encode(entry.getValue())); return out; }
    if (value.getClass().isArray()) { int n = Array.getLength(value); List<Object> out = new ArrayList<>(); for (int i=0;i<n;i++) out.add(encode(Array.get(value,i))); return out; }
    if (value instanceof Collection) { List<Object> out = new ArrayList<>(); for (Object v : (Collection<?>) value) out.add(encode(v)); return out; }
    return handle(value);
  }
  private Map<String, Object> codecSelftest() {
    List<Object> values = Arrays.asList(Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY,
        Arrays.asList(1.0, Double.NaN));
    int rejected = 0;
    for (Object value : values) {
      try {
        encode(map("value", value));
        throw new WorkerFailure("CODEC_SELFTEST_FAILED", "JSON encoder accepted a non-finite number");
      } catch (WorkerFailure expected) {
        if (!"NON_FINITE_JSON_VALUE".equals(expected.code)) throw expected;
      }
      try {
        Json.write(map("value", value));
        throw new WorkerFailure("CODEC_SELFTEST_FAILED", "JSON writer accepted a non-finite number");
      } catch (Json.NonFiniteJsonValue expected) {
        rejected++;
      }
    }
    boolean finite = Json.write(map("finite", Arrays.asList(1, 2.5))).contains("2.5");
    StringWriter wire = new StringWriter();
    try {
      reply(new BufferedWriter(wire), map("ok", true, "request_id", "codec-selftest",
          "result", map("value", Double.NaN)));
    } catch (IOException failure) {
      throw new WorkerFailure("CODEC_SELFTEST_FAILED", "structured failure could not be written");
    }
    Map<String, Object> structured = object(wire.toString().trim());
    boolean structuredFailure = Boolean.FALSE.equals(structured.get("ok"))
        && "NON_FINITE_JSON_VALUE".equals(structured.get("code"))
        && Boolean.TRUE.equals(structured.get("serialization_failed"))
        && Boolean.TRUE.equals(structured.get("execution_state_unknown"))
        && Boolean.TRUE.equals(structured.get("post_dispatch"))
        && "codec-selftest".equals(structured.get("request_id"));
    if (rejected != values.size() || !finite || !structuredFailure)
      throw new WorkerFailure("CODEC_SELFTEST_FAILED", "finite JSON codec contract failed");
    return map("kind", "map", "nested", map("value", 7), "array", Arrays.asList("x", 2),
        "nonfinite_rejected", true, "nonfinite_rejected_count", rejected,
        "nonfinite_code", "NON_FINITE_JSON_VALUE", "finite_passed", finite,
        "structured_failure_verified", structuredFailure);
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
  @SuppressWarnings("unchecked") private static List<Object> list(Object value) {
    if (value instanceof List) return (List<Object>) value;
    if (value instanceof Object[]) return new ArrayList<>(Arrays.asList((Object[]) value));
    if (value != null && value.getClass().isArray()) {
      int len = Array.getLength(value);
      List<Object> out = new ArrayList<>(len);
      for (int i = 0; i < len; i++) out.add(Array.get(value, i));
      return out;
    }
    return Collections.emptyList();
  }
  @SuppressWarnings("unchecked") private static Map<String,Object> object(String input) { Object value=Json.parse(input); if (!(value instanceof Map)) throw new IllegalArgumentException("JSON object required"); return (Map<String,Object>) value; }
  private static long number(Object value, long fallback) { return value instanceof Number ? ((Number)value).longValue() : fallback; }
  private static Map<String,Object> map(Object... values) { Map<String,Object> out=new LinkedHashMap<>(); for(int i=0;i<values.length;i+=2) out.put(String.valueOf(values[i]),values[i+1]); return out; }
  private static Map<String,Object> error(String code,String message) { return map("ok",false,"code",code,"message",message); }
  private static Map<String,Object> failure(String code,Throwable t,boolean unknown) {
    StringBuilder sb = new StringBuilder();
    sb.append(t.getClass().getSimpleName()).append(": ");
    try {
      java.lang.reflect.Method m = t.getClass().getMethod("niceString");
      Object n = m.invoke(t);
      if (n != null && !n.toString().trim().isEmpty()) {
        sb.append(n.toString().trim());
      } else {
        sb.append(String.valueOf(t.getMessage()));
      }
    } catch (Throwable ignored) {
      sb.append(String.valueOf(t.getMessage()));
    }
    if (t.getCause() != null && t.getCause() != t) {
      sb.append(" (cause: ").append(t.getCause().getClass().getSimpleName()).append(": ").append(t.getCause().getMessage()).append(")");
    }
    return map("ok",false,"code",code,"message",sb.toString(),"execution_state_unknown",unknown);
  }
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
      else if(v instanceof Number){if(!isFiniteNumber((Number)v))throw new NonFiniteJsonValue((Number)v);out.append(v);} else if(v instanceof Boolean)out.append(v); else if(v instanceof Map){out.append('{');boolean first=true;for(Map.Entry<?,?> e:((Map<?,?>)v).entrySet()){if(!first)out.append(',');first=false;write(out,String.valueOf(e.getKey()));out.append(':');write(out,e.getValue());}out.append('}');}
      else if(v instanceof Iterable){out.append('[');boolean first=true;for(Object x:(Iterable<?>)v){if(!first)out.append(',');first=false;write(out,x);}out.append(']');} else write(out,String.valueOf(v)); }
    static boolean isFiniteNumber(Number value) {
      if (value instanceof Double) return Double.isFinite((Double) value);
      if (value instanceof Float) return Float.isFinite((Float) value);
      String text = String.valueOf(value);
      return !"NaN".equals(text) && !"Infinity".equals(text) && !"-Infinity".equals(text);
    }
    static final class NonFiniteJsonValue extends IllegalArgumentException {
      final Number value;
      NonFiniteJsonValue(Number value) { super("non-finite JSON number"); this.value=value; }
    }
    static final class Parser { final String s; int p; Parser(String s){this.s=s==null?"":s;} Object value(){ws();Object v=raw();ws();if(p!=s.length())throw new IllegalArgumentException("trailing JSON");return v;} Object raw(){ws();if(p>=s.length())throw new IllegalArgumentException("empty JSON");char c=s.charAt(p);if(c=='{')return obj();if(c=='[')return arr();if(c=='\"')return str();if(s.startsWith("true",p)){p+=4;return true;}if(s.startsWith("false",p)){p+=5;return false;}if(s.startsWith("null",p)){p+=4;return null;}return num();} Map<String,Object> obj(){p++;Map<String,Object>m=new LinkedHashMap<>();ws();if(t('}'))return m;while(true){ws();String k=str();ws();need(':');m.put(k,raw());ws();if(t('}'))return m;need(',');}} List<Object> arr(){p++;List<Object>a=new ArrayList<>();ws();if(t(']'))return a;while(true){a.add(raw());ws();if(t(']'))return a;need(',');}} String str(){need('\"');StringBuilder b=new StringBuilder();while(p<s.length()){char c=s.charAt(p++);if(c=='\"')return b.toString();if(c=='\\'){if(p>=s.length())break;char e=s.charAt(p++);if(e=='n')b.append('\n');else if(e=='r')b.append('\r');else if(e=='t')b.append('\t');else if(e=='\"'||e=='\\'||e=='/')b.append(e);else if(e=='u'){if(p+4>s.length())throw new IllegalArgumentException("bad unicode");b.append((char)Integer.parseInt(s.substring(p,p+4),16));p+=4;}else throw new IllegalArgumentException("bad escape");}else b.append(c);}throw new IllegalArgumentException("unterminated string");} Number num(){int q=p;while(p<s.length()&&"-+0123456789.eE".indexOf(s.charAt(p))>=0)p++;String n=s.substring(q,p);try{return n.contains(".")||n.contains("e")||n.contains("E")?Double.valueOf(n):Long.valueOf(n);}catch(Exception e){throw new IllegalArgumentException("bad number");}}void ws(){while(p<s.length()&&Character.isWhitespace(s.charAt(p)))p++;}boolean t(char c){if(p<s.length()&&s.charAt(p)==c){p++;return true;}return false;}void need(char c){if(!t(c))throw new IllegalArgumentException("expected "+c);}}
  }
}
