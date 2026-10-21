import com.comsol.model.AbstractModel;
import com.comsol.model.FunctionFeature;
import com.comsol.model.Model;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Frozen no-solve native probe for function.evaluate, invoked on one fresh,
 * task-owned COMSOL Model through the trusted Java Worker. Setup definitions
 * are intentionally small and use no geometry, mesh, study, solver, dataset,
 * plot, import, refresh, training, solve, or run operation.
 */
public final class FunctionEvaluateProbe {
    private FunctionEvaluateProbe() {}

    private static List<String> strings(String[] values) {
        return values == null ? Collections.<String>emptyList() : Arrays.asList(values.clone());
    }

    private static Object invoke(FunctionFeature function, String getter, String property) {
        try {
            Method method = function.getClass().getMethod(getter, String.class);
            Object value = method.invoke(function, property);
            if (value instanceof String[]) return strings((String[]) value);
            if (value instanceof String[][]) {
                List<List<String>> rows = new ArrayList<>();
                for (String[] row : (String[][]) value) rows.add(strings(row));
                return rows;
            }
            return value;
        } catch (Throwable error) {
            return "UNREADABLE:" + error.getClass().getName() + ":" + String.valueOf(error.getMessage());
        }
    }

    private static Map<String, Object> functionState(FunctionFeature function) {
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("type", function.getType());
        state.put("function_names", strings(function.functionNames()));
        String[] properties = new String[] {"expr", "args", "argunit", "fununit", "complex", "nargs", "source", "table", "interp", "extrap", "argrange"};
        Map<String, Object> readback = new LinkedHashMap<>();
        for (String property : properties) {
            try {
                if (!function.hasProperty(property)) continue;
                String valueType = function.getValueType(property);
                String getter;
                if ("String".equals(valueType)) getter = "getString";
                else if ("StringArray".equals(valueType)) getter = "getStringArray";
                else if ("StringMatrix".equals(valueType)) getter = "getStringMatrix";
                else if ("Int".equals(valueType)) getter = "getInt";
                else if ("Boolean".equals(valueType)) getter = "getBoolean";
                else getter = "UNSUPPORTED:" + valueType;
                readback.put(property, "UNSUPPORTED:".equals(getter) || getter.startsWith("UNSUPPORTED:")
                    ? getter : invoke(function, getter, property));
            } catch (Throwable error) {
                readback.put(property, "UNREADABLE:" + error.getClass().getName() + ":" + String.valueOf(error.getMessage()));
            }
        }
        state.put("properties", readback);
        return state;
    }

    private static Map<String, Object> snapshot(Model model) {
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("global_function_tags", strings(model.func().tags()));
        state.put("component_tags", strings(model.component().tags()));
        state.put("parameter_names", strings(model.param().varnames()));
        state.put("study_tags", strings(model.study().tags()));
        state.put("solver_tags", strings(model.sol().tags()));
        state.put("dataset_tags", strings(model.result().dataset().tags()));
        Map<String, Object> functions = new LinkedHashMap<>();
        for (String tag : new String[] {"fe_global", "fe_complex", "fe_rate", "fe_q", "fe_interp_none", "fe_interp_linear"}) {
            if (model.func().hasTag(tag)) functions.put("root." + tag, functionState(model.func(tag)));
        }
        if (model.component().hasTag("fe_component")) {
            functions.put("root.fe_component.fe_local", functionState(model.component("fe_component").func("fe_local")));
            state.put("component_function_tags", strings(model.component("fe_component").func().tags()));
        }
        state.put("function_definitions", functions);
        return state;
    }

    private static void analytic(AbstractModel parent, String tag, String name, String expression,
                                 String[] arguments, String[] argumentUnits, String functionUnit,
                                 boolean complex) {
        FunctionFeature function = parent.func().create(tag, "Analytic");
        function.set("funcname", name);
        function.set("expr", expression);
        function.set("args", arguments);
        function.set("argunit", argumentUnits);
        function.set("fununit", new String[] {functionUnit});
        if (complex) function.set("complex", "on");
    }

