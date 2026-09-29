import com.comsol.model.Model;
import com.comsol.model.Unit;
import com.comsol.model.UnitList;
import com.comsol.model.UnitSystem;
import com.comsol.model.physics.FeatureInfo;
import com.comsol.model.physics.FeatureInfoList;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsField;
import com.comsol.model.physics.PhysicsFieldList;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Offline interface-stub test for the probe collector; no COMSOL runtime is started. */
public final class W21FieldIdentityProbeOfflineTest {
    private static int assertions;

    private W21FieldIdentityProbeOfflineTest() {}

    public static void main(String[] args) {
        Fixture good = new Fixture("task-owned-model", new String[][]{
                {"raw|shape", "line\ncontinuation"}
        });
        String json = good.collect("task-owned-model");
        require(json.contains("\"status\":\"STRUCTURE_CAPTURED_ONLY\""), "complete status");
        require(json.contains("\"native_admission\":\"UNVERIFIED\""), "admission stays closed");
        require(json.contains("\"physics_type\":\"HeatTransfer\""), "physics type present");
        require(json.contains("\"field_name\":\"T\""), "field name present");
        require(json.contains("\"components\":[\"T\"]"), "components present");
        require(json.contains("raw|shape") && json.contains("line\\ncontinuation"), "raw table cells escaped and preserved");
        require(json.contains("\"is_locked\":true"), "explicit lock check present");
        require(json.contains("\"quantity\":\"Temperature\""), "unit quantity present");
        require(json.contains("\"dimension\":[0,0,0,0,1]"), "unit dimension present");
        require(json.contains("\"field_unit_linkage\":\"NOT_ESTABLISHED\""), "unit metadata is not linked to field");
        require(utf8(json).length <= W21FieldIdentityProbe.MAX_JSON_UTF8_BYTES, "successful payload byte bound");
        require(good.tableReads.equals(Arrays.asList("Shape", "Expression")),
                "default request preserves the existing full table selection");

        Fixture discovery = new Fixture("task-owned-model", new String[][]{{"shape"}});
        String discovered = discovery.collectWithRequest(request("discovery_only", Boolean.TRUE),
                new ArrayList<String>());
        require(discovered.contains("\"status\":\"DISCOVERY_ONLY_CAPTURED\""), "discovery status");
        require(discovered.contains("\"capture_scope\":\"PHYSICS_FIELDS_AND_FEATURE_INFO_TAGS_ONLY\""),
                "discovery scope is explicit");
        require(discovered.contains("\"feature_info_tables\":\"NOT_READ\""),
                "discovery states tables were not read");
        require(discovered.contains("\"metadata_complete\":false"),
                "discovery cannot claim complete metadata");
        require(discovery.tableReads.isEmpty(), "discovery does not touch Shape or Expression tables");

        Fixture targeted = new Fixture("task-owned-model", new String[][]{{"T", "shape row"}});
        String targetedJson = targeted.collectWithRequest(request(
                "feature_info_tag", "fi1", "table_id", "Shape"), Arrays.asList("T"));
        require(targetedJson.contains("\"status\":\"TARGETED_TABLE_CAPTURED_ONLY\""),
                "targeted table status");
        require(targetedJson.contains("\"unselected_feature_info_tables\":\"NOT_READ\""),
                "targeted response explicitly excludes other tables");
        require(targetedJson.contains("\"metadata_complete\":false"),
                "one table cannot claim complete metadata");
        require(targeted.tableReads.equals(Arrays.asList("Shape")),
                "targeted request reads only the selected table");

        Fixture targetedOverflow = new Fixture("task-owned-model",
                rows(W21FieldIdentityProbe.MAX_TABLE_ROWS + 1));
        String targetedLimit = targetedOverflow.collectWithRequest(request(
                "feature_info_tag", "fi1", "table_id", "Shape"), new ArrayList<String>());
        require(targetedLimit.contains("\"status\":\"OUTPUT_LIMIT_EXCEEDED\""),
                "targeted row overflow is rejected without truncation");
        require(targetedLimit.contains("\"limit_location\":{"), "overflow has a location receipt");
        require(targetedLimit.contains("\"physics_tag\":\"ht\""), "overflow names physics tag");
        require(targetedLimit.contains("\"feature_info_tag\":\"fi1\""), "overflow names FeatureInfo tag");
        require(targetedLimit.contains("\"table_id\":\"Shape\""), "overflow names selected table");
        require(targetedLimit.contains("\"actual_rows\":129")
                && targetedLimit.contains("\"hard_limit\":128"), "overflow reports actual and hard row counts");
        require(!targetedLimit.contains("raw|shape") && !targetedLimit.contains("\"rows\":"),
                "overflow contains no partial raw rows");
        require(targetedOverflow.tableReads.equals(Arrays.asList("Shape")),
                "overflow never reads the unselected Expression table");

        Fixture absentTag = new Fixture("task-owned-model", new String[][]{{"unused"}});
        String absent = absentTag.collectWithRequest(request(
                "feature_info_tag", "missing", "table_id", "Shape"), new ArrayList<String>());
        require(absent.contains("\"status\":\"FEATURE_INFO_TAG_NOT_FOUND\""),
                "missing selected FeatureInfo tag is explicit");
        require(absentTag.tableReads.isEmpty(), "missing tag fails before any table read");

        requireInvalidProbeRequest(request("feature_info_tag", "fi1"), "table/tag pair");
        requireInvalidProbeRequest(request("table_id", "Shape"), "table/tag pair");
        requireInvalidProbeRequest(request("feature_info_tag", null, "table_id", null),
                "explicit null targeted pair");
        requireInvalidProbeRequest(request("feature_info_tag", "fi1", "table_id", "Units"),
                "table allowlist");
        requireInvalidProbeRequest(request("discovery_only", Boolean.TRUE,
                "feature_info_tag", "fi1", "table_id", "Shape"), "discovery conflict");
        requireInvalidProbeRequest(request("discovery_only", "true"), "discovery type");

        Fixture atRowLimit = new Fixture("task-owned-model", rows(W21FieldIdentityProbe.MAX_TABLE_ROWS));
        String boundary = atRowLimit.collect("task-owned-model");
        require(boundary.contains("\"status\":\"STRUCTURE_CAPTURED_ONLY\""), "exact row limit accepted");
        require(boundary.contains("\"row_count\":128"), "exact row count preserved");

        Fixture tooManyRows = new Fixture("task-owned-model", rows(W21FieldIdentityProbe.MAX_TABLE_ROWS + 1));
        String overflow = tooManyRows.collect("task-owned-model");
        require(overflow.contains("\"status\":\"OUTPUT_LIMIT_EXCEEDED\""), "row overflow rejected");
        require(overflow.contains("\"payload_complete\":false"), "overflow is marked incomplete");
        require(!overflow.contains("requested_physics_tag") && !overflow.contains("raw|shape"), "overflow carries no partial readback");
        require(utf8(overflow).length <= W21FieldIdentityProbe.MAX_JSON_UTF8_BYTES, "failure envelope byte bound");

        Fixture tooLongCell = new Fixture("task-owned-model", new String[][]{
                {repeat('x', W21FieldIdentityProbe.MAX_CELL_CHARS + 1)}
        });
        String textOverflow = tooLongCell.collect("task-owned-model");
        require(textOverflow.contains("\"status\":\"OUTPUT_LIMIT_EXCEEDED\""), "cell overflow rejected");
        require(!textOverflow.contains("\"shape_table\""), "cell overflow carries no partial tables");

        Fixture tooManyBytes = new Fixture("task-owned-model", multibyteRows(60, 480));
        String byteOverflow = tooManyBytes.collect("task-owned-model");
        require(byteOverflow.contains("\"status\":\"OUTPUT_LIMIT_EXCEEDED\""), "UTF-8 byte overflow rejected");
        require(byteOverflow.contains("JSON_UTF8_BYTE_LIMIT_EXCEEDED"), "UTF-8 byte limit identified");
        require(!byteOverflow.contains("\"shape_table\""), "byte overflow carries no partial tables");

        Fixture longType = new Fixture("task-owned-model", new String[0][],
                repeat('p', W21FieldIdentityProbe.MAX_CELL_CHARS + 1), "T", new String[]{"T"});
        String typeOverflow = longType.collect("task-owned-model");
        require(typeOverflow.contains("\"status\":\"OUTPUT_LIMIT_EXCEEDED\""), "physics type overflow rejected");
        require(typeOverflow.contains("TEXT_LIMIT_EXCEEDED_physics_type"), "physics type limit identified");
        require(!typeOverflow.contains("\"identity\""), "physics type overflow carries no partial identity");

        Fixture longFieldName = new Fixture("task-owned-model", new String[0][], "HeatTransfer",
                repeat('f', W21FieldIdentityProbe.MAX_CELL_CHARS + 1), new String[]{"T"});
        String fieldNameOverflow = longFieldName.collect("task-owned-model");
        require(fieldNameOverflow.contains("\"status\":\"OUTPUT_LIMIT_EXCEEDED\""), "field name overflow rejected");
        require(fieldNameOverflow.contains("TEXT_LIMIT_EXCEEDED_field_name"), "field name limit identified");
        require(!fieldNameOverflow.contains("physics_fields"), "field name overflow carries no partial fields");

        Fixture longComponent = new Fixture("task-owned-model", new String[0][], "HeatTransfer", "T",
                new String[]{repeat('c', W21FieldIdentityProbe.MAX_CELL_CHARS + 1)});
        String componentOverflow = longComponent.collect("task-owned-model");
        require(componentOverflow.contains("\"status\":\"OUTPUT_LIMIT_EXCEEDED\""), "component overflow rejected");
        require(componentOverflow.contains("TEXT_LIMIT_EXCEEDED_field_component"), "component limit identified");
        require(!componentOverflow.contains("physics_fields"), "component overflow carries no partial fields");

        String versionOverflow = good.collectWithVersion(repeat('v', W21FieldIdentityProbe.MAX_CELL_CHARS + 1));
        require(versionOverflow.contains("\"status\":\"OUTPUT_LIMIT_EXCEEDED\""), "version overflow rejected");
        require(versionOverflow.contains("TEXT_LIMIT_EXCEEDED_comsol_version"), "version limit identified");
        require(!versionOverflow.contains("\"identity\""), "version overflow carries no partial identity");

        Fixture wrongModel = new Fixture("actual-model", new String[0][]);
        String mismatch = wrongModel.collect("other-model");
        require(mismatch.contains("\"status\":\"MODEL_TAG_MISMATCH\""), "model tag mismatch rejected");
        require(wrongModel.physicsLookups == 0, "mismatch stops before physics lookup");

        String physicsMismatch = good.collect("task-owned-model", "other-physics");
        require(physicsMismatch.contains("\"status\":\"PHYSICS_TAG_MISMATCH\""), "physics tag mismatch rejected");
        require(!physicsMismatch.contains("physics_fields"), "physics mismatch stops before field enumeration");

        System.out.println("W21FieldIdentityProbeOfflineTest PASS assertions=" + assertions);
        System.out.println("HEALTHY_JSON\t" + json);
        System.out.println("ROW_OVERFLOW_JSON\t" + overflow);
        System.out.println("CELL_OVERFLOW_JSON\t" + textOverflow);
        System.out.println("BYTE_OVERFLOW_JSON\t" + byteOverflow);
        System.out.println("TYPE_OVERFLOW_JSON\t" + typeOverflow);
        System.out.println("FIELD_NAME_OVERFLOW_JSON\t" + fieldNameOverflow);
        System.out.println("COMPONENT_OVERFLOW_JSON\t" + componentOverflow);
        System.out.println("VERSION_OVERFLOW_JSON\t" + versionOverflow);
        System.out.println("MODEL_MISMATCH_JSON\t" + mismatch);
        System.out.println("PHYSICS_MISMATCH_JSON\t" + physicsMismatch);
        System.out.println("DISCOVERY_JSON\t" + discovered);
        System.out.println("TARGETED_JSON\t" + targetedJson);
        System.out.println("TARGETED_ROW_OVERFLOW_JSON\t" + targetedLimit);
        System.out.println("MISSING_FEATURE_INFO_TAG_JSON\t" + absent);
    }

