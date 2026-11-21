"""T35 typed-identity edges for the shared progress reader — synthetic, fresh :memory: only.

Scope and honesty contract (read before trusting any run):

* Every test builds a FRESH ``OperationStore(':memory:')`` and only ever
  mutates its own synthetic rows through the store's SQLite connection
  (same fault model as ``test_t35_progress_identity_completeness_20261113``).
  No Worker, no COMSOL, no daemon, no network, no real historical UNKNOWN
  state, and no prior test/log data is read or copied. Reuses the
  compaction-fixture primitives ``_make_capture``/``_append_capture``/
  ``_admit``/``_read``/``_mutate_row_metadata`` verbatim; the only extra
  builder here is ``_capture_with_revision`` because the shared fixture
  pins ``expected_revision`` to 3 and this file needs exact durable
  revisions 0/1. No product file is modified.
* Each test first proves a genuine CAPTURED positive control on the clean
  capture, THEN corrupts durable and/or event records; a corrupt case may
  only ever answer with the fail-closed summary (status UNKNOWN, text
  None) or with an ``ExecutionContractError`` refusal (the class default
  ``safe_retry is False`` — the reader never dispatches). A bare TypeError
  or any other escaping exception is NOT accepted as a pass: the reader
  documents fail-closed UNKNOWN for malformed input, so an escaping raw
  exception is a product finding, not a compliant answer. No reason
  wording or code literal is imposed beyond what the existing suite
  already pins.

Content-derived behaviour predictions (from reading the product, NOT
from a run — a disagreement with the run is a product finding to triage):

* Typed ``operations.metadata.operation`` corruption (key present, JSON
  scalar): ``_progress_log_summary`` (:881-890) resolves
  ``bound_operation`` via set membership; int/true/false/null/unsupported
  string are hashable, resolve to ``None`` and hit the durable identity
  lower bound at :938-950 -> UNKNOWN "durable job/operation identity is
  missing or malformed...". Event-side sync is internally consistent but
  irrelevant there: durable-vs-event equality is explicitly necessary
  but not sufficient (:926-937), so this holds even with both sides
  corrupted identically.
* Typed ``operation`` as JSON ARRAY or OBJECT: ``stored_operation in
  {"study.run", "mesh.convergence_study"}`` (:883) raises
  ``TypeError: unhashable type: 'list'/'dict'``. That TypeError escapes
  the summary inside the snapshot context and is caught by
  ``solver_log_read``'s generic handler (:1313-1317), re-raised as
  ``PreWriteRefusal(EXECUTION_STATE_UNKNOWN)`` with the default
  ``safe_retry=False`` — a compliant fail-closed refusal. The unhashable
  test accepts UNKNOWN or ExecutionContractError; it FAILS (red, product
  finding) if a raw TypeError ever escapes unwrapped.
* Bool/int identity trap (requested by the parent agent): durable
  ``execution.expected_revision`` stays the legal integer 1 (strictly
  typed on the durable side at :922, and the producer itself refuses a
  bool revision pre-dispatch, ``_comsol_progress_capture.py:65``), while
  every progress event's ``model_revision`` is synthetically JSON
  ``true``. ``_same_progress_binding`` (:855-856) compares with plain
  ``==``, so ``True == 1`` holds and the 12-field binding PASSES — the
  summary as read would certify CAPTURED. This test asserts the safety
  property instead (a bool-valued event revision against integer 1 is
  never certified; expected UNKNOWN): a RED run on
  ``test_event_revision_json_true_never_certifies_integer_revision_one``
  is the expected outcome and an honest product finding — the event-side
  binding lacks the ``type(...) is int`` strictness the durable side and
  the producer both enforce. The ``true``-against-``0`` contrast test
  stays green (``True == 0`` is False -> binding mismatch) and isolates
  the trap to Python's ``bool``/``int`` equality.
* durable-side JSON ``true`` revision on BOTH execution mappings: the
  durable lower bound ``type(execution.get("expected_revision")) is not
  int`` (:922) fails closed -> UNKNOWN "durable job/operation do not
  share a complete project/model/revision binding"; event side is then
  never consulted.

NOT RUN: per the delegated task these tests were not executed here.
"""
from __future__ import annotations

import json
import uuid

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore
from tests import test_t35_progress_compaction_independent_20261113 as fixture

# Statuses that positively certify capture output; none may ever appear
# for a malformed identity, in any branch of any test below.
CERTIFYING = {"CAPTURED", "CAPTURED_TRUNCATED"}


