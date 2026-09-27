from copy import deepcopy

import pytest

from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._runtime_state import save_runtime_state
from comsol_mcp._session_lifecycle import (
    SessionLifecycleMalformed,
    SessionLifecycleProjectConflict,
    SessionLifecycleRevisionConflict,
    SessionLifecycleStore,
    new_lifecycle_record,
    validate_lifecycle_record,
)


def _lifecycle(project_id="project-a", session_id="session-a", **overrides):
    fields = {
        "project_id": project_id,
        "session_id": session_id,
        "state": "CONNECTED",
        "runtime_id": "comsol-64",
        "endpoint": {"host": "127.0.0.1", "port": 2036},
        "client_state": "CONNECTED",
        "server_state": "SHARED",
        "server_ownership": "shared",
        "worker_instance_id": "worker-1",
        "worker_epoch": 1,
        "server_instance_id": "worker-epoch-1",
        "health": {"status": "HEALTHY", "observed_at": "2026-09-27T08:00:00Z", "source": "worker-health"},
    }
    fields.update(overrides)
    return new_lifecycle_record(**fields)


def _runtime_identity():
    return {
        "runtime_id": "comsol-64",
        "worker_instance_id": "worker-1",
        "connection_epoch": 1,
        "server_instance_id": "worker-epoch-1",
    }


def test_runtime_snapshot_merge_preserves_lifecycle_and_lifecycle_update_preserves_ledger(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        lifecycle = SessionLifecycleStore(store)
        lifecycle.save(_lifecycle())
        ledger = SessionLedger("session-a", "worker-epoch-1")
        ledger.bind_model("ModelA", fingerprint="model-a")
        save_runtime_state(store, ledger, _runtime_identity())

        row = store.get_metadata("sessions", "session-a")
        assert row["lifecycle"]["project_id"] == "project-a"
        assert row["models"]["ModelA"]["fingerprint"] == "model-a"

        updated = deepcopy(row["lifecycle"])
        updated["state"] = "DISCONNECTED"
        updated["client_state"] = "DISCONNECTED"
        lifecycle.save(updated, expected_revision=1)

        final = store.get_metadata("sessions", "session-a")
        assert final["lifecycle"]["revision"] == 2
        assert final["lifecycle"]["state"] == "DISCONNECTED"
        assert final["models"]["ModelA"]["fingerprint"] == "model-a"
        assert final["worker_identity"] == _runtime_identity()
    finally:
        store.close()


def test_legacy_runtime_row_remains_unattributed_and_is_not_backfilled(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        store.put_metadata("sessions", "legacy-session", {
            "schema_version": 1,
            "session_id": "legacy-session",
            "worker_identity": {"worker_instance_id": "legacy-worker"},
        })
        lifecycle = SessionLifecycleStore(store)
        assert lifecycle.get("project-a", "legacy-session") is None
        assert lifecycle.list_for_project("project-a") == []
        assert "lifecycle" not in store.get_metadata("sessions", "legacy-session")
    finally:
        store.close()


def test_lifecycle_project_filter_and_foreign_identity_refusal(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        lifecycle = SessionLifecycleStore(store)
        lifecycle.save(_lifecycle(project_id="project-a", session_id="session-a"))
        lifecycle.save(_lifecycle(project_id="project-b", session_id="session-b"))
        assert [row["session_id"] for row in lifecycle.list_for_project("project-a")] == ["session-a"]
        assert lifecycle.get("project-a", "session-a")["project_id"] == "project-a"
        with pytest.raises(SessionLifecycleProjectConflict):
            lifecycle.get("project-a", "session-b")
        with pytest.raises(SessionLifecycleProjectConflict):
            lifecycle.save(_lifecycle(project_id="project-a", session_id="session-b"), expected_revision=1)
    finally:
        store.close()


def test_lifecycle_revision_conflict_and_invalid_identity_fail_closed(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        lifecycle = SessionLifecycleStore(store)
        first = lifecycle.save(_lifecycle())
        with pytest.raises(SessionLifecycleRevisionConflict):
            lifecycle.save(_lifecycle(state="DISCONNECTED", client_state="DISCONNECTED"), expected_revision=4)
        assert lifecycle.get("project-a", "session-a") == first

        with pytest.raises(SessionLifecycleMalformed, match="unsupported fields"):
            validate_lifecycle_record({**_lifecycle(), "credentials_ref": "must-not-persist"})
        with pytest.raises(SessionLifecycleMalformed, match="managed server requires exact process"):
            lifecycle.save(_lifecycle(
                state="CONNECTED", server_state="MCP_MANAGED", server_ownership="mcp_managed",
            ))
    finally:
        store.close()


def test_lifecycle_schema_corruption_does_not_become_a_false_project_listing(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    try:
        store.put_metadata("sessions", "broken", {"session_id": "broken", "lifecycle": {"project_id": "project-a"}})
        lifecycle = SessionLifecycleStore(store)
        with pytest.raises(SessionLifecycleMalformed):
            lifecycle.list_for_project("project-a")
    finally:
        store.close()
