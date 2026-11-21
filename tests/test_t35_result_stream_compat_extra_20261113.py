"""T35 stream-compat extras: giant stored results whose corruption lives in a
NON-error subtree, and Python-legal NaN/Infinity compatibility for
``solver.log_read`` (authored, NOT RUN).

Scope and honesty boundaries (binding for every test in this file):

- Real layers only where claimed: the real ``solver_log_read`` (the same
  streaming-fold reader published in the frozen parent snapshot
  ``_state/t35-log-overflow-parent-20261112/g3_solver_ops_scope_consistency_parent_20261112.py.txt``
  and in the live ``comsol_mcp._g3_solver_ops``) against real fresh
  ``OperationStore(":memory:")`` ledgers with brand-new synthetic
  begin/add_event/finish state. No daemon, ControlDaemon, Worker, COMSOL,
  SSH, model, endpoint, credential, historical/prior DB, or real UNKNOWN job
  is touched. Nothing here uses tmp_path fixtures, disk files, network,
  threads, subprocesses, or DELETE of any kind. Every test closes only its
  own ``":memory:"`` store in ``finally``.
- Direct SQL here is synthesis restricted to THIS FILE's own synthetic rows
  inside its own fresh in-memory ledger (``UPDATE operations SET result=?``
  after a normal ``store.finish``, plus grounding SELECTs). Every write is
  fully parameterized and grounded before execution: placeholder count ==
  bound-parameter count, the WHERE parameter is exactly this job's
  operation_id, no payload marker ever appears inside the SQL text itself,
  and ``rowcount == 1`` proves it touched exactly its own row. No
  corruption/deformation written here is ever repaired, deleted, or
  rewritten back afterwards.
- JSON validity/invalidity is proven ONLY with CPython ``json.loads`` on the
  exact durable text. This file asserts NOTHING about SQLite ``json_valid``
  / ``json_extract`` / ``json_each`` and never treats SQLite JSON behaviour
  as a semantic basis.
- This file patches NO module constants (unlike the sibling T35 capacity
  file). ``ops._MAX_LOG_EVENT_ROW_BYTES`` is READ, never patched, purely to
  ground that the payload and the corruption offset sit past the admitted
  event-row ceiling; that ceiling bounds event ROWS, not the stored result.
- tracemalloc measures the PYTHON allocator only. Nothing here measures or
  certifies SQLite C-level allocations, process RSS, or DB size, and no
  conclusion about any of those may be drawn from these peaks. "Post-setup"
  means the tracemalloc session starts only after the ledger is fully
  built, so setup allocations never count. The post-setup peak-delta bound
  in test 2 is a file-local PROPOSED candidate bound for a future bounded
  result projection; it is not enforced by the module today.
- Current implementation facts these tests pin (from the frozen/live source,
  not from a run): every read's ``store.job()`` -> ``_job`` ->
  ``get_operation()`` -> ``_op`` fully ``json.loads`` the stored result with
  no error-subtree bound and no try/except, so (a) any JSON-syntax damage
  ANYWHERE in the result — including deep inside a non-error subtree past
  the 4 MiB row ceiling — raises through ``store.job()`` and the reader
  converts it to an ``EXECUTION_STATE_UNKNOWN`` refusal (test 1 expected
  GREEN via the raise branch), and (b) Python-legal NaN/Infinity tokens
  decode and survive intact as float NaN/+inf/-inf (test 2 preservation
  expected GREEN), while that same full materialization is expected to
  violate test 2's proposed post-setup peak-delta bound (expected RED).
- Test-by-test expected status and first-failure point:
  * test 1 ``test_t35_xs_giant_result_nonerror_corruption_past_row_ceiling_fails_closed``:
    expected GREEN today through the raise branch (first assertion there is
    the ``EXECUTION_STATE_UNKNOWN`` code check). Synthesis tripwires fire
    first if the fixture itself drifts (valid-doc control parse, then the
    definite ``json.loads`` failure on the corrupted text). If a future
    projection returned a page instead of refusing, the else branch's first
    assertion ``chain["status"] != "CAPTURED"`` is the tripwire against an
    error-only scan pretending the result is fine; ``!= "NOT_CAPTURED"``
    guards the error silently vanishing.
  * test 2 ``test_t35_xs_giant_result_nan_infinity_preserved_post_setup_peak_bound``:
    the no-refusal guard (an ``ExecutionContractError`` on the large read
    fails the test via ``pytest.fail`` — refusing is NOT a pass), the
    CAPTURED chain with exact NaN/±Infinity values (None values fail with
    an explicit silently-null message — silent nulling is NOT a pass), the
    page completeness, and the durable token preservation are expected
    GREEN today. The final post-setup peak-delta candidate-bound assertion
    is expected RED today (``store.job()``/``_job``/``_op`` materialize the
    entire stored result); its message reports byte counts only and never
    echoes the payload.
- This file was authored and never executed; nothing below may be cited as
  a test run or a passed/failed result.
"""
from __future__ import annotations

