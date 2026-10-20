"""Bounded software controls for the W24 one-call schema-only campaign mode."""
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import run_native_w24_static_shape_setup_campaign as campaign


class _Monitor:
    stop_reason = None

    def sample_once(self):
        return None


class _Daemon:
    store = object()
    session_registry = object()




class _SchemaResponseDaemon(_Daemon):
    def __init__(self, result):
        self.store = object()
        self.session_registry = object()
        self.calls = []
        self.result = result

    def dispatch(self, request):
        self.calls.append(request)
        return {"success": True,
                "data": {"worker": {"status": "SUCCEEDED"},
                         "readback": {"readback": self.result}}}


class W24SchemaOnlyModeTests(unittest.TestCase):
    def test_budget_is_one_fresh_model_one_java_call_zero_science(self):
        budget, routes, slots = campaign._execution_contract(campaign.EXECUTION_MODE_SCHEMA_ONLY)
        self.assertEqual(slots, [])
        self.assertEqual(budget["planned_setup_receipts"], 0)
        self.assertEqual(budget["model_create_calls"], 1)
        self.assertEqual(budget["trusted_java_calls"], 1)
        self.assertEqual(budget["schema_probe_calls"], 1)
        self.assertEqual(budget["schema_probe_properties"], 8)
        self.assertEqual(budget["geometry_configuration_calls"], 2)
        self.assertEqual(budget["geometry_build_calls"], 0)
        self.assertEqual(budget["max_task_owned_comsol_servers"], 1)
        self.assertEqual(budget["max_managed_workers"], 1)
        self.assertEqual(budget["max_session_start_calls"], 1)
        self.assertEqual(budget["max_session_connect_calls"], 1)
        self.assertEqual(budget["max_session_stop_calls"], 1)
        self.assertEqual(budget["physics_property_set_calls"], 0)
        self.assertEqual(budget["study_run_submissions"], 0)
        self.assertEqual(budget["solver_calls"], 0)
        self.assertEqual(budget["max_single_unsolved_mph_bytes"], 0)
        self.assertEqual(budget["max_total_project_output_bytes"], 0)
        self.assertEqual(budget["max_gui_processes"], 0)
        self.assertEqual(budget["wall_seconds_from_server_birth_including_cleanup"], 1200)
        self.assertTrue(any("one model_create seed" in route for route in routes))
        self.assertTrue(any("exactly one trusted W24StaticShapeFixture" in route for route in routes))
        setup_budget, _, setup_slots = campaign._execution_contract(campaign.EXECUTION_MODE_SETUP14)
        self.assertEqual(setup_budget, campaign.SETUP_BUDGET)
        self.assertEqual(len(setup_slots), 14)

    def test_schema_route_guard_accepts_exact_call_once_and_rejects_widening(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            events = Path(temp) / "events.jsonl"
            events.write_bytes(b"")
            gate = campaign.BudgetedSetupDaemon(
                _Daemon(), monitor=_Monitor(), deadline_monotonic=campaign.time.monotonic() + 60,
                events_path=events, workspace=workspace, project_id="project-1",
                schema_probe_only=True)
            exact = {"operation_id": "code.execute_java", "arguments": {
                "source_artifact": "W24StaticShapeFixture.java",
                "entrypoint": "W24StaticShapeFixture#run",
                "arguments": {"action": "property_schema_only"},
                "mode": "trusted"}}
            gate._validate_operation("operation_call", exact)
            gate._validate_operation("model_create", {"name": "w24_static_shape_setup_parent"})
            for mutated in (
                {**exact, "arguments": {**exact["arguments"], "mode": "execute"}},
                {**exact, "arguments": {**exact["arguments"], "source_artifact": "W24StaticShapeReadback.java"}},
                {**exact, "arguments": {**exact["arguments"], "arguments": {"action": "build", "case_id": "flat", "configuration_id": "baseline"}}},
                {**exact, "arguments": {**exact["arguments"], "extra": True}},
            ):
                with self.assertRaises(campaign.CandidateError):
                    gate._validate_operation("operation_call", mutated)
            gate.schema_probe_completed = True
            with self.assertRaises(campaign.CandidateError):
                gate._validate_operation("operation_call", exact)

            setup_gate = campaign.BudgetedSetupDaemon(
                _Daemon(), monitor=_Monitor(), deadline_monotonic=campaign.time.monotonic() + 60,
                events_path=events, workspace=workspace, project_id="project-1")
            with self.assertRaises(campaign.CandidateError):
                setup_gate._validate_operation("operation_call", exact)

    def test_gate_dispatch_records_the_single_terminal_schema_result(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            events = Path(temp) / "events.jsonl"
            events.write_bytes(b"")
            expected = {"status": "SCHEMA_ONLY_DEFAULTS_CAPTURED_NOT_SOLVED",
                        "action": "property_schema_only", "model_tag": "model-1"}
            daemon = _SchemaResponseDaemon(expected)
            gate = campaign.BudgetedSetupDaemon(
                daemon, monitor=_Monitor(), deadline_monotonic=campaign.time.monotonic() + 1200,
                events_path=events, workspace=workspace, project_id="project-1",
                schema_probe_only=True)
            request = {"operation": "operation_call", "arguments": {
                "operation_id": "code.execute_java", "arguments": {
                    "source_artifact": "W24StaticShapeFixture.java",
                    "entrypoint": "W24StaticShapeFixture#run",
                    "arguments": {"action": "property_schema_only"}, "mode": "trusted"}},
                "execution": {"project_id": "project-1", "session_id": "session-1",
                              "request_id": "request-1", "idempotency_key": "key-1"}}
            response = gate.dispatch(request)
            self.assertTrue(response["success"])
            self.assertEqual(gate.schema_probe_result, expected)
            self.assertTrue(gate.schema_probe_completed)
            self.assertEqual(len(daemon.calls), 1)
            with self.assertRaises(campaign.CandidateError):
                gate.dispatch(request)
            self.assertEqual(len(daemon.calls), 1)

    def test_cli_has_distinct_review_bound_schema_only_switches(self):
        prepare = campaign._parser().parse_args(
            ["prepare", "--evidence", "/private/tmp/evidence", "--schema-only"])
        self.assertTrue(prepare.schema_only)
        execute = campaign._parser().parse_args([
            "execute", "--evidence", "/private/tmp/evidence", "--work", "/private/tmp/work",
            "--reviewed-candidate-sha256", "a" * 64, "--schema-only"])
        self.assertTrue(execute.schema_only)

    def test_schema_result_accepts_typed_raw_values_and_geometry_scope(self):
        property_names = {
            "LaminarFlow.PhysicalModelProperty.IncludeGravity": ("IncludeGravity", "String", "0", ["1", "0"], "getString", "typed_scalar"),
            "LaminarFlow.PhysicalModelProperty.Compressibility": ("Compressibility", "String", "Incompressible", ["Incompressible", "WeaklyCompressible", "CompressibleMALT03"], "getString", "typed_scalar"),
            "PhaseField.PhaseFieldModel.chiOption": ("chiOption", "String", "userdef", ["velocity", "userdef"], "getString", "typed_scalar"),
            "PhaseField.PhaseFieldModel.chi": ("chi", "String", "1", None, "getString", "typed_scalar"),
            "PhaseField.PhaseFieldModel.epsilon_pf": ("epsilon_pf", "String", "pf.ep_default", None, "getString", "typed_scalar"),
            "TwoPhaseFlowPhaseField.IncludeSurfaceTension": ("IncludeSurfaceTension", "String", "1", ["1", "0"], "getString", "typed_scalar"),
            "TwoPhaseFlowPhaseField.SurfaceTensionCoefficient": ("SurfaceTensionCoefficient", "String", "LibraryCoefficientLiquidGas", ["LibraryCoefficientLiquidGas", "LibraryCoefficientLiquidLiquid", "LibraryCoefficientLiquidMixtureGas", "userdef"], "getString", "typed_scalar"),
            "TwoPhaseFlowPhaseField.sigma": ("sigma", "String", "7e-2[N/m]", None, "getString", "typed_scalar"),
        }
        properties = {}
        for key, (name, value_type, value, allowed, reader, representation) in property_names.items():
            row = {"property_name": name, "has_property": True, "declared_type": value_type,
                   "allowed_values": allowed, "value_status": "READ_WITH_DECLARED_TYPE_GETTER",
                   "value_reader": reader, "value": value,
                   "value_representation": representation,
                   "expression_value": value if representation == "expression_as_string" else None}
            properties[key] = row
        candidate = {
            "status": "SCHEMA_ONLY_DEFAULTS_CAPTURED_NOT_SOLVED", "action": "property_schema_only",
            "model_tag": "model-1", "physics_tags": ["spf", "pf"],
            "physics_types": ["LaminarFlow", "PhaseField"], "coupling_tag": "tpf1",
            "coupling_type": "TwoPhaseFlowPhaseField",
            "geometry_setup": {"dimension": 2, "length_unit": "m", "length_unit_setter_calls": 1,
                               "axisymmetric": True, "axisymmetric_setter_calls": 1,
                               "geometry_build_calls": 0},
            "properties": properties, "physics_property_set_calls": 0,
            "study_run_calls": 0, "solver_calls": 0, "study_tags": [], "solver_tags": [],
            "model_save_calls": 0, "result_evaluation_calls": 0, "native_acceptance": "NOT_RUN",
        }
        result = campaign._validate_schema_probe_result(candidate, expected_model_tag="model-1")
        self.assertEqual(result["status"], "SCHEMA_ONLY_RESULT_VALIDATED_NOT_SOLVED")
        self.assertEqual(result["property_count"], 8)
        self.assertEqual(result["geometry_length_unit_setter_calls"], 1)
        self.assertEqual(result["geometry_axisymmetric_setter_calls"], 1)
        self.assertEqual(result["physics_property_set_calls"], 0)
        self.assertEqual(result["study_run_submissions"], 0)
        self.assertEqual(result["solver_calls"], 0)

        # Unknown API value types remain metadata-only and uninterpreted.
        unknown_type = deepcopy(candidate)
        unknown_row = unknown_type["properties"]["PhaseField.PhaseFieldModel.epsilon_pf"]
        unknown_row.update({"declared_type": "Integer", "value_status": "UNSUPPORTED_DECLARED_TYPE",
                            "value_reader": None, "value": None,
                            "value_representation": "unsupported_declared_type",
                            "expression_value": None})
        self.assertEqual(
            campaign._validate_schema_probe_result(unknown_type, expected_model_tag="model-1")["property_count"], 8)
        unknown_row["value"] = 4
        with self.assertRaises(campaign.CandidateError):
            campaign._validate_schema_probe_result(unknown_type, expected_model_tag="model-1")

        absent = deepcopy(candidate)
        absent_row = absent["properties"]["PhaseField.PhaseFieldModel.epsilon_pf"]
        absent_row.update({"has_property": False, "declared_type": None, "allowed_values": None,
                           "value_status": "PROPERTY_ABSENT", "value_reader": None, "value": None,
                           "value_representation": None, "expression_value": None})
        self.assertEqual(
            campaign._validate_schema_probe_result(absent, expected_model_tag="model-1")["property_count"], 8)
        absent_row["declared_type"] = "Integer"
        with self.assertRaises(campaign.CandidateError):
            campaign._validate_schema_probe_result(absent, expected_model_tag="model-1")

    def test_schema_result_rejects_missing_properties_and_science_artifacts(self):
        base = {"status": "SCHEMA_ONLY_DEFAULTS_CAPTURED_NOT_SOLVED",
                "action": "property_schema_only", "model_tag": "model-1",
                "physics_tags": ["spf", "pf"], "physics_types": ["LaminarFlow", "PhaseField"],
                "coupling_tag": "tpf1", "coupling_type": "TwoPhaseFlowPhaseField",
                "geometry_setup": {"dimension": 2, "length_unit": "m", "length_unit_setter_calls": 1,
                                    "axisymmetric": True, "axisymmetric_setter_calls": 1,
                                    "geometry_build_calls": 0},
                "properties": {}, "physics_property_set_calls": 0,
                "study_run_calls": 0, "solver_calls": 0, "study_tags": [], "solver_tags": [],
                "model_save_calls": 0, "result_evaluation_calls": 0, "native_acceptance": "NOT_RUN"}
        with self.assertRaises(campaign.CandidateError):
            campaign._validate_schema_probe_result(base, expected_model_tag="model-1")
        wrong_study = deepcopy(base)
        wrong_study["study_tags"] = ["stdShape"]
        with self.assertRaises(campaign.CandidateError):
            campaign._validate_schema_probe_result(wrong_study, expected_model_tag="model-1")
        wrong_model = deepcopy(base)
        wrong_model["model_tag"] = "foreign"
        with self.assertRaises(campaign.CandidateError):
            campaign._validate_schema_probe_result(wrong_model, expected_model_tag="model-1")

    def test_freeze_verifier_binds_schema_mode_and_preserves_legacy_setup_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"; repo.mkdir()
            dependency = {"status": "PREBIRTH_DEPENDENCY_PREFLIGHT_PASS"}
            source = {"checkout_kind": "git_archive_plus_explicit_overlay",
                      "source_closure_sha256": "c" * 64}

            def freeze_for(mode, include_mode=True):
                evidence = root / ("evidence-" + mode + ("-explicit" if include_mode else "-legacy"))
                evidence.mkdir()
                budget, routes, slots = campaign._execution_contract(mode)
                body = {"schema": campaign.FREEZE_SCHEMA,
                        "status": "PREPARED_SETUP_ONLY_NOT_NATIVE",
                        "source": source, "budget": budget, "routes": routes,
                        "ordered_setup_slots": slots,
                        "dependency_preflight": dependency}
                if include_mode:
                    body["execution_mode"] = mode
                freeze = {**body, "candidate_sha256": campaign._json_hash(body)}
                (evidence / campaign.FREEZE_FILE).write_bytes(campaign._json_bytes(freeze))
                return evidence, freeze["candidate_sha256"]

            with patch.object(campaign, "_source_inventory", return_value=source), \
                 patch.object(campaign, "preflight_campaign_dependencies", return_value=dependency):
                schema_evidence, schema_sha = freeze_for(campaign.EXECUTION_MODE_SCHEMA_ONLY)
                verified = campaign.verify_candidate(repo=repo, evidence=schema_evidence,
                    reviewed_sha256=schema_sha, schema_probe_only=True)
                self.assertEqual(verified["execution_mode"], campaign.EXECUTION_MODE_SCHEMA_ONLY)
                with self.assertRaises(campaign.CandidateError):
                    campaign.verify_candidate(repo=repo, evidence=schema_evidence,
                        reviewed_sha256=schema_sha, schema_probe_only=False)

                legacy_evidence, legacy_sha = freeze_for(campaign.EXECUTION_MODE_SETUP14, include_mode=False)
                verified_legacy = campaign.verify_candidate(repo=repo, evidence=legacy_evidence,
                    reviewed_sha256=legacy_sha, schema_probe_only=False)
                self.assertNotIn("execution_mode", verified_legacy)
                self.assertEqual(len(verified_legacy["ordered_setup_slots"]), 14)
                with self.assertRaises(campaign.CandidateError):
                    campaign.verify_candidate(repo=repo, evidence=legacy_evidence,
                        reviewed_sha256=legacy_sha, schema_probe_only=True)

    def test_exact_plan_binds_schema_only_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"; repo.mkdir()
            namespace = root / "activity"; namespace.mkdir()
            python = Path(__import__("sys").executable)
            python_site = root / "site"; python_site.mkdir()
            comsol = root / "comsol"; comsol.mkdir()
            jdk = root / "jdk"; jdk.mkdir()
            binding = {"namespace": str(namespace), "activity_id": "activity-1",
                       "binding_sha256": "a" * 64,
                       "python": {"entry": str(python), "site_packages": str(python_site)},
                       "comsol": {"root": str(comsol)}, "jdk": {"home": str(jdk)}}
            evidence = namespace / "candidate-evidence"
            evidence.mkdir()
            work = namespace / "work"
            with patch.object(campaign.fresh_runtime, "scoped_path", return_value=None):
                plan = campaign._build_exact_execute_plan_from_binding(
                    repo=repo, evidence=evidence, work=work,
                    reviewed_sha256="b" * 64, binding=binding, schema_probe_only=True)
                setup_plan = campaign._build_exact_execute_plan_from_binding(
                    repo=repo, evidence=evidence, work=work,
                    reviewed_sha256="b" * 64, binding=binding, schema_probe_only=False)
            self.assertIn("--schema-only", plan["argv"])
            self.assertNotIn("--schema-only", setup_plan["argv"])
            self.assertEqual(plan["native"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
