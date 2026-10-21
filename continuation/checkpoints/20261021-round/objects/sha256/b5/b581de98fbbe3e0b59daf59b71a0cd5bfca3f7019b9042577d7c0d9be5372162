"""Source and provenance guards for W24 Phase Field setup ordering."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
JAVA_PATH = ROOT / "tools/java/W24StaticShapeFixture.java"
MIRROR_PATH = ROOT / "comsol_mcp/data/science_recipes/java/W24StaticShapeFixture.java"
ORIGINS_PATH = ROOT / "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json"
PROVENANCE_PATH = ROOT / "tests/fixtures/w24_static_shape_phasefield_api_provenance.json"


def _java_method_body(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    state = "code"
    escaped = False
    i = opening
    while i < len(source):
        char = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                state = "code"
                i += 1
        elif state == "string":
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                state = "code"
        elif state == "char":
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "'":
                state = "code"
        else:
            if char == "/" and nxt == "/":
                state = "line_comment"
                i += 1
            elif char == "/" and nxt == "*":
                state = "block_comment"
                i += 1
            elif char == '"':
                state = "string"
            elif char == "'":
                state = "char"
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[opening + 1:i]
        i += 1
    raise AssertionError("unterminated Java method body: " + marker)


class W24StaticShapePhaseFieldSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = JAVA_PATH.read_text(encoding="utf-8")

    def test_phase_field_is_created_before_material_binding_and_coupling(self):
        build = _java_method_body(self.java, "private static Map<String, Object> build(")
        create = build.index("Physics phaseField = createPhaseFieldPhysics(model, allDomainIds, trace);")
        materials = build.index("addMaterials(model, allDomainIds, phaseField, trace);")
        physics = build.index("addPhysics(model, phaseField, caseId,")
        self.assertLess(create, materials)
        self.assertLess(materials, physics)

        create_body = _java_method_body(self.java, "private static Physics createPhaseFieldPhysics(")
        self.assertIn('.create("pf", "PhaseField", GEOMETRY)', create_body)
        self.assertIn('"PhaseField".equals(phaseField.getType())', create_body)
        self.assertIn("sameIds(expectedDomainIds, phaseField.selection().entities(2))", create_body)

        materials_body = _java_method_body(self.java, "private static Map<String, Object> addMaterials(")
        material_guard = materials_body.index("if (phaseField == null")
        bind = materials_body.index('multiphase.set("vfDefinition", phaseFieldTag)')
        readback = materials_body.index('multiphase.getString("vfDefinition")')
        self.assertLess(material_guard, bind)
        self.assertLess(bind, readback)
        physics_body = _java_method_body(self.java, "private static Map<String, Object> addPhysics(")
        self.assertNotIn('.create("pf", "PhaseField"', physics_body)
        self.assertIn('"pf".equals(phaseField.tag())', physics_body)

    def test_initial_value_nodes_are_selected_by_live_fluid_semantics(self):
        resolver = _java_method_body(self.java, "static Map<String, PhysicsFeature> resolvePhaseInitialValueFeatures(")
        for token in ("phaseField.feature().tags()", 'phaseField.feature(tag)', 'feature.hasProperty("FluidInDomain")',
                      'feature.hasProperty("InitialValuesOption")', 'feature.getString("FluidInDomain")',
                      "feature.getType()", '"Fluid1phipf"', '"Fluid2phipf"',
                      '"init".equals(featureType)', '"initFluid2".equals(featureType)',
                      "requireExactStringEnumProperty(feature, \"InitialValuesOption\"",
                      "requireExactStringEnumProperty(feature, \"FluidInDomain\"",
                      "byFluid.put(fluid, feature)", "byFluid.size() != 2"):
            self.assertIn(token, resolver)
        self.assertNotIn('feature("init1")', self.java)
        self.assertNotIn('feature("initfluid2")', self.java)
        physics = _java_method_body(self.java, "private static Map<String, Object> addPhysics(")
        self.assertIn("resolvePhaseInitialValueFeatures(pf)", physics)
        for token in ('phaseInitialValues.get("Fluid1phipf")', 'phaseInitialValues.get("Fluid2phipf")',
                      'initGas.selection().named("sel_gas_domain")',
                      'initGlue.getString("InitialValuesOption")',
                      'initGas.getString("InitialValuesOption")',
                      'initGlue.getString("FluidInDomain")',
                      'initGas.getString("FluidInDomain")'):
            self.assertIn(token, physics)
        self.assertNotIn('initGlue.selection().named(', physics)
        self.assertNotIn('initGlue.selection().set(', physics)
        self.assertNotIn('initGlue.selection().all(', physics)
        self.assertIn('readback.put("phase1_initial_feature_tag", initGlue.tag())', physics)
        self.assertIn('readback.put("phase2_initial_feature_type", initGas.getType())', physics)
        coverage = _java_method_body(self.java, "private static Map<String, Object> verifyInitialDomainCoverage(")
        for token in ("defaultInit.selection().inputEntities()", "defaultInit.selection().entities(2)",
                      'fluid2Init.selection().named()', 'fluid2Init.selection().inputEntities()',
                      '"sel_gas_domain"', "DOCUMENTED_DEFAULT_REMAINDER_DERIVED_FROM_ALL_MINUS_FLUID2_OVERRIDE",
                      "phase1_initial_effective_domain_is_direct_readback",
                      "NOT_EXPOSED_BY_READ_ONLY_API_METADATA"):
            self.assertIn(token, coverage)

    def test_wetted_wall_builder_preserves_default_and_uses_exact_owned_custom_nodes(self):
        physics = _java_method_body(self.java, "private static Map<String, Object> addPhysics(")
        self.assertIn('Arrays.equals(existingWettedWallTags, new String[]{"ww1"})', physics)
        self.assertIn('pf.feature("ww1")', physics)
        self.assertIn('"WettedWall".equals(defaultWettedWall.getType())', physics)
        self.assertIn('String tag = "wwshape" + (index + 1)', physics)
        self.assertIn('pf.create(tag, "WettedWall", 1)', physics)
        self.assertIn('if (contains(pf.feature().tags(), tag))', physics)
        self.assertIn('wetted.selection().named(entry.getKey())', physics)
        self.assertIn('assignedWettingBoundaryIds.add(boundaryId)', physics)
        self.assertIn('"default_wetted_wall_feature_metadata"', physics)
        self.assertIn('"selection_status", "DOCUMENTED_DEFAULT_NOT_EDITABLE"', physics)
        self.assertNotIn('defaultWettedWall.selection()', physics)
        self.assertNotIn('existingWettedWallTags[index]', physics)
        self.assertNotIn('catch (Exception', physics)

        readback = (ROOT / "tools/java/W24StaticShapeReadback.java").read_text(encoding="utf-8")
        walls = _java_method_body(readback, "private static Map<String, Object> readback(")
        self.assertIn('phase.feature("ww1")', walls)
        self.assertIn('Set<String> expectedWallTags', walls)
        self.assertIn('expectedWallTags.add("wwshape" + index)', walls)
        self.assertIn('"wwshape" + (wallIndex + 1)', walls)
        self.assertIn('customBoundaryIds.add(boundaryId)', walls)
        self.assertIn('"default_wetted_wall_feature_metadata"', walls)
        self.assertIn('"selection_status", "DOCUMENTED_DEFAULT_NOT_EDITABLE"', walls)
        self.assertIn('result.put("wetted_wall_features", wallFeatures)', walls)

    def test_readback_source_preserves_default_and_proves_explicit_fluid2_remainder(self):
        source = (ROOT / "tools/java/W24StaticShapeReadback.java").read_text(encoding="utf-8")
        resolver = _java_method_body(source, "private static Map<String, PhysicsFeature> resolvePhaseInitialValueFeatures(")
        self.assertIn('"initFluid2".equals(featureType)', resolver)
        self.assertIn('requireExactStringEnumProperty(feature, "InitialValuesOption"', resolver)
        coverage = _java_method_body(source, "private static Map<String, Object> verifyInitialDomainCoverage(")
        self.assertIn("defaultInit.selection().inputEntities()", coverage)
        self.assertIn('fluid2Init.selection().named()', coverage)
        self.assertIn("COMSOL_6_4_DEFAULT_SELECTION_ALL_ENTITIES_NOT_OVERRIDDEN_BY_SAME_LEVEL_NODE", coverage)
        self.assertIn("result.putAll(initialDomainReadback)", source)

    def test_physics_properties_use_typed_metadata_and_semantic_feature_resolution(self):
        self.assertIn("import com.comsol.model.ParameterEntity;", self.java)
        self.assertIn("static void requirePropertyValueType(ParameterEntity feature", self.java)
        resolver = _java_method_body(self.java, "static PhysicsFeature resolvePhaseFieldModel(")
        for token in ("phaseField.feature().tags()", "feature.getType()",
                      '"PhaseFieldModel".equals(feature.getType())',
                      "resolved != null", "resolved == null"):
            self.assertIn(token, resolver)
        self.assertNotIn('feature("pfm1")', self.java)

        metadata = _java_method_body(self.java, "static void requireStringEnumProperty(")
        self.assertIn("requirePropertyValueType(feature, name, \"String\")", metadata)
        self.assertIn("feature.getAllowedPropertyValues(name)", metadata)
        self.assertIn("allowedValues == null", metadata)

        physics = _java_method_body(self.java, "private static Map<String, Object> addPhysics(")
        for token in ('spf.prop("PhysicalModelProperty")',
                      'requireStringEnumProperty(physicalModel, "IncludeGravity", "0")',
                      'physicalModel.set("IncludeGravity", "0")',
                      'physicalModel.getString("IncludeGravity")',
                      '"1".equals(gravitySwitch)',
                      'requirePropertyValueType(phaseFieldModel, "epsilon_pf", "String")',
                      'requireStringEnumProperty(physicalModel, "Compressibility", "Incompressible")',
                      'physicalModel.set("Compressibility", "Incompressible")',
                      '"Incompressible".equals(compressibility)',
                      'phaseFieldModel.set("chiOption", "userdef")',
                      'phaseFieldModel.set("chi", "chiPF")',
                      'requirePropertyValueType(phaseFieldModel, "chi", "String")',
                      'phaseFieldModel.getString("chi")',
                      'requireStringEnumProperty(tpf, "IncludeSurfaceTension", "1")',
                      'requirePropertyValueType(tpf, "sigma", "String")',
                      'tpf.set("IncludeSurfaceTension", "1")',
                      'tpf.getString("IncludeSurfaceTension")',
                      '"1".equals(surfaceTensionSwitch)'):
            self.assertIn(token, physics)
        for guard, setter in (
                ('requireStringEnumProperty(physicalModel, "IncludeGravity", "0")',
                 'physicalModel.set("IncludeGravity", "0")'),
                ('requireStringEnumProperty(physicalModel, "Compressibility", "Incompressible")',
                 'physicalModel.set("Compressibility", "Incompressible")'),
                ('requireStringEnumProperty(phaseFieldModel, "chiOption", "userdef")',
                 'phaseFieldModel.set("chiOption", "userdef")'),
                ('requirePropertyValueType(phaseFieldModel, "chi", "String")',
                 'phaseFieldModel.set("chi", "chiPF")'),
                ('requirePropertyValueType(phaseFieldModel, "epsilon_pf", "String")',
                 'phaseFieldModel.set("epsilon_pf", "epsPF")'),
                ('requireStringEnumProperty(tpf, "IncludeSurfaceTension", "1")',
                 'tpf.set("IncludeSurfaceTension", "1")'),
                ('requireStringEnumProperty(tpf, "SurfaceTensionCoefficient", "userdef")',
                 'tpf.set("SurfaceTensionCoefficient", "userdef")'),
                ('requirePropertyValueType(tpf, "sigma", "String")',
                 'tpf.set("sigma", "sigma0")')):
            self.assertLess(physics.index(guard), physics.index(setter))
        self.assertLess(physics.index('phaseFieldModel.set("chiOption", "userdef")'),
                        physics.index('phaseFieldModel.set("chi", "chiPF")'))

    def test_independent_readback_reuses_semantic_resolvers_and_exact_typed_values(self):
        readback = (ROOT / "tools/java/W24StaticShapeReadback.java").read_text(encoding="utf-8")
        self.assertNotIn("W24StaticShapeFixture", readback)
        self.assertIn("private static PhysicsFeature resolvePhaseFieldModel(Physics phaseField)", readback)
        self.assertIn("private static Map<String, PhysicsFeature> resolvePhaseInitialValueFeatures(Physics phaseField)", readback)
        self.assertIn("private static void requirePropertyValueType(ParameterEntity feature", readback)
        self.assertIn("private static void requireStringEnumProperty(ParameterEntity feature", readback)
        self.assertIn("resolvePhaseFieldModel(phase)", readback)
        self.assertIn("resolvePhaseInitialValueFeatures(phase)", readback)
        self.assertNotIn('phase.feature("pfm1")', readback)
        self.assertNotIn('phase.feature("init1")', readback)
        self.assertNotIn('phase.feature("initfluid2")', readback)
        for token in ('getString("IncludeGravity")', 'getString("IncludeSurfaceTension")',
                      '"Incompressible".equals(compressibility)',
                      '"userdef".equals(chiOption)', '"chiPF".equals(chiExpression)',
                      '"SpecifyPhase".equals(initGlue.getString("InitialValuesOption"))',
                      '"SpecifyPhase".equals(initGas.getString("InitialValuesOption"))'):
            self.assertIn(token, readback)
        self.assertNotIn('getBoolean("IncludeGravity")', readback)
        self.assertNotIn('getBoolean("IncludeSurfaceTension")', readback)
        self.assertIn('requirePropertyValueType(phaseModel, "epsilon_pf", "String")', readback)
        self.assertIn('requirePropertyValueType(coupling, "sigma", "String")', readback)
        for token in ('requireStringEnumProperty(physicalModel, "IncludeGravity", "0")',
                      'requireStringEnumProperty(physicalModel, "Compressibility", "Incompressible")',
                      'requireStringEnumProperty(phaseModel, "chiOption", "userdef")',
                      'requirePropertyValueType(phaseModel, "chi", "String")',
                      'requirePropertyValueType(phaseModel, "epsilon_pf", "String")',
                      'requireStringEnumProperty(coupling, "IncludeSurfaceTension", "1")',
                      'requireStringEnumProperty(coupling, "SurfaceTensionCoefficient", "userdef")',
                      'requirePropertyValueType(coupling, "sigma", "String")'):
            self.assertIn(token, readback)

    def test_schema_only_action_reads_unset_defaults_without_solving_or_mutating_properties(self):
        run = _java_method_body(self.java, "public static Object run(Model parent, Map<String, Object> args)")
        self.assertIn('"property_schema_only".equals(action)', run)
        self.assertIn('readFreshPropertySchemaDefaults(parent, args)', run)

        probe = _java_method_body(self.java, "private static Map<String, Object> readFreshPropertySchemaDefaults(")
        empty_guard = probe.index('parent.study().tags().length != 0')
        create_component = probe.index('parent.component().create(COMPONENT)')
        self.assertLess(empty_guard, create_component)
        for token in ('readDeclaredPropertyDefault(physicalModel, "IncludeGravity")',
                      'readDeclaredPropertyDefault(physicalModel, "Compressibility")',
                      'readDeclaredPropertyDefault(phaseFieldModel, "chiOption")',
                      'readDeclaredPropertyDefault(phaseFieldModel, "chi")',
                      'readDeclaredPropertyDefault(phaseFieldModel, "epsilon_pf")',
                      'readDeclaredPropertyDefault(coupling, "IncludeSurfaceTension")',
                      'readDeclaredPropertyDefault(coupling, "SurfaceTensionCoefficient")',
                      'readDeclaredPropertyDefault(coupling, "sigma")',
                      '"physics_property_set_calls", 0', '"study_run_calls", 0', '"solver_calls", 0',
                      '"model_save_calls", 0', '"result_evaluation_calls", 0',
                      '"length_unit_setter_calls"'):
            self.assertIn(token, probe)
        self.assertNotRegex(probe, r"\.set\s*\(")
        self.assertNotRegex(probe, r"\.run\s*\(")
        self.assertIn('geom.lengthUnit("m")', probe)
        self.assertIn('geom.axisymmetric(true)', probe)
        self.assertNotIn('.study().create(', probe)
        self.assertNotIn('.sol().create(', probe)
        self.assertNotIn('model.save(', probe)

        reader = _java_method_body(self.java, "private static Map<String, Object> readDeclaredPropertyDefault(")
        for token in ("entity.hasProperty(name)", "entity.getValueType(name)",
                      "entity.getAllowedPropertyValues(name)", 'entity.getBoolean(name)',
                      'entity.getString(name)', '"UNSUPPORTED_DECLARED_TYPE"'):
            self.assertIn(token, reader)
        self.assertNotIn('entity.getDouble(name)', reader)
        self.assertNotIn("Boolean.equals(", reader)
        self.assertNotIn("chi is declared Double", self.java)

    def test_packaged_java_mirror_and_origin_hash_are_exact(self):
        java_bytes = JAVA_PATH.read_bytes()
        self.assertEqual(MIRROR_PATH.read_bytes(), java_bytes)
        origins = json.loads(ORIGINS_PATH.read_text(encoding="utf-8"))
        matches = [row for row in origins["files"]
                   if row.get("origin") == "tools/java/W24StaticShapeFixture.java"
                   and row.get("resource") == "comsol_mcp/data/science_recipes/java/W24StaticShapeFixture.java"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["sha256"], hashlib.sha256(java_bytes).hexdigest())

    def test_api_provenance_separates_documentation_from_runtime_defaults(self):
        evidence = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(evidence["comsol_version"], "6.4.0.293")
        docs = {(row["doc_id"], row["chunk_id"]): row for row in evidence["official_local_documentation"]}
        expected = {
            (5838, 19242): "b2069f4780ca75b13dd1f35cb9faee9a0e1eef9371f74cdead3ce8d0a4875f9e",
            (4014, 16577): "444f4671d15af6ddfefb58cb7dae1e93d7a51f1a872ceef2cf40ed8bd8f33f0d",
            (4082, 16692): "e83f428fe1baed130a0a275f814660110cfc1fc5149418500ff3e7641ca78a2b",
            (7759, 23154): "54d7fd17c368fc504f66502692d4da8a0b83edb6f7ba0f58e8f7d6c83aa3dcff",
            (7644, 22963): "463ef0c4e8b2b7315e8950bd576f4b783718c75308307cef0a4d584d86b835ab",
            (7758, 23153): "9eeb763b12eb4d5f6eaff7561381bb200aab2150165063410a4e9fa3592b5e7c",
            (7668, 23005): "d48a6bc07e9862244a7fd496246477200d065a661ae69605de4ef3ae7e678539",
            (7675, 23018): "befb8cc1c0e3df1ce74d2fa2cd1377e73d378e5c26170558e9f40dc255cc3bc3",
        }
        self.assertEqual({key: docs[key]["sha256"] for key in expected}, expected)
        self.assertIn("cannot be changed", docs[(5838, 19242)]["supports"])
        self.assertIn("inputEntities()", docs[(4082, 16692)]["supports"])
        runtime = evidence["candidate_runtime_contract"]
        self.assertIn("DEFAULT_CLASSIFICATION_NOT_EXPOSED", runtime["fresh_build_6_4_0_293_defaults"])
        self.assertEqual(runtime["phase_initial_values"]["exact_feature_types"],
                         {"Fluid1phipf": "init", "Fluid2phipf": "initFluid2"})
        self.assertEqual(runtime["phase_initial_values"]["default_fluid1_selection"]["setter"],
                         "FORBIDDEN_DEFAULT_NODE_SELECTION_IS_UNCHANGEABLE")
        self.assertEqual(runtime["phase_initial_values"]["fluid2_selection"]["named_selection"],
                         "sel_gas_domain")
        self.assertEqual(runtime["typed_property_gates"]["flow_property_group"], "PhysicalModelProperty")
        self.assertEqual(runtime["typed_property_gates"]["gravity"]["serialized_value"], False)
        self.assertEqual(runtime["typed_property_gates"]["surface_tension_enabled"]["serialized_value"], True)
        self.assertEqual(runtime["typed_property_gates"]["compressibility"]["allowed_value"], "Incompressible")
        self.assertEqual(runtime["typed_property_gates"]["chi_option"]["allowed_value"], "userdef")
        self.assertEqual(runtime["typed_property_gates"]["chi_expression"]["expression"], "chiPF")
        self.assertFalse(runtime["typed_property_gates"]["chi_expression"]["numeric_substitution_allowed"])
        self.assertFalse(runtime["native_model_execution"])
        self.assertFalse(runtime["study_or_solver_execution"])
        self.assertEqual(runtime["science_acceptance"], "NOT_RUN")
        historical = evidence["historical_saved_model_observation"]
        self.assertEqual(historical["saved_model_version"], "COMSOL 6.4.0.257")
        self.assertEqual(historical["observed_legacy_tags"], ["init1", "initfluid2"])
        self.assertIn("not used as the runtime lookup contract", historical["scope"])


if __name__ == "__main__":
    unittest.main()
