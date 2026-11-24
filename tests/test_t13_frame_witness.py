from __future__ import annotations

import ast
import base64
import contextvars
import hashlib
import json
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT
sys.path.insert(0, str(SOURCE))

from comsol_mcp import _execution_service as execution_service
from comsol_mcp import _g2_code, _java_worker, _managed_backend
from comsol_mcp._domain_outcome import final_state
from comsol_mcp._execution_contract import ModelRef, permission_for_legacy_tool


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


class MemoryStream:
    """In-memory NDJSON peer; it cannot open a socket or reach a Worker."""

    def __init__(self, *, fail_operation_write: bool = False) -> None:
        self.responses: list[bytes] = []
        self.writes: list[dict] = []
        self.operation_attempts = 0
        self.fail_operation_write = fail_operation_write

    def write(self, raw: bytes) -> int:
        request = json.loads(raw.decode("utf-8"))
        self.writes.append(request)
        if request.get("token") == "fake-token":
            self.responses.append(b'{"ok":true}\n')
        elif request.get("type") == "source_binding_capabilities":
            self.responses.append(json.dumps({
                "ok": True,
                "status": "SUCCEEDED",
                "type": "source_binding_capabilities",
                "request_id": request["request_id"],
                "result": {
                    "source_binding_protocol": _java_worker._SOURCE_BINDING_PROTOCOL,
                    "worker_build_id": _java_worker._SOURCE_BINDING_WORKER_BUILD_ID,
                    "instance_id": "fake-instance",
                },
            }).encode() + b"\n")
        else:
            self.operation_attempts += 1
            if self.fail_operation_write:
                raise OSError("fake frame-write failure")
            self.responses.append(json.dumps({
                "ok": False,
                "status": "FAILED",
                "type": request.get("type"),
                "request_id": request.get("request_id"),
                "code": "FAKE_REJECTED",
                "failure": {"code": "FAKE_REJECTED"},
            }).encode() + b"\n")
        return len(raw)

    def flush(self) -> None:
        return None

    def readline(self) -> bytes:
        return self.responses.pop(0) if self.responses else b""


class MemoryConnection:
    def __init__(self, stream: MemoryStream) -> None:
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, _timeout):
        return None

    def makefile(self, *_args):
        return self.stream


class FakeWorker(_java_worker.PersistentJavaWorker):
    pass


def worker_for(stream: MemoryStream, events: list[dict] | None = None) -> FakeWorker:
    worker = object.__new__(FakeWorker)
    worker._port = 43127
    worker._startup_unknown = None
    worker._original_process_factory = None
    worker._process_factory = None
    worker._token = "fake-token"
    worker._generation = 1
    worker._next_generation = 2
    worker._known_requests = {}
    worker._coupled_admission_owner = None
    worker._operation_context = threading.local()
    worker._on_request_event = events.append if events is not None else None
    worker._validate_request_reply = lambda request, reply: (
        isinstance(reply, dict)
        and reply.get("request_id") == request.get("request_id")
        and reply.get("type") == request.get("type")
    )
    worker._sync_generation = lambda _reply: None
    worker.backend_snapshot = lambda model_tag, **_kwargs: {
        "model_tag": model_tag,
        "fingerprint": "fake-fingerprint",
        "external_event_counter": 3,
    }
    return worker


def patch_transport(stream: MemoryStream):
    original = _java_worker.socket.create_connection
    _java_worker.socket.create_connection = lambda *_args, **_kwargs: MemoryConnection(stream)
    return original


def source_identity(worker, *, request_id: str = "frame-req"):
    source = b"public class Main {}\n"
    digest = hashlib.sha256(source).hexdigest()
    snapshot = SimpleNamespace(path=Path("Main.java"), raw_bytes=source, sha256=digest)
    body = {
        "type": "code_execute",
        "request_id": request_id,
        "tag": "model-A",
        "source_name": "Main.java",
        "source_snapshot_b64": base64.b64encode(source).decode("ascii"),
        "expected_source_sha256": digest,
        "entrypoint": "Main",
        "arguments": {},
    }
    context = _java_worker._new_local_predispatch_context(
        worker=worker, request_id=request_id, source_snapshot=snapshot,
        model_tag="model-A", source_sha256=digest, entrypoint="Main",
    )
    return body, snapshot, context, digest


