"""T16 (Qwen, 2026-11-09): v3 fidelity CONTRACTS A–F for the T16 docs-chain harness.

Independent-review follow-up.  The reviewer probe
(``T16-harness-v2-fidelity-review-qwen-20261109.md`` appendix, sha256 66bfc8d7…)
PASSED against harness 8a51cd41… — a probe PASS there records that each gap
EXISTS, it is not a fix claim, and those probes are kept as historical
characterization evidence (not inverted, not deleted).  This file states the
opposite polarity: the NEW v3 contracts.  They are RED against 8a51cd41… and
must go GREEN only against a harness that actually closes gaps A–F:

A  a failed daemon start whose release ALSO cannot be proven must make both
   the original start exception and the unprovable ``release_error`` visible
   to the caller; a proven release keeps the plain original exception.
B  a port probe is proof of unreachability only when the OS says the port is
   refused; a probe exception or a non-refused status never books
   ``control_port_unreachable`` (a required condition for cleanup PASS).
C  ``loopback_rpc_used`` follows call arrival — the chain transport actually
   entered in this run — and a NOT_RECORDED/never-executed chain never claims
   an authenticated RPC.
D  a receipt write/copy failure is guarded: the link verdict and the original
   chain exception survive, the secondary persistence failure is reported on
   the in-memory receipt, and an unpersisted receipt is never claimed saved.
E  the installed-site-packages branch may flip evidence_class to
   fix-verification only together with the full ``_assert_fixed_chain_invariants``
   set; 4-file byte equality alone must not book a behavioral fix verification.
F  an unset or unknown ``evidence_class`` fails CLOSED: only an explicit
   fix-verification class can book ``fix_verified = True``.

Safety: everything here is bounded synthetic — fake children (reusing
FakeChild/RaceChild/FastClock from the synthetic suite), injected socket
namespaces, and the suite's own free-proport connect_ex checks.  No real
control daemon, listening socket, COMSOL/Worker/SSH, docs chain, or historical
process is started or touched; the repo is not mutated.
"""
from __future__ import annotations

import errno
import inspect
import json
import re
import subprocess
import sys
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


class ProbeSocketNS:
    """Injected socket namespace: connect_ex returns ``code`` or raises ``error``."""

    def __init__(self, *, code=errno.ECONNREFUSED, error=None):
        self.code = code
        self.error = error
        self.calls = 0

    def socket(self, *a, **k):
        ns = self

        class _P:
            def settimeout(self, t):
                pass

            def connect_ex(self, addr):
                ns.calls += 1
                if ns.error is not None:
                    raise ns.error
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


def _arm(monkeypatch, store, *, child_factory=None):
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


