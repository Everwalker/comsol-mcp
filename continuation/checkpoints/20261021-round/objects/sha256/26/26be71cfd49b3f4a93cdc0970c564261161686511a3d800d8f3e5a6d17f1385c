package comsol_mcp.worker_java;

import com.comsol.model.ModelParam;
import com.comsol.model.ResultParam;
import com.comsol.util.exceptions.FlException;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Field;
import java.lang.reflect.Proxy;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * No-engine regression for the Worker’s narrow terminal interpolation-error
 * classifier. It calls the exact production helper and starts no COMSOL API
 * model, server, study, or solver.
 */
public final class FunctionEvaluateWorkerClassificationProbe {
    private static final String TAG = "Interpolation_function_is_out_of_range";
    private static final String REQUEST_ID = "native-request-test-17";

    private FunctionEvaluateWorkerClassificationProbe() {}

    private static Object proxy(Class<?>... interfaces) {
        InvocationHandler handler = (proxy, method, args) -> {
            Class<?> type = method.getReturnType();
            if (type == boolean.class) return false;
            if (type == byte.class) return (byte) 0;
            if (type == short.class) return (short) 0;
            if (type == int.class) return 0;
            if (type == long.class) return 0L;
            if (type == float.class) return 0.0f;
            if (type == double.class) return 0.0d;
            if (type == char.class) return '\0';
            return null;
        };
        return Proxy.newProxyInstance(
            FunctionEvaluateWorkerClassificationProbe.class.getClassLoader(), interfaces, handler);
    }

    private static void require(boolean condition, String name) {
        if (!condition) throw new AssertionError(name);
    }

    private static void rejected(Object target, String method, List<?> args, Throwable failure, String id,
                                 String name) {
        require(PersistentComsolWorker.terminalInterpolationRangeFailure(
            target, method, args, failure, id) == null, name);
    }

    private static Throwable uninitializedFlException(Class<?> type, String message) throws Exception {
        // FlException constructors initialize COMSOL's Eclipse locale service;
        // allocate only the Java throwable shell so this regression stays
        // entirely outside the COMSOL runtime and engine.
        Class<?> unsafeType = Class.forName("sun.misc.Unsafe");
        Field singleton = unsafeType.getDeclaredField("theUnsafe");
        singleton.setAccessible(true);
        Object unsafe = singleton.get(null);
        Object instance = unsafeType.getMethod("allocateInstance", Class.class).invoke(unsafe, type);
        Field detailMessage = Throwable.class.getDeclaredField("detailMessage");
        long offset = (Long) unsafeType.getMethod("objectFieldOffset", Field.class)
            .invoke(unsafe, detailMessage);
        unsafeType.getMethod("putObject", Object.class, long.class, Object.class)
            .invoke(unsafe, instance, offset, message);
        return (Throwable) instance;
    }

    private static final class ChildFlException extends FlException {
        ChildFlException(String message) { super(message); }
    }

    public static void main(String[] args) throws Exception {
        Object modelParam = proxy(ModelParam.class);
        Object resultParam = proxy(ResultParam.class);
        Object dualParam = proxy(ModelParam.class, ResultParam.class);
        FlException exact = (FlException) uninitializedFlException(FlException.class, TAG);
        Map<String, Object> accepted = PersistentComsolWorker.terminalInterpolationRangeFailure(
            modelParam, "evaluateComplex", Collections.<Object>singletonList("root.f(-0.1[m])"),
            exact, REQUEST_ID);
        require(accepted != null, "exact ModelParam range tag must be terminal");
        require(Boolean.TRUE.equals(accepted.get("terminal_sample_error")), "terminal marker");
        require(Boolean.FALSE.equals(accepted.get("execution_state_unknown")), "known completion marker");
        require(REQUEST_ID.equals(accepted.get("native_request_id")), "request id preservation");
        require(TAG.equals(accepted.get("error_tag")), "raw tag preservation");

        rejected(resultParam, "evaluateComplex", Collections.<Object>singletonList("root.f(-0.1[m])"),
            exact, REQUEST_ID, "ResultParam excluded");
        rejected(dualParam, "evaluateComplex", Collections.<Object>singletonList("root.f(-0.1[m])"),
            exact, REQUEST_ID, "dual ModelParam/ResultParam excluded");
        rejected(new Object(), "evaluateComplex", Collections.<Object>singletonList("x"),
            exact, REQUEST_ID, "wrong receiver excluded");
        rejected(modelParam, "evaluateUnit", Collections.<Object>singletonList("root.f(-0.1[m])"),
            exact, REQUEST_ID, "wrong method excluded");
        rejected(modelParam, "evaluateComplex", Collections.emptyList(), exact, REQUEST_ID,
            "zero-argument signature excluded");
        rejected(modelParam, "evaluateComplex", Arrays.<Object>asList("x", "m"), exact, REQUEST_ID,
            "overloaded signature excluded");
        rejected(modelParam, "evaluateComplex", Collections.<Object>singletonList(1.0), exact, REQUEST_ID,
            "non-string argument excluded");
        rejected(modelParam, "evaluateComplex", Collections.<Object>singletonList("x"),
            new IllegalArgumentException(TAG), REQUEST_ID, "non-FlException excluded");
        rejected(modelParam, "evaluateComplex", Collections.<Object>singletonList("x"),
            uninitializedFlException(ChildFlException.class, TAG), REQUEST_ID, "FlException subclass excluded");
        rejected(modelParam, "evaluateComplex", Collections.<Object>singletonList("x"),
            uninitializedFlException(FlException.class, "different-tag"), REQUEST_ID, "different tag excluded");

        System.out.println("PASS FunctionEvaluateWorkerClassificationProbe; no COMSOL engine started");
    }
}
