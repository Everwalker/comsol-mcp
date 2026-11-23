"""Synthetic SQLite interruption after a real BEGIN, without COMSOL or legacy state."""
import pytest  # pyright: ignore[reportMissingImports]

from tests.test_t10_runtime_atomic_persistence_20261123 import runtime_backend  # noqa: F401


class InterruptAfterBegin:
    def __init__(self, database, exception_type):
        self.database = database
        self.exception_type = exception_type
        self.injected = False

    def execute(self, sql, *args, **kwargs):
        result = self.database.execute(sql, *args, **kwargs)
        if sql == "BEGIN IMMEDIATE" and not self.injected:
            self.injected = True
            assert self.database.in_transaction
            raise self.exception_type("injected after SQLite BEGIN succeeded")
        return result

    def __getattr__(self, name):
        return getattr(self.database, name)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_runtime_begin_interrupt_does_not_leave_transaction_open(runtime_backend, exception_type):
    store, ledger, ref, backend = runtime_backend
    key = backend._model_project_key(ref.as_dict())
    rows = (("sessions", ledger.session_id), ("runtimes", "atomic-runtime"), ("revisions", key))
    before = [store.get_metadata(table, ident) for table, ident in rows]
    ledger._models[ref.model_tag].fingerprint = "after"
    database = store.db
    proxy = InterruptAfterBegin(database, exception_type)
    store.db = proxy
    try:
        with pytest.raises(exception_type, match="injected after SQLite BEGIN succeeded"):
            backend.persist()
        assert proxy.injected
        assert not database.in_transaction
        assert [store.get_metadata(table, ident) for table, ident in rows] == before
    finally:
        store.db = database
        if database.in_transaction:
            database.execute("ROLLBACK")  # only this test-owned connection


def test_preexisting_transaction_is_not_rolled_back_by_persist(runtime_backend):
    store, _, _, backend = runtime_backend
    store.db.execute("CREATE TABLE synthetic_owner (marker TEXT)")
    store.db.execute("BEGIN IMMEDIATE")
    store.db.execute("INSERT INTO synthetic_owner VALUES ('owned')")
    try:
        with pytest.raises(RuntimeError, match="existing transaction"):
            backend.persist()
        assert store.db.in_transaction
        assert store.db.execute("SELECT marker FROM synthetic_owner").fetchone()[0] == "owned"
    finally:
        if store.db.in_transaction:
            store.db.execute("ROLLBACK")
