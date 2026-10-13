from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp._control_daemon import ControlDaemon, WorkerRetirementRefused
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from tools import run_native_w24_cure_science as runner


PROJECT_ID = "project-transition-test"
WORKER_IDENTITY = {"pid": 210, "birth": "Sun Sep 27 07:00:00 2026",
                   "command": "java -cp task-worker PersistentComsolWorker"}
SERVER_IDENTITY = {"pid": 200, "birth": "Sun Sep 27 06:59:00 2026",
                   "command": "mphserver -port 12345"}


class _Process:
    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None
        self.terminate_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = 0

    def wait(self, timeout=None):
        assert self.returncode is not None
        return self.returncode


class _Store:
    def __init__(self, status: str = "SUCCEEDED", *, observed: bool = True,
                 execution_unknown: bool = False):
        self.jobs = [{"job_id": "job-1", "status": status, "project_id": PROJECT_ID}]
        rows = [{"event": "worker_request", "metadata": {
            "phase": "submitted", "request_id": "worker-request-1", "kind": "call"}}]
        if observed:
            reply = {"status": "SUCCEEDED"}
            if execution_unknown:
                reply["failure"] = {"execution_state_unknown": True}
            rows.append({"event": "worker_request", "metadata": {
                "phase": "observed", "request_id": "worker-request-1",
                "kind": "call", "reply": reply}})
        self.job_events = {"job-1": rows}

    def list_jobs(self, *, offset: int, limit: int, project_id: str):
        rows = [row for row in self.jobs if row.get("project_id") == project_id]
        return rows[offset:offset + limit]

    def events(self, job_id: str, *, offset: int, limit: int):
        rows = self.job_events[job_id]
        return rows[offset:offset + limit]


class _ProjectAuthority:
    def __init__(self, workspace: Path, project_id: str = PROJECT_ID):
        self.record = {"project_id": project_id, "workspace": str(workspace)}

    def get_project(self, project_id: str):
        if project_id != self.record["project_id"]:
            raise KeyError(project_id)
        return dict(self.record)


class _Queue:
    def __init__(self, depth: int = 0):
        self._work_queue = SimpleNamespace(qsize=lambda: depth)
        self._shutdown = False


def _quiescence_readback(worker, *, worker_status="QUIESCENT", project_id=PROJECT_ID,
                         session_id="session-1", worker_identity=None):
    state = worker_status == "QUIESCENT"
    identity = worker_identity or {
        "worker_instance_id": "worker-instance-1", "connection_epoch": 1,
        "server_instance_id": "server-epoch-1",
    }
    return {"success": True, "data": {
        "schema_version": 1, "status": worker_status, "worker_status": worker_status,
        "scope": "exact-daemon-worker+all-projects+durable-direct-rpc",
        "safe_to_retire_worker": state,
        "binding": {"project_id": project_id, "session_id": session_id,
                    "worker_object_identity": f"python-object:{id(worker)}", **identity},
        "checks": {"exact_worker_binding": True,
                   "all_projects_in_daemon_ledger_scanned": True,
                   "snapshot_stable": True,
                   "all_accepted_jobs_terminal": state,
                   "no_durable_unknown_or_unclassified_jobs": state,
                   "all_direct_rpc_requests_observed_once_and_terminal": state},
    }}


