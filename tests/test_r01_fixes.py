"""Unit and regression tests for Round R01 remediation.

Covers:
- P01: Gateway preservation of nested arguments for registry_call / operation_call
- P02: Gateway preservation of arguments Map for code.execute_java
- P03: Gateway unpacking for W20 tools with bound None defaults
- Positive control: Conflicting parameters between outer kwargs and wrapped arguments
- P05: Removal of name-based isolation exemptions (gated strictly by effect)
- Model selection: Removal of ledger[0] fallback; ambiguous models raise AMBIGUOUS_TARGET
- Revision integrity: Rejection of expired revisions on evaluation/mutation
- ObservationRef: Model binding, dataset check, sha256 integrity, EXTERNAL_DATA_ONLY vs MODEL_VALIDATED
- Report publication: Atomic write via ArtifactStore, strict bool parsing, non-fallthrough of STATUS_UNKNOWN
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import pytest

from mcp.server.fastmcp import FastMCP
from comsol_mcp._execution_contract import ExecutionContractError, ModelRef, SessionLedger, model_ref_from_mapping
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._g3_w20_validation import (
    ObservationRef,
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_UNVERIFIED,
    STATUS_UNKNOWN,
    validate_solution,
    validate_report,
    validate_expressions,
    validate_conservation,
    ConvergenceStudy,
    ConvergenceStep,
)
from comsol_mcp._artifact_store import ArtifactStore


class TestGatewayArgumentNormalization:
    """Tests P01, P02, P03 and conflicting parameter validation."""

    def test_p01_operation_call_preserves_nested_arguments(self):
        """P01: operation_call retains nested arguments dict rather than flattening."""
        server = FastMCP("test-p01")
        captured = {}

        def dummy_backend(operation, arguments, execution):
            captured["operation"] = operation
            captured["arguments"] = arguments
            captured["execution"] = execution
            return {"success": True, "data": {"status": "ok"}}

        gw = GatewayRegistry(server, dummy_backend)

        def dummy_operation_call(operation_id: str, arguments: dict = None, **kwargs):
            return "unused"

        gw.add_tool(dummy_operation_call, name="operation_call")

        nested_args = {"expr": "T*2", "points": [1.0, 2.0]}
        tool = server._tool_manager._tools["operation_call"]
        import asyncio
        res = asyncio.run(tool.fn(operation_id="eval.point", arguments=nested_args))
        assert captured["operation"] == "operation_call"
        assert captured["arguments"]["arguments"] == nested_args
        assert isinstance(captured["arguments"]["arguments"], dict)

    def test_p02_code_execute_java_preserves_arguments_map(self):
        """P02: code.execute_java retains arguments Map rather than flattening."""
        server = FastMCP("test-p02")
        captured = {}

        def dummy_backend(operation, arguments, execution):
            captured["operation"] = operation
            captured["arguments"] = arguments
            captured["execution"] = execution
            return {"success": True, "data": {"status": "ok"}}

        gw = GatewayRegistry(server, dummy_backend)

        def dummy_execute_java(source_artifact: str = "", entrypoint: str = "", arguments: dict = None, **kwargs):
            return "unused"

        gw.add_tool(dummy_execute_java, name="code.execute_java")

        java_args = {"mesh_size": 0.05, "iterations": 100}
        tool = server._tool_manager._tools["code.execute_java"]
        import asyncio
        asyncio.run(tool.fn(source_artifact="model.java", entrypoint="Builder", arguments=java_args))
        assert captured["operation"] == "code.execute_java"
        assert captured["arguments"]["arguments"] == java_args
        assert isinstance(captured["arguments"]["arguments"], dict)

    def test_p03_validate_expressions_unpacks_with_bound_none_defaults(self):
        """P03: W20 validate tools correctly unpack arguments when default is None."""
        server = FastMCP("test-p03")
        captured = {}

        def dummy_backend(operation, arguments, execution):
            captured["operation"] = operation
            captured["arguments"] = arguments
            return {"success": True, "data": {"status": "ok"}}

        gw = GatewayRegistry(server, dummy_backend)

        def dummy_validate_expressions(expressions: list = None, arguments: dict = None):
            return "unused"

        gw.add_tool(dummy_validate_expressions, name="validate.expressions")

        tool = server._tool_manager._tools["validate.expressions"]
        import asyncio
        asyncio.run(tool.fn(arguments={"expressions": ["1+1", "2*3"]}))
        assert captured["operation"] == "validate.expressions"
        assert captured["arguments"]["expressions"] == ["1+1", "2*3"]

    def test_conflicting_wrapper_parameters_refused(self):
        """Positive control: Conflicting outer kwargs and inner arguments are rejected."""
        server = FastMCP("test-conflict")
        gw = GatewayRegistry(server, lambda op, args, exec: {"success": True, "data": {}})

        def dummy_validate_report(destination: str = None, overwrite: bool = False, arguments: dict = None):
            return "unused"

        gw.add_tool(dummy_validate_report, name="validate.report")
        tool = server._tool_manager._tools["validate.report"]
        import asyncio
        res = asyncio.run(tool.fn(
            destination="out1.json",
            arguments={"destination": "out2.json"}
        ))
        assert res.isError
        assert res.structuredContent["error"]["code"] == "INVALID_REQUEST"


class TestModelSelectionAndRevision:
    """Tests removal of ledger[0] fallback and strict revision enforcement."""

    def test_ambiguous_models_without_ref_raises_error(self, tmp_path):
        """M1 / F02: Multiple models loaded without explicit model_ref raises AMBIGUOUS_TARGET."""
        from comsol_mcp._managed_backend import ManagedBackend
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._execution_service import ExecutionService

        store = OperationStore(tmp_path / "ops.sqlite3")
        ledger = SessionLedger("session-1", "server-1")
        # Bind two models
        ref1 = ledger.bind_model("model1")
        ref2 = ledger.bind_model("model2")

        class MockAdapter:
            def snapshot(self, tag):
                return {"external_event_counter": 0, "fingerprint": "fp", "tag": tag}

        class MockWorker:
            def client(self):
                class MockClient:
                    def models(self):
                        return [type("M", (), {"tag": lambda: "model1"})(), type("M", (), {"tag": lambda: "model2"})()]
                return MockClient()

        import comsol_mcp._server as srv
        old_cur = getattr(srv, "_current_model", None)
        try:
            srv._current_model = None
            service = ExecutionService(ledger, MockAdapter(), project_root=tmp_path)
            backend = ManagedBackend(tmp_path, store, service=service, worker=MockWorker(), registry={})

            with pytest.raises(ExecutionContractError) as exc:
                # Invoking without model_ref when multiple models exist must raise AMBIGUOUS_TARGET
                backend._invoke_g2_model("node.inspect", {"path": {"segments": []}}, {}, "op-1", lambda e: None)
            assert exc.value.code == "AMBIGUOUS_TARGET"
        finally:
            srv._current_model = old_cur

    def test_current_model_selected_when_multiple_models_exist(self, tmp_path):
        """R02: cur_tag is checked first and resolves unambiguously even if multiple models exist."""
        from comsol_mcp._managed_backend import ManagedBackend
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._execution_service import ExecutionService
        import comsol_mcp._server as srv

        store = OperationStore(tmp_path / "ops.sqlite3")
        ledger = SessionLedger("session-1", "server-1")
        ref1 = ledger.bind_model("model1")
        ref2 = ledger.bind_model("model2")

        class MockAdapter:
            def model_snapshot(self, tag):
                return {"external_event_counter": 0, "fingerprint": "fp", "tag": tag}

        class MockWorker:
            def client(self):
                class MockClient:
                    def models(self):
                        return [type("M", (), {"tag": lambda: "model1"})(), type("M", (), {"tag": lambda: "model2"})()]
                return MockClient()

        old_cur = getattr(srv, "_current_model", None)
        try:
            srv._current_model = type("M", (), {"tag": lambda *args: "model1"})()
            service = ExecutionService(ledger, MockAdapter(), project_root=tmp_path)
            backend = ManagedBackend(tmp_path, store, service=service, worker=MockWorker(), registry={})

            import comsol_mcp._managed_backend as mb
            original_inspect = mb.inspect_node
            mb.inspect_node = lambda worker, tag, path, **kw: {"inspected_tag": tag, "success": True}
            try:
                res = backend._invoke_g2_model("node.inspect", {"path": {"segments": []}}, {}, "op-1", lambda e: None)
                assert res["execution"]["model_ref"]["model_tag"] == "model1"
            finally:
                mb.inspect_node = original_inspect
        finally:
            srv._current_model = old_cur

    def test_validate_report_allowed_unbound_with_multiple_models(self, tmp_path):
        """R02: validate.report executes unbound without raising AMBIGUOUS_TARGET when multiple models exist."""
        from comsol_mcp._managed_backend import ManagedBackend
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._execution_service import ExecutionService
        import comsol_mcp._server as srv

        store = OperationStore(tmp_path / "ops.sqlite3")
        ledger = SessionLedger("session-1", "server-1")
        ledger.bind_model("model1")
        ledger.bind_model("model2")

        old_cur = getattr(srv, "_current_model", None)
        try:
            srv._current_model = None

            class MockAdapter:
                def snapshot(self, tag):
                    return {"external_event_counter": 0, "fingerprint": "fp", "tag": tag}

            class MockWorker:
                paths = type("P", (), {"project_root": tmp_path, "resolved_project_root": lambda: tmp_path})()
                def client(self):
                    class MockClient:
                        def models(self):
                            return [type("M", (), {"tag": lambda: "model1"})(), type("M", (), {"tag": lambda: "model2"})()]
                    return MockClient()

            service = ExecutionService(ledger, MockAdapter(), project_root=tmp_path)
            backend = ManagedBackend(tmp_path, store, service=service, worker=MockWorker(), registry={})
            backend._require_g2_isolation = lambda: {"verified": True}

            dest = str(tmp_path / "report.json")
            res = backend._invoke_g2_model("validate.report", {
                "destination": dest,
                "data": {"status": "PASS", "numerical_verification_status": "PASS"}
            }, {}, "op-report-1", lambda e: None)
            assert res["success"] is True
            assert (tmp_path / "report.json").is_file()
            assert (tmp_path / "report.md").is_file()
        finally:
            srv._current_model = old_cur

    def test_expired_revision_rejected_on_evaluating_operation(self, tmp_path):
        """M1 / F02: Expired revision on evaluation/mutation operation raises REVISION_CONFLICT."""
        from comsol_mcp._managed_backend import ManagedBackend
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._execution_service import ExecutionService

        store = OperationStore(tmp_path / "ops.sqlite3")
        ledger = SessionLedger("session-1", "server-1")
        service = ExecutionService(ledger, None, project_root=tmp_path)
        ref = ledger.bind_model("model1")
        # Advance revision
        ref_obj = ref
        ledger.observe_engine_state(ref_obj, external_event_counter=1, fingerprint="fp2")
        state = ledger._state_for(ref_obj)
        state.revision = 3

        backend = ManagedBackend(tmp_path, store, service=service, worker=type("W", (), {})(), registry={})
        backend._require_g2_isolation = lambda: {"verified": True}

        # Calling with expected_revision=1 when current revision is 3 must raise REVISION_CONFLICT
        with pytest.raises(ExecutionContractError) as exc:
            backend._invoke_g3_model(
                "result.evaluate",
                ref_obj,
                {"spec": {}},
                {"model_ref": ref_obj.as_dict(), "expected_revision": 1},
                "op-eval",
                "session-1",
            )
        assert exc.value.code == "REVISION_CONFLICT"

    def test_p05_isolation_enforced_by_effect_not_name(self, tmp_path):
        """P05: Operations in REQUIRES_ISOLATION require isolation proof regardless of name prefix."""
        from comsol_mcp._managed_backend import ManagedBackend
        from comsol_mcp._operation_store import OperationStore
        from comsol_mcp._execution_service import ExecutionService

        store = OperationStore(tmp_path / "ops.sqlite3")
        ledger = SessionLedger("session-1", "server-1")
        service = ExecutionService(ledger, None, project_root=tmp_path)
        ref = ledger.bind_model("model1")
        ref_obj = ref

        backend = ManagedBackend(tmp_path, store, service=service, worker=type("W", (), {})(), registry={})

        # plot.feature_create requires isolation and must fail if isolation is not established
        with pytest.raises(ExecutionContractError) as exc:
            backend._invoke_g3_model(
                "plot.feature_create",
                ref_obj,
                {"spec": {}},
                {"model_ref": ref_obj.as_dict()},
                "op-eval",
                "session-1",
            )
        assert exc.value.code == "ISOLATION_PROOF_REQUIRED"


class TestObservationRefValidation:
    """Tests ObservationRef verification, hash integrity, and scope tagging."""

    def test_caller_data_alone_yields_external_data_only(self):
        """Caller-supplied observations cannot claim model verification."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        res = validate_solution(worker, "m1", {
            "solution": {"dataset": "dset1"},
            "criteria": {
                "oracle": "steady_state_copper_block",
                "observations": {
                    "T_0.0125": 312.505,
                    "T_0.025": 325.01,
                    "T_0.0375": 337.495,
                    "HeatFlow": 80.05,
                }
            }
        })
        assert res["status"] == STATUS_PASS
        assert res["numerical_verification_status"] == STATUS_PASS
        assert res["scope"] == "EXTERNAL_DATA_ONLY"
        assert res["model_validated"] is False
        assert res["observation_origin"] == "CALLER_SUPPLIED"

    def test_valid_observation_ref_yields_model_validated(self):
        """Legitimate ObservationRef with matching model and hash yields MODEL_VALIDATED."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        obs = {
            "T_0.0125": 312.505,
            "T_0.025": 325.01,
            "T_0.0375": 337.495,
            "HeatFlow": 80.05,
        }
        obs_hash = hashlib.sha256(json.dumps(obs, sort_keys=True).encode("utf-8")).hexdigest()
        obs_ref = ObservationRef(
            observation_id="obs-1",
            model_tag="m1",
            dataset="dset1",
            observations=obs,
            producer_operation_id="op-sample-1",
            sha256=obs_hash,
            scope="MODEL_VALIDATED",
        )

        res = validate_solution(worker, "m1", {
            "solution": {"dataset": "dset1"},
            "criteria": {"oracle": "steady_state_copper_block"},
            "observation_ref": obs_ref,
        })
        assert res["status"] == STATUS_PASS
        assert res["numerical_verification_status"] == STATUS_PASS
        assert res["scope"] == "MODEL_VALIDATED"
        assert res["model_validated"] is True
        assert res["observation_origin"] == "OBSERVATION_REF"

    def test_cross_model_observation_ref_rejected(self):
        """ObservationRef belonging to a different model is rejected."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        obs = {"T_0.025": 325.0}
        obs_ref = ObservationRef(
            observation_id="obs-1",
            model_tag="other_model",
            dataset="dset1",
            observations=obs,
            producer_operation_id="op-1",
        )
        res = validate_solution(worker, "m1", {
            "solution": {"dataset": "dset1"},
            "criteria": {"oracle": "steady_state_copper_block"},
            "observation_ref": obs_ref,
        })
        assert res["status"] == STATUS_FAIL
        assert "Cross-model ObservationRef rejected" in res["message"]

    def test_tampered_observation_ref_hash_rejected(self):
        """ObservationRef with altered observations failing sha256 check is rejected."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        obs = {"T_0.025": 325.0}
        obs_ref = ObservationRef(
            observation_id="obs-1",
            model_tag="m1",
            dataset="dset1",
            observations=obs,
            producer_operation_id="op-1",
            sha256="fake_corrupted_hash",
        )
        res = validate_solution(worker, "m1", {
            "solution": {"dataset": "dset1"},
            "criteria": {"oracle": "steady_state_copper_block"},
            "observation_ref": obs_ref,
        })
        assert res["status"] == STATUS_FAIL
        assert "sha256 mismatch" in res["message"]

    def test_transient_sine_diffusion_oracle_registered(self):
        """C10: transient_sine_diffusion oracle is properly registered and assesses PASS."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        from comsol_mcp._g3_w20_validation import transient_analytical_solution
        xs = [0.25, 0.5, 0.75]
        ts = [0.01, 0.03, 0.1]
        trans_obs = {f"T_{x}_{t}": transient_analytical_solution(x, t) for x in xs for t in ts}

        res = validate_solution(worker, "m1", {
            "solution": {"dataset": "dset1"},
            "criteria": {
                "oracle": "transient_sine_diffusion",
                "observations": trans_obs,
            }
        })
        assert res["status"] == STATUS_PASS
        assert res["numerical_verification_status"] == STATUS_PASS
        assert res["checks"].get("oracle_registered") is not False


