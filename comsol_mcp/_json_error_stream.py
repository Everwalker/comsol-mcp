"""T35 candidate primitive: budgeted, single-pass projection of the
top-level ``"error"`` field of a JSON document fed as one-shot UTF-8 byte
chunks.

Engineering proposal under test by
``tests/test_t35_json_error_stream_contract_20261113.py`` (authored, NOT RUN
by this author; the parent owns execution, wiring and review). Production
motivation, read from source: ``_g3_solver_ops._stored_result_exception_chain``
/ ``_stored_result_error_summary`` read ``job["result"]["error"]`` out of a
fully ``json.loads``-decoded stored result, so this projection must AGREE
with that decode (duplicate top-level keys resolve to the LAST occurrence,
exactly as ``json.loads`` resolves them); decoding the whole document is the
memory-critical step for multi-MiB stored results, which is why this
projection streams.

Contract: ``project_error_chunks(chunks, *, max_error_bytes) -> dict``
----------------------------------------------------------------------
* ``chunks`` is a single-pass iterable of ``bytes``/``bytearray`` pieces of
  ONE UTF-8 JSON document. Zero-length pieces are transparent. The pieces
  are never concatenated into the whole document.
* The result is ``{"error": value}`` holding ONLY the last top-level
  ``"error"`` entry (CPython ``json.loads`` duplicate-key semantics,
  including keys that need unescaping: ``"err\\u006Fr"`` IS ``"error"``),
  or ``{}`` when no top-level ``error`` key exists or the root value is not
  an object (array/scalar/``null`` roots project to ``{}``).
* The WHOLE document is validated: syntax damage anywhere — including after
  a fully scanned error field — truncation, trailing garbage, empty or
  whitespace-only input, invalid UTF-8 bytes, and a leading BOM all raise
  (``JsonStreamError`` or a standard ``ValueError``/``TypeError``).
  Returning ``{}`` for an invalid document is forbidden by contract.
* ``max_error_bytes`` bounds the recorded source UTF-8 length of the
  SELECTED (last-wins) error subtree ONLY. If that final selection
  overflows the budget, raise ``SelectedErrorBudgetExceededError`` — never
  truncate, never disguise as "no error". An EARLIER duplicate error that
  overflowed the budget is tolerated (dropped) as long as the LAST error
  fits; the budget never fires while a later duplicate may still supersede.
* Semantics are pinned to CPython ``json.loads`` on the same bytes:
  ``NaN``/``Infinity``/``-Infinity`` accepted as values and preserved;
  ``\\uXXXX`` escapes (including unpaired surrogates) accepted exactly as
  ``json.loads`` accepts them; ASCII-only number lexing (rejects leading
  zeros, ``+``, ``.5``, ``1.``, ``1e``); integer literals whose digit
  count exceeds this interpreter's ``int()`` digit limit are rejected
  HERE in digit-count parity with ``json.loads`` (a limit configured via
  ``sys.set_int_max_str_digits`` is honoured at check time; interpreters
  without ``sys.get_int_max_str_digits`` impose no such limit and long
  integers are accepted); every syntactically valid float literal is
  accepted, because ``float()`` always succeeds on it (overflow ->
  ``inf``, underflow -> ``0.0``), exactly as the oracle does; non-ASCII
  whitespace and control characters outside strings are rejected.
* Byte-level parity is scoped to the strict-UTF-8, no-BOM input
  contract: it is NOT claimed to be full ``json.loads(bytes)``
  equivalence. ``json.loads(bytes)`` sniffs encodings and strips a
  leading UTF-8 BOM (and accepts UTF-16/UTF-32 BOM forms); this
  projection deliberately refuses a BOM and non-strict UTF-8 as an
  explicit input-contract boundary, not as an accidental gap.
* The selected error subtree is materialized by calling ``json.loads`` on
  the RECORDED source text of exactly that subtree — never by a parallel
  hand-rolled value builder — so numbers, escapes, nested duplicate keys
  and NaN/Infinity land bit-identically to the oracle decode.

Memory bounds (what stays resident while streaming)
---------------------------------------------------
These bounds describe THIS module's own Python allocations for bounded
caller chunks; they are not a whole-system memory bound (the caller's
own chunk buffers are outside this module) and carry no equivalence
claim at arbitrary nesting depth or for non-strict-UTF-8 input.

* Input: at most one caller chunk plus a constant look-ahead window
  (consumed prefix is trimmed); callers may therefore require each chunk
  to be bounded, and no call ever holds the whole document.
* Non-selected top-level values are validated character-by-character and
  discarded; numbers are validated with O(1) counters — ``_scan_number``
  never accumulates the token text — so a multi-MiB number outside the
  recorded subtree allocates nothing token-sized. Top-level keys are
  kept only while they can still unescape to
  ``error`` (at most the 30 characters ``\\uXXXX`` x 5 can produce), so huge
  keys are not retained either.
* The selected error subtree is retained ONLY while it fits: the record
  buffer is capped at ``max_error_bytes`` (overflow switches that subtree
  to validate-and-drop mode immediately); whitespace consumed inside an
  active recording goes through the recording path too, so the budget
  bounds the byte-exact raw source of the subtree, whitespace included.
* At the end, only the single selected subtree (<= ``max_error_bytes``)
  is handed to ``json.loads``, so the returned value's size is bounded
  by the budget, never by the whole document.
* Nesting is capped at ``MAX_DEPTH``; deeper documents raise explicitly
  (never accepted silently, never allowed to crash the interpreter).
  CPython ``json.loads`` itself fails with ``RecursionError`` near ~1000
  nesting, so this is an explicit, documented unsupported boundary, not a
  silent semantic gap.

This module is intentionally WIRED TO NOTHING yet: no caller imports it;
the parent reviews, runs, and wires it separately.
"""
from __future__ import annotations

