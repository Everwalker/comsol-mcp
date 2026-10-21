import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Creates the task-owned W08 fixture through the production injected Model.
 * The driver uses a fresh MCP-owned model by default, so this source never
 * loads or switches a hidden model and never uses a direct COMSOL connection.
 */
public final class Phase3Fixture {
    // effect: WRITE
    private Phase3Fixture() {}

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null) {
            throw new IllegalArgumentException("injected Model is required");
        }
        String componentTag = "phase3comp";
        String geometryTag = "phase3geom";
        String workPlaneTag = "wp3";
        if (arguments != null) {
            if (arguments.get("component") instanceof String && !((String) arguments.get("component")).isEmpty()) {
                componentTag = (String) arguments.get("component");
            }
            if (arguments.get("geometry") instanceof String && !((String) arguments.get("geometry")).isEmpty()) {
                geometryTag = (String) arguments.get("geometry");
            }
            if (arguments.get("work_plane") instanceof String && !((String) arguments.get("work_plane")).isEmpty()) {
                workPlaneTag = (String) arguments.get("work_plane");
            }
        }

        if (!model.component().hasTag(componentTag)) {
            model.component().create(componentTag, true);
        }
        if (!model.component(componentTag).geom().hasTag(geometryTag)) {
            model.component(componentTag).geom().create(geometryTag, 3);
        }
        GeomSequence geometry = model.component(componentTag).geom(geometryTag);
        if (!geometry.feature().hasTag(workPlaneTag)) {
            geometry.create(workPlaneTag, "WorkPlane");
        }
        geometry.feature(workPlaneTag).set("quickplane", "xy");
        GeomSequence local = geometry.feature(workPlaneTag).geom();
        if (!local.feature().hasTag("rectA")) local.create("rectA", "Rectangle");
        if (!local.feature().hasTag("rectB")) local.create("rectB", "Rectangle");
        if (!local.feature().hasTag("ptMatrix")) local.create("ptMatrix", "Point");
        if (!local.feature().hasTag("bezierInt")) local.create("bezierInt", "BezierPolygon");
        local.feature("rectA").set("pos", new String[]{"0", "0"});
        local.feature("rectA").set("size", new String[]{"1", "1"});
        local.feature("rectB").set("pos", new String[]{"2", "0"});
        local.feature("rectB").set("size", new String[]{"1", "1"});
        local.feature("ptMatrix").set("p", new double[][]{{0.0, 1.0}, {0.0, 1.0}});
        local.feature("bezierInt").set("degree", 1);
        local.feature("bezierInt").set("p", new double[][]{{0.0, 0.5}, {0.0, 0.5}});
        geometry.run();

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("component", componentTag);
        result.put("geometry", geometryTag);
        result.put("work_plane", workPlaneTag);
        result.put("local_features", new String[]{"rectA", "rectB", "ptMatrix", "bezierInt"});
        result.put("dimension", 3);
        return result;
    }
}
