import com.comsol.model.Model;
import com.comsol.model.Unit;
import com.comsol.model.UnitList;
import com.comsol.model.UnitSystem;
import com.comsol.model.physics.FeatureInfo;
import com.comsol.model.physics.FeatureInfoList;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsField;
import com.comsol.model.physics.PhysicsFieldList;
import com.comsol.model.util.ModelUtil;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Read-only, bounded structure probe for a caller-supplied task-owned COMSOL model.
 * This class never creates, solves, saves, or launches a model. Its output is exploratory
 * metadata only and always leaves native admission UNVERIFIED.
 */
public final class W21FieldIdentityProbe {
    static final int MAX_FIELDS = 32;
    static final int MAX_FEATURE_INFO_TAGS = 64;
    static final int MAX_TABLE_ROWS = 128;
    static final int MAX_TOTAL_TABLE_ROWS = 512;
    static final int MAX_UNIT_ROWS = 256;
    static final int MAX_LOCK_IDENTIFIERS = 32;
    static final int MAX_CELL_CHARS = 512;
    static final int MAX_JSON_CHARS = 32768;
    static final int MAX_JSON_UTF8_BYTES = 65536;

    private W21FieldIdentityProbe() {}

    /**
     * Worker-callable entry point. Required arguments: expected_model_tag and physics_tag.
     * Optional requested_lock_identifiers is a String[] or List<String>. No files or
     * model state are changed. The returned value is a compact JSON string.
     */
    public static Object run(Model model, Map<String, Object> args) {
        try {
            if (model == null) return failure("INVALID_INPUT", "MODEL_HANDLE_MISSING");
            if (args == null) return failure("INVALID_INPUT", "ARGUMENTS_MISSING");
            String expectedModelTag = requiredText(args.get("expected_model_tag"), "EXPECTED_MODEL_TAG");
            String physicsTag = requiredText(args.get("physics_tag"), "PHYSICS_TAG");
            List<String> lockIds = parseLockIds(args.get("requested_lock_identifiers"));
            String actualModelTag = boundedText(model.tag(), "model_tag");
            if (!expectedModelTag.equals(actualModelTag)) {
                return failure("MODEL_TAG_MISMATCH", "EXPECTED_MODEL_TAG_MISMATCH");
            }
            String version = null;
            String versionError = null;
            try {
                version = boundedText(ModelUtil.getComsolVersion(), "comsol_version");
            } catch (LimitExceeded ex) {
                throw ex;
            } catch (Exception ex) {
                versionError = ex.getClass().getSimpleName();
            } catch (LinkageError ex) {
                versionError = ex.getClass().getSimpleName();
            }
            return collectBoundModel(model, expectedModelTag, physicsTag, lockIds, version, versionError);
        } catch (LimitExceeded ex) {
            return failure("OUTPUT_LIMIT_EXCEEDED", ex.code);
        } catch (InputException ex) {
            return failure("INVALID_INPUT", ex.code);
        } catch (Exception ex) {
            return failure("READBACK_UNAVAILABLE", ex.getClass().getSimpleName());
        } catch (LinkageError ex) {
            return failure("READBACK_UNAVAILABLE", ex.getClass().getSimpleName());
        }
    }

