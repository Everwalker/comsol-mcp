// effect: TRUSTED_CODE
import com.comsol.model.Model;
import com.comsol.model.ParameterEntity;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

/** Small project-local Poisson problem on the injected model. */
public final class UniversalSmoke {
    private static void setScalarExpression(ParameterEntity feature, String name, String value) {
        String type = feature.getValueType(name);
        if (type != null && type.toLowerCase().contains("matrix")) feature.setIndex(name, value, 0, 0);
        else if (type != null && type.toLowerCase().contains("array")) feature.setIndex(name, value, 0);
        else feature.set(name, value);
    }

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null || !model.tag().equals(arguments.get("expected_model_tag")))
            throw new IllegalArgumentException("expected the bound model");
        model.param().set("smoke_source", "1");
        model.component().create("smoke_comp", false);
        model.component("smoke_comp").geom().create("smoke_geom", 2);
        model.component("smoke_comp").geom("smoke_geom").create("square", "Square");
        model.component("smoke_comp").geom("smoke_geom").feature("square").set("size", "1[m]");
        model.component("smoke_comp").geom("smoke_geom").run();
        model.component("smoke_comp").mesh().create("smoke_mesh", "smoke_geom");
        model.component("smoke_comp").physics().create("smoke_pde", "CoefficientFormPDE", "smoke_geom");
        setScalarExpression(model.component("smoke_comp").physics("smoke_pde").feature("cfeq1"), "f", "smoke_source");
        model.component("smoke_comp").physics("smoke_pde").create("zero", "DirichletBoundary", 1);
        model.component("smoke_comp").physics("smoke_pde").feature("zero").selection().all();
        setScalarExpression(model.component("smoke_comp").physics("smoke_pde").feature("zero"), "r", "0");
        model.component("smoke_comp").mesh("smoke_mesh").run();
        model.study().create("smoke_study");
        model.study("smoke_study").create("stationary", "Stationary");
        model.study("smoke_study").run();
        model.result().create("smoke_plot", "PlotGroup2D");
        model.result("smoke_plot").feature().create("surface", "Surface");
        model.result("smoke_plot").feature("surface").set("expr", "u");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("model_tag", model.tag());
        out.put("domain_count", model.component("smoke_comp").geom("smoke_geom").getNDomains());
        out.put("study_tags", Arrays.asList(model.study().tags()));
        out.put("solution_tags", Arrays.asList(model.sol().tags()));
        out.put("dataset_tags", Arrays.asList(model.result().dataset().tags()));
        return out;
    }
}
