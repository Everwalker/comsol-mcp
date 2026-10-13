from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace
import uuid
from pathlib import Path

import pytest

from tools import run_native_w24_static_shape_sensitivity_lifecycle as lifecycle
from tools.run_native_w24_cure_science import ManagedModelBinding
from tools.w24_static_shape_sensitivity import build_sensitivity_campaign_plan, sensitivity_submission_slots


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(tmp_path: Path) -> dict:
    project = tmp_path / "registered-project"
    outputs = project / "outputs"
    outputs.mkdir(parents=True)
    mcp_home = tmp_path / "mcp-home"
    mcp_home.mkdir()
    store = mcp_home / "operations.sqlite3"
    store.write_bytes(b"test operation store identity\n")
    st = store.stat()
    approval_root = tmp_path / "private-approval"
    approval_root.mkdir(mode=0o700)
    approval_inbox = approval_root / "approval-inbox.json"
    old_server = {
        "pid": 9001, "birth": "start_epoch_ms:900000", "start_epoch_ms": 900000,
        "birth_epoch_s": 900.0, "port": 57001,
    }
    setup_hash = "1" * 64
    history = {
        key: {"receipt_path": str(tmp_path / f"old-{index}.json"),
              "receipt_sha256": f"{index + 1:064x}",
              "artifact_path": str(outputs / f"old-{index}.mph"),
              "artifact_sha256": f"{index + 101:064x}", "artifact_size_bytes": index + 1}
        for index, key in enumerate(lifecycle.CAPTURE_KEY_ORDER)
    }
    candidate = {
        "schema": lifecycle.CANDIDATE_SCHEMA,
        "status": "PREPARED_STATIC_INPUTS_NOT_APPROVED_NOT_RUN",
        "native_acceptance": "NOT_RUN",
        "campaign_id": "w24-lifecycle-test-campaign",
        "readmission_transition_id": "w24-lifecycle-transition",
        "setup_campaign": {
            "receipt_sha256": setup_hash, "project_id": "project-w24-test",
            "project_workspace": str(project), "project_root": str(project),
            "work_dir": str(tmp_path / "old-setup-work"), "mcp_home": str(mcp_home),
            "operation_store_path": str(store),
            "operation_store_identity": {"device": st.st_dev, "inode": st.st_ino},
            "historical_setup_slots": history,
            "historical_worker_epoch": ["old-session", 2, "old-server-epoch"],
            "historic_setup_server_identity": old_server,
        },
        "source_sha256": {name: f"{index + 1:064x}"
                          for index, name in enumerate(lifecycle.SOURCE_PATHS)},
        "lifecycle_dependency_sha256": {name: f"{index + 201:064x}"
                                        for index, name in enumerate(lifecycle.LIFECYCLE_DEPENDENCY_PATHS)},
        "orchestrator_sha256": "a" * 64,
        "sensitivity_plan": build_sensitivity_campaign_plan(),
        "ordered_slots": list(sensitivity_submission_slots()),
        "capture_resource_estimate": {"test_only": True},
        "new_science_server_budget": {
            "birth_source": "new science Server exact Popen start epoch, before the separate managed Worker birth",
            "wall_seconds_including_all_readmission_approval_wait_science_cleanup": 3600,
            "cleanup_reserve_seconds": 90,
            "setup_server_budget_is_historical_and_never_reused": True,
        },
        "approval_protocol": {
            "inbox_schema": lifecycle.APPROVAL_INBOX_SCHEMA,
            "approval_inbox_path": str(approval_inbox),
            "approval_root": str(approval_root),
            "approval_input_status": "PENDING_NOT_APPROVED",
            "expected_approval_sha256_must_come_from_external_inbox": True,
            "one_observation_only_no_replacement_hash_retry": True,
            "same_live_runtime_required_no_restart_or_handle_reconstruction": True,
        },
    }
    candidate["candidate_sha256"] = "b" * 64
    return candidate


