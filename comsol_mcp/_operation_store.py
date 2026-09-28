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
STAGE_ATTEMPT_TABLE_COLUMNS = (
    ("attempt_id", "TEXT", 1, 1, None),
    ("scope_digest", "TEXT", 1, 0, None),
    ("stage_id", "TEXT", 1, 0, None),
    ("idempotency_key", "TEXT", 1, 0, None),
    ("request_hash", "TEXT", 1, 0, None),
    ("status", "TEXT", 1, 0, None),
    ("version", "INTEGER", 1, 0, None),
    ("record_json", "TEXT", 1, 0, None),
    ("created_at", "TEXT", 1, 0, "CURRENT_TIMESTAMP"),
    ("updated_at", "TEXT", 1, 0, "CURRENT_TIMESTAMP"),
)
STAGE_ATTEMPT_STATUSES = frozenset({
    "ADMITTED", "DISPATCH_INTENT", "RUNNING", "NOT_DISPATCHED_UNVERIFIED",
    "MAPPING_CONFIGURED_PARTIAL", "SUCCEEDED_PARTIAL", "ACCEPTED",
    "FAILED", "UNKNOWN", "CANCELLED",
})
STAGE_ATTEMPT_TRANSITIONS = {
    "ADMITTED": frozenset({"DISPATCH_INTENT", "RUNNING", "NOT_DISPATCHED_UNVERIFIED", "MAPPING_CONFIGURED_PARTIAL", "FAILED", "UNKNOWN", "CANCELLED"}),
    "DISPATCH_INTENT": frozenset({"RUNNING", "UNKNOWN", "CANCELLED"}),
    "RUNNING": frozenset({"RUNNING", "SUCCEEDED_PARTIAL", "FAILED", "UNKNOWN", "CANCELLED"}),
}
STAGE_EXECUTION_STATUSES = frozenset({
    "NOT_STARTED", "NOT_DISPATCHED", "MAPPING_CONFIGURED", "DISPATCH_INTENT", "RUNNING",
    "SOLVE_SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED",
})
STAGE_ACCEPTANCE_STATUSES = frozenset({
    "NOT_EVALUATED", "UNVERIFIED", "PARTIAL", "ACCEPTED", "REJECTED", "UNKNOWN",
})


class IdempotencyConflict(RuntimeError):
    pass