import codecs
import json
import sys
from collections.abc import Iterable

__all__ = [
    "project_error_chunks",
    "JsonStreamError",
    "SelectedErrorBudgetExceededError",
    "MAX_DEPTH",
]

# Explicit supported-nesting boundary. Kept below the interpreter's default
# recursion limit for the 2-frames-per-level scanner, and near (slightly
# under) the depth at which CPython json.loads itself stops; documents
# beyond it are refused with an explanatory JsonStreamError.
MAX_DEPTH = 400

# "error" is 5 characters; each character's longest JSON escape form is
# "\uXXXX" (6 characters), so a top-level key that could unescape to
# "error" needs at most 30 characters between the quotes. Longer keys can
# never be the selected key and are validated without retention.
_KEY_RAW_LIMIT = 30

_WS = frozenset(" \t\n\r")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_SHORT_ESCAPES = frozenset('"\\/bfnrt')
# First character -> the full literal token accepted at any value position.
# NaN/Infinity/-Infinity are Python-legal json.loads extensions, mirrored
# here exactly (only these spellings; "nan", "NAN", "-NaN", "inf" fail).
_LITERAL_WORDS = {
    "t": "true",
    "f": "false",
    "n": "null",
    "N": "NaN",
    "I": "Infinity",
}


class JsonStreamError(ValueError):
    """The document stream is not a valid single JSON document for this
    projection (syntax damage anywhere, truncation, trailing garbage,
    empty input, invalid UTF-8, unsupported boundary such as excessive
    nesting, or an integer literal beyond this interpreter's ``int()``
    digit limit). Subclasses ``ValueError`` so standard
    ``except ValueError`` handling around ``json.loads`` keeps working."""


class SelectedErrorBudgetExceededError(JsonStreamError):
    """The whole document was valid, but the SELECTED (last-wins) top-level
    ``error`` subtree exceeded ``max_error_bytes``. Raised instead of
    truncating the subtree or disguising the result as ``{}``."""


def _utf8_len(ch: str) -> int:
    """Exact UTF-8 byte length of one code point (input text never holds
    surrogates: the stream decoder is strict UTF-8)."""
    o = ord(ch)
    if o < 0x80:
        return 1
    if o < 0x800:
        return 2
    if o < 0x10000:
        return 3
    return 4


