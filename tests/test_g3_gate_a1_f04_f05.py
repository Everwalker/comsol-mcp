"""Gate A1 regression tests: F04 (method mutation witness & signatures) and F05 (error signal escalation)."""
from __future__ import annotations

import pytest

from comsol_mcp._domain_outcome import (
    STATE_FAILED,
    STATE_PARTIAL,
    STATE_SUCCEEDED,
    STATE_UNKNOWN,
    classify_envelope,
    current_witness,
    is_mutation_call,
    is_mutation_method,
    record_engine_method,
    witness_scope,
)
from comsol_mcp._execution_contract import (
    ExecutionContractError,
    PreWriteRefusal,
    SessionLedger,
    model_ref_from_mapping,
)
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._java_worker import RemoteJava
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._mcp_gateway import mcp_result
from comsol_mcp._operation_store import OperationStore


class _Adapter:
    def __init__(self) -> None:
        self.counter = 0
        self.fingerprint = "fp-1"

    def model_snapshot(self, tag):
        return {
            "model_tag": tag,
            "server_instance_id": "server",
            "external_event_counter": self.counter,
            "fingerprint": self.fingerprint,
        }


class _DummyWorker:
    def __init__(self) -> None:
        self.generation = 1
        self.submitted: list[tuple[str, dict]] = []

    def submit(self, kind, payload, **kwargs):
        self.submitted.append((kind, dict(payload)))
        return {"ok": True, "status": "SUCCEEDED", "result": None}


# ===========================================================================
# F04: Method Mutation Witness and Signature Tests
# ===========================================================================


def test_f04_overloaded_methods_distinguish_getters_from_setters():
    """label(), active(), lengthUnit() are read with 0 args, mutations with 1+ args."""
    # 0 args -> getter (read-only)
    assert is_mutation_call("label", ()) is False
    assert is_mutation_call("active", ()) is False
    assert is_mutation_call("lengthUnit", ()) is False
    assert is_mutation_call("name", ()) is False
    assert is_mutation_call("comments", ()) is False

    # 1 arg -> setter (mutation)
    assert is_mutation_call("label", ("MyModel",)) is True
    assert is_mutation_call("active", (False,)) is True
    assert is_mutation_call("lengthUnit", ("mm",)) is True
    assert is_mutation_call("name", ("NewName",)) is True
    assert is_mutation_call("comments", ("Updated description",)) is True


def test_f04_selection_methods_are_recognized_as_mutations():
    """all(), named(), inherit() on selection are mutations, not pure accessors."""
    assert is_mutation_call("all", ()) is True
    assert is_mutation_call("named", ("sel1",)) is True
    assert is_mutation_call("inherit", (True,)) is True
    assert is_mutation_method("all") is True
    assert is_mutation_method("named") is True
    assert is_mutation_method("inherit") is True


def test_f04_geom_accessor_vs_selection_geom():
    """geom() navigation is read; selection.geom(dim) / selection.geom(dim, entities) is write."""
    assert is_mutation_call("geom", ()) is False
    assert is_mutation_call("geom", ("geom1",)) is False
    # Dimension index or dimension + entities specifies selection geometry
    assert is_mutation_call("geom", (2,)) is True
    assert is_mutation_call("geom", (2, [1, 2, 3])) is True


def test_f04_unknown_method_defaults_to_mutation_fail_closed():
    """Unknown methods must not be silently treated as read-only."""
    assert is_mutation_call("customDynamicEngineExtension", ()) is True
    assert is_mutation_method("arbitraryNonWhitelistedMethod") is True


def test_f04_trusted_java_and_modelutil_dispatches_are_witnessed():
    """code_execute and modelutil create/load/remove must be caught by witness."""
    assert is_mutation_call("run", command="code_execute") is True
    assert is_mutation_call("compile", command="code_compile") is False
    assert is_mutation_call("create", command="modelutil") is True
    assert is_mutation_call("load", command="modelutil") is True
    assert is_mutation_call("remove", command="modelutil") is True
    assert is_mutation_call("tags", command="modelutil") is False
    assert is_mutation_call("getComsolVersion", command="modelutil") is False


def test_f04_dispatch_witness_records_calls_and_detects_mutation():
    worker = _DummyWorker()
    node = RemoteJava(worker, "handle-1", worker.generation, "java.lang.Object")

    with witness_scope() as witness:
        # Pure getter: getType()
        node.getType()
        assert witness.mutation_issued is False
        assert len(witness.dispatches) == 1
        assert witness.dispatches[0]["is_mutation"] is False

        # Getter: label() with 0 args
        node.label()
        assert witness.mutation_issued is False

        # Selection mutation: all()
        node.all()
        assert witness.mutation_issued is True
        assert witness.first_mutation == "all"

        # Setter: label("new")
        node.label("new_label")
        assert witness.first_mutation == "all"  # first mutation retained

    witness_dict = witness.as_dict()
    assert witness_dict["engine_calls"] == 4
    assert witness_dict["mutation_method"] == "all"
    assert witness_dict["mutation_issued"] is True
    assert len(witness_dict["dispatches"]) == 4


