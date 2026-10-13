"""Native-route contracts for W23 (engine calls are replaced with typed evidence)."""
from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from comsol_mcp import _g2_registry, _g3_common, _g3_ops, _g3_results, _w23_results
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService


PHASOR = {
    "time_dependence": "exp(-i omega t)",
    "complex_field_representation": _w23_results.PHASOR_CONVENTION_ID,
}


def _source(prefix: str) -> dict[str, object]:
    return {"dataset_id": f"d_{prefix}", "solution_id": f"sol_{prefix}",
            "outer_index": 1, "inner_index": 1}


def _fields(prefix: str) -> dict[str, object]:
    return {
        "electric": {axis: f"{prefix}E{axis.upper()}" for axis in "xyz"},
        "magnetic": {axis: f"{prefix}H{axis.upper()}" for axis in "xyz"},
    }


def _definition() -> dict[str, object]:
    output = {"component": "comp1", "geometry": "geom1", "tag": "sel_out"}
    incident = {"component": "comp1", "geometry": "geom1", "tag": "sel_in"}
    return {
        "schema_version": "1.0.0",
        "phasor_convention": dict(PHASOR),
        "output_surface": {"plane_id": "plane-output", "selection": output, "normal_sign": 1},
        "signal": {"field_id": "field-signal", "source": _source("signal"), "fields": _fields("s")},
        "reference_mode": {"mode_id": "mode-1", "mode_axis_parameter": "modeIndex",
                           "source": _source("mode"), "fields": _fields("m")},
        "incident_reference": {"reference_id": "incident-1", "input_plane_id": "plane-input",
                               "selection": incident, "normal_sign": 1,
                               "source": _source("incident"), "fields": _fields("i")},
        "power_floor": {"value": 1e-12, "unit": "W/m"},
    }


def _capture_definition() -> dict[str, object]:
    definition = _definition()
    definition["capture"] = {
        "aperture_id": "receiver_core_aperture",
        "plane_id": "plane-output",
        "selection": {"component": "comp1", "geometry": "geom1", "tag": "sel_capture"},
        "normal_sign": 1,
        "incident_reference_id": "incident-1",
    }
    return definition


def _project_bound_model_ref(daemon, suffix: str, monkeypatch) -> tuple[dict[str, object], dict[str, object]]:
    """Create a real project row and bind the existing fake-Worker model to it."""
    # The backend persists project attribution alongside an attested Worker
    # identity. This is test identity only; the fake Worker never contacts COMSOL.
    daemon.backend.worker_identity = {
        "runtime_id": "test-runtime", "worker_instance_id": "test-worker",
        "connection_epoch": 1, "server_instance_id": "server",
    }
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": f"w23-{suffix}", "workspace": f"w23-{suffix}",
            "policy": {"permissions": ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": f"w23-create-{suffix}",
                      "idempotency_key": f"w23-create-{suffix}"},
    })
    assert created["success"] is True, created
    project = created["data"]["project"]
    import comsol_mcp._server as server
    monkeypatch.setattr(server, "_current_model", getattr(server, "_current_model", None), raising=False)
    adopted = daemon.dispatch({
        "operation": "model.adopt",
        "arguments": {"server_model_tag": "m"},
        "execution": {
            "project_id": project["project_id"], "session_id": "session",
            "request_id": f"w23-adopt-{suffix}", "idempotency_key": f"w23-adopt-{suffix}",
        },
    })
    assert adopted["success"] is True, adopted
    return project, adopted["execution"]["model_ref"]


