"""T16/U03 (Qwen, 2026-11-09): installed public MCP stdio -> real control daemon
-> installed OfflineDocsIndex over the locally installed official COMSOL 6.4
help, used to locate one unfamiliar-but-legal API with a verifiable source.

Difference from the 2026-11-07 T16 evidence (which must not be re-run here):
that run covered a substituted control-client boundary in the stdio child and
an in-process probe.  These tests exercise the full production transport:
an authenticated loopback RPC to one *task-owned* control daemon started in a
brand-new isolated root, plus the console-module entry
``python -m comsol_mcp.mcp_server`` imported from the venv site-packages
(installed identity run) or from the worktree source (source identity run).

Safety: no COMSOL, Java Worker, Desktop, SSH, or previously started daemon,
model, or socket is touched.  Each test starts exactly one control daemon
under its own pytest tmp_path, proves the endpoint pid equals that child, and
terminates that exact pid.  The COMSOL help tree is read-only.  Opt-in like
the existing loopback fixture because some sandboxes prohibit loopback bind.

Same-day harness correction (2026-11-09, pinned by
``test_t16_daemon_lifecycle_synthetic_qwen_20261109``): the morning version of
this file (a) leaked its own task-owned daemon child when endpoint wait,
endpoint validation, or the pre-try/finally ``ps`` ownership assert raised,
and (b) computed the receipt ``status`` from cleanup evidence alone, so a
chain-assertion failure still persisted ``status: PASS``, and (c) claimed
``network_used: false`` although the chain does use authenticated loopback
RPC.  The helpers below now (a) release exactly the child started here on
every pre-return failure (ownership is checked inside the starter), (b)
persist ``chain_status``/``cleanup_status`` separately with overall PASS only
when both pass, plus ``evidence_class``/``fix_verified`` so a characterization
of the pre-fix installed artifact is never booked as a fix verification, and
(c) report ``loopback_rpc_used``/``external_network_used``.  Morning receipts
are historical and are not rewritten; the copy name here gained a ``-v2``
suffix.  Historical receipt ``status`` values only ever reflected cleanup;
the morning link verdicts stand on the independently logged pytest exit codes.

Same-day second correction (pinned by the v2 section of the same synthetic
file): (a) the chain tests no longer sequence the starter themselves — the
whole start -> receipt back-fill -> chain -> cleanup flow sits in
``_run_task_owned_chain_session``, so a receipt-construction/hashing failure
in the old gap between the starter's return and the try/finally releases the
task-owned child and persists a chain_status NOT_RECORDED -> FAIL receipt
instead of leaking the daemon; and (b) ``_release_task_owned_child`` guards
every poll/terminate/wait/kill call, records unprovable failures in
``cleanup["release_error"]`` (which forces cleanup/status FAIL) and proves
poll->terminate/kill exit races by re-poll, so a release-path exception can
no longer escape ``_cleanup_and_receipt`` and lose the whole cleanup record.

Third same-day correction (pinned by
``test_t16_harness_v3_fidelity_contract_qwen_20261109``; closes review gaps
A–F of ``T16-harness-v2-fidelity-review-qwen-20261109``): (A) a failed start
whose child release cannot be proven now raises with the release provenance
chained to the original failure instead of dropping it; (B) the control-port
probe books ``control_port_unreachable`` only on an explicit ECONNREFUSED —
a raising probe or any other status is reported in ``port_probe_error`` and
fails cleanup instead of silently becoming a PASS precondition; (C)
``loopback_rpc_used`` follows the chain transport's call arrival instead of a
hardcoded True, so a NOT_RECORDED run claims no RPC it never performed; (D)
receipt write/copy faults are guarded and recorded (``receipt_persisted``/
``persistence_error``/``copy_error``) instead of masking the link verdict or
vanishing the record, and the session finally cannot have its propagating
exception replaced; (E) the installed branch books fix-verification only
together with the full ``_assert_fixed_chain_invariants`` set; and (F)
``fix_verified`` fails closed — only an explicitly declared fix-verification
``evidence_class`` can book it.

Fourth same-day correction (pinned by
``test_t16_harness_v4_persistence_qwen_20261109``; closes the parent-agent
counterexamples in ``T16-harness-v3-parent-counterexamples-20261109.txt``
against frozen f93e8df3…): (P) the receipt is persisted as a revision
protocol — the file on disk no longer freezes ``receipt_persisted: false``
while memory claims ``true``, a ``copy_error`` also lands in the persisted
primary, and a successful evidence-dir copy is re-copied so it matches the
final primary byte-for-byte; whenever persistence is claimed, the on-disk
primary equals the in-memory record. (L) loopback usage has three honest
states — never_attempted / attempted_unverified / confirmed: an empty
``checks`` dict written by a finally is no longer treated as proof of an
RPC, the transport-entry/attempt/confirmation facts are recorded at their
real points inside the stdio body, and only a completed chain verdict or an
observed successful daemon-mediated answer may describe an authenticated
loopback RPC. (S) if ``_cleanup_and_receipt`` itself faults with an
unexpected exception, the session finally downgrades the record
(``cleanup_record_error`` + cleanup/status FAIL + fix_verified False) and
re-persists it, so a half-built cleanup record can never book a PASS on disk
or in memory. (E) the start-failure release fallback no longer builds a
self-referential ``exc.__cause__ = exc`` cycle nor calls ``add_note``
(Python 3.11+ only); it raises a plain wrapper carrying the original as
``__cause__`` so both failures stay visible on every supported Python.

Third identity (opt-in via ``COMSOL_T16_WHEEL_PATH``/``_SHA256``/``_ROOT``/
``_OLD_ARTIFACT_DIR``): installs the pinned T42 candidate wheel offline into
a brand-new isolated venv (no reuse of any existing venv), then runs the same
production chain against those bytes and asserts the post-fix behavior.
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HELP_ROOT = Path("/Applications/COMSOL64/Multiphysics/doc")
# Real official 6.4 Java API javadoc subtree (ModelEntity/ModelUtil live here).
API_SUBTREE = HELP_ROOT / (
    "help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/api/com/comsol/model")
_KEY_FILES = ("_g2_docs.py", "_managed_backend.py", "_control_daemon.py", "_mcp_gateway.py")
# v3 contract F (fail-closed booking): fix_verified is only ever claimed for an
# explicitly declared fix-verification class; "unspecified" or any unknown/
# experimental class can never book it.
_FIX_VERIFICATION_CLASSES = frozenset({"production-chain-fix-verification"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _child_env(root: Path, *, home: Path, project: Path, pythonpath: str | None) -> dict:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root / "isolated-home"),
        "TMPDIR": str(root),
        "COMSOL_SERVER_MCP_HOME": str(home),
        "COMSOL_PROJECT_ROOT": str(project),
        "COMSOL_MCP_TOOL_PROFILE": "full",
    }
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    return env


def _module_identity(env: dict, python: str | None = None) -> dict:
    python = python or sys.executable
    probe = subprocess.run(
        [python, "-c",
         "import json, platform, comsol_mcp, mcp; "
         "print(json.dumps({'file': comsol_mcp.__file__, 'mcp_file': mcp.__file__, "
         "'python_version': platform.python_version()}))"],
        cwd=str(Path(python).parent), env=env, capture_output=True, text=True, timeout=30)
    if probe.returncode != 0:
        raise AssertionError(f"identity probe failed: {probe.stderr[:400]}")
    return json.loads(probe.stdout)


def _release_task_owned_child(process) -> dict:
    """Release exactly the child this task started; never raise.

    Signals only the Popen handle returned here — never a scanned pid, a port
    probe target, or a pre-existing daemon/socket.  Every OS call is guarded:
    a child that dies in the race between poll() and terminate()/kill() is
    proved dead by a re-poll, and any failure that cannot be proved that way
    (terminate/kill/poll raising, a child outliving escalate-and-kill) is
    reported in the returned ``{"released", "error"}`` status instead of
    escaping and destroying the caller's cleanup record — the unguarded
    morning version let the exception escape and wrote no receipt at all.
    """
    status = {"released": False, "error": None}

    def _safe_poll():
        try:
            return process.poll()
        except (OSError, ValueError):
            return None

    try:
        if process.poll() is not None:
            status["released"] = True
            return status
        try:
            process.terminate()
        except (OSError, ValueError) as exc:
            if _safe_poll() is not None:
                status["released"] = True  # exited between poll and terminate
            else:
                status["error"] = f"terminate failed: {type(exc).__name__}: {exc}"
            return status
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except (OSError, ValueError) as exc:
                if _safe_poll() is None:
                    status["error"] = f"kill failed: {type(exc).__name__}: {exc}"
                    return status
                # else: kill raced with the exit and re-poll proves the child dead
            else:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        status["released"] = _safe_poll() is not None
    except (OSError, ValueError) as exc:
        status["error"] = f"release failed: {type(exc).__name__}: {exc}"
    return status


def _wait_for_task_owned_endpoint(control_home: Path, process, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"task-owned control daemon exited early: {process.poll()}")
        try:
            return json.loads((control_home / "control.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError("task-owned control endpoint did not appear before the deadline")


def _start_task_owned_daemon(root: Path, env: dict, control_home: Path, log_path: Path, *,
                             python: str | None = None, endpoint_timeout: float = 20.0) -> tuple:
    """Start one task-owned control daemon and fully validate it before returning.

    Every pre-return failure — early exit, missing/invalid endpoint, or a
    ps-cmdline ownership mismatch — releases the child started here and then
    re-raises, so a failed start never leaves its own daemon behind.  The
    ownership check lives inside the starter on purpose: an assert the caller
    runs only after the return would leak the child until the caller's own
    try/finally exists.  If the release itself cannot be PROVEN (terminate/kill
    raised and re-poll still shows the child alive), the raised exception also
    carries that release provenance — the caller sees the original start
    failure AND the unprovable stop, never a silent drop of either.  Returns
    (process, endpoint, cmdline).
    """
    python = python or sys.executable
    command = [python, "-m", "comsol_mcp._control_daemon", "--home", str(control_home)]
    with log_path.open("wb") as stream:
        process = subprocess.Popen(command, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        endpoint = _wait_for_task_owned_endpoint(control_home, process, endpoint_timeout)
        assert endpoint.get("pid") == process.pid
        assert type(endpoint.get("port")) is int and 1 <= endpoint["port"] <= 65535
        assert isinstance(endpoint.get("token"), str) and endpoint["token"]
        ps = subprocess.run(["ps", "-o", "args=", "-p", str(process.pid)],
                            capture_output=True, text=True)
        cmdline = ps.stdout.strip()
        assert str(control_home) in cmdline, "ps cmdline must name this run's own control home"
    except BaseException as exc:
        release = _release_task_owned_child(process)
        if not release["released"]:
            # v3 contract A: a dropped release status would hide that this
            # run's own child may still be alive — surface BOTH the original
            # start failure and the unprovable stop in one visible chain.
            detail = release["error"] or "child stop-state could not be proven"
            message = (f"{exc} | task-owned child release NOT PROVEN after start "
                       f"failure: {detail} (the stop was not proved; do not treat "
                       "the daemon as confirmed stopped)")
            try:
                chained = type(exc)(message)
            except Exception:
                # v4: the fallback must not touch the original exception.
                # ``chained = exc`` would end in ``exc.__cause__ = exc`` — a
                # self-referential cycle — and ``exc.add_note(...)`` exists
                # only on Python 3.11+ (this package supports 3.10).  A plain
                # wrapper carries the release provenance in its message and
                # keeps the original start failure as __cause__ everywhere.
                chained = RuntimeError(message)
            chained.__cause__ = exc
            raise chained
        raise
    return process, endpoint, cmdline


def _run_chain_recorded(receipt: dict, run):
    """Run the chain plus its invariant asserts, recording the LINK result.

    A chain (link) assertion failure is recorded as chain_status FAIL and
    re-raised so the pytest verdict stays red; the persisted receipt then
    says overall FAIL even when cleanup was perfectly clean.  PASS here is
    only ever a LINK verdict; cleanup and evidence class are separate axes
    combined in ``_cleanup_and_receipt``.
    """
    try:
        outcome = run()
    except BaseException as exc:
        receipt["chain_status"] = "FAIL"
        receipt["chain_failure"] = f"{type(exc).__name__}: {exc}"[:1000]
        raise
    receipt["chain_status"] = "PASS"
    receipt["chain_failure"] = None
    return outcome


def _run_task_owned_chain_session(root: Path, env: dict, control_home: Path, log_path: Path,
                                  receipt: dict, run_chain, *, python: str | None = None,
                                  endpoint_timeout: float = 20.0) -> None:
    """Own the whole task-owned daemon lifecycle as one atomic try/finally.

    The morning harness had the three chain tests sequence the starter, the
    receipt back-fill (the ``daemon`` record incl. cmdline hashing), and their
    own try/finally separately: a failure in that gap leaked the started child
    with no cleanup record.  Here ownership transfer is atomic — the moment
    ``_start_task_owned_daemon`` returns, the child is already inside the
    try/finally that releases it and persists the receipt, so a back-fill or
    hashing failure releases the child and records ``chain_status:
    NOT_RECORDED`` -> overall FAIL.  If the start itself fails, the starter
    has already released its own child and no receipt is claimed here.
    """
    process = endpoint = None
    try:
        process, endpoint, cmdline = _start_task_owned_daemon(
            root, env, control_home, log_path, python=python, endpoint_timeout=endpoint_timeout)
        receipt["daemon"] = {"pid": process.pid, "port": endpoint["port"],
                             "cmdline_sha256": hashlib.sha256(cmdline.encode()).hexdigest(),
                             "token_recorded": False, "home": str(control_home)}
        _run_chain_recorded(receipt, run_chain)
    finally:
        if endpoint is not None:
            try:
                _cleanup_and_receipt(process, endpoint, log_path, receipt)
            except BaseException as exc:  # v3 contract D: a secondary failure in
                # the finally must never replace (mask) the link verdict that is
                # already propagating; report it on the in-memory receipt instead.
                # v4: but a cleanup record whose builder itself faulted is not
                # trustworthy — a half-built record may never book PASS, and an
                # interrupted session may never look successful.  Downgrade and
                # re-persist so the primary file keeps the corrected verdict; if
                # even the repair write faults, that failure is recorded too.
                receipt["cleanup_record_error"] = f"{type(exc).__name__}: {exc}"[:500]
                receipt["cleanup_status"] = "FAIL"
                receipt["status"] = "FAIL"
                receipt["fix_verified"] = False
                try:
                    _persist_receipt(receipt)
                except BaseException as persist_exc:
                    receipt["receipt_repair_error"] = (
                        f"{type(persist_exc).__name__}: {persist_exc}")[:500]


def _persist_receipt(receipt) -> None:
    """Persist the receipt so the file on disk never contradicts memory.

    v4 revision protocol: write a first revision (``receipt_persisted:
    false`` — honest until the write itself answers), attempt the optional
    evidence-dir copy, then rewrite a final revision carrying the now-known
    ``receipt_persisted``/``copy_error`` fields; when the copy succeeded it
    is re-copied from the final primary so the evidence copy matches it
    byte-for-byte.  The morning single-pass write instead froze
    ``receipt_persisted: false`` on disk while memory claimed ``true``, and
    kept ``copy_error`` memory-only.  Persistence is only ever claimed when
    the last write carried the final fields; every later fault downgrades
    that claim instead of leaving a stale PASS record on disk.
    """
    receipt["receipt_persisted"] = False
    receipt.pop("copy_error", None)
    receipt_path = Path(receipt["receipt_file"])
    target_dir = os.environ.get("COMSOL_T16_RECEIPT_DIR")
    copy_path = None
    if target_dir:
        copy_path = Path(target_dir) / (
            f"T16-installed-docs-qwen-20261109-v2-receipt-{receipt['identity_kind']}.json")

    def _write() -> bool:
        try:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8")
            return True
        except OSError as exc:
            receipt["persistence_error"] = (
                f"receipt write failed: {type(exc).__name__}: {exc}")[:500]
            return False

    if not _write():
        return
    if copy_path is not None:
        try:
            copy_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(receipt_path, copy_path)
        except OSError as exc:
            receipt["copy_error"] = (f"receipt evidence-dir copy failed: "
                                     f"{type(exc).__name__}: {exc}")[:500]
    receipt["receipt_persisted"] = True
    if not _write():
        # The disk record still holds (at best) the pre-persistence revision,
        # which itself says receipt_persisted: false — memory agrees.
        receipt["receipt_persisted"] = False
        return
    if copy_path is not None and "copy_error" not in receipt:
        try:
            shutil.copy(receipt_path, copy_path)
        except OSError as exc:
            receipt["copy_error"] = (f"receipt evidence-dir copy of the final revision "
                                     f"failed: {type(exc).__name__}: {exc}")[:500]
            if not _write():
                receipt["receipt_persisted"] = False


def _cleanup_and_receipt(process, endpoint, log_path, receipt) -> None:
    """Release the task-owned child, then persist a three-axis receipt.

    Three axes kept deliberately separate: ``chain_status`` (what the link
    answered), ``cleanup_status`` (did this run release exactly its own
    child and port), and ``evidence_class``/``fix_verified`` (is this PASS a
    fix verification or only a characterization of a known-defective old
    artifact — a characterization PASS is never booked as a fix, and since
    v3 only an explicitly declared fix-verification class can be booked at
    all).  Overall status is PASS only when chain and cleanup both pass; a
    missing chain verdict fails safe.  The port field records unreachability
    ONLY when the OS explicitly refused the connection; a probe fault or an
    ambiguous status is reported in ``port_probe_error`` and fails cleanup
    rather than silently becoming proof.  ``loopback_rpc_used`` follows the
    chain's actual call arrival, and every receipt-write/copy fault is
    recorded instead of raising away the verdict or fabricating a saved
    record.
    """
    release = _release_task_owned_child(process)
    cleanup = {"exact_child_exited": False, "control_port_unreachable": False,
               "engine_evidence_in_daemon_log": [], "release_error": release["error"],
               "port_probe_error": None, "daemon_log_readable": True}
    try:
        cleanup["exact_child_exited"] = process.poll() is not None
    except (OSError, ValueError) as exc:
        if cleanup["release_error"] is None:
            cleanup["release_error"] = f"post-release poll failed: {type(exc).__name__}: {exc}"
    # v3 contract B: only an explicit ECONNREFUSED proves the port is gone.
    # connect_ex raising, or any other nonzero status, stays unconfirmed and
    # is reported — a probe fault must never satisfy a PASS precondition.
    deadline = time.monotonic() + 3.0
    probe_fault: str | None = None
    while time.monotonic() < deadline:
        try:
            with socket.socket() as probe:
                probe.settimeout(0.2)
                status_code = probe.connect_ex(("127.0.0.1", endpoint["port"]))
        except OSError as exc:
            probe_fault = f"port probe raised {type(exc).__name__}: {exc}"
            time.sleep(0.05)
            continue
        if status_code == 0:
            probe_fault = "control port still accepting connections at the deadline"
            time.sleep(0.05)
            continue
        if status_code == errno.ECONNREFUSED:
            cleanup["control_port_unreachable"] = True
            probe_fault = None
            break
        probe_fault = (f"connect_ex returned errno {status_code}, which is not "
                       "ECONNREFUSED (unreachability not proven)")
        time.sleep(0.05)
    if probe_fault is not None:
        cleanup["port_probe_error"] = probe_fault
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
        cleanup["daemon_log_readable"] = False
    cleanup["engine_evidence_in_daemon_log"] = [
        needle for needle in ("PersistentComsolWorker", "mphserver", "javaw", "COMSOL Multiphysics Server")
        if needle.lower() in log_text.lower()
    ]
    receipt["cleanup"] = cleanup
    # An unprovable release failure or an unreadable daemon log can never leave
    # a PASS cleanup: the child stop-state and the error report stay honest FAIL.
    cleanup_status = ("PASS" if cleanup["exact_child_exited"] and cleanup["control_port_unreachable"]
                      and not cleanup["engine_evidence_in_daemon_log"]
                      and not cleanup["release_error"] and cleanup["daemon_log_readable"]
                      else "FAIL")
    receipt["cleanup_status"] = cleanup_status
    chain_status = receipt.get("chain_status", "NOT_RECORDED")
    receipt["chain_status"] = chain_status
    receipt.setdefault("chain_failure", None)
    receipt["status"] = "PASS" if chain_status == "PASS" and cleanup_status == "PASS" else "FAIL"
    evidence_class = receipt.get("evidence_class", "unspecified")
    receipt["evidence_class"] = evidence_class
    # v3 contract F: fail-closed — only an explicitly declared fix-verification
    # class books fix_verified; "unspecified" or any unknown class cannot.
    receipt["fix_verified"] = (receipt["status"] == "PASS"
                               and evidence_class in _FIX_VERIFICATION_CLASSES)
    # v3 contract C + v4: loopback usage follows evidence, never inference
    # from a possibly-empty ``checks`` dict written by a finally (the parent
    # probe showed a run that never entered the stdio transport claiming an
    # authenticated RPC that way).  Three honest states: never_attempted
    # (transport never entered), attempted_unverified (transport entered /
    # daemon-directed calls issued, but no successful daemon-mediated answer
    # observed), and confirmed (a completed chain verdict, or an explicitly
    # observed successful daemon-mediated call in ``_run_docs_chain``).
    loopback_confirmed = (chain_status == "PASS"
                          or receipt.get("loopback_rpc_confirmed") is True)
    loopback_attempted = (loopback_confirmed
                          or receipt.get("loopback_rpc_attempted") is True
                          or receipt.get("loopback_transport_entered") is True)
    if loopback_confirmed:
        loopback_state = "confirmed"
        loopback_note = (
            "the chain authenticated to the task-owned control daemon over 127.0.0.1; "
            "no external network, DNS, or download was used")
    elif loopback_attempted:
        loopback_state = "attempted_unverified"
        loopback_note = (
            "the stdio chain transport was entered and daemon-directed calls may "
            "have been issued, but no successful daemon-mediated response was "
            "observed: loopback RPC remains unproven and no completed loopback "
            "RPC is claimed; no external network, DNS, or download was used")
    else:
        loopback_state = "never_attempted"
        loopback_note = (
            f"the docs-chain RPC transport was never entered in this run "
            f"(chain_status={chain_status}); no loopback RPC was performed and "
            "none is claimed")
    receipt["network_observations"] = {
        "loopback_rpc_used": loopback_confirmed,
        "loopback_rpc_attempted": loopback_attempted,
        "loopback_rpc_confirmed": loopback_confirmed,
        "loopback_rpc_state": loopback_state,
        "external_network_used": False,
        "note": loopback_note,
    }
    receipt["prohibited_actions_not_taken"] = {
        "comsol_engine_started": False, "worker_started": False, "desktop_used": False,
        "ssh_used": False, "preexisting_daemon_or_socket_contacted": False,
        "model_loaded_or_saved": False, "external_network_used": False,
    }
    # v3 contract D: persisting the record must never raise away the verdicts
    # computed above, and an unwritten receipt is never claimed saved.  v4:
    # and the persisted primary/copy must carry the same final persistence
    # fields memory holds (see ``_persist_receipt``).
    _persist_receipt(receipt)



def _call(session, operation_id, inner, execution=None):
    arguments = {"operation_id": operation_id, "arguments": inner}
    if execution is not None:
        arguments["execution"] = execution
    return session.call_tool("registry_call", arguments=arguments)


@pytest.mark.skipif(os.environ.get("COMSOL_MCP_RUN_INSTALLED_DOCS_T16") != "1",
                    reason="opt-in: starts one task-owned control daemon on loopback")
@pytest.mark.skipif(not API_SUBTREE.is_dir(), reason="requires the local official COMSOL 6.4 help tree")
def test_installed_stdio_chain_finds_sourced_unfamiliar_api_in_real_6_4_help(tmp_path):
    """Installed identity: public stdio entry from site-packages -> real daemon -> real 6.4 help."""
    home, project = tmp_path / "home", tmp_path / "project"
    (home / "control-private").mkdir(parents=True)
    project.mkdir()
    env = _child_env(tmp_path, home=home, project=project, pythonpath=None)
    (tmp_path / "isolated-home").mkdir()

    site_packages = Path(site_packages_dir := next(p for p in sys.path if p.endswith("site-packages"))).resolve()
    identity = _module_identity(env)
    installed_module = Path(identity["file"]).resolve()
    assert str(installed_module).startswith(str(site_packages))
    assert installed_module.parent == site_packages / "comsol_mcp", \
        "installed copy must be the site-packages artifact, not the worktree package dir"
    assert installed_module != (REPO / "comsol_mcp" / "__init__.py").resolve()
    installed_sha = {name: _sha256(installed_module.parent / name) for name in _KEY_FILES}
    worktree_sha = {name: _sha256(REPO / "comsol_mcp" / name) for name in _KEY_FILES}
    artifact_matches_worktree = installed_sha == worktree_sha

    log_path = tmp_path / "control-daemon.log"
    assert not (home / "control-private" / "control.json").exists()
    receipt = {"schema": "comsol-mcp-t16-installed-docs/2", "identity_kind": "installed-site-packages",
               "python": sys.version, "receipt_file": str(tmp_path / "receipt.json"),
               # A pre-fix installed artifact re-run is a CHARACTERIZATION of
               # the old defect (not fix verification); if the venv artifact
               # were ever rebuilt to match the worktree this flips to fix
               # verification — and v3 contract E binds that flip to ALSO
               # running the full fixed-behavior invariant set below, so
               # 4-file byte identity alone can never book a fix PASS.
               "evidence_class": ("production-chain-fix-verification" if artifact_matches_worktree
                                  else "characterization-pre-fix-installed-artifact"),
               "installed_module": str(installed_module), "installed_sha256": installed_sha,
               "worktree_sha256": worktree_sha,
               "artifact_matches_worktree": artifact_matches_worktree}

    def _chain():
        checks = _run_docs_chain(tmp_path, env, receipt)
        if artifact_matches_worktree:
            # v3 contract E: booking production-chain-fix-verification must
            # pin the same post-49334c68 behavior the worktree/wheel
            # identities pin — byte equality is identity, not behavior.
            _assert_fixed_chain_invariants(checks)
            return
        # Characterization branch: chain invariants that must hold for ANY
        # artifact — the API text is only reachable from the real file,
        # hashes verify, and absence is honest.
        assert checks["index_status"] == "SUCCEEDED" and checks["indexed_count"] > 100
        assert checks["index_version"] == checks["search_version_used"]
        assert checks["modelutil_hit_present"] is True and checks["sha_matches_disk"] is True
        assert checks["phrase_present_via_get"] is True
        assert checks["wrapper_search_loadOnServer"] == [] and checks["wrapper_search_docMarker"] == []
        assert checks["sixthree"] == "UNAVAILABLE" and checks["bogus_ref"] == "NOT_FOUND" \
            and checks["outside_root_refusal"] is True
        # Honest multi-token semantics of this artifact are recorded, not
        # asserted as correct: the pre-fix installed artifact predates
        # commit 49334c68, and the receipt captures what it actually
        # answers for fabricated or cross-document queries.
        assert checks["marker_alias_version"] in ("6.4", "unknown")
    # The session helper owns the daemon from the moment the start returns:
    # receipt back-fill (daemon record, cmdline hashing) and the chain all sit
    # inside the try/finally that releases this run's own child.
    _run_task_owned_chain_session(tmp_path, env, home / "control-private", log_path, receipt, _chain)
    assert receipt.get("receipt_persisted") is True, \
        "a booked session PASS must have actually persisted its receipt (v3 contract D)"
    assert receipt["status"] == "PASS", (receipt["chain_failure"], receipt["cleanup"])


@pytest.mark.skipif(os.environ.get("COMSOL_MCP_RUN_INSTALLED_DOCS_T16") != "1",
                    reason="opt-in: starts one task-owned control daemon on loopback")
@pytest.mark.skipif(not API_SUBTREE.is_dir(), reason="requires the local official COMSOL 6.4 help tree")
def test_worktree_source_chain_closes_marker_and_scoring_gaps(tmp_path):
    """Source identity: same production chain from the worktree source pins
    commit 49334c68 behavior: COMSOL64 install marker -> version 6.4, and
    every published multi-token hit must verify its tokens via docs.get."""
    home, project = tmp_path / "home", tmp_path / "project"
    (home / "control-private").mkdir(parents=True)
    project.mkdir()
    env = _child_env(tmp_path, home=home, project=project, pythonpath=str(REPO))
    (tmp_path / "isolated-home").mkdir()

    identity = _module_identity(env)
    source_module = Path(identity["file"]).resolve()
    assert source_module == (REPO / "comsol_mcp" / "__init__.py").resolve()

    log_path = tmp_path / "control-daemon.log"
    receipt = {"schema": "comsol-mcp-t16-installed-docs/2", "identity_kind": "worktree-source",
               "python": sys.version, "receipt_file": str(tmp_path / "receipt.json"),
               "evidence_class": "production-chain-fix-verification",
               "source_module": str(source_module),
               "worktree_sha256": {name: _sha256(REPO / "comsol_mcp" / name) for name in _KEY_FILES}}

    def _chain():
        checks = _run_docs_chain(tmp_path, env, receipt, runtime_alias="COMSOL64")
        _assert_fixed_chain_invariants(checks)
    _run_task_owned_chain_session(tmp_path, env, home / "control-private", log_path, receipt, _chain)
    assert receipt.get("receipt_persisted") is True, \
        "a booked session PASS must have actually persisted its receipt (v3 contract D)"
    assert receipt["status"] == "PASS", (receipt["chain_failure"], receipt["cleanup"])


def _assert_fixed_chain_invariants(checks: dict) -> None:
    """What the post-49334c68 chain (worktree source or candidate wheel
    bytes) must answer; the pre-fix installed artifact is deliberately NOT
    asserted with this set."""
    assert checks["index_status"] == "SUCCEEDED" and checks["indexed_count"] > 100
    assert checks["index_version"] == checks["search_version_used"] == "6.4"
    assert checks["marker_alias_version"] == "6.4", "install-directory version marker must resolve to 6.4"
    assert checks["modelutil_hit_present"] is True and checks["sha_matches_disk"] is True
    assert checks["phrase_present_via_get"] is True
    # Non-empty first, then verified: all_verify over zero hits is vacuous
    # and must never be claimed as "hits verified".
    assert checks["multi_token_hit_count"] >= 1, \
        "AND-query must publish at least one hit before claiming verification"
    assert checks["multi_token_unverified_hits"] == 0, \
        "every published multi-token hit must be individually verified"
    assert checks["multi_token_hits_all_verify"] is True, \
        "published hits must each contain every query token (AND semantics)"
    assert checks["cross_doc_query"] == "NOT_FOUND"
    assert checks["fabricated"] == "NOT_FOUND" and checks["sixthree"] == "UNAVAILABLE" \
        and checks["bogus_ref"] == "NOT_FOUND" and checks["outside_root_refusal"] is True
    assert checks["sha_matches_disk"] is True and checks["phrase_present_via_get"] is True
    assert checks["docmarker_hit_present"] is True and checks["docmarker_signature_via_get"] is True


def _run_docs_chain(root: Path, env: dict, receipt: dict, *, runtime_alias: str = "6.4",
                    python: str | None = None) -> dict:
    """One MCP stdio session -> task-owned daemon -> installed docs index."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    checks: dict = {}
    # v4: transport/attempt/confirmation facts are recorded only at their
    # real milestones inside the stdio body — the presence of ``checks`` in
    # the receipt proves nothing about loopback usage by itself.
    loopback = {"transport_entered": False, "attempted": False, "confirmed": False}

    async def _stdio_test():
        params = StdioServerParameters(command=python or sys.executable,
                                       args=["-m", "comsol_mcp.mcp_server"],
                                       cwd=str(root), env=env)
        async with stdio_client(params) as (read, write):
            loopback["transport_entered"] = True
            async with ClientSession(read, write) as session:
                assert (await session.initialize()) is not None
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"registry_call", "registry_describe", "registry_search"}.issubset(names)

                described = await session.call_tool("registry_describe",
                                                    arguments={"operation_id": "docs.search"})
                assert described.isError is False
                props = described.structuredContent["data"]["input_schema"]["properties"]
                assert "product" in props and "node_type" not in props

                # v4: the first daemon-directed registry_call — the gateway
                # proxies registry_call to the task-owned control daemon, so
                # issuing this call is an attempt at a loopback RPC; a
                # non-error daemon answer is observed proof it completed.
                loopback["attempted"] = True
                created = await _call(session, "project.create",
                                      {"label": "t16-installed-docs", "workspace": "t16-installed-docs",
                                       "policy": {"permissions": ["inspect", "project_write", "compute"]},
                                       "idempotency_key": "t16-id-create"},
                                      {"idempotency_key": "t16-id-create"})
                if created.isError is False:
                    loopback["confirmed"] = True
                assert created.isError is False, created.structuredContent
                project_id = created.structuredContent["data"]["project"]["project_id"]
                checks["project_id"] = project_id

                indexed = await _call(session, "docs.index",
                                      {"project_id": project_id, "idempotency_key": "t16-id-index",
                                       "runtime_id": runtime_alias, "sources": [str(API_SUBTREE)]},
                                      {"project_id": project_id, "idempotency_key": "t16-id-index"})
                assert indexed.isError is False, indexed.structuredContent
                index_data = indexed.structuredContent["data"]
                checks["index_status"] = index_data["status"]
                checks["index_version"] = index_data["version"]
                checks["indexed_count"] = index_data["indexed_count"]

                # Second alias probe: undotted install-dir alias must stay its
                # own version dimension; record what this artifact infers.
                marker = await _call(session, "docs.index",
                                     {"project_id": project_id, "idempotency_key": "t16-id-marker",
                                      "runtime_id": "COMSOL64", "sources": [str(API_SUBTREE / "util")]},
                                     {"project_id": project_id, "idempotency_key": "t16-id-marker"})
                assert marker.isError is False, marker.structuredContent
                checks["marker_alias_version"] = marker.structuredContent["data"]["version"]

                search_version = index_data["version"]
                checks["search_version_used"] = search_version
                found = await _call(session, "docs.search",
                                    {"project_id": project_id, "query": "loadOnServer",
                                     "version": search_version},
                                    {"project_id": project_id})
                assert found.isError is False
                results = found.structuredContent["data"]["results"]
                assert found.structuredContent["data"]["status"] == "SUCCEEDED"
                modelutil = next((r for r in results if r["source"].endswith("util/ModelUtil.html")), None)
                checks["modelutil_hit_present"] = modelutil is not None
                assert modelutil is not None, "official ModelUtil javadoc must be a hit for loadOnServer"
                disk_sha = _sha256(Path(modelutil["source"]))
                checks["sha_matches_disk"] = disk_sha == modelutil["source_sha256"]
                checks["source_path"] = modelutil["source"]

                got = await _call(session, "docs.get",
                                  {"project_id": project_id, "document_ref": modelutil["document_ref"],
                                   "section": "loadOnServer"},
                                  {"project_id": project_id})
                fragment = got.structuredContent["data"]
                checks["phrase_present_via_get"] = (fragment["status"] == "SUCCEEDED"
                                                    and "Load a model from a server file" in fragment["content"])

                # AND-token quality: every published multi-token hit must
                # contain every token, provable per hit via docs.get.
                multi = await _call(session, "docs.search",
                                    {"project_id": project_id, "query": "loadOnServer server file",
                                     "version": search_version}, {"project_id": project_id})
                multi_hits = multi.structuredContent["data"]["results"]
                all_verify = True
                for hit in multi_hits[:5]:
                    for token in ("loadOnServer", "server", "file"):
                        probe = await _call(session, "docs.get",
                                            {"project_id": project_id, "document_ref": hit["document_ref"],
                                             "section": token}, {"project_id": project_id})
                        if probe.structuredContent["data"]["status"] != "SUCCEEDED":
                            all_verify = False
                            break
                checks["multi_token_hits_all_verify"] = all_verify
                checks["multi_token_hit_count"] = len(multi_hits)
                # Verification scope is explicit: only the first
                # min(5, count) published hits are individually verified;
                # all_verify over count==0 is vacuous, so fixed-chain
                # callers must additionally assert count>=1 and unverified==0
                # before claiming every published hit was verified.
                checks["multi_token_hits_verified_count"] = min(5, len(multi_hits))
                checks["multi_token_unverified_hits"] = max(0, len(multi_hits) - 5)

                # Tokens that never co-occur on one page must not publish it.
                cross = await _call(session, "docs.search",
                                    {"project_id": project_id,
                                     "query": "loadOnServer chatbot window", "version": search_version},
                                    {"project_id": project_id})
                checks["cross_doc_query"] = cross.structuredContent["data"]["status"]

                # Unfamiliar-but-legal: no reviewed wrapper exists for these receivers.
                for term, key in (("loadOnServer", "wrapper_search_loadOnServer"),
                                  ("docMarker", "wrapper_search_docMarker")):
                    searched = await session.call_tool("registry_search", arguments={"query": term})
                    checks[key] = searched.structuredContent["data"]["operations"]

                # Ranking note (documented limitation): the bare token
                # 'docMarker' appears on every ModelEntity-subclass page via
                # inherited-method tables, so tie ordering can page ModelEntity
                # itself out; the two-token query ranks it top and also proves
                # AND coverage.
                docmarker = await _call(session, "docs.search",
                                        {"project_id": project_id, "query": "ModelEntity docMarker",
                                         "version": search_version, "limit": 20}, {"project_id": project_id})
                dm_hits = docmarker.structuredContent["data"]["results"]
                dm = [r for r in dm_hits if r["source"].endswith("model/ModelEntity.html")]
                checks["docmarker_hit_present"] = bool(dm)
                fragments = []
                for hit in dm[:5]:
                    dm_get = await _call(session, "docs.get",
                                         {"project_id": project_id, "document_ref": hit["document_ref"],
                                          "section": "docMarker"}, {"project_id": project_id})
                    dm_data = dm_get.structuredContent["data"]
                    if dm_data["status"] == "SUCCEEDED":
                        fragments.append(dm_data["content"])
                joined = "\n".join(fragments)
                # The javadoc HTML carries U+200B zero-width markers between
                # the return type and the method name; match tolerantly.
                checks["docmarker_signature_via_get"] = (
                    re.search(r"java\.lang\.String[\s\u200b]+docMarker", joined) is not None
                    and "topic key" in joined)

                # Honest absence over real dimensions.
                fabricated = await _call(session, "docs.search",
                                         {"project_id": project_id, "query": "phantom zzz term",
                                          "version": search_version}, {"project_id": project_id})
                checks["fabricated"] = fabricated.structuredContent["data"]["status"]
                sixthree = await _call(session, "docs.search",
                                       {"project_id": project_id, "query": "loadOnServer",
                                        "version": "6.3"}, {"project_id": project_id})
                checks["sixthree"] = sixthree.structuredContent["data"]["status"]
                bogus = await _call(session, "docs.get",
                                    {"project_id": project_id, "document_ref": "docs:6.4:deadbeef:0"},
                                    {"project_id": project_id})
                checks["bogus_ref"] = bogus.structuredContent["data"]["status"]

                # A source outside the approved help roots must be refused.
                outside = root / "outside-corpus"
                outside.mkdir()
                (outside / "fake.html").write_text("<html>fake</html>", encoding="utf-8")
                refused = await _call(session, "docs.index",
                                      {"project_id": project_id, "idempotency_key": "t16-id-refuse",
                                       "runtime_id": "6.4", "sources": [str(outside)]},
                                      {"project_id": project_id, "idempotency_key": "t16-id-refuse"})
                checks["outside_root_refusal"] = ("PERMISSION_DENIED" in
                                                  json.dumps(refused.structuredContent)
                                                  and refused.isError is True)

    try:
        asyncio.run(_stdio_test())
    finally:
        receipt["checks"] = checks
        # v4: honest loopback bookkeeping — these flags carry the truth even
        # when the transport failed before any RPC (``checks`` stays empty).
        receipt["loopback_transport_entered"] = loopback["transport_entered"]
        receipt["loopback_rpc_attempted"] = loopback["attempted"]
        receipt["loopback_rpc_confirmed"] = loopback["confirmed"]
    return checks


