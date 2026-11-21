"""T35 regression: a failing post-COMMIT readback inside cancel_queued must
keep its own exception visible to the caller, not be replaced by a secondary
ROLLBACK error on an already-ended transaction.

Scope and injection boundary
----------------------------
The failure is synthetic and confined to one brand-new ``:memory:`` ledger:
the store instance attribute ``store.job`` is replaced with a sentinel wrapper,
so the first ``self.job(...)`` executed by ``cancel_queued`` after its ``COMMIT``
raises ``PostCommitReadFailure``.  Before raising, the wrapper records, through
the saved original ``store.job`` callable, that both rows and the CANCELLED
event really committed and that no transaction is still open.  No real or
historical job, model, daemon, Worker, SSH, native process, UNKNOWN state or
file-backed database is touched, and no pre-existing record is mutated.

Expected intent vs current handler
----------------------------------
The current ``cancel_queued`` handler rolls back unconditionally after its
``COMMIT`` has already closed the transaction, so a post-commit readback
failure would surface as a secondary ``sqlite3.OperationalError``
("cannot rollback - no transaction is active") with ``__cause__`` left None.
The chain assertions below encode the required behaviour (sentinel preserved
by identity, either as the raised exception or in the explicit ``__cause__``
chain).  This file exposes that masking; it was authored without executing it,
so it must not be cited as an observed product RED.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from comsol_mcp._operation_store import OperationStore


class PostCommitReadFailure(RuntimeError):
    """Synthetic sentinel raised only by the injected wrapper below."""


def _new_queued_job(store: OperationStore, tag: str) -> dict:
    record, reused = store.begin(
        request_id=f"t35-cancel-postcommit-request-{tag}",
        idempotency_key=f"t35-cancel-postcommit-key-{tag}",
        request_hash=f"t35-cancel-postcommit-hash-{tag}",
        operation="study.run",
        metadata={"execution": {"project_id": "t35-cancel-postcommit-project"}},
    )
    assert reused is False
    return record


def _pair_status(read_job, job_id: str) -> tuple[str, str]:
    job = read_job(job_id)
    assert job is not None and job["operation"] is not None
    return job["status"], job["operation"]["status"]


def _explicit_cause_chain(exc: BaseException | None) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__
    return chain


def test_cancel_queued_post_commit_read_failure_keeps_original_exception_chain() -> None:
    store = OperationStore(":memory:")
    try:
        record = _new_queued_job(store, "injected")
        job_id = record["job_id"]

        # Known control baseline: a fresh QUEUED pair and no events at all.
        read_job = store.job  # saved original; every post-injection read uses it
        assert _pair_status(read_job, job_id) == ("QUEUED", "QUEUED")
        assert store.events(job_id, 0, 100) == []
        assert store.db.in_transaction is False

        captured: dict = {"calls": 0}

        def failing_job(job_id: str) -> dict[str, Any] | None:
            captured["calls"] += 1
            captured["in_transaction_at_failure"] = store.db.in_transaction
            captured["committed_job"] = read_job(job_id)
            captured["committed_events"] = [
                event["event"] for event in store.events(job_id, 0, 100)
            ]
            exc = PostCommitReadFailure(
                f"synthetic post-commit readback failure for {job_id}"
            )
            captured["sentinel"] = exc
            raise exc

        # Instance attribute shadows the bound method, so only this store's
        # cancel_queued post-commit self.job(...) read hits the sentinel.
        store.job = failing_job

        with pytest.raises(Exception) as excinfo:
            store.cancel_queued(job_id, reason="synthetic post-commit failure")

        # The sentinel fired exactly once, and only after COMMIT had already
        # closed the transaction.
        assert captured["calls"] == 1
        assert captured["in_transaction_at_failure"] is False

        # The committed effect really landed, read back with the saved
        # original job callable before the sentinel raised.
        committed = captured["committed_job"]
        assert committed["status"] == "CANCELLED"
        assert committed["operation"]["status"] == "CANCELLED"
        assert committed["operation"]["result"]["error"]["code"] == "OPERATION_CANCELLED"
        assert captured["committed_events"] == ["CANCELLED"]

        raised = excinfo.value
        # The caller must not be handed a bare no-active-transaction ROLLBACK
        # error in place of the original failure.
        assert not (
            isinstance(raised, sqlite3.OperationalError) and raised.__cause__ is None
        ), (
            "post-commit failure masked by a secondary ROLLBACK error: "
            f"{type(raised).__name__}: {raised}"
        )
        # The original failure type or an explicit __cause__ chain to it must
        # survive.
        assert any(
            link is captured["sentinel"] for link in _explicit_cause_chain(raised)
        ), (
            f"sentinel not preserved by identity in the explicit __cause__ "
            f"chain of {type(raised).__name__}"
        )

        # The committed CANCELLED pair/event and the ended transaction survive
        # the failed readback.
        assert _pair_status(read_job, job_id) == ("CANCELLED", "CANCELLED")
        assert [event["event"] for event in store.events(job_id, 0, 100)] == ["CANCELLED"]
        assert store.db.in_transaction is False
    finally:
        store.close()


def test_cancel_queued_post_commit_success_is_clean_control() -> None:
    store = OperationStore(":memory:")
    try:
        record = _new_queued_job(store, "control")
        job_id = record["job_id"]
        read_job = store.job
        assert _pair_status(read_job, job_id) == ("QUEUED", "QUEUED")
        assert store.events(job_id, 0, 100) == []

        accepted, code, updated = store.cancel_queued(job_id, reason="synthetic control")
        assert accepted is True and code == "CANCELLED"
        assert updated is not None and updated["status"] == "CANCELLED"
        assert _pair_status(read_job, job_id) == ("CANCELLED", "CANCELLED")
        assert [event["event"] for event in store.events(job_id, 0, 100)] == ["CANCELLED"]
        assert store.db.in_transaction is False
    finally:
        store.close()
