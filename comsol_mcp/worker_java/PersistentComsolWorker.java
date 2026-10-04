package comsol_mcp.worker_java;

import com.sun.security.auth.module.NTSystem;
import com.comsol.model.Model;
import com.comsol.model.ModelEntity;
import com.comsol.model.NodeGroup;
import com.comsol.model.NodeGroupList;
import com.comsol.model.PrimitiveModelEntity;
import com.comsol.model.GeomObjectSelection;
import com.comsol.model.MeshSequence;
import com.comsol.model.ModelParam;
import com.comsol.model.NumericalFeature;
import com.comsol.model.ResultParam;
import com.comsol.model.SolverFeature;
import com.comsol.model.XmeshInfo;
import com.comsol.util.exceptions.FlException;
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
  private static final String D2_COMSOL_VERSION = "6.4.0.293";
  private static final Set<String> D2_METADATA_METHODS = new HashSet<>(Arrays.asList(
      "nodeGroup", "tags", "size", "get", "feature", "getAfter",
      "getContainer", "resolveModelPath", "ungroup"));
  private static final long W21_FIELD_MAX_NUMERIC_SCALARS = 65_536L;
  private static final int W21_FIELD_MAX_JSON_BYTES = 8 * 1024 * 1024;
  private static final Set<String> METHODS = new HashSet<>(Arrays.asList(
      "active", "author", "batch", "bem", "clear", "clearAll", "component", "coeff",
      "comments", "create", "createAutoSequences", "dataset", "descr", "disableUpdates",
      "feature", "geom", "get", "getAllowedPropertyValues", "getComsolVersion",
      "getEntityFromModelPath", "getFilePath", "getLastComputationDate",
      "getLastComputationTime", "getLastComputationVersion", "getSize", "getPVals", "getReal",
      "getData", "getImag", "getBoolean", "getBooleanArray", "getBooleanMatrix",
      "getDouble", "getDoubleArray", "getDoubleMatrix", "getInt", "getIntArray", "getIntMatrix",
      "getString", "getStringArray", "getStringMatrix", "getType", "getValueType",
      "getEntryKeys", "getEntryKeyIndex",
      "isActive", "isComplex", "isGeometryMeshDependent", "isInheriting", "isInitialized",
      "label", "location", "locationUri", "mesh", "model", "modelNode", "name", "numerical",
      "param", "physics", "properties", "remove", "rename", "result", "run", "runAll",
      "runNoGen", "resetHist", "save", "selection", "set", "setIndex", "setEntry", "sol", "study", "tag", "tags",
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
      // W21 current-mesh evidence uses only bounded block overloads.  The
      // dispatcher below type-checks MeshSequence and requires an explicit
      // position/count (1..1024), so the generic reflective overload path
      // cannot expose whole-mesh arrays.
      "getVertex", "getElem", "getElemEntity",
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
      // G3 W14 selection initialization: GeomObjectSelection.init(int) is
      // declared by the installed COMSOL 6.4 API; verified with javap against
      // com.comsol.api_1.0.0.jar (sha256 9bdc47a9e320be5721956336f44f5afa4cb06a20cfa887bc7d32d1a837483a67).
      "init",
      // G3 sampling: minimal API methods for Interp result extraction
      // (NumericalFeature.setInterpolationCoordinates, getCoordinates, getNData).
      // getCoordinatesShape is a Worker adapter: it calls the native getter but
      // returns only a validated [dimension, point_count] shape, never the
      // coordinate matrix itself.
      "setInterpolationCoordinates", "getCoordinates", "getCoordinatesShape", "getNData",
      // W21-only bounded field payload adapter. Unlike the generic getters,
      // this returns real/imaginary field values and coordinates only after
      // enforcing a complete-payload scalar and serialized-JSON byte limit.
      "getStrictFieldReadback",
      // W21 initial-output admission reads only the field/count/case summary
      // of one actual Variables feature. The typed adapter performs the
      // documented Variables.xmeshInfo()/clearXmesh() lifecycle and never
      // serializes DOF coordinates or an unbounded XmeshInfo object.
      "getVariablesXmeshReadback",
      // Restart/model inspection needs only the embedded Model.FileResourceList
      // tag inventory. This narrow adapter is type-checked below and returns
      // strings; it does not expose FileResourceList or a generic file() handle.
      // COMSOL 6.4 Model.file()->FileResourceList and inherited tags() were
      // verified by the local 6.4 API docs and javap of apiplugins/
      // com.comsol.api_1.0.0.jar (sha256 9bdc47a9e320be5721956336f44f5afa4cb06a20cfa887bc7d32d1a837483a67).
      "getFileResourceTags",
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
      // W21 selected historical solution→mesh association (COMSOL 6.4
      // Programming Reference p.546/549; javap of the installed public API):
      // SolutionInfo.getISol(outer,inner) returns zero-based [iMulti,iSol], and
      // SolverSequence.getMesh(geometry,iMulti) returns the associated mesh tag.
      // This does not prove topology, DOF, frame, or history equivalence.
      "getISol", "getMesh", "getSolverSequences",
      // W18: plot group, geometry/mesh image, and export inspection/execution
      "isPlotGroup", "axis", "camera", "showFrame", "image", "plot",
      // ProbeFeature.genResult(String): explicit write, never history-read preparation.
      "genResult", "copy", "resolveModelPath"));
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
    if (!"connect".equals(type) && !"call".equals(type) && !"model".equals(type) && !"modelutil".equals(type) && !"license_checkout".equals(type) && !"model_snapshot".equals(type) && !"disconnect".equals(type) && !"lock_selftest".equals(type) && !"code_compile".equals(type) && !"code_execute".equals(type) && !"children".equals(type) && !"walk".equals(type) && !"public_describe".equals(type) && !"g2_entity_identity".equals(type) && !"g2_nodegroup_read".equals(type) && !"g2_nodegroup_ungroup".equals(type))
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
      else if ("license_checkout".equals(type)) result = licenseCheckout(request);
      else if ("code_compile".equals(type)) result = compileJava(request);
      else if ("code_execute".equals(type)) result = executeJava(request);
      else if ("children".equals(type)) result = childrenProbe(request);
      else if ("walk".equals(type)) result = walk(request);
      else if ("public_describe".equals(type)) result = publicDescribe(request);
      else if ("g2_entity_identity".equals(type)) result = g2EntityIdentity(request);
      else if ("g2_nodegroup_read".equals(type)) result = g2NodeGroupRead(request);
      else if ("g2_nodegroup_ungroup".equals(type)) result = g2NodeGroupUngroup(request);
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
      List<String> options = Arrays.asList("-encoding", "UTF-8", "-classpath", System.getProperty("java.class.path", ""), "-d", classes.toString());
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

  /**
   * Explicit, seat-consuming checkout route. This is deliberately a separate
   * Worker command; the generic modelutil allow-list never contains checkout.
   * The operation uses the already-connected client and never connects,
   * disconnects, starts, or stops a COMSOL server.
   */
  private Object licenseCheckout(Map<String, Object> request) throws Exception {
    ensureConnected();
    Object rawProducts = request.get("products");
    if (!(rawProducts instanceof List))
      throw new WorkerFailure("INVALID_REQUEST", "products must be a nonempty array of product tokens",
          map("checkout_dispatched", false));
    List<?> values = (List<?>) rawProducts;
    if (values.isEmpty() || values.size() > 32)
      throw new WorkerFailure("INVALID_REQUEST", "products must contain between 1 and 32 product tokens",
          map("checkout_dispatched", false));
    String[] products = new String[values.size()];
    Set<String> unique = new HashSet<>();
    for (int i = 0; i < values.size(); i++) {
      Object value = values.get(i);
      if (!(value instanceof String))
        throw new WorkerFailure("INVALID_REQUEST", "each product must be a string token",
            map("checkout_dispatched", false));
      String product = (String) value;
      if (!product.matches("[A-Za-z][A-Za-z0-9_]{0,63}") || !unique.add(product))
        throw new WorkerFailure("INVALID_REQUEST", "product tokens must be valid and unique",
            map("checkout_dispatched", false));
      products[i] = product;
    }
    final Method method;
    try {
      // ModelUtil declares checkoutLicense(String...) as checkoutLicense(String[]).
      method = ModelUtil.class.getMethod("checkoutLicense", String[].class);
    } catch (NoSuchMethodException absent) {
      throw new WorkerFailure("MODEL_UTIL_METHOD_ABSENT",
          "the connected runtime does not declare ModelUtil.checkoutLicense(String...)",
          map("checkout_dispatched", false));
    }
    if (!Modifier.isStatic(method.getModifiers()) || method.getReturnType() != boolean.class) {
      throw new WorkerFailure("MODEL_UTIL_SIGNATURE_UNSUPPORTED",
          "the connected runtime checkoutLicense signature does not match the documented boolean method",
          map("checkout_dispatched", false));
    }
    try {
      Object result = method.invoke(null, (Object) products);
      if (!(result instanceof Boolean))
        throw new WorkerFailure("MODEL_UTIL_SIGNATURE_UNSUPPORTED",
            "the connected runtime checkoutLicense did not return a boolean",
            map("checkout_dispatched", true, "execution_state_unknown", true, "post_dispatch", true));
      return map("granted", result, "method", "ModelUtil.checkoutLicense(String...)",
          "product_count", products.length, "checkout_scope", "current_client_session");
    } catch (InvocationTargetException invoked) {
      Throwable cause = invoked.getCause() == null ? invoked : invoked.getCause();
      String message = cause.getMessage() == null ? cause.getClass().getSimpleName() : cause.getMessage();
      throw new WorkerFailure("LICENSE_CHECKOUT_CALL_FAILED", message,
          map("checkout_dispatched", true, "execution_state_unknown", true, "post_dispatch", true), cause);
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
    if ("getStrictFieldReadback".equals(method)) {
      if (!args.isEmpty()) throw new IllegalArgumentException("getStrictFieldReadback takes no arguments");
      return getStrictFieldReadback(target);
    }
    if ("getVariablesXmeshReadback".equals(method)) {
      if (!args.isEmpty()) throw new IllegalArgumentException("getVariablesXmeshReadback takes no arguments");
      return getVariablesXmeshReadback(target);
    }
    if ("getFileResourceTags".equals(method)) {
      if (!args.isEmpty()) throw new IllegalArgumentException("getFileResourceTags takes no arguments");
      return getFileResourceTags(target);
    }
    if (isMeshBlockGetter(method)) {
      if (!(target instanceof MeshSequence)) throw new SecurityException("METHOD_REJECTED");
      validateMeshBlockArguments(method, args);
    }
    if ("init".equals(method)) {
      // This narrowly enables the exact W14 API call. Do not expose another
      // object's unrelated init overload through the name-based dispatcher.
      if (!(target instanceof GeomObjectSelection)) throw new SecurityException("METHOD_REJECTED");
      if (args.size() != 1) {
        throw new IllegalArgumentException("GeomObjectSelection.init requires one integer dimension");
      }
      ((GeomObjectSelection) target).init(requireSelectionDimension(args.get(0)));
      return null;
    }
    // Only the model's typed ModelParam.evaluateComplex(String) path may
    // classify the one observed interpolation-range error as a completed
    // sample failure. ResultParam, overloaded calls, other tags, transport,
    // serialization and every other API exception retain generic UNKNOWN.
    if (target instanceof ModelParam && !(target instanceof ResultParam) && "evaluateComplex".equals(method)
        && args.size() == 1 && args.get(0) instanceof String) {
      return evaluateModelParameterComplex((ModelParam) target, (String) args.get(0),
          string(request.get("request_id")));
    }
    return invoke(target, target.getClass(), method, args);
  }

  private static boolean isMeshBlockGetter(String method) {
    return "getVertex".equals(method) || "getElem".equals(method) || "getElemEntity".equals(method);
  }

  // Package-visible only for the no-engine Java contract harness. The native
  // dispatch invokes this same validator before reflection.
  static void validateMeshBlockArguments(String method, List<?> args) {
    if (!isMeshBlockGetter(method)) throw new IllegalArgumentException("unsupported mesh block getter");
    int expected = "getVertex".equals(method) ? 2 : 3;
    if (args == null || args.size() != expected) {
      throw new IllegalArgumentException("mesh block getter requires explicit position/count arguments");
    }
    int offsetIndex = expected == 2 ? 0 : 1;
    int countIndex = offsetIndex + 1;
    if (expected == 3 && (!(args.get(0) instanceof String) || ((String) args.get(0)).isEmpty())) {
      throw new IllegalArgumentException("mesh element type must be explicit");
    }
    long offset = requireMeshBlockInteger(args.get(offsetIndex), "position");
    long count = requireMeshBlockInteger(args.get(countIndex), "count");
    if (offset < 0 || offset > Integer.MAX_VALUE || count < 1 || count > 1024
        || offset + count > Integer.MAX_VALUE) {
      throw new IllegalArgumentException("mesh block position/count is outside the bounded range");
    }
  }

  private static long requireMeshBlockInteger(Object value, String label) {
    if (!(value instanceof Number)) throw new IllegalArgumentException("mesh block " + label + " must be an integer");
    Number number = (Number) value;
    double asDouble = number.doubleValue();
    long asLong = number.longValue();
    if (!Double.isFinite(asDouble) || asDouble != (double) asLong) {
      throw new IllegalArgumentException("mesh block " + label + " must be an integer");
    }
    return asLong;
  }

  private Object evaluateModelParameterComplex(ModelParam target, String expression,
                                               String nativeRequestId) throws Exception {
    try {
      return target.evaluateComplex(expression);
    } catch (Exception failure) {
      Map<String, Object> terminal = terminalInterpolationRangeFailure(
          target, "evaluateComplex", Collections.<Object>singletonList(expression),
          failure, nativeRequestId);
      if (terminal == null) throw failure;
      throw new WorkerFailure("FUNCTION_EVALUATION_ERROR",
          "ModelParam.evaluateComplex(String) completed with a recognized interpolation range error",
          terminal, failure);
    }
  }

  /**
   * Narrow classifier for one stable, directly observed FlException tag. The
   * caller is the direct typed synchronous ModelParam read boundary, so there
   * is no reflective InvocationTargetException wrapper to interpret. ResultParam
   * is excluded explicitly, including a hypothetical dual-interface receiver.
   * No localized message matching is used. Package visibility exists only so
   * the no-engine Java probe can test all fail-closed predicates against this
   * production helper.
   */
  static Map<String, Object> terminalInterpolationRangeFailure(
      Object target, String method, List<?> args, Throwable failure, String nativeRequestId) {
    if (!(target instanceof ModelParam) || target instanceof ResultParam || !"evaluateComplex".equals(method)
        || args == null || args.size() != 1 || !(args.get(0) instanceof String)
        || failure == null || failure.getClass() != FlException.class
        || !"Interpolation_function_is_out_of_range".equals(failure.getMessage())) {
      return null;
    }
    Map<String, Object> details = new LinkedHashMap<>();
    details.put("classification", "COMSOL_INTERPOLATION_RANGE");
    details.put("terminal_sample_error", true);
    details.put("execution_state_unknown", false);
    details.put("post_dispatch", true);
    details.put("serialization_failed", false);
    details.put("read_only_source", "com.comsol.model.ModelParam.evaluateComplex(String)");
    details.put("method", method);
    details.put("target_contract", ModelParam.class.getName());
    details.put("target_implementation_type", target.getClass().getName());
    details.put("exception_type", failure.getClass().getName());
    details.put("error_tag", failure.getMessage());
    details.put("exception_message", failure.getMessage());
    if (nativeRequestId != null && !nativeRequestId.isEmpty()) {
      details.put("native_request_id", nativeRequestId);
    }
    Throwable cause = failure.getCause();
    if (cause != null) {
      details.put("cause_type", cause.getClass().getName());
      details.put("cause_message", String.valueOf(cause.getMessage()));
    } else {
      details.put("cause_type", null);
      details.put("cause_message", null);
    }
    return details;
  }

  /** Return only the public Model.FileResourceList tag inventory. */
  private Object getFileResourceTags(Object target) throws Exception {
    if (!(target instanceof Model)) {
      throw new WorkerFailure("FILE_RESOURCE_TAGS_TARGET_INVALID",
          "getFileResourceTags is only valid for a Model handle");
    }
    String[] tags = ((Model) target).file().tags();
    return tags == null ? Collections.emptyList() : new ArrayList<String>(Arrays.asList(tags));
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

  /**
   * Return the exact W21 numerical field payload only if its complete JSON
   * representation fits the hard scalar and byte limits. This special path
   * runs before the ordinary Worker response encoder, so an oversized matrix
   * is rejected without crossing the process boundary.
   */
  private Object getStrictFieldReadback(Object target) throws Exception {
    if (!(target instanceof NumericalFeature)) {
      throw new WorkerFailure("FIELD_READBACK_TARGET_INVALID",
          "getStrictFieldReadback is only valid for a NumericalFeature handle");
    }
    NumericalFeature feature = (NumericalFeature) target;
    double[][] coordinates = feature.getCoordinates();
    double[][][] real = feature.getData();
    boolean complex = feature.isComplex();
    double[][][] imaginary = complex ? feature.getImagData() : null;
    return strictFieldPayload(real, imaginary, coordinates, complex,
        W21_FIELD_MAX_NUMERIC_SCALARS, W21_FIELD_MAX_JSON_BYTES);
  }

  /**
   * Read the bounded solved-for-DOF summary of one Variables feature and
   * release the Xmesh object in the same Worker request. COMSOL documents
   * Variables.xmeshInfo() as the solved-for (non-internal) DOFs; unlike the
   * SolverSequence overload it allocates data that must be cleared.
   */
  private Object getVariablesXmeshReadback(Object target) throws Exception {
    if (!(target instanceof SolverFeature)) {
      throw new WorkerFailure("INITIAL_DOF_READBACK_TARGET_INVALID",
          "getVariablesXmeshReadback is only valid for a SolverFeature handle");
    }
    SolverFeature feature = (SolverFeature) target;
    if (!feature.isActive()) {
      return map("status", "INACTIVE", "feature_tag", feature.tag(),
          "feature_active", false, "cleanup", "NOT_CREATED");
    }
    XmeshInfo info = null;
    try {
      info = feature.xmeshInfo();
      if (info == null) {
        throw new WorkerFailure("INITIAL_DOF_READBACK_UNAVAILABLE",
            "Variables.xmeshInfo() returned null");
      }
      String[] meshCases = info.meshCases();
      String[] fieldNames = info.fieldNames();
      int[] fieldDofs = info.fieldNDofs();
      String[] geometries = info.geoms();
      int totalDofs = info.nDofs();
      if (meshCases == null || meshCases.length == 0 || meshCases.length > 64
          || fieldNames == null || fieldNames.length == 0 || fieldNames.length > 256
          || fieldDofs == null || fieldDofs.length != fieldNames.length
          || geometries == null || geometries.length == 0 || geometries.length > 64
          || totalDofs < 0) {
        throw new WorkerFailure("INITIAL_DOF_READBACK_INVALID_SHAPE",
            "Variables.xmeshInfo() returned an incomplete or unbounded summary");
      }
      long summedDofs = 0L;
      List<Integer> counts = new ArrayList<>();
      for (int index = 0; index < fieldNames.length; index++) {
        if (fieldNames[index] == null || fieldNames[index].isEmpty() || fieldDofs[index] < 0) {
          throw new WorkerFailure("INITIAL_DOF_READBACK_INVALID_SHAPE",
              "Variables.xmeshInfo() returned an invalid field name or DOF count");
        }
        summedDofs += fieldDofs[index];
        counts.add(fieldDofs[index]);
      }
      if (summedDofs != (long) totalDofs) {
        throw new WorkerFailure("INITIAL_DOF_READBACK_INVALID_SHAPE",
            "Variables.xmeshInfo() per-field counts do not sum to nDofs");
      }
      for (String value : meshCases) {
        if (value == null || value.isEmpty()) throw new WorkerFailure(
            "INITIAL_DOF_READBACK_INVALID_SHAPE", "mesh case tag is empty");
      }
      for (String value : geometries) {
        if (value == null || value.isEmpty()) throw new WorkerFailure(
            "INITIAL_DOF_READBACK_INVALID_SHAPE", "geometry tag is empty");
      }
      return map("status", "VERIFIED", "feature_tag", feature.tag(),
          "feature_active", true, "scope", "variables_solved_for_dofs",
          "mesh_cases", Arrays.asList(meshCases), "n_dofs", totalDofs,
          "field_names", Arrays.asList(fieldNames), "field_n_dofs", counts,
          "geometries", Arrays.asList(geometries), "cleanup", "clearXmesh");
    } finally {
      if (info != null) feature.clearXmesh();
    }
  }

  /** Package-visible for the offline Java contract harness; production uses fixed limits above. */
  static Map<String, Object> strictFieldPayload(
      double[][][] real, double[][][] imaginary, double[][] coordinates,
      boolean complex, long maxNumericScalars, int maxJsonBytes) {
    if (real == null || real.length == 0 || coordinates == null || coordinates.length == 0
        || maxNumericScalars < 1 || maxJsonBytes < 1) {
      throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE",
          "field and coordinate arrays must be nonempty and the response limits positive");
    }
    int expressions = real.length;
    int solutions = -1;
    int points = -1;
    for (int expression = 0; expression < expressions; expression++) {
      double[][] bySolution = real[expression];
      if (bySolution == null || bySolution.length == 0) {
        throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE", "real field data contains an empty solution axis");
      }
      if (solutions < 0) solutions = bySolution.length;
      if (bySolution.length != solutions) {
        throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE", "real field data has a ragged solution axis");
      }
      for (double[] row : bySolution) {
        if (row == null || row.length == 0) {
          throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE", "real field data contains an empty point axis");
        }
        if (points < 0) points = row.length;
        if (row.length != points) {
          throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE", "real field data has a ragged point axis");
        }
        for (double value : row) {
          if (!Double.isFinite(value)) {
            throw new WorkerFailure("FIELD_READBACK_INVALID_NUMERIC", "real field data contains a non-finite value");
          }
        }
      }
    }
    if (complex != (imaginary != null)) {
      throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE", "complex flag and imaginary field array disagree");
    }
    if (imaginary != null) {
      if (imaginary.length != expressions) {
        throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE", "imaginary field expression count differs from real data");
      }
      for (int expression = 0; expression < expressions; expression++) {
        if (imaginary[expression] == null || imaginary[expression].length != solutions) {
          throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE", "imaginary field solution count differs from real data");
        }
        for (int solution = 0; solution < solutions; solution++) {
          double[] row = imaginary[expression][solution];
          if (row == null || row.length != points) {
            throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE", "imaginary field point count differs from real data");
          }
          for (double value : row) {
            if (!Double.isFinite(value)) {
              throw new WorkerFailure("FIELD_READBACK_INVALID_NUMERIC", "imaginary field data contains a non-finite value");
            }
          }
        }
      }
    }
    for (double[] row : coordinates) {
      if (row == null || row.length != points) {
        throw new WorkerFailure("FIELD_READBACK_INVALID_SHAPE",
            "coordinate point count differs from the selected field data");
      }
      for (double value : row) {
        if (!Double.isFinite(value)) {
          throw new WorkerFailure("FIELD_READBACK_INVALID_NUMERIC", "coordinate matrix contains a non-finite value");
        }
      }
    }
    long fieldScalars = (long) expressions * (long) solutions * (long) points * (complex ? 2L : 1L);
    long coordinateScalars = (long) coordinates.length * (long) points;
    long totalScalars = fieldScalars + coordinateScalars;
    if (totalScalars > maxNumericScalars) {
      throw new WorkerFailure("FIELD_READBACK_LIMIT_EXCEEDED",
          "real/imaginary field values and coordinates exceed the numeric scalar limit",
          map("numeric_scalar_count", totalScalars, "max_numeric_scalars", maxNumericScalars));
    }

    List<Object> realJson = toJson(real);
    List<Object> imaginaryJson = imaginary == null ? null : toJson(imaginary);
    List<Object> coordinatesJson = toJson(coordinates);
    Map<String, Object> payload = map(
        "real", realJson,
        "imag", imaginaryJson,
        "coordinates", coordinatesJson,
        "is_complex", complex,
        "layout", "expression,solnum,point",
        "shape", Arrays.asList((long) expressions, (long) solutions, (long) points),
        "numeric_scalar_count", totalScalars,
        "json_payload_bytes", 0L);
    String serialized;
    try {
      // This is the exact bounded data payload; only a small Worker response
      // envelope is added after this check.
      for (int attempt = 0; attempt < 4; attempt++) {
        serialized = Json.write(payload);
        int byteCount = serialized.getBytes(StandardCharsets.UTF_8).length;
        if (number(payload.get("json_payload_bytes"), -1) == byteCount) break;
        payload.put("json_payload_bytes", (long) byteCount);
      }
      serialized = Json.write(payload);
    } catch (Json.NonFiniteJsonValue failure) {
      throw new WorkerFailure("FIELD_READBACK_INVALID_NUMERIC",
          "strict field payload cannot be encoded as finite JSON");
    }
    int payloadBytes = serialized.getBytes(StandardCharsets.UTF_8).length;
    if (payloadBytes > maxJsonBytes) {
      throw new WorkerFailure("FIELD_READBACK_LIMIT_EXCEEDED",
          "strict field JSON payload exceeds the byte limit",
          map("json_payload_bytes", (long) payloadBytes, "max_json_payload_bytes", (long) maxJsonBytes));
    }
    payload.put("json_payload_bytes", (long) payloadBytes);
    return payload;
  }

  private static List<Object> toJson(double[][][] values) {
    List<Object> expressions = new ArrayList<>();
    for (double[][] expression : values) {
      List<Object> solutions = new ArrayList<>();
      for (double[] solution : expression) {
        List<Object> points = new ArrayList<>();
        for (double value : solution) points.add(value);
        solutions.add(points);
      }
      expressions.add(solutions);
    }
    return expressions;
  }

  private static List<Object> toJson(double[][] values) {
    List<Object> dimensions = new ArrayList<>();
    for (double[] dimension : values) {
      List<Object> points = new ArrayList<>();
      for (double value : dimension) points.add(value);
      dimensions.add(points);
    }
    return dimensions;
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

  private static void requireD2RequestFields(Map<String, Object> request,
      Set<String> required, Set<String> payloadFields) {
    Set<String> allowed = new HashSet<>(required);
    allowed.addAll(payloadFields);
    allowed.add("type");
    allowed.add("request_id");
    if (request.containsKey("queue_timeout_ms")) allowed.add("queue_timeout_ms");
    if (!request.keySet().containsAll(required) || !allowed.containsAll(request.keySet()))
      throw new WorkerFailure("INVALID_REQUEST", "D2 private command contains missing or unknown fields");
    if (!(request.get("request_id") instanceof String) || ((String) request.get("request_id")).isEmpty())
      throw new WorkerFailure("INVALID_REQUEST", "D2 private command requires a nonempty request_id");
    if (request.containsKey("queue_timeout_ms")) {
      Object timeout = request.get("queue_timeout_ms");
      if (!(timeout instanceof Long) || ((Long) timeout).longValue() < 0L)
        throw new WorkerFailure("INVALID_REQUEST", "queue_timeout_ms must be an exact nonnegative integer");
    }
  }

  private static long requireD2Generation(Map<String, Object> request) {
    Object value = request.get("generation");
    if (!(value instanceof Long) || ((Long) value).longValue() < 1L)
      throw new WorkerFailure("INVALID_REQUEST", "generation must be an exact positive integer");
    return ((Long) value).longValue();
  }

  private static String requireD2String(Map<String, Object> request, String field) {
    Object value = request.get(field);
    if (!(value instanceof String) || ((String) value).isEmpty())
      throw new WorkerFailure("INVALID_REQUEST", field + " must be a nonempty string");
    return (String) value;
  }

  private static List<?> requireD2Args(Map<String, Object> request) {
    Object value = request.get("args");
    if (!(value instanceof List<?>)) throw new WorkerFailure("INVALID_REQUEST", "args must be a JSON array");
    return (List<?>) value;
  }

  private static void requireD2Signature(Class<?> owner, String name, Class<?> result, Class<?>... parameters) {
    try {
      Method method = owner.getMethod(name, parameters);
      if (method.getReturnType() != result)
        throw new WorkerFailure("API_UNSUPPORTED", "D2 API return type differs from its reviewed signature");
    } catch (NoSuchMethodException failure) {
      throw new WorkerFailure("API_UNSUPPORTED", "D2 API signature is absent from the compiled COMSOL interface", null, failure);
    }
  }

  private static String requireD2RuntimeVersion() {
    String actual = ModelUtil.getComsolVersion();
    if (!D2_COMSOL_VERSION.equals(actual))
      throw new WorkerFailure("API_UNSUPPORTED", "D2 API route requires COMSOL " + D2_COMSOL_VERSION);
    return actual;
  }

  private Object g2EntityIdentity(Map<String, Object> request) {
    ensureConnected();
    requireD2RequestFields(request, new HashSet<>(Arrays.asList("left_handle", "right_handle", "generation")), Collections.emptySet());
    long claimed = requireD2Generation(request);
    if (claimed != generation.get()) throw new WorkerFailure("STALE_WORKER_HANDLE", "D2 identity generation is stale");
    String leftHandle = requireD2String(request, "left_handle");
    String rightHandle = requireD2String(request, "right_handle");
    Object left = handles.get(leftHandle);
    Object right = handles.get(rightHandle);
    if (left == null || right == null)
      throw new WorkerFailure("UNKNOWN_WORKER_HANDLE", "D2 identity requires two current non-null handles");
    if (!(left instanceof PrimitiveModelEntity) || !(right instanceof PrimitiveModelEntity))
      throw new WorkerFailure("API_UNSUPPORTED", "D2 identity operands must implement PrimitiveModelEntity");
    // Do not query COMSOL metadata or properties here. Java reference identity
    // is deliberately the sole witness carried by this private command.
    return map("same_reference", left == right, "generation", claimed,
        "identity_scope", "java_reference_identity");
  }

  private Object g2NodeGroupRead(Map<String, Object> request) throws Exception {
    ensureConnected();
    requireD2RequestFields(request, new HashSet<>(Arrays.asList("handle", "generation", "method", "args")), Collections.emptySet());
    long claimed = requireD2Generation(request);
    if (claimed != generation.get()) throw new WorkerFailure("STALE_WORKER_HANDLE", "D2 read generation is stale");
    String handle = requireD2String(request, "handle");
    String method = requireD2String(request, "method");
    Object target = handles.get(handle);
    if (target == null) throw new WorkerFailure("UNKNOWN_WORKER_HANDLE", "D2 read handle is absent or stale");
    List<?> args = requireD2Args(request);
    String receiver;
    String returnType;
    List<String> parameterTypes = new ArrayList<>();
    Object value;
    if ("nodeGroup".equals(method)) {
      if (!(target instanceof Model) || (args.size() != 0 && args.size() != 1))
        throw new WorkerFailure("API_UNSUPPORTED", "nodeGroup read requires Model.nodeGroup() or Model.nodeGroup(String)");
      String version = requireD2RuntimeVersion();
      receiver = Model.class.getName();
      if (args.isEmpty()) {
        requireD2Signature(Model.class, "nodeGroup", NodeGroupList.class);
        value = ((Model) target).nodeGroup();
        returnType = NodeGroupList.class.getName();
      } else {
        if (!(args.get(0) instanceof String) || ((String) args.get(0)).isEmpty())
          throw new WorkerFailure("INVALID_REQUEST", "nodeGroup(String) requires one nonempty string tag");
        requireD2Signature(Model.class, "nodeGroup", NodeGroup.class, String.class);
        parameterTypes.add(String.class.getName());
        value = ((Model) target).nodeGroup((String) args.get(0));
        returnType = NodeGroup.class.getName();
      }
      if (value == null || (args.isEmpty() ? !(value instanceof NodeGroupList) : !(value instanceof NodeGroup)))
        throw new WorkerFailure("API_UNSUPPORTED", "Model.nodeGroup returned an unexpected public receiver type");
      return map("generation", claimed, "receiver_interface", receiver, "method", method,
          "parameters", parameterTypes, "return_type", returnType, "runtime_version", version,
          "value", encode(value));
    }
    String version = requireD2RuntimeVersion();
    if ("tags".equals(method)) {
      if (!(target instanceof NodeGroupList) || !args.isEmpty()) throw new WorkerFailure("API_UNSUPPORTED", "tags read requires NodeGroupList.tags()");
      requireD2Signature(NodeGroupList.class, "tags", String[].class);
      String[] tags = ((NodeGroupList) target).tags();
      if (tags == null) throw new WorkerFailure("EXECUTION_STATE_UNKNOWN", "NodeGroupList.tags returned null");
      Set<String> unique = new HashSet<>();
      for (String tag : tags) if (tag == null || tag.isEmpty() || !unique.add(tag))
        throw new WorkerFailure("EXECUTION_STATE_UNKNOWN", "NodeGroupList.tags returned an empty or duplicate tag");
      receiver = NodeGroupList.class.getName(); returnType = String[].class.getName(); value = tags;
    } else if ("size".equals(method)) {
      if (!(target instanceof NodeGroup) || !args.isEmpty()) throw new WorkerFailure("API_UNSUPPORTED", "size read requires NodeGroup.size()");
      requireD2Signature(NodeGroup.class, "size", int.class);
      receiver = NodeGroup.class.getName(); returnType = int.class.getName(); value = Integer.valueOf(((NodeGroup) target).size());
    } else if ("get".equals(method)) {
      if (!(target instanceof NodeGroup) || args.size() != 1 || !(args.get(0) instanceof Long))
        throw new WorkerFailure("API_UNSUPPORTED", "get read requires NodeGroup.get(int) with an exact integer");
      long index = ((Long) args.get(0)).longValue();
      if (index < 0L || index > Integer.MAX_VALUE) throw new WorkerFailure("INVALID_REQUEST", "NodeGroup.get index is out of range");
      requireD2Signature(NodeGroup.class, "get", ModelEntity.class, int.class);
      receiver = NodeGroup.class.getName(); returnType = ModelEntity.class.getName();
      parameterTypes.add(int.class.getName()); value = ((NodeGroup) target).get((int) index);
      if (!(value instanceof ModelEntity) || !(value instanceof PrimitiveModelEntity))
        throw new WorkerFailure("API_UNSUPPORTED", "NodeGroup.get returned a non-primitive ModelEntity");
    } else if ("feature".equals(method)) {
      if (!(target instanceof NodeGroup) || !args.isEmpty()) throw new WorkerFailure("API_UNSUPPORTED", "feature read requires NodeGroup.feature()");
      requireD2Signature(NodeGroup.class, "feature", NodeGroupList.class);
      receiver = NodeGroup.class.getName(); returnType = NodeGroupList.class.getName(); value = ((NodeGroup) target).feature();
      if (!(value instanceof NodeGroupList)) throw new WorkerFailure("API_UNSUPPORTED", "NodeGroup.feature returned an unexpected receiver type");
    } else if ("getAfter".equals(method)) {
      if (!(target instanceof NodeGroup) || !args.isEmpty()) throw new WorkerFailure("API_UNSUPPORTED", "getAfter read requires NodeGroup.getAfter()");
      requireD2Signature(NodeGroup.class, "getAfter", ModelEntity.class);
      receiver = NodeGroup.class.getName(); returnType = ModelEntity.class.getName(); value = ((NodeGroup) target).getAfter();
      if (value != null && (!(value instanceof ModelEntity) || !(value instanceof PrimitiveModelEntity)))
        throw new WorkerFailure("API_UNSUPPORTED", "NodeGroup.getAfter returned an unexpected entity type");
    } else if ("getContainer".equals(method)) {
      if (!(target instanceof PrimitiveModelEntity) || !args.isEmpty()) throw new WorkerFailure("API_UNSUPPORTED", "getContainer read requires PrimitiveModelEntity.getContainer()");
      requireD2Signature(PrimitiveModelEntity.class, "getContainer", PrimitiveModelEntity.class);
      receiver = PrimitiveModelEntity.class.getName(); returnType = PrimitiveModelEntity.class.getName(); value = ((PrimitiveModelEntity) target).getContainer();
      if (value != null && !(value instanceof PrimitiveModelEntity)) throw new WorkerFailure("API_UNSUPPORTED", "getContainer returned an unexpected receiver type");
    } else if ("resolveModelPath".equals(method)) {
      if (!(target instanceof PrimitiveModelEntity) || !args.isEmpty()) throw new WorkerFailure("API_UNSUPPORTED", "resolveModelPath read requires PrimitiveModelEntity.resolveModelPath()");
      requireD2Signature(PrimitiveModelEntity.class, "resolveModelPath", String.class);
      receiver = PrimitiveModelEntity.class.getName(); returnType = String.class.getName(); value = ((PrimitiveModelEntity) target).resolveModelPath();
      if (value != null && !(value instanceof String)) throw new WorkerFailure("API_UNSUPPORTED", "resolveModelPath returned a non-string value");
    } else {
      throw new WorkerFailure("API_UNSUPPORTED", "method is not in the private D2 read table");
    }
    return map("generation", claimed, "receiver_interface", receiver, "method", method,
        "parameters", parameterTypes, "return_type", returnType, "runtime_version", version,
        "value", encode(value));
  }

  private Object g2NodeGroupUngroup(Map<String, Object> request) throws Exception {
    ensureConnected();
    requireD2RequestFields(request, new HashSet<>(Arrays.asList("handle", "generation", "tag")), Collections.emptySet());
    long claimed = requireD2Generation(request);
    if (claimed != generation.get()) throw new WorkerFailure("STALE_WORKER_HANDLE", "D2 mutation generation is stale");
    String handle = requireD2String(request, "handle");
    String tag = requireD2String(request, "tag");
    Object target = handles.get(handle);
    if (!(target instanceof NodeGroupList)) throw new WorkerFailure("API_UNSUPPORTED", "ungroup requires an actual NodeGroupList receiver");
    String version = requireD2RuntimeVersion();
    requireD2Signature(NodeGroupList.class, "ungroup", void.class, String.class);
    requireD2Signature(NodeGroupList.class, "tags", String[].class);
    String[] tags = ((NodeGroupList) target).tags();
    boolean found = false;
    if (tags != null) for (String item : tags) if (tag.equals(item)) { found = true; break; }
    if (!found) throw new WorkerFailure("NODE_NOT_FOUND", "target NodeGroup tag is no longer present",
        map("ungroup_dispatched", false, "generation", claimed));
    try {
      ((NodeGroupList) target).ungroup(tag);
    } catch (Throwable failure) {
      String message = failure.getMessage() == null ? failure.getClass().getSimpleName() : failure.getMessage();
      throw new WorkerFailure("NODEGROUP_UNGROUP_FAILED", message,
          map("ungroup_dispatched", true, "execution_state_unknown", true, "post_dispatch", true,
              "generation", claimed, "semantic_operation", "NodeGroupList.ungroup(String)"), failure);
    }
    return map("ungroup_dispatched", true, "generation", claimed, "tag", tag,
        "semantic_operation", "NodeGroupList.ungroup(String)", "runtime_version", version);
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
  private static void publicInterfaces(Class<?> type, Set<Class<?>> out) {
    if (type == null) return;
    for (Class<?> iface : type.getInterfaces()) {
      if (Modifier.isPublic(iface.getModifiers()) && iface.getName().startsWith("com.comsol.model.")) out.add(iface);
      publicInterfaces(iface, out);
    }
    publicInterfaces(type.getSuperclass(), out);
  }
  private Object publicDescribe(Map<String, Object> request) {
    ensureConnected();
    if (number(request.get("generation"), -1) != generation.get()) throw new IllegalStateException("STALE_WORKER_HANDLE");
    Object target = handles.get(string(request.get("handle")));
    if (target == null) throw new IllegalArgumentException("UNKNOWN_WORKER_HANDLE");
    Map<String, Object> descriptor = publicReceiverMetadata(target.getClass());
    descriptor.put("runtime_version", ModelUtil.getComsolVersion());
    return descriptor;
  }
  private static Map<String, Object> publicReceiverMetadata(Class<?> type) {
    Set<Class<?>> interfaces = new TreeSet<>(Comparator.comparing(Class::getName));
    publicInterfaces(type, interfaces);
    List<String> names = new ArrayList<>(); List<Object> methods = new ArrayList<>();
    for (Class<?> iface : interfaces) {
      names.add(iface.getName());
      for (Method method : iface.getMethods()) {
        if ((!METHODS.contains(method.getName()) && !D2_METADATA_METHODS.contains(method.getName())) || Modifier.isStatic(method.getModifiers())) continue;
        List<String> parameters = new ArrayList<>();
        for (Class<?> parameter : method.getParameterTypes()) parameters.add(parameter.getName());
        methods.add(map("interface", iface.getName(), "method", method.getName(), "parameters", parameters, "returns", method.getReturnType().getName()));
      }
    }
    return map("interfaces", names, "methods", methods);
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
  private static int requireSelectionDimension(Object value) {
    if (!(value instanceof Long || value instanceof Integer)) {
      throw new IllegalArgumentException("GeomObjectSelection.init requires one integer dimension");
    }
    long dimension = ((Number) value).longValue();
    if (dimension != 2L && dimension != 3L) {
      throw new IllegalArgumentException("GeomObjectSelection.init dimension must be 2 or 3");
    }
    return (int) dimension;
  }
  private Map<String, Object> selectionInitDimensionSelftest() {
    return map("valid_2", requireSelectionDimension(Long.valueOf(2L)) == 2,
        "valid_3", requireSelectionDimension(Integer.valueOf(3)) == 3,
        "oversized_rejected", rejectsSelectionDimension(Long.valueOf(4294967298L)),
        "negative_rejected", rejectsSelectionDimension(Long.valueOf(-1L)),
        "fractional_rejected", rejectsSelectionDimension(Double.valueOf(2.0)));
  }
  private static boolean rejectsSelectionDimension(Object value) {
    try {
      requireSelectionDimension(value);
      return false;
    } catch (IllegalArgumentException expected) {
      return true;
    }
  }
  private Object reflectionSelftest() throws Exception {
    return map("duplicate_interface_tag", invoke(new DuplicateTagFixture(), DuplicateTagFixture.class, "tag", Collections.emptyList()),
        "numerical_allowed", METHODS.contains("numerical"),
        "selection_init_dimension", selectionInitDimensionSelftest());
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
  static final class WorkerFailure extends RuntimeException {
    final String code; final Map<String,Object> details;
    WorkerFailure(String code,String message){this(code,message,null);}
    WorkerFailure(String code,String message,Map<String,Object> details){super(message);this.code=code;this.details=details;}
    WorkerFailure(String code,String message,Map<String,Object> details,Throwable cause){super(message,cause);this.code=code;this.details=details;}
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
    static final class Parser { final String s; int p; Parser(String s){this.s=s==null?"":s;} Object value(){ws();Object v=raw();ws();if(p!=s.length())throw new IllegalArgumentException("trailing JSON");return v;} Object raw(){ws();if(p>=s.length())throw new IllegalArgumentException("empty JSON");char c=s.charAt(p);if(c=='{')return obj();if(c=='[')return arr();if(c=='\"')return str();if(s.startsWith("true",p)){p+=4;return true;}if(s.startsWith("false",p)){p+=5;return false;}if(s.startsWith("null",p)){p+=4;return null;}return num();} Map<String,Object> obj(){p++;Map<String,Object>m=new LinkedHashMap<>();ws();if(t('}'))return m;while(true){ws();String k=str();ws();need(':');m.put(k,raw());ws();if(t('}'))return m;need(',');}} List<Object> arr(){p++;List<Object>a=new ArrayList<>();ws();if(t(']'))return a;while(true){a.add(raw());ws();if(t(']'))return a;need(',');}} String str(){need('\"');StringBuilder b=new StringBuilder();while(p<s.length()){char c=s.charAt(p++);if(c=='\"')return b.toString();if(c=='\\'){if(p>=s.length())break;char e=s.charAt(p++);if(e=='n')b.append('\n');else if(e=='r')b.append('\r');else if(e=='t')b.append('\t');else if(e=='\"'||e=='\\'||e=='/')b.append(e);else if(e=='u'){if(p+4>s.length())throw new IllegalArgumentException("bad unicode");b.append((char)Integer.parseInt(s.substring(p,p+4),16));p+=4;}else throw new IllegalArgumentException("bad escape");}else b.append(c);}throw new IllegalArgumentException("unterminated string");} Number num(){int q=p;while(p<s.length()&&"-+0123456789.eE".indexOf(s.charAt(p))>=0)p++;String n=s.substring(q,p);try{if(n.contains(".")||n.contains("e")||n.contains("E"))return Double.valueOf(n);return Long.valueOf(n);}catch(Exception e){throw new IllegalArgumentException("bad number");}}void ws(){while(p<s.length()&&Character.isWhitespace(s.charAt(p)))p++;}boolean t(char c){if(p<s.length()&&s.charAt(p)==c){p++;return true;}return false;}void need(char c){if(!t(c))throw new IllegalArgumentException("expected "+c);}}
  }
}