def _install_native_evidence(monkeypatch, *, mode_frequency_hz=193.1e12,
                             cross_value=4 + 3j, cross_unit="W/m", cross_complex=True,
                             cleanup_removed=True, dataset_override=None,
                             capture_value=-0.5, capture_unit="W/m", capture_dimension=1):
    definition = _definition()
    calls = []
    monkeypatch.setattr(_g3_common, "bound_model", lambda _worker, _tag: object())

    def solution_info(_model, source, *, label):
        frequency = mode_frequency_hz if label == "reference_mode" else 193.1e12
        names = ["freq"] + (["modeIndex"] if label == "reference_mode" else [])
        values = [frequency] + ([1] if label == "reference_mode" else [])
        units = ["Hz"] + ([None] if label == "reference_mode" else [])
        return ({"binding_complete": True, "solution": source["solution_id"],
                 "component": "comp1", "geometry": "geom1"}, {
            "outer_index": source["outer_index"], "inner_index": source["inner_index"],
            "solnum": 1,
            "frequency": {"parameter": "freq", "value": frequency, "unit": "Hz", "frequency_hz": frequency},
            "parameter_names": names, "parameter_values": values, "parameter_units": units,
            "parameters_by_pair": {name: (value, unit) for name, value, unit in zip(names, values, units)},
        })

    def resolve_selection(_worker, model_tag, selection):
        return {"component": selection["component"], "geometry": selection["geometry"],
                "selection_tag": selection["tag"], "entities": [1, 2],
                "entity_dimension": capture_dimension if selection["tag"] == "sel_capture" else 1,
                "source": "test-native-readback"}

    monkeypatch.setattr(_w23_results, "_solution_info", solution_info)
    monkeypatch.setattr(_w23_results, "_space_dimension", lambda *_a, **_k: 2)
    monkeypatch.setattr(_g3_common, "resolve_selection_entities", resolve_selection)

    def result_evaluate(_worker, _tag, arguments):
        spec = arguments["spec"]
        expression = spec["expressions"][0]
        dataset = spec["solution"]["dataset"]
        selection_tag = spec["selection"]["tag"]
        is_cross = "setind(modeIndex,1)" in expression
        if selection_tag == "sel_capture":
            value, unit, is_complex = capture_value, capture_unit, False
        elif is_cross:
            value, unit, is_complex = cross_value, cross_unit, cross_complex
        else:
            value, unit, is_complex = 2.0, "W/m", False
        cleanup = {"created": True, "removed": cleanup_removed,
                   "cleanup_failed": not cleanup_removed,
                   "type_id": "IntLine", "tag": "w23_tmp"}
        calls.append({"spec": spec, "dataset": dataset, "expression": expression,
                      "selection_tag": selection_tag})
        return {
            "values": [[[[{"real": value.real, "imag": value.imag} if isinstance(value, complex) else value]]]],
            "is_complex": is_complex,
            "status": {"ok": cleanup_removed, "execution_state_unknown": not cleanup_removed,
                       "cleanup_failed": not cleanup_removed,
                       "engine_error": None if cleanup_removed else {"code": "REMOVE_FAILED", "message": "remove readback unavailable"}},
            "cleanup": cleanup,
            "field_array": {"units": {"expression": {expression: unit}}},
            "dataset": dataset_override or dataset,
            "solution": spec["solution"]["solution"],
            "solution_axes": {"pair_mapping_complete": True, "outer": [1], "inner": [1]},
            "selection_measure": 1.0,
        }

    monkeypatch.setattr(_g3_results, "result_evaluate", result_evaluate)
    return definition, calls


def test_public_definition_is_native_selector_only_and_closed():
    definition = _definition()
    assert _w23_results.validate_definition_shape(definition) == definition
    _g2_registry.validate_call("result.mode_overlap", {"definition": definition})
    with pytest.raises(ExecutionContractError, match="unsupported fields"):
        _w23_results.validate_definition_shape({**definition, "signal_arrays": []})
    with pytest.raises(ExecutionContractError, match="bare COMSOL variable"):
        invalid = _definition()
        invalid["signal"]["fields"]["electric"]["x"] = "real(Ex)"
        _w23_results.validate_definition_shape(invalid)
    with pytest.raises(ExecutionContractError, match="unsupported arguments"):
        _g2_registry.validate_call("result.mode_overlap", {"definition": definition, "signal_arrays": []})


def test_capture_definition_must_bind_same_output_plane_normal_and_incident_reference():
    definition = _capture_definition()
    assert _w23_results.validate_definition_shape(definition) == definition
    for key, value, error in (
        ("plane_id", "other-plane", "capture.plane_id"),
        ("incident_reference_id", "other-incident", "incident_reference_id"),
        ("normal_sign", -1, "same oriented"),
    ):
        invalid = _capture_definition()
        invalid["capture"][key] = value
        with pytest.raises(ExecutionContractError, match=error):
            _w23_results.validate_definition_shape(invalid)


