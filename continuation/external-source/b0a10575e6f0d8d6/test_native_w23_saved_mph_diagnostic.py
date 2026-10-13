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
    mode_units,
    protocol,
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
    units = None
    if expressions == list(RAW_FIELD_EXPRESSIONS):
        from tools.run_native_w23_saved_mph_diagnostic import FIELD_REQUESTED_UNITS
        units = list(FIELD_REQUESTED_UNITS)
    elif expressions == ["freq"]: units = ["Hz"]
    elif dataset in {"dset2", "dset3"}:
        units = list(mode_units("1" if dataset == "dset2" else "2").values())
    return {"operation": "operation_call", "arguments": {
        "operation_id": "result.at_points",
        "arguments": {"spec": {"expressions": expressions,
                                 "solution": {"dataset": dataset, "solution": solution},
                                 "complex_mode": complex_mode, **({"units": units} if units else {})},
                      "points": [{"x": point[0], "y": point[1]} for point in points], "coordinate_unit": "um", "frame": "spatial"},
    }}


def test_saved_field_and_bma_point_reads_allow_only_the_frozen_dataset_grid_and_expressions():
    for x in (-10.0, 8.0):
        for count in (321, 641):
            request = _point_dispatch("dset1", "sol1", list(RAW_FIELD_EXPRESSIONS),
                                      _field_points(x, count))
            assert _allowed_diagnostic_dispatch(request) is True
    for dataset, solution, x in (("dset2", "sol2", -10.0), ("dset3", "sol3", 8.0)):
        assert _allowed_diagnostic_dispatch(
            _point_dispatch(dataset, solution, list(protocol.bma_point_expressions("1" if dataset == "dset2" else "2")), [[x, 0.0]])) is True

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


def test_saved_frequency_and_port_units_are_exact_allowlist():
    request = _point_dispatch("dset1", "sol1", ["freq"], [[-10.0, 0.0]])
    assert _allowed_diagnostic_dispatch(request)
    request["arguments"]["arguments"]["spec"]["units"] = ["GHz"]
    assert _allowed_diagnostic_dispatch(request)
    request["arguments"]["arguments"]["spec"]["units"] = ["MHz"]
    assert not _allowed_diagnostic_dispatch(request)
    wrong_port = _point_dispatch("dset3", "sol3", ["ewfd.beta_1", "ewfd.neff_1", "ewfd.k0"], [[8.0, 0.0]])
    assert not _allowed_diagnostic_dispatch(wrong_port)
    correct = _point_dispatch("dset3", "sol3", ["ewfd.beta_2", "ewfd.neff_2", "ewfd.k0"], [[8.0, 0.0]])
    assert _allowed_diagnostic_dispatch(correct)
    correct["arguments"]["arguments"]["spec"].pop("units")
    assert not _allowed_diagnostic_dispatch(correct)