    private static final class Fixture {
        final String modelTag;
        final String[][] shapeRows;
        final String physicsType;
        final String fieldName;
        final String[] componentNames;
        final List<String> tableReads = new ArrayList<String>();
        int physicsLookups;
        final FeatureInfo info;
        final PhysicsField field;
        final Physics physics;
        final Unit unit;
        final UnitSystem unitSystem;
        final Model model;

        Fixture(String modelTag, String[][] shapeRows) {
            this(modelTag, shapeRows, "HeatTransfer", "T", new String[]{"T"});
        }

        Fixture(String modelTag, String[][] shapeRows, String physicsType, String fieldName,
                String[] componentNames) {
            this.modelTag = modelTag;
            this.shapeRows = shapeRows;
            this.physicsType = physicsType;
            this.fieldName = fieldName;
            this.componentNames = componentNames;
            info = proxy(FeatureInfo.class, new InvocationHandler() {
                public Object invoke(Object proxy, Method method, Object[] args) {
                    String name = method.getName();
                    if ("tag".equals(name)) return "fi1";
                    if ("getInfoTable".equals(name)) {
                        Fixture.this.tableReads.add(String.valueOf(args[0]));
                        return "Shape".equals(args[0]) ? Fixture.this.shapeRows
                                : new String[][]{{"T", "raw expression row"}};
                    }
                    if ("isLocked".equals(name)) return Boolean.valueOf("T".equals(args[0]));
                    return unexpected(method);
                }
            });
            field = proxy(PhysicsField.class, new InvocationHandler() {
                public Object invoke(Object proxy, Method method, Object[] args) {
                    String name = method.getName();
                    if ("tag".equals(name)) return "temperature";
                    if ("field".equals(name)) return Fixture.this.fieldName;
                    if ("component".equals(name)) return Fixture.this.componentNames;
                    return unexpected(method);
                }
            });
            physics = proxy(Physics.class, new InvocationHandler() {
                public Object invoke(Object proxy, Method method, Object[] args) {
                    String name = method.getName();
                    if ("tag".equals(name)) return "ht";
                    if ("getType".equals(name)) return Fixture.this.physicsType;
                    if ("field".equals(name)) {
                        if (args == null || args.length == 0) return fieldList(field);
                        return field;
                    }
                    if ("featureInfo".equals(name)) {
                        if (args == null || args.length == 0) return featureInfoList(info);
                        return info;
                    }
                    return unexpected(method);
                }
            });
            unit = proxy(Unit.class, new InvocationHandler() {
                public Object invoke(Object proxy, Method method, Object[] args) {
                    String name = method.getName();
                    if ("tag".equals(name)) return "temperature";
                    if ("quantity".equals(name)) return "Temperature";
                    if ("dimension".equals(name)) return new int[]{0, 0, 0, 0, 1};
                    if ("symbol".equals(name)) return "K";
                    if ("scale".equals(name)) return Double.valueOf(1.0);
                    if ("offset".equals(name)) return Double.valueOf(0.0);
                    return unexpected(method);
                }
            });
            unitSystem = proxy(UnitSystem.class, new InvocationHandler() {
                public Object invoke(Object proxy, Method method, Object[] args) {
                    String name = method.getName();
                    if ("tag".equals(name)) return "si";
                    if ("baseUnit".equals(name)) {
                        if (args == null || args.length == 0) return unitList("temperature");
                        return unit;
                    }
                    if ("additionalUnit".equals(name) || "derivedUnit".equals(name)) {
                        if (args == null || args.length == 0) return unitList();
                        return unit;
                    }
                    return unexpected(method);
                }
            });
            model = proxy(Model.class, new InvocationHandler() {
                public Object invoke(Object proxy, Method method, Object[] args) {
                    String name = method.getName();
                    if ("tag".equals(name)) return Fixture.this.modelTag;
                    if ("physics".equals(name)) {
                        Fixture.this.physicsLookups++;
                        return physics;
                    }
                    if ("baseSystem".equals(name)) return unitSystem;
                    return unexpected(method);
                }
            });
        }

