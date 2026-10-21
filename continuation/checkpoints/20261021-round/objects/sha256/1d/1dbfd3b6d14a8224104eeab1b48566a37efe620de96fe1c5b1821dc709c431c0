import com.comsol.model.GeomSequence;
import com.comsol.model.Material;
import com.comsol.model.Model;
import com.comsol.model.ParameterEntity;
import com.comsol.model.PropFeature;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverFeatureList;
import com.comsol.model.SolverSequence;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.MultiphysicsCoupling;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Supplier;

/** Read-only native checks and immutable save for an unsolved W24 shape model. */
public final class W24StaticShapeReadback {
    private static final String COMPONENT = "comp1";
    private static final String GEOMETRY = "geom1";
    private static final double CAPILLARY_TIME_S = 1.0 / 60.0;

    private W24StaticShapeReadback() { }

    public static Object run(Model model, Map<String, Object> args) throws IOException {
        String action = String.valueOf(args.getOrDefault("action", "readback"));
        if ("readback".equals(action)) return readback(model, args);
        if ("save".equals(action)) return saveUnsolved(model, args);
        throw new IllegalArgumentException("action must be exactly readback or save");
    }

    // Each trusted Java artifact is compiled and loaded independently.
    // Keep this readback-only resolver and metadata set local to this source.
    private static PhysicsFeature resolvePhaseFieldModel(Physics phaseField) {
        if (phaseField == null || !"PhaseField".equals(phaseField.getType())) {
            throw new IllegalStateException("Phase Field model resolution requires exact PhaseField physics");
        }
        String[] featureTags = phaseField.feature().tags();
        if (featureTags == null || featureTags.length == 0 || featureTags.length > 64) {
            throw new IllegalStateException("Phase Field feature tag inventory is unavailable or exceeds its bound");
        }
        Set<String> seenTags = new HashSet<>();
        PhysicsFeature resolved = null;
        for (String tag : featureTags) {
            if (tag == null || tag.trim().isEmpty() || tag.length() > 128 || !seenTags.add(tag)) {
                throw new IllegalStateException("Phase Field feature tag inventory is malformed or ambiguous");
            }
            PhysicsFeature feature = phaseField.feature(tag);
            if (feature == null || !tag.equals(feature.tag())) {
                throw new IllegalStateException("Phase Field feature tag did not resolve to its exact feature object");
            }
            if ("PhaseFieldModel".equals(feature.getType())) {
                if (resolved != null) {
                    throw new IllegalStateException("Phase Field exposes multiple PhaseFieldModel features");
                }
                resolved = feature;
            }
        }
        if (resolved == null) {
            throw new IllegalStateException("Phase Field exposes no semantic PhaseFieldModel feature");
        }
        return resolved;
    }

    private static Map<String, PhysicsFeature> resolvePhaseInitialValueFeatures(Physics phaseField) {
        String[] featureTags = phaseField.feature().tags();
        if (featureTags == null || featureTags.length > 64) {
            throw new IllegalStateException("Phase Field feature tag inventory is unavailable or exceeds its bound");
        }
        Set<String> seenTags = new HashSet<>();
        Map<String, PhysicsFeature> byFluid = new LinkedHashMap<>();
        for (String tag : featureTags) {
            if (tag == null || tag.trim().isEmpty() || tag.length() > 128 || !seenTags.add(tag)) {
                throw new IllegalStateException("Phase Field feature tag inventory is malformed or ambiguous");
            }
            PhysicsFeature feature = phaseField.feature(tag);
            if (feature == null || !tag.equals(feature.tag())) {
                throw new IllegalStateException("Phase Field feature tag did not resolve to its exact feature object");
            }
            boolean hasFluid = feature.hasProperty("FluidInDomain");
            boolean hasInitialValueOption = feature.hasProperty("InitialValuesOption");
            String featureType = feature.getType();
            boolean initialFeatureType = "init".equals(featureType) || "initFluid2".equals(featureType);
            if (hasFluid != hasInitialValueOption || initialFeatureType != hasFluid) {
                throw new IllegalStateException("Phase Field initial-value feature properties are incomplete");
            }
            if (!hasFluid) continue;
            String fluid = feature.getString("FluidInDomain");
            String expectedType = "Fluid1phipf".equals(fluid) ? "init" :
                ("Fluid2phipf".equals(fluid) ? "initFluid2" : null);
            if (expectedType == null || !expectedType.equals(featureType)) {
                throw new IllegalStateException("Phase Field initial-value feature has an unknown type or fluid semantic");
            }
            requireExactStringEnumProperty(feature, "InitialValuesOption", "SpecifyPhase",
                "SpecifyPhase", "UserDefined");
            requireExactStringEnumProperty(feature, "FluidInDomain", fluid,
                "Fluid1phipf", "Fluid2phipf");
            if (byFluid.put(fluid, feature) != null) {
                throw new IllegalStateException("duplicate Phase Field initial-value mapping for " + fluid);
            }
        }
        if (byFluid.size() != 2 || !byFluid.containsKey("Fluid1phipf") ||
            !byFluid.containsKey("Fluid2phipf")) {
            throw new IllegalStateException("Phase Field must expose exactly one initial-value feature for each fluid");
        }
        return byFluid;
    }

    private static void requireExactStringEnumProperty(ParameterEntity feature, String name,
            String expectedValue, String... expectedAllowedValues) {
        requirePropertyValueType(feature, name, "String");
        String[] observedAllowedValues = feature.getAllowedPropertyValues(name);
        Set<String> expected = new HashSet<>(Arrays.asList(expectedAllowedValues));
        Set<String> observed = observedAllowedValues == null ? Collections.emptySet() :
            new HashSet<>(Arrays.asList(observedAllowedValues));
        if (observedAllowedValues == null || observed.size() != observedAllowedValues.length ||
            expected.size() != expectedAllowedValues.length || !expected.equals(observed) ||
            !expected.contains(expectedValue) || !expectedValue.equals(feature.getString(name))) {
            throw new IllegalStateException("COMSOL property type, exact enum set, or current value differs for " + name);
        }
    }

    private static void requirePropertyValueType(ParameterEntity feature, String name, String expectedType) {
        if (feature == null || !feature.hasProperty(name)) {
            throw new IllegalStateException("required COMSOL property is absent: " + name);
        }
        String observedType = feature.getValueType(name);
        if (!expectedType.equals(observedType)) {
            throw new IllegalStateException("COMSOL property value type differs for " + name +
                ": expected " + expectedType + ", got " + observedType);
        }
    }

    private static void requireStringEnumProperty(ParameterEntity feature, String name, String expectedValue) {
        requirePropertyValueType(feature, name, "String");
        String[] allowedValues = feature.getAllowedPropertyValues(name);
        if (allowedValues == null || !contains(allowedValues, expectedValue)) {
            throw new IllegalStateException("COMSOL finite enum metadata does not permit exact " + name +
                " value " + expectedValue);
        }
    }