def validator_args(worker, body, snapshot, context, digest):
    return dict(
        worker=worker, request_id=body["request_id"], operation_type="code_execute",
        context=context, model_tag="model-A", source_sha256=digest,
        entrypoint="Main", source_snapshot=snapshot,
    )


def exercise_request_witness_and_managed_service() -> None:
    # Full isolated path: _request -> submit -> ManagedBackend -> ExecutionService.
    stream = MemoryStream()
    events: list[dict] = []
    worker = worker_for(stream, events)
    original = patch_transport(stream)
    try:
        with tempfile.TemporaryDirectory(prefix="t13-frame-", dir=ROOT / "tests") as tmp:
            project = Path(tmp)
            (project / "Main.java").write_text("public class Main {}\n", encoding="utf-8")
            backend = object.__new__(_managed_backend.ManagedBackend)
            backend._project_root_context = contextvars.ContextVar("fake-project-root", default=None)
            backend._project_root_binding_context = contextvars.ContextVar("fake-project-root-binding", default=None)
            backend._base_project_root = project
            backend.worker = worker
            body = {
                "source_artifact": "Main.java",
                "entrypoint": "Main",
                "mode": "trusted",
                "arguments": {"private_marker": object()},
            }
            result = backend._run_g2_action(
                "code.execute_java", "model-A", body, "managed-frame-req",
            )
    finally:
        _java_worker.socket.create_connection = original

    check(result["success"] is False, "managed source serialization must fail")
    check(result["data"]["execution_state_unknown"] is False, "validated local witness stays known")
    check(result["data"]["partial_change"] is False, "validated local witness reports no partial change")
    check(result["error"]["code"] == "WORKER_REQUEST_SERIALIZATION_FAILED", "safe error code survives ManagedBackend")
    check(result["error"]["phase"] == "request_serialization", "safe phase survives ManagedBackend")
    check(result["data"]["worker"]["status"] == "FAILED", "worker projection remains FAILED")
    check(result["data"]["worker"]["transport_stage"] == "request_serialization", "worker stage survives projection")
    check(result["data"]["worker"]["model_tag"] == "model-A", "model identity survives safe projection")
    check(result["data"]["worker"]["failure"]["transport_stage"] == "request_serialization",
          "nested safe failure retains the serialization stage")
    check(stream.operation_attempts == 0, "serialization witness precedes operation-frame write")
    check(len(stream.writes) == 2, "only auth and capability frames were written")
    check(len(events) == 1 and events[0].get("phase") == "binding_refused"
          and events[0].get("status") == "FAILED",
          "Worker event records the witnessed pre-dispatch FAILED")
    check("changed" not in events[0],
          "Worker refusal event does not claim an unrecorded changed value")
    check(worker._known_requests["managed-frame-req"]["status"] == "FAILED", "trusted failure cached as FAILED")
    check("private_marker" not in json.dumps(result), "caller payload value is not exposed")
    direct_execution = _g2_code.execution_result(
        description={"source_sha256": result["data"]["source_sha256"],
                     "entrypoint": result["data"]["entrypoint"]},
        worker_reply=result["data"]["worker"], before=result["data"]["before"],
        expected_model_tag="model-A",
    )
    check(direct_execution["error"]["code"] == "WORKER_REQUEST_SERIALIZATION_FAILED",
          "execution_result retains validated serialization code")
    check(direct_execution["error"]["phase"] == "request_serialization",
          "execution_result retains validated serialization phase")
    check(direct_execution["data"]["partial_change"] is False
          and direct_execution["data"]["execution_state_unknown"] is False,
          "execution_result retains FAILED/no-change state")

    ref = ModelRef("session-A", "server-A", "model-A", 1)

    class State:
        revision = 6
        dirty = False
        active_operation_id = None
        external_event_counter = 3
        fingerprint = "fake-fingerprint"

    class FakeLedger:
        session_id = ref.session_id
        server_instance_id = ref.server_instance_id
        server_ownership = "external"
        permissions = {permission_for_legacy_tool("code_execute_java")}
        model_ownership = {ref.model_tag: "user_owned"}

        def __init__(self):
            self.state = State()
            self.finishes = []

        def _state_for(self, _model_ref):
            return self.state

        def observe_engine_state(self, *_args, **_kwargs):
            return None

        def begin_write(self, *_args, request_id=None, **_kwargs):
            ticket = SimpleNamespace(
                operation_id="memory-ticket", request_id=request_id or "memory-request",
                request_hash="memory-hash",
            )
            self.state.active_operation_id = ticket.operation_id
            return ticket

        def finish(self, _ticket, *, outcome, changed, fingerprint=None):
            self.finishes.append((outcome, changed, fingerprint))
            self.state.active_operation_id = None
            self.state.dirty = outcome in {"partial", "unknown"}
            if changed:
                self.state.revision += 1
                self.state.fingerprint = fingerprint
            return {
                "outcome": outcome, "revision": self.state.revision,
                "dirty": self.state.dirty,
                "partial_change": outcome == "partial",
                "execution_state_unknown": outcome == "unknown",
            }

    class FakeAdapter:
        calls = 0

        def model_snapshot(self, model_tag):
            self.calls += 1
            return {
                "model_tag": model_tag,
                "server_instance_id": ref.server_instance_id,
                "external_event_counter": 3,
                "fingerprint": "fake-fingerprint",
            }

    ledger = FakeLedger()
    adapter = FakeAdapter()
    service = execution_service.ExecutionService(
        ledger, adapter, project_root=ROOT / "tests", on_state_change=lambda _event: None,
    )
    managed = service.execute_legacy(
        "code_execute_java", lambda _args: result, {}, model_ref=ref,
        expected_revision=6, request_id="managed-frame-req",
    )
    check(ledger.finishes == [("failed", False, "fake-fingerprint")], "ExecutionService records failed/no-change")
    check(managed["success"] is False, "ExecutionService does not publish success")
    check(managed["data"]["execution_state_unknown"] is False, "ExecutionService preserves known state")
    check(managed["data"]["partial_change"] is False, "ExecutionService preserves no-change")
    check(managed["error"]["code"] == "WORKER_REQUEST_SERIALIZATION_FAILED", "ExecutionService preserves safe error code")
    check(managed["error"]["phase"] == "request_serialization", "ExecutionService preserves safe stage")
    check(adapter.calls == 2, "known failure uses equal pre/post snapshots")
    check(final_state(result, engine_changed=False) == ("failed", False), "shared final classifier says failed/no-change")


