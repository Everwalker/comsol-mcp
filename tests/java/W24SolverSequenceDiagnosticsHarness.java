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
        verifiesCouponAndScienceFieldBindingContracts();
        verifiesScienceContinuousDoseToleranceReadback();
        rejectsMissingNativeGroupBeforeScienceSolverConfiguration();
        System.out.println("W24 solver-sequence diagnostics proxy checks: PASS (" + cases + " cases)");
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
                new String[][]{{"u", "w", "v"}}, nativeKeys(), "exactly {u,w}");
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
            ? W24CureScienceFixture.validateSolverFieldContract(dimension, axisymmetric, tags, names, components, keys)
            : W24CureCouponFixture.validateSolverFieldContract(dimension, axisymmetric, tags, names, components, keys);
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