class _Clock:
    def __init__(self, value: float):
        self.value = value

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class _Runtime:
    def __init__(self, *, server_epoch: float = 1000.0):
        self.server_epoch = server_epoch
        self.session = {
            "project_id": "project-w24-test", "session_id": "science-session",
            "worker_instance_id": "worker-instance-current", "worker_epoch": 3,
            "server_instance_id": "science-server-epoch",
        }
        self.worker_birth = {
            "pid": 9002, "birth": "start_epoch_ms:1001000", "start_epoch_ms": 1001000,
            "birth_epoch_s": 1001.0,
        }
        self.budget = None
        self.unknown = False
        self.cleanup_allow: list[bool] = []
        self.preserve_called = False
        self.server_starts = 0
        self.worker_replaced = False
        self.science_authorized = False
        self.managed_runner = _ManagedRunner()

    def prepare_project_and_server(self):
        return {"status": "PREBIRTH_GATES_PASSED", "scope": "injected-handles-only"}

    def start_server(self):
        self.server_starts += 1
        ms = int(self.server_epoch * 1000)
        return {"birth_observed": True, "pid": 9000, "birth": f"start_epoch_ms:{ms}",
                "start_epoch_ms": ms, "birth_epoch_s": ms / 1000.0,
                "port": 57002, "listener": {"port": 57002}}

    def bind_birth_budget(self, birth_budget, server_birth):
        assert server_birth["pid"] == 9000
        self.budget = birth_budget

    def connect_worker(self, *, birth_budget):
        assert birth_budget is self.budget
        return {"session": dict(self.session), "worker_birth": dict(self.worker_birth),
                "managed_runner": self.managed_runner}

    def current_worker_epoch(self):
        if self.worker_replaced:
            return ("replacement-session", 4, "replacement-server")
        return (self.session["session_id"], self.session["worker_epoch"],
                self.session["server_instance_id"])

    def current_worker_birth(self):
        if self.worker_replaced:
            return (9010, "replacement-birth", 1002000)
        return (self.worker_birth["pid"], self.worker_birth["birth"],
                self.worker_birth["start_epoch_ms"])

    def authorize_readmission_phase(self):
        self.readmission_authorized = True

    def authorize_science_phase(self):
        self.science_authorized = True

    def has_unknown_operations(self):
        return self.unknown

    def cleanup(self, *, session, birth_budget, allow_cleanup):
        self.cleanup_allow.append(allow_cleanup)
        if not allow_cleanup:
            return {"status": "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED", "signal_sent": False}
        if session is None and birth_budget is None:
            return {"status": "PREBIRTH_DAEMON_CLOSED_NO_SERVER", "injected_only": True}
        return {"status": "EXACT_WORKER_RETIRED_SERVER_TERM_REAPED", "injected_only": True}

    def preserve_handles(self):
        self.preserve_called = True


class _ManagedRunner:
    def __init__(self):
        self.project_id = "project-w24-test"
        self.workspace = Path("/")
        self.daemon = SimpleNamespace(store=SimpleNamespace(path="/private/tmp/nonexistent-store"))


class _Factory:
    def __init__(self, runtime: _Runtime):
        self.runtime = runtime
        self.preflight_count = 0

    def preflight(self, **_kwargs):
        self.preflight_count += 1

    def create(self, *, candidate, **_kwargs):
        self.runtime.managed_runner.daemon.store.path = candidate["setup_campaign"]["operation_store_path"]
        return self.runtime


