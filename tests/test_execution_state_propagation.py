"""Structured engine-state propagation and physics discovery safety tests."""
from __future__ import annotations

import json

import pytest

from comsol_mcp import _java_worker, _physics_ops, _state
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._java_worker import JavaWorkerError
from comsol_mcp._state import ToolExecutionError


def _failed_worker_reply() -> dict[str, object]:
    return {
        "ok": False,
        "failure": {
            "code": "ENGINE_CALL_FAILED",
            "message": "Selection_is_not_editable",
            "execution_state_unknown": True,
        },
        # The old top-level envelope may still report a clean state.  The
        # nested Worker flag is authoritative for the propagation test.
        "execution": {"dirty": False},
    }


class _WorkerFailSelection:
    def isInheriting(self):
        return False

    def set(self, _entities):
        _java_worker._decode_reply(_failed_worker_reply(), object())


class _WorkerFailFeature:
    def __init__(self):
        self._selection = _WorkerFailSelection()

    def selection(self):
        return self._selection


class _WorkerFailFeatureCollection:
    def __init__(self):
        self._feature = _WorkerFailFeature()

    def __call__(self, _tag):
        return self._feature


class _WorkerFailPhysics:
    def __init__(self):
        self._features = _WorkerFailFeatureCollection()

    def feature(self, tag=None):
        return self._features if tag is None else self._features(tag)


class _WorkerFailComponent:
    def __init__(self):
        self._physics = _WorkerFailPhysics()

    def physics(self, _tag):
        return self._physics


class _WorkerFailModel:
    def __init__(self):
        self.java = type(
            "Java",
            (),
            {"component": lambda _self, _name: _WorkerFailComponent()},
        )()


def _run_wrapped_worker_failure() -> str:
    # This is the production wrapping path: the Java protocol error enters
    # _physics_ops, becomes ValueError from the original cause, and is then
    # converted into the legacy envelope by _state._run_tool.
    return _physics_ops._set_physics_selection(
        _WorkerFailModel(), "comp1", "c", "cfeq1", [1]
    )


def _capture_tool_result(monkeypatch, *, return_json: bool = False):
    captured: dict[str, object] = {}
    monkeypatch.setattr(_state, "_setup_logging", lambda: None)
    def result(tool, success, data=None, error=""):
        captured.update(tool=tool, success=success, data=data or {}, error=error)
        if return_json:
            return json.dumps({"success": success, "tool": tool, "data": data or {}, "error": error})
        return "envelope"
    monkeypatch.setattr(
        _state,
        "_tool_result",
        result,
    )
    return captured


def test_decode_reply_preserves_structured_worker_failure_and_message():
    reply = _failed_worker_reply()
    with pytest.raises(JavaWorkerError) as raised:
        _java_worker._decode_reply(reply, object())

    error = raised.value
    assert error.reply == reply
    assert error.failure == reply["failure"]
    assert error.execution_state_unknown is True
    assert json.loads(str(error)) == reply


def test_run_tool_collects_unknown_from_cause_without_parsing_error_text(monkeypatch):
    captured = _capture_tool_result(monkeypatch)
    assert _state._run_tool("selection_probe", _run_wrapped_worker_failure) == "envelope"
    assert captured["success"] is False
    assert captured["data"] == {"execution_state_unknown": True}

    # A JSON-looking ordinary message is not a structured signal and must not
    # become an unknown-state result merely because its text contains a flag.
    ordinary = _state._run_tool(
        "ordinary_failure",
        lambda: (_ for _ in ()).throw(ValueError('{"execution_state_unknown": true}')),
    )
    assert ordinary == "envelope"
    assert captured["data"] == {}


def test_tool_execution_data_cannot_clear_true_cause_signal(monkeypatch):
    captured = _capture_tool_result(monkeypatch)

    def callback():
        try:
            _java_worker._decode_reply(_failed_worker_reply(), object())
        except JavaWorkerError as cause:
            raise ToolExecutionError(
                "structured failure",
                data={"execution_state_unknown": False, "local": "detail"},
            ) from cause
        raise AssertionError("the fixture reply must fail")

    _state._run_tool("structured_failure", callback)
    assert captured["data"] == {"execution_state_unknown": True, "local": "detail"}


