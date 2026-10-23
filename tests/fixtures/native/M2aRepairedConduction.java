// effect: TRUSTED_CODE
import com.comsol.model.Model;
import com.comsol.model.ParameterEntity;
import java.io.File;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

/** Task-local M2a 1D coefficient-PDE build/render on the injected ModelRef. */
public final class M2aRepairedConduction {
    private static String required(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (!(value instanceof String) || ((String) value).trim().isEmpty())
            throw new IllegalArgumentException("missing string argument: " + key);
        return (String) value;
    }

    public static Object run(Model model, Map<String, Object> args) {
        if (model == null || args == null) throw new IllegalArgumentException("injected model and arguments are required");
        String phase = required(args, "phase");
        if ("build".equals(phase)) return build(model, args);
        if ("configure_plot".equals(phase)) return configurePlot(model, args);
        if ("render".equals(phase)) return render(model, args);
        throw new IllegalArgumentException("unsupported phase");
    }

    private static boolean isMatrix(String type) {
        String t = type == null ? "" : type.toLowerCase();
        return t.contains("matrix") || t.endsWith("[][]");
    }

    private static boolean isArray(String type) {
        String t = type == null ? "" : type.toLowerCase();
        return !isMatrix(type) && (t.contains("array") || t.endsWith("[]"));
    }

    private static void setStringProperty(ParameterEntity feature, String name, String value) {
        if (!feature.hasProperty(name)) throw new IllegalStateException("required COMSOL property is absent: " + name);
        String type = feature.getValueType(name);
        if (type == null || !type.toLowerCase().contains("string"))
            throw new IllegalStateException("COMSOL property is not string-valued: " + name + " type=" + type);
        if (isMatrix(type)) feature.setIndex(name, value, 0, 0);
        else if (isArray(type)) feature.setIndex(name, value, 0);
        else feature.set(name, value);
    }

    private static String readStringProperty(ParameterEntity feature, String name) {
        String type = feature.getValueType(name);
        if (isMatrix(type)) {
            String[][] values = feature.getStringMatrix(name);
            if (values == null || values.length == 0 || values[0] == null || values[0].length == 0)
                throw new IllegalStateException("string matrix readback is empty: " + name);
            return values[0][0];
        }
        if (isArray(type)) {
            String[] values = feature.getStringArray(name);
            if (values == null || values.length == 0)
                throw new IllegalStateException("string array readback is empty: " + name);
            return values[0];
        }
        return feature.getString(name);
    }

    private static Map<String, Object> propertyMetadata(ParameterEntity feature, String name) {
        Map<String, Object> value = new LinkedHashMap<String, Object>();
        boolean present = feature.hasProperty(name);
        value.put("name", name);
        value.put("has_property", Boolean.valueOf(present));
        if (present) {
            value.put("value_type", feature.getValueType(name));
            String[] allowed = feature.getAllowedPropertyValues(name);
            value.put("allowed_values", allowed == null ? null : Arrays.asList(allowed));
        }
        return value;
    }

