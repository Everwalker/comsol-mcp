"""T35 vertical RED tests: real ``solver.log_read`` past the ordinary log length.

Scope and honesty boundaries (binding for every test in this file):

- Real layers only where claimed: the real unbound
  ``comsol_mcp._g3_solver_ops.solver_log_read`` against a real fresh
  ``OperationStore(":memory:")`` ledger with real begin/finish/add_event/
  events/collect_job_event_snapshot state. No daemon, ControlDaemon, Worker,
  COMSOL, SSH, model, or historical UNKNOWN job is touched; every job and
  event here is brand-new synthetic.
- Authorization is enforced one layer before ``solver_log_read`` (the route's
  project authority gate). This file calls ``solver_log_read`` directly and
  therefore does NOT validate, and must not be cited as validating, real
  project authorization.
- Tests 2 and 3 encode the PROPOSED T35 acceptance contract: past the
  ordinary snapshot page-cap the reader must still page successfully, return
  memory-bounded reads, expose all primary evidence, and keep the terminal
  state recordable, WITHOUT repeatedly raising the store constants. They are
  expected RED against the current implementation. They do not claim the old
  contract previously guaranteed success past the cap.
- The reduced-constant patch in test 2 (JOB_EVENT_SNAPSHOT_MAX_PAGES 128->1,
  default page_size 1000 untouched) is a capacity simulation, not the real
  128000-event threshold. The 4 MiB delta threshold in test 3 is a regression
  threshold for THIS synthetic sample pair, not a global production limit.
- tracemalloc measures Python-level allocations only. Nothing here measures
  or proves anything about SQLite C-level memory, process RSS, disk usage, or
  the memory of a single gigantic event row.
- The store-module constant patch and the tracemalloc session are each
  restored/stopped in ``finally``; if tracemalloc is already tracing when a
  memory test starts, that test fails closed and never stops user state.
"""
from __future__ import annotations

import tracemalloc

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp import _operation_store as ops_store
from comsol_mcp._operation_store import OperationStore

SESSION = "t35-solver-log-stream-session"
CAUSE_CHAIN = [
    {"type": "T35SyntheticSolverException", "message": "synthetic-cause-layer-1"},
    {"type": "T35SyntheticEngineException", "message": "synthetic-cause-layer-2"},
]

# Synthetic near-2KiB rows: payload is exactly this many characters, so the
# serialized metadata row lands just under two kibibytes.
PAYLOAD_CHARS = 1900
# Metadata JSON adds keys/index/payload framing on top of the payload itself.
ROW_LENGTH_SLACK = 160

SMALL_SAMPLE_EVENTS = 256
LARGE_SAMPLE_EVENTS = 4096
TRACED_PEAK_DELTA_ALLOWANCE_BYTES = 4 * 1024 * 1024


def _synthetic_payload(index: int) -> str:
    return f"{index:06d}:" + "x" * (PAYLOAD_CHARS - len(f"{index:06d}:"))


def _start_synthetic_job(store: OperationStore, *, project: str, request_tag: str) -> dict:
    """Real store.begin with the complete project/execution binding that
    _g3_solver_ops._job_project_binding requires for both job and operation."""
    record, reused = store.begin(
        request_id=f"t35-red-request-{request_tag}",
        idempotency_key=f"t35-red-key-{request_tag}",
        request_hash=f"t35-red-hash-{request_tag}",
        operation="study.run",
        metadata={
            "operation": "study.run",
            "arguments": {},
            "execution": {"project_id": project, "session_id": SESSION},
        },
    )
    assert reused is False
    assert record["job_id"] and record["operation_id"]
    return record


def _write_synthetic_events(store: OperationStore, job_id: str, *, family: str,
                            count: int, terminal_at: int | None = None) -> dict:
    """Write `count` small synthetic events; optionally place a manual
    terminal-marker event at `terminal_at`. Returns only the first written
    event's expected shape (for exact readback checks) so callers never have
    to retain the full sample list during a measurement."""
    first_expected = None
    for index in range(count):
        is_terminal = terminal_at is not None and index == terminal_at
        name = "T35SyntheticTerminalMarker" if is_terminal else "T35SyntheticProgress"
        metadata = {"family": family, "index": index, "payload": _synthetic_payload(index)}
        if index == 0:
            first_expected = {"event": name, "metadata": dict(metadata)}
        store.add_event(job_id, name, metadata)
    assert first_expected is not None and first_expected["metadata"]["index"] == 0
    return first_expected