    private static Map<String, Object> verifyInitialDomainCoverage(Physics phaseField,
            PhysicsFeature defaultInit, PhysicsFeature fluid2Init, int[] glueDomainIds,
            int[] gasDomainIds) {
        int[] allDomainIds = sortedUnion(glueDomainIds, gasDomainIds);
        int[] phaseFieldDomainIds = phaseField.selection().entities(2);
        int[] defaultInputDomainIds = defaultInit.selection().inputEntities();
        String defaultNamedSelectionTag = defaultInit.selection().named();
        int[] defaultSelectionEntities = defaultInit.selection().entities(2);
        int[] fluid2InputDomainIds = fluid2Init.selection().inputEntities();
        String fluid2NamedSelectionTag = fluid2Init.selection().named();
        int[] fluid2SelectionEntities = fluid2Init.selection().entities(2);
        if (allDomainIds.length != 2 || intersects(glueDomainIds, gasDomainIds) ||
            !sameIds(phaseFieldDomainIds, allDomainIds) ||
            defaultInputDomainIds == null || !sameIds(defaultInputDomainIds, allDomainIds) ||
            defaultNamedSelectionTag == null || !defaultNamedSelectionTag.isEmpty() ||
            fluid2InputDomainIds == null || !sameIds(fluid2InputDomainIds, gasDomainIds) ||
            !"sel_gas_domain".equals(fluid2NamedSelectionTag) ||
            !sameIds(fluid2SelectionEntities, gasDomainIds)) {
            throw new IllegalStateException("Phase Field initial-value raw inputs or explicit Fluid2 override differ");
        }
        int[] effectiveDefaultDomainIds;
        boolean defaultRemainderDirect = false;
        String defaultRemainderSource;
        if (sameIds(defaultSelectionEntities, allDomainIds)) {
            effectiveDefaultDomainIds = difference(allDomainIds, gasDomainIds);
            defaultRemainderSource = "DOCUMENTED_DEFAULT_REMAINDER_DERIVED_FROM_ALL_MINUS_FLUID2_OVERRIDE";
        } else if (sameIds(defaultSelectionEntities, glueDomainIds)) {
            effectiveDefaultDomainIds = defaultSelectionEntities.clone();
            defaultRemainderDirect = true;
            defaultRemainderSource = "DIRECT_SELECTION_GETTER_REMAINDER_MATCHING_DOCUMENTED_DEFAULT";
        } else {
            throw new IllegalStateException("default Fluid1 selection getter is neither the full base nor the glue remainder");
        }
        if (!sameIds(effectiveDefaultDomainIds, glueDomainIds) ||
            intersects(effectiveDefaultDomainIds, fluid2SelectionEntities) ||
            !sameIds(sortedUnion(effectiveDefaultDomainIds, fluid2SelectionEntities), allDomainIds)) {
            throw new IllegalStateException("default-remainder and Fluid2 override do not partition the exact two domains");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("all_domain_ids", boxed(allDomainIds));
        result.put("phase_field_domain_ids", boxed(phaseFieldDomainIds));
        result.put("phase1_initial_raw_input_domain_ids", boxed(defaultInputDomainIds));
        result.put("phase1_initial_raw_input_entities_read_status", "READ");
        result.put("phase1_initial_raw_named_selection_tag", defaultNamedSelectionTag);
        result.put("phase1_initial_selection_entities_readback", boxed(defaultSelectionEntities));
        result.put("phase1_initial_effective_domain_ids", boxed(effectiveDefaultDomainIds));
        result.put("phase1_initial_effective_domain_source", defaultRemainderSource);
        result.put("phase1_initial_effective_domain_is_direct_readback", defaultRemainderDirect);
        result.put("phase1_initial_selection_semantics",
            "COMSOL_6_4_DEFAULT_SELECTION_ALL_ENTITIES_NOT_OVERRIDDEN_BY_SAME_LEVEL_NODE_DOC_5838_CHUNK_19242");
        result.put("phase1_initial_default_feature_classification",
            "NOT_EXPOSED_BY_READ_ONLY_API_METADATA");
        result.put("phase1_initial_selection", boxed(effectiveDefaultDomainIds));
        result.put("phase2_initial_raw_input_domain_ids", boxed(fluid2InputDomainIds));
        result.put("phase2_initial_raw_input_entities_read_status", "READ");
        result.put("phase2_initial_raw_named_selection_tag", fluid2NamedSelectionTag);
        result.put("phase2_initial_selection_entities_readback", boxed(fluid2SelectionEntities));
        result.put("phase2_initial_effective_domain_ids", boxed(fluid2SelectionEntities));
        result.put("phase2_initial_effective_domain_source", "DIRECT_NAMED_SELECTION_READBACK");
        result.put("phase2_initial_effective_domain_is_direct_readback", true);
        result.put("phase2_initial_selection", boxed(fluid2SelectionEntities));
        return result;
    }

    private static Map<String, Object> probeCapture(String operation, Supplier<?> call) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("operation", operation);
        try {
            row.put("status", "READ");
            row.put("value", probeJsonValue(call.get()));
        } catch (RuntimeException ex) {
            row.put("status", "READ_ERROR");
            row.put("error", readbackFeatureError(ex));
        }
        return row;
    }

    private static Object probeValue(Map<String, Object> capture) {
        return capture != null && "READ".equals(capture.get("status")) ? capture.get("value") : null;
    }

    private static List<?> probeListValue(Map<String, Object> capture) {
        Object value = probeValue(capture);
        return value instanceof List<?> ? (List<?>) value : Collections.emptyList();
    }

    private static Object probeJsonValue(Object value) {
        if (value == null || value instanceof String || value instanceof Boolean || value instanceof Number) return value;
        if (value instanceof String[]) return new ArrayList<>(Arrays.asList(((String[]) value).clone()));
        if (value instanceof int[]) {
            List<Integer> items = new ArrayList<>(); for (int item : (int[]) value) items.add(item); return items;
        }
        if (value instanceof long[]) {
            List<Long> items = new ArrayList<>(); for (long item : (long[]) value) items.add(item); return items;
        }
        if (value instanceof double[]) {
            List<Double> items = new ArrayList<>(); for (double item : (double[]) value) items.add(item); return items;
        }
        if (value instanceof boolean[]) {
            List<Boolean> items = new ArrayList<>(); for (boolean item : (boolean[]) value) items.add(item); return items;
        }
        if (value instanceof Object[]) {
            List<Object> items = new ArrayList<>(); for (Object item : (Object[]) value) items.add(probeJsonValue(item)); return items;
        }
        if (value instanceof List<?>) {
            List<Object> items = new ArrayList<>(); for (Object item : (List<?>) value) items.add(probeJsonValue(item)); return items;
        }
        String text = String.valueOf(value);
        return text.length() > 512 ? text.substring(0, 512) : text;
    }

    private static Map<String, Object> captureProbeProperties(PropFeature feature, String[] names) {
        Map<String, Object> result = new LinkedHashMap<>();
        Map<String, Object> inventory = probeCapture("properties", feature::properties);
        result.put("property_inventory_capture", inventory);
        Set<String> available = new HashSet<>();
        Object rawInventory = probeValue(inventory);
        if (rawInventory instanceof List<?>) {
            for (Object item : (List<?>) rawInventory) if (item instanceof String) available.add((String) item);
        }
        Map<String, Object> rows = new LinkedHashMap<>();
        for (String name : names) {
            Map<String, Object> property = new LinkedHashMap<>();
            property.put("property_name", name);
            Map<String, Object> has = probeCapture("hasProperty:" + name, () -> feature.hasProperty(name));
            property.put("has_property_capture", has);
            property.put("listed_in_properties", available.contains(name));
            Object hasValue = probeValue(has);
            if (!(hasValue instanceof Boolean) || !((Boolean) hasValue)) {
                property.put("declared_type_capture", null);
                property.put("allowed_values_capture", null);
                property.put("value_capture", null);
                property.put("value_reader", null);
                property.put("value_status", hasValue instanceof Boolean ? "PROPERTY_ABSENT" : "HAS_PROPERTY_READ_ERROR");
                rows.put(name, property);
                continue;
            }
            Map<String, Object> type = probeCapture("getValueType:" + name, () -> feature.getValueType(name));
            Map<String, Object> allowed = probeCapture("getAllowedPropertyValues:" + name,
                () -> feature.getAllowedPropertyValues(name));
            property.put("declared_type_capture", type);
            property.put("allowed_values_capture", allowed);
            Object typeValue = probeValue(type);
            String valueReader = null;
            Supplier<?> getter = null;
            if ("String".equals(typeValue)) { valueReader = "getString"; getter = () -> feature.getString(name); }
            else if ("Boolean".equals(typeValue)) { valueReader = "getBoolean"; getter = () -> feature.getBoolean(name); }
            else if ("Int".equals(typeValue)) { valueReader = "getInt"; getter = () -> feature.getInt(name); }
            else if ("Double".equals(typeValue)) { valueReader = "getString_expression"; getter = () -> feature.getString(name); }
            else if ("StringArray".equals(typeValue)) { valueReader = "getStringArray"; getter = () -> feature.getStringArray(name); }
            else if ("BooleanArray".equals(typeValue)) { valueReader = "getBooleanArray"; getter = () -> feature.getBooleanArray(name); }
            else if ("IntArray".equals(typeValue)) { valueReader = "getIntArray"; getter = () -> feature.getIntArray(name); }
            else if ("DoubleArray".equals(typeValue)) { valueReader = "getDoubleArray"; getter = () -> feature.getDoubleArray(name); }
            property.put("value_reader", valueReader);
            Map<String, Object> value = getter == null ? null : probeCapture(valueReader + ":" + name, getter);
            property.put("value_capture", value);
            property.put("value_status", value == null ? "TYPE_RECORDED_VALUE_NOT_INTERPRETED" :
                "READ".equals(value.get("status")) ? "READ_BY_DECLARED_TYPE" : "VALUE_READ_ERROR");
            rows.put(name, property);
        }
        result.put("properties", rows);
        return result;
    }