def _int_digit_limit() -> int | None:
    """This interpreter's decimal digit limit for ``int(str)``, or None
    when it has no such limit. Read at check time so a limit configured
    via ``sys.set_int_max_str_digits`` (Python >= 3.10.7) is honoured;
    older 3.10.x builds lack the getter entirely (int() then accepts any
    digit count), and a reported limit of 0 means the limit is off."""
    getter = getattr(sys, "get_int_max_str_digits", None)
    if getter is None:
        return None
    limit = getter()
    return limit if limit > 0 else None


class _CharStream:
    """Incremental strict-UTF-8 bytes->str feed over one-shot byte chunks.

    Residency: one chunk's decoded text plus a small unconsumed window;
    the consumed prefix is trimmed, so the full document is never
    concatenated. Invalid UTF-8 (including truncated multi-byte
    sequences at the final flush, raw lone surrogates such as
    surrogatepass bytes, and CESU forms) raises JsonStreamError. This
    strict-UTF-8, no-BOM decoding is the module's input contract and is
    NOT presented as equivalence with ``json.loads(bytes)``, which also
    sniffs encodings and strips a leading UTF-8 BOM: BOM'd and
    non-strict-UTF-8 streams are deliberately refused here.
    """

    def __init__(self, chunks: Iterable[bytes]) -> None:
        try:
            self._iter = iter(chunks)
        except TypeError as exc:
            raise TypeError(
                "chunks must be an iterable of bytes/bytearray pieces, "
                f"not {type(chunks).__name__}"
            ) from exc
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buf = ""
        self._size = 0
        self._pos = 0
        self._eof = False

    def _fill(self) -> bool:
        """Pull chunks until at least one new character lands; False at the
        real end of input (after flushing the decoder)."""
        while True:
            if self._eof:
                return False
            try:
                chunk = next(self._iter)
            except StopIteration:
                try:
                    tail = self._decoder.decode(b"", True)
                except UnicodeDecodeError as exc:
                    raise JsonStreamError(
                        f"stream ended inside an incomplete UTF-8 sequence: {exc}"
                    ) from exc
                self._eof = True
                if tail:
                    self._buf += tail
                    self._size = len(self._buf)
                    return True
                return False
            if not isinstance(chunk, (bytes, bytearray)):
                raise TypeError(
                    "each chunk must be bytes or bytearray, got "
                    f"{type(chunk).__name__} (str chunks and byte-bytes of a "
                    "bare bytes object are not chunk pieces)"
                )
            if not chunk:
                continue  # zero-length pieces are transparent
            try:
                text = self._decoder.decode(bytes(chunk))
            except UnicodeDecodeError as exc:
                raise JsonStreamError(f"invalid UTF-8 byte chunk: {exc}") from exc
            if text:
                self._buf += text
                self._size = len(self._buf)
                return True
            # else: chunk decoded to nothing (mid-sequence continuation);
            # pull the next piece of the split character.

    def ensure(self, n: int) -> bool:
        """True when at least ``n`` unconsumed characters are available."""
        while self._size - self._pos < n:
            if not self._fill():
                return False
        if self._pos >= 65536:  # trim the consumed prefix; window stays O(1)
            self._buf = self._buf[self._pos:]
            self._size = len(self._buf)
            self._pos = 0
        return True

    def peek(self, n: int) -> str:
        """Next up-to-``n`` characters without consuming (short at EOF)."""
        self.ensure(n)
        return self._buf[self._pos:self._pos + n]

    def take(self) -> str | None:
        if not self.ensure(1):
            return None
        ch = self._buf[self._pos]
        self._pos += 1
        return ch


