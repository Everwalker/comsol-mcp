"""G3 R04: "the actions ran" is not the same claim as "the invariants hold".

The G2 review found that ``run_transaction`` stored ``invariants`` without ever
executing them and that ``_verify_transaction_record`` only re-read durable log
fields.  These tests pin the executable, typed invariant vocabulary
(``node_exists``/``node_type``/``property_equals``/``property_tolerance``/
``selection_non_empty``/``selection_dimension``):

* a required invariant that is unsupported or malformed is rejected *before*
  any action runs (write-time rejection),
* an optional unsupported invariant is NOT_RUN and never counts as a pass,
* after execution every declared invariant is evaluated against the live bound
  model and gets PASS/FAIL/NOT_RUN plus observed evidence,
* a required FAIL makes the whole transaction a non-success
  (``VERIFICATION_FAILED``) while the applied actions, the checkpoint and the
  recovery option are still returned,
* the durable record carries execution_status, verification_status,
  invariant_results, model_ref and revision, and ``transaction.verify`` labels
  durable-log checks (``durable_record``) separately from the apply-time live
  model-state checks, refusing a record bound to another model.

Selection readback is authoritative through ``com.comsol.model.Selection``:
``entities()``/``entities(int)`` were verified by javap on 2026-09-20 against
the installed COMSOL 6.4.0.293 apiplugins/com.comsol.api_1.0.0.jar.
"""
from __future__ import annotations

from contextlib import nullcontext
import zipfile

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_engine import evaluate_invariant, execute_transaction
from comsol_mcp._g2_transactions import TransactionStore, parse_invariants, run_transaction, validate_invariants
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore


class _Node:
    """Small live-model double: properties, children and selection entities."""

    def __init__(self, *, tag="node", type_id="Node", values=None, value_types=None,
                 children=None, entities_by_dim=None, label=None):
        self._tag, self.type_id = tag, type_id
        self._label = label
        self.values = dict(values or {})
        self.value_types = dict(value_types or {})
        self.children = {name: dict(items) for name, items in (children or {}).items()}
        self.entities_by_dim = {int(dim): list(items) for dim, items in (entities_by_dim or {}).items()}
        self.calls: list[tuple] = []

    def tag(self):
        return self._tag

    def label(self):
        return self._label or self._tag

    def getType(self):
        return self.type_id

    def properties(self):
        return list(self.values)

    def getValueType(self, name):
        return self.value_types.get(name)

    def getAllowedPropertyValues(self, _name):
        return None

    def getDouble(self, name):
        self.calls.append(("getDouble", name))
        return self.values[name]

    def getString(self, name):
        self.calls.append(("getString", name))
        return self.values[name]

    def set(self, name, typed):
        self.calls.append(("set", name, typed))
        self.values[name] = typed["data"]

    def entities(self, dimension=None):
        self.calls.append(("entities", dimension))
        if dimension is None:
            return [entity for dim in sorted(self.entities_by_dim) for entity in self.entities_by_dim[dim]]
        return list(self.entities_by_dim.get(int(dimension), []))

    def save(self, path):
        self.calls.append(("save", str(path)))
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("model/model.mphbin", b"checkpoint")

    def __getattr__(self, name):
        children = object.__getattribute__(self, "children")
        if name in children:
            return lambda tag: children[name][tag]
        raise AttributeError(name)


_FEATURE_PATH = {"segments": [{"collection": "component", "tag": "comp1"},
                              {"collection": "feature", "tag": "rect1"}]}
_SELECTION_PATH = {"segments": [{"collection": "component", "tag": "comp1"},
                                {"collection": "selection", "tag": "sel1"}]}


def _tree(*, height=2.0, entities_by_dim=None):
    rect = _Node(tag="rect1", type_id="Rectangle", values={"height": height},
                 value_types={"height": "Double"})
    sel = _Node(tag="sel1", type_id="BoxSelection",
                entities_by_dim={2: [1, 3]} if entities_by_dim is None else entities_by_dim)
    comp = _Node(tag="comp1", type_id="Component", children={"feature": {"rect1": rect},
                                                             "selection": {"sel1": sel}})
    return _Node(tag="main", type_id="Model", children={"component": {"comp1": comp}})


