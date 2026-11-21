"""T35 differential contract tests for the PROPOSED incremental error-field
projection ``comsol_mcp._json_error_stream.project_error_chunks`` (authored, NOT RUN).

Contract under test (a PROPOSAL for a future stdlib-only incremental
primitive, NOT an existing API):

    project_error_chunks(chunks, *, max_error_bytes) -> dict

  * ``chunks`` is an iterable of ``bytes`` pieces of one JSON document.
  * The result is a dict containing only the LAST top-level ``"error"``
    key (CPython ``json.loads`` duplicate-key semantics), or ``{}`` when
    the key is absent or the decoded root is not a dict.
  * If the SELECTED error subtree exceeds ``max_error_bytes``, or the
    whole document is invalid (JSON syntax anywhere, bad UTF-8, truncation,
    trailing garbage), the call must RAISE — its own exception class or a
    standard one. Returning ``{}`` for an invalid document is forbidden:
    it would disguise "unreadable result" as "no error".

Scope and honesty boundaries (binding for every test in this file):

- ``comsol_mcp._json_error_stream`` does NOT exist as of authoring. Every
  test first passes an explicit module/attribute gate (``importlib`` lookup
  at CALL time inside the test, never a module-level import): a missing
  module or attribute is reported as an explicit ``INTERFACE RED`` failure,
  never as a bare ``ImportError`` misread as product behaviour, never as a
  skip. Expected status today: every test here fails as INTERFACE RED.
  Because the gate runs BEFORE any ``pytest.raises`` block, an expected
  raise can never mask the module's absence.
- Semantics reference / independent oracle: CPython ``json.loads`` on the
  same bytes. Every positive case is differentially compared against it
  (duplicate keys resolve to the LAST occurrence; non-dict roots and
  missing error keys project to ``{}``; Python-legal ``NaN``/``Infinity``
  survive with their values, compared NaN-aware).
- This file implements NO production code and touches NOTHING real: no
  ``OperationStore``, no SQLite (not even ``:memory:``), no daemon,
  ControlDaemon, Worker, COMSOL, SSH, model, endpoint, credential,
  historical/prior DB, real job, network, thread, subprocess, or disk file.
  It imports only stdlib and pytest at module level, so importing the file
  cannot trigger package side effects.
- Sizes are intentionally TINY (all fixtures < 4 KiB, chunk sizes 1..64).
  That is a deliberate test-design choice to avoid pressuring the
  environment; it is NOT a file-size ceiling for the product. The 4 MiB
  stored-result overflow that motivated this primitive stays a motivation,
  not an assertion here.
- ``max_error_bytes`` accounting (exact serialized form of the selected
  subtree) is left open by the proposal, so budget tests use large margins
  (selected subtree > 4x over budget, or < 1/7 of budget) and never sit
  near a boundary.
- This file was authored and never executed; nothing below may be cited
  as a test run or a passed/failed result. The parent agent reviews the
  contract and experiment safety before deciding to run it.

Production grounding (read from source, not from a run): today
``comsol_mcp._g3_solver_ops._stored_result_exception_chain`` /
``_stored_result_error_summary`` read ``job["result"]["error"]`` from a
fully ``json.loads``-decoded result, so the decoded dict's LAST duplicate
top-level ``"error"`` key is the accurate source; this projection must
agree with that decode, and the sibling T35 files already pin those
last-wins and NaN/Infinity semantics for the real reader path.
"""
from __future__ import annotations

import importlib
import inspect
import json
import math

import pytest

MODULE_NAME = "comsol_mcp._json_error_stream"
FUNC_NAME = "project_error_chunks"

# Generous budget: every legal payload below is far under it, so positive
# cases exercise completion paths, not size refusal.
GENEROUS_BUDGET_BYTES = 4096
# Tight budgets for the budget differential, applied with >= 4x margins.
TIGHT_BUDGET_BYTES = 64
FIT_BUDGET_BYTES = 128

HUGE_ERROR_CHARS = 512
SELECTED_OVER_ERROR_CHARS = 320

CHUNK_SIZES = (1, 3, 7, 64)

ERROR_CHAIN_JSON = (
    '{"code": "T35_CONTRACT_ERR", "cause_chain": '
    '[{"type": "T35ContractOuter", "message": "boom-chain-1"}]}'
)

