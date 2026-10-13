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
 * One explicitly managed W24 static-shape solve action.
 *
 * The action is never called by setup/readback. Its only supported operation
 * submits one study.run for stdShape, which contains PhaseInitialization and
 * the transient phase-field evolution, then saves the solved model. The Python
 * managed campaign gate separately confirms the durable Worker submission
 * count and binds the resulting model revision before capture.
 */
public final class W24StaticShapeStudyRun {
    private W24StaticShapeStudyRun() { }

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
        int expectedIndex = exactInt(args.get("submission_index"), "submission_index");
        if (expectedIndex < 1 || expectedIndex > 2 ||
            (expectedIndex == 1 && !"flat".equals(caseId)) ||
            (expectedIndex == 2 && !"step".equals(caseId))) {
            throw new IllegalArgumentException("baseline pair order is exactly flat then step, one submission each");
        }
        Path workspace = Path.of(pathArgument(args.get("workspace_path"), "workspace_path")).toRealPath();
        Path ledger = childPath(workspace, pathArgument(args.get("ledger_path"), "ledger_path"));
        Path save = childPath(workspace, pathArgument(args.get("save_path"), "save_path"));
        if (!ledger.getFileName().toString().equals("static_shape_study_runs.jsonl") ||
            !save.getFileName().toString().equals("static_shape_" + caseId + "_solved.mph") ||
            Files.exists(save, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("study ledger/save path is not the exact new project output for this case");
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
        Path lockPath = childPath(workspace,
            ledger.resolveSibling("static_shape_study_runs.lock").toString());
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
                return runLocked(model, workspace, ledger, save, caseId, expectedTag,
                    expectedIndex, slotKey, approvalSha, campaignId,
                    optionalToken(args.get("previous_slot_idempotency_key"),
                                  "previous_slot_idempotency_key"),
                    optionalToken(args.get("previous_model_tag"), "previous_model_tag"));
            }
        }
    }

    private static Object runLocked(Model model, Path workspace, Path ledger, Path save,
                                    String caseId, String expectedTag, int expectedIndex,
                                    String slotKey, String approvalSha, String campaignId,
                                    String previousSlotKey, String previousModelTag) throws IOException {
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
        int[] size = sequence.getSize();
        if (size == null || size.length != 2 || size[0] != 0 || size[1] != 0) {
            throw new IllegalStateException("baseline model already contains solution data; refusing reset or repeat");
        }
        int prior = appendSubmissionIntent(ledger, expectedIndex, caseId, model.tag(),
            slotKey, approvalSha, campaignId, previousSlotKey, previousModelTag,
            expectedIndex == 2 ? "flat" : null);

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
        result.put("model_tag", model.tag());
        result.put("study_tag", "stdShape");
        result.put("study_step_order", Arrays.asList(studySteps));
        result.put("solver_sequence", sequences[0]);
        result.put("time_feature", time.tag());
        result.put("submission_index", prior);
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

    private static int appendSubmissionIntent(Path ledger, int expectedIndex,
                                               String caseId, String modelTag,
                                               String slotKey, String approvalSha,
                                               String campaignId, String previousSlotKey,
                                               String previousModelTag,
                                               String previousCaseId) throws IOException {
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
            throw new IllegalStateException("durable local study ledger does not match the next frozen pair slot");
        }
        if (lines.stream().anyMatch(line -> line.contains("\"slot_idempotency_key\":\"" + slotKey + "\""))) {
            throw new IllegalStateException("this solve slot already has an admitted intent; refusal is not a retry");
        }
        if (expectedIndex == 2) {
            String priorRow = "{\"event\":\"study_run_submitted\",\"submission_index\":1,\"case_id\":\"" +
                previousCaseId + "\",\"study_tag\":\"stdShape\",\"model_tag\":\"" +
                ("flat".equals(previousCaseId) && previousModelTag != null ? previousModelTag : "") +
                "\",\"slot_idempotency_key\":\"" + previousSlotKey + "\",\"approval_sha256\":\"" +
                approvalSha + "\",\"campaign_id\":\"" + campaignId + "\"}";
            if (previousSlotKey == null || previousModelTag == null || !lines.get(0).equals(priorRow)) {
                throw new IllegalStateException("step solve requires the exact prior flat slot from this approval/campaign");
            }
        }
        String row = "{\"event\":\"study_run_submitted\",\"submission_index\":" + expectedIndex +
            ",\"case_id\":\"" + caseId + "\",\"study_tag\":\"stdShape\",\"model_tag\":\"" +
            modelTag + "\",\"slot_idempotency_key\":\"" + slotKey + "\",\"approval_sha256\":\"" +
            approvalSha + "\",\"campaign_id\":\"" + campaignId + "\"}";
        try (FileChannel channel = FileChannel.open(ledger, StandardOpenOption.CREATE,
                StandardOpenOption.WRITE, StandardOpenOption.APPEND, LinkOption.NOFOLLOW_LINKS)) {
            java.nio.ByteBuffer bytes = StandardCharsets.UTF_8.encode(row + "\n");
            while (bytes.hasRemaining()) channel.write(bytes);
            channel.force(true);
        }
        return expectedIndex;
    }

    private static String optionalToken(Object value, String name) {
        if (value == null || "".equals(value)) return null;
        return token(value, name);
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