class _Client:
    def __init__(self, root):
        self.root = root

    def model(self, _tag):
        return self.root


class _Worker:
    def __init__(self, root):
        self.root = root

    @property
    def generation(self):
        return 3

    def client(self):
        return _Client(self.root)

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def backend_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server",
                "external_event_counter": 0, "fingerprint": "fingerprint"}


_ACTION = {"operation_id": "node.inspect", "arguments": {"path": {"segments": []}}}


def _decl(type_, path=None, **extra):
    return {"type": type_, "path": path if path is not None else _FEATURE_PATH, **extra}


# ---------------------------------------------------------------------------
# preflight: write-time rejection, optional NOT_RUN
# ---------------------------------------------------------------------------


def test_required_unsupported_invariant_is_rejected_before_any_action():
    ran: list[int] = []

    def runner(_operation, _arguments, index):
        ran.append(index)
        return {"success": True}

    with pytest.raises(ExecutionContractError) as exc:
        run_transaction([_ACTION], runner=runner, invariants=[_decl("model_untouched")])

    assert exc.value.code == "INVARIANT_UNSUPPORTED"
    assert ran == []


def test_required_malformed_invariants_are_rejected_before_any_action():
    ran: list[int] = []
    malformed = [
        {"type": "node_exists"},                                              # missing path
        {"type": "node_type", "path": _FEATURE_PATH},                          # missing type_id
        {"type": "property_tolerance", "path": _FEATURE_PATH, "name": "h", "value": "hot"},  # non-numeric
        {"type": "selection_dimension", "path": _SELECTION_PATH, "dimension": 9},            # out of range
        {"type": "node_exists", "path": {"segments": [{"accessor": "arbitrary"}]}},          # bad path
        {"type": "node_exists", "path": _FEATURE_PATH, "extra": 1},                          # unknown field
        "not-an-object",
    ]
    for declaration in malformed:
        with pytest.raises(ExecutionContractError) as exc:
            run_transaction([_ACTION], runner=lambda _o, _a, index: ran.append(index) or {"success": True},
                            invariants=[declaration])
        assert exc.value.code in {"INVALID_INVARIANT", "INVALID_NODE_PATH"}
    assert ran == []


def test_unsupported_type_is_not_a_supported_optional_either():
    rows = parse_invariants([_decl("scope_preserved")])
    assert rows[0]["supported"] is False


def test_optional_unsupported_invariant_is_not_run_and_actions_still_execute():
    ran: list[int] = []

    def runner(_operation, _arguments, index):
        ran.append(index)
        return {"success": True}

    record = run_transaction([_ACTION], runner=runner,
                             invariants=[{**_decl("model_untouched"), "required": False}])

    assert ran == [0]
    assert record.execution_status == "SUCCEEDED"
    assert record.verification_status == "PARTIAL"
    check = record.invariant_results["checks"][0]
    assert check["status"] == "NOT_RUN" and check["required"] is False
    assert record.invariant_results["counts"]["not_run"] == 1


def test_no_invariants_is_not_run_not_verified():
    record = run_transaction([_ACTION], runner=lambda _o, _a, _i: {"success": True})
    assert record.verification_status == "NOT_RUN"
    assert record.invariant_results["status"] == "NOT_RUN"


def test_validate_invariants_returns_parsed_rows_for_the_supported_vocabulary():
    rows = validate_invariants([
        _decl("node_exists"), _decl("node_type", type_id="Rectangle"),
        _decl("property_equals", name="height", value={"kind": "float64", "shape": [], "data": 2.0}),
        _decl("property_tolerance", name="height", value=2.0, tolerance=0.1),
        _decl("selection_non_empty", path=_SELECTION_PATH), _decl("selection_dimension", path=_SELECTION_PATH, dimension=2),
    ])
    assert [row["type"] for row in rows] == ["node_exists", "node_type", "property_equals",
                                            "property_tolerance", "selection_non_empty", "selection_dimension"]
    assert all(row["supported"] for row in rows)


# ---------------------------------------------------------------------------
# live evaluation through execute_transaction
# ---------------------------------------------------------------------------