    /* Package-private seam for an offline API-interface harness; production calls run(). */
    static String collectBoundModel(Model model, String expectedModelTag, String physicsTag,
                                    List<String> lockIds, String comsolVersion,
                                    String versionError) {
        try {
            if (model == null || expectedModelTag == null || physicsTag == null || lockIds == null) {
                return failure("INVALID_INPUT", "REQUIRED_VALUE_MISSING");
            }
            String actualModelTag = boundedText(model.tag(), "model_tag");
            if (!expectedModelTag.equals(actualModelTag)) {
                return failure("MODEL_TAG_MISMATCH", "EXPECTED_MODEL_TAG_MISMATCH");
            }
            if (lockIds.size() > MAX_LOCK_IDENTIFIERS) {
                throw new LimitExceeded("LOCK_IDENTIFIER_LIMIT_EXCEEDED");
            }

            Budget budget = new Budget();
            Map<String, Object> root = map();
            root.put("probe", "W21FieldIdentityProbe");
            root.put("schema_version", Integer.valueOf(1));
            root.put("status", "STRUCTURE_CAPTURED_ONLY");
            root.put("native_admission", "UNVERIFIED");
            root.put("evidence_scope", "READ_ONLY_API_STRUCTURE; NO_SOLVE_OR_ADMISSION");
            root.put("limits", limits());

            Map<String, Object> version = map();
            if (comsolVersion == null) {
                version.put("status", "UNAVAILABLE");
                version.put("error_class", safeClassName(versionError));
                root.put("status", "PARTIAL_UNSUPPORTED");
            } else {
                version.put("status", "AVAILABLE");
                version.put("value", boundedText(comsolVersion, "comsol_version"));
            }
            root.put("comsol_version", version);

            Physics physics;
            try {
                physics = model.physics(physicsTag);
            } catch (Exception ex) {
                return failure("PHYSICS_LOOKUP_FAILED", ex.getClass().getSimpleName());
            } catch (LinkageError ex) {
                return failure("PHYSICS_LOOKUP_UNSUPPORTED", ex.getClass().getSimpleName());
            }
            if (physics == null) return failure("PHYSICS_NOT_FOUND", "PHYSICS_HANDLE_NULL");
            String actualPhysicsTag = boundedText(physics.tag(), "physics_tag");
            if (!physicsTag.equals(actualPhysicsTag)) {
                return failure("PHYSICS_TAG_MISMATCH", "EXPECTED_PHYSICS_TAG_MISMATCH");
            }

            Map<String, Object> identity = map();
            identity.put("expected_model_tag", expectedModelTag);
            identity.put("model_tag", actualModelTag);
            identity.put("physics_tag", actualPhysicsTag);
            identity.put("requested_physics_tag", physicsTag);
            try {
                identity.put("physics_type", boundedText(physics.getType(), "physics_type"));
            } catch (LimitExceeded ex) {
                throw ex;
            } catch (Exception ex) {
                identity.put("physics_type_status", "UNAVAILABLE");
                identity.put("physics_type_error_class", ex.getClass().getSimpleName());
                root.put("status", "PARTIAL_UNSUPPORTED");
            } catch (LinkageError ex) {
                identity.put("physics_type_status", "UNSUPPORTED");
                identity.put("physics_type_error_class", ex.getClass().getSimpleName());
                root.put("status", "PARTIAL_UNSUPPORTED");
            }
            root.put("identity", identity);

            root.put("physics_fields", readFields(physics, budget, root));
            root.put("feature_info", readFeatureInfo(physics, lockIds, budget, root));
            root.put("base_unit_system", readBaseUnits(model, budget, root));
            root.put("interpretation_limits", interpretationLimits());

            return encode(root);
        } catch (LimitExceeded ex) {
            return failure("OUTPUT_LIMIT_EXCEEDED", ex.code);
        } catch (Exception ex) {
            return failure("READBACK_UNAVAILABLE", ex.getClass().getSimpleName());
        } catch (LinkageError ex) {
            return failure("READBACK_UNAVAILABLE", ex.getClass().getSimpleName());
        }
    }

