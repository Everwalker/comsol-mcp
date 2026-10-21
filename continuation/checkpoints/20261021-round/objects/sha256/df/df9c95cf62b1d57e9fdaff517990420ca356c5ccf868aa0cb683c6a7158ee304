import com.comsol.model.Coordsys;
import com.comsol.model.Model;
import com.comsol.model.ModelNode;
import com.comsol.model.physics.FeatureInfo;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Compile-only future probe for an already-existing managed COMSOL Model.
 *
 * This class has no main method, does not create a Model, does not start a
 * server, and does not run a study or solver. It only adds one Scaling feature
 * to an explicitly supplied Model and performs property/selection/Equation
 * View readback. Exact map readback alone is never reported as physical or
 * complex-coordinate acceptance.
 */
public final class NativeW23ScalingMapProbe {
    public static final String SCHEMA_ID = "urn:comsol-mcp:w23:scaling-map-probe:1.0.0";
    private NativeW23ScalingMapProbe() {}

    /**
     * Future authorized entrypoint. The caller must supply the current managed
     * model, actual domain IDs for exactly one frozen map region, and the
     * selected physics feature. This method never starts COMSOL or solves.
     */
    public static Map<String, Object> inspectExistingManagedModel(
            Model model, String componentTag, String geometryTag, String scalingTag,
            int[] expectedDomainIds, String[] expectedMap,
            String physicsTag, String physicsFeatureTag) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", SCHEMA_ID);
        result.put("probe_entrypoint", "inspectExistingManagedModel");
        result.put("model_start_requested", false);
        result.put("server_start_requested", false);
        result.put("study_or_solver_invoked", false);
        result.put("native_probe_status", "NOT_RUN_BY_SOFTWARE_STAGE");
        result.put("complex_map_support", "UNVERIFIED");
        result.put("physics_coordinate_transform", "UNVERIFIED");
        result.put("overall_acceptance", "UNVERIFIED");
        try {
            require(model != null, "managed Model instance is required");
            require(nonempty(componentTag) && nonempty(geometryTag) && nonempty(scalingTag),
                    "component, geometry, and unique Scaling tags are required");
            require(nonempty(physicsTag) && nonempty(physicsFeatureTag),
                    "exact physics and physics-feature tags are required");
            require(expectedDomainIds != null && expectedDomainIds.length > 0,
                    "actual selected domain IDs are required");
            requireUniquePositive(expectedDomainIds);
            require(expectedMap != null && expectedMap.length == 3,
                    "a three-coordinate Scaling map is required");
            require(Arrays.stream(expectedMap).allMatch(NativeW23ScalingMapProbe::nonempty),
                    "all Scaling map coordinates must be nonempty");
            require(Arrays.stream(expectedMap).anyMatch(value -> value.contains("1-i")),
                    "candidate map must retain its explicit complex stretch term");

            ModelNode component = model.component(componentTag);
            require(component != null, "requested component does not exist in the supplied Model");
            require(Arrays.stream(component.coordSystem().tags()).noneMatch(scalingTag::equals),
                    "refusing to replace an existing coordinate-system feature");
            Coordsys scaling = component.coordSystem().create(scalingTag, geometryTag, "Scaling");
            scaling.selection().set(expectedDomainIds);
            scaling.set("map", expectedMap);
            result.put("scaling_feature_type", scaling.getType());
            result.put("expected_domain_ids", asList(expectedDomainIds));
            result.put("expected_map_xyz", Arrays.asList(expectedMap.clone()));

            String[] mapReadback = scaling.getStringArray("map");
            int[] selectedDomains = scaling.selection().entities(3);
            Arrays.sort(selectedDomains);
            int[] expectedSorted = expectedDomainIds.clone();
            Arrays.sort(expectedSorted);
            boolean mapExact = Arrays.equals(expectedMap, mapReadback);
            boolean selectionExact = Arrays.equals(expectedSorted, selectedDomains);
            result.put("map_readback_xyz", mapReadback == null ? null : Arrays.asList(mapReadback));
            result.put("map_property_exact_readback", mapExact);
            result.put("selected_domain_ids_readback", asList(selectedDomains));
            result.put("domain_selection_exact_readback", selectionExact);
            if (!mapExact || !selectionExact) {
                result.put("native_probe_status", "FAIL_CLOSED_MAP_OR_SELECTION_READBACK_MISMATCH");
                result.put("complex_map_support", mapExact ? "PROPERTY_TEXT_PRESERVED_ONLY" : "REJECTED_OR_CHANGED");
                return result;
            }
            result.put("complex_map_support", "PROPERTY_TEXT_PRESERVED_NOT_PHYSICS_PROOF");

            Physics physics = component.physics(physicsTag);
            require(physics != null, "requested physics interface does not exist");
            PhysicsFeature physicsFeature = physics.feature(physicsFeatureTag);
            require(physicsFeature != null, "requested physics feature does not exist");
            int[] physicsDomains = physicsFeature.selection().entities(3);
            Arrays.sort(physicsDomains);
            result.put("physics_feature_type", physicsFeature.getType());
            result.put("physics_feature_domain_ids_readback", asList(physicsDomains));
            result.put("physics_domain_intersects_scaling_domain",
                    intersects(expectedSorted, physicsDomains));
            result.put("physics_domain_exactly_matches_scaling_domain",
                    Arrays.equals(expectedSorted, physicsDomains));

            FeatureInfo scalingInfo = scaling.featureInfo("info");
            FeatureInfo physicsInfo = physicsFeature.featureInfo("info");
            Map<String, Object> equationView = new LinkedHashMap<>();
            equationView.put("scaling_expressions", tableOrFailure(scalingInfo, "Expression"));
            equationView.put("scaling_shapes", tableOrFailure(scalingInfo, "Shape"));
            equationView.put("physics_expressions", tableOrFailure(physicsInfo, "Expression"));
            equationView.put("physics_weak_equations", tableOrFailure(physicsInfo, "Weak"));
            equationView.put("physics_constraints", tableOrFailure(physicsInfo, "Constraint"));
            equationView.put("collection_method", "FeatureInfo.getInfoTable(id, recursive, all)");
            equationView.put("interpretation", "RAW_READBACK_ONLY_REQUIRES_INDEPENDENT_REVIEW");
            result.put("equation_view", equationView);
            result.put("equation_view_captured", true);
            result.put("equation_view_proves_scaling_map_consumption", false);
            result.put("equation_view_proves_complex_map_survives", false);
            result.put("physics_coordinate_transform", "UNVERIFIED_REQUIRES_EQUATION_REVIEW");
            result.put("complex_map_support", "UNVERIFIED_PROPERTY_READBACK_ONLY");
            result.put("native_probe_status", "READBACK_CAPTURED_ACCEPTANCE_UNVERIFIED");
            return result;
        } catch (RuntimeException exception) {
            result.put("native_probe_status", "FAIL_CLOSED_PROBE_ERROR");
            result.put("failure_type", exception.getClass().getName());
            result.put("failure_message", exception.getMessage() == null ? "" : exception.getMessage());
            result.put("complex_map_support", "UNVERIFIED_OR_REJECTED");
            result.put("physics_coordinate_transform", "UNVERIFIED");
            result.put("overall_acceptance", "UNVERIFIED");
            return result;
        }
    }

    private static List<List<String>> tableOrFailure(FeatureInfo featureInfo, String id) {
        try {
            String[][] table = featureInfo.getInfoTable(id, "recursive", "all");
            List<List<String>> rows = new ArrayList<>();
            if (table != null) {
                for (String[] row : table)
                    rows.add(row == null ? new ArrayList<>() : Arrays.asList(row.clone()));
            }
            return rows;
        } catch (RuntimeException exception) {
            List<String> error = new ArrayList<>();
            error.add("READBACK_UNAVAILABLE");
            error.add(exception.getClass().getName());
            error.add(exception.getMessage() == null ? "" : exception.getMessage());
            return Arrays.asList(error);
        }
    }

    private static boolean intersects(int[] left, int[] right) {
        for (int value : left)
            for (int candidate : right)
                if (value == candidate) return true;
        return false;
    }

    private static List<Integer> asList(int[] values) {
        List<Integer> result = new ArrayList<>();
        for (int value : values) result.add(value);
        return result;
    }

    private static boolean nonempty(String value) {
        return value != null && !value.trim().isEmpty();
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalArgumentException(message);
    }

    private static void requireUniquePositive(int[] values) {
        for (int i = 0; i < values.length; i++) {
            require(values[i] > 0, "domain IDs must be positive");
            for (int j = i + 1; j < values.length; j++)
                require(values[i] != values[j], "duplicate domain IDs are forbidden");
        }
    }
}
