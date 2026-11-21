"""T35 real in-memory-store test for the large stored-result memory gap of
``solver.log_read`` (authored, NOT RUN).

Scope and honesty boundaries (binding for every test in this file):

- Real layers only where claimed: the real ``solver_log_read`` against real
  fresh ``OperationStore(":memory:")`` ledgers with brand-new synthetic
  begin/add_event/finish state. No daemon, ControlDaemon, Worker, COMSOL,
  SSH, model, endpoint, credential, historical/prior DB, or real UNKNOWN job
  is touched, and nothing here uses tmp_path fixtures, disk files, network,
  threads, subprocesses, or deletes.
- Unlike the sibling T35 capacity files, this file patches NO constants at
  all. It exercises the current default fold/page/progress path end to end
  and isolates one specific gap: every read's ``store.job()`` ->
  ``OperationStore._job`` -> ``get_operation()`` -> ``_op`` round-trip
  ``json.loads`` the ENTIRE stored operation result into Python objects,
  regardless of the page actually requested. The test asserts the CURRENT
  public reader/store behaviour; observed source symbols/line numbers were
  drifting while this file was authored (a separate project-binding gate
  hardening in ``_job_project_binding`` may be in progress), so nothing here
  embeds line numbers or source hashes and only the public
  ``solver_log_read`` / ``OperationStore`` surface is used.
- The cross-project rejection assertion reflects the reader's own
  ``_job_project_binding`` gate as currently published
  (``PROJECT_IDENTITY_MISMATCH``). It is exercised only because the rejected
  read is the SAME real call chain (``store.job()`` still precedes the
  binding gate); it is not a project-authorization claim — real route
  authorization is enforced one layer above ``solver_log_read`` and is
  neither validated nor implied here.
- The 4 MiB thresholds are regression bounds for THESE synthetic sample
  pairs only, not a production limit.
- tracemalloc measures the PYTHON allocator only. Nothing here measures or
  certifies SQLite C-level memory, process RSS, file/db size, or per-row
  admission budgets (the sibling files cover per-row budgets).
- Test 2 encodes the PROPOSED bounded-result contract and is expected RED
  against the current implementation (the 16 MiB-class stored result must
  materialize on every read today). This file was authored and never
  executed; nothing below may be cited as a test run or a passed result.
- If tracemalloc is already tracing when a measurement starts, that test
  fails closed immediately and never stops the pre-existing foreign
  tracing state. Each test closes only its own ``":memory:"`` stores.
"""
from __future__ import annotations

import gc
import tracemalloc

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

SESSION = "t35-mem-result-session"

# 16 MiB single synthetic string field; the marker sits at its head so the
# non-leak assertions can search for one short token without ever printing
# or reproducing the payload itself.
LARGE_FIELD_CHARS = 16 * 1024 * 1024
LARGE_PAYLOAD_MARKER = "T35-MEM-LARGE-RESULT-NO-LEAK-MARKER"

# Python-allocator regression allowance for these synthetic pairs.
PEAK_ALLOWANCE_BYTES = 4 * 1024 * 1024

# Deliberately different (length, layer types, and messages) stored chains,
# so an exact readback proves WHICH job's stored result the reader exposed.
SMALL_CAUSE_CHAIN = [
    {"type": "T35MemSmallException", "message": "t35-mem-small-cause-1"},
]
LARGE_CAUSE_CHAIN = [
    {"type": "T35MemLargeOuterException", "message": "t35-mem-large-cause-1"},
    {"type": "T35MemLargeInnerException", "message": "t35-mem-large-cause-2"},
]

EVENT_COUNT = 3


def _expected_event_names() -> list[str]:
    return [f"T35MemEvent{index}" for index in range(EVENT_COUNT)]


def _expected_event_metadata() -> list[dict]:
    return [{"index": index} for index in range(EVENT_COUNT)]


def _start_job(store: OperationStore, *, project: str, tag: str) -> dict:
    """Real store.begin with the complete project/execution binding that
    ``_g3_solver_ops._job_project_binding`` requires for both job and
    operation rows."""
    record, reused = store.begin(
        request_id=f"t35-mem-req-{tag}",
        idempotency_key=f"t35-mem-key-{tag}",
        request_hash=f"t35-mem-hash-{tag}",
        operation="study.run",
        metadata={
            "operation": "study.run",
            "arguments": {},
            "execution": {"project_id": project, "session_id": SESSION},
        },
    )
    assert reused is False and record["job_id"] and record["operation_id"]
    return record


def _add_identical_events(store: OperationStore, job_id: str) -> None:
    for index in range(EVENT_COUNT):
        store.add_event(job_id, f"T35MemEvent{index}", {"index": index})


