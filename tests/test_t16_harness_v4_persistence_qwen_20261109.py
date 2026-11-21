"""T16 (Qwen, 2026-11-09): v4 counterexample pins for real-evidence persistence
and loopback honesty in the T16 harness.

Background (read-only, not re-executed here): the parent-agent probe
``docs/universal_mcp_execution/evidence/full/T16-harness-v3-parent-counterexamples-20261109.txt``
against the frozen f93e8df3… harness measured
``receipt_persisted_disk: false`` while memory said ``true``, and a chain that
was aborted *before* the stdio transport (coroutine closed before the event
loop ran) still persisted ``checks: {}`` and a network note claiming the chain
"authenticated to the task-owned control daemon".  Those probes stay as
historical counterexample evidence; this file states the opposite polarity —
the v4 contracts.  They are RED against f93e8df3… and GREEN only against the
v4 harness:

P  real persistence: whenever the harness claims ``receipt_persisted: true``,
   the on-disk primary JSON must EQUAL the in-memory receipt (the morning
   single-pass write froze a revision saying ``receipt_persisted: false``),
   a copy fault must be visible in the persisted primary (memory-only
   ``copy_error`` lies by omission), and a successful evidence-dir copy must
   be byte-identical to the final primary.
L  loopback honesty has three states — never_attempted / attempted_unverified
   / confirmed.  An empty ``checks`` dict written by a finally is NOT proof
   of an RPC: a run that never entered the stdio transport claims no loopback
   RPC, a run that entered the transport but saw no successful daemon-mediated
   answer claims an attempt but never an authenticated RPC, and only a full
   PASS verdict or an explicitly successful daemon-mediated call confirms one.
S  an unexpected non-OSError fault inside ``_cleanup_and_receipt`` can never
   leave a booked PASS: the session finally downgrades the record
   (cleanup_record_error + cleanup/status FAIL) AND re-persists it, keeping
   the primary file; a chain that never wrote any record still gets an honest
   FAIL persisted; a KeyboardInterrupt inside the chain never looks like a
   success.
E  the start-failure release chain must not assume exception constructors or
   3.11+ APIs: the fallback may not set ``exc.__cause__ = exc`` (a
   self-referential cycle) and may not call ``add_note`` (absent on Python
   3.10); a plain wrapper carrying ``__cause__`` works on every supported
   version and keeps both the start failure and the release provenance
   visible in ``str()``.

Safety: fully synthetic — fake children (synth.FakeChild/RaceChild), fast
clock, injected socket namespaces (no real connect, no bind, no free-port
probe), fake asyncio/mcp modules.  No real control daemon, listening socket,
loopback connection, COMSOL/Worker/SSH, docs chain, or historical
process/receipt/evidence is started, touched, or rewritten.
"""
from __future__ import annotations

import contextlib
import errno
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

try:  # tests/ is a package; both import forms appear in this repo
    import test_t16_docs_installed_qwen_20261109 as t16
    import test_t16_daemon_lifecycle_synthetic_qwen_20261109 as synth
except ImportError:  # pragma: no cover - depends on pytest import mode
    from tests import test_t16_docs_installed_qwen_20261109 as t16
    from tests import test_t16_daemon_lifecycle_synthetic_qwen_20261109 as synth


# ---------------------------------------------------------------------------
# shared synthetic scaffolding (no OS-level sockets or processes ever)
# ---------------------------------------------------------------------------

class ProbeSocketNS:
    """Injected socket namespace: connect_ex returns ``code`` (default refusal).

    Never a real socket: the harness probe only ever calls settimeout/
    connect_ex on this stand-in, so no loopback connect happens at all.
    """

    def __init__(self, *, code=errno.ECONNREFUSED):
        self.code = code
        self.calls = 0

    def socket(self, *a, **k):
        ns = self

        class _P:
            def settimeout(self, t):
                pass

            def connect_ex(self, addr):
                ns.calls += 1
                return ns.code

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _P()


