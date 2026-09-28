from __future__ import annotations

import hashlib
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from comsol_mcp._execution_contract import (
    ExecutionContractError, SessionLedger, canonical_request_hash,
    model_ref_from_mapping,
)
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._stage_contract import sha256_json
from comsol_mcp._w21_native_output import (
    _EvidenceUnavailable, _check_tuple, _max_abs_pairwise_error,
    produce_stage_output_readback,
)
from comsol_mcp._w21_stage_backend import validate_output_readback


class _Adapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server-w21",
                "external_event_counter": 0, "fingerprint": "fixture-model"}


_SELECTION = {
    "kind": "all", "component": "comp1", "geometry": "geom1",
    "entity_dimension": 3,
}


def _solution(dataset: str, solution: str, *, time_value: float | None = None,
              ambiguous_time: bool = False) -> dict:
    rows = []
    for inner, value in ((1, 0.0), (2, 1.0), (3, 2.0)):
        if time_value is not None and inner == 3:
            value = time_value
        if ambiguous_time and inner == 3:
            value = 1.0
        rows.append({"outer": 1, "inner": inner, "solnum": inner})
    parameter_rows = {
        f"1:{inner}": {"solnum": inner, "names": ["t"], "values": [value], "units": ["s"]}
        for inner, value in ((1, 0.0), (2, 1.0), (3, 2.0))
    }
    if time_value is not None:
        parameter_rows["1:3"]["values"] = [time_value]
    if ambiguous_time:
        parameter_rows["1:3"]["values"] = [1.0]
    return {
        "dataset": dataset, "solution": solution,
        "binding_source": "SolutionInfo.getSolnum(outer, strict)",
        "binding_complete": True, "pair_mapping_complete": True,
        "solnum_pairs": rows, "outer_indices": [1], "inner_indices": [1, 2, 3],
        "parameters_complete": True,
        "parameters_source": "SolutionInfo.getPNames/getPvals/getUnits",
        "parameters": {"by_pair": parameter_rows},
    }


def _continuity(*, source_solution=None, target_solution=None):
    return {
        "kind": "continuity", "check_id": "temperature-boundary",
        "operator": "pointwise_max_abs", "source_variable": "T", "target_variable": "T",
        "source_solution": source_solution or {
            "dataset": "dset1", "solution": "sol1", "outer": "first",
            "time": [{"value": 1.0, "unit": "s"}],
        },
        "target_solution": target_solution or {
            "dataset": "dset2", "solution": "sol2", "outer": "first",
            "time": [{"value": 1.0, "unit": "s"}],
        },
        "source_selection": dict(_SELECTION), "target_selection": dict(_SELECTION),
        "frame": "spatial", "boundary_time": {"value": 1.0, "unit": "s"},
        "unit": "K", "tolerance": {"absolute": 0.1, "relative": 0.01},
    }


def _conservation(*, target_solution=None, mutate_terms=None):
    terms = [
        {"side": "source", "variable": "T", "coefficient": -1.0,
         "selection": dict(_SELECTION), "entity_dimension": 3},
        {"side": "target", "variable": "T", "coefficient": 1.0,
         "selection": dict(_SELECTION), "entity_dimension": 3},
    ]
    if mutate_terms:
        mutate_terms(terms)
    return {
        "kind": "conservation", "check_id": "temperature-balance",
        "operator": "native_integral", "quantity": "temperature",
        "source_solution": {
            "dataset": "dset1", "solution": "sol1", "outer": "first",
            "time": [{"value": 1.0, "unit": "s"}],
        },
        "target_solution": target_solution or {
            "dataset": "dset2", "solution": "sol2", "outer": "first",
            "time": [{"value": 1.0, "unit": "s"}],
        },
        "terms": terms, "unit": "K",
        "tolerance": {"absolute": 0.1, "relative": 0.01},
    }


