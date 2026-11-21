"""T35 single-connection in-memory tests for ``solver_log_snapshot``
transaction ownership and caller-exception folding (authored, NOT RUN).

Scope and honesty boundaries (binding for every test in this file):

- Real layers only: brand-new synthetic ``begin``/``add_event``/``finish``
  state on fresh ``OperationStore(":memory:")`` ledgers, driven through the
  real ``solver_log_snapshot`` / ``fold_job_event_prefix`` /
  ``project_operation_result_error`` primitives on each store's own single
  SQLite connection. No daemon, ControlDaemon, Worker, COMSOL, SSH, model,
  endpoint, credential, historical/prior DB, or real UNKNOWN job is touched,
  and nothing here uses tmp_path fixtures, disk files, threads, subprocesses,
  network, or deletes.
- These are internal store-consistency primitives: nothing here is, or is
  evidence for, authentication, real project authorization, principal
  identity, WAL behaviour, durability across processes, or concurrency.
  The ``journal_mode=WAL`` pragma the store issues on ``":memory:"`` is not
  asserted or relied upon anywhere below.
- The exact refusal substrings asserted here were read from the currently
  published ``_operation_store.py`` source at authoring time; only the
  public method surface is used, and no line numbers or source hashes are
  embedded. The durable job-event append surface on this store is
  ``add_event`` (there is no separately named ``append_event`` method).
- Each test opens exactly one fresh ``":memory:"`` store, closes only its
  own store in ``finally``, and asserts no transaction is left open.
- This file was authored and never executed; nothing below may be cited as
  a test run, a passed result, or a fix confirmation.
"""
from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._operation_store import OperationStore

SESSION = "t35-txn-session"
PROJECT = "t35-txn-project"
EVENT_COUNT = 3

# The durable stored result is deliberately tiny: this file is about
# transaction ownership and exception folding, not payload capacity (the
# sibling T35 capacity/size files cover byte and memory budgets).
STORED_RESULT = {
    "success": False,
    "note": "t35-txn-stored-result",
    "error": {
        "code": "T35_TXN_SYNTHETIC",
        "message": "t35 txn synthetic failure",
        "safe_retry": False,
    },
}


class _T35SnapshotSentinelError(RuntimeError):
    """Distinct caller-raised sentinel used to pin exception folding."""


def _seed_job(store: OperationStore, tag: str) -> dict:
    """Fresh synthetic job/event/result state through the real ledger APIs."""
    record, reused = store.begin(
        request_id=f"t35-txn-req-{tag}",
        idempotency_key=f"t35-txn-key-{tag}",
        request_hash=f"t35-txn-hash-{tag}",
        operation="study.run",
        metadata={
            "operation": "study.run",
            "arguments": {},
            "execution": {"project_id": PROJECT, "session_id": SESSION},
        },
    )
    assert reused is False and record["job_id"] and record["operation_id"]
    for index in range(EVENT_COUNT):
        store.add_event(record["job_id"], f"T35TxnEvent{index}", {"index": index})
    accepted, authoritative = store.finish(
        record["operation_id"], status="FAILED", result=STORED_RESULT
    )
    assert accepted is True and authoritative == "FAILED"
    return record


def _fold(store: OperationStore, job_id: str, *, join: bool,
          visitor=None, snapshot: tuple[int, int] | None = None):
    """One fold call in either join or default self-owned mode."""
    count, last = snapshot if snapshot is not None else (None, None)
    return store.fold_job_event_prefix(
        job_id,
        snapshot_count=count,
        snapshot_last_id=last,
        max_row_bytes=4096,
        visitor=visitor if visitor is not None else (lambda row: None),
        _join_read_snapshot=join,
    )


