import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import java.util.LinkedHashMap;
import java.util.Map;

/** Task-owned geometry-only fixture for the artifact registration/import probe. */
public final class NativeArtifactGeometryFixture {
    public static Object run(Model model, Map<String, Object> args) {
        String phase = String.valueOf(args.get("phase"));
        if ("export".equals(phase)) {
            String path = String.valueOf(args.get("path"));
            model.component().create("comp1");
            GeomSequence geometry = model.component("comp1").geom().create("geom1", 2);
            geometry.lengthUnit("m");
            geometry.feature().create("rect1", "Rectangle");
            geometry.feature("rect1").set("base", "corner");
            geometry.feature("rect1").set("pos", new String[]{"0[m]", "0[m]"});
            geometry.feature("rect1").set("size", new String[]{"2[m]", "1[m]"});
            geometry.run();
            String exported = geometry.exportFinal(path);

            Map<String, Object> result = new LinkedHashMap<>();
            result.put("phase", phase);
            result.put("exported_path", exported);
            // GeomSequence inherits these GeomInfo methods from GeomContainer.
            result.put("source_dimension", geometry.getSDim());
            result.put("source_domains", geometry.getNDomains());
            result.put("source_bounding_box", geometry.getBoundingBox());
            result.put("source_length_unit", geometry.lengthUnit());
            return result;
        }
        if ("target".equals(phase)) {
            model.component().create("comp1");
            GeomSequence geometry = model.component("comp1").geom().create("geom1", 2);
            geometry.lengthUnit("m");

            Map<String, Object> result = new LinkedHashMap<>();
            result.put("phase", phase);
            result.put("target_dimension", geometry.getSDim());
            result.put("target_length_unit", geometry.lengthUnit());
            return result;
        }
        throw new IllegalArgumentException("phase must be export or target");
    }
}