@pytest.fixture
def project_root():
    root = Path("/Volumes/CTestEff/w21-native-output") / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _fixture(project_root, *, checks=None, evidence_mode="unverified", fault=None,
             ambiguous_time=False, metric_values=(12.0, 10.0)):
    root = Path(project_root)
    service = ExecutionService(SessionLedger("session-w21", "server-w21"), _Adapter(), project_root=root)
    bound = service.bind_model("model-main")
    ref = model_ref_from_mapping(bound["execution"]["model_ref"])
    state = service.ledger._state_for(ref)
    state.revision = 40
    project_id = "project-w21-fixture"
    context = ContextVar(f"w21-output-test-mode-{id(root)}", default=None)

    class Backend:
        def __init__(self):
            self.service = service
            self.worker = SimpleNamespace(paths=SimpleNamespace(resolved_project_root=root))
            self._stage_output_mode_context = context
            self.persisted = {}
            self.calls = []
            self.submitted_callbacks = []
            self.sent = []
            self.binding_by_dataset = {
                "dset1": _solution("dset1", "sol1", ambiguous_time=ambiguous_time),
                "dset2": _solution("dset2", "sol2", ambiguous_time=ambiguous_time),
            }
            self.metric_values = metric_values

        def model_project_binding(self, model_ref):
            if dict(model_ref) != ref.as_dict():
                return {"attribution": "UNVERIFIED", "project_id": None}
            return {"attribution": "PROJECT_BOUND", "project_id": project_id}

        def invoke(self, operation, arguments, execution, operation_id, event_callback):
            self.calls.append((operation, deepcopy(arguments), dict(execution), operation_id))
            worker_id = f"worker-{len(self.calls)}"
            method = ("getSolutioninfo" if operation == "dataset.solution_indices" else
                      "getData" if context.get() == "strict_metric_evidence" else
                      "getStrictFieldReadback")
            event = {
                "kind": "call", "phase": "submitted", "operation_id": operation_id,
                "request_id": worker_id, "request_hash": hashlib.sha256(worker_id.encode()).hexdigest(),
                "metadata": {"type": "call", "request_id": worker_id, "handle": "model-handle",
                             "generation": ref.generation, "method": method, "args": []},
            }
            event_callback(event)
            self.submitted_callbacks.append((operation_id, operation))
            self.sent.append((operation_id, operation, worker_id))
            event_callback({**event, "phase": "observed", "status": "ok"})

            if operation == "dataset.solution_indices":
                data = deepcopy(self.binding_by_dataset[arguments["path"]])
                revision_delta = 0
            elif operation == "result.evaluate":
                spec = arguments["spec"]
                tuple_value = {"outer": spec["solution"]["outer"],
                               "inner": spec["solution"]["inner"],
                               "solnum": spec["solution"]["inner"]}
                if fault in {"unknown_after_submit", "cleanup_unknown"}:
                    message = ("injected temporary-node cleanup failure" if fault == "cleanup_unknown"
                               else "injected post-submit loss")
                    raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", message, stage="post_dispatch")
                if context.get() == "strict_field_readback":
                    dataset = spec["solution"]["dataset"]
                    if dataset == "dset1":
                        values = [1.0, 2.0]
                    else:
                        values = [1.5, 2.0] if tuple_value["inner"] == 2 else [10.0, 20.0]
                    coordinates = [[0.0, 1.0], [0.0, 0.0], [0.0, 0.0]]
                    if fault == "coordinates" and dataset == "dset2":
                        coordinates = [[0.0, 0.5], [0.0, 0.0], [0.0, 0.0]]
                    if fault == "nonfinite":
                        values[0] = float("nan")
                    selection = deepcopy(spec["selection"])
                    if fault == "selection" and dataset == "dset2":
                        selection["geometry"] = "geom-other"
                    selection_readback = {
                        "role": "primary", "feature_tag": "tmp_eval",
                        "source": "actual_transient_numerical_feature",
                        "component": selection["component"], "geometry": selection["geometry"],
                        "entity_dimension": selection["entity_dimension"], "kind": selection["kind"],
                        "tag": selection.get("tag"), "entities": [1, 2],
                        "native_selection_readback": {"geometry": selection["geometry"],
                            "dimension": selection["entity_dimension"], "entities": [1, 2],
                            "is_inheriting": False},
                    }
                    coordinates_evidence = {"values": coordinates, "shape": [3, 2],
                        "source": "PersistentComsolWorker.getStrictFieldReadback -> NumericalFeature.getCoordinates()",
                        "coordinate_frame": "spatial" if evidence_mode == "verified" else "UNVERIFIED"}
                    if evidence_mode == "verified":
                        coordinates_evidence["coordinate_frame_status"] = "VERIFIED"
                    if fault == "frame" and dataset == "dset2":
                        coordinates_evidence["coordinate_frame"] = "material"
                    intrinsic = None
                    if evidence_mode == "verified":
                        intrinsic_unit = "K" if fault != "unit" else "degC"
                        intrinsic = {"T": {"status": "VERIFIED", "intrinsic_unit": intrinsic_unit,
                                            "field_dimensionality": "VERIFIED"}}
                    strict = {
                        "status": "VERIFIED", "solution_tuple": {**tuple_value,
                            "source": "SolutionInfo.getSolnum(outer, strict)"},
                        "field_array": {"values": [[[[values[0], values[1]]]]],
                                        "shape": [1, 1, 1, 2], "units": ["K"]},
                        "coordinates": coordinates_evidence,
                        "expression_unit_readback": {"values": {"T": "K"},
                            "source": "NumericalFeature.getStringArray('unit')",
                            "interpretation": "configured/model-dependent"},
                        "selection_readback": selection_readback,
                    }
                    if intrinsic is not None:
                        strict["intrinsic_unit_readback"] = intrinsic
                    data = {"strict_field_readback": strict}
                else:
                    value = self.metric_values[0] if spec["solution"]["dataset"] == "dset1" else self.metric_values[1]
                    primary = {
                        "role": "primary", "feature_tag": "tmp_intvolume",
                        "source": "actual_transient_numerical_feature",
                        "component": spec["selection"]["component"],
                        "geometry": spec["selection"]["geometry"],
                        "entity_dimension": spec["selection"]["entity_dimension"],
                        "kind": spec["selection"]["kind"], "tag": spec["selection"].get("tag"),
                        "entities": [1, 2],
                        "native_selection_readback": {"geometry": spec["selection"]["geometry"],
                            "dimension": spec["selection"]["entity_dimension"],
                            "entities": [1, 2], "is_inheriting": False},
                    }
                    if fault == "metric_selection":
                        primary["geometry"] = "geom-other"
                        primary["native_selection_readback"]["geometry"] = "geom-other"
                    data = {"field_array": {"values": [[[value]]]},
                            "strict_metric_evidence": {
                                "status": "VERIFIED",
                                "selection_features": [primary],
                                "selected_solution_pairs": [tuple_value],
                                "expression_unit_readback": {"T": "K"},
                            }}
                state.revision += 1
                revision_delta = 1
            else:
                raise AssertionError(f"unexpected managed operation: {operation}")

            expected_revision = execution["expected_revision"]
            ticket_hash = canonical_request_hash(
                operation, arguments, ref, expected_revision, project_id=project_id,
                request_id=execution["request_id"], idempotency_key=execution["idempotency_key"],
            )
            if fault == "wrong_ticket_revision" and operation == "result.evaluate":
                returned_revision = state.revision - 1
            else:
                returned_revision = state.revision
            return {"success": True, "data": data,
                    "execution": {"model_ref": ref.as_dict(), "session_id": ref.session_id,
                                  "project_id": project_id, "revision": returned_revision,
                                  "operation_id": f"service-ticket-{len(self.calls)}",
                                  "request_id": execution["request_id"], "request_hash": ticket_hash}}

    backend = Backend()
    backend.store = SimpleNamespace(persist_artifact=lambda key, metadata: backend.persisted.__setitem__(key, deepcopy(metadata)))
    binding = {"project_id": project_id, "model_ref": ref.as_dict(),
               "attempt_id": "attempt-w21", "source_fingerprint": "source-fixture",
               "plan_sha256": "a" * 64}
    target_selection = {"dataset": "dset2", "solution": "sol2", "outer": "first", "inner": "last"}
    stage = {"stage_id": "heat-transfer", "target_selection": target_selection,
             "checks": list(checks if checks is not None else [_continuity()])}
    plan = {"version": 2, "plan_id": "heat-cycle", "stages": [stage]}
    solve_result = {"success": True, "execution": {"model_ref": ref.as_dict(),
        "session_id": ref.session_id, "project_id": project_id, "revision": state.revision}}
    return backend, binding, stage, plan, solve_result, state


