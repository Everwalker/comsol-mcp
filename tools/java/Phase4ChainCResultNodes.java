import com.comsol.model.Model;
import com.comsol.model.NumericalFeature;
import com.comsol.model.TableFeature;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Derived Values node + Table + their data association for the chain C
 * user-style fixture.
 *
 * The published G3 operation surface has no result-node operation
 * (``result.numerical_manage``/``result.table_manage`` are
 * ``PROPOSED_NOT_IMPLEMENTED``), so the only published route to the result
 * nodes a user model carries is the trusted-code operation
 * (``code.execute_java``).  This source never claims more than the engine
 * confirms: it creates at most one numerical node and one table, associates
 * them through the numerical node's own ``table`` property, and returns a
 * readback of every step.  A refusal (no dataset, no solution, a build that
 * does not know a type token) is recorded as a refusal - never as a pass.
 *
 * Documented calls used here (COMSOL 6.4 local corpus):
 *
 * * ``model.result().numerical().create(<ftag>, "EvalGlobal")`` - results API
 *   page ``comsol_api_results.52.051.html`` and Programming Reference p.799
 *   (``result().numerical().create``); the Application Programming Guide's
 *   results example
 *   (``application_programming_guide.15.28.html``) creates a numerical node
 *   with a type token, sets ``expr``, creates a table with
 *   ``result().table().create("tbl1", "Table")``, associates it with
 *   ``result().numerical("max1").set("table", "tbl1")`` and fills it with
 *   ``setResult()``;
 * * the dataset *association* is the numerical node's ``data`` property
 *   (Programming Reference Table 7-37, ``data`` "The dataset this feature
 *   refers to").
 *
 * Method availability was verified against the installed build with
 * ``javap`` (``NumericalFeatureList.create(String,String)``,
 * ``NumericalFeature.set/getString``, ``TableFeatureList.create(String,String)``,
 * ``TableFeature.addColumns(String[],double[][])``/``getFilledReal()``,
 * ``PropFeatureList.tags()``); the class is compiled against
 * ``apiplugins/com.comsol.api_1.0.0.jar`` as part of the offline checks.
 *
 * The injected Model is the only model this source touches: it never creates,
 * loads or clears a model.
 */
public final class Phase4ChainCResultNodes {
    // effect: WRITE
    private Phase4ChainCResultNodes() {}

    private static final List<String> NUMERICAL_TYPE_CANDIDATES =
            Arrays.asList("EvalGlobal", "MaxVolume", "AvVolume");

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null) {
            throw new IllegalArgumentException("injected Model is required");
        }
        String dataset = "dset1";
        String expression = "T";
        String numericalTag = "cder1";
        String tableTag = "ctbl1";
        if (arguments != null) {
            dataset = stringArgument(arguments, "dataset", dataset);
            expression = stringArgument(arguments, "expression", expression);
            numericalTag = stringArgument(arguments, "numerical_tag", numericalTag);
            tableTag = stringArgument(arguments, "table_tag", tableTag);
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("model_tag", model.tag());
        result.put("requested", requested(dataset, expression, numericalTag, tableTag));
        result.put("numerical_tags_before", tags(numericalTags(model)));
        result.put("table_tags_before", tags(tableTags(model)));

        Map<String, Object> numerical = createNumericalNode(model, numericalTag);
        result.put("numerical", numerical);
        String created = numerical.get("created") instanceof String ? (String) numerical.get("created") : null;

        if (created != null) {
            result.put("numerical_bindings", bindNumericalNode(model, created, dataset, expression));
        }

        Map<String, Object> table = createTableNode(model, tableTag);
        result.put("table", table);
        String tableCreated = table.get("created") instanceof String ? (String) table.get("created") : null;

        if (created != null && tableCreated != null) {
            result.put("association", associate(model, created, tableCreated));
        }

        result.put("numerical_tags_after", tags(numericalTags(model)));
        result.put("table_tags_after", tags(tableTags(model)));
        Map<String, Object> root = new LinkedHashMap<>();
        root.put("created", created);
        root.put("table_created", tableCreated);
        root.put("readback", readback(model, created, tableCreated));
        result.put("readback", root);
        return result;
    }

    private static Map<String, Object> requested(String dataset, String expression, String numericalTag, String tableTag) {
        Map<String, Object> requested = new LinkedHashMap<>();
        requested.put("dataset", dataset);
        requested.put("expression", expression);
        requested.put("numerical_tag", numericalTag);
        requested.put("table_tag", tableTag);
        return requested;
    }

    private static Map<String, Object> createNumericalNode(Model model, String tag) {
        Map<String, Object> out = new LinkedHashMap<>();
        List<Object> attempts = new ArrayList<>();
        List<String> existing = tags(numericalTags(model));
        out.put("tag", tag);
        out.put("existing_tags", existing);
        if (existing.contains(tag)) {
            out.put("status", "TAG_CONFLICT");
            out.put("attempts", attempts);
            out.put("created", null);
            return out;
        }
        String created = null;
        for (String candidate : NUMERICAL_TYPE_CANDIDATES) {
            Map<String, Object> attempt = new LinkedHashMap<>();
            attempt.put("type_id", candidate);
            try {
                model.result().numerical().create(tag, candidate);
                String typeReadback = null;
                try {
                    typeReadback = model.result().numerical(tag).getType();
                } catch (Exception ignored) {
                    typeReadback = null;
                }
                attempt.put("status", "CREATED");
                attempt.put("type_readback", typeReadback);
                created = tag;
                attempts.add(attempt);
                break;
            } catch (Exception exc) {
                attempt.put("status", "REFUSED");
                attempt.put("error", describe(exc));
                attempts.add(attempt);
            }
        }
        out.put("attempts", attempts);
        out.put("created", created);
        out.put("status", created == null ? "REFUSED" : "CREATED");
        return out;
    }

    private static Map<String, Object> createTableNode(Model model, String tag) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("tag", tag);
        List<String> existing = tags(tableTags(model));
        out.put("existing_tags", existing);
        if (existing.contains(tag)) {
            out.put("status", "TAG_CONFLICT");
            out.put("created", null);
            return out;
        }
        try {
            model.result().table().create(tag, "Table");
            out.put("status", "CREATED");
            out.put("created", tag);
        } catch (Exception exc) {
            out.put("status", "REFUSED");
            out.put("error", describe(exc));
            out.put("created", null);
        }
        return out;
    }

    private static Map<String, Object> bindNumericalNode(Model model, String tag, String dataset, String expression) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("path", "result/numerical/" + tag);
        out.put("dataset", writeProperty(model, tag, "data", dataset));
        out.put("expression", writeExpression(model, tag, expression));
        return out;
    }

    private static Map<String, Object> writeProperty(Model model, String tag, String property, String value) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("property", property);
        out.put("requested", value);
        try {
            model.result().numerical(tag).set(property, value);
            out.put("status", "APPLIED");
            out.put("readback", readProperty(model, tag, property));
        } catch (Exception exc) {
            out.put("status", "REFUSED");
            out.put("error", describe(exc));
        }
        return out;
    }

    private static Map<String, Object> writeExpression(Model model, String tag, String expression) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("property", "expr");
        out.put("requested", expression);
        try {
            model.result().numerical(tag).set("expr", new String[] {expression});
            out.put("status", "APPLIED");
            out.put("readback", readProperty(model, tag, "expr"));
        } catch (Exception exc) {
            out.put("status", "REFUSED");
            out.put("error", describe(exc));
        }
        return out;
    }

    private static String readProperty(Model model, String tag, String property) {
        try {
            return model.result().numerical(tag).getString(property);
        } catch (Exception ignored) {
            return null;
        }
    }

    private static Map<String, Object> associate(Model model, String numericalTag, String tableTag) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("numerical", numericalTag);
        out.put("table", tableTag);
        out.put("property", "table");
        try {
            model.result().numerical(numericalTag).set("table", tableTag);
            out.put("status", "APPLIED");
            out.put("readback", readProperty(model, numericalTag, "table"));
        } catch (Exception exc) {
            out.put("status", "REFUSED");
            out.put("error", describe(exc));
            out.put("readback", null);
        }
        out.put("evaluated", evaluate(model, numericalTag));
        return out;
    }

    /**
     * Fill the associated table.  A missing solution (the fixture study was not
     * solved) is the expected refusal here, and it is recorded as one.
     */
    private static Map<String, Object> evaluate(Model model, String numericalTag) {
        Map<String, Object> out = new LinkedHashMap<>();
        try {
            model.result().numerical(numericalTag).setResult();
            out.put("status", "APPLIED");
        } catch (Exception exc) {
            out.put("status", "REFUSED");
            out.put("error", describe(exc));
        }
        return out;
    }

    private static Map<String, Object> readback(Model model, String numericalTag, String tableTag) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (numericalTag != null) {
            NumericalFeature feature = model.result().numerical(numericalTag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", numericalTag);
            try {
                row.put("type", feature.getType());
            } catch (Exception exc) {
                row.put("type_error", describe(exc));
            }
            row.put("data", readProperty(model, numericalTag, "data"));
            row.put("expr", readProperty(model, numericalTag, "expr"));
            row.put("table", readProperty(model, numericalTag, "table"));
            out.put("numerical", row);
        }
        if (tableTag != null) {
            TableFeature table = model.result().table(tableTag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tableTag);
            try {
                double[][] real = table.getFilledReal();
                row.put("filled_rows", real == null ? null : real.length);
                row.put("filled_columns", real == null || real.length == 0 ? null : real[0].length);
            } catch (Exception exc) {
                row.put("filled_error", describe(exc));
            }
            out.put("table", row);
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

    private static String[] numericalTags(Model model) {
        try {
            return model.result().numerical().tags();
        } catch (Exception ignored) {
            return null;
        }
    }

    private static String[] tableTags(Model model) {
        try {
            return model.result().table().tags();
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

    private static String describe(Exception exc) {
        return exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage());
    }
}