    private static Map<String, Object> readFields(Physics physics, Budget budget,
                                                   Map<String, Object> root) {
        Map<String, Object> section = map();
        try {
            PhysicsFieldList fields = physics.field();
            if (fields == null) {
                section.put("status", "UNAVAILABLE");
                section.put("error_class", "NULL_FIELD_LIST");
                root.put("status", "PARTIAL_UNSUPPORTED");
                return section;
            }
            String[] tags = fields.tags();
            if (tags == null) tags = new String[0];
            if (tags.length > MAX_FIELDS) throw new LimitExceeded("FIELD_COUNT_LIMIT_EXCEEDED");
            List<Object> items = new ArrayList<Object>();
            for (String rawTag : tags) {
                String tag = boundedText(rawTag, "physics_field_tag");
                PhysicsField field = fields.get(tag);
                Map<String, Object> item = map();
                item.put("tag", tag);
                if (field == null) {
                    item.put("status", "UNAVAILABLE");
                    item.put("error_class", "NULL_FIELD_HANDLE");
                    root.put("status", "PARTIAL_UNSUPPORTED");
                } else {
                    item.put("status", "AVAILABLE");
                    item.put("entity_tag", boundedText(field.tag(), "field_entity_tag"));
                    try {
                        item.put("field_name", boundedText(field.field(), "field_name"));
                    } catch (LimitExceeded ex) {
                        throw ex;
                    } catch (Exception ex) {
                        item.put("field_name_status", "UNAVAILABLE");
                        item.put("field_name_error_class", ex.getClass().getSimpleName());
                        root.put("status", "PARTIAL_UNSUPPORTED");
                    } catch (LinkageError ex) {
                        item.put("field_name_status", "UNSUPPORTED");
                        item.put("field_name_error_class", ex.getClass().getSimpleName());
                        root.put("status", "PARTIAL_UNSUPPORTED");
                    }
                    try {
                        item.put("components", stringArray(field.component(), "field_component"));
                    } catch (LimitExceeded ex) {
                        throw ex;
                    } catch (Exception ex) {
                        item.put("components_status", "UNAVAILABLE");
                        item.put("components_error_class", ex.getClass().getSimpleName());
                        root.put("status", "PARTIAL_UNSUPPORTED");
                    } catch (LinkageError ex) {
                        item.put("components_status", "UNSUPPORTED");
                        item.put("components_error_class", ex.getClass().getSimpleName());
                        root.put("status", "PARTIAL_UNSUPPORTED");
                    }
                }
                items.add(item);
            }
            section.put("status", tags.length == 0 ? "EMPTY" : "AVAILABLE");
            section.put("count", Integer.valueOf(tags.length));
            section.put("items", items);
            section.put("intrinsic_field_unit", "NOT_READ_BACK");
            section.put("unit_linkage", "NOT_ESTABLISHED_BY_UNIT_SYSTEM_METADATA");
        } catch (LimitExceeded ex) {
            throw ex;
        } catch (Exception ex) {
            section.put("status", "UNAVAILABLE");
            section.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        } catch (LinkageError ex) {
            section.put("status", "UNSUPPORTED");
            section.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        }
        return section;
    }

