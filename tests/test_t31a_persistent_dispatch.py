from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._session_context import SessionIdentityConflict
from comsol_mcp._session_lifecycle import SessionLifecycleStore, new_lifecycle_record


class _FakeAdapter:
    def model_snapshot(self, tag):
        return {
            "model_tag": tag,
            "server_instance_id": "server",
            "fingerprint": "fake-fingerprint",
            "external_event_counter": 0,
        }


class _FakeWorker:
    def __init__(self):
        self.calls = []

    def __call__(self, arguments):
        self.calls.append(dict(arguments))
        return {"success": True, "data": {"accepted": dict(arguments)}}


def _home() -> Path:
    path = Path(tempfile.mkdtemp(prefix="t31a-persistent-", dir="/private/tmp"))
    (path / "workspace").mkdir()
    return path


def _service(home: Path, token: str) -> ExecutionService:
    return ExecutionService(
        SessionLedger(f"session-{token}", "server"),
        _FakeAdapter(),
        project_root=home / "workspace",
    )


def _request(service: ExecutionService, token: str) -> dict:
    binding = service.bind_model(f"model-{token}")
    model_ref = binding["execution"]["model_ref"]
    return {
        "operation": "set_parameters",
        "arguments": {"x": token},
        "execution": {
            "model_ref": model_ref,
            "expected_revision": 0,
            "idempotency_key": f"t31a-key-{token}",
            "request_id": f"t31a-request-{token}",
        },
    }