def _run(root, invariants, *, runner=None, **kwargs):
    worker = _Worker(root)
    return execute_transaction(worker, "main", [_ACTION],
                               runner=runner or (lambda _o, _a, _i: {"success": True}),
                               invariants=invariants, model_ref={"model_tag": "main", "schema_version": 1},
                               revision=2, pre_revision=1, **kwargs)


def test_all_required_invariants_pass_is_verified():
    result = _run(_tree(), [
        _decl("node_exists"),
        _decl("node_type", type_id="Rectangle"),
        _decl("property_equals", name="height", value={"kind": "float64", "shape": [], "data": 2.0}),
        _decl("property_tolerance", name="height", value=2.0, tolerance=0.001),
        _decl("selection_non_empty", path=_SELECTION_PATH),
        _decl("selection_dimension", path=_SELECTION_PATH, dimension=2),
    ])

    assert result["success"] is True
    assert result["data"]["execution_status"] == "SUCCEEDED"
    assert result["data"]["verification_status"] == "VERIFIED"
    assert result["data"]["invariant_results"]["status"] == "VERIFIED"
    assert [check["status"] for check in result["data"]["invariant_results"]["checks"]] == ["PASS"] * 6
    assert result["error"] is None


def test_required_live_failure_makes_the_transaction_a_non_success_but_keeps_applied_rows():
    root = _tree(height=5.0)  # the model does not satisfy the declared invariant
    result = _run(root, [_decl("property_equals", name="height",
                               value={"kind": "float64", "shape": [], "data": 2.0})])

    assert result["success"] is False
    assert result["error"]["code"] == "VERIFICATION_FAILED"
    assert result["partial_change"] is True
    assert result["execution_state_unknown"] is False
    # The actions did apply and their evidence is still returned.
    assert [row["index"] for row in result["data"]["applied"]] == [0]
    check = result["data"]["invariant_results"]["checks"][0]
    assert check["status"] == "FAIL"
    assert check["observed"]["data"] == 5.0
    assert check["expected"]["data"] == 2.0


def test_executed_actions_that_change_the_model_are_re_read_not_echoed():
    root = _tree(height=2.0)
    worker = _Worker(root)

    class _Verifier:
        """Apply-time and later live reads must disagree after a second change."""

        def __init__(self):
            self.calls = 0

    def runner(_operation, _arguments, _index):
        root.children["component"]["comp1"].children["feature"]["rect1"].values["height"] = 4.0
        return {"success": True}

    result = execute_transaction(worker, "main", [_ACTION], runner=runner,
                                 invariants=[_decl("property_equals", name="height",
                                                   value={"kind": "float64", "shape": [], "data": 4.0})],
                                 model_ref={"model_tag": "main"}, revision=7)
    assert result["success"] is True
    assert result["data"]["invariant_results"]["checks"][0]["observed"]["data"] == 4.0
    assert result["data"]["invariant_results"]["model_ref"] == {"model_tag": "main"}
    assert result["data"]["invariant_results"]["revision"] == 7

    # A later live read of the same declaration sees the newest state, so the
    # verification is a live model read rather than a log echo.
    root.children["component"]["comp1"].children["feature"]["rect1"].values["height"] = 9.0
    later = evaluate_invariant(worker, "main", _decl("property_equals", name="height",
                                                     value={"kind": "float64", "shape": [], "data": 4.0}))
    assert later["status"] == "FAIL" and later["observed"]["data"] == 9.0


def test_node_exists_failure_and_type_mismatch():
    root = _tree()
    worker = _Worker(root)
    missing = evaluate_invariant(worker, "main", _decl("node_exists", {"segments": [
        {"collection": "component", "tag": "comp1"}, {"collection": "feature", "tag": "absent"}]}))
    assert missing["status"] == "FAIL" and missing["reason"] == "node_not_found"

    wrong_type = evaluate_invariant(worker, "main", _decl("node_type", type_id="Circle"))
    assert wrong_type["status"] == "FAIL"
    assert wrong_type["observed"]["type_id"] == "Rectangle"