import gc
import json
import math
import tracemalloc

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore

SESSION = "t35-xs-session"

EVENT_COUNT = 3

# 5 MiB single synthetic string field for the corruption test: the durable
# result (and the corruption offset inside it) must sit past the 4 MiB
# per-event-row admission ceiling that bounds event ROWS only.
NONERROR_PAD_CHARS = 5 * 1024 * 1024
NONERROR_LARGE_MARKER = "T35-XS-NONERR-LARGE-PAYLOAD-NO-ECHO-MARKER"

NONERROR_CHAIN_MARKER = "T35-XS-NONERR-CHAIN-MARKER"
NONERROR_ERROR_CODE = "T35_XS_NONERR_SYNTHETIC"

# 8 MiB pad for the NaN/Infinity test: clearly a "large" result while the
# non-finite markers live in short dedicated fields (markers deliberately
# avoid the substrings 'NaN', 'Infinity', and '"error"').
NANINF_PAD_CHARS = 8 * 1024 * 1024
NANINF_LARGE_MARKER = "T35-XS-NONFINITE-LARGE-PAYLOAD-NO-ECHO-MARKER"
NANINF_CHAIN_MARKER = "T35-XS-NONFINITE-CHAIN-MARKER"
NANINF_ERROR_CODE = "T35_XS_NONFINITE_SYNTHETIC"
NANINF_NAN_TOKENS = 2          # one outside the error subtree, one inside
NANINF_INFINITY_TOKENS = 4     # +/- Infinity outside, +/- Infinity inside

# Python-allocator regression allowance for the tiny control read.
PEAK_ALLOWANCE_BYTES = 4 * 1024 * 1024

# PROPOSED (file-local, not enforced by the module today) post-setup bound:
# comparing a large and a small result with identical tiny events and chain
# shape must not move the Python tracemalloc peak by more than this. The jobs,
# project identifiers and some non-error fields also differ. Expected RED
# today; a future bounded result projection
# is the intended target.
CANDIDATE_POST_SETUP_PEAK_DELTA_BYTES = 4 * 1024 * 1024

# A refusal/diagnostic surface must stay small enough to log safely.
ERROR_SURFACE_MAX_CHARS = 4096

# The small stored error this file pins (test 1): tiny, real, and textually
# intact even after the surrounding non-error subtree is damaged.
SMALL_ERROR_CHAIN = [
    {"type": "T35XsNonErrOuter", "message": NONERROR_CHAIN_MARKER + "-chain-1"},
]

# The original legal NaN/Infinity chain (test 2), inside the error subtree.
NANINF_EXPECTED_CHAIN = [{
    "type": "T35XsNonFiniteChain",
    "message": NANINF_CHAIN_MARKER + "-chain-1",
    "score": float("nan"),
    "ratio": float("inf"),
    "depth": float("-inf"),
}]