def test_result_mode_overlap_runs_through_native_integral_adapter_and_preserves_complex(monkeypatch):
    definition, calls = _install_native_evidence(monkeypatch)
    result = _w23_results.result_mode_overlap(object(), "m", {"definition": definition})

    assert len(calls) == 4
    assert [call["dataset"] for call in calls] == ["d_signal", "d_mode", "d_signal", "d_incident"]
    assert all(call["spec"]["entity_dim"] == 1 for call in calls)
    assert all(call["spec"]["selection"]["entity_dimension"] == 1 for call in calls)
    assert all(call["spec"]["aggregate"] == "integral" for call in calls)
    assert "setind(modeIndex,1)" in calls[2]["expression"]
    assert "setval(freq,193100000000000[Hz])" in calls[2]["expression"]
    assert result["status"] == "SUCCEEDED"
    assert result["result_status"] == "COMPUTED_NATIVE_INTEGRALS"
    assert result["integrals"]["reciprocal_overlap_numerator"]["imag"] == 3.0
    assert result["overlap_amplitude"]["real"] == pytest.approx(0.5)
    assert result["overlap_amplitude"]["imag"] == pytest.approx(0.375)
    assert result["overlap_amplitude"]["unit"] == "1"
    assert result["normalized_overlap"] == pytest.approx(25 / 64)
    assert result["eta_mode"] == pytest.approx(25 / 64)
    assert result["eta_capture"]["status"] == "NOT_COMPUTED"
    assert result["evidence"]["independent_quadrature_benchmark"] == "NOT_RUN"
    assert result["evidence"]["physical_model_acceptance"] == "NOT_RUN"
    json.dumps(result, allow_nan=False)
    from jsonschema import validate
    validate(instance=result, schema=_w23_results.NATIVE_RESULT_SCHEMA)


def test_native_capture_flux_uses_separate_named_aperture_and_preserves_signed_ratio(monkeypatch):
    definition, calls = _install_native_evidence(monkeypatch, capture_value=-0.5)
    definition["capture"] = _capture_definition()["capture"]

    result = _w23_results.result_mode_overlap(object(), "m", {"definition": definition})

    capture_calls = [call for call in calls if call["selection_tag"] == "sel_capture"]
    assert len(calls) == 5
    assert len(capture_calls) == 1
    assert capture_calls[0]["dataset"] == "d_signal"
    assert capture_calls[0]["spec"]["selection"]["entity_dimension"] == 1
    assert "0.5*real((1)*" in capture_calls[0]["expression"]
    assert result["eta_capture"]["status"] == "COMPUTED_NATIVE_APERTURE_FLUX"
    assert result["eta_capture"]["value"] == pytest.approx(-0.25)
    assert result["eta_capture"]["signed_flux_preserved"] is True
    assert result["eta_capture"]["numerator"]["real"] == pytest.approx(-0.5)
    assert result["eta_capture"]["denominator"]["reference_id"] == "incident-1"
    assert result["eta_capture"]["region"]["selection"]["selection_tag"] == "sel_capture"
    assert result["integrals"]["capture_aperture_signal_flux"]["engine"]["cleanup"]["removed"] is True
    assert result["evidence"]["cleanup_verified"] is True
    json.dumps(result, allow_nan=False)
    from jsonschema import validate
    validate(instance=result, schema=_w23_results.NATIVE_RESULT_SCHEMA)


def test_native_capture_selection_dimension_is_verified_before_any_integral(monkeypatch):
    definition, calls = _install_native_evidence(monkeypatch, capture_dimension=2)
    definition["capture"] = _capture_definition()["capture"]
    with pytest.raises(ExecutionContractError, match="capture_surface entity dimension"):
        _w23_results.result_mode_overlap(object(), "m", {"definition": definition})
    assert calls == []


def test_native_capture_flux_unit_mismatch_is_rejected(monkeypatch):
    definition, _calls = _install_native_evidence(monkeypatch, capture_unit="W")
    definition["capture"] = _capture_definition()["capture"]
    with pytest.raises(ExecutionContractError, match="capture aperture signed signal flux native integrated unit"):
        _w23_results.result_mode_overlap(object(), "m", {"definition": definition})


