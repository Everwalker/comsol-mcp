import com.comsol.model.Coordsys;
import com.comsol.model.GeomFeature;
import com.comsol.model.GeomMeasure;
import com.comsol.model.GeomObjectSelection;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.ModelNode;
import com.comsol.model.NumericalFeature;
import com.comsol.model.PropFeature;
import com.comsol.model.SolutionInfo;
import com.comsol.model.SolverSequence;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.FeatureInfo;
import com.comsol.model.physics.FeatureInfoList;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.Collections;

/**
 * Versioned COMSOL 6.4 API contract helpers for W23 radiation geometry v2.
 *
 * This source is compile-checked only in the software stage. No method here is
 * dispatched during software tests. Geometry, PML, Port, topology, materials,
 * and solution acceptance remain native readback requirements.
 */
public final class NativeW23RadiationGeometryV2 {
    public static final String SCHEMA_ID = "urn:comsol-mcp:w23:radiation-geometry-v2-java:1.0.0";
    static final double CV_EDGE_SAMPLE_TOLERANCE_UM = 1.0e-4;
    static final int CV_EDGE_SAMPLE_MAX_DEPTH = 12;
    static final int CV_EDGE_SAMPLE_INITIAL_SEGMENTS = 8;
    private static final String COMPONENT = "comp3d";
    private static final String GEOMETRY = "geom3d";
    private static final String CV_PARTITION = "cvPartitionV2";
    private static final double[][] CV_LIMITS_UM = {{-19.0, 19.0}, {-5.5, 5.5}, {-5.5, 5.5}};
    private static final double CV_GEOMETRY_TOLERANCE_UM = 1.0e-7;
    // Empty until a real 6.4 calibration is independently reviewed and pinned.
    private static final String APPROVED_QABS_SCHEMA_ID = "";
    private static final String APPROVED_QABS_CALIBRATION_SHA256 = "";
    private static final String APPROVED_QABS_CALIBRATION_ROUTE_BINDING_SHA256 = "";
    private static final String APPROVED_QABS_APPROVAL_EVIDENCE_SHA256 = "";
    private static final int APPROVED_QABS_EXPRESSION_COLUMN = -1;
    private static final int APPROVED_QABS_UNIT_COLUMN = -1;
    private static final int APPROVED_QABS_MINIMUM_FEATURE_INFO_OWNERS = 2;

    private NativeW23RadiationGeometryV2() {}

    /** Public managed-Java entry point for read-only CV inventory and sampled power terms. */
    public static Object run(Model model, Map<String, Object> args) {
        if (model == null || args == null) throw new IllegalArgumentException("model and arguments are required");
        String phase = String.valueOf(args.get("phase"));
        if ("read_cv_topology".equals(phase))
            return readControlVolumeTopologyForBox(model, COMPONENT, GEOMETRY);
        if ("cv_power_terms".equals(phase))
            return evaluateControlVolumePowerTerms(model, args);
        if ("qabs_mapping_calibration".equals(phase))
            return calibrateQabsMapping(model, args);
        throw new IllegalArgumentException("phase must be read_cv_topology, qabs_mapping_calibration, or cv_power_terms");
    }

    public static Map<String, Object> configureDomainBackedNumericPort(
            PhysicsFeature feature, int portNumber) {
        if (feature == null || portNumber < 1 || portNumber > 2)
            throw new IllegalArgumentException("exact input/output Numeric Port feature and number are required");
        feature.set("PortType", "Numeric");
        feature.set("PortName", String.valueOf(portNumber));
        feature.set("PortSlit", 1);
        feature.set("SlitType", "DomainBacked");
        String type = feature.getString("PortType");
        String name = feature.getString("PortName");
        String slit = feature.getString("PortSlit");
        String slitType = feature.getString("SlitType");
        if (!"Numeric".equals(type) || !String.valueOf(portNumber).equals(name)
                || !"1".equals(slit) || !"DomainBacked".equals(slitType))
            throw new IllegalStateException("Numeric Port DomainBacked slit did not read back exactly");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", SCHEMA_ID);
        result.put("feature_type", feature.getType());
        result.put("PortType", type);
        result.put("PortName", name);
        result.put("PortSlit", slit);
        result.put("SlitType", slitType);
        result.put("native_readback_required", true);
        return result;
    }