def _produce(fixture):
    backend, binding, stage, plan, solve_result, state = fixture
    return produce_stage_output_readback(
        backend, binding=binding, stage=stage, plan=plan,
        stage_run_operation_id="stage-run-op", model_revision=state.revision,
        solve_result=solve_result,
    )


def test_worker_readback_binds_each_field_observation_and_boundary_tuple_separately(project_root):
    fixture = _fixture(project_root, checks=[_continuity()])
    output = _produce(fixture)
    backend, binding, stage, _plan, _solve_result, _state = fixture

    assert output["status"] == "UNVERIFIED"
    assert output["output_tuple"]["inner"] == 3  # final stage output at t=2 s
    check = output["checks"][0]
    assert check["source_tuple"]["inner"] == check["target_tuple"]["inner"] == 2  # boundary t=1 s
    assert check["target_tuple"] != output["output_tuple"]
    assert check["measured_error"] == pytest.approx(0.5)
    assert check["observed_error"] is None and check["status"] == "UNVERIFIED"

    source_ref = next(ref for ref in output["evidence_refs"] if ref["readphase"] == "temperature-boundary:source-field")
    target_ref = next(ref for ref in output["evidence_refs"] if ref["readphase"] == "temperature-boundary:target-field")
    assert source_ref["child_operation_id"] != target_ref["child_operation_id"]
    assert source_ref["request_id"] != target_ref["request_id"]
    assert source_ref["observation_revision"] < target_ref["observation_revision"]
    assert source_ref["sha256"] != target_ref["sha256"]
    for reference in (source_ref, target_ref):
        persisted = backend.persisted[reference["artifact_id"]]
        assert persisted["stage_run_operation_id"] == "stage-run-op"
        assert persisted["model_revision"] == reference["observation_revision"]
        assert persisted["managed_operation_id"] == reference["managed_operation_id"]
        assert persisted["check_definition_sha256"] == sha256_json(stage["checks"][0])
        assert persisted["selection_sha256"] == sha256_json(
            stage["checks"][0]["source_selection" if reference is source_ref else "target_selection"]
        )
    source_step = next(step for step in output["revision_chain"]
                       if step["child_operation_id"] == source_ref["child_operation_id"])
    target_step = next(step for step in output["revision_chain"]
                       if step["child_operation_id"] == target_ref["child_operation_id"])
    assert source_step["revision"] == source_ref["observation_revision"]
    assert target_step["revision"] == target_ref["observation_revision"]
    assert source_step["managed_operation_id"] == source_ref["managed_operation_id"]
    assert target_step["managed_operation_id"] == target_ref["managed_operation_id"]

    valid, missing, passed = validate_output_readback(
        output, binding=binding, stage_run_operation_id="stage-run-op",
        model_revision=40, target_selection=stage["target_selection"], checks=stage["checks"],
    )
    assert not valid and missing and not passed