def _write_journal(path: Path, *, include_finish: bool = True,
                   terminal: bool = True, project_id: str = PROJECT_ID):
    rows = [{"event": "direct_rpc_started", "call_id": "rpc-1",
             "operation": "operation_call", "project_id": project_id,
             "worker_session_index": 1}]
    if include_finish:
        rows.append({"event": "direct_rpc_finished", "call_id": "rpc-1",
                     "operation": "operation_call", "project_id": project_id,
                     "worker_session_index": 1, "rpc_returned": True,
                     "terminal": terminal})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path, monkeypatch, *, job_status="SUCCEEDED", observed=True,
             execution_unknown=False, queue_depth=0, journal_complete=True,
             journal_terminal=True, project_id=PROJECT_ID,
             expected_identity=WORKER_IDENTITY):
    workspace = tmp_path / "registered-project"
    workspace.mkdir()
    worker = SimpleNamespace(_process=_Process(WORKER_IDENTITY["pid"]))
    store = _Store(job_status, observed=observed, execution_unknown=execution_unknown)
    backend_identity = {"worker_instance_id": "worker-instance-1", "connection_epoch": 1,
                        "server_instance_id": "server-epoch-1"}
    daemon = SimpleNamespace(
        project_authority=_ProjectAuthority(workspace, project_id),
        store=store, running={}, queue=_Queue(queue_depth),
        closed=SimpleNamespace(is_set=lambda: False), worker=worker,
        backend=SimpleNamespace(worker=worker, worker_identity=backend_identity))
    server = SimpleNamespace(
        worker=worker, proc=_Process(SERVER_IDENTITY["pid"]),
        process_identity=SERVER_IDENTITY)

    def snapshot(pid: int):
        return {**(WORKER_IDENTITY if pid == WORKER_IDENTITY["pid"] else SERVER_IDENTITY)}

    monkeypatch.setattr("comsol_mcp._g2_isolation._process_snapshot", snapshot)
    journal = tmp_path / "direct_rpc_journal.jsonl"
    _write_journal(journal, include_finish=journal_complete,
                   terminal=journal_terminal)
    result = runner._worker_transition_reconciliation(
        daemon=daemon, project_id=PROJECT_ID,
        immutable_project_id=PROJECT_ID, project_workspace=workspace,
        server=server, expected_worker_object=worker,
        expected_worker_identity=expected_identity,
        direct_rpc_journal=journal, worker_session_index=1,
        worker_session_id="session-1",
        worker_quiescence=_quiescence_readback(
            worker, worker_status="BUSY" if queue_depth else "QUIESCENT",
            project_id=project_id, worker_identity=backend_identity))
    return result, daemon, server, workspace, journal


def test_worker_transition_requires_complete_terminal_evidence(tmp_path, monkeypatch):
    result, *_ = _fixture(tmp_path, monkeypatch)
    assert result["status"] == "SAFE_TO_CLOSE_FIRST_WORKER"
    assert all(result["checks"].values())
    assert result["direct_rpc_journal"]["started_count"] == 1
    assert result["worker_request_inventory"]["observed_request_count"] == 1


@pytest.mark.parametrize(
    "overrides,failed_check",
    [
        ({"journal_complete": False}, "complete_direct_rpc_journal_terminal"),
        ({"journal_terminal": False}, "complete_direct_rpc_journal_terminal"),
        ({"job_status": "UNKNOWN"}, "all_paged_project_jobs_terminal"),
        ({"job_status": "RUNNING"}, "all_paged_project_jobs_terminal"),
        ({"observed": False}, "all_worker_requests_observed_terminal"),
        ({"execution_unknown": True}, "no_execution_unknown_worker_effect"),
        ({"queue_depth": 1}, "daemon_idle"),
        ({"expected_identity": {**WORKER_IDENTITY, "birth": "different"}},
         "exact_current_worker_identity"),
    ],
)
def test_worker_transition_fails_closed_on_nonterminal_or_identity_gap(
        tmp_path, monkeypatch, overrides, failed_check):
    result, *_ = _fixture(tmp_path, monkeypatch, **overrides)
    assert result["safe_to_close_first_worker"] is False
    assert result["checks"][failed_check] is False