    private static Object build(Model model, Map<String, Object> args) {
        String component = required(args, "component_tag");
        String geometry = required(args, "geometry_tag");
        String mesh = required(args, "mesh_tag");
        String interval = required(args, "interval_tag");
        String physics = required(args, "physics_tag");
        String leftBoundary = required(args, "left_boundary_tag");
        String rightBoundary = required(args, "right_boundary_tag");
        String study = required(args, "study_tag");
        String step = required(args, "study_step_tag");
        String plot = required(args, "plot_group_tag");
        String line = required(args, "line_tag");
        String lengthName = required(args, "length_parameter");
        String conductivityName = required(args, "conductivity_parameter");
        String leftName = required(args, "left_temperature_parameter");
        String rightName = required(args, "right_temperature_parameter");
        String lengthExpression = required(args, "length_expression");
        String conductivityExpression = required(args, "conductivity_expression");
        String leftExpression = required(args, "left_temperature_expression");
        String rightExpression = required(args, "right_temperature_expression");
        String sourceExpression = required(args, "source_expression");
        String sourceUnit = required(args, "source_unit");

        boolean defaultComponentBefore = model.component().hasTag("comp1");
        if (!defaultComponentBefore) throw new IllegalStateException("expected default component comp1 is absent before build");
        if (model.component().hasTag(component)) throw new IllegalStateException("refusing to overwrite component tag " + component);
        String[] componentsBefore = model.component().tags();

        model.param().set(lengthName, lengthExpression);
        model.param().set(conductivityName, conductivityExpression);
        model.param().set(leftName, leftExpression);
        model.param().set(rightName, rightExpression);

        model.component().create(component, false);
        model.component(component).geom().create(geometry, 1);
        model.component(component).mesh().create(mesh, geometry);
        model.component(component).geom(geometry).create(interval, "Interval");
        model.component(component).geom(geometry).feature(interval).set("p1", "0[m]");
        model.component(component).geom(geometry).feature(interval).set("p2", lengthName);
        model.component(component).geom(geometry).run();

        model.component(component).physics().create(physics, "CoefficientFormPDE", geometry);
        ParameterEntity units = model.component(component).physics(physics).prop("Units");
        String[] unitPropertyNames = units.properties();
        Map<String, Object> dependentQuantityMetadata = propertyMetadata(units, "DependentVariableQuantity");
        Map<String, Object> customSourceUnitMetadata = propertyMetadata(units, "CustomSourceTermUnit");
        Map<String, Object> sourceQuantityMetadata = propertyMetadata(units, "SourceTermQuantity");
        if (!Boolean.TRUE.equals(dependentQuantityMetadata.get("has_property"))
                || !Boolean.TRUE.equals(customSourceUnitMetadata.get("has_property")))
            throw new IllegalStateException("same-version Units PropFeature lacks the documented properties");
        setStringProperty(units, "DependentVariableQuantity", "temperature");
        setStringProperty(units, "CustomSourceTermUnit", sourceUnit);
        String dependentQuantityReadback = readStringProperty(units, "DependentVariableQuantity");
        String customSourceUnitReadback = readStringProperty(units, "CustomSourceTermUnit");
        if (!"temperature".equals(dependentQuantityReadback) || !sourceUnit.equals(customSourceUnitReadback))
            throw new IllegalStateException("Units PropFeature readback did not exactly match requested metadata");

        ParameterEntity equation = model.component(component).physics(physics).feature("cfeq1");
        Map<String, Object> coefficientMetadata = propertyMetadata(equation, "c");
        Map<String, Object> sourceMetadata = propertyMetadata(equation, "f");
        setStringProperty(equation, "c", conductivityName);
        setStringProperty(equation, "f", sourceExpression);
        String conductivityReadback = readStringProperty(equation, "c");
        String sourceReadback = readStringProperty(equation, "f");
        if (!conductivityName.equals(conductivityReadback))
            throw new IllegalStateException("coefficient c readback mismatch");
        if (!sourceExpression.equals(sourceReadback))
            throw new IllegalStateException("source term f readback mismatch");

        model.component(component).physics(physics).create(leftBoundary, "DirichletBoundary", 0);
        model.component(component).physics(physics).feature(leftBoundary).selection().set(new int[] {1});
        ParameterEntity leftFeature = model.component(component).physics(physics).feature(leftBoundary);
        setStringProperty(leftFeature, "r", leftName);
        String leftReadback = readStringProperty(leftFeature, "r");
        model.component(component).physics(physics).create(rightBoundary, "DirichletBoundary", 0);
        model.component(component).physics(physics).feature(rightBoundary).selection().set(new int[] {2});
        ParameterEntity rightFeature = model.component(component).physics(physics).feature(rightBoundary);
        setStringProperty(rightFeature, "r", rightName);
        String rightReadback = readStringProperty(rightFeature, "r");

        model.component(component).mesh().run();
        model.study().create(study);
        model.study(study).create(step, "Stationary");
        model.result().create(plot, "PlotGroup1D");
        model.result(plot).feature().create(line, "LineGraph");
        model.result(plot).feature(line).set("expr", new String[] {"u+0[K]"});

        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("phase", "build");
        out.put("model_tag", model.tag());
        out.put("default_component_present_before", Boolean.valueOf(defaultComponentBefore));
        out.put("default_component_preserved_after", Boolean.valueOf(model.component().hasTag("comp1")));
        out.put("components_before", Arrays.asList(componentsBefore));
        out.put("components_after", Arrays.asList(model.component().tags()));
        out.put("component_tag", component);
        out.put("geometry_tag", geometry);
        out.put("geometry_dimension", Integer.valueOf(model.component(component).geom(geometry).getSDim()));
        out.put("geometry_domains", Integer.valueOf(model.component(component).geom(geometry).getNDomains()));
        out.put("mesh_tag", mesh);
        out.put("interval_tag", interval);
        out.put("physics_tag", physics);
        out.put("physics_type", model.component(component).physics(physics).getType());
        out.put("units_property_names", unitPropertyNames == null ? null : Arrays.asList(unitPropertyNames));
        out.put("units_property_metadata", Arrays.asList(new Object[] {dependentQuantityMetadata, customSourceUnitMetadata, sourceQuantityMetadata}));
        out.put("dependent_variable_quantity_readback", dependentQuantityReadback);
        out.put("custom_source_term_unit_readback", customSourceUnitReadback);
        out.put("coefficient_c_metadata", coefficientMetadata);
        out.put("source_f_metadata", sourceMetadata);
        out.put("coefficient_c_readback", conductivityReadback);
        out.put("source_f_requested", sourceExpression);
        out.put("source_f_readback", sourceReadback);
        out.put("source_f_readback_match", Boolean.valueOf(sourceExpression.equals(sourceReadback)));
        out.put("left_boundary_readback", leftReadback);
        out.put("right_boundary_readback", rightReadback);
        out.put("study_tag", study);
        out.put("study_step_tag", step);
        out.put("plot_group_tag", plot);
        out.put("plot_expression", "u+0[K]");
        out.put("model_util_global_management_used", Boolean.FALSE);
        return out;
    }

