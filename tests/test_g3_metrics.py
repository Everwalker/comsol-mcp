from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
import uuid
import zipfile

import pytest

from comsol_mcp import _g3_ops
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_registry import validate_call
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._metric_contract import definition_sha256
from comsol_mcp._g2_tools import register as register_g2
from comsol_mcp._observation_store import observation_context
from comsol_mcp._tools_w21 import register as register_w21
from comsol_mcp import _g3_metrics, _w21_execution
from comsol_mcp import _g3_results


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def add_tool(self, function, **options):
        self.tools[options.get("name", function.__name__)] = function


class _Adapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server", "external_event_counter": 0, "fingerprint": "fixed"}


class _Study:
    def getLastComputationDate(self):
        return "2026-09-28"

    def getLastComputationVersion(self):
        return "synthetic"


class _Solution:
    def study(self):
        return "std1"

    def getPVals(self):
        return [1.0]


class _Model:
    def __init__(self, project_root):
        self.project_root = Path(project_root)

    def sol(self, _tag):
        return _Solution()

    def study(self, _tag):
        return _Study()

    def save(self, path, save_copy=True, *, overwrite=True):
        assert save_copy is True
        from comsol_mcp._atomic_save import atomic_save

        def write_mph(temporary):
            with zipfile.ZipFile(temporary, "w") as archive:
                archive.writestr("model/model.mphbin", b"synthetic W21 case model")

        return atomic_save(path, write_mph, self.project_root, overwrite=overwrite)


class _Worker:
    generation = 1

    def __init__(self, project_root):
        self.project_root = Path(project_root)
        self.requests = []

    def client(self):
        return self

    def model(self, _tag):
        return _Model(self.project_root)

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def submit(self, kind, payload, **kwargs):
        self.requests.append((kind, dict(payload), kwargs))
        raise AssertionError("metric definition/evaluation adapter must not issue an unplanned Worker request")


@pytest.fixture(autouse=True)
def _no_native_isolation_gate(monkeypatch):
    # This suite proves the public software route and its SQLite provenance.
    # It does not claim native COMSOL acceptance or owned-server isolation.
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())


def _definition():
    return {
        "expression": "T",
        "expected_unit": "K",
        "aggregate": "integral",
        "complex_mode": "real",
        "solution": {"dataset": "dset1", "solution": "sol1"},
        "selection": {
            "kind": "explicit", "component": "comp1", "geometry": "geom1",
            "entity_dimension": 3, "entities": [1, 2],
        },
    }


def _project(daemon, label):
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": label, "workspace": label,
                      "policy": {"permissions": ["inspect", "project_write", "compute"]}},
        "execution": {"request_id": f"create-{label}", "idempotency_key": f"create-{label}"},
    })
    assert result["success"] is True, result
    return result["data"]["project"]["project_id"], Path(result["data"]["project"]["workspace"])


def _setup(tmp_path, monkeypatch, *, native_values=None, native_complex_mode="real", native_is_complex=False,
           native_global_unit_readback_status="READBACK_ONLY", native_global_unit="1"):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    service = ExecutionService(SessionLedger("session", "server"), _Adapter(), project_root=project_root)
    bound = service.bind_model("model")
    ref = model_ref_from_mapping(bound["execution"]["model_ref"]).as_dict()
    # The fake is used only by the injected native seam and the real observation
    # artifact writer; it never claims to be a COMSOL runtime.
    worker = _Worker(project_root)
    daemon = ControlDaemon(tmp_path / "control", service=service, worker=worker,
                           registry={}, project_root=project_root)
    project_id, workspace = _project(daemon, "metric-project")
    worker.project_root = workspace
    key = daemon.backend._model_project_key(ref)
    daemon.store.put_metadata("revisions", key, {
        "model_ref": ref, "revision": 0, "dirty": False,
        "attribution": "PROJECT_BOUND", "project_id": project_id,
    })
    assert daemon.backend.model_project_binding(ref) == {"attribution": "PROJECT_BOUND", "project_id": project_id}
    execution = {"project_id": project_id, "session_id": "session", "model_ref": ref,
                 "expected_revision": 0, "idempotency_key": "metric-key", "request_id": "metric-request"}
    host = _FakeMcp()
    gateway = GatewayRegistry(
        host,
        dispatcher=lambda operation, arguments, execution: daemon.dispatch({
            "operation": operation, "arguments": arguments, "execution": execution,
        }),
    )
    register_g2(gateway)
    values = iter(native_values if native_values is not None else [3.25, 3.5])
    native_calls = []

    def native_evaluate(_worker, _model_tag, arguments, *, strict_metric_evidence=False):
        assert strict_metric_evidence is True
        native_calls.append(arguments)
        value = next(values)
        spec = arguments["spec"]
        selection = spec.get("selection") or {}
        strict_evidence = {
            "status": "VERIFIED",
            "selection_source": "actual_transient_numerical_feature_readback",
            "selection_membership_identical": True,
            "selection_features": [{"role": "primary", "feature_tag": "primary-feature",
                                    "source": "actual_transient_numerical_feature",
                                    "component": selection.get("component"), "geometry": selection.get("geometry"),
                                    "entity_dimension": selection.get("entity_dimension"), "kind": selection.get("kind"),
                                    "tag": selection.get("tag"), "entities": selection.get("entities"),
                                    "native_selection_readback": {"entities": selection.get("entities"),
                                                                   "geometry": selection.get("geometry"),
                                                                   "dimension": selection.get("entity_dimension")}},
                                   ],
            "selected_solution_pairs": [{"outer": 1, "inner": 1, "solnum": 1}],
            "expression_unit_readback": {"T": "K"},
            "requested_solution": {"dataset": "dset1", "solution": "sol1"},
        }
        cleanup = {"tag": "primary-feature", "type_id": "IntVolume", "created": True,
                   "removed": True, "verified_removed": True, "cleanup_failed": False, "error": None}
        if spec.get("weight_expression") is not None:
            roles = ("weight_validation", "denominator", "numerator")
            cleanup["children"] = []
            for role in roles:
                tag = f"{role}-feature"
                strict_evidence["selection_features"].append({
                    "role": role, "feature_tag": tag,
                    "source": "actual_transient_numerical_feature",
                    "component": selection.get("component"), "geometry": selection.get("geometry"),
                    "entity_dimension": selection.get("entity_dimension"), "kind": selection.get("kind"),
                    "tag": selection.get("tag"), "entities": selection.get("entities"),
                    "native_selection_readback": {"entities": selection.get("entities"),
                                                   "geometry": selection.get("geometry"),
                                                   "dimension": selection.get("entity_dimension")},
                })
                cleanup["children"].append({"tag": tag, "type_id": "IntVolume", "created": True,
                                             "removed": True, "verified_removed": True,
                                             "cleanup_failed": False, "error": None})
            global_readback = {
                "status": native_global_unit_readback_status,
                "unit": native_global_unit if native_global_unit_readback_status == "READBACK_ONLY" else None,
                "expression": spec["weight_expression"],
                "source": "Model.param().evaluateUnit(expression)",
                "scope": "global_parameter_context",
            }
            if native_global_unit_readback_status != "READBACK_ONLY":
                global_readback["unavailable_reason"] = "synthetic global-context resolver did not return a unit"
            strict_evidence["weight_validation"] = {
                "status": "UNVERIFIED",
                "expression": spec["weight_expression"],
                "unit_evidence": {
                    "status": "UNVERIFIED",
                    "roi_context_dimensionality": {
                        "status": "UNVERIFIED",
                        "expression": spec["weight_expression"],
                        "scope": "selected_numerical_feature_expression_over_dataset_and_selection",
                        "reason": "synthetic route has no ROI-context dimensionality resolver",
                    },
                    "global_parameter_context_unit_readback": global_readback,
                    "feature_unit_property_readback": {
                        "value": "1", "source": "NumericalFeature.getStringArray('unit')",
                        "interpretation": "configuration_readback_or_model_dependent_default",
                        "dimensionality_verified": False,
                    },
                },
                "minimum": {
                    "status": "VERIFIED", "source": "MinVolume.getReal_native_minimum",
                    "by_solution": [{"outer": 1, "inner": 1, "solnum": 1, "minimum": 0.5}],
                    "sampling": {
                        "method": "native_minimum_at_integration_points",
                        "points_property_readback": "integration",
                        "minimum_intorder_readback": 4,
                        "sampling_order": "Gauss_integration_points_intorder_4",
                        "scope": "sampled_integration_points_only",
                        "continuous_roi_nonnegativity": "NOT_PROVEN",
                        "integral_rule_configuration": {
                            "status": "VERIFIED_MATCHING_METHOD_AND_ORDER_READBACKS",
                            "minimum_points": "integration", "minimum_intorder": 4,
                            "numerator": {"source": "native_numerical_feature_property_set_and_readback",
                                          "method": "integration", "intorderactive": "on", "intorder": 4},
                            "denominator": {"source": "native_numerical_feature_property_set_and_readback",
                                            "method": "integration", "intorderactive": "on", "intorder": 4},
                            "actual_gauss_point_set_identity": "UNVERIFIED_NATIVE_POINT_IDENTITIES_NOT_EXPOSED",
                        },
                        "actual_sample_coverage": "UNVERIFIED_NATIVE_GAUSS_POINT_IDENTITIES_NOT_EXPOSED",
                    },
                },
                "denominator": {
                    "status": "VERIFIED", "source": "native_integral_of_weight_over_same_dataset_and_ROI",
                    "strictly_positive_finite": True,
                    "by_solution": [{"outer": 1, "inner": 1, "solnum": 1, "value": 2.0}],
                },
                "selection_roles": ["denominator", "numerator", "primary", "weight_validation"],
                "same_dataset_solution_tuple_and_selection": True,
            }
        return {
            "status": {"ok": True, "status": "APPLIED"},
            "cleanup": cleanup,
            "expressions": ["T"], "dataset": "dset1", "solution": "sol1",
            "aggregate": "integral", "complex_mode": native_complex_mode, "is_complex": native_is_complex,
            "values": [value],
            "field_array": {
                "values": [[[[value]]]], "axes": ["expression", "outer", "inner", "point"],
                "shape": [1, 1, 1, 1], "coords": {"outer": [1], "inner": [1], "point": [1]},
                "units": {"expression": "K"}, "metadata": {}, "is_complex": native_is_complex,
            },
            "expression_units": {"T": "K"},
            "strict_metric_evidence": strict_evidence,
        }

    monkeypatch.setattr(_g3_results, "result_evaluate", native_evaluate)
    return daemon, worker, project_id, execution, host, native_calls


