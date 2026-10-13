import com.comsol.model.Model;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;

/** Export the sole injected model; never execute generated ModelUtil code. */
public final class P0ExportJava {
    // effect: FILE_WRITE
    private P0ExportJava() {}
    public static Object run(Model model, Map<String, Object> arguments) throws Exception {
        if (model == null || arguments == null || arguments.size() != 1 || !(arguments.get("workspace") instanceof String)) {
            throw new IllegalArgumentException("one injected Model and exact owned workspace required");
        }
        Path projects = Paths.get(System.getenv("COMSOL_PROJECT_ROOT")).toRealPath();
        Path workspace = Paths.get((String) arguments.get("workspace"));
        if (!workspace.isAbsolute() || !workspace.toRealPath().equals(workspace) || !workspace.startsWith(projects) || workspace.equals(projects)) {
            throw new IllegalArgumentException("workspace must be an existing task project below configured project root");
        }
        Path output = workspace.resolve("P0Export.java");
        if (Files.exists(output) || Files.isSymbolicLink(output)) {
            throw new IllegalArgumentException("export destination must be absent");
        }
        model.save(output.toString(), "java");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("destination", output.toString());
        result.put("bytes", Files.size(output));
        result.put("model_tag", model.tag());
        result.put("semantics", "EXPORTED_SOURCE_ONLY_NOT_EXECUTED");
        return result;
    }
}