def test_verified_field_measurement_reports_real_threshold_fail_without_promotion(project_root):
    fixture = _fixture(project_root, checks=[_continuity()], evidence_mode="verified")
    output = _produce(fixture)
    check = output["checks"][0]
    assert output["status"] == "VERIFIED"
    assert check["status"] == "FAIL"
    assert check["observed_error"] == pytest.approx(0.5)
    assert check["reference_scale"] == pytest.approx(2.0)
    valid, missing, passed = validate_output_readback(
        output, binding=fixture[1], stage_run_operation_id="stage-run-op",
        model_revision=40, target_selection=fixture[2]["target_selection"], checks=fixture[2]["checks"],
    )
    assert valid, missing
    assert not passed


@pytest.mark.parametrize("fault", ["unit", "frame", "coordinates", "selection"])
def test_field_unit_frame_grid_and_selector_mismatches_remain_unverified(project_root, fault):
    fixture = _fixture(project_root, checks=[_continuity()], evidence_mode="verified", fault=fault)
    output = _produce(fixture)
    row = output["checks"][0]
    assert output["status"] == "UNVERIFIED"
    assert row["status"] == "UNVERIFIED"
    assert row["observed_error"] is None
    assert row["missing"]


def test_solution_info_time_resolution_rejects_ambiguous_exact_tuple(project_root):
    fixture = _fixture(project_root, checks=[_continuity()], ambiguous_time=True)
    output = _produce(fixture)
    assert output["output_tuple"]["inner"] == 3
    assert output["checks"][0]["status"] == "UNVERIFIED"
    assert "resolve exactly one actual" in " ".join(output["checks"][0]["missing"])


