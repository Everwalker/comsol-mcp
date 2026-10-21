import com.comsol.model.Model;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Best-effort Derived Values node for the chain C user-style fixture.
 *
 * The published G3 operation surface has no derived-value operation, so a user
 * "Derived Values" node can only be produced through the trusted-code route
 * (``code.execute_java``).  This source never claims more than the engine
 * confirms: it tries a small, ordered list of documented numerical (Derived
 * Values) type tokens, creates at most one node, and returns a per-candidate
 * log.  When every candidate is refused the returned map says so and no node
 * was created -- the fixture receipt then records ``skipped``.
 *
 * The injected Model is the only model this source touches: it never creates,
 * loads or clears a model.
 */
public final class Phase4ChainCDerivedValue {
    // effect: WRITE
    private Phase4ChainCDerivedValue() {}

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null) {
            throw new IllegalArgumentException("injected Model is required");
        }
        String tag = "cder1";
        String expression = "T";
        if (arguments != null) {
            if (arguments.get("preferred_tag") instanceof String && !((String) arguments.get("preferred_tag")).isEmpty()) {
                tag = (String) arguments.get("preferred_tag");
            }
            if (arguments.get("expression") instanceof String && !((String) arguments.get("expression")).isEmpty()) {
                expression = (String) arguments.get("expression");
            }
        }

        Map<String, Object> result = new LinkedHashMap<>();
        List<Object> attempts = new ArrayList<>();
        List<String> candidates = Arrays.asList("IntVolume", "AvVolume", "MaxVolume", "IntSurface", "IntLine");
        List<String> existing = new ArrayList<>();
        try {
            String[] tags = model.result().numerical().tags();
            if (tags != null) {
                existing.addAll(Arrays.asList(tags));
            }
        } catch (Exception exc) {
            result.put("numerical_listing_error", exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage()));
        }
        result.put("numerical_tags_before", existing);

        String created = null;
        String created_type = null;
        for (String candidate : candidates) {
            Map<String, Object> attempt = new LinkedHashMap<>();
            attempt.put("type_id", candidate);
            if (existing.contains(tag)) {
                attempt.put("status", "TAG_CONFLICT");
                attempts.add(attempt);
                break;
            }
            try {
                model.result().numerical().create(tag, candidate);
                String readback = null;
                try {
                    readback = model.result().numerical(tag).getType();
                } catch (Exception ignored) {
                    readback = null;
                }
                attempt.put("status", "CREATED");
                attempt.put("type_readback", readback);
                created = tag;
                created_type = readback == null ? candidate : readback;
                attempts.add(attempt);
                break;
            } catch (Exception exc) {
                attempt.put("status", "REFUSED");
                attempt.put("error", exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage()));
                attempts.add(attempt);
            }
        }

        Map<String, Object> expression_write = new LinkedHashMap<>();
        if (created != null) {
            try {
                model.result().numerical(created).set("expr", expression);
                expression_write.put("property", "expr");
                expression_write.put("requested", expression);
                expression_write.put("status", "APPLIED");
                try {
                    expression_write.put("readback", model.result().numerical(created).getString("expr"));
                } catch (Exception ignored) {
                    expression_write.put("readback", null);
                }
            } catch (Exception exc) {
                expression_write.put("property", "expr");
                expression_write.put("requested", expression);
                expression_write.put("status", "REFUSED");
                expression_write.put("error", exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage()));
            }
        }

        List<String> after = new ArrayList<>();
        try {
            String[] tags = model.result().numerical().tags();
            if (tags != null) {
                after.addAll(Arrays.asList(tags));
            }
        } catch (Exception exc) {
            result.put("numerical_listing_error_after", exc.getClass().getSimpleName() + ": " + String.valueOf(exc.getMessage()));
        }

        result.put("attempts", attempts);
        result.put("created", created);
        result.put("created_type", created_type);
        result.put("expression_write", expression_write);
        result.put("numerical_tags_after", after);
        result.put("model_tag", model.tag());
        return result;
    }
}
