"""T35 — two-row visible-effect fault/rollback and positive-control tests for
``OperationStore.record_cancel_intent``.

These tests call the REAL ``OperationStore.record_cancel_intent`` method
against a fresh ``OperationStore(':memory:')`` ledger.  They are durable-
consistency tests of one store primitive only: no claim of authentication,
public MCP behaviour, daemon wiring, native cancellation, project authority,
real concurrent writers, or on-disk durability appears anywhere, and this
file contains NO authority object.

Why a WITNESS trigger instead of the sibling's blanket fault trigger
(tests/test_t35_record_cancel_intent_atomic_20261113.py): the method's
guarded UPDATEs check ``rowcount == 1``, which counts WHERE-matched rows,
NOT rows whose values actually changed.  ``update_job`` already copies
``jobs.started_at`` into ``operations.started_at`` on every transition, so
in the sibling fixture the operations UPDATE of ``record_cancel_intent`` is
a VALUE no-op (started_at/finished_at already equal) even though it matches
one row — its rollback assertions therefore cannot witness that the
operations UPDATE truly took effect.  Here the fixture deliberately breaks
that sync: after seeding, the associated operation row's ``started_at`` is
replaced with a task sentinel DIFFERENT from ``jobs.started_at`` (asserted
different BEFORE the call).  The task-owned ``BEFORE INSERT ON job_events``
trigger then reads the CURRENT state at the exact instant of the method's
own ``CancelRequested`` INSERT — strictly after both UPDATEs inside the same
``BEGIN IMMEDIATE`` transaction — via ``SELECT CASE WHEN``:

- if the job metadata already has ``cancel_requested`` true AND the
  associated operation's ``started_at`` already equals the job's
  ``started_at``, the two UPDATEs demonstrably LANDED WITH EFFECT, and the
  trigger raises ``ABORT 'T35_BOTH_UPDATES_WITNESSED'``;
- otherwise it raises ``ABORT 'T35_UPDATES_NOT_WITNESSED'`` instead.

The fault test asserts the caught message is exactly the FIRST sentinel —
so a future implementation whose operations UPDATE degenerates into a
no-op, or whose jobs UPDATE fails to merge metadata, fails loudly rather
than passing on a hollow rowcount.  It then reads back the job row, the
original operation row, and the complete event log, byte-identical to the
pre-call snapshot: the rollback must restore the different original
operation ``started_at`` sentinel (still unequal to the job's), append no
event, and leave ``in_transaction`` False.  A separate no-trigger positive
control then proves the SAME desynced pair genuinely converges: the
operation's different ``started_at`` is really synchronized to the job's,
the merged job metadata and the single appended CancelRequested event land,
and the durable ``operations.result`` is preserved.

Static basis (read-only, frozen):
- ``_state/t35-log-overflow-parent-20261112/store_cancel_intent_candidate_b6183204.py.txt``
  lines 2488-2628 (``record_cancel_intent``: binding before any write,
  two guarded UPDATEs with the pre-read status, event INSERT only after
  both matched one row, COMMIT last, ROLLBACK-and-reraise wrapper), and
  lines 2005-2065 (``update_job`` syncs ``operations.started_at``/
  ``finished_at`` from the jobs row — the reason the sibling fixture's
  operations UPDATE is a value no-op).
- ``comsol_mcp/_job_project_scope.py`` — binding reads only the metadata
  top-level ``project_id`` and ``execution.project_id`` claims.
- ``tests/test_t35_record_cancel_intent_atomic_20261113.py`` — the sibling
  whose ``_seed_pair``/``_raw_snapshot`` fixture style this file reuses in
  task-local form; nothing from it is rerun, reproduced, or bypassed.

Scope and honesty limits:
- Each test owns a fresh ``OperationStore(':memory:')`` and seeds its own
  QUEUED→RUNNING pair through the real ``begin``/``update_job`` paths plus
  task-local marker events and a durable operations result.  The single
  fixture desync is a direct single-row ``UPDATE operations SET started_at``
  on the same ':memory:' connection using a task-authored string.  No
  historical job, on-disk database, Worker, process, thread, SSH, endpoint,
  credential, model, COMSOL, or network resource is touched; no
  UNKNOWN/RECONCILING state appears.  ':memory:' means nothing about disk
  durability, and the single-connection BEGIN IMMEDIATE harness makes no
  real-concurrency claim.
- Every byte passed to the database (tags, markers, project ID, reason,
  started_at sentinel, trigger name/messages) is a string authored here.
- Rollback read-back is byte-level on the raw rows and the full ordered
  job_events log, plus a closed-transaction check.
- Author-tooling deviation, recorded honestly: this file was authored by
  the delegated sub-agent via a single write_file only.  Per the work
  package it was NOT executed (no pytest, terminal, or code execution was
  permitted), and the live ``comsol_mcp/_operation_store.py`` was not read
  (owned by another agent); the method contract is taken from the frozen
  b6183204 candidate named above.  If the live store lacks the method or
  the RAISE(ABORT)-in-trigger surfaces a different exception class than the
  sibling harness already pins (sqlite3.IntegrityError), these tests fail
  loudly rather than silently passing.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest  # pyright: ignore[reportMissingImports]

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._operation_store import OperationStore

PROJECT_A = "t35rw-proj-A-20261113"

# Task sentinel written ONLY into operations.started_at by the fixture, to
# break update_job's started_at sync.  Chosen structurally unlike a
# CURRENT_TIMESTAMP ("YYYY-MM-DD HH:MM:SS") value so inequality is certain.
OP_STARTED_SENTINEL = "T35RW-SENT-OP-STARTED-19710101T000000Z"

# Task-owned witness trigger.  Fires only on the method's own
# CancelRequested event INSERT, i.e. strictly after both guarded UPDATEs in
# the same BEGIN IMMEDIATE transaction, and inspects the ledger AS IT IS AT
# THAT INSTANT: (1) jobs.metadata.cancel_requested already true, and (2)
# the associated operations.started_at already equal to jobs.started_at.
# CASE arm one proves both UPDATEs landed with real effect; CASE arm two
# catches the hollow-rowcount/no-op degeneration.
_WITNESS_TRIGGER = (
    "CREATE TRIGGER t35rw_both_updates_witness "
    "BEFORE INSERT ON job_events "
    "FOR EACH ROW WHEN NEW.event = 'CancelRequested' "
    "BEGIN "
    "SELECT CASE WHEN "
    "(SELECT json_extract(j.metadata, '$.cancel_requested') "
    " FROM jobs j WHERE j.job_id = NEW.job_id) = 1 "
    "AND "
    "(SELECT o.started_at FROM operations o "
    " WHERE o.operation_id = "
    "(SELECT j2.operation_id FROM jobs j2 WHERE j2.job_id = NEW.job_id)) "
    "= "
    "(SELECT j3.started_at FROM jobs j3 WHERE j3.job_id = NEW.job_id) "
    "THEN RAISE(ABORT, 'T35_BOTH_UPDATES_WITNESSED') "
    "ELSE RAISE(ABORT, 'T35_UPDATES_NOT_WITNESSED') "
    "END; "
    "END"
)


@pytest.fixture
def owned_store():
    store = OperationStore(":memory:")
    try:
        yield store
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Self-contained seed helpers (task-local bytes only), mirroring the
# sibling file's _seed_pair/_raw_snapshot style under T35RW markers.
# ---------------------------------------------------------------------------

def _seed_pair(
    store: OperationStore,
    *,
    tag: str,
) -> tuple[str, str, dict[str, Any]]:
    """Fresh QUEUED→RUNNING pair with task-local events and a durable
    operations result, through the real begin/update_job paths.  ``begin``
    writes the SAME metadata to both rows, so the project binding is
    unambiguously present on both sides.

    Returns (job_id, operation_id, seed_metadata).
    """
    metadata: dict[str, Any] = {
        "t35rw_synthetic": True,
        "content_marker": f"T35RW-SENT-CONTENT-{tag}",
        "execution": {"project_id": PROJECT_A},
    }
    record, reused = store.begin(
        request_id=f"t35rw-req-{tag}",
        idempotency_key=f"t35rw-idem-{tag}",
        request_hash="1" * 64,
        operation="job.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False
    job_id = record["job_id"]
    operation_id = record["operation_id"]
    assert isinstance(job_id, str) and job_id and isinstance(operation_id, str)

    store.update_job(job_id, "RUNNING")
    for marker in ("e1", "e2"):
        store.add_event(
            job_id, "T35RWFixtureEvent", {"marker": f"T35RW-SENT-EVT-{tag}-{marker}"}
        )
    store.update_job(
        job_id,
        "RUNNING",
        result={"success": True, "data": {"marker": f"T35RW-SENT-RESULT-{tag}"}},
    )

    settled = store.job(job_id)
    assert settled is not None and settled["status"] == "RUNNING"
    return job_id, operation_id, metadata


def _desync_operation_started_at(
    store: OperationStore, operation_id: str, sentinel: str
) -> None:
    """Fixture-only single-row write breaking update_job's started_at sync."""
    store.db.execute(
        "UPDATE operations SET started_at=? WHERE operation_id=?",
        (sentinel, operation_id),
    )