@pytest.mark.parametrize("outer", ["direct", "registry_call", "operation_call"])
def test_managed_entrypoints_use_one_native_route_and_keep_evaluate_gates(tmp_path, monkeypatch, outer):
    definition, calls = _install_native_evidence(monkeypatch)

    class Snapshot:
        def model_snapshot(self, tag):
            return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp", "external_event_counter": 0}

    class Model:
        java = SimpleNamespace(tag=lambda: "m")

    class Client:
        def model(self, tag):
            assert tag == "m"
            return Model()

    class Worker:
        def client(self):
            return Client()

        def operation_context(self, *_args, **_kwargs):
            return nullcontext()

    service = ExecutionService(SessionLedger("session", "server"), Snapshot(), project_root=tmp_path)
    from comsol_mcp._control_daemon import ControlDaemon

    daemon = ControlDaemon(tmp_path / "control", service=service, registry={}, worker=Worker(), project_root=tmp_path)
    project, model_ref = _project_bound_model_ref(daemon, outer, monkeypatch)
    isolation_calls = []
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: isolation_calls.append(True) or {"status": "TEST_RECEIPT"})
    execution = {"project_id": project["project_id"], "session_id": "session",
                 "model_ref": model_ref, "expected_revision": 0,
                 "idempotency_key": f"key-{outer}", "request_id": f"request-{outer}"}
    inner = {"definition": definition}
    if outer == "direct":
        request = {"operation": "result.mode_overlap", "arguments": inner, "execution": execution}
    else:
        request = {"operation": outer, "arguments": {"operation_id": "result.mode_overlap", "arguments": inner},
                   "execution": execution}
    try:
        response = daemon.dispatch(request)
    finally:
        daemon.close()

    assert response["success"] is True, response
    assert response["data"]["result_status"] == "COMPUTED_NATIVE_INTEGRALS"
    assert len(calls) == 4
    assert _g3_ops.effect_of("result.mode_overlap") == "EVALUATE"
    assert _g3_ops.requires_isolation("result.mode_overlap") is True
    assert isolation_calls == [True]
    # EVALUATE keeps the standard project_write ticket semantics; only the
    # already-existing function.evaluate special case avoids revision advance.
    assert response["execution"]["revision"] == 1
    assert response["execution"]["dirty"] is False


def test_evaluate_routes_refuse_without_owned_server_receipt_before_native_dispatch(tmp_path, monkeypatch):
    definition, numerical_calls = _install_native_evidence(monkeypatch)

    class Snapshot:
        def model_snapshot(self, tag):
            return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp", "external_event_counter": 0}

    class Model:
        java = SimpleNamespace(tag=lambda: "m")

    class Client:
        def model(self, tag):
            return Model()

    class Worker:
        def client(self):
            return Client()

        def operation_context(self, *_args, **_kwargs):
            return nullcontext()

    from comsol_mcp import _managed_backend
    from comsol_mcp._control_daemon import ControlDaemon

    service = ExecutionService(SessionLedger("session", "server"), Snapshot(), project_root=tmp_path)
    daemon = ControlDaemon(tmp_path / "control", service=service, registry={}, worker=Worker(), project_root=tmp_path)
    project, model_ref = _project_bound_model_ref(daemon, "no-receipt", monkeypatch)
    monkeypatch.setattr(_managed_backend, "configured_receipt", lambda: None)
    evaluate_args = {"spec": {"expressions": ["emw.Ex"], "solution": {
        "dataset": "d_signal", "solution": "sol_signal", "outer": 1, "inner": 1,
    }, "aggregate": "integral", "entity_dim": 1}}
    execution = {"project_id": project["project_id"], "session_id": "session",
                 "model_ref": model_ref, "expected_revision": 0}
    try:
        overlap = daemon.dispatch({"operation": "result.mode_overlap", "arguments": {"definition": definition},
                                   "execution": {**execution, "idempotency_key": "no-receipt-overlap", "request_id": "no-receipt-overlap"}})
        evaluation = daemon.dispatch({"operation": "result.evaluate", "arguments": evaluate_args,
                                      "execution": {**execution, "idempotency_key": "no-receipt-eval", "request_id": "no-receipt-eval"}})
    finally:
        daemon.close()

    assert overlap["success"] is False and overlap["error"]["code"] == "ISOLATION_PROOF_REQUIRED"
    assert evaluation["success"] is False and evaluation["error"]["code"] == "ISOLATION_PROOF_REQUIRED"
    assert numerical_calls == []
    state = service.ledger._state_for(model_ref_from_mapping(model_ref))
    assert state.revision == 0 and state.dirty is False