    public static Map<String, Object> configureUserDefinedPml(
            Coordsys feature, int[] domainIds, String[] distanceExpressions,
            String[] dmaxExpressions, String typicalWavelengthExpression) {
        if (feature == null) throw new IllegalArgumentException("PML coordinate-system feature is required");
        requireUniquePositive(domainIds, "PML domain IDs");
        if (distanceExpressions == null || dmaxExpressions == null
                || distanceExpressions.length < 1 || distanceExpressions.length > 3
                || distanceExpressions.length != dmaxExpressions.length)
            throw new IllegalArgumentException("userDefined PML requires one to three paired d/dmax directions");
        if (typicalWavelengthExpression == null || typicalWavelengthExpression.trim().isEmpty())
            throw new IllegalArgumentException("frozen typical wavelength expression is required");
        for (int i = 0; i < distanceExpressions.length; i++) {
            if (distanceExpressions[i] == null || distanceExpressions[i].trim().isEmpty()
                    || dmaxExpressions[i] == null || dmaxExpressions[i].trim().isEmpty())
                throw new IllegalArgumentException("every PML d and dmax expression must be nonempty");
        }

        feature.selection().set(domainIds);
        feature.set("ScalingType", "userDefined");
        feature.set("stretchingType", "polynomial");
        feature.set("wavelengthSourceType", "userDefined");
        feature.set("typicalWavelength", typicalWavelengthExpression);
        feature.set("PMLfactor", 1.0);
        feature.set("PMLgamma", 1.0);
        feature.set("directions", distanceExpressions.length);
        for (int i = 0; i < distanceExpressions.length; i++) {
            feature.setIndex("d", distanceExpressions[i], i);
            feature.setIndex("dmax", dmaxExpressions[i], i);
        }

        int[] actualDomains = feature.selection().entities(3);
        Arrays.sort(actualDomains);
        int[] expectedDomains = domainIds.clone();
        Arrays.sort(expectedDomains);
        if (!Arrays.equals(actualDomains, expectedDomains))
            throw new IllegalStateException("PML feature domain selection differs from the exact partition cell");
        if (!"userDefined".equals(feature.getString("ScalingType"))
                || !"polynomial".equals(feature.getString("stretchingType"))
                || feature.getInt("directions") != distanceExpressions.length
                || Double.compare(feature.getDouble("PMLfactor"), 1.0) != 0
                || Double.compare(feature.getDouble("PMLgamma"), 1.0) != 0)
            throw new IllegalStateException("PML property readback differs from the frozen polynomial profile");
        List<Map<String, Object>> directions = new ArrayList<>();
        for (int i = 0; i < distanceExpressions.length; i++) {
            String d = feature.getString("d", i);
            String dmax = feature.getString("dmax", i);
            if (!distanceExpressions[i].equals(d) || !dmaxExpressions[i].equals(dmax))
                throw new IllegalStateException("indexed PML d/dmax expression changed on readback");
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("index", i);
            row.put("distance_expression", d);
            row.put("dmax_expression", dmax);
            directions.add(row);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", SCHEMA_ID);
        result.put("feature_type", feature.getType());
        result.put("node_tag", feature.tag());
        result.put("selected_domain_ids", asList(actualDomains));
        result.put("ScalingType", feature.getString("ScalingType"));
        result.put("stretchingType", feature.getString("stretchingType"));
        result.put("wavelengthSourceType", feature.getString("wavelengthSourceType"));
        result.put("typicalWavelength", feature.getString("typicalWavelength"));
        result.put("PMLfactor", feature.getDouble("PMLfactor"));
        result.put("PMLgamma", feature.getDouble("PMLgamma"));
        result.put("directions", directions);
        result.put("native_readback_required", true);
        return result;
    }

    /** Configure, but do not run, an exact six-plane PartitionDomains feature. */
    public static Map<String, Object> configureControlVolumePartition(
            GeomSequence geom, String geometryTag, int[] targetDomainIds, int[] partitionFaceIds) {
        if (geom == null || geometryTag == null || geometryTag.trim().isEmpty())
            throw new IllegalArgumentException("geometry sequence and tag are required");
        requireUniquePositive(targetDomainIds, "control-volume target domains");
        requireUniquePositive(partitionFaceIds, "control-volume partition faces");
        if (partitionFaceIds.length != 6)
            throw new IllegalArgumentException("the candidate control volume requires six partition planes");
        if (Arrays.asList(geom.feature().tags()).contains(CV_PARTITION))
            throw new IllegalStateException("refusing to replace an existing control-volume PartitionDomains feature");
        GeomFeature partition = geom.feature().create(CV_PARTITION, "PartitionDomains");
        partition.selection("domain").set(geometryTag, targetDomainIds);
        partition.set("partitionwith", "faces");
        partition.selection("face").set(geometryTag, partitionFaceIds);
        partition.set("selresult", "on");
        partition.set("selresultshow", "bnd");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", SCHEMA_ID);
        result.put("feature_tag", CV_PARTITION);
        result.put("feature_type", partition.getType());
        result.put("partitionwith", partition.getString("partitionwith"));
        result.put("target_domain_ids", asList(targetDomainIds));
        result.put("partition_face_ids", asList(partitionFaceIds));
        result.put("geometry_execution", "NOT_CALLED_BY_SOFTWARE_STAGE");
        return result;
    }

    /** Read exact native adjacency, area, point/normal, material, Port and PML membership. */
    public static Map<String, Object> readControlVolumeTopology(
            Model model, String componentTag, String geometryTag, int[] boundaryIds) {
        if (model == null || componentTag == null || geometryTag == null)
            throw new IllegalArgumentException("managed model/component/geometry identity is required");
        requireUniquePositive(boundaryIds, "control-volume boundary IDs");
        ModelNode component = model.component(componentTag);
        GeomSequence geom = component.geom(geometryTag);
        if (!Arrays.asList(geom.feature().tags()).contains(CV_PARTITION)
                || !"PartitionDomains".equals(geom.feature(CV_PARTITION).getType()))
            throw new IllegalStateException("actual CV PartitionDomains feature is missing");
        GeomFeature partition = geom.feature(CV_PARTITION);
        if (!"faces".equals(partition.getString("partitionwith")))
            throw new IllegalStateException("actual CV partition must use the six registered faces");
        if (!"um".equals(geom.lengthUnit()))
            throw new IllegalStateException("W23 CV geometry unit must read back as um before coordinate comparison");
        int[] partitionSourceFaces = partition.selection("face").entities(geometryTag, 2);
        int[] partitionTargetDomains = partition.selection("domain").entities(geometryTag, 3);
        if (partitionSourceFaces == null || partitionSourceFaces.length != 6
                || partitionTargetDomains == null || partitionTargetDomains.length == 0)
            throw new IllegalStateException("CV PartitionDomains source faces/target domains are incomplete");

        Map<Integer, String> materialByDomain = materialDomainReadback(component);
        Set<Integer> pmlDomains = pmlDomainReadback(component);
        Set<Integer> portBoundaries = portBoundaryReadback(component);
        List<Map<String, Object>> faces = new ArrayList<>();
        GeomMeasure measure = geom.measure();
        GeomObjectSelection allSelection = measure.selection();
        allSelection.all(geometryTag);
        int[] allDomainIds = allSelection.entities(geometryTag, 3);
        if (allDomainIds == null || allDomainIds.length != geom.getNDomains())
            throw new IllegalStateException("native full-domain selection disagrees with the geometry domain count");
        Arrays.sort(allDomainIds);
        List<Map<String, Object>> domainInventory = new ArrayList<>();
        List<Integer> interiorDomainIds = new ArrayList<>();
        Set<Integer> seenDomainIds = new LinkedHashSet<>();
        for (int domainId : allDomainIds) {
            if (domainId < 1 || !seenDomainIds.add(domainId))
                throw new IllegalStateException("native full-domain inventory contains invalid or duplicate IDs");
            String materialTag = materialByDomain.get(domainId);
            if (materialTag == null)
                throw new IllegalStateException("native full-domain inventory contains a domain without material assignment");
            double[] bbox = selectedDomainBoundingBox(measure, geometryTag, domainId);
            boolean entirelyInside = bboxWithinCv(bbox);
            boolean isPml = pmlDomains.contains(domainId);
            if (entirelyInside) {
                if (isPml) throw new IllegalStateException("PML domain is classified inside the physical control volume");
                interiorDomainIds.add(domainId);
            }
            Map<String, Object> domain = new LinkedHashMap<>();
            domain.put("domain_id", domainId);
            domain.put("bounding_box_um", bbox);
            domain.put("material_tag", materialTag);
            domain.put("is_pml", isPml);
            domain.put("physical_domain_role", isPml ? "PML" : "NON_PML");
            domainInventory.add(domain);
        }
        allSelection.clear(geometryTag);
        if (interiorDomainIds.isEmpty())
            throw new IllegalStateException("native control-volume has no fully contained non-PML domains");
        for (int boundaryId : boundaryIds) {
            int[] adjacentDomains = geom.getAdj(2, 3, boundaryId);
            int[] adjacentDomainOrientations = geom.getAdjOrient(2, 3, boundaryId);
            if (adjacentDomains == null || adjacentDomains.length != 2
                    || adjacentDomains[0] == adjacentDomains[1]
                    || adjacentDomainOrientations == null || adjacentDomainOrientations.length != 2
                    || (adjacentDomainOrientations[0] != -1 && adjacentDomainOrientations[0] != 1)
                    || (adjacentDomainOrientations[1] != -1 && adjacentDomainOrientations[1] != 1)
                    || adjacentDomainOrientations[0] == adjacentDomainOrientations[1])
                throw new IllegalStateException("CV boundary is not one internal two-domain face");
            int[] edgeIds = geom.getAdj(2, 1, boundaryId);
            int[] edgeSigns = geom.getAdjOrient(2, 1, boundaryId);
            if (edgeIds == null || edgeSigns == null || edgeIds.length == 0
                    || edgeIds.length != edgeSigns.length)
                throw new IllegalStateException("CV face has no exact edge/orientation adjacency");
            List<Map<String, Object>> unorderedEdges = new ArrayList<>();
            Map<Integer, double[]> vertexCoordinates = new LinkedHashMap<>();
            for (int i = 0; i < edgeIds.length; i++) {
                if (edgeSigns[i] != -1 && edgeSigns[i] != 1)
                    throw new IllegalStateException("CV edge orientation is indeterminate");
                int[] edgeVertices = geom.getAdj(1, 0, edgeIds[i]);
                if (edgeVertices == null || edgeVertices.length < 1 || edgeVertices.length > 2)
                    throw new IllegalStateException("CV edge endpoint topology is unsupported or incomplete");
                int firstVertex = edgeVertices[0];
                int lastVertex = edgeVertices.length == 1 ? firstVertex : edgeVertices[1];
                int startVertex = edgeSigns[i] > 0 ? firstVertex : lastVertex;
                int endVertex = edgeSigns[i] > 0 ? lastVertex : firstVertex;
                vertexCoordinates.putIfAbsent(startVertex, selectedVertexCoordinate(measure, geometryTag, startVertex));
                vertexCoordinates.putIfAbsent(endVertex, selectedVertexCoordinate(measure, geometryTag, endVertex));
                List<double[]> samples = edgeCurveSamples(geom, edgeIds[i], edgeSigns[i] < 0);
                if (!samePoint(samples.get(0), vertexCoordinates.get(startVertex), 1e-7)
                        || !samePoint(samples.get(samples.size() - 1), vertexCoordinates.get(endVertex), 1e-7))
                    throw new IllegalStateException("actual edge curve samples do not match oriented endpoint vertices");
                Map<String, Object> edge = new LinkedHashMap<>();
                edge.put("edge_id", edgeIds[i]);
                edge.put("orientation", edgeSigns[i]);
                edge.put("start_vertex_id", startVertex);
                edge.put("end_vertex_id", endVertex);
                edge.put("sample_order", "face_orientation");
                edge.put("sample_points_um", samples);
                unorderedEdges.add(edge);
            }
            List<List<Map<String, Object>>> edgeLoops = orderEdgeLoops(unorderedEdges);
            double[] range = geom.faceParamRange(boundaryId);
            if (range == null || range.length != 4)
                throw new IllegalStateException("CV face does not have the expected 2-D parameter range");
            double[][] parameter = {{(range[0] + range[1]) * 0.5, (range[2] + range[3]) * 0.5}};
            double[][] points = geom.faceX(boundaryId, parameter);
            double[][] normals = geom.faceNormal(boundaryId, parameter);
            if (points == null || points.length != 1 || points[0].length != 3
                    || normals == null || normals.length != 1 || normals[0].length != 3)
                throw new IllegalStateException("CV face point/normal readback is malformed");
            measure.selection().set(geometryTag, 2, boundaryId);
            double area = measure.getArea();
            measure.selection().clear(geometryTag);
            String material0 = materialByDomain.get(adjacentDomains[0]);
            String material1 = materialByDomain.get(adjacentDomains[1]);
            if (material0 == null || material1 == null)
                throw new IllegalStateException("CV adjacent domain lacks an actual native material assignment");
            boolean touchesPml = pmlDomains.contains(adjacentDomains[0]) || pmlDomains.contains(adjacentDomains[1]);
            Map<String, Object> adjacentBounds = new LinkedHashMap<>();
            for (int domainId : adjacentDomains)
                adjacentBounds.put(String.valueOf(domainId), selectedDomainBoundingBox(measure, geometryTag, domainId));
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("boundary_id", boundaryId);
            row.put("approximate_area_value_native_units", area);
            row.put("area_method", "GeomMeasure.getArea");
            row.put("area_method_accuracy", "APPROXIMATE_RENDERING_MESH_PER_COMSOL_6.4_PROGRAMMING_REFERENCE_MANUAL_P277");
            row.put("face_parameter_sample_point_um", points[0]);
            row.put("raw_face_normal_xyz", normals[0]);
            row.put("face_normal_method", "GeomSequence.faceNormal intrinsic orientation; outward direction derived by software adapter");
            row.put("adjacent_domain_ids", adjacentDomains);
            row.put("adjacent_domain_orientation", adjacentDomainOrientations);
            row.put("adjacent_domain_bbox_um_by_id", adjacentBounds);
            row.put("adjacent_material_tags", Arrays.asList(material0, material1));
            row.put("adjacent_domain_pml_by_id", Map.of(
                    String.valueOf(adjacentDomains[0]), pmlDomains.contains(adjacentDomains[0]),
                    String.valueOf(adjacentDomains[1]), pmlDomains.contains(adjacentDomains[1])));
            row.put("touches_pml", touchesPml);
            row.put("touches_port", portBoundaries.contains(boundaryId));
            List<Map<String, Object>> vertices = new ArrayList<>();
            for (Map.Entry<Integer, double[]> entry : vertexCoordinates.entrySet())
                vertices.add(Map.of("vertex_id", entry.getKey(), "point_um", entry.getValue()));
            row.put("vertex_coordinates_um", vertices);
            row.put("edge_loops", edgeLoops);
            faces.add(row);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", "urn:comsol-mcp:w23:control-volume-topology:1.0.0");
        result.put("evidence_scope", "COMSOL_NATIVE_PARTITION_DOMAINS");
        result.put("topology_source", "GeomInfo.getAdj/getAdjOrient+GeomMeasure");
        result.put("partition_feature_type", partition.getType());
        result.put("actual_partition_feature_readback", true);
        result.put("coordinate_unit", geom.lengthUnit());
        result.put("geometry_length_unit", geom.lengthUnit());
        result.put("geometry_length_unit_readback", true);
        result.put("area_unit", "UNVERIFIED_GEOMETRY_MEASURE_OUTPUT_UNIT");
        result.put("boundary_ids", asList(boundaryIds));
        result.put("partition_source_face_ids", asList(partitionSourceFaces));
        result.put("partition_target_domain_ids", asList(partitionTargetDomains));
        result.put("geometry_domain_count", geom.getNDomains());
        result.put("domain_inventory", domainInventory);
        result.put("cv_interior_domain_ids", interiorDomainIds);
        result.put("cv_box_um", cvBoxReadback());
        result.put("faces", faces);
        result.put("area_diagnostic_method", "GeomMeasure.getArea is approximate per COMSOL 6.4 Programming Reference Manual p.277; not used as final area proof");
        result.put("native_readback_only", true);
        result.put("native_surface_integral_of_one", "NOT_RUN");
        result.put("power_balance", "NOT_RUN");
        return result;
    }

    private static Map<String, Object> readControlVolumeTopologyForBox(
            Model model, String componentTag, String geometryTag) {
        ModelNode component = model.component(componentTag);
        GeomSequence geom = component.geom(geometryTag);
        GeomMeasure measure = geom.measure();
        GeomObjectSelection selection = measure.selection();
        selection.all(geometryTag);
        int[] allBoundaryIds = selection.entities(geometryTag, 2);
        selection.clear(geometryTag);
        if (allBoundaryIds == null || allBoundaryIds.length == 0)
            throw new IllegalStateException("native geometry has no boundary inventory");
        Arrays.sort(allBoundaryIds);
        List<Integer> candidateIds = new ArrayList<>();
        for (int boundaryId : allBoundaryIds) {
            if (boundaryId < 1) throw new IllegalStateException("native boundary inventory has an invalid ID");
            double[] bbox = selectedBoundaryBoundingBox(measure, geometryTag, boundaryId);
            int matches = 0;
            for (int axis = 0; axis < 3; axis++) {
                for (int side = 0; side < 2; side++) {
                    double plane = CV_LIMITS_UM[axis][side];
                    int lowIndex = 2 * axis;
                    int highIndex = lowIndex + 1;
                    if (Math.abs(bbox[lowIndex] - plane) > CV_GEOMETRY_TOLERANCE_UM
                            || Math.abs(bbox[highIndex] - plane) > CV_GEOMETRY_TOLERANCE_UM)
                        continue;
                    boolean withinRectangle = true;
                    for (int other = 0; other < 3; other++) {
                        if (other == axis) continue;
                        int otherLow = 2 * other;
                        int otherHigh = otherLow + 1;
                        if (bbox[otherLow] < CV_LIMITS_UM[other][0] - CV_GEOMETRY_TOLERANCE_UM
                                || bbox[otherHigh] > CV_LIMITS_UM[other][1] + CV_GEOMETRY_TOLERANCE_UM) {
                            withinRectangle = false;
                            break;
                        }
                    }
                    if (!withinRectangle)
                        throw new IllegalStateException("native boundary on a registered CV plane extends outside its finite rectangle");
                    matches++;
                }
            }
            if (matches > 1)
                throw new IllegalStateException("native CV boundary patch ambiguously belongs to multiple registered planes");
            if (matches == 1) candidateIds.add(boundaryId);
        }
        if (candidateIds.isEmpty())
            throw new IllegalStateException("native geometry has no boundary entities on the registered control-volume planes");
        int[] ids = new int[candidateIds.size()];
        for (int index = 0; index < ids.length; index++) ids[index] = candidateIds.get(index);
        return readControlVolumeTopology(model, componentTag, geometryTag, ids);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> evaluateControlVolumePowerTerms(
            Model model, Map<String, Object> args) {
        if (!"NOT_RUN".equals(args.get("native_result"))
                || !Boolean.FALSE.equals(args.get("study_or_solver_invoked")))
            throw new IllegalArgumentException("CV terms route must be read-only and must not invoke a study or solver");
        Map<String, Object> topology = requiredMap(args.get("control_volume_topology"), "control_volume_topology");
        Map<String, Object> source = requiredMap(args.get("source"), "source");
        Map<String, Object> sourceCohortReadback = requiredMap(args.get("source_field_readback"), "source_field_readback");
        if (!"COMSOL_NATIVE_RAW".equals(sourceCohortReadback.get("native_result")))
            throw new IllegalArgumentException("source field proof must be an actual native readback");
        Map<String, Object> sourceCohort = requiredMap(sourceCohortReadback.get("source_cohort"), "source_field_readback.source_cohort");
        if (!"urn:comsol-mcp:w23:source-cohort-snapshot:1.0.0".equals(sourceCohort.get("schema_id"))
                || !"COMSOL_NATIVE_SOURCE_COHORT_SNAPSHOTS".equals(sourceCohort.get("native_result")))
            throw new IllegalArgumentException("source proof lacks the native stored-solution cohort readback");
        if (!"urn:comsol-mcp:w23:control-volume-topology:1.0.0".equals(topology.get("schema_id"))
                || !"COMSOL_NATIVE_PARTITION_DOMAINS".equals(topology.get("evidence_scope"))
                || !"PartitionDomains".equals(topology.get("partition_feature_type"))
                || !Boolean.TRUE.equals(topology.get("actual_partition_feature_readback")))
            throw new IllegalArgumentException("power integration requires versioned native PartitionDomains topology");
        Map<String, Object> sourceBefore = requiredMap(sourceCohort.get("before"), "source cohort before");
        Map<String, Object> sourceAfter = requiredMap(sourceCohort.get("after"), "source cohort after");
        if (!sourceBefore.equals(sourceAfter))
            throw new IllegalStateException("source stored-solution/fixture identity changed during its source sample");
        String datasetTag = requiredString(source.get("dataset_id"), "source.dataset_id");
        String solutionTag = requiredString(source.get("solution_id"), "source.solution_id");
        int outer = requiredPositiveIndex(source.get("outer_index"), "source.outer_index");
        int inner = requiredPositiveIndex(source.get("inner_index"), "source.inner_index");
        int solnum = requiredPositiveIndex(source.get("solnum"), "source.solnum");
        if (!sameNestedValues(source, sourceFieldSource(sourceCohortReadback)) || inner != solnum)
            throw new IllegalArgumentException("source contract differs from the exact paired field readback/selected SolutionInfo tuple");
        Map<String, Object> selectedSolutionBefore =
                readSelectedSolutionIdentity(model, datasetTag, solutionTag, outer, inner, solnum);
        if (!sourceIdentityMatchesCohort(selectedSolutionBefore, sourceBefore))
            throw new IllegalStateException("current native dataset/solution/tuple/date/parameters differ from the source cohort");
        Map<String, Object> sourceFixture = requiredMap(
                sourceBefore.get("fixture_explicit_configuration"), "source cohort fixture configuration");
        Map<String, Object> sourcePhysics = requiredMap(sourceFixture.get("physics"), "source cohort physics");
        Map<String, Object> sourceEquationInventory = requiredMap(
                sourcePhysics.get("equation_view_inventory"), "source cohort Equation View inventory");
        Map<String, Object> currentEquationInventory = physicsEquationInventory(
                model.component(COMPONENT).physics("ewfd"));
        if (!sameNestedValues(sourceEquationInventory, currentEquationInventory))
            throw new IllegalStateException("current EWFD Equation View inventory differs from the source-field cohort");
        Map<String, Object> qabsCheck = validateQabsContractAndCertificate(
                model, args, sourceEquationInventory);

        List<Integer> faceIds = new ArrayList<>();
        Map<String, int[]> faceIdsByRole = new LinkedHashMap<>();
        Set<String> expectedFaceRoles = new LinkedHashSet<>(Arrays.asList(
                "x_minus", "x_plus", "y_minus", "y_plus", "z_minus", "z_plus"));
        Object rawFaces = topology.get("faces");
        if (!(rawFaces instanceof List)) throw new IllegalArgumentException("validated native CV topology faces are required");
        for (Object rawFace : (List<?>) rawFaces) {
            Map<String, Object> face = requiredMap(rawFace, "control-volume face");
            String role = requiredString(face.get("role"), "control-volume face role");
            Object rawPatches = face.get("patches");
            if (!(rawPatches instanceof List) || ((List<?>) rawPatches).isEmpty())
                throw new IllegalArgumentException("each control-volume face role must own actual patches");
            List<Integer> ids = new ArrayList<>();
            for (Object rawPatch : (List<?>) rawPatches) {
                Map<String, Object> patch = requiredMap(rawPatch, "control-volume patch");
                int id = requiredPositiveIndex(patch.get("boundary_id"), "patch boundary_id");
                ids.add(id);
                faceIds.add(id);
            }
            Collections.sort(ids);
            if (faceIdsByRole.put(role, toIntArray(ids)) != null)
                throw new IllegalArgumentException("control-volume face role is duplicated");
        }
        if (!faceIdsByRole.keySet().equals(expectedFaceRoles)
                || new LinkedHashSet<>(faceIds).size() != faceIds.size())
            throw new IllegalArgumentException("six registered CV faces must have unique patch IDs");
        Collections.sort(faceIds);
        int[] allFaceIds = toIntArray(faceIds);

        // Re-read the full topology and domain inventory in this same managed operation.
        Map<String, Object> freshTopology = readControlVolumeTopology(
                model, COMPONENT, GEOMETRY, allFaceIds);
        if (!samePositiveIds(allFaceIds, listToIntArray(topology.get("boundary_ids"))))
            throw new IllegalArgumentException("validated topology boundary inventory differs from its six role patch groups");
        Object rawInterior = topology.get("cv_interior_domain_ids");
        if (!(rawInterior instanceof List))
            throw new IllegalArgumentException("native topology must include the full CV interior-domain inventory");
        int[] interiorDomainIds = listToIntArray(rawInterior);
        requireUniquePositive(interiorDomainIds, "CV interior domain IDs");
        if (!samePositiveIds(interiorDomainIds, listToIntArray(freshTopology.get("cv_interior_domain_ids"))))
            throw new IllegalStateException("CV interior-domain IDs changed between topology and integral dispatch");
        if (!sameNestedValues(topology.get("domain_inventory"), freshTopology.get("domain_inventory")))
            throw new IllegalStateException("complete native domain/material/PML inventory changed before integration");
        if (!sourceTagAndModelMatch(model, source, sourceBefore))
            throw new IllegalStateException("native model/dataset/solution identity differs from the exact source-field cohort");

        List<Map<String, Object>> surfaceTerms = new ArrayList<>();
        Map<String, Object> areaByRole = new LinkedHashMap<>();
        Map<String, Object> fluxByRole = new LinkedHashMap<>();
        String nonce = Long.toUnsignedString(System.nanoTime(), 36);
        List<String> numericalTags = new ArrayList<>();
        List<Map<String, Object>> numericalReadbacks = new ArrayList<>();
        Map<String, Object> volumeReadback = null;
        Map<String, Object> pinReadback = null;
        Map<String, Object> qabsIntegralReadback = null;
        String operationFailure = "";
        String cleanupFailure = "";
        List<String> remainingTags = new ArrayList<>();
        try {
            for (String role : new String[]{"x_minus", "x_plus", "y_minus", "y_plus", "z_minus", "z_plus"}) {
                int[] ids = faceIdsByRole.get(role);
                if (ids == null || ids.length == 0)
                    throw new IllegalArgumentException("registered CV face role is missing: " + role);
                String axis = role.substring(0, 1);
                int sign = role.endsWith("minus") ? -1 : 1;
                String baseExpression = "ewfd.Poav" + axis;
                String expression = sign < 0 ? "-" + baseExpression : baseExpression;
                Map<String, Object> area = evaluateIntegral(model, "w23cv" + nonce + "a" + axis + sign,
                        "IntSurface", datasetTag, solnum, outer, 2, ids, "1", "m^2", numericalTags);
                numericalReadbacks.add(area);
                Map<String, Object> flux = evaluateIntegral(model, "w23cv" + nonce + "f" + axis + sign,
                        "IntSurface", datasetTag, solnum, outer, 2, ids, expression, "W", numericalTags);
                numericalReadbacks.add(flux);
                areaByRole.put(role, area.get("value"));
                fluxByRole.put(role, flux.get("value"));
                surfaceTerms.add(Map.of("role", role, "boundary_ids", asList(ids),
                        "normal_direction", role.endsWith("minus") ? "-" + axis : "+" + axis,
                        "raw_normal_source", "same registered native CV topology readback",
                        "expression", expression, "unit", flux.get("unit"),
                        "integral_w", flux.get("value"), "area_expression", "1",
                        "area_unit", area.get("unit"), "area_m2", area.get("value")));
            }
            volumeReadback = evaluateIntegral(model, "w23cv" + nonce + "v", "IntVolume",
                    datasetTag, solnum, outer, 3, interiorDomainIds, "1", "m^3", numericalTags);
            numericalReadbacks.add(volumeReadback);
            pinReadback = evaluateIntegral(model, "w23cv" + nonce + "p", "EvalGlobal",
                    datasetTag, solnum, outer, -1, new int[0], "ewfd.Pin", "W", numericalTags);
            numericalReadbacks.add(pinReadback);
            if (Boolean.TRUE.equals(qabsCheck.get("integral_authorized"))) {
                Map<String, Object> contract = requiredMap(args.get("q_abs_expression_contract"),
                        "q_abs_expression_contract");
                qabsIntegralReadback = evaluateIntegral(model, "w23cv" + nonce + "q", "IntVolume",
                        datasetTag, solnum, outer, 3, interiorDomainIds,
                        requiredString(contract.get("expression"), "Qabs expression"), "W", numericalTags);
                numericalReadbacks.add(qabsIntegralReadback);
            }
        } catch (RuntimeException error) {
            operationFailure = error.getClass().getName() + ": " + String.valueOf(error.getMessage());
        } finally {
            for (int index = numericalTags.size() - 1; index >= 0; index--) {
                String tag = numericalTags.get(index);
                try {
                    if (containsNumerical(model, tag)) model.result().numerical().remove(tag);
                    if (containsNumerical(model, tag))
                        throw new IllegalStateException("request-owned temporary numerical node remains after removal");
                } catch (RuntimeException error) {
                    remainingTags.add(tag);
                    if (!cleanupFailure.isEmpty()) cleanupFailure += "; ";
                    cleanupFailure += tag + ": " + error.getClass().getName() + ": " + String.valueOf(error.getMessage());
                }
            }
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", "urn:comsol-mcp:w23:cv-power-terms:1.0.0");
        result.put("native_result", "COMSOL_NATIVE_CV_POWER_TERMS_READBACK");
        result.put("study_or_solver_invoked", false);
        result.put("control_volume_topology", freshTopology);
        Map<String, Object> selectedSolutionAfter =
                readSelectedSolutionIdentity(model, datasetTag, solutionTag, outer, inner, solnum);
        if (!selectedSolutionBefore.equals(selectedSolutionAfter))
            throw new IllegalStateException("selected stored-solution identity changed during read-only CV integration");
        result.put("source_identity", selectedSolutionAfter);
        result.put("surface_terms", surfaceTerms);
        result.put("surface_area_by_role_m2", areaByRole);
        result.put("signed_surface_flux_by_role_w", fluxByRole);
        result.put("interior_domain_ids", asList(interiorDomainIds));
        result.put("volume_integral_of_one", volumeReadback == null
                ? unavailableTerm("NOT_AVAILABLE_INTEGRATION_FAILED", "m^3") : volumeReadback);
        result.put("positive_incident_power", pinReadback == null
                ? unavailableTerm("NOT_AVAILABLE_PIN_EVALUATION_FAILED", "W") : pinReadback);
        if (qabsIntegralReadback == null) {
            String expression = (String) qabsCheck.getOrDefault("expression", "NOT_PROVIDED");
            Map<String, Object> unavailable = unavailableTerm(
                    qabsCheck.containsKey("expression")
                            ? "NOT_AVAILABLE_REVIEWED_COLUMN_MAPPING_CERTIFICATE_REQUIRED"
                            : "NOT_AVAILABLE_EXPLICIT_QABS_CONTRACT_REQUIRED", "W");
            unavailable.put("expression", expression);
            result.put("q_abs_volume_integral", unavailable);
        } else {
            result.put("q_abs_volume_integral", qabsIntegralReadback);
        }
        result.put("q_abs_expression_contract", args.get("q_abs_expression_contract"));
        result.put("q_abs_mapping_certificate", args.get("q_abs_mapping_certificate"));
        result.put("q_abs_field_mapping", qabsCheck);
        result.put("normalization", "surface flux / positive native ewfd.Pin");
        result.put("absolute_balance_tolerance", "NOT_FROZEN");
        result.put("producer_step_binding", "UNVERIFIED");
        result.put("producer_step_note", "dataset/solution/tuple are read back; selected tuple to exact Frequency producer step is not inferred from study containment");
        result.put("temporary_numerical_features", numericalReadbacks);
        int remainingCount = remainingTags.size();
        result.put("cleanup", Map.of("created_count", numericalTags.size(),
                "removed_count", numericalTags.size() - remainingCount,
                "removed", remainingCount == 0, "cleanup_failed", remainingCount != 0,
                "remaining_tags", remainingTags,
                "error", cleanupFailure));
        result.put("operation_failure", operationFailure);
        result.put("status", operationFailure.isEmpty() && cleanupFailure.isEmpty()
                ? "RAW_TERMS_READBACK_COMPLETE_QABS_AND_PRODUCER_UNVERIFIED"
                : "INCOMPLETE_CV_POWER_TERMS_READBACK");
        Object residual = null;
        if (qabsIntegralReadback != null && pinReadback != null
                && pinReadback.get("value") instanceof Number
                && ((Number) pinReadback.get("value")).doubleValue() > 0.0
                && fluxByRole.size() == 6) {
            double sum = 0.0;
            for (Object value : fluxByRole.values()) {
                if (!(value instanceof Number) || !Double.isFinite(((Number) value).doubleValue())) {
                    sum = Double.NaN;
                    break;
                }
                sum += ((Number) value).doubleValue();
            }
            if (Double.isFinite(sum) && qabsIntegralReadback.get("value") instanceof Number)
                residual = (sum + ((Number) qabsIntegralReadback.get("value")).doubleValue())
                        / ((Number) pinReadback.get("value")).doubleValue();
        }
        result.put("balance_residual", residual);
        result.put("scientific_acceptance", "NOT_RUN_QABS_MAPPING_PRODUCER_BINDING_AND_ABSOLUTE_TOLERANCE_OPEN");
        return result;
    }

    /** Validate a declared expression's unit and, separately, a reviewed row/column mapping certificate. */
    private static Map<String, Object> validateQabsContractAndCertificate(
            Model model, Map<String, Object> args, Map<String, Object> currentInventory) {
        Object rawContract = args.get("q_abs_expression_contract");
        Object rawCertificate = args.get("q_abs_mapping_certificate");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", "urn:comsol-mcp:w23:qabs-expression-contract:1.0.0");
        result.put("integral_authorized", false);
        result.put("mapping_status", "UNVERIFIED");
        result.put("equation_inventory_matches_source", true);
        if (rawContract == null) {
            if (rawCertificate != null)
                throw new IllegalArgumentException("Qabs mapping certificate cannot be supplied without an expression contract");
            result.put("status", "NO_EXPLICIT_QABS_EXPRESSION_CONTRACT");
            return result;
        }
        Map<String, Object> contract = requiredMap(rawContract, "q_abs_expression_contract");
        if (!"urn:comsol-mcp:w23:qabs-expression-contract:1.0.0".equals(contract.get("schema_id"))
                || !"ewfd".equals(contract.get("physics_tag"))
                || !"W/m^3".equals(contract.get("expected_density_unit"))
                || !"W".equals(contract.get("integral_unit"))
                || Boolean.TRUE.equals(contract.get("declaration_is_mapping_evidence")))
            throw new IllegalArgumentException("Qabs declaration must bind EWFD, density W/m^3, and integral W without claiming mapping proof");
        String expression = requiredString(contract.get("expression"), "q_abs_expression_contract.expression");
        String evaluatedUnit = model.param().evaluateUnit(expression);
        result.put("expression", expression);
        result.put("physics_tag", "ewfd");
        result.put("declared_density_unit", contract.get("expected_density_unit"));
        result.put("evaluated_density_unit", evaluatedUnit);
        if (!"W/m^3".equals(evaluatedUnit))
            throw new IllegalStateException("native evaluateUnit readback does not match the declared Qabs density unit W/m^3");
        if (rawCertificate == null) {
            result.put("status", "UNIT_READBACK_ONLY_MAPPING_CERTIFICATE_REQUIRED");
            result.put("mapping_status", "UNVERIFIED");
            return result;
        }
        if (APPROVED_QABS_SCHEMA_ID.isEmpty() || APPROVED_QABS_CALIBRATION_SHA256.isEmpty()
                || APPROVED_QABS_CALIBRATION_ROUTE_BINDING_SHA256.isEmpty()
                || APPROVED_QABS_APPROVAL_EVIDENCE_SHA256.isEmpty()
                || APPROVED_QABS_EXPRESSION_COLUMN < 0 || APPROVED_QABS_UNIT_COLUMN < 0
                || APPROVED_QABS_MINIMUM_FEATURE_INFO_OWNERS < 2)
            throw new IllegalStateException("no independently approved Qabs column schema is pinned in this source version");
        Map<String, Object> certificate = requiredMap(rawCertificate, "q_abs_mapping_certificate");
        if (!"urn:comsol-mcp:w23:qabs-mapping-certificate:1.0.0".equals(certificate.get("schema_id"))
                || !"PINNED_NATIVE_COLUMN_SCHEMA_EVIDENCE".equals(certificate.get("status"))
                || !APPROVED_QABS_SCHEMA_ID.equals(certificate.get("approved_schema_id"))
                || !"ewfd".equals(certificate.get("physics_tag"))
                || !expression.equals(certificate.get("expression"))
                || !"W/m^3".equals(certificate.get("density_unit"))
                || !sameNestedValues(currentInventory, certificate.get("equation_inventory")))
            throw new IllegalArgumentException("Qabs certificate is detached from the current raw native Equation View inventory");
        Map<String, Object> approvedSchema = requiredMap(certificate.get("approved_schema"), "pinned Qabs schema");
        Map<String, Object> calibration = requiredMap(certificate.get("calibration"), "Qabs calibration evidence");
        Map<String, Object> calibrationSchema = requiredMap(calibration.get("schema_candidate"), "Qabs calibrated schema candidate");
        Map<String, Object> calibrationColumns = requiredMap(calibrationSchema.get("column_indices"), "Qabs calibrated columns");
        Map<String, Object> calibrationCrossFeature = requiredMap(
                calibrationSchema.get("cross_feature_comparison"), "Qabs cross-FeatureInfo comparison");
        Object rawControls = calibrationSchema.get("control_expressions");
        Map<String, Object> approvedColumns = requiredMap(approvedSchema.get("column_indices"), "pinned Qabs columns");
        Map<String, Object> certificateColumns = requiredMap(certificate.get("column_indices"), "Qabs certificate columns");
        if (!"NATIVE_CALIBRATION_ROUTE_VALIDATED".equals(calibration.get("status"))
                || !APPROVED_QABS_CALIBRATION_SHA256.equals(calibration.get("readback_sha256"))
                || !APPROVED_QABS_CALIBRATION_SHA256.equals(approvedSchema.get("calibration_readback_sha256"))
                || !APPROVED_QABS_CALIBRATION_ROUTE_BINDING_SHA256.equals(
                        approvedSchema.get("calibration_route_binding_sha256"))
                || !"6.4.0.293".equals(approvedSchema.get("comsol_version"))
                || !Integer.valueOf(APPROVED_QABS_EXPRESSION_COLUMN).equals(calibrationColumns.get("expression"))
                || !Integer.valueOf(APPROVED_QABS_UNIT_COLUMN).equals(calibrationColumns.get("unit"))
                || !sameNestedValues(approvedColumns, calibrationColumns)
                || !sameNestedValues(approvedColumns, certificateColumns)
                || !"AT_LEAST_TWO_DISTINCT_FEATUREINFO_OWNERS".equals(calibrationCrossFeature.get("status"))
                || !Integer.valueOf(APPROVED_QABS_MINIMUM_FEATURE_INFO_OWNERS)
                        .equals(approvedSchema.get("minimum_distinct_feature_info_owners"))
                || !(calibrationCrossFeature.get("distinct_owner_count") instanceof Number)
                || ((Number) calibrationCrossFeature.get("distinct_owner_count")).intValue()
                        < APPROVED_QABS_MINIMUM_FEATURE_INFO_OWNERS
                || !Arrays.asList(Map.of("expression", "ewfd.Ex", "expected_unit", "V/m"),
                        Map.of("expression", "ewfd.Hx", "expected_unit", "A/m")).equals(rawControls)
                || !APPROVED_QABS_APPROVAL_EVIDENCE_SHA256.equals(approvedSchema.get("approval_evidence_sha256"))
                || !(approvedSchema.get("approval_evidence_sha256") instanceof String)
                || !((String) approvedSchema.get("approval_evidence_sha256")).matches("[0-9a-f]{64}"))
            throw new IllegalArgumentException("Qabs mapping certificate does not match the pinned calibration schema identity");
        Map<String, Object> owner = requiredMap(certificate.get("feature_info_owner"), "Qabs feature-info owner");
        String ownerPath = requiredString(owner.get("parent_path"), "Qabs owner parent_path");
        String ownerTag = requiredString(owner.get("feature_info_tag"), "Qabs owner feature_info_tag");
        int rowIndex = requiredPositiveOrZeroIndex(certificate.get("row_index"), "Qabs row_index");
        Map<String, Object> columns = certificateColumns;
        int expressionColumn = requiredPositiveOrZeroIndex(columns.get("expression"), "Qabs expression column");
        int unitColumn = requiredPositiveOrZeroIndex(columns.get("unit"), "Qabs unit column");
        if (expressionColumn == unitColumn)
            throw new IllegalArgumentException("Qabs expression and unit must use distinct reviewed Equation View columns");
        List<Map<String, Object>> currentControlObservations = new ArrayList<>();
        List<Map<String, Object>> currentControlUnitReadbacks = new ArrayList<>();
        List<Map<String, String>> knownControlSpecs = Arrays.asList(
                Map.of("expression", "ewfd.Ex", "expected_unit", "V/m"),
                Map.of("expression", "ewfd.Hx", "expected_unit", "A/m"));
        for (Map<String, String> control : knownControlSpecs) {
            String controlExpression = (String) control.get("expression");
            String expectedUnit = (String) control.get("expected_unit");
            String controlEvaluatedUnit = model.param().evaluateUnit(controlExpression);
            if (!expectedUnit.equals(controlEvaluatedUnit))
                throw new IllegalStateException("known Qabs schema control expression unit changed");
            Map<String, Object> observed = findEquationExpressionUnit(currentInventory, controlExpression, expectedUnit);
            if (observed == null
                    || !Integer.valueOf(expressionColumn).equals(observed.get("expression_column"))
                    || !Integer.valueOf(unitColumn).equals(observed.get("unit_column")))
                throw new IllegalStateException("known Qabs control row does not match the pinned current column schema");
            currentControlObservations.add(observed);
            currentControlUnitReadbacks.add(Map.of("expression", controlExpression,
                    "expected_unit", expectedUnit, "evaluated_unit", controlEvaluatedUnit,
                    "source", "Model.param().evaluateUnit"));
        }
        Map<String, Object> currentTargetObservation = findEquationExpressionUnit(
                currentInventory, expression, "W/m^3");
        if (currentTargetObservation == null
                || !ownerPath.equals(currentTargetObservation.get("parent_path"))
                || !ownerTag.equals(currentTargetObservation.get("feature_info_tag"))
                || !Integer.valueOf(rowIndex).equals(currentTargetObservation.get("row_index"))
                || !Integer.valueOf(expressionColumn).equals(currentTargetObservation.get("expression_column"))
                || !Integer.valueOf(unitColumn).equals(currentTargetObservation.get("unit_column")))
            throw new IllegalStateException("Qabs target row differs from its pinned current inventory owner or coordinates");
        List<Map<String, Object>> currentAllObservations = new ArrayList<>(currentControlObservations);
        currentAllObservations.add(currentTargetObservation);
        Map<String, Object> currentCrossFeature = crossFeatureSummary(currentAllObservations);
        if (!sameNestedValues(calibrationSchema.get("control_observations"), currentControlObservations)
                || !sameNestedValues(calibrationSchema.get("target_observation"), currentTargetObservation)
                || !sameNestedValues(calibrationCrossFeature, currentCrossFeature))
            throw new IllegalStateException("Qabs calibration control/target/cross-feature evidence differs from current native rows");
        List<?> inventoryEntries = (List<?>) currentInventory.get("entries");
        int ownerMatches = 0, expressionOccurrences = 0;
        List<?> selectedRows = null;
        for (Object rawEntry : inventoryEntries) {
            Map<String, Object> entry = requiredMap(rawEntry, "Equation View entry");
            if (ownerPath.equals(entry.get("parent_path")) && ownerTag.equals(entry.get("feature_info_tag"))) {
                ownerMatches++;
                Object rows = entry.get("rows");
                if (!(rows instanceof List)) throw new IllegalStateException("raw Equation View rows are malformed");
                selectedRows = (List<?>) rows;
            }
            Object rows = entry.get("rows");
            if (!(rows instanceof List)) throw new IllegalStateException("raw Equation View rows are malformed");
            for (Object rawRow : (List<?>) rows) {
                if (!(rawRow instanceof List)) throw new IllegalStateException("raw Equation View row is malformed");
                List<?> cells = (List<?>) rawRow;
                if (expressionColumn < cells.size() && expression.equals(cells.get(expressionColumn)))
                    expressionOccurrences++;
            }
        }
        if (ownerMatches != 1 || selectedRows == null || rowIndex >= selectedRows.size())
            throw new IllegalArgumentException("Qabs owner path/tag/row is absent or ambiguous in the current Equation View");
        Object selectedRow = selectedRows.get(rowIndex);
        if (!(selectedRow instanceof List)) throw new IllegalStateException("selected Qabs Equation View row is malformed");
        List<?> cells = (List<?>) selectedRow;
        if (Math.max(expressionColumn, unitColumn) >= cells.size()
                || !expression.equals(cells.get(expressionColumn))
                || !"W/m^3".equals(cells.get(unitColumn)) || expressionOccurrences != 1)
            throw new IllegalArgumentException("reviewed Qabs expression/unit columns do not uniquely match current raw rows");
        if (certificate.get("equation_inventory_sha256") instanceof String) {
            String sha = (String) certificate.get("equation_inventory_sha256");
            if (!sha.matches("[0-9a-f]{64}"))
                throw new IllegalArgumentException("Qabs Equation View inventory digest is malformed");
        } else {
            throw new IllegalArgumentException("Qabs Equation View inventory digest is missing");
        }
        Map<String, Object> sourceRoute = requiredMap(certificate.get("source_route"), "Qabs source route");
        Map<String, Object> calibrationRoute = requiredMap(calibration.get("route_binding"), "Qabs calibration route binding");
        if (sourceRoute.isEmpty() || calibrationRoute.isEmpty())
            throw new IllegalArgumentException("Qabs schema certificate lacks calibration and current source-route bindings");
        result.put("status", "PINNED_COLUMN_CERTIFICATE_AND_NATIVE_UNIT_READBACK_MATCH");
        result.put("mapping_status", "CERTIFICATE_BOUND_TO_CURRENT_NATIVE_EQUATION_VIEW");
        result.put("approved_schema_id", APPROVED_QABS_SCHEMA_ID);
        result.put("approval_evidence_sha256", approvedSchema.get("approval_evidence_sha256"));
        result.put("equation_inventory_sha256", certificate.get("equation_inventory_sha256"));
        result.put("feature_info_owner", owner);
        result.put("row_index", rowIndex);
        result.put("column_indices", columns);
        result.put("control_unit_readbacks", currentControlUnitReadbacks);
        result.put("control_row_observations", currentControlObservations);
        result.put("target_row_observation", currentTargetObservation);
        result.put("cross_feature_comparison", currentCrossFeature);
        result.put("integral_authorized", true);
        return result;
    }

    /** Read-only native calibration recipe. A candidate never becomes an approved production schema here. */
    private static Map<String, Object> calibrateQabsMapping(Model model, Map<String, Object> args) {
        Map<String, Object> managedIdentity = readManagedIdentity(model, args);
        Map<String, Object> source = requiredMap(args.get("source"), "source");
        String datasetTag = requiredString(source.get("dataset_id"), "source.dataset_id");
        String solutionTag = requiredString(source.get("solution_id"), "source.solution_id");
        int outer = requiredPositiveIndex(source.get("outer_index"), "source.outer_index");
        int inner = requiredPositiveIndex(source.get("inner_index"), "source.inner_index");
        int solnum = requiredPositiveIndex(source.get("solnum"), "source.solnum");
        if (inner != solnum) throw new IllegalArgumentException("Qabs calibration selected inner index must equal solnum");
        Map<String, Object> nativeIdentity = readSelectedSolutionIdentity(
                model, datasetTag, solutionTag, outer, inner, solnum);
        Map<String, Object> stored = requiredMap(nativeIdentity.get("stored_solution"), "native stored solution");
        Map<String, Object> contract = requiredMap(args.get("q_abs_expression_contract"), "q_abs_expression_contract");
        if (!"urn:comsol-mcp:w23:qabs-expression-contract:1.0.0".equals(contract.get("schema_id"))
                || !"ewfd".equals(contract.get("physics_tag"))
                || !"W/m^3".equals(contract.get("expected_density_unit"))
                || !"W".equals(contract.get("integral_unit")))
            throw new IllegalArgumentException("exact EWFD W/m^3 candidate expression contract is required");
        String targetExpression = requiredString(contract.get("expression"), "q_abs_expression_contract.expression");
        List<Map<String, Object>> controls = List.of(
                Map.of("expression", "ewfd.Ex", "expected_unit", "V/m"),
                Map.of("expression", "ewfd.Hx", "expected_unit", "A/m"));
        if (!sameNestedValues(args.get("control_expressions"), controls)
                || !stored.get("computation_version").equals(args.get("comsol_version_expected")))
            throw new IllegalArgumentException("calibration controls/version differ from the frozen route recipe");
        Map<String, Object> inventory = physicsEquationInventory(model.component(COMPONENT).physics("ewfd"));
        List<Map<String, Object>> controlReadbacks = new ArrayList<>();
        List<Map<String, Object>> controlObservations = new ArrayList<>();
        for (Map<String, Object> control : controls) {
            String expression = (String) control.get("expression");
            String expectedUnit = (String) control.get("expected_unit");
            String evaluatedUnit = model.param().evaluateUnit(expression);
            Map<String, Object> observation = findEquationExpressionUnit(inventory, expression, expectedUnit);
            controlReadbacks.add(Map.of("expression", expression, "expected_unit", expectedUnit,
                    "evaluated_unit", evaluatedUnit == null ? "NOT_AVAILABLE" : evaluatedUnit,
                    "source", "Model.param().evaluateUnit"));
            if (observation == null || !expectedUnit.equals(evaluatedUnit)) {
                controlObservations.add(Map.of("expression", expression,
                        "expected_unit", expectedUnit, "row_observation", "NOT_UNIQUE_OR_MISSING"));
            } else {
                controlObservations.add(observation);
            }
        }
        String targetUnit = model.param().evaluateUnit(targetExpression);
        Map<String, Object> targetObservation = findEquationExpressionUnit(
                inventory, targetExpression, "W/m^3");
        List<Map<String, Object>> rowControls = new ArrayList<>();
        for (Map<String, Object> control : controls)
            rowControls.add(Map.of("expression", control.get("expression"),
                    "expected_unit", control.get("expected_unit")));
        List<Map<String, Object>> allObservations = new ArrayList<>(controlObservations);
        if (targetObservation != null) allObservations.add(targetObservation);
        Map<String, Object> crossFeatureComparison = crossFeatureSummary(allObservations);
        Map<String, Object> schemaCandidate = null;
        int[][] locations = new int[3][2];
        boolean complete = controlReadbacks.size() == 2;
        for (int index = 0; index < 2; index++) {
            Map<String, Object> controlReadback = controlReadbacks.get(index);
            Map<String, Object> observation = controlObservations.get(index);
            if (!controlReadback.get("expected_unit").equals(controlReadback.get("evaluated_unit"))
                    || observation.get("row_index") == null) {
                complete = false;
                continue;
            }
            locations[index][0] = ((Number) observation.get("expression_column")).intValue();
            locations[index][1] = ((Number) observation.get("unit_column")).intValue();
        }
        if (targetObservation == null || !"W/m^3".equals(targetUnit)) complete = false;
        if (complete) {
            locations[2][0] = ((Number) targetObservation.get("expression_column")).intValue();
            locations[2][1] = ((Number) targetObservation.get("unit_column")).intValue();
            if (!Arrays.equals(locations[0], locations[1]) || !Arrays.equals(locations[0], locations[2]))
                complete = false;
        }
        if (((Number) crossFeatureComparison.get("distinct_owner_count")).intValue() < 2)
            complete = false;
        if (complete) {
            schemaCandidate = new LinkedHashMap<>();
            schemaCandidate.put("column_indices", Map.of("expression", locations[0][0], "unit", locations[0][1]));
            schemaCandidate.put("control_expressions", rowControls);
            schemaCandidate.put("control_observations", controlObservations);
            schemaCandidate.put("target_observation", targetObservation);
            schemaCandidate.put("cross_feature_comparison", crossFeatureComparison);
        }
        Map<String, Object> targetUnitReadback = new LinkedHashMap<>();
        targetUnitReadback.put("expression", targetExpression);
        targetUnitReadback.put("expected_unit", contract.get("expected_density_unit"));
        targetUnitReadback.put("evaluated_unit", targetUnit == null ? "NOT_AVAILABLE" : targetUnit);
        targetUnitReadback.put("source", "Model.param().evaluateUnit");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_id", "urn:comsol-mcp:w23:qabs-mapping-calibration:1.0.0");
        result.put("native_result", "COMSOL_NATIVE_QABS_MAPPING_CALIBRATION_READBACK");
        result.put("status", schemaCandidate == null
                ? "CANDIDATE_MAPPING_UNRESOLVED" : "CANDIDATE_SCHEMA_NEEDS_INDEPENDENT_REVIEW");
        result.put("study_or_solver_invoked", false);
        result.put("model_mutated", false);
        result.put("managed_identity", managedIdentity);
        result.put("dataset_tag", datasetTag);
        result.put("solution_tag", solutionTag);
        result.put("selected_tuple", Map.of("outer_index", outer, "inner_index", inner, "solnum", solnum));
        result.put("native_source_identity", nativeIdentity);
        result.put("comsol_version", stored.get("computation_version"));
        result.put("q_abs_expression_contract", contract);
        result.put("control_expressions", rowControls);
        result.put("control_unit_readbacks", controlReadbacks);
        result.put("control_row_observations", controlObservations);
        result.put("equation_view_inventory", inventory);
        result.put("target_unit_readback", targetUnitReadback);
        result.put("target_row_observation", targetObservation);
        result.put("cross_feature_comparison", crossFeatureComparison);
        result.put("schema_candidate", schemaCandidate);
        result.put("production_schema_approval", "NOT_APPROVED_IN_CALIBRATION_ROUTE");
        return result;
    }

    private static Map<String, Object> crossFeatureSummary(List<Map<String, Object>> observations) {
        Map<String, Map<String, Object>> owners = new LinkedHashMap<>();
        for (Map<String, Object> observation : observations) {
            Object rawPath = observation.get("parent_path");
            Object rawTag = observation.get("feature_info_tag");
            if (!(rawPath instanceof String) || !(rawTag instanceof String)) continue;
            String key = rawPath + "\u0000" + rawTag;
            Map<String, Object> owner = new LinkedHashMap<>();
            owner.put("parent_path", rawPath);
            owner.put("feature_info_tag", rawTag);
            owners.put(key, owner);
        }
        List<String> keys = new ArrayList<>(owners.keySet());
        Collections.sort(keys);
        List<Map<String, Object>> rows = new ArrayList<>();
        for (String key : keys) rows.add(owners.get(key));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", rows.size() >= 2
                ? "AT_LEAST_TWO_DISTINCT_FEATUREINFO_OWNERS"
                : "INSUFFICIENT_DISTINCT_FEATUREINFO_OWNERS");
        result.put("distinct_owner_count", rows.size());
        result.put("owners", rows);
        return result;
    }

    private static Map<String, Object> readManagedIdentity(Model model, Map<String, Object> args) {
        Map<String, Object> supplied = requiredMap(args.get("managed_identity"), "managed_identity");
        String projectId = requiredString(supplied.get("project_id"), "project_id");
        String modelTag = requiredString(supplied.get("model_tag"), "model_tag");
        if (!modelTag.equals(model.tag()))
            throw new IllegalStateException("managed Model tag differs from the native Model");
        Map<String, Object> modelRef = requiredMap(supplied.get("model_ref"), "managed ModelRef");
        Object rawRevision = supplied.get("expected_revision");
        if (!(rawRevision instanceof Number) || rawRevision instanceof Boolean
                || ((Number) rawRevision).doubleValue() != ((Number) rawRevision).longValue()
                || ((Number) rawRevision).longValue() < 0)
            throw new IllegalArgumentException("managed expected revision must be a nonnegative integer");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("project_id", projectId);
        result.put("model_ref", modelRef);
        result.put("model_tag", modelTag);
        result.put("expected_revision", ((Number) rawRevision).longValue());
        return result;
    }

    private static Map<String, Object> findEquationExpressionUnit(
            Map<String, Object> inventory, String expression, String expectedUnit) {
        List<?> entries = (List<?>) inventory.get("entries");
        List<Map<String, Object>> hits = new ArrayList<>();
        for (Object rawEntry : entries) {
            Map<String, Object> entry = requiredMap(rawEntry, "Equation View entry");
            Object rawRows = entry.get("rows");
            if (!(rawRows instanceof List)) throw new IllegalStateException("Equation View rows are unavailable");
            List<?> rows = (List<?>) rawRows;
            for (int rowIndex = 0; rowIndex < rows.size(); rowIndex++) {
                Object rawRow = rows.get(rowIndex);
                if (!(rawRow instanceof List)) throw new IllegalStateException("Equation View row is malformed");
                List<?> row = (List<?>) rawRow;
                for (int expressionColumn = 0; expressionColumn < row.size(); expressionColumn++) {
                    if (!expression.equals(row.get(expressionColumn))) continue;
                    for (int unitColumn = 0; unitColumn < row.size(); unitColumn++) {
                        if (expressionColumn == unitColumn || !expectedUnit.equals(row.get(unitColumn))) continue;
                        Map<String, Object> hit = new LinkedHashMap<>();
                        hit.put("parent_path", entry.get("parent_path"));
                        hit.put("feature_info_tag", entry.get("feature_info_tag"));
                        hit.put("row_index", rowIndex);
                        hit.put("expression_column", expressionColumn);
                        hit.put("unit_column", unitColumn);
                        hits.add(hit);
                    }
                }
            }
        }
        return hits.size() == 1 ? hits.get(0) : null;
    }

    private static Map<String, Object> evaluateIntegral(
            Model model, String tag, String type, String datasetTag, int solnum, int outer,
            int dimension, int[] entities, String expression, String resultUnit,
            List<String> ownedTags) {
        if (containsNumerical(model, tag))
            throw new IllegalStateException("refusing to overwrite a preexisting numerical result node: " + tag);
        NumericalFeature feature = model.result().numerical().create(tag, type);
        ownedTags.add(tag);
        try {
            feature.set("data", datasetTag);
            feature.set("expr", new String[]{expression});
            feature.set("unit", new String[]{resultUnit});
            feature.set("innerinput", "manual");
            feature.set("solnum", Integer.toString(solnum));
            feature.set("outerinput", "manual");
            feature.set("outersolnum", Integer.toString(outer));
            feature.set("solrepresentation", "solnum");
            if (dimension >= 0) {
                feature.selection().geom(GEOMETRY, dimension);
                feature.selection().set(entities);
            }
            String[] expressionReadback = feature.getStringArray("expr");
            String[] unitReadback = feature.getStringArray("unit");
            if (!datasetTag.equals(feature.getString("data"))
                    || expressionReadback == null || expressionReadback.length != 1
                    || !expression.equals(expressionReadback[0])
                    || unitReadback == null || unitReadback.length != 1 || !resultUnit.equals(unitReadback[0])
                    || !"manual".equals(feature.getString("innerinput"))
                    || !Integer.toString(solnum).equals(feature.getString("solnum"))
                    || !"manual".equals(feature.getString("outerinput"))
                    || !Integer.toString(outer).equals(feature.getString("outersolnum"))
                    || !"solnum".equals(feature.getString("solrepresentation")))
                throw new IllegalStateException("native integral data/expression/unit/SolutionSpec readback differs");
            if (dimension >= 0 && (feature.selection().dim() != dimension
                    || !samePositiveIds(entities, feature.selection().entities())))
                throw new IllegalStateException("native integral geometry dimension/entity selection differs");
            feature.run();
            if (feature.isComplex()) throw new IllegalStateException("power/area/Pin integral unexpectedly returned complex values");
            double[][] values = feature.getReal();
            if (values == null || values.length != 1 || values[0] == null || values[0].length != 1
                    || !Double.isFinite(values[0][0]))
                throw new IllegalStateException("native integral did not return one finite value for the selected tuple");
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("tag", tag);
            result.put("feature_type", feature.getType());
            result.put("dataset", feature.getString("data"));
            result.put("expression", Arrays.asList(feature.getStringArray("expr")));
            result.put("unit", feature.getStringArray("unit")[0]);
            result.put("innerinput", feature.getString("innerinput"));
            result.put("solnum", feature.getString("solnum"));
            result.put("outerinput", feature.getString("outerinput"));
            result.put("outersolnum", feature.getString("outersolnum"));
            result.put("solrepresentation", feature.getString("solrepresentation"));
            result.put("geometry", dimension < 0 ? "GLOBAL" : feature.selection().geom());
            result.put("entity_dimension", dimension);
            result.put("entity_ids", dimension < 0 ? List.of() : boxedIds(feature.selection().entities()));
            result.put("complex", false);
            result.put("result_shape", List.of(1, 1));
            result.put("value", values[0][0]);
            return result;
        } finally {
            // The caller owns the cleanup ledger and performs/records removal.
        }
    }

    private static Map<String, Object> unavailableTerm(String status, String unit) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("status", status);
        value.put("unit", unit);
        value.put("value", null);
        value.put("expression", "NOT_PROVIDED");
        return value;
    }

    /** Preserve raw FeatureInfo rows and owner paths without inventing column labels. */
    private static Map<String, Object> physicsEquationInventory(Physics physics) {
        List<Map<String, Object>> entries = new ArrayList<>();
        collectPhysicsEquationEntries(physics, physics.tag(), "Physics", entries);
        for (String tag : physics.feature().tags())
            collectPhysicsEquationTree(physics.feature(tag), tag, entries);
        Map<String, Object> output = new LinkedHashMap<>();
        output.put("schema_id", "urn:comsol-mcp:w23:physics-equation-view-raw-inventory:1.0.0");
        output.put("physics_tag", physics.tag());
        output.put("feature_info_table_id", "Expression");
        output.put("options", Arrays.asList("all"));
        output.put("column_semantics", "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE");
        output.put("mapping_authentication", "NOT_AUTHENTICATED");
        output.put("entries", entries);
        return output;
    }

    private static void collectPhysicsEquationTree(
            PhysicsFeature feature, String path, List<Map<String, Object>> entries) {
        collectPhysicsEquationEntries(feature, path, feature.getType(), entries);
        for (String child : feature.feature().tags())
            collectPhysicsEquationTree(feature.feature(child), path + "/" + child, entries);
    }

    private static void collectPhysicsEquationEntries(
            com.comsol.model.physics.EquationViewParent parent, String parentPath,
            String parentType, List<Map<String, Object>> entries) {
        FeatureInfoList list = parent.featureInfo();
        if (list == null || list.tags() == null)
            throw new IllegalStateException("Equation View FeatureInfo tag inventory is unavailable");
        for (String tag : list.tags()) {
            FeatureInfo info = parent.featureInfo(tag);
            if (info == null)
                throw new IllegalStateException("Equation View FeatureInfo entry disappeared during readback");
            String[][] rawRows = info.getInfoTable("Expression", "all");
            if (rawRows == null) throw new IllegalStateException("FeatureInfo returned no raw Expression table");
            List<List<String>> rows = new ArrayList<>();
            List<Integer> widths = new ArrayList<>();
            for (String[] rawRow : rawRows) {
                if (rawRow == null) throw new IllegalStateException("FeatureInfo Expression table contains a null row");
                List<String> row = new ArrayList<>(Arrays.asList(rawRow));
                rows.add(row);
                widths.add(row.size());
            }
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("parent_path", parentPath);
            entry.put("parent_type", parentType);
            entry.put("feature_info_tag", info.tag());
            entry.put("feature_info_name", info.name());
            entry.put("table_id", "Expression");
            entry.put("options", Arrays.asList("all"));
            entry.put("row_count", rows.size());
            entry.put("row_widths", widths);
            entry.put("rows", rows);
            entry.put("column_semantics", "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE");
            entries.add(entry);
        }
    }

    private static Map<String, Object> sourceFieldSource(Map<String, Object> readback) {
        Map<String, Object> result = new LinkedHashMap<>();
        Object source = readback.get("source");
        if (!(source instanceof Map)) return result;
        Map<?, ?> value = (Map<?, ?>) source;
        for (String key : new String[]{"dataset_id", "solution_id", "outer_index", "inner_index", "solnum"})
            result.put(key, value.get(key));
        return result;
    }

    private static boolean sourceTagAndModelMatch(Model model, Map<String, Object> source,
            Map<String, Object> cohortBefore) {
        Map<String, Object> dataset = requiredMap(cohortBefore.get("dataset"), "source cohort dataset");
        Map<String, Object> stored = requiredMap(cohortBefore.get("stored_solution"), "source cohort stored solution");
        Map<String, Object> datasetProperties = requiredMap(dataset.get("properties"), "dataset properties");
        Map<String, Object> selected = requiredMap(stored.get("selected_tuple"), "selected tuple");
        Map<String, Object> fixture = requiredMap(cohortBefore.get("fixture_explicit_configuration"), "fixture configuration");
        Map<String, Object> parameters = requiredMap(fixture.get("parameters"), "fixture parameters");
        for (String key : new String[]{"lambda0", "f0", "w23Ncore", "w23Nclad", "w23Nlens",
                "w23CoreR", "w23CladR", "w23LensR", "w23AirHalfY", "w23AirHalfZ", "w23PmlT",
                "w23XIn", "w23XInEnd", "w23XOutStart", "w23XOut", "w23XDomainMax",
                "w23OutDy", "w23OutDz", "w23ThetaY", "w23ThetaZ"}) {
            if (!(parameters.get(key) instanceof String) || ((String) parameters.get(key)).trim().isEmpty()) return false;
        }
        return "Solution".equals(dataset.get("feature_type"))
                && sameNestedValues(source.get("dataset_id"), dataset.get("tag"))
                && sameNestedValues(source.get("solution_id"), datasetProperties.get("solution"))
                && sameNestedValues(source.get("solution_id"), stored.get("solution_tag"))
                && sameNestedValues(source.get("solution_id"), selected.get("solver_sequence_tag"))
                && sameNestedValues(source.get("outer_index"), selected.get("outer_index"))
                && sameNestedValues(source.get("inner_index"), selected.get("inner_index"))
                && sameNestedValues(source.get("solnum"), selected.get("solnum"));
    }

    private static Map<String, Object> readSelectedSolutionIdentity(
            Model model, String datasetTag, String solutionTag, int outer, int inner, int solnum) {
        PropFeature dataset = model.result().dataset(datasetTag);
        SolverSequence sequence = model.sol(solutionTag);
        String studyTag = sequence.study();
        if (!"Solution".equals(dataset.getType()) || !dataset.hasProperty("solution")
                || !solutionTag.equals(dataset.getString("solution"))
                || studyTag == null || !Arrays.asList(model.study().tags()).contains(studyTag))
            throw new IllegalStateException("selected result dataset is not bound to its actual stored solver sequence/study");
        SolutionInfo info = sequence.getSolutioninfo();
        if (!info.isValid() || sequence.isEmpty())
            throw new IllegalStateException("selected native stored solution is invalid or empty");
        int[] outerValues = info.getOuterSolnum();
        if (outerValues == null || !contains(outerValues, outer))
            throw new IllegalStateException("SolutionInfo outer index is not present");
        int[] innerValues = info.getSolnum(outer, true);
        if (innerValues == null || !contains(innerValues, inner) || inner != solnum
                || !solutionTag.equals(info.getSolverSequence(outer)))
            throw new IllegalStateException("SolutionInfo exact inner/solnum tuple is not present for this sequence");
        long computationDate = model.study(studyTag).getLastComputationDate();
        String computationVersion = model.study(studyTag).getLastComputationVersion();
        if (computationDate <= 0 || computationVersion == null || computationVersion.trim().isEmpty())
            throw new IllegalStateException("stored solution computation date/version readback is missing");
        String[] names = sequence.getPNames();
        double[] values = sequence.getPVals();
        if (names == null || values == null || names.length != values.length)
            throw new IllegalStateException("stored-solution parameter axis readback is incomplete");
        List<Map<String, Object>> parameterAxis = new ArrayList<>();
        for (int index = 0; index < names.length; index++) {
            if (names[index] == null || names[index].trim().isEmpty() || !Double.isFinite(values[index]))
                throw new IllegalStateException("stored-solution parameter axis is malformed");
            parameterAxis.add(Map.of("name", names[index], "value", values[index]));
        }
        Map<String, Object> selected = Map.of("outer_index", outer, "inner_index", inner,
                "solnum", solnum, "solver_sequence_tag", solutionTag);
        Map<String, Object> result = new LinkedHashMap<>();
        Map<String, Object> datasetIdentity = new LinkedHashMap<>();
        datasetIdentity.put("tag", datasetTag);
        datasetIdentity.put("feature_type", dataset.getType());
        Map<String, String> datasetProperties = new LinkedHashMap<>();
        for (String property : new String[]{"solution", "data", "solnum", "outersolnum"})
            if (dataset.hasProperty(property)) datasetProperties.put(property, dataset.getString(property));
        datasetIdentity.put("properties", datasetProperties);

        Map<String, Object> solutionInfo = new LinkedHashMap<>();
        solutionInfo.put("is_valid", info.isValid());
        solutionInfo.put("solver_sequence_is_empty", sequence.isEmpty());
        solutionInfo.put("outer_solnums", asList(outerValues));
        List<Map<String, Object>> solutionPairs = intListRows(info, outerValues, solutionTag);
        solutionInfo.put("solution_pairs", solutionPairs);
        solutionInfo.put("pair_count", solutionPairs.size());
        Map<String, Object> storedSolution = new LinkedHashMap<>();
        storedSolution.put("solution_tag", solutionTag);
        storedSolution.put("study_tag", studyTag);
        storedSolution.put("computation_date_ms", computationDate);
        storedSolution.put("computation_version", computationVersion);
        storedSolution.put("parameter_axis", parameterAxis);
        storedSolution.put("solution_info", solutionInfo);
        storedSolution.put("selected_tuple", selected);
        result.put("dataset", datasetIdentity);
        result.put("stored_solution", storedSolution);
        List<Map<String, Object>> steps = new ArrayList<>();
        for (String tag : model.study(studyTag).feature().tags()) {
            StudyFeature feature = model.study(studyTag).feature(tag);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tag", tag);
            row.put("feature_type", feature.getType());
            for (String property : new String[]{"PortName", "modeFreq", "plist"})
                if (feature.hasProperty(property)) row.put(property, feature.getString(property));
            if (feature.hasProperty("neigs")) row.put("neigs", feature.getInt("neigs"));
            steps.add(row);
        }
        result.put("producer_study_tag", studyTag);
        result.put("producer_study_steps", steps);
        result.put("producer_step_binding", "UNVERIFIED");
        result.put("selected_tuple_to_frequency_step", "UNVERIFIED");
        return result;
    }

    private static boolean sourceIdentityMatchesCohort(
            Map<String, Object> identity, Map<String, Object> cohort) {
        Object expectedDataset = cohort.get("dataset");
        Object expectedSolution = cohort.get("stored_solution");
        return expectedDataset instanceof Map && expectedSolution instanceof Map
                && sameNestedValues(identity.get("dataset"), expectedDataset)
                && sameNestedValues(identity.get("stored_solution"), expectedSolution);
    }

    private static boolean sameNestedValues(Object left, Object right) {
        if (left == right) return true;
        if (left == null || right == null) return false;
        if (left instanceof Number || right instanceof Number) {
            if (!(left instanceof Number) || !(right instanceof Number)
                    || left instanceof Boolean || right instanceof Boolean) return false;
            double a = ((Number) left).doubleValue();
            double b = ((Number) right).doubleValue();
            return Double.isFinite(a) && Double.isFinite(b) && Double.compare(a, b) == 0;
        }
        if (left instanceof Map && right instanceof Map) {
            Map<?, ?> a = (Map<?, ?>) left, b = (Map<?, ?>) right;
            if (!a.keySet().equals(b.keySet())) return false;
            for (Object key : a.keySet()) if (!sameNestedValues(a.get(key), b.get(key))) return false;
            return true;
        }
        if (left instanceof List && right instanceof List) {
            List<?> a = (List<?>) left, b = (List<?>) right;
            if (a.size() != b.size()) return false;
            for (int index = 0; index < a.size(); index++)
                if (!sameNestedValues(a.get(index), b.get(index))) return false;
            return true;
        }
        return left.equals(right);
    }

    private static List<Map<String, Object>> intListRows(SolutionInfo info, int[] outers, String sequenceTag) {
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int outer : outers) {
            int[] inners = info.getSolnum(outer, true);
            if (inners == null) throw new IllegalStateException("SolutionInfo inner-solution axis is unavailable");
            for (int inner : inners) rows.add(Map.of("outer_index", outer, "inner_index", inner,
                    "solnum", inner, "solver_sequence_tag", sequenceTag));
        }
        return rows;
    }

    private static boolean contains(int[] values, int expected) {
        for (int value : values) if (value == expected) return true;
        return false;
    }

    private static Map<String, Object> requiredMap(Object value, String label) {
        if (!(value instanceof Map)) throw new IllegalArgumentException(label + " must be an object");
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
            if (!(entry.getKey() instanceof String)) throw new IllegalArgumentException(label + " keys must be strings");
            result.put((String) entry.getKey(), entry.getValue());
        }
        return result;
    }

