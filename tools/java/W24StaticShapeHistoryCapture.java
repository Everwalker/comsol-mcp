import com.comsol.model.DatasetFeature;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.NumericalFeature;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverFeatureList;
import com.comsol.model.SolverSequence;
import com.comsol.model.Study;
import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.channels.Channels;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
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

/**
 * Read-only capture of every stored W24 static-shape solution field/time.
 * COMSOL 6.4 NumericalFeature.getData() ordering is data[expr][solnum][vertex]
 * (NumericalFeature.html, doc 7657/chunk 22986, SHA-256
 * 5f0ccbd71e184ae3a806a1ce344a00f9ac4de270da774da5d9163058b1ecd8d1);
 * SolverSequence.getPVals() returns stored time values for Time-dependent
 * solutions (Programming Reference Manual, doc 3626/page 547, SHA-256
 * 5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105).
 * This entrypoint never runs a study or clears/replaces solution data.
 */
public final class W24StaticShapeHistoryCapture {
    private static final String COMPONENT = "comp1";
    private static final String GEOMETRY = "geom1";
    private static final byte[] MAGIC = new byte[]{'W', '2', '4', 'S', 'H', 'A', 'P', '1'};
    private static final int BINARY_VERSION = 1;
    private static final double TIME_TOLERANCE_S = 1e-12;
    private static final long MAX_HISTORY_BYTES = 1024L * 1024L * 1024L;

    private W24StaticShapeHistoryCapture() { }

    public static Object run(Model model, Map<String, Object> args) throws IOException {
        if (!"capture".equals(String.valueOf(args.getOrDefault("action", "")))) {
            throw new IllegalArgumentException("action must be exactly capture");
        }
        return capture(model, args);
    }