def test_neighboring_result_evaluate_keeps_normal_evaluate_ticket_when_receipt_is_valid(tmp_path, monkeypatch):
    class Snapshot:
        def model_snapshot(self, tag):
            return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp", "external_event_counter": 0}

    class Model:
        java = SimpleNamespace(tag=lambda: "m")

    class Client:
        def model(self, tag):
            return Model()

    class Worker:
        def client(self):
            return Client()

        def operation_context(self, *_args, **_kwargs):
            return nullcontext()

    from comsol_mcp._control_daemon import ControlDaemon

    service = ExecutionService(SessionLedger("session", "server"), Snapshot(), project_root=tmp_path)
    daemon = ControlDaemon(tmp_path / "control", service=service, registry={}, worker=Worker(), project_root=tmp_path)
    project, model_ref = _project_bound_model_ref(daemon, "receipt-eval", monkeypatch)
    isolation_calls = []
    native_calls = []
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: isolation_calls.append(True) or {"status": "OWNED"})
    monkeypatch.setitem(_g3_ops.DISPATCH, "result.evaluate",
                        lambda *_args, **_kwargs: native_calls.append(True) or {"status": "SUCCEEDED", "result": 3})
    try:
        response = daemon.dispatch({"operation": "result.evaluate", "arguments": {"spec": {}},
                                    "execution": {"project_id": project["project_id"],
                                                  "session_id": "session", "model_ref": model_ref,
                                                  "expected_revision": 0, "idempotency_key": "receipt-eval",
                                                  "request_id": "receipt-eval"}})
    finally:
        daemon.close()
    assert response["success"] is True, response
    assert native_calls == [True]
    assert isolation_calls == [True]
    assert response["execution"]["revision"] == 1
    assert response["execution"]["dirty"] is False


def test_frequency_mismatch_fails_before_any_numerical_feature(monkeypatch):
    definition, calls = _install_native_evidence(monkeypatch, mode_frequency_hz=200e12)
    with pytest.raises(ExecutionContractError) as caught:
        _w23_results.result_mode_overlap(object(), "m", {"definition": definition})
    assert caught.value.code == "FREQUENCY_MISMATCH"
    assert calls == []


@pytest.mark.parametrize("kwargs,code", [
    ({"cross_unit": "W"}, "UNIT_MISMATCH"),
    ({"cross_complex": False}, "COMPLEX_DATA_ERROR"),
])
def test_overlap_cross_integral_must_be_native_complex_and_dimensionally_consistent(monkeypatch, kwargs, code):
    definition, _calls = _install_native_evidence(monkeypatch, **kwargs)
    with pytest.raises(ExecutionContractError) as caught:
        _w23_results.result_mode_overlap(object(), "m", {"definition": definition})
    assert caught.value.code == code


def test_unverified_integral_cleanup_preserves_unknown_and_raw_evidence(monkeypatch):
    source = _source("signal")
    selection = _definition()["output_surface"]["selection"]
    readback = {"component": "comp1", "geometry": "geom1", "selection_tag": "sel_out", "entity_dimension": 1}
    monkeypatch.setattr(_g3_results, "result_evaluate", lambda *_args, **_kwargs: {
        "status": {"ok": False, "execution_state_unknown": True,
                    "engine_error": {"code": "REMOVE_FAILED", "message": "node removal could not be read back"}},
        "cleanup": {"created": True, "removed": False, "cleanup_failed": True, "type_id": "IntLine"},
    })
    with pytest.raises(ExecutionContractError) as caught:
        _w23_results._native_integral(object(), "m", source, selection, readback, "expr", label="test integral")
    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert caught.value.stage == "post_dispatch"
    assert caught.value.details["cleanup"]["cleanup_failed"] is True
    assert caught.value.details["native_status"]["engine_error"]["code"] == "REMOVE_FAILED"
