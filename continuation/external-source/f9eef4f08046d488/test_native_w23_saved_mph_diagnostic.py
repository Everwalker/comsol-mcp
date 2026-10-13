from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.run_native_w23_saved_mph_diagnostic import (
    ALLOWED_MANAGED_OPERATIONS,
    ALLOWED_OPERATION_CALL_IDS,
    STUDY_SEQUENCE_TYPES,
    FIXTURE,
    _allowed_diagnostic_dispatch,
    _native_argv,
    validate_fixture_readback,
    MODE_POINT_EXPRESSIONS,
    RAW_FIELD_EXPRESSIONS,
    _field_points,
)


def _readback(*, pair: list[list[int]] | None = None) -> dict:
    pair = [[1, 1]] if pair is None else pair
    query_rows = [
        {"arguments": [kind], "status": "CAPTURED", "return_value": ["sol1"]}
        for kind in STUDY_SEQUENCE_TYPES
    ]
    matrix_calls = [
        {
            "method": method,
            "argument_types": ["int[][]"],
            "arguments": [pair],
            "receiver_type": "com.comsol.model.SolutionInfo",
            "status": "CAPTURED",
            "actual_return_type": "[Ljava.lang.String;",
            "return_value": [],
        }
        for method in ("getPNames", "getPvals", "getPvalsImag", "getUnits")
    ] if pair else []
    return {
        "diagnostic_phase": "saved_solution_diagnostic",
        "explicit_study_or_solver_run_calls_in_this_phase": 0,
        "native_scientific_acceptance": "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY",
        "internal_solver_execution_count": "UNKNOWN_NOT_INFERRED_FROM_READBACK",
        "study_solver_sequence_queries": query_rows,
        "datasets": [],
        "solver_sequences": [{
            "tag": "sol1",
            "sequence_receiver_type": "com.comsol.model.SolverSequence",
            "parameter_names_getter": {
                "method": "getPNames", "receiver_type": "com.comsol.model.SolverSequence",
                "status": "CAPTURED", "actual_return_type": "[Ljava.lang.String;",
                "return_value": [],
            },
            "solution_info": {
                "solution_info_receiver_type": "com.comsol.model.SolutionInfo",
                "solution_info_getter": {
                    "method": "getSolutioninfo", "receiver_type": "com.comsol.model.SolverSequence",
                    "status": "CAPTURED", "actual_return_type": "com.comsol.model.SolutionInfo",
                    "return_value": "<same receiver captured below>",
                },
                "observed_one_based_outer_inner_pairs": pair,
                "solver_sequence_parameter_reads": [
                    {
                        "outer": outer, "inner_solnum": inner,
                        "real_parameter_values": {
                            "method": "getPVals", "receiver_type": "com.comsol.model.SolverSequence",
                            "argument_types": ["int"], "arguments": [inner],
                            "status": "CAPTURED", "actual_return_type": "[D", "return_value": [],
                        },
                        "imaginary_parameter_values": {
                            "method": "getPValsImag", "receiver_type": "com.comsol.model.SolverSequence",
                            "argument_types": ["int"], "arguments": [inner],
                            "status": "CAPTURED", "actual_return_type": "[D", "return_value": [],
                        },
                    }
                    for outer, inner in pair
                ],
                "metadata_calls": matrix_calls,
            },
        }],
        "dataset_solution_info": [],
    }


def test_exact_documented_one_based_outer_inner_tuple_is_captured():
    result = validate_fixture_readback(_readback())
    assert result["status"] == "PASS_DIAGNOSTIC_CAPTURE_SCHEMA_ONLY"
    assert result["typed_matrix_getter_calls"] == 4
    assert result["solution_roles_inferred"] is False
    assert result["scientific_acceptance"] == "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY"


def test_captured_missing_axis_is_diagnostic_data_not_a_fabricated_tuple():
    result = validate_fixture_readback(_readback(pair=[]))
    assert result["typed_matrix_getter_calls"] == 0
    assert result["solution_roles_inferred"] is False


@pytest.mark.parametrize("bad_pair", [
    [[0, 1]],
    [[1, 0]],
    [[1], [1]],
    [[1, 1], [1, 2]],
    [[True, 1]],
])
def test_matrix_getters_reject_non_single_or_nonpositive_tuple(bad_pair):
    payload = _readback()
    for call in payload["solver_sequences"][0]["solution_info"]["metadata_calls"]:
        call["arguments"] = [bad_pair]
    with pytest.raises(ValueError, match="documented one-based"):
        validate_fixture_readback(payload)


def test_diagnostic_rejects_scientific_acceptance_or_inferred_solver_count():
    payload = _readback()
    payload["native_scientific_acceptance"] = "PASS"
    with pytest.raises(ValueError, match="NOT_ACCEPTED"):
        validate_fixture_readback(payload)

    payload = _readback()
    payload["internal_solver_execution_count"] = 0
    with pytest.raises(ValueError, match="cannot infer"):
        validate_fixture_readback(payload)


def test_operation_scope_excludes_study_solver_and_model_writes():
    assert ALLOWED_MANAGED_OPERATIONS == {
        "server_connect", "model_load", "model.inspect", "dataset.list",
        "dataset.solution_indices", "operation_call",
    }
    assert ALLOWED_OPERATION_CALL_IDS == {"code.execute_java", "result.at_points"}
    for forbidden in ("study.run", "solver.run", "result.evaluate", "result.mode_overlap", "model.save"):
        assert forbidden not in ALLOWED_MANAGED_OPERATIONS
        assert forbidden not in ALLOWED_OPERATION_CALL_IDS


