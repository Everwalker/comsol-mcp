"""T35 result-projection edge tests for ``solver.log_read`` (authored, NOT RUN).

Scope and honesty boundaries (binding for every test in this file):

- Real layers only where claimed: the real ``solver_log_read`` against real
  fresh ``OperationStore(":memory:")`` ledgers with brand-new synthetic
  begin/add_event/finish state. No daemon, ControlDaemon, Worker, COMSOL,
  SSH, model, endpoint, credential, historical/prior DB, or real UNKNOWN job
  is touched. Nothing here uses tmp_path fixtures, disk files, network,
  threads, subprocesses, or DELETE of any kind. Every test closes only its
  own ``":memory:"`` store in ``finally``.
- Direct SQL here is synthesis on this file's own synthetic rows inside its
  own fresh in-memory ledger (duplicate-key synthesis in test 1, truncation
  in test 3), plus metadata-only grounding SELECTs. It never touches a real
  or historical database, and no synthetic corruption/deformation is ever
  repaired, deleted, or rewritten back by the test afterwards.
- JSON semantics reference is CPython ``json.loads`` (a duplicate top-level
  key resolves to the LAST occurrence). A separate fresh-``:memory:``
  parent-side observation on SQLite 3.53.4 is recorded as context ONLY:
  ``json_extract`` resolved duplicate keys to the FIRST item, and
  ``json_each``'s ``id`` is officially guaranteed unique but NOT ordered.
  Both are version-specific behaviours: this file never asserts any
  ``json_extract``/``json_each`` result and never treats SQLite JSON id
  ordering as a cross-version basis. A future SQLite-side bounded result
  projection must match the Python decode this reader performs, not the
  other way around.
- This file patches NO module constants at all (unlike the sibling T35
  capacity file): default ``_MAX_LOG_EVENT_ROW_BYTES`` /
  ``_MAX_LOG_PAGE_JSON_BYTES`` / ``_MAX_LOG_PROGRESS_JSON_BYTES`` paths run
  untouched. The 4 MiB stored-error-subtree bound in test 2 is a
  file-local PROPOSED bound for a future bounded projection; it is not
  enforced by ``comsol_mcp._g3_solver_ops`` today.
- tracemalloc measures the PYTHON allocator only. Nothing here measures or
  certifies SQLite C-level allocations, process RSS, or DB file size, and
  no conclusion about any of those may be drawn from these peaks.
- Current implementation facts these edges pin (from the current source,
  not from a run): every read's ``store.job()`` -> ``_job`` ->
  ``get_operation()`` -> ``_op`` fully ``json.loads`` the stored result with
  no error-subtree bound and no try/except, so an oversized stored chain is
  today returned intact as CAPTURED (test 2 expected RED) while a corrupted
  result column today raises through ``store.job()`` and the reader converts
  it to ``EXECUTION_STATE_UNKNOWN`` (test 3 expected GREEN).
- Test-by-test expected status and first-failure point:
  * test 1 (duplicate top-level error keys + large normal payload):
    expected GREEN today. First assertion that would fire on a wrong
    projection is ``chain["items"] == LAST_WINS_CHAIN`` — a SQLite
    ``json_extract``-style first-key projection would surface the decoy
    chain first.
  * test 2 (oversized stored error subtree, PROPOSED contract): expected
    RED today. Today the read returns normally with the whole oversized
    chain as CAPTURED items, so the first failing assertion is
    ``chain["status"] != "CAPTURED"`` inside the no-raise branch.
  * test 3 (corrupted large result JSON): expected GREEN today via the
    raise branch (``store.job()`` decode fails -> ``EXECUTION_STATE_UNKNOWN``
    refusal). The forbidden silent outcome (corrupt result disguised as
    ``NOT_CAPTURED`` error-free data) is what the else-branch first
    assertion would catch if a future projection regressed.
  * test 4 (small non-error result): expected GREEN today; it pins that a
    genuine no-error read keeps the legacy exact
    ``{"status": "NOT_CAPTURED", "source": None, "items": []}`` shape so
    the fail-closed expectations above never bleed into normal reads.
- This file was authored and never executed; nothing below may be cited as
  a test run or a passed/failed result.
"""
from __future__ import annotations

import gc
import json
import tracemalloc

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

SESSION = "t35-edge-session"

EVENT_COUNT = 3