    private static String requiredString(Object value, String label) {
        if (!(value instanceof String) || ((String) value).trim().isEmpty())
            throw new IllegalArgumentException(label + " must be a nonempty string");
        return (String) value;
    }

    private static int requiredPositiveIndex(Object value, String label) {
        if (!(value instanceof Number) || value instanceof Boolean
                || ((Number) value).doubleValue() != ((Number) value).intValue()
                || ((Number) value).intValue() < 1)
            throw new IllegalArgumentException(label + " must be a positive exact integer");
        return ((Number) value).intValue();
    }

    private static int requiredPositiveOrZeroIndex(Object value, String label) {
        if (!(value instanceof Number) || value instanceof Boolean
                || ((Number) value).doubleValue() != ((Number) value).intValue()
                || ((Number) value).intValue() < 0)
            throw new IllegalArgumentException(label + " must be a nonnegative exact integer");
        return ((Number) value).intValue();
    }

    private static int[] listToIntArray(Object value) {
        if (!(value instanceof List)) throw new IllegalArgumentException("native ID list is required");
        List<?> rows = (List<?>) value;
        int[] result = new int[rows.size()];
        for (int index = 0; index < rows.size(); index++)
            result[index] = requiredPositiveIndex(rows.get(index), "native ID");
        return result;
    }

