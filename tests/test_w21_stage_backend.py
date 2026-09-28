from __future__ import annotations

from copy import deepcopy

from comsol_mcp._stage_contract import sha256_json
from comsol_mcp._w21_stage_backend import (
    ADMISSION_CONTRACT,
    OUTPUT_CONTRACT,
    stage_attempt_binding,
    validate_native_admission,
    validate_output_readback,
)


def _attempt():
    return {
        "project_id": "project-a",
        "model_ref": {"session_id": "session-a", "server_instance_id": "server-a",
                      "model_tag": "model1", "generation": 1},
        "expected_revision": 8,
        "plan_id": "plan-a",
        "plan_sha256": "a" * 64,
        "definition_sha256": "b" * 64,
        "stage_id": "stage-a",
        "ordinal": 1,
        "attempt_id": "attempt-a",
        "operation_id": "operation-a",
        "request_hash": "c" * 64,
        "source_attempt_id": None,
    }


def _target_binding(spec):
    outer, inner, solnum = 1, 1, 1
    filters = {}
    names, values, units = [], [], []
    if not spec.get("time"):
        # Actual synthetic SolutionInfo boundary-time row, even when its
        # SolutionSpec selects only an outer/inner tuple.
        names.append("t")
        values.append(1.0)
        units.append("s")
    for axis, name in (("time", "t"), ("frequency", "freq")):
        if spec.get(axis):
            quantity = spec[axis][0]
            filters[axis] = {"status": "VERIFIED", "parameter_name": name,
                             "value": quantity["value"], "unit": quantity["unit"]}
            names.append(name)
            values.append(quantity["value"])
            units.append(quantity["unit"])
    for name, expected in (spec.get("parameters") or {}).items():
        names.append(name)
        values.append(expected.get("value") if isinstance(expected, dict) and "value" in expected else expected)
        units.append(expected.get("unit", "") if isinstance(expected, dict) else "")
    params = {"names": names, "values": values, "units": units, "solnum": solnum}
    return {
        "dataset": spec["dataset"], "solution": spec["solution"],
        "binding_complete": True, "pair_mapping_complete": True,
        "binding_source": "dataset property + SolutionInfo.getSolnum(outer, strict)",
        "outer_indices": [outer], "inner_indices": [inner],
        "solnum_pairs": [{"outer": outer, "inner": inner, "solnum": solnum}],
        "parameters_complete": True,
        "parameters_source": "SolutionInfo.getPNames(int[][])/getPvals(int[][])/getUnits(int[][])",
        "parameters": {"by_pair": {"1:1": params}},
        "selection_resolution": {
            "status": "VERIFIED", "target_selection_sha256": sha256_json(spec),
            "resolved_pair": {"outer": outer, "inner": inner, "solnum": solnum},
            "filters": filters,
        },
    }


def _valid_output(check, *, target_spec=None, observed_error=0.15, scale=10.0):
    spec = target_spec or {
        "dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1],
    }
    binding = stage_attempt_binding(_attempt())
    tuple_value = {"dataset": "dset2", "solution": "sol2", "outer": 1, "inner": 1, "solnum": 1}
    target_binding = _target_binding(spec)
    value = {
        "contract": OUTPUT_CONTRACT,
        "status": "VERIFIED",
        "binding": binding,
        "stage_run_operation_id": "operation-a",
        "model_revision": 9,
        "target_selection_sha256": sha256_json(spec),
        "output_tuple": tuple_value,
        "solution_binding": target_binding,
        "checks": [],
    }
    if check is not None:
        source_spec = {"dataset": "dset1", "solution": "sol1", "outer": [1], "inner": [1]}
        source_tuple = {"dataset": "dset1", "solution": "sol1", "outer": 1, "inner": 1, "solnum": 1}
        source_binding = _target_binding(source_spec)
        value["checks"] = [{
            "check_id": check["check_id"],
            "status": "PASS" if observed_error <= check["tolerance"]["absolute"] + check["tolerance"]["relative"] * scale else "FAIL",
            "check_definition_sha256": sha256_json(check),
            "unit": check["unit"],
            "observed_error": observed_error,
            "reference_scale": scale,
            "binding": binding,
            "stage_run_operation_id": "operation-a",
            "model_revision": 9,
            "evidence_ref": {"kind": "native-check-readback", "sha256": "d" * 64},
            "source_tuple": source_tuple,
            "source_solution_binding": source_binding,
            "target_tuple": tuple_value,
            "target_solution_binding": target_binding,
        }]
    return value


