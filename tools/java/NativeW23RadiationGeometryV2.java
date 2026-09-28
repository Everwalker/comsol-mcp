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

    private NativeW23RadiationGeometryV2() {}

    /** Public managed-Java entry point for read-only CV inventory and sampled power terms. */
    public static Object run(Model model, Map<String, Object> args) {
        if (model == null || args == null) throw new IllegalArgumentException("model and arguments are required");
        String phase = String.valueOf(args.get("phase"));
        if ("read_cv_topology".equals(phase))
            return readControlVolumeTopologyForBox(model, COMPONENT, GEOMETRY);
        if ("cv_power_terms".equals(phase))
            return evaluateControlVolumePowerTerms(model, args);
        throw new IllegalArgumentException("phase must be read_cv_topology or cv_power_terms");
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
        result.put("q_abs_volume_integral", unavailableTerm(
                "NOT_AVAILABLE_NATIVE_PHYSICS_FIELD_MAPPING_REQUIRED", "W"));
        result.put("q_abs_field_mapping", Map.of(
                "status", "UNVERIFIED", "expression", "NOT_PROVIDED",
                "unit", "NOT_PROVIDED", "required_evidence", "same-model EWFD equation/field definition and native unit readback"));
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
        result.put("balance_residual", null);
        result.put("scientific_acceptance", "NOT_RUN_QABS_MAPPING_PRODUCER_BINDING_AND_ABSOLUTE_TOLERANCE_OPEN");
        return result;
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
