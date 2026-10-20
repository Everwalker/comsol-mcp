package comsol_mcp.worker_java;

import com.comsol.model.ParameterEntity;
import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.PhysicsFeatureList;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

/** Executes the production Worker snapshot compiler and Java helper controls per artifact. */
public final class PersistentComsolWorkerW24SingleSourceHarness {
    private static int checks;

    private static final class FeatureSpec {
        final String tag;
        final String type;
        final String fluid;
        final boolean hasFluid;
        final boolean hasOption;
        final String returnedTag;

        FeatureSpec(String tag, String type, String fluid) {
            this(tag, type, fluid, true, true, tag);
        }

        FeatureSpec(String tag, String type, String fluid, boolean hasFluid,
                    boolean hasOption, String returnedTag) {
            this.tag = tag;
            this.type = type;
            this.fluid = fluid;
            this.hasFluid = hasFluid;
            this.hasOption = hasOption;
            this.returnedTag = returnedTag;
        }
    }

    private static final class SemanticResult {
        final String modelTag;
        final String fluid1Tag;
        final String fluid2Tag;

        SemanticResult(String modelTag, String fluid1Tag, String fluid2Tag) {
            this.modelTag = modelTag;
            this.fluid1Tag = fluid1Tag;
            this.fluid2Tag = fluid2Tag;
        }

        @Override public boolean equals(Object other) {
            if (!(other instanceof SemanticResult)) return false;
            SemanticResult value = (SemanticResult) other;
            return modelTag.equals(value.modelTag) && fluid1Tag.equals(value.fluid1Tag) &&
                fluid2Tag.equals(value.fluid2Tag);
        }

        @Override public int hashCode() { return modelTag.hashCode() * 31 + fluid1Tag.hashCode() * 7 + fluid2Tag.hashCode(); }
        @Override public String toString() { return modelTag + "/" + fluid1Tag + "/" + fluid2Tag; }
    }

    private PersistentComsolWorkerW24SingleSourceHarness() { }

    private static void check(boolean ok, String message) {
        checks++;
        if (!ok) throw new AssertionError(message);
    }

    private static Object snapshotAndCompile(Path sourcePath, Path classesPath) throws Exception {
        Method sourceSpec = PersistentComsolWorker.class.getDeclaredMethod("sourceSpec", Map.class);
        sourceSpec.setAccessible(true);
        Map<String, Object> request = new HashMap<>();
        request.put("source_artifact", sourcePath.toString());
        request.put("entrypoint", sourcePath.getFileName().toString().replaceFirst("\\.java$", ""));
        Object snapshot = sourceSpec.invoke(null, request);

        Class<?> sourceSpecClass = Class.forName("comsol_mcp.worker_java.PersistentComsolWorker$SourceSpec");
        Method compileSnapshot = PersistentComsolWorker.class.getDeclaredMethod(
            "compileSnapshot", sourceSpecClass, Path.class);
        compileSnapshot.setAccessible(true);
        Map<?, ?> result = (Map<?, ?>) compileSnapshot.invoke(null, snapshot, classesPath);
        check(Boolean.TRUE.equals(result.get("compiled")), sourcePath.getFileName() + " compileSnapshot: " + result);
        check(Files.isRegularFile(classesPath.resolve(request.get("entrypoint") + ".class")),
            sourcePath.getFileName() + " primary class emitted to its own output");
        return snapshot;
    }