def test_property_tolerance_boundary_and_non_numeric_rejection():
    worker = _Worker(_tree(height=2.0))
    inside = evaluate_invariant(worker, "main", _decl("property_tolerance", name="height", value=2.05, tolerance=0.1))
    assert inside["status"] == "PASS"
    outside = evaluate_invariant(worker, "main", _decl("property_tolerance", name="height", value=2.5, tolerance=0.1))
    assert outside["status"] == "FAIL"
    assert outside["observed"]["data"] == 2.0

    string_node = _tree()
    string_node.children["component"]["comp1"].children["feature"]["rect1"].values["name"] = "wide"
    string_node.children["component"]["comp1"].children["feature"]["rect1"].value_types["name"] = "String"
    not_numeric = evaluate_invariant(_Worker(string_node), "main",
                                     _decl("property_tolerance", name="name", value=1.0, tolerance=0.1))
    assert not_numeric["status"] == "FAIL" and not_numeric["reason"] == "property_not_numeric"


def test_selection_non_empty_and_dimension_readback():
    empty = _Worker(_tree(entities_by_dim={}))
    empty_result = evaluate_invariant(empty, "main", _decl("selection_non_empty", path=_SELECTION_PATH))
    assert empty_result["status"] == "FAIL" and empty_result["observed"]["entities"] == []

    wrong_dim = evaluate_invariant(_Worker(_tree(entities_by_dim={2: [1, 3]})), "main",
                                   _decl("selection_dimension", path=_SELECTION_PATH, dimension=1))
    assert wrong_dim["status"] == "FAIL"

    named = evaluate_invariant(_Worker(_tree()), "main",
                               _decl("selection_non_empty", path=_SELECTION_PATH, selection_name="other"))
    assert named["status"] == "FAIL" and named["reason"] == "selection_name_mismatch"


def test_required_evaluation_error_is_failure_and_optional_is_not_run():
    def broken_verifier(_declaration, _index):
        raise RuntimeError("engine readback exploded")

    required = run_transaction([_ACTION], runner=lambda _o, _a, _i: {"success": True},
                               invariants=[_decl("node_exists")], invariant_verifier=broken_verifier)
    assert required.verification_status == "FAILED"
    assert required.invariant_results["checks"][0]["status"] == "FAIL"
    assert "engine readback exploded" in required.invariant_results["checks"][0]["detail"]

    optional = run_transaction([_ACTION], runner=lambda _o, _a, _i: {"success": True},
                               invariants=[{**_decl("node_exists"), "required": False}],
                               invariant_verifier=broken_verifier)
    assert optional.verification_status == "PARTIAL"
    assert optional.invariant_results["checks"][0]["status"] == "NOT_RUN"


def test_optional_failure_is_partial_but_not_a_required_failure():
    result = _run(_tree(), [
        _decl("node_exists"),
        {**_decl("property_tolerance", name="height", value=9.0, tolerance=0.1), "required": False},
    ])
    assert result["success"] is True
    assert result["data"]["verification_status"] == "PARTIAL"
    assert result["data"]["invariant_results"]["status"] == "PARTIAL"
    assert result["error"] is None


def test_verification_is_not_run_when_the_actions_did_not_complete():
    result = _run(_tree(), [_decl("node_exists")],
                  runner=lambda _o, _a, _i: {"success": False, "error": {"code": "ENGINE_CALL_FAILED"}})
    assert result["success"] is False
    assert result["data"]["execution_status"] == "FAILED"
    assert result["data"]["verification_status"] == "NOT_RUN"
    assert result["data"]["invariant_results"]["reason"] == "actions did not complete"
    assert result["data"]["invariant_results"]["checks"] == []


def test_record_carries_binding_fields_and_survives_the_store():
    record = run_transaction([_ACTION], runner=lambda _o, _a, _i: {"success": True},
                             invariants=[_decl("node_exists")],
                             invariant_verifier=lambda _d, _i: {"status": "PASS", "observed": {}, "expected": {}},
                             model_ref={"model_tag": "main"}, revision=5, pre_revision=4)
    payload = record.as_dict()
    for field in ("execution_status", "verification_status", "invariant_results", "model_ref", "revision", "pre_revision"):
        assert field in payload, field
    assert payload["execution_status"] == "SUCCEEDED"
    assert payload["verification_status"] == "VERIFIED"
    assert payload["model_ref"] == {"model_tag": "main"} and payload["revision"] == 5


# ---------------------------------------------------------------------------
# managed transaction.apply / transaction.verify
# ---------------------------------------------------------------------------


class _SnapshotAdapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server",
                "external_event_counter": 0, "fingerprint": "fingerprint"}


