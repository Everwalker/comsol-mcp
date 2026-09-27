from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, canonical_request_hash
from comsol_mcp._operation_store import IdempotencyConflict, OperationStore
from comsol_mcp._project_authority import ProjectAuthority


HOST_CEILING = frozenset({"inspect", "project_write", "compute"})


class Harness:
    """Exercise the existing OperationStore admission/finish protocol around the draft."""

    def __init__(self, store: OperationStore, authority: ProjectAuthority):
        self.store = store
        self.authority = authority

    def call(self, operation: str, arguments: dict, key: str):
        digest = canonical_request_hash(operation, arguments, None, None)
        record, reused = self.store.begin(
            request_id="request-" + key,
            idempotency_key=key,
            request_hash=digest,
            operation=operation,
            metadata={"operation": operation, "arguments": arguments, "execution": {}},
        )
        if reused:
            return record["result"]
        result = self.authority.dispatch(operation, arguments)
        self.store.finish(record["operation_id"], status="SUCCEEDED", result=result)
        return result


def make_authority(store, workspace_root, permissions=None, verifier=None, grant_ceiling=None):
    state = {"permissions": set(permissions or {"inspect", "project_write", "compute"})}
    service = ProjectAuthority(
        store,
        workspace_root=workspace_root,
        permission_provider=lambda: state["permissions"],
        grant_ceiling=grant_ceiling or HOST_CEILING,
        authorization_verifier=verifier,
    )
    return service, state


def create_project(service, name="project-one", policy=None):
    return service.dispatch("project.create", {
        "label": name,
        "workspace": name,
        "policy": policy or {"permissions": ["inspect", "project_write"]},
    })["data"]["project"]