    private static SemanticResult exerciseArtifact(String className, Path classesPath) throws Exception {
        try (URLClassLoader loader = new URLClassLoader(
                new URL[] {classesPath.toUri().toURL()}, PersistentComsolWorkerW24SingleSourceHarness.class.getClassLoader())) {
            Class<?> artifact = Class.forName(className, true, loader);
            check(artifact.getClassLoader() != PersistentComsolWorkerW24SingleSourceHarness.class.getClassLoader(),
                className + " loaded from its own artifact classloader");
            Method phaseResolver = artifact.getDeclaredMethod("resolvePhaseFieldModel", Physics.class);
            phaseResolver.setAccessible(true);
            PhysicsFeature selected = (PhysicsFeature) phaseResolver.invoke(null,
                physics(new String[] {"wall", "semanticModel"},
                    feature("wall", "WettedWall", ""),
                    feature("semanticModel", "PhaseFieldModel", "")));
            check("semanticModel".equals(selected.tag()), className + " unique PhaseFieldModel semantic selection");
            rejects(phaseResolver, physics(new String[] {"wall"}, feature("wall", "WettedWall", "")),
                className + " missing PhaseFieldModel");
            rejects(phaseResolver, physics(new String[] {"phaseA", "phaseB"},
                    feature("phaseA", "PhaseFieldModel", ""), feature("phaseB", "PhaseFieldModel", "")),
                className + " ambiguous PhaseFieldModel");

            Method initialResolver = artifact.getDeclaredMethod("resolvePhaseInitialValueFeatures", Physics.class);
            initialResolver.setAccessible(true);
            Physics initialPhysics = physics(new String[] {"gasNode", "unrelated", "glueNode"},
                feature("glueNode", "init", "Fluid1phipf"),
                feature("gasNode", "initFluid2", "Fluid2phipf"),
                new FeatureSpec("unrelated", "WettedWall", "", false, false, "unrelated"));
            @SuppressWarnings("unchecked")
            Map<String, PhysicsFeature> initial = (Map<String, PhysicsFeature>) initialResolver.invoke(null, initialPhysics);
            String fluid1 = initial.get("Fluid1phipf").tag();
            String fluid2 = initial.get("Fluid2phipf").tag();
            check("glueNode".equals(fluid1) && "gasNode".equals(fluid2),
                className + " order-independent fluid semantics");

            Method requireType = artifact.getDeclaredMethod(
                "requirePropertyValueType", ParameterEntity.class, String.class, String.class);
            requireType.setAccessible(true);
            requireType.invoke(null, parameter("Boolean"), "p", "Boolean");
            requireType.invoke(null, parameter("String"), "p", "String");
            requireType.invoke(null, parameter("Double"), "p", "Double");
            rejects(requireType, parameter("String"), "p", "Boolean", className + " rejects Boolean metadata mismatch");
            rejects(requireType, parameter("Boolean"), "p", "String", className + " rejects String metadata mismatch");
            rejects(requireType, parameter("String"), "p", "Double", className + " rejects Double metadata mismatch");

            Method requireEnum = artifact.getDeclaredMethod(
                "requireStringEnumProperty", ParameterEntity.class, String.class, String.class);
            requireEnum.setAccessible(true);
            requireEnum.invoke(null, parameter("String", new String[] {"userdef", "automatic"}),
                "chiOption", "userdef");
            requireEnum.invoke(null, parameter("String", new String[] {"Incompressible", "Compressible"}),
                "Compressibility", "Incompressible");
            requireEnum.invoke(null, parameter("String", new String[] {"1", "0"}),
                "IncludeGravity", "0");
            requireEnum.invoke(null, parameter("String", new String[] {"1", "0"}),
                "IncludeSurfaceTension", "1");
            requireEnum.invoke(null, parameter("String", new String[] {
                "LibraryCoefficientLiquidGas", "LibraryCoefficientLiquidLiquid",
                "LibraryCoefficientLiquidMixtureGas", "userdef"}),
                "SurfaceTensionCoefficient", "userdef");
            rejects(requireEnum, parameter("String", new String[] {"automatic", "automatic2"}),
                "chiOption", "userdef", className + " rejects missing userdef enum");
            rejects(requireEnum, parameter("String", new String[] {"Compressible"}),
                "Compressibility", "Incompressible", className + " rejects missing Incompressible enum");
            if ("W24StaticShapeFixture".equals(className)) {
                exerciseDeclaredPropertyReader(artifact);
            }
            return new SemanticResult(selected.tag(), fluid1, fluid2);
        }
    }

