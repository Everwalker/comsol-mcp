"""Durable, serialized operation/job ledger."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


# ``resume_claims`` is an additive SQLite table.  Keep the on-disk version at
# 1 because older v1 readers ignore unknown tables and preserve them; the
# production ProcessLock prevents mixed-version daemons from coordinating the
# same project concurrently.  Older binaries do not implement resume and are
# never permitted to replay a claimed continuation after restart.
SCHEMA_VERSION = 1
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST")
METADATA_TABLES = {
    "sessions": "session_id",
    "runtimes": "runtime_id",
    "revisions": "model_key",
    "artifacts": "artifact_id",
    "checkpoints": "checkpoint_id",
}
PROJECT_TABLE_COLUMNS = (
    ("project_id", "TEXT", 1, 1),
    ("workspace", "TEXT", 1, 0),
    ("schema_version", "INTEGER", 1, 0),
    ("revision", "INTEGER", 1, 0),
    ("record_json", "TEXT", 1, 0),
    ("created_at", "TEXT", 1, 0),
    ("updated_at", "TEXT", 1, 0),
)


class IdempotencyConflict(RuntimeError):
    pass


class JobCleanupError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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


def session_recovery_evidence_sha256(value: dict[str, Any]) -> str:
    """Hash the canonical, secret-free session recovery evidence payload."""
    return hashlib.sha256(_dumps_canonical(value).encode("utf-8")).hexdigest()


class OperationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        try:
            self.db.row_factory = sqlite3.Row
            with self.lock:
                self.db.execute("PRAGMA journal_mode=WAL")
                self._migrate()
        except BaseException:
            # A rejected or failed migration must not leave a connection (or
            # WAL lock) behind.  Preserve the migration error if close itself
            # also fails; the caller needs the original failure reason.
            try:
                self.db.close()
            except BaseException:
                pass
            raise

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def _migrate(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL)")
            rows = self.db.execute("SELECT version FROM schema_meta").fetchall()
            if len(rows) > 1:
                raise RuntimeError("ambiguous operation database schema metadata")
            if not rows:
                # An absent version row is only a new database when there is
                # no pre-existing user schema/data to mislabel as current.
                existing = self.db.execute(
                    "SELECT type,name FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' AND name!='schema_meta'"
                ).fetchall()
                if existing:
                    raise RuntimeError("unversioned operation database contains existing schema objects")
                self.db.execute("INSERT INTO schema_meta VALUES(1)")
            elif type(rows[0][0]) is not int:
                raise RuntimeError("invalid operation database schema version")
            elif rows[0][0] == 0:
                self.db.execute("UPDATE schema_meta SET version=1")
            elif rows[0][0] != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported operation database schema {rows[0][0]}")
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
            # Additive extension: existing operation/job/audit/result tables
            # are unchanged, so v1 databases open transactionally without a
            # rewrite or version bump.  A claim is unique per source job and
            # idempotency key and points at the normal child operation/job.
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS resume_claims("
                "source_job_id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE NOT NULL,"
                "request_hash TEXT NOT NULL,child_operation_id TEXT UNIQUE NOT NULL,"
                "child_job_id TEXT UNIQUE NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            resume_object = self.db.execute(
                "SELECT type FROM sqlite_master WHERE name='resume_claims'"
            ).fetchone()
            expected_columns = (
                ("source_job_id", "TEXT", 0, 1, None),
                ("idempotency_key", "TEXT", 1, 0, None),
                ("request_hash", "TEXT", 1, 0, None),
                ("child_operation_id", "TEXT", 1, 0, None),
                ("child_job_id", "TEXT", 1, 0, None),
                ("created_at", "TEXT", 0, 0, "CURRENT_TIMESTAMP"),
            )
            observed_columns = tuple(
                (row[1], (row[2] or "").upper(), row[3], row[5], row[4])
                for row in self.db.execute("PRAGMA table_info(resume_claims)").fetchall()
            )
            unique_columns: set[tuple[str, ...]] = set()
            for index in self.db.execute("PRAGMA index_list(resume_claims)").fetchall():
                if index[2] != 1 or (len(index) > 4 and index[4] != 0):
                    continue
                index_name = str(index[1]).replace("'", "''")
                index_columns = self.db.execute(
                    f"PRAGMA index_info('{index_name}')"
                ).fetchall()
                # A unique expression/composite index is not evidence that a
                # single named column is unique.  In particular, do not drop
                # expression rows before deciding the index's arity.
                if len(index_columns) != 1:
                    continue
                index_column = index_columns[0]
                if index_column[1] < 0 or index_column[2] is None:
                    continue
                unique_columns.add((index_column[2],))
            if (resume_object is None or resume_object[0] != "table"
                    or observed_columns != expected_columns
                    or not {("source_job_id",), ("idempotency_key",),
                            ("child_operation_id",), ("child_job_id",)} <= unique_columns):
                raise RuntimeError("invalid resume_claims schema")
            # Project authority is an additive v1 extension. Keep it in the
            # daemon's single transactional store so backups/restarts carry
            # project records together with operation and job history.
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS projects("
                "project_id TEXT PRIMARY KEY NOT NULL,workspace TEXT NOT NULL UNIQUE,"
                "schema_version INTEGER NOT NULL,revision INTEGER NOT NULL,record_json TEXT NOT NULL,"
                "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
            )
            project_object = self.db.execute(
                "SELECT type FROM sqlite_master WHERE name='projects'"
            ).fetchone()
            observed_project_columns = tuple(
                (row[1], (row[2] or "").upper(), row[3], row[5])
                for row in self.db.execute("PRAGMA table_info(projects)").fetchall()
            )
            unique_project_columns: set[tuple[str, ...]] = set()
            for index in self.db.execute("PRAGMA index_list(projects)").fetchall():
                if index[2] != 1 or (len(index) > 4 and index[4] != 0):
                    continue
                name = str(index[1]).replace("'", "''")
                columns = self.db.execute(f"PRAGMA index_info('{name}')").fetchall()
                if len(columns) == 1 and columns[0][1] >= 0 and columns[0][2] is not None:
                    unique_project_columns.add((columns[0][2],))
            if (project_object is None or project_object[0] != "table"
                    or observed_project_columns != PROJECT_TABLE_COLUMNS
                    or ("workspace",) not in unique_project_columns):
                raise RuntimeError("invalid projects schema")
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

    def begin_resume(
        self,
        *,
        source_job_id: str,
        request_id: str,
        idempotency_key: str,
        request_hash: str,
        metadata: dict[str, Any],
        timeouts: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically claim a failed source job and create one continuation.

        The child is an ordinary ``study.run`` operation in the serial queue.
        Parent status/result are immutable; only an auditable relationship is
        added.  A source job gets one continuation claim for its lifetime.
        """
        if not all(isinstance(value, str) and value for value in
                   (source_job_id, request_id, idempotency_key, request_hash)):
            raise ValueError("resume claim identity fields must be non-empty strings")
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                claim = self.db.execute(
                    "SELECT * FROM resume_claims WHERE source_job_id=?", (source_job_id,)
                ).fetchone()
                if claim:
                    if claim["idempotency_key"] != idempotency_key or claim["request_hash"] != request_hash:
                        raise IdempotencyConflict("source job already has a different resume claim")
                    row = self.db.execute(
                        "SELECT * FROM operations WHERE operation_id=?", (claim["child_operation_id"],)
                    ).fetchone()
                    if not row:
                        raise RuntimeError("resume claim points at a missing child operation")
                    record = self._op(row)
                    record["job_id"] = claim["child_job_id"]
                    self.db.execute("COMMIT")
                    return record, True

                parent = self.db.execute(
                    "SELECT j.status,j.operation_id,j.metadata AS job_metadata,o.metadata AS operation_metadata "
                    "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
                    (source_job_id,),
                ).fetchone()
                if not parent:
                    raise KeyError(f"source job not found: {source_job_id}")
                if parent["status"] != "FAILED":
                    raise ValueError(f"resume source must be FAILED, observed {parent['status']}")
                existing = self.db.execute(
                    "SELECT operation_id,request_hash FROM operations WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    raise IdempotencyConflict("resume idempotency key is already used by another operation")

                operation_id, child_job_id = str(uuid4()), str(uuid4())
                operation_metadata = dict(metadata)
                operation_metadata.setdefault("operation", "study.run")
                child_metadata = {**operation_metadata, "resume_of_job_id": source_job_id,
                                  "parent_job_id": source_job_id}
                serialized_metadata = _dumps_canonical(operation_metadata)
                serialized_child = _dumps_canonical(child_metadata)
                serialized_timeouts = _dumps_canonical(timeouts or {})
                self.db.execute(
                    "INSERT INTO operations(operation_id,request_id,idempotency_key,request_hash,operation,status,metadata,effective_timeouts) "
                    "VALUES(?,?,?,?,?,'QUEUED',?,?)",
                    (operation_id, request_id, idempotency_key, request_hash, "study.run", serialized_metadata, serialized_timeouts),
                )
                self.db.execute(
                    "INSERT INTO jobs(job_id,operation_id,status,metadata,effective_timeouts) VALUES(?,?,'QUEUED',?,?)",
                    (child_job_id, operation_id, serialized_child, serialized_timeouts),
                )
                self.db.execute(
                    "INSERT INTO resume_claims(source_job_id,idempotency_key,request_hash,child_operation_id,child_job_id) "
                    "VALUES(?,?,?,?,?)",
                    (source_job_id, idempotency_key, request_hash, operation_id, child_job_id),
                )
                parent_job_metadata = json.loads(parent["job_metadata"] or "{}")
                child_ids = list(parent_job_metadata.get("continuation_job_ids", []))
                if child_job_id not in child_ids:
                    child_ids.append(child_job_id)
                parent_job_metadata.update({"continuation_job_ids": child_ids, "resumed_by_job_id": child_job_id})
                parent_operation_metadata = json.loads(parent["operation_metadata"] or "{}")
                parent_operation_metadata.update({"continuation_job_ids": child_ids, "resumed_by_job_id": child_job_id})
                self.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?",
                                (_dumps_canonical(parent_job_metadata), source_job_id))
                self.db.execute("UPDATE operations SET metadata=? WHERE operation_id=?",
                                (_dumps_canonical(parent_operation_metadata), parent["operation_id"]))
                self.db.execute(
                    "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                    (source_job_id, "ResumeClaimed", _dumps_canonical({
                        "child_job_id": child_job_id,
                        "child_operation_id": operation_id,
                        "request_hash": request_hash,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    })),
                )
                row = self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
                record = self._op(row)
                record["job_id"] = child_job_id
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

    def session_recovery_resolution(self, job_id: str) -> dict[str, Any] | None:
        """Return the first immutable recovery resolution event for one job."""
        with self.lock:
            row = self.db.execute(
                "SELECT id,event,metadata,created_at FROM job_events "
                "WHERE job_id=? AND event='SessionRecoveryResolution' ORDER BY id LIMIT 1",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            return {**dict(row), "metadata": json.loads(row["metadata"] or "{}")}

    def session_lifecycle_recovery_resolution(self, job_id: str) -> dict[str, Any] | None:
        """Return the first immutable lifecycle recovery event for one job."""
        with self.lock:
            row = self.db.execute(
                "SELECT id,event,metadata,created_at FROM job_events "
                "WHERE job_id=? AND event='SessionLifecycleRecoveryResolution' ORDER BY id LIMIT 1",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            return {**dict(row), "metadata": json.loads(row["metadata"] or "{}")}

    def record_session_lifecycle_recovery_resolution(
        self, job_id: str, source_operation_id: str, expected_lifecycle_revision: int,
        lifecycle_after: dict[str, Any], evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically CAS lifecycle state and append one source-bound resolution.

        The historical source operation/job/result remain untouched. The
        lifecycle transition, append-only proof event, and source-job
        quiescence pointer either commit together or do not become visible.
        """
        if (not isinstance(job_id, str) or not job_id
                or not isinstance(source_operation_id, str) or not source_operation_id
                or type(expected_lifecycle_revision) is not int or expected_lifecycle_revision < 1
                or not isinstance(lifecycle_after, dict) or not isinstance(evidence, dict)):
            raise ValueError("lifecycle recovery requires exact source and lifecycle identities")
        required = {
            "schema_version", "source_job_id", "source_operation_id",
            "session_recovery_operation_id", "project_id", "session_id",
            "source_operation", "source_status", "source_operation_status",
            "source_result_sha256", "original_unknown_reason", "worker_observation",
            "original_worker_binding", "request_observations", "terminal_reply_identities",
            "reconnect_baseline", "connection_observation", "worker_close_event_sha256",
            "worker_close_reaped_event_sha256", "resolution_scope", "classification",
            "replay_performed", "new_worker_created",
        }
        if (not required.issubset(evidence)
                or evidence.get("schema_version") != 1
                or evidence.get("source_job_id") != job_id
                or evidence.get("source_operation_id") != source_operation_id
                or evidence.get("source_operation") not in {
                    "session.connect", "session.disconnect", "session.reconnect",
                }
                or evidence.get("source_status") not in {"UNKNOWN", "RECONCILING"}
                or evidence.get("source_operation_status") not in {"UNKNOWN", "RECONCILING"}
                or evidence.get("replay_performed") is not False
                or evidence.get("new_worker_created") is not False
                or not isinstance(evidence.get("worker_observation"), dict)
                or not isinstance(evidence.get("original_worker_binding"), dict)
                or not isinstance(evidence.get("request_observations"), list)
                or not isinstance(evidence.get("terminal_reply_identities"), list)
                or not isinstance(evidence.get("classification"), str)
                or not evidence.get("classification")
                or evidence.get("resolution_scope") not in {
                    "SESSION_LIFECYCLE_RPC_TERMINAL_QUIESCENCE",
                    "SESSION_WORKER_RETIRED_EXACT",
                }):
            raise ValueError("lifecycle recovery evidence is incomplete or has mismatched source binding")

        from ._session_lifecycle import validate_lifecycle_record

        proposed = validate_lifecycle_record(lifecycle_after)
        project_id, session_id = evidence["project_id"], evidence["session_id"]
        if (proposed["project_id"] != project_id or proposed["session_id"] != session_id
                or proposed["revision"] != expected_lifecycle_revision + 1
                or proposed["health"] != {"status": "UNKNOWN", "observed_at": None, "source": None}):
            raise ValueError("lifecycle recovery target is not the next exact session revision")

        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT j.job_id,j.operation_id,j.status AS job_status,j.metadata AS job_metadata,"
                    "o.status AS operation_status,o.operation,o.result AS operation_result "
                    "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_JOB_NOT_FOUND", "resolution": None}
                if row["operation_id"] != source_operation_id:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_OPERATION_MISMATCH", "resolution": None}
                existing = self.db.execute(
                    "SELECT id,event,metadata,created_at FROM job_events "
                    "WHERE job_id=? AND event='SessionLifecycleRecoveryResolution' ORDER BY id LIMIT 1",
                    (job_id,),
                ).fetchone()
                if existing is not None:
                    resolution = {**dict(existing), "metadata": json.loads(existing["metadata"] or "{}")}
                    self.db.execute("COMMIT")
                    return {"recorded": False, "reason": "ALREADY_RESOLVED", "resolution": resolution}

                unresolved = {"UNKNOWN", "RECONCILING"}
                if row["job_status"] not in unresolved or row["operation_status"] not in unresolved:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_NOT_UNRESOLVED", "resolution": None}
                if row["operation"] != evidence["source_operation"]:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_OPERATION_KIND_MISMATCH", "resolution": None}
                job_metadata = json.loads(row["job_metadata"] or "{}")
                if (not isinstance(job_metadata, dict)
                        or job_metadata.get("project_id") != project_id
                        or job_metadata.get("session_id") != session_id
                        or job_metadata.get("operation") != evidence["source_operation"]):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_SESSION_BINDING_MISMATCH", "resolution": None}
                try:
                    source_result = json.loads(row["operation_result"] or "null")
                except (TypeError, json.JSONDecodeError):
                    source_result = None
                if not isinstance(source_result, dict):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_RESULT_UNAVAILABLE", "resolution": None}
                result_digest = session_recovery_evidence_sha256({"source_result": source_result})
                if evidence["source_result_sha256"] != result_digest:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_RESULT_CHANGED", "resolution": None}

                session_row = self.db.execute(
                    "SELECT metadata FROM sessions WHERE session_id=?", (session_id,),
                ).fetchone()
                if session_row is None:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SESSION_LIFECYCLE_NOT_FOUND", "resolution": None}
                session_metadata = json.loads(session_row["metadata"] or "{}")
                if not isinstance(session_metadata, dict) or not isinstance(session_metadata.get("lifecycle"), dict):
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SESSION_LIFECYCLE_MALFORMED", "resolution": None}
                current = validate_lifecycle_record(session_metadata["lifecycle"])
                if current["project_id"] != project_id or current["session_id"] != session_id:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SESSION_LIFECYCLE_IDENTITY_MISMATCH", "resolution": None}
                if current["revision"] != expected_lifecycle_revision or current["state"] != "UNKNOWN":
                    self.db.execute("ROLLBACK")
                    return {
                        "recorded": False, "reason": "SESSION_LIFECYCLE_REVISION_CONFLICT",
                        "current_revision": current["revision"], "current_state": current["state"],
                        "resolution": None,
                    }

                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                updated = dict(proposed)
                updated["revision"] = current["revision"] + 1
                updated["created_at"] = current["created_at"]
                updated["updated_at"] = now
                updated = validate_lifecycle_record(updated)
                event_evidence = {
                    **evidence,
                    "lifecycle_transition": {
                        "from_revision": current["revision"],
                        "to_revision": updated["revision"],
                        "from_state": current["state"],
                        "from_client_state": current["client_state"],
                        "target_lifecycle": updated,
                    },
                }
                digest = session_recovery_evidence_sha256(event_evidence)
                event_metadata = {**event_evidence, "evidence_sha256": digest}
                cursor = self.db.execute(
                    "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                    (job_id, "SessionLifecycleRecoveryResolution", _dumps_canonical(event_metadata)),
                )
                session_metadata = dict(session_metadata)
                session_metadata["lifecycle"] = updated
                self.db.execute(
                    "UPDATE sessions SET metadata=? WHERE session_id=?",
                    (_dumps_canonical(session_metadata), session_id),
                )
                job_metadata["reconciled_quiescent"] = True
                job_metadata["session_lifecycle_recovery_resolution"] = {
                    "event_id": int(cursor.lastrowid),
                    "evidence_sha256": digest,
                    "session_id": session_id,
                    "project_id": project_id,
                    "lifecycle_revision": updated["revision"],
                    "resolution_scope": evidence["resolution_scope"],
                }
                self.db.execute(
                    "UPDATE jobs SET metadata=? WHERE job_id=?",
                    (_dumps_canonical(job_metadata), job_id),
                )
                self.db.execute("COMMIT")
                return {
                    "recorded": True, "reason": None,
                    "resolution": {"id": int(cursor.lastrowid),
                                   "event": "SessionLifecycleRecoveryResolution",
                                   "metadata": event_metadata},
                    "lifecycle": updated,
                }
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def record_session_recovery_resolution(
        self, job_id: str, source_operation_id: str, evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically append a proof and index its quiescence result.

        The original job/operation status and result are left untouched. Only
        UNKNOWN/RECONCILING pairs may receive a resolution event, and the
        first event wins so later recovery calls cannot rewrite the proof.
        """
        if (not isinstance(job_id, str) or not job_id
                or not isinstance(source_operation_id, str) or not source_operation_id
                or not isinstance(evidence, dict)):
            raise ValueError("session recovery resolution requires exact source identities and evidence")
        required = {"schema_version", "source_job_id", "source_operation_id",
                    "session_recovery_operation_id", "worker_binding", "model_ref",
                    "model_revision", "request_observations", "original_unknown_reason",
                    "source_result_sha256", "replay_performed", "new_worker_created"}
        if (not required.issubset(evidence)
                or evidence.get("schema_version") != 1
                or evidence.get("source_job_id") != job_id
                or evidence.get("source_operation_id") != source_operation_id
                or evidence.get("replay_performed") is not False
                or evidence.get("new_worker_created") is not False):
            raise ValueError("session recovery evidence is incomplete or has mismatched source binding")
        digest = session_recovery_evidence_sha256(evidence)
        event_metadata = {**evidence, "evidence_sha256": digest}

        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT j.status AS job_status,j.metadata AS job_metadata,j.operation_id,"
                    "o.status AS operation_status FROM jobs j "
                    "JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_JOB_NOT_FOUND", "resolution": None}
                if row["operation_id"] != source_operation_id:
                    self.db.execute("ROLLBACK")
                    return {"recorded": False, "reason": "SOURCE_OPERATION_MISMATCH", "resolution": None}
                existing = self.db.execute(
                    "SELECT id,event,metadata,created_at FROM job_events "
                    "WHERE job_id=? AND event='SessionRecoveryResolution' ORDER BY id LIMIT 1",
                    (job_id,),
                ).fetchone()
                if existing is not None:
                    self.db.execute("COMMIT")
                    resolution = {**dict(existing), "metadata": json.loads(existing["metadata"] or "{}")}
                    return {"recorded": False, "reason": "ALREADY_RESOLVED", "resolution": resolution}
                unresolved = {"UNKNOWN", "RECONCILING"}
                if row["job_status"] not in unresolved or row["operation_status"] not in unresolved:
                    self.db.execute("ROLLBACK")
                    return {
                        "recorded": False, "reason": "SOURCE_NOT_UNRESOLVED",
                        "source_job_status": row["job_status"],
                        "source_operation_status": row["operation_status"],
                        "resolution": None,
                    }
                cursor = self.db.execute(
                    "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                    (job_id, "SessionRecoveryResolution", _dumps_canonical(event_metadata)),
                )
                indexed = {
                    "event_id": int(cursor.lastrowid),
                    "evidence_sha256": digest,
                    "session_recovery_operation_id": evidence["session_recovery_operation_id"],
                    "worker_binding": evidence["worker_binding"],
                    "model_ref": evidence["model_ref"],
                    "model_revision": evidence["model_revision"].get("observed_revision"),
                }
                metadata = json.loads(row["job_metadata"] or "{}")
                metadata["reconciled_quiescent"] = True
                metadata["session_recovery_resolution"] = indexed
                self.db.execute(
                    "UPDATE jobs SET metadata=? WHERE job_id=?",
                    (_dumps_canonical(metadata), job_id),
                )
                self.db.execute("COMMIT")
                return {
                    "recorded": True, "reason": None,
                    "resolution": {"id": int(cursor.lastrowid), "event": "SessionRecoveryResolution",
                                   "metadata": event_metadata},
                }
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def events(self, job_id: str, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {**dict(row), "metadata": json.loads(row["metadata"] or "{}")}
                for row in self.db.execute(
                    "SELECT * FROM job_events WHERE job_id=? ORDER BY id LIMIT ? OFFSET ?",
                    (job_id, max(0, min(int(limit), 1000)), max(0, int(offset))),
                )
            ]

    def compact_terminal_job_metadata(self, job_ids: list[str], *, project_id: str | None = None) -> list[str]:
        """Compact selected terminal job-view metadata without deleting audit data.

        Operations, idempotency hashes/keys, results, job events, artifact rows,
        and checkpoint rows remain untouched. The job row retains project
        selectors and a cleanup tombstone; a durable event records the action.
        """
        if not job_ids or any(not isinstance(job_id, str) or not job_id for job_id in job_ids):
            raise JobCleanupError("INVALID_REQUEST", "job_ids must contain non-empty strings")
        if len(set(job_ids)) != len(job_ids):
            raise JobCleanupError("INVALID_REQUEST", "job_ids must not contain duplicates")

        dependency_fields = {"depends_on_job_ids", "dependency_job_ids", "parent_job_id", "continuation_of_job_id", "resume_of_job_id"}

        def dependency_values(value: Any) -> set[str]:
            found: set[str] = set()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in dependency_fields:
                        if isinstance(item, str) and item:
                            found.add(item)
                        elif isinstance(item, list):
                            found.update(candidate for candidate in item if isinstance(candidate, str) and candidate)
                    found.update(dependency_values(item))
            elif isinstance(value, list):
                for item in value:
                    found.update(dependency_values(item))
            return found

        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                selected: list[dict[str, Any]] = []
                for job_id in job_ids:
                    row = self.db.execute(
                        "SELECT j.job_id,j.operation_id,j.status,j.metadata AS job_metadata,"
                        "o.metadata AS operation_metadata "
                        "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
                        (job_id,),
                    ).fetchone()
                    if not row:
                        raise JobCleanupError("NODE_NOT_FOUND", f"job not found: {job_id}")
                    if row["status"] not in TERMINAL:
                        raise JobCleanupError("JOB_NOT_TERMINAL", f"job {job_id} is not terminal")
                    job_metadata = json.loads(row["job_metadata"] or "{}")
                    operation_metadata = json.loads(row["operation_metadata"] or "{}")
                    if project_id is not None:
                        observed_projects: set[str] = set()
                        for metadata in (job_metadata, operation_metadata):
                            if not isinstance(metadata, dict):
                                continue
                            for source in (metadata, metadata.get("arguments", {}), metadata.get("execution", {})):
                                if isinstance(source, dict) and isinstance(source.get("project_id"), str):
                                    observed_projects.add(source["project_id"])
                        if project_id not in observed_projects:
                            raise JobCleanupError("PROJECT_SCOPE_MISMATCH", f"job {job_id} is not recorded in project {project_id}")
                    if dependency_values([job_metadata, operation_metadata]):
                        raise JobCleanupError("JOB_HAS_DEPENDENCIES", f"job {job_id} carries linked job dependencies")
                    selected.append({
                        "job_id": job_id,
                        "operation_id": row["operation_id"],
                        "status": row["status"],
                        "job_metadata": job_metadata,
                    })

                selected_ids = set(job_ids)
                for row in self.db.execute(
                    "SELECT j.job_id,j.metadata AS job_metadata,o.metadata AS operation_metadata "
                    "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id"
                ).fetchall():
                    if row["job_id"] in selected_ids:
                        continue
                    linked = dependency_values([json.loads(row["job_metadata"] or "{}"), json.loads(row["operation_metadata"] or "{}")])
                    if linked.intersection(selected_ids):
                        dependent_id = row["job_id"]
                        raise JobCleanupError("JOB_HAS_DEPENDENCIES", f"job {dependent_id} depends on a selected cleanup job")

                cleaned: list[str] = []
                for row in selected:
                    old_metadata = row["job_metadata"]
                    if old_metadata.get("cleanup", {}).get("metadata_compacted") is True:
                        continue
                    compacted: dict[str, Any] = {}
                    for source in (old_metadata, old_metadata.get("arguments", {}), old_metadata.get("execution", {})):
                        if isinstance(source, dict):
                            for field in ("project_id", "project_root"):
                                if field in source and source[field] is not None:
                                    compacted.setdefault(field, source[field])
                    cleaned_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    compacted["cleanup"] = {
                        "metadata_compacted": True,
                        "cleaned_at": cleaned_at,
                        "prior_status": row["status"],
                        "preserved": ["operation/idempotency record", "result", "job events", "formal artifacts", "checkpoints"],
                    }
                    self.db.execute(
                        "UPDATE jobs SET metadata=? WHERE job_id=?",
                        (_dumps_canonical(compacted), row["job_id"]),
                    )
                    self.db.execute(
                        "INSERT INTO job_events(job_id,event,metadata) VALUES(?,?,?)",
                        (row["job_id"], "JobCacheCompacted", _dumps_canonical({
                            "operation_id": row["operation_id"],
                            "terminal_status": row["status"],
                            "preserved": compacted["cleanup"]["preserved"],
                            "at": cleaned_at,
                        })),
                    )
                    cleaned.append(row["job_id"])
                self.db.execute("COMMIT")
                return cleaned
            except Exception:
                self.db.execute("ROLLBACK")
                raise

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
            if table == "artifacts":
                row = self.db.execute(
                    f"SELECT metadata FROM artifacts WHERE {column}=?", (key,),
                ).fetchone()
                if row is not None:
                    existing = json.loads(row[0])
                    if existing.get("schema_version") == 2 and existing != metadata:
                        raise ValueError("registered artifact metadata is immutable")
                    if existing.get("schema_version") == 2:
                        return
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

    def merge_metadata_fields(self, table: str, key: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Atomically merge top-level fields for shared runtime metadata rows.

        Session rows contain both the Worker ledger snapshot and lifecycle
        authority. Runtime persistence may refresh its own top-level fields,
        but must not erase the nested lifecycle record. This narrow helper is
        intentionally limited to those two row types; immutable artifact and
        revision semantics continue to use their dedicated methods.
        """
        if table not in {"sessions", "runtimes"}:
            raise ValueError("metadata field merge is supported only for sessions and runtimes")
        if not isinstance(updates, dict):
            raise TypeError("metadata updates must be an object")
        column = self._metadata_column(table)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    f"SELECT metadata FROM {table} WHERE {column}=?", (key,),
                ).fetchone()
                current = json.loads(row[0]) if row is not None else {}
                if not isinstance(current, dict):
                    raise RuntimeError(f"persisted {table} metadata is malformed")
                merged = dict(current)
                merged.update(updates)
                self.db.execute(
                    f"INSERT INTO {table}({column},metadata) VALUES(?,?) "
                    f"ON CONFLICT({column}) DO UPDATE SET metadata=excluded.metadata",
                    (key, json.dumps(merged, sort_keys=True)),
                )
                self.db.execute("COMMIT")
                return merged
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def update_session_lifecycle(self, session_id: str, updater) -> dict[str, Any]:
        """Update only ``sessions.metadata.lifecycle`` under the store lock.

        ``updater`` receives a detached current lifecycle mapping (or ``None``)
        and must return the replacement mapping. The surrounding runtime
        ledger fields are preserved in the same SQLite transaction.
        """
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id is required")
        if not callable(updater):
            raise TypeError("lifecycle updater must be callable")
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT metadata FROM sessions WHERE session_id=?", (session_id,),
                ).fetchone()
                metadata = json.loads(row[0]) if row is not None else {}
                if not isinstance(metadata, dict):
                    raise RuntimeError("persisted session metadata is malformed")
                current = metadata.get("lifecycle")
                if current is not None and not isinstance(current, dict):
                    raise RuntimeError("persisted lifecycle metadata is malformed")
                replacement = updater(dict(current) if current is not None else None)
                if not isinstance(replacement, dict):
                    raise TypeError("lifecycle updater must return an object")
                metadata = dict(metadata)
                metadata["lifecycle"] = replacement
                self.db.execute(
                    "INSERT INTO sessions(session_id,metadata) VALUES(?,?) "
                    "ON CONFLICT(session_id) DO UPDATE SET metadata=excluded.metadata",
                    (session_id, json.dumps(metadata, sort_keys=True)),
                )
                self.db.execute("COMMIT")
                return replacement
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def save_metadata(self, *args: Any, **kwargs: Any) -> None:
        self.put_metadata(*args, **kwargs)

    def get_metadata(self, table: str, key: str) -> dict[str, Any] | None:
        column = self._metadata_column(table)
        with self.lock:
            row = self.db.execute(f"SELECT metadata FROM {table} WHERE {column}=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def read_experiment_snapshot(
        self, project_id: str, experiment_id: str, *, case_id: str | None = None,
    ) -> dict[str, Any]:
        """Read one W21 experiment and its producer records from one SQLite snapshot.

        This is a control-plane read: it neither enters the Worker scheduler nor
        mutates OperationStore state.  The method deliberately owns the key
        conventions for W21's durable design/run/case records so callers cannot
        combine records observed at different database revisions.
        """
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id is required")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise ValueError("experiment_id is required")
        if case_id is not None and (not isinstance(case_id, str) or not case_id):
            raise ValueError("case_id must be a non-empty string")

        def decode_operation(row: sqlite3.Row) -> dict[str, Any]:
            value = dict(row)
            value["result"] = json.loads(value["result"]) if value.get("result") else None
            value["metadata"] = json.loads(value.get("metadata") or "{}")
            value["effective_timeouts"] = json.loads(value.get("effective_timeouts") or "{}")
            value["job_metadata"] = json.loads(value.pop("job_metadata") or "{}")
            return value

        def decode_artifact(row: sqlite3.Row | None) -> dict[str, Any] | None:
            return json.loads(row[0]) if row is not None else None

        project_paths = (
            "$.project_id", "$.execution.project_id", "$.arguments.project_id",
            "$.arguments.arguments.project_id",
        )

        def owned_json(expression: str) -> tuple[str, tuple[str, ...]]:
            any_claim = " OR ".join(
                f"(json_type({expression},'{path}')='text' AND json_extract({expression},'{path}')=?)"
                for path in project_paths
            )
            all_consistent = " AND ".join(
                f"(json_type({expression},'{path}') IS NULL OR "
                f"(json_type({expression},'{path}')='text' AND json_extract({expression},'{path}')=?))"
                for path in project_paths
            )
            return (
                f"CASE WHEN json_valid({expression}) THEN (({any_claim}) AND ({all_consistent})) ELSE 0 END",
                (project_id,) * (2 * len(project_paths)),
            )

        producer_project_match, producer_project_params = owned_json("o.metadata")
        artifact_project_match = (
            "CASE WHEN json_valid(a.metadata) THEN "
            "CASE WHEN json_type(a.metadata,'$.project_id') IS NOT NULL THEN "
            "(json_type(a.metadata,'$.project_id')='text' AND json_extract(a.metadata,'$.project_id')=?) "
            "ELSE EXISTS(SELECT 1 FROM operations o "
            "WHERE o.operation_id=json_extract(a.metadata,'$.producer') AND "
            f"({producer_project_match})) END ELSE 0 END"
        )
        artifact_project_params = (project_id, *producer_project_params)

        with self.lock:
            self.db.execute("BEGIN")
            try:
                design_row = self.db.execute(
                    "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                    ("w21experiment:" + experiment_id, *artifact_project_params),
                ).fetchone()
                design = decode_artifact(design_row)
                planned_case_ids: list[str] = []
                if isinstance(design, dict) and isinstance(design.get("cases"), list):
                    planned_case_ids = [
                        row.get("case_id") for row in design["cases"]
                        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
                    ]
                selected_case_ids = (
                    [case_id] if case_id is not None and case_id in planned_case_ids
                    else [] if case_id is not None
                    else planned_case_ids
                )
                run = decode_artifact(self.db.execute(
                    "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                    ("w21experimentrun:" + experiment_id, *artifact_project_params),
                ).fetchone())
                cases: dict[str, dict[str, Any]] = {}
                for selected in selected_case_ids:
                    record = decode_artifact(self.db.execute(
                        "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                        ("w21experimentcase:" + experiment_id + ":" + selected, *artifact_project_params),
                    ).fetchone())
                    if record is not None:
                        cases[selected] = record

                producer_ids = {
                    value.get("producer")
                    for value in (design, run, *cases.values())
                    if isinstance(value, dict) and isinstance(value.get("producer"), str)
                }
                operation_rows: dict[str, dict[str, Any]] = {}
                if producer_ids:
                    placeholders = ",".join("?" for _ in producer_ids)
                    rows = self.db.execute(
                        "SELECT o.*,j.status AS job_status,j.metadata AS job_metadata "
                        "FROM operations o LEFT JOIN jobs j ON j.operation_id=o.operation_id "
                        f"WHERE o.operation_id IN ({placeholders}) AND {producer_project_match}",
                        (*tuple(sorted(producer_ids)), *producer_project_params),
                    ).fetchall()
                    operation_rows.update({row["operation_id"]: decode_operation(row) for row in rows})

                # A queued run has not executed its callback yet, so its run
                # artifact does not exist.  Capture canonical run claims in
                # the same read transaction so inspect can report QUEUED or
                # RUNNING without waiting for the engine lane.
                run_operation_rows: list[dict[str, Any]] = []
                if design is not None and run is None:
                    run_experiment_match = (
                        "CASE WHEN json_valid(o.metadata) THEN "
                        "(json_extract(o.metadata,'$.arguments.experiment_id')=? OR "
                        "(json_extract(o.metadata,'$.arguments.operation_id')='experiment.run' AND "
                        "json_extract(o.metadata,'$.arguments.arguments.experiment_id')=?)) ELSE 0 END"
                    )
                    rows = self.db.execute(
                        "SELECT o.*,j.status AS job_status,j.metadata AS job_metadata "
                        "FROM operations o LEFT JOIN jobs j ON j.operation_id=o.operation_id "
                        "WHERE o.operation IN ('experiment.run','registry_call','operation_call') "
                        "AND " + run_experiment_match + " AND " + producer_project_match + " "
                        "ORDER BY o.created_at,o.operation_id",
                        (experiment_id, experiment_id, *producer_project_params),
                    ).fetchall()
                    run_operation_rows = [decode_operation(row) for row in rows]

                self.db.execute("COMMIT")
                return {
                    "design": design,
                    "run": run,
                    "cases": cases,
                    "planned_case_ids": planned_case_ids,
                    "operations": operation_rows,
                    "run_operations": run_operation_rows,
                }
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def list_metadata(self, table: str) -> list[dict[str, Any]]:
        self._metadata_column(table)  # Table interpolation is safe only after allowlisting.
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(f"SELECT metadata FROM {table}")]

    def persist_artifact(self, key: str, metadata: dict[str, Any]) -> None:
        if not metadata.get("sha256"):
            raise ValueError("artifact sha256 required")
        # Older output/checkpoint paths share this table. Preserve a registered
        # content-addressed record if a legacy writer happens to reuse its key;
        # put_metadata rejects a different payload instead of silently
        # replacing the project/host/path/provenance binding.
        self.put_metadata("artifacts", key, metadata)

    def register_artifact_if_absent(self, key: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
        """Atomically insert one content-addressed artifact record.

        Artifact registrations share the existing artifacts table with other
        durable artifact references. They must never use the general upsert:
        two callers registering the same digest with different project or
        provenance metadata must see the original row and let the caller reject
        the conflict, rather than replacing a record another operation uses.

        Returns the pre-existing row when the key was already present, or
        ``None`` when this transaction inserted the new row.
        """
        if not metadata.get("sha256"):
            raise ValueError("artifact sha256 required")
        column = self._metadata_column("artifacts")
        encoded = json.dumps(metadata, sort_keys=True)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    f"SELECT metadata FROM artifacts WHERE {column}=?", (key,),
                ).fetchone()
                if row is not None:
                    existing = json.loads(row[0])
                    self.db.execute("COMMIT")
                    return existing
                self.db.execute(
                    f"INSERT INTO artifacts({column},metadata) VALUES(?,?)",
                    (key, encoded),
                )
                self.db.execute("COMMIT")
                return None
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

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