class JobCleanupError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class StagePlanStoreConflict(RuntimeError):
    """A plan or stage ID is already bound to different immutable content."""

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
            # Durable W21 stage attempts are additive to v1 databases. Their
            # canonical record includes the full project/ModelRef/plan/stage
            # binding and is integrity checked on every read and CAS update.
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS stage_attempts("
                "attempt_id TEXT PRIMARY KEY NOT NULL,scope_digest TEXT NOT NULL,"
                "stage_id TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,"
                "request_hash TEXT NOT NULL,status TEXT NOT NULL,version INTEGER NOT NULL,"
                "record_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_stage_attempt_scope "
                "ON stage_attempts(scope_digest,stage_id,created_at,attempt_id)"
            )
            attempt_object = self.db.execute(
                "SELECT type FROM sqlite_master WHERE name='stage_attempts'"
            ).fetchone()
            observed_attempt_columns = tuple(
                (row[1], (row[2] or "").upper(), row[3], row[5], row[4])
                for row in self.db.execute("PRAGMA table_info(stage_attempts)").fetchall()
            )
            unique_attempt_columns: set[tuple[str, ...]] = set()
            for index in self.db.execute("PRAGMA index_list(stage_attempts)").fetchall():
                if index[2] != 1 or (len(index) > 4 and index[4] != 0):
                    continue
                name = str(index[1]).replace("'", "''")
                columns = self.db.execute(f"PRAGMA index_info('{name}')").fetchall()
                if len(columns) == 1 and columns[0][1] >= 0 and columns[0][2] is not None:
                    unique_attempt_columns.add((columns[0][2],))
            if (attempt_object is None or attempt_object[0] != "table"
                    or observed_attempt_columns != STAGE_ATTEMPT_TABLE_COLUMNS
                    or not {("attempt_id",), ("idempotency_key",)} <= unique_attempt_columns):
                raise RuntimeError("invalid stage_attempts schema")
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
                    metric_kinds = {
                        "w17_metric_definition_head", "w17_metric_definition_version", "w17_metric_evaluation",
                    }
                    if existing.get("kind") in metric_kinds:
                        if existing != metadata:
                            raise ValueError("metric records are immutable outside their atomic store adapter")
                        return
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
                metric_definition_versions: list[dict[str, Any]] = []
                metric_version_snapshot_valid = True
                if isinstance(design, dict):
                    definition = design.get("definition")
                    references = definition.get("metric_evaluations", []) if isinstance(definition, dict) else None
                    if not isinstance(references, list):
                        metric_version_snapshot_valid = False
                    else:
                        seen_metric_ids: set[str] = set()
                        for reference in references:
                            if (not isinstance(reference, dict)
                                    or set(reference) != {"metric_id", "version", "definition_sha256"}):
                                metric_version_snapshot_valid = False
                                break
                            metric_id = reference.get("metric_id")
                            version = reference.get("version")
                            if (not isinstance(metric_id, str) or not metric_id or metric_id in seen_metric_ids
                                    or isinstance(version, bool) or not isinstance(version, int) or version < 1):
                                metric_version_snapshot_valid = False
                                break
                            seen_metric_ids.add(metric_id)
                            scope = self._metric_scope_digest(project_id, metric_id)
                            version_key = f"w17metric.version.{scope}.{version:08d}"
                            version_row = self.db.execute(
                                f"SELECT metadata FROM artifacts WHERE {self._metadata_column('artifacts')}=?",
                                (version_key,),
                            ).fetchone()
                            version_record = decode_artifact(version_row)
                            if (not isinstance(version_record, dict)
                                    or version_record.get("kind") != "w17_metric_definition_version"
                                    or version_record.get("project_id") != project_id
                                    or version_record.get("metric_id") != metric_id
                                    or version_record.get("version") != version
                                    or version_record.get("removed") is not False
                                    or version_record.get("definition_sha256") != reference.get("definition_sha256")
                                    or version_record.get("sha256") != self._metric_record_digest(version_record)):
                                metric_version_snapshot_valid = False
                                break
                            metric_definition_versions.append(version_record)
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
                attempts: dict[str, dict[str, Any]] = {}
                evaluations: dict[str, dict[str, Any]] = {}
                observations: dict[str, dict[str, Any]] = {}
                for selected in selected_case_ids:
                    record = decode_artifact(self.db.execute(
                        "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                        ("w21experimentcase:" + experiment_id + ":" + selected, *artifact_project_params),
                    ).fetchone())
                    if record is not None:
                        cases[selected] = record
                    attempt = decode_artifact(self.db.execute(
                        "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                        ("w21experimentattempt:" + experiment_id + ":" + selected, *artifact_project_params),
                    ).fetchone())
                    if attempt is not None:
                        attempts[selected] = attempt
                    case_data = record.get("case") if isinstance(record, dict) else None
                    if case_data is None and isinstance(run, dict) and isinstance(run.get("cases"), list):
                        case_data = next((row for row in run["cases"]
                                          if isinstance(row, dict) and row.get("case_id") == selected), None)
                    sample_reference = case_data.get("observation_ref") if isinstance(case_data, dict) else None
                    sample_observation_id = (
                        sample_reference.get("observation_id")
                        if isinstance(sample_reference, dict) else None
                    )
                    if isinstance(sample_observation_id, str) and sample_observation_id:
                        sample_observation = decode_artifact(self.db.execute(
                            "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                            (sample_observation_id, *artifact_project_params),
                        ).fetchone())
                        if sample_observation is not None:
                            observations[sample_observation_id] = sample_observation
                    association = case_data.get("metric_evaluation") if isinstance(case_data, dict) else None
                    evaluation_id = association.get("evaluation_id") if isinstance(association, dict) else None
                    if isinstance(evaluation_id, str) and evaluation_id:
                        evaluation = decode_artifact(self.db.execute(
                            "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                            (evaluation_id, *artifact_project_params),
                        ).fetchone())
                        if evaluation is not None:
                            evaluations[selected] = evaluation
                        binding = evaluation.get("case_binding") if isinstance(evaluation, dict) else None
                        sample_reference = binding.get("sample_observation_ref") if isinstance(binding, dict) else None
                        sample_observation_id = (
                            sample_reference.get("observation_id")
                            if isinstance(sample_reference, dict) else None
                        )
                        if isinstance(sample_observation_id, str) and sample_observation_id:
                            sample_observation = decode_artifact(self.db.execute(
                                "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                                (sample_observation_id, *artifact_project_params),
                            ).fetchone())
                            if sample_observation is not None:
                                observations[sample_observation_id] = sample_observation
                        items = evaluation.get("items") if isinstance(evaluation, dict) else None
                        if isinstance(items, list):
                            for item in items:
                                reference = item.get("observation_ref") if isinstance(item, dict) else None
                                observation_id = reference.get("observation_id") if isinstance(reference, dict) else None
                                if not isinstance(observation_id, str) or not observation_id:
                                    continue
                                observation = decode_artifact(self.db.execute(
                                    "SELECT a.metadata FROM artifacts a WHERE a.artifact_id=? AND " + artifact_project_match,
                                    (observation_id, *artifact_project_params),
                                ).fetchone())
                                if observation is not None:
                                    observations[observation_id] = observation

                producer_ids = {
                    value.get("producer")
                    for value in (design, run, *cases.values(), *attempts.values())
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
                    "attempts": attempts,
                    "evaluations": evaluations,
                    "observations": observations,
                    "metric_definition_versions": metric_definition_versions,
                    "metric_version_snapshot_valid": metric_version_snapshot_valid,
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

    @staticmethod
    def _stage_scope_digest(project_id: str, model_ref: dict[str, Any]) -> str:
        from ._stage_contract import sha256_json
        return sha256_json({"project_id": project_id, "model_ref": model_ref})

    @classmethod
    def stage_plan_key(cls, project_id: str, model_ref: dict[str, Any], plan_id: str) -> str:
        return f"w21-stage-plan:{cls._stage_scope_digest(project_id, model_ref)}:{plan_id}"

    @classmethod
    def stage_id_key(cls, project_id: str, model_ref: dict[str, Any], stage_id: str) -> str:
        return f"w21-stage-id:{cls._stage_scope_digest(project_id, model_ref)}:{stage_id}"

    @staticmethod
    def _validate_stage_plan_record(record: Any) -> dict[str, Any]:
        from ._stage_contract import sha256_json, validate_stage_plan_definition
        if not isinstance(record, dict):
            raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage plan is malformed")
        payload = dict(record)
        observed = payload.pop("sha256", None)
        definition = record.get("definition")
        try:
            normalized_definition = validate_stage_plan_definition(definition)
        except Exception as exc:
            raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage plan definition is malformed") from exc
        if (type(record.get("schema_version")) is not int or record.get("schema_version") != 1
                or record.get("kind") != "w21_stage_plan"
                or not isinstance(record.get("project_id"), str)
                or not isinstance(record.get("model_ref"), dict)
                or not isinstance(record.get("plan_id"), str)
                or record.get("plan_id") != normalized_definition.get("plan_id")
                or type(record.get("declaration_revision")) is not int
                or record.get("declaration_revision") < 0
                or record.get("declaration_status") != "DECLARED_UNVERIFIED"
                or not isinstance(record.get("declaration_evidence"), dict)
                or normalized_definition != definition
                or record.get("definition_sha256") != sha256_json(definition)
                or not isinstance(observed, str) or observed != sha256_json(payload)):
            raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage plan failed its integrity check")
        return record

    @staticmethod
    def _stage_json_equal(left: Any, right: Any) -> bool:
        """Compare persisted JSON identities without Python bool/int aliasing."""
        from ._stage_contract import canonical_json

        try:
            return canonical_json(left) == canonical_json(right)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _stage_index_record(project_id: str, model_ref: dict[str, Any], plan_record: dict[str, Any],
                            stage: dict[str, Any]) -> dict[str, Any]:
        from ._stage_contract import sha256_json

        index = {
            "schema_version": 1,
            "kind": "w21_stage_id_index",
            "project_id": project_id,
            "model_ref": dict(model_ref),
            "plan_id": plan_record["plan_id"],
            "plan_sha256": plan_record["sha256"],
            "definition_sha256": plan_record["definition_sha256"],
            "stage_id": stage["stage_id"],
            "ordinal": stage["ordinal"],
        }
        index["sha256"] = sha256_json(index)
        return index

    def _load_validated_stage_scope_locked(
        self, project_id: str, model_ref: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Read every plan and index in one stable project/ModelRef scope.

        Multiple plans may coexist in a scope. Every plan must own exactly the
        indexes named by its definition, and every index must bind back to its
        owner plan and exact stage ordinal. This detects missing, extra, stale,
        or cross-owned rows without treating valid indexes from another plan
        as corruption of the current plan.
        """
        from ._stage_contract import sha256_json

        digest = self._stage_scope_digest(project_id, model_ref)
        column = self._metadata_column("artifacts")
        plan_rows = self.db.execute(
            f"SELECT {column},metadata FROM artifacts WHERE {column} LIKE ? ORDER BY {column}",
            (f"w21-stage-plan:{digest}:%",),
        ).fetchall()
        index_rows = self.db.execute(
            f"SELECT {column},metadata FROM artifacts WHERE {column} LIKE ? ORDER BY {column}",
            (f"w21-stage-id:{digest}:%",),
        ).fetchall()

        plans: dict[str, dict[str, Any]] = {}
        for row in plan_rows:
            key = row[0]
            try:
                record = json.loads(row[1])
            except (TypeError, ValueError) as exc:
                raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage plan metadata is unreadable") from exc
            record = self._validate_stage_plan_record(record)
            plan_id = record["plan_id"]
            if (key != self.stage_plan_key(project_id, model_ref, plan_id)
                    or record.get("project_id") != project_id
                    or not self._stage_json_equal(record.get("model_ref"), model_ref)
                    or plan_id in plans):
                raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored plan ownership or key is inconsistent")
            plans[plan_id] = record

        indexes: dict[str, dict[str, Any]] = {}
        indexes_by_plan: dict[str, dict[str, dict[str, Any]]] = {}
        for row in index_rows:
            key = row[0]
            try:
                index = self._validate_stage_index_record(json.loads(row[1]))
            except (TypeError, ValueError) as exc:
                raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage index metadata is unreadable") from exc
            stage_id = index.get("stage_id")
            plan_id = index.get("plan_id")
            if (not isinstance(stage_id, str) or not stage_id
                    or not isinstance(plan_id, str) or not plan_id
                    or type(index.get("ordinal")) is not int or index["ordinal"] < 1
                    or key != self.stage_id_key(project_id, model_ref, stage_id)
                    or index.get("project_id") != project_id
                    or not self._stage_json_equal(index.get("model_ref"), model_ref)
                    or stage_id in indexes):
                raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage index ownership or key is inconsistent")
            indexes[stage_id] = index
            indexes_by_plan.setdefault(plan_id, {})[stage_id] = index

        if set(indexes_by_plan) - set(plans):
            raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage index refers to a missing plan")

        for plan_id, plan in plans.items():
            stages = plan["definition"]["stages"]
            expected_ids = {stage["stage_id"] for stage in stages}
            actual_for_plan = indexes_by_plan.get(plan_id, {})
            if set(actual_for_plan) != expected_ids:
                raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage index set differs from its owning plan")
            for stage in stages:
                expected = self._stage_index_record(project_id, model_ref, plan, stage)
                if actual_for_plan.get(stage["stage_id"]) != expected:
                    raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage index binding differs from its owning plan")

        return plans, indexes

    def _read_validated_stage_scope(
        self, project_id: str, model_ref: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Read a consistent scope snapshot, preserving an enclosing transaction."""
        with self.lock:
            started_transaction = not self.db.in_transaction
            if started_transaction:
                self.db.execute("BEGIN")
            try:
                result = self._load_validated_stage_scope_locked(project_id, model_ref)
                if started_transaction:
                    self.db.execute("COMMIT")
                return result
            except BaseException:
                if started_transaction and self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def register_stage_plan(self, *, project_id: str, model_ref: dict[str, Any],
                            plan_record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Atomically register a complete immutable plan and all stage IDs.

        ``model_ref`` is the complete stable ModelRef identity. Its generation
        and server instance are part of the scope; a later managed revision is
        recorded separately and does not change plan identity.
        """
        from ._stage_contract import canonical_json, sha256_json

        if not isinstance(plan_record, dict):
            raise StagePlanStoreConflict("INVALID_REQUEST", "stage plan registration payload is malformed")
        plan_id = plan_record.get("plan_id")
        definition = plan_record.get("definition")
        definition_sha = plan_record.get("definition_sha256")
        stages = definition.get("stages") if isinstance(definition, dict) else None
        if (not isinstance(project_id, str) or not project_id
                or not isinstance(model_ref, dict)
                or not isinstance(plan_id, str) or not plan_id
                or type(plan_record.get("schema_version")) is not int
                or plan_record.get("schema_version") != 1
                or plan_record.get("kind") != "w21_stage_plan"
                or plan_record.get("project_id") != project_id
                or not self._stage_json_equal(plan_record.get("model_ref"), model_ref)
                or not isinstance(definition, dict)
                or definition.get("plan_id") != plan_id
                or definition_sha != sha256_json(definition)
                or not isinstance(stages, list) or not stages):
            raise StagePlanStoreConflict("INVALID_REQUEST", "stage plan registration payload is malformed")
        try:
            self._validate_stage_plan_record(plan_record)
        except StagePlanStoreConflict as exc:
            raise StagePlanStoreConflict("INVALID_REQUEST", "stage plan registration payload failed validation") from exc
        plan_key = self.stage_plan_key(project_id, model_ref, plan_id)
        plan_encoded = canonical_json(plan_record)
        plan_column = self._metadata_column("artifacts")
        index_records: list[tuple[str, str]] = []
        for stage in stages:
            if not isinstance(stage, dict):
                raise StagePlanStoreConflict("INVALID_REQUEST", "stage plan stage index is malformed")
            try:
                index = self._stage_index_record(project_id, model_ref, plan_record, stage)
            except (KeyError, TypeError) as exc:
                raise StagePlanStoreConflict("INVALID_REQUEST", "stage plan stage index is malformed") from exc
            if type(index["ordinal"]) is not int:
                raise StagePlanStoreConflict("INVALID_REQUEST", "stage plan stage index is malformed")
            index_records.append((self.stage_id_key(project_id, model_ref, index["stage_id"]), canonical_json(index)))

        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                existing_row = self.db.execute(
                    f"SELECT metadata FROM artifacts WHERE {plan_column}=?", (plan_key,),
                ).fetchone()
                if existing_row is not None:
                    existing = self._validate_stage_plan_record(json.loads(existing_row[0]))
                    if (existing.get("project_id") != project_id
                            or not self._stage_json_equal(existing.get("model_ref"), model_ref)
                            or existing.get("plan_id") != plan_id):
                        raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored plan scope is inconsistent")
                    if existing.get("definition_sha256") != definition_sha or existing.get("definition") != definition:
                        raise StagePlanStoreConflict("STAGE_PLAN_CONFLICT", "plan_id is already registered with different definition content")
                    # Same plan ID and definition is idempotent. Validate all
                    # coexisting plans and their own complete index sets.
                    plans, _indexes = self._load_validated_stage_scope_locked(project_id, model_ref)
                    stored = plans.get(plan_id)
                    if stored is None:
                        raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored plan is absent from its model scope")
                    self.db.execute("COMMIT")
                    return stored, False

                # Reject corruption anywhere in this identity scope before
                # adding another plan; valid disjoint plans remain allowed.
                _plans, existing_indexes = self._load_validated_stage_scope_locked(project_id, model_ref)
                if any(stage_id in existing_indexes for stage_id in (stage["stage_id"] for stage in stages)):
                    raise StagePlanStoreConflict("STAGE_ID_CONFLICT", "stage_id is already registered in this project and model scope")

                plan_sha = plan_record.get("sha256")
                if not isinstance(plan_sha, str) or plan_sha != sha256_json({k: v for k, v in plan_record.items() if k != "sha256"}):
                    raise StagePlanStoreConflict("INVALID_REQUEST", "stage plan record hash is invalid")
                self.db.execute(
                    f"INSERT INTO artifacts({plan_column},metadata) VALUES(?,?)", (plan_key, plan_encoded),
                )
                for stage_key, encoded in index_records:
                    self.db.execute(
                        f"INSERT INTO artifacts({plan_column},metadata) VALUES(?,?)", (stage_key, encoded),
                    )
                self.db.execute("COMMIT")
                return plan_record, True
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def get_stage_plan(self, project_id: str, model_ref: dict[str, Any], plan_id: str) -> dict[str, Any] | None:
        """Read a plan only after verifying all plans and indexes in its scope."""
        plans, _indexes = self._read_validated_stage_scope(project_id, model_ref)
        return plans.get(plan_id)

    @staticmethod
    def _validate_stage_index_record(record: Any) -> dict[str, Any]:
        from ._stage_contract import sha256_json
        if not isinstance(record, dict):
            raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage ID index is malformed")
        payload = dict(record)
        observed = payload.pop("sha256", None)
        if (type(record.get("schema_version")) is not int or record.get("schema_version") != 1
                or record.get("kind") != "w21_stage_id_index"
                or not isinstance(observed, str) or observed != sha256_json(payload)):
            raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage ID index failed its integrity check")
        return record

    def get_stage_id_index(self, project_id: str, model_ref: dict[str, Any], stage_id: str) -> dict[str, Any] | None:
        """Read one index only after validating its owner plan and complete scope."""
        resolved = self.resolve_stage(project_id, model_ref, stage_id)
        return resolved["index"] if resolved is not None else None

    def resolve_stage(self, project_id: str, model_ref: dict[str, Any], stage_id: str) -> dict[str, Any] | None:
        """Resolve an index to its exact persisted plan and stage declaration."""
        plans, indexes = self._read_validated_stage_scope(project_id, model_ref)
        index = indexes.get(stage_id)
        if index is None:
            return None
        plan = plans.get(index["plan_id"])
        if plan is None:
            raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage index owner plan is unavailable")
        matches = [stage for stage in plan["definition"]["stages"] if stage["stage_id"] == stage_id]
        if len(matches) != 1:
            raise StagePlanStoreConflict("STAGE_PLAN_STATE_UNKNOWN", "stored stage index does not resolve uniquely in its plan")
        return {"index": index, "plan": plan, "stage": matches[0]}

    @staticmethod
    def _validate_stage_attempt_record(record: Any, *, project_id: str | None = None,
                                        model_ref: dict[str, Any] | None = None) -> dict[str, Any]:
        from ._stage_contract import sha256_json

        if not isinstance(record, dict):
            raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stored stage attempt is malformed")
        payload = dict(record)
        observed = payload.pop("sha256", None)
        scope_digest = record.get("scope_digest")
        status = record.get("status")
        if (type(record.get("schema_version")) is not int or record.get("schema_version") != 1
                or record.get("kind") != "w21_stage_attempt"
                or not isinstance(record.get("project_id"), str) or not record["project_id"]
                or not isinstance(record.get("model_ref"), dict)
                or not isinstance(scope_digest, str) or len(scope_digest) != 64
                or scope_digest != OperationStore._stage_scope_digest(record["project_id"], record["model_ref"])
                or not isinstance(record.get("plan_id"), str) or not record["plan_id"]
                or not isinstance(record.get("plan_sha256"), str) or len(record["plan_sha256"]) != 64
                or not isinstance(record.get("definition_sha256"), str) or len(record["definition_sha256"]) != 64
                or not isinstance(record.get("stage_id"), str) or not record["stage_id"]
                or type(record.get("ordinal")) is not int or record["ordinal"] < 1
                or not isinstance(record.get("attempt_id"), str) or not record["attempt_id"]
                or type(record.get("attempt_number")) is not int or record["attempt_number"] < 1
                or not isinstance(record.get("idempotency_key"), str) or not record["idempotency_key"]
                or not isinstance(record.get("operation_id"), str) or not record["operation_id"]
                or not isinstance(record.get("request_hash"), str) or len(record["request_hash"]) != 64
                or not isinstance(record.get("request_id"), str) or not record["request_id"]
                or type(record.get("expected_revision")) is not int or record["expected_revision"] < 0
                or (record.get("source_attempt_id") is not None
                    and (not isinstance(record.get("source_attempt_id"), str) or not record["source_attempt_id"]))
                or status not in STAGE_ATTEMPT_STATUSES
                or type(record.get("version")) is not int or record["version"] < 1
                or type(record.get("engine_dispatched")) is not bool
                or record.get("execution_status") not in STAGE_EXECUTION_STATUSES
                or record.get("acceptance_status") not in STAGE_ACCEPTANCE_STATUSES
                or not isinstance(record.get("evidence"), list)
                or not isinstance(record.get("result"), (dict, type(None)))
                or not isinstance(observed, str) or observed != sha256_json(payload)
                or (project_id is not None and record.get("project_id") != project_id)
                or (model_ref is not None and not OperationStore._stage_json_equal(record.get("model_ref"), model_ref))):
            raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stored stage attempt failed its integrity or scope check")
        return record

    def _stage_attempt_from_row(self, row, *, project_id: str | None = None,
                                model_ref: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            record = json.loads(row["record_json"])
        except (TypeError, ValueError, KeyError) as exc:
            raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stored stage attempt JSON is unreadable") from exc
        record = self._validate_stage_attempt_record(record, project_id=project_id, model_ref=model_ref)
        if (record["attempt_id"] != row["attempt_id"]
                or record["scope_digest"] != row["scope_digest"]
                or record["stage_id"] != row["stage_id"]
                or record["idempotency_key"] != row["idempotency_key"]
                or record["request_hash"] != row["request_hash"]
                or record["status"] != row["status"]
                or record["version"] != row["version"]):
            raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stage attempt index columns disagree with its hashed record")
        plans, indexes = self._load_validated_stage_scope_locked(record["project_id"], record["model_ref"])
        plan = plans.get(record["plan_id"])
        index = indexes.get(record["stage_id"])
        if (plan is None or index is None
                or plan.get("sha256") != record["plan_sha256"]
                or plan.get("definition_sha256") != record["definition_sha256"]
                or index.get("plan_id") != record["plan_id"]
                or index.get("ordinal") != record["ordinal"]):
            raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stage attempt no longer binds to its exact stored plan/index")
        return record

    def begin_stage_attempt(self, *, project_id: str, model_ref: dict[str, Any], stage_id: str,
                            expected_revision: int, request_id: str, idempotency_key: str,
                            request_hash: str, operation_id: str, source_attempt_id: str | None = None
                            ) -> tuple[dict[str, Any], bool]:
        """Atomically bind one durable attempt to a validated plan and predecessors."""
        from ._stage_contract import sha256_json, canonical_json

        if (not isinstance(project_id, str) or not project_id or not isinstance(model_ref, dict)
                or not isinstance(stage_id, str) or not stage_id
                or type(expected_revision) is not int or expected_revision < 0
                or not isinstance(request_id, str) or not request_id
                or not isinstance(operation_id, str) or not operation_id
                or not isinstance(idempotency_key, str) or not idempotency_key
                or not isinstance(request_hash, str) or len(request_hash) != 64):
            raise StagePlanStoreConflict("INVALID_REQUEST", "stage attempt admission fields are malformed")
        digest = self._stage_scope_digest(project_id, model_ref)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                existing_row = self.db.execute(
                    "SELECT * FROM stage_attempts WHERE idempotency_key=?", (idempotency_key,),
                ).fetchone()
                if existing_row is not None:
                    existing = self._stage_attempt_from_row(existing_row)
                    if (existing["request_hash"] != request_hash
                            or existing["scope_digest"] != digest
                            or existing["project_id"] != project_id
                            or not self._stage_json_equal(existing["model_ref"], model_ref)
                            or existing["stage_id"] != stage_id):
                        raise StagePlanStoreConflict("IDEMPOTENCY_CONFLICT", "stage attempt idempotency key is bound to different content")
                    self.db.execute("COMMIT")
                    return existing, True

                resolved = self.resolve_stage(project_id, model_ref, stage_id)
                if resolved is None:
                    raise StagePlanStoreConflict("STAGE_NOT_FOUND", "stage_id is not registered in this project/model scope")
                plan, stage = resolved["plan"], resolved["stage"]
                if plan["definition"].get("version") != 2:
                    raise StagePlanStoreConflict("STAGE_PLAN_V1_DECLARATION_ONLY", "version 1 stage plans are declaration-only")

                rows = self.db.execute(
                    "SELECT * FROM stage_attempts WHERE scope_digest=? ORDER BY created_at,attempt_id",
                    (digest,),
                ).fetchall()
                records = [self._stage_attempt_from_row(row, project_id=project_id, model_ref=model_ref) for row in rows]
                same_stage = [row for row in records if row["stage_id"] == stage_id]
                if any(row["status"] in {"ADMITTED", "DISPATCH_INTENT", "RUNNING", "UNKNOWN"}
                       or row["engine_dispatched"]
                       for row in same_stage):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_UNRESOLVED", "a prior stage attempt is active or dispatched; explicit recovery is required before retry")
                if any(row["status"] in {"SUCCEEDED_PARTIAL", "ACCEPTED"} for row in same_stage):
                    raise StagePlanStoreConflict("STAGE_ALREADY_COMPLETED", "stage already has a completed durable attempt")

                accepted_by_stage: dict[str, list[dict[str, Any]]] = {}
                for row in records:
                    if row["status"] == "ACCEPTED":
                        accepted_by_stage.setdefault(row["stage_id"], []).append(row)
                for dependency in stage["depends_on"]:
                    accepted = accepted_by_stage.get(dependency, [])
                    if len(accepted) != 1:
                        code = "STAGE_PREDECESSOR_UNAVAILABLE" if not accepted else "STAGE_PREDECESSOR_AMBIGUOUS"
                        raise StagePlanStoreConflict(code, "every dependency needs one exact scientifically accepted stage attempt")
                source_id = stage.get("source_selection", {}).get("stage_id") if isinstance(stage.get("source_selection"), dict) else None
                if source_id is not None:
                    selected = accepted_by_stage.get(source_id, [])
                    if len(selected) != 1 or source_attempt_id != selected[0]["attempt_id"]:
                        raise StagePlanStoreConflict("STAGE_SOURCE_ATTEMPT_MISMATCH", "source_attempt_id must identify the unique accepted predecessor attempt")
                elif source_attempt_id is not None:
                    raise StagePlanStoreConflict("STAGE_SOURCE_ATTEMPT_MISMATCH", "initial-state stages cannot claim a predecessor attempt")

                attempt_number = 1 + max((row["attempt_number"] for row in same_stage), default=0)
                record = {
                    "schema_version": 1,
                    "kind": "w21_stage_attempt",
                    "project_id": project_id,
                    "model_ref": dict(model_ref),
                    "scope_digest": digest,
                    "plan_id": plan["plan_id"],
                    "plan_sha256": plan["sha256"],
                    "definition_sha256": plan["definition_sha256"],
                    "stage_id": stage_id,
                    "ordinal": stage["ordinal"],
                    "attempt_id": str(uuid4()),
                    "attempt_number": attempt_number,
                    "idempotency_key": idempotency_key,
                    "operation_id": operation_id,
                    "request_hash": request_hash,
                    "request_id": request_id,
                    "expected_revision": expected_revision,
                    "source_attempt_id": source_attempt_id,
                    "status": "ADMITTED",
                    "version": 1,
                    "engine_dispatched": False,
                    "execution_status": "NOT_STARTED",
                    "acceptance_status": "NOT_EVALUATED",
                    "evidence": [],
                    "result": None,
                }
                record["sha256"] = sha256_json(record)
                encoded = canonical_json(record)
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self.db.execute(
                    "INSERT INTO stage_attempts(attempt_id,scope_digest,stage_id,idempotency_key,request_hash,status,version,record_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (record["attempt_id"], digest, stage_id, idempotency_key, request_hash,
                     record["status"], record["version"], encoded, now, now),
                )
                self.db.execute("COMMIT")
                return record, False
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def get_stage_attempt(self, project_id: str, model_ref: dict[str, Any], attempt_id: str) -> dict[str, Any] | None:
        digest = self._stage_scope_digest(project_id, model_ref)
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM stage_attempts WHERE scope_digest=? AND attempt_id=?",
                (digest, attempt_id),
            ).fetchone()
            return self._stage_attempt_from_row(row, project_id=project_id, model_ref=model_ref) if row else None

    def get_stage_attempt_for_operation(self, project_id: str, model_ref: dict[str, Any], operation_id: str) -> dict[str, Any] | None:
        """Resolve the one pre-admitted attempt bound to a producer operation."""
        if not isinstance(operation_id, str) or not operation_id:
            raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "producer operation identity is malformed")
        matches = [record for record in self.list_stage_attempts(project_id, model_ref)
                   if record["operation_id"] == operation_id]
        if len(matches) > 1:
            raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "producer operation resolves to multiple stage attempts")
        return matches[0] if matches else None

    def list_stage_attempts(self, project_id: str, model_ref: dict[str, Any], *, stage_id: str | None = None) -> list[dict[str, Any]]:
        digest = self._stage_scope_digest(project_id, model_ref)
        query = "SELECT * FROM stage_attempts WHERE scope_digest=?"
        params: tuple[Any, ...] = (digest,)
        if stage_id is not None:
            query += " AND stage_id=?"
            params += (stage_id,)
        query += " ORDER BY created_at,attempt_id"
        with self.lock:
            rows = self.db.execute(query, params).fetchall()
            records = [self._stage_attempt_from_row(row, project_id=project_id, model_ref=model_ref) for row in rows]
            return sorted(records, key=lambda item: (item["stage_id"], item["attempt_number"], item["attempt_id"]))

    def update_stage_attempt(self, project_id: str, model_ref: dict[str, Any], attempt_id: str, *,
                             expected_version: int, status: str, engine_dispatched: bool,
                             execution_status: str, acceptance_status: str,
                             evidence: list[dict[str, Any]], result: dict[str, Any] | None = None
                             ) -> dict[str, Any]:
        """CAS one attempt state; UNKNOWN and completed records are immutable."""
        from ._stage_contract import sha256_json, canonical_json

        if (type(expected_version) is not int or expected_version < 1
                or status not in STAGE_ATTEMPT_STATUSES
                or type(engine_dispatched) is not bool
                or not isinstance(execution_status, str) or not isinstance(acceptance_status, str)
                or not isinstance(evidence, list)
                or (result is not None and not isinstance(result, dict))):
            raise StagePlanStoreConflict("INVALID_REQUEST", "stage attempt CAS fields are malformed")
        digest = self._stage_scope_digest(project_id, model_ref)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT * FROM stage_attempts WHERE scope_digest=? AND attempt_id=?",
                    (digest, attempt_id),
                ).fetchone()
                if row is None:
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_NOT_FOUND", "stage attempt is not registered in this project/model scope")
                current = self._stage_attempt_from_row(row, project_id=project_id, model_ref=model_ref)
                if current["version"] != expected_version:
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_CAS_CONFLICT", "stage attempt changed before the requested update")
                if status == "ACCEPTED" or acceptance_status == "ACCEPTED":
                    raise StagePlanStoreConflict("STAGE_ACCEPTANCE_UNVERIFIED", "generic attempt CAS cannot certify scientific acceptance")
                if status not in STAGE_ATTEMPT_TRANSITIONS.get(current["status"], frozenset()):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_TRANSITION_INVALID", "stage attempt status transition is not permitted")
                if current["engine_dispatched"] and not engine_dispatched:
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "engine dispatch evidence cannot be cleared")
                if status == "NOT_DISPATCHED_UNVERIFIED" and (
                        engine_dispatched or execution_status != "NOT_DISPATCHED"
                        or acceptance_status != "UNVERIFIED"):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "not-dispatched status requires explicit no-engine and unverified evidence")
                if status == "MAPPING_CONFIGURED_PARTIAL" and (
                        not engine_dispatched or execution_status != "MAPPING_CONFIGURED"
                        or acceptance_status != "PARTIAL"):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "partial mapping status requires a dispatched configuration readback")
                if status == "RUNNING" and (not engine_dispatched or execution_status != "RUNNING"):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "running status requires recorded engine dispatch")
                if status == "DISPATCH_INTENT" and (
                        engine_dispatched or execution_status != "DISPATCH_INTENT"
                        or acceptance_status != "NOT_EVALUATED"):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "dispatch intent must be durably recorded before engine dispatch")
                if status == "SUCCEEDED_PARTIAL" and (
                        not engine_dispatched or execution_status != "SOLVE_SUCCEEDED"
                        or acceptance_status not in {"PARTIAL", "UNVERIFIED"}):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "partial success requires a dispatched solve and explicit scientific limitation")
                if not self._stage_json_equal(evidence[:len(current["evidence"])], current["evidence"]):
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_STATE_UNKNOWN", "stage attempt evidence is append-only")
                updated = dict(current)
                updated.update({
                    "status": status,
                    "version": expected_version + 1,
                    "engine_dispatched": engine_dispatched,
                    "execution_status": execution_status,
                    "acceptance_status": acceptance_status,
                    "evidence": evidence,
                    "result": result,
                })
                updated.pop("sha256", None)
                updated["sha256"] = sha256_json(updated)
                self._validate_stage_attempt_record(updated, project_id=project_id, model_ref=model_ref)
                updated_cursor = self.db.execute(
                    "UPDATE stage_attempts SET status=?,version=?,record_json=?,updated_at=? WHERE attempt_id=? AND version=?",
                    (status, expected_version + 1, canonical_json(updated),
                     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), attempt_id, expected_version),
                )
                if updated_cursor.rowcount != 1:
                    raise StagePlanStoreConflict("STAGE_ATTEMPT_CAS_CONFLICT", "stage attempt changed during the requested update")
                self.db.execute("COMMIT")
                return updated
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    @staticmethod
    def _metric_scope_digest(project_id: str, metric_id: str) -> str:
        payload = f"{project_id}\0{metric_id}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _metric_record_digest(record: dict[str, Any]) -> str:
        payload = {key: value for key, value in record.items() if key != "sha256"}
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def append_metric_definition(self, project_id: str, metric_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Atomically append one immutable project-scoped metric definition version.

        The artifact table holds immutable version rows and one small head row.
        Both are changed in one SQLite transaction so a failed write cannot
        expose a half-published definition.  Repeating the current definition
        or tombstone returns its existing version instead of appending noise.
        """
        if not isinstance(project_id, str) or not project_id or not isinstance(metric_id, str) or not metric_id:
            raise ValueError("project_id and metric_id are required")
        if not isinstance(record, dict) or record.get("project_id") != project_id or record.get("metric_id") != metric_id:
            raise ValueError("metric definition record scope does not match its key")
        record = dict(record)
        expected_latest_version = record.pop("_expected_latest_version", None)
        scope = self._metric_scope_digest(project_id, metric_id)
        head_key = f"w17metric.head.{scope}"
        version_prefix = f"w17metric.version.{scope}."
        column = self._metadata_column("artifacts")
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                head_row = self.db.execute(
                    f"SELECT metadata FROM artifacts WHERE {column}=?", (head_key,),
                ).fetchone()
                head = json.loads(head_row[0]) if head_row is not None else None
                if head is not None:
                    if (not isinstance(head, dict) or head.get("kind") != "w17_metric_definition_head"
                            or head.get("project_id") != project_id or head.get("metric_id") != metric_id
                            or head.get("sha256") != self._metric_record_digest(head)):
                        raise RuntimeError("metric definition head is corrupt or misattributed")
                    latest_key = head.get("latest_key")
                    if not isinstance(latest_key, str) or not latest_key.startswith(version_prefix):
                        raise RuntimeError("metric definition head has an invalid version pointer")
                    latest_row = self.db.execute(
                        f"SELECT metadata FROM artifacts WHERE {column}=?", (latest_key,),
                    ).fetchone()
                    if latest_row is None:
                        raise RuntimeError("metric definition head points to a missing version")
                    latest = json.loads(latest_row[0])
                    if (not isinstance(latest, dict) or latest.get("project_id") != project_id
                            or latest.get("metric_id") != metric_id
                            or self._metric_record_digest(latest) != latest.get("sha256")):
                        raise RuntimeError("metric definition version is corrupt or misattributed")
                    if expected_latest_version is not None and latest.get("version") != expected_latest_version:
                        raise ValueError("metric definition version advanced before the requested update")
                    same_state = (
                        latest.get("removed") is bool(record.get("removed"))
                        and latest.get("definition_sha256") == record.get("definition_sha256")
                    )
                    if same_state and (bool(record.get("removed")) or latest.get("definition") == record.get("definition")):
                        self.db.execute("COMMIT")
                        return latest
                    next_version = latest.get("version")
                    if isinstance(next_version, bool) or not isinstance(next_version, int) or next_version < 1:
                        raise RuntimeError("metric definition version counter is corrupt")
                    version = next_version + 1
                else:
                    if expected_latest_version is not None:
                        raise ValueError("metric definition version advanced before the requested update")
                    version = 1
                version_record = dict(record)
                version_record["version"] = version
                version_record["sha256"] = self._metric_record_digest(version_record)
                version_key = f"{version_prefix}{version:08d}"
                existing_version = self.db.execute(
                    f"SELECT metadata FROM artifacts WHERE {column}=?", (version_key,),
                ).fetchone()
                if existing_version is not None:
                    existing = json.loads(existing_version[0])
                    if existing != version_record:
                        raise RuntimeError("immutable metric definition version key collision")
                else:
                    self.db.execute(
                        f"INSERT INTO artifacts({column},metadata) VALUES(?,?)",
                        (version_key, json.dumps(version_record, sort_keys=True, separators=(",", ":"), allow_nan=False)),
                    )
                head_record = {
                    "kind": "w17_metric_definition_head", "project_id": project_id,
                    "metric_id": metric_id, "latest_version": version,
                    "latest_key": version_key, "removed": bool(version_record.get("removed")),
                    "definition_sha256": version_record.get("definition_sha256"),
                }
                head_record["sha256"] = self._metric_record_digest(head_record)
                if head_row is None:
                    self.db.execute(
                        f"INSERT INTO artifacts({column},metadata) VALUES(?,?)",
                        (head_key, json.dumps(head_record, sort_keys=True, separators=(",", ":"), allow_nan=False)),
                    )
                else:
                    self.db.execute(
                        f"UPDATE artifacts SET metadata=? WHERE {column}=?",
                        (json.dumps(head_record, sort_keys=True, separators=(",", ":"), allow_nan=False), head_key),
                    )
                self.db.execute("COMMIT")
                return version_record
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def read_metric_definitions(
        self, project_id: str, *, metric_id: str | None = None, include_removed: bool = False,
    ) -> list[dict[str, Any]]:
        """Read each current metric version from one SQLite snapshot."""
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id is required")
        column = self._metadata_column("artifacts")
        scope = self._metric_scope_digest(project_id, metric_id) if metric_id is not None else None
        with self.lock:
            self.db.execute("BEGIN")
            try:
                if scope is None:
                    project_prefix = hashlib.sha256(f"{project_id}\0".encode("utf-8")).hexdigest()
                    # Scope digests also include metric_id, so a broad listing
                    # reads heads by record attribution rather than guessing an
                    # ID encoding from the opaque key.
                    rows = self.db.execute(
                        f"SELECT metadata FROM artifacts WHERE {column} LIKE 'w17metric.head.%' ORDER BY {column}"
                    ).fetchall()
                else:
                    rows = self.db.execute(
                        f"SELECT metadata FROM artifacts WHERE {column}=?", (f"w17metric.head.{scope}",),
                    ).fetchall()
                output: list[dict[str, Any]] = []
                for row in rows:
                    head = json.loads(row[0])
                    if not isinstance(head, dict) or head.get("kind") != "w17_metric_definition_head":
                        raise RuntimeError("metric definition head is malformed")
                    if head.get("sha256") != self._metric_record_digest(head):
                        raise RuntimeError("metric definition head hash does not match its contents")
                    if head.get("project_id") != project_id:
                        continue
                    if metric_id is not None and head.get("metric_id") != metric_id:
                        continue
                    key = head.get("latest_key")
                    if not isinstance(key, str):
                        raise RuntimeError("metric definition head has no version pointer")
                    version_row = self.db.execute(
                        f"SELECT metadata FROM artifacts WHERE {column}=?", (key,),
                    ).fetchone()
                    if version_row is None:
                        raise RuntimeError("metric definition head points to a missing version")
                    record = json.loads(version_row[0])
                    if (not isinstance(record, dict) or record.get("project_id") != project_id
                            or record.get("metric_id") != head.get("metric_id")
                            or record.get("sha256") != self._metric_record_digest(record)
                            or record.get("sha256") is None):
                        raise RuntimeError("metric definition version is corrupt or misattributed")
                    if include_removed or not record.get("removed"):
                        output.append(record)
                self.db.execute("COMMIT")
                return output
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def read_metric_definition_versions(
        self,
        project_id: str,
        references: list[dict[str, Any]],
        *,
        require_latest: bool = False,
        require_active_head: bool = True,
    ) -> list[dict[str, Any]]:
        """Resolve exact immutable metric versions in one project-scoped snapshot.

        The caller supplies only ``metric_id``, ``version`` and the semantic
        definition hash.  Returned definitions always come from the immutable
        version row; caller-provided definition bodies are never accepted as
        observations.  ``require_latest`` is used when an experiment freezes a
        new metric reference.  A later active version does not invalidate that
        frozen reference, but a removal tombstone prevents a new run.
        """
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id is required")
        if not isinstance(references, list) or not references:
            raise ValueError("at least one metric version reference is required")
        column = self._metadata_column("artifacts")
        seen: set[str] = set()
        with self.lock:
            self.db.execute("BEGIN")
            try:
                output: list[dict[str, Any]] = []
                for reference in references:
                    if (not isinstance(reference, dict)
                            or set(reference) != {"metric_id", "version", "definition_sha256"}):
                        raise ValueError("metric version reference is malformed")
                    metric_id = reference.get("metric_id")
                    version = reference.get("version")
                    definition_hash = reference.get("definition_sha256")
                    if (not isinstance(metric_id, str) or not metric_id or metric_id in seen
                            or isinstance(version, bool) or not isinstance(version, int) or version < 1
                            or not isinstance(definition_hash, str) or len(definition_hash) != 64):
                        raise ValueError("metric version reference is invalid or duplicated")
                    seen.add(metric_id)
                    scope = self._metric_scope_digest(project_id, metric_id)
                    prefix = f"w17metric.version.{scope}."
                    head_key = f"w17metric.head.{scope}"
                    head_row = self.db.execute(
                        f"SELECT metadata FROM artifacts WHERE {column}=?", (head_key,),
                    ).fetchone()
                    if head_row is None:
                        raise ValueError("metric version is unavailable in this project")
                    head = json.loads(head_row[0])
                    if (not isinstance(head, dict) or head.get("kind") != "w17_metric_definition_head"
                            or head.get("project_id") != project_id or head.get("metric_id") != metric_id
                            or head.get("sha256") != self._metric_record_digest(head)):
                        raise RuntimeError("metric definition head is corrupt or misattributed")
                    latest = head.get("latest_version")
                    latest_key = head.get("latest_key")
                    if (isinstance(latest, bool) or not isinstance(latest, int) or latest < 1
                            or latest_key != f"{prefix}{latest:08d}"):
                        raise RuntimeError("metric definition head has an invalid version pointer")
                    if require_active_head and head.get("removed") is not False:
                        raise ValueError("removed metric definitions cannot be used for a new experiment run")
                    if require_latest and latest != version:
                        raise ValueError("new experiment designs must pin the current metric definition version")
                    version_key = f"{prefix}{version:08d}"
                    version_row = self.db.execute(
                        f"SELECT metadata FROM artifacts WHERE {column}=?", (version_key,),
                    ).fetchone()
                    if version_row is None:
                        raise ValueError("pinned metric definition version is unavailable")
                    record = json.loads(version_row[0])
                    if (not isinstance(record, dict) or record.get("kind") != "w17_metric_definition_version"
                            or record.get("project_id") != project_id or record.get("metric_id") != metric_id
                            or record.get("version") != version or record.get("removed") is not False
                            or record.get("sha256") != self._metric_record_digest(record)
                            or record.get("definition_sha256") != definition_hash):
                        raise RuntimeError("pinned metric definition version failed integrity or semantic binding")
                    output.append(record)
                self.db.execute("COMMIT")
                return output
            except BaseException:
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
