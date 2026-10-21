import com.comsol.model.Coordsys;
import com.comsol.model.GeomFeature;
import com.comsol.model.GeomMeasure;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.SelectionFeature;
import com.comsol.model.physics.PhysicsFeature;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Candidate native adapter for the W23 true-distance port transition.
 *
 * The method is called only through the managed trusted-code route.  It does
 * not run a study or solver.  Its result reports native geometry/PML readback
 * only; compilation or an offline recipe check is not COMSOL acceptance.
 */
public final class NativeW23PortTransitionV1 {
    private static final String SCHEMA = "urn:comsol-mcp:w23:port-transition-native-recipe:1.0.0";
    private static final String COMPONENT = "comp3d";
    private static final String GEOMETRY = "geom3d";
    private static final String PREFIX = "w23t1_";
    private static final double GEOM_TOL_UM = 2e-6;
    private static final double NORMAL_TOL = 2e-5;

    private NativeW23PortTransitionV1() {}

    public static Object run(Model model, Map<String, Object> arguments) {
        if (model == null || arguments == null)
            throw new IllegalArgumentException("model and adapter arguments are required");
        if (!"apply_transition_case".equals(arguments.get("phase")))
            throw new IllegalArgumentException("phase must be apply_transition_case");
        Map<String, Object> recipe = object(arguments.get("recipe"), "recipe");
        Map<String, Object> identity = object(arguments.get("managed_identity"), "managed_identity");
        String recipeSha = nonempty(arguments.get("recipe_sha256"), "recipe_sha256");
        validateRecipe(recipe, recipeSha);
        Map<String, Object> boundIdentity = validateModelBinding(model, identity);
        return apply(model, recipe, recipeSha, boundIdentity);
    }

    /** Offline structural contract usable without constructing or launching COMSOL. */
    public static Map<String, Object> validateRecipe(Map<String, Object> recipe,
                                                      String recipeSha256) {
        if (recipe == null || !SCHEMA.equals(recipe.get("schema_id")))
            throw new IllegalArgumentException("exact W23 transition V1 recipe schema is required");
        if (number(recipe.get("schema_version"), "schema_version") != 1)
            throw new IllegalArgumentException("unsupported W23 transition recipe version");
        nonempty(recipe.get("case_id"), "case_id");
        if (!"w23_full3d_fiber_ball_lens_vector_pml_v1".equals(recipe.get("source_fixture_id")))
            throw new IllegalArgumentException("recipe is not bound to the frozen W23 full-3D fixture");
        Map<String, Object> construction = object(recipe.get("construction"), "construction");
        if (!"CANDIDATE_NOT_RUN".equals(construction.get("native_support")))
            throw new IllegalArgumentException("recipe must not claim native support acceptance");
        if (!Boolean.FALSE.equals(construction.get("study_or_solver_invoked")))
            throw new IllegalArgumentException("transition adapter recipe must be solve-free");
        Map<String, Object> stretch = object(construction.get("pml_stretch"), "PML stretch");
        if (!"userDefined".equals(stretch.get("ScalingType"))
                || !"polynomial".equals(stretch.get("stretchingType"))
                || !"userDefined".equals(stretch.get("wavelengthSourceType"))
                || !"lambda0".equals(stretch.get("typicalWavelength"))
                || Double.compare(number(stretch.get("PMLfactor"), "PMLfactor"), 1.0) != 0
                || Double.compare(number(stretch.get("PMLgamma"), "PMLgamma"), 1.0) != 0)
            throw new IllegalArgumentException("native PML stretch differs from the frozen candidate profile");
        if (!recipeSha256.equals(recipe.get("recipe_sha256")))
            throw new IllegalArgumentException("managed recipe hash differs from embedded recipe hash");
        if (!"NativeW23PortTransitionV1.java".equals(recipe.get("java_source_artifact"))
                || !"NativeW23PortTransitionV1#run".equals(recipe.get("java_entrypoint")))
            throw new IllegalArgumentException("recipe is bound to a different Java adapter");
        Map<String, Object> port = object(recipe.get("port_contract"), "port_contract");
        if (!Boolean.TRUE.equals(port.get("full_air_aperture_required"))
                || !Boolean.TRUE.equals(port.get("capture_aperture_separate")))
            throw new IllegalArgumentException("full-air Port/capture separation contract is required");
        List<?> profiles = list(recipe.get("profiles"), "profiles");
        List<?> cells = list(recipe.get("region_cells"), "region_cells");
        List<?> groups = list(recipe.get("pml_groups"), "pml_groups");
        if (profiles.isEmpty() || cells.isEmpty() || groups.isEmpty())
            throw new IllegalArgumentException("native recipe has an empty profile/cell/PML closure");

        Map<String, Map<String, Object>> profileById = new LinkedHashMap<>();
        for (Object raw : profiles) {
            Map<String, Object> profile = object(raw, "profile row");
            String id = nonempty(profile.get("profile_id"), "profile_id");
            if (profileById.put(id, profile) != null)
                throw new IllegalArgumentException("duplicate exact profile id: " + id);
            List<?> primitives = list(profile.get("boundary_primitives"), "boundary_primitives");
            if (primitives.size() < 3)
                throw new IllegalArgumentException("closed profile has fewer than three primitives: " + id);
            verifyProfile(profile);
        }
        Set<String> regionIds = new LinkedHashSet<>();
        Set<String> usedProfiles = new LinkedHashSet<>();
        Set<Integer> allowedDirections = new HashSet<>(Arrays.asList(0, 1, 2, 3));
        int physicalCellCount = 0;
        for (Object raw : cells) {
            Map<String, Object> cell = object(raw, "region cell");
            String id = nonempty(cell.get("region_id"), "region_id");
            if (!regionIds.add(id)) throw new IllegalArgumentException("duplicate region_id: " + id);
            List<?> owner = list(cell.get("owner_tuple"), "owner_tuple");
            if (owner.size() != 3) throw new IllegalArgumentException("owner tuple must have three axes");
            List<?> profileIds = list(cell.get("profile_ids"), "profile_ids");
            if (profileIds.isEmpty()) throw new IllegalArgumentException("region has no exact profile");
            for (Object profileId : profileIds) {
                String key = nonempty(profileId, "profile_id reference");
                if (!profileById.containsKey(key))
                    throw new IllegalArgumentException("region references missing profile: " + key);
                usedProfiles.add(key);
            }
            int directions = integer(cell.get("expected_active_directions"), "active direction count");
            if (!allowedDirections.contains(directions))
                throw new IllegalArgumentException("PML direction count is outside the 0..3 contract");
            List<?> width = list(cell.get("width_interval_um"), "width interval");
            if (width.size() != 2 || !(finite(width.get(1)) > finite(width.get(0))))
                throw new IllegalArgumentException("width extrusion interval must be finite and increasing");
            if ("none".equals(owner.get(0)) && "none".equals(owner.get(1))
                    && "none".equals(owner.get(2))) physicalCellCount++;
        }
        if (!usedProfiles.equals(profileById.keySet()))
            throw new IllegalArgumentException("profile closure contains an unused or missing exact profile");
        if (physicalCellCount != 1)
            throw new IllegalArgumentException("recipe requires exactly one central physical owner tuple");

        Map<String, Integer> directionCountByRegion = new HashMap<>();
        for (Object raw : cells) {
            Map<String, Object> cell = object(raw, "region cell");
            directionCountByRegion.put(String.valueOf(cell.get("region_id")),
                    integer(cell.get("expected_active_directions"), "active direction count"));
        }
        Set<String> pmlRegionIds = new LinkedHashSet<>();
        for (Object raw : groups) {
            Map<String, Object> group = object(raw, "PML group");
            nonempty(group.get("group_id"), "PML group id");
            List<?> owners = list(group.get("region_ids"), "PML region_ids");
            List<?> directions = list(group.get("directions"), "PML directions");
            if (owners.isEmpty() || directions.isEmpty() || directions.size() > 3)
                throw new IllegalArgumentException("PML group has an invalid owner/direction cardinality");
            for (Object owner : owners) {
                String id = nonempty(owner, "PML region id");
                if (!regionIds.contains(id)) throw new IllegalArgumentException("PML group references unknown region");
                if (!pmlRegionIds.add(id)) throw new IllegalArgumentException("PML region is assigned more than once");
                if (directionCountByRegion.get(id) != directions.size())
                    throw new IllegalArgumentException("PML direction count differs from its owner tuple");
            }
        }
        if (pmlRegionIds.size() != cells.size() - physicalCellCount)
            throw new IllegalArgumentException("PML groups do not cover every nonphysical owner tuple exactly once");
        verifyPortContract(port, object(recipe.get("frame"), "frame"));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", SCHEMA);
        result.put("case_id", recipe.get("case_id"));
        result.put("profile_count", profileById.size());
        result.put("region_count", regionIds.size());
        result.put("pml_group_count", groups.size());
        result.put("contract_status", "STRUCTURE_VALID_NATIVE_NOT_RUN");
        return result;
    }