def exercise_token_boundaries() -> None:
    stream = MemoryStream()
    worker = worker_for(stream)
    body, snapshot, context, digest = source_identity(worker, request_id="one-shot")
    body["unencodable"] = object()
    original = patch_transport(stream)
    try:
        try:
            worker._request(body, timeout_s=1.0, require_source_binding=True,
                            predispatch_context=context)
        except _java_worker.JavaWorkerError as exc:
            witness = exc
        else:
            raise AssertionError("source serialization should fail")
    finally:
        _java_worker.socket.create_connection = original

    check(witness.reply["status"] == "FAILED", "private serialization witness is failed")
    check(witness.reply["source_operation_dispatched"] is False, "private witness has exact no-dispatch flag")
    check("unencodable" not in str(witness), "private exception text is generic")
    check(witness.__cause__ is None and witness.__context__ is None, "serializer exception chain is cleared")

    wrong_worker = worker_for(MemoryStream())
    values = validator_args(worker, body, snapshot, context, digest)
    check(_java_worker._validated_local_predispatch_refusal_reply(
        witness, **{**values, "worker": wrong_worker},
    ) is None, "wrong Worker identity cannot consume/accept witness")
    normalized = _java_worker._validated_local_predispatch_refusal_reply(witness, **values)
    check(normalized is not None and normalized["code"] == "WORKER_REQUEST_SERIALIZATION_FAILED",
          "matching identity accepts one canonical witness")
    check(_java_worker._validated_local_predispatch_refusal_reply(witness, **values) is None,
          "a consumed token cannot be reused")

    identity_mismatches = (
        ("request_id", "wrong-request"),
        ("operation_type", "code_compile"),
        ("model_tag", "model-B"),
        ("source_sha256", "0" * 64),
        ("entrypoint", "Other"),
        ("source_snapshot", SimpleNamespace(
            path=Path("Main.java"), raw_bytes=snapshot.raw_bytes, sha256=snapshot.sha256,
        )),
        ("context", _java_worker._new_local_predispatch_context(
            worker=worker, request_id=body["request_id"], source_snapshot=snapshot,
            model_tag="model-A", source_sha256=digest, entrypoint="Main",
        )),
    )
    for field, wrong_value in identity_mismatches:
        identity_worker = worker_for(MemoryStream())
        identity_body, identity_snapshot, identity_context, identity_hash = source_identity(
            identity_worker, request_id=f"identity-{field}",
        )
        identity_body["unencodable"] = object()
        identity_error = _java_worker._local_request_serialization_refusal(
            identity_worker, identity_body, context=identity_context, cause_type="TypeError",
        )
        identity_args = validator_args(identity_worker, identity_body, identity_snapshot,
                                       identity_context, identity_hash)
        identity_args[field] = wrong_value
        check(_java_worker._validated_local_predispatch_refusal_reply(
            identity_error, **identity_args,
        ) is None, f"mismatched {field} identity cannot accept witness")
        check(_java_worker._validated_local_predispatch_refusal_reply(
            identity_error, **validator_args(identity_worker, identity_body,
                                             identity_snapshot, identity_context,
                                             identity_hash),
        ) is None, f"mismatched {field} identity consumes no reusable proof")

    tamper_worker = worker_for(MemoryStream())
    tamper_body, tamper_snapshot, tamper_context, tamper_hash = source_identity(
        tamper_worker, request_id="tampered",
    )
    tamper_body["unencodable"] = object()
    forged = _java_worker._local_request_serialization_refusal(
        tamper_worker, tamper_body, context=tamper_context, cause_type="TypeError",
    )
    forged.reply["code"] = "WORKER_SOURCE_BINDING_UNAVAILABLE"
    tamper_values = validator_args(tamper_worker, tamper_body, tamper_snapshot,
                                   tamper_context, tamper_hash)
    check(_java_worker._validated_local_predispatch_refusal_reply(forged, **tamper_values) is None,
          "tampered certificate is rejected")
    forged.reply["code"] = "WORKER_REQUEST_SERIALIZATION_FAILED"
    check(_java_worker._validated_local_predispatch_refusal_reply(forged, **tamper_values) is None,
          "tampered certificate consumes its token and cannot be repaired/replayed")

    race_worker = worker_for(MemoryStream())
    race_body, race_snapshot, race_context, race_hash = source_identity(
        race_worker, request_id="race",
    )
    race_body["unencodable"] = object()
    race_error = _java_worker._local_request_serialization_refusal(
        race_worker, race_body, context=race_context, cause_type="TypeError",
    )
    competitors = [race_error, _java_worker._local_request_serialization_refusal(
        race_worker, race_body, context=race_context, cause_type="TypeError",
    )]
    barrier = threading.Barrier(2)
    results: list[dict | None] = []

    def consume(error):
        barrier.wait()
        results.append(_java_worker._validated_local_predispatch_refusal_reply(
            error, **validator_args(race_worker, race_body, race_snapshot,
                                    race_context, race_hash),
        ))

    threads = [threading.Thread(target=consume, args=(error,)) for error in competitors]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    check(sum(result is not None for result in results) == 1, "competing exceptions have one winner")