    private static int[] toIntArray(List<Integer> values) {
        int[] result = new int[values.size()];
        for (int index = 0; index < values.size(); index++) result[index] = values.get(index);
        return result;
    }

    private static boolean samePositiveIds(int[] left, int[] right) {
        if (left == null || right == null || left.length != right.length) return false;
        int[] a = left.clone(), b = right.clone();
        Arrays.sort(a); Arrays.sort(b);
        for (int index = 0; index < a.length; index++)
            if (a[index] < 1 || (index > 0 && a[index] == a[index - 1]) || a[index] != b[index]) return false;
        return true;
    }

    private static List<Integer> boxedIds(int[] values) { return asList(values); }

    private static boolean containsNumerical(Model model, String tag) {
        return Arrays.asList(model.result().numerical().tags()).contains(tag);
    }

    private static double[] selectedBoundaryBoundingBox(
            GeomMeasure measure, String geometryTag, int boundaryId) {
        measure.selection().set(geometryTag, 2, boundaryId);
        double[] bounds;
        try {
            bounds = measure.getBoundingBox();
        } finally {
            measure.selection().clear(geometryTag);
        }
        if (bounds == null || bounds.length != 6)
            throw new IllegalStateException("native CV boundary bounding box is malformed");
        return bounds.clone();
    }