    private static void exerciseDeclaredPropertyReader(Class<?> artifact) throws Exception {
        Method reader = artifact.getDeclaredMethod(
            "readDeclaredPropertyDefault", ParameterEntity.class, String.class);
        reader.setAccessible(true);
        Map<String, Integer> reads = new HashMap<>();

        Object[][] observedStringProperties = {
            {"IncludeGravity", new String[] {"1", "0"}, "0"},
            {"Compressibility", new String[] {"Incompressible", "WeaklyCompressible", "CompressibleMALT03"}, "Incompressible"},
            {"chiOption", new String[] {"velocity", "userdef"}, "userdef"},
            {"chi", null, "1"},
            {"epsilon_pf", null, "pf.ep_default"},
            {"IncludeSurfaceTension", new String[] {"1", "0"}, "1"},
            {"SurfaceTensionCoefficient", new String[] {"LibraryCoefficientLiquidGas", "LibraryCoefficientLiquidLiquid",
                "LibraryCoefficientLiquidMixtureGas", "userdef"}, "LibraryCoefficientLiquidGas"},
            {"sigma", null, "7e-2[N/m]"},
        };
        for (Object[] property : observedStringProperties) {
            String propertyName = (String) property[0];
            String[] allowed = (String[]) property[1];
            String defaultValue = (String) property[2];
            reads.clear();
            @SuppressWarnings("unchecked")
            Map<String, Object> row = (Map<String, Object>) reader.invoke(null,
                schemaParameter("String", allowed, true, defaultValue, reads), propertyName);
            check("String".equals(row.get("declared_type")) &&
                  "getString".equals(row.get("value_reader")) &&
                  defaultValue.equals(row.get("value")) &&
                  "typed_scalar".equals(row.get("value_representation")) &&
                  row.get("expression_value") == null &&
                  (allowed == null ? row.get("allowed_values") == null :
                      Arrays.asList(allowed).equals(row.get("allowed_values"))),
                "observed W24 property is captured with its exact String type/default: " + propertyName);
            check(reads.equals(java.util.Collections.singletonMap("getString", 1)),
                "observed W24 property uses only getString: " + propertyName);
        }

        reads.clear();
        @SuppressWarnings("unchecked")
        Map<String, Object> genericDoubleRow = (Map<String, Object>) reader.invoke(null,
            schemaParameter("Double", null, true, "raw-expression", reads), "genericDoubleProperty");
        check("Double".equals(genericDoubleRow.get("declared_type")) &&
              "getString".equals(genericDoubleRow.get("value_reader")) &&
              "raw-expression".equals(genericDoubleRow.get("value")) &&
              "raw-expression".equals(genericDoubleRow.get("expression_value")) &&
              "expression_as_string".equals(genericDoubleRow.get("value_representation")),
            "generic Double properties remain raw String reads without evaluation");
        check(reads.equals(java.util.Collections.singletonMap("getString", 1)),
            "generic Double schema read avoids numeric evaluation");

        reads.clear();
        @SuppressWarnings("unchecked")
        Map<String, Object> unsupportedRow = (Map<String, Object>) reader.invoke(null,
            schemaParameter("StringArray", null, true, null, reads), "arrayProperty");
        check("StringArray".equals(unsupportedRow.get("declared_type")) &&
              "UNSUPPORTED_DECLARED_TYPE".equals(unsupportedRow.get("value_status")) && reads.isEmpty(),
            "unsupported schema types are retained without guessed getters or conversion");

        reads.clear();
        @SuppressWarnings("unchecked")
        Map<String, Object> absentRow = (Map<String, Object>) reader.invoke(null,
            schemaParameter("String", null, false, null, reads), "missingProperty");
        check(Boolean.FALSE.equals(absentRow.get("has_property")) &&
              "PROPERTY_ABSENT".equals(absentRow.get("value_status")) && reads.isEmpty(),
            "absent property remains explicit and triggers no value getter");
    }

    private static ParameterEntity schemaParameter(String valueType, String[] allowed,
                                                    boolean present, Object scalarValue,
                                                    Map<String, Integer> reads) {
        return proxy(ParameterEntity.class, (instance, method, args) -> {
            String name = method.getName();
            if ("hasProperty".equals(name)) return present;
            if ("getValueType".equals(name)) return valueType;
            if ("getAllowedPropertyValues".equals(name)) return allowed == null ? null : allowed.clone();
            if ("getBoolean".equals(name) || "getString".equals(name) || "getDouble".equals(name)) {
                reads.put(name, reads.getOrDefault(name, 0) + 1);
                if ("getBoolean".equals(name) && "Boolean".equals(valueType)) return Boolean.TRUE;
                if ("getString".equals(name) && ("String".equals(valueType) || "Double".equals(valueType)))
                    return scalarValue;
                throw new IllegalStateException("wrong getter for declared type " + valueType + ": " + name);
            }
            return objectMethod(instance, method, args);
        });
    }

    private static void rejects(Method method, Object... argsAndLabel) throws Exception {
        String label = (String) argsAndLabel[argsAndLabel.length - 1];
        Object[] args = Arrays.copyOf(argsAndLabel, argsAndLabel.length - 1);
        try {
            method.invoke(null, args);
            throw new AssertionError(label + " unexpectedly passed");
        } catch (InvocationTargetException exception) {
            check(exception.getCause() instanceof IllegalStateException,
                label + " rejected with fail-closed IllegalStateException, got " + exception.getCause());
        }
    }

    private static FeatureSpec feature(String tag, String type, String fluid) {
        return new FeatureSpec(tag, type, fluid);
    }

    private static Physics physics(String[] tags, FeatureSpec... specs) {
        Map<String, FeatureSpec> byTag = new LinkedHashMap<>();
        for (FeatureSpec spec : specs) byTag.put(spec.tag, spec);
        PhysicsFeatureList list = proxy(PhysicsFeatureList.class, (instance, method, args) ->
            "tags".equals(method.getName()) ? tags.clone() : objectMethod(instance, method, args));
        return proxy(Physics.class, (instance, method, args) -> {
            if ("getType".equals(method.getName())) return "PhaseField";
            if ("feature".equals(method.getName()) && method.getParameterCount() == 0) return list;
            if ("feature".equals(method.getName()) && method.getParameterCount() == 1) {
                FeatureSpec spec = byTag.get(String.valueOf(args[0]));
                return spec == null ? null : featureProxy(spec);
            }
            return objectMethod(instance, method, args);
        });
    }