def _cleanup(tmp_path, receipt, child, *, port=54321, socket_ns=None):
    """Drive _cleanup_and_receipt with an injected probe namespace; return persisted JSON."""
    log_path = tmp_path / "daemon.log"
    log_path.write_text("synthetic log\n", encoding="utf-8")
    receipt.setdefault("receipt_file", str(tmp_path / "receipt.json"))
    receipt.setdefault("identity_kind", "synthetic")
    saved = t16.socket
    if socket_ns is not None:
        t16.socket = socket_ns
    try:
        t16._cleanup_and_receipt(child, {"port": port}, log_path, receipt)
    finally:
        t16.socket = saved
    return json.loads(Path(receipt["receipt_file"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# A — starter release provenance is visible to the caller
# ---------------------------------------------------------------------------

def test_contract_A_unprovable_release_on_start_failure_is_visible_to_caller(
        tmp_path, monkeypatch):
    """Release failure on the start-failure path must reach the caller chained."""
    root, env, control_home = synth._env_and_home(tmp_path)
    store: dict = {"home": control_home}
    live = synth.RaceChild(poll_sequence=[None],
                           terminate_error=PermissionError(1, "Operation not permitted"))
    _arm(monkeypatch, store, child_factory=lambda cmd: live)

    with pytest.raises(AssertionError) as excinfo:
        t16._start_task_owned_daemon(root, env, control_home, root / "daemon.log")

    msg = str(excinfo.value)
    # the original link/start failure stays visible, and so does the fact that
    # releasing the child could NOT be proven — both in one visible chain:
    assert "did not appear before the deadline" in msg
    assert "terminate failed" in msg
    assert "release NOT PROVEN" in msg
    assert excinfo.value.__cause__ is not None, "original exception must stay chained"
    # the child is genuinely still alive: nothing may claim the stop succeeded
    assert live.poll() is None


def test_contract_A_proven_release_on_start_failure_keeps_plain_original(tmp_path, monkeypatch):
    """Positive control: a provably released child keeps the bare original error."""
    root, env, control_home = synth._env_and_home(tmp_path)
    store: dict = {"home": control_home}
    _arm(monkeypatch, store)

    with pytest.raises(AssertionError, match="did not appear before the deadline") as excinfo:
        t16._start_task_owned_daemon(root, env, control_home, root / "daemon.log")

    assert "release NOT PROVEN" not in str(excinfo.value)
    child = store["child"]
    assert child.terminate_calls + child.kill_calls >= 1
    assert child.poll() is not None


# ---------------------------------------------------------------------------
# B — port probe: only an explicit refusal proves unreachability
# ---------------------------------------------------------------------------

def test_contract_B_probe_error_is_never_unreachable_proof(tmp_path):
    """connect_ex raising (not ECONNREFUSED) must FAIL cleanup, not prove it."""
    ns = ProbeSocketNS(error=OSError(13, "Permission denied"))
    receipt = {"schema": "synthetic", "chain_status": "PASS",
               "evidence_class": "production-chain-fix-verification"}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ns)
    assert ns.calls >= 1, "the probe was attempted but observed nothing conclusive"
    assert persisted["cleanup"]["control_port_unreachable"] is False
    assert persisted["cleanup"]["port_probe_error"], "probe fault must be reported"
    assert persisted["cleanup_status"] == "FAIL", "an unconfirmed port cannot PASS cleanup"
    assert persisted["status"] == "FAIL" and persisted["fix_verified"] is False


def test_contract_B_nonrefused_status_is_not_unreachable_proof(tmp_path):
    """A nonzero connect_ex status that is not ECONNREFUSED proves nothing."""
    ns = ProbeSocketNS(code=errno.EAGAIN)
    receipt = {"schema": "synthetic", "chain_status": "PASS",
               "evidence_class": "production-chain-fix-verification"}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ns)
    assert persisted["cleanup"]["control_port_unreachable"] is False
    assert persisted["cleanup_status"] == "FAIL"


def test_contract_B_still_listening_port_is_not_unreachable(tmp_path):
    """connect_ex==0 (port still live) stays FAIL — passes before and after."""
    ns = ProbeSocketNS(code=0)
    receipt = {"schema": "synthetic", "chain_status": "PASS",
               "evidence_class": "production-chain-fix-verification"}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ns)
    assert persisted["cleanup"]["control_port_unreachable"] is False
    assert persisted["cleanup_status"] == "FAIL"


def test_contract_B_real_refused_port_still_proves_unreachable(tmp_path):
    """Positive control: a genuine ECONNREFUSED from the OS still earns PASS.

    Uses only the suite's free-port connect_ex probe (a client connect to an
    unbound loopback port), never a listening socket.
    """
    port = synth._free_port()
    receipt = {"schema": "synthetic", "chain_status": "PASS",
               "evidence_class": "production-chain-fix-verification"}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), port=port)
    assert persisted["cleanup"]["control_port_unreachable"] is True
    assert persisted["cleanup_status"] == "PASS"
    assert persisted["status"] == "PASS" and persisted["fix_verified"] is True


# ---------------------------------------------------------------------------
# C — loopback RPC claim follows actual chain call arrival
# ---------------------------------------------------------------------------

