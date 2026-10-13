import com.comsol.model.ComponentMeshList;
import com.comsol.model.GeomSequence;
import com.comsol.model.Model;
import com.comsol.model.ModelNode;
import com.comsol.model.PhysicsList;
import com.comsol.model.SolverFeature;
import com.comsol.model.SolverFeatureList;
import com.comsol.model.SolverSequence;
import com.comsol.model.SolverSequenceList;
import com.comsol.model.Study;
import com.comsol.model.StudyFeature;
import com.comsol.model.StudyFeatureList;
import com.comsol.model.StudyList;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsField;
import com.comsol.model.physics.PhysicsFieldList;
import com.comsol.model.physics.PhysicsProp;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Offline proxy checks for the W24 typed solver-sequence selector. */
public final class W24SolverSequenceDiagnosticsHarness {
    private static final Method COUPON_CREATE = method(W24CureCouponFixture.class,
        "createAndRequireUniqueAttachedSolverSequence", Model.class, Study.class,
        StudyFeature.class, String.class);
    private static final Method SCIENCE_SELECT = method(W24CureScienceFixture.class,
        "requireUniqueAttachedSolverSequence", Model.class, Study.class,
        String.class, String.class, String.class);
    private static final Method SCIENCE_CONFIGURE_TIME = method(W24CureScienceFixture.class,
        "configureTimeSolver", Model.class, SolverFeature.class, double.class);
    private static int cases;

    private W24SolverSequenceDiagnosticsHarness() { }

    public static void main(String[] args) throws Exception {
        acceptsTypedAndNoneSequencesForCoupon();
        acceptsNoneTransientAndStationarySequencesForScience();
        rejectsZeroMultipleAndMixedCandidates();
        rejectsMalformedAndOverlappingTags();
        rejectsWrongCategoryAndAttachment();
        rejectsInvalidRootFeatureContracts();
        failsClosedOnRequiredAndStructuralGetterErrors();
        keepsAllDiagnosticOnlyAndRetainsGeneratorCause();
        boundsLargeDiagnosticSnapshots();
        verifiesCouponMeshGenerationComposition();
        verifiesMeshOnlyPhysicalVolumeFromActualGetters();
        verifiesScienceMeasureApplicabilityFromActualGetters();
        verifiesCouponAndScienceFieldBindingContracts();
        verifiesFullPhysicsFieldInventoryFromActualGetters();
        verifiesConfiguredModesAndRawMetadataFromActualGetters();
        verifiesPartialObservationFailures();
        verifiesScienceContinuousDoseToleranceReadback();
        rejectsMissingNativeGroupBeforeScienceSolverConfiguration();
        System.out.println("W24 solver-sequence diagnostics proxy checks: PASS (" + cases + " cases)");
    }


    private static void verifiesScienceMeasureApplicabilityFromActualGetters() throws Exception {
        Method cure = method(W24CureScienceFixture.class, "evaluateNumerical", Model.class, String.class,
            List.class, String.class, String.class, String[].class, String[].class, int.class, int[].class,
            String.class, int.class);
        Method mechanics = method(W24CureScienceFixture.class, "mechanicsNumerical", Model.class, String.class,
            List.class, String.class, String.class, String[].class, String[].class, int[].class);
        Method interpolation = method(W24CureScienceFixture.class, "evaluateInterpolation", Model.class, String.class,
            List.class, String.class, String[].class, String[].class, double[][].class, int.class);
        String[][] specs = {{"cure", "IntSurface", "2", "intvolume"}, {"cure", "IntLine", "1", "intsurface"},
            {"cure", "AvSurface", "2", "intvolume"}, {"mechanics", "AvSurface", "2", "intvolume"},
            {"mechanics", "MaxSurface", "2", ""}, {"interp", "Interp", "0", ""}};
        int count = 0;
        for (String[] spec : specs) {
            List<String> faults = new ArrayList<>(Arrays.asList("", "type", "type_getter", "dataset",
                "unit", "expression", "solnum"));
            if (spec[0].equals("interp")) faults.addAll(Arrays.asList("coorderr", "matherr"));
            else faults.addAll(Arrays.asList("dimension", "geometry", "entities"));
            if (!spec[3].isEmpty()) faults.add("flag");
            for (String fault : faults) {
                List<String> events = new ArrayList<>();
                List<String> owned = new ArrayList<>();
                Model model = scienceMeasureModel(fault, spec[1], Integer.parseInt(spec[2]), spec[3], spec[0].equals("mechanics"), events);
                Object value = null;
                try {
                    if (spec[0].equals("cure")) value = cure.invoke(null, model, "solution_fixture", owned,
                        spec[1], "role", new String[]{"x"}, new String[]{"Pa"}, Integer.parseInt(spec[2]),
                        new int[]{3}, "on", 2);
                    else if (spec[0].equals("mechanics")) value = mechanics.invoke(null, model, "solution_fixture",
                        owned, spec[1], "role", new String[]{"x"}, new String[]{"Pa"}, new int[]{3});
                    else value = interpolation.invoke(null, model, "solution_fixture", owned, "role",
                        new String[]{"x"}, new String[]{"Pa"}, new double[][]{{1e-6}, {2e-6}}, 2);
                    require(fault.isEmpty(), "Science malformed getter passed: " + Arrays.toString(spec) + "/" + fault);
                    java.lang.reflect.Field field = value.getClass().getDeclaredField("readback");
                    field.setAccessible(true);
                    Map<String, Object> row = (Map<String, Object>) field.get(value);
                    require(spec[1].equals(row.get("type")) && "solution_fixture".equals(row.get("dataset")) &&
                            row.get("unit").equals(Arrays.asList("Pa")), "Science must retain actual readback");
                    if (!spec[0].equals("interp")) {
                        require(row.get("entity_dimension").equals(Integer.parseInt(spec[2])) &&
                                row.get("entity_ids").equals(Arrays.asList(3)) &&
                                (spec[3].isEmpty() ? row.get("axisymmetric_measure_property") == null : spec[3].equals(row.get("axisymmetric_measure_property"))) &&
                                (spec[3].isEmpty() ? row.get("axisymmetric_measure_value") == null :
                                    "on".equals(row.get("axisymmetric_measure_value"))),
                            "Science dimension and axisymmetric physical flag must be actual");
                    }
                } catch (InvocationTargetException failure) {
                    require(!fault.isEmpty() && failure.getCause() instanceof IllegalStateException,
                        "Science getter failure must fail closed: " + fault + "/" + failure.getCause());
                    require(!events.contains("run") && !events.contains("getReal") && !events.contains("getData"),
                        "Science rejected getter must not evaluate: " + fault);
                }
                require(owned.size() == 1 && events.get(0).equals("create:" + spec[1]),
                    "Actual owned numerical tag must be recorded before readback failure for caller cleanup");
                require(events.contains("getType"), "requested type cannot stand in for actual getter");
                cases++; count++;
            }
        }
        System.out.println("W24 Science measurement applicability proxy checks: PASS (" + count + " cases; software only)");
    }

    private static Model scienceMeasureModel(String fault, String type, int dimension, String flag, boolean mechanics,
            List<String> events) {
        Map<String, Object> config = new LinkedHashMap<>();
        com.comsol.model.LocalSelection selection = proxy(com.comsol.model.LocalSelection.class, (instance, method, args) -> {
            switch (method.getName()) {
                case "geom":
                    if (args == null || args.length == 0) return fault.equals("geometry") ? "wronggeom" : "geom1";
                    require(args.length == 2 && args[0].equals("geom1") && args[1].equals(dimension),
                        "Science selected dimensional setter mismatch");
                    events.add("selection.geom"); return instance;
                case "set":
                    require(Arrays.equals((int[]) args[0], new int[]{3}), "Science entity setter changed");
                    events.add("selection.set"); return instance;
                case "dim": return fault.equals("dimension") ? 3 : dimension;
                case "entities": return fault.equals("entities") ? new int[]{4} : new int[]{3};
                default: return objectMethod(instance, method, args);
            }
        });
        com.comsol.model.NumericalFeature feature = proxy(com.comsol.model.NumericalFeature.class, (instance, method, args) -> {
            String name = method.getName();
            if (name.equals("set")) {
                String key = String.valueOf(args[0]);
                require(Arrays.asList("data", "expr", "unit", "solnum", "coorderr", "matherr", "intvolume", "intsurface").contains(key),
                    "unexpected Science numerical setter " + key);
                if (key.equals("intvolume") || key.equals("intsurface"))
                    require(key.equals(flag) && args[1].equals("on"), "wrong dimensional physical integration flag");
                config.put(key, args[1]); return instance;
            }
            if (name.equals("selection")) return selection;
            if (name.equals("setInterpolationCoordinates")) return null;
            if (name.equals("getType")) {
                events.add("getType");
                if (fault.equals("type_getter")) throw new IllegalStateException("synthetic Science getType failure");
                if (fault.equals("type")) return type.equals("IntLine") ? "IntSurface" :
                    type.equals("AvSurface") ? "AvVolume" : type.equals("MaxSurface") ? "MaxVolume" :
                    type.equals("IntSurface") ? "IntVolume" : "Eval";
                return type;
            }
            if (name.equals("getString")) {
                String key = String.valueOf(args[0]); events.add("getString:" + key);
                if (fault.equals("dataset") && key.equals("data")) return "foreign_solution";
                if (fault.equals("solnum") && key.equals("solnum")) return "last";
                if (fault.equals("flag") && key.equals(flag)) return "off";
                return config.get(key);
            }
            if (name.equals("getStringArray")) {
                String key = String.valueOf(args[0]); events.add("getStringArray:" + key);
                if (fault.equals("unit") && key.equals("unit")) return new String[]{"m"};
                if (fault.equals("expression") && key.equals("expr")) return new String[]{"foreign"};
                return config.get(key);
            }
            if (name.equals("getBoolean")) return !fault.equals(String.valueOf(args[0]));
            if (name.equals("run") || name.equals("getReal") || name.equals("getData")) {
                require(events.contains("getType") && events.contains("getString:data") &&
                        events.contains("getStringArray:unit"), "evaluation requires actual binding getters first");
                if (!type.equals("Interp")) require(events.indexOf("selection.geom") < events.indexOf("selection.set"),
                    "exact dimensional selection precedes entity assignment");
                if (type.equals("MaxSurface")) require(!config.containsKey("intvolume") && !config.containsKey("intsurface"),
                    "pointwise extrema must not be revolution-integrated");
                events.add(name);
                if (name.equals("getReal")) return mechanics ?
                    new double[][]{{1.0}} : new double[][]{{1.0, 2.0}};
                if (name.equals("getData")) return new double[][][]{{{1.0}, {2.0}}};
                return null;
            }
            return objectMethod(instance, method, args);
        });
        com.comsol.model.NumericalFeatureList list = proxy(com.comsol.model.NumericalFeatureList.class, (instance, method, args) -> {
            if (method.getName().equals("create")) {
                require(type.equals(args[1]), "requested Science type is wrong");
                events.add("create:" + args[1]);return feature;
            }
            return objectMethod(instance, method, args);
        });
        com.comsol.model.Results results = proxy(com.comsol.model.Results.class, (instance, method, args) -> {
            if (method.getName().equals("numerical")) return args == null || args.length == 0 ? list : feature;
            return objectMethod(instance, method, args);
        });
        return proxy(Model.class, (instance, method, args) -> {
            if (method.getName().equals("result")) return results;
            return objectMethod(instance, method, args);
        });
    }

