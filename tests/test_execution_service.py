import hashlib

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService


class Adapter:
    def __init__(self):
        self.counter = 0
        self.fingerprint = "one"
        self.fingerprint_components = None

    def model_snapshot(self, _tag):
        result = {"model_tag": _tag, "server_instance_id": "srv", "external_event_counter": self.counter, "fingerprint": self.fingerprint}
        if self.fingerprint_components is not None:
            result["fingerprint_components"] = self.fingerprint_components
        return result


def service(tmp_path, adapter=None):
    adapter = adapter or Adapter()
    value = ExecutionService(SessionLedger("session", "srv"), adapter, project_root=tmp_path)
    ref = value.bind_model("model")
    return value, adapter, ref["execution"]["model_ref"]


def _ref(mapping):
    from comsol_mcp._execution_contract import model_ref_from_mapping
    return model_ref_from_mapping(mapping)


def _valid_v1_components():
    components = {
        "schema_version": 1,
        "tag": "model",
        "label": "Fixture model",
        "file_path": "/tmp/fixture.mph",
        "time_modified_epoch_ms": 123,
        "parameters": [{"name": "alpha", "expression": "1"}],
    }
    source = "model\x00Fixture model\x00/tmp/fixture.mph\x00123\x00alpha=1\x00"
    return components, hashlib.sha256(source.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("operation", ["inspect_evidence", "reconcile"])
@pytest.mark.parametrize("with_v1_components", [False, True])
def test_snapshot_coverage_reflects_validated_fingerprint_components(
        tmp_path, operation, with_v1_components):
    adapter = Adapter()
    if with_v1_components:
        adapter.fingerprint_components, adapter.fingerprint = _valid_v1_components()
    value, _adapter, mapping = service(tmp_path, adapter)
    result = getattr(value, operation)(_ref(mapping))
    snapshot = result["model_snapshot"]

    assert snapshot["coverage"] == (
        "tag+label+file_path+time_modified+parameters" if with_v1_components else "UNKNOWN"
    )
    assert snapshot["cas_guarantee"] == "NONE"
    assert snapshot["fingerprint_components_status"] == (
        "AVAILABLE_V1" if with_v1_components else "UNAVAILABLE_LEGACY"
    )
    assert ("fingerprint_components" in snapshot) is with_v1_components
    assert result["execution"]["revision"] == 0
    assert result["execution"]["dirty"] is False


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