def test_t35_snapshot_owns_transaction_and_join_reads_share_it():
    """Normal context: the yielded view is a result-free metadata view
    (both ``job['result']`` and the operation view's ``result`` are
    ``None``), a ``_join_read_snapshot`` fold and
    ``project_operation_result_error`` both see the active owned
    transaction and leave it open, and only normal context exit closes it.
    """
    store = OperationStore(":memory:")
    try:
        record = _seed_job(store, "owner")
        job_id, operation_id = record["job_id"], record["operation_id"]
        assert store.db.in_transaction is False

        folded: list[dict] = []
        with store.solver_log_snapshot(job_id) as view:
            assert store.db.in_transaction is True
            assert view is not None
            assert view["job_id"] == job_id and view["status"] == "FAILED"
            assert view["metadata"] == {
                "operation": "study.run",
                "arguments": {},
                "execution": {"project_id": PROJECT, "session_id": SESSION},
            }
            assert isinstance(view["effective_timeouts"], dict)
            # The stored result is never fetched into the view.
            assert view["result"] is None
            operation_view = view["operation"]
            assert operation_view is not None
            assert operation_view["operation_id"] == operation_id
            assert operation_view["status"] == "FAILED"
            assert operation_view["result"] is None

            fold_result = _fold(store, job_id, join=True, visitor=folded.append)
            # A joined fold requires and observes the open owner snapshot,
            # and issues no COMMIT/ROLLBACK of its own.
            assert store.db.in_transaction is True
            assert fold_result["complete"] is True
            assert fold_result["status"] == "COMPLETE"
            assert fold_result["expected_event_count"] == EVENT_COUNT
            assert [row["event"] for row in folded] == [
                f"T35TxnEvent{index}" for index in range(EVENT_COUNT)
            ]

            projected = store.project_operation_result_error(
                operation_id, max_error_bytes=8192
            )
            assert store.db.in_transaction is True
            assert projected == {"error": STORED_RESULT["error"]}

        # Normal exit concludes this context's own transaction only.
        assert store.db.in_transaction is False
        # Connection stays usable and the durable ledger is unchanged.
        assert len(store.events(job_id)) == EVENT_COUNT
    finally:
        store.close()


def test_t35_snapshot_refuses_pre_existing_caller_transaction_untouched():
    """A caller-owned BEGIN is refused rather than adopted: the context
    raises before its own BEGIN and neither commits nor rolls back the
    caller's transaction — the still-active caller transaction and its
    uncommitted marker survive, and only the caller concludes it.
    """
    store = OperationStore(":memory:")
    try:
        record = _seed_job(store, "refuse")
        job_id = record["job_id"]
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute(
            "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
            (job_id, "T35TxnCallerMarker", "{}"),
        )
        assert store.db.in_transaction is True

        with pytest.raises(ValueError) as refused:
            with store.solver_log_snapshot(job_id):
                pytest.fail(
                    "solver_log_snapshot must not enter inside a foreign transaction"
                )
        assert "pre-existing transaction" in str(refused.value)

        # Not committed by the snapshot: the caller transaction is still
        # the active one, so the caller's own COMMIT below succeeds.
        assert store.db.in_transaction is True
        # Not rolled back by the snapshot: the uncommitted marker is still
        # visible on this same connection.
        marker = store.db.execute(
            "SELECT id FROM job_events "
            "WHERE job_id=? AND event='T35TxnCallerMarker'",
            (job_id,),
        ).fetchone()
        assert marker is not None

        store.db.execute("COMMIT")
        assert store.db.in_transaction is False
        committed = store.db.execute(
            "SELECT id FROM job_events "
            "WHERE job_id=? AND event='T35TxnCallerMarker'",
            (job_id,),
        ).fetchone()
        assert committed is not None and committed["id"] == marker["id"]
    finally:
        store.close()


def test_t35_join_fold_and_projection_refuse_without_active_transaction():
    """Detached from any transaction, ``_join_read_snapshot=True`` and
    ``project_operation_result_error`` fail closed before any read: the
    visitor is never called, no transaction is opened, and the connection
    is left clean.
    """
    store = OperationStore(":memory:")
    try:
        record = _seed_job(store, "detached")
        job_id, operation_id = record["job_id"], record["operation_id"]
        assert store.db.in_transaction is False

        visited: list[dict] = []
        with pytest.raises(ValueError) as fold_refused:
            _fold(store, job_id, join=True, visitor=visited.append)
        assert "requires an active caller-owned transaction" in str(fold_refused.value)
        assert visited == []
        assert store.db.in_transaction is False

        with pytest.raises(ValueError) as projection_refused:
            store.project_operation_result_error(operation_id, max_error_bytes=8192)
        assert "requires the active read transaction" in str(projection_refused.value)
        assert store.db.in_transaction is False
    finally:
        store.close()