def test_worker_transition_rejects_project_identity_change(tmp_path, monkeypatch):
    workspace = tmp_path / "registered-project"
    workspace.mkdir()
    worker = SimpleNamespace(_process=_Process(WORKER_IDENTITY["pid"]))
    daemon = SimpleNamespace(
        project_authority=_ProjectAuthority(workspace, "foreign-project"),
        store=_Store(), running={}, queue=_Queue(),
        closed=SimpleNamespace(is_set=lambda: False), worker=worker,
        backend=SimpleNamespace(worker=worker, worker_identity={
            "worker_instance_id": "worker-instance-1", "connection_epoch": 1,
            "server_instance_id": "server-epoch-1"}))
    server = SimpleNamespace(worker=worker, proc=_Process(SERVER_IDENTITY["pid"]),
                             process_identity=SERVER_IDENTITY)
    monkeypatch.setattr(
        "comsol_mcp._g2_isolation._process_snapshot",
        lambda pid: WORKER_IDENTITY if pid == WORKER_IDENTITY["pid"] else SERVER_IDENTITY)
    journal = tmp_path / "direct_rpc_journal.jsonl"
    _write_journal(journal)
    result = runner._worker_transition_reconciliation(
        daemon=daemon, project_id=PROJECT_ID,
        immutable_project_id="different-project", project_workspace=workspace,
        server=server, expected_worker_object=worker,
        expected_worker_identity=WORKER_IDENTITY,
        direct_rpc_journal=journal, worker_session_index=1,
        worker_session_id="session-1",
        worker_quiescence=_quiescence_readback(worker))
    assert result["safe_to_close_first_worker"] is False
    assert result["checks"]["immutable_project_identity_and_workspace"] is False