def _fallback(host, name, operation_id, arguments, execution):
    response = asyncio.run(host.tools[name](operation_id=operation_id, arguments=arguments, execution=execution))
    return response.structuredContent


def test_metric_contract_rejects_phase_and_bool_threshold_and_publishes_closed_schema():
    from comsol_mcp._g2_registry import operation_describe

    base = {"project_id": "p", "session_id": "s",
            "model_ref": {"schema_version": 1, "session_id": "s", "server_instance_id": "w", "model_tag": "m", "generation": 1},
            "expected_revision": 0, "idempotency_key": "k", "metric_id": "temperature", "definition": _definition()}
    validate_call("metric.define", base)
    with pytest.raises(ExecutionContractError, match="phase"):
        validate_call("metric.define", {**base, "definition": {**_definition(), "complex_mode": "phase"}})
    with pytest.raises(ExecutionContractError, match="finite real"):
        validate_call("metric.define", {**base, "definition": {**_definition(), "threshold": {"relation": "gt", "value": True, "unit": "K"}}})
    weighted_definition = {
        **_definition(),
        "weight": {"expression": "w", "expected_unit": "1", "nonnegative": True},
    }
    validate_call("metric.define", {**base, "definition": weighted_definition})
    with pytest.raises(ExecutionContractError, match="unsupported fields"):
        validate_call("metric.define", {**base, "definition": {
            **weighted_definition,
            "weight": {**weighted_definition["weight"], "dimensionality_verified": True},
        }})
    schema = operation_describe("metric.define")["input_schema"]
    assert schema["additionalProperties"] is False
    assert {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "metric_id", "definition"} <= set(schema["required"])
    compare_envelope = {
        "project_id": "p", "session_id": "s", "model_ref": base["model_ref"],
        "expected_revision": 0, "idempotency_key": "compare-key",
        "cases": [
            {"evaluation_id": "ev-a", "sha256": "a" * 64, "values": [999]},
            {"evaluation_id": "ev-b", "sha256": "b" * 64, "values": [-999]},
        ],
        "metric_ids": ["temperature"],
        "tolerances": {"temperature": {"absolute": 1.0, "relative": 0.0, "unit": "K"}},
    }
    with pytest.raises(ExecutionContractError, match="exactly an evaluation reference"):
        validate_call("metric.compare", compare_envelope)