def test_native_integral_reads_3d_integral_terms_and_keeps_target_boundary_tuple(project_root):
    check = _conservation()
    fixture = _fixture(project_root, checks=[check], metric_values=(12.0, 10.0))
    output = _produce(fixture)
    backend, _binding, stage, _plan, _solve, _state = fixture
    row = output["checks"][0]
    assert output["status"] == "UNVERIFIED"  # native intrinsic integral units are not established
    assert row["status"] == "UNVERIFIED"
    assert row["signed_error"] == pytest.approx(-2.0)
    assert row["observed_error"] == pytest.approx(2.0)
    assert row["reference_scale"] == pytest.approx(22.0)
    assert row["target_tuple"]["inner"] == output["output_tuple"]["inner"] == 3
    assert row["target_term_tuple"]["inner"] == 2
    assert row["target_term_tuple"] != row["target_tuple"]
    assert all(call[1].get("spec", {}).get("aggregate") == "integral"
               and call[1]["spec"].get("entity_dim") == 3
               for call in backend.calls if call[0] == "result.evaluate")
    term_rows = row["term_readbacks"]
    assert [term["solution_tuple"]["inner"] for term in term_rows] == [2, 2]
    assert all(term["selection_status"] == "VERIFIED" for term in term_rows)
    assert all(backend.persisted[term["evidence_ref"]["artifact_id"]]["model_revision"]
               == term["observation_revision"] for term in term_rows)

    # The producer itself keeps conservation UNVERIFIED because this G3 path
    # reports configured units without intrinsic integral dimensionality.
    # Complete only that validator input field here to exercise its tuple
    # contract; this fixture does not claim runtime/scientific acceptance.
    validator_claim = deepcopy(output)
    validator_claim["status"] = "VERIFIED"
    validator_claim["checks"][0]["unit"] = check["unit"]
    validator_claim["checks"][0]["status"] = "FAIL"
    valid, missing, passed = validate_output_readback(
        validator_claim, binding=fixture[1], stage_run_operation_id="stage-run-op",
        model_revision=40, target_selection=stage["target_selection"], checks=stage["checks"],
    )
    assert valid, missing
    assert not passed

    # Compatibility: term_readbacks are authoritative; envelopes predating
    # producer-added target term summaries remain valid.
    legacy_envelope = deepcopy(validator_claim)
    for key in ("target_term_tuples", "target_term_solution_bindings",
                "target_term_tuple", "target_term_solution_binding"):
        legacy_envelope["checks"][0].pop(key, None)
    accepted, reasons, accepted_pass = validate_output_readback(
        legacy_envelope, binding=fixture[1], stage_run_operation_id="stage-run-op",
        model_revision=40, target_selection=stage["target_selection"], checks=stage["checks"],
    )
    assert accepted, reasons
    assert not accepted_pass

    bad_claims = {}
    wrong_solution = deepcopy(validator_claim)
    wrong_solution["checks"][0]["term_readbacks"][1]["solution_binding"]["solution"] = "sol-wrong"
    wrong_solution["checks"][0]["term_readbacks"][1]["solution_tuple"]["solution"] = "sol-wrong"
    bad_claims["wrong target solution"] = wrong_solution
    missing_term = deepcopy(validator_claim)
    missing_term["checks"][0]["term_readbacks"].pop()
    bad_claims["missing term"] = missing_term
    duplicate_term = deepcopy(validator_claim)
    duplicate_term["checks"][0]["term_readbacks"].append(
        deepcopy(duplicate_term["checks"][0]["term_readbacks"][0])
    )
    bad_claims["duplicate term"] = duplicate_term
    incorrect_numeric = deepcopy(validator_claim)
    incorrect_numeric["checks"][0]["observed_error"] += 0.5
    bad_claims["tampered error"] = incorrect_numeric
    forged_summary = deepcopy(validator_claim)
    forged_summary["checks"][0]["target_term_tuples"][0]["inner"] = 999
    bad_claims["forged auxiliary term tuple"] = forged_summary
    for label, claim in bad_claims.items():
        accepted, reasons, accepted_pass = validate_output_readback(
            claim, binding=fixture[1], stage_run_operation_id="stage-run-op",
            model_revision=40, target_selection=stage["target_selection"], checks=stage["checks"],
        )
        assert not accepted and reasons and not accepted_pass, label


