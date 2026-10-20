import com.comsol.model.Model;
import com.comsol.model.GeomSequence;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Arrays;

/** Read actual unsolved domain settings on the injected public ModelRef only. */
public final class ScientificBuildReadback {
    public static Object run(Model model, Map<String, Object> args) {
        if (model == null || args == null || !model.tag().equals(args.get("expected_model_tag")))
            throw new IllegalArgumentException("readback requires the exact injected target");
        String domain = String.valueOf(args.get("domain"));
        String[] names;
        int dimension;
        if ("W22".equals(domain)) {
            names = new String[]{"L", "p0", "p1", "p2", "ptotal", "alpha", "Tamb"}; dimension = 3;
        } else if ("W23".equals(domain)) {
            names = new String[]{"lambda0", "f0", "n_core", "n_clad", "h_core", "x_in", "x_receiver"}; dimension = 2;
        } else if ("W24_SHAPE".equals(domain)) {
            names = new String[]{"Rdrop", "rhoGlue", "muGlue", "sigma0", "epsPF"}; dimension = 2;
        } else if ("W24_CURE".equals(domain)) {
            names = new String[]{"T0", "alpha0", "kUV", "Ea", "alpha_gel"}; dimension = 2;
        } else throw new IllegalArgumentException("closed domain required");
        GeomSequence geometry = "W22".equals(domain) ? model.geom("geom1") : model.component("comp1").geom("geom1");
        if (geometry.getSDim() != dimension || geometry.getNDomains() < 1)
            throw new IllegalStateException("domain geometry dimension or built domain count differs");
        if (domain.startsWith("W24") && (!geometry.isAxisymmetric() ||
            geometry.getNDomains() != ("W24_SHAPE".equals(domain) ? 2 : 4)))
            throw new IllegalStateException("W24 domain geometry differs from the actual fixture");
        Map<String,Object> parameters = new LinkedHashMap<>();
        for (String name : names) {
            double value = model.param().evaluate(name);
            if (!Double.isFinite(value)) throw new IllegalStateException("nonfinite native parameter: " + name);
            Map<String,Object> parameter = new LinkedHashMap<>();
            parameter.put("expression", model.param().get(name)); parameter.put("value", value);
            parameter.put("unit_raw", model.param().evaluateUnit(name));
            parameters.put(name, parameter);
        }
        Map<String,Object> output = new LinkedHashMap<>();
        output.put("schema", "COMSOL_SCIENTIFIC_UNSOLVED_BUILD_READBACK_V1");
        output.put("status", "READONLY_UNSOLVED_BUILD_METADATA");
        output.put("model_tag", model.tag()); output.put("domain", domain);
        output.put("geometry_dimension", geometry.getSDim());
        output.put("geometry_domains", geometry.getNDomains());
        output.put("geometry_bounds", geometry.getBoundingBox());
        output.put("axisymmetric", geometry.isAxisymmetric());
        output.put("parameters", parameters);
        output.put("material_tags", Arrays.asList(model.material().tags()));
        output.put("physics_tags", Arrays.asList("W22".equals(domain) ? model.physics().tags()
            : model.component("comp1").physics().tags()));
        output.put("study_tags", Arrays.asList(model.study().tags()));
        output.put("solution_tags", Arrays.asList(model.sol().tags()));
        output.put("dataset_tags", Arrays.asList(model.result().dataset().tags()));
        output.put("study_or_solver_invoked", false);
        output.put("numerical_physical_experimental_acceptance", "NOT_RUN");
        return output;
    }
}