def _spy_submit(scheduler):
    calls = []
    original = scheduler.submit

    def submit(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    scheduler.submit = submit
    return calls


def _install_fake_worker(daemon: ControlDaemon, fake_worker: _FakeWorker) -> None:
    def execute(record, _operation, arguments, _execution, _timeouts, _submitted, **_kwargs):
        return daemon._finish(record, fake_worker(arguments), "SUCCEEDED")

    daemon._execute = execute


def _late_result_fixture(tmp_path, token):
    home = tmp_path / f"late-result-{token}"
    home.mkdir()
    (home / "workspace").mkdir()
    worker = _FakeWorker()
    service = _service(home, token)
    request = _request(service, token)
    daemon = ControlDaemon(home, service=service, registry={"set_parameters": worker})
    _install_fake_worker(daemon, worker)
    return daemon, worker, request


def _record_from_result(result):
    execution = result["execution"]
    return {key: execution[key] for key in (
        "request_id", "operation_id", "request_hash", "idempotency_key", "job_id",
    )}


def _install_late_result_insert_failure(daemon):
    databases = daemon.store.db.execute("PRAGMA database_list").fetchall()
    database_path = next(row[2] for row in databases if row[1] == "main")
    assert database_path and Path(database_path).is_file(), database_path
    daemon.store.db.execute(
        "CREATE TRIGGER t35_fail_late_result_insert "
        "BEFORE INSERT ON job_events WHEN NEW.event='LateResultRecorded' "
        "BEGIN SELECT RAISE(ABORT, 'synthetic T35 late-result insert failure'); END"
    )
    daemon.store.db.commit()


def test_late_finish_event_and_log_fault_returns_authoritative_result_and_same_key(tmp_path, monkeypatch):
    token = uuid4().hex
    daemon, worker, request = _late_result_fixture(tmp_path, token)
    release_calls = []
    daemon._release_terminal_resource_reservations = lambda job_id: (
        release_calls.append(job_id) or {"state": "COMPLETE"}
    )
    try:
        first = daemon.dispatch(request)
        record = _record_from_result(first)
        durable_before = daemon.store.job(record["job_id"])
        assert durable_before["status"] == "SUCCEEDED"
        assert durable_before["result"] == first

        original_add_event = daemon.store.add_event
        failed_event_writes = []

        def fail_late_result_event(job_id, event, metadata=None):
            if event == "LateResultRecorded":
                failed_event_writes.append((job_id, event))
                raise OSError("injected late-result event write failure")
            return original_add_event(job_id, event, metadata)

        def fail_log_write():
            raise OSError("injected control error log write failure")

        monkeypatch.setattr(daemon.store, "add_event", fail_late_result_event)
        monkeypatch.setattr(daemon, "_log_exception", fail_log_write)
        late = daemon._finish(
            record,
            {"success": False, "error": {"code": "LATE_CANDIDATE"}},
            "FAILED",
        )

        assert failed_event_writes == [(record["job_id"], "LateResultRecorded")]
        assert late["success"] is True
        assert late["data"] == first["data"]
        warning = late["execution"]["late_result_warning"]
        assert warning == {
            "code": "LATE_RESULT_TRACE_WRITE_FAILED",
            "failed_steps": ["late_result_event_write", "control_errors_log_write"],
            "diagnostic_durable": False,
            "durability_scope": "response_only",
            "authoritative_status": "SUCCEEDED",
            "event_error_type": "OSError",
            "log_error_type": "OSError",
            "log_attempt": "FAILED",
        }
        assert daemon.store.job(record["job_id"])["status"] == "SUCCEEDED"
        assert daemon.store.job(record["job_id"])["result"] == first
        assert release_calls == [record["job_id"]]

        duplicate = daemon.dispatch(request)
        assert duplicate == first
        assert worker.calls == [{"x": token}]
        assert release_calls == [record["job_id"]]
    finally:
        daemon.close()


def test_late_finish_sqlite_inner_event_failure_returns_stored_result_without_replay(tmp_path, monkeypatch):
    token = uuid4().hex
    daemon, worker, request = _late_result_fixture(tmp_path, token)
    release_calls = []
    daemon._release_terminal_resource_reservations = lambda job_id: (
        release_calls.append(job_id) or {"state": "COMPLETE"}
    )
    log_calls = []
    try:
        first = daemon.dispatch(request)
        record = _record_from_result(first)
        _install_late_result_insert_failure(daemon)

        def fail_log_write():
            log_calls.append(True)
            raise OSError("injected control error log write failure")

        monkeypatch.setattr(daemon, "_log_exception", fail_log_write)
        late = daemon._finish(
            record,
            {"success": False, "error": {"code": "LATE_CANDIDATE"}},
            "FAILED",
        )

        assert late["success"] is True
        assert late["data"] == first["data"]
        warning = late["execution"]["late_result_warning"]
        assert warning["code"] == "LATE_RESULT_FINISH_STORAGE_FAILED"
        assert warning["failed_steps"] == ["operation_store_finish", "control_errors_log_write"]
        assert warning["diagnostic_durable"] is False
        assert warning["durability_scope"] == "response_only"
        assert warning["authoritative_status"] == "SUCCEEDED"
        assert warning["store_error_type"] == "IntegrityError"
        assert warning["log_error_type"] == "OSError"
        assert warning["log_attempt"] == "FAILED"
        assert log_calls == [True]

        operation = daemon.store.get_operation(record["operation_id"])
        job = daemon.store.job(record["job_id"])
        assert operation["status"] == "SUCCEEDED"
        assert operation["result"] == first
        assert job["operation_id"] == record["operation_id"]
        assert job["status"] == "SUCCEEDED"
        assert job["result"] == first
        assert "LateResultRecorded" not in [event["event"] for event in daemon.store.events(record["job_id"])]
        assert worker.calls == [{"x": token}]
        assert release_calls == [record["job_id"]]

        duplicate = daemon.dispatch(request)
        assert duplicate == first
        assert worker.calls == [{"x": token}]
        assert release_calls == [record["job_id"]]
    finally:
        daemon.close()


def test_late_finish_sqlite_inner_event_failure_without_result_never_returns_candidate(tmp_path):
    token = uuid4().hex
    daemon, worker, request = _late_result_fixture(tmp_path, token)
    release_calls = []
    daemon._release_terminal_resource_reservations = lambda job_id: (
        release_calls.append(job_id) or {"state": "COMPLETE"}
    )
    try:
        first = daemon.dispatch(request)
        record = _record_from_result(first)
        daemon.store.db.execute(
            "UPDATE operations SET result=NULL WHERE operation_id=?",
            (record["operation_id"],),
        )
        daemon.store.db.commit()
        _install_late_result_insert_failure(daemon)

        returned = daemon._finish(
            record,
            {"success": True, "data": {"candidate_only": "must not escape"}},
            "SUCCEEDED",
        )

        assert returned["success"] is False
        assert returned["error"]["code"] == "AUTHORITATIVE_RESULT_UNAVAILABLE"
        assert returned["error"]["safe_retry"] is False
        assert returned["data"] == {
            "status": "SUCCEEDED",
            "authoritative_result_available": False,
        }
        assert returned["execution"]["operation_id"] == record["operation_id"]
        warning = returned["execution"]["late_result_warning"]
        assert warning["diagnostic_durable"] is False
        assert warning["durability_scope"] == "response_only"
        assert daemon.store.get_operation(record["operation_id"])["result"] is None
        assert daemon.store.job(record["job_id"])["result"] is None
        assert worker.calls == [{"x": token}]
        assert release_calls == [record["job_id"]]
    finally:
        daemon.close()


def test_late_finish_sqlite_inner_event_failure_with_untrusted_readback_stays_unknown(tmp_path, monkeypatch):
    for case in ("unknown", "nonterminal", "identity_mismatch", "read_failure"):
        token = f"{case}-{uuid4().hex}"
        case_dir = tmp_path / case
        case_dir.mkdir()
        daemon, worker, request = _late_result_fixture(case_dir, token)
        release_calls = []
        daemon._release_terminal_resource_reservations = lambda job_id, calls=release_calls: (
            calls.append(job_id) or {"state": "COMPLETE"}
        )
        try:
            first = daemon.dispatch(request)
            record = _record_from_result(first)
            _install_late_result_insert_failure(daemon)
            original_finish = daemon.store.finish

            def fail_then_change_readback(operation_id, *, status, result, _lifecycle_update=None):
                try:
                    return original_finish(
                        operation_id, status=status, result=result, _lifecycle_update=_lifecycle_update)
                except Exception:
                    if case == "unknown":
                        daemon.store.db.execute(
                            "UPDATE operations SET status='UNKNOWN' WHERE operation_id=?", (operation_id,))
                        daemon.store.db.execute(
                            "UPDATE jobs SET status='UNKNOWN' WHERE operation_id=?", (operation_id,))
                        daemon.store.db.commit()
                    elif case == "nonterminal":
                        daemon.store.db.execute(
                            "UPDATE operations SET status='RUNNING' WHERE operation_id=?", (operation_id,))
                        daemon.store.db.execute(
                            "UPDATE jobs SET status='RUNNING' WHERE operation_id=?", (operation_id,))
                        daemon.store.db.commit()
                    elif case == "identity_mismatch":
                        daemon.store.db.execute(
                            "UPDATE jobs SET operation_id='wrong-operation-id' WHERE operation_id=?", (operation_id,))
                        daemon.store.db.commit()
                    raise

            monkeypatch.setattr(daemon.store, "finish", fail_then_change_readback)
            if case == "read_failure":
                def fail_readback(_operation_id):
                    raise OSError("injected authoritative readback failure")

                monkeypatch.setattr(daemon.store, "get_operation", fail_readback)

            returned = daemon._finish(
                record,
                {"success": True, "data": {"candidate_only": case}},
                "SUCCEEDED",
            )

            assert returned["success"] is False, (case, returned)
            assert returned["error"]["code"] == "EXECUTION_STATE_UNKNOWN", (case, returned)
            assert returned["error"]["safe_retry"] is False, (case, returned)
            assert returned["data"]["status"] == "UNKNOWN", (case, returned)
            assert returned["execution"]["operation_id"] == record["operation_id"]
            warning = returned["execution"]["late_result_warning"]
            assert warning["diagnostic_durable"] is False
            assert warning["durability_scope"] == "response_only"
            assert warning["authoritative_status"] == "UNKNOWN"
            assert worker.calls == [{"x": token}], case
            assert release_calls == [record["job_id"]], case
        finally:
            daemon.close()


def test_native_lifecycle_late_finish_event_fault_keeps_existing_exception_scope(tmp_path):
    store = OperationStore(tmp_path / "native-lifecycle.sqlite")
    lifecycle = SessionLifecycleStore(store)
    try:
        lifecycle_record = new_lifecycle_record(
            project_id="t35-project",
            session_id="t35-session",
        )
        lifecycle.save(lifecycle_record)
        record, reused = store.begin(
            request_id="t35-native-request",
            idempotency_key="t35-native-key",
            request_hash="t35-native-hash",
            operation="session.stop",
        )
        assert reused is False
        authoritative = {
            "success": True,
            "data": {"status": "SUCCEEDED"},
            "execution": {key: record[key] for key in (
                "request_id", "operation_id", "request_hash", "idempotency_key", "job_id",
            )},
        }
        accepted, status = store.finish(
            record["operation_id"], status="SUCCEEDED", result=authoritative)
        assert (accepted, status) == (True, "SUCCEEDED")
        store.db.execute(
            "CREATE TRIGGER t35_fail_native_late_result_insert "
            "BEFORE INSERT ON job_events WHEN NEW.event='LateResultRecorded' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic native T35 late-result insert failure'); END"
        )
        store.db.commit()

        receiver = SimpleNamespace(store=store, session_lifecycle=lifecycle)
        try:
            ControlDaemon._finish(
                receiver,
                record,
                {"success": False, "error": {"code": "LATE_NATIVE_CANDIDATE"}},
                "FAILED",
                _native_lifecycle_transition=(lifecycle_record, lifecycle_record["revision"],),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("native lifecycle storage exception was unexpectedly recovered")

        assert store.db.in_transaction is False
        assert store.get_operation(record["operation_id"])["status"] == "SUCCEEDED"
        assert store.get_operation(record["operation_id"])["result"] == authoritative
        assert store.job(record["job_id"])["status"] == "SUCCEEDED"
    finally:
        store.close()


def test_late_finish_without_authoritative_result_does_not_return_candidate(tmp_path):
    token = uuid4().hex
    daemon, _worker, request = _late_result_fixture(tmp_path, token)
    try:
        first = daemon.dispatch(request)
        record = _record_from_result(first)
        daemon.store.db.execute(
            "UPDATE operations SET result=NULL WHERE operation_id=?",
            (record["operation_id"],),
        )
        assert daemon.store.job(record["job_id"])["result"] is None

        returned = daemon._finish(
            record,
            {"success": True, "data": {"candidate_only": "must not escape"}},
            "SUCCEEDED",
        )

        assert returned["success"] is False
        assert returned["error"]["code"] == "AUTHORITATIVE_RESULT_UNAVAILABLE"
        assert returned["data"] == {
            "status": "SUCCEEDED",
            "authoritative_result_available": False,
        }
        assert returned["execution"]["job_id"] == record["job_id"]
        assert daemon.store.job(record["job_id"])["status"] == "SUCCEEDED"
        assert "candidate_only" not in returned["data"]
    finally:
        daemon.close()


def test_file_backed_dispatch_is_single_submission_across_duplicate_and_restart():
    home = _home()
    token = uuid4().hex
    fake_worker = _FakeWorker()
    registry = {"set_parameters": fake_worker}
    daemons = []
    try:
        service = _service(home, token)
        request = _request(service, token)
        daemon = ControlDaemon(home, service=service, registry=registry)
        daemons.append(daemon)
        _install_fake_worker(daemon, fake_worker)
        submits = _spy_submit(daemon.session_scheduler)

        first = daemon.dispatch(request)
        duplicate = daemon.dispatch(request)

        assert first["success"] is True
        assert duplicate == first
        assert len(submits) == 1
        assert fake_worker.calls == [{"x": token}]
        job_id = first["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "SUCCEEDED"

        daemon.close()
        restarted_service = _service(home, token)
        assert _request(restarted_service, token) == request
        restarted = ControlDaemon(home, service=restarted_service, registry=registry)
        daemons.append(restarted)
        _install_fake_worker(restarted, fake_worker)
        restart_submits = _spy_submit(restarted.session_scheduler)

        replay = restarted.dispatch(request)

        assert replay == first
        assert restart_submits == []
        assert fake_worker.calls == [{"x": token}]
        assert restarted.store.job(job_id)["status"] == "SUCCEEDED"
    finally:
        for daemon in reversed(daemons):
            daemon.close()
        shutil.rmtree(home, ignore_errors=True)


def test_known_scheduler_identity_conflict_is_persisted_and_not_requeued():
    home = _home()
    token = uuid4().hex
    fake_worker = _FakeWorker()
    registry = {"set_parameters": fake_worker}
    daemons = []
    try:
        service = _service(home, token)
        request = _request(service, token)
        daemon = ControlDaemon(home, service=service, registry=registry)
        daemons.append(daemon)
        _install_fake_worker(daemon, fake_worker)
        rejected_submits = []

        def reject_before_enqueue(*_args, **_kwargs):
            rejected_submits.append(True)
            raise SessionIdentityConflict("synthetic lane identity conflict")

        daemon.session_scheduler.submit = reject_before_enqueue
        first = daemon.dispatch(request)

        row = daemon.store.db.execute(
            "SELECT j.job_id FROM operations o JOIN jobs j USING(operation_id) WHERE o.idempotency_key=?",
            (request["execution"]["idempotency_key"],),
        ).fetchone()
        assert row is not None
        job_id = row["job_id"]
        durable_job = daemon.store.job(job_id)
        assert durable_job["status"] == "FAILED", durable_job
        assert first["success"] is False
        assert first["error"]["code"] == "SESSION_IDENTITY_CONFLICT"
        assert first["error"]["safe_retry"] is False
        assert first["data"]["engine_dispatched"] is False
        assert len(rejected_submits) == 1
        assert first["execution"]["job_id"] == job_id

        duplicate = daemon.dispatch(request)
        assert duplicate == first
        assert len(rejected_submits) == 1
        assert fake_worker.calls == []

        daemon.close()
        restarted_service = _service(home, token)
        restarted = ControlDaemon(home, service=restarted_service, registry=registry)
        daemons.append(restarted)
        _install_fake_worker(restarted, fake_worker)
        restart_submits = _spy_submit(restarted.session_scheduler)

        replay = restarted.dispatch(request)

        assert replay == first
        assert restart_submits == []
        assert fake_worker.calls == []
        assert restarted.store.job(job_id)["status"] == "FAILED"
    finally:
        for daemon in reversed(daemons):
            daemon.close()
        shutil.rmtree(home, ignore_errors=True)