class ExitedChild:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def _arm_session(monkeypatch, store, *, child_factory=None, receipt_dir=None):
    """Route the harness starter through fake children/files (never a real Popen)."""

    def fake_popen(command, **kwargs):
        child = (child_factory or synth.FakeChild)(command)
        store["child"] = child
        return child

    def fake_run(cmd, *a, **k):
        home = str(store.get("home", ""))
        return subprocess.CompletedProcess(
            cmd, 0, f"{sys.executable} -m comsol_mcp._control_daemon --home {home}", "")

    monkeypatch.setattr(t16, "time", synth.FastClock())
    monkeypatch.setattr(t16.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(t16.subprocess, "run", fake_run)
    monkeypatch.delenv("COMSOL_T16_RECEIPT_DIR", raising=False)
    if receipt_dir is not None:  # re-arm AFTER the delenv, like an operator would export
        monkeypatch.setenv("COMSOL_T16_RECEIPT_DIR", str(receipt_dir))


def _cleanup(tmp_path, receipt, child, *, socket_ns, port=54321):
    """Drive _cleanup_and_receipt with an injected probe namespace; return persisted JSON."""
    log_path = tmp_path / "daemon.log"
    log_path.write_text("synthetic log\n", encoding="utf-8")
    receipt.setdefault("receipt_file", str(tmp_path / "receipt.json"))
    receipt.setdefault("identity_kind", "synthetic")
    saved = t16.socket
    t16.socket = socket_ns
    try:
        t16._cleanup_and_receipt(child, {"port": port}, log_path, receipt)
    finally:
        t16.socket = saved
    return json.loads(Path(receipt["receipt_file"]).read_text(encoding="utf-8"))


def _base_receipt(**extra):
    receipt = {"schema": "synthetic-v4", "chain_status": "PASS",
               "evidence_class": "production-chain-fix-verification",
               "identity_kind": "synthetic"}
    receipt.update(extra)
    return receipt


# ---------------------------------------------------------------------------
# P — what lands on disk must equal what memory claims
# ---------------------------------------------------------------------------

def test_persisted_primary_equals_in_memory_receipt(tmp_path, monkeypatch):
    """RED (f93e8df3): the on-disk primary froze receipt_persisted=false while
    memory claimed true.  The persisted record must equal the final in-memory
    receipt exactly whenever persistence is claimed."""
    monkeypatch.delenv("COMSOL_T16_RECEIPT_DIR", raising=False)
    receipt = _base_receipt()
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    assert receipt.get("receipt_persisted") is True
    assert persisted["receipt_persisted"] is True, (
        "the receipt file itself must not contradict the harness memory")
    assert persisted == receipt, (
        "disk and memory must agree field-for-field once persistence is claimed")


def test_evidence_dir_copy_equals_final_primary_bytes(tmp_path, monkeypatch):
    """A successful evidence copy must be byte-identical to the final primary,
    not to a stale pre-persistence revision."""
    evidence_dir = tmp_path / "evidence-dir"
    evidence_dir.mkdir()
    monkeypatch.setenv("COMSOL_T16_RECEIPT_DIR", str(evidence_dir))
    receipt = _base_receipt()
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    assert receipt.get("copy_error") is None
    assert persisted["receipt_persisted"] is True
    copy_path = evidence_dir / "T16-installed-docs-qwen-20261109-v2-receipt-synthetic.json"
    primary = Path(receipt["receipt_file"]).read_bytes()
    assert copy_path.is_file()
    assert copy_path.read_bytes() == primary, (
        "the copied receipt must carry the same final persistence fields as the primary")


def test_copy_fault_reaches_the_persisted_primary(tmp_path, monkeypatch):
    """RED (f93e8df3): copy_error only ever lived in memory — the saved
    primary (and its copy) silently omitted the persistence fault."""
    copy_blocker = tmp_path / "copy-blocker"
    copy_blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("COMSOL_T16_RECEIPT_DIR", str(copy_blocker))
    receipt = _base_receipt()
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    assert receipt.get("receipt_persisted") is True
    assert receipt.get("copy_error"), "copy failure must be reported (v3 contract D)"
    assert persisted.get("copy_error"), (
        "the copy fault must be part of the persisted record, not memory-only")
    assert persisted["status"] == "PASS", "a copy fault must not flip the link/cleanup verdict"


# ---------------------------------------------------------------------------
# L — loopback usage: never_attempted / attempted_unverified / confirmed
# ---------------------------------------------------------------------------

class AbortingAsyncio:
    """Stands in for asyncio: closes the coroutine and fails BEFORE the event
    loop could ever run the stdio body — the parent probe's exact shape."""

    @staticmethod
    def run(coro):
        coro.close()
        raise RuntimeError("synthetic: transport aborted before the event loop ran")


def _install_fake_mcp(monkeypatch, responder):
    """Fake mcp transport so _run_docs_chain exercises real bookkeeping with
    zero sockets, zero subprocesses: every stream is a fake token."""
    mcp_mod = types.ModuleType("mcp")

    class StdioServerParameters:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ClientSession:
        def __init__(self, read, write):
            self.read, self.write = read, write

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            return {"ok": True}

        async def list_tools(self):
            return types.SimpleNamespace(
                tools=[types.SimpleNamespace(name=n) for n in
                       ("registry_call", "registry_describe", "registry_search")])

        async def call_tool(self, name, arguments=None):
            return responder(name, arguments)

    mcp_mod.ClientSession = ClientSession
    mcp_mod.StdioServerParameters = StdioServerParameters
    client_pkg = types.ModuleType("mcp.client")
    stdio_mod = types.ModuleType("mcp.client.stdio")

    @contextlib.asynccontextmanager
    async def stdio_client(params):
        yield "fake-read-stream", "fake-write-stream"

    stdio_mod.stdio_client = stdio_client
    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.client", client_pkg)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_mod)


Res = types.SimpleNamespace


def _run_chain_and_cleanup(tmp_path, monkeypatch, *, abort_asyncio=False, responder=None):
    root, env, _ = synth._env_and_home(tmp_path)
    receipt = {"schema": "synthetic-v4", "identity_kind": "synthetic",
               "receipt_file": str(tmp_path / "receipt.json"),
               "evidence_class": "production-chain-fix-verification"}
    if abort_asyncio:
        monkeypatch.setattr(t16, "asyncio", AbortingAsyncio)
    else:
        _install_fake_mcp(monkeypatch, responder)
    with pytest.raises(RuntimeError):
        t16._run_chain_recorded(receipt, lambda: t16._run_docs_chain(root, env, receipt))
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    return receipt, persisted


def test_transport_never_entered_never_claims_loopback(tmp_path, monkeypatch):
    """RED (f93e8df3): coroutine closed before the loop ran still wrote
    checks={} and the persisted note claimed an authenticated daemon RPC.
    A never-attempted transport must claim NOTHING."""
    receipt, persisted = _run_chain_and_cleanup(tmp_path, monkeypatch, abort_asyncio=True)
    assert persisted["checks"] == {}, "parent counterexample shape reproduced"
    obs = persisted["network_observations"]
    assert obs["loopback_rpc_used"] is False, (
        "an empty checks dict is not evidence of an RPC (v4)")
    assert obs.get("loopback_rpc_state") == "never_attempted"
    assert obs["loopback_rpc_attempted"] is False
    assert obs["loopback_rpc_confirmed"] is False
    assert "authenticated" not in obs["note"], (
        "a run that never entered the transport may never describe authenticated RPC")


def describe_ok(name, arguments):
    if name == "registry_describe":
        return Res(isError=False,
                   structuredContent={"data": {"input_schema":
                                              {"properties": {"product": {}}}}})
    return None


def responder_attempt_only(name, arguments):
    result = describe_ok(name, arguments)
    if result is not None:
        return result
    raise RuntimeError("synthetic: daemon-directed call aborted mid-flight")


def responder_confirmed(name, arguments):
    result = describe_ok(name, arguments)
    if result is not None:
        return result
    if name == "registry_call" and (arguments or {}).get("operation_id") == "project.create":
        return Res(isError=False, structuredContent={
            "data": {"project": {"project_id": "synthetic-project"}}})
    raise RuntimeError("synthetic break after the first confirmed daemon answer")


def test_transport_entered_without_daemon_answer_is_attempted_unverified(tmp_path, monkeypatch):
    """RED (f93e8df3): entering stdio only (no successful daemon-mediated
    response) still claimed an authenticated RPC.  Now: an attempt is reported,
    completion is NOT claimed, and the word 'authenticated' never appears."""
    receipt, persisted = _run_chain_and_cleanup(
        tmp_path, monkeypatch, responder=responder_attempt_only)
    obs = persisted["network_observations"]
    assert obs["loopback_rpc_used"] is False
    assert obs.get("loopback_rpc_state") == "attempted_unverified"
    assert obs["loopback_rpc_attempted"] is True
    assert obs["loopback_rpc_confirmed"] is False
    assert "authenticated" not in obs["note"], (
        "creating a client without a proven daemon answer may not claim an authenticated RPC")
    assert persisted["chain_status"] == "FAIL" and persisted["status"] == "FAIL"


def test_confirmed_daemon_answer_claims_loopback(tmp_path, monkeypatch):
    """Positive control: a successful daemon-mediated project.create confirms
    the loopback RPC even when the chain later breaks."""
    receipt, persisted = _run_chain_and_cleanup(
        tmp_path, monkeypatch, responder=responder_confirmed)
    obs = persisted["network_observations"]
    assert obs["loopback_rpc_confirmed"] is True
    assert obs.get("loopback_rpc_state") == "confirmed"
    assert obs["loopback_rpc_used"] is True
    assert "authenticated" in obs["note"]
    assert persisted["checks"].get("project_id") == "synthetic-project"
    # a confirmed RPC still cannot book an overall PASS once the chain broke
    assert persisted["chain_status"] == "FAIL" and persisted["status"] == "FAIL"


# ---------------------------------------------------------------------------
# S — cleanup faults never leave a booked PASS
# ---------------------------------------------------------------------------

def _session(tmp_path, monkeypatch, run_chain, *, store=None, receipt=None, receipt_dir=None):
    root, env, control_home = synth._env_and_home(tmp_path)
    store = store if store is not None else {}
    store.setdefault("home", control_home)
    _arm_session(monkeypatch, store, receipt_dir=receipt_dir)
    synth._write_endpoint(control_home, port=54321)
    receipt = receipt if receipt is not None else _base_receipt(
        receipt_file=str(tmp_path / "receipt.json"))
    saved = t16.socket
    t16.socket = ProbeSocketNS()
    try:
        t16._run_task_owned_chain_session(root, env, control_home,
                                          root / "daemon.log", receipt, run_chain)
    finally:
        t16.socket = saved
    return root, receipt, store


def test_unexpected_cleanup_fault_after_write_cannot_book_pass(tmp_path, monkeypatch):
    """RED (f93e8df3): a non-OSError fault in the copy block escaped cleanup,
    was swallowed by the session finally, and BOTH memory and disk kept a
    status: PASS booked from a half-built record."""
    evidence_dir = tmp_path / "evidence-dir"
    evidence_dir.mkdir()
    calls = {"n": 0}
    real_copy = shutil.copy

    def flaky_copy(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected non-OSError cleanup fault")
        return real_copy(src, dst)

    monkeypatch.setattr(t16.shutil, "copy", flaky_copy)
    store: dict = {}
    _, receipt, store = _session(tmp_path, monkeypatch, lambda: {"ok": True}, store=store,
                                 receipt_dir=evidence_dir)
    assert receipt.get("cleanup_record_error"), "the fault must be reported (v3 contract D)"
    assert receipt["status"] == "FAIL", (
        "a cleanup record whose builder itself faulted may never book PASS")
    assert receipt["cleanup_status"] == "FAIL"
    assert receipt.get("fix_verified") is False
    path = Path(receipt["receipt_file"])
    assert path.is_file(), "the primary receipt file must be kept, not lost"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["status"] == "FAIL", "disk must not keep a PASS booked by the faulted cleanup"
    assert "cleanup_record_error" in persisted, "the fault belongs in the persisted record too"
    assert persisted["receipt_persisted"] is True
    # the repaired copy path recovered (second injected call delegates for real)
    copy_path = evidence_dir / "T16-installed-docs-qwen-20261109-v2-receipt-synthetic.json"
    assert copy_path.is_file() and copy_path.read_bytes() == path.read_bytes()
    child = store["child"]
    assert child.terminate_calls + child.kill_calls >= 1
    assert child.poll() is not None


def test_unexpected_cleanup_fault_before_write_still_persists_honest_fail(tmp_path, monkeypatch):
    """RED (f93e8df3): cleanup raising before any write left the receipt with
    NO status key — a PASS chain plus a broken cleanup produced no verdict at
    all.  v4 downgrades and persists an honest FAIL record."""
    monkeypatch.setattr(t16, "_release_task_owned_child",
                        lambda process: (_ for _ in ()).throw(
                            RuntimeError("injected fault at the top of cleanup")))
    receipt = _base_receipt(receipt_file=str(tmp_path / "receipt.json"))
    _session(tmp_path, monkeypatch, lambda: {"ok": True}, receipt=receipt)
    assert receipt.get("cleanup_record_error")
    assert receipt.get("status") == "FAIL", (
        "the session must leave an explicit FAIL, not a verdict-free receipt")
    path = Path(receipt["receipt_file"])
    assert path.is_file(), "a faulted cleanup must still persist an honest record"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["status"] == "FAIL"
    assert persisted["chain_status"] == "PASS", "the link verdict itself survives untouched"
    assert "cleanup_record_error" in persisted


def test_keyboard_interrupt_in_chain_never_books_success(tmp_path, monkeypatch):
    """An interrupted chain stays FAIL end to end; the interrupt is reported,
    never disguised as a booked success."""
    receipt = _base_receipt(receipt_file=str(tmp_path / "receipt.json"))

    def interrupted():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _session(tmp_path, monkeypatch, interrupted, receipt=receipt)
    persisted = json.loads(Path(receipt["receipt_file"]).read_text(encoding="utf-8"))
    assert persisted["chain_status"] == "FAIL"
    assert persisted["status"] == "FAIL" and persisted["fix_verified"] is False
    assert "KeyboardInterrupt" in persisted["chain_failure"]


# ---------------------------------------------------------------------------
# E — start-failure release chaining survives exotic exception types
# ---------------------------------------------------------------------------

class TwoArgStartError(RuntimeError):
    """Start failure whose constructor cannot take the harness's single
    message — forces the fallback path in the starter."""

    def __init__(self, marker, detail):
        super().__init__(marker)
        self.marker, self.detail = marker, detail


class NoNoteStartError(TwoArgStartError):
    """Additionally simulates Python < 3.11 where BaseException.add_note does
    not exist: any fallback that calls add_note explodes (AttributeError) and
    loses the original start failure entirely."""

    def add_note(self, note):
        raise AttributeError("'NoNoteStartError' object has no attribute 'add_note'")


def _arm_unprovable_release_start(monkeypatch, root, error_cls):
    def fake_popen(command, **kwargs):
        # child provably stays alive: release can never be proven
        child = synth.RaceChild(poll_sequence=[None],
                                terminate_error=PermissionError(1, "Operation not permitted"))
        store["child"] = child
        return child

    store: dict = {}

    def failing_wait(control_home, process, timeout):
        raise error_cls("START-FAIL-MARKER", "detail")

    monkeypatch.setattr(t16.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(t16, "_wait_for_task_owned_endpoint", failing_wait)
    return store


def test_unconstructable_start_error_fallback_never_self_causes(tmp_path, monkeypatch):
    """RED (f93e8df3): the fallback did ``chained = exc`` then
    ``exc.__cause__ = exc`` — a self-referential cycle — and the release
    provenance only landed in an add_note, not in str()."""
    root, env, control_home = synth._env_and_home(tmp_path)
    store = _arm_unprovable_release_start(monkeypatch, root, TwoArgStartError)

    with pytest.raises(BaseException) as excinfo:
        t16._start_task_owned_daemon(root, env, control_home, root / "daemon.log")

    raised = excinfo.value
    assert raised.__cause__ is not raised, (
        "an exception may never be its own cause; the cycle hides the original")
    assert isinstance(raised.__cause__, TwoArgStartError), "original start failure stays chained"
    assert "START-FAIL-MARKER" in str(raised), "the original failure must be visible in str()"
    assert "release NOT PROVEN" in str(raised), "the unprovable stop must be visible in str()"
    assert "terminate failed" in str(raised)
    assert store["child"].poll() is None, "the live child must not be claimed as stopped"


def test_add_note_absent_path_keeps_original_failure_visible(tmp_path, monkeypatch):
    """RED (f93e8df3, Python 3.10 shape): the fallback called add_note, which
    does not exist pre-3.11; the resulting AttributeError replaced the whole
    start failure.  v4 uses a plain wrapper, so the original stays chained and
    visible without any 3.11-only API."""
    root, env, control_home = synth._env_and_home(tmp_path)
    _arm_unprovable_release_start(monkeypatch, root, NoNoteStartError)

    with pytest.raises(BaseException) as excinfo:
        t16._start_task_owned_daemon(root, env, control_home, root / "daemon.log")

    raised = excinfo.value
    assert type(raised) is not AttributeError, (
        "a missing add_note must not replace the original start failure")
    assert isinstance(raised.__cause__, NoNoteStartError)
    assert "START-FAIL-MARKER" in str(raised)
    assert "release NOT PROVEN" in str(raised)
