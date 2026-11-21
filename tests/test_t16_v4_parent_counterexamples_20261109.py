"""Independent synthetic counterexamples for T16 harness v4.

No real subprocess, socket, COMSOL, Worker, daemon or external data is used.
These assertions describe desired evidence honesty, not completed fixes.
"""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

import test_t16_docs_installed_qwen_20261109 as t16
import test_t16_daemon_lifecycle_synthetic_qwen_20261109 as synth
import test_t16_harness_v4_persistence_qwen_20261109 as v4


def test_nonerror_marker_without_project_does_not_confirm_loopback(tmp_path, monkeypatch):
    root, env, _ = synth._env_and_home(tmp_path)
    receipt = {
        "identity_kind": "synthetic",
        "receipt_file": str(tmp_path / "receipt.json"),
        "evidence_class": "production-chain-fix-verification",
    }

    def responder(name, arguments):
        described = v4.describe_ok(name, arguments)
        if described is not None:
            return described
        # Shape of an ordinary non-error marker, not a project.create result.
        return SimpleNamespace(isError=False, structuredContent={
            "data": {"operation_id": "project.create", "arguments": {}}
        })

    v4._install_fake_mcp(monkeypatch, responder)
    with pytest.raises(KeyError, match="project"):
        t16._run_chain_recorded(receipt, lambda: t16._run_docs_chain(root, env, receipt))
    assert receipt["chain_status"] == "FAIL"
    assert receipt["loopback_rpc_attempted"] is True
    persisted = v4._cleanup(tmp_path, receipt, v4.ExitedChild(), socket_ns=v4.ProbeSocketNS())
    assert persisted["network_observations"]["loopback_rpc_confirmed"] is False, persisted["network_observations"]
    assert "authenticated" not in persisted["network_observations"]["note"]


def test_post_write_failure_does_not_leave_disk_claiming_confirmed_persistence(tmp_path, monkeypatch):
    monkeypatch.delenv("COMSOL_T16_RECEIPT_DIR", raising=False)
    receipt_file = tmp_path / "receipt.json"
    receipt = {"identity_kind": "synthetic", "receipt_file": str(receipt_file),
               "chain_status": "PASS", "cleanup_status": "PASS", "status": "PASS", "fix_verified": True}
    original = Path.write_text
    attempts = []

    def write_then_fail(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == receipt_file:
            attempts.append(path)
            if len(attempts) == 2:
                # Model an error surfaced after bytes were written, e.g. a
                # close/flush failure. Durability is unknown, not success.
                raise OSError("synthetic post-write/close failure")
        return result

    monkeypatch.setattr(Path, "write_text", write_then_fail)
    t16._persist_receipt(receipt)
    assert len(attempts) == 2
    assert receipt["receipt_persisted"] is False
    assert "post-write/close failure" in receipt["persistence_error"]
    disk = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert disk["receipt_persisted"] is False or disk == receipt, (disk, receipt)