def test_reopen_refuses_to_close_or_term_worker_when_journal_is_incomplete(tmp_path, monkeypatch):
    workspace = tmp_path / "registered-project"
    workspace.mkdir()
    saved = workspace / "staged-baseline.mph"
    saved.write_bytes(b"saved native file")
    ledger = workspace / "study-runs.jsonl"
    ledger.write_text("placeholder", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    worker_process = _Process(WORKER_IDENTITY["pid"])
    worker = SimpleNamespace(_process=worker_process)
    daemon = SimpleNamespace(
        project_authority=_ProjectAuthority(workspace), store=_Store(), running={},
        queue=_Queue(), closed=SimpleNamespace(is_set=lambda: False),
        worker=worker, backend=SimpleNamespace(
            worker=worker,
            worker_identity={"worker_instance_id": "worker-instance-1",
                             "server_instance_id": "server-epoch-1", "connection_epoch": 7}),
        close_calls=0)
    daemon.close = lambda: setattr(daemon, "close_calls", daemon.close_calls + 1)
    lease = SimpleNamespace(readback=_quiescence_readback(
        worker, session_id="session-1", worker_identity=daemon.backend.worker_identity),
        mark_cleanup_started=lambda: None, confirm_worker_stopped=lambda: None)

    @contextmanager
    def retirement_guard(_project_id, _session_id):
        yield lease

    daemon.worker_retirement_guard = retirement_guard
    server_process = _Process(SERVER_IDENTITY["pid"])
    server = SimpleNamespace(worker=worker, proc=server_process,
                             process_identity=SERVER_IDENTITY, evidence=evidence,
                             close_worker_calls=0)
    server.close_worker = lambda: setattr(server, "close_worker_calls", server.close_worker_calls + 1)
    (evidence / "worker_connection.json").write_text(json.dumps({
        "status": "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED",
        "worker_runtime": {"pid": WORKER_IDENTITY["pid"], "port": 61234},
    }), encoding="utf-8")
    monkeypatch.setattr(
        "comsol_mcp._g2_isolation._process_snapshot",
        lambda pid: WORKER_IDENTITY if pid == WORKER_IDENTITY["pid"] else SERVER_IDENTITY)
    journal = evidence / "direct_rpc_journal.jsonl"
    _write_journal(journal, include_finish=False)
    binding = runner.ManagedModelBinding(
        PROJECT_ID, "session-1",
        {"project_id": PROJECT_ID, "session_id": "session-1", "server_instance_id": "server-epoch-1",
         "generation": 1, "model_tag": "m1"}, 5)
    adapter = runner.NativeScienceCampaignAdapter.__new__(runner.NativeScienceCampaignAdapter)
    adapter.worker_sessions = 1
    adapter.worker_start_attempt_count = 1
    adapter.worker2_start_intent_written = False
    adapter.workspace = workspace
    adapter.project_ledger = ledger
    adapter.captures = {f"staged_baseline:{tag}": {} for tag in ("stdUV", "stdBake", "stdCool")}
    adapter.project_id = adapter._immutable_project_id = PROJECT_ID
    adapter.daemon = daemon
    adapter.server = server
    adapter.evidence = evidence
    adapter.direct_rpc_journal = journal
    adapter._worker_object = worker
    adapter._worker_process_identity = WORKER_IDENTITY
    adapter.models = {"staged_baseline": binding}
    adapter.mechanics_models = {}
    adapter._retired_model_ref_epochs = set()
    adapter._retired_worker_server_instance_ids = set()
    monkeypatch.setattr(runner, "read_solve_ledger", lambda _: [{"slot": i} for i in range(5)])

    with pytest.raises(runner.CampaignError, match="transition is blocked"):
        adapter.reopen_staged_baseline(saved, timeout_s=60)
    assert daemon.close_calls == 0
    assert server.close_worker_calls == 0
    assert worker_process.terminate_calls == 0
    assert server_process.terminate_calls == 0


def test_real_control_daemon_fences_w24_worker_transition_across_projects(tmp_path, monkeypatch):
    class _Snapshot:
        def model_snapshot(self, tag):
            return {"model_tag": tag, "server_instance_id": "server-epoch-live",
                    "fingerprint": "test", "external_event_counter": 0}

    class _Client:
        def disconnect(self):
            return None

    class _Worker:
        def __init__(self, process):
            self._process = process
            self.closed = False

        def client(self):
            return _Client()

        def close(self):
            assert self._process.poll() is not None
            self.closed = True

    def create_project(daemon, root, label):
        response = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": label, "workspace": label,
                          "policy": {"permissions": ["inspect", "project_write", "compute"]}},
            "execution": {"request_id": f"create-{label}", "idempotency_key": f"create-{label}"},
        })
        assert response["success"] is True
        return response["data"]["project"]

    project_root = tmp_path / "projects"
    project_root.mkdir()
    session_id = "session-stable-endpoint"
    service = ExecutionService(SessionLedger(session_id, "server-epoch-live"),
                               _Snapshot(), project_root=project_root)
    worker_process = _Process(WORKER_IDENTITY["pid"])
    worker = _Worker(worker_process)
    daemon = ControlDaemon(
        tmp_path / "control", project_root=project_root, service=service,
        registry={"legacy_test": lambda _args: {"success": True, "data": {"reached": True}}},
        worker=worker)
    backend_identity = {"runtime_id": "test-runtime", "worker_instance_id": "worker-live",
                        "connection_epoch": 3, "server_instance_id": "server-epoch-live"}
    daemon.backend.worker_identity = backend_identity
    science_project = create_project(daemon, project_root, "science")
    other_project = create_project(daemon, project_root, "other")
    server_process = _Process(SERVER_IDENTITY["pid"])
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    server = SimpleNamespace(worker=worker, proc=server_process,
                             process_identity=SERVER_IDENTITY, port=12345,
                             evidence=evidence)
    journal = evidence / "direct_rpc_journal.jsonl"
    _write_journal(journal, project_id=science_project["project_id"])
    monkeypatch.setattr(
        "comsol_mcp._g2_isolation._process_snapshot",
        lambda pid: WORKER_IDENTITY if pid == WORKER_IDENTITY["pid"] else SERVER_IDENTITY)

    adapter = runner.NativeScienceCampaignAdapter.__new__(runner.NativeScienceCampaignAdapter)
    adapter.project_id = adapter._immutable_project_id = science_project["project_id"]
    adapter.workspace = Path(science_project["workspace"])
    adapter.daemon = daemon
    adapter.server = server
    adapter.evidence = evidence
    adapter.direct_rpc_journal = journal
    adapter.worker_sessions = 1
    adapter._worker_object = worker
    adapter._worker_process_identity = WORKER_IDENTITY
    adapter.models = {"staged_baseline": runner.ManagedModelBinding(
        science_project["project_id"], session_id,
        {"session_id": session_id, "server_instance_id": "server-epoch-live",
         "generation": 1, "model_tag": "staged"}, 0)}
    adapter.mechanics_models = {}
    adapter._retired_model_ref_epochs = set()
    adapter._retired_worker_server_instance_ids = set()

    try:
        # A live job in a different registered project blocks this Worker's
        # retirement even though the W24 project's own ledger is idle.
        foreign_job, reused = daemon.store.begin(
            request_id="other-project-active", idempotency_key="other-project-active",
            request_hash="other-project-active-hash", operation="model.inspect",
            metadata={"operation": "model.inspect", "arguments": {}, "execution": {
                "project_id": other_project["project_id"], "session_id": session_id}},
            timeouts={"rpc_timeout_s": 30})
        assert reused is False
        daemon.store.update_job(foreign_job["job_id"], "RUNNING")
        with pytest.raises(WorkerRetirementRefused):
            with daemon.worker_retirement_guard(science_project["project_id"], session_id):
                raise AssertionError("busy cross-project Worker must not enter retirement")
        assert worker_process.terminate_calls == 0
        daemon.store.update_job(foreign_job["job_id"], "SUCCEEDED")

        with daemon.worker_retirement_guard(science_project["project_id"], session_id) as lease:
            transition = adapter._require_worker_transition_safe(lease.readback)
            assert transition["safe_to_close_first_worker"] is True
            assert transition["checks"]["daemon_idle"] is True
            refused = daemon.dispatch({
                "operation": "legacy_test", "arguments": {},
                "execution": {"request_id": "during-retirement", "idempotency_key": "during-retirement"},
            })
            assert refused["success"] is False
            assert refused["error"]["code"] == "WORKER_RETIREMENT_IN_PROGRESS"
            close_receipt = adapter._close_first_worker_term_only(retirement_lease=lease)
            assert close_receipt["status"] == "EXACT_WORKER_CLOSED_TERM_ONLY"
            lease.confirm_worker_stopped()
        assert daemon._worker_retirement_state == "RETIRED"
        assert worker_process.terminate_calls == 1
        assert worker.closed is True
        assert server.worker is None
    finally:
        daemon.close()