def test_native_integral_selector_mismatch_is_not_accepted(project_root):
    fixture = _fixture(project_root, checks=[_conservation()], fault="metric_selection")
    output = _produce(fixture)
    row = output["checks"][0]
    assert row["status"] == "UNVERIFIED"
    assert any("selection was not read back" in reason for reason in row["missing"])


def test_exact_binding_resolves_first_last_list_and_time_tuples_and_rejects_ambiguity():
    raw = _solution("dset2", "sol2")
    resolved, binding = _check_tuple({"dataset": "dset2", "solution": "sol2",
        "outer": "first", "inner": "last"}, raw)
    assert resolved["outer"] == 1 and resolved["inner"] == 3 and resolved["solnum"] == 3
    assert binding["selection_resolution"]["status"] == "VERIFIED"
    listed, _ = _check_tuple({"dataset": "dset2", "solution": "sol2",
        "outer": [1], "inner": [2]}, raw)
    assert listed["inner"] == 2 and listed["solnum"] == 2
    by_time, _ = _check_tuple({"dataset": "dset2", "solution": "sol2",
        "outer": "first", "time": [{"value": 1.0, "unit": "s"}]}, raw)
    assert by_time["inner"] == 2 and by_time["solnum"] == 2
    with pytest.raises(_EvidenceUnavailable, match="exactly one"):
        _check_tuple({"dataset": "dset2", "solution": "sol2", "time": [{"value": 1.0, "unit": "s"}]},
                     _solution("dset2", "sol2", ambiguous_time=True))


def test_empty_check_list_stays_unverified(project_root):
    fixture = _fixture(project_root, checks=[])
    output = _produce(fixture)
    assert output["status"] == "UNVERIFIED"
    assert output["checks"] == []
    valid, missing, passed = validate_output_readback(
        output, binding=fixture[1], stage_run_operation_id="stage-run-op",
        model_revision=40, target_selection=fixture[2]["target_selection"], checks=[],
    )
    assert not valid and not passed and missing


def test_nonfinite_field_overflow_and_unknown_after_dispatch_fail_closed(project_root):
    with pytest.raises(_EvidenceUnavailable, match="overflowed"):
        _max_abs_pairwise_error([1e308], [-1e308])
    fixture = _fixture(project_root, checks=[_continuity()], fault="nonfinite")
    output = _produce(fixture)
    assert output["checks"][0]["status"] == "UNVERIFIED"
    assert any("nonfinite" in reason for reason in output["checks"][0]["missing"])

    unknown = _fixture(project_root, checks=[_continuity()], fault="unknown_after_submit")
    backend = unknown[0]
    with pytest.raises(ExecutionContractError, match="injected post-submit loss"):
        _produce(unknown)
    assert len([call for call in backend.calls if call[0] == "result.evaluate"]) == 1
    assert len(backend.sent) == 3  # output tuple, field tuple binding, one submitted evaluation; no retry


def test_temporary_node_cleanup_unknown_propagates_without_second_read(project_root):
    fixture = _fixture(project_root, checks=[_continuity()], fault="cleanup_unknown")
    backend = fixture[0]
    with pytest.raises(ExecutionContractError, match="temporary-node cleanup failure"):
        _produce(fixture)
    assert len([call for call in backend.calls if call[0] == "result.evaluate"]) == 1


def test_inconsistent_managed_ticket_revision_is_unknown_without_retry(project_root):
    fixture = _fixture(project_root, checks=[_continuity()], fault="wrong_ticket_revision")
    backend = fixture[0]
    with pytest.raises(ExecutionContractError, match="revision chain is invalid"):
        _produce(fixture)
    assert len([call for call in backend.calls if call[0] == "result.evaluate"]) == 1