def test_managed_operation_call_allows_only_the_exact_read_only_java_phase():
    request = {
        "operation": "operation_call",
        "arguments": {
            "operation_id": "code.execute_java",
            "arguments": {
                "source_artifact": FIXTURE.name,
                "entrypoint": "NativeW23TEScienceFixture#run",
                "arguments": {"phase": "saved_solution_diagnostic"},
                "mode": "trusted",
            },
        },
    }
    assert _allowed_diagnostic_dispatch(request) is True

    invalid_requests = []
    invalid = {"operation": "operation_call", "arguments": {
        **request["arguments"], "operation_id": "study.run"}}
    invalid_requests.append(invalid)
    invalid = {"operation": "operation_call", "arguments": {
        **request["arguments"], "arguments": {
            **request["arguments"]["arguments"],
            "arguments": {"phase": "identity"}}}}
    invalid_requests.append(invalid)
    invalid = {"operation": "operation_call", "arguments": {
        **request["arguments"], "arguments": {
            **request["arguments"]["arguments"],
            "arguments": {"phase": "saved_solution_diagnostic", "save": True}}}}
    invalid_requests.append(invalid)
    invalid = {"operation": "operation_call", "arguments": {
        **request["arguments"], "arguments": {
            **request["arguments"]["arguments"], "source_artifact": "other.java"}}}
    invalid_requests.append(invalid)
    for invalid in invalid_requests:
        assert _allowed_diagnostic_dispatch(invalid) is False
    assert _allowed_diagnostic_dispatch({"operation": "study.run", "arguments": {}}) is False


def _point_dispatch(dataset: str, solution: str, expressions: list[str], points: list[list[float]],
                    *, complex_mode: str = "preserve") -> dict:
    return {"operation": "operation_call", "arguments": {
        "operation_id": "result.at_points",
        "arguments": {"spec": {"expressions": expressions,
                                 "solution": {"dataset": dataset, "solution": solution},
                                 "complex_mode": complex_mode},
                      "points": points, "coordinate_unit": "um", "frame": "spatial"},
    }}


def test_saved_field_and_bma_point_reads_allow_only_the_frozen_dataset_grid_and_expressions():
    for x in (-10.0, 8.0):
        for count in (321, 641):
            request = _point_dispatch("dset1", "sol1", list(RAW_FIELD_EXPRESSIONS),
                                      _field_points(x, count))
            assert _allowed_diagnostic_dispatch(request) is True
    for dataset, solution, x in (("dset2", "sol2", -10.0), ("dset3", "sol3", 8.0)):
        assert _allowed_diagnostic_dispatch(
            _point_dispatch(dataset, solution, list(MODE_POINT_EXPRESSIONS), [[x, 0.0]])) is True

    denied = [
        _point_dispatch("dset3", "sol3", list(RAW_FIELD_EXPRESSIONS), _field_points(8.0, 321)),
        _point_dispatch("dset1", "sol1", list(RAW_FIELD_EXPRESSIONS), _field_points(8.0, 321)[:-1]),
        _point_dispatch("dset1", "sol1", list(RAW_FIELD_EXPRESSIONS)[:-1], _field_points(8.0, 321)),
        _point_dispatch("dset1", "sol1", list(RAW_FIELD_EXPRESSIONS), _field_points(8.0, 321),
                        complex_mode="real"),
        _point_dispatch("dset2", "sol2", list(MODE_POINT_EXPRESSIONS), [[-10.0, 0.25]]),
        _point_dispatch("dset2", "sol3", list(MODE_POINT_EXPRESSIONS), [[-10.0, 0.0]]),
    ]
    for request in denied:
        assert _allowed_diagnostic_dispatch(request) is False


def test_native_argv_unsets_offline_guard_and_uses_candidate_only_pythonpath():
    argv = _native_argv(
        Path("/tmp/freeze.json"), "f" * 64, Path("/tmp/approval.json"),
        Path("/tmp/saved.mph"), Path("/private/tmp/comsol-mcp-w23-te-saved-mph-zero-solve-readback-20261012-011-test"),
        Path("/tmp/evidence"),
    )
    assert argv[:9] == [
        "env", "-u", "CODEX_NETWORK_GUARD_LOG",
        "-u", "CODEX_NETWORK_GUARD_LABEL",
        "-u", "CODEX_NETWORK_GUARD_RUN_ID",
        "TMPDIR=/private/tmp",
        f"PYTHONPATH={Path(__file__).resolve().parents[1]}",
    ]
    assert f"PYTHONPATH={Path(__file__).resolve().parents[1]}" in argv
    assert not any("network-guard" in item for item in argv)


def test_java_diagnostic_phase_uses_documented_tuple_and_has_no_write_or_solve_call():
    fixture = Path(__file__).resolve().parents[1] / "tools/java/NativeW23TEScienceFixture.java"
    source = fixture.read_text(encoding="utf-8")
    start = source.index("private static Map<String, Object> savedSolutionDiagnostic")
    end = source.index("private static Map<String, Object> captureSolverTree", start)
    phase = source[start:end]
    phase = re.sub(r"/\*.*?\*/|//[^\n]*", "", phase, flags=re.DOTALL)

    assert '"All", "None", "SolverSequence", "CopySolution"' in phase
    assert '"ParametricStore", "Stored", "Parametric"' in phase
    assert "getSolnum(outer, true)" in phase
    assert "new int[][]{{pairOuter, pairInner}}" in phase
    assert "getPNames(pair)" in phase and "getPvals(pair)" in phase
    assert "sequence::getPNames" in phase
    assert "sequence.getPVals(pairInner)" in phase
    assert "sequence.getPValsImag(pairInner)" in phase
    assert ".run(" not in phase
    assert ".save(" not in phase
    assert ".create(" not in phase