    private static Map<String, Object> evaluate(Model model, String label, String expression) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("case", label);
        row.put("expression", expression);
        try {
            double[] pair = model.param().evaluateComplex(expression);
            row.put("evaluateComplex", pair == null ? null : pair.clone());
            row.put("value_status", "OBSERVED");
        } catch (Throwable error) {
            row.put("value_status", "ERROR");
            row.put("value_error_type", error.getClass().getName());
            row.put("value_error", String.valueOf(error.getMessage()));
        }
        try {
            row.put("evaluateUnit", model.param().evaluateUnit(expression));
            row.put("unit_status", "OBSERVED");
        } catch (Throwable error) {
            row.put("unit_status", "ERROR");
            row.put("unit_error_type", error.getClass().getName());
            row.put("unit_error", String.valueOf(error.getMessage()));
        }
        return row;
    }

    private static Map<String, Object> temporaryDerivative(Model model, String label, String tag,
                                                          String functionName, String derivativeExpression,
                                                          double expected) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("case", label);
        row.put("probe_kind", "temporary_analytic_function");
        row.put("tag", tag);
        row.put("function_name", functionName);
        row.put("definition", derivativeExpression);
        row.put("evaluation_expression", "root." + functionName + "(2,3)");
        row.put("expected", new double[] {expected, 0.0});
        row.put("global_function_tags_before", strings(model.func().tags()));

        FunctionFeature function = null;
        Throwable cleanupError = null;
        try {
            function = model.func().create(tag, "Analytic");
            row.put("create_returned", true);
            row.put("tag_present_after_create_attempt", model.func().hasTag(tag));
            function.set("funcname", functionName);
            function.set("expr", derivativeExpression);
            function.set("args", new String[] {"x", "y"});
            function.set("argunit", new String[] {"1", "1"});
            function.set("fununit", new String[] {"1"});
            row.put("configured_function_state", functionState(function));
            Map<String, Object> evaluation = evaluate(model, label, "root." + functionName + "(2,3)");
            row.putAll(evaluation);
            row.put("probe_status", "ERROR".equals(evaluation.get("value_status"))
                ? "PROBE_FAILED_API_UNSUPPORTED" : "EVALUATED");
        } catch (Throwable error) {
            row.put("probe_status", "PROBE_FAILED_API_UNSUPPORTED");
            row.put("probe_error_type", error.getClass().getName());
            row.put("probe_error", String.valueOf(error.getMessage()));
        } finally {
            try {
                boolean presentBeforeCleanup = model.func().hasTag(tag);
                row.put("tag_present_before_cleanup", presentBeforeCleanup);
                if (presentBeforeCleanup) model.func().remove(tag);
                boolean absent = !model.func().hasTag(tag);
                row.put("cleanup_status", presentBeforeCleanup
                    ? (absent ? "REMOVED" : "FAILED_TAG_REMAINS")
                    : "NOT_CREATED_VERIFIED_ABSENT");
                row.put("function_tag_absent_after_cleanup", absent);
                if (!absent) throw new IllegalStateException("temporary derivative function remains after remove: " + tag);
            } catch (Throwable error) {
                row.put("cleanup_status", "FAILED");
                row.put("cleanup_error_type", error.getClass().getName());
                row.put("cleanup_error", String.valueOf(error.getMessage()));
                cleanupError = error;
            }
        }
        if (cleanupError != null) throw new IllegalStateException("temporary derivative function cleanup failed: " + tag, cleanupError);
        row.put("global_function_tags_after", strings(model.func().tags()));
        return row;
    }

    private static Map<String, Object> notRunDerivative(String label, String reason, double expected) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("case", label);
        row.put("probe_kind", "temporary_analytic_function");
        row.put("probe_status", "NOT_RUN");
        row.put("reason", reason);
        row.put("expected", new double[] {expected, 0.0});
        return row;
    }

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null) throw new IllegalArgumentException("injected Model is required");
        String mode = arguments == null ? "prepare" : String.valueOf(arguments.getOrDefault("mode", "prepare"));
        if ("snapshot_only".equals(mode)) {
            Map<String, Object> response = new LinkedHashMap<>();
            response.put("probe_version", "function-evaluate-native/2.0.0");
            response.put("comsol_version", model.getComsolVersion());
            response.put("snapshot", snapshot(model));
            response.put("read_only", true);
            return response;
        }

        for (String tag : new String[] {"fe_global", "fe_complex", "fe_rate", "fe_q", "fe_interp_none", "fe_interp_linear"}) {
            if (model.func().hasTag(tag)) throw new IllegalStateException("fixture function tag already exists: " + tag);
        }
        if (model.component().hasTag("fe_component")) throw new IllegalStateException("fixture component already exists: fe_component");

        model.component().create("fe_component", false);
        analytic(model, "fe_global", "shared", "x+10*y", new String[] {"x", "y"}, new String[] {"1", "1"}, "1", false);
        analytic(model.component("fe_component"), "fe_local", "shared", "100+x+10*y", new String[] {"x", "y"}, new String[] {"1", "1"}, "1", false);
        analytic(model, "fe_complex", "complex_h", "(2+3*i)*x", new String[] {"x"}, new String[] {"1"}, "1", true);
        analytic(model, "fe_rate", "rate", "x/t", new String[] {"x", "t"}, new String[] {"m", "s"}, "m/s", false);
        analytic(model, "fe_q", "q", "2*x^2+3*x*y+5*y^2", new String[] {"x", "y"}, new String[] {"1", "1"}, "1", false);

        FunctionFeature interpolationNone = model.func().create("fe_interp_none", "Interpolation");
        interpolationNone.set("funcname", "interp_none");
        interpolationNone.set("source", "table");
        interpolationNone.set("nargs", 1);
        interpolationNone.set("table", new String[][] {{"0", "0"}, {"1", "10"}});
        interpolationNone.set("argunit", new String[] {"1"});
        interpolationNone.set("fununit", new String[] {"1"});
        interpolationNone.set("interp", "linear");
        interpolationNone.set("extrap", "none");
        FunctionFeature interpolationLinear = model.func().create("fe_interp_linear", "Interpolation");
        interpolationLinear.set("funcname", "interp_linear");
        interpolationLinear.set("source", "table");
        interpolationLinear.set("nargs", 1);
        interpolationLinear.set("table", new String[][] {{"0", "0"}, {"1", "10"}});
        interpolationLinear.set("argunit", new String[] {"1"});
        interpolationLinear.set("fununit", new String[] {"1"});
        interpolationLinear.set("interp", "linear");
        interpolationLinear.set("extrap", "linear");

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("probe_version", "function-evaluate-native/2.0.0");
        response.put("comsol_version", model.getComsolVersion());
        response.put("snapshot_before_direct_sampling", snapshot(model));
        List<Map<String, Object>> samples = new ArrayList<>();
        samples.add(evaluate(model, "global_shared_2_3", "root.shared(2,3)"));
        samples.add(evaluate(model, "component_shadow_shared_2_3", "root.fe_component.shared(2,3)"));
        samples.add(evaluate(model, "complex_h_2", "root.complex_h(2)"));
        samples.add(evaluate(model, "rate_m_s", "root.rate(2[m],3[s])"));
        samples.add(evaluate(model, "rate_cm_ms", "root.rate(200[cm],3000[ms])"));
        samples.add(evaluate(model, "interp_none_interior", "root.interp_none(0.5)"));
        samples.add(evaluate(model, "interp_none_left_endpoint", "root.interp_none(0)"));
        samples.add(evaluate(model, "interp_none_right_endpoint", "root.interp_none(1)"));
        samples.add(evaluate(model, "interp_none_left_outside", "root.interp_none(-0.1)"));
        samples.add(evaluate(model, "interp_none_right_outside", "root.interp_none(1.1)"));
        samples.add(evaluate(model, "interp_linear_right_outside", "root.interp_linear(1.1)"));
        response.put("direct_samples", samples);

        response.put("snapshot_before_derivative_candidates", snapshot(model));
        List<Map<String, Object>> derivatives = new ArrayList<>();
        String[][] candidates = new String[][] {
            {"dx", "fe_tmp_dx", "fe_tmp_dx", "d(root.q(x,y),x)", "17"},
            {"dy", "fe_tmp_dy", "fe_tmp_dy", "d(root.q(x,y),y)", "36"},
            {"dxx", "fe_tmp_dxx", "fe_tmp_dxx", "d(d(root.q(x,y),x),x)", "4"},
            {"dyy", "fe_tmp_dyy", "fe_tmp_dyy", "d(d(root.q(x,y),y),y)", "10"}
        };
        boolean stopAfterFailure = false;
        for (String[] candidate : candidates) {
            double expected = Double.parseDouble(candidate[4]);
            if (stopAfterFailure) {
                derivatives.add(notRunDerivative(candidate[0], "earlier temporary derivative candidate failed", expected));
                continue;
            }
            Map<String, Object> row = temporaryDerivative(
                model, candidate[0], candidate[1], candidate[2], candidate[3], expected);
            derivatives.add(row);
            stopAfterFailure = "PROBE_FAILED_API_UNSUPPORTED".equals(row.get("probe_status"))
                || "ERROR".equals(row.get("value_status"));
        }
        response.put("derivative_candidates", derivatives);
        response.put("snapshot_after_derivative_candidates", snapshot(model));
        response.put("all_derivative_temporaries_removed", derivatives.stream()
            .filter(row -> "temporary_analytic_function".equals(row.get("probe_kind")))
            .filter(row -> !"NOT_RUN".equals(row.get("probe_status")))
            .allMatch(row -> Boolean.TRUE.equals(row.get("function_tag_absent_after_cleanup"))));
        response.put("snapshot_after_direct_sampling", snapshot(model));
        response.put("solve_called", false);
        response.put("mutating_data_import_or_refresh_called", false);
        return response;
    }
}
