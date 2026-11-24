from __future__ import annotations

import base64
import hashlib
import threading
from types import SimpleNamespace
from unittest.mock import patch

import test_t18_java_diagnostic_20261124 as receipts
from comsol_mcp._java_worker import (  # noqa: E402
    JavaWorkerError,
    _LOCAL_PREDISPATCH_STAGES,
    PersistentJavaWorker,
    _new_local_predispatch_context,
    _local_source_binding_refusal,
    _validated_local_predispatch_refusal_reply,
)


def _known_refusal(receipt: dict, code: str, safe_retry: bool) -> None:
    fixture = receipts.make_fixture(receipt)
    result = receipts.invoke_fixture(fixture)
    state = fixture.ledger._state_for(fixture.ref)
    assert result["success"] is False
    assert result["error"]["code"] == code
    assert result["error"]["safe_retry"] is safe_retry
    assert result["data"]["execution_state_unknown"] is False
    assert result["data"]["partial_change"] is False
    assert state.dirty is False and state.revision == 0
    assert state.active_operation_id is None


def test_bound_worker_refusals_keep_known_semantics() -> None:
    compile_unavailable = receipts.compile_failure()
    compile_unavailable["failure"]["code"] = "COMPILE_UNAVAILABLE"
    _known_refusal(receipts.compile_failure(), "COMPILE_ERROR", True)
    _known_refusal(compile_unavailable, "COMPILE_UNAVAILABLE", True)
    _known_refusal(receipts.trusted_code_rejected(), "TRUSTED_CODE_REJECTED", False)
    _known_refusal(receipts.source_binding_failure(), "SOURCE_HASH_MISMATCH", False)


def test_local_connect_refusal_is_known_and_ledger_clean() -> None:
    fixture = receipts.make_fixture(None)
    class FakeJavaWorker(PersistentJavaWorker):
        def backend_snapshot(self, model_tag: str, **_kwargs) -> dict:
            return {"model_tag": model_tag, "fingerprint": "before",
                    "external_event_counter": 0}

    worker = object.__new__(FakeJavaWorker)
    worker._port = 32145
    worker._token = "fake-token"
    worker._startup_unknown = None
    worker._original_process_factory = None
    worker._process_factory = None
    worker._coupled_admission_owner = None
    worker._known_requests = {}
    worker._operation_context = threading.local()
    worker._on_request_event = None
    worker._event_log = []
    fixture.worker = worker
    fixture.backend.worker = worker

    with patch("comsol_mcp._java_worker.socket.create_connection",
               side_effect=ConnectionRefusedError("local fake refusal")) as connect:
        result = receipts.invoke_fixture(fixture)

    state = fixture.ledger._state_for(fixture.ref)
    assert connect.call_count == 1
    assert result["success"] is False
    assert result["error"]["code"] == "WORKER_SOURCE_BINDING_UNAVAILABLE"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["execution_state_unknown"] is False
    assert result["data"]["partial_change"] is False
    assert result["data"]["worker"]["source_operation_dispatched"] is False
    assert state.dirty is False and state.revision == 0
    assert state.active_operation_id is None


def _witness_fixture():
    raw = b"public class Task {}\n"
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = SimpleNamespace(
        raw_bytes=raw, sha256=digest, path=SimpleNamespace(name="Task.java"),
    )
    request = {
        "type": "code_execute", "request_id": "call-1", "tag": "model-A",
        "source_name": "Task.java", "source_snapshot_b64": base64.b64encode(raw).decode("ascii"),
        "expected_source_sha256": digest, "entrypoint": "Task",
    }
    worker = object()
    context = _new_local_predispatch_context(
        worker=worker, request_id="call-1", source_snapshot=snapshot,
        model_tag="model-A", source_sha256=digest, entrypoint="Task",
    )
    error = _local_source_binding_refusal(worker, request, context=context)
    assert error is not None
    expected = {
        "worker": worker, "request_id": "call-1", "operation_type": "code_execute",
        "context": context, "model_tag": "model-A", "source_sha256": digest,
        "entrypoint": "Task", "source_snapshot": snapshot,
    }
    return error, expected