def test_contract_C_never_executed_chain_must_not_claim_loopback_rpc(tmp_path, monkeypatch):
    """A NOT_RECORDED receipt (chain transport never entered) claims no RPC."""
    root, env, control_home = synth._env_and_home(tmp_path)
    store: dict = {"home": control_home}
    _arm(monkeypatch, store)
    synth._write_endpoint(control_home, port=54321)
    receipt = {"schema": "synthetic", "identity_kind": "synthetic",
               "receipt_file": str(tmp_path / "receipt.json"),
               "evidence_class": "production-chain-fix-verification"}

    def boom(data=b""):
        raise RuntimeError("injected backfill hashing failure")

    monkeypatch.setattr(t16.hashlib, "sha256", boom)
    ns = ProbeSocketNS()
    saved = t16.socket
    t16.socket = ns
    try:
        with pytest.raises(RuntimeError, match="injected backfill"):
            t16._run_task_owned_chain_session(root, env, control_home, root / "daemon.log",
                                              receipt, lambda: {"ok": True})
    finally:
        t16.socket = saved
    persisted = json.loads(Path(receipt["receipt_file"]).read_text(encoding="utf-8"))
    assert persisted["chain_status"] == "NOT_RECORDED"
    assert "checks" not in persisted, "no chain/stdio/RPC was ever executed"
    assert persisted["network_observations"]["loopback_rpc_used"] is False
    assert "authenticated" not in persisted["network_observations"]["note"]


def test_contract_C_entered_chain_records_loopback_usage(tmp_path):
    """Positive control: a chain whose transport was entered does claim it."""
    receipt = {"schema": "synthetic", "chain_status": "PASS",
               "evidence_class": "production-chain-fix-verification",
               "checks": {"index_status": "SUCCEEDED"}}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    assert persisted["network_observations"]["loopback_rpc_used"] is True
    assert "authenticated" in persisted["network_observations"]["note"]


# ---------------------------------------------------------------------------
# D — receipt persistence failures never mask verdicts or fabricate records
# ---------------------------------------------------------------------------

def _session_chain_break(tmp_path, monkeypatch, receipt_file):
    root, env, control_home = synth._env_and_home(tmp_path)
    store: dict = {"home": control_home}
    _arm(monkeypatch, store)
    synth._write_endpoint(control_home, port=54321)
    receipt = {"schema": "synthetic", "identity_kind": "synthetic",
               "receipt_file": str(receipt_file),
               "evidence_class": "production-chain-fix-verification"}
    ns = ProbeSocketNS()
    saved = t16.socket
    t16.socket = ns
    try:
        with pytest.raises(BaseException) as excinfo:
            t16._run_task_owned_chain_session(
                root, env, control_home, root / "daemon.log", receipt,
                lambda: (_ for _ in ()).throw(AssertionError("LINK-VERDICT-FAILURE")))
    finally:
        t16.socket = saved
    return receipt, store, excinfo


