from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace


from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp import _g2_code
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._java_worker import JavaWorkerError, JavaWorkerTimeout


SOURCE = b"public class NewTask {}\n"
CLIENT_REQUEST_ID = "t18-client-request"
WORKER_REQUEST_ID = "t18-worker-operation"
MODEL_TAG = "mcp1"
PRIVATE_PATH = "/PRIVATE/t18-secret/model.mph"
PRIVATE_TEXT = "IllegalStateException: private-details"


def candidate_backend():
    return ManagedBackend


class InMemorySnapshotAdapter:
    def __init__(self):
        self.calls = []

    def model_snapshot(self, model_tag):
        self.calls.append(("model_snapshot", model_tag))
        return {"model_tag": model_tag, "fingerprint": "before", "external_event_counter": 0}


class FakeWorker:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def backend_snapshot(self, model_tag):
        self.calls.append(("backend_snapshot", model_tag))
        return {"model_tag": model_tag, "fingerprint": "before", "external_event_counter": 0}

    def execute_java(self, *_args, request_id=None, **_kwargs):
        self.calls.append(("execute_java", request_id))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def compile_java(self, *_args, request_id=None, **_kwargs):
        self.calls.append(("compile_java", request_id))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def description():
    digest = hashlib.sha256(SOURCE).hexdigest()
    return SimpleNamespace(
        path=SimpleNamespace(name="NewTask.java"), raw_bytes=SOURCE, sha256=digest,
    ), {
        "source_artifact": "src/NewTask.java", "source_sha256": digest,
        "entrypoint": "NewTask", "bytes": len(SOURCE),
    }


def make_fixture(outcome):
    snapshot, desc = description()
    ledger = SessionLedger(
        "t18-session", "t18-server",
        permissions={"inspect", "project_write", "compute", "trusted_code"},
    )
    ref = ledger.bind_model(MODEL_TAG, fingerprint="before")
    ledger.mark_external_observed(ref, fingerprint="before")
    adapter = InMemorySnapshotAdapter()
    service = ExecutionService(ledger, adapter, project_root="/tmp")
    backend = object.__new__(candidate_backend())
    backend._project_root_context = ContextVar("t18_v3_project_root", default=None)
    backend._project_root_binding_context = ContextVar("t18_v3_project_root_binding", default=None)
    backend._base_project_root = Path("/tmp")
    backend.service = service
    backend.worker = FakeWorker(outcome)
    backend._require_g2_isolation = lambda: {"synthetic_test_only": True}
    arguments = {
        "source_artifact": "src/NewTask.java", "entrypoint": "NewTask",
        "arguments": {}, "mode": "trusted",
    }
    execution = {
        "project_id": "t18-project", "session_id": "t18-session",
        "model_ref": ref.as_dict(), "expected_revision": 0,
        "idempotency_key": "t18-key", "request_id": CLIENT_REQUEST_ID,
    }
    return SimpleNamespace(
        backend=backend, worker=backend.worker, adapter=adapter, ledger=ledger,
        ref=ref, snapshot=snapshot, description=desc, arguments=arguments,
        execution=execution,
    )


def invoke_fixture(fixture, operation="code.execute_java"):
    old_read, old_describe = _g2_code.read_source_snapshot, _g2_code.describe_snapshot
    _g2_code.read_source_snapshot = lambda *_args: fixture.snapshot
    _g2_code.describe_snapshot = lambda *_args: fixture.description
    try:
        return fixture.backend.invoke(
            operation, fixture.arguments, fixture.execution,
            WORKER_REQUEST_ID, lambda _event: None,
        )
    finally:
        _g2_code.read_source_snapshot, _g2_code.describe_snapshot = old_read, old_describe