def test_f04_setter_then_refusal_raises_execution_state_unknown(tmp_path, monkeypatch):
    """Calling a setter like label(str) or all() before raising PreWriteRefusal must be UNKNOWN."""
    adapter = _Adapter()
    service = ExecutionService(SessionLedger("session", "server"), adapter, project_root=tmp_path)
    bound = service.bind_model("model")
    ref = model_ref_from_mapping(bound["execution"]["model_ref"])
    backend = ManagedBackend(tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
                             service=service, worker=_DummyWorker(), registry={})

    def mutate_label_then_refuse():
        record_engine_method("label", "modified_name", command="call")
        raise PreWriteRefusal("VALIDATION_ERROR", "failed after label setter")

    monkeypatch.setattr("comsol_mcp._g3_ops.REQUIRES_ISOLATION", frozenset())
    from comsol_mcp import _g3_ops
    monkeypatch.setitem(_g3_ops.DISPATCH, "parameter.set", lambda _w, _m, _a: mutate_label_then_refuse())

    with pytest.raises(ExecutionContractError) as exc_info:
        backend._invoke_g3_model(
            "parameter.set", ref, {},
            {"session_id": "session", "model_ref": ref.as_dict(),
             "expected_revision": 0, "request_id": "req-1"},
            "req-1", "session",
        )

    assert exc_info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert exc_info.value.stage == "post_dispatch"
    assert exc_info.value.details["witness"]["mutation_method"] == "label"


# ===========================================================================
# F05: Error Signal Escalation and Conservative Merging Tests
# ===========================================================================


def test_f05_inner_execution_state_unknown_cannot_be_cleared_by_outer_false():
    """Inner execution_state_unknown=True must win over outer False / success=True."""
    inner = {
        "status": "APPLIED",
        "execution_state_unknown": True,
        "cleanup": {"cleanup_failed": False},
    }
    outer = {
        "success": True,
        "execution_state_unknown": False,
        "data": inner,
    }

    outcome = classify_envelope(outer)
    assert outcome.state == STATE_UNKNOWN
    assert outcome.execution_state_unknown is True
    assert outcome.failure is not None
    assert outcome.failure["code"] == "EXECUTION_STATE_UNKNOWN"

    res = mcp_result(outer | {"domain_outcome": outcome.as_dict()})
    # DomainOutcome state is unknown so envelope marks failure
    assert outcome.success is False


def test_f05_inner_cleanup_failed_cannot_be_cleared_by_outer_false():
    """Inner cleanup_failed=True must escalate to STATE_UNKNOWN even if outer reports cleanup_failed=False."""
    inner = {
        "status": "APPLIED",
        "cleanup": {"created": True, "cleanup_failed": True, "error": {"code": "CLEANUP_FAILED"}},
    }
    outer = {
        "success": True,
        "cleanup_failed": False,
        "data": inner,
    }

    outcome = classify_envelope(outer)
    assert outcome.state == STATE_UNKNOWN
    assert outcome.cleanup_failed is True
    assert outcome.failure["code"] == "CLEANUP_FAILED"


def test_f05_inner_partial_change_cannot_be_cleared_by_outer_false():
    """Inner partial_change=True must persist even if outer says partial_change=False."""
    inner = {
        "status": {"ok": False, "status": "PARTIAL_FAILURE"},
        "partial_change": True,
        "applied": [{"name": "k"}],
        "failed": [{"error": {"code": "SET_FAILED"}}],
    }
    outer = {
        "success": False,
        "partial_change": False,
        "data": inner,
    }

    outcome = classify_envelope(outer)
    assert outcome.state == STATE_PARTIAL
    assert outcome.applied_count == 1
    assert outcome.failed_count == 1


def test_f05_inner_status_unknown_token_persists_over_outer_applied():
    """Inner EXECUTION_STATE_UNKNOWN status token is not overwritten by outer APPLIED."""
    inner = {
        "status": "EXECUTION_STATE_UNKNOWN",
    }
    outer = {
        "status": "APPLIED",
        "success": True,
        "data": inner,
    }

    outcome = classify_envelope(outer)
    assert outcome.state == STATE_UNKNOWN
