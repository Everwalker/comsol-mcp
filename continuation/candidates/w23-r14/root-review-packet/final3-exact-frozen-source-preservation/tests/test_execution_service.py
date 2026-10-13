import pytest

from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService


class Adapter:
    def __init__(self):
        self.counter = 0
        self.fingerprint = "one"

    def model_snapshot(self, _tag):
        return {"model_tag": _tag, "server_instance_id": "srv", "external_event_counter": self.counter, "fingerprint": self.fingerprint}


def service(tmp_path):
    adapter = Adapter()
    value = ExecutionService(SessionLedger("session", "srv"), adapter, project_root=tmp_path)
    ref = value.bind_model("model")
    return value, adapter, ref["execution"]["model_ref"]


def _ref(mapping):
    from comsol_mcp._execution_contract import model_ref_from_mapping
    return model_ref_from_mapping(mapping)


def test_service_conflict_prevents_callback_before_write(tmp_path):
    value, adapter, mapping = service(tmp_path)
    called = []
    adapter.counter = 1
    adapter.fingerprint = "desktop-change"
    with pytest.raises(ExecutionContractError, match="external"):
        value.execute_legacy("set_parameters", lambda args: called.append(args), {}, model_ref=_ref(mapping), expected_revision=0)
    assert called == []


def test_service_reconcile_invalidates_old_revision_then_executes(tmp_path):
    value, adapter, mapping = service(tmp_path)
    ref = _ref(mapping)
    adapter.counter, adapter.fingerprint = 1, "desktop-change"
    reconciled = value.reconcile(ref)
    assert reconciled["execution"]["revision"] == 1
    with pytest.raises(ExecutionContractError, match="expected_revision"):
        value.execute_legacy("set_parameters", lambda args: args, {}, model_ref=ref, expected_revision=0)
    result = value.execute_legacy("set_parameters", lambda args: {"success": True, "data": {"ok": args == {"x": 1}}}, {"x": 1}, model_ref=ref, expected_revision=1)
    assert result["data"] == {"ok": True}
    assert result["execution"]["revision"] == 2


def test_service_paths_are_canonicalized_before_callback(tmp_path):
    value, _adapter, mapping = service(tmp_path)
    seen = {}
    value.execute_legacy("save_model", lambda args: (seen.update(args) or {"success": True}), {"path": "output/a.mph"}, model_ref=_ref(mapping), expected_revision=0, path_parameters=("path",))
    assert seen["path"].startswith(str(tmp_path))


def test_service_unbound_create_is_permitted_but_unbound_write_is_not(tmp_path):
    value, _adapter, _mapping = service(tmp_path)
    assert value.execute_legacy("model_create", lambda args: {"success": True, "data": "created"})["data"] == "created"
    with pytest.raises(ExecutionContractError, match="selected model_ref"):
        value.execute_legacy("set_parameters", lambda args: args)


def test_service_rejects_adapter_snapshot_for_a_different_selected_model(tmp_path):
    value, adapter, mapping = service(tmp_path)
    original = adapter.model_snapshot
    adapter.model_snapshot = lambda tag: {**original(tag), "model_tag": "another-model"}
    with pytest.raises(ExecutionContractError, match="different model tag"):
        value.execute_legacy("get_parameters", lambda args: args, model_ref=_ref(mapping))


def test_false_legacy_envelope_is_not_promoted_to_success(tmp_path):
    value, _adapter, mapping = service(tmp_path)
    result = value.execute_legacy("set_parameters", lambda args: '{"success": false, "status": "partial", "error": "cleanup failed"}', {}, model_ref=_ref(mapping), expected_revision=0)
    assert result["success"] is False
    assert result["data"]["partial_change"] is True


def test_post_snapshot_failure_releases_ticket_and_marks_unknown(tmp_path):
    value, adapter, mapping = service(tmp_path)
    calls = 0
    original = adapter.model_snapshot
    def unstable(tag):
        nonlocal calls
        calls += 1
        if calls > 1: raise RuntimeError("worker lost")
        return original(tag)
    adapter.model_snapshot = unstable
    with pytest.raises(ExecutionContractError, match="post-execution"):
        value.execute_legacy("set_parameters", lambda args: {"success": True, "data": args}, {}, model_ref=_ref(mapping), expected_revision=0)
    # The ticket was released; the dirty state blocks a fresh write rather than
    # reporting ENGINE_BUSY forever.
    with pytest.raises(ExecutionContractError, match="external|fingerprint"):
        value.ledger.begin_write("set_parameters", {}, _ref(mapping), 1, fingerprint="one")
