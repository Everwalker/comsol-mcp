from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from comsol_mcp._operation_store import OperationStore
from tools import run_native_w23_te_managed_preflight as runner


def _local_comsol_imports(source: Path) -> set[Path]:
    module_path = source.resolve().relative_to(runner.REPO)
    parts = list(module_path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    current_module = ".".join(parts)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    discovered: set[Path] = set()

    def add_module(name: str) -> None:
        if name != "comsol_mcp" and not name.startswith("comsol_mcp."):
            return
        segments = name.split(".")
        for index in range(1, len(segments)):
            package_init = runner.REPO.joinpath(*segments[:index], "__init__.py")
            if package_init.is_file():
                discovered.add(package_init.resolve())
        module_file = runner.REPO.joinpath(*segments).with_suffix(".py")
        package_file = runner.REPO.joinpath(*segments, "__init__.py")
        if module_file.is_file():
            discovered.add(module_file.resolve())
        elif package_file.is_file():
            discovered.add(package_file.resolve())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "comsol_mcp" or alias.name.startswith("comsol_mcp."):
                    add_module(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = current_module.split(".")[:-node.level]
                if node.module:
                    base.extend(node.module.split("."))
                    add_module(".".join(base))
                else:
                    for alias in node.names:
                        add_module(".".join([*base, alias.name]))
            elif node.module == "comsol_mcp":
                add_module(node.module)
                for alias in node.names:
                    add_module(f"comsol_mcp.{alias.name}")
            elif node.module and node.module.startswith("comsol_mcp."):
                add_module(node.module)
    return discovered


def test_frozen_source_paths_cover_transitive_local_runtime_imports() -> None:
    frozen = {path.resolve() for path in runner.SOURCE_PATHS.values()}
    pending = list(frozen)
    scanned: set[Path] = set()
    required: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in scanned or source.suffix != ".py" or not source.is_file():
            continue
        scanned.add(source)
        for imported in _local_comsol_imports(source):
            required.add(imported)
            if imported not in scanned:
                pending.append(imported)
    assert required <= frozen, "unfrozen local runtime dependencies: " + ", ".join(
        str(path.relative_to(runner.REPO)) for path in sorted(required - frozen))


def test_unknown_dispatch_is_recorded_once_and_never_retried(tmp_path: Path) -> None:
    calls = 0

    def dispatcher(**_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("transport observation timed out")

    record = tmp_path / "worker_request.json"
    identity = {"request_id": "req-once", "idempotency_key": "idem-once"}
    with pytest.raises(TimeoutError, match="transport observation timed out"):
        runner._invoke_once(dispatcher, {}, record, identity)

    receipt = json.loads(record.read_text())
    assert calls == 1
    assert receipt["status"] == "UNKNOWN"
    assert receipt["dispatch_count"] == 1
    assert receipt["request_identity"] == identity
    assert receipt["retry"] == "FORBIDDEN"


def test_terminal_worker_failure_is_observed_without_retry(tmp_path: Path) -> None:
    calls = 0

    def dispatcher(**_kwargs):
        nonlocal calls
        calls += 1
        return {"success": False, "data": {"worker": {"status": "FAILED"}}}

    record = tmp_path / "worker_request.json"
    response = runner._invoke_once(dispatcher, {}, record, {"request_id": "req-fail"})

    receipt = json.loads(record.read_text())
    assert calls == 1
    assert response["data"]["worker"]["status"] == "FAILED"
    assert receipt["status"] == "TERMINAL"
    assert receipt["worker_status"] == "FAILED"


def test_missing_terminal_worker_receipt_stays_unknown(tmp_path: Path) -> None:
    response = {"success": True, "data": {"message": "accepted"}}
    assert runner._worker_request_terminal(response) is False

    record = tmp_path / "worker_request.json"
    runner._invoke_once(lambda **_kwargs: response, {}, record, {"request_id": "req-pending"})
    receipt = json.loads(record.read_text())
    assert receipt["status"] == "UNKNOWN"
    assert receipt["retry"] == "FORBIDDEN"


def test_lost_worker_label_is_unknown_for_cleanup() -> None:
    assert runner._worker_request_terminal({"data": {"worker": {"status": "LOST"}}}) is False


def test_actual_study_run_audit_uses_real_operation_store_list_job_contract(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        job, _ = store.begin(
            request_id="request-audit", idempotency_key="idempotency-audit",
            request_hash="hash-audit", operation="operation_call",
            metadata={"project_id": runner.PROJECT_ID},
        )
        store.add_event(job["job_id"], "worker_request", {
            "phase": "submitted", "kind": "call",
            "metadata": {"type": "call", "method": "run"},
        })
        store.add_event(job["job_id"], "worker_request", {
            "phase": "submitted", "kind": "call",
            "metadata": {"type": "call", "method": "getString"},
        })
        daemon = type("Daemon", (), {"store": store})()
        rows = runner._actual_study_run_submissions(daemon)
        assert len(rows) == 1
        assert rows[0]["worker_event"]["metadata"]["method"] == "run"
    finally:
        store.close()


def test_idle_proof_fails_closed_for_unobserved_request_in_real_ledger(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        job, _ = store.begin(
            request_id="request-live", idempotency_key="idempotency-live",
            request_hash="hash-live", operation="operation_call",
            metadata={"project_id": runner.PROJECT_ID},
        )
        store.update_job(job["job_id"], "RUNNING")
        store.add_event(job["job_id"], "worker_request", {
            "phase": "submitted", "request_id": "req-live",
        })
        daemon = type("Daemon", (), {"store": store})()
        proof = runner._daemon_idle_proof(daemon)
        assert proof["idle"] is False
        assert proof["reason"] == "nonterminal_or_unknown_work"
        assert proof["worker_requests"][0]["observed_status"] is None
    finally:
        store.close()


def test_idle_proof_passes_only_terminal_job_and_observed_worker_request(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        job, _ = store.begin(
            request_id="request-done", idempotency_key="idempotency-done",
            request_hash="hash-done", operation="operation_call",
            metadata={"project_id": runner.PROJECT_ID},
        )
        store.add_event(job["job_id"], "worker_request", {
            "phase": "submitted", "request_id": "req-done",
        })
        store.add_event(job["job_id"], "worker_request", {
            "phase": "observed", "request_id": "req-done", "status": "SUCCEEDED",
        })
        store.finish(job["operation_id"], status="SUCCEEDED", result={"success": True})
        daemon = type("Daemon", (), {"store": store})()
        proof = runner._daemon_idle_proof(daemon)
        assert proof["idle"] is True
        assert proof["reason"] == "all_jobs_and_worker_requests_terminal"
        assert proof["worker_requests"][0]["observed_status"] == "SUCCEEDED"
    finally:
        store.close()


def test_lost_worker_observation_does_not_make_daemon_idle(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        job, _ = store.begin(
            request_id="request-lost", idempotency_key="idempotency-lost",
            request_hash="hash-lost", operation="operation_call",
            metadata={"project_id": runner.PROJECT_ID},
        )
        store.add_event(job["job_id"], "worker_request", {
            "phase": "submitted", "request_id": "req-lost",
        })
        store.add_event(job["job_id"], "worker_request", {
            "phase": "observed", "request_id": "req-lost", "status": "LOST",
        })
        store.finish(job["operation_id"], status="LOST", result={"success": False})
        daemon = type("Daemon", (), {"store": store})()
        proof = runner._daemon_idle_proof(daemon)
        assert proof["idle"] is False
        assert proof["worker_requests"][0]["observed_status"] == "LOST"
    finally:
        store.close()


def test_fullpaged_ledger_reconciles_terminal_worker_and_preserves_unknown_domain(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        connect, _ = store.begin(
            request_id="request-connect", idempotency_key="idem-connect",
            request_hash="hash-connect", operation="server_connect",
            metadata={"project_id": runner.PROJECT_ID},
        )
        store.finish(connect["operation_id"], status="SUCCEEDED", result={"success": True})
        fixture, _ = store.begin(
            request_id="request-fixture", idempotency_key="idem-fixture",
            request_hash="hash-fixture", operation="operation_call",
            metadata={"project_id": runner.PROJECT_ID},
        )
        for request_id, kind, status in (
            ("snapshot-1", "model_snapshot", "SUCCEEDED"),
            ("snapshot-2", "model_snapshot", "SUCCEEDED"),
            ("fixture-1", "code_execute", "FAILED"),
        ):
            store.add_event(fixture["job_id"], "worker_request", {
                "phase": "submitted", "request_id": request_id, "kind": kind,
                "metadata": {"request_id": request_id, "type": kind},
            })
            store.add_event(fixture["job_id"], "worker_request", {
                "phase": "observed", "request_id": request_id, "kind": kind,
                "status": status, "reply": {"status": status},
                "metadata": {"request_id": request_id, "type": kind},
            })
        unknown_result = {"success": False, "execution_state_unknown": True,
                          "domain_outcome": {"state": "unknown", "safe_retry": False},
                          "requires_model_reconciliation": True}
        store.finish(fixture["operation_id"], status="UNKNOWN", result=unknown_result)
        daemon = type("Daemon", (), {"store": store})()

        receipt = runner._ledger_reconciliation(daemon, page_size=1)
        sqlite_receipt = runner._backup_operation_store(daemon, tmp_path / "ledger-snapshot.sqlite3")

        assert receipt["pagination"]["job_pages"] == 2
        assert receipt["pagination"]["event_pages_by_job"][fixture["job_id"]] > 1
        assert receipt["submitted_count"] == receipt["observed_count"] == 3
        assert receipt["safe_for_owned_cleanup"] is True
        assert runner._ledger_allows_owned_cleanup(receipt, require_fixture_terminal=True) is True
        assert receipt["durable_result"] == {
            "domain_outcome": "UNKNOWN", "execution_state_unknown": True,
            "domain_state_was_not_rewritten": True, "safe_retry": False,
        }
        assert sqlite_receipt["consistent_sqlite_backup"] is True
        snapshot_db = sqlite3.connect(sqlite_receipt["path"])
        try:
            status = snapshot_db.execute("SELECT status FROM jobs WHERE job_id=?",
                                         (fixture["job_id"],)).fetchone()[0]
        finally:
            snapshot_db.close()
        assert status == "UNKNOWN"
        assert store.job(fixture["job_id"])["status"] == "UNKNOWN"
    finally:
        store.close()


def test_fullpaged_ledger_blocks_missing_or_orphan_worker_observations(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        job, _ = store.begin(
            request_id="request-incomplete", idempotency_key="idem-incomplete",
            request_hash="hash-incomplete", operation="operation_call",
            metadata={"project_id": runner.PROJECT_ID},
        )
        store.add_event(job["job_id"], "worker_request", {
            "phase": "submitted", "request_id": "req-pending", "kind": "code_execute",
        })
        store.add_event(job["job_id"], "worker_request", {
            "phase": "observed", "request_id": "req-orphan", "status": "SUCCEEDED",
        })
        daemon = type("Daemon", (), {"store": store})()
        receipt = runner._ledger_reconciliation(daemon, page_size=1)
        assert receipt["safe_for_owned_cleanup"] is False
        assert receipt["pending_submissions"]
        assert receipt["orphan_observations"]
        assert runner._ledger_allows_owned_cleanup(receipt) is False
    finally:
        store.close()


def test_empty_reconciled_ledger_does_not_claim_an_unknown_domain_was_retained() -> None:
    receipt = runner._ledger_reconciliation(None)
    assert receipt["status"] == "PASS_TERMINAL_WORKER_LEDGER_RECONCILED_NO_UNKNOWN_DOMAIN_STATE"
    assert receipt["durable_result"] == {
        "domain_outcome": "NOT_UNKNOWN", "execution_state_unknown": False,
        "domain_state_was_not_rewritten": True, "safe_retry": None,
    }


def test_exact_cleanup_sends_term_only_after_ledger_and_exact_identity_match(tmp_path: Path, monkeypatch) -> None:
    class FakePopen:
        def __init__(self, pid: int):
            self.pid = pid
            self.alive = True
            self.terminated = False

        def poll(self):
            return None if self.alive else 0

        def terminate(self):
            self.terminated = True
            self.alive = False

        def wait(self, timeout=None):
            self.alive = False
            return 0

    server_id = {"pid": 701, "birth": "birth-server", "command": "/task/comsol mphserver",
                 "command_sha256": "server-hash"}
    worker_id = {"pid": 702, "birth": "birth-worker", "command": "/task/java PersistentComsolWorker",
                 "command_sha256": "worker-hash"}
    server_proc, worker_proc = FakePopen(701), FakePopen(702)
    server = type("Server", (), {})()
    server.proc = server_proc
    server.port = 61001
    server.work = Path("/task")
    server.worker = type("Worker", (), {"_process": worker_proc,
                                        "close": lambda self: None})()
    current = {701: server_id.copy(), 702: worker_id.copy()}

    def snapshot(pid):
        proc = server_proc if pid == 701 else worker_proc if pid == 702 else None
        return current.get(pid) if proc is None or proc.alive else None

    inventory_before = {
        "probes_ok": True, "quiescent": False,
        "processes": [
            {"pid": 701, "ppid": 1, "comm": "java", "argv": server_id["command"]},
            {"pid": 702, "ppid": 1, "comm": "java", "argv": worker_id["command"]},
        ],
        "comsol_engine_processes": [{"pid": 701}],
        "persistent_worker_processes": [{"pid": 702}],
    }
    inventory_after = {"probes_ok": True, "quiescent": True,
                       "comsol_engine_processes": [], "persistent_worker_processes": []}
    inventory_calls = iter((inventory_before, inventory_after))
    monkeypatch.setattr(runner, "_process_inventory", lambda: next(inventory_calls))
    monkeypatch.setattr(runner, "_listener_matches", lambda _server: {"matches": True})
    monkeypatch.setattr(runner, "_port_listener_probe", lambda port: {"port": port, "absent": True})
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        fixture, _ = store.begin(
            request_id="unknown-domain-operation", idempotency_key="unknown-domain-idem",
            request_hash="unknown-domain-hash", operation="operation_call",
            metadata={"project_id": runner.PROJECT_ID},
        )
        store.add_event(fixture["job_id"], "worker_request", {
            "phase": "submitted", "request_id": "fixture-failed", "kind": "code_execute",
            "metadata": {"request_id": "fixture-failed", "type": "code_execute"},
        })
        store.add_event(fixture["job_id"], "worker_request", {
            "phase": "observed", "request_id": "fixture-failed", "kind": "code_execute",
            "status": "FAILED", "reply": {"status": "FAILED"},
            "metadata": {"request_id": "fixture-failed", "type": "code_execute"},
        })
        unknown_result = {"success": False, "execution_state_unknown": True,
                          "domain_outcome": {"state": "unknown", "safe_retry": False},
                          "requires_model_reconciliation": True}
        store.finish(fixture["operation_id"], status="UNKNOWN", result=unknown_result)
        daemon = type("Daemon", (), {"store": store})()
        reconciliation = runner._ledger_reconciliation(daemon, page_size=1)
        assert reconciliation["status"] == "PASS_TERMINAL_WORKER_LEDGER_RECONCILED_DURABLE_DOMAIN_UNKNOWN_RETAINED"
        assert reconciliation["submitted_count"] == reconciliation["observed_count"] == 1
        assert runner._ledger_allows_owned_cleanup(reconciliation, require_fixture_terminal=True)

        receipt = runner._exact_owned_cleanup(
            server, server_identity=server_id, worker_identity=worker_id, worker_port=61002,
            process_snapshot=snapshot, reconciliation=reconciliation, evidence=tmp_path,
            require_fixture_terminal=True)

        assert store.job(fixture["job_id"])["status"] == "UNKNOWN"
        assert store.job(fixture["job_id"])["result"] == unknown_result
    finally:
        store.close()

    assert receipt["status"] == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT"
    assert server_proc.terminated is True
    assert worker_proc.terminated is True
    assert receipt["durable_unknown_preserved"] is True
    assert all(action["signal"] == "SIGTERM" for action in receipt["actions"])


def test_exact_cleanup_refuses_identity_drift_without_signaling(tmp_path: Path, monkeypatch) -> None:
    class FakePopen:
        pid = 711
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    proc = FakePopen()
    server = type("Server", (), {"proc": proc, "port": 61011, "work": Path("/task"),
                                 "worker": None})()
    monkeypatch.setattr(runner, "_process_inventory", lambda: {"probes_ok": True, "processes": []})
    monkeypatch.setattr(runner, "_listener_matches", lambda _server: {"matches": True})
    reconciliation = {"safe_for_owned_cleanup": True, "status": "PASS", "durable_result": {},
                      "checks": {"all_submissions_observed_once": True,
                                 "all_observed_workers_terminal": True,
                                 "no_orphan_observations": True,
                                 "no_ambiguous_worker_ids": True,
                                 "no_queued_or_running_jobs": True}}
    expected = {"pid": 711, "birth": "expected", "command": "/task/comsol mphserver",
                "command_sha256": "expected-hash"}
    observed = {"pid": 711, "birth": "different", "command": "/task/comsol mphserver",
                "command_sha256": "other-hash"}
    receipt = runner._exact_owned_cleanup(
        server, server_identity=expected, worker_identity=None, worker_port=None,
        process_snapshot=lambda _pid: observed, reconciliation=reconciliation,
        evidence=tmp_path)
    assert receipt["status"] == "CLEANUP_BLOCKED"
    assert proc.terminated is False


def test_exact_cleanup_refuses_unattributed_comsol_descendant(tmp_path: Path, monkeypatch) -> None:
    class FakePopen:
        pid = 721
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    proc = FakePopen()
    server = type("Server", (), {"proc": proc, "port": 61021, "work": Path("/task"),
                                 "worker": None})()
    expected = {"pid": 721, "birth": "birth", "command": "/task/java mphserver",
                "command_sha256": "hash"}
    child = {"pid": 722, "birth": "child-birth", "command": "/other/comsol mphserver",
             "command_sha256": "child-hash"}
    monkeypatch.setattr(runner, "_process_inventory", lambda: {
        "probes_ok": True, "processes": [
            {"pid": 721, "ppid": 1, "comm": "java", "argv": expected["command"]},
            {"pid": 722, "ppid": 721, "comm": "bash", "argv": child["command"]},
        ]})
    monkeypatch.setattr(runner, "_listener_matches", lambda _server: {"matches": True})
    reconciliation = {"safe_for_owned_cleanup": True, "status": "PASS", "durable_result": {},
                      "checks": {"all_submissions_observed_once": True,
                                 "all_observed_workers_terminal": True,
                                 "no_orphan_observations": True,
                                 "no_ambiguous_worker_ids": True,
                                 "no_queued_or_running_jobs": True}}
    receipt = runner._exact_owned_cleanup(
        server, server_identity=expected, worker_identity=None, worker_port=None,
        process_snapshot=lambda pid: {721: expected, 722: child}.get(pid),
        reconciliation=reconciliation, evidence=tmp_path)
    assert receipt["status"] == "CLEANUP_BLOCKED"
    assert "attribution is ambiguous" in receipt["reason"]
    assert proc.terminated is False


def test_native_selection_readback_requires_boundary_domains_and_assigned_entities() -> None:
    def box(dimension, xmin=0, xmax=1):
        props = {"entitydim": {"has_property_exact": True, "string_readback": str(dimension)},
                 "condition": {"has_property_exact": True, "string_readback": "inside"}}
        props.update({key: {"has_property_exact": True, "string_readback": str(value)}
                      for key, value in (("xmin", xmin), ("xmax", xmax), ("ymin", -1), ("ymax", 1))})
        return {"requested_properties": props}

    def entities(tag, dimension, ids, assigned=None):
        record = {"selection_tag": tag, "entity_dimension": dimension,
                  "entity_ids": ids, "entity_count": len(ids)}
        if assigned is not None:
            record.update({"assigned_entity_ids": assigned,
                           "assigned_entity_count": len(assigned),
                           "matches_assigned_selection": assigned == ids})
        return record

    readback = {
        "port_selections": [box(1, -10.001, -9.999), box(1, 9.999, 10.001)],
        "pml_selection": box(2),
        "port_plane_semantics": {
            "scope": "API_PROPERTY_READBACK_ONLY",
            "api_only_output_port_x_um": 10.0,
            "science_receiver_plane_x_um": 8.0,
            "output_selection_is_science_receiver": False,
            "must_not_reuse_as_coupling_plane": True,
            "science_receiver_status": "REQUIRED_IN_LATER_SCIENCE_BUILDER_NOT_BUILT",
        },
        "port_entity_readback": [entities("selInputPort", 1, [1, 2], [1, 2]),
                                  entities("selOutputPort", 1, [3, 4], [3, 4])],
        "pml_entity_readback": entities("selPmlDomains", 2, [1, 2, 3]),
    }
    assert runner._validate_native_selection_readback(readback) == {
        "input_port_entity_count": 2, "output_port_entity_count": 2, "pml_domain_count": 3}
    readback["port_selections"][0]["requested_properties"]["entitydim"]["string_readback"] = "2"
    with pytest.raises(AssertionError, match="expected 1"):
        runner._validate_native_selection_readback(readback)
    readback["port_selections"][0]["requested_properties"]["entitydim"]["string_readback"] = "1"
    readback["port_plane_semantics"]["science_receiver_plane_x_um"] = 10.0
    readback["port_plane_semantics"]["output_selection_is_science_receiver"] = True
    readback["port_plane_semantics"]["must_not_reuse_as_coupling_plane"] = False
    with pytest.raises(AssertionError, match="API-only x=10 Port"):
        runner._validate_native_selection_readback(readback)


def test_freeze_validation_rejects_a_changed_hash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "EVIDENCE_ROOT", tmp_path)
    evidence = tmp_path / "candidate"
    evidence.mkdir()
    freeze = evidence / "freeze.json"
    freeze.write_text('{"status":"FROZEN_CANDIDATE_AWAITING_EXPLICIT_EXECUTION_SLOT"}\n')
    with pytest.raises(RuntimeError, match="freeze hash mismatch"):
        runner._approve_freeze(freeze, "0" * 64)


def test_freeze_validation_rejects_paths_outside_candidate_root(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze.json"
    freeze.write_text("{}\n")
    with pytest.raises(RuntimeError, match="under the W23 evidence root"):
        runner._approve_freeze(freeze, "0" * 64)


def test_execute_script_imports_runtime_from_foreign_cwd_then_rejects_outside_freeze(tmp_path: Path) -> None:
    freeze = tmp_path / "nonexistent-candidate" / "freeze.json"
    work = tmp_path / "never-created-work"
    evidence = tmp_path / "never-created-evidence"
    result = subprocess.run(
        [sys.executable, str(Path(runner.__file__).resolve()), "execute",
         "--freeze", str(freeze), "--approved-freeze-sha256", "0" * 64,
         "--work", str(work), "--evidence", str(evidence)],
        cwd=tmp_path, text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode != 0
    assert "under the W23 evidence root" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "ImportError" not in result.stderr
    assert not work.exists()
    assert not evidence.exists()


def test_execute_cli_imports_runtime_then_rejects_invalid_freeze_without_birth(tmp_path: Path) -> None:
    repo = Path(runner.__file__).resolve().parents[1]
    script = r"""
import sys
from pathlib import Path
repo, scratch = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo))
from tools import run_native_w23_te_managed_preflight as runner
runner.EVIDENCE_ROOT = scratch / "synthetic-evidence-root"
freeze = runner.EVIDENCE_ROOT / "synthetic-invalid-candidate" / "freeze.json"
freeze.parent.mkdir(parents=True)
freeze.write_text('{"status":"SYNTHETIC_INVALID_FREEZE"}\\n', encoding="utf-8")
work = scratch / "never-created-work"
evidence = scratch / "never-created-evidence"
sys.argv = [str(Path(runner.__file__).resolve()), "execute",
            "--freeze", str(freeze), "--approved-freeze-sha256", "0" * 64,
            "--work", str(work), "--evidence", str(evidence)]
raise SystemExit(runner.main())
"""
    work = tmp_path / "never-created-work"
    evidence = tmp_path / "never-created-evidence"
    result = subprocess.run(
        [sys.executable, "-c", script, str(repo), str(tmp_path)],
        cwd=tmp_path, text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode != 0
    assert "freeze hash mismatch" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "ImportError" not in result.stderr
    assert not work.exists()
    assert not evidence.exists()


def test_local_inventory_is_quiescent_only_after_both_probes_pass(monkeypatch) -> None:
    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(
            command, 0, "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n", "")

    monkeypatch.setattr(runner.subprocess, "run", run)
    inventory = runner._process_inventory()
    assert inventory["probes_ok"] is True
    assert inventory["quiescent"] is True
    assert inventory["comsol_engine_processes"] == []
    assert inventory["persistent_worker_processes"] == []
    assert inventory["comsol_listener_rows"] == []


def test_local_inventory_blocks_existing_engine_worker_and_listener(monkeypatch) -> None:
    ps_rows = (
        " 700 1 /Applications/COMSOL64/Multiphysics/bin/comsol mphserver -port 61000\n"
        " 701 1 java com.comsol.mcp.worker_java.PersistentComsolWorker\n"
    )
    lsof_rows = (
        "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
        "mphserver 700 user 5u IPv4 0 0 TCP 127.0.0.1:61000 (LISTEN)\n"
    )

    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return subprocess.CompletedProcess(command, 0, ps_rows, "")
        return subprocess.CompletedProcess(command, 0, lsof_rows, "")

    monkeypatch.setattr(runner.subprocess, "run", run)
    inventory = runner._process_inventory()
    assert inventory["probes_ok"] is True
    assert inventory["quiescent"] is False
    assert [row["pid"] for row in inventory["comsol_engine_processes"]] == [700]
    assert [row["pid"] for row in inventory["persistent_worker_processes"]] == [701]
    assert len(inventory["comsol_listener_rows"]) == 1


def test_local_readback_parser_requires_matching_worker_and_operation_results() -> None:
    readback = {"fixture_id": "fixture", "ports": []}
    response = {
        "success": True,
        "data": {
            "worker": {"ok": True, "status": "SUCCEEDED", "result": {"readback": readback}},
            "readback": {"executed": True, "readback": readback},
        },
    }
    assert runner._java_action_readback(response, "test") == readback
    response["data"]["worker"]["result"]["readback"] = {"different": True}
    with pytest.raises(AssertionError, match="differ"):
        runner._java_action_readback(response, "test")