    private static Map<String, Object> captureSolverSequenceTopology(
            Model model, String sequenceTag, int[] nodeCount) {
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("requested_tag", sequenceTag);
        try {
            SolverSequence sequence = model.sol(sequenceTag);
            record.put("resolution_status", "READ");
            record.put("tag_capture", probeCapture("solver." + sequenceTag + ".tag", sequence::tag));
            record.put("sequence_type_capture", probeCapture(
                "solver." + sequenceTag + ".getSequenceType", sequence::getSequenceType));
            record.put("attached_capture", probeCapture(
                "solver." + sequenceTag + ".isAttached", sequence::isAttached));
            record.put("study_capture", probeCapture("solver." + sequenceTag + ".study", sequence::study));
            record.put("feature_tree_capture", captureSolverFeatureTree(
                sequence.feature(), sequenceTag, "", nodeCount, 0));
            boolean complete = "READ".equals(record.get("resolution_status")) &&
                "READ".equals(((Map<?, ?>) record.get("sequence_type_capture")).get("status")) &&
                "READ".equals(((Map<?, ?>) record.get("attached_capture")).get("status")) &&
                "READ".equals(((Map<?, ?>) record.get("study_capture")).get("status"));
            record.put("capture_complete", complete);
        } catch (RuntimeException ex) {
            record.put("resolution_status", "READ_ERROR");
            record.put("resolution_error", readbackFeatureError(ex));
            record.put("capture_complete", false);
        }
        return record;
    }