    private static void verifiesCouponMeshGenerationComposition() throws Exception {
        // This proxy records the actual production addMesh calls. Synthetic
        // remaining-domain/element state is composition evidence, not meshing.
        List<String> events = new ArrayList<>();
        Map<String, String> types = new LinkedHashMap<>();
        Map<String, Map<String, Object>> settings = new LinkedHashMap<>();
        Map<String, com.comsol.model.MeshFeature> features = new LinkedHashMap<>();
        List<Integer> generatedDomains = new ArrayList<>();
        String[] localTags = {"sizeAl", "sizeAu", "sizeAdh", "sizeFiber"};
        String[] selections = {"sel_alumina", "sel_gold", "sel_adhesive", "sel_fiber"};
        double[][] sizes = {{50e-6, 10e-6}, {50e-6, 5e-6}, {8e-6, 2e-6}, {25e-6, 5e-6}};
        java.util.function.Function<String, com.comsol.model.MeshFeature> featureFor = tag -> {
            settings.put(tag, new LinkedHashMap<>());
            com.comsol.model.MeshSelection selection = proxy(com.comsol.model.MeshSelection.class,
                (instance, method, args) -> {
                    require(!tag.equals("ftri1"), "FreeTri must retain documented default remaining-domain selection");
                    if (method.getName().equals("geom")) {
                        require(args.length == 2 && args[0].equals("geom1") && args[1].equals(2),
                            "local Size selection must keep geom1 entity dimension 2");
                        settings.get(tag).put("geom", args[0]); settings.get(tag).put("dim", args[1]);
                        events.add(tag + ".selection.geom"); return instance;
                    }
                    if (method.getName().equals("named")) {
                        settings.get(tag).put("selection", args[0]);
                        events.add(tag + ".selection.named"); return instance;
                    }
                    return objectMethod(instance, method, args);
                });
            return proxy(com.comsol.model.MeshFeature.class, (instance, method, args) -> {
                if (method.getName().equals("selection") && method.getParameterCount() == 0) return selection;
                if (method.getName().equals("set")) {
                    String key = String.valueOf(args[0]);
                    require(Arrays.asList("custom", "hmax", "hmin").contains(key), "unexpected mesh sizing setting");
                    settings.get(tag).put(key, args[1]); events.add(tag + ".set:" + key); return instance;
                }
                return objectMethod(instance, method, args);
            });
        };
        types.put("size", "Size"); features.put("size", featureFor.apply("size"));
        com.comsol.model.MeshFeatureList featureList = proxy(com.comsol.model.MeshFeatureList.class,
            (instance, method, args) -> {
                if (method.getName().equals("create")) {
                    String tag = String.valueOf(args[0]);
                    require(Arrays.asList(localTags).contains(tag) && args[1].equals("Size") && !types.containsKey(tag),
                        "unexpected or duplicate local Size feature");
                    types.put(tag, "Size"); features.put(tag, featureFor.apply(tag));
                    events.add("feature.create:" + tag); return features.get(tag);
                }
                return objectMethod(instance, method, args);
            });
        com.comsol.model.MeshSequence mesh = proxy(com.comsol.model.MeshSequence.class,
            (instance, method, args) -> {
                if (method.getName().equals("feature")) {
                    if (method.getParameterCount() == 0) return featureList;
                    require(features.containsKey(String.valueOf(args[0])), "unknown mesh feature getter");
                    return features.get(String.valueOf(args[0]));
                }
                if (method.getName().equals("create")) {
                    require(args[0].equals("ftri1") && args[1].equals("FreeTri") && !types.containsKey("ftri1"),
                        "exactly one FreeTri generator required");
                    require(types.size() == 5 && events.get(events.size() - 1).equals("sizeFiber.set:hmin"),
                        "generator must follow all four completed local Size attributes");
                    require(types.values().stream().allMatch("Size"::equals) && generatedDomains.isEmpty(),
                        "Size-only sequence must remain synthetically empty before generator creation");
                    events.add("SizeOnly.empty");
                    types.put("ftri1", "FreeTri"); features.put("ftri1", featureFor.apply("ftri1"));
                    events.add("sequence.create:ftri1:FreeTri"); return features.get("ftri1");
                }
                if (method.getName().equals("run") && method.getParameterCount() == 0) {
                    events.add("mesh.run");
                    if (types.containsValue("FreeTri")) generatedDomains.addAll(Arrays.asList(1, 2, 3, 4));
                    return null;
                }
                if (method.getName().equals("isEmpty")) return generatedDomains.isEmpty();
                if (method.getName().equals("getNumElem")) return generatedDomains.size() * 2;
                return objectMethod(instance, method, args);
            });
        // Counterexample: default Size-only run produces no synthetic entities.
        mesh.run();
        require(mesh.isEmpty() && mesh.getNumElem() == 0, "Size-only run must not imply element generation");
        events.clear(); cases++;
        ComponentMeshList meshes = proxy(ComponentMeshList.class, (instance, method, args) -> {
            if (method.getName().equals("create")) {
                require(args.length == 2 && args[0].equals("mesh1") && args[1].equals("geom1"),
                    "mesh must preserve mesh1/geom1 binding"); return mesh;
            }
            return objectMethod(instance, method, args);
        });
        ModelNode component = proxy(ModelNode.class, (instance, method, args) -> {
            if (method.getName().equals("mesh") && method.getParameterCount() == 0) return meshes;
            return objectMethod(instance, method, args);
        });
        Model model = proxy(Model.class, (instance, method, args) -> {
            if (method.getName().equals("component")) {
                require(args.length == 1 && args[0].equals("comp1"), "unexpected component binding"); return component;
            }
            return objectMethod(instance, method, args);
        });
        method(W24CureCouponFixture.class, "addMesh", Model.class).invoke(null, model);
        require(settings.get("size").get("custom").equals("on") &&
                settings.get("size").get("hmax").equals(50e-6) && settings.get("size").get("hmin").equals(5e-6),
            "default mesh sizes must remain frozen");
        for (int i = 0; i < localTags.length; i++) {
            Map<String, Object> local = settings.get(localTags[i]);
            require(local.get("geom").equals("geom1") && local.get("dim").equals(2) &&
                    local.get("selection").equals(selections[i]) && local.get("custom").equals("on") &&
                    local.get("hmax").equals(sizes[i][0]) && local.get("hmin").equals(sizes[i][1]),
                "local Size selection/dimensions/values changed: " + localTags[i]);
        }
        require(events.get(events.size() - 1).equals("mesh.run") &&
                events.get(events.size() - 2).equals("sequence.create:ftri1:FreeTri") &&
                generatedDomains.equals(Arrays.asList(1, 2, 3, 4)) && !mesh.isEmpty() && mesh.getNumElem() > 0 &&
                settings.get("ftri1").isEmpty(),
            "generator must directly precede the sole production run and leave all remaining domains selected");
        cases++;
        System.out.println("W24 coupon mesh-generation composition proxy checks: PASS (2 cases; software only)");
    }

    private static void verifiesMeshOnlyPhysicalVolumeFromActualGetters() throws Exception {
        Method reader = method(W24CureCouponFixture.class, "captureAxisymmetricMesh", Model.class);
        List<String> positiveEvents = new ArrayList<>();
        Map<String, Object> raw = (Map<String, Object>) reader.invoke(null, meshVolumeModel("", positiveEvents));
        require(raw.get("geometry_dimension").equals(2) && Boolean.TRUE.equals(raw.get("geometry_axisymmetric")) &&
                "m".equals(raw.get("geometry_length_unit")) && "mesh1".equals(raw.get("mesh_tag")) &&
                raw.get("vertex_count").equals(12) && raw.get("triangle_count").equals(4) &&
                Arrays.asList("r_m", "z_m").equals(raw.get("vertex_coordinate_axes")) &&
                Arrays.asList("getTypes", "getNumElem", "getNumVertex", "getVertex(position,count)",
                    "getElem(tri,position,count)", "getElemEntity(tri,position,count)").equals(raw.get("capture_methods")) &&
                !positiveEvents.contains("mesh.run"),
            "mesh capture must bind the existing 2-D axisymmetric meter mesh using read-only block getters");
        require(Arrays.deepEquals((double[][]) raw.get("vertex_coordinates"),
                    new double[][]{{0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0},
                                   {0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1}}) &&
                Arrays.deepEquals((int[][]) raw.get("triangle_connectivity"),
                    new int[][]{{0, 3, 6, 9}, {1, 4, 7, 10}, {2, 5, 8, 11}}) &&
                Arrays.equals((int[]) raw.get("triangle_geometric_entities"), new int[]{1, 2, 3, 4}),
            "full native vertex, zero-based triangle, and geometric-entity arrays must be returned unchanged");
        int accepted = 1;
        cases++;
        for (String fault : new String[]{"geometry_dimension", "geometry_axisym", "length_unit",
                "mesh_missing", "mesh_geometry", "mesh_dimension", "mesh_empty", "mesh_zero",
                "types_null", "types_duplicate", "unsupported_quad", "count_mismatch",
                "vertex_getter", "vertex_shape", "vertex_nan", "negative_radius", "triangle_getter",
                "triangle_shape", "bad_index", "entity_getter", "entity_shape", "entity_zero",
                "vertex_count_drift", "element_count_drift"}) {
            List<String> events = new ArrayList<>();
            try {
                reader.invoke(null, meshVolumeModel(fault, events));
                throw new AssertionError("invalid native mesh getter passed: " + fault);
            } catch (InvocationTargetException failure) {
                require(failure.getCause() instanceof IllegalStateException,
                    "invalid mesh observation must fail closed with its actual cause: " + fault + "/" + failure.getCause());
                require(events.stream().noneMatch(event -> event.endsWith(".run") || event.endsWith(".compute")),
                    "mesh validation must never build or solve: " + fault);
            }
            accepted++;
            cases++;
        }
        Method area = method(W24CureCouponFixture.class, "observeAxisymmetricDomainArea",
            Model.class, String.class, int[].class, double.class);
        Map<String, Object> areaRow = (Map<String, Object>) area.invoke(null,
            meshVolumeModel("", new ArrayList<>()), "sel_alumina", new int[]{1}, 9.81747704247e-11);
        require(areaRow.get("cross_section_area_m2").equals(0.5) &&
                areaRow.get("area_source").equals("NATIVE_GEOMETRY_MEASURE_GETVOLUME_2D") &&
                areaRow.get("expected_volume_m3").equals(9.81747704247e-11),
            "the native 2-D geometry area remains separate from mesh-derived physical volume");
        cases++;
        for (int[] invalid : new int[][]{null, {}, {0}, {1, 2}}) {
            try {
                area.invoke(null, meshVolumeModel("", new ArrayList<>()), "sel_alumina", invalid, 1.0);
                throw new AssertionError("invalid actual domain selection passed area readback");
            } catch (InvocationTargetException failure) {
                require(failure.getCause() instanceof IllegalStateException,
                    "invalid domain area selection must fail closed");
            }
            cases++;
        }
        System.out.println("W24 actual triangle-mesh axisymmetric volume proxy checks: PASS (" +
            (accepted + 5) + " cases; software only)");
    }