def exercise_unknown_and_callback_boundaries() -> None:
    # Ordinary JavaWorkerError self-report is not a no-dispatch certificate.
    unknown_events: list[dict] = []
    worker = worker_for(MemoryStream(), unknown_events)
    source = b"public class Main {}\n"
    digest = hashlib.sha256(source).hexdigest()
    payload = {
        "tag": "model-A", "source_name": "Main.java",
        "source_snapshot_b64": base64.b64encode(source).decode(),
        "expected_source_sha256": digest, "entrypoint": "Main", "arguments": {},
    }
    context = _java_worker._new_local_predispatch_context(
        worker=worker, request_id="self-report", source_snapshot=SimpleNamespace(
            path=Path("Main.java"), raw_bytes=source, sha256=digest,
        ), model_tag="model-A", source_sha256=digest, entrypoint="Main",
    )

    def ordinary_error(_data, **_kwargs):
        raise _java_worker.JavaWorkerError("untrusted", reply={
            "ok": False, "status": "FAILED", "request_id": "self-report",
            "type": "code_execute", "code": "WORKER_REQUEST_SERIALIZATION_FAILED",
            "transport_stage": "request_serialization",
            "source_operation_dispatched": False,
            "execution_state_unknown": False, "safe_retry": False,
            "failure": {"code": "WORKER_REQUEST_SERIALIZATION_FAILED",
                        "source_operation_dispatched": False,
                        "execution_state_unknown": False, "safe_retry": False},
        })

    worker._request = ordinary_error
    try:
        worker.submit("code_execute", payload, request_id="self-report",
                      _predispatch_context=context)
    except _java_worker.JavaWorkerError as exc:
        check(exc.reply["status"] == "UNKNOWN", "ordinary self-reported refusal becomes UNKNOWN")
        check(exc.reply.get("execution_state_unknown") is True, "ordinary self-report cannot establish known state")
        check("source_operation_dispatched" not in exc.reply, "untrusted no-dispatch assertion is removed")
    else:
        raise AssertionError("ordinary self-reported refusal should raise")
    check(len(unknown_events) == 1
          and unknown_events[0].get("phase") == "unresponsive"
          and unknown_events[0].get("status") == "UNKNOWN",
          "Worker event preserves UNKNOWN for an untrusted self-report")

    # Missing token: serialization still cannot claim FAILED/no-change.
    stream = MemoryStream()
    no_token_worker = worker_for(stream)
    request, _snapshot, _context, _digest = source_identity(no_token_worker, request_id="missing-token")
    request["unencodable"] = object()
    original = patch_transport(stream)
    try:
        try:
            no_token_worker._request(request, timeout_s=1.0, require_source_binding=True)
        except _java_worker.JavaWorkerError as exc:
            check(exc.reply["status"] == "UNKNOWN", "missing token remains UNKNOWN")
            check(exc.reply.get("source_operation_dispatched") is not False,
                  "missing token does not claim no dispatch")
        else:
            raise AssertionError("missing context must not accept a serialization witness")
    finally:
        _java_worker.socket.create_connection = original

    # Callback payload changes still reach the actual operation frame.
    stream = MemoryStream()
    callback_worker = worker_for(stream)
    request, _snapshot, context, _digest = source_identity(callback_worker, request_id="callback-change")
    original = patch_transport(stream)
    try:
        callback_worker._request(
            request, timeout_s=1.0, require_source_binding=True,
            predispatch_context=context,
            before_dispatch=lambda: request.update(callback_payload="changed-by-callback"),
        )
    finally:
        _java_worker.socket.create_connection = original
    operation_frame = next(row for row in stream.writes if row.get("type") == "code_execute")
    check(operation_frame["callback_payload"] == "changed-by-callback",
          "callback payload mutation remains in the final frame")

    # A callback with partial side effects followed by failure is still UNKNOWN.
    stream = MemoryStream()
    callback_worker = worker_for(stream)
    request, _snapshot, context, _digest = source_identity(callback_worker, request_id="callback-failure")
    effects: list[str] = []

    def partial_callback():
        effects.append("audit-side-effect")
        raise RuntimeError("callback private detail")

    original = patch_transport(stream)
    try:
        try:
            callback_worker._request(request, timeout_s=1.0, require_source_binding=True,
                                     predispatch_context=context, before_dispatch=partial_callback)
        except _java_worker.JavaWorkerError as exc:
            check(exc.reply["status"] == "UNKNOWN", "callback exception is UNKNOWN")
            check(exc.reply["transport_stage"] == "before_dispatch_callback", "callback failure stage retained")
            check("callback private detail" not in str(exc), "callback exception text is redacted")
        else:
            raise AssertionError("callback failure should raise")
    finally:
        _java_worker.socket.create_connection = original
    check(effects == ["audit-side-effect"], "partial callback effect is observed once")
    check(stream.operation_attempts == 0, "callback failure does not write or retry operation frame")

    # If the callback makes the frame unserializable, its side effect prevents a no-change witness.
    stream = MemoryStream()
    callback_worker = worker_for(stream)
    request, _snapshot, context, _digest = source_identity(callback_worker, request_id="callback-mutated-frame")
    effects = []

    def mutate_to_unserializable():
        effects.append("audit-side-effect")
        request["unencodable"] = object()

    original = patch_transport(stream)
    try:
        try:
            callback_worker._request(request, timeout_s=1.0, require_source_binding=True,
                                     predispatch_context=context,
                                     before_dispatch=mutate_to_unserializable)
        except _java_worker.JavaWorkerError as exc:
            check(exc.reply["status"] == "UNKNOWN", "post-callback serialization failure is UNKNOWN")
            check(exc.reply["reason"] == "request_serialization_after_callback_failed",
                  "post-callback failure reason is preserved")
            check(exc.reply["transport_stage"] == "request_serialization",
                  "post-callback serialization stage is preserved")
        else:
            raise AssertionError("post-callback serialization failure should raise")
    finally:
        _java_worker.socket.create_connection = original
    check(effects == ["audit-side-effect"], "post-callback effect occurs once")
    check(stream.operation_attempts == 0, "post-callback serialization failure does not write or replay")

    # Once operation-frame write is attempted, transport error stays UNKNOWN and is not replayed.
    stream = MemoryStream(fail_operation_write=True)
    write_worker = worker_for(stream)
    request, _snapshot, context, _digest = source_identity(write_worker, request_id="write-attempt")
    original = patch_transport(stream)
    try:
        try:
            write_worker._request(request, timeout_s=1.0, require_source_binding=True,
                                  predispatch_context=context)
        except _java_worker.JavaWorkerError as exc:
            check(exc.reply["status"] == "UNKNOWN", "frame write attempt is UNKNOWN")
        else:
            raise AssertionError("frame write failure should raise")
    finally:
        _java_worker.socket.create_connection = original
    check(stream.operation_attempts == 1, "frame write failure is attempted once without replay")

    # code_compile remains conservative: the callback precedes encoding and the error is UNKNOWN.
    compile_events: list[dict] = []
    stream = MemoryStream()
    compile_worker = worker_for(stream, compile_events)
    compile_payload = {
        "source_name": "Main.java",
        "source_snapshot_b64": base64.b64encode(source).decode(),
        "expected_source_sha256": digest, "entrypoint": "Main",
        "unencodable": object(),
    }
    original = patch_transport(stream)
    try:
        try:
            compile_worker.submit("code_compile", compile_payload, request_id="compile-conservative")
        except _java_worker.JavaWorkerError as exc:
            check(exc.reply["status"] == "UNKNOWN", "code_compile serialization remains UNKNOWN")
            check(exc.reply["transport_stage"] == "request_serialization", "code_compile stage retained")
        else:
            raise AssertionError("code_compile serialization should raise")
    finally:
        _java_worker.socket.create_connection = original
    check(compile_worker._known_requests["compile-conservative"]["status"] == "UNKNOWN",
          "code_compile cache remains UNKNOWN")
    check(any(event["phase"] == "submitted" for event in compile_events),
          f"code_compile callback ordering remains unchanged: {compile_events!r}")

    # Non-source-bound serialization also remains conservative.
    stream = MemoryStream()
    ordinary_worker = worker_for(stream)
    original = patch_transport(stream)
    try:
        try:
            ordinary_worker.submit("call", {"unencodable": object()}, request_id="ordinary-conservative")
        except _java_worker.JavaWorkerError as exc:
            check(exc.reply["status"] == "UNKNOWN", "non-source-bound serialization remains UNKNOWN")
        else:
            raise AssertionError("non-source-bound serialization should raise")
    finally:
        _java_worker.socket.create_connection = original