def _wheel_member_sha256(wheel: Path, name: str) -> str:
    with zipfile.ZipFile(wheel) as archive:
        return hashlib.sha256(archive.read(f"comsol_mcp/{name}")).hexdigest()


@pytest.mark.skipif(os.environ.get("COMSOL_MCP_RUN_INSTALLED_DOCS_T16") != "1",
                    reason="opt-in: starts one task-owned control daemon on loopback")
@pytest.mark.skipif(not all(os.environ.get(k) for k in (
                            "COMSOL_T16_WHEEL_PATH", "COMSOL_T16_WHEEL_SHA256", "COMSOL_T16_WHEEL_ROOT",
                            "COMSOL_T16_WHEEL_PYTHON", "COMSOL_T16_WHEEL_WHEELHOUSE")),
                    reason="opt-in wheel run: pinned wheel + sha256 + brand-new root + base python + offline wheelhouse")
@pytest.mark.skipif(not API_SUBTREE.is_dir(), reason="requires the local official COMSOL 6.4 help tree")
def test_pinned_candidate_wheel_installed_offline_in_new_venv_runs_the_fixed_chain(tmp_path):
    """Wheel identity: the pinned T42 candidate bytes, installed *offline* into a
    brand-new self-contained venv (base interpreter + pinned wheelhouse are both
    env-pinned; no existing venv touched, reused, or chained — a chained venv
    would silently resolve deps against the base install, not the uv venv), then
    the same production stdio -> task-owned daemon -> docs chain must answer
    with the post-49334c68 behavior.  Candidate bytes are distinguished from the
    old pre-fix installed artifact by hashes, not by the shared 0.1.16 version.
    """
    wheel = Path(os.environ["COMSOL_T16_WHEEL_PATH"]).resolve()
    expected_sha = os.environ["COMSOL_T16_WHEEL_SHA256"]
    out_root = Path(os.environ["COMSOL_T16_WHEEL_ROOT"]).resolve()
    base_python = Path(os.environ["COMSOL_T16_WHEEL_PYTHON"]).resolve()
    wheelhouse = Path(os.environ["COMSOL_T16_WHEEL_WHEELHOUSE"]).resolve()
    assert _sha256(wheel) == expected_sha, "candidate wheel bytes must equal the pinned identity"
    assert base_python.is_file() and wheelhouse.is_dir()
    # Fresh-isolation guards: the output root must not exist, and the venv we
    # are about to create must not exist either.  Existing venvs are never
    # referenced for writing.
    assert not out_root.exists(), "wheel run must use a brand-new output dir"
    out_root.mkdir(parents=True)
    venv_dir = out_root / "wheel-venv"
    wheel_python = str(venv_dir / "bin" / "python")

    install_root_env = {"PATH": "/usr/bin:/bin", "HOME": str(out_root / "isolated-home"),
                        "TMPDIR": str(out_root)}
    (out_root / "isolated-home").mkdir()
    install_transcript = {}
    steps = (
        ("venv_create", [str(base_python), "-m", "venv", str(venv_dir)]),
        ("pip_install_offline", [wheel_python, "-m", "pip", "install", "--no-index", "--no-cache-dir",
                                 "--find-links", str(wheelhouse), str(wheel)]),
    )
    for label, command in steps:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=240,
                              env=install_root_env, cwd=str(out_root))
        install_transcript[label] = {"command": command, "returncode": proc.returncode,
                                     "stdout_tail": proc.stdout[-1500:], "stderr_tail": proc.stderr[-1500:]}
        assert proc.returncode == 0, f"{label} failed rc={proc.returncode}: {proc.stderr[-400:]}"


    home, project = tmp_path / "home", tmp_path / "project"
    (home / "control-private").mkdir(parents=True)
    project.mkdir()
    (tmp_path / "isolated-home").mkdir()
    env = _child_env(tmp_path, home=home, project=project, pythonpath=None)

    identity = _module_identity(env, python=wheel_python)
    resolved_module = Path(identity["file"]).resolve()
    assert str(venv_dir.resolve()) in str(resolved_module), \
        "import must resolve to the brand-new venv site-packages, not any pre-existing venv"
    assert str(venv_dir.resolve()) in str(Path(identity["mcp_file"]).resolve()), \
        "the venv must be self-contained: dependency imports also resolve inside the new venv"
    resolved_sha = {name: _sha256(resolved_module.parent / name) for name in _KEY_FILES}
    wheel_sha = {name: _wheel_member_sha256(wheel, name) for name in _KEY_FILES}
    worktree_sha = {name: _sha256(REPO / "comsol_mcp" / name) for name in _KEY_FILES}
    assert resolved_sha == wheel_sha, "installed venv copy must be exactly the wheel bytes"
    assert wheel_sha == worktree_sha, \
        "candidate wheel must carry the worktree (post-49334c68) key-file bytes"

    old_artifact_dir = os.environ.get("COMSOL_T16_OLD_ARTIFACT_DIR")
    old_artifact_sha = None
    differs_from_old = None
    if old_artifact_dir:
        old_dir = Path(old_artifact_dir)
        old_artifact_sha = {name: _sha256(old_dir / name) for name in _KEY_FILES}
        # Read-only comparison against the old installed artifact; identity
        # is by bytes, never by the shared 0.1.16 version string.
        differs_from_old = wheel_sha != old_artifact_sha

    log_path = tmp_path / "control-daemon.log"
    receipt = {"schema": "comsol-mcp-t16-installed-docs/2", "identity_kind": "installed-wheel-candidate",
               "python": sys.version, "receipt_file": str(out_root / "receipt.json"),
               "evidence_class": "production-chain-fix-verification",
               "wheel": {"path": str(wheel), "sha256": expected_sha},
               "wheel_base_python": str(base_python), "wheelhouse": str(wheelhouse),
               "wheel_install": install_transcript,
               "wheel_python": wheel_python, "wheel_python_version": identity.get("python_version"),
               "mcp_module": identity.get("mcp_file"),
               "resolved_module": str(resolved_module),
               "resolved_sha256": resolved_sha, "wheel_member_sha256": wheel_sha,
               "worktree_sha256": worktree_sha,
               "candidate_matches_worktree": wheel_sha == worktree_sha,
               "old_artifact_dir": old_artifact_dir, "old_artifact_sha256": old_artifact_sha,
               "candidate_differs_from_old_installed_artifact": differs_from_old}

    def _chain():
        checks = _run_docs_chain(tmp_path, env, receipt, runtime_alias="COMSOL64", python=wheel_python)
        _assert_fixed_chain_invariants(checks)
    _run_task_owned_chain_session(tmp_path, env, home / "control-private", log_path, receipt, _chain,
                                  python=wheel_python)
    assert receipt.get("receipt_persisted") is True, \
        "a booked session PASS must have actually persisted its receipt (v3 contract D)"
    assert receipt["status"] == "PASS", (receipt["chain_failure"], receipt["cleanup"])