def success_reply():
    digest = hashlib.sha256(SOURCE).hexdigest()
    return {
        "ok": True, "status": "SUCCEEDED", "type": "code_execute",
        "request_id": WORKER_REQUEST_ID, "_source_binding_verified": True,
        "result": {
            "executed": True, "source_sha256": digest, "entrypoint": "NewTask",
            "model_tag": MODEL_TAG, "worker_build_id": "bound-build",
            "worker_instance_id": "bound-instance", "readback": {"value": 7},
        },
    }


def compile_failure(request_id=WORKER_REQUEST_ID):
    digest = hashlib.sha256(SOURCE).hexdigest()
    return {
        "ok": False, "status": "FAILED", "type": "code_execute",
        "request_id": request_id,
        "failure": {
            "code": "COMPILE_ERROR", "message": "Java compilation failed",
            "source_sha256": digest, "entrypoint": "NewTask",
            "artifact": PRIVATE_PATH,
            "diagnostics": [{
                "kind": "ERROR", "source": "NewTask.java",
                "compilation_source": "NewTask.java", "line": 1,
                "column": 9, "start": 8, "end": 9,
                "message": "Java compiler reported an error",
            }],
        },
    }


def trusted_code_rejected(request_id=WORKER_REQUEST_ID):
    digest = hashlib.sha256(SOURCE).hexdigest()
    return {
        "ok": False, "status": "FAILED", "type": "code_execute",
        "request_id": request_id,
        "failure": {
            "code": "TRUSTED_CODE_REJECTED", "message": "trusted-code policy finding",
            "source_sha256": digest, "entrypoint": "NewTask",
            "diagnostics": [],
            "policy": "javac_ast_resolved_ModelUtil_owner_and_static_initializer",
        },
    }


def source_binding_failure(request_id=WORKER_REQUEST_ID):
    code = "SOURCE_HASH_MISMATCH"
    message = "Worker source snapshot differs from the independently observed source SHA"
    failure = {
        "code": code, "message": message,
        "source_operation_dispatched": False,
        "execution_state_unknown": False, "safe_retry": False,
    }
    return {
        "ok": False, "request_id": request_id, "type": "code_execute",
        "status": "FAILED", "code": code, "message": message,
        "source_operation_dispatched": False,
        "execution_state_unknown": False, "safe_retry": False,
        "failure": failure,
    }


def attach_reply(error, reply):
    error.reply = reply
    return error


def assert_unknown(fixture, result):
    state = fixture.ledger._state_for(fixture.ref)
    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["execution_state_unknown"] is True
    assert result["data"]["partial_change"] is True
    assert result["execution"]["request_id"] == CLIENT_REQUEST_ID
    assert result["data"]["worker"]["request_id"] == WORKER_REQUEST_ID
    assert fixture.adapter.calls == [("model_snapshot", MODEL_TAG)]
    assert fixture.worker.calls == [
        ("backend_snapshot", MODEL_TAG), ("execute_java", WORKER_REQUEST_ID),
    ]
    assert state.active_operation_id is None
    assert state.dirty is True and state.revision == 1


def test_f1_exceptions_cannot_use_success_receipts():
    for error in (
        JavaWorkerError(f"{PRIVATE_TEXT} {PRIVATE_PATH}", reply=success_reply()),
        attach_reply(RuntimeError(f"{PRIVATE_TEXT} {PRIVATE_PATH}"), success_reply()),
    ):
        fixture = make_fixture(error)
        result = invoke_fixture(fixture)
        assert_unknown(fixture, result)
        assert result["data"]["worker"]["status"] == "UNKNOWN"
        assert result["data"]["worker"]["execution_state_unknown"] is True
        assert result["data"]["source_binding_verified"] is False
        public = json.dumps(result, ensure_ascii=False)
        assert PRIVATE_PATH not in public and PRIVATE_TEXT not in public

    contradictory = success_reply()
    contradictory["execution_state_unknown"] = True
    contradictory["failure"] = {
        "code": "ENGINE_CALL_FAILED", "execution_state_unknown": True,
        "message": PRIVATE_TEXT,
    }
    fixture = make_fixture(contradictory)
    result = invoke_fixture(fixture)
    assert_unknown(fixture, result)
    assert result["data"]["worker"]["status"] == "UNKNOWN"

    nested_unknown = success_reply()
    nested_unknown["result"]["execution_state_unknown"] = True
    fixture = make_fixture(nested_unknown)
    result = invoke_fixture(fixture)
    assert_unknown(fixture, result)
    assert result["data"]["worker"]["observed_result"]["execution_state_unknown"] is True