def test_t35_caller_sentinel_inside_snapshot_reraises_and_closes_transaction():
    """An explicit caller exception raised inside the owned snapshot is
    re-raised unchanged (same object, type and message) and the still-open
    owned transaction is rolled back, leaving the connection usable.
    """
    store = OperationStore(":memory:")
    try:
        record = _seed_job(store, "sentinel")
        job_id = record["job_id"]
        sentinel = _T35SnapshotSentinelError("t35-snapshot-sentinel")

        with pytest.raises(_T35SnapshotSentinelError) as raised:
            with store.solver_log_snapshot(job_id) as view:
                assert view is not None
                assert store.db.in_transaction is True
                raise sentinel

        assert raised.value is sentinel
        assert str(raised.value) == "t35-snapshot-sentinel"
        assert store.db.in_transaction is False
        # Same connection and ledger; the next read works normally.
        assert store.job(job_id)["status"] == "FAILED"
    finally:
        store.close()


def test_t35_missing_job_yields_none_and_still_closes_owned_transaction():
    """A missing job still gets one consistent owned snapshot: the context
    yields ``None`` with the transaction open, and both normal exit and a
    caller raise inside the missing-job context close the owned
    transaction.
    """
    store = OperationStore(":memory:")
    try:
        record = _seed_job(store, "missing")
        absent_job_id = "t35-txn-absent-" + record["job_id"]

        with store.solver_log_snapshot(absent_job_id) as view:
            assert view is None
            assert store.db.in_transaction is True
        assert store.db.in_transaction is False

        sentinel = _T35SnapshotSentinelError("t35-missing-job-sentinel")
        with pytest.raises(_T35SnapshotSentinelError):
            with store.solver_log_snapshot(absent_job_id) as view:
                assert view is None
                assert store.db.in_transaction is True
                raise sentinel
        assert store.db.in_transaction is False
        # The seeded job is untouched by either missing-job snapshot.
        assert store.job(record["job_id"])["status"] == "FAILED"
    finally:
        store.close()


def test_t35_default_self_owned_fold_positive_control_and_refold_matches():
    """GREEN control: the default (non-joined) fold owns and closes its own
    read snapshot, visits the whole fresh prefix in id order, and a
    continuation re-fold of the frozen count/last-id pair reproduces the
    exact published digests.
    """
    store = OperationStore(":memory:")
    try:
        record = _seed_job(store, "fold-ctl")
        job_id = record["job_id"]

        folded: list[dict] = []
        first = _fold(store, job_id, join=False, visitor=folded.append)
        assert first["complete"] is True and first["status"] == "COMPLETE"
        assert first["expected_event_count"] == first["fetched_event_count"] == EVENT_COUNT
        assert [row["event"] for row in folded] == [
            f"T35TxnEvent{index}" for index in range(EVENT_COUNT)
        ]
        assert [row["id"] for row in folded] == sorted(row["id"] for row in folded)
        # The self-owned transaction is closed again after the call.
        assert store.db.in_transaction is False

        refolded: list[dict] = []
        second = _fold(
            store, job_id, join=False, visitor=refolded.append,
            snapshot=(first["expected_event_count"], first["snapshot_last_id"]),
        )
        assert second["complete"] is True
        assert second["id_sha256"] is not None and second["events_sha256"] is not None
        assert (second["id_sha256"], second["events_sha256"]) == (
            first["id_sha256"], first["events_sha256"]
        )
        assert [row["id"] for row in refolded] == [row["id"] for row in folded]
        assert store.db.in_transaction is False
    finally:
        store.close()
