"""T35 store-slice tests: additive read-only SQLite event-prefix fold.

Scope and honesty boundaries (binding for every test in this file):

- The production surface exercised here is the new unwired internal method
  ``OperationStore.fold_job_event_prefix`` together with the already reviewed
  ``CanonicalJobEventDigests`` helper. It is an implementation slice, not a
  public MCP operation, and no solver route is wired to it by these tests.
- Synthetic fresh ``OperationStore(":memory:")`` ledgers only. No real file
  database, daemon, Worker, COMSOL, SSH, model access, historical UNKNOWN job,
  or prior denied outcome is touched; every job and event here is brand-new
  and synthetic. Every store is closed in ``finally``.
- The ``max_row_bytes`` admission is an application-side SQLite row-length
  bound only. These tests never measure or assert SQLite C-level memory,
  process RSS, disk usage, or real file permissions.
- Fold results intentionally contain no row/id/page lists; provisional
  visitor callbacks before an early stop must be treated as discardable
  state by callers, and two tests assert exactly that provisional shape.
- One test patches the old collector constant JOB_EVENT_SNAPSHOT_MAX_PAGES
  (128 -> 1) purely as a capacity simulation; it captures the original value
  and restores it in ``finally``. No production constant change is asserted.
- The author did not execute this file. The parent runs it verbatim under the
  identity gate; nothing here claims a pass or fail status.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from comsol_mcp import _operation_store as ops_store
from comsol_mcp._operation_store import OperationStore

RESULT_KEYS = {
    "job_id", "complete", "status", "expected_event_count", "fetched_event_count",
    "snapshot_last_id", "id_sha256", "events_sha256", "issues",
}

UNICODE_SAMPLES = [
    "中文日志",
    "😀 solver progress",
    "café naïve",
    "é combining e\u0301",
    "tab\tnewline\nrtl\u200f",
    "Ω≈ç√∫",
]


def _seed_job(store: OperationStore, tag: str) -> str:
    record, reused = store.begin(
        request_id=f"t35-fold-request-{tag}",
        idempotency_key=f"t35-fold-key-{tag}",
        request_hash=f"t35-fold-hash-{tag}",
        operation="experiment.stage_run",
    )
    assert reused is False
    assert record["job_id"]
    return record["job_id"]


def _whole_list_digests(rows: list[dict]) -> tuple[str, str]:
    """Recompute the v1 whole-list digest pair independently of the fold."""
    ids_sha = hashlib.sha256(
        ",".join(str(row["id"]) for row in rows).encode("ascii")
    ).hexdigest()
    events_sha = hashlib.sha256(
        json.dumps(
            rows, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return ids_sha, events_sha


def _fold_fresh(store: OperationStore, job_id: str, max_row_bytes: int, seen: list[dict]):
    return store.fold_job_event_prefix(
        job_id, snapshot_count=None, snapshot_last_id=None,
        max_row_bytes=max_row_bytes, visitor=seen.append,
    )


def _fold_continuation(store: OperationStore, job_id: str, snapshot_count: int,
                       snapshot_last_id: int, max_row_bytes: int, seen: list[dict]):
    return store.fold_job_event_prefix(
        job_id, snapshot_count=snapshot_count, snapshot_last_id=snapshot_last_id,
        max_row_bytes=max_row_bytes, visitor=seen.append,
    )


def _assert_incomplete(result: dict, seen: list[dict]) -> None:
    assert result["complete"] is False
    assert result["status"] == "INCOMPLETE"
    assert result["id_sha256"] is None, "incomplete results must not publish an id digest"
    assert result["events_sha256"] is None, "incomplete results must not publish an events digest"
    assert result["issues"], "an incomplete fold must carry a bounded early-stop reason"


def test_t35_fold_empty_prefix_complete_with_canonical_empty_hashes():
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "empty")
        seen: list[dict] = []
        result = _fold_fresh(store, job_id, 4096, seen)
        assert seen == []
        assert set(result) == RESULT_KEYS
        assert result["complete"] is True and result["status"] == "COMPLETE"
        assert result["expected_event_count"] == result["fetched_event_count"] == 0
        assert result["snapshot_last_id"] == 0
        assert result["id_sha256"] == hashlib.sha256(b"").hexdigest()
        assert result["events_sha256"] == hashlib.sha256(b"[]").hexdigest()
        assert result["issues"] == []
        # The existing collector agrees on the same canonical empty pair.
        snapshot = store.collect_job_event_snapshot(job_id)
        assert snapshot["complete"] is True
        assert result["id_sha256"] == snapshot["id_sha256"]
        assert result["events_sha256"] == snapshot["events_sha256"]
    finally:
        store.close()


def test_t35_fold_mixed_unicode_prefix_matches_collector_digests():
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "unicode")
        for index, text in enumerate(UNICODE_SAMPLES):
            store.add_event(job_id, "progress", {"index": index, "text": text})
        store.add_event(job_id, "progress", {})
        store.add_event(job_id, "nested", {"outer": {"inner": [1, 2, None]}})

        seen: list[dict] = []
        result = _fold_fresh(store, job_id, 65536, seen)
        snapshot = store.collect_job_event_snapshot(job_id)
        assert snapshot["complete"] is True and result["complete"] is True
        assert result["expected_event_count"] == result["fetched_event_count"] == 8
        assert result["snapshot_last_id"] == snapshot["snapshot_last_id"]
        assert result["id_sha256"] == snapshot["id_sha256"]
        assert result["events_sha256"] == snapshot["events_sha256"]
        expected_ids, expected_events = _whole_list_digests(snapshot["events"])
        assert result["id_sha256"] == expected_ids
        assert result["events_sha256"] == expected_events
        assert seen == snapshot["events"]
    finally:
        store.close()


def test_t35_fold_frozen_prefix_stable_after_later_append():
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "append")
        for index in range(3):
            store.add_event(job_id, "progress", {"index": index})
        first_seen: list[dict] = []
        first = _fold_fresh(store, job_id, 4096, first_seen)
        assert first["complete"] is True
        assert first["expected_event_count"] == first["fetched_event_count"] == 3
        frozen_last_id = first["snapshot_last_id"]

        store.add_event(job_id, "late-append", {"index": 3})
        seen: list[dict] = []
        second = _fold_continuation(store, job_id, 3, frozen_last_id, 4096, seen)
        assert second["complete"] is True and second["status"] == "COMPLETE"
        assert second["snapshot_last_id"] == frozen_last_id
        assert second["expected_event_count"] == second["fetched_event_count"] == 3
        assert second["id_sha256"] == first["id_sha256"]
        assert second["events_sha256"] == first["events_sha256"]
        assert seen == first_seen
        prefix_rows = store.events(job_id, 0, 1000)[:3]
        expected_ids, expected_events = _whole_list_digests(prefix_rows)
        assert second["id_sha256"] == expected_ids
        assert second["events_sha256"] == expected_events
        assert all(row["event"] != "late-append" for row in seen)
    finally:
        store.close()


def test_t35_fold_metadata_mutation_keeps_ids_but_changes_events_digest():
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "mutation")
        for index in range(2):
            store.add_event(job_id, "progress", {"index": index})
        first_seen: list[dict] = []
        first = _fold_fresh(store, job_id, 4096, first_seen)
        assert first["complete"] is True
        second_row_id = first_seen[1]["id"]

        store.db.execute(
            "UPDATE job_events SET metadata=? WHERE id=?",
            ('{"index": 1, "mutated": true}', second_row_id),
        )
        seen: list[dict] = []
        second = _fold_continuation(store, job_id, 2, first["snapshot_last_id"], 4096, seen)
        assert second["complete"] is True and second["status"] == "COMPLETE"
        assert second["id_sha256"] == first["id_sha256"], "ids survived so the id digest must match"
        assert second["events_sha256"] != first["events_sha256"], \
            "same ids with changed metadata must not publish false equality"
        snapshot = store.collect_job_event_snapshot(job_id)
        assert snapshot["complete"] is True
        assert second["events_sha256"] == snapshot["events_sha256"]
        assert seen[1]["metadata"] == {"index": 1, "mutated": True}
    finally:
        store.close()


def test_t35_fold_deletion_count_mismatch_incomplete_before_visitor():
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "deletion")
        for index in range(3):
            store.add_event(job_id, "progress", {"index": index})
        first_seen: list[dict] = []
        first = _fold_fresh(store, job_id, 4096, first_seen)
        assert first["complete"] is True
        middle_row_id = first_seen[1]["id"]

        store.db.execute("DELETE FROM job_events WHERE id=?", (middle_row_id,))
        seen: list[dict] = []
        result = _fold_continuation(store, job_id, 3, first["snapshot_last_id"], 4096, seen)
        _assert_incomplete(result, seen)
        assert seen == [], "a frozen-prefix mismatch must fail before any visitor call"
        assert result["expected_event_count"] == 3
        assert result["fetched_event_count"] == 0
        assert result["snapshot_last_id"] == first["snapshot_last_id"]
        assert any("prefix" in issue or "count" in issue for issue in result["issues"])
    finally:
        store.close()


def test_t35_fold_oversized_row_stops_before_visitor_with_provisional_callback():
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "oversize")
        store.add_event(job_id, "progress", {"index": 0})
        # The same bound must admit the small row before any big row exists.
        probe_seen: list[dict] = []
        admitted = _fold_fresh(store, job_id, 1000, probe_seen)
        assert admitted["complete"] is True and admitted["fetched_event_count"] == 1

        store.add_event(job_id, "progress", {"index": 1, "payload": "x" * 4000})
        seen: list[dict] = []
        result = _fold_fresh(store, job_id, 1000, seen)
        _assert_incomplete(result, seen)
        assert result["expected_event_count"] == 2
        assert result["fetched_event_count"] == 1
        assert len(seen) == 1 and seen[0]["metadata"] == {"index": 0}, \
            "the earlier callback is provisional state a caller must discard"
        assert seen[0]["metadata"].get("index") != 1, "the oversized row must never reach the visitor"
        assert any("byte" in issue for issue in result["issues"])
        assert len(result["issues"]) == 1, "issues stay bounded per early-stop reason"
    finally:
        store.close()


@pytest.mark.parametrize(
    "raw_metadata,case_id",
    [("not-json", "invalid-json"), ("NaN", "nonstandard-nan"), (None, "sql-null-metadata")],
    ids=["invalid-json", "nonstandard-nan", "sql-null-metadata"],
)
def test_t35_fold_malformed_metadata_fails_closed(raw_metadata, case_id):
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, f"malformed-{case_id}")
        store.add_event(job_id, "progress", {"index": 0})
        store.add_event(job_id, "progress", {"index": 1})
        second_row_id = store.db.execute(
            "SELECT id FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,),
        ).fetchone()[0]
        store.db.execute("UPDATE job_events SET metadata=? WHERE id=?", (raw_metadata, second_row_id))

        seen: list[dict] = []
        result = _fold_fresh(store, job_id, 65536, seen)
        _assert_incomplete(result, seen)
        assert len(seen) == 1 and seen[0]["metadata"] == {"index": 0}, \
            "only the provisional pre-failure callback may have happened"
        assert any("malformed" in issue or "NULL" in issue for issue in result["issues"])
        # The bad row is left untouched and the connection stays usable.
        assert store.db.execute(
            "SELECT metadata FROM job_events WHERE id=?", (second_row_id,),
        ).fetchone()[0] == raw_metadata
        assert store.db.in_transaction is False
        assert store.db.execute(
            "SELECT COUNT(*) FROM job_events WHERE job_id=?", (job_id,),
        ).fetchone()[0] == 2
    finally:
        store.close()


@pytest.mark.parametrize(
    "mutate,case_id",
    [
        (lambda store, job_id: store.db.execute(
            "UPDATE job_events SET event='' WHERE job_id=?", (job_id,)), "empty-event"),
        (lambda store, job_id: store.db.execute(
            "INSERT INTO job_events(id,job_id,event,metadata) VALUES(?,?,?,?)",
            (-7, job_id, "progress", "{}")), "nonpositive-id"),
    ],
    ids=["empty-event", "nonpositive-id"],
)
def test_t35_fold_foreign_shape_rows_fail_incomplete(mutate, case_id):
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, f"shape-{case_id}")
        for index in range(2):
            store.add_event(job_id, "progress", {"index": index})
        mutate(store, job_id)
        seen: list[dict] = []
        result = _fold_fresh(store, job_id, 65536, seen)
        _assert_incomplete(result, seen)
        assert result["id_sha256"] is None and result["events_sha256"] is None
        assert len(result["issues"]) == 1
    finally:
        store.close()


def test_t35_fold_visitor_exception_rolls_back_and_store_stays_usable():
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "visitor-raise")
        for index in range(3):
            store.add_event(job_id, "progress", {"index": index})
        seen: list[dict] = []

        def exploding_visitor(row: dict) -> None:
            if row["metadata"]["index"] == 1:
                raise RuntimeError("t35-synthetic-visitor-failure")
            seen.append(row)

        with pytest.raises(RuntimeError, match="t35-synthetic-visitor-failure"):
            store.fold_job_event_prefix(
                job_id, snapshot_count=None, snapshot_last_id=None,
                max_row_bytes=4096, visitor=exploding_visitor,
            )
        assert store.db.in_transaction is False, "the read transaction must be rolled back"
        assert len(store.events(job_id)) == 3
        replay_seen: list[dict] = []
        replay = _fold_fresh(store, job_id, 4096, replay_seen)
        assert replay["complete"] is True
        snapshot = store.collect_job_event_snapshot(job_id)
        assert replay["events_sha256"] == snapshot["events_sha256"]
        assert replay["id_sha256"] == snapshot["id_sha256"]
    finally:
        store.close()


_INVALID_FOLD_CALLS = [
    pytest.param({"snapshot_count": 5, "snapshot_last_id": None,
                  "max_row_bytes": 4096}, id="half-specified-count"),
    pytest.param({"snapshot_count": None, "snapshot_last_id": 5,
                  "max_row_bytes": 4096}, id="half-specified-last-id"),
    pytest.param({"snapshot_count": -1, "snapshot_last_id": -1,
                  "max_row_bytes": 4096}, id="negative-bounds"),
    pytest.param({"snapshot_count": 0, "snapshot_last_id": 7,
                  "max_row_bytes": 4096}, id="zero-count-nonzero-last-id"),
    pytest.param({"snapshot_count": 4, "snapshot_last_id": 0,
                  "max_row_bytes": 4096}, id="nonzero-count-zero-last-id"),
    pytest.param({"snapshot_count": True, "snapshot_last_id": True,
                  "max_row_bytes": 4096}, id="bool-bounds"),
    pytest.param({"snapshot_count": 5.0, "snapshot_last_id": 5,
                  "max_row_bytes": 4096}, id="float-count"),
    pytest.param({"snapshot_count": None, "snapshot_last_id": None,
                  "max_row_bytes": True}, id="bool-max-row-bytes"),
    pytest.param({"snapshot_count": None, "snapshot_last_id": None,
                  "max_row_bytes": 0}, id="zero-max-row-bytes"),
    pytest.param({"snapshot_count": None, "snapshot_last_id": None,
                  "max_row_bytes": -5}, id="negative-max-row-bytes"),
    pytest.param({"snapshot_count": None, "snapshot_last_id": None,
                  "max_row_bytes": 4096, "visitor": "not-callable"}, id="non-callable-visitor"),
]


@pytest.mark.parametrize("overrides", _INVALID_FOLD_CALLS)
def test_t35_fold_rejects_invalid_bounds_and_types(overrides):
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "bounds")
        kwargs = {"snapshot_count": None, "snapshot_last_id": None,
                  "max_row_bytes": 4096, "visitor": lambda row: None}
        kwargs.update(overrides)
        with pytest.raises(ValueError):
            store.fold_job_event_prefix(job_id, **kwargs)
        assert store.db.in_transaction is False, "arguments must be rejected before BEGIN"
    finally:
        store.close()


def test_t35_fold_missing_or_blank_job_id_controls():
    store = OperationStore(":memory:")
    try:
        with pytest.raises(ValueError):
            store.fold_job_event_prefix(
                "", snapshot_count=None, snapshot_last_id=None,
                max_row_bytes=4096, visitor=lambda row: None,
            )
        seen: list[dict] = []
        result = store.fold_job_event_prefix(
            "t35-no-such-job", snapshot_count=None, snapshot_last_id=None,
            max_row_bytes=4096, visitor=seen.append,
        )
        _assert_incomplete(result, seen)
        assert seen == []
        assert any("cardinality" in issue for issue in result["issues"])
        assert store.db.in_transaction is False
    finally:
        store.close()


def test_t35_fold_streams_many_rows_across_internal_batches_matching_collector():
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "batched")
        for index in range(1500):
            store.add_event(job_id, "progress", {"i": index})
        seen: list[dict] = []
        result = _fold_fresh(store, job_id, 512, seen)
        snapshot = store.collect_job_event_snapshot(job_id)
        assert snapshot["complete"] is True
        assert result["complete"] is True and result["status"] == "COMPLETE"
        assert result["expected_event_count"] == result["fetched_event_count"] == 1500
        assert result["id_sha256"] == snapshot["id_sha256"]
        assert result["events_sha256"] == snapshot["events_sha256"]
        assert len(seen) == 1500
        assert [row["id"] for row in seen] == [event["id"] for event in snapshot["events"]]
    finally:
        store.close()


def test_t35_fold_completes_past_reduced_collector_page_cap():
    """Capacity simulation only: the fold slice must not depend on the old
    collector's JOB_EVENT_SNAPSHOT_MAX_PAGES while the same 1002-event job is
    INCOMPLETE to the old collector. Not the real 128*1000 production bound."""
    original_max_pages = ops_store.JOB_EVENT_SNAPSHOT_MAX_PAGES
    assert original_max_pages == 128, "store default MAX_PAGES drifted from the reviewed baseline"
    store = OperationStore(":memory:")
    try:
        job_id = _seed_job(store, "past-cap")
        for index in range(1002):
            store.add_event(job_id, "progress", {"i": index})

        ops_store.JOB_EVENT_SNAPSHOT_MAX_PAGES = 1
        refused = store.collect_job_event_snapshot(job_id)
        assert refused["complete"] is False
        assert any("bounded page limit" in issue for issue in refused["issues"])

        seen: list[dict] = []
        result = _fold_fresh(store, job_id, 512, seen)
        assert result["complete"] is True and result["status"] == "COMPLETE"
        assert result["fetched_event_count"] == 1002
        assert len(seen) == 1002

        # Restore the constant, then let the unchanged collector confirm the
        # same prefix on the same store agrees with the fold's digests.
        ops_store.JOB_EVENT_SNAPSHOT_MAX_PAGES = original_max_pages
        accepted = store.collect_job_event_snapshot(job_id)
        assert accepted["complete"] is True
        assert result["id_sha256"] == accepted["id_sha256"]
        assert result["events_sha256"] == accepted["events_sha256"]
    finally:
        ops_store.JOB_EVENT_SNAPSHOT_MAX_PAGES = original_max_pages
        store.close()
