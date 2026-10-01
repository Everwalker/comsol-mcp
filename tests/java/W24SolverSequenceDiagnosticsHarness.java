import com.comsol.model.ComponentMeshList;
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
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

/** Offline proxies for W24 solver-sequence diagnostics and strict selection. */
public final class W24SolverSequenceDiagnosticsHarness {
    private static final String STUDY_TAG = "stdUV";
    private static final Method CREATE_AND_REQUIRE = method();

    private W24SolverSequenceDiagnosticsHarness() { }

    public static void main(String[] args) throws Exception {
        exactFilterIsTheOnlySelector();
        rejectsZeroExactSequences();
        rejectsMultipleExactSequences();
        rejectsNonattachedExactSequence();
        rejectsExactSequenceAttachedToWrongStudy();
        rejectsAllOnlyCopyStoredAndParametricSequences();
        diagnosticGetterErrorIsRecordedAndDoesNotFallback();
        generatorFailureRetainsCauseAndBothSnapshots();
        largeTagArraysStayBoundedAndReportTruncation();
        System.out.println("W24 solver-sequence diagnostics proxy checks: PASS (9 cases)");
    }

    private static Method method() {
        try {
            Method result = W24CureCouponFixture.class.getDeclaredMethod(
                "createAndRequireUniqueAttachedSolverSequence", Model.class, Study.class,
                StudyFeature.class, String.class);
            result.setAccessible(true);
            return result;
        } catch (ReflectiveOperationException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private static void exactFilterIsTheOnlySelector() throws Exception {
        State state = new State();
        state.globalTags = new String[]{"solGlobalOnly"};
        state.allTags = new String[]{"solAllOnly"};
        state.exactTags = new String[]{"solExact"};
        state.addSequence("solGlobalOnly", "stdOther");
        state.addSequence("solAllOnly", "stdOther");
        SolverSequence selected = state.addSequence("solExact", STUDY_TAG);

        SolverSequence result = create(state);
        require(result == selected, "selection did not use the exact SolverSequence filter");
        require(state.createAutoSequenceCalls == 1, "auto-sequence generation call count differs");
        require(state.exactFilterCalls == 3, "before/after/strict exact-filter reads were not made");
        require(state.allFilterCalls == 2, "All filter should be diagnostic-only before and after generation");
        require(state.meshTagReads == 2, "mesh tags were not captured in both snapshots");
        require(state.physicsSolveForReads == 2, "physics URI/solveFor was not captured in both snapshots");
        assertNoForbiddenMutations(state);
    }

    private static void rejectsZeroExactSequences() throws Exception {
        State state = new State();
        IllegalStateException failure = expectFailure(state,
            "expected one generated solver sequence for " + STUDY_TAG, "got []");
        require(state.exactFilterCalls == 3, "zero exact result was not checked after generation");
        require(state.allFilterCalls == 2, "All diagnostics were not limited to pre/post snapshots");
        require(failure.getMessage().contains("study.getSolverSequences(SolverSequence)=[]"),
            "zero-result diagnostics omitted the exact filter result");
    }

    private static void rejectsMultipleExactSequences() throws Exception {
        State state = new State();
        state.globalTags = new String[]{"solA", "solB"};
        state.allTags = new String[]{"solA", "solB"};
        state.exactTags = new String[]{"solA", "solB"};
        state.addSequence("solA", STUDY_TAG);
        state.addSequence("solB", STUDY_TAG);

        IllegalStateException failure = expectFailure(state,
            "expected one generated solver sequence for " + STUDY_TAG, "got [solA, solB]");
        require(failure.getMessage().contains("study.getSolverSequences(SolverSequence)=[solA, solB]"),
            "multiple-result diagnostics omitted both exact candidates");
    }

    private static void rejectsNonattachedExactSequence() throws Exception {
        State state = new State();
        state.globalTags = new String[]{"solDetached"};
        state.allTags = new String[]{"solDetached"};
        state.exactTags = new String[]{"solDetached"};
        state.addSequence("solDetached", STUDY_TAG, false, "TimeDependent");

        expectFailure(state,
            "generated solver sequence is not attached to " + STUDY_TAG +
                ": solDetached -> " + STUDY_TAG + ", isAttached=false");
    }

    private static void rejectsExactSequenceAttachedToWrongStudy() throws Exception {
        State state = new State();
        state.globalTags = new String[]{"solOtherStudy"};
        state.allTags = new String[]{"solOtherStudy"};
        state.exactTags = new String[]{"solOtherStudy"};
        state.addSequence("solOtherStudy", "stdOther", true, "TimeDependent");

        expectFailure(state,
            "generated solver sequence is not attached to " + STUDY_TAG +
                ": solOtherStudy -> stdOther, isAttached=true");
    }

    private static void rejectsAllOnlyCopyStoredAndParametricSequences() throws Exception {
        State state = new State();
        state.globalTags = new String[]{"solCopy", "solStored", "solParametric"};
        state.allTags = state.globalTags;
        state.addSequence("solCopy", "stdOther", true, "Copy");
        state.addSequence("solStored", "stdOther", true, "StoredSolution");
        state.addSequence("solParametric", "stdOther", true, "Parametric");

        IllegalStateException failure = expectFailure(state,
            "expected one generated solver sequence for " + STUDY_TAG, "got []");
        require(state.exactFilterCalls == 3 && state.allFilterCalls == 2,
            "All-only sequences changed the exact-filter selection path");
        for (String sequenceDetail : new String[]{
                "solver[solCopy].sequenceType=Copy",
                "solver[solStored].sequenceType=StoredSolution",
                "solver[solParametric].sequenceType=Parametric"}) {
            require(failure.getMessage().contains(sequenceDetail),
                "All-only sequence diagnostic was omitted: " + sequenceDetail);
        }
    }

    private static void diagnosticGetterErrorIsRecordedAndDoesNotFallback() throws Exception {
        State state = new State();
        state.globalTags = new String[]{"solDecoy"};
        state.allThrows = true;
        state.exactResponses = new String[][]{
            {"solExact"}, {"solExact"}, new String[0]
        };
        state.addSequence("solDecoy", "stdOther");
        state.addSequence("solExact", STUDY_TAG);

        IllegalStateException failure = expectFailure(state,
            "expected one generated solver sequence for " + STUDY_TAG, "got []",
            "study.getSolverSequences(All)=READ_ERROR(IllegalStateException: synthetic All unavailable)");
        require(state.createAutoSequenceCalls == 1, "diagnostic getter failure blocked auto generation");
        require(state.exactFilterCalls == 3 && state.allFilterCalls == 2,
            "diagnostic error changed the exact-only selector");
        require(failure.getMessage().contains("solver_diagnostic_before={") &&
            failure.getMessage().contains("solver_diagnostic_after={"),
            "diagnostic getter error omitted pre/post snapshots");
    }

    private static void generatorFailureRetainsCauseAndBothSnapshots() throws Exception {
        State state = new State();
        state.generatorFailure = new IllegalArgumentException("synthetic generator failure");
        IllegalStateException failure = expectFailure(state,
            "createAutoSequences(\"sol\") failed for " + STUDY_TAG);
        require(failure.getCause() == state.generatorFailure, "generator failure cause was not preserved");
        require(state.exactFilterCalls == 2 && state.allFilterCalls == 2,
            "pre/post snapshots were not both read after generator failure");
        require(failure.getMessage().contains("solver_diagnostic_before={") &&
            failure.getMessage().contains("solver_diagnostic_after={"),
            "generator failure omitted pre/post snapshots");
    }

    private static void largeTagArraysStayBoundedAndReportTruncation() throws Exception {
        State state = new State();
        state.globalTags = indexedTags("solGlobal", 100);
        state.allTags = indexedTags("solAll", 100);
        for (String tag : state.globalTags) state.addSequence(tag, STUDY_TAG);
        for (String tag : state.allTags) state.addSequence(tag, "stdOther");

        IllegalStateException failure = expectFailure(state,
            "expected one generated solver sequence for " + STUDY_TAG, "got []",
            "model.sol_tags=[solGlobal00", "[TRUNCATED 32/100]");
        require(state.sequenceLookupCalls == 64,
            "diagnostic sequence lookups exceeded 32 candidates in either snapshot: " +
                state.sequenceLookupCalls);
        require(state.sequenceMetadataReads == 256,
            "sequence metadata reads exceeded the 32-sequence bound: " + state.sequenceMetadataReads);
        require(state.rootFeatureTypeReads == 128,
            "root feature type reads exceeded the 32-sequence bound: " + state.rootFeatureTypeReads);
        require(!failure.getMessage().contains("solGlobal99") && !failure.getMessage().contains("solAll99"),
            "diagnostic details unexpectedly reached beyond the bounded tag prefixes");
    }

    private static String[] indexedTags(String prefix, int count) {
        String[] tags = new String[count];
        for (int i = 0; i < count; i++) tags[i] = prefix + (i < 10 ? "0" : "") + i;
        return tags;
    }

    private static SolverSequence create(State state) throws Exception {
        try {
            return (SolverSequence) CREATE_AND_REQUIRE.invoke(null, state.model, state.study,
                state.step, STUDY_TAG);
        } catch (InvocationTargetException exception) {
            Throwable cause = exception.getCause();
            if (cause instanceof Exception) throw (Exception) cause;
            throw exception;
        }
    }

    private static IllegalStateException expectFailure(State state, String... messageFragments)
            throws Exception {
        IllegalStateException failure;
        try {
            create(state);
            throw new AssertionError("solver-sequence operation unexpectedly succeeded");
        } catch (IllegalStateException expected) {
            failure = expected;
        }
        for (String fragment : messageFragments) {
            require(failure.getMessage().contains(fragment),
                "failure omitted expected message fragment " + fragment + ": " + failure.getMessage());
        }
        require(failure.getMessage().contains("solver_diagnostic_before={") &&
            failure.getMessage().contains("solver_diagnostic_after={"),
            "failure omitted pre/post snapshots");
        require(state.createAutoSequenceCalls == 1, "failure path did not call generator exactly once");
        assertNoForbiddenMutations(state);
        return failure;
    }

    private static void assertNoForbiddenMutations(State state) {
        require(state.nativeRunCalls == 0, "proxy observed a solver/study run");
        require(state.attachCalls == 0, "proxy observed a sequence attach/detach call");
        require(state.setterCalls == 0, "proxy observed an unexpected setter or create call");
    }

    private static final class SequenceSpec {
        private final String tag;
        private final String studyTag;
        private final boolean attached;
        private final String sequenceType;

        private SequenceSpec(String tag, String studyTag, boolean attached, String sequenceType) {
            this.tag = tag;
            this.studyTag = studyTag;
            this.attached = attached;
            this.sequenceType = sequenceType;
        }
    }

    private static final class State {
        private String[] globalTags = new String[0];
        private String[] allTags = new String[0];
        private String[] exactTags = new String[0];
        private String[][] exactResponses;
        private boolean allThrows;
        private RuntimeException generatorFailure;
        private int createAutoSequenceCalls;
        private int exactFilterCalls;
        private int allFilterCalls;
        private int meshTagReads;
        private int physicsSolveForReads;
        private int sequenceLookupCalls;
        private int sequenceMetadataReads;
        private int rootFeatureTypeReads;
        private int nativeRunCalls;
        private int attachCalls;
        private int setterCalls;
        private final Map<String, SolverSequence> sequences = new LinkedHashMap<>();
        private final Model model = modelProxy(this);
        private final Study study = studyProxy(this);
        private final StudyFeature step = stepProxy(this);

        private SolverSequence addSequence(String tag, String studyTag) {
            return addSequence(tag, studyTag, true, "TimeDependent");
        }

        private SolverSequence addSequence(String tag, String studyTag, boolean attached, String sequenceType) {
            SequenceSpec spec = new SequenceSpec(tag, studyTag, attached, sequenceType);
            SolverSequence result = sequenceProxy(this, spec);
            sequences.put(tag, result);
            return result;
        }
    }

    private static Model modelProxy(State state) {
        StudyList studies = tagsOnly(StudyList.class, new String[]{STUDY_TAG});
        Physics physics = proxy(Physics.class, (instance, method, args) -> {
            switch (method.getName()) {
                case "getType": return "SolidMechanics";
                case "resolveModelPath": return "comp1.solid";
                default: return objectMethod(instance, method, args);
            }
        });
        PhysicsList physicsList = tagsOnly(PhysicsList.class, new String[]{"solid"});
        ModelNode component = componentProxy(state);
        SolverSequenceList solverList = tagsOnly(SolverSequenceList.class, () -> state.globalTags);

        return proxy(Model.class, (instance, method, args) -> {
            switch (method.getName()) {
                case "study":
                    if (method.getParameterCount() == 0) return studies;
                    throw new AssertionError("unexpected model.study overload");
                case "physics":
                    if (method.getParameterCount() == 0) return physicsList;
                    if (method.getParameterCount() == 1 && "solid".equals(args[0])) return physics;
                    throw new AssertionError("unexpected model.physics call: " + Arrays.toString(args));
                case "component":
                    if (method.getParameterCount() == 1 && "comp1".equals(args[0])) return component;
                    throw new AssertionError("unexpected model.component call: " + Arrays.toString(args));
                case "sol":
                    if (method.getParameterCount() == 0) return solverList;
                    if (method.getParameterCount() == 1) {
                        state.sequenceLookupCalls++;
                        return state.sequences.get(String.valueOf(args[0]));
                    }
                    throw new AssertionError("unexpected model.sol overload");
                default: return objectMethod(instance, method, args);
            }
        });
    }

    private static ModelNode componentProxy(State state) {
        ComponentMeshList meshes = tagsOnly(ComponentMeshList.class, () -> {
            state.meshTagReads++;
            return new String[]{"mesh1"};
        });
        return proxy(ModelNode.class, (instance, method, args) -> {
            if (method.getName().equals("mesh") && method.getParameterCount() == 0) return meshes;
            return objectMethod(instance, method, args);
        });
    }

    private static Study studyProxy(State state) {
        StudyFeatureList features = tagsOnly(StudyFeatureList.class, new String[]{"time", "init1"});
        return proxy(Study.class, (instance, method, args) -> {
            if (method.getName().equals("createAutoSequences")) {
                require("sol".equals(args[0]), "fixture changed auto-sequence argument");
                state.createAutoSequenceCalls++;
                if (state.generatorFailure != null) throw state.generatorFailure;
                return null;
            }
            rejectForbiddenMutation(state, method);
            switch (method.getName()) {
                case "feature":
                    if (method.getParameterCount() == 0) return features;
                    throw new AssertionError("unexpected study.feature overload");
                case "getSolverSequences":
                    String filter = String.valueOf(args[0]);
                    if ("All".equals(filter)) {
                        state.allFilterCalls++;
                        if (state.allThrows) throw new IllegalStateException("synthetic All unavailable");
                        return state.allTags;
                    }
                    if ("SolverSequence".equals(filter)) {
                        int index = state.exactFilterCalls++;
                        if (state.exactResponses != null) {
                            return state.exactResponses[Math.min(index, state.exactResponses.length - 1)];
                        }
                        return state.exactTags;
                    }
                    throw new AssertionError("unexpected solver sequence filter: " + filter);
                default: return objectMethod(instance, method, args);
            }
        });
    }

    private static StudyFeature stepProxy(State state) {
        return proxy(StudyFeature.class, (instance, method, args) -> {
            rejectForbiddenMutation(state, method);
            switch (method.getName()) {
                case "tag": return "time";
                case "type": return "Transient";
                case "isActive": return true;
                case "solveFor":
                    state.physicsSolveForReads++;
                    return true;
                default: return objectMethod(instance, method, args);
            }
        });
    }

    private static SolverSequence sequenceProxy(State state, SequenceSpec spec) {
        String[] featureTags = {"st1", "t1"};
        Map<String, SolverFeature> features = new LinkedHashMap<>();
        features.put("st1", featureProxy(state, "StudyStep"));
        features.put("t1", featureProxy(state, "Time"));
        SolverFeatureList featureList = tagsAndGet(SolverFeatureList.class, featureTags, features);
        return proxy(SolverSequence.class, (instance, method, args) -> {
            rejectForbiddenMutation(state, method);
            switch (method.getName()) {
                case "tag": return spec.tag;
                case "getType":
                    state.sequenceMetadataReads++;
                    return "SolverSequence";
                case "getSequenceType":
                    state.sequenceMetadataReads++;
                    return spec.sequenceType;
                case "study":
                    if (method.getParameterCount() == 0) {
                        state.sequenceMetadataReads++;
                        return spec.studyTag;
                    }
                    throw new AssertionError("unexpected solver study setter");
                case "isAttached":
                    state.sequenceMetadataReads++;
                    return spec.attached;
                case "feature":
                    if (method.getParameterCount() == 0) return featureList;
                    if (method.getParameterCount() == 1) return features.get(String.valueOf(args[0]));
                    throw new AssertionError("unexpected sequence.feature overload");
                default: return objectMethod(instance, method, args);
            }
        });
    }

    private static SolverFeature featureProxy(State state, String type) {
        return proxy(SolverFeature.class, (instance, method, args) -> {
            rejectForbiddenMutation(state, method);
            if (method.getName().equals("getType") && method.getParameterCount() == 0) {
                state.rootFeatureTypeReads++;
                return type;
            }
            return objectMethod(instance, method, args);
        });
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

    private static void rejectForbiddenMutation(State state, Method method) {
        String name = method.getName();
        if (name.equals("run") || name.startsWith("run") ||
            name.equals("runNoGen") || name.equals("continueRun")) {
            state.nativeRunCalls++;
            throw new AssertionError("unexpected study/solver run call: " + name);
        }
        if (name.equals("attach") || name.equals("detach")) {
            state.attachCalls++;
            throw new AssertionError("unexpected solver attach call: " + name);
        }
        if (name.startsWith("set") || name.equals("activate") || name.startsWith("create") ||
            (name.equals("study") && method.getParameterCount() > 0)) {
            state.setterCalls++;
            throw new AssertionError("unexpected setter/create call: " + name);
        }
    }

    private static <T> T proxy(Class<T> api, InvocationHandler handler) {
        Object result = Proxy.newProxyInstance(api.getClassLoader(), new Class<?>[]{api}, (instance, method, args) -> {
            if (method.getDeclaringClass() == Object.class) return objectMethod(instance, method, args);
            Object value = handler.invoke(instance, method, args);
            if (value == null && method.getReturnType().isPrimitive() &&
                method.getReturnType() != Void.TYPE) {
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
