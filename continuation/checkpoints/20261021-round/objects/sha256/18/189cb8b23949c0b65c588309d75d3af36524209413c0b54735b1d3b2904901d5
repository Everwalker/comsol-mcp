import com.comsol.model.Model;
import com.comsol.model.util.ModelUtil;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Bounded fingerprint diagnostic for the managed transaction trial path.
 *
 * Executed only through code.execute_java with the injected Model. It saves a
 * copy, loads that copy, mutates only the copy, removes the copy model and
 * deletes the temporary artifact. Main-model snapshots use public Model APIs;
 * this is a write-scoped diagnostic, not an acceptance action.
 */
// effect: WRITE
public final class Phase3TrialFingerprint {
    private Phase3TrialFingerprint() {}

    private static Map<String, Object> snapshot(Model model) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("tag", model.tag());
        result.put("label", model.label());
        result.put("file_path", model.getFilePath());
        result.put("time_modified", String.valueOf(model.timeModified()));

        Map<String, String> parameters = new LinkedHashMap<>();
        String[] names = model.param().varnames();
        if (names != null) {
            for (String name : names) {
                parameters.put(name, model.param().get(name));
            }
        }
        result.put("parameters", parameters);
        return result;
    }

    private static boolean hasTag(String tag) {
        String[] tags = ModelUtil.tags();
        if (tags == null) return false;
        for (String item : tags) {
            if (tag.equals(item)) return true;
        }
        return false;
    }

    private static void failure(Map<String, Object> result, String stage, Exception error) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("stage", stage);
        row.put("type", error.getClass().getName());
        row.put("message", String.valueOf(error.getMessage()));
        result.put("failure", row);
    }

    public static Object run(Model model, Map<String, Object> arguments) throws Exception {
        if (model == null) {
            throw new IllegalArgumentException("injected Model is required");
        }
        String copyPath = arguments != null && arguments.get("copy_path") instanceof String
                ? (String) arguments.get("copy_path") : "";
        String copyTag = arguments != null && arguments.get("copy_tag") instanceof String
                ? (String) arguments.get("copy_tag") : "phase3_trial_diag_copy";
        if (copyPath.isEmpty()) {
            throw new IllegalArgumentException("copy_path is required");
        }

        Path artifact = Paths.get(copyPath);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("stage", "before");
        result.put("copy_tag", copyTag);
        result.put("copy_path_exists_before", Files.exists(artifact));
        result.put("copy_tag_present_before", hasTag(copyTag));
        result.put("main_before", snapshot(model));

        if (Files.exists(artifact)) {
            failure(result, "precondition_copy_path", new IllegalStateException("copy_path already exists"));
            return result;
        }
        if (hasTag(copyTag)) {
            failure(result, "precondition_copy_tag", new IllegalStateException("copy_tag already exists"));
            return result;
        }
        if (artifact.getParent() == null || !Files.isDirectory(artifact.getParent())) {
            failure(result, "precondition_copy_parent", new IllegalStateException("copy_path parent directory is missing"));
            return result;
        }

        Model copy = null;
        boolean saved = false;
        try {
            try {
                model.save(copyPath, true);
                saved = true;
                result.put("main_after_save_copy", snapshot(model));
            } catch (Exception error) {
                failure(result, "save_copy", error);
            }

            if (saved) {
                try {
                    copy = ModelUtil.load(copyTag, copyPath);
                    result.put("main_after_load_copy", snapshot(model));
                    result.put("copy_before_edit", snapshot(copy));
                } catch (Exception error) {
                    failure(result, "load_copy", error);
                }
            }

            if (copy != null && !result.containsKey("failure")) {
                try {
                    copy.comments("phase3-trial-copy-edit");
                    copy.param().set("phase3_trial_copy_only", "1");
                    result.put("copy_after_edit", snapshot(copy));
                    result.put("main_after_copy_edit", snapshot(model));
                } catch (Exception error) {
                    failure(result, "edit_copy", error);
                }
            }
        } finally {
            if (copy != null) {
                try {
                    ModelUtil.remove(copy.tag());
                } catch (Exception error) {
                    failure(result, "remove_copy_model", error);
                }
                result.put("copy_tag_present_after_remove", hasTag(copyTag));
                result.put("main_after_remove_copy", snapshot(model));
            }
            if (saved) {
                try {
                    result.put("artifact_deleted", Files.deleteIfExists(artifact));
                } catch (Exception error) {
                    failure(result, "delete_copy_artifact", error);
                }
                result.put("artifact_exists_after_cleanup", Files.exists(artifact));
            }
        }
        result.put("stage", result.containsKey("failure") ? "failed" : "complete");
        return result;
    }
}
