import com.comsol.model.FunctionFeature;
import com.comsol.model.Model;
import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The engine-side half of the W14.T034 missing-dependency fixture: save a model
 * whose interpolation function reads a file that will be deleted afterwards.
 *
 * The published operation surface can do this too (``function.create`` +
 * ``model.save``), and that is what the driver's own live path uses; this source
 * is the trusted-code route for deployments whose tool profile does not publish
 * those operations.  It performs only the two steps that need the engine - create
 * the file-sourced ``Interpolation`` function and save the model while the file
 * still exists - and then reports the state of both files.  Deleting the source
 * file, unloading the model and reloading it are harness steps: this source never
 * deletes anything, and it never creates, loads or clears a model.
 *
 * Documented API used here (all verified against the installed build with
 * ``javap`` on ``apiplugins/com.comsol.api_1.0.0.jar``, and compiled against it
 * by the offline checks):
 *
 * * ``model.func()`` / ``model.func(tag)`` returning ``FunctionFeatureList`` /
 *   ``FunctionFeature`` (``com.comsol.model.AbstractModel``);
 * * ``model.func().create(<ftag>, "Interpolation")`` - the type token the
 *   published ``function.create`` operation uses for the same node;
 * * the property names are the ones a container COMSOL 6.4 wrote for such a node
 *   enumerates as its defaults (``sourcetype``, ``filename``, ``funcname``,
 *   ``filetype``, ``dseparator`` - recorded
 *   ``evidence/phase4/runs/20260920T130620Z-g3-live/driver5c/local dir with
 *   spaces/中文目录/phase4-model 模型.mph``, ``dmodel.xml``
 *   ``FunctionFeature op="Interpolation" tag="int1"`` ``NodeDefaultValues``);
 * * ``model.save(<path>)`` (``com.comsol.model.Model``).
 */
public final class Phase4MissingDependency {
    // effect: WRITE
    private Phase4MissingDependency() {}

    /** The file-backed source mode of an Interpolation function. */
    private static final String SOURCE_TYPE_FILE = "file";

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null) {
            throw new IllegalArgumentException("injected Model is required");
        }
        String tag = "phase4_missing_dep";
        String savePath = "";
        String funcname = tag;
        if (arguments != null) {
            tag = stringArgument(arguments, "function_tag", tag);
            funcname = stringArgument(arguments, "funcname", tag);
            savePath = stringArgument(arguments, "save_path", "");
        }
        File source = null;
        if (arguments != null && arguments.get("source_file") instanceof String
                && !((String) arguments.get("source_file")).isEmpty()) {
            source = new File((String) arguments.get("source_file"));
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("model_tag", model.tag());
        result.put("function_tag", tag);
        result.put("save_path", savePath);
        result.put("source_file", source == null ? null : source.getAbsolutePath());
        result.put("source_file_exists_before", source != null && source.isFile());
        result.put("source_file_bytes", source != null && source.isFile() ? Long.valueOf(source.length()) : null);
        result.put("function_tags_before", tags(functionTags(model)));

        if (source == null) {
            result.put("status", "REFUSED");
            result.put("reason", "arguments.source_file is required: the whole point of the fixture is the file "
                    + "the function depends on");
            return result;
        }
        if (savePath.isEmpty()) {
            result.put("status", "REFUSED");
            result.put("reason", "arguments.save_path is required: the model has to be saved while the file exists");
            return result;
        }

        Map<String, Object> created = createInterpolation(model, tag);
        result.put("create", created);
        if (!tag.equals(created.get("created"))) {
            result.put("status", "REFUSED");
            result.put("reason", "the Interpolation function could not be created: " + created.get("status"));
            result.put("function_tags_after", tags(functionTags(model)));
            return result;
        }

        Map<String, Object> bindings = new LinkedHashMap<>();
        bindings.put("sourcetype", write(model, tag, "sourcetype", SOURCE_TYPE_FILE));
        bindings.put("filename", write(model, tag, "filename", source.getAbsolutePath()));
        bindings.put("funcname", write(model, tag, "funcname", funcname));
        bindings.put("filetype", write(model, tag, "filetype", "csv"));
        bindings.put("dseparator", write(model, tag, "dseparator", "space"));
        result.put("bindings", bindings);

        Map<String, Object> readback = new LinkedHashMap<>();
        readback.put("tag", tag);
        readback.put("type", describeType(model, tag));
        readback.put("sourcetype", read(model, tag, "sourcetype"));
        readback.put("filename", read(model, tag, "filename"));
        readback.put("funcname", read(model, tag, "funcname"));
        result.put("readback", readback);

        Map<String, Object> saved = new LinkedHashMap<>();
        saved.put("path", savePath);
        try {
            model.save(savePath);
            saved.put("status", "APPLIED");
        } catch (Exception exc) {
            saved.put("status", "REFUSED");
            saved.put("error", exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage()));
        }
        File target = new File(savePath);
        saved.put("file_exists", target.isFile());
        saved.put("file_bytes", target.isFile() ? Long.valueOf(target.length()) : null);
        result.put("save", saved);
        result.put("function_tags_after", tags(functionTags(model)));
        result.put("source_file_exists_after", source.isFile());
        result.put("harness_steps", Arrays.asList(
                "delete the source file (the dependency the reload must report)",
                "unload the model",
                "model.load the saved file and read the function back"));
        result.put("status", "APPLIED".equals(saved.get("status")) && target.isFile() ? "APPLIED" : "FAILED");
        return result;
    }

    private static Map<String, Object> createInterpolation(Model model, String tag) {
        Map<String, Object> out = new LinkedHashMap<>();
        List<String> existing = tags(functionTags(model));
        out.put("existing_tags", existing);
        if (existing.contains(tag)) {
            out.put("status", "TAG_CONFLICT");
            out.put("created", null);
            return out;
        }
        try {
            model.func().create(tag, "Interpolation");
            out.put("status", "CREATED");
            out.put("created", tag);
            out.put("type_readback", describeType(model, tag));
        } catch (Exception exc) {
            out.put("status", "REFUSED");
            out.put("created", null);
            out.put("error", exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage()));
        }
        return out;
    }

    private static Map<String, Object> write(Model model, String tag, String property, String value) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("property", property);
        out.put("requested", value);
        try {
            model.func(tag).set(property, value);
            out.put("status", "APPLIED");
            out.put("readback", read(model, tag, property));
        } catch (Exception exc) {
            out.put("status", "REFUSED");
            out.put("error", exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage()));
        }
        return out;
    }

    private static String read(Model model, String tag, String property) {
        try {
            return model.func(tag).getString(property);
        } catch (Exception ignored) {
            return null;
        }
    }

    private static String describeType(Model model, String tag) {
        try {
            return model.func(tag).getType();
        } catch (Exception ignored) {
            return null;
        }
    }

    private static String[] functionTags(Model model) {
        try {
            return model.func().tags();
        } catch (Exception ignored) {
            return null;
        }
    }

    private static List<String> tags(String[] raw) {
        List<String> out = new ArrayList<>();
        if (raw != null) {
            out.addAll(Arrays.asList(raw));
        }
        return out;
    }

    private static String stringArgument(Map<String, Object> arguments, String name, String fallback) {
        Object value = arguments.get(name);
        if (value instanceof String && !((String) value).isEmpty()) {
            return (String) value;
        }
        return fallback;
    }
}
