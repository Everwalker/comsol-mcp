import base64
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore


PROJECT_ID = "t35-luna-private-project"
JOB_ID = "t35-luna-succeeded-job"
OPERATION_ID = "t35-luna-succeeded-operation"


class _WithoutBlobopen:
    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        if name == "blobopen":
            raise AttributeError(name)
        return getattr(self._connection, name)


def _make_store(path, *, encoding=None):
    if encoding is not None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(f"PRAGMA encoding='{encoding}'")
            connection.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
            connection.execute("INSERT INTO schema_meta VALUES(1)")
            connection.commit()
        finally:
            connection.close()
    store = OperationStore(path)
    metadata = json.dumps({"project_id": PROJECT_ID})
    store.db.execute(
        "INSERT INTO operations(operation_id,operation,status,metadata) VALUES(?,?,?,?)",
        (OPERATION_ID, "fixture", "SUCCEEDED", metadata),
    )
    store.db.execute(
        "INSERT INTO jobs(job_id,operation_id,status,metadata) VALUES(?,?,?,?)",
        (JOB_ID, OPERATION_ID, "SUCCEEDED", metadata),
    )
    store.db = _WithoutBlobopen(store.db)
    return store


def _call(store, job_id, event_id, field, *, offset=0, limit=49152, field_sha256=None,
          project_id=PROJECT_ID):
    arguments = {
        "job_id": job_id,
        "event_id": event_id,
        "field": field,
        "offset": offset,
        "limit": limit,
    }
    if field_sha256 is not None:
        arguments["field_sha256"] = field_sha256
    daemon = object.__new__(ControlDaemon)
    daemon.store = store
    return daemon._control_read("job_log_event_read", arguments, scoped_project_id=project_id)


def _insert_event(store, event, metadata, created_at):
    cursor = store.db.execute(
        "INSERT INTO job_events(job_id,event,metadata,created_at) VALUES(?,?,?,?)",
        (JOB_ID, event, metadata, created_at),
    )
    return cursor.lastrowid


def _decode(response):
    assert response["success"] is True
    return response["data"], base64.b64decode(response["data"]["chunk_base64"], validate=True)


class T35Python310FallbackTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="t35-luna-", dir=Path(__file__).resolve().parents[1],
        )
        self.store = _make_store(Path(self._temporary_directory.name) / "t35-succeeded.sqlite")

    def tearDown(self):
        self.store.close()
        self._temporary_directory.cleanup()

    def test_missing_blobopen_reads_raw_pages_and_preserves_storage_classes(self):
        store = self.store
        self.assertFalse(hasattr(store.db, "blobopen"))
        text_value = "A" * 49151 + "€" + "\0" + "tail"
        expected = text_value.encode("utf-8")
        event_id = _insert_event(store, text_value, sqlite3.Binary(b"\x00\xff\x80"), None)

        first_data, first = _decode(_call(store, JOB_ID, event_id, "event"))
        second_data, second = _decode(_call(
            store, JOB_ID, event_id, "event", offset=first_data["next_offset"],
            field_sha256=first_data["sha256"],
        ))
        self.assertEqual(first[-1:], b"\xe2")  # A UTF-8 code point may straddle byte pages.
        self.assertTrue(second.startswith(b"\x82\xac\0"))
        self.assertEqual(first + second, expected)
        self.assertEqual(first_data["sha256"], second_data["sha256"])
        self.assertEqual(first_data["sha256"], hashlib.sha256(expected).hexdigest())
        self.assertTrue(second_data["complete"])
        self.assertEqual(second_data["trusted_terminal_status"], "SUCCEEDED")

        blob_data, blob = _decode(_call(store, JOB_ID, event_id, "metadata"))
        self.assertEqual(blob_data["storage_type"], "blob")
        self.assertEqual(blob, b"\x00\xff\x80")

        null_data, null = _decode(_call(store, JOB_ID, event_id, "created_at"))
        self.assertEqual(null_data["storage_type"], "null")
        self.assertTrue(null_data["is_sql_null"])
        self.assertEqual(null, b"")

        empty_id = _insert_event(store, "", sqlite3.Binary(b""), "")
        empty_text_data, empty_text = _decode(_call(store, JOB_ID, empty_id, "event"))
        empty_blob_data, empty_blob = _decode(_call(store, JOB_ID, empty_id, "metadata"))
        self.assertEqual((empty_text_data["storage_type"], empty_text_data["byte_length"], empty_text), ("text", 0, b""))
        self.assertEqual((empty_blob_data["storage_type"], empty_blob_data["byte_length"], empty_blob), ("blob", 0, b""))
        empty_digest = hashlib.sha256(b"").hexdigest()
        self.assertEqual(empty_text_data["sha256"], empty_digest)
        self.assertEqual(empty_blob_data["sha256"], empty_digest)

        statuses = store.db.execute(
            "SELECT j.status,o.status FROM jobs j JOIN operations o USING(operation_id) WHERE j.job_id=?",
            (JOB_ID,),
        ).fetchone()
        self.assertEqual(tuple(statuses), ("SUCCEEDED", "SUCCEEDED"))


    def test_project_job_binding_digest_change_and_non_utf8_text(self):
        store = self.store
        event_id = _insert_event(store, "x" * 50000, "{}", "now")
        traces = []
        store.db.set_trace_callback(traces.append)

        wrong_project = _call(store, JOB_ID, event_id, "event", project_id="another-project")
        missing_job = _call(store, "missing-job", event_id, "event")
        self.assertEqual(wrong_project["error"]["code"], "NODE_NOT_FOUND")
        self.assertEqual(missing_job["error"]["code"], "NODE_NOT_FOUND")
        self.assertEqual(wrong_project["data"], {})
        self.assertEqual(missing_job["data"], {})
        self.assertFalse(any("FROM job_events" in sql for sql in traces))

        first_data, _ = _decode(_call(store, JOB_ID, event_id, "event"))
        updater = sqlite3.connect(store.path)
        try:
            updater.execute("UPDATE job_events SET event=? WHERE id=?", ("y" * 50000, event_id))
            updater.commit()
        finally:
            updater.close()
        changed = _call(
            store, JOB_ID, event_id, "event", offset=49152,
            field_sha256=first_data["sha256"],
        )
        self.assertFalse(changed["success"])
        self.assertEqual(changed["error"]["code"], "EVENT_FIELD_CHANGED")
        self.assertEqual(changed["data"], {})

        utf16_store = _make_store(
            Path(self._temporary_directory.name) / "t35-succeeded-utf16.sqlite",
            encoding="UTF-16le",
        )
        try:
            utf16_event = _insert_event(utf16_store, "non-ascii café", sqlite3.Binary(b"\xff\0"), "now")
            text_rejected = _call(utf16_store, JOB_ID, utf16_event, "event")
            self.assertEqual(text_rejected["error"]["code"], "EVENT_ENCODING_UNSUPPORTED")
            blob_data, blob = _decode(_call(utf16_store, JOB_ID, utf16_event, "metadata"))
            self.assertEqual(blob_data["storage_type"], "blob")
            self.assertEqual(blob, b"\xff\0")
        finally:
            utf16_store.close()


if __name__ == "__main__":
    unittest.main()
