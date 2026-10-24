package comsol_mcp.worker_java;

import com.sun.security.auth.module.NTSystem;
import com.comsol.model.Model;
import com.comsol.model.ModelEntity;
import com.comsol.model.ModelEntityList;
import com.comsol.model.NodeGroup;
import com.comsol.model.NodeGroupList;
import com.comsol.model.PrimitiveModelEntity;
import com.comsol.model.GeomObjectSelection;
import com.comsol.model.GeomSequence;
import com.comsol.model.MaterialList;
import com.comsol.model.MaterialModel;
import com.comsol.model.MeshSequence;
import com.comsol.model.ModelParam;
import com.comsol.model.physics.Physics;
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
import java.nio.charset.CodingErrorAction;
import java.nio.charset.CharacterCodingException;
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
import javax.tools.SimpleJavaFileObject;
import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.BlockTree;
import com.sun.source.tree.ImportTree;
import com.sun.source.tree.MemberReferenceTree;
import com.sun.source.tree.MethodInvocationTree;
import com.sun.source.tree.Tree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.TreePath;
import com.sun.source.util.TreePathScanner;
import com.sun.source.util.Trees;
import javax.lang.model.element.Element;
import javax.lang.model.element.ExecutableElement;
import javax.lang.model.element.TypeElement;
import java.nio.file.attribute.BasicFileAttributes;

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
  private static final int PUBLIC_METADATA_INTERFACE_LIMIT = 1024;
  private static final int PUBLIC_METADATA_METHOD_LIMIT = 8192;
  private static final int PUBLIC_METADATA_SCAN_LIMIT = 65536;
  private static final String D2_COMSOL_VERSION = "6.4.0.293";
  private static final String D2_VERSION_COMPONENT = "(?:0|[1-9][0-9]*)";
  private static final java.util.regex.Pattern D2_CANONICAL_VERSION = java.util.regex.Pattern.compile(
      D2_VERSION_COMPONENT + "\\." + D2_VERSION_COMPONENT + "\\." + D2_VERSION_COMPONENT + "\\." + D2_VERSION_COMPONENT);
  private static final java.util.regex.Pattern D2_BUILD_DECLARATION = java.util.regex.Pattern.compile(
      "COMSOL Multiphysics (" + D2_VERSION_COMPONENT + "\\." + D2_VERSION_COMPONENT
          + "(?:\\." + D2_VERSION_COMPONENT + ")?) \\((?:开发版本|Build): (" + D2_VERSION_COMPONENT + ")\\)");
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
      "getLastComputationTime", "getLastComputationVersion", "getSize", "getSizeMulti", "getNU", "getPVals", "getReal",
      "getData", "getImag", "getBoolean", "getBooleanArray", "getBooleanMatrix",
      "getDouble", "getDoubleArray", "getDoubleMatrix", "getInt", "getIntArray", "getIntMatrix",
      "getString", "getStringArray", "getStringMatrix", "getType", "getValueType",
      "getEntryKeys", "getEntryKeyIndex", "identifier",
      // G3 geometry export/repair and registered material import.  COMSOL
      // 6.4 public API docs: GeomSequence.objectNames()/exportFinal(String)
      // (GeomSequence.html SHA-256 047a6aeebc08362e81db383f1b758b688303a702b26a7d020e18146fa63ad661)
      // and MaterialList.insert(String,String[]) (MaterialList.html SHA-256
      // 3ae0833653f79722a163e80c41abf4cc8bd7fa890179862d879d576f7a91190c).
      "objectNames", "exportFinal", "insert",
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
      // Model delivery uses the public FileResource.archive() member only;
      // resource() exposes an ephemeral extraction path and is deliberately
      // not called by this typed inventory adapter.
      "getFileResourceInventory", "saveModelPackage",
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
      // SolverSequence.clearSolutionData() is the only clear route used by
      // solver.solution_clear; the adapter requires exactly one sol:<tag>
      // NodePath and verifies isEmpty()/isInitialized() after dispatch.
      // COMSOL 6.4 API reference: SolverSequence.clearSolutionData().
      "getSolutioninfo", "getOuterSolnum", "getMaxInner", "getLevelNames", "getLevels", "clearSolutionData",
      // G3.3 independent 6.4 javap verification: native geometry and per-solution metadata.
      "isAxisymmetric", "getSolnum", "getSolnums", "getPvals", "getUnits", "getUnit",
      "getPNamesOuter", "getPUnitsOuter", "getSolverSequence",
      // W23 saved-solution diagnostic: COMSOL 6.4 SolverSequence.getPValsImag(int)
      // exposes the imaginary part for one documented one-based solution number.
      // It supplements (and does not replace) SolutionInfo tuple mapping.
      "getPValsImag",
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
      "create", "load", "loadCopy", "model", "remove", "tags", "uniquetag", "modelsUsedByOtherClients",
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
  // True from the first dispatched showProgress(String) call until the exact
  // typed stop sequence has redirected to stdout and disabled progress.  A
  // lost/failed receipt intentionally leaves this latched, blocking another
  // capture or disconnect from inheriting an unknown client-global sink.
  private volatile boolean progressLogActive;
  private volatile String progressLogPath = "";
  private volatile String serverIdentity = "";

  private PersistentComsolWorker(String token, int port, long initialGeneration, Path serverLockRoot) throws IOException {
    this.token = token;
    this.generation = new AtomicLong(initialGeneration);
    this.serverLockRoot = serverLockRoot;
    this.codeRoot = Files.createTempDirectory("comsol-mcp-java-code-");
    this.server = new ServerSocket();
    this.server.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port));
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
    String data = Json.write(map("type", "ready", "host", server.getInetAddress().getHostAddress(), "port", server.getLocalPort(),
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
    if (!"connect".equals(type) && !"call".equals(type) && !"model".equals(type) && !"modelutil".equals(type) && !"progress_log".equals(type) && !"license_checkout".equals(type) && !"model_snapshot".equals(type) && !"disconnect".equals(type) && !"lock_selftest".equals(type) && !"code_compile".equals(type) && !"code_execute".equals(type) && !"children".equals(type) && !"walk".equals(type) && !"public_describe".equals(type) && !"g2_entity_identity".equals(type) && !"g2_nodegroup_read".equals(type) && !"g2_nodegroup_ungroup".equals(type))
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
      else if ("progress_log".equals(type)) result = progressLog(request);
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
      if (t.getCause() != null && !failure.containsKey("cause_chain"))
        failure.put("cause_chain", throwableChain(t.getCause(), progressLogPath));
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
    if (progressLogActive)
      throw new WorkerFailure("EXECUTION_STATE_UNKNOWN",
          "COMSOL progress capture is still active or unresolved; disconnect is blocked",
          map("execution_state_unknown", true, "post_dispatch", true, "safe_retry", false));
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
    ModelParam parameterSet = model.param();
    String label = model.label();
    String filePath = model.getFilePath();
    long timeModified = model.timeModified();
    String[] parameterNames = parameterSet.varnames(); Arrays.sort(parameterNames);
    String[] parameterExpressions = new String[parameterNames.length];
    for (int i = 0; i < parameterNames.length; i++) {
      if (parameterNames[i] == null || parameterNames[i].isEmpty())
        throw new IllegalStateException("MODEL_FINGERPRINT_PARAMETER_NAMES_INVALID");
      parameterExpressions[i] = parameterSet.get(parameterNames[i]);
    }
    Map<String, String> sortedParameterExpressions = parameterExpressionsFromSample(
        parameterNames, parameterExpressions);
    Map<String, Object> fingerprintValue = modelFingerprintFromSample(tag, label, filePath,
        timeModified, sortedParameterExpressions);
    if (String.valueOf(fingerprintValue.get("fingerprint")).isEmpty())
      throw new IllegalStateException("MODEL_FINGERPRINT_UNAVAILABLE");
    long externalEventCounter = changedByTag.computeIfAbsent(tag, ignored -> new AtomicLong()).get();
    return map("tag", tag, "model_tag", tag, "fingerprint", fingerprintValue.get("fingerprint"),
        "fingerprint_components", fingerprintValue.get("fingerprint_components"),
        "fingerprint_scope", Arrays.asList("tag", "label", "file_path", "time_modified", "parameters"),
        "cas_limit", "control-plane revision plus observed change events; not COMSOL atomic CAS", "external_event_counter", externalEventCounter,
        "changed_tags", new ArrayList<>(changedTags), "server_instance_id", serverIdentity, "worker_instance_id", instanceId, "instance_id", instanceId, "generation", generation.get());
  }

  /** Validate and bind each sampled expression to its sorted name exactly once. */
  static Map<String, String> parameterExpressionsFromSample(String[] names, String[] expressions) {
    if (names == null || expressions == null || names.length != expressions.length)
      throw new IllegalStateException("MODEL_FINGERPRINT_PARAMETER_VALUES_INVALID");
    TreeMap<String, String> sorted = new TreeMap<>();
    for (int i = 0; i < names.length; i++) {
      String name = names[i], expression = expressions[i];
      if (name == null || name.isEmpty() || expression == null || sorted.containsKey(name))
        throw new IllegalStateException("MODEL_FINGERPRINT_PARAMETER_VALUES_INVALID");
      sorted.put(name, expression);
    }
    return new LinkedHashMap<>(sorted);
  }

  /** Build the legacy digest and its raw witness from one already sampled value set. */
  static Map<String, Object> modelFingerprintFromSample(String tag, String label, String filePath,
                                                        long timeModified,
                                                        Map<String, String> parameterExpressions) throws Exception {
    if (tag == null || tag.isEmpty() || label == null || filePath == null || timeModified < 0
        || tag.indexOf('\u0000') >= 0 || label.indexOf('\u0000') >= 0 || filePath.indexOf('\u0000') >= 0
        || parameterExpressions == null)
      throw new IllegalStateException("MODEL_FINGERPRINT_COMPONENTS_INVALID");
    TreeMap<String, String> sortedParameters = new TreeMap<>(parameterExpressions);
    if (sortedParameters.size() != parameterExpressions.size())
      throw new IllegalStateException("MODEL_FINGERPRINT_PARAMETER_NAMES_INVALID");
    StringBuilder source = new StringBuilder();
    source.append(tag).append('\u0000').append(label).append('\u0000').append(filePath)
        .append('\u0000').append(timeModified).append('\u0000');
    List<Object> parameterRows = new ArrayList<>();
    for (Map.Entry<String, String> entry : sortedParameters.entrySet()) {
      String name = entry.getKey(), expression = entry.getValue();
      if (name == null || name.isEmpty() || expression == null
          || name.indexOf('\u0000') >= 0 || expression.indexOf('\u0000') >= 0)
        throw new IllegalStateException("MODEL_FINGERPRINT_PARAMETER_VALUES_INVALID");
      source.append(name).append('=').append(expression).append('\u0000');
      parameterRows.add(map("name", name, "expression", expression));
    }
    Map<String, Object> components = map("schema_version", 1, "tag", tag, "label", label,
        "file_path", filePath, "time_modified_epoch_ms", timeModified, "parameters", parameterRows);
    return map("fingerprint", sha256(source.toString()), "fingerprint_components", components);
  }
  private Object compileJava(Map<String, Object> request) throws Exception {
    return compileJava(sourceSpec(request));
  }
  private Object compileJava(SourceSpec source) throws Exception {
    String key = source.sha256 + "|" + source.entrypoint;
    CompiledArtifact existing = compiledArtifacts.get(key);
    if (existing != null && Files.isDirectory(existing.classes)) {
      return map("compiled", true, "source_sha256", source.sha256, "entrypoint", source.entrypoint,
          "artifact", existing.classes.toString(), "diagnostics", Collections.emptyList());
    }
    JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
    if (compiler == null) throw new WorkerFailure("COMPILE_UNAVAILABLE", "the Worker JVM does not expose javac", map("source_sha256", source.sha256, "entrypoint", source.entrypoint, "diagnostics", Collections.emptyList()));
    Path classes = codeRoot.resolve(source.sha256.substring(0, 24));
    Files.createDirectories(classes);
    Map<String,Object> compilation = compileSnapshot(source, classes);
    boolean success = Boolean.TRUE.equals(compilation.get("compiled"));
    List<?> diagnosticRows = (List<?>)compilation.get("diagnostics");
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
    if (artifact == null || !Files.isDirectory(artifact.classes)) { compileJava(source); artifact = compiledArtifacts.get(key); }
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
  private static JavaFileObject snapshotFile(SourceSpec source, String filename) {
    return new SimpleJavaFileObject(source.path.resolveSibling(filename).toUri(), JavaFileObject.Kind.SOURCE) {
      @Override public CharSequence getCharContent(boolean ignoreEncodingErrors) { return source.text; }
    };
  }
  /** Actual javac AST chooses the compilation unit name; the entrypoint never chooses it. */
  private static Map<String,Object> compileSnapshot(SourceSpec source, Path classes) throws Exception {
    JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
    if (compiler == null) throw new WorkerFailure("COMPILE_UNAVAILABLE", "the Worker JVM does not expose javac");
    String filename = source.path.getFileName().toString();
    List<String> publicTypes = new ArrayList<>();
    try (StandardJavaFileManager manager = compiler.getStandardFileManager(null, Locale.ROOT, StandardCharsets.UTF_8)) {
      DiagnosticCollector<JavaFileObject> parseDiagnostics = new DiagnosticCollector<>();
      List<String> analysisOptions = Arrays.asList(
          "-proc:none", "-classpath", System.getProperty("java.class.path", ""));
      JavacTask parser = (JavacTask)compiler.getTask(null, manager, parseDiagnostics,
          analysisOptions, null, Collections.singletonList(snapshotFile(source, "RegisteredSource.java")));
      Iterable<? extends CompilationUnitTree> units = parser.parse();
      for (Diagnostic<? extends JavaFileObject> diagnostic : parseDiagnostics.getDiagnostics()) {
        if (diagnostic.getKind() == Diagnostic.Kind.ERROR)
          return map("compiled", false, "diagnostics", sourceDiagnostics(source, parseDiagnostics));
      }
      try {
        parser.analyze();
      } catch (RuntimeException ignored) {
        // javac's actual compile below remains authoritative for ordinary
        // source errors; inspect every symbol that did resolve fail-closed.
      }
      Trees trees = Trees.instance(parser);
      for (CompilationUnitTree unit : units) {
        String finding = trustedCodePolicyFinding(unit, trees);
        if (finding != null) {
          throw new WorkerFailure("TRUSTED_CODE_REJECTED", finding,
              map("source_sha256", source.sha256, "entrypoint", source.entrypoint,
                  "diagnostics", sourceDiagnostics(source, parseDiagnostics),
                  "policy", "javac_ast_resolved_ModelUtil_owner_and_static_initializer"));
        }
        for (Tree type : unit.getTypeDecls()) {
          if (type instanceof ClassTree && ((ClassTree)type).getModifiers().getFlags().contains(javax.lang.model.element.Modifier.PUBLIC))
            publicTypes.add(((ClassTree)type).getSimpleName().toString());
        }
      }
    }
    // No public declaration retains the legacy basename. Multiple declarations must fail javac normally.
    if (publicTypes.size() == 1) filename = publicTypes.get(0) + ".java";
    DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
    boolean success;
    try (StandardJavaFileManager manager = compiler.getStandardFileManager(diagnostics, Locale.ROOT, StandardCharsets.UTF_8)) {
      List<String> options = Arrays.asList("-proc:none", "-encoding", "UTF-8", "-classpath",
          System.getProperty("java.class.path", ""), "-d", classes.toString());
      success = Boolean.TRUE.equals(compiler.getTask(null, manager, diagnostics, options, null,
          Collections.singletonList(snapshotFile(source, filename))).call());
    }
    return map("compiled", success, "diagnostics", sourceDiagnostics(source, diagnostics));
  }
  private static String trustedCodePolicyFinding(CompilationUnitTree unit, Trees trees) {
    final String modelUtil = "com.comsol.model.util.ModelUtil";
    // Runtime version is environment metadata. ModelUtil.tags() is excluded:
    // even names-only global model enumeration crosses the injected Model scope.
    final Set<String> safeMetadata = Collections.singleton("getComsolVersion");
    for (ImportTree imported : unit.getImports()) {
      if (!imported.isStatic()) continue;
      String name = imported.getQualifiedIdentifier().toString();
      String prefix = modelUtil + ".";
      if (name.startsWith(prefix)) {
        String member = name.substring(prefix.length());
        if (member.contains(".") || member.equals("*") || !safeMetadata.contains(member))
          return "project Java may not statically import ModelUtil model/connection management APIs";
      }
    }
    final String[] finding = new String[1];
    new TreePathScanner<Void, Void>() {
      private void inspect(TreePath path) {
        Element element = trees.getElement(path);
        if (!(element instanceof ExecutableElement)) return;
        Element owner = element.getEnclosingElement();
        if (owner instanceof TypeElement
            && ((TypeElement)owner).getQualifiedName().contentEquals(modelUtil)) {
          String method = element.getSimpleName().toString();
          if (!safeMetadata.contains(method))
            finding[0] = "project Java may not call ModelUtil." + method
                + "; global model and connection management is outside the injected Model scope";
        }
      }
      @Override public Void visitMethodInvocation(MethodInvocationTree node, Void unused) {
        inspect(new TreePath(getCurrentPath(), node.getMethodSelect()));
        return finding[0] == null ? super.visitMethodInvocation(node, unused) : null;
      }
      @Override public Void visitMemberReference(MemberReferenceTree node, Void unused) {
        inspect(getCurrentPath());
        return finding[0] == null ? super.visitMemberReference(node, unused) : null;
      }
      @Override public Void visitBlock(com.sun.source.tree.BlockTree node, Void unused) {
        if (node.isStatic()) finding[0] = "project Java may not declare a static initializer";
        return finding[0] == null ? super.visitBlock(node, unused) : null;
      }
    }.scan(unit, null);
    return finding[0];
  }
  private static List<Object> sourceDiagnostics(SourceSpec source, DiagnosticCollector<JavaFileObject> diagnostics) {
    List<Object> rows = new ArrayList<>();
    for (Diagnostic<? extends JavaFileObject> item : diagnostics.getDiagnostics()) {
      rows.add(map("kind", item.getKind().name(), "source", source.path.toString(),
          "compilation_source", item.getSource() == null ? "" : item.getSource().getName(),
          "line", item.getLineNumber(), "column", item.getColumnNumber(), "start", item.getStartPosition(), "end", item.getEndPosition(),
          "message", item.getMessage(Locale.ROOT)));
    }
    return rows;
  }
  private static boolean sameDirectoryIdentity(BasicFileAttributes a, BasicFileAttributes b) {
    return a.isDirectory() && b.isDirectory() && !a.isSymbolicLink() && !b.isSymbolicLink()
        && Objects.equals(a.fileKey(), b.fileKey()) && a.creationTime().equals(b.creationTime());
  }
  private static boolean sameSourceIdentity(BasicFileAttributes a, BasicFileAttributes b) {
    return a.isRegularFile() == b.isRegularFile() && a.isDirectory() == b.isDirectory()
        && Objects.equals(a.fileKey(), b.fileKey()) && a.size() == b.size()
        && a.lastModifiedTime().equals(b.lastModifiedTime()) && a.creationTime().equals(b.creationTime());
  }
  private static SourceSpec sourceSpec(Map<String, Object> request) throws Exception {
    String raw = string(request.get("source_artifact"));
    if (raw.isEmpty()) throw new WorkerFailure("ARTIFACT_MISSING", "source_artifact is required");
    Path path = Paths.get(raw).toAbsolutePath().normalize();
    Map<Path,BasicFileAttributes> parents = new LinkedHashMap<>();
    for (Path parent = path.getParent(); parent != null; parent = parent.getParent()) {
      BasicFileAttributes attrs = Files.readAttributes(parent, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
      if (!attrs.isDirectory() || attrs.isSymbolicLink()) throw new WorkerFailure("ARTIFACT_MISSING", "source ancestry must be ordinary directories");
      parents.put(parent, attrs);
    }
    BasicFileAttributes before = Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
    if (!before.isRegularFile() || before.isSymbolicLink() || !path.getFileName().toString().endsWith(".java"))
      throw new WorkerFailure("ARTIFACT_MISSING", "source artifact must be a regular Java file");
    if (before.size() > 2 * 1024 * 1024) throw new WorkerFailure("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit");
    byte[] bytes;
    try (FileChannel channel = FileChannel.open(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
      if (channel.size() != before.size()) throw new WorkerFailure("ARTIFACT_MISSING", "source changed before snapshot");
      ByteArrayOutputStream output = new ByteArrayOutputStream();
      java.nio.ByteBuffer buffer = java.nio.ByteBuffer.allocate(8192);
      while (channel.read(buffer) != -1) {
        buffer.flip(); output.write(buffer.array(), 0, buffer.remaining()); buffer.clear();
        if (output.size() > 2 * 1024 * 1024) throw new WorkerFailure("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit");
      }
      bytes = output.toByteArray();
    }
    BasicFileAttributes after = Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
    if (bytes.length != before.size() || !sameSourceIdentity(before, after))
      throw new WorkerFailure("ARTIFACT_MISSING", "source changed during snapshot");
    for (Map.Entry<Path,BasicFileAttributes> parent : parents.entrySet()) {
      BasicFileAttributes afterParent = Files.readAttributes(parent.getKey(), BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
      if (!sameDirectoryIdentity(parent.getValue(), afterParent)) throw new WorkerFailure("ARTIFACT_MISSING", "source ancestry changed during snapshot");
    }
    String digest = sha256Bytes(bytes);
    String basename = path.getFileName().toString();
    if (basename.matches("[0-9a-f]{64}\\.java") && !basename.substring(0, 64).equals(digest))
      throw new WorkerFailure("ARTIFACT_MISSING", "content-addressed source SHA does not match its bytes");
    String entrypoint = string(request.get("entrypoint"));
    String text;
    try {
      text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
          .onUnmappableCharacter(CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(bytes)).toString();
    } catch (CharacterCodingException invalidEncoding) {
      throw new WorkerFailure("COMPILE_ERROR", "Java source contains invalid UTF-8", map(
          "source_sha256", digest, "entrypoint", entrypoint,
          "diagnostics", Collections.singletonList(map("kind", "ERROR", "source", path.toString(),
              "line", -1L, "column", -1L, "start", -1L, "end", -1L, "message", "Java source contains invalid UTF-8"))));
    }
    if (entrypoint.isEmpty()) {
      java.util.regex.Matcher cls = java.util.regex.Pattern.compile("(?:class|interface|record)\\s+([A-Za-z_$][\\w$]*)").matcher(text);
      if (!cls.find()) throw new WorkerFailure("INVALID_REQUEST", "source has no Java class entrypoint");
      entrypoint = cls.group(1);
    }
    return new SourceSpec(path, text, digest, entrypoint);
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
   * Private, serialized ModelUtil progress capture control.
   *
   * <p>COMSOL 6.4 ModelUtil.showProgress(String) selects a client-side
   * progress file; the Programming Reference documents null as stdout.
   * ModelUtil.showProgress(boolean) returns whether the display setting
   * changed. The path is host-minted inside one project-private job scope and
   * is never returned or copied into Worker receipts.</p>
   * <p>Versioned API evidence: COMSOL 6.4.0.293 ModelUtil, offline KB
   * doc 7768/chunk 23171, SHA-256
   * a5e4c080407a6735a5dd78e95b9fb089d92c0f518477ffdcc4f62b3348085c4eb;
   * Programming Reference doc 4086/chunk 16699, SHA-256
   * efba38a9c78d87e68e7306ced73ccc947c97ca43802bfb5b29045b45c56bb16a.</p>
   */
  private Object progressLog(Map<String, Object> request) throws Exception {
    ensureConnected();
    Object rawPath = request.get("path");
    if (rawPath == null) {
      if (!progressLogActive)
        throw new WorkerFailure("INVALID_REQUEST", "there is no active progress capture to stop",
            map("capture_dispatched", false));
      try {
        // The first overload redirects future progress output away from the
        // job file. Only then disable the client progress display and verify
        // COMSOL confirms the transition.
        ModelUtil.showProgress((String) null);
        boolean disabled = ModelUtil.showProgress(false);
        if (!disabled)
          throw new WorkerFailure("COMSOL_PROGRESS_DISABLE_UNCONFIRMED",
              "COMSOL did not confirm progress-display disable after sink redirection",
              map("execution_state_unknown", true, "post_dispatch", true, "safe_retry", false,
                  "cause_chain", Collections.emptyList()));
        progressLogActive = false;
        progressLogPath = "";
        return map("active", false, "disabled", true);
      } catch (WorkerFailure failure) {
        throw failure;
      } catch (Throwable failure) {
        throw new WorkerFailure("COMSOL_PROGRESS_DISABLE_FAILED",
            "COMSOL progress sink could not be redirected and disabled with a verified receipt",
            map("execution_state_unknown", true, "post_dispatch", true, "safe_retry", false,
                "cause_chain", throwableChain(failure, progressLogPath)), failure);
      }
    }
    if (!(rawPath instanceof String) || ((String) rawPath).isEmpty())
      throw new WorkerFailure("INVALID_REQUEST", "progress capture path must be an absolute regular file",
          map("capture_dispatched", false));
    if (progressLogActive)
      throw new WorkerFailure("EXECUTION_STATE_UNKNOWN", "another progress capture is active or unresolved",
          map("execution_state_unknown", true, "post_dispatch", true, "safe_retry", false));
    final Path path;
    try {
      path = Paths.get((String) rawPath);
      if (!path.isAbsolute() || !path.normalize().equals(path)
          || hasSymbolicLinkComponent(path)
          || !Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)
          || Files.size(path) != 0L)
        throw new IOException("progress path must be a new empty regular file without symbolic-link components");
    } catch (Exception invalid) {
      throw new WorkerFailure("INVALID_REQUEST", "progress capture path must be an absolute empty regular file",
          map("capture_dispatched", false));
    }
    // Latch before the API call: if COMSOL changes its global sink and then
    // throws, a second request must not silently redirect another job's log.
    progressLogActive = true;
    progressLogPath = path.toString();
    try {
      ModelUtil.showProgress(path.toString());
      return map("active", true, "disabled", false);
    } catch (Throwable failure) {
      throw new WorkerFailure("COMSOL_PROGRESS_ENABLE_FAILED",
          "COMSOL progress sink activation did not produce a verified receipt",
          map("execution_state_unknown", true, "post_dispatch", true, "safe_retry", false,
              "cause_chain", throwableChain(failure, progressLogPath)), failure);
    }
  }

  private static boolean hasSymbolicLinkComponent(Path path) {
    Path current = path.getRoot();
    if (current == null) return true;
    for (Path part : path) {
      current = current.resolve(part);
      if (Files.isSymbolicLink(current)) return true;
    }
    return false;
  }

  private static List<Object> throwableChain(Throwable failure, String privatePath) {
    List<Object> chain = new ArrayList<>();
    Set<Throwable> seen = Collections.newSetFromMap(new IdentityHashMap<>());
    Throwable current = failure;
    while (current != null && seen.add(current) && chain.size() < 32) {
      String message = current.getMessage();
      if (message == null || message.trim().isEmpty()) message = current.getClass().getSimpleName();
      if (privatePath != null && !privatePath.isEmpty()) message = message.replace(privatePath, "<private-progress-file>");
      if (message.length() > 1200) message = message.substring(0, 1200);
      chain.add(map("type", current.getClass().getName(), "message", message));
      current = current.getCause();
    }
    return chain;
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
    String method = string(request.get("method"));
    List<Object> args = list(request.get("args"));
    // W23 R31: expose only the exact read-only ModelEntityList.index(String)
    // operation. It deliberately remains outside both generic method sets;
    // the receiver, primitive-int descriptor, and sole String argument are
    // checked here and by the same predicate used for publicDescribe.
    if ("index".equals(method)) return readModelEntityListIndex(target, args);
    // A material tensor proof needs the logical stored dimensions in addition
    // to the typed getter's raw array shape.  Keep this exact String overload
    // outside METHODS so no unrelated receiver's size overload is exposed.
    if ("size".equals(method)) return readMaterialModelSize(target, args);
    if (!METHODS.contains(method)) throw new SecurityException("METHOD_REJECTED");
    if ("identifier".equals(method)) {
      // COMSOL 6.4.0.293 Physics.identifier() is deprecated in favor of
      // ModelEntity.tag(). Keep only the exact Physics zero-argument getter;
      // the overloaded identifier(String) setter and every other receiver
      // remain outside the Worker reflection surface. Official API reference:
      // local KB doc 7757, chunk 23152, Physics.html SHA-256
      // d0de70997a9ea928edd8f1d207f926ce82c3849e5846c20189d28d67cd9273fc.
      if (!(target instanceof Physics) || !args.isEmpty()) throw new SecurityException("METHOD_REJECTED");
    }
    if ("objectNames".equals(method)) {
      if (!(target instanceof GeomSequence) || !args.isEmpty()) throw new SecurityException("METHOD_REJECTED");
    }
    if ("exportFinal".equals(method)) {
      if (!(target instanceof GeomSequence) || args.size() != 1 || !(args.get(0) instanceof String)
          || ((String) args.get(0)).isEmpty()) throw new SecurityException("METHOD_REJECTED");
    }
    if ("insert".equals(method)) {
      if (!(target instanceof MaterialList) || args.size() != 2 || !(args.get(0) instanceof String)
          || ((String) args.get(0)).isEmpty() || !(args.get(1) instanceof List<?>))
        throw new SecurityException("METHOD_REJECTED");
      for (Object materialTag : (List<?>) args.get(1)) {
        if (!(materialTag instanceof String) || ((String) materialTag).isEmpty())
          throw new IllegalArgumentException("MaterialList.insert tags must be nonempty strings");
      }
    }
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
    if ("getFileResourceInventory".equals(method)) {
      if (!args.isEmpty()) throw new IllegalArgumentException("getFileResourceInventory takes no arguments");
      return getFileResourceInventory(target);
    }
    if ("saveModelPackage".equals(method)) {
      return saveModelPackage(target, args);
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

  /** Return COMSOL-owned FileResource tags and exact MPH archive member names. */
  private Object getFileResourceInventory(Object target) throws Exception {
    if (!(target instanceof Model)) {
      throw new WorkerFailure("FILE_RESOURCE_INVENTORY_TARGET_INVALID",
          "getFileResourceInventory is only valid for a Model handle");
    }
    Model model = (Model) target;
    String[] tags = model.file().tags();
    List<Object> rows = new ArrayList<Object>();
    if (tags == null) return rows;
    if (tags.length > 512) {
      throw new WorkerFailure("FILE_RESOURCE_INVENTORY_LIMIT",
          "Model.FileResourceList exceeds the bounded 512-tag inventory");
    }
    Set<String> seen = new HashSet<String>();
    for (String tag : tags) {
      if (tag == null || tag.isEmpty() || !seen.add(tag)) {
        throw new WorkerFailure("FILE_RESOURCE_INVENTORY_INVALID",
            "Model.FileResourceList returned an empty or duplicate tag");
      }
      // archive() returns the model-internal archive path. Never use
      // FileResource.resource(), which is a temporary extracted filesystem path.
      String archive = model.file(tag).archive();
      rows.add(map("tag", tag, "archive", archive));
    }
    return rows;
  }

  /**
   * Save one actual MPH copy without changing the bound model's live filepath.
   * includeSolution is implemented only through the documented
   * excludeComputedDataInMph(boolean) getter/setter, with exact readback and
   * finally restoration. An uncertain restoration is reported as UNKNOWN.
   */
  private Object saveModelPackage(Object target, List<Object> args) throws Exception {
    if (!(target instanceof Model)) {
      throw new WorkerFailure("MODEL_PACKAGE_TARGET_INVALID",
          "saveModelPackage is only valid for a Model handle",
          map("save_dispatched", false, "execution_state_unknown", false));
    }
    if (args.size() != 2 || !(args.get(0) instanceof String) || ((String) args.get(0)).trim().isEmpty()
        || !(args.get(1) instanceof Boolean)) {
      throw new WorkerFailure("INVALID_REQUEST",
          "saveModelPackage requires an absolute scratch path and an includeSolution boolean",
          map("save_dispatched", false, "execution_state_unknown", false));
    }
    Model model = (Model) target;
    Path destination = Paths.get((String) args.get(0)).toAbsolutePath().normalize();
    if (Files.exists(destination, LinkOption.NOFOLLOW_LINKS)) {
      throw new WorkerFailure("MODEL_PACKAGE_DESTINATION_EXISTS",
          "typed package scratch destination already exists",
          map("save_dispatched", false, "execution_state_unknown", false));
    }
    if (!Files.isDirectory(destination.getParent(), LinkOption.NOFOLLOW_LINKS)
        || Files.isSymbolicLink(destination.getParent())) {
      throw new WorkerFailure("MODEL_PACKAGE_DESTINATION_INVALID",
          "typed package scratch parent must be an existing non-symlink directory",
          map("save_dispatched", false, "execution_state_unknown", false));
    }
    String originalPath;
    boolean originalExclude;
    try {
      originalPath = model.getFilePath();
      originalExclude = model.excludeComputedDataInMph();
    } catch (Throwable failure) {
      throw new WorkerFailure("MODEL_PACKAGE_PRECHECK_FAILED",
          "model package getter precheck failed before changing the bound model",
          map("save_dispatched", false, "execution_state_unknown", false,
              "cause_type", failure.getClass().getName()), failure);
    }
    boolean includeSolution = ((Boolean) args.get(1)).booleanValue();
    boolean desiredExclude = !includeSolution;
    boolean setterAttempted = false;
    boolean saveDispatched = false;
    Throwable saveFailure = null;
    Throwable restoreFailure = null;
    boolean restored = false;
    Boolean temporaryReadback = null;
    Boolean restoredReadback = null;
    try {
      if (originalExclude != desiredExclude) {
        setterAttempted = true;
        model.excludeComputedDataInMph(desiredExclude);
        temporaryReadback = Boolean.valueOf(model.excludeComputedDataInMph());
        if (temporaryReadback.booleanValue() != desiredExclude) {
          throw new WorkerFailure("MODEL_PACKAGE_OPTION_READBACK_MISMATCH",
              "excludeComputedDataInMph did not read back the requested package option",
              map("save_dispatched", false, "execution_state_unknown", true,
                  "setter_attempted", true, "temporary_option_readback", temporaryReadback));
        }
      } else {
        temporaryReadback = Boolean.valueOf(originalExclude);
      }
      saveDispatched = true;
      model.save(destination.toString(), true);
    } catch (Throwable failure) {
      saveFailure = failure;
    } finally {
      if (setterAttempted) {
        try {
          model.excludeComputedDataInMph(originalExclude);
          restoredReadback = Boolean.valueOf(model.excludeComputedDataInMph());
          restored = restoredReadback.booleanValue() == originalExclude;
          if (!restored) {
            restoreFailure = new IllegalStateException("excludeComputedDataInMph restoration readback mismatch");
          }
        } catch (Throwable failure) {
          restoreFailure = failure;
        }
      } else {
        restored = true;
        restoredReadback = Boolean.valueOf(originalExclude);
      }
    }
    String finalPath = null;
    try { finalPath = model.getFilePath(); } catch (Throwable failure) {
      if (restoreFailure == null) restoreFailure = failure;
    }
    boolean livePathPreserved = Objects.equals(originalPath, finalPath);
    if (saveFailure != null || restoreFailure != null || !restored || !livePathPreserved) {
      Map<String,Object> details = map(
          "save_dispatched", saveDispatched,
          "execution_state_unknown", saveDispatched || setterAttempted,
          "post_dispatch", saveDispatched || setterAttempted,
          "setter_attempted", setterAttempted,
          "restore_verified", restored,
          "original_exclude_computed_data", originalExclude,
          "temporary_option_readback", temporaryReadback,
          "restored_option_readback", restoredReadback,
          "live_filepath_preserved", livePathPreserved,
          "save_failure_type", saveFailure == null ? null : saveFailure.getClass().getName(),
          "restore_failure_type", restoreFailure == null ? null : restoreFailure.getClass().getName());
      String message = restoreFailure != null || !restored
          ? "model package option restoration could not be verified"
          : (!livePathPreserved ? "saveCopy changed the bound model's live filepath" :
              "COMSOL saveCopy failed after the typed package dispatch");
      throw new WorkerFailure("EXECUTION_STATE_UNKNOWN", message, details,
          restoreFailure != null ? restoreFailure : saveFailure);
    }
    if (!Files.isRegularFile(destination, LinkOption.NOFOLLOW_LINKS)
        || Files.isSymbolicLink(destination) || Files.size(destination) <= 0) {
      throw new WorkerFailure("EXECUTION_STATE_UNKNOWN",
          "saveCopy completed without a verified nonempty regular MPH file",
          map("save_dispatched", true, "execution_state_unknown", true,
              "post_dispatch", true, "restore_verified", true,
              "live_filepath_preserved", livePathPreserved,
              "include_solution", includeSolution));
    }
    return map("status", "SAVED_COPY", "include_solution", includeSolution,
        "original_exclude_computed_data", originalExclude,
        "temporary_option_readback", temporaryReadback,
        "restored_option_readback", restoredReadback,
        "restore_verified", restored,
        "live_filepath_preserved", livePathPreserved,
        "mph_size_bytes", Files.size(destination));
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

  static String normalizeD2RuntimeVersion(String raw) {
    if (raw == null || raw.isEmpty() || raw.codePointCount(0, raw.length()) > 256)
      throw new IllegalArgumentException("COMSOL runtime version must be a bounded nonempty string");
    if (D2_CANONICAL_VERSION.matcher(raw).matches()) return raw;
    java.util.regex.Matcher declaration = D2_BUILD_DECLARATION.matcher(raw);
    if (!declaration.matches())
      throw new IllegalArgumentException("COMSOL runtime version is not a complete recognized declaration");
    String release = declaration.group(1);
    if (release.split("\\.").length == 2) release += ".0";
    return release + "." + declaration.group(2);
  }

  private static String requireD2RuntimeVersion() {
    String actual = ModelUtil.getComsolVersion();
    String semantic = null;
    try { semantic = normalizeD2RuntimeVersion(actual); }
    catch (IllegalArgumentException ignored) { /* Fail closed below; retain the raw observed value only on success. */ }
    if (!D2_COMSOL_VERSION.equals(semantic))
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
    if (type == null || out.size() > PUBLIC_METADATA_INTERFACE_LIMIT) return;
    for (Class<?> iface : type.getInterfaces()) {
      if (Modifier.isPublic(iface.getModifiers()) && iface.getName().startsWith("com.comsol.model.")) out.add(iface);
      if (out.size() > PUBLIC_METADATA_INTERFACE_LIMIT) return;
      publicInterfaces(iface, out);
      if (out.size() > PUBLIC_METADATA_INTERFACE_LIMIT) return;
    }
    publicInterfaces(type.getSuperclass(), out);
  }
  private Object publicDescribe(Map<String, Object> request) {
    ensureConnected();
    if (number(request.get("generation"), -1) != generation.get()) throw new IllegalStateException("STALE_WORKER_HANDLE");
    Object target = handles.get(string(request.get("handle")));
    if (target == null) throw new IllegalArgumentException("UNKNOWN_WORKER_HANDLE");
    Map<String, Object> descriptor = publicReceiverMetadata(target);
    descriptor.put("runtime_version", ModelUtil.getComsolVersion());
    return descriptor;
  }
  private static Map<String, Object> publicReceiverMetadata(Class<?> type) {
    return publicReceiverMetadata(type, null);
  }
  private static Map<String, Object> publicReceiverMetadata(Object target) {
    return publicReceiverMetadata(target.getClass(), target);
  }
  private static Map<String, Object> publicReceiverMetadata(Class<?> type, Object target) {
    Set<Class<?>> interfaces = new TreeSet<>(Comparator.comparing(Class::getName));
    publicInterfaces(type, interfaces);
    boolean metadataComplete = interfaces.size() <= PUBLIC_METADATA_INTERFACE_LIMIT;
    List<Class<?>> boundedInterfaces = new ArrayList<>();
    List<String> names = new ArrayList<>();
    for (Class<?> iface : interfaces) {
      if (boundedInterfaces.size() >= PUBLIC_METADATA_INTERFACE_LIMIT) break;
      boundedInterfaces.add(iface);
      names.add(iface.getName());
    }
    TreeMap<String, Object> methods = new TreeMap<>();
    int examined = 0;
    outer:
    for (Class<?> iface : boundedInterfaces) {
      for (Method method : iface.getMethods()) {
        if (++examined > PUBLIC_METADATA_SCAN_LIMIT) {
          metadataComplete = false;
          break outer;
        }
        if (!Modifier.isPublic(method.getModifiers()) || Modifier.isStatic(method.getModifiers())
            || method.getDeclaringClass() == Object.class || method.isSynthetic() || method.isBridge()) continue;
        String owner = method.getDeclaringClass().getName();
        if (!owner.startsWith("com.comsol.model.")) continue;
        List<String> parameters = new ArrayList<>();
        for (Class<?> parameter : method.getParameterTypes()) parameters.add(parameter.getName());
        String returns = method.getReturnType().getName();
        String key = owner + "#" + method.getName() + "(" + String.join(",", parameters) + ")->" + returns;
        if (methods.containsKey(key)) continue;
        if (methods.size() >= PUBLIC_METADATA_METHOD_LIMIT) {
          metadataComplete = false;
          break outer;
        }
        methods.put(key, map("interface", owner, "method", method.getName(),
            "parameters", parameters, "returns", returns));
      }
    }
    return map("interfaces", names, "methods", new ArrayList<>(methods.values()),
        "metadata_complete", metadataComplete,
        "metadata_limits", map("interfaces", PUBLIC_METADATA_INTERFACE_LIMIT,
            "methods", PUBLIC_METADATA_METHOD_LIMIT));
  }
  private static boolean modelEntityListIndexCapability(Object target, Method method, List<?> args) {
    if (!(target instanceof ModelEntityList<?>) || method == null
        || method.getDeclaringClass() != ModelEntityList.class
        || !"index".equals(method.getName())
        || method.getReturnType() != int.class
        || !Arrays.equals(method.getParameterTypes(), new Class<?>[]{String.class})) return false;
    return args == null || (args.size() == 1 && args.get(0) instanceof String);
  }
  private static Object readModelEntityListIndex(Object target, List<?> args) {
    try {
      Method method = ModelEntityList.class.getMethod("index", String.class);
      if (!modelEntityListIndexCapability(target, method, args))
        throw new SecurityException("METHOD_REJECTED");
      return Integer.valueOf(((ModelEntityList<?>) target).index((String) args.get(0)));
    } catch (NoSuchMethodException unavailable) {
      throw new SecurityException("METHOD_REJECTED");
    }
  }
  private static boolean materialModelSizeCapability(Object target, Method method, List<?> args) {
    if (!(target instanceof MaterialModel) || method == null || Modifier.isStatic(method.getModifiers())
        || !"size".equals(method.getName()) || method.getReturnType() != int[].class
        || !Arrays.equals(method.getParameterTypes(), new Class<?>[]{String.class})) return false;
    if (args != null && (args.size() != 1 || !(args.get(0) instanceof String)
        || ((String) args.get(0)).isEmpty())) return false;
    try {
      return MaterialModel.class.getMethod("size", String.class).equals(method);
    } catch (NoSuchMethodException unavailable) {
      return false;
    }
  }
  private static Object readMaterialModelSize(Object target, List<?> args) throws Exception {
    try {
      Method method = MaterialModel.class.getMethod("size", String.class);
      if (!materialModelSizeCapability(target, method, args)) throw new SecurityException("METHOD_REJECTED");
      int[] dimensions = ((MaterialModel) target).size((String) args.get(0));
      if (dimensions == null || dimensions.length != 2
          || dimensions[0] <= 0 || dimensions[1] <= 0) {
        throw new WorkerFailure("EXECUTION_STATE_UNKNOWN",
            "MaterialModel.size(String) returned null or nonpositive dimensions");
      }
      return dimensions;
    } catch (NoSuchMethodException unavailable) {
      throw new WorkerFailure("API_UNSUPPORTED",
          "MaterialModel.size(String) is absent from the compiled COMSOL interface", null, unavailable);
    }
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
    return map("ok",false,"code",code,"message",sb.toString(),"execution_state_unknown",unknown,
        "cause_chain", throwableChain(t, ""));
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
    Map<String,Object> snapshot(){Map<String,Object> out=map("ok",!"FAILED".equals(status),"request_id",id,"type",type,"status",status,"queued_at_ms",queuedAt,"started_at_ms",startedAt,"completed_at_ms",completedAt);if("SUCCEEDED".equals(status)||result!=null)out.put("result",result);if(failure!=null)out.put("failure",failure);return out;}
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

  /** Fixed one-frame JDK-only diagnostic, never an admission/Resume issuer. */
  public static final class OsQualificationProbe {
    private static final int VERSION = 1, REQUEST_LIMIT = 32768, RESPONSE_LIMIT = 65536;
    private static final int RECORD_LIMIT = 16, PATH_LIMIT = 4096, READ_LIMIT = 16777216;
    private static final int TIMEOUT_MS = 1000;
    private static volatile Session retained;

    private static final class ProtocolException extends java.io.IOException {
      ProtocolException() { super(); }
    }
    private static final class Fact {
      int acquisition, close;
      String closeException = "";
    }
    private static final class Operation {
      int index, type, maxbytes, port;
      String path;
      byte[] address;
    }
    private static final class Observation {
      final Operation operation;
      final Fact input = new Fact(), output = new Fact(), socket = new Fact();
      boolean entered, bodyReturned, eof, limit;
      int phase, echo; // phase matches v1 Python PHASES; echo0unknown/1false/2true.
      String exception = "";
      Long readCount, writeCount;
      byte[] readHash, writeHash, localAddress, remoteAddress;
      int localPort, remotePort;
      java.io.InputStream inputObject;
      java.io.OutputStream outputObject;
      java.net.Socket socketObject;
      Observation(Operation op) { operation = op; }
    }
    private static final class Session {
      final String nonce, recipe;
      final Fact stdinFact = new Fact();
      final java.util.ArrayList<Operation> operations = new java.util.ArrayList<>();
      final java.util.ArrayList<Observation> observations = new java.util.ArrayList<>();
      java.io.DataInputStream input;
      java.io.OutputStream output;
      byte[] requestBody, responseFrame;
      String frameException = "";
      boolean rejected, outputEntered, outputReturned, flushReturned, closeEntered, closeReturned;
      String outputException = "", outputCloseException = "";
      Session(String n, String r) { nonce = n; recipe = r; }
    }

    private static String exceptionClass(Throwable error) { return error.getClass().getName(); }
    private static boolean hex(String value, int count) {
      if (value == null || value.length() != count) return false;
      for (int i=0;i<count;i++) { char c=value.charAt(i); if (!((c>='0'&&c<='9')||(c>='a'&&c<='f'))) return false; }
      return true;
    }
    private static byte[] bytes(java.io.DataInputStream in, int count, int limit) throws java.io.IOException {
      if (count < 0 || count > limit) throw new ProtocolException();
      byte[] raw=new byte[count]; in.readFully(raw); return raw;
    }
    private static String utf8(java.io.DataInputStream in, int limit) throws java.io.IOException {
      byte[] raw=bytes(in,in.readUnsignedShort(),limit);
      String value=java.nio.charset.StandardCharsets.UTF_8.newDecoder()
          .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
          .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT)
          .decode(java.nio.ByteBuffer.wrap(raw)).toString();
      if (value.indexOf('\0')>=0) throw new ProtocolException();
      return value;
    }
    private static String ascii(java.io.DataInputStream in, int count) throws java.io.IOException {
      byte[] raw=bytes(in,count,count);
      for(byte b:raw) if(b<0) throw new ProtocolException();
      return new String(raw,java.nio.charset.StandardCharsets.US_ASCII);
    }
    private static void path(String value) throws java.io.IOException {
      if (value.length()==0 || value.indexOf('\0')>=0 || value.startsWith("\\") || value.startsWith("//")
          || value.endsWith("/") || value.endsWith("\\") || !java.nio.file.Paths.get(value).isAbsolute())
        throw new ProtocolException();
      for (String part:value.replace('\\','/').split("/",-1))
        if (part.equals(".") || part.equals("..")) throw new ProtocolException();
    }
    private static String createName(Session s, int index) {
      return "comsol-os-probe-"+s.nonce+"-"+index+".dat";
    }
    private static byte[] payload(Session s, int index, boolean echo) {
      String value=echo ? "OSQP1:"+s.nonce+":"+index+"\n" : "COMSOL-MCP-OS-PROBE/1\n"+s.nonce+"\n"+index+"\n";
      return value.getBytes(java.nio.charset.StandardCharsets.US_ASCII);
    }
    private static byte[] digest(byte[] raw) throws java.security.NoSuchAlgorithmException {
      return java.security.MessageDigest.getInstance("SHA-256").digest(raw);
    }

    private static void readRequest(Session s) {
      try {
        s.input=new java.io.DataInputStream(System.in); s.stdinFact.acquisition=1;
        int length=s.input.readInt();
        s.requestBody=bytes(s.input,length,REQUEST_LIMIT-4);
        if (s.input.read()!=-1) throw new ProtocolException(); // one bounded trailing sentinel, no second frame.
        java.io.DataInputStream in=new java.io.DataInputStream(new java.io.ByteArrayInputStream(s.requestBody));
        if (!java.util.Arrays.equals(bytes(in,4,4),new byte[]{'C','Q','0','1'}) || in.readUnsignedShort()!=VERSION)
          throw new ProtocolException();
        String nonce=ascii(in,32), recipe=ascii(in,64);
        if (!hex(nonce,32)||!hex(recipe,64)||!nonce.equals(s.nonce)||!recipe.equals(s.recipe)) throw new ProtocolException();
        int count=in.readUnsignedShort();
        if(count<1||count>RECORD_LIMIT) throw new ProtocolException();
        boolean[] seen=new boolean[RECORD_LIMIT];
        for(int i=0;i<count;i++) {
          Operation op=new Operation(); op.index=in.readUnsignedByte(); op.type=in.readUnsignedByte();
          if(op.index>=RECORD_LIMIT||seen[op.index]||op.type<1||op.type>3) throw new ProtocolException();
          seen[op.index]=true;
          byte[] record=bytes(in,in.readInt(),REQUEST_LIMIT-4);
          java.io.DataInputStream data=new java.io.DataInputStream(new java.io.ByteArrayInputStream(record));
          if(op.type==1||op.type==2) {
            op.path=utf8(data,PATH_LIMIT); path(op.path);
            if(op.type==1) { op.maxbytes=data.readInt(); if(op.maxbytes<1||op.maxbytes>READ_LIMIT) throw new ProtocolException(); }
            else if(!java.nio.file.Paths.get(op.path).getFileName().toString().equals(createName(s,op.index)))
              throw new ProtocolException();
          } else {
            int size=data.readUnsignedByte(); if(size!=4&&size!=16) throw new ProtocolException();
            op.address=bytes(data,size,16); op.port=data.readUnsignedShort(); if(op.port==0) throw new ProtocolException();
          }
          if(data.available()!=0) throw new ProtocolException();
          s.operations.add(op);
        }
        if(in.available()!=0) throw new ProtocolException();
      } catch(Throwable error) { s.rejected=true; s.frameException=exceptionClass(error); }
      finally {
        if(s.input!=null) {
          s.stdinFact.close=2;
          try { s.input.close(); s.stdinFact.close=1; }
          catch(Throwable error) {
            s.stdinFact.closeException=exceptionClass(error); s.rejected=true;
            if(s.frameException.length()==0) s.frameException=s.stdinFact.closeException;
          }
        }
      }
      // Validate the whole request and close the original input before any operation.
      if(!s.rejected) for(Operation op:s.operations) s.observations.add(new Observation(op));
    }

    private static void closeResources(Observation o) {
      // Socket streams are borrowed views. Closing either would close the same
      // socket; close the one owning original Socket exactly once instead.
      if(o.socketObject!=null) {
        o.socket.close=2;
        try { o.socketObject.close(); o.socket.close=1; }
        catch(Throwable error) { o.socket.closeException=exceptionClass(error); }
      } else {
        if(o.inputObject!=null) {
          o.input.close=2;
          try { o.inputObject.close(); o.input.close=1; }
          catch(Throwable error) { o.input.closeException=exceptionClass(error); }
        }
        if(o.outputObject!=null) {
          o.output.close=2;
          try { o.outputObject.close(); o.output.close=1; }
          catch(Throwable error) { o.output.closeException=exceptionClass(error); }
        }
      }
      if(o.exception.length()==0 && (o.input.close==2||o.output.close==2||o.socket.close==2)) o.phase=8;
    }
    private static boolean runOperation(Session s, Observation o) {
      Operation op=o.operation; o.entered=true; o.phase=2;
      try {
        if(op.type==1) {
          o.input.acquisition=2;
          o.inputObject=java.nio.file.Files.newInputStream(java.nio.file.Paths.get(op.path),java.nio.file.StandardOpenOption.READ);
          o.input.acquisition=1; o.phase=3; o.readCount=0L;
          java.security.MessageDigest hash=java.security.MessageDigest.getInstance("SHA-256");
          byte[] buffer=new byte[4096];
          while(o.readCount<op.maxbytes) {
            int count=o.inputObject.read(buffer,0,(int)Math.min(buffer.length,op.maxbytes-o.readCount));
            if(count<0) { o.eof=true; break; }
            if(count==0) throw new ProtocolException();
            hash.update(buffer,0,count); o.readCount+=count;
          }
          o.limit=!o.eof&&o.readCount==op.maxbytes; o.readHash=hash.digest();
        } else if(op.type==2) {
          o.output.acquisition=2;
          o.outputObject=java.nio.file.Files.newOutputStream(java.nio.file.Paths.get(op.path),
              java.nio.file.StandardOpenOption.CREATE_NEW,java.nio.file.StandardOpenOption.WRITE);
          o.output.acquisition=1; o.phase=4;
          byte[] raw=payload(s,op.index,false); o.outputObject.write(raw);
          o.writeCount=(long)raw.length; o.writeHash=digest(raw); o.phase=5; o.outputObject.flush();
        } else {
          o.socket.acquisition=2; o.socketObject=new java.net.Socket(); o.socket.acquisition=1; o.phase=6;
          java.net.InetAddress address=java.net.InetAddress.getByAddress(op.address); // bytes only, never DNS.
          o.socketObject.connect(new java.net.InetSocketAddress(address,op.port),TIMEOUT_MS);
          o.socketObject.setSoTimeout(TIMEOUT_MS);
          java.net.InetSocketAddress local=(java.net.InetSocketAddress)o.socketObject.getLocalSocketAddress();
          java.net.InetSocketAddress remote=(java.net.InetSocketAddress)o.socketObject.getRemoteSocketAddress();
          o.localAddress=local.getAddress().getAddress(); o.localPort=local.getPort();
          o.remoteAddress=remote.getAddress().getAddress(); o.remotePort=remote.getPort();
          o.input.acquisition=2; o.inputObject=o.socketObject.getInputStream(); o.input.acquisition=3; o.input.close=3;
          o.output.acquisition=2; o.outputObject=o.socketObject.getOutputStream(); o.output.acquisition=3; o.output.close=3;
          byte[] raw=payload(s,op.index,true); o.phase=4; o.outputObject.write(raw);
          o.writeCount=(long)raw.length; o.writeHash=digest(raw); o.phase=5; o.outputObject.flush();
          o.phase=7; byte[] reply=new byte[raw.length]; o.readCount=0L;
          while(o.readCount<reply.length) {
            int count=o.inputObject.read(reply,o.readCount.intValue(),reply.length-o.readCount.intValue());
            if(count<0) { o.eof=true; throw new java.io.EOFException(); }
            if(count==0) throw new ProtocolException();
            o.readCount+=count;
          }
          o.readHash=digest(reply); o.echo=java.util.Arrays.equals(reply,raw)?2:1;
        }
        o.bodyReturned=true; o.phase=9;
      } catch(Throwable error) { o.exception=exceptionClass(error); }
      finally { closeResources(o); }
      return o.exception.length()==0&&o.input.close!=2&&o.output.close!=2&&o.socket.close!=2;
    }

    private static void text(java.io.DataOutputStream out, String value, int limit) throws java.io.IOException {
      byte[] raw=value.getBytes(java.nio.charset.StandardCharsets.UTF_8);
      if(raw.length>limit||value.indexOf('\0')>=0) throw new ProtocolException();
      out.writeShort(raw.length); out.write(raw);
    }
    private static void fact(java.io.DataOutputStream out, Fact f) throws java.io.IOException {
      out.writeByte(f.acquisition); out.writeByte(f.close); text(out,f.closeException,128);
    }
    private static void count(java.io.DataOutputStream out, Long value) throws java.io.IOException {
      out.writeBoolean(value!=null); if(value!=null) out.writeLong(value);
    }
    private static void hash(java.io.DataOutputStream out, byte[] value) throws java.io.IOException {
      out.writeBoolean(value!=null);
      if(value!=null) { if(value.length!=32) throw new ProtocolException(); out.write(value); }
    }
    private static void endpoint(java.io.DataOutputStream out, byte[] address, int port) throws java.io.IOException {
      out.writeBoolean(address!=null);
      if(address!=null) {
        if((address.length!=4&&address.length!=16)||port<1||port>65535) throw new ProtocolException();
        out.writeByte(address.length); out.write(address); out.writeShort(port);
      }
    }
    private static byte[] response(Session s) throws java.io.IOException {
      java.io.ByteArrayOutputStream buffer=new java.io.ByteArrayOutputStream();
      java.io.DataOutputStream out=new java.io.DataOutputStream(buffer);
      out.write(new byte[]{'C','R','0','1'}); out.writeShort(VERSION);
      out.write(s.nonce.getBytes(java.nio.charset.StandardCharsets.US_ASCII));
      out.write(s.recipe.getBytes(java.nio.charset.StandardCharsets.US_ASCII));
      out.writeByte(s.rejected?1:0); text(out,s.frameException,128); fact(out,s.stdinFact);
      out.writeByte(0); // self output write/flush/close cannot be attested inside its own prior bytes.
      out.writeShort(s.observations.size());
      for(Observation o:s.observations) {
        java.io.ByteArrayOutputStream raw=new java.io.ByteArrayOutputStream();
        java.io.DataOutputStream row=new java.io.DataOutputStream(raw);
        row.writeBoolean(o.entered); row.writeByte(o.phase); row.writeBoolean(o.bodyReturned); text(row,o.exception,128);
        fact(row,o.input); fact(row,o.output); fact(row,o.socket);
        count(row,o.readCount); count(row,o.writeCount); hash(row,o.readHash); hash(row,o.writeHash);
        row.writeBoolean(o.eof); row.writeBoolean(o.limit); row.writeByte(o.echo);
        endpoint(row,o.localAddress,o.localPort); endpoint(row,o.remoteAddress,o.remotePort); row.flush();
        out.writeByte(o.operation.index); out.writeByte(o.operation.type); out.writeInt(raw.size()); out.write(raw.toByteArray());
      }
      out.flush(); if(buffer.size()+4>RESPONSE_LIMIT) throw new ProtocolException();
      java.io.ByteArrayOutputStream frame=new java.io.ByteArrayOutputStream();
      java.io.DataOutputStream framed=new java.io.DataOutputStream(frame);
      framed.writeInt(buffer.size()); framed.write(buffer.toByteArray()); framed.flush(); return frame.toByteArray();
    }

    public static void main(String[] args) {
      // This class never accesses an outer constructor/main/static state, vendor
      // classes, reflection, process creation, arbitrary code or commands.
      if(args.length!=2||!hex(args[0],32)||!hex(args[1],64)) { System.err.println("OSQ_ARGUMENTS"); System.exit(64); return; }
      Session s=new Session(args[0],args[1]); retained=s; s.output=System.out;
      try {
        readRequest(s);
        if(!s.rejected) {
          boolean proceed=true;
          for(Observation observation:s.observations) {
            if(proceed) proceed=runOperation(s,observation);
          }
        }
        s.responseFrame=response(s); s.outputEntered=true;
        s.output.write(s.responseFrame); s.outputReturned=true; s.output.flush(); s.flushReturned=true;
      } catch(Throwable error) { s.outputException=exceptionClass(error); }
      finally {
        s.closeEntered=true;
        try { s.output.close(); s.closeReturned=true; }
        catch(Throwable error) { s.outputCloseException=exceptionClass(error); }
      }
      if(s.outputException.length()!=0||s.outputCloseException.length()!=0) {
        // No message/stack/path data. Parent must retain its original process
        // and partial-output facts; an exit code is not a denial/qualification.
        System.err.println("OSQ_OUTPUT_UNVERIFIED"); System.exit(74);
      }
    }
  }
}