# (case_id, text) pairs; the oracle below derives the expected projection.
PAYLOADS = [
    ("error-string", '{"error": "solver failure"}'),
    ("error-chain-object",
     '{"success": false, "note": "ctx", "error": ' + ERROR_CHAIN_JSON + '}'),
    ("error-null", '{"error": null}'),
    ("error-bool", '{"error": true}'),
    ("error-int", '{"error": -7}'),
    ("error-float", '{"error": 2.5}'),
    ("error-list", '{"error": [1, "two", null]}'),
    ("error-nested-dict", '{"error": {"a": {"b": [1, 2]}}}'),
    ("no-error-key", '{"ok": true, "note": "none"}'),
    ("empty-object", "{}"),
    ("root-list", '[{"error": "inside list, not top level"}]'),
    ("root-null", "null"),
    ("root-int", "42"),
    ("root-string", '"just a string"'),
    ("dup-error-small-then-big",
     '{"error": "first", "error": {"code": "LAST", "cause_chain": []}}'),
    ("dup-error-escaped-then-plain",
     '{"erro\\u0072": "first-escaped-key", "error": "second-plain-key"}'),
    ("dup-error-plain-then-escaped",
     '{"error": "first-plain-key", "err\\u006Fr": "second-escaped-key"}'),
    ("unicode-message",
     '{"error": {"msg": "\u6c42\u89e3\u5668\u5931\u8d25 \U0001f600", '
     '"esc": "caf\\u00e9"}}'),
    ("nan-error-value", '{"error": NaN}'),
    ("nan-infinity-in-error", '{"error": {"score": NaN, "inf": Infinity, "neg": -Infinity}}'),
    ("nan-outside-error-dropped", '{"metrics": {"nan_outside": NaN}, "error": "still fine"}'),
    ("surrounding-whitespace", '  \n\t{"error": "ws ok"}  \n'),
]

# Invalid-document negatives: (case_id, doc_bytes, completed_error_json_or_None,
# damage_marker_or_None). Each is grounded invalid with CPython json.loads
# BEFORE the proposed callable is invoked, so a raise cannot be an artefact
# of a broken fixture, and a fixture that secretly became valid fails first.
CORRUPT_AFTER_CONTROL = (
    '{"error": ' + ERROR_CHAIN_JSON + ', "pad": "' + "P" * 800 +
    '", "tail": {"items": [1, 2, 3]}}'
)
CORRUPT_AFTER_DOC = CORRUPT_AFTER_CONTROL.replace("[1, 2, 3]", "[1,, 2, 3]")

# Fixture drift guard at collection time: the control is valid, the damage
# is exactly the doubled comma after a completed error, nothing else.
assert json.loads(CORRUPT_AFTER_CONTROL)["error"]["code"] == "T35_CONTRACT_ERR"
assert len(CORRUPT_AFTER_DOC) == len(CORRUPT_AFTER_CONTROL) + 1
try:
    json.loads(CORRUPT_AFTER_DOC)
except json.JSONDecodeError:
    pass
else:
    raise AssertionError("the corrupted sibling fixture must be JSON-invalid")

NEGATIVE_CASES = [
    # Completed top-level error first, then a large sibling with the syntax
    # damage: an error-only scan would return the intact chain, which the
    # contract forbids — the WHOLE suffix must be validated.
    ("large-broken-sibling-after-completed-error",
     CORRUPT_AFTER_DOC.encode("utf-8"), ERROR_CHAIN_JSON, b"[1,, 2, 3]"),
    # Damage strictly before the error; a prefix-stopping parser would
    # claim the document has no error.
    ("corruption-before-error",
     b'{"ok": tru, "error": "boom-before"}', None, b"tru,"),
    # Complete, parseable error, then trailing garbage after the document.
    ("trailing-garbage-after-complete-error",
     b'{"error": "boom-tail"} oops{', '"boom-tail"', b"oops{"),
    # Document ends mid error value: truncation must not look like success.
    ("truncated-mid-error-value",
     b'{"ok": 1, "error": {"code": "T35_CONTRACT_TRUNC", "message": "uns',
     None, b'"message": "uns'),
    # Invalid UTF-8 bytes (the bytes alone are already invalid UTF-8).
    ("invalid-utf8-bytes",
     b'{"error": "dam\xff\xfeage"}', None, b"\xff"),
    # No bytes at all: an empty document is invalid, not "no error".
    ("empty-chunk-list", b"", None, None),
]


def _projection():
    """Prerequisite gate: look the PROPOSED callable up with importlib at
    call time. Missing module/attribute is an explicit INTERFACE RED; a
    bare ImportError is never treated as product behaviour."""
    try:
        module = importlib.import_module(MODULE_NAME)
    except ImportError as exc:
        pytest.fail(
            f"INTERFACE RED: {MODULE_NAME} is not implemented yet "
            f"(ImportError: {exc}); this is an interface gap, not product behaviour"
        )
    try:
        func = getattr(module, FUNC_NAME)
    except AttributeError:
        pytest.fail(f"INTERFACE RED: {MODULE_NAME} lacks {FUNC_NAME}")
    if not callable(func):
        pytest.fail(f"INTERFACE RED: {MODULE_NAME}.{FUNC_NAME} is not callable")
    return func