# 16 MiB single synthetic string field, comparable to the sibling
# result-payload file; its marker sits at the head so grounding searches
# need one short token and no assertion ever prints the payload.
LARGE_FIELD_CHARS = 16 * 1024 * 1024
LARGE_PAYLOAD_MARKER = "T35-EDGE-LARGE-NORMAL-PAYLOAD-NO-ECHO-MARKER"

FIRST_DECOY_CODE = "T35_EDGE_FIRST_DECOY"
FIRST_DECOY_MARKER = "T35-EDGE-FIRST-DUPLICATE-ERROR-DECOY"
LAST_WINS_CODE = "T35_EDGE_LAST_WINS"
LAST_WINS_MARKER = "T35-EDGE-LAST-DUPLICATE-ERROR-WINS"
LAST_WINS_CHAIN = [
    {"type": "T35EdgeLastWinsException", "message": LAST_WINS_MARKER + "-chain-1"},
]

# Python-allocator regression allowance for the tiny single-key control read.
PEAK_ALLOWANCE_BYTES = 4 * 1024 * 1024

# PROPOSED (file-local, not enforced by the module today) bound for one
# stored result.error exception-chain subtree returned by a bounded
# projection. The test 2 chain is built clearly past it.
PROPOSED_ERROR_CHAIN_BOUND_BYTES = 4 * 1024 * 1024
OVERSIZE_EXTRA_BYTES = 512 * 1024
OVERSIZE_CHAIN_MARKER = "T35-EDGE-OVERSIZED-STORED-CHAIN-NO-ECHO-MARKER"

CORRUPT_CHAIN_MARKER = "T35-EDGE-CORRUPTED-RESULT-CHAIN-MARKER"
CORRUPT_PAD_CHARS = 2 * 1024 * 1024

SMALL_CAUSE_CHAIN = [
    {"type": "T35EdgeSmallException", "message": "t35-edge-small-cause-1"},
]

# A refusal/diagnostic surface must stay small enough to log safely.
ERROR_SURFACE_MAX_CHARS = 4096


def _canonical_chain_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _start_job(store: OperationStore, *, project: str, tag: str) -> dict:
    """Real store.begin with the complete project/execution binding that
    ``_g3_solver_ops._job_project_binding`` requires for both rows."""
    record, reused = store.begin(
        request_id=f"t35-edge-req-{tag}",
        idempotency_key=f"t35-edge-key-{tag}",
        request_hash=f"t35-edge-hash-{tag}",
        operation="study.run",
        metadata={
            "operation": "study.run",
            "arguments": {},
            "execution": {"project_id": project, "session_id": SESSION},
        },
    )
    assert reused is False and record["job_id"] and record["operation_id"]
    return record


def _add_small_events(store: OperationStore, job_id: str) -> None:
    for index in range(EVENT_COUNT):
        store.add_event(job_id, f"T35EdgeEvent{index}", {"index": index})


def _finish_failed(store: OperationStore, operation_id: str, result: dict) -> None:
    accepted, authoritative = store.finish(operation_id, status="FAILED", result=result)
    assert accepted is True and authoritative == "FAILED"