@pytest.fixture
def backend_context(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    root = _tree(height=2.0)
    worker = _Worker(root)
    service = ExecutionService(SessionLedger("session", "server"), _SnapshotAdapter(), project_root=tmp_path)
    ref = service.bind_model("main")["execution"]["model_ref"]
    other = service.bind_model("scratch")["execution"]["model_ref"]
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    backend.project_root = tmp_path
    try:
        yield backend, worker, service, ref, other, root
    finally:
        backend.docs_index.close()
        store.close()


def _execution(ref, **extra):
    return {"session_id": "session", "model_ref": ref, "expected_revision": 0,
            "request_id": extra.pop("request_id", "txn-request"), "idempotency_key": extra.pop("idempotency_key", "txn-request"), **extra}


def _apply(backend, ref, root, *, invariants, checkpoint_policy="never", operation_id="txn-apply", mutate=True):
    def run_action(_operation, _model_tag, _arguments, _request_id):
        if mutate:
            root.children["component"]["comp1"].children["feature"]["rect1"].values["height"] = 3.0
        return {"success": True, "data": {"status": "SUCCEEDED"}}

    backend._run_g2_action = run_action
    return backend._invoke_g2_model(
        "transaction.apply",
        {"actions": [_ACTION], "invariants": invariants, "checkpoint_policy": checkpoint_policy},
        _execution(ref, request_id=operation_id, idempotency_key=operation_id),
        operation_id,
        lambda _event: None,
    )


def test_apply_binds_model_ref_revision_and_invariant_results_into_the_durable_record(backend_context, monkeypatch):
    backend, _worker, service, ref, _other, root = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    result = _apply(backend, ref, root, invariants=[
        _decl("node_exists"),
        _decl("property_equals", name="height", value={"kind": "float64", "shape": [], "data": 3.0}),
    ])

    assert result["success"] is True
    transaction_id = result["data"]["transaction_id"]
    durable = backend.transactions.get(transaction_id)
    assert durable is not None
    assert durable["model_ref"] == ref
    assert durable["revision"] == service.ledger._state_for(_ref_obj(service, ref)).revision
    assert durable["pre_revision"] == 0
    assert durable["execution_status"] == "SUCCEEDED"
    assert durable["verification_status"] == "VERIFIED"
    assert durable["invariant_results"]["model_ref"] == ref
    assert durable["invariant_results"]["revision"] == durable["revision"]
    assert [check["status"] for check in durable["invariant_results"]["checks"]] == ["PASS", "PASS"]


def _ref_obj(service, ref_mapping):
    from comsol_mcp._execution_contract import model_ref_from_mapping
    return model_ref_from_mapping(ref_mapping)


def test_apply_rejects_an_unsupported_required_invariant_before_any_write_or_checkpoint(backend_context, monkeypatch):
    backend, worker, _service, ref, _other, root = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    with pytest.raises(ExecutionContractError) as exc:
        _apply(backend, ref, root, invariants=[_decl("model_untouched")], checkpoint_policy="before")

    assert exc.value.code == "INVARIANT_UNSUPPORTED"
    assert root.children["component"]["comp1"].children["feature"]["rect1"].values["height"] == 2.0
    assert backend.store.list_metadata("checkpoints") == []
    assert not (backend.project_root / "g2_artifacts").exists()


def test_required_failure_keeps_checkpoint_and_recovery_option(backend_context, monkeypatch):
    backend, _worker, _service, ref, _other, root = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    result = _apply(backend, ref, root, checkpoint_policy="before", invariants=[
        _decl("property_equals", name="height", value={"kind": "float64", "shape": [], "data": 99.0}),
    ])

    assert result["success"] is False
    assert result["error"]["code"] == "VERIFICATION_FAILED"
    assert result["data"]["execution_status"] == "SUCCEEDED"
    assert result["data"]["verification_status"] == "FAILED"
    # The modification, its before-checkpoint and the recovery option survive.
    assert [row["index"] for row in result["data"]["applied"]] == [0]
    assert result["data"]["checkpoint_id"]
    assert result["data"]["checkpoint_metadata"]["sha256"]
    durable = backend.transactions.get(result["data"]["transaction_id"])
    assert durable["checkpoint_id"] == result["data"]["checkpoint_id"]
    assert durable["verification_status"] == "FAILED"


def test_transaction_verify_rejects_a_record_bound_to_another_model(backend_context, monkeypatch):
    backend, _worker, _service, ref, other, root = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})
    applied = _apply(backend, ref, root, invariants=[_decl("node_exists")])
    transaction_id = applied["data"]["transaction_id"]

    with pytest.raises(ExecutionContractError) as exc:
        backend._invoke_g2_model(
            "transaction.verify",
            {"transaction_id": transaction_id, "checks": []},
            _execution(other, request_id="verify-cross", idempotency_key="verify-cross"),
            "verify-cross",
            lambda _event: None,
        )
    assert exc.value.code == "MODEL_IDENTITY_MISMATCH"


