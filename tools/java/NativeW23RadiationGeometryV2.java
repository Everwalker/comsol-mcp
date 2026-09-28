import com.comsol.model.Coordsys;
import com.comsol.model.GeomFeature;
import com.comsol.model.GeomMeasure;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.ModelNode;
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

    private NativeW23RadiationGeometryV2() {}

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
        result.put("faces", faces);
        result.put("area_diagnostic_method", "GeomMeasure.getArea is approximate per COMSOL 6.4 Programming Reference Manual p.277; not used as final area proof");
        result.put("native_readback_only", true);
        result.put("native_surface_integral_of_one", "NOT_RUN");
        result.put("power_balance", "NOT_RUN");
        return result;
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