def _db_event_count(store: OperationStore, job_id: str) -> int:
    return store.db.execute(
        "SELECT COUNT(*) FROM job_events WHERE job_id=?", (job_id,)
    ).fetchone()[0]


def _db_event_row_length_bounds(store: OperationStore, job_id: str) -> tuple[int, int]:
    row = store.db.execute(
        "SELECT MIN(LENGTH(metadata)),MAX(LENGTH(metadata)) FROM job_events WHERE job_id=?",
        (job_id,),
    ).fetchone()
    return int(row[0]), int(row[1])


def _stored_event_ids(store: OperationStore, job_id: str) -> list[int]:
    """Independent readback of stored event order through the real
    store.events paging API (each call is capped at 1000 rows)."""
    ids: list[int] = []
    offset = 0
    while True:
        rows = store.events(job_id, offset, 1000)
        if not rows:
            return ids
        ids.extend(row["id"] for row in rows)
        if len(rows) < 1000:
            return ids
        offset += 1000


def _assert_summary_stable(page: dict, baseline: dict) -> None:
    snapshot = page["snapshot"]
    base_snapshot = baseline["snapshot"]
    for key in ("id_sha256", "events_sha256", "event_count", "last_event_id"):
        assert snapshot[key] == base_snapshot[key], f"summary field {key!r} drifted across pages"
    assert page["job_status"] == baseline["job_status"]
    assert page["solver_output_log"]["status"] == baseline["solver_output_log"]["status"]
    assert page["exception_chain"]["status"] == baseline["exception_chain"]["status"]


def test_t35_solver_log_read_within_cap_pages_remain_positive_control():
    """Positive control at genuine defaults (MAX_PAGES=128, PAGE_SIZE=1000):
    a 50-event job pages cleanly, freezes its cursor prefix, and stays stable."""
    project = "t35-red-cap-positive-project"
    store = OperationStore(":memory:")
    try:
        record = _start_synthetic_job(store, project=project, request_tag="cap-positive")
        job_id = record["job_id"]
        first_expected = _write_synthetic_events(store, job_id, family="cap-positive", count=50)
        assert _db_event_count(store, job_id) == 50

        pages: list[dict] = []
        cursor = None
        for _guard in range(6):
            arguments = {"job_id": job_id, "limit": 20}
            if cursor is not None:
                arguments["cursor"] = cursor
            page = ops.solver_log_read(store, project, arguments)
            pages.append(page)
            cursor = page["next_cursor"]
            if cursor is None:
                break
            if pages and page["event_count"] == 0:
                pytest.fail("cursor continuation returned an empty page before the end")
        assert cursor is None
        assert len(pages) == 3
        assert [page["event_count"] for page in pages] == [20, 20, 10]
        for page in pages[1:]:
            _assert_summary_stable(page, pages[0])

        collected = [event for page in pages for event in page["events"]]
        assert [event["id"] for event in collected] == _stored_event_ids(store, job_id)
        assert len(set(event["id"] for event in collected)) == 50
        assert pages[0]["events"][0]["event"] == first_expected["event"]
        assert pages[0]["events"][0]["metadata"] == first_expected["metadata"]
        assert pages[0]["snapshot"]["event_count"] == 50

        # A post-cursor append must never leak into the frozen continuation.
        store.add_event(job_id, "T35PostCursorOverflow", {"family": "cap-positive", "note": "late append"})
        continuation = ops.solver_log_read(store, project,
                                           {"job_id": job_id, "cursor": pages[1]["next_cursor"], "limit": 20})
        assert continuation["event_count"] == 10
        assert continuation["next_cursor"] is None
        assert all(event["event"] != "T35PostCursorOverflow" for event in continuation["events"])
        assert continuation["snapshot"]["event_count"] == 50
        assert _assert_summary_stable(continuation, pages[0]) is None
    finally:
        store.close()