    private static void verifyProfile(Map<String, Object> profile) {
        nonempty(profile.get("role"), "profile role");
        List<?> primitives = list(profile.get("boundary_primitives"), "boundary primitives");
        List<double[]> starts = new ArrayList<>();
        List<double[]> ends = new ArrayList<>();
        double signedAreaTwice = 0.0;
        for (Object raw : primitives) {
            Map<String, Object> primitive = object(raw, "profile primitive");
            String kind = nonempty(primitive.get("kind"), "primitive kind");
            double[] start = vector(primitive.get("start_su_um"), 2, "primitive start");
            double[] end = vector(primitive.get("end_su_um"), 2, "primitive end");
            starts.add(start); ends.add(end);
            if (distance(start, end) <= 1e-13)
                throw new IllegalArgumentException("zero-length profile primitive");
            if ("line".equals(kind)) {
                signedAreaTwice += start[0] * end[1] - start[1] * end[0];
            } else if ("circular_arc".equals(kind)) {
                double[] center = vector(primitive.get("center_su_um"), 2, "arc center");
                double radius = finite(primitive.get("radius_um"));
                double angle0 = finite(primitive.get("angle_start_rad"));
                double angle1 = finite(primitive.get("angle_end_rad"));
                if (radius <= 0.0 || Math.abs(angle1 - angle0) <= 1e-13
                        || Math.abs(angle1 - angle0) >= Math.PI)
                    throw new IllegalArgumentException("arc is not one of the frozen short circular pieces");
                double[] computedStart = new double[]{center[0] + radius * Math.cos(angle0),
                        center[1] + radius * Math.sin(angle0)};
                double[] computedEnd = new double[]{center[0] + radius * Math.cos(angle1),
                        center[1] + radius * Math.sin(angle1)};
                if (distance(start, computedStart) > GEOM_TOL_UM
                        || distance(end, computedEnd) > GEOM_TOL_UM)
                    throw new IllegalArgumentException("arc endpoint data contradicts its center/radius/angles");
                signedAreaTwice += radius * (center[0] * (Math.sin(angle1) - Math.sin(angle0))
                        - center[1] * (Math.cos(angle1) - Math.cos(angle0)))
                        + radius * radius * (angle1 - angle0);
            } else {
                throw new IllegalArgumentException("unsupported exact profile primitive " + kind);
            }
        }
        double closure = 0.0;
        for (int i = 0; i < ends.size(); i++)
            closure = Math.max(closure, distance(ends.get(i), starts.get((i + 1) % starts.size())));
        double area = Math.abs(0.5 * signedAreaTwice);
        double claimedArea = finite(profile.get("exact_enclosed_area_um2"));
        double claimedClosure = finite(profile.get("closure_max_error_um"));
        if (closure > GEOM_TOL_UM || area <= 0.0
                || Math.abs(area - claimedArea) > Math.max(1e-10, area * 1e-10)
                || Math.abs(closure - claimedClosure) > GEOM_TOL_UM)
            throw new IllegalArgumentException("closed profile analytic area/closure differs from frozen primitive data");
    }

    private static void verifyPortContract(Map<String, Object> port, Map<String, Object> frame) {
        double[] a = vector(frame.get("a_axis_xyz"), 3, "a axis");
        double[] b = vector(frame.get("b_axis_xyz"), 3, "b axis");
        double[] w = vector(frame.get("w_axis_xyz"), 3, "w axis");
        requireOrthonormalRightHanded(a, b, w);
        verifyPortPolygon(port, true, new double[]{1.0, 0.0, 0.0},
                new double[]{0.0, 1.0, 0.0}, new double[]{0.0, 0.0, 1.0});
        verifyPortPolygon(port, false, a, b, w);
        double[] outputCenter = vector(port.get("output_center_global_xyz_um"), 3, "output center");
        double[] frameCenter = vector(frame.get("origin_xyz_um"), 3, "frame origin");
        if (Math.abs(dot(subtract(outputCenter, frameCenter), a)) > GEOM_TOL_UM)
            throw new IllegalArgumentException("output Port polygon center is not on the receiver output plane");
        if (!"portIn3d".equals(port.get("input_port_feature_tag"))
                || !"portOut3d".equals(port.get("output_port_feature_tag"))
                || !"sel3dInputPort".equals(port.get("input_selection_tag"))
                || !"sel3dOutputPort".equals(port.get("output_selection_tag"))
                || !"sel3dOutputCoreCapture".equals(port.get("capture_selection_tag")))
            throw new IllegalArgumentException("full-aperture face sets are not bound to the frozen Numeric Port/capture tags");
    }

    private static void verifyPortPolygon(Map<String, Object> port, boolean input,
            double[] normal, double[] axisU, double[] axisV) {
        String polygonKey = input ? "input_polygon_global_xyz_um" : "output_polygon_global_xyz_um";
        String centerKey = input ? "input_center_global_xyz_um" : "output_center_global_xyz_um";
        String normalKey = input ? "input_normal_axis_xyz" : "output_normal_axis_xyz";
        String areaKey = input ? "input_area_um2" : "output_area_um2";
        double[] center = vector(port.get(centerKey), 3, input ? "input center" : "output center");
        double[] declaredNormal = vector(port.get(normalKey), 3, "port normal");
        normalize(declaredNormal);
        if (Math.abs(Math.abs(dot(declaredNormal, normal)) - 1.0) > NORMAL_TOL)
            throw new IllegalArgumentException("Port normal does not match its frozen frame");
        double[][] vertices = vectorArray(port.get(polygonKey), 3, "Port aperture vertices");
        for (double[] vertex : vertices)
            if (Math.abs(dot(subtract(vertex, center), normal)) > GEOM_TOL_UM)
                throw new IllegalArgumentException("Port polygon vertex is not on its declared plane");
        if (Math.abs(dot(cross(axisU, axisV), normal) - 1.0) > NORMAL_TOL)
            throw new IllegalArgumentException("Port aperture coordinates are not right-handed with its normal");
        double[] half = apertureHalfWidths(vertices, center, axisU, axisV);
        double expectedArea = number(port.get(areaKey), "Port aperture area");
        double rectangleArea = 4.0 * half[0] * half[1];
        if (expectedArea <= 0.0 || Math.abs(expectedArea - rectangleArea) > expectedArea * 1e-10)
            throw new IllegalArgumentException("Port polygon area/widths differ from the frozen full aperture");
    }

