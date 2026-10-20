from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tools/java/W24StaticShapeFixture.java"
PACKAGE_FIXTURE = ROOT / "comsol_mcp/data/science_recipes/java/W24StaticShapeFixture.java"
RUNNER = ROOT / "tools/run_native_w24_static_shape_setup.py"


class StaticShapeSelectionDiagnosticTests(unittest.TestCase):
    def test_fixture_trace_is_private_fresh_append_only_and_bounded(self):
        source = FIXTURE.read_text()
        self.assertIn("MAX_SELECTION_TRACE_EVENTS = 1024", source)
        self.assertIn('w24_selection_stage_[A-Za-z0-9_-]{1,96}[.]jsonl', source)
        self.assertIn("if (suppliedPath == null) return new SelectionTrace(null);", source)
        self.assertIn("if (channel == null) return;", source)
        self.assertIn("Files.exists(path, LinkOption.NOFOLLOW_LINKS)", source)
        self.assertIn("Files.isSymbolicLink(parent)", source)
        self.assertIn("Files.getPosixFilePermissions(parent, LinkOption.NOFOLLOW_LINKS)", source)
        self.assertIn("PosixFilePermission.GROUP_READ", source)
        self.assertIn("PosixFilePermission.OTHERS_WRITE", source)
        self.assertIn("StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE", source)
        self.assertNotIn("StandardOpenOption.TRUNCATE_EXISTING", source)
        self.assertEqual(source.count("channel.force(false)"), 1)
        self.assertIn('!stage.matches("[A-Za-z0-9_.-]{1,96}")', source)
        self.assertEqual(FIXTURE.read_bytes(), PACKAGE_FIXTURE.read_bytes())

    def test_selection_mutations_have_before_after_stage_breadcrumbs(self):
        source = FIXTURE.read_text()
        required_pairs = [
            ('phaseField.selection.geom', 'phaseField.selection().geom(GEOMETRY, 2);'),
            ('phaseField.selection.all', 'phaseField.selection().all();'),
            ('multiphase.selection.geom', 'multiphase.selection().geom(GEOMETRY, 2);'),
            ('multiphase.selection.all', 'multiphase.selection().all();'),
            ('laminarFlow.selection.geom', 'spf.selection().geom(GEOMETRY, 2);'),
            ('laminarFlow.selection.all', 'spf.selection().all();'),
            ('initialValues.gas.selection.named', 'initGas.selection().named("sel_gas_domain");'),
            ('wettedWall.', 'wetted.selection().named(entry.getKey());'),
            ('pressureReference.selection.named', 'reference.selection().named(selectionTag);'),
            ('integration.selection.geom', 'model.component(COMPONENT).cpl("ivol").selection().geom(GEOMETRY, 2);'),
            ('integration.selection.all', 'model.component(COMPONENT).cpl("ivol").selection().all();'),
            ('maximum.selection.geom', 'model.component(COMPONENT).cpl("maxspeed").selection().geom(GEOMETRY, 2);'),
            ('maximum.selection.all', 'model.component(COMPONENT).cpl("maxspeed").selection().all();'),
        ]
        for stage, call in required_pairs:
            call_at = source.index(call)
            if stage.startswith(("integration.", "maximum.")):
                wrapper = f'trace.call("{stage}", () -> {{'
                before_at = source.rfind(wrapper, 0, call_at)
                after_at = source.find('return null; });', call_at + len(call))
                self.assertGreaterEqual(before_at, 0, (stage, call))
                self.assertGreater(after_at, call_at, (stage, call))
            else:
                before_at = source.rfind('"before"', 0, call_at)
                after_at = source.find('"after"', call_at + len(call))
                self.assertGreaterEqual(before_at, 0, (stage, call))
                self.assertGreater(after_at, call_at, (stage, call))
                if stage != 'wettedWall.':
                    self.assertIn(stage, source[max(0, before_at - 100):call_at])
                    self.assertIn(stage, source[call_at:after_at + 100])

        runner = RUNNER.read_text()
        self.assertIn('f"w24_selection_stage_{configuration_id}_{case_id}_{uuid4().hex}.jsonl"', runner)
        self.assertIn('"selection_diagnostic_path": str(selection_trace_path)', runner)

    def test_remaining_setup_calls_are_exactly_traced_and_time_solver_properties_stay_in_their_domain(self):
        source = FIXTURE.read_text()
        for stage in (
            "metric.ivol.create", "metric.maxspeed.create", "metrics.variable.shapeMetrics.create",
            "metrics.shapeMetrics.set.phase1_volume", "metrics.shapeMetrics.get.phase1_volume",
            "metrics.shapeMetrics.set.phasefield_bulk_free_energy",
            "metrics.shapeMetrics.get.phasefield_bulk_free_energy",
            "mesh.mesh1.create", "mesh.mesh1.size.set.custom", "mesh.mesh1.size.set.hmax",
            "mesh.mesh1.size.set.hmin", "mesh.mesh1.run", "mesh.mesh1.size.get.hmax",
            "mesh.mesh1.size.get.hmin", "mesh.mesh1.size.get.custom",
            "study.stdShape.create", "study.stdShape.phasei.create", "study.stdShape.time.create",
            "study.stdShape.steps.get.tags", "study.stdShape.time.set.usertol",
            "study.stdShape.time.set.rtol", "study.stdShape.time.set.tlist",
            "study.stdShape.time.get.usertol", "study.stdShape.time.get.rtol",
            "study.stdShape.time.get.tlist", "study.stdShape.createAutoSequences.sol", "solver."):
            self.assertIn(f'trace.call("{stage}', source)
        self.assertIn('".getSolverSequences." + queryType', source)
        self.assertIn('"All", "SolverSequence", "CopySolution", "ParametricStore"', source)
        self.assertIn('inspectGeneratedStudySolverChain(model, study)', source)
        self.assertIn('captureProbeProperties(feature, new String[]{"sol"})', source)
        self.assertIn(r'\"phase\":\"error\"', source)
        self.assertIn(r'\"exception_class\"', source)
        self.assertIn(r'\"exception_message\"', source)
        self.assertIn("catch (RuntimeException | Error failure)", source)
        self.assertIn("jsonQuote(failure.getClass().getName())", source)
        self.assertNotIn('initGlue.selection().named(', source)
        self.assertIn('defaultInit.selection().inputEntities()', source)
        self.assertIn('defaultInit.selection().entities(2)', source)

        study = source[source.index("private static Map<String, Object> addUnsolvedStudy("):
                       source.index("private static Configuration configuration(")]
        for forbidden in ('time.set("tout"', 'time.set("tstepsbdf"', 'time.set("tstepsstore"'):
            self.assertNotIn(forbidden, study)
        solver = source[source.index("private static void configureTimeSolver("):
                        source.index("private static SolverFeature findUniqueTimeFeature(")]
        for property_name in ("tout", "tstepsbdf", "tstepsstore"):
            self.assertIn(f'time.set("{property_name}"', solver)
            self.assertIn(f'.get.{property_name}', source)
        for property_name in ("usertol", "rtol", "tlist"):
            self.assertIn(f'time.set("{property_name}"', study)
            self.assertIn(f'time.getString("{property_name}"', study)
        for property_name in ("initstudy", "useinitsol"):
            self.assertNotIn(f'time.set("{property_name}"', study)
        self.assertIn('new String[]{"initmethod", "initstudy", "useinitsol"}', source)
        readback = (ROOT / "tools/java/W24StaticShapeReadback.java").read_text()
        for field in ("study_step_order", "time_initialization_method", "time_initialization_study", "use_initial_solution",
                      "use_user_tolerance", "relative_tolerance", "time_solver_output_owner",
                      "time_solver_feature_tag", "generated_study_solver_chain"):
            self.assertIn(f'result.put("{field}"', readback)
        validator = RUNNER.read_text()
        for field in ("study_step_order", "time_initialization_method", "time_initialization_study", "use_initial_solution",
                      "use_user_tolerance", "relative_tolerance", "time_solver_output_owner",
                      "time_solver_feature_tag", "generated_study_solver_chain"):
            self.assertIn(f'result.get("{field}")', validator)


if __name__ == "__main__":
    unittest.main()
