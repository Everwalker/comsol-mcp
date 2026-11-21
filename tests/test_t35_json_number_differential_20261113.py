"""T35 differential probes: integer digit-limit parity and exact
selected-source byte-budget boundaries for the incremental projector
``comsol_mcp._json_error_stream.project_error_chunks`` (authored, NOT RUN
by this author; the parent owns execution, wiring and review).

Independent oracle: CPython ``json.loads`` on the same text, run INSIDE
the same in-process digit-limit context as the candidate, so every claim
below is a differential against what the oracle actually does, never an
assumption about it.

What is pinned (all expectations read off the frozen candidate snapshot
``_state/t35-log-overflow-parent-20261112/json_error_stream_candidate_495baac5.py.txt``;
the live helper module was never opened by this author):

1. Digit limit 640 (the smallest value ``sys.set_int_max_str_digits``
   accepts): positive AND negative 640-digit integers at IGNORED
   positions (a non-error sibling, or an early duplicate error that a
   later small error supersedes) are legal for oracle and candidate
   alike; 641-digit integers are rejected by both — and a later small
   error never rescues an over-limit integer, in the early-duplicate
   case or the independent-sibling case (the whole document is validated).
2. Under the same limit, syntactically valid floats whose integer part
   has 641 digits (followed by ``.5`` / an exponent) or whose exponent
   is longer than the limit are accepted per the oracle (``float()``
   always succeeds on a valid JSON float; overflow -> ``inf``,
   underflow -> ``0.0``); the selected projection equals the oracle
   value via the NaN/inf-aware matcher.
3. With the interpreter limit disabled (0), a 5000-digit integer at
   ignored positions is accepted by the oracle FIRST and then by the
   candidate (5000 > CPython's 4300 default, so the differential is
   only meaningful with the limit switched off).
4. Exact source-byte budget of the selected subtree: subtree bytes ==
   budget accepted, budget - 1 raises; whitespace INSIDE the subtree
   counts, whitespace between colon and value or after the value does
   not; multibyte characters and ``\\uXXXX`` escapes consume their RAW
   source UTF-8 bytes, not their decoded length (same decoded value,
   different budget decision).
5. Duplicate ordering control kept distinct from the boundary probes: an
   earlier over-budget error superseded by a small later error succeeds
   (positive control); reversed, the last-wins selection is over budget
   and must raise the dedicated budget class.

Honesty boundaries (binding):

- The digit-limit context manager mutates ONLY this process's
  interpreter setting, captures the original via
  ``sys.get_int_max_str_digits`` and restores it in ``finally`` (the
  tests additionally assert the restore afterwards). It skips ONLY when
  the interpreter has no ``sys.set/get_int_max_str_digits`` API at all.
  No env, config, subprocess or cross-process change is attempted.
- The exact-boundary decisions (``== budget`` accept, ``budget - 1``
  raise; colon/post-value whitespace excluded) were read off ``_record``'s
  strict ``>`` comparison and the colon/post-value ``_skip_ws`` ordering
  in the FROZEN snapshot. Unlike the sibling contract file (which stayed
  >= 4x away from boundaries on purpose), this file deliberately sits ON
  the boundary — that is its job. If the live module ever deviates from
  the frozen snapshot, these probes must FAIL rather than the
  expectations being relaxed to fit.
- Exception assertions stay specific: ``ValueError`` for parse-level
  refusals (``JsonStreamError`` derives from it), and the dedicated
  ``SelectedErrorBudgetExceededError`` for budget refusals — never a
  bare ``Exception``.
- Missing module/attribute is an explicit INTERFACE RED via the
  importlib gate that runs BEFORE any ``pytest.raises`` block, mirroring
  ``tests/test_t35_json_error_stream_contract_20261113.py``; an expected
  raise can never mask the module's absence.
- Pure stdlib + pytest: no store, SQLite, process, thread, network, disk
  file, model, Worker, COMSOL or SSH. Every call feeds fixed 64-byte
  chunks; fixtures stay a few KiB.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import math
import sys

import pytest  # pyright: ignore[reportMissingImports]

MODULE_NAME = "comsol_mcp._json_error_stream"
FUNC_NAME = "project_error_chunks"
BUDGET_ERROR_NAME = "SelectedErrorBudgetExceededError"

# One fixed bounded chunk size for every call in this file; 64 also
# splits the multibyte fixtures mid-character on purpose.
CHUNK_BYTES = 64

# Generous: every number token below (<= ~4 KiB) is far under it, so a
# digit-limit refusal can never be a budget refusal in disguise.
GENEROUS_BUDGET_BYTES = 4096
TIGHT_BUDGET_BYTES = 64

DIGIT_LIMIT = 640            # smallest set_int_max_str_digits accepts (besides 0)
INT_AT_LIMIT_DIGITS = 640    # exactly at the limit -> legal for int()/json.loads
INT_OVER_LIMIT_DIGITS = 641  # one over -> ValueError
LIMIT_OFF_DIGITS = 5000      # above CPython's 4300 default limit

OVER_ERROR_CHARS = 300
SMALL_ERROR = "small-selected"

assert INT_OVER_LIMIT_DIGITS == DIGIT_LIMIT + 1
assert LIMIT_OFF_DIGITS > 4300
assert OVER_ERROR_CHARS + 2 > 4 * TIGHT_BUDGET_BYTES

# Exact-boundary fixtures: the recorded subtree text is bracketed and its
# raw UTF-8 byte length is the budget to hit (frozen candidate ``_record``
# overflows only on ``rec_bytes + blen > budget``).
EXACT_DOC = b'{"error":[1]}'
SELECTED_SUBTREE_BYTES = len(b"[1]")          # 3
INNER_WS_DOC = b'{"error":[  1  ]}'
INNER_WS_SUBTREE_BYTES = len(b"[  1  ]")      # 7
OUTER_WS_DOC = b'{ "error" :   [1]   }'       # subtree still b"[1]"
LITERAL_SUBTREE_BYTES = len('"é"'.encode("utf-8"))       # 4 raw bytes
ESCAPED_SUBTREE_BYTES = len('"\\u00e9"'.encode("utf-8"))  # 8 raw source bytes

assert SELECTED_SUBTREE_BYTES == 3
assert INNER_WS_SUBTREE_BYTES == SELECTED_SUBTREE_BYTES + 4
assert LITERAL_SUBTREE_BYTES == 4 and ESCAPED_SUBTREE_BYTES == 8


def _projection():
    """Prerequisite gate (call-time importlib lookup, like the sibling
    contract file): a missing module/attribute is an INTERFACE RED, never
    a bare ImportError and never hidden by an expected ``pytest.raises``."""
    try:
        module = importlib.import_module(MODULE_NAME)
    except ImportError as exc:
        return pytest.fail(
            f"INTERFACE RED: {MODULE_NAME} is not available "
            f"(ImportError: {exc}); this is an interface gap, not product behaviour"
        )
    try:
        func = getattr(module, FUNC_NAME)
    except AttributeError:
        return pytest.fail(f"INTERFACE RED: {MODULE_NAME} lacks {FUNC_NAME}")
    if not callable(func):
        return pytest.fail(f"INTERFACE RED: {MODULE_NAME}.{FUNC_NAME} is not callable")
    return func


def _budget_error_class():
    """Pin the dedicated budget exception class by name (mirrors the
    sibling budget test's direct import) and require it to be
    ValueError-derived, so budget assertions never widen to Exception."""
    try:
        module = importlib.import_module(MODULE_NAME)
    except ImportError as exc:
        return pytest.fail(
            f"INTERFACE RED: {MODULE_NAME} is not available "
            f"(ImportError: {exc}); this is an interface gap, not product behaviour"
        )
    cls = getattr(module, BUDGET_ERROR_NAME, None)
    if cls is None:
        return pytest.fail(f"INTERFACE RED: {MODULE_NAME} lacks {BUDGET_ERROR_NAME}")
    if not (isinstance(cls, type) and issubclass(cls, ValueError)):
        return pytest.fail(
            f"INTERFACE RED: {BUDGET_ERROR_NAME} must be a ValueError-derived class"
        )
    return cls


@contextlib.contextmanager
def _proc_int_max_str_digits(limit: int):
    """Set this interpreter's int digit limit for the duration of the
    block ONLY, restoring the captured original in ``finally``. Skips the
    test only when the interpreter lacks the API entirely; changes stay
    inside this process (no env/config/subprocess side effects). Yields
    the original limit so the caller can assert the restore happened."""
    if not hasattr(sys, "set_int_max_str_digits"):
        pytest.skip(
            "interpreter has no sys.set/get_int_max_str_digits; "
            "digit-limit differentials cannot run here"
        )
    original = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(limit)
        assert sys.get_int_max_str_digits() == limit, (
            "fixture drift: the digit-limit change did not take effect in-process"
        )
        yield original
    finally:
        sys.set_int_max_str_digits(original)


def _chunks(data: bytes) -> list:
    return [data[i:i + CHUNK_BYTES] for i in range(0, len(data), CHUNK_BYTES)]


def _json_equal(actual, expected) -> bool:
    """Scalar JSON equality that keeps non-finite floats signed and NaN
    NaN-equal, and never lets bool/float cross types."""
    if isinstance(expected, float) and not math.isfinite(expected):
        if isinstance(actual, bool) or not isinstance(actual, float):
            return False
        if math.isnan(expected):
            return math.isnan(actual)
        return math.isinf(actual) and (actual > 0) == (expected > 0)
    return type(actual) is type(expected) and actual == expected


# (case_id, token, oracle_accepts_at_640): 640 digits sit exactly ON the
# limit and stay legal; 641 crosses it; the sign must not shift the
# boundary (CPython counts digits, not the sign) — the oracle grounding
# inside each test verifies that instead of assuming it.
_DIGIT_TOKENS = (
    ("int-640-at-limit", "9" * INT_AT_LIMIT_DIGITS, True),
    ("neg-int-640-at-limit", "-" + "9" * INT_AT_LIMIT_DIGITS, True),
    ("int-641-over-limit", "9" * INT_OVER_LIMIT_DIGITS, False),
    ("neg-int-641-over-limit", "-" + "9" * INT_OVER_LIMIT_DIGITS, False),
)

# The big number sits where the LAST-wins projection ignores it: an
# independent sibling, or an early duplicate error superseded by the
# later small one.
_POSITIONS = {
    "sibling": lambda tok: '{"note": ' + tok + ', "error": "' + SMALL_ERROR + '"}',
    "dup-error-early": (
        lambda tok: '{"error": ' + tok + ', "error": "' + SMALL_ERROR + '"}'
    ),
}


@pytest.mark.parametrize("position_id", sorted(_POSITIONS))
@pytest.mark.parametrize(
    "case_id,token,oracle_accepts",
    _DIGIT_TOKENS,
    ids=[case_id for case_id, _, _ in _DIGIT_TOKENS],
)
def test_t35_digit_limit_640_integer_parity_at_ignored_positions(
    position_id, case_id, token, oracle_accepts
):
    """Differential at the exact digit boundary: the oracle and the
    candidate must make the SAME accept/reject decision for +/- 640- and
    641-digit integers sitting at ignored positions. A rejected token in
    the early-duplicate or sibling position must raise even though a
    valid small error follows — the later small error never rescues the
    over-limit integer."""
    text = _POSITIONS[position_id](token)
    data = text.encode("utf-8")
    project = _projection()
    with _proc_int_max_str_digits(DIGIT_LIMIT) as original:
        if oracle_accepts:
            grounding = json.loads(text)  # oracle accepts first
            assert grounding["error"] == SMALL_ERROR, (
                f"fixture drift: the oracle must parse {case_id} at an ignored "
                "position and still pick the small last-wins error"
            )
            actual = project(_chunks(data), max_error_bytes=GENEROUS_BUDGET_BYTES)
            assert actual == {"error": grounding["error"]}, (
                f"{case_id}/{position_id}: projection must equal the oracle "
                "error projection on the accept path"
            )
        else:
            with pytest.raises(ValueError):
                json.loads(text)  # oracle grounds the refusal first
            with pytest.raises(ValueError):
                project(_chunks(data), max_error_bytes=GENEROUS_BUDGET_BYTES)
    assert sys.get_int_max_str_digits() == original, (
        "digit-limit probe leaked its interpreter change"
    )


# Valid floats the oracle accepts at limit 640 even with >641-digit or
# >limit-digit parts: float() always succeeds (overflow -> inf,
# underflow -> 0.0). Contrast with _DIGIT_TOKENS above: the identical
# 641-digit INTEGER PART is refused only when no '.'/exponent follows.
_LONG_FLOAT_CASES = (
    ("int-part-641-frac-overflow", "9" * INT_OVER_LIMIT_DIGITS + ".5"),
    ("int-part-641-frac-exp-finite", "9" * INT_OVER_LIMIT_DIGITS + ".5e-500"),
    ("int-part-641-exp-underflow", "9" * INT_OVER_LIMIT_DIGITS + "e-1000"),
    ("long-exponent-overflow", "1.5e" + "9" * INT_OVER_LIMIT_DIGITS),
    ("long-exponent-underflow", "2.5e-" + "9" * INT_OVER_LIMIT_DIGITS),
)


@pytest.mark.parametrize(
    "case_id,token", _LONG_FLOAT_CASES, ids=[c for c, _ in _LONG_FLOAT_CASES]
)
def test_t35_long_float_literals_selected_under_digit_limit(case_id, token):
    """Selected (error-position) long float literals must be accepted at
    limit 640 exactly as the oracle does, and the projected value must
    equal the oracle's float — inf-aware, so overflow/underflow land on
    the same inf/0.0 the oracle produced instead of a refusal."""
    text = '{"error": ' + token + "}"
    project = _projection()
    with _proc_int_max_str_digits(DIGIT_LIMIT) as original:
        expected = json.loads(text)["error"]  # oracle grounding first
        actual = project(
            _chunks(text.encode("utf-8")), max_error_bytes=GENEROUS_BUDGET_BYTES
        )
        assert isinstance(actual, dict), "projection must return a dict"
        assert set(actual) == {"error"}
        assert _json_equal(actual["error"], expected), (
            f"{case_id}: selected float must be value-identical to the "
            "json.loads oracle (inf/NaN-aware), not refused"
        )
    assert sys.get_int_max_str_digits() == original, (
        "digit-limit probe leaked its interpreter change"
    )


def test_t35_long_float_in_ignored_position_is_not_refused_by_the_int_check():
    """The float exemption is not limited to recorded subtrees: a float
    with a 641-digit integer part in an IGNORED position must also pass
    both oracle and candidate at limit 640 (grammar-only validation in
    the skipped path, exactly like the oracle's float() route)."""
    token = "9" * INT_OVER_LIMIT_DIGITS + ".5"
    project = _projection()
    for position_id in sorted(_POSITIONS):
        text = _POSITIONS[position_id](token)
        with _proc_int_max_str_digits(DIGIT_LIMIT) as original:
            assert json.loads(text)["error"] == SMALL_ERROR, (
                "oracle grounding: the long float at an ignored position "
                "must be valid for json.loads at limit 640"
            )
            actual = project(
                _chunks(text.encode("utf-8")),
                max_error_bytes=GENEROUS_BUDGET_BYTES,
            )
            assert actual == {"error": SMALL_ERROR}, (
                f"{position_id}: candidate must not refuse a float the "
                "oracle accepts just because its integer part is long"
            )
        assert sys.get_int_max_str_digits() == original, (
            "digit-limit probe leaked its interpreter change"
        )


@pytest.mark.parametrize("position_id", sorted(_POSITIONS))
def test_t35_multi_thousand_digit_int_accepted_once_limit_disabled(position_id):
    """With the interpreter limit disabled (0): the oracle first accepts
    a 5000-digit integer at an ignored position (5000 digits exceed the
    4300 default, so this only differentiates with the limit off), and
    the candidate must agree and still project the small last-wins
    error. The context manager restores whatever the limit was."""
    token = "7" * LIMIT_OFF_DIGITS
    text = _POSITIONS[position_id](token)
    project = _projection()
    with _proc_int_max_str_digits(0) as original:
        grounding = json.loads(text)  # oracle acceptance first
        assert grounding["error"] == SMALL_ERROR, (
            "fixture drift: with limit 0 the oracle must accept a 5000-digit "
            "integer at an ignored position"
        )
        actual = project(
            _chunks(text.encode("utf-8")), max_error_bytes=GENEROUS_BUDGET_BYTES
        )
        assert actual == {"error": grounding["error"]}
    assert sys.get_int_max_str_digits() == original, (
        "digit-limit probe leaked its interpreter change"
    )


def test_t35_selected_source_budget_exact_boundary_accepts_and_one_short_raises():
    """Selected subtree is exactly 3 raw bytes (``[1]``): ``max_error_bytes
    == 3`` must be accepted (``_record`` overflows only on strictly
    greater) and ``max_error_bytes == 2`` must raise the dedicated budget
    class — the recorded source, not the decoded value, sits on the
    boundary."""
    cls = _budget_error_class()
    project = _projection()
    assert json.loads(EXACT_DOC) == {"error": [1]}
    for budget in (SELECTED_SUBTREE_BYTES, SELECTED_SUBTREE_BYTES + 1):
        actual = project(_chunks(EXACT_DOC), max_error_bytes=budget)
        assert actual == {"error": [1]}, (
            f"a subtree of exactly {SELECTED_SUBTREE_BYTES} source bytes must "
            f"fit a budget of {budget}"
        )
    with pytest.raises(cls):
        project(
            _chunks(EXACT_DOC), max_error_bytes=SELECTED_SUBTREE_BYTES - 1
        )


def test_t35_internal_whitespace_counts_and_colon_or_trailing_whitespace_does_not():
    """Whitespace INSIDE the selected subtree is part of its recorded raw
    source and moves the boundary (``[  1  ]`` = 7 bytes: 7 fits, 6
    raises); whitespace between the colon and the value, or after the
    value, is consumed outside the recording and never counts
    (``OUTER_WS_DOC``'s subtree is still 3 bytes and fits 3)."""
    cls = _budget_error_class()
    project = _projection()
    assert (
        json.loads(INNER_WS_DOC)
        == json.loads(OUTER_WS_DOC)
        == json.loads(EXACT_DOC)
        == {"error": [1]}
    ), "fixture drift: all three documents decode identically"
    actual = project(
        _chunks(INNER_WS_DOC), max_error_bytes=INNER_WS_SUBTREE_BYTES
    )
    assert actual == {"error": [1]}
    with pytest.raises(cls):
        project(
            _chunks(INNER_WS_DOC), max_error_bytes=INNER_WS_SUBTREE_BYTES - 1
        )
    actual = project(
        _chunks(OUTER_WS_DOC), max_error_bytes=SELECTED_SUBTREE_BYTES
    )
    assert actual == {"error": [1]}, (
        "colon-side and post-value whitespace must not consume the budget"
    )


def test_t35_budget_counts_raw_source_bytes_for_multibyte_and_escapes():
    """Same decoded error string, different budget decisions: ``"é"``
    costs 4 raw UTF-8 bytes and fits 4, while ``"\\u00e9"`` costs its 8
    literal source characters and refuses at 4 but fits at 8 — the
    budget bounds the raw source, not the decoded length."""
    cls = _budget_error_class()
    project = _projection()
    literal = '{"error":"é"}'.encode("utf-8")
    escaped = b'{"error":"\\u00e9"}'
    assert json.loads(literal) == json.loads(escaped) == {"error": "é"}
    assert project(_chunks(literal), max_error_bytes=LITERAL_SUBTREE_BYTES) == {
        "error": "é"
    }
    with pytest.raises(cls):
        project(_chunks(escaped), max_error_bytes=LITERAL_SUBTREE_BYTES)
    assert project(_chunks(escaped), max_error_bytes=ESCAPED_SUBTREE_BYTES) == {
        "error": "é"
    }


def test_t35_earlier_over_budget_error_is_replaced_by_small_later_error():
    """Positive control (kept distinct from the boundary probes): the
    FIRST top-level error is >> budget but the LAST one is tiny; the
    budget must not fire early — the projection returns the small
    last-wins error, identical to the oracle."""
    project = _projection()
    text = (
        '{"error": "' + "H" * OVER_ERROR_CHARS + '", "error": "' + SMALL_ERROR + '"}'
    )
    expected = json.loads(text)
    assert expected == {"error": SMALL_ERROR}, (
        "fixture drift: the oracle must pick the small last-wins error"
    )
    actual = project(_chunks(text.encode("utf-8")), max_error_bytes=TIGHT_BUDGET_BYTES)
    assert actual == expected, (
        "an earlier over-budget duplicate error must never force a refusal "
        "while a fitting later duplicate supersedes it"
    )


def test_t35_last_wins_over_budget_error_raises_in_reverse_order():
    """Reverse order: the LAST top-level error is the huge one, so the
    selection itself is over budget (302 bytes vs 64) and the call must
    raise the dedicated budget class — never return the earlier small
    error, never ``{}``, never truncate."""
    cls = _budget_error_class()
    project = _projection()
    text = (
        '{"error": "' + SMALL_ERROR + '", "error": "' + "H" * OVER_ERROR_CHARS + '"}'
    )
    expected = json.loads(text)
    assert expected == {"error": "H" * OVER_ERROR_CHARS}, (
        "fixture drift: the oracle's last-wins selection must be the huge error"
    )
    with pytest.raises(cls):
        project(_chunks(text.encode("utf-8")), max_error_bytes=TIGHT_BUDGET_BYTES)