    private static Map<String, Object> readFeatureInfo(Physics physics, List<String> lockIds,
                                                        Budget budget, Map<String, Object> root) {
        Map<String, Object> section = map();
        try {
            FeatureInfoList infos = physics.featureInfo();
            if (infos == null) {
                section.put("status", "UNAVAILABLE");
                section.put("error_class", "NULL_FEATURE_INFO_LIST");
                root.put("status", "PARTIAL_UNSUPPORTED");
                return section;
            }
            String[] tags = infos.tags();
            if (tags == null) tags = new String[0];
            if (tags.length > MAX_FEATURE_INFO_TAGS) {
                throw new LimitExceeded("FEATURE_INFO_COUNT_LIMIT_EXCEEDED");
            }
            List<Object> items = new ArrayList<Object>();
            for (String rawTag : tags) {
                String tag = boundedText(rawTag, "feature_info_tag");
                FeatureInfo info = physics.featureInfo(tag);
                Map<String, Object> item = map();
                item.put("tag", tag);
                if (info == null) {
                    item.put("status", "UNAVAILABLE");
                    item.put("error_class", "NULL_FEATURE_INFO_HANDLE");
                    root.put("status", "PARTIAL_UNSUPPORTED");
                    items.add(item);
                    continue;
                }
                item.put("status", "AVAILABLE");
                item.put("shape_table", readRawTable(info, "Shape", budget, root));
                item.put("expression_table", readRawTable(info, "Expression", budget, root));
                List<Object> locks = new ArrayList<Object>();
                for (String identifier : lockIds) {
                    Map<String, Object> lock = map();
                    lock.put("requested_identifier", identifier);
                    try {
                        lock.put("status", "AVAILABLE");
                        lock.put("is_locked", Boolean.valueOf(info.isLocked(identifier)));
                    } catch (Exception ex) {
                        lock.put("status", "UNAVAILABLE");
                        lock.put("error_class", ex.getClass().getSimpleName());
                        root.put("status", "PARTIAL_UNSUPPORTED");
                    } catch (LinkageError ex) {
                        lock.put("status", "UNSUPPORTED");
                        lock.put("error_class", ex.getClass().getSimpleName());
                        root.put("status", "PARTIAL_UNSUPPORTED");
                    }
                    locks.add(lock);
                }
                item.put("explicit_identifier_lock_checks", locks);
                items.add(item);
            }
            section.put("status", tags.length == 0 ? "EMPTY" : "AVAILABLE");
            section.put("count", Integer.valueOf(tags.length));
            section.put("items", items);
            section.put("table_semantics", "RAW_ROWS; COLUMN_MEANINGS_NOT_INFERRED");
            section.put("lock_check_scope", "ONLY_CALLER_SUPPLIED_IDENTIFIERS");
        } catch (LimitExceeded ex) {
            throw ex;
        } catch (Exception ex) {
            section.put("status", "UNAVAILABLE");
            section.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        } catch (LinkageError ex) {
            section.put("status", "UNSUPPORTED");
            section.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        }
        return section;
    }

    private static Map<String, Object> readRawTable(FeatureInfo info, String tableId,
                                                     Budget budget, Map<String, Object> root) {
        Map<String, Object> table = map();
        table.put("requested_table_id", tableId);
        try {
            String[][] rows = info.getInfoTable(tableId, "all");
            if (rows == null) {
                table.put("status", "UNAVAILABLE");
                table.put("error_class", "NULL_TABLE");
                root.put("status", "PARTIAL_UNSUPPORTED");
                return table;
            }
            if (rows.length > MAX_TABLE_ROWS) throw new LimitExceeded("TABLE_ROW_LIMIT_EXCEEDED");
            budget.totalRows += rows.length;
            if (budget.totalRows > MAX_TOTAL_TABLE_ROWS) {
                throw new LimitExceeded("TOTAL_TABLE_ROW_LIMIT_EXCEEDED");
            }
            List<Object> rawRows = new ArrayList<Object>();
            for (String[] row : rows) {
                if (row == null) {
                    rawRows.add(null);
                    continue;
                }
                if (row.length > 64) throw new LimitExceeded("TABLE_COLUMN_LIMIT_EXCEEDED");
                List<Object> cells = new ArrayList<Object>();
                for (String cell : row) {
                    cells.add(cell == null ? null : boundedText(cell, "table_cell"));
                }
                rawRows.add(cells);
            }
            table.put("status", "AVAILABLE");
            table.put("row_count", Integer.valueOf(rows.length));
            table.put("rows", rawRows);
        } catch (LimitExceeded ex) {
            throw ex;
        } catch (Exception ex) {
            table.put("status", "UNAVAILABLE");
            table.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        } catch (LinkageError ex) {
            table.put("status", "UNSUPPORTED");
            table.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        }
        return table;
    }

    private static Map<String, Object> readBaseUnits(Model model, Budget budget,
                                                      Map<String, Object> root) {
        Map<String, Object> section = map();
        try {
            UnitSystem system = model.baseSystem();
            if (system == null) {
                section.put("status", "UNAVAILABLE");
                section.put("error_class", "NULL_BASE_UNIT_SYSTEM");
                root.put("status", "PARTIAL_UNSUPPORTED");
                return section;
            }
            section.put("status", "AVAILABLE");
            section.put("tag", boundedText(system.tag(), "unit_system_tag"));
            List<Object> kinds = new ArrayList<Object>();
            kinds.add(readUnitKind("base", system.baseUnit(), system, budget, root));
            kinds.add(readUnitKind("additional", system.additionalUnit(), system, budget, root));
            kinds.add(readUnitKind("derived", system.derivedUnit(), system, budget, root));
            section.put("unit_kinds", kinds);
            section.put("field_unit_linkage", "NOT_ESTABLISHED");
        } catch (LimitExceeded ex) {
            throw ex;
        } catch (Exception ex) {
            section.put("status", "UNAVAILABLE");
            section.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        } catch (LinkageError ex) {
            section.put("status", "UNSUPPORTED");
            section.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        }
        return section;
    }