    private static boolean bboxWithinCv(double[] bbox) {
        if (bbox == null || bbox.length != 6) return false;
        for (int axis = 0; axis < 3; axis++) {
            if (!Double.isFinite(bbox[2 * axis]) || !Double.isFinite(bbox[2 * axis + 1])
                    || bbox[2 * axis] > bbox[2 * axis + 1]
                    || bbox[2 * axis] < CV_LIMITS_UM[axis][0] - CV_GEOMETRY_TOLERANCE_UM
                    || bbox[2 * axis + 1] > CV_LIMITS_UM[axis][1] + CV_GEOMETRY_TOLERANCE_UM)
                return false;
        }
        return true;
    }

    private static List<List<Double>> cvBoxReadback() {
        List<List<Double>> rows = new ArrayList<>();
        for (double[] bounds : CV_LIMITS_UM) rows.add(Arrays.asList(bounds[0], bounds[1]));
        return rows;
    }

    private static double[] selectedVertexCoordinate(
            GeomMeasure measure, String geometryTag, int vertexId) {
        measure.selection().set(geometryTag, 0, vertexId);
        double[] coordinate = measure.getVtxCoord();
        measure.selection().clear(geometryTag);
        if (coordinate == null || coordinate.length != 3)
            throw new IllegalStateException("native CV vertex coordinate readback is malformed");
        return coordinate;
    }