        String collect(String expectedModelTag) {
            return collect(expectedModelTag, "ht");
        }

        String collect(String expectedModelTag, String expectedPhysicsTag) {
            return W21FieldIdentityProbe.collectBoundModel(model, expectedModelTag, expectedPhysicsTag,
                    Arrays.asList("T"), "6.4.0.293", null);
        }

        String collectWithVersion(String version) {
            return W21FieldIdentityProbe.collectBoundModel(model, "task-owned-model", "ht",
                    Arrays.asList("T"), version, null);
        }

        String collectWithRequest(Map<String, Object> request, List<String> lockIds) {
            return W21FieldIdentityProbe.collectBoundModel(model, "task-owned-model", "ht",
                    lockIds, "6.4.0.293", null, W21FieldIdentityProbe.parseProbeRequest(request));
        }
    }

    private static Map<String, Object> request(Object... entries) {
        Map<String, Object> value = new LinkedHashMap<String, Object>();
        for (int i = 0; i < entries.length; i += 2) {
            value.put((String) entries[i], entries[i + 1]);
        }
        return value;
    }

    private static void requireInvalidProbeRequest(Map<String, Object> request, String label) {
        try {
            W21FieldIdentityProbe.parseProbeRequest(request);
            throw new AssertionError("invalid probe request accepted: " + label);
        } catch (IllegalArgumentException expected) {
            // Expected: bad argument combinations fail before a model API call.
        } catch (RuntimeException expected) {
            // InputException is private; its fail-closed type/message is tested by rejection.
        }
    }

