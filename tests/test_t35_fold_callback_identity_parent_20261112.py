"""T35 fold callback identity control on a new synthetic in-memory job.

A visitor receives a mutable row, and may locally annotate that row; no store
write or transaction operation is allowed from the callback.  A changed local
id must not redirect keyset iteration or alter the prefix digest.  This checks
only a Python callback model, not concurrent database writers or production
solver-route integration.
"""
from __future__ import annotations

from comsol_mcp._operation_store import OperationStore


def test_fold_visitor_local_id_mutation_does_not_redirect_iteration():
    store = OperationStore(":memory:")
    try:
        record, reused = store.begin(
            request_id="t35-fold-visitor-mutation-request",
            idempotency_key="t35-fold-visitor-mutation-key",
            request_hash="t35-fold-visitor-mutation-hash",
            operation="study.run",
        )
        assert reused is False
        job_id = record["job_id"]
        for index in range(3):
            store.add_event(job_id, "synthetic-progress", {"index": index})
        original = store.collect_job_event_snapshot(job_id)
        assert original["complete"] is True
        expected_ids = [event["id"] for event in original["events"]]
        seen: list[int] = []

        def visitor(row: dict) -> None:
            seen.append(row["id"])
            row["id"] = 2**40

        result = store.fold_job_event_prefix(
            job_id, snapshot_count=None, snapshot_last_id=None,
            max_row_bytes=4096, visitor=visitor,
        )
        assert result["complete"] is True, result
        assert result["expected_event_count"] == result["fetched_event_count"] == 3
        assert seen == expected_ids
        assert result["id_sha256"] == original["id_sha256"]
        assert result["events_sha256"] == original["events_sha256"]
        assert store.events(job_id) == original["events"]
    finally:
        store.close()