def exercise_ast_contracts() -> None:
    files = {
        "worker": SOURCE / "comsol_mcp/_java_worker.py",
        "managed": SOURCE / "comsol_mcp/_managed_backend.py",
        "service": SOURCE / "comsol_mcp/_execution_service.py",
        "code": SOURCE / "comsol_mcp/_g2_code.py",
    }
    trees = {name: ast.parse(path.read_text(encoding="utf-8")) for name, path in files.items()}
    worker_text = files["worker"].read_text(encoding="utf-8")
    submit_tree = trees["worker"]
    submit_method = next(
        node for node in ast.walk(submit_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "submit"
    )
    submit_source = ast.get_source_segment(worker_text, submit_method) or ""
    check("_local_predispatch_error_matches_call" in submit_source,
          "submit requires private local witness helper")
    check("exc.reply.get(\"source_operation_dispatched\") is False" not in submit_source,
          "submit does not trust a plain reply assertion")
    request_method = next(
        node for node in ast.walk(trees["worker"])
        if isinstance(node, ast.FunctionDef) and node.name == "_request"
    )
    request_source = ast.get_source_segment(worker_text, request_method) or ""
    check(request_source.index("preflight_frame =") < request_source.index("before_dispatch()")
          < request_source.index("request_frame = (json.dumps(data")
          < request_source.index("stream.write(request_frame)"),
          "preflight, callback, final serialization, and write remain ordered")
    check('data.get("type") == "code_execute"' in request_source,
          "only code_execute receives the private serialization preflight")
    managed_text = files["managed"].read_text(encoding="utf-8")
    check("_validated_local_predispatch_refusal_reply" in managed_text,
          "ManagedBackend validates and consumes local token")
    check('"request_serialization"' in managed_text and '"WORKER_REQUEST_SERIALIZATION_FAILED"' in managed_text,
          "ManagedBackend carries safe serialization code/stage")
    service_text = files["service"].read_text(encoding="utf-8")
    check("outcome, changed = final_state(data, engine_changed=changed_engine)" in service_text,
          "ExecutionService retains shared final classification")
    check('self.ledger.finish(ticket, outcome=outcome, changed=changed, fingerprint=after["fingerprint"])' in service_text,
          "ExecutionService commits classifier failed/no-change result")
    ephemeral_path = SOURCE / "comsol_mcp/_ephemeral_result_ticket.py"
    ephemeral_tree = ast.parse(ephemeral_path.read_text(encoding="utf-8"))
    scope_class = next(node for node in ephemeral_tree.body
                       if isinstance(node, ast.ClassDef) and node.name == "_Scope")
    before_submit = next(node for node in scope_class.body
                         if isinstance(node, ast.FunctionDef) and node.name == "before_submit")
    writes_body = any(
        isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name) and node.value.id == "body"
        for node in ast.walk(before_submit)
    )
    check(not writes_body, "current production audit callback does not mutate the request payload")
    check(final_state({"success": False, "data": {"partial_change": False,
          "execution_state_unknown": False}, "error": {
              "code": "WORKER_REQUEST_SERIALIZATION_FAILED",
              "phase": "request_serialization", "partial_changes": False,
              "safe_retry": False,
          }}, engine_changed=False) == ("failed", False),
          "known rejected envelope classifies failed/no-change")
    check(_managed_backend._t18_bound_not_executed_rejection({
        "ok": False, "status": "FAILED", "request_id": "plain",
        "type": "code_execute", "code": "WORKER_REQUEST_SERIALIZATION_FAILED",
        "source_operation_dispatched": False, "execution_state_unknown": False,
        "safe_retry": False,
        "failure": {"code": "WORKER_REQUEST_SERIALIZATION_FAILED",
                    "source_operation_dispatched": False,
                    "execution_state_unknown": False, "safe_retry": False},
    }, {"source_sha256": "a" * 64, "entrypoint": "Main"}, "plain", "model-A") is None,
          "ordinary correlated Worker reply cannot substitute for private token")


def test_t13_pre_serialization_event_and_ledger_failed_no_change() -> None:
    exercise_request_witness_and_managed_service()


def test_t13_private_identity_and_single_consumption() -> None:
    exercise_token_boundaries()


def test_t13_untrusted_callback_write_and_compile_remain_unknown() -> None:
    exercise_unknown_and_callback_boundaries()


def test_t13_source_order_and_classifier_contracts() -> None:
    exercise_ast_contracts()


if __name__ == "__main__":
    exercise_request_witness_and_managed_service()
    exercise_token_boundaries()
    exercise_unknown_and_callback_boundaries()
    exercise_ast_contracts()
    print("PASS: managed source serialization witness reaches FAILED/no-change")
    print("PASS: one-shot token identity, tamper, and competing-exception controls")
    print("PASS: ordinary self-report, callback, write, compile, and non-source UNKNOWN controls")
    print("PASS: AST and shared ExecutionService classification controls")
