import com.comsol.model.physics.Physics;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.physics.PhysicsFeatureList;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.LinkedHashMap;
import java.util.Map;

/** Offline proxy checks for the fixture's private DistributedODE resolver. */
public final class W24DistributedODEResolverHarness {
    private static final Method RESOLVER = resolverMethod();

    private W24DistributedODEResolverHarness() { }

    public static void main(String[] args) throws Exception {
        resolvesRenamedEquationAlongsideControls();
        rejectsMissingEquationWithObservedFeatures();
        rejectsDuplicateEquationsWithObservedFeatures();
        System.out.println("W24 DistributedODE resolver proxy checks: PASS (3 cases)");
    }

    private static Method resolverMethod() {
        try {
            Method method = W24CureCouponFixture.class.getDeclaredMethod(
                "distributedOdeFeature", Physics.class);
            method.setAccessible(true);
            return method;
        } catch (ReflectiveOperationException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private static void resolvesRenamedEquationAlongsideControls() throws Exception {
        Map<String, String> types = new LinkedHashMap<>();
        types.put("dode1", "DistributedODE");
        types.put("init1", "init");
        types.put("other1", "OtherFeature");
        PhysicsFeature selected = resolve(proxyPhysics("odeAlpha", types));
        require("dode1".equals(selected.tag()), "renamed equation tag was not selected");
        require("DistributedODE".equals(selected.getType()), "equation type readback differs");
    }

    private static void rejectsMissingEquationWithObservedFeatures() throws Exception {
        Map<String, String> types = new LinkedHashMap<>();
        types.put("init1", "init");
        types.put("other1", "OtherFeature");
        expectRejected(proxyPhysics("odeMissing", types), "init1=init", "other1=OtherFeature");
    }

    private static void rejectsDuplicateEquationsWithObservedFeatures() throws Exception {
        Map<String, String> types = new LinkedHashMap<>();
        types.put("dode1", "DistributedODE");
        types.put("dode2", "DistributedODE");
        types.put("init1", "init");
        expectRejected(proxyPhysics("odeDuplicate", types),
            "dode1=DistributedODE", "dode2=DistributedODE", "init1=init");
    }

    private static PhysicsFeature resolve(Physics physics) throws Exception {
        try {
            return (PhysicsFeature) RESOLVER.invoke(null, physics);
        } catch (InvocationTargetException exception) {
            Throwable cause = exception.getCause();
            if (cause instanceof Exception) throw (Exception) cause;
            throw exception;
        }
    }

    private static void expectRejected(Physics physics, String... observed) throws Exception {
        try {
            resolve(physics);
            throw new AssertionError("resolver accepted a non-unique DistributedODE feature set");
        } catch (IllegalStateException expected) {
            require(expected.getMessage().contains("expected exactly one direct DistributedODE feature"),
                "rejection omitted the uniqueness requirement: " + expected.getMessage());
            for (String feature : observed) {
                require(expected.getMessage().contains(feature),
                    "rejection omitted observed feature " + feature + ": " + expected.getMessage());
            }
        }
    }

    private static Physics proxyPhysics(String physicsTag, Map<String, String> types) {
        Map<String, PhysicsFeature> features = new LinkedHashMap<>();
        for (Map.Entry<String, String> entry : types.entrySet()) {
            String featureTag = entry.getKey();
            String featureType = entry.getValue();
            features.put(featureTag, proxy(PhysicsFeature.class, (proxy, method, args) -> {
                if (method.getName().equals("getType") && method.getParameterCount() == 0) {
                    return featureType;
                }
                if (method.getName().equals("tag") && method.getParameterCount() == 0) {
                    return featureTag;
                }
                return objectMethod(proxy, method, args);
            }));
        }

        PhysicsFeatureList featureList = proxy(PhysicsFeatureList.class, (proxy, method, args) -> {
            if (method.getName().equals("tags") && method.getParameterCount() == 0) {
                return features.keySet().toArray(new String[0]);
            }
            return objectMethod(proxy, method, args);
        });

        return proxy(Physics.class, (proxy, method, args) -> {
            if (method.getName().equals("feature") && method.getParameterCount() == 0) {
                return featureList;
            }
            if (method.getName().equals("feature") && method.getParameterCount() == 1) {
                return features.get(String.valueOf(args[0]));
            }
            if (method.getName().equals("tag") && method.getParameterCount() == 0) {
                return physicsTag;
            }
            return objectMethod(proxy, method, args);
        });
    }

    private static <T> T proxy(Class<T> api, InvocationHandler handler) {
        Object proxy = Proxy.newProxyInstance(
            api.getClassLoader(), new Class<?>[]{api}, (instance, method, args) -> {
                if (method.getDeclaringClass() == Object.class) {
                    return objectMethod(instance, method, args);
                }
                Object result = handler.invoke(instance, method, args);
                if (result == null && method.getReturnType().isPrimitive()) {
                    throw new AssertionError("unexpected primitive API call: " + method.getName());
                }
                if (result == null && method.getName().equals("getType")) {
                    throw new AssertionError("feature type proxy unexpectedly returned null");
                }
                return result;
            });
        return api.cast(proxy);
    }

    private static Object objectMethod(Object proxy, Method method, Object[] args) {
        switch (method.getName()) {
            case "toString": return "W24Proxy(" + proxy.getClass().getInterfaces()[0].getSimpleName() + ")";
            case "hashCode": return System.identityHashCode(proxy);
            case "equals": return proxy == args[0];
            default: throw new AssertionError("unexpected proxy API call: " + method);
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