def _oracle_projection(text: str) -> dict:
    """Independent semantics reference: CPython ``json.loads`` on the same
    document. Duplicate top-level keys resolve to the LAST occurrence;
    non-dict roots and missing error keys project to ``{}``."""
    value = json.loads(text)
    if isinstance(value, dict) and "error" in value:
        return {"error": value["error"]}
    return {}


def _json_equal(actual, expected) -> bool:
    """Deep JSON equality that keeps NaN NaN-equal and signed infinities
    signed, and never lets ``True``/``1`` cross types."""
    if isinstance(expected, float) and not math.isfinite(expected):
        if isinstance(actual, bool) or not isinstance(actual, float):
            return False
        if math.isnan(expected):
            return math.isnan(actual)
        return math.isinf(actual) and (actual > 0) == (expected > 0)
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and set(actual) == set(expected)
                and all(_json_equal(actual[k], expected[k]) for k in expected))
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_json_equal(a, e) for a, e in zip(actual, expected)))
    return type(actual) is type(expected) and actual == expected


def _byte_chunks(data: bytes, size: int) -> list:
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_t35_jes_interface_shape():
    """Gate first, then pin the proposed signature shape: one positional
    ``chunks`` parameter and a KEYWORD-ONLY ``max_error_bytes``."""
    project = _projection()
    params = inspect.signature(project).parameters
    assert "chunks" in params, (
        f"{FUNC_NAME} must accept the positional chunk stream parameter 'chunks' "
        f"(got {sorted(params)})"
    )
    assert "max_error_bytes" in params, (
        f"{FUNC_NAME} must accept 'max_error_bytes' (got {sorted(params)})"
    )
    assert params["max_error_bytes"].kind is inspect.Parameter.KEYWORD_ONLY, (
        "max_error_bytes is proposed as keyword-only"
    )


@pytest.mark.parametrize("case_id,text", PAYLOADS, ids=[p[0] for p in PAYLOADS])
def test_t35_jes_projection_matches_json_loads_oracle(case_id, text):
    """Positive differential control: for every legal document, feeding the
    bytes at several chunk sizes (including size 1, which splits multibyte
    UTF-8 sequences and every token) must produce EXACTLY the projection
    ``json.loads`` implies on the completion path — same key set, same
    error value (NaN/±Infinity preserved via the NaN-aware matcher)."""
    project = _projection()
    expected = _oracle_projection(text)
    if case_id == "dup-error-escaped-then-plain":
        assert expected == {"error": "second-plain-key"}
    elif case_id == "dup-error-plain-then-escaped":
        assert expected == {"error": "second-escaped-key"}
    data = text.encode("utf-8")
    assert len(data) < 4096, "fixture discipline: keep these contract probes tiny"
    for size in CHUNK_SIZES + (len(data) + 1,):
        actual = project(_byte_chunks(data, size), max_error_bytes=GENEROUS_BUDGET_BYTES)
        assert isinstance(actual, dict), f"{case_id}: projection must return a dict (chunk size {size})"
        assert set(actual) == set(expected), (
            f"{case_id}: projection keys {sorted(actual)} must equal the oracle's "
            f"{sorted(expected)} exactly (chunk size {size}); the dict must contain "
            f"only the last top-level error"
        )
        for key in expected:
            assert _json_equal(actual[key], expected[key]), (
                f"{case_id}: projected {key!r} subtree differs from the CPython "
                f"json.loads oracle (chunk size {size})"
            )


def test_t35_jes_accepts_single_pass_iterator_of_byte_chunks():
    """Streaming intent: the chunk stream may be a single-pass iterator,
    not an indexable sequence."""
    project = _projection()
    text = PAYLOADS[1][1]
    expected = _oracle_projection(text)
    data = text.encode("utf-8")
    chunks = iter(_byte_chunks(data, 5))
    actual = project(chunks, max_error_bytes=GENEROUS_BUDGET_BYTES)
    assert set(actual) == set(expected) and _json_equal(actual.get("error"), expected.get("error"))


def test_t35_jes_empty_chunks_are_transparent():
    """Zero-length byte chunks between real chunks must not disturb the
    incremental decode."""
    project = _projection()
    text = PAYLOADS[1][1]
    expected = _oracle_projection(text)
    data = text.encode("utf-8")
    chunks = []
    for byte in data:
        chunks.append(bytes([byte]))
        chunks.append(b"")
    chunks.append(b"")
    actual = project(chunks, max_error_bytes=GENEROUS_BUDGET_BYTES)
    assert set(actual) == set(expected) and _json_equal(actual.get("error"), expected.get("error"))