def test_campaign_cleanup_uses_fenced_cross_project_readback(tmp_path, monkeypatch):
    class _Snapshot:
        def model_snapshot(self, tag):
            return {"model_tag": tag, "server_instance_id": "server-epoch-cleanup",
                    "fingerprint": "test", "external_event_counter": 0}

    class _Client:
        def disconnect(self):
            return None

    class _Worker:
        def __init__(self, process):
            self._process = process
            self.closed = False

        def client(self):
            return _Client()

        def close(self):
            assert self._process.poll() is not None
            self.closed = True

    project_root = tmp_path / "projects"
    project_root.mkdir()
    session_id = "session-cleanup"
    service = ExecutionService(SessionLedger(session_id, "server-epoch-cleanup"),
                               _Snapshot(), project_root=project_root)
    worker_process = _Process(WORKER_IDENTITY["pid"])
    worker = _Worker(worker_process)
    daemon = ControlDaemon(
        tmp_path / "control", project_root=project_root, service=service,
        registry={}, worker=worker)
    daemon.backend.worker_identity = {
        "runtime_id": "test-runtime", "worker_instance_id": "worker-cleanup",
        "connection_epoch": 4, "server_instance_id": "server-epoch-cleanup"}
    retirement_state_at_close = []
    original_daemon_close = daemon.close

    def capture_retired_close():
        retirement_state_at_close.append(daemon._worker_retirement_state)
        original_daemon_close()

    daemon.close = capture_retired_close

    def create_project(label):
        response = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": label, "workspace": label,
                          "policy": {"permissions": ["inspect", "project_write", "compute"]}},
            "execution": {"request_id": f"create-{label}", "idempotency_key": f"create-{label}"},
        })
        assert response["success"] is True
        return response["data"]["project"]

    try:
        science = create_project("science")
        other = create_project("other")
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        server_process = _Process(SERVER_IDENTITY["pid"])
        server = SimpleNamespace(worker=worker, proc=server_process,
                                 process_identity=SERVER_IDENTITY, port=12345,
                                 work=project_root)
        monkeypatch.setattr(
            "comsol_mcp._g2_isolation._process_snapshot",
            lambda pid: WORKER_IDENTITY if pid == WORKER_IDENTITY["pid"] else SERVER_IDENTITY)
        from tools import run_native_w24_cure_preflight as preflight
        monkeypatch.setattr(preflight, "_process_inventory", lambda: {
            "probes_ok": True, "quiescent": True,
            "comsol_engine_processes": [], "persistent_worker_processes": []})

        runtime = runner.NativeCampaignRuntime.__new__(runner.NativeCampaignRuntime)
        runtime.server = server
        runtime.daemon = daemon
        runtime.project_id = science["project_id"]
        runtime.project_workspace = Path(science["workspace"])
        runtime.evidence = evidence
        runtime.worker_identity = WORKER_IDENTITY
        runtime.worker_port = 61234
        runtime.adapter = None
        runtime.direct_native_call_guard = {"all_returned": True, "calls": []}
        runtime.solve_ledger_path = None

        initial = runtime.reconcile_for_cleanup()
        assert initial["safe_for_owned_cleanup"] is True
        assert initial["public_worker_quiescence"]["data"]["scope"] == (
            "exact-daemon-worker+all-projects+durable-direct-rpc")

        active, reused = daemon.store.begin(
            request_id="other-project-active-cleanup", idempotency_key="other-project-active-cleanup",
            request_hash="active-cleanup-hash", operation="model.inspect",
            metadata={"operation": "model.inspect", "arguments": {}, "execution": {
                "project_id": other["project_id"], "session_id": session_id}},
            timeouts={"rpc_timeout_s": 30})
        assert reused is False
        daemon.store.update_job(active["job_id"], "RUNNING")
        refused = runtime.cleanup_exact_owned(initial)
        assert refused["status"] == "CLEANUP_BLOCKED"
        assert worker_process.terminate_calls == 0
        assert server_process.terminate_calls == 0
        assert daemon._worker_retirement_state == "OPEN"

        daemon.store.update_job(active["job_id"], "SUCCEEDED")

        def exact_cleanup(server_arg, **kwargs):
            assert daemon._worker_retirement_state == "RETIRING"
            assert kwargs["server_identity"] == SERVER_IDENTITY
            assert kwargs["worker_identity"] == WORKER_IDENTITY
            assert kwargs["worker_port"] == 61234
            server_arg.worker._process.terminate()
            server_arg.worker.close()
            server_arg.proc.terminate()
            return {"status": "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT",
                    "durable_unknown_preserved": True}

        monkeypatch.setattr(
            "tools.run_native_w23_te_managed_preflight._exact_owned_cleanup", exact_cleanup)
        refreshed = runtime.reconcile_for_cleanup()
        assert refreshed["safe_for_owned_cleanup"] is True
        result = runtime.cleanup_exact_owned(refreshed)
        assert result["status"] == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT"
        assert result["worker_retirement_confirmation"] == (
            "EXACT_POPEN_EXIT_CONFIRMED_UNDER_ADMISSION_FENCE")
        assert retirement_state_at_close == ["RETIRED"]
        assert worker_process.terminate_calls == 1
        assert server_process.terminate_calls == 1
        assert runtime.daemon is None
    finally:
        if not daemon.closed.is_set():
            daemon.close()


