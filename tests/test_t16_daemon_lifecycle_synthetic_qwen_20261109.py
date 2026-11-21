"""T16 (Qwen, 2026-11-09): synthetic lifecycle/receipt tests for the T16 harness.

These tests never start a real control daemon, never bind a listening socket,
and never read the COMSOL help tree.  They drive the *harness helpers* of
``test_t16_docs_installed_qwen_20261109`` (the production T16 chain test file)
with fake child processes and a fast clock, and they fail (RED) against the
2026-11-09 morning harness because that harness had three defects:

1. ``_start_task_owned_daemon`` raised on endpoint-missing / invalid-endpoint
   / early-exit paths *without releasing the child it had just spawned*, and
   the first production test asserted ``ps`` cmdline ownership *before* its
   try/finally, so a failed ownership assert also leaked the child.
2. ``_cleanup_and_receipt`` computed the receipt ``status`` from cleanup
   evidence alone, so a chain (link) assertion failure still persisted a
   ``PASS`` receipt; and an unkillable child escalated into an uncaught
   ``TimeoutExpired`` that wrote no receipt at all.
3. The receipt claimed ``network_used: false`` while the chain genuinely used
   an authenticated *loopback* RPC, and gave no way to mark a
   characterization PASS of a known-defective old artifact as
   ``fix_verified: false``.

The fix separates the three dimensions explicitly in the receipt:
``chain_status`` (link assertions), ``cleanup_status`` (child/port release),
and ``evidence_class`` + ``fix_verified`` (characterization vs fix), replaces
``network_used`` with ``loopback_rpc_used`` / ``external_network_used``, and
makes every daemon-start validation failure release the exact task-owned
child before re-raising.  Historical receipts are not rewritten by this file.

Second same-day repair (same-day v2, pinned by the tests appended below):
against the corrected morning harness these NEW tests are RED before and
GREEN after the follow-up fix, and they pin two residual ownership/evidence
defects the morning fix left open:

4. Ownership gap after the starter returns: the production chain tests
   sequenced ``_start_task_owned_daemon`` -> receipt back-fill (``daemon``
   record incl. cmdline hashing) -> try/finally by themselves, so a failure
   inside that gap (receipt construction / hashing) leaked the started
   task-owned child with no cleanup record.  The harness now owns the whole
   flow in ``_run_task_owned_chain_session``: once the start returns, the
   child is already inside the try/finally that releases it and persists a
   chain_status NOT_RECORDED -> FAIL receipt.
5. Release-path exceptions: ``_release_task_owned_child`` did ``poll() ->
   terminate() -> wait(8) -> kill() -> wait(5)`` unguarded, so a child dying
   between poll and terminate (PermissionError), or terminate/kill/poll
   raising, escaped ``_cleanup_and_receipt`` and destroyed the whole cleanup
   record (no receipt written).  Release now never raises: races are proved
   by re-poll, and any unprovable failure is recorded in
   ``cleanup["release_error"]`` which forces cleanup/status FAIL (stop-state
   and error reporting can never be mislabeled PASS).
"""
from __future__ import annotations

import inspect
import json
import socket
import subprocess
import sys
import time as real_time
from pathlib import Path

import pytest

try:  # tests/ is a package; both import forms appear in this repo
    import test_t16_docs_installed_qwen_20261109 as t16
except ImportError:  # pragma: no cover - depends on pytest import mode
    from tests import test_t16_docs_installed_qwen_20261109 as t16

FAKE_PID = 987_654_321  # fake child; never an OS pid we touch


class FakeChild:
    """Stand-in for subprocess.Popen child; records release attempts."""

    def __init__(self, command, *, running=True, stubborn=False, early_exit_code=None):
        self.command = list(command)
        self.pid = FAKE_PID
        self.running = running
        self.stubborn = stubborn
        self.exit_code = early_exit_code
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def returncode(self):
        return self.poll()

    def poll(self):
        return None if self.running else (self.exit_code if self.exit_code is not None else 0)

    def wait(self, timeout=None):
        status = self.poll()
        if status is not None:
            return status
        if self.stubborn:
            raise subprocess.TimeoutExpired(cmd=self.command[0], timeout=timeout or 0)
        if self.terminate_calls or self.kill_calls:
            self.running = False
            self.exit_code = -15
            return self.exit_code
        raise subprocess.TimeoutExpired(cmd=self.command[0], timeout=timeout or 0)

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1


class FastClock:
    """t16.time shim: the 20 s endpoint deadline collapses to a few loops."""

    def __init__(self):
        self._now = 0.0

    def monotonic(self):
        self._now += 1.0
        return self._now

    def sleep(self, seconds):
        real_time.sleep(0.002)