    /** Read and validate the generated initialization-to-transient solver graph. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> inspectGeneratedStudySolverChain(Model model, Study study) {
        if (model == null || study == null) throw new IllegalArgumentException("model/study is required");
        String studyTag = study.tag();
        List<String> findings = new ArrayList<>();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "W24_GENERATED_STUDY_SOLVER_CHAIN_R2");
        result.put("study_tag", studyTag);
        Map<String, Object> stepTagsCapture = probeCapture("study." + studyTag + ".feature.tags",
            () -> study.feature().tags());
        result.put("study_step_tags_capture", stepTagsCapture);
        List<String> stepTags = stringsFromCapture(stepTagsCapture);
        result.put("study_step_tags", stepTags);
        if (!Arrays.asList("phasei", "time").equals(stepTags)) findings.add("STUDY_STEP_ORDER_OR_TAG_READBACK_DIFFERS");
        List<Map<String, Object>> steps = new ArrayList<>();
        Map<String, Map<String, Object>> stepInitialization = new LinkedHashMap<>();
        for (String stepTag : stepTags) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("requested_tag", stepTag);
            try {
                StudyFeature feature = study.feature(stepTag);
                row.put("resolution_status", "READ");
                row.put("type_capture", probeCapture("study." + studyTag + ".step." + stepTag + ".type", feature::getType));
                row.put("properties", captureProbeProperties(feature,
                    new String[]{"initmethod", "initstudy", "useinitsol"}));
                Map<String, Object> scope = (Map<String, Object>) row.get("properties");
                Map<String, Object> properties = (Map<String, Object>) scope.get("properties");
                Map<String, Object> initialization = new LinkedHashMap<>();
                for (String name : new String[]{"initmethod", "initstudy", "useinitsol"}) {
                    initialization.put(name, readTypedCapturedString((Map<String, Object>) properties.get(name)));
                }
                stepInitialization.put(stepTag, initialization);
            } catch (RuntimeException ex) {
                row.put("resolution_status", "READ_ERROR");
                row.put("resolution_error", readbackFeatureError(ex));
                findings.add("STUDY_STEP_METADATA_INCOMPLETE:" + stepTag);
            }
            steps.add(row);
        }
        result.put("study_steps", steps);
        result.put("study_step_initialization_readback", stepInitialization);
        for (String stepTag : new String[]{"phasei", "time"}) {
            Map<String, Object> initialization = stepInitialization.get(stepTag);
            Map<String, Object> stepRecord = null;
            for (Map<String, Object> candidate : steps) {
                if (stepTag.equals(candidate.get("requested_tag"))) stepRecord = candidate;
            }
            String expectedType = "phasei".equals(stepTag) ? "PhaseInitialization" : "Transient";
            String expectedMethod = "phasei".equals(stepTag) ? "init" : "sol";
            String expectedStudy = "phasei".equals(stepTag) ? "zero" : studyTag;
            String actualType = stepRecord == null ? null :
                stringCapture((Map<String, Object>) stepRecord.get("type_capture"));
            if (initialization == null || stepRecord == null || !"READ".equals(stepRecord.get("resolution_status")) ||
                !expectedType.equals(actualType) || !expectedMethod.equals(initialization.get("initmethod")) ||
                !expectedStudy.equals(initialization.get("initstudy")) || !"off".equals(initialization.get("useinitsol"))) {
                findings.add("STUDY_STEP_INITIALIZATION_READBACK_DIFFERS:" + stepTag);
            }
        }

        String[] queryTypes = new String[]{"All", "SolverSequence", "CopySolution", "ParametricStore",
            "Stored", "Parametric", "None"};
        List<Map<String, Object>> queryRows = new ArrayList<>();
        Map<String, List<String>> queryValues = new LinkedHashMap<>();
        Set<String> tags = new java.util.LinkedHashSet<>();
        for (String queryType : queryTypes) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("query_type", queryType);
            Map<String, Object> capture = probeCapture("study." + studyTag + ".getSolverSequences." + queryType,
                () -> study.getSolverSequences(queryType));
            row.put("capture", capture);
            Object value = probeValue(capture);
            if (!(value instanceof List<?>) || ((List<?>) value).size() > 128) {
                findings.add("SEQUENCE_QUERY_CAPTURE_INVALID:" + queryType);
            } else {
                List<String> values = stringsFromCapture(capture);
                if (values.size() != ((List<?>) value).size() || new HashSet<>(values).size() != values.size()) {
                    findings.add("SEQUENCE_QUERY_TAGS_INVALID:" + queryType);
                }
                queryValues.put(queryType, values);
                tags.addAll(values);
            }
            queryRows.add(row);
        }
        result.put("sequence_query_types", Arrays.asList(queryTypes));
        result.put("sequence_query_rows", queryRows);
        Map<String, Object> globalCapture = probeCapture("model.sol.tags", () -> model.sol().tags());
        result.put("global_solver_tags_capture", globalCapture);
        List<String> globalTags = stringsFromCapture(globalCapture);
        result.put("global_solver_tags", globalTags);
        if (new HashSet<>(queryValues.getOrDefault("All", Collections.emptyList())).size() !=
                queryValues.getOrDefault("All", Collections.emptyList()).size() ||
            !new HashSet<>(queryValues.getOrDefault("All", Collections.emptyList())).equals(new HashSet<>(globalTags))) {
            findings.add("STUDY_ALL_QUERY_AND_MODEL_SOL_TAGS_DIFFER");
        }
        tags.addAll(globalTags);
        result.put("sequence_tag_union", new ArrayList<>(tags));
        if (tags.size() > 128) findings.add("SEQUENCE_UNION_EXCEEDS_BOUND");
        List<Map<String, Object>> records = new ArrayList<>();
        int[] nodeCount = new int[]{0};
        int captureLimit = Math.min(tags.size(), 128);
        int index = 0;
        for (String tag : tags) {
            if (index++ >= captureLimit) break;
            records.add(captureSolverSequenceTopology(model, tag, nodeCount));
        }
        result.put("sequence_records", records);
        if (nodeCount[0] > 128) findings.add("SEQUENCE_FEATURE_NODE_BOUND_EXCEEDED");

        Map<String, Map<String, Object>> recordsByTag = new LinkedHashMap<>();
        List<String> validAttachedTags = new ArrayList<>();
        List<String> invalidAttachedTags = new ArrayList<>();
        Map<String, String> storeReferences = new LinkedHashMap<>();
        for (Map<String, Object> record : records) {
            String tag = String.valueOf(record.get("requested_tag"));
            recordsByTag.put(tag, record);
            if (!Boolean.TRUE.equals(record.get("capture_complete"))) {
                findings.add("SEQUENCE_IDENTITY_CAPTURE_INCOMPLETE:" + tag);
            }
            Map<String, Object> featureTree = (Map<String, Object>) record.get("feature_tree_capture");
            if (featureTree == null || !"CAPTURED".equals(featureTree.get("capture_status"))) {
                findings.add("SEQUENCE_FEATURE_TREE_CAPTURE_INCOMPLETE:" + tag);
            }
            String sequenceType = stringCapture((Map<String, Object>) record.get("sequence_type_capture"));
            if (sequenceType == null || !Arrays.asList(queryTypes).subList(1, queryTypes.length).contains(sequenceType)) {
                findings.add("SEQUENCE_TYPE_UNCLASSIFIED:" + tag);
            } else if (!queryValues.getOrDefault(sequenceType, Collections.emptyList()).contains(tag)) {
                findings.add("SEQUENCE_TYPE_QUERY_MISMATCH:" + tag);
            }
            Boolean attached = booleanCapture((Map<String, Object>) record.get("attached_capture"));
            String associatedStudy = stringCapture((Map<String, Object>) record.get("study_capture"));
            if (attached == null || associatedStudy == null) {
                findings.add("SEQUENCE_ATTACHMENT_READBACK_INCOMPLETE:" + tag);
            }
            if (Boolean.TRUE.equals(attached) && studyTag.equals(associatedStudy)) {
                List<String> chainFindings = initializationChainFindings(record, studyTag);
                if (chainFindings.isEmpty()) {
                    validAttachedTags.add(tag);
                    storeReferences.put(tag, storeSolutionReference(record));
                } else {
                    invalidAttachedTags.add(tag);
                    findings.addAll(chainFindings);
                }
            } else if (Boolean.TRUE.equals(attached)) {
                findings.add("UNEXPECTED_ATTACHED_SEQUENCE_STUDY:" + tag);
            }
        }
        if (validAttachedTags.size() != 1 || !invalidAttachedTags.isEmpty()) {
            findings.add("ATTACHED_INITIALIZATION_CHAIN_NOT_UNIQUE");
        }
        String selectedTag = validAttachedTags.size() == 1 && invalidAttachedTags.isEmpty() ? validAttachedTags.get(0) : null;
        String storedTag = selectedTag == null ? null : storeReferences.get(selectedTag);
        if (selectedTag != null) {
            Map<String, Object> stored = recordsByTag.get(storedTag);
            if (storedTag == null || !queryValues.getOrDefault("Stored", Collections.emptyList()).equals(
                    Collections.singletonList(storedTag)) || stored == null ||
                !"Stored".equals(stringCapture((Map<String, Object>) stored.get("sequence_type_capture"))) ||
                !Boolean.FALSE.equals(booleanCapture((Map<String, Object>) stored.get("attached_capture")))) {
                findings.add("STORE_SOLUTION_REFERENCE_NOT_UNIQUELY_RESOLVED_TO_STORED_SEQUENCE");
            }
        }
        result.put("attached_chain_candidate_tags", validAttachedTags);
        result.put("attached_chain_failure_tags", invalidAttachedTags);
        result.put("selected_attached_sequence_tag", selectedTag);
        result.put("store_solution_output_tag", storedTag);
        result.put("semantic_findings", findings);
        result.put("status", findings.isEmpty() ? "PHASE_INIT_TO_TIME_CHAIN_VALIDATED" :
            "CHAIN_CAPTURED_WITH_FINDINGS_NOT_SOLVED");
        result.put("native_acceptance", "NOT_RUN");
        result.put("study_run_calls", 0);
        result.put("solver_run_calls", 0);
        result.put("model_save_calls", 0);
        result.put("result_evaluation_calls", 0);
        return result;
    }

    @SuppressWarnings("unchecked")
    private static List<String> initializationChainFindings(Map<String, Object> record, String studyTag) {
        List<String> findings = new ArrayList<>();
        List<Map<String, Object>> nodes = new ArrayList<>();
        flattenSolverFeatureNodes((Map<String, Object>) record.get("feature_tree_capture"), nodes);
        List<Integer> phaseSteps = new ArrayList<>(), timeSteps = new ArrayList<>();
        List<Integer> phaseVariables = new ArrayList<>(), timeVariables = new ArrayList<>();
        List<Integer> stationary = new ArrayList<>(), stores = new ArrayList<>(), times = new ArrayList<>();
        for (int i = 0; i < nodes.size(); i++) {
            Map<String, Object> node = nodes.get(i);
            String type = stringCapture((Map<String, Object>) node.get("type_capture"));
            if ("StudyStep".equals(type)) {
                String study = featureStringProperty(node, "study_step_reference_properties", "study");
                String step = featureStringProperty(node, "study_step_reference_properties", "studystep");
                if (studyTag.equals(study) && "phasei".equals(step)) phaseSteps.add(i);
                if (studyTag.equals(study) && "time".equals(step)) timeSteps.add(i);
            } else if ("Variables".equals(type)) {
                String control = featureStringProperty(node, "initial_source_properties", "control");
                if ("phasei".equals(control)) phaseVariables.add(i);
                if ("time".equals(control)) timeVariables.add(i);
            } else if ("Stationary".equals(type)) stationary.add(i);
            else if ("StoreSolution".equals(type)) stores.add(i);
            else if ("Time".equals(type)) times.add(i);
        }
        String sequenceTag = String.valueOf(record.get("requested_tag"));
        if (phaseSteps.size() != 1 || timeSteps.size() != 1 || phaseVariables.size() != 1 ||
            timeVariables.size() != 1 || stationary.size() != 1 || stores.size() != 1 || times.size() != 1) {
            findings.add("ATTACHED_SEQUENCE_CHAIN_ROLE_CARDINALITY:" + sequenceTag);
            return findings;
        }
        int[] order = new int[]{phaseSteps.get(0), phaseVariables.get(0), stationary.get(0), stores.get(0),
            timeSteps.get(0), timeVariables.get(0), times.get(0)};
        for (int i = 1; i < order.length; i++) {
            if (order[i - 1] >= order[i]) {
                findings.add("ATTACHED_SEQUENCE_CHAIN_ORDER:" + sequenceTag);
                break;
            }
        }
        Map<String, Object> phaseVars = nodes.get(phaseVariables.get(0));
        Map<String, Object> timeVars = nodes.get(timeVariables.get(0));
        Map<String, Object> store = nodes.get(stores.get(0));
        if (!"init".equals(featureStringProperty(phaseVars, "initial_source_properties", "initmethod")) ||
            !"zero".equals(featureStringProperty(phaseVars, "initial_source_properties", "initsol")) ||
            !"current".equals(featureStringProperty(phaseVars, "initial_source_properties", "initsoluse"))) {
            findings.add("PHASE_INITIALIZATION_VARIABLES_DEFAULTS_DIFFER:" + sequenceTag);
        }
        String storeTag = featureStringProperty(store, "store_solution_properties", "sol");
        if (!"sol".equals(featureStringProperty(timeVars, "initial_source_properties", "initmethod")) ||
            !sequenceTag.equals(featureStringProperty(timeVars, "initial_source_properties", "initsol")) ||
            storeTag == null || !storeTag.equals(featureStringProperty(timeVars, "initial_source_properties", "initsoluse"))) {
            findings.add("TRANSIENT_VARIABLES_STORE_REFERENCE_MISMATCH:" + sequenceTag);
        }
        Map<String, Object> prop = featurePropertyRow(store, "store_solution_properties", "sol");
        if (prop == null || !Boolean.TRUE.equals(propertyCaptureValue(prop, "has_property_capture")) ||
            !"String".equals(propertyCaptureValue(prop, "declared_type_capture")) ||
            !"READ_BY_DECLARED_TYPE".equals(prop.get("value_status"))) {
            findings.add("STORE_SOLUTION_REFERENCE_NOT_TYPED_READ:" + sequenceTag);
        }
        return findings;
    }

    @SuppressWarnings("unchecked")
    private static void flattenSolverFeatureNodes(Map<String, Object> tree, List<Map<String, Object>> nodes) {
        if (tree == null || !(tree.get("nodes") instanceof List<?>)) return;
        for (Object value : (List<?>) tree.get("nodes")) {
            if (!(value instanceof Map<?, ?>)) continue;
            Map<String, Object> node = (Map<String, Object>) value;
            nodes.add(node);
            Object child = node.get("child_feature_tree");
            if (child instanceof Map<?, ?>) flattenSolverFeatureNodes((Map<String, Object>) child, nodes);
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> featurePropertyRow(Map<String, Object> feature, String scope, String name) {
        Object rawScope = feature.get(scope);
        if (!(rawScope instanceof Map<?, ?>)) return null;
        Object rawProperties = ((Map<?, ?>) rawScope).get("properties");
        if (!(rawProperties instanceof Map<?, ?>)) return null;
        Object row = ((Map<?, ?>) rawProperties).get(name);
        return row instanceof Map<?, ?> ? (Map<String, Object>) row : null;
    }

    private static Object propertyCaptureValue(Map<String, Object> row, String captureName) {
        Object capture = row.get(captureName);
        return capture instanceof Map<?, ?> ? probeValue((Map<String, Object>) capture) : null;
    }

    private static String featureStringProperty(Map<String, Object> feature, String scope, String name) {
        Map<String, Object> row = featurePropertyRow(feature, scope, name);
        Object value = row == null ? null : propertyCaptureValue(row, "value_capture");
        return value instanceof String ? (String) value : null;
    }

    private static String storeSolutionReference(Map<String, Object> record) {
        List<Map<String, Object>> nodes = new ArrayList<>();
        flattenSolverFeatureNodes((Map<String, Object>) record.get("feature_tree_capture"), nodes);
        for (Map<String, Object> node : nodes) {
            if ("StoreSolution".equals(stringCapture((Map<String, Object>) node.get("type_capture")))) {
                return featureStringProperty(node, "store_solution_properties", "sol");
            }
        }
        return null;
    }

    private static String readTypedCapturedString(Map<String, Object> property) {
        if (property == null || !"String".equals(propertyCaptureValue(property, "declared_type_capture")) ||
            !Boolean.TRUE.equals(propertyCaptureValue(property, "has_property_capture")) ||
            !"READ_BY_DECLARED_TYPE".equals(property.get("value_status"))) return null;
        Object value = propertyCaptureValue(property, "value_capture");
        return value instanceof String ? (String) value : null;
    }

    private static String stringCapture(Map<String, Object> capture) {
        Object value = probeValue(capture);
        return value instanceof String ? (String) value : null;
    }

    private static Boolean booleanCapture(Map<String, Object> capture) {
        Object value = probeValue(capture);
        return value instanceof Boolean ? (Boolean) value : null;
    }

    private static List<String> stringsFromCapture(Map<String, Object> capture) {
        Object value = probeValue(capture);
        if (!(value instanceof List<?>)) return Collections.emptyList();
        List<String> result = new ArrayList<>();
        for (Object item : (List<?>) value) if (item instanceof String) result.add((String) item);
        return result;
    }

    private static Map<String, Object> captureSolverFeatureTree(
            SolverFeatureList list, String sequenceTag, String parentPath,
            int[] nodeCount, int depth) {
        Map<String, Object> tree = new LinkedHashMap<>();
        tree.put("sequence_tag", sequenceTag);
        tree.put("parent_path", parentPath);
        Map<String, Object> tagsCapture = probeCapture(
            "solver." + sequenceTag + ".features." + parentPath + ".tags", list::tags);
        tree.put("tags_capture", tagsCapture);
        Object tagsRaw = probeValue(tagsCapture);
        List<Map<String, Object>> nodes = new ArrayList<>();
        if (!(tagsRaw instanceof List<?>)) {
            tree.put("nodes", nodes);
            tree.put("capture_status", "FEATURE_TAG_READ_ERROR");
            return tree;
        }
        List<?> tags = (List<?>) tagsRaw;
        if (tags.size() > 128) tree.put("tag_count_status", "EXCEEDS_128_TAG_CAP");
        for (Object item : tags) {
            if (!(item instanceof String)) continue;
            String tag = (String) item;
            Map<String, Object> node = new LinkedHashMap<>();
            node.put("tag", tag);
            node.put("path", parentPath.isEmpty() ? tag : parentPath + "/" + tag);
            if (depth > 16 || nodeCount[0] >= 128) {
                node.put("capture_status", depth > 16 ? "DEPTH_CAP" : "NODE_CAP");
                nodes.add(node);
                continue;
            }
            nodeCount[0]++;
            try {
                SolverFeature feature = list.get(tag);
                node.put("resolution_status", "READ");
                Map<String, Object> typeCapture = probeCapture(
                    "solver." + sequenceTag + "." + node.get("path") + ".getType", feature::getType);
                node.put("type_capture", typeCapture);
                Object type = probeValue(typeCapture);
                Map<String, Object> propertyInventory = probeCapture(
                    "solver." + sequenceTag + "." + node.get("path") + ".properties", feature::properties);
                node.put("property_inventory_capture", propertyInventory);
                Object propertyNames = probeValue(propertyInventory);
                List<String> studyReferenceNames = new ArrayList<>();
                if (propertyNames instanceof List<?>) {
                    for (Object propertyName : (List<?>) propertyNames) {
                        if (!(propertyName instanceof String)) continue;
                        String lower = ((String) propertyName).toLowerCase(java.util.Locale.ROOT);
                        if (lower.contains("study") || lower.contains("step")) {
                            studyReferenceNames.add((String) propertyName);
                        }
                    }
                }
                node.put("study_step_reference_property_names", studyReferenceNames);
                node.put("study_step_reference_properties",
                    captureProbeProperties(feature, studyReferenceNames.toArray(new String[0])));
                if ("Variables".equals(type)) {
                    node.put("initial_source_properties", captureProbeProperties(feature,
                        new String[]{"control", "initmethod", "initsol", "initsoluse", "useinitsol"}));
                } else if ("Time".equals(type)) {
                    node.put("time_settings", captureProbeProperties(feature,
                        new String[]{"timemethod", "tunit", "tstepsbdf", "tout", "tstepsstore",
                                     "maxstepconstraintbdf", "maxstepbdf"}));
                } else if ("StoreSolution".equals(type)) {
                    node.put("store_solution_properties", captureProbeProperties(feature, new String[]{"sol"}));
                }
                SolverFeatureList childList = feature.feature();
                Map<String, Object> childTree = captureSolverFeatureTree(
                    childList, sequenceTag, String.valueOf(node.get("path")), nodeCount, depth + 1);
                node.put("child_feature_tree", childTree);
                node.put("capture_status", "CAPTURED");
            } catch (RuntimeException ex) {
                node.put("capture_status", "READ_ERROR");
                node.put("capture_error", readbackFeatureError(ex));
            }
            nodes.add(node);
        }
        tree.put("nodes", nodes);
        tree.put("capture_status", "CAPTURED");
        return tree;
    }


    private static String readbackFeatureError(RuntimeException ex) {
        String message = ex.getMessage();
        if (message == null) message = "";
        return message.length() > 512 ? message.substring(0, 512) : message;
    }

    private static Map<String, Object> readback(Model model, Map<String, Object> args) {
        String expectedConfigurationId = String.valueOf(
            args.getOrDefault("expected_configuration_id", "baseline"));
        Configuration configuration = configuration(expectedConfigurationId);
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        int[] glueIds = model.component(COMPONENT).selection("sel_glue_domain").entities(2);
        int[] gasIds = model.component(COMPONENT).selection("sel_gas_domain").entities(2);
        if (geom.getSDim() != 2 || !geom.isAxisymmetric() || geom.getNDomains() != 2 ||
            glueIds.length != 1 || gasIds.length != 1 || glueIds[0] == gasIds[0]) {
            throw new IllegalStateException("saved static-shape model lost its two-domain axisymmetric geometry");
        }
        int[] allDomains = sortedUnion(glueIds, gasIds);

        String[] selectionTags = model.component(COMPONENT).selection().tags();
        Map<String, int[]> wetting = new LinkedHashMap<>();
        String caseId;
        if (contains(selectionTags, "sel_wet_flat_base")) {
            caseId = "flat";
            wetting.put("sel_wet_flat_base",
                model.component(COMPONENT).selection("sel_wet_flat_base").entities(1));
        } else if (contains(selectionTags, "sel_wet_mesa_top") &&
                   contains(selectionTags, "sel_wet_mesa_side") &&
                   contains(selectionTags, "sel_wet_lower_base")) {
            caseId = "step";
            wetting.put("sel_wet_mesa_top",
                model.component(COMPONENT).selection("sel_wet_mesa_top").entities(1));
            wetting.put("sel_wet_mesa_side",
                model.component(COMPONENT).selection("sel_wet_mesa_side").entities(1));
            wetting.put("sel_wet_lower_base",
                model.component(COMPONENT).selection("sel_wet_lower_base").entities(1));
        } else {
            throw new IllegalStateException("saved shape model has neither the exact flat nor stepped wetting selections");
        }
        Set<Integer> wetIds = new HashSet<>();
        Map<String, Object> wettingReadback = new LinkedHashMap<>();
        for (Map.Entry<String, int[]> entry : wetting.entrySet()) {
            int[] ids = entry.getValue();
            if (ids.length == 0) throw new IllegalStateException("saved substrate selection is empty: " + entry.getKey());
            for (int id : ids) {
                if (!wetIds.add(id)) throw new IllegalStateException("saved substrate selections overlap at boundary " + id);
            }
            wettingReadback.put(entry.getKey(), boxed(ids));
        }
        int[] axisIds = model.component(COMPONENT).selection("sel_axis").entities(1);
        for (int id : axisIds) {
            if (wetIds.contains(id)) throw new IllegalStateException("saved wetting selection includes the symmetry axis");
        }

        Material glue = model.material("matGlue");
        Material gas = model.material("matGas");
        Material multiphase = model.component(COMPONENT).material("mpmat1");
        int[] multiphaseDomains = multiphase.selection().entities(2);
        Physics flow = model.component(COMPONENT).physics("spf");
        Physics phase = model.component(COMPONENT).physics("pf");
        Map<String, PhysicsFeature> phaseInitialValues =
            resolvePhaseInitialValueFeatures(phase);
        PhysicsFeature initGlue = phaseInitialValues.get("Fluid1phipf");
        PhysicsFeature initGas = phaseInitialValues.get("Fluid2phipf");
        Map<String, Object> initialDomainReadback =
            verifyInitialDomainCoverage(phase, initGlue, initGas, glueIds, gasIds);
        PhysicsFeature phaseModel = resolvePhaseFieldModel(phase);
        PhysicsFeature pressureReference = flow.feature("pressureReference");
        ParameterEntity physicalModel = flow.prop("PhysicalModelProperty");
        requireStringEnumProperty(physicalModel, "IncludeGravity", "0");
        requireStringEnumProperty(physicalModel, "Compressibility", "Incompressible");
        requirePropertyValueType(phaseModel, "chi", "String");
        requirePropertyValueType(phaseModel, "epsilon_pf", "String");
        requirePropertyValueType(physicalModel, "Compressibility", "String");
        String compressibility = physicalModel.getString("Compressibility");
        String gravitySwitch = physicalModel.getString("IncludeGravity");
        boolean gravityEnabled = "1".equals(gravitySwitch);
        requireStringEnumProperty(phaseModel, "chiOption", "userdef");
        String chiOption = phaseModel.getString("chiOption");
        String chiExpression = phaseModel.getString("chi");
        if (!"Incompressible".equals(compressibility) ||
            !"0".equals(gravitySwitch) || gravityEnabled ||
            !"userdef".equals(chiOption) || !"chiPF".equals(chiExpression) ||
            !"PressurePointConstraint".equals(pressureReference.getType()) ||
            !"0[Pa]".equals(pressureReference.getString("p0")) ||
            !"Fluid1phipf".equals(initGlue.getString("FluidInDomain")) ||
            !"Fluid2phipf".equals(initGas.getString("FluidInDomain")) ||
            !"SpecifyPhase".equals(initGlue.getString("InitialValuesOption")) ||
            !"SpecifyPhase".equals(initGas.getString("InitialValuesOption")) ||
            !sameIds(allDomains, multiphaseDomains)) {
            throw new IllegalStateException("saved material/initial-value domain mapping differs from the built model");
        }

        PhysicsFeature defaultWettedWall = phase.feature("ww1");
        if (!"ww1".equals(defaultWettedWall.tag()) || !"WettedWall".equals(defaultWettedWall.getType())) {
            throw new IllegalStateException("saved model is missing the exact default Wetted Wall node ww1");
        }
        Map<String, Object> defaultWettedWallMetadata = new LinkedHashMap<>();
        defaultWettedWallMetadata.put("feature_tag", defaultWettedWall.tag());
        defaultWettedWallMetadata.put("feature_type", defaultWettedWall.getType());
        defaultWettedWallMetadata.put("classification",
            "COMSOL_6_4_DEFAULT_ALL_NOT_OVERRIDDEN_SELECTION_DOC_5838_CHUNK_19242");
        defaultWettedWallMetadata.put("selection_status", "DOCUMENTED_DEFAULT_NOT_EDITABLE");

        Set<String> expectedWallTags = new HashSet<>();
        expectedWallTags.add("ww1");
        for (int index = 1; index <= wetting.size(); index++) expectedWallTags.add("wwshape" + index);
        Set<String> observedWallTags = new HashSet<>();
        for (String tag : phase.feature().tags()) {
            if ("WettedWall".equals(phase.feature(tag).getType())) observedWallTags.add(tag);
        }
        if (observedWallTags.size() != expectedWallTags.size() || !observedWallTags.equals(expectedWallTags)) {
            throw new IllegalStateException("saved Wetted Wall default/custom feature inventory is not exact");
        }
        List<Map<String, Object>> wallFeatures = new ArrayList<>();
        List<String> wettingNames = new ArrayList<>(wetting.keySet());
        Set<Integer> customBoundaryIds = new HashSet<>();
        for (int wallIndex = 0; wallIndex < wettingNames.size(); wallIndex++) {
            String tag = "wwshape" + (wallIndex + 1);
            PhysicsFeature feature = phase.feature(tag);
            int[] selected = feature.selection().entities(1);
            if (!tag.equals(feature.tag()) || !"WettedWall".equals(feature.getType()) || selected.length == 0 ||
                !"SpecifyContactAngleDirectly".equals(feature.getString("SpecifyContactAngle")) ||
                !"thetaSubstrate".equals(feature.getString("thetaw")) ||
                !sameIds(wetting.get(wettingNames.get(wallIndex)), selected)) {
                throw new IllegalStateException("saved custom Wetted Wall feature configuration is incomplete: " + tag);
            }
            for (int boundaryId : selected) {
                if (!customBoundaryIds.add(boundaryId)) {
                    throw new IllegalStateException("saved custom Wetted Wall selections overlap at boundary " + boundaryId);
                }
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("feature_tag", tag);
            row.put("feature_type", feature.getType());
            row.put("surface_selection", wettingNames.get(wallIndex));
            row.put("boundary_ids", boxed(selected));
            row.put("contact_angle_expression", feature.getString("thetaw"));
            wallFeatures.add(row);
        }
        Material multiphaseMaterial = multiphase;
        if (!"pf".equals(multiphaseMaterial.getString("vfDefinition")) ||
            !"matGlue".equals(multiphaseMaterial.feature("phase1").getString("link")) ||
            !"matGas".equals(multiphaseMaterial.feature("phase2").getString("link"))) {
            throw new IllegalStateException("saved multiphase material links differ from the intended glue/gas phases");
        }

        Map<String, Object> parameterReadback = new LinkedHashMap<>();
        parameterReadback.put("rhoGlue", parameter(model, "rhoGlue", 1200.0, "kg/m^3"));
        parameterReadback.put("muGlue", parameter(model, "muGlue", 1.0, "Pa*s"));
        parameterReadback.put("rhoGas", parameter(model, "rhoGas", 1.2, "kg/m^3"));
        parameterReadback.put("muGas", parameter(model, "muGas", 0.018, "Pa*s"));
        parameterReadback.put("sigma0", parameter(model, "sigma0", 0.03, "N/m"));
        parameterReadback.put("epsPF", parameter(model, "epsPF", configuration.epsilonM, "m"));
        parameterReadback.put("Rdrop", parameter(model, "Rdrop", 500e-6, "m"));

        Study study = model.study("stdShape");
        String[] studyStepOrder = study.feature().tags();
        StudyFeature phaseInitialization = study.feature("phasei");
        StudyFeature transientStep = study.feature("time");
        String phaseInitializationType = phaseInitialization.getType();
        String transientType = transientStep.getType();
        Map<String, Object> solverChain = inspectGeneratedStudySolverChain(model, study);
        if (!"PHASE_INIT_TO_TIME_CHAIN_VALIDATED".equals(solverChain.get("status"))) {
            throw new IllegalStateException("saved phase-init/store/transient chain failed semantic readback: " +
                solverChain.get("semantic_findings"));
        }
        Map<String, Object> stepInitialization = (Map<String, Object>) solverChain.get("study_step_initialization_readback");
        Map<String, Object> timeInitialization = stepInitialization == null ? null :
            (Map<String, Object>) stepInitialization.get("time");
        String timeInitializationMethod = timeInitialization == null ? null :
            (String) timeInitialization.get("initmethod");
        String timeInitializationStudy = timeInitialization == null ? null :
            (String) timeInitialization.get("initstudy");
        String useInitialSolution = timeInitialization == null ? null :
            (String) timeInitialization.get("useinitsol");
        String useUserTolerance = transientStep.getString("usertol");
        String relativeTolerance = transientStep.getString("rtol");
        String timeList = transientStep.getString("tlist");
        List<String> solverTagList = (List<String>) solverChain.get("sequence_tag_union");
        String[] solverTags = solverTagList == null ? new String[0] : solverTagList.toArray(new String[0]);
        String attachedSequenceTag = (String) solverChain.get("selected_attached_sequence_tag");
        String storedOutputTag = (String) solverChain.get("store_solution_output_tag");
        if (!Arrays.equals(studyStepOrder, new String[]{"phasei", "time"}) ||
            !"PhaseInitialization".equals(phaseInitializationType) ||
            !"Transient".equals(transientType) || solverTags.length < 2 ||
            attachedSequenceTag == null || storedOutputTag == null) {
            throw new IllegalStateException("saved shape study lost its Phase Initialization/Transient sequence");
        }
        if (!"sol".equals(timeInitializationMethod) || !"stdShape".equals(timeInitializationStudy) ||
            !"off".equals(useInitialSolution) ||
            !"on".equals(useUserTolerance) || !"1e-4".equals(relativeTolerance) ||
            !"range(0[s],tCapillary/2,20*tCapillary)".equals(timeList)) {
            throw new IllegalStateException("saved transient study initialization/tolerance/time-list readback differs");
        }
        SolverSequence sequence = model.sol(attachedSequenceTag);
        if (!sequence.isAttached() || !"stdShape".equals(sequence.study())) {
            throw new IllegalStateException("saved shape solver sequence is detached from stdShape");
        }
        SolverFeature timeSolver = uniqueTimeFeature(sequence);
        String timeSolverFeatureTag = timeSolver.tag();
        String bdfOutput = timeSolver.getString("tstepsbdf");
        String outputMode = timeSolver.getString("tout");
        int storedSteps = timeSolver.getInt("tstepsstore");
        double maxStep = timeSolver.getDouble("maxstepbdf");
        if (!"strict".equals(bdfOutput) || !"tsteps".equals(outputMode) || storedSteps != 1 ||
            !Double.isFinite(maxStep) || maxStep <= 0.0 ||
            Math.abs(maxStep - configuration.maximumStepS) >
                Math.max(1e-15, configuration.maximumStepS * 1e-12)) {
            throw new IllegalStateException("saved shape solver output/max-step settings differ from the configured study");
        }
        String[] meshTags = model.component(COMPONENT).mesh().tags();
        if (!contains(meshTags, "mesh1")) throw new IllegalStateException("saved static-shape mesh1 is absent");
        Map<String, Object> meshReadback = readMesh(model, configuration);

        MultiphysicsCoupling coupling = model.component(COMPONENT).multiphysics("tpf1");
        requireStringEnumProperty(coupling, "IncludeSurfaceTension", "1");
        requireStringEnumProperty(coupling, "SurfaceTensionCoefficient", "userdef");
        requirePropertyValueType(coupling, "sigma", "String");
        String surfaceTensionSwitch = coupling.getString("IncludeSurfaceTension");
        boolean surfaceTensionEnabled = "1".equals(surfaceTensionSwitch);
        if (!"TwoPhaseFlowPhaseField".equals(coupling.getType()) ||
            !"spf".equals(coupling.getString("Fluid_physics")) ||
            !"pf".equals(coupling.getString("Mathematics_physics")) ||
            !"mpmat1".equals(coupling.getString("multiphaseMaterialList")) ||
            !"1".equals(surfaceTensionSwitch) || !surfaceTensionEnabled ||
            !"userdef".equals(coupling.getString("SurfaceTensionCoefficient")) ||
            !"sigma0".equals(coupling.getString("sigma"))) {
            throw new IllegalStateException("saved two-phase coupling/surface-tension readback is incomplete");
        }
        Map<String, Object> solutionState = requireNoStoredSolutionData(model);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "STATIC_SHAPE_NATIVE_CONFIGURATION_READBACK");
        result.put("native_acceptance", "NOT_RUN");
        result.put("model_tag", model.tag());
        result.put("case_id", caseId);
        result.put("configuration_id", configuration.id);
        result.put("geometry_dimension", geom.getSDim());
        result.put("geometry_axisymmetric", geom.isAxisymmetric());
        result.put("geometry_domain_count", geom.getNDomains());
        result.put("geometry_boundary_count", geom.getNBoundaries());
        result.put("glue_domain_ids", boxed(glueIds));
        result.put("gas_domain_ids", boxed(gasIds));
        result.put("multiphase_domain_ids", boxed(multiphaseDomains));
        result.put("multiphase_volume_fraction_definition", multiphaseMaterial.getString("vfDefinition"));
        result.put("phase1_material_link", multiphaseMaterial.feature("phase1").getString("link"));
        result.put("phase2_material_link", multiphaseMaterial.feature("phase2").getString("link"));
        result.put("substrate_wetting_selections", wettingReadback);
        result.put("axis_boundary_ids", boxed(axisIds));
        result.put("default_wetted_wall_feature_metadata", defaultWettedWallMetadata);
        result.put("wetted_wall_features", wallFeatures);
        result.put("parameter_values_and_units", parameterReadback);
        result.put("configuration_readback", Map.of(
            "configuration_id", configuration.id,
            "mesh", meshReadback,
            "maximum_step_s", maxStep,
            "maximum_step_over_capillary_time", maxStep / CAPILLARY_TIME_S));
        result.put("glue_density_expression", glue.propertyGroup("def").getString("density"));
        result.put("gas_density_expression", gas.propertyGroup("def").getString("density"));
        result.put("glue_viscosity_expression", glue.propertyGroup("def").getString("dynamicviscosity"));
        result.put("gas_viscosity_expression", gas.propertyGroup("def").getString("dynamicviscosity"));
        result.put("phase_field_type", phase.getType());
        result.put("phase_field_model_tag", phaseModel.tag());
        result.put("phase_field_model_type", phaseModel.getType());
        result.put("phase_field_epsilon_expression", phaseModel.getString("epsilon_pf"));
        result.put("phase_field_chi_option", chiOption);
        result.put("phase_field_chi_value_type", phaseModel.getValueType("chi"));
        result.put("phase_field_chi_expression", chiExpression);
        result.put("phase1_initial_feature_tag", initGlue.tag());
        result.put("phase1_initial_feature_type", initGlue.getType());
        result.put("phase1_initial_values_option", initGlue.getString("InitialValuesOption"));
        result.put("phase1_initial_value", initGlue.getString("FluidInDomain"));
        result.put("phase2_initial_feature_tag", initGas.tag());
        result.put("phase2_initial_feature_type", initGas.getType());
        result.put("phase2_initial_values_option", initGas.getString("InitialValuesOption"));
        result.put("phase2_initial_value", initGas.getString("FluidInDomain"));
        result.putAll(initialDomainReadback);
        result.put("flow_type", flow.getType());
        result.put("flow_compressibility", compressibility);
        result.put("gravity_enabled", gravityEnabled);
        result.put("pressure_reference_type", pressureReference.getType());
        result.put("pressure_reference_value", pressureReference.getString("p0"));
        result.put("pressure_reference_point_ids", boxed(pressureReference.selection().entities(0)));
        result.put("two_phase_coupling_type", coupling.getType());
        result.put("surface_tension_enabled", surfaceTensionEnabled);
        result.put("surface_tension_mode", coupling.getString("SurfaceTensionCoefficient"));
        result.put("surface_tension_expression", coupling.getString("sigma"));
        result.put("phase_initialization_step_type", phaseInitializationType);
        result.put("transient_step_type", transientType);
        result.put("study_step_order", Arrays.asList(studyStepOrder));
        result.put("time_initialization_study", timeInitializationStudy);
        result.put("use_initial_solution", useInitialSolution);
        result.put("use_user_tolerance", useUserTolerance);
        result.put("relative_tolerance", relativeTolerance);
        result.put("time_list_expression", timeList);
        result.put("generated_study_solver_chain", solverChain);
        result.put("solver_sequence_tags", Arrays.asList(solverTags));
        result.put("attached_solver_sequence_tag", attachedSequenceTag);
        result.put("stored_solution_output_tag", storedOutputTag);
        result.put("time_initialization_method", timeInitializationMethod);
        result.put("time_solver_feature_tag", timeSolverFeatureTag);
        result.put("time_solver_output_owner", "GENERATED_TIME_SOLVER_FEATURE");
        result.put("bdf_output_time_policy", bdfOutput);
        result.put("output_mode", outputMode);
        result.put("stored_time_steps_policy", storedSteps);
        result.put("maximum_step_s", maxStep);
        result.put("mesh_tags", Arrays.asList(meshTags));
        result.put("solution_state_readback", solutionState);
        result.put("study_run_calls_this_action", 0);
        return result;
    }

    private static Map<String, Object> readMesh(Model model, Configuration expected) {
        com.comsol.model.MeshSequence mesh = model.component(COMPONENT).mesh("mesh1");
        double hmax = mesh.feature("size").getDouble("hmax");
        double hmin = mesh.feature("size").getDouble("hmin");
        String custom = mesh.feature("size").getString("custom");
        if (!Double.isFinite(hmax) || !Double.isFinite(hmin) ||
            Math.abs(hmax - expected.meshHmaxM) > Math.max(1e-15, expected.meshHmaxM * 1e-12) ||
            Math.abs(hmin - expected.meshHminM) > Math.max(1e-15, expected.meshHminM * 1e-12) ||
            !("on".equalsIgnoreCase(custom) || "true".equalsIgnoreCase(custom) || "1".equals(custom))) {
            throw new IllegalStateException("saved mesh hmax/hmin/custom readback differs from the preregistered configuration");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("mesh_tag", "mesh1");
        result.put("custom", true);
        result.put("hmax_m", hmax);
        result.put("hmin_m", hmin);
        result.put("hmax_over_epsilon", hmax / expected.epsilonM);
        result.put("hmin_over_epsilon", hmin / expected.epsilonM);
        return result;
    }

    private static Configuration configuration(String id) {
        switch (id) {
            case "baseline": return new Configuration(id, 8e-6, 4e-6, 2e-6, 0.10);
            case "mesh_ratio_1_3": return new Configuration(id, 8e-6, 8e-6 / 3.0, 2e-6, 0.10);
            case "mesh_ratio_1_1": return new Configuration(id, 8e-6, 8e-6, 2e-6, 0.10);
            case "epsilon_6um": return new Configuration(id, 6e-6, 3e-6, 1.5e-6, 0.10);
            case "epsilon_10um": return new Configuration(id, 10e-6, 5e-6, 2.5e-6, 0.10);
            case "step_0_05Tc": return new Configuration(id, 8e-6, 4e-6, 2e-6, 0.05);
            case "step_0_20Tc": return new Configuration(id, 8e-6, 4e-6, 2e-6, 0.20);
            default: throw new IllegalArgumentException("expected_configuration_id is not a preregistered W24 configuration");
        }
    }

    private static final class Configuration {
        final String id;
        final double epsilonM;
        final double meshHmaxM;
        final double meshHminM;
        final double maximumStepOverTc;
        final double maximumStepS;

        Configuration(String id, double epsilonM, double meshHmaxM, double meshHminM,
                      double maximumStepOverTc) {
            this.id = id;
            this.epsilonM = epsilonM;
            this.meshHmaxM = meshHmaxM;
            this.meshHminM = meshHminM;
            this.maximumStepOverTc = maximumStepOverTc;
            this.maximumStepS = maximumStepOverTc * CAPILLARY_TIME_S;
        }
    }

    private static Map<String, Object> saveUnsolved(Model model, Map<String, Object> args) throws IOException {
        String workspaceText = String.valueOf(args.getOrDefault("workspace_path", ""));
        String destinationText = String.valueOf(args.getOrDefault("path", ""));
        if (workspaceText.isEmpty() || destinationText.isEmpty()) {
            throw new IllegalArgumentException("save requires workspace_path and path");
        }
        Path workspace = Paths.get(workspaceText).toAbsolutePath().normalize();
        Path destination = Paths.get(destinationText).toAbsolutePath().normalize();
        if (destination.equals(workspace) || !destination.startsWith(workspace) ||
            !destination.getFileName().toString().endsWith(".mph")) {
            throw new IllegalArgumentException("save destination must be a child .mph file in the registered workspace");
        }
        Path parent = destination.getParent();
        if (parent == null || !Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS) ||
            Files.isSymbolicLink(parent) || !parent.toRealPath().equals(parent) ||
            Files.exists(destination, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalStateException("save destination parent is unsafe or destination already exists");
        }
        Map<String, Object> solutionState = requireNoStoredSolutionData(model);
        model.save(destination.toString());
        if (!Files.isRegularFile(destination, LinkOption.NOFOLLOW_LINKS) ||
            Files.isSymbolicLink(destination) || Files.size(destination) <= 0) {
            throw new IllegalStateException("COMSOL save returned without a new nonempty regular MPH file");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "SAVED_UNSOLVED_STATIC_SHAPE_MODEL");
        result.put("model_tag", model.tag());
        result.put("path", destination.toString());
        result.put("size_bytes", Files.size(destination));
        result.put("sha256", sha256(Files.readAllBytes(destination)));
        result.put("solution_data_present", false);
        result.put("solution_state_readback", solutionState);
        result.put("study_run_calls_this_action", 0);
        return result;
    }

    /**
     * Use documented SolverSequence emptiness/initialization readbacks.
     * getSize() throws Solution_not_initialized for an unsolved generated
     * sequence, so it is not a valid empty-state probe. Never mutate or clear
     * a solution to manufacture an "unsolved" status.
     */
    private static Map<String, Object> requireNoStoredSolutionData(Model model) {
        String[] tags = model.sol().tags();
        Arrays.sort(tags);
        if (tags.length == 0) {
            throw new IllegalStateException("generated study has no solver sequence to inspect");
        }
        List<Map<String, Object>> rows = new ArrayList<>();
        for (String tag : tags) {
            SolverSequence sequence = model.sol(tag);
            boolean solutionsEmpty = sequence.isEmpty();
            boolean solutionInitialized = sequence.isInitialized();
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("solver_sequence_tag", tag);
            row.put("readback_status", solutionsEmpty ? "EMPTY" : "NONEMPTY");
            row.put("solutions_empty", solutionsEmpty);
            row.put("solution_initialized", solutionInitialized);
            rows.add(row);
            if (!solutionsEmpty) {
                throw new IllegalStateException("existing solution data prevents the unsolved setup claim: " + tag);
            }
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "NO_STORED_SOLUTION_DATA");
        result.put("readback_method",
            "SolverSequence.isEmpty() [associated solution data]; SolverSequence.isInitialized() [solution initialization state]");
        result.put("solver_sequences", rows);
        return result;
    }