    private static double[] selectedDomainBoundingBox(
            GeomMeasure measure, String geometryTag, int domainId) {
        measure.selection().set(geometryTag, 3, domainId);
        double[] bounds;
        try {
            bounds = measure.getBoundingBox();
        } finally {
            measure.selection().clear(geometryTag);
        }
        if (bounds == null || bounds.length != 6)
            throw new IllegalStateException("native adjacent-domain bounding box is malformed");
        return bounds.clone();
    }

    static List<double[]> edgeCurveSamples(GeomSequence geom, int edgeId, boolean reverse) {
        double[] range = geom.edgeParamRange(edgeId);
        if (range == null || range.length != 2)
            throw new IllegalStateException("native CV edge parameter range is malformed");
        double low = Math.min(range[0], range[1]);
        double high = Math.max(range[0], range[1]);
        return adaptiveCurveSamples(parameters -> geom.edgeX(edgeId, parameters), low, high,
                reverse, CV_EDGE_SAMPLE_TOLERANCE_UM, CV_EDGE_SAMPLE_MAX_DEPTH,
                CV_EDGE_SAMPLE_INITIAL_SEGMENTS);
    }

    interface EdgeCurveEvaluator {
        double[][] evaluate(double[] parameters);
    }

    static List<double[]> adaptiveCurveSamples(
            EdgeCurveEvaluator evaluator, double low, double high, boolean reverse,
            double maxDeviation, int maxDepth, int initialSegments) {
        if (evaluator == null || !Double.isFinite(low) || !Double.isFinite(high) || !(high > low)
                || !Double.isFinite(maxDeviation) || !(maxDeviation > 0.0)
                || maxDepth < 1 || maxDepth > 20 || initialSegments < 1 || initialSegments > 256)
            throw new IllegalArgumentException("adaptive edge curve sampling contract is invalid");
        List<double[]> samples = new ArrayList<>();
        double previousParameter = low;
        double[] previousPoint = evaluateCurve(evaluator, new double[] {low})[0];
        samples.add(previousPoint.clone());
        for (int segment = 0; segment < initialSegments; segment++) {
            double nextParameter = low + (high - low) * (segment + 1.0) / initialSegments;
            double[] nextPoint = evaluateCurve(evaluator, new double[] {nextParameter})[0];
            appendAdaptiveCurveSegment(evaluator, previousParameter, nextParameter,
                    previousPoint, nextPoint, maxDeviation, maxDepth, 0, samples);
            previousParameter = nextParameter;
            previousPoint = nextPoint;
        }
        if (samples.size() > 65537)
            throw new IllegalStateException("native CV edge curve exceeded the bounded adaptive sample count");
        if (reverse) java.util.Collections.reverse(samples);
        return samples;
    }