def _arm_fakes(monkeypatch, store, *, running=True, stubborn=False, early_exit_code=None,
               cmdline_home=None):
    def fake_popen(command, **kwargs):
        child = FakeChild(command, running=running, stubborn=stubborn,
                          early_exit_code=early_exit_code)
        store["child"] = child
        return child

    def fake_run(cmd, *args, **kwargs):
        home = str(cmdline_home if cmdline_home is not None else store.get("home", ""))
        stdout = f"{sys.executable} -m comsol_mcp._control_daemon --home {home}"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(t16, "time", FastClock())
    monkeypatch.setattr(t16.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(t16.subprocess, "run", fake_run)
    monkeypatch.delenv("COMSOL_T16_RECEIPT_DIR", raising=False)


def _env_and_home(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    control_home = root / "control-private"
    control_home.mkdir(parents=True)
    env = t16._child_env(root, home=root / "home", project=root / "project", pythonpath=None)
    return root, env, control_home


def _write_endpoint(control_home, *, pid=FAKE_PID, port=54321, token="synthetic-token"):
    endpoint = {"schema": "synthetic", "pid": pid, "port": port, "token": token}
    (control_home / "control.json").write_text(json.dumps(endpoint), encoding="utf-8")
    return endpoint


@pytest.mark.parametrize("failure", ["endpoint_absent", "pid_mismatch", "bad_port", "missing_token"])
def test_daemon_start_validation_failures_release_their_own_child(tmp_path, monkeypatch, failure):
    """A task-owned daemon start must not leave its own child when validation raises."""
    root, env, control_home = _env_and_home(tmp_path)
    store: dict = {"home": control_home}
    _arm_fakes(monkeypatch, store)
    if failure == "pid_mismatch":
        _write_endpoint(control_home, pid=FAKE_PID + 1)
    elif failure == "bad_port":
        _write_endpoint(control_home, port=0)
    elif failure == "missing_token":
        _write_endpoint(control_home, token="")
    # endpoint_absent: control.json never appears.

    with pytest.raises(AssertionError):
        t16._start_task_owned_daemon(root, env, control_home, root / "daemon.log")

    child = store["child"]
    assert child.terminate_calls + child.kill_calls >= 1, \
        f"{failure}: validation failed but the task-owned child was never released"
    assert child.poll() is not None, f"{failure}: task-owned child still running after failure"


def test_cmdline_ownership_mismatch_is_checked_before_return_and_releases_child(
        tmp_path, monkeypatch):
    """ps-cmdline ownership is the starter's responsibility, not a post-return assert."""
    root, env, control_home = _env_and_home(tmp_path)
    store: dict = {"home": control_home}
    _arm_fakes(monkeypatch, store)
    _write_endpoint(control_home)
    monkeypatch.setattr(t16.subprocess, "run",
                        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, "some unrelated process", ""))

    with pytest.raises(AssertionError):
        t16._start_task_owned_daemon(root, env, control_home, root / "daemon.log")

    child = store["child"]
    assert child.terminate_calls + child.kill_calls >= 1, \
        "cmdline ownership mismatch must release the task-owned child"
    assert child.poll() is not None


def test_early_exit_is_reported_and_no_live_child_remains(tmp_path, monkeypatch):
    """Pinning check (green before and after): early exit raises with no hang."""
    root, env, control_home = _env_and_home(tmp_path)
    store: dict = {"home": control_home}
    _arm_fakes(monkeypatch, store, running=False, early_exit_code=3)

    with pytest.raises(AssertionError, match="exited early"):
        t16._start_task_owned_daemon(root, env, control_home, root / "daemon.log")
    assert store["child"].poll() == 3


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            pytest.fail("chosen probe port unexpectedly reachable")
    return port


def _clean_exit_child():
    class ExitedChild:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0
    return ExitedChild()


def _run_cleanup(tmp_path, receipt, *, child=None, port=None, chain_status=None):
    log_path = tmp_path / "daemon.log"
    log_path.write_text("synthetic log\n", encoding="utf-8")
    endpoint = {"port": port or _free_port()}
    if chain_status is not None:
        receipt["chain_status"] = chain_status
    receipt.setdefault("receipt_file", str(tmp_path / "receipt.json"))
    receipt.setdefault("identity_kind", "synthetic")
    t16._cleanup_and_receipt(child or _clean_exit_child(), endpoint, log_path, receipt)
    persisted = json.loads(Path(receipt["receipt_file"]).read_text(encoding="utf-8"))
    return receipt, persisted


def test_chain_assertion_failure_marks_receipt_FAIL_not_PASS(tmp_path):
    """The core defect: cleanup-clean but chain-broken runs must persist FAIL."""
    receipt = {"schema": "synthetic", "chain_status": "FAIL",
               "chain_failure": "AssertionError: synthetic chain break",
               "evidence_class": "production-chain-fix-verification"}
    receipt, persisted = _run_cleanup(tmp_path, receipt, chain_status="FAIL")
    assert receipt["chain_status"] == "FAIL"
    assert receipt["cleanup_status"] == "PASS", "cleanup itself succeeded and stays PASS"
    assert receipt["status"] == "FAIL", "status must not be PASS when the chain failed"
    assert persisted["status"] == "FAIL"
    assert "synthetic chain break" in persisted["chain_failure"]


def test_missing_chain_status_defaults_to_FAIL(tmp_path):
    """A caller that never recorded a chain result cannot yield a PASS receipt."""
    receipt, _ = _run_cleanup(tmp_path, {"schema": "synthetic"})
    assert receipt["chain_status"] == "NOT_RECORDED"
    assert receipt["status"] == "FAIL"


def test_stubborn_child_writes_FAIL_receipt_instead_of_crashing(tmp_path):
    """Unkillable child: cleanup must record FAIL and persist, not raise away the receipt."""
    child = FakeChild(["fake"], running=True, stubborn=True)
    receipt = {"schema": "synthetic"}
    receipt, persisted = _run_cleanup(tmp_path, receipt, child=child)
    assert child.terminate_calls >= 1 and child.kill_calls >= 1
    assert receipt["cleanup_status"] == "FAIL"
    assert receipt["status"] == "FAIL"
    assert persisted["cleanup"]["exact_child_exited"] is False


def test_receipt_distinguishes_loopback_from_external_network(tmp_path):
    """Loopback RPC was used honestly; only external network use is claimed False."""
    receipt, persisted = _run_cleanup(tmp_path, {"schema": "synthetic"}, chain_status="PASS")
    observations = persisted["network_observations"]
    assert observations["loopback_rpc_used"] is True
    assert observations["external_network_used"] is False
    assert "network_used" not in persisted["prohibited_actions_not_taken"]
    assert persisted["prohibited_actions_not_taken"]["external_network_used"] is False


def test_characterization_pass_is_never_fix_verified(tmp_path):
    """Characterizing an old artifact's reproduced defect is not a fix PASS."""
    receipt, persisted = _run_cleanup(
        tmp_path, {"schema": "synthetic",
                   "evidence_class": "characterization-pre-fix-installed-artifact"},
        chain_status="PASS")
    assert receipt["status"] == "PASS"
    assert persisted["fix_verified"] is False
    assert persisted["evidence_class"] == "characterization-pre-fix-installed-artifact"

    receipt2, persisted2 = _run_cleanup(
        tmp_path,
        {"schema": "synthetic", "evidence_class": "production-chain-fix-verification",
         "receipt_file": str(tmp_path / "receipt2.json")},
        chain_status="PASS")
    assert receipt2["status"] == "PASS"
    assert persisted2["fix_verified"] is True


# ---------------------------------------------------------------------------
# Same-day v2 repair pins: ownership gap after the starter returns, and
# exceptions on the release path (fake children only; no real pid is ever
# signalled and no real daemon is started).
# ---------------------------------------------------------------------------


class RaceChild:
    """Fake child with injected release-path faults (never a real pid).

    ``poll_sequence`` yields a return value or raises the listed exception
    per poll() call; the last entry repeats.  terminate/wait/kill errors are
    injected verbatim to pin the poll->terminate/kill exit races.
    """

    def __init__(self, *, poll_sequence, terminate_error=None, wait_error=None, kill_error=None):
        self.pid = FAKE_PID
        self.poll_sequence = list(poll_sequence)
        self.poll_calls = 0
        self.terminate_error = terminate_error
        self.wait_error = wait_error
        self.kill_error = kill_error
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        entry = self.poll_sequence[min(self.poll_calls, len(self.poll_sequence) - 1)]
        self.poll_calls += 1
        if isinstance(entry, BaseException):
            raise entry
        return entry

    def terminate(self):
        self.terminate_calls += 1
        if self.terminate_error is not None:
            raise self.terminate_error

    def wait(self, timeout=None):
        if self.wait_error is not None:
            raise self.wait_error
        return self.poll()

    def kill(self):
        self.kill_calls += 1
        if self.kill_error is not None:
            raise self.kill_error


def test_terminate_race_after_child_exit_still_records_proven_cleanup(tmp_path):
    """Child died between poll() and terminate(): re-poll proves the exit.

    Pre-fix the PermissionError from terminate() escaped _cleanup_and_receipt
    and no receipt was written at all.  Post-fix the race is proven dead by
    re-poll (evidence, not assumption) and cleanup may legitimately PASS.
    """
    child = RaceChild(poll_sequence=[None, -15],
                      terminate_error=PermissionError(3, "No such process"))
    receipt, persisted = _run_cleanup(
        tmp_path, {"schema": "synthetic", "chain_status": "PASS",
                   "evidence_class": "production-chain-fix-verification"}, child=child)
    assert child.terminate_calls == 1 and child.kill_calls == 0
    assert persisted["cleanup"]["release_error"] is None
    assert persisted["cleanup"]["exact_child_exited"] is True
    assert persisted["cleanup_status"] == "PASS"
    assert persisted["status"] == "PASS" and persisted["fix_verified"] is True


def test_terminate_failure_with_live_child_persists_cleanup_FAIL(tmp_path):
    """terminate() raising with the child provably alive must FAIL, not vanish."""
    child = RaceChild(poll_sequence=[None], terminate_error=OSError(1, "Operation not permitted"))
    receipt, persisted = _run_cleanup(
        tmp_path, {"schema": "synthetic", "chain_status": "PASS",
                   "evidence_class": "production-chain-fix-verification"}, child=child)
    assert child.kill_calls == 0
    # type name is errno-mapped (OSError(1) -> PermissionError), so assert the
    # provenance prefix, not the literal class name.
    assert persisted["cleanup"]["release_error"].startswith("terminate failed:")
    assert persisted["cleanup"]["exact_child_exited"] is False
    assert persisted["cleanup_status"] == "FAIL", "stop state may not be PASS with a release failure"
    assert persisted["status"] == "FAIL" and persisted["fix_verified"] is False


def test_kill_failure_after_wait_timeout_persists_cleanup_FAIL(tmp_path):
    """Escalation kill() raising while the child stays alive: FAIL, receipt kept."""
    child = RaceChild(poll_sequence=[None],
                      wait_error=subprocess.TimeoutExpired(cmd="fake", timeout=8),
                      kill_error=OSError(3, "No such process"))
    receipt, persisted = _run_cleanup(
        tmp_path, {"schema": "synthetic", "chain_status": "PASS",
                   "evidence_class": "production-chain-fix-verification"}, child=child)
    assert child.terminate_calls >= 1 and child.kill_calls >= 1
    # errno 3 maps to ProcessLookupError; pin the provenance prefix.
    assert persisted["cleanup"]["release_error"].startswith("kill failed:")
    assert persisted["cleanup"]["exact_child_exited"] is False
    assert persisted["cleanup_status"] == "FAIL"
    assert persisted["status"] == "FAIL" and persisted["fix_verified"] is False


def test_poll_failure_on_release_persists_error_instead_of_crashing(tmp_path):
    """poll() itself raising must degrade to a recorded FAIL, never crash cleanup."""
    child = RaceChild(poll_sequence=[OSError(9, "Bad file descriptor")])
    receipt, persisted = _run_cleanup(
        tmp_path, {"schema": "synthetic", "chain_status": "PASS",
                   "evidence_class": "production-chain-fix-verification"}, child=child)
    assert persisted["cleanup"]["release_error"] is not None
    assert persisted["cleanup"]["exact_child_exited"] is False
    assert persisted["cleanup_status"] == "FAIL", "unknown stop state may never be PASS"
    assert persisted["status"] == "FAIL"


def test_kill_race_where_child_already_died_records_proven_cleanup(tmp_path):
    """kill() raising because the child already died: re-poll proves it, PASS is earned."""
    child = RaceChild(poll_sequence=[None, -9],
                      wait_error=subprocess.TimeoutExpired(cmd="fake", timeout=8),
                      kill_error=PermissionError(3, "No such process"))
    receipt, persisted = _run_cleanup(
        tmp_path, {"schema": "synthetic", "chain_status": "PASS",
                   "evidence_class": "production-chain-fix-verification"}, child=child)
    assert child.kill_calls >= 1
    assert persisted["cleanup"]["release_error"] is None
    assert persisted["cleanup"]["exact_child_exited"] is True
    assert persisted["cleanup_status"] == "PASS"


def _session_inputs(tmp_path, monkeypatch, *, with_endpoint=True):
    root, env, control_home = _env_and_home(tmp_path)
    store: dict = {"home": control_home}
    _arm_fakes(monkeypatch, store)
    if with_endpoint:
        _write_endpoint(control_home, port=_free_port())
    return root, env, control_home, store


def _require_session_helper():
    session = getattr(t16, "_run_task_owned_chain_session", None)
    assert session is not None, (
        "the harness must run start -> receipt back-fill -> chain -> cleanup as one "
        "ownership session; caller-side sequencing in the gap after the starter's "
        "return leaks the task-owned child with no cleanup record")
    return session


def _synthetic_receipt(tmp_path):
    return {"schema": "synthetic", "identity_kind": "synthetic",
            "receipt_file": str(tmp_path / "receipt.json"),
            "evidence_class": "production-chain-fix-verification"}


def test_session_receipt_backfill_failure_releases_child_and_persists_FAIL(tmp_path, monkeypatch):
    """Injected failure in receipt back-fill (cmdline hashing) after a good start.

    This is the ownership gap between the starter's return and the caller's
    try/finally in the pre-fix harness: the child leaked and no receipt ever
    existed.  Post-fix the session helper releases the child and persists
    chain_status NOT_RECORDED -> overall FAIL.
    """
    root, env, control_home, store = _session_inputs(tmp_path, monkeypatch)
    session = _require_session_helper()
    receipt = _synthetic_receipt(tmp_path)

    def boom(data=b""):
        raise RuntimeError("injected receipt backfill hashing failure")

    monkeypatch.setattr(t16.hashlib, "sha256", boom)
    with pytest.raises(RuntimeError, match="injected receipt backfill"):
        session(root, env, control_home, root / "daemon.log", receipt, lambda: {"ok": True})
    child = store["child"]
    assert child.terminate_calls + child.kill_calls >= 1, "back-fill failure leaked the task-owned child"
    assert child.poll() is not None, "task-owned child still running after back-fill failure"
    persisted = json.loads(Path(receipt["receipt_file"]).read_text(encoding="utf-8"))
    assert persisted["chain_status"] == "NOT_RECORDED"
    assert persisted["cleanup_status"] == "PASS"
    assert persisted["status"] == "FAIL"
    assert persisted["fix_verified"] is False


def test_session_chain_failure_still_releases_child_and_persists_FAIL(tmp_path, monkeypatch):
    """A chain break inside the session keeps the link FAIL and releases the child."""
    root, env, control_home, store = _session_inputs(tmp_path, monkeypatch)
    session = _require_session_helper()
    receipt = _synthetic_receipt(tmp_path)

    def broken_chain():
        raise AssertionError("synthetic chain break inside session")

    with pytest.raises(AssertionError, match="synthetic chain break inside session"):
        session(root, env, control_home, root / "daemon.log", receipt, broken_chain)
    child = store["child"]
    assert child.terminate_calls + child.kill_calls >= 1
    assert child.poll() is not None
    persisted = json.loads(Path(receipt["receipt_file"]).read_text(encoding="utf-8"))
    assert persisted["chain_status"] == "FAIL"
    assert "synthetic chain break inside session" in persisted["chain_failure"]
    assert persisted["cleanup_status"] == "PASS"
    assert persisted["status"] == "FAIL" and persisted["fix_verified"] is False


def test_session_start_failure_leaves_no_child_and_no_receipt(tmp_path, monkeypatch):
    """Start itself failing: the starter already released its child; no receipt claimed."""
    root, env, control_home, store = _session_inputs(tmp_path, monkeypatch, with_endpoint=False)
    session = _require_session_helper()
    receipt = _synthetic_receipt(tmp_path)
    with pytest.raises(AssertionError, match="did not appear before the deadline"):
        session(root, env, control_home, root / "daemon.log", receipt, lambda: {"ok": True})
    child = store["child"]
    assert child.terminate_calls + child.kill_calls >= 1
    assert child.poll() is not None
    assert not (tmp_path / "receipt.json").exists(), "a failed start must not fabricate a receipt"


@pytest.mark.parametrize("test_name", [
    "test_installed_stdio_chain_finds_sourced_unfamiliar_api_in_real_6_4_help",
    "test_worktree_source_chain_closes_marker_and_scoring_gaps",
    "test_pinned_candidate_wheel_installed_offline_in_new_venv_runs_the_fixed_chain",
])
def test_production_chain_tests_route_through_the_ownership_session(test_name):
    """Ownership is only safe where the harness owns it: no production test may
    sequence the starter itself, leaving a failure-prone gap before try/finally."""
    source = inspect.getsource(getattr(t16, test_name))
    assert "_run_task_owned_chain_session(" in source, \
        f"{test_name} must run its daemon through the session helper"
    assert "_start_task_owned_daemon(" not in source, \
        f"{test_name} must not sequence the raw starter (ownership gap before try/finally)"