class TestValidateReportPublication:
    """Tests F07: Atomic publishing, strict bool overwrite, and status aggregation."""

    def test_strict_bool_overwrite_parsing(self, tmp_path):
        """String 'false' must NOT be parsed as True."""
        dest = str(tmp_path / "report.json")
        worker = type("MockWorker", (), {"client": lambda self: None})()
        # First write
        res1 = validate_report(worker, "m1", {
            "destination": dest,
            "overwrite": True,
            "data": {"status": STATUS_PASS, "numerical_verification_status": STATUS_PASS},
        })
        assert res1["status"] == STATUS_PASS

        # Second write with overwrite="false" must fail (DESTINATION_EXISTS)
        res2 = validate_report(worker, "m1", {
            "destination": dest,
            "overwrite": "false",
            "data": {"status": STATUS_PASS, "numerical_verification_status": STATUS_PASS},
        })
        assert res2["status"] == STATUS_FAIL
        assert "already exists" in res2.get("write_error", "")

        # Invalid bool string raises ValueError
        with pytest.raises(ValueError):
            validate_report(worker, "m1", {
                "destination": dest,
                "overwrite": "maybe",
            })

    def test_unknown_status_does_not_fall_through_to_pass(self, tmp_path):
        """STATUS_UNKNOWN in child data must yield UNVERIFIED, never PASS."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        res = validate_report(worker, "m1", {
            "data": {
                "status": STATUS_UNKNOWN,
                "error_tolerance_data": {"numerical_verification_status": STATUS_UNKNOWN},
            }
        })
        assert res["status"] != STATUS_PASS
        assert res["status"] == STATUS_UNVERIFIED

    def test_atomic_dual_file_publication_and_registration(self, tmp_path):
        """Dual files (.json and .md) are atomically written and registered in ArtifactStore."""
        dest = str(tmp_path / "final_report.json")
        worker = type("MockWorker", (), {"client": lambda self: None})()
        res = validate_report(worker, "m1", {
            "destination": dest,
            "overwrite": False,
            "data": {"status": STATUS_PASS, "numerical_verification_status": STATUS_PASS},
        })
        assert res["status"] == STATUS_PASS
        json_file = tmp_path / "final_report.json"
        md_file = tmp_path / "final_report.md"
        assert json_file.is_file()
        assert md_file.is_file()
        assert ArtifactStore.is_registered_artifact(json_file)
        assert ArtifactStore.is_registered_artifact(md_file)


class TestConvergenceAndConservation:
    """Tests static error rejection in convergence and non-finite conservation check."""

    def test_convergence_rejects_flat_errors(self):
        """Convergence study rejects flat error trend across varying mesh sizes."""
        study = ConvergenceStudy()
        study.add_step(ConvergenceStep(level=1, mesh_size_metric=0.1, tolerance=1e-3, time_step=0.01, error=0.05, resources={}))
        study.add_step(ConvergenceStep(level=2, mesh_size_metric=0.05, tolerance=1e-3, time_step=0.01, error=0.05, resources={}))
        study.add_step(ConvergenceStep(level=3, mesh_size_metric=0.025, tolerance=1e-3, time_step=0.01, error=0.05, resources={}))
        analysis = study.analyze_trend()
        assert analysis["status"] == STATUS_FAIL
        assert analysis["trend"] != "monotonic"

    def test_conservation_rejects_non_finite_and_non_positive_tol(self):
        """Conservation check rejects NaN/Inf terms and non-positive tolerance."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        res_nan = validate_conservation(worker, "m1", {
            "definition": {"inflow": float("nan"), "outflow": 80.0, "tolerance": 0.02}
        })
        assert res_nan["status"] == STATUS_FAIL
        assert "Non-finite" in res_nan["message"]

        res_zero_tol = validate_conservation(worker, "m1", {
            "definition": {"inflow": 80.0, "outflow": 80.0, "tolerance": 0.0}
        })
        assert res_zero_tol["status"] == STATUS_FAIL
        assert "Non-positive tolerance" in res_zero_tol["message"]
