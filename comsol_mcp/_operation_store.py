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


class JobList(list):
    def __init__(self, items: list[dict[str, Any]], total: int, offset: int, limit: int):
        super().__init__(items)
        self.total = total
        self.offset = offset
        self.limit = limit
        self.has_more = (offset + len(items)) < total

    def as_dict(self) -> dict[str, Any]:
        return {
            "jobs": list(self),
            "total": self.total,
            "offset": self.offset,
            "limit": self.limit,
            "has_more": self.has_more,
        }


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
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)")
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

    def finish(self, operation_id: str, *, status: str, result: dict[str, Any]) -> tuple[bool, str]:
        """Atomically record a result observation and synchronize its job status.

        Returns ``(accepted, authoritative_status)`` so the caller can use the
        actual persistent state for RPC responses and event emission.

        ``UNKNOWN`` and ``RECONCILING`` are durable nonterminal observations:
        they retain the result envelope (including ``safe_retry=False``) but
        deliberately leave ``finished_at`` unset so restart reconciliation can
        continue from the original request rather than replaying it.
        """
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                op_row = self.db.execute(
                    "SELECT status, result FROM operations WHERE operation_id=?", (operation_id,)
                ).fetchone()
                job_row = self.db.execute(
                    "SELECT job_id, status FROM jobs WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if not op_row or not job_row:
                    raise KeyError(f"operation/job pair missing for {operation_id}")

                current_status = op_row["status"]
                job_id = job_row["job_id"]

                # D03: Terminal State Immunity:
                # If already in a terminal state, DO NOT overwrite with another terminal state
                # or nonterminal state! Record as a LateResultRecorded event instead.
                if current_status in TERMINAL:
                    self.db.execute(
                        "INSERT INTO job_events(job_id, event, metadata) VALUES(?, 'LateResultRecorded', ?)",
                        (job_id, _dumps_canonical({
                            "prior_status": current_status,
                            "ignored_status": status,
                            "late_result": result,
                        }))
                    )
                    self.db.execute("COMMIT")
                    return False, current_status

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
                return True, status
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

    def update_job(
        self,
        job_id: str,
        status: str,
        metadata: dict[str, Any] | None = None,
        *,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT status, metadata, operation_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row:
                    current_status = row["status"]
                    operation_id = row["operation_id"]
                    # D03: Terminal state cannot be overwritten by any state (even another terminal state)
                    if current_status in TERMINAL:
                        if status != current_status:
                            self.db.execute(
                                "INSERT INTO job_events(job_id, event, metadata) VALUES(?, 'LateTransitionRejected', ?)",
                                (job_id, _dumps_canonical({
                                    "current_status": current_status,
                                    "rejected_status": status,
                                    "metadata": metadata,
                                }))
                            )
                            self.db.execute("COMMIT")
                            return
                        merged = {**json.loads(row["metadata"] or "{}"), **(metadata or {})}
                        self.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?", (_dumps_canonical(merged), job_id))
                        self.db.execute("COMMIT")
                        return

                    merged = {**json.loads(row["metadata"] or "{}"), **(metadata or {})}
                    self.db.execute(
                        "UPDATE jobs SET status=?,metadata=?,"
                        "started_at=CASE WHEN ? IN ('STARTING','RUNNING') THEN COALESCE(started_at,CURRENT_TIMESTAMP) ELSE started_at END,"
                        "finished_at=CASE WHEN ? IN ('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST') "
                        "THEN COALESCE(finished_at,CURRENT_TIMESTAMP) ELSE finished_at END WHERE job_id=?",
                        (status, _dumps_canonical(merged), status, status, job_id),
                    )
                    if result is not None:
                        self.db.execute(
                            "UPDATE operations SET status=?,result=?,"
                            "started_at=(SELECT started_at FROM jobs WHERE job_id=?),"
                            "finished_at=(SELECT finished_at FROM jobs WHERE job_id=?) "
                            "WHERE operation_id=?",
                            (status, _dumps_canonical(result), job_id, job_id, operation_id),
                        )
                    else:
                        self.db.execute(
                            "UPDATE operations SET status=?,"
                            "started_at=(SELECT started_at FROM jobs WHERE job_id=?),"
                            "finished_at=(SELECT finished_at FROM jobs WHERE job_id=?) "
                            "WHERE operation_id=?",
                            (status, job_id, job_id, operation_id),
                        )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def transition_status(
        self,
        job_id: str,
        expected_statuses: tuple[str, ...] | list[str] | set[str] | str,
        new_status: str,
        *,
        metadata: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        event_name: str | None = None,
        event_meta: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Atomically transition job status from expected_statuses to new_status with CAS."""
        if isinstance(expected_statuses, str):
            expected = {expected_statuses}
        else:
            expected = set(expected_statuses)

        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if not row:
                    self.db.execute("ROLLBACK")
                    return False, "NOT_FOUND", None
                current = row["status"]
                operation_id = row["operation_id"]
                if current not in expected:
                    self.db.execute("COMMIT")
                    return False, current, self._job(row)

                # D03: Terminal state cannot be transitioned to another state
                if current in TERMINAL and new_status != current:
                    self.db.execute(
                        "INSERT INTO job_events(job_id, event, metadata) VALUES(?, 'LateTransitionRejected', ?)",
                        (job_id, _dumps_canonical({
                            "current_status": current,
                            "rejected_status": new_status,
                            "metadata": metadata,
                        }))
                    )
                    self.db.execute("COMMIT")
                    return False, current, self._job(row)

                merged_meta = {**json.loads(row["metadata"] or "{}"), **(metadata or {})}
                self.db.execute(
                    "UPDATE jobs SET status=?,metadata=?,"
                    "started_at=CASE WHEN ? IN ('STARTING','RUNNING') THEN COALESCE(started_at,CURRENT_TIMESTAMP) ELSE started_at END,"
                    "finished_at=CASE WHEN ? IN ('SUCCEEDED','FAILED','CANCELLED','EXPIRED','LOST') "
                    "THEN COALESCE(finished_at,CURRENT_TIMESTAMP) ELSE finished_at END WHERE job_id=?",
                    (new_status, _dumps_canonical(merged_meta), new_status, new_status, job_id),
                )
                if result is not None:
                    self.db.execute(
                        "UPDATE operations SET status=?,result=?,"
                        "started_at=(SELECT started_at FROM jobs WHERE job_id=?),"
                        "finished_at=(SELECT finished_at FROM jobs WHERE job_id=?) "
                        "WHERE operation_id=?",
                        (new_status, _dumps_canonical(result), job_id, job_id, operation_id),
                    )
                else:
                    self.db.execute(
                        "UPDATE operations SET status=?,"
                        "started_at=(SELECT started_at FROM jobs WHERE job_id=?),"
                        "finished_at=(SELECT finished_at FROM jobs WHERE job_id=?) "
                        "WHERE operation_id=?",
                        (new_status, job_id, job_id, operation_id),
                    )
                if event_name:
                    self.db.execute(
                        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                        (job_id, event_name, _dumps_canonical(event_meta or {})),
                    )
                self.db.execute("COMMIT")
                updated = self.job(job_id)
                return True, new_status, updated
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def list_jobs(
        self,
        offset: int = 0,
        limit: int = 100,
        status: str | None = None,
        project_id: str | None = None,
    ) -> JobList:
        bound_offset = max(0, int(offset))
        bound_limit = max(1, min(int(limit), 1000))
        with self.lock:
            conditions = ["1=1"]
            params: list[Any] = []
            if status is not None:
                conditions.append("status=?")
                params.append(str(status))
            if project_id is not None:
                conditions.append(
                    "(json_extract(COALESCE(metadata, '{}'), '$.project_id') = ? "
                    "OR json_extract(COALESCE(metadata, '{}'), '$.execution.project_id') = ? "
                    "OR json_extract(COALESCE(metadata, '{}'), '$.project_root') = ?)"
                )
                params.extend([str(project_id), str(project_id), str(project_id)])

            where_clause = " AND ".join(conditions)
            total = self.db.execute(f"SELECT COUNT(*) FROM jobs WHERE {where_clause}", params).fetchone()[0]
            page_params = list(params) + [bound_limit, bound_offset]
            rows = self.db.execute(
                f"SELECT * FROM jobs WHERE {where_clause} ORDER BY rowid DESC LIMIT ? OFFSET ?",
                page_params,
            ).fetchall()
            items = [self._job(row) for row in rows]
            return JobList(items, total=total, offset=bound_offset, limit=bound_limit)

    def cancel_queued(self, job_id: str, reason: str = "cancelled by user") -> tuple[bool, str, dict[str, Any] | None]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if not row:
                    self.db.execute("ROLLBACK")
                    return False, "NOT_FOUND", None
                current_status = row["status"]
                operation_id = row["operation_id"]
                if current_status == "CANCELLED":
                    job_record = self._job(row)
                    self.db.execute("COMMIT")
                    return True, "ALREADY_CANCELLED", job_record
                if current_status != "QUEUED":
                    job_record = self._job(row)
                    self.db.execute("COMMIT")
                    return False, current_status, job_record

                cancel_result = {
                    "success": False,
                    "data": {
                        "status": "CANCELLED",
                        "job_id": job_id,
                        "engine_dispatched": False,
                        "engine_stopped": False,
                        "mode": "QUEUED_ABORT",
                    },
                    "error": {
                        "code": "OPERATION_CANCELLED",
                        "message": f"job was cancelled while queued: {reason}",
                        "safe_retry": True,
                        "engine_stopped": False,
                    },
                    "execution": {"job_id": job_id, "operation_id": operation_id},
                }
                serialized_result = _dumps_canonical(cancel_result)
                self.db.execute(
                    "UPDATE jobs SET status='CANCELLED', finished_at=CURRENT_TIMESTAMP WHERE job_id=? AND status='QUEUED'",
                    (job_id,),
                )
                self.db.execute(
                    "UPDATE operations SET status='CANCELLED', result=?, finished_at=CURRENT_TIMESTAMP WHERE operation_id=? AND status='QUEUED'",
                    (serialized_result, operation_id),
                )
                event_meta = json.dumps({
                    "reason": reason,
                    "engine_dispatched": False,
                    "cancelled_while": "QUEUED",
                    "mode": "QUEUED_ABORT",
                }, sort_keys=True)
                self.db.execute(
                    "INSERT INTO job_events(job_id, event, metadata) VALUES(?, 'CANCELLED', ?)",
                    (job_id, event_meta),
                )
                self.db.execute("COMMIT")
                updated = self.job(job_id)
                return True, "CANCELLED", updated
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