    private static Map<String, Object> readUnitKind(String kind, UnitList units,
                                                     UnitSystem system, Budget budget,
                                                     Map<String, Object> root) {
        Map<String, Object> section = map();
        section.put("kind", kind);
        if (units == null) {
            section.put("status", "UNAVAILABLE");
            section.put("error_class", "NULL_UNIT_LIST");
            root.put("status", "PARTIAL_UNSUPPORTED");
            return section;
        }
        try {
            String[] tags = units.tags();
            if (tags == null) tags = new String[0];
            budget.unitRows += tags.length;
            if (budget.unitRows > MAX_UNIT_ROWS) throw new LimitExceeded("UNIT_ROW_LIMIT_EXCEEDED");
            List<Object> rows = new ArrayList<Object>();
            for (String rawTag : tags) {
                String tag = boundedText(rawTag, "unit_tag");
                Unit unit;
                if ("base".equals(kind)) unit = system.baseUnit(tag);
                else if ("additional".equals(kind)) unit = system.additionalUnit(tag);
                else unit = system.derivedUnit(tag);
                Map<String, Object> row = map();
                row.put("tag", tag);
                if (unit == null) {
                    row.put("status", "UNAVAILABLE");
                    row.put("error_class", "NULL_UNIT_HANDLE");
                    root.put("status", "PARTIAL_UNSUPPORTED");
                } else {
                    row.put("status", "AVAILABLE");
                    row.put("entity_tag", boundedText(unit.tag(), "unit_entity_tag"));
                    row.put("quantity", boundedText(unit.quantity(), "unit_quantity"));
                    row.put("dimension", intArray(unit.dimension()));
                    row.put("symbol", boundedText(unit.symbol(), "unit_symbol"));
                    row.put("scale", finiteOrText(unit.scale()));
                    row.put("offset", finiteOrText(unit.offset()));
                }
                rows.add(row);
            }
            section.put("status", tags.length == 0 ? "EMPTY" : "AVAILABLE");
            section.put("count", Integer.valueOf(tags.length));
            section.put("items", rows);
        } catch (LimitExceeded ex) {
            throw ex;
        } catch (Exception ex) {
            section.put("status", "UNAVAILABLE");
            section.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        } catch (LinkageError ex) {
            section.put("status", "UNSUPPORTED");
            section.put("error_class", ex.getClass().getSimpleName());
            root.put("status", "PARTIAL_UNSUPPORTED");
        }
        return section;
    }

    private static Map<String, Object> limits() {
        Map<String, Object> value = map();
        value.put("fields", Integer.valueOf(MAX_FIELDS));
        value.put("feature_info_tags", Integer.valueOf(MAX_FEATURE_INFO_TAGS));
        value.put("rows_per_info_table", Integer.valueOf(MAX_TABLE_ROWS));
        value.put("total_info_table_rows", Integer.valueOf(MAX_TOTAL_TABLE_ROWS));
        value.put("units_total", Integer.valueOf(MAX_UNIT_ROWS));
        value.put("lock_identifiers", Integer.valueOf(MAX_LOCK_IDENTIFIERS));
        value.put("characters_per_cell", Integer.valueOf(MAX_CELL_CHARS));
        value.put("serialized_json_characters", Integer.valueOf(MAX_JSON_CHARS));
        value.put("serialized_json_utf8_bytes", Integer.valueOf(MAX_JSON_UTF8_BYTES));
        value.put("overflow_behavior", "FAIL_WITH_SMALL_ERROR_ENVELOPE; NEVER_TRUNCATE");
        return value;
    }

