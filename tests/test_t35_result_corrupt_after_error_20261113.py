"""Large invalid JSON after a captured error must not be accepted.

Uses a task-created :memory: ledger and the real solver-log reader. The error
subtree is intact, but a syntactically invalid sibling occurs after it and
beyond 4 MiB. This is a negative control for any future bounded projection;
it does not touch a real job, model, Worker, network, process or disk ledger.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from comsol_mcp import _g3_solver_ops as ops
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore
from tests import test_t35_result_stream_compat_extra_20261113 as fixture

assert Path(fixture.__file__).resolve() == (
    Path(__file__).resolve().parent / "test_t35_result_stream_compat_extra_20261113.py"
)


def test_large_result_corruption_after_error_fails_closed_and_preserves_bytes() -> None:
    project = "t35-corrupt-after-synthetic-project"
    marker = "t35-corrupt-after-synthetic-marker"
    error = {"code": "T35_SYNTHETIC", "cause_chain": [{"message": marker}]}
    store = OperationStore(":memory:")
    try:
        record = fixture._start_job(store, project=project, tag="corrupt-after")
        job_id, operation_id = record["job_id"], record["operation_id"]
        fixture._add_small_events(store, job_id)
        fixture._finish_failed(store, operation_id, {"success": False})
        rows_before = fixture._rows_signature(fixture._stored_rows(store, job_id))

        # sort_keys puts error before padding and the damaged tail.
        valid = json.dumps({"error": error, "padding": "P" * (5 * 1024 * 1024),
                            "tail": {"items": [1, 2, 3]}}, sort_keys=True)
        assert json.loads(valid)["error"] == error
        needle = '"items": [1, 2, 3]'
        replacement = '"items": [1,, 2, 3]'
        assert valid.count(needle) == 1
        assert valid.index('"error"') < valid.index(needle)
        assert valid.index(needle) > ops._MAX_LOG_EVENT_ROW_BYTES
        corrupted = valid.replace(needle, replacement, 1)
        with pytest.raises(json.JSONDecodeError):
            json.loads(corrupted)
        assert json.loads(json.dumps(error)) == error
        del valid
        cursor = store.db.execute(
            "UPDATE operations SET result=? WHERE operation_id=?", (corrupted, operation_id)
        )
        assert cursor.rowcount == 1
        assert fixture._db_result_raw(store, operation_id) == corrupted

        try:
            page = ops.solver_log_read(store, project, {"job_id": job_id, "limit": fixture.EVENT_COUNT})
        except ExecutionContractError as exc:
            assert exc.code == "EXECUTION_STATE_UNKNOWN"
            assert marker not in str(exc) and len(str(exc)) <= 4096
        else:
            chain = page["exception_chain"]
            assert chain.get("status") == "UNKNOWN"
            assert chain.get("items") in (None, [])
            source = chain.get("source")
            assert not (isinstance(source, str) and source.startswith(("event ", "worker request")))
            assert marker not in json.dumps(chain)
            assert page["job_status"] == "FAILED"

        assert fixture._db_job_status(store, job_id) == "FAILED"
        assert fixture._db_operation_status(store, operation_id) == "FAILED"
        assert fixture._db_result_raw(store, operation_id) == corrupted
        assert fixture._rows_signature(fixture._stored_rows(store, job_id)) == rows_before
    finally:
        store.close()