    private static void appendAdaptiveCurveSegment(
            EdgeCurveEvaluator evaluator, double low, double high,
            double[] lowPoint, double[] highPoint, double maxDeviation,
            int maxDepth, int depth, List<double[]> samples) {
        double span = high - low;
        double[] fractions = {0.25, 0.5, 0.75};
        double[] parameters = {low + span * fractions[0], low + span * fractions[1],
                low + span * fractions[2]};
        double[][] points = evaluateCurve(evaluator, parameters);
        double maxObservedDeviation = 0.0;
        for (int index = 0; index < points.length; index++) {
            double fraction = fractions[index];
            double deviationSquared = 0.0;
            for (int axis = 0; axis < 3; axis++) {
                double linearValue = lowPoint[axis] + fraction * (highPoint[axis] - lowPoint[axis]);
                double difference = points[index][axis] - linearValue;
                deviationSquared += difference * difference;
            }
            maxObservedDeviation = Math.max(maxObservedDeviation, Math.sqrt(deviationSquared));
        }
        if (maxObservedDeviation > maxDeviation) {
            if (depth >= maxDepth)
                throw new IllegalStateException("native CV edge curve failed the frozen chord-deviation tolerance");
            double middle = parameters[1];
            double[] middlePoint = points[1];
            appendAdaptiveCurveSegment(evaluator, low, middle, lowPoint, middlePoint,
                    maxDeviation, maxDepth, depth + 1, samples);
            appendAdaptiveCurveSegment(evaluator, middle, high, middlePoint, highPoint,
                    maxDeviation, maxDepth, depth + 1, samples);
            return;
        }
        samples.add(highPoint.clone());
        if (samples.size() > 65537)
            throw new IllegalStateException("native CV edge curve exceeded the bounded adaptive sample count");
    }

