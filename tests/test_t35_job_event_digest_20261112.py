"""Synthetic tests for CanonicalJobEventDigests (T35 v1 event-stream digests).

All fixtures are constructed here; no OperationStore, Worker, model, or
network state is touched.  Every expected digest is recomputed independently
with the stdlib hashlib/json originals over the whole row list, never with
the class under test.  These tests are written but not executed by the
authoring agent; the parent runs them.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from comsol_mcp._job_event_digest import CanonicalJobEventDigests


def _reference_digests(rows: list[dict]) -> tuple[str, str]:
    """Original v1 whole-list spec, computed independently of the helper."""
    canonical = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    events_hex = hashlib.sha256(canonical).hexdigest()
    ids_hex = hashlib.sha256(
        ",".join(str(row["id"]) for row in rows).encode("ascii")
    ).hexdigest()
    return ids_hex, events_hex


def _mixed_rows() -> list[dict]:
    """Nested Unicode, escapes/controls, numbers, booleans and null."""
    return [
        {"id": 1, "event": "job_created", "metadata": {"note": "创建"}},
        {"id": 2, "event": "worker_request",
         "metadata": {"text": "ключ \"quote\" back\\slash tab\tnew\nline \u0001ctl",
                      "пояс": {"вложен": ["δ", "😀"]}}},
        {"id": 7, "event": "progress_chunk",
         "numbers": {"neg": -12, "exp": 1.5e-9, "float": 3.25, "big": 10 ** 25},
         "flags": [True, False, None], "empty": [{}, [], ""]},
        {"id": 12345678901234567890, "event": "terminal",
         "metadata": {"status": "SUCCEEDED", "ключ\\": {"嵌套": "值"}}},
    ]


def _seeded(rows: list[dict]) -> tuple[CanonicalJobEventDigests, tuple[str, str]]:
    digest = CanonicalJobEventDigests()
    for row in rows:
        digest.add(row)
    return digest, digest.snapshot()


def test_t35_jed_empty_sequence_matches_reference():
    digest = CanonicalJobEventDigests()
    assert digest.count == 0
    assert digest.snapshot() == _reference_digests([])
    assert digest.snapshot() == digest.snapshot()
    assert digest.count == 0


def test_t35_jed_single_row_matches_reference():
    row = {"id": 1, "event": "x", "metadata": {"u": "ünïcøde ✓"}}
    digest, snapshot = _seeded([row])
    assert snapshot == _reference_digests([row])
    assert digest.count == 1


def test_t35_jed_every_prefix_matches_reference():
    rows = _mixed_rows()
    digest = CanonicalJobEventDigests()
    for index, row in enumerate(rows):
        digest.add(row)
        assert digest.count == index + 1
        assert digest.snapshot() == _reference_digests(rows[:index + 1])


def test_t35_jed_snapshot_is_nondestructive_and_append_continues():
    rows = _mixed_rows()
    digest, first = _seeded(rows[:2])
    assert digest.snapshot() == first
    assert digest.snapshot() == first
    assert digest.count == 2
    for row in rows[2:]:
        digest.add(row)
    assert digest.snapshot() == _reference_digests(rows)
    assert digest.count == len(rows)


@pytest.mark.parametrize("bad_row", [
    pytest.param([1, 2], id="not-a-mapping"),
    pytest.param({"event": "missing-id"}, id="missing-id"),
    pytest.param({"id": True, "event": "bool-id"}, id="bool-id"),
    pytest.param({"id": 3.0, "event": "float-id"}, id="float-id"),
    pytest.param({"id": None, "event": "null-id"}, id="null-id"),
    pytest.param({"id": 0, "event": "zero-id"}, id="zero-id"),
    pytest.param({"id": -5, "event": "negative-id"}, id="negative-id"),
    pytest.param({"id": float("nan"), "event": "nan-id"}, id="nan-id"),
    pytest.param({"id": 2, "event": "duplicate-id"}, id="duplicate-id"),
    pytest.param({"id": 1, "event": "descending-id"}, id="descending-id"),
])
def test_t35_jed_bad_identity_leaves_state_unchanged(bad_row):
    rows = _mixed_rows()
    digest, before = _seeded(rows[:2])
    expected_error = TypeError if isinstance(bad_row, list) else ValueError
    with pytest.raises(expected_error):
        digest.add(bad_row)  # type: ignore[arg-type]  # deliberate invalid input
    assert digest.snapshot() == before
    assert digest.count == 2
    digest.add(rows[2])
    digest.add(rows[3])
    assert digest.snapshot() == _reference_digests(rows)


@pytest.mark.parametrize("bad_row", [
    pytest.param({"id": 3, "event": "unserializable", "payload": object()},
                 id="object-payload"),
    pytest.param({"id": 3, "event": "nan-payload", "payload": float("nan")},
                 id="nan-payload"),
    pytest.param({"id": 3, "event": "inf-payload", "payload": float("inf")},
                 id="inf-payload"),
])
def test_t35_jed_unserializable_row_leaves_state_unchanged(bad_row):
    rows = _mixed_rows()
    digest, before = _seeded(rows[:2])
    with pytest.raises((TypeError, ValueError)):
        digest.add(bad_row)
    assert digest.snapshot() == before
    assert digest.count == 2
    digest.add(rows[2])
    digest.add(rows[3])
    assert digest.snapshot() == _reference_digests(rows)


def test_t35_jed_count_is_read_only():
    digest = CanonicalJobEventDigests()
    with pytest.raises(AttributeError):
        digest.count = 1
    digest.add({"id": 4, "event": "x"})
    assert digest.count == 1
    with pytest.raises(AttributeError):
        digest.count = 0