    private static Object configurePlot(Model model, Map<String, Object> args) {
        String component = required(args, "component_tag");
        String geometry = required(args, "geometry_tag");
        String plot = required(args, "plot_group_tag");
        String line = required(args, "line_tag");
        Object rawIndex = args.get("line_entity_index");
        if (!(rawIndex instanceof Number)) throw new IllegalArgumentException("missing numeric argument: line_entity_index");
        int entityIndex = ((Number) rawIndex).intValue();
        if (entityIndex < 1) throw new IllegalArgumentException("line_entity_index must be positive");
        int geometryDimension = model.component(component).geom(geometry).getSDim();
        model.result(plot).feature(line).selection().geom(geometry, geometryDimension);
        model.result(plot).feature(line).selection().set(entityIndex);
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("phase", "configure_plot");
        out.put("component_tag", component);
        out.put("geometry_tag", geometry);
        out.put("geometry_dimension", Integer.valueOf(geometryDimension));
        out.put("plot_group_tag", plot);
        out.put("line_tag", line);
        out.put("selected_entity_index", Integer.valueOf(entityIndex));
        return out;
    }

    private static Object render(Model model, Map<String, Object> args) {
        String plot = required(args, "plot_group_tag");
        String imageTag = required(args, "image_tag");
        String outputPath = required(args, "output_path");
        File output = new File(outputPath);
        if (output.exists()) throw new IllegalStateException("refusing to overwrite existing image artifact");
        File parent = output.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs())
            throw new IllegalStateException("could not create project-local image directory");
        model.result().export().create(imageTag, plot, "Image");
        model.result().export(imageTag).set("imagetype", "png");
        model.result().export(imageTag).set("pngfilename", output.getAbsolutePath());
        model.result().export(imageTag).run();
        if (!output.isFile() || output.length() < 8)
            throw new IllegalStateException("COMSOL image export did not produce a nonempty PNG");
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("phase", "render");
        out.put("image_tag", imageTag);
        out.put("output_path", output.getAbsolutePath());
        out.put("size_bytes", Long.valueOf(output.length()));
        out.put("exists", Boolean.valueOf(output.isFile()));
        return out;
    }

    /** Protocol regression only: successful invocation with an actual null return. */
    public static Object nullSuccess(Model model) {
        if (model == null) throw new IllegalArgumentException("injected model is required");
        return null;
    }
}