def _raw_snapshot(store: OperationStore, job_id: str) -> dict[str, Any]:
    """Byte-level read-back of the complete relevant ledger: jobs row, its
    associated operations row (if any), and every job_events row."""
    job_row = store.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    assert job_row is not None
    operation_row = store.db.execute(
        "SELECT * FROM operations WHERE operation_id=?", (job_row["operation_id"],)
    ).fetchone()
    event_rows = store.db.execute(
        "SELECT * FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
    ).fetchall()
    return {
        "job": dict(job_row),
        "operation": dict(operation_row) if operation_row is not None else None,
        "events": [dict(row) for row in event_rows],
    }


def _seed_desynced_pair(
    store: OperationStore, *, tag: str
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Seed a RUNNING pair, desync the operation started_at to the task
    sentinel, install nothing, and snapshot.  Asserts up front that the
    original values really ARE different — the precondition every later
    witness/rollback/convergence assertion depends on."""
    job_id, operation_id, seed_metadata = _seed_pair(store, tag=tag)
    _desync_operation_started_at(store, operation_id, OP_STARTED_SENTINEL)
    before = _raw_snapshot(store, job_id)
    assert before["operation"] is not None
    assert before["operation"]["started_at"] == OP_STARTED_SENTINEL
    assert before["job"]["started_at"] is not None
    assert before["job"]["started_at"] != OP_STARTED_SENTINEL, (
        "fixture desync must make the original started_at values differ"
    )
    return job_id, operation_id, seed_metadata, before


# ---------------------------------------------------------------------------
# Fault + witness: RAISE(ABORT) on the CancelRequested INSERT must fire on
# the WITNESSED arm — both UPDATEs demonstrably landed with effect at that
# instant — and the store's rollback must undo both rows and the event log
# completely, restoring the different original operation started_at.
# ---------------------------------------------------------------------------

def test_both_updates_witnessed_then_fully_rolled_back(owned_store) -> None:
    tag = "WITNESS-FAULT"
    job_id, _operation_id, _seed_metadata, before = _seed_desynced_pair(
        owned_store, tag=tag
    )
    owned_store.db.execute(_WITNESS_TRIGGER)
    assert owned_store.db.in_transaction is False, (
        "trigger creation must not leave a transaction open before the call"
    )

    with pytest.raises(sqlite3.IntegrityError) as caught:
        owned_store.record_cancel_intent(
            job_id,
            project_id=PROJECT_A,
            expected_status="RUNNING",
            reason=f"T35RW-SENT-REASON-{tag}",
        )
    message = str(caught.value)
    # Exactly the WITNESSED sentinel: at the event INSERT instant the job
    # metadata already carried cancel_requested=true AND the operation row
    # had already moved from the different sentinel to the job's
    # started_at — both UPDATEs achieved real value changes, not a
    # rowcount-only no-op.  The NOT_WITNESSED arm must not have fired.
    assert "T35_BOTH_UPDATES_WITNESSED" in message, (
        "the witness trigger must abort on the both-updates-landed arm: "
        f"got {message!r}"
    )
    assert "T35_UPDATES_NOT_WITNESSED" not in message, (
        "a NOT_WITNESSED abort means an UPDATE did not take effect: "
        f"got {message!r}"
    )
    assert owned_store.db.in_transaction is False, (
        "the rollback wrapper must close the transaction after the injected fault"
    )

    after = _raw_snapshot(owned_store, job_id)
    # Byte-identical read-back of job row, original operation row, and the
    # complete ordered event log proves the rollback undid BOTH updated
    # rows and that no CancelRequested event survived.
    assert after == before, (
        "witness fault must roll back both updated rows and the event log"
    )
    # The rollback restored the ORIGINAL DIFFERENT sentinel — the operation
    # row is back out of sync with the job, exactly as before the call.
    assert after["operation"]["started_at"] == OP_STARTED_SENTINEL
    assert after["operation"]["started_at"] != after["job"]["started_at"]
    assert "cancel_requested" not in (after["job"]["metadata"] or "")
    assert not [row for row in after["events"] if row["event"] == "CancelRequested"]
    assert [row["id"] for row in after["events"]] == [
        row["id"] for row in before["events"]
    ]
    assert after["operation"]["result"] == before["operation"]["result"]


# ---------------------------------------------------------------------------
# Positive control, identical desynced fixture but NO trigger at all: the
# original different operation started_at must really be synchronized to
# the job's, the merged job metadata and the single appended
# CancelRequested event must land, and the durable operations result plus
# the job's own started_at/finished_at must be preserved.
# ---------------------------------------------------------------------------

def test_no_trigger_control_converges_started_at_and_appends_event(owned_store) -> None:
    tag = "WITNESS-CTRL"
    job_id, _operation_id, seed_metadata, before = _seed_desynced_pair(
        owned_store, tag=tag
    )

    result = owned_store.record_cancel_intent(
        job_id,
        project_id=PROJECT_A,
        expected_status="RUNNING",
        reason=f"T35RW-SENT-REASON-{tag}",
    )
    assert isinstance(result, tuple) and len(result) == 2
    ok, code = result
    assert isinstance(ok, bool) and isinstance(code, str)
    assert (ok, code) == (True, "INTENT_RECORDED")
    assert owned_store.db.in_transaction is False, (
        "transaction left open after commit"
    )

    after = _raw_snapshot(owned_store, job_id)
    # The operations row's originally-different started_at is genuinely
    # synchronized to the job row's, and finished_at agrees too (both NULL
    # while RUNNING).  This is the positive effect the witness trigger
    # asserted the fault test rolled back.
    assert after["operation"]["started_at"] == after["job"]["started_at"]
    assert after["operation"]["started_at"] != OP_STARTED_SENTINEL, (
        "the sentinel started_at must have been replaced by the job's value"
    )
    assert after["operation"]["finished_at"] == after["job"]["finished_at"]
    changed_operation_columns = {
        column
        for column in after["operation"]
        if after["operation"][column] != before["operation"][column]
    }
    assert changed_operation_columns <= {"started_at", "finished_at"}, (
        "only the synced timestamps may change on the operations row; in "
        f"particular result changed: {changed_operation_columns!r}"
    )
    assert (
        after["operation"]["result"] == before["operation"]["result"]
    ), "the intent record must never touch the durable operations result"

    # The jobs row changes ONLY its metadata column (status unchanged;
    # started_at preserved by the RUNNING CASE/COALESCE branch).
    assert {k: v for k, v in after["job"].items() if k != "metadata"} == {
        k: v for k, v in before["job"].items() if k != "metadata"
    }
    merged = json.loads(after["job"]["metadata"])
    assert merged == {
        **seed_metadata,
        "cancel_requested": True,
        "cancel_reason": f"T35RW-SENT-REASON-{tag}",
    }

    # Exactly one new event; the seeded prefix is byte-identical.
    assert len(after["events"]) == len(before["events"]) + 1
    assert after["events"][: len(before["events"])] == before["events"]
    new_event = after["events"][-1]
    assert new_event["job_id"] == job_id
    assert new_event["event"] == "CancelRequested"
    event_metadata = json.loads(new_event["metadata"])
    assert set(event_metadata) == {"reason", "mode", "cancel_accepted", "engine_stopped", "at"}
    assert event_metadata["reason"] == f"T35RW-SENT-REASON-{tag}"
    assert event_metadata["mode"] == "UNSUPPORTED_NATIVE_CANCEL"
    assert event_metadata["cancel_accepted"] is True
    assert event_metadata["engine_stopped"] is False
    assert isinstance(event_metadata["at"], (int, float))
    cancel_events = [row for row in after["events"] if row["event"] == "CancelRequested"]
    assert len(cancel_events) == 1, "exactly one CancelRequested intent must exist"
