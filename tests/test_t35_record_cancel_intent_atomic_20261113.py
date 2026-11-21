"""T35 — independent synthetic fault/positive-control tests for the additive
OperationStore.record_cancel_intent atomic durable-consistency primitive.

These tests call the REAL ``OperationStore.record_cancel_intent`` method
directly against fresh ``OperationStore(':memory:')`` ledgers.  They are NOT
claims of authentication, public MCP behaviour, daemon wiring, or native
cancellation: the method's project-binding verdict is a durable-consistency
check on already-stored metadata, not an authorization result, and this file
deliberately contains NO authority object at all — the dispatcher-level
permission gate is the sibling ``test_t35_cancel_wiring_synthetic_20261113.py``
concern, and a store primitive must never be reported as granting capability.

Static basis (read-only, frozen):
- ``_state/t35-log-overflow-parent-20261112/store_cancel_intent_candidate_b6183204.py.txt``
  lines 2488-2628 define ``record_cancel_intent``: one ``BEGIN IMMEDIATE``
  reads the job row and its uniquely associated operation row, decodes both
  metadata columns, requires the pure ``consistent_project_id`` helper to
  report the SAME unambiguous project ID on both sides equal to the exact
  ``project_id`` argument, and that binding verdict strictly precedes every
  status early return, UPDATE and event insert.  Return codes asserted here
  are the documented identifiable codes: NOT_FOUND, PROJECT_BINDING_UNKNOWN,
  PROJECT_SCOPE_MISMATCH, STATE_MISMATCH, STATUS_CHANGED, INTENT_RECORDED.
  A success keeps status, merges ``cancel_requested``/``cancel_reason`` into
  job metadata, never writes ``operations.result`` (update_job's no-result
  branch), and appends exactly one ``CancelRequested`` event with
  ``mode="UNSUPPORTED_NATIVE_CANCEL"`` after both guarded UPDATEs matched one
  row each.
- ``comsol_mcp/_job_project_scope.py`` — trusted binding fields are only the
  metadata top-level ``project_id`` and ``execution.project_id``; every
  present claim must be a nonempty string and agree exactly across both sides.
- ``tests/test_t35_cancel_wiring_synthetic_20261113.py`` — sibling envelope
  wiring tests; nothing from it is rerun, reproduced, or bypassed here, and
  this file never touches daemon dispatch.

Fault injection: a task-owned SQLite trigger on ``job_events`` with
``RAISE(ABORT, ...)`` scoped by ``WHEN NEW.event='CancelRequested'`` fails
exactly the event INSERT that the method performs AFTER its two guarded
UPDATEs; the test asserts the exception propagates, both updated rows and the
event log roll back byte-identically, and ``in_transaction`` is False.  An
inert-trigger no-fault control proves the CancelRequested insert genuinely
succeeds when the injected ABORT does not fire (the trigger harness itself
cannot mask or fake the positive path).

Scope and honesty limits:
- Every test owns a fresh ``OperationStore(':memory:')`` and seeds its own
  small QUEUED→(optionally RUNNING→terminal) pair through the real
  ``begin``/``update_job`` paths plus task-local marker events and a durable
  operations result.  No historical job, on-disk database, prior test file,
  Worker, process, thread, SSH, endpoint, credential, model, COMSOL, or
  network resource is touched; no UNKNOWN/RECONCILING state and no
  force_stop appears anywhere.  Fixture-only mutations of a seeded pair are
  direct single-row UPDATEs on the same ':memory:' connection using
  task-authored strings, so the negative fixtures exercise documented store
  states without inventing a second writer.
- Every byte passed to the database (markers, project IDs, metadata JSON,
  reason, trigger name/message) is a string authored in this file.
- Refusals assert the exact identifiable code AND a byte-identical full
  read-back of the jobs row, operations row, and complete job_events log,
  plus a closed transaction.
- Author-tooling deviation, recorded honestly: this file was authored by the
  delegated sub-agent via a single write_file only.  Per the work package it
  was NOT executed (no pytest, terminal, or code execution was permitted),
  and the live ``comsol_mcp/_operation_store.py`` could not be re-read to
  reconfirm the candidate's integration; the method contract is taken from
  the frozen b6183204 candidate named above.  If the live store lacks the
  method, the tests fail loudly with AttributeError rather than silently
  passing.
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

PROJECT_A = "t35rci-proj-A-20261113"
PROJECT_B = "t35rci-proj-B-20261113"

# Mirrors the candidate's TERMINAL tuple (D03 immutability set), spelled out
# here as task-authored literals so the parametrization stays self-contained.
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST")

BINDING_UNKNOWN_CASES = (
    "side_conflict",
    "malformed_operation_metadata",
    "absent_operation_association",
    "no_project_binding",
)

# Task-owned fault trigger: fires ONLY on the method's own CancelRequested
# event INSERT, i.e. strictly after both guarded UPDATEs inside the same
# write transaction.
_FAULT_TRIGGER = (
    "CREATE TRIGGER t35rci_cancel_event_fault "
    "BEFORE INSERT ON job_events "
    "FOR EACH ROW WHEN NEW.event = 'CancelRequested' "
    "BEGIN SELECT RAISE(ABORT, 'T35RCI-SENT-TRIGGER-FAULT'); END"
)

# Inert control trigger: identical mechanism, WHEN clause names a sentinel
# event that never appears, so the RAISE can never fire.
_INERT_TRIGGER = (
    "CREATE TRIGGER t35rci_inert_control_trigger "
    "BEFORE INSERT ON job_events "
    "FOR EACH ROW WHEN NEW.event = 'T35RCI-NeverFiresSentinel' "
    "BEGIN SELECT RAISE(ABORT, 'T35RCI-SENT-INERT-CONTROL-FAULT'); END"
)


@pytest.fixture
def owned_store():
    store = OperationStore(":memory:")
    try:
        yield store
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Self-contained seed helpers (task-local bytes only).
# ---------------------------------------------------------------------------

def _seed_pair(
    store: OperationStore,
    *,
    tag: str,
    bound_project: str | None = PROJECT_A,
    to_running: bool = True,
    terminal_status: str | None = None,
    event_markers: tuple[str, ...] = ("e1", "e2"),
    with_result: bool = True,
) -> tuple[str, str, dict[str, Any]]:
    """Create one fresh QUEUED (then optionally RUNNING, then optionally
    terminal) job/operation pair with task-local events and a durable
    operations result.  ``begin`` writes the SAME metadata to both rows, so a
    bound project is unambiguously present on both sides.

    Returns (job_id, operation_id, seed_metadata).
    """
    assert terminal_status is None or to_running
    metadata: dict[str, Any] = {
        "t35rci_synthetic": True,
        "content_marker": f"T35RCI-SENT-CONTENT-{tag}",
    }
    if bound_project is not None:
        metadata["execution"] = {"project_id": bound_project}
    record, reused = store.begin(
        request_id=f"t35rci-req-{tag}",
        idempotency_key=f"t35rci-idem-{tag}",
        request_hash="1" * 64,
        operation="job.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False
    job_id = record["job_id"]
    operation_id = record["operation_id"]
    assert isinstance(job_id, str) and job_id and isinstance(operation_id, str)

    if to_running:
        store.update_job(job_id, "RUNNING")
    for marker in event_markers:
        store.add_event(
            job_id, "T35RCIFixtureEvent", {"marker": f"T35RCI-SENT-EVT-{tag}-{marker}"}
        )
    if with_result:
        store.update_job(
            job_id,
            "RUNNING" if to_running else "QUEUED",
            result={"success": True, "data": {"marker": f"T35RCI-SENT-RESULT-{tag}"}},
        )
    if terminal_status is not None:
        store.update_job(job_id, terminal_status)

    settled = store.job(job_id)
    expected_final = terminal_status or ("RUNNING" if to_running else "QUEUED")
    assert settled is not None and settled["status"] == expected_final
    return job_id, operation_id, metadata


def _mutate_operation_metadata(store: OperationStore, operation_id: str, value: str) -> None:
    """Fixture-only single-row write of a task-authored metadata string."""
    store.db.execute(
        "UPDATE operations SET metadata=? WHERE operation_id=?", (value, operation_id)
    )


def _mutate_operation_status(store: OperationStore, operation_id: str, status: str) -> None:
    store.db.execute(
        "UPDATE operations SET status=? WHERE operation_id=?", (status, operation_id)
    )


def _mutate_job_operation_link(store: OperationStore, job_id: str, link: str) -> None:
    store.db.execute(
        "UPDATE jobs SET operation_id=? WHERE job_id=?", (link, job_id)
    )


# ---------------------------------------------------------------------------
# Full relevant-row snapshots (raw stored bytes, not parsed views).
# ---------------------------------------------------------------------------

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


def _intent(
    store: OperationStore,
    job_id: str,
    *,
    project_id: str,
    expected_status: str,
    reason: str,
) -> tuple[bool, str]:
    """Direct real-method call; the return shape itself is under test."""
    result = store.record_cancel_intent(
        job_id, project_id=project_id, expected_status=expected_status, reason=reason
    )
    assert isinstance(result, tuple) and len(result) == 2
    assert isinstance(result[0], bool) and isinstance(result[1], str)
    return result


def _assert_refusal(
    store: OperationStore,
    outcome: tuple[bool, str],
    code: str,
    before: dict[str, Any],
    job_id: str,
    label: str,
) -> None:
    ok, observed = outcome
    assert ok is False, f"{label}: expected refusal, got {outcome!r}"
    assert observed == code, f"{label}: expected {code}, got {observed!r}"
    after = _raw_snapshot(store, job_id)
    assert after == before, f"{label}: job/operation/events changed despite refusal"
    assert store.db.in_transaction is False, f"{label}: transaction left open after refusal"


# ---------------------------------------------------------------------------
# Positive: same-project RUNNING pair.  The original durable operation result
# and both statuses survive, and exactly one CancelRequested intent is
# appended with the documented event payload.
# ---------------------------------------------------------------------------

def test_running_same_project_intent_preserves_result_and_appends_exactly_one_event(
    owned_store,
) -> None:
    tag = "RPOS"
    job_id, operation_id, seed_metadata = _seed_pair(owned_store, tag=tag)
    reason = f"T35RCI-SENT-REASON-{tag}"
    before = _raw_snapshot(owned_store, job_id)

    ok, code = _intent(
        owned_store, job_id, project_id=PROJECT_A, expected_status="RUNNING", reason=reason
    )
    assert (ok, code) == (True, "INTENT_RECORDED")
    assert owned_store.db.in_transaction is False, "transaction left open after commit"

    after = _raw_snapshot(owned_store, job_id)
    # The durable operations row is untouched in every column: status stays
    # RUNNING, the seeded result bytes survive, timestamps unchanged.
    assert after["operation"] == before["operation"], (
        "intent recording must not alter the durable operations row"
    )
    # The jobs row changes ONLY its metadata column (started/finished/created
    # and status are guarded by the RUNNING CASE branches).
    assert {k: v for k, v in after["job"].items() if k != "metadata"} == {
        k: v for k, v in before["job"].items() if k != "metadata"
    }
    merged = json.loads(after["job"]["metadata"])
    assert merged == {**seed_metadata, "cancel_requested": True, "cancel_reason": reason}

    # Exactly one new event; the seeded prefix is byte-identical.
    assert len(after["events"]) == len(before["events"]) + 1
    assert after["events"][: len(before["events"])] == before["events"]
    new_event = after["events"][-1]
    assert new_event["job_id"] == job_id
    assert new_event["event"] == "CancelRequested"
    event_metadata = json.loads(new_event["metadata"])
    assert set(event_metadata) == {"reason", "mode", "cancel_accepted", "engine_stopped", "at"}
    assert event_metadata["reason"] == reason
    assert event_metadata["mode"] == "UNSUPPORTED_NATIVE_CANCEL"
    assert event_metadata["cancel_accepted"] is True
    assert event_metadata["engine_stopped"] is False
    assert isinstance(event_metadata["at"], (int, float))
    cancel_events = [row for row in after["events"] if row["event"] == "CancelRequested"]
    assert len(cancel_events) == 1, "exactly one CancelRequested intent must exist"

    # Parsed public views agree with the raw ledger.
    parsed = owned_store.job(job_id)
    assert parsed is not None and parsed["status"] == "RUNNING"
    assert parsed["operation"]["status"] == "RUNNING"
    assert parsed["operation"]["result"] == {
        "success": True,
        "data": {"marker": f"T35RCI-SENT-RESULT-{tag}"},
    }


# ---------------------------------------------------------------------------
# Negative: a consistent binding naming a DIFFERENT project than the
# argument — identifiable PROJECT_SCOPE_MISMATCH, zero durable change.
# ---------------------------------------------------------------------------

def test_different_project_refused_scope_mismatch_state_preserved(owned_store) -> None:
    tag = "XPROJ"
    job_id, _operation_id, _meta = _seed_pair(owned_store, tag=tag, bound_project=PROJECT_A)
    before = _raw_snapshot(owned_store, job_id)
    outcome = _intent(
        owned_store, job_id, project_id=PROJECT_B, expected_status="RUNNING",
        reason=f"T35RCI-SENT-REASON-{tag}",
    )
    _assert_refusal(
        owned_store, outcome, "PROJECT_SCOPE_MISMATCH", before, job_id, "different-project"
    )


# ---------------------------------------------------------------------------
# Negative: no unambiguous both-sides binding — cross-side metadata
# disagreement, malformed durable metadata, a broken operation association,
# or no project claim at all — all collapse to PROJECT_BINDING_UNKNOWN.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", BINDING_UNKNOWN_CASES)
def test_binding_unknown_refused_binding_unknown_state_preserved(case, owned_store) -> None:
    tag = f"BU-{case}"
    bound = None if case == "no_project_binding" else PROJECT_A
    job_id, operation_id, _meta = _seed_pair(owned_store, tag=tag, bound_project=bound)
    if case == "side_conflict":
        _mutate_operation_metadata(
            owned_store, operation_id,
            json.dumps(
                {"t35rci_synthetic": True, "execution": {"project_id": PROJECT_B}},
                sort_keys=True,
            ),
        )
    elif case == "malformed_operation_metadata":
        _mutate_operation_metadata(owned_store, operation_id, '{"t35rci-broken-metadata')
    elif case == "absent_operation_association":
        _mutate_job_operation_link(owned_store, job_id, "t35rci-sent-absent-operation")
    before = _raw_snapshot(owned_store, job_id)
    outcome = _intent(
        owned_store, job_id, project_id=PROJECT_A, expected_status="RUNNING",
        reason=f"T35RCI-SENT-REASON-{tag}",
    )
    _assert_refusal(
        owned_store, outcome, "PROJECT_BINDING_UNKNOWN", before, job_id, f"binding-unknown/{case}"
    )


# ---------------------------------------------------------------------------
# Negative: durable job/operation status disagreement — STATE_MISMATCH.
# ---------------------------------------------------------------------------

def test_job_operation_status_disagreement_refused_state_mismatch(owned_store) -> None:
    tag = "DESYNC"
    job_id, operation_id, _meta = _seed_pair(owned_store, tag=tag)
    _mutate_operation_status(owned_store, operation_id, "QUEUED")
    before = _raw_snapshot(owned_store, job_id)
    outcome = _intent(
        owned_store, job_id, project_id=PROJECT_A, expected_status="RUNNING",
        reason=f"T35RCI-SENT-REASON-{tag}",
    )
    _assert_refusal(
        owned_store, outcome, "STATE_MISMATCH", before, job_id, "status-disagreement"
    )


# ---------------------------------------------------------------------------
# Negative: a RUNNING pair whose current status differs from the pre-read
# expected_status — STATUS_CHANGED.
# ---------------------------------------------------------------------------

def test_expected_status_mismatch_refused_status_changed(owned_store) -> None:
    tag = "EXPST"
    job_id, _operation_id, _meta = _seed_pair(owned_store, tag=tag)
    before = _raw_snapshot(owned_store, job_id)
    outcome = _intent(
        owned_store, job_id, project_id=PROJECT_A, expected_status="STARTING",
        reason=f"T35RCI-SENT-REASON-{tag}",
    )
    _assert_refusal(
        owned_store, outcome, "STATUS_CHANGED", before, job_id, "expected-status-mismatch"
    )


# ---------------------------------------------------------------------------
# Negative: QUEUED pairs belong to cancel_queued; the intent primitive must
# refuse even when expected_status matches the durable QUEUED status.
# ---------------------------------------------------------------------------

def test_queued_pair_refused_status_changed(owned_store) -> None:
    tag = "QUEUED"
    job_id, _operation_id, _meta = _seed_pair(owned_store, tag=tag, to_running=False)
    before = _raw_snapshot(owned_store, job_id)
    outcome = _intent(
        owned_store, job_id, project_id=PROJECT_A, expected_status="QUEUED",
        reason=f"T35RCI-SENT-REASON-{tag}",
    )
    _assert_refusal(owned_store, outcome, "STATUS_CHANGED", before, job_id, "queued-pair")


# ---------------------------------------------------------------------------
# Negative: every terminal status is refused even when expected_status names
# that same terminal status (D03 immutability clause, not the drift clause).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("terminal_status", TERMINAL_STATUSES)
def test_terminal_pair_refused_status_changed(terminal_status, owned_store) -> None:
    tag = f"TERM-{terminal_status}"
    job_id, _operation_id, _meta = _seed_pair(
        owned_store, tag=tag, terminal_status=terminal_status
    )
    before = _raw_snapshot(owned_store, job_id)
    outcome = _intent(
        owned_store, job_id, project_id=PROJECT_A, expected_status=terminal_status,
        reason=f"T35RCI-SENT-REASON-{tag}",
    )
    _assert_refusal(
        owned_store, outcome, "STATUS_CHANGED", before, job_id, f"terminal/{terminal_status}"
    )


# ---------------------------------------------------------------------------
# Ordering contract: the binding verdict strictly precedes the status gate —
# a QUEUED pair bound to A called with project B reports the binding outcome,
# not STATUS_CHANGED.
# ---------------------------------------------------------------------------

def test_binding_verdict_precedes_status_gate(owned_store) -> None:
    tag = "ORDER"
    job_id, _operation_id, _meta = _seed_pair(owned_store, tag=tag, to_running=False)
    before = _raw_snapshot(owned_store, job_id)
    outcome = _intent(
        owned_store, job_id, project_id=PROJECT_B, expected_status="QUEUED",
        reason=f"T35RCI-SENT-REASON-{tag}",
    )
    _assert_refusal(
        owned_store, outcome, "PROJECT_SCOPE_MISMATCH", before, job_id, "binding-precedes-status"
    )


# ---------------------------------------------------------------------------
# Fault injection: RAISE(ABORT) on the event insert AFTER both guarded
# UPDATEs.  The exception must propagate, and BOTH updated rows plus the
# existing event log must be fully rolled back with the transaction closed.
# ---------------------------------------------------------------------------

def test_trigger_abort_after_updates_rolls_back_all_writes(owned_store) -> None:
    tag = "FAULT"
    job_id, operation_id, _meta = _seed_pair(owned_store, tag=tag)
    owned_store.db.execute(_FAULT_TRIGGER)
    before = _raw_snapshot(owned_store, job_id)

    with pytest.raises(sqlite3.IntegrityError) as caught:
        owned_store.record_cancel_intent(
            job_id, project_id=PROJECT_A, expected_status="RUNNING",
            reason=f"T35RCI-SENT-REASON-{tag}",
        )
    assert "T35RCI-SENT-TRIGGER-FAULT" in str(caught.value), (
        "the injected RAISE(ABORT) message must be the propagated error"
    )
    assert owned_store.db.in_transaction is False, (
        "the rollback wrapper must close the transaction after the injected fault"
    )

    after = _raw_snapshot(owned_store, job_id)
    # Byte-identical read-back proves BOTH guarded UPDATEs (job metadata merge
    # and the operations no-result branch) were undone, and that neither the
    # pre-existing events nor any partial CancelRequested survived.
    assert after == before, (
        "trigger fault must roll back both updated rows and leave the event log intact"
    )
    assert "cancel_requested" not in (after["job"]["metadata"] or "")
    assert not [row for row in after["events"] if row["event"] == "CancelRequested"]
    assert [row["id"] for row in after["events"]] == [row["id"] for row in before["events"]]
    assert after["operation"]["result"] == before["operation"]["result"]


# ---------------------------------------------------------------------------
# No-fault positive control: with the identical trigger harness present but
# its WHEN clause unable to fire, the CancelRequested event genuinely
# inserts and the call succeeds — the fault test's mechanism cannot be
# passing by suppressing the insert path.
# ---------------------------------------------------------------------------

def test_inert_trigger_control_proves_event_inserts(owned_store) -> None:
    tag = "CTRL"
    job_id, _operation_id, _meta = _seed_pair(owned_store, tag=tag)
    owned_store.db.execute(_INERT_TRIGGER)
    before = _raw_snapshot(owned_store, job_id)

    ok, code = _intent(
        owned_store, job_id, project_id=PROJECT_A, expected_status="RUNNING",
        reason=f"T35RCI-SENT-REASON-{tag}",
    )
    assert (ok, code) == (True, "INTENT_RECORDED")
    assert owned_store.db.in_transaction is False

    after = _raw_snapshot(owned_store, job_id)
    cancel_events = [row for row in after["events"] if row["event"] == "CancelRequested"]
    assert len(cancel_events) == 1, (
        "the CancelRequested event insert must actually land when no injected fault fires"
    )
    assert after["events"][: len(before["events"])] == before["events"]
    assert json.loads(cancel_events[0]["metadata"])["mode"] == "UNSUPPORTED_NATIVE_CANCEL"
