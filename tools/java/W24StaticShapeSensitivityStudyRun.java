import com.comsol.model.Model;
import com.comsol.model.SolverSequence;
import com.comsol.model.Study;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverFeatureList;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.channels.OverlappingFileLockException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.nio.file.attribute.PosixFilePermission;

/**
 * One explicitly managed W24 14-slot sensitivity solve action.
 *
 * The action is never called by setup/readback. Its only supported operation
 * submits one study.run for stdShape, which contains PhaseInitialization and
 * the transient phase-field evolution, then saves the solved model. The Python
 * managed campaign gate separately confirms the durable Worker submission
 * count and binds the resulting model revision before capture.
 */
public final class W24StaticShapeSensitivityStudyRun {
    private static final String[] CONFIGURATION_ORDER = {
        "baseline", "mesh_ratio_1_3", "mesh_ratio_1_1", "epsilon_6um",
        "epsilon_10um", "step_0_05Tc", "step_0_20Tc"};
    private W24StaticShapeSensitivityStudyRun() { }

    public static Object run(Model model, Map<String, Object> args) throws IOException {
        if (!"run".equals(String.valueOf(args.getOrDefault("action", "")))) {
            throw new IllegalArgumentException("action must be exactly run");
        }
        String caseId = token(args.get("case_id"), "case_id");
        if (!("flat".equals(caseId) || "step".equals(caseId))) {
            throw new IllegalArgumentException("case_id must be exactly flat or step");
        }
        String expectedTag = token(args.get("expected_model_tag"), "expected_model_tag");
        if (!expectedTag.equals(model.tag())) {
            throw new IllegalStateException("managed ModelRef tag differs from the requested static-shape model");
        }
        String configurationId = token(args.get("configuration_id"), "configuration_id");
        Configuration configuration = configuration(configurationId);
        int expectedIndex = exactInt(args.get("submission_index"), "submission_index");
        if (expectedIndex < 1 || expectedIndex > 14 ||
            !configurationId.equals(CONFIGURATION_ORDER[(expectedIndex - 1) / 2]) ||
            !caseId.equals((expectedIndex % 2 == 1) ? "flat" : "step") ||
            !Boolean.TRUE.equals(args.get("sensitivity_campaign"))) {
            throw new IllegalArgumentException("sensitivity slots must follow the exact seven-configuration flat/step order");
        }
        Path workspace = Path.of(pathArgument(args.get("workspace_path"), "workspace_path")).toRealPath();
        Path ledger = childPath(workspace, pathArgument(args.get("ledger_path"), "ledger_path"));
        Path save = childPath(workspace, pathArgument(args.get("save_path"), "save_path"));
        if (!ledger.getFileName().toString().equals("static_shape_sensitivity_study_runs.jsonl") ||
            !save.getFileName().toString().equals("static_shape_" + configurationId + "_" + caseId + "_solved.mph") ||
            Files.exists(save, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("sensitivity ledger/save path is not the exact new project output for this slot");
        }
        String slotKey = token(args.get("slot_idempotency_key"), "slot_idempotency_key");
        if (!slotKey.matches("w24-static-shape-slot-[0-9a-f]{64}")) {
            throw new IllegalArgumentException("slot_idempotency_key is not the frozen deterministic slot identity");
        }
        String approvalSha = token(args.get("approval_sha256"), "approval_sha256");
        if (!approvalSha.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException("approval_sha256 must be a lowercase SHA-256");
        }
        String campaignId = token(args.get("campaign_id"), "campaign_id");
        List<Map<String, Object>> allSlots = slotRecords(args.get("ordered_slots"));
        validateOrderedSlots(allSlots, expectedIndex, configurationId, caseId,
            model.tag(), slotKey, configuration);
        String slotHistory = token(args.get("slot_history_sha256"), "slot_history_sha256");
        if (!slotHistory.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException("slot_history_sha256 must be a lowercase SHA-256");
        }
        Path lockPath = childPath(workspace,
            ledger.resolveSibling("static_shape_sensitivity_study_runs.lock").toString());
        if (Files.exists(lockPath, LinkOption.NOFOLLOW_LINKS) &&
            (Files.isSymbolicLink(lockPath) || !Files.isRegularFile(lockPath, LinkOption.NOFOLLOW_LINKS))) {
            throw new IllegalStateException("persistent solve-slot lock must be a regular project child");
        }
        // Never truncate, unlink, or replace this inode: two concurrent callers
        // must contend on the same OS lock before observing or appending a slot.
        try (FileChannel lockChannel = FileChannel.open(lockPath, StandardOpenOption.CREATE,
                StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)) {
            try {
                Files.setPosixFilePermissions(lockPath, EnumSet.of(
                    PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE));
            } catch (UnsupportedOperationException ignoredOnWindows) {
                // Windows uses its native FileLock implementation and workspace ACL.
            }
            if (Files.isSymbolicLink(lockPath) || !Files.isRegularFile(lockPath, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalStateException("solve-slot lock path changed or is not a regular file");
            }
            FileLock lock;
            try {
                lock = lockChannel.tryLock();
            } catch (OverlappingFileLockException alreadyHeldInThisProcess) {
                throw new IllegalStateException("another W24 solve caller already owns the project lock",
                                                alreadyHeldInThisProcess);
            }
            if (lock == null) {
                throw new IllegalStateException("another W24 solve caller already owns the project lock");
            }
            try (FileLock held = lock) {
                return runLocked(model, workspace, ledger, save, caseId, configurationId,
                    expectedTag, expectedIndex, slotKey, approvalSha, campaignId,
                    allSlots, slotHistory, configuration);
            }
        }
    }

    private static Object runLocked(Model model, Path workspace, Path ledger, Path save,
                                    String caseId, String configurationId, String expectedTag, int expectedIndex,
                                    String slotKey, String approvalSha, String campaignId,
                                    List<Map<String, Object>> allSlots, String slotHistory,
                                    Configuration configuration) throws IOException {
        requireCaseIdentity(model, caseId);
        Study study = model.study("stdShape");
        String[] sequences = study.getSolverSequences("SolverSequence");
        if (sequences.length != 1) throw new IllegalStateException("stdShape must have exactly one solver sequence");
        SolverSequence sequence = model.sol(sequences[0]);
        if (!sequence.isAttached() || !"stdShape".equals(sequence.study())) {
            throw new IllegalStateException("stdShape solver sequence is detached or attached to another study");
        }
        String[] studySteps = study.feature().tags();
        if (!Arrays.equals(studySteps, new String[]{"phasei", "time"}) ||
            !"PhaseInitialization".equals(study.feature("phasei").getType()) ||
            !"Transient".equals(study.feature("time").getType())) {
            throw new IllegalStateException("stdShape must execute exactly PhaseInitialization then Transient");
        }
        SolverFeature time = uniqueTimeFeature(sequence);
        if (!"bdf".equals(time.getString("timemethod")) ||
            !"strict".equals(time.getString("tstepsbdf")) ||
            !"tsteps".equals(time.getString("tout")) || time.getInt("tstepsstore") != 1) {
            throw new IllegalStateException("stdShape strict BDF output/storage policy failed pre-run readback");
        }
        verifyConfiguration(model, time, configuration);
        int[] size = sequence.getSize();
        if (size == null || size.length != 2 || size[0] != 0 || size[1] != 0) {
            throw new IllegalStateException("sensitivity model already contains solution data; refusing reset or repeat");
        }
        int prior = appendSensitivitySubmissionIntent(ledger, expectedIndex,
            allSlots, slotKey, approvalSha, campaignId, slotHistory);

        long started = System.nanoTime();
        study.run();
        double elapsedSeconds = (System.nanoTime() - started) / 1.0e9;
        model.save(save.toString());
        if (!Files.isRegularFile(save, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(save) ||
            Files.size(save) <= 0L || !save.toRealPath().startsWith(workspace)) {
            throw new IllegalStateException("solved static-shape MPH did not persist as a project child");
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "NATIVE_STUDY_RUN_RETURNED");
        result.put("native_acceptance", "NOT_ESTABLISHED_CAPTURE_AND_SCIENCE_GATES_REQUIRED");
        result.put("case_id", caseId);
        result.put("configuration_id", configurationId);
        result.put("configuration_readback", Map.of(
            "mesh_hmax_m", configuration.meshHmaxM,
            "mesh_hmin_m", configuration.meshHminM,
            "epsilon_m", configuration.epsilonM,
            "maximum_step_s", configuration.maximumStepS));
        result.put("model_tag", model.tag());
        result.put("study_tag", "stdShape");
        result.put("study_step_order", Arrays.asList(studySteps));
        result.put("solver_sequence", sequences[0]);
        result.put("time_feature", time.tag());
        result.put("submission_index", prior);
        result.put("ordered_slots_sha256", slotHistory);
        result.put("elapsed_s", elapsedSeconds);
        result.put("study_run_calls_from_this_action", 1);
        result.put("phase_initialization_included", true);
        result.put("save_path", save.toString());
        result.put("size_bytes", Files.size(save));
        result.put("sha256", sha256(save));
        return result;
    }

    private static void requireCaseIdentity(Model model, String caseId) {
        String expected = "flat".equals(caseId) ? "sel_wet_flat_base" : "sel_wet_mesa_top";
        if (!Arrays.asList(model.component("comp1").selection().tags()).contains(expected)) {
            throw new IllegalStateException("static-shape model does not contain the requested fixture selections");
        }
        int[] glue = model.component("comp1").selection("sel_glue_domain").entities(2);
        int[] gas = model.component("comp1").selection("sel_gas_domain").entities(2);
        if (glue.length != 1 || gas.length != 1 || glue[0] == gas[0]) {
            throw new IllegalStateException("static-shape model lost its distinct glue/gas domain selections");
        }
    }

    private static int appendSensitivitySubmissionIntent(Path ledger, int expectedIndex,
            List<Map<String, Object>> allSlots, String slotKey, String approvalSha,
            String campaignId, String slotHistorySha) throws IOException {
        List<String> lines = new ArrayList<>();
        if (Files.exists(ledger, LinkOption.NOFOLLOW_LINKS)) {
            if (Files.isSymbolicLink(ledger) || !Files.isRegularFile(ledger, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalStateException("study ledger is not a regular project file");
            }
            for (String line : Files.readAllLines(ledger, StandardCharsets.UTF_8)) {
                if (!line.isBlank()) lines.add(line);
            }
        }
        if (lines.size() != expectedIndex - 1) {
            throw new IllegalStateException("durable local sensitivity ledger is not exactly at the next reviewed slot; no retry");
        }
        if (!slotHistorySha.equals(slotHistoryDigest(allSlots))) {
            throw new IllegalStateException("ordered sensitivity slot history hash differs from the Java-validated slot plan");
        }
        for (int index = 0; index < expectedIndex - 1; index++) {
            String expected = ledgerRow(allSlots.get(index), approvalSha, campaignId);
            if (!lines.get(index).equals(expected)) {
                throw new IllegalStateException("durable local ledger prefix differs from the exact approved sensitivity slots");
            }
        }
        Map<String, Object> current = allSlots.get(expectedIndex - 1);
        if (!slotKey.equals(current.get("slot_idempotency_key"))) {
            throw new IllegalStateException("current sensitivity slot key differs from the approved ordered slot");
        }
        String row = ledgerRow(current, approvalSha, campaignId);
        try (FileChannel channel = FileChannel.open(ledger, StandardOpenOption.CREATE,
                StandardOpenOption.WRITE, StandardOpenOption.APPEND, LinkOption.NOFOLLOW_LINKS)) {
            java.nio.ByteBuffer bytes = StandardCharsets.UTF_8.encode(row + "\n");
            while (bytes.hasRemaining()) channel.write(bytes);
            channel.force(true);
        }
        return expectedIndex;
    }

    private static String ledgerRow(Map<String, Object> slot, String approvalSha,
                                    String campaignId) {
        return "{\"event\":\"study_run_submitted\",\"submission_index\":" +
            slot.get("submission_index") + ",\"configuration_id\":\"" +
            slot.get("configuration_id") + "\",\"case_id\":\"" + slot.get("case_id") +
            "\",\"study_tag\":\"stdShape\",\"model_tag\":\"" + slot.get("model_tag") +
            "\",\"slot_idempotency_key\":\"" + slot.get("slot_idempotency_key") +
            "\",\"approval_sha256\":\"" + approvalSha + "\",\"campaign_id\":\"" +
            campaignId + "\"}";
    }

    private static List<Map<String, Object>> slotRecords(Object value) {
        if (!(value instanceof List<?>)) {
            throw new IllegalArgumentException("ordered_slots must contain the exact fourteen approved model slots");
        }
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object item : (List<?>) value) {
            if (!(item instanceof Map<?, ?>)) {
                throw new IllegalArgumentException("ordered_slots contains a non-object record");
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> row = (Map<String, Object>) item;
            java.util.Set<String> required = new java.util.HashSet<>(Arrays.asList(
                "submission_index", "configuration_id", "case_id", "model_tag",
                "slot_idempotency_key"));
            if (!row.keySet().equals(required)) {
                throw new IllegalArgumentException("ordered_slots record has missing or unreviewed fields");
            }
            out.add(row);
        }
        return out;
    }

    private static void validateOrderedSlots(List<Map<String, Object>> slots, int currentIndex,
            String configurationId, String caseId, String modelTag, String slotKey,
            Configuration configuration) {
        if (slots.size() != 14) throw new IllegalArgumentException("ordered_slots must contain exactly fourteen entries");
        java.util.Set<String> modelTags = new java.util.HashSet<>();
        java.util.Set<String> slotKeys = new java.util.HashSet<>();
        for (int index = 0; index < slots.size(); index++) {
            Map<String, Object> row = slots.get(index);
            String expectedConfiguration = CONFIGURATION_ORDER[index / 2];
            String expectedCase = index % 2 == 0 ? "flat" : "step";
            int declared = exactInt(row.get("submission_index"), "ordered_slots.submission_index");
            String declaredConfiguration = token(row.get("configuration_id"), "ordered_slots.configuration_id");
            String declaredCase = token(row.get("case_id"), "ordered_slots.case_id");
            String declaredModel = token(row.get("model_tag"), "ordered_slots.model_tag");
            String declaredKey = token(row.get("slot_idempotency_key"), "ordered_slots.slot_idempotency_key");
            if (declared != index + 1 || !expectedConfiguration.equals(declaredConfiguration) ||
                !expectedCase.equals(declaredCase) ||
                !declaredKey.matches("w24-static-shape-slot-[0-9a-f]{64}") ||
                !modelTags.add(declaredModel) || !slotKeys.add(declaredKey)) {
                throw new IllegalArgumentException("ordered_slots differs from the unique frozen configuration-major order");
            }
        }
        Map<String, Object> current = slots.get(currentIndex - 1);
        if (!configurationId.equals(current.get("configuration_id")) ||
            !caseId.equals(current.get("case_id")) || !modelTag.equals(current.get("model_tag")) ||
            !slotKey.equals(current.get("slot_idempotency_key"))) {
            throw new IllegalArgumentException("current model/case/configuration does not match its ordered slot");
        }
        if (configuration.id == null) throw new IllegalStateException("configuration mapping is incomplete");
    }

    private static String slotHistoryDigest(List<Map<String, Object>> slots) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (Map<String, Object> row : slots) {
                String canonical = exactInt(row.get("submission_index"), "submission_index") + "|" +
                    token(row.get("configuration_id"), "configuration_id") + "|" +
                    token(row.get("case_id"), "case_id") + "|" +
                    token(row.get("model_tag"), "model_tag") + "|" +
                    token(row.get("slot_idempotency_key"), "slot_idempotency_key") + "\n";
                digest.update(canonical.getBytes(StandardCharsets.UTF_8));
            }
            StringBuilder hex = new StringBuilder();
            for (byte value : digest.digest()) hex.append(String.format("%02x", value & 0xff));
            return hex.toString();
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("JVM lacks SHA-256", impossible);
        }
    }

    private static void verifyConfiguration(Model model, SolverFeature time,
                                            Configuration expected) {
        double epsilon = model.param().evaluate("epsPF");
        com.comsol.model.MeshSequence mesh = model.component("comp1").mesh("mesh1");
        double hmax = mesh.feature("size").getDouble("hmax");
        double hmin = mesh.feature("size").getDouble("hmin");
        double maximumStep = time.getDouble("maxstepbdf");
        String custom = mesh.feature("size").getString("custom");
        if (!Double.isFinite(epsilon) || !Double.isFinite(hmax) || !Double.isFinite(hmin) ||
            !Double.isFinite(maximumStep) ||
            Math.abs(epsilon - expected.epsilonM) > Math.max(1e-14, expected.epsilonM * 1e-12) ||
            Math.abs(hmax - expected.meshHmaxM) > Math.max(1e-15, expected.meshHmaxM * 1e-12) ||
            Math.abs(hmin - expected.meshHminM) > Math.max(1e-15, expected.meshHminM * 1e-12) ||
            Math.abs(maximumStep - expected.maximumStepS) > Math.max(1e-15, expected.maximumStepS * 1e-12) ||
            !("on".equalsIgnoreCase(custom) || "true".equalsIgnoreCase(custom) || "1".equals(custom))) {
            throw new IllegalStateException("native epsilon, mesh size, or maximum time step differs from the exact sensitivity config");
        }
    }

    private static Configuration configuration(String id) {
        switch (id) {
            case "baseline": return new Configuration(id, 8e-6, 4e-6, 2e-6, 0.10);
            case "mesh_ratio_1_3": return new Configuration(id, 8e-6, 8e-6 / 3.0, 2e-6, 0.10);
            case "mesh_ratio_1_1": return new Configuration(id, 8e-6, 8e-6, 2e-6, 0.10);
            case "epsilon_6um": return new Configuration(id, 6e-6, 3e-6, 1.5e-6, 0.10);
            case "epsilon_10um": return new Configuration(id, 10e-6, 5e-6, 2.5e-6, 0.10);
            case "step_0_05Tc": return new Configuration(id, 8e-6, 4e-6, 2e-6, 0.05);
            case "step_0_20Tc": return new Configuration(id, 8e-6, 4e-6, 2e-6, 0.20);
            default: throw new IllegalArgumentException("configuration_id is not one of the seven preregistered variants");
        }
    }

    private static final class Configuration {
        final String id;
        final double epsilonM;
        final double meshHmaxM;
        final double meshHminM;
        final double maximumStepS;

        Configuration(String id, double epsilonM, double meshHmaxM, double meshHminM,
                      double maximumStepOverTc) {
            this.id = id;
            this.epsilonM = epsilonM;
            this.meshHmaxM = meshHmaxM;
            this.meshHminM = meshHminM;
            this.maximumStepS = maximumStepOverTc / 60.0;
        }
    }


    private static SolverFeature uniqueTimeFeature(SolverSequence sequence) {
        Map<String, SolverFeature> matches = new LinkedHashMap<>();
        collectTimeFeatures(sequence.feature().tags(), sequence.feature(), "", matches);
        if (matches.size() != 1) throw new IllegalStateException("expected exactly one Time solver feature");
        return matches.values().iterator().next();
    }

    private static void collectTimeFeatures(String[] tags, SolverFeatureList list, String parent,
                                            Map<String, SolverFeature> matches) {
        for (String tag : tags) {
            SolverFeature feature = list.get(tag);
            String path = parent.isEmpty() ? tag : parent + "/" + tag;
            if ("Time".equals(feature.getType())) matches.put(path, feature);
            String[] children = feature.feature().tags();
            if (children.length > 0) collectTimeFeatures(children, feature.feature(), path, matches);
        }
    }

    private static Path childPath(Path workspace, String supplied) throws IOException {
        Path path = Path.of(supplied).toAbsolutePath().normalize();
        if (Files.isSymbolicLink(Path.of(supplied)) || !path.startsWith(workspace) || path.equals(workspace) ||
            path.getParent() == null || !path.getParent().toRealPath().equals(path.getParent())) {
            throw new IllegalArgumentException("study action path must be a project-owned regular-file child");
        }
        return path;
    }

    private static String sha256(Path path) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (java.io.InputStream input = Files.newInputStream(path)) {
                byte[] buffer = new byte[1024 * 1024];
                int count;
                while ((count = input.read(buffer)) >= 0) digest.update(buffer, 0, count);
            }
            StringBuilder text = new StringBuilder();
            for (byte value : digest.digest()) text.append(String.format("%02x", value & 0xff));
            return text.toString();
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("Java runtime has no SHA-256", impossible);
        }
    }

    private static String token(Object value, String name) {
        if (!(value instanceof String) || ((String) value).isBlank()) {
            throw new IllegalArgumentException(name + " must be a nonempty string");
        }
        String text = (String) value;
        if (!text.matches("[A-Za-z0-9_./-]{1,256}")) {
            throw new IllegalArgumentException(name + " contains unsupported characters");
        }
        return text;
    }

    private static String pathArgument(Object value, String name) {
        if (!(value instanceof String) || ((String) value).isBlank() ||
            ((String) value).indexOf('\0') >= 0) {
            throw new IllegalArgumentException(name + " must be a nonempty filesystem path");
        }
        return (String) value;
    }

    private static int exactInt(Object value, String name) {
        if (!(value instanceof Number) || value instanceof Boolean) {
            throw new IllegalArgumentException(name + " must be an integer");
        }
        double number = ((Number) value).doubleValue();
        if (!Double.isFinite(number) || number != Math.rint(number) || number < Integer.MIN_VALUE ||
            number > Integer.MAX_VALUE) throw new IllegalArgumentException(name + " must be an exact integer");
        return (int) number;
    }
}