def _check(target_spec):
    return {
        "kind": "continuity", "check_id": "continuity-a", "unit": "K",
        "tolerance": {"absolute": 0.1, "relative": 0.01},
        "source_solution": {"dataset": "dset1", "solution": "sol1", "outer": [1], "inner": [1]},
        "target_solution": target_spec,
        "boundary_time": {"value": 1.0, "unit": "s"},
    }


def test_native_admission_is_bound_to_backend_attempt_and_every_required_fact():
    binding = stage_attempt_binding(_attempt())
    proof = {
        "contract": ADMISSION_CONTRACT,
        "producer": "managed-backend-native-readback",
        "status": "VERIFIED",
        "binding": deepcopy(binding),
        "facts": {
            "source_attempt_binding": "VERIFIED", "target_field_identity": "VERIFIED",
            "source_target_units": "VERIFIED", "source_target_mesh": "VERIFIED",
            "frame_identity": "VERIFIED", "history_identity": "VERIFIED",
        },
        "evidence_refs": [{"sha256": "e" * 64}],
    }
    assert validate_native_admission(proof, binding) == (True, [])
    proof["binding"]["expected_revision"] += 1
    valid, missing = validate_native_admission(proof, binding)
    assert valid is False and any("exact attempt/model/revision/plan" in item for item in missing)
    proof["binding"] = deepcopy(binding)
    proof["facts"]["source_target_units"] = "UNVERIFIED"
    assert validate_native_admission(proof, binding)[0] is False
    proof["facts"]["source_target_units"] = "VERIFIED"
    proof["evidence_refs"] = [{"sha256": "not-a-content-hash"}]
    assert validate_native_admission(proof, binding)[0] is False

    contradictory = {
        **proof,
        "status": "UNVERIFIED",
        "evidence_refs": [{"sha256": "e" * 64}],
        "facts": {fact: "VERIFIED" for fact in (
            "source_attempt_binding", "target_field_identity", "source_target_units",
            "source_target_mesh", "frame_identity", "history_identity",
        )},
    }
    valid, missing = validate_native_admission(contradictory, binding)
    assert valid is False and any("overall status" in item for item in missing)


def test_output_requires_exact_solution_binding_frozen_check_and_bound_revision():
    target = {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1]}
    check = _check(target)
    proof = _valid_output(check, target_spec=target)
    result = validate_output_readback(
        proof, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )
    assert result == (True, [], True)

    stale = deepcopy(proof)
    stale["model_revision"] = 10
    assert validate_output_readback(
        stale, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )[0] is False

    stale = deepcopy(proof)
    stale["checks"][0]["check_definition_sha256"] = "f" * 64
    assert validate_output_readback(
        stale, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )[0] is False

    stale = deepcopy(proof)
    stale["checks"][0]["evidence_ref"]["sha256"] = "z" * 64
    assert validate_output_readback(
        stale, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )[0] is False