    private static List<Object> interpretationLimits() {
        List<Object> limits = new ArrayList<Object>();
        limits.add("FeatureInfo rows are preserved raw; no column meanings are inferred.");
        limits.add("UnitSystem entries are environment definitions and are not linked to dependent fields.");
        limits.add("This probe does not establish dependent-field intrinsic units or native admission.");
        return limits;
    }

    private static List<Object> stringArray(String[] values, String label) {
        List<Object> result = new ArrayList<Object>();
        if (values == null) return result;
        if (values.length > MAX_FIELDS) throw new LimitExceeded("STRING_ARRAY_LIMIT_EXCEEDED");
        for (String value : values) result.add(value == null ? null : boundedText(value, label));
        return result;
    }

    private static List<Object> intArray(int[] values) {
        List<Object> result = new ArrayList<Object>();
        if (values == null) return result;
        if (values.length > 64) throw new LimitExceeded("UNIT_DIMENSION_LIMIT_EXCEEDED");
        for (int value : values) result.add(Integer.valueOf(value));
        return result;
    }

    private static Object finiteOrText(double value) {
        if (Double.isNaN(value)) return "NaN";
        if (Double.isInfinite(value)) return value < 0 ? "-Infinity" : "Infinity";
        return Double.valueOf(value);
    }

    private static List<String> parseLockIds(Object raw) {
        List<String> values = new ArrayList<String>();
        if (raw == null) return values;
        if (raw instanceof String[]) {
            String[] array = (String[]) raw;
            if (array.length > MAX_LOCK_IDENTIFIERS) throw new LimitExceeded("LOCK_IDENTIFIER_LIMIT_EXCEEDED");
            for (String item : array) values.add(requiredText(item, "LOCK_IDENTIFIER"));
            return values;
        }
        if (raw instanceof List<?>) {
            List<?> list = (List<?>) raw;
            if (list.size() > MAX_LOCK_IDENTIFIERS) throw new LimitExceeded("LOCK_IDENTIFIER_LIMIT_EXCEEDED");
            for (Object item : list) values.add(requiredText(item, "LOCK_IDENTIFIER"));
            return values;
        }
        throw new InputException("LOCK_IDENTIFIERS_MUST_BE_STRING_LIST");
    }

    private static String requiredText(Object raw, String code) {
        if (!(raw instanceof String)) throw new InputException(code + "_MISSING_OR_NOT_STRING");
        String value = (String) raw;
        if (value.length() == 0 || value.length() > MAX_CELL_CHARS) {
            throw new InputException(code + "_EMPTY_OR_TOO_LONG");
        }
        return value;
    }

    private static String boundedText(String value, String code) {
        if (value == null) return null;
        if (value.length() > MAX_CELL_CHARS) throw new LimitExceeded("TEXT_LIMIT_EXCEEDED_" + code);
        return value;
    }

    private static String safeClassName(String value) {
        if (value == null || value.length() == 0) return "UNKNOWN";
        StringBuilder result = new StringBuilder();
        for (int i = 0; i < value.length() && i < 80; i++) {
            char ch = value.charAt(i);
            if (Character.isLetterOrDigit(ch) || ch == '_' || ch == '$') result.append(ch);
        }
        return result.length() == 0 ? "UNKNOWN" : result.toString();
    }

    private static Map<String, Object> map() {
        return new LinkedHashMap<String, Object>();
    }

    private static String failure(String status, String code) {
        Map<String, Object> result = map();
        result.put("probe", "W21FieldIdentityProbe");
        result.put("status", safeClassName(status));
        result.put("code", safeClassName(code));
        result.put("native_admission", "UNVERIFIED");
        result.put("payload_complete", Boolean.FALSE);
        try {
            return encode(result);
        } catch (Exception ignored) {
            return "{\"probe\":\"W21FieldIdentityProbe\",\"status\":\"OUTPUT_LIMIT_EXCEEDED\",\"native_admission\":\"UNVERIFIED\",\"payload_complete\":false}";
        }
    }