def test_create_roundtrip_restart_and_contract_update_uses_existing_operation_store(tmp_path):
    db_path = tmp_path / "control" / "operations.sqlite3"
    workspace_root = tmp_path / "authorized"
    workspace_root.mkdir()
    store = OperationStore(db_path)
    service, _ = make_authority(store, workspace_root)
    harness = Harness(store, service)
    result = harness.call("project.create", {
        "label": "Thermal study",
        "workspace": "cases/thermal",
        "policy": {"permissions": ["inspect", "project_write"], "timeouts": {"execution_timeout_s": 300}},
    }, "create-1")
    project = result["data"]["project"]
    assert Path(project["workspace"]).is_dir()
    assert project["revision"] == 1
    assert project["policy"]["permissions"] == ["inspect", "project_write"]

    contract = {"goal": "thermal response", "roi": {"name": "heater"}, "delivery_version": "draft"}
    changed = service.dispatch("project.contract_set", {"project_id": project["project_id"], "contract": contract})
    assert changed["data"]["revision"] == 2
    assert changed["data"]["contract_sha256"] == hashlib.sha256(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    store.close()
    reopened = OperationStore(db_path)
    restarted, _ = make_authority(reopened, workspace_root)
    readback = restarted.dispatch("project.inspect", {"project_id": project["project_id"]})
    assert readback["data"]["project"]["contract"] == contract
    assert readback["data"]["project"]["revision"] == 2
    assert restarted.dispatch("project.permissions", {"project_id": project["project_id"]})["data"]["effective_permissions"] == ["inspect", "project_write"]
    reopened.close()


def test_existing_durable_idempotency_replays_once_and_rejects_changed_body(tmp_path):
    workspace_root = tmp_path / "authorized"
    workspace_root.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    service, _ = make_authority(store, workspace_root)
    harness = Harness(store, service)
    args = {"label": "Repeat", "workspace": "repeat", "policy": {"permissions": ["inspect"]}}
    first = harness.call("project.create", args, "same-create-key")
    second = harness.call("project.create", args, "same-create-key")
    assert second == first
    assert len(list(workspace_root.iterdir())) == 1
    with pytest.raises(IdempotencyConflict):
        harness.call("project.create", {**args, "label": "Changed"}, "same-create-key")
    assert len(list(workspace_root.iterdir())) == 1
    assert len(store.db.execute("SELECT project_id FROM projects").fetchall()) == 1
    store.close()


def test_same_key_replay_uses_stored_result_not_a_second_create(tmp_path):
    workspace_root = tmp_path / "authorized"
    workspace_root.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    service, _ = make_authority(store, workspace_root)
    harness = Harness(store, service)
    args = {"label": "Replay", "workspace": "replay", "policy": {"permissions": ["inspect"]}}
    first = harness.call("project.create", args, "replay-key")
    original_id = first["data"]["project"]["project_id"]
    # Simulate process restart between duplicate RPCs.
    store.close()
    reopened = OperationStore(tmp_path / "ops.sqlite3")
    restarted, _ = make_authority(reopened, workspace_root)
    replay = Harness(reopened, restarted).call("project.create", args, "replay-key")
    assert replay["data"]["project"]["project_id"] == original_id
    assert len(reopened.db.execute("SELECT project_id FROM projects").fetchall()) == 1
    reopened.close()


def test_workspace_escape_symlink_alias_and_foreign_project_are_refused_without_project_rows(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "alias").symlink_to(outside, target_is_directory=True)
    store = OperationStore(tmp_path / "ops.sqlite3")
    service, _ = make_authority(store, root)
    before = store.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    with pytest.raises(ExecutionContractError) as escape:
        service.dispatch("project.create", {"label": "bad", "workspace": "alias/child", "policy": {}})
    assert escape.value.code in {"PROJECT_WORKSPACE_OUTSIDE_ROOT", "PROJECT_WORKSPACE_SYMLINK_REFUSED"}
    with pytest.raises(ExecutionContractError) as traversal:
        service.dispatch("project.create", {"label": "bad", "workspace": "../outside/child", "policy": {}})
    assert traversal.value.code == "PROJECT_WORKSPACE_OUTSIDE_ROOT"
    with pytest.raises(ExecutionContractError) as foreign:
        service.dispatch("project.inspect", {"project_id": "attacker-chosen-foreign-id"})
    assert foreign.value.code == "PROJECT_NOT_FOUND"
    assert store.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == before
    assert list(outside.iterdir()) == []
    assert not (root / "child").exists()
    store.close()


def test_replaced_workspace_symlink_is_detected_before_project_state_mutation(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    service, _ = make_authority(store, root)
    project = create_project(service, name="bound")
    workspace = Path(project["workspace"])
    moved = root / "original-workspace"
    workspace.rename(moved)
    workspace.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ExecutionContractError) as mismatch:
        service.dispatch("project.contract_set", {"project_id": project["project_id"], "contract": {"goal": "must not write"}})
    assert mismatch.value.code == "PROJECT_WORKSPACE_IDENTITY_MISMATCH"
    row = store.db.execute("SELECT record_json,revision FROM projects WHERE project_id=?", (project["project_id"],)).fetchone()
    assert json.loads(row["record_json"])["contract"] == {}
    assert row["revision"] == 1
    assert list(outside.iterdir()) == []
    store.close()


def test_policy_cannot_self_escalate_and_policy_update_requires_host_authority_and_ref(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    calls = []
    service, state = make_authority(store, root, permissions={"inspect", "project_write"},
                                    verifier=lambda project_id, ref: calls.append((project_id, ref)) or ref == "admitted-ref",
                                    grant_ceiling=HOST_CEILING | {"host_control"})
    with pytest.raises(ExecutionContractError) as escalation:
        service.dispatch("project.create", {"label": "bad", "workspace": "bad", "policy": {"permissions": ["compute"]}})
    assert escalation.value.code == "POLICY_ESCALATION_REFUSED"
    assert list(root.iterdir()) == []
    project = create_project(service)
    pid = project["project_id"]
    with pytest.raises(ExecutionContractError) as denied:
        service.dispatch("project.policy_set", {"project_id": pid, "policy": {"permissions": ["compute"]}, "authorization_ref": "admitted-ref"})
    assert denied.value.code == "PERMISSION_DENIED"
    assert calls == []
    state["permissions"].add("host_control")
    before = service.dispatch("project.inspect", {"project_id": pid})["data"]["project"]
    with pytest.raises(ExecutionContractError) as no_ref:
        service.dispatch("project.policy_set", {"project_id": pid, "policy": {"permissions": ["inspect"]}, "authorization_ref": "wrong-ref"})
    assert no_ref.value.code == "AUTHORIZATION_REQUIRED"
    assert calls == [(pid, "wrong-ref")]
    with pytest.raises(ExecutionContractError) as self_grant:
        service.dispatch("project.policy_set", {"project_id": pid, "policy": {"permissions": ["trusted_code"]}, "authorization_ref": "admitted-ref"})
    assert self_grant.value.code in {"INVALID_REQUEST", "POLICY_ESCALATION_REFUSED"}
    assert service.dispatch("project.inspect", {"project_id": pid})["data"]["project"] == before
    state["permissions"].add("compute")
    changed = service.dispatch("project.policy_set", {
        "project_id": pid,
        "policy": {"permissions": ["inspect", "compute"], "timeouts": {"execution_timeout_s": 8}},
        "authorization_ref": "admitted-ref",
    })
    assert changed["data"]["revision"] == before["revision"] + 1
    assert changed["data"]["authorization_ref_sha256"] == hashlib.sha256(b"admitted-ref").hexdigest()
    stored = service.dispatch("project.inspect", {"project_id": pid})["data"]["project"]
    assert "admitted-ref" not in json.dumps(stored)
    assert stored["policy_change_log"][-1]["authorization_ref_sha256"] == hashlib.sha256(b"admitted-ref").hexdigest()
    store.close()


def test_contract_and_policy_reject_credential_fields_before_mutation(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    service, state = make_authority(store, root, verifier=lambda project_id, ref: True,
                                    grant_ceiling=HOST_CEILING | {"host_control"})
    project = create_project(service)
    before = service.dispatch("project.inspect", {"project_id": project["project_id"]})["data"]["project"]
    with pytest.raises(ExecutionContractError) as contract_error:
        service.dispatch("project.contract_set", {"project_id": project["project_id"], "contract": {"password": "do-not-persist"}})
    assert contract_error.value.code == "INVALID_REQUEST"
    state["permissions"].add("host_control")
    with pytest.raises(ExecutionContractError) as policy_error:
        service.dispatch("project.policy_set", {"project_id": project["project_id"], "policy": {"permissions": ["inspect"], "api_token": "do-not-persist"}, "authorization_ref": "valid"})
    assert policy_error.value.code == "INVALID_REQUEST"
    after = service.dispatch("project.inspect", {"project_id": project["project_id"]})["data"]["project"]
    assert after == before
    store.close()


def test_project_creation_rolls_back_new_empty_directories_when_sqlite_insert_fails(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    service, _ = make_authority(store, root)
    store.db.execute("CREATE TRIGGER reject_project_insert BEFORE INSERT ON projects BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.dispatch("project.create", {"label": "rollback", "workspace": "nested/workspace", "policy": {"permissions": ["inspect"]}})
    assert not (root / "nested").exists()
    assert store.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    store.close()


def test_existing_online_backup_restore_roundtrips_project_records(tmp_path):
    from comsol_mcp._state_backup import create_backup, restore_backup

    root = tmp_path / "authorized"
    root.mkdir()
    source_home = tmp_path / "source-control"
    source_home.mkdir()
    store = OperationStore(source_home / "operations.sqlite3")
    service, _ = make_authority(store, root)
    project = create_project(service, name="backup-roundtrip")
    store.close()

    backup_path = tmp_path / "project-state.sqlite3"
    backup = create_backup(source_home, backup_path)
    restored_home = tmp_path / "restored-control"
    restored_home.mkdir()
    restore = restore_backup(restored_home, backup_path, backup["manifest_path"], confirm=True)
    assert restore["success"] is True
    reopened = OperationStore(restored_home / "operations.sqlite3")
    restored_authority, _ = make_authority(reopened, root)
    restored = restored_authority.dispatch("project.inspect", {"project_id": project["project_id"]})
    assert restored["data"]["project"]["workspace"] == project["workspace"]
    assert Path(project["workspace"]).is_dir()
    reopened.close()


def test_state_export_is_persisted_only_secret_free_and_marks_unknown_jobs(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    service, _ = make_authority(store, root)
    project = create_project(service, policy={"permissions": ["inspect", "project_write"]})
    pid = project["project_id"]
    contract = {"goal": "readback only", "thresholds": {"max_error": 0.01}}
    service.dispatch("project.contract_set", {"project_id": pid, "contract": contract})
    ref = {"schema_version": 1, "session_id": "session-A", "server_instance_id": "server-A", "model_tag": "ModelA", "generation": 1}
    operation, _ = store.begin(
        request_id="job-request", idempotency_key="job-key", request_hash="a" * 64,
        operation="study.run",
        metadata={
            "operation": "study.run",
            "arguments": {"study": "std1", "unrelated": "not exported"},
            "execution": {"project_id": pid, "model_ref": ref, "expected_revision": 2, "authorization_ref": "hidden-ref"},
        },
    )
    store.finish(operation["operation_id"], status="UNKNOWN", result={"secret": "result-body-must-not-export"})
    store.put_metadata("revisions", json.dumps(ref, sort_keys=True), {"project_id": pid, "model_ref": ref, "revision": 2})
    other, _ = store.begin(
        request_id="other-request", idempotency_key="other-key", request_hash="b" * 64,
        operation="model.save",
        metadata={"operation": "model.save", "arguments": {}, "execution": {"project_id": "foreign", "api_token": "omitted"}},
    )
    store.finish(other["operation_id"], status="FAILED", result={"message": "omitted"})

    exported = service.dispatch("project.state_export", {"project_id": pid, "detail": "summary"})["data"]
    encoded = json.dumps(exported, sort_keys=True)
    assert len(exported["jobs"]) == 1
    assert exported["jobs"][0]["status"] == "UNKNOWN"
    assert exported["active_or_unknown_jobs"] == [operation["job_id"]]
    assert exported["models"] == [{"model_ref": ref, "revision": 2}]
    assert exported["scope"] == "persisted_records_only; no live engine query"
    assert "hidden-ref" not in encoded
    assert "result-body-must-not-export" not in encoded
    assert "api_token" not in encoded
    assert "unrelated" not in encoded
    with pytest.raises(ExecutionContractError) as detail:
        service.dispatch("project.state_export", {"project_id": pid, "detail": "full"})
    assert detail.value.code == "INVALID_REQUEST"
    store.close()


def test_project_policy_permissions_are_a_second_gate_for_project_data(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    store = OperationStore(tmp_path / "ops.sqlite3")
    service, _ = make_authority(store, root)
    project = create_project(service, policy={"permissions": ["project_write"]})
    with pytest.raises(ExecutionContractError) as denied:
        service.dispatch("project.inspect", {"project_id": project["project_id"]})
    assert denied.value.code == "PERMISSION_DENIED"
    with pytest.raises(ExecutionContractError) as export_denied:
        service.dispatch("project.state_export", {"project_id": project["project_id"]})
    assert export_denied.value.code == "PERMISSION_DENIED"
    store.close()


def test_malformed_project_store_schema_fails_closed(tmp_path):
    path = tmp_path / "ops.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
    raw.execute("INSERT INTO schema_meta VALUES(1)")
    raw.execute("CREATE TABLE projects(project_id TEXT PRIMARY KEY, arbitrary TEXT)")
    raw.commit()
    raw.close()
    with pytest.raises(RuntimeError, match="invalid projects schema"):
        OperationStore(path)