def test_f2_only_bound_normal_compile_rejection_is_known_not_executed():
    fixture = make_fixture(compile_failure())
    result = invoke_fixture(fixture)
    state = fixture.ledger._state_for(fixture.ref)
    assert result["success"] is False
    assert result["error"]["code"] == "COMPILE_ERROR"
    assert result["error"]["safe_retry"] is True
    assert result["data"]["partial_change"] is False
    assert result["data"]["execution_state_unknown"] is False
    assert result["data"]["worker"]["failure"]["diagnostics"] == [{
        "line": 1, "column": 9, "start": 8, "end": 9, "kind": "ERROR",
    }]
    public = json.dumps(result, ensure_ascii=False)
    assert PRIVATE_PATH not in public and PRIVATE_TEXT not in public
    assert "artifact" not in result["data"]["worker"]["failure"]
    assert "message" not in result["data"]["worker"]["failure"]
    assert fixture.adapter.calls == [
        ("model_snapshot", MODEL_TAG), ("model_snapshot", MODEL_TAG),
    ]
    assert fixture.worker.calls == [
        ("backend_snapshot", MODEL_TAG), ("execute_java", WORKER_REQUEST_ID),
    ]
    assert state.active_operation_id is None
    assert state.dirty is False and state.revision == 0

    # Exception class and a compile-shaped .reply are not authenticated evidence.
    untrusted = attach_reply(RuntimeError("compile reply is untrusted"), compile_failure())
    fixture = make_fixture(untrusted)
    assert_unknown(fixture, invoke_fixture(fixture))

    # The request id is checked against the exact id sent to execute_java.
    fixture = make_fixture(compile_failure("another-request"))
    result = invoke_fixture(fixture)
    assert_unknown(fixture, result)
    assert result["data"]["worker"]["observed_request_id"] == "another-request"

    wrong_source = compile_failure()
    wrong_source["failure"]["source_sha256"] = "f" * 64
    fixture = make_fixture(wrong_source)
    assert_unknown(fixture, invoke_fixture(fixture))


def test_reviewer_trusted_code_rejection_is_known_not_executed_not_retryable():
    fixture = make_fixture(trusted_code_rejected())
    result = invoke_fixture(fixture)
    state = fixture.ledger._state_for(fixture.ref)
    assert result["success"] is False
    assert result["error"]["code"] == "TRUSTED_CODE_REJECTED"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["partial_change"] is False
    assert result["data"]["execution_state_unknown"] is False
    assert result["data"]["worker"]["failure"]["diagnostics"] == []
    assert result["data"]["worker"]["failure"]["policy"] == (
        "javac_ast_resolved_ModelUtil_owner_and_static_initializer"
    )
    assert fixture.adapter.calls == [
        ("model_snapshot", MODEL_TAG), ("model_snapshot", MODEL_TAG),
    ]
    assert fixture.worker.calls == [
        ("backend_snapshot", MODEL_TAG), ("execute_java", WORKER_REQUEST_ID),
    ]
    assert state.active_operation_id is None
    assert state.dirty is False and state.revision == 0

    bad_receipts = []
    wrong_source = trusted_code_rejected()
    wrong_source["failure"]["source_sha256"] = "f" * 64
    bad_receipts.append(wrong_source)
    wrong_entrypoint = trusted_code_rejected()
    wrong_entrypoint["failure"]["entrypoint"] = "OtherTask"
    bad_receipts.append(wrong_entrypoint)
    bad_receipts.append(trusted_code_rejected("another-request"))
    contradictory = trusted_code_rejected()
    contradictory["execution_state_unknown"] = True
    bad_receipts.append(contradictory)
    partial = trusted_code_rejected()
    partial["failure"]["partial_change"] = True
    bad_receipts.append(partial)
    tagged_failure = trusted_code_rejected()
    tagged_failure["_source_binding_verified"] = False
    bad_receipts.append(tagged_failure)
    success_flag = trusted_code_rejected()
    success_flag["success"] = False
    bad_receipts.append(success_flag)
    for receipt in bad_receipts:
        fixture = make_fixture(receipt)
        assert_unknown(fixture, invoke_fixture(fixture))