    private static double[][] evaluateCurve(EdgeCurveEvaluator evaluator, double[] parameters) {
        double[][] points = evaluator.evaluate(parameters);
        if (points == null || points.length != parameters.length)
            throw new IllegalStateException("native CV edge curve sampling returned an incomplete array");
        double[][] copy = new double[points.length][3];
        for (int row = 0; row < points.length; row++) {
            if (points[row] == null || points[row].length != 3)
                throw new IllegalStateException("native CV edge curve point is malformed");
            for (int axis = 0; axis < 3; axis++) {
                if (!Double.isFinite(points[row][axis]))
                    throw new IllegalStateException("native CV edge curve point is nonfinite");
                copy[row][axis] = points[row][axis];
            }
        }
        return copy;
    }

    private static List<List<Map<String, Object>>> orderEdgeLoops(List<Map<String, Object>> edges) {
        List<Map<String, Object>> remaining = new ArrayList<>(edges);
        List<List<Map<String, Object>>> loops = new ArrayList<>();
        while (!remaining.isEmpty()) {
            Map<String, Object> first = remaining.remove(0);
            List<Map<String, Object>> loop = new ArrayList<>();
            loop.add(first);
            int start = (Integer) first.get("start_vertex_id");
            int end = (Integer) first.get("end_vertex_id");
            int steps = 0;
            while (end != start) {
                int match = -1;
                for (int index = 0; index < remaining.size(); index++) {
                    if (((Integer) remaining.get(index).get("start_vertex_id")) == end) {
                        if (match >= 0)
                            throw new IllegalStateException("native CV boundary edge graph branches");
                        match = index;
                    }
                }
                if (match < 0) throw new IllegalStateException("native CV boundary edge graph has a gap");
                Map<String, Object> next = remaining.remove(match);
                loop.add(next);
                end = (Integer) next.get("end_vertex_id");
                if (++steps > edges.size()) throw new IllegalStateException("native CV edge loop did not close");
            }
            loops.add(loop);
        }
        return loops;
    }

    private static boolean samePoint(double[] a, double[] b, double tolerance) {
        if (a == null || b == null || a.length != 3 || b.length != 3) return false;
        double norm = 0.0;
        for (int i = 0; i < 3; i++) norm += (a[i] - b[i]) * (a[i] - b[i]);
        return Math.sqrt(norm) <= tolerance;
    }

    private static Map<Integer, String> materialDomainReadback(ModelNode component) {
        Map<Integer, String> materialByDomain = new HashMap<>();
        for (String materialTag : component.material().tags()) {
            int[] domains = component.material(materialTag).selection().entities(3);
            for (int domain : domains) {
                String previous = materialByDomain.put(domain, materialTag);
                if (previous != null && !previous.equals(materialTag))
                    throw new IllegalStateException("multiple native materials claim one CV domain");
            }
        }
        return materialByDomain;
    }

    private static Set<Integer> pmlDomainReadback(ModelNode component) {
        Set<Integer> domains = new LinkedHashSet<>();
        for (String tag : component.coordSystem().tags()) {
            Coordsys feature = component.coordSystem(tag);
            if ("PML".equals(feature.getType()))
                for (int domain : feature.selection().entities(3)) domains.add(domain);
        }
        return domains;
    }

    private static Set<Integer> portBoundaryReadback(ModelNode component) {
        Set<Integer> boundaries = new LinkedHashSet<>();
        for (String physicsTag : component.physics().tags()) {
            Physics physics = component.physics(physicsTag);
            for (String featureTag : physics.feature().tags()) {
                PhysicsFeature feature = physics.feature(featureTag);
                if ("Port".equals(feature.getType()))
                    for (int boundary : feature.selection().entities(2)) boundaries.add(boundary);
            }
        }
        return boundaries;
    }

    private static void requireUniquePositive(int[] values, String label) {
        if (values == null || values.length == 0)
            throw new IllegalArgumentException(label + " are required");
        Set<Integer> seen = new HashSet<>();
        for (int value : values)
            if (value < 1 || !seen.add(value))
                throw new IllegalArgumentException(label + " must be unique positive entity IDs");
    }

    private static List<Integer> asList(int[] values) {
        List<Integer> result = new ArrayList<>();
        for (int value : values) result.add(value);
        return result;
    }
}