def test_t35_solver_log_read_past_snapshot_cap_pages_expose_full_event_evidence_and_terminal_state():
    """PROPOSED-contract RED test: past the reduced snapshot page cap the real
    solver.log_read must still page successfully.

    With JOB_EVENT_SNAPSHOT_MAX_PAGES temporarily 128->1 (default
    page_size=1000 untouched), a 1001-event job exceeds the cap. The current
    collector marks the snapshot INCOMPLETE and solver_log_read refuses; the
    assertion below instead demands the proposed bounded behaviour: full
    unique-id coverage, stable summaries, a readable terminal marker and
    stored cause chain, and a cursor that freezes the 1002-event prefix even
    after a 1003rd event is appended. This is not a claim that the old
    contract previously guaranteed past-cap success.
    """
    project = "t35-red-past-cap-project"
    original_max_pages = ops_store.JOB_EVENT_SNAPSHOT_MAX_PAGES
    assert original_max_pages == 128, "store default MAX_PAGES drifted from the reviewed baseline"
    assert ops_store.JOB_EVENT_SNAPSHOT_PAGE_SIZE == 1000, "default page size must stay untouched"
    store = OperationStore(":memory:")
    try:
        ops_store.JOB_EVENT_SNAPSHOT_MAX_PAGES = 1
        record = _start_synthetic_job(store, project=project, request_tag="past-cap")
        job_id, operation_id = record["job_id"], record["operation_id"]

        # Cross the reduced cap before recording the terminal state.
        terminal_at = 1001
        first_expected = _write_synthetic_events(store, job_id, family="past-cap",
                                                 count=terminal_at)
        assert _db_event_count(store, job_id) == 1001

        # Record the terminal state and a synthetic marker after overflow.
        accepted, authoritative = store.finish(
            operation_id, status="FAILED",
            result={"success": False, "error": {
                "code": "T35_SYNTHETIC_SOLVER_FAILED",
                "message": "synthetic failure recorded past the ordinary log length",
                "cause_chain": CAUSE_CHAIN,
            }},
        )
        assert accepted is True and authoritative == "FAILED"
        store.add_event(job_id, "T35SyntheticTerminalMarker",
                        {"family": "past-cap", "index": terminal_at,
                         "payload": _synthetic_payload(terminal_at)})
        assert _db_event_count(store, job_id) == 1002
        terminal_job = store.job(job_id)
        assert terminal_job is not None and terminal_job["status"] == "FAILED"
        assert terminal_job["result"]["error"]["cause_chain"] == CAUSE_CHAIN
        frozen_ids = _stored_event_ids(store, job_id)
        assert len(frozen_ids) == 1002

        page1 = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 1000})
        assert page1["event_count"] == 1000
        assert page1["job_status"] == "FAILED"
        assert page1["next_cursor"], "past-cap read must still hand out a v1 cursor"
        assert page1["snapshot"]["status"] == "COMPLETE"
        assert page1["snapshot"]["event_count"] == 1002
        # The snapshot bound is the whole frozen 1002-event prefix, not the
        # last event on page 1.
        assert page1["snapshot"]["last_event_id"] == frozen_ids[1001]
        assert [event["id"] for event in page1["events"]] == frozen_ids[:1000]
        assert page1["events"][0]["event"] == first_expected["event"]
        assert page1["events"][0]["metadata"] == first_expected["metadata"]
        assert page1["exception_chain"]["status"] == "CAPTURED"
        assert page1["exception_chain"]["items"] == CAUSE_CHAIN

        # Append one post-cursor overflow event before continuing.
        store.add_event(job_id, "T35PostCursorOverflow", {"family": "past-cap", "index": 1002})
        assert _db_event_count(store, job_id) == 1003

        page2 = ops.solver_log_read(store, project,
                                    {"job_id": job_id, "cursor": page1["next_cursor"], "limit": 1000})
        assert page2["event_count"] == 2
        assert page2["next_cursor"] is None
        assert page2["events"][-1]["event"] == "T35SyntheticTerminalMarker"
        assert page2["events"][-1]["metadata"]["index"] == terminal_at
        assert page2["job_status"] == "FAILED"
        assert page2["exception_chain"]["status"] == "CAPTURED"
        assert page2["exception_chain"]["items"] == CAUSE_CHAIN
        _assert_summary_stable(page2, page1)

        collected = page1["events"] + page2["events"]
        stored_ids = _stored_event_ids(store, job_id)
        assert [event["id"] for event in collected] == stored_ids[:1002]
        assert len(set(event["id"] for event in collected)) == 1002
        overflow_id = stored_ids[1002]
        assert all(event["id"] != overflow_id for event in collected), \
            "continuation must cover only the 1002 events frozen by the first page"
        assert collected[-1]["event"] == "T35SyntheticTerminalMarker"
        assert collected[-1]["metadata"]["index"] == terminal_at
    finally:
        ops_store.JOB_EVENT_SNAPSHOT_MAX_PAGES = original_max_pages
        store.close()


