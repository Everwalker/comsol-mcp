"""T10 synthetic same-worker legacy project binding, using actual SQLite/backend.

Only a fresh synthetic schema-version legacy record is used; no real Worker,
model, UNKNOWN, historical state or refused generation/rebind probe is touched.
"""
import json
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._runtime_state import restore_runtime_state, runtime_state_snapshot


def key(ref):
    return json.dumps(ref, sort_keys=True, separators=(",", ":"))


def scenario(tmp_path, current_project=None):
    store = OperationStore(tmp_path / "operations.sqlite3")
    original = SessionLedger("legacy-session", "legacy-server")
    ref = original.bind_model("legacy-model", fingerprint="legacy-data")
    identity = {"runtime_id": "legacy-runtime", "worker_instance_id": "legacy-worker",
                "connection_epoch": 4, "server_instance_id": "legacy-server"}
    state = runtime_state_snapshot(original, identity)
    state["models"][ref.model_tag]["ref"]["schema_version"] = True
    old_ref = state["models"][ref.model_tag]["ref"]
    old_key, new_key = key(old_ref), key(ref.as_dict())
    assert old_key != new_key
    store.put_metadata("sessions", "legacy-session", state)
    store.put_metadata("runtimes", "legacy-runtime", state)
    store.put_metadata("revisions", old_key, {"project_id": "project-a", "revision": 0,
                                               "attribution": "PROJECT_BOUND", "model_ref": old_ref})
    if current_project is not None:
        store.put_metadata("revisions", new_key, {"project_id": current_project, "revision": 0,
                                                  "attribution": "PROJECT_BOUND", "model_ref": ref.as_dict()})
    ledger, unknown = restore_runtime_state(store, "legacy-session", identity)
    assert not unknown
    backend = ManagedBackend(tmp_path / "control", store, service=SimpleNamespace(ledger=ledger),
                             registry={}, project_root=tmp_path)
    backend.worker_identity = identity
    calls = []
    backend.project_authorizer = lambda project, permission: calls.append((project, permission)) or {"project_id": project}
    return store, backend, old_key, new_key, calls


def test_legacy_project_is_preserved_with_same_worker_and_authority(tmp_path):
    store, backend, old_key, new_key, calls = scenario(tmp_path)
    try:
        backend.persist()
        assert store.get_metadata("revisions", old_key)["project_id"] == "project-a"
        assert store.get_metadata("revisions", new_key)["project_id"] == "project-a"
        assert calls == [("project-a", "project_write")]
        backend.persist()
        assert store.get_metadata("revisions", new_key)["project_id"] == "project-a"
    finally:
        backend.close()
        store.close()


def test_conflicting_legacy_and_current_projects_leave_all_rows_unchanged(tmp_path):
    store, backend, old_key, new_key, calls = scenario(tmp_path, "project-b")
    rows = (("sessions", "legacy-session"), ("runtimes", "legacy-runtime"),
            ("revisions", old_key), ("revisions", new_key))
    try:
        before = [store.get_metadata(table, ident) for table, ident in rows]
        with pytest.raises(Exception) as exc:
            backend.persist()
        assert getattr(exc.value, "code", None) == "PROJECT_IDENTITY_MISMATCH"
        assert [store.get_metadata(table, ident) for table, ident in rows] == before
        assert not store.db.in_transaction
    finally:
        backend.close()
        store.close()
