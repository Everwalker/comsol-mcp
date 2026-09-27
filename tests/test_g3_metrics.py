from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path

import pytest

from comsol_mcp import _g3_ops
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_registry import validate_call
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._metric_contract import definition_sha256
from comsol_mcp._g2_tools import register as register_g2
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
    def sol(self, _tag):
        return _Solution()

    def study(self, _tag):
        return _Study()


class _Worker:
    generation = 1

    def __init__(self, project_root):
        self.project_root = Path(project_root)
        self.requests = []

    def client(self):
        return self

    def model(self, _tag):
        return _Model()

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


def _setup(tmp_path, monkeypatch, *, native_values=None, native_complex_mode="real", native_is_complex=False):
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
        return {
            "status": {"ok": True, "status": "APPLIED"},
            "cleanup": {"cleanup_failed": False},
            "expressions": ["T"], "dataset": "dset1", "solution": "sol1",
            "aggregate": "integral", "complex_mode": native_complex_mode, "is_complex": native_is_complex,
            "values": [value],
            "field_array": {
                "values": [[[[value]]]], "axes": ["expression", "outer", "inner", "point"],
                "shape": [1, 1, 1, 1], "coords": {"outer": [1], "inner": [1], "point": [1]},
                "units": {"expression": "K"}, "metadata": {}, "is_complex": native_is_complex,
            },
            "expression_units": {"T": "K"},
            "strict_metric_evidence": {
                "status": "VERIFIED",
                "selection_source": "actual_transient_numerical_feature_readback",
                "selection_membership_identical": True,
                "selection_features": [{"role": "primary", "entities": [1, 2]}],
                "selected_solution_pairs": [{"outer": 1, "inner": 1, "solnum": 1}],
                "expression_unit_readback": {"T": "K"},
                "requested_solution": {"dataset": "dset1", "solution": "sol1"},
            },
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