def _db_result_raw(store: OperationStore, operation_id: str) -> str:
    return store.db.execute(
        "SELECT result FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()[0]


def _db_result_length(store: OperationStore, operation_id: str) -> int:
    return store.db.execute(
        "SELECT LENGTH(result) FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()[0]


def _db_job_status(store: OperationStore, job_id: str) -> str:
    return store.db.execute(
        "SELECT status FROM jobs WHERE job_id=?", (job_id,)
    ).fetchone()[0]


def _db_operation_status(store: OperationStore, operation_id: str) -> str:
    return store.db.execute(
        "SELECT status FROM operations WHERE operation_id=?", (operation_id,)
    ).fetchone()[0]


def _stored_rows(store: OperationStore, job_id: str) -> list[dict]:
    return store.events(job_id, 0, 1000)


def _rows_signature(rows: list[dict]) -> list[tuple]:
    return [(row["id"], row["event"], json.dumps(row["metadata"], sort_keys=True))
            for row in rows]


def _assert_complete_small_page(page: dict, rows: list[dict], *, job_id: str,
                                project: str) -> None:
    """Output-completeness proof shared by the paging-independent reads.
    Event payloads are tiny, so no message can echo a large payload."""
    assert page["job_id"] == job_id
    assert page["project_id"] == project
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


def _expected_event_names() -> list[str]:
    return [f"T35EdgeEvent{index}" for index in range(EVENT_COUNT)]


def _expected_event_metadata() -> list[dict]:
    return [{"index": index} for index in range(EVENT_COUNT)]


def _read_with_isolated_tracing(store: OperationStore, project: str,
                                arguments: dict) -> tuple[dict, int]:
    """One solver.log_read inside a tracemalloc session (PYTHON allocator
    only; SQLite C allocations and RSS are outside this measurement and are
    never inferred from it). Fails closed, without stopping foreign state,
    if tracing is already active; always stops its own session."""
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


def test_t35_edge_duplicate_top_level_error_keys_follow_python_json_last_wins():
    """Expected GREEN today; first wrong-projection tripwire is
    ``chain["items"] == LAST_WINS_CHAIN``.

    A brand-new synthetic FAILED job is finished normally, then its durable
    ``operations.result`` TEXT is replaced (own-row direct SQL) with one
    VALID JSON document whose top level contains a 16 MiB normal (non-error)
    field plus TWO top-level ``"error"`` keys: a first-position decoy error
    with its own chain and a later small error whose ``cause_chain`` is the
    accurate chain. The current reader decodes the whole result with CPython
    ``json.loads``, so duplicate-key LAST-wins semantics must hold: the
    exposed ``exception_chain`` equals exactly the last key's chain, never
    the decoy. An independent positive control re-decodes the exact durable
    text with pure ``json.loads`` and shows the same last-key selection,
    pinning the semantics reference itself. (A parent-side SQLite 3.53.4
    observation that ``json_extract`` returns the FIRST duplicate item is
    context only: it is not asserted here and must not be relied on across
    SQLite versions.)

    The large non-error payload must not disturb chain accuracy, page
    completeness, or the durable terminal state, and neither the payload
    marker nor the decoy marker may appear in the returned chain surface.
    One tiny single-key control job proves the same chain source shape on
    the cheap path with a bounded Python-allocator peak; the 16 MiB read is
    deliberately NOT peak-bounded here (the sibling result-payload file owns
    that peak pair)."""
    project = "t35-edge-dupkeys-project"
    control_project = "t35-edge-dupkeys-control-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="dupkeys")
        job_id, operation_id = record["job_id"], record["operation_id"]
        _add_small_events(store, job_id)
        _finish_failed(store, operation_id,
                       {"success": False, "note": "t35-edge-placeholder"})

        large_field = LARGE_PAYLOAD_MARKER + "L" * (LARGE_FIELD_CHARS - len(LARGE_PAYLOAD_MARKER))
        first_error = ('{"code": "' + FIRST_DECOY_CODE + '", "message": "' + FIRST_DECOY_MARKER + '", '
                       '"cause_chain": [{"type": "T35EdgeFirstDecoyChain", "message": "'
                       + FIRST_DECOY_MARKER + '-chain"}]}')
        last_error = ('{"code": "' + LAST_WINS_CODE + '", "message": "' + LAST_WINS_MARKER + '", '
                      '"cause_chain": [{"type": "T35EdgeLastWinsException", "message": "'
                      + LAST_WINS_MARKER + '-chain-1"}]}')
        synthesized = ("{" + f'"large_data": "{large_field}", '
                       + '"note": "t35-edge-dupkeys", '
                       + '"error": ' + first_error + ", "
                       + '"error": ' + last_error + ", "
                       + '"success": false}')
        store.db.execute("UPDATE operations SET result=? WHERE operation_id=?",
                         (synthesized, operation_id))
        del large_field, first_error, last_error, synthesized

        db_raw = _db_result_raw(store, operation_id)
        assert db_raw.count('"error"') == 2
        assert FIRST_DECOY_MARKER in db_raw and LAST_WINS_MARKER in db_raw
        assert LARGE_PAYLOAD_MARKER in db_raw
        assert _db_result_length(store, operation_id) >= LARGE_FIELD_CHARS

        # Independent positive control: pure CPython json.loads on the exact
        # durable text resolves the duplicate key to the LAST occurrence.
        control = json.loads(db_raw)
        assert control["error"]["code"] == LAST_WINS_CODE
        assert control["error"]["cause_chain"] == LAST_WINS_CHAIN
        del db_raw, control

        # The synthesis kept the durable terminal state intact.
        assert _db_job_status(store, job_id) == "FAILED"
        assert _db_operation_status(store, operation_id) == "FAILED"
        rows = _stored_rows(store, job_id)
        assert len(rows) == EVENT_COUNT

        # Tiny single-key control: same chain source/shape on the cheap path,
        # with a Python-only peak bound (SQLite C/RSS are not measured).
        small = _start_job(store, project=control_project, tag="dupkeys-ctl")
        _add_small_events(store, small["job_id"])
        _finish_failed(store, small["operation_id"], {
            "success": False,
            "note": "t35-edge-small-single-key",
            "error": {"code": "T35_EDGE_SMALL_SYNTHETIC", "message": "small synthetic failure",
                      "cause_chain": SMALL_CAUSE_CHAIN},
        })
        gc.collect()
        small_page, small_peak = _read_with_isolated_tracing(
            store, control_project, {"job_id": small["job_id"], "limit": EVENT_COUNT})
        assert small_page["exception_chain"]["status"] == "CAPTURED"
        assert small_page["exception_chain"]["source"] == "stored result.error.cause_chain"
        assert small_page["exception_chain"]["items"] == SMALL_CAUSE_CHAIN
        assert small_peak <= PEAK_ALLOWANCE_BYTES, (
            f"tiny single-key read already exceeded the Python baseline bound ({small_peak} bytes)"
        )
        del small_page
        gc.collect()

        # THE edge read: 16 MiB normal payload + duplicate top-level errors.
        page = ops.solver_log_read(store, project, {"job_id": job_id, "limit": EVENT_COUNT})
        _assert_complete_small_page(page, rows, job_id=job_id, project=project)
        assert page["job_status"] == "FAILED"
        chain = page["exception_chain"]
        assert chain["status"] == "CAPTURED"
        assert chain["source"] == "stored result.error.cause_chain"
        assert chain["items"] == LAST_WINS_CHAIN
        surface = json.dumps(chain, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        assert len(surface) < ERROR_SURFACE_MAX_CHARS
        assert FIRST_DECOY_MARKER not in surface and FIRST_DECOY_CODE not in surface, (
            "the reader must follow CPython json.loads duplicate-key last-wins, "
            "not a first-key selection"
        )
        assert LARGE_PAYLOAD_MARKER not in surface, (
            "the normal large payload must never be echoed through exception_chain"
        )
    finally:
        store.close()


def test_t35_edge_oversized_stored_error_chain_fails_closed_not_echoed():
    """PROPOSED-contract test, expected RED today; first failing assertion
    against the current implementation is ``chain["status"] != "CAPTURED"``
    in the no-raise branch (today the read succeeds and returns the whole
    oversized chain as CAPTURED items).

    One fresh in-memory ledger holds one brand-new synthetic FAILED job:
    two tiny events (the first carrying a SMALL decoy ``exception_chain``)
    and a stored ``result.error.cause_chain`` subtree whose canonical JSON is
    > the file-local PROPOSED 4 MiB bound and carries a head marker.

    Under the proposed bounded-projection contract, a read that cannot
    return that subtree completely must fail closed with a controlled
    outcome on every page: either an ``ExecutionContractError`` with code
    ``EXECUTION_STATE_UNKNOWN`` (small, marker-free surface) or a returned
    page whose ``exception_chain.status`` is exactly ``UNKNOWN`` with no
    items. Explicitly forbidden outcomes:
    ``NOT_CAPTURED`` (the stored error silently vanishing), a fake-complete
    ``CAPTURED`` echo of the oversized payload, automatic substitution of
    the small event decoy chain as the source, and any marker echo in a
    refusal surface. Whatever the outcome, the durable chain, terminal
    states, and stored events must be left untouched afterwards. The
    4 MiB bound is proposed here only; the module enforces no
    stored-error-subtree bound today."""
    project = "t35-edge-oversize-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="oversize")
        job_id, operation_id = record["job_id"], record["operation_id"]
        store.add_event(job_id, "T35EdgePlain", {"n": 0})
        store.add_event(job_id, "T35EdgeDecoyChain",
                        {"exception_chain": [{"type": "T35EdgeDecoyEventChain",
                                              "message": "decoy-small-chain"}]})

        huge_message = (OVERSIZE_CHAIN_MARKER
                        + "E" * (PROPOSED_ERROR_CHAIN_BOUND_BYTES + OVERSIZE_EXTRA_BYTES
                                 - len(OVERSIZE_CHAIN_MARKER)))
        huge_chain = [{"type": "T35EdgeOversizedOuter", "message": huge_message}]
        del huge_message
        assert _canonical_chain_bytes(huge_chain) > PROPOSED_ERROR_CHAIN_BOUND_BYTES
        _finish_failed(store, operation_id, {
            "success": False,
            "note": "t35-edge-oversize",
            "error": {"code": "T35_EDGE_OVERSIZED_SYNTHETIC",
                      "message": "oversized stored error subtree",
                      "cause_chain": huge_chain},
        })
        del huge_chain

        pre_len = _db_result_length(store, operation_id)
        assert pre_len > PROPOSED_ERROR_CHAIN_BOUND_BYTES
        rows = _stored_rows(store, job_id)
        assert len(rows) == 2

        try:
            page = ops.solver_log_read(store, project, {"job_id": job_id, "limit": 10})
        except ExecutionContractError as exc:
            assert exc.code == "EXECUTION_STATE_UNKNOWN", (
                "an uncertifiable over-bound stored error subtree must fail closed as "
                "controlled EXECUTION_STATE_UNKNOWN"
            )
            text = str(exc)
            assert OVERSIZE_CHAIN_MARKER not in text, "refusal must not echo the oversized marker"
            assert len(text) <= ERROR_SURFACE_MAX_CHARS
        else:
            chain = page["exception_chain"]
            assert chain["status"] != "CAPTURED", (
                "a stored error subtree beyond the proposed bound must not be certified "
                "as CAPTURED with its oversized items"
            )
            assert chain["status"] != "NOT_CAPTURED", (
                "a genuinely stored error must never degrade to NOT_CAPTURED"
            )
            assert chain["status"] == "UNKNOWN"
            assert chain.get("items") in ([], None)
            source = chain.get("source")
            assert not (isinstance(source, str) and (source.startswith("event ")
                                                     or source.startswith("worker request"))), (
                "auto-selecting an event/worker candidate under size pressure is a "
                "source substitution, not a bounded projection"
            )
            surface = json.dumps(chain, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), default=str)
            assert OVERSIZE_CHAIN_MARKER not in surface
            assert len(surface) <= ERROR_SURFACE_MAX_CHARS
            assert [event["id"] for event in page["events"]] == [row["id"] for row in rows]
            assert page["job_status"] == "FAILED"

        # Regardless of outcome, the read must not damage durable state.
        assert _db_job_status(store, job_id) == "FAILED"
        assert _db_operation_status(store, operation_id) == "FAILED"
        assert _db_result_length(store, operation_id) == pre_len
        post_raw = _db_result_raw(store, operation_id)
        assert OVERSIZE_CHAIN_MARKER in post_raw, (
            "the stored chain is audit data; the reader must not silently truncate it"
        )
        del post_raw
        assert _rows_signature(_stored_rows(store, job_id)) == _rows_signature(rows)
    finally:
        store.close()


def test_t35_edge_corrupted_large_result_json_never_disguises_as_error_free():
    """Expected GREEN today through the raise branch (the current
    ``store.job()`` -> ``_op`` ``json.loads`` raises and
    ``solver_log_read`` converts it to an ``EXECUTION_STATE_UNKNOWN``
    refusal whose first branch assertion is the code check). The else
    branch's first assertion (``status != "NOT_CAPTURED"``) is the
    tripwire for a future projection that would treat a
    ``json_valid``-failing result as error-free data.

    A fresh in-memory job is finished FAILED with a ~2 MiB valid result
    whose ``error.cause_chain`` carries a marker early in the canonical
    text; the durable result is then truncated mid-string by direct SQL on
    this file's own row (grounded by keeping a pad character at the cut, so
    invalidity is proven without relying on any SQLite JSON function). A
    naive first-key/regex extractor could still see error-looking bytes in
    the truncated prefix, which makes the disguise risk concrete.

    The read must either fail closed as ``EXECUTION_STATE_UNKNOWN`` (small,
    marker-free surface) or return a page whose ``exception_chain.status``
    is an explicit ``UNKNOWN``; a ``NOT_CAPTURED`` disguise is forbidden.
    Afterwards the corrupted text, terminal states, and stored events must
    all be byte-for-byte what the test left them as — the reader must not
    repair, truncate, or rewrite durable data."""
    project = "t35-edge-corrupt-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="corrupt")
        job_id, operation_id = record["job_id"], record["operation_id"]
        _add_small_events(store, job_id)
        _finish_failed(store, operation_id, {
            "success": False,
            "note": "t35-edge-corrupt",
            "pad": "C" * CORRUPT_PAD_CHARS,
            "error": {"code": "T35_EDGE_CORRUPT_SYNTHETIC",
                      "message": CORRUPT_CHAIN_MARKER + " intact-chain",
                      "cause_chain": [{"type": "T35EdgePreCorruptionChain",
                                       "message": CORRUPT_CHAIN_MARKER}]},
        })
        full_raw = _db_result_raw(store, operation_id)
        cut = len(full_raw) // 2
        truncated = full_raw[:cut]
        # Grounded invalidity: the cut lands inside the pad string, leaving
        # an unterminated JSON string (no SQLite json function relied on).
        assert full_raw[cut - 1] == "C" and CORRUPT_CHAIN_MARKER in truncated
        store.db.execute("UPDATE operations SET result=? WHERE operation_id=?", (truncated, operation_id))
        del full_raw, truncated

        assert _db_result_length(store, operation_id) == cut
        assert _db_job_status(store, job_id) == "FAILED"
        assert _db_operation_status(store, operation_id) == "FAILED"
        rows = _stored_rows(store, job_id)
        assert len(rows) == EVENT_COUNT

        try:
            page = ops.solver_log_read(store, project, {"job_id": job_id, "limit": EVENT_COUNT})
        except ExecutionContractError as exc:
            assert exc.code == "EXECUTION_STATE_UNKNOWN", (
                "a corrupted durable result must fail closed, never pass silently"
            )
            text = str(exc)
            assert CORRUPT_CHAIN_MARKER not in text, "refusal must not echo corrupt payload text"
            assert len(text) <= ERROR_SURFACE_MAX_CHARS
        else:
            chain = page["exception_chain"]
            assert chain["status"] != "NOT_CAPTURED", (
                "corrupted stored result JSON must never be disguised as having no error"
            )
            assert chain["status"] == "UNKNOWN"
            assert chain.get("items") in ([], None)

        # Durable corruption stays exactly as synthesized; nothing repaired,
        # truncated further, or deleted.
        assert _db_job_status(store, job_id) == "FAILED"
        assert _db_operation_status(store, operation_id) == "FAILED"
        post_raw = _db_result_raw(store, operation_id)
        assert len(post_raw) == cut and CORRUPT_CHAIN_MARKER in post_raw
        del post_raw
        assert _rows_signature(_stored_rows(store, job_id)) == _rows_signature(rows)
    finally:
        store.close()


def test_t35_edge_small_nonerror_result_keeps_legacy_not_captured():
    """Expected GREEN today; first assertion pins ``job_status`` and the
    exact legacy ``exception_chain`` dict.

    Regression guard for the non-error path: a brand-new synthetic
    SUCCEEDED job with a tiny result (no ``error`` key at all) and tiny
    events must keep the pre-existing behaviour byte-for-byte — a complete
    page, frozen snapshot digests equal to the direct whole-list helpers,
    and ``exception_chain`` exactly ``{"status": "NOT_CAPTURED", "source":
    None, "items": []}``. This pins that the fail-closed/UNKNOWN
    expectations above apply only to oversized or corrupted stored errors
    and never turn a genuine no-error read into an UNKNOWN refusal."""
    project = "t35-edge-nonerror-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="nonerror")
        job_id, operation_id = record["job_id"], record["operation_id"]
        _add_small_events(store, job_id)
        accepted, authoritative = store.finish(
            operation_id, status="SUCCEEDED",
            result={"success": True, "note": "t35-edge-nonerror"})
        assert accepted is True and authoritative == "SUCCEEDED"
        assert _db_result_length(store, operation_id) < 4096
        rows = _stored_rows(store, job_id)
        assert len(rows) == EVENT_COUNT

        page = ops.solver_log_read(store, project, {"job_id": job_id, "limit": EVENT_COUNT})
        assert page["job_status"] == "SUCCEEDED"
        _assert_complete_small_page(page, rows, job_id=job_id, project=project)
        assert page["exception_chain"] == {"status": "NOT_CAPTURED", "source": None, "items": []}
        assert _db_job_status(store, job_id) == "SUCCEEDED"
    finally:
        store.close()