def _set_field_on_all_progress_events(store: OperationStore, job_id: str,
                                      field: str, value: object) -> int:
    """Sync one binding field to ``value`` in EVERY COMSOLProgress* row,
    modelling corruption that durable and event records share identically."""
    rows = store.db.execute(
        "SELECT id,event,metadata FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    updated = 0
    for row in rows:
        if not row["event"].startswith("COMSOLProgress"):
            continue
        metadata = json.loads(row["metadata"])
        assert field in metadata, f"event {row['event']} lacks binding field {field}"
        metadata[field] = value
        store.db.execute("UPDATE job_events SET metadata=? WHERE id=?",
                         (json.dumps(metadata, sort_keys=True), row["id"]))
        updated += 1
    assert updated >= 4, f"expected the full producer lifecycle, only touched {updated} rows"
    return updated


def _capture_with_revision(store: OperationStore, revision: int) -> dict:
    """Like ``fixture._make_capture`` but with an exact durable
    ``execution.expected_revision`` (the shared fixture pins 3)."""
    project = fixture._new_project()
    record = fixture._admit(store, project, metadata={
        "operation": "study.run",
        "execution": {
            "project_id": project,
            "session_id": "t35t-session-" + uuid.uuid4().hex,
            "model_ref": {"kind": "t35t-synthetic-model", "token": uuid.uuid4().hex},
            "expected_revision": revision,
        },
    })
    ctx = fixture._append_capture(store, record, project, b"t35t typed-edge progress line\n" * 40)
    store.update_job(ctx["job_id"], "SUCCEEDED")
    return ctx


def _assert_captured_control(store: OperationStore, ctx: dict) -> None:
    log = fixture._read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]
    assert log["status"] == "CAPTURED", f"positive control broken before tamper: {log}"
    assert log["sha256"] == fixture._sha(ctx["payload"])


# --------------------------------------------------------------------------
# hashable JSON types in the durable persisted operation (synced to events)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("json_literal,value", [
    pytest.param("7", 7, id="operation-int"),
    pytest.param("true", True, id="operation-json-true"),
    pytest.param("false", False, id="operation-json-false"),
    pytest.param("null", None, id="operation-json-null"),
    # A near-miss string: not study.run/mesh.convergence_study, not a
    # registry_call/operation_call wrapper selection.
    pytest.param('"study.run-extension"', "study.run-extension", id="operation-unsupported-string"),
])
def test_typed_persisted_operation_never_certifies(json_literal, value):
    """operations.metadata.operation replaced (key kept) by each hashable
    JSON type and synced into every progress event. Expected: durable
    lower bound -> UNKNOWN "durable job/operation identity is missing or
    malformed..." with no text; never CAPTURED."""
    store = OperationStore(":memory:")
    try:
        ctx = fixture._make_capture(store)
        _assert_captured_control(store, ctx)
        fixture._mutate_row_metadata(
            store, "operations", "operation_id", ctx["record"]["operation_id"],
            lambda metadata: metadata.__setitem__("operation", value))
        _set_field_on_all_progress_events(store, ctx["job_id"], "operation", value)
        log = fixture._read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]
        assert log["status"] not in CERTIFYING, (
            f"operation={json_literal} must never be certified, got {log['status']}")
        assert log["status"] == "UNKNOWN" and log["text"] is None, log
        assert "durable job/operation identity" in (log.get("reason") or ""), log.get("reason")
    finally:
        store.close()


# --------------------------------------------------------------------------
# unhashable JSON types: compliant fail-closed, raw TypeError NOT a pass
# --------------------------------------------------------------------------

@pytest.mark.parametrize("json_literal,value", [
    # Shape is wrong even though the content looks solver-shaped: a list
    # can never be a persisted operation name.
    pytest.param('["study.run"]', ["study.run"], id="operation-list"),
    pytest.param('{"name": "study.run"}', {"name": "study.run"}, id="operation-dict"),
])
def test_unhashable_persisted_operation_fails_closed_not_certified(json_literal, value):
    """operations.metadata.operation replaced by a JSON array/object and
    synced to events. Accepted outcomes: fail-closed UNKNOWN, or an
    ExecutionContractError refusal with safe_retry False (predicted
    current mechanism: unhashable set membership at _g3_solver_ops.py:883
    wrapped by solver_log_read's generic handler at :1313 into
    EXECUTION_STATE_UNKNOWN — a compliant refusal). A raw TypeError or
    any other escaping exception fails the test as a product finding;
    certifying CAPTURED is never acceptable."""
    store = OperationStore(":memory:")
    try:
        ctx = fixture._make_capture(store)
        _assert_captured_control(store, ctx)
        fixture._mutate_row_metadata(
            store, "operations", "operation_id", ctx["record"]["operation_id"],
            lambda metadata: metadata.__setitem__("operation", value))
        _set_field_on_all_progress_events(store, ctx["job_id"], "operation", value)
        try:
            log = fixture._read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]
        except ExecutionContractError as exc:
            assert exc.safe_retry is False, (
                f"operation={json_literal} refusal must not advertise a safe retry")
            assert isinstance(exc.code, str) and exc.code, exc
            return
        assert log["status"] not in CERTIFYING, (
            f"operation={json_literal} must never be certified, got {log['status']}")
        assert log["status"] == "UNKNOWN" and log["text"] is None, log
    finally:
        store.close()


