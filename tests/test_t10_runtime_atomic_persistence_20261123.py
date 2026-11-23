"""T10: real-store runtime persistence must not partially publish a snapshot.

All data, databases and project roots are synthetic and task-owned. No COMSOL,
Worker, historical state or previously refused identity probes are involved.
"""
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore


@pytest.fixture
def runtime_backend(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    ledger = SessionLedger("atomic-session", "atomic-server")
    ref = ledger.bind_model("atomic-model", fingerprint="before")
    backend = ManagedBackend(tmp_path / "control", store,
                             service=SimpleNamespace(ledger=ledger),
                             registry={}, project_root=tmp_path)
    backend.worker_identity = {
        "runtime_id": "atomic-runtime", "worker_instance_id": "atomic-worker",
        "connection_epoch": 1, "server_instance_id": "atomic-server",
    }
    backend.persist()
    try:
        yield store, ledger, ref, backend
    finally:
        backend.close()
        store.close()


def test_runtime_snapshot_updates_all_rows_on_success(runtime_backend):
    store, ledger, ref, backend = runtime_backend
    ledger._models[ref.model_tag].fingerprint = "after"
    backend.persist()
    key = backend._model_project_key(ref.as_dict())
    assert store.get_metadata("sessions", ledger.session_id)["models"][ref.model_tag]["fingerprint"] == "after"
    assert store.get_metadata("runtimes", "atomic-runtime")["models"][ref.model_tag]["fingerprint"] == "after"
    assert store.get_metadata("revisions", key)["fingerprint"] == "after"


def test_runtime_snapshot_rolls_back_when_runtime_row_write_fails(runtime_backend):
    store, ledger, ref, backend = runtime_backend
    key = backend._model_project_key(ref.as_dict())
    before = [store.get_metadata(table, ident) for table, ident in (
        ("sessions", ledger.session_id), ("runtimes", "atomic-runtime"), ("revisions", key))]
    ledger._models[ref.model_tag].fingerprint = "after"
    store.db.execute("CREATE TRIGGER fail_runtime_update BEFORE UPDATE ON runtimes "
                     "BEGIN SELECT RAISE(ABORT, 'synthetic runtime failure'); END")
    with pytest.raises(Exception, match="synthetic runtime failure"):
        backend.persist()
    after = [store.get_metadata(table, ident) for table, ident in (
        ("sessions", ledger.session_id), ("runtimes", "atomic-runtime"), ("revisions", key))]
    assert after == before
    assert not store.db.in_transaction


def test_runtime_snapshot_rolls_back_when_revision_write_fails(runtime_backend):
    store, ledger, ref, backend = runtime_backend
    key = backend._model_project_key(ref.as_dict())
    rows = (("sessions", ledger.session_id), ("runtimes", "atomic-runtime"), ("revisions", key))
    before = [store.get_metadata(table, ident) for table, ident in rows]
    ledger._models[ref.model_tag].fingerprint = "after"
    store.db.execute("CREATE TRIGGER fail_revision_update BEFORE UPDATE ON revisions "
                     "BEGIN SELECT RAISE(ABORT, 'synthetic revision failure'); END")
    with pytest.raises(Exception, match="synthetic revision failure"):
        backend.persist()
    assert [store.get_metadata(table, ident) for table, ident in rows] == before
    assert not store.db.in_transaction


def test_runtime_snapshot_keeps_existing_lifecycle_metadata(runtime_backend):
    store, ledger, ref, backend = runtime_backend
    store.merge_metadata_fields("sessions", ledger.session_id, {"lifecycle": {"marker": "preserve"}})
    ledger._models[ref.model_tag].fingerprint = "after"
    backend.persist()
    session = store.get_metadata("sessions", ledger.session_id)
    assert session["lifecycle"] == {"marker": "preserve"}
    assert session["models"][ref.model_tag]["fingerprint"] == "after"