def _start_job(store: OperationStore, *, project: str, tag: str) -> dict:
    """Real store.begin with the complete project/execution binding that
    ``_g3_solver_ops._job_project_binding`` requires for both rows."""
    record, reused = store.begin(
        request_id=f"t35-xs-req-{tag}",
        idempotency_key=f"t35-xs-key-{tag}",
        request_hash=f"t35-xs-hash-{tag}",
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
        store.add_event(job_id, f"T35XsEvent{index}", {"index": index})


def _finish_failed(store: OperationStore, operation_id: str, result: dict) -> None:
    accepted, authoritative = store.finish(operation_id, status="FAILED", result=result)
    assert accepted is True and authoritative == "FAILED"


def _run_parameterized_update(store: OperationStore, sql: str, params: tuple,
                              *, payload_markers: tuple) -> None:
    """Write ONLY this file's own row through fully bound parameters, and
    verify the placeholder/parameter pairing BEFORE and AFTER execution."""
    assert sql.count("?") == len(params), (
        "raw SQL placeholder count must equal the bound-parameter count"
    )
    for marker in payload_markers:
        assert marker not in sql, (
            "payload text must travel only as a bound parameter, never interpolated into SQL"
        )
    cursor = store.db.execute(sql, params)
    assert cursor.rowcount == 1, "the write must touch exactly this test's own row"


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


def _expected_event_names() -> list[str]:
    return [f"T35XsEvent{index}" for index in range(EVENT_COUNT)]


def _expected_event_metadata() -> list[dict]:
    return [{"index": index} for index in range(EVENT_COUNT)]


def _assert_complete_small_page(page: dict, rows: list[dict], *, job_id: str,
                                project: str) -> None:
    """Output-completeness proof shared by the reads. Event payloads are
    tiny, so no message can echo a large payload."""
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


def _read_with_isolated_tracing(store: OperationStore, project: str,
                                arguments: dict) -> tuple[dict, int]:
    """One solver.log_read inside a tracemalloc session started AFTER the
    ledger was fully loaded (post-setup). Fails closed, without stopping
    foreign state, if tracing is already active; always stops its own
    session. Measures the PYTHON allocator only."""
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


def _chain_items_match(actual_items, expected_items) -> tuple[bool, str | None]:
    """Field-exact comparison that keeps NaN NaN-equal and signed
    infinities signed, and reports silently-nulled fields explicitly.
    Returns (ok, first_bad_description)."""
    if not isinstance(actual_items, list) or len(actual_items) != len(expected_items):
        return False, "exception_chain item count differs from the stored chain"
    for index, (actual, expected) in enumerate(zip(actual_items, expected_items)):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return False, f"item {index} fields differ from the stored chain"
        for key, expected_value in expected.items():
            actual_value = actual[key]
            if isinstance(expected_value, float) and not math.isfinite(expected_value):
                if actual_value is None:
                    return False, (
                        f"item {index}.{key} was silently converted to null; "
                        "Python-legal NaN/Infinity must be preserved"
                    )
                if isinstance(actual_value, bool) or not isinstance(actual_value, float):
                    return False, f"item {index}.{key} lost its non-finite float type"
                if math.isnan(expected_value):
                    if not math.isnan(actual_value):
                        return False, f"item {index}.{key} is no longer NaN"
                elif not (math.isinf(actual_value) and (actual_value > 0) == (expected_value > 0)):
                    return False, f"item {index}.{key} is no longer the same-signed infinity"
            elif type(actual_value) is not type(expected_value) or actual_value != expected_value:
                return False, f"item {index}.{key} differs from the original legal value"
    return True, None


def test_t35_xs_giant_result_nonerror_corruption_past_row_ceiling_fails_closed():
    """Expected GREEN today through the raise branch (``store.job()`` decode
    fails -> ``EXECUTION_STATE_UNKNOWN`` refusal); first assertion there is
    the code check. The else branch's first assertion
    ``chain["status"] != "CAPTURED"`` is the tripwire for a future
    projection that scans only the error subtree and certifies a result
    whose non-error half is syntactically broken.

    A brand-new synthetic FAILED job is finished normally, then its durable
    ``operations.result`` TEXT (this file's own row only, fully parameterized
    UPDATE) is replaced with one >4 MiB document laid out as
    ``{"large_data": <5 MiB pad>, "deep_normal": {"tail": [1, 2, 3]},
    "error": <small error>, "success": false}`` from which exactly the one
    closing brace of the NON-error ``deep_normal`` object has been deleted.
    Before writing, the synthesis is grounded three ways: the uncorrupted
    document parses cleanly with ``json.loads`` (validity control), the
    corruption offset is proven to lie past ``ops._MAX_LOG_EVENT_ROW_BYTES``
    (the per-event-row admission ceiling — an error-only or first-4MiB scan
    would never see the damage), and deleting that brace makes
    ``json.loads`` on the exact corrupted text raise ``JSONDecodeError``
    (definite syntax corruption, no SQLite JSON function involved), while
    the small error subtree itself still parses standalone — i.e. an
    error-only scanner COULD claim CAPTURED, which is exactly what the
    reader must not do.

    The read must fail closed: either an ``ExecutionContractError`` with
    code ``EXECUTION_STATE_UNKNOWN`` and a small marker-free surface, or
    (future projection) a page whose ``exception_chain.status`` is exactly
    ``UNKNOWN`` with no items and no event/worker source substitution.
    ``CAPTURED`` (pretending from an error-only scan) and ``NOT_CAPTURED``
    (the small stored error silently vanishing) are both forbidden.
    Afterwards the corrupted text, terminal states, and stored events are
    byte-for-byte what this test left them as — nothing repaired,
    truncated further, or deleted."""
    project = "t35-xs-nonerr-corrupt-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="nonerr-corrupt")
        job_id, operation_id = record["job_id"], record["operation_id"]
        _add_small_events(store, job_id)
        _finish_failed(store, operation_id,
                       {"success": False, "note": "t35-xs-nonerr-placeholder"})

        pad = NONERROR_LARGE_MARKER + "L" * (NONERROR_PAD_CHARS - len(NONERROR_LARGE_MARKER))
        error_json = ('{"code": "' + NONERROR_ERROR_CODE + '", "message": "'
                      + NONERROR_CHAIN_MARKER + ' intact-chain", "cause_chain": '
                      + json.dumps(SMALL_ERROR_CHAIN) + '}')
        doc = ('{"large_data": "' + pad + '", '
               + '"deep_normal": {"tail": [1, 2, 3]}, '
               + '"error": ' + error_json + ', "success": false}')
        del pad

        # Validity control for the UNCORRUPTED synthesis.
        parsed = json.loads(doc)
        assert parsed["error"]["code"] == NONERROR_ERROR_CODE
        assert parsed["error"]["cause_chain"] == SMALL_ERROR_CHAIN
        del parsed

        # The corruption site is inside the NON-error subtree AND past the
        # per-event-row admission ceiling.
        deep_start = doc.index('"deep_normal"')
        assert deep_start > ops._MAX_LOG_EVENT_ROW_BYTES, (
            "the non-error subtree must begin past the 4 MiB admitted-row ceiling"
        )
        cut = doc.index('}', deep_start)
        assert doc[cut] == '}' and doc[cut - 1] == ']', (
            "the deleted brace must be the closing brace of the deep_normal object"
        )
        corrupted = doc[:cut] + doc[cut + 1:]
        assert len(corrupted) == len(doc) - 1
        assert cut > ops._MAX_LOG_EVENT_ROW_BYTES

        # Definite JSON syntax corruption on the exact text, proven with
        # pure CPython json.loads (no SQLite JSON function is relied on).
        with pytest.raises(json.JSONDecodeError):
            json.loads(corrupted)
        # The small error subtree itself is textually intact and parses
        # standalone: an error-only scanner would "see" a clean error.
        assert ('"error": ' + error_json) in corrupted
        assert json.loads(error_json)["cause_chain"] == SMALL_ERROR_CHAIN
        assert '"tail": [1, 2, 3]}' not in corrupted
        del doc

        _run_parameterized_update(
            store,
            "UPDATE operations SET result=? WHERE operation_id=?",
            (corrupted, operation_id),
            payload_markers=(NONERROR_LARGE_MARKER, NONERROR_CHAIN_MARKER),
        )

        durable_len = _db_result_length(store, operation_id)
        assert durable_len > ops._MAX_LOG_EVENT_ROW_BYTES, (
            "the stored result must be larger than the 4 MiB admitted-row ceiling"
        )
        assert _db_job_status(store, job_id) == "FAILED"
        assert _db_operation_status(store, operation_id) == "FAILED"
        rows = _stored_rows(store, job_id)
        assert len(rows) == EVENT_COUNT

        try:
            page = ops.solver_log_read(store, project, {"job_id": job_id,
                                                        "limit": EVENT_COUNT})
        except ExecutionContractError as exc:
            assert exc.code == "EXECUTION_STATE_UNKNOWN", (
                "a giant result with JSON syntax damage in a NON-error subtree must "
                "fail closed as controlled EXECUTION_STATE_UNKNOWN, never pass by "
                "scanning only the small error subtree"
            )
            text = str(exc)
            assert NONERROR_CHAIN_MARKER not in text and NONERROR_LARGE_MARKER not in text, (
                "the refusal must not echo the corrupted payload"
            )
            assert len(text) <= ERROR_SURFACE_MAX_CHARS
        else:
            chain = page["exception_chain"]
            assert chain["status"] != "CAPTURED", (
                "scanning only the intact error subtree while the surrounding result "
                "is syntactically corrupt must never be certified as CAPTURED"
            )
            assert chain["status"] != "NOT_CAPTURED", (
                "a genuinely stored small error must never degrade to NOT_CAPTURED"
            )
            assert chain["status"] == "UNKNOWN"
            assert chain.get("items") in ([], None)
            source = chain.get("source")
            assert not (isinstance(source, str) and (source.startswith("event ")
                                                     or source.startswith("worker request"))), (
                "substituting an event/worker candidate is not a fail-closed answer "
                "to whole-result corruption"
            )
            surface = json.dumps(chain, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), default=str)
            assert NONERROR_CHAIN_MARKER not in surface and NONERROR_LARGE_MARKER not in surface
            assert len(surface) <= ERROR_SURFACE_MAX_CHARS
            assert [event["id"] for event in page["events"]] == [row["id"] for row in rows]
            assert page["job_status"] == "FAILED"

        # Durable corruption stays exactly as synthesized; nothing repaired,
        # truncated further, or deleted.
        assert _db_job_status(store, job_id) == "FAILED"
        assert _db_operation_status(store, operation_id) == "FAILED"
        assert _db_result_length(store, operation_id) == durable_len
        post_raw = _db_result_raw(store, operation_id)
        assert post_raw == corrupted, "the stored synthetic result changed"
        assert NONERROR_CHAIN_MARKER in post_raw and ('"error": ' + error_json) in post_raw, (
            "the stored error is audit data; the reader must not silently rewrite it"
        )
        del post_raw
        assert _rows_signature(_stored_rows(store, job_id)) == _rows_signature(rows)
    finally:
        store.close()