def test_source_binding_refusal_requires_correlated_explicit_not_dispatched_receipt():
    fixture = make_fixture(source_binding_failure())
    result = invoke_fixture(fixture)
    state = fixture.ledger._state_for(fixture.ref)
    assert result["success"] is False
    assert result["error"]["code"] == "SOURCE_HASH_MISMATCH"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["partial_change"] is False
    assert result["data"]["execution_state_unknown"] is False
    assert result["data"]["worker"]["source_operation_dispatched"] is False
    assert result["data"]["worker"]["failure"]["execution_state_unknown"] is False
    assert fixture.adapter.calls == [
        ("model_snapshot", MODEL_TAG), ("model_snapshot", MODEL_TAG),
    ]
    assert fixture.worker.calls == [
        ("backend_snapshot", MODEL_TAG), ("execute_java", WORKER_REQUEST_ID),
    ]
    assert state.active_operation_id is None
    assert state.dirty is False and state.revision == 0

    malformed = []
    missing_dispatch = source_binding_failure()
    del missing_dispatch["source_operation_dispatched"]
    malformed.append(missing_dispatch)
    contradictory_dispatch = source_binding_failure()
    contradictory_dispatch["failure"]["source_operation_dispatched"] = True
    malformed.append(contradictory_dispatch)
    missing_unknown = source_binding_failure()
    del missing_unknown["failure"]["execution_state_unknown"]
    malformed.append(missing_unknown)
    wrong_request = source_binding_failure("another-request")
    malformed.append(wrong_request)
    wrong_type = source_binding_failure()
    wrong_type["type"] = "code_compile"
    malformed.append(wrong_type)
    conflicting_code = source_binding_failure()
    conflicting_code["failure"]["code"] = "SOURCE_SNAPSHOT_INVALID"
    malformed.append(conflicting_code)
    retryable_claim = source_binding_failure()
    retryable_claim["safe_retry"] = True
    malformed.append(retryable_claim)
    for receipt in malformed:
        fixture = make_fixture(receipt)
        assert_unknown(fixture, invoke_fixture(fixture))


def test_mismatched_normal_success_receipts_remain_unknown_without_replay():
    bad_receipts = []
    wrong_request = success_reply()
    wrong_request["request_id"] = "another-request"
    bad_receipts.append(wrong_request)
    wrong_source = success_reply()
    wrong_source["result"]["source_sha256"] = "f" * 64
    bad_receipts.append(wrong_source)
    wrong_entrypoint = success_reply()
    wrong_entrypoint["result"]["entrypoint"] = "OtherTask"
    bad_receipts.append(wrong_entrypoint)
    wrong_model = success_reply()
    wrong_model["result"]["model_tag"] = "other-model"
    bad_receipts.append(wrong_model)
    for receipt in bad_receipts:
        fixture = make_fixture(receipt)
        assert_unknown(fixture, invoke_fixture(fixture))


