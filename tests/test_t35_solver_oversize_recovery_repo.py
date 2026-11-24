from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path



try:
    import mcp.server.fastmcp  # noqa: F401
except ModuleNotFoundError:
    # ControlDaemon imports legacy tool declarations while building its
    # registry. This inert stub only satisfies that import; no MCP server,
    # transport, request handling, or tool registration is started.
    class _SchemaOnlyFastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def add_tool(self, tool):
            return tool

    mcp_module = types.ModuleType("mcp")
    mcp_module.__path__ = []
    server_module = types.ModuleType("mcp.server")
    server_module.__path__ = []
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = _SchemaOnlyFastMCP
    mcp_module.server = server_module
    server_module.fastmcp = fastmcp_module
    sys.modules.update({
        "mcp": mcp_module,
        "mcp.server": server_module,
        "mcp.server.fastmcp": fastmcp_module,
    })

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import JOB_EVENT_MAX_ROW_BYTES


class SolverOversizeRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="t35-oversize-recovery-")
        root = Path(self.temporary.name)
        self.project_root = root / "projects"
        self.project_root.mkdir()
        self.daemon = ControlDaemon(root / "control", project_root=self.project_root)
        self.project_a = self._create_project("recovery-a", "recovery-a")
        self.project_b = self._create_project("recovery-b", "recovery-b")
        self.job_a, self.event_id, self.metadata_text, self.event_text, self.created_at = (
            self._create_oversized_success_job(self.project_a)
        )
        self.job_b, self.event_id_b = self._create_small_success_job(self.project_b)

    def tearDown(self):
        try:
            self.daemon.close()
        finally:
            self.temporary.cleanup()

    def _create_project(self, label: str, workspace: str) -> str:
        request_id = f"synthetic-project-request-{label}"
        response = self.daemon.dispatch({
            "operation": "operation_call",
            "arguments": {
                "operation_id": "project.create",
                "arguments": {
                    "label": label,
                    "workspace": workspace,
                    "policy": {"permissions": ["inspect"]},
                },
            },
            "execution": {
                "request_id": request_id,
                "idempotency_key": f"synthetic-project-key-{label}",
            },
        })
        self.assertTrue(response.get("success"), response)
        project_id = response["data"]["project"]["project_id"]
        self.daemon.project_authority.authorize_operation(project_id, "inspect")
        return project_id

    def _new_success_job(self, project_id: str, label: str) -> str:
        request_id = f"synthetic-job-request-{label}"
        record, reused = self.daemon.store.begin(
            request_id=request_id,
            idempotency_key=f"synthetic-job-key-{label}",
            request_hash=hashlib.sha256(request_id.encode()).hexdigest(),
            operation="synthetic.event_recovery_fixture",
            metadata={"execution": {"project_id": project_id}},
        )
        self.assertFalse(reused)
        accepted, status = self.daemon.store.finish(
            record["operation_id"], status="SUCCEEDED",
            result={"success": True, "synthetic": True},
        )
        self.assertTrue(accepted)
        self.assertEqual(status, "SUCCEEDED")
        return record["job_id"]

    def _create_oversized_success_job(self, project_id: str):
        job_id = self._new_success_job(project_id, "oversized")
        self.daemon.store.add_event(job_id, "SYNTHETIC_PRELUDE", {"synthetic": True})

        prefix = '{"pad":"'
        boundary_prefix = "x" * (49_152 - len(prefix.encode("utf-8")) - 1)
        split_marker = "汉🙂"
        suffix = '"}'
        target_metadata_bytes = JOB_EVENT_MAX_ROW_BYTES + 1
        filler_length = (
            target_metadata_bytes
            - len(prefix.encode("utf-8"))
            - len(boundary_prefix.encode("utf-8"))
            - len(split_marker.encode("utf-8"))
            - len(suffix.encode("utf-8"))
        )
        metadata_text = prefix + boundary_prefix + split_marker + ("x" * filler_length) + suffix
        self.assertEqual(len(metadata_text.encode("utf-8")), target_metadata_bytes)
        json.loads(metadata_text)
        event_text = "SYNTHETIC_SOLVER_EVENT\u0000温度"
        created_at = "2026-11-24T08:00:00Z\u0000尾"

        # This new SQLite file models one historical row that predates the
        # current writer trigger. The trigger is removed only in this fixture DB.
        with self.daemon.store.lock:
            self.daemon.store.db.execute("DROP TRIGGER IF EXISTS job_events_job_log_bound_v1")
            cursor = self.daemon.store.db.execute(
                "INSERT INTO job_events(job_id,event,metadata,created_at) VALUES(?,?,?,?)",
                (job_id, event_text, metadata_text, created_at),
            )
            event_id = int(cursor.lastrowid)
        return job_id, event_id, metadata_text, event_text, created_at

    def _create_small_success_job(self, project_id: str):
        job_id = self._new_success_job(project_id, "foreign")
        self.daemon.store.add_event(job_id, "FOREIGN_PROJECT_ONLY", {"private": "synthetic"})
        with self.daemon.store.lock:
            row = self.daemon.store.db.execute(
                "SELECT MAX(id) FROM job_events WHERE job_id=?", (job_id,),
            ).fetchone()
        return job_id, int(row[0])

    def _call(self, operation: str, arguments: dict, project_id: str) -> dict:
        return self.daemon.dispatch({
            "operation": "operation_call",
            "arguments": {
                "operation_id": operation,
                "arguments": arguments,
            },
            "execution": {"project_id": project_id},
        })

    def _read_page(
        self, job_id: str, event_id: int, field: str, offset: int,
        limit: int, field_sha256: str | None = None,
    ) -> dict:
        args = {
            "job_id": job_id,
            "event_id": event_id,
            "field": field,
            "offset": offset,
            "limit": limit,
        }
        if field_sha256 is not None:
            args["field_sha256"] = field_sha256
        return self._call("job.log.event.read", args, self.project_a)

    def _collect_field(self, field: str) -> tuple[bytes, dict, int]:
        first_response = self._read_page(
            self.job_a, self.event_id, field, 0, 49_152,
        )
        if not first_response.get("success"):
            return b"", first_response, 0

        first = first_response["data"]
        digest = first["sha256"]
        total_length = first["byte_length"]
        chunks = []
        pages = 0
        data = first
        while True:
            chunks.append(base64.b64decode(data["chunk_base64"], validate=True))
            pages += 1
            if data["complete"]:
                break
            next_offset = data["next_offset"]
            response = self._read_page(
                self.job_a, self.event_id, field, next_offset, 49_152, digest,
            )
            if not response.get("success"):
                return b"".join(chunks), response, pages
            data = response["data"]
            self.assertEqual(data["sha256"], digest)
            self.assertEqual(data["offset"], next_offset)
        joined = b"".join(chunks)
        self.assertEqual(len(joined), total_length)
        self.assertEqual(hashlib.sha256(joined).hexdigest(), digest)
        return joined, first_response, pages

    def _job_statuses(self, job_id: str) -> tuple[str, str]:
        with self.daemon.store.lock:
            row = self.daemon.store.db.execute(
                "SELECT j.status AS job_status,o.status AS operation_status "
                "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
                "WHERE j.job_id=?",
                (job_id,),
            ).fetchone()
        return row["job_status"], row["operation_status"]

    def _unknown_counts(self) -> tuple[int, int]:
        with self.daemon.store.lock:
            row = self.daemon.store.db.execute(
                "SELECT (SELECT COUNT(*) FROM jobs WHERE status='UNKNOWN') AS jobs, "
                "(SELECT COUNT(*) FROM operations WHERE status='UNKNOWN') AS operations",
            ).fetchone()
        return int(row["jobs"]), int(row["operations"])

    def test_authorized_gap_chunk_read_and_fail_closed_boundaries(self):
        print(f"runtime Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}")
        self.assertEqual(self._job_statuses(self.job_a), ("SUCCEEDED", "SUCCEEDED"))
        self.assertEqual(self._job_statuses(self.job_b), ("SUCCEEDED", "SUCCEEDED"))

        # job.log uses a decimal row-offset cursor; the gap consumes one row.
        cursor = None
        gap = None
        for _ in range(10):
            args = {"job_id": self.job_a, "limit": 1}
            if cursor is not None:
                args["cursor"] = cursor
            response = self._call("job.log", args, self.project_a)
            self.assertTrue(response.get("success"), response)
            data = response["data"]
            self.assertEqual(data["next_cursor"], str(data["next_offset"]))
            self.assertEqual(len(data["events"]), 1)
            item = data["events"][0]
            if item.get("record_type") == "event_gap":
                gap = item
                break
            cursor = data["next_cursor"]
        self.assertIsNotNone(gap, "the oversized synthetic row must appear as an event_gap")
        self.assertEqual(gap["id"], self.event_id)
        self.assertFalse(gap["complete"])
        self.assertEqual(gap["reason"], "legacy_row_exceeds_raw_row_bound")
        self.assertGreater(gap["stored_row_bytes"], JOB_EVENT_MAX_ROW_BYTES)

        invalid_cursor = self._call(
            "job.log", {"job_id": self.job_a, "cursor": "01", "limit": 1},
            self.project_a,
        )
        self.assertEqual(invalid_cursor["error"]["code"], "INVALID_REQUEST")

        # The existing solver reader still fails closed for this row. Its
        # read error does not change either durable success status.
        solver_log = self._call(
            "solver.log_read",
            {"project_id": self.project_a, "job_id": self.job_a},
            self.project_a,
        )
        self.assertEqual(solver_log["error"]["code"], "EXECUTION_STATE_UNKNOWN")
        self.assertEqual(self._job_statuses(self.job_a), ("SUCCEEDED", "SUCCEEDED"))
        self.assertEqual(self._unknown_counts(), (0, 0))

        # A caller authorized for A cannot infer B's event through the reader.
        foreign = self._call(
            "job.log.event.read",
            {"job_id": self.job_b, "event_id": self.event_id_b, "field": "event"},
            self.project_a,
        )
        self.assertEqual(foreign["error"]["code"], "NODE_NOT_FOUND")
        self.assertFalse(foreign.get("data", {}).get("chunk_base64"))
        inner_project_override = self._call(
            "job.log.event.read",
            {
                "job_id": self.job_a,
                "event_id": self.event_id,
                "field": "metadata",
                "project_id": self.project_b,
            },
            self.project_a,
        )
        self.assertEqual(inner_project_override["error"]["code"], "INVALID_REQUEST")

        # Recover every raw field through the default Python/SQLite branch.
        metadata_bytes, metadata_response, metadata_pages = self._collect_field("metadata")
        self.assertTrue(metadata_response.get("success"), metadata_response)
        self.assertEqual(metadata_bytes, self.metadata_text.encode("utf-8"))
        first_metadata_chunk = base64.b64decode(
            metadata_response["data"]["chunk_base64"], validate=True,
        )
        self.assertEqual(
            first_metadata_chunk[-1:],
            "汉".encode("utf-8")[:1],
            "the first byte page should end inside a multibyte UTF-8 character",
        )
        with self.assertRaises(UnicodeDecodeError):
            first_metadata_chunk.decode("utf-8")

        first_metadata = metadata_response["data"]
        missing_digest = self._read_page(
            self.job_a, self.event_id, "metadata",
            first_metadata["next_offset"], 49_152,
        )
        self.assertEqual(missing_digest["error"]["code"], "INVALID_REQUEST")

        event_bytes, event_response, event_pages = self._collect_field("event")
        self.assertTrue(event_response.get("success"), event_response)
        self.assertEqual(event_bytes, self.event_text.encode("utf-8"))
        self.assertIn(b"\0", event_bytes)
        self.assertEqual(event_bytes.decode("utf-8"), self.event_text)

        created_bytes, created_response, created_pages = self._collect_field("created_at")
        self.assertTrue(created_response.get("success"), created_response)
        self.assertEqual(created_bytes, self.created_at.encode("utf-8"))
        self.assertIn(b"\0", created_bytes)
        self.assertEqual(created_bytes.decode("utf-8"), self.created_at)

        # Same-byte-length replacement between pages must invalidate the
        # digest-bound continuation without returning continuation bytes.
        before_length = len(self.metadata_text.encode("utf-8"))
        changed = self.metadata_text.replace("x", "y", 1)
        self.assertEqual(len(changed.encode("utf-8")), before_length)
        first_page = self._read_page(
            self.job_a, self.event_id, "metadata", 0, 4_096,
        )
        self.assertTrue(first_page.get("success"), first_page)
        first_data = first_page["data"]
        with self.daemon.store.lock:
            self.daemon.store.db.execute(
                "UPDATE job_events SET metadata=? WHERE id=? AND job_id=?",
                (changed, self.event_id, self.job_a),
            )
            after_length = self.daemon.store.db.execute(
                "SELECT length(CAST(metadata AS BLOB)) FROM job_events WHERE id=?",
                (self.event_id,),
            ).fetchone()[0]
        self.assertEqual(after_length, before_length)
        changed_page = self._read_page(
            self.job_a, self.event_id, "metadata",
            first_data["next_offset"], 4_096, first_data["sha256"],
        )
        self.assertEqual(changed_page["error"]["code"], "EVENT_FIELD_CHANGED")
        self.assertFalse(changed_page.get("data", {}).get("chunk_base64"))
        with self.daemon.store.lock:
            self.daemon.store.db.execute(
                "UPDATE job_events SET metadata=? WHERE id=? AND job_id=?",
                (self.metadata_text, self.event_id, self.job_a),
            )

        print(
            "blobopen TEXT route recovered metadata/event/created_at: "
            f"{metadata_pages}/{event_pages}/{created_pages} pages; "
            "UTF-8 boundary split, NUL preservation, SHA-256 and same-length "
            "change rejection passed"
        )

        self.assertEqual(self._job_statuses(self.job_a), ("SUCCEEDED", "SUCCEEDED"))
        self.assertEqual(self._job_statuses(self.job_b), ("SUCCEEDED", "SUCCEEDED"))
        self.assertEqual(self._unknown_counts(), (0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