class _Projector:
    """Single-pass scanner that validates the whole document while
    retaining only: the still-possible top-level key (<= 30 chars) and the
    in-budget recording of the newest top-level ``error`` value."""

    def __init__(self, stream: _CharStream, max_error_bytes: int) -> None:
        self._stream = stream
        self._budget = max_error_bytes
        # Active error-value recording (None = validate-and-drop mode).
        self._rec: list[str] | None = None
        self._rec_bytes = 0
        self._rec_overflowed = False
        # Final selection state (last-wins among top-level "error" keys).
        self._selected_text: str | None = None
        self._selected_over = False

    # ---- low-level consumption --------------------------------------

    def _peek(self) -> str | None:
        window = self._stream.peek(1)
        return window[0] if window else None

    def _peek_at(self, i: int) -> str | None:
        window = self._stream.peek(i + 1)
        return window[i] if len(window) > i else None

    def _record(self, ch: str) -> None:
        if self._rec is None:
            return
        blen = _utf8_len(ch)
        if self._rec_bytes + blen > self._budget:
            # This error candidate is over budget; validate the remainder
            # without retaining it. A later duplicate key may still win.
            self._rec_overflowed = True
            self._rec = None
            self._rec_bytes = 0
        else:
            self._rec.append(ch)
            self._rec_bytes += blen

    def _take(self) -> str | None:
        """Consume one character into the active recording (if any)."""
        ch = self._stream.take()
        if ch is not None:
            self._record(ch)
        return ch

    def _take_required(self, context: str) -> str:
        """Consume one character the caller has already peeked and proved
        present; the None branch is purely defensive so static checkers and
        pathological stream bugs fail loudly, never append silently."""
        ch = self._take()
        if ch is None:
            raise JsonStreamError(
                f"Expecting {context}: JSON stream ended unexpectedly"
            )
        return ch

    def _skip_ws(self) -> str | None:
        """Skip inter-token JSON whitespace. Whitespace consumed while a
        subtree recording is active goes through ``_take``, so it lands
        in the recorded text and counts against ``max_error_bytes``: the
        budget bounds the byte-exact raw source of the subtree, internal
        whitespace included. Whitespace outside any active recording is
        consumed and discarded."""
        while True:
            ch = self._peek()
            if ch is None or ch not in _WS:
                return ch
            self._take()

    def _match_word(self, word: str) -> None:
        window = self._stream.peek(len(word))
        if len(window) < len(word):
            raise JsonStreamError(
                f"Expecting literal {word!r} but the stream ended at {window!r}"
            )
        for expected in word:
            got = self._take()
            if got != expected:
                raise JsonStreamError(
                    f"Expecting literal {word!r}: unexpected character {got!r}"
                )

    # ---- tokens ------------------------------------------------------

    def _scan_string(self, as_key: bool) -> str | None:
        """Validate one string token. With ``as_key`` (top-level object
        keys only) keep its raw inner characters while they can still
        unescape to ``"error"`` and decode them with ``json.loads`` so
        escape semantics match the oracle key resolution exactly. Returns
        the decoded key, or None when not tracked / over the retention cap."""
        raw: list[str] | None = [] if as_key else None
        raw_len = 0

        def push(ch: str) -> None:
            nonlocal raw, raw_len
            if raw is None:
                return
            raw_len += 1
            if raw_len > _KEY_RAW_LIMIT:
                raw = None  # can never unescape to "error": stop retaining
            else:
                raw.append(ch)

        open_quote = self._take()
        if open_quote != '"':
            raise JsonStreamError('Expecting \'"\' to open a string')
        while True:
            ch = self._take()
            if ch is None:
                raise JsonStreamError("Unterminated string: stream ended inside the string")
            if ch == '"':
                break
            if ch == "\\":
                esc = self._take()
                if esc is None:
                    raise JsonStreamError("Unterminated string: stream ended after a backslash")
                if esc in _SHORT_ESCAPES:
                    push(ch)
                    push(esc)
                elif esc == "u":
                    push(ch)
                    push(esc)
                    for _ in range(4):
                        hexch = self._take()
                        if hexch is None or hexch not in _HEX_DIGITS:
                            raise JsonStreamError(
                                "Invalid \\u escape: expecting four hex digits"
                            )
                        push(hexch)
                else:
                    raise JsonStreamError(
                        f"Invalid escape sequence in string: '\\{esc}'"
                    )
            elif ord(ch) < 0x20:
                raise JsonStreamError(
                    f"Invalid control character (0x{ord(ch):02x}) inside string"
                )
            else:
                push(ch)
        if raw is None:
            return None
        try:
            return json.loads('"' + "".join(raw) + '"')
        except ValueError as exc:  # defensive: the text was validated above
            raise JsonStreamError(
                f"internal inconsistency: retained key text failed to re-decode: {exc}"
            ) from exc

    def _scan_number(self) -> None:
        """Validate the CPython/JSON number grammar (ASCII digits only,
        no leading zeros, no bare ``+``, frac/exp need digits) with O(1)
        state: the token text is never accumulated, so a multi-MiB
        number outside the recorded subtree allocates nothing
        token-sized.

        Oracle accept/reject parity WITHOUT materializing the literal:
          * integer literal (no frac, no exp): the oracle's ``int()``
            conversion fails on it exactly when its digit count exceeds
            this interpreter's digit limit, so counting digits here
            reproduces the oracle decision (including a limit configured
            via ``sys.set_int_max_str_digits``, and no limit on
            interpreters lacking that getter);
          * float literal: ``float()`` always succeeds on a
            syntactically valid JSON float (huge mantissa/underflow ->
            ``0.0``, huge exponent/overflow -> ``inf``), so grammar
            validation alone matches the oracle and long float literals
            the oracle accepts are never refused here.

        The digit-limit refusal is checked only AFTER the whole token
        has been scanned, because a huge integer part followed by ``.``
        or an exponent is a float the oracle accepts."""
        int_digits = 0
        ch = self._peek()
        if ch == "-":
            self._take_required("number minus sign")
            ch = self._peek()
        if ch == "0":
            self._take_required("'0'")
            int_digits = 1
        elif ch is not None and "1" <= ch <= "9":
            while True:
                ch = self._peek()
                if ch is None or not ("0" <= ch <= "9"):
                    break
                self._take_required("digit")
                int_digits += 1
        else:
            raise JsonStreamError("Expecting a digit in number literal")
        is_float = False
        ch = self._peek()
        if ch == ".":
            is_float = True
            self._take_required("'.'")
            ch = self._peek()
            if ch is None or not ("0" <= ch <= "9"):
                raise JsonStreamError("Expecting digit after '.' in number")
            while True:
                ch = self._peek()
                if ch is None or not ("0" <= ch <= "9"):
                    break
                self._take_required("digit")
        ch = self._peek()
        if ch in ("e", "E"):
            is_float = True
            self._take_required("exponent marker")
            ch = self._peek()
            if ch in ("+", "-"):
                self._take_required("exponent sign")
            ch = self._peek()
            if ch is None or not ("0" <= ch <= "9"):
                raise JsonStreamError("Expecting digit in number exponent")
            while True:
                ch = self._peek()
                if ch is None or not ("0" <= ch <= "9"):
                    break
                self._take_required("exponent digit")
        if not is_float:
            limit = _int_digit_limit()
            if limit is not None and int_digits > limit:
                raise JsonStreamError(
                    f"integer literal with {int_digits} digits exceeds "
                    f"this interpreter's int() digit limit ({limit}); "
                    "json.loads rejects the same literal for the same "
                    "reason"
                )

    # ---- containers (recording is transparent to structure) ----------

    def _scan_value(self, depth: int) -> None:
        ch = self._peek()
        if ch is None:
            raise JsonStreamError("Expecting value: JSON stream ended unexpectedly")
        if ch == "{":
            self._scan_object(depth)
            return
        if ch == "[":
            self._scan_array(depth)
            return
        if ch == '"':
            self._scan_string(as_key=False)
            return
        word = _LITERAL_WORDS.get(ch)
        if word is not None:
            self._match_word(word)
            return
        if ch == "-" and self._peek_at(1) == "I":
            self._match_word("-Infinity")
            return
        if ch == "-" or "0" <= ch <= "9":
            self._scan_number()
            return
        raise JsonStreamError(f"Expecting value: unexpected character {ch!r}")

    def _scan_array(self, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise JsonStreamError(
                f"nesting exceeds the explicitly supported MAX_DEPTH={MAX_DEPTH}; "
                "deeper documents are refused rather than accepted blindly "
                "(CPython json.loads itself hits RecursionError near ~1000)"
            )
        self._take()  # '['
        self._skip_ws()
        if self._peek() == "]":
            self._take()
            return
        while True:
            self._scan_value(depth + 1)
            self._skip_ws()
            ch = self._peek()
            if ch == ",":
                self._take()
                self._skip_ws()
                continue
            if ch == "]":
                self._take()
                return
            raise JsonStreamError("Expecting ',' delimiter or ']' in array")

    def _scan_object(self, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise JsonStreamError(
                f"nesting exceeds the explicitly supported MAX_DEPTH={MAX_DEPTH}; "
                "deeper documents are refused rather than accepted blindly "
                "(CPython json.loads itself hits RecursionError near ~1000)"
            )
        track_key = depth == 1  # only the ROOT object's keys are top level
        self._take()  # '{'
        self._skip_ws()
        if self._peek() == "}":
            self._take()
            return
        while True:
            if self._peek() != '"':
                raise JsonStreamError(
                    "Expecting property name enclosed in double quotes"
                )
            key = self._scan_string(as_key=track_key)
            self._skip_ws()
            if self._peek() != ":":
                raise JsonStreamError("Expecting ':' after object key")
            self._take()
            self._skip_ws()
            is_error = track_key and key == "error"
            if is_error:
                # Duplicate top-level keys are last-wins in json.loads:
                # every new top-level "error" value REPLACES the selection,
                # including one that overflows the budget (decided at end
                # of stream, so an earlier over-budget error never forces
                # a premature refusal).
                self._rec = []
                self._rec_bytes = 0
                self._rec_overflowed = False
            self._scan_value(depth + 1)
            if is_error:
                if self._rec_overflowed or self._rec is None:
                    self._selected_text = None
                    self._selected_over = True
                else:
                    self._selected_text = "".join(self._rec)
                    self._selected_over = False
                self._rec = None
            self._skip_ws()
            ch = self._peek()
            if ch == ",":
                self._take()
                self._skip_ws()
                continue
            if ch == "}":
                self._take()
                return
            raise JsonStreamError("Expecting ',' delimiter or '}' in object")


def project_error_chunks(chunks: Iterable[bytes], *, max_error_bytes: int) -> dict:
    """Project the last top-level ``"error"`` value out of a JSON document
    delivered as one-shot UTF-8 byte chunks, without holding the whole
    document. See the module docstring for the full contract, the exact
    budget accounting (recorded source UTF-8 bytes of the selected
    subtree), and the memory/depth bounds.

    Returns ``{}`` ONLY for a fully valid document that has no top-level
    ``error`` key or whose root is not an object. Invalid documents and
    over-budget selections raise.
    """
    if isinstance(max_error_bytes, bool) or not isinstance(max_error_bytes, int):
        raise ValueError(
            "max_error_bytes must be a non-negative int, got "
            f"{type(max_error_bytes).__name__}"
        )
    if max_error_bytes < 0:
        raise ValueError("max_error_bytes must not be negative")

    stream = _CharStream(chunks)
    projector = _Projector(stream, max_error_bytes)
    try:
        head = projector._skip_ws()
        if head is None:
            raise JsonStreamError(
                "input stream is empty or whitespace-only: an empty document "
                "is invalid JSON, not 'no error'"
            )
        if head == "{":
            projector._scan_object(depth=1)
        else:
            # Non-object roots (array/scalar/literal) project to {} but are
            # still fully validated; no key of a nested object inside an
            # array root is ever "top level".
            projector._scan_value(depth=1)
        if projector._skip_ws() is not None:
            raise JsonStreamError(
                "Extra data: trailing content after the JSON document"
            )
    except RecursionError as exc:
        raise JsonStreamError(
            "document nesting exceeded the interpreter recursion limit; "
            f"nesting beyond MAX_DEPTH={MAX_DEPTH} is an explicitly "
            "unsupported boundary"
        ) from exc

    selected_text = projector._selected_text
    if selected_text is None and not projector._selected_over:
        return {}
    if projector._selected_over:
        raise SelectedErrorBudgetExceededError(
            f"the selected last-wins top-level 'error' subtree exceeds "
            f"max_error_bytes={max_error_bytes}; refusing rather than "
            "truncating or disguising it as 'no error'"
        )
    if selected_text is None:
        raise JsonStreamError(
            "internal inconsistency: selection state is neither a recorded "
            "subtree text nor an overflow flag"
        )
    try:
        selected = json.loads(selected_text)
    except ValueError as exc:  # defensive: the text was validated while recording
        raise JsonStreamError(
            f"internal inconsistency: recorded error subtree failed to decode: {exc}"
        ) from exc
    return {"error": selected}