    private static Model meshVolumeModel(String fault, List<String> events) {
        double[][] vertices = {{0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0},
                               {0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1}};
        int[][] triangles = {{0, 3, 6, 9}, {1, 4, 7, 10}, {2, 5, 8, 11}};
        int[] triangleEntities = {1, 2, 3, 4};
        String[] types = fault.equals("types_null") ? null :
            fault.equals("types_duplicate") ? new String[]{"vtx", "edg", "tri", "tri"} :
            fault.equals("unsupported_quad") ? new String[]{"vtx", "edg", "tri", "quad"} :
            new String[]{"vtx", "edg", "tri"};
        Map<String, Integer> counts = new LinkedHashMap<>();
        counts.put("vtx", 12);
        counts.put("edg", 12);
        counts.put("tri", 4);
        if (fault.equals("unsupported_quad")) counts.put("quad", 1);
        final int[] totalGetter = {0}, vertexCountGetter = {0};
        com.comsol.model.GeomSequence geometry = proxy(com.comsol.model.GeomSequence.class, (instance, method, args) -> {
            switch (method.getName()) {
                case "getSDim": return fault.equals("geometry_dimension") ? 3 : 2;
                case "isAxisymmetric": return !fault.equals("geometry_axisym");
                case "lengthUnit": return fault.equals("length_unit") ? "mm" : "m";
                default: return objectMethod(instance, method, args);
            }
        });
        com.comsol.model.MeshSequence mesh = proxy(com.comsol.model.MeshSequence.class, (instance, method, args) -> {
            String name = method.getName();
            events.add("mesh." + name);
            switch (name) {
                case "geom": return fault.equals("mesh_geometry") ? "foreign_geom" : "geom1";
                case "getSDim": return fault.equals("mesh_dimension") ? 3 : 2;
                case "isEmpty": return fault.equals("mesh_empty");
                case "getNumElem":
                    if (args == null || args.length == 0) {
                        totalGetter[0]++;
                        int sum = counts.values().stream().mapToInt(Integer::intValue).sum();
                        return fault.equals("mesh_zero") ? 0 :
                            fault.equals("count_mismatch") || (fault.equals("element_count_drift") && totalGetter[0] > 1)
                                ? sum - 1 : sum;
                    }
                    String type = String.valueOf(args[0]);
                    return counts.getOrDefault(type, 0);
                case "getNumVertex":
                    vertexCountGetter[0]++;
                    return fault.equals("vertex_count_drift") && vertexCountGetter[0] > 1 ? vertices[0].length + 1 :
                        vertices[0].length;
                case "getTypes": return types;
                case "getVertex": {
                    if (fault.equals("vertex_getter")) throw new IllegalStateException("synthetic getVertex error");
                    int position = (Integer) args[0], count = (Integer) args[1];
                    if (fault.equals("vertex_shape")) return new double[][]{{0, 1}, {0}};
                    double[][] result = new double[][]{new double[count], new double[count]};
                    for (int i = 0; i < count; i++) {
                        result[0][i] = vertices[0][position + i];
                        result[1][i] = vertices[1][position + i];
                    }
                    if (fault.equals("vertex_nan")) result[1][0] = Double.NaN;
                    if (fault.equals("negative_radius")) result[0][0] = -1.0;
                    return result;
                }
                case "getElem": {
                    if (fault.equals("triangle_getter")) throw new IllegalStateException("synthetic getElem error");
                    require("tri".equals(args[0]), "only tri connectivity is part of the native volume witness");
                    int position = (Integer) args[1], count = (Integer) args[2];
                    if (fault.equals("triangle_shape")) return new int[][]{{0}, {1, 2}, {2}};
                    int[][] result = new int[3][count];
                    for (int i = 0; i < count; i++)
                        for (int row = 0; row < 3; row++) result[row][i] = triangles[row][position + i];
                    if (fault.equals("bad_index")) result[0][0] = vertices[0].length;
                    return result;
                }
                case "getElemEntity": {
                    if (fault.equals("entity_getter")) throw new IllegalStateException("synthetic getElemEntity error");
                    require("tri".equals(args[0]), "only tri entity bindings are part of the native volume witness");
                    int position = (Integer) args[1], count = (Integer) args[2];
                    if (fault.equals("entity_shape")) return new int[]{1};
                    int[] result = Arrays.copyOfRange(triangleEntities, position, position + count);
                    if (fault.equals("entity_zero")) result[0] = 0;
                    return result;
                }
                default: return objectMethod(instance, method, args);
            }
        });
        com.comsol.model.GeomMeasureFinal measure = proxy(com.comsol.model.GeomMeasureFinal.class, (instance, method, args) -> {
            if (method.getName().equals("selection")) {
                return proxy(com.comsol.model.MeshSelection.class, (selected, selectedMethod, selectedArgs) ->
                    selectedMethod.getName().equals("geom") || selectedMethod.getName().equals("set")
                        ? selected : objectMethod(selected, selectedMethod, selectedArgs));
            }
            if (method.getName().equals("getVolume")) return 0.5;
            return objectMethod(instance, method, args);
        });
        com.comsol.model.ModelNode component = proxy(com.comsol.model.ModelNode.class, (instance, method, args) -> {
            if (method.getName().equals("geom")) return geometry;
            if (method.getName().equals("mesh")) {
                if (fault.equals("mesh_missing")) throw new IllegalStateException("synthetic missing mesh1");
                return mesh;
            }
            if (method.getName().equals("measure")) return measure;
            return objectMethod(instance, method, args);
        });
        return proxy(Model.class, (instance, method, args) -> {
            if (method.getName().equals("component")) return component;
            return objectMethod(instance, method, args);
        });
    }