    private static PhysicsFeature featureProxy(FeatureSpec spec) {
        return proxy(PhysicsFeature.class, (instance, method, args) -> {
            String name = method.getName();
            if ("tag".equals(name)) return spec.returnedTag;
            if ("getType".equals(name)) return spec.type;
            if ("hasProperty".equals(name)) {
                String property = String.valueOf(args[0]);
                if ("FluidInDomain".equals(property)) return spec.hasFluid;
                if ("InitialValuesOption".equals(property)) return spec.hasOption;
                return false;
            }
            if ("getValueType".equals(name)) return "String";
            if ("getAllowedPropertyValues".equals(name)) {
                String property = String.valueOf(args[0]);
                if ("InitialValuesOption".equals(property)) return new String[] {"SpecifyPhase", "UserDefined"};
                if ("FluidInDomain".equals(property)) return new String[] {"Fluid1phipf", "Fluid2phipf"};
                return null;
            }
            if ("getString".equals(name)) {
                String property = String.valueOf(args[0]);
                if ("FluidInDomain".equals(property)) return spec.fluid;
                if ("InitialValuesOption".equals(property)) return "SpecifyPhase";
            }
            return objectMethod(instance, method, args);
        });
    }

    private static ParameterEntity parameter(String valueType) {
        return parameter(valueType, new String[] {"allowed"});
    }

    private static ParameterEntity parameter(String valueType, String[] allowed) {
        return proxy(ParameterEntity.class, (instance, method, args) -> {
            if ("hasProperty".equals(method.getName())) return true;
            if ("getValueType".equals(method.getName())) return valueType;
            if ("getAllowedPropertyValues".equals(method.getName())) return allowed.clone();
            return objectMethod(instance, method, args);
        });
    }

    @SuppressWarnings("unchecked")
    private static <T> T proxy(Class<T> type, InvocationHandler handler) {
        return (T) Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[] {type}, handler);
    }

    private static Object objectMethod(Object proxy, Method method, Object[] args) {
        if ("toString".equals(method.getName())) return "synthetic-" + method.getDeclaringClass().getSimpleName();
        if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);
        if ("equals".equals(method.getName())) return proxy == args[0];
        return null;
    }

    private static String sha(Path path) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path));
        StringBuilder text = new StringBuilder();
        for (byte item : digest) text.append(String.format("%02x", item & 255));
        return text.toString();
    }

    public static void main(String[] args) throws Exception {
        Path sourceRoot = Paths.get(args[0]);
        Path outputRoot = Paths.get(args[1]);
        Files.createDirectories(outputRoot);
        Path fixture = sourceRoot.resolve("tools/java/W24StaticShapeFixture.java");
        Path readback = sourceRoot.resolve("tools/java/W24StaticShapeReadback.java");
        String fixtureSha = sha(fixture);
        String readbackSha = sha(readback);
        Path fixtureClasses = Files.createDirectory(outputRoot.resolve("fixture-classes"));
        Path readbackClasses = Files.createDirectory(outputRoot.resolve("readback-classes"));
        snapshotAndCompile(fixture, fixtureClasses);
        snapshotAndCompile(readback, readbackClasses);
        check(!Files.exists(readbackClasses.resolve("W24StaticShapeFixture.class")),
            "readback output contains no fixture artifact");
        check(!Files.exists(fixtureClasses.resolve("W24StaticShapeReadback.class")),
            "fixture output contains no readback artifact");
        SemanticResult fixtureResult = exerciseArtifact("W24StaticShapeFixture", fixtureClasses);
        SemanticResult readbackResult = exerciseArtifact("W24StaticShapeReadback", readbackClasses);
        check(fixtureResult.equals(readbackResult), "two independently compiled artifacts share exact resolver semantics");
        check(fixtureSha.equals(sha(fixture)) && readbackSha.equals(sha(readback)),
            "registered source bytes remain unchanged through compileSnapshot");
        System.out.println("PASS_W24_SINGLE_SOURCE_COMPILE_AND_HELPER_CONTROLS checks=" + checks +
            " fixture_sha256=" + fixtureSha + " readback_sha256=" + readbackSha +
            " engine=NOT_RUN Worker_constructor=NOT_RUN model=NOT_RUN");
    }
}