def test_t35_xs_giant_result_nan_infinity_preserved_post_setup_peak_bound():
    """Large-result compatibility edge: a >4 MiB stored result containing
    Python-legal NaN/Infinity (as emitted by ``json.dumps`` with the default
    ``allow_nan=True``) BOTH inside the error subtree and outside it must
    keep the reader's original legal output and values. A refusal
    (``ExecutionContractError``) and silent ``null`` substitution are
    explicitly NOT passes.

    A fresh in-memory store holds two brand-new synthetic FAILED jobs with
    byte-identical tiny events: a tiny control whose small chain carries
    NaN/±Infinity, and a large one whose durable result (written through a
    verified parameterized UPDATE on this file's own row) is an ~8 MiB
    document whose non-error ``metrics`` field carries NaN, +Infinity and
    -Infinity outside the error subtree, while ``error.cause_chain`` carries
    the same three non-finite values inside it. Positive controls before any
    read: pure ``json.loads`` on the exact text proves the document is
    Python-legal (it parses, and the non-finite values decode to
    ``float('nan')``/``inf``/``-inf``), the durable token counts match the
    synthesis exactly, and at least one NaN and one Infinity token sit on
    each side of the ``"error"`` key — covering outside and inside.

    For the large read: any refusal fails the test; the chain must come
    back as the stored ``result.error.cause_chain`` with field-exact
    NaN/±Infinity values (the matcher names silent nulls explicitly), the
    page must stay complete with frozen whole-list digests and an honest
    NOT_CAPTURED ``solver_output_log`` (no UNKNOWN degradation), and the
    durable NaN/Infinity tokens must survive the read untouched.

    Then the post-setup tracemalloc pair: the tiny control read must stay
    under ``PEAK_ALLOWANCE_BYTES`` (baseline pin), and the PROPOSED
    candidate bound compares the large and small result Python peaks within
    ``CANDIDATE_POST_SETUP_PEAK_DELTA_BYTES``. The jobs, project IDs and some
    non-error fields also differ, so this is not a one-variable comparison.
    That last assertion is expected RED today against the current
    implementation (every read's ``store.job()`` -> ``_job`` ->
    ``get_operation()`` -> ``_op`` fully ``json.loads`` the stored result);
    its message reports byte counts only, never the payload. Python
    allocator only — no RSS/SQLite-C/DB-size claim is made or implied."""
    project = "t35-xs-naninf-project"
    control_project = "t35-xs-naninf-ctl-project"
    store = OperationStore(":memory:")
    try:
        record = _start_job(store, project=project, tag="naninf")
        control = _start_job(store, project=control_project, tag="naninf-ctl")
        job_id, operation_id = record["job_id"], record["operation_id"]
        assert job_id != control["job_id"] and operation_id != control["operation_id"]
        _add_small_events(store, job_id)
        _add_small_events(store, control["job_id"])
        _finish_failed(store, operation_id,
                       {"success": False, "note": "t35-xs-naninf-placeholder"})
        _finish_failed(store, control["operation_id"],
                       {"success": False, "note": "t35-xs-naninf-ctl-placeholder"})

        error_json = json.dumps({
            "code": NANINF_ERROR_CODE,
            "message": NANINF_CHAIN_MARKER + " non-finite synthetic failure",
            "cause_chain": NANINF_EXPECTED_CHAIN,
        }, sort_keys=True)
        assert "NaN" in error_json and "Infinity" in error_json and "-Infinity" in error_json

        pad = NANINF_LARGE_MARKER + "L" * (NANINF_PAD_CHARS - len(NANINF_LARGE_MARKER))
        doc = ('{"large_synthetic_field": "' + pad + '", '
               + '"metrics": {"nan_outside": NaN, "pos_inf_outside": Infinity, '
               + '"neg_inf_outside": -Infinity}, '
               + '"note": "t35-xs-naninf", '
               + '"error": ' + error_json + '}')
        del pad

        # Python-legality positive control: CPython json.loads accepts
        # NaN/Infinity and decodes them to the exact float values.
        parsed = json.loads(doc)
        assert math.isnan(parsed["metrics"]["nan_outside"])
        assert parsed["metrics"]["pos_inf_outside"] == float("inf")
        assert parsed["metrics"]["neg_inf_outside"] == float("-inf")
        ok_ctl, why_ctl = _chain_items_match(
            parsed["error"]["cause_chain"], NANINF_EXPECTED_CHAIN)
        assert ok_ctl, f"synthesis control failed: {why_ctl}"
        del parsed

        # Outside/inside coverage in the exact durable text.
        idx_error = doc.index('"error"')
        assert doc.count("NaN") == NANINF_NAN_TOKENS
        assert doc.count("Infinity") == NANINF_INFINITY_TOKENS
        assert doc.find("NaN") < idx_error and doc.rfind("NaN") > idx_error
        assert doc.find("Infinity") < idx_error and doc.rfind("Infinity") > idx_error

        _run_parameterized_update(
            store,
            "UPDATE operations SET result=? WHERE operation_id=?",
            (doc, operation_id),
            payload_markers=(NANINF_LARGE_MARKER, NANINF_CHAIN_MARKER),
        )
        del doc

        # Tiny control chain text (same non-finite values, tiny size).
        ctl_text = ('{"success": false, "note": "t35-xs-naninf-ctl", "error": '
                    + error_json + '}')
        _run_parameterized_update(
            store,
            "UPDATE operations SET result=? WHERE operation_id=?",
            (ctl_text, control["operation_id"]),
            payload_markers=(NANINF_CHAIN_MARKER,),
        )
        del ctl_text

        large_len = _db_result_length(store, operation_id)
        assert large_len > ops._MAX_LOG_EVENT_ROW_BYTES, (
            "the NaN/Infinity result must be a giant (>4 MiB) stored result"
        )
        pre_raw = _db_result_raw(store, operation_id)
        assert pre_raw.count("NaN") == NANINF_NAN_TOKENS
        assert pre_raw.count("Infinity") == NANINF_INFINITY_TOKENS
        assert NANINF_LARGE_MARKER in pre_raw
        del pre_raw
        assert _db_job_status(store, job_id) == "FAILED"
        assert _db_operation_status(store, operation_id) == "FAILED"
        rows = _stored_rows(store, job_id)
        assert len(rows) == EVENT_COUNT
        control_rows = _stored_rows(store, control["job_id"])
        assert [row["event"] for row in control_rows] == [row["event"] for row in rows]
        assert [row["metadata"] for row in control_rows] == [row["metadata"] for row in rows]
        gc.collect()

        # Tiny control read: same non-finite chain on the cheap path, with a
        # bounded post-setup Python peak pinning the baseline.
        small_page, small_peak = _read_with_isolated_tracing(
            store, control_project, {"job_id": control["job_id"], "limit": EVENT_COUNT})
        _assert_complete_small_page(small_page, control_rows,
                                    job_id=control["job_id"], project=control_project)
        small_chain = small_page["exception_chain"]
        assert small_chain["status"] == "CAPTURED"
        assert small_chain["source"] == "stored result.error.cause_chain"
        ok_small, why_small = _chain_items_match(small_chain["items"], NANINF_EXPECTED_CHAIN)
        assert ok_small, f"tiny-path chain lost or deformed non-finite values: {why_small}"
        assert small_peak <= PEAK_ALLOWANCE_BYTES, (
            f"tiny non-finite control read already exceeded the Python baseline bound "
            f"({small_peak} bytes)"
        )
        del small_page
        gc.collect()

        # THE large read: refusing is explicitly not a pass. pytest.fail
        # always raises, so the except branch never falls through; this
        # checker cannot resolve pytest's NoReturn stub here, so the
        # binding guard below pins that invariant statically. It asserts
        # only what every path already guarantees and weakens nothing.
        large_page = large_peak = None
        try:
            large_page, large_peak = _read_with_isolated_tracing(
                store, project, {"job_id": job_id, "limit": EVENT_COUNT})
        except ExecutionContractError as exc:
            pytest.fail(
                "a giant stored result whose JSON is Python-legal (NaN/Infinity) must be "
                "served, not refused; refusing is not a pass "
                f"(code={getattr(exc, 'code', type(exc).__name__)})"
            )
        assert large_page is not None and type(large_peak) is int, (
            "the successful read must have bound page and peak; a refusal exits "
            "above via pytest.fail and is not a pass"
        )
        _assert_complete_small_page(large_page, rows, job_id=job_id, project=project)
        assert large_page["job_status"] == "FAILED"
        chain = large_page["exception_chain"]
        assert chain["status"] == "CAPTURED", (
            "a Python-legal NaN/Infinity chain must stay CAPTURED; degrading it is a "
            "silent deformation of an intact stored result"
        )
        assert chain["source"] == "stored result.error.cause_chain"
        ok_items, why_items = _chain_items_match(chain["items"], NANINF_EXPECTED_CHAIN)
        assert ok_items, f"reader deformed the original legal values: {why_items}"
        surface = json.dumps(chain, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=True)
        assert NANINF_LARGE_MARKER not in surface, (
            "the large non-error payload must never be echoed through exception_chain"
        )
        assert len(surface) <= ERROR_SURFACE_MAX_CHARS
        del large_page, chain

        # Durable non-finite values survive the read untouched.
        assert _db_job_status(store, job_id) == "FAILED"
        assert _db_operation_status(store, operation_id) == "FAILED"
        assert _db_result_length(store, operation_id) == large_len
        post_raw = _db_result_raw(store, operation_id)
        assert post_raw.count("NaN") == NANINF_NAN_TOKENS
        assert post_raw.count("Infinity") == NANINF_INFINITY_TOKENS
        assert NANINF_LARGE_MARKER in post_raw
        del post_raw
        assert _rows_signature(_stored_rows(store, job_id)) == _rows_signature(rows)

        # THE proposed post-setup bound (expected RED today). Byte counts
        # only; the payload itself is never printed or reconstructed here.
        delta = large_peak - small_peak
        assert delta <= CANDIDATE_POST_SETUP_PEAK_DELTA_BYTES, (
            f"the large/small solver.log_read post-setup Python tracemalloc peak "
            f"difference was {delta} bytes (small_peak={small_peak}, "
            f"large_peak={large_peak}; candidate bound "
            f"{CANDIDATE_POST_SETUP_PEAK_DELTA_BYTES}; payload not printed)"
        )
    finally:
        store.close()