def _readmission(tmp_path: Path, candidate: dict) -> dict:
    bindings = {}
    receipt_hashes = {}
    receipt_paths = {}
    old_hashes = {}
    for index, key in enumerate(lifecycle.CAPTURE_KEY_ORDER):
        binding = ManagedModelBinding(
            candidate["setup_campaign"]["project_id"], "science-session",
            {"model_tag": f"tag-{index}", "session_id": "science-session",
             "generation": 3, "server_instance_id": "science-server-epoch"}, index + 1)
        bindings[key] = binding
        receipt_hashes[key] = f"{index + 301:064x}"
        receipt_paths[key] = tmp_path / f"readmitted-{index}.json"
        old_hashes[key] = candidate["setup_campaign"]["historical_setup_slots"][key]["receipt_sha256"]
    manifest = tmp_path / "readmission-manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    return {
        "status": "SCIENCE_WORKER_READMISSION_COMPLETE",
        "transition_id": candidate["readmission_transition_id"],
        "bindings": bindings, "setup_receipt_sha256": receipt_hashes,
        "setup_receipt_paths": receipt_paths,
        "historical_setup_receipt_sha256": old_hashes,
        "manifest_path": str(manifest), "manifest_sha256": _sha(manifest),
    }


def _approval_writer(*, candidate, approval_input_path, approval_input_sha256,
                     birth_budget, clock, sleep, code="approved", post_write=None):
    del birth_budget, sleep
    root = Path(candidate["approval_protocol"]["approval_root"])
    approval = root / "approval.json"
    data = json.dumps({"schema": "W24_EXTERNAL_APPROVAL_TEST_V1", "decision": code},
                      sort_keys=True).encode()
    approval.write_bytes(data)
    inbox = {
        "schema": lifecycle.APPROVAL_INBOX_SCHEMA,
        "campaign_id": candidate["campaign_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "approval_input_path": str(approval_input_path),
        "approval_input_sha256": approval_input_sha256,
        "approval_path": str(approval),
        "expected_approval_sha256": hashlib.sha256(data).hexdigest(),
    }
    inbox_path = Path(candidate["approval_protocol"]["approval_inbox_path"])
    inbox_path.write_text(json.dumps(inbox, sort_keys=True) + "\n", encoding="utf-8")
    if post_write:
        post_write()
    return lifecycle.wait_for_external_approval(
        candidate=candidate, approval_input_path=approval_input_path,
        approval_input_sha256=approval_input_sha256, birth_budget=clock.budget,
        clock=clock.now, sleep=clock.sleep)


def _write_approval_from_waiter_kwargs(kwargs, clock):
    clock.budget = kwargs["birth_budget"]
    return _approval_writer(
        candidate=kwargs["candidate"], approval_input_path=kwargs["approval_input_path"],
        approval_input_sha256=kwargs["approval_input_sha256"],
        birth_budget=kwargs["birth_budget"], clock=clock, sleep=clock.sleep)


def _execute(tmp_path: Path, *, runtime: _Runtime, execute_callback=None,
             clock: _Clock | None = None, waiter=None, readmit_callback=None):
    candidate = _candidate(tmp_path)
    clock = clock or _Clock(1002.0)
    factory = _Factory(runtime)
    readmission = _readmission(tmp_path, candidate)
    calls = {"readmit": 0, "execute": 0, "approval": 0}

    def readmit_fn(*args, **kwargs):
        calls["readmit"] += 1
        assert kwargs["birth_budget"] is runtime.budget
        if readmit_callback:
            readmission_result = readmit_callback(runtime, readmission)
            if readmission_result is not None:
                return readmission_result
        return {**readmission, "bindings": readmission["bindings"]}

    def execute_fn(*args, **kwargs):
        calls["execute"] += 1
        assert kwargs["birth_budget"] is runtime.budget
        if execute_callback:
            return execute_callback(*args, **kwargs)
        return {"status": "ALL_14_CAPTURED_SENSITIVITY_GATES_PASS_REVIEW_REQUIRED",
                "actual_study_run_submissions": 14}

    def approval_fn(**kwargs):
        calls["approval"] += 1
        clock.budget = kwargs["birth_budget"]
        if waiter:
            return waiter(clock, runtime, kwargs)
        return _write_approval_from_waiter_kwargs(kwargs, clock)

    result = lifecycle.run_reviewed_lifecycle(
        candidate=candidate, candidate_sha256=candidate["candidate_sha256"],
        evidence_dir=tmp_path / "lifecycle-evidence",
        server_work=Path("/private/tmp") / f"w24-lifecycle-test-{uuid.uuid4().hex}",
        runtime_factory=factory, approval_waiter=approval_fn,
        readmit=readmit_fn, execute=execute_fn,
        clock=clock.now, sleep=clock.sleep,
    )
    return result, calls, factory, candidate


def test_two_phase_lifecycle_uses_one_server_budget_external_approval_and_live_handles(tmp_path):
    runtime = _Runtime()
    result, calls, factory, candidate = _execute(tmp_path, runtime=runtime)

    assert factory.preflight_count == 1
    assert runtime.server_starts == 1
    assert runtime.budget.birth_epoch_s == runtime.server_epoch
    assert runtime.budget.budget_s == 3600.0
    assert runtime.budget.cleanup_reserve_s == 90.0
    assert runtime.readmission_authorized is True
    assert runtime.science_authorized is True
    assert calls == {"readmit": 1, "execute": 1, "approval": 1}
    assert runtime.cleanup_allow == [True]
    assert result["status"] == "SCIENCE_TERMINAL_CLEANUP_VERIFIED_REQUIRES_INDEPENDENT_REVIEW"
    assert result["native_acceptance"] == "NOT_ACCEPTED"
    assert result["approval_input"]["status"] == "PENDING_NOT_APPROVED"
    claim = Path(result["science_server_birth_claim"]["path"])
    assert claim.is_file() and candidate["campaign_id"] in claim.read_text()


def test_missing_approval_stops_at_cleanup_reserve_with_zero_solve_dispatch(tmp_path):
    runtime = _Runtime()
    clock = _Clock(1000.0 + 3600.0 - 90.0)

    def missing(_clock, _runtime, kwargs):
        _clock.budget = kwargs["birth_budget"]
        return lifecycle.wait_for_external_approval(
            candidate=kwargs["candidate"], approval_input_path=kwargs["approval_input_path"],
            approval_input_sha256=kwargs["approval_input_sha256"],
            birth_budget=kwargs["birth_budget"], clock=_clock.now, sleep=_clock.sleep)

    result, calls, _factory, _candidate = _execute(tmp_path, runtime=runtime,
                                                   clock=clock, waiter=missing)
    assert calls["execute"] == 0
    assert calls["approval"] == 1
    assert runtime.cleanup_allow == [True]
    assert result["cleanup"]["status"] == "EXACT_WORKER_RETIRED_SERVER_TERM_REAPED"
    assert "approval did not arrive before cleanup reserve" in result["error"]["message"]


def test_first_mismatched_approval_inbox_is_final_and_never_retried(tmp_path):
    runtime = _Runtime()
    clock = _Clock(1002.0)

    def wrong_campaign(_clock, _runtime, kwargs):
        _clock.budget = kwargs["birth_budget"]
        _write_approval_from_waiter_kwargs(kwargs, _clock)
        path = Path(kwargs["candidate"]["approval_protocol"]["approval_inbox_path"])
        inbox = json.loads(path.read_text())
        inbox["campaign_id"] = "foreign-campaign"
        path.write_text(json.dumps(inbox) + "\n")
        return lifecycle.wait_for_external_approval(
            candidate=kwargs["candidate"], approval_input_path=kwargs["approval_input_path"],
            approval_input_sha256=kwargs["approval_input_sha256"],
            birth_budget=kwargs["birth_budget"], clock=_clock.now, sleep=_clock.sleep)

    result, calls, _factory, _candidate = _execute(tmp_path, runtime=runtime,
                                                   clock=clock, waiter=wrong_campaign)
    assert calls["approval"] == 1
    assert calls["execute"] == 0
    assert runtime.server_starts == 1
    assert runtime.cleanup_allow == [True]
    assert "another campaign" in result["error"]["message"]


def test_approval_arriving_inside_cleanup_reserve_cannot_start_solve(tmp_path):
    runtime = _Runtime()
    clock = _Clock(1002.0)

    def late(_clock, _runtime, kwargs):
        _clock.budget = kwargs["birth_budget"]
        envelope = _write_approval_from_waiter_kwargs(kwargs, _clock)
        _clock.value = kwargs["birth_budget"].deadline_epoch_s - kwargs["birth_budget"].cleanup_reserve_s
        return envelope

    result, calls, _factory, _candidate = _execute(tmp_path, runtime=runtime,
                                                   clock=clock, waiter=late)
    assert calls["execute"] == 0
    assert runtime.cleanup_allow == [True]
    assert "reserved cleanup time" in result["error"]["message"]


def test_worker_replacement_during_external_approval_wait_fails_closed(tmp_path):
    runtime = _Runtime()
    clock = _Clock(1002.0)

    def replace(_clock, _runtime, kwargs):
        _clock.budget = kwargs["birth_budget"]
        envelope = _write_approval_from_waiter_kwargs(kwargs, _clock)
        _runtime.worker_replaced = True
        return envelope

    result, calls, _factory, _candidate = _execute(tmp_path, runtime=runtime,
                                                   clock=clock, waiter=replace)
    assert calls["execute"] == 0
    assert runtime.cleanup_allow == [True]
    assert "Worker was replaced" in result["error"]["message"]


def test_unknown_readmission_preserves_handles_and_never_waits_or_solves(tmp_path):
    runtime = _Runtime()
    clock = _Clock(1002.0)

    def unknown(_runtime, readmission):
        _runtime.unknown = True
        return readmission

    result, calls, _factory, _candidate = _execute(
        tmp_path, runtime=runtime, clock=clock, readmit_callback=unknown)
    assert calls["approval"] == 0
    assert calls["execute"] == 0
    assert runtime.cleanup_allow == [False]
    assert runtime.preserve_called is True
    assert result["status"] == "ACTIVE_OR_UNKNOWN_HANDLES_PRESERVED"
    assert result["native_acceptance"] == "NOT_ACCEPTED"


def test_one_birth_claim_blocks_new_invocation_after_process_handles_are_lost(tmp_path):
    runtime = _Runtime()
    result, calls, _factory, candidate = _execute(tmp_path, runtime=runtime)
    assert calls["execute"] == 1
    with pytest.raises(FileExistsError):
        lifecycle._claim_science_birth_once(candidate)
    alternate_invocation = {
        **candidate,
        "candidate_sha256": "c" * 64,
        "campaign_id": "w24-alternate-campaign",
        "readmission_transition_id": "w24-alternate-transition",
    }
    with pytest.raises(FileExistsError):
        lifecycle._claim_science_birth_once(alternate_invocation)
    assert runtime.server_starts == 1
    assert Path(result["science_server_birth_claim"]["path"]).is_file()


def test_incomplete_setup_candidate_is_rejected_before_runtime_factory_or_birth(tmp_path):
    candidate = _candidate(tmp_path)
    del candidate["setup_campaign"]["historical_setup_slots"][lifecycle.CAPTURE_KEY_ORDER[-1]]
    runtime = _Runtime()
    factory = _Factory(runtime)
    with pytest.raises(lifecycle.LifecycleError, match="fourteen-slot"):
        lifecycle.run_reviewed_lifecycle(
            candidate=candidate, candidate_sha256=candidate["candidate_sha256"],
            evidence_dir=tmp_path / "never-created", server_work=Path("/private/tmp") / "unused",
            runtime_factory=factory, approval_waiter=lambda **_kwargs: None)
    assert factory.preflight_count == 0
    assert runtime.server_starts == 0
    assert not (tmp_path / "never-created").exists()


class _Monitor:
    stop_reason = None
    last_sample = {"status": "SAMPLED_WITHIN_THRESHOLD"}

    @property
    def last_sample_monotonic(self):
        return time.monotonic()


class _DispatchOwner:
    def __init__(self, tmp_path: Path):
        self.setup = {"project_id": "project-guard"}
        self.candidate = {}
        self.birth_budget = lifecycle.BirthBudget(time.time())
        self.monitor = _Monitor()
        self.session = {"session_id": "session-guard"}
        self.events = tmp_path / "guard-events.jsonl"
        self.events.write_bytes(b"")
        self._phase = "readmission"
        self._unknown = False
        self._route_count = 0
        self._bootstrap_model_create_count = 0

    def _admit_route(self, request):
        return lifecycle.ProductionSensitivityRuntime._admit_route(self, request)

    def _observe_route_result(self, request, response):
        return lifecycle.ProductionSensitivityRuntime._observe_route_result(self, request, response)


class _Daemon:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or {"success": True}

    def dispatch(self, request):
        self.calls.append(request)
        return self.response


def _guard_request(operation: str, args=None):
    return {"operation": operation, "arguments": args or {},
            "execution": {"project_id": "project-guard", "session_id": "session-guard",
                          "request_id": "request-123", "idempotency_key": "key-123"}}


def test_dispatch_guard_refuses_wrong_phase_before_public_dispatch_and_quarantines_unknown(tmp_path):
    owner = _DispatchOwner(tmp_path)
    daemon = _Daemon()
    guarded = lifecycle._AdmissionDaemon(owner, daemon)
    with pytest.raises(lifecycle._LifecycleDispatchRejected):
        guarded.dispatch(_guard_request("model_create", {"name": "unapproved"}))
    assert daemon.calls == []
    assert owner._unknown is False

    daemon.response = {"success": True, "data": {"worker": {"status": "RUNNING"}}}
    guarded.dispatch(_guard_request("model_load", {"path": "/tmp/model.mph"}))
    assert len(daemon.calls) == 1
    assert owner._unknown is True
    with pytest.raises(lifecycle._LifecycleDispatchRejected, match="UNKNOWN"):
        guarded.dispatch(_guard_request("model.inspect", {"detail": "summary"}))
    assert len(daemon.calls) == 1


def test_public_entrypoint_wires_the_same_process_state_machine(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path)
    readmission = _readmission(tmp_path, candidate)
    runtime = _Runtime()
    clock = _Clock(1002.0)
    factory = _Factory(runtime)
    monkeypatch.setattr(lifecycle, "load_candidate", lambda _path, _sha: candidate)
    monkeypatch.setattr(lifecycle, "readmit_sensitivity_setup_artifacts",
                        lambda *_args, **_kwargs: readmission)
    monkeypatch.setattr(lifecycle, "execute_sensitivity_campaign",
                        lambda *_args, **_kwargs: {
                            "status": "ALL_14_CAPTURED_SENSITIVITY_GATES_PASS_REVIEW_REQUIRED",
                            "actual_study_run_submissions": 14})
    result = lifecycle.run_sensitivity_lifecycle(
        candidate_path=tmp_path / "frozen-candidate.json",
        expected_candidate_sha256=candidate["candidate_sha256"],
        evidence_dir=tmp_path / "public-entry-evidence",
        server_work=Path("/private/tmp") / f"w24-lifecycle-test-{uuid.uuid4().hex}",
        approval_waiter=lambda **kwargs: _write_approval_from_waiter_kwargs(kwargs, clock),
        runtime_factory=factory, clock=clock.now, sleep=clock.sleep)
    assert runtime.server_starts == 1
    assert runtime.budget.birth_epoch_s == runtime.server_epoch
    assert result["status"] == "SCIENCE_TERMINAL_CLEANUP_VERIFIED_REQUIRES_INDEPENDENT_REVIEW", result.get("error")