def test_exact_worker_close_waits_for_disconnect_then_uses_term_only(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    events = evidence / "events.jsonl"
    class _Client:
        def disconnect(self):
            return None

    class _ClosableWorker:
        def __init__(self, process):
            self._process = process
            self.closed = False

        def client(self):
            return _Client()

        def close(self):
            assert self._process.poll() is not None
            self.closed = True

    class _RetirementLease:
        def __init__(self):
            self.marked = False

        def mark_cleanup_started(self):
            self.marked = True

    worker_process = _Process(WORKER_IDENTITY["pid"])
    worker = _ClosableWorker(worker_process)
    server = SimpleNamespace(worker=worker)
    monkeypatch.setattr(
        "comsol_mcp._g2_isolation._process_snapshot",
        lambda pid: WORKER_IDENTITY)
    adapter = runner.NativeScienceCampaignAdapter.__new__(runner.NativeScienceCampaignAdapter)
    adapter._worker_object = worker
    adapter._worker_process_identity = WORKER_IDENTITY
    adapter.server = server
    adapter.project_id = PROJECT_ID
    adapter.worker_sessions = 1
    adapter.direct_rpc_journal = evidence / "direct_rpc_journal.jsonl"
    adapter.evidence = evidence
    lease = _RetirementLease()
    receipt = adapter._close_first_worker_term_only(retirement_lease=lease)
    assert receipt["status"] == "EXACT_WORKER_CLOSED_TERM_ONLY"
    assert worker_process.terminate_calls == 1
    assert worker.closed is True
    assert lease.marked is True
    assert server.worker is None
    journal = [json.loads(line) for line in adapter.direct_rpc_journal.read_text().splitlines()]
    assert [row["event"] for row in journal] == ["direct_rpc_started", "direct_rpc_finished"]
    assert journal[-1]["terminal"] is True


def _binding(session_id: str, server_instance_id: str, generation: int = 1):
    return runner.ManagedModelBinding(
        PROJECT_ID, session_id,
        {"session_id": session_id, "server_instance_id": server_instance_id,
         "generation": generation, "model_tag": "m1"}, 0)


def test_worker_epoch_allows_stable_logical_session_on_new_worker():
    adapter = runner.NativeScienceCampaignAdapter.__new__(runner.NativeScienceCampaignAdapter)
    adapter.project_id = adapter._immutable_project_id = PROJECT_ID
    adapter.daemon = SimpleNamespace(backend=SimpleNamespace(worker_identity={
        "server_instance_id": "server-epoch-2", "connection_epoch": 8}))
    adapter._retired_model_ref_epochs = {("session-fixed-endpoint", "server-epoch-1", 1)}
    adapter._retired_worker_server_instance_ids = {"server-epoch-1"}
    binding = _binding("session-fixed-endpoint", "server-epoch-2")

    epoch = adapter._validate_current_worker_binding(binding, name="staged_baseline")

    assert epoch == ("session-fixed-endpoint", "server-epoch-2", 1)
    assert binding.session_id == "session-fixed-endpoint"


def test_worker_epoch_rejects_retired_server_instance_even_with_same_session():
    adapter = runner.NativeScienceCampaignAdapter.__new__(runner.NativeScienceCampaignAdapter)
    adapter.project_id = adapter._immutable_project_id = PROJECT_ID
    adapter.daemon = SimpleNamespace(backend=SimpleNamespace(worker_identity={
        "server_instance_id": "server-epoch-1", "connection_epoch": 7}))
    adapter._retired_model_ref_epochs = {("session-fixed-endpoint", "server-epoch-1", 1)}
    adapter._retired_worker_server_instance_ids = {"server-epoch-1"}

    with pytest.raises(runner.CampaignError, match="retired Worker epoch"):
        adapter._validate_current_worker_binding(
            _binding("session-fixed-endpoint", "server-epoch-1"), name="old-model")


def test_observed_worker_birth_is_counted_even_when_startup_failed(tmp_path, monkeypatch):
    process = _Process(515)
    process.returncode = 9
    worker = SimpleNamespace(_process=process)
    server = SimpleNamespace(worker=worker)
    monkeypatch.setattr("comsol_mcp._g2_isolation._process_snapshot", lambda pid: {
        "pid": pid, "birth": "Sun Sep 27 08:00:00 2026", "command": "java PersistentComsolWorker"})
    adapter = runner.NativeScienceCampaignAdapter.__new__(runner.NativeScienceCampaignAdapter)
    adapter.server = server
    adapter.worker_lifecycle_log = tmp_path / "worker_lifecycle.jsonl"
    adapter.worker_births = [{"attempt_index": 1, "birth_observed": True, "pid": 500}]
    adapter.worker_sessions = 1

    observed = adapter._record_worker_start_attempt(
        attempt_index=2, outcome="START_WORKER_RAISED_NO_RETRY", error="startup failed")

    assert observed["birth_observed"] is True
    assert observed["pid"] == 515
    assert len(adapter.worker_births) == 2
    assert adapter.worker_sessions == 1
    rows = [json.loads(line) for line in adapter.worker_lifecycle_log.read_text().splitlines()]
    assert rows[0]["event"] == "worker_birth_observed"
    assert rows[1]["event"] == "worker_start_attempt_terminal"
    assert rows[1]["worker_birth_count"] == 2
    assert rows[1]["successful_connected_worker_sessions"] == 1