    private static Map<String, Object> validateModelBinding(Model model, Map<String, Object> identity) {
        String projectId = nonempty(identity.get("project_id"), "project_id");
        String sessionId = nonempty(identity.get("session_id"), "session_id");
        String serverInstanceId = nonempty(identity.get("server_instance_id"), "server_instance_id");
        String modelTag = nonempty(identity.get("model_tag"), "model_tag");
        Object rawRef = identity.get("model_ref");
        if (!(rawRef instanceof Map) || ((Map<?, ?>) rawRef).isEmpty())
            throw new IllegalArgumentException("persisted managed ModelRef is required");
        if (!modelTag.equals(model.tag()))
            throw new IllegalStateException("Worker Model tag differs from the frozen managed ModelRef");
        Object revision = identity.get("expected_revision");
        long expectedRevision = (long) number(revision, "expected_revision");
        if (expectedRevision < 0 || expectedRevision != number(revision, "expected_revision"))
            throw new IllegalArgumentException("managed expected revision must be a nonnegative integer");
        Map<String, Object> ref = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : ((Map<?, ?>) rawRef).entrySet()) {
            if (!(entry.getKey() instanceof String)) throw new IllegalArgumentException("ModelRef keys must be strings");
            ref.put((String) entry.getKey(), entry.getValue());
        }
        if (integer(ref.get("schema_version"), "ModelRef schema_version") != 1
                || !sessionId.equals(ref.get("session_id"))
                || !serverInstanceId.equals(ref.get("server_instance_id"))
                || !modelTag.equals(ref.get("model_tag"))
                || integer(ref.get("generation"), "ModelRef generation") < 1)
            throw new IllegalArgumentException("ModelRef session/server/tag/generation differs from the exact managed identity");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("project_id", projectId);
        result.put("model_ref", ref);
        result.put("model_tag", modelTag);
        result.put("expected_revision", expectedRevision);
        return result;
    }

    private static Map<String, Object> apply(Model model, Map<String, Object> recipe,
            String recipeSha, Map<String, Object> identity) {
        String caseId = nonempty(recipe.get("case_id"), "case_id");
        requireFixture(model);
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        if (!"um".equals(geom.lengthUnit()))
            throw new IllegalStateException("fixture geometry length unit is not the frozen micrometre unit");
        for (String tag : geom.feature().tags())
            if (tag.startsWith(PREFIX))
                throw new IllegalStateException("V1 transition mutation already exists; refusing a second apply");

        Map<String, Object> construction = object(recipe.get("construction"), "construction");
        if (Boolean.TRUE.equals(construction.get("study_or_solver_invoked")))
            throw new IllegalArgumentException("transition recipe is not metadata-only");
        Map<String, Object> frame = object(recipe.get("frame"), "frame");
        List<?> origin = list(frame.get("origin_xyz_um"), "frame origin");
        double[] axisA = vector(frame.get("a_axis_xyz"), 3, "a axis");
        double[] axisB = vector(frame.get("b_axis_xyz"), 3, "b axis");
        double[] axisW = vector(frame.get("w_axis_xyz"), 3, "w axis");
        double[] center = vector(origin, 3, "frame origin");
        requireOrthonormalRightHanded(axisA, axisB, axisW);
        Map<String, Object> sourcePortReadback = verifyCurrentReceiverPortTransform(model, recipe);

        // Remove only the obsolete six-plane envelope/result operations.  The
        // optical source primitives and capture selector remain untouched.
        removeIfPresent(geom.feature(), "allDomains", "pmlShell", "airRemainder", "innerBox", "outerBox");
        if (model.component(COMPONENT).coordSystem().hasTag("pmlYZ"))
            model.component(COMPONENT).coordSystem().remove("pmlYZ");

        List<?> profileRows = list(recipe.get("profiles"), "profiles");
        List<?> cellRows = list(recipe.get("region_cells"), "region_cells");
        List<?> pmlRows = list(recipe.get("pml_groups"), "pml_groups");
        Map<String, Map<String, Object>> profiles = new LinkedHashMap<>();
        for (Object raw : profileRows) {
            Map<String, Object> profile = object(raw, "profile");
            profiles.put(nonempty(profile.get("profile_id"), "profile_id"), profile);
        }
        Map<String, String> regionFeatureById = new LinkedHashMap<>();
        int cellIndex = 0;
        for (Object rawCell : cellRows) {
            Map<String, Object> cell = object(rawCell, "region cell");
            String regionId = nonempty(cell.get("region_id"), "region_id");
            String regionFeature = String.format("%sregion%03d", PREFIX, cellIndex++);
            List<String> extrusionTags = new ArrayList<>();
            List<?> profileIds = list(cell.get("profile_ids"), "profile_ids");
            List<?> width = list(cell.get("width_interval_um"), "width interval");
            double w0 = finite(width.get(0));
            double w1 = finite(width.get(1));
            int profileIndex = 0;
            for (Object rawId : profileIds) {
                String profileId = nonempty(rawId, "profile reference");
                Map<String, Object> profile = profiles.get(profileId);
                if (profile == null) throw new IllegalArgumentException("region references absent profile " + profileId);
                String extrusionTag = String.format("%svolume%03d_%02d", PREFIX, cellIndex - 1, profileIndex++);
                String workPlaneTag = String.format("%splane%03d_%02d", PREFIX, cellIndex - 1, profileIndex - 1);
                createExtrudedProfile(geom, profile, workPlaneTag, extrusionTag,
                        center, axisA, axisB, axisW, w0, w1);
                extrusionTags.add(extrusionTag);
            }
            createUnion(geom, regionFeature, extrusionTags.toArray(new String[0]));
            regionFeatureById.put(regionId, regionFeature);
        }

        List<String> pmlGroupFeatureTags = new ArrayList<>();
        Map<String, Map<String, Object>> pmlGroupById = new LinkedHashMap<>();
        int groupIndex = 0;
        for (Object rawGroup : pmlRows) {
            Map<String, Object> group = object(rawGroup, "PML group");
            String groupId = nonempty(group.get("group_id"), "PML group id");
            String featureTag = String.format("%spmlgroup%03d", PREFIX, groupIndex++);
            List<String> inputs = new ArrayList<>();
            for (Object rawRegion : list(group.get("region_ids"), "PML group region_ids")) {
                String regionFeature = regionFeatureById.get(nonempty(rawRegion, "region id"));
                if (regionFeature == null) throw new IllegalArgumentException("PML group owns unknown region");
                inputs.add(regionFeature);
            }
            createUnion(geom, featureTag, inputs.toArray(new String[0]));
            pmlGroupFeatureTags.add(featureTag);
            pmlGroupById.put(groupId, group);
        }

        List<String> physicalInputs = new ArrayList<>();
        int pmlRegionCount = 0;
        for (Object rawCell : cellRows) {
            Map<String, Object> cell = object(rawCell, "region cell");
            List<?> owners = list(cell.get("owner_tuple"), "owner tuple");
            String regionFeature = regionFeatureById.get(String.valueOf(cell.get("region_id")));
            boolean physical = "none".equals(owners.get(0)) && "none".equals(owners.get(1))
                    && "none".equals(owners.get(2));
            if (physical) physicalInputs.add(regionFeature);
            else pmlRegionCount++;
        }
        if (physicalInputs.size() != 1 || pmlRegionCount == 0)
            throw new IllegalStateException("native geometry requires a single central physical cell and PML cells");

        geom.create("airRemainder", "Difference");
        geom.feature("airRemainder").selection("input").set(new String[]{physicalInputs.get(0)});
        geom.feature("airRemainder").selection("input2").set(new String[]{"solidOptics"});
        geom.feature("airRemainder").set("keepsubtract", "on");
        resultSelection(geom, "airRemainder");

        geom.create("pmlShell", "Union");
        geom.feature("pmlShell").selection("input").set(pmlGroupFeatureTags.toArray(new String[0]));
        geom.feature("pmlShell").set("intbnd", "on");
        geom.feature("pmlShell").set("keep", "on");
        resultSelection(geom, "pmlShell");

        geom.create("allDomains", "Union");
        geom.feature("allDomains").selection("input").set(
                new String[]{"airRemainder", "solidOptics", "pmlShell"});
        geom.feature("allDomains").set("intbnd", "on");
        geom.feature("allDomains").set("keep", "on");
        resultSelection(geom, "allDomains");
        geom.run();
        if (geom.getNDomains() <= 0 || geom.isAssembly())
            throw new IllegalStateException("transition geometry did not produce a unioned domain partition");

        Map<String, int[]> regionDomains = new LinkedHashMap<>();
        Set<Integer> allOwnedRegionDomains = new LinkedHashSet<>();
        for (Map.Entry<String, String> entry : regionFeatureById.entrySet()) {
            int[] ids = generatedDomainIds(model, entry.getValue());
            if (ids.length == 0) throw new IllegalStateException("empty native owner tuple selection: " + entry.getKey());
            for (int id : ids)
                if (!allOwnedRegionDomains.add(id))
                    throw new IllegalStateException("two exact owner tuples map to the same native domain id");
            regionDomains.put(entry.getKey(), ids);
        }
        int[] pmlShellIds = generatedDomainIds(model, "pmlShell");
        int[] airIds = generatedDomainIds(model, "airRemainder");
        int[] allDomainIds = generatedDomainIds(model, "allDomains");
        if (pmlShellIds.length == 0 || airIds.length == 0)
            throw new IllegalStateException("native PML/physical-air domain readback is empty");
        if (allDomainIds.length != geom.getNDomains())
            throw new IllegalStateException("allDomains result selection does not enumerate every actual native domain");
        assertDisjoint(pmlShellIds, airIds);

        List<Map<String, Object>> pmlReadbacks = new ArrayList<>();
        Set<Integer> groupedPmlDomainIds = new LinkedHashSet<>();
        int pmlIndex = 0;
        for (Object rawGroup : pmlRows) {
            Map<String, Object> group = object(rawGroup, "PML group");
            String groupId = nonempty(group.get("group_id"), "PML group id");
            List<Integer> domainIds = new ArrayList<>();
            for (Object rawRegion : list(group.get("region_ids"), "PML group region_ids"))
                for (int id : regionDomains.get(nonempty(rawRegion, "PML region id"))) domainIds.add(id);
            int[] ids = uniqueIds(domainIds);
            if (ids.length == 0) throw new IllegalStateException("PML distance group has no native domains");
            for (int id : ids)
                if (!groupedPmlDomainIds.add(id))
                    throw new IllegalStateException("two distance signatures select the same native PML domain");
            String nodeTag = String.format("%spml%03d", PREFIX, pmlIndex++);
            Map<String, Object> pml = configurePml(model, nodeTag, ids,
                    list(group.get("directions"), "PML directions"));
            pml.put("group_id", groupId);
            pml.put("region_ids", new ArrayList<>(list(group.get("region_ids"), "PML region ids")));
            pmlReadbacks.add(pml);
        }
        if (!sameSet(pmlShellIds, groupedPmlDomainIds.stream().mapToInt(Integer::intValue).toArray()))
            throw new IllegalStateException("native PML groups do not cover pmlShell domain IDs exactly");

        Map<String, Object> portContract = object(recipe.get("port_contract"), "port contract");
        Map<String, Object> inputPort = configurePortSelection(model, portContract, frame, true);
        Map<String, Object> outputPort = configurePortSelection(model, portContract, frame, false);
        SelectionFeature capture = model.component(COMPONENT).selection("sel3dOutputCoreCapture");
        if (!"Cylinder".equals(capture.getType()) || capture.entities(2).length == 0)
            throw new IllegalStateException("independent output core-capture selection changed or is empty");
        verifyPortBinding(model, "portIn3d", "sel3dInputPort", inputPort);
        verifyPortBinding(model, "portOut3d", "sel3dOutputPort", outputPort);

        Map<String, Object> output = new LinkedHashMap<>();
        output.put("schema_id", "urn:comsol-mcp:w23:port-transition-native-readback:1.0.0");
        output.put("case_id", caseId);
        output.put("recipe_sha256", recipeSha);
        output.put("source_plan_sha256", recipe.get("source_plan_sha256"));
        output.put("managed_identity", identity);
        output.put("geometry", Map.of("tag", GEOMETRY, "length_unit", geom.lengthUnit(),
                "domain_count", geom.getNDomains(), "is_assembly", geom.isAssembly(),
                "all_domain_ids", intList(allDomainIds),
                "air_domain_ids", intList(airIds), "pml_domain_ids", intList(pmlShellIds),
                "owner_tuple_domain_ids", intMap(regionDomains)));
        output.put("pml_coordinate_systems", pmlReadbacks);
        output.put("source_case_receiver_port_readback", sourcePortReadback);
        output.put("input_port", inputPort);
        output.put("output_port", outputPort);
        output.put("capture_selection", Map.of("tag", capture.tag(), "type", capture.getType(),
                "entity_ids", intList(capture.entities(2)), "mutated", false));
        output.put("native_result", "NATIVE_GEOMETRY_AND_PML_READBACK_ONLY");
        output.put("geometry_compatibility", "UNVERIFIED");
        output.put("pml_compatibility", "UNVERIFIED");
        output.put("physical_result", "NOT_RUN");
        output.put("study_or_solver_invoked", false);
        return output;
    }

    private static void requireFixture(Model model) {
        if (!contains(model.component().tags(), COMPONENT)
                || !contains(model.component(COMPONENT).geom().tags(), GEOMETRY))
            throw new IllegalStateException("source-owned W23 full-3D fixture component/geometry is missing");
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        for (String tag : new String[]{"coreIn", "cladShellIn", "rotCoreOutZ", "rotCladShellOutZ",
                "lensBall", "solidOptics", "outerBox", "innerBox", "airRemainder", "pmlShell", "allDomains"})
            if (!geom.feature().hasTag(tag))
                throw new IllegalStateException("source-owned fixture geometry feature is missing: " + tag);
        for (String tag : new String[]{"sel3dInputPort", "sel3dOutputPort", "sel3dOutputCoreCapture"})
            if (!model.component(COMPONENT).selection().hasTag(tag))
                throw new IllegalStateException("source-owned fixture selection is missing: " + tag);
        for (String tag : new String[]{"portIn3d", "portOut3d"})
            if (!model.component(COMPONENT).physics().hasTag("ewfd")
                    || !model.component(COMPONENT).physics("ewfd").feature().hasTag(tag)
                    || !"Port".equals(model.component(COMPONENT).physics("ewfd").feature(tag).getType()))
                throw new IllegalStateException("source-owned numeric Port feature is missing: " + tag);
    }

    private static Map<String, Object> verifyCurrentReceiverPortTransform(Model model,
                                                                            Map<String, Object> recipe) {
        Map<String, Object> transform = object(recipe.get("receiver_transform"), "receiver_transform");
        Map<String, Object> frame = object(recipe.get("frame"), "frame");
        if (!"right-handed global +y then +z".equals(transform.get("rotation_order")))
            throw new IllegalArgumentException("receiver transform rotation order is unsupported");
        double[] expectedAxis = vector(transform.get("axis_xyz"), 3, "receiver axis");
        double[] expectedCenter = vector(transform.get("center_xyz_um"), 3, "receiver center");
        if (distance(expectedAxis, vector(frame.get("a_axis_xyz"), 3, "frame axis")) > NORMAL_TOL
                || distance(expectedCenter, vector(frame.get("origin_xyz_um"), 3, "frame origin")) > GEOM_TOL_UM)
            throw new IllegalArgumentException("recipe output plane differs from its receiver transform");
        double claddingRadius = number(transform.get("cladding_radius_um"), "receiver cladding radius");
        double halfLength = number(transform.get("selection_half_length_um"), "receiver section half-length");
        double margin = number(transform.get("selection_radial_margin_um"), "receiver section margin");
        if (claddingRadius <= 0.0 || halfLength <= 0.0 || margin < 0.0)
            throw new IllegalArgumentException("receiver output-section parameters are invalid");

        SelectionFeature selection = model.component(COMPONENT).selection("sel3dOutputPort");
        if (!"Cylinder".equals(selection.getType()) || selection.getInt("entitydim") != 2
                || !"inside".equals(selection.getString("condition"))
                || !"cartesian".equals(selection.getString("axistype"))
                || Math.abs(selection.getDouble("bottom")) > 1e-12
                || Math.abs(selection.getDouble("top") - 2.0 * halfLength) > 1e-10
                || Math.abs(selection.getDouble("r") - claddingRadius - margin) > 1e-10)
            throw new IllegalStateException("source fixture output section is not the exact registered cylinder");
        double[] axis = selection.getDoubleArray("axis");
        double[] base = selection.getDoubleArray("pos");
        if (axis == null || base == null || axis.length != 3 || base.length != 3
                || distance(axis, expectedAxis) > NORMAL_TOL)
            throw new IllegalStateException("source fixture output section axis differs from the frozen receiver transform");
        double[] center = new double[3];
        for (int i = 0; i < 3; i++) center[i] = base[i] + halfLength * axis[i];
        if (distance(center, expectedCenter) > GEOM_TOL_UM)
            throw new IllegalStateException("source fixture output section center differs from the frozen receiver transform");
        int[] faces = selection.entities(2);
        if (faces == null || faces.length == 0)
            throw new IllegalStateException("source fixture output section selects no actual native face IDs");
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        List<Map<String, Object>> facts = new ArrayList<>();
        for (int face : faces) {
            double[] range = geom.faceParamRange(face);
            if (range == null || range.length != 4)
                throw new IllegalStateException("source output section face has a malformed native parameter range");
            double[][] parameters = new double[9][2];
            int k = 0;
            for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) {
                parameters[k][0] = range[0] + (range[1] - range[0]) * i / 2.0;
                parameters[k][1] = range[2] + (range[3] - range[2]) * j / 2.0;
                k++;
            }
            double[][] points = geom.faceX(face, parameters);
            double[][] normals = geom.faceNormal(face, parameters);
            if (points == null || points.length != parameters.length || normals == null
                    || normals.length != parameters.length)
                throw new IllegalStateException("source output section face readback is malformed");
            for (int i = 0; i < points.length; i++) {
                if (points[i] == null || points[i].length != 3 || normals[i] == null || normals[i].length != 3)
                    throw new IllegalStateException("source output section point/normal tuple is malformed");
                double[] delta = subtract(points[i], center);
                double axial = dot(delta, axis);
                double radial2 = dot(delta, delta) - axial * axial;
                double norm = Math.sqrt(dot(normals[i], normals[i]));
                if (Math.abs(axial) > GEOM_TOL_UM || radial2 < -GEOM_TOL_UM
                        || radial2 > (claddingRadius + margin + GEOM_TOL_UM) * (claddingRadius + margin + GEOM_TOL_UM)
                        || !Double.isFinite(norm) || norm <= 0.0
                        || Math.abs(Math.abs(dot(normals[i], axis) / norm) - 1.0) > NORMAL_TOL)
                    throw new IllegalStateException("actual source output faces contradict the case receiver plane/cylinder");
            }
            facts.add(Map.of("boundary_id", face, "samples", points.length,
                    "plane", "MATCH", "normal_parallel_to_receiver_axis", true));
        }
        GeomMeasure measure = geom.measure();
        measure.selection().init(2);
        measure.selection().set("allDomains", faces);
        double area = measure.getArea();
        double expectedArea = Math.PI * (claddingRadius + margin) * (claddingRadius + margin);
        double relativeError = Math.abs(area - expectedArea) / expectedArea;
        if (!Double.isFinite(area) || relativeError > 0.03)
            throw new IllegalStateException("actual source output face area differs from its registered receiver section");
        return Map.of("selection_tag", selection.tag(), "face_ids", intList(faces),
                "axis_xyz", boxed(axis), "center_xyz_um", boxed(center),
                "approximate_face_area", area, "expected_section_area", expectedArea,
                "relative_area_error", relativeError, "face_readback", facts,
                "readback_scope", "CURRENT_NATIVE_FIXTURE_CASE_PRECONDITION");
    }

    private static void createExtrudedProfile(GeomSequence geom, Map<String, Object> profile,
            String workPlaneTag, String extrusionTag, double[] center, double[] a, double[] b,
            double[] w, double w0, double w1) {
        List<?> primitives = list(profile.get("boundary_primitives"), "profile primitives");
        geom.create(workPlaneTag, "WorkPlane");
        GeomFeature workPlane = geom.feature(workPlaneTag);
        workPlane.set("planetype", "coordinates");
        double[] planeOrigin = add(center, scale(w, w0));
        workPlane.set("genpoints", new double[][]{
                planeOrigin, add(planeOrigin, a), add(planeOrigin, b)});
        com.comsol.model.GeomSequence local = workPlane.geom();
        List<String> children = new ArrayList<>();
        List<double[]> lineChain = new ArrayList<>();
        int partIndex = 0;
        for (Object rawPrimitive : primitives) {
            Map<String, Object> primitive = object(rawPrimitive, "profile primitive");
            String kind = nonempty(primitive.get("kind"), "primitive kind");
            if ("line".equals(kind)) {
                double[] start = vector(primitive.get("start_su_um"), 2, "line start");
                double[] end = vector(primitive.get("end_su_um"), 2, "line end");
                if (lineChain.isEmpty()) lineChain.add(start);
                else if (distance(lineChain.get(lineChain.size() - 1), start) > GEOM_TOL_UM)
                    throw new IllegalArgumentException("disconnected consecutive line primitives");
                lineChain.add(end);
            } else if ("circular_arc".equals(kind)) {
                flushLineChain(local, children, lineChain, workPlaneTag, partIndex++);
                GeomFeature arc = local.create(workPlaneTag + "_arc" + partIndex, "CircularArc");
                double[] arcCenter = vector(primitive.get("center_su_um"), 2, "arc center");
                double radius = finite(primitive.get("radius_um"));
                double angle1 = finite(primitive.get("angle_start_deg"));
                double angle2 = finite(primitive.get("angle_end_deg"));
                if (radius <= 0.0 || Math.abs(angle2 - angle1) <= 1e-12)
                    throw new IllegalArgumentException("invalid exact circular arc in native recipe");
                arc.set("specify", "center");
                arc.set("center", arcCenter);
                arc.set("r", radius);
                arc.set("angle1", angle1);
                arc.set("angle2", angle2);
                arc.set("clockwise", angle2 < angle1 ? "on" : "off");
                arc.set("shortarc", "on");
                arc.set("type", "curve");
                children.add(arc.tag());
                double[] declaredStart = vector(primitive.get("start_su_um"), 2, "arc start");
                double[] declaredEnd = vector(primitive.get("end_su_um"), 2, "arc end");
                double[] computedStart = new double[]{arcCenter[0] + radius * Math.cos(Math.toRadians(angle1)),
                        arcCenter[1] + radius * Math.sin(Math.toRadians(angle1))};
                double[] computedEnd = new double[]{arcCenter[0] + radius * Math.cos(Math.toRadians(angle2)),
                        arcCenter[1] + radius * Math.sin(Math.toRadians(angle2))};
                if (distance(computedStart, declaredStart) > GEOM_TOL_UM
                        || distance(computedEnd, declaredEnd) > GEOM_TOL_UM)
                    throw new IllegalArgumentException("arc endpoints differ from frozen primitive geometry");
            } else {
                throw new IllegalArgumentException("unsupported exact profile primitive: " + kind);
            }
        }
        flushLineChain(local, children, lineChain, workPlaneTag, partIndex);
        if (children.isEmpty()) throw new IllegalArgumentException("profile has no exact curve primitives");
        String solidTag;
        if (children.size() == 1 && "Polygon".equals(local.feature(children.get(0)).getType())) {
            GeomFeature only = local.feature(children.get(0));
            only.set("type", "solid");
            solidTag = only.tag();
        } else {
            solidTag = local.feature().compositeCurves(children.toArray(new String[0]));
            local.feature(solidTag).set("type", "solid");
        }
        local.run();
        if (!local.feature().hasTag(solidTag))
            throw new IllegalStateException("exact closed profile did not produce a native 2-D solid feature");
        GeomFeature extrusion = geom.create(extrusionTag, "Extrude");
        extrusion.set("extrudefrom", "workplane");
        extrusion.set("workplane", workPlaneTag);
        extrusion.set("distance", w1 - w0);
        resultSelection(geom, extrusionTag);
    }

    private static void flushLineChain(com.comsol.model.GeomSequence local, List<String> children,
            List<double[]> lineChain, String tag, int index) {
        if (lineChain.isEmpty()) return;
        // Polygon(type=solid) closes its final vertex to the first one.  The
        // frozen line loops also carry an explicit final endpoint, so omit
        // that repeated endpoint instead of creating a zero-length edge.
        if (lineChain.size() >= 4
                && distance(lineChain.get(0), lineChain.get(lineChain.size() - 1)) <= GEOM_TOL_UM)
            lineChain.remove(lineChain.size() - 1);
        if (lineChain.size() < 2) throw new IllegalArgumentException("open line chain has fewer than two points");
        double[] x = new double[lineChain.size()];
        double[] y = new double[lineChain.size()];
        for (int i = 0; i < lineChain.size(); i++) {
            x[i] = lineChain.get(i)[0];
            y[i] = lineChain.get(i)[1];
        }
        GeomFeature polygon = local.create(tag + "_line" + index, "Polygon");
        polygon.set("type", "open");
        polygon.set("x", x);
        polygon.set("y", y);
        children.add(polygon.tag());
        lineChain.clear();
    }

    private static void createUnion(GeomSequence geom, String tag, String[] inputs) {
        if (inputs == null || inputs.length == 0)
            throw new IllegalArgumentException("a geometry union must own at least one input feature");
        geom.create(tag, "Union");
        geom.feature(tag).selection("input").set(inputs);
        geom.feature(tag).set("intbnd", "on");
        geom.feature(tag).set("keep", "on");
        resultSelection(geom, tag);
    }

    private static Map<String, Object> configurePml(Model model, String tag, int[] domainIds,
                                                       List<?> directions) {
        if (directions.isEmpty() || directions.size() > 3)
            throw new IllegalArgumentException("userDefined PML needs one to three exact distance directions");
        Coordsys pml = model.component(COMPONENT).coordSystem().create(tag, GEOMETRY, "PML");
        pml.selection().set(domainIds);
        pml.set("ScalingType", "userDefined");
        pml.set("stretchingType", "polynomial");
        pml.set("wavelengthSourceType", "userDefined");
        pml.set("typicalWavelength", "lambda0");
        pml.set("PMLfactor", 1.0);
        pml.set("PMLgamma", 1.0);
        pml.set("directions", directions.size());
        List<Map<String, Object>> readbackDirections = new ArrayList<>();
        List<Map<String, String>> expectedDirections = new ArrayList<>();
        for (int i = 0; i < directions.size(); i++) {
            Map<String, Object> direction = object(directions.get(i), "PML direction");
            String d = nonempty(direction.get("distance_expression"), "PML distance expression");
            String dmax = nonempty(direction.get("dmax_expression"), "PML dmax expression");
            String owner = nonempty(direction.get("owner"), "direction owner");
            pml.setIndex("d", d, i);
            pml.setIndex("dmax", dmax, i);
            String actualD = pml.getString("d", i);
            String actualDmax = pml.getString("dmax", i);
            expectedDirections.add(Map.of("owner", owner, "distance_expression", d, "dmax_expression", dmax));
            readbackDirections.add(Map.of("owner", owner,
                    "distance_expression", actualD, "dmax_expression", actualDmax));
        }
        int[] actual = pml.selection().entities(3);
        if (!sameSet(domainIds, actual)
                || !"userDefined".equals(pml.getString("ScalingType"))
                || !"polynomial".equals(pml.getString("stretchingType"))
                || !"userDefined".equals(pml.getString("wavelengthSourceType"))
                || !"lambda0".equals(pml.getString("typicalWavelength"))
                || pml.getInt("directions") != directions.size()
                || Double.compare(pml.getDouble("PMLfactor"), 1.0) != 0
                || Double.compare(pml.getDouble("PMLgamma"), 1.0) != 0)
            throw new IllegalStateException("native PML feature properties or domain IDs changed on readback");
        List<Map<String, Object>> actualDirections = new ArrayList<>();
        for (int i = 0; i < expectedDirections.size(); i++) {
            Map<String, String> expected = expectedDirections.get(i);
            actualDirections.add(Map.of("owner", expected.get("owner"),
                    "distance_expression", pml.getString("d", i),
                    "dmax_expression", pml.getString("dmax", i)));
        }
        verifyPmlDirectionReadback(new ArrayList<>(expectedDirections), actualDirections);
        readbackDirections.clear();
        readbackDirections.addAll(actualDirections);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("node_tag", tag);
        result.put("domain_ids", intList(actual));
        result.put("ScalingType", pml.getString("ScalingType"));
        result.put("stretchingType", pml.getString("stretchingType"));
        result.put("wavelengthSourceType", pml.getString("wavelengthSourceType"));
        result.put("typicalWavelength", pml.getString("typicalWavelength"));
        result.put("directions", readbackDirections);
        result.put("property_readback", "MATCH");
        return result;
    }

    /** Fail closed unless the native PML getters match each frozen direction row. */
    public static void verifyPmlDirectionReadback(List<?> expectedRows, List<?> actualRows) {
        if (expectedRows == null || actualRows == null || expectedRows.size() != actualRows.size())
            throw new IllegalArgumentException("PML direction readback count differs from recipe");
        for (int i = 0; i < expectedRows.size(); i++) {
            Map<String, Object> expected = object(expectedRows.get(i), "expected PML direction");
            Map<String, Object> actual = object(actualRows.get(i), "actual PML direction");
            for (String field : new String[]{"owner", "distance_expression", "dmax_expression"}) {
                String wanted = nonempty(expected.get(field), "expected PML " + field);
                String observed = nonempty(actual.get(field), "actual PML " + field);
                if (!wanted.equals(observed))
                    throw new IllegalArgumentException("native PML readback differs from recipe for " + field);
            }
        }
    }

    private static Map<String, Object> configurePortSelection(Model model,
            Map<String, Object> contract, Map<String, Object> frame, boolean input) {
        String selectionTag = nonempty(contract.get(input ? "input_selection_tag" : "output_selection_tag"),
                "port selection tag");
        String portTag = nonempty(contract.get(input ? "input_port_feature_tag" : "output_port_feature_tag"),
                "physics Port tag");
        double[] center = vector(contract.get(input ? "input_center_global_xyz_um" : "output_center_global_xyz_um"),
                3, "port center");
        double[] normal = vector(contract.get(input ? "input_normal_axis_xyz" : "output_normal_axis_xyz"),
                3, "port normal");
        normalize(normal);
        double[] axisU = input ? new double[]{0.0, 1.0, 0.0}
                : vector(frame.get("b_axis_xyz"), 3, "output Port b axis");
        double[] axisV = input ? new double[]{0.0, 0.0, 1.0}
                : vector(frame.get("w_axis_xyz"), 3, "output Port w axis");
        double[][] vertices = vectorArray(contract.get(input ? "input_polygon_global_xyz_um"
                : "output_polygon_global_xyz_um"), 3, "Port aperture vertices");
        double[] half = apertureHalfWidths(vertices, center, axisU, axisV);
        double expectedArea = number(contract.get(input ? "input_area_um2" : "output_area_um2"),
                "Port aperture area");
        Map<String, Object> measured = findExactPortFaces(model, center, normal, axisU, axisV,
                half[0], half[1], expectedArea);
        int[] boundaryIds = intArray(measured.get("boundary_ids"));
        if (model.component(COMPONENT).selection().hasTag(selectionTag))
            model.component(COMPONENT).selection().remove(selectionTag);
        SelectionFeature explicit = model.component(COMPONENT).selection().create(selectionTag, "Explicit");
        explicit.geom(GEOMETRY);
        explicit.set("entitydim", 2);
        explicit.set(boundaryIds);
        if (explicit.getInt("entitydim") != 2 || !GEOMETRY.equals(explicit.geom())
                || !sameSet(boundaryIds, explicit.entities(2)))
            throw new IllegalStateException("explicit full-air Port boundary IDs changed on selection readback");
        PhysicsFeature port = model.component(COMPONENT).physics("ewfd").feature(portTag);
        port.selection().named(selectionTag);
        if (!selectionTag.equals(port.selection().named()) || !sameSet(boundaryIds, port.selection().entities(2)))
            throw new IllegalStateException("Numeric Port binding changed after exact face selection update");
        measured.put("selection_tag", selectionTag);
        measured.put("physics_port_tag", portTag);
        measured.put("boundary_ids", intList(boundaryIds));
        measured.put("entity_dimension", 2);
        measured.put("port_binding_status", "READBACK_MATCH");
        return measured;
    }

    private static Map<String, Object> findExactPortFaces(Model model, double[] center,
            double[] normal, double[] axisU, double[] axisV, double halfU, double halfV,
            double expectedArea) {
        GeomSequence geom = model.component(COMPONENT).geom(GEOMETRY);
        if (geom.getNBoundaries() <= 0) throw new IllegalStateException("native geometry has no boundary entities");
        List<Integer> candidateIds = new ArrayList<>();
        List<Map<String, Object>> faceFacts = new ArrayList<>();
        for (int boundaryId = 1; boundaryId <= geom.getNBoundaries(); boundaryId++) {
            double[] range = geom.faceParamRange(boundaryId);
            if (range == null || range.length != 4) continue;
            double[][] parameters = new double[25][2];
            int sample = 0;
            for (int iu = 0; iu < 5; iu++) for (int iv = 0; iv < 5; iv++) {
                parameters[sample][0] = range[0] + (range[1] - range[0]) * iu / 4.0;
                parameters[sample][1] = range[2] + (range[3] - range[2]) * iv / 4.0;
                sample++;
            }
            double[][] points = geom.faceX(boundaryId, parameters);
            double[][] normals = geom.faceNormal(boundaryId, parameters);
            if (points == null || points.length != parameters.length || normals == null
                    || normals.length != parameters.length) continue;
            boolean entireFaceInAperture = true;
            for (int i = 0; i < points.length; i++) {
                if (points[i] == null || points[i].length != 3 || normals[i] == null || normals[i].length != 3) {
                    entireFaceInAperture = false; break;
                }
                double[] delta = subtract(points[i], center);
                double planeDistance = Math.abs(dot(delta, normal));
                double u = dot(delta, axisU), v = dot(delta, axisV);
                if (planeDistance > GEOM_TOL_UM || Math.abs(u) > halfU + GEOM_TOL_UM
                        || Math.abs(v) > halfV + GEOM_TOL_UM) {
                    entireFaceInAperture = false; break;
                }
                double nNorm = Math.sqrt(dot(normals[i], normals[i]));
                if (!Double.isFinite(nNorm) || nNorm == 0.0
                        || Math.abs(Math.abs(dot(normals[i], normal) / nNorm) - 1.0) > NORMAL_TOL) {
                    entireFaceInAperture = false; break;
                }
            }
            if (entireFaceInAperture) {
                candidateIds.add(boundaryId);
                faceFacts.add(Map.of("boundary_id", boundaryId, "sample_count", points.length,
                        "plane", "MATCH", "normal_parallel", true));
            }
        }
        if (candidateIds.isEmpty()) throw new IllegalStateException("no native planar faces match the frozen full-air aperture");
        int[] ids = candidateIds.stream().mapToInt(Integer::intValue).toArray();
        verifyPerimeterCoverage(geom, ids, center, normal, axisU, axisV, halfU, halfV);
        GeomMeasure measure = geom.measure();
        measure.selection().init(2);
        measure.selection().set("allDomains", ids);
        double area = measure.getArea();
        double relativeError = Math.abs(area - expectedArea) / expectedArea;
        if (!Double.isFinite(area) || area <= 0.0 || relativeError > 0.01)
            throw new IllegalStateException("selected native Port-face area is not consistent with the frozen full aperture");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("boundary_ids", intList(ids));
        result.put("face_count", ids.length);
        result.put("face_samples", faceFacts);
        result.put("expected_aperture_area_um2", expectedArea);
        result.put("measured_area_native_geometry_units2", area);
        result.put("relative_area_error", relativeError);
        result.put("area_is_approximate", true);
        result.put("area_method", "GeomMeasure.getArea plus coplanar face checks, edge adjacency and perimeter intervals");
        result.put("aperture_extents_um", Arrays.asList(halfU, halfV));
        result.put("face_set_status", "EXACT_ENTITY_IDS_WITH_NATIVE_GEOMETRY_READBACK");
        return result;
    }

    private static void verifyPerimeterCoverage(GeomSequence geom, int[] selectedFaces,
            double[] center, double[] normal, double[] axisU, double[] axisV,
            double halfU, double halfV) {
        Set<Integer> selected = new HashSet<>();
        for (int id : selectedFaces) selected.add(id);
        Set<Integer> edges = new LinkedHashSet<>();
        // COMSOL GeomInfo dimensions are fromDim,toDim: a selected 3-D face
        // (2) maps to its edges (1), and each edge maps back to adjacent faces.
        for (int face : selectedFaces) for (int edge : geom.getAdj(2, 1, face)) edges.add(edge);
        if (edges.isEmpty()) throw new IllegalStateException("selected Port faces have no native perimeter edges");
        List<PerimeterEdgeSample> samplesByEdge = new ArrayList<>();
        for (int edge : edges) {
            int selectedAdjacencies = 0;
            for (int adjacentFace : geom.getAdj(1, 2, edge)) if (selected.contains(adjacentFace)) selectedAdjacencies++;
            if (selectedAdjacencies == 2) {
                samplesByEdge.add(new PerimeterEdgeSample(selectedAdjacencies, new double[0][]));
                continue;
            }
            if (selectedAdjacencies != 1)
                throw new IllegalStateException("Port edge adjacency is empty or non-manifold");
            double[] range = geom.edgeParamRange(edge);
            if (range == null || range.length != 2) throw new IllegalStateException("Port perimeter edge range is malformed");
            double[][] points = new double[9][];
            for (int i = 0; i < points.length; i++) {
                double t = range[0] + (range[1] - range[0]) * i / (points.length - 1.0);
                double[][] point = geom.edgeX(edge, new double[]{t});
                if (point == null || point.length != 1 || point[0].length != 3)
                    throw new IllegalStateException("Port perimeter edge point readback is malformed");
                points[i] = point[0];
            }
            samplesByEdge.add(new PerimeterEdgeSample(selectedAdjacencies, points));
        }
        verifyPerimeterSamples(center, normal, axisU, axisV, halfU, halfV, samplesByEdge);
    }

    /** A model-free input row for the same perimeter classifier used by native readback. */
    public static final class PerimeterEdgeSample {
        private final int selectedFaceAdjacencyCount;
        private final double[][] xyzSamples;

        public PerimeterEdgeSample(int selectedFaceAdjacencyCount, double[][] xyzSamples) {
            this.selectedFaceAdjacencyCount = selectedFaceAdjacencyCount;
            this.xyzSamples = xyzSamples;
        }
    }

    /**
     * Classifies sampled native edge coordinates and proves exact signed-side
     * interval coverage. This pure helper is also exercised without a Model.
     */
    public static void verifyPerimeterSamples(double[] center, double[] normal,
            double[] axisU, double[] axisV, double halfU, double halfV,
            List<PerimeterEdgeSample> edgeSamples) {
        requireFiniteVector(center, "perimeter center");
        requireFiniteVector(normal, "perimeter normal");
        requireFiniteVector(axisU, "perimeter u axis");
        requireFiniteVector(axisV, "perimeter v axis");
        requireOrthonormalRightHanded(axisU, axisV, normal);
        if (!Double.isFinite(halfU) || !Double.isFinite(halfV) || halfU <= 0.0 || halfV <= 0.0)
            throw new IllegalArgumentException("perimeter half widths must be finite and positive");
        if (edgeSamples == null || edgeSamples.isEmpty())
            throw new IllegalArgumentException("Port perimeter edge samples are empty");

        Map<String, List<double[]>> intervals = new LinkedHashMap<>();
        for (String side : new String[]{"u-", "u+", "v-", "v+"}) intervals.put(side, new ArrayList<>());
        for (PerimeterEdgeSample edge : edgeSamples) {
            if (edge == null) throw new IllegalArgumentException("Port edge sample row is null");
            if (edge.selectedFaceAdjacencyCount == 2) continue; // internal edge between selected faces
            if (edge.selectedFaceAdjacencyCount != 1)
                throw new IllegalArgumentException("Port edge is not a one-face perimeter or two-face interior edge");
            if (edge.xyzSamples == null || edge.xyzSamples.length < 2)
                throw new IllegalArgumentException("Port perimeter edge needs at least two coordinate samples");

            double[][] local = new double[edge.xyzSamples.length][2];
            for (int i = 0; i < edge.xyzSamples.length; i++) {
                double[] point = edge.xyzSamples[i];
                requireFiniteVector(point, "Port perimeter coordinate");
                double[] delta = subtract(point, center);
                double planeDistance = dot(delta, normal);
                double u = dot(delta, axisU), v = dot(delta, axisV);
                if (!Double.isFinite(planeDistance) || !Double.isFinite(u) || !Double.isFinite(v))
                    throw new IllegalArgumentException("Port perimeter projection is nonfinite");
                if (Math.abs(planeDistance) > GEOM_TOL_UM)
                    throw new IllegalArgumentException("an unmatched selected Port edge is not on the aperture plane");
                local[i][0] = u;
                local[i][1] = v;
            }

            String perimeterSide = null;
            for (String side : intervals.keySet()) {
                boolean isU = side.charAt(0) == 'u';
                double signedTarget = side.endsWith("-")
                        ? -(isU ? halfU : halfV) : (isU ? halfU : halfV);
                int crossIndex = isU ? 0 : 1;
                boolean allOnSide = true;
                for (double[] point : local) {
                    if (Math.abs(point[crossIndex] - signedTarget) > GEOM_TOL_UM) {
                        allOnSide = false;
                        break;
                    }
                }
                if (allOnSide) {
                    if (perimeterSide != null)
                        throw new IllegalArgumentException("Port edge lies on multiple aperture sides");
                    perimeterSide = side;
                }
            }
            if (perimeterSide == null)
                throw new IllegalArgumentException("selected Port edge is interior, off-side, or bounds an aperture hole");

            boolean isU = perimeterSide.charAt(0) == 'u';
            int alongIndex = isU ? 1 : 0;
            double previous = local[0][alongIndex];
            double minimum = previous, maximum = previous;
            int direction = 0;
            for (int i = 1; i < local.length; i++) {
                double current = local[i][alongIndex];
                double delta = current - previous;
                if (!Double.isFinite(delta) || Math.abs(delta) <= 1e-12)
                    throw new IllegalArgumentException("Port perimeter samples are degenerate or nonfinite");
                int currentDirection = delta > 0.0 ? 1 : -1;
                if (direction != 0 && direction != currentDirection)
                    throw new IllegalArgumentException("Port perimeter edge reverses along its signed side");
                direction = currentDirection;
                minimum = Math.min(minimum, current);
                maximum = Math.max(maximum, current);
                previous = current;
            }
            if (maximum - minimum <= GEOM_TOL_UM)
                throw new IllegalArgumentException("Port perimeter edge has no measurable side interval");
            intervals.get(perimeterSide).add(new double[]{minimum, maximum});
        }
        verifyIntervals(intervals.get("u-"), -halfV, halfV, "u-");
        verifyIntervals(intervals.get("u+"), -halfV, halfV, "u+");
        verifyIntervals(intervals.get("v-"), -halfU, halfU, "v-");
        verifyIntervals(intervals.get("v+"), -halfU, halfU, "v+");
    }

    private static void requireFiniteVector(double[] values, String label) {
        if (values == null || values.length != 3)
            throw new IllegalArgumentException(label + " must have exactly three coordinates");
        for (double value : values)
            if (!Double.isFinite(value)) throw new IllegalArgumentException(label + " must be finite");
    }

    private static void verifyIntervals(List<double[]> intervals, double lower, double upper, String label) {
        if (intervals.isEmpty()) throw new IllegalStateException("Port exterior boundary is missing side " + label);
        intervals.sort((a, b) -> Double.compare(a[0], b[0]));
        double cursor = lower;
        for (double[] interval : intervals) {
            if (interval[0] < cursor - GEOM_TOL_UM)
                throw new IllegalStateException("Port perimeter edge intervals overlap on side " + label);
            if (interval[0] > cursor + GEOM_TOL_UM)
                throw new IllegalStateException("Port perimeter has an uncovered interval on side " + label);
            cursor = Math.max(cursor, interval[1]);
        }
        if (cursor < upper - GEOM_TOL_UM)
            throw new IllegalStateException("Port perimeter does not reach the final aperture corner on side " + label);
    }

    private static double[] apertureHalfWidths(double[][] vertices, double[] center,
            double[] axisU, double[] axisV) {
        double halfU = 0.0, halfV = 0.0;
        Set<String> corners = new HashSet<>();
        for (double[] vertex : vertices) {
            double[] delta = subtract(vertex, center);
            double u = dot(delta, axisU), v = dot(delta, axisV);
            halfU = Math.max(halfU, Math.abs(u));
            halfV = Math.max(halfV, Math.abs(v));
            corners.add((u >= 0 ? "+" : "-") + (v >= 0 ? "+" : "-"));
        }
        if (vertices.length != 4 || corners.size() != 4 || halfU <= 0.0 || halfV <= 0.0)
            throw new IllegalArgumentException("frozen full-air aperture is not a complete four-corner rectangle");
        return new double[]{halfU, halfV};
    }

    private static double[][] vectorArray(Object raw, int vectorSize, String label) {
        List<?> rows = list(raw, label);
        if (rows.size() != 4) throw new IllegalArgumentException(label + " must contain exactly four frozen vertices");
        double[][] result = new double[rows.size()][];
        for (int i = 0; i < rows.size(); i++) result[i] = vector(rows.get(i), vectorSize, label + " vertex");
        return result;
    }

    private static double[] subtract(double[] a, double[] b) {
        double[] result = new double[a.length];
        for (int i = 0; i < result.length; i++) result[i] = a[i] - b[i];
        return result;
    }

    private static void verifyPortBinding(Model model, String portTag, String selectionTag,
                                           Map<String, Object> portReadback) {
        PhysicsFeature port = model.component(COMPONENT).physics("ewfd").feature(portTag);
        if (!"Port".equals(port.getType()) || !selectionTag.equals(port.selection().named()))
            throw new IllegalStateException("Numeric Port feature is not bound to its exact explicit aperture selection");
        int[] ids = port.selection().entities(2);
        if (ids == null || ids.length == 0 || !sameSet(ids, intArray(portReadback.get("boundary_ids"))))
            throw new IllegalStateException("Numeric Port does not read back the explicit full-aperture boundary ID set");
    }

    private static void resultSelection(GeomSequence geom, String tag) {
        geom.feature(tag).set("selresult", "on");
        geom.feature(tag).set("selresultshow", "dom");
    }

    private static int[] generatedDomainIds(Model model, String featureTag) {
        String selectionTag = GEOMETRY + "_" + featureTag + "_dom";
        if (!model.component(COMPONENT).selection().hasTag(selectionTag))
            throw new IllegalStateException("geometry feature result did not create its exact domain selection: " + featureTag);
        SelectionFeature selection = model.component(COMPONENT).selection(selectionTag);
        int[] ids = selection.entities(3);
        if (ids == null) return new int[0];
        int[] copy = ids.clone();
        Arrays.sort(copy);
        return copy;
    }

    private static void removeIfPresent(com.comsol.model.GeomFeatureList features, String... tags) {
        for (String tag : tags) if (features.hasTag(tag)) features.remove(tag);
    }

    private static void assertDisjoint(int[] first, int[] second) {
        Set<Integer> left = new HashSet<>();
        for (int id : first) left.add(id);
        for (int id : second) if (left.contains(id))
            throw new IllegalStateException("physical-air and PML owner domains overlap");
    }

    private static void requireOrthonormalRightHanded(double[] a, double[] b, double[] w) {
        if (Math.abs(dot(a, a) - 1) > NORMAL_TOL || Math.abs(dot(b, b) - 1) > NORMAL_TOL
                || Math.abs(dot(w, w) - 1) > NORMAL_TOL || Math.abs(dot(a, b)) > NORMAL_TOL
                || Math.abs(dot(a, w)) > NORMAL_TOL || Math.abs(dot(b, w)) > NORMAL_TOL
                || distance(cross(a, b), w) > NORMAL_TOL)
            throw new IllegalArgumentException("frozen WorkPlane axes must be orthonormal and satisfy a cross b = w");
    }

    private static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map)) throw new IllegalArgumentException(label + " must be an object");
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
            if (!(entry.getKey() instanceof String)) throw new IllegalArgumentException(label + " keys must be strings");
            out.put((String) entry.getKey(), entry.getValue());
        }
        return out;
    }

    private static List<?> list(Object value, String label) {
        if (!(value instanceof List)) throw new IllegalArgumentException(label + " must be an array");
        return (List<?>) value;
    }

    private static String nonempty(Object value, String label) {
        if (!(value instanceof String) || ((String) value).trim().isEmpty())
            throw new IllegalArgumentException(label + " must be a nonempty string");
        return (String) value;
    }

    private static double number(Object value, String label) {
        if (!(value instanceof Number) || value instanceof Boolean)
            throw new IllegalArgumentException(label + " must be numeric");
        double result = ((Number) value).doubleValue();
        if (!Double.isFinite(result)) throw new IllegalArgumentException(label + " must be finite");
        return result;
    }

    private static int integer(Object value, String label) {
        double result = number(value, label);
        if (result != Math.rint(result) || result < Integer.MIN_VALUE || result > Integer.MAX_VALUE)
            throw new IllegalArgumentException(label + " must be an exact integer");
        return (int) result;
    }

    private static double finite(Object value) {
        if (!(value instanceof Number) || value instanceof Boolean || !Double.isFinite(((Number) value).doubleValue()))
            throw new IllegalArgumentException("recipe numeric value must be finite");
        return ((Number) value).doubleValue();
    }

    private static double[] vector(Object raw, int size, String label) {
        List<?> values = list(raw, label);
        if (values.size() != size) throw new IllegalArgumentException(label + " has the wrong vector length");
        double[] result = new double[size];
        for (int i = 0; i < size; i++) result[i] = finite(values.get(i));
        return result;
    }

    private static double[] add(double[] a, double[] b) {
        double[] result = new double[a.length];
        for (int i = 0; i < result.length; i++) result[i] = a[i] + b[i];
        return result;
    }

    private static double[] scale(double[] value, double factor) {
        double[] result = value.clone();
        for (int i = 0; i < result.length; i++) result[i] *= factor;
        return result;
    }

    private static double dot(double[] a, double[] b) {
        double result = 0.0;
        for (int i = 0; i < a.length; i++) result += a[i] * b[i];
        return result;
    }

    private static double[] cross(double[] a, double[] b) {
        return new double[]{a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
    }

    private static double distance(double[] a, double[] b) {
        double sum = 0.0;
        for (int i = 0; i < a.length; i++) sum += (a[i] - b[i]) * (a[i] - b[i]);
        return Math.sqrt(sum);
    }

    private static void normalize(double[] value) {
        double norm = Math.sqrt(dot(value, value));
        if (!Double.isFinite(norm) || norm <= 0.0) throw new IllegalArgumentException("zero port axis");
        for (int i = 0; i < value.length; i++) value[i] /= norm;
    }

    private static boolean contains(String[] values, String expected) {
        return Arrays.asList(values).contains(expected);
    }

    private static int[] uniqueIds(List<Integer> values) {
        LinkedHashSet<Integer> ids = new LinkedHashSet<>(values);
        for (Integer id : ids) if (id == null || id <= 0) throw new IllegalArgumentException("domain IDs must be positive");
        int[] result = ids.stream().mapToInt(Integer::intValue).toArray();
        Arrays.sort(result);
        return result;
    }

    private static boolean sameSet(int[] left, int[] right) {
        if (left == null || right == null || left.length != right.length) return false;
        int[] a = left.clone(), b = right.clone();
        Arrays.sort(a); Arrays.sort(b);
        return Arrays.equals(a, b);
    }

    private static List<Integer> intList(int[] values) {
        List<Integer> result = new ArrayList<>();
        for (int value : values) result.add(value);
        return result;
    }

    private static List<Double> boxed(double[] values) {
        List<Double> result = new ArrayList<>();
        for (double value : values) result.add(value);
        return result;
    }

    private static Map<String, Object> intMap(Map<String, int[]> values) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<String, int[]> entry : values.entrySet()) result.put(entry.getKey(), intList(entry.getValue()));
        return result;
    }

    private static int[] intArray(Object raw) {
        List<?> values = list(raw, "entity IDs");
        int[] result = new int[values.size()];
        for (int i = 0; i < values.size(); i++) result[i] = (int) number(values.get(i), "entity ID");
        return result;
    }
}