    private static PhysicsFieldList fieldList(final PhysicsField field) {
        return proxy(PhysicsFieldList.class, new InvocationHandler() {
            public Object invoke(Object proxy, Method method, Object[] args) {
                if ("tags".equals(method.getName())) return new String[]{"temperature"};
                if ("get".equals(method.getName())) return field;
                return unexpected(method);
            }
        });
    }

    private static FeatureInfoList featureInfoList(final FeatureInfo info) {
        return proxy(FeatureInfoList.class, new InvocationHandler() {
            public Object invoke(Object proxy, Method method, Object[] args) {
                if ("tags".equals(method.getName())) return new String[]{"fi1"};
                if ("get".equals(method.getName())) return info;
                return unexpected(method);
            }
        });
    }

    private static UnitList unitList(final String... tags) {
        return proxy(UnitList.class, new InvocationHandler() {
            public Object invoke(Object proxy, Method method, Object[] args) {
                if ("tags".equals(method.getName())) return tags;
                return unexpected(method);
            }
        });
    }

    @SuppressWarnings("unchecked")
    private static <T> T proxy(Class<T> type, InvocationHandler handler) {
        return (T) Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type}, handler);
    }

    private static Object unexpected(Method method) {
        if ("toString".equals(method.getName())) return "offline-api-stub";
        if ("hashCode".equals(method.getName())) return Integer.valueOf(1);
        if ("equals".equals(method.getName())) return Boolean.FALSE;
        throw new AssertionError("unexpected API call in probe harness: " + method.getName());
    }

    private static String[][] rows(int count) {
        String[][] values = new String[count][1];
        for (int i = 0; i < count; i++) values[i][0] = "row-" + i;
        return values;
    }

    private static String[][] multibyteRows(int count, int cellLength) {
        String[][] values = new String[count][1];
        String cell = repeat('汉', cellLength);
        for (int i = 0; i < count; i++) values[i][0] = cell;
        return values;
    }

    private static String repeat(char ch, int count) {
        StringBuilder value = new StringBuilder(count);
        for (int i = 0; i < count; i++) value.append(ch);
        return value.toString();
    }

    private static byte[] utf8(String value) {
        return value.getBytes(StandardCharsets.UTF_8);
    }

    private static void require(boolean condition, String label) {
        assertions++;
        if (!condition) throw new AssertionError(label);
    }
}
