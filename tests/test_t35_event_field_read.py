from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from comsol_mcp import _tools_control
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._g2_registry import BY_TOOL, CONTROL_IMPLEMENTED_OPERATIONS, validate_call
from comsol_mcp._operation_store import (
    JOB_EVENT_FIELD_READ_MAX_BASE64_BYTES,
    JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES,
)


TASK_ROOT = Path(__file__).resolve().parents[1]


def _create_project(daemon: ControlDaemon, label: str) -> dict:
    request_id = "t35-event-project-" + uuid4().hex
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": label,
            "workspace": label,
            "policy": {"permissions": ["inspect", "project_write"]},
        },
        "execution": {"request_id": request_id, "idempotency_key": request_id},
    })
    assert result["success"] is True, result
    return result["data"]["project"]


def _call(daemon: ControlDaemon, project_id: str, operation: str, arguments: dict) -> dict:
    return daemon.dispatch({
        "operation": operation,
        "arguments": arguments,
        "execution": {"project_id": project_id},
    })


def test_public_project_scoped_event_field_chunk_read(monkeypatch):
    run_root = TASK_ROOT / "test-data" / ("run-" + uuid4().hex)
    test_db_dir = run_root / "control"
    project_root = run_root / "projects"
    test_db_dir.mkdir(parents=True)
    project_root.mkdir()
    daemon = ControlDaemon(test_db_dir, project_root=project_root, registry={})
    try:
        project = _create_project(daemon, "event-owner")
        other_project = _create_project(daemon, "other-project")
        project_id = project["project_id"]
        job_id, operation_id = "t35-event-job-" + uuid4().hex, "t35-event-op-" + uuid4().hex
        metadata = json.dumps({"project_id": project_id}, separators=(",", ":"))
        result_bytes = json.dumps(
            {"success": True, "data": {"ok": True, "source": "synthetic"}},
            separators=(",", ":"),
        )

        with daemon.store.lock:
            db = daemon.store.db
            db.execute(
                "INSERT INTO operations(operation_id,request_id,idempotency_key,request_hash,operation,status,result,metadata,effective_timeouts) "
                "VALUES(?,?,?,?,?,'SUCCEEDED',?,?,?)",
                (operation_id, operation_id, operation_id, "a" * 64, "synthetic.history_fixture", result_bytes, metadata, "{}"),
            )
            db.execute(
                "INSERT INTO jobs(job_id,operation_id,status,metadata,effective_timeouts) "
                "VALUES(?,?,'SUCCEEDED',?,?)",
                (job_id, operation_id, metadata, "{}"),
            )
            normal_id = db.execute(
                "INSERT INTO job_events(job_id,event,metadata,created_at) VALUES(?,?,?,?)",
                (job_id, "NORMAL", '{"message":"ordinary row"}', "2026-11-24T00:00:00Z"),
            ).lastrowid
            large_id = db.execute(
                "INSERT INTO job_events(job_id,event,metadata,created_at) VALUES(?,?,?,?)",
                (job_id, "FAILED", "historical placeholder", "2026-11-24T00:00:01Z"),
            ).lastrowid
            null_id = db.execute(
                "INSERT INTO job_events(job_id,event,metadata,created_at) VALUES(?,?,?,?)",
                (job_id, "NULL_FIELD", None, "2026-11-24T00:00:02Z"),
            ).lastrowid
            empty_id = db.execute(
                "INSERT INTO job_events(job_id,event,metadata,created_at) VALUES(?,?,?,?)",
                (job_id, "EMPTY_FIELD", "", "2026-11-24T00:00:03Z"),
            ).lastrowid
            blob_id = db.execute(
                "INSERT INTO job_events(job_id,event,metadata,created_at) VALUES(?,?,?,?)",
                (job_id, "BLOB_FIELD", b"\x00\xfflegacy-blob", "2026-11-24T00:00:04Z"),
            ).lastrowid

            # Simulate a legacy row written before the current insert bound.
            # The row and database are created exclusively for this test.
            raw = (b"A" * (JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES - 2)) + "界".encode("utf-8") + b"\x00\xf0\x9f\xa7\xaa"
            raw += b"z" * (4 * 1024 * 1024 + 257 - len(raw))
            assert len(raw) > 4 * 1024 * 1024
            raw_text = raw.decode("utf-8", "strict")
            db.execute("UPDATE job_events SET metadata=? WHERE id=?", (raw_text, large_id))

        # The generated historical field exceeds the v3 writer bound but is
        # still available in full through the new original-byte reader.
        public_mcp = FastMCP("t35-event-field-read-test")
        _tools_control.register(public_mcp)
        assert "job_log_event_read" in public_mcp._tool_manager._tools
        public_schema = public_mcp._tool_manager._tools["job_log_event_read"].parameters
        assert public_schema["properties"]["field"]["enum"] == ["event", "metadata", "created_at"]
        assert BY_TOOL["job_log_event_read"].operation_id == "job.log.event.read"
        assert "job.log.event.read" in CONTROL_IMPLEMENTED_OPERATIONS
        validate_call("job.log.event.read", {
            "job_id": job_id, "event_id": large_id, "field": "metadata",
            "limit": JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES,
        })

        # Public ordinary job.log still returns ordinary rows and marks the
        # over-limit row as incomplete; its event tag is not trusted status.
        log = _call(daemon, project_id, "job.log", {"job_id": job_id, "limit": 20})
        assert log["success"] is True, log
        log_events = log["data"]["events"]
        assert any(item.get("id") == normal_id and item.get("event") == "NORMAL" for item in log_events)
        gap = next(item for item in log_events if item.get("id") == large_id)
        assert gap["record_type"] == "event_gap" and gap["complete"] is False
        assert gap.get("terminal_event") == "FAILED"

        # Also check the stable inline job.result contract on this synthetic
        # SUCCEEDED job; event reading never changes the stored result.
        result = _call(daemon, project_id, "job.result", {"job_id": job_id})
        assert result["success"] is True, result
        assert result["data"]["result"] == {"success": True, "data": {"ok": True, "source": "synthetic"}}

        original_sha = hashlib.sha256(raw).hexdigest()
        offset = 0
        collected = bytearray()
        pages = 0
        while True:
            args = {
                "job_id": job_id, "event_id": large_id, "field": "metadata",
                "offset": offset, "limit": JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES,
            }
            if offset:
                args["field_sha256"] = original_sha
            page = _call(daemon, project_id, "job.log.event.read", args)
            assert page["success"] is True, page
            data = page["data"]
            assert len(data["chunk_base64"].encode("ascii")) <= JOB_EVENT_FIELD_READ_MAX_BASE64_BYTES
            chunk = base64.b64decode(data["chunk_base64"], validate=True)
            assert len(chunk) <= JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES
            assert data["offset"] == offset
            assert data["sha256"] == original_sha
            assert data["byte_length"] == len(raw)
            assert data["storage_type"] == "text" and data["is_sql_null"] is False
            assert data["database_encoding"] == "UTF-8"
            assert data["job_status"] == data["operation_status"] == "SUCCEEDED"
            assert data["trusted_terminal_status"] == "SUCCEEDED"
            collected.extend(chunk)
            offset = data["next_offset"]
            pages += 1
            if data["complete"]:
                break
        assert pages > 1
        assert collected == raw
        assert collected[:JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES][-2:] == "界".encode("utf-8")[:2]
        assert collected[JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES:JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES + 1] == "界".encode("utf-8")[2:]
        assert b"\x00" in collected
        assert hashlib.sha256(collected).hexdigest() == original_sha

        # The registered public tool entry maps to the same daemon route.
        monkeypatch.setattr(
            _tools_control,
            "dispatch",
            lambda operation, arguments, execution: daemon.dispatch({
                "operation": operation,
                "arguments": arguments,
                "execution": {"project_id": project_id},
            }),
        )
        tool_page = _tools_control.job_log_event_read(
            job_id, normal_id, "metadata", limit=32,
        )
        assert tool_page["success"] is True, tool_page
        assert base64.b64decode(tool_page["data"]["chunk_base64"]) == b'{"message":"ordinary row"}'

        for field, expected in {
            "event": b"NORMAL",
            "created_at": b"2026-11-24T00:00:00Z",
        }.items():
            field_page = _call(daemon, project_id, "job.log.event.read", {
                "job_id": job_id, "event_id": normal_id, "field": field,
            })
            assert field_page["success"] is True, field_page
            assert base64.b64decode(field_page["data"]["chunk_base64"]) == expected
        blob_page = _call(daemon, project_id, "job.log.event.read", {
            "job_id": job_id, "event_id": blob_id, "field": "metadata",
        })
        assert blob_page["success"] is True, blob_page
        assert blob_page["data"]["storage_type"] == "blob"
        assert base64.b64decode(blob_page["data"]["chunk_base64"]) == b"\x00\xfflegacy-blob"

        null_result = _call(daemon, project_id, "job.log.event.read", {
            "job_id": job_id, "event_id": null_id, "field": "metadata",
        })
        assert null_result["success"] is True
        assert null_result["data"]["storage_type"] == "null"
        assert null_result["data"]["is_sql_null"] is True
        assert null_result["data"]["byte_length"] == 0
        assert null_result["data"]["chunk_base64"] == ""

        empty_result = _call(daemon, project_id, "job.log.event.read", {
            "job_id": job_id, "event_id": empty_id, "field": "metadata",
        })
        assert empty_result["success"] is True
        assert empty_result["data"]["storage_type"] == "text"
        assert empty_result["data"]["is_sql_null"] is False
        assert empty_result["data"]["byte_length"] == 0
        assert empty_result["data"]["sha256"] == hashlib.sha256(b"").hexdigest()

        real_connection = daemon.store.db

        class WithoutBlobOpen:
            def __getattr__(self, name):
                if name == "blobopen":
                    raise AttributeError(name)
                return getattr(real_connection, name)

        daemon.store.db = WithoutBlobOpen()
        try:
            no_blobopen = _call(daemon, project_id, "job.log.event.read", {
                "job_id": job_id, "event_id": large_id, "field": "metadata",
            })
        finally:
            daemon.store.db = real_connection
        assert no_blobopen["success"] is True
        assert no_blobopen["data"]["sha256"] == original_sha
        assert no_blobopen["data"]["byte_length"] == len(raw)
        assert base64.b64decode(no_blobopen["data"]["chunk_base64"], validate=True) == raw[:JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES]

        class WithNonUtf8Encoding:
            def __getattr__(self, name):
                return getattr(real_connection, name)

            def execute(self, sql, *args):
                if sql == "PRAGMA encoding":
                    class Result:
                        @staticmethod
                        def fetchone():
                            return ("UTF-16le",)
                    return Result()
                return real_connection.execute(sql, *args)

        daemon.store.db = WithNonUtf8Encoding()
        try:
            unsupported_encoding = _call(daemon, project_id, "job.log.event.read", {
                "job_id": job_id, "event_id": large_id, "field": "metadata",
            })
        finally:
            daemon.store.db = real_connection
        assert unsupported_encoding["success"] is False
        assert unsupported_encoding["error"]["code"] == "EVENT_ENCODING_UNSUPPORTED"
        assert unsupported_encoding["data"] == {}

        missing = _call(daemon, project_id, "job.log.event.read", {
            "job_id": job_id, "event_id": int(empty_id) + 100000, "field": "metadata",
        })
        assert missing["success"] is False and missing["error"]["code"] == "EVENT_NOT_FOUND"

        invalid_field = _call(daemon, project_id, "job_log_event_read", {
            "job_id": job_id, "event_id": large_id, "field": "private_column",
        })
        assert invalid_field["success"] is False and invalid_field["error"]["code"] == "INVALID_REQUEST"
        missing_digest = _call(daemon, project_id, "job.log.event.read", {
            "job_id": job_id, "event_id": large_id, "field": "metadata", "offset": 1,
        })
        assert missing_digest["success"] is False and missing_digest["error"]["code"] == "INVALID_REQUEST"

        # An authorized but different project receives the same opaque
        # not-found response as an unknown job, before event lookup.
        foreign = _call(daemon, other_project["project_id"], "job.log.event.read", {
            "job_id": job_id, "event_id": large_id, "field": "metadata",
        })
        assert foreign["success"] is False
        assert foreign["error"]["code"] == "NODE_NOT_FOUND"
        assert foreign["data"] == {}
        assert "event" not in foreign["error"]["message"].lower()

        # A digest from page one fences independent continuation snapshots.
        with daemon.store.lock:
            daemon.store.db.execute(
                "UPDATE job_events SET metadata=? WHERE id=?", ("changed after first page", large_id),
            )
        changed = _call(daemon, project_id, "job.log.event.read", {
            "job_id": job_id, "event_id": large_id, "field": "metadata",
            "offset": JOB_EVENT_FIELD_READ_MAX_PAGE_BYTES, "limit": 32, "field_sha256": original_sha,
        })
        assert changed["success"] is False
        assert changed["error"]["code"] == "EVENT_FIELD_CHANGED"
        assert changed["data"] == {}

        print(f"synthetic task-owned SQLite: {daemon.store.path}; job status=SUCCEEDED; no Worker dispatched")
    finally:
        daemon.close()
