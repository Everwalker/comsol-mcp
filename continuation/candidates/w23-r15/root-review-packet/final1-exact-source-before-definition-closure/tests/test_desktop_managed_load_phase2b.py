"""Software fixtures only: real durable store/scheduler, no Desktop/native provider."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import threading
import time
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._desktop_service import DesktopModelObservation, DesktopOperationError
from comsol_mcp._execution_contract import SessionLedger, model_ref_from_mapping, canonical_request_hash
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._session_context import SessionRuntimeConfig, SessionRuntimeContext, SessionEndpointIdentity, use_session_context
from comsol_mcp._session_lifecycle import new_lifecycle_record
from test_desktop_service import FixtureDesktopAdapter


class SoftwareWorker:
    """Injected RPC seam. No process, Java, socket or platform call exists here."""
    def __init__(self):
        self.trace = []
        self.scope = threading.local()
        self.tagset = ["foreign"]
        self.failure = None
        self.fingerprint = "f" * 64
        self.serial = 0
        self.java = self
        self.loaded_tag = None
        self.request_serial = 0

    def health(self):
        return {"connected": True, "instance_id": "worker-b", "generation": 1, "server": "127.0.0.1:2040"}

    @contextmanager
    def operation_context(self, operation_id, on_request_event):
        old = getattr(self.scope, "operation", None)
        old_callback = getattr(self.scope, "callback", None)
        self.scope.operation = operation_id
        self.scope.callback = on_request_event
        try:
            yield
        finally:
            self.scope.operation = old
            self.scope.callback = old_callback

    def observe(self, method, **details):
        self.trace.append({"method": method, "parent": getattr(self.scope, "operation", None),
                           "thread": threading.get_ident(), **details})
        callback = getattr(self.scope, "callback", None)
        if callback is not None:
            self.request_serial += 1
            request = {"request_id": "software-subrequest-" + str(self.request_serial),
                       "request_sha256": sha256(json.dumps({"method": method, "details": details}, sort_keys=True).encode()).hexdigest(),
                       "operation_id": self.scope.operation, "scope": "INJECTED_SOFTWARE_METHOD_ENTRY_ONLY"}
            callback({**request, "phase": "submitted"})
            callback({**request, "phase": "observed", "status": "METHOD_ENTRY_NOT_NATIVE_TERMINAL"})

    def tags(self):
        self.observe("tags")
        if self.failure == "duplicate_tags": return ["foreign", "foreign"]
        if self.failure == "malformed_tags": return [None]
        if self.failure == "extra_delta" and self.loaded_tag: return [*self.tagset, "unowned-extra"]
        if self.failure == "missing_foreign" and self.loaded_tag: return [self.loaded_tag]
        return list(self.tagset)

    def uniquetag(self, prefix):
        self.observe("uniquetag")
        if self.failure == "reused_tag":
            return "foreign"
        self.serial += 1
        return prefix + str(self.serial)

    def load(self, path, *, tag):
        self.observe("load", path=path, tag=tag)
        self.tagset.append(tag)
        self.loaded_tag = tag
        if self.failure == "load":
            raise RuntimeError("injected load failure after tag effect")
        return self

    def tag(self):
        return "wrong-return" if self.failure == "returned_tag" else self.loaded_tag

    def model_snapshot(self, tag):
        self.observe("snapshot", tag=tag)
        if self.failure == "snapshot":
            raise RuntimeError("injected bind snapshot failure")
        return {"model_tag": tag, "server_instance_id": "server-b", "fingerprint": self.fingerprint,
                "external_event_counter": 0}

    def model(self, tag):
        self.observe("receiver_selection", tag=tag)
        raise AssertionError("blocked attempt must not select a receiver")

    def start(self, *a, **kw):
        raise AssertionError("no start")

    connect = start
    remove = start


class Harness:
    def __init__(self, root):
        self.root = root.resolve()
        self.adapter = FixtureDesktopAdapter()
        default = ExecutionService(SessionLedger("default", "server-default", permissions={"inspect", "host_control"}), self, project_root=self.root)
        self.daemon = ControlDaemon(root / "daemon", service=default, desktop_adapter=self.adapter)
        self.adapter.store = self.daemon.store
        self.adapter.models["WINDOW:1"] = DesktopModelObservation(
            "STANDALONE", standalone_model_id="standalone-1", fingerprint="source-fingerprint", dirty=True)
        self.worker = SoftwareWorker()
        self.service = ExecutionService(SessionLedger("target", "server-b", permissions={"inspect", "project_write", "host_control"}),
                                        self.worker, project_root=self.root)
        self.backend = ManagedBackend(root / "target", self.daemon.store, service=self.service,
                                      worker=self.worker, project_root=self.root)
        self.backend.worker_identity = {"runtime_id": "fixture", "worker_instance_id": "worker-b", "connection_epoch": 1,
                                        "server_instance_id": "server-b", "server_ownership": "shared"}
        self.backend.endpoint_key = "127.0.0.1:2040"
        runtime = SessionRuntimeConfig("fixture", "6.4.0.293", root, root / "NOT_EXECUTED-java", (root / "NOT_LOADED.jar",), root, root)
        self.context = SessionRuntimeContext("p", "target", self.root, runtime, SessionEndpointIdentity("127.0.0.1", 2040, 1),
                    backend=self.backend, worker_instance_id="worker-b", worker=self.worker, service=self.service,
                    client=self.worker, client_connected=True, current_model=object(), current_model_origin="foreign", current_model_path="foreign.mph")
        self.daemon.session_registry.register(self.context)
        # Same text tag in A/C must not be mistaken for B's identity.
        default.ledger.bind_model("w25copy1", ownership="user_owned", fingerprint="a" * 64)
        self.foreign = SessionRuntimeContext("foreign-project", "foreign-session", self.root, runtime,
                  SessionEndpointIdentity("127.0.0.1", 2041, 1), current_model=object(),
                  current_model_origin="foreign-C", current_model_path="C.mph")
        self.daemon.session_registry.register(self.foreign)
        self.foreign_pointer = self.foreign.current_model
        self.daemon._session_backends[("p", "target")] = self.backend
        self.daemon._session_worker_handles[("p", "target")] = self.worker
        self.daemon.session_lifecycle.save(new_lifecycle_record(project_id="p", session_id="target", state="CONNECTED", runtime_id="fixture",
             endpoint={"host": "127.0.0.1", "port": 2040}, client_state="CONNECTED", server_state="SHARED", server_ownership="shared",
             worker_instance_id="worker-b", worker_epoch=1, server_instance_id="server-b"))
        # Explicit trusted software project-authority seam, no ACL broadening in product.
        self.daemon.project_authority.authorize_operation = lambda project, permission: {"workspace": self.root} if project == "p" else (_ for _ in ()).throw(ValueError("wrong project"))
        status = self.daemon.dispatch({"operation": "desktop.status", "arguments": {"project_id": "p"}})
        self.window = status["data"]["windows"][0]["window_ref"]
        self.pointer = self.context.current_model
        self.before = self.snapshot()

    def model_snapshot(self, tag):
        raise AssertionError("default backend must not inspect target")

    def request(self, key="copy-1", **changes):
        args = {"project_id": "p", "idempotency_key": key, "request_id": "req-" + key, "window_ref": self.window,
                "target_session_id": "target", "save_policy": {"mode": "save_copy", "destination_path": str(self.root / (key + ".mph")), "overwrite": False}}
        args.update(changes)
        return {"operation": "desktop.migrate_standalone", "arguments": args}

    def migrate(self, request=None):
        return self.daemon.dispatch(request or self.request())

    def attempt(self):
        rows = list(self.daemon._desktop_attempt_rows("p"))
        assert len(rows) == 1
        return rows[0]

    def assert_blocked(self, raw, operation="parameter_set"):
        before = list(self.worker.trace)
        with use_session_context(self.context), pytest.raises(DesktopOperationError, match="unresolved"):
            self.daemon._desktop_before_model_dispatch(operation, {"model_ref": raw}, {})
        assert self.worker.trace == before
        assert self.context.current_model is self.pointer

    def close(self):
        evidence_dir = os.environ.get("W25_PHASE2B_EVIDENCE_DIR")
        if evidence_dir:
            path = Path(evidence_dir) / (self.root.name + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"scope": "INJECTED_SOFTWARE_WORKER_AND_ADAPTER_ONLY", "before": self.before,
                      "after": self.snapshot(), "worker_trace": self.worker.trace}, indent=2) + "\n")
        self.daemon.close()

    def snapshot(self):
        store = self.daemon.store
        return {"operation_count": store.db.execute("SELECT count(*) FROM operations").fetchone()[0],
                "jobs": list(store.list_jobs(project_id="p", limit=100)),
                "events": {j["job_id"]: store.events(j["job_id"], limit=1000) for j in store.list_jobs(project_id="p", limit=100)},
                "revisions": store.list_metadata("revisions"), "sessions": store.list_metadata("sessions"),
                "ledger_models": {tag: {"model_ref": state.ref.as_dict(), "revision": state.revision, "dirty": state.dirty,
                   "fingerprint": state.fingerprint, "active_operation_id": state.active_operation_id}
                    for tag, state in self.service.ledger._models.items()},
                "generations": dict(self.service.ledger._generations),
                "selected_pointer_preserved": self.context.current_model is self.pointer,
                "foreign_pointer_preserved": self.foreign.current_model is self.foreign_pointer,
                "selected_origin": self.context.current_model_origin, "selected_path": self.context.current_model_path,
                "native_fixture_tags": list(self.worker.tagset), "volatile_attempts": self.daemon._desktop_unpersisted_attempts,
                "copy_files": [{"path": str(p), "size": p.stat().st_size, "sha256": sha256(p.read_bytes()).hexdigest()}
                               for p in sorted(self.root.glob("*.mph")) if p.is_file() and not p.is_symlink()]}


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    try:
        yield h
    finally:
        h.close()


def queue_valid_parameter_write(harness, ref, key):
    """Queue a schema-valid legacy mutating operation with its real identity envelope."""
    from comsol_mcp._g2_registry import validate_call

    daemon = harness.daemon
    state = harness.service.ledger._state_for(ref)
    arguments = {"parameters_json": json.dumps([
        {"name": "w25_race_probe", "expression": "1"},
    ])}
    execution = {
        "project_id": "p",
        "session_id": harness.context.session_id,
        "model_ref": ref.as_dict(),
        "expected_revision": state.revision,
        "idempotency_key": key,
        "request_id": key,
    }
    # This is the real registered legacy set_parameters operation and its MCP
    # input schema. Keep the authorizer/backend seams below, but do not admit a
    # made-up operation or an empty argument body as evidence of a queued write.
    validate_call("set_parameters", {**arguments, **execution}, allow_unbound_identity=True)
    request_hash = canonical_request_hash(
        "set_parameters", arguments, ref, state.revision,
        project_id="p", idempotency_key=key, request_id=key,
    )
    record, reused = daemon.store.begin(
        request_id=key, idempotency_key=key, request_hash=request_hash,
        operation="set_parameters", metadata={"project_id": "p"},
    )
    assert not reused
    timeouts = {"queue_timeout_s": None, "execution_timeout_s": None, "progress_timeout_s": None}
    future = daemon.session_scheduler.submit(
        harness.context, daemon._execute, record, "set_parameters", arguments,
        execution, timeouts, time.monotonic(),
    )
    return record, future


def test_one_parent_claim_registered_worker_load_inspect_and_release(harness):
    result = harness.migrate()
    assert result["success"], result
    job, attempt = harness.attempt()
    ref = model_ref_from_mapping(result["data"]["target_model_ref"])
    assert ref.session_id == "target" and ref.server_instance_id == "server-b"
    assert result["data"]["desktop_binding_verified"] is False
    assert job["status"] == "SUCCEEDED" and attempt["state"] == "READY"
    args = harness.request()["arguments"]
    semantic = {k: v for k, v in args.items() if k not in {"request_id", "idempotency_key"}}
    digest = canonical_request_hash("desktop.migrate_standalone", semantic, None, None, project_id="p")
    assert attempt["request_hash"] == digest == harness.daemon.store.get_operation(job["operation_id"])["request_hash"]
    assert harness.daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    assert harness.daemon.store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert {entry["parent"] for entry in harness.worker.trace} == {job["operation_id"]}
    events = [e["metadata"] for e in harness.daemon.store.events(job["job_id"], limit=1000) if e["event"] == "worker_request"]
    submitted = [e for e in events if e["phase"] == "submitted"]
    assert len(submitted) == len(harness.worker.trace)
    assert len({e["request_id"] for e in submitted}) == len(submitted)
    assert all(e["operation_id"] == job["operation_id"] and len(e["request_sha256"]) == 64 for e in submitted)
    assert len({entry["thread"] for entry in harness.worker.trace}) == 1
    assert sum(entry["method"] == "load" for entry in harness.worker.trace) == 1
    assert sum(entry["method"] == "snapshot" for entry in harness.worker.trace) >= 3
    state = harness.service.ledger._state_for(ref)
    assert not state.dirty and state.active_operation_id is None and state.revision == 0
    assert harness.backend.model_project_binding(ref.as_dict()) == {"attribution": "PROJECT_BOUND", "project_id": "p"}
    assert harness.context.current_model is harness.pointer
    assert harness.foreign.current_model is harness.foreign_pointer
    assert harness.adapter.models["WINDOW:1"].mode == "STANDALONE"
    before = list(harness.worker.trace)
    replay = harness.migrate()
    assert replay == result
    assert harness.worker.trace == before


@pytest.mark.parametrize("failure", ["reused_tag", "duplicate_tags", "malformed_tags", "extra_delta", "missing_foreign", "load", "returned_tag", "snapshot", "emit", "persist", "silent_persist", "source_preservation", "tail_persist"])
def test_partial_effects_are_durable_non_ready_never_replayed(harness, monkeypatch, failure):
    harness.worker.failure = failure
    if failure == "emit":
        monkeypatch.setattr(harness.service, "_emit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("injected emit failure")))
    if failure in {"persist", "tail_persist"}:
        original = harness.backend.persist
        def persist():
            states = list(harness.service.ledger._models.values())
            if failure == "persist" or (states and states[0].active_operation_id is None):
                raise RuntimeError("injected persist failure")
            return original()
        monkeypatch.setattr(harness.backend, "persist", persist)
    if failure == "silent_persist": monkeypatch.setattr(harness.backend, "persist", lambda: None)
    if failure == "source_preservation":
        harness.adapter.preserve_source = False
    request = harness.request()
    result = harness.migrate(request)
    assert not result["success"], result
    job, attempt = harness.attempt()
    assert attempt["state"] == "NON_READY"
    assert attempt["durable_guard"] == "ATTEMPT_NON_READY_RECORDED"
    assert attempt["cause"]["message"]
    assert job["status"] == ("SUCCEEDED" if failure == "tail_persist" else "UNKNOWN")
    trace = list(harness.worker.trace)
    replay = harness.migrate(request)
    assert not replay["success"] and harness.worker.trace == trace
    new_key = harness.migrate(harness.request("different-key"))
    assert not new_key["success"] and harness.worker.trace == trace
    if attempt.get("model_ref"):
        raw = attempt["model_ref"]
        state = harness.service.ledger._state_for(model_ref_from_mapping(raw))
        assert state.dirty and state.active_operation_id == job["operation_id"]
        harness.assert_blocked(raw)
        alias = {**raw, "generation": raw["generation"] + 10}
        harness.assert_blocked(alias)
        harness.assert_blocked({**raw, "server_instance_id": "forged-server"})
        harness.assert_blocked({**raw, "session_id": "forged-session"})
        # A stale clean revision row cannot overrule durable owned-attempt metadata.
        key = harness.backend._model_project_key(raw)
        harness.daemon.store.put_metadata("revisions", key, {"model_ref": raw, "dirty": False, "revision": 0, "fingerprint": "f" * 64, "project_id": "p"})
        harness.assert_blocked(raw)
    harness.assert_blocked(None, "model_load")
    assert harness.context.current_model is harness.pointer


@pytest.mark.parametrize("bad", ["missing", "retired", "disconnected", "epoch", "table", "lifecycle", "worker_health"])
def test_target_capture_refuses_before_any_worker_call(harness, monkeypatch, bad):
    if bad == "missing":
        request = harness.request(target_session_id="not-registered")
    else:
        request = harness.request()
    if bad == "retired": harness.context.client_retired = True
    if bad == "disconnected": harness.context.client_connected = False
    if bad == "epoch": harness.backend.worker_identity["connection_epoch"] = 2
    if bad == "table": harness.daemon._session_backends.pop(("p", "target"))
    if bad == "lifecycle": harness.daemon.session_lifecycle.save(new_lifecycle_record(project_id="p", session_id="target", state="NEW"))
    if bad == "worker_health": monkeypatch.setattr(harness.worker, "health", lambda: {"connected": False})
    result = harness.migrate(request)
    assert not result["success"]
    assert harness.worker.trace == []
    assert harness.service.ledger._models == {}
    assert harness.context.current_model is harness.pointer


def test_reopened_store_guard_survives_stale_clean_registry(harness):
    harness.worker.failure = "snapshot"
    assert not harness.migrate()["success"]
    job, attempt = harness.attempt()
    raw = attempt["model_ref"]
    from comsol_mcp._operation_store import OperationStore
    reopened = OperationStore(harness.daemon.store.path)
    old = harness.daemon.store
    try:
        harness.daemon.store = reopened
        state = harness.service.ledger._state_for(model_ref_from_mapping(raw))
        state.dirty, state.active_operation_id, state.fingerprint = False, None, "f" * 64
        harness.assert_blocked(raw)
        assert reopened.operation_job(job["operation_id"])["metadata"]["desktop_copy_load"]["state"] == "NON_READY"
    finally:
        harness.daemon.store = old
        reopened.close()


@pytest.mark.parametrize("stage", ["finish_before", "finish_after", "service_receipt"])
def test_outer_tail_failure_freezes_ref_without_overwriting_terminal(harness, monkeypatch, stage):
    store = harness.daemon.store
    original = store.finish
    def finish(operation_id, **kwargs):
        if stage == "finish_after":
            original(operation_id, **kwargs)
        raise RuntimeError("injected outer finish failure")
    if stage == "service_receipt":
        original_complete = harness.daemon.desktop._complete_pending_binding
        def complete(operation_id, *, succeeded):
            if succeeded:
                raise RuntimeError("injected service receipt failure")
            return original_complete(operation_id, succeeded=succeeded)
        monkeypatch.setattr(harness.daemon.desktop, "_complete_pending_binding", complete)
    else:
        monkeypatch.setattr(store, "finish", finish)
    result = harness.migrate()
    assert not result["success"] and result["data"]["target_readiness"] == "NON_READY"
    job, attempt = harness.attempt()
    assert job["status"] == ("RUNNING" if stage == "finish_before" else "SUCCEEDED")
    assert result["data"]["parent_persisted_status"] == job["status"]
    assert attempt["state"] == "NON_READY" and attempt["cause"]["message"]
    harness.assert_blocked(attempt["model_ref"])
    trace = list(harness.worker.trace)
    replay = harness.migrate()
    assert not replay["success"] and harness.worker.trace == trace


def test_attempt_persistence_error_reports_unverified_and_preserves_volatile_guard(harness, monkeypatch):
    original = harness.daemon._desktop_save_attempt
    def save(record, attempt):
        if attempt["state"] == "NON_READY":
            raise RuntimeError("injected durable attempt storage failure")
        return original(record, attempt)
    monkeypatch.setattr(harness.daemon, "_desktop_save_attempt", save)
    harness.worker.failure = "snapshot"
    result = harness.migrate()
    assert not result["success"]
    volatile = next(iter(harness.daemon._desktop_unpersisted_attempts.values()))
    assert volatile["durable_guard"] == "UNVERIFIED"
    assert volatile["secondary_errors"][-1]["stage"] == "attempt_persist"
    harness.assert_blocked(volatile["model_ref"])


@pytest.mark.parametrize("change", ["retire", "replace_table", "file_bytes", "symlink", "hash_receipt", "foreign_pointer"])
def test_queued_recheck_and_file_identity_refuse_effects(harness, monkeypatch, change):
    original = harness.daemon._desktop_queued_load_copy
    def callback(arguments, parent, capture, scope, witness):
        if change == "retire": harness.context.client_retired = True
        if change == "replace_table": harness.daemon._session_backends[("p", "target")] = object()
        if change == "file_bytes": Path(witness["path"]).write_bytes(b"changed copy")
        if change == "symlink":
            path = Path(witness["path"])
            path.rename(path.with_suffix(".saved"))
            path.symlink_to(path.with_suffix(".saved"))
        if change == "hash_receipt": arguments["source_sha256"] = "0" * 64
        if change == "foreign_pointer":
            old_load = harness.worker.load
            def load(*a, **kw):
                harness.context.current_model = object()
                return old_load(*a, **kw)
            monkeypatch.setattr(harness.worker, "load", load)
        return original(arguments, parent, capture, scope, witness)
    monkeypatch.setattr(harness.daemon, "_desktop_queued_load_copy", callback)
    result = harness.migrate()
    assert not result["success"]
    if change != "foreign_pointer":
        assert harness.worker.trace == []
    else:
        job, attempt = harness.attempt()
        assert attempt["state"] == "NON_READY"
        assert "selected pointer changed" in attempt["cause"]["message"]


@pytest.mark.parametrize("field", ["operation_id", "job_id", "operation", "request_id", "request_hash", "idempotency_key", "project_id"])
def test_exact_parent_context_rechecked_inside_lane(harness, monkeypatch, field):
    from dataclasses import replace
    original = harness.daemon._desktop_queued_load_copy
    def callback(arguments, parent, capture, scope, witness):
        return original(arguments, replace(parent, **{field: "wrong"}), capture, scope, witness)
    monkeypatch.setattr(harness.daemon, "_desktop_queued_load_copy", callback)
    result = harness.migrate()
    assert not result["success"] and harness.worker.trace == []
    assert harness.service.ledger._models == {}


def test_bind_clean_interval_is_serial_and_core_write_is_rejected_before_receiver(harness, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original_emit = harness.service._emit
    def emit(event, *a, **kw):
        if event == "bound":
            entered.set()
            assert release.wait(5)
        return original_emit(event, *a, **kw)
    monkeypatch.setattr(harness.service, "_emit", emit)
    daemon = harness.daemon
    monkeypatch.setattr(daemon, "_authorize_project_execution", lambda *a, **kw: None)
    monkeypatch.setattr(daemon.project_authority, "apply_timeout_caps", lambda project, timeouts: timeouts)
    monkeypatch.setattr(daemon.project_authority, "get_project", lambda project: {"workspace": harness.root})
    def invoke(*a, **kw):
        harness.worker.model("must-not-select")
    monkeypatch.setattr(harness.backend, "invoke", invoke)
    with ThreadPoolExecutor(max_workers=1) as executor:
        migration = executor.submit(harness.migrate)
        assert entered.wait(5)
        state = next(iter(harness.service.ledger._models.values()))
        assert state.active_operation_id is None  # actual bind emission clean interval
        _record, write = queue_valid_parameter_write(harness, state.ref, "write")
        assert not write.done()
        release.set()
        rejected = write.result(5)
        result = migration.result(5)
    assert not rejected["success"]
    assert result["success"], result
    assert not any(t["method"] == "receiver_selection" for t in harness.worker.trace)
    assert harness.context.current_model is harness.pointer
    assert daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 2


@pytest.mark.parametrize("bad", ["owner_missing", "terminal_parent", "owned_tag_prior", "retirement_lane", "closed_scheduler"])
def test_owner_and_prior_identity_or_retirement_refuse(harness, monkeypatch, bad):
    if bad == "owned_tag_prior": harness.context.owned_model_tags.add("w25copy1")
    if bad == "retirement_lane":
        with harness.daemon.session_scheduler._lock:
            harness.daemon.session_scheduler._closed_session_bindings.add(harness.context.worker_binding_key)
    if bad == "closed_scheduler": harness.daemon.session_scheduler.close()
    if bad in {"owner_missing", "terminal_parent"}:
        original = harness.daemon._desktop_queued_load_copy
        def callback(arguments, parent, capture, scope, witness):
            if bad == "owner_missing":
                with harness.daemon.desktop._operation_lock:
                    harness.daemon.desktop._active_operations.discard(parent.operation_id)
            else:
                harness.daemon.store.update_job(parent.job_id, "UNKNOWN")
            return original(arguments, parent, capture, scope, witness)
        monkeypatch.setattr(harness.daemon, "_desktop_queued_load_copy", callback)
    result = harness.migrate()
    assert not result["success"]
    assert not any(t["method"] == "load" for t in harness.worker.trace)


@pytest.mark.parametrize("bad", ["dirty", "active", "retired_context", "missing_context", "worker_health", "wrong_revision_row", "lost_attempt_metadata"])
def test_same_key_replay_checks_current_readiness_without_rpc(harness, monkeypatch, bad):
    first = harness.migrate()
    assert first["success"], first
    job, attempt = harness.attempt()
    raw = attempt["model_ref"]
    state = harness.service.ledger._state_for(model_ref_from_mapping(raw))
    if bad == "dirty": state.dirty = True
    if bad == "active": state.active_operation_id = "another-parent"
    if bad == "retired_context": harness.context.client_retired = True
    if bad == "missing_context": harness.daemon.session_registry.remove("p", "target", expected=harness.context)
    if bad == "worker_health": monkeypatch.setattr(harness.worker, "health", lambda: {"connected": False})
    if bad == "wrong_revision_row": harness.daemon.store.put_metadata("revisions", harness.backend._model_project_key(raw), {"model_ref": raw, "revision": 99})
    if bad == "lost_attempt_metadata":
        harness.daemon.store.update_job(job["job_id"], "SUCCEEDED", {"desktop_copy_load": None})
    trace = list(harness.worker.trace)
    replay = harness.migrate()
    assert not replay["success"] and replay["data"]["parent_persisted_status"] == "SUCCEEDED"
    assert harness.daemon.store.get_operation(job["operation_id"])["status"] == "SUCCEEDED"
    assert harness.worker.trace == trace


def test_ambient_foreign_context_does_not_route_load_or_inspection(harness):
    with use_session_context(harness.foreign):
        result = harness.migrate()
    assert result["success"], result
    assert result["data"]["target_model_ref"]["session_id"] == "target"
    assert harness.foreign.current_model is harness.foreign_pointer
    assert not any(t["method"] == "receiver_selection" for t in harness.worker.trace)


def test_foreign_pointer_change_after_post_inspect_prevents_release(harness, monkeypatch):
    original = harness.adapter.verify_source_preserved
    def preserve(*a, **kw):
        harness.foreign.current_model = object()
        return original(*a, **kw)
    monkeypatch.setattr(harness.adapter, "verify_source_preserved", preserve)
    result = harness.migrate()
    assert not result["success"] and result["data"]["parent_persisted_status"] == "SUCCEEDED"
    job, attempt = harness.attempt()
    assert attempt["state"] == "NON_READY" and "selected pointer changed" in attempt["cause"]["message"]
    assert job["status"] == "SUCCEEDED"


@pytest.mark.parametrize("corruption", ["revision_ref", "session_ref", "session_worker", "session_server", "saved_ownership", "project", "ownership", "post_inspect"])
def test_independent_persisted_identity_and_post_inspect_failures(harness, monkeypatch, corruption):
    original = harness.backend.persist
    def persist():
        original()
        if not harness.service.ledger._models:
            return
        state = next(iter(harness.service.ledger._models.values()))
        ref = state.ref.as_dict()
        store = harness.daemon.store
        key = harness.backend._model_project_key(ref)
        if corruption in {"revision_ref", "project"}:
            row = store.get_metadata("revisions", key)
            if corruption == "revision_ref": row["model_ref"] = {**ref, "server_instance_id": "foreign"}
            else: row["project_id"] = "foreign-project"
            store.put_metadata("revisions", key, row)
        if corruption in {"session_ref", "session_worker", "session_server", "saved_ownership"}:
            row = store.get_metadata("sessions", ref["session_id"])
            if corruption == "session_ref": row["models"][ref["model_tag"]]["ref"] = {**ref, "generation": 99}
            elif corruption == "session_worker": row["worker_identity"]["connection_epoch"] = 99
            elif corruption == "session_server": row["worker_identity"]["server_instance_id"] = "foreign-server"
            else: row["models"][ref["model_tag"]]["ownership"] = "user_owned"
            store.put_metadata("sessions", ref["session_id"], row)
        if corruption == "ownership": harness.service.ledger.model_ownership[ref["model_tag"]] = "user_owned"
    if corruption == "post_inspect":
        snapshot = harness.worker.model_snapshot
        def observed(tag):
            if sum(t["method"] == "snapshot" for t in harness.worker.trace) >= 2:
                raise RuntimeError("injected final private inspection failure")
            return snapshot(tag)
        monkeypatch.setattr(harness.worker, "model_snapshot", observed)
    else:
        monkeypatch.setattr(harness.backend, "persist", persist)
    result = harness.migrate()
    assert not result["success"]
    job, attempt = harness.attempt()
    # Phase2c keeps the already-marked load effect sticky across the READ
    # wrapper. Frozen Phase2b's parent FAILED remains historical evidence.
    assert job["status"] == "UNKNOWN"
    assert attempt["state"] == "NON_READY"
    assert attempt["cause"]["message"]
    state = harness.service.ledger._state_for(model_ref_from_mapping(attempt["model_ref"]))
    assert state.dirty and state.active_operation_id == job["operation_id"]
    harness.assert_blocked(attempt["model_ref"])


def test_foreign_parent_read_cannot_inspect_provisional_ref(harness, monkeypatch):
    from comsol_mcp._desktop_service import DesktopDispatchContext
    entered, release = threading.Event(), threading.Event()
    original = harness.adapter.verify_source_preserved
    def preservation(*a, **kw):
        entered.set()
        assert release.wait(5)
        return original(*a, **kw)
    monkeypatch.setattr(harness.adapter, "verify_source_preserved", preservation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        migration = executor.submit(harness.migrate)
        assert entered.wait(5)
        job, attempt = harness.attempt()
        record, reused = harness.daemon.store.begin(request_id="foreign-read", idempotency_key="foreign-read", request_hash="foreign-hash",
                                                   operation="desktop.bind", metadata={"project_id": "p"})
        harness.daemon.store.update_job(record["job_id"], "RUNNING")
        context = DesktopDispatchContext(operation_id=record["operation_id"], job_id=record["job_id"], operation=record["operation"],
                                         request_id=record["request_id"], idempotency_key=record["idempotency_key"], request_hash=record["request_hash"], project_id="p")
        before = list(harness.worker.trace)
        try:
            with pytest.raises(DesktopOperationError, match="active operation"):
                harness.daemon._desktop_engine_queue("desktop.binding.inspect", {"project_id": "p"}, model_ref_from_mapping(attempt["model_ref"]), context)
            assert harness.worker.trace == before
        finally:
            release.set()
        assert migration.result(5)["success"]


def test_ready_writeback_failure_freezes_before_same_lane_write_runs(harness, monkeypatch):
    daemon = harness.daemon
    queued_writes = []
    original_save = daemon._desktop_save_attempt

    monkeypatch.setattr(daemon, "_authorize_project_execution", lambda *a, **kw: None)
    monkeypatch.setattr(daemon.project_authority, "apply_timeout_caps", lambda project, timeouts: timeouts)
    monkeypatch.setattr(daemon.project_authority, "get_project", lambda project: {"workspace": harness.root})
    monkeypatch.setattr(harness.backend, "invoke", lambda *a, **kw: harness.worker.model("must-not-select"))

    def save_then_uncertain_ready(record, attempt):
        original_save(record, attempt)
        if attempt.get("state") == "READY":
            target = next(iter(harness.service.ledger._models.values()))
            _write_record, future = queue_valid_parameter_write(
                harness, target.ref, "ready-window-write",
            )
            queued_writes.append(future)
            # Persistence succeeded; make the callback observe an uncertain return path.
            raise RuntimeError("injected READY readback uncertainty")

    monkeypatch.setattr(daemon, "_desktop_save_attempt", save_then_uncertain_ready)
    result = harness.migrate()
    assert not result["success"]
    assert result["data"]["parent_persisted_status"] == "SUCCEEDED"
    assert len(queued_writes) == 1
    rejected = queued_writes[0].result(5)
    assert not rejected["success"]
    job, attempt = harness.attempt()
    assert job["status"] == "SUCCEEDED"
    assert attempt["state"] == "NON_READY"
    assert attempt["durable_guard"] == "ATTEMPT_NON_READY_RECORDED"
    assert attempt["cause"]["message"] == "injected READY readback uncertainty"
    assert all(entry["method"] != "receiver_selection" for entry in harness.worker.trace)
    target = next(iter(harness.service.ledger._models.values()))
    assert target.dirty and target.fingerprint is None
    assert target.active_operation_id == job["operation_id"]