def test_top_level_native_cli_failure_keeps_stdout_stderr(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools import run_native_w23_saved_mph_diagnostic as runner
    args = SimpleNamespace(evidence=tmp_path / "native-execute", freeze=tmp_path / "freeze.json",
                           freeze_sha256="0"*64, approval=tmp_path / "approval.json",
                           saved_model=tmp_path / "model.mph", work=tmp_path / "work")
    def fail(*arguments):
        print("before failure")
        raise ValueError("preserved synthetic CLI failure")
    monkeypatch.setattr(runner, "execute", fail)
    with pytest.raises(ValueError, match="preserved synthetic"):
        runner._execute_with_cli_capture(args)
    assert "before failure" in (tmp_path / "native-execute.cli.stdout.bin").read_text()
    assert "ValueError: preserved synthetic CLI failure" in (tmp_path / "native-execute.cli.stderr.bin").read_text()
    assert not args.evidence.exists()



def test_field_allowlist_requires_exact_explicit_units():
    request = _point_dispatch("dset1", "sol1", list(RAW_FIELD_EXPRESSIONS), _field_points(8.0, 321))
    assert _allowed_diagnostic_dispatch(request)
    request["arguments"]["arguments"]["spec"].pop("units")
    assert not _allowed_diagnostic_dispatch(request)
    request["arguments"]["arguments"]["spec"]["units"] = ["V/m"] * len(RAW_FIELD_EXPRESSIONS)
    assert not _allowed_diagnostic_dispatch(request)



@pytest.mark.parametrize("mutation", [None, "model_during", "revision_during", "epoch_during", "epoch_before_second", "revision_before_second"])
def test_actual_frequency_read_controller_stops_on_first_identity_drift(tmp_path, monkeypatch, mutation):
    import copy
    import time
    from types import SimpleNamespace
    from tools import run_native_w23_saved_mph_diagnostic as runner
    health = {"ok": True, "connected": True, "queued_or_running": 0,
              "instance_id": "worker1", "generation": 2, "server": "127.0.0.1:1234"}
    calls = []
    class Worker:
        def health(self, **kwargs):
            value = copy.deepcopy(health)
            if mutation == "epoch_during" and calls: value["generation"] = 3
            if mutation == "epoch_before_second": value["generation"] = 3
            return value
    ref = {"schema_version": 1, "model_tag": "mcp1", "generation": 1,
           "session_id": "session1", "server_instance_id": "server1"}
    def points(*args, **kwargs):
        calls.append(kwargs)
        returned_ref = copy.deepcopy(kwargs["ref"])
        revision = kwargs["revision"]
        if mutation == "model_during": returned_ref["model_tag"] = "other"
        if mutation == "revision_during": revision += 1
        return {"test": "mock point transport only"}, returned_ref, revision
    monkeypatch.setattr(runner, "_at_points_once", points)
    monkeypatch.setattr(runner.protocol, "decode_production_complex_point_readback", lambda *args, **kwargs: {"decoded": "mock"})
    expected = None
    if mutation in {"epoch_before_second", "revision_before_second"}:
        expected = {"model_ref_before": copy.deepcopy(ref), "revision_before": 0,
                    "worker_epoch_before": {key: health[key] for key in ("instance_id", "generation", "server")}}
    kwargs = dict(requested_unit="GHz" if expected else "Hz", ref=ref,
                  revision=1 if mutation == "revision_before_second" else 0, project_id="project1",
                  deadline_epoch=time.time()+1000, evidence=tmp_path, count_witness={}, expected_context=expected)
    if mutation:
        with pytest.raises(RuntimeError, match="frequency-control"):
            runner._frequency_readback_once(object(), SimpleNamespace(worker=Worker()), **kwargs)
        assert len(calls) == (0 if expected else 1)
    else:
        decoded, context, new_ref, revision = runner._frequency_readback_once(object(), SimpleNamespace(worker=Worker()), **kwargs)
        assert len(calls) == 1 and calls[0]["requested_units"] == ("Hz",)
        assert context["model_ref_before"] == context["model_ref_after"] == ref
        assert context["worker_epoch_before"] == context["worker_epoch_after"]


def _actual_execute_prebirth_fixture(monkeypatch, *, filesystem_case=False):
    import copy,json,os,shutil,uuid
    from pathlib import Path
    from tools import run_native_w23_saved_mph_diagnostic as runner
    # Genuine production input validators consume real source manifests and the
    # preserved MPH bytes. Only the native-effect handoff is a sentinel.
    if filesystem_case:
        monkeypatch.setattr(runner,"CANDIDATE_REVISION",runner.CANDIDATE_REVISION+"-fixture-"+uuid.uuid4().hex)
    folder=runner.REPO/"prebirth-input-tests"/uuid.uuid4().hex;folder.mkdir(parents=True)
    work=runner._expected_work_path();evidence=folder/"native-execute"
    assert not work.exists() and not work.is_symlink()
    compile_root=Path(os.environ["W23_OFFLINE_CLASS_TEST_SEED"])
    compile_receipt=json.loads(Path(os.environ["W23_OFFLINE_CLASS_TEST_RECEIPT"]).read_text())
    assert compile_receipt["status"]=="PASS_COMSOL_64_JDK11_COMPILE_AND_JAVAP"
    for record in compile_receipt["compiled_sources"].values():
        assert runner.sha256_file(Path(record["source"]))==record["source_sha256"]
    shutil.copytree(compile_root,folder/"offline_gates"/"compile")
    source=runner._source_files();manifest=runner.science._write_source_manifest_artifact(folder,source)
    freeze={"status":"FROZEN_AWAITING_ROOT_APPROVAL","activity_id":runner.ACTIVITY_ID,"candidate_id":runner.CANDIDATE_ID,"candidate_revision":runner.CANDIDATE_REVISION,"source_files":source,"source_manifest_sha256":runner._source_digest(source),"source_manifest_artifact":manifest,"work_path":str(work),"execution_evidence_path":str(evidence),"read_only_call_plan":{"ordered_point_slots":["freq_Hz","freq_GHz","field4","mode2"]},"offline_preflight":{"status":"PASS_ALL_OFFLINE_GATES","compile":copy.deepcopy(compile_receipt)}}
    freeze_path=folder/"freeze.json";runner.write_json(freeze_path,freeze)
    # Every target mutation starts behind a real successful material/source gate.
    runner._validate_freeze(freeze_path,runner.sha256_file(freeze_path))
    runner.write_json(folder/"VALID_MATERIAL_PRE_MUTATION.json",{
        "status":"PASS_REAL_PRODUCTION_FREEZE_AND_MATERIAL_BEFORE_TARGET_MUTATION",
        "activity_id":runner.ACTIVITY_ID,"revision":runner.CANDIDATE_REVISION,
        "material":runner.science._check_offline_receipts(freeze_path,freeze),
        "native_effect_boundary":"SENTINEL_ONLY_NOT_RUN","material_gate_mocked":False})
    approval_path=folder/"reference-only-native-sentinel-approval.json"
    def approval_for(path=work,target=evidence):
        digest=runner.sha256_file(freeze_path);argv=runner._native_argv(freeze_path,digest,approval_path,runner.SOURCE_MODEL,path,target)
        approval={"status":"ROOT_APPROVED_FOR_EXACT_FROZEN_CANDIDATE","activity_id":runner.ACTIVITY_ID,"candidate_id":runner.CANDIDATE_ID,"candidate_revision":runner.CANDIDATE_REVISION,"freeze_sha256":digest,"source_manifest_sha256":freeze["source_manifest_sha256"],"native_argv_sha256":runner.sha256_json(argv),"max_owned_server_processes":1,"max_worker_sessions":1,"max_gui_processes":0,"max_study_run_calls":0,"max_solver_run_calls":0,"max_seconds_from_server_birth":1800,"cleanup_reserve_seconds":120,"retry_unknown_or_nonterminal_worker_request":False,"read_only_call_plan_sha256":runner.sha256_json(freeze["read_only_call_plan"]),"exact_native_invocation":{"argv":argv,"cwd":str(runner.REPO),"environment":{"TMPDIR":"/private/tmp","PYTHONPATH":str(runner.REPO),"CODEX_NETWORK_GUARD_LOG":None,"CODEX_NETWORK_GUARD_LABEL":None,"CODEX_NETWORK_GUARD_RUN_ID":None}},"issued_by":"SYNTHETIC_EXECUTE_PREBIRTH_TEST_ONLY_NATIVE_BOUNDARY_REPLACED"}
        runner.write_json(approval_path,approval)
        return digest
    monkeypatch.setenv("TMPDIR","/private/tmp");monkeypatch.setenv("PYTHONPATH",str(runner.REPO))
    for key in ("CODEX_NETWORK_GUARD_LOG","CODEX_NETWORK_GUARD_LABEL","CODEX_NETWORK_GUARD_RUN_ID"):monkeypatch.delenv(key,raising=False)
    handoffs=[]
    def boundary(*args):handoffs.append(args);return {"status":"TEST_NATIVE_EFFECT_HANDOFF_NOT_EXECUTED","native":"NOT_RUN"}
    monkeypatch.setattr(runner,"_execute_native_campaign",boundary)
    return runner,freeze,freeze_path,approval_path,work,evidence,approval_for,handoffs


def test_production_execute_prebirth_exact_activity017_reaches_only_native_boundary(monkeypatch):
    runner,freeze,fp,ap,work,evidence,approve,handoffs=_actual_execute_prebirth_fixture(monkeypatch)
    suffix=runner.ACTIVITY_ID.removeprefix("w23-saved-mph-zero-solve-readback-")
    assert work==Path("/private/tmp")/("comsol-mcp-w23-te-saved-mph-zero-solve-readback-"+suffix+"-"+runner.CANDIDATE_REVISION)
    digest=approve()
    outcome=runner.execute(fp,digest,ap,runner.SOURCE_MODEL,work,evidence)
    assert outcome["native"]=="NOT_RUN" and len(handoffs)==1
    assert not work.exists() and not evidence.exists()
    assert handoffs[0][-1]["source_files"]==runner._source_files()


@pytest.mark.parametrize("mutation",["old012","old013","old014","old015","wrong_activity","wrong_revision","outside","relative","dotdot","existing_work","symlink_work","dangling_symlink_work","existing_evidence","symlink_evidence","wrong_evidence","outside_evidence","freeze_wrong_work","bad_activity","bad_revision","bad_approval_hash","bad_exact_argv","wrong_env","offline_guard_env","source_map_tamper","symlink_freeze","symlink_approval","symlink_model","illegal_path"])
def test_production_execute_prebirth_negative_has_zero_native_handoff_work_server_or_cas(monkeypatch,mutation):
    import json
    from pathlib import Path
    filesystem_case=mutation in {"existing_work","symlink_work","dangling_symlink_work"}
    runner,freeze,fp,ap,expected,evidence,approve,handoffs=_actual_execute_prebirth_fixture(monkeypatch,filesystem_case=filesystem_case)
    work=expected;model=runner.SOURCE_MODEL;created=[]
    activity_suffix=runner.ACTIVITY_ID.removeprefix("w23-saved-mph-zero-solve-readback-")
    if mutation=="old012":work=Path(str(work).replace(activity_suffix,"20261012-012"))
    elif mutation=="old013":work=Path(str(work).replace(activity_suffix,"20261012-013"))
    elif mutation=="old014":work=Path(str(work).replace(activity_suffix,"20261012-014"))
    elif mutation=="old015":work=Path(str(work).replace(activity_suffix,"20261012-015"))
    elif mutation=="wrong_activity":work=Path(str(work).replace(activity_suffix,activity_suffix+"-wrong"))
    elif mutation=="wrong_revision":work=Path(str(work).replace(runner.CANDIDATE_REVISION,"candidate20-r4"))
    elif mutation=="outside":work=Path("/tmp")/work.name
    elif mutation=="relative":work=Path(work.name)
    elif mutation=="dotdot":work=work.parent/".."/"tmp"/work.name
    elif mutation=="illegal_path":work=Path("/private/tmp/invalid\x00name")
    elif mutation=="existing_work":work.mkdir();created.append(work)
    elif mutation in {"symlink_work","dangling_symlink_work"}:work.symlink_to(fp if mutation=="symlink_work" else fp.parent/"absent");created.append(work)
    elif mutation=="existing_evidence":evidence.mkdir();created.append(evidence)
    elif mutation=="symlink_evidence":evidence.symlink_to(fp);created.append(evidence)
    elif mutation=="wrong_evidence":evidence=evidence.with_name("other-native-execute")
    elif mutation=="outside_evidence":evidence=Path("/private/tmp/not-private-candidate-evidence");freeze["execution_evidence_path"]=str(evidence)
    elif mutation=="freeze_wrong_work":freeze["work_path"]=str(work)+"-foreign"
    elif mutation=="bad_activity":freeze["activity_id"]="foreign-activity"
    elif mutation=="bad_revision":freeze["candidate_revision"]="foreign-revision"
    elif mutation=="source_map_tamper":next(iter(freeze["source_files"].values()))["sha256"]="0"*64
    if mutation in {"old012","old013","old014","old015","wrong_activity","wrong_revision"}:
        assert work!=expected, "target token mutation must change canonical work path"
    runner.write_json(fp,freeze);digest=approve(work,evidence)
    if mutation in {"bad_approval_hash","bad_exact_argv"}:
        value=json.loads(ap.read_text())
        if mutation=="bad_approval_hash":value["freeze_sha256"]="0"*64
        else:value["exact_native_invocation"]["argv"][-1]="foreign-evidence"
        runner.write_json(ap,value)
    elif mutation=="wrong_env":monkeypatch.setenv("TMPDIR","/tmp")
    elif mutation=="offline_guard_env":monkeypatch.setenv("CODEX_NETWORK_GUARD_RUN_ID","invalid-native-inherited-guard")
    elif mutation=="symlink_freeze":q=fp.with_name("freeze-symlink.json");q.symlink_to(fp);fp=q
    elif mutation=="symlink_approval":q=ap.with_name("approval-symlink.json");q.symlink_to(ap);ap=q
    elif mutation=="symlink_model":q=fp.with_name("source-symlink.mph");q.symlink_to(model);model=q
    try:
        reasons={**{key:"exact frozen activity/revision canonical path" for key in ("old012","old013","old014","old015","wrong_activity","wrong_revision","outside","relative","dotdot","freeze_wrong_work","illegal_path")},
            **{key:"freeze identity/status/source-map digest is invalid" for key in ("bad_activity","bad_revision","source_map_tamper")},
            **{key:"exact frozen activity root approval mismatch" for key in ("bad_approval_hash","bad_exact_argv")},
            **{key:"canonical without symlinks" for key in ("symlink_work","dangling_symlink_work","symlink_evidence")},
            "existing_work":"work path must be fresh","existing_evidence":"evidence path must be fresh",
            "wrong_evidence":"exact frozen private-candidate path","outside_evidence":"exact frozen private-candidate path",
            "wrong_env":"native environment must use candidate-only","offline_guard_env":"native environment must not inherit",
            "symlink_freeze":"freeze must be a canonical ordinary private-candidate path",
            "symlink_approval":"approval must be a canonical ordinary private-candidate path",
            "symlink_model":"saved model must be a canonical ordinary private-candidate path"}
        with pytest.raises((RuntimeError,ValueError,FileExistsError,OSError),match=re.escape(reasons[mutation])) as refused:
            runner.execute(fp,digest,ap,model,work,evidence)
        assert "offline gates" not in str(refused.value) and "compiler output" not in str(refused.value)
        runner.write_json(ap.parent/"TARGETED_REFUSAL_RESULT.json",{
            "status":"PASS_TARGETED_REFUSAL_AFTER_REAL_MATERIAL_GATE","mutation":mutation,
            "expected_reason":reasons[mutation],"actual_reason":str(refused.value),
            "actual_type":type(refused.value).__name__,"native_handoffs":len(handoffs),
            "work":str(work),"expected_work":str(expected),"material_gate_mocked":False})
        assert handoffs==[]
        if expected not in created:assert not expected.exists() and not expected.is_symlink()
        if evidence not in created:assert not evidence.exists()
    finally:
        # These are empty/symlink test-only inputs, never native model trees.
        for path in reversed(created):
            if path.is_symlink():path.unlink()
            else:path.rmdir()
    assert not expected.exists() and not expected.is_symlink()


def test_production_cli_main_parse_and_execute_reaches_native_boundary_without_birth(monkeypatch):
    import sys
    runner,freeze,fp,ap,work,evidence,approve,handoffs=_actual_execute_prebirth_fixture(monkeypatch)
    digest=approve()
    monkeypatch.setattr(sys,"argv",[str(runner.__file__),"execute","--freeze",str(fp),"--freeze-sha256",digest,"--approval",str(ap),"--saved-model",str(runner.SOURCE_MODEL),"--work",str(work),"--evidence",str(evidence)])
    assert runner.main()==2  # sentinel is explicitly NOT_RUN, never a fake PASS
    assert len(handoffs)==1 and not work.exists() and not evidence.exists()
    assert "TEST_NATIVE_EFFECT_HANDOFF_NOT_EXECUTED" in evidence.with_name(evidence.name+".cli.stdout.bin").read_text()


def test_production_cli_negative_raw_traceback_preserves_original_prebirth_refusal(monkeypatch):
    import sys
    from pathlib import Path
    runner,freeze,fp,ap,work,evidence,approve,handoffs=_actual_execute_prebirth_fixture(monkeypatch)
    activity_suffix=runner.ACTIVITY_ID.removeprefix("w23-saved-mph-zero-solve-readback-")
    work=Path(str(work).replace(activity_suffix,"20261012-013"));assert work!=runner._expected_work_path();digest=approve(work,evidence)
    monkeypatch.setattr(sys,"argv",[str(runner.__file__),"execute","--freeze",str(fp),"--freeze-sha256",digest,"--approval",str(ap),"--saved-model",str(runner.SOURCE_MODEL),"--work",str(work),"--evidence",str(evidence)])
    with pytest.raises(ValueError,match="exact frozen activity/revision"):
        runner.main()
    assert not handoffs and not evidence.exists()
    assert "exact frozen activity/revision canonical path" in evidence.with_name(evidence.name+".cli.stderr.bin").read_text()
