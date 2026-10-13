"""Round 7-A offline regressions: the false EXECUTION_STATE_UNKNOWN source.

Live evidence (`evidence/phase4/runs/20260920T130620Z-g3-live/driver5c`):
``node.property_set`` refused a numeric property written as an unevaluated
expression with ``EXECUTION_STATE_UNKNOWN: legacy callback raised after write
dispatch`` even though the refusal happened inside the property contract, before
any setter call.  The unknown job then poisoned the control gate and refused
every later operation ("reconcile unfinished engine work before new operations"),
which is what blocked R01/R03, GUARD_T035/T038/T005 and W16_T020 in that run.

These tests pin the two halves of the fix:
* a refusal that provably happened before the engine dispatch keeps its own code
  and leaves the model, the revision and the gate untouched;
* every other callback failure keeps the fail-closed unknown state, but the
  original exception type/message is now part of the error envelope.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_contract import resolve_node_path
from comsol_mcp._managed_backend import ManagedBackend


class _Adapter:
    """A scope-limited engine fingerprint that never changes on its own."""

    def __init__(self, *, fail_snapshot: bool = False):
        self.counter = 0
        self.fingerprint = "fixture"
        self.fail_snapshot = fail_snapshot

    def model_snapshot(self, tag):
        if self.fail_snapshot:
            raise OSError(49, "Can't assign requested address")
        return {"model_tag": tag, "server_instance_id": "server",
                "external_event_counter": self.counter, "fingerprint": self.fingerprint}


def _service(tmp_path, **kwargs):
    adapter = _Adapter(**kwargs)
    service = ExecutionService(SessionLedger("session", "server"), adapter, project_root=tmp_path)
    bound = service.bind_model("model")
    from comsol_mcp._execution_contract import model_ref_from_mapping
    return service, adapter, model_ref_from_mapping(bound["execution"]["model_ref"])


def _ref():
    from comsol_mcp._execution_contract import ModelRef
    return ModelRef(session_id="session", server_instance_id="server", model_tag="model", generation=1)


def _g2_callback(raising: BaseException):
    """A ManagedBackend G2 callback whose action raises ``raising``."""
    backend = ManagedBackend.__new__(ManagedBackend)

    def action(*_args, **_kwargs):
        raise raising

    backend._run_g2_action = action  # type: ignore[method-assign]
    return ManagedBackend._g2_callback(backend, "node.property_set", _ref(), {}, "op-1", 0)


def test_declared_prewrite_refusal_is_a_clean_failure_not_an_unknown_job(tmp_path):
    """RED before the fix: the refusal came back as EXECUTION_STATE_UNKNOWN."""
    service, _adapter, ref = _service(tmp_path)
    refusal = PreWriteRefusal("PROPERTY_TYPE_MISMATCH", "property expects array rank 1, received 2")

    result = service.execute_legacy("set_parameters", _g2_callback(refusal), {}, model_ref=ref,
                                   expected_revision=0, request_id="phase4-r01-numeric-expression",
                                   effect="project_write")

    assert result["success"] is False
    assert result["error"]["code"] == "PROPERTY_TYPE_MISMATCH"
    assert result["error"]["message"] == "property expects array rank 1, received 2"
    # Nothing was written, so nothing may look unknown, dirty or advanced.
    assert result["data"]["execution_state_unknown"] is False
    assert result["data"]["status"] == "REFUSED"
    assert result["execution"]["revision"] == 0
    assert result["execution"]["dirty"] is False


def test_a_clean_refusal_does_not_poison_the_next_write(tmp_path):
    """The live gate refused every later call after one unknown job."""
    service, _adapter, ref = _service(tmp_path)
    service.execute_legacy("set_parameters",
                           _g2_callback(PreWriteRefusal("PROPERTY_TYPE_MISMATCH", "kind mismatch")),
                           {}, model_ref=ref, expected_revision=0, effect="project_write")

    follow_up = service.execute_legacy("set_parameters", lambda _args: {"success": True, "data": {}},
                                       {}, model_ref=ref, expected_revision=0, effect="project_write")

    assert follow_up["success"] is True
    assert follow_up["execution"]["revision"] == 1


def test_other_callback_failures_keep_the_unknown_state_and_record_their_cause(tmp_path):
    """EU stays fail-closed; only the swallowed cause is added to the evidence."""
    service, _adapter, ref = _service(tmp_path)

    with pytest.raises(ExecutionContractError) as info:
        service.execute_legacy("set_parameters", _g2_callback(TypeError("unsupported operand")),
                               {}, model_ref=ref, expected_revision=0, request_id="phase4-cause",
                               effect="project_write")

    error = info.value
    assert error.code == "EXECUTION_STATE_UNKNOWN"
    assert str(error) == "legacy callback raised after write dispatch"
    assert error.safe_retry is False
    assert error.details == {"cause_type": "TypeError", "cause_message": "unsupported operand"}
    assert error.as_dict()["details"]["cause_type"] == "TypeError"


def test_a_pre_dispatch_engine_failure_is_retryable_and_not_an_unknown_job(tmp_path):
    """An unreachable engine before dispatch has no state to reconcile."""
    service, adapter, ref = _service(tmp_path)
    adapter.fail_snapshot = True

    with pytest.raises(ExecutionContractError) as info:
        service.execute_legacy("set_parameters", lambda _args: {"success": True}, {},
                               model_ref=ref, expected_revision=0, effect="project_write")

    error = info.value
    assert error.code == "ENGINE_UNRESPONSIVE"
    assert error.safe_retry is True
    assert error.details["cause_type"] == "OSError"
    assert "Can't assign requested address" in error.details["cause_message"]


def test_a_missing_node_names_the_members_the_engine_reports():
    """The refusal is data-driven: it answers with the real tree, not a guess."""

    class _Container:
        def tags(self):
            return ["p4geom"]

    class _Component:
        def geom(self, tag=None):
            if tag is None:
                return _Container()
            raise RuntimeError(f"no such geometry {tag}")

    class _Model:
        def component(self, tag):
            return _Component()

    model = _Model()

    with pytest.raises(ExecutionContractError) as info:
        resolve_node_path(model, {"segments": [{"collection": "component", "tag": "comp1"},
                                              {"collection": "geom", "tag": "geom1"}]})

    assert info.value.code == "NODE_NOT_FOUND"
    assert "geom:geom1" in str(info.value)
    assert "p4geom" in str(info.value)


def test_a_documented_box_region_group_is_accepted_by_the_selection_validator():
    """RED before the fix: 'box' was refused as neither an assignment nor a property."""
    from comsol_mcp._g3_w13 import _split_selection_definition

    parsed = _split_selection_definition(
        {"box": {"xmin": 0.0, "xmax": 1.0, "ymin": -1.0, "ymax": 1.0}}, "Box"
    )

    assert parsed["properties"] == {"xmin": 0.0, "xmax": 1.0, "ymin": -1.0, "ymax": 1.0}


def test_a_region_group_for_another_selection_type_stays_refused():
    from comsol_mcp._g3_w13 import _split_selection_definition

    with pytest.raises(ExecutionContractError) as info:
        _split_selection_definition({"box": {"xmin": 0.0, "xmax": 1.0}}, "Disk")
    assert info.value.code == "INVALID_REQUEST"
    assert "Box selection" in str(info.value)

    with pytest.raises(ExecutionContractError) as unknown:
        _split_selection_definition({"box": {"xmin": 0.0, "radius": 1.0}}, "Box")
    assert unknown.value.code == "INVALID_REQUEST"
    assert "radius" in str(unknown.value)

    # A documented limit with a non-numeric value is still refused.
    with pytest.raises(ExecutionContractError) as typed:
        _split_selection_definition({"box": {"xmin": "0", "xmax": 1.0}}, "Box")
    assert typed.value.code == "INVALID_REQUEST"
    assert "must be a number" in str(typed.value)
