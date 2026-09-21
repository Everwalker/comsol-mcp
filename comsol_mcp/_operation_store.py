"""Durable, serialized operation/job ledger."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST")
METADATA_TABLES = {
    "sessions": "session_id",
    "runtimes": "runtime_id",
    "revisions": "model_key",
    "artifacts": "artifact_id",
    "checkpoints": "checkpoint_id",
}


class IdempotencyConflict(RuntimeError):
    pass


def _dumps_canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        def _sanitize(item: Any) -> Any:
            if isinstance(item, dict):
                return {str(k) if k is not None else "null": _sanitize(v) for k, v in item.items()}
            if isinstance(item, (list, tuple)):
                return [_sanitize(x) for x in item]
            return item
        return json.dumps(_sanitize(value), sort_keys=True)


class OperationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self._migrate()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def _migrate(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL)")
            row = self.db.execute("SELECT version FROM schema_meta").fetchone()
            if not row:
                self.db.execute("INSERT INTO schema_meta VALUES(1)")
            elif row[0] == 0:
                self.db.execute("UPDATE schema_meta SET version=1")
            elif row[0] != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported operation database schema {row[0]}")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS operations("
                "operation_id TEXT PRIMARY KEY,request_id TEXT,idempotency_key TEXT UNIQUE,"
                "request_hash TEXT,operation TEXT,status TEXT,result TEXT,metadata TEXT,"
                "created_at TEXT DEFAULT CURRENT_TIMESTAMP,started_at TEXT,finished_at TEXT,"
                "effective_timeouts TEXT)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS jobs("
                "job_id TEXT PRIMARY KEY,operation_id TEXT UNIQUE,status TEXT,metadata TEXT,"
                "created_at TEXT DEFAULT CURRENT_TIMESTAMP,started_at TEXT,finished_at TEXT,"
                "effective_timeouts TEXT)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS job_events("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,event TEXT,metadata TEXT,"
                "created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            for table, key_column in METADATA_TABLES.items():
                revision = ",revision INTEGER NOT NULL" if table == "revisions" else ""
                self.db.execute(
                    f"CREATE TABLE IF NOT EXISTS {table}("
                    f"{key_column} TEXT PRIMARY KEY,metadata TEXT NOT NULL{revision})"
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def begin(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        request_hash: str,
        operation: str,
        metadata: dict[str, Any] | None = None,
        timeouts: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                existing = self.db.execute(
                    "SELECT * FROM operations WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflict("idempotency key was reused with a different request body")
                    record = self._op(existing)
                    job = self.db.execute(
                        "SELECT job_id FROM jobs WHERE operation_id=?", (record["operation_id"],)
                    ).fetchone()
                    record["job_id"] = job[0] if job else None
                    self.db.execute("COMMIT")
                    return record, True

                operation_id, job_id = str(uuid4()), str(uuid4())
                serialized_metadata = json.dumps(metadata or {}, sort_keys=True)
                serialized_timeouts = json.dumps(timeouts or {}, sort_keys=True)
                self.db.execute(
                    "INSERT INTO operations("
                    "operation_id,request_id,idempotency_key,request_hash,operation,status,metadata,effective_timeouts"
                    ") VALUES(?,?,?,?,?,'QUEUED',?,?)",
                    (operation_id, request_id, idempotency_key, request_hash, operation, serialized_metadata, serialized_timeouts),
                )
                self.db.execute(
                    "INSERT INTO jobs(job_id,operation_id,status,metadata,effective_timeouts) "
                    "VALUES(?,?,'QUEUED',?,?)",
                    (job_id, operation_id, serialized_metadata, serialized_timeouts),
                )
                record = self._op(
                    self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
                )
                record["job_id"] = job_id
                self.db.execute("COMMIT")
                return record, False
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def start_job(self, operation_id: str, metadata: dict[str, Any] | None = None) -> str:
        """Compatibility path for older databases that predate job creation in begin()."""
        with self.lock:
            row = self.db.execute("SELECT job_id FROM jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if row:
                return row[0]
            job_id = str(uuid4())
            self.db.execute(
                "INSERT INTO jobs(job_id,operation_id,status,metadata) VALUES(?,?,'QUEUED',?)",
                (job_id, operation_id, json.dumps(metadata or {}, sort_keys=True)),
            )
            return job_id

    def finish(self, operation_id: str, *, status: str, result: dict[str, Any]) -> None:
        """Atomically record a result observation and synchronize its job status.

        ``UNKNOWN`` and ``RECONCILING`` are durable nonterminal observations:
        they retain the result envelope (including ``safe_retry=False``) but
        deliberately leave ``finished_at`` unset so restart reconciliation can
        continue from the original request rather than replaying it.
        """
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                operation = self.db.execute(
                    "UPDATE operations SET status=?,result=?,finished_at="
                    "CASE WHEN ? IN ('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST') "
                    "THEN COALESCE(finished_at,CURRENT_TIMESTAMP) ELSE finished_at END "
                    "WHERE operation_id=?",
                    (status, _dumps_canonical(result), status, operation_id),
                )
                job = self.db.execute(
                    "UPDATE jobs SET status=?,finished_at="
                    "CASE WHEN ? IN ('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST') "
                    "THEN COALESCE(finished_at,CURRENT_TIMESTAMP) ELSE finished_at END "
                    "WHERE operation_id=?",
                    (status, status, operation_id),
                )
                if operation.rowcount != 1 or job.rowcount != 1:
                    raise KeyError(f"operation/job pair missing for {operation_id}")
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            return self._op(row) if row else None

    def operation_job(self, operation_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE operation_id=?", (operation_id,)).fetchone()
            return self._job(row) if row else None

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return self._job(row) if row else None

    def update_job(self, job_id: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT metadata FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row:
                    merged = {**json.loads(row[0] or "{}"), **(metadata or {})}
                    self.db.execute(
                        "UPDATE jobs SET status=?,metadata=?,"
                        "started_at=CASE WHEN ? IN ('STARTING','RUNNING') THEN COALESCE(started_at,CURRENT_TIMESTAMP) ELSE started_at END,"
                        "finished_at=CASE WHEN ? IN ('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST') "
                        "THEN COALESCE(finished_at,CURRENT_TIMESTAMP) ELSE finished_at END WHERE job_id=?",
                        (status, _dumps_canonical(merged), status, status, job_id),
                    )
                    self.db.execute(
                        "UPDATE operations SET status=?,"
                        "started_at=(SELECT started_at FROM jobs WHERE job_id=?),"
                        "finished_at=(SELECT finished_at FROM jobs WHERE job_id=?) "
                        "WHERE operation_id=(SELECT operation_id FROM jobs WHERE job_id=?)",
                        (status, job_id, job_id, job_id),
                    )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def add_event(self, job_id: str, event: str, metadata: dict[str, Any] | None = None) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                (job_id, event, json.dumps(metadata or {}, sort_keys=True)),
            )

    def events(self, job_id: str, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {**dict(row), "metadata": json.loads(row["metadata"] or "{}")}
                for row in self.db.execute(
                    "SELECT * FROM job_events WHERE job_id=? ORDER BY id LIMIT ? OFFSET ?",
                    (job_id, max(0, min(int(limit), 1000)), max(0, int(offset))),
                )
            ]

    def unresolved_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                self._job(row)
                for row in self.db.execute(
                    "SELECT * FROM jobs WHERE status NOT IN ('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST')"
                )
            ]

    def reconcile_after_restart(self) -> list[str]:
        with self.lock:
            rows = self.db.execute(
                "SELECT job_id FROM jobs WHERE status NOT IN "
                "('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST','RECONCILING')"
            ).fetchall()
            job_ids = [row[0] for row in rows]
            for job_id in job_ids:
                self.update_job(job_id, "RECONCILING")
            return job_ids

    @staticmethod
    def _metadata_column(table: str) -> str:
        try:
            return METADATA_TABLES[table]
        except KeyError as exc:
            raise ValueError("unsupported metadata table") from exc

    def put_metadata(self, table: str, key: str, metadata: dict[str, Any]) -> None:
        column = self._metadata_column(table)
        with self.lock:
            if table == "revisions":
                self.db.execute(
                    "INSERT INTO revisions(model_key,metadata,revision) VALUES(?,?,?) "
                    "ON CONFLICT(model_key) DO UPDATE SET metadata=excluded.metadata,revision=excluded.revision",
                    (key, json.dumps(metadata, sort_keys=True), int(metadata["revision"])),
                )
            else:
                self.db.execute(
                    f"INSERT INTO {table}({column},metadata) VALUES(?,?) "
                    f"ON CONFLICT({column}) DO UPDATE SET metadata=excluded.metadata",
                    (key, json.dumps(metadata, sort_keys=True)),
                )

    def save_metadata(self, *args: Any, **kwargs: Any) -> None:
        self.put_metadata(*args, **kwargs)

    def get_metadata(self, table: str, key: str) -> dict[str, Any] | None:
        column = self._metadata_column(table)
        with self.lock:
            row = self.db.execute(f"SELECT metadata FROM {table} WHERE {column}=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def list_metadata(self, table: str) -> list[dict[str, Any]]:
        self._metadata_column(table)  # Table interpolation is safe only after allowlisting.
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(f"SELECT metadata FROM {table}")]

    def persist_artifact(self, key: str, metadata: dict[str, Any]) -> None:
        if not metadata.get("sha256"):
            raise ValueError("artifact sha256 required")
        self.put_metadata("artifacts", key, metadata)

    def persist_checkpoint(self, key: str, metadata: dict[str, Any]) -> None:
        if not metadata.get("sha256"):
            raise ValueError("checkpoint sha256 required")
        self.put_metadata("checkpoints", key, metadata)

    @staticmethod
    def _op(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["result"] = json.loads(value["result"]) if value.get("result") else None
        value["metadata"] = json.loads(value.get("metadata") or "{}")
        value["effective_timeouts"] = json.loads(value.get("effective_timeouts") or "{}")
        return value

    def _job(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = json.loads(value["metadata"] or "{}")
        value["effective_timeouts"] = json.loads(value.get("effective_timeouts") or "{}")
        value["operation"] = self.get_operation(value["operation_id"])
        value["result"] = value["operation"]["result"] if value["operation"] else None
        return value