# --------------------------------------------------------------------------
# JSON bool vs integer revision traps (parent-requested equality trap)
# --------------------------------------------------------------------------

def test_event_revision_json_true_never_certifies_integer_revision_one():
    """Durable expected_revision stays the legal integer 1 on both sides;
    every progress event's model_revision becomes JSON ``true``.
    Contract: a bool-valued event identity is never certified against an
    integer 1, even though Python's ``True == 1`` — the equality-based
    12-field binding (:855-856) must not authenticate a type-confused
    revision, mirroring the durable-side ``type(...) is int`` at :922 and
    the producer's bool-revision refusal (_comsol_progress_capture.py:65).
    Predicted CURRENT result: red — the reader as read certifies CAPTURED
    through ``True == 1``. Treat that as the product finding this test
    exists to surface (event-side strict-type check missing), NOT as a
    test to relax. No specific reason wording is pinned."""
    store = OperationStore(":memory:")
    try:
        ctx = _capture_with_revision(store, 1)
        _assert_captured_control(store, ctx)
        _set_field_on_all_progress_events(store, ctx["job_id"], "model_revision", True)
        log = fixture._read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]
        assert log["status"] not in CERTIFYING, (
            "event model_revision JSON true against integer durable revision 1 must never "
            f"be certified on Python True == 1, got {log['status']}: {log}")
        assert log["status"] == "UNKNOWN" and log["text"] is None, log
    finally:
        store.close()


@pytest.mark.parametrize("event_revision,tamper_events", [
    # Durable and events corrupted identically to JSON true.
    pytest.param(True, True, id="durable-revision-true-events-json-true"),
    # Only the durable side becomes JSON true; events keep integer 1.
    pytest.param(1, False, id="durable-revision-true-events-int-one"),
])
def test_durable_revision_json_true_never_certifies(event_revision, tamper_events):
    """Durable execution.expected_revision JSON ``true`` on BOTH the
    operation and job metadata: the durable lower bound (:922
    ``type(...) is not int``) must fail closed to UNKNOWN regardless of
    the event-side revision value. Reason substring is the one already
    pinned by the existing suite."""
    store = OperationStore(":memory:")
    try:
        ctx = _capture_with_revision(store, 1)
        _assert_captured_control(store, ctx)
        fixture._mutate_row_metadata(
            store, "operations", "operation_id", ctx["record"]["operation_id"],
            lambda metadata: metadata["execution"].__setitem__("expected_revision", True))
        fixture._mutate_row_metadata(
            store, "jobs", "job_id", ctx["job_id"],
            lambda metadata: metadata["execution"].__setitem__("expected_revision", True))
        if tamper_events:
            _set_field_on_all_progress_events(store, ctx["job_id"], "model_revision", event_revision)
        log = fixture._read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]
        assert log["status"] not in CERTIFYING, (
            f"durable revision JSON true must never be certified, got {log['status']}")
        assert log["status"] == "UNKNOWN" and log["text"] is None, log
        assert "complete project/model/revision binding" in (log.get("reason") or ""), log.get("reason")
    finally:
        store.close()


def test_event_revision_json_true_against_zero_is_binding_mismatch():
    """Contrast isolating the trap: against durable integer 0, Python's
    ``True == 0`` is False, so the existing equality-based binding already
    fails closed to UNKNOWN "progress producer event identity differs"
    (reason substring pinned by the existing suite). Only integer 1
    smuggles a JSON bool through ``==``."""
    store = OperationStore(":memory:")
    try:
        ctx = _capture_with_revision(store, 0)
        _assert_captured_control(store, ctx)
        _set_field_on_all_progress_events(store, ctx["job_id"], "model_revision", True)
        log = fixture._read(store, ctx["project_id"], ctx["job_id"])["solver_output_log"]
        assert log["status"] not in CERTIFYING, (
            f"event revision true vs durable 0 must not certify, got {log['status']}")
        assert log["status"] == "UNKNOWN" and log["text"] is None, log
        assert "progress producer event identity differs" in (log.get("reason") or ""), log.get("reason")
    finally:
        store.close()