    private static void verifiesCouponAndScienceFieldBindingContracts() {
        for (boolean science : new boolean[]{false, true}) {
            Map<String, Object> base = fieldContract(science, 2, true,
                new String[]{"u"}, new String[]{"u"}, new String[][]{{"u", "w"}},
                new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u"});
            require("comp1_u".equals(((Map<?, ?>) base.get("component_solver_entry_bindings")).get("w")),
                "logical w must bind to observed comp1_u");
            Map<String, Object> logical = toleranceRows(science, base, actualRows(false));
            require(logical.size() == 6 && "comp1_u".equals(
                ((Map<?, ?>) logical.get("comp1_w")).get("solver_entry_key")),
                "six logical rows must bind u and w to the actual comp1_u entry");
            cases++;

            Map<String, Object> withDose = fieldContract(science, 2, true,
                new String[]{"u"}, new String[]{"u"}, new String[][]{{"u", "w"}},
                new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u", "comp1_Duv_rel"});
            Map<String, Object> doseRows = actualRows(true);
            Map<String, Object> doseLogical = toleranceRows(science, withDose, doseRows);
            require(doseRows.containsKey("comp1_Duv_rel") && doseLogical.size() == 6,
                "dose entry must remain separately observed and must not create a component row");
            cases++;
            expectFieldFailure(science, 3, true, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u", "w"}}, nativeKeys(), "2-D axisymmetric");
            expectFieldFailure(science, 2, false, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u", "w"}}, nativeKeys(), "2-D axisymmetric");
            expectFieldFailure(science, 2, true, new String[]{"u"}, new String[]{"v"},
                new String[][]{{"u", "w"}}, nativeKeys(), "field=u");
            expectFieldFailure(science, 2, true, null, null, null, nativeKeys(), "field=u");
            expectFieldFailure(science, 2, true, new String[0], new String[0], new String[0][],
                nativeKeys(), "field=u");
            expectFieldFailure(science, 2, true, new String[]{"u", "u"}, new String[]{"u", "u"},
                new String[][]{{"u", "w"}, {"u", "w"}}, nativeKeys(), "field=u");
            expectFieldFailure(science, 2, true, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u"}}, nativeKeys(), "exactly {u,w}");
            expectFieldFailure(science, 2, true, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u", "w", "unknown"}}, nativeKeys(), "exactly {u,w}");
            expectFieldFailure(science, 2, true, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u", "u"}}, nativeKeys(), "ambiguous");
            expectFieldFailure(science, 2, true, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u", "w"}}, new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost"},
                "entry table");
            expectFieldFailure(science, 2, true, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u", "w"}}, new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u", "comp1_w"},
                "entry table");
            expectFieldFailure(science, 2, true, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u", "w"}}, new String[]{"comp1_T", "comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u"},
                "ambiguous");
            expectFieldFailure(science, 2, true, new String[]{"u"}, new String[]{"u"},
                new String[][]{{"u", "w"}}, null, "null atolmethod");

            rejectToleranceMutation(science, "comp1_u", "atolmethod", "factor", "readback mismatch");
            rejectToleranceMutation(science, "comp1_u", "atolvaluemethod", "factor", "readback mismatch");
            rejectToleranceMutation(science, "comp1_u", "atol", "NaN", "readback mismatch");
            rejectToleranceMutation(science, "comp1_u", "atol", "1e-9", "readback mismatch");
            rejectToleranceMutation(science, "comp1_Duv_rel", "atolmethod", "factor", "readback mismatch");
            rejectToleranceMutation(science, "comp1_Duv_rel", "atolvaluemethod", "factor", "readback mismatch");
            rejectToleranceMutation(science, "comp1_Duv_rel", "atol", "NaN", "readback mismatch");
            rejectToleranceMutation(science, "comp1_Duv_rel", "atol", "1e-7", "readback mismatch");
        }
    }

    private static void verifiesFullPhysicsFieldInventoryFromActualGetters() throws Exception {
        for (boolean science : new boolean[]{false, true}) {
            Method reader = method(science ? W24CureScienceFixture.class : W24CureCouponFixture.class,
                "readSolverFieldContract", Model.class, SolverFeature.class);
            for (int order = 0; order < 3; order++) {
                String[] tags = order == 0 ? new String[]{"disp"}
                    : order == 1 ? new String[]{"aux", "disp"} : new String[]{"disp", "aux"};
                String[] names = order == 0 ? new String[]{"u"}
                    : order == 1 ? new String[]{"auxiliary_fixture_field", "u"} : new String[]{"u", "auxiliary_fixture_field"};
                String[][] components = order == 0 ? new String[][]{{"u", "w"}}
                    : order == 1 ? new String[][]{{"fixture_aux"}, {"u", "w"}} : new String[][]{{"u", "w"}, {"fixture_aux"}};
                final int[] nameGetters = {0}, componentGetters = {0};
                Model model = descriptorModel(tags, names, components, "", nameGetters, componentGetters);
                SolverFeature time = entryTable("");
                Map<String, Object> contract = (Map<String, Object>) reader.invoke(null, model, time);
                List<Map<String, Object>> inventory = (List<Map<String, Object>>) contract.get("physics_field_descriptors");
                require(inventory.size() == tags.length && contract.get("physics_field_count").equals(tags.length) &&
                        contract.get("selected_displacement_field_count").equals(1) &&
                        "disp".equals(((Map<?, ?>) contract.get("physics_field_descriptor")).get("field_tag")) &&
                        nameGetters[0] == tags.length && componentGetters[0] == tags.length,
                    "actual descriptor getters must retain the complete inventory and uniquely select u");
                cases++;
            }
            for (String[] aliases : new String[][]{{"u"}, {"w"}, {"u", "w"}}) {
                Model model = descriptorModel(new String[]{"aux", "disp"}, new String[]{"auxiliary_fixture_field", "u"},
                    new String[][]{aliases, {"u", "w"}}, "", new int[]{0}, new int[]{0});
                try {
                    reader.invoke(null, model, entryTable(""));
                    throw new AssertionError("nonselected actual descriptor aliased u/w component ownership");
                } catch (InvocationTargetException expected) {
                    String message = expected.getCause().getMessage();
                    require(expected.getCause() instanceof IllegalStateException &&
                            message.contains("aliases selected raw component ownership") &&
                            message.contains("actual_tags=[aux, disp]") && message.contains("actual_total_count=2") &&
                            message.contains("actual_field_names=[auxiliary_fixture_field, u]") &&
                            message.contains("actual_components="),
                        "actual getter ownership alias must fail with complete observed inventory");
                }
                cases++;
            }
            for (String failing : new String[]{"tags", "lookup:aux", "lookup-null:aux", "field:aux", "component:aux", "entry_keys"}) {
                Model model = descriptorModel(new String[]{"aux", "disp"}, new String[]{"auxiliary_fixture_field", "u"},
                    new String[][]{{"fixture_aux"}, {"u", "w"}}, failing, new int[]{0}, new int[]{0});
                try {
                    reader.invoke(null, model, entryTable(failing));
                    throw new AssertionError("required actual getter failure was ignored: " + failing);
                } catch (InvocationTargetException expected) {
                    String message = expected.getCause().getMessage();
                    require(expected.getCause() instanceof IllegalStateException && message.contains("getter_stage=") &&
                            message.contains("actual_tags=") && message.contains("actual_total_count=") &&
                            message.contains("actual_field_names=") && message.contains("actual_components=") &&
                            (failing.equals("lookup-null:aux") || message.contains("synthetic getter failure")),
                        "required getter failure must preserve observed inventory and getter stage: " + failing);
                }
                cases++;
            }
            expectFieldFailure(science, 2, true, new String[]{"aux", "disp"}, new String[]{"u"},
                new String[][]{{"fixture_aux"}, {"u", "w"}}, nativeKeys(), "misaligned");
            expectFieldFailure(science, 2, true, new String[]{"aux", "disp"}, new String[]{"auxiliary_fixture_field", "u"},
                new String[][]{{"u", "w"}}, nativeKeys(), "misaligned");
            expectFieldFailure(science, 2, true, new String[]{"disp"}, null,
                new String[][]{{"u", "w"}}, nativeKeys(), "null/empty/misaligned");
            expectFieldFailure(science, 2, true, new String[]{"aux", "disp"}, new String[]{"auxiliary_fixture_field", "u"},
                new String[][]{null, {"u", "w"}}, nativeKeys(), "incomplete");
            expectFieldFailure(science, 2, true, new String[]{"aux", "disp"}, new String[]{"auxiliary_fixture_field", "u"},
                new String[][]{new String[0], {"u", "w"}}, nativeKeys(), "incomplete");
            expectFieldFailure(science, 2, true, new String[]{"aux", "disp"}, new String[]{" ", "u"},
                new String[][]{{"fixture_aux"}, {"u", "w"}}, nativeKeys(), "incomplete");
            expectFieldFailure(science, 2, true, new String[]{"disp1", "disp2"}, new String[]{"u", "u"},
                new String[][]{{"u", "w"}, {"u", "w"}}, nativeKeys(), "selected_count=2");
            expectFieldFailure(science, 2, true, new String[]{"aux"}, new String[]{"auxiliary_fixture_field"},
                new String[][]{{"fixture_aux"}}, nativeKeys(), "selected_count=0");
            expectFieldFailure(science, 2, true, new String[]{"aux", "disp"}, new String[]{"auxiliary_fixture_field", "u"},
                new String[][]{{"fixture_aux", "fixture_aux"}, {"u", "w"}}, nativeKeys(), "ambiguous");
        }
    }

    private static void verifiesPartialObservationFailures() throws Exception {
        for (boolean science : new boolean[]{false, true}) {
            Method reader = method(science ? W24CureScienceFixture.class : W24CureCouponFixture.class,
                "readSolverFieldContract", Model.class, SolverFeature.class);
            for (String failure : new String[]{"geom_dimension_getter", "geom_axisym_getter",
                    "geom_invalid_dimension", "geom_nonaxisymmetric", "tags", "component:disp",
                    "entry_keys", "entry_keys_null"}) {
                modeGetterCalls.clear();
                Model model = descriptorModel(new String[]{"disp"}, new String[]{"u"}, new String[][]{{"u", "v", "w"}},
                    failure, new int[]{0}, new int[]{0});
                try {
                    reader.invoke(null, model, entryTable(failure));
                    throw new AssertionError("invalid partial observations passed");
                } catch (InvocationTargetException expected) {
                    String message = expected.getCause().getMessage();
                    require(message.contains("actual_geometry_dimension=") &&
                        message.contains("actual_geometry_axisymmetric=") &&
                        message.contains("actual_Time_atolmethod_entry_keys=") &&
                        message.contains("actual_Mode2Daxi_includeTwist=") &&
                        message.contains("actual_Mode2Daxi_ModeExtension="), "partial failure omitted observation categories");
                    if (failure.equals("geom_dimension_getter"))
                        require(message.contains("actual_geometry_dimension=UNOBSERVED") &&
                            message.contains("actual_geometry_axisymmetric=UNOBSERVED"), "failed geometry getter invented values");
                    if (failure.equals("geom_axisym_getter"))
                        require(message.contains("actual_geometry_dimension=2") &&
                            message.contains("actual_geometry_axisymmetric=UNOBSERVED"), "prior dimension was lost");
                    if (failure.equals("entry_keys_null"))
                        require(message.contains("actual_Time_atolmethod_entry_keys=null") &&
                            message.contains("actual_Mode2Daxi_ModeExtension=false"), "observed null differs from unobserved");
                    else if (failure.startsWith("geom_invalid") || failure.equals("geom_nonaxisymmetric"))
                        require(message.contains("actual_Time_atolmethod_entry_keys=[comp1_T") &&
                            message.contains("actual_Mode2Daxi_ModeExtension=false"), "geometry validation lost later actual reads");
                    else
                        require(message.contains("actual_Time_atolmethod_entry_keys=UNOBSERVED") &&
                            message.contains("actual_Mode2Daxi_ModeExtension=UNOBSERVED") && modeGetterCalls.isEmpty(),
                            "failed sequential getter invented later observations");
                }
                cases++;
            }
        }
    }

    private static final List<String> modeGetterCalls = new ArrayList<>();

    private static PhysicsProp modeProperty(String failing) {
        return proxy(PhysicsProp.class, (instance, method, args) -> {
            if (method.getName().startsWith("set")) throw new AssertionError("mode setter forbidden");
            if (method.getName().equals("getBoolean")) {
                String property = (String) args[0];
                require(property.equals("includeTwist") || property.equals("ModeExtension"), "unexpected mode getter");
                modeGetterCalls.add(property);
                if (failing.equals("missing:" + property) || failing.equals("getter:" + property))
                    throw new IllegalStateException("synthetic getter failure " + failing);
                return failing.equals("enabled_both") || failing.equals("enabled:" + property);
            }
            return objectMethod(instance, method, args);
        });
    }

    private static void verifiesConfiguredModesAndRawMetadataFromActualGetters() throws Exception {
        for (boolean science : new boolean[]{false, true}) {
            Method reader = method(science ? W24CureScienceFixture.class : W24CureCouponFixture.class,
                "readSolverFieldContract", Model.class, SolverFeature.class);
            for (String[] raw : new String[][]{{"u", "w"}, {"u", "v", "w"}, {"w", "v", "u"}}) {
                modeGetterCalls.clear();
                Model model = descriptorModel(new String[]{"disp"}, new String[]{"u"}, new String[][]{raw},
                    "", new int[]{0}, new int[]{0});
                Map<String, Object> contract = (Map<String, Object>) reader.invoke(null, model, entryTable(""));
                Map<?, ?> modes = (Map<?, ?>) contract.get("axisymmetric_mode_readbacks");
                require(modeGetterCalls.equals(Arrays.asList("includeTwist", "ModeExtension")) &&
                    ((Map<?, ?>) modes.get("includeTwist")).get("value").equals(false) &&
                    ((Map<?, ?>) modes.get("ModeExtension")).get("value").equals(false) &&
                    ((Map<?, ?>) contract.get("physics_field_descriptor")).get("components").equals(Arrays.asList(raw)) &&
                    contract.get("configured_logical_components").equals(Arrays.asList("u", "w")) &&
                    contract.get("effective_solved_dof_validation").equals("UNVERIFIED"),
                    "typed getters must retain raw metadata and configured/effective scope");
                cases++;
            }
            for (String failure : new String[]{"enabled:includeTwist", "enabled:ModeExtension", "enabled_both",
                    "mode_node_null", "mode_node_getter", "missing:includeTwist", "missing:ModeExtension",
                    "getter:includeTwist", "getter:ModeExtension"}) {
                modeGetterCalls.clear();
                Model model = descriptorModel(new String[]{"disp"}, new String[]{"u"}, new String[][]{{"u", "v", "w"}},
                    failure, new int[]{0}, new int[]{0});
                try {
                    reader.invoke(null, model, entryTable(""));
                    throw new AssertionError("invalid/unobserved mode passed: " + failure);
                } catch (InvocationTargetException exception) {
                    String message = exception.getCause().getMessage();
                    require(message.contains("getter_stage=") && message.contains("actual_geometry_dimension=2") &&
                        message.contains("actual_geometry_axisymmetric=true") &&
                        message.contains("actual_Time_atolmethod_entry_keys=[comp1_T") &&
                        message.contains("actual_components=[[u, v, w]]") &&
                        message.contains("actual_Mode2Daxi_includeTwist=") && message.contains("actual_Mode2Daxi_ModeExtension="),
                        "mode failure must retain all earlier observations");
                    if (failure.endsWith("includeTwist") && !failure.startsWith("enabled"))
                        require(modeGetterCalls.equals(Arrays.asList("includeTwist")) &&
                            message.contains("actual_Mode2Daxi_ModeExtension=UNOBSERVED"), "later getter must remain unobserved");
                    if (failure.endsWith("ModeExtension") && !failure.startsWith("enabled"))
                        require(modeGetterCalls.equals(Arrays.asList("includeTwist", "ModeExtension")) &&
                            message.contains("actual_Mode2Daxi_includeTwist=false") &&
                            message.contains("actual_Mode2Daxi_ModeExtension=UNOBSERVED"), "prior false must be retained");
                }
                cases++;
            }
            for (Boolean[] modes : new Boolean[][]{{null, false}, {false, null}}) {
                try {
                    if (science) W24CureScienceFixture.validateSolverFieldContract(2, true, modes[0], modes[1],
                        new String[]{"disp"}, new String[]{"u"}, new String[][]{{"u", "w"}}, nativeKeys());
                    else W24CureCouponFixture.validateSolverFieldContract(2, true, modes[0], modes[1],
                        new String[]{"disp"}, new String[]{"u"}, new String[][]{{"u", "w"}}, nativeKeys());
                    throw new AssertionError("unobserved pure mode passed");
                } catch (IllegalStateException expected) {
                    require(expected.getMessage().contains("observed disabled"), "null mode failed unexpectedly");
                }
                cases++;
            }
            for (String[] raw : new String[][]{{"u", "v", "v", "w"}, {"u", "v", "other"}, {"u", "v", "w", "other"}}) {
                Model model = descriptorModel(new String[]{"disp"}, new String[]{"u"}, new String[][]{raw},
                    "", new int[]{0}, new int[]{0});
                try {
                    reader.invoke(null, model, entryTable(""));
                    throw new AssertionError("invalid raw component metadata passed");
                } catch (InvocationTargetException expected) {
                    require(expected.getCause().getMessage().contains("actual_Mode2Daxi_includeTwist=false") &&
                        expected.getCause().getMessage().contains("actual_Time_atolmethod_entry_keys=[comp1_T"),
                        "component validation must retain mode and Time readbacks");
                }
                cases++;
            }
            Model alias = descriptorModel(new String[]{"aux", "disp"}, new String[]{"auxiliary", "u"},
                new String[][]{{"v"}, {"u", "v", "w"}}, "", new int[]{0}, new int[]{0});
            try {
                reader.invoke(null, alias, entryTable(""));
                throw new AssertionError("auxiliary v alias passed");
            } catch (InvocationTargetException expected) {
                require(expected.getCause().getMessage().contains("aliases selected raw component ownership"),
                    "v ownership must be fail closed");
            }
            cases++;
        }
    }

    private static SolverFeature entryTable(String failing) {
        return proxy(SolverFeature.class, (instance, method, args) -> {
            if (method.getName().equals("getEntryKeys")) {
                if (failing.equals("entry_keys")) throw new IllegalStateException("synthetic getter failure entry_keys");
                if (failing.equals("entry_keys_null")) return null;
                return nativeKeys();
            }
            return objectMethod(instance, method, args);
        });
    }

    private static Model descriptorModel(String[] tags, String[] names, String[][] components, String failing,
            int[] nameGetters, int[] componentGetters) {
        GeomSequence geom = proxy(GeomSequence.class, (instance, method, args) -> {
            if (method.getName().equals("getSDim")) {
                if (failing.equals("geom_dimension_getter")) throw new IllegalStateException("synthetic geometry dimension failure");
                return failing.equals("geom_invalid_dimension") ? 3 : 2;
            }
            if (method.getName().equals("isAxisymmetric")) {
                if (failing.equals("geom_axisym_getter")) throw new IllegalStateException("synthetic geometry axisym failure");
                return !failing.equals("geom_nonaxisymmetric");
            }
            return objectMethod(instance, method, args);
        });
        ModelNode component = proxy(ModelNode.class, (instance, method, args) -> {
            if (method.getName().equals("geom") && method.getParameterCount() == 1) return geom;
            return objectMethod(instance, method, args);
        });
        PhysicsFieldList list = proxy(PhysicsFieldList.class, (instance, method, args) -> {
            if (method.getName().equals("tags") && method.getParameterCount() == 0) {
                if (failing.equals("tags")) throw new IllegalStateException("synthetic getter failure tags");
                return tags.clone();
            }
            return objectMethod(instance, method, args);
        });
        Physics physics = proxy(Physics.class, (instance, method, args) -> {
            if (method.getName().equals("prop") && method.getParameterCount() == 1) {
                require("Mode2Daxi".equals(args[0]), "unexpected mode property node");
                if (failing.equals("mode_node_null")) return null;
                if (failing.equals("mode_node_getter")) throw new IllegalStateException("synthetic getter failure mode node");
                return modeProperty(failing);
            }
            if (method.getName().equals("field") && method.getParameterCount() == 0) return list;
            if (method.getName().equals("field") && method.getParameterCount() == 1) {
                String tag = (String) args[0];
                if (failing.equals("lookup:" + tag)) throw new IllegalStateException("synthetic getter failure lookup");
                if (failing.equals("lookup-null:" + tag)) return null;
                int index = Arrays.asList(tags).indexOf(tag);
                return proxy(PhysicsField.class, (field, getter, parameters) -> {
                    if (getter.getName().equals("field") && getter.getParameterCount() == 0) {
                        if (failing.equals("field:" + tag)) throw new IllegalStateException("synthetic getter failure field");
                        nameGetters[0]++;
                        return names[index];
                    }
                    if (getter.getName().equals("component") && getter.getParameterCount() == 0) {
                        if (failing.equals("component:" + tag)) throw new IllegalStateException("synthetic getter failure component");
                        componentGetters[0]++;
                        return components[index].clone();
                    }
                    return objectMethod(field, getter, parameters);
                });
            }
            return objectMethod(instance, method, args);
        });
        return proxy(Model.class, (instance, method, args) -> {
            if (method.getName().equals("component") && method.getParameterCount() == 1) return component;
            if (method.getName().equals("physics") && method.getParameterCount() == 1) return physics;
            return objectMethod(instance, method, args);
        });
    }

    private static void verifiesScienceContinuousDoseToleranceReadback() throws Exception {
        SolverConfigurationProbe positive = configureScienceTimeWithDose(nativeKeysWithDose(), false);
        Map<String, Object> rows = (Map<String, Object>) positive.readbacks.get("field_tolerances");
        require(positive.doseSetCount == 3 && positive.doseGetterCount == 3 &&
                positive.doseSetProperties.equals(Arrays.asList("atolmethod", "atolvaluemethod", "atol")) &&
                "1e-8".equals(((Map<?, ?>) rows.get("comp1_Duv_rel")).get("atol")),
            "Science continuous dose entry must be separately configured and read back before return");
        cases++;

        SolverConfigurationProbe absent = configureScienceTimeWithDose(nativeKeys(), false);
        Map<String, Object> absentRows = (Map<String, Object>) absent.readbacks.get("field_tolerances");
        require(absent.doseSetCount == 0 && absent.doseGetterCount == 0 &&
                !absentRows.containsKey("comp1_Duv_rel"),
            "dose-free Science continuous solver must neither configure nor fabricate a dose row");
        cases++;

        SolverConfigurationProbe mismatch = configureScienceTimeWithDose(nativeKeysWithDose(), true);
        require(mismatch.failure != null && mismatch.failure.getMessage().contains("readback mismatch") &&
                mismatch.doseSetCount == 3 && mismatch.doseGetterCount == 3,
            "Science continuous configuration must reject and count an incorrect dose getter readback");
        cases++;
    }

    private static SolverConfigurationProbe configureScienceTimeWithDose(String[] keys, boolean badDoseReadback)
            throws Exception {
        final int[] doseSetCount = {0};
        final int[] doseGetterCount = {0};
        final List<String> doseSetProperties = new ArrayList<>();
        final Map<String, Object> general = new LinkedHashMap<>();
        final Map<String, Map<String, Object>> rows = new LinkedHashMap<>();
        Map<String, Object> initialRows = actualRows(Arrays.asList(keys).contains("comp1_Duv_rel"));
        for (Map.Entry<String, Object> entry : initialRows.entrySet()) {
            rows.put(entry.getKey(), new LinkedHashMap<>((Map<String, Object>) entry.getValue()));
        }
        SolverFeature time = proxy(SolverFeature.class, (instance, method, args) -> {
            String name = method.getName();
            if (name.equals("getEntryKeys")) return keys.clone();
            if (name.equals("setEntry")) {
                String property = String.valueOf(args[0]);
                String field = String.valueOf(args[1]);
                Object value = args[2];
                rows.computeIfAbsent(field, ignored -> new LinkedHashMap<>()).put(property, value);
                if (field.equals("comp1_Duv_rel")) {
                    doseSetCount[0]++;
                    doseSetProperties.add(property);
                }
                return null;
            }
            if (name.equals("getString")) {
                if (args.length == 2) {
                    String property = String.valueOf(args[0]);
                    String field = String.valueOf(args[1]);
                    if (field.equals("comp1_Duv_rel")) doseGetterCount[0]++;
                    if (field.equals("comp1_Duv_rel") && badDoseReadback && property.equals("atol")) {
                        return "1e-7";
                    }
                    Map<String, Object> row = rows.get(field);
                    return row == null ? null : String.valueOf(row.get(property));
                }
                return String.valueOf(general.get(args[0]));
            }
            if (name.equals("set")) {
                general.put(String.valueOf(args[0]), args[1]);
                return null;
            }
            if (name.equals("getInt")) return 1;
            if (name.equals("getDouble")) return ((Number) general.get(args[0])).doubleValue();
            return objectMethod(instance, method, args);
        });
        Model model = fieldModel(2, true, new String[]{"u"}, "u", new String[]{"u", "w"});
        Map<String, Object> readbacks = null;
        IllegalStateException failure = null;
        try {
            readbacks = (Map<String, Object>) SCIENCE_CONFIGURE_TIME.invoke(null, model, time, 1.0);
        } catch (InvocationTargetException exception) {
            Throwable cause = exception.getCause();
            if (cause instanceof IllegalStateException) failure = (IllegalStateException) cause;
            else throw exception;
        }
        return new SolverConfigurationProbe(readbacks, failure, doseSetCount[0], doseGetterCount[0], doseSetProperties);
    }

    private static final class SolverConfigurationProbe {
        final Map<String, Object> readbacks;
        final IllegalStateException failure;
        final int doseSetCount;
        final int doseGetterCount;
        final List<String> doseSetProperties;

        SolverConfigurationProbe(Map<String, Object> readbacks, IllegalStateException failure,
                int doseSetCount, int doseGetterCount, List<String> doseSetProperties) {
            this.readbacks = readbacks;
            this.failure = failure;
            this.doseSetCount = doseSetCount;
            this.doseGetterCount = doseGetterCount;
            this.doseSetProperties = doseSetProperties;
        }
    }

    private static void rejectsMissingNativeGroupBeforeScienceSolverConfiguration() throws Exception {
        final int[] setterCalls = {0};
        Model model = fieldModel(2, true, new String[]{"u"}, "u", new String[]{"u", "w"});
        SolverFeature time = proxy(SolverFeature.class, (instance, method, args) -> {
            if (method.getName().equals("getEntryKeys")) {
                return new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost"};
            }
            if (method.getName().startsWith("set")) {
                setterCalls[0]++;
                throw new AssertionError("missing group was discovered after solver configuration started");
            }
            return objectMethod(instance, method, args);
        });
        try {
            SCIENCE_CONFIGURE_TIME.invoke(null, model, time, 1.0);
            throw new AssertionError("missing comp1_u solver group unexpectedly passed");
        } catch (InvocationTargetException exception) {
            require(exception.getCause() instanceof IllegalStateException &&
                    exception.getCause().getMessage().contains("entry table"),
                "missing solver group failed for an unrelated reason");
        }
        require(setterCalls[0] == 0, "missing native group must fail before any time-solver setter");
        cases++;
    }

    private static Map<String, Object> fieldContract(boolean science, int dimension, boolean axisymmetric,
            String[] tags, String[] names, String[][] components, String[] keys) {
        return science
            ? W24CureScienceFixture.validateSolverFieldContract(dimension, axisymmetric, false, false, tags, names, components, keys)
            : W24CureCouponFixture.validateSolverFieldContract(dimension, axisymmetric, false, false, tags, names, components, keys);
    }

    private static Map<String, Object> toleranceRows(boolean science, Map<String, Object> contract,
            Map<String, Object> rows) {
        return science ? W24CureScienceFixture.validateSolverToleranceRows(contract, rows)
            : W24CureCouponFixture.validateSolverToleranceRows(contract, rows);
    }

    private static void expectFieldFailure(boolean science, int dimension, boolean axisymmetric,
            String[] tags, String[] names, String[][] components, String[] keys, String message) {
        try {
            fieldContract(science, dimension, axisymmetric, tags, names, components, keys);
            throw new AssertionError("invalid field contract unexpectedly passed: " + message);
        } catch (IllegalStateException expected) {
            require(expected.getMessage().contains(message), "field contract failed with an unexpected diagnostic");
        }
        cases++;
    }

    private static void rejectToleranceMutation(boolean science, String field, String key,
            String replacement, String message) {
        Map<String, Object> contract = fieldContract(science, 2, true, new String[]{"u"}, new String[]{"u"},
            new String[][]{{"u", "w"}}, nativeKeysWithDose());
        Map<String, Object> rows = actualRows(true);
        ((Map<String, Object>) rows.get(field)).put(key, replacement);
        try {
            toleranceRows(science, contract, rows);
            throw new AssertionError("invalid solver readback unexpectedly passed: " + field + "/" + key);
        } catch (IllegalStateException expected) {
            require(expected.getMessage().contains(message), "solver readback failed with unexpected diagnostic");
        }
        cases++;
    }

    private static Map<String, Object> actualRows(boolean dose) {
        Map<String, Object> rows = new LinkedHashMap<>();
        String[] names = dose
            ? new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u", "comp1_Duv_rel"}
            : new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u"};
        String[] values = dose
            ? new String[]{"1e-4", "1e-8", "1e-8", "1e-8", "1e-12", "1e-8"}
            : new String[]{"1e-4", "1e-8", "1e-8", "1e-8", "1e-12"};
        for (int i = 0; i < names.length; i++) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("atolmethod", "unscaled");
            row.put("atolvaluemethod", "manual");
            row.put("atol", values[i]);
            rows.put(names[i], row);
        }
        return rows;
    }

    private static String[] nativeKeys() {
        return new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u"};
    }

    private static String[] nativeKeysWithDose() {
        return new String[]{"comp1_T", "comp1_alpha", "comp1_alpha_iso", "comp1_qpost", "comp1_u", "comp1_Duv_rel"};
    }

    private static Model fieldModel(int dimension, boolean axisymmetric, String[] fieldTags,
            String fieldName, String[] components) {
        GeomSequence geom = proxy(GeomSequence.class, (instance, method, args) -> {
            if (method.getName().equals("getSDim")) return dimension;
            if (method.getName().equals("isAxisymmetric")) return axisymmetric;
            return objectMethod(instance, method, args);
        });
        ModelNode component = proxy(ModelNode.class, (instance, method, args) -> {
            if (method.getName().equals("geom") && args != null && args.length == 1) return geom;
            return objectMethod(instance, method, args);
        });
        PhysicsFieldList fields = tagsOnly(PhysicsFieldList.class, fieldTags);
        PhysicsField field = proxy(PhysicsField.class, (instance, method, args) -> {
            if (method.getName().equals("field") && method.getParameterCount() == 0) return fieldName;
            if (method.getName().equals("component") && method.getParameterCount() == 0) return components;
            return objectMethod(instance, method, args);
        });
        Physics physics = proxy(Physics.class, (instance, method, args) -> {
            if (method.getName().equals("prop") && method.getParameterCount() == 1) {
                require("Mode2Daxi".equals(args[0]), "unexpected mode property node");
                return modeProperty("");
            }
            if (method.getName().equals("field") && method.getParameterCount() == 0) return fields;
            if (method.getName().equals("field") && method.getParameterCount() == 1) return field;
            return objectMethod(instance, method, args);
        });
        return proxy(Model.class, (instance, method, args) -> {
            if (method.getName().equals("component") && method.getParameterCount() == 1) return component;
            if (method.getName().equals("physics") && method.getParameterCount() == 1) return physics;
            return objectMethod(instance, method, args);
        });
    }

    private static Method method(Class<?> owner, String name, Class<?>... parameters) {
        try {
            Method result = owner.getDeclaredMethod(name, parameters);
            result.setAccessible(true);
            return result;
        } catch (ReflectiveOperationException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private static void acceptsTypedAndNoneSequencesForCoupon() throws Exception {
        State typed = state("stdUV", "time", "Time");
        typed.solverSequenceTags = new String[]{"solTyped"};
        typed.allTags = typed.solverSequenceTags;
        typed.addSequence("solTyped", "stdUV", true, "SolverSequence");
        require(runCoupon(typed) == typed.sequences.get("solTyped"), "typed coupon sequence was not selected");
        require(typed.solverFilterCalls == 3 && typed.noneFilterCalls == 3 && typed.allFilterCalls == 2,
            "coupon did not make required filters after the two diagnostic snapshots");
        assertNoForbiddenActions(typed);
        cases++;

        State none = state("stdUV", "time", "Time");
        none.noneTags = new String[]{"solNone"};
        none.allTags = none.noneTags;
        none.addSequence("solNone", "stdUV", true, "None");
        require(runCoupon(none) == none.sequences.get("solNone"), "None coupon sequence was not selected");
        assertNoForbiddenActions(none);
        cases++;
    }

    private static void acceptsNoneTransientAndStationarySequencesForScience() throws Exception {
        State transientState = state("stdUV", "time", "Time");
        transientState.noneTags = new String[]{"solTransient"};
        transientState.addSequence("solTransient", "stdUV", true, "None");
        require(runScience(transientState, "Time") == transientState.sequences.get("solTransient"),
            "None transient science sequence was not selected");
        require(transientState.solverFilterCalls == 1 && transientState.noneFilterCalls == 1,
            "science transient selector did not query both required categories once");
        assertNoForbiddenActions(transientState);
        cases++;

        State stationary = state("stdMech", "stat", "Stationary");
        stationary.noneTags = new String[]{"solStationary"};
        stationary.addSequence("solStationary", "stdMech", true, "None");
        require(runScience(stationary, "Stationary") == stationary.sequences.get("solStationary"),
            "None stationary science sequence was not selected");
        assertNoForbiddenActions(stationary);
        cases++;
    }

    private static void rejectsZeroMultipleAndMixedCandidates() throws Exception {
        State zero = state("stdUV", "time", "Time");
        expectCouponFailure(zero, "expected one generated solver sequence", "got []");
        cases++;

        State multiple = state("stdUV", "time", "Time");
        multiple.solverSequenceTags = new String[]{"solA", "solB"};
        multiple.addSequence("solA", "stdUV", true, "SolverSequence");
        multiple.addSequence("solB", "stdUV", true, "SolverSequence");
        expectScienceFailure(multiple, "expected one attached solver sequence");
        cases++;

        State mixed = state("stdUV", "time", "Time");
        mixed.solverSequenceTags = new String[]{"solTyped"};
        mixed.noneTags = new String[]{"solNone"};
        mixed.addSequence("solTyped", "stdUV", true, "SolverSequence");
        mixed.addSequence("solNone", "stdUV", true, "None");
        expectCouponFailure(mixed, "expected one generated solver sequence", "solTyped", "solNone");
        cases++;
    }

    private static void rejectsMalformedAndOverlappingTags() throws Exception {
        State blank = state("stdUV", "time", "Time");
        blank.solverSequenceTags = new String[]{""};
        expectCouponFailure(blank, "blank/null tag");
        cases++;

        State nullTag = state("stdUV", "time", "Time");
        nullTag.noneTags = new String[]{null};
        expectScienceFailure(nullTag, "blank/null tag");
        cases++;

        State nullArray = state("stdUV", "time", "Time");
        nullArray.noneTags = null;
        expectCouponFailure(nullArray, "None filter returned null");
        cases++;

        State duplicate = state("stdUV", "time", "Time");
        duplicate.solverSequenceTags = new String[]{"solDup", "solDup"};
        expectScienceFailure(duplicate, "duplicate tag solDup");
        cases++;

        State overlap = state("stdUV", "time", "Time");
        overlap.solverSequenceTags = new String[]{"solSame"};
        overlap.noneTags = new String[]{"solSame"};
        expectCouponFailure(overlap, "overlaps required filters");
        cases++;
    }

    private static void rejectsWrongCategoryAndAttachment() throws Exception {
        State category = state("stdUV", "time", "Time");
        category.solverSequenceTags = new String[]{"solWrongCategory"};
        category.addSequence("solWrongCategory", "stdUV", true, "None");
        expectCouponFailure(category, "solver category mismatch", "filter=SolverSequence");
        cases++;

        State detached = state("stdUV", "time", "Time");
        detached.noneTags = new String[]{"solDetached"};
        detached.addSequence("solDetached", "stdUV", false, "None");
        expectScienceFailure(detached, "not attached to stdUV", "isAttached=false");
        cases++;

        State wrongStudy = state("stdUV", "time", "Time");
        wrongStudy.noneTags = new String[]{"solOtherStudy"};
        wrongStudy.addSequence("solOtherStudy", "stdOther", true, "None");
        expectCouponFailure(wrongStudy, "not attached to stdUV", "stdOther");
        cases++;
    }

    private static void rejectsInvalidRootFeatureContracts() throws Exception {
        State missingStep = state("stdUV", "time", "Time");
        missingStep.rootFeatures = new String[][]{{"v1", "Variables"}, {"t1", "Time"}};
        missingStep.noneTags = new String[]{"solMissingStep"};
        missingStep.addSequence("solMissingStep", "stdUV", true, "None");
        expectCouponFailure(missingStep, "requires unique StudyStep and Variables");
        cases++;

        State duplicateStep = state("stdMech", "stat", "Stationary");
        duplicateStep.rootFeatures = new String[][]{
            {"st1", "StudyStep"}, {"st2", "StudyStep"}, {"v1", "Variables"}, {"s1", "Stationary"}};
        duplicateStep.noneTags = new String[]{"solDuplicateStep"};
        duplicateStep.addSequence("solDuplicateStep", "stdMech", true, "None");
        expectScienceFailure(duplicateStep, "duplicate StudyStep");
        cases++;

        State missingVariables = state("stdUV", "time", "Time");
        missingVariables.rootFeatures = new String[][]{{"st1", "StudyStep"}, {"t1", "Time"}};
        missingVariables.noneTags = new String[]{"solMissingVariables"};
        missingVariables.addSequence("solMissingVariables", "stdUV", true, "None");
        expectScienceFailure(missingVariables, "requires unique StudyStep and Variables");
        cases++;

        State duplicateVariables = state("stdUV", "time", "Time");
        duplicateVariables.rootFeatures = new String[][]{
            {"st1", "StudyStep"}, {"v1", "Variables"}, {"v2", "Variables"}, {"t1", "Time"}};
        duplicateVariables.noneTags = new String[]{"solDuplicateVariables"};
        duplicateVariables.addSequence("solDuplicateVariables", "stdUV", true, "None");
        expectCouponFailure(duplicateVariables, "duplicate Variables");
        cases++;

        State missingSolver = state("stdUV", "time", "Time");
        missingSolver.rootFeatures = new String[][]{{"st1", "StudyStep"}, {"v1", "Variables"}};
        missingSolver.noneTags = new String[]{"solMissingTime"};
        missingSolver.addSequence("solMissingTime", "stdUV", true, "None");
        expectCouponFailure(missingSolver, "expected exactly one native Time solver feature");
        cases++;

        State duplicateSolver = state("stdMech", "stat", "Stationary");
        duplicateSolver.rootFeatures = new String[][]{
            {"st1", "StudyStep"}, {"v1", "Variables"}, {"s1", "Stationary"}, {"s2", "Stationary"}};
        duplicateSolver.noneTags = new String[]{"solDuplicateStationary"};
        duplicateSolver.addSequence("solDuplicateStationary", "stdMech", true, "None");
        expectScienceFailure(duplicateSolver, "expected exactly one native Stationary solver feature");
        cases++;

        State wrongStudyLink = state("stdUV", "time", "Time");
        wrongStudyLink.linkedStudy = "stdOther";
        wrongStudyLink.noneTags = new String[]{"solWrongStudyLink"};
        wrongStudyLink.addSequence("solWrongStudyLink", "stdUV", true, "None");
        expectCouponFailure(wrongStudyLink, "StudyStep relation mismatch", "stdOther");
        cases++;

        State wrongStepLink = state("stdMech", "stat", "Stationary");
        wrongStepLink.linkedStep = "time";
        wrongStepLink.noneTags = new String[]{"solWrongStepLink"};
        wrongStepLink.addSequence("solWrongStepLink", "stdMech", true, "None");
        expectScienceFailure(wrongStepLink, "StudyStep relation mismatch", "expected=stdMech/stat");
        cases++;
    }

    private static void failsClosedOnRequiredAndStructuralGetterErrors() throws Exception {
        State exactError = state("stdUV", "time", "Time");
        exactError.solverFilterFailure = new IllegalStateException("synthetic SolverSequence failure");
        expectCouponFailure(exactError, "failed to read required SolverSequence filter");
        cases++;

        State noneError = state("stdUV", "time", "Time");
        noneError.noneFilterFailure = new IllegalStateException("synthetic None failure");
        expectScienceFailure(noneError, "failed to read required None filter");
        cases++;

        State structureError = state("stdUV", "time", "Time");
        structureError.noneTags = new String[]{"solBadStepRead"};
        structureError.addSequence("solBadStepRead", "stdUV", true, "None");
        structureError.studyStepGetterFailure = new IllegalArgumentException("synthetic StudyStep read failure");
        expectCouponFailure(structureError, "failed to verify generated solver sequence");
        cases++;
    }

    private static void keepsAllDiagnosticOnlyAndRetainsGeneratorCause() throws Exception {
        State allOnly = state("stdUV", "time", "Time");
        allOnly.globalTags = new String[]{"solCopy", "solStored", "solParametric"};
        allOnly.allTags = allOnly.globalTags;
        allOnly.addSequence("solCopy", "stdOther", true, "CopySolution");
        allOnly.addSequence("solStored", "stdOther", true, "Stored");
        allOnly.addSequence("solParametric", "stdOther", true, "Parametric");
        expectCouponFailure(allOnly, "expected one generated solver sequence", "got []",
            "solver[solCopy].sequenceType=CopySolution", "solver[solStored].sequenceType=Stored",
            "solver[solParametric].sequenceType=Parametric");
        cases++;

        State allGetterError = state("stdUV", "time", "Time");
        allGetterError.allFilterFailure = new IllegalStateException("synthetic All unavailable");
        expectCouponFailure(allGetterError, "expected one generated solver sequence", "got []",
            "study.getSolverSequences(All)=READ_ERROR(IllegalStateException: synthetic All unavailable)");
        cases++;

        State generatorFailure = state("stdUV", "time", "Time");
        generatorFailure.generatorFailure = new IllegalArgumentException("synthetic generator failure");
        IllegalStateException failure = expectCouponFailure(generatorFailure,
            "createAutoSequences(\"sol\") failed for stdUV");
        require(failure.getCause() == generatorFailure.generatorFailure,
            "generator failure cause was not preserved");
        cases++;
    }

    private static void boundsLargeDiagnosticSnapshots() throws Exception {
        State state = state("stdUV", "time", "Time");
        state.globalTags = indexedTags("solGlobal", 100);
        state.allTags = indexedTags("solAll", 100);
        for (String tag : state.globalTags) state.addSequence(tag, "stdUV", true, "None");
        for (String tag : state.allTags) state.addSequence(tag, "stdOther", true, "CopySolution");
        IllegalStateException failure = expectCouponFailure(state,
            "expected one generated solver sequence", "got []", "[TRUNCATED 32/100]");
        require(state.sequenceLookupCalls <= 64,
            "diagnostic model.sol lookups exceeded 32 tags in either of two snapshots: " + state.sequenceLookupCalls);
        require(state.sequenceMetadataReads <= 256,
            "sequence metadata reads exceeded bounded diagnostic snapshots: " + state.sequenceMetadataReads);
        require(!failure.getMessage().contains("solGlobal99") && !failure.getMessage().contains("solAll99"),
            "diagnostic snapshots inspected tags past their bounded prefixes");
        cases++;
    }

    private static String[] indexedTags(String prefix, int count) {
        String[] tags = new String[count];
        for (int i = 0; i < count; i++) tags[i] = prefix + (i < 10 ? "0" : "") + i;
        return tags;
    }

    private static State state(String studyTag, String studyStepTag, String expectedSolverType) {
        return new State(studyTag, studyStepTag, expectedSolverType);
    }

    private static SolverSequence runCoupon(State state) throws Exception {
        try {
            return (SolverSequence) COUPON_CREATE.invoke(null, state.model, state.study, state.step,
                state.studyTag);
        } catch (InvocationTargetException exception) {
            throw asException(exception.getCause());
        }
    }

    private static SolverSequence runScience(State state, String expectedType) throws Exception {
        state.study.createAutoSequences("sol");
        try {
            return (SolverSequence) SCIENCE_SELECT.invoke(null, state.model, state.study,
                state.studyTag, state.studyStepTag, expectedType);
        } catch (InvocationTargetException exception) {
            throw asException(exception.getCause());
        }
    }

    private static Exception asException(Throwable cause) throws Exception {
        if (cause instanceof Exception) return (Exception) cause;
        throw new AssertionError("unexpected non-Exception failure", cause);
    }

    private static IllegalStateException expectCouponFailure(State state, String... fragments) throws Exception {
        try {
            runCoupon(state);
            throw new AssertionError("coupon selector unexpectedly succeeded");
        } catch (IllegalStateException expected) {
            assertFragments(expected, fragments);
            require(expected.getMessage().contains("solver_diagnostic_before={") &&
                expected.getMessage().contains("solver_diagnostic_after={"),
                "coupon failure omitted bounded pre/post snapshots");
            require(state.createAutoSequenceCalls == 1, "coupon failure did not call generator exactly once");
            assertNoForbiddenActions(state);
            return expected;
        }
    }

    private static IllegalStateException expectScienceFailure(State state, String... fragments) throws Exception {
        try {
            runScience(state, state.expectedSolverType);
            throw new AssertionError("science selector unexpectedly succeeded");
        } catch (IllegalStateException expected) {
            assertFragments(expected, fragments);
            require(state.createAutoSequenceCalls == 1, "science failure did not call generator exactly once");
            assertNoForbiddenActions(state);
            return expected;
        }
    }

    private static void assertFragments(IllegalStateException failure, String[] fragments) {
        for (String fragment : fragments) {
            require(failure.getMessage().contains(fragment), "failure omitted " + fragment +
                ": " + failure.getMessage());
        }
    }

    private static void assertNoForbiddenActions(State state) {
        require(state.nativeRunCalls == 0, "proxy observed a study or solver run");
        require(state.attachCalls == 0, "proxy observed a solver attach/detach call");
        require(state.setterCalls == 0, "proxy observed a setter or unapproved create call");
    }

    private static final class State {
        private final String studyTag;
        private final String studyStepTag;
        private final String expectedSolverType;
        private String[] globalTags = new String[0];
        private String[] allTags = new String[0];
        private String[] solverSequenceTags = new String[0];
        private String[] noneTags = new String[0];
        private String[][] rootFeatures;
        private String linkedStudy;
        private String linkedStep;
        private RuntimeException generatorFailure;
        private RuntimeException solverFilterFailure;
        private RuntimeException noneFilterFailure;
        private RuntimeException allFilterFailure;
        private RuntimeException studyStepGetterFailure;
        private int createAutoSequenceCalls;
        private int solverFilterCalls;
        private int noneFilterCalls;
        private int allFilterCalls;
        private int sequenceLookupCalls;
        private int sequenceMetadataReads;
        private int nativeRunCalls;
        private int attachCalls;
        private int setterCalls;
        private final Map<String, SolverSequence> sequences = new LinkedHashMap<>();
        private final Model model;
        private final Study study;
        private final StudyFeature step;

        private State(String studyTag, String studyStepTag, String expectedSolverType) {
            this.studyTag = studyTag;
            this.studyStepTag = studyStepTag;
            this.expectedSolverType = expectedSolverType;
            this.linkedStudy = studyTag;
            this.linkedStep = studyStepTag;
            String solverTag = expectedSolverType.equals("Time") ? "t1" : "s1";
            this.rootFeatures = new String[][]{
                {"st1", "StudyStep"}, {"v1", "Variables"}, {solverTag, expectedSolverType}};
            this.step = stepProxy(this);
            this.model = modelProxy(this);
            this.study = studyProxy(this);
        }

        private SolverSequence addSequence(String tag, String attachedStudy, boolean attached,
                                           String sequenceType) {
            SolverSequence result = sequenceProxy(this, tag, attachedStudy, attached, sequenceType);
            sequences.put(tag, result);
            return result;
        }
    }

    private static Model modelProxy(State state) {
        StudyList studies = tagsOnly(StudyList.class, new String[]{state.studyTag});
        Physics physics = proxy(Physics.class, (instance, method, args) -> {
            if (method.getName().equals("getType")) return "SolidMechanics";
            if (method.getName().equals("resolveModelPath")) return "comp1.solid";
            return objectMethod(instance, method, args);
        });
        PhysicsList physicses = tagsOnly(PhysicsList.class, new String[]{"solid"});
        ModelNode component = proxy(ModelNode.class, (instance, method, args) -> {
            if (method.getName().equals("mesh") && method.getParameterCount() == 0) {
                return tagsOnly(ComponentMeshList.class, new String[]{"mesh1"});
            }
            return objectMethod(instance, method, args);
        });
        SolverSequenceList solvers = tagsOnly(SolverSequenceList.class, () -> state.globalTags);
        return proxy(Model.class, (instance, method, args) -> {
            switch (method.getName()) {
                case "study":
                    if (method.getParameterCount() == 0) return studies;
                    throw new AssertionError("unexpected model.study overload");
                case "physics":
                    if (method.getParameterCount() == 0) return physicses;
                    if (method.getParameterCount() == 1 && "solid".equals(args[0])) return physics;
                    throw new AssertionError("unexpected model.physics call");
                case "component": return component;
                case "sol":
                    if (method.getParameterCount() == 0) return solvers;
                    state.sequenceLookupCalls++;
                    return state.sequences.get(String.valueOf(args[0]));
                default: return objectMethod(instance, method, args);
            }
        });
    }

    private static Study studyProxy(State state) {
        StudyFeatureList features = tagsAndGet(StudyFeatureList.class,
            new String[]{state.studyStepTag, "init1"}, Map.of(state.studyStepTag, state.step));
        return proxy(Study.class, (instance, method, args) -> {
            if (method.getName().equals("createAutoSequences")) {
                require("sol".equals(args[0]), "fixture changed createAutoSequences argument");
                state.createAutoSequenceCalls++;
                if (state.generatorFailure != null) throw state.generatorFailure;
                return null;
            }
            rejectForbiddenMutation(state, method);
            switch (method.getName()) {
                case "feature":
                    if (method.getParameterCount() == 0) return features;
                    return args[0].equals(state.studyStepTag) ? state.step : null;
                case "getSolverSequences":
                    String category = String.valueOf(args[0]);
                    if (category.equals("All")) {
                        state.allFilterCalls++;
                        if (state.allFilterFailure != null) throw state.allFilterFailure;
                        return state.allTags;
                    }
                    if (category.equals("SolverSequence")) {
                        state.solverFilterCalls++;
                        if (state.solverFilterFailure != null) throw state.solverFilterFailure;
                        return state.solverSequenceTags;
                    }
                    if (category.equals("None")) {
                        state.noneFilterCalls++;
                        if (state.noneFilterFailure != null) throw state.noneFilterFailure;
                        return state.noneTags;
                    }
                    throw new AssertionError("unexpected solver sequence category: " + category);
                default: return objectMethod(instance, method, args);
            }
        });
    }

    private static StudyFeature stepProxy(State state) {
        return proxy(StudyFeature.class, (instance, method, args) -> {
            rejectForbiddenMutation(state, method);
            switch (method.getName()) {
                case "tag": return state.studyStepTag;
                case "type": return state.expectedSolverType.equals("Time") ? "Transient" : "Stationary";
                case "isActive": return true;
                case "solveFor": return true;
                default: return objectMethod(instance, method, args);
            }
        });
    }

    private static SolverSequence sequenceProxy(State state, String tag, String studyTag,
            boolean attached, String sequenceType) {
        Map<String, SolverFeature> features = new LinkedHashMap<>();
        String[] tags = new String[state.rootFeatures.length];
        for (int i = 0; i < state.rootFeatures.length; i++) {
            tags[i] = state.rootFeatures[i][0];
            features.put(tags[i], featureProxy(state, tags[i], state.rootFeatures[i][1]));
        }
        SolverFeatureList root = tagsAndGet(SolverFeatureList.class, tags, features);
        return proxy(SolverSequence.class, (instance, method, args) -> {
            rejectForbiddenMutation(state, method);
            switch (method.getName()) {
                case "tag": return tag;
                case "getType":
                    state.sequenceMetadataReads++;
                    return "SolverSequence";
                case "getSequenceType":
                    state.sequenceMetadataReads++;
                    return sequenceType;
                case "study":
                    if (method.getParameterCount() == 0) {
                        state.sequenceMetadataReads++;
                        return studyTag;
                    }
                    throw new AssertionError("unexpected solver.study setter");
                case "isAttached":
                    state.sequenceMetadataReads++;
                    return attached;
                case "feature":
                    if (method.getParameterCount() == 0) return root;
                    return features.get(String.valueOf(args[0]));
                default: return objectMethod(instance, method, args);
            }
        });
    }

    private static SolverFeature featureProxy(State state, String tag, String type) {
        return proxy(SolverFeature.class, (instance, method, args) -> {
            rejectForbiddenMutation(state, method);
            if (method.getName().equals("getType") && method.getParameterCount() == 0) return type;
            if (method.getName().equals("tag") && method.getParameterCount() == 0) return tag;
            if (method.getName().equals("getString") && method.getParameterCount() == 1) {
                if (state.studyStepGetterFailure != null) throw state.studyStepGetterFailure;
                if (type.equals("StudyStep") && args[0].equals("study")) return state.linkedStudy;
                if (type.equals("StudyStep") && args[0].equals("studystep")) return state.linkedStep;
                throw new AssertionError("unexpected solver feature getString: " + Arrays.toString(args));
            }
            if (method.getName().equals("feature") && method.getParameterCount() == 0) {
                return tagsOnly(SolverFeatureList.class, new String[0]);
            }
            return objectMethod(instance, method, args);
        });
    }

    private static void rejectForbiddenMutation(State state, Method method) {
        String name = method.getName();
        if (name.equals("run") || name.startsWith("run") || name.equals("continueRun")) {
            state.nativeRunCalls++;
            throw new AssertionError("unexpected native run call: " + name);
        }
        if (name.equals("attach") || name.equals("detach")) {
            state.attachCalls++;
            throw new AssertionError("unexpected sequence attach/detach: " + name);
        }
        if (name.startsWith("set") || name.equals("activate") ||
            (name.startsWith("create") && !name.equals("createAutoSequences")) ||
            (name.equals("study") && method.getParameterCount() > 0)) {
            state.setterCalls++;
            throw new AssertionError("unexpected mutation: " + name);
        }
    }

    private interface Tags { String[] get(); }

    private static <T> T tagsOnly(Class<T> api, String[] tags) {
        return tagsOnly(api, () -> tags);
    }

    private static <T> T tagsOnly(Class<T> api, Tags tags) {
        return proxy(api, (instance, method, args) -> {
            if (method.getName().equals("tags") && method.getParameterCount() == 0) return tags.get();
            return objectMethod(instance, method, args);
        });
    }

    private static <T, E> T tagsAndGet(Class<T> api, String[] tags, Map<String, E> entries) {
        return proxy(api, (instance, method, args) -> {
            if (method.getName().equals("tags") && method.getParameterCount() == 0) return tags;
            if (method.getName().equals("get") && method.getParameterCount() == 1) {
                return entries.get(String.valueOf(args[0]));
            }
            return objectMethod(instance, method, args);
        });
    }

    private static <T> T proxy(Class<T> api, InvocationHandler handler) {
        Object result = Proxy.newProxyInstance(api.getClassLoader(), new Class<?>[]{api}, (instance, method, args) -> {
            if (method.getDeclaringClass() == Object.class) return objectMethod(instance, method, args);
            Object value = handler.invoke(instance, method, args);
            if (value == null && method.getReturnType().isPrimitive() && method.getReturnType() != Void.TYPE) {
                throw new AssertionError("unexpected primitive API call: " + method.getName());
            }
            return value;
        });
        return api.cast(result);
    }

    private static Object objectMethod(Object instance, Method method, Object[] args) {
        switch (method.getName()) {
            case "toString": return "W24Proxy(" + instance.getClass().getInterfaces()[0].getSimpleName() + ")";
            case "hashCode": return System.identityHashCode(instance);
            case "equals": return instance == args[0];
            default: throw new AssertionError("unexpected proxy API call: " + method);
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