class _Adapter:
    def __init__(self):
        self.counter = 0
        self.fingerprint = "fixture"

    def model_snapshot(self, tag):
        return {
            "model_tag": tag,
            "server_instance_id": "server",
            "external_event_counter": self.counter,
            "fingerprint": self.fingerprint,
        }


def _service(tmp_path):
    adapter = _Adapter()
    service = ExecutionService(SessionLedger("session", "server"), adapter, project_root=tmp_path)
    bound = service.bind_model("model")
    return service, adapter, model_ref_from_mapping(bound["execution"]["model_ref"])


def test_execution_service_marks_wrapped_unknown_dirty_and_blocks_retry(monkeypatch, tmp_path):
    captured = _capture_tool_result(monkeypatch, return_json=True)
    service, _adapter, ref = _service(tmp_path)
    result = service.execute_legacy(
        "set_parameters",
        lambda _args: _state._run_tool("selection_probe", _run_wrapped_worker_failure),
        {},
        model_ref=ref,
        expected_revision=0,
    )

    assert result["success"] is False
    assert result["data"]["execution_state_unknown"] is True
    assert result["execution"]["dirty"] is True
    assert result["execution"]["revision"] == 1
    assert captured["data"] == {"execution_state_unknown": True}

    with pytest.raises(ExecutionContractError, match="reconciliation"):
        service.execute_legacy(
            "set_parameters",
            lambda _args: {"success": True},
            {},
            model_ref=ref,
            expected_revision=1,
        )


def test_execution_service_keeps_plain_local_value_error_retryable(monkeypatch, tmp_path):
    captured = _capture_tool_result(monkeypatch, return_json=True)
    service, _adapter, ref = _service(tmp_path)
    result = service.execute_legacy(
        "set_parameters",
        lambda _args: _state._run_tool(
            "ordinary_failure",
            lambda: (_ for _ in ()).throw(ValueError("invalid local input")),
        ),
        {},
        model_ref=ref,
        expected_revision=0,
    )

    assert result["success"] is False
    assert result["data"]["execution_state_unknown"] is False
    assert result["execution"]["dirty"] is False
    assert result["execution"]["revision"] == 0
    assert captured["data"] == {}


class _Selection:
    def __init__(self, inherited: bool):
        self.inherited = inherited

    def isInheriting(self):
        return self.inherited


class _Feature:
    def __init__(self, selection):
        self._selection = selection

    def label(self):
        return "feature"

    def identifier(self):
        return "FluxBoundary"

    def properties(self):
        return ["selection"]

    def selection(self):
        return self._selection


class _FeatureWithoutInheritancePredicate(_Feature):
    class _NoPredicate:
        pass

    def __init__(self):
        pass

    def selection(self):
        return self._NoPredicate()


class _FeatureCollection:
    def __init__(self, rows):
        self.rows = rows

    def tags(self):
        return list(self.rows)

    def __call__(self, tag):
        return self.rows[tag]


class _Physics:
    def __init__(self, rows):
        self._features = _FeatureCollection(rows)

    def feature(self, tag=None):
        return self._features if tag is None else self._features(tag)


class _Component:
    def __init__(self, physics):
        self._physics = physics

    def physics(self, _tag):
        return self._physics


class _Model:
    def __init__(self, feature):
        self.java = type("Java", (), {"component": lambda _self, _name: _Component(_Physics({"cfeq1": feature}))})()


@pytest.mark.parametrize("inherited", [False, True])
def test_physics_feature_discovery_exposes_inherited_state_without_fake_editability(inherited):
    rows = _physics_ops._list_physics_features(_Model(_Feature(_Selection(inherited))), "comp1", "c")
    assert rows == [
        {
            "tag": "cfeq1",
            "label": "feature",
            "feature_type": "FluxBoundary",
            "properties": ["selection"],
            "selection_inheriting": inherited,
            "selection_editable": None,
        }
    ]


def test_physics_feature_discovery_is_unknown_when_inherit_predicate_is_unavailable():
    rows = _physics_ops._list_physics_features(
        _Model(_FeatureWithoutInheritancePredicate()), "comp1", "c"
    )
    assert rows[0]["selection_inheriting"] is None
    assert rows[0]["selection_editable"] is None