def test_t35_jes_no_error_key_under_tight_budget_returns_empty_dict():
    """The budget bounds the SELECTED error subtree, not the whole document:
    a valid 1.5 KB document with no error key must still project to ``{}``
    under a budget far smaller than the document."""
    project = _projection()
    text = '{"data": "' + "D" * 1500 + '"}'
    assert _oracle_projection(text) == {}
    data = text.encode("utf-8")
    for size in (7, 64):
        actual = project(_byte_chunks(data, size), max_error_bytes=TIGHT_BUDGET_BYTES)
        assert actual == {}, (
            f"a valid no-error document must project to exactly {{}} under a tight "
            f"budget (chunk size {size}, {len(data)} document bytes)"
        )


def test_t35_jes_huge_duplicate_error_then_small_selected_error_fits_budget():
    """Duplicate-key last-wins interacts with the budget: an EARLIER huge
    error value (>> budget) followed by a LATER small one (well inside the
    budget) must project the small last-wins error. An implementation that
    refuses as soon as ANY seen error value breaks the budget gets this
    wrong — the selected subtree is what the budget bounds."""
    project = _projection()
    text = ('{"error": "' + "H" * HUGE_ERROR_CHARS + '", "error": "small-selected"}')
    expected = _oracle_projection(text)
    assert expected == {"error": "small-selected"}, "fixture drift: json oracle must pick the small error"
    assert len(json.dumps(expected["error"])) * 7 < FIT_BUDGET_BYTES
    assert (HUGE_ERROR_CHARS + 2) > 4 * FIT_BUDGET_BYTES, (
        "fixture drift: the earlier error subtree must sit clearly over the budget"
    )
    data = text.encode("utf-8")
    for size in (7, 17):
        actual = project(_byte_chunks(data, size), max_error_bytes=FIT_BUDGET_BYTES)
        assert set(actual) == {"error"} and _json_equal(actual.get("error"), expected["error"]), (
            f"last-wins selected error (16 bytes) is well inside the {FIT_BUDGET_BYTES}-byte "
            f"budget; the earlier huge error must not force a refusal (chunk size {size})"
        )


def test_t35_jes_selected_error_over_budget_raises_not_empty_dict():
    """The SELECTED (last-wins) error subtree is >4x over the budget: the
    call must raise — its own class or a standard one, deliberately not
    named here since neither exists yet — and must NOT return ``{}`` or a
    truncated subtree. ``DID NOT RAISE`` is itself the contract violation."""
    project = _projection()
    error_value = "E" * SELECTED_OVER_ERROR_CHARS
    text = '{"error": "' + error_value + '"}'
    expected = _oracle_projection(text)
    assert expected["error"] == error_value, "fixture drift: oracle must see the huge error"
    assert len(error_value.encode("utf-8")) > 4 * TIGHT_BUDGET_BYTES
    data = text.encode("utf-8")
    with pytest.raises(Exception):
        project(_byte_chunks(data, 7), max_error_bytes=TIGHT_BUDGET_BYTES)


@pytest.mark.parametrize(
    "case_id,doc_bytes,error_json,damage_marker",
    NEGATIVE_CASES,
    ids=[c[0] for c in NEGATIVE_CASES],
)
def test_t35_jes_invalid_document_raises_never_disguises_as_no_error(
    case_id, doc_bytes, error_json, damage_marker
):
    """Invalid whole documents must raise, never project to ``{}`` as "no
    error". Grounding comes FIRST, on the fixture itself: CPython
    ``json.loads`` on the exact concatenated bytes already fails, and where
    a completed error sits before the damage, the error text is proven
    intact and parseable on its own — i.e. an error-only scan COULD claim
    success, which is exactly what this contract forbids. Then every chunk
    size (including byte-by-byte) must raise."""
    project = _projection()
    with pytest.raises(ValueError):
        json.loads(doc_bytes)
    if error_json is not None:
        encoded = error_json.encode("utf-8")
        assert doc_bytes.count(encoded) == 1
        json.loads(error_json)
        assert doc_bytes.index(encoded) < len(doc_bytes) - 1, (
            "fixture drift: the completed error must sit before the damage"
        )
        if damage_marker is not None:
            assert doc_bytes.index(damage_marker) > doc_bytes.index(encoded)
    if damage_marker is not None:
        assert damage_marker in doc_bytes
    for size in (37, 1):
        chunks = _byte_chunks(doc_bytes, size)
        with pytest.raises(Exception):
            project(chunks, max_error_bytes=GENEROUS_BUDGET_BYTES)
