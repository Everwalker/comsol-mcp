import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.PhysicsFeatureList;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.LinkedHashMap;
import java.util.Map;

/** Exercises the production semantic resolver with API-interface proxies only. */
public final class W24PhaseFieldResolverControl {
    private static final class FeatureSpec {
        final String tag;
        final String type;
        final String fluid;
        final boolean hasFluid;
        final boolean hasOption;
        final String reportedTag;

        FeatureSpec(String tag, String type, String fluid) {
            this(tag, type, fluid, true, true, tag);
        }

        FeatureSpec(String tag, String type, String fluid, boolean hasFluid,
                    boolean hasOption, String reportedTag) {
            this.tag = tag;
            this.type = type;
            this.fluid = fluid;
            this.hasFluid = hasFluid;
            this.hasOption = hasOption;
            this.reportedTag = reportedTag;
        }
    }

    private static int passed;

    private W24PhaseFieldResolverControl() { }

    public static void main(String[] args) throws Exception {
        positiveOrderIndependentMapping();
        rejectsMissingFluid();
        rejectsDuplicateFluid();
        rejectsUnknownFluid();
        rejectsIncompletePropertyPair();
        rejectsRepeatedFeatureTags();
        rejectsFeatureTagMismatch();
        rejectsBlankFeatureType();
        System.out.println("W24_PHASEFIELD_RESOLVER_CONTROLS_PASS tests=" + passed);
    }

    private static void positiveOrderIndependentMapping() throws Exception {
        Map<String, PhysicsFeature> result = resolve(
            new String[] {"featureGas", "featureGlue"},
            feature("featureGlue", "Initial_values", "Fluid1phipf"),
            feature("featureGas", "Initial_values,_Fluid_2", "Fluid2phipf"));
        check(result.size() == 2, "positive resolver result count");
        check("featureGlue".equals(result.get("Fluid1phipf").tag()), "Fluid1 semantic mapping");
        check("featureGas".equals(result.get("Fluid2phipf").tag()), "Fluid2 semantic mapping");
    }

    private static void rejectsMissingFluid() throws Exception {
        rejects("missing fluid", new String[] {"featureGlue"},
            feature("featureGlue", "Initial_values", "Fluid1phipf"));
    }

    private static void rejectsDuplicateFluid() throws Exception {
        rejects("duplicate fluid", new String[] {"g1", "g2", "gas"},
            feature("g1", "Initial_values", "Fluid1phipf"),
            feature("g2", "Initial_values", "Fluid1phipf"),
            feature("gas", "Initial_values,_Fluid_2", "Fluid2phipf"));
    }

    private static void rejectsUnknownFluid() throws Exception {
        rejects("unknown fluid", new String[] {"g1", "gas"},
            feature("g1", "Initial_values", "UnknownFluid"),
            feature("gas", "Initial_values,_Fluid_2", "Fluid2phipf"));
    }

    private static void rejectsIncompletePropertyPair() throws Exception {
        rejects("incomplete properties", new String[] {"g1", "gas"},
            new FeatureSpec("g1", "Initial_values", "Fluid1phipf", true, false, "g1"),
            feature("gas", "Initial_values,_Fluid_2", "Fluid2phipf"));
    }

    private static void rejectsRepeatedFeatureTags() throws Exception {
        rejects("duplicate tags", new String[] {"g1", "g1", "gas"},
            feature("g1", "Initial_values", "Fluid1phipf"),
            feature("gas", "Initial_values,_Fluid_2", "Fluid2phipf"));
    }

    private static void rejectsFeatureTagMismatch() throws Exception {
        rejects("tag mismatch", new String[] {"g1", "gas"},
            new FeatureSpec("g1", "Initial_values", "Fluid1phipf", true, true, "not-g1"),
            feature("gas", "Initial_values,_Fluid_2", "Fluid2phipf"));
    }

    private static void rejectsBlankFeatureType() throws Exception {
        rejects("blank feature type", new String[] {"g1", "gas"},
            feature("g1", "  ", "Fluid1phipf"),
            feature("gas", "Initial_values,_Fluid_2", "Fluid2phipf"));
    }

    private static FeatureSpec feature(String tag, String type, String fluid) {
        return new FeatureSpec(tag, type, fluid);
    }

    private static Map<String, PhysicsFeature> resolve(String[] tags, FeatureSpec... specs)
            throws Exception {
        Map<String, FeatureSpec> features = new LinkedHashMap<>();
        for (FeatureSpec spec : specs) features.put(spec.tag, spec);
        final PhysicsFeatureList list = proxy(PhysicsFeatureList.class, (proxy, method, args) -> {
            if ("tags".equals(method.getName())) return tags.clone();
            return objectMethod(proxy, method, args);
        });
        final Physics phaseField = proxy(Physics.class, (proxy, method, args) -> {
            if ("feature".equals(method.getName()) && method.getParameterCount() == 0) return list;
            if ("feature".equals(method.getName()) && method.getParameterCount() == 1) {
                FeatureSpec spec = features.get(String.valueOf(args[0]));
                return spec == null ? null : featureProxy(spec);
            }
            return objectMethod(proxy, method, args);
        });
        Method resolver = W24StaticShapeFixture.class.getDeclaredMethod(
            "resolvePhaseInitialValueFeatures", Physics.class);
        resolver.setAccessible(true);
        @SuppressWarnings("unchecked")
        Map<String, PhysicsFeature> result = (Map<String, PhysicsFeature>) resolver.invoke(null, phaseField);
        return result;
    }

    private static void rejects(String name, String[] tags, FeatureSpec... specs) throws Exception {
        try {
            resolve(tags, specs);
            throw new AssertionError(name + " unexpectedly passed");
        } catch (java.lang.reflect.InvocationTargetException exception) {
            Throwable cause = exception.getCause();
            if (!(cause instanceof IllegalStateException)) {
                throw new AssertionError(name + " threw " + cause, cause);
            }
            passed++;
        }
    }

    private static PhysicsFeature featureProxy(FeatureSpec spec) {
        return proxy(PhysicsFeature.class, (proxy, method, args) -> {
            String name = method.getName();
            if ("tag".equals(name)) return spec.reportedTag;
            if ("getType".equals(name)) return spec.type;
            if ("hasProperty".equals(name)) {
                String property = String.valueOf(args[0]);
                if ("FluidInDomain".equals(property)) return spec.hasFluid;
                if ("InitialValuesOption".equals(property)) return spec.hasOption;
                return false;
            }
            if ("getString".equals(name)) {
                return "FluidInDomain".equals(String.valueOf(args[0])) ? spec.fluid : "";
            }
            return objectMethod(proxy, method, args);
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

    private static void check(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
        passed++;
    }
}