    private static Map<String, Object> capture(Model model, Map<String, Object> args) throws IOException {
        String requestedCase = token(args.get("case_id"), "case_id");
        String workspaceText = token(args.get("workspace_path"), "workspace_path");
        String outputText = token(args.get("path"), "path");
        Path workspace = Paths.get(workspaceText).toRealPath();
        Path output = Paths.get(outputText).toAbsolutePath().normalize();
        if (!Files.isDirectory(workspace, LinkOption.NOFOLLOW_LINKS) ||
            Files.isSymbolicLink(Paths.get(workspaceText)) || output.equals(workspace) ||
            !output.startsWith(workspace) || !output.getFileName().toString().endsWith(".w24bin")) {
            throw new IllegalArgumentException("capture output must be a new .w24bin child of the registered project workspace");
        }
        Path outputParent = output.getParent();
        if (outputParent == null || !Files.isDirectory(outputParent, LinkOption.NOFOLLOW_LINKS) ||
            Files.isSymbolicLink(outputParent) || !outputParent.toRealPath().equals(outputParent) ||
            Files.exists(output, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalStateException("capture output parent is unsafe or output already exists");
        }

        GeomSequence geometry = model.component(COMPONENT).geom(GEOMETRY);
        if (geometry.getSDim() != 2 || !geometry.isAxisymmetric() || geometry.getNDomains() != 2) {
            throw new IllegalStateException("history capture requires the exact two-domain axisymmetric fixture");
        }
        int boundaryCount = geometry.getNBoundaries();
        String caseId = identifyCase(model);
        if (!requestedCase.equals(caseId)) throw new IllegalArgumentException("requested case differs from native fixture selections");
        int[] glueIds = model.component(COMPONENT).selection("sel_glue_domain").entities(2);
        int[] gasIds = model.component(COMPONENT).selection("sel_gas_domain").entities(2);
        if (glueIds.length != 1 || gasIds.length != 1 || glueIds[0] == gasIds[0]) {
            throw new IllegalStateException("native glue/gas domains are not distinct singleton selections");
        }
        int[] axisIds = model.component(COMPONENT).selection("sel_axis").entities(1);
        if (axisIds.length == 0) throw new IllegalStateException("native symmetry-axis selection is empty");
        for (int id : axisIds) if (id <= 0 || id > boundaryCount) {
            throw new IllegalStateException("native symmetry-axis selection has an invalid boundary ID");
        }
        int[] allDomainIds = sortedUnion(glueIds, gasIds);
        Map<String, int[]> wetSelections = verifyWettingSelections(model, caseId);
        int[] wettedBoundaryIds = unionIds(wetSelections);
        if (wettedBoundaryIds.length == 0) throw new IllegalStateException("native wetted substrate boundary union is empty");
        for (int id : wettedBoundaryIds) if (contains(axisIds, id)) {
            throw new IllegalStateException("native wetted substrate union includes a symmetry-axis boundary");
        }
        for (int[] ids : wetSelections.values()) for (int id : ids) {
            if (id <= 0 || id > boundaryCount) throw new IllegalStateException("native substrate selection has an invalid boundary ID");
        }

        Map<String, Map<String, Object>> parameters = new LinkedHashMap<>();
        double rDrop = parameter(model, parameters, "Rdrop", 500e-6, "m");
        double hFlat = parameter(model, parameters, "hFlat", 100e-6, "m");
        double rBox = parameter(model, parameters, "Rbox", 1.25e-3, "m");
        double hBox = parameter(model, parameters, "Hbox", 0.75e-3, "m");
        double epsilon = parameter(model, parameters, "epsPF", 8e-6, "m");
        double rMesa = parameter(model, parameters, "Rmesa", 300e-6, "m");
        double hMesa = parameter(model, parameters, "hMesa", 40e-6, "m");
        double zStepTop = parameter(model, parameters, "zStepTop", 114.4e-6, "m");
        double capillaryTime = parameter(model, parameters, "tCapillary", 0.016666666666666666, "s");
        parameter(model, parameters, "rhoGlue", 1200.0, "kg/m^3");
        parameter(model, parameters, "muGlue", 1.0, "Pa*s");
        parameter(model, parameters, "rhoGas", 1.2, "kg/m^3");
        parameter(model, parameters, "muGas", 0.018, "Pa*s");
        parameter(model, parameters, "sigma0", 0.03, "N/m");
        if (Math.abs(epsilon / 4.0 - 2e-6) > 1e-15 || rDrop <= 0.0 || rDrop >= rBox ||
            hFlat <= 0.0 || hBox <= zStepTop || hBox <= hMesa || rMesa <= 0.0 || rMesa >= rDrop ||
            Math.abs(zStepTop - (hFlat + square(rMesa / rDrop) * hMesa)) > 1e-14) {
            throw new IllegalStateException("native geometry/phase-field parameters differ from the reviewed baseline");
        }
        double analyticInitialVolume = "flat".equals(caseId)
            ? Math.PI * rDrop * rDrop * hFlat
            : Math.PI * (rDrop * rDrop * zStepTop - rMesa * rMesa * hMesa);
        double pairedFlatVolume = Math.PI * rDrop * rDrop * hFlat;
        if (Math.abs(analyticInitialVolume - pairedFlatVolume) > Math.max(1e-18, pairedFlatVolume * 1e-13)) {
            throw new IllegalStateException("native flat and stepped fixtures no longer have equal analytic liquid volume");
        }

        Study study = model.study("stdShape");
        String tlist = study.feature("time").getString("tlist");
        if (!"range(0[s],tCapillary/2,20*tCapillary)".equals(tlist) ||
            !"PhaseInitialization".equals(study.feature("phasei").getType()) ||
            !"Transient".equals(study.feature("time").getType())) {
            throw new IllegalStateException("native study is not the reviewed phase-init/transient output grid");
        }
        String[] solverTags = study.getSolverSequences("SolverSequence");
        if (solverTags.length != 1) throw new IllegalStateException("stdShape must have exactly one solver sequence");
        String solverTag = solverTags[0];
        SolverSequence sequence = model.sol(solverTag);
        if (!sequence.isAttached() || !"stdShape".equals(sequence.study()) || !"Time".equals(sequence.getType())) {
            throw new IllegalStateException("native solution does not identify the attached time-dependent stdShape sequence");
        }
        int[] solutionSize = sequence.getSize();
        if (solutionSize == null || solutionSize.length != 2 || solutionSize[0] <= 0 || solutionSize[1] <= 0) {
            throw new IllegalStateException("native solver sequence contains no readable stored solution vector");
        }
        SolverFeature timeSolver = uniqueTimeFeature(sequence);
        if (!"strict".equals(timeSolver.getString("tstepsbdf")) ||
            !"tsteps".equals(timeSolver.getString("tout")) || timeSolver.getInt("tstepsstore") != 1) {
            throw new IllegalStateException("native solver output was not configured to store strict requested times");
        }
        double[] storedTimes = sequence.getPVals();
        if (storedTimes == null || storedTimes.length != solutionSize[1]) {
            throw new IllegalStateException("native stored-time vector does not cover every stored solution number");
        }
        double[] requestedTimes = requestedTimes(capillaryTime);
        requireRequestedTimes(storedTimes, requestedTimes);

        double spacing = epsilon / 4.0;
        int radialIntervals = exactIntervals(rBox, spacing, "Rbox");
        double[] radii = new double[radialIntervals];
        double[] floors = new double[radialIntervals];
        int[] profileCounts = new int[radialIntervals];
        int[] profileOffsets = new int[radialIntervals];
        double[][] zColumns = new double[radialIntervals][];
        int totalProfilePoints = 0;
        for (int column = 0; column < radialIntervals; column++) {
            double radius = (column + 0.5) * spacing;
            double floor = "step".equals(caseId) && radius < rMesa ? hMesa : 0.0;
            int intervals = exactIntervals(hBox - floor, spacing, "Hbox-floor");
            radii[column] = radius;
            floors[column] = floor;
            profileCounts[column] = intervals + 1;
            profileOffsets[column] = totalProfilePoints;
            zColumns[column] = new double[intervals + 1];
            for (int level = 0; level <= intervals; level++) zColumns[column][level] = floor + level * spacing;
            totalProfilePoints += intervals + 1;
        }
        double[][] profileCoordinates = new double[2][totalProfilePoints];
        for (int column = 0; column < radialIntervals; column++) {
            for (int level = 0; level < profileCounts[column]; level++) {
                int point = profileOffsets[column] + level;
                profileCoordinates[0][point] = radii[column];
                profileCoordinates[1][point] = zColumns[column][level];
            }
        }
        double[][] wallCoordinates = substrateCoordinates(caseId, rBox, rMesa, hMesa, spacing);
        long expectedBytes = expectedBinarySize(storedTimes.length, radialIntervals,
            totalProfilePoints, wallCoordinates[0].length, zColumns);
        if (expectedBytes > MAX_HISTORY_BYTES) {
            throw new IllegalStateException("native raw history would exceed the 1 GiB artifact safety bound");
        }

        String datasetTag = "w24shapedata" + Long.toUnsignedString(System.nanoTime(), 36);
        List<String> numericalTags = new ArrayList<>();
        boolean datasetCreated = false;
        Map<String, Object> result = new LinkedHashMap<>();
        try {
            DatasetFeature dataset = model.result().dataset().create(datasetTag, "Solution");
            datasetCreated = true;
            dataset.set("solution", solverTag);
            if (!solverTag.equals(dataset.getString("solution"))) {
                throw new IllegalStateException("history dataset did not read back the exact solver sequence");
            }

            NumericalCapture profile = interpolate(model, datasetTag, "profile_phi", profileCoordinates,
                storedTimes.length, 2, allDomainIds, numericalTags);
            NumericalCapture wall = interpolate(model, datasetTag, "substrate_phi", wallCoordinates,
                storedTimes.length, 1, wettedBoundaryIds, numericalTags);
            NumericalCapture volume = evaluateNumerical(model, datasetTag, numericalTags,
                "IntVolume", "phase1_volume", "(1-pf.phipf)/2", "m^3", 2, allDomainIds,
                "intvolume", "on", storedTimes.length);
            NumericalCapture speed = evaluateNumerical(model, datasetTag, numericalTags,
                "MaxVolume", "maximum_speed", "sqrt(spf.u^2+spf.w^2)", "m/s", 2,
                allDomainIds, null, null, storedTimes.length);

            writeBinary(output, storedTimes, radii, floors, zColumns, profile.values,
                wallCoordinates, wallArclengths(wallCoordinates), wall.values, volume.values, speed.values,
                totalProfilePoints, wallCoordinates[0].length);
            if (!Files.isRegularFile(output, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(output) ||
                Files.size(output) <= 0 || !output.toRealPath().startsWith(workspace)) {
                throw new IllegalStateException("raw native history artifact failed project-path/regular-file verification");
            }

            result.put("schema", "W24_NATIVE_STATIC_SHAPE_RAW_HISTORY_V1");
            result.put("status", "NATIVE_RAW_STATIC_SHAPE_HISTORY_CAPTURED");
            result.put("native_acceptance", "NOT_ESTABLISHED_CAPTURE_ONLY");
            result.put("native_study_run_calls_this_action", 0);
            result.put("model_tag", model.tag());
            result.put("case_id", caseId);
            result.put("geometry", geometryReadback(geometry, boundaryCount, glueIds, gasIds,
                allDomainIds, axisIds, wetSelections));
            result.put("parameters_and_units", parameters);
            result.put("analytic_initial_glue_volume_m3", analyticInitialVolume);
            result.put("paired_flat_analytic_volume_m3", pairedFlatVolume);
            result.put("study_tag", "stdShape");
            result.put("solver_tag", solverTag);
            result.put("solver_type", sequence.getType());
            result.put("solver_size", Arrays.asList(solutionSize[0], solutionSize[1]));
            result.put("solver_sequence_time_output", Map.of(
                "time_list_expression", tlist,
                "output_mode", timeSolver.getString("tout"),
                "bdf_time_policy", timeSolver.getString("tstepsbdf"),
                "stored_time_steps_policy", timeSolver.getInt("tstepsstore")));
            result.put("stored_times_s", boxed(storedTimes));
            result.put("requested_times_s", boxed(requestedTimes));
            result.put("time_source", "COMSOL 6.4 SolverSequence.getPVals() for Time-dependent solution");
            Map<String, Object> profileMetadata = new LinkedHashMap<>();
            profileMetadata.put("expression", "pf.phipf");
            profileMetadata.put("unit", "1");
            profileMetadata.put("coordinates", "r,z in m");
            profileMetadata.put("shape", Arrays.asList(1, storedTimes.length, totalProfilePoints));
            profileMetadata.put("radial_point_count", radialIntervals);
            profileMetadata.put("profile_point_count", totalProfilePoints);
            profileMetadata.put("radial_support", "cell-centered fixed samples within 0 <= r < Rbox");
            profileMetadata.put("radial_support_upper_exclusive_m", rBox);
            profileMetadata.put("first_radius_m", radii[0]);
            profileMetadata.put("last_radius_m", radii[radii.length - 1]);
            profileMetadata.put("sample_spacing_m", spacing);
            profileMetadata.put("floor_source", "fixture substrate geometry and exact case parameters");
            profileMetadata.put("feature_readback", profile.readback);
            result.put("profile_field", profileMetadata);
            result.put("substrate_field", Map.of(
                "expression", "pf.phipf", "unit", "1", "coordinates", "r,z in m",
                "shape", Arrays.asList(1, storedTimes.length, wallCoordinates[0].length),
                "sample_spacing_m", spacing, "path_order", "flat r=0..Rbox; step mesa-top then mesa-side then lower-base",
                "entity_dimension", 1, "entity_ids", boxed(wettedBoundaryIds),
                "feature_readback", wall.readback));
            result.put("phase1_volume", Map.of(
                "expression", "(1-pf.phipf)/2", "unit", "m^3", "dimension", 2,
                "shape", Arrays.asList(1, storedTimes.length),
                "entity_ids", boxed(allDomainIds), "axisymmetric_measure", "intvolume=on",
                "feature_readback", volume.readback));
            result.put("maximum_speed", Map.of(
                "expression", "sqrt(spf.u^2+spf.w^2)", "unit", "m/s", "dimension", 2,
                "shape", Arrays.asList(1, storedTimes.length),
                "entity_ids", boxed(allDomainIds), "feature_readback", speed.readback));
            result.put("binary_artifact", Map.of("path", output.toString(), "size_bytes", Files.size(output),
                "sha256", sha256(output), "layout", "W24SHAP1/v1 big-endian doubles; see decoder contract"));
            result.put("cleanup", Map.of("temporary_numerical_tags_removed", Collections.emptyList(),
                "temporary_dataset_removed", false, "status", "PENDING_FINALLY"));
        } finally {
            RuntimeException cleanupFailure = null;
            List<String> removedTags = new ArrayList<>();
            for (String tag : numericalTags) {
                try {
                    model.result().numerical().remove(tag);
                    removedTags.add(tag);
                } catch (RuntimeException exception) {
                    if (cleanupFailure == null) cleanupFailure = exception;
                }
            }
            boolean removedDataset = false;
            if (datasetCreated) {
                try {
                    model.result().dataset().remove(datasetTag);
                    removedDataset = true;
                } catch (RuntimeException exception) {
                    if (cleanupFailure == null) cleanupFailure = exception;
                }
            }
            if (cleanupFailure != null) {
                throw new IllegalStateException("native field data may be captured but temporary result cleanup failed", cleanupFailure);
            }
            if (!result.isEmpty()) {
                result.put("cleanup", Map.of("temporary_numerical_tags_removed", removedTags,
                    "temporary_dataset_removed", removedDataset, "status", "TEMPORARY_RESULTS_REMOVED"));
            }
        }
        return result;
    }

    private static String identifyCase(Model model) {
        String[] tags = model.component(COMPONENT).selection().tags();
        boolean flat = contains(tags, "sel_wet_flat_base");
        boolean step = contains(tags, "sel_wet_mesa_top") && contains(tags, "sel_wet_mesa_side") &&
            contains(tags, "sel_wet_lower_base");
        if (flat == step) throw new IllegalStateException("native selections do not identify exactly one flat/step fixture");
        return flat ? "flat" : "step";
    }

    private static Map<String, int[]> verifyWettingSelections(Model model, String caseId) {
        Map<String, int[]> expected = new LinkedHashMap<>();
        if ("flat".equals(caseId)) {
            expected.put("sel_wet_flat_base", model.component(COMPONENT).selection("sel_wet_flat_base").entities(1));
        } else {
            expected.put("sel_wet_mesa_top", model.component(COMPONENT).selection("sel_wet_mesa_top").entities(1));
            expected.put("sel_wet_mesa_side", model.component(COMPONENT).selection("sel_wet_mesa_side").entities(1));
            expected.put("sel_wet_lower_base", model.component(COMPONENT).selection("sel_wet_lower_base").entities(1));
        }
        Set<Integer> seen = new HashSet<>();
        for (Map.Entry<String, int[]> entry : expected.entrySet()) {
            if (entry.getValue().length == 0) throw new IllegalStateException("native wetting boundary selection is empty");
            for (int id : entry.getValue()) if (!seen.add(id)) throw new IllegalStateException("native wetting boundary selections overlap");
        }
        for (int id : model.component(COMPONENT).selection("sel_axis").entities(1)) {
            if (seen.contains(id)) throw new IllegalStateException("axis boundary is present in native substrate selection");
        }
        return expected;
    }

    private static Map<String, Object> geometryReadback(GeomSequence geometry, int boundaryCount,
            int[] glueIds, int[] gasIds, int[] allIds, int[] axisIds, Map<String, int[]> wetting) {
        Map<String, Object> wallIds = new LinkedHashMap<>();
        for (Map.Entry<String, int[]> entry : wetting.entrySet()) wallIds.put(entry.getKey(), boxed(entry.getValue()));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("dimension", geometry.getSDim());
        result.put("axisymmetric", geometry.isAxisymmetric());
        result.put("domain_count", geometry.getNDomains());
        result.put("boundary_count", boundaryCount);
        result.put("glue_domain_ids", boxed(glueIds));
        result.put("gas_domain_ids", boxed(gasIds));
        result.put("all_fluid_domain_ids", boxed(allIds));
        result.put("wetted_boundary_ids", wallIds);
        result.put("axis_boundary_ids", boxed(axisIds));
        return result;
    }

    private static NumericalCapture interpolate(Model model, String datasetTag, String role,
            double[][] coordinates, int timeCount, int dimension, int[] entities,
            List<String> numericalTags) {
        String tag = "w24shi" + Long.toUnsignedString(System.nanoTime(), 36);
        NumericalFeature feature = model.result().numerical().create(tag, "Interp");
        numericalTags.add(tag);
        String[] expressions = new String[]{"pf.phipf"};
        String[] units = new String[]{"1"};
        feature.set("data", datasetTag);
        feature.set("expr", expressions);
        feature.set("unit", units);
        feature.set("solnum", "all");
        feature.set("coorderr", "on");
        feature.set("matherr", "on");
        feature.selection().geom(GEOMETRY, dimension);
        feature.selection().set(entities);
        feature.setInterpolationCoordinates(coordinates);
        if (!datasetTag.equals(feature.getString("data")) ||
            !Arrays.equals(expressions, feature.getStringArray("expr")) ||
            !Arrays.equals(units, feature.getStringArray("unit")) ||
            !"all".equals(feature.getString("solnum")) ||
            !"on".equalsIgnoreCase(feature.getString("coorderr")) ||
            !"on".equalsIgnoreCase(feature.getString("matherr")) ||
            feature.selection().dim() != dimension || !sameIds(entities, feature.selection().entities())) {
            throw new IllegalStateException("native history interpolation configuration readback mismatch: " + role);
        }
        feature.run();
        if (feature.isComplex()) {
            throw new IllegalStateException("native history interpolation unexpectedly produced complex values: " + role);
        }
        double[][] actualCoordinates = feature.getCoordinates();
        if (!sameCoordinates(coordinates, actualCoordinates, 1e-12) || feature.getNData() != coordinates[0].length) {
            throw new IllegalStateException("native interpolation coordinate readback differs from requested physical samples: " + role);
        }
        double[][][] values = feature.getData();
        if (values == null || values.length != 1 || values[0] == null ||
            values[0].length != timeCount || values[0].length == 0) {
            throw new IllegalStateException("native history interpolation time/expression shape mismatch: " + role);
        }
        int pointCount = coordinates[0].length;
        for (int time = 0; time < timeCount; time++) {
            if (values[0][time] == null || values[0][time].length != pointCount) {
                throw new IllegalStateException("native history interpolation point shape mismatch: " + role);
            }
            for (double value : values[0][time]) {
                if (!Double.isFinite(value)) throw new IllegalStateException("native history field contains nonfinite phi");
            }
        }
        Map<String, Object> readback = new LinkedHashMap<>();
        readback.put("tag", tag);
        readback.put("type", "Interp");
        readback.put("dataset", feature.getString("data"));
        readback.put("expression", Arrays.asList(feature.getStringArray("expr")));
        readback.put("unit", Arrays.asList(feature.getStringArray("unit")));
        readback.put("solnum", feature.getString("solnum"));
        readback.put("coorderr", feature.getString("coorderr"));
        readback.put("matherr", feature.getString("matherr"));
        readback.put("complex", feature.isComplex());
        readback.put("geometry", feature.selection().geom());
        readback.put("entity_dimension", feature.selection().dim());
        readback.put("entity_ids", boxed(feature.selection().entities()));
        readback.put("coordinate_readback", "NumericalFeature.getCoordinates() matched requested global r,z samples within 1e-12 m");
        readback.put("coordinate_count", feature.getNData());
        readback.put("shape", Arrays.asList(1, timeCount, pointCount));
        readback.put("result_method", "NumericalFeature.getData()[expression][stored_time][point]");
        return new NumericalCapture(values[0], readback);
    }

    private static NumericalCapture evaluateNumerical(Model model, String datasetTag, List<String> numericalTags,
            String type, String role, String expression, String unit, int dimension, int[] entities,
            String measureKey, String measureValue, int timeCount) {
        String tag = "w24shn" + Long.toUnsignedString(System.nanoTime(), 36);
        NumericalFeature feature = model.result().numerical().create(tag, type);
        numericalTags.add(tag);
        feature.set("data", datasetTag);
        feature.selection().geom(GEOMETRY, dimension);
        feature.selection().set(entities);
        feature.set("expr", new String[]{expression});
        feature.set("unit", new String[]{unit});
        feature.set("solnum", "all");
        if (measureKey != null) feature.set(measureKey, measureValue);
        if (!datasetTag.equals(feature.getString("data")) ||
            !Arrays.equals(new String[]{expression}, feature.getStringArray("expr")) ||
            !Arrays.equals(new String[]{unit}, feature.getStringArray("unit")) ||
            !"all".equals(feature.getString("solnum")) || feature.selection().dim() != dimension ||
            !sameIds(entities, feature.selection().entities()) ||
            (measureKey != null && !measureValue.equals(feature.getString(measureKey)))) {
            throw new IllegalStateException("native history numerical configuration readback mismatch: " + role);
        }
        feature.run();
        if (feature.isComplex()) {
            throw new IllegalStateException("native history metric unexpectedly produced complex values: " + role);
        }
        double[][] values = feature.getReal();
        if (values == null || values.length != 1 || values[0] == null || values[0].length != timeCount) {
            throw new IllegalStateException("native history numerical result shape mismatch: " + role);
        }
        for (double value : values[0]) if (!Double.isFinite(value)) {
            throw new IllegalStateException("native history numerical result contains a nonfinite value: " + role);
        }
        Map<String, Object> readback = new LinkedHashMap<>();
        readback.put("tag", tag);
        readback.put("type", type);
        readback.put("dataset", feature.getString("data"));
        readback.put("expression", Arrays.asList(feature.getStringArray("expr")));
        readback.put("unit", Arrays.asList(feature.getStringArray("unit")));
        readback.put("solnum", feature.getString("solnum"));
        readback.put("complex", feature.isComplex());
        readback.put("geometry", feature.selection().geom());
        readback.put("entity_dimension", feature.selection().dim());
        readback.put("entity_ids", boxed(feature.selection().entities()));
        readback.put("measure_property", measureKey);
        readback.put("measure_value", measureKey == null ? null : feature.getString(measureKey));
        readback.put("shape", Arrays.asList(1, values[0].length));
        return new NumericalCapture(values, readback);
    }

    private static SolverFeature uniqueTimeFeature(SolverSequence sequence) {
        Map<String, SolverFeature> matches = new LinkedHashMap<>();
        collectTimeFeatures(sequence.feature().tags(), sequence.feature(), matches);
        if (matches.size() != 1) throw new IllegalStateException("expected exactly one Time solver feature");
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

    private static double[][] substrateCoordinates(String caseId, double rBox, double rMesa,
                                                    double hMesa, double spacing) {
        List<Double> r = new ArrayList<>();
        List<Double> z = new ArrayList<>();
        if ("flat".equals(caseId)) {
            int intervals = exactIntervals(rBox, spacing, "Rbox wall");
            for (int index = 0; index <= intervals; index++) {
                r.add(index * spacing); z.add(0.0);
            }
        } else {
            int topIntervals = exactIntervals(rMesa, spacing, "Rmesa wall");
            int sideIntervals = exactIntervals(hMesa, spacing, "hMesa wall");
            int endIntervals = exactIntervals(rBox - rMesa, spacing, "lower substrate wall");
            for (int index = 0; index <= topIntervals; index++) {
                r.add(index * spacing); z.add(hMesa);
            }
            for (int index = 1; index <= sideIntervals; index++) {
                r.add(rMesa); z.add(hMesa - index * spacing);
            }
            for (int index = 1; index <= endIntervals; index++) {
                r.add(rMesa + index * spacing); z.add(0.0);
            }
        }
        double[][] out = new double[2][r.size()];
        for (int index = 0; index < r.size(); index++) {
            out[0][index] = r.get(index);
            out[1][index] = z.get(index);
        }
        return out;
    }

    private static void writeBinary(Path output, double[] times, double[] radii, double[] floors,
            double[][] zColumns, double[][] profileByTime, double[][] wallCoordinates,
            double[] wallArclengths, double[][] wallByTime, double[][] volume, double[][] speed,
            int profilePointCount, int wallPointCount) throws IOException {
        try (FileChannel channel = FileChannel.open(output, StandardOpenOption.CREATE_NEW,
                StandardOpenOption.WRITE);
             DataOutputStream out = new DataOutputStream(new BufferedOutputStream(Channels.newOutputStream(channel)))) {
            out.write(MAGIC);
            out.writeInt(BINARY_VERSION);
            out.writeInt(times.length);
            out.writeInt(radii.length);
            out.writeInt(profilePointCount);
            out.writeInt(wallPointCount);
            for (double time : times) out.writeDouble(time);
            for (double radius : radii) out.writeDouble(radius);
            for (int column = 0; column < radii.length; column++) {
                out.writeDouble(floors[column]);
                out.writeInt(zColumns[column].length);
                for (double z : zColumns[column]) out.writeDouble(z);
            }
            for (int time = 0; time < times.length; time++) {
                if (profileByTime[time].length != profilePointCount) throw new IllegalStateException("profile writer count changed");
                for (double phi : profileByTime[time]) out.writeDouble(phi);
            }
            for (int point = 0; point < wallPointCount; point++) {
                out.writeDouble(wallArclengths[point]);
                out.writeDouble(wallCoordinates[0][point]);
                out.writeDouble(wallCoordinates[1][point]);
            }
            for (int time = 0; time < times.length; time++) {
                if (wallByTime[time].length != wallPointCount) throw new IllegalStateException("wall writer count changed");
                for (double phi : wallByTime[time]) out.writeDouble(phi);
            }
            if (volume.length != 1 || speed.length != 1 || volume[0].length != times.length || speed[0].length != times.length) {
                throw new IllegalStateException("metric writer shape changed");
            }
            for (double value : volume[0]) out.writeDouble(value);
            for (double value : speed[0]) out.writeDouble(value);
            out.flush();
            channel.force(true);
        }
    }

    private static double[] wallArclengths(double[][] coordinates) {
        if (coordinates == null || coordinates.length != 2 || coordinates[0] == null ||
            coordinates[1] == null || coordinates[0].length != coordinates[1].length ||
            coordinates[0].length < 2) {
            throw new IllegalStateException("substrate coordinate path is malformed");
        }
        double[] arclength = new double[coordinates[0].length];
        for (int index = 1; index < arclength.length; index++) {
            double dr = coordinates[0][index] - coordinates[0][index - 1];
            double dz = coordinates[1][index] - coordinates[1][index - 1];
            double increment = Math.hypot(dr, dz);
            if (!Double.isFinite(increment) || increment <= 0.0) {
                throw new IllegalStateException("substrate path has a repeated or nonfinite physical coordinate");
            }
            arclength[index] = arclength[index - 1] + increment;
        }
        return arclength;
    }

    private static long expectedBinarySize(int timeCount, int radialCount, int profilePointCount,
            int wallPointCount, double[][] zColumns) {
        long zCount = 0;
        for (double[] column : zColumns) zCount += column.length;
        long doubles = 3L * timeCount + 2L * radialCount + zCount +
            (long) timeCount * profilePointCount + 3L * wallPointCount +
            (long) timeCount * wallPointCount;
        return 28L + 4L * radialCount + 8L * doubles;
    }

    private static boolean sameCoordinates(double[][] requested, double[][] actual, double tolerance) {
        if (requested == null || actual == null || requested.length != 2 || actual.length != 2 ||
            requested[0] == null || requested[1] == null || actual[0] == null || actual[1] == null ||
            requested[0].length != actual[0].length || requested[1].length != actual[1].length ||
            requested[0].length != requested[1].length) return false;
        for (int dimension = 0; dimension < 2; dimension++) {
            for (int point = 0; point < requested[dimension].length; point++) {
                if (!Double.isFinite(actual[dimension][point]) ||
                    Math.abs(requested[dimension][point] - actual[dimension][point]) > tolerance) return false;
            }
        }
        return true;
    }

    private static void requireRequestedTimes(double[] stored, double[] requested) {
        if (stored == null || stored.length < requested.length) throw new IllegalStateException("native solution omitted requested times");
        for (int index = 0; index < stored.length; index++) {
            if (!Double.isFinite(stored[index]) || (index > 0 && stored[index] <= stored[index - 1])) {
                throw new IllegalStateException("native stored time vector is nonfinite or unordered");
            }
        }
        for (double target : requested) {
            boolean found = false;
            for (double actual : stored) if (Math.abs(actual - target) <= TIME_TOLERANCE_S) { found = true; break; }
            if (!found) throw new IllegalStateException("strict stored times do not contain requested t=" + target);
        }
    }

    private static double[] requestedTimes(double capillaryTime) {
        double[] requested = new double[41];
        for (int index = 0; index < requested.length; index++) requested[index] = (index * 0.5) * capillaryTime;
        return requested;
    }

    private static int exactIntervals(double length, double spacing, String label) {
        double ratio = length / spacing;
        long rounded = Math.round(ratio);
        if (rounded < 2 || Math.abs(ratio - rounded) > 1e-9) {
            throw new IllegalStateException(label + " is not covered by the frozen exact profile spacing");
        }
        return (int) rounded;
    }

    private static double parameter(Model model, Map<String, Map<String, Object>> output,
                                    String name, double expected, String expectedUnit) {
        double value = model.param().evaluate(name);
        String unit = model.param().evaluateUnit(name).replace(" ", "");
        if (!Double.isFinite(value) || Math.abs(value - expected) > Math.max(1e-14, Math.abs(expected) * 1e-12) ||
            !expectedUnit.equals(unit)) {
            throw new IllegalStateException("native capture parameter value/unit differs from frozen baseline: " + name);
        }
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("expression", model.param().get(name));
        row.put("value_si", value);
        row.put("unit", unit);
        output.put(name, row);
        return value;
    }

    private static int[] sortedUnion(int[] first, int[] second) {
        int[] values = new int[first.length + second.length];
        System.arraycopy(first, 0, values, 0, first.length);
        System.arraycopy(second, 0, values, first.length, second.length);
        Arrays.sort(values);
        int count = 0;
        for (int value : values) if (count == 0 || values[count - 1] != value) values[count++] = value;
        return Arrays.copyOf(values, count);
    }

    private static int[] unionIds(Map<String, int[]> selections) {
        int size = 0;
        for (int[] ids : selections.values()) size += ids.length;
        int[] all = new int[size];
        int offset = 0;
        for (int[] ids : selections.values()) {
            System.arraycopy(ids, 0, all, offset, ids.length);
            offset += ids.length;
        }
        Arrays.sort(all);
        int unique = 0;
        for (int id : all) if (unique == 0 || all[unique - 1] != id) all[unique++] = id;
        return Arrays.copyOf(all, unique);
    }

    private static boolean sameIds(int[] first, int[] second) {
        int[] a = first.clone(), b = second.clone();
        Arrays.sort(a); Arrays.sort(b);
        return Arrays.equals(a, b);
    }

    private static boolean contains(String[] values, String expected) {
        for (String value : values) if (expected.equals(value)) return true;
        return false;
    }

    private static boolean contains(int[] values, int expected) {
        for (int value : values) if (expected == value) return true;
        return false;
    }

    private static double square(double value) { return value * value; }

    private static String token(Object value, String label) {
        if (!(value instanceof String) || ((String) value).trim().isEmpty()) {
            throw new IllegalArgumentException(label + " must be a nonempty string");
        }
        return ((String) value).trim();
    }

    private static List<Integer> boxed(int[] values) {
        List<Integer> rows = new ArrayList<>();
        for (int value : values) rows.add(value);
        return rows;
    }

    private static List<Double> boxed(double[] values) {
        List<Double> rows = new ArrayList<>();
        for (double value : values) rows.add(value);
        return rows;
    }

    private static String sha256(Path path) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (java.io.InputStream input = Files.newInputStream(path)) {
                byte[] buffer = new byte[1024 * 1024];
                int count;
                while ((count = input.read(buffer)) >= 0) if (count > 0) digest.update(buffer, 0, count);
            }
            StringBuilder text = new StringBuilder();
            for (byte value : digest.digest()) text.append(String.format("%02x", value & 0xff));
            return text.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static final class NumericalCapture {
        final double[][] values;
        final Map<String, Object> readback;
        NumericalCapture(double[][] values, Map<String, Object> readback) {
            this.values = values;
            this.readback = readback;
        }
    }
}
