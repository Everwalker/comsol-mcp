#!/usr/bin/env python3
"""Independent Reviewer Adversarial and Negative Control Tests for Round R01.

These tests independently probe the candidate code in comsol_mcp/ without
relying on developer assertions or fixtures.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
import tempfile
import pytest

from mcp.server.fastmcp import FastMCP
from comsol_mcp._execution_contract import (
    ExecutionContractError,
    ModelRef,
    SessionLedger,
    model_ref_from_mapping,
)
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._g3_ops import REQUIRES_ISOLATION
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._artifact_store import ArtifactStore
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
    transient_analytical_solution,
)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tools"))
from review_gate import assess_validator_response


class TestReviewerGatewayAdversarial:
    """Independent adversarial tests for Gateway argument routing (P01-P03, controls)."""

    def test_reviewer_p01_operation_call_preserves_nested_structure(self):
        """Verify operation_call preserves nested arguments dict without flattening."""
        server = FastMCP("rev-p01")
        dispatched = {}

        def mock_dispatch(op, args, execution):
            dispatched["op"] = op
            dispatched["args"] = args
            return {"success": True, "data": args}

        gw = GatewayRegistry(server, mock_dispatch)

        def mock_op_call(operation_id: str, arguments: dict = None, **kwargs):
            return "ok"

        gw.add_tool(mock_op_call, name="operation_call")
        tool = server._tool_manager._tools["operation_call"]

        nested = {"sub_key": "val", "inner_dict": {"a": 1}}
        res = asyncio.run(tool.fn(operation_id="eval.test", arguments=nested))

        assert dispatched["op"] == "operation_call"
        assert "arguments" in dispatched["args"], "Outer arguments key must NOT be stripped"
        assert dispatched["args"]["arguments"] == nested
        assert "sub_key" not in dispatched["args"], "Inner sub_key must NOT be flattened to top level"

    def test_reviewer_p02_execute_java_preserves_arguments_map(self):
        """Verify code.execute_java preserves arguments Map without flattening."""
        server = FastMCP("rev-p02")
        dispatched = {}

        def mock_dispatch(op, args, execution):
            dispatched["op"] = op
            dispatched["args"] = args
            return {"success": True, "data": args}

        gw = GatewayRegistry(server, mock_dispatch)

        def mock_exec_java(source_artifact: str = "", entrypoint: str = "", arguments: dict = None, **kwargs):
            return "ok"

        gw.add_tool(mock_exec_java, name="code.execute_java")
        tool = server._tool_manager._tools["code.execute_java"]

        java_args = {"param_a": 10.5, "param_b": "grid"}
        res = asyncio.run(tool.fn(source_artifact="code.java", entrypoint="Main", arguments=java_args))

        assert dispatched["op"] == "code.execute_java"
        assert "arguments" in dispatched["args"]
        assert dispatched["args"]["arguments"] == java_args
        assert "param_a" not in dispatched["args"]

    def test_reviewer_p03_bound_none_replaced_by_wrapper_value(self):
        """Verify W20 validate tools unpack inner arguments when default is None."""
        server = FastMCP("rev-p03")
        dispatched = {}

        def mock_dispatch(op, args, execution):
            dispatched["op"] = op
            dispatched["args"] = args
            return {"success": True, "data": args}

        gw = GatewayRegistry(server, mock_dispatch)

        def mock_validate_expressions(expressions: list = None, arguments: dict = None):
            return "ok"

        gw.add_tool(mock_validate_expressions, name="validate.expressions")
        tool = server._tool_manager._tools["validate.expressions"]

        exprs = ["T > 300", "p < 1e5"]
        res = asyncio.run(tool.fn(arguments={"expressions": exprs}))

        assert dispatched["op"] == "validate.expressions"
        assert dispatched["args"].get("expressions") == exprs

    def test_reviewer_adversarial_conflicting_parameters_rejected(self):
        """Adversarial: outer argument conflicting with inner wrapped argument produces INVALID_REQUEST."""
        server = FastMCP("rev-conflict")
        gw = GatewayRegistry(server, lambda op, args, exec: {"success": True, "data": {}})

        def mock_validate_report(destination: str = None, overwrite: bool = False, arguments: dict = None):
            return "ok"

        gw.add_tool(mock_validate_report, name="validate.report")
        tool = server._tool_manager._tools["validate.report"]

        res = asyncio.run(tool.fn(destination="file_a.json", arguments={"destination": "file_b.json"}))
        assert res.isError is True
        assert res.structuredContent["error"]["code"] == "INVALID_REQUEST"
        assert "Conflicting values" in res.structuredContent["error"]["message"]

    def test_reviewer_adversarial_unsupported_parameter_rejected(self):
        """Adversarial: unknown kwargs for non-var-kw tool produce INVALID_REQUEST."""
        server = FastMCP("rev-unsupported")
        gw = GatewayRegistry(server, lambda op, args, exec: {"success": True, "data": {}})

        def mock_tool(tag: str = ""):
            return "ok"

        gw.add_tool(mock_tool, name="node.tag")
        tool = server._tool_manager._tools["node.tag"]

        res = asyncio.run(tool.fn(tag="m1", malicious_extra_payload="malicious"))
        assert res.isError is True
        assert res.structuredContent["error"]["code"] == "INVALID_REQUEST"
        assert "Unsupported parameter" in res.structuredContent["error"]["message"]


class TestReviewerModelIsolationAdversarial:
    """Independent adversarial tests for Model Selection, Revision, and Isolation (F02, P05)."""

    def test_reviewer_adversarial_multi_model_without_ref_rejected(self, tmp_path):
        """F02: Multiple active models without explicit ref must raise AMBIGUOUS_TARGET, NOT ledger[0]."""
        store = OperationStore(tmp_path / "ops.sqlite3")
        ledger = SessionLedger("session-1", "server-1")
        ref1 = ledger.bind_model("model_alpha")
        ref2 = ledger.bind_model("model_beta")

        class MockWorker:
            def client(self):
                class MockClient:
                    def models(self):
                        return [
                            type("M", (), {"tag": lambda: "model_alpha"})(),
                            type("M", (), {"tag": lambda: "model_beta"})(),
                        ]
                return MockClient()

        class MockAdapter:
            def snapshot(self, tag):
                return {"external_event_counter": 0, "fingerprint": "fp", "tag": tag}

        service = ExecutionService(ledger, MockAdapter(), project_root=tmp_path)
        backend = ManagedBackend(tmp_path, store, service=service, worker=MockWorker(), registry={})

        with pytest.raises(ExecutionContractError) as exc_info:
            backend._invoke_g2_model(
                "node.inspect",
                {"path": {"segments": []}},
                {},
                "op-no-ref",
                lambda e: None,
            )
        assert exc_info.value.code == "AMBIGUOUS_TARGET"
        assert "Multiple models loaded" in str(exc_info.value)

    def test_reviewer_adversarial_expired_revision_rejected(self, tmp_path):
        """F02: Expired revision must raise REVISION_CONFLICT and NOT be rewritten silently."""
        store = OperationStore(tmp_path / "ops.sqlite3")
        ledger = SessionLedger("session-1", "server-1")
        service = ExecutionService(ledger, None, project_root=tmp_path)
        ref = ledger.bind_model("model_rev")

        # Artificially advance revision to 4
        state = ledger._state_for(ref)
        state.revision = 4

        backend = ManagedBackend(tmp_path, store, service=service, worker=type("W", (), {})(), registry={})
        backend._require_g2_isolation = lambda: {"verified": True}

        # Provide expired revision = 1
        with pytest.raises(ExecutionContractError) as exc_info:
            backend._invoke_g3_model(
                "result.evaluate",
                ref,
                {"spec": {}},
                {"model_ref": ref.as_dict(), "expected_revision": 1},
                "op-rev-eval",
                "session-1",
            )
        assert exc_info.value.code == "REVISION_CONFLICT"
        assert "requested revision 1 does not match managed revision 4" in str(exc_info.value)

    def test_reviewer_p05_name_based_exemption_removed(self, tmp_path):
        """P05: Operations in REQUIRES_ISOLATION must enforce isolation regardless of name."""
        store = OperationStore(tmp_path / "ops.sqlite3")
        ledger = SessionLedger("session-1", "server-1")
        service = ExecutionService(ledger, None, project_root=tmp_path)
        ref = ledger.bind_model("model_iso")

        backend = ManagedBackend(tmp_path, store, service=service, worker=type("W", (), {})(), registry={})

        # When operation is in REQUIRES_ISOLATION and isolation proof is absent, it must raise ISOLATION_PROOF_REQUIRED
        with pytest.raises(ExecutionContractError) as exc_info:
            backend._invoke_g3_model(
                "plot.feature_create",
                ref,
                {"spec": {}},
                {"model_ref": ref.as_dict()},
                "op-iso-test",
                "session-1",
            )
        assert exc_info.value.code == "ISOLATION_PROOF_REQUIRED"


class TestReviewerObservationRefBlindTest:
    """Independent Blind Test: Bad model / caller-only data cannot get MODEL_VALIDATED."""

    def test_reviewer_blind_caller_numbers_cannot_get_model_validated(self):
        """Blind test: caller provides perfect analytical numbers, but without ObservationRef it must be EXTERNAL_DATA_ONLY."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        xs = [0.25, 0.5, 0.75]
        ts = [0.01, 0.03, 0.1]
        perfect_obs = {f"T_{x}_{t}": transient_analytical_solution(x, t) for x in xs for t in ts}

        res = validate_solution(worker, "model_target", {
            "solution": {"dataset": "dset1"},
            "criteria": {
                "oracle": "transient_sine_diffusion",
                "observations": perfect_obs,
            }
        })
        assert res["status"] == STATUS_PASS
        assert res["numerical_verification_status"] == STATUS_PASS
        # Crucial check: Cannot be MODEL_VALIDATED or physical_validation_status == PASS!
        assert res["scope"] == "EXTERNAL_DATA_ONLY", "Caller-supplied numbers must remain EXTERNAL_DATA_ONLY"
        assert res["model_validated"] is False, "Caller-supplied data cannot claim model_validated=True"
        assert res["physical_validation_status"] == STATUS_UNVERIFIED, "Without model observation, physical validation is UNVERIFIED"
        assert res["observation_origin"] == "CALLER_SUPPLIED"

    def test_reviewer_blind_cross_model_ref_rejected(self):
        """Blind test: ObservationRef belonging to model_A cannot validate model_B."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        obs = {"T_0.025": 325.0}
        obs_ref = ObservationRef(
            observation_id="obs-legit-model-A",
            model_tag="model_A",
            dataset="dset1",
            observations=obs,
            producer_operation_id="op-1",
        )
        res = validate_solution(worker, "model_B", {
            "solution": {"dataset": "dset1"},
            "criteria": {"oracle": "steady_state_copper_block"},
            "observation_ref": obs_ref,
        })
        assert res["status"] == STATUS_FAIL
        assert res["scope"] == "CROSS_MODEL_REFUSED"
        assert res["model_validated"] is False
        assert "Cross-model ObservationRef rejected" in res["message"]

    def test_reviewer_blind_dataset_mismatch_rejected(self):
        """Blind test: ObservationRef for dataset_1 cannot validate dataset_2."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        obs = {"T_0.025": 325.0}
        obs_ref = ObservationRef(
            observation_id="obs-dset1",
            model_tag="m1",
            dataset="dataset_1",
            observations=obs,
            producer_operation_id="op-1",
        )
        res = validate_solution(worker, "m1", {
            "solution": {"dataset": "dataset_2"},
            "criteria": {"oracle": "steady_state_copper_block"},
            "observation_ref": obs_ref,
        })
        assert res["status"] == STATUS_FAIL
        assert res["scope"] == "DATASET_MISMATCH"
        assert res["model_validated"] is False

    def test_reviewer_blind_tampered_digest_rejected(self):
        """Blind test: Altering observation numbers so sha256 mismatch occurs is rejected."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        obs = {"T_0.025": 325.0}
        obs_ref = ObservationRef(
            observation_id="obs-tampered",
            model_tag="m1",
            dataset="dset1",
            observations=obs,
            producer_operation_id="op-1",
            sha256="0000000000000000000000000000000000000000000000000000000000000000",
        )
        res = validate_solution(worker, "m1", {
            "solution": {"dataset": "dset1"},
            "criteria": {"oracle": "steady_state_copper_block"},
            "observation_ref": obs_ref,
        })
        assert res["status"] == STATUS_FAIL
        assert res["scope"] == "INTEGRITY_COMPROMISED"
        assert res["model_validated"] is False


class TestReviewerReportAndOracles:
    """Independent tests for Report publishing, strict bool, C10 oracle, and conservation."""

    def test_reviewer_strict_bool_and_atomic_report(self, tmp_path):
        """F07: Overwrite 'false' as string must not allow file overwrite; dual files written atomically."""
        dest = str(tmp_path / "review_report.json")
        worker = type("MockWorker", (), {"client": lambda self: None})()

        # Step 1: initial write
        res1 = validate_report(worker, "m1", {
            "destination": dest,
            "overwrite": True,
            "data": {"status": STATUS_PASS, "numerical_verification_status": STATUS_PASS},
        })
        assert res1["status"] == STATUS_PASS
        assert Path(dest).is_file()
        assert Path(tmp_path / "review_report.md").is_file()

        # Step 2: overwrite with string 'false' -> must FAIL with file exists
        res2 = validate_report(worker, "m1", {
            "destination": dest,
            "overwrite": "false",
            "data": {"status": STATUS_PASS, "numerical_verification_status": STATUS_PASS},
        })
        assert res2["status"] == STATUS_FAIL
        assert "already exists" in res2["write_error"]

    def test_reviewer_c10_oracle_and_assessment(self):
        """C10: transient_sine_diffusion oracle correctly passes assess_validator_response."""
        worker = type("MockWorker", (), {"client": lambda self: None})()
        xs = [0.25, 0.5, 0.75]
        ts = [0.01, 0.03, 0.1]
        trans_obs = {f"T_{x}_{t}": transient_analytical_solution(x, t) for x in xs for t in ts}

        raw_res = validate_solution(worker, "m1", {
            "solution": {"dataset": "dset1"},
            "criteria": {
                "oracle": "transient_sine_diffusion",
                "observations": trans_obs,
            }
        })
        # Wrap in MCP envelope
        mcp_envelope = {
            "isError": False,
            "structuredContent": {
                "success": True,
                "data": raw_res,
            }
        }
        assessment = assess_validator_response(mcp_envelope, "PASS")
        assert assessment["passed"] is True
        assert assessment["product_status"] == STATUS_PASS

    def test_reviewer_conservation_rejects_inf_nan_and_negative_tolerance(self):
        """F08: Conservation check rejects non-finite values and invalid tolerance."""
        worker = type("MockWorker", (), {"client": lambda self: None})()

        # Non-finite inflow
        res_inf = validate_conservation(worker, "m1", {
            "definition": {"inflow": float("inf"), "outflow": 80.0, "tolerance": 0.01}
        })
        assert res_inf["status"] == STATUS_FAIL
        assert "Non-finite term" in res_inf["message"]

        # Negative tolerance
        res_neg_tol = validate_conservation(worker, "m1", {
            "definition": {"inflow": 80.0, "outflow": 80.0, "tolerance": -0.05}
        })
        assert res_neg_tol["status"] == STATUS_FAIL
        assert "Non-positive tolerance" in res_neg_tol["message"]

    def test_reviewer_convergence_rejects_static_errors(self):
        """F06: Convergence study rejects identical/static error values."""
        study = ConvergenceStudy()
        study.add_step(ConvergenceStep(level=1, mesh_size_metric=0.1, tolerance=1e-3, time_step=0.01, error=0.02, resources={}))
        study.add_step(ConvergenceStep(level=2, mesh_size_metric=0.05, tolerance=1e-3, time_step=0.01, error=0.02, resources={}))
        study.add_step(ConvergenceStep(level=3, mesh_size_metric=0.025, tolerance=1e-3, time_step=0.01, error=0.02, resources={}))

        res = study.analyze_trend()
        assert res["status"] == STATUS_FAIL
        assert res["trend"] != "monotonic"