def _request_for(context, request_id: str) -> dict:
    return {
        "type": context.operation_type, "request_id": request_id,
        "tag": context.model_tag, "source_name": context.source_snapshot.path.name,
        "source_snapshot_b64": base64.b64encode(context.source_snapshot.raw_bytes).decode("ascii"),
        "expected_source_sha256": context.source_sha256,
        "entrypoint": context.entrypoint,
    }


def test_local_witness_is_bound_one_use_and_not_created_from_reply_alone() -> None:
    # The accepted T13 witness also covers the source-bound first serialization.
    assert _LOCAL_PREDISPATCH_STAGES == frozenset({"connect", "request_serialization"})
    error, expected = _witness_fixture()
    refusal = _validated_local_predispatch_refusal_reply(error, **expected)
    assert refusal is not None
    assert refusal["request_id"] == "call-1"
    assert refusal["model_tag"] == "model-A"
    assert refusal["source_sha256"] == expected["source_sha256"]
    assert refusal["entrypoint"] == "Task"
    assert _validated_local_predispatch_refusal_reply(error, **expected) is None

    candidate, binding = _witness_fixture()
    context = binding["context"]
    wrong_token = _new_local_predispatch_context(
        worker=binding["worker"], request_id=binding["request_id"],
        source_snapshot=context.source_snapshot, model_tag=context.model_tag,
        source_sha256=context.source_sha256, entrypoint=context.entrypoint,
    )
    assert _validated_local_predispatch_refusal_reply(
        candidate, **{**binding, "context": wrong_token},
    ) is None
    assert _validated_local_predispatch_refusal_reply(candidate, **binding) is None

    candidate, binding = _witness_fixture()
    assert _validated_local_predispatch_refusal_reply(
        candidate, **{**binding, "worker": object()},
    ) is None
    assert _validated_local_predispatch_refusal_reply(candidate, **binding) is not None

    mismatches = (
        {"request_id": "call-2"},
        {"operation_type": "code_compile"},
        {"context": object()},
        {"model_tag": "model-B"},
        {"source_sha256": "f" * 64},
        {"entrypoint": "OtherTask"},
        {"source_snapshot": SimpleNamespace()},
    )
    for changed in mismatches:
        candidate, binding = _witness_fixture()
        wrong = {**binding, **changed}
        assert _validated_local_predispatch_refusal_reply(candidate, **wrong) is None
        assert _validated_local_predispatch_refusal_reply(candidate, **binding) is None

    candidate, binding = _witness_fixture()
    attached_only = JavaWorkerError("reply only", reply=candidate.reply)
    assert _validated_local_predispatch_refusal_reply(attached_only, **binding) is None

    candidate, binding = _witness_fixture()
    candidate.reply["failure"]["source_operation_dispatched"] = True
    assert _validated_local_predispatch_refusal_reply(candidate, **binding) is None
    assert _validated_local_predispatch_refusal_reply(candidate, **binding) is None

    candidate, binding = _witness_fixture()
    candidate.reply["failure"].pop("source_sha256")
    assert _validated_local_predispatch_refusal_reply(candidate, **binding) is None
    assert _validated_local_predispatch_refusal_reply(candidate, **binding) is None


def test_call_token_binds_request_id_and_is_shared_one_use() -> None:
    error, binding = _witness_fixture()
    token = binding["context"]
    assert _local_source_binding_refusal(
        binding["worker"], _request_for(token, "call-2"), context=token,
    ) is None

    same_request_error = _local_source_binding_refusal(
        binding["worker"], _request_for(token, "call-1"), context=token,
    )
    assert same_request_error is not None
    assert _validated_local_predispatch_refusal_reply(error, **binding) is not None
    assert _validated_local_predispatch_refusal_reply(same_request_error, **binding) is None


def test_call_token_has_one_concurrent_consumer() -> None:
    error, binding = _witness_fixture()
    token = binding["context"]
    second_error = _local_source_binding_refusal(
        binding["worker"], _request_for(token, "call-1"), context=token,
    )
    assert second_error is not None

    barrier = threading.Barrier(3)
    results = [None, None]

    def consume(index: int, candidate) -> None:
        barrier.wait()
        results[index] = _validated_local_predispatch_refusal_reply(candidate, **binding)

    threads = [
        threading.Thread(target=consume, args=(0, error)),
        threading.Thread(target=consume, args=(1, second_error)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sum(result is not None for result in results) == 1