    private static String encode(Object value) {
        StringBuilder json = new StringBuilder();
        appendJson(json, value);
        if (json.length() > MAX_JSON_CHARS) throw new LimitExceeded("JSON_CHARACTER_LIMIT_EXCEEDED");
        byte[] encoded = json.toString().getBytes(StandardCharsets.UTF_8);
        if (encoded.length > MAX_JSON_UTF8_BYTES) throw new LimitExceeded("JSON_UTF8_BYTE_LIMIT_EXCEEDED");
        return json.toString();
    }

    private static void appendJson(StringBuilder json, Object value) {
        if (value == null) {
            appendRaw(json, "null");
        } else if (value instanceof String) {
            appendQuoted(json, (String) value);
        } else if (value instanceof Boolean || value instanceof Integer || value instanceof Long) {
            appendRaw(json, String.valueOf(value));
        } else if (value instanceof Number) {
            double number = ((Number) value).doubleValue();
            if (Double.isNaN(number) || Double.isInfinite(number)) {
                throw new LimitExceeded("NON_JSON_NUMBER");
            }
            appendRaw(json, String.valueOf(value));
        } else if (value instanceof List<?>) {
            appendRaw(json, "[");
            List<?> list = (List<?>) value;
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) appendRaw(json, ",");
                appendJson(json, list.get(i));
            }
            appendRaw(json, "]");
        } else if (value instanceof Map<?, ?>) {
            appendRaw(json, "{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!(entry.getKey() instanceof String)) throw new LimitExceeded("NON_STRING_JSON_KEY");
                if (!first) appendRaw(json, ",");
                first = false;
                appendQuoted(json, (String) entry.getKey());
                appendRaw(json, ":");
                appendJson(json, entry.getValue());
            }
            appendRaw(json, "}");
        } else {
            throw new LimitExceeded("UNSUPPORTED_JSON_VALUE");
        }
    }

    private static void appendQuoted(StringBuilder json, String value) {
        if (value.length() > MAX_CELL_CHARS) throw new LimitExceeded("TEXT_LIMIT_EXCEEDED");
        appendRaw(json, "\"");
        final char[] hex = "0123456789abcdef".toCharArray();
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if (ch == '"' || ch == '\\') {
                appendRaw(json, "\\");
                appendChar(json, ch);
            } else if (ch == '\b') appendRaw(json, "\\b");
            else if (ch == '\f') appendRaw(json, "\\f");
            else if (ch == '\n') appendRaw(json, "\\n");
            else if (ch == '\r') appendRaw(json, "\\r");
            else if (ch == '\t') appendRaw(json, "\\t");
            else if (ch < 0x20) {
                appendRaw(json, "\\u00");
                appendChar(json, hex[(ch >> 4) & 0xf]);
                appendChar(json, hex[ch & 0xf]);
            } else {
                appendChar(json, ch);
            }
        }
        appendRaw(json, "\"");
    }

    private static void appendRaw(StringBuilder json, String value) {
        if (json.length() + value.length() > MAX_JSON_CHARS) {
            throw new LimitExceeded("JSON_CHARACTER_LIMIT_EXCEEDED");
        }
        json.append(value);
    }

    private static void appendChar(StringBuilder json, char value) {
        if (json.length() + 1 > MAX_JSON_CHARS) throw new LimitExceeded("JSON_CHARACTER_LIMIT_EXCEEDED");
        json.append(value);
    }

    private static final class Budget {
        int totalRows;
        int unitRows;
    }

    private static final class LimitExceeded extends RuntimeException {
        final String code;
        LimitExceeded(String code) { this.code = safeClassName(code); }
    }

    private static final class InputException extends RuntimeException {
        final String code;
        InputException(String code) { this.code = safeClassName(code); }
    }
}