    private static SolverFeature uniqueTimeFeature(SolverSequence sequence) {
        Map<String, SolverFeature> matches = new LinkedHashMap<>();
        collectTimeFeatures(sequence.feature().tags(), sequence.feature(), matches);
        if (matches.size() != 1) throw new IllegalStateException("expected one saved Time solver feature");
        return matches.values().iterator().next();
    }

    private static void collectTimeFeatures(String[] tags, SolverFeatureList list,
                                           Map<String, SolverFeature> matches) {
        for (String tag : tags) {
            SolverFeature feature = list.get(tag);
            if ("Time".equals(feature.getType())) matches.put(tag, feature);
            String[] children = feature.feature().tags();
            if (children.length > 0) collectTimeFeatures(children, feature.feature(), matches);
        }
    }

    private static boolean contains(String[] values, String expected) {
        for (String value : values) if (expected.equals(value)) return true;
        return false;
    }

    private static int[] sortedUnion(int[] a, int[] b) {
        int[] merged = new int[a.length + b.length];
        System.arraycopy(a, 0, merged, 0, a.length);
        System.arraycopy(b, 0, merged, a.length, b.length);
        Arrays.sort(merged);
        int count = 0;
        for (int value : merged) {
            if (count == 0 || merged[count - 1] != value) merged[count++] = value;
        }
        return Arrays.copyOf(merged, count);
    }