def test_output_rejects_conflicting_pair_keys_and_inconsistent_axes():
    target = {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1]}
    check = _check(target)
    proof = _valid_output(check, target_spec=target)
    call = lambda item: validate_output_readback(
        item, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )

    duplicate_pair = deepcopy(proof)
    duplicate_pair["solution_binding"]["solnum_pairs"].append(
        {"outer": 1, "inner": 1, "solnum": 99}
    )
    duplicate_pair["checks"][0]["target_solution_binding"] = deepcopy(
        duplicate_pair["solution_binding"]
    )
    assert call(duplicate_pair)[0] is False

    for field, value in (
        ("outer_indices", [1, 2]),
        ("inner_indices", [1, 2]),
        ("inner_indices_by_outer", {1: [1, 2]}),
    ):
        inconsistent = deepcopy(proof)
        inconsistent["solution_binding"][field] = value
        inconsistent["checks"][0]["target_solution_binding"] = deepcopy(
            inconsistent["solution_binding"]
        )
        assert call(inconsistent)[0] is False, field

def test_output_rejects_duplicate_checks_selector_mismatch_and_parameter_mismatch():
    target = {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1],
              "time": [{"value": 1.0, "unit": "s"}]}
    check = _check(target)
    proof = _valid_output(check, target_spec=target)
    assert validate_output_readback(
        proof, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )[0] is True

    mismatched = deepcopy(proof)
    mismatched["solution_binding"]["parameters"]["by_pair"]["1:1"]["values"] = [2.0]
    mismatched["checks"][0]["target_solution_binding"] = deepcopy(mismatched["solution_binding"])
    assert validate_output_readback(
        mismatched, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )[0] is False

    duplicate = deepcopy(proof)
    duplicate["checks"].append(deepcopy(duplicate["checks"][0]))
    assert validate_output_readback(
        duplicate, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )[0] is False

    malformed_extra = deepcopy(proof)
    malformed_extra["checks"].append(None)
    assert validate_output_readback(
        malformed_extra, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )[0] is False


def test_output_selection_filters_require_actual_solutioninfo_values_and_named_axes():
    target = {
        "dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1],
        "frequency": [{"value": 2.5e6, "unit": "Hz"}],
        "parameters": {"sweep": {"value": "case-a", "unit": ""}},
    }
    check = _check(target)
    proof = _valid_output(check, target_spec=target)
    call = lambda item: validate_output_readback(
        item, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )
    assert call(proof) == (True, [], True)

    # A matching whole-selection hash is insufficient if the actual typed
    # SolutionInfo row does not carry the requested value on this tuple.
    mismatch = deepcopy(proof)
    row = mismatch["solution_binding"]["parameters"]["by_pair"]["1:1"]
    row["values"][row["names"].index("freq")] = 1.0e6
    mismatch["checks"][0]["target_solution_binding"] = deepcopy(mismatch["solution_binding"])
    assert call(mismatch)[0] is False

    # A caller/backend label cannot relabel an arbitrary parameter as time or
    # frequency when the actual parameter name is not an axis name.
    renamed = deepcopy(proof)
    row = renamed["solution_binding"]["parameters"]["by_pair"]["1:1"]
    row["names"][row["names"].index("freq")] = "frequency_like"
    renamed["solution_binding"]["selection_resolution"]["filters"]["frequency"]["parameter_name"] = "frequency_like"
    renamed["checks"][0]["target_solution_binding"] = deepcopy(renamed["solution_binding"])
    assert call(renamed)[0] is False


