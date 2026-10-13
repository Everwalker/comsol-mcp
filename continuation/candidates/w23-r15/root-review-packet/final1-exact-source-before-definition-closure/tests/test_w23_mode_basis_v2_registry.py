"""Managed registry seam for W23 v2 (all engine surfaces are synthetic)."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from comsol_mcp import _g2_registry, _g3_common, _g3_ops
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._w23_basis_v2_results import OPERATION_ID, build_definition
from tests.test_w23_mode_basis_v2_native_plan import _request
from tests.test_w23_mode_basis_v2_results import _install_native_test_doubles
from tests.test_w23_native_results import _project_bound_model_ref
from tools.w23_mode_basis_v2 import build_two_mode_basis_request


def _managed_request(project_id, model_ref):
    base = _request()

    def bind(source):
        return {**source, "project_id": project_id, "model_ref": dict(model_ref),
                "model_revision": 0}

    modes = [{"mode_id": row["mode_id"], "mode_index": row["mode_index"],
              "source": bind(row["source"])} for row in base["basis_modes"]]
    request = build_two_mode_basis_request(
        basis_id=base["basis_id"], case=base["case"], project_id=project_id,
        model_ref=model_ref, model_revision=0, geometry_revision=base["geometry_revision"],
        frequency_hz=base["frequency_hz"], coordinate_frame=base["coordinate_frame"],
        output_surface=base["output_surface"], input_surface=base["input_surface"],
        signal_source=bind(base["signal_source"]), mode_sources=modes,
        incident_source=bind(base["incident_source"]), applicability=base["applicability"],
    )
    axes = [{"mode_id": row["mode_id"], "parameter": "lambda"}
            for row in request["basis_modes"]]
    return request, build_definition(request, axes)


class _Snapshot:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp",
                "external_event_counter": 0}


class _Model:
    java = SimpleNamespace(tag=lambda: "m")


class _Client:
    def model(self, tag):
        assert tag == "m"
        return _Model()


class _Worker:
    def client(self):
        return _Client()

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()


def _daemon(tmp_path):
    from comsol_mcp._control_daemon import ControlDaemon

    service = ExecutionService(SessionLedger("session", "server"), _Snapshot(),
                               project_root=tmp_path)
    return ControlDaemon(tmp_path / "control", service=service, registry={}, worker=_Worker(),
                         project_root=tmp_path)


def _request_envelope(outer, definition, project, model_ref):
    execution = {
        "project_id": project["project_id"], "session_id": "session",
        "model_ref": model_ref, "expected_revision": 0,
        "idempotency_key": f"w23-v2-{outer}", "request_id": f"w23-v2-{outer}",
    }
    inner = {"definition": definition}
    if outer == "direct":
        return {"operation": OPERATION_ID, "arguments": inner, "execution": execution}
    return {"operation": outer,
            "arguments": {"operation_id": OPERATION_ID, "arguments": inner},
            "execution": execution}


def test_v2_operation_is_versioned_discoverable_and_keeps_evaluate_isolation():
    assert _g2_registry.is_implemented(OPERATION_ID)
    assert _g3_ops.OPERATION_ORIGINS[OPERATION_ID] == "_w23_basis_v2_results"
    assert _g3_ops.effect_of(OPERATION_ID) == "EVALUATE"
    assert _g3_ops.requires_isolation(OPERATION_ID) is True
    assert OPERATION_ID in _g3_ops.REQUIRES_ISOLATION

    entry = _g2_registry.registry_describe(OPERATION_ID)
    assert entry["mcp_tool_name"] == "result_mode_overlap_basis_v2"
    assert entry["effect"] == "EVALUATE"
    assert entry["input_schema"]["required"] == ["definition"]
    assert entry["input_schema"]["properties"]["definition"]["$id"].endswith("definition:1.0.0")
    assert entry["data_schema"]["$id"].endswith("native-result:1.0.0")
    assert "ordinary managed project_write permission, isolation, revision and callback gates apply" in entry["runtime_dispatch_contract"]["effect"]


@pytest.mark.parametrize("outer", ["direct", "registry_call", "operation_call"])
def test_v2_managed_entrypoints_share_normal_route_and_evaluate_ticket(tmp_path, monkeypatch, outer):
    from jsonschema import validate

    daemon = _daemon(tmp_path)
    try:
        project, model_ref = _project_bound_model_ref(daemon, f"v2-{outer}", monkeypatch)
        request, definition = _managed_request(project["project_id"], model_ref)
        calls = _install_native_test_doubles(monkeypatch, request)
        isolation_calls = []
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: isolation_calls.append(True) or {"status": "TEST_ONLY_RECEIPT"})

        response = daemon.dispatch(_request_envelope(outer, definition, project, model_ref))

        assert response["success"] is True, response
        assert response["data"]["operation_id"] == OPERATION_ID
        assert response["data"]["study_or_solver_invoked"] is False
        assert response["data"]["production_route_status"] == "ROUTE_REGISTERED_NATIVE_INTEGRATION_ONLY"
        assert response["data"]["native_result"] == "COMSOL_NATIVE_RAW"
        assert len(calls) == 16
        assert sum(call["term_id"] in {
            "G00", "G01", "G10", "G11", "b0", "b1", "P_signal", "P_incident",
        } for call in calls) == 8
        assert sum(call["term_id"].startswith("normal ") for call in calls) == 8
        assert len(isolation_calls) == 1
        assert response["execution"]["revision"] == 1
        assert response["execution"]["dirty"] is False
        validate(instance=response["data"], schema=_g2_registry.registry_describe(OPERATION_ID)["data_schema"])
    finally:
        daemon.close()


def test_v2_evaluate_without_owned_server_receipt_refuses_before_worker_native_dispatch(tmp_path, monkeypatch):
    daemon = _daemon(tmp_path)
    try:
        project, model_ref = _project_bound_model_ref(daemon, "v2-no-receipt", monkeypatch)
        request, definition = _managed_request(project["project_id"], model_ref)
        native_calls = _install_native_test_doubles(monkeypatch, request)
        bound_model_calls = []
        monkeypatch.setattr(_g3_common, "bound_model",
                            lambda *_args, **_kwargs: bound_model_calls.append(True) or {"model_tag": "m"})
        from comsol_mcp import _managed_backend
        monkeypatch.setattr(_managed_backend, "configured_receipt", lambda: None)
        gate_calls = []
        original_gate = daemon.backend._require_g2_isolation

        def checked_gate():
            gate_calls.append(True)
            return original_gate()

        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", checked_gate)
        response = daemon.dispatch(_request_envelope("direct", definition, project, model_ref))

        assert response["success"] is False
        assert response["error"]["code"] == "ISOLATION_PROOF_REQUIRED"
        assert gate_calls == [True]
        assert native_calls == []
        assert bound_model_calls == []
        state = daemon.service.ledger._state_for(model_ref_from_mapping(model_ref))
        assert state.revision == 0 and state.dirty is False
    finally:
        daemon.close()


def test_v2_registry_shape_is_strict_before_scheduling():
    from tests.test_w23_mode_basis_v2_results import _definition as make_definition

    _request_value, definition = make_definition()
    _g2_registry.validate_call(OPERATION_ID, {"definition": definition})
    with pytest.raises(ExecutionContractError):
        _g2_registry.validate_call(OPERATION_ID, {"definition": definition, "signal_arrays": []})
    changed = {**definition, "definition_sha256": "0" * 64}
    with pytest.raises(ExecutionContractError) as caught:
        _g2_registry.validate_call(OPERATION_ID, {"definition": changed})
    assert caught.value.code == "IDENTITY_DIGEST_MISMATCH"