def test_t35_solver_log_read_single_page_traced_peak_is_bounded_across_synthetic_sample_sizes():
    """PROPOSED-contract RED test: one solver.log_read(limit=1) call must not
    have its Python-traced peak scale with total event count.

    Fixtures (256 vs 4096 near-2KiB synthetic events, each in its own fresh
    ":memory:" store under the real default caps) are fully built BEFORE any
    tracemalloc session starts, so the traced region covers only the single
    real read call — never the fixture list. The 4 MiB delta threshold is a
    regression guard for this synthetic pair, not a production memory bound;
    tracemalloc sees no SQLite C memory, RSS, disk, or single-large-event
    memory, and nothing here claims otherwise.
    """
    if tracemalloc.is_tracing():
        pytest.fail("tracemalloc is already tracing in this process; this test "
                    "refuses to start or stop a session it does not own (fail-closed)")

    started = False
    stores: list[OperationStore] = []
    try:
        def build_sample(*, event_count: int, request_tag: str) -> dict:
            project = f"t35-red-bounded-{request_tag}-project"
            store = OperationStore(":memory:")
            stores.append(store)
            record = _start_synthetic_job(store, project=project, request_tag=request_tag)
            first_expected = _write_synthetic_events(store, record["job_id"],
                                                     family=request_tag, count=event_count)
            assert _db_event_count(store, record["job_id"]) == event_count
            low, high = _db_event_row_length_bounds(store, record["job_id"])
            assert PAYLOAD_CHARS <= low <= high <= PAYLOAD_CHARS + ROW_LENGTH_SLACK, \
                f"synthetic rows must stay near-2KiB; observed metadata lengths {low}..{high}"
            return {"store": store, "project": project, "job_id": record["job_id"],
                    "event_count": event_count, "first_expected": first_expected}

        # Both fixtures are constructed with tracemalloc OFF.
        small = build_sample(event_count=SMALL_SAMPLE_EVENTS, request_tag="small")
        large = build_sample(event_count=LARGE_SAMPLE_EVENTS, request_tag="large")

        def measure_traced_peak(sample: dict) -> tuple[int, dict]:
            nonlocal started
            tracemalloc.start()
            started = True
            try:
                response = ops.solver_log_read(sample["store"], sample["project"],
                                               {"job_id": sample["job_id"], "limit": 1})
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
                started = False
            return peak, response

        small_peak, small_response = measure_traced_peak(small)
        large_peak, large_response = measure_traced_peak(large)

        for sample, response in ((small, small_response), (large, large_response)):
            assert response["job_id"] == sample["job_id"]
            assert response["project_id"] == sample["project"]
            assert response["job_status"] == "QUEUED"
            assert response["event_count"] == 1 and len(response["events"]) == 1
            assert response["snapshot"]["event_count"] == sample["event_count"]
            assert response["snapshot"]["status"] == "COMPLETE"
            only_event = response["events"][0]
            assert only_event["event"] == sample["first_expected"]["event"]
            assert only_event["metadata"] == sample["first_expected"]["metadata"]

        assert large_peak - small_peak <= TRACED_PEAK_DELTA_ALLOWANCE_BYTES, (
            f"single-page solver.log_read traced peak scaled with event count: "
            f"small(256)={small_peak} large(4096)={large_peak} "
            f"delta={large_peak - small_peak} > {TRACED_PEAK_DELTA_ALLOWANCE_BYTES}"
        )
    finally:
        if started:
            tracemalloc.stop()
        for store in stores:
            store.close()
