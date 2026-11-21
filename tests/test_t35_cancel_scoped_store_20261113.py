"""T35 — PROPOSED scoped ``cancel_queued`` store-consistency tests (2026-11-13).

Status: PROPOSED-INTERFACE RED.  Every test here exercises an additive
signature that does NOT exist on ``OperationStore`` today::

    store.cancel_queued(job_id, reason='...', *,
                        project_id=..., enforce_project_binding=True)

The method actually shipped in ``comsol_mcp/_operation_store.py`` is
``cancel_queued(self, job_id: str, reason: str = "cancelled by user")`` with no
project kwargs.  The expectation on first execution of this file is therefore
an interface RED, exactly one per proposed call:

* ``TypeError: OperationStore.cancel_queued() got an unexpected keyword
  argument 'project_id'`` — success, cross-project, diverged-sides, and
  non-bool-enforce cases (they name ``project_id`` in the call).
* ``TypeError: ... unexpected keyword argument 'enforce_project_binding'`` —
  the strict missing-scope case (it names only ``enforce_project_binding``).

The rejection controls deliberately do NOT catch ``TypeError``: a missing
interface must surface as TypeError (interface RED) and must never be
laundered into a "reasonable rejection".  A rejected scoped call is accepted
only as ``False``, a ``(False, code, record)`` tuple, or a ``ValueError`` —
no future error class or code name is hardcoded — and each accepted rejection
is then required to leave the durable job row, the raw operation row (the
nested operation side), the event log, and the read-snapshot binding flags
byte-identical, with ``store.db.in_transaction is False``.

Scope and honesty boundaries (do not over-read these tests):

* All fixtures are task-owned fresh ``OperationStore(':memory:')`` databases
  created inside each test; no historical store, database file, daemon,
  worker, or model is touched, and the only mutations to seeded rows are
  task-owned SQL on rows this file itself created.
* These are store-level consistency/visibility assertions inside ONE process
  on ONE thread.  They do NOT prove caller principal identity, authorization,
  cross-process/cross-daemon isolation, or metadata immutability.
* The tested API is an engineering PROPOSAL only.  It is not equivalent to,
  and does not restate, the user-facing cancellation contract.
* The genuine dynamic A→B product counterexample (a live job re-bound while a
  cancel races it) is out of scope for this static in-memory file and was
  recorded separately by the parent review; nothing here substitutes for it.
* Known taxonomy risk (recorded, not worked around): ``list_jobs`` already
  raises ``TypeError("enforce_project_binding must be a boolean")`` for a
  non-bool strict flag.  This file follows the delegation's acceptance set
  (``False``/``ValueError`` only).  If the final scoped-cancel API reuses the
  TypeError precedent, the non-bool case below must be revisited together
  with the RED-detection rule — not silently widened to accept TypeError.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from comsol_mcp._job_project_scope import consistent_project_id
from comsol_mcp._operation_store import OperationStore

PROJ_A = "t35-proj-A-" + uuid4().hex[:8]
PROJ_B = "t35-proj-B-" + uuid4().hex[:8]
CANCEL_REASON = "t35-proposed-scoped-cancel"

_VALUE_ERROR_REJECTION = object()


@pytest.fixture()
def store():
    """Task-owned fresh in-memory ledger; nothing historical is opened."""
    s = OperationStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def _seed_queued_pair(store: OperationStore, metadata: dict) -> tuple[str, str]:
    """Create one QUEUED job/operation pair; both rows carry `metadata`."""
    record, reused = store.begin(
        request_id=f"t35-req-{uuid4()}",
        idempotency_key=f"t35-idem-{uuid4()}",
        request_hash=f"t35-hash-{uuid4()}",
        operation="t35.proposed-scoped-cancel.probe",
        metadata=metadata,
    )
    assert reused is False
    return record["job_id"], record["operation_id"]


def _raw_rows(store: OperationStore, job_id: str, operation_id: str):
    """Exact durable rows (JSON still serialized) — job side + operation side."""
    with store.lock:
        job_row = store.db.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        op_row = store.db.execute(
            "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
    assert job_row is not None and op_row is not None
    return dict(job_row), dict(op_row)


def _binding_flags(store: OperationStore, job_id: str, project_ids: list[str]):
    """Read-only ``project_job_read_snapshot`` verdict per candidate project."""
    flags: dict[str, bool] = {}
    for project_id in project_ids:
        with store.project_job_read_snapshot(job_id, project_id) as bound:
            flags[project_id] = bound
    return flags


def _full_snapshot(store, job_id, operation_id, project_ids):
    """Everything the rejection controls compare: job dict, both raw durable
    rows (the nested/joined operation side included), full event log, and the
    read-snapshot binding flags."""
    with store.lock:
        job = store.job(job_id)
        job_row, op_row = _raw_rows(store, job_id, operation_id)
        events = store.events(job_id, limit=1000)
    return {
        "job": job,
        "job_row": job_row,
        "operation_row": op_row,
        "events": events,
        "bound": _binding_flags(store, job_id, project_ids),
    }


def _assert_rejected_without_write(store, job_id, operation_id, project_ids, invoke):
    """A scoped rejection is legitimate only as False / (False, ...) / ValueError.

    ``TypeError`` and every other exception intentionally propagate: the
    current absence of the proposed kwargs must stay an interface RED and is
    never a product isolation result.  After an accepted rejection shape, the
    store must show zero mutation and no leftover transaction BEFORE the
    rejection is counted as reasonable.
    """
    before = _full_snapshot(store, job_id, operation_id, project_ids)
    try:
        outcome = invoke()
    except ValueError:
        outcome = _VALUE_ERROR_REJECTION
    rejected = (
        outcome is _VALUE_ERROR_REJECTION
        or outcome is False
        or (isinstance(outcome, tuple) and len(outcome) >= 1 and outcome[0] is False)
    )
    if not rejected:
        pytest.fail(
            "proposed scoped cancel_queued must reject with False, a "
            "(False, code, record) tuple, or ValueError — observed "
            f"{outcome!r}; TypeError deliberately propagates as interface RED"
        )
    after = _full_snapshot(store, job_id, operation_id, project_ids)
    assert after == before, (
        "a rejected scoped cancel must leave the job row, operation row, "
        "nested operation metadata, event log, and binding flags identical"
    )
    assert store.db.in_transaction is False, (
        "a rejected scoped cancel must not leave an open SQLite transaction"
    )


def test_t35_proposed_scoped_cancel_success_same_project(store):
    """PROPOSED RED: both sides bound to A and call scope=A → cancel completes."""
    metadata = {"project_id": PROJ_A}
    job_id, operation_id = _seed_queued_pair(store, metadata)

    job_row, op_row = _raw_rows(store, job_id, operation_id)
    assert consistent_project_id(
        json.loads(job_row["metadata"]), json.loads(op_row["metadata"])
    ) == PROJ_A
    assert _binding_flags(store, job_id, [PROJ_A, PROJ_B]) == {
        PROJ_A: True,
        PROJ_B: False,
    }
    strict_queued_before = store.list_jobs(
        status="QUEUED", project_id=PROJ_A, enforce_project_binding=True
    )
    assert strict_queued_before.total == 1

    # First execution reaches this line and raises TypeError ('project_id')
    # — the intended interface RED for the not-yet-existing kwarg.
    ok, code, _record = store.cancel_queued(
        job_id, reason=CANCEL_REASON, project_id=PROJ_A, enforce_project_binding=True
    )
    assert ok is True
    assert code == "CANCELLED"

    # Full durable verification of BOTH rows and the event log.
    job_row, op_row = _raw_rows(store, job_id, operation_id)
    assert job_row["status"] == "CANCELLED"
    assert job_row["finished_at"] is not None
    assert json.loads(job_row["metadata"]) == metadata
    assert op_row["status"] == "CANCELLED"
    assert op_row["finished_at"] is not None
    assert json.loads(op_row["metadata"]) == metadata
    result = json.loads(op_row["result"])
    assert result["data"]["status"] == "CANCELLED"
    assert result["data"]["job_id"] == job_id
    assert result["error"]["code"] == "OPERATION_CANCELLED"
    assert CANCEL_REASON in result["error"]["message"]
    assert result["execution"]["job_id"] == job_id
    assert result["execution"]["operation_id"] == operation_id

    events = store.events(job_id, limit=1000)
    assert [event["event"] for event in events] == ["CANCELLED"]
    assert events[0]["metadata"] == {
        "reason": CANCEL_REASON,
        "engine_dispatched": False,
        "cancelled_while": "QUEUED",
        "mode": "QUEUED_ABORT",
    }

    assert _binding_flags(store, job_id, [PROJ_A, PROJ_B]) == {
        PROJ_A: True,
        PROJ_B: False,
    }
    strict_queued_after = store.list_jobs(
        status="QUEUED", project_id=PROJ_A, enforce_project_binding=True
    )
    assert strict_queued_after.total == 0
    assert store.db.in_transaction is False


def test_t35_proposed_scoped_cancel_cross_project_rejected_without_write(store):
    """PROPOSED RED: both sides bound to A but call scope=B → deny, zero write."""
    metadata = {"project_id": PROJ_A}
    job_id, operation_id = _seed_queued_pair(store, metadata)
    assert _binding_flags(store, job_id, [PROJ_A, PROJ_B]) == {
        PROJ_A: True,
        PROJ_B: False,
    }

    _assert_rejected_without_write(
        store,
        job_id,
        operation_id,
        [PROJ_A, PROJ_B],
        lambda: store.cancel_queued(
            job_id,
            reason=CANCEL_REASON,
            project_id=PROJ_B,
            enforce_project_binding=True,
        ),
    )

    job_row, op_row = _raw_rows(store, job_id, operation_id)
    assert job_row["status"] == "QUEUED"
    assert op_row["status"] == "QUEUED"
    assert op_row["result"] is None
    assert store.events(job_id, limit=1000) == []


def test_t35_proposed_scoped_cancel_diverged_sides_rejected(store):
    """PROPOSED RED: task-owned SQL rebinds this test's job row A→B while the
    operation row keeps A; an A-scoped call must still deny and write nothing.

    This is a STATIC divergence fixture (self-created rows only), not the live
    A→B dynamic race counterexample the parent review records separately.
    """
    metadata = {"project_id": PROJ_A}
    job_id, operation_id = _seed_queued_pair(store, metadata)

    # Mutation is allowed only because this file created the row itself.
    diverged_job_metadata = {"execution": {"project_id": PROJ_B}}
    with store.lock:
        store.db.execute(
            "UPDATE jobs SET metadata=? WHERE job_id=?",
            (json.dumps(diverged_job_metadata, sort_keys=True), job_id),
        )

    job_row, op_row = _raw_rows(store, job_id, operation_id)
    assert consistent_project_id(
        json.loads(job_row["metadata"]), json.loads(op_row["metadata"])
    ) is None
    assert _binding_flags(store, job_id, [PROJ_A, PROJ_B]) == {
        PROJ_A: False,
        PROJ_B: False,
    }

    _assert_rejected_without_write(
        store,
        job_id,
        operation_id,
        [PROJ_A, PROJ_B],
        lambda: store.cancel_queued(
            job_id,
            reason=CANCEL_REASON,
            project_id=PROJ_A,
            enforce_project_binding=True,
        ),
    )

    job_row, op_row = _raw_rows(store, job_id, operation_id)
    assert job_row["status"] == "QUEUED"
    assert json.loads(job_row["metadata"]) == diverged_job_metadata
    assert json.loads(op_row["metadata"]) == metadata
    assert op_row["result"] is None
    assert store.events(job_id, limit=1000) == []


def test_t35_proposed_strict_mode_missing_scope_rejected(store):
    """PROPOSED RED: enforce_project_binding=True with no project_id → deny,
    zero write.  First failure here is TypeError ('enforce_project_binding')."""
    metadata = {"project_id": PROJ_A}
    job_id, operation_id = _seed_queued_pair(store, metadata)

    _assert_rejected_without_write(
        store,
        job_id,
        operation_id,
        [PROJ_A, PROJ_B],
        lambda: store.cancel_queued(
            job_id, reason=CANCEL_REASON, enforce_project_binding=True
        ),
    )

    job_row, op_row = _raw_rows(store, job_id, operation_id)
    assert job_row["status"] == "QUEUED"
    assert op_row["status"] == "QUEUED"


def test_t35_proposed_strict_mode_non_bool_enforce_rejected(store):
    """PROPOSED RED: a non-bool enforce flag must deny and write nothing.

    Acceptance is False/ValueError per the delegation; see the module docstring
    for the recorded risk that the sibling ``list_jobs`` strict path today
    rejects a non-bool flag with TypeError instead.
    """
    metadata = {"project_id": PROJ_A}
    job_id, operation_id = _seed_queued_pair(store, metadata)

    _assert_rejected_without_write(
        store,
        job_id,
        operation_id,
        [PROJ_A, PROJ_B],
        lambda: store.cancel_queued(
            job_id,
            reason=CANCEL_REASON,
            project_id=PROJ_A,
            enforce_project_binding="yes",
        ),
    )

    job_row, op_row = _raw_rows(store, job_id, operation_id)
    assert job_row["status"] == "QUEUED"
    assert op_row["status"] == "QUEUED"