def test_public_metric_define_evaluate_compare_list_and_tombstone_use_real_sqlite(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, host, native_calls = _setup(tmp_path, monkeypatch)
    try:
        defined = daemon.dispatch({
            "operation": "metric.define", "arguments": {"metric_id": "temperature", "definition": _definition()},
            "execution": dict(execution),
        })
        assert defined["success"] is True, defined
        assert defined["data"]["version"] == 1
        current = {**execution, "expected_revision": defined["execution"]["revision"]}
        # Repeating the same semantic definition produces no new immutable version.
        same = daemon.dispatch({
            "operation": "metric.define", "arguments": {"metric_id": "temperature", "definition": _definition()},
            "execution": {**current, "request_id": "metric-request-2", "idempotency_key": "metric-key-2"},
        })
        assert same["success"] is True and same["data"]["version"] == 1
        current = {**current, "expected_revision": same["execution"]["revision"]}

        listed = _fallback(host, "registry_call", "metric.list", {"filter": {}},
                           {**current, "idempotency_key": "list-key", "request_id": "list"})
        assert listed["success"] is True, listed
        assert len(listed["data"]["metrics"]) == 1
        assert listed["data"]["snapshot"] == "single_sqlite_read_transaction"

        first = _fallback(host, "registry_call", "metric.evaluate", {"metric_ids": ["temperature"]},
                          {**current, "request_id": "eval-1", "idempotency_key": "eval-key-1"})
        current = {**current, "expected_revision": first["execution"]["revision"]}
        second = _fallback(host, "operation_call", "metric.evaluate", {"metric_ids": ["temperature"]},
                           {**current, "request_id": "eval-2", "idempotency_key": "eval-key-2"})
        assert first["success"] is True, first
        assert second["success"] is True, second
        assert len(native_calls) == 2
        assert first["data"]["items"][0]["values"][0]["value"] == 3.25
        assert first["data"]["items"][0]["observation_ref"]["sha256"]
        assert len(list((worker.project_root / "observations").glob("*.json"))) == 2
        current = {**current, "expected_revision": second["execution"]["revision"]}

        compared = _fallback(host, "registry_call", "metric.compare", {
            "cases": [
                {"evaluation_id": first["data"]["evaluation_id"], "sha256": first["data"]["sha256"]},
                {"evaluation_id": second["data"]["evaluation_id"], "sha256": second["data"]["sha256"]},
            ],
            "metric_ids": ["temperature"],
            "tolerances": {"temperature": {"absolute": 0.5, "relative": 0.0, "unit": "K"}},
        }, {**current, "request_id": "compare", "idempotency_key": "compare-key"})
        assert compared["success"] is True, compared
        assert compared["data"]["comparisons"][0]["tuple_results"][0]["comparisons"][0]["status"] == "WITHIN_TOLERANCE"
        current = {**current, "expected_revision": compared["execution"]["revision"]}

        removed = daemon.dispatch({
            "operation": "metric.remove", "arguments": {"metric_id": "temperature"},
            "execution": {**current, "request_id": "remove", "idempotency_key": "remove-key"},
        })
        assert removed["success"] is True and removed["data"]["active"] is False
        current = {**current, "expected_revision": removed["execution"]["revision"]}
        hidden = _fallback(host, "registry_call", "metric.list", {"filter": {}},
                           {**current, "idempotency_key": "hidden-list-key", "request_id": "hidden-list"})
        all_versions = _fallback(host, "registry_call", "metric.list", {"filter": {"include_removed": True}},
                                 {**current, "idempotency_key": "all-list-key", "request_id": "all-list"})
        assert hidden["success"] is True and hidden["data"]["metrics"] == []
        assert all_versions["success"] is True and all_versions["data"]["metrics"][0]["version"] == 2
        assert worker.requests == []
    finally:
        daemon.close()


def test_public_weighted_metric_refuses_when_global_unit_scope_is_not_roi_bound(tmp_path, monkeypatch):
    daemon, worker, _project_id, execution, host, native_calls = _setup(tmp_path, monkeypatch)
    weighted_definition = {
        **_definition(),
        "weight": {"expression": "w", "expected_unit": "1", "nonnegative": True},
    }
    try:
        defined = daemon.dispatch({
            "operation": "metric.define",
            "arguments": {"metric_id": "weighted", "definition": weighted_definition},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        current = {**execution, "expected_revision": defined["execution"]["revision"]}
        first = _fallback(host, "registry_call", "metric.evaluate", {"metric_ids": ["weighted"]},
                          {**current, "request_id": "weighted-eval-1", "idempotency_key": "weighted-eval-1"})
        assert first["success"] is False
        assert first["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert first["error"]["details"]["cause_code"] == "WEIGHT_UNIT_UNVERIFIED"
        assert len(native_calls) == 1 and native_calls[0]["spec"]["weight_expression"] == "w"
        assert list((worker.project_root / "observations").glob("*.json")) == []
    finally:
        daemon.close()


def test_public_weighted_metric_refuses_when_global_unit_diagnostic_is_unavailable(tmp_path, monkeypatch):
    daemon, worker, _project_id, execution, host, native_calls = _setup(
        tmp_path, monkeypatch, native_global_unit_readback_status="UNAVAILABLE"
    )
    weighted_definition = {
        **_definition(),
        "weight": {"expression": "w", "expected_unit": "1", "nonnegative": True},
    }
    try:
        defined = daemon.dispatch({
            "operation": "metric.define",
            "arguments": {"metric_id": "weighted", "definition": weighted_definition},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        evaluated = _fallback(host, "registry_call", "metric.evaluate", {"metric_ids": ["weighted"]},
                              {**execution, "expected_revision": defined["execution"]["revision"],
                               "request_id": "weighted-unverified", "idempotency_key": "weighted-unverified"})
        assert evaluated["success"] is False
        # The engine evaluation already ran, so the operation ledger preserves
        # UNKNOWN while retaining the exact refusal cause from the unit gate.
        assert evaluated["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert evaluated["error"]["details"]["cause_code"] == "WEIGHT_UNIT_UNVERIFIED"
        assert len(native_calls) == 1 and native_calls[0]["spec"]["weight_expression"] == "w"
        assert list((worker.project_root / "observations").glob("*.json")) == []
    finally:
        daemon.close()


def test_metric_project_scope_and_experiment_case_refs_fail_closed(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, host, _calls = _setup(tmp_path, monkeypatch)
    try:
        other_project, _workspace = _project(daemon, "other-metric-project")
        created = daemon.dispatch({
            "operation": "metric.define", "arguments": {"metric_id": "temperature", "definition": _definition()},
            "execution": dict(execution),
        })
        assert created["success"] is True
        # A second project has a separate scope and cannot enumerate the first project's metric.
        other_bound = daemon.backend.service.bind_model("other-model")
        other_ref = model_ref_from_mapping(other_bound["execution"]["model_ref"]).as_dict()
        daemon.store.put_metadata("revisions", daemon.backend._model_project_key(other_ref), {
            "model_ref": other_ref, "revision": 0, "dirty": False,
            "attribution": "PROJECT_BOUND", "project_id": other_project,
        })
        other_exec = {**execution, "project_id": other_project, "model_ref": other_ref,
                      "expected_revision": 0, "request_id": "other-list", "idempotency_key": "other-list-key"}
        foreign = _fallback(host, "registry_call", "metric.list", {"filter": {}}, other_exec)
        assert foreign["success"] is True and foreign["data"]["metrics"] == []
        unassociated = _fallback(host, "registry_call", "metric.compare", {
            "cases": [{"experiment_id": "exp1", "case_id": "case1"}, {"experiment_id": "exp1", "case_id": "case2"}],
            "metric_ids": ["temperature"],
            "tolerances": {"temperature": {"absolute": 0.0, "relative": 0.0, "unit": "K"}},
        }, {**execution, "expected_revision": created["execution"]["revision"],
            "request_id": "compare-unassociated", "idempotency_key": "compare-unassociated"})
        assert unassociated["success"] is False
        assert unassociated["error"]["code"] == "UNVERIFIED_METRIC_ASSOCIATION"

        evaluated = _fallback(host, "registry_call", "metric.evaluate", {"metric_ids": ["temperature"]},
                              {**execution, "expected_revision": created["execution"]["revision"],
                               "request_id": "eval-project-a", "idempotency_key": "eval-project-a"})
        assert evaluated["success"] is True, evaluated
        evaluated_again = _fallback(host, "operation_call", "metric.evaluate", {"metric_ids": ["temperature"]},
                                    {**execution, "expected_revision": evaluated["execution"]["revision"],
                                     "request_id": "eval-project-a-again", "idempotency_key": "eval-project-a-again"})
        assert evaluated_again["success"] is True, evaluated_again
        foreign_compare = _fallback(host, "registry_call", "metric.compare", {
            "cases": [
                {"evaluation_id": evaluated["data"]["evaluation_id"], "sha256": evaluated["data"]["sha256"]},
                {"evaluation_id": evaluated_again["data"]["evaluation_id"], "sha256": evaluated_again["data"]["sha256"]},
            ],
            "metric_ids": ["temperature"],
            "tolerances": {"temperature": {"absolute": 0.0, "relative": 0.0, "unit": "K"}},
        }, {**other_exec, "request_id": "compare-foreign-eval", "idempotency_key": "compare-foreign-eval"})
        assert foreign_compare["success"] is False
        assert foreign_compare["error"]["code"] == "UNVERIFIED_METRIC_EVALUATION"
    finally:
        daemon.close()


def test_metric_compare_rejects_tampered_observation_bytes_without_engine_rpc(tmp_path, monkeypatch):
    daemon, worker, _project_id, execution, host, _native_calls = _setup(tmp_path, monkeypatch)
    try:
        defined = daemon.dispatch({
            "operation": "metric.define", "arguments": {"metric_id": "temperature", "definition": _definition()},
            "execution": dict(execution),
        })
        assert defined["success"] is True, defined
        first = _fallback(host, "registry_call", "metric.evaluate", {"metric_ids": ["temperature"]},
                          {**execution, "expected_revision": defined["execution"]["revision"],
                           "request_id": "tamper-eval-1", "idempotency_key": "tamper-eval-1"})
        second = _fallback(host, "operation_call", "metric.evaluate", {"metric_ids": ["temperature"]},
                           {**execution, "expected_revision": first["execution"]["revision"],
                            "request_id": "tamper-eval-2", "idempotency_key": "tamper-eval-2"})
        assert first["success"] is True and second["success"] is True

        eval_record = daemon.store.get_metadata("artifacts", first["data"]["evaluation_id"])
        observation_ref = eval_record["items"][0]["observation_ref"]
        observation = daemon.store.get_metadata("artifacts", observation_ref["observation_id"])
        raw_path = worker.project_root / observation["artifact"]["file_path"]
        raw_path.write_bytes(raw_path.read_bytes() + b" ")

        compare = _fallback(host, "registry_call", "metric.compare", {
            "cases": [
                {"evaluation_id": first["data"]["evaluation_id"], "sha256": first["data"]["sha256"]},
                {"evaluation_id": second["data"]["evaluation_id"], "sha256": second["data"]["sha256"]},
            ],
            "metric_ids": ["temperature"],
            "tolerances": {"temperature": {"absolute": 1.0, "relative": 0.0, "unit": "K"}},
        }, {**execution, "expected_revision": second["execution"]["revision"],
            "request_id": "tamper-compare", "idempotency_key": "tamper-compare"})
        assert compare["success"] is False
        assert compare["error"]["code"] == "INTEGRITY_COMPROMISED"
        assert worker.requests == []
    finally:
        daemon.close()


def test_metric_compare_rejects_overflow_from_finite_persisted_artifact_values(tmp_path, monkeypatch):
    daemon, worker, _project_id, execution, host, _native_calls = _setup(
        tmp_path, monkeypatch, native_values=[-1.0e308, 1.0e308]
    )
    try:
        defined = daemon.dispatch({
            "operation": "metric.define", "arguments": {"metric_id": "temperature", "definition": _definition()},
            "execution": dict(execution),
        })
        assert defined["success"] is True, defined
        first = _fallback(host, "registry_call", "metric.evaluate", {"metric_ids": ["temperature"]},
                          {**execution, "expected_revision": defined["execution"]["revision"],
                           "request_id": "overflow-eval-1", "idempotency_key": "overflow-eval-1"})
        second = _fallback(host, "operation_call", "metric.evaluate", {"metric_ids": ["temperature"]},
                           {**execution, "expected_revision": first["execution"]["revision"],
                            "request_id": "overflow-eval-2", "idempotency_key": "overflow-eval-2"})
        assert first["success"] is True and second["success"] is True
        assert first["data"]["items"][0]["values"][0]["value"] == -1.0e308
        assert second["data"]["items"][0]["values"][0]["value"] == 1.0e308
        assert len(list((worker.project_root / "observations").glob("*.json"))) == 2

        compare = _fallback(host, "registry_call", "metric.compare", {
            "cases": [
                {"evaluation_id": first["data"]["evaluation_id"], "sha256": first["data"]["sha256"]},
                {"evaluation_id": second["data"]["evaluation_id"], "sha256": second["data"]["sha256"]},
            ],
            "metric_ids": ["temperature"],
            "tolerances": {"temperature": {"absolute": 0.0, "relative": 2.0, "unit": "K"}},
        }, {**execution, "expected_revision": second["execution"]["revision"],
            "request_id": "overflow-compare", "idempotency_key": "overflow-compare"})
        assert compare["success"] is False
        assert compare["error"]["code"] == "COMPARISON_OVERFLOW"
        assert worker.requests == []
    finally:
        daemon.close()


def test_metric_definition_store_serializes_same_definition_and_fences_stale_remove(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = OperationStore(tmp_path / "metric-store.sqlite3")
    try:
        definition = _definition()
        base = {
            "kind": "w17_metric_definition_version", "schema_version": 1,
            "project_id": "project-a", "metric_id": "temperature",
            "definition": definition, "definition_sha256": definition_sha256(definition),
            "created_model_ref": {"model_tag": "m"}, "created_revision": 0,
            "producer": "operation-1", "removed": False,
        }
        with ThreadPoolExecutor(max_workers=8) as pool:
            versions = list(pool.map(
                lambda _index: store.append_metric_definition("project-a", "temperature", dict(base)),
                range(8),
            ))
        assert {row["version"] for row in versions} == {1}
        changed = {**base, "definition": {**definition, "expression": "p"},
                   "definition_sha256": definition_sha256({**definition, "expression": "p"})}
        next_version = store.append_metric_definition("project-a", "temperature", changed)
        assert next_version["version"] == 2
        stale_remove = {**changed, "removed": True, "_expected_latest_version": 1}
        with pytest.raises(ValueError, match="advanced"):
            store.append_metric_definition("project-a", "temperature", stale_remove)
        exact_remove = {**changed, "removed": True, "_expected_latest_version": 2}
        tombstone = store.append_metric_definition("project-a", "temperature", exact_remove)
        assert tombstone["version"] == 3 and tombstone["removed"] is True
        rows = store.read_metric_definitions("project-a", metric_id="temperature", include_removed=True)
        assert len(rows) == 1 and rows[0]["version"] == 3
        assert store.read_metric_definitions("project-b") == []
        version_key = f"w17metric.version.{store._metric_scope_digest('project-a', 'temperature')}.00000003"
        with pytest.raises(ValueError, match="immutable"):
            store.put_metadata("artifacts", version_key, {**tombstone, "removed": False})
    finally:
        store.close()


def test_metric_complex_comparison_requires_and_uses_explicit_modulus(tmp_path, monkeypatch):
    daemon, worker, _project_id, execution, host, _native_calls = _setup(
        tmp_path, monkeypatch,
        native_values=[{"real": 1.0, "imag": 2.0}, {"real": 4.0, "imag": 6.0}],
        native_complex_mode="preserve", native_is_complex=True,
    )
    try:
        definition = {**_definition(), "complex_mode": "preserve"}
        created = daemon.dispatch({
            "operation": "metric.define", "arguments": {"metric_id": "phasor", "definition": definition},
            "execution": {**execution, "request_id": "complex-define", "idempotency_key": "complex-define"},
        })
        assert created["success"] is True, created
        first = _fallback(host, "registry_call", "metric.evaluate", {"metric_ids": ["phasor"]},
                          {**execution, "expected_revision": created["execution"]["revision"],
                           "request_id": "complex-eval-1", "idempotency_key": "complex-eval-1"})
        second = _fallback(host, "operation_call", "metric.evaluate", {"metric_ids": ["phasor"]},
                           {**execution, "expected_revision": first["execution"]["revision"],
                            "request_id": "complex-eval-2", "idempotency_key": "complex-eval-2"})
        assert first["success"] is True and second["success"] is True
        cases = [
            {"evaluation_id": first["data"]["evaluation_id"], "sha256": first["data"]["sha256"]},
            {"evaluation_id": second["data"]["evaluation_id"], "sha256": second["data"]["sha256"]},
        ]
        without_distance = _fallback(host, "registry_call", "metric.compare", {
            "cases": cases, "metric_ids": ["phasor"],
            "tolerances": {"phasor": {"absolute": 6.0, "relative": 0.0, "unit": "K"}},
        }, {**execution, "expected_revision": second["execution"]["revision"],
            "request_id": "complex-compare-no-distance", "idempotency_key": "complex-compare-no-distance"})
        assert without_distance["success"] is False
        assert without_distance["error"]["code"] == "API_UNSUPPORTED"
        with_distance = _fallback(host, "operation_call", "metric.compare", {
            "cases": cases, "metric_ids": ["phasor"],
            "tolerances": {"phasor": {"absolute": 6.0, "relative": 0.0, "unit": "K", "complex_distance": "modulus"}},
        }, {**execution, "expected_revision": without_distance["execution"]["revision"],
            "request_id": "complex-compare-modulus", "idempotency_key": "complex-compare-modulus"})
        assert with_distance["success"] is True, with_distance
        row = with_distance["data"]["comparisons"][0]["tuple_results"][0]
        assert row["comparisons"][0]["absolute_delta"] == 5.0
        assert row["comparisons"][0]["status"] == "WITHIN_TOLERANCE"
        assert worker.requests == []
    finally:
        daemon.close()


def _begin_callback_operation(daemon, project_id, model_ref, revision, operation, arguments):
    request_id = operation + "-" + uuid.uuid4().hex
    metadata = {
        "operation": operation,
        "arguments": dict(arguments),
        "execution": {"project_id": project_id, "model_ref": dict(model_ref), "expected_revision": revision},
    }
    request_hash = hashlib.sha256(json.dumps(
        [operation, arguments, project_id, revision, request_id], sort_keys=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    record, reused = daemon.store.begin(
        request_id=request_id, idempotency_key=request_id,
        request_hash=request_hash, operation=operation, metadata=metadata,
    )
    assert reused is False
    job = daemon.store.operation_job(record["operation_id"])
    daemon.store.update_job(job["job_id"], "RUNNING")
    return record["operation_id"], job["job_id"]


def _install_synthetic_experiment_callbacks(monkeypatch, *, wrong_readback_case=None, fail_solve=False,
                                            identity_drift_call=None):
    state = {"parameter": None, "solved_parameter": None, "solve_count": 0, "unit_calls": [],
             "identity_calls": 0, "attempt_ids_seen_by_solve": [], "saved_case_values": []}
    monkeypatch.setattr(_w21_execution, "validate_definition", lambda *_args: None)
    monkeypatch.setattr(_w21_execution, "validate_parameters", lambda *_args: None)
    monkeypatch.setattr(
        _w21_execution,
        "model_identity",
        lambda *_args: {"engine": {"comsol_version": "synthetic-6.4"}, "cache_policy": "SYNTHETIC"},
    )

    def apply_parameters(_worker, _tag, values, units):
        state["parameter"] = float(values["p"])
        readback_value = state["parameter"] + 1.0 if wrong_readback_case == state["parameter"] else state["parameter"]
        return {
            "scope": "model",
            "parameters": [{
                "name": "p", "group": None, "expression": repr(float(values["p"])) + "[" + units["p"] + "]",
                "unit": units["p"],
                # ModelParam.evaluate(String) is in the root base unit system.
                "evaluated": {"kind": "float64", "shape": [], "data": readback_value / 100.0},
            }],
        }

    class _RequestedUnitParam:
        def evaluate(self, name, unit):
            state["unit_calls"].append((name, unit, state["parameter"]))
            if name != "p" or unit != "cm":
                raise AssertionError("case readback did not request the exact declared unit")
            return state["parameter"] + (1.0 if wrong_readback_case == state["parameter"] else 0.0)

    monkeypatch.setattr(_Model, "param", lambda _self, *args: _RequestedUnitParam(), raising=False)
    monkeypatch.setattr(_w21_execution, "apply_parameters", apply_parameters)

    def study_run(_worker, _tag, _arguments):
        attempts = [row for row in state["store"].list_metadata("artifacts")
                    if row.get("kind") == "w21experiment_case_attempt"
                    and row.get("parameters") == {"p": state["parameter"]}]
        assert len(attempts) == 1, "the exact case attempt must be durable before Study.run"
        state["attempt_ids_seen_by_solve"].append(attempts[0]["attempt_id"])
        state["solve_count"] += 1
        state["solved_parameter"] = state["parameter"]
        if fail_solve:
            return {"status": "FAILED", "failed": True, "study": "std1"}
        return {"status": "COMPLETED", "study": "std1", "solver_reply": f"solve-{state['solve_count']}"}

    monkeypatch.setattr(_w21_execution, "study_run", study_run)
    monkeypatch.setattr(
        _w21_execution,
        "result_at_points",
        lambda _worker, _tag, _spec: {
            "status": {"ok": True, "status": "APPLIED"},
            "dataset": "dset1", "solution": "sol1", "expressions": ["T"],
            "values": [[[[300.0 + state["solved_parameter"]]]]],
            "field_array": {
                "values": [[[[300.0 + state["solved_parameter"]]]]],
                "axes": ["expression", "outer", "inner", "point"],
                "shape": [1, 1, 1, 1],
                "coords": {"outer": [1], "inner": [1], "point": [1]},
                "units": {"expression": "K"}, "metadata": {}, "is_complex": False,
            },
        },
    )
    monkeypatch.setattr(
        _w21_execution,
        "stored_times",
        lambda _worker, _tag, sample: ([0.0], {
            "binding_complete": True, "solution": sample["solution"], "time_values": [0.0],
        }),
    )
    from comsol_mcp import _g3_w20_validation
    monkeypatch.setattr(
        _g3_w20_validation, "validate_solution",
        lambda *_args: {"numerical_verification_status": "PASS"},
    )
    monkeypatch.setattr(_w21_execution, "extract_metrics", lambda *_args: {"legacy_metrics": {"peak": 301.0}})

    from comsol_mcp import _observation_store
    def solution_identity(_worker, _tag, solution):
        state["identity_calls"] += 1
        observed_solution = "sol-other" if state["identity_calls"] == identity_drift_call else solution
        return {
            "solution": observed_solution, "study": "std1",
            "computation_date": f"synthetic-solve-{state['solved_parameter']}",
            "computation_version": "synthetic-comsol-6.4",
            "times": [0.0],
        }

    monkeypatch.setattr(_observation_store, "solution_identity", solution_identity)

    def save_same_worker_case(_model, path, save_copy=True, *, overwrite=True):
        assert save_copy is True
        value = state["solved_parameter"]
        from comsol_mcp._atomic_save import atomic_save

        def write_mph(temporary):
            with zipfile.ZipFile(temporary, "w") as archive:
                archive.writestr("case.txt", str(value))

        saved = atomic_save(path, write_mph, _model.project_root, overwrite=overwrite)
        state["saved_case_values"].append(value)
        return saved

    monkeypatch.setattr(_Model, "save", save_same_worker_case)
    return state


def _run_metric_experiment(daemon, worker, project_id, model_ref, host, state, *, cases=(1.0, 2.0),
                           metric_id="case-temperature", run_experiment=True, after_design=None,
                           objective_contract=None, run_resources=None):
    define_args = {"metric_id": metric_id, "definition": _definition()}
    define_operation, define_job = _begin_callback_operation(
        daemon, project_id, model_ref, 0, "metric.define", define_args,
    )
    with observation_context(daemon.store, model_ref, 0, define_operation, project_id=project_id):
        metric_ref = _g3_metrics.metric_define(worker, "model", define_args)
    daemon.store.update_job(define_job, "SUCCEEDED", result={"success": True, "data": metric_ref})

    experiment_definition = {
        "study": "std1",
        "sample": {"spec": {"solution": {"dataset": "dset1"}, "expressions": ["T"]},
                   "points": [[0.0, 0.0]], "coordinate_unit": "m"},
        "metrics": {"peak": {"expression": "T", "unit": "K", "indices": [0]}},
        "times": [0.0],
        "validation": {"range": [250.0, 400.0]},
        "parameters": {"p": [float(value) for value in cases]},
        "units": {"p": "cm"},
        "sampling": {"kind": "cartesian_grid"},
        "budget": {"max_cases": len(cases), "max_wall_time_s": 60.0},
        "metric_evaluations": [{
            "metric_id": metric_ref["metric_id"], "version": metric_ref["version"],
            "definition_sha256": metric_ref["definition_sha256"],
        }],
    }
    if objective_contract is not None:
        experiment_definition.update(
            objective_contract(metric_ref) if callable(objective_contract) else objective_contract
        )
    design_args = {"definition": experiment_definition}
    design_operation, design_job = _begin_callback_operation(
        daemon, project_id, model_ref, 0, "experiment.design", design_args,
    )
    with observation_context(daemon.store, model_ref, 0, design_operation, project_id=project_id):
        design = _w21_execution.op_experiment_design(worker, "model", design_args)
    daemon.store.update_job(design_job, "SUCCEEDED", result={"success": True, "data": design})

    if not run_experiment:
        return metric_ref, design, None, None, None
    if after_design is not None:
        after_design(metric_ref, design)

    run_args = {"experiment_id": design["experiment_id"]}
    if run_resources is not None:
        run_args["resources"] = run_resources
    run_operation, run_job = _begin_callback_operation(
        daemon, project_id, model_ref, design["model_revision"], "experiment.run", run_args,
    )
    state["store"] = daemon.store
    with observation_context(daemon.store, model_ref, design["model_revision"], run_operation,
                            project_id=project_id):
        run = _w21_execution.op_experiment_run(worker, "model", run_args)
    daemon.store.update_job(run_job, "SUCCEEDED", result={"success": True, "data": run})
    return metric_ref, design, run, run_operation, run_job


def _public_experiment_host(daemon):
    host = _FakeMcp()
    gateway = GatewayRegistry(
        host,
        dispatcher=lambda operation, arguments, execution: daemon.dispatch({
            "operation": operation, "arguments": arguments, "execution": execution,
        }),
    )
    register_w21(gateway)
    return host


def test_experiment_run_binds_frozen_metric_versions_to_attempt_solution_and_public_durable_case(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(tmp_path, monkeypatch)
    model_ref = execution["model_ref"]
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    daemon2 = None
    try:
        version2_result = {}

        def add_new_metric_head(metric_ref, design):
            update_args = {"metric_id": metric_ref["metric_id"],
                           "definition": {**_definition(), "threshold": {"relation": "gte", "value": 3.0,
                                                                           "unit": "K"}}}
            update_operation, update_job = _begin_callback_operation(
                daemon, project_id, model_ref, design["model_revision"], "metric.define", update_args,
            )
            with observation_context(daemon.store, model_ref, design["model_revision"], update_operation,
                                    project_id=project_id):
                version2_result.update(_g3_metrics.metric_define(worker, "model", update_args))
            daemon.store.update_job(update_job, "SUCCEEDED",
                                    result={"success": True, "data": dict(version2_result)})

        metric_ref, design, run, run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, model_ref, None, state, after_design=add_new_metric_head,
        )
        assert run["status"] == "COMPLETE"
        assert len(run["cases"]) == 2
        assert version2_result["version"] == 2
        assert state["unit_calls"] == [("p", "cm", 1.0), ("p", "cm", 2.0)]
        assert [row["case_value_readback"]["value"] for case in run["cases"]
                for row in case["parameter_readback"]["parameters"]] == [1.0, 2.0]
        attempt_ids = [case["case_attempt_id"] for case in run["cases"]]
        assert len(set(attempt_ids)) == 2 and all(value.startswith("att_") for value in attempt_ids)
        for case in run["cases"]:
            assert case["solve"]["case_attempt_id"] == case["case_attempt_id"]
            assert case["sample"]["case_attempt_id"] == case["case_attempt_id"]
            assert case["parameter_readback"]["case_attempt_id"] == case["case_attempt_id"]
            assert case["metric_evaluation"]["case_attempt_id"] == case["case_attempt_id"]
            assert case["metric_evaluation"]["metric_definition_refs"] == [{
                "metric_id": metric_ref["metric_id"], "version": 1,
                "definition_sha256": metric_ref["definition_sha256"],
            }]
            evaluation = daemon.store.get_metadata("artifacts", case["metric_evaluation"]["evaluation_id"])
            assert evaluation["case_binding"]["attempt_id"] == case["case_attempt_id"]
            assert evaluation["case_binding"]["solution_identity"]["computation_date"].endswith(
                str(case["parameters"]["p"])
            )
            assert evaluation["items"][0]["definition_version"] == 1
            assert evaluation["items"][0]["case_attempt_id"] == case["case_attempt_id"]
            saved_case = case["case_model_artifact"]
            assert saved_case["status"] == "SAVED_HASH_VERIFIED_RELOAD_UNVERIFIED"
            assert saved_case["save_copy"] is True
            assert saved_case["restore_status"] == "NOT_RELOAD_VERIFIED"
            case_file = worker.project_root / saved_case["relative_path"]
            with zipfile.ZipFile(case_file) as archive:
                assert archive.read("case.txt").decode() == str(case["parameters"]["p"])
        assert state["saved_case_values"] == [1.0, 2.0]

        # Public reads still bind every case to the design's immutable v1 definition.
        host = _public_experiment_host(daemon)
        for case in run["cases"]:
                response = asyncio.run(host.tools["experiment_case_result"](
                    project_id=project_id, experiment_id=design["experiment_id"], case_id=case["case_id"],
                )).structuredContent
                assert response["success"] is True, response
                assert response["data"]["result"]["metric_evaluation"]["case_attempt_id"] == case["case_attempt_id"]
                assert response["data"]["case_model_artifact"]["status"] == "SAVED_HASH_VERIFIED_RELOAD_UNVERIFIED"
        inspected = asyncio.run(host.tools["experiment_inspect"](
            project_id=project_id, experiment_id=design["experiment_id"],
        )).structuredContent
        assert inspected["success"] is True, inspected
        assert [row["status"] for row in inspected["data"]["cases"]] == ["COMPLETED", "COMPLETED"]
        assert [row["parameters"] for row in inspected["data"]["cases"]] == [{"p": 1.0}, {"p": 2.0}]

        # Durable association reads survive daemon/store reopen and never need a Worker.
        daemon.close()
        daemon = None
        daemon2 = ControlDaemon(tmp_path / "control", project_root=tmp_path / "projects", registry={})
        host2 = _public_experiment_host(daemon2)
        reopened = asyncio.run(host2.tools["experiment_case_result"](
            project_id=project_id, experiment_id=design["experiment_id"], case_id="case-0002",
        )).structuredContent
        assert reopened["success"] is True, reopened
        assert reopened["data"]["result"]["metric_evaluation"]["evaluation_id"] == run["cases"][1]["metric_evaluation"]["evaluation_id"]
        assert reopened["data"]["case_model_artifact"]["file_sha256"] == run["cases"][1]["case_model_artifact"]["file_sha256"]
        assert daemon2.backend.worker is None
        assert daemon2.session_scheduler.submit is not None

        foreign_project, _foreign_workspace = _project(daemon2, "foreign-association-project")
        foreign_read = asyncio.run(host2.tools["experiment_case_result"](
            project_id=foreign_project, experiment_id=design["experiment_id"], case_id="case-0002",
        )).structuredContent
        assert foreign_read["success"] is False
        assert foreign_read["error"]["code"] == "EXPERIMENT_NOT_FOUND"

        # A SQLite tamper of the attempt payload cannot keep the old hash valid.
        attempt_key = f"w21experimentattempt:{design['experiment_id']}:case-0002"
        attempt_record = daemon2.store.get_metadata("artifacts", attempt_key)
        attempt_record["parameters"]["p"] = 200.0
        daemon2.store.db.execute(
            "UPDATE artifacts SET metadata=? WHERE artifact_id=?",
            (json.dumps(attempt_record, sort_keys=True), attempt_key),
        )
        daemon2.store.db.commit()
        tampered = asyncio.run(host2.tools["experiment_case_result"](
            project_id=project_id, experiment_id=design["experiment_id"], case_id="case-0002",
        )).structuredContent
        assert tampered["success"] is False
        assert tampered["error"]["code"] == "EXPERIMENT_STATE_UNKNOWN"

        # Re-running this frozen experiment is not an idempotent solve retry.
        rerun_operation, _rerun_job = _begin_callback_operation(
            daemon2, project_id, model_ref, design["model_revision"], "experiment.run",
            {"experiment_id": design["experiment_id"]},
        )
        with observation_context(daemon2.store, model_ref, design["model_revision"], rerun_operation,
                                project_id=project_id):
            with pytest.raises(ExecutionContractError, match="already has a run claim"):
                _w21_execution.op_experiment_run(worker, "model", {"experiment_id": design["experiment_id"]})
        assert state["solve_count"] == 2
    finally:
        if daemon is not None:
            daemon.close()
        if daemon2 is not None:
            daemon2.close()


def _objective_contract(metric_ref, *, bound=3.4, direction="minimize"):
    reference = {key: metric_ref[key] for key in ("metric_id", "version", "definition_sha256")}
    return {
        "objective": {
            "metric_ref": reference, "tuple": {"outer": 1, "inner": 1},
            "direction": direction, "unit": "K",
        },
        "constraints": [{
            "constraint_id": "temperature-limit", "metric_ref": reference,
            "tuple": {"outer": 1, "inner": 1}, "relation": "<=", "value": bound, "unit": "K",
        }],
    }


def test_experiment_inspect_ranks_only_verified_feasible_completed_cases_and_marks_partial_scope(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(tmp_path, monkeypatch)
    model_ref = execution["model_ref"]
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    try:
        _metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, model_ref, None, state,
            objective_contract=_objective_contract,
        )
        invalid_definitions = []
        bool_index = copy.deepcopy(design["definition"])
        bool_index["objective"]["tuple"]["outer"] = True
        invalid_definitions.append(bool_index)
        nonfinite_bound = copy.deepcopy(design["definition"])
        nonfinite_bound["constraints"][0]["value"] = float("nan")
        invalid_definitions.append(nonfinite_bound)
        unknown_contract_field = copy.deepcopy(design["definition"])
        unknown_contract_field["objective"]["allow_projection"] = True
        invalid_definitions.append(unknown_contract_field)
        for invalid_definition in invalid_definitions:
            with pytest.raises(PreWriteRefusal):
                _w21_execution._strict_grid_design(worker, "model", invalid_definition)
        wrong_frozen_ref = copy.deepcopy(design["definition"])
        wrong_frozen_ref["objective"]["metric_ref"]["version"] = 2
        with pytest.raises(PreWriteRefusal):
            _w21_execution._validate_experiment_objective_contract(
                wrong_frozen_ref, design["metric_definition_snapshots"],
            )
        wrong_unit = copy.deepcopy(design["definition"])
        wrong_unit["objective"]["unit"] = "degC"
        with pytest.raises(PreWriteRefusal):
            _w21_execution._validate_experiment_objective_contract(
                wrong_unit, design["metric_definition_snapshots"],
            )
        preserve_complex = copy.deepcopy(design["metric_definition_snapshots"])
        preserve_complex[0]["definition"]["complex_mode"] = "preserve"
        with pytest.raises(PreWriteRefusal):
            _w21_execution._validate_experiment_objective_contract(
                design["definition"], preserve_complex,
            )
        assert run["status"] == "COMPLETE"
        public = _public_experiment_host(daemon)
        result = asyncio.run(public.tools["experiment_inspect"](
            project_id=project_id, experiment_id=design["experiment_id"],
        )).structuredContent
        assert result["success"] is True, result
        best = result["data"]["best_feasible"]
        assert best["status"] == "FOUND"
        assert best["case_id"] == "case-0001"
        assert best["objective"]["value"] == 3.25
        assert best["constraints"][0]["status"] == "PASS"
        assert best["comparison_scope"] == "verified_completed_cases"
        assert best["ranking_complete"] is True
        assert best["tie_break"] == "case_ordinal_ascending"
        assert best["counts"] == {
            "total_cases": 2, "verified_completed_cases": 2, "feasible_cases": 1,
            "infeasible_cases": 1, "unresolved_cases": 0, "not_run_cases": 0, "failed_cases": 0,
        }

        # A resource-limited execution may report the best completed feasible
        # case but must not call its ranking complete while a planned case did
        # not run.
        partial_root = tmp_path / "partial"
        partial_root.mkdir()
        partial_daemon, partial_worker, partial_project, partial_execution, _host2, _calls2 = _setup(
            partial_root, monkeypatch,
        )
        try:
            partial_model_ref = partial_execution["model_ref"]
            partial_state = _install_synthetic_experiment_callbacks(monkeypatch)
            _ref, partial_design, partial_run, _op, _job = _run_metric_experiment(
                partial_daemon, partial_worker, partial_project, partial_model_ref, None, partial_state,
                objective_contract=_objective_contract, run_resources={"max_cases": 1},
            )
            assert partial_run["status"] == "PARTIAL"
            partial_public = _public_experiment_host(partial_daemon)
            partial_result = asyncio.run(partial_public.tools["experiment_inspect"](
                project_id=partial_project, experiment_id=partial_design["experiment_id"],
            )).structuredContent
            assert partial_result["success"] is True, partial_result
            partial_best = partial_result["data"]["best_feasible"]
            assert partial_best["status"] == "FOUND"
            assert partial_best["case_id"] == "case-0001"
            assert partial_best["comparison_scope"] == "verified_completed_cases"
            assert partial_best["ranking_complete"] is False
            assert partial_best["counts"]["total_cases"] == 2
            assert partial_best["counts"]["verified_completed_cases"] == 1
            assert partial_best["counts"]["not_run_cases"] == 1
        finally:
            partial_daemon.close()
    finally:
        if daemon is not None:
            daemon.close()


def test_experiment_inspect_reports_none_feasible_only_after_every_terminal_case_fails_constraints(tmp_path, monkeypatch):
    daemon, worker, project_id, _execution, _host, _native_calls = _setup(
        tmp_path, monkeypatch, native_values=[3.25, 3.5],
    )
    model_ref = _execution["model_ref"]
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    try:
        _ref, design, run, _operation, _job = _run_metric_experiment(
            daemon, worker, project_id, model_ref, None, state,
            objective_contract=lambda metric_ref: _objective_contract(metric_ref, bound=2.0),
        )
        assert run["status"] == "COMPLETE"
        public = _public_experiment_host(daemon)
        result = asyncio.run(public.tools["experiment_inspect"](
            project_id=project_id, experiment_id=design["experiment_id"],
        )).structuredContent
        assert result["success"] is True, result
        best = result["data"]["best_feasible"]
        assert best["status"] == "NONE_FEASIBLE"
        assert best["case_id"] is None
        assert best["comparison_scope"] == "all_planned_terminal_cases"
        assert best["ranking_complete"] is True
        assert best["counts"]["infeasible_cases"] == 2
        assert best["counts"]["unresolved_cases"] == 0
    finally:
        daemon.close()


def test_experiment_objective_tuple_resolution_requires_one_finite_projected_value():
    metric_ref = {"metric_id": "temperature", "version": 1, "definition_sha256": "a" * 64}
    metric_record = {**metric_ref, "definition": {"complex_mode": "real", "expected_unit": "K"}}
    base_item = {
        "metric_id": "temperature", "definition_version": 1,
        "definition_sha256": "a" * 64, "definition": metric_record["definition"],
        "complex_mode": "real", "unit": "K",
    }

    def resolve(rows):
        return ControlDaemon._verified_experiment_metric_value(
            {"evaluation_id": "mev_test", "sha256": "b" * 64,
             "items": [{**base_item, "values": rows}]},
            metric_record, metric_ref, {"outer": 1, "inner": 1}, "K",
        )

    exact = {"outer": 1, "inner": 1, "solnum": 1, "value": 4.5}
    assert resolve([exact])["value"] == 4.5
    assert resolve([exact, dict(exact)]) is None
    assert resolve([{**exact, "value": True}]) is None
    assert resolve([{**exact, "value": float("inf")}]) is None
    assert resolve([{**exact, "value": 10**10000}]) is None


def test_experiment_objective_ties_use_ascending_case_ordinal(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, _host, _calls = _setup(
        tmp_path, monkeypatch, native_values=[3.25, 3.25],
    )
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    try:
        _ref, design, _run, _operation, _job = _run_metric_experiment(
            daemon, worker, project_id, execution["model_ref"], None, state,
            objective_contract=_objective_contract,
        )
        response = asyncio.run(_public_experiment_host(daemon).tools["experiment_inspect"](
            project_id=project_id, experiment_id=design["experiment_id"],
        )).structuredContent
        assert response["success"] is True, response
        best = response["data"]["best_feasible"]
        assert best["status"] == "FOUND"
        assert best["case_id"] == "case-0001"
        assert best["tie_break"] == "case_ordinal_ascending"
        assert best["counts"]["feasible_cases"] == 2
    finally:
        daemon.close()


@pytest.mark.parametrize("observation_role", ["case_sample", "metric_item"])
@pytest.mark.parametrize("damage", ["rewrite", "missing", "symlink_escape"])
def test_public_experiment_reads_rehash_actual_observation_files(
    tmp_path, monkeypatch, observation_role, damage,
):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(
        tmp_path, monkeypatch, native_values=[3.25],
    )
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    target_path = None
    original_bytes = None
    try:
        _metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, execution["model_ref"], None, state, cases=(1.0,),
        )
        case = run["cases"][0]
        evaluation = daemon.store.get_metadata("artifacts", case["metric_evaluation"]["evaluation_id"])
        reference = (
            evaluation["case_binding"]["sample_observation_ref"]
            if observation_role == "case_sample"
            else evaluation["items"][0]["observation_ref"]
        )
        observation = daemon.store.get_metadata("artifacts", reference["observation_id"])
        target_path = Path(observation["artifact"]["file_path"])
        original_bytes = target_path.read_bytes()
        if damage == "rewrite":
            target_path.write_bytes(b'{"tampered":true}\n')
        elif damage == "missing":
            target_path.unlink()
        else:
            outside = tmp_path / "outside-observation.json"
            outside.write_bytes(original_bytes)
            target_path.unlink()
            target_path.symlink_to(outside)

        host = _public_experiment_host(daemon)
        case_result = asyncio.run(host.tools["experiment_case_result"](
            project_id=project_id, experiment_id=design["experiment_id"], case_id="case-0001",
        )).structuredContent
        inspected = asyncio.run(host.tools["experiment_inspect"](
            project_id=project_id, experiment_id=design["experiment_id"],
        )).structuredContent
        for response in (case_result, inspected):
            assert response["success"] is False, response
            assert response["error"]["code"] == "EXPERIMENT_STATE_UNKNOWN"
    finally:
        if target_path is not None and original_bytes is not None:
            if target_path.is_symlink() or target_path.exists():
                target_path.unlink()
            target_path.write_bytes(original_bytes)
        daemon.close()


@pytest.mark.parametrize("observation_role", ["case_sample", "metric_item"])
@pytest.mark.parametrize("metadata_swap", ["foreign_project_id", "foreign_workspace_path"])
def test_public_experiment_reads_reject_cross_project_observation_metadata(
    tmp_path, monkeypatch, observation_role, metadata_swap,
):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(
        tmp_path, monkeypatch, native_values=[3.25],
    )
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    try:
        _metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, execution["model_ref"], None, state, cases=(1.0,),
        )
        foreign_project, foreign_workspace = _project(daemon, "foreign-observation-project")
        case = run["cases"][0]
        evaluation = daemon.store.get_metadata("artifacts", case["metric_evaluation"]["evaluation_id"])
        reference = (
            evaluation["case_binding"]["sample_observation_ref"]
            if observation_role == "case_sample"
            else evaluation["items"][0]["observation_ref"]
        )
        observation_id = reference["observation_id"]
        observation = daemon.store.get_metadata("artifacts", observation_id)
        if metadata_swap == "foreign_project_id":
            observation["project_id"] = foreign_project
        else:
            observation["artifact"]["file_path"] = str(
                Path(foreign_workspace) / "observations" / f"{observation_id}.json"
            )
        daemon.store.db.execute(
            "UPDATE artifacts SET metadata=? WHERE artifact_id=?",
            (json.dumps(observation, sort_keys=True), observation_id),
        )
        daemon.store.db.commit()

        host = _public_experiment_host(daemon)
        case_result = asyncio.run(host.tools["experiment_case_result"](
            project_id=project_id, experiment_id=design["experiment_id"], case_id="case-0001",
        )).structuredContent
        inspected = asyncio.run(host.tools["experiment_inspect"](
            project_id=project_id, experiment_id=design["experiment_id"],
        )).structuredContent
        for response in (case_result, inspected):
            assert response["success"] is False, response
            assert response["error"]["code"] == "EXPERIMENT_STATE_UNKNOWN"
        foreign_read = asyncio.run(host.tools["experiment_case_result"](
            project_id=foreign_project, experiment_id=design["experiment_id"], case_id="case-0001",
        )).structuredContent
        assert foreign_read["success"] is False
        assert foreign_read["error"]["code"] == "EXPERIMENT_NOT_FOUND"
    finally:
        daemon.close()


@pytest.mark.parametrize("damage", ["rewrite", "missing", "symlink_escape"])
def test_public_experiment_reads_rehash_case_model_files(tmp_path, monkeypatch, damage):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(
        tmp_path, monkeypatch, native_values=[3.25],
    )
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    try:
        _metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, execution["model_ref"], None, state, cases=(1.0,),
        )
        row = run["cases"][0]["case_model_artifact"]
        target = worker.project_root / row["relative_path"]
        original = target.read_bytes()
        outside = tmp_path / "outside.mph"
        outside.write_bytes(original)
        if damage == "rewrite":
            target.write_bytes(original + b"tampered")
        elif damage == "missing":
            target.unlink()
        else:
            target.unlink()
            target.symlink_to(outside)

        host = _public_experiment_host(daemon)
        public_case = asyncio.run(host.tools["experiment_case_result"](
            project_id=project_id, experiment_id=design["experiment_id"], case_id="case-0001",
        )).structuredContent
        assert public_case["success"] is False, public_case
        assert public_case["error"]["code"] == "EXPERIMENT_STATE_UNKNOWN"
        public_inspect = asyncio.run(host.tools["experiment_inspect"](
            project_id=project_id, experiment_id=design["experiment_id"],
        )).structuredContent
        assert public_inspect["success"] is False, public_inspect
        assert public_inspect["error"]["code"] == "EXPERIMENT_STATE_UNKNOWN"
    finally:
        daemon.close()


@pytest.mark.parametrize("save_failure", ["api_error", "destination_race"])
def test_case_model_save_failure_is_not_reported_as_restorable(tmp_path, monkeypatch, save_failure):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(
        tmp_path, monkeypatch, native_values=[3.25],
    )
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    destination_created = []
    if save_failure == "api_error":
        def fail_save(*_args, **_kwargs):
            raise OSError("synthetic save failure")
        monkeypatch.setattr(_Model, "save", fail_save)
    else:
        original_save = _Model.save

        def race_save(model, path, save_copy=True, *, overwrite=True):
            Path(path).write_bytes(b"preexisting candidate")
            destination_created.append(Path(path))
            return original_save(model, path, save_copy, overwrite=overwrite)
        monkeypatch.setattr(_Model, "save", race_save)

    try:
        _metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, execution["model_ref"], None, state, cases=(1.0,),
        )
        host = _public_experiment_host(daemon)
        artifact = run["cases"][0]["case_model_artifact"]
        assert artifact["status"] == "SAVE_FAILED"
        assert artifact["restore_status"] == "NOT_AVAILABLE"
        response = asyncio.run(host.tools["experiment_case_result"](
            project_id=project_id, experiment_id=design["experiment_id"], case_id="case-0001",
        )).structuredContent
        assert response["success"] is True, response
        assert response["data"]["case_model_artifact"]["status"] == "SAVE_FAILED"
        assert response["data"]["case_model_artifact"]["restore_status"] == "NOT_AVAILABLE"
        if save_failure == "destination_race":
            assert destination_created and destination_created[0].read_bytes() == b"preexisting candidate"
    finally:
        daemon.close()


def test_experiment_metric_attempt_rejects_wrong_native_parameter_before_evaluation(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(tmp_path, monkeypatch)
    state = _install_synthetic_experiment_callbacks(monkeypatch, wrong_readback_case=2.0)
    try:
        _metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, execution["model_ref"], None, state,
        )
        assert run["status"] == "FAILED"
        assert run["cases"][0]["status"] == "COMPLETED"
        assert run["cases"][0]["metric_evaluation"]["evaluation_id"]
        second = run["cases"][1]
        assert second["status"] == "FAILED"
        assert "differs from the frozen case value" in second["error"]
        assert "metric_evaluation" not in second
        assert second["case_attempt_id"]
        assert state["solve_count"] == 1
        assert state["attempt_ids_seen_by_solve"] == [run["cases"][0]["case_attempt_id"]]
        evaluations = [row for row in daemon.store.list_metadata("artifacts")
                       if row.get("kind") == "w17_metric_evaluation"]
        assert len(evaluations) == 1
        assert not any(
            row.get("kind") == "w17_metric_evaluation"
            and row.get("case_binding", {}).get("case_id") == "case-0002"
            for row in evaluations
        )
    finally:
        daemon.close()


def test_experiment_attempt_ordinal_rejects_bool_even_with_recomputed_payload_hash(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(tmp_path, monkeypatch, native_values=[3.25])
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    try:
        _metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, execution["model_ref"], None, state, cases=(1.0,),
        )
        case = run["cases"][0]
        evaluation = daemon.store.get_metadata("artifacts", case["metric_evaluation"]["evaluation_id"])
        binding = dict(evaluation["case_binding"])
        attempt_key = f"w21experimentattempt:{design['experiment_id']}:case-0001"
        attempt = daemon.store.get_metadata("artifacts", attempt_key)
        attempt["case_ordinal"] = True
        attempt["sha256"] = _w21_execution.digest({key: value for key, value in attempt.items() if key != "sha256"})
        binding["case_ordinal"] = True
        binding["attempt_sha256"] = attempt["sha256"]
        daemon.store.db.execute(
            "UPDATE artifacts SET metadata=? WHERE artifact_id=?",
            (json.dumps(attempt, sort_keys=True), attempt_key),
        )
        daemon.store.db.commit()
        context = {
            "store": daemon.store,
            "project_id": project_id,
            "model_ref": run["model_ref"],
            "revision": run["model_revision"],
            "producer": run["producer"],
        }
        with pytest.raises(PreWriteRefusal, match="durable experiment attempt"):
            _w21_execution._verify_experiment_case_attempt(
                context,
                f"{design['experiment_id']}:case-0001",
                design["study"],
                case["parameters"],
                design["definition"]["units"],
                design["metric_definition_snapshots"],
                binding,
            )
    finally:
        daemon.close()


def test_experiment_metric_rejects_solution_identity_drift_and_wrong_native_solution_pair(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, _host, native_calls = _setup(tmp_path, monkeypatch, native_values=[3.25])
    state = _install_synthetic_experiment_callbacks(monkeypatch, identity_drift_call=3)
    try:
        _metric_ref, _design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, execution["model_ref"], None, state, cases=(1.0,),
        )
        assert run["cases"][0]["status"] == "FAILED"
        assert run["cases"][0]["error"] == "native solution identity changed before metric evaluation"
        assert "metric_evaluation" not in run["cases"][0]
        assert native_calls == []
        assert not [row for row in daemon.store.list_metadata("artifacts")
                    if row.get("kind") == "w17_metric_evaluation"]
    finally:
        daemon.close()

    (tmp_path / "wrong-native-solution").mkdir()
    daemon2, worker2, project_id2, execution2, _host2, native_calls2 = _setup(
        tmp_path / "wrong-native-solution", monkeypatch, native_values=[3.25],
    )
    state2 = _install_synthetic_experiment_callbacks(monkeypatch)
    original_evaluate = _g3_results.result_evaluate

    def wrong_solution_evaluate(*args, **kwargs):
        result = original_evaluate(*args, **kwargs)
        result["solution"] = "sol-other"
        return result

    monkeypatch.setattr(_g3_results, "result_evaluate", wrong_solution_evaluate)
    try:
        _metric_ref, _design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon2, worker2, project_id2, execution2["model_ref"], None, state2, cases=(1.0,),
        )
        assert run["cases"][0]["status"] == "FAILED"
        assert "exact solution" in run["cases"][0]["error"]
        assert "metric_evaluation" not in run["cases"][0]
        assert len(native_calls2) == 1
        assert not [row for row in daemon2.store.list_metadata("artifacts")
                    if row.get("kind") == "w17_metric_evaluation"]
    finally:
        daemon2.close()


def test_experiment_metric_failed_solve_does_not_create_success_association(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(tmp_path, monkeypatch, native_values=[3.25])
    model_ref = execution["model_ref"]
    state = _install_synthetic_experiment_callbacks(monkeypatch, fail_solve=True)
    try:
        _metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, model_ref, None, state, cases=(1.0,),
        )
        assert run["status"] == "FAILED"
        assert run["cases"][0]["status"] == "FAILED"
        assert "metric_evaluation" not in run["cases"][0]
        attempt_key = f"w21experimentattempt:{design['experiment_id']}:case-0001"
        assert daemon.store.get_metadata("artifacts", attempt_key)["attempt_id"] == run["cases"][0]["case_attempt_id"]
        assert not [row for row in daemon.store.list_metadata("artifacts")
                    if row.get("kind") == "w17_metric_evaluation"]
    finally:
        daemon.close()


def test_removed_metric_head_blocks_new_frozen_run_before_attempt_or_solve(tmp_path, monkeypatch):
    daemon, worker, project_id, execution, _host, _native_calls = _setup(tmp_path, monkeypatch, native_values=[3.25])
    model_ref = execution["model_ref"]
    state = _install_synthetic_experiment_callbacks(monkeypatch)
    try:
        metric_ref, design, run, _run_operation, _run_job = _run_metric_experiment(
            daemon, worker, project_id, model_ref, None, state, cases=(1.0,), run_experiment=False,
        )
        remove_args = {"metric_id": metric_ref["metric_id"]}
        remove_operation, remove_job = _begin_callback_operation(
            daemon, project_id, model_ref, design["model_revision"], "metric.remove", remove_args,
        )
        with observation_context(daemon.store, model_ref, design["model_revision"], remove_operation,
                                project_id=project_id):
            removed = _g3_metrics.metric_remove(worker, "model", remove_args)
        daemon.store.update_job(remove_job, "SUCCEEDED", result={"success": True, "data": removed})
        assert removed["active"] is False
        run_args = {"experiment_id": design["experiment_id"]}
        run_operation, _run_job = _begin_callback_operation(
            daemon, project_id, model_ref, design["model_revision"], "experiment.run", run_args,
        )
        with observation_context(daemon.store, model_ref, design["model_revision"], run_operation,
                                project_id=project_id):
            with pytest.raises(ExecutionContractError, match="removed metric definitions"):
                _w21_execution.op_experiment_run(worker, "model", run_args)
        assert state["solve_count"] == 0
        assert daemon.store.get_metadata("artifacts", "w21experimentrun:" + design["experiment_id"]) is None
        assert daemon.store.get_metadata(
            "artifacts", f"w21experimentattempt:{design['experiment_id']}:case-0001"
        ) is None
    finally:
        daemon.close()