def test_f3_runtime_and_timeout_are_redacted_unknown_without_replay():
    runtime = {
        "ok": False, "status": "FAILED", "type": "code_execute",
        "request_id": WORKER_REQUEST_ID,
        "failure": {
            "code": "ENGINE_CALL_FAILED", "execution_state_unknown": True,
            "message": f"{PRIVATE_TEXT} {PRIVATE_PATH}",
            "cause_chain": [{"type": "java.lang.IllegalStateException", "message": PRIVATE_TEXT}],
            "diagnostics": [{"line": 12, "message": f"{PRIVATE_PATH} {PRIVATE_TEXT}", "token": "secret"}],
            "artifact": PRIVATE_PATH,
        },
    }
    timeout = JavaWorkerTimeout(
        f"RPC timeout {PRIVATE_PATH}",
        reply={
            "ok": False, "status": "UNKNOWN", "type": "code_execute",
            "request_id": WORKER_REQUEST_ID, "execution_state_unknown": True,
            "transport_stage": "reply_read", "errno": 110,
            "failure": {"code": "ENGINE_UNRESPONSIVE", "message": PRIVATE_TEXT},
        },
    )
    for outcome in (runtime, timeout):
        fixture = make_fixture(outcome)
        result = invoke_fixture(fixture)
        assert_unknown(fixture, result)
        public = json.dumps(result, ensure_ascii=False)
        assert PRIVATE_PATH not in public and PRIVATE_TEXT not in public
        worker = result["data"]["worker"]
        assert "message" not in worker
        assert worker["failure"]["code"] in {"ENGINE_CALL_FAILED", "ENGINE_UNRESPONSIVE"}
        if outcome is runtime:
            assert worker["failure"]["cause_types"] == ["java.lang.IllegalStateException"]
            assert worker["failure"]["diagnostics"] == [
                {"line": 12},
            ]
        else:
            assert worker["transport_stage"] == "reply_read"
            assert worker["errno"] == 110


def test_normal_compile_and_bound_execution_are_not_conflated():
    digest = hashlib.sha256(SOURCE).hexdigest()
    compile_reply = {
        "ok": True, "status": "SUCCEEDED", "type": "code_compile",
        "request_id": WORKER_REQUEST_ID, "_source_binding_verified": True,
        "result": {
            "compiled": True, "source_sha256": digest, "entrypoint": "NewTask",
            "worker_build_id": "bound-build", "worker_instance_id": "bound-instance",
            "diagnostics": [],
        },
    }
    fixture = make_fixture(compile_reply)
    result = invoke_fixture(fixture, "code.compile_java")
    assert result["success"] is True
    assert result["data"]["compiled"] is True
    assert result.get("execution_success") is not True
    assert fixture.worker.calls == [("compile_java", WORKER_REQUEST_ID)]

    fixture = make_fixture(success_reply())
    result = invoke_fixture(fixture)
    state = fixture.ledger._state_for(fixture.ref)
    assert result["success"] is True
    assert result["data"]["execution_success"] is True
    assert result["data"]["worker"]["result"]["executed"] is True
    assert result["data"]["readback"]["readback"] == {"value": 7}
    assert fixture.adapter.calls == [
        ("model_snapshot", MODEL_TAG), ("model_snapshot", MODEL_TAG),
    ]
    assert fixture.worker.calls == [
        ("backend_snapshot", MODEL_TAG), ("execute_java", WORKER_REQUEST_ID),
        ("backend_snapshot", MODEL_TAG),
    ]
    assert state.active_operation_id is None
    assert state.dirty is False and state.revision == 1


if __name__ == "__main__":
    tests = (
        test_f1_exceptions_cannot_use_success_receipts,
        test_f2_only_bound_normal_compile_rejection_is_known_not_executed,
        test_reviewer_trusted_code_rejection_is_known_not_executed_not_retryable,
        test_source_binding_refusal_requires_correlated_explicit_not_dispatched_receipt,
        test_mismatched_normal_success_receipts_remain_unknown_without_replay,
        test_f3_runtime_and_timeout_are_redacted_unknown_without_replay,
        test_normal_compile_and_bound_execution_are_not_conflated,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
