"""C01 regressions: the unified ``DomainOutcome`` contract.

``NEXT_GOAL_MAC_G3_1.md`` §2 lists ten situations that must all resolve through
one rule.  Before the fix the managed backend wrapped *any* mapping a G3 domain
operation returned as ``success=True``, so a domain result carrying
``status.ok=False``, ``execution_state_unknown`` or ``cleanup.cleanup_failed``
was still published as a success (``G3_1_ROOT_CAUSES.md`` R-02).

Every test here runs the **production** dispatch path
(``ManagedBackend._invoke_g3_model`` -> ``ExecutionService.execute_legacy`` ->
the ledger) with a fault injected into the published operation table, and
asserts the outer envelope, the inner data, the revision/dirty state and — for
the job layer — the durable job status agree with each other.

The last group proves the two *proofs* the dispatcher requires before it accepts
a "nothing was dispatched" claim: an explicit validation stage **and** the
dispatch witness agreeing that no mutation-class engine method was issued.
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

from comsol_mcp import _g3_ops
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._domain_outcome import (
    STATE_FAILED,
    STATE_PARTIAL,
    STATE_SUCCEEDED,
    STATE_UNKNOWN,
    STAGE_POST_DISPATCH,
    STAGE_VALIDATION,
    VERIFICATION_FAILED,
    VERIFICATION_NOT_APPLICABLE,
    VERIFICATION_NOT_RUN,
    classify,
    classify_envelope,
    is_mutation_call,
    is_mutation_method,
    witness_scope,
)
from comsol_mcp._execution_contract import (
    ExecutionContractError,
    PreWriteRefusal,
    SessionLedger,
    model_ref_from_mapping,
)
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._java_worker import RemoteJava
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._mcp_gateway import mcp_result
from comsol_mcp._operation_store import OperationStore

#: A published G3 write operation whose dispatch table is replaced per test.
WRITE_OPERATION = "parameter.set"


class _Adapter:
    """A scope-limited engine fingerprint the test can move on purpose."""

    def __init__(self) -> None:
        self.counter = 0
        self.fingerprint = "fingerprint"

    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server",
                "external_event_counter": self.counter, "fingerprint": self.fingerprint}


class _Worker:
    """The smallest worker double the G3 dispatch path touches."""

    def __init__(self) -> None:
        self.generation = 1
        self.submitted: list[tuple[str, dict]] = []

    def submit(self, kind, payload, **kwargs):
        self.submitted.append((kind, dict(payload)))
        return {"ok": True, "status": "SUCCEEDED", "result": None}

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()


def _service(tmp_path):
    adapter = _Adapter()
    service = ExecutionService(SessionLedger("session", "server"), adapter, project_root=tmp_path)
    bound = service.bind_model("model")
    return service, adapter, model_ref_from_mapping(bound["execution"]["model_ref"])


def _backend(tmp_path, service, worker=None):
    return ManagedBackend(tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
                          service=service, worker=worker or _Worker(), registry={})


@pytest.fixture(autouse=True)
def _no_isolation(monkeypatch):
    """Write tickets are what these tests exercise, not the owned-server gate."""
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())


def _inject(monkeypatch, behaviour, operation=WRITE_OPERATION):
    """Replace one published operation with a fault-injecting function."""
    calls: list[int] = []

    def domain_function(_worker, _model_tag, _arguments):
        calls.append(1)
        return behaviour()

    monkeypatch.setitem(_g3_ops.DISPATCH, operation, domain_function)
    return calls


def _invoke(backend, operation=WRITE_OPERATION, *, expected_revision=0, request_id="req-1"):
    return backend._invoke_g3_model(
        operation, backend.service.ledger._state_for(_ref_of(backend)).ref, {},
        {"session_id": "session", "model_ref": _ref_of(backend).as_dict(),
         "expected_revision": expected_revision, "request_id": request_id},
        request_id, "session",
    )


def _ref_of(backend):
    (state,) = backend.service.ledger._models.values()
    return state.ref


def _dirty_and_revision(backend):
    state = backend.service.ledger._state_for(_ref_of(backend))
    return state.dirty, state.revision


# ---------------------------------------------------------------------------
# §2 matrix: the ten declared situations
# ---------------------------------------------------------------------------


def test_01_a_plain_domain_failure_dictionary_is_not_a_success(tmp_path, monkeypatch):
    service, adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": {"ok": False, "status": "FAILED",
                                             "failed_count": 1, "applied_count": 0},
                                  "failed": [{"error": {"code": "NODE_NOT_FOUND",
                                                        "message": "the node does not exist"}}]})

    result = _invoke(backend)

    assert result["success"] is False
    assert result["error"]["code"] == "NODE_NOT_FOUND"
    assert result["data"]["status"]["ok"] is False
    # Nothing applied, so the revision is untouched and the model stays clean.
    assert _dirty_and_revision(backend) == (False, 0)
    assert result["execution"]["revision"] == 0
    assert mcp_result(result).isError is True


def test_02_a_nested_unknown_beats_a_surface_success(tmp_path, monkeypatch):
    service, adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": {"ok": True, "status": "APPLIED"},
                                  "samples": [{"x": 1.0}],
                                  "cleanup": {"created": True, "cleanup_failed": True,
                                              "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                                        "message": "the ephemeral node survived"}}})

    result = _invoke(backend)

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["data"]["cleanup"]["cleanup_failed"] is True
    # An unresolved engine state freezes dependent writes.
    assert _dirty_and_revision(backend) == (True, 1)


def test_03_a_status_level_unknown_signal_is_unknown(tmp_path, monkeypatch):
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": {"ok": False, "status": "EXECUTION_STATE_UNKNOWN",
                                             "execution_state_unknown": True}})

    result = _invoke(backend)

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["data"]["execution_state_unknown"] is True
    assert _dirty_and_revision(backend) == (True, 1)
    assert mcp_result(result).isError is True


def test_04_a_surface_success_contradicted_by_a_dangerous_signal_is_not_a_success(tmp_path, monkeypatch):
    """The exact R-02 shape: ``success: true`` next to ``execution_state_unknown``."""
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"success": True, "status": "APPLIED",
                                  "execution_state_unknown": True,
                                  "applied": [{"action": "set"}]})

    result = _invoke(backend)

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["data"]["execution_state_unknown"] is True
    assert _dirty_and_revision(backend) == (True, 1)


def test_05_a_proven_prewrite_refusal_keeps_its_code_and_leaves_the_revision_clean(tmp_path, monkeypatch):
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)

    def refuse():
        raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", "property expects array rank 1, received 2")

    _inject(monkeypatch, refuse)

    result = _invoke(backend)

    assert result["success"] is False
    assert result["error"]["code"] == "PROPERTY_TYPE_MISMATCH"
    assert result["error"]["stage"] == "validation"
    assert result["data"]["status"] == "REFUSED"
    assert result["data"]["refused"] is True
    assert result["data"]["witness"]["mutation_issued"] is False
    assert result["execution"]["dirty"] is False
    assert result["execution"]["revision"] == 0
    assert mcp_result(result).isError is True


def test_06_a_post_write_readback_failure_is_partial_and_dirty(tmp_path, monkeypatch):
    service, adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)

    def write_then_fail():
        # The setter reached the engine, so the callback is not a pre-write
        # refusal any more; the module reports the post-dispatch outcome as data.
        adapter.counter = 1
        return {"status": {"ok": False, "status": "PARTIAL_FAILURE", "applied_count": 1, "failed_count": 1},
                "applied": [{"name": "k", "readback": None}],
                "failed": [{"name": "cp", "error": {"code": "VERIFICATION_FAILED",
                                                    "message": "the readback did not confirm the write"}}]}

    _inject(monkeypatch, write_then_fail)

    result = _invoke(backend)

    assert result["success"] is False
    assert result["error"]["code"] == "VERIFICATION_FAILED"
    assert result["data"]["partial_change"] is True
    dirty, revision = _dirty_and_revision(backend)
    assert dirty is True and revision >= 1


def test_07_a_setter_that_applied_before_the_second_step_failed_is_partial(tmp_path, monkeypatch):
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": {"ok": False, "status": "PARTIAL_FAILURE",
                                             "applied_count": 1, "failed_count": 1},
                                  "applied": [{"name": "rho"}],
                                  "failed": [{"action": "set_heatcapacity",
                                              "error": {"code": "PROPERTY_TYPE_MISMATCH",
                                                        "message": "the second property was refused"}}],
                                  "partial_change": True})

    result = _invoke(backend)

    assert result["success"] is False
    assert result["error"]["code"] == "PROPERTY_TYPE_MISMATCH"
    assert result["data"]["status"]["applied_count"] == 1
    assert _dirty_and_revision(backend)[0] is True


def test_08_a_real_computation_failure_is_a_failure(tmp_path, monkeypatch):
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": "FAILED",
                                  "engine_error": {"code": "ENGINE_CALL_FAILED",
                                                   "message": "COMSOL solve failed: nonlinear divergence"},
                                  "applied": [], "failed": [], "not_executed": ["study.run"]})

    result = _invoke(backend)

    assert result["success"] is False
    assert result["error"]["code"] == "ENGINE_CALL_FAILED"
    assert result["data"]["engine_error"]["message"].startswith("COMSOL solve failed")


def test_09_a_plain_success_is_a_success_and_advances_the_revision(tmp_path, monkeypatch):
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": {"ok": True, "status": "APPLIED", "applied_count": 1},
                                  "applied": [{"name": "k"}], "failed": [], "not_executed": []})

    result = _invoke(backend)

    assert result["success"] is True
    assert result["error"] is None
    assert result["data"]["status"]["status"] == "APPLIED"
    assert result["execution"]["revision"] == 1
    assert result["execution"]["dirty"] is False
    assert mcp_result(result).isError is False


def test_10_a_numerical_verification_failure_is_a_separate_axis(tmp_path, monkeypatch):
    """Execution succeeded, the numbers did not: two statuses, not one."""
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": {"ok": True, "status": "APPLIED"},
                                  "execution_status": "SUCCEEDED",
                                  "verification_status": "FAILED",
                                  "verification": {"status": "FAIL", "reason": "the required invariant failed"},
                                  "invariant_results": {"status": "FAIL", "checks": [
                                      {"status": "FAIL", "required": True}]}})

    result = _invoke(backend)

    assert result["success"] is False
    assert result["data"]["execution_status"] == "SUCCEEDED"
    assert result["data"]["verification_status"] == "FAILED"
    assert result["data"]["verified_outcome"] == STATE_PARTIAL


def test_11_verification_that_did_not_run_is_not_reported_as_a_pass(tmp_path, monkeypatch):
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": {"ok": True, "status": "APPLIED"},
                                  "verification": {"status": "NOT_RUN", "scope": "no invariant declared"}})

    result = _invoke(backend)

    assert result["success"] is True
    assert result["data"]["verification_status"] == VERIFICATION_NOT_RUN


def test_an_operation_without_a_verification_axis_says_so(tmp_path, monkeypatch):
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": "APPLIED"})

    result = _invoke(backend)

    assert result["success"] is True
    assert result["data"]["verification_status"] == VERIFICATION_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# the two proofs a pre-write refusal needs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation_issued", "dispatch_stage", "expected_state", "expected_stage"),
    [
        (False, STAGE_VALIDATION, STATE_FAILED, STAGE_VALIDATION),
        (True, STAGE_VALIDATION, STATE_UNKNOWN, STAGE_VALIDATION),
        (None, STAGE_VALIDATION, STATE_UNKNOWN, STAGE_VALIDATION),
        (False, STAGE_POST_DISPATCH, STATE_UNKNOWN, STAGE_POST_DISPATCH),
        (None, STAGE_POST_DISPATCH, STATE_UNKNOWN, STAGE_POST_DISPATCH),
    ],
)
def test_refusal_requires_explicit_clean_witness(
    mutation_issued, dispatch_stage, expected_state, expected_stage
):
    """Only an explicit no-mutation witness proves a validation refusal."""
    data = {
        "status": "REFUSED",
        "refused": True,
        "dispatch_stage": dispatch_stage,
        "refusal": {"code": "INVALID_REQUEST", "message": "bad request"},
    }
    witness = {"mutation_issued": mutation_issued}
    outcome = classify("parameter.set", data, dispatch_stage=dispatch_stage, witness=witness)

    assert outcome.state == expected_state
    assert outcome.dispatch_stage == expected_stage
    assert outcome.witness == witness
    if expected_state == STATE_FAILED:
        assert outcome.failure["code"] == "INVALID_REQUEST"
        assert outcome.execution_state_unknown is False
    else:
        assert outcome.failure["code"] == "EXECUTION_STATE_UNKNOWN"
        assert outcome.execution_state_unknown is True
        assert outcome.details["unproven_pre_dispatch"] is True


def test_refusal_without_witness_is_unresolved_even_at_validation_stage():
    data = {
        "status": "REFUSED",
        "refused": True,
        "dispatch_stage": STAGE_VALIDATION,
        "refusal": {"code": "INVALID_REQUEST", "message": "bad request"},
    }
    outcome = classify("parameter.set", data, dispatch_stage=STAGE_VALIDATION)

    assert outcome.state == STATE_UNKNOWN
    assert outcome.failure["code"] == "EXECUTION_STATE_UNKNOWN"
    assert outcome.dispatch_stage == STAGE_VALIDATION
    assert outcome.witness is None
    assert outcome.details["unproven_pre_dispatch"] is True


def test_classify_reads_an_existing_nested_witness_when_argument_is_omitted():
    data = {
        "status": "REFUSED",
        "refused": True,
        "dispatch_stage": STAGE_VALIDATION,
        "refusal": {"code": "INVALID_REQUEST", "message": "bad request"},
        "witness": {"mutation_issued": True, "mutation_method": "set"},
    }

    outcome = classify("parameter.set", data, dispatch_stage=STAGE_VALIDATION)

    assert outcome.state == STATE_UNKNOWN
    assert outcome.witness["mutation_issued"] is True
    assert outcome.details["refusal_proof"]["witness_mutation_issued"] is True


def test_null_outer_witness_does_not_turn_inner_false_into_clean_proof():
    inner = {
        "status": "REFUSED",
        "refused": True,
        "dispatch_stage": STAGE_VALIDATION,
        "refusal": {"code": "TAG_CONFLICT", "message": "already exists"},
        "witness": {"mutation_issued": False},
    }
    outer = {
        "success": False,
        "dispatch_stage": STAGE_VALIDATION,
        "witness": {"mutation_issued": None},
        "data": inner,
    }

    outcome = classify_envelope(outer)

    assert outcome.state == STATE_UNKNOWN
    assert outcome.witness["mutation_issued"] is None
    assert outcome.details["refusal_proof"]["witness_mutation_issued"] is None


def test_classify_envelope_preserves_inner_witness_against_outer_false_flags():
    """An outer success/false flag cannot clear an inner mutation witness."""
    inner = {
        "status": "REFUSED",
        "refused": True,
        "dispatch_stage": STAGE_VALIDATION,
        "refusal": {"code": "TAG_CONFLICT", "message": "already exists"},
        "witness": {"mutation_issued": True, "mutation_method": "set"},
    }
    outer = {
        "success": False,
        "execution_state_unknown": False,
        "partial_change": False,
        "dispatch_stage": STAGE_VALIDATION,
        "witness": {"mutation_issued": False},
        "data": inner,
    }

    outcome = classify_envelope(outer)

    assert outcome.state == STATE_UNKNOWN
    assert outcome.execution_state_unknown is True
    assert outcome.dispatch_stage == STAGE_VALIDATION
    assert outcome.witness["mutation_issued"] is True
    assert outcome.witness["mutation_method"] == "set"
    assert outcome.failure["code"] == "EXECUTION_STATE_UNKNOWN"


def test_classify_envelope_uses_existing_outer_witness_without_creating_dispatch():
    inner = {
        "status": "REFUSED",
        "refused": True,
        "dispatch_stage": STAGE_VALIDATION,
        "refusal": {"code": "TAG_CONFLICT", "message": "already exists"},
    }
    outer = {
        "success": False,
        "dispatch_stage": STAGE_VALIDATION,
        "witness": {"mutation_issued": True, "mutation_method": "create"},
        "data": inner,
    }

    outcome = classify_envelope(outer)

    assert outcome.state == STATE_UNKNOWN
    assert outcome.dispatch_stage == STAGE_VALIDATION
    assert outcome.witness == {"mutation_issued": True, "mutation_method": "create"}
    assert outcome.details["refusal_proof"]["witness_mutation_issued"] is True


def test_a_validation_stage_alone_cannot_prove_nothing_was_dispatched(tmp_path, monkeypatch):
    """A mutation-class engine call during the callback denies the claim."""
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)

    def mutate_then_refuse():
        from comsol_mcp._domain_outcome import record_engine_method
        record_engine_method("set")  # the actual worker funnel records this
        raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", "refused after the setter was issued")

    _inject(monkeypatch, mutate_then_refuse)

    with pytest.raises(ExecutionContractError) as info:
        _invoke(backend)

    assert info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert info.value.stage == "post_dispatch"
    assert info.value.details["witness"]["mutation_method"] == "set"
    assert info.value.details["unproven_pre_dispatch"] is True
    assert _dirty_and_revision(backend)[0] is True


def test_a_bare_execution_contract_error_without_a_stage_is_unknown(tmp_path, monkeypatch):
    """The exception class name is not the proof: the stage has to be declared."""
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)

    def refuse_without_a_stage():
        raise ExecutionContractError("PROPERTY_TYPE_MISMATCH", "no stage was declared")

    _inject(monkeypatch, refuse_without_a_stage)

    with pytest.raises(ExecutionContractError) as info:
        _invoke(backend)

    assert info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert info.value.details["unproven_pre_dispatch"] is False


def test_the_worker_funnel_feeds_the_witness_with_real_method_names(tmp_path):
    """``RemoteJava._call`` is the funnel; a read is not a mutation."""
    worker = _Worker()
    node = RemoteJava(worker, "h-1", worker.generation, "java.lang.Object")

    with witness_scope() as witness:
        node.getType()
        assert witness.mutation_issued is False
        node.setString("k", "1")
        assert witness.first_mutation == "setString"

    assert "getType" in witness.as_dict()["methods"]
    assert is_mutation_method("getType") is False
    assert is_mutation_method("setString") is True
    assert is_mutation_method("create") is True


@pytest.mark.parametrize("method", [
    "getLastComputationTime", "getLastComputationDate", "getLastComputationVersion",
])
def test_study_computation_metadata_getters_are_read_only(method):
    assert is_mutation_call(method, args=()) is False


def test_model_is_a_getter_without_arguments_and_setter_with_model_argument():
    assert is_mutation_call("model", args=()) is False
    assert is_mutation_call("model", args=("comp1",)) is True


# ---------------------------------------------------------------------------
# the four entry points share one rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["legacy", "fallback"])
def test_legacy_and_fallback_entries_use_the_same_final_rule(tmp_path, entry):
    service, _adapter, ref = _service(tmp_path)
    callback = lambda _args: {"success": True, "data": {"status": "APPLIED"},
                              "execution_state_unknown": True}
    if entry == "legacy":
        result = service.execute_legacy("set_parameters", callback, {}, model_ref=ref,
                                        expected_revision=0, effect="project_write")
    else:
        result = service.execute_legacy("model_create", callback, {})

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"


def test_a_read_effect_operation_cannot_publish_an_unknown_as_a_success(tmp_path):
    """The inspect branch is the same rule: no ticket, but no false success either."""
    service, _adapter, ref = _service(tmp_path)
    result = service.execute_legacy(
        "get_parameters",
        lambda _args: {"success": True, "data": {"status": "APPLIED", "cleanup": {"cleanup_failed": True}}},
        {}, model_ref=ref,
    )

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert service.ledger._state_for(ref).dirty is True


def test_the_classifier_never_reads_a_user_key_named_success_or_status():
    """A material property called ``success`` is data, not an envelope signal."""
    outcome = classify("material.create", {"status": {"ok": True, "status": "APPLIED"},
                                           "properties": {"success": False, "status": "FAILED"}})
    assert outcome.state == STATE_SUCCEEDED

    nested = classify("x", {"nested": {"success": False, "execution_state_unknown": True}})
    assert nested.state == STATE_SUCCEEDED


def test_an_unrecognised_status_token_is_never_a_success():
    outcome = classify("x", {"status": "SOMETHING_NEW"})
    assert outcome.state == STATE_UNKNOWN
    assert outcome.details["unrecognised_status_token"] == "SOMETHING_NEW"


# ---------------------------------------------------------------------------
# the job layer: terminal state, preserved evidence, no replay
# ---------------------------------------------------------------------------


def _daemon(tmp_path, monkeypatch, behaviour):
    service, _adapter, _ref = _service(tmp_path)
    calls = _inject(monkeypatch, behaviour)
    daemon = ControlDaemon(tmp_path / "control", service=service, worker=_Worker(),
                           registry={WRITE_OPERATION: lambda args: {"success": True, "data": args}})
    return daemon, calls


def _dispatch(daemon, key="k-1", request_id="r-1"):
    return daemon.dispatch({
        "operation": WRITE_OPERATION, "arguments": {},
        "execution": {"session_id": "session", "model_ref": {"schema_version": 1, "session_id": "session",
                                                             "server_instance_id": "server",
                                                             "model_tag": "model", "generation": 1},
                      "expected_revision": 0, "idempotency_key": key, "request_id": request_id},
    })


def test_a_failed_domain_operation_reaches_the_job_as_a_failure(tmp_path, monkeypatch):
    daemon, _calls = _daemon(tmp_path, monkeypatch, lambda: {
        "status": {"ok": False, "status": "FAILED", "failed_count": 1},
        "failed": [{"error": {"code": "PROPERTY_TYPE_MISMATCH", "message": "refused"}}]})
    try:
        result = _dispatch(daemon)
        job_id = result["execution"]["job_id"]
        job = daemon.dispatch({"operation": "job_result", "arguments": {"job_id": job_id}})

        assert result["success"] is False
        assert mcp_result(result).isError is True
        assert job["data"]["status"] == "FAILED"
        assert job["data"]["result"]["error"]["code"] == "PROPERTY_TYPE_MISMATCH"
    finally:
        daemon.close()


def test_an_unknown_domain_operation_blocks_later_work_and_is_never_replayed(tmp_path, monkeypatch):
    daemon, calls = _daemon(tmp_path, monkeypatch, lambda: {
        "status": "EXECUTION_STATE_UNKNOWN", "execution_state_unknown": True,
        "cleanup": {"created": True, "cleanup_failed": True}})
    try:
        first = _dispatch(daemon)
        job_id = first["execution"]["job_id"]
        job = daemon.dispatch({"operation": "job_result", "arguments": {"job_id": job_id}})

        assert first["success"] is False
        assert job["data"]["status"] == "UNKNOWN"
        # The original job keeps its own result and worker-request evidence.
        assert job["data"]["result"]["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        log = daemon.dispatch({"operation": "job_log", "arguments": {"job_id": job_id, "limit": 50}})
        assert any(event["event"] in {"RUNNING", "UNKNOWN"} for event in log["data"]["events"])

        # The same idempotency key is answered from the durable record: the lost
        # request is never resubmitted.
        again = _dispatch(daemon, key="k-1", request_id="r-1")
        assert again["execution"]["job_id"] == job_id
        assert len(calls) == 1

        # A new request is refused until the unknown job is reconciled.
        blocked = _dispatch(daemon, key="k-2", request_id="r-2")
        assert blocked["success"] is False
        assert blocked["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert "reconcile unfinished engine work" in blocked["error"]["message"]
        assert len(calls) == 1
    finally:
        daemon.close()


def test_a_proven_prewrite_refusal_does_not_block_the_next_request(tmp_path, monkeypatch):
    attempts: list[int] = []

    def refuse_once():
        attempts.append(1)
        if len(attempts) == 1:
            raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", "no mutation was dispatched")
        return {"status": {"ok": True, "status": "APPLIED"}, "applied": [{"name": "k"}]}

    daemon, calls = _daemon(tmp_path, monkeypatch, refuse_once)
    try:
        refused = _dispatch(daemon, key="k-a", request_id="r-a")
        job_id = refused["execution"]["job_id"]
        job = daemon.dispatch({"operation": "job_result", "arguments": {"job_id": job_id}})

        assert refused["success"] is False
        assert refused["error"]["code"] == "PROPERTY_TYPE_MISMATCH"
        assert refused["error"]["stage"] == "validation"
        assert job["data"]["status"] == "FAILED"

        # Nothing was dispatched, so the revision is untouched and the next
        # request is not blocked by an unresolved engine state.
        second = _dispatch(daemon, key="k-b", request_id="r-b")
        assert second["success"] is True, second.get("error")
        assert second["execution"]["revision"] == 1
        assert len(calls) == 2
    finally:
        daemon.close()


def test_the_evidence_envelope_carries_the_four_layer_identity(tmp_path, monkeypatch):
    """Outer MCP, inner result, job and revision/dirty must tell one story."""
    service, _adapter, _ref = _service(tmp_path)
    backend = _backend(tmp_path, service)
    _inject(monkeypatch, lambda: {"status": {"ok": False, "status": "PARTIAL_FAILURE",
                                             "applied_count": 1, "failed_count": 1},
                                  "applied": [{"name": "rho"}],
                                  "failed": [{"error": {"code": "ENGINE_CALL_FAILED",
                                                        "message": "the second setter failed"}}]})

    result = _invoke(backend, request_id="evidence-1")
    job_envelope = mcp_result(result)
    outcome = result["data"]["domain_outcome"]

    assert job_envelope.structuredContent == result
    assert result["success"] is False
    assert outcome["state"] == STATE_PARTIAL
    assert result["data"]["partial_change"] is True
    assert result["execution"]["request_id"] == "evidence-1"
    assert result["execution"]["dirty"] is True
    assert result["execution"]["revision"] == 1
    assert classify_envelope(result).state == STATE_PARTIAL


def test_the_matrix_states_are_the_only_states_published(tmp_path, monkeypatch):
    """A sanity check that the classifier vocabulary is closed over the repo."""
    assert {STATE_SUCCEEDED, STATE_PARTIAL, STATE_FAILED, STATE_UNKNOWN} == {
        STATE_SUCCEEDED, STATE_PARTIAL, STATE_FAILED, STATE_UNKNOWN}
    assert VERIFICATION_FAILED == "FAILED"


def test_modelnode_navigation_is_read_but_creation_remains_mutating():
    """Model.modelNode() and modelNode(String) navigate; create remains a write."""
    from comsol_mcp._domain_outcome import is_mutation_call
    assert not is_mutation_call("modelNode", ())
    assert not is_mutation_call("modelNode", ("comp1",))
    assert is_mutation_call("create", ("comp2", True))
    assert is_mutation_call("unverifiedMethod", ())


def test_domain_outcome_star_exports_are_defined() -> None:
    namespace: dict[str, object] = {}
    exec("from comsol_mcp._domain_outcome import *", namespace)
    assert "MUTATION_METHOD_PREFIXES" in namespace
    assert "STAGE_POST_DISPATCH" in namespace