def test_contract_D_unrecoverable_write_failure_keeps_verdict_and_reports(tmp_path, monkeypatch):
    """Receipt file under an unwriteable parent: verdict survives, failure logged."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    receipt, store, excinfo = _session_chain_break(
        tmp_path, monkeypatch, blocker / "receipt.json")

    assert type(excinfo.value) is AssertionError, (
        f"a persistence fault must not mask the link verdict: {excinfo.value!r}")
    assert "LINK-VERDICT-FAILURE" in str(excinfo.value)
    assert receipt["chain_status"] == "FAIL", "link verdict must survive on the receipt"
    assert receipt.get("receipt_persisted") is False, "an unwritten receipt is not saved"
    assert receipt.get("persistence_error"), "secondary persistence failure must be reported"
    assert not (blocker / "receipt.json").exists()
    child = store["child"]
    assert child.terminate_calls + child.kill_calls >= 1, "child still released first"


def test_contract_D_missing_parent_dir_is_recovered_and_persisted(tmp_path, monkeypatch):
    """A merely-missing parent dir must be created, so the record never vanishes."""
    receipt, store, excinfo = _session_chain_break(
        tmp_path, monkeypatch, tmp_path / "fresh-dir" / "receipt.json")

    assert type(excinfo.value) is AssertionError, (
        f"recovery must still leave the link verdict raised: {excinfo.value!r}")
    assert "LINK-VERDICT-FAILURE" in str(excinfo.value)
    data = json.loads((tmp_path / "fresh-dir" / "receipt.json").read_text(encoding="utf-8"))
    assert data["chain_status"] == "FAIL"
    assert receipt.get("receipt_persisted") is True


def test_contract_D_copy_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    """Evidence-dir copy failure is reported; the primary receipt stays saved."""
    copy_blocker = tmp_path / "copy-blocker"
    copy_blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("COMSOL_T16_RECEIPT_DIR", str(copy_blocker))
    receipt = {"schema": "synthetic", "chain_status": "PASS",
               "evidence_class": "production-chain-fix-verification"}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    assert persisted["status"] == "PASS"
    assert receipt.get("receipt_persisted") is True
    assert receipt.get("copy_error"), "copy failure must be reported, not raised away"


# ---------------------------------------------------------------------------
# E — installed fix-verification booking is coupled to the fixed-invariant set
# ---------------------------------------------------------------------------

def test_contract_E_installed_fix_booking_requires_fixed_invariant_asserts():
    """Booking fix-verification must run the same fixed behavior invariants."""
    test = getattr(t16, "test_installed_stdio_chain_finds_sourced_unfamiliar_api_in_real_6_4_help")
    source = inspect.getsource(test)
    # the byte-identity booking rule may stay...
    assert '"production-chain-fix-verification" if artifact_matches_worktree' in source
    # ...but it must be wired to the SAME behavior invariants the other
    # identities use — 4-file equality alone is identity, not behavior.
    # (only comment lines may sit between the guard and the call)
    assert re.search(r"if artifact_matches_worktree:\s*\n(?:\s*#[^\n]*\n)*"
                     r"\s*_assert_fixed_chain_invariants\(checks\)",
                     source), ("installed branch must assert the fixed-chain invariants "
                               "exactly when it books fix-verification")

    # behavior pair: the characterization-weak set admits regression-shaped
    # results the fixed set rejects; booking must use the strict set.
    def weak_chain_asserts(checks):
        assert checks["index_status"] == "SUCCEEDED" and checks["indexed_count"] > 100
        assert checks["index_version"] == checks["search_version_used"]
        assert checks["modelutil_hit_present"] is True and checks["sha_matches_disk"] is True
        assert checks["phrase_present_via_get"] is True
        assert checks["wrapper_search_loadOnServer"] == [] and checks["wrapper_search_docMarker"] == []
        assert checks["sixthree"] == "UNAVAILABLE" and checks["bogus_ref"] == "NOT_FOUND" \
            and checks["outside_root_refusal"] is True
        assert checks["marker_alias_version"] in ("6.4", "unknown")

    weak = {"index_status": "SUCCEEDED", "indexed_count": 500, "index_version": "6.4",
            "search_version_used": "6.4", "modelutil_hit_present": True, "sha_matches_disk": True,
            "phrase_present_via_get": True, "wrapper_search_loadOnServer": [],
            "wrapper_search_docMarker": [], "sixthree": "UNAVAILABLE", "bogus_ref": "NOT_FOUND",
            "outside_root_refusal": True, "marker_alias_version": "unknown",
            "cross_doc_query": "SUCCEEDED", "fabricated": "SUCCEEDED",
            "multi_token_hit_count": 0, "multi_token_unverified_hits": 0,
            "multi_token_hits_all_verify": True, "docmarker_hit_present": False,
            "docmarker_signature_via_get": False}
    weak_chain_asserts(weak)  # weak set admits it (historical characterization)
    with pytest.raises(AssertionError):
        t16._assert_fixed_chain_invariants(weak)  # fixed set rejects it


# ---------------------------------------------------------------------------
# F — evidence_class defaults fail closed
# ---------------------------------------------------------------------------

def test_contract_F_unset_evidence_class_never_books_fix_verified(tmp_path):
    receipt = {"schema": "synthetic", "chain_status": "PASS"}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    assert persisted["evidence_class"] == "unspecified"
    assert persisted["status"] == "PASS"
    assert persisted["fix_verified"] is False, "an undeclared class cannot verify a fix"


def test_contract_F_unknown_evidence_class_never_books_fix_verified(tmp_path):
    receipt = {"schema": "synthetic", "chain_status": "PASS",
               "evidence_class": "some-experimental-class"}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    assert persisted["fix_verified"] is False, "an unknown class cannot verify a fix"


def test_contract_F_explicit_fix_verification_class_is_booked(tmp_path):
    """Positive control: the explicit fix-verification class still books."""
    receipt = {"schema": "synthetic", "chain_status": "PASS",
               "evidence_class": "production-chain-fix-verification"}
    persisted = _cleanup(tmp_path, receipt, ExitedChild(), socket_ns=ProbeSocketNS())
    assert persisted["status"] == "PASS" and persisted["fix_verified"] is True