def _continuity_boundary_fixture():
    stage_target = {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [2]}
    check_target = {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1]}
    source_spec = {"dataset": "dset1", "solution": "sol1", "outer": [1], "inner": [1]}
    check = {
        "kind": "continuity", "check_id": "boundary-continuity", "unit": "K",
        "source_solution": source_spec, "target_solution": check_target,
        "boundary_time": {"value": 1.0, "unit": "s"},
        "tolerance": {"absolute": 0.1, "relative": 0.01},
    }
    proof = _valid_output(None, target_spec=stage_target)
    target_binding = proof["solution_binding"]
    target_binding["inner_indices"] = [1, 2]
    target_binding["inner_indices_by_outer"] = {1: [1, 2]}
    target_binding["solnum_pairs"] = [
        {"outer": 1, "inner": 1, "solnum": 1},
        {"outer": 1, "inner": 2, "solnum": 2},
    ]
    target_binding["selection_resolution"]["resolved_pair"] = {
        "outer": 1, "inner": 2, "solnum": 2,
    }
    target_binding["parameters"]["by_pair"] = {
        "1:1": {"names": ["t"], "values": [1.0], "units": ["s"], "solnum": 1},
        "1:2": {"names": ["t"], "values": [2.0], "units": ["s"], "solnum": 2},
    }
    proof["output_tuple"] = {
        "dataset": "dset2", "solution": "sol2", "outer": 1, "inner": 2, "solnum": 2,
    }
    selected_target_binding = deepcopy(target_binding)
    selected_target_binding["inner_indices"] = [1]
    selected_target_binding["inner_indices_by_outer"] = {1: [1]}
    selected_target_binding["solnum_pairs"] = [
        {"outer": 1, "inner": 1, "solnum": 1},
    ]
    selected_target_binding["parameters"]["by_pair"] = {
        "1:1": {"names": ["t"], "values": [1.0], "units": ["s"], "solnum": 1},
    }
    selected_target_binding["selection_resolution"] = {
        "status": "VERIFIED", "target_selection_sha256": sha256_json(check_target),
        "resolved_pair": {"outer": 1, "inner": 1, "solnum": 1}, "filters": {},
    }
    source_binding = _target_binding(source_spec)
    proof["checks"] = [{
        "check_id": check["check_id"], "status": "PASS",
        "check_definition_sha256": sha256_json(check), "unit": check["unit"],
        "observed_error": 0.05, "reference_scale": 10.0,
        "binding": deepcopy(proof["binding"]), "stage_run_operation_id": proof["stage_run_operation_id"],
        "model_revision": proof["model_revision"], "evidence_ref": {"sha256": "d" * 64},
        "source_tuple": {"dataset": "dset1", "solution": "sol1", "outer": 1, "inner": 1, "solnum": 1},
        "source_solution_binding": source_binding,
        "target_tuple": {"dataset": "dset2", "solution": "sol2", "outer": 1, "inner": 1, "solnum": 1},
        "target_solution_binding": selected_target_binding,
    }]
    return proof, check, stage_target


def test_v2_continuity_checks_boundary_tuple_not_terminal_output_tuple():
    proof, check, stage_target = _continuity_boundary_fixture()
    kwargs = {
        "binding": stage_attempt_binding(_attempt()), "stage_run_operation_id": "operation-a",
        "model_revision": 9, "target_selection": stage_target, "checks": [check],
    }
    assert validate_output_readback(proof, **kwargs) == (True, [], True)

    wrong_boundary = deepcopy(check)
    wrong_boundary["boundary_time"] = {"value": 1.5, "unit": "s"}
    wrong_proof = deepcopy(proof)
    wrong_proof["checks"][0]["check_definition_sha256"] = sha256_json(wrong_boundary)
    valid, missing, passed = validate_output_readback(
        wrong_proof, **{**kwargs, "checks": [wrong_boundary]},
    )
    assert valid is False and passed is False and missing


def test_numeric_fail_is_valid_but_never_pass_and_overflow_is_rejected():
    target = {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1]}
    check = _check(target)
    failed = _valid_output(check, target_spec=target, observed_error=5.0, scale=10.0)
    valid, missing, passed = validate_output_readback(
        failed, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )
    assert valid is True and missing == [] and passed is False

    overflow_check = deepcopy(check)
    overflow_check["tolerance"]["relative"] = 2.0
    overflow = _valid_output(overflow_check, target_spec=target, observed_error=1.0, scale=1e308)
    valid, missing, passed = validate_output_readback(
        overflow, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )
    assert valid is False and passed is False and missing

    huge_integer = _valid_output(check, target_spec=target)
    huge_integer["checks"][0]["reference_scale"] = 10 ** 10000
    assert validate_output_readback(
        huge_integer, binding=stage_attempt_binding(_attempt()), stage_run_operation_id="operation-a",
        model_revision=9, target_selection=target, checks=[check],
    )[0] is False