    private static boolean sameIds(int[] a, int[] b) {
        int[] left = a.clone();
        int[] right = b.clone();
        Arrays.sort(left);
        Arrays.sort(right);
        return Arrays.equals(left, right);
    }

    private static int[] difference(int[] base, int[] removed) {
        List<Integer> result = new ArrayList<>();
        for (int value : base) {
            boolean found = false;
            for (int excluded : removed) if (value == excluded) found = true;
            if (!found) result.add(value);
        }
        int[] values = new int[result.size()];
        for (int index = 0; index < result.size(); index++) values[index] = result.get(index);
        return values;
    }

    private static boolean intersects(int[] a, int[] b) {
        for (int left : a) for (int right : b) if (left == right) return true;
        return false;
    }

    private static List<Integer> boxed(int[] values) {
        List<Integer> rows = new ArrayList<>();
        for (int value : values) rows.add(value);
        return rows;
    }

    private static Map<String, Object> parameter(Model model, String name, double expected,
                                                 String expectedUnit) {
        String expression = model.param().get(name);
        double value = model.param().evaluate(name);
        String unit = model.param().evaluateUnit(name).replace(" ", "");
        if (!Double.isFinite(value) || Math.abs(value - expected) > Math.max(1e-14, Math.abs(expected) * 1e-12) ||
            !expectedUnit.equals(unit)) {
            throw new IllegalStateException("saved parameter value/unit mismatch for " + name);
        }
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("expression", expression);
        row.put("value_si", value);
        row.put("unit", unit);
        return row;
    }

    private static String sha256(byte[] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder hex = new StringBuilder(digest.length * 2);
            for (byte value : digest) hex.append(String.format("%02x", value & 0xff));
            return hex.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("JVM lacks SHA-256", impossible);
        }
    }
}