def test_transaction_verify_labels_record_scope_and_model_state_scope(backend_context, monkeypatch):
    backend, _worker, service, ref, _other, root = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})
    applied = _apply(backend, ref, root, invariants=[
        _decl("property_equals", name="height", value={"kind": "float64", "shape": [], "data": 3.0}),
    ])
    transaction_id = applied["data"]["transaction_id"]

    result = backend._invoke_g2_model(
        "transaction.verify",
        {"transaction_id": transaction_id, "checks": [
            {"field": "verification_status", "equals": "VERIFIED"},
            {"field": "model_ref.model_tag", "equals": "main"},
        ]},
        _execution(ref, request_id="verify-scope", idempotency_key="verify-scope"),
        "verify-scope",
        lambda _event: None,
    )

    assert result["success"] is True
    data = result["data"]
    assert data["scope"] == "durable_record"
    assert [check["scope"] for check in data["checks"]] == ["durable_record", "durable_record"]
    assert data["status"] == "VERIFIED"
    live = data["live_model_state"]
    assert live["scope"] == "model_state"
    assert live["source"] == "transaction.apply invariant_results"
    assert live["status"] == "VERIFIED"
    assert live["model_ref"] == ref and live["revision"] == data["revision"]
    assert "not a live re-read" in live["note"]


def test_expired_record_check_passes_while_a_live_re_read_proves_the_change(backend_context, monkeypatch):
    backend, _worker, service, ref, _other, root = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})
    applied = _apply(backend, ref, root, invariants=[
        _decl("property_equals", name="height", value={"kind": "float64", "shape": [], "data": 3.0}),
    ])
    transaction_id = applied["data"]["transaction_id"]

    # The live model moves on after the transaction was verified.
    root.children["component"]["comp1"].children["feature"]["rect1"].values["height"] = 8.0
    verified = backend._invoke_g2_model(
        "transaction.verify",
        {"transaction_id": transaction_id, "checks": [{"field": "verification_status", "equals": "VERIFIED"}]},
        _execution(ref, request_id="verify-late", idempotency_key="verify-late"),
        "verify-late",
        lambda _event: None,
    )
    # The durable log still says VERIFIED (that is what a log check proves)...
    assert verified["data"]["status"] == "VERIFIED"
    assert verified["data"]["live_model_state"]["status"] == "VERIFIED"
    # ...while an explicit live re-read of the same declaration now FAILs.
    live = evaluate_invariant(backend.worker, ref["model_tag"], _decl(
        "property_equals", name="height", value={"kind": "float64", "shape": [], "data": 3.0}))
    assert live["status"] == "FAIL" and live["observed"]["data"] == 8.0


def test_corrupt_transaction_store_blocks_the_durable_write(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "operations.sqlite3")
    path = tmp_path / "transactions.json"
    path.write_bytes(b"{broken")
    root = _tree()
    worker = _Worker(root)
    service = ExecutionService(SessionLedger("session", "server"), _SnapshotAdapter(), project_root=tmp_path)
    ref = service.bind_model("main")["execution"]["model_ref"]
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    backend.project_root = tmp_path
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})
    try:
        assert isinstance(backend.transactions, TransactionStore)
        with pytest.raises(ExecutionContractError) as exc:
            _apply(backend, ref, root, invariants=[])
        assert exc.value.code == "STORE_CORRUPT"
        assert path.read_bytes() == b"{broken"
    finally:
        backend.docs_index.close()
        store.close()