def _finish_with_result(store: OperationStore, operation_id: str, result: dict) -> None:
    accepted, authoritative = store.finish(operation_id, status="FAILED", result=result)
    assert accepted is True and authoritative == "FAILED"


def _db_result_length(store: OperationStore, operation_id: str) -> int:
    """Grounding through the real ledger: the durable result column size.
    Runs only OUTSIDE any tracemalloc session."""
    return store.db.execute(
        "SELECT LENGTH(result) FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()[0]


def _db_raw_result_contains(store: OperationStore, operation_id: str, marker: str) -> bool:
    """Prove the large payload really is inside the stored durable result
    (databaseside grounding), without printing the payload."""
    raw = store.db.execute(
        "SELECT result FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()[0]
    try:
        return marker in raw
    finally:
        del raw


def _read_with_isolated_tracing(store: OperationStore, project: str,
                                arguments: dict) -> tuple[dict, int]:
    """One solver.log_read inside a tracemalloc session started AFTER the
    ledger was fully loaded. Fails closed (never stopping foreign state) if
    tracing is already active, and always stops its own session."""
    if tracemalloc.is_tracing():
        pytest.fail(
            "tracemalloc was already tracing before this test's session; "
            "failing closed and leaving the foreign tracing state untouched"
        )
    tracemalloc.start()
    try:
        page = ops.solver_log_read(store, project, arguments)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return page, peak


def _assert_complete_page(page: dict, rows: list[dict], *, job_id: str, project: str,
                          expected_chain: list[dict]) -> None:
    """Full output-completeness proof for one read, run BEFORE any peak
    assertion. Event payloads are tiny, so no assertion message can echo a
    large payload; the large field lives only in the stored result."""
    assert page["job_id"] == job_id
    assert page["project_id"] == project
    assert page["job_status"] == "FAILED"
    assert page["event_count"] == EVENT_COUNT == len(page["events"]) == len(rows)
    assert [event["id"] for event in page["events"]] == [row["id"] for row in rows]
    assert [event["event"] for event in page["events"]] == _expected_event_names()
    assert [event["metadata"] for event in page["events"]] == _expected_event_metadata()
    assert page["next_cursor"] is None
    assert page["snapshot"]["status"] == "COMPLETE"
    assert page["snapshot"]["event_count"] == EVENT_COUNT
    assert page["snapshot"]["last_event_id"] == rows[-1]["id"]
    assert page["snapshot"]["id_sha256"] == ops._event_ids_digest(rows)
    assert page["snapshot"]["events_sha256"] == ops._event_digest(rows)
    assert page["solver_output_log"]["status"] == "NOT_CAPTURED"
    chain = page["exception_chain"]
    assert chain["status"] == "CAPTURED"
    assert chain["source"] == "stored result.error.cause_chain"
    assert chain["items"] == expected_chain


def test_t35_small_stored_result_log_read_python_peak_positive_control():
    """GREEN control: a brand-new synthetic job with a tiny stored result and
    the same tiny event shape pages completely, exposes its distinct stored
    ``error.cause_chain`` exactly, and its isolated tracemalloc peak stays
    under ``PEAK_ALLOWANCE_BYTES``. This control pins the fold/digest/page
    baseline so the RED delta in test 2 is attributable to result size."""
    project = "t35-mem-small-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="small-ctl")
        job_id = record["job_id"]
        _add_identical_events(store, job_id)
        _finish_with_result(store, record["operation_id"], {
            "success": False,
            "note": "t35-mem-small-result",
            "error": {
                "code": "T35_MEM_SMALL_SYNTHETIC",
                "message": "small synthetic failure",
                "cause_chain": SMALL_CAUSE_CHAIN,
            },
        })
        assert _db_result_length(store, record["operation_id"]) < 4096
        rows = store.events(job_id, 0, 1000)
        assert len(rows) == EVENT_COUNT
        gc.collect()

        page, peak = _read_with_isolated_tracing(
            store, project, {"job_id": job_id, "limit": EVENT_COUNT})

        # Completeness/correctness first, peak bound only afterwards.
        _assert_complete_page(page, rows, job_id=job_id, project=project,
                              expected_chain=SMALL_CAUSE_CHAIN)
        assert peak <= PEAK_ALLOWANCE_BYTES, (
            f"small-result read already exceeded the Python baseline bound ({peak} bytes)"
        )
    finally:
        store.close()


def test_t35_large_stored_result_log_read_python_peak_bound_red():
    """PROPOSED-contract RED test: one fresh in-memory ledger holds TWO
    brand-new jobs with byte-identical tiny events, one with a tiny stored
    result and one with a ~16 MiB stored result field. Each read runs in its
    own post-load tracemalloc session. After BOTH outputs are proven
    complete (project/job ids, exact event ids/names/metadata, frozen
    snapshot digests, and each job's distinct stored ``error.cause_chain``),
    the proposed bound demands that swapping only the stored result size
    keeps the Python peak delta under ``PEAK_ALLOWANCE_BYTES``. The current
    implementation is expected to violate this because every read's
    ``store.job()`` -> ``_job`` -> ``get_operation()`` -> ``_op`` fully
    ``json.loads`` the stored result. Expected RED, authored, never run."""
    project_small = "t35-mem-large-ctl-project"
    project_large = "t35-mem-large-res-project"
    store = OperationStore(":memory:")
    try:
        small = _start_job(store, project=project_small, tag="large-ctl")
        large = _start_job(store, project=project_large, tag="large-res")
        assert small["job_id"] != large["job_id"]
        _add_identical_events(store, small["job_id"])
        _add_identical_events(store, large["job_id"])

        _finish_with_result(store, small["operation_id"], {
            "success": False,
            "note": "t35-mem-small-result",
            "error": {
                "code": "T35_MEM_SMALL_SYNTHETIC",
                "message": "small synthetic failure",
                "cause_chain": SMALL_CAUSE_CHAIN,
            },
        })
        large_field = LARGE_PAYLOAD_MARKER + "L" * (LARGE_FIELD_CHARS - len(LARGE_PAYLOAD_MARKER))
        _finish_with_result(store, large["operation_id"], {
            "success": False,
            "note": "t35-mem-large-result",
            "large_synthetic_field": large_field,
            "error": {
                "code": "T35_MEM_LARGE_SYNTHETIC",
                "message": "large synthetic failure",
                "cause_chain": LARGE_CAUSE_CHAIN,
            },
        })
        del large_field

        # Ledger grounding BEFORE any measurement: the big result really is
        # durable in the real database and the small one really is tiny.
        assert _db_result_length(store, small["operation_id"]) < 4096
        assert _db_result_length(store, large["operation_id"]) >= LARGE_FIELD_CHARS
        assert _db_raw_result_contains(store, large["operation_id"], LARGE_PAYLOAD_MARKER)

        small_rows = store.events(small["job_id"], 0, 1000)
        large_rows = store.events(large["job_id"], 0, 1000)
        assert [row["event"] for row in small_rows] == [row["event"] for row in large_rows]
        assert [row["metadata"] for row in small_rows] == [row["metadata"] for row in large_rows]
        gc.collect()

        small_page, small_peak = _read_with_isolated_tracing(
            store, project_small, {"job_id": small["job_id"], "limit": EVENT_COUNT})
        _assert_complete_page(small_page, small_rows, job_id=small["job_id"],
                              project=project_small, expected_chain=SMALL_CAUSE_CHAIN)
        assert small_peak <= PEAK_ALLOWANCE_BYTES, (
            f"small-result control read exceeded the Python baseline bound ({small_peak} bytes)"
        )
        del small_page
        gc.collect()

        large_page, large_peak = _read_with_isolated_tracing(
            store, project_large, {"job_id": large["job_id"], "limit": EVENT_COUNT})
        _assert_complete_page(large_page, large_rows, job_id=large["job_id"],
                              project=project_large, expected_chain=LARGE_CAUSE_CHAIN)
        del large_page
        gc.collect()

        # Same real call chain only: the cross-project read of the large job
        # must still fail closed, and its error surface must not leak the
        # payload. (store.job() materializes before this gate today; that is
        # part of the same chain this file characterizes, so the rejected
        # read's own peak is deliberately NOT asserted here.)
        with pytest.raises(ExecutionContractError) as cross:
            ops.solver_log_read(store, project_small,
                                {"job_id": large["job_id"], "limit": EVENT_COUNT})
        assert cross.value.code == "PROJECT_IDENTITY_MISMATCH"
        error_text = str(cross.value)
        assert LARGE_PAYLOAD_MARKER not in error_text
        assert len(error_text) < 4096

        # THE proposed memory bound (expected RED today). Report byte counts
        # only; the payload itself is never printed or reconstructed here.
        delta = large_peak - small_peak
        assert delta <= PEAK_ALLOWANCE_BYTES, (
            f"swapping only the stored-result size changed solver.log_read's Python "
            f"tracemalloc peak by {delta} bytes; store.job()/_job/_op materialize the "
            f"entire stored result JSON on every read (small_peak={small_peak}, "
            f"large_peak={large_peak}; payload not printed)"
        )
    finally:
        store.close()
